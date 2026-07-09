"""
Video editor API — projects, direct-to-storage uploads, chat -> agent turns,
EDL versions, renders.

The API never touches media bytes and never runs ffmpeg/whisper/LLM loops:
it stores pointers + JSON and enqueues rows in video_jobs for the worker
(see worker/ at the repo root). Chat history reuses the existing
chat_sessions / chat_messages tables (one session per project, plus an
'activity' role for agent tool calls).
"""

import importlib.util
import json
import os
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from flask import Blueprint, request, jsonify, current_app

from routes.auth import token_required
import storage

# The EDL schema's single source of truth is worker/schemas.py (pure
# pydantic, no worker-internal imports). Loaded under a unique module name so
# nothing in the worker dir can shadow backend modules.
_schemas_path = os.path.join(os.path.dirname(__file__), "..", "..",
                             "worker", "schemas.py")
_spec = importlib.util.spec_from_file_location(
    "worker_schemas", os.path.abspath(_schemas_path))
wschemas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wschemas)

video_bp = Blueprint("video", __name__)

MAX_CONCURRENT_JOBS_PER_USER = int(os.getenv("MAX_CONCURRENT_JOBS_PER_USER", "3"))
MESSAGES_PER_HOUR = int(os.getenv("MESSAGES_PER_HOUR", "20"))

# Keep in sync with worker/config.py — indexes built by an older pipeline
# version are re-indexed automatically when the project is opened.
PIPELINE_VERSION = int(os.getenv("PIPELINE_VERSION", "2"))

VIDEO_KINDS = ("original", "proxy", "audio", "thumb", "sheet", "render",
               "music", "image_ref", "video_clip")


@contextmanager
def vdb():
    conn = psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _project_for_user(cur, project_id, user_id):
    cur.execute("SELECT * FROM projects WHERE id = %s AND user_id = %s",
                (project_id, int(user_id)))
    return cur.fetchone()


def _running_jobs_count(cur, user_id):
    cur.execute("""SELECT COUNT(*) AS n FROM video_jobs
                   WHERE user_id = %s AND state IN ('queued','running')""",
                (int(user_id),))
    return cur.fetchone()["n"]


def _enqueue(cur, project_id, user_id, jtype, payload):
    cur.execute("""INSERT INTO video_jobs (project_id, user_id, type, payload)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (project_id, int(user_id), jtype, Json(payload)))
    return cur.fetchone()["id"]


def _active_original(cur, project_id):
    """Latest uploaded original video — the video this project edits."""
    cur.execute("""SELECT * FROM assets
                   WHERE project_id = %s AND kind = 'original'
                   ORDER BY id DESC LIMIT 1""", (project_id,))
    return cur.fetchone()


def _index_row(cur, sha256):
    if not sha256:
        return None
    cur.execute("""SELECT id, created_at, pipeline_version FROM indexes
                   WHERE video_sha256 = %s""", (sha256,))
    return cur.fetchone()


def _latest_edl(cur, project_id):
    cur.execute("""SELECT version, json, created_by, created_at FROM edls
                   WHERE project_id = %s ORDER BY version DESC LIMIT 1""",
                (project_id,))
    return cur.fetchone()


def _asset_out(a):
    return {
        "id": a["id"], "kind": a["kind"], "storage_key": a["storage_key"],
        "bytes": a["bytes"], "duration_s": a["duration_s"],
        "width": a["width"], "height": a["height"], "fps": a["fps"],
        "sha256": a["sha256"], "meta": a.get("meta") or {},
        "created_at": a["created_at"].isoformat() if a.get("created_at") else None,
    }


# ------------------------------------------------------------------ #
#  Health — lets the frontend know which pieces are configured        #
# ------------------------------------------------------------------ #

@video_bp.route("/video/health", methods=["GET"])
def video_health():
    return jsonify({
        "ok": True,
        "storage_configured": storage.is_configured(),
        "llm_configured": bool(os.getenv("OPENAI_API_KEY")),
    })


# ------------------------------------------------------------------ #
#  Projects                                                            #
# ------------------------------------------------------------------ #

@video_bp.route("/projects", methods=["POST"])
@token_required
def create_project(user_id):
    data = request.get_json() or {}
    title = (data.get("title") or "").strip() or "Untitled project"
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s) RETURNING id",
                    (int(user_id), title))
        session_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO projects (user_id, title, chat_session_id)
                       VALUES (%s, %s, %s) RETURNING id, title, created_at""",
                    (int(user_id), title, session_id))
        p = cur.fetchone()
    return jsonify({"project": {"id": p["id"], "title": p["title"],
                                "created_at": p["created_at"].isoformat()}}), 201


