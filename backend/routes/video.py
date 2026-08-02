"""
Video editor API — projects, direct-to-storage uploads, chat -> agent turns,
EDL versions, renders.

The API never touches media bytes and never runs ffmpeg/whisper/LLM loops:
it stores pointers + JSON and enqueues rows in video_jobs for the worker
(see worker/ at the repo root). Chat history reuses the existing
chat_sessions / chat_messages tables (one session per project, plus an
'activity' role for agent tool calls).
"""

import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from flask import Blueprint, request, jsonify, current_app

from routes.auth import token_required
from credits import check_and_reserve, get_balance
import plan_gate
import mp4probe
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

# worker/timeline.py rides along the same way (registered first so its
# `from worker_schemas import ...` fallback resolves): Timeline math and the
# shared program-item re-anchoring (remap_program_items) must be the SAME
# code on both services, or a UI insert removal and an agent cut would
# re-anchor sfx/zooms/overlays differently.
sys.modules.setdefault("worker_schemas", wschemas)
_tl_path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "worker", "timeline.py")
_tl_spec = importlib.util.spec_from_file_location(
    "worker_timeline", os.path.abspath(_tl_path))
wtimeline = importlib.util.module_from_spec(_tl_spec)
_tl_spec.loader.exec_module(wtimeline)

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
# Round 67: defaults follow the worker onto OpenAI GPT-5.6 Luna. Use the
# exact "-luna" id — the bare "gpt-5.6" alias routes to Sol.
CONCIERGE_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
CONCIERGE_MODEL = os.getenv("CONCIERGE_MODEL",
                            os.getenv("AGENT_MODEL", "gpt-5.6-luna"))
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


def _image_base_url():
    return os.getenv("IMAGE_BASE_URL", "https://api.x.ai/v1")


def _image_api_key():
    """Mirrors config.IMAGE_API_KEY: the chat key is inherited only when the
    image provider IS the chat provider."""
    explicit = os.getenv("IMAGE_API_KEY", "")
    if explicit:
        return explicit
    if _image_base_url() == os.getenv("OPENAI_BASE_URL",
                                      "https://api.deepseek.com"):
        return os.getenv("OPENAI_API_KEY", "")
    return ""


def _image_gen_enabled():
    """Mirrors the worker's generate_image availability check (worker/llm.
    image_available) so the concierge never promises (or denies) AI images out
    of sync with what the editing agent can actually do. Image gen is available
    on the DashScope native endpoint OR any OpenAI-compatible base (xAI).

    Image generation has its OWN provider (IMAGE_BASE_URL/IMAGE_API_KEY), which
    is why this no longer keys off OPENAI_BASE_URL: the chat provider moved to
    DeepSeek, which ships no image-generation model at all. The key is
    inherited only when both providers are the same one — otherwise it must be
    set explicitly, and until it is, images are honestly OFF rather than a 404
    per attempt. Keep in lockstep with worker/llm.image_available."""
    if not os.getenv("IMAGE_GEN_MODEL", "grok-imagine-image-quality"):
        return False
    if not _image_api_key():
        return False
    return bool(os.getenv("IMAGE_API_URL", "") or _image_base_url())


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
    return "dashscope" in _image_base_url()


def _sound_gen_enabled():
    """Mirrors the worker's generate_sfx availability (worker/eleven.
    sound_gen_available) — a dedicated ElevenLabs key, independent of the LLM
    stack. Empty key: the concierge must not offer AI sound generation (the
    built-in pack still works once a video/program exists)."""
    return bool(os.getenv("ELEVENLABS_API_KEY", ""))


def _web_record_enabled():
    """Mirrors the worker's website-capture gate (worker/config.
    WEB_RECORD_ENABLED plus a baked Chromium, which ships in the worker
    image). Same contract and same reason as _url_fetch_enabled above: the
    concierge's list reads as exhaustive, so a capability missing from it is
    actively denied — and 'record my site as a demo' is precisely the request
    a founder makes BEFORE uploading anything, which is exactly when the
    concierge, not the agent, is answering."""
    return os.getenv("WEB_RECORD_ENABLED", "1") == "1"


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
    if _web_record_enabled():
        gen_now.append("record a LIVE WEBSITE straight into the edit from "
                       "nothing but its address — either a scrolling pan of "
                       "the page, or a real walkthrough where a visible "
                       "cursor clicks through the product and the clicks are "
                       "zoomed and sounded (the way to make a launch or "
                       "product-demo video)")

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
        # Transcript-based editing and speed ramps are the two things that
        # genuinely need a main video (both address its source timeline);
        # everything else is available now, once content is on the canvas,
        # or never.
        facts.append(
            "Once a video WITH SPEECH is in the program, transcript-based "
            "editing also unlocks: cutting silences and bad takes, word-timed "
            "captions (including karaoke), censoring on-screen text, and "
            "speed ramps / slow motion (speed changes address the main "
            "video's own timeline, so they need one). Once any visual "
            "content is on the canvas you can also layer image or clip "
            "overlays over it (picture-in-picture, a corner logo — silent, "
            "not motion-tracked) and text / motion-graphics templates. "
            "Regardless, you canNOT do true crossfades that overlap two "
            "shots' footage, motion-track a sticker or graphic onto a "
            "moving object, place text behind the subject, choose a "
            "different transition style per cut, or use custom uploaded "
            "fonts (there is a built-in font menu)"
            + ("" if _video_gen_enabled() else
               ", and you canNOT generate moving VIDEO footage")
            + ". If they ask about something not covered here, say you're not "
            "sure it's supported rather than promising it.")
    else:
        # The can-do list is EXHAUSTIVE by contract (the closing sentence
        # says so), so every round-35 capability the agent actually has must
        # appear here or the concierge actively denies it — the same
        # two-surfaces-disagreeing failure the provider gates prevent.
        facts.append(
            "Once a video is ready you can: cut silences and bad takes, add "
            "word-timed captions (including karaoke word-pop styles), add "
            "background music or voiceover (music can duck smoothly under "
            "speech, dipping and swelling with the voice), use ONLY the "
            "sound of a video they upload — a TikTok, Reel or YouTube "
            "download works as the song and its picture never appears in "
            "the edit, so they never have to convert it to an audio file "
            "themselves, drop one-shot "
            "sound effects (whooshes, impacts, risers, clicks, dings) on "
            "exact moments from a built-in pack"
            + (", or generate custom sound effects to order from a text "
               "description" if _sound_gen_enabled() else "")
            + ", change playback speed on chosen parts of the video (speed "
            "ramps, timelapse, slow motion — slow motion repeats frames, so "
            "below about 0.6x it visibly steps rather than staying fluid), "
            "overlay uploaded images or clips on top of the video as "
            "picture-in-picture, b-roll or a logo (video overlays are "
            "silent, and overlays do not track moving objects), add text "
            "and motion-graphics templates (title, subtitle, lower third, "
            "callout, big number, quote, chapter) with entrance/exit "
            "animations including typewriter — or none at all for an "
            "instant appear/disappear, zooms (including smooth Ken "
            "Burns style and punch-ins aimed at a chosen spot in the "
            "frame), transitions at every cut in 7 styles (dip to black or "
            "white, whip left/right, zoom punch, glitch, flash), fades, "
            "color-grade presets plus continuous custom color controls "
            "(exposure, contrast, saturation, temperature, tint), windowed "
            "finishing effects (film grain, vignette, glow, chromatic "
            "aberration, dream blur, VHS, flash, camera shake), analyze "
            "the real audio's beat grid and the speaker's vocal stress to "
            "align cuts to the music's beat, punch in on emphasized words "
            "and lay an automatic sound-design pass, apply a whole "
            "coordinated look in one request, master the final mix to the "
            "-14 LUFS social loudness target, vertical/square/portrait "
            "reframing, blur/pixelate/black-out a fixed region to censor "
            "burned-in usernames, watermarks or on-screen text, and splice "
            "uploaded clips or images into the video full-frame"
            + (", record a live WEBSITE as real video from its address — "
               "either a scrolling pan of the page or a driven walkthrough "
               "with a visible cursor clicking through the product, which "
               "gets cut into a showcase with the clicks zoomed and sounded"
               if _web_record_enabled() else "")
            + (", and download a video, song or image from a LINK they paste "
               "(direct file links and YouTube/TikTok/Vimeo/SoundCloud pages) "
               "and put it straight into the edit"
               if _url_fetch_enabled() else "")
            + (", and generate short AI video clips from a text description "
               "(or animate a still image into a moving clip) that splice "
               "into the edit"
               if _video_gen_enabled() else "")
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
                   "still-frame moments, not tracked effects), do"
                   if not _video_gen_enabled() else "do"))
               if _image_gen_enabled() else
               (". You canNOT: generate still images from nothing, do"
                if _video_gen_enabled() else
                ". You canNOT: generate footage or images from nothing, "
                "do"))
            + " true crossfades that overlap two shots' footage (transitions "
            "animate around the cut but never blend both sides), motion-"
            "track a sticker or graphic onto a moving object, place text "
            "behind the subject, choose a different transition style per "
            "cut (one style applies to every junction), or use custom "
            "uploaded fonts (there is a built-in font menu). These two "
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
        try:
            resp = _concierge_llm().chat.completions.create(**create_kwargs)
        except Exception as adapt_e:
            # OpenAI's reasoning family (the Luna default) rejects the
            # classic max_tokens and a non-default temperature. Mirror the
            # worker's dialect adaptation in miniature: correct once, retry
            # once — a real failure still lands in the outer except.
            msg = str(adapt_e).lower()
            adapted = False
            if "max_tokens" in create_kwargs and "max_tokens" in msg:
                create_kwargs["max_completion_tokens"] = \
                    create_kwargs.pop("max_tokens")
                adapted = True
            if "temperature" in create_kwargs and "temperature" in msg:
                create_kwargs.pop("temperature")
                adapted = True
            if not adapted:
                raise
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


def _edl_at(cur, project_id, version):
    cur.execute("""SELECT version, json, created_by, created_at FROM edls
                   WHERE project_id = %s AND version = %s""",
                (project_id, version))
    return cur.fetchone()


# ── TWO EDL VERSIONS CAN BE THE SAME VIDEO ───────────────────────────────────
#
# Splitting a clip is a keep-list rewrite that changes NOTHING you can see:
# [[0, 354.61]] split at 18.54 becomes [[0, 18.54], [18.54, 354.61]], which the
# renderer concatenates straight back into the same programme. But a version is
# a version, so it got its own full re-encode — 36 seconds of it on project 246,
# three times in four minutes, for frames that were already on the user's
# screen. Every manual cut in the studio is split-then-delete, so HALF of all
# preview work was rendering a picture identical to the one it replaced.
#
# Worse than the waste: the split's render and the delete's render finish
# seconds apart, and whichever asset id lands higher is the one that attaches.
# When the no-op won, the user's cut visibly did not happen.
#
# So a version's render is keyed on what the renderer will actually PRODUCE.
# Two versions with the same program signature share one encode.
#
# Contiguous keep spans merge — except when a transition is configured, because
# `timeline.transition_junctions` counts a junction per keep boundary and a
# split genuinely adds one (an `every_cut` whip pan would appear at the split).
# Everything else in the EDL is anchored in program or source time, never in
# segment indices, so merging cannot move it.
def _program_signature(edl):
    """Canonical form of what this EDL renders, ignoring differences that the
    renderer cannot express (currently: where the keep list is subdivided)."""
    try:
        e = json.loads(json.dumps(edl))
    except (TypeError, ValueError):
        return None
    effects = e.get("effects") or {}
    if not (effects.get("transition") or {}):
        merged = []
        for span in (e.get("keep") or []):
            try:
                s, t = round(float(span[0]), 3), round(float(span[1]), 3)
            except (TypeError, ValueError, IndexError):
                return None            # malformed: never claim equivalence
            if merged and abs(merged[-1][1] - s) < 1e-3:
                merged[-1][1] = t
            else:
                merged.append([s, t])
        e["keep"] = merged
        # A split INSERT is the same no-op a split keep span is (round 61b):
        # _split_insert leaves head + tail co-located at one boundary, with the
        # tail's source_start_s continuing exactly where the head stops, and
        # list order playing head-then-tail — the renderer concatenates them
        # straight back into the clip they were. So merge content-continuous
        # neighbours before hashing, or every scissors click on a dropped-in
        # clip pays a full re-encode for a byte-identical programme. Only
        # ADJACENT-in-list pieces merge (list order is program order at a
        # shared boundary), only when every other field agrees, and never when
        # either half carries a motion — a Ken Burns move eases per BLOCK, so
        # two halves genuinely render differently from one whole. Skipped when
        # a transition is configured, same as the keep merge above: the
        # head/tail join is a junction the transition resolver can see.
        ins_in = e.get("inserts") or []
        if len(ins_in) >= 2:
            ins_out = [dict(ins_in[0])]
            for nxt in ins_in[1:]:
                prev = ins_out[-1]
                joins = False
                try:
                    if (abs(float(nxt.get("at_output_s"))
                            - float(prev.get("at_output_s"))) < 1e-3
                            and not prev.get("motion")
                            and not nxt.get("motion")
                            and all(prev.get(k) == nxt.get(k)
                                    for k in (set(prev) | set(nxt))
                                    if k not in ("id", "duration_s",
                                                 "source_start_s"))):
                        if nxt.get("kind") == "image":
                            joins = True
                        else:
                            s0 = float(prev.get("source_start_s") or 0.0)
                            s1 = float(nxt.get("source_start_s") or 0.0)
                            joins = abs(
                                s1 - (s0 + float(prev.get("duration_s") or 0.0))
                            ) < 0.005
                except (TypeError, ValueError):
                    joins = False
                if joins:
                    prev["duration_s"] = round(
                        float(prev.get("duration_s") or 0.0)
                        + float(nxt.get("duration_s") or 0.0), 3)
                else:
                    ins_out.append(dict(nxt))
            e["inserts"] = ins_out
    try:
        return wschemas.edl_signature(e)
    except Exception:
        return None


def _preview_twin(cur, project_id, edl, exclude_version=None):
    """The id of an existing preview render that already shows this exact
    programme, or None. Lets a split, an undo/redo, or a re-applied edit attach
    an encode that already exists instead of paying for it again."""
    sig = _program_signature(edl)
    if not sig:
        return None
    cur.execute("""SELECT id, meta FROM assets
                   WHERE project_id = %s AND kind = 'render'
                   ORDER BY id DESC LIMIT 200""", (project_id,))
    renders = cur.fetchall()
    best = {}
    for r in renders:
        m = r.get("meta") or {}
        if m.get("variant") != "preview":
            continue
        try:
            v = int(m.get("edl_version"))
        except (TypeError, ValueError):
            continue
        if v == exclude_version:
            continue
        best.setdefault(v, r["id"])       # rows come newest-first
    if not best:
        return None
    cur.execute("""SELECT version, json FROM edls
                   WHERE project_id = %s AND version = ANY(%s)""",
                (project_id, list(best.keys())))
    for row in cur.fetchall():
        if _program_signature(row["json"]) == sig:
            return best[row["version"]]
    return None


