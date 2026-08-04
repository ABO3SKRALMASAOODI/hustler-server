"""
Admin observability for the video editor. Everything an operator needs to
understand a session after the fact: per-user rollups, ops counters, a
per-project inspector (full chat + activity, EDL diffs, jobs with timings,
assets with short-lived presigned previews, the raw index, and every model
call persisted by the worker in llm_calls), and a cost view.

Security: every route is behind admin_required (same JWT-email gate as the
legacy admin), presigned links are <=15 min (storage.PRESIGN_EXPIRY), and
llm_calls payloads are capped + key-redacted by the worker before storage.
"""

import os

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app

from routes.admin import admin_required, _scope, METRICS_EPOCH
import model_prices
import storage

admin_video_bp = Blueprint("admin_video", __name__)

# FALLBACK $ per 1M tokens, for a model model_prices.py does not list (default =
# DeepSeek V4 Pro, $1.74 in / $3.48 out). Every listed model is priced from its
# OWN row's `model` column instead, because a turn can run on DeepSeek for a
# free user and Grok for a subscriber and one blended rate is then wrong for
# both. MUST match worker/config.py or the admin spend view disagrees with what
# users were actually charged.
PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "1.74"))
PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "3.48"))
# Cache-hit input price — see worker/config.LLM_PRICE_CACHED_IN_PER_M. Rows
# carry their cache-hit slice in response->>'cached_in' and their reasoning
# tokens (charged only where the provider bills them separately) in
# response->>'reasoning_out'.
PRICE_CACHED_IN_PER_M = float(
    os.getenv("LLM_PRICE_CACHED_IN_PER_M", "0.003625"))

PRICE_FALLBACK = {"in": PRICE_IN_PER_M, "cached_in": PRICE_CACHED_IN_PER_M,
                  "out": PRICE_OUT_PER_M, "reasoning_separate": False}


def adb():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _row_cost(alias=""):
    """USD cost of ONE llm_calls row, priced from that row's own model.

    Identical expression to worker/db.charge_turn_credits, so the admin spend
    view and the credits actually deducted cannot disagree. Cache-HIT input is
    billed at a small fraction of a miss, so counting every prompt token at the
    miss price would show a spend several times what users were charged; rows
    written before the split simply have no 'cached_in' and price as all-miss,
    which is what they were.

    `alias` prefixes the column names when the query joins llm_calls.
    """
    p = (alias + ".") if alias else ""
    return model_prices.row_cost_sql(
        PRICE_FALLBACK, model_col=p + "model", response_col=p + "response",
        prompt_col=p + "prompt_tokens", completion_col=p + "completion_tokens")


def _cost_expr(alias=""):
    """Aggregate cost over a group of llm_calls rows."""
    return "COALESCE(SUM(" + _row_cost(alias) + "), 0)"


# A user message is "unserved" when no agent_turn job ever picked it up —
# the strongest signal that a user asked for something and got silence.
# payload->>'message_id' is text, so the message id is cast to match.
UNSERVED_EXISTS = """NOT EXISTS (SELECT 1 FROM video_jobs vj
                      WHERE vj.type = 'agent_turn'
                        AND vj.payload->>'message_id' = cm.id::text)"""


def _presign(key):
    if not storage.is_configured():
        return None
    try:
        # Admin inspection links stay on the short 15-min expiry (the long
        # PRESIGN_GET_EXPIRY exists for the studio player, not for admin).
        return storage.presign_get(key, expires=storage.PRESIGN_EXPIRY)
    except Exception:
        return None


def _msg_brief(m):
    return {"id": m["id"], "content": m["content"], "meta": m["meta"],
            "created_at": m["created_at"].isoformat()}


