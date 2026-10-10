"""Money: MRR, paying customers, cash collected, the ledger, plans, snapshots.

MRR and "paying customers" are the same population by construction (Paddle
says active), so average plan value is never one plan's revenue divided by
another plan's customers. Cash is every successful payment Paddle reported in
the period, including old and deleted accounts (that is real money), minus
only the admin and test accounts.
"""
from datetime import timedelta

import billing
from plan_catalog import LEGACY_SHOPFRONT_PLANS, NEW_PLANS, PLAN_PRICES_USD
from admin_metrics import db, defs, ranges

PLAN_NAMES = {"mcp_connect": "MCP Connect", "advanced": "Advanced",
              "ultimate": "Ultimate", "ai": "Creator", "ai_pro": "Pro",
              "ai_max": "Frontier", "mcp": "MCP (legacy)", "plus": "Plus",
              "pro": "Pro (legacy)", "ultra": "Ultra", "titan": "Titan",
              "ace": "Ace", "free": "Free"}
CURRENT_PLANS = set(NEW_PLANS) | {"ultimate"}


def plan_tier(plan):
    if plan in CURRENT_PLANS:
        return "current"
    if plan in LEGACY_SHOPFRONT_PLANS:
        return "grandfathered"
    return "retired"


def plan_label(plan):
    return PLAN_NAMES.get(plan or "free", (plan or "free").replace("_", " ")
                          .title())


STATUS_SQL = f"""CASE WHEN {defs.paying('u')} THEN 'paying'
     WHEN {defs.failing('u')} THEN 'payment_failing'
     WHEN EXISTS (SELECT 1 FROM payments sp WHERE sp.user_id = u.id
                    AND {defs.success('sp')}) THEN 'canceled'
     ELSE 'free' END"""

STATUS_LABELS = {"paying": "Paying", "payment_failing": "Payment failing",
                 "canceled": "Cancelled", "free": "Free"}


def paying_groups(cur):
    """Paying customers grouped by plan and period (the MRR population)."""
    cur.execute(f"""SELECT COALESCE(u.billing_plan, u.plan) AS plan,
                           COALESCE(u.billing_period, 'monthly') AS period,
                           count(*) AS n
                      FROM users u
                     WHERE {defs.paying('u')} AND {defs.customer('u')}
                     GROUP BY 1, 2""")
    return [dict(r) for r in cur.fetchall()]


def mrr_and_paying(cur):
    groups = paying_groups(cur)
    mrr = round(sum(billing.monthly_value(g["plan"], g["period"]) * g["n"]
                    for g in groups), 2)
    return mrr, sum(int(g["n"]) for g in groups), groups


def status_counts(cur):
    cur.execute(f"""SELECT {STATUS_SQL} AS status, count(*) AS n
                      FROM users u WHERE {defs.customer('u')} GROUP BY 1""")
    out = {k: 0 for k in STATUS_LABELS}
    for r in cur.fetchall():
        out[r["status"]] = int(r["n"])
    out["ever_paid"] = out["paying"] + out["payment_failing"] + out["canceled"]
    # A paying/failing customer may not have a successful payment on record
    # (grandfathered rows); ever_paid counts the ledger, not the status.
    cur.execute(f"""SELECT count(*) AS n FROM users u
                     WHERE {defs.customer('u')} AND EXISTS (
                       SELECT 1 FROM payments p WHERE p.user_id = u.id
                          AND {defs.success('p')})""")
    out["ever_paid"] = int(cur.fetchone()["n"])
    return out


