# The Count ↔ Notion Worker: integration contracts (worker view)

> Last audited: 2026-06-09. Sibling docs:
> - `the-count/docs/integrations/staff-room-ai.md` (relative: `../../../docs/integrations/staff-room-ai.md`)
> - `v0-staff-room-ai/docs/integrations/the-count.md`

## TL;DR

This worker no longer calls Plaid. It mirrors the Supabase tables that the-count's sync
writes — `plaid_accounts` and the `plaid_transactions_coded` view (transactions joined
with their resolved Schedule C line and custom category) — into two managed Notion
databases. It holds no Plaid credentials and no item list; `PLAID_ITEMS_JSON` is gone.

## What this worker OWNS

- Two managed Notion DBs: `bankAccounts` (replaced hourly), `bankTransactions`
  (incremental every 5m + manual `plaidTransactionsBackfill` for full re-mirror/deletes)
- Its Supabase request budget (pacer `supabaseApi`, 10 req/sec)
- Sync cursor state (keyset cursor on `(updated_at, transaction_id)`, in worker runtime)

## What this worker CONSUMES

- Supabase PostgREST (service role): `plaid_accounts`, `plaid_items`
  (institution names only), `plaid_transactions_coded` view — always filtered by
  `env=eq.$PLAID_ENV` (`src/index.ts`)
- Env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `PLAID_ENV`
  (accepts `PLAID_ENVIRONMENT` as fallback)
- No Plaid env vars, no `PLAID_ITEMS_JSON`

## Triggers exposed

- `plaidAccountsSync` — hourly replace; `ntn workers sync trigger plaidAccountsSync`
- `plaidTransactionsSync` — 5m incremental delta; `ntn workers sync trigger plaidTransactionsSync`
- `plaidTransactionsBackfill` — manual replace (full re-mirror; also the only path that
  removes Notion rows for transactions deleted upstream):
  `ntn workers sync state reset plaidTransactionsBackfill && ntn workers sync trigger plaidTransactionsBackfill`

## What this worker DOES NOT consume

- Plaid — zero direct API usage (the-count owns the pull)
- the-count's SQLite or Flask HTTP endpoints — no calls
- The website's HTTP API — no references

## Notion targets

| DB name | Write mode | Key field | Properties written |
|---|---|---|---|
| `bankAccounts` | `replace` (hourly) | `Account ID` | `Account ID`, `Name` (title), `Item ID`, `Institution name`, `Mask`, `Official name`, `Type`, `Subtype`, `ISO currency`, `Current balance`, `Available balance`, `Credit limit` |
| `bankTransactions` | `incremental` (5m) + manual backfill | `Transaction ID` | previous columns plus **`Schedule C line`** (select: Line 1…Line 27a, "Needs review") and **`Custom category`** (text) |

## Operational notes

- Workspace ID and Worker ID live in `workers.json` (Workspace `1d958a5f-deb0-4119-b8a1-9aa26929b498`, Worker `019e2c91-a3b0-73a9-8035-3e59bc24637a`)
- DB IDs auto-created on first deploy; visible via `ntn workers db list`
- `NOTION_API_TOKEN` is required for deploy but currently set manually
- Rollout: see O10 in Open contracts — env push + deploy + one backfill trigger

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
