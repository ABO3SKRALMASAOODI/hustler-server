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
from datetime import timedelta, timezone

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


# ── Awareness: the four questions (owner, 29 Oct 2026) ───────────────────
# One read model for the top of the admin (today so far) and the 08:00
# digest (yesterday): did customers get good results; what failed and what
# was fixed automatically; signups and active users; infrastructure health.

REPO_COMMIT = {"be": "https://github.com/ABO3SKRALMASAOODI/hustler-server/commit/",
               "fe": "https://github.com/ABO3SKRALMASAOODI/startup_frontend/commit/"}
TICKET_URL = "https://valmera.io/admin?tab=tickets&ticket="
BOX_HEALTH_PREFIX = "box_health"      # app_kv rows the render box writes
BOX_STALE = timedelta(minutes=10)


def commit_links(fix_sha):
    """[{sha, url}] for a ticket's fix_sha ("abc123,fe:def456")."""
    out = []
    for part in (fix_sha or "").split(","):
        part = part.strip()
        if not part:
            continue
        repo, _, sha = part.rpartition(":")
        repo = repo or "be"
        out.append({"sha": sha[:12], "repo": repo,
                    "url": REPO_COMMIT.get(repo, REPO_COMMIT["be"]) + sha})
    return out


def results_between(cur, start, end):
    cur.execute("""
        SELECT count(*) AS judged,
               count(*) FILTER (WHERE verdict = 'delivered_good') AS good,
               count(*) FILTER (WHERE verdict = 'delivered_poor') AS poor,
               count(*) FILTER (WHERE verdict = 'not_delivered') AS not_d,
               round(avg(score), 1) AS score
          FROM session_reviews
         WHERE started_at >= %(s)s AND started_at < %(e)s""",
                {"s": start, "e": end})
    r = cur.fetchone() or {}
    judged = int(r.get("judged") or 0)
    good = int(r.get("good") or 0)
    return {"judged": judged, "good": good, "poor": int(r.get("poor") or 0),
            "not_delivered": int(r.get("not_d") or 0),
            "rate_pct": _rate(good, judged),
            "avg_score": float(r["score"]) if r.get("score") is not None
            else None}


INTAKE_STALE = timedelta(minutes=10)


def intake_last_pass(cur):
    """When the automatic intake last ran (its watermark), or None."""
    import json
    from ops_loop import intake
    cur.execute("SELECT value FROM app_kv WHERE key = %s",
                (intake.WATERMARK_KEY,))
    row = cur.fetchone()
    if not row:
        return None
    try:
        v = json.loads(row["value"]) if isinstance(row["value"], str) \
            else row["value"]
        at = (v or {}).get("at")
        return _aware_dt(at) if at else None
    except (TypeError, ValueError, AttributeError):
        return None