def cash(cur, period):
    """Q-CASH for the period: USD cents, payment count, non-USD count."""
    cur.execute(f"""
        SELECT COALESCE(sum(p.amount_cents) FILTER (WHERE p.currency = 'USD'),
                        0) AS cents,
               count(*) FILTER (WHERE p.currency = 'USD') AS payments,
               count(*) FILTER (WHERE p.currency <> 'USD') AS non_usd
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {defs.success('p')}
           AND COALESCE(p.occurred_at, p.created_at) >= %(start)s
           AND COALESCE(p.occurred_at, p.created_at) < %(end)s
           AND (u.id IS NULL OR LOWER(u.email) NOT IN ({defs.EXCLUDED_SQL}))
    """, period.params())
    r = cur.fetchone() or {}
    return {"usd": defs.usd(r.get("cents")),
            "payments": int(r.get("payments") or 0),
            "non_usd": int(r.get("non_usd") or 0)}


def cash_by_day(cur, period):
    day = ranges.local_date_sql("COALESCE(p.occurred_at, p.created_at)", False)
    cur.execute(f"""
        SELECT {day} AS day,
               COALESCE(sum(p.amount_cents), 0) AS cents, count(*) AS n
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {defs.success('p')} AND p.currency = 'USD'
           AND COALESCE(p.occurred_at, p.created_at) >= %(start)s
           AND COALESCE(p.occurred_at, p.created_at) < %(end)s
           AND (u.id IS NULL OR LOWER(u.email) NOT IN ({defs.EXCLUDED_SQL}))
         GROUP BY 1""", period.params())
    return {r["day"]: (defs.usd(r["cents"]), int(r["n"]))
            for r in cur.fetchall()}


_FIRSTS = f"""
    WITH firsts AS (
      SELECT p.user_id, min(COALESCE(p.occurred_at, p.created_at)) AS at
        FROM payments p JOIN users u ON u.id = p.user_id
       WHERE {defs.success('p')} AND {defs.customer('u')}
       GROUP BY p.user_id)"""


def new_paying(cur, period):
    cur.execute(_FIRSTS + """
        SELECT count(*) AS n FROM firsts
         WHERE at >= %(start)s AND at < %(end)s""", period.params())
    return int(cur.fetchone()["n"])


def new_paying_by_day(cur, period):
    day = ranges.local_date_sql("at", False)
    cur.execute(_FIRSTS + f"""
        SELECT {day} AS day, count(*) AS n FROM firsts
         WHERE at >= %(start)s AND at < %(end)s GROUP BY 1""", period.params())
    return {r["day"]: int(r["n"]) for r in cur.fetchall()}


def failed_payments(cur, period):
    cur.execute(f"""
        SELECT count(*) AS n, COALESCE(sum(p.amount_cents), 0) AS cents
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {defs.failed_payment('p')} AND p.currency = 'USD'
           AND COALESCE(p.occurred_at, p.created_at) >= %(start)s
           AND COALESCE(p.occurred_at, p.created_at) < %(end)s
           AND (u.id IS NULL OR LOWER(u.email) NOT IN ({defs.EXCLUDED_SQL}))
    """, period.params())
    r = cur.fetchone() or {}
    return {"payments": int(r.get("n") or 0), "usd": defs.usd(r.get("cents"))}


def ledger(cur):
    """Q-LEDGER: every successful USD payment, by whose account it was."""
    cur.execute(f"""
        SELECT CASE WHEN u.id IS NULL THEN 'unlinked'
                    WHEN LOWER(u.email) IN ({defs.EXCLUDED_SQL}) THEN 'excluded'
                    WHEN u.created_at < DATE '{defs.METRICS_EPOCH}'
                      THEN 'pre_relaunch'
                    ELSE 'customers' END AS bucket,
               count(*) AS n, COALESCE(sum(p.amount_cents), 0) AS cents,
               min(COALESCE(p.occurred_at, p.created_at)) AS first_at,
               max(COALESCE(p.occurred_at, p.created_at)) AS last_at
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {defs.success('p')} AND p.currency = 'USD'
         GROUP BY 1""")
    b = {r["bucket"]: r for r in cur.fetchall()}

    def usd(k):
        return defs.usd((b.get(k) or {}).get("cents"))
    unlinked = b.get("unlinked") or {}
    note = "No payments from deleted accounts."
    if unlinked.get("n"):
        first = ranges.local_day(unlinked["first_at"])
        last = ranges.local_day(unlinked["last_at"])
        note = (f"{int(unlinked['n'])} payments on "
                f"{ranges.span_label(first, last)} whose account no longer "
                "exists, most likely subscriptions to the previous product.")
    total = round(sum(usd(k) for k in ("customers", "pre_relaunch",
                                       "unlinked", "excluded")), 2)
    return {"total_usd": total, "customers_usd": usd("customers"),
            "pre_relaunch_usd": usd("pre_relaunch"),
            "unlinked_usd": usd("unlinked"), "excluded_usd": usd("excluded"),
            "unlinked_payments": int(unlinked.get("n") or 0),
            "unlinked_note": note}


