#!/usr/bin/env python3
"""Gmail Cleaner -- one server, two front doors, over a shared core.

Built on the official MCP SDK (mcp / FastMCP) and Starlette. No Flask.

Run modes
---------
    python server.py            # combined server (default):
                                #   web UI  ->  http://localhost:8000/
                                #   MCP     ->  http://localhost:8000/mcp   (Streamable HTTP)

    python server.py --stdio    # pure MCP over stdio, no web UI
                                #   (this is "regular" MCP -- what Claude Desktop speaks)

Both modes expose the SAME tools/logic from core.py. The web routes are for a
human in a browser; the MCP tools are for an AI assistant like Claude. Neither
can permanently delete: the gmail.modify scope only moves mail to Trash
(recoverable ~30 days).
"""

from __future__ import annotations

import os
import sys

import anyio
import uvicorn
from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import core

HOST = "127.0.0.1"
PORT = 8000
BASE = os.path.dirname(os.path.abspath(__file__))

# Stateless + JSON responses keep the HTTP transport simple: every request is
# self-contained (no session id to track) and replies are plain JSON, which is
# easy for clients -- and for curl -- to consume. The default
# streamable_http_path is "/mcp", so the SDK's app already serves there.
mcp = FastMCP("gmail-cleaner", stateless_http=True, json_response=True)


# --------------------------------------------------------------------------- #
# Shared validation (used by both the MCP tools and the web API)
# --------------------------------------------------------------------------- #
def _validate(categories, older_than, require_primary_age: bool):
    """Return (categories, older_than, error). error is None on success.

    Enforces the Primary age guardrail server-side when require_primary_age.
    """
    older = (older_than or "").strip() or None
    if not isinstance(categories, list) or not categories:
        return None, None, "Select at least one category."
    unknown = [c for c in categories if c not in core.CATEGORY_QUERIES]
    if unknown:
        return None, None, f"Unknown categor(ies): {', '.join(unknown)}."
    if require_primary_age and core.PRIMARY in categories and not older:
        return None, None, (
            "Primary needs an age filter (e.g. '1y') before it can be trashed."
        )
    return categories, older, None


def _require_auth():
    """Raise a helpful error if we can't talk to Gmail yet (MCP context)."""
    if not core.credentials_present():
        raise ValueError(
            "No credentials yet. Run `python server.py`, open the web UI, and "
            "complete the one-time setup first."
        )
    if not core.is_authorized():
        raise ValueError(
            "Not authorized yet. Run `python server.py`, click 'Connect Gmail' "
            "in the web UI, then try again."
        )


# --------------------------------------------------------------------------- #
# MCP tools (the door for Claude). Async so the blocking Gmail calls run in a
# worker thread and never stall the event loop / web UI.
# --------------------------------------------------------------------------- #
@mcp.tool()
async def account_status() -> dict:
    """Check whether Gmail Cleaner is connected and which mailbox it controls.

    Read-only. Returns {connected, email, categories}.
    """
    connected = core.credentials_present() and core.is_authorized()
    email = ""
    if connected:
        service = await anyio.to_thread.run_sync(core.get_service)
        email = await anyio.to_thread.run_sync(core.get_account_email, service)
    return {
        "connected": connected,
        "email": email,
        "categories": list(core.CATEGORY_QUERIES),
    }


@mcp.tool()
async def preview_cleanup(categories: list[str], older_than: str = "") -> dict:
    """Count how many messages WOULD be trashed, per category. Read-only.

    categories: any of promotions, updates, social, forums, primary.
    older_than: optional Gmail age token like '30d', '6m', '1y'. '' means any age.

    Always run this and report the counts to the user before trash_cleanup.
    """
    _require_auth()
    cats, age, err = _validate(categories, older_than, require_primary_age=False)
    if err:
        raise ValueError(err)
    service = await anyio.to_thread.run_sync(core.get_service)
    counts = {}
    for category in cats:
        counts[category] = await anyio.to_thread.run_sync(
            core.count_category, service, category, age
        )
    return {"counts": counts, "total": sum(counts.values())}


@mcp.tool()
async def trash_cleanup(
    categories: list[str], older_than: str = "", confirm_primary: str = ""
) -> dict:
    """Move matching messages to Trash. Recoverable ~30 days; never permanent.

    Same arguments as preview_cleanup. SAFETY: trashing 'primary' (the user's
    important personal mail) requires BOTH older_than to be set AND
    confirm_primary == 'PRIMARY'. Confirm counts with the user first.
    """
    _require_auth()
    cats, age, err = _validate(categories, older_than, require_primary_age=True)
    if err:
        raise ValueError(err)
    if core.PRIMARY in cats and confirm_primary.strip().upper() != "PRIMARY":
        raise ValueError(
            "Refusing to trash Primary without confirm_primary='PRIMARY'."
        )
    service = await anyio.to_thread.run_sync(core.get_service)
    trashed = {}
    for category in cats:
        ids = await anyio.to_thread.run_sync(
            core.list_message_ids, service, core.build_query(category, age)
        )
        trashed[category] = await anyio.to_thread.run_sync(
            core.trash_ids, service, ids
        )
    return {"trashed": trashed, "total": sum(trashed.values())}


