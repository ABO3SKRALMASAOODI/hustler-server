"""Customers list and customer page (plan §2.6, endpoints 11–12)."""
from admin_metrics import channels_report, db, defs, funnel, money, registry, subscriptions
from admin_metrics.ranges import RangeError

FILTERS = ("all", "paying", "payment_failing", "canceled", "ever_paid", "free")
SORTS = {"joined": "created_at", "last_active": "last_active_at",
         "paid": "paid_cents", "projects": "projects", "exports": "exports",
         "credits_used": "credits_used", "first_paid": "first_paid_at",
         "last_paid": "last_paid_at"}
DATE_FIELDS = ("joined", "active", "first_paid", "first_upload",
               "first_edit", "first_export")
STAGE_KEYS = ("uploaded", "asked_edit", "exported", "paid")

LAST_MESSAGE = """(SELECT max(x.t) FROM projects mp CROSS JOIN LATERAL (
        SELECT cm.created_at AT TIME ZONE 'UTC' AS t FROM chat_messages cm
         WHERE cm.session_id = mp.chat_session_id AND cm.role = 'user'
         ORDER BY cm.id DESC LIMIT 1) x WHERE mp.user_id = {u}.id)"""
LAST_UPLOAD = """(SELECT max(a.created_at) FROM projects ap
        JOIN assets a ON a.project_id = ap.id AND a.kind = 'original'
       WHERE ap.user_id = {u}.id)"""
LAST_EXPORT = """(SELECT max(vj.updated_at) FROM video_jobs vj
       WHERE vj.user_id = {u}.id AND vj.type = 'final' AND vj.state = 'done')"""


def last_active_sql(u="u"):
    return (f"GREATEST({u}.last_seen_at, {LAST_MESSAGE.format(u=u)}, "
            f"{LAST_UPLOAD.format(u=u)}, {LAST_EXPORT.format(u=u)})")


ACTIVE_IN_RANGE = """EXISTS (
    SELECT 1 FROM projects ap JOIN assets a ON a.project_id = ap.id
     WHERE ap.user_id = {u}.id AND a.kind = 'original'
       AND a.created_at >= %(start_tz)s AND a.created_at < %(end_tz)s
    UNION ALL
    SELECT 1 FROM projects mp JOIN chat_messages cm
           ON cm.session_id = mp.chat_session_id
     WHERE mp.user_id = {u}.id AND cm.role = 'user'
       AND cm.created_at >= %(start)s AND cm.created_at < %(end)s
    UNION ALL
    SELECT 1 FROM video_jobs vj WHERE vj.user_id = {u}.id
       AND vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
       AND vj.created_at >= %(start_tz)s AND vj.created_at < %(end_tz)s
    UNION ALL
    SELECT 1 FROM video_jobs vj WHERE vj.user_id = {u}.id
       AND vj.type = 'final' AND vj.state = 'done'
       AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s)"""

FIRST_PAID_IN_RANGE = f"""(SELECT min(COALESCE(fp.occurred_at, fp.created_at))
      FROM payments fp WHERE fp.user_id = {{u}}.id AND {defs.success('fp')})
      >= %(start)s AND (SELECT min(COALESCE(fp.occurred_at, fp.created_at))
      FROM payments fp WHERE fp.user_id = {{u}}.id AND {defs.success('fp')})
      < %(end)s"""


FIRST_EVENT_SQL = {
    "first_upload": """(SELECT min(a.created_at) FROM projects p
        JOIN assets a ON a.project_id = p.id
       WHERE p.user_id = {u}.id AND a.kind = 'original')""",
    "first_edit": """LEAST(
        (SELECT min(cm.created_at) AT TIME ZONE 'UTC' FROM projects p
           JOIN chat_messages cm ON cm.session_id = p.chat_session_id
          WHERE p.user_id = {u}.id AND cm.role = 'user'),
        (SELECT min(vj.created_at) FROM video_jobs vj
          WHERE vj.user_id = {u}.id AND vj.type = 'mcp_tool'
            AND vj.payload->>'mutation' = 'true'))""",
    "first_export": """(SELECT min(vj.updated_at) FROM video_jobs vj
       WHERE vj.user_id = {u}.id AND vj.type = 'final'
         AND vj.state = 'done')""",
}