@admin_video_bp.route("/admin/video/overview", methods=["GET"])
@admin_required
def video_overview():
    with adb() as conn:
        cur = conn.cursor()

        cur.execute(f"""
            SELECT u.id, u.email,
                   COUNT(DISTINCT p.id) AS projects,
                   COALESCE(m.msgs, 0) AS messages,
                   COALESCE(j.done, 0) AS jobs_done,
                   COALESCE(j.failed, 0) AS jobs_failed,
                   COALESCE(j.active, 0) AS jobs_active,
                   COALESCE(a.bytes, 0) AS storage_bytes,
                   COALESCE(l.tokens_in, 0) AS tokens_in,
                   COALESCE(l.tokens_out, 0) AS tokens_out,
                   COALESCE(l.est_cost, 0) AS est_cost,
                   GREATEST(COALESCE(j.last, to_timestamp(0)),
                            COALESCE(m.last, to_timestamp(0))) AS last_active
            FROM users u
            JOIN projects p ON p.user_id = u.id
            LEFT JOIN (SELECT p2.user_id, COUNT(*) AS msgs,
                              MAX(cm.created_at) AS last
                       FROM chat_messages cm
                       JOIN projects p2 ON p2.chat_session_id = cm.session_id
                       WHERE cm.role = 'user'
                       GROUP BY p2.user_id) m ON m.user_id = u.id
            LEFT JOIN (SELECT user_id,
                              COUNT(*) FILTER (WHERE state='done') AS done,
                              COUNT(*) FILTER (WHERE state='failed') AS failed,
                              COUNT(*) FILTER (WHERE state IN
                                               ('queued','running')) AS active,
                              MAX(updated_at) AS last
                       FROM video_jobs GROUP BY user_id) j ON j.user_id = u.id
            LEFT JOIN (SELECT p3.user_id, SUM(ast.bytes)::bigint AS bytes
                       FROM assets ast
                       JOIN projects p3 ON p3.id = ast.project_id
                       GROUP BY p3.user_id) a ON a.user_id = u.id
            LEFT JOIN (SELECT p4.user_id,
                              SUM(lc.prompt_tokens) AS tokens_in,
                              SUM(lc.completion_tokens) AS tokens_out,
                              """ + _cost_expr("lc") + """ AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            GROUP BY u.id, u.email, m.msgs, j.done, j.failed, j.active,
                     a.bytes, l.tokens_in, l.tokens_out, l.est_cost,
                     j.last, m.last
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """)
        users = cur.fetchall()

        # global ops counters + 14-day trends
        cur.execute("""
            SELECT DATE(created_at) AS day,
                   COUNT(*) FILTER (WHERE type='agent_turn') AS turns,
                   COUNT(*) FILTER (WHERE type IN ('preview','final')) AS renders
            FROM video_jobs
            WHERE created_at > NOW() - INTERVAL '14 days'
            GROUP BY 1 ORDER BY 1
        """)
        daily = cur.fetchall()

        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE type='agent_turn') AS turns_total,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->>'auto_render')::boolean IS TRUE) AS auto_renders,
              COALESCE(SUM((result->'honesty'->>'false_claims')::int)
                FILTER (WHERE type='agent_turn'), 0) AS false_claims,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->'honesty'->>'corrective_note')::boolean
                    IS TRUE) AS corrective_notes,
              COUNT(*) FILTER (WHERE type='agent_turn'
                AND (result->'honesty'->>'fallback_reply')::boolean
                    IS TRUE) AS fallback_replies,
              COUNT(*) FILTER (WHERE state='failed') AS failed,
              COUNT(*) FILTER (WHERE state IN ('done','failed')) AS finished,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                (result->'timings'->>'queue_wait_s')::float)
                FILTER (WHERE result->'timings'->>'queue_wait_s'
                        IS NOT NULL) AS median_queue_wait_s
            FROM video_jobs
        """)
        ops = cur.fetchone()

        stage_medians = {}
        for jtype, stages in (("index", ("whisper_s", "proxy_s", "shots_s",
                                         "total_s")),
                              ("preview", ("download_s", "encode_s",
                                           "upload_s", "total_s")),
                              ("final", ("download_s", "encode_s",
                                         "upload_s", "total_s")),
                              ("agent_turn", ("llm_s", "total_s"))):
            row = {}
            for st in stages:
                cur.execute(f"""
                    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                        (result->'timings'->>%s)::float) AS med
                    FROM video_jobs
                    WHERE type = %s AND state = 'done'
                      AND result->'timings'->>%s IS NOT NULL
                """, (st, jtype, st))
                med = cur.fetchone()["med"]
                if med is not None:
                    row[st] = round(med, 2)
            stage_medians[jtype] = row

        cur.execute("""
            SELECT COUNT(*) AS n FROM chat_messages
            WHERE role='activity' AND content LIKE '%NO CHANGE%'
        """)
        no_change = cur.fetchone()["n"]

        # attention feed: only things a human can act on (failed/stuck jobs).
        # Unserved messages are deliberately NOT here — with auto-resume live
        # they self-heal, and there is no admin action to take on old ones.
        cur.execute("""
            SELECT * FROM (
                SELECT 'failed_job' AS type, p.id AS project_id,
                       p.title AS project_title, u.email,
                       vj.type || ': ' || LEFT(COALESCE(vj.error, ''), 140)
                           AS detail,
                       vj.updated_at AS happened_at
                FROM video_jobs vj
                JOIN projects p ON p.id = vj.project_id
                JOIN users u ON u.id = vj.user_id
                WHERE vj.state = 'failed'
                  AND vj.updated_at > NOW() - INTERVAL '7 days'
                UNION ALL
                SELECT 'stuck_job', p.id, p.title, u.email,
                       vj.type || ' stuck (' || vj.state || ')',
                       COALESCE(vj.heartbeat_at, vj.created_at)
                FROM video_jobs vj
                JOIN projects p ON p.id = vj.project_id
                JOIN users u ON u.id = vj.user_id
                WHERE vj.state IN ('queued', 'running')
                  AND ((vj.heartbeat_at IS NULL
                        AND vj.created_at < NOW() - INTERVAL '10 minutes')
                       OR vj.heartbeat_at < NOW() - INTERVAL '10 minutes')
                UNION ALL
                SELECT 'upload_failed', ce.project_id, p.title, u.email,
                       -- Every JSON accessor is parenthesised on purpose:
                       -- `||` binds tighter than `->>`, so the unparenthesised
                       -- form parses as (' - ' || ce.detail) ->> 'filename'
                       -- and dies with "invalid input syntax for type json"
                       -- at RUNTIME, taking the whole overview page with it.
                       COALESCE((ce.detail->>'reason'), ce.kind)
                         || COALESCE(' - ' || (ce.detail->>'filename'), '')
                         || COALESCE(' (' || ROUND(
                              (CASE WHEN (ce.detail->>'bytes') ~ '^[0-9]+$'
                                    THEN (ce.detail->>'bytes')::numeric
                               END) / 1073741824.0, 2) || ' GB)', ''),
                       ce.created_at
                FROM client_events ce
                LEFT JOIN projects p ON p.id = ce.project_id
                LEFT JOIN users u ON u.id = ce.user_id
                WHERE ce.kind IN ('upload_rejected', 'upload_failed')
                  AND ce.created_at > NOW() - INTERVAL '7 days'
            ) t
            ORDER BY happened_at DESC NULLS LAST
            LIMIT 40
        """)
        attention = cur.fetchall()

        # headline totals + liveness — "is everything working" at a glance
        cur.execute("""
            SELECT
              (SELECT COUNT(*) FROM projects) AS projects,
              (SELECT COUNT(*) FROM assets WHERE kind='original') AS videos,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE type IN ('preview','final')
                   AND state='done') AS renders_done,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE state='queued') AS queued_now,
              (SELECT COUNT(*) FROM video_jobs
                 WHERE state='running') AS running_now,
              (SELECT MAX(updated_at) FROM video_jobs
                 WHERE state IN ('done','failed','running'))
                 AS last_worker_activity
        """)
        totals = cur.fetchone()

        # Which model actually ran for each purpose (ground truth from
        # llm_calls) — so "am I really on Grok?" is answerable at a glance and
        # the agent-vs-vision-vs-image split is no longer a mystery.
        cur.execute("""
            SELECT purpose,
                   (ARRAY_AGG(model ORDER BY created_at DESC))[1] AS model,
                   COUNT(*) AS calls,
                   MAX(created_at) AS last_at
            FROM llm_calls
            WHERE created_at > NOW() - INTERVAL '30 days'
              AND model IS NOT NULL
            GROUP BY purpose
            ORDER BY last_at DESC NULLS LAST
        """)
        model_rows = cur.fetchall()

        # Vision liveness, inferred from behaviour. A day with agent turns but
        # not one vision call is the signature of a lost vision key: on a
        # healthy day look_at runs on roughly a third of turns. Requiring a
        # meaningful number of agent calls first is what stops a quiet weekend
        # from reading as an outage.
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE purpose = 'agent') AS agent_calls,
                   COUNT(*) FILTER (WHERE purpose LIKE 'vision%') AS vision_calls
            FROM llm_calls
            WHERE created_at > NOW() - INTERVAL '24 hours'
        """)
        vrow = cur.fetchone() or {}
        _agent_n = int(vrow.get("agent_calls") or 0)
        _vision_n = int(vrow.get("vision_calls") or 0)
        # None = "not enough traffic to say", which must not render as a
        # red light. Only a confident zero is an outage.
        vision_live = None if _agent_n < 25 else bool(_vision_n)

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")

    def _provider(b):
        b = (b or "").lower()
        if "dashscope" in b:
            return "DashScope (Qwen)"
        if "deepseek" in b:
            return "DeepSeek"
        if "x.ai" in b:
            return "xAI (Grok)"
        if "openai" in b:
            return "OpenAI"
        return "custom"

    return jsonify({
        "totals": {
            "users": len(users),
            "projects": totals["projects"],
            "videos": totals["videos"],
            "renders_done": totals["renders_done"],
            "queued_now": totals["queued_now"],
            "running_now": totals["running_now"],
            "last_worker_activity": totals["last_worker_activity"].isoformat()
                if totals["last_worker_activity"] else None,
        },
        "health": {
            "storage_configured": bool(os.getenv("S3_ENDPOINT")
                                       and os.getenv("S3_BUCKET")),
            "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
            # IS THE AGENT STILL LOOKING AT THE FOOTAGE. Vision is honest-off
            # by contract — when its provider is unconfigured every tool says
            # "visual inspection unavailable" and nothing fails — which is
            # right for the user and invisible to us. The Jul 26 2026 provider
            # switch dropped the dispatcher's inherited vision key and it took
            # TWO DAYS and 419 agent calls with zero look_at to notice.
            # Inferred from behaviour, not from this service's env: the worker
            # is a different service with different variables, so its env is
            # not ours to read and its BEHAVIOUR is the only truth available.
            "vision_live": vision_live,
        },
        "users": [{**u, "last_active": u["last_active"].isoformat()
                   if u.get("last_active") else None,
                   "storage_bytes": int(u["storage_bytes"] or 0),
                   "tokens_in": int(u["tokens_in"] or 0),
                   "tokens_out": int(u["tokens_out"] or 0),
                   "est_cost": round(float(u["est_cost"] or 0), 4)}
                  for u in users],
        "daily": [{"day": d["day"].isoformat(), "turns": d["turns"],
                   "renders": d["renders"]} for d in daily],
        "ops": {
            "turns_total": ops["turns_total"],
            "auto_renders": ops["auto_renders"],
            "false_claims": ops["false_claims"],
            "corrective_notes": ops["corrective_notes"],
            "fallback_replies": ops["fallback_replies"],
            "no_change_count": no_change,
            "job_failure_rate": round(
                ops["failed"] / ops["finished"], 4) if ops["finished"] else 0,
            "median_queue_wait_s": round(ops["median_queue_wait_s"], 2)
                if ops["median_queue_wait_s"] is not None else None,
            "stage_medians": stage_medians,
        },
        "attention": [
            {"type": a["type"], "project_id": a["project_id"],
             "project_title": a["project_title"], "email": a["email"],
             "detail": a["detail"],
             "at": a["happened_at"].isoformat()
                 if a["happened_at"] else None}
            for a in attention],
        "models": {
            # Backend service config (concierge/index-greet run here). The
            # worker service can be pointed elsewhere — trust "observed".
            "configured": {
                "provider": _provider(base_url),
                "base_url": base_url,
                "agent_model": os.getenv("AGENT_MODEL", "deepseek-v4-pro"),
                # Vision runs on its OWN provider (worker/config.py): the
                # chat provider may take no images at all — DeepSeek 400s on
                # every one, which blinded the agent on Jul 26 2026.
                "vision_model": os.getenv("VISION_MODEL", "grok-4.5"),
                "vision_base_url": os.getenv("VISION_BASE_URL",
                                             "https://api.x.ai/v1"),
                "image_gen_model": os.getenv("IMAGE_GEN_MODEL",
                                             "grok-imagine-image-quality"),
                "image_edit_model": os.getenv("IMAGE_EDIT_MODEL", "") or None,
                "whisper_model": os.getenv("WHISPER_MODEL", "medium"),
                "price_in_per_m": PRICE_IN_PER_M,
                "price_out_per_m": PRICE_OUT_PER_M,
            },
            # What each purpose ACTUALLY used, last 30 days, newest first.
            "observed": [
                {"purpose": m["purpose"], "model": m["model"],
                 "calls": int(m["calls"] or 0),
                 "last_at": m["last_at"].isoformat() if m["last_at"] else None}
                for m in model_rows],
        },
    })


