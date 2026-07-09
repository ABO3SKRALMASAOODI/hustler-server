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

from routes.admin import admin_required
import storage

admin_video_bp = Blueprint("admin_video", __name__)

# Estimated $ per 1M tokens when the API reports usage (qwen-plus ballpark;
# override via env if the model or pricing changes).
PRICE_IN_PER_M = float(os.getenv("LLM_PRICE_IN_PER_M", "0.4"))
PRICE_OUT_PER_M = float(os.getenv("LLM_PRICE_OUT_PER_M", "1.2"))


def adb():
    return psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)


def _cost_expr():
    return (f"(COALESCE(SUM(prompt_tokens),0) * {PRICE_IN_PER_M} + "
            f"COALESCE(SUM(completion_tokens),0) * {PRICE_OUT_PER_M}) "
            "/ 1000000.0")


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
                    WHERE e.project_id = p.id) AS versions
            FROM projects p JOIN users u ON u.id = p.user_id
            WHERE u.email ILIKE %s OR p.title ILIKE %s
            ORDER BY p.id DESC LIMIT 100
        """, (f"%{search}%", f"%{search}%"))
        rows = cur.fetchall()
    return jsonify({"projects": [
        {**r, "created_at": r["created_at"].isoformat(),
         "last_job": r["last_job"].isoformat() if r["last_job"] else None}
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

        # Thumbnails + contact sheets have no asset rows — their keys live
        # in the index JSON and in render results. Surface them here so the
        # admin grid can show everything.
        cur.execute("""SELECT i.json FROM indexes i
                       WHERE i.video_sha256 = (
                           SELECT sha256 FROM assets
                           WHERE project_id = %s AND kind='original'
                           ORDER BY id DESC LIMIT 1)""", (project_id,))
        idx_row = cur.fetchone()

    def _presign(key):
        if not storage.is_configured():
            return None
        try:
            return storage.presign_get(key)
        except Exception:
            return None

    out_assets = []
    for a in assets:
        row = {"id": a["id"], "kind": a["kind"],
               "storage_key": a["storage_key"], "bytes": a["bytes"],
               "duration_s": a["duration_s"], "width": a["width"],
               "height": a["height"], "meta": a.get("meta") or {},
               "created_at": a["created_at"].isoformat()}
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

    return jsonify({
        "project": {"id": p["id"], "title": p["title"], "email": p["email"],
                    "created_at": p["created_at"].isoformat()},
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
    with adb() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM llm_calls WHERE project_id=%s",
                    (project_id,))
        total = cur.fetchone()["n"]
        cur.execute("""SELECT id, job_id, purpose, model, request, response,
                              prompt_tokens, completion_tokens, created_at
                       FROM llm_calls WHERE project_id = %s
                       ORDER BY id DESC LIMIT %s OFFSET %s""",
                    (project_id, per, (page - 1) * per))
        rows = cur.fetchall()

    def _vision_urls(req):
        # Vision requests record image STORAGE KEYS (never bytes) — presign
        # them so the admin can see the exact tiles the model saw.
        names = (req or {}).get("images") or []
        urls = {}
        for n in names:
            if isinstance(n, str) and "/" in n:
                try:
                    urls[n] = storage.presign_get(n)
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
