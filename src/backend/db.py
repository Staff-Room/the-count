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

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'spend',
                color TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tag_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag_id INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                match_field TEXT NOT NULL,
                match_op TEXT NOT NULL,
                match_value TEXT NOT NULL,
                min_amount REAL,
                max_amount REAL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tag_rules_tag ON tag_rules(tag_id, priority);
            CREATE INDEX IF NOT EXISTS idx_tag_rules_priority ON tag_rules(enabled, priority);

            CREATE TABLE IF NOT EXISTS transaction_tags (
                transaction_id TEXT PRIMARY KEY,
                tag_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'rule',
                rule_id INTEGER,
                confidence REAL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                FOREIGN KEY (rule_id) REFERENCES tag_rules(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_transaction_tags_tag ON transaction_tags(tag_id);
            CREATE INDEX IF NOT EXISTS idx_transaction_tags_source ON transaction_tags(source);

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                tag_id INTEGER,
                account_id TEXT,
                period TEXT NOT NULL DEFAULT 'month',
                period_start TEXT,
                target_amount REAL,
                target_count INTEGER,
                currency TEXT NOT NULL DEFAULT 'USD',
                rollover INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_goals_active_kind ON goals(active, kind);

            CREATE TABLE IF NOT EXISTS goal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'contribution',
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_goal_events_goal ON goal_events(goal_id, event_date);
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


def delete_transaction(transaction_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,))


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
    tag_id: Optional[int] = None,
    untagged_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if since:
        clauses.append("t.date >= ?")
        params.append(since)
    if until:
        clauses.append("t.date <= ?")
        params.append(until)
    if q:
        clauses.append("(LOWER(t.name) LIKE ? OR LOWER(COALESCE(t.merchant_name,'')) LIKE ?)")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    if tag_id is not None:
        clauses.append("tt.tag_id = ?")
        params.append(tag_id)
    if untagged_only:
        clauses.append("tt.tag_id IS NULL")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with connection() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM transactions t
            LEFT JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
            {where}
            """,
            params,
        ).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT t.transaction_id, t.item_id, t.account_id, t.amount, t.iso_currency_code,
                   t.date, t.authorized_date, t.name, t.merchant_name, t.pending,
                   t.primary_category, t.detailed_category, t.payment_channel,
                   tt.tag_id AS tag_id, tt.source AS tag_source,
                   tg.key AS tag_key, tg.label AS tag_label, tg.color AS tag_color
            FROM transactions t
            LEFT JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
            LEFT JOIN tags tg ON tg.id = tt.tag_id
            {where}
            ORDER BY t.date DESC, t.transaction_id DESC
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


def list_tags() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, key, label, kind, color FROM tags ORDER BY label"
        ).fetchall()
    return [dict(r) for r in rows]


def get_tag(tag_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, key, label, kind, color FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()
    return dict(row) if row else None


def get_tag_by_key(key: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT id, key, label, kind, color FROM tags WHERE key = ?", (key,)
        ).fetchone()
    return dict(row) if row else None


def insert_tag(
    key: str, label: str, kind: str = "spend", color: Optional[str] = None
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO tags (key, label, kind, color, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                label = excluded.label,
                kind = excluded.kind,
                color = COALESCE(excluded.color, tags.color)
            RETURNING id
            """,
            (key, label, kind, color, now),
        )
        row = cur.fetchone()
    return int(row["id"])