def _aware_dt(v):
    from datetime import datetime
    v = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def failures_between(cur, start, end, now=None):
    """Failures filed in [start, end) by ticket, the tickets fixed in it,
    what is still open, and the P0s nobody picked up (two reads)."""
    from ops_loop import intake
    now = now or end
    cur.execute("""
        SELECT t.id, h->>'user' AS u, h->>'project' AS project
          FROM tickets t, jsonb_array_elements(t.history) h
         WHERE t.last_seen >= %(s)s AND h->>'event' = 'failure'
           AND (h->>'at')::timestamptz >= %(s)s
           AND (h->>'at')::timestamptz < %(e)s""", {"s": start, "e": end})
    per, users = {}, set()
    for r in cur.fetchall():
        d = per.setdefault(r["id"], {"failures": 0, "users": set(),
                                     "last_project": None})
        d["failures"] += 1
        if r["u"] is not None:
            d["users"].add(r["u"])
            users.add(r["u"])
        d["last_project"] = r["project"] or d["last_project"]
    cur.execute("""
        SELECT id, cause_key, title, severity, status, root_cause, fix_sha,
               regression_check, recurrence_count, closed_at, created_at,
               (SELECT max((h->>'at')::timestamptz) FROM
                  jsonb_array_elements(history) h
                 WHERE h->>'event' IN ('opened', 'recurred', 'escalated'))
                   AS opened_at
          FROM tickets
         WHERE id = ANY(%(ids)s) OR status IN ('open', 'fixing')
            OR (status = 'fixed' AND closed_at >= %(s)s
                AND closed_at < %(e)s)""",
                {"ids": sorted(per), "s": start, "e": end})
    tickets = {r["id"]: dict(r) for r in cur.fetchall()}
    by_ticket, fixed, unhandled = [], [], []
    for tid, agg in per.items():
        t = tickets.get(tid)
        if not t:
            continue
        by_ticket.append({
            "id": tid, "cause_key": t["cause_key"], "title": t["title"],
            "severity": t["severity"], "status": t["status"],
            "fix_sha": t["fix_sha"], "regression_check": t["regression_check"],
            "closed_at": _iso(t["closed_at"]),
            "recurrence_count": t["recurrence_count"],
            "failures": agg["failures"], "users": len(agg["users"]),
            "last_project": agg["last_project"],
            "url": TICKET_URL + str(tid),
            "commits": commit_links(t.get("fix_sha")),
            "auto": t["cause_key"].startswith("auto-")})
    by_ticket.sort(key=lambda x: (-x["failures"], x["id"]))
    for t in sorted(tickets.values(), key=lambda x: x["closed_at"] or end):
        if t["status"] == "fixed" and t["closed_at"] and \
                start <= t["closed_at"] < end:
            cause = (t.get("root_cause") or "").strip()
            fixed.append({"id": t["id"], "cause_key": t["cause_key"],
                          "title": t["title"], "severity": t["severity"],
                          "root_cause": cause.splitlines()[0][:220]
                          if cause else None,
                          "fix_sha": t["fix_sha"],
                          "regression_check": t["regression_check"],
                          "recurrence_count": t["recurrence_count"],
                          "closed_at": _iso(t["closed_at"]),
                          "url": TICKET_URL + str(t["id"]),
                          "commits": commit_links(t.get("fix_sha"))})
        opened = t.get("opened_at") or t.get("created_at")
        if t["status"] == "open" and t["severity"] == "P0" and opened and \
                now - opened >= intake.UNHANDLED_AFTER:
            unhandled.append({"id": t["id"], "title": t["title"],
                              "waiting_min": int((now - opened)
                                                 .total_seconds() // 60),
                              "url": TICKET_URL + str(t["id"])})
    active = [t for t in tickets.values() if t["status"] in ("open",
                                                             "fixing")]
    last_pass = intake_last_pass(cur)
    return {"intake_last_at": _iso(last_pass),
            "intake_stale": last_pass is None
            or now - last_pass > INTAKE_STALE,
            "total": sum(x["failures"] for x in by_ticket),
            "customers": len(users), "by_ticket": by_ticket, "fixed": fixed,
            "open": sum(1 for t in active if t["status"] == "open"),
            "fixing": sum(1 for t in active if t["status"] == "fixing"),
            "p0_active": sum(1 for t in active if t["severity"] == "P0"),
            "unhandled": unhandled}


def people_between(cur, start, end):
    """Signups (the Overview's definition) and ACTIVE customers: anyone who
    sent a message, uploaded, ran a job or an MCP call in [start, end)."""
    p = {"s": start, "e": end, "sn": ranges.naive(start),
         "en": ranges.naive(end), "internal": list(EXCLUDED_USER_IDS)}
    cur.execute(f"""
        WITH act AS (
          SELECT j.user_id FROM video_jobs j
           WHERE j.created_at >= %(s)s AND j.created_at < %(e)s
          UNION
          SELECT ce.user_id FROM client_events ce
           WHERE ce.created_at >= %(s)s AND ce.created_at < %(e)s
             AND ce.kind IN ('upload_started', 'upload_landed',
                             'first_prompt_sent', 'export_clicked',
                             'download_triggered')
          UNION
          SELECT p.user_id FROM chat_messages m
            JOIN projects p ON p.chat_session_id = m.session_id
           WHERE m.created_at >= %(sn)s AND m.created_at < %(en)s
             AND m.role = 'user')
        SELECT (SELECT count(*) FROM users u
                 WHERE {defs.customer('u')}
                   AND NOT (u.id = ANY(%(internal)s))
                   AND u.created_at >= %(sn)s AND u.created_at < %(en)s)
                   AS signups,
               (SELECT count(*) FROM act JOIN users u ON u.id = act.user_id
                 WHERE {defs.customer('u')}
                   AND NOT (u.id = ANY(%(internal)s))) AS active""", p)
    r = cur.fetchone() or {}
    return {"signups": int(r.get("signups") or 0),
            "active": int(r.get("active") or 0)}


def queue_now(cur, now):
    """The job queue right now and over the last 24 h (every user,
    including the owner's factory, since it shares the capacity)."""
    cur.execute("""
        SELECT count(*) FILTER (WHERE state = 'queued') AS queued,
               count(*) FILTER (WHERE state = 'running') AS running,
               EXTRACT(EPOCH FROM (%(now)s - min(created_at) FILTER (
                   WHERE state = 'queued'))) / 60.0 AS oldest_queued_min
          FROM video_jobs
         WHERE state IN ('queued', 'running')
           AND type NOT IN ('filmstrip')""", {"now": now})
    q = dict(cur.fetchone() or {})
    cur.execute("""
        SELECT count(*) AS jobs,
               count(*) FILTER (WHERE state = 'failed') AS failed,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY NULLIF(
                   result->'timings'->>'queue_wait_s', '')::float)
                   AS p90_wait_s
          FROM video_jobs
         WHERE updated_at >= %(since)s AND state IN ('done', 'failed')""",
                {"since": now - timedelta(hours=24)})
    d = dict(cur.fetchone() or {})
    oldest = q.get("oldest_queued_min")
    return {"queued": int(q.get("queued") or 0),
            "running": int(q.get("running") or 0),
            "oldest_queued_min": round(float(oldest), 1)
            if oldest is not None else None,
            "jobs_24h": int(d.get("jobs") or 0),
            "failed_24h": int(d.get("failed") or 0),
            "p90_wait_s_24h": round(float(d["p90_wait_s"]), 1)
            if d.get("p90_wait_s") is not None else None}


def box_health(cur, now):
    """What the render box(es) last reported (app_kv box_health*: at,
    load1, cpus, mem_used_pct, disk_used_pct, running_jobs). None until a
    box reports; `stale` when its last report is older than BOX_STALE."""
    import json
    cur.execute("""SELECT key, value, updated_at FROM app_kv
                    WHERE key = %s OR key LIKE %s ORDER BY key""",
                (BOX_HEALTH_PREFIX, BOX_HEALTH_PREFIX + ":%"))
    boxes = []
    for r in cur.fetchall():
        try:
            v = json.loads(r["value"]) if isinstance(r["value"], str) \
                else (r["value"] or {})
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        at = r["updated_at"]
        at = at.replace(tzinfo=timezone.utc) if at is not None and \
            at.tzinfo is None else at
        boxes.append({
            "name": v.get("name") or r["key"].partition(":")[2] or "box",
            "load1": v.get("load1"), "cpus": v.get("cpus"),
            "mem_used_pct": v.get("mem_used_pct"),
            "disk_used_pct": v.get("disk_used_pct"),
            "running_jobs": v.get("running_jobs"),
            "reported_at": _iso(at),
            "stale": bool(at and now - at > BOX_STALE)})
    return boxes or None


def cost_facts(cur, now):
    """The executor + R2 spend (infra_cost's model of our own ledger)."""
    from admin_metrics import infra_cost
    rep = infra_cost.report(cur, now)
    return {"yesterday_usd": rep.get("yesterday_usd"),
            "cycle_to_date_usd": (rep.get("cycle") or {}).get("to_date_usd"),
            "cycle_projected_usd": (rep.get("cycle") or {})
            .get("projected_usd"),
            "owner_share_7d_pct": rep.get("last_7d_owner_share_pct"),
            "what": "Cloudflare executors + R2, modeled from our own ledger"}


def infra_between(cur, now, costs=None):
    """Queue, box health and spend. `costs` replaces the direct cost read
    (the admin serves it from the shared report cache)."""
    out = {"queue": queue_now(cur, now), "boxes": box_health(cur, now),
           "costs": None}
    try:
        out["costs"] = costs() if costs else cost_facts(cur, now)
    except Exception as e:                     # never lose the other parts
        out["costs_error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def awareness(cur, start, end, now=None, costs=None):
    """The four answers for [start, end). Each part fails on its own."""
    now = now or ranges.utc_now()
    try:
        ready = tables_ready(cur)
    except Exception as e:
        ready = False
        print(f"⚠️ [ops views] tables_ready failed: {type(e).__name__}: {e}",
              flush=True)
    out = {"start": _iso(start), "end": _iso(end), "loop_ready": ready}
    parts = (("results", lambda: results_between(cur, start, end)
              if out["loop_ready"] else None),
             ("results_7d", lambda: results_between(
                 cur, end - timedelta(days=7), end)
              if out["loop_ready"] else None),
             ("failures", lambda: failures_between(cur, start, end, now)
              if out["loop_ready"] else None),
             ("people", lambda: people_between(cur, start, end)),
             ("people_7d", lambda: people_between(
                 cur, end - timedelta(days=7), end)),
             ("infra", lambda: infra_between(cur, now, costs)))
    for name, fn in parts:
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = None
            out.setdefault("errors", {})[name] = \
                f"{type(e).__name__}: {e}"[:200]
    return out


def today(cur, now=None, costs=None):
    """Awareness for today so far (admin timezone): the top of the admin."""
    now = now or ranges.utc_now()
    start = ranges.local_midnight(ranges.local_today(now))
    out = awareness(cur, start, now, now, costs=costs)
    out["day"] = ranges.local_today(now).isoformat()
    return out


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
