<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd581018cebcaa2f4690752 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Seed transaction classification from card-issuer codes

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd581018cebcaa2f4690752

## Decision

Use the merchant/category codes provided by card issuers (e.g., Capital One) as the
starting classification for ingestion, then refine to GL/Schedule C coding.

## Context & Options

Issuer codes give a useful first-pass classification for free, reducing manual coding
effort. They are a starting point only — not authoritative — since issuer categories don't
map 1:1 to Schedule C.
