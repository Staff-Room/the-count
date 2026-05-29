"""
Evaluation harness for the Ingestion-stage classification, in TWO stages.

Corrected model (per the user, 2026-05-28):
  * `Source Category` in the oracle is the USER's own manually-assigned category
    (their custom taxonomy; the 1000+ manual labels). It is the GROUND TRUTH for
    Stage 1 — NOT an issuer/Plaid guess.
  * `Sch C Line` + `GL Account` are COWORK's classification of each user category
    to tax categories (from tax documents). They are an AI MAPPING to be validated
    (Stage 2), NOT independent ground truth.

    Stage 1 (classifier under test):  Plaid txn  ->  user custom category
    Stage 2 (Cowork tax mapping):     user category  ->  Schedule C line / GL / disposition

This is test-support code (leading underscore = not collected by pytest). The
real classifier and the user's full labeled file are drop-in: implement
`CategoryClassifier`, and/or point `load_gold_corpus` at the new file in this schema.

See docs/testing/ingestion-classifier-eval.md.
"""

from __future__ import annotations

import collections
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

ORACLE_DIR = Path(__file__).resolve().parent / "fixtures" / "oracle"
DETAIL_CSV = ORACLE_DIR / "detail.csv"

DISPOSITIONS = ("schedule_c", "personal", "transfer", "wash", "review")
_COA_CODE = re.compile(r"^\d{4}\b")  # "6095 Software & Subscriptions"

# Values in the user-category column that mean "no label assigned".
_UNLABELED = {"", "(uncat)", "uncat", "n/a", "none"}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TxnInput:
    """Classifier INPUT: a raw transaction. The user category is NOT a feature."""

    source_txn_id: str
    date: str
    description: str
    amount: float  # Plaid sign: positive == outflow, negative == inflow
    notes: str = ""
    plaid_category: str = ""  # Plaid PFC seed (card-issuer ADR); absent in v1 oracle
    merchant: str = ""


@dataclass(frozen=True)
class CategoryPrediction:
    """Stage-1 OUTPUT: the predicted user category + provenance."""

    user_category: str
    coding_method: str = "rule"  # "rule" | "inferred"
    confidence: Optional[float] = None
    plaid_seed: Optional[str] = None  # the Plaid PFC the prediction was seeded from


@dataclass(frozen=True)
class TaxCoding:
    """Stage-2 mapping (Cowork's output): how a user category maps to tax categories."""

    disposition: str
    schedule_c_line: Optional[str] = None
    gl_account: Optional[str] = None
    business_use_pct: float = 0.0
    needs_review: bool = False


@dataclass(frozen=True)
class GoldRow:
    txn: TxnInput
    user_category: str          # GOLD for Stage 1 (may be unlabeled)
    cowork_tax: TaxCoding       # Cowork's Stage-2 output for this row (to validate)


# --------------------------------------------------------------------------- #
# Gold loader (adapts the oracle detail.csv)
# --------------------------------------------------------------------------- #
def _f(val: str) -> float:
    val = (val or "").strip()
    try:
        return float(val) if val else 0.0
    except ValueError:
        return 0.0


def _disposition(sch_c_line: str) -> str:
    s = (sch_c_line or "").strip().upper()
    if s.startswith("LINE"):
        return "schedule_c"
    if s == "PERSONAL":
        return "personal"
    if s == "TRANSFER":
        return "transfer"
    if s == "WASH":
        return "wash"
    return "review"


def load_gold_corpus(path: Path = DETAIL_CSV) -> list[GoldRow]:
    out: list[GoldRow] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            amount = _f(r.get("Debit", "")) - _f(r.get("Credit", ""))
            disposition = _disposition(r.get("Sch C Line", ""))
            gl = (r.get("GL Account", "") or "").strip() or None
            cowork = TaxCoding(
                disposition=disposition,
                schedule_c_line=((r.get("Sch C Line", "") or "").strip()
                                 if disposition == "schedule_c" else None),
                gl_account=gl if (gl and _COA_CODE.match(gl)) else None,
                business_use_pct=_f(r.get("Biz %", "")),
                needs_review=(disposition == "review"
                              or bool((r.get("Flag", "") or "").strip())),
            )
            out.append(GoldRow(
                txn=TxnInput(
                    source_txn_id=f"gold-{i}",
                    date=(r.get("Date", "") or "").strip(),
                    description=(r.get("Description", "") or "").strip(),
                    amount=amount,
                    notes=(r.get("Notes", "") or "").strip(),
                ),
                user_category=(r.get("Source Category", "") or "").strip(),
                cowork_tax=cowork,
            ))
    return out


def is_labeled(row: GoldRow) -> bool:
    return row.user_category.strip().lower() not in _UNLABELED


# --------------------------------------------------------------------------- #
# Stage 1 — classifier seam + baseline
# --------------------------------------------------------------------------- #
class CategoryClassifier(Protocol):
    name: str

    def classify(self, txn: TxnInput) -> CategoryPrediction:
        ...


