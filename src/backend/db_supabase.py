"""
Supabase (PostgREST) persistence for Plaid items, sync cursors, and transactions.

Canonical item store is public.plaid_items (written by the StaffRoomAI website;
access tokens AES-256-GCM encrypted with INTEGRATIONS_ENCRYPTION_KEY). This
store reads those items, decrypts tokens, and writes plaid_accounts /
plaid_transactions / plaid_sync_cursors. Categorization rules are applied as
transactions land (see categorize.py).

Only items whose env matches PLAID_ENV are visible — sandbox tokens never mix
with production rows.

Required env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, INTEGRATIONS_ENCRYPTION_KEY.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import categorize

_BATCH = 500
_item_cache: dict[str, dict[str, Any]] = {}  # item_id -> {user_id, env, ...}
_mapping_cache: dict[str, list[dict[str, Any]]] = {}  # user_id -> sorted mappings

# All writes are PK-keyed upserts/deletes, so retrying POST/PATCH/DELETE on
# transient failures is safe.
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD", "POST", "PATCH", "DELETE"),
        )
    ),
)


def clear_caches() -> None:
    """Drop per-run caches. Long-lived processes and warm serverless
    instances reuse module state; call before each sync run so item and
    category-rule changes take effect."""
    _item_cache.clear()
    _mapping_cache.clear()


def plaid_env() -> str:
    env = (os.getenv("PLAID_ENV") or os.getenv("PLAID_ENVIRONMENT") or "sandbox").lower()
    if env == "development":  # Plaid retired development.plaid.com
        env = "production"
    return env


def _base_url() -> str:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    return f"{url}/rest/v1"


def _headers(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _req(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, str]] = None,
    payload: Any = None,
    prefer: Optional[str] = None,
) -> Any:
    extra = {"Prefer": prefer} if prefer else None
    resp = _session.request(
        method,
        f"{_base_url()}/{path}",
        params=params,
        json=payload,
        headers=_headers(extra),
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase {method} {path} failed ({resp.status_code}): {resp.text[:500]}")
    if resp.text:
        return resp.json()
    return None


# ---------------------------------------------------------------------------
# Token decryption (mirror of v0-staff-room-ai/lib/integrations/crypto.ts)
# ---------------------------------------------------------------------------
def _encryption_key() -> bytes:
    raw = os.getenv("INTEGRATIONS_ENCRYPTION_KEY") or ""
    if not raw:
        raise RuntimeError("INTEGRATIONS_ENCRYPTION_KEY is not set")
    try:
        b64 = base64.b64decode(raw, validate=True)
        if len(b64) == 32:
            return b64
    except (ValueError, binascii.Error):
        pass
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise RuntimeError("INTEGRATIONS_ENCRYPTION_KEY must be 32 bytes (base64 or hex)")
    return key


def decrypt_secret(payload: str) -> str:
    parts = payload.split(":")
    if len(parts) != 4 or parts[0] != "v1":
        raise ValueError("Invalid encrypted secret format")
    iv, tag, ciphertext = (base64.b64decode(p) for p in parts[1:])
    plaintext = AESGCM(_encryption_key()).decrypt(iv, ciphertext + tag, None)
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Schema lives in Supabase migrations; nothing to do."""


def _fetch_items(filters: dict[str, str]) -> list[dict[str, Any]]:
    params = {
        "select": "item_id,access_token_encrypted,institution_id,institution_name,user_id,env,status,created_at",
        "env": f"eq.{plaid_env()}",
        **filters,
    }
    rows = _req("GET", "plaid_items", params=params) or []
    out = []
    for r in rows:
        r["access_token"] = decrypt_secret(r.pop("access_token_encrypted"))
        _item_cache[r["item_id"]] = r
        out.append(r)
    return out


def iter_items_with_tokens() -> list[dict[str, Any]]:
    """Active items for the current PLAID_ENV, tokens decrypted."""
    items = _fetch_items({"status": "eq.active", "order": "created_at"})
    cursors = {
        c["item_id"]: c
        for c in (_req("GET", "plaid_sync_cursors", params={"select": "*"}) or [])
    }
    for r in items:
        c = cursors.get(r["item_id"], {})
        r["cursor"] = c.get("cursor")
        r["last_sync_at"] = c.get("last_sync_at")
        r["last_error"] = c.get("last_error")
    return items


def list_items() -> list[dict[str, Any]]:
    rows = iter_items_with_tokens()
    for r in rows:
        r.pop("access_token", None)
    return rows


def get_item(item_id: str) -> Optional[dict[str, Any]]:
    rows = _fetch_items({"item_id": f"eq.{item_id}"})
    return rows[0] if rows else None


def _item_meta(item_id: str) -> dict[str, Any]:
    if item_id not in _item_cache:
        rows = _fetch_items({"item_id": f"eq.{item_id}"})
        if not rows:
            raise RuntimeError(f"Unknown plaid item {item_id} for env={plaid_env()}")
    return _item_cache[item_id]


