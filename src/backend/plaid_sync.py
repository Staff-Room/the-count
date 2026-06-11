"""
Apply Plaid transactions_sync responses to the active store (see db.py).
"""

from __future__ import annotations

import json
from typing import Any

from plaid.model.removed_transaction import RemovedTransaction
from plaid.model.transaction import Transaction

import db


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
