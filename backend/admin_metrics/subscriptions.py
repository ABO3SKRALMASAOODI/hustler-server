"""Lifetime payment dates and subscription journeys, shared by both customer views."""
from datetime import datetime, timedelta, timezone

from admin_metrics import db, defs


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def summarize(groups, snapshots, current_id=None, now=None):
    now = now or datetime.now(timezone.utc)
    groups = sorted(groups, key=lambda p: _utc(p["first_paid_at"]))
    contracts = [g for g in groups if g.get("subscription_id")]
    first = min((_utc(g["first_paid_at"]) for g in groups), default=None)
    last = max((_utc(g["last_paid_at"]) for g in groups), default=None)
    kind, returned_at = "never", None
    if contracts:
        latest = contracts[-1]
        previous = contracts[:-1]
        start = _utc((snapshots.get(latest["subscription_id"]) or {}).get("started_at"))
        # A renewal (same contract), recovery or overlapping plan switch is
        # never a return. Every earlier paid contract must have ended first.
        ends = [_utc((snapshots.get(p["subscription_id"]) or {}).get("canceled_at"))
                for p in previous]
        if previous and start and all(e and e < start for e in ends):
            kind, returned_at = "returned", _utc(latest["first_paid_at"])
        elif previous and (not start or any(e is None for e in ends)):
            kind = "history_unknown"
        elif not previous and _utc(latest["first_paid_at"]) >= now - timedelta(days=30):
            kind = "new"
        else:
            kind = "existing"
    elif groups:
        kind = "one_time"

    sid = current_id or (contracts[-1]["subscription_id"] if contracts else None)
    snapshot = snapshots.get(sid) or {}
    state = "unknown" if sid else "none"
    effective_at = None
    if snapshot.get("status") == "canceled":
        state, effective_at = "ended", snapshot.get("canceled_at")
    elif snapshot.get("scheduled_action") == "cancel":
        state, effective_at = "cancelled", snapshot.get("scheduled_effective_at")
    elif snapshot.get("status") == "paused":
        state = "paused"
    elif snapshot.get("scheduled_action") == "pause":
        state, effective_at = "pausing", snapshot.get("scheduled_effective_at")
    elif snapshot.get("status") == "past_due":
        state = "past_due"
    elif snapshot.get("status") in ("active", "trialing") and snapshot.get("next_billed_at"):
        state, effective_at = "renews", snapshot["next_billed_at"]
    return {
        "first_paid_at": defs.iso(first), "last_paid_at": defs.iso(last),
        "paid_usd": defs.usd(sum(g.get("paid_cents") or 0 for g in groups)),
        "payment_count": sum(g.get("payment_count") or 0 for g in groups),
        "subscriber_kind": kind, "returned_at": defs.iso(returned_at),
        "renewal_state": state, "renewal_at": defs.iso(effective_at),
        "subscription_checked_at": defs.iso(snapshot.get("observed_at")),
    }


def for_customers(cur, customers):
    if not customers:
        return {}
    ids = [c["id"] for c in customers]
    cur.execute(f"""SELECT p.user_id, p.subscription_id,
               min(COALESCE(p.occurred_at, p.created_at)) AS first_paid_at,
               max(COALESCE(p.occurred_at, p.created_at)) AS last_paid_at,
               COALESCE(sum(p.amount_cents) FILTER (WHERE p.currency='USD'),0) AS paid_cents,
               count(*) AS payment_count
          FROM payments p WHERE p.user_id = ANY(%s) AND {defs.success('p')}
         GROUP BY p.user_id, p.subscription_id""", (ids,))
    groups = {}
    for row in cur.fetchall():
        groups.setdefault(row["user_id"], []).append(dict(row))
    sids = {g["subscription_id"] for rows in groups.values() for g in rows
            if g["subscription_id"]}
    sids.update(c["subscription_id"] for c in customers if c.get("subscription_id"))
    snapshots = {}
    if sids and db.has_table(cur, "admin_subscription_snapshots"):
        cur.execute("SELECT * FROM admin_subscription_snapshots WHERE subscription_id = ANY(%s)",
                    (sorted(sids),))
        snapshots = {r["subscription_id"]: dict(r) for r in cur.fetchall()}
    return {c["id"]: summarize(groups.get(c["id"], []), snapshots, c.get("subscription_id"))
            for c in customers}