# ── HOW LONG DID THIS PERSON WAIT? ──────────────────────────────────────────
#
# The three waits a customer actually experiences, per project, so they can be
# read against the one number that should predict them: how long their video
# is. Every churn investigation this codebase has run started with someone
# saying "it took forever" and ended in a hand-written SQL query.
#
#   upload_s  their bytes leaving their machine — from the moment the browser
#             says it started (client_events.upload_started, round 57) to the
#             asset row existing. Falls back to the project's own creation
#             time for everything uploaded before that event existed.
#   index_s   the first successful analysis. A CACHE HIT is flagged, because
#             re-uploading a file we have already indexed is 10 seconds and
#             tells you nothing about the pipeline's real speed.
#   edit_s    the MEDIAN preview render — what the user waits, every time, to
#             see a change they just made. The median and not the mean: one
#             cold 4K encode should not describe a session of small cuts.
#
# The LATERALs pick a specific row (ORDER BY ... LIMIT 1) rather than
# aggregating: MIN(created_at) with MIN(updated_at) can silently pair the start
# of one job with the end of another and produce a duration that never happened.
_PROJECT_TIMINGS = """
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

_TIMING_COLS = """
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


def _timing_out(r):
    """Round the seconds and drop the negatives. A clock that runs backwards
    (an asset row written before its own upload_started event was flushed) is
    not a measurement — reporting it as one puts a nonsense point on the chart."""
    def secs(v):
        if v is None:
            return None
        v = float(v)
        return round(v, 1) if v >= 0 else None
    return {
        "duration_s": round(float(r["duration_s"]), 1) if r["duration_s"] else None,
        "source_bytes": r["source_bytes"],
        "width": r["width"], "height": r["height"],
        "upload_s": secs(r["upload_s"]),
        # False = derived from the project's creation time, because this upload
        # predates client_events (round 57). It includes however long the user
        # spent choosing a file, so it is an upper bound, not a measurement.
        "upload_measured": bool(r["upload_measured"]),
        "index_s": secs(r["index_s"]),
        "index_cached": bool(r["index_cached"]),
        "edit_s": secs(r["edit_s"]), "previews": r["previews"] or 0,
        "turn_s": secs(r["turn_s"]), "turns": r["turns"] or 0,
    }


