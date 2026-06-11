<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd581b8a438cdce286102a2 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Use Plaid for automated source connection

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd581b8a438cdce286102a2

## Decision

Connect client bank and card accounts via Plaid to pull statement data automatically into
the Sources stage, rather than relying on manual statement uploads.

## Context & Options

Gathering bank/credit-card statements is the most time-consuming admin task today. Plaid
would automate the Sources stage. The manual-upload path (e.g., Rockport test case) is the
near-term mechanism; Plaid is the target once the core workflow is productized.