# --------------------------------------------------------------------------- #
# Web UI (the door for humans). Plain Starlette routes -- the "Flask-esque"
# features (HTML, static files, JSON API, uploads) live here.
# --------------------------------------------------------------------------- #
def _page(name: str) -> FileResponse:
    return FileResponse(os.path.join(BASE, "templates", name))


async def _json_body(request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 - empty / malformed body -> treat as {}
        return {}


def _http_message(err: HttpError) -> str:
    return f"Gmail API error: {getattr(err, 'reason', None) or err}"


async def index(request):
    """Dashboard, or the setup wizard for first-time users."""
    if not core.credentials_present():
        return RedirectResponse("/setup")
    return _page("dashboard.html")


async def setup_page(request):
    return _page("setup.html")


async def api_status(request):
    has_creds = core.credentials_present()
    connected = has_creds and core.is_authorized()
    email = ""
    if connected:
        try:
            service = await run_in_threadpool(core.get_service)
            email = await run_in_threadpool(core.get_account_email, service)
        except HttpError as err:
            return JSONResponse({"error": _http_message(err)}, status_code=502)
    return JSONResponse(
        {
            "has_credentials": has_creds,
            "connected": connected,
            "email": email,
            "categories": list(core.CATEGORY_QUERIES),
            "primary": core.PRIMARY,
        }
    )


async def api_upload_credentials(request):
    raw = await request.body()
    if not raw:
        return JSONResponse({"error": "No file received."}, status_code=400)
    try:
        core.save_credentials(raw)
    except core.CredentialsError as err:
        return JSONResponse({"error": str(err)}, status_code=400)
    return JSONResponse({"ok": True})


async def api_connect(request):
    if not core.credentials_present():
        return JSONResponse({"error": "Upload credentials.json first."}, status_code=400)
    try:
        service = await run_in_threadpool(core.get_service)
        email = await run_in_threadpool(core.get_account_email, service)
    except core.CredentialsError as err:
        return JSONResponse({"error": str(err)}, status_code=400)
    except HttpError as err:
        return JSONResponse({"error": _http_message(err)}, status_code=502)
    except Exception as err:  # noqa: BLE001 - surface any OAuth failure to the UI
        return JSONResponse({"error": f"Authorization failed: {err}"}, status_code=500)
    return JSONResponse({"ok": True, "email": email})


async def api_preview(request):
    body = await _json_body(request)
    cats, age, err = _validate(
        body.get("categories") or [], body.get("older_than") or "", False
    )
    if err:
        return JSONResponse({"error": err}, status_code=400)
    try:
        service = await run_in_threadpool(core.get_service)
        counts = {}
        for category in cats:
            counts[category] = await run_in_threadpool(
                core.count_category, service, category, age
            )
    except core.CredentialsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except HttpError as e:
        return JSONResponse({"error": _http_message(e)}, status_code=502)
    return JSONResponse({"counts": counts, "total": sum(counts.values())})


async def api_clean(request):
    body = await _json_body(request)
    cats, age, err = _validate(
        body.get("categories") or [], body.get("older_than") or "", True
    )
    if err:
        return JSONResponse({"error": err}, status_code=400)
    if core.PRIMARY in cats:
        confirm = str(body.get("confirm_primary", "")).strip().upper()
        if confirm != "PRIMARY":
            return JSONResponse(
                {"error": "Primary requires typing PRIMARY to confirm."},
                status_code=400,
            )
    try:
        service = await run_in_threadpool(core.get_service)
        trashed = {}
        for category in cats:
            ids = await run_in_threadpool(
                core.list_message_ids, service, core.build_query(category, age)
            )
            trashed[category] = await run_in_threadpool(core.trash_ids, service, ids)
    except core.CredentialsError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except HttpError as e:
        return JSONResponse({"error": _http_message(e)}, status_code=502)
    return JSONResponse({"trashed": trashed, "total": sum(trashed.values())})


# --------------------------------------------------------------------------- #
# App assembly
# --------------------------------------------------------------------------- #
def build_app():
    """Compose one Starlette app: the human web UI plus the MCP endpoint.

    mcp.streamable_http_app() returns a Starlette app that already serves MCP at
    /mcp and wires the lifespan that runs the session manager. We add the human
    web routes to that same app, so both live in one process and the MCP
    endpoint stays at exactly /mcp (no mount redirect).
    """
    app = mcp.streamable_http_app()
    app.router.routes.extend(
        [
            Route("/", index),
            Route("/setup", setup_page),
            Route("/api/status", api_status),
            Route("/api/upload-credentials", api_upload_credentials, methods=["POST"]),
            Route("/api/connect", api_connect, methods=["POST"]),
            Route("/api/preview", api_preview, methods=["POST"]),
            Route("/api/clean", api_clean, methods=["POST"]),
            Mount(
                "/static",
                app=StaticFiles(directory=os.path.join(BASE, "static")),
                name="static",
            ),
        ]
    )
    return app


def main():
    if "--stdio" in sys.argv[1:]:
        # Pure MCP over stdio -- no web server. Claude Desktop launches this.
        mcp.run("stdio")
        return
    print(
        f"Gmail Cleaner\n"
        f"  web UI : http://{HOST}:{PORT}/\n"
        f"  MCP    : http://{HOST}:{PORT}/mcp\n"
        f"  (Ctrl+C to stop)"
    )
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
