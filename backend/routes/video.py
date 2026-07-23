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
import re
import threading
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from flask import Blueprint, request, jsonify, current_app

from routes.auth import token_required
from credits import check_and_reserve
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
# Forced (cache-skipping) preview re-renders per EDL version per hour. The
# studio's own 2-per-visit bound lives in a ref that a page reload clears, so
# this is the one that actually holds. See render_preview_endpoint.
MAX_FORCED_RENDERS_PER_HOUR = int(os.getenv("MAX_FORCED_RENDERS_PER_HOUR", "4"))
# Beacons accepted per user per hour (see client_event).
MAX_CLIENT_EVENTS_PER_HOUR = int(os.getenv("MAX_CLIENT_EVENTS_PER_HOUR", "60"))
MESSAGES_PER_HOUR = int(os.getenv("MESSAGES_PER_HOUR", "20"))

# Single source of truth: worker/schemas.py (loaded above as wschemas), so
# the backend and worker can NEVER disagree. This used to be an env var set
# separately on each service; the two drifted for a day (Jul 16-17 2026) and
# every project open triggered a full 30-90 min re-index that still wrote the
# old version — an infinite loop that starved real customers' jobs.
PIPELINE_VERSION = wschemas.PIPELINE_VERSION

VIDEO_KINDS = ("original", "proxy", "audio", "thumb", "sheet", "render",
               "music", "image_ref", "video_clip")

# Concierge chat: before an indexed video exists, replies are REAL LLM
# calls (same OpenAI-compatible env as the worker) — never canned
# templates. The template strings survive only as a fallback when the
# model call fails. Calls are recorded to llm_calls with job_id NULL:
# visible in admin, never charged (credit charging sums per agent-turn
# job).
CONCIERGE_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.x.ai/v1")
CONCIERGE_MODEL = os.getenv("CONCIERGE_MODEL",
                            os.getenv("AGENT_MODEL", "grok-4.5"))
CONCIERGE_TIMEOUT_S = float(os.getenv("CONCIERGE_TIMEOUT_S", "14"))

_concierge_client = None


def _concierge_llm():
    global _concierge_client
    if _concierge_client is None:
        from openai import OpenAI
        _concierge_client = OpenAI(base_url=CONCIERGE_BASE_URL,
                                   api_key=os.getenv("OPENAI_API_KEY", ""),
                                   timeout=CONCIERGE_TIMEOUT_S,
                                   max_retries=0)
    return _concierge_client


# A concierge reply that claims work already happened is a lie — nothing
# has been analyzed or edited yet. Such drafts fall back to the template.
_CONCIERGE_CLAIM = re.compile(
    r"(?i)\b(?:i(?:'ve| have| already| just)+ (?:cut|trimmed|edited|"
    r"rendered|captioned|analyzed|generated|made|created)|"
    r"your video is ready)")


def _image_gen_enabled():
    """Mirrors the worker's generate_image availability check (worker/llm.
    image_available) so the concierge never promises (or denies) AI images out
    of sync with what the editing agent can actually do. Image gen is available
    on the DashScope native endpoint OR any OpenAI-compatible base (xAI)."""
    if not os.getenv("IMAGE_GEN_MODEL", "grok-2-image-1212"):
        return False
    if os.getenv("IMAGE_API_URL", ""):
        return True
    return bool(os.getenv("OPENAI_BASE_URL", "https://api.x.ai/v1"))


def _url_fetch_enabled():
    """Mirrors the worker's fetch_url gate (worker/config.URL_FETCH_ENABLED).

    Same contract as _image_gen_enabled above, and the same reason: the
    concierge's capability list tells the user it is EXHAUSTIVE, so a
    capability missing from it is actively denied. Without this mirror, a user
    who pastes a link while their first video is still indexing is told link
    fetching probably is not supported — and then the agent turns round and
    does it, which is the two-surfaces-disagreeing failure the deployment
    gates exist to prevent."""
    return os.getenv("URL_FETCH_ENABLED", "1") == "1"


def _image_edit_enabled():
    """Restyling an existing frame/image needs DashScope's native endpoint;
    the OpenAI-compatible /images/generations backend (xAI) can only GENERATE
    (mirrors worker/llm.image_edit_available)."""
    if not _image_gen_enabled():
        return False
    if os.getenv("IMAGE_API_URL", ""):
        return True
    return "dashscope" in os.getenv("OPENAI_BASE_URL", "https://api.x.ai/v1")


def _sound_gen_enabled():
    """Mirrors the worker's generate_sfx availability (worker/eleven.
    sound_gen_available) — a dedicated ElevenLabs key, independent of the LLM
    stack. Empty key: the concierge must not offer AI sound generation (the
    built-in pack still works once a video/program exists)."""
    return bool(os.getenv("ELEVENLABS_API_KEY", ""))


def _video_gen_enabled():
    """Mirrors the worker's generate_video availability (worker/videogen.
    video_gen_available) — a fal.ai key + the fal provider. Empty key: the
    concierge must keep saying moving-video generation isn't available."""
    return (bool(os.getenv("FAL_KEY", ""))
            and os.getenv("VIDEO_PROVIDER", "fal") == "fal")


def _concierge_stage(idx_state):
    """Map the latest index job state to what the concierge may claim.
    A FAILED index is its own stage — telling that user 'no video is
    uploaded yet' would be a lie about their broken upload."""
    if idx_state in ("queued", "running"):
        return "indexing"
    if idx_state == "failed":
        return "index_failed"
    if idx_state is None:
        return "no_video"
    return "ready"


