#!/usr/bin/env python3
"""Emit PLAID_ITEMS_JSON for the Notion worker from The Count SQLite store."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "src" / "backend"
sys.path.insert(0, str(_BACKEND))

import db  # noqa: E402


def main() -> None:
    db.init_db()
    rows = db.iter_items_with_tokens()
    out = [
        {
            "item_id": r["item_id"],
            "access_token": r["access_token"],
            **(
                {"institution_name": r["institution_name"]}
                if r.get("institution_name")
                else {}
            ),
        }
        for r in rows
    ]
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
