# The Count ↔ Staff Room AI: integration contracts (backend view)

> Last audited: 2026-06-09. Sibling docs:
> - `v0-staff-room-ai/docs/integrations/the-count.md`
> - `notion-worker/docs/integrations/the-count.md` (relative path; under `the-count/notion-worker/`)

## TL;DR

The Flask backend is the **single Plaid puller**. In `BACKEND_STORE=supabase` mode it reads
items from Supabase `plaid_items` (decrypting tokens with the shared
`INTEGRATIONS_ENCRYPTION_KEY`), pulls `transactions/sync`, applies the user's
`category_mappings` (Schedule C coding via `categorize.py`), and writes
`plaid_transactions` / `plaid_accounts` / `plaid_sync_cursors` back to Supabase. In
`sqlite` mode (default) it remains the localhost dev dashboard with its own Link flow and
local store. The Notion worker and the website's MCP tools read what this backend writes.

## What this backend OWNS

- The Plaid pull: `transactions/sync` cursor management + rate-limited paging
  (`scripts/sync_plaid_now.py`, `src/backend/app.py:_sync_one_item`)
- Categorization at ingestion: `src/backend/categorize.py` applies `category_mappings`
  in priority order; unmapped transactions land on `L99` (needs review); manual codings
  (`categorized_by='manual'`) are never overwritten
- The store dispatcher: `src/backend/db.py` (`BACKEND_STORE=sqlite|supabase`) →
  `db_sqlite.py` (local dashboard) or `db_supabase.py` (cloud sync target)
- Supabase writes: `plaid_transactions`, `plaid_accounts`, `plaid_sync_cursors`
- Local dev dashboard + local Link flow (sqlite mode only)
- Notion worker sync triggers via `ntn` CLI (trigger-only; no more env handoff)

## HTTP endpoints exposed

| Route | Method | Request | Response | Notes |
|---|---|---|---|---|
| `/api/status` | GET | — | `{connected_items, environment, transaction_store, items[]}` | |
| `/api/create_link_token` | POST | — | `{link_token, expiration}` | sqlite/dev mode only |
| `/api/exchange_public_token` | POST | `{public_token, metadata}` | `{success, item_id}` | sqlite/dev mode only (supabase store raises — link via website) |
| `/api/sync_transactions` | POST | `{full?: bool}` or `?full=1` | `{summary, details[], notion_worker}` | syncs all items into the active store |
| `/api/sync/item` | POST | `{item_id}` + optional `X-Sync-Secret` header | `{ok, item_id, added, modified, removed, pages}` | called by website after Plaid Link; guarded by `SYNC_TRIGGER_SECRET` if set |
| `/api/accounts` | GET | — | `{accounts[]}` | live Plaid call per item |
| `/api/items/<item_id>` | DELETE | path param | `{ok, item_id}` | |
| `/api/transactions` | GET | `?limit&offset&account_id&since&until&q` | `{transactions[], total}` | |
| `/api/transactions/export.csv` | GET | filters | CSV stream | |
| `/api/dashboard/summary` | GET | — | dashboard aggregate | |

## What this backend CONSUMES

- Plaid REST: `link/token/create`, `item/public_token/exchange`, `transactions/sync`, `accounts/get`, `item/remove`
- Supabase PostgREST (supabase mode): reads `plaid_items` (decrypts `access_token_encrypted`),
  `category_mappings`; writes `plaid_transactions`, `plaid_accounts`, `plaid_sync_cursors`
  (`src/backend/db_supabase.py`)
- `ntn` CLI on PATH (worker sync triggers only) — silent no-op if missing
- Env vars: `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV` (canonical; `PLAID_ENVIRONMENT`
  fallback), `BACKEND_STORE`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
  `INTEGRATIONS_ENCRYPTION_KEY` (must equal the website's), `SYNC_TRIGGER_SECRET`,
  `THE_COUNT_DB_PATH`, `NOTION_WORKER_*`

## What this backend DOES NOT consume

- The website's HTTP API — no calls (the website calls *us* via `/api/sync/item`)
- The Notion API directly — talks only through `ntn workers` CLI
- `PLAID_ITEMS_JSON` — deleted; the worker no longer receives credentials from us

## Operational surface (cron, schedules)

- launchd: `~/Library/LaunchAgents/com.zwconsulting.thecount.plaidsync.plist`, daily 07:15
- Runs `scripts/run_sync.sh` → `python scripts/sync_plaid_now.py` (active store) → triggers worker syncs
- Cloud deployment (phase 4) pending — see O9

## Open contracts

> This section is shared across all three integration docs. Keep it in sync.

### Resolved 2026-06-09 (Plaid triplication resolution)

| # | Was | Resolution |
|---|---|---|
| M1 | `PLAID_ENV` vs `PLAID_ENVIRONMENT` naming | All three systems now read `PLAID_ENV` first (`PLAID_ENVIRONMENT` kept as a fallback in the-count + worker). |
| M2 | Three Plaid-item stores | Supabase `plaid_items` is the single canonical store, env-tagged (`env='sandbox'\|'production'`). the-count reads it in `BACKEND_STORE=supabase` mode; the worker reads the Supabase mirror tables; `PLAID_ITEMS_JSON` is deleted. |
| M3 | Three Plaid Link flows | Website Link is canonical: it writes `plaid_items` (with `env`) then triggers the-count's `POST /api/sync/item`. The backend's local Link flow remains for sqlite dev mode only; the worker never links. |
| M4 (was O9) | No cloud scheduler for the Plaid pull | Vercel project `the-count` (`the-count-rho.vercel.app`) runs `GET /api/cron-sync` daily at 08:00 UTC (`vercel.json` cron, `CRON_SECRET` bearer auth). Only that one function is deployed — the Flask app stays local (O3 still open). The 07:15 launchd job remains as a redundant local runner. |

### Unresolved (waiting on a decision)

| # | Open contract | Decision needed |
|---|---|---|
| O1 | `/the-count` Schedule C dashboard reads static JSON/CSV | Replace with live Supabase reads (same query as `schedule_c_summary`) or keep as a month-end snapshot? |
| O2 | the-count's `/api/transactions` has no documented caller | The MCP tools now cover agent queries; is this Flask endpoint still needed beyond the local dashboard? |
| O3 | Backend auth is only a shared secret on `/api/sync/item` | Other Flask endpoints are unauthenticated localhost-only; a real auth model is a blocker for the phase 4 cloud deploy. |
| O4 | Notion DB IDs are opaque (auto-created on first deploy) | If anything outside the worker references those DBs, IDs must be exported somewhere. |
| O5 | `NOTION_API_TOKEN` setup is manual | Worker requires it; backend doesn't help configure it; not in any onboarding doc. |
| O6 | `PLAID_WEBHOOK_URL` is dead code in the-count backend | Either implement a webhook receiver, or remove the env var. |
| O7 | `ntn` CLI silently no-ops if missing in backend | Should fail loudly, or detect once at startup and log a warning. |
| O8 | Plaid token rotation | Website assumes tokens stay valid; no refresh logic anywhere. |
| O10 | Worker rollout pending | The Supabase-reading worker is committed but not deployed. Needs `ntn workers env set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... PLAID_ENV=...` then `ntn workers deploy`, then one manual `plaidTransactionsBackfill` trigger. Until then the old direct-Plaid build keeps running (de-facto fallback). |
| O11 | Historical backfill into `plaid_transactions` | the-count's SQLite holds existing history. One-shot import into Supabase (`env='production'`), or re-pull from Plaid with `--full` (limited to ~24 months)? |
