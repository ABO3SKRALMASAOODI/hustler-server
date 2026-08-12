"""Shorts mode — job type "shorts_plan" (round 99).

One job turns a long indexed video into N finished vertical shorts. Each
short is a CHILD PROJECT (projects.parent_project_id) that shares the
parent's original + proxy BY STORAGE KEY — prefix-based storage deletion
makes that safe (a child's delete only wipes objects under the CHILD's
prefix) — and reuses the sha-keyed index row outright, so spawning a child
costs four INSERTs and zero media work. The child then gets a real EDL
seeded through the SAME tool functions the agent runs (keep_segments →
auto_reframe → captions → punch-ins → optional music/grade), and a `final`
render fanned out to the executor. Because a child is a full project, "open
it and refine it in chat" needs no new machinery at all — that is the whole
point of this shape.

The optional REFERENCE clip: the user drops a short they like (uploaded as a
video_clip with meta.role='shorts_reference'). It gets the normal clip index
(transcript + tiles + audio perception), and _reference_profile turns that
into a style profile — measured cut cadence and BPM/energy from the ears,
captions/effects/grade read off the tiles by the vision model — which then
steers both the plan (clip length, tone) and the per-child seeding (caption
preset, punch strength, grade, music).

BILLING: every model call here is recorded to llm_calls under THIS job (same
recorder pattern as mcp_exec), so main.py can charge it exactly like an agent
turn — model cost through the standard margin — plus a flat
config.SHORTS_CLIP_CREDITS per finished clip for the render compute.
The gate mirrors the message path: no credits → no run, and the wall is a
chat message the studio already knows how to draw.
"""

import json
import math
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import agent_tools
import config
import db as dbx
import llm
import storage
from psycopg2.extras import Json
from schemas import GRADE_PRESETS

CLIP_MIN_S = 10.0          # below this a "short" is a jump cut, not a clip
CLIP_MAX_S = 75.0          # above this it stops being a short at all
REF_WAIT_S = 180           # how long to wait for the reference's own index
CAPTION_PRESETS = ("classic", "clean", "documentary", "broadcast", "retro",
                   "neon", "podcast", "beast", "karaoke", "elegant",
                   "spotlight", "stacked", "impact", "lyric")


# ------------------------------------------------------------------ helpers
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _default_count(duration_s):
    """~1 clip per 5 minutes, always at least 1, capped in config."""
    return max(1, min(config.SHORTS_MAX_CLIPS,
                      int(round(duration_s / 300.0)) or 1))


def _save_shorts_meta(conn, project_id, shorts_meta):
    with conn.cursor() as cur:
        cur.execute("""UPDATE projects
                       SET meta = COALESCE(meta, '{}'::jsonb)
                                  || jsonb_build_object('shorts', %s::jsonb)
                       WHERE id = %s""",
                    (json.dumps(shorts_meta), project_id))


def _find_reference(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT * FROM assets
                       WHERE project_id = %s AND kind = 'video_clip'
                         AND meta->>'role' = 'shorts_reference'
                       ORDER BY id DESC LIMIT 1""", (project_id,))
        return cur.fetchone()


def _json_from(text):
    """Parse the model's JSON, tolerating code fences and leading prose."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _ask_json(worker_db, job, subscribed, plan, system, user, purpose,
              max_tokens=3000):
    """One JSON-answering call on the plan-appropriate agent model, recorded
    to llm_calls under this job (the recorder is set by run_shorts_plan).
    Returns a dict or None."""
    client, model = llm.agent_client_for(subscribed, plan)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for attempt in (1, 2):
        try:
            resp = llm.create_with_dialect(client, model, messages,
                                           max_tokens=max_tokens,
                                           temperature=0.3)
        except Exception as e:
            llm.record(purpose, {"model": model, "system": system[:2000],
                                 "user_chars": len(user)},
                       {"error": str(e)[:300]}, None)
            return None
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        llm.record(purpose, {"model": model, "system": system[:2000],
                             "user_chars": len(user)},
                   {"text": text[:4000]}, usage)
        out = _json_from(text)
        if out is not None:
            return out
        if attempt == 1:
            messages.append({"role": "assistant", "content": text[:2000]})
            messages.append({"role": "user", "content":
                             "That was not parseable JSON. Reply with the "
                             "JSON object only — no prose, no code fences."})
    return None


# ------------------------------------------------------------------ reference
_VISION_STYLE_PROMPT = """These are evenly spaced frames from a REFERENCE short video a user wants their own clips styled after. Read the frames and answer in STRICT JSON only:
{"captions": {"present": bool, "uppercase": bool, "style_words": "3-8 words describing the caption look (font weight, color, outline, position, one-word-at-a-time vs phrases)"},
 "effects": "3-10 words naming visible effects (punch zooms, shake, flashes, split screen, b-roll cutaways, emoji/stickers, none)",
 "grade_words": "2-6 words describing the color grade (e.g. warm orange, desaturated film, high contrast teal, black and white, natural)",
 "energy": "calm" | "medium" | "hype",
 "notes": "one sentence: the overall editing style in plain words"}"""


