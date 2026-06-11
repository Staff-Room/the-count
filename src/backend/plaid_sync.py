"""
Apply Plaid transactions_sync responses to the active store (see db.py).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.removed_transaction import RemovedTransaction
from plaid.model.transaction import Transaction
from plaid.model.transactions_sync_request import TransactionsSyncRequest

import db

MUTATION_ERROR_CODE = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
MAX_MUTATION_RESTARTS = 3

ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def make_client() -> plaid_api.PlaidApi:
    """Plaid API client for the configured PLAID_ENV (default production)."""
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


def _transaction_to_row(tx: Transaction, item_id: str) -> dict[str, Any]:
    d = tx.to_dict()
    pfc = d.get("personal_finance_category") or {}
    primary = pfc.get("primary") if isinstance(pfc, dict) else None
    detailed = pfc.get("detailed") if isinstance(pfc, dict) else None
    cat_legacy = d.get("category") or []
    if not primary and cat_legacy:
        primary = cat_legacy[0] if cat_legacy else None

    date_s = d.get("date")
    if hasattr(date_s, "isoformat"):
        date_s = date_s.isoformat()
    auth = d.get("authorized_date")
    if auth and hasattr(auth, "isoformat"):
        auth = auth.isoformat()

    return {
        "transaction_id": d["transaction_id"],
        "item_id": item_id,
        "account_id": d["account_id"],
        "amount": float(d["amount"]),
        "iso_currency_code": d.get("iso_currency_code"),
        "date": str(date_s) if date_s is not None else "",
        "authorized_date": str(auth) if auth else None,
        "name": d.get("name"),
        "merchant_name": d.get("merchant_name"),
        "pending": bool(d.get("pending")),
        "primary_category": primary,
        "detailed_category": detailed,
        "payment_channel": d.get("payment_channel"),
        "raw_json": json.dumps(d, default=str),
    }


def apply_sync_response(item_id: str, response: Any) -> dict[str, int]:
    """Persist added/modified/removed from one transactions_sync page."""
    added = getattr(response, "added", None) or []
    modified = getattr(response, "modified", None) or []
    removed = getattr(response, "removed", None) or []

    rows = [
        _transaction_to_row(tx, item_id)
        for tx in list(added) + list(modified)
        if isinstance(tx, Transaction)
    ]
    db.upsert_transactions(rows)

    removed_ids = []
    for rt in removed:
        if isinstance(rt, RemovedTransaction):
            tid = rt.transaction_id
        elif isinstance(rt, dict):
            tid = rt.get("transaction_id")
        else:
            tid = getattr(rt, "transaction_id", None)
        if tid:
            removed_ids.append(tid)
    db.delete_transactions(removed_ids)

    return {
        "added": len(added),
        "modified": len(modified),
        "removed": len(removed),
    }


def _plaid_error_code(e: plaid.ApiException) -> str | None:
    try:
        return json.loads(e.body).get("error_code")
    except Exception:
        return None


def sync_item(
    client: Any,
    item_id: str,
    access_token: str,
    *,
    min_page_interval_s: float = 0.5,
) -> dict[str, int]:
    """Run transactions_sync to completion for one item.

    The cursor is persisted after every applied page (pages are applied
    before the cursor is saved, and upserts/deletes are idempotent), so an
    interrupt or serverless timeout never loses progress — the next run
    resumes where this one stopped instead of re-pulling from scratch.

    On TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION, pagination restarts
    from the cursor it began with, per Plaid's protocol; replayed pages are
    idempotent.
    """
    start_cursor = db.get_cursor(item_id)
    cursor = start_cursor
    restarts = 0
    stats = {"added": 0, "modified": 0, "removed": 0, "pages": 0}
    while True:
        t0 = time.time()
        req = (
            TransactionsSyncRequest(access_token=access_token, cursor=cursor)
            if cursor
            else TransactionsSyncRequest(access_token=access_token)
        )
        try:
            response = client.transactions_sync(req)
        except plaid.ApiException as e:
            if _plaid_error_code(e) == MUTATION_ERROR_CODE and restarts < MAX_MUTATION_RESTARTS:
                restarts += 1
                cursor = start_cursor
                db.set_cursor(item_id, cursor, error=None)
                stats = {"added": 0, "modified": 0, "removed": 0, "pages": 0}
                continue
            raise
        page = apply_sync_response(item_id, response)
        for k in ("added", "modified", "removed"):
            stats[k] += page[k]
        stats["pages"] += 1
        cursor = response.next_cursor
        db.set_cursor(item_id, cursor, error=None)
        if not response.has_more:
            return stats
        elapsed = time.time() - t0
        if elapsed < min_page_interval_s:
            time.sleep(min_page_interval_s - elapsed)


def sync_item_and_accounts(
    client: Any,
    item_id: str,
    access_token: str,
    *,
    min_page_interval_s: float = 0.5,
) -> dict[str, int]:
    """sync_item plus an account-balance refresh (stores that support it)."""
    stats = sync_item(
        client, item_id, access_token, min_page_interval_s=min_page_interval_s
    )
    if hasattr(db, "upsert_accounts"):
        accounts = (
            client.accounts_get(AccountsGetRequest(access_token=access_token))
            .to_dict()
            .get("accounts")
            or []
        )
        db.upsert_accounts(item_id, accounts)
        stats["accounts"] = len(accounts)
    return stats
