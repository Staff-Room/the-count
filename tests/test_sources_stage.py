"""
Group A — Sources stage (seam S1: Plaid API -> raw transaction store).

These pin the contract the Ingestion stage consumes. They run against the current
code (db.py, plaid_sync.py, app.py) with Plaid fully mocked — no network.

See docs/testing/plaid-to-coded-transactions.md (Group A) and
docs/testing/README.md for the invariants referenced (INV-*).
"""

from __future__ import annotations

import json
from types import SimpleNamespace


def _raw_txn(**overrides):
    """A raw Plaid transaction row as persisted by plaid_sync / db."""
    base = {
        "transaction_id": "txn-1",
        "item_id": "item-1",
        "account_id": "acct-1",
        "amount": 24.0,  # Plaid: positive == outflow
        "iso_currency_code": "USD",
        "date": "2025-01-10",
        "authorized_date": "2025-01-09",
        "name": "PADDLE.NET* N8N CLOUD1",
        "merchant_name": "n8n",
        "pending": False,
        "primary_category": "GENERAL_SERVICES",
        "detailed_category": "GENERAL_SERVICES_OTHER",
        "payment_channel": "online",
        "raw_json": json.dumps({"k": "v"}),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# A4 — INV-COMPLETE / INV-IDEMPOTENT (inner-loop)
# ---------------------------------------------------------------------------
def test_a4_upsert_is_idempotent(fresh_db):
    db = fresh_db
    db.upsert_item("item-1", "access-token")
    db.upsert_transaction_row(_raw_txn())
    db.upsert_transaction_row(_raw_txn(amount=99.0))  # same id -> update, not a 2nd row

    assert db.transaction_counts()["count"] == 1
    rows, total = db.fetch_transactions(limit=10)
    assert total == 1
    assert rows[0]["amount"] == 99.0


def test_a4_apply_sync_removed_deletes(fresh_db):
    db = fresh_db
    import plaid_sync

    db.upsert_item("item-1", "tok")
    db.upsert_transaction_row(_raw_txn(transaction_id="gone"))

    resp = SimpleNamespace(added=[], modified=[], removed=[{"transaction_id": "gone"}])
    stats = plaid_sync.apply_sync_response("item-1", resp)

    assert stats == {"added": 0, "modified": 0, "removed": 1}
    assert db.transaction_counts()["count"] == 0


# ---------------------------------------------------------------------------
# A5 — INV-SIGN (raw) + provenance-seed fidelity (inner-loop)
# ---------------------------------------------------------------------------
def test_a5_sign_is_preserved(fresh_db):
    db = fresh_db
    db.upsert_item("item-1", "tok")
    db.upsert_transaction_row(_raw_txn(transaction_id="out", amount=24.0))      # outflow
    db.upsert_transaction_row(
        _raw_txn(transaction_id="in", amount=-1149.48,                          # inflow
                 name="External Deposit VENMO - CASHOUT")
    )

    rows, _ = db.fetch_transactions(limit=10)
    by_id = {r["transaction_id"]: r for r in rows}
    assert by_id["out"]["amount"] == 24.0       # Plaid outflow stays positive
    assert by_id["in"]["amount"] == -1149.48    # Plaid inflow stays negative


def test_a5_category_and_channel_fidelity(fresh_db):
    db = fresh_db
    db.upsert_item("item-1", "tok")
    db.upsert_transaction_row(_raw_txn())

    rows, _ = db.fetch_transactions(limit=10)
    r = rows[0]
    assert r["iso_currency_code"] == "USD"
    assert r["primary_category"] == "GENERAL_SERVICES"     # PFC seed (card-issuer ADR)
    assert r["detailed_category"] == "GENERAL_SERVICES_OTHER"
    assert r["payment_channel"] == "online"


def test_a5_raw_json_retained_for_provenance(fresh_db):
    db = fresh_db
    db.upsert_item("item-1", "tok")
    payload = {"personal_finance_category": {"primary": "GENERAL_SERVICES"}}
    db.upsert_transaction_row(_raw_txn(raw_json=json.dumps(payload)))

    with db.connection() as conn:
        row = conn.execute(
            "SELECT raw_json FROM transactions WHERE transaction_id = 'txn-1'"
        ).fetchone()
    assert json.loads(row["raw_json"])["personal_finance_category"]["primary"] == "GENERAL_SERVICES"


# ---------------------------------------------------------------------------
# A1–A3, A6 — connection & pull mechanics (outer-loop), Plaid mocked
# ---------------------------------------------------------------------------
def test_a1_create_link_token(app_mod, http, monkeypatch):
    monkeypatch.setattr(
        app_mod.plaid_client,
        "link_token_create",
        lambda req: {"link_token": "link-sandbox-1", "expiration": "2026-01-01T00:00:00Z"},
    )
    resp = http.post("/api/create_link_token")
    assert resp.status_code == 200
    assert resp.get_json()["link_token"] == "link-sandbox-1"


def test_a2_exchange_persists_item(app_mod, http, monkeypatch, fresh_db):
    monkeypatch.setattr(
        app_mod.plaid_client,
        "item_public_token_exchange",
        lambda req: {"access_token": "access-1", "item_id": "item-xyz"},
    )
    resp = http.post(
        "/api/exchange_public_token",
        json={
            "public_token": "public-1",
            "metadata": {"institution": {"institution_id": "ins_1", "name": "Test Bank"}},
        },
    )
    assert resp.status_code == 200
    item = fresh_db.get_item("item-xyz")
    assert item is not None
    assert item["access_token"] == "access-1"
    assert item["institution_name"] == "Test Bank"


def test_a3_sync_paginates_until_done_and_persists_cursor(app_mod, http, monkeypatch, fresh_db):
    fresh_db.upsert_item("item-1", "access-1", institution_name="Test Bank")
    pages = [
        SimpleNamespace(added=[], modified=[], removed=[], has_more=True, next_cursor="cur-1"),
        SimpleNamespace(added=[], modified=[], removed=[], has_more=False, next_cursor="cur-2"),
    ]
    calls = {"n": 0}

    def fake_sync(req):
        page = pages[calls["n"]]
        calls["n"] += 1
        return page

    monkeypatch.setattr(app_mod.plaid_client, "transactions_sync", fake_sync)
    resp = http.post("/api/sync_transactions")
    assert resp.status_code == 200
    assert calls["n"] == 2                       # paginated until has_more == False
    assert fresh_db.get_cursor("item-1") == "cur-2"


def test_a6_full_resets_cursor_then_replays(app_mod, http, monkeypatch, fresh_db):
    fresh_db.upsert_item("item-1", "access-1")
    fresh_db.set_cursor("item-1", "old-cursor")

    monkeypatch.setattr(
        app_mod.plaid_client,
        "transactions_sync",
        lambda req: SimpleNamespace(
            added=[], modified=[], removed=[], has_more=False, next_cursor="new-cursor"
        ),
    )
    resp = http.post("/api/sync_transactions?full=1")
    assert resp.status_code == 200
    assert fresh_db.get_cursor("item-1") == "new-cursor"
