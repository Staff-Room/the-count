#!/usr/bin/env python3
"""
One-shot Plaid sync into the active store (BACKEND_STORE=sqlite|supabase).

Loads .env, walks every linked item, runs transactions_sync with the saved cursor
until the response no longer has_more. Rate-limit aware: sleeps briefly between items
and uses a per-page minimum interval to stay well under Plaid's 30 req/min/item ceiling.
In supabase mode, also refreshes account balances per item.

Usage:
    python scripts/sync_plaid_now.py            # incremental
    python scripts/sync_plaid_now.py --full     # reset cursors, re-pull everything
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import db  # noqa: E402
import plaid  # noqa: E402
import plaid_sync  # noqa: E402
from plaid.api import plaid_api  # noqa: E402
from plaid.model.accounts_get_request import AccountsGetRequest  # noqa: E402

ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}
plaid_env = (os.getenv("PLAID_ENV") or os.getenv("PLAID_ENVIRONMENT") or "sandbox").lower()
if plaid_env not in ENV_MAP:
    print(f"Unknown PLAID_ENV={plaid_env!r}; defaulting to sandbox")
    plaid_env = "sandbox"

configuration = plaid.Configuration(
    host=ENV_MAP[plaid_env],
    api_key={
        "clientId": os.getenv("PLAID_CLIENT_ID"),
        "secret": os.getenv("PLAID_SECRET"),
        "plaidVersion": "2020-09-14",
    },
)
client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

MIN_PAGE_INTERVAL_S = 0.5   # be polite — under 30 req/min/item
INTER_ITEM_DELAY_S = 1.0


def sync_one(item_id: str, access_token: str) -> dict:
    return plaid_sync.sync_item(
        client, item_id, access_token, min_page_interval_s=MIN_PAGE_INTERVAL_S
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="reset cursors and re-pull")
    args = parser.parse_args()

    if args.full:
        db.reset_all_sync_cursors()
        print("Reset all sync cursors.")

    items = list(db.iter_items_with_tokens())
    if not items:
        print("No linked items.")
        return 0

    print(f"PLAID_ENV={plaid_env}  store={db.STORE}  items={len(items)}")
    totals = {"added": 0, "modified": 0, "removed": 0, "pages": 0}
    any_error = False

    for i, row in enumerate(items):
        item_id = row["item_id"]
        inst = row.get("institution_name") or "?"
        token = row["access_token"]
        print(f"\n→ {inst} ({item_id})")
        try:
            stats = sync_one(item_id, token)
            print(f"  added={stats['added']} modified={stats['modified']} removed={stats['removed']} pages={stats['pages']}")
            for k in totals:
                totals[k] += stats[k]
            if hasattr(db, "upsert_accounts"):
                accounts = (
                    client.accounts_get(AccountsGetRequest(access_token=token))
                    .to_dict()
                    .get("accounts")
                    or []
                )
                db.upsert_accounts(item_id, accounts)
                print(f"  accounts refreshed: {len(accounts)}")
        except plaid.ApiException as e:
            any_error = True
            try:
                err = json.loads(e.body)
            except Exception:
                err = {"raw": e.body}
            print(f"  PLAID ERROR: {err.get('error_code')} — {err.get('error_message')}")
            db.set_cursor(item_id, db.get_cursor(item_id), error=json.dumps(err))
        except Exception as e:
            any_error = True
            print(f"  ERROR: {e}")
            db.set_cursor(item_id, db.get_cursor(item_id), error=str(e))

        if i < len(items) - 1:
            time.sleep(INTER_ITEM_DELAY_S)

    print(f"\nTotal across items: {totals}")
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
