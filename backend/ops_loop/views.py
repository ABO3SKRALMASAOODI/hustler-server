"""Admin read models for the outcome loop, and the digest's outcome section.

  summary   the Overview strip: SIGNUPS today / yesterday / 7 / 30 days with a
            30-day daily series, the OUTCOME RATE (share of reviewed sessions
            delivered_good, today and 7 days) and tickets open / fixed this
            week.
  sessions  one card per reviewed session with its evidence pictures.
  tickets   tickets grouped by root cause.

Signups are the admin's one customer population (admin_metrics.defs.customer:
verified accounts, no admin or test accounts) minus the internal ids, counted
by admin-timezone day, so the strip agrees with the Users tab and the digest.
Every query here is a small indexed read; nothing is cached.
"""
from datetime import timedelta

from admin_metrics import defs, ranges
from ops_loop import EXCLUDED_USER_IDS

TABLES = ("session_reviews", "tickets", "ticket_reviews")


def tables_ready(cur):
    cur.execute("""SELECT to_regclass('public.session_reviews') AS r,
                          to_regclass('public.tickets') AS t,
                          to_regclass('public.ticket_reviews') AS l""")
    row = cur.fetchone() or {}
    return bool(row.get("r") and row.get("t") and row.get("l"))


def _iso(v):
    return v.isoformat() if v is not None else None


def _rate(good, total):
    return round(100.0 * good / total, 1) if total else None


# ── The Overview strip ───────────────────────────────────────────────────

def signups(cur, now=None, days=30):
    now = now or ranges.utc_now()
    today = ranges.local_today(now)
    first = today - timedelta(days=days - 1)
    day_sql = ranges.local_date_sql("u.created_at", tz_aware=False)
    cur.execute(f"""
        SELECT {day_sql} AS day,
               count(*) FILTER (WHERE {defs.customer('u')}) AS n,
               count(*) FILTER (WHERE u.is_verified IS DISTINCT FROM 1
                                  AND {defs.scope('u')}) AS unverified
          FROM users u
         WHERE u.created_at >= %(start)s
           AND NOT (u.id = ANY(%(internal)s))
         GROUP BY 1""",
                {"start": ranges.naive(ranges.local_midnight(first)),
                 "tz": ranges.TZ_NAME, "internal": list(EXCLUDED_USER_IDS)})
    got = {r["day"]: r for r in cur.fetchall()}
    daily = []
    for i in range(days):
        d = first + timedelta(days=i)
        r = got.get(d) or {}
        daily.append({"day": d.isoformat(), "signups": int(r.get("n") or 0),
                      "unverified": int(r.get("unverified") or 0)})
    n = [d["signups"] for d in daily]
    return {"today": n[-1], "yesterday": n[-2] if len(n) > 1 else None,
            "d7": sum(n[-7:]), "d30": sum(n),
            "unverified_today": daily[-1]["unverified"],
            "daily": daily, "timezone": ranges.TZ_NAME,
            "definition": "Verified customer accounts created that day "
                          "(admin timezone); no admin, test or internal "
                          "accounts. Unverified sign-ups are counted "
                          "separately."}


