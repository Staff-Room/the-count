# Architecture Decision Records (read-only mirror)

> **This directory is a read-only mirror.** The canonical ADRs live in Notion:
> **The Count → System Documentation → [The Count (ADR)](https://www.notion.so/01a3ef6cafd582f49cf9819893ee950d) → Decisions database.**
> Treat the ADRs as the **declared model**: binding constraints that code conforms to,
> not suggestions. If code diverges from an ADR, that is a defect, not a design choice.
>
> **Do not hand-edit the files in this folder.** Edit the decision in Notion, then
> re-sync (see below). These files exist so code and tests can be checked against the
> declared model without a live Notion round-trip.

- **Decisions database:** <https://www.notion.so/e9e3ef6cafd5821bbb280180222335f2>
- **Data source:** `collection://4253ef6c-afd5-82e0-bf55-0724d6469b00`
- **Last synced:** 2026-05-28 (re-synced after the double-entry → single-entry reversal)

## Decisions

| Decision | Status | Date | Source |
|---|---|---|---|
| [Five-stage data pipeline architecture](five-stage-data-pipeline.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd5814d9855f1640adb16d4) |
| [Use Plaid for automated source connection](use-plaid-for-source-connection.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581b8a438cdce286102a2) |
| [Seed transaction classification from card-issuer codes](seed-classification-from-card-issuer-codes.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581018cebcaa2f4690752) |
| [Dual-code transactions to GL account type and Schedule C category at ingestion](dual-code-gl-account-type-and-schedule-c.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd58101a49cc10e604129cc) |
| [Support custom subcategories nested under Schedule C primary categories](custom-subcategories-under-schedule-c.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd58170a04fed2603928169) |
| [Capture personal-vs-business and client-assignment coding dimensions](personal-vs-business-and-client-assignment.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581abb637f8ffd77ef888) |
| [Keep books on a cash basis, not accrual](cash-basis-not-accrual.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581078f79eaf6679db4b5) |
| [Record transactions as a single-entry coded listing, not double-entry journal entries](single-entry-coded-listing.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36f3ef6cafd581e2a1a1c3737e1c0e85) |
| [Storage ledger artifact is the coded transaction listing (retire "modified general ledger")](storage-ledger-is-coded-transaction-listing.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36f3ef6cafd58151b699ea1cebeea0ba) |
| [Recognize on cash-movement date; receivables and payables out of scope](recognize-on-cash-movement-date.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36f3ef6cafd5813aa261e830cc1e269d) |
| [Validation gates after ingestion and processing, with a flag-don't-assume prompt rule](validation-gates-after-ingestion-and-processing.md) | Accepted | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581fab559e9207d6c4c0d) |
| [Storage layer = monthly modified ledger, balance sheet, and income statement](storage-monthly-modified-ledger.md) | ~~Superseded~~ | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd581059253e88f66d59b96) |
| [The journal-entry event is the system's core record (double-entry, both legs)](journal-entry-event-core-record.md) | ~~Superseded~~ | 2026-05-28 | [Notion](https://www.notion.so/36e3ef6cafd5819e9006d1c8e1573528) |

## Re-syncing this mirror

The mirror is generated from the Notion Decisions data source. To refresh:

1. Query the `All Decisions` view of the Decisions database (data source
   `collection://4253ef6c-afd5-82e0-bf55-0724d6469b00`) via the Notion MCP.
2. Regenerate one `*.md` per decision plus `adr-manifest.json` from the result.
3. Update **Last synced** above.

`adr-manifest.json` is the machine-readable index (stable Notion page IDs + status)
that conformance tooling should load rather than parsing the Markdown.

## Model change log

- **2026-05-28 — double-entry → single-entry reversal.** The journal-entry (double-entry)
  ADR and the "modified general ledger" storage ADR were **superseded**. The canonical record
  is now a **single-entry coded transaction listing** (one row per source transaction, contra
  assumed = cash). The Storage ledger artifact is that listing (append-only log + derived
  statements). Recognition is strictly on cash-movement date; A/R and A/P are out of scope.
  **Consequence for tests:** the `debits = credits` invariant is retired; ingestion accuracy
  is enforced by **reconciling listing totals to source-statement totals**. See
  [`docs/testing/`](../testing/README.md).

## Known gaps (surfaced, awaiting decision)

- **ADRs are unnumbered in Notion.** Decisions are referenced informally (e.g. "ADR #8")
  but the Decisions database has no Number/ID property, so "#N" references are ambiguous.
- **Coding-rule ambiguities** (which date is "cash movement" for cards; pending-transaction
  handling; binary personal-vs-business vs. fractional business-use %) are flagged in
  [`docs/testing/plaid-to-coded-transactions.md`](../testing/plaid-to-coded-transactions.md).
