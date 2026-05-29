"""
Group B (core) — Ingestion classification, in two stages.

  Stage 1  Plaid txn  ->  user custom category   (the classifier under test;
           gold = the user's manual labels, the `Source Category` column).
  Stage 2  user category -> Schedule C line / GL  (Cowork's tax mapping; validated
           for structure, NOT assumed correct — true correctness needs tax review).

This is CLASSIFIER EVALUATION, not deterministic unit tests:
  * HARD GATES (zero tolerance) — encode the ADRs; must always pass.
  * QUALITY FLOORS (regression guard) — met by the baseline now; ratchet up when
    the real classifier + the user's full labeled file land.

Swap-in points (see docs/testing/ingestion-classifier-eval.md):
  * real classifier -> set CLASSIFIER to the production implementation.
  * real labels      -> point load_gold_corpus at the user's labeled file (same schema).
"""

from __future__ import annotations

import json

import pytest

from _classifier_eval import (
    DescriptionKeywordBaseline,
    build_category_tax_map,
    evaluate_stage1,
    is_labeled,
    load_gold_corpus,
    validate_stage2,
)

CLASSIFIER = DescriptionKeywordBaseline()

# Regression-guard bounds (baseline reality; ratchet toward the targets noted).
MIN_STAGE1_ACCURACY = 0.05   # floor; raise toward 1.0 as the real classifier lands
MIN_LABEL_COVERAGE = 0.30    # fraction of rows carrying a user label


@pytest.fixture(scope="module")
def corpus():
    c = load_gold_corpus()
    assert c, "gold corpus is empty"
    return c


@pytest.fixture(scope="module")
def stage1(corpus):
    return evaluate_stage1(CLASSIFIER, corpus)


@pytest.fixture(scope="module")
def stage2(corpus):
    return validate_stage2(corpus)


# ----------------------------------------------------------------------------- #
# Stage 1 — HARD GATES
# ----------------------------------------------------------------------------- #
def test_gate_stage1_predictions_well_formed(corpus):
    for r in corpus:
        p = CLASSIFIER.classify(r.txn)
        assert isinstance(p.user_category, str) and p.user_category
        assert p.coding_method in ("rule", "inferred")


def test_gate_stage1_deterministic(corpus):
    """INV-IDEMPOTENT: same transaction -> same predicted category."""
    for r in corpus[:200]:
        assert CLASSIFIER.classify(r.txn) == CLASSIFIER.classify(r.txn)


def test_gate_stage1_user_category_is_label_not_feature(corpus):
    """The user category must never be fed to the classifier as input.

    TxnInput has no user-category field; this guards against regressions that
    would leak the Stage-1 label into the features.
    """
    from _classifier_eval import TxnInput

    assert not hasattr(TxnInput, "user_category")
    assert "user_category" not in TxnInput.__dataclass_fields__


# ----------------------------------------------------------------------------- #
# Stage 1 — QUALITY FLOORS
# ----------------------------------------------------------------------------- #
def test_stage1_label_coverage(stage1):
    assert stage1["label_coverage"] >= MIN_LABEL_COVERAGE, stage1


def test_stage1_accuracy_floor(stage1):
    assert stage1["accuracy"] >= MIN_STAGE1_ACCURACY, stage1


# ----------------------------------------------------------------------------- #
# Stage 2 — validate Cowork's tax mapping (structure, not correctness)
# ----------------------------------------------------------------------------- #
def test_gate_stage2_gl_rolls_up_to_single_schedule_c_line(stage2):
    """INV-SUBCAT: each GL account maps to exactly one Schedule C line in the data."""
    assert stage2["rollup_violations"] == {}, stage2["rollup_violations"]


def test_gate_stage2_mapping_is_total(corpus, stage2):
    """Every labeled user category has a Cowork tax mapping (no silent gaps)."""
    labeled_cats = {r.user_category for r in corpus if is_labeled(r)}
    assert set(stage2["category_map"]) == labeled_cats


def test_stage2_ambiguous_categories_are_surfaced(stage2):
    """INV-FLAG: categories Cowork mapped inconsistently are surfaced for tax review,
    not silently bucketed. (This asserts they are *captured*, not that none exist.)"""
    cat_map = stage2["category_map"]
    for cat in stage2["ambiguous_categories"]:
        assert cat_map[cat]["needs_tax_review"] is True
        assert cat_map[cat]["disposition_purity"] < 1.0


# ----------------------------------------------------------------------------- #
# Report artifact
# ----------------------------------------------------------------------------- #
def test_emit_report(stage1, stage2, tmp_path):
    report = {
        "stage1": stage1,
        "stage2": {k: v for k, v in stage2.items() if k != "category_map"},
        "stage2_category_map": stage2["category_map"],
    }
    out = tmp_path / "classifier_eval_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nStage 1:", json.dumps(stage1, indent=2, ensure_ascii=False))
    print("\nStage 2 (summary):", json.dumps(
        {k: v for k, v in stage2.items() if k != "category_map"},
        indent=2, ensure_ascii=False))
    assert out.exists()
