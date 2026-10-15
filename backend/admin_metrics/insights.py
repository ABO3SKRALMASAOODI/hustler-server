"""Activation, engagement, audience and exports (the numbers the old admin
showed that the rebuilt one was missing), on the shared definitions.

- "Active" is one definition everywhere (Today, Customers, Engagement): the
  customer uploaded a video, sent an editor message, made an AI-app edit or
  finished an export (ACTIVE_UNION).
- "First" events are all-time firsts: a customer's first upload counts on the
  day it happened, never again.
- Visitors are people only (visitors.CLASS_SQL): robots, link previews and
  the owner's devices never reach a country, device or hour count.
"""
from datetime import timedelta, timezone
from statistics import median

from admin_metrics import defs, health, money, ranges, registry, visitors

# ── Active customers (shared with admin_v2) ──────────────────────────────
ACTIVE_UNION = """
    SELECT p.user_id, {day_a} AS day FROM assets a
      JOIN projects p ON p.id = a.project_id
     WHERE a.kind = 'original' AND a.created_at >= %(start_tz)s
       AND a.created_at < %(end_tz)s
    UNION ALL
    SELECT p.user_id, {day_m} FROM chat_messages cm
      JOIN projects p ON p.chat_session_id = cm.session_id
     WHERE cm.role = 'user' AND cm.created_at >= %(start)s
       AND cm.created_at < %(end)s
    UNION ALL
    SELECT vj.user_id, {day_j} FROM video_jobs vj
     WHERE vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
       AND vj.created_at >= %(start_tz)s AND vj.created_at < %(end_tz)s
    UNION ALL
    SELECT vj.user_id, {day_e} FROM video_jobs vj
     WHERE vj.type = 'final' AND vj.state = 'done'
       AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s"""


def active_sql(by_day):
    if by_day:
        parts = dict(day_a=ranges.local_date_sql("a.created_at", True),
                     day_m=ranges.local_date_sql("cm.created_at", False),
                     day_j=ranges.local_date_sql("vj.created_at", True),
                     day_e=ranges.local_date_sql("vj.updated_at", True))
    else:
        parts = dict(day_a="NULL::date", day_m="NULL::date",
                     day_j="NULL::date", day_e="NULL::date")
    return ACTIVE_UNION.format(**parts)


def active_pairs(cur, period):
    """{(user_id, local day)} for customers active in the period."""
    cur.execute(f"""SELECT DISTINCT a.user_id, a.day
                      FROM ({active_sql(True)}) a
                      JOIN users u ON u.id = a.user_id
                     WHERE {defs.customer('u')}""", period.params())
    return {(r["user_id"], r["day"]) for r in cur.fetchall()}


# ── Activation: first upload, first edit request, first export ───────────
FIRST_KINDS = ("upload", "edit", "export")
FIRST_KEYS = {"upload": "first_upload", "edit": "first_edit",
              "export": "first_export"}

_FIRSTS_SQL = f"""
    WITH ev AS (
      SELECT 'upload' AS kind, p.user_id, a.created_at AS t
        FROM assets a JOIN projects p ON p.id = a.project_id
       WHERE a.kind = 'original' AND a.created_at >= %(since_tz)s
      UNION ALL
      SELECT 'edit', p.user_id, cm.created_at AT TIME ZONE 'UTC'
        FROM chat_messages cm JOIN projects p
             ON p.chat_session_id = cm.session_id
       WHERE cm.role = 'user' AND cm.created_at >= %(since)s
      UNION ALL
      SELECT 'edit', vj.user_id, vj.created_at FROM video_jobs vj
       WHERE vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
         AND vj.created_at >= %(since_tz)s
      UNION ALL
      SELECT 'export', vj.user_id, vj.updated_at FROM video_jobs vj
       WHERE vj.type = 'final' AND vj.state = 'done'
         AND vj.updated_at >= %(since_tz)s
    ), firsts AS (
      SELECT ev.kind, ev.user_id, min(ev.t) AS t FROM ev GROUP BY 1, 2
    )
    SELECT f.kind, f.user_id, f.t FROM firsts f JOIN users u ON u.id = f.user_id
     WHERE {defs.customer('u')}
       AND NOT CASE f.kind
         WHEN 'upload' THEN EXISTS (
           SELECT 1 FROM projects p JOIN assets a ON a.project_id = p.id
            WHERE p.user_id = f.user_id AND a.kind = 'original'
              AND a.created_at < %(since_tz)s)
         WHEN 'edit' THEN (EXISTS (
           SELECT 1 FROM projects p JOIN chat_messages cm
                  ON cm.session_id = p.chat_session_id
            WHERE p.user_id = f.user_id AND cm.role = 'user'
              AND cm.created_at < %(since)s)
           OR EXISTS (
           SELECT 1 FROM video_jobs vj WHERE vj.user_id = f.user_id
              AND vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
              AND vj.created_at < %(since_tz)s))
         ELSE EXISTS (
           SELECT 1 FROM video_jobs vj WHERE vj.user_id = f.user_id
              AND vj.type = 'final' AND vj.state = 'done'
              AND vj.updated_at < %(since_tz)s)
       END"""


