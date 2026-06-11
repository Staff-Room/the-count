"""Vercel function: on-demand single-item Plaid sync.

Called by the StaffRoomAI website right after Plaid Link
(POST {THE_COUNT_SYNC_URL}/api/sync/item with {"item_id": ...}) so a newly
linked account's transactions land without waiting for the nightly cron.

Auth fails closed: SYNC_TRIGGER_SECRET must be set and callers must send it
in the X-Sync-Secret header (constant-time compare).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "backend"))

import db  # noqa: E402
import plaid  # noqa: E402
import plaid_sync  # noqa: E402


def authorized(provided: str) -> bool:
    secret = os.getenv("SYNC_TRIGGER_SECRET", "").strip()
    return bool(secret) and hmac.compare_digest(provided, secret)


def run_item_sync(item_id: str, client=None) -> tuple[int, dict]:
    """Sync one item into the supabase store. Returns (status, payload)."""
    if db.STORE != "supabase":
        return 500, {
            "ok": False,
            "error": f"refusing to run with BACKEND_STORE={db.STORE!r} (need supabase)",
        }
    if hasattr(db, "clear_caches"):
        db.clear_caches()  # warm instances reuse module state; rules may have changed
    row = db.get_item(item_id)
    if not row:
        return 404, {"error": "Unknown item"}
    client = client or plaid_sync.make_client()
    try:
        stats = plaid_sync.sync_item_and_accounts(client, item_id, row["access_token"])
        return 200, {"ok": True, "item_id": item_id, **stats}
    except plaid.ApiException as e:
        try:
            err = json.loads(e.body)
        except Exception:
            err = {"raw": str(e.body)[:500]}
        db.set_cursor(item_id, db.get_cursor(item_id), error=json.dumps(err))
        return 502, {"ok": False, "item_id": item_id, "error": err}
    except Exception as e:
        db.set_cursor(item_id, db.get_cursor(item_id), error=str(e))
        return 500, {"ok": False, "item_id": item_id, "error": str(e)[:500]}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (Vercel runtime contract)
        if not authorized(self.headers.get("X-Sync-Secret", "")):
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        item_id = (body or {}).get("item_id")
        if not item_id:
            self._respond(400, {"error": "item_id is required"})
            return
        try:
            self._respond(*run_item_sync(item_id))
        except Exception as e:
            self._respond(500, {"ok": False, "error": str(e)[:500]})

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
