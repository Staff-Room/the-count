"""
Auto-tagger: maps transactions to lifestyle tags via user-editable rules.

Match fields: merchant_name | name | primary_category | detailed_category |
              account_id | payment_channel
Match ops:    equals | contains | regex | in_list | startswith
"""

from __future__ import annotations

import re
from typing import Any, Optional

import db


VALID_FIELDS = {
    "merchant_name",
    "name",
    "primary_category",
    "detailed_category",
    "account_id",
    "payment_channel",
}

VALID_OPS = {"equals", "contains", "regex", "in_list", "startswith"}


# Starter tag set + rules. Iterating order = priority order.
DEFAULT_TAGS: list[dict[str, Any]] = [
    {"key": "income", "label": "Income", "kind": "income", "color": "#3dd6a5"},
    {"key": "savings_transfer", "label": "Savings transfer", "kind": "save", "color": "#7fb1d3"},
    {"key": "rent_mortgage", "label": "Rent / mortgage", "kind": "spend", "color": "#bb8db0"},
    {"key": "groceries", "label": "Groceries", "kind": "spend", "color": "#9ec05d"},
    {"key": "dining_out", "label": "Dining out", "kind": "spend", "color": "#f0a955"},
    {"key": "coffee", "label": "Coffee", "kind": "spend", "color": "#c08b6e"},
    {"key": "bars_alcohol", "label": "Bars & alcohol", "kind": "spend", "color": "#d36a8a"},
    {"key": "transport", "label": "Transport", "kind": "spend", "color": "#62b0c8"},
    {"key": "travel", "label": "Travel", "kind": "spend", "color": "#7fa1f0"},
    {"key": "subscriptions", "label": "Subscriptions", "kind": "spend", "color": "#bdb14e"},
    {"key": "entertainment", "label": "Entertainment", "kind": "spend", "color": "#a877d9"},
    {"key": "shopping", "label": "Shopping", "kind": "spend", "color": "#e07b91"},
    {"key": "fitness", "label": "Fitness & wellness", "kind": "spend", "color": "#67c896"},
    {"key": "health", "label": "Healthcare", "kind": "spend", "color": "#56b890"},
    {"key": "utilities", "label": "Utilities & bills", "kind": "spend", "color": "#7f8a9d"},
    {"key": "fees_interest", "label": "Fees & interest", "kind": "spend", "color": "#a06262"},
    {"key": "transfer_internal", "label": "Internal transfer", "kind": "save", "color": "#5d6b7d"},
]


