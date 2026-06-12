# The Count ↔ Staff Room AI: integration contracts (backend view)

> Last audited: 2026-06-12. Sibling docs:
> - `v0-staff-room-ai/docs/integrations/the-count.md`
> - `notion-worker/docs/integrations/the-count.md` (relative path; under `the-count/notion-worker/`)

## TL;DR

This backend is the **single Plaid puller**, deployed as two Vercel functions on
project `the-count` (`the-count-rho.vercel.app`). It reads items from Supabase
`plaid_items` (decrypting tokens with the shared `INTEGRATIONS_ENCRYPTION_KEY`),
pulls `transactions/sync`, applies the user's `category_mappings` (Schedule C
coding via `categorize.py`), and writes `plaid_transactions` / `plaid_accounts` /
`plaid_sync_cursors` back to Supabase. The Flask app, local Link flow, and
launchd runner are retired (2026-06-11); `BACKEND_STORE=sqlite` survives only as
the test harness / local-dev store. The Notion worker and the website's MCP
tools read what this backend writes.

## What this backend OWNS

- The Plaid pull: `transactions/sync` cursor management + rate-limited paging,
  all through one engine (`src/backend/plaid_sync.py:sync_item` /
  `sync_item_and_accounts`) with per-page cursor persistence and
  `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION` restart
- Categorization at ingestion: `src/backend/categorize.py` applies
  `category_mappings` in priority order; unmapped transactions land on `L99`
  (needs review); manual codings (`categorized_by='manual'`) are never overwritten
- The store dispatcher: `src/backend/db.py` (`BACKEND_STORE=supabase` in cloud;
  `sqlite` for tests/local dev) → `db_supabase.py` / `db_sqlite.py`
- Supabase writes: `plaid_transactions`, `plaid_accounts`, `plaid_sync_cursors`
- Manual ops: `scripts/sync_plaid_now.py` (one-shot sync against the active
  store; `--full` resets cursors and re-pulls — the only full-resync path)

## HTTP endpoints exposed (the entire surface — both on Vercel)

| Route | Method | Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| `/api/cron-sync` | GET | `Authorization: Bearer $CRON_SECRET` (fail-closed, constant-time) | — | `{ok, items[]}` | Vercel cron, daily 08:00 UTC; syncs all active items + account balances |
| `/api/sync/item` | POST | `X-Sync-Secret: $SYNC_TRIGGER_SECRET` (fail-closed, constant-time) | `{item_id}` | `{ok, item_id, added, modified, removed, pages, accounts}` | Three website callers (`THE_COUNT_SYNC_URL`): post-Link trigger (fire-and-forget), the dashboard "Sync from bank" button, and the `sync_transactions` MCP tool — last two fan out over all active items via `lib/integrations/the-count-sync.ts`. 404 unknown item, 502 Plaid error |

## What this backend CONSUMES

- Plaid REST: `transactions/sync`, `accounts/get` (plus `item/remove` via
  `scripts/disconnect_all_plaid_items.py`)
- Supabase PostgREST: reads `plaid_items` (decrypts `access_token_encrypted`),
  `category_mappings`; writes `plaid_transactions`, `plaid_accounts`,
  `plaid_sync_cursors` (`src/backend/db_supabase.py`; retries on 429/5xx)
