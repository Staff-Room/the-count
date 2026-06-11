"""
Pipeline hardening — per-page cursor durability, Plaid mutation restart,
and fail-closed auth + handler logic for the api/sync/item.py Vercel
function (the website's post-Link trigger).
"""

from __future__ import annotations

import json

import plaid
import pytest

from conftest import FakeClient, fake_page, plaid_error


# ---------------------------------------------------------------------------
# Cursor durability: progress survives a crash/timeout mid-pagination
# ---------------------------------------------------------------------------
def test_cursor_persisted_after_each_page(fresh_db):
    import plaid_sync

    fresh_db.upsert_item("item-1", "tok")
    client = FakeClient([fake_page("cur-1", has_more=True), RuntimeError("boom")])

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
            fake_page("cur-1", has_more=True),
            plaid_error("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"),
            fake_page("cur-2", has_more=True),
            fake_page("cur-3"),
        ]
    )

    stats = plaid_sync.sync_item(client, "item-1", "tok", min_page_interval_s=0)

    assert client.calls == ["start", "cur-1", "start", "cur-2"]
    assert fresh_db.get_cursor("item-1") == "cur-3"
    assert stats["pages"] == 2  # counts the restarted pass only


def test_non_mutation_plaid_error_propagates(fresh_db):
    import plaid_sync

    fresh_db.upsert_item("item-1", "tok")
    client = FakeClient([plaid_error("ITEM_LOGIN_REQUIRED")])

    with pytest.raises(plaid.ApiException):
        plaid_sync.sync_item(client, "item-1", "tok", min_page_interval_s=0)


# ---------------------------------------------------------------------------
# api/sync/item.py — fail-closed auth
# ---------------------------------------------------------------------------
def test_sync_item_unauthorized_when_secret_unset(sync_item_mod, monkeypatch):
    monkeypatch.delenv("SYNC_TRIGGER_SECRET", raising=False)
    assert sync_item_mod.authorized("anything") is False
    assert sync_item_mod.authorized("") is False


def test_sync_item_unauthorized_on_wrong_secret(sync_item_mod, monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_SECRET", "s3cret")
    assert sync_item_mod.authorized("nope") is False


def test_sync_item_authorized_with_correct_secret(sync_item_mod, monkeypatch):
    monkeypatch.setenv("SYNC_TRIGGER_SECRET", "s3cret")
    assert sync_item_mod.authorized("s3cret") is True


# ---------------------------------------------------------------------------
# api/sync/item.py — run_item_sync
# ---------------------------------------------------------------------------
def test_run_item_sync_refuses_non_supabase_store(sync_item_mod, fresh_db):
    status, payload = sync_item_mod.run_item_sync("item-1")
    assert status == 500
    assert "BACKEND_STORE" in payload["error"]


def test_run_item_sync_unknown_item_is_404(sync_item_mod, fresh_db, monkeypatch):
    # flip only the store guard; db's functions stay bound to the sqlite store
    # because db.py star-imports at module load
    monkeypatch.setattr(sync_item_mod.db, "STORE", "supabase")
    status, payload = sync_item_mod.run_item_sync("no-such-item")
    assert status == 404


def test_run_item_sync_happy_path(sync_item_mod, fresh_db, monkeypatch):
    monkeypatch.setattr(sync_item_mod.db, "STORE", "supabase")
    fresh_db.upsert_item("item-1", "tok")
    client = FakeClient([fake_page("cur-1")])

    status, payload = sync_item_mod.run_item_sync("item-1", client=client)

    assert status == 200
    assert payload["ok"] is True and payload["pages"] == 1
    assert fresh_db.get_cursor("item-1") == "cur-1"


def test_run_item_sync_plaid_error_is_502_and_persists_last_error(
    sync_item_mod, fresh_db, monkeypatch
):
    monkeypatch.setattr(sync_item_mod.db, "STORE", "supabase")
    fresh_db.upsert_item("item-1", "tok")
    client = FakeClient([plaid_error("ITEM_LOGIN_REQUIRED")])

    status, payload = sync_item_mod.run_item_sync("item-1", client=client)

    assert status == 502
    assert payload["ok"] is False
    assert payload["error"]["error_code"] == "ITEM_LOGIN_REQUIRED"
    item = fresh_db.list_items()[0]
    assert json.loads(item["last_error"])["error_code"] == "ITEM_LOGIN_REQUIRED"
