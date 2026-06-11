"""Vercel cron entrypoint: nightly Plaid transactions/sync for all active items.

This is the ONLY surface deployed to Vercel — the Flask app's unauthenticated
endpoints stay local (see O3 in docs/integrations). Auth: Vercel cron invokes
GET with `Authorization: Bearer $CRON_SECRET`; anything else gets 401.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "backend"))

import db  # noqa: E402
import plaid  # noqa: E402
import plaid_sync  # noqa: E402

MIN_PAGE_INTERVAL_S = 0.5


def run_sync() -> dict:
    if db.STORE != "supabase":
        raise RuntimeError(f"refusing to run with BACKEND_STORE={db.STORE!r} (need supabase)")
    if hasattr(db, "clear_caches"):
        db.clear_caches()  # warm instances reuse module state; rules may have changed
    client = plaid_sync.make_client()
    results = []
    ok = True
    for row in db.iter_items_with_tokens():
        item_id = row["item_id"]
        entry = {"item_id": item_id, "institution": row.get("institution_name")}
        try:
            entry.update(
                plaid_sync.sync_item_and_accounts(
                    client, item_id, row["access_token"],
                    min_page_interval_s=MIN_PAGE_INTERVAL_S,
                )
            )
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
        if not secret or not hmac.compare_digest(auth, f"Bearer {secret}"):
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
