# ✉ Gmail Cleaner

A small, safe tool to sweep **Promotions / Updates / Social / Forums** — and
optionally old **Primary** mail — into Gmail's Trash. Three front doors over one
shared core (`core.py`):

- **Web app** — a clean local page in your browser, with a first-run setup
  wizard.
- **MCP server** — real Model Context Protocol tools so an AI assistant
  (e.g. Claude) can do the cleanup for you.
- **CLI** — a scriptable command, perfect for cron.

The web app and the MCP server are the **same process** — one server, built on
the official MCP SDK + [Starlette](https://www.starlette.io/) (no Flask):

```
python server.py
  web UI : http://localhost:8000/      ← humans, in a browser
  MCP    : http://localhost:8000/mcp   ← Claude, over Streamable HTTP
```

```
http://localhost:8000
┌─────────────────────────────────┐
│  ✉  Gmail Cleaner               │
│  Signed in: you@gmail.com       │
│                                 │
│  ☑ Promotions      16,573       │
│  ☑ Updates            188       │
│  ☐ Social               0       │
│  ☐ Forums              42       │
│ ┌─ ⚠ Primary ──────────────── ┐ │
│ │ ☐ Primary           1,204   │ │
│ └────────────────────────────┘ │
│  Only mail older than [ 1y ▾ ] │
│  [ Preview ]   [ Move to Trash ]│
└─────────────────────────────────┘
```

## Why it's safe

- **Trash only — never permanent.** It uses Gmail's `gmail.modify` scope, which
  can *only* move messages to Trash. The Gmail API physically refuses permanent
  deletion with this scope. Anything trashed is recoverable for ~30 days.
- **Dry-run first.** The web app (Preview), the MCP `preview_cleanup` tool, and
  the CLI (default) all show counts before anything moves.
- **Primary is guarded.** Your important personal mail can only be trashed with
  an age filter set, plus a typed `PRIMARY` confirmation.
- **Starred & labelled mail is spared.** Categories are mutually exclusive
  (cleaning Promotions can't touch Primary), and by default anything you've
  starred or filed under a custom label — e.g. a labelled Primary message — is
  never trashed. (CLI power users can override with `--include-flagged`.)

---

## 1. Install

Requires Python 3.10.7+.

```bash
git clone https://github.com/vpamrit/gmail-cleanup-mcp.git
cd gmail-cleanup-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. One-time Google setup (~5 min)

Gmail Cleaner talks to your mailbox through Google's own API, so you create a
free personal OAuth credential once. **The web app walks you through this on
first run** (`python server.py` → it opens the setup wizard), but here are the
same steps for reference:

1. **Create a Google Cloud project** —
   <https://console.cloud.google.com/projectcreate> (any name, e.g.
   `gmail-cleanup`).
2. **Enable the Gmail API** —
   <https://console.cloud.google.com/apis/library/gmail.googleapis.com> → *Enable*.
3. **Configure the OAuth consent screen** —
   <https://console.cloud.google.com/apis/credentials/consent>:
   - User type **External** → *Create*.
   - Fill in the app name and your email.
   - On **Test users**, add your own Gmail address. (Test mode is fine — no
     Google verification is needed for personal use.)
4. **Create the credential** —
   <https://console.cloud.google.com/apis/credentials> → *Create Credentials* →
   *OAuth client ID* → application type **Desktop app** → *Create* →
   **Download JSON**.
5. **Provide the file:**
   - **Web app:** drag & drop the downloaded JSON onto the setup screen.
   - **CLI:** save it as `credentials.json` in this folder.

On first authorization a browser window asks you to sign in and grant access.
You may see an *"unverified app"* warning — click **Advanced → Go to … (unsafe)**;
that's expected for your own personal app. A `token.json` is then written so you
won't need to re-authorize next time.

> 🔒 **Keep `credentials.json` and `token.json` private** — they grant access to
> your mailbox. They're already in `.gitignore`; never commit them.

---

## 3. Use the web app

```bash
python server.py
# open http://localhost:8000
```

Pick categories → **Preview** (shows counts, changes nothing) → **Move to
Trash** → confirm. The server binds to `127.0.0.1` only, so it's not reachable
from your network.

## 4. Use it from Claude (MCP)

Start the server (`python server.py`) so the MCP endpoint is live at
`http://localhost:8000/mcp`, then register it.

**Claude Code** (supports HTTP MCP directly):

```bash
claude mcp add gmail-cleaner --transport http http://localhost:8000/mcp
```

**Claude Desktop** speaks only **stdio**, so point it at the stdio mode instead
(no separate server to keep running — Claude launches it):

```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "gmail-cleaner": {
      "command": "/absolute/path/to/gmail-cleanup-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/gmail-cleanup-mcp/server.py", "--stdio"]
    }
  }
}
```

Exposed tools:

| Tool | What it does |
|------|--------------|
| `account_status` | Is it connected? Which mailbox? (read-only) |
| `preview_cleanup(categories, older_than)` | Count what *would* be trashed (read-only) |
| `trash_cleanup(categories, older_than, confirm_primary)` | Move matching mail to Trash |

Quick check that the tools are live (with the server running):

```bash
curl -s -X POST http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Or explore them in a GUI with the MCP Inspector:
`npx @modelcontextprotocol/inspector` → connect to `http://localhost:8000/mcp`.

> First-time auth can't happen over MCP (it needs a browser). Run the web app
> once and click **Connect Gmail**; after that the MCP tools reuse `token.json`.

## 5. Use the CLI

```bash
# Dry run with default categories (promotions, updates, social):
python gmail_cleanup.py

# Pick categories explicitly:
python gmail_cleanup.py --categories promotions updates

# Only mail older than 30 days (keeps recent stuff):
python gmail_cleanup.py --older-than 30d

# Actually move matching mail to Trash:
python gmail_cleanup.py --confirm

# Trash OLD Primary mail too (an age filter is required for primary):
python gmail_cleanup.py --categories primary --older-than 1y --confirm
```

Age tokens (`--older-than`) use Gmail's format: `30d`, `6m`, `1y`.

### Run automatically on a schedule (optional)

**Simple cron** (`crontab -e`) — trashes promotions/updates/social older than 30
days, daily at 8am:

```
0 8 * * * cd ~/path/to/gmail-cleanup-mcp && .venv/bin/python gmail_cleanup.py --older-than 30d --confirm >> cleanup.log 2>&1
```

**systemd user timer** (recommended on Linux — catches up if the machine was
off). Create `~/.config/systemd/user/gmail-cleanup.service`:

```ini
[Service]
Type=oneshot
WorkingDirectory=%h/path/to/gmail-cleanup-mcp
ExecStart=%h/path/to/gmail-cleanup-mcp/.venv/bin/python %h/path/to/gmail-cleanup-mcp/gmail_cleanup.py --categories promotions social updates forums --confirm
ExecStart=%h/path/to/gmail-cleanup-mcp/.venv/bin/python %h/path/to/gmail-cleanup-mcp/gmail_cleanup.py --categories primary --older-than 60d --confirm
```

and `~/.config/systemd/user/gmail-cleanup.timer`:

```ini
[Timer]
OnCalendar=Mon *-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Then enable it (and run once now to test):

```bash
systemctl --user daemon-reload
systemctl --user enable --now gmail-cleanup.timer
systemctl --user start gmail-cleanup.service   # optional: run immediately
systemctl --user list-timers gmail-cleanup.timer
journalctl --user -u gmail-cleanup.service     # see what it did
```

> **Token longevity:** for any unattended schedule, set the OAuth consent screen
> to **In production** (APIs & Services → OAuth consent screen → *Publish app*).
> In *Testing* mode Google expires the refresh token after 7 days, which would
> break the schedule weekly. To run even while logged out:
> `loginctl enable-linger $USER`.

---

## About Primary

Primary is your important personal mail, so it's deliberately harder to touch,
everywhere:

- **Off by default** and visually separated in the web UI.
- **Requires an age filter** — you can't trash *all* of Primary. (Enforced by
  the server, the MCP tool, and the CLI — not just the UI.)
- Needs a **typed `PRIMARY` confirmation** (web app and the `trash_cleanup` tool).
- Like everything else, it only ever moves to Trash (recoverable ~30 days).

## Architecture

```
core.py            shared Gmail logic (auth, count, trash, queries)  ← the brain
├─ gmail_cleanup.py  CLI front door (no server)
└─ server.py         ONE process:
                       • Starlette web UI  → humans   (/, /setup, /api/*, /static)
                       • MCP tools         → Claude    (/mcp, or --stdio)
templates/         dashboard.html, setup.html
static/            style.css, app.js   (no build step)
test_core.py       unit tests for the pure logic
```

Why one server instead of two? The official MCP SDK's HTTP transport is a
Starlette (ASGI) app, and Starlette already does everything Flask would —
routing, HTML, static files, JSON, uploads. So the human web routes hang right
next to the `/mcp` endpoint in the same app. `--stdio` runs *only* the MCP half
(what Claude Desktop expects), with no web server at all.

## Run the tests

```bash
pip install pytest
python -m pytest -q
```