@video_bp.route("/projects", methods=["GET"])
@token_required
def list_projects(user_id):
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id, p.title, p.created_at,
                   EXISTS (SELECT 1 FROM assets a
                           WHERE a.project_id = p.id AND a.kind = 'original')
                       AS has_video
            FROM projects p
            WHERE p.user_id = %s
            ORDER BY p.id DESC
            LIMIT 100
        """, (int(user_id),))
        rows = cur.fetchall()
    return jsonify({"projects": [
        {"id": r["id"], "title": r["title"], "has_video": r["has_video"],
         "created_at": r["created_at"].isoformat()} for r in rows
    ]})


@video_bp.route("/projects/<int:project_id>", methods=["GET"])
@token_required
def get_project(user_id, project_id):
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404

        cur.execute("""SELECT * FROM assets WHERE project_id = %s
                       ORDER BY id DESC LIMIT 200""", (project_id,))
        assets = cur.fetchall()

        original = _active_original(cur, project_id)
        indexed = bool(original and _index_row(cur, original["sha256"]))

        edl = _latest_edl(cur, project_id)

        # Latest job of each type, so the client can drive progress UI.
        cur.execute("""
            SELECT DISTINCT ON (type) id, type, state, progress, error,
                   payload, result, updated_at
            FROM video_jobs WHERE project_id = %s
            ORDER BY type, id DESC
        """, (project_id,))
        jobs = {r["type"]: {
            "id": r["id"], "state": r["state"], "progress": r["progress"],
            "error": r["error"], "payload": r["payload"], "result": r["result"],
            "updated_at": r["updated_at"].isoformat(),
        } for r in cur.fetchall()}

    return jsonify({
        "project": {"id": p["id"], "title": p["title"],
                    "created_at": p["created_at"].isoformat()},
        "assets": [_asset_out(a) for a in assets],
        "video": _asset_out(original) if original else None,
        "indexed": indexed,
        "latest_edl": ({"version": edl["version"], "json": edl["json"],
                        "created_by": edl["created_by"]} if edl else None),
        "jobs": jobs,
    })


@video_bp.route("/projects/<int:project_id>/title", methods=["PATCH"])
@token_required
def rename_project(user_id, project_id):
    title = ((request.get_json() or {}).get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404
        cur.execute("UPDATE projects SET title = %s WHERE id = %s",
                    (title[:120], project_id))
    return jsonify({"ok": True})


# ------------------------------------------------------------------ #
#  Uploads — presigned, direct to object storage                       #
# ------------------------------------------------------------------ #

@video_bp.route("/projects/<int:project_id>/uploads", methods=["POST"])
@token_required
def create_upload(user_id, project_id):
    if not storage.is_configured():
        return jsonify({"error": "Video storage is not configured yet"}), 503

    data = request.get_json() or {}
    filename = data.get("filename") or ""
    nbytes = data.get("bytes")
    kind = data.get("kind") or "original"
    if kind not in ("original", "music", "image", "clip"):
        return jsonify({"error": "kind must be original, music, image "
                                 "or clip"}), 400

    try:
        ext, content_type = storage.validate_upload(filename, nbytes, kind)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404

    key = storage.new_original_key(project_id, ext, kind)
    try:
        out = storage.presign_upload(key, nbytes, content_type)
    except Exception as e:
        current_app.logger.exception("presign failed")
        return jsonify({"error": f"Could not prepare upload: {e}"}), 502
    out["kind"] = kind
    return jsonify(out)


@video_bp.route("/projects/<int:project_id>/uploads/complete", methods=["POST"])
@token_required
def complete_upload(user_id, project_id):
    if not storage.is_configured():
        return jsonify({"error": "Video storage is not configured yet"}), 503

    data = request.get_json() or {}
    key = data.get("storage_key") or ""
    kind = data.get("kind") or "original"
    filename = data.get("filename") or ""
    upload_id = data.get("upload_id")
    parts = data.get("parts") or []
    duration_s = data.get("duration_s")   # client-probed, music only

    prefix = storage.KEY_PREFIX.get(kind, "originals")
    if not key.startswith(f"{prefix}/{project_id}/"):
        return jsonify({"error": "storage_key does not belong to this project"}), 400

    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        if kind == "original" and \
                _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return jsonify({"error": "Too many jobs running. "
                                     "Wait for one to finish."}), 429

    if upload_id:
        try:
            storage.complete_multipart(key, upload_id, parts)
        except Exception as e:
            storage.abort_multipart(key, upload_id)
            return jsonify({"error": f"Upload could not be finalized: {e}"}), 400

    nbytes = storage.head_bytes(key)
    if nbytes is None:
        return jsonify({"error": "Uploaded file not found in storage"}), 400
    if nbytes > storage.max_upload_bytes():
        return jsonify({"error": "File exceeds the upload size limit"}), 400

    asset_kind = {"original": "original", "music": "music",
                  "image": "image_ref", "clip": "video_clip"}[kind]
    try:
        duration_s = min(max(float(duration_s), 0.1), 4 * 3600) \
            if duration_s else None
    except (TypeError, ValueError):
        duration_s = None

    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO assets (project_id, kind, storage_key,
                                           bytes, duration_s, meta)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                    (project_id, asset_kind, key, nbytes,
                     duration_s if kind in ("music", "clip") else None,
                     Json({"filename": filename})))
        asset_id = cur.fetchone()["id"]
        job_id = None
        if kind == "original":
            job_id = _enqueue(cur, project_id, user_id, "index",
                              {"asset_id": asset_id})

    return jsonify({"asset_id": asset_id, "index_job_id": job_id,
                    "kind": asset_kind})


# ------------------------------------------------------------------ #
#  Index                                                               #
# ------------------------------------------------------------------ #

@video_bp.route("/projects/<int:project_id>/index/status", methods=["GET"])
@token_required
def index_status(user_id, project_id):
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        original = _active_original(cur, project_id)
        indexed = bool(original and _index_row(cur, original["sha256"]))
        cur.execute("""SELECT id, state, progress, error FROM video_jobs
                       WHERE project_id = %s AND type = 'index'
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        job = cur.fetchone()
    return jsonify({
        "indexed": indexed,
        "job": ({"id": job["id"], "state": job["state"],
                 "progress": job["progress"], "error": job["error"]}
                if job else None),
    })