def _pick_caption_preset(vis):
    """Map the vision model's description of the reference captions onto the
    caption presets the renderer actually has."""
    words = ((vis or {}).get("captions") or {}).get("style_words") or ""
    words = (words + " " + str((vis or {}).get("notes") or "")).lower()
    if any(k in words for k in ("script", "italic serif", "cursive",
                                "lyric", "handwritten")):
        return "lyric"
    if any(k in words for k in ("neon", "glow", "cyber", "electric")):
        return "neon"
    if any(k in words for k in ("news", "broadcast", "lower third")):
        return "broadcast"
    if any(k in words for k in ("retro", "vintage", "outlined", "poster")):
        return "retro"
    # A reference NAMED as karaoke wins over the generic one-word phrases:
    # karaoke is a visible phrase whose SPOKEN word lights up, and vision
    # often describes it as "word-by-word karaoke".
    if "karaoke" in words:
        return "karaoke"
    # One word OWNING the frame, no phrase around it — 'spotlight'.
    if any(k in words for k in ("one word", "word-by-word",
                                "word at a time", "single word")):
        return "spotlight"
    if any(k in words for k in ("stacked", "interlock", "different size",
                                "mixed size", "huge word")):
        return "stacked"
    if any(k in words for k in ("bold", "heavy", "thick", "yellow", "hype",
                                "beast", "impact")):
        return "beast"
    if any(k in words for k in ("serif", "elegant", "thin",
                                "classy")):
        return "elegant"
    if any(k in words for k in ("subtitle", "closed caption", "boxed",
                                "documentary")):
        return "documentary"
    # The safe fallback is coherent white typography, not a moving yellow
    # box. Reference analysis is uncertain by definition; default restraint.
    return "clean"


def _pick_grade(vis):
    words = (str((vis or {}).get("grade_words") or "")).lower()
    if not words:
        return None
    if "black and white" in words or "b&w" in words or "monochrome" in words:
        return "bw"
    if any(k in words for k in ("teal", "cinematic", "film noir")):
        return "cinematic"
    if any(k in words for k in ("warm", "orange", "golden")):
        return "warm"
    if any(k in words for k in ("cool", "blue", "cold")):
        return "cool"
    if any(k in words for k in ("vintage", "retro", "faded", "film")):
        return "vintage"
    if any(k in words for k in ("vibrant", "saturated", "punchy",
                                "high contrast")):
        return "vibrant"
    return None    # "natural" and everything unrecognized: leave it alone


def _reference_profile(worker_db, job, ref, workdir):
    """Style profile measured from the reference clip, or a stub when its
    index never lands. Never raises — a broken reference degrades to default
    styling, it must not kill the run."""
    deadline = time.time() + REF_WAIT_S
    idx_row, sha = None, None
    while time.time() < deadline:
        row = worker_db.run(dbx.get_asset, ref["id"])
        if not row:
            return None
        sha = row.get("sha256")
        if sha:
            idx_row = worker_db.run(dbx.get_index_by_sha, sha)
            if idx_row and (row.get("meta") or {}).get("indexed"):
                break
        time.sleep(5)
    profile = {"source": "reference", "analyzed": False,
               "reference_asset_id": ref["id"],
               "filename": (ref.get("meta") or {}).get("filename")}
    if not idx_row:
        profile["note"] = ("reference clip was still analyzing — styled "
                          "with defaults")
        return profile
    ridx = idx_row["json"]
    rdur = float(((ridx.get("video") or {}).get("duration")) or 0.0)
    if rdur <= 0:
        return profile
    shots = ridx.get("shots") or []
    words = [w for w in (ridx.get("words") or []) if not w.get("filler")]
    perception = ridx.get("perception") or {}
    speech_s = sum(max(0.0, float(w["t1"]) - float(w["t0"])) for w in words)
    speech_ratio = min(1.0, speech_s / rdur)
    bpm = perception.get("bpm")
    bpm_conf = float(perception.get("bpm_conf") or 0.0)
    profile.update({
        "analyzed": True,
        "duration_s": round(rdur, 1),
        "cuts_per_min": round(len(shots) / rdur * 60.0, 1) if shots else 0.0,
        "avg_shot_s": round(rdur / max(1, len(shots)), 1),
        "speech_ratio": round(speech_ratio, 2),
        "music": {
            "bpm": bpm,
            # Ears: a confident tempo under sparse speech is a music-driven
            # short; wall-to-wall talking with a weak tempo is not.
            "prominent": bool(bpm and bpm_conf >= 0.45
                              and speech_ratio < 0.65),
        },
    })

    # Eyes: the clip index already carries the filmstrip tiles — read the
    # style off those instead of decoding the video again.
    tile_keys = ridx.get("tile_keys") or []
    frames = []
    if tile_keys:
        picks = [tile_keys[int(i * (len(tile_keys) - 1) / 5)]
                 for i in range(6)] if len(tile_keys) > 1 else tile_keys
        seen = set()
        for k in picks:
            if k in seen:
                continue
            seen.add(k)
            local = os.path.join(workdir, f"ref_{len(frames)}.jpg")
            try:
                storage.download_to(k, local)
                frames.append(local)
            except Exception:
                continue
    vis = None
    if frames:
        answer = llm.ask_vision(_VISION_STYLE_PROMPT, frames,
                                max_tokens=800, purpose="shorts_reference",
                                image_names=[f"ref_tile_{i}" for i
                                             in range(len(frames))],
                                reasoning_effort="low")
        vis = _json_from(answer) if answer else None
        if answer and vis is None:
            profile["vision_notes"] = answer[:400]
    if vis:
        profile["vision"] = vis
        profile["vision_notes"] = str(vis.get("notes") or "")[:300]
    captions = (vis or {}).get("captions") or {}
    energy = str((vis or {}).get("energy") or "").lower()
    if not energy:
        energy = ("hype" if profile["cuts_per_min"] >= 25
                  else "calm" if profile["cuts_per_min"] <= 6 else "medium")
    profile.update({
        "energy": energy,
        "captions": captions.get("present", True),
        "captions_preset": _pick_caption_preset(vis),
        "uppercase": bool(captions.get("uppercase")),
        "grade": _pick_grade(vis),
        "punch_strength": 0.18 if energy == "hype" else 0.12,
        "effects_notes": str((vis or {}).get("effects") or "")[:200],
    })
    if profile["music"]["prominent"]:
        tempo = ""
        if bpm:
            tempo = ("fast " if bpm >= 120 else "mid-tempo "
                     if bpm >= 90 else "slow ")
        mood = {"hype": "energetic phonk beat", "calm": "lofi chill beat",
                "medium": "upbeat groove"}.get(energy, "upbeat groove")
        profile["music"]["query"] = tempo + mood
    return profile


