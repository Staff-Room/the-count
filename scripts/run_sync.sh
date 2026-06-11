#!/usr/bin/env bash
# Wrapper run by launchd: activate venv, run the sync, then trigger Notion worker syncs.
set -euo pipefail

REPO_DIR="/Users/ethanwalker/Documents/GitHub/the-count"
LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"

# shellcheck disable=SC1091
source "${REPO_DIR}/venv/bin/activate"

cd "${REPO_DIR}"

# 1) Pull Plaid → active store (BACKEND_STORE in .env: sqlite or supabase)
python scripts/sync_plaid_now.py

# 2) Trigger Notion worker syncs (best-effort; worker has its own schedule anyway)
NTN_BIN="$(command -v ntn || echo /usr/local/bin/ntn)"
if [[ -x "${NTN_BIN}" ]]; then
  cd "${REPO_DIR}/notion-worker"
  "${NTN_BIN}" workers sync trigger plaidTransactionsSync || true
  "${NTN_BIN}" workers sync trigger plaidAccountsSync || true
fi
