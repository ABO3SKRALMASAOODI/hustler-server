"""Q-VISITORS: who visited, as people, link previews, robots or no-signal loads.

Every browser id (page_visits.device_id from the current tracker, rows with an
analytics_id) seen in a period gets exactly one class, first match wins (G8):

  1. robot         its browser name matches ROBOT_UA
  2. internal      it ever opened /admin, or is listed as the owner's device
  3. person        interacted, clicked a tracked button, scrolled, or 2+ pages
  4. link_preview  every row carries an outreach code and came from Meta
  5. person        stayed active for more than 30 seconds
  6. no_signal     everything else

Only new-tracker rows (about a thousand a day) are read, always through the
visited_at index, so no query re-runs the robot regex over all history.
"""
from admin_metrics import db, defs, ranges

CLASSES = ("person", "link_preview", "no_signal", "robot", "internal")


def internal_ids(cur):
    """Browsers that ever opened the admin (cached for an hour)."""
    def compute():
        cur.execute("""SELECT DISTINCT device_id FROM page_visits
                        WHERE visited_at >= %s AND analytics_id IS NOT NULL
                          AND page LIKE '/admin%%' AND device_id IS NOT NULL""",
                    (ranges.naive(defs.VISITS_SINCE),))
        return sorted({r["device_id"] for r in cur.fetchall()}
                      | set(defs.INTERNAL_DEVICE_IDS))
    return db.cached_value("internal_ids", 3600, compute)


def _flags(cur):
    interacted = ("COALESCE(pv.interacted, FALSE)"
                  if db.has_column(cur, "page_visits", "interacted")
                  else "FALSE")
    signed_in = ("COALESCE(pv.signed_in, FALSE)"
                 if db.has_column(cur, "page_visits", "signed_in")
                 else "FALSE")
    return interacted, signed_in


CLASS_SQL = f"""CASE WHEN d.robot THEN 'robot'
            WHEN d.internal THEN 'internal'
            WHEN d.interacted OR d.clicked OR d.scroll > 0 OR d.pages >= 2
              THEN 'person'
            WHEN d.preview_shape THEN 'link_preview'
            WHEN d.active_s > {defs.PERSON_ACTIVE_SECONDS} THEN 'person'
            ELSE 'no_signal' END"""


def browsers_cte(cur, by_day=False, extra_where="", extra_cols=""):
    """SQL for CTEs `v` (rows) and `d` (one row per browser[, day])."""
    interacted, signed_in = _flags(cur)
    day = (ranges.local_date_sql("pv.visited_at", tz_aware=False) + " AS day,"
           if by_day else "")
    group = "v.day, v.device_id" if by_day else "v.device_id"
    return f"""
      v AS (
        SELECT pv.device_id, pv.analytics_id, pv.page, pv.visited_at,
               pv.referrer, pv.attribution, {day}
               COALESCE(pv.user_agent, '') AS user_agent,
               COALESCE(pv.time_on_page, 0) AS active_s,
               COALESCE(pv.scroll_depth, 0) AS scroll,
               {interacted} AS interacted, {signed_in} AS signed_in,
               (COALESCE(pv.attribution->'first'->>'code', '') <> ''
                OR COALESCE(pv.attribution->'last'->>'code', '') <> '')
                   AS coded
          FROM page_visits pv
         WHERE pv.analytics_id IS NOT NULL AND pv.device_id IS NOT NULL
           AND pv.visited_at >= %(start)s AND pv.visited_at < %(end)s
           {extra_where}
      ), ev AS (
        SELECT DISTINCT e.visit_id FROM website_events e
         WHERE e.created_at >= %(start)s
      ), d AS (
        SELECT {group},
               bool_or(v.user_agent ~* %(robot_ua)s) AS robot,
               bool_or(v.device_id = ANY(%(internal_ids)s)) AS internal,
               count(*) AS pages, max(v.scroll) AS scroll,
               max(v.active_s) AS active_s,
               bool_or(v.interacted) AS interacted,
               bool_or(v.signed_in) AS signed_in,
               bool_or(ev.visit_id IS NOT NULL) AS clicked,
               bool_and(v.coded AND COALESCE(v.referrer, '')
                        = ANY(%(preview_refs)s)) AS preview_shape
               {extra_cols}
          FROM v LEFT JOIN ev ON ev.visit_id = v.analytics_id
         GROUP BY {group}
      )"""


def params(cur, period, **extra):
    p = period.params()
    p.update(robot_ua=defs.ROBOT_UA, internal_ids=internal_ids(cur),
             preview_refs=list(defs.PREVIEW_REFERRERS))
    p.update(extra)
    return p


def _empty():
    return {c: 0 for c in CLASSES} | {"signed_in": 0, "browsers": 0}


def classify(cur, period):
    """Counts per class for the whole period (each browser counted once)."""
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return None
    cur.execute(f"""WITH {browsers_cte(cur)}
        SELECT {CLASS_SQL} AS class, count(*) AS browsers,
               count(*) FILTER (WHERE d.signed_in) AS signed_in
          FROM d GROUP BY 1""", params(cur, period))
    out = _empty()
    for r in cur.fetchall():
        out[r["class"]] = int(r["browsers"])
        out["browsers"] += int(r["browsers"])
        if r["class"] == "person":
            out["signed_in"] = int(r["signed_in"])
    return out


def classify_by_day(cur, period):
    """{date: counts} with each browser classified per admin-timezone day.

    Days before tracking began map to None (unknown, never 0)."""
    out = {}
    tracked_from = ranges.local_day(defs.VISITS_SINCE)
    for day in period.day_list():
        out[day] = _empty() if day >= tracked_from else None
    if all(v is None for v in out.values()):
        return out
    cur.execute(f"""WITH {browsers_cte(cur, by_day=True)}
        SELECT d.day, {CLASS_SQL} AS class, count(*) AS browsers
          FROM d GROUP BY 1, 2""", params(cur, period))
    for r in cur.fetchall():
        bucket = out.get(r["day"])
        if bucket is None:
            continue
        bucket[r["class"]] = int(r["browsers"])
        bucket["browsers"] += int(r["browsers"])
    return out


def people_rows(cur, period):
    """One row per person-class browser with its first row in the period.

    Used for channel and landing-page attribution of people."""
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return []
    extra = """,
               (array_agg(v.attribution ORDER BY v.visited_at))[1]
                   AS first_attribution,
               (array_agg(v.referrer ORDER BY v.visited_at))[1]
                   AS first_referrer,
               (array_agg(v.page ORDER BY v.visited_at))[1] AS first_page,
               min(v.visited_at) AS first_at"""
    cur.execute(f"""WITH {browsers_cte(cur, extra_cols=extra)}
        SELECT d.device_id, d.first_attribution, d.first_referrer,
               d.first_page, d.first_at, d.signed_in
          FROM d WHERE {CLASS_SQL} = 'person'""", params(cur, period))
    return [dict(r) for r in cur.fetchall()]


def device_class_sql():
    """Exported so outreach can classify browsers with identical rules."""
    return CLASS_SQL