def activation_firsts(cur, since):
    """[(kind, user_id, aware UTC time)] of all-time firsts at/after `since`.

    A first in the window is the earliest event in the window of someone with
    no such event before it, so only the window's events are read."""
    cur.execute(_FIRSTS_SQL, {"since_tz": since, "since": ranges.naive(since)})
    out = []
    for r in cur.fetchall():
        t = r["t"]
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append((r["kind"], r["user_id"], t))
    return out


def count_firsts(firsts, period):
    """{kind: count} of firsts inside the period."""
    out = {k: 0 for k in FIRST_KINDS}
    if period is None:
        return out
    end = period.end or ranges.utc_now()
    for kind, _uid, t in firsts:
        if period.start <= t < end:
            out[kind] += 1
    return out


def firsts_by_day(firsts, days):
    """{kind: {local day: count}} for the given days."""
    wanted = set(days)
    out = {k: {} for k in FIRST_KINDS}
    for kind, _uid, t in firsts:
        d = ranges.local_day(t)
        if d in wanted:
            out[kind][d] = out[kind].get(d, 0) + 1
    return out


def activation_metrics(cur, period, href_fn=None):
    """Three Metrics (first upload / edit / export) with 14-day sparklines."""
    comp = period.compare()
    spark = ranges.trailing_days(14, period.to_day)
    since = min(x for x in (period.start, comp.start if comp else None,
                            spark.start) if x is not None)
    firsts = activation_firsts(cur, since)
    now_c, prev_c = count_firsts(firsts, period), count_firsts(firsts, comp)
    by_day = firsts_by_day(firsts, spark.day_list())
    out = {}
    for kind in FIRST_KINDS:
        key = FIRST_KEYS[kind]
        out[key] = registry.metric(
            key, now_c[kind], prev_c[kind] if comp else None,
            spark=[{"date": d.isoformat(), "value": by_day[kind].get(d, 0)}
                   for d in spark.day_list()],
            href=href_fn(kind) if href_fn else None)
    return out


# ── Engagement: DAU / WAU / MAU, stickiness, segments, retention ─────────
SEGMENTS = (
    ("daily", "Almost daily (10+ days)", 10, None),
    ("regular", "Regular (4–9 days)", 4, 9),
    ("occasional", "Occasional (2–3 days)", 2, 3),
    ("once", "Once", 1, 1),
)
RETENTION_WEEKS = 12
RETENTION_COLUMNS = 8


def _window(end_day, days):
    return ranges.make_period("custom", end_day - timedelta(days=days - 1),
                              end_day)


