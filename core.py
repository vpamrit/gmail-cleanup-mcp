#!/usr/bin/env python3
"""Shared Gmail-cleanup logic used by both the CLI (gmail_cleanup.py) and the
web app (app.py).

Safe by design:
  * Uses the gmail.modify scope, which can only *trash* messages (recoverable
    for ~30 days). The Gmail API refuses permanent deletion with this scope.
  * Nothing in this module deletes permanently. trash_ids() only adds the
    TRASH label, which is reversible from Gmail's Trash folder.

This module never makes a destructive call on its own -- callers decide when to
invoke trash_ids(). Counting (list_message_ids) is always read-only.
"""

from __future__ import annotations

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# gmail.modify lets us add the TRASH label. It deliberately does NOT permit
# permanent deletion, which keeps every action in this tool recoverable.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Gmail search-query tokens for each tab. "primary" is the user's important
# personal mail and is treated with extra care by callers (see PRIMARY).
CATEGORY_QUERIES = {
    "primary": "category:primary",
    "promotions": "category:promotions",
    "updates": "category:updates",
    "social": "category:social",
    "forums": "category:forums",
}

# The high-risk category. Callers must require an age filter before trashing it.
PRIMARY = "primary"

# batchModify accepts at most 1000 message ids per request.
BATCH_SIZE = 1000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.json")


class CredentialsError(Exception):
    """Raised when credentials.json is missing or not a usable OAuth file."""


def credentials_present() -> bool:
    """True if a credentials.json file exists in the project folder."""
    return os.path.exists(CREDENTIALS_FILE)


def is_authorized() -> bool:
    """True if we already hold a token that can be used (possibly after a
    silent refresh) without prompting the user again."""
    if not os.path.exists(TOKEN_FILE):
        return False
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    except (ValueError, json.JSONDecodeError):
        return False
    if creds.valid:
        return True
    return bool(creds.expired and creds.refresh_token)


def validate_credentials_json(raw: bytes | str) -> dict:
    """Parse and sanity-check an uploaded OAuth client file.

    Returns the parsed dict on success; raises CredentialsError otherwise.
    Accepts the "installed" (Desktop app) client type that this tool expects.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise CredentialsError(f"That file is not valid JSON: {err}") from err

    if "installed" not in data:
        kind = next(iter(data), "unknown")
        raise CredentialsError(
            "This looks like the wrong OAuth client type "
            f"(found '{kind}'). Create an OAuth client ID of type "
            "'Desktop app' and download that JSON."
        )
    section = data["installed"]
    missing = [k for k in ("client_id", "client_secret") if not section.get(k)]
    if missing:
        raise CredentialsError(
            f"Credentials file is missing required field(s): {', '.join(missing)}."
        )
    return data


def save_credentials(raw: bytes | str) -> None:
    """Validate then write the uploaded OAuth client file to credentials.json."""
    validate_credentials_json(raw)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    with open(CREDENTIALS_FILE, "w") as fh:
        fh.write(text)


def get_service():
    """Authenticate (cached in token.json) and return a Gmail API client.

    If no valid token exists this opens the Google consent screen in a browser
    via a temporary local redirect server, exactly like the CLI's first run.
    """
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_present():
                raise CredentialsError(
                    f"Missing {CREDENTIALS_FILE}. Download your OAuth "
                    "'Desktop app' credentials from the Google Cloud Console "
                    "and save them there. See the README."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_account_email(service) -> str:
    """Return the email address of the authorized mailbox."""
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")


def build_query(
    category: str, older_than: str | None = None, protect: bool = True
) -> str:
    """Build a Gmail search query for one category, excluding Trash.

    Gmail categories are mutually exclusive, so a query for one category never
    matches mail in another (cleaning Promotions can't touch Primary, etc.).

    When ``protect`` is True (the default) we additionally spare anything you've
    explicitly kept -- starred mail, and mail you've filed under a custom label
    (e.g. a labelled Primary message) -- so cleaning a category never removes
    something you've flagged or filed.
    """
    query = CATEGORY_QUERIES[category]
    if older_than:
        query += f" older_than:{older_than}"
    # Skip anything already in Trash so counts are honest.
    query += " -in:trash"
    if protect:
        # -is:starred     : keep starred mail
        # -has:userlabels : keep anything filed under a user-created label
        query += " -is:starred -has:userlabels"
    return query


def list_message_ids(service, query: str) -> list[str]:
    """Return all message ids matching a Gmail search query (handles paging).

    Read-only -- this never modifies the mailbox.
    """
    ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute()
        )
        ids.extend(msg["id"] for msg in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def trash_ids(service, ids: list[str], progress=None) -> int:
    """Add the TRASH label to messages in batches of up to BATCH_SIZE.

    Recoverable: TRASH is reversible from Gmail until it purges (~30 days).
    `progress`, if given, is called as progress(done, total) after each batch.
    Returns the number of message ids processed.
    """
    total = len(ids)
    for start in range(0, total, BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        service.users().messages().batchModify(
            userId="me",
            body={"ids": chunk, "addLabelIds": ["TRASH"]},
        ).execute()
        if progress:
            progress(start + len(chunk), total)
    return total


def count_category(
    service, category: str, older_than: str | None = None, protect: bool = True
) -> int:
    """Return how many messages a category currently matches (read-only)."""
    return len(list_message_ids(service, build_query(category, older_than, protect)))
