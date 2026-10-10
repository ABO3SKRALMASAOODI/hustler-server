"""Is the product working for customers? (plan §2.9, endpoints 25–26).

Every count here is customers-only by default (G2). The owner's production
work (98% of jobs) is added only with include_owner=1, so a quiet customer
day is never hidden behind the owner's own renders.
"""
import re

from admin_metrics import defs, projects, ranges, registry

UPLOAD_FAILURE_KINDS = ("upload_rejected", "upload_failed")


def reason_key(raw):
    """Plain grouping for upload failure reasons (raw text varies by client)."""
    r = (raw or "").strip().lower()
    if not r:
        return "unknown"
    if "size cap" in r or "larger than" in r or "too large" in r:
        return "too_large"
    if "duration" in r or "too long" in r:
        return "too_long"
    if "empty" in r or "incomplete" in r or "entitytoosmall" in r:
        return "empty_file"
    if "unsupported" in r or "type" in r and "file" not in r:
        return "unsupported_type"
    if "size missing" in r or "invalid" in r:
        return "size_missing"
    if "network" in r or "vpn" in r or "blocking" in r or "reach our storage" in r:
        return "network_blocked"
    if "timed out" in r or "timeout" in r:
        return "timed_out"
    if "cancel" in r:
        return "cancelled"
    return "other"


REASON_LABELS = {
    "too_large": "File too large", "too_long": "Video too long",
    "empty_file": "Empty or incomplete file",
    "unsupported_type": "Unsupported file type",
    "size_missing": "File size missing", "network_blocked":
    "A network filter, VPN or extension blocked it",
    "timed_out": "Upload timed out", "cancelled": "Cancelled by the person",
    "other": "Other error", "unknown": "No reason recorded",
}


def plain_error(text, limit=300):
    """First meaningful line of an error, never a stack trace."""
    if not text:
        return None
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(("Traceback", "File \""))]
    first = lines[-1] if lines and lines[0].startswith("Traceback") else \
        (lines[0] if lines else "")
    first = re.sub(r"\s+", " ", first)
    return first[:limit] or None


def engine(cur, include_owner=False):
    cur.execute("""
        SELECT count(*) FILTER (WHERE state = 'queued') AS queued,
               count(*) FILTER (WHERE state = 'running') AS running,
               EXTRACT(EPOCH FROM (NOW() - min(created_at) FILTER (
                   WHERE state = 'queued'))) AS oldest_queued_s
          FROM video_jobs WHERE state IN ('queued', 'running')""")
    q = cur.fetchone() or {}
    cur.execute("""SELECT max(GREATEST(updated_at, heartbeat_at)) AS t
                     FROM video_jobs
                    WHERE updated_at > NOW() - INTERVAL '2 days'""")
    last = (cur.fetchone() or {}).get("t")
    cur.execute(f"""
        SELECT count(*) AS n FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE vj.state IN ('queued','running') AND {projects.STUCK}
           AND {defs.population('u', include_owner)}""")
    stuck = int(cur.fetchone()["n"])
    queued, running = int(q.get("queued") or 0), int(q.get("running") or 0)
    oldest = q.get("oldest_queued_s")
    oldest = int(oldest) if oldest is not None else None
    from datetime import datetime, timezone
    idle_s = (datetime.now(timezone.utc) - last).total_seconds() if last \
        else None
    if queued and (oldest or 0) > 600 and not running:
        status, text = "critical", (f"Waiting: {queued} queued for over "
                                    f"{(oldest or 0) // 60} min, nothing "
                                    "running")
    elif queued and idle_s is not None and idle_s > 1800:
        status, text = "serious", (f"Idle for {int(idle_s // 60)} min with "
                                   f"{queued} queued")
    elif running or (idle_s is not None and idle_s <= 1800):
        status, text = "good", (f"Working: {running} running, {queued} queued"
                                if running or queued else "Working")
    else:
        status, text = "good", "Idle: nothing to do"
    return {"status": status, "text": text, "last_activity_at": defs.iso(last),
            "queued": queued, "running": running, "oldest_queued_s": oldest,
            "stuck": stuck}