def engagement(cur, period):
    end_day = period.to_day
    reach_back = max(period.days or 1, 30, RETENTION_WEEKS * 7 + 7)
    wide = _window(end_day, reach_back)
    pairs = active_pairs(cur, wide)
    by_user, by_day = {}, {}
    for uid, d in pairs:
        by_user.setdefault(uid, set()).add(d)
        by_day.setdefault(d, set()).add(uid)

    def active_between(first, last):
        return {u for d, users in by_day.items() if first <= d <= last
                for u in users}

    dau = len(by_day.get(end_day, ()))
    wau = len(active_between(end_day - timedelta(days=6), end_day))
    mau_users = active_between(end_day - timedelta(days=29), end_day)
    mau = len(mau_users)
    last7 = [len(by_day.get(end_day - timedelta(days=i), ())) for i in range(7)]
    stickiness = defs.pct(sum(last7) / 7.0, mau) if mau else None

    comp = period.compare()
    prev = None
    if comp is not None and comp.to_day >= wide.from_day:
        p_end = comp.to_day
        prev = {"dau": len(by_day.get(p_end, ())),
                "wau": len(active_between(p_end - timedelta(days=6), p_end)),
                "mau": len(active_between(p_end - timedelta(days=29), p_end))
                if p_end - timedelta(days=29) >= wide.from_day else None}

    cur.execute(f"SELECT count(*) AS n FROM users u WHERE {defs.customer('u')}"
                f" AND u.created_at < %(end)s", period.params())
    customers_total = int(cur.fetchone()["n"])

    days_30 = {u: len([d for d in ds
                       if end_day - timedelta(days=29) <= d <= end_day])
               for u, ds in by_user.items()}
    segments = []
    for key, label, lo, hi in SEGMENTS:
        n = sum(1 for v in days_30.values()
                if v >= lo and (hi is None or v <= hi))
        segments.append({"key": key, "label": label, "customers": n})
    segments.append({"key": "inactive", "label": "Not active in 30 days",
                     "customers": max(0, customers_total - mau)})

    series = [{"date": d.isoformat(), "active": len(by_day.get(d, ()))}
              for d in period.day_list()]
    tiles = [
        registry.metric("dau", dau, prev["dau"] if prev else None),
        registry.metric("wau", wau, prev["wau"] if prev else None),
        registry.metric("mau", mau, prev["mau"] if prev else None),
        registry.metric("stickiness", stickiness, comparable=False),
    ]
    return {"tiles": tiles, "active_by_day": series, "segments": segments,
            "customers_total": customers_total,
            "work_by_day": work_by_day(cur, period),
            "totals": totals(cur),
            "retention": retention(cur, end_day, by_user)}


def work_by_day(cur, period):
    """Edit requests (editor messages + AI-app edits), previews rendered and
    exports per day, customers only (the old "turns + renders per day")."""
    dm = ranges.local_date_sql("cm.created_at", False)
    dj = ranges.local_date_sql("vj.created_at", True)
    du = ranges.local_date_sql("vj.updated_at", True)
    cur.execute(f"""
        SELECT x.day, x.kind, count(*) AS n FROM (
          SELECT {dm} AS day, 'edit' AS kind, p.user_id FROM chat_messages cm
            JOIN projects p ON p.chat_session_id = cm.session_id
           WHERE cm.role = 'user' AND cm.created_at >= %(start)s
             AND cm.created_at < %(end)s
          UNION ALL
          SELECT {dj}, 'edit', vj.user_id FROM video_jobs vj
           WHERE vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
             AND vj.created_at >= %(start_tz)s AND vj.created_at < %(end_tz)s
          UNION ALL
          SELECT {du}, 'preview', vj.user_id FROM video_jobs vj
           WHERE vj.type = 'preview' AND vj.state = 'done'
             AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
        ) x JOIN users u ON u.id = x.user_id
         WHERE {defs.customer('u')} GROUP BY 1, 2""", period.params())
    by = {}
    for r in cur.fetchall():
        by.setdefault(r["day"], {})[r["kind"]] = int(r["n"])
    return [{"date": d.isoformat(), "edits": (by.get(d) or {}).get("edit", 0),
             "previews": (by.get(d) or {}).get("preview", 0)}
            for d in period.day_list()]


