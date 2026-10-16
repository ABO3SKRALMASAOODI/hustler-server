"""Lifetime payment dates and subscription journeys, shared by both customer views.

ONE subscriber state, from Paddle's stored snapshot (snapshot_state): every
row, every filter and every headline count reads it, so the admin can never
say "active" in one column and "cancelled" in the next. Paddle keeps a
cancelled subscription `active` (scheduled_change = cancel) until its paid
period ends, and keeps a refused renewal `past_due` while it retries the card;
its dashboard's "Active subscribers" counts both. So:

    renews     Paddle active, nothing scheduled: it will charge again
    cancelled  Paddle active, cancel scheduled: access until the period ends
    past_due   the renewal charge was refused, Paddle is retrying
    ended      Paddle cancelled it; no access
"""
from datetime import datetime, timedelta, timezone

import billing
from admin_metrics import db, defs


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def snapshot_state(snapshot):
    """(state, the date it refers to) for one stored Paddle snapshot."""
    status, action = snapshot.get("status"), snapshot.get("scheduled_action")
    if status == "canceled":
        return "ended", snapshot.get("canceled_at")
    if action == "cancel":
        return "cancelled", snapshot.get("scheduled_effective_at")
    if status == "paused":
        return "paused", None
    if action == "pause":
        return "pausing", snapshot.get("scheduled_effective_at")
    if status == "past_due":
        return "past_due", None
    if status == "trialing":
        return "trialing", snapshot.get("next_billed_at")
    if status == "active" and snapshot.get("next_billed_at"):
        return "renews", snapshot["next_billed_at"]
    return "unknown", None


def _account_state(account, sid, groups):
    """No Paddle snapshot yet: the account's own billing columns, never a guess."""
    status = account.get("billing_status")
    if status in ("past_due", "paused"):
        return "past_due", account.get("payment_failed_at")
    if status == "canceled" and (groups or sid):
        return "ended", account.get("billing_synced_at")
    return ("unknown" if sid else "none"), None


def summarize(groups, snapshots, current_id=None, now=None, account=None):
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
    if snapshot:
        state, effective_at = snapshot_state(snapshot)
        if state == "past_due" and account:
            effective_at = account.get("payment_failed_at")
    else:
        state, effective_at = _account_state(account or {}, sid, groups)
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
    return {c["id"]: summarize(groups.get(c["id"], []), snapshots,
                               c.get("subscription_id"), account=c)
            for c in customers}


# ── Every account's state (Users and Subscribers filters) ────────────────
# The filters ask summarize() itself, over every account that ever paid or
# holds a subscription, so a filter can never disagree with the row it lists.
FILTER_STATES = {
    "renewing": ("renews",),
    "cancelled_access": ("cancelled",),
    "payment_failing": ("past_due",),
    "ended": ("ended",),
}


def account_states(cur):
    """{user_id: state} for every account that ever paid or has a subscription."""
    cur.execute(f"""
        SELECT u.id, u.subscription_id, u.billing_status, u.is_subscribed,
               u.payment_failed_at, u.billing_synced_at
          FROM users u
         WHERE u.subscription_id IS NOT NULL
            OR u.billing_status IN ('past_due', 'paused')
            OR EXISTS (SELECT 1 FROM payments p
                        WHERE p.user_id = u.id AND {defs.success('p')})""")
    accounts = [dict(r) for r in cur.fetchall()]
    return {uid: j["renewal_state"]
            for uid, j in for_customers(cur, accounts).items()}


def ids_in(states, flt, alias="u"):
    """SQL: the account is in filter `flt` (ids are integers from our DB)."""
    wanted = FILTER_STATES[flt]
    ids = sorted(int(uid) for uid, state in states.items() if state in wanted)
    if not ids:
        return "FALSE"
    return f"{alias}.id = ANY(ARRAY[{','.join(map(str, ids))}]::int[])"


