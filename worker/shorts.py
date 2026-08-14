"""Shorts mode — job type "shorts_plan" (round 99).

One job scouts a long indexed video for genuinely complete, worthwhile
micro-stories. Each selected story becomes a LOCKED child project that shares
the parent's original + proxy BY STORAGE KEY and starts with exactly one
editorial decision: the parent scout's story cut. It is deliberately not
styled, reframed, captioned, scored, or rendered here.

The user explicitly presses Edit on a card to boot a fresh full-tool editing
agent inside that child. This separation is the product contract: the parent
is a discerning story scout; the child agent is the creative editor. A fixed
batch recipe must never impersonate either one.

The optional REFERENCE clip is shared into every child as the real indexed
asset. The scout does not reduce it to presets or let its duration distort
story selection. The fresh editor watches and hears it, then decides which
relationships are worth transferring.

BILLING: every model call here is recorded to llm_calls under THIS job (same
recorder pattern as mcp_exec), so main.py can charge it exactly like an agent
turn. There is no per-clip render surcharge because this job performs no
creative render; each explicit child edit is billed as its own agent turn.
The gate mirrors the message path: no credits → no run, and the wall is a chat
message the studio already knows how to draw.
"""

import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timezone

import agent_tools
import config
import db as dbx
import llm
import reference_profile as reference_grammar
import storage
from psycopg2.extras import Json
from schemas import GRADE_PRESETS

CLIP_MIN_S = 10.0          # technical floor; the scout still judges the story
CLIP_MAX_S = 120.0         # complete social stories may need more than 60s
REF_WAIT_S = 180           # how long to wait for the reference's own index
CAPTION_PRESETS = ("classic", "clean", "documentary", "broadcast", "retro",
                   "neon", "podcast", "beast", "karaoke", "elegant",
                   "spotlight", "stacked", "impact", "lyric")
LONG_TRANSCRIPT_DIRECT_CHARS = 45000


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
    # The same indexed reference used by the general editor carries much
    # richer transferable grammar than a single cuts/min number: cadence
    # contrast, beat relationship, energy arc, motion and composition. Keep a
    # compact copy with the Shorts project so every child inherits those
    # relationships without storing thousands of raw cut timestamps.
    measured_grammar = reference_grammar.from_index(ridx)
    measured_rhythm = dict(measured_grammar.get("rhythm") or {})
    measured_rhythm.pop("cut_times_s", None)
    measured_grammar["rhythm"] = measured_rhythm
    profile["measured_grammar"] = measured_grammar
    profile["measured_grammar_text"] = reference_grammar.describe(
        measured_grammar)[:1800]

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
_PLAN_SYSTEM = """You are the parent story scout for Valmera. You are given the timed sentence transcript of a long podcast or video. Your only job is to find the few genuinely compelling, self-contained stories worth handing to a fresh short-form editor. You do not style, caption, reframe, score music, prescribe B-roll, or imitate an editing template.

STORY CONTRACT — A SHORT IS A MICRO-STORY, NOT A TRANSCRIPT EXCERPT. Every selected window must have (1) a hook/setup or intelligible question, (2) development that changes or deepens the viewer's understanding, and (3) a payoff: resolution, lesson, reveal, decision, consequence, or punchline. A handful of related sentences that merely state information is not a short. Reject it even if one sentence is quotable. In interviews/podcasts, include the nearby question or premise when the answer needs it, preserve speaker turns, and do not cut away before the answer resolves. Before accepting a clip, be able to summarize its setup, development and payoff separately.

QUALITY OVER QUANTITY — Returning fewer stories is a sign of judgment. Never fill a requested count with weak fragments. Do not choose several clips that repeat the same idea. Let each story use the time its arc genuinely needs; do not compress it to one or two sentences merely to resemble the duration of an editing reference.

Reply with STRICT JSON only:
{"clips": [{"start": <source seconds>, "end": <source seconds>, "title": "<truthful story title, max 55 chars, no quotes inside>", "hook": "<the first spoken words of the clip, verbatim>", "score": <0-100 relative story-worthiness>, "story": {"setup": "<what establishes the question/stakes>", "development": "<what changes/deepens>", "payoff": "<how it resolves and why it satisfies>"}}]}

Rules: start and end MUST be sentence boundaries taken from the transcript timestamps. Never start mid-sentence. Prefer clips that contain their own necessary context. For multi-speaker material, never open on an answer whose preceding nearby question/setup is required to understand it; include the question. Do not cut away before the answer resolves. Do not overlap clips. Titles may create curiosity but must never lie about the content. Return an empty clips array when nothing is genuinely worthy."""