def by_plan(cur, groups=None, mrr=None):
    groups = groups if groups is not None else paying_groups(cur)
    if mrr is None:
        mrr = sum(billing.monthly_value(g["plan"], g["period"]) * g["n"]
                  for g in groups)
    plans = {}
    for g in groups:
        p = plans.setdefault(g["plan"], {"plan": g["plan"],
                                         "label": plan_label(g["plan"]),
                                         "tier": plan_tier(g["plan"]),
                                         "paying": 0, "monthly": 0,
                                         "yearly": 0, "mrr_usd": 0.0})
        n = int(g["n"])
        p["paying"] += n
        p["yearly" if g["period"] == "yearly" else "monthly"] += n
        p["mrr_usd"] = round(p["mrr_usd"] + billing.monthly_value(
            g["plan"], g["period"]) * n, 2)
    order = {"current": 0, "grandfathered": 1, "retired": 2}
    rows = sorted(plans.values(), key=lambda p: (order[p["tier"]],
                                                 -p["mrr_usd"], p["plan"]))
    for p in rows:
        p["share"] = defs.pct(p["mrr_usd"], mrr) if mrr else None
    return rows


# ── Daily snapshots (migration 032) ──────────────────────────────────────

def snapshots_ready(cur):
    return db.has_table(cur, "billing_daily_status")


def snapshots_since(cur):
    if not snapshots_ready(cur):
        return None

    def compute():
        cur.execute("SELECT min(day) AS d FROM billing_daily_status")
        r = cur.fetchone()
        return r["d"] if r else None
    return db.cached_value("snapshots_since", 600, compute)


def mrr_history(cur, period):
    since = snapshots_since(cur)
    if not since:
        return {"available": False, "since": None, "rows": []}
    cur.execute("""SELECT day, COALESCE(sum(monthly_value_cents)
                                        FILTER (WHERE paying), 0) AS cents,
                          count(*) FILTER (WHERE paying) AS paying
                     FROM billing_daily_status
                    WHERE day >= %s AND day <= %s
                    GROUP BY day ORDER BY day""",
                (period.from_day, period.to_day))
    return {"available": True, "since": since.isoformat(),
            "rows": [{"date": r["day"].isoformat(),
                      "mrr_usd": defs.usd(r["cents"]),
                      "paying": int(r["paying"])} for r in cur.fetchall()]}


def mrr_at(cur, day):
    """MRR recorded in the snapshot for `day`, or None without one."""
    since = snapshots_since(cur)
    if not since or day < since:
        return None
    cur.execute("""SELECT COALESCE(sum(monthly_value_cents) FILTER
                                   (WHERE paying), 0) AS cents, count(*) AS n
                     FROM billing_daily_status WHERE day = %s""", (day,))
    r = cur.fetchone()
    return defs.usd(r["cents"]) if r and r["n"] else None


