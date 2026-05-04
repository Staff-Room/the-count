"""
Goal evaluator. Computes period-aware progress for each goal kind:
spend_cap, spend_floor, frequency, savings_target, streak.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Optional

import db


VALID_KINDS = {"spend_cap", "spend_floor", "frequency", "savings_target", "streak"}
VALID_PERIODS = {"week", "month", "quarter", "year", "custom"}


def _today() -> date:
    return date.today()


def _to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def period_window(
    period: str,
    *,
    today: Optional[date] = None,
    period_start: Optional[str] = None,
    offset: int = 0,
) -> tuple[date, date, str]:
    """Return (start, end, label) for the goal's period.

    offset: 0 = current period, -1 = previous, +1 = next.
    """
    t = today or _today()

    if period == "week":
        # ISO Monday start
        monday = t - timedelta(days=t.weekday())
        start = monday + timedelta(days=offset * 7)
        end = start + timedelta(days=6)
        label = f"Week of {start.strftime('%b %d')}"
        return start, end, label

    if period == "month":
        y, m = t.year, t.month + offset
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
        return start, end, start.strftime("%B %Y")

    if period == "quarter":
        q_index = (t.month - 1) // 3
        target_q = q_index + offset
        y = t.year + (target_q // 4)
        q_in_year = target_q % 4
        if q_in_year < 0:
            q_in_year += 4
            y -= 1
        start_month = q_in_year * 3 + 1
        start = date(y, start_month, 1)
        end_month = start_month + 2
        end = date(y, end_month, monthrange(y, end_month)[1])
        return start, end, f"Q{q_in_year + 1} {y}"

    if period == "year":
        y = t.year + offset
        start = date(y, 1, 1)
        end = date(y, 12, 31)
        return start, end, str(y)

    if period == "custom":
        ps = _to_date(period_start) or date(t.year, t.month, 1)
        # Default custom window: 30-day rolling from period_start, sliding by offset windows.
        window = timedelta(days=30)
        start = ps + offset * window
        end = start + window - timedelta(days=1)
        label = f"{start.isoformat()} – {end.isoformat()}"
        return start, end, label

    # Fallback: month
    return period_window("month", today=t, offset=offset)


def _days_elapsed_inclusive(start: date, end: date, today: date) -> int:
    if today < start:
        return 0
    capped = min(today, end)
    return (capped - start).days + 1


def _days_in_period(start: date, end: date) -> int:
    return (end - start).days + 1


def _classify_progress(
    actual: float,
    target: Optional[float],
    *,
    pace_ratio: Optional[float] = None,
    inverted: bool = False,
) -> str:
    """Return on_track | at_risk | over | met | unconfigured."""
    if target is None or target == 0:
        return "unconfigured"

    if inverted:
        # spend_floor: higher actual is better
        if actual >= target:
            return "met"
        if pace_ratio is not None and pace_ratio < 0.85:
            return "at_risk"
        return "on_track"

    if actual >= target:
        return "over"
    if actual >= target * 0.95:
        return "at_risk"
    if pace_ratio is not None and pace_ratio > 1.10:
        return "at_risk"
    return "on_track"


def _evaluate_spend_cap(
    goal: dict[str, Any],
    *,
    today: date,
    offset: int,
    inverted: bool = False,
) -> dict[str, Any]:
    start, end, label = period_window(
        goal["period"], today=today, period_start=goal.get("period_start"), offset=offset
    )
    target = goal.get("target_amount")
    tag_id = goal.get("tag_id")
    if not tag_id:
        return _empty_progress(goal, label, start, end, target=target,
                               status="unconfigured", note="No tag assigned")

    summary = db.sum_tagged_for_period(
        tag_id=int(tag_id),
        since=start.isoformat(),
        until=end.isoformat(),
        account_id=goal.get("account_id"),
        direction="outflow",
        include_pending=True,
        exclude_transfers=True,
        currency=goal.get("currency"),
    )

    actual = round(summary["signed_total"], 2)
    if actual < 0 and not inverted:
        # Net of refunds
        actual = max(0.0, actual)
    actual = abs(actual) if not inverted else summary["signed_total"]
    actual_amount = max(0.0, summary["signed_total"]) if not inverted else summary["signed_total"]

    days_elapsed = _days_elapsed_inclusive(start, end, today)
    days_total = _days_in_period(start, end)
    expected = (target * (days_elapsed / days_total)) if (target and days_total) else None
    pace_ratio = (
        (actual_amount / expected) if (expected is not None and expected > 0) else None
    )
    projected = (
        round(actual_amount * (days_total / max(days_elapsed, 1)), 2)
        if days_elapsed > 0 else None
    )

    status = _classify_progress(actual_amount, target, pace_ratio=pace_ratio, inverted=inverted)

    supporting = db.list_supporting_transactions(
        tag_id=int(tag_id),
        since=start.isoformat(),
        until=end.isoformat(),
        account_id=goal.get("account_id"),
        limit=20,
    )

    remaining = None
    if target is not None:
        remaining = round(target - actual_amount, 2)

    percent = None
    if target and target > 0:
        percent = round((actual_amount / target) * 100, 1)

    return {
        "id": goal["id"],
        "name": goal["name"],
        "kind": goal["kind"],
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "target": target,
        "actual": round(actual_amount, 2),
        "remaining": remaining,
        "percent": percent,
        "pace_ratio": round(pace_ratio, 3) if pace_ratio is not None else None,
        "expected_to_date": round(expected, 2) if expected is not None else None,
        "projected_end_of_period": projected,
        "pending_amount": round(summary["pending_amount"], 2),
        "off_currency_count": summary["off_currency_count"],
        "row_count": summary["row_count"],
        "status": status,
        "tag_id": goal.get("tag_id"),
        "tag_label": goal.get("tag_label"),
        "tag_color": goal.get("tag_color"),
        "supporting_transactions": supporting,
    }


def _empty_progress(
    goal: dict[str, Any],
    label: str,
    start: date,
    end: date,
    *,
    target: Optional[float] = None,
    status: str = "unconfigured",
    note: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": goal["id"],
        "name": goal["name"],
        "kind": goal["kind"],
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "target": target,
        "actual": 0,
        "remaining": target,
        "percent": 0,
        "pace_ratio": None,
        "expected_to_date": None,
        "projected_end_of_period": None,
        "pending_amount": 0,
        "off_currency_count": 0,
        "row_count": 0,
        "status": status,
        "tag_id": goal.get("tag_id"),
        "tag_label": goal.get("tag_label"),
        "tag_color": goal.get("tag_color"),
        "supporting_transactions": [],
        "note": note,
    }


def _evaluate_frequency(
    goal: dict[str, Any], *, today: date, offset: int
) -> dict[str, Any]:
    start, end, label = period_window(
        goal["period"], today=today, period_start=goal.get("period_start"), offset=offset
    )
    target_count = goal.get("target_count")
    tag_id = goal.get("tag_id")
    if not tag_id:
        return _empty_progress(goal, label, start, end, target=target_count,
                               status="unconfigured", note="No tag assigned")

    summary = db.sum_tagged_for_period(
        tag_id=int(tag_id),
        since=start.isoformat(),
        until=end.isoformat(),
        account_id=goal.get("account_id"),
        exclude_transfers=True,
    )
    supporting = db.list_supporting_transactions(
        tag_id=int(tag_id),
        since=start.isoformat(),
        until=end.isoformat(),
        account_id=goal.get("account_id"),
        limit=50,
    )

    actual = summary["distinct_days"]
    days_elapsed = _days_elapsed_inclusive(start, end, today)
    days_total = _days_in_period(start, end)
    expected = (
        target_count * (days_elapsed / days_total) if target_count and days_total else None
    )
    pace_ratio = (actual / expected) if (expected and expected > 0) else None
    projected = (
        round(actual * (days_total / max(days_elapsed, 1)), 1)
        if days_elapsed > 0 else None
    )

    status = _classify_progress(actual, target_count, pace_ratio=pace_ratio)

    percent = None
    if target_count and target_count > 0:
        percent = round((actual / target_count) * 100, 1)
    remaining = None
    if target_count is not None:
        remaining = max(0, target_count - actual)

    return {
        "id": goal["id"],
        "name": goal["name"],
        "kind": goal["kind"],
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "target": target_count,
        "actual": actual,
        "remaining": remaining,
        "percent": percent,
        "pace_ratio": round(pace_ratio, 3) if pace_ratio is not None else None,
        "expected_to_date": round(expected, 2) if expected is not None else None,
        "projected_end_of_period": projected,
        "pending_amount": 0,
        "off_currency_count": 0,
        "row_count": summary["row_count"],
        "status": status,
        "tag_id": goal.get("tag_id"),
        "tag_label": goal.get("tag_label"),
        "tag_color": goal.get("tag_color"),
        "supporting_transactions": supporting,
    }


def _evaluate_savings_target(
    goal: dict[str, Any], *, today: date, offset: int
) -> dict[str, Any]:
    start, end, label = period_window(
        goal["period"], today=today, period_start=goal.get("period_start"), offset=offset
    )
    target = goal.get("target_amount")
    tag_id = goal.get("tag_id")

    actual = 0.0
    pending_amount = 0.0
    row_count = 0
    if tag_id:
        summary = db.sum_tagged_for_period(
            tag_id=int(tag_id),
            since=start.isoformat(),
            until=end.isoformat(),
            account_id=goal.get("account_id"),
            direction="inflow",
            include_pending=True,
            exclude_transfers=False,
            currency=goal.get("currency"),
        )
        actual += summary["magnitude_total"]
        pending_amount = summary["pending_amount"]
        row_count = summary["row_count"]

    events = db.list_goal_events(
        goal_id=goal["id"], since=start.isoformat(), until=end.isoformat()
    )
    events_total = sum(float(e["amount"]) for e in events)
    actual += events_total

    days_elapsed = _days_elapsed_inclusive(start, end, today)
    days_total = _days_in_period(start, end)
    expected = (target * (days_elapsed / days_total)) if (target and days_total) else None
    pace_ratio = (actual / expected) if (expected and expected > 0) else None
    projected = (
        round(actual * (days_total / max(days_elapsed, 1)), 2)
        if days_elapsed > 0 else None
    )

    status = _classify_progress(actual, target, pace_ratio=pace_ratio, inverted=True)

    percent = None
    if target and target > 0:
        percent = round((actual / target) * 100, 1)
    remaining = None
    if target is not None:
        remaining = round(target - actual, 2)

    supporting = []
    if tag_id:
        supporting = db.list_supporting_transactions(
            tag_id=int(tag_id),
            since=start.isoformat(),
            until=end.isoformat(),
            account_id=goal.get("account_id"),
            limit=20,
            exclude_transfers=False,
        )

    return {
        "id": goal["id"],
        "name": goal["name"],
        "kind": goal["kind"],
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "target": target,
        "actual": round(actual, 2),
        "remaining": remaining,
        "percent": percent,
        "pace_ratio": round(pace_ratio, 3) if pace_ratio is not None else None,
        "expected_to_date": round(expected, 2) if expected is not None else None,
        "projected_end_of_period": projected,
        "pending_amount": round(pending_amount, 2),
        "off_currency_count": 0,
        "row_count": row_count,
        "status": status,
        "events_total": round(events_total, 2),
        "events_count": len(events),
        "tag_id": goal.get("tag_id"),
        "tag_label": goal.get("tag_label"),
        "tag_color": goal.get("tag_color"),
        "supporting_transactions": supporting,
    }


def _evaluate_streak(
    goal: dict[str, Any], *, today: date, offset: int
) -> dict[str, Any]:
    """Streak: count consecutive prior periods that met an underlying spend_cap-like rule.

    The streak goal reuses target_amount as the per-period cap and target_count (optional)
    as the streak length aspiration.
    """
    start, end, label = period_window(
        goal["period"], today=today, period_start=goal.get("period_start"), offset=offset
    )
    cap = goal.get("target_amount")
    target_streak = goal.get("target_count") or 4

    if not goal.get("tag_id") or cap is None:
        return _empty_progress(
            goal, label, start, end, target=target_streak,
            status="unconfigured", note="Streak needs a tag and a per-period cap (target_amount)",
        )

    counts = db.transaction_counts()
    earliest = _to_date(counts.get("min_date") if counts else None)

    streak = 0
    history: list[dict[str, Any]] = []
    for i in range(0, 26):
        s, e, lbl = period_window(
            goal["period"],
            today=today,
            period_start=goal.get("period_start"),
            offset=offset - i,
        )
        if e > today:
            history.append({"period": lbl, "actual": None, "met": None, "in_progress": True})
            continue
        if earliest is not None and e < earliest:
            history.append({
                "period": lbl,
                "actual": None,
                "met": None,
                "in_progress": False,
                "no_data": True,
            })
            break
        summary = db.sum_tagged_for_period(
            tag_id=int(goal["tag_id"]),
            since=s.isoformat(),
            until=e.isoformat(),
            account_id=goal.get("account_id"),
            direction="outflow",
            include_pending=True,
            exclude_transfers=True,
            currency=goal.get("currency"),
        )
        actual = max(0.0, summary["signed_total"])
        has_data = summary["row_count"] > 0
        met = has_data and actual <= float(cap)
        history.append({
            "period": lbl,
            "actual": round(actual, 2),
            "met": met,
            "in_progress": False,
            "no_data": not has_data,
        })
        if met:
            streak += 1
        else:
            break

    pace_ratio = streak / target_streak if target_streak else None
    status = "met" if streak >= target_streak else ("on_track" if streak > 0 else "at_risk")

    return {
        "id": goal["id"],
        "name": goal["name"],
        "kind": goal["kind"],
        "period": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "target": target_streak,
        "actual": streak,
        "remaining": max(0, target_streak - streak) if target_streak else None,
        "percent": round(min(100.0, (streak / target_streak) * 100), 1) if target_streak else None,
        "pace_ratio": pace_ratio,
        "expected_to_date": None,
        "projected_end_of_period": None,
        "pending_amount": 0,
        "off_currency_count": 0,
        "row_count": 0,
        "status": status,
        "cap_per_period": cap,
        "history": history[:8],
        "tag_id": goal.get("tag_id"),
        "tag_label": goal.get("tag_label"),
        "tag_color": goal.get("tag_color"),
        "supporting_transactions": [],
    }


def evaluate_goal(
    goal: dict[str, Any], *, today: Optional[date] = None, offset: int = 0
) -> dict[str, Any]:
    t = today or _today()
    kind = goal.get("kind")

    if kind == "spend_cap":
        return _evaluate_spend_cap(goal, today=t, offset=offset, inverted=False)
    if kind == "spend_floor":
        return _evaluate_spend_cap(goal, today=t, offset=offset, inverted=True)
    if kind == "frequency":
        return _evaluate_frequency(goal, today=t, offset=offset)
    if kind == "savings_target":
        return _evaluate_savings_target(goal, today=t, offset=offset)
    if kind == "streak":
        return _evaluate_streak(goal, today=t, offset=offset)

    start, end, label = period_window(
        goal.get("period") or "month",
        today=t,
        period_start=goal.get("period_start"),
        offset=offset,
    )
    return _empty_progress(
        goal, label, start, end, target=goal.get("target_amount"),
        status="unconfigured", note=f"Unknown goal kind: {kind}",
    )


def evaluate_all(
    *, today: Optional[date] = None, offset: int = 0, active_only: bool = True
) -> list[dict[str, Any]]:
    return [
        evaluate_goal(g, today=today, offset=offset)
        for g in db.list_goals(active_only=active_only)
    ]


def summarize_statuses(progresses: list[dict[str, Any]]) -> dict[str, int]:
    out = {"on_track": 0, "at_risk": 0, "over": 0, "met": 0, "unconfigured": 0}
    for p in progresses:
        s = p.get("status") or "unconfigured"
        out[s] = out.get(s, 0) + 1
    return out


def daily_burn_for_goal(
    goal: dict[str, Any], *, today: Optional[date] = None, offset: int = 0
) -> list[dict[str, Any]]:
    """For sparkline rendering: daily totals for the goal's tag in its period."""
    t = today or _today()
    if not goal.get("tag_id"):
        return []
    start, end, _label = period_window(
        goal.get("period") or "month",
        today=t,
        period_start=goal.get("period_start"),
        offset=offset,
    )
    return db.daily_tagged_totals(
        tag_id=int(goal["tag_id"]),
        since=start.isoformat(),
        until=end.isoformat(),
        account_id=goal.get("account_id"),
        exclude_transfers=True,
    )