@admin_video_bp.route("/admin/video/projects", methods=["GET"])
@admin_required
def video_projects():
    search = (request.args.get("search") or "").strip()
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.title, p.created_at, u.email,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user') AS messages,
                   (SELECT MAX(v.updated_at) FROM video_jobs v
                    WHERE v.project_id = p.id) AS last_job,
                   (SELECT COUNT(*) FROM edls e
                    WHERE e.project_id = p.id) AS versions,
                   -- Did this customer EXPORT (finish a video)? A successful
                   -- export = a 'final' render job that reached 'done'.
                   (SELECT COUNT(*) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS exports,
                   (SELECT MAX(vf.updated_at) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS last_export,
                   """ + _TIMING_COLS + """
            FROM projects p JOIN users u ON u.id = p.user_id
            """ + _PROJECT_TIMINGS + """
            WHERE u.email ILIKE %s OR p.title ILIKE %s
            ORDER BY p.id DESC LIMIT 100
        """, (f"%{search}%", f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"projects": [
        {"id": r["id"], "title": r["title"], "email": r["email"],
         "messages": r["messages"], "versions": r["versions"],
         "exports": r["exports"],
         "created_at": r["created_at"].isoformat(),
         "last_job": r["last_job"].isoformat() if r["last_job"] else None,
         "last_export": (r["last_export"].isoformat()
                         if r["last_export"] else None),
         **_timing_out(r)}
        for r in rows]})


@admin_video_bp.route("/admin/video/timings", methods=["GET"])
@admin_required
def video_timings():
    """Every project that has a video, as points for the wait-vs-length chart.

    Deliberately a WIDER set than the project list (which is capped at 100 and
    filtered by a search box): the shape only shows up across the whole corpus,
    and the single most useful thing it shows is where the line STOPS being a
    line — the length past which a wait becomes a churn."""
    try:
        days = max(1, min(int(request.args.get("days") or 90), 3650))
    except (TypeError, ValueError):
        days = 90
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.created_at, u.email, """ + _TIMING_COLS + """
            FROM projects p JOIN users u ON u.id = p.user_id
            """ + _PROJECT_TIMINGS + """
            WHERE p.created_at > NOW() - (%s || ' days')::interval
              AND o.duration_s IS NOT NULL
            ORDER BY p.id DESC LIMIT 1000
        """, (str(days),))
        rows = cur.fetchall()
    return jsonify({"days": days, "points": [
        {"id": r["id"], "email": r["email"],
         "created_at": r["created_at"].isoformat(), **_timing_out(r)}
        for r in rows]})


PREVIEWABLE = ("thumb", "sheet", "render", "proxy", "image_ref", "original",
               "music", "video_clip")


@admin_video_bp.route("/admin/video/projects/<int:project_id>",
                      methods=["GET"])
@admin_required
def video_project_detail(project_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT p.*, u.email FROM projects p
                       JOIN users u ON u.id = p.user_id
                       WHERE p.id = %s""", (project_id,))
        p = cur.fetchone()
        if not p:
            return jsonify({"error": "Project not found"}), 404

        cur.execute("""SELECT id, role, content, meta, created_at
                       FROM chat_messages WHERE session_id = %s
                       ORDER BY id ASC LIMIT 2000""", (p["chat_session_id"],))
        messages = cur.fetchall()

        cur.execute("""SELECT version, json, created_by, created_at
                       FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 200""", (project_id,))
        edls = cur.fetchall()

        cur.execute("""SELECT id, type, state, progress, error, payload,
                              result, attempts, created_at, updated_at
                       FROM video_jobs WHERE project_id = %s
                       ORDER BY id DESC LIMIT 300""", (project_id,))
        jobs = cur.fetchall()

        cur.execute("""SELECT * FROM assets WHERE project_id = %s
                       ORDER BY id DESC LIMIT 400""", (project_id,))
        assets = cur.fetchall()

        cur.execute("""SELECT COUNT(*) AS n FROM llm_calls
                       WHERE project_id = %s""", (project_id,))
        llm_count = cur.fetchone()["n"]

        cur.execute("""SELECT id, state, error, payload, result,
                              created_at, updated_at
                       FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                       ORDER BY id ASC""", (project_id,))
        turn_jobs = cur.fetchall()

        cur.execute("""SELECT id, job_id, purpose, model, prompt_tokens,
                              completion_tokens, created_at
                       FROM llm_calls
                       WHERE project_id = %s AND job_id IS NOT NULL
                       ORDER BY id ASC""", (project_id,))
        llm_summaries = cur.fetchall()

        cur.execute(f"""SELECT cm.id FROM chat_messages cm
                        WHERE cm.session_id = %s AND cm.role = 'user'
                          AND {UNSERVED_EXISTS}
                        ORDER BY cm.id ASC""", (p["chat_session_id"],))
        unserved_ids = [r["id"] for r in cur.fetchall()]

        cur.execute("""SELECT id, type, progress, payload,
                              EXTRACT(EPOCH FROM (NOW() - created_at)) AS age_s
                       FROM video_jobs
                       WHERE project_id = %s AND state IN ('running', 'queued')
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        _live = cur.fetchone()
        live_turn = {
            "job_id": _live["id"], "type": _live["type"],
            "progress": _live["progress"],
            "message_id": (_live["payload"] or {}).get("message_id"),
            "running_for_s": int(_live["age_s"] or 0),
        } if _live else None

        # Thumbnails + contact sheets have no asset rows — their keys live
        # in the index JSON and in render results. Surface them here so the
        # admin grid can show everything.
        cur.execute("""SELECT i.json FROM indexes i
                       WHERE i.video_sha256 = (
                           SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        idx_row = cur.fetchone()

    # Who triggered each render? A render asset carries no trigger of its own —
    # that lives on the render JOB's payload (`source: 'user_edit'` for a studio
    # timeline edit; absent for an agent-initiated render). Map the job back to
    # the asset it produced (result.render_asset_id) so the admin card can say
    # "USER edited" vs "AGENT rendered" instead of blaming the agent for the
    # customer's own edits, and flag `force` re-encodes (the studio's
    # "couldn't load" recovery) so a burst of identical re-renders reads clearly.
    render_trigger = {}
    for j in jobs:
        if j["type"] not in ("preview", "final"):
            continue
        res = j.get("result") if isinstance(j.get("result"), dict) else {}
        pay = j.get("payload") if isinstance(j.get("payload"), dict) else {}
        aid = res.get("render_asset_id")
        if aid is None:
            continue
        render_trigger[aid] = {
            "source": "user_edit" if pay.get("source") == "user_edit"
                      else "agent",
            "forced": bool(pay.get("force")),
        }

    out_assets = []
    for a in assets:
        row = {"id": a["id"], "kind": a["kind"],
               "storage_key": a["storage_key"], "bytes": a["bytes"],
               "duration_s": a["duration_s"], "width": a["width"],
               "height": a["height"], "meta": a.get("meta") or {},
               "created_at": a["created_at"].isoformat()}
        if a["id"] in render_trigger:
            row["trigger"] = render_trigger[a["id"]]
        if a["kind"] in PREVIEWABLE:
            row["url"] = _presign(a["storage_key"])
        out_assets.append(row)

    seen_keys = {a["storage_key"] for a in assets}
    idx = (idx_row or {}).get("json") or {}
    for skey in idx.get("sheet_keys") or []:
        if skey not in seen_keys:
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": skey, "meta": {},
                               "url": _presign(skey)})
    # v10: the filmstrip tiles ARE the visual index — show them where the
    # contact sheets used to appear.
    for tkey in idx.get("tile_keys") or []:
        if tkey not in seen_keys:
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": tkey, "meta": {"tile": True},
                               "url": _presign(tkey)})
    for shot in idx.get("shots") or []:
        tkey = shot.get("thumb_key")
        if tkey and tkey not in seen_keys:
            out_assets.append({"id": None, "kind": "thumb",
                               "storage_key": tkey,
                               "meta": {"shot": shot.get("id")},
                               "url": _presign(tkey)})
    for a in assets:
        rkey = (a.get("meta") or {}).get("sheet_key")
        if rkey and rkey not in seen_keys:
            seen_keys.add(rkey)
            out_assets.append({"id": None, "kind": "sheet",
                               "storage_key": rkey,
                               "meta": {"render_asset": a["id"]},
                               "url": _presign(rkey)})

    # Group the session into agent turns: a turn owns the window from its
    # triggering user message up to (not including) the next user message.
    # Rows before the first turn (canned replies, index_ready) stay only in
    # the flat "messages" array above.
    llm_by_job = {}
    for r in llm_summaries:
        llm_by_job.setdefault(r["job_id"], []).append(
            {"id": r["id"], "purpose": r["purpose"], "model": r["model"],
             "prompt_tokens": r["prompt_tokens"],
             "completion_tokens": r["completion_tokens"],
             "created_at": r["created_at"].isoformat()})

    msg_by_id = {m["id"]: m for m in messages}
    user_msg_ids = sorted(m["id"] for m in messages if m["role"] == "user")

    turns = []
    for t in turn_jobs:
        payload = t.get("payload") if isinstance(t.get("payload"), dict) \
            else {}
        res = t.get("result") if isinstance(t.get("result"), dict) else {}
        try:
            mid = int(payload.get("message_id"))
        except (TypeError, ValueError):
            mid = None
        um = msg_by_id.get(mid)
        activity, assistant_msgs = [], []
        if um:
            nxt = next((i for i in user_msg_ids if i > um["id"]), None)
            for m in messages:  # ordered by id ASC
                if m["id"] <= um["id"]:
                    continue
                if nxt is not None and m["id"] >= nxt:
                    break
                if m["role"] == "activity":
                    activity.append(_msg_brief(m))
                elif m["role"] == "assistant":
                    assistant_msgs.append(_msg_brief(m))
        try:
            edl_version = int(res["edl_version"]) \
                if res.get("edl_version") is not None else None
        except (TypeError, ValueError):
            edl_version = None
        turns.append({
            "job_id": t["id"], "state": t["state"],
            "created_at": t["created_at"].isoformat(),
            "updated_at": t["updated_at"].isoformat(),
            "user_message": _msg_brief(um) if um else None,
            "activity": activity,
            "assistant_messages": assistant_msgs,
            "llm_calls": llm_by_job.get(t["id"], []),
            "honesty": res.get("honesty"),
            "timings": res.get("timings"),
            "credits_charged": res.get("credits_charged"),
            "edl_version": edl_version,
            "error": t["error"],
        })

    # Export signal for THIS conversation: successful exports = 'final' jobs
    # that reached 'done'; also surface attempts (incl. failed) to spot a
    # customer who TRIED to export but the render failed.
    final_done = [j for j in jobs if j["type"] == "final"
                  and j["state"] == "done"]
    final_all = [j for j in jobs if j["type"] == "final"]
    last_export = max((j["updated_at"] for j in final_done), default=None)
    exports = {
        "count": len(final_done),
        "attempts": len(final_all),
        "failed": len([j for j in final_all if j["state"] == "failed"]),
        "last_at": last_export.isoformat() if last_export else None,
    }

    # Uploads that never became an asset, in the project they were aimed at.
    # Shown beside the chat because that is where the absence shows: a project
    # whose whole story is "user tried to add a video and nothing happened".
    with adb() as conn2:
        cur2 = conn2.cursor()
        cur2.execute("""SELECT id, kind, detail, created_at, project_id
                        FROM client_events
                        WHERE project_id = %s AND kind = ANY(%s)
                        ORDER BY id DESC LIMIT 50""",
                     (project_id, list(UPLOAD_EVENT_KINDS)))
        upload_events = [_upload_event_row(e) for e in cur2.fetchall()]

    return jsonify({
        "project": {"id": p["id"], "title": p["title"], "email": p["email"],
                    "created_at": p["created_at"].isoformat()},
        "exports": exports,
        "upload_events": upload_events,
        "upload_failures": len([e for e in upload_events
                                if e["kind"] in UPLOAD_FAILURE_KINDS]),
        "messages": [
            {"id": m["id"], "role": m["role"], "content": m["content"],
             "meta": m["meta"], "created_at": m["created_at"].isoformat()}
            for m in messages],
        "edls": [
            {"version": e["version"], "json": e["json"],
             "created_by": e["created_by"],
             "created_at": e["created_at"].isoformat()} for e in edls],
        "jobs": [
            {"id": j["id"], "type": j["type"], "state": j["state"],
             "progress": j["progress"], "error": j["error"],
             "payload": j["payload"], "result": j["result"],
             "attempts": j["attempts"],
             "created_at": j["created_at"].isoformat(),
             "updated_at": j["updated_at"].isoformat()} for j in jobs],
        "assets": out_assets,
        "llm_call_count": llm_count,
        "turns": turns,
        "unserved_message_ids": unserved_ids,
        # A turn that is STILL WORKING looks exactly like a turn that was
        # ignored: the user's message sits there with no assistant reply under
        # it. A founder read that as "this customer got no response" on
        # 2026-07-25 while the agent was 90 seconds into a two-minute edit that
        # then answered fine. Name the in-flight turn so the two are never
        # confused again.
        "live_turn": live_turn,
    })


# ── Upload failures (round 57) ───────────────────────────────────────────
# A file the user tried to give us that never became an asset. This is the
# blind spot that mattered most: 201 of 203 index jobs have ever succeeded, so
# nothing server-side was failing — the losses were in the browser, where a
# size or duration refusal fired before a project existed and left no trace at
# all. 65 of 214 accounts have no project, and until now every one of them read
# as "signed up and never tried".
UPLOAD_FAILURE_KINDS = ("upload_rejected", "upload_failed")
UPLOAD_EVENT_KINDS = UPLOAD_FAILURE_KINDS + ("upload_started",)


def _upload_event_row(e):
    d = e["detail"] or {}
    return {
        "id": e["id"], "kind": e["kind"],
        "created_at": e["created_at"].isoformat(),
        "project_id": e.get("project_id"),
        "filename": d.get("filename"),
        "bytes": d.get("bytes"),
        "duration_s": d.get("duration_s"),
        "reason": d.get("reason"),
        "stage": d.get("stage"),
        "origin": d.get("origin"),
        "detail": d,
    }


@admin_video_bp.route("/admin/video/upload_failures", methods=["GET"])
@admin_required
def video_upload_failures():
    """Every upload that did not land, newest first, with who and what.

    Includes `upload_started` so the ratio is readable: a start with no
    matching asset is an upload that died in transit, which no error message
    anywhere would ever have shown us.
    """
    limit = min(int(request.args.get("limit", 200)), 500)
    days = min(int(request.args.get("days", 30)), 365)
    failures_only = request.args.get("failures_only", "1") != "0"
    kinds = UPLOAD_FAILURE_KINDS if failures_only else UPLOAD_EVENT_KINDS
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT e.id, e.kind, e.detail, e.created_at,
                              e.project_id, e.user_id, u.email, p.title
                       FROM client_events e
                       LEFT JOIN users u ON u.id = e.user_id
                       LEFT JOIN projects p ON p.id = e.project_id
                       WHERE e.kind = ANY(%s)
                         AND e.created_at > NOW() - (%s || ' days')::interval
                       ORDER BY e.id DESC LIMIT %s""",
                    (list(kinds), str(days), limit))
        rows = cur.fetchall()

        cur.execute("""SELECT kind,
                              COUNT(*) AS n,
                              COUNT(DISTINCT user_id) AS users
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY kind""",
                    (list(UPLOAD_EVENT_KINDS), str(days)))
        totals = {r["kind"]: {"events": r["n"], "users": r["users"]}
                  for r in cur.fetchall()}

        # The reasons, ranked. This is the answer to "what is actually
        # stopping people", which is the whole point of the surface.
        cur.execute("""SELECT COALESCE(detail->>'reason', '(none)') AS reason,
                              COUNT(*) AS n, COUNT(DISTINCT user_id) AS users
                       FROM client_events
                       WHERE kind = ANY(%s)
                         AND created_at > NOW() - (%s || ' days')::interval
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 20""",
                    (list(UPLOAD_FAILURE_KINDS), str(days)))
        by_reason = [{"reason": r["reason"], "events": r["n"],
                      "users": r["users"]} for r in cur.fetchall()]

    return jsonify({
        "days": days,
        "totals": totals,
        "by_reason": by_reason,
        "events": [dict(_upload_event_row(e),
                        user_id=e["user_id"], email=e["email"],
                        project_title=e["title"]) for e in rows],
    })


@admin_video_bp.route("/admin/video/projects/<int:project_id>/index",
                      methods=["GET"])
@admin_required
def video_project_index(project_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT i.json, i.pipeline_version, i.created_at
                       FROM indexes i
                       WHERE i.video_sha256 = (
                           SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "No index for this project"}), 404
    return jsonify({"index": row["json"],
                    "pipeline_version": row["pipeline_version"],
                    "created_at": row["created_at"].isoformat()})


@admin_video_bp.route("/admin/video/projects/<int:project_id>/llm_calls",
                      methods=["GET"])
@admin_required
def video_project_llm_calls(project_id):
    page = max(1, request.args.get("page", type=int) or 1)
    per = 20
    job_id = request.args.get("job_id", type=int)
    purpose = request.args.get("purpose")
    where = ["project_id = %s"]
    params = [project_id]
    if job_id is not None:
        where.append("job_id = %s")
        params.append(job_id)
    if purpose:
        where.append("purpose = %s")
        params.append(purpose)
    where_sql = " AND ".join(where)
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS n FROM llm_calls WHERE {where_sql}",
                    params)
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT id, job_id, purpose, model, request, response,
                               prompt_tokens, completion_tokens, created_at
                        FROM llm_calls WHERE {where_sql}
                        ORDER BY id DESC LIMIT %s OFFSET %s""",
                    params + [per, (page - 1) * per])
        rows = cur.fetchall()

    def _vision_urls(req):
        # Vision requests record image STORAGE KEYS (never bytes) — presign
        # them so the admin can see the exact tiles the model saw.
        names = (req or {}).get("images") or []
        urls = {}
        for n in names:
            if isinstance(n, str) and "/" in n:
                try:
                    urls[n] = storage.presign_get(
                        n, expires=storage.PRESIGN_EXPIRY)
                except Exception:
                    urls[n] = None
        return urls or None

    calls = []
    for r in rows:
        c = {**r, "created_at": r["created_at"].isoformat()}
        if (r["purpose"] or "").startswith("vision"):
            c["image_urls"] = _vision_urls(r["request"])
        calls.append(c)
    return jsonify({"total": total, "page": page, "per_page": per,
                    "calls": calls})


@admin_video_bp.route("/admin/video/costs", methods=["GET"])
@admin_required
def video_costs():
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.email, DATE(lc.created_at) AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc
            JOIN projects p ON p.id = lc.project_id
            JOIN users u ON u.id = p.user_id
            WHERE lc.created_at > NOW() - INTERVAL '30 days'
            GROUP BY u.email, DATE(lc.created_at)
            ORDER BY day DESC, est_cost DESC
            LIMIT 500
        """)
        rows = cur.fetchall()
        cur.execute(f"""
            SELECT lc.purpose, COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc GROUP BY lc.purpose ORDER BY est_cost DESC
        """)
        by_purpose = cur.fetchall()
        # Per MODEL, because that is the axis spend now splits on: free
        # accounts and paying ones can answer on different providers, and the
        # reasoning column is the evidence for whether a provider bills
        # thinking tokens on top of the completion (model_prices.py).
        cur.execute(f"""
            SELECT COALESCE(NULLIF(lc.model, ''), 'unknown') AS model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(lc.prompt_tokens), 0) AS tokens_in,
                   COALESCE(SUM(COALESCE(
                       (lc.response->>'cached_in')::float, 0)), 0) AS cached_in,
                   COALESCE(SUM(lc.completion_tokens), 0) AS tokens_out,
                   COALESCE(SUM(COALESCE(
                       (lc.response->>'reasoning_out')::float, 0)), 0)
                       AS reasoning_out,
                   {_cost_expr("lc")} AS est_cost
            FROM llm_calls lc
            WHERE lc.created_at > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY est_cost DESC
        """)
        by_model = cur.fetchall()
    return jsonify({
        "pricing": {"in_per_m": PRICE_IN_PER_M, "out_per_m": PRICE_OUT_PER_M,
                    "cached_in_per_m": PRICE_CACHED_IN_PER_M,
                    "models": model_prices.MODEL_PRICES,
                    "note": "each row is priced from its OWN model; the "
                            "in/out/cached numbers above are only the fallback "
                            "for a model not in the table. Cache-hit input is "
                            "priced separately; reasoning tokens are charged "
                            "only where the provider bills them on top of "
                            "completion_tokens."},
        "daily": [{**r, "day": r["day"].isoformat(),
                   "est_cost": round(float(r["est_cost"] or 0), 4)}
                  for r in rows],
        "by_purpose": [{**r, "est_cost": round(float(r["est_cost"] or 0), 4)}
                       for r in by_purpose],
        "by_model": [{**r,
                      "cached_in": int(float(r["cached_in"] or 0)),
                      "reasoning_out": int(float(r["reasoning_out"] or 0)),
                      "est_cost": round(float(r["est_cost"] or 0), 4)}
                     for r in by_model],
    })


