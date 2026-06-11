"""Vercel cron entrypoint: nightly Plaid transactions/sync for all active items.

This is the ONLY surface deployed to Vercel — the Flask app's unauthenticated
endpoints stay local (see O3 in docs/integrations). Auth: Vercel cron invokes
GET with `Authorization: Bearer $CRON_SECRET`; anything else gets 401.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "backend"))

import db  # noqa: E402
import plaid  # noqa: E402
import plaid_sync  # noqa: E402
from plaid.api import plaid_api  # noqa: E402
from plaid.model.accounts_get_request import AccountsGetRequest  # noqa: E402
from plaid.model.transactions_sync_request import TransactionsSyncRequest  # noqa: E402

MIN_PAGE_INTERVAL_S = 0.5

ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def _client() -> plaid_api.PlaidApi:
    env = (os.getenv("PLAID_ENV") or os.getenv("PLAID_ENVIRONMENT") or "production").lower()
    configuration = plaid.Configuration(
        host=ENV_MAP.get(env, plaid.Environment.Production),
        api_key={
            "clientId": os.getenv("PLAID_CLIENT_ID"),
            "secret": os.getenv("PLAID_SECRET"),
            "plaidVersion": "2020-09-14",
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _sync_one(client: plaid_api.PlaidApi, item_id: str, access_token: str) -> dict:
    cursor = db.get_cursor(item_id)
    stats = {"added": 0, "modified": 0, "removed": 0, "pages": 0}
    while True:
        t0 = time.time()
        req = (
            TransactionsSyncRequest(access_token=access_token, cursor=cursor)
            if cursor
            else TransactionsSyncRequest(access_token=access_token)
        )
        response = client.transactions_sync(req)
        page = plaid_sync.apply_sync_response(item_id, response)
        for k in ("added", "modified", "removed"):
            stats[k] += page[k]
        stats["pages"] += 1
        cursor = response.next_cursor
        if not response.has_more:
            break
        elapsed = time.time() - t0
        if elapsed < MIN_PAGE_INTERVAL_S:
            time.sleep(MIN_PAGE_INTERVAL_S - elapsed)
    db.set_cursor(item_id, cursor, error=None)
    return stats


def run_sync() -> dict:
    if db.STORE != "supabase":
        raise RuntimeError(f"refusing to run with BACKEND_STORE={db.STORE!r} (need supabase)")
    client = _client()
    results = []
    ok = True
    for row in db.iter_items_with_tokens():
        item_id = row["item_id"]
        entry = {"item_id": item_id, "institution": row.get("institution_name")}
        try:
            entry.update(_sync_one(client, item_id, row["access_token"]))
            accounts = (
                client.accounts_get(AccountsGetRequest(access_token=row["access_token"]))
                .to_dict()
                .get("accounts")
                or []
            )
            db.upsert_accounts(item_id, accounts)
            entry["accounts"] = len(accounts)
        except plaid.ApiException as e:
            ok = False
            try:
                err = json.loads(e.body)
            except Exception:
                err = {"raw": str(e.body)[:500]}
            entry["error"] = err.get("error_code") or "PLAID_ERROR"
            db.set_cursor(item_id, db.get_cursor(item_id), error=json.dumps(err))
        except Exception as e:
            ok = False
            entry["error"] = str(e)[:500]
            db.set_cursor(item_id, db.get_cursor(item_id), error=str(e))
        results.append(entry)
    return {"ok": ok, "items": results}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel runtime contract)
        secret = os.getenv("CRON_SECRET")
        auth = self.headers.get("Authorization", "")
        if not secret or auth != f"Bearer {secret}":
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            self._respond(200, run_sync())
        except Exception as e:
            self._respond(500, {"ok": False, "error": str(e)[:500]})

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