def _filters(cur, period, args, params):
    """WHERE fragments shared by the list and its chip counts."""
    where = [defs.customer("u")]
    date_field = (args.get("date_field") or "joined").strip()
    if date_field not in DATE_FIELDS:
        raise RangeError("date_field must be one of "
                         + ", ".join(DATE_FIELDS) + ".")
    if not period.is_all:
        if date_field == "joined":
            where.append("u.created_at >= %(start)s AND u.created_at < %(end)s")
        elif date_field == "active":
            where.append(ACTIVE_IN_RANGE.format(u="u"))
        elif date_field in FIRST_EVENT_SQL:
            first = FIRST_EVENT_SQL[date_field].format(u="u")
            where.append(f"({first} >= %(start_tz)s AND {first} < %(end_tz)s)")
        else:
            where.append(FIRST_PAID_IN_RANGE.format(u="u"))
    q = (args.get("q") or "").strip()
    if q:
        params["q_like"] = f"%{q[:120]}%"
        if q.isdigit() and len(q) <= 18:
            # Digits are a customer id or part of an email ("jo1987@…").
            where.append("(u.id = %(q_id)s OR (u.email ILIKE %(q_like)s OR u.display_name ILIKE %(q_like)s))")
            params["q_id"] = int(q)
        else:
            where.append("(u.email ILIKE %(q_like)s OR u.display_name ILIKE %(q_like)s)")
    for name, negate in (("reached", False), ("not_reached", True)):
        stage = (args.get(name) or "").strip()
        if not stage:
            continue
        if stage not in STAGE_KEYS:
            raise RangeError(f"{name} must be one of {', '.join(STAGE_KEYS)}.")
        pred = funnel.stage_predicate(stage, "u")
        where.append(f"NOT {pred}" if negate else pred)
    ch = (args.get("channel") or "").strip()
    if ch:
        from acquisition import CHANNEL_LABELS
        if ch not in CHANNEL_LABELS:
            raise RangeError("Unknown channel.")
        rows = channel_report_rows(cur, period, date_field)
        params["channel_ids"] = [r["id"] for r in rows
                                 if r["touch"]["channel"] == ch] or [-1]
        where.append("u.id = ANY(%(channel_ids)s)")
    return where


def channel_report_rows(cur, period, date_field):
    from admin_metrics import ranges
    p = period if date_field == "joined" else \
        ranges.Period(key="all", label="Any time")
    return channels_report.classify_signups(cur, p, "first")


def _status_filter(f):
    if f == "all":
        return "TRUE"
    if f == "ever_paid":
        return (f"EXISTS (SELECT 1 FROM payments sp WHERE sp.user_id = b.id "
                f"AND {defs.success('sp')})")
    return f"b.status = '{f}'"