def _failures_where(include_owner):
    return f"""vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s
               AND {defs.population('u', include_owner)}"""


def failures(cur, period, include_owner=False, recent_limit=20):
    where = _failures_where(include_owner)
    cur.execute(f"""
        SELECT vj.type,
               count(*) AS total,
               count(*) FILTER (WHERE {projects.UNSUPERSEDED_FAILED}) AS failed,
               count(DISTINCT vj.user_id) FILTER (
                   WHERE {projects.UNSUPERSEDED_FAILED}) AS people
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE vj.type IN ({projects.ACTIONABLE}) AND {where}
         GROUP BY 1""", period.params())
    by_type = []
    total_failed = 0
    for r in cur.fetchall():
        total_failed += int(r["failed"])
        by_type.append({"type": r["type"],
                        "label": defs.JOB_LABELS.get(r["type"], r["type"]),
                        "failed": int(r["failed"]), "total": int(r["total"]),
                        "people": int(r["people"])})
    order = {t: i for i, t in enumerate(defs.USER_FACING_JOB_TYPES)}
    by_type.sort(key=lambda r: order.get(r["type"], 99))
    cur.execute(f"""
        SELECT count(DISTINCT vj.user_id) AS people
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE {where} AND {projects.UNSUPERSEDED_FAILED}""", period.params())
    people = int(cur.fetchone()["people"])
    from admin_metrics import money
    cur.execute(f"""
        SELECT vj.id, vj.type, vj.project_id, vj.updated_at, vj.error,
               u.id AS user_id, u.email, COALESCE(u.billing_plan, u.plan)
                   AS plan, {money.STATUS_SQL} AS status
          FROM video_jobs vj JOIN users u ON u.id = vj.user_id
         WHERE {where} AND {projects.UNSUPERSEDED_FAILED}
         ORDER BY vj.updated_at DESC LIMIT %(limit)s""",
                {**period.params(), "limit": recent_limit})
    recent = [{"job_id": r["id"], "type": r["type"],
               "label": defs.JOB_FAILED_LABELS.get(r["type"], "Failed"),
               "project_id": r["project_id"],
               "customer": {"id": r["user_id"], "email": r["email"],
                            "plan": r["plan"], "status": r["status"]},
               "at": defs.iso(r["updated_at"]),
               "error": plain_error(r["error"])} for r in cur.fetchall()]
    metric = registry.metric(
        "customer_failures", total_failed,
        breakdown=[registry.item("people_affected", people)])
    return {"metric": metric, "by_type": by_type, "recent": recent}


MSG_NO_EDIT = f"""
    SELECT cm.id, cm.created_at, p.id AS project_id, u.id AS user_id, u.email,
           COALESCE(u.billing_plan, u.plan) AS plan,
           {defs.paying('u')} AS paying,
           (SELECT r.meta->>'kind' FROM chat_messages r
             WHERE r.session_id = cm.session_id AND r.id > cm.id
               AND r.role = 'assistant' ORDER BY r.id LIMIT 1) AS reply_kind
      FROM chat_messages cm
      JOIN projects p ON p.chat_session_id = cm.session_id
      JOIN users u ON u.id = p.user_id
     WHERE cm.role = 'user' AND {{population}}
       AND cm.created_at >= %(start)s AND cm.created_at < %(end)s
       AND cm.created_at < (NOW() AT TIME ZONE 'UTC') - INTERVAL '15 minutes'
       AND NOT EXISTS (
           SELECT 1 FROM video_jobs vj WHERE vj.project_id = p.id
              AND vj.type IN ('agent_turn','mcp_tool','preview','shorts_plan')
              AND vj.created_at >= cm.created_at AT TIME ZONE 'UTC'
              AND vj.created_at < (cm.created_at + INTERVAL '15 minutes')
                                  AT TIME ZONE 'UTC')"""