# (tag_key, priority, field, op, value)
DEFAULT_RULES: list[tuple[str, int, str, str, str]] = [
    # Specific merchant heuristics first.
    ("coffee", 10, "merchant_name", "regex", r"(?i)starbucks|peet'?s|blue bottle|philz|dutch bros|caribou coffee|tim hortons"),
    ("coffee", 12, "name", "regex", r"(?i)starbucks|peet'?s|blue bottle|philz|dutch bros|caribou coffee|tim hortons"),
    ("dining_out", 14, "merchant_name", "regex", r"(?i)doordash|ubereats|uber eats|grubhub|seamless|caviar|postmates|chipotle|sweetgreen|chick[- ]?fil[- ]?a|panera|shake shack"),
    ("dining_out", 16, "name", "regex", r"(?i)doordash|ubereats|uber eats|grubhub|seamless|caviar|postmates"),
    ("bars_alcohol", 18, "merchant_name", "regex", r"(?i)brewing|brewery|tap house|tavern|pub|bar [a-z]|liquor|wine shop"),
    ("transport", 20, "merchant_name", "regex", r"(?i)^uber$|^lyft$|uber technologies|lyft inc|metro|amtrak|caltrain|bart|chevron|shell oil|exxon|76 [- ]?gas|costco gas"),
    ("transport", 22, "name", "regex", r"(?i)uber|lyft|amtrak|metro|caltrain|chevron|shell oil|exxon"),
    ("travel", 24, "merchant_name", "regex", r"(?i)airbnb|vrbo|delta air|united air|american air|southwest|jetblue|alaska air|expedia|booking\.com|hotels\.com|marriott|hilton|hyatt"),
    ("subscriptions", 26, "merchant_name", "regex", r"(?i)netflix|spotify|hulu|disney\+|disney plus|hbo max|max\.com|youtube premium|apple\.com/bill|apple services|nytimes|new york times|wsj|notion|github|patreon|substack|amazon prime|adobe|dropbox|icloud"),
    ("entertainment", 28, "merchant_name", "regex", r"(?i)cinemark|amc theatres|regal cinemas|ticketmaster|axs\.com|stubhub|live nation|steam|playstation|nintendo"),
    ("fitness", 30, "merchant_name", "regex", r"(?i)peloton|equinox|24 hour fitness|planet fitness|crossfit|barry'?s|soulcycle|orangetheory|gym|yoga"),
    ("groceries", 32, "merchant_name", "regex", r"(?i)trader joe'?s|whole foods|safeway|kroger|publix|wegmans|aldi|h-?e-?b|costco wholesale|sam'?s club|sprouts|food lion"),
    ("shopping", 34, "merchant_name", "regex", r"(?i)amazon\.com|amzn\b|target|walmart|best buy|nordstrom|macy'?s|ikea|home depot|lowe'?s|etsy"),
    ("utilities", 36, "merchant_name", "regex", r"(?i)comcast|xfinity|spectrum|verizon|at&t|t-mobile|tmobile|pg&e|con ?ed|consolidated edison|water dept|gas company|sewer"),

    # Plaid Personal Finance Category baseline.
    ("income", 50, "primary_category", "equals", "INCOME"),
    ("rent_mortgage", 52, "detailed_category", "equals", "RENT_AND_UTILITIES_RENT"),
    ("rent_mortgage", 54, "detailed_category", "equals", "LOAN_PAYMENTS_MORTGAGE_PAYMENT"),
    ("utilities", 56, "primary_category", "equals", "RENT_AND_UTILITIES"),
    ("groceries", 58, "detailed_category", "equals", "FOOD_AND_DRINK_GROCERIES"),
    ("dining_out", 60, "detailed_category", "equals", "FOOD_AND_DRINK_RESTAURANT"),
    ("dining_out", 62, "detailed_category", "equals", "FOOD_AND_DRINK_FAST_FOOD"),
    ("coffee", 64, "detailed_category", "equals", "FOOD_AND_DRINK_COFFEE"),
    ("bars_alcohol", 66, "detailed_category", "equals", "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR"),
    ("transport", 68, "primary_category", "equals", "TRANSPORTATION"),
    ("travel", 70, "primary_category", "equals", "TRAVEL"),
    ("entertainment", 72, "primary_category", "equals", "ENTERTAINMENT"),
    ("shopping", 74, "primary_category", "equals", "GENERAL_MERCHANDISE"),
    ("health", 76, "primary_category", "equals", "MEDICAL"),
    ("fees_interest", 78, "primary_category", "equals", "BANK_FEES"),
    ("fees_interest", 80, "primary_category", "equals", "LOAN_PAYMENTS"),
    ("transfer_internal", 90, "primary_category", "startswith", "TRANSFER_"),

    # Legacy category fallback (older Plaid responses).
    ("dining_out", 100, "primary_category", "equals", "FOOD_AND_DRINK"),
    ("transport", 102, "primary_category", "equals", "TRAVEL"),
    ("shopping", 104, "primary_category", "equals", "SHOPS"),
]


_REGEX_CACHE: dict[tuple[str, str], Optional[re.Pattern[str]]] = {}


def _get_regex(value: str, op: str) -> Optional[re.Pattern[str]]:
    key = (op, value)
    if key in _REGEX_CACHE:
        return _REGEX_CACHE[key]
    try:
        pat = re.compile(value)
    except re.error:
        pat = None
    _REGEX_CACHE[key] = pat
    return pat