def list_customers(cur, period, args, page, per_page):
    f = (args.get("filter") or "all").strip()
    if f not in FILTERS:
        raise RangeError("filter must be one of " + ", ".join(FILTERS) + ".")
    sort = (args.get("sort") or "joined").strip()
    if sort not in SORTS:
        raise RangeError("sort must be joined, last_active, paid, projects, "
                         "exports, credits_used, first_paid or last_paid.")
    direction = "ASC" if (args.get("dir") or "desc").lower() == "asc" \
        else "DESC"
    params = period.params()
    where = " AND ".join(_filters(cur, period, args, params))
    base = f"""WITH base AS MATERIALIZED (
        SELECT u.id, u.email, u.display_name, u.plan, u.billing_plan, u.billing_period,
               u.created_at, u.last_seen_at, u.device_type, u.subscription_id,
               {money.STATUS_SQL} AS status
          FROM users u WHERE {where})"""

    cur.execute(base + f"""
        SELECT b.status, count(*) AS n,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM payments sp WHERE sp.user_id = b.id
                      AND {defs.success('sp')})) AS ever_paid
          FROM base b GROUP BY 1""", params)
    counts = {k: 0 for k in ("all", "paying", "payment_failing", "canceled",
                             "ever_paid", "free")}
    for r in cur.fetchall():
        counts[r["status"]] = int(r["n"])
        counts["all"] += int(r["n"])
        counts["ever_paid"] += int(r["ever_paid"])
    total = counts.get(f, 0) if f != "all" else counts["all"]

    order = SORTS[sort]
    nulls = "NULLS LAST" if sort in ("first_paid", "last_paid") or direction == "DESC" else "NULLS FIRST"
    params.update(limit=per_page, offset=(page - 1) * per_page)
    cur.execute(base + f"""
        , agg AS (
          SELECT b.*,
                 (SELECT p.id FROM projects p WHERE p.user_id = b.id
                   AND p.parent_project_id IS NULL
                   ORDER BY p.created_at DESC, p.id DESC LIMIT 1) AS latest_project_id,
                 (SELECT count(*) FROM projects p WHERE p.user_id = b.id
                     AND p.parent_project_id IS NULL) AS projects,
                 (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = b.id
                     AND vj.type = 'final' AND vj.state = 'done') AS exports,
                 (SELECT COALESCE(sum(p.amount_cents) FILTER (
                             WHERE p.currency = 'USD'), 0)
                    FROM payments p WHERE p.user_id = b.id
                     AND {defs.success('p')}) AS paid_cents,
                 (SELECT min(COALESCE(p.occurred_at, p.created_at)) FROM payments p
                   WHERE p.user_id = b.id AND {defs.success('p')}) AS first_paid_at,
                 (SELECT max(COALESCE(p.occurred_at, p.created_at)) FROM payments p
                   WHERE p.user_id = b.id AND {defs.success('p')}) AS last_paid_at,
                 (SELECT COALESCE(sum(jc.credits_used), 0) FROM job_credits jc
                   WHERE jc.user_id = b.id) AS credits_used,
                 {last_active_sql('b')} AS last_active_at
            FROM base b WHERE {_status_filter(f)})
        SELECT * FROM agg ORDER BY {order} {direction} {nulls}, id DESC
         LIMIT %(limit)s OFFSET %(offset)s""", params)
    rows = [dict(r) for r in cur.fetchall()]
    ids = [r["id"] for r in rows]
    # The page's details are independent questions: ask them at once (R10).
    got = db.parallel([
        ("touches", lambda c: channels_report.customer_touches(c, ids)),
        ("told", lambda c: survey_answers(c, ids)),
        ("extra", lambda c: row_extras(c, ids)),
        ("subscriptions", lambda c: subscriptions.for_customers(c, rows)),
        ("problems", billing_problem_ids),
        ("signed_in", lambda c: signed_in_metric(c, period, where, params)),
    ])
    touches, told, extra = _take(got, "touches"), _take(got, "told"), \
        _take(got, "extra")
    out = []
    for r in rows:
        first = touches.get(r["id"], (None, None, None))[0]
        t = told.get(r["id"])
        x = extra.get(r["id"], {})
        out.append({
            **_take(got, "subscriptions").get(r["id"], {}),
            "id": r["id"], "email": r["email"],
            "name": r.get("display_name"), "latest_project_id": r.get("latest_project_id"),
            "plan": r["billing_plan"] or r["plan"],
            "billing_period": r["billing_period"],
            "status": r["status"], "joined_at": defs.iso(r["created_at"]),
            "last_active_at": defs.iso(r["last_active_at"]),
            "device": r["device_type"],
            "came_from": first or channels_report.complete_touch({}),
            "told_us": ({"key": t, "label": channels_report.survey_label(t)}
                        if t else None),
            "projects": int(r["projects"] or 0),
            "exports": int(r["exports"] or 0),
            "paid_usd": defs.usd(r["paid_cents"]),
            "credits_left": _num(x.get("credits_balance")),
            "credits_used": round(float(r.get("credits_used") or 0), 1),
            "edit_requests": int(x.get("edit_requests") or 0),
            "uploads": int(x.get("uploads") or 0),
            "country": x.get("country"),
            "browser": x.get("device_browser"),
            "auth_provider": x.get("auth_provider"),
        })

    problems = _take(got, "problems")
    signed_in = _take(got, "signed_in")
    return {
        "summary": {**counts,
                    "billing_problems": len(problems),
                    "signed_in": signed_in},
        "list": {"rows": out, "page": page, "per_page": per_page,
                 "total": total},
    }


