"""Q-FUNNEL, Q-BLOCKERS and the weekly signup groups.

The group (cohort) is the customers who signed up in the period; each stage
counts the people in that group who have reached it at any time since. The
stages are not forced to nest (someone can pay without exporting), so
"lost here" is never negative.
"""
from datetime import timedelta

from admin_metrics import defs, ranges, registry

STAGES = (
    ("signed_up", "funnel_signed_up"),
    ("uploaded", "funnel_uploaded"),
    ("asked_edit", "funnel_asked_edit"),
    ("exported", "funnel_exported"),
    ("paid", "funnel_paid"),
)

UPLOADED = """EXISTS (SELECT 1 FROM projects p JOIN assets a ON a.project_id = p.id
                      WHERE p.user_id = {u}.id AND a.kind = 'original')"""
ASKED_EDIT = """(EXISTS (SELECT 1 FROM projects p
                          JOIN chat_messages cm ON cm.session_id = p.chat_session_id
                         WHERE p.user_id = {u}.id AND cm.role = 'user')
                 OR EXISTS (SELECT 1 FROM video_jobs vj
                             WHERE vj.user_id = {u}.id AND vj.type = 'mcp_tool'
                               AND vj.payload->>'mutation' = 'true'))"""
EXPORTED = """EXISTS (SELECT 1 FROM video_jobs vj WHERE vj.user_id = {u}.id
                      AND vj.type = 'final' AND vj.state = 'done')"""


def paid_sql(u):
    return (f"EXISTS (SELECT 1 FROM payments pp WHERE pp.user_id = {u}.id "
            f"AND {defs.success('pp')})")


def stage_predicate(stage, u="u"):
    """SQL for 'this customer has reached `stage`' (shared with Customers)."""
    if stage == "uploaded":
        return UPLOADED.format(u=u)
    if stage == "asked_edit":
        return ASKED_EDIT.format(u=u)
    if stage == "exported":
        return EXPORTED.format(u=u)
    if stage == "paid":
        return paid_sql(u)
    raise ValueError(stage)


def _cohort_counts_sql(group_expr=None):
    group_col = f"{group_expr} AS grp," if group_expr else ""
    group_by = "GROUP BY 1" if group_expr else ""
    return f"""
        WITH c AS (SELECT u.id, u.created_at FROM users u
                    WHERE {defs.customer('u')}
                      AND u.created_at >= %(start)s AND u.created_at < %(end)s)
        SELECT {group_col} count(*) AS signed_up,
          count(*) FILTER (WHERE {stage_predicate('uploaded', 'c')}) AS uploaded,
          count(*) FILTER (WHERE {stage_predicate('asked_edit', 'c')})
              AS asked_edit,
          count(*) FILTER (WHERE {stage_predicate('exported', 'c')}) AS exported,
          count(*) FILTER (WHERE {stage_predicate('paid', 'c')}) AS paid
        FROM c {group_by}"""


def stage_counts(cur, period):
    cur.execute(_cohort_counts_sql(), period.params())
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k, _ in STAGES}


def blockers(cur, period):
    cur.execute(f"""
        WITH c AS (SELECT u.id FROM users u
                    WHERE {defs.customer('u')}
                      AND u.created_at >= %(start)s AND u.created_at < %(end)s)
        SELECT
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM client_events ce WHERE ce.user_id = c.id
                 AND ce.kind IN ('upload_rejected','upload_failed')))
              AS upload_failed,
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM client_events ce WHERE ce.user_id = c.id
                 AND ce.kind = 'subscription_upload_locked')) AS paywall_upload,
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM client_events ce WHERE ce.user_id = c.id
                 AND ce.kind IN ('subscription_cards_impression',
                                 'trial_gate_shown'))) AS plans_seen,
          count(*) FILTER (WHERE EXISTS (
              SELECT 1 FROM projects p
                JOIN chat_messages cm ON cm.session_id = p.chat_session_id
               WHERE p.user_id = c.id AND cm.role = 'assistant'
                 AND cm.meta->>'kind' = 'subscription_required'))
              AS paywall_chat
        FROM c""", period.params())
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in
            ("upload_failed", "paywall_upload", "plans_seen", "paywall_chat")}


CHECKOUT_STAGES = (
    ("opened", "Opened checkout"),
    ("loaded", "Checkout loaded"),
    ("payment_selected", "Chose a payment method"),
    ("attempted", "Tried to pay"),
    ("recorded", "Payment recorded within 7 days"),
)


