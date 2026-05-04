"""
The Count - Plaid integration and financial dashboard backend.
"""

from __future__ import annotations

import csv
import io
import json
import os
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

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
from plaid.model.transactions_sync_request import TransactionsSyncRequest

import db
import goals as goal_eval
import plaid_sync
import tagging

load_dotenv()

app = Flask(__name__)

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_SECRET = os.getenv("PLAID_SECRET")
PLAID_ENV = os.getenv("PLAID_ENVIRONMENT", "sandbox")

PLAID_ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}
if hasattr(plaid.Environment, "Development"):
    PLAID_ENVIRONMENTS["development"] = plaid.Environment.Development

_plaid_host = PLAID_ENVIRONMENTS.get(PLAID_ENV) or plaid.Environment.Sandbox
configuration = plaid.Configuration(
    host=_plaid_host,
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
        "plaidVersion": "2020-09-14",
    },
)
api_client = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)

db.init_db()
tagging.seed_default_tags_and_rules()
try:
    counts = db.tag_summary_counts()
    if counts.get("total", 0) > 0 and counts.get("tagged", 0) == 0:
        tagging.apply_rules(None)
except Exception:
    pass


def _month_start(d: date | None = None) -> date:
    if d is None:
        d = date.today()
    return date(d.year, d.month, 1)


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
    cursor = db.get_cursor(item_id)
    total_stats = {"added": 0, "modified": 0, "removed": 0, "pages": 0}
    touched_ids: list[str] = []

    while True:
        if cursor:
            sync_req = TransactionsSyncRequest(
                access_token=access_token, cursor=cursor
            )
        else:
            sync_req = TransactionsSyncRequest(access_token=access_token)

        response = plaid_client.transactions_sync(sync_req)
        total_stats["pages"] += 1
        page = plaid_sync.apply_sync_response(item_id, response)
        total_stats["added"] += page["added"]
        total_stats["modified"] += page["modified"]
        total_stats["removed"] += page["removed"]

        for tx in (getattr(response, "added", None) or []):
            tid = getattr(tx, "transaction_id", None)
            if tid:
                touched_ids.append(tid)
        for tx in (getattr(response, "modified", None) or []):
            tid = getattr(tx, "transaction_id", None)
            if tid:
                touched_ids.append(tid)

        cursor = response.next_cursor
        if not response.has_more:
            break

    db.set_cursor(item_id, cursor, error=None)

    if touched_ids:
        try:
            tag_stats = tagging.apply_rules(touched_ids)
            total_stats["tagged"] = tag_stats.get("tagged", 0)
        except Exception as e:
            app.logger.warning(f"Tagging failed for {item_id}: {e}")

    return total_stats


@app.route("/api/sync_transactions", methods=["POST"])
def sync_transactions():
    """Pull latest transactions from Plaid for all linked items."""
    items = db.iter_items_with_tokens()
    if not items:
        return jsonify({"synced": [], "message": "No linked accounts"})

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

    summary = {
        "items": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "transactions_added": sum(r.get("added", 0) for r in results if r.get("ok")),
    }
    return jsonify({"summary": summary, "details": results})


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


def _parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


@app.route("/api/transactions", methods=["GET"])
def get_transactions():
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    account_id = request.args.get("account_id")
    since = request.args.get("since")
    until = request.args.get("until")
    q = request.args.get("q")
    tag_id = _parse_int(request.args.get("tag_id"))
    untagged_only = _parse_bool(request.args.get("untagged_only"))

    rows, total = db.fetch_transactions(
        limit=limit,
        offset=offset,
        account_id=account_id or None,
        since=since or None,
        until=until or None,
        q=q or None,
        tag_id=tag_id,
        untagged_only=untagged_only,
    )
    return jsonify({"transactions": rows, "total": total, "limit": limit, "offset": offset})


@app.route("/api/transactions/export.csv", methods=["GET"])
def export_transactions_csv():
    """Export filtered transactions for spreadsheet workflows (replaces manual xlsx paste)."""
    account_id = request.args.get("account_id")
    since = request.args.get("since")
    until = request.args.get("until")
    q = request.args.get("q")
    tag_id = _parse_int(request.args.get("tag_id"))
    untagged_only = _parse_bool(request.args.get("untagged_only"))

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
            "tag",
            "tag_source",
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
            tag_id=tag_id,
            untagged_only=untagged_only,
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
                    t.get("tag_label") or t.get("tag_key") or "",
                    t.get("tag_source") or "",
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


# --- Tags ---------------------------------------------------------------------


