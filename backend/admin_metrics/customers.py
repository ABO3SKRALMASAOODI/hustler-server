"""Customers list and customer page (plan §2.6, endpoints 11–12)."""
from admin_metrics import channels_report, defs, funnel, money, registry
from admin_metrics.ranges import RangeError

FILTERS = ("all", "paying", "payment_failing", "canceled", "ever_paid", "free")
SORTS = {"joined": "created_at", "last_active": "last_active_at",
         "paid": "paid_cents", "projects": "projects"}
DATE_FIELDS = ("joined", "active", "first_paid")
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


def _filters(cur, period, args, params):
    """WHERE fragments shared by the list and its chip counts."""
    where = [defs.customer("u")]
    date_field = (args.get("date_field") or "joined").strip()
    if date_field not in DATE_FIELDS:
        raise RangeError("date_field must be joined, active or first_paid.")
    if not period.is_all:
        if date_field == "joined":
            where.append("u.created_at >= %(start)s AND u.created_at < %(end)s")
        elif date_field == "active":
            where.append(ACTIVE_IN_RANGE.format(u="u"))
        else:
            where.append(FIRST_PAID_IN_RANGE.format(u="u"))
    q = (args.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("u.id = %(q_id)s")
            params["q_id"] = int(q)
        else:
            where.append("u.email ILIKE %(q_like)s")
            params["q_like"] = f"%{q[:120]}%"
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
        raise RangeError("sort must be joined, last_active, paid or projects.")
    direction = "ASC" if (args.get("dir") or "desc").lower() == "asc" \
        else "DESC"
    params = period.params()
    where = " AND ".join(_filters(cur, period, args, params))
    base = f"""WITH base AS MATERIALIZED (
        SELECT u.id, u.email, u.plan, u.billing_plan, u.billing_period,
               u.created_at, u.last_seen_at, u.device_type,
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
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    params.update(limit=per_page, offset=(page - 1) * per_page)
    cur.execute(base + f"""
        , agg AS (
          SELECT b.*,
                 (SELECT count(*) FROM projects p WHERE p.user_id = b.id
                     AND p.parent_project_id IS NULL) AS projects,
                 (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = b.id
                     AND vj.type = 'final' AND vj.state = 'done') AS exports,
                 (SELECT COALESCE(sum(p.amount_cents) FILTER (
                             WHERE p.currency = 'USD'), 0)
                    FROM payments p WHERE p.user_id = b.id
                     AND {defs.success('p')}) AS paid_cents,
                 {last_active_sql('b')} AS last_active_at
            FROM base b WHERE {_status_filter(f)})
        SELECT * FROM agg ORDER BY {order} {direction} {nulls}, id DESC
         LIMIT %(limit)s OFFSET %(offset)s""", params)
    rows = [dict(r) for r in cur.fetchall()]
    ids = [r["id"] for r in rows]
    touches = channels_report.customer_touches(cur, ids)
    told = survey_answers(cur, ids)
    out = []
    for r in rows:
        first = touches.get(r["id"], (None, None, None))[0]
        t = told.get(r["id"])
        out.append({
            "id": r["id"], "email": r["email"],
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
        })

    problems = billing_problem_ids(cur)
    signed_in = signed_in_metric(cur, period, where, params)
    return {
        "summary": {**counts,
                    "billing_problems": len(problems),
                    "signed_in": signed_in},
        "list": {"rows": out, "page": page, "per_page": per_page,
                 "total": total},
    }


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


def customer_detail(cur, user_id):
    cur.execute(f"""
        SELECT u.id, u.email, u.created_at, u.is_verified, u.auth_provider,
               u.device_type, u.plan, u.billing_plan, u.billing_status,
               u.billing_period, u.last_seen_at, u.credits_balance,
               u.credits_monthly, u.payment_recovered_at,
               u.payment_failed_at, u.billing_synced_at,
               {money.STATUS_SQL} AS status,
               {last_active_sql('u')} AS last_active_at
          FROM users u WHERE u.id = %s""", (user_id,))
    u = cur.fetchone()
    if not u:
        return None
    u = dict(u)
    cur.execute("""SELECT COALESCE(sum(credits_used), 0) AS used
                     FROM job_credits WHERE user_id = %s
                      AND created_at > NOW() - INTERVAL '30 days'""",
                (user_id,))
    used = float((cur.fetchone() or {}).get("used") or 0)
    touches = channels_report.customer_touches(cur, [user_id])
    first, last, tracking = touches.get(
        user_id, (channels_report.complete_touch({}),) * 2 + ("unknown",))

    cur.execute("""SELECT channel, use_case, goal, channel_other, use_case_other,
                          goal_other, completed_at, skipped
                     FROM onboarding_responses WHERE user_id = %s""",
                (user_id,))
    s = cur.fetchone()
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

    cur.execute(f"""
        SELECT p.id, COALESCE(p.occurred_at, p.created_at) AS at,
               p.amount_cents, p.currency, p.status, p.plan, p.origin,
               p.error_code, {defs.success('p')} AS ok,
               {defs.failed_payment('p')} AS failed
          FROM payments p WHERE p.user_id = %s
         ORDER BY COALESCE(p.occurred_at, p.created_at) DESC, p.id DESC
         LIMIT 200""", (user_id,))
    payments = [dict(r) for r in cur.fetchall()]
    ok = [p for p in payments if p["ok"] and p["currency"] == "USD"]
    first_paid = min((p["at"] for p in payments if p["ok"]), default=None)
    events = []
    if first_paid:
        events.append({"at": defs.iso(first_paid), "kind": "started_paying",
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
              AND vj.type = 'final' AND vj.state = 'done') AS exports
    """, {"u": user_id})
    usage = dict(cur.fetchone() or {})

    from admin_metrics import projects as projects_mod
    project_rows = projects_mod.customer_projects(cur, user_id, limit=100)

    return {
        "customer": {
            "id": u["id"], "email": u["email"],
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
                        "used_30d": round(used, 2)},
        },
        "acquisition": {"first": first, "last": last, "tracking": tracking},
        "survey": survey,
        "billing": {
            "paid_usd": round(sum(defs.usd(p["amount_cents"]) for p in ok), 2),
            "first_paid_at": defs.iso(first_paid),
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
                  "last_upload_at": defs.iso(usage.get("last_upload_at"))},
        "projects": project_rows,
    }


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