def totals(cur):
    """All-time totals for customers (the old admin's "Totals" card)."""
    c = defs.customer("u")
    cur.execute(f"""
        WITH cu AS (SELECT u.id FROM users u WHERE {c})
        SELECT (SELECT count(*) FROM cu) AS customers,
               (SELECT count(*) FROM projects p JOIN cu ON cu.id = p.user_id
                 WHERE p.parent_project_id IS NULL) AS projects,
               (SELECT count(*) FROM assets a JOIN projects p
                       ON p.id = a.project_id JOIN cu ON cu.id = p.user_id
                 WHERE a.kind = 'original') AS uploads,
               (SELECT COALESCE(sum(a.duration_s), 0) FROM assets a
                  JOIN projects p ON p.id = a.project_id
                  JOIN cu ON cu.id = p.user_id
                 WHERE a.kind = 'original') AS uploaded_s,
               (SELECT count(*) FROM chat_messages cm JOIN projects p
                       ON p.chat_session_id = cm.session_id
                  JOIN cu ON cu.id = p.user_id WHERE cm.role = 'user')
                 AS messages,
               (SELECT count(*) FROM video_jobs vj JOIN cu ON cu.id = vj.user_id
                 WHERE vj.type = 'final' AND vj.state = 'done') AS exports,
               (SELECT count(*) FROM video_jobs vj JOIN cu ON cu.id = vj.user_id
                 WHERE vj.type = 'preview' AND vj.state = 'done') AS previews,
               (SELECT COALESCE(sum(jc.credits_used), 0) FROM job_credits jc
                  JOIN cu ON cu.id = jc.user_id) AS credits_used""")
    r = dict(cur.fetchone() or {})
    return {"customers": int(r.get("customers") or 0),
            "projects": int(r.get("projects") or 0),
            "uploads": int(r.get("uploads") or 0),
            "uploaded_hours": round(float(r.get("uploaded_s") or 0) / 3600.0,
                                    1),
            "messages": int(r.get("messages") or 0),
            "exports": int(r.get("exports") or 0),
            "previews": int(r.get("previews") or 0),
            "credits_used": round(float(r.get("credits_used") or 0), 1)}


def retention(cur, end_day, by_user):
    """Weekly signup groups and the share active in each later week."""
    this_monday = end_day - timedelta(days=end_day.weekday())
    first_monday = this_monday - timedelta(weeks=RETENTION_WEEKS - 1)
    cur.execute(f"""SELECT u.id, u.created_at FROM users u
                     WHERE {defs.customer('u')}
                       AND u.created_at >= %(start)s""",
                {"start": ranges.naive(ranges.local_midnight(first_monday))})
    groups = {}
    for r in cur.fetchall():
        joined = ranges.local_day(r["created_at"].replace(tzinfo=timezone.utc))
        monday = joined - timedelta(days=joined.weekday())
        groups.setdefault(monday, []).append(r["id"])
    rows = []
    for i in range(RETENTION_WEEKS):
        monday = this_monday - timedelta(weeks=i)
        ids = groups.get(monday, [])
        weeks_seen = (this_monday - monday).days // 7 + 1
        cells = []
        for w in range(min(RETENTION_COLUMNS, weeks_seen)):
            lo = monday + timedelta(weeks=w)
            hi = lo + timedelta(days=6)
            n = sum(1 for u in ids
                    if any(lo <= d <= hi for d in by_user.get(u, ())))
            cells.append({"week": w, "active": n,
                          "pct": defs.pct(n, len(ids)) if ids else None,
                          "partial": hi >= end_day})
        rows.append({"week": monday.isoformat(),
                     "label": ranges.week_label(monday),
                     "signups": len(ids), "cells": cells})
    return {"columns": RETENTION_COLUMNS, "rows": rows}


