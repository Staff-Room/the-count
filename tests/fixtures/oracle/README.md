# v1 Income-Statement Conformance Oracle

This fixture is the **conformance oracle** for the inner-loop (correctness) tests. v2's
computed Schedule C income statement **must reconcile to the numbers here**. Any divergence
is either a **v2 defect** or a **known v1 misclassification** (see `flags_for_rozella.csv`)
to be logged.

## Provenance

- **Source:** Google Sheet `ZW_Consulting_TY2025_Schedule_C_Income_Statement_V1`
  (exported by Cowork; the `.xlsx` in this folder is the immutable source-of-record).
- **Entity:** ZW Consulting LLC (TX) — sole proprietorship / single-member LLC, **cash basis**.
- **Tax year:** 2025.
- **Captured:** 2026-05-28.
- **Integrity:** see `SOURCE_SHA256`
  (`c5ed969fd1a01e7e9cfe10698dc3e5ff7362b34991cda60acbd9d34e65d28887`).

> **Do not hand-edit the derived files.** To refresh, replace the `.xlsx` and run
> `python scripts/build_oracle_fixture.py`, which regenerates the CSVs + JSON deterministically.

## Files

| File | What it is |
|---|---|
| `ZW_Consulting_TY2025_Schedule_C_Income_Statement_V1.xlsx` | Immutable source workbook. |
| `income_statement_v1.json` | **Primary oracle.** Parsed Schedule C P&L: line items + totals. |
| `schedule_c_pl.csv` | The Schedule C P&L tab, verbatim. |
| `detail.csv` | 936 hand-coded transactions (the v1 ledger): Date, Description, Source Category, Debit, Credit, Sch C Line, GL Account, Biz %, Deductible $, Flag, Rationale. |
| `flags_for_rozella.csv` | **Known v1 open items / misclassifications.** Divergences traceable to these are *logged*, not v2 defects. |
| `methodology_and_sources.csv` | Coding rules + IRS citations that produced the v1 numbers. |
| `SOURCE_SHA256` | Integrity pin for the workbook. |

## Headline numbers (reconcile to these)

| Schedule C line | Amount (USD) |
|---|---|
| Line 1 — Gross receipts | 4,090.52 |
| Line 7 — Gross income | 4,090.52 |
| Line 28 — Total expenses | 15,700.6375 |
| **Line 31 — Net profit / (loss)** | **−11,610.1175** |

Internal checks that hold in this fixture (and that v2 must also satisfy):
`sum(expense line items) == Line 28` and `gross_income − total_expenses == net_profit`.

## How to use as an oracle

- Load `income_statement_v1.json` and compare v2's computed Schedule C lines against
  `line_items[].amount` and `totals` (use a small tolerance for the fractional-cent values,
  which come from business-use-% allocations in the v1 detail).
- When a v2 number disagrees, classify the cause: a row in `flags_for_rozella.csv`
  (→ known v1 issue, log it) vs. anything else (→ v2 defect).

## Caveats that shape the tests (flagged, not assumed)

- **Single-leg coding.** `detail.csv` codes each transaction as one Debit *or* Credit against
  one GL account — it is **not** a full two-leg journal entry. v2's balanced journal-entry
  events (both legs) must still roll up to the same Schedule C lines.
- **Fractional business-use %.** v1 applies a `Biz %` to get `Deductible $`
  (e.g. Line 9 = 117.7275). The proposed journal-entry event schema models
  `personal_or_business` as a **binary** flag, which cannot represent partial allocation.
  This gap is flagged for decision (see the journal-entry event ADR open questions).
- **Items excluded from P&L.** Transfers, credit-card payments, family gifts, refunds, and
  car/truck actuals (deferred to the mileage method) are intentionally excluded — consistent
  with the inner-loop invariants (transfers net to zero; CC payments are transfers).