# ------------------------------------------------------------------ the plan
_PLAN_SYSTEM = """You are Valmera's shorts producer. You are given the timed sentence transcript of a long video. Choose complete story/conversation arcs that work as standalone vertical shorts: a setup or question, development/answer, and a resolution or punchline. Self-contained does NOT mean extracting an isolated quotable answer. In interviews/podcasts, a question that makes the answer intelligible belongs in the clip even when the answer contains the flashier hook; preserve the question-and-answer exchange and speaker turn.

Reply with STRICT JSON only:
{"clips": [{"start": <seconds>, "end": <seconds>, "title": "<hook-style title, max 55 chars, no quotes inside>", "hook": "<the first spoken words of the clip, verbatim>", "score": <0-100 how likely to hold attention>, "music": <true|false would background music help this clip>}]}

Rules: start and end MUST be sentence boundaries taken from the transcript timestamps. Never start mid-sentence. Prefer clips that contain their own necessary context. For multi-speaker material, never open on an answer whose preceding nearby question/setup is required to understand it; include the question. Do not cut away before the answer resolves. Do not overlap clips. Titles are written like social hooks (curiosity, stakes, numbers), never clickbait lies about the content."""

_VISUAL_PLAN_SYSTEM = """You are Valmera's shorts producer reviewing labeled filmstrip sheets from a video with little or no usable speech. Select visually self-contained highlight windows for vertical shorts. Read the timestamps printed under every frame. Prefer clear action, reactions, reveals, skill, movement, before/after changes, wins, near misses, or visually coherent sequences; avoid loading screens, menus, static dead time, repeated moments, and windows whose subject is too small to understand.

Reply with STRICT JSON only:
{"clips": [{"start": <seconds>, "end": <seconds>, "title": "<truthful hook title, max 55 chars>", "hook": "<short description of the opening visual>", "score": <0-100>, "music": <true|false>}]}

The sheets sample the full video rather than every frame. Use their timestamps to choose an approximate continuous window around each visible highlight. Do not overlap clips and do not invent events that are not visible."""