UPLOAD_KINDS = ("original", "music", "image_ref", "video_clip")
# Repainted copies of a source (round 39 erase). Deliberately NOT in
# UPLOAD_KINDS — nobody uploaded them — but they are full-size video objects,
# so they belong in the per-user media view where their storage is visible.
CLEAN_KINDS = ("clean_source", "clean_proxy")


def _machine_made(alias):
    """SQL predicate: this asset was made BY THE PRODUCT, not uploaded.

    Every asset the agent synthesizes, generates, downloads from a pasted link
    or records from a website lands in the same kinds a person's own files do —
    a locally-built glitch card and a user's own clip are both 'video_clip'.
    Counting on kind alone made the admin report that a user "uploaded
    corrupt-digital.mp4" the day add_corrupt_screen shipped, when in fact the
    agent had built it inside their project. The producing tools each stamp
    meta: generated (colour/title cards, glitch screens, generate_image,
    generate_video), fetched (fetch_url), recorded (record_website).

    Compared as text, not cast to bool: a cast raises on any meta value that
    isn't a bool literal, which would take down the whole admin page over one
    odd row written by a future tool.
    """
    return (f"(COALESCE({alias}.meta->>'generated', '') = 'true' "
            f"OR COALESCE({alias}.meta->>'fetched', '') = 'true' "
            f"OR COALESCE({alias}.meta->>'recorded', '') = 'true')")


