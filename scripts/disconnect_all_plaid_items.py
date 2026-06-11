#!/usr/bin/env python3
"""Remove all linked Plaid items (Plaid API + local SQLite). Use before env migration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "src" / "backend"
sys.path.insert(0, str(_BACKEND))
load_dotenv(_ROOT / ".env")

import db  # noqa: E402
import plaid
from plaid.api import plaid_api
from plaid.model.item_remove_request import ItemRemoveRequest

PLAID_ENV = (__import__("os").getenv("PLAID_ENV") or __import__("os").getenv("PLAID_ENVIRONMENT") or "sandbox")
PLAID_ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "development": plaid.Environment.Development,
    "production": plaid.Environment.Production,
}


def main() -> int:
    db.init_db()
    items = db.iter_items_with_tokens()
    if not items:
        print("No linked items in SQLite.")
        return 0

    configuration = plaid.Configuration(
        host=PLAID_ENVIRONMENTS[PLAID_ENV],
        api_key={
            "clientId": __import__("os").getenv("PLAID_CLIENT_ID"),
            "secret": __import__("os").getenv("PLAID_SECRET"),
            "plaidVersion": "2020-09-14",
        },
    )
    client = plaid_api.PlaidApi(plaid.ApiClient(configuration))

    for row in items:
        item_id = row["item_id"]
        label = row.get("institution_name") or item_id
        try:
            client.item_remove(ItemRemoveRequest(access_token=row["access_token"]))
            print(f"Removed from Plaid: {label}")
        except plaid.ApiException as e:
            body = json.loads(e.body) if e.body else {}
            print(f"Plaid remove failed for {label}: {body}", file=sys.stderr)
            print("Deleting local row anyway.", file=sys.stderr)
        db.delete_item(item_id)
        print(f"Cleared local data: {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
