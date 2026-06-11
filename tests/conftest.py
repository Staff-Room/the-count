"""
Shared test setup for The Count.

Establishes an isolated SQLite database (via THE_COUNT_DB_PATH) and puts the
flat-import backend modules (`db`, `plaid_sync`) on the path BEFORE they are
imported, plus loaders for the v1 conformance-oracle fixtures and a scripted
fake Plaid client shared across test modules.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "src" / "backend"
ORACLE_DIR = REPO_ROOT / "tests" / "fixtures" / "oracle"

# Backend modules use flat imports (`import db`), so the dir must be importable.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Isolate the DB and neutralize external side effects BEFORE importing backend.
os.environ["THE_COUNT_DB_PATH"] = str(
    Path(tempfile.gettempdir()) / f"thecount-test-{uuid.uuid4().hex}.db"
)
os.environ.setdefault("PLAID_CLIENT_ID", "test-client-id")
os.environ.setdefault("PLAID_SECRET", "test-secret")
os.environ["PLAID_ENV"] = "sandbox"
os.environ["BACKEND_STORE"] = "sqlite"  # tests always run against isolated SQLite

import plaid  # noqa: E402
import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Scripted fake Plaid client (shared by sources-stage and hardening tests)
# ---------------------------------------------------------------------------
class FakeClient:
    """transactions_sync plays back a script: each entry is a response page
    or an exception to raise. Cursors of each request are recorded."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def transactions_sync(self, req):
        self.calls.append(getattr(req, "cursor", None))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def fake_page(next_cursor, has_more=False):
    return SimpleNamespace(
        added=[], modified=[], removed=[], has_more=has_more, next_cursor=next_cursor
    )


def plaid_error(error_code: str) -> plaid.ApiException:
    e = plaid.ApiException(status=400, reason="Bad Request")
    e.body = json.dumps({"error_code": error_code})
    return e


@pytest.fixture()
def fresh_db():
    """A clean backend `db` module — tables truncated before each test."""
    import db

    db.init_db()
    with db.connection() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM sync_cursors")
        conn.execute("DELETE FROM items")
    return db


@pytest.fixture(scope="session")
def sync_item_mod():
    """The api/sync/item.py Vercel function, loaded via importlib (the
    nested path prevents a normal import). Safe: BACKEND_STORE=sqlite and
    the temp DB are set above, before any backend import."""
    spec = importlib.util.spec_from_file_location(
        "api_sync_item", REPO_ROOT / "api" / "sync" / "item.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def oracle_income_statement():
    return json.loads((ORACLE_DIR / "income_statement_v1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def oracle_detail_rows():
    with (ORACLE_DIR / "detail.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
