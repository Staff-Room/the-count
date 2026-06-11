"""
Group A — Sources stage (seam S1: Plaid API -> raw transaction store).

These pin the contract the Ingestion stage consumes. They run against the current
code (db.py, plaid_sync.py) with Plaid fully mocked — no network.

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
# A3, A6 — pull mechanics (Plaid mocked). A1/A2 (Link flow) retired: linking
# is website-owned (contract M3); the backend's only sources surface is the
# sync loop itself.
# ---------------------------------------------------------------------------
def test_a3_sync_paginates_until_done_and_persists_cursor(fresh_db):
    import plaid_sync
    from conftest import FakeClient, fake_page

    fresh_db.upsert_item("item-1", "access-1", institution_name="Test Bank")
    client = FakeClient([fake_page("cur-1", has_more=True), fake_page("cur-2")])

    stats = plaid_sync.sync_item(client, "item-1", "access-1", min_page_interval_s=0)

    assert stats["pages"] == 2                   # paginated until has_more == False
    assert fresh_db.get_cursor("item-1") == "cur-2"


def test_a6_full_resets_cursor_then_replays(fresh_db):
    import plaid_sync
    from conftest import FakeClient, fake_page

    fresh_db.upsert_item("item-1", "access-1")
    fresh_db.set_cursor("item-1", "old-cursor")

    fresh_db.reset_all_sync_cursors()            # the --full path
    client = FakeClient([fake_page("new-cursor")])
    plaid_sync.sync_item(client, "item-1", "access-1", min_page_interval_s=0)

    assert client.calls == [None]                # replayed from scratch, not old-cursor
    assert fresh_db.get_cursor("item-1") == "new-cursor"