def _parse_act(raw):
    """Parse the concierge's {act, reply} JSON. Falls back to treating the
    whole output as a chat reply (act=False) when it isn't valid JSON — so a
    model that ignored the format instruction still produces a sane chat turn."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and "reply" in obj:
            return str(obj.get("reply") or "").strip(), bool(obj.get("act"))
    except Exception:
        pass
    return raw, False


def _concierge_reply(stage, history, attachments, index_error=None,
                     can_act=False):
    """LLM-authored reply for chat while no indexed main video exists yet.
    stage: 'indexing' | 'index_failed' | 'no_video'.

    Returns (text, meta, llm_record, act). `act` is True only when can_act (the
    'no_video' blank-canvas stage) AND the model judged the user's message to be
    a request to CREATE/ADD/BUILD/EDIT something a canvas agent turn should run
    now — the caller then enqueues that turn instead of posting `text`.
    llm_record is None only when no API key is configured."""
    want_act = can_act and stage == "no_video"
    # What can be generated with no video, from the live provider gates.
    gen_now = []
    if _image_gen_enabled():
        gen_now.append("generate images from a text description")
    if _video_gen_enabled():
        gen_now.append("generate short video clips from a description, or "
                       "animate a still image into a moving clip")
    if _sound_gen_enabled():
        gen_now.append("generate custom sound effects from a description")

    if stage == "indexing":
        fallback = ("I'm still analyzing your video — transcribing it and "
                    "mapping the shots. Your request is saved: I'll start "
                    "on it automatically the moment analysis finishes, "
                    "no need to resend it.")
        state = ("Their video IS uploaded and you are analyzing it right "
                 "now (transcribing, mapping shots); long videos can take "
                 "several minutes. It is NOT ready to edit yet.")
        saved = ("Any editing request they send now is saved, and you "
                 "start on it automatically the moment their video "
                 "finishes analyzing — they never need to resend it.")
    elif stage == "index_failed":
        fallback = ("I couldn't analyze the video you uploaded, so I "
                    "can't edit it yet. Please upload it again (or try a "
                    "different file) and I'll take it from there.")
        state = ("Their video WAS uploaded but the analysis FAILED"
                 + (f" (reason: {str(index_error)[:200]})" if index_error
                    else "")
                 + ", so you cannot edit it. Be upfront about that and "
                 "ask them to re-upload the file (or try a different "
                 "one) using the panel on the right.")
        saved = ("Their editing requests are saved, but nothing can "
                 "start until a video is successfully analyzed — "
                 "re-uploading is the fix.")
    else:  # no_video — a blank CapCut-style canvas, no main video required
        if gen_now:
            fallback = ("Tell me what to make and I'll start on it now — you "
                        "don't need to upload a video first. You can also drop "
                        "images, clips or audio into the panel on the right.")
        else:
            fallback = ("Drop a video — or images, clips or audio — into the "
                        "panel on the right and I'll build your edit from them.")
        state = ("No main video is uploaded yet — but they do NOT need one to "
                 "start. The studio is a blank canvas: they build a program "
                 "from AI-generated and/or uploaded images, clips and sounds, "
                 "in any order, and can add a main video whenever they want "
                 "(or never).")
        if gen_now:
            saved = ("RIGHT NOW, with no video, you can: " + ", ".join(gen_now)
                     + ", and arrange generated or uploaded images / clips / "
                     "sounds into an edit — images and clips become full-frame "
                     "moments on the canvas. When they ask you to create, "
                     "generate or add something, it happens NOW; you never tell "
                     "them to upload a video first.")
        else:
            saved = ("RIGHT NOW you can accept uploaded images, clips and audio "
                     "and arrange them into an edit. AI generation of images, "
                     "video or sound is NOT enabled on this deployment, so do "
                     "not offer or promise it.")
    if not os.getenv("OPENAI_API_KEY"):
        return fallback, {"kind": "canned", "stage": stage}, None, False

    facts = [
        state,
        saved,
        "You have not edited, rendered, analyzed or looked at anything "
        "yet — never claim or imply that you did.",
    ]
    if stage == "no_video":
        # Transcript-based editing is the one thing that genuinely needs a
        # video; everything else is either available now or never.
        facts.append(
            "Once a video WITH SPEECH is in the program, transcript-based "
            "editing also unlocks: cutting silences and bad takes, word-timed "
            "captions (including karaoke), and censoring on-screen text. "
            "Regardless, you canNOT change playback speed, do true crossfades "
            "(overlapping footage), overlay logos/watermarks, or add custom "
            "caption fonts, outlines or stickers"
            + ("" if _video_gen_enabled() else
               ", and you canNOT generate moving VIDEO footage")
            + ". If they ask about something not covered here, say you're not "
            "sure it's supported rather than promising it.")
    else:
        facts.append(
            "Once a video is ready you can: cut silences and bad takes, add "
            "word-timed captions (including karaoke word-pop styles), add "
            "background music or voiceover, drop one-shot sound effects "
            "(whooshes, impacts, risers, clicks, dings) on exact moments "
            "from a built-in pack, zooms (including smooth Ken "
            "Burns style), dip-to-black/white transitions, fades, color "
            "grades, vertical/square/portrait reframing, blur/pixelate/"
            "black-out a fixed region to censor burned-in usernames, "
            "watermarks or on-screen text, and splice uploaded "
            "clips or images into the video full-frame"
            + (", and download a video, song or image from a LINK they paste "
               "(direct file links and YouTube/TikTok/Vimeo/SoundCloud pages) "
               "and put it straight into the edit"
               if _url_fetch_enabled() else "")
            + ((", and generate images with AI from a text description"
                + (", or by restyling a frame of their video or an uploaded "
                   "image (e.g. giving a character a new hairstyle)"
                   if _image_edit_enabled() else "")
                + " — which get spliced in as full-frame still moments. You "
                  "canNOT: "
                + ("" if _image_edit_enabled() else
                   "restyle or edit an existing frame or photo (only generate a "
                   "fresh image from a description), ")
                + ("generate or alter MOVING footage (AI images land as "
                   "still-frame moments, not tracked effects), change"
                   if not _video_gen_enabled() else "change"))
               if _image_gen_enabled() else
               ". You canNOT: generate footage or images from nothing, "
               "change")
            + " playback speed, do true crossfades (overlapping footage), "
            "overlay logos or watermarks on top of the video, or add custom "
            "caption fonts, outlines or stickers. These two "
            "lists are exhaustive — if they ask about anything not on them, "
            "say you're not sure it's supported yet rather than promising it.")
    if attachments:
        facts.append("Attached to this message and saved for the edit: " +
                     "; ".join(f"{a['kind']} "
                               f"'{a.get('filename') or 'file'}'"
                               for a in attachments) + ".")
    system = (
        "You are Valmera, an AI video editor the user chats with inside "
        "the studio.\nFACTS — every reply must respect all of them:\n- " +
        "\n- ".join(facts))
    if want_act:
        system += (
            "\n\nDecide whether the user's latest message is a REQUEST to "
            "create / generate / add / place / build / edit something you can "
            "actually start now (per the FACTS), versus small talk or a "
            "question. Reply with ONLY a JSON object and nothing else: "
            "{\"act\": <true|false>, \"reply\": <string>}. Set act=true when "
            "they want you to DO something now (e.g. 'generate an image of X', "
            "'make a video of Y', 'add a whoosh', 'put these together') — then "
            "`reply` is a short one-line acknowledgement of what you're "
            "starting (plain text, no markdown). Set act=false for greetings, "
            "thanks or questions — then `reply` answers them in 1-2 sentences. "
            "NEVER set act=true for something the FACTS say is unavailable.")
    else:
        system += (
            "\nReply to the user's last message naturally in 1-3 short "
            "sentences, plain text only (no markdown, no emoji, no lists). "
            "Answer what they actually said: greet a greeting, answer "
            "questions about what you can do, and if they asked for an edit "
            "confirm it's saved and say what happens next. Never promise a "
            "specific completion time.")
    msgs = [{"role": "system", "content": system}]
    for h in history[-10:]:
        msgs.append({"role": h["role"],
                     "content": (h["content"] or "")[:800]})
    req = {"model": CONCIERGE_MODEL, "messages": msgs}
    try:
        create_kwargs = dict(model=CONCIERGE_MODEL, messages=msgs,
                             max_tokens=300, temperature=0.5)
        if want_act:
            # Force the {act, reply} object so a plain-prose answer to a real
            # create request can't be silently misread as chat (act=False) and
            # dropped with no agent turn.
            create_kwargs["response_format"] = {"type": "json_object"}
        resp = _concierge_llm().chat.completions.create(**create_kwargs)
        raw = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        rec = {"model": CONCIERGE_MODEL, "request": req,
               "response": {"reply": raw},
               "prompt_tokens": getattr(usage, "prompt_tokens", None),
               "completion_tokens": getattr(usage, "completion_tokens",
                                            None)}
        text, act = (raw, False)
        if want_act:
            text, act = _parse_act(raw)
        # The ACT ack IS posted to the user, so it must clear the same honesty
        # bar as a chat reply: a drifted past-tense "I've generated…" is a lie
        # (the turn is only being queued now). The agent then does the real work
        # and reports it truthfully.
        if act:
            if _CONCIERGE_CLAIM.search(text or ""):
                text = "On it — starting that now."
            return text, {"kind": "concierge", "stage": stage, "act": True}, \
                rec, True
        if text and not _CONCIERGE_CLAIM.search(text):
            return text, {"kind": "concierge", "stage": stage}, rec, False
        rec["response"] = {"rejected": raw or "(empty completion)"}
        return fallback, {"kind": "canned", "stage": stage}, rec, False
    except Exception as e:
        print(f"[concierge] LLM call failed: {e}", flush=True)
        return fallback, {"kind": "canned", "stage": stage}, {
            "model": CONCIERGE_MODEL, "request": req,
            "response": {"error": str(e)[:300]},
            "prompt_tokens": None, "completion_tokens": None}, False


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


def _delete_project_rows(cur, project_id, session_id):
    """Delete every DB row for a project (child rows first). The sha-keyed
    `indexes` row has a NOT NULL project_id with ON DELETE CASCADE, so it is
    OWNED by whichever project first built it — a plain DELETE-if-unshared is
    defeated because deleting the owner cascades the row away regardless. So
    when another project still references the same source file, we RE-POINT the
    index to a surviving project before the cascade; unshared indexes cascade
    away naturally (nobody needs them)."""
    cur.execute("""SELECT DISTINCT sha256 FROM assets
                   WHERE project_id = %s AND kind = 'original'
                     AND sha256 IS NOT NULL""", (project_id,))
    shas = [r["sha256"] for r in cur.fetchall()]
    cur.execute("SELECT id FROM video_jobs WHERE project_id = %s", (project_id,))
    job_keys = [f"video:{r['id']}"[:16] for r in cur.fetchall()]

    # Re-point shared indexes to a surviving sharer BEFORE deleting this
    # project's assets/rows, so the ON DELETE CASCADE can't take a row another
    # project still needs.
    for sha in shas:
        cur.execute("""SELECT project_id FROM assets
                       WHERE sha256 = %s AND kind = 'original'
                         AND project_id <> %s LIMIT 1""", (sha, project_id))
        keeper = cur.fetchone()
        if keeper:
            cur.execute("""UPDATE indexes SET project_id = %s
                           WHERE video_sha256 = %s AND project_id = %s""",
                        (keeper["project_id"], sha, project_id))

    cur.execute("DELETE FROM llm_calls WHERE project_id = %s", (project_id,))
    if job_keys:
        cur.execute("DELETE FROM job_credits WHERE job_id = ANY(%s)",
                    (job_keys,))
    cur.execute("DELETE FROM video_jobs WHERE project_id = %s", (project_id,))
    cur.execute("DELETE FROM edls WHERE project_id = %s", (project_id,))
    cur.execute("DELETE FROM assets WHERE project_id = %s", (project_id,))
    if session_id:
        cur.execute("DELETE FROM chat_messages WHERE session_id = %s",
                    (session_id,))
    cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
    if session_id:
        cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))


@video_bp.route("/projects/<int:project_id>", methods=["DELETE"])
@token_required
def delete_project(user_id, project_id):
    """Delete a project and ALL of its data — every DB row AND every stored
    object. The DB deletion is committed FIRST, then storage is wiped: an
    irreversible R2 delete must never run before a transaction that might roll
    back (that would leave a live project whose media is destroyed). Any object
    that outlives a failed storage pass is orphaned and moppable — exactly what
    'DB rows are the source of truth' means."""
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404
        # A running index/render would re-create R2 objects after the wipe,
        # leaving orphaned copies of the user's (now 'deleted') media.
        cur.execute("""SELECT 1 FROM video_jobs WHERE project_id = %s
                       AND state IN ('queued','running') LIMIT 1""",
                    (project_id,))
        if cur.fetchone():
            return jsonify({"error": "This project has an operation in "
                                     "progress — try deleting it in a moment.",
                            "code": "busy"}), 409
        _delete_project_rows(cur, project_id, p["chat_session_id"])
    # DB deletion has committed. Now wipe storage (best-effort; orphans moppable).
    objects_deleted = 0
    try:
        if storage.is_configured():
            objects_deleted = storage.delete_project_objects(project_id)
    except Exception as e:
        print(f"[delete_project] object delete failed for {project_id}: {e}")
    return jsonify({"ok": True, "objects_deleted": objects_deleted})


@video_bp.route("/projects/<int:project_id>/messages/<int:message_id>/feedback",
                methods=["POST"])
@token_required
def message_feedback(user_id, project_id, message_id):
    """Thumbs up/down on an assistant reply — the ground-truth training signal
    (Q2). Stored on the message meta; polling/admin can read it back. Passing
    rating=null clears it."""
    rating = (request.get_json() or {}).get("rating")
    if rating not in ("up", "down", None):
        return jsonify({"error": "rating must be 'up', 'down' or null"}), 400
    with vdb() as conn:
        cur = conn.cursor()
        p = _project_for_user(cur, project_id, user_id)
        if not p:
            return jsonify({"error": "Project not found"}), 404
        cur.execute("""SELECT id, role FROM chat_messages
                       WHERE id = %s AND session_id = %s""",
                    (message_id, p["chat_session_id"]))
        m = cur.fetchone()
        if not m or m["role"] != "assistant":
            return jsonify({"error": "Message not found"}), 404
        cur.execute("""UPDATE chat_messages
                       SET meta = COALESCE(meta, '{}'::jsonb) || %s::jsonb
                       WHERE id = %s""",
                    (Json({"feedback": rating}), message_id))
    return jsonify({"ok": True, "rating": rating})


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

        # Idempotency FIRST: this POST is the single point where a finished
        # multi-GB upload becomes a real asset, so the client retries it on
        # network blips. A retry of a complete that already succeeded (its
        # response was lost) must return the original result — not 400 on
        # the consumed multipart id, and never a duplicate asset + second
        # index job.
        cur.execute("""SELECT id, kind FROM assets
                       WHERE project_id = %s AND storage_key = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, key))
        dup = cur.fetchone()
        if dup:
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                             AND (payload->>'asset_id')::int = %s
                           ORDER BY id DESC LIMIT 1""",
                        (project_id, dup["id"]))
            ij = cur.fetchone()
            return jsonify({"asset_id": dup["id"],
                            "index_job_id": ij["id"] if ij else None,
                            "kind": dup["kind"], "duplicate": True})

        if kind == "original" and \
                _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return jsonify({"error": "Too many jobs running. "
                                     "Wait for one to finish."}), 429

    if upload_id:
        try:
            storage.complete_multipart(key, upload_id, parts)
        except Exception as e:
            # A retried complete can hit an already-consumed upload_id. If
            # the assembled object EXISTS, the first complete succeeded and
            # this is that retry — proceed. Only abort when it truly failed.
            if storage.head_bytes(key) is None:
                storage.abort_multipart(key, upload_id)
                return jsonify({"error": f"Upload could not be "
                                         f"finalized: {e}"}), 400

    nbytes = storage.head_bytes(key)
    if nbytes is None:
        return jsonify({"error": "Uploaded file not found in storage"}), 400
    if nbytes > storage.max_upload_bytes():
        return jsonify({"error": "File exceeds the upload size limit"}), 400

    # Magic-byte sniff: the extension was validated at presign, but a renamed
    # file (e.g. a .txt renamed to .mp4) would otherwise sail through and fail
    # deep inside indexing with a confusing error. Reject the clear mismatches
    # early with a clean message; ambiguous bytes are allowed.
    head = storage.get_range(key, 64)
    if storage.content_matches_kind(head, kind) is False:
        return jsonify({
            "error": "That file's contents don't match its type — it may be "
                     "renamed or corrupted. Please upload a real "
                     f"{'video' if kind in ('original', 'clip') else kind} "
                     "file."}), 400

    asset_kind = {"original": "original", "music": "music",
                  "image": "image_ref", "clip": "video_clip"}[kind]
    try:
        duration_s = min(max(float(duration_s), 0.1), 4 * 3600) \
            if duration_s else None
    except (TypeError, ValueError):
        duration_s = None

    with vdb() as conn:
        cur = conn.cursor()
        # The early dedupe ran in its OWN transaction, so two overlapping
        # completes (a proxy-timeout retry racing the still-running original
        # request) could both pass it. assets has no unique constraint on
        # storage_key to lean on, so serialize per project: lock the project
        # row and re-check under the lock before inserting — otherwise the
        # race lands a duplicate asset AND a duplicate 16-45 min index job.
        cur.execute("SELECT id FROM projects WHERE id = %s FOR UPDATE",
                    (project_id,))
        cur.execute("""SELECT id, kind FROM assets
                       WHERE project_id = %s AND storage_key = %s
                       ORDER BY id DESC LIMIT 1""", (project_id, key))
        dup = cur.fetchone()
        if dup:
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                             AND (payload->>'asset_id')::int = %s
                           ORDER BY id DESC LIMIT 1""",
                        (project_id, dup["id"]))
            ij = cur.fetchone()
            return jsonify({"asset_id": dup["id"],
                            "index_job_id": ij["id"] if ij else None,
                            "kind": dup["kind"], "duplicate": True})
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


