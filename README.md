# 🧛 The Count

A personal-finance ledger that links bank accounts through **Plaid**, stores them
locally in SQLite, and mirrors the same data into **Notion** so you can budget,
slice, and report on it from inside your existing workspace.

> *"I vant to count your transactions!"*

---

## What it does

- **Connect institutions** with Plaid Link from a local Flask dashboard.
- **Cache** accounts, balances, and transactions in a local SQLite database.
- **Sync** the same data to two managed Notion databases (`Bank accounts (Plaid)`
  and `Bank transactions (Plaid)`) via a hosted Notion Worker.
- **Browse and export** balances, monthly cash flow, category breakdowns, and
  filtered transactions from the dashboard (CSV export included).
- **Stay organized** via a Notion project (Project → Task/Milestone → Activity).

## Architecture

```
┌──────────────────────┐        Plaid Link (browser)        ┌──────────────┐
│  Flask dashboard     │ ─────────────────────────────────▶ │   Plaid API  │
│  src/backend/app.py  │ ◀── access_token / transactions ── │              │
│  (localhost:5001)    │                                    └──────┬───────┘
│                      │                                           │
│   ┌──────────────┐   │                                           │
│   │  SQLite DB   │   │                                           │
│   │ thecount.db  │   │ scripts/export_plaid_items_for_worker.py  │
│   └──────────────┘   │ ─────────────────────────────────┐        │
└──────────┬───────────┘                                  │        │
           │                                              ▼        ▼
           │ /api/sync_transactions triggers     ┌────────────────────────┐
           └───────────────────────────────────▶ │  Notion Worker         │
                                                 │  notion-worker/        │
                                                 │   - plaidAccountsSync  │
                                                 │   - plaidTransactionsSync
                                                 └───────────┬────────────┘
                                                             │
                                                             ▼
                                                 ┌────────────────────────┐
                                                 │  Notion databases      │
                                                 │  (managed by worker)   │
                                                 └────────────────────────┘
```

Two halves talk to Plaid independently:

1. **Local app** (`src/backend/`) — Flask server that owns Plaid Link, stores
   `access_token`s and synced transactions in SQLite, and renders the dashboard.
2. **Notion Worker** (`notion-worker/`) — a hosted `@notionhq/workers` package
   that runs on a schedule (accounts hourly, transactions every 5 minutes) and
   writes into two managed Notion databases. It reads `PLAID_ITEMS_JSON` from
   its environment, which the local app exports for it.

The dashboard's "sync" button also pushes the latest linked items to the worker
and triggers an immediate sync, so the local store and Notion stay in lockstep.

## Repository layout

| Path | Purpose |
|---|---|
| `run.py` | Dev entry point — boots Flask on `:5001`. |
| `src/backend/app.py` | Flask routes (dashboard + JSON API). |
| `src/backend/db.py` | SQLite schema and queries. |
| `src/backend/plaid_sync.py` | Applies Plaid `transactions_sync` deltas. |
| `src/backend/templates/dashboard.html` | Dashboard UI. |
| `notion-worker/` | Notion Worker (TypeScript). See `notion-worker/SETUP.md`. |
| `scripts/verify_plaid_env.py` | Sanity-check Plaid keys vs environment. |
| `scripts/export_plaid_items_for_worker.py` | Emit `PLAID_ITEMS_JSON` from SQLite. |
| `scripts/disconnect_all_plaid_items.py` | Wipe linked items locally and in Plaid. |
| `.env.example` / `.mcp.json.example` | Templates for local secrets. |

## Prerequisites

