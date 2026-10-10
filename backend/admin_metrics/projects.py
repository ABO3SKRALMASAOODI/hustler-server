"""Projects list, the bounded project page and its on-demand tabs (R1, R2).

The old project page sent a project's whole history in one response (up to
108 MB: the same chat activity copied once per agent slice, every job's
payload and result, every version's EDL). Here the page returns a summary
with every body referenced, never duplicated, and each heavy part loads only
when its tab is opened. A hard 4 MB cap drops the largest section with a
reason rather than ever sending more.
"""
import json

from admin_metrics import defs, money

# ── Waits (shared with /admin/video/projects and /timings) ───────────────
# The three waits a customer experiences per project. LATERALs pick a
# specific row (ORDER BY ... LIMIT 1) so start and end belong to one job.
PROJECT_TIMINGS = """
    LEFT JOIN LATERAL (
        SELECT a.id, a.duration_s, a.bytes, a.width, a.height, a.created_at
        FROM assets a
        WHERE a.project_id = p.id AND a.kind = 'original'
        ORDER BY a.id ASC LIMIT 1) o ON TRUE
    LEFT JOIN LATERAL (
        SELECT ce.created_at
        FROM client_events ce
        WHERE ce.project_id = p.id AND ce.kind = 'upload_started'
          AND ce.created_at <= o.created_at
        ORDER BY (ce.detail->>'bytes' ~ '^[0-9]+$'
                  AND (ce.detail->>'bytes')::bigint = o.bytes) DESC NULLS LAST,
                 ce.id DESC
        LIMIT 1) ue ON TRUE
    LEFT JOIN LATERAL (
        SELECT vj.created_at AS t0, vj.updated_at AS t1,
               (vj.result->>'cached') AS cached
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'index' AND vj.state = 'done'
        ORDER BY vj.id ASC LIMIT 1) ij ON TRUE
    LEFT JOIN LATERAL (
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (vj.updated_at - vj.created_at))
               ) AS med,
               COUNT(*) AS n
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'preview'
          AND vj.state = 'done' AND vj.updated_at > vj.created_at) pv ON TRUE
    LEFT JOIN LATERAL (
        SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY EXTRACT(EPOCH FROM (vj.updated_at - vj.created_at))
               ) AS med,
               COUNT(*) AS n
        FROM video_jobs vj
        WHERE vj.project_id = p.id AND vj.type = 'agent_turn'
          AND vj.state = 'done' AND vj.updated_at > vj.created_at) ag ON TRUE
"""

TIMING_COLS = """
    o.duration_s AS duration_s, o.bytes AS source_bytes,
    o.width AS width, o.height AS height,
    EXTRACT(EPOCH FROM (o.created_at
                        - COALESCE(ue.created_at, p.created_at))) AS upload_s,
    (ue.created_at IS NOT NULL) AS upload_measured,
    EXTRACT(EPOCH FROM (ij.t1 - ij.t0)) AS index_s,
    (ij.cached = 'true') AS index_cached,
    pv.med AS edit_s, pv.n AS previews,
    ag.med AS turn_s, ag.n AS turns
"""


def _secs(v):
    """Seconds rounded; a clock running backwards is not a measurement."""
    if v is None:
        return None
    v = float(v)
    return round(v, 1) if v >= 0 else None


def timing_out(r):
    return {
        "duration_s": round(float(r["duration_s"]), 1) if r["duration_s"] else None,
        "source_bytes": r["source_bytes"],
        "width": r["width"], "height": r["height"],
        "upload_s": _secs(r["upload_s"]),
        # False = derived from the project's creation time (no upload-start
        # event): an upper bound that includes choosing the file.
        "upload_measured": bool(r["upload_measured"]),
        "index_s": _secs(r["index_s"]),
        "index_cached": bool(r["index_cached"]),
        "edit_s": _secs(r["edit_s"]), "previews": r["previews"] or 0,
        "turn_s": _secs(r["turn_s"]), "turns": r["turns"] or 0,
    }


# ── Problems: failed user-facing work with no newer attempt, stuck work ──
ACTIONABLE = ", ".join(f"'{t}'" for t in defs.USER_FACING_JOB_TYPES)
REPLACEMENT_STATES = "'queued', 'running', 'done', 'failed'"

UNSUPERSEDED_FAILED = f"""vj.state = 'failed' AND vj.type IN ({ACTIONABLE})
    AND NOT EXISTS (
      SELECT 1 FROM video_jobs newer
       WHERE newer.project_id = vj.project_id AND newer.id > vj.id
         AND newer.type IN ({ACTIONABLE})
         AND newer.state IN ({REPLACEMENT_STATES})
         AND (
           ((vj.type = 'preview' AND newer.type IN ('preview','final'))
             OR (vj.type = 'final' AND newer.type = 'final'))
           AND COALESCE(CASE WHEN (newer.payload->>'edl_version') ~ '^[0-9]+$'
                             THEN (newer.payload->>'edl_version')::int END, -1)
               >= COALESCE(CASE WHEN (vj.payload->>'edl_version') ~ '^[0-9]+$'
                                THEN (vj.payload->>'edl_version')::int END, -1)
           OR (vj.type = 'agent_turn' AND (vj.payload->>'message_id') IS NOT NULL
               AND newer.type = 'agent_turn'
               AND newer.payload->>'message_id' = vj.payload->>'message_id')
           OR (vj.type = 'index' AND (vj.payload->>'asset_id') IS NOT NULL
               AND newer.type = 'index'
               AND newer.payload->>'asset_id' = vj.payload->>'asset_id')
           OR (vj.type = 'shorts_plan' AND newer.type = 'shorts_plan')))"""