@video_bp.route("/projects/<int:project_id>/index", methods=["GET"])
@token_required
def get_index(user_id, project_id):
    """Trimmed index for the transcript panel: no sheet/thumb keys, no captions."""
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        original = _active_original(cur, project_id)
        if not original or not original["sha256"]:
            return jsonify({"error": "No indexed video"}), 404
        cur.execute("SELECT json FROM indexes WHERE video_sha256 = %s",
                    (original["sha256"],))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "No indexed video"}), 404
    idx = row["json"]
    return jsonify({"index": {
        "video": idx.get("video"),
        "sentences": idx.get("sentences", []),
        "words": idx.get("words", []),
        "silences": idx.get("silences", []),
        "shots": [{"id": s.get("id"), "start": s.get("start"), "end": s.get("end")}
                  for s in idx.get("shots", [])],
    }})


# ------------------------------------------------------------------ #
#  Consolidated live state — ONE endpoint the studio polls             #
# ------------------------------------------------------------------ #

@video_bp.route("/projects/<int:project_id>/state", methods=["GET"])
@token_required
def project_state(user_id, project_id):
    """Everything the studio needs per polling tick in one response:
    new messages (after_id), job progress, the latest EDL, the version
    list with render pointers, the newest preview, and music assets.
    A page refresh must never be required — this endpoint is the reason."""
    after_id = request.args.get("after_id", type=int) or 0
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404

        original = _active_original(cur, project_id)
        idx_row = _index_row(cur, original["sha256"]) if original else None
        indexed = bool(idx_row)
        edl = _latest_edl(cur, project_id)

        cur.execute("""
            SELECT DISTINCT ON (type) id, type, state, progress, error,
                   updated_at
            FROM video_jobs WHERE project_id = %s
            ORDER BY type, id DESC
        """, (project_id,))
        jobs = {r["type"]: {
            "id": r["id"], "state": r["state"], "progress": r["progress"],
            "error": r["error"], "updated_at": r["updated_at"].isoformat(),
        } for r in cur.fetchall()}

        # Self-heal: an index built by an older pipeline version is stale —
        # re-index in the background (the old index keeps serving meanwhile,
        # so the workspace stays usable).
        if idx_row and idx_row.get("pipeline_version", 1) != PIPELINE_VERSION:
            ij = jobs.get("index")
            if not ij or ij["state"] not in ("queued", "running"):
                current_app.logger.info(
                    "project %s: index pipeline v%s != v%s — refreshing",
                    project_id, idx_row.get("pipeline_version"),
                    PIPELINE_VERSION)
                _enqueue(cur, project_id, user_id, "index",
                         {"asset_id": original["id"]})

        cur.execute("""SELECT id, role, content, meta, created_at
                       FROM chat_messages
                       WHERE session_id = %s AND id > %s
                       ORDER BY id ASC LIMIT 500""",
                    (p["chat_session_id"], after_id))
        msgs = cur.fetchall()

        cur.execute("""SELECT version, created_by, created_at FROM edls
                       WHERE project_id = %s ORDER BY version DESC LIMIT 100""",
                    (project_id,))
        versions = cur.fetchall()

        cur.execute("""SELECT id, kind, storage_key, duration_s, sha256, meta,
                              created_at
                       FROM assets
                       WHERE project_id = %s
                         AND kind IN ('render', 'music', 'proxy',
                                      'video_clip', 'image_ref')
                       ORDER BY id DESC LIMIT 150""", (project_id,))
        extra = cur.fetchall()

    renders = [a for a in extra if a["kind"] == "render"]
    by_version = {}
    latest_preview = None
    for a in renders:
        m = a.get("meta") or {}
        v, variant = m.get("edl_version"), m.get("variant")
        if v is not None:
            by_version.setdefault(int(v), {})[variant] = a["id"]
        if variant == "preview" and latest_preview is None:
            latest_preview = {"asset_id": a["id"],
                              "edl_version": v,
                              "created_at": a["created_at"].isoformat()}
    music = [a for a in extra if a["kind"] == "music"]
    proxies = [a for a in extra if a["kind"] == "proxy"]
    proxy = next((a for a in proxies
                  if original and a["sha256"] == original["sha256"]),
                 proxies[0] if proxies else None)

    return jsonify({
        "project": {"id": p["id"], "title": p["title"]},
        "video": _asset_out(original) if original else None,
        "proxy_asset_id": proxy["id"] if proxy else None,
        "indexed": indexed,
        "jobs": jobs,
        "messages": [
            {"id": r["id"], "role": r["role"], "content": r["content"],
             "meta": r["meta"], "created_at": r["created_at"].isoformat()}
            for r in msgs],
        "last_message_id": msgs[-1]["id"] if msgs else after_id,
        "latest_edl": ({"version": edl["version"], "json": edl["json"],
                        "created_by": edl["created_by"]} if edl else None),
        "edl_versions": [
            {"version": v["version"], "created_by": v["created_by"],
             "created_at": v["created_at"].isoformat(),
             "preview_asset_id": by_version.get(v["version"], {}).get("preview"),
             "final_asset_id": by_version.get(v["version"], {}).get("final")}
            for v in versions],
        "latest_preview": latest_preview,
        "music_assets": [
            {"id": a["id"], "storage_key": a["storage_key"],
             "filename": (a.get("meta") or {}).get("filename"),
             "duration_s": a["duration_s"]} for a in music],
        "media_assets": [
            {"id": a["id"], "kind": a["kind"],
             "storage_key": a["storage_key"],
             "filename": (a.get("meta") or {}).get("filename"),
             "duration_s": a["duration_s"]}
            for a in extra if a["kind"] in ("video_clip", "image_ref")],
    })


