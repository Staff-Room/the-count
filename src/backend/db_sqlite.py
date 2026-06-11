"""
SQLite persistence for Plaid items, sync cursors, and cached transactions.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

_local = threading.local()


def get_db_path() -> Path:
    from os import getenv

    raw = getenv("THE_COUNT_DB_PATH", "")
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parent / "data" / "thecount.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_connection() -> sqlite3.Connection:
    path = get_db_path()
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect(path)
    return _local.conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                institution_id TEXT,
                institution_name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_cursors (
                item_id TEXT PRIMARY KEY,
                cursor TEXT,
                last_sync_at TEXT,
                last_error TEXT,
                FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                amount REAL NOT NULL,
                iso_currency_code TEXT,
                date TEXT NOT NULL,
                authorized_date TEXT,
                name TEXT,
                merchant_name TEXT,
                pending INTEGER NOT NULL DEFAULT 0,
                primary_category TEXT,
                detailed_category TEXT,
                payment_channel TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_item ON transactions(item_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
            CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
            """
        )


def upsert_item(
    item_id: str,
    access_token: str,
    institution_id: Optional[str] = None,
    institution_name: Optional[str] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO items (item_id, access_token, institution_id, institution_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                access_token = excluded.access_token,
                institution_id = COALESCE(excluded.institution_id, items.institution_id),
                institution_name = COALESCE(excluded.institution_name, items.institution_name)
            """,
            (item_id, access_token, institution_id, institution_name, now),
        )
        conn.execute(
            """
            INSERT INTO sync_cursors (item_id, cursor, last_sync_at, last_error)
            VALUES (?, NULL, NULL, NULL)
            ON CONFLICT(item_id) DO NOTHING
            """,
            (item_id,),
        )


def list_items() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT i.item_id, i.institution_id, i.institution_name, i.created_at,
                   c.cursor, c.last_sync_at, c.last_error
            FROM items i
            LEFT JOIN sync_cursors c ON c.item_id = i.item_id
            ORDER BY i.created_at
            """
        ).fetchall()
    return [dict(r) for r in rows]


def iter_items_with_tokens() -> list[dict[str, Any]]:
    """Server-side only: item rows including access_token for Plaid calls."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT i.item_id, i.access_token, i.institution_id, i.institution_name, i.created_at,
                   c.cursor, c.last_sync_at, c.last_error
            FROM items i
            LEFT JOIN sync_cursors c ON c.item_id = i.item_id
            ORDER BY i.created_at
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_item(item_id: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE item_id = ?", (item_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_item(item_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))


def get_cursor(item_id: str) -> Optional[str]:
    with connection() as conn:
        row = conn.execute(
            "SELECT cursor FROM sync_cursors WHERE item_id = ?", (item_id,)
        ).fetchone()
    return row["cursor"] if row and row["cursor"] else None


def set_cursor(item_id: str, cursor: Optional[str], error: Optional[str] = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO sync_cursors (item_id, cursor, last_sync_at, last_error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                cursor = excluded.cursor,
                last_sync_at = excluded.last_sync_at,
                last_error = excluded.last_error
            """,
            (item_id, cursor, now, error),
        )


def reset_all_sync_cursors() -> int:
    """Clear Plaid /transactions/sync cursors so the next sync replays history."""
    with connection() as conn:
        cur = conn.execute(
            "UPDATE sync_cursors SET cursor = NULL, last_error = NULL"
        )
        return cur.rowcount


def upsert_transaction_row(t: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, item_id, account_id, amount, iso_currency_code,
                date, authorized_date, name, merchant_name, pending,
                primary_category, detailed_category, payment_channel, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                item_id = excluded.item_id,
                account_id = excluded.account_id,
                amount = excluded.amount,
                iso_currency_code = excluded.iso_currency_code,
                date = excluded.date,
                authorized_date = excluded.authorized_date,
                name = excluded.name,
                merchant_name = excluded.merchant_name,
                pending = excluded.pending,
                primary_category = excluded.primary_category,
                detailed_category = excluded.detailed_category,
                payment_channel = excluded.payment_channel,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                t["transaction_id"],
                t["item_id"],
                t["account_id"],
                t["amount"],
                t.get("iso_currency_code"),
                t["date"],
                t.get("authorized_date"),
                t.get("name"),
                t.get("merchant_name"),
                1 if t.get("pending") else 0,
                t.get("primary_category"),
                t.get("detailed_category"),
                t.get("payment_channel"),
                t.get("raw_json"),
                now,
            ),
        )


def upsert_transactions(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        upsert_transaction_row(r)


def delete_transaction(transaction_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))


def delete_transactions(transaction_ids: list[str]) -> None:
    for t in transaction_ids:
        delete_transaction(t)


def transaction_counts() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS min_d, MAX(date) AS max_d FROM transactions"
        ).fetchone()
    return {"count": row["n"], "min_date": row["min_d"], "max_date": row["max_d"]}


def fetch_transactions(
    *,
    limit: int = 100,
    offset: int = 0,
    account_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    q: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if since:
        clauses.append("date >= ?")
        params.append(since)
    if until:
        clauses.append("date <= ?")
        params.append(until)
    if q:
        clauses.append("(LOWER(name) LIKE ? OR LOWER(COALESCE(merchant_name,'')) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions{where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT transaction_id, item_id, account_id, amount, iso_currency_code,
                   date, authorized_date, name, merchant_name, pending,
                   primary_category, detailed_category, payment_channel
            FROM transactions
            {where}
            ORDER BY date DESC, transaction_id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["pending"] = bool(d["pending"])
        out.append(d)
    return out, total


def spending_inflow_totals(
    since_date: str, until_date: Optional[str] = None
) -> dict[str, float]:
    """Plaid: positive amount = outflow; negative = inflow."""
    clauses = ["date >= ?"]
    params: list[Any] = [since_date]
    if until_date:
        clauses.append("date <= ?")
        params.append(until_date)
    where = " WHERE " + " AND ".join(clauses)
    with connection() as conn:
        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) AS outflow,
                COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) AS inflow
            FROM transactions
            {where}
            """,
            params,
        ).fetchone()
    return {"outflow": float(row["outflow"]), "inflow": float(row["inflow"])}


def sum_by_category(
    since: Optional[str] = None, outflows_only: bool = False
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if since:
        clauses.append("date >= ?")
        params.append(since)
    if outflows_only:
        clauses.append("amount > 0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(primary_category, 'UNCATEGORIZED') AS cat,
                   SUM(amount) AS total
            FROM transactions
            {where}
            GROUP BY cat
            ORDER BY ABS(SUM(amount)) DESC
            """,
            params,
        ).fetchall()
    return [{"category": r["cat"], "total": r["total"]} for r in rows]