COUNTRY_SQL = """(SELECT pv.country FROM website_signups ws
        JOIN page_visits pv ON pv.device_id = ws.device_id
       WHERE ws.user_id = {u}.id AND pv.country IS NOT NULL
         AND pv.country NOT IN ('', 'Unknown')
       ORDER BY pv.visited_at DESC LIMIT 1)"""

EDIT_REQUESTS_SQL = """((SELECT count(*) FROM projects p JOIN chat_messages cm
          ON cm.session_id = p.chat_session_id
       WHERE p.user_id = {u}.id AND cm.role = 'user')
    + (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = {u}.id
       AND vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'))"""


def row_extras(cur, ids):
    """Credits, edit requests, uploads and country for a page of customers.

    Country is where their signup browser was seen (tracked signups only);
    None means we don't know, never a guess."""
    if not ids:
        return {}
    cur.execute(f"""
        SELECT u.id, u.credits_balance, u.device_browser, u.auth_provider,
               {EDIT_REQUESTS_SQL.format(u='u')} AS edit_requests,
               (SELECT count(*) FROM projects p JOIN assets a
                       ON a.project_id = p.id
                 WHERE p.user_id = u.id AND a.kind = 'original') AS uploads,
               {COUNTRY_SQL.format(u='u')} AS country
          FROM users u WHERE u.id = ANY(%s)""", (list(ids),))
    return {r["id"]: dict(r) for r in cur.fetchall()}


def _take(results, name):
    """A parallel task's value; its error is raised as if asked in order."""
    ok, value = results[name]
    if not ok:
        raise value
    return value


def signed_in_metric(cur, period, where, params):
    if period.is_all or not period.partial:
        return registry.metric(
            "signed_in_customers", None, status="unavailable",
            note="We keep only each customer's latest visit, so this is "
                 "available only for periods that end now.")
    cur.execute(f"""SELECT count(*) AS n FROM users u
                     WHERE {defs.customer('u')}
                       AND u.last_seen_at >= %(start_tz)s""", params)
    return registry.metric("signed_in_customers", int(cur.fetchone()["n"]))


def billing_problem_ids(cur):
    rows, _ = money.billing_problems(cur)
    return [r["id"] for r in rows]


def survey_answers(cur, ids):
    if not ids:
        return {}
    cur.execute("""SELECT user_id, CASE WHEN skipped THEN NULL ELSE channel END
                          AS channel
                     FROM onboarding_responses WHERE user_id = ANY(%s)""",
                (list(ids),))
    return {r["user_id"]: r["channel"] for r in cur.fetchall()}


def customer_ref(row, prefix=""):
    """CustomerRef from a row carrying id/email/plan/status columns."""
    return {"id": row[prefix + "id"], "email": row[prefix + "email"],
            "plan": row.get(prefix + "plan"),
            "status": row.get(prefix + "status") or "free"}


