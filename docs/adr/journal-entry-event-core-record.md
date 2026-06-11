<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd5819e9006d1c8e1573528 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# The journal-entry event is the system's core record (double-entry, both legs)

- **Status:** Superseded
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd5819e9006d1c8e1573528
- **Superseded by:** [Record transactions as a single-entry coded listing, not double-entry journal entries](single-entry-coded-listing.md)

> **SUPERSEDED.** The Count adopted **single-entry** bookkeeping: there are no balanced
> double-entry journal entries, and the `debits = credits` invariant below no longer applies.
> The canonical record is a flat, single-leg coded transaction listing. This page is retained
> for history only. See [single-entry-coded-listing.md](single-entry-coded-listing.md) and
> [storage-ledger-is-coded-transaction-listing.md](storage-ledger-is-coded-transaction-listing.md).

## Decision

Adopt one canonical event — the **journal-entry event** — as the system's core record.
Every coded transaction is a balanced double-entry journal entry carrying **BOTH legs** (a
debit and a credit), produced at the **Ingestion → Storage seam** by transforming one
Plaid/Stripe transaction (one leg + a category guess) into a balanced entry. Minimum fields:
`entry_id`; `date` (cash-movement date); `currency`; `amount`; `debit_account` +
`credit_account`, each with a GL account type (asset/liability/equity/revenue/expense);
`memo`; source reference (system + source txn id); coding dimensions (Schedule C category +
optional custom subcategory, personal-vs-business, client assignment); provenance (rule vs.
inferred); review status (unreviewed/confirmed/corrected). **Every entry must balance:
debits = credits.**

## Context & Options

The Five-stage pipeline ADR defines stages but no ADR pins the *record* that crosses the
Ingestion → Storage seam, so each stage invents its own shape and the seam can't be tested as
a contract. Pinning one event gives the dual-coding, custom-subcategory,
personal-vs-business/client-assignment, and validation-gate ADRs a single object to attach
to. A Plaid transaction is the ingestion INPUT (one leg + an issuer/Plaid category guess per
the card-issuer-codes ADR), **NOT** the event; ingestion derives the contra leg and the GL
account types. Reference: Brian Feroldi, "Accounting Basics Explained Simply,"
longtermmindset.co (double-entry, the accounting equation).

## Why this ADR

The Count is a five-stage pipeline (Sources → Ingestion → Storage → Processing →
Analysis/QA), but no ADR pins the *record* that flows across the Ingestion → Storage seam.
Without a pinned event, every stage invents its own shape and the seams cannot be tested as
contracts. This ADR declares that record.

## The core event: the journal-entry event

Every coded transaction is one **balanced double-entry journal entry** that carries BOTH
legs. It is produced by the Ingestion stage and is the unit stored by the Storage stage.

- A **Plaid/Stripe transaction is the INPUT, not the event**: it is one leg (a single account
  movement) plus a category *guess* (issuer/Plaid code, per the card-issuer-codes ADR).
- The **Ingestion → Storage seam** transforms that input into a balanced entry by (a) deriving
  the contra leg, (b) assigning a GL account type to each leg, and (c) attaching coding
  dimensions.

## Field schema (v1, minimum)

```json
{
  "entry_id": "uuid",                         // stable id of the journal-entry event
  "date": "YYYY-MM-DD",                        // cash-movement date (cash-basis ADR)
  "currency": "USD",                           // ISO 4217
  "amount": "decimal(>0)",                     // entry magnitude; equals each leg's amount
  "debit_account": "string",                   // GL account debited
  "debit_account_type": "asset|liability|equity|revenue|expense",
  "credit_account": "string",                  // GL account credited
  "credit_account_type": "asset|liability|equity|revenue|expense",
  "memo": "string",
  "source": {
    "system": "plaid|stripe|manual",
    "source_txn_id": "string",                 // Plaid/Stripe txn id; idempotency key
    "source_account_id": "string"              // originating bank/card account
  },
  "coding": {
    "schedule_c_category": "string|null",      // null for non-P&L entries (e.g. transfers)
    "custom_subcategory": "string|null",       // rolls up to schedule_c_category
    "personal_or_business": "personal|business",
    "client_id": "string|null"                 // revenue attribution
  },
  "provenance": {
    "coding_method": "rule|inferred",          // how it was coded
    "rule_id": "string|null",                  // set when coding_method = rule
    "model_confidence": "number|null",         // set when coding_method = inferred
    "source_category_guess": "string|null"     // issuer/Plaid seed (card-issuer ADR)
  },
  "review_status": "unreviewed|confirmed|corrected",
  "ingested_at": "ISO-8601 datetime"
}
```

## Invariants (enforced at the ingestion validation gate)

- **Balanced:** debit amount == credit amount == `amount`, and `amount` > 0. (Double-entry;
  Validation-gate ADR.)
- `currency` and `date` are required.
- `source.source_txn_id` is the **idempotency key**: one event per source transaction.
- Account-type rules the schema must support (the inner-loop test oracle):
  - Transfers between the user's own accounts net to zero and touch **no** revenue/expense
    account (both legs asset/liability; `schedule_c_category` = null).
  - Owner draws hit **equity**, not expense.
  - Loan principal is a **liability** movement, not expense.
  - Credit-card payments are **transfers** (asset ↔ liability), not expense.
- Over any set of entries, the balance sheet identity holds: **Assets = Liabilities +
  Equity** (Dual-coding ADR).

## Mapping to existing ADRs

- **Cash basis ADR** → `date` is the cash-movement date (recorded when cash moves).
- **Dual-coding ADR** → every leg carries a GL account type AND the entry carries a
  Schedule C category.
- **Card-issuer-codes ADR** → `provenance.source_category_guess` seeds, but does not bind,
  coding.
- **Custom-subcategory ADR** → `coding.custom_subcategory` rolls up to `schedule_c_category`.
- **Personal-vs-business + client-assignment ADR** → `coding.personal_or_business`,
  `coding.client_id`.
- **Validation-gate ADR** → invariants above are checked at the ingestion gate;
  flag-don't-assume on missing/ambiguous coding.

## Open questions to pin (flagged, not assumed)

1. **GL storage model is unpinned.** The Storage ADR says Storage maintains a *modified
   general ledger* plus stored balance sheet and income statement. It does NOT say the GL is
   an append-only event log, nor that statements are pure projections never hand-edited. Two
   different models:
   - **(A) Append-only / event-sourced GL:** entries immutable; corrections are new
     reversing entries; statements are derived projections.
   - **(B) Modified (mutable) GL:** entries edited in place; statements stored as artifacts,
     refreshed monthly.
   The `review_status = corrected` value and the word *modified* lean toward (B);
   auditability favors (A). **Needs its own ADR before any storage/correction code is
   written.**
2. **Multi-leg / split entries.** v1 fixes a single debit + single credit. Splits (one
   deposit across several Schedule C lines) need either multiple entries sharing a
   `source_txn_id` or a generalized `legs[]` array. Deferred; flag when a real case appears.
3. **ADR numbering.** The Decisions DB has no Number/ID property, yet decisions get
   referenced as "ADR #8". Recommend adding a stable Number property so references are
   unambiguous.
