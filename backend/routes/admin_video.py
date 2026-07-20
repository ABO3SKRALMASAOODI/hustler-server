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
import storage

admin_video_bp = Blueprint("admin_video", __name__)

# Estimated $ per 1M tokens when the API reports usage (default = Grok 4.5,
# $2 in / $6 out; override via env if the model or pricing changes).
PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "2.0"))
PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "6.0"))


def adb():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _cost_expr():
    return (f"(COALESCE(SUM(prompt_tokens),0) * {PRICE_IN_PER_M} + "
            f"COALESCE(SUM(completion_tokens),0) * {PRICE_OUT_PER_M}) "
            "/ 1000000.0")


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
                              (SUM(COALESCE(lc.prompt_tokens,0)) * %s
                               + SUM(COALESCE(lc.completion_tokens,0)) * %s)
                              / 1000000.0 AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            GROUP BY u.id, u.email, m.msgs, j.done, j.failed, j.active,
                     a.bytes, l.tokens_in, l.tokens_out, l.est_cost,
                     j.last, m.last
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """, (PRICE_IN_PER_M, PRICE_OUT_PER_M))
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

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.x.ai/v1")

    def _provider(b):
        b = (b or "").lower()
        if "dashscope" in b:
            return "DashScope (Qwen)"
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
                "agent_model": os.getenv("AGENT_MODEL", "grok-4.5"),
                "vision_model": os.getenv("VISION_MODEL", "grok-4.5"),
                "image_gen_model": os.getenv("IMAGE_GEN_MODEL", "grok-2-image"),
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
                      AND vf.state='done') AS last_export
            FROM projects p JOIN users u ON u.id = p.user_id
            WHERE u.email ILIKE %s OR p.title ILIKE %s
            ORDER BY p.id DESC LIMIT 100
        """, (f"%{search}%", f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"projects": [
        {**r, "created_at": r["created_at"].isoformat(),
         "last_job": r["last_job"].isoformat() if r["last_job"] else None,
         "last_export": r["last_export"].isoformat() if r["last_export"] else None}
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

    return jsonify({
        "project": {"id": p["id"], "title": p["title"], "email": p["email"],
                    "created_at": p["created_at"].isoformat()},
        "exports": exports,
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
                   {_cost_expr()} AS est_cost
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
                   {_cost_expr()} AS est_cost
            FROM llm_calls lc GROUP BY lc.purpose ORDER BY est_cost DESC
        """)
        by_purpose = cur.fetchall()
    return jsonify({
        "pricing": {"in_per_m": PRICE_IN_PER_M, "out_per_m": PRICE_OUT_PER_M,
                    "note": "estimated from API usage fields when present"},
        "daily": [{**r, "day": r["day"].isoformat(),
                   "est_cost": round(float(r["est_cost"] or 0), 4)}
                  for r in rows],
        "by_purpose": [{**r, "est_cost": round(float(r["est_cost"] or 0), 4)}
                       for r in by_purpose],
    })


UPLOAD_KINDS = ("original", "music", "image_ref", "video_clip")


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
                              COUNT(*) FILTER (WHERE ast.kind IN %s)
                                  AS uploads,
                              SUM(ast.bytes)::bigint AS bytes,
                              MAX(ast.created_at) AS last
                       FROM assets ast
                       JOIN projects p3 ON p3.id = ast.project_id
                       GROUP BY p3.user_id) a ON a.user_id = u.id
            LEFT JOIN (SELECT p4.user_id,
                              SUM(lc.prompt_tokens) AS tokens_in,
                              SUM(lc.completion_tokens) AS tokens_out,
                              (SUM(COALESCE(lc.prompt_tokens,0)) * %s
                               + SUM(COALESCE(lc.completion_tokens,0)) * %s)
                              / 1000000.0 AS est_cost
                       FROM llm_calls lc
                       JOIN projects p4 ON p4.id = lc.project_id
                       GROUP BY p4.user_id) l ON l.user_id = u.id
            WHERE u.email ILIKE %s
            ORDER BY last_active DESC NULLS LAST
            LIMIT 200
        """, (UPLOAD_KINDS, PRICE_IN_PER_M, PRICE_OUT_PER_M, f"%{search}%"))
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
    ("paid", "Paid (current)"),
]

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
                    (COALESCE(u.is_subscribed, 0) = 1
                     OR COALESCE(u.plan, 'free') NOT IN ('free', ''))
                        AS paid
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
        "note": ("Each row is the cohort of users who signed up in that "
                 "period; each stage counts how many of THEM ever reached it "
                 "(a funnel per cohort, not a running total). \"Paid\" "
                 "reflects CURRENT subscription state, so a canceled user "
                 "drops back out of it."),
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
                    (user_id, UPLOAD_KINDS))
        uploads = cur.fetchall()

        cur.execute("""SELECT job_id, credits_used, tokens_used, created_at
                       FROM job_credits WHERE user_id = %s
                       ORDER BY created_at DESC LIMIT 50""", (user_id,))
        ledger = cur.fetchall()

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
        "uploads": [
            {"id": a["id"], "project_id": a["project_id"],
             "kind": a["kind"],
             "filename": (a.get("meta") or {}).get("filename"),
             "bytes": a["bytes"], "duration_s": a["duration_s"],
             "width": a["width"], "height": a["height"],
             "created_at": a["created_at"].isoformat(),
             "url": _presign(a["storage_key"])}
            for a in uploads],
        "ledger": [
            {"job_id": l["job_id"],
             "credits_used": float(l["credits_used"] or 0),
             "tokens_used": int(l["tokens_used"] or 0),
             "created_at": l["created_at"].isoformat()}
            for l in ledger],
    })
