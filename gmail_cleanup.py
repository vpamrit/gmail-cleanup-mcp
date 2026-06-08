#!/usr/bin/env python3
"""Move Gmail messages in chosen tabs (Promotions, Updates, Social, Forums and
optionally Primary) to Trash.

Safe by design:
  * Dry-run is the DEFAULT. Nothing is touched until you pass --confirm.
  * Uses the gmail.modify scope, which can only *trash* (recoverable for ~30
    days) -- the Gmail API refuses permanent deletion with this scope.
  * Primary is your important personal mail, so it requires an age filter
    (--older-than) before it can be trashed.

Usage examples:
    # See what WOULD be trashed (no changes):
    python gmail_cleanup.py

    # Only certain categories:
    python gmail_cleanup.py --categories promotions updates

    # Keep recent mail, only trash items older than 30 days:
    python gmail_cleanup.py --older-than 30d

    # Trash old Primary mail too (age filter is required for primary):
    python gmail_cleanup.py --categories primary --older-than 1y --confirm

    # Actually move matching messages to Trash:
    python gmail_cleanup.py --confirm

The web UI (python app.py) is a friendlier front-end over the same logic.
"""

from __future__ import annotations

import argparse
import sys

from googleapiclient.errors import HttpError

import core


def parse_args():
    parser = argparse.ArgumentParser(
        description="Move Gmail tab mail to Trash (dry-run by default)."
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=sorted(core.CATEGORY_QUERIES),
        default=["promotions", "updates", "social"],
        help="Categories to clean (default: promotions updates social).",
    )
    parser.add_argument(
        "--older-than",
        default=None,
        metavar="AGE",
        help="Only affect mail older than this Gmail age token, e.g. 30d, 6m, 1y.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually move messages to Trash. Without this flag it is a dry run.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Primary is high-risk: never allow trashing all of it with no age filter.
    if core.PRIMARY in args.categories and not args.older_than:
        sys.exit(
            "Refusing to target Primary without an age filter. Re-run with "
            "--older-than (e.g. --older-than 1y) so recent personal mail is "
            "kept safe."
        )

    try:
        service = core.get_service()
    except core.CredentialsError as err:
        sys.exit(str(err))

    mode = "LIVE (moving to Trash)" if args.confirm else "DRY RUN (no changes)"
    print(f"Mode: {mode}")
    if args.older_than:
        print(f"Age filter: older than {args.older_than}")
    print(f"Categories: {', '.join(args.categories)}\n")

    grand_total = 0
    for category in args.categories:
        query = core.build_query(category, args.older_than)
        print(f"[{category}] query: {query}")
        try:
            ids = core.list_message_ids(service, query)
        except HttpError as err:
            print(f"    ERROR fetching messages: {err}")
            continue

        print(f"    found {len(ids)} message(s)")
        grand_total += len(ids)

        if ids and args.confirm:
            core.trash_ids(
                service,
                ids,
                progress=lambda done, total: print(f"    trashed {done}/{total}"),
            )
        print()

    if args.confirm:
        print(f"Done. Moved {grand_total} message(s) to Trash (recoverable ~30 days).")
    else:
        print(
            f"Dry run complete. {grand_total} message(s) would be trashed.\n"
            "Re-run with --confirm to actually move them to Trash."
        )


if __name__ == "__main__":
    main()
