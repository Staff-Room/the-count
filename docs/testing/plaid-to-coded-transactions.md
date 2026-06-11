# Decomposition — Plaid accounts → coded transactions

**Scope:** the slice of the journey from *linked Plaid accounts* to a *coded transaction
listing*. Stages covered: **Sources → Ingestion → Storage** (this segment **stops at coded
transactions**; the balance sheet / income statement projections belong to the downstream
Processing / Analysis stages and are out of scope here).

Each test below is **stage-bounded**: it depends only on its stage's defined input/output
seam, is self-contained within a single ~200k-token context window, and is tagged
`inner-loop` or `outer-loop`. Invariant codes (`INV-*`) are defined in
[README.md](README.md).

## Seam contracts (the three transforms in this segment)

```
        Plaid API                  raw transaction store              coded transaction listing
   (Link + /transactions/sync)        (db.py: items,                  (single-entry; append-only;
            │                          transactions,                   one row per source txn)
            │   SOURCES                sync_cursors)   INGESTION              STORAGE
            ▼                              ▼               ▼                      ▼
  ┌───────────────────┐  S1   ┌───────────────────┐  S2  ┌───────────────┐  S3  ┌──────────────┐
  │ link/exchange/sync │ ───▶ │ raw Plaid txn row  │ ───▶ │ coded row      │ ───▶ │ append-only  │
  │ (Vercel functions) │      │ (one leg + PFC     │      │ (GL type + Sch │      │ listing +    │
  │                    │      │  category guess)   │      │  C + dims +    │      │ reconcile    │
  │                    │      │                    │      │  provenance)   │      │ gate         │
  └───────────────────┘      └───────────────────┘      └───────────────┘      └──────────────┘
```

- **S1 — Sources seam.** Input: Plaid `transactions_sync` responses (linking is
  website-owned, contract M3). Output: rows in `items`, `transactions`, `sync_cursors`
  (`src/backend/db.py`, `src/backend/plaid_sync.py`; HTTP entry points are the
  Vercel functions `api/cron-sync.py` and `api/sync/item.py`). **Implemented.**
- **S2 — Ingestion seam.** Input: one raw Plaid transaction (a single account movement + an
  issuer/Plaid `personal_finance_category` guess). Output: **one coded listing row**
  (date, amount, vendor, GL account type, Schedule C line + optional subcategory,
  personal-vs-business, client assignment, provenance, review status, source ref).
  **Not yet implemented** — the coding engine is the build target these tests specify.
- **S3 — Storage seam.** Input: coded rows + the source statement totals. Output: an
  **append-only coded transaction listing** (the Storage ledger artifact) plus a
  **reconciliation** result. **Not yet implemented.**

The raw Plaid row shape (the S1→S2 contract) is fixed by `transactions` in `db.py`:
`transaction_id, item_id, account_id, amount, iso_currency_code, date, authorized_date,
name, merchant_name, pending, primary_category, detailed_category, payment_channel, raw_json`.

---

## Group A — Sources stage  (seam S1; runnable against current code)

Input = mocked Plaid responses. Output = the raw store. No coding logic involved. These pin
the contract that Ingestion consumes.

| ID | Loop | Assertion | Depends on |
|---|---|---|---|
| **A1** | — | **Retired 2026-06-11.** Link-token creation is website-owned (contract M3); no backend surface remains. | — |
| **A2** | — | **Retired 2026-06-11.** Token exchange / item persistence is website-owned (contract M3). | — |
| **A3** | outer | Sync paginates `transactions_sync` until `has_more=false` and persists the cursor (per page). | `plaid_sync.sync_item`, `db.set/get_cursor` |
| **A4** | **inner** | `apply_sync_response` applies `added`/`modified`/`removed` → upsert/delete; replaying the same page is **idempotent** (one row per `transaction_id`). → **INV-COMPLETE, INV-IDEMPOTENT** | `plaid_sync.apply_sync_response`, `db` |
| **A5** | **inner** | Raw fidelity: amount **sign**, `iso_currency_code`, `date`/`authorized_date`, PFC `primary`/`detailed`, `payment_channel` persist losslessly; `raw_json` retained for provenance. → **INV-SIGN (raw), INV-PROVENANCE (seed)** | `plaid_sync._transaction_to_row` |
| **A6** | outer | A full resync resets cursors (`db.reset_all_sync_cursors`) and replays history from scratch. | `plaid_sync.sync_item`, `scripts/sync_plaid_now.py --full` |

