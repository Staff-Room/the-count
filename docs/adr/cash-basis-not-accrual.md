<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd581078f79eaf6679db4b5 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Keep books on a cash basis, not accrual

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd581078f79eaf6679db4b5

## Decision

The Count records revenue and expenses **when cash actually moves** (received or paid), not
when earned or incurred. The matching principle is intentionally NOT applied. Any assets are
carried at historical (acquisition) cost.

## Context & Options

Target clients are small / sole-proprietor businesses that don't need QuickBooks. Cash basis
is simpler — bookkeeping only has to record cash movement — and accurately reflects the
client's cash position, which also lines up with how they file (Schedule C). Accrual was
rejected for the core product: it requires anticipating events and applying
revenue-recognition + matching across periods, adding complexity this segment doesn't need.
Accepted trade-off: cash basis doesn't match revenue to the period that generated it, so it
is a less precise measure of profitability — acceptable here. Reference: Brian Feroldi,
"Accounting Basics Explained Simply," longtermmindset.co.