def seed_default_tags_and_rules(*, force: bool = False) -> dict[str, int]:
    """Idempotent: ensure the default tags exist; insert default rules only when absent."""
    inserted_tags = 0
    inserted_rules = 0

    for t in DEFAULT_TAGS:
        existing = db.get_tag_by_key(t["key"])
        if existing is None:
            db.insert_tag(t["key"], t["label"], t["kind"], t.get("color"))
            inserted_tags += 1

    if not force:
        existing_rule_count = sum(len(db.list_tag_rules(t["id"])) for t in db.list_tags())
        if existing_rule_count > 0:
            return {"tags_inserted": inserted_tags, "rules_inserted": 0}

    tag_id_by_key = {t["key"]: t["id"] for t in db.list_tags()}
    for tag_key, priority, field, op, value in DEFAULT_RULES:
        tag_id = tag_id_by_key.get(tag_key)
        if not tag_id:
            continue
        db.insert_tag_rule(
            tag_id=tag_id,
            match_field=field,
            match_op=op,
            match_value=value,
            priority=priority,
            enabled=True,
        )
        inserted_rules += 1

    return {"tags_inserted": inserted_tags, "rules_inserted": inserted_rules}


def _value_for_field(row: dict[str, Any], field: str) -> Optional[str]:
    v = row.get(field)
    if v is None:
        return None
    return str(v)


def _rule_matches(rule: dict[str, Any], row: dict[str, Any]) -> bool:
    field = rule["match_field"]
    op = rule["match_op"]
    target = rule.get("match_value") or ""
    if field not in VALID_FIELDS or op not in VALID_OPS:
        return False

    val = _value_for_field(row, field)
    min_a = rule.get("min_amount")
    max_a = rule.get("max_amount")
    if min_a is not None or max_a is not None:
        amt = abs(float(row.get("amount") or 0.0))
        if min_a is not None and amt < float(min_a):
            return False
        if max_a is not None and amt > float(max_a):
            return False

    if val is None:
        return False

    if op == "equals":
        return val == target
    if op == "startswith":
        return val.startswith(target)
    if op == "contains":
        return target.lower() in val.lower()
    if op == "in_list":
        items = [s.strip() for s in target.split(",") if s.strip()]
        return val in items
    if op == "regex":
        pat = _get_regex(target, op)
        if pat is None:
            return False
        return pat.search(val) is not None
    return False


def apply_rules(transaction_ids: Optional[list[str]] = None) -> dict[str, int]:
    """Evaluate rules in priority order; first match wins. Manual tags are preserved."""
    rules_all = [r for r in db.list_tag_rules() if r.get("enabled", True)]
    if not rules_all:
        return {"evaluated": 0, "tagged": 0, "skipped_manual": 0, "unmatched": 0}

    # list_tag_rules already returns rows ordered by priority, id.
    rules = rules_all

    rows = db.fetch_transactions_for_tagging(transaction_ids)
    tagged = 0
    skipped_manual = 0
    unmatched = 0

    for row in rows:
        if row.get("tag_source") == "manual":
            skipped_manual += 1
            continue
        match = None
        for rule in rules:
            try:
                if _rule_matches(rule, row):
                    match = rule
                    break
            except Exception:
                continue
        if match is None:
            unmatched += 1
            continue
        db.upsert_transaction_tag(
            transaction_id=row["transaction_id"],
            tag_id=int(match["tag_id"]),
            source="rule",
            rule_id=int(match["id"]),
            confidence=None,
        )
        tagged += 1

    return {
        "evaluated": len(rows),
        "tagged": tagged,
        "skipped_manual": skipped_manual,
        "unmatched": unmatched,
    }


def set_manual_tag(transaction_id: str, tag_id: int) -> None:
    db.upsert_transaction_tag(
        transaction_id=transaction_id,
        tag_id=tag_id,
        source="manual",
        rule_id=None,
        confidence=1.0,
        overwrite_manual=True,
    )


def clear_tag(transaction_id: str) -> None:
    db.clear_transaction_tag(transaction_id)
