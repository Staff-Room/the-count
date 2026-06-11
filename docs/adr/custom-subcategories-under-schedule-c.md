<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd58170a04fed2603928169 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Support custom subcategories nested under Schedule C primary categories

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd58170a04fed2603928169

## Decision

Allow client-specific subcategories (e.g., technology supplies vs. musical supplies) while
keeping the Schedule C primary category as the parent, so detail rolls up cleanly to the
tax-required category.

## Context & Options

Clients want granular expense visibility, but the tax output must stay on standard
Schedule C lines. Nesting preserves both. Alternative of free-form categories was rejected
because it breaks the Schedule C roll-up.