def messages_without_edit(cur, period, include_owner=False, recent_limit=20):
    sql = MSG_NO_EDIT.format(population=defs.population("u", include_owner))
    cur.execute(sql + " ORDER BY cm.id DESC", period.params())
    rows = [dict(r) for r in cur.fetchall()]
    from admin_metrics import money
    status = {}
    if rows:
        cur.execute(f"""SELECT u.id, {money.STATUS_SQL} AS status FROM users u
                         WHERE u.id = ANY(%s)""",
                    (list({r['user_id'] for r in rows}),))
        status = {r["id"]: r["status"] for r in cur.fetchall()}
    return {
        "paying": sum(1 for r in rows if r["paying"]),
        "free": sum(1 for r in rows if not r["paying"]),
        "people": len({r["user_id"] for r in rows}),
        "rows": rows,
        "recent": [{"message_id": r["id"], "project_id": r["project_id"],
                    "customer": {"id": r["user_id"], "email": r["email"],
                                 "plan": r["plan"],
                                 "status": status.get(r["user_id"], "free")},
                    "at": defs.iso(r["created_at"]),
                    "reply_kind": r["reply_kind"]}
                   for r in rows[:recent_limit]],
    }


def uploads(cur, period, include_owner=False, recent_limit=20):
    pop = defs.population("u", include_owner)
    cur.execute(f"""
        SELECT count(*) FILTER (WHERE ce.kind = 'upload_started') AS started,
               count(*) FILTER (WHERE ce.kind = 'upload_landed') AS landed,
               count(DISTINCT ce.user_id) FILTER (
                   WHERE ce.kind IN ('upload_rejected','upload_failed'))
                   AS failed_people
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('upload_started','upload_landed','upload_rejected',
                           'upload_failed')
           AND ce.created_at >= %(start_tz)s AND ce.created_at < %(end_tz)s
           AND {pop}""", period.params())
    t = cur.fetchone() or {}
    from admin_metrics import money
    cur.execute(f"""
        SELECT ce.created_at, ce.detail->>'reason' AS reason,
               CASE WHEN (ce.detail->>'bytes') ~ '^[0-9]+$'
                    THEN (ce.detail->>'bytes')::bigint END AS bytes,
               u.id AS user_id, u.email,
               COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('upload_rejected','upload_failed')
           AND ce.created_at >= %(start_tz)s AND ce.created_at < %(end_tz)s
           AND {pop}
         ORDER BY ce.created_at DESC""", period.params())
    rows = [dict(r) for r in cur.fetchall()]
    reasons = {}
    for r in rows:
        k = reason_key(r["reason"])
        reasons[k] = reasons.get(k, 0) + 1
    return {
        "started": int(t.get("started") or 0),
        "landed": int(t.get("landed") or 0),
        "failed_people": int(t.get("failed_people") or 0),
        "reasons": [{"reason": k, "label": REASON_LABELS[k], "count": n}
                    for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])],
        "recent": [{"at": defs.iso(r["created_at"]),
                    "customer": {"id": r["user_id"], "email": r["email"],
                                 "plan": r["plan"], "status": r["status"]},
                    "reason_label": REASON_LABELS[reason_key(r["reason"])],
                    "file_size_bytes": r["bytes"]}
                   for r in rows[:recent_limit]],
    }


EXPORT_STAGES = (
    ("export_clicked", "Clicked export"),
    ("export_job_started", "Export started"),
    ("export_render_done", "Render done"),
    ("download_url_ready", "Download link ready"),
    ("download_triggered", "Download started"),
)