def top_customers(cur, period, limit=15):
    """Customers who used the most credits in the period."""
    cur.execute(f"""
        WITH used AS (
          SELECT jc.user_id, sum(jc.credits_used) AS credits,
                 count(DISTINCT jc.job_id) AS turns
            FROM job_credits jc JOIN users u ON u.id = jc.user_id
           WHERE jc.created_at >= %(start)s AND jc.created_at < %(end)s
             AND {defs.customer('u')}
           GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %(limit)s)
        SELECT used.*, u.email, COALESCE(u.billing_plan, u.plan) AS plan,
               u.billing_period, u.created_at, {money.STATUS_SQL} AS status,
               (SELECT count(*) FROM video_jobs vj WHERE vj.user_id = u.id
                   AND vj.type = 'final' AND vj.state = 'done'
                   AND vj.updated_at >= %(start_tz)s
                   AND vj.updated_at < %(end_tz)s) AS exports,
               (SELECT count(*) FROM projects p WHERE p.user_id = u.id
                   AND p.parent_project_id IS NULL
                   AND p.created_at >= %(start_tz)s
                   AND p.created_at < %(end_tz)s) AS projects,
               (SELECT COALESCE(sum(p.amount_cents) FILTER (
                           WHERE p.currency = 'USD'), 0)
                  FROM payments p WHERE p.user_id = u.id
                   AND {defs.success('p')}) AS paid_cents
          FROM used JOIN users u ON u.id = used.user_id
         ORDER BY used.credits DESC NULLS LAST""",
                {**period.params(), "limit": limit})
    return [{"customer": {"id": r["user_id"],
                          "email": r["email"], "plan": r["plan"],
                          "status": r["status"]},
             "billing_period": r["billing_period"],
             "joined_at": defs.iso(r["created_at"]),
             "credits_used": round(float(r["credits"] or 0), 1),
             "turns": int(r["turns"] or 0),
             "exports": int(r["exports"] or 0),
             "projects": int(r["projects"] or 0),
             "paid_usd": defs.usd(r["paid_cents"])}
            for r in cur.fetchall()]


def activity_feed(cur, limit=40):
    """The latest things customers did: signed up, uploaded, exported, paid."""
    cur.execute(f"""
        (SELECT 'signup' AS kind, u.created_at AT TIME ZONE 'UTC' AS at,
                u.id AS user_id, NULL::int AS project_id, NULL::text AS title,
                NULL::float AS amount, u.auth_provider AS detail
           FROM users u WHERE {defs.customer('u')}
          ORDER BY u.created_at DESC LIMIT %(n)s)
        UNION ALL
        (SELECT 'upload', a.created_at, p.user_id, p.id, p.title,
                a.duration_s, a.meta->>'filename'
           FROM assets a JOIN projects p ON p.id = a.project_id
           JOIN users u ON u.id = p.user_id
          WHERE a.kind = 'original' AND {defs.customer('u')}
          ORDER BY a.created_at DESC LIMIT %(n)s)
        UNION ALL
        (SELECT 'export', vj.updated_at, vj.user_id, vj.project_id, p.title,
                NULL::float, NULL::text
           FROM video_jobs vj JOIN users u ON u.id = vj.user_id
           LEFT JOIN projects p ON p.id = vj.project_id
          WHERE vj.type = 'final' AND vj.state = 'done'
            AND vj.updated_at >= NOW() - INTERVAL '30 days'
            AND {defs.customer('u')}
          ORDER BY vj.updated_at DESC LIMIT %(n)s)
        UNION ALL
        (SELECT 'payment', COALESCE(py.occurred_at, py.created_at)
                AT TIME ZONE 'UTC', py.user_id, NULL::int, py.plan,
                py.amount_cents / 100.0, py.currency
           FROM payments py JOIN users u ON u.id = py.user_id
          WHERE {defs.success('py')} AND {defs.customer('u')}
          ORDER BY COALESCE(py.occurred_at, py.created_at) DESC LIMIT %(n)s)
        ORDER BY at DESC LIMIT %(n)s""", {"n": limit})
    rows = [dict(r) for r in cur.fetchall()]
    ids = sorted({r["user_id"] for r in rows if r["user_id"] is not None})
    people = {}
    if ids:
        cur.execute(f"""SELECT u.id, u.email,
                               COALESCE(u.billing_plan, u.plan) AS plan,
                               {money.STATUS_SQL} AS status
                          FROM users u WHERE u.id = ANY(%s)""", (ids,))
        people = {r["id"]: dict(r) for r in cur.fetchall()}
    out = []
    for r in rows:
        p = people.get(r["user_id"]) or {}
        item = {"kind": r["kind"], "at": defs.iso(r["at"]),
                "customer": {"id": r["user_id"], "email": p.get("email"),
                             "plan": p.get("plan"),
                             "status": p.get("status") or "free"},
                "project": ({"id": r["project_id"], "title": r["title"]}
                            if r["project_id"] else None)}
        if r["kind"] == "upload":
            item["detail"] = r["detail"]
            item["length_s"] = round(float(r["amount"]), 1) \
                if r["amount"] is not None else None
        elif r["kind"] == "payment":
            item["amount"] = round(float(r["amount"] or 0), 2)
            item["currency"] = r["detail"]
            item["plan"] = r["title"]
        elif r["kind"] == "signup":
            item["detail"] = r["detail"]
        out.append(item)
    return out