TRANSCRIPT_MAX_CHARS = 400
_WORD_RE = re.compile(r"\S+")


def _retokenize_span(new_text, t0, t1):
    """Split corrected sentence text into word tokens and lay them across the
    sentence's [t0,t1] window, proportional to token length. Captions are
    word-timed, so the corrected words must carry timings or karaoke captions
    would desync."""
    toks = _WORD_RE.findall(new_text)
    if not toks:
        return []
    t0 = float(t0)
    t1 = max(float(t1), t0 + 0.05)
    span = t1 - t0
    weights = [max(len(t), 1) for t in toks]
    total = float(sum(weights))
    out, cursor = [], t0
    for i, (tok, w) in enumerate(zip(toks, weights)):
        wt0 = cursor
        wt1 = t1 if i == len(toks) - 1 else cursor + span * (w / total)
        out.append({"w": tok, "t0": round(wt0, 3), "t1": round(wt1, 3)})
        cursor = wt1
    return out


def _apply_transcript_edit(idx, sentence_id, new_text):
    """Return (new_index_dict, updated_sentence) or (None, error_msg).

    Rebuilds the whole word list from the sentence partition so every
    sentence's absolute wi0/wi1 stays consistent after the edited sentence
    changes its word count. Sentences produced by group_sentences tile the
    word list contiguously, so slicing each by its own wi0/wi1 is lossless."""
    sentences = idx.get("sentences") or []
    words = idx.get("words") or []
    target = next((s for s in sentences if s.get("id") == sentence_id), None)
    if not target:
        return None, "sentence not found"

    def _slice(s):
        wi0, wi1 = s.get("wi0"), s.get("wi1")
        if isinstance(wi0, int) and isinstance(wi1, int) \
                and 0 <= wi0 <= wi1 < len(words):
            return [{"w": w.get("w"), "t0": w.get("t0"), "t1": w.get("t1")}
                    for w in words[wi0:wi1 + 1]]
        return None

    new_words, new_sentences, updated = [], [], None
    for s in sentences:
        s2 = dict(s)
        if s.get("id") == sentence_id:
            toks = _retokenize_span(new_text, s.get("t0"), s.get("t1"))
            s2["text"] = new_text
            updated = s2
        else:
            toks = _slice(s)
            # An un-sliceable neighbour (older index without word indices)
            # means we can't safely rebuild — fall back to a text-only edit.
            if toks is None:
                text_only = [dict(x) for x in sentences]
                for x in text_only:
                    if x.get("id") == sentence_id:
                        x["text"] = new_text
                        updated = x
                out = dict(idx)
                out["sentences"] = text_only
                return out, updated
        s2["wi0"] = len(new_words)
        s2["wi1"] = len(new_words) + len(toks) - 1
        new_words.extend(toks)
        new_sentences.append(s2)

    out = dict(idx)
    out["sentences"] = new_sentences
    out["words"] = new_words
    return out, updated