_VISUAL_PLAN_SYSTEM = """You are Valmera's story scout reviewing labeled filmstrip sheets from a video with little or no usable speech. Select only visually self-contained arcs worth handing to a fresh short-form editor. Read the timestamps printed under every frame. Prefer action with a setup and outcome, reactions, reveals, skill, before/after changes, wins, near misses, or another satisfying visual progression; avoid loading screens, menus, static dead time, repeated moments, and isolated spectacle with no intelligible development.

Reply with STRICT JSON only:
{"clips": [{"start": <seconds>, "end": <seconds>, "title": "<truthful hook title, max 55 chars>", "hook": "<short description of the opening visual>", "score": <0-100>}]}

Do not prescribe music, captions, reframing or effects. The sheets sample the full video rather than every frame. Use their timestamps to choose an approximate continuous window around each visible arc. Do not overlap clips and do not invent events that are not visible."""


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


def _shortlist_transcript_arcs(index, duration, n_target, direction=""):
    """Rank complete discourse windows across a long recording.

    Sending the first 180k characters of a two-hour podcast is both costly
    and editorially biased: the model cannot choose an excellent exchange it
    never receives.  This deterministic pass scores every sentence-boundary
    arc, preserves question/answer context, balances evidence across the full
    timeline, and gives one global model call a compact set of complete arcs
    to judge.  It is an attention allocator, not a clip decision.
    """
    sents = []
    for raw in (index or {}).get("sentences") or []:
        text = re.sub(r"\s+", " ", str(raw.get("text") or "")).strip()
        try:
            t0, t1 = float(raw["t0"]), float(raw["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and t1 > t0:
            sents.append({"t0": t0, "t1": t1, "text": text,
                          "speaker": raw.get("speaker")})
    if not sents:
        return "", {"candidates": 0, "source_sentences": 0}

    direction_words = {w for w in re.findall(
        r"\w+", str(direction or "").casefold(), flags=re.UNICODE)
                       if len(w) >= 4}
    hook_terms = {
        "mistake", "secret", "truth", "problem", "reason", "changed",
        "learned", "failed", "won", "lost", "money", "million", "never",
        "always", "best", "worst", "how", "why", "but", "because",
    }
    continuation = ("and ", "but ", "so ", "because ", "then ", "it ",
                    "that ", "this ", "they ", "he ", "she ", "yes ",
                    "no ", "well ", "exactly ")
    answer_cues = ("because ", "the reason", "what happened", "i think",
                   "i learned", "the answer", "it was", "we did")
    candidates = []
    for anchor in range(len(sents)):
        start_i = anchor
        current = sents[anchor]
        if anchor > 0:
            previous = sents[anchor - 1]
            cur_low = current["text"].casefold()
            prev_question = previous["text"].rstrip().endswith("?")
            speaker_reply = (previous.get("speaker") is not None
                             and current.get("speaker") is not None
                             and previous.get("speaker") != current.get("speaker"))
            if speaker_reply and (prev_question
                                  or cur_low.startswith(answer_cues)) \
                    and current["t0"] - previous["t1"] <= 2.5:
                start_i = anchor - 1

        # Prefer a complete 25-60s arc. End only on a sentence boundary and
        # let terminal punctuation / a new question break ties near target.
        possible = []
        for end_i in range(start_i, len(sents)):
            span = sents[end_i]["t1"] - sents[start_i]["t0"]
            if span > CLIP_MAX_S:
                break
            if span >= 18.0:
                ending = sents[end_i]["text"].rstrip()
                boundary = ending.endswith((".", "!", "?"))
                possible.append((abs(span - 42.0) - (.9 if boundary else 0),
                                 end_i))
            if span >= 60.0:
                break
        if not possible:
            continue
        end_i = min(possible)[1]
        rows = sents[start_i:end_i + 1]
        start, end = rows[0]["t0"], rows[-1]["t1"]
        text = " ".join(row["text"] for row in rows)
        words = re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
        distinct = len(set(words)) / max(1, len(words))
        speakers = {row.get("speaker") for row in rows
                    if row.get("speaker") is not None}
        first = rows[0]["text"].casefold().lstrip()
        questions = sum(row["text"].rstrip().endswith("?") for row in rows)
        score = 2.2 * distinct
        score += 1.4 if questions and len(speakers) >= 2 else .35 * questions
        score += min(2.0, sum(w in hook_terms for w in words) * .18)
        score += min(1.5, sum(any(ch.isdigit() for ch in w)
                              for w in words) * .3)
        score += min(1.8, len(direction_words.intersection(words)) * .45)
        score += .5 if rows[-1]["text"].rstrip().endswith((".", "!")) else 0
        if first.startswith(continuation) and start_i == anchor:
            score -= 1.2
        if questions and len(speakers) < 2 \
                and not any(row["text"].casefold().startswith(answer_cues)
                            for row in rows[1:]):
            score -= .6
        candidates.append({"start": start, "end": end, "score": score,
                           "rows": rows})

    if not candidates:
        return _transcript_block(index), {
            "candidates": 0, "source_sentences": len(sents)}

    # Evidence grows with both requested output and recording length. Two
    # strong arcs per five-minute region prevents an opening-heavy shortlist;
    # remaining places go to the strongest non-duplicate arcs globally.
    budget = max(16, int(n_target or 1) * 6,
                 int(math.ceil(max(duration, 1.0) / 600.0)) * 4)
    budget = min(64, budget)
    bucket_s = max(180.0, max(duration, 1.0) /
                   max(1.0, math.ceil(budget / 2.0)))
    buckets = {}
    for candidate in candidates:
        midpoint = (candidate["start"] + candidate["end"]) / 2.0
        buckets.setdefault(int(midpoint / bucket_s), []).append(candidate)

    chosen = []

    def duplicate(candidate):
        for old in chosen:
            overlap = max(0.0, min(candidate["end"], old["end"])
                          - max(candidate["start"], old["start"]))
            shorter = min(candidate["end"] - candidate["start"],
                          old["end"] - old["start"])
            if shorter > 0 and overlap / shorter > .68:
                return True
        return False

    for bucket in sorted(buckets):
        for candidate in sorted(buckets[bucket],
                                key=lambda row: -row["score"]):
            if not duplicate(candidate):
                chosen.append(candidate)
                break
    for candidate in sorted(candidates, key=lambda row: -row["score"]):
        if len(chosen) >= budget:
            break
        if not duplicate(candidate):
            chosen.append(candidate)
    chosen.sort(key=lambda row: row["start"])

    blocks = []
    for i, candidate in enumerate(chosen, 1):
        blocks.append(
            f"CANDIDATE ARC {i} "
            f"[{candidate['start']:.1f}-{candidate['end']:.1f}]")
        for row in candidate["rows"]:
            speaker = (f" S{row['speaker']}"
                       if row.get("speaker") is not None else "")
            blocks.append(
                f"[{row['t0']:.1f}-{row['t1']:.1f}]{speaker} {row['text']}")
        blocks.append("")
    return "\n".join(blocks).strip(), {
        "candidates": len(chosen), "source_sentences": len(sents),
        "source_duration_s": round(float(duration or 0.0), 1)}


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
        story = c.get("story") or {}
        if isinstance(story, dict):
            clean_story = {
                stage: re.sub(r"\s+", " ", str(story.get(stage) or ""))
                .strip()[:300]
                for stage in ("setup", "development", "payoff")
            }
            if any(clean_story.values()):
                normalized["story"] = clean_story
        visual_direction = re.sub(r"\s+", " ", str(
            c.get("visual_direction") or "")).strip()[:500]
        if visual_direction:
            normalized["visual_direction"] = visual_direction
        broll = []
        for raw_moment in (c.get("broll") or [])[:6]:
            if not isinstance(raw_moment, dict):
                continue
            try:
                at = float(raw_moment["at"])
                duration_s = max(1.0, min(8.0, float(
                    raw_moment.get("duration_s") or 3.0)))
            except (KeyError, TypeError, ValueError):
                continue
            query = re.sub(r"\s+", " ", str(
                raw_moment.get("query") or "")).strip()[:160]
            purpose = re.sub(r"\s+", " ", str(
                raw_moment.get("purpose") or "")).strip()[:220]
            if not query or not purpose or at < s or at > e:
                continue
            broll.append({
                "at": round(at, 2), "duration_s": duration_s,
                "query": query, "purpose": purpose,
                "kind": ("photo" if str(raw_moment.get("kind") or "").lower()
                         == "photo" else "video"),
            })
        if "broll" in c:
            normalized["broll"] = broll
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


def _caller_planned_clips(payload, index, duration):
    """Normalize explicit outside-model arcs, or None for automatic planning."""
    raw = payload.get("clips")
    if raw is None:
        return None
    restored = _complete_conversation_arcs(raw, index, duration)
    return _validated_clips(restored, duration, len(raw), index=index)


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

    len_hint = ("Let each selected story use the time its complete arc needs "
                "(often 25-90 seconds); completeness outranks runtime. The "
                "editing reference belongs to the later child editor and "
                "must not distort story selection.")
    note = (payload.get("style_note") or "").strip()[:400]

    transcript = _transcript_block(index)
    shortlist_meta = None
    if len(transcript) >= LONG_TRANSCRIPT_DIRECT_CHARS or \
            transcript.endswith("[transcript truncated]"):
        transcript, shortlist_meta = _shortlist_transcript_arcs(
            index, duration, n_target, note)
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
    evidence_note = ""
    evidence_label = "TRANSCRIPT"
    if shortlist_meta:
        evidence_label = "RANKED COMPLETE-ARC EVIDENCE"
        evidence_note = (
            f"The source has {shortlist_meta['source_sentences']} timed "
            f"sentences. A deterministic discourse pass surfaced "
            f"{shortlist_meta['candidates']} complete candidate arcs spread "
            "across the FULL recording; this is not a chronological "
            "truncation. Judge them globally. Keep start/end on sentence "
            "boundaries shown inside ONE candidate arc; never splice two "
            "unrelated arcs together.\n")
    user = (f"Video duration: {duration:.1f}s. "
            f"Aim for {n_target} clips (fewer if the material is thin, "
            f"never more than {config.SHORTS_MAX_CLIPS}). {len_hint}\n"
            f"Detected speakers: {int(index.get('speakers') or 0)}. "
            "When there are multiple speakers, preserve complete nearby "
            "question-and-answer turns rather than isolated quotes.\n"
            + evidence_note
            + (f"User's direction: {note}\n" if note else "")
            + f"\n{evidence_label}:\n{transcript}")
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
                     Json({"clip": {
                         key: clip.get(key) for key in (
                             "order", "start", "end", "score", "hook",
                             "story", "visual_direction", "broll")
                         if clip.get(key) is not None},
                           "shorts_editor": {
                               "status": "locked",
                               "parent_project_id": parent["id"],
                           }})))
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


