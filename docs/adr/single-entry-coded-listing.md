<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36f3ef6cafd581e2a1a1c3737e1c0e85 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Record transactions as a single-entry coded listing, not double-entry journal entries

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36f3ef6cafd581e2a1a1c3737e1c0e85
- **Supersedes:** [The journal-entry event is the system's core record (double-entry, both legs)](journal-entry-event-core-record.md)

## Decision

The Count's canonical record is a **flat, single-leg coded transaction listing — one row per
source transaction**. The system does **not** generate balanced double-entry journal entries;
the offsetting side is assumed to be **cash** and is **not recorded**, since for cash-basis
clients (credit cards paid off monthly) the contra is always cash.

Per-row fields: `date`, `amount`, `vendor` (to/from), GL account / expense-or-revenue
category, `personal-vs-business`, plus any ingestion-specific fields.

## Context & Options

ADR-1 had pinned a double-entry journal-entry event (both legs, debits = credits) as the core
record. This session reversed that: there will be no journal entries — the output is a single
listing of coded transactions. The pattern adopted is **single-entry bookkeeping** — the
check-register model the IRS treats as income-statement-based — the deliberate opposite of the
double-entry-ledger-as-event-source pattern. It fits because the target segment is cash-basis
sole proprietors / personal finance with **no accounts receivable or payable**, exactly the
niche single-entry suits; storing a second, always-"cash" leg is redundant overhead.

**Rejected:** ADR-1's double-entry event.

**Accepted trade-off:** single-entry is **not self-balancing**, so the `debits = credits`
arithmetic error-check is **given up**. Accuracy is instead enforced by **reconciling listing
totals back to the source statement totals** (see the validation-gate ADR).

Reference: M. Fowler, *Event Sourcing* (2005); single-entry bookkeeping (IRS-recognized).