# ── Audience: countries, devices, browsers, hours (people only) ──────────
AUDIENCE_V = ("COALESCE(NULLIF(pv.country, ''), 'Unknown') AS country, "
              "pv.device_type AS device_type, pv.browser AS browser,")
AUDIENCE_D = """,
       (array_agg(v.country ORDER BY v.visited_at)
            FILTER (WHERE v.country <> 'Unknown'))[1] AS country,
       (array_agg(v.device_type ORDER BY v.visited_at)
            FILTER (WHERE v.device_type IS NOT NULL))[1] AS device_type,
       (array_agg(v.browser ORDER BY v.visited_at)
            FILTER (WHERE v.browser IS NOT NULL))[1] AS browser,
       min(v.visited_at) AS first_at"""


def _ranked(counter, limit=None):
    rows = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if limit:
        rows = rows[:limit]
    return [{"key": k, "people": v} for k, v in rows]


def audience(cur, period):
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return None
    tracked = period
    if period.start is not None and period.start < defs.VISITS_SINCE:
        import dataclasses
        tracked = dataclasses.replace(period, start=defs.VISITS_SINCE)
    cur.execute(f"""WITH {visitors.browsers_cte(
                        cur, extra_cols=AUDIENCE_D, extra_v=AUDIENCE_V)}
        SELECT d.country, d.device_type, d.browser, d.first_at, d.pages,
               d.active_s
          FROM d WHERE {visitors.CLASS_SQL} = 'person'""",
                visitors.params(cur, tracked))
    rows = [dict(r) for r in cur.fetchall()]
    countries, devices, browsers = {}, {}, {}
    hours = [0] * 24
    for r in rows:
        c = r["country"] or "Unknown"
        countries[c] = countries.get(c, 0) + 1
        dv = (r["device_type"] or "unknown").lower()
        devices[dv] = devices.get(dv, 0) + 1
        b = r["browser"] or "Unknown"
        browsers[b] = browsers.get(b, 0) + 1
        if r["first_at"] is not None:
            at = r["first_at"].replace(tzinfo=timezone.utc)
            hours[at.astimezone(ranges.TZ).hour] += 1
    known = {k: v for k, v in countries.items() if k != "Unknown"}
    pages = [int(r["pages"] or 0) for r in rows]
    active = [int(r["active_s"] or 0) for r in rows if r["active_s"]]
    return {"people": len(rows),
            "countries": _ranked(known, 20),
            "countries_unknown": countries.get("Unknown", 0),
            "devices": _ranked(devices),
            "browsers": _ranked(browsers, 10),
            "hours": [{"hour": h, "people": n} for h, n in enumerate(hours)],
            "pages_per_person": round(sum(pages) / len(pages), 2)
            if pages else None,
            "median_active_s": median(active) if active else None,
            "partial": tracked is not period,
            "tracked_from": defs.iso(defs.VISITS_SINCE)}


# ── Exports ──────────────────────────────────────────────────────────────
RENDER_OF_JOB = """LEFT JOIN LATERAL (
        SELECT ra.storage_key, ra.duration_s, ra.meta->>'sheet_key' AS sheet_key
          FROM assets ra
         WHERE ra.project_id = vj.project_id AND ra.kind = 'render'
           AND ra.meta->>'variant' = 'final'
           AND ra.meta->>'render_job_id' = vj.id::text
         ORDER BY ra.id DESC LIMIT 1) r ON TRUE"""