- **Python** 3.8+
- **Node.js** 22+ and npm 10.9.2+ (only for the Notion Worker)
- A **Plaid** account — start in `sandbox`, then request a **Trial** in the
  [Plaid Dashboard](https://dashboard.plaid.com/) to use real bank data on
  `production`. Plaid no longer runs a separate `development` host.
- (Optional) A **Notion** workspace on a Business/Enterprise plan with
  Notion Workers enabled, and the [`ntn` CLI](https://developers.notion.com/cli/get-started/overview).

## Quick start

```bash
# 1. Clone and enter the project
git clone https://github.com/<you>/the-count.git
cd the-count

# 2. Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure Plaid
cp .env.example .env
# Edit .env: set PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENVIRONMENT (sandbox or production)
python scripts/verify_plaid_env.py     # confirms keys match the environment

# 4. Run the dashboard
python run.py
# Open http://localhost:5001/dashboard, click "Add account", and link via Plaid Link.
# Hit "Sync" to pull transactions into SQLite.
```

That's the full local loop. Linked items live in `src/backend/data/thecount.db`
(gitignored), so re-runs pick up where you left off.

### Optional: push data into Notion

Once at least one account is linked locally:

```bash
# Export linked items for the worker (writes JSON to stdout)
python3 scripts/export_plaid_items_for_worker.py > /tmp/plaid-items.json

# Configure and deploy the worker
cd notion-worker
npm install
ntn login                              # one-time auth to your Notion workspace
ntn workers env set \
  PLAID_CLIENT_ID="..." \
  PLAID_SECRET="..." \
  PLAID_ENV="production"               # or "sandbox"
ntn workers env set PLAID_ITEMS_JSON="$(cat /tmp/plaid-items.json)"
ntn workers deploy --name the-count-plaid-ledger
```

After the first successful deploy, Notion creates the two managed databases.
Subsequent syncs run on schedule (accounts: `1h`, transactions: `5m`) — or you
can force a run with `ntn workers sync trigger plaidTransactionsSync`.

The local dashboard's **Sync** button will also auto-push items to the worker
and trigger a sync when `ntn` is on your PATH (toggle via `NOTION_WORKER_AUTO_SYNC`).

See [`notion-worker/SETUP.md`](notion-worker/SETUP.md) for the full deploy
playbook, troubleshooting, and token-rotation guidance.

## Day-to-day commands

```bash
# Run the dashboard
python run.py

# Re-verify Plaid credentials after env changes
python scripts/verify_plaid_env.py

# Inspect or operate the Notion worker
cd notion-worker
ntn workers sync status
ntn workers sync trigger plaidTransactionsSync --preview   # dry run
ntn workers sync state reset plaidTransactionsSync         # reset cursors

# Disconnect everything (Plaid + local) before switching environments
PLAID_ENVIRONMENT=sandbox python scripts/disconnect_all_plaid_items.py
```

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

## Configuration reference

Local `.env` (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `PLAID_CLIENT_ID` / `PLAID_SECRET` | Plaid API credentials (per environment). |
| `PLAID_ENVIRONMENT` | `sandbox` or `production`. |
| `PLAID_WEBHOOK_URL` | Optional — only if you expose a public tunnel. |
| `THE_COUNT_DB_PATH` | Override SQLite location (default `src/backend/data/thecount.db`). |
| `NOTION_WORKER_AUTO_SYNC` | Disable auto-push to the Notion worker from the dashboard sync. |
| `NOTION_WORKER_DIR` | Override the path to the `notion-worker/` directory. |
| `NOTION_WORKER_ACCOUNTS_SYNC_KEY` / `NOTION_WORKER_TRANSACTIONS_SYNC_KEY` | Override the worker capability keys. |

## Troubleshooting

- **"Plaid sandbox credentials failed"** — keys are per-environment; copy the
  secret that matches `PLAID_ENVIRONMENT` from the
  [Plaid Dashboard → Keys](https://dashboard.plaid.com/developers/keys).
- **Notion worker reports 0 upserts / 0 deletes** — `PLAID_ITEMS_JSON` is
  missing or stale. Re-export and `ntn workers env set` it.
- **Switching from sandbox to production** — sandbox `access_token`s do **not**
  work in production. Disconnect first:
  `PLAID_ENVIRONMENT=sandbox python scripts/disconnect_all_plaid_items.py`,
  then change `.env` and re-link from the dashboard.
- **Dashboard sync didn't update Notion** — make sure `ntn` is on your PATH,
  `notion-worker/` exists at the repo root (or set `NOTION_WORKER_DIR`), and
  `NOTION_WORKER_AUTO_SYNC` isn't set to `false`.