# ── The headline split, per Paddle subscription ──────────────────────────
# Counted per subscription, like Paddle's dashboard: a subscription whose
# Valmera account was deleted still counts there, and is reported here.
SPLIT_SQL = """
    SELECT s.subscription_id, s.status, s.scheduled_action, s.next_billed_at,
           s.scheduled_effective_at, s.canceled_at, s.period_ends_at,
           s.observed_at,
           acct.id AS user_id, acct.plan AS account_plan,
           acct.period AS account_period,
           paid.plan AS paid_plan, paid.at AS paid_at
      FROM admin_subscription_snapshots s
      LEFT JOIN LATERAL (
            SELECT u.id, COALESCE(u.billing_plan, u.plan) AS plan,
                   u.billing_period AS period
              FROM users u WHERE u.subscription_id = s.subscription_id
             ORDER BY u.id LIMIT 1) acct ON TRUE
      LEFT JOIN LATERAL (
            SELECT p.plan, COALESCE(p.occurred_at, p.created_at) AS at
              FROM payments p
             WHERE p.subscription_id = s.subscription_id AND {success}
             ORDER BY 2 DESC LIMIT 1) paid ON TRUE"""


def plan_of(row):
    """The plan and billing period a subscription is charged for."""
    plan = row.get("account_plan")
    if not plan or plan == "free":
        plan = row.get("paid_plan") or plan
    period = row.get("account_period")
    if not period:
        # No account holds it (deleted, or replaced): a paid period far longer
        # than a month is a yearly plan.
        paid, ends = _utc(row.get("paid_at")), _utc(row.get("period_ends_at"))
        period = ("yearly" if paid and ends and (ends - paid).days > 180
                  else "monthly")
    return plan, period


SPLIT_KEYS = {"renews": "renewing", "cancelled": "cancelled_with_access",
              "past_due": "payment_failing", "paused": "paused",
              "pausing": "pausing", "trialing": "trialing", "ended": "ended",
              "unknown": "unknown"}
HEADLINE = ("renewing", "cancelled_with_access", "payment_failing")


def split(rows):
    """Paddle's subscriptions, split the way the owner reads them.

    MRR is billing.monthly_value (list price, yearly ÷ 12). Renewing MRR is
    the renewing subscriptions only; Paddle MRR adds the cancelled ones that
    are still active until their period ends, as Paddle's dashboard does.
    """
    counts = {k: 0 for k in SPLIT_KEYS.values()}
    value = {k: 0.0 for k in SPLIT_KEYS.values()}
    no_account = {k: 0 for k in HEADLINE}
    paddle_active, paddle_mrr, checked = 0, 0.0, None
    for row in rows:
        key = SPLIT_KEYS[snapshot_state(row)[0]]
        counts[key] += 1
        seen = _utc(row.get("observed_at"))
        checked = max(checked, seen) if checked and seen else (seen or checked)
        if key == "ended":
            continue
        worth = billing.monthly_value(*plan_of(row))
        value[key] += worth
        if row.get("status") in ("active", "past_due"):
            paddle_active += 1
        if row.get("status") == "active":
            paddle_mrr += worth
        if key in no_account and not row.get("user_id"):
            no_account[key] += 1
    return {
        **counts,
        "paddle_active": paddle_active,
        "renewing_mrr_usd": round(value["renewing"], 2),
        "paddle_mrr_usd": round(paddle_mrr, 2),
        "payment_failing_mrr_usd": round(value["payment_failing"], 2),
        "without_account": no_account,
        "checked_at": defs.iso(checked),
    }


def paddle_split(cur):
    """The headline split, or {"available": False} before snapshots exist."""
    if not db.has_table(cur, "admin_subscription_snapshots"):
        return {"available": False}
    cur.execute(SPLIT_SQL.format(success=defs.success("p")))
    return {"available": True, **split([dict(r) for r in cur.fetchall()])}