def outcomes(cur, now=None):
    now = now or ranges.utc_now()
    today = ranges.local_midnight(ranges.local_today(now))
    week = ranges.local_midnight(ranges.local_today(now) - timedelta(days=6))
    cur.execute("""
        SELECT count(*) FILTER (WHERE started_at >= %(today)s) AS today,
               count(*) FILTER (WHERE started_at >= %(today)s
                                  AND verdict = 'delivered_good') AS good_today,
               count(*) FILTER (WHERE started_at >= %(week)s) AS d7,
               count(*) FILTER (WHERE started_at >= %(week)s
                                  AND verdict = 'delivered_good') AS good_d7,
               count(*) FILTER (WHERE started_at >= %(week)s
                                  AND verdict = 'delivered_poor') AS poor_d7,
               count(*) FILTER (WHERE started_at >= %(week)s
                                  AND verdict = 'not_delivered') AS not_d7,
               round(avg(score) FILTER (WHERE started_at >= %(week)s), 1)
                   AS score_d7,
               max(reviewed_at) AS last_reviewed_at,
               count(*) AS all_time
          FROM session_reviews""", {"today": today, "week": week})
    r = cur.fetchone() or {}
    return {"today": {"reviewed": r.get("today") or 0,
                      "good": r.get("good_today") or 0,
                      "rate_pct": _rate(r.get("good_today") or 0,
                                        r.get("today") or 0)},
            "d7": {"reviewed": r.get("d7") or 0,
                   "good": r.get("good_d7") or 0,
                   "poor": r.get("poor_d7") or 0,
                   "not_delivered": r.get("not_d7") or 0,
                   "rate_pct": _rate(r.get("good_d7") or 0, r.get("d7") or 0),
                   "avg_score": float(r["score_d7"])
                   if r.get("score_d7") is not None else None},
            "last_reviewed_at": _iso(r.get("last_reviewed_at")),
            "all_time": r.get("all_time") or 0,
            "definition": "Share of the sessions the Reviewer judged "
                          "delivered_good, by session start (admin "
                          "timezone)."}


def ticket_counts(cur, now=None):
    now = now or ranges.utc_now()
    week = now - timedelta(days=7)
    cur.execute("""
        SELECT count(*) FILTER (WHERE status IN ('open', 'fixing')) AS open,
               count(*) FILTER (WHERE status IN ('open', 'fixing')
                                  AND severity = 'P0') AS p0_open,
               count(*) FILTER (WHERE status = 'fixing') AS fixing,
               count(*) FILTER (WHERE status IN ('open', 'fixing')
                                  AND recurrence_count > 0) AS recurred_open,
               count(*) FILTER (WHERE created_at >= %(week)s) AS opened_7d,
               count(*) FILTER (WHERE status = 'fixed'
                                  AND closed_at >= %(week)s) AS fixed_7d
          FROM tickets""", {"week": week})
    r = cur.fetchone() or {}
    return {k: int(r.get(k) or 0) for k in ("open", "p0_open", "fixing",
                                             "recurred_open", "opened_7d",
                                             "fixed_7d")}


def summary(cur, now=None):
    out = {"signups": signups(cur, now)}
    if tables_ready(cur):
        out["outcomes"] = outcomes(cur, now)
        out["tickets"] = ticket_counts(cur, now)
        out["loop_ready"] = True
    else:
        out["outcomes"] = out["tickets"] = None
        out["loop_ready"] = False
    return out


# ── The Sessions tab ─────────────────────────────────────────────────────

def _evidence(items, presign):
    out = []
    for e in items or []:
        e = dict(e)
        e["url"] = presign(e.get("storage_key")) if presign else None
        out.append(e)
    return out