FILMSTRIP_OF = """LEFT JOIN LATERAL (
        SELECT fj.result->>'key' AS key, (fj.result->>'cols')::int AS cols,
               (fj.result->>'rows')::int AS rows
          FROM video_jobs fj
         WHERE fj.project_id = {pid} AND fj.type = 'filmstrip'
           AND fj.state = 'done' AND fj.result ? 'key'
         ORDER BY fj.id DESC LIMIT 1) {alias} ON TRUE"""


def filmstrip_thumb(row, presign, prefix="fs_"):
    """{url, cols, rows}: the first tile of the project's filmstrip, or None."""
    key = row.get(prefix + "key")
    if not key or presign is None:
        return None
    url = presign(key)
    if not url:
        return None
    return {"url": url, "cols": row.get(prefix + "cols") or 1,
            "rows": row.get(prefix + "rows") or 1}


def _render_seconds(result):
    try:
        v = ((result or {}).get("timings") or {}).get("total_s")
        return round(float(v), 1) if v is not None else None
    except (TypeError, ValueError):
        return None


def export_row(r, presign):
    return {
        "id": r["id"], "at": defs.iso(r["updated_at"]),
        "started_at": defs.iso(r["created_at"]),
        "customer": {"id": r["user_id"], "email": r["email"],
                     "plan": r["plan"], "status": r["status"]},
        "project": {"id": r["project_id"], "title": r["title"],
                    "parent_id": r.get("parent_id")},
        "length_s": round(float(r["duration_s"]), 1)
        if r.get("duration_s") is not None else None,
        "render_s": _render_seconds(r.get("result")),
        "url": presign(r["storage_key"]) if presign and r.get("storage_key")
        else None,
        "thumb": filmstrip_thumb(r, presign),
    }


EXPORT_SELECT = f"""
    SELECT vj.id, vj.user_id, vj.project_id, vj.created_at, vj.updated_at,
           vj.result, p.title, p.parent_project_id AS parent_id, u.email,
           COALESCE(u.billing_plan, u.plan) AS plan,
           {money.STATUS_SQL} AS status,
           r.storage_key, r.duration_s, fs.key AS fs_key, fs.cols AS fs_cols,
           fs.rows AS fs_rows
      FROM video_jobs vj JOIN users u ON u.id = vj.user_id
      LEFT JOIN projects p ON p.id = vj.project_id
      {RENDER_OF_JOB}
      {FILMSTRIP_OF.format(pid="COALESCE(p.parent_project_id, p.id)",
                           alias="fs")}"""


