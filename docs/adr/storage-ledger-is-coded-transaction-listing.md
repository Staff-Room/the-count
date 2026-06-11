<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36f3ef6cafd58151b699ea1cebeea0ba -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Storage ledger artifact is the coded transaction listing (retire "modified general ledger")

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36f3ef6cafd58151b699ea1cebeea0ba
- **Supersedes:** [Storage layer = monthly modified ledger, balance sheet, and income statement](storage-monthly-modified-ledger.md)

## Decision

The Storage stage's **ledger artifact is the single coded transaction listing** from the
single-entry ADR. The undefined "modified general ledger" term is **retired**. The balance
sheet, income statement, and monthly-refresh cadence from ADR-5 are **retained**.

## Context & Options

ADR-5 promised a "modified general ledger plus a balance sheet and income statement, refreshed
monthly," but the session flagged that no one could define "modified general ledger" and that
ADR-5 never declared an append-only event-sourced log. The ambiguity is resolved by pinning
the artifact to the **coded transaction listing**. What is retained — an **append-only listing
of events with derived statements** — is the **append-only-log-with-derived-projections**
pattern: the log is the source of truth and the statements are projections.

**Rejected:** keeping the undefined "modified general ledger."

Dependent on the single-entry decision; if single-entry is reversed, this reverts with it.

Reference: P. Helland (the log is the truth; the store is a cache); M. Fowler, *Event Sourcing*.