def _adopt_preview(cur, project_id, twin_id, version):
    """Point a new EDL version at an existing preview encode of the same
    programme. The row is a pointer at the same storage key, not a copy: no
    bytes move, and reused_from_asset_id stays canonical (an alias of an
    alias still names the asset that owns the bytes) so the studio can tell
    in one comparison that the object on screen has not changed."""
    cur.execute("""SELECT storage_key, bytes, duration_s, width, height,
                          fps, meta
                   FROM assets WHERE id = %s""", (twin_id,))
    src = cur.fetchone()
    meta = dict(src["meta"] or {})
    meta.update({"edl_version": version, "variant": "preview",
                 "reused_from_asset_id":
                     meta.get("reused_from_asset_id") or twin_id})
    cur.execute("""INSERT INTO assets (project_id, kind, storage_key,
                                       bytes, duration_s, width,
                                       height, fps, meta)
                   VALUES (%s,'render',%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (project_id, src["storage_key"], src["bytes"],
                 src["duration_s"], src["width"], src["height"],
                 src["fps"], Json(meta)))
    return cur.fetchone()["id"]


def _branch_edl(cur, session_id, project_id, base_row):
    """Append a copy of an older version as the new newest — "go back and
    continue from here", the same append-only branch the user-op route has
    done since round 59, now available to a chat message sent while the user
    is viewing an older state. Returns the new version number.

    The copy means the worker's latest_edl() IS the state the user was
    looking at when they typed — without it the agent edited the newest
    version while the user watched an older one, and the reply described a
    timeline the user could not see."""
    cur.execute("""INSERT INTO edls (project_id, version, json, created_by)
                   VALUES (%s, (SELECT COALESCE(MAX(version), 0) + 1
                                FROM edls WHERE project_id = %s),
                           %s, 'user') RETURNING version""",
                (project_id, project_id, Json(base_row["json"])))
    version = cur.fetchone()["version"]
    # Queued previews of older versions are already worthless — same sweep,
    # same reasoning as the user-op route below.
    cur.execute("""UPDATE video_jobs
                      SET state = 'done', result = %s, updated_at = NOW()
                    WHERE project_id = %s AND type = 'preview'
                      AND state = 'queued'
                      AND (payload->>'edl_version')::int < %s""",
                (Json({"superseded_by": version}), project_id, version))
    # The state being branched to was on the user's screen, so its encode
    # almost always exists — adopt it and the player never has to reload.
    twin = _preview_twin(cur, project_id, base_row["json"],
                         exclude_version=version)
    if twin is not None:
        _adopt_preview(cur, project_id, twin, version)
    # NO chat message (round 67). This used to write "you → EDL vN: went back
    # to edit state vX and continued from there" into the chat, and the studio
    # showed its own "editing from an earlier state" banner on top — the owner:
    # "weird words appear at the chat area … remove all of those, I don't want
    # 1000 things in the chat". The branch itself is fully recorded in the edls
    # table (created_by + version lineage); the chat is for the conversation.
    return version


# ------------------------------------------------------------------ #
#  Who pays for the encode, and when (round 60)                        #
# ------------------------------------------------------------------ #
#
# A preview is a full re-encode of the whole programme — 33 to 65 seconds each
# on project 246. That session was scissors, delete, scissors, delete, and it
# bought SIXTEEN of them (~11 minutes of ffmpeg) to arrive at one video; every
# render but the last showed a timeline the user had already changed. Since
# round 58 the studio plays a timing edit itself, off the proxy, in the time it
# takes to click — so the encode does not have to happen per click. It has to
# happen ONCE, on the state the user stops at.
#
# The two rules below are the whole mechanism, and they are deliberately
# separate: one decides what a write does, the other decides what the poll's
# safety net does, and it was the safety net that would have quietly undone the
# saving (exactly as it once re-queued the render an adopted twin had avoided).

def _preview_plan(twin, defer):
    """'adopt' | 'enqueue' | 'defer' for one user EDL write.

    An existing encode of the same programme is ALWAYS adopted, deferral or
    not: it costs nothing, no bytes move, and it is the exact picture rather
    than a draft. Only a write that would otherwise start a fresh encode can be
    deferred, and only because the client said it can show the edit meanwhile.
    """
    if twin is not None:
        return "adopt"
    return "defer" if defer else "enqueue"


def _should_heal_preview(edl, indexed, drafting, agent_turn_failed=False):
    """Should this poll enqueue a preview for the newest EDL?

    The heal exists because the current edit must always have a render on the
    way — any path that fails to enqueue leaves the studio waiting on something
    nobody will ever request, with no error to see and no button to press.

    `drafting` is the version the polling client says it is showing as a draft
    and will ask a render for itself. Suppressing exactly that version is what
    makes deferral possible at all: the heal runs every 2 seconds, so without
    this it would re-queue the encode the deferral exists to coalesce, one poll
    later. Scoped to ONE version and to a client that keeps saying so — the
    moment a tab closes, navigates, or edits again it stops sending it and the
    net is back.

    `agent_turn_failed` (round 67b): agent-made versions are normally the
    turn's own responsibility — it renders its preview, or the worker
    auto-renders one when it forgets. That contract has exactly one hole: a
    turn KILLED mid-flight (a deploy restart, an OOM) leaves the versions it
    already wrote with no preview, no job, and — before this flag — a safety
    net that deliberately looked away. A real user watched "Updating your
    preview…" forever over a stale video (project 298, job 1614, killed by
    the round-67 worker deploy itself). When the project's newest agent turn
    is FAILED there is no turn left to race and nothing speculative about
    rendering the state it abandoned — so the net covers it.
    """
    if not edl or not indexed:
        return False
    if edl["created_by"] != "user" and not agent_turn_failed:
        return False
    return drafting != edl["version"]


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


@video_bp.route("/video/limits", methods=["GET"])
def video_limits():
    """What the studio is allowed to upload. Deliberately unauthenticated and
    deliberately the ONLY place the numbers come from.

    The studio used to carry its own `2 * 1024 * 1024 * 1024` literal, so the
    server's cap and the cap a user actually hit were two different values that
    nobody had to keep in step. Raising MAX_UPLOAD_GB on Render would have
    changed nothing anyone could see.
    """
    return jsonify(storage.upload_limits())


# ------------------------------------------------------------------ #
#  Projects                                                            #
# ------------------------------------------------------------------ #

@video_bp.route("/projects", methods=["POST"])
@token_required
def create_project(user_id):
    data = request.get_json() or {}
    title = (data.get("title") or "").strip() or "Untitled project"
    with vdb() as conn:
        # DELIBERATELY UNGATED (round 49). Creating a project and uploading a
        # video are now free for everyone; the wall is the agent TURN, and
        # nothing before it. See plan_gate.py — the gate used to stand here and
        # at /uploads, which meant a new account met a pricing page before it
        # had shown us a single frame. The card that asks for the trial now
        # arrives AFTER indexing, and it can describe the user's own video.
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
    if kind not in ("original", "music", "image", "clip", "proxy"):
        return jsonify({"error": "kind must be original, music, image, "
                                 "clip or proxy"}), 400
    if kind == "proxy" and not storage.PROXY_FIRST_UPLOADS:
        # The studio reads this off /video/limits and never asks, but a stale
        # bundle in an open tab would. Refuse here too rather than accept bytes
        # the indexer is not yet deployed to understand.
        return jsonify({"error": "Prepared uploads are not enabled"}), 400

    try:
        ext, content_type = storage.validate_upload(filename, nbytes, kind)
    except ValueError as e:
        # Recorded server-side as well as client-side: this is the twin of the
        # studio's own pre-flight check, and it is the copy that survives a
        # user closing the tab the instant their file is refused.
        record_client_event(user_id, project_id, "upload_rejected", detail={
            "reason": str(e), "filename": filename, "bytes": nbytes,
            "kind": kind, "stage": "presign"}, origin="server")
        return jsonify({"error": str(e)}), 400

    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        # DELIBERATELY UNGATED (round 49) — uploading and indexing are the
        # demo. The cost of an index is ours to spend on someone deciding
        # whether to buy; the cost of an agent turn is not, and that is where
        # the gate now stands.

    key = storage.new_original_key(project_id, ext, kind)
    try:
        out = storage.presign_upload(key, nbytes, content_type)
    except Exception as e:
        current_app.logger.exception("presign failed")
        return jsonify({"error": f"Could not prepare upload: {e}"}), 502
    out["kind"] = kind
    # How big a file we could take through our own servers if this browser
    # turns out not to be able to reach storage directly. Carried on the
    # presign so the client always knows without a second request — and so it
    # can say "too big to route through us, try a different network" up front
    # instead of after a 4 GB upload earns a 413.
    out["relay_max_bytes"] = storage.RELAY_MAX_BYTES
    # The one beacon that is NOT a failure. Without a start there is no
    # denominator: an upload that dies mid-transfer looks identical to one that
    # was never attempted, and "how many big uploads never finish" is the
    # question this whole surface exists to answer.
    record_client_event(user_id, project_id, "upload_started", detail={
        "filename": filename, "bytes": nbytes, "kind": kind,
        "mode": out.get("mode"),
        # The transfer's shape, so "why was this one slow" is answerable from
        # this row alone — the 2026-07-31 investigation had to infer "single
        # PUT, one TCP stream" from the absence of these fields.
        **({"part_size": out.get("part_size"),
            "n_parts": len(out.get("part_urls") or [])}
           if out.get("mode") == "multipart" else {})}, origin="server")
    return jsonify(out)


# Recognizing a re-upload (round 67d). The two halves must compute the SAME
# bytes: first CHUNK, plus the last CHUNK when the file is bigger than one —
# hashed together. The studio hashes its local File in ~a second; we verify
# against the candidate object with two ranged GETs and cache the answer on
# the asset so the next check is one SELECT.
_DEDUP_CHUNK = 8 * 1024 * 1024


def _dedup_quickhash_of_key(key, size):
    first = storage.get_range_at(key, 0, min(int(size), _DEDUP_CHUNK))
    if first is None:
        return None
    last = b""
    if size > _DEDUP_CHUNK:
        off = max(_DEDUP_CHUNK, int(size) - _DEDUP_CHUNK)
        last = storage.get_range_at(key, off, int(size) - off)
        if last is None:
            return None
    return hashlib.sha256(first + last).hexdigest()


@video_bp.route("/projects/<int:project_id>/uploads/dedup", methods=["POST"])
@token_required
def dedup_upload(user_id, project_id):
    """Skip the transfer when this user has already uploaded these bytes.

    The user who tests with the same clip re-uploaded 4.35 GB twice in one
    afternoon (projects 298 and 300, 2026-07-31) and waited ~12 minutes each
    time for bytes the bucket already held — and round 47's music customer
    re-uploaded the same file four times. The index has been content-addressed
    since round 58 (same sha -> cache hit), so the transfer was the only part
    still being paid for twice.

    The client asks BEFORE building a proxy or uploading anything, sending
    (bytes, quickhash). A hit creates the asset row pointing at a key that
    does not exist yet — exactly the round-58 deferred-original shape, same
    load-bearing `upload_state` — and the INDEX JOB copies the object
    server-side (worker/indexer.py), so no bytes cross the user's link at
    all. Any mismatch or internal failure answers {dedup: false}: this path
    may only ever make an upload faster, never block one.
    """
    if not storage.is_configured():
        return jsonify({"dedup": False})
    data = request.get_json() or {}
    filename = (data.get("filename") or "").strip()
    try:
        nbytes = int(data.get("bytes") or 0)
    except (TypeError, ValueError):
        nbytes = 0
    quickhash = (data.get("quickhash") or "").strip().lower()
    if nbytes <= 0 or len(quickhash) != 64:
        return jsonify({"dedup": False})
    try:
        storage.validate_upload(filename or "video.mp4", nbytes, "original")
    except ValueError as e:
        # Same refusal create_upload would give — better now than after the
        # client skipped its own pre-flight because dedup said nothing.
        return jsonify({"dedup": False, "error": str(e)}), 400

    try:
        with vdb() as conn:
            cur = conn.cursor()
            if not _project_for_user(cur, project_id, user_id):
                return jsonify({"error": "Project not found"}), 404
            cur.execute("""SELECT a.id, a.storage_key, a.bytes, a.duration_s,
                                  a.width, a.height, a.fps, a.meta
                           FROM assets a JOIN projects p ON p.id = a.project_id
                           WHERE p.user_id = %s AND a.kind = 'original'
                             AND a.bytes = %s AND a.sha256 IS NOT NULL
                           ORDER BY a.id DESC LIMIT 4""",
                        (int(user_id), nbytes))
            candidates = cur.fetchall() or []
        match = None
        for cand in candidates:
            meta = cand.get("meta") or {}
            # A proxy-first original carries the PROXY's sha while its own
            # bytes are still in the browser — sha256 alone does not prove
            # the object is there.
            if meta.get("upload_state") == "pending":
                continue
            if storage.head_bytes(cand["storage_key"]) != nbytes:
                continue
            known = (meta.get("quickhash") or "").lower()
            if not known:
                known = _dedup_quickhash_of_key(cand["storage_key"], nbytes)
                if known:
                    with vdb() as conn:
                        conn.cursor().execute(
                            """UPDATE assets SET meta = meta || %s
                               WHERE id = %s""",
                            (Json({"quickhash": known}), cand["id"]))
            if known and known == quickhash:
                match = cand
                break
        if not match:
            return jsonify({"dedup": False})

        ext = os.path.splitext(match["storage_key"])[1].lstrip(".") or "mp4"
        new_key = storage.new_original_key(project_id, ext, "original")
        with vdb() as conn:
            cur = conn.cursor()
            cur.execute("""INSERT INTO assets (project_id, kind, storage_key,
                                               bytes, duration_s, width,
                                               height, fps, meta)
                           VALUES (%s, 'original', %s, %s, %s, %s, %s, %s, %s)
                           RETURNING id""",
                        (project_id, new_key, nbytes, match["duration_s"],
                         match["width"], match["height"], match["fps"],
                         Json({"filename": filename,
                               "upload_state": "pending",
                               "quickhash": quickhash,
                               "dedup_src": match["storage_key"],
                               "declared_bytes": nbytes})))
            asset_id = cur.fetchone()["id"]
            job_id = _enqueue(cur, project_id, user_id, "index",
                              {"asset_id": asset_id,
                               "dedup_src": match["storage_key"]})
        record_client_event(user_id, project_id, "upload_deduped", detail={
            "filename": filename, "bytes": nbytes,
            "src_asset_id": match["id"]}, origin="server")
        return jsonify({"dedup": True, "asset_id": asset_id,
                        "index_job_id": job_id, "kind": "original"})
    except Exception:
        current_app.logger.exception("dedup check failed")
        return jsonify({"dedup": False})


@video_bp.route("/projects/<int:project_id>/uploads/relay", methods=["POST"])
@token_required
def relay_upload(user_id, project_id):
    """Take the bytes ourselves when the browser cannot reach storage.

    The fallback of last resort — see storage.RELAY_MAX_BYTES for the customer
    this exists for. The client calls it only after the direct presigned PUT
    has failed at the NETWORK layer (no HTTP status), never for a 4xx: a
    refused or expired presign is a bug to fix, not traffic to re-route.

    The key is not trusted. It has to be one this project's own create_upload
    could have issued — same prefix check complete_upload_core makes — so a
    valid token for project A can never write into project B, and no caller can
    choose a path outside the upload prefixes.
    """
    if not storage.is_configured():
        return jsonify({"error": "Video storage is not configured yet"}), 503
    key = request.args.get("key") or ""
    kind = request.args.get("kind") or "original"
    if kind not in ("original", "music", "image", "clip", "proxy"):
        return jsonify({"error": "unsupported kind"}), 400
    prefix = storage.KEY_PREFIX.get(kind, "originals")
    if not key.startswith(f"{prefix}/{project_id}/") or ".." in key:
        return jsonify({"error": "storage_key does not belong to this "
                                 "project"}), 400
    with vdb() as conn:
        if not _project_for_user(conn.cursor(), project_id, user_id):
            return jsonify({"error": "Project not found"}), 404

    declared = request.content_length
    try:
        storage.relay_upload(key, request.stream,
                             request.headers.get("Content-Type"),
                             declared_bytes=declared)
    except storage.RelayTooLarge as e:
        record_client_event(user_id, project_id, "upload_rejected", detail={
            "reason": str(e), "bytes": declared, "kind": kind,
            "stage": "relay"}, origin="server")
        return jsonify({"error": str(e)}), 413
    except Exception as e:
        current_app.logger.exception("relay upload failed")
        record_client_event(user_id, project_id, "upload_failed", detail={
            "reason": str(e)[:300], "bytes": declared, "kind": kind,
            "stage": "relay"}, origin="server")
        return jsonify({"error": f"Could not save the upload: {e}"}), 502

    nbytes = storage.head_bytes(key)
    if nbytes is None:
        return jsonify({"error": "Upload did not land in storage"}), 502
    # The presigned multipart upload the browser could not use is now garbage
    # staged in the bucket that nothing will ever complete. Aborting it is
    # separate from the object we just wrote (an MPU is its own staging area),
    # and best-effort: a failure here costs storage, not correctness.
    abandoned = request.args.get("abandon_upload_id")
    if abandoned:
        try:
            storage.abort_multipart(key, abandoned)
        except Exception:
            current_app.logger.warning(
                "could not abort abandoned multipart %s for %s",
                abandoned, key)
    # Countable on purpose. This route is a symptom, not a feature: if it
    # starts carrying real traffic, something about the direct path is broken
    # for a whole population and the number is how we find out.
    record_client_event(user_id, project_id, "upload_relayed", detail={
        "bytes": nbytes, "kind": kind}, origin="server")
    return jsonify({"ok": True, "storage_key": key, "bytes": nbytes})


# How far the original's real duration may sit from what the browser reported
# before the index is rebuilt from the file itself. Mirrors the worker's
# client_proxy_gap_tolerance ceiling: a gate has to mean the same thing on a
# 30-second clip and a 3-hour one, and 2% of three hours is 3.6 minutes of
# footage the edit would be measured against wrongly.
_ORIGINAL_DRIFT_TOLERANCE_S = 5.0


def _clean_dim(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def _register_deferred_original(user_id, project_id, key, filename, declared,
                                client_proxy_key, proxy_bytes, data):
    """Create the original asset for a proxy-first upload and start indexing.

    The asset's `storage_key` points at an object that DOES NOT EXIST YET. That
    is the whole trick, and it is why `meta.upload_state` is load-bearing:
    every reader that needs the real bytes (export, above all) has to check it
    rather than assume a row implies an object. Duration and geometry come from
    the browser's own read of the file, so the studio can describe the video
    correctly while only the proxy has arrived.
    """
    duration_s = data.get("duration_s")
    try:
        duration_s = min(max(float(duration_s), 0.1), 4 * 3600) \
            if duration_s else None
    except (TypeError, ValueError):
        duration_s = None
    width = _clean_dim(data.get("width"), 16, 16384)
    height = _clean_dim(data.get("height"), 16, 16384)

    with vdb() as conn:
        cur = conn.cursor()
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
                           ORDER BY id DESC LIMIT 1""", (project_id, dup["id"]))
            ij = cur.fetchone()
            return {"asset_id": dup["id"],
                    "index_job_id": ij["id"] if ij else None,
                    "kind": dup["kind"], "duplicate": True}, 200
        cur.execute("""INSERT INTO assets (project_id, kind, storage_key,
                                           bytes, duration_s, width, height,
                                           meta)
                       VALUES (%s, 'original', %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (project_id, key, declared, duration_s, width, height,
                     Json({"filename": filename,
                           "upload_state": "pending",
                           "client_proxy_key": client_proxy_key,
                           "client_proxy_bytes": proxy_bytes,
                           "declared_bytes": declared})))
        asset_id = cur.fetchone()["id"]
        job_id = _enqueue(cur, project_id, user_id, "index",
                          {"asset_id": asset_id,
                           "client_proxy_key": client_proxy_key})
    record_client_event(user_id, project_id, "upload_proxy_first", detail={
        "filename": filename, "bytes": declared, "proxy_bytes": proxy_bytes,
        "duration_s": duration_s}, origin="server")
    return {"asset_id": asset_id, "index_job_id": job_id, "kind": "original",
            "original_pending": True}, 200


def complete_upload_core(user_id, project_id, data):
    """Turn a finished direct-to-storage upload into an asset (+ an index job
    for a main video). Returns (payload_dict, http_status).

    A function rather than route body because the MCP surface (routes/mcp.py)
    finishes uploads too, and this is the single point where a multi-GB upload
    becomes a real asset: the idempotency, the size cap, the magic-byte sniff
    and the per-project lock that stops a duplicate 45-minute index job all
    live here. Two copies of it would be two different sets of those rules.
    """
    if not storage.is_configured():
        return {"error": "Video storage is not configured yet"}, 503

    key = data.get("storage_key") or ""
    kind = data.get("kind") or "original"
    filename = data.get("filename") or ""
    upload_id = data.get("upload_id")
    parts = data.get("parts") or []
    # COMPLETING A MULTIPART UPLOAD WITH NO PARTS IS A DELETE (round 61).
    # The relay path writes the object whole and then has no parts to report;
    # a caller that still sent its upload_id would ask S3 to assemble the key
    # out of nothing, replacing bytes that are already correctly in place. The
    # client clears the id, and this makes it impossible to get wrong from any
    # client, including an old bundle in an open tab.
    if upload_id and not parts:
        upload_id = None
    duration_s = data.get("duration_s")   # client-probed, music/clip/original

    prefix = storage.KEY_PREFIX.get(kind, "originals")
    if not key.startswith(f"{prefix}/{project_id}/"):
        return {"error": "storage_key does not belong to this project"}, 400

    # A browser-built proxy is BYTES, not an asset. It is finalized here so a
    # multipart upload_id gets consumed exactly once, then handed to the
    # original's own complete as `client_proxy_key` — the worker is what
    # decides whether those bytes are a usable proxy, and only it writes the
    # proxy asset row. Creating one here would publish a proxy to the player
    # before anything had probed it.
    if kind == "proxy":
        with vdb() as conn:
            if not _project_for_user(conn.cursor(), project_id, user_id):
                return {"error": "Project not found"}, 404
        if upload_id:
            try:
                storage.complete_multipart(key, upload_id, parts)
            except Exception as e:
                if storage.head_bytes(key) is None:
                    storage.abort_multipart(key, upload_id)
                    record_client_event(user_id, project_id, "upload_failed",
                                        detail={"reason": str(e)[:300],
                                                "kind": "proxy",
                                                "stage": "complete_multipart"},
                                        origin="server")
                    return {"error": f"Upload could not be finalized: {e}"}, 400
        nbytes = storage.head_bytes(key)
        if nbytes is None:
            return {"error": "Prepared video not found in storage"}, 400
        return {"storage_key": key, "bytes": nbytes, "kind": "proxy"}, 200

    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return {"error": "Project not found"}, 404

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
            return {"asset_id": dup["id"],
                    "index_job_id": ij["id"] if ij else None,
                    "kind": dup["kind"], "duplicate": True}, 200

        if kind == "original" and \
                _running_jobs_count(cur, user_id) >= MAX_CONCURRENT_JOBS_PER_USER:
            return {"error": "Too many jobs running. "
                             "Wait for one to finish."}, 429

    # ── The deferred original ────────────────────────────────────────────
    # The browser already built and uploaded a 540p proxy; the original is
    # still streaming up in the background and will be finished by
    # /uploads/original-ready. We register the asset NOW so indexing can start
    # against the proxy — the original is not needed until export, and making
    # the user watch 4 GiB move before they can do anything is the entire
    # problem this path exists to remove.
    client_proxy_key = (data.get("client_proxy_key") or "").strip()
    pending_original = (bool(data.get("original_pending")) and kind == "original"
                        and storage.PROXY_FIRST_UPLOADS)
    if pending_original:
        if not client_proxy_key:
            return {"error": "A deferred original needs a client_proxy_key"}, 400
        if not client_proxy_key.startswith(
                f"{storage.KEY_PREFIX['proxy']}/{project_id}/"):
            return {"error": "client_proxy_key does not belong to this "
                             "project"}, 400
        proxy_bytes = storage.head_bytes(client_proxy_key)
        if proxy_bytes is None:
            return {"error": "The prepared video is missing — upload it "
                             "again"}, 400
        head = storage.get_range(client_proxy_key, 64)
        if storage.content_matches_kind(head, "original") is False:
            return {"error": "The prepared video is not a video file"}, 400
        declared = data.get("bytes")
        try:
            declared = int(declared) if declared else None
        except (TypeError, ValueError):
            declared = None
        if declared and declared > storage.max_upload_bytes():
            record_client_event(user_id, project_id, "upload_rejected", detail={
                "reason": "over size cap at deferred registration",
                "filename": filename, "bytes": declared,
                "cap_bytes": storage.max_upload_bytes(), "kind": kind,
                "stage": "defer"}, origin="server")
            return {"error": "File exceeds the upload size limit"}, 400
        return _register_deferred_original(
            user_id, project_id, key, filename, declared, client_proxy_key,
            proxy_bytes, data)

    if upload_id:
        try:
            storage.complete_multipart(key, upload_id, parts)
        except Exception as e:
            # A retried complete can hit an already-consumed upload_id. If
            # the assembled object EXISTS, the first complete succeeded and
            # this is that retry — proceed. Only abort when it truly failed.
            if storage.head_bytes(key) is None:
                storage.abort_multipart(key, upload_id)
                record_client_event(user_id, project_id, "upload_failed",
                                    detail={"reason": str(e)[:300],
                                            "filename": filename, "kind": kind,
                                            "stage": "complete_multipart"},
                                    origin="server")
                return {"error": f"Upload could not be finalized: {e}"}, 400

    nbytes = storage.head_bytes(key)
    if nbytes is None:
        record_client_event(user_id, project_id, "upload_failed", detail={
            "reason": "object missing in storage after upload",
            "filename": filename, "kind": kind, "stage": "head"},
            origin="server")
        return {"error": "Uploaded file not found in storage"}, 400
    if nbytes > storage.max_upload_bytes():
        record_client_event(user_id, project_id, "upload_rejected", detail={
            "reason": "over size cap at completion", "filename": filename,
            "bytes": nbytes, "cap_bytes": storage.max_upload_bytes(),
            "kind": kind, "stage": "complete"}, origin="server")
        return {"error": "File exceeds the upload size limit"}, 400

    # Magic-byte sniff: the extension was validated at presign, but a renamed
    # file (e.g. a .txt renamed to .mp4) would otherwise sail through and fail
    # deep inside indexing with a confusing error. Reject the clear mismatches
    # early with a clean message; ambiguous bytes are allowed.
    head = storage.get_range(key, 64)
    if storage.content_matches_kind(head, kind) is False:
        record_client_event(user_id, project_id, "upload_rejected", detail={
            "reason": "magic bytes do not match kind", "filename": filename,
            "bytes": nbytes, "kind": kind, "stage": "sniff"}, origin="server")
        return {
            "error": "That file's contents don't match its type — it may be "
                     "renamed or corrupted. Please upload a real "
                     f"{'video' if kind in ('original', 'clip') else kind} "
                     "file."}, 400

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
            return {"asset_id": dup["id"],
                    "index_job_id": ij["id"] if ij else None,
                    "kind": dup["kind"], "duplicate": True}, 200
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

    return {"asset_id": asset_id, "index_job_id": job_id,
            "kind": asset_kind}, 200


@video_bp.route("/projects/<int:project_id>/uploads/complete", methods=["POST"])
@token_required
def complete_upload(user_id, project_id):
    payload, status = complete_upload_core(user_id, project_id,
                                           request.get_json() or {})
    return jsonify(payload), status


def _pending_original(cur, project_id, asset_id):
    cur.execute("""SELECT * FROM assets
                   WHERE id = %s AND project_id = %s AND kind = 'original'""",
                (asset_id, project_id))
    a = cur.fetchone()
    if not a:
        return None, ({"error": "Video not found"}, 404)
    if (a.get("meta") or {}).get("upload_state") != "pending":
        # Already finished. Idempotent by design: the studio retries this POST
        # after a network blip, and a finished background upload must not be
        # reported as an error to a client that simply lost the response.
        return a, ({"asset_id": a["id"], "upload_state": "ready",
                    "duplicate": True}, 200)
    return a, None


@video_bp.route("/projects/<int:project_id>/uploads/original-progress",
                methods=["POST"])
@token_required
def original_upload_progress(user_id, project_id):
    """Stamp how far the background original upload has got.

    Purely so the product can be HONEST later: an export attempted before the
    original lands says "your video is 62% uploaded" instead of a bare refusal,
    and an abandoned upload is visible in admin rather than looking like a
    project that simply never exported.
    """
    data = request.get_json() or {}
    try:
        frac = max(0.0, min(1.0, float(data.get("progress") or 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "progress must be a number"}), 400
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        a, early = _pending_original(cur, project_id, data.get("asset_id"))
        if early:
            return jsonify(early[0]), early[1]
        cur.execute("""UPDATE assets
                       SET meta = meta || %s WHERE id = %s""",
                    (Json({"upload_progress": round(frac, 4)}), a["id"]))
    return jsonify({"ok": True}), 200


@video_bp.route("/projects/<int:project_id>/uploads/original-ready",
                methods=["POST"])
@token_required
def original_upload_ready(user_id, project_id):
    """The background upload of the real original finished — verify and adopt it.

    This is the moment the deferred half of a proxy-first upload becomes real,
    so it repeats the checks the normal path does at completion: finalize the
    multipart, confirm the object exists, and sniff its magic bytes. An
    original that fails them leaves `upload_state` pending rather than being
    adopted, because a project whose export would die is better described as
    still missing its video than as ready.
    """
    data = request.get_json() or {}
    asset_id = data.get("asset_id")
    upload_id = data.get("upload_id")
    parts = data.get("parts") or []
    # COMPLETING A MULTIPART UPLOAD WITH NO PARTS IS A DELETE (round 61).
    # The relay path writes the object whole and then has no parts to report;
    # a caller that still sent its upload_id would ask S3 to assemble the key
    # out of nothing, replacing bytes that are already correctly in place. The
    # client clears the id, and this makes it impossible to get wrong from any
    # client, including an old bundle in an open tab.
    if upload_id and not parts:
        upload_id = None

    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        a, early = _pending_original(cur, project_id, asset_id)
        if early:
            return jsonify(early[0]), early[1]
        key = a["storage_key"]

    if upload_id:
        try:
            storage.complete_multipart(key, upload_id, parts)
        except Exception as e:
            if storage.head_bytes(key) is None:
                storage.abort_multipart(key, upload_id)
                record_client_event(user_id, project_id, "upload_failed",
                                    detail={"reason": str(e)[:300],
                                            "kind": "original",
                                            "stage": "original_ready"},
                                    origin="server")
                return jsonify({"error": f"Upload could not be finalized: {e}"
                                }), 400

    nbytes = storage.head_bytes(key)
    if nbytes is None:
        record_client_event(user_id, project_id, "upload_failed", detail={
            "reason": "original missing in storage after background upload",
            "kind": "original", "stage": "original_ready"}, origin="server")
        return jsonify({"error": "Uploaded file not found in storage"}), 400
    if nbytes > storage.max_upload_bytes():
        return jsonify({"error": "File exceeds the upload size limit"}), 400
    head = storage.get_range(key, 64)
    if storage.content_matches_kind(head, "original") is False:
        return jsonify({"error": "That file's contents don't match a video"}), 400

    # ── Check the claim against the file ─────────────────────────────────
    # The edit was built against a duration the BROWSER reported, because for
    # the whole time the project has been editable this object did not exist.
    # Now it does, so verify it — reading the moov atom over a few ranged
    # requests, never downloading the file. A mismatch means every timestamp in
    # the EDL is measured against the wrong length, and the first thing that
    # would notice is the export.
    #
    # Fails OPEN: an unparseable container is not evidence the browser lied,
    # and refusing an upload over an unreadable header would break exactly the
    # long-tail formats this check cannot help with anyway.
    claimed = a.get("duration_s")
    drift = None
    if claimed and (a.get("meta") or {}).get("client_proxy_key"):
        actual = mp4probe.duration_of_key(storage, key, nbytes)
        if actual and abs(actual - float(claimed)) > _ORIGINAL_DRIFT_TOLERANCE_S:
            drift = {"claimed_s": round(float(claimed), 2),
                     "actual_s": round(actual, 2)}

    with vdb() as conn:
        cur = conn.cursor()
        patch = {"upload_state": "ready", "upload_progress": 1.0}
        if drift:
            patch["duration_drift"] = drift
        cur.execute("""UPDATE assets SET bytes = %s, meta = meta || %s
                       WHERE id = %s""", (nbytes, Json(patch), asset_id))
        if drift:
            # Re-index from the ORIGINAL. No client_proxy_key on the payload,
            # so the indexer takes the ordinary trusted path — which is the
            # same self-healing rule the worker already applies whenever the
            # original turns out to exist.
            cur.execute("""SELECT user_id FROM projects WHERE id = %s""",
                        (project_id,))
            owner = cur.fetchone()
            _enqueue(cur, project_id, owner["user_id"], "index",
                     {"asset_id": asset_id, "reindex": True})
            print(f"[uploads] project {project_id}: original is "
                  f"{drift['actual_s']}s but the browser claimed "
                  f"{drift['claimed_s']}s — re-indexing from the original",
                  flush=True)

    record_client_event(user_id, project_id, "upload_original_ready", detail={
        "bytes": nbytes, **({"duration_drift": drift} if drift else {})},
        origin="server")
    return jsonify({"asset_id": asset_id, "upload_state": "ready",
                    "bytes": nbytes,
                    **({"reindexing": True} if drift else {})}), 200


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


def _retokenize_span(new_text, t0, t1, speaker=None):
    """Split corrected sentence text into word tokens and lay them across the
    sentence's [t0,t1] window, proportional to token length. Captions are
    word-timed, so the corrected words must carry timings or karaoke captions
    would desync.

    The rewritten words inherit the sentence's SPEAKER (the user retyped that
    person's line, not somebody else's) and re-derive their own filler flag,
    so an edit cannot quietly take a video from diarized to undiarized.
    """
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
        out.append({"w": tok, "t0": round(wt0, 3), "t1": round(wt1, 3),
                    "speaker": speaker,
                    "filler": wschemas.is_filler_token(tok)})
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
            # The WHOLE word, not a three-field copy. This used to rebuild
            # {w,t0,t1} and drop everything else, which after round 69 would
            # mean correcting one line of the transcript silently stripped
            # speaker labels and filler tags from the ENTIRE video — taking
            # remove_filler_words back to a no-op for that project.
            return [dict(w) for w in words[wi0:wi1 + 1]]
        return None

    new_words, new_sentences, updated = [], [], None
    for s in sentences:
        s2 = dict(s)
        if s.get("id") == sentence_id:
            toks = _retokenize_span(new_text, s.get("t0"), s.get("t1"),
                                    speaker=s.get("speaker"))
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

        # SELF-HEAL: the current edit must always have a render on the way.
        #
        # Belt to the braces above. Any path that fails to enqueue a preview —
        # the concurrency cap that used to skip it, a 500 between the EDL
        # insert and the enqueue, a future caller that simply forgets — leaves
        # the newest EDL with no job, and the studio waits on a render that
        # will never be requested. There is no error to see and no button to
        # press: the progress bar just sits full.
        #
        # Naturally idempotent, which is what makes it safe to run on every
        # poll: the moment a job row exists for that version the condition is
        # false. A FAILED job also counts as existing, so a render that keeps
        # dying is never re-queued in a loop here — that is the retry path's
        # job, and it has its own bounded budget.
        # Scoped to USER edits deliberately. An agent turn enqueues its own
        # preview and has its own retry path, so healing those would (a) race
        # a turn that is about to enqueue one for the same version, and (b)
        # speculatively render every project whose last EDL was the agent's
        # opening version — 56 of them at the time of writing, none of which
        # anyone had asked to see. The bug was in the user-edit path, where
        # this write is the only thing that ever enqueues.
        # A version can also be covered WITHOUT a job: an edit that renders the
        # same programme as an earlier version adopts its encode outright
        # (_preview_twin). Without this clause the heal fires on every one of
        # those and re-queues exactly the render the adoption exists to avoid —
        # it did, on the first run of the studio test.
        # ...and EXCEPT while a client is drafting this exact version and will
        # ask for the render itself — see _should_heal_preview.
        drafting = request.args.get("drafting", type=int)
        # Round 67b: a turn killed mid-flight (deploy restart, OOM) leaves its
        # written versions unrendered and no job to wait on. Only when the
        # newest agent_turn is terminally FAILED does the heal extend to
        # agent-made versions — a live turn still owns its own render, and a
        # completed one always left a preview behind.
        agent_turn_failed = False
        if edl and edl.get("created_by") != "user":
            cur.execute("""SELECT state FROM video_jobs
                           WHERE project_id = %s AND type = 'agent_turn'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            last_turn = cur.fetchone()
            agent_turn_failed = bool(last_turn
                                     and last_turn["state"] == "failed")
        if _should_heal_preview(edl, indexed, drafting, agent_turn_failed):
            cur.execute("""SELECT 1 FROM video_jobs
                           WHERE project_id = %s AND type = 'preview'
                             AND (payload->>'edl_version')::int = %s
                           LIMIT 1
                           """, (project_id, edl["version"]))
            covered = cur.fetchone() is not None
            if not covered:
                # The cast is guarded: meta is worker-written JSON, and ONE row
                # with a non-numeric edl_version would raise here and take the
                # whole state poll — the studio's heartbeat — down with it.
                cur.execute("""SELECT 1 FROM assets
                               WHERE project_id = %s AND kind = 'render'
                                 AND meta->>'variant' = 'preview'
                                 AND meta->>'edl_version' ~ '^[0-9]+$'
                                 AND (meta->>'edl_version')::int = %s
                               LIMIT 1""", (project_id, edl["version"]))
                covered = cur.fetchone() is not None
            if not covered:
                print(f"[state] project {project_id}: EDL v{edl['version']} "
                      f"had no preview job — enqueuing one", flush=True)
                _enqueue(cur, project_id, user_id, "preview",
                         {"edl_version": edl["version"],
                          "source": "user_edit", "self_heal": True})

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

        # First page 2500, follow-ups 500. A busy project holds thousands of
        # activity rows, and at 500/page the studio spent its first minute
        # filtering chat against PARTIAL history (2-8s per polling tick per
        # page) — the version stepper and the rewind filter both compute
        # from message stamps, so they were wrong until the backlog arrived.
        page = 2500 if not after_id else 500
        cur.execute("""SELECT id, role, content, meta, created_at
                       FROM chat_messages
                       WHERE session_id = %s AND id > %s
                       ORDER BY id ASC LIMIT %s""",
                    (p["chat_session_id"], after_id, page))
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
        _gate = _final_gate(cur, project_id, user_id)

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
        if variant == "final" and not _gate(a["id"], m, v):
            continue          # stale card, transitions or watermark: re-export
        if variant not in bv:
            # object_id names the bytes, not the row. Two versions that render
            # the same programme share one encode (see _preview_twin), and the
            # studio must not tear the video down and reload it to show a
            # picture that is already on screen — pressing Split would restart
            # playback from zero.
            bv[variant] = {"id": a["id"], "created_at": a["created_at"],
                           "object_id": m.get("reused_from_asset_id")
                           or a["id"]}
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
                          "object_id": pv.get("object_id") or pv["id"],
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
             # object_id names the BYTES (see by_version above). The studio
             # compares it on version picks so an adopted twin never tears
             # down and reloads the identical picture — it was only ever sent
             # by /edls, so every pick fed from this poll reloaded.
             "preview_object_id":
                 (by_version.get(v["version"], {}).get("preview")
                  or {}).get("object_id"),
             "final_asset_id":
                 (by_version.get(v["version"], {}).get("final") or {}).get("id")}
            for v in versions],
        "latest_preview": latest_preview,
        "music_assets": [
            {"id": a["id"], "storage_key": a["storage_key"],
             "filename": (a.get("meta") or {}).get("filename"),
             "duration_s": a["duration_s"]} for a in music],
        # `generated` marks media the AGENT produced inside this project —
        # generated images/video, colour and glitch cards, link downloads,
        # website captures. They are legitimately re-insertable, so they belong
        # in the picker, but they are not the user's own files and the UI must
        # not present them as such.
        "media_assets": [
            {"id": a["id"], "kind": a["kind"],
             "storage_key": a["storage_key"],
             "filename": (a.get("meta") or {}).get("filename"),
             "generated": bool((a.get("meta") or {}).get("generated")
                               or (a.get("meta") or {}).get("fetched")
                               or (a.get("meta") or {}).get("recorded")),
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
    # The EDL version the user was LOOKING AT when they typed (the studio
    # sends it only while stepped back in the edit history). Same contract as
    # the user-op route's base_version: a message sent from an older state
    # means "continue from HERE", and the turn must branch, not edit the
    # newest version behind the user's back.
    try:
        base_version = int(data.get("base_version"))
    except (TypeError, ValueError):
        base_version = None
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

        # One editor at a time per project — EDL writes must not race. That
        # includes an outside model driving this project over MCP (round 49):
        # it holds the timeline for the length of one tool call, and the MCP
        # side refuses symmetrically while an agent turn is live.
        cur.execute("""SELECT type FROM video_jobs
                       WHERE project_id = %s
                         AND type IN ('agent_turn', 'mcp_tool')
                         AND state IN ('queued','running')
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        busy = cur.fetchone()
        if busy:
            return jsonify({"error": (
                "Another editing session is working on this project right "
                "now — give it a moment." if busy["type"] == "mcp_tool"
                else "The editor is still working on your previous request."
            )}), 409

        original = _active_original(cur, project_id)
        indexed = bool(original and _index_row(cur, original["sha256"]))

        # The plan gate — round 50: "this account has spent its 50 free
        # credits and holds no plan". It hangs off `indexed` for the SAME
        # reason the credits gate below does: neither question applies until a
        # message would actually run an agent turn.
        #
        # It used to sit above all of this, unconditionally. So a brand-new
        # account that typed "hi" into an EMPTY project — no upload, nothing to
        # edit — got 402 plan_required, and the studio answered a greeting with
        # a paywall headed "I've watched it. Here's what I found." It had
        # watched nothing. The wall was real but it arrived before the product
        # had said a single word, which is precisely what round 49 set out to
        # stop doing.
        #
        # Pre-index chat is the concierge: cheap, rate-limited above, and never
        # charged. Let it answer. The gate closes the moment there is an index,
        # which is also the moment the card has something true to say.
        if indexed and plan_gate.needs_plan(conn, user_id):
            return plan_gate.gate_response(jsonify)

        # Credits gate — same condition, different question: the plan gate
        # above answers for anyone with NO plan (their free 50 are spent), this
        # one answers for everybody else — a spent trial slice, or a subscriber
        # who has burned this cycle's pool.
        if indexed and not check_and_reserve(conn, user_id,
                                             min_credits=1.0):
            info = get_balance(conn, user_id)
            spent = info.get("free_trial_exhausted")
            # FOUR walls now, four different true things to say. A refused card
            # is the newest and the most specific: this person tried to pay,
            # the payment did not go through, and neither "start a trial" nor
            # "you're out of credits" is a description of that. Telling them to
            # subscribe when they already have would be the same class of lie
            # the admin was telling about them.
            if info.get("payment_failed"):
                return jsonify({
                    "error": (f"{info.get('payment_failed_message')} "
                              "Your credits are on hold until it clears — "
                              "updating your card puts everything back."),
                    "payment_failed": True,
                    "plan": info.get("billing_plan") or info.get("plan"),
                    "code": "payment_failed"}), 402
            # A trialling user has already entered a card, so "start a trial"
            # is nonsense to them and "wait for your cycle" is worse — the rest
            # of their plan is released by CONVERTING, which is a thing they
            # can do right now.
            if info.get("trial_cap_reached"):
                return jsonify({
                    "error": ("That's the credits included with your free "
                              "trial. Your plan starts the moment you keep "
                              "it — no waiting, and the full monthly pool "
                              "unlocks straight away."),
                    "trial_cap_reached": True,
                    "plan": info.get("plan"),
                    "trial_credits": info.get("plan_limit"),
                    "code": "trial_cap_reached"}), 402
            return jsonify({
                "error": ("You're out of credits."
                          if spent else
                          "You're out of credits — they refresh on your "
                          "plan's cycle, or upgrade for a bigger monthly "
                          "pool."),
                "free_trial_exhausted": bool(spent),
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

        # Resolve which edit state this message is ABOUT, and whether the
        # turn must branch to it first. Every message is stamped with that
        # version (meta.edl_version) so the studio can roll the chat back in
        # step with the version stepper — the stamp is the state the user was
        # looking at, which is base_version while stepped back and the newest
        # version otherwise.
        latest_row = _latest_edl(cur, project_id) if indexed else None
        branch_base = None
        stepped_back = False
        # ROUND 71b: the stamp is the version the TURN STARTS FROM — never
        # base_version. The studio cuts the rolled-back chat at the first
        # user message stamped at-or-after the viewed state, so a message
        # stamped with the OLD version it was typed against outlived its own
        # rollback: the user stepped back onto the branched copy (a NEWER
        # number showing the OLDER picture) and their request stayed in the
        # chat over the picture from before it. Stamp = the head now, or the
        # branched copy re-stamped after _branch_edl below; branch_base
        # records where the user stepped back to, which is what prunes the
        # abandoned exchanges.
        msg_version = latest_row["version"] if latest_row else None
        if latest_row and base_version is not None \
                and base_version != latest_row["version"]:
            picked = _edl_at(cur, project_id, base_version)
            if picked:
                stepped_back = True
                try:
                    differs = (wschemas.edl_signature(picked["json"])
                               != wschemas.edl_signature(latest_row["json"]))
                except Exception:
                    differs = True
                if differs:
                    branch_base = picked

        meta = {}
        if msg_version is not None:
            meta["edl_version"] = msg_version
        if stepped_back:
            # This message CONTINUES from an older state — everything the
            # chat said after that state now belongs to an abandoned branch.
            # The studio prunes those rows on this marker (round 71); nothing
            # server-side is deleted, and no banner/divider is ever written.
            meta["branch_base"] = base_version
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

            if branch_base is not None:
                # Branch BEFORE the turn is queued, in the same transaction
                # as the message: the worker's latest_edl() then already IS
                # the state the user was looking at, and the studio's poll
                # sees the branch (with its adopted preview) immediately.
                new_v = _branch_edl(cur, p["chat_session_id"], project_id,
                                    branch_base)
                # Re-stamp with the branched copy — the state this turn
                # actually starts from (see the round-71b note above). Same
                # transaction as the insert, so no poll ever sees the old
                # stamp.
                meta["edl_version"] = new_v
                cur.execute("UPDATE chat_messages SET meta = %s WHERE id = %s",
                            (Json(meta), message_id))
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
                    info = get_balance(conn, ctx["user_id"])
                    spent = info.get("free_trial_exhausted")
                    # A trialling account reaches here too, and neither of the
                    # other two sentences is addressed to it: they have already
                    # entered a card, and the rest of their pool is released by
                    # KEEPING the plan, not by waiting for a cycle. The studio
                    # picks the card variant off exactly these three flags.
                    trial = info.get("trial_cap_reached")
                    declined = info.get("payment_failed")
                    _say((f"{info.get('payment_failed_message')} Your credits "
                          "are on hold until it clears — updating your card "
                          "puts everything back."
                          if declined else
                          "That's the credits included with your free trial."
                          if trial else
                          "You're out of credits."
                          if spent else
                          "You're out of credits — they refresh on your "
                          "plan's cycle, or upgrade for a bigger monthly pool "
                          "to keep creating."),
                         {"kind": "concierge", "credits_exhausted": True,
                          "free_trial_exhausted": bool(spent),
                          "payment_failed": bool(declined),
                          "trial_cap_reached": bool(trial)})
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

# Longest an inserted clip may be. One number, shared by the drop
# (insert_media) and the resize handle (set_insert_duration), so a clip cannot
# arrive at a length the chip then refuses to restore.
_INSERT_MAX_S = 600.0


def _pick_audio_error(asset, what):
    """Why an asset can't be used as sound HERE, and where it can be.

    A video's soundtrack IS usable — the agent lifts it out (worker
    _audio_from_clip). This service has no ffmpeg, so the timeline route says
    where the capability lives instead of "pick an audio file", which reads as
    "your file is no good" and sends the user off to convert it by hand."""
    if asset and asset["kind"] == "video_clip":
        return ("That's a video. Ask in chat to use its sound (\"use the song "
                "from this clip\") and the editor will take the audio out of "
                "it for you — its picture stays out of the edit.")
    return f"Pick an uploaded audio file for {what}."


def _split_insert(edl, tl, at):
    """Split the inserted clip under program time `at` into two inserts.

    Returns (new_edl, desc) or raises ValueError with a user-facing reason —
    the same contract as every branch of _apply_edl_op.

    An IMAGE cannot be split in any meaningful sense: both halves would be the
    same still, and "the second half of a photograph" is not a thing a user
    means. It is refused in those words rather than silently producing two
    identical blocks.
    """
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    if not inserts:
        raise ValueError("Move the playhead onto the footage to split.")
    wins = wtimeline.insert_windows(inserts, tl)
    # By POSITION, not by value: two b-roll blocks of the same clip and length
    # are equal dicts, and list.index() would then hand back the first one.
    at_i = next((k for k, i in enumerate(inserts)
                 if i.get("id") in wins
                 and wins[i["id"]][0] - 1e-6 <= at <= wins[i["id"]][1] + 1e-6),
                None)
    if at_i is None:
        raise ValueError("Move the playhead onto the footage to split.")
    hit = inserts[at_i]
    start, end = wins[hit["id"]]
    if hit.get("kind") == "image":
        raise ValueError("That block is a still image — splitting it would "
                         "just give you the same picture twice. Drag its edge "
                         "to change how long it shows instead.")
    # Same guard the footage branch uses: a cut 0.05s from an edge is not a
    # cut, it is a second block nobody can see or grab.
    head = round(at - start, 3)
    tail = round(end - at, 3)
    if head < 0.05 or tail < 0.05:
        raise ValueError("That point is already a clip edge — nothing to "
                         "split.")
    src0 = round(max(0.0, float(hit.get("source_start_s") or 0.0)), 3)
    taken = {i.get("id") for i in inserts}
    n = 1
    while f"ins{n}" in taken:
        n += 1
    second = dict(hit)
    second["id"] = f"ins{n}"
    second["duration_s"] = tail
    # Where the tail starts IN THE CLIP — the head's own offset plus the head's
    # length. Without this the second half replays the beginning of the clip,
    # which is the bug that makes "split" look like "duplicate".
    second["source_start_s"] = round(src0 + head, 3)
    hit["duration_s"] = head
    # Directly AFTER its own head in the list: list order is what decides
    # program order at a shared boundary, so appending it would play the tail
    # before the head whenever another insert sits at the same cut.
    inserts.insert(at_i + 1, second)
    edl["inserts"] = inserts
    return edl, (f"split the inserted clip at {round(at, 2)}s "
                 f"({head}s + {tail}s)")


def _apply_edl_op(edl, op, args, assets_by_id, src_dur=None,
                  speech_spans=None):
    """Apply one UI operation to an EDL dict. Returns (new_edl, desc) or
    raises ValueError with a user-facing message. Mirrors the agent tools'
    snapping semantics (worker/agent_tools.py).

    src_dur: the original's duration (needed by the keep-editing ops).
    speech_spans: [(t0, t1)] source-time sentence spans, or None when the
    index isn't loaded — used only by add_music's context-aware defaults."""
    edl = json.loads(json.dumps(edl))   # deep copy
    if op == "set_frame":
        ratio = str(args.get("ratio") or "source")
        mode = str(args.get("mode") or "crop")
        if ratio == "source":
            edl["frame"] = None
            return edl, "output frame back to source"
        frame = {"ratio": ratio, "mode": mode}
        # Subject-aware crop focus (round 36) — same field the agent's
        # auto_reframe writes. Round 54 gave the STUDIO a way to send one too:
        # the reframe panel's crop box is this pair, so a user can aim the
        # crop at their subject instead of accepting the dead-centre window
        # that made every reframe look like a zoom into the middle.
        for k in ("focus_x", "focus_y"):
            if args.get(k) is not None:
                frame[k] = float(args[k])
        edl["frame"] = frame
        aim = ""
        if mode == "crop" and ("focus_x" in frame or "focus_y" in frame):
            aim = (f", aimed at {round(frame.get('focus_x', 0.5) * 100)}%"
                   f"/{round(frame.get('focus_y', 0.5) * 100)}%")
        return edl, f"output frame {ratio} ({mode}{aim})"

    if op == "split_keep":
        # Split the take under the playhead into two clips — the CapCut
        # scissors. Purely a keep-list rewrite: the program's picture and
        # timing are unchanged until the user deletes/trims a side (round-35
        # span_to_out handles the resulting contiguous boundary exactly).
        at = float(args.get("at_program_s") or 0.0)
        tl = wtimeline.Timeline(edl["keep"], edl.get("inserts") or [],
                                edl.get("speed") or [])
        src = tl.out_to_src(at)
        if src is None:
            # THE SCISSORS WORK ON AN INSERTED CLIP TOO (round 61).
            #
            # out_to_src maps a program time inside a splice to None, and the
            # answer used to be "move the playhead onto the footage" — which is
            # a refusal to cut a block that is sitting right there on the
            # timeline looking exactly like every other block. A spliced clip is
            # a clip; the reason the tool could not touch one was a
            # representation gap, not an editorial rule.
            #
            # Both halves stay at the SAME keep boundary (there is no other
            # legal position for an insert) and source_start_s says which half
            # each one is. That only reads back in the right order because
            # timeline._ins_sort_key breaks a shared boundary by LIST order:
            # sorting by duration, as it used to, played the tail first.
            return _split_insert(edl, tl, at)
        keep = [list(x) for x in edl["keep"]]
        for i, (s, e) in enumerate(keep):
            if s + 0.05 < src < e - 0.05:
                cut = round(float(src), 3)
                keep[i:i + 1] = [[s, cut], [cut, e]]
                edl["keep"] = keep
                return edl, f"split the clip at {round(at, 2)}s"
        raise ValueError("That point is already a clip edge — nothing to "
                         "split.")

    if op == "remove_keep_segment":
        idx = int(args.get("index", -1))
        keep = [list(x) for x in edl.get("keep") or []]
        if not (0 <= idx < len(keep)):
            return edl, "clip already gone"
        if len(keep) == 1:
            # Also refused when there ARE inserts. Popping the last keep
            # segment leaves keep empty with no canvas, which validate_edl
            # rejects in language about canvas programs that means nothing to
            # someone who just pressed delete — and the honest answer is the
            # same either way: something has to be left to cut.
            if edl.get("inserts"):
                raise ValueError(
                    "Can't delete the last piece of your own footage — the "
                    "spliced-in clips would be all that's left. Delete those "
                    "clips first, or trim this one instead.")
            raise ValueError("Can't delete the only clip — the video would "
                             "be empty. Trim it instead.")
        s, e = keep.pop(idx)
        edl["keep"] = keep
        return edl, (f"deleted the clip covering {round(s, 2)}-"
                     f"{round(e, 2)}s of the source")

    if op == "trim_keep_segment":
        # Drag a clip edge by delta_s PROGRAM seconds (negative = leftward).
        # Applied in SOURCE time via the local speed factor, clamped to the
        # neighbours — dragging outward restores cut footage, inward cuts
        # more, exactly like a clip trim in any NLE.
        idx = int(args.get("index", -1))
        edge = str(args.get("edge") or "")
        try:
            delta = float(args.get("delta_s"))
        except (TypeError, ValueError):
            raise ValueError("Bad trim delta.")
        keep = [list(x) for x in edl.get("keep") or []]
        if not (0 <= idx < len(keep)) or edge not in ("start", "end"):
            raise ValueError("Bad trim target.")
        s, e = keep[idx]
        pieces = wschemas.speed_pieces(s, e, edl.get("speed") or [])
        if edge == "start":
            factor = pieces[0][2] if pieces else 1.0
            lo = keep[idx - 1][1] if idx > 0 else 0.0
            new_s = round(min(max(s + delta * factor, lo), e - 0.1), 3)
            if abs(new_s - s) < 0.01:
                return edl, "no trim"
            keep[idx][0] = new_s
        else:
            factor = pieces[-1][2] if pieces else 1.0
            hi = (keep[idx + 1][0] if idx < len(keep) - 1
                  else float(src_dur or e + abs(delta) + 1.0))
            new_e = round(max(min(e + delta * factor, hi), s + 0.1), 3)
            if abs(new_e - e) < 0.01:
                return edl, "no trim"
            keep[idx][1] = new_e
        edl["keep"] = keep
        return edl, (f"trimmed the clip to {keep[idx][0]}-{keep[idx][1]}s "
                     "(source)")

    if op == "trim_music":
        prog = wschemas.program_duration(edl)
        for m in (edl.get("music") or []):
            if m.get("id") == args.get("id"):
                old_start = float(m["start"])
                off = max(0.0, float(m.get("offset_s") or 0.0))
                track = next((a for a in assets_by_id.values()
                              if a.get("storage_key") == m.get("storage_key")),
                             None)
                track_dur = float((track or {}).get("duration_s") or 0.0)
                start = round(min(max(float(args.get("start",
                                                     m["start"]) or 0.0),
                                      0.0), max(0.0, prog - 0.5)), 2)
                # Round 79f — the LEFT edge edits the TRACK, not the
                # schedule: trimming the head consumes track head
                # (offset_s advances) and pulling it back out restores it,
                # so the song stays anchored in time like in any editor.
                # Extending is bounded by the head the track actually has.
                d = start - old_start
                if d < 0:
                    d = max(d, -off)
                    start = round(old_start + d, 2)
                if abs(d) > 1e-9:
                    m["offset_s"] = round(off + d, 3)
                    off = m["offset_s"]
                end = round(min(max(float(args.get("end", m["end"])
                                          or prog), start + 0.5), prog), 2)
                # A window longer than the track's remainder would play
                # silence off the end (loop excepted) — clamp honestly.
                if track_dur > 0.05 and not m.get("loop"):
                    end = min(end, round(start + max(0.5, track_dur - off), 2))
                m["start"], m["end"] = start, end
                return edl, f"music {m['id']} now plays {start}-{end}s"
        return edl, "music already gone"

    if op == "slip_music":
        # Round 79f — SLIP: slide the song under a fixed window. The block
        # stays where it is on the timeline; offset_s picks which part of
        # the track fills it. This is the missing verb that made everything
        # past the first 28s of a 66s track unreachable from the UI.
        for m in (edl.get("music") or []):
            if m.get("id") == args.get("id"):
                length = float(m["end"]) - float(m["start"])
                off = max(0.0, float(args.get("offset_s") or 0.0))
                track = next((a for a in assets_by_id.values()
                              if a.get("storage_key") == m.get("storage_key")),
                             None)
                track_dur = float((track or {}).get("duration_s") or 0.0)
                if track_dur > 0.05 and not m.get("loop"):
                    off = min(off, max(0.0, track_dur - length))
                m["offset_s"] = round(off, 3)
                return edl, (f"music {m['id']} slipped — now plays from "
                             f"{m['offset_s']}s into the track")
        return edl, "music already gone"

    if op == "split_music":
        # Round 79 — the timeline's scissors reach the MUSIC lane. "Cut this
        # part of the song out" was impossible from the UI: trim could only
        # shorten an edge, so removing a middle passage meant deleting the
        # whole track and re-adding it twice. The split is sample-continuous:
        # the tail starts exactly at the head's cut, offset_s advanced by the
        # head's length, so playback across the boundary is seamless until
        # the user moves or deletes one half.
        at = float(args.get("at_program_s") or 0.0)
        items = list(edl.get("music") or [])
        hit = next((m for m in items if m.get("id") == args.get("id")), None)
        if hit is None:
            # No id (or stale): split whatever plays under the playhead.
            hit = next((m for m in items
                        if float(m["start"]) < at < float(m["end"])), None)
        if hit is None:
            raise ValueError("No music under the playhead to split — put the "
                             "playhead inside the track first.")
        s0, e0 = float(hit["start"]), float(hit["end"])
        if not (s0 + 0.25 <= at <= e0 - 0.25):
            raise ValueError("Put the playhead at least 0.25s inside the "
                             "music to split it.")
        at = round(at, 2)
        delta = at - s0
        tail = json.loads(json.dumps(hit))
        taken = {m.get("id") for m in items}
        n = 1
        while f"mus{n}" in taken:
            n += 1
        tail["id"] = f"mus{n}"
        tail["start"], tail["end"] = at, e0
        # The tail continues where the head stops. When the item loops a
        # short track, wrap through the track's real length or the offset
        # can point past the file and the render's loop math breaks.
        off = float(hit.get("offset_s") or 0.0) + delta
        track = next((a for a in assets_by_id.values()
                      if a.get("storage_key") == hit.get("storage_key")), None)
        track_dur = float((track or {}).get("duration_s") or 0.0)
        if hit.get("loop") and track_dur > 0.05:
            off = off % track_dur
        tail["offset_s"] = round(off, 3)
        # Edge fades stay at the OUTER edges: a fade at the cut itself would
        # dip the music at a boundary the split promises is inaudible.
        hit["end"] = at
        if hit.get("fade_out_s"):
            tail["fade_out_s"] = hit["fade_out_s"]
        hit["fade_out_s"] = None
        tail["fade_in_s"] = None
        items.insert(items.index(hit) + 1, tail)
        edl["music"] = items
        return edl, (f"split music {hit['id']} at {at}s — "
                     f"{hit['id']} plays {s0}-{at}s, {tail['id']} "
                     f"plays {at}-{e0}s")

    if op == "retime_overlay":
        prog = wschemas.program_duration(edl)
        for ov in (edl.get("overlays") or []):
            if ov.get("id") == args.get("id"):
                if ov.get("screen"):
                    # A screen takeover has to END exactly where the clip it
                    # pushes into begins — dragging its block in the timeline
                    # would slide the push off the cut and turn the one join
                    # it exists to hide into a jump. Same rule the agent tool
                    # applies (agent_tools.move_overlay); one policy, both
                    # surfaces.
                    raise ValueError(
                        "This block is a screen takeover — its timing is "
                        "locked to the clip it pushes into, so it can't be "
                        "dragged on its own. Ask me to move the takeover and "
                        "I'll move both together.")
                if args.get("start") is not None:
                    ov["start"] = round(min(max(float(args["start"]), 0.0),
                                            max(0.0, prog - 0.2)), 2)
                if args.get("duration_s") is not None:
                    ov["duration_s"] = round(
                        min(max(float(args["duration_s"]), 0.2),
                            max(0.2, prog - float(ov["start"]))), 2)
                else:
                    over = float(ov["start"]) + float(ov["duration_s"]) - prog
                    if over > 0.01:
                        ov["duration_s"] = round(prog - float(ov["start"]), 2)
                # Keyframes past a shortened window would fail validation
                # and reject the whole write — trim them, same as the agent
                # tool does.
                for prop in ("x", "y"):
                    if isinstance(ov.get(prop), list):
                        ov[prop] = wschemas.clip_anim(ov[prop],
                                                      float(ov["duration_s"]))
                return edl, (f"overlay {ov['id']} now "
                             f"{ov['start']}-"
                             f"{round(float(ov['start']) + float(ov['duration_s']), 2)}s")
        return edl, "overlay already gone"

    if op == "remove_overlay":
        before = edl.get("overlays") or []
        hit = next((o for o in before if o.get("id") == args.get("id")), None)
        edl["overlays"] = [o for o in before
                           if o.get("id") != args.get("id")]
        if len(edl["overlays"]) == len(before):
            return edl, "overlay already gone"
        if hit and hit.get("screen"):
            # Deleting the pin alone would leave the clip it pushed into
            # cutting in cold, with the shot still zoomed into a screen —
            # so the handoff goes with it, exactly as remove_screen_takeover
            # does on the agent side.
            hand = round(float(hit["start"]) + float(hit["duration_s"]), 2)
            tl = wtimeline.Timeline(edl.get("keep") or [],
                                    edl.get("inserts") or [],
                                    edl.get("speed") or [])
            wins = wtimeline.insert_windows(edl.get("inserts") or [], tl)
            drop = next((i.get("id") for i in (edl.get("inserts") or [])
                         if i.get("asset_key") == hit.get("asset_key")
                         and wins.get(i.get("id"))
                         and abs(wins[i["id"]][0] - hand) < 0.06), None)
            if drop:
                edl["inserts"] = [i for i in (edl.get("inserts") or [])
                                  if i.get("id") != drop]
                return edl, ("removed the screen takeover and the clip it "
                             "pushed into")
        return edl, f"removed overlay {args.get('id')}"

    if op == "retime_text":
        prog = wschemas.program_duration(edl)
        for tx in (edl.get("texts") or []):
            if tx.get("id") == args.get("id"):
                if tx.get("behind"):
                    # Words behind the subject own a MEASURED mask of specific
                    # frames. Dragging the block in program time slides them off
                    # it, and the render would then cut the subject out of a
                    # different second of video — visible as the person being
                    # erased in the wrong place. Same policy as a screen
                    # takeover's pinned block (retime_overlay above): one rule,
                    # both surfaces.
                    raise ValueError(
                        "These words sit BEHIND the subject, measured from "
                        "these exact frames — so they can't be dragged to a "
                        "different moment. Ask me to move them and I'll "
                        "re-measure at the new spot, or delete them and add "
                        "them where you want.")
                length = float(tx["end"]) - float(tx["start"])
                if args.get("start") is not None:
                    tx["start"] = round(min(max(float(args["start"]), 0.0),
                                            max(0.0, prog - 0.3)), 2)
                    if args.get("end") is None:
                        tx["end"] = round(min(float(tx["start"]) + length,
                                              prog), 2)
                if args.get("end") is not None:
                    tx["end"] = round(min(max(float(args["end"]),
                                              float(tx["start"]) + 0.3),
                                          prog), 2)
                return edl, (f"text {tx['id']} now "
                             f"{tx['start']}-{tx['end']}s")
        return edl, "text already gone"

    if op == "remove_text":
        before = edl.get("texts") or []
        edl["texts"] = [t for t in before if t.get("id") != args.get("id")]
        if len(edl["texts"]) == len(before):
            return edl, "text already gone"
        return edl, f"removed text {args.get('id')}"

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
            # A DROPPED CLIP IS AS LONG AS THE CLIP (round 61).
            #
            # This used to clamp a duration-less drop to 10 seconds, on the
            # reasoning that dumping a 10-minute recording whole into a short
            # edit is never intended. That reasoning belongs to the AGENT's
            # insert_media (worker/agent_tools.py), which picks its own b-roll
            # lengths and has its own default — this op is only ever reached by
            # a human dragging a file onto their own timeline, and they chose
            # that file. On 2026-07-30 a 23.86s clip arrived as a 10.0s insert,
            # and getting the other 13.86s back took SIX separate drags of the
            # resize handle across two positions on the timeline.
            #
            # The ceiling that remains is set_insert_duration's, so the tool
            # and the chip agree on what an insert can be.
            if dur > _INSERT_MAX_S:
                dur = _INSERT_MAX_S
        at = float(args.get("at_output_s") or 0.0)
        inserts = list(edl.get("inserts") or [])
        # Speed-aware boundaries: a sped keep segment occupies its remapped
        # length, so snapping against raw source lengths would splice at
        # positions validate_edl (and the worker's own snapping) disagree on.
        bounds = wschemas.keep_boundaries(edl["keep"], edl.get("speed"))
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
                        _INSERT_MAX_S), 2)
                # Program-item re-anchoring happens in user_edl_write's
                # shared remap (same code the worker tools run).
                return edl, f"insert {i['id']} duration {i['duration_s']}s"
        return edl, "insert already gone"

    if op == "move_insert":
        inserts = list(edl.get("inserts") or [])
        target_ins = next((i for i in inserts
                           if i.get("id") == args.get("id")), None)
        if not target_ins:
            return edl, "insert already gone"
        at = float(args.get("at_output_s") or 0.0)
        # Speed-aware, same as insert_media above.
        bounds = wschemas.keep_boundaries(edl["keep"], edl.get("speed"))
        others = sorted((float(i["at_output_s"]), float(i["duration_s"]))
                        for i in inserts if i is not target_ins)
        final_of = {b: b + sum(d for a, d in others if a <= b + 1e-6)
                    for b in bounds}
        target = min(bounds, key=lambda b: abs(final_of[b] - at))
        # Round 75b: at_output_s alone cannot express "between those two
        # scenes". Inserts sharing a boundary play in LIST order, and this
        # op never reordered the list — on a timeline built from splits
        # (every scene an insert at ONE boundary, which is what splitting
        # produces), dragging a scene anywhere snapped it to the same
        # boundary and kept its old list position: the drag did literally
        # nothing. The item is reinserted at the position among its
        # boundary-mates whose window starts nearest the requested time.
        rest = [i for i in inserts if i is not target_ins]
        target_ins["at_output_s"] = target
        mates = [i for i in rest
                 if abs(float(i["at_output_s"]) - target) < 1e-6]
        blk = target + sum(d for a, d in others if a < target - 1e-6)
        starts, acc = [], blk
        for m in mates:
            starts.append(acc)
            acc += float(m["duration_s"])
        starts.append(acc)                    # the slot after the last mate
        j = min(range(len(starts)), key=lambda k: abs(starts[k] - at))
        if not mates:
            pos = len(rest)
        elif j < len(mates):
            pos = rest.index(mates[j])
        else:
            pos = rest.index(mates[-1]) + 1
        rest.insert(pos, target_ins)
        edl["inserts"] = rest
        return edl, (f"moved insert {target_ins['id']} to "
                     f"{round(starts[j], 2)}s")

    if op == "remove_insert":
        before = edl.get("inserts") or []
        edl["inserts"] = [i for i in before if i.get("id") != args.get("id")]
        if len(edl["inserts"]) == len(before):
            return edl, "insert already gone"
        return edl, f"removed insert {args.get('id')}"

    if op == "add_voiceover":
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        # kind 'audio' is the pipeline's extracted source-audio track — it
        # must never be layered back over itself.
        if not asset or asset["kind"] != "music":
            raise ValueError(_pick_audio_error(asset, "the voiceover"))
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
            raise ValueError(_pick_audio_error(asset, "the music"))
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
        # Context-aware defaults, mirroring worker add_music (round 36):
        # under speech the track is a -18dB smooth-ducked bed; with no
        # surviving speech it is the LEAD audio at -4dB with no duck — the
        # unconditional -18dB default made every timeline-added song on a
        # speechless video inaudible.
        speech = 0.0
        if speech_spans:
            tl = wtimeline.Timeline(edl.get("keep") or [],
                                    edl.get("inserts") or [],
                                    edl.get("speed") or [])
            for t0, t1 in speech_spans:
                for a, b in tl.span_to_out(t0, t1):
                    speech += max(0.0, min(b, end) - max(a, start))
        lead = speech < 1.0
        items.append({"id": f"mus{n}", "storage_key": asset["storage_key"],
                      "start": start, "end": end,
                      "gain_db": -4.0 if lead else -18.0,
                      "duck": not lead,
                      "duck_mode": None if lead else "smooth"})
        edl["music"] = items
        return edl, (f"music added {start}-{end}s (mus{n})"
                     + (" — lead audio, no speech under it" if lead else
                        " — ducked under the speech"))

    if op == "add_overlay":
        # Round 79 — a drop aimed at the B-ROLL lane. The timeline's file
        # drop used to route on file TYPE alone, so every video became a
        # spliced scene whether the user aimed at the video lane or not;
        # aiming at the b-roll lane now lays the media OVER the program (a
        # full-frame cutaway: the picture switches, the audio keeps playing)
        # instead of splicing it in and shifting everything after it.
        asset = assets_by_id.get(int(args.get("asset_id") or 0))
        if not asset or asset["kind"] not in ("video_clip", "image_ref"):
            raise ValueError("Pick an uploaded video clip or image for "
                             "b-roll.")
        prog = wschemas.program_duration(edl)
        start = round(min(max(float(args.get("start") or 0.0), 0.0),
                          max(0.0, prog - 0.3)), 2)
        kind = "video" if asset["kind"] == "video_clip" else "image"
        if args.get("duration_s") is not None:
            dur = float(args["duration_s"])
        elif kind == "image":
            dur = 4.0
        else:
            dur = float(asset.get("duration_s") or 4.0)
        dur = round(min(max(dur, 0.3), max(0.3, prog - start)), 2)
        items = list(edl.get("overlays") or [])
        taken = {o.get("id") for o in items}
        n = 1
        while f"ov{n}" in taken:
            n += 1
        items.append({"id": f"ov{n}", "kind": kind,
                      "asset_key": asset["storage_key"],
                      "start": start, "duration_s": dur, "fit": "cover"})
        edl["overlays"] = items
        return edl, (f"b-roll added {start}-{round(start + dur, 2)}s "
                     f"(ov{n}) — full-frame over the program, its audio "
                     f"keeps playing")

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


def _reanchor_after_op(old_j, new_edl, desc):
    """Everything that has to follow the program when a UI op changes its
    shape. Returns (new_edl, desc + disclosure).

    TWO passes, in this order, and the order matters — the second one builds a
    Timeline from the inserts the first one moved:

    1. SPLICED INSERTS follow their junction (timeline.resnap_inserts), the
       same call the worker's keep writes make. This route used to skip it
       entirely, on a comment saying "keep/speed are agent-only" that stopped
       being true the day the timeline learned to split, trim and delete. A
       project with one clip pinned at the LAST boundary (project 246 v19:
       ins1 at 33.57s of keep [[111.85, 130.08], [339.27, 354.61]]) could not
       have EITHER take deleted: the boundary the clip sat on stopped
       existing, validate_edl correctly refused the EDL, and the user got
       "at_output_s 33.57 is not on a keep-segment boundary" with nothing
       deleted and no click in the timeline that could get out of it.
    2. PROGRAM-TIME ITEMS re-anchor through the shared remap the worker tools
       run: content-anchored zooms/sfx/stylize follow their footage,
       program-anchored music/vo/overlays/texts clamp — otherwise removing an
       insert 400s over a stale zoom, or every sound after it drifts by the
       insert's length.
    """
    def _said(notes):
        return "; ".join(n[6:] if n.startswith("note: ") else n
                         for n in notes)

    if new_edl.get("keep") != old_j.get("keep") and new_edl.get("inserts"):
        new_edl["inserts"], notes = wtimeline.resnap_inserts(
            new_edl["inserts"], old_j.get("keep") or [],
            new_edl.get("keep") or [],
            old_j.get("speed") or [], new_edl.get("speed") or [])
        if notes:
            desc += " — " + _said(notes)
    if (new_edl.get("keep"), new_edl.get("inserts"), new_edl.get("speed")) != \
            (old_j.get("keep"), old_j.get("inserts"), old_j.get("speed")):
        old_tl = wtimeline.Timeline(old_j.get("keep") or [],
                                    old_j.get("inserts") or [],
                                    old_j.get("speed") or [])
        new_tl = wtimeline.Timeline(new_edl.get("keep") or [],
                                    new_edl.get("inserts") or [],
                                    new_edl.get("speed") or [])
        notes = wtimeline.remap_program_items(new_edl, old_tl, new_tl)
        if notes:
            desc += " — " + _said(notes)
    return new_edl, desc


@video_bp.route("/projects/<int:project_id>/edl", methods=["POST"])
@token_required
def user_edl_write(user_id, project_id):
    """User-authored EDL version from a UI action (frame selector, timeline
    insert/voiceover chips). Validates with the same schema the worker uses,
    appends a created_by='user' version and auto-renders a preview."""
    data = request.get_json() or {}
    op = str(data.get("op") or "")
    args = data.get("args") or {}
    # WHICH VERSION IS THE USER LOOKING AT?
    #
    # This used to be "the newest one, always". The studio lets you step back
    # through the edit history, and the timeline you can see and click is the
    # one you stepped back to — so a cut made there was applied to a completely
    # different keep list, and the studio then refused to display the result
    # (it only follows the newest version when nothing is pinned). The user
    # clicked Split and nothing happened; clicked again and got "that point is
    # already a clip edge". Both are the same bug wearing different clothes.
    #
    # An index or a program time is only meaningful against the EDL it was read
    # off. So the studio now names its base, and editing from an older state
    # branches by APPEND — the result becomes the new newest version, which is
    # what "go back and cut from here" means in every NLE.
    try:
        base_version = int(data["base_version"])
    except (KeyError, TypeError, ValueError):
        base_version = None
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
        latest_row = _latest_edl(cur, project_id)
        if not latest_row:
            cur.execute("""INSERT INTO edls (project_id, version, json,
                                             created_by)
                           VALUES (%s, 1, %s, 'user')""",
                        (project_id,
                         Json(wschemas.default_edl(original["duration_s"]))))
            latest_row = _latest_edl(cur, project_id)
        edl_row = latest_row
        if base_version is not None and base_version != latest_row["version"]:
            picked = _edl_at(cur, project_id, base_version)
            if not picked:
                return jsonify({"error": "That edit version no longer "
                                         "exists."}), 404
            edl_row = picked
        branched_from = (edl_row["version"]
                         if edl_row["version"] != latest_row["version"]
                         else None)

        cur.execute("""SELECT id, kind, storage_key, duration_s, meta
                       FROM assets WHERE project_id = %s""", (project_id,))
        assets_by_id = {a["id"]: a for a in cur.fetchall()}

        # Sentence spans feed add_music's context-aware defaults only —
        # loaded lazily so every other op skips the index read.
        speech_spans = None
        if op == "add_music":
            try:
                cur.execute("""SELECT json->'sentences' AS s FROM indexes
                               WHERE video_sha256 = %s""",
                            (original["sha256"],))
                row = cur.fetchone()
                speech_spans = [(float(x["t0"]), float(x["t1"]))
                                for x in ((row and row["s"]) or [])]
            except Exception:
                speech_spans = None

        try:
            new_edl, desc = _apply_edl_op(edl_row["json"], op, args,
                                          assets_by_id,
                                          src_dur=float(
                                              original["duration_s"]),
                                          speech_spans=speech_spans)
            new_edl, desc = _reanchor_after_op(edl_row["json"], new_edl, desc)
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

        # A QUEUED PREVIEW OF AN OLDER VERSION IS ALREADY WORTHLESS.
        #
        # Retire them before counting. Two things go wrong without this, and
        # one of them strands the user permanently: cutting twice in a few
        # seconds enqueued a preview per version, the user hit
        # MAX_CONCURRENT_JOBS_PER_USER on the LAST one, and the cap below
        # silently skipped it — the EDL committed, no render was ever
        # requested, and nothing re-enqueues. The studio then waits forever for
        # a preview of the current version. (Observed on project 229: v2/v3/v4
        # each got a job, v5 arrived 3s later at exactly 3 running jobs and got
        # none.) The other is pure waste: rendering v2 after v5 exists burns a
        # slot and real money on frames nobody will ever see.
        #
        # Marked 'done' rather than 'failed' — the state check allows no
        # 'cancelled', and a failure would land in the admin attention feed as
        # if something had broken.
        cur.execute("""UPDATE video_jobs
                          SET state = 'done', result = %s, updated_at = NOW()
                        WHERE project_id = %s AND type = 'preview'
                          AND state = 'queued'
                          AND (payload->>'edl_version')::int < %s""",
                    (Json({"superseded_by": version}), project_id, version))
        superseded = cur.rowcount

        # Enqueued UNCONDITIONALLY, and that is safe because of the sweep
        # above: a project can hold at most one queued preview at a time, so a
        # user hammering the timeline cannot pile up work. The cap still
        # governs everything expensive (agent turns, indexes, finals); what it
        # must never do is leave the CURRENT edit with no way to be seen.
        #
        # source='user_edit' lets the worker post a chat note if THIS preview
        # fails (agent-enqueued previews react inline instead).
        #
        # Unless an encode of this exact programme already exists (a split, an
        # undo/redo, a re-applied edit) — then it is adopted for the new version
        # and no render is asked for at all. The row is a pointer at the same
        # storage key, not a copy: no bytes move.
        # ...or DEFERRED, when the client says it can already show this edit
        # itself and will ask for the encode once the clicking stops — see
        # _preview_plan and _should_heal_preview for why and for what stops that
        # becoming "no render at all". The CLIENT decides, because the client is
        # what knows whether its draft engine can represent this EDL
        # (livePreview.unsupportedParts) and whether it still holds the proxy.
        # An older studio sends nothing and renders immediately, as before.
        twin = _preview_twin(cur, project_id, normalized,
                             exclude_version=version)
        plan = _preview_plan(twin, bool(data.get("defer_preview")))
        preview_job, reused = None, None
        if plan == "defer":
            pass
        elif plan == "adopt":
            reused = _adopt_preview(cur, project_id, twin, version)
        else:
            preview_job = _enqueue(cur, project_id, user_id, "preview",
                                   {"edl_version": version,
                                    "source": "user_edit"})
        cur.execute("""INSERT INTO chat_messages (session_id, role, content,
                                                  meta)
                       VALUES (%s, 'activity', %s, %s)""",
                    (p["chat_session_id"],
                     f"you → EDL v{version}: {desc}",
                     # edl_version is what lets the studio roll the chat back
                     # in step with the version stepper.
                     Json({"tool": "user_edit", "op": op,
                           "edl_version": version,
                           **({"branched_from": branched_from}
                              if branched_from is not None else {})})))

    return jsonify({"version": version, "preview_job_id": preview_job,
                    "reused_preview_asset_id": reused,
                    "preview_deferred": bool(preview_job is None
                                             and reused is None),
                    "branched_from": branched_from,
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
OUTRO_VERSION = 2      # v2: the site's white robot + premium wordmark card


def _final_is_current(meta):
    # Two ways a final is current, and BOTH are needed:
    #
    #   outro_v == OUTRO_VERSION — it carries the card we ship today.
    #   outro_v == 0             — the worker rendered deliberately WITHOUT a
    #                              card (OUTRO_DURATION_S=0, or an image built
    #                              without brand/endcard.png). There is no
    #                              newer card for it to be missing, so demanding
    #                              OUTRO_VERSION here would hide that final
    #                              forever while the worker's cache keeps
    #                              serving it — Download becomes a permanent
    #                              no-op with no error anywhere.
    #
    # This used to test PRESENCE alone, which got the card-less case right and
    # the version bump wrong: after OUTRO_VERSION went 1 -> 2 with a new card
    # design, every existing final still stamped 1 was reported as current, so
    # the studio presigned it and never posted /render/final. The worker's
    # cache busts on a stale stamp perfectly well — nothing ever ASKED it to.
    # Exports kept serving the old card, which is exactly what was reported.
    #
    # A stamp of some OTHER version means an OLD card, so it re-exports; the
    # worker then re-encodes and stamps the current one. Converges after
    # exactly one re-render, and renders predating the card (no key at all)
    # still re-export as before.
    v = (meta or {}).get("outro_v")
    return v == 0 or v == OUTRO_VERSION


# Mirrors worker/config.TRANSITION_VERSION — a worker test asserts they match.
TRANSITION_VERSION = 2


def _transitions_are_current(meta, edl_has_transition=True):
    """Was this final rendered with scene-scoped transitions?

    The backend half of worker/renderer.transitions_current, and it has to
    exist for the same reason the OUTRO_VERSION half does: the studio
    short-circuits to presigning an existing final_asset_id and never posts
    /render/final, so the worker's cache never gets ASKED to bust. A final
    rendered before round 48 has a junction effect on EVERY cut — 45 whip pans
    through one continuous shot, on a real customer's edit — and without this
    it stays downloadable forever.

    `edl_has_transition` is the half this shipped WITHOUT, and its absence took
    Download down for every user on the platform. The worker only ever busts an
    EDL that actually carries a transition; this side demanded the stamp on
    EVERY final, so the moment renders stopped carrying it (a render image
    older than round 48 — see _worker_confirmed_current) not one export in the
    product was reported as current. The rule has to be the SAME rule on both
    sides, so the caller passes what the worker reads off the EDL. Grandfathers
    exactly what the worker grandfathers, and nothing more.
    """
    if not edl_has_transition:
        return True
    return (meta or {}).get("trans_v") == TRANSITION_VERSION


def _versions_with_transition(cur, project_id):
    """EDL versions whose json carries a transition, as worker/renderer reads it.

    Evaluated in SQL rather than by pulling the EDLs: /state is polled every 2s
    per open studio and an EDL is a large document, so shipping 100 of them per
    poll to test one key would be the expensive way to ask a cheap question.
    The comparison mirrors Python truthiness on the shapes a transition can
    take — absent, null, or an empty object all mean "no transition".
    """
    cur.execute("""SELECT version,
                          COALESCE(json -> 'effects' -> 'transition',
                                   'null'::jsonb)
                          NOT IN ('null'::jsonb, 'false'::jsonb, '{}'::jsonb,
                                  '[]'::jsonb, '""'::jsonb, '0'::jsonb)
                            AS has_transition
                   FROM edls WHERE project_id = %s""", (project_id,))
    return {r["version"] for r in cur.fetchall() if r["has_transition"]}


# How long a pipeline probe is trusted, and how many recent renders it reads.
# /state is polled every 2s per open studio, so this has to collapse to about
# one query a minute per process rather than one per poll.
_PIPELINE_PROBE_TTL_S = 60
_PIPELINE_PROBE_SAMPLE = 25
_pipeline_probe = {}          # key -> (checked_at_monotonic, emits: bool)


def _pipeline_emits(cur, key):
    """Does the render service actually WRITE this stamp?

    A gate must never demand something the producer cannot make. These stamps
    are the backend's GUESS about what the render pipeline emits — but the
    backend is not the pipeline. Renders are produced by the remote executor,
    which is deployed by hand and can sit rounds behind the auto-deployed
    backend. When it does, the guess is wrong for every render ever made, and
    a gate built on it hides files that nothing can replace.

    That is not the theory, it is the measured behaviour: with the executor
    frozen at round 41, a user pressed Download, the pipeline built a brand new
    final at his request, and the gate hid it one second later for missing a
    stamp that build does not write. Re-rendering could only ever produce the
    same file again — which is exactly what the second press proved, and why it
    took two presses and two queues to get one download.

    So the stamp gates learn what the pipeline emits by looking at what it just
    emitted. If none of the last few renders carries the key, this deployment
    does not write it, and demanding it is demanding the impossible: the check
    disables itself until the executor is redeployed, then re-arms on its own
    the moment a render carries the stamp again. `any` over a sample rather
    than the single newest row, so a mid-rollout mix arms the gate rather than
    disarming it — the safe direction is the gate being ON.

    Key PRESENCE, never its value: `outro_v: 0` and `wm_v: 0` are meaningful
    values, so "does the pipeline write this at all" is the only question a
    probe can honestly answer. Whether the value is CURRENT stays with the
    per-stamp checks.
    """
    hit = _pipeline_probe.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _PIPELINE_PROBE_TTL_S:
        return hit[1]
    try:
        cur.execute("""SELECT bool_or(meta ? %s) AS emits FROM (
                           SELECT meta FROM assets WHERE kind = 'render'
                           ORDER BY id DESC LIMIT %s) t""",
                    (key, _PIPELINE_PROBE_SAMPLE))
        row = cur.fetchone()
        # No renders at all yet: nothing to contradict the gate, so leave it
        # armed. A fresh install must not start out with its checks disabled.
        emits = True if not row or row["emits"] is None else bool(row["emits"])
    except Exception as e:                                # pragma: no cover
        current_app.logger.warning("pipeline probe for %s failed: %s", key, e)
        return True                                       # fail toward the gate
    _pipeline_probe[key] = (now, emits)
    return emits


def _worker_confirmed_current(cur, project_id):
    """Render assets the WORKER has already declared current, after being asked.

    This is the loop-breaker, and it exists because the deadlock the
    _watermark_wanted docstring warns about has now shipped twice. The gates
    above are not truth — they are a REQUEST: hide a final so the studio posts
    /render/final and the worker's cache is finally asked to bust. When the two
    services disagree about a stamp there is no way out. The gate hides the
    file, the studio asks for a re-render, the worker answers `cached: true`
    with the very same asset, the gate hides it again. Download spins forever
    and no file is ever produced. Users press it ten, thirteen, seventeen times
    and leave.

    A `cached: true` result is the worker saying, with full sight of the EDL and
    its own pipeline stamps, "this file is current". At that point the backend's
    opinion is stale metadata, not a fact, and re-rendering cannot help because
    the worker has already declined to. So the user wins: serve the file they
    have. Worst case they download a render one pipeline revision old; the
    alternative on offer is nothing, forever.

    Deliberately keyed on `cached`, not merely on "a job produced this asset":
    a legitimately stale render still gets re-encoded fresh exactly once, which
    is what the gates are for. Only the SECOND identical answer is the loop.
    """
    cur.execute("""SELECT DISTINCT (result ->> 'render_asset_id')::int AS aid
                   FROM video_jobs
                   WHERE project_id = %s AND type = 'final' AND state = 'done'
                     AND result ->> 'cached' = 'true'
                     AND result ->> 'render_asset_id' ~ '^[0-9]+$'""",
                (project_id,))
    return {r["aid"] for r in cur.fetchall()}


# Mirrors worker/config.WATERMARK_VERSION — a worker test asserts they match.
# The free-tier mark is burned into FINAL renders only (see the worker's
# renderer.wants_watermark), so this is the backend half of the same rule.
WATERMARK_VERSION = 2


def _user_is_paid(cur, user_id):
    """Same rule as worker/db.user_is_paid, and deliberately the same bias:
    either signal counts as paid, because marking a paying customer's export
    is a broken promise while missing a mark on a free one costs nothing."""
    if not user_id:
        return False
    cur.execute("SELECT is_subscribed, plan FROM users WHERE id = %s",
                (user_id,))
    row = cur.fetchone()
    if not row:
        return False
    plan = ((row.get("plan") if isinstance(row, dict) else row["plan"])
            or "free").strip().lower()
    sub = row.get("is_subscribed") if isinstance(row, dict) \
        else row["is_subscribed"]
    return bool(sub) or plan not in ("", "free")


def _watermark_settings(cur):
    """The admin toggles, read the same way worker/db.video_settings reads them.

    to_regclass is checked FIRST rather than catching the error, because in
    postgres a failed statement poisons the whole transaction — and this runs
    inside /state, which is polled every 2s for every open studio.
    """
    default = {"enabled": True, "force": False}
    try:
        cur.execute("SELECT to_regclass('public.video_settings') AS t")
        row = cur.fetchone()
        if not row or not (row.get("t") if isinstance(row, dict) else row["t"]):
            return default
        cur.execute("SELECT watermark_enabled, watermark_force "
                    "FROM video_settings WHERE id = 1")
        row = cur.fetchone()
        if not row:
            return default
        return {"enabled": bool(row["watermark_enabled"]),
                "force": bool(row["watermark_force"])}
    except Exception:
        return default


def _watermark_wanted(is_paid, settings):
    """The wm_v a final rendered RIGHT NOW would carry — the backend half of
    worker/renderer.wants_watermark, and it has to stay the same rule.

    When these two disagree the studio deadlocks: this gate hides the render as
    stale, the worker re-renders and hands back the identical cached asset, the
    gate hides it again, and Download spins forever without ever producing a
    file. That is not hypothetical — it shipped. `force` was readable by the
    worker and invisible here, so every export by a PAID account with the admin
    Force switch on was hidden from its owner in an endless loop.
    """
    if not settings.get("enabled"):
        return 0
    if is_paid and not settings.get("force"):
        return 0
    return WATERMARK_VERSION


def _watermark_is_current(meta, is_paid, settings=None):
    """Does this cached final carry the mark this user should have NOW?

    The upgrade path is the reason this exists: a free user exports (wm_v=1),
    pays, hits Download again. Without this the studio presigns the cached
    marked file and they are still staring at the watermark they just paid to
    remove — the exact shape of the end-card bug, which sat in the layer
    ABOVE a worker cache that was busting correctly the whole time.

    Absent stamp means 0 (no mark): correct for a paid user, stale for a free
    one, so only free users re-encode their pre-feature exports.
    """
    want = _watermark_wanted(is_paid, settings or {"enabled": True,
                                                   "force": False})
    return ((meta or {}).get("wm_v") or 0) == want


def _final_gate(cur, project_id, user_id):
    """The one place that decides whether a rendered final is downloadable.

    Both /state and /edls answer this question, and they MUST answer it the
    same way — the studio arms its Download button off /state and fires it off
    the version list, so a disagreement is a button that is enabled and does
    nothing. They were two copies of the same three-way `and`; now they are one
    function, and a fourth stamp is added in one place.

    Both callers have already run _project_for_user, so `user_id` is the
    project's owner and the watermark rule reads the right plan.

    Returns a predicate over a render asset row. Every lookup it needs happens
    once, here — /state is polled every 2s per open studio, so this stays a
    handful of indexed reads per poll (the pipeline probe is cached across
    them) and never becomes a query per render row.
    """
    confirmed = _worker_confirmed_current(cur, project_id)
    is_paid = _user_is_paid(cur, user_id)
    wm = _watermark_settings(cur)
    # Only ask the EDLs which versions carry a transition when the answer can
    # still change something — with the stamp unwritten the check is off anyway.
    stamps_transitions = _pipeline_emits(cur, "trans_v")
    with_transition = (_versions_with_transition(cur, project_id)
                       if stamps_transitions else set())

    def ok(asset_id, meta, version):
        if asset_id in confirmed:
            return True           # the worker has already declined to re-render
        return (_final_is_current(meta)
                and _transitions_are_current(meta, version in with_transition)
                and _watermark_is_current(meta, is_paid, wm))
    return ok


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
        _gate = _final_gate(cur, project_id, user_id)

    by_version = {}
    for r in renders:
        m = r.get("meta") or {}
        v, variant = m.get("edl_version"), m.get("variant")
        try:                      # a malformed meta value must not 500 the list
            v = int(v)
        except (TypeError, ValueError):
            continue
        bv = by_version.setdefault(v, {})
        if variant == "final" and not _gate(r["id"], m, v):
            continue          # stale card, transitions or watermark: re-export
        # Keep the NEWEST asset id per (version, variant): a version can be
        # re-rendered, and the version list must point at the latest encode.
        if r["id"] > (bv.get(variant) or {}).get("id", 0):
            bv[variant] = {"id": r["id"],
                           "object_id": m.get("reused_from_asset_id")
                           or r["id"]}

    def _pick(v, variant, field="id"):
        return (by_version.get(v, {}).get(variant) or {}).get(field)

    return jsonify({"edls": [
        {"version": v["version"], "created_by": v["created_by"],
         "created_at": v["created_at"].isoformat(),
         "preview_asset_id": _pick(v["version"], "preview"),
         "preview_object_id": _pick(v["version"], "preview", "object_id"),
         "final_asset_id": _pick(v["version"], "final")}
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
        # THE EXPORT IS THE ONE THING THAT GENUINELY NEEDS THE ORIGINAL.
        # Everything before it runs on the proxy, which is why a proxy-first
        # upload can start editing within seconds. Finals render from the
        # source file at full resolution, so if the background upload has not
        # landed yet, say exactly that and how far along it is — a bare "not
        # ready" on a video the user can see and has already edited reads as a
        # bug rather than as a transfer still in flight.
        original = _active_original(cur, project_id)
        meta = (original or {}).get("meta") or {}
        if meta.get("upload_state") == "pending":
            pct = int(round(float(meta.get("upload_progress") or 0) * 100))
            return jsonify({
                "error": "Your original video is still uploading in the "
                         f"background ({pct}% done). Exports render from the "
                         "full-resolution file, so this needs to finish "
                         "first — your edit is saved and nothing is lost.",
                "code": "original_uploading",
                "upload_progress": pct}), 409
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
        cur.execute("""SELECT version, json FROM edls
                       WHERE project_id = %s AND version = %s""",
                    (project_id, version))
        want = cur.fetchone()
        if not want:
            return jsonify({"error": "That EDL version does not exist"}), 400
        # STEPPING BACK THROUGH THE HISTORY MUST NOT COST AN ENCODE.
        #
        # The studio renders on demand when a version has no preview of its
        # own, which is every version created by a split — and a split renders
        # the same picture as its parent. force=true is exempt: its entire
        # purpose is fresh bytes for a render this browser will not play.
        if not force:
            twin = _preview_twin(cur, project_id, want["json"],
                                 exclude_version=version)
            if twin is not None:
                cur.execute("""SELECT storage_key, bytes, duration_s, width,
                                      height, fps, meta
                               FROM assets WHERE id = %s""", (twin,))
                src = cur.fetchone()
                meta = dict(src["meta"] or {})
                meta.update({"edl_version": version, "variant": "preview",
                             "reused_from_asset_id":
                                 meta.get("reused_from_asset_id") or twin})
                cur.execute("""INSERT INTO assets (project_id, kind,
                                   storage_key, bytes, duration_s, width,
                                   height, fps, meta)
                               VALUES (%s,'render',%s,%s,%s,%s,%s,%s,%s)
                               RETURNING id""",
                            (project_id, src["storage_key"], src["bytes"],
                             src["duration_s"], src["width"], src["height"],
                             src["fps"], Json(meta)))
                return jsonify({"job_id": None,
                                "reused_preview_asset_id":
                                    cur.fetchone()["id"]})
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
#
# TWO FAMILIES, and the second one is why the first was not enough:
#   player_*  — playback failures (the round-33 MediaError code-4 saga).
#   upload_*  — a file that never became an asset. 201 of 203 index jobs have
#               ever succeeded, so the drop-off between "signed up" and "has a
#               video" was NEVER server-side; it happened in the browser, where
#               nothing was recorded. A client-side size/duration rejection
#               fires before a project even exists, so those users were
#               indistinguishable from someone who signed up and walked away —
#               65 of 214 accounts sit in that bucket.
CLIENT_EVENT_KINDS = {"player_error", "player_error_probe",
                      "player_recovered", "attach_failed",
                      "upload_started", "upload_rejected", "upload_failed",
                      # Proxy-first upload: the browser built a 540p proxy and
                      # sent that first. Recorded as its own kind so the split
                      # between the fast path and the legacy whole-file path is
                      # countable — without it, "did the browser transcode
                      # actually work for real users" has no answer.
                      "upload_proxy_first", "upload_proxy_failed",
                      "upload_original_ready",
                      # The direct PUT to storage died in the browser and the
                      # bytes came through our own servers instead (round 61).
                      # A symptom, not a feature: if this starts carrying real
                      # traffic the direct path is broken for a population, and
                      # this count is how that becomes visible.
                      "upload_relayed",
                      # Mid-transfer, the measured link was so slow the studio
                      # switched to building + sending a 540p proxy instead of
                      # making the user wait out the original (round 70 — a
                      # 45 MB upload at 48 KB/s took 15.5 minutes and the user
                      # left without typing a word). Carries the measured
                      # bytes/s and the projection that triggered the switch.
                      "upload_slow_rescue",
                      # One row per finished main-video transfer: elapsed, bps,
                      # mode, retry count. The denominator's other half —
                      # upload_started says what was attempted, this says what
                      # the link actually delivered.
                      "upload_transfer"}

# The kinds that mean "a user tried to give us a video and we did not take it".
# Surfaced in admin on their own rather than mixed into the rest, because these
# are the ones nobody goes looking for.
UPLOAD_FAILURE_KINDS = ("upload_rejected", "upload_failed")


def _clean_event_detail(detail):
    """Cap what a client can write: this is user-controlled input landing in a
    table an admin will read. Scalars only, bounded count and length. Numbers
    are range-checked rather than trusted — JSON has no integer bound, so a
    bare `{"n": <4000-digit number>}` passed an isinstance(int) check and
    landed in the row at full length, sailing past the 300-char cap that
    exists precisely to stop that.
    """
    if not isinstance(detail, dict):
        return {}
    clean = {}
    for k, v in list(detail.items())[:20]:
        key = str(k)[:40]
        if v is None or isinstance(v, bool):
            clean[key] = v
        elif isinstance(v, (int, float)):
            clean[key] = v if -1e15 < v < 1e15 else str(v)[:300]
        else:
            clean[key] = str(v)[:300]
    return clean


def record_client_event(user_id, project_id, kind, asset_id=None, detail=None,
                        origin="client"):
    """Write one forensics row. NEVER raises and never blocks a caller.

    Shared by the two beacon routes and by the server's OWN upload rejections,
    so a failure is recorded whether or not the browser cooperates — a user who
    closes the tab the moment their 4 GB file is refused still leaves a trace.
    `project_id` may be None: the client-side caps fire before ensureProject.
    """
    if kind not in CLIENT_EVENT_KINDS:
        return False
    clean = _clean_event_detail(detail)
    clean["origin"] = origin
    try:
        with vdb() as conn:
            cur = conn.cursor()
            if project_id is not None and \
                    not _project_for_user(cur, project_id, user_id):
                return False
            # Telemetry must never become a write amplifier: these endpoints
            # are cheap to call in a loop from a page the user controls.
            cur.execute("""SELECT COUNT(*) AS n FROM client_events
                           WHERE user_id = %s
                             AND created_at > NOW() - INTERVAL '1 hour'""",
                        (int(user_id),))
            if (cur.fetchone() or {}).get("n", 0) >= MAX_CLIENT_EVENTS_PER_HOUR:
                return False
            # An asset_id from the client is a claim, not a fact. Storing an
            # unverified one lets a forensics row point at another tenant's
            # asset — and this table exists to be TRUSTED during an incident.
            if asset_id is not None and project_id is not None:
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
            elif asset_id is not None:
                clean["asset_id_unverified"] = asset_id
                asset_id = None
            cur.execute("""INSERT INTO client_events
                               (user_id, project_id, kind, asset_id, detail)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (int(user_id), project_id, kind, asset_id,
                         json.dumps(clean)))
    except Exception as exc:      # never let telemetry break the studio
        print(f"[client_event] dropped ({kind}): {exc}", flush=True)
        return False
    return True


def _event_request_args(data):
    kind = str(data.get("kind") or "")[:40]
    try:
        asset_id = int(data.get("asset_id"))
    except (TypeError, ValueError):
        asset_id = None
    return kind, asset_id, data.get("detail")


@video_bp.route("/projects/<int:project_id>/client-event", methods=["POST"])
@token_required
def client_event(user_id, project_id):
    kind, asset_id, detail = _event_request_args(request.get_json(silent=True)
                                                 or {})
    if kind not in CLIENT_EVENT_KINDS:
        return jsonify({"ok": True, "ignored": True})
    stored = record_client_event(user_id, project_id, kind, asset_id, detail)
    return jsonify({"ok": True, "stored": stored})


@video_bp.route("/client-event", methods=["POST"])
@token_required
def client_event_no_project(user_id):
    """The same beacon for failures that happen BEFORE a project exists.

    The studio validates size, type and duration before it calls
    ensureProject — deliberately, so a refused file never strands an empty
    project. That ordering is also why the most important failure we have was
    the one we could not record: there was no project to hang it on.
    """
    kind, _asset_id, detail = _event_request_args(request.get_json(silent=True)
                                                  or {})
    if kind not in CLIENT_EVENT_KINDS:
        return jsonify({"ok": True, "ignored": True})
    stored = record_client_event(user_id, None, kind, None, detail)
    return jsonify({"ok": True, "stored": stored})


# ------------------------------------------------------------------ #
#  Assets                                                              #
# ------------------------------------------------------------------ #

# Mirrors worker/filmstrip.TIMELINE_MEDIA_VERSION — a worker test asserts they
# match. Unlike OUTRO_VERSION this gate never WITHHOLDS anything: a strip built
# by an older worker keeps serving its frames while the richer one is built.
# That is the round-53 lesson applied before it could bite again — a version
# gate that hides finished work until some other service catches up is how
# Download became a platform-wide no-op.
TIMELINE_MEDIA_VERSION = 2

# Builds this route will ever run for ONE asset set. Any gate that asks for a
# rebuild must be able to give up: if a worker somehow never writes the stamp,
# the alternative is a job enqueued on every project open forever. Counting per
# SIG rather than per project is what keeps that safety valve from also
# blocking the legitimate case — a user who adds a tenth clip has a genuinely
# new asset set and gets a genuinely new build, however many came before.
#
# ROUND 61 — and the budget is per (asset set, BUILDER VERSION).
#
# It used to be per asset set alone, which quietly made a builder bug
# permanent. Project 246's asset set spent both of its builds on a worker that
# OOM-killed itself decoding a 4K clip; after the fix shipped, the two corpses
# would still have said "already tried twice" and that timeline would have
# stayed grey forever. Two failures are evidence about the CODE that failed,
# not about the footage — so a new TIMELINE_MEDIA_VERSION is a new budget, and
# bumping it is how a fix to this job reaches the projects it was written for.
MAX_FILMSTRIP_BUILDS_PER_SIG = 2

# Asset kinds that can appear as a block on the timeline.
_TIMELINE_ASSET_KINDS = ("video_clip", "image_ref", "music", "audio")


def _timeline_media_sig(cur, project_id):
    """A fingerprint of everything the timeline could need artwork for.

    Computed HERE and handed to the worker, which echoes it back untouched —
    so the value the gate compares against is the value the gate itself
    produced. A worker that recomputed it could disagree by one asset forever,
    and each disagreement would be a rebuild.
    """
    cur.execute("""SELECT storage_key FROM assets
                   WHERE project_id = %s AND kind = ANY(%s)
                   ORDER BY storage_key""",
                (project_id, list(_TIMELINE_ASSET_KINDS)))
    keys = [r["storage_key"] for r in cur.fetchall() if r.get("storage_key")]
    # Bundled library tracks are not assets; the only record that one is on
    # this timeline is the live EDL.
    cur.execute("""SELECT COALESCE(json -> 'music', '[]'::jsonb) AS m
                   FROM edls WHERE project_id = %s
                   ORDER BY version DESC LIMIT 1""", (project_id,))
    row = cur.fetchone()
    for item in (row and row["m"]) or []:
        k = (item or {}).get("storage_key")
        if k:
            keys.append(k)
    import hashlib
    return hashlib.sha1("\n".join(sorted(set(keys)))
                        .encode("utf-8")).hexdigest()[:16]


def _presigned_timeline_media(res):
    """Turn a filmstrip job result into what the studio draws from.

    Sheets become URLs; waveforms are already inline (see worker/filmstrip.py
    — an envelope is smaller than the presigned URL that would fetch it).
    """
    out = dict(res)
    key = out.pop("key", None)
    out.pop("sig", None)
    out["url"] = storage.presign_get(key) if key else None
    assets = {}
    for ref, a in (out.get("assets") or {}).items():
        a = dict(a)
        akey = a.pop("key", None)
        if akey:
            a["url"] = storage.presign_get(akey)
        assets[ref] = a
    out["assets"] = assets
    return out


@video_bp.route("/projects/<int:project_id>/filmstrip", methods=["GET"])
@token_required
def filmstrip(user_id, project_id):
    """Everything the studio timeline draws itself from: the sprite sheet of
    frames, the audio envelopes, and the per-asset artwork.

    Poll-shaped on purpose, and the shape is the whole design: the FIRST call
    for a video enqueues a worker job and answers `building`; every call after
    it answers `ready` from a finished job row, with no work done at all. The
    studio asks on every project open, so the common path has to be a single
    indexed SELECT.

    Every failure mode answers 200 with available=false and a reason. This is
    decoration: the timeline it decorates has drawn perfectly good blocks since
    round 5, and a 500 here would take the whole panel down (its
    SectionBoundary catches a crash, not a rejected fetch) over thumbnails.
    That includes the case where migration 011 has not been applied yet — the
    enqueue raises, and the answer is "not available", not an error page.
    """
    if not storage.is_configured():
        return jsonify({"available": False,
                        "reason": "storage is not configured"})
    with vdb() as conn:
        cur = conn.cursor()
        if not _project_for_user(cur, project_id, user_id):
            return jsonify({"error": "Project not found"}), 404
        try:
            want_sig = _timeline_media_sig(cur, project_id)
        except Exception:
            want_sig = None
        cur.execute("""SELECT result FROM video_jobs
                       WHERE project_id = %s AND type = 'filmstrip'
                         AND state = 'done'
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        done = cur.fetchone()
        payload = None
        if done and (done.get("result") or {}).get("available"):
            res = dict(done["result"])
            # A replaced upload keeps the project and its EDL but is a
            # different video, so a strip built from the old one would draw the
            # WRONG frames under the user's cuts. The key carries the source
            # sha; when it no longer matches, fall through and rebuild.
            cur.execute("""SELECT sha256, duration_s FROM assets
                           WHERE project_id = %s AND kind = 'original'
                           ORDER BY id DESC LIMIT 1""", (project_id,))
            src = cur.fetchone() or {}
            stale = bool(src.get("sha256")) and \
                (src["sha256"] or "")[:16] not in (res.get("key") or "")
            if res.get("key") and not stale:
                payload = {"available": True, "state": "ready",
                           "expires_in": storage.PRESIGN_GET_EXPIRY,
                           **_presigned_timeline_media(res)}
                # Fresh enough to draw, but built before the waveforms existed
                # or before the newest asset landed? Ask for ONE rebuild and
                # serve what we have in the same breath.
                fresh = (res.get("tm_v") == TIMELINE_MEDIA_VERSION
                         and (want_sig is None or res.get("sig") == want_sig))
                if fresh:
                    return jsonify(payload)

        # same_sig counts only this BUILDER's attempts at this asset set. A job
        # from before the current TIMELINE_MEDIA_VERSION carries a different
        # stamp (or none at all) and does not spend the budget — see the comment
        # on MAX_FILMSTRIP_BUILDS_PER_SIG.
        cur.execute("""SELECT COUNT(*) FILTER (
                                  WHERE state IN ('queued','running')) AS live,
                              COUNT(*) FILTER (
                                  WHERE payload ->> 'sig'
                                        IS NOT DISTINCT FROM %s
                                    AND payload ->> 'tm_v' = %s) AS same_sig
                       FROM video_jobs
                       WHERE project_id = %s AND type = 'filmstrip'""",
                    (want_sig, str(TIMELINE_MEDIA_VERSION), project_id))
        counts = cur.fetchone() or {"live": 0, "same_sig": 0}
        if counts["live"]:
            return jsonify(dict(payload, rebuilding=True) if payload
                           else {"available": False, "state": "building"})
        if counts["same_sig"] >= MAX_FILMSTRIP_BUILDS_PER_SIG:
            # Out of budget for this exact asset set. Serving the older
            # artwork beats serving nothing, and beats asking forever.
            return jsonify(payload or {"available": False,
                                       "state": "unavailable"})
        cur.execute("""SELECT id FROM assets
                       WHERE project_id = %s AND kind IN ('proxy','original')
                       LIMIT 1""", (project_id,))
        if not cur.fetchone():
            return jsonify(payload or {"available": False,
                                       "state": "no_video"})
        try:
            cur.execute("""INSERT INTO video_jobs
                             (project_id, user_id, type, state, payload)
                           VALUES (%s, %s, 'filmstrip', 'queued', %s)
                           RETURNING id""",
                        (project_id, int(user_id),
                         Json({"sig": want_sig,
                               "tm_v": str(TIMELINE_MEDIA_VERSION)})))
            cur.fetchone()
            conn.commit()
        except Exception as e:
            conn.rollback()
            current_app.logger.warning(
                "filmstrip enqueue failed for project %s: %s "
                "(is migration 011 applied?)", project_id, e)
            return jsonify(payload or {"available": False,
                                       "state": "unavailable"})
    # `rebuilding` keeps the studio polling on a project that ALREADY has
    # artwork: without it the client stops the moment available=true and the
    # richer artwork it just asked for would not appear until the next open.
    return jsonify(dict(payload, rebuilding=True) if payload
                   else {"available": False, "state": "building"})


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