@app.route("/api/tags", methods=["GET"])
def api_list_tags():
    return jsonify({"tags": db.list_tags(), "summary": db.tag_summary_counts()})


def _slugify(s: str) -> str:
    out = []
    for ch in s.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    return "".join(out).strip("_") or "tag"


@app.route("/api/tags", methods=["POST"])
def api_create_tag():
    body = request.get_json(force=True, silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label is required"}), 400
    key = (body.get("key") or _slugify(label)).strip()
    kind = body.get("kind") or "spend"
    if kind not in {"spend", "save", "income"}:
        return jsonify({"error": "kind must be one of spend|save|income"}), 400
    color = body.get("color")
    tag_id = db.insert_tag(key=key, label=label, kind=kind, color=color)
    return jsonify({"tag": db.get_tag(tag_id)}), 201


@app.route("/api/tags/<int:tag_id>", methods=["PATCH"])
def api_update_tag(tag_id: int):
    if db.get_tag(tag_id) is None:
        return jsonify({"error": "tag not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    db.update_tag(
        tag_id,
        label=body.get("label"),
        kind=body.get("kind"),
        color=body.get("color"),
    )
    return jsonify({"tag": db.get_tag(tag_id)})


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id: int):
    if db.get_tag(tag_id) is None:
        return jsonify({"error": "tag not found"}), 404
    db.delete_tag(tag_id)
    return jsonify({"ok": True, "tag_id": tag_id})


# --- Tag rules ----------------------------------------------------------------


@app.route("/api/tag_rules", methods=["GET"])
def api_list_tag_rules():
    tag_id = _parse_int(request.args.get("tag_id"))
    return jsonify({"rules": db.list_tag_rules(tag_id=tag_id)})


@app.route("/api/tag_rules", methods=["POST"])
def api_create_tag_rule():
    body = request.get_json(force=True, silent=True) or {}
    tag_id = _parse_int(str(body.get("tag_id")))
    if tag_id is None or db.get_tag(tag_id) is None:
        return jsonify({"error": "valid tag_id required"}), 400
    field = body.get("match_field")
    op = body.get("match_op")
    value = body.get("match_value") or ""
    if field not in tagging.VALID_FIELDS:
        return jsonify({"error": f"match_field must be one of {sorted(tagging.VALID_FIELDS)}"}), 400
    if op not in tagging.VALID_OPS:
        return jsonify({"error": f"match_op must be one of {sorted(tagging.VALID_OPS)}"}), 400
    rule_id = db.insert_tag_rule(
        tag_id=tag_id,
        match_field=field,
        match_op=op,
        match_value=str(value),
        priority=int(body.get("priority") or 100),
        min_amount=body.get("min_amount"),
        max_amount=body.get("max_amount"),
        enabled=bool(body.get("enabled", True)),
    )
    if bool(body.get("apply_now", True)):
        try:
            tagging.apply_rules(None)
        except Exception as e:
            app.logger.warning(f"apply_rules after rule create failed: {e}")
    return jsonify({"rule": db.get_tag_rule(rule_id)}), 201


@app.route("/api/tag_rules/<int:rule_id>", methods=["PATCH"])
def api_update_tag_rule(rule_id: int):
    if db.get_tag_rule(rule_id) is None:
        return jsonify({"error": "rule not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    field = body.get("match_field")
    if field is not None and field not in tagging.VALID_FIELDS:
        return jsonify({"error": "invalid match_field"}), 400
    op = body.get("match_op")
    if op is not None and op not in tagging.VALID_OPS:
        return jsonify({"error": "invalid match_op"}), 400
    db.update_tag_rule(
        rule_id,
        **{k: v for k, v in body.items() if k in {
            "tag_id", "priority", "match_field", "match_op",
            "match_value", "min_amount", "max_amount", "enabled",
        }},
    )
    if bool(body.get("apply_now", False)):
        try:
            tagging.apply_rules(None)
        except Exception:
            pass
    return jsonify({"rule": db.get_tag_rule(rule_id)})


@app.route("/api/tag_rules/<int:rule_id>", methods=["DELETE"])
def api_delete_tag_rule(rule_id: int):
    if db.get_tag_rule(rule_id) is None:
        return jsonify({"error": "rule not found"}), 404
    db.delete_tag_rule(rule_id)
    return jsonify({"ok": True, "rule_id": rule_id})


@app.route("/api/tag_rules/reapply", methods=["POST"])
def api_reapply_rules():
    body = request.get_json(force=True, silent=True) or {}
    transaction_ids = body.get("transaction_ids") or None
    stats = tagging.apply_rules(transaction_ids)
    return jsonify({"stats": stats, "summary": db.tag_summary_counts()})


@app.route("/api/transactions/<transaction_id>/tag", methods=["POST", "DELETE"])
def api_set_transaction_tag(transaction_id: str):
    if request.method == "DELETE":
        tagging.clear_tag(transaction_id)
        return jsonify({"ok": True, "transaction_id": transaction_id, "tag": None})

    body = request.get_json(force=True, silent=True) or {}
    tag_id = _parse_int(str(body.get("tag_id")))
    if tag_id is None:
        tag_key = body.get("tag_key")
        if tag_key:
            tag = db.get_tag_by_key(str(tag_key))
            if tag:
                tag_id = int(tag["id"])
    if tag_id is None or db.get_tag(tag_id) is None:
        return jsonify({"error": "valid tag_id or tag_key required"}), 400

    tagging.set_manual_tag(transaction_id, tag_id)

    create_rule = bool(body.get("create_rule"))
    rule_field = body.get("rule_field") or "merchant_name"
    rule_op = body.get("rule_op") or "equals"
    rule_value = body.get("rule_value")
    rule_id = None
    if create_rule and rule_value:
        if rule_field in tagging.VALID_FIELDS and rule_op in tagging.VALID_OPS:
            rule_id = db.insert_tag_rule(
                tag_id=tag_id,
                match_field=rule_field,
                match_op=rule_op,
                match_value=str(rule_value),
                priority=int(body.get("rule_priority") or 50),
                enabled=True,
            )
            try:
                tagging.apply_rules(None)
            except Exception:
                pass

    return jsonify({
        "ok": True,
        "transaction_id": transaction_id,
        "tag": db.get_tag(tag_id),
        "created_rule_id": rule_id,
    })


# --- Goals --------------------------------------------------------------------


_GOAL_KIND_REQUIREMENTS = {
    "spend_cap": {"target_amount": True, "tag": True},
    "spend_floor": {"target_amount": True, "tag": True},
    "frequency": {"target_count": True, "tag": True},
    "savings_target": {"target_amount": True, "tag": False},
    "streak": {"target_amount": True, "tag": True},
}


def _validate_goal_payload(body: dict, *, partial: bool = False) -> tuple[bool, str | None]:
    if not partial:
        if not body.get("name"):
            return False, "name is required"
        kind = body.get("kind")
        if kind not in goal_eval.VALID_KINDS:
            return False, f"kind must be one of {sorted(goal_eval.VALID_KINDS)}"
        period = body.get("period") or "month"
        if period not in goal_eval.VALID_PERIODS:
            return False, f"period must be one of {sorted(goal_eval.VALID_PERIODS)}"
        req = _GOAL_KIND_REQUIREMENTS[kind]
        if req["tag"] and not body.get("tag_id"):
            return False, f"goal kind '{kind}' requires tag_id"
        if req.get("target_amount") and body.get("target_amount") in (None, ""):
            return False, f"goal kind '{kind}' requires target_amount"
        if req.get("target_count") and body.get("target_count") in (None, ""):
            return False, f"goal kind '{kind}' requires target_count"
    else:
        if "kind" in body and body["kind"] not in goal_eval.VALID_KINDS:
            return False, f"kind must be one of {sorted(goal_eval.VALID_KINDS)}"
        if "period" in body and body["period"] not in goal_eval.VALID_PERIODS:
            return False, f"period must be one of {sorted(goal_eval.VALID_PERIODS)}"
    return True, None


@app.route("/api/goals", methods=["GET"])
def api_list_goals():
    active_only = _parse_bool(request.args.get("active_only"))
    return jsonify({"goals": db.list_goals(active_only=active_only)})


@app.route("/api/goals", methods=["POST"])
def api_create_goal():
    body = request.get_json(force=True, silent=True) or {}
    ok, err = _validate_goal_payload(body, partial=False)
    if not ok:
        return jsonify({"error": err}), 400
    tag_id = _parse_int(str(body.get("tag_id"))) if body.get("tag_id") is not None else None
    if tag_id is not None and db.get_tag(tag_id) is None:
        return jsonify({"error": "tag_id not found"}), 400

    goal_id = db.insert_goal(
        name=str(body["name"]),
        kind=str(body["kind"]),
        tag_id=tag_id,
        account_id=body.get("account_id"),
        period=str(body.get("period") or "month"),
        period_start=body.get("period_start"),
        target_amount=body.get("target_amount"),
        target_count=body.get("target_count"),
        currency=str(body.get("currency") or "USD"),
        rollover=bool(body.get("rollover")),
        active=bool(body.get("active", True)),
        notes=body.get("notes"),
    )
    return jsonify({"goal": db.get_goal(goal_id)}), 201


@app.route("/api/goals/<int:goal_id>", methods=["GET"])
def api_get_goal(goal_id: int):
    g = db.get_goal(goal_id)
    if not g:
        return jsonify({"error": "goal not found"}), 404
    return jsonify({"goal": g})


@app.route("/api/goals/<int:goal_id>", methods=["PATCH"])
def api_update_goal(goal_id: int):
    g = db.get_goal(goal_id)
    if not g:
        return jsonify({"error": "goal not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    ok, err = _validate_goal_payload(body, partial=True)
    if not ok:
        return jsonify({"error": err}), 400
    db.update_goal(
        goal_id,
        **{k: v for k, v in body.items() if k in {
            "name", "kind", "tag_id", "account_id", "period", "period_start",
            "target_amount", "target_count", "currency", "rollover", "active", "notes",
        }},
    )
    return jsonify({"goal": db.get_goal(goal_id)})


@app.route("/api/goals/<int:goal_id>", methods=["DELETE"])
def api_delete_goal(goal_id: int):
    if not db.get_goal(goal_id):
        return jsonify({"error": "goal not found"}), 404
    db.delete_goal(goal_id)
    return jsonify({"ok": True, "goal_id": goal_id})


@app.route("/api/goals/<int:goal_id>/events", methods=["GET", "POST"])
def api_goal_events(goal_id: int):
    g = db.get_goal(goal_id)
    if not g:
        return jsonify({"error": "goal not found"}), 404
    if request.method == "GET":
        return jsonify({"events": db.list_goal_events(goal_id)})
    body = request.get_json(force=True, silent=True) or {}
    amount = body.get("amount")
    if amount in (None, ""):
        return jsonify({"error": "amount is required"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be numeric"}), 400
    event_date = body.get("event_date") or date.today().isoformat()
    event_id = db.insert_goal_event(
        goal_id=goal_id,
        event_date=str(event_date),
        amount=amount,
        kind=str(body.get("kind") or "contribution"),
        note=body.get("note"),
    )
    return jsonify({"event_id": event_id, "events": db.list_goal_events(goal_id)}), 201


@app.route("/api/goals/progress", methods=["GET"])
def api_goals_progress():
    period_arg = request.args.get("period") or "current"
    offset = 0
    if period_arg == "previous":
        offset = -1
    elif period_arg == "next":
        offset = 1
    elif period_arg.startswith("offset:"):
        try:
            offset = int(period_arg.split(":", 1)[1])
        except ValueError:
            offset = 0

    active_only = _parse_bool(request.args.get("active_only") or "true")
    progresses = goal_eval.evaluate_all(offset=offset, active_only=active_only)
    return jsonify({
        "progresses": progresses,
        "summary": goal_eval.summarize_statuses(progresses),
        "offset": offset,
    })


@app.route("/api/goals/<int:goal_id>/progress", methods=["GET"])
def api_goal_progress(goal_id: int):
    g = db.get_goal(goal_id)
    if not g:
        return jsonify({"error": "goal not found"}), 404
    period_arg = request.args.get("period") or "current"
    offset = 0
    if period_arg == "previous":
        offset = -1
    elif period_arg == "next":
        offset = 1
    elif period_arg.startswith("offset:"):
        try:
            offset = int(period_arg.split(":", 1)[1])
        except ValueError:
            offset = 0
    progress = goal_eval.evaluate_goal(g, offset=offset)
    burn = goal_eval.daily_burn_for_goal(g, offset=offset)
    return jsonify({"progress": progress, "daily_burn": burn})


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

    progresses = goal_eval.evaluate_all(active_only=True)
    goals_summary = goal_eval.summarize_statuses(progresses)

    def _goal_priority(p: dict) -> tuple[int, float]:
        order = {"over": 0, "at_risk": 1, "on_track": 2, "met": 3, "unconfigured": 4}
        return (order.get(p.get("status") or "unconfigured", 5),
                -(p.get("pace_ratio") or 0))

    top_goals = sorted(progresses, key=_goal_priority)[:4]
    tag_summary = db.tag_summary_counts()

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
            "goals_summary": goals_summary,
            "top_goals": top_goals,
            "tag_summary": tag_summary,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
