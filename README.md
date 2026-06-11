# 🧛 The Count

The transaction ingestion backend for StaffRoomAI: it pulls bank and credit-card
transactions from **Plaid**, codes them against **Schedule C** at ingestion, and
stores them durably in **Supabase**, where the website dashboard, MCP tools, and
a Notion worker consume them.

> *"I vant to count your transactions!"*

---

## What it does

- **Pulls transactions** from Plaid (`transactions/sync`) for every linked item
  in Supabase `plaid_items` — tokens are AES-256-GCM encrypted by the StaffRoomAI
  website at link time and decrypted here with the shared key.
- **Codes at ingestion**: applies the user's `category_mappings` rules
  (merchant / name / Plaid-category matches, priority order) to assign a
  Schedule C line; unmatched lands on `L99` (needs review); manual codings are
  never overwritten.
- **Writes Supabase**: `plaid_transactions`, `plaid_accounts`,
  `plaid_sync_cursors` — read live by the website's `/the-count` dashboard, the
  Staffroom MCP tools, and the Notion worker.

## Architecture

```
                         StaffRoomAI website (separate repo/deploy)
                         owns Plaid Link; writes encrypted tokens to plaid_items
                                          │
                                          │ POST /api/sync/item  (after Link,
                                          │  X-Sync-Secret, best-effort)
                                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Vercel project "the-count"  (the-count-rho.vercel.app)         │
│                                                                 │
│  GET  /api/cron-sync   ← Vercel cron, daily 08:00 UTC           │
│  POST /api/sync/item   ← website trigger, one item on demand    │
│                                                                 │
│  both → src/backend/plaid_sync.sync_item:                       │
│    Plaid transactions/sync → categorize.py → Supabase upserts   │
└──────────────┬──────────────────────────────────────────────────┘
               ▼
        Supabase (plaid_transactions / plaid_accounts / plaid_sync_cursors)
               │
               ├── website /the-count dashboard (live reads)
               ├── Staffroom MCP tools (queries + manual categorization)
               └── Notion worker (self-scheduled: accounts 1h, transactions 5m)
```

The entire HTTP surface is those two authenticated Vercel functions. There is no
local server; `BACKEND_STORE=sqlite` survives only as the test harness and
local-dev store.

## Repository layout

| Path | Purpose |
|---|---|
| `api/cron-sync.py` | Vercel cron function — nightly sync of all items. |
| `api/sync/item.py` | Vercel function — on-demand single-item sync (website trigger). |
| `src/backend/plaid_sync.py` | The sync engine: pagination, per-page cursor persistence, mutation restart. |
| `src/backend/categorize.py` | Schedule C coding rules applied at ingestion. |
| `src/backend/db.py` | Store dispatcher (`BACKEND_STORE=supabase` \| `sqlite`). |
| `src/backend/db_supabase.py` | PostgREST store: token decryption, retried upserts. |
| `src/backend/db_sqlite.py` | Test-harness / local-dev store. |
| `scripts/sync_plaid_now.py` | Manual one-shot sync; `--full` resets cursors (the full-resync path). |
| `scripts/verify_plaid_env.py` | Sanity-check Plaid keys vs environment. |
| `scripts/disconnect_all_plaid_items.py` | Wipe linked items (Plaid + store). |
| `notion-worker/` | Notion Worker (TypeScript) — see `notion-worker/SETUP.md`. |
| `docs/integrations/` | Cross-repo contracts (shared "Open contracts" block). |
| `docs/testing/` | Stage-bounded test decomposition + invariants. |

## Deployment

- **Vercel project** `the-count` → `the-count-rho.vercel.app`; deploys ride the
  Git integration on merge to `main`.
- **Cron**: `vercel.json` schedules `GET /api/cron-sync` daily at 08:00 UTC
  (300s max duration; per-page cursor persistence makes long backfills
  resumable across runs).
- **Env vars** (Vercel project settings): `PLAID_CLIENT_ID`, `PLAID_SECRET`,
  `PLAID_ENV`, `BACKEND_STORE=supabase`, `SUPABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY`, `INTEGRATIONS_ENCRYPTION_KEY` (must match the
  website's), `CRON_SECRET`, `SYNC_TRIGGER_SECRET` (must match the website's).
- The website needs `THE_COUNT_SYNC_URL=https://the-count-rho.vercel.app` and
  the same `SYNC_TRIGGER_SECRET` for the post-Link trigger.

## Local development & testing

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the test suite (isolated SQLite store, Plaid fully mocked)
python -m pytest tests/ -q

# Verify Plaid credentials match the configured environment
python scripts/verify_plaid_env.py

# Manual sync against the production store (idempotent)
BACKEND_STORE=supabase python scripts/sync_plaid_now.py
BACKEND_STORE=supabase python scripts/sync_plaid_now.py --full   # reset cursors, re-pull history
```

## Observability

- Per-item status: `plaid_sync_cursors.last_sync_at` / `last_error` in Supabase.
- Run logs: Vercel → the-count → Functions logs.
- The cron returns a JSON report (`{ok, items:[{item_id, added, modified,
  removed, pages, accounts, error?}]}`) visible in the cron invocation logs.

## Project management (Notion)

This repo is paired with a Notion project page used to plan and track work:

- **Project**: top-level direction → <https://www.notion.so/26e592a3035280ebbe93cfe1d58af13a>
- **Tasks / Milestones**: multi-day objectives within the project
- **Activities**: focused work sessions, created from a template
  (ID `15f592a30352800998baef9f9bcf83dd`)

Agents working in this repo are expected to keep the current **Activity** page
up to date — see [`CLAUDE.md`](CLAUDE.md) for the working-session protocol and
[`GitHub-Notion-Integration-Workflow.md`](GitHub-Notion-Integration-Workflow.md)
for the GitHub ↔ Notion linking flow.

## Troubleshooting

- **"Plaid sandbox credentials failed"** — keys are per-environment; copy the
  secret that matches `PLAID_ENV` from the
  [Plaid Dashboard → Keys](https://dashboard.plaid.com/developers/keys).
- **Website-linked account shows no transactions** — check the-count's Vercel
  function logs for `/api/sync/item` (a 401 means `SYNC_TRIGGER_SECRET` is
  missing/mismatched between the two Vercel projects); the nightly cron will
  still pick the item up.
- **Items invisible to the sync** — `PLAID_ENV` filters every Supabase query;
  it must match the env the website linked the item under.
- **Switching from sandbox to production** — sandbox `access_token`s do **not**
  work in production. Disconnect first:
  `PLAID_ENV=sandbox python scripts/disconnect_all_plaid_items.py`, then re-link
  from the website with the production env configured.