def exports_report(cur, period, presign, recent_limit=40, failed_limit=20):
    params = period.params()
    day = ranges.local_date_sql("vj.updated_at", True)
    cur.execute(f"""
        SELECT {day} AS day,
               count(*) FILTER (WHERE vj.state = 'done') AS done,
               count(*) FILTER (WHERE vj.state = 'failed') AS failed,
               count(DISTINCT vj.user_id) FILTER (WHERE vj.state = 'done')
                   AS people,
               COALESCE(sum(r.duration_s) FILTER (WHERE vj.state = 'done'), 0)
                   AS seconds,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY NULLIF(
                   vj.result->'timings'->>'total_s', '')::float)
                   FILTER (WHERE vj.state = 'done') AS render_median_s
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
          LEFT JOIN LATERAL (
            SELECT ra.duration_s FROM assets ra
             WHERE ra.project_id = vj.project_id AND ra.kind = 'render'
               AND ra.meta->>'variant' = 'final'
               AND ra.meta->>'render_job_id' = vj.id::text
             ORDER BY ra.id DESC LIMIT 1) r ON TRUE
         WHERE vj.type = 'final' AND vj.state IN ('done', 'failed')
           AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
           AND {defs.customer('u')}
         GROUP BY GROUPING SETS ((1), ())""", params)
    by_day, total = {}, None
    for r in cur.fetchall():
        if r["day"] is None:
            total = r
        else:
            by_day[r["day"]] = r
    days = period.day_list()
    series = [{"date": d.isoformat(),
               "exports": int((by_day.get(d) or {}).get("done") or 0),
               "failed": int((by_day.get(d) or {}).get("failed") or 0),
               "people": int((by_day.get(d) or {}).get("people") or 0)}
              for d in days]
    total = total or {}
    done = int(total.get("done") or 0)
    failed = int(total.get("failed") or 0)
    comp = period.compare()
    prev = {}
    if comp is not None:
        cur.execute(f"""
            SELECT count(*) FILTER (WHERE vj.state = 'done') AS done,
                   count(*) FILTER (WHERE vj.state = 'failed') AS failed,
                   count(DISTINCT vj.user_id) FILTER (WHERE vj.state = 'done')
                       AS people
              FROM video_jobs vj JOIN users u ON u.id = vj.user_id
             WHERE vj.type = 'final' AND vj.state IN ('done', 'failed')
               AND vj.updated_at >= %(start_tz)s
               AND vj.updated_at < %(end_tz)s
               AND {defs.customer('u')}""", comp.params())
        prev = dict(cur.fetchone() or {})
    p_done, p_failed = prev.get("done"), prev.get("failed")
    tiles = [
        registry.metric("exports", done, int(p_done) if comp else None),
        registry.metric("exporters", int(total.get("people") or 0),
                        int(prev.get("people") or 0) if comp else None),
        registry.metric("failed_exports", failed,
                        int(p_failed) if comp else None),
        registry.metric(
            "export_success_rate", defs.pct(done, done + failed),
            defs.pct(int(p_done or 0), int(p_done or 0) + int(p_failed or 0))
            if comp and (p_done or p_failed) else None),
        registry.metric("exported_minutes",
                        round(float(total.get("seconds") or 0) / 60.0, 1),
                        comparable=False),
    ]

    cur.execute(EXPORT_SELECT + f"""
         WHERE vj.type = 'final' AND vj.state = 'done'
           AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
           AND {defs.customer('u')}
         ORDER BY vj.updated_at DESC, vj.id DESC LIMIT %(limit)s""",
                {**params, "limit": recent_limit})
    recent = [export_row(dict(r), presign) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT vj.id, vj.user_id, vj.project_id, vj.updated_at, vj.error,
               vj.attempts, p.title, u.email,
               COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status,
               EXISTS (SELECT 1 FROM video_jobs later
                        WHERE later.project_id = vj.project_id
                          AND later.type = 'final' AND later.state = 'done'
                          AND later.id > vj.id) AS retried_ok
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
          LEFT JOIN projects p ON p.id = vj.project_id
         WHERE vj.type = 'final' AND vj.state = 'failed'
           AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
           AND {defs.customer('u')}
         ORDER BY vj.updated_at DESC LIMIT %(limit)s""",
                {**params, "limit": failed_limit})
    failures = [{"id": r["id"], "at": defs.iso(r["updated_at"]),
                 "customer": {"id": r["user_id"], "email": r["email"],
                              "plan": r["plan"], "status": r["status"]},
                 "project": {"id": r["project_id"], "title": r["title"]},
                 "error": health.plain_error(r["error"]),
                 "attempts": int(r["attempts"] or 0),
                 "fixed_by_retry": bool(r["retried_ok"])}
                for r in cur.fetchall()]

    cur.execute(f"""
        SELECT count(DISTINCT vj.project_id) AS projects,
               count(DISTINCT vj.user_id) AS people
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE vj.type = 'preview' AND vj.state = 'done'
           AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
           AND {defs.customer('u')}""", params)
    pv = dict(cur.fetchone() or {})
    steps = health.export_steps(cur, period)
    funnel = [{"key": "previewed", "label": "Saw a preview",
               "people": int(pv.get("people") or 0),
               "projects": int(pv.get("projects") or 0)}]
    funnel += [{"key": s["key"], "label": s["label"], "people": s["people"],
                "projects": s["projects"], "events": s["events"]}
               for s in steps["stages"]]
    return {"tiles": tiles, "by_day": series, "recent": recent,
            "failed": failures, "funnel": funnel,
            "funnel_failures": steps["failures"],
            "render_median_s": round(float(total["render_median_s"]), 1)
            if total.get("render_median_s") is not None else None}
