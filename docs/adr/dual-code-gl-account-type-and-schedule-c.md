<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd58101a49cc10e604129cc -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Dual-code transactions to GL account type and Schedule C category at ingestion

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd58101a49cc10e604129cc

## Decision

During ingestion, every transaction is coded to both (a) a GL account type — **asset,
liability, equity, revenue, or expense** — and (b) a **Schedule C category**. The
Schedule C–formatted income statement is the canonical analysis output.

## Context & Options

Cash-basis clients ultimately need IRS Schedule C alignment, but proper books still require
GL account typing for the balance sheet. The five GL account types map directly to the
standard accounting categories — assets, liabilities, equity (the balance sheet) and
revenue, expenses (the income statement) — and the balance sheet must satisfy the accounting
equation: **Assets = Liabilities + Equity**. Assets are recorded at historical cost. Coding
both the account type and the Schedule C category at ingestion avoids a re-classification
pass later. Alternative — coding only to Schedule C — was rejected because it can't produce
a balance sheet. Reference: Brian Feroldi, "Accounting Basics Explained Simply,"
longtermmindset.co.