# ------------------------------------------------------------------ #
#  Chat -> agent turns                                                 #
# ------------------------------------------------------------------ #

@video_bp.route("/projects/<int:project_id>/messages", methods=["GET"])
@token_required
def get_messages(user_id, project_id):
    after_id = request.args.get("after_id", type=int) or 0
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404
        cur.execute("""SELECT id, role, content, meta, created_at
                       FROM chat_messages
                       WHERE session_id = %s AND id > %s
                       ORDER BY id ASC LIMIT 500""",
                    (p["chat_session_id"], after_id))
        rows = cur.fetchall()
    return jsonify({"messages": [
        {"id": r["id"], "role": r["role"], "content": r["content"],
         "meta": r["meta"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]})


@video_bp.route("/projects/<int:project_id>/messages", methods=["POST"])
@token_required
def post_message(user_id, project_id):
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    client_msg_id = (str(data.get("client_msg_id") or "")[:64]) or None
    attachment_ids = data.get("attachments") or []
    if not isinstance(attachment_ids, list):
        attachment_ids = []
    attachment_ids = [int(a) for a in attachment_ids[:4]
                      if isinstance(a, (int, str)) and str(a).isdigit()]
    if not text:
        return jsonify({"error": "text required"}), 400
    if len(text) > 4000:
        return jsonify({"error": "Message too long (4000 chars max)"}), 400

    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404

        # Idempotency FIRST: a retransmit of a message we already accepted
        # returns the original row — before rate limits or the busy check,
        # so a duplicate POST can never 409 or double-enqueue.
        if client_msg_id:
            cur.execute("""SELECT id FROM chat_messages
                           WHERE session_id = %s AND role = 'user'
                             AND meta->>'client_msg_id' = %s""",
                        (p["chat_session_id"], client_msg_id))
            dup = cur.fetchone()
            if dup:
                return jsonify({"queued": True, "message_id": dup["id"],
                                "duplicate": True})

        # Rate limit: cap LLM spend per project.
        cur.execute("""SELECT COUNT(*) AS n FROM chat_messages
                       WHERE session_id = %s AND role = 'user'
                         AND created_at > NOW() - INTERVAL '1 hour'""",
                    (p["chat_session_id"],))
        if cur.fetchone()["n"] >= MESSAGES_PER_HOUR:
            return jsonify({"error": "Message limit reached for this hour. "
                                     "Try again a bit later."}), 429

        # One agent turn at a time per project — EDL writes must not race.
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND state IN ('queued','running')""", (project_id,))
        if cur.fetchone():
            return jsonify({"error": "The editor is still working on your "
                                     "previous request."}), 409

        # Attachments must be this project's chat-attachable assets.
        attachments_meta = []
        if attachment_ids:
            cur.execute("""SELECT id, kind, duration_s, meta FROM assets
                           WHERE project_id = %s AND id = ANY(%s)
                             AND kind IN ('music','image_ref',
                                          'video_clip')""",
                        (project_id, attachment_ids))
            by_id = {a["id"]: a for a in cur.fetchall()}
            attachments_meta = [
                {"id": aid, "kind": by_id[aid]["kind"],
                 "filename": (by_id[aid].get("meta") or {}).get("filename"),
                 "duration_s": by_id[aid]["duration_s"]}
                for aid in attachment_ids if aid in by_id]

        meta = {}
        if client_msg_id:
            meta["client_msg_id"] = client_msg_id
        if attachments_meta:
            meta["attachments"] = [a["id"] for a in attachments_meta]
            meta["attachments_info"] = attachments_meta
        try:
            cur.execute("""INSERT INTO chat_messages (session_id, role,
                                                      content, meta)
                           VALUES (%s, 'user', %s, %s) RETURNING id""",
                        (p["chat_session_id"], text,
                         Json(meta) if meta else None))
            message_id = cur.fetchone()["id"]
        except psycopg2.errors.UniqueViolation:
            # Raced with an identical retransmit — the unique index on
            # (session_id, client_msg_id) makes exactly one insert win.
            conn.rollback()
            cur = conn.cursor()
            cur.execute("""SELECT id FROM chat_messages
                           WHERE session_id = %s AND role = 'user'
                             AND meta->>'client_msg_id' = %s""",
                        (p["chat_session_id"], client_msg_id))
            row = cur.fetchone()
            return jsonify({"queued": True, "duplicate": True,
                            "message_id": row["id"] if row else None})

        original = _active_original(cur, project_id)
        indexed = bool(original and _index_row(cur, original["sha256"]))
        if not indexed:
            cur.execute("""SELECT state FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            idx_job = cur.fetchone()
            if idx_job and idx_job["state"] in ("queued", "running"):
                hint = ("I'm still analyzing your video — transcribing it and "
                        "mapping the shots. I'll be ready for editing requests "
                        "in a moment. Your message is saved; send it again "
                        "once indexing finishes.")
            else:
                hint = ("Upload a video first and I'll get to work. Drop a "
                        "file into the panel on the right — once I've "
                        "analyzed it you can ask for any edit in plain "
                        "English.")
            cur.execute("""INSERT INTO chat_messages (session_id, role, content)
                           VALUES (%s, 'assistant', %s)""",
                        (p["chat_session_id"], hint))
            return jsonify({"queued": False, "message_id": message_id})

        if _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return jsonify({"error": "Too many jobs running. "
                                     "Wait for one to finish."}), 429
        if not os.getenv("OPENAI_API_KEY"):
            cur.execute("""INSERT INTO chat_messages (session_id, role, content)
                           VALUES (%s, 'assistant',
                                   'The editing agent is not configured yet — hang tight.')""",
                        (p["chat_session_id"],))
            return jsonify({"queued": False, "message_id": message_id})

        job_id = _enqueue(cur, project_id, user_id, "agent_turn",
                          {"message_id": message_id})

    return jsonify({"queued": True, "message_id": message_id,
                    "job_id": job_id})


# ------------------------------------------------------------------ #
#  User-authored EDL writes (frame selector, timeline inserts, voiceover)
# ------------------------------------------------------------------ #

def _apply_edl_op(edl, op, args, assets_by_id):
    """Apply one UI operation to an EDL dict. Returns (new_edl, desc) or
    raises ValueError with a user-facing message. Mirrors the agent tools'
    snapping semantics (worker/agent_tools.py)."""
    edl = json.loads(json.dumps(edl))   # deep copy
    if op == "set_frame":
        ratio = str(args.get("ratio") or "source")
        mode = str(args.get("mode") or "crop")
        if ratio == "source":
            edl["frame"] = None
            return edl, "output frame back to source"
        edl["frame"] = {"ratio": ratio, "mode": mode}
        return edl, f"output frame {ratio} ({mode})"

    if op == "insert_media":
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        if not asset or asset["kind"] not in ("video_clip", "image_ref"):
            raise ValueError("Pick an uploaded clip or image to insert.")
        kind = "image" if asset["kind"] == "image_ref" else "video"
        if kind == "image":
            dur = round(min(max(float(args.get("duration_s") or 3.0), 0.2),
                            60.0), 2)
        else:
            base = args.get("duration_s") or asset.get("duration_s")
            if not base:
                raise ValueError("That clip's duration isn't known yet — "
                                 "give it a second and try again.")
            dur = round(min(float(base),
                            float(asset.get("duration_s") or base)), 2)
        at = float(args.get("at_output_s") or 0.0)
        inserts = list(edl.get("inserts") or [])
        bounds = wschemas.keep_boundaries(edl["keep"])
        ins_sorted = sorted((float(i["at_output_s"]), float(i["duration_s"]))
                            for i in inserts)
        final_of = {b: b + sum(d for a, d in ins_sorted if a <= b + 1e-6)
                    for b in bounds}
        target = min(bounds, key=lambda b: abs(final_of[b] - at))
        taken = {i.get("id") for i in inserts}
        n = 1
        while f"ins{n}" in taken:
            n += 1
        inserts.append({"id": f"ins{n}", "asset_key": asset["storage_key"],
                        "kind": kind, "at_output_s": target,
                        "duration_s": dur})
        edl["inserts"] = inserts
        return edl, (f"inserted {kind} at "
                     f"{round(final_of[target], 2)}s (ins{n})")

    if op == "set_insert_duration":
        # Idempotent: the chip may reference an insert a previous click (or
        # the agent) already removed — treat as a no-op, not an error.
        for i in (edl.get("inserts") or []):
            if i.get("id") == args.get("id"):
                i["duration_s"] = round(
                    min(max(float(args.get("duration_s") or 3.0), 0.2),
                        600.0), 2)
                return edl, f"insert {i['id']} duration {i['duration_s']}s"
        return edl, "insert already gone"

    if op == "move_insert":
        inserts = list(edl.get("inserts") or [])
        target_ins = next((i for i in inserts
                           if i.get("id") == args.get("id")), None)
        if not target_ins:
            return edl, "insert already gone"
        at = float(args.get("at_output_s") or 0.0)
        bounds = wschemas.keep_boundaries(edl["keep"])
        others = sorted((float(i["at_output_s"]), float(i["duration_s"]))
                        for i in inserts if i is not target_ins)
        final_of = {b: b + sum(d for a, d in others if a <= b + 1e-6)
                    for b in bounds}
        target = min(bounds, key=lambda b: abs(final_of[b] - at))
        target_ins["at_output_s"] = target
        edl["inserts"] = inserts
        return edl, (f"moved insert {target_ins['id']} to "
                     f"{round(final_of[target], 2)}s")

    if op == "remove_insert":
        before = edl.get("inserts") or []
        edl["inserts"] = [i for i in before if i.get("id") != args.get("id")]
        if len(edl["inserts"]) == len(before):
            return edl, "insert already gone"
        return edl, f"removed insert {args.get('id')}"

    if op == "add_voiceover":
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        if not asset or asset["kind"] not in ("music", "audio"):
            raise ValueError("Pick an uploaded audio file for the voiceover.")
        vos = list(edl.get("voiceover") or [])
        taken = {v.get("id") for v in vos}
        n = 1
        while f"vo{n}" in taken:
            n += 1
        vos.append({"id": f"vo{n}", "asset_key": asset["storage_key"],
                    "start_output_s": round(
                        max(0.0, float(args.get("start_output_s") or 0.0)), 2),
                    "gain_db": float(args.get("gain_db") or 0.0),
                    "duck_others": bool(args.get("duck_others", True))})
        edl["voiceover"] = vos
        return edl, f"voiceover added (vo{n})"

    if op == "add_music":
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        if not asset or asset["kind"] not in ("music", "audio"):
            raise ValueError("Pick an uploaded audio file for the music.")
        prog = wschemas.program_duration(edl)
        start = round(min(max(float(args.get("start") or 0.0), 0.0),
                          max(0.0, prog - 0.2)), 2)
        end_default = start + float(asset.get("duration_s") or prog)
        end = round(min(max(float(args.get("end") or end_default),
                            start + 0.1), prog), 2)
        items = list(edl.get("music") or [])
        taken = {m.get("id") for m in items}
        n = 1
        while f"mus{n}" in taken:
            n += 1
        items.append({"id": f"mus{n}", "storage_key": asset["storage_key"],
                      "start": start, "end": end,
                      "gain_db": -18.0, "duck": True})
        edl["music"] = items
        return edl, f"music added {start}-{end}s (mus{n})"

    if op == "move_music":
        prog = wschemas.program_duration(edl)
        for m in (edl.get("music") or []):
            if m.get("id") == args.get("id"):
                length = float(m["end"]) - float(m["start"])
                start = round(min(max(float(args.get("start") or 0.0), 0.0),
                                  max(0.0, prog - length)), 2)
                m["start"] = start
                m["end"] = round(min(start + length, prog), 2)
                return edl, f"moved music {m['id']} to {start}s"
        return edl, "music already gone"

    if op == "remove_music":
        before = edl.get("music") or []
        edl["music"] = [m for m in before if m.get("id") != args.get("id")]
        if len(edl["music"]) == len(before):
            return edl, "music already gone"
        return edl, f"removed music {args.get('id')}"

    if op == "move_voiceover":
        prog = wschemas.program_duration(edl)
        for v in (edl.get("voiceover") or []):
            if v.get("id") == args.get("id"):
                start = max(0.0, float(args.get("start_output_s") or 0.0))
                v["start_output_s"] = round(
                    min(start, max(0.0, prog - 0.1)), 2)
                return edl, (f"moved voiceover {v['id']} to "
                             f"{v['start_output_s']}s")
        return edl, "voiceover already gone"

    if op == "remove_voiceover":
        before = edl.get("voiceover") or []
        edl["voiceover"] = [v for v in before if v.get("id") != args.get("id")]
        if len(edl["voiceover"]) == len(before):
            return edl, "voiceover already gone"
        return edl, f"removed voiceover {args.get('id')}"

    raise ValueError(f"Unknown operation '{op}'.")


@video_bp.route("/projects/<int:project_id>/edl", methods=["POST"])
@token_required
def user_edl_write(user_id, project_id):
    """User-authored EDL version from a UI action (frame selector, timeline
    insert/voiceover chips). Validates with the same schema the worker uses,
    appends a created_by='user' version and auto-renders a preview."""
    data = request.get_json() or {}
    op = str(data.get("op") or "")
    args = data.get("args") or {}
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404
        original = _active_original(cur, project_id)
        if not original or not original["duration_s"]:
            return jsonify({"error": "Upload a video first"}), 400
        # EDL writes must not race the agent
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND state IN ('queued','running')""", (project_id,))
        if cur.fetchone():
            return jsonify({"error": "The editor is working on a request — "
                                     "try again when it finishes."}), 409
        edl_row = _latest_edl(cur, project_id)
        if not edl_row:
            cur.execute("""INSERT INTO edls (project_id, version, json,
                                             created_by)
                           VALUES (%s, 1, %s, 'user')""",
                        (project_id,
                         Json(wschemas.default_edl(original["duration_s"]))))
            edl_row = _latest_edl(cur, project_id)

        cur.execute("""SELECT id, kind, storage_key, duration_s, meta
                       FROM assets WHERE project_id = %s""", (project_id,))
        assets_by_id = {a["id"]: a for a in cur.fetchall()}

        try:
            new_edl, desc = _apply_edl_op(edl_row["json"], op, args,
                                          assets_by_id)
            normalized = wschemas.validate_edl(
                new_edl, float(original["duration_s"])).model_dump()
        except (ValueError, wschemas.EDLValidationError) as e:
            return jsonify({"error": str(e)[:300]}), 400

        if wschemas.edl_signature(normalized) == \
                wschemas.edl_signature(edl_row["json"]):
            return jsonify({"version": edl_row["version"],
                            "no_change": True,
                            "edl": edl_row["json"]})

        cur.execute("""INSERT INTO edls (project_id, version, json, created_by)
                       VALUES (%s, (SELECT COALESCE(MAX(version), 0) + 1
                                    FROM edls WHERE project_id = %s),
                               %s, 'user') RETURNING version""",
                    (project_id, project_id, Json(normalized)))
        version = cur.fetchone()["version"]

        preview_job = None
        if _running_jobs_count(cur, user_id) < MAX_CONCURRENT_JOBS_PER_USER:
            preview_job = _enqueue(cur, project_id, user_id, "preview",
                                   {"edl_version": version})
        cur.execute("""INSERT INTO chat_messages (session_id, role, content,
                                                  meta)
                       VALUES (%s, 'activity', %s, %s)""",
                    (p["chat_session_id"],
                     f"you → EDL v{version}: {desc}",
                     Json({"tool": "user_edit", "op": op})))

    return jsonify({"version": version, "preview_job_id": preview_job,
                    "desc": desc, "edl": normalized})


# ------------------------------------------------------------------ #
#  EDL versions + renders                                              #
# ------------------------------------------------------------------ #

@video_bp.route("/projects/<int:project_id>/edls", methods=["GET"])
@token_required
def list_edls(user_id, project_id):
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        cur.execute("""SELECT version, created_by, created_at FROM edls
                       WHERE project_id = %s ORDER BY version DESC LIMIT 100""",
                    (project_id,))
        versions = cur.fetchall()
        cur.execute("""SELECT id, storage_key, meta FROM assets
                       WHERE project_id = %s AND kind = 'render'""",
                    (project_id,))
        renders = cur.fetchall()

    by_version = {}
    for r in renders:
        m = r.get("meta") or {}
        v, variant = m.get("edl_version"), m.get("variant")
        if v is not None:
            by_version.setdefault(int(v), {})[variant] = r["id"]

    return jsonify({"edls": [
        {"version": v["version"], "created_by": v["created_by"],
         "created_at": v["created_at"].isoformat(),
         "preview_asset_id": by_version.get(v["version"], {}).get("preview"),
         "final_asset_id": by_version.get(v["version"], {}).get("final")}
        for v in versions
    ]})


@video_bp.route("/projects/<int:project_id>/edls/<int:version>", methods=["GET"])
@token_required
def get_edl_version(user_id, project_id, version):
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        cur.execute("""SELECT version, json, created_by, created_at FROM edls
                       WHERE project_id = %s AND version = %s""",
                    (project_id, version))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Version not found"}), 404
    return jsonify({"edl": {"version": row["version"], "json": row["json"],
                            "created_by": row["created_by"]}})


@video_bp.route("/projects/<int:project_id>/render/final", methods=["POST"])
@token_required
def render_final(user_id, project_id):
    """Explicitly user-confirmed: this endpoint IS the confirmation gate.
    The agent can only render previews."""
    data = request.get_json() or {}
    version = data.get("edl_version")
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        cur.execute("SELECT version FROM edls WHERE project_id = %s AND version = %s",
                    (project_id, version if version is not None else -1))
        if not cur.fetchone():
            return jsonify({"error": "That EDL version does not exist"}), 400
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'final'
                         AND state IN ('queued','running')""", (project_id,))
        if cur.fetchone():
            return jsonify({"error": "A final render is already in progress"}), 409
        if _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return jsonify({"error": "Too many jobs running. "
                                     "Wait for one to finish."}), 429
        job_id = _enqueue(cur, project_id, user_id, "final",
                          {"edl_version": int(version)})
    return jsonify({"job_id": job_id})


# ------------------------------------------------------------------ #
#  Assets                                                              #
# ------------------------------------------------------------------ #

@video_bp.route("/assets/<int:asset_id>/url", methods=["GET"])
@token_required
def asset_url(user_id, asset_id):
    if not storage.is_configured():
        return jsonify({"error": "Video storage is not configured yet"}), 503
    download = request.args.get("download") == "1"
    with vdb() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT a.* FROM assets a
                       JOIN projects p ON p.id = a.project_id
                       WHERE a.id = %s AND p.user_id = %s""",
                    (asset_id, int(user_id)))
        a = cur.fetchone()
        if not a:
            return jsonify({"error": "Asset not found"}), 404
    name = None
    if download:
        meta = a.get("meta") or {}
        name = meta.get("filename") or f"valmera_{a['kind']}_{a['id']}.mp4"
    url = storage.presign_get(a["storage_key"], download_name=name)
    return jsonify({"url": url, "expires_in": storage.PRESIGN_EXPIRY})