STUCK = f"""vj.state IN ('queued', 'running') AND vj.type IN ({ACTIONABLE})
    AND ((vj.heartbeat_at IS NULL AND vj.created_at < NOW() - INTERVAL '10 minutes')
         OR vj.heartbeat_at < NOW() - INTERVAL '10 minutes')"""

FAMILY = """(SELECT {p}.id UNION ALL SELECT fc.id FROM projects fc
              WHERE fc.parent_project_id = {p}.id)"""


def family(p="p"):
    return FAMILY.format(p=p)


def has_problems(p="p"):
    return (f"EXISTS (SELECT 1 FROM video_jobs vj WHERE vj.project_id IN "
            f"{family(p)} AND (({UNSUPERSEDED_FAILED}) OR ({STUCK})))")


def exported(p="p"):
    return (f"EXISTS (SELECT 1 FROM video_jobs ej WHERE ej.project_id IN "
            f"{family(p)} AND ej.type = 'final' AND ej.state = 'done')")


LAST_ACTIVITY = """GREATEST(
    (SELECT max(lj.updated_at) FROM video_jobs lj WHERE lj.project_id = {p}.id),
    (SELECT lm.created_at AT TIME ZONE 'UTC' FROM chat_messages lm
      WHERE lm.session_id = {p}.chat_session_id ORDER BY lm.id DESC LIMIT 1),
    (SELECT max(la.created_at) FROM assets la WHERE la.project_id = {p}.id),
    {p}.created_at)"""

FILTERS = ("customers", "paying", "mine", "problems", "not_exported", "all")
SORTS = {"created": "p.created_at", "last_activity": "last_activity_at",
         "messages": "messages", "video_length": "duration_s"}
DATE_FIELDS = ("created", "active", "exported")


class BadRequest(ValueError):
    pass


def _paying_owner(u="u"):
    return (f"(({defs.paying(u)} OR {defs.failing(u)}) AND EXISTS ("
            f"SELECT 1 FROM payments sp WHERE sp.user_id = {u}.id "
            f"AND {defs.success('sp')}))")


# Sets of PARENT project ids, built job-first so the job indexes do the work
# (state/type and updated_at) instead of probing every project's family.
SETS = f"""
    bad_parents AS MATERIALIZED (
      SELECT COALESCE(bp.parent_project_id, bp.id) AS id
        FROM video_jobs vj JOIN projects bp ON bp.id = vj.project_id
       WHERE vj.state = 'failed' AND {UNSUPERSEDED_FAILED}
      UNION
      SELECT COALESCE(bp.parent_project_id, bp.id)
        FROM video_jobs vj JOIN projects bp ON bp.id = vj.project_id
       WHERE {STUCK}),
    exported_parents AS MATERIALIZED (
      SELECT DISTINCT COALESCE(xp.parent_project_id, xp.id) AS id
        FROM video_jobs ej JOIN projects xp ON xp.id = ej.project_id
       WHERE ej.state = 'done' AND ej.type = 'final')"""

RANGE_SETS = {
    "exported": """
    range_parents AS MATERIALIZED (
      SELECT DISTINCT COALESCE(rp.parent_project_id, rp.id) AS id
        FROM video_jobs ej JOIN projects rp ON rp.id = ej.project_id
       WHERE ej.type = 'final' AND ej.state = 'done'
         AND ej.updated_at >= %(start_tz)s AND ej.updated_at < %(end_tz)s)""",
    "active": """
    range_parents AS MATERIALIZED (
      SELECT COALESCE(rp.parent_project_id, rp.id) AS id
        FROM video_jobs aj JOIN projects rp ON rp.id = aj.project_id
       WHERE aj.updated_at >= %(start_tz)s AND aj.updated_at < %(end_tz)s
      UNION
      SELECT COALESCE(rp.parent_project_id, rp.id)
        FROM chat_messages am JOIN projects rp
          ON rp.chat_session_id = am.session_id
       WHERE am.created_at >= %(start)s AND am.created_at < %(end)s
      UNION
      SELECT rp.id FROM projects rp
       WHERE rp.created_at >= %(start_tz)s AND rp.created_at < %(end_tz)s)""",
}


def _filter_sql(f, include_owner):
    if f == "customers":
        return defs.customer("u")
    if f == "paying":
        return f"({defs.customer('u')} AND {_paying_owner('u')})"
    if f == "mine":
        return defs.owner("u")
    pop = defs.population("u", include_owner)
    if f == "problems":
        return f"({pop} AND p.id IN (SELECT id FROM bad_parents))"
    if f == "not_exported":
        return f"({pop} AND p.id NOT IN (SELECT id FROM exported_parents))"
    return pop


