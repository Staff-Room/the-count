# Plaid → Notion cash-book worker

This package is a **Notion Worker** that keeps two managed databases in sync with **Plaid**: **Bank accounts** and **Bank transactions**. Amounts follow Plaid’s convention (positive = outflows / charges, negative = inflows / credits).

## Prerequisites

- **Notion**: Business or Enterprise with **Notion Workers** enabled for the workspace (enable once via the Notion UI if `ntn workers deploy` reports workers are disabled).
- **Node**: 22+ (see `package.json` engines).
- **`ntn` CLI**: [Notion CLI](https://developers.notion.com/cli/get-started/overview); authenticate with `ntn login`.

## One-time: link bank accounts (The Count app)

1. Run the Flask app (`python run.py` from the repo root with `.env` containing `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENVIRONMENT`).
2. Use **Plaid Link** on the dashboard to connect institutions (stores `item_id` + `access_token` in SQLite).

## Push secrets to the hosted worker

Export linked items as JSON. The script lives at the **repository root** (`scripts/`, not inside `notion-worker/`):

```bash
# From repo root (recommended)
python3 scripts/export_plaid_items_for_worker.py > /tmp/plaid-items.json

# Or, if your shell is already in notion-worker/
python3 ../scripts/export_plaid_items_for_worker.py > /tmp/plaid-items.json
```

Set worker environment (replace values). If your `ntn` version rejects `--yes`, omit that flag:

```bash
cd notion-worker
ntn workers env set PLAID_CLIENT_ID="your-id" PLAID_SECRET="your-secret" PLAID_ENV="sandbox"
ntn workers env set PLAID_ITEMS_JSON="$(cat /tmp/plaid-items.json)"
```

Alternatively pass a minified JSON string built manually. Optional per-item field: `institution_name` (friendly label in the accounts table).

## Deploy

```bash
cd notion-worker
ntn workers deploy --name the-count-plaid-ledger
```

After the first successful deploy, Notion creates the managed databases. Re-deploy after code changes.

## Schedules and rate limits

- **Accounts** (`plaidAccountsSync`): `replace` mode every **1 hour** — full refresh from `/accounts/get`.
- **Transactions** (`plaidTransactionsSync`): **incremental** mode every **5 minutes** — `/transactions/sync` with per-item cursors stored in sync state.
- **Pacer** `plaidApi`: **8 requests / second** shared across syncs that call Plaid.

## Operating syncs

```bash
ntn workers sync status
ntn workers sync trigger plaidTransactionsSync --preview
ntn workers sync trigger plaidTransactionsSync
ntn workers sync state reset plaidTransactionsSync
ntn workers runs list
ntn workers runs logs <runId>
```

Use **`state reset`** after bugs or if cursors drift; the next run performs a fresh incremental window (Plaid will send an appropriate delta from the new cursor).

## Troubleshooting

- **0 upserts / 0 deletes** on sync: the worker likely has no Plaid items. Set **`PLAID_ITEMS_JSON`** (and Plaid client + secret + env) with `ntn workers env set` after running the export script from the **repo root** (see above).

## Optional next steps

- **Plaid webhooks**: Point Plaid’s webhook URL at a **`worker.webhook`** handler in this worker to complement the 5-minute schedule (not implemented in v1).
- **Manual backfill**: Add a second `replace`-mode sync or use `state reset` + full history policies per Plaid docs if you need a clean rebuild.

## Token rotation

When an item is removed or re-linked in Plaid, update **`PLAID_ITEMS_JSON`** via `ntn workers env set` and consider `ntn workers sync state reset plaidTransactionsSync`.
