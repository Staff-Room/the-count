"""
Pipeline hardening — per-page cursor durability, Plaid mutation restart,
and fail-closed auth on the website's sync trigger.

These extend Group A (seam S1): the same sync loop now backs app.py,
api/cron-sync.py, and scripts/sync_plaid_now.py via plaid_sync.sync_item.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import plaid
import pytest


class FakeClient:
    """Scripted transactions_sync: each entry is a response page or an
    exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def transactions_sync(self, req):
        self.calls.append(getattr(req, "cursor", None))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _page(next_cursor, has_more=False):
    return SimpleNamespace(
        added=[], modified=[], removed=[], has_more=has_more, next_cursor=next_cursor
    )


def _mutation_error():
    e = plaid.ApiException(status=400, reason="Bad Request")
    e.body = json.dumps(
        {"error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"}
    )
    return e


# ---------------------------------------------------------------------------
# Cursor durability: progress survives a crash/timeout mid-pagination
# ---------------------------------------------------------------------------
def test_cursor_persisted_after_each_page(fresh_db):
    import plaid_sync

    fresh_db.upsert_item("item-1", "tok")
    client = FakeClient([_page("cur-1", has_more=True), RuntimeError("boom")])

    with pytest.raises(RuntimeError):
        plaid_sync.sync_item(client, "item-1", "tok", min_page_interval_s=0)

    # the first page's cursor was saved before the failure — the next run
    # resumes from cur-1 instead of re-pulling from scratch
    assert fresh_db.get_cursor("item-1") == "cur-1"


def test_mutation_during_pagination_restarts_from_start_cursor(fresh_db):
    import plaid_sync

    fresh_db.upsert_item("item-1", "tok")
    fresh_db.set_cursor("item-1", "start")
    client = FakeClient(
        [
            _page("cur-1", has_more=True),
            _mutation_error(),
            _page("cur-2", has_more=True),
            _page("cur-3"),
        ]
    )

    stats = plaid_sync.sync_item(client, "item-1", "tok", min_page_interval_s=0)

    assert client.calls == ["start", "cur-1", "start", "cur-2"]
    assert fresh_db.get_cursor("item-1") == "cur-3"
    assert stats["pages"] == 2  # counts the restarted pass only


def test_non_mutation_plaid_error_propagates(fresh_db):
    import plaid_sync

    fresh_db.upsert_item("item-1", "tok")
    e = plaid.ApiException(status=400, reason="Bad Request")
    e.body = json.dumps({"error_code": "ITEM_LOGIN_REQUIRED"})
    client = FakeClient([e])

    with pytest.raises(plaid.ApiException):
        plaid_sync.sync_item(client, "item-1", "tok", min_page_interval_s=0)


# ---------------------------------------------------------------------------
# /api/sync/item fails closed
# ---------------------------------------------------------------------------
def test_sync_item_401_when_secret_unset(app_mod, http, monkeypatch):
    monkeypatch.delenv("SYNC_TRIGGER_SECRET", raising=False)
    resp = http.post("/api/sync/item", json={"item_id": "x"})
    assert resp.status_code == 401


def test_sync_item_401_on_wrong_secret(app_mod, http, monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_SECRET", "s3cret")
    resp = http.post(
        "/api/sync/item", json={"item_id": "x"}, headers={"X-Sync-Secret": "nope"}
    )
    assert resp.status_code == 401


def test_sync_item_authorized_with_correct_secret(app_mod, http, monkeypatch, fresh_db):
    monkeypatch.setenv("SYNC_TRIGGER_SECRET", "s3cret")
    resp = http.post(
        "/api/sync/item",
        json={"item_id": "missing"},
        headers={"X-Sync-Secret": "s3cret"},
    )
    assert resp.status_code == 404  # authorized; item simply unknown