def _common_where(period, args, params):
    """(extra CTEs, WHERE fragments) shared by the list and its chip counts."""
    where = ["p.parent_project_id IS NULL"]
    ctes = [SETS]
    date_field = (args.get("date_field") or "created").strip()
    if date_field not in DATE_FIELDS:
        raise BadRequest("date_field must be created, active or exported.")
    if not period.is_all:
        if date_field == "created":
            where.append("p.created_at >= %(start_tz)s "
                         "AND p.created_at < %(end_tz)s")
        else:
            ctes.append(RANGE_SETS[date_field])
            where.append("p.id IN (SELECT id FROM range_parents)")
    q = (args.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("p.id = %(q_id)s")
            params["q_id"] = int(q)
        else:
            where.append("(p.title ILIKE %(q_like)s OR u.email ILIKE %(q_like)s)")
            params["q_like"] = f"%{q[:120]}%"
    cid = args.get("customer_id")
    if cid not in (None, ""):
        try:
            params["customer_id"] = int(cid)
        except (TypeError, ValueError):
            raise BadRequest("customer_id must be a number.")
        where.append("p.user_id = %(customer_id)s")
    return ",".join(ctes), where


def list_projects(cur, period, args, page, per_page):
    f = (args.get("filter") or "customers").strip()
    if f not in FILTERS:
        raise BadRequest("filter must be one of " + ", ".join(FILTERS) + ".")
    include_owner = str(args.get("include_owner") or "0") == "1"
    sort = (args.get("sort") or "created").strip()
    if sort not in SORTS:
        raise BadRequest("sort must be created, last_activity, messages or "
                         "video_length.")
    direction = "ASC" if (args.get("dir") or "desc").lower() == "asc" \
        else "DESC"
    params = period.params()
    ctes, where = _common_where(period, args, params)
    common = " AND ".join(where)
    pop = defs.population("u", include_owner)

    # Chip counts share every filter except the chip itself.
    cur.execute(f"""
        WITH {ctes}
        SELECT count(*) FILTER (WHERE {defs.customer('u')}) AS customers,
               count(*) FILTER (WHERE {defs.customer('u')}
                                  AND {_paying_owner('u')}) AS paying,
               count(*) FILTER (WHERE {defs.owner('u')}) AS mine,
               count(*) FILTER (WHERE {pop} AND bad.id IS NOT NULL)
                   AS problems,
               count(*) FILTER (WHERE {pop} AND ex.id IS NULL) AS not_exported,
               count(*) FILTER (WHERE {pop}) AS all_projects
          FROM projects p JOIN users u ON u.id = p.user_id
          LEFT JOIN bad_parents bad ON bad.id = p.id
          LEFT JOIN exported_parents ex ON ex.id = p.id
         WHERE {common} AND ({defs.customer('u')} OR {defs.owner('u')})""",
                params)
    c = cur.fetchone() or {}
    counts = {k: int(c.get(k) or 0) for k in
              ("customers", "paying", "mine", "problems", "not_exported")}
    total = int(c.get("all_projects") or 0) if f == "all" else counts[f]

    order = SORTS[sort]
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    params.update(limit=per_page, offset=(page - 1) * per_page)
    cur.execute(f"""
        WITH {ctes}, page AS MATERIALIZED (
          SELECT p.*, u.email, u.plan AS user_plan, u.billing_plan,
                 {money.STATUS_SQL} AS status,
                 (SELECT o.duration_s FROM assets o WHERE o.project_id = p.id
                     AND o.kind = 'original' ORDER BY o.id LIMIT 1)
                     AS duration_s,
                 (SELECT count(*) FROM chat_messages cm
                    JOIN projects fm ON fm.chat_session_id = cm.session_id
                   WHERE fm.id IN {family('p')} AND cm.role = 'user')
                     AS messages,
                 {LAST_ACTIVITY.format(p='p')} AS last_activity_at
            FROM projects p JOIN users u ON u.id = p.user_id
           WHERE {common} AND {_filter_sql(f, include_owner)}
           ORDER BY {order} {direction} {nulls}, p.id DESC
           LIMIT %(limit)s OFFSET %(offset)s)
        SELECT p.id, p.title, p.kind, p.created_at, p.user_id, p.email,
               COALESCE(p.billing_plan, p.user_plan) AS plan, p.status,
               p.messages, p.last_activity_at,
               (SELECT count(*) FROM projects ch
                 WHERE ch.parent_project_id = p.id) AS shorts,
               (SELECT count(*) FROM edls e WHERE e.project_id = p.id)
                   AS versions,
               (SELECT count(*) FROM video_jobs ej
                 WHERE ej.project_id IN {family('p')} AND ej.type = 'final'
                   AND ej.state = 'done') AS exports,
               (SELECT count(*) FROM video_jobs vj
                 WHERE vj.project_id IN {family('p')}
                   AND {UNSUPERSEDED_FAILED}) AS failed_jobs,
               (SELECT count(*) FROM video_jobs vj
                 WHERE vj.project_id IN {family('p')} AND {STUCK})
                   AS stuck_jobs,
               (SELECT count(*) FROM client_events ce
                 WHERE ce.project_id IN {family('p')}
                   AND ce.kind IN ('trial_gate_shown',
                                   'subscription_upload_locked'))
                   AS paywall_hits,
               {TIMING_COLS},
               {led_to_payment_sql('p')} AS led_to_payment
          FROM page p {PROJECT_TIMINGS}
         ORDER BY {order} {direction} {nulls}, p.id DESC""", params)
    rows = []
    for r in cur.fetchall():
        t = timing_out(r)
        rows.append({
            "id": r["id"], "title": r["title"], "kind": kind(r["kind"]),
            "created_at": defs.iso(r["created_at"]),
            "customer": {"id": r["user_id"], "email": r["email"],
                         "plan": r["plan"], "status": r["status"]},
            "video_length_s": t["duration_s"],
            "upload_wait_s": t["upload_s"],
            "upload_wait_estimated": not t["upload_measured"]
            if t["upload_s"] is not None else False,
            "analysis_wait_s": t["index_s"],
            "analysis_reused": t["index_cached"],
            "edit_wait_median_s": t["edit_s"],
            "messages": int(r["messages"] or 0),
            "shorts": int(r["shorts"] or 0),
            "versions": int(r["versions"] or 0),
            "exports": int(r["exports"] or 0),
            "problems": {"failed_jobs": int(r["failed_jobs"] or 0),
                         "stuck_jobs": int(r["stuck_jobs"] or 0)},
            "paywall_hits": int(r["paywall_hits"] or 0),
            "led_to_payment": bool(r["led_to_payment"]),
            "last_activity_at": defs.iso(r["last_activity_at"]),
        })
    return {"rows": rows, "page": page, "per_page": per_page, "total": total,
            "counts": counts}


def kind(value):
    return "shorts" if (value or "").startswith("short") else "edit"


def led_to_payment_sql(p="p"):
    """This was the customer's last project before their first payment."""
    return f"""COALESCE((
        WITH first_pay AS (
          SELECT min(COALESCE(fp.occurred_at, fp.created_at)) AS at
            FROM payments fp WHERE fp.user_id = {p}.user_id
             AND {defs.success('fp')})
        SELECT {p}.id = COALESCE(
            (SELECT ce.project_id FROM client_events ce, first_pay
              WHERE ce.user_id = {p}.user_id AND ce.project_id IS NOT NULL
                AND ce.kind IN ('trial_gate_shown','subscription_upload_locked')
                AND ce.created_at <= (first_pay.at AT TIME ZONE 'UTC')
              ORDER BY ce.created_at DESC, ce.id DESC LIMIT 1),
            (SELECT lp.id FROM projects lp, first_pay
              WHERE lp.user_id = {p}.user_id AND lp.parent_project_id IS NULL
                AND lp.created_at <= (first_pay.at AT TIME ZONE 'UTC')
              ORDER BY lp.created_at DESC, lp.id DESC LIMIT 1))
          FROM first_pay WHERE first_pay.at IS NOT NULL), FALSE)"""


def customer_projects(cur, user_id, limit=100):
    """The newest projects of one customer, for the customer page."""
    cur.execute(f"""
        SELECT p.id, p.title, p.created_at,
               (SELECT o.duration_s FROM assets o WHERE o.project_id = p.id
                   AND o.kind = 'original' ORDER BY o.id LIMIT 1) AS duration_s,
               (SELECT count(*) FROM chat_messages cm
                  JOIN projects fm ON fm.chat_session_id = cm.session_id
                 WHERE fm.id IN {family('p')} AND cm.role = 'user') AS messages,
               (SELECT count(*) FROM edls e WHERE e.project_id = p.id)
                   AS versions,
               (SELECT count(*) FROM video_jobs ej
                 WHERE ej.project_id IN {family('p')} AND ej.type = 'final'
                   AND ej.state = 'done') AS exports,
               (SELECT count(*) FROM video_jobs vj
                 WHERE vj.project_id IN {family('p')}
                   AND (({UNSUPERSEDED_FAILED}) OR ({STUCK}))) AS problems,
               {LAST_ACTIVITY.format(p='p')} AS last_activity_at
          FROM projects p
         WHERE p.user_id = %s AND p.parent_project_id IS NULL
         ORDER BY p.id DESC LIMIT %s""", (user_id, limit))
    return [{"id": r["id"], "title": r["title"],
             "created_at": defs.iso(r["created_at"]),
             "video_length_s": round(float(r["duration_s"]), 1)
             if r["duration_s"] else None,
             "messages": int(r["messages"] or 0),
             "versions": int(r["versions"] or 0),
             "exports": int(r["exports"] or 0),
             "problems": int(r["problems"] or 0),
             "last_activity_at": defs.iso(r["last_activity_at"])}
            for r in cur.fetchall()]


# ── The bounded project page ─────────────────────────────────────────────
MESSAGE_PAGE = 300
TEXT_MAX = 8000
JOB_LIMIT = 500
VERSION_LIMIT = 500
ERROR_MAX = 500
SIZE_CAP = 4 * 1024 * 1024
META_KEYS = ("edl_version", "tool", "quality_status", "preview_asset_id")


def message_out(m):
    text = m.get("content") or ""
    meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
    return {"id": m["id"], "role": m["role"],
            "created_at": defs.iso(m["created_at"]),
            "kind": meta.get("kind"),
            "text": text[:TEXT_MAX], "text_truncated": len(text) > TEXT_MAX,
            "meta": {k: meta[k] for k in META_KEYS if k in meta}}


def messages_page(cur, session_id, before_id=None, limit=MESSAGE_PAGE):
    """Latest `limit` messages before `before_id`, returned oldest first."""
    if session_id is None:
        return {"rows": [], "has_more": False, "before_id": None}
    params = [session_id]
    cond = ""
    if before_id:
        cond = "AND id < %s"
        params.append(int(before_id))
    params.append(int(limit) + 1)
    cur.execute(f"""SELECT id, role, LEFT(content, {TEXT_MAX + 1}) AS content,
                           meta, created_at
                      FROM chat_messages WHERE session_id = %s {cond}
                     ORDER BY id DESC LIMIT %s""", params)
    rows = list(cur.fetchall())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return {"rows": [message_out(dict(m)) for m in rows],
            "has_more": has_more,
            "before_id": rows[0]["id"] if (rows and has_more) else None}


def _duration(a, b):
    if a is None or b is None:
        return None
    s = (b - a).total_seconds()
    return round(s, 1) if s >= 0 else None


def project_detail(cur, project_id):
    cur.execute(f"""
        SELECT p.id, p.title, p.kind, p.created_at, p.parent_project_id,
               p.chat_session_id, p.user_id, u.email,
               COALESCE(u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status,
               {LAST_ACTIVITY.format(p='p')} AS last_activity_at,
               {TIMING_COLS}
          FROM projects p JOIN users u ON u.id = p.user_id
          {PROJECT_TIMINGS}
         WHERE p.id = %s""", (project_id,))
    p = cur.fetchone()
    if not p:
        return None
    p = dict(p)
    t = timing_out(p)
    sid = p["chat_session_id"]

    messages = messages_page(cur, sid)
    cur.execute("""SELECT count(*) AS n,
                          count(*) FILTER (WHERE role = 'user') AS requests
                     FROM chat_messages WHERE session_id = %s""", (sid,))
    mc = cur.fetchone() or {}

    # Turns: one entry per user message. A long request runs as many
    # continuation slices that all point at the same message; activity is
    # referenced by message id once, never copied per slice.
    cur.execute("""SELECT id, role FROM chat_messages WHERE session_id = %s
                    ORDER BY id""", (sid,))
    ordered = [(r["id"], r["role"]) for r in cur.fetchall()]
    # Credits come from the ledger (indexed), never from each job's result:
    # detoasting every slice's result was most of this page's database time.
    cur.execute("""SELECT vj.id, vj.state, vj.created_at, vj.updated_at,
                          LEFT(vj.error, %s) AS error,
                          vj.payload->>'message_id' AS message_id,
                          vj.payload->>'root_agent_job_id' AS root_id,
                          (SELECT sum(jc.credits_used) FROM job_credits jc
                            WHERE jc.job_id = 'video:' || vj.id) AS credits
                     FROM video_jobs vj
                    WHERE vj.project_id = %s AND vj.type = 'agent_turn'
                    ORDER BY vj.id""", (ERROR_MAX, project_id))
    turn_jobs = [dict(r) for r in cur.fetchall()]
    turns = group_turns(turn_jobs, ordered)

    cur.execute("""SELECT id, type, state, progress, LEFT(error, %s) AS error,
                          attempts, created_at, updated_at,
                          (result IS NOT NULL) AS has_result,
                          CASE WHEN type = 'mcp_tool' THEN payload->>'tool'
                          END AS tool
                     FROM video_jobs WHERE project_id = %s
                    ORDER BY id DESC LIMIT %s""",
                (ERROR_MAX, project_id, JOB_LIMIT))
    jobs = [{"id": j["id"], "type": j["type"], "state": j["state"],
             "progress": j["progress"], "error": j["error"],
             "attempts": j["attempts"],
             "created_at": defs.iso(j["created_at"]),
             "updated_at": defs.iso(j["updated_at"]),
             "duration_s": _duration(j["created_at"], j["updated_at"])
             if j["state"] in ("done", "failed") else None,
             "has_result": bool(j["has_result"]), "tool": j["tool"]}
            for j in cur.fetchall()]
    cur.execute("SELECT count(*) AS n FROM video_jobs WHERE project_id = %s",
                (project_id,))
    jobs_total = int(cur.fetchone()["n"])

    cur.execute("""SELECT version, created_by, created_at,
                          pg_column_size(json) AS size_bytes
                     FROM edls WHERE project_id = %s
                    ORDER BY version DESC LIMIT %s""",
                (project_id, VERSION_LIMIT))
    versions = [{"version": v["version"],
                 "created_by": "user" if v["created_by"] == "user" else "agent",
                 "created_at": defs.iso(v["created_at"]),
                 "size_bytes": int(v["size_bytes"] or 0)}
                for v in cur.fetchall()]
    cur.execute("SELECT count(*) AS n FROM edls WHERE project_id = %s",
                (project_id,))
    version_total = int(cur.fetchone()["n"])

    cur.execute(f"""
        SELECT
          (SELECT count(*) FROM video_jobs ej WHERE ej.project_id IN
             (SELECT %(p)s UNION ALL SELECT id FROM projects
               WHERE parent_project_id = %(p)s)
             AND ej.type = 'final' AND ej.state = 'done') AS exports,
          (SELECT count(*) FROM video_jobs vj WHERE vj.project_id = %(p)s
             AND {UNSUPERSEDED_FAILED}) AS failed_jobs,
          (SELECT count(*) FROM video_jobs vj WHERE vj.project_id = %(p)s
             AND {STUCK}) AS stuck_jobs,
          (SELECT count(*) FROM client_events ce WHERE ce.project_id = %(p)s
             AND ce.kind IN ('trial_gate_shown','subscription_upload_locked'))
             AS paywall_hits""", {"p": project_id})
    s = cur.fetchone() or {}

    cur.execute("""SELECT c.id, c.title, c.created_at,
                          (SELECT count(*) FROM edls e WHERE e.project_id = c.id)
                              AS versions,
                          (SELECT count(*) FROM video_jobs vf
                            WHERE vf.project_id = c.id AND vf.type = 'final'
                              AND vf.state = 'done') AS exports
                     FROM projects c WHERE c.parent_project_id = %s
                    ORDER BY c.id LIMIT 200""", (project_id,))
    shorts = [{"id": c["id"], "title": c["title"],
               "created_at": defs.iso(c["created_at"]),
               "versions": int(c["versions"] or 0),
               "exports": int(c["exports"] or 0)} for c in cur.fetchall()]

    out = {
        "project": {
            "id": p["id"], "title": p["title"], "kind": kind(p["kind"]),
            "created_at": defs.iso(p["created_at"]),
            "parent_project_id": p["parent_project_id"],
            "customer": {"id": p["user_id"], "email": p["email"],
                         "plan": p["plan"], "status": p["status"]},
            "is_internal": defs.is_internal_email(p["email"]),
            "video": {"length_s": t["duration_s"], "width": t["width"],
                      "height": t["height"], "bytes": t["source_bytes"]},
        },
        "summary": {
            "requests": int(mc.get("requests") or 0),
            "versions": version_total,
            "exports": int(s.get("exports") or 0),
            "failed_jobs": int(s.get("failed_jobs") or 0),
            "stuck_jobs": int(s.get("stuck_jobs") or 0),
            "upload_wait_s": t["upload_s"],
            "analysis_wait_s": t["index_s"],
            "edit_wait_median_s": t["edit_s"],
            "paywall_hits": int(s.get("paywall_hits") or 0),
            "last_activity_at": defs.iso(p["last_activity_at"]),
        },
        "messages": {**messages, "total": int(mc.get("n") or 0)},
        "turns": turns,
        "jobs": {"rows": jobs, "total": jobs_total},
        "versions": versions,
        "shorts": shorts,
        "truncated": False, "truncated_reason": None,
    }
    return enforce_cap(out)


def group_turns(turn_jobs, ordered_messages, max_ids=2000):
    """Group agent slices by the user message they serve (no bodies)."""
    user_ids = [mid for mid, role in ordered_messages if role == "user"]
    by_root = {}
    for j in turn_jobs:
        try:
            if j.get("message_id") is not None:
                by_root.setdefault(str(j["id"]), int(j["message_id"]))
        except (TypeError, ValueError):
            pass
    groups = {}
    for j in turn_jobs:
        mid = None
        try:
            mid = int(j["message_id"]) if j.get("message_id") else None
        except (TypeError, ValueError):
            mid = None
        if mid is None and j.get("root_id"):
            mid = by_root.get(str(j["root_id"]))
        key = mid if mid is not None else f"job:{j['id']}"
        g = groups.setdefault(key, {"message_id": mid, "slices": []})
        credits = j.get("credits")
        try:
            credits = float(credits) if credits is not None else None
        except (TypeError, ValueError):
            credits = None
        g["slices"].append({
            "job_id": j["id"], "state": j["state"],
            "created_at": defs.iso(j["created_at"]),
            "updated_at": defs.iso(j["updated_at"]),
            "duration_s": _duration(j["created_at"], j["updated_at"]),
            "credits": credits,
            "error": (j.get("error") or None) and j["error"][:ERROR_MAX]})
    out = []
    for key, g in groups.items():
        mid = g["message_id"]
        activity = []
        if mid is not None:
            nxt = next((u for u in user_ids if u > mid), None)
            for m_id, role in ordered_messages:
                if m_id <= mid:
                    continue
                if nxt is not None and m_id >= nxt:
                    break
                if role in ("activity", "assistant"):
                    activity.append(m_id)
                    if len(activity) >= max_ids:
                        break
        first = g["slices"][0]
        out.append({"message_id": mid, "created_at": first["created_at"],
                    "slices": g["slices"],
                    "activity_message_ids": activity})
    out.sort(key=lambda t: t["created_at"] or "")
    return out


def enforce_cap(out, cap=SIZE_CAP):
    """Never send more than `cap` bytes: drop the largest section instead."""
    size = len(json.dumps(out, default=str))
    dropped = []
    while size > cap:
        candidates = {"turns": out["turns"], "messages": out["messages"],
                      "jobs": out["jobs"], "versions": out["versions"],
                      "shorts": out["shorts"]}
        name = max(candidates, key=lambda k: len(json.dumps(candidates[k],
                                                            default=str)))
        if name == "messages":
            out["messages"] = {"rows": [], "has_more": True,
                               "before_id": None,
                               "total": out["messages"].get("total", 0)}
        elif name == "jobs":
            out["jobs"] = {"rows": [], "total": out["jobs"].get("total", 0)}
        else:
            out[name] = []
        dropped.append(name)
        size = len(json.dumps(out, default=str))
        if len(dropped) > 5:
            break
    if dropped:
        out["truncated"] = True
        words = {"turns": "agent steps", "messages": "the latest messages",
                 "jobs": "the job list", "versions": "the version list",
                 "shorts": "the shorts list"}
        still = []
        if "messages" in dropped:
            still.append("the conversation still loads page by page")
        if "versions" in dropped:
            still.append("a version still opens by its number")
        if "jobs" in dropped:
            still.append("a job still opens by its id")
        out["truncated_reason"] = (
            "This project is very large, so these parts were left out to keep "
            "the page fast: " + ", ".join(words.get(d, d) for d in dropped)
            + "." + (" " + "; ".join(still).capitalize() + "." if still
                     else ""))
    return out


# ── On-demand tabs ───────────────────────────────────────────────────────
DETAIL_CAP = 1024 * 1024


def _capped(value):
    text = json.dumps(value, default=str)
    if len(text) <= DETAIL_CAP:
        return value, False
    return {"_truncated_json": text[:DETAIL_CAP]}, True


def project_session(cur, project_id):
    cur.execute("SELECT chat_session_id FROM projects WHERE id = %s",
                (project_id,))
    r = cur.fetchone()
    return (True, r["chat_session_id"]) if r else (False, None)


def job_detail(cur, project_id, job_id):
    cur.execute("""SELECT id, type, state, payload, result, error
                     FROM video_jobs WHERE id = %s AND project_id = %s""",
                (job_id, project_id))
    j = cur.fetchone()
    if not j:
        return None
    payload, p_trunc = _capped(j["payload"] or {})
    result_raw = j["result"]
    timings = result_raw.get("timings") if isinstance(result_raw, dict) \
        else None
    result, r_trunc = _capped(result_raw) if result_raw is not None \
        else (None, False)
    return {"id": j["id"], "type": j["type"], "state": j["state"],
            "payload": payload, "payload_truncated": p_trunc,
            "result": result, "result_truncated": r_trunc,
            "error": j["error"], "timings": timings}


def version_detail(cur, project_id, version, with_previous=False):
    cur.execute("""SELECT version, created_by, created_at, json FROM edls
                    WHERE project_id = %s AND version = %s""",
                (project_id, version))
    v = cur.fetchone()
    if not v:
        return None
    body, trunc = _capped(v["json"])
    previous = None
    if with_previous:
        cur.execute("""SELECT version, json FROM edls
                        WHERE project_id = %s AND version < %s
                        ORDER BY version DESC LIMIT 1""", (project_id, version))
        pv = cur.fetchone()
        if pv:
            pbody, _ = _capped(pv["json"])
            previous = {"version": pv["version"], "json": pbody}
    return {"version": v["version"],
            "created_by": "user" if v["created_by"] == "user" else "agent",
            "created_at": defs.iso(v["created_at"]), "json": body,
            "json_truncated": trunc, "previous": previous}


PREVIEWABLE = ("thumb", "sheet", "render", "proxy", "image_ref", "original",
               "music", "video_clip")


def assets(cur, project_id, presign):
    cur.execute("""SELECT id, kind, storage_key, bytes, duration_s, width,
                          height, created_at
                     FROM assets WHERE project_id = %s
                    ORDER BY id DESC LIMIT 400""", (project_id,))
    return {"rows": [{
        "id": a["id"], "kind": a["kind"], "bytes": a["bytes"],
        "duration_s": a["duration_s"], "width": a["width"],
        "height": a["height"], "created_at": defs.iso(a["created_at"]),
        "preview_url": presign(a["storage_key"])
        if a["kind"] in PREVIEWABLE else None} for a in cur.fetchall()]}


def index_detail(cur, project_id):
    cur.execute("""SELECT i.json, i.created_at, pg_column_size(i.json) AS size
                     FROM indexes i
                    WHERE i.video_sha256 = (
                        SELECT sha256 FROM assets WHERE project_id = %s
                           AND kind = 'original' ORDER BY id DESC LIMIT 1)
                    ORDER BY i.id DESC LIMIT 1""", (project_id,))
    r = cur.fetchone()
    if not r:
        return {"index": None, "created_at": None, "size_bytes": 0,
                "truncated": False}
    body, trunc = _capped(r["json"])
    return {"index": body, "created_at": defs.iso(r["created_at"]),
            "size_bytes": int(r["size"] or 0), "truncated": trunc}


PREVIEW_CHARS = 500


def _preview(value):
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("reply", "answer", "text", "question", "user", "error"):
            if isinstance(value.get(key), str):
                return value[key][:PREVIEW_CHARS]
        msgs = value.get("messages")
        if isinstance(msgs, list) and msgs:
            last = msgs[-1]
            content = last.get("content") if isinstance(last, dict) else last
            return json.dumps(content, default=str)[:PREVIEW_CHARS]
    return json.dumps(value, default=str)[:PREVIEW_CHARS]


def llm_calls(cur, project_id, page, per_page):
    from routes.admin_video import _row_cost
    cur.execute("SELECT count(*) AS n FROM llm_calls WHERE project_id = %s",
                (project_id,))
    total = int(cur.fetchone()["n"])
    cur.execute(f"""
        SELECT lc.id, lc.created_at, lc.purpose, lc.model, lc.job_id,
               lc.prompt_tokens, lc.completion_tokens,
               {_row_cost('lc')} AS cost,
               CASE WHEN pg_column_size(lc.request) <= 65536 THEN lc.request END
                   AS request,
               CASE WHEN pg_column_size(lc.response) <= 65536 THEN lc.response
               END AS response,
               LEFT(lc.request::text, %s) AS request_text,
               LEFT(lc.response::text, %s) AS response_text
          FROM llm_calls lc WHERE lc.project_id = %s
         ORDER BY lc.id DESC LIMIT %s OFFSET %s""",
                (PREVIEW_CHARS, PREVIEW_CHARS, project_id, per_page,
                 (page - 1) * per_page))
    rows = []
    for r in cur.fetchall():
        rows.append({
            "id": r["id"], "created_at": defs.iso(r["created_at"]),
            "purpose": r["purpose"], "model": r["model"],
            "job_id": r["job_id"], "prompt_tokens": r["prompt_tokens"],
            "completion_tokens": r["completion_tokens"],
            "cost_usd": round(float(r["cost"] or 0), 4),
            "request_preview": _preview(r["request"]) if r["request"]
            is not None else r["request_text"],
            "response_preview": _preview(r["response"]) if r["response"]
            is not None else r["response_text"],
        })
    return {"rows": rows, "page": page, "per_page": per_page, "total": total}


def llm_call(cur, project_id, call_id):
    from routes.admin_video import _row_cost
    cur.execute(f"""SELECT lc.id, lc.created_at, lc.purpose, lc.model,
                           lc.job_id, lc.request, lc.response,
                           {_row_cost('lc')} AS cost
                      FROM llm_calls lc WHERE lc.id = %s
                       AND lc.project_id = %s""", (call_id, project_id))
    r = cur.fetchone()
    if not r:
        return None
    request, rq_t = _capped(r["request"])
    response, rs_t = _capped(r["response"]) if r["response"] is not None \
        else (None, False)
    return {"id": r["id"], "created_at": defs.iso(r["created_at"]),
            "purpose": r["purpose"], "model": r["model"],
            "job_id": r["job_id"], "request": request,
            "request_truncated": rq_t, "response": response,
            "response_truncated": rs_t,
            "cost_usd": round(float(r["cost"] or 0), 4)}


def tool_outcomes(cur, project_id):
    from routes.admin_video import _mcp_non_success_sql, _mcp_refusal_sql
    fam = """(SELECT %(p)s UNION ALL SELECT id FROM projects
               WHERE parent_project_id = %(p)s)"""
    cur.execute(f"""
        SELECT COALESCE(mt.payload->>'tool', '(unknown)') AS tool,
               count(*) AS calls,
               count(*) FILTER (WHERE {_mcp_non_success_sql('mt')})
                   AS non_success,
               count(*) FILTER (WHERE {_mcp_refusal_sql('mt')}) AS refused
          FROM video_jobs mt
         WHERE mt.project_id IN {fam} AND mt.type = 'mcp_tool'
         GROUP BY 1 ORDER BY 2 DESC""", {"p": project_id})
    by_tool = [{"tool": r["tool"], "calls": int(r["calls"]),
                "non_success": int(r["non_success"]),
                "refused": int(r["refused"])} for r in cur.fetchall()]
    cur.execute(f"""
        SELECT mt.id, COALESCE(mt.payload->>'tool', '(unknown)') AS tool,
               mt.updated_at,
               LEFT(COALESCE(mt.error, mt.result->>'text', ''), 300) AS error
          FROM video_jobs mt
         WHERE mt.project_id IN {fam} AND mt.type = 'mcp_tool'
           AND {_mcp_non_success_sql('mt')}
         ORDER BY mt.id DESC LIMIT 20""", {"p": project_id})
    recent = [{"job_id": r["id"], "tool": r["tool"],
               "at": defs.iso(r["updated_at"]), "error": r["error"] or None}
              for r in cur.fetchall()]
    return {"calls": sum(t["calls"] for t in by_tool),
            "non_success": sum(t["non_success"] for t in by_tool),
            "refused": sum(t["refused"] for t in by_tool),
            "by_tool": by_tool, "recent_failures": recent}