def export_steps(cur, period, include_owner=False):
    pop = defs.population("u", include_owner)
    kinds = [k for k, _ in EXPORT_STAGES]
    cur.execute(f"""
        SELECT ce.kind, count(*) AS events, count(DISTINCT ce.user_id) AS people,
               count(DISTINCT ce.project_id) FILTER (
                   WHERE ce.project_id IS NOT NULL) AS projects
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = ANY(%(kinds)s)
           AND ce.created_at >= %(start_tz)s AND ce.created_at < %(end_tz)s
           AND {pop}
         GROUP BY 1""", {**period.params(), "kinds": kinds})
    by = {r["kind"]: r for r in cur.fetchall()}
    cur.execute(f"""
        SELECT ce.kind AS stage,
               COALESCE(NULLIF(ce.detail->>'code',''),
                        NULLIF(ce.detail->>'reason',''), '(unspecified)')
                   AS reason, count(*) AS n
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('export_blocked','download_failed')
           AND ce.created_at >= %(start_tz)s AND ce.created_at < %(end_tz)s
           AND {pop}
         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20""", period.params())
    return {"stages": [{"key": k, "label": label,
                        "events": int((by.get(k) or {}).get("events") or 0),
                        "people": int((by.get(k) or {}).get("people") or 0),
                        "projects": int((by.get(k) or {}).get("projects") or 0)}
                       for k, label in EXPORT_STAGES],
            "failures": [{"stage": r["stage"], "reason": str(r["reason"])[:120],
                          "count": int(r["n"])} for r in cur.fetchall()]}


def wait_points(cur, period, include_owner=False, limit=1000):
    from admin_metrics import money
    cur.execute(f"""
        SELECT p.id, p.created_at, u.id AS user_id, u.email,
               COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status, {projects.TIMING_COLS}
          FROM projects p JOIN users u ON u.id = p.user_id
          {projects.PROJECT_TIMINGS}
         WHERE p.created_at >= %(start_tz)s AND p.created_at < %(end_tz)s
           AND {defs.population('u', include_owner)}
           AND o.duration_s IS NOT NULL
         ORDER BY p.id DESC LIMIT %(limit)s""",
                {**period.params(), "limit": limit + 1})
    rows = [dict(r) for r in cur.fetchall()]
    truncated = len(rows) > limit
    return rows[:limit], truncated


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return round(values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2,
                 1)


def waits(cur, period, include_owner=False):
    rows, truncated = wait_points(cur, period, include_owner)
    points = []
    up, an, ed = [], [], []
    for r in rows:
        t = projects.timing_out(r)
        ref = {"id": r["user_id"], "email": r["email"], "plan": r["plan"],
               "status": r["status"]}
        base = {"project_id": r["id"], "video_s": t["duration_s"],
                "at": defs.iso(r["created_at"]), "customer_id": r["user_id"]}
        if t["upload_s"] is not None:
            points.append({**base, "stage": "upload", "wait_s": t["upload_s"],
                           "estimated": not t["upload_measured"],
                           "reused": False, "_c": ref})
            if t["upload_measured"]:
                up.append(t["upload_s"])
        if t["index_s"] is not None:
            points.append({**base, "stage": "analysis", "wait_s": t["index_s"],
                           "estimated": False, "reused": t["index_cached"],
                           "_c": ref})
            if not t["index_cached"]:
                an.append(t["index_s"])
        if t["edit_s"] is not None:
            points.append({**base, "stage": "edit", "wait_s": t["edit_s"],
                           "estimated": False, "reused": False, "_c": ref})
            ed.append(t["edit_s"])
    worst = sorted((p for p in points if not p["estimated"]
                    and not p["reused"]),
                   key=lambda p: -(p["wait_s"] or 0))[:20]
    return {
        "points": [{k: v for k, v in p.items() if k != "_c"} for p in points],
        "medians": {"upload_s": _median(up), "analysis_s": _median(an),
                    "edit_s": _median(ed)},
        "worst": [{"project_id": p["project_id"], "stage": p["stage"],
                   "wait_s": p["wait_s"], "video_s": p["video_s"],
                   "customer": p["_c"]} for p in worst],
        "truncated": truncated,
    }


