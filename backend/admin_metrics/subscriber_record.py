"""Paid people with renewal still on, reconstructed from provider versions.

A person counts once even with overlapping subscriptions. Cancellation removes
that contract when requested, not when access expires. Trials and failed
payments do not count. Reading this report never contacts or changes Paddle.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from admin_metrics import db, defs, ranges


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def renews(row):
    return (row.get("status") == "active" and not row.get("scheduled_action")
            and bool(row.get("next_billed_at")))


def cancellation_times(rows):
    """Latest continuous cancellation: request and effective end, separately.

    Only a provider event establishes the request time. A later GET while
    cancellation is pending must not replace that time with the sync time.
    Resuming clears the prior request; a new cancellation starts a new one.
    """
    requested = ends = None
    pending = False
    previous = None
    for row in sorted(rows, key=lambda r: utc(r["provider_updated_at"])):
        scheduled = row.get("scheduled_action") == "cancel"
        ended = row.get("status") == "canceled"
        if scheduled:
            if not pending:
                requested = utc(row["provider_updated_at"]) if row.get("event_id") else None
            pending = True
            ends = row.get("scheduled_effective_at") or row.get("period_ends_at")
        elif ended:
            if not pending:
                # An observed transition into an immediate cancellation.
                requested = (utc(row["provider_updated_at"])
                             if row.get("event_id") and previous is not None else None)
            pending = True
            ends = row.get("canceled_at") or ends
        else:
            requested = ends = None
            pending = False
        previous = row
    return {"cancelled_at": defs.iso(utc(requested)),
            "plan_ends_at": defs.iso(utc(ends))}


def build_record(contracts, history, now=None):
    now = utc(now) or datetime.now(timezone.utc)
    by_id = defaultdict(list)
    for row in history:
        if utc(row["provider_updated_at"]) <= now:
            by_id[row["subscription_id"]].append(row)
    changes = defaultdict(list)
    missing = 0
    for contract in contracts:
        sid, uid = contract["subscription_id"], contract["user_id"]
        paid_at = utc(contract["first_paid_at"])
        if paid_at > now:
            continue
        rows = sorted(by_id[sid], key=lambda r: utc(r["provider_updated_at"]))
        # Full history must start at the subscription's creation/activation,
        # not at a recent snapshot or cancellation. Missing history is stated.
        first = next((r for r in rows if r.get("event_id")), None)
        if not first or first.get("event_type") not in (
                "subscription.created", "subscription.activated", "subscription.trialing",
                "subscription.imported") or utc(first["provider_updated_at"]) > paid_at + timedelta(minutes=1):
            missing += 1
        changes[paid_at].append((sid, uid, "paid", True))
        for row in rows:
            changes[utc(row["provider_updated_at"])].append((sid, uid, "state", renews(row)))
    paid, states, owners, active_people = set(), {}, {}, set()
    high, high_at, points = 0, None, []
    daily = {}
    for at, entries in sorted(changes.items()):
        before = len(active_people)
        for sid, uid, kind, value in entries:
            owners[sid] = uid
            if kind == "paid":
                paid.add(sid)
            else:
                states[sid] = value
        active_people = {owners[sid] for sid in paid if states.get(sid)}
        count = len(active_people)
        if count > high:
            high, high_at = count, at
        if not points or points[-1]["value"] != count:
            points.append({"at": defs.iso(at), "value": count})
        day = ranges.local_day(at).isoformat()
        daily[day] = max(daily.get(day, 0), before, count)
    # Fill quiet days with the last closing count, retaining intraday peaks.
    series, closing, index = [], 0, 0
    if changes:
        day = ranges.local_day(min(changes))
        while day <= ranges.local_day(now):
            end = ranges.local_midnight(day + timedelta(days=1))
            while index < len(points) and utc(points[index]["at"]) < end:
                closing = points[index]["value"]
                index += 1
            series.append({"day": day.isoformat(), "value": daily.get(day.isoformat(), closing)})
            day += timedelta(days=1)
    return {"current": len(active_people), "high": high, "high_at": defs.iso(high_at),
            "complete": missing == 0, "missing_contracts": missing,
            "contracts": len(contracts), "series": series,
            "since": defs.iso(min(changes)) if changes else None,
            "checked_at": defs.iso(now)}


def read_record(cur):
    # Payments retain the original account id after deletion. Keep those
    # customers in history when the live provider confirms their contract;
    # deleting an account must never lower a previously reached record.
    # Existing owner/test/pre-relaunch accounts still follow customer scope.
    if not db.has_table(cur, "admin_subscription_history"):
        return {"available": False}
    cur.execute(f"""SELECT p.user_id, p.subscription_id,
        min(COALESCE(p.occurred_at,p.created_at)) AS first_paid_at
        FROM payments p LEFT JOIN users u ON u.id=p.user_id
        WHERE ({defs.customer('u')} OR (
            u.id IS NULL AND p.user_id IS NOT NULL
            AND COALESCE(p.occurred_at,p.created_at) >= DATE '{defs.METRICS_EPOCH}'
            AND EXISTS (SELECT 1 FROM admin_subscription_history h
                        WHERE h.subscription_id=p.subscription_id AND h.event_id IS NOT NULL)
        )) AND {defs.success('p')}
          AND p.subscription_id LIKE 'sub_%%'
        GROUP BY p.user_id,p.subscription_id""")
    contracts = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM admin_subscription_history WHERE subscription_id = ANY(%s)",
                ([c["subscription_id"] for c in contracts],))
    history = [dict(r) for r in cur.fetchall()]
    return {"available": True, **build_record(contracts, history)}


def for_subscriptions(cur, ids):
    if not ids or not db.has_table(cur, "admin_subscription_history"):
        return {}
    cur.execute("SELECT * FROM admin_subscription_history WHERE subscription_id = ANY(%s)",
                (sorted(ids),))
    groups = defaultdict(list)
    for row in cur.fetchall():
        groups[row["subscription_id"]].append(dict(row))
    return {sid: cancellation_times(rows) for sid, rows in groups.items()}
