<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd581059253e88f66d59b96 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Storage layer = monthly modified ledger, balance sheet, and income statement

- **Status:** Superseded
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd581059253e88f66d59b96
- **Superseded by:** [Storage ledger artifact is the coded transaction listing (retire "modified general ledger")](storage-ledger-is-coded-transaction-listing.md)

> **SUPERSEDED.** The undefined "modified general ledger" term was retired. The Storage ledger
> artifact is now the single coded transaction listing (append-only log + derived statements).
> The balance sheet, income statement, and monthly-refresh cadence are retained by the
> superseding ADR. See [storage-ledger-is-coded-transaction-listing.md](storage-ledger-is-coded-transaction-listing.md).

## Decision

The Storage stage maintains a **modified general ledger** plus a **balance sheet** and
**income statement**, refreshed on a **monthly cadence**.

## Context & Options

Monthly close is the natural period for cash-basis bookkeeping and matches the month-end
close workflow. Storing all three artifacts (not just the ledger) means downstream
processing reads from settled statements rather than recomputing each time.

> **Conformance note (not part of the canonical decision):** This ADR pins a *modified
> general ledger* and **stored** statements refreshed monthly. It does **not** declare the
> ledger to be an append-only / event-sourced log, nor that the statements are pure
> projections "never hand-edited." That storage/correction model is currently **unpinned**
> and is flagged as an open question in
> [journal-entry-event-core-record.md](journal-entry-event-core-record.md).
