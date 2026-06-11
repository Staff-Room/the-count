"""
Shared test setup for The Count.

Establishes an isolated SQLite database (via THE_COUNT_DB_PATH) and puts the
flat-import backend modules (`db`, `app`, `plaid_sync`) on the path BEFORE they
are imported, plus loaders for the v1 conformance-oracle fixtures.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

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
os.environ["NOTION_WORKER_AUTO_SYNC"] = "false"  # never shell out to `ntn` in tests

import pytest  # noqa: E402


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


@pytest.fixture()
def app_mod(fresh_db):
    import app as appmod

    return appmod


@pytest.fixture()
def http(app_mod):
    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client()


@pytest.fixture(scope="session")
def oracle_income_statement():
    return json.loads((ORACLE_DIR / "income_statement_v1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def oracle_detail_rows():
    with (ORACLE_DIR / "detail.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
