"""
Apply category_mappings rules to transactions at sync time.

Rules are evaluated in priority order (ascending); first match wins.
Unmapped transactions fall to L99 (Uncategorized, needs review) per the
dual-coding ADR. Pure functions — no I/O.
"""

from __future__ import annotations

from typing import Any, Optional

FALLBACK_CODE = "L99"


def _matches(mapping: dict[str, Any], tx: dict[str, Any]) -> bool:
    value = (mapping.get("match_value") or "").lower()
    match_type = mapping.get("match_type")
    if match_type == "plaid_detailed":
        return value == (tx.get("detailed_category") or "").lower()
    if match_type == "merchant":
        return value == (tx.get("merchant_name") or "").lower()
    if match_type == "name_substring":
        return bool(value) and value in (tx.get("name") or "").lower()
    return False


def resolve(tx: dict[str, Any], mappings: list[dict[str, Any]]) -> dict[str, Optional[str]]:
    """Return the coding columns for one transaction. mappings must be
    pre-sorted by priority ascending."""
    for m in mappings:
        if _matches(m, tx):
            return {
                "schedule_c_code": m["schedule_c_code"],
                "custom_category_id": m.get("custom_category_id"),
                "gl_account_type": m.get("gl_account_type"),
                "categorized_by": "rule",
            }
    return {
        "schedule_c_code": FALLBACK_CODE,
        "custom_category_id": None,
        "gl_account_type": None,
        "categorized_by": "rule",
    }