# Description-keyword rules -> user category. Deliberately partial: it exists to
# exercise the harness and set the regression floor, not to be a good classifier.
_KEYWORD_RULES: list[tuple[tuple[str, ...], str]] = [
    (("OPENAI", "CHATGPT", "CLAUDE", "ANTHROPIC", "CURSOR", "WINDSURF",
      "ELEVENLABS", "PERPLEXITY"), "AI Service"),
    (("NOTION", "GITHUB", "PADDLE", "N8N", "MICROSOFT", "VERCEL", "LUCID",
      "GODADDY"), "Cloud Services"),
    (("REGUS", "WEWORK", "COWORK"), "Coworking Space"),
    (("STARLINK", "COMCAST", "XFINITY", "AT&T", "VERIZON"), "Utilities"),
    (("LYFT", "UBER", "CLIPPER", "BART", "LIME"), "Public Transit"),
    (("SHELL", "CHEVRON", "EXXON", "ARCO", "76 "), "Gas"),
]


class DescriptionKeywordBaseline:
    name = "baseline-description-keyword"

    def classify(self, txn: TxnInput) -> CategoryPrediction:
        desc = (txn.description or "").upper()
        for keys, category in _KEYWORD_RULES:
            if any(k in desc for k in keys):
                return CategoryPrediction(
                    user_category=category, coding_method="rule",
                    plaid_seed=txn.plaid_category or None,
                )
        return CategoryPrediction(
            user_category="(uncat)", coding_method="rule",
            plaid_seed=txn.plaid_category or None,
        )


def evaluate_stage1(clf: CategoryClassifier, rows: list[GoldRow]) -> dict:
    labeled = [r for r in rows if is_labeled(r)]
    correct = 0
    per_cat_tp: dict[str, int] = collections.Counter()
    per_cat_fp: dict[str, int] = collections.Counter()
    per_cat_fn: dict[str, int] = collections.Counter()
    support: dict[str, int] = collections.Counter()

    for r in labeled:
        gold = r.user_category
        pred = clf.classify(r.txn).user_category
        support[gold] += 1
        if pred == gold:
            correct += 1
            per_cat_tp[gold] += 1
        else:
            per_cat_fp[pred] += 1
            per_cat_fn[gold] += 1

    cats = set(support) | set(per_cat_fp)
    f1s = []
    per_category = {}
    for c in sorted(cats):
        tp, fp, fn = per_cat_tp[c], per_cat_fp[c], per_cat_fn[c]
        p = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * p * rec / (p + rec) if (p + rec) else 0.0
        per_category[c] = {"precision": round(p, 4), "recall": round(rec, 4),
                           "f1": round(f1, 4), "support": support[c]}
        f1s.append(f1)

    return {
        "stage": "1-transaction-to-user-category",
        "classifier": getattr(clf, "name", clf.__class__.__name__),
        "n_total": len(rows),
        "n_labeled": len(labeled),
        "label_coverage": round(len(labeled) / len(rows), 4) if rows else 0.0,
        "n_user_categories": len(support),
        "accuracy": round(correct / len(labeled), 4) if labeled else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "per_category": per_category,
    }


# --------------------------------------------------------------------------- #
# Stage 2 — validate Cowork's (user category -> tax) mapping
# --------------------------------------------------------------------------- #
def build_category_tax_map(rows: list[GoldRow]) -> dict[str, dict]:
    """For each user category, summarize how Cowork mapped it (majority + purity)."""
    by_cat: dict[str, list[GoldRow]] = collections.defaultdict(list)
    for r in rows:
        if is_labeled(r):
            by_cat[r.user_category].append(r)

    out: dict[str, dict] = {}
    for cat, rs in sorted(by_cat.items()):
        dispositions = collections.Counter(r.cowork_tax.disposition for r in rs)
        lines = collections.Counter(
            r.cowork_tax.schedule_c_line for r in rs if r.cowork_tax.schedule_c_line
        )
        top_disp, top_disp_n = dispositions.most_common(1)[0]
        purity = round(top_disp_n / len(rs), 4)
        out[cat] = {
            "n": len(rs),
            "majority_disposition": top_disp,
            "disposition_purity": purity,
            "schedule_c_lines": dict(lines),
            "gl_accounts": sorted({r.cowork_tax.gl_account for r in rs if r.cowork_tax.gl_account}),
            "needs_tax_review": purity < 1.0,  # Cowork was not consistent -> validate
        }
    return out


def validate_stage2(rows: list[GoldRow]) -> dict:
    cat_map = build_category_tax_map(rows)

    # Roll-up consistency: a GL account must map to exactly one Schedule C line.
    gl_to_lines: dict[str, set] = collections.defaultdict(set)
    for r in rows:
        if r.cowork_tax.gl_account and r.cowork_tax.schedule_c_line:
            gl_to_lines[r.cowork_tax.gl_account].add(r.cowork_tax.schedule_c_line)
    rollup_violations = {gl: sorted(lines) for gl, lines in gl_to_lines.items()
                         if len(lines) > 1}

    ambiguous = {c: m for c, m in cat_map.items() if m["needs_tax_review"]}
    return {
        "stage": "2-user-category-to-tax-mapping",
        "n_user_categories": len(cat_map),
        "rollup_violations": rollup_violations,
        "ambiguous_categories": sorted(ambiguous),
        "category_map": cat_map,
    }