def customer_detail(cur, user_id, presign=None):
    cur.execute(f"""
        SELECT u.id, u.email, u.display_name, u.created_at, u.is_verified, u.auth_provider,
               u.device_type, u.device_browser, u.plan, u.billing_plan,
               u.billing_status, u.billing_period, u.last_seen_at, u.subscription_id,
               u.credits_balance, u.credits_monthly, u.credits_daily,
               u.credits_bonus, u.credits_monthly_limit,
               u.payment_recovered_at, u.payment_failed_at,
               u.billing_synced_at, u.trial_status, u.trial_ends_at,
               {money.STATUS_SQL} AS status,
               {last_active_sql('u')} AS last_active_at,
               {COUNTRY_SQL.format(u='u')} AS country,
               (SELECT COALESCE(sum(jc.credits_used), 0) FROM job_credits jc
                 WHERE jc.user_id = u.id) AS credits_used_total
          FROM users u WHERE u.id = %s""", (user_id,))
    u = cur.fetchone()
    if not u:
        return None
    u = dict(u)
    from admin_metrics import projects as projects_mod
    # Everything below depends only on the account: ask it all at once (R10).
    got = db.parallel([
        ("used", lambda c: _used_30d(c, user_id)),
        ("touches", lambda c: channels_report.customer_touches(c, [user_id])),
        ("survey", lambda c: _survey_row(c, user_id)),
        ("payments", lambda c: _payment_rows(c, user_id)),
        ("subscriptions", lambda c: subscriptions.for_customers(c, [u])),
        ("usage", lambda c: _usage_row(c, user_id)),
        ("projects", lambda c: projects_mod.customer_projects(
            c, user_id, limit=100, presign=presign)),
        ("exports", lambda c: customer_exports(c, user_id, presign)),
        ("activity", lambda c: customer_activity(c, user_id, [])),
        ("ledger", lambda c: credits_ledger(c, user_id)),
    ])
    used = _take(got, "used")
    touches = _take(got, "touches")
    first, last, tracking = touches.get(
        user_id, (channels_report.complete_touch({}),) * 2 + ("unknown",))

    s = _take(got, "survey")
    survey = None
    if s:
        other = " · ".join(v for v in (s["channel_other"], s["use_case_other"],
                                       s["goal_other"]) if v) or None
        survey = {"channel": s["channel"],
                  "channel_label": channels_report.survey_label(s["channel"])
                  if s["channel"] else None,
                  "use_case": s["use_case"], "goal": s["goal"],
                  "other_text": other,
                  "answered_at": defs.iso(s["completed_at"]),
                  "skipped": bool(s["skipped"])}

    payments = _take(got, "payments")
    subscription = _take(got, "subscriptions")[user_id]
    first_paid = subscription["first_paid_at"]
    events = []
    if first_paid:
        events.append({"at": first_paid, "kind": "started_paying",
                       "label": "Started paying"})
    for p in payments:
        if p["failed"]:
            events.append({"at": defs.iso(p["at"]), "kind": "payment_failed",
                           "label": f"Payment of ${defs.usd(p['amount_cents']):.2f} failed"})
    if u.get("payment_recovered_at"):
        events.append({"at": defs.iso(u["payment_recovered_at"]),
                       "kind": "payment_recovered",
                       "label": "Payment recovered"})
    if u.get("billing_status") == "canceled" and u.get("billing_synced_at"):
        events.append({"at": defs.iso(u["billing_synced_at"]),
                       "kind": "canceled_noticed",
                       "label": "Cancellation noticed by the hourly check"})
    plans_seen = [p for p in reversed(payments) if p["ok"] and p["plan"]]
    for prev, nxt in zip(plans_seen, plans_seen[1:]):
        if prev["plan"] != nxt["plan"]:
            events.append({"at": defs.iso(nxt["at"]), "kind": "plan_changed",
                           "label": f"Plan changed: {money.plan_label(prev['plan'])}"
                                    f" → {money.plan_label(nxt['plan'])}"})
    events.sort(key=lambda e: e["at"] or "", reverse=True)

    usage = _take(got, "usage")
    project_rows = _take(got, "projects")
    exports = _take(got, "exports")
    activity = _with_payments(_take(got, "activity"), payments)

    return {
        "customer": {
            "id": u["id"], "email": u["email"], "name": u.get("display_name"),
            "joined_at": defs.iso(u["created_at"]),
            "verified": bool(u["is_verified"]),
            "auth_provider": u["auth_provider"], "device": u["device_type"],
            "plan": u["billing_plan"] or u["plan"],
            "billing_status": u["billing_status"],
            "billing_period": u["billing_period"], "status": u["status"],
            "last_active_at": defs.iso(u["last_active_at"]),
            "is_internal": defs.is_internal_email(u["email"]),
            "credits": {"balance": _num(u["credits_balance"]),
                        "monthly": _num(u["credits_monthly"]),
                        "used_30d": round(used, 2),
                        "daily": _num(u.get("credits_daily")),
                        "bonus": _num(u.get("credits_bonus")),
                        "monthly_limit": _num(u.get("credits_monthly_limit")),
                        "used_total": round(float(
                            u.get("credits_used_total") or 0), 1)},
            "country": u.get("country"),
            "browser": u.get("device_browser"),
            "trial_status": u.get("trial_status"),
            "trial_ends_at": defs.iso(u.get("trial_ends_at")),
        },
        "acquisition": {"first": first, "last": last, "tracking": tracking},
        "survey": survey,
        "billing": {
            **subscription,
            "payments": [{
                "id": p["id"], "at": defs.iso(p["at"]),
                "amount_usd": defs.usd(p["amount_cents"]),
                "currency": p["currency"], "status": p["status"],
                "status_label": payment_status_label(p),
                "plan": p["plan"], "origin": p["origin"],
                "error_code": p["error_code"]} for p in payments],
            "events": events,
        },
        "usage": {"projects": int(usage.get("projects") or 0),
                  "uploads": int(usage.get("uploads") or 0),
                  "edit_requests": int(usage.get("edit_requests") or 0),
                  "exports": int(usage.get("exports") or 0),
                  "storage_bytes": int(usage.get("storage_bytes") or 0),
                  "last_upload_at": defs.iso(usage.get("last_upload_at"))},
        "credits_ledger": _take(got, "ledger"),
        "projects": project_rows,
        "exports": exports,
        "activity": activity,
    }