Hardening extensions (same seam): per-page cursor durability, mutation-error
restart, and fail-closed auth + handler logic for `api/sync/item.py` — see
[`tests/test_sync_hardening.py`](../../tests/test_sync_hardening.py).

**Fixture:** a scripted `FakeClient` (`tests/conftest.py`) playing back
`fake_page(...)` responses / exceptions + a temp SQLite DB via `THE_COUNT_DB_PATH`.
A3/A6 drive `plaid_sync.sync_item` with the fake client; A4–A5 call `plaid_sync`
directly with synthetic `Transaction` objects (no network). See
[`tests/test_sources_stage.py`](../../tests/test_sources_stage.py).

---

## Group B — Ingestion stage  (seam S2; the heart; spec — coding engine to be built)

Input = one raw Plaid transaction. Output = one coded listing row. **Golden data:** the
oracle's `detail.csv` — each row is a labeled (transaction → coding) example. Each test picks
representative oracle rows as cases.

> **Two-stage classification (see [ingestion-classifier-eval.md](ingestion-classifier-eval.md)).**
> In the oracle, `Source Category` is the **user's own manual category** (Stage-1 *label*,
> ground truth), while `Sch C Line` / `GL Account` are **Cowork's** tax mapping (Stage-2,
> AI output to validate). The Plaid `personal_finance_category` is the separate **issuer
> seed** (a live feature, absent from the v1 oracle) referenced by the card-issuer ADR — do
> not confuse it with `Source Category`.

| ID | Loop | Assertion (invariant) | Oracle example(s) |
|---|---|---|---|
| **B1** | inner | Exactly one coded row per posted source txn; `source_txn_id` preserved as key. **INV-COMPLETE** | any Detail row |
| **B2** | inner | Row `date` = cash-movement (posted) date; no A/R or A/P row produced. **INV-CASHDATE** | all rows dated on posting |
| **B3** | inner | Sign/direction: Plaid `+` (outflow) → expense/asset-out; `−` (inflow) → revenue/asset-in. **INV-SIGN** | Deposit `VENMO CASHOUT` (inflow → revenue); `NOTION LABS` (outflow → expense) |
| **B4** | inner | Dual-coding: each P&L row has GL type + a Schedule C line; the Plaid PFC seed is stored as `source_category_guess` but coding may differ. **INV-DUALCODE, INV-PROVENANCE** | `NOTION LABS` → user cat "Cloud Services" → Line 18 / GL 6095 |
| **B5** | inner | Internal transfer between own accounts → coded transfer (non-P&L), not revenue/expense. **INV-TRANSFER** | Methodology: "internal transfers … excluded" |
| **B6** | inner | Credit-card **payment** → transfer (bank asset ↔ card liability), excluded from expense. **INV-CCPAY** | Methodology: "CC payments … excluded from P&L" |
| **B7** | inner | Owner **draw** → equity, not expense; owner **contribution** → equity, not revenue. **INV-DRAW** | Methodology: "family gifts (D. Walker / Rent Assistance) … excluded" |
| **B8** | inner | Loan **principal** → liability, not expense. **INV-LOAN** | (no v1 case — synthetic fixture; log as oracle gap) |
| **B9** | inner | `personal-vs-business` set; personal rows excluded from business Schedule C totals. **INV-PERSONAL** | excluded "Kindle books / Spotify" rows |
| **B10** | inner | Custom subcategory rolls up to its Schedule C parent line. **INV-SUBCAT** | `6095 Software & Subscriptions` → Line 18 |
| **B11** | inner | Provenance complete: `coding_method ∈ {rule,inferred}` (+ rule_id / confidence); `review_status` defaults `unreviewed`. **INV-PROVENANCE** | any Detail row + `Rationale` col |
| **B12** | inner | Ambiguous/missing coding is **flagged for review**, not silently bucketed. **INV-FLAG** | `Venmo CASHOUT $1,019.58` and `Venmo PAYMENT $300` (both in `flags_for_rozella.csv`) |