- Env vars (Vercel project `the-count`): `PLAID_CLIENT_ID`, `PLAID_SECRET`,
  `PLAID_ENV` (canonical; `PLAID_ENVIRONMENT` fallback), `BACKEND_STORE=supabase`,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `INTEGRATIONS_ENCRYPTION_KEY`
  (must equal the website's), `CRON_SECRET`, `SYNC_TRIGGER_SECRET` (must equal
  the website's)

## What this backend DOES NOT consume

- The website's HTTP API — no calls (the website calls *us* via `/api/sync/item`)
- The Notion API or `ntn` CLI — the worker self-schedules off Supabase; the
  backend no longer triggers it
- Plaid Link / token exchange — linking is website-only (contract M3)

## Operational surface

- Vercel cron is the sole scheduler (`vercel.json`: `0 8 * * *` →
  `GET /api/cron-sync`, maxDuration 300s, per-page cursor persistence makes
  long backfills resumable across runs)
- Failures are observable in `plaid_sync_cursors.last_error` (per item) and
  Vercel function logs
- Manual / recovery: `BACKEND_STORE=supabase python scripts/sync_plaid_now.py [--full]`

## Open contracts

> This section is shared across all three integration docs. Keep it in sync.

### Resolved 2026-06-09 (Plaid triplication resolution)

| # | Was | Resolution |
|---|---|---|
| M1 | `PLAID_ENV` vs `PLAID_ENVIRONMENT` naming | All three systems now read `PLAID_ENV` first (`PLAID_ENVIRONMENT` kept as a fallback in the-count + worker). |
| M2 | Three Plaid-item stores | Supabase `plaid_items` is the single canonical store, env-tagged (`env='sandbox'\|'production'`). the-count reads it in `BACKEND_STORE=supabase` mode; the worker reads the Supabase mirror tables; `PLAID_ITEMS_JSON` is deleted. |
| M3 | Three Plaid Link flows | Website Link is canonical: it writes `plaid_items` (with `env`) then triggers the-count's `POST /api/sync/item`. Amended 2026-06-11: the backend's local Link flow was deleted with the Flask app — linking is website-only. The worker never links. |
| M4 (was O9) | No cloud scheduler for the Plaid pull | Vercel project `the-count` (`the-count-rho.vercel.app`) runs `GET /api/cron-sync` daily at 08:00 UTC (`vercel.json` cron, `CRON_SECRET` bearer auth). Amended 2026-06-11: the 07:15 launchd redundant runner is retired (see M10). |

### Resolved 2026-06-11 (cloud migration — Flask retired)

| # | Was | Resolution |
|---|---|---|
| M5 (was O3) | Unauthenticated Flask surface blocked cloud deploy | The Flask app is deleted. the-count's entire HTTP surface is two authenticated Vercel functions: `GET /api/cron-sync` (Bearer `CRON_SECRET`) and `POST /api/sync/item` (`X-Sync-Secret`, fail-closed, constant-time). |
| M6 (was O1) | `/the-count` dashboard reads static JSON/CSV | Stale claim — the page reads live Supabase (`app/(app)/the-count/_lib/live.ts` querying `plaid_transactions_coded`). No static data dir remains. |
| M7 (was O2) | `/api/transactions` had no documented caller | Deleted with the Flask app. Agent queries go through the website MCP tools; human UI is the website dashboard. |
| M8 (was O6) | `PLAID_WEBHOOK_URL` dead code | Deleted with the Flask app. |
| M9 (was O7) | `ntn` CLI silently no-ops in backend | The backend's worker-trigger path is deleted; the worker self-schedules (accounts 1h, transactions 5m) reading Supabase. |
| M10 | 07:15 launchd job (redundant local runner) | Retired after cloud verification. Vercel cron is the sole scheduler; manual ops / full resync: `BACKEND_STORE=supabase python scripts/sync_plaid_now.py [--full]`. |

### Unresolved (waiting on a decision)

| # | Open contract | Decision needed |
|---|---|---|
| O4 | Notion DB IDs are opaque (auto-created on first deploy) | If anything outside the worker references those DBs, IDs must be exported somewhere. |
| O5 | `NOTION_API_TOKEN` setup is manual | Worker requires it; backend doesn't help configure it; not in any onboarding doc. |
| O8 | Plaid token rotation | Website assumes tokens stay valid; no refresh logic anywhere. |
| O10 | Worker rollout pending | The Supabase-reading worker is committed but not deployed. Needs `ntn workers env set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... PLAID_ENV=...` then `ntn workers deploy`, then one manual `plaidTransactionsBackfill` trigger. Until then the old direct-Plaid build keeps running (de-facto fallback). |
| O11 | Historical backfill into `plaid_transactions` | the-count's SQLite holds existing history. One-shot import into Supabase (`env='production'`), or re-pull from Plaid with `--full` (limited to ~24 months)? |