def _used_30d(cur, user_id):
    cur.execute("""SELECT COALESCE(sum(credits_used), 0) AS used
                     FROM job_credits WHERE user_id = %s
                      AND created_at > NOW() - INTERVAL '30 days'""",
                (user_id,))
    return float((cur.fetchone() or {}).get("used") or 0)


def _survey_row(cur, user_id):
    cur.execute("""SELECT channel, use_case, goal, channel_other, use_case_other,
                          goal_other, completed_at, skipped
                     FROM onboarding_responses WHERE user_id = %s""",
                (user_id,))
    return cur.fetchone()


def _payment_rows(cur, user_id):
    cur.execute(f"""
        SELECT p.id, COALESCE(p.occurred_at, p.created_at) AS at,
               p.amount_cents, p.currency, p.status, p.plan, p.origin,
               p.error_code, {defs.success('p')} AS ok,
               {defs.failed_payment('p')} AS failed
          FROM payments p WHERE p.user_id = %s
         ORDER BY COALESCE(p.occurred_at, p.created_at) DESC, p.id DESC
         LIMIT 200""", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def _usage_row(cur, user_id):
    cur.execute("""
        SELECT
          (SELECT count(*) FROM projects p WHERE p.user_id = %(u)s
              AND p.parent_project_id IS NULL) AS projects,
          (SELECT count(*) FROM projects p JOIN assets a ON a.project_id = p.id
            WHERE p.user_id = %(u)s AND a.kind = 'original') AS uploads,
          (SELECT max(a.created_at) FROM projects p JOIN assets a
                ON a.project_id = p.id
            WHERE p.user_id = %(u)s AND a.kind = 'original') AS last_upload_at,
          (SELECT count(*) FROM projects p JOIN chat_messages cm
                ON cm.session_id = p.chat_session_id
            WHERE p.user_id = %(u)s AND cm.role = 'user')
          + (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = %(u)s
              AND vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true')
              AS edit_requests,
          (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = %(u)s
              AND vj.type = 'final' AND vj.state = 'done') AS exports,
          (SELECT COALESCE(sum(a.bytes), 0) FROM projects p JOIN assets a
                ON a.project_id = p.id WHERE p.user_id = %(u)s) AS storage_bytes
    """, {"u": user_id})
    return dict(cur.fetchone() or {})


def credits_ledger(cur, user_id, limit=30):
    """Their latest credit charges: one row per charged editing turn."""
    cur.execute("""SELECT jc.job_id, jc.turn, jc.credits_used, jc.tokens_used,
                          jc.created_at
                     FROM job_credits jc WHERE jc.user_id = %s
                    ORDER BY jc.created_at DESC, jc.id DESC LIMIT %s""",
                (user_id, limit))
    return [{"job": r["job_id"], "turn": r["turn"],
             "credits": round(float(r["credits_used"] or 0), 2),
             "tokens": int(r["tokens_used"] or 0),
             "at": defs.iso(r["created_at"])} for r in cur.fetchall()]


def customer_exports(cur, user_id, presign, limit=50):
    """Their finished exports, newest first, with a link while it lasts."""
    from admin_metrics import insights
    cur.execute(insights.EXPORT_SELECT + """
         WHERE vj.user_id = %(u)s AND vj.type = 'final' AND vj.state = 'done'
         ORDER BY vj.updated_at DESC, vj.id DESC LIMIT %(limit)s""",
                {"u": user_id, "limit": limit})
    return [insights.export_row(dict(r), presign) for r in cur.fetchall()]


def customer_activity(cur, user_id, payments, limit=40):
    """What they did lately: messages to the editor, uploads, exports, failed
    jobs and payments, newest first."""
    cur.execute("""
        (SELECT 'message' AS kind, cm.created_at AT TIME ZONE 'UTC' AS at,
                p.id AS project_id, p.title, left(cm.content, 160) AS text
           FROM projects p JOIN chat_messages cm
                ON cm.session_id = p.chat_session_id
          WHERE p.user_id = %(u)s AND cm.role = 'user'
          ORDER BY cm.id DESC LIMIT %(n)s)
        UNION ALL
        (SELECT 'upload', a.created_at, p.id, p.title, a.meta->>'filename'
           FROM projects p JOIN assets a ON a.project_id = p.id
          WHERE p.user_id = %(u)s AND a.kind = 'original'
          ORDER BY a.id DESC LIMIT %(n)s)
        UNION ALL
        (SELECT CASE WHEN vj.state = 'done' THEN 'export'
                     ELSE 'export_failed' END,
                vj.updated_at, vj.project_id, p.title, NULL
           FROM video_jobs vj LEFT JOIN projects p ON p.id = vj.project_id
          WHERE vj.user_id = %(u)s AND vj.type = 'final'
            AND vj.state IN ('done', 'failed')
          ORDER BY vj.id DESC LIMIT %(n)s)
        ORDER BY at DESC LIMIT %(n)s""", {"u": user_id, "n": limit})
    out = [{"kind": r["kind"], "at": defs.iso(r["at"]),
            "project": ({"id": r["project_id"], "title": r["title"]}
                        if r["project_id"] else None),
            "text": r["text"]} for r in cur.fetchall()]
    return _with_payments(out, payments, limit)


def _with_payments(rows, payments, limit=40):
    """The activity rows plus their successful and failed payments."""
    out = list(rows)
    for p in payments[:20]:
        if p["ok"] or p["failed"]:
            out.append({"kind": "payment" if p["ok"] else "payment_failed",
                        "at": defs.iso(p["at"]), "project": None,
                        "text": f"${defs.usd(p['amount_cents']):.2f}"
                                + (f" {p['currency']}"
                                   if p["currency"] != "USD" else "")
                                + (f" · {money.plan_label(p['plan'])}"
                                   if p["plan"] else "")})
    out.sort(key=lambda e: e["at"] or "", reverse=True)
    return out[:limit]


def payment_status_label(p):
    if p.get("ok"):
        return "Successful"
    if p.get("failed"):
        return "Failed"
    status = (p.get("status") or "").lower()
    if status in ("completed", "paid") and not p.get("amount_cents"):
        return "Free trial start ($0)"
    return {"canceled": "Cancelled", "ready": "Not charged yet",
            "past_due": "Failed", "billed": "Billed, not paid yet"}.get(
                status, status.replace("_", " ").capitalize() or "Unknown")


def _num(v):
    return None if v is None else float(v)