def upsert_item(*args: Any, **kwargs: Any) -> None:
    raise NotImplementedError(
        "Linking writes plaid_items via the StaffRoomAI website (Plaid Link + "
        "token encryption live there). The supabase store is sync-only."
    )


def delete_item(item_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _req(
        "PATCH",
        "plaid_items",
        params={"item_id": f"eq.{item_id}", "env": f"eq.{plaid_env()}"},
        payload={"status": "disconnected", "disconnected_at": now, "updated_at": now},
    )
    _item_cache.pop(item_id, None)


# ---------------------------------------------------------------------------
# Sync cursors
# ---------------------------------------------------------------------------
def get_cursor(item_id: str) -> Optional[str]:
    rows = _req(
        "GET",
        "plaid_sync_cursors",
        params={"select": "cursor", "item_id": f"eq.{item_id}"},
    )
    return rows[0]["cursor"] if rows else None


def set_cursor(item_id: str, cursor: Optional[str], error: Optional[str] = None) -> None:
    _req(
        "POST",
        "plaid_sync_cursors",
        payload={
            "item_id": item_id,
            "env": plaid_env(),
            "cursor": cursor,
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_error": error,
        },
        prefer="resolution=merge-duplicates",
    )


def reset_all_sync_cursors() -> int:
    rows = _req(
        "PATCH",
        "plaid_sync_cursors",
        params={"env": f"eq.{plaid_env()}"},
        payload={"cursor": None, "last_error": None},
        prefer="return=representation",
    )
    return len(rows or [])


# ---------------------------------------------------------------------------
# Transactions (+ categorization at landing)
# ---------------------------------------------------------------------------
def _mappings_for_user(user_id: str) -> list[dict[str, Any]]:
    if user_id not in _mapping_cache:
        _mapping_cache[user_id] = _req(
            "GET",
            "category_mappings",
            params={
                "select": "match_type,match_value,schedule_c_code,custom_category_id,gl_account_type,priority",
                "user_id": f"eq.{user_id}",
                "order": "priority",
            },
        ) or []
    return _mapping_cache[user_id]


def _manual_ids(transaction_ids: list[str]) -> set[str]:
    """Transactions already manually coded — never clobbered by rules."""
    manual: set[str] = set()
    for i in range(0, len(transaction_ids), _BATCH):
        chunk = ",".join(f'"{t}"' for t in transaction_ids[i : i + _BATCH])
        rows = _req(
            "GET",
            "plaid_transactions",
            params={
                "select": "transaction_id",
                "transaction_id": f"in.({chunk})",
                "categorized_by": "eq.manual",
            },
        ) or []
        manual.update(r["transaction_id"] for r in rows)
    return manual


def upsert_transactions(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    manual = _manual_ids([r["transaction_id"] for r in rows])

    coded_payload: list[dict[str, Any]] = []
    manual_payload: list[dict[str, Any]] = []
    for r in rows:
        meta = _item_meta(r["item_id"])
        base = {
            "transaction_id": r["transaction_id"],
            "item_id": r["item_id"],
            "account_id": r["account_id"],
            "user_id": meta["user_id"],
            "env": plaid_env(),
            "amount": r["amount"],
            "iso_currency_code": r.get("iso_currency_code"),
            "date": r["date"],
            "authorized_date": r.get("authorized_date"),
            "name": r.get("name"),
            "merchant_name": r.get("merchant_name"),
            "pending": bool(r.get("pending")),
            "primary_category": r.get("primary_category"),
            "detailed_category": r.get("detailed_category"),
            "payment_channel": r.get("payment_channel"),
            "raw": json.loads(r["raw_json"]) if r.get("raw_json") else None,
            "updated_at": now,
        }
        if r["transaction_id"] in manual:
            manual_payload.append(base)
        else:
            coding = categorize.resolve(base, _mappings_for_user(meta["user_id"]))
            coded_payload.append({**base, **coding})

    for payload in (coded_payload, manual_payload):
        for i in range(0, len(payload), _BATCH):
            _req(
                "POST",
                "plaid_transactions",
                payload=payload[i : i + _BATCH],
                prefer="resolution=merge-duplicates",
            )


def upsert_transaction_row(t: dict[str, Any]) -> None:
    upsert_transactions([t])


def delete_transactions(transaction_ids: list[str]) -> None:
    for i in range(0, len(transaction_ids), _BATCH):
        chunk = ",".join(f'"{t}"' for t in transaction_ids[i : i + _BATCH])
        _req("DELETE", "plaid_transactions", params={"transaction_id": f"in.({chunk})"})


def delete_transaction(transaction_id: str) -> None:
    delete_transactions([transaction_id])


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def upsert_accounts(item_id: str, accounts: list[dict[str, Any]]) -> None:
    """accounts: Plaid /accounts/get account dicts."""
    if not accounts:
        return
    meta = _item_meta(item_id)
    now = datetime.now(timezone.utc).isoformat()
    payload = []
    for a in accounts:
        b = a.get("balances") or {}
        payload.append(
            {
                "account_id": a["account_id"],
                "item_id": item_id,
                "user_id": meta["user_id"],
                "env": plaid_env(),
                "name": a.get("name"),
                "official_name": a.get("official_name"),
                "mask": a.get("mask"),
                "type": str(a.get("type") or "") or None,
                "subtype": str(a.get("subtype") or "") or None,
                "current_balance": b.get("current"),
                "available_balance": b.get("available"),
                "credit_limit": b.get("limit"),
                "iso_currency_code": b.get("iso_currency_code"),
                "updated_at": now,
            }
        )
    _req("POST", "plaid_accounts", payload=payload, prefer="resolution=merge-duplicates")


# ---------------------------------------------------------------------------
# Read surface (dashboard/API parity with the sqlite store)
# ---------------------------------------------------------------------------
def _tx_params(
    account_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    q: Optional[str] = None,
) -> dict[str, str]:
    params: dict[str, str] = {"env": f"eq.{plaid_env()}"}
    if account_id:
        params["account_id"] = f"eq.{account_id}"
    if since and until:
        params["and"] = f"(date.gte.{since},date.lte.{until})"
    elif since:
        params["date"] = f"gte.{since}"
    elif until:
        params["date"] = f"lte.{until}"
    if q:
        params["or"] = f"(name.ilike.*{q}*,merchant_name.ilike.*{q}*)"
    return params


def transaction_counts() -> dict[str, Any]:
    env = f"eq.{plaid_env()}"
    first = _req("GET", "plaid_transactions", params={"select": "date", "env": env, "order": "date.asc", "limit": "1"})
    last = _req("GET", "plaid_transactions", params={"select": "date", "env": env, "order": "date.desc", "limit": "1"})
    resp = _session.head(
        f"{_base_url()}/plaid_transactions",
        params={"select": "transaction_id", "env": env},
        headers=_headers({"Prefer": "count=exact"}),
        timeout=30,
    )
    content_range = resp.headers.get("Content-Range", "/0")
    count = int(content_range.split("/")[-1]) if "/" in content_range else 0
    return {
        "count": count,
        "min_date": first[0]["date"] if first else None,
        "max_date": last[0]["date"] if last else None,
    }


def fetch_transactions(
    *,
    limit: int = 100,
    offset: int = 0,
    account_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    q: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    params = _tx_params(account_id, since, until, q)
    params.update(
        {
            "select": "transaction_id,item_id,account_id,amount,iso_currency_code,date,"
            "authorized_date,name,merchant_name,pending,primary_category,"
            "detailed_category,payment_channel,schedule_c_code,custom_category_id",
            "order": "date.desc,transaction_id.desc",
            "limit": str(limit),
            "offset": str(offset),
        }
    )
    resp = _session.get(
        f"{_base_url()}/plaid_transactions",
        params=params,
        headers=_headers({"Prefer": "count=exact"}),
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase fetch_transactions failed ({resp.status_code}): {resp.text[:500]}")
    total = int(resp.headers.get("Content-Range", "/0").split("/")[-1] or 0)
    rows = resp.json()
    for r in rows:
        r["amount"] = float(r["amount"])
    return rows, total


def _amounts(params: dict[str, str], select: str) -> list[dict[str, Any]]:
    """Page through all matching rows (aggregates are computed client-side)."""
    out: list[dict[str, Any]] = []
    offset = 0
    # PostgREST silently caps responses at max-rows (Supabase default 1000);
    # paging above the cap makes the len(rows) < page check exit early.
    page = 1_000
    while True:
        rows = _req(
            "GET",
            "plaid_transactions",
            params={
                **params,
                "select": select,
                "order": "transaction_id",
                "limit": str(page),
                "offset": str(offset),
            },
        ) or []
        out.extend(rows)
        if len(rows) < page:
            return out
        offset += page


def spending_inflow_totals(
    since_date: str, until_date: Optional[str] = None
) -> dict[str, float]:
    """Plaid: positive amount = outflow; negative = inflow."""
    rows = _amounts(_tx_params(since=since_date, until=until_date), "amount")
    outflow = sum(float(r["amount"]) for r in rows if float(r["amount"]) > 0)
    inflow = sum(-float(r["amount"]) for r in rows if float(r["amount"]) < 0)
    return {"outflow": outflow, "inflow": inflow}


def sum_by_category(
    since: Optional[str] = None, outflows_only: bool = False
) -> list[dict[str, Any]]:
    params = _tx_params(since=since)
    if outflows_only:
        params["amount"] = "gt.0"
    rows = _amounts(params, "amount,primary_category")
    totals: dict[str, float] = {}
    for r in rows:
        cat = r.get("primary_category") or "UNCATEGORIZED"
        totals[cat] = totals.get(cat, 0.0) + float(r["amount"])
    return [
        {"category": k, "total": v}
        for k, v in sorted(totals.items(), key=lambda kv: abs(kv[1]), reverse=True)
    ]