def _is_machine(a):
    """Python twin of _machine_made, for rows already fetched."""
    m = a.get("meta") or {}
    return bool(m.get("generated") or m.get("fetched") or m.get("recorded"))


def _asset_row(a):
    """One media row for the admin, carrying WHERE it came from.

    `origin` is the honest provenance line: which tool/model made it, or the
    URL it was pulled from — so a glitch card reads "local:glitch-digital" and
    a downloaded clip shows its source page, instead of both looking like
    files the customer chose to upload.
    """
    m = a.get("meta") or {}
    origin = None
    if m.get("generated"):
        origin = m.get("model") or "generated"
    elif m.get("fetched"):
        origin = m.get("source_url") or "fetched from a link"
    elif m.get("recorded"):
        origin = m.get("source_url") or "website capture"
    return {"id": a["id"], "project_id": a["project_id"],
            "kind": a["kind"],
            "filename": m.get("filename"),
            "bytes": a["bytes"], "duration_s": a["duration_s"],
            "width": a["width"], "height": a["height"],
            "created_at": a["created_at"].isoformat(),
            "origin": origin,
            "url": _presign(a["storage_key"])}


@admin_video_bp.route("/admin/video/users", methods=["GET"])
@admin_required
def video_users():
    search = (request.args.get("search") or "").strip()
    with adb() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.id, u.email, u.created_at, u.plan,
                   u.credits_daily, u.credits_bonus, u.credits_monthly,
                   u.credits_balance,
                   pr.n AS projects,
                   COALESCE(m.msgs, 0) AS messages,
                   COALESCE(m.unserved, 0) AS unserved,
                   COALESCE(j.turns, 0) AS turns,
                   COALESCE(j.exports, 0) AS exports,
                   COALESCE(a.uploads, 0) AS uploads,
                   COALESCE(a.generated, 0) AS generated,
                   COALESCE(a.bytes, 0) AS storage_bytes,
                   COALESCE(l.tokens_in, 0) AS tokens_in,
                   COALESCE(l.tokens_out, 0) AS tokens_out,
                   COALESCE(l.est_cost, 0) AS est_cost,
                   GREATEST(m.last, j.last, a.last) AS last_active
            FROM users u
            JOIN (SELECT user_id, COUNT(*) AS n FROM projects
                  GROUP BY user_id) pr ON pr.user_id = u.id
            LEFT JOIN (SELECT p2.user_id,
                              COUNT(*) FILTER (WHERE cm.role='user') AS msgs,
                              COUNT(*) FILTER (WHERE cm.role='user'
                                  AND {UNSERVED_EXISTS}) AS unserved,
                              MAX(cm.created_at) AS last
                       FROM chat_messages cm
                       JOIN projects p2 ON p2.chat_session_id = cm.session_id
                       GROUP BY p2.user_id) m ON m.user_id = u.id
            LEFT JOIN (SELECT user_id,
                              COUNT(*) FILTER (WHERE type='agent_turn')
                                  AS turns,
                              COUNT(*) FILTER (WHERE type='final'
                                  AND state='done') AS exports,
                              MAX(updated_at) AS last
                       FROM video_jobs GROUP BY user_id) j ON j.user_id = u.id
            LEFT JOIN (SELECT p3.user_id,
                              COUNT(*) FILTER (WHERE ast.kind IN %s
                                  AND NOT {_machine_made('ast')}) AS uploads,
                              COUNT(*) FILTER (WHERE {_machine_made('ast')})
                                  AS generated,
                              SUM(ast.bytes)::bigint AS bytes,
                              MAX(ast.created_at) AS last
                       FROM assets ast
                       JOIN projects p3 ON p3.id = ast.project_id
                       GROUP BY p3.user_id) a ON a.user_id = u.id
            LEFT JOIN (SELECT p4.user_id,
                              SUM(lc.prompt_tokens) AS tokens_in,
                              SUM(lc.completion_tokens) AS tokens_out,
                              """ + _cost_expr("lc") + """ AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            WHERE u.email ILIKE %s
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """, (UPLOAD_KINDS, f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"users": [
        {"id": r["id"], "email": r["email"],
         "created_at": r["created_at"].isoformat(),
         "plan": r["plan"],
         "credits": {"daily": float(r["credits_daily"] or 0),
                     "bonus": float(r["credits_bonus"] or 0),
                     "monthly": float(r["credits_monthly"] or 0),
                     "balance": float(r["credits_balance"] or 0)},
         "projects": r["projects"], "messages": r["messages"],
         "turns": r["turns"], "exports": r["exports"],
         "unserved": r["unserved"],
         "uploads": r["uploads"],
         "generated": r["generated"],
         "storage_bytes": int(r["storage_bytes"] or 0),
         "tokens_in": int(r["tokens_in"] or 0),
         "tokens_out": int(r["tokens_out"] or 0),
         "est_cost": round(float(r["est_cost"] or 0), 4),
         "last_active": r["last_active"].isoformat()
             if r["last_active"] else None}
        for r in rows]})


COHORT_STAGES = [
    ("signed_up", "Signed up"),
    ("uploaded", "Uploaded a video"),
    ("messaged", "Messaged the editor"),
    ("exported", "Exported a video"),
    ("trial", "Started a trial"),
    ("paid", "Paid"),
]

# TRIAL and PAID are two different questions and were being answered by one
# number. Every plan sells a 3-day trial, Paddle creates the subscription at
# checkout, and `is_subscribed` flips on day zero — so the old "Paid" column
# counted everyone who had ever handed over a card, whether or not a payment
# ever cleared. On a funnel whose whole purpose is to show whether trials
# convert, that is the one column that must not blur them.
#
#   trial  ever started a trial. The demand signal — it is what the pricing
#          page and the discount are optimising, and it is worth counting even
#          for someone who cancelled on day one.
#   paid   money actually moved: the trial ran its course and Paddle charged
#          (trial_status='converted'), or the account subscribed without a
#          trial at all (the grandfathered plans predate trials).
#
# Both read `trial_status`, written from Paddle's own subscription status by
# backend/trial_state.py — never inferred from a timer here.
COHORT_TRIAL_SQL = "u.trial_started_at IS NOT NULL OR u.trial_status IS NOT NULL"
COHORT_PAID_SQL = """
    u.trial_status = 'converted'
    OR (u.trial_started_at IS NULL
        AND (COALESCE(u.is_subscribed, 0) = 1
             OR COALESCE(u.plan, 'free') NOT IN ('free', '')))
"""
# Before the round-46 migration those columns do not exist. Falling back keeps
# the tab rendering (with trial folded into paid, exactly as it read before)
# instead of 500ing the whole page on a missing column.
COHORT_TRIAL_FALLBACK = "FALSE"
COHORT_PAID_FALLBACK = ("COALESCE(u.is_subscribed, 0) = 1 "
                        "OR COALESCE(u.plan, 'free') NOT IN ('free', '')")


def _trial_columns_exist(cur):
    try:
        cur.execute("""SELECT COUNT(*) AS n FROM information_schema.columns
                        WHERE table_name = 'users'
                          AND column_name IN ('trial_status',
                                              'trial_started_at')""")
        row = cur.fetchone()
        return int((row or {}).get("n") or 0) == 2
    except Exception:                                       # pragma: no cover
        return False

# Empty periods to draw BEFORE the metrics epoch. Without a run-up the chart
# opens on the first real cohort's conversion — a line that starts pinned to the
# top of the axis with nothing behind it to read it against. A short flat-zero
# lead-in makes the relaunch land as a visible jump. It is not a fudge: the
# series counts post-relaunch accounts only, and there were genuinely zero of
# those before the epoch.
COHORT_LEAD_IN = {"day": 7, "week": 3, "month": 2}


@admin_video_bp.route("/admin/video/cohorts", methods=["GET"])
@admin_required
def video_cohorts():
    """Lean-Startup cohort funnel analysis: group users by signup cohort and
    track what fraction of each cohort reached each activation/monetization
    stage. Unlike a running total, each signup cohort is measured
    independently, so product improvements show up as newer cohorts
    converting better. ?period=week|month|day (default week)."""
    period = (request.args.get("period") or "week").strip().lower()
    if period not in ("day", "week", "month"):
        period = "week"
    with adb() as conn:
        cur = conn.cursor()
        have_trials = _trial_columns_exist(cur)
        trial_sql = (COHORT_TRIAL_SQL if have_trials
                     else COHORT_TRIAL_FALLBACK)
        paid_sql = COHORT_PAID_SQL if have_trials else COHORT_PAID_FALLBACK
        cur.execute("""
            WITH base AS (
                SELECT u.id,
                    date_trunc(%s, u.created_at) AS cohort,
                    EXISTS(SELECT 1 FROM projects p
                           JOIN assets a ON a.project_id = p.id
                           WHERE p.user_id = u.id AND a.kind = 'original')
                        AS uploaded,
                    EXISTS(SELECT 1 FROM projects p
                           JOIN chat_messages cm
                                ON cm.session_id = p.chat_session_id
                           WHERE p.user_id = u.id AND cm.role = 'user')
                        AS messaged,
                    EXISTS(SELECT 1 FROM video_jobs vj
                           WHERE vj.user_id = u.id AND vj.type = 'final'
                             AND vj.state = 'done')
                        AS exported,
                    (""" + trial_sql + """) AS trial,
                    (""" + paid_sql + """) AS paid
                FROM users u
                -- Only real post-relaunch accounts: old-idea signups and the
                -- long-lived test accounts (all created before the metrics
                -- epoch) otherwise pollute every cohort, and a manually-credited
                -- test account even shows up under "paid".
                WHERE u.created_at IS NOT NULL AND """ + _scope('u') + """
            ),
            agg AS (
                SELECT cohort,
                       COUNT(*) AS signed_up,
                       COUNT(*) FILTER (WHERE uploaded) AS uploaded,
                       COUNT(*) FILTER (WHERE messaged) AS messaged,
                       COUNT(*) FILTER (WHERE exported) AS exported,
                       COUNT(*) FILTER (WHERE trial) AS trial,
                       COUNT(*) FILTER (WHERE paid) AS paid
                FROM base
                GROUP BY cohort
            ),
            -- Every period from the lead-in through now, so the x-axis is a
            -- real timeline. GROUP BY alone emits only periods that HAD
            -- signups, which silently closes the gaps: a dead week vanishes
            -- and the line jumps straight to the next active one as if the
            -- week never happened.
            spine AS (
                SELECT generate_series(
                    date_trunc(%s, %s::timestamptz)
                        - (%s * ('1 ' || %s)::interval),
                    date_trunc(%s, NOW()),
                    ('1 ' || %s)::interval) AS cohort
            )
            SELECT s.cohort,
                   COALESCE(a.signed_up, 0) AS signed_up,
                   COALESCE(a.uploaded, 0) AS uploaded,
                   COALESCE(a.messaged, 0) AS messaged,
                   COALESCE(a.exported, 0) AS exported,
                   COALESCE(a.trial, 0) AS trial,
                   COALESCE(a.paid, 0) AS paid,
                   (s.cohort < date_trunc(%s, %s::timestamptz)) AS lead_in
            FROM spine s
            LEFT JOIN agg a ON a.cohort = s.cohort
            ORDER BY s.cohort
        """, (period, period, METRICS_EPOCH,
              COHORT_LEAD_IN.get(period, 3), period,
              period, period, period, METRICS_EPOCH))
        rows = cur.fetchall()
    cohorts = [{
        "cohort": r["cohort"].date().isoformat() if r["cohort"] else None,
        "signed_up": int(r["signed_up"] or 0),
        "uploaded": int(r["uploaded"] or 0),
        "messaged": int(r["messaged"] or 0),
        "exported": int(r["exported"] or 0),
        "trial": int(r["trial"] or 0),
        "paid": int(r["paid"] or 0),
        # Pre-epoch run-up: real zero for this series, but not a cohort that
        # ever existed — the funnel table below skips these rows.
        "lead_in": bool(r["lead_in"]),
    } for r in rows]
    return jsonify({
        "period": period,
        "metrics_epoch": METRICS_EPOCH,
        "stages": [{"key": k, "label": lbl} for k, lbl in COHORT_STAGES],
        "cohorts": cohorts,
        "trial_tracking": have_trials,
        "note": ("Each row is the cohort of users who signed up in that "
                 "period; each stage counts how many of THEM ever reached it "
                 "(a funnel per cohort, not a running total). \"Started a "
                 "trial\" is ever-started and never drops back out, so a "
                 "cancelled trial still counts — it is the demand signal. "
                 "\"Paid\" is money that actually moved: a trial that "
                 "converted, or a subscription taken without a trial. The gap "
                 "between the two columns is your trial conversion rate."
                 + ("" if have_trials else
                    " NOTE: the trial columns are not present on this "
                    "database, so \"Started a trial\" reads 0 and \"Paid\" "
                    "falls back to current subscription state.")),
    })


@admin_video_bp.route("/admin/video/users/<int:user_id>", methods=["GET"])
@admin_required
def video_user_detail(user_id):
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT id, email, created_at, plan, is_subscribed,
                              credits_daily, credits_bonus, credits_monthly,
                              credits_balance, credits_daily_reset
                       FROM users WHERE id = %s""", (user_id,))
        u = cur.fetchone()
        if not u:
            return jsonify({"error": "User not found"}), 404

        cur.execute(f"""
            SELECT p.id, p.title, p.created_at,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user') AS messages,
                   (SELECT COUNT(*) FROM video_jobs v
                    WHERE v.project_id = p.id
                      AND v.type='agent_turn') AS turns,
                   (SELECT COUNT(*) FROM video_jobs vf
                    WHERE vf.project_id = p.id AND vf.type='final'
                      AND vf.state='done') AS exports,
                   (SELECT COUNT(*) FROM edls e
                    WHERE e.project_id = p.id) AS versions,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.session_id = p.chat_session_id
                      AND cm.role='user' AND {UNSERVED_EXISTS}) AS unserved,
                   GREATEST(
                     (SELECT MAX(cm.created_at) FROM chat_messages cm
                      WHERE cm.session_id = p.chat_session_id),
                     (SELECT MAX(v.updated_at) FROM video_jobs v
                      WHERE v.project_id = p.id),
                     (SELECT MAX(a.created_at) FROM assets a
                      WHERE a.project_id = p.id)) AS last_activity
            FROM projects p WHERE p.user_id = %s
            ORDER BY p.id DESC
        """, (user_id,))
        projects = cur.fetchall()

        cur.execute("""SELECT a.id, a.project_id, a.kind, a.storage_key,
                              a.bytes, a.duration_s, a.width, a.height,
                              a.meta, a.created_at
                       FROM assets a
                       JOIN projects p ON p.id = a.project_id
                       WHERE p.user_id = %s AND a.kind IN %s
                       ORDER BY a.id DESC LIMIT 100""",
                    (user_id, UPLOAD_KINDS + CLEAN_KINDS))
        assets = cur.fetchall()

        cur.execute("""SELECT job_id, credits_used, tokens_used, created_at
                       FROM job_credits WHERE user_id = %s
                       ORDER BY created_at DESC LIMIT 50""", (user_id,))
        ledger = cur.fetchall()

        # Every upload this account attempted, including the ones that never
        # reached a project. For an account with no projects at all this is the
        # ONLY row that distinguishes "tried and was refused" from "signed up
        # and left" — which is the difference between a bug and a bounce.
        cur.execute("""SELECT id, kind, detail, created_at, project_id
                       FROM client_events
                       WHERE user_id = %s AND kind = ANY(%s)
                       ORDER BY id DESC LIMIT 100""",
                    (user_id, list(UPLOAD_EVENT_KINDS)))
        upload_events = [_upload_event_row(e) for e in cur.fetchall()]

    return jsonify({
        "user": {"id": u["id"], "email": u["email"],
                 "created_at": u["created_at"].isoformat(),
                 "plan": u["plan"], "is_subscribed": u["is_subscribed"],
                 "credits": {"daily": float(u["credits_daily"] or 0),
                             "bonus": float(u["credits_bonus"] or 0),
                             "monthly": float(u["credits_monthly"] or 0),
                             "balance": float(u["credits_balance"] or 0),
                             "daily_reset": u["credits_daily_reset"]
                                 .isoformat()
                                 if u["credits_daily_reset"] else None}},
        "projects": [
            {"id": p["id"], "title": p["title"],
             "created_at": p["created_at"].isoformat(),
             "messages": p["messages"], "turns": p["turns"],
             "exports": p["exports"],
             "versions": p["versions"], "unserved": p["unserved"],
             "last_activity": p["last_activity"].isoformat()
                 if p["last_activity"] else None}
            for p in projects],
        # Two lists, never one. These rows share the same kinds — the agent's
        # own media is stamped in meta — and merging them told the founder a
        # user had uploaded a file the AGENT had built in their project.
        "uploads": [_asset_row(a) for a in assets if not _is_machine(a)],
        "generated": [_asset_row(a) for a in assets if _is_machine(a)],
        "upload_events": upload_events,
        "upload_failures": len([e for e in upload_events
                                if e["kind"] in UPLOAD_FAILURE_KINDS]),
        "ledger": [
            {"job_id": l["job_id"],
             "credits_used": float(l["credits_used"] or 0),
             "tokens_used": int(l["tokens_used"] or 0),
             "created_at": l["created_at"].isoformat()}
            for l in ledger],
    })