@video_bp.route("/projects/<int:project_id>/transcript", methods=["PATCH"])
@token_required
def edit_transcript(user_id, project_id):
    """Correct one transcript sentence (e.g. a mis-heard brand name). Updates
    the shared index in place so future captions + agent turns use the fix.
    Body: {sentence_id, text}."""
    data = request.get_json(silent=True) or {}
    sentence_id = (data.get("sentence_id") or "").strip()
    new_text = (data.get("text") or "").strip()
    if not sentence_id or not new_text:
        return jsonify({"error": "sentence_id and text are required"}), 400
    if len(new_text) > TRANSCRIPT_MAX_CHARS:
        return jsonify({"error": f"text too long (max {TRANSCRIPT_MAX_CHARS} "
                                 "characters)"}), 400
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        # Block edits mid-index — the worker would overwrite them on completion.
        cur.execute("""SELECT 1 FROM video_jobs
                       WHERE project_id = %s AND type = 'index'
                         AND state IN ('queued','running') LIMIT 1""",
                    (project_id,))
        if cur.fetchone():
            return jsonify({"error": "The video is still being analyzed — "
                                     "try again in a moment."}), 409
        original = _active_original(cur, project_id)
        if not original or not original["sha256"]:
            return jsonify({"error": "No indexed video"}), 404
        # The index is content-addressed (one shared row per video_sha256), so
        # a write would bleed into any OTHER user who uploaded the byte-identical
        # file. Fail closed if this video's hash is shared across accounts — the
        # correction must never mutate a stranger's transcript.
        cur.execute("""SELECT 1 FROM assets a JOIN projects p ON p.id = a.project_id
                       WHERE a.sha256 = %s AND a.kind = 'original'
                         AND p.user_id <> %s LIMIT 1""",
                    (original["sha256"], int(user_id)))
        if cur.fetchone():
            return jsonify({"error": "This video is shared with another account, "
                                     "so its transcript can't be edited here."}), 409
        cur.execute("SELECT json FROM indexes WHERE video_sha256 = %s FOR UPDATE",
                    (original["sha256"],))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "No indexed video"}), 404
        new_index, updated = _apply_transcript_edit(
            row["json"], sentence_id, new_text)
        if new_index is None:
            return jsonify({"error": updated}), 404
        cur.execute("UPDATE indexes SET json = %s WHERE video_sha256 = %s",
                    (Json(new_index), original["sha256"]))
        # Do captions currently pull from the transcript? If so the studio can
        # offer a one-click re-render so the correction shows on screen. NOTE:
        # edls.json['captions'] is a 3-way union — dict (from_transcript), None
        # (off), or a LIST (manual items) — so guard with isinstance before .get.
        cur.execute("""SELECT json FROM edls WHERE project_id = %s
                       ORDER BY version DESC LIMIT 1""", (project_id,))
        edl_row = cur.fetchone()
        caps = edl_row["json"].get("captions") if edl_row else None
        captions_active = bool(isinstance(caps, dict)
                               and caps.get("mode") == "from_transcript")
    return jsonify({"ok": True, "sentence": updated,
                    "captions_active": captions_active})


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

        # Self-heal on open, two cases, both BOUNDED to 2 index jobs per
        # project per 6 hours so no condition can ever loop the worker:
        #  1. stale index — built by an older pipeline version; re-index in
        #     the background (the old index keeps serving meanwhile).
        #  2. dead project — the last index job FAILED (worker death, OOM)
        #     and nothing would ever retry it. The failure note tells users
        #     "re-open the project to try again"; this makes that true —
        #     before, a failed analysis left the project dead forever.
        ij = jobs.get("index")
        idx_active = bool(ij and ij["state"] in ("queued", "running"))
        heal_reason = None
        # is_reindex distinguishes the cases for the worker: a stale-pipeline
        # refresh must stay QUIET in chat (the project already greeted and may
        # have edits), while a heal of a never-successful index is the user's
        # FIRST analysis and should greet normally when it lands.
        is_reindex = False
        if idx_row and idx_row.get("pipeline_version", 1) != PIPELINE_VERSION:
            heal_reason = (f"pipeline v{idx_row.get('pipeline_version')} != "
                           f"v{PIPELINE_VERSION}")
            is_reindex = True
        elif original and not idx_row and ij and ij["state"] == "failed":
            heal_reason = "last index job failed"
        elif original and idx_row and ij and ij["state"] == "failed":
            # A shared-sha index row (another project indexed the same file)
            # can exist while THIS project's setup died mid-cache-hit — sha
            # set, "indexed" true, but no proxy/EDL of its own, so its player
            # never loads. Re-running the job is a fast cache-hit that
            # finishes the per-project setup.
            cur.execute("""SELECT 1 FROM assets
                           WHERE project_id = %s AND kind = 'proxy'
                             AND sha256 = %s LIMIT 1""",
                        (project_id, original["sha256"]))
            if not cur.fetchone():
                heal_reason = "index cache-hit setup incomplete (no proxy)"
        if heal_reason and not idx_active:
            # Serialize with concurrent polls (two tabs on one project): both
            # could pass the checks above and burn the whole heal budget on
            # duplicate enqueues. Lock the project row, re-check under it.
            cur.execute("SELECT id FROM projects WHERE id = %s FOR UPDATE",
                        (project_id,))
            cur.execute("""SELECT 1 FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                             AND state IN ('queued','running') LIMIT 1""",
                        (project_id,))
            still_idle = cur.fetchone() is None
            cur.execute("""SELECT COUNT(*) AS n FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                             AND created_at > NOW() - INTERVAL '6 hours'""",
                        (project_id,))
            # < 3: the upload's own index job counts too, so this allows the
            # original attempt plus two heals per 6h — bounded, but "re-open
            # the project to try again" stays true on the first re-open.
            if still_idle and cur.fetchone()["n"] < 3:
                current_app.logger.info("project %s: re-indexing (%s)",
                                        project_id, heal_reason)
                _enqueue(cur, project_id, user_id, "index",
                         {"asset_id": original["id"],
                          "reindex": is_reindex})
            elif still_idle:
                current_app.logger.warning(
                    "project %s: NOT re-indexing (%s) — hit the "
                    "3-jobs-per-6h self-heal bound", project_id, heal_reason)

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
    # `extra` is ordered id DESC, so the FIRST render seen per (version, variant)
    # is the NEWEST asset for it — keep that one. Overwriting on every row (the
    # old behaviour) left the OLDEST re-render of a version as its pointer.
    for a in renders:
        m = a.get("meta") or {}
        v, variant = m.get("edl_version"), m.get("variant")
        # int(v) on a malformed meta value used to raise straight out of the
        # request — and /state is polled every 2s, so one bad asset row bricked
        # the whole project's studio forever. Skip the row instead.
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        bv = by_version.setdefault(v, {})
        if variant == "final" and not _final_is_current(m):
            continue                       # pre-end-card export: not current
        if variant not in bv:
            bv[variant] = {"id": a["id"], "created_at": a["created_at"]}
    # The preview the player should show is the render of the NEWEST edl version
    # — NOT merely the newest render asset id. A late re-render of an OLDER
    # version (a version-picker tap, a retried/redelivered job) inserts a higher
    # asset id for a lower version; picking "newest id" then flips the player
    # back to that older cut/caption state. Pick by max version, and expose the
    # newest-asset id per version to the version list.
    latest_preview = None
    preview_versions = [v for v, d in by_version.items() if d.get("preview")]
    if preview_versions:
        vmax = max(preview_versions)
        pv = by_version[vmax]["preview"]
        latest_preview = {"asset_id": pv["id"], "edl_version": vmax,
                          "created_at": pv["created_at"].isoformat()}
    music = [a for a in extra if a["kind"] == "music"]
    proxies = [a for a in extra if a["kind"] == "proxy"]
    # Only ever hand back a proxy that belongs to the ACTIVE original. The
    # proxies[0] fallback could serve a previous upload's proxy (a different
    # video) in the window after a re-upload before its own proxy is built.
    proxy = next((a for a in proxies
                  if original and a["sha256"] == original["sha256"]), None)

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
             "preview_asset_id":
                 (by_version.get(v["version"], {}).get("preview") or {}).get("id"),
             "final_asset_id":
                 (by_version.get(v["version"], {}).get("final") or {}).get("id")}
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

        # Credits gate — only when an agent turn will actually run (the
        # worker charges model usage per turn). Pre-index chat stays free:
        # concierge replies are cheap, rate-limited, and never charged.
        original = _active_original(cur, project_id)
        indexed = bool(original and _index_row(cur, original["sha256"]))
        if indexed and not check_and_reserve(conn, user_id,
                                             min_credits=1.0):
            return jsonify({
                "error": "You're out of credits — they refresh daily, or "
                         "upgrade for a bigger monthly pool.",
                "code": "insufficient_credits"}), 402

        # Job-cap check BEFORE the insert: returning 429 after inserting the
        # message left it committed with no agent_turn ever enqueued — an
        # orphaned "unserved" message the user had to resend. Checked here so a
        # capacity 429 never persists the message; the client can auto-retry.
        if indexed and (_running_jobs_count(cur, user_id)
                        >= MAX_CONCURRENT_JOBS_PER_USER):
            return jsonify({
                "error": "You have a few edits still processing — I'll take "
                         "this one as soon as one finishes.",
                "code": "busy_capacity"}), 429

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

        concierge = None
        if not indexed:
            # Gather context inside the transaction, but make the LLM call
            # AFTER it commits — a model call must never hold a DB
            # transaction (and the user's message must survive regardless).
            cur.execute("""SELECT state, error FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            idx_job = cur.fetchone()
            cur.execute("""SELECT role, content FROM chat_messages
                           WHERE session_id = %s
                             AND role IN ('user', 'assistant')
                           ORDER BY id DESC LIMIT 12""",
                        (p["chat_session_id"],))
            _stage = _concierge_stage(idx_job["state"] if idx_job else None)
            concierge = {
                "stage": _stage,
                "index_error": idx_job["error"] if idx_job else None,
                "history": list(reversed(cur.fetchall())),
                "session_id": p["chat_session_id"],
                # A canvas agent turn (no main video) can run only in the
                # 'no_video' blank-canvas stage; while a video indexes or after
                # a failed index, the pending/failed video is the program.
                "can_act": _stage == "no_video",
                "user_id": user_id,
                "message_id": message_id,
            }

        else:
            if not os.getenv("OPENAI_API_KEY"):
                cur.execute("""INSERT INTO chat_messages (session_id, role,
                                                          content)
                               VALUES (%s, 'assistant',
                                       'The editing agent is not configured yet — hang tight.')""",
                            (p["chat_session_id"],))
                return jsonify({"queued": False, "message_id": message_id})

            job_id = _enqueue(cur, project_id, user_id, "agent_turn",
                              {"message_id": message_id})

    if concierge is not None:
        # The model call runs in a thread with its own DB connection — the
        # backend has only 3 sync gunicorn workers serving everything, so a
        # 14s completion must never occupy one. The studio's 2s poll picks
        # the reply up; "concierge": true lets it show a typing indicator.
        threading.Thread(
            target=_concierge_respond,
            args=(current_app.config["DATABASE_URL"], project_id,
                  concierge, attachments_meta),
            daemon=True).start()
        return jsonify({"queued": False, "concierge": True,
                        "message_id": message_id})

    return jsonify({"queued": True, "message_id": message_id,
                    "job_id": job_id})


def _concierge_respond(db_url, project_id, ctx, attachments):
    """Thread body: LLM call, then reply + llm_calls insert on a fresh
    connection. _concierge_reply already degrades to the template on any
    model failure, so only a DB failure can swallow the reply (logged)."""
    try:
        reply, reply_meta, llm_rec, act = _concierge_reply(
            ctx["stage"], ctx["history"], attachments,
            index_error=ctx.get("index_error"), can_act=ctx.get("can_act"))
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        try:
            cur = conn.cursor()

            def _say(content, meta):
                cur.execute("""INSERT INTO chat_messages (session_id, role,
                                                          content, meta)
                               VALUES (%s, 'assistant', %s, %s)""",
                            (ctx["session_id"], content, Json(meta)))

            # The model call took up to ~14s. If the index state moved in
            # that window (analysis finished, failed, or a video arrived),
            # the drafted reply describes a world that no longer exists —
            # drop it instead of inserting "I'm still analyzing" under the
            # ready notice (the auto-resumed agent turn answers instead).
            cur.execute("""SELECT state FROM video_jobs
                           WHERE project_id = %s AND type = 'index'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            row = cur.fetchone()
            stage_now = _concierge_stage(row["state"] if row else None)
            fresh = stage_now == ctx["stage"]

            if fresh and act:
                # The user asked to CREATE / BUILD something on the blank
                # canvas — run a real agent turn (no main video required). It
                # charges credits per turn exactly like any edit, so reserve
                # first and fail honestly if they're tapped out or already busy.
                if not check_and_reserve(conn, ctx["user_id"], min_credits=1.0):
                    _say("You're out of credits — they refresh daily, or "
                         "upgrade for a bigger monthly pool to keep creating.",
                         {"kind": "concierge", "credits_exhausted": True})
                elif (_running_jobs_count(cur, ctx["user_id"])
                      >= MAX_CONCURRENT_JOBS_PER_USER):
                    _say("I've got a couple of things still processing — I'll "
                         "start this the moment one finishes.",
                         {"kind": "concierge"})
                else:
                    # The per-project "one agent turn at a time" 409 guard in
                    # post_message can't see a turn THIS thread hasn't enqueued
                    # yet, so two blank-canvas requests ~1s apart could both
                    # reach here and enqueue two turns that race EDL writes.
                    # Serialize on the project with an advisory xact lock (held
                    # to commit) + a re-check, so the second thread waits, sees
                    # the first's turn, and stands down.
                    cur.execute("SELECT pg_advisory_xact_lock(%s)",
                                (project_id,))
                    cur.execute("""SELECT 1 FROM video_jobs
                                   WHERE project_id = %s AND type = 'agent_turn'
                                     AND state IN ('queued','running')
                                   LIMIT 1""", (project_id,))
                    if cur.fetchone():
                        _say("I'm still working on your previous request — I'll "
                             "get to this one next.", {"kind": "concierge"})
                    else:
                        if reply:
                            _say(reply, {"kind": "concierge", "act": True})
                        _enqueue(cur, project_id, ctx["user_id"], "agent_turn",
                                 {"message_id": ctx["message_id"]})
            elif fresh:
                _say(reply, reply_meta)
            elif llm_rec:
                llm_rec["response"] = dict(llm_rec.get("response") or {},
                                           stale=f"index moved to "
                                                 f"{stage_now} during "
                                                 f"reply; not shown")
            if llm_rec:
                cur.execute("""INSERT INTO llm_calls (project_id, job_id,
                                   purpose, model, request, response,
                                   prompt_tokens, completion_tokens)
                               VALUES (%s, NULL, 'concierge', %s, %s, %s,
                                       %s, %s)""",
                            (project_id, llm_rec["model"],
                             Json(llm_rec["request"]),
                             Json(llm_rec["response"]),
                             llm_rec["prompt_tokens"],
                             llm_rec["completion_tokens"]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[concierge] respond thread failed: {e}", flush=True)


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
            # A drop with no explicit duration starts as a 10s insert at
            # most — dumping a 10-minute recording whole into a short edit
            # is never intended; the duration chip can extend it.
            if not args.get("duration_s"):
                dur = min(dur, 10.0)
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
        # Removing an insert shortens the program, but sfx are bounded by the
        # OLD length — validate_edl would reject the whole op, so clicking x
        # on an insert would fail over an unrelated sound effect.
        prog = wschemas.program_duration(edl)
        edl["sfx"] = [s for s in (edl.get("sfx") or [])
                      if float(s.get("at") or 0.0) <= max(0.0, prog - 0.05)]
        return edl, f"removed insert {args.get('id')}"

    if op == "add_voiceover":
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        # kind 'audio' is the pipeline's extracted source-audio track — it
        # must never be layered back over itself.
        if not asset or asset["kind"] != "music":
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
        if not asset or asset["kind"] != "music":
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

    if op == "remove_sfx":
        before = edl.get("sfx") or []
        edl["sfx"] = [s for s in before if s.get("id") != args.get("id")]
        if len(edl["sfx"]) == len(before):
            return edl, "sound effect already gone"
        return edl, f"removed sfx {args.get('id')}"

    if op == "move_sfx":
        # A point event, so it clamps to the program end rather than
        # preserving a length the way move_music does.
        prog = wschemas.program_duration(edl)
        at = max(0.0, min(float(args.get("at") or 0.0), max(0.0, prog - 0.05)))
        for s in (edl.get("sfx") or []):
            if s.get("id") == args.get("id"):
                s["at"] = round(at, 2)
                return edl, f"moved sfx {s['id']} to {s['at']}s"
        return edl, "sound effect already gone"

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
            # source='user_edit' lets the worker post a chat note if THIS
            # preview fails (agent-enqueued previews react inline instead).
            preview_job = _enqueue(cur, project_id, user_id, "preview",
                                   {"edl_version": version,
                                    "source": "user_edit"})
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

# A final rendered before the branded end card existed is no longer a
# deliverable export: it is missing the card every new export carries. The
# worker's render cache already busts on a stale stamp, but nothing would ever
# ASK it to — the studio short-circuits straight to presigning an existing
# final_asset_id and never posts /render/final. So a stale final is simply not
# reported as one; the studio then takes its existing "no final yet" path,
# enqueues a render, and the worker re-encodes with the card.
#
# Previews are exempt: they carry no card, so their absent stamp is correct.
OUTRO_VERSION = 1


def _final_is_current(meta):
    # PRESENCE of the stamp, not its value. The worker legitimately writes
    # outro_v=0 when it rendered without a card (OUTRO_DURATION_S=0, or an
    # image built without brand/endcard.png). Comparing to OUTRO_VERSION would
    # hide that final forever while the worker's cache keeps serving it as
    # current — Download becomes a permanent no-op with no error anywhere.
    # Renders predating the card carry no key at all, so they still re-export,
    # and this converges after exactly one re-render either way.
    return "outro_v" in (meta or {})


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
        try:                      # a malformed meta value must not 500 the list
            v = int(v)
        except (TypeError, ValueError):
            continue
        bv = by_version.setdefault(v, {})
        if variant == "final" and not _final_is_current(m):
            continue                       # pre-end-card export: not current
        # Keep the NEWEST asset id per (version, variant): a version can be
        # re-rendered, and the version list must point at the latest encode.
        if r["id"] > bv.get(variant, 0):
            bv[variant] = r["id"]

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
    try:
        version = int(data.get("edl_version"))
    except (TypeError, ValueError):
        return jsonify({"error": "edl_version must be an integer"}), 400
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        cur.execute("SELECT version FROM edls WHERE project_id = %s AND version = %s",
                    (project_id, version))
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
                          {"edl_version": version})
    return jsonify({"job_id": job_id})


@video_bp.route("/projects/<int:project_id>/render/preview", methods=["POST"])
@token_required
def render_preview_endpoint(user_id, project_id):
    """Re-render the preview for an EDL version. Used by the studio to retry a
    preview that failed (or never rendered) without making another edit.

    force=true additionally bypasses the worker's render cache. That is the
    ONLY way out of "the render exists but this browser will not play it":
    without it the worker serves the stored asset straight back and the user
    retries forever against the same unplayable object."""
    data = request.get_json() or {}
    try:
        version = int(data.get("edl_version"))
    except (TypeError, ValueError):
        return jsonify({"error": "edl_version must be an integer"}), 400
    force = bool(data.get("force"))
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        cur.execute("SELECT version FROM edls WHERE project_id = %s AND version = %s",
                    (project_id, version))
        if not cur.fetchone():
            return jsonify({"error": "That EDL version does not exist"}), 400
        # Don't stack a second preview for a version already rendering — EXCEPT
        # for a forced re-render: an in-flight normal job for this version will
        # serve the very asset the user is telling us they cannot play, so
        # joining it would report success and change nothing on their screen.
        if not force:
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'preview'
                             AND state IN ('queued','running')
                             AND (payload->>'edl_version')::int = %s""",
                        (project_id, version))
            existing = cur.fetchone()
            if existing:
                return jsonify({"job_id": existing["id"], "already_running": True})
        else:
            # Bound the escape hatch: one forced re-render per version at a
            # time, so a user leaning on Retry can't queue an encode per press.
            cur.execute("""SELECT id FROM video_jobs
                           WHERE project_id = %s AND type = 'preview'
                             AND state IN ('queued','running')
                             AND (payload->>'edl_version')::int = %s
                             AND payload->>'force' = 'true'""",
                        (project_id, version))
            existing = cur.fetchone()
            if existing:
                return jsonify({"job_id": existing["id"], "already_running": True,
                                "forced": True})
            # ...and a DURABLE cap on top of it. The studio also limits itself to
            # 2 escalations per visit, but that counter lives in a ref: a reload
            # resets it, and "reload and hit retry again" is exactly what a user
            # with an unplayable render does. Forced renders skip the render
            # cache by design, so each one is a full re-encode of the ORIGINAL —
            # on a 1-vCPU box with MEDIA_SLOTS=1 an unbounded sequence of them
            # occupies the single global media slot and starves every other
            # customer's preview (the round-19 churn cause, self-inflicted).
            cur.execute("""SELECT COUNT(*) AS n FROM video_jobs
                           WHERE project_id = %s AND type = 'preview'
                             AND (payload->>'edl_version')::int = %s
                             AND payload->>'force' = 'true'
                             AND created_at > NOW() - INTERVAL '1 hour'""",
                        (project_id, version))
            if (cur.fetchone() or {}).get("n", 0) >= MAX_FORCED_RENDERS_PER_HOUR:
                # Honest: re-encoding again genuinely will not help them, and
                # saying so beats silently queueing work that changes nothing.
                return jsonify({
                    "error": "We've already rebuilt this preview a few times and "
                             "it still won't play in this browser. Download the "
                             "edit, or try opening the project in another browser.",
                    "code": "forced_render_limit"}), 429
        if _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return jsonify({"error": "You have a few edits still processing — "
                                     "try again in a moment.",
                            "code": "busy_capacity"}), 429
        payload = {"edl_version": version, "source": "user_edit"}
        if force:
            payload["force"] = True
        job_id = _enqueue(cur, project_id, user_id, "preview", payload)
    return jsonify({"job_id": job_id, "forced": force})


# Client-side failures (a <video> that will not decode, a presign that never
# resolves) are invisible to us: media bytes go browser <-> R2 directly, so the
# API sees nothing but a user who says "it's broken". This records them.
# Best-effort by contract — a beacon must NEVER surface an error to the user or
# block the UI it is reporting on.
CLIENT_EVENT_KINDS = {"player_error", "player_error_probe",
                      "player_recovered", "attach_failed"}


@video_bp.route("/projects/<int:project_id>/client-event", methods=["POST"])
@token_required
def client_event(user_id, project_id):
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or "")[:40]
    if kind not in CLIENT_EVENT_KINDS:
        return jsonify({"ok": True, "ignored": True})
    try:
        asset_id = int(data.get("asset_id"))
    except (TypeError, ValueError):
        asset_id = None
    detail = data.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    # Cap what a client can write: this is user-controlled input landing in a
    # table an admin will read. Scalars only, bounded count and length. Numbers
    # are range-checked rather than trusted — JSON has no integer bound, so a
    # bare `{"n": <4000-digit number>}` passed an isinstance(int) check and
    # landed in the row at full length, sailing past the 300-char cap that
    # exists precisely to stop that.
    clean = {}
    for k, v in list(detail.items())[:20]:
        key = str(k)[:40]
        if v is None or isinstance(v, bool):
            clean[key] = v
        elif isinstance(v, (int, float)):
            clean[key] = v if -1e15 < v < 1e15 else str(v)[:300]
        else:
            clean[key] = str(v)[:300]
    try:
        with vdb() as conn:
            cur = conn.cursor()
            if not _project_for_user(cur, project_id, user_id):
                return jsonify({"ok": True, "ignored": True})
            # Telemetry must never become a write amplifier: this endpoint is
            # cheap to call in a loop from a page the user already controls.
            cur.execute("""SELECT COUNT(*) AS n FROM client_events
                           WHERE user_id = %s
                             AND created_at > NOW() - INTERVAL '1 hour'""",
                        (int(user_id),))
            if (cur.fetchone() or {}).get("n", 0) >= MAX_CLIENT_EVENTS_PER_HOUR:
                return jsonify({"ok": True, "throttled": True})
            # An asset_id from the client is a claim, not a fact. Storing an
            # unverified one lets a forensics row point at another tenant's
            # asset — and this table exists to be TRUSTED during an incident.
            if asset_id is not None:
                cur.execute("""SELECT 1 FROM assets
                               WHERE id = %s AND project_id = %s""",
                            (asset_id, project_id))
                if not cur.fetchone():
                    # Keep the claim rather than lose it: a render that has
                    # since been pruned as superseded is EXACTLY the kind of
                    # asset a failure beacon is about, and "unverified" is more
                    # useful to an incident than a silent NULL.
                    clean["asset_id_unverified"] = asset_id
                    asset_id = None
            cur.execute("""INSERT INTO client_events
                               (user_id, project_id, kind, asset_id, detail)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (int(user_id), project_id, kind, asset_id,
                         json.dumps(clean)))
    except Exception as exc:      # never let telemetry break the studio
        print(f"[client_event] dropped ({kind}): {exc}", flush=True)
        return jsonify({"ok": True, "stored": False})
    return jsonify({"ok": True, "stored": True})


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
    return jsonify({"url": url, "expires_in": storage.PRESIGN_GET_EXPIRY})
