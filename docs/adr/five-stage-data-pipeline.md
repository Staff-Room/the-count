<!-- READ-ONLY MIRROR of a Notion ADR. Canonical: https://www.notion.so/36e3ef6cafd5814d9855f1640adb16d4 -->
<!-- Do not hand-edit. Edit in Notion, then re-sync (see ./README.md). Last synced: 2026-05-28. -->

# Five-stage data pipeline architecture

- **Status:** Accepted
- **Date:** 2026-05-28
- **Canonical source:** https://www.notion.so/36e3ef6cafd5814d9855f1640adb16d4

## Decision

The Count is structured as a five-stage pipeline: **Sources → Ingestion → Storage →
Processing → Analysis/QA**. Each stage has a defined input and output so workflows can be
built and tested stage by stage.

## Context & Options

Needed a common backbone for every financial workflow. The staged model lets us productize
the early stages independently and isolates where errors occur. Alternative was an
end-to-end monolithic flow, rejected because it makes validation and incremental
productization harder.