**Known-misclassification handling:** when a B-test's expected coding contradicts a row that
appears in `flags_for_rozella.csv`, the divergence is **logged as a known v1 issue**, not a v2
failure (see README "conformance oracle").

---

## Group C — Storage stage + ingestion validation gate  (seam S3; spec — store to be built)

Input = a set of coded rows for an account/period + source statement totals. Output = the
append-only listing + reconciliation verdict.

| ID | Loop | Assertion (invariant) |
|---|---|---|
| **C1** | inner | **Append-only:** a correction adds a new/versioned row; the prior row is retained; `review_status` transitions `unreviewed → confirmed | corrected`. (storage-listing ADR) |
| **C2** | **inner (GATE)** | **Reconciliation:** sum of coded amounts per account/period ties to the source bank/card statement total; a mismatch is **flagged**, not absorbed. **INV-RECONCILE** |
| **C3** | inner | **Completeness + transfer netting:** every source txn in the period appears exactly once; transfer rows net to zero across the user's own accounts. **INV-COMPLETE, INV-TRANSFER** |
| **C4** | inner | **Idempotent rebuild:** re-running ingestion over the same raw feed produces an identical listing. **INV-IDEMPOTENT** |

---

## Bounded outer-loop for this segment

| ID | Loop | Assertion |
|---|---|---|
| **J-PCT** | outer | A user with linked Plaid accounts pulls transactions and ends with a coded listing that (a) **reconciles** to source-statement totals (INV-RECONCILE) **and** (b) satisfies the classification invariants (INV-TRANSFER/CCPAY/DRAW/LOAN/DUALCODE/PERSONAL). Passes only when the coded transactions are **correct**, not merely present. |

`J-PCT` is the acceptance gate for this segment — a true subset of THE JOURNEY that ends at
"coded transactions," before statements/dashboard.

---

## Mapping to the original brief's inner-loop list (what changed under single-entry)

| Original brief invariant | Status now | Replacement |
|---|---|---|
| Every entry balances (debits = credits) | **Retired** (single-entry) | INV-RECONCILE (C2) + INV-COMPLETE (C3) |
| Transfers net to zero, touch no income/expense | Kept | INV-TRANSFER (B5, C3) |
| Owner draws hit equity, not expense | Kept | INV-DRAW (B7) |
| Loan principal is not expense | Kept | INV-LOAN (B8) |
| Credit-card payments are transfers | Kept | INV-CCPAY (B6) |
| v1 income statement as conformance oracle | Refined | `detail.csv` = per-row coding oracle for Ingestion; `income_statement_v1.json` reconciliation belongs to the downstream Processing stage |

## Coding-rule ambiguities to pin before B/C are implemented (flag, don't assume)

1. **Which date is "cash movement" for a card purchase** — Plaid `date` (posted) vs
   `authorized_date`? Cash-basis suggests posted `date`; confirm.
2. **Pending transactions** — exclude until posted (cash hasn't moved), or include and update?
   The cash-movement ADR implies exclude-until-posted; confirm.
3. **Binary personal-vs-business vs. fractional business-use %** — the personal-vs-business
   ADR pins a **binary flag**, but the v1 oracle applies a `Biz %` (e.g. mileage, Starlink
   75%). A binary flag cannot represent partial allocation. Needs a decision (add
   `business_use_pct`, or handle allocation outside the listing).
4. **Source of statement totals for INV-RECONCILE** — Plaid balances/statements vs. the
   uploaded source statement? Pin the authoritative total the gate reconciles against.