def _seed_story_child(worker_db, job, child_id, index, clip, workdir):
    """Cut the parent's chosen story and nothing else.

    This is intentionally small. The fresh agent started by the card's Edit
    button must make every creative decision after it has watched this exact
    reel and loaded the relevant craft skills. Returning a decorated draft
    here would quietly restore the old one-recipe-for-everything product.
    """
    child = worker_db.run(dbx.get_project, child_id)
    wd = os.path.join(workdir, f"child_{child_id}")
    os.makedirs(wd, exist_ok=True)
    ctx = agent_tools.ToolContext(worker_db, job, child, index, wd)
    try:
        result = agent_tools.execute(
            ctx, "keep_segments",
            {"segments": [[clip["start"], clip["end"]]],
             "snap_to_words": True})
        row = ctx.latest_edl()
        return row["version"], str(result).splitlines()[0][:200]
    finally:
        shutil.rmtree(wd, ignore_errors=True)


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
        # The scout never converts a reference into presets. Preserve the real
        # asset and the user's words for the fresh child editor, which can
        # watch/hear them in context and make its own decisions.
        style = ({"source": "reference",
                  "reference_asset_id": ref["id"]} if ref else {})
        if (payload.get("style_note") or "").strip():
            style["user_note"] = str(payload["style_note"]).strip()[:400]
        style = style or None
        worker_db.run(dbx.set_progress, job_id, 22)

        clips = list(shorts_meta.get("clips") or []) \
            if shorts_meta.get("plan_job_id") == job_id else []
        caller_planned = _caller_planned_clips(payload, index, duration)
        if not clips:
            clips = (caller_planned if caller_planned is not None else
                     _plan_clips(worker_db, job, index, duration, style,
                                 payload, subscribed, plan, workdir=workdir))
        if not clips:
            # NOT permanent: this is an LLM planning answer, and a retry can
            # genuinely land clips where the first pass came back empty.
            raise RuntimeError("I couldn't find clip-worthy moments in "
                               "this transcript")
        worker_db.run(dbx.set_progress, job_id, 35)

        shorts_meta = {
            "status": "selecting", "plan_job_id": job_id,
            "started_at": shorts_meta.get("started_at") or _now_iso(),
            "reference_asset_id": ref["id"] if ref else None,
            "style_profile": style,
            "selection_source": (str(payload.get("source") or "caller_direct")
                                 if caller_planned is not None else
                                 "valmera_planner"),
            "finals_deferred": True,
            "clips": clips,
        }
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        proxy = worker_db.run(dbx.latest_asset, project_id, "proxy")
        n = len(clips)
        # The scout creates RAW STORY CUTS. No caption preset, crop, grade,
        # zoom, B-roll, music, final render, or other creative choice belongs
        # here. Each card stays locked until the user explicitly boots its
        # fresh editor.
        for position, clip in enumerate(clips, 1):
            if not clip.get("child_project_id"):
                child_id, child_session = worker_db.run(
                    _create_child, job["user_id"], project, clip)
                worker_db.run(_share_asset, child_id, original,
                              project_id)
                if proxy:
                    worker_db.run(_share_asset, child_id, proxy, project_id)
                if ref:
                    # The child editor must be able to watch the real reference
                    # itself. A prose style summary is context, not eyesight.
                    worker_db.run(_share_asset, child_id, ref, project_id)
                worker_db.run(
                    dbx.add_message, child_session, "assistant",
                    f"“{clip['title']}” was selected from “{project['title']}” "
                    f"({clip['start']:.0f}s-{clip['end']:.0f}s) because it "
                    "contains a complete story worth developing. This is the "
                    "un-styled story cut; its fresh editor has not been "
                    "started yet.",
                    {"kind": "short_intro"})
                clip["child_project_id"] = child_id
            if not clip.get("seed_edl_version"):
                version, seed_note = _seed_story_child(
                    worker_db, job, clip["child_project_id"], index, clip,
                    workdir)
                clip["seed_edl_version"] = version
                # edl_version remains for older clients; its meaning is now
                # explicitly the raw selection boundary, not a styled edit.
                clip["edl_version"] = version
                clip["seed_note"] = seed_note
            clip["edit_status"] = "locked"
            worker_db.run(_save_shorts_meta, project_id, shorts_meta)
            worker_db.run(dbx.set_progress, job_id,
                          35 + int(58 * position / n))

        shorts_meta["status"] = "ready"
        shorts_meta["finished_at"] = _now_iso()
        worker_db.run(_save_shorts_meta, project_id, shorts_meta)

        if session_id:
            mins = duration / 60.0
            lens = [c["end"] - c["start"] for c in clips]
            worker_db.run(
                dbx.add_message, session_id, "assistant",
                f"I reviewed all {mins:.0f} minutes and found {n} stor"
                f"{'ies' if n != 1 else 'y'} genuinely worth developing "
                f"({min(lens):.0f}-{max(lens):.0f}s). These are raw story "
                "cuts, not template edits. Choose Edit on any card to boot "
                "a fresh short-form editor for that reel.",
                {"kind": "shorts_candidates", "clips": n,
                 "parent_project_id": project_id})

        worker_db.run(dbx.set_progress, job_id, 97)
        return {"clips": n, "rendered_clips": 0, "billable": True,
                "reference": bool(ref),
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
