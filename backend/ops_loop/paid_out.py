"""A subscription spent and the request undone (owner, 7 Nov 2026: "a subscription
that spends its credits and leaves the request undone is the worst outcome this
product has"). Account 2855 paid at 17:13; by 19:08 six charged turns had all
ended partial and the 1,500 credits were gone. Only the owner can act on credits,
so this is a P0 and the owner hears it at once (cockpit_alerts), in facts: what
is done about a customer's credits is theirs (no billing advice anywhere).

This is the one reader that asks who pays, because the condition is about a
subscription: it says what happened to that money. It lowers nothing: the
intake's severity never depends on payment, and a free account's refusal storm
pages exactly like this customer's (ops_loop.refusals). The trial's budget stop
stays what it was, the trial's own limit answering with the plans.
"""
from datetime import timedelta

from admin_metrics import defs
from ops_loop import EXCLUDED_USER_IDS

# The worker's own test for a customer being exhausted (agent_loop: "exhausted").
OUT_OF_CREDITS = 1
BUDGET_STATUSES = ("budget",)
CAUSE_KEY = "auto-paid-credits-out"
# How far back the owner's alert reads: its claim keeps it to one email, and a
# send that failed comes back on the next tick for this long.
ALERT_WINDOW = timedelta(hours=1)


def _i():
    from ops_loop import intake
    return intake


def rows(cur, since, now, limit=400):
    """Subscribers whose request ended unfulfilled in (since, now] with their
    credits at zero, and what the plan's money bought so far: the charged turns
    of the last 30 days, and how many of them ended unfulfilled.

    A budget stop is the worker saying the wallet is spent (it fires only when a
    turn tried to spend all of it). Any other ending counts on the balance, which
    is today's, so it speaks only for the customer's latest turn: the intake's
    window reaches back, and every earlier turn would read as out of credits."""
    i = _i()
    cur.execute(f"""
        SELECT j.id AS job_id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->>'outcome' AS outcome, u.plan, u.credits_balance,
               u.credits_monthly_limit,
               spent.turns, spent.credits, spent.unfulfilled
          FROM video_jobs j JOIN users u ON u.id = j.user_id
          CROSS JOIN LATERAL (
                SELECT count(*) AS turns, COALESCE(sum(c.spent), 0) AS credits,
                       count(*) FILTER (WHERE c.outcome IS DISTINCT FROM
                                        'fulfilled') AS unfulfilled
                  FROM (SELECT NULLIF(v.result->>'credits_charged',
                                      '')::numeric AS spent,
                               v.result->>'outcome' AS outcome
                          FROM video_jobs v
                         WHERE v.user_id = j.user_id AND v.state = 'done'
                           AND v.type = 'agent_turn'
                           AND v.updated_at > j.updated_at - interval '30 days'
                           AND v.updated_at <= j.updated_at) c
                 WHERE c.spent > 0) spent
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND {i.ENDED_SHORT} AND {defs.paying("u")} AND {i._customers()}
           AND (COALESCE(j.result->>'status', '') = ANY(%(budget)s)
                OR (COALESCE(u.credits_balance, 0) < %(out)s
                    AND NOT EXISTS (
                          SELECT 1 FROM video_jobs n
                           WHERE n.user_id = j.user_id AND n.id > j.id
                             AND n.type = 'agent_turn')))
         ORDER BY j.updated_at LIMIT %(limit)s""",
                {"since": i._aware(since), "now": i._aware(now), "limit": limit,
                 "internal": list(EXCLUDED_USER_IDS),
                 "budget": list(BUDGET_STATUSES), "out": OUT_OF_CREDITS})
    return [dict(r) for r in cur.fetchall()]


def facts(r):
    """One line. Never a suggestion."""
    limit = float(r.get("credits_monthly_limit") or 0)
    left = float(r.get("credits_balance") or 0)
    of = f" of {limit:,.0f}" if limit else ""
    turns = r["turns"]
    return (f"account #{r['user_id']} on {r.get('plan') or 'a paid plan'}: "
            f"{left:,.0f}{of} credits left · project {r['project_id']}, the "
            f"request ended {r['outcome']} at {_i()._aware(r['at']):%H:%M} UTC · "
            f"{turns} paid turn{'s' if turns != 1 else ''} in 30 days, "
            f"{float(r['credits']):,.0f} credits spent, {r['unfulfilled']} ended "
            f"unfulfilled")


def scan(cur, since, now, p):
    i = _i()
    return [{"ref": f"credits:{r['job_id']}", "source": "credits",
             "at": i._aware(r["at"]), "user_id": r["user_id"],
             "project_id": r["project_id"], "severity": "P0",
             "cause_key": CAUSE_KEY, "job_id": r["job_id"],
             "title": "A subscriber ran out of credits with their request undone",
             "detail": i._short(facts(r), 600)}
            for r in rows(cur, since, now, p["limit"])]


def alerts(cur, now):
    """What the owner has not heard yet: (job id, line)."""
    return [(r["job_id"], facts(r))
            for r in rows(cur, now - ALERT_WINDOW, now)]