def sessions(cur, days=30, verdict=None, page=1, per_page=30, presign=None,
             now=None):
    now = now or ranges.utc_now()
    if not tables_ready(cur):
        return {"loop_ready": False, "sessions": [], "total": 0,
                "counts": {}}
    since = now - timedelta(days=max(1, min(int(days), 365)))
    per_page = max(1, min(int(per_page), 100))
    page = max(1, int(page))
    where, p = "r.started_at >= %(since)s", {"since": since}
    cur.execute(f"""SELECT verdict, count(*) AS n FROM session_reviews r
                     WHERE {where} GROUP BY 1""", p)
    counts = {r["verdict"]: r["n"] for r in cur.fetchall()}
    if verdict:
        where += " AND r.verdict = %(verdict)s"
        p["verdict"] = verdict
    p.update(limit=per_page, offset=(page - 1) * per_page)
    cur.execute(f"""
        SELECT r.id, r.session_key, r.surface, r.user_id, r.project_id,
               r.request_ids, r.started_at, r.settled_at, r.verdict, r.score,
               r.reasoning, r.root_cause, r.request_summary, r.evidence,
               r.signals, r.reviewer, r.review_count, r.reviewed_at,
               u.email, LEFT(pr.title, 160) AS project_title,
               pr.kind AS project_kind,
               COALESCE((SELECT json_agg(json_build_object(
                           'id', t.id, 'cause_key', t.cause_key,
                           'title', t.title, 'severity', t.severity,
                           'status', t.status, 'fix_sha', t.fix_sha,
                           'recurrence_count', t.recurrence_count)
                           ORDER BY t.id)
                           FROM ticket_reviews tr
                           JOIN tickets t ON t.id = tr.ticket_id
                          WHERE tr.review_id = r.id), '[]'::json) AS tickets
          FROM session_reviews r
          LEFT JOIN users u ON u.id = r.user_id
          LEFT JOIN projects pr ON pr.id = r.project_id
         WHERE {where}
         ORDER BY r.started_at DESC, r.id DESC
         LIMIT %(limit)s OFFSET %(offset)s""", p)
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        for k in ("started_at", "settled_at", "reviewed_at"):
            d[k] = _iso(d[k])
        d["evidence"] = _evidence(d["evidence"], presign)
        rows.append(d)
    total = counts.get(verdict, 0) if verdict else sum(counts.values())
    good = counts.get("delivered_good", 0)
    return {"loop_ready": True, "sessions": rows, "total": total,
            "page": page, "per_page": per_page, "days": days,
            "counts": counts,
            "rate_pct": _rate(good, sum(counts.values()))}


# ── The Tickets tab ──────────────────────────────────────────────────────

STATUS_ORDER = {"open": 0, "fixing": 1, "fixed": 2, "wont_fix": 3}
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def tickets(cur, status="all", presign=None, limit=300):
    if not tables_ready(cur):
        return {"loop_ready": False, "groups": [], "total": 0}
    where, p = "TRUE", {"limit": max(1, min(int(limit), 500))}
    if status == "active":
        where = "t.status IN ('open', 'fixing')"
    elif status in STATUS_ORDER:
        where, p["status"] = "t.status = %(status)s", status
    cur.execute(f"""
        SELECT t.*,
               COALESCE((SELECT json_agg(json_build_object(
                           'id', r.id, 'session_key', r.session_key,
                           'verdict', r.verdict, 'score', r.score,
                           'user_id', r.user_id, 'project_id', r.project_id,
                           'surface', r.surface,
                           'started_at', r.started_at,
                           'evidence', r.evidence) ORDER BY r.started_at DESC)
                           FROM ticket_reviews tr
                           JOIN session_reviews r ON r.id = tr.review_id
                          WHERE tr.ticket_id = t.id), '[]'::json) AS reviews
          FROM tickets t
         WHERE {where}
         ORDER BY t.last_seen DESC
         LIMIT %(limit)s""", p)
    groups = {}
    for t in cur.fetchall():
        t = dict(t)
        reviews = t.pop("reviews") or []
        for r in reviews:
            r["evidence"] = _evidence((r.get("evidence") or [])[:2], presign)
        t["reviews"] = reviews[:12]
        t["sessions"] = len(reviews)
        t["users"] = len({r["user_id"] for r in reviews})
        for k in ("first_seen", "last_seen", "closed_at", "created_at",
                  "updated_at"):
            t[k] = _iso(t[k])
        name = (t.get("root_cause") or "").strip() or t["cause_key"]
        g = groups.setdefault(name, {"root_cause": name,
                                     "has_root_cause": bool(t.get("root_cause")),
                                     "tickets": []})
        g["tickets"].append(t)
    out = []
    for g in groups.values():
        g["tickets"].sort(key=lambda t: (STATUS_ORDER.get(t["status"], 9),
                                         SEVERITY_ORDER.get(t["severity"], 9)))
        lead = g["tickets"][0]
        g["status"] = lead["status"]
        g["severity"] = min((t["severity"] for t in g["tickets"]),
                            key=lambda s: SEVERITY_ORDER.get(s, 9))
        g["sessions"] = sum(t["sessions"] for t in g["tickets"])
        g["recurrence_count"] = sum(t["recurrence_count"]
                                    for t in g["tickets"])
        out.append(g)
    out.sort(key=lambda g: (STATUS_ORDER.get(g["status"], 9),
                            SEVERITY_ORDER.get(g["severity"], 9),
                            -g["sessions"]))
    return {"loop_ready": True, "groups": out,
            "total": sum(len(g["tickets"]) for g in out)}