def _transcript_block(index, max_chars=180000):
    lines = []
    for s in index.get("sentences") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        spk = s.get("speaker")
        tag = f" S{spk}" if spk is not None else ""
        lines.append(f"[{float(s['t0']):.1f}-{float(s['t1']):.1f}]{tag} "
                     f"{text}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n[transcript truncated]"
    return block


def _validated_clips(raw, duration, want, index=None, visual=False):
    """Normalize one transcript or visual planner answer."""
    starts = [float(s.get("start")) for s in (index or {}).get("shots") or []
              if s.get("start") is not None]
    ends = [float(s.get("end")) for s in (index or {}).get("shots") or []
            if s.get("end") is not None]
    clips = []
    for c in raw:
        try:
            s = max(0.0, float(c["start"]))
            e = min(duration, float(c["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        # Visual estimates come from sparse labeled sheets. Snap nearby edges
        # to detected shot boundaries so a highlight does not begin/end on a
        # random action frame.
        if visual and starts:
            near = min(starts, key=lambda x: abs(x - s))
            if abs(near - s) <= 5.0:
                s = near
        if visual and ends:
            near = min(ends, key=lambda x: abs(x - e))
            if abs(near - e) <= 5.0:
                e = near
        if e - s < CLIP_MIN_S:
            continue
        if e - s > CLIP_MAX_S:
            e = s + CLIP_MAX_S
        title = re.sub(r"\s+", " ", str(c.get("title") or "")).strip()[:55]
        normalized = {
            "start": round(s, 2), "end": round(e, 2),
            "title": title or f"Short from {s:.0f}s",
            "hook": str(c.get("hook") or "").strip()[:160],
            "score": max(0, min(100, int(c.get("score") or 50))),
            "music": bool(c.get("music")),
        }
        if c.get("context_restored"):
            normalized["context_restored"] = str(c["context_restored"])[:80]
        clips.append(normalized)
    clips.sort(key=lambda c: -c["score"])
    chosen = []
    for c in clips:
        if len(chosen) >= (want or config.SHORTS_MAX_CLIPS):
            break
        overlap = any(min(c["end"], k["end"]) - max(c["start"], k["start"])
                      > 0.3 * (c["end"] - c["start"]) for k in chosen)
        if not overlap:
            chosen.append(c)
    for i, c in enumerate(chosen):
        c["order"] = i
    return chosen


def _complete_conversation_arcs(raw, index, duration):
    """Deterministically restore a nearby question before an isolated answer.

    The LLM is still responsible for editorial selection, but diarized
    sentence timestamps can catch its common shorts mistake without another
    model call: starting exactly on speaker B's answer while speaker A's
    immediately preceding question is just outside the window.
    """
    sents = [s for s in (index or {}).get("sentences") or []
             if (s.get("text") or "").strip()]
    speakers = {s.get("speaker") for s in sents
                if s.get("speaker") is not None}
    if len(speakers) < 2:
        return raw
    answer_cues = ("yes", "no", "well", "because", "i think", "i would",
                   "it is", "that's", "that is", "the reason")
    out = []
    for original in raw:
        clip = dict(original)
        try:
            start, end = float(clip["start"]), float(clip["end"])
        except (KeyError, TypeError, ValueError):
            out.append(clip)
            continue
        current = next((s for s in sents
                        if float(s["t0"]) - 0.15 <= start <=
                        float(s["t1"]) + 0.15), None)
        previous = next((s for s in reversed(sents)
                         if float(s["t1"]) <= start + 0.15), None)
        if current and previous and \
                current.get("speaker") is not None and \
                previous.get("speaker") is not None and \
                current.get("speaker") != previous.get("speaker"):
            gap = start - float(previous["t1"])
            prev_text = (previous.get("text") or "").strip()
            cur_text = (current.get("text") or "").strip().lower()
            looks_like_question = prev_text.endswith("?") or any(
                prev_text.lower().startswith(q) for q in
                ("what ", "why ", "how ", "when ", "where ", "who ",
                 "do ", "does ", "did ", "can ", "could ", "would ",
                 "is ", "are ", "tell me"))
            answer_like = cur_text.startswith(answer_cues)
            extension = start - float(previous["t0"])
            if gap <= 2.5 and extension <= 14.0 and \
                    end - float(previous["t0"]) <= CLIP_MAX_S and \
                    (looks_like_question or answer_like):
                clip["start"] = float(previous["t0"])
                clip["context_restored"] = "preceding question/setup"
        out.append(clip)
    return out


def _visual_plan_clips(index, duration, n_target, want, note, workdir):
    """Vision fallback for gameplay, sports, training and music footage."""
    keys = list(index.get("tile_keys") or [])
    if not keys or not llm.vision_available():
        raise dbx.PermanentJobError(
            "this video has no transcribed speech or visual filmstrip to "
            "select shorts from")
    max_sheets = 18
    if len(keys) > max_sheets:
        stride = len(keys) / float(max_sheets)
        keys = [keys[min(len(keys) - 1, int(i * stride))]
                for i in range(max_sheets)]
    paths, labels = [], []
    for i, key in enumerate(keys):
        path = os.path.join(workdir, f"visual_plan_{i:02d}.jpg")
        try:
            storage.download_to(key, path)
        except Exception:
            continue
        paths.append(path)
        labels.append(f"source filmstrip {i + 1} (timestamps printed in image)")
    if not paths:
        raise dbx.PermanentJobError(
            "this video has no transcribed speech and its visual filmstrip "
            "could not be read")
    context = (f"Video duration: {duration:.1f}s. Select about {n_target} "
               f"clips, never more than {config.SHORTS_MAX_CLIPS}; each "
               "should be 15-60 seconds."
               + (f" User direction: {note}" if note else ""))
    answer = llm.ask_vision(
        _VISUAL_PLAN_SYSTEM + "\n\n" + context, paths,
        max_tokens=1800, purpose="shorts_visual_plan",
        image_names=labels, reasoning_effort="low")
    out = _json_from(answer) if answer else None
    return _validated_clips((out or {}).get("clips") or [], duration, want,
                            index=index, visual=True)


def _transcript_is_useful(index, duration):
    """Whether speech is rich enough to choose highlights by meaning.

    Whisper occasionally returns a sign-off or three hallucinated words from
    minutes of otherwise visual footage.  Treating that as a transcript hid
    the filmstrips from the shorts planner and produced an empty plan twice.
    """
    words = index.get("words") or []
    if words:
        count = len([w for w in words if str(w.get("w") or "").strip()])
    else:
        text = " ".join(str(s.get("text") or "")
                        for s in (index.get("sentences") or []))
        count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    if count == 0:
        return False
    minutes = max(float(duration or 0.0) / 60.0, 0.25)
    # Fewer than two words/minute on a long source is not a semantic spine.
    if duration >= 60.0 and count < 8 and count / minutes < 2.0:
        return False
    spans = []
    for sent in (index.get("sentences") or []):
        try:
            spans.append(max(0.0, float(sent["t1"]) - float(sent["t0"])))
        except (KeyError, TypeError, ValueError):
            continue
    coverage = sum(spans) / max(float(duration or 0.0), 0.1)
    if duration >= 60.0 and count < 12 and coverage < 0.03:
        return False
    return True


def _plan_clips(worker_db, job, index, duration, style, payload,
                subscribed, plan, workdir=None):
    """Plan validated clip windows from speech, or visually when speechless."""
    want = payload.get("count")
    try:
        want = int(want) if want else None
    except (TypeError, ValueError):
        want = None
    n_target = min(want, config.SHORTS_MAX_CLIPS) if want \
        else _default_count(duration)

    len_hint = "Each clip should run 15-60 seconds."
    if style and style.get("analyzed"):
        ref_len = style.get("duration_s") or 0
        if ref_len:
            lo = max(CLIP_MIN_S + 2, min(55, ref_len * 0.7))
            hi = min(CLIP_MAX_S - 5, max(25, ref_len * 1.3))
            len_hint = (f"The user's reference short is {ref_len:.0f}s with "
                        f"{style.get('cuts_per_min', 0):.0f} cuts/min and "
                        f"{style.get('energy')} energy — aim for "
                        f"{lo:.0f}-{hi:.0f}s clips with the same feel.")
    note = (payload.get("style_note") or "").strip()[:400]

    transcript = _transcript_block(index)
    visual_ready = bool(index.get("tile_keys") or []) \
        and llm.vision_available()
    if (not transcript.strip() or not _transcript_is_useful(index, duration)) \
            and visual_ready:
        wd = workdir or os.path.join(
            config.TMP_DIR, f"shorts_visual_{(job or {}).get('id', 'plan')}")
        os.makedirs(wd, exist_ok=True)
        return _visual_plan_clips(index, duration, n_target, want, note, wd)
    if not transcript.strip():
        wd = workdir or os.path.join(
            config.TMP_DIR, f"shorts_visual_{(job or {}).get('id', 'plan')}")
        os.makedirs(wd, exist_ok=True)
        return _visual_plan_clips(index, duration, n_target, want, note, wd)
    user = (f"Video duration: {duration:.1f}s. "
            f"Aim for {n_target} clips (fewer if the material is thin, "
            f"never more than {config.SHORTS_MAX_CLIPS}). {len_hint}\n"
            f"Detected speakers: {int(index.get('speakers') or 0)}. "
            "When there are multiple speakers, preserve complete nearby "
            "question-and-answer turns rather than isolated quotes.\n"
            + (f"User's direction: {note}\n" if note else "")
            + f"\nTRANSCRIPT:\n{transcript}")
    out = _ask_json(worker_db, job, subscribed, plan, _PLAN_SYSTEM, user,
                    "shorts_plan", max_tokens=3500)
    raw_clips = _complete_conversation_arcs(
        (out or {}).get("clips") or [], index, duration)
    clips = _validated_clips(raw_clips, duration, want, index=index)
    # One empty language-plan answer should not turn into a whole job retry
    # when the same job already has visual evidence. Fall through to vision in
    # this execution, preserving both time and the model tokens already spent.
    if not clips and visual_ready:
        wd = workdir or os.path.join(
            config.TMP_DIR, f"shorts_visual_{(job or {}).get('id', 'plan')}")
        os.makedirs(wd, exist_ok=True)
        return _visual_plan_clips(index, duration, n_target, want, note, wd)
    return clips


# ------------------------------------------------------------------ children
def _create_child(conn, user_id, parent, clip):
    """Child project + shared-by-key assets, one transaction."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO chat_sessions (user_id, title)
                       VALUES (%s, %s) RETURNING id""",
                    (user_id, clip["title"]))
        session_id = cur.fetchone()["id"]
        cur.execute("""INSERT INTO projects (user_id, title, chat_session_id,
                                             kind, parent_project_id, meta)
                       VALUES (%s, %s, %s, 'short', %s, %s)
                       RETURNING id""",
                    (user_id, clip["title"], session_id, parent["id"],
                     Json({"clip": {"order": clip["order"],
                                    "start": clip["start"],
                                    "end": clip["end"],
                                    "score": clip["score"],
                                    "hook": clip["hook"]}})))
        child_id = cur.fetchone()["id"]
    return child_id, session_id


def _share_asset(conn, child_id, src_asset, note):
    meta = dict(src_asset.get("meta") or {})
    meta.pop("staged", None)
    meta.pop("tray_pos", None)
    meta["shared_from_project"] = note
    return dbx.insert_asset(
        conn, child_id, src_asset["kind"], src_asset["storage_key"],
        bytes_=src_asset.get("bytes"), duration_s=src_asset.get("duration_s"),
        width=src_asset.get("width"), height=src_asset.get("height"),
        fps=src_asset.get("fps"), sha256=src_asset.get("sha256"), meta=meta)


def _seed_child(worker_db, job, child_id, index, clip, style, workdir,
                proxy_local=None):
    """Build the child's EDL through the agent's own tools. Returns
    (edl_version, notes). Tool REJECTions degrade the styling, never fail
    the clip — a short without a grade is still a short."""
    child = worker_db.run(dbx.get_project, child_id)
    wd = os.path.join(workdir, f"child_{child_id}")
    os.makedirs(wd, exist_ok=True)
    ctx = agent_tools.ToolContext(worker_db, job, child, index, wd)
    if proxy_local and os.path.exists(proxy_local):
        # Every child shares the parent's proxy by key — download it once
        # for the whole run, not once per child (the reframe sampler reads
        # frames from it in every seed).
        ctx._proxy_local = proxy_local
    notes = []

    def call(tool, **args):
        try:
            out = agent_tools.execute(ctx, tool, args)
        except agent_tools.AskUser as e:
            out = f"skipped (would ask: {e.question})"
        except Exception as e:
            out = f"failed ({str(e)[:160]})"
        out = str(out)
        notes.append(f"{tool}: {(out.splitlines() or [''])[0][:160]}")
        return out

    call("keep_segments", segments=[[clip["start"], clip["end"]]],
         snap_to_words=True)
    call("auto_reframe", ratio="9:16", mode="auto")

    words = [w for w in (index.get("words") or []) if not w.get("filler")]
    spoken = [w for w in words
              if clip["start"] - 0.05
              <= (float(w["t0"]) + float(w["t1"])) / 2
              <= clip["end"] + 0.05]
    if len(spoken) >= 4:
        # size 'l': a vertical reel is watched on a phone with the sound
        # often off — captions are the read, not a garnish. 'm' (the Aug 8
        # run) read like a broadcast subtitle; modern reels set them big.
        cap_style = {"preset": (style or {}).get("captions_preset")
                     or "clean", "size": "l"}
        if (style or {}).get("uppercase"):
            cap_style["uppercase"] = True
        if (style or {}).get("captions", True):
            call("add_captions", mode="from_transcript", style=cap_style)
        length = clip["end"] - clip["start"]
        punches = max(1, min(6, int(round(length / 9.0))))
        call("punch_in_on_emphasis", count=punches,
             strength=(style or {}).get("punch_strength") or 0.12)

    grade = (style or {}).get("grade")
    if grade in GRADE_PRESETS:
        call("set_color_grade", preset=grade)

    music = (style or {}).get("music") or {}
    # THE USER'S WORD BEATS EVERY HEURISTIC. The Aug 8 run: the user typed
    # "add music" into the shorts direction and got silence on all 8 clips,
    # because music only fired when the REFERENCE was music-prominent or the
    # clip had almost no speech. An explicit ask forces it everywhere; the
    # planner's per-clip music=true now also counts under speech — that is
    # what ducking is FOR (duck=True sits the bed under the voice).
    want_music = bool((style or {}).get("music_requested")) \
        or bool(music.get("prominent")) or bool(clip.get("music"))
    if want_music:
        try:
            _add_music(ctx, call, worker_db, child_id, clip, music)
        except Exception as e:
            notes.append(f"music: failed ({str(e)[:120]})")

    row = ctx.latest_edl()
    shutil.rmtree(wd, ignore_errors=True)
    return row["version"], notes


def _add_music(ctx, call, worker_db, child_id, clip, music):
    """Best-effort: search the licensed pool, fetch one, lay it under the
    clip with ducking. Every step already refuses politely on its own."""
    query = music.get("query") or "upbeat energetic beat"
    length = clip["end"] - clip["start"]
    out = call("search_music", query=query,
               min_seconds=max(15, int(length * 0.8)))
    hits = getattr(ctx, "_music_hits", None) or {}
    if not hits:
        return
    bpm = music.get("bpm")

    def rank(h):
        hb = h.get("bpm")
        if bpm and hb:
            return abs(float(hb) - float(bpm))
        return 1e6
    best = sorted(hits.values(), key=rank)[0]
    call("fetch_music", id=best["id"])
    asset = worker_db.run(dbx.latest_asset, child_id, "music")
    if asset:
        call("add_music", storage_key=asset["storage_key"], duck=True)


# ------------------------------------------------------------------ the job
def run_shorts_plan(worker_db, job):
    job_id, project_id = job["id"], job["project_id"]
    payload = job.get("payload") or {}
    project = worker_db.run(dbx.get_project, project_id)
    if not project:
        raise RuntimeError("project not found")
    session_id = project["chat_session_id"]

    shorts_meta = dict((project.get("meta") or {}).get("shorts") or {})
    if shorts_meta.get("status") == "ready" and \
            shorts_meta.get("plan_job_id") == job_id:
        # A reclaimed job whose first run finished — nothing to redo.
        return {"clips": len(shorts_meta.get("clips") or []),
                "resumed": True, "billable": False}

    original = worker_db.run(dbx.latest_asset, project_id, "original")
    if not original or not original.get("sha256"):
        raise RuntimeError("the video is still being analyzed")
    # Proxy-first uploads: the project is editable off the browser proxy while
    # the full-resolution original is STILL UPLOADING. Finals read the
    # original, so cutting proceeds now and the exports are deferred — the
    # backend's original-ready hook fans them out the moment the bytes land.
    original_pending = (original.get("meta") or {}) \
        .get("upload_state") == "pending"
    idx_row = worker_db.run(dbx.get_index_by_sha, original["sha256"])
    if not idx_row:
        raise RuntimeError("the video is still being analyzed")
    index = idx_row["json"]
    duration = float((index.get("video") or {}).get("duration") or 0.0)
    if duration < 60.0:
        # Deploy skew or an already-queued job can still reach this runner
        # after the API/indexer routing fix. Degrade into the direct editor;
        # never resurrect the old user-facing failure.
        worker_db.run(dbx.set_project_kind, project_id, "edit")
        pending = (worker_db.run(dbx.pending_user_message,
                                 project_id, session_id)
                   if session_id else None)
        if pending and worker_db.run(
                dbx.user_credits_balance, job["user_id"]) >= 1.0:
            worker_db.run(dbx.enqueue_job, project_id, job["user_id"],
                          "agent_turn",
                          {"message_id": pending["id"],
                           "auto_resumed": True, "direct_short": True})
        elif session_id:
            worker_db.run(
                dbx.add_message, session_id, "assistant",
                f"This {duration:.0f}-second upload already fits one short, "
                "so I opened it in the Editor. Tell me how you want this "
                "short tightened, reframed or styled and I'll edit it "
                "directly.", {"kind": "direct_short"})
        return {"direct_edit": True, "billable": False}

    # The gate, mirrored from the message path: cutting shorts runs the model
    # and the render farm, so it needs the same credit standing as a turn.
    try:
        subscribed, plan, trialing = worker_db.run(dbx.user_billing,
                                                   job["user_id"])
    except Exception:
        subscribed, plan, trialing = False, "free", False
    balance = worker_db.run(dbx.user_credits_balance, job["user_id"])
    if balance < 1.0:
        shorts_meta.update({"status": "gated", "plan_job_id": job_id,
                            "gated_at": _now_iso()})
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)
        if session_id:
            text = ("I watched the video and I'm ready to cut shorts from "
                    "it, but you're out of credits. "
                    + ("Your plan's pool refreshes on renewal — or top up "
                       "by upgrading." if subscribed else
                       "Start your 3-day free trial and press Make shorts "
                       "again."))
            worker_db.run(dbx.add_message, session_id, "assistant", text,
                          {"kind": "shorts_gate", "credits_exhausted": True})
        return {"gated": True, "billable": False}

    workdir = os.path.join(config.TMP_DIR, f"shorts_{job_id}")
    os.makedirs(workdir, exist_ok=True)

    def _recorder(purpose, request, response, usage):
        model = (request or {}).get("model")
        try:
            worker_db.run(dbx.insert_llm_call, project_id, job_id, purpose,
                          model, request, response,
                          getattr(usage, "prompt_tokens", None) if usage
                          else None,
                          getattr(usage, "completion_tokens", None) if usage
                          else None)
        except Exception as e:
            print(f"[shorts {job_id}] llm_call record failed: {e}",
                  flush=True)

    llm.set_recorder(_recorder)
    llm.set_turn_plan(plan if subscribed else "")
    try:
        worker_db.run(dbx.set_progress, job_id, 8)

        ref = worker_db.run(_find_reference, project_id)
        style = None
        if ref:
            style = _reference_profile(worker_db, job, ref, workdir)
        # The user's typed direction outranks anything measured off a
        # reference: "add music" in the note means every clip gets a bed
        # (ducked under speech), full stop — the Aug 8 run ignored exactly
        # this and shipped 8 silent clips against an explicit ask.
        note_l = (payload.get("style_note") or "").lower()
        if re.search(r"\b(music|soundtrack|song|beat|bgm|track)\b", note_l):
            style = style or {}
            style["music_requested"] = True
            style.setdefault("music", {})
        worker_db.run(dbx.set_progress, job_id, 22)

        clips = list(shorts_meta.get("clips") or []) \
            if shorts_meta.get("plan_job_id") == job_id else []
        if not clips:
            clips = _plan_clips(worker_db, job, index, duration, style,
                                payload, subscribed, plan, workdir=workdir)
        if not clips:
            # NOT permanent: this is an LLM planning answer, and a retry can
            # genuinely land clips where the first pass came back empty.
            raise RuntimeError("I couldn't find clip-worthy moments in "
                               "this transcript")
        worker_db.run(dbx.set_progress, job_id, 35)

        shorts_meta = {
            "status": "cutting", "plan_job_id": job_id,
            "started_at": shorts_meta.get("started_at") or _now_iso(),
            "reference_asset_id": ref["id"] if ref else None,
            "style_profile": style,
            "finals_deferred": bool(original_pending),
            "clips": clips,
        }
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        proxy = worker_db.run(dbx.latest_asset, project_id, "proxy")
        n = len(clips)
        # Phase 1 — children EXIST first: cheap ordered INSERTs, so the board
        # can draw every card before a single frame is cut.
        for clip in clips:
            if not clip.get("child_project_id"):
                child_id, child_session = worker_db.run(
                    _create_child, job["user_id"], project, clip)
                worker_db.run(_share_asset, child_id, original,
                              project_id)
                if proxy:
                    worker_db.run(_share_asset, child_id, proxy, project_id)
                worker_db.run(
                    dbx.add_message, child_session, "assistant",
                    f"This short — “{clip['title']}” — is cut from "
                    f"“{project['title']}” "
                    f"({clip['start']:.0f}s-{clip['end']:.0f}s). It's "
                    "rendering now. Tell me what to refine: the hook, "
                    "captions, pacing, music, any effect you want.",
                    {"kind": "short_intro"})
                clip["child_project_id"] = child_id
                worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        # Phase 2 — seed the EDLs IN PARALLEL, each on its own DB connection
        # (Db is one-per-thread by contract). The Aug 8 session seeded 8
        # children one after another and the user watched ~22 minutes of
        # upload-to-done; seeding is tool calls + a few frame samples, so
        # four at once is bounded by IO, not CPU. Each finished child fans
        # its final out IMMEDIATELY — renders overlap the remaining seeds.
        meta_lock = threading.Lock()
        done = [0]
        shared_proxy = None
        if proxy:
            shared_proxy = os.path.join(workdir, "shared_proxy.mp4")
            try:
                storage.download_to(proxy["storage_key"], shared_proxy)
            except Exception:
                shared_proxy = None

        def _seed_one(clip):
            db_local = dbx.Db()
            try:
                version, notes = _seed_child(
                    db_local, job, clip["child_project_id"], index, clip,
                    style, workdir, proxy_local=shared_proxy)
                with meta_lock:
                    clip["edl_version"] = version
                    clip["seed_notes"] = notes[-6:]
                    if original_pending:
                        clip["final_deferred"] = True
                if not original_pending:
                    db_local.run(dbx.enqueue_job, clip["child_project_id"],
                                 job["user_id"], "final",
                                 {"edl_version": version,
                                  "source": "shorts"})
                with meta_lock:
                    done[0] += 1
                    db_local.run(_save_shorts_meta, project_id, shorts_meta)
                    db_local.run(dbx.set_progress, job_id,
                                 35 + int(58 * done[0] / n))
            except Exception as e:
                with meta_lock:
                    clip["seed_error"] = str(e)[:200]
                print(f"[shorts {job_id}] child "
                      f"{clip.get('child_project_id')} seed failed: {e}",
                      flush=True)
            finally:
                db_local.reset()

        todo = [c for c in clips if not c.get("edl_version")]
        if todo:
            with ThreadPoolExecutor(
                    max_workers=min(4, max(1, len(todo)))) as pool:
                list(pool.map(_seed_one, todo))
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        shorts_meta["status"] = "ready"
        shorts_meta["finished_at"] = _now_iso()
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        if session_id:
            mins = duration / 60.0
            lens = [c["end"] - c["start"] for c in clips]
            styled = ""
            if style and style.get("analyzed"):
                bits = [f"{style.get('energy')} pacing"]
                if style.get("captions_preset"):
                    bits.append(f"{style['captions_preset']} captions")
                if style.get("grade"):
                    bits.append(f"a {style['grade']} grade")
                if (style.get("music") or {}).get("prominent"):
                    bits.append("music matched to its tempo")
                styled = (" I styled them after your reference — "
                          + ", ".join(bits) + ".")
            tail = ("They're rendering on your Shorts board now — each one "
                    "is its own project, so open any of them and tell me "
                    "what to change.")
            if original_pending:
                tail = ("They're built and on your Shorts board — your "
                        "full-resolution video is still uploading in the "
                        "background, and each one exports automatically the "
                        "moment it lands. Open any of them meanwhile and "
                        "tell me what to change.")
            worker_db.run(
                dbx.add_message, session_id, "assistant",
                f"I watched all {mins:.0f} minutes and cut {n} short"
                f"{'s' if n != 1 else ''} ({min(lens):.0f}-{max(lens):.0f}s "
                "each), reframed to 9:16 with captions and emphasis "
                f"punch-ins.{styled} {tail}",
                {"kind": "shorts_ready", "clips": n})

        worker_db.run(dbx.set_progress, job_id, 97)
        return {"clips": n, "billable": True,
                "reference": bool(ref and style and style.get("analyzed")),
                "children": [c.get("child_project_id") for c in clips]}
    except Exception:
        # Leave an honest board state — the studio overlays the job error.
        try:
            shorts_meta["status"] = "failed"
            worker_db.run(_save_shorts_meta, project_id, shorts_meta)
        except Exception:
            pass
        raise
    finally:
        llm.set_recorder(None)
        llm.clear_turn_plan()
        shutil.rmtree(workdir, ignore_errors=True)