def stopped_paying(cur, period):
    """Customers paying on day D-1 and not on day D, for D in the period.

    None (unavailable) until the snapshots cover the day before the period.
    """
    since = snapshots_since(cur)
    if not since or since > period.from_day - timedelta(days=1):
        return None
    cur.execute("""
        SELECT count(DISTINCT cur.user_id) AS n
          FROM billing_daily_status prev
          JOIN billing_daily_status cur
            ON cur.user_id = prev.user_id AND cur.day = prev.day + 1
         WHERE prev.paying AND NOT cur.paying
           AND cur.day >= %s AND cur.day <= %s""",
                (period.from_day, period.to_day))
    stopped = int(cur.fetchone()["n"])
    # A paying account that disappears from the next day's snapshot (deleted
    # or no longer billed) also stopped paying.
    cur.execute("""
        SELECT count(*) AS n FROM billing_daily_status prev
         WHERE prev.paying AND prev.day >= %s AND prev.day < %s
           AND NOT EXISTS (SELECT 1 FROM billing_daily_status nxt
                            WHERE nxt.user_id = prev.user_id
                              AND nxt.day = prev.day + 1)""",
                (period.from_day - timedelta(days=1), period.to_day))
    return stopped + int(cur.fetchone()["n"])


# ── Billing problems (the hourly DB-vs-Paddle contradiction list) ───────

BILLING_PROBLEMS_SQL = """
    SELECT u.id, u.email, u.plan, u.is_subscribed, u.billing_status,
           u.trial_status, u.credits_monthly, u.subscription_id,
           u.billing_synced_at, u.payment_failed_at,
           CASE
             WHEN u.billing_status = 'canceled' AND u.is_subscribed = 1
               THEN 'canceled_but_subscribed'
             WHEN u.billing_status IN ('past_due','paused') AND u.credits_monthly > 0
               THEN 'refused_but_funded'
             WHEN u.trial_status = 'converted' AND NOT EXISTS (
                    SELECT 1 FROM payments p WHERE p.user_id = u.id
                       AND p.status = 'completed' AND p.amount_cents > 0)
               THEN 'converted_without_payment'
             WHEN u.billing_status = 'not_in_paddle'
               THEN 'not_in_paddle'
             WHEN u.is_subscribed = 1 AND u.subscription_id IS NULL
               THEN 'subscribed_without_id'
           END AS problem
      FROM users u
     WHERE (u.billing_status = 'canceled' AND u.is_subscribed = 1)
        OR (u.billing_status IN ('past_due','paused') AND u.credits_monthly > 0)
        OR (u.billing_status = 'not_in_paddle')
        OR (u.trial_status = 'converted' AND NOT EXISTS (
               SELECT 1 FROM payments p WHERE p.user_id = u.id
                  AND p.status = 'completed' AND p.amount_cents > 0))
        OR (u.is_subscribed = 1 AND u.subscription_id IS NULL)
     ORDER BY u.id"""

PROBLEM_TEXT = {
    "canceled_but_subscribed": ("We say subscribed, Paddle says cancelled",
                                "Subscribed", "Cancelled"),
    "refused_but_funded": ("Payment refused but the monthly credits are "
                           "still there", "Credits funded", "Payment failing"),
    "converted_without_payment": ("Marked as converted with no payment on "
                                  "record", "Converted", "No payment"),
    "not_in_paddle": ("Paddle doesn't recognise this subscription",
                      "Subscribed", "Not found"),
    "subscribed_without_id": ("Subscribed with no Paddle subscription id",
                              "Subscribed", "No subscription id"),
}


def billing_problems(cur):
    cur.execute(BILLING_PROBLEMS_SQL)
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT max(billing_synced_at) AS t FROM users
                    WHERE billing_synced_at IS NOT NULL""")
    last = (cur.fetchone() or {}).get("t")
    return rows, last


def billing_problem_count(cur):
    rows, _ = billing_problems(cur)
    return len(rows)


def monthly_value_cents(plan, period):
    return int(round(billing.monthly_value(plan, period) * 100))


def price_table():
    return {k: v for k, v in PLAN_PRICES_USD.items()}
