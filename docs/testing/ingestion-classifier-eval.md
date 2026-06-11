# Ingestion classifier — evaluation harness

The Ingestion classifier pulls Plaid transactions and categorizes them. There are **two
stages**, and they have different oracles:

```
Stage 1 (the classifier under test):   Plaid txn  ──▶  user custom category
Stage 2 (Cowork's tax mapping):         user category  ──▶  Schedule C line / GL / disposition
```

> **What is ground truth (corrected 2026-05-28).**
> - The oracle's **`Source Category`** is the **user's own manually-assigned category** — the
>   custom taxonomy, the 1000+ manual labels. This is the **ground truth for Stage 1**. It is
>   **not** an issuer/Plaid guess and is never a classifier input.
> - The oracle's **`Sch C Line` + `GL Account`** are **Cowork's** classification of each user
>   category to tax categories (from tax documents). They are an **AI mapping to validate**
>   (Stage 2) — **not** independent ground truth. True tax correctness needs human/tax review
>   (the `flags_for_rozella.csv` rows are where Cowork was unsure).

This is **classifier evaluation**, not deterministic unit testing. Tests split into:
- **Hard gates** (zero tolerance) — encode the ADRs / data contract; always pass.
- **Quality floors** (regression guard) — met by the baseline now; ratchet up as the real
  classifier and the full labeled file land.

Harness: [`tests/_classifier_eval.py`](../../tests/_classifier_eval.py);
suite: [`tests/test_ingestion_classifier.py`](../../tests/test_ingestion_classifier.py).

## Stage 1 — transaction → user category

- **Input (`TxnInput`):** `description`, `amount` (Plaid sign: + outflow / − inflow),
  `notes`, `plaid_category` (the Plaid PFC seed — the *real* card-issuer-ADR seed, a feature;
  absent in the v1 oracle), `merchant`. **The user category is the label and is never an input.**
- **Output (`CategoryPrediction`):** `user_category` + provenance (`coding_method`
  rule|inferred, `confidence`, `plaid_seed`).
- **Gold:** the `Source Category` column (868/935 rows labeled in the v1 oracle; 30 categories).
- **Metrics:** label coverage, top-1 accuracy, macro-F1, per-category precision/recall.
  (Accuracy alone is weak — the classes are imbalanced; watch macro-F1 and per-category recall.)
- **Hard gates:** predictions well-formed; deterministic (INV-IDEMPOTENT); the label is
  structurally prevented from leaking into the features.

## Stage 2 — user category → tax (Cowork's mapping, to validate)

`build_category_tax_map()` summarizes, per user category, how Cowork mapped it: majority
disposition (`schedule_c` / `personal` / `transfer` / `wash` / `review`), the Schedule C
line(s) and GL account(s) seen, and a **purity** score. `validate_stage2()` then checks:
- **Roll-up (INV-SUBCAT):** each GL account maps to exactly one Schedule C line.
- **Totality:** every labeled user category has a mapping (no silent gaps).
- **Ambiguity (INV-FLAG):** categories Cowork mapped inconsistently (`purity < 1.0`) are
  surfaced as `needs_tax_review` — flagged, not silently bucketed. (v1 example: `Business
  Expense`, `Utilities`.)

> Stage 2 validates *structure*, not tax correctness. "Is `Coworking Space` → Line 20b
> correct?" is a **tax-review** question (Rozella / tax docs), outside automated scope.

## Data contract — bring your 1000+ labels in this shape

The loader reads the v1 oracle's column names. Your labeled export can either reuse those or
provide the normalized fields below.

| Field | Role | Required | Oracle column |
|---|---|---|---|
| `source_txn_id` | id (idempotency) | recommended | — (synthesized) |
| `date` | feature | yes | `Date` |
| `description` | feature (raw payee text) | **yes** | `Description` |
| `amount` (signed) or `debit`/`credit` | feature | **yes** | `Debit` − `Credit` |
| `notes` | feature | optional | `Notes` |
| `merchant_name` | feature | optional | — |
| `plaid_category` | feature (Plaid PFC seed) | optional | — |
| **`user_category`** | **Stage-1 label (your category)** | **yes** | `Source Category` |
| `schedule_c_line` | Stage-2 (Cowork output) | optional | `Sch C Line` |
| `gl_account` | Stage-2 (Cowork output) | optional | `GL Account` |
| `business_use_pct` | Stage-2 (Cowork output) | optional | `Biz %` |
| `flag` | Stage-2 review marker | optional | `Flag` |

Notes for the export:
- Use the **raw bank-feed description** (e.g. `NOTION LABS, INC.`) — that's the classifier's
  main signal. Add `plaid_category` and `merchant_name` if you have them; they materially help.
- Rows you never categorized can be left blank / `(uncat)`; they count toward coverage but are
  excluded from accuracy.
- If you have the Plaid raw pull alongside your labels, include `plaid_category` — it lets the
  classifier use the issuer seed (card-issuer ADR) and lets us test seed-vs-final divergence.

## Swapping in the real classifier / real labels

- **Real classifier:** implement the `CategoryClassifier` protocol (rules and/or an LLM call
  emitting `coding_method="inferred"` + `confidence`) and set `CLASSIFIER` in the suite.
- **Real labels:** point `load_gold_corpus(path=...)` at your labeled file; the Stage-2
  taxonomy is derived from it automatically. Then ratchet `MIN_STAGE1_ACCURACY` upward.

## Open decisions affecting this classifier (flag, don't assume)

1. **Binary personal-vs-business vs. fractional business-use %.** The personal-vs-business ADR
   pins a **binary** flag, but your labels carry fractional `Biz %` (0.25 / 0.5 / 0.75). The
   schema models `business_use_pct`; this needs an ADR update or an explicit allocation rule.
2. **`plaid_category` availability.** Does your 1000+ export include the Plaid PFC per row? It
   is the strongest seed feature; if available we wire it in.
3. **Category-set drift.** Is your live custom category list identical to the 30 in the v1
   oracle, or larger? The label space (and Stage-2 mapping) is taken from whatever file we load.
