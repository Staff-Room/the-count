# Testing The Count — philosophy & invariants

Tests are derived from the **declared model** (the ADRs, mirrored read-only in
[`../adr/`](../adr/README.md)). If code diverges from an ADR, the test encodes the ADR and the
code is the defect — not the other way around.

> **Model-change notice (2026-05-28).** The Count reversed from **double-entry journal
> entries** to **single-entry coded listing**. The original task brief's inner-loop rule —
> *"every entry balances (debits = credits)"* — is **retired**. See
> [single-entry-coded-listing.md](../adr/single-entry-coded-listing.md). The invariants below
> are rebuilt for single-entry.

## Two test layers

| Layer | Question it answers | Passes when |
|---|---|---|
| **Inner loop (correctness)** | Are the numbers / codings right by first principles? | Stage output satisfies the accounting invariants below. |
| **Outer loop (journey)** | Can the user actually get there? | The end-to-end (or bounded) flow completes **and** the resulting data satisfies the inner-loop invariants — not merely that a screen renders. |

Every test is tagged `inner-loop` or `outer-loop` and grouped by **pipeline stage**
(`Sources → Ingestion → Storage → Processing → Analysis/QA`, per the five-stage ADR). A test
depends only on its stage's **defined input/output seam**, so it stays solvable inside a single
~200k-token context window and never reaches across the whole pipeline.

## Inner-loop invariants under single-entry (first principles)

Derived from: single-entry coded listing, cash-basis, recognize-on-cash-movement,
dual-coding, custom-subcategory, personal-vs-business/client-assignment, and validation-gate
ADRs.

### Structural accuracy (replaces `debits = credits`)
- **INV-RECONCILE** — For each account and period, the sum of coded-listing amounts ties back
  to the **source bank/card statement net movement**. This reconciliation is the single-entry
  substitute for self-balancing (validation-gate ADR, amended).
- **INV-COMPLETE** — Every source transaction is represented in the listing **exactly once**
  (no drops, no duplicates). One row per source transaction, keyed by source txn id.
- **INV-IDEMPOTENT** — Re-running ingestion over the same source feed yields the same listing.

### Recognition & timing
- **INV-CASHDATE** — A row's `date` is the **cash-movement date**. No row is created whose
  contra is anything other than cash; no receivable/payable is ever produced
  (recognize-on-cash-movement ADR).

### Classification (the "coded transactions" correctness)
- **INV-DUALCODE** — Every row carries a **GL account type** (asset/liability/equity/revenue/
  expense). Every revenue/expense row also carries a **Schedule C line** (dual-coding ADR).
- **INV-SIGN** — Plaid sign convention (positive = outflow, negative = inflow) is translated
  to the correct direction: outflow → expense or asset-decrease; inflow → revenue or
  asset-increase.
- **INV-TRANSFER** — Transfers between the user's own accounts are coded as **transfers**
  (non-P&L): they touch **no** revenue/expense account, and across the user's own accounts they
  **net to zero**.
- **INV-CCPAY** — Credit-card **payments** are transfers (bank asset ↔ card liability), **not**
  expense. (The card **purchases** are the expenses.)
- **INV-DRAW** — Owner draws hit **equity**, not expense; owner contributions hit equity, not
  revenue.
- **INV-LOAN** — Loan **principal** movement is a **liability** change, not expense (only
  interest is expense).
- **INV-PERSONAL** — `personal-vs-business` is set; **personal** rows are excluded from the
  business Schedule C totals.
- **INV-SUBCAT** — A custom subcategory **rolls up** to its Schedule C parent line
  (custom-subcategory ADR).

### Provenance & control
- **INV-PROVENANCE** — Every row records how it was coded: `coding_method ∈ {rule, inferred}`
  (rule id when rule; confidence when inferred), plus the issuer/Plaid **seed** category
  (card-issuer ADR — a starting point, not authoritative), and a `review_status ∈
  {unreviewed, confirmed, corrected}`.
- **INV-FLAG** — When coding is genuinely ambiguous or data is missing, the row is **flagged
  for review**, never silently bucketed (validation-gate / flag-don't-assume ADR).

## The conformance oracle

[`tests/fixtures/oracle/`](../../tests/fixtures/oracle/) holds the v1 Schedule C income
statement and its hand-coded **Detail** listing.

- For the **Ingestion** stage (this segment), `detail.csv` is the **golden coded listing**:
  each row is a labeled (transaction → coding) example for the classification invariants.
- `flags_for_rozella.csv` is the registry of **known v1 misclassifications**: a v2/v1
  divergence traceable to a row here is **logged**, not treated as a v2 defect.
- `income_statement_v1.json` is the reconciliation target for the **Processing/Analysis**
  stages (downstream of this segment).

## Index

- [plaid-to-coded-transactions.md](plaid-to-coded-transactions.md) — the stage-bounded
  decomposition for the **Plaid accounts → coded transactions** pipeline (Sources, Ingestion,
  Storage), the current focus.
- [ingestion-classifier-eval.md](ingestion-classifier-eval.md) — the two-stage **classifier
  evaluation** harness (transaction → user category → tax mapping) and the **labeled-data
  contract** for the user's manual categorizations.

> **Note on `INV-PROVENANCE` and the "issuer seed".** The seed is the Plaid
> `personal_finance_category` (a live input feature). It is **distinct from** the user's own
> `Source Category`, which is a **ground-truth label**, not a seed. See
> [ingestion-classifier-eval.md](ingestion-classifier-eval.md).