# ── Watermark toggles (round 41) ─────────────────────────────────────────
# Live operator switches for the free-tier mark, read by the worker on every
# render (worker/db.video_settings). Deliberately DB-backed rather than env:
# an env var needs a redeploy of the Cloud Run executor to take effect, which
# is minutes and a build — useless as a switch you want to flip and observe.
#
# Table is created lazily here, mirroring newsletter_settings, so no schema
# change lands in models.py.
def _ensure_video_settings(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS video_settings (
            id INTEGER PRIMARY KEY,
            watermark_enabled BOOLEAN DEFAULT TRUE,
            watermark_force BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("INSERT INTO video_settings (id) VALUES (1) "
                "ON CONFLICT (id) DO NOTHING")


@admin_video_bp.route("/admin/video/settings", methods=["GET"])
@admin_required
def video_settings_get():
    with adb() as conn:
        cur = conn.cursor()
        _ensure_video_settings(cur)
        conn.commit()
        cur.execute("SELECT watermark_enabled, watermark_force, updated_at "
                    "FROM video_settings WHERE id = 1")
        row = cur.fetchone() or {}
    return jsonify({
        "watermark_enabled": bool(row.get("watermark_enabled", True)),
        "watermark_force": bool(row.get("watermark_force", False)),
        "updated_at": (row.get("updated_at").isoformat()
                       if row.get("updated_at") else None),
    })


@admin_video_bp.route("/admin/video/settings", methods=["POST"])
@admin_required
def video_settings_set():
    body = request.get_json(silent=True) or {}
    with adb() as conn:
        cur = conn.cursor()
        _ensure_video_settings(cur)
        # Only the keys actually sent are written, so the two switches can be
        # flipped independently without one clobbering the other.
        sets, vals = [], []
        for key, col in (("watermark_enabled", "watermark_enabled"),
                         ("watermark_force", "watermark_force")):
            if key in body:
                sets.append(f"{col} = %s")
                vals.append(bool(body[key]))
        if not sets:
            return jsonify({"error": "nothing to update"}), 400
        cur.execute(f"UPDATE video_settings SET {', '.join(sets)}, "
                    "updated_at = NOW() WHERE id = 1", vals)
        conn.commit()
        cur.execute("SELECT watermark_enabled, watermark_force "
                    "FROM video_settings WHERE id = 1")
        row = cur.fetchone() or {}
    return jsonify({"ok": True,
                    "watermark_enabled": bool(row.get("watermark_enabled")),
                    "watermark_force": bool(row.get("watermark_force"))})