# ── The digest ───────────────────────────────────────────────────────────

def digest_facts(cur, start, end, presign=None, thumbs=4):
    """The outcome section of the 08:00 digest for [start, end)."""
    if not tables_ready(cur):
        return None
    week = end - timedelta(days=7)
    cur.execute("""
        SELECT count(*) FILTER (WHERE reviewed_at >= %(s)s
                                  AND reviewed_at < %(e)s) AS reviewed,
               count(*) FILTER (WHERE started_at >= %(s)s
                                  AND started_at < %(e)s) AS day_n,
               count(*) FILTER (WHERE started_at >= %(s)s AND started_at < %(e)s
                                  AND verdict = 'delivered_good') AS day_good,
               count(*) FILTER (WHERE started_at >= %(w)s
                                  AND started_at < %(e)s) AS week_n,
               count(*) FILTER (WHERE started_at >= %(w)s AND started_at < %(e)s
                                  AND verdict = 'delivered_good') AS week_good
          FROM session_reviews""", {"s": start, "e": end, "w": week})
    r = cur.fetchone() or {}
    cur.execute("""SELECT id, cause_key, title, severity, status, root_cause,
                          fix_sha, regression_check, recurrence_count
                     FROM tickets WHERE created_at >= %(s)s
                      AND created_at < %(e)s ORDER BY id""",
                {"s": start, "e": end})
    opened = [dict(x) for x in cur.fetchall()]
    cur.execute("""SELECT id, cause_key, title, severity, root_cause, fix_sha,
                          regression_check, recurrence_count
                     FROM tickets WHERE status = 'fixed'
                      AND closed_at >= %(s)s AND closed_at < %(e)s
                    ORDER BY id""", {"s": start, "e": end})
    fixed = [dict(x) for x in cur.fetchall()]
    cur.execute("""SELECT count(*) AS n FROM tickets
                    WHERE status IN ('open', 'fixing')""")
    still_open = (cur.fetchone() or {}).get("n") or 0
    # Pictures: the latest problems first, then anything reviewed.
    cur.execute("""SELECT id, session_key, verdict, score, evidence
                     FROM session_reviews
                    WHERE reviewed_at >= %(w)s AND reviewed_at < %(e)s
                    ORDER BY (verdict = 'delivered_good'), reviewed_at DESC
                    LIMIT 12""", {"w": end - timedelta(days=2), "e": end})
    pics = []
    for row in cur.fetchall():
        for e in row["evidence"] or []:
            if len(pics) >= thumbs:
                break
            url = presign(e.get("storage_key")) if presign else None
            if url:
                pics.append({"url": url, "caption": e.get("caption"),
                             "verdict": row["verdict"], "score": row["score"],
                             "session_key": row["session_key"]})
                break       # one picture per session, the first is the proof
    return {"reviewed": r.get("reviewed") or 0,
            "day": {"reviewed": r.get("day_n") or 0,
                    "good": r.get("day_good") or 0,
                    "rate_pct": _rate(r.get("day_good") or 0,
                                      r.get("day_n") or 0)},
            "week": {"reviewed": r.get("week_n") or 0,
                     "good": r.get("week_good") or 0,
                     "rate_pct": _rate(r.get("week_good") or 0,
                                       r.get("week_n") or 0)},
            "opened": opened, "fixed": fixed, "still_open": still_open,
            "thumbnails": pics}
