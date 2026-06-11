"""
The Count - Plaid integration and financial dashboard backend.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
import shutil
import subprocess
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import plaid
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

import db
import plaid_sync

load_dotenv()

app = Flask(__name__)

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
# PLAID_ENV is canonical (matches the website); PLAID_ENVIRONMENT kept as fallback.
_raw_plaid_env = (os.getenv("PLAID_ENV") or os.getenv("PLAID_ENVIRONMENT") or "sandbox").lower()
# Plaid retired development.plaid.com; real-data testing uses production (Trial plan).
if _raw_plaid_env == "development":
    import warnings

    warnings.warn(
        "PLAID_ENV=development is deprecated (host removed). Using production. "
        "Request a Trial plan in the Plaid Dashboard for free real-data testing.",
        stacklevel=1,
    )
    PLAID_ENV = "production"
else:
    PLAID_ENV = _raw_plaid_env

PLAID_ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

configuration = plaid.Configuration(
    host=PLAID_ENVIRONMENTS[PLAID_ENV],
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
        "plaidVersion": "2020-09-14",
    },
)
api_client = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)

db.init_db()


def _month_start(d: date | None = None) -> date:
    if d is None:
        d = date.today()
    return date(d.year, d.month, 1)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _notion_worker_dir() -> Path:
    configured = os.getenv("NOTION_WORKER_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "notion-worker"


def _run_ntn_command(
    ntn_bin: str, args: list[str], *, cwd: Path, description: str
) -> None:
    proc = subprocess.run(
        [ntn_bin, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{description} failed (exit {proc.returncode})")


def _sync_notion_worker_from_linked_items(*, reset_sync_state: bool = False) -> dict:
    """
    Trigger Notion worker syncs after a Plaid pull. The worker reads
    transactions from Supabase (no Plaid credentials or item handoff needed).

    When reset_sync_state is True, clears worker cursors first (use after
    deleting Notion databases or when Plaid/Notion are out of sync).
    """
    if not _env_bool("NOTION_WORKER_AUTO_SYNC", True):
        return {
            "attempted": False,
            "ok": False,
            "skipped": True,
            "message": "NOTION_WORKER_AUTO_SYNC is disabled",
        }

    worker_dir = _notion_worker_dir()
    if not worker_dir.exists():
        return {
            "attempted": False,
            "ok": False,
            "skipped": True,
            "message": f"Notion worker directory not found: {worker_dir}",
        }

    ntn_bin = shutil.which("ntn")
    if not ntn_bin:
        return {
            "attempted": False,
            "ok": False,
            "skipped": True,
            "message": "ntn CLI is not installed or not on PATH",
        }

    accounts_sync_key = os.getenv(
        "NOTION_WORKER_ACCOUNTS_SYNC_KEY", "plaidAccountsSync"
    ).strip()
    transactions_sync_key = os.getenv(
        "NOTION_WORKER_TRANSACTIONS_SYNC_KEY", "plaidTransactionsSync"
    ).strip()

    try:
        sync_keys = [
            k
            for k in (accounts_sync_key, transactions_sync_key)
            if k
        ]
        reset_keys: list[str] = []
        if reset_sync_state:
            for sync_key in sync_keys:
                _run_ntn_command(
                    ntn_bin,
                    ["workers", "sync", "state", "reset", sync_key],
                    cwd=worker_dir,
                    description=f"ntn workers sync state reset {sync_key}",
                )
                reset_keys.append(sync_key)

        triggered = []
        for sync_key in sync_keys:
            _run_ntn_command(
                ntn_bin,
                ["workers", "sync", "trigger", sync_key],
                cwd=worker_dir,
                description=f"ntn workers sync trigger {sync_key}",
            )
            triggered.append(sync_key)

        return {
            "attempted": True,
            "ok": True,
            "reset_sync_state": reset_sync_state,
            "reset": reset_keys,
            "triggered": triggered,
        }
    except Exception as e:
        return {
            "attempted": True,
            "ok": False,
            "message": str(e),
        }


@app.route("/")
def root():
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    counts = db.transaction_counts()
    items = db.list_items()
    return jsonify(
        {
            "connected_items": len(items),
            "environment": PLAID_ENV,
            "transaction_store": counts,
            "items": [
                {
                    "item_id": i["item_id"],
                    "institution_name": i.get("institution_name"),
                    "last_sync_at": i.get("last_sync_at"),
                    "last_error": i.get("last_error"),
                }
                for i in items
            ],
        }
    )


@app.route("/api/create_link_token", methods=["POST"])
def create_link_token():
    try:
        webhook = os.getenv("PLAID_WEBHOOK_URL", "").strip()
        kwargs = {
            "products": [Products("transactions")],
            "client_name": "The Count",
            "country_codes": [CountryCode("US")],
            "language": "en",
            "user": LinkTokenCreateRequestUser(client_user_id="local-dashboard-user"),
        }
        if webhook:
            kwargs["webhook"] = webhook

        link_request = LinkTokenCreateRequest(**kwargs)
        response = plaid_client.link_token_create(link_request)
        return jsonify(
            {
                "link_token": response["link_token"],
                "expiration": response["expiration"],
            }
        )
    except plaid.ApiException as e:
        return jsonify({"error": json.loads(e.body)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/exchange_public_token", methods=["POST"])
def exchange_public_token():
    try:
        body = request.get_json(force=True, silent=True) or {}
        public_token = body.get("public_token")
        meta = body.get("metadata") or {}
        institution = meta.get("institution") or {}

        if not public_token:
            return jsonify({"error": "public_token is required"}), 400

        exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = plaid_client.item_public_token_exchange(exchange_request)
        access_token = response["access_token"]
        item_id = response["item_id"]

        db.upsert_item(
            item_id,
            access_token,
            institution_id=institution.get("institution_id"),
            institution_name=institution.get("name"),
        )

        return jsonify(
            {
                "success": True,
                "item_id": item_id,
                "message": "Successfully connected account",
            }
        )
    except plaid.ApiException as e:
        return jsonify({"error": json.loads(e.body)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _sync_one_item(item_id: str, access_token: str) -> dict:
    """Run transactions_sync until complete; persist cursor and transactions."""
    return plaid_sync.sync_item(plaid_client, item_id, access_token)


def _parse_sync_full_flag() -> bool:
    body = request.get_json(force=True, silent=True) or {}
    if body.get("full") is True:
        return True
    raw = request.args.get("full", "")
    return str(raw).lower() in ("1", "true", "yes")


@app.route("/api/sync_transactions", methods=["POST"])
def sync_transactions():
    """Pull latest transactions from Plaid for all linked items."""
    full = _parse_sync_full_flag()
    if hasattr(db, "clear_caches"):
        db.clear_caches()
    items = db.iter_items_with_tokens()
    if not items:
        notion_worker = _sync_notion_worker_from_linked_items(
            reset_sync_state=full
        )
        return jsonify(
            {
                "synced": [],
                "message": "No linked accounts",
                "full": full,
                "notion_worker": notion_worker,
            }
        )

    if full:
        db.reset_all_sync_cursors()

    results = []
    for row in items:
        item_id = row["item_id"]
        token = row["access_token"]
        try:
            stats = _sync_one_item(item_id, token)
            results.append({"item_id": item_id, "ok": True, **stats})
        except plaid.ApiException as e:
            err = json.loads(e.body)
            db.set_cursor(item_id, db.get_cursor(item_id), error=json.dumps(err))
            results.append({"item_id": item_id, "ok": False, "error": err})
        except Exception as e:
            db.set_cursor(item_id, db.get_cursor(item_id), error=str(e))
            results.append({"item_id": item_id, "ok": False, "error": str(e)})

    ok_results = [r for r in results if r.get("ok")]
    summary = {
        "items": len(results),
        "ok": len(ok_results),
        "transactions_added": sum(r.get("added", 0) for r in ok_results),
        "transactions_modified": sum(r.get("modified", 0) for r in ok_results),
        "full": full,
    }
    notion_worker = _sync_notion_worker_from_linked_items(
        reset_sync_state=full
    )
    return jsonify(
        {"summary": summary, "details": results, "notion_worker": notion_worker}
    )


@app.route("/api/sync/item", methods=["POST"])
def sync_single_item():
    """Sync one item on demand — called by the website after Plaid Link.

    Fails closed: SYNC_TRIGGER_SECRET must be set, and callers must send it
    in the X-Sync-Secret header (constant-time compare).
    """
    secret = os.getenv("SYNC_TRIGGER_SECRET", "").strip()
    provided = request.headers.get("X-Sync-Secret", "")
    if not secret or not hmac.compare_digest(provided, secret):
        return jsonify({"error": "Unauthorized"}), 401

    if hasattr(db, "clear_caches"):
        db.clear_caches()
    body = request.get_json(force=True, silent=True) or {}
    item_id = body.get("item_id")
    if not item_id:
        return jsonify({"error": "item_id is required"}), 400

    row = db.get_item(item_id)
    if not row:
        return jsonify({"error": "Unknown item"}), 404

    try:
        stats = _sync_one_item(item_id, row["access_token"])
        if hasattr(db, "upsert_accounts"):
            accounts_response = plaid_client.accounts_get(
                AccountsGetRequest(access_token=row["access_token"])
            )
            accounts = accounts_response.to_dict().get("accounts") or []
            db.upsert_accounts(item_id, accounts)
        return jsonify({"ok": True, "item_id": item_id, **stats})
    except plaid.ApiException as e:
        err = json.loads(e.body)
        db.set_cursor(item_id, db.get_cursor(item_id), error=json.dumps(err))
        return jsonify({"ok": False, "item_id": item_id, "error": err}), 502
    except Exception as e:
        db.set_cursor(item_id, db.get_cursor(item_id), error=str(e))
        return jsonify({"ok": False, "item_id": item_id, "error": str(e)}), 500


@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    try:
        all_accounts = []
        for row in db.iter_items_with_tokens():
            item_id = row["item_id"]
            access_token = row["access_token"]
            inst = row.get("institution_name") or "Linked institution"

            accounts_request = AccountsGetRequest(access_token=access_token)
            accounts_response = plaid_client.accounts_get(accounts_request)
            data = accounts_response.to_dict()

            for account in data.get("accounts") or []:
                balances = account.get("balances") or {}
                all_accounts.append(
                    {
                        "item_id": item_id,
                        "institution_name": inst,
                        "account_id": account.get("account_id"),
                        "name": account.get("name"),
                        "official_name": account.get("official_name"),
                        "type": account.get("type"),
                        "subtype": account.get("subtype"),
                        "mask": account.get("mask"),
                        "balance": {
                            "available": balances.get("available"),
                            "current": balances.get("current"),
                            "currency": balances.get("iso_currency_code"),
                        },
                    }
                )

        return jsonify({"accounts": all_accounts})
    except plaid.ApiException as e:
        return jsonify({"error": json.loads(e.body)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/items/<item_id>", methods=["DELETE"])
def disconnect_item(item_id: str):
    row = db.get_item(item_id)
    if not row:
        return jsonify({"error": "Unknown item"}), 404
    try:
        remove_req = ItemRemoveRequest(access_token=row["access_token"])
        plaid_client.item_remove(remove_req)
    except plaid.ApiException as e:
        return jsonify({"error": json.loads(e.body)}), 400
    db.delete_item(item_id)
    return jsonify({"ok": True, "item_id": item_id})


@app.route("/api/transactions", methods=["GET"])
def get_transactions():
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    account_id = request.args.get("account_id")
    since = request.args.get("since")
    until = request.args.get("until")
    q = request.args.get("q")

    rows, total = db.fetch_transactions(
        limit=limit,
        offset=offset,
        account_id=account_id or None,
        since=since or None,
        until=until or None,
        q=q or None,
    )
    return jsonify({"transactions": rows, "total": total, "limit": limit, "offset": offset})


@app.route("/api/transactions/export.csv", methods=["GET"])
def export_transactions_csv():
    """Export filtered transactions for spreadsheet workflows (replaces manual xlsx paste)."""
    account_id = request.args.get("account_id")
    since = request.args.get("since")
    until = request.args.get("until")
    q = request.args.get("q")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "date",
            "amount",
            "currency",
            "name",
            "merchant_name",
            "primary_category",
            "detailed_category",
            "pending",
            "account_id",
            "item_id",
            "transaction_id",
        ]
    )

    offset = 0
    page = 5000
    total_written = 0
    max_rows = 100_000

    while total_written < max_rows:
        rows, _ = db.fetch_transactions(
            limit=page,
            offset=offset,
            account_id=account_id or None,
            since=since or None,
            until=until or None,
            q=q or None,
        )
        if not rows:
            break
        for t in rows:
            writer.writerow(
                [
                    t["date"],
                    t["amount"],
                    t.get("iso_currency_code") or "",
                    t.get("name") or "",
                    t.get("merchant_name") or "",
                    t.get("primary_category") or "",
                    t.get("detailed_category") or "",
                    "yes" if t.get("pending") else "no",
                    t.get("account_id") or "",
                    t.get("item_id") or "",
                    t.get("transaction_id") or "",
                ]
            )
            total_written += 1
            if total_written >= max_rows:
                break
        if len(rows) < page:
            break
        offset += page

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="the-count-transactions.csv"'
        },
    )


@app.route("/api/dashboard/summary", methods=["GET"])
def dashboard_summary():
    """Balances from Plaid, aggregates and recent activity from SQLite."""
    month_start = _month_start()
    month_end = date(
        month_start.year,
        month_start.month,
        monthrange(month_start.year, month_start.month)[1],
    )

    accounts_payload: list[dict] = []
    total_assets = 0.0
    total_liabilities = 0.0

    for row in db.iter_items_with_tokens():
        access_token = row["access_token"]
        inst = row.get("institution_name") or "Institution"
        try:
            accounts_request = AccountsGetRequest(access_token=access_token)
            accounts_response = plaid_client.accounts_get(accounts_request)
            for account in accounts_response.to_dict().get("accounts") or []:
                b = account.get("balances") or {}
                current = b.get("current")
                if current is None:
                    continue
                cur_f = float(current)
                t = (account.get("type") or "").lower()

                if t in ("depository", "investment"):
                    total_assets += cur_f
                elif t == "credit":
                    total_liabilities += cur_f
                elif t == "loan":
                    total_liabilities += abs(cur_f)
                else:
                    total_assets += cur_f

                accounts_payload.append(
                    {
                        "item_id": row["item_id"],
                        "institution_name": inst,
                        "account_id": account.get("account_id"),
                        "name": account.get("name"),
                        "type": t,
                        "subtype": account.get("subtype"),
                        "mask": account.get("mask"),
                        "current": current,
                        "available": b.get("available"),
                        "currency": b.get("iso_currency_code"),
                    }
                )
        except plaid.ApiException:
            accounts_payload.append(
                {
                    "item_id": row["item_id"],
                    "institution_name": inst,
                    "error": "Could not load accounts (re-link or check credentials)",
                }
            )

    counts = db.transaction_counts()
    flows = db.spending_inflow_totals(
        since_date=month_start.isoformat(),
        until_date=month_end.isoformat(),
    )
    categories = db.sum_by_category(
        since=month_start.isoformat(), outflows_only=True
    )
    recent, _ = db.fetch_transactions(limit=12, offset=0)

    last30 = date.today() - timedelta(days=30)
    flows_30 = db.spending_inflow_totals(since_date=last30.isoformat())

    return jsonify(
        {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "month": {
                "label": month_start.strftime("%B %Y"),
                "start": month_start.isoformat(),
                "end": month_end.isoformat(),
            },
            "balances": {
                "assets": round(total_assets, 2),
                "liabilities": round(total_liabilities, 2),
                "net_simplified": round(total_assets - total_liabilities, 2),
            },
            "linked_items": [
                {
                    "item_id": i["item_id"],
                    "institution_name": i.get("institution_name"),
                    "last_sync_at": i.get("last_sync_at"),
                    "last_error": i.get("last_error"),
                }
                for i in db.list_items()
            ],
            "accounts": accounts_payload,
            "activity_month": {
                "spend_outflow": round(flows["outflow"], 2),
                "income_inflow": round(flows["inflow"], 2),
                "net_cash_flow": round(flows["inflow"] - flows["outflow"], 2),
            },
            "activity_last_30_days": {
                "spend_outflow": round(flows_30["outflow"], 2),
                "income_inflow": round(flows_30["inflow"], 2),
            },
            "categories_month": categories[:12],
            "transaction_store": counts,
            "recent_transactions": recent,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