def update_tag(
    tag_id: int,
    label: Optional[str] = None,
    kind: Optional[str] = None,
    color: Optional[str] = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if label is not None:
        sets.append("label = ?")
        params.append(label)
    if kind is not None:
        sets.append("kind = ?")
        params.append(kind)
    if color is not None:
        sets.append("color = ?")
        params.append(color)
    if not sets:
        return
    params.append(tag_id)
    with connection() as conn:
        conn.execute(f"UPDATE tags SET {', '.join(sets)} WHERE id = ?", params)


def delete_tag(tag_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def list_tag_rules(tag_id: Optional[int] = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if tag_id is not None:
        clauses.append("r.tag_id = ?")
        params.append(tag_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT r.id, r.tag_id, t.key AS tag_key, t.label AS tag_label,
                   r.priority, r.match_field, r.match_op, r.match_value,
                   r.min_amount, r.max_amount, r.enabled
            FROM tag_rules r
            JOIN tags t ON t.id = r.tag_id
            {where}
            ORDER BY r.priority, r.id
            """,
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def insert_tag_rule(
    tag_id: int,
    match_field: str,
    match_op: str,
    match_value: str,
    *,
    priority: int = 100,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    enabled: bool = True,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO tag_rules (
                tag_id, priority, match_field, match_op, match_value,
                min_amount, max_amount, enabled, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                tag_id,
                priority,
                match_field,
                match_op,
                match_value,
                min_amount,
                max_amount,
                1 if enabled else 0,
                now,
            ),
        )
        row = cur.fetchone()
    return int(row["id"])


def update_tag_rule(rule_id: int, **fields: Any) -> None:
    allowed = {
        "tag_id",
        "priority",
        "match_field",
        "match_op",
        "match_value",
        "min_amount",
        "max_amount",
        "enabled",
    }
    sets: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(rule_id)
    with connection() as conn:
        conn.execute(f"UPDATE tag_rules SET {', '.join(sets)} WHERE id = ?", params)


def delete_tag_rule(rule_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM tag_rules WHERE id = ?", (rule_id,))


def get_tag_rule(rule_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM tag_rules WHERE id = ?", (rule_id,)
        ).fetchone()
    return dict(row) if row else None


def get_transaction_tag(transaction_id: str) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM transaction_tags WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_transaction_tag(
    transaction_id: str,
    tag_id: int,
    source: str,
    rule_id: Optional[int] = None,
    confidence: Optional[float] = None,
    *,
    overwrite_manual: bool = False,
) -> None:
    """Idempotent tag write. Manual tags survive rule re-runs unless overwrite_manual."""
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        if not overwrite_manual:
            existing = conn.execute(
                "SELECT source FROM transaction_tags WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if existing and existing["source"] == "manual" and source != "manual":
                return
        conn.execute(
            """
            INSERT INTO transaction_tags (
                transaction_id, tag_id, source, rule_id, confidence, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                tag_id = excluded.tag_id,
                source = excluded.source,
                rule_id = excluded.rule_id,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (transaction_id, tag_id, source, rule_id, confidence, now),
        )


def clear_transaction_tag(transaction_id: str) -> None:
    with connection() as conn:
        conn.execute(
            "DELETE FROM transaction_tags WHERE transaction_id = ?",
            (transaction_id,),
        )


def fetch_transactions_for_tagging(
    transaction_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Return rows used for rule evaluation (id, fields rules can match on)."""
    with connection() as conn:
        if transaction_ids is None:
            rows = conn.execute(
                """
                SELECT t.transaction_id, t.account_id, t.amount, t.name,
                       t.merchant_name, t.primary_category, t.detailed_category,
                       t.payment_channel, t.iso_currency_code,
                       tt.source AS tag_source
                FROM transactions t
                LEFT JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
                """
            ).fetchall()
        elif not transaction_ids:
            return []
        else:
            placeholders = ",".join("?" for _ in transaction_ids)
            rows = conn.execute(
                f"""
                SELECT t.transaction_id, t.account_id, t.amount, t.name,
                       t.merchant_name, t.primary_category, t.detailed_category,
                       t.payment_channel, t.iso_currency_code,
                       tt.source AS tag_source
                FROM transactions t
                LEFT JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
                WHERE t.transaction_id IN ({placeholders})
                """,
                transaction_ids,
            ).fetchall()
    return [dict(r) for r in rows]


def tag_summary_counts() -> dict[str, int]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM transactions) AS total,
                (SELECT COUNT(*) FROM transaction_tags) AS tagged,
                (SELECT COUNT(*) FROM transaction_tags WHERE source = 'manual') AS manual
            """
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "tagged": int(row["tagged"] or 0),
        "manual": int(row["manual"] or 0),
        "untagged": int((row["total"] or 0) - (row["tagged"] or 0)),
    }


def list_goals(active_only: bool = False) -> list[dict[str, Any]]:
    clauses = []
    if active_only:
        clauses.append("g.active = 1")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT g.*, t.key AS tag_key, t.label AS tag_label, t.color AS tag_color
            FROM goals g
            LEFT JOIN tags t ON t.id = g.tag_id
            {where}
            ORDER BY g.active DESC, g.created_at DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = bool(d["active"])
        d["rollover"] = bool(d["rollover"])
        out.append(d)
    return out


def get_goal(goal_id: int) -> Optional[dict[str, Any]]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT g.*, t.key AS tag_key, t.label AS tag_label, t.color AS tag_color
            FROM goals g
            LEFT JOIN tags t ON t.id = g.tag_id
            WHERE g.id = ?
            """,
            (goal_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["active"] = bool(d["active"])
    d["rollover"] = bool(d["rollover"])
    return d


def insert_goal(
    *,
    name: str,
    kind: str,
    tag_id: Optional[int] = None,
    account_id: Optional[str] = None,
    period: str = "month",
    period_start: Optional[str] = None,
    target_amount: Optional[float] = None,
    target_count: Optional[int] = None,
    currency: str = "USD",
    rollover: bool = False,
    active: bool = True,
    notes: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO goals (
                name, kind, tag_id, account_id, period, period_start,
                target_amount, target_count, currency, rollover, active,
                notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                name,
                kind,
                tag_id,
                account_id,
                period,
                period_start,
                target_amount,
                target_count,
                currency,
                1 if rollover else 0,
                1 if active else 0,
                notes,
                now,
            ),
        )
        row = cur.fetchone()
    return int(row["id"])


def update_goal(goal_id: int, **fields: Any) -> None:
    allowed = {
        "name",
        "kind",
        "tag_id",
        "account_id",
        "period",
        "period_start",
        "target_amount",
        "target_count",
        "currency",
        "rollover",
        "active",
        "notes",
    }
    sets: list[str] = []
    params: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("rollover", "active"):
            v = 1 if v else 0
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(goal_id)
    with connection() as conn:
        conn.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", params)


def delete_goal(goal_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def insert_goal_event(
    goal_id: int,
    event_date: str,
    amount: float,
    kind: str = "contribution",
    note: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO goal_events (goal_id, event_date, amount, kind, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (goal_id, event_date, amount, kind, note, now),
        )
        row = cur.fetchone()
    return int(row["id"])


def list_goal_events(
    goal_id: int,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses = ["goal_id = ?"]
    params: list[Any] = [goal_id]
    if since:
        clauses.append("event_date >= ?")
        params.append(since)
    if until:
        clauses.append("event_date <= ?")
        params.append(until)
    where = " WHERE " + " AND ".join(clauses)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, goal_id, event_date, amount, kind, note, created_at
            FROM goal_events
            {where}
            ORDER BY event_date DESC, id DESC
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def sum_tagged_for_period(
    tag_id: int,
    since: str,
    until: str,
    *,
    account_id: Optional[str] = None,
    direction: str = "outflow",
    include_pending: bool = True,
    exclude_transfers: bool = True,
    currency: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregate tagged transactions inside [since, until].

    Off-currency rows (iso_currency_code present and != currency) are excluded from
    the signed/magnitude/pending sums and from row_count/distinct_days, but reported
    via off_currency_count so the UI can surface them.
    """
    clauses = ["tt.tag_id = ?", "t.date >= ?", "t.date <= ?"]
    params: list[Any] = [tag_id, since, until]
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if not include_pending:
        clauses.append("t.pending = 0")
    if exclude_transfers:
        clauses.append(
            "(t.primary_category IS NULL OR t.primary_category NOT LIKE 'TRANSFER_%')"
        )
    where = " WHERE " + " AND ".join(clauses)

    cur_match_expr = (
        "(t.iso_currency_code IS NULL OR t.iso_currency_code = ?)"
        if currency
        else "1"
    )
    cur_off_expr = (
        "(t.iso_currency_code IS NOT NULL AND t.iso_currency_code != ?)"
        if currency
        else "0"
    )

    if direction == "outflow":
        amount_expr = "CASE WHEN t.amount > 0 THEN t.amount ELSE -t.amount END"
        signed_expr = "t.amount"
    elif direction == "inflow":
        amount_expr = "CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END"
        signed_expr = "CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END"
    else:
        amount_expr = "ABS(t.amount)"
        signed_expr = "t.amount"

    extra_params: list[Any] = []
    if currency:
        # Each conditional appears 5 times below (signed, magnitude, pending, count, distinct).
        extra_params = [currency, currency, currency, currency, currency, currency]

    sql = f"""
        SELECT
            COALESCE(SUM(CASE WHEN {cur_match_expr} THEN {signed_expr} ELSE 0 END), 0) AS signed_total,
            COALESCE(SUM(CASE WHEN {cur_match_expr} THEN {amount_expr} ELSE 0 END), 0) AS magnitude_total,
            COALESCE(SUM(CASE WHEN {cur_match_expr} AND t.pending = 1 THEN {amount_expr} ELSE 0 END), 0) AS pending_amount,
            COUNT(CASE WHEN {cur_match_expr} THEN 1 END) AS row_count,
            COUNT(DISTINCT CASE WHEN {cur_match_expr} THEN t.date END) AS distinct_days,
            SUM(CASE WHEN {cur_off_expr} THEN 1 ELSE 0 END) AS off_currency_count
        FROM transactions t
        JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
        {where}
    """

    if currency:
        # Order of placeholders in SELECT (5 cur_match + 1 cur_off), then WHERE params.
        bound = [currency] * 6 + params
    else:
        bound = list(params)

    with connection() as conn:
        row = conn.execute(sql, bound).fetchone()
    return {
        "signed_total": float(row["signed_total"] or 0.0),
        "magnitude_total": float(row["magnitude_total"] or 0.0),
        "pending_amount": float(row["pending_amount"] or 0.0),
        "row_count": int(row["row_count"] or 0),
        "distinct_days": int(row["distinct_days"] or 0),
        "off_currency_count": int(row["off_currency_count"] or 0),
    }


def list_supporting_transactions(
    tag_id: int,
    since: str,
    until: str,
    *,
    account_id: Optional[str] = None,
    limit: int = 50,
    exclude_transfers: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["tt.tag_id = ?", "t.date >= ?", "t.date <= ?"]
    params: list[Any] = [tag_id, since, until]
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if exclude_transfers:
        clauses.append(
            "(t.primary_category IS NULL OR t.primary_category NOT LIKE 'TRANSFER_%')"
        )
    where = " WHERE " + " AND ".join(clauses)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.transaction_id, t.date, t.amount, t.name, t.merchant_name,
                   t.iso_currency_code, t.pending,
                   tt.source AS tag_source
            FROM transactions t
            JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
            {where}
            ORDER BY t.date DESC, t.transaction_id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pending"] = bool(d["pending"])
        out.append(d)
    return out


def daily_tagged_totals(
    tag_id: int,
    since: str,
    until: str,
    *,
    account_id: Optional[str] = None,
    exclude_transfers: bool = True,
) -> list[dict[str, Any]]:
    clauses = ["tt.tag_id = ?", "t.date >= ?", "t.date <= ?"]
    params: list[Any] = [tag_id, since, until]
    if account_id:
        clauses.append("t.account_id = ?")
        params.append(account_id)
    if exclude_transfers:
        clauses.append(
            "(t.primary_category IS NULL OR t.primary_category NOT LIKE 'TRANSFER_%')"
        )
    where = " WHERE " + " AND ".join(clauses)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.date AS d,
                   SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) AS outflow,
                   SUM(CASE WHEN t.amount < 0 THEN -t.amount ELSE 0 END) AS inflow,
                   COUNT(*) AS n
            FROM transactions t
            JOIN transaction_tags tt ON tt.transaction_id = t.transaction_id
            {where}
            GROUP BY t.date
            ORDER BY t.date
            """,
            params,
        ).fetchall()
    return [
        {
            "date": r["d"],
            "outflow": float(r["outflow"] or 0.0),
            "inflow": float(r["inflow"] or 0.0),
            "count": int(r["n"] or 0),
        }
        for r in rows
    ]


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
