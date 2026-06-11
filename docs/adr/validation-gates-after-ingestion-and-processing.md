<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd581fab559e9207d6c4c0d -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Validation gates after ingestion and processing, with a flag-don't-assume prompt rule

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd581fab559e9207d6c4c0d

## Decision

Insert explicit validation/QA steps after both the ingestion and the processing pipelines.
All prompts must include the instruction: *"If data is missing or inconsistent, flag it
before proceeding rather than filling in assumptions."*

## Context & Options

Financial accuracy is non-negotiable; silent AI assumptions are the main risk. Gating after
each pipeline catches errors before they propagate, and the prompt rule prevents the model
from fabricating to fill gaps. The ingestion gate enforces accuracy by **reconciling the coded
transaction listing's totals back to the source bank/card statement totals** — the
reconciliation step that substitutes for self-balancing under single-entry — which is the
structural check that the books actually tie out. Together these are the core controls that
make the AI workflow trustworthy enough for client books. Reference: Brian Feroldi, "Accounting
Basics Explained Simply," longtermmindset.co (objectivity).

> **Amended 2026-05-28:** the original double-entry / `debits = credits` balancing rationale
> was retired when The Count adopted single-entry. See
> [single-entry-coded-listing.md](single-entry-coded-listing.md).
