<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36f3ef6cafd5813aa261e830cc1e269d -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Recognize on cash-movement date; receivables and payables out of scope

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36f3ef6cafd5813aa261e830cc1e269d

## Decision

Every transaction is recognized on the **date cash actually moves**, so timing gaps between a
source event and cash settling do **not** create receivables or payables. Genuine A/R and A/P
(e.g., Stripe revenue arriving next month; a card charge paid next month) are **out of scope**
for The Count.

## Context & Options

The session noticed sources have different timing — a transaction may hit Stripe before cash
lands in the bank (a receivable), and a card expense is booked before it is paid (a payable).
This is the exact boundary at which single-entry / cash basis breaks: anything transacted on
credit normally needs double-entry / accrual. Two paths:

1. **Recognize strictly on cash-movement date** — receivables/payables simply don't exist
   until cash moves, keeping the cash-basis ADR and the single-entry decision (contra = cash)
   internally consistent.
2. Recognize on source-event date — which creates an A/R or A/P whose contra is not cash,
   breaking the single-entry assumption and pulling toward modified cash basis.

**Path (1) was chosen:** the product targets cash-basis sole proprietors / personal finance,
where the timing precision of accrual is not needed and the simplicity is the point.

Reference: cash vs. accrual basis; single-entry limits.