def projects_exported_share(cur, include_owner=False):
    cur.execute(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE {projects.exported('p')}) AS exported
          FROM projects p JOIN users u ON u.id = p.user_id
         WHERE p.parent_project_id IS NULL
           AND p.created_at >= NOW() - INTERVAL '7 days'
           AND {defs.population('u', include_owner)}""")
    r = cur.fetchone() or {}
    total, done = int(r.get("total") or 0), int(r.get("exported") or 0)
    value = defs.pct(done, total)
    return registry.metric(
        "projects_exported_share", value,
        note=(f"{done} of {total} projects created in the last 7 days."
              if total else "No customer projects in the last 7 days."),
        status="ok" if total else "unavailable",
        breakdown=[{"key": "exported", "label": "Exported", "value": done},
                   {"key": "projects", "label": "Projects", "value": total}])


def health_page(cur, period, include_owner=False):
    w = waits(cur, period, include_owner)
    m = messages_without_edit(cur, period, include_owner)
    return {
        "engine": engine(cur, include_owner),
        "failures": failures(cur, period, include_owner),
        "messages_without_edit": {"paying": m["paying"], "free": m["free"],
                                  "recent": m["recent"]},
        "uploads": uploads(cur, period, include_owner),
        "export_steps": export_steps(cur, period, include_owner),
        "waits": {"upload_median_s": w["medians"]["upload_s"],
                  "analysis_median_s": w["medians"]["analysis_s"],
                  "edit_median_s": w["medians"]["edit_s"],
                  "points": len(w["points"])},
        "projects_exported_share": projects_exported_share(cur, include_owner),
    }


def _n(count, singular, plural=None):
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _range_words(period):
    return {"today": "today", "yesterday": "yesterday"}.get(
        period.key, "in this period")


def health_items(cur, period, money_failing=None, failed_payments=None):
    """The five Today health pills, customers only (plan §6.6 thresholds)."""
    words = _range_words(period)
    r = period.key
    items = []
    f = failures(cur, period, recent_limit=0)
    n = f["metric"]["value"] or 0
    people = f["metric"]["breakdown"][0]["value"]
    d = registry.definition("customer_failures")
    items.append({"key": "customer_failures", "label": d["label"],
                  "how": d["how"], "value": n,
                  "status": "good" if n == 0 else
                  ("warning" if n < 5 else "critical"),
                  "text": (f"{_n(n, 'failure')} ({_n(people, 'person', 'people')}) "
                           f"{words}" if n else f"None {words}"),
                  "href": f"/admin/health?range={r}#failures"})
    u = uploads(cur, period, recent_limit=0)
    n = u["failed_people"]
    d = registry.definition("failed_uploads")
    items.append({"key": "failed_uploads", "label": d["label"],
                  "how": d["how"], "value": n,
                  "status": "good" if n == 0 else
                  ("warning" if n < 5 else "critical"),
                  "text": (f"{_n(n, 'person', 'people')} {words}"
                           if n else f"None {words}"),
                  "href": f"/admin/health?range={r}#uploads"})
    failing = money_failing if money_failing is not None else 0
    failed = failed_payments if failed_payments is not None else 0
    n = failing + failed
    d = registry.definition("payment_problems")
    items.append({"key": "payment_problems", "label": d["label"],
                  "how": d["how"], "value": n,
                  "status": "good" if n == 0 else "warning",
                  "text": (f"{failing} failing now, "
                           f"{_n(failed, 'failed charge')} {words}"
                           if n else "None"),
                  "href": f"/admin/revenue?range={r}#payments"})
    m = messages_without_edit(cur, period, recent_limit=0)
    n = m["paying"] + m["free"]
    d = registry.definition("messages_without_edit")
    items.append({"key": "messages_without_edit", "label": d["label"],
                  "how": d["how"], "value": n,
                  "status": "critical" if m["paying"] else
                  ("warning" if m["free"] else "good"),
                  "text": (f"{_n(n, 'message')} ({m['paying']} from paying "
                           f"customers) {words}" if n else f"None {words}"),
                  "href": f"/admin/health?range={r}#messages"})
    e = engine(cur)
    d = registry.definition("editing_engine")
    items.append({"key": "editing_engine", "label": d["label"],
                  "how": d["how"], "value": e["queued"],
                  "status": e["status"], "text": e["text"],
                  "href": "/admin/health#engine"})
    return items