def checkout(cur, period):
    """Customers' checkout steps in the period (events since 2 Oct)."""
    cur.execute(f"""
        WITH ev AS (
          SELECT ce.user_id, ce.detail->>'stage' AS stage, ce.created_at
            FROM client_events ce JOIN users u ON u.id = ce.user_id
           WHERE ce.kind = 'checkout_stage' AND {defs.customer('u')}
             AND ce.created_at >= %(start_tz)s AND ce.created_at < %(end_tz)s),
        opened AS (SELECT user_id, min(created_at) AS at FROM ev
                    WHERE stage = 'opened' GROUP BY 1)
        SELECT
          (SELECT count(DISTINCT user_id) FROM ev WHERE stage='opened') opened,
          (SELECT count(DISTINCT user_id) FROM ev WHERE stage='loaded') loaded,
          (SELECT count(DISTINCT user_id) FROM ev
            WHERE stage='payment_selected') payment_selected,
          (SELECT count(DISTINCT user_id) FROM ev
            WHERE stage IN ('payment_initiated','attempted')) attempted,
          (SELECT count(*) FROM opened o WHERE EXISTS (
              SELECT 1 FROM payments p WHERE p.user_id = o.user_id
                 AND {defs.success('p')}
                 AND COALESCE(p.occurred_at, p.created_at)
                     >= (o.at AT TIME ZONE 'UTC')
                 AND COALESCE(p.occurred_at, p.created_at)
                     < (o.at AT TIME ZONE 'UTC') + interval '7 days')) recorded
    """, period.params())
    row = cur.fetchone() or {}
    return {"since": defs.CHECKOUT_SINCE.date().isoformat(),
            "stages": [{"key": k, "label": label,
                        "people": int(row.get(k) or 0)}
                       for k, label in CHECKOUT_STAGES]}


def _href(period, extra):
    q = f"range={period.key}"
    if period.key == "custom":
        q += f"&from={period.from_day}&to={period.to_day}"
    return f"/admin/customers?{q}&date_field=joined{extra}"


def funnel(cur, period, people_metric):
    counts = stage_counts(cur, period)
    stages = []
    first = counts["signed_up"]
    prev_key = None
    for key, reg in STAGES:
        d = registry.definition(reg)
        value = counts[key]
        prev = counts[prev_key] if prev_key else None
        stages.append({
            "key": key, "label": d["label"], "how": d["how"],
            "value": value,
            "pct_of_first": defs.pct(value, first),
            "pct_of_prev": defs.pct(value, prev) if prev_key else None,
            "lost": max(prev - value, 0) if prev_key else None,
            "href": _href(period, "" if key == "signed_up"
                          else f"&reached={key}"),
            "lost_href": (_href(period,
                                (f"&reached={prev_key}" if prev_key !=
                                 "signed_up" else "")
                                + f"&not_reached={key}")
                          if prev_key else None),
        })
        prev_key = key
    b = blockers(cur, period)
    blocker_rows = [
        ("uploaded", "upload_failed", "blocker_upload_failed",
         "/admin/health?range={r}#uploads"),
        ("uploaded", "paywall_upload", "blocker_paywall_upload", None),
        ("asked_edit", "paywall_chat", "blocker_paywall_chat", None),
        ("paid", "plans_seen", "blocker_plans_seen", None),
    ]
    out_blockers = []
    for stage, key, reg, href in blocker_rows:
        d = registry.definition(reg)
        out_blockers.append({"stage": stage, "key": key, "label": d["label"],
                             "how": d["how"], "people": b[key],
                             "href": href.format(r=period.key) if href
                             else None})
    return {"people_same_period": people_metric, "stages": stages,
            "blockers": out_blockers, "checkout": checkout(cur, period)}


def cohorts(cur, weeks=12, now=None):
    """Signups per admin-timezone week (Mon–Sun), newest first."""
    today = ranges.local_today(now)
    this_monday = today - timedelta(days=today.weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    period = ranges.make_period("custom", first_monday, today, now)
    week_expr = ("date_trunc('week', (c.created_at AT TIME ZONE 'UTC' "
                 "AT TIME ZONE %(tz)s))::date")
    cur.execute(_cohort_counts_sql(week_expr), period.params())
    by_week = {r["grp"]: r for r in cur.fetchall()}
    rows = []
    for i in range(weeks):
        monday = this_monday - timedelta(weeks=i)
        r = by_week.get(monday) or {}
        rows.append({
            "week_start": monday.isoformat(),
            "label": ranges.week_label(monday),
            **{k: int(r.get(k) or 0) for k, _ in STAGES},
            "maturing": (today - monday).days < 14,
        })
    return {"rows": rows}
