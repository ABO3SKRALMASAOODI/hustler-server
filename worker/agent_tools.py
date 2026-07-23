"""Agent tools. Every argument is validated and clamped, every error is a
short instructive string the model can act on, every output fits the token
budget. Write tools create new EDL versions and return one-line diffs."""

import difflib
import json
import os
import re
import shutil
import time
import uuid

import audit
import config
import db as dbx
import eleven
import llm
import media
import music_library
import sfx_library
import storage
import videogen
import timeline as timeline_mod
import url_media
from captions import KARAOKE_HARD_MAX
from schemas import (CANVAS_DIMS, CaptionStyle, EDLValidationError, Frame,
                     canvas_edl, describe_edl, DEFAULT_CANVAS_FPS,
                     edl_signature, is_canvas_program, keep_boundaries,
                     output_duration, program_duration, validate_edl,
                     MAX_INSERT_DURATION_S, GAIN_MIN_DB, GAIN_MAX_DB,
                     GRADE_PRESETS, TRANSITION_STYLES, TRANSITION_MIN_S,
                     TRANSITION_MAX_S)
from timeline import Timeline


class AskUser(Exception):
    """Raised by the ask_user tool to suspend the loop until the user replies."""

    def __init__(self, question):
        super().__init__(question)
        self.question = question


class ToolContext:
    def __init__(self, worker_db, job, project, index, workdir):
        self.db = worker_db
        self.job = job
        self.project = project
        self.project_id = project["id"]
        self.session_id = project["chat_session_id"]
        # index is None for a canvas program (no main video): the project holds
        # only images/clips/audio, or nothing yet. has_main_video gates every
        # tool that reads the source footage; duration is the master clock for
        # main-video edits (0 when there is none — placement tools bound
        # themselves against program_duration instead).
        self.index = index or {}
        self.has_main_video = bool(index and index.get("video"))
        self.duration = (float(index["video"]["duration"])
                         if self.has_main_video else 0.0)
        # Default output aspect for a no-main-video program; refined from the
        # first asset actually placed (see insert_media / _canvas_for_asset).
        self.canvas_ratio = "16:9"
        self.workdir = workdir
        self._proxy_local = None
        self._asset_locals = {}       # asset id -> downloaded local path
        self.last_preview = None      # set by render_preview
        self.last_selfcheck = None    # vision one-liner from the last preview
        self.versions_written = []    # EDL versions created this turn
        self.rendered_versions = set()  # versions with a successful preview
        self.autorendered = False     # loop set: model skipped render_preview
        self.write_calls = []         # successful write tool names this turn
        self.images_generated = []    # assets created by generate_image
        self.sfx_generated = []       # sounds created by generate_sfx
        self.videos_generated = []    # clips created by generate_video
        self.urls_fetched = []        # assets created by fetch_url
        # USD cost of non-LLM generations this turn (sfx flat, video per-second)
        # — added to running_credits so the in-turn spend cap sees them.
        self.gen_extra_cost_usd = 0.0
        # Live per-turn model spend, for the graceful spend cap. tokens are
        # accumulated by the loop's llm recorder; images are priced flat.
        self.tokens_in = 0
        self.tokens_out = 0
        self.credit_budget = None     # set by run_agent_job; None = uncapped

    def running_credits(self):
        """Model cost spent so far this turn, in credits (1 credit = $0.01),
        using the same formula as db.charge_turn_credits so the in-turn cap
        and the final charge agree."""
        cost = (self.tokens_in * config.LLM_PRICE_IN_PER_M +
                self.tokens_out * config.LLM_PRICE_OUT_PER_M) / 1e6
        cost += len(self.images_generated) * config.IMAGE_PRICE_USD
        cost += self.gen_extra_cost_usd     # generated sfx + video (real $)
        return round(cost / 0.01, 2)

    def over_budget(self):
        return (self.credit_budget is not None and
                self.running_credits() >= self.credit_budget)

    def clamp(self, t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            raise ValueError(f"'{t}' is not a number of seconds")
        # With no main video there is no source clock to clamp against; the
        # placement tools bound program positions against program_duration
        # themselves, so keep a generous upper here rather than collapsing
        # every time to 0.
        upper = self.duration if self.duration > 0 else 1e7
        return round(min(max(t, 0.0), upper), 2)

    def proxy_path(self):
        if self._proxy_local is None:
            proxy = self.db.run(dbx.latest_asset, self.project_id, "proxy")
            if not proxy:
                raise RuntimeError("no proxy available")
            local = os.path.join(self.workdir, "proxy.mp4")
            storage.download_to(proxy["storage_key"], local)
            self._proxy_local = local
        return self._proxy_local

    def latest_edl(self):
        row = self.db.run(dbx.latest_edl, self.project_id)
        if not row:
            from schemas import default_edl
            base = (default_edl(self.duration) if self.has_main_video
                    else canvas_edl(self.canvas_ratio))
            v = self.db.run(dbx.insert_edl, self.project_id, base, "agent")
            row = self.db.run(dbx.get_edl_version, self.project_id, v)
        return row

    def write_edl(self, new_edl_dict, change_desc):
        """Validate + append a new version. Returns the diff line, a NO
        CHANGE notice when the result is byte-identical to the current
        version (no version row is created), or a REJECTED message on
        validation failure."""
        prev = self.latest_edl()
        try:
            normalized = validate_edl(new_edl_dict, self.duration).model_dump()
        except EDLValidationError as e:
            return f"REJECTED (EDL v{prev['version']} unchanged): {e}"
        if edl_signature(normalized) == edl_signature(prev["json"]):
            return (f"NO CHANGE — the EDL is identical to v{prev['version']}; "
                    "the requested change may need a different tool or may "
                    "not be supported. Do NOT tell the user you changed "
                    "anything.")
        version = self.db.run(dbx.insert_edl, self.project_id, normalized,
                              "agent")
        self.versions_written.append(version)
        before = describe_edl(prev["json"])
        after = describe_edl(normalized, self.duration)
        return (f"EDL v{prev['version']} -> v{version}: {change_desc}. "
                f"Before: {before}. After: {after}.")


def _cap(text, budget=None):
    budget = budget or config.TOOL_OUTPUT_CHAR_BUDGET
    if len(text) <= budget:
        return text
    return text[:budget] + "\n...[truncated — narrow your range and call again]"


def _fmt_t(t):
    return f"{t:.2f}"


# ------------------------------------------------------------------ #
#  READ tools                                                          #
# ------------------------------------------------------------------ #

def get_video_info(ctx):
    if not ctx.has_main_video:
        edl = ctx.latest_edl()
        ins = edl["json"].get("inserts") or []
        return ("No main video in this project — this is a blank canvas. Build "
                "the program from generated or uploaded images/clips: create "
                "with generate_image / generate_video, then place with "
                "insert_media. "
                f"Current EDL v{edl['version']}: {len(ins)} placed "
                f"clip{'s' if len(ins) != 1 else ''}, "
                f"{program_duration(edl['json'])}s total.")
    v = ctx.index["video"]
    sil = [s for s in ctx.index.get("silences", []) if s[1] - s[0] >= 0.7]
    total_sil = sum(e - s for s, e in sil)
    edl = ctx.latest_edl()
    return (f"duration={v['duration']}s, {v['width']}x{v['height']} @ "
            f"{v['fps']}fps, audio={'yes' if v['has_audio'] else 'NO'}. "
            f"{len(ctx.index.get('shots', []))} shots, "
            f"{len(ctx.index.get('sentences', []))} sentences / "
            f"{len(ctx.index.get('words', []))} words, "
            f"{len(sil)} silences >=0.7s totalling {total_sil:.1f}s. "
            f"Current EDL v{edl['version']}: {describe_edl(edl['json'], v['duration'])}.")


def get_transcript(ctx, start=0, end=None):
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    if end <= start:
        return "REJECTED: end must be greater than start."
    rows = [s for s in ctx.index.get("sentences", [])
            if s["t1"] > start and s["t0"] < end]
    if not rows:
        return (f"No transcribed speech between {start}s and {end}s."
                if ctx.index.get("sentences") else
                "This video has no transcript (no speech or no audio track).")
    out = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}] {s['text']}"
           for s in rows]
    # Transcripts get a much larger budget than other tools: silently losing
    # the tail of a long video is exactly how far-apart repetitions go unseen.
    return (_cap("\n".join(out), budget=config.TRANSCRIPT_CHAR_BUDGET)
            + "\n(for word-exact timing, call get_words(start, end))")


def _norm_token(w):
    return re.sub(r"[^a-z0-9']+", "", (w or "").lower())


def find_repeated_phrases(out_words, shingle=4):
    """Repeated N-word phrases in the kept program text, as
    [(phrase, [program_times])]. Consecutive repeated shingles merge into
    longer phrases so 'we just built the ultimate ai pipeline' reports once,
    not as four overlapping 4-gram hits."""
    toks = [( _norm_token(w["w"]), w["t0"]) for w in out_words]
    toks = [(t, at) for t, at in toks if t]
    if len(toks) < shingle * 2:
        return []
    counts = {}
    for i in range(len(toks) - shingle + 1):
        key = " ".join(t for t, _ in toks[i:i + shingle])
        counts.setdefault(key, []).append(i)
    rep_idx = sorted({i for idxs in counts.values() if len(idxs) > 1
                      for i in idxs})
    if not rep_idx:
        return []
    runs, s, p = [], rep_idx[0], rep_idx[0]
    for i in rep_idx[1:]:
        if i == p + 1:
            p = i
        else:
            runs.append((s, p))
            s = p = i
    runs.append((s, p))
    phrases = {}
    for a, b in runs:
        text = " ".join(t for t, _ in toks[a:b + shingle])
        phrases.setdefault(text, []).append(round(toks[a][1], 1))
    return [(t, times) for t, times in phrases.items() if len(times) > 1]


def get_kept_transcript(ctx):
    """The transcript of what the CURRENT edit actually keeps — program
    time — with repeated-phrase detection. THE tool for verifying that a
    repetition/tightening pass really removed the repeats."""
    latest = ctx.latest_edl()
    edl = latest["json"]
    tl = Timeline(edl["keep"], edl.get("inserts") or [])
    out_words = tl.kept_words(ctx.index.get("words", []))
    if not out_words:
        return ("The current edit keeps no transcribed speech."
                if ctx.index.get("words") else
                "This video has no transcript (no speech or no audio track).")
    lines, group = [], []

    def flush():
        if not group:
            return
        src0 = tl.out_to_src(group[0]["t0"])
        src1 = tl.out_to_src(group[-1]["t1"])
        src = (f" | src {_fmt_t(src0)}-{_fmt_t(src1)}"
               if src0 is not None and src1 is not None else "")
        lines.append(f"[{_fmt_t(group[0]['t0'])}-{_fmt_t(group[-1]['t1'])}"
                     f"{src}] " + " ".join(w["w"] for w in group))
        group.clear()

    for w in out_words:
        if group and (w["t0"] - group[-1]["t1"] > 0.9 or len(group) >= 14):
            flush()
        group.append(w)
    flush()
    header = (f"Program transcript of EDL v{latest['version']} "
              f"({tl.out_duration:.1f}s output — program time, with the "
              "matching source spans):")
    reps = find_repeated_phrases(out_words)
    if reps:
        rep_lines = [f"  '{text}' at " + ", ".join(f"{t}s" for t in times)
                     for text, times in reps[:6]]
        note = ("\nPOSSIBLE REPETITIONS still in the output:\n"
                + "\n".join(rep_lines)
                + "\nIf these are true repeats, cut the weaker take using "
                  "the src spans above.")
    else:
        note = "\nNo repeated phrases detected in the output."
    return _cap(header + "\n" + "\n".join(lines) + note,
                budget=config.TRANSCRIPT_CHAR_BUDGET)


GET_WORDS_MAX_RANGE_S = 60.0


def get_words(ctx, start=0, end=None):
    """Word-level timestamps straight from the index — the ONLY correct
    source for cut points inside a sentence."""
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    if end <= start:
        return "REJECTED: end must be greater than start."
    if end - start > GET_WORDS_MAX_RANGE_S + 0.01:
        return (f"REJECTED: range {end - start:.0f}s is too wide — call "
                f"get_words on ranges of {GET_WORDS_MAX_RANGE_S:.0f}s or "
                f"less (e.g. get_words({start:.0f}, "
                f"{start + GET_WORDS_MAX_RANGE_S:.0f}), then the next "
                "window). Use get_transcript to find the right region first.")
    words = ctx.index.get("words", [])
    rows = [w for w in words if w["t1"] > start and w["t0"] < end]
    if not rows:
        return (f"No transcribed words between {start}s and {end}s."
                if words else
                "This video has no transcript (no speech or no audio track).")
    out = [f"{_fmt_t(w['t0'])}-{_fmt_t(w['t1'])} {w['w']}" for w in rows]
    return _cap("\n".join(out))


def search_transcript(ctx, query):
    q = (query or "").strip().lower()
    if not q:
        return "REJECTED: query is empty."
    sentences = ctx.index.get("sentences", [])
    if not sentences:
        return "This video has no transcript to search."
    exact = [s for s in sentences if q in s["text"].lower()]
    fuzzy = []
    if len(exact) < 5:
        texts = {s["id"]: s["text"].lower() for s in sentences}
        close = difflib.get_close_matches(q, list(texts.values()), n=8,
                                          cutoff=0.5)
        hit_ids = {sid for sid, t in texts.items() if t in close}
        fuzzy = [s for s in sentences
                 if s["id"] in hit_ids and s not in exact]
    lines = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}] {s['text']}"
             for s in exact[:20]]
    lines += [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}] (similar) {s['text']}"
              for s in fuzzy[:8]]
    if not lines:
        return f"No matches for '{query}'. Try a shorter or different phrase."
    return _cap(f"{len(exact)} exact matches:\n" + "\n".join(lines))


def get_shots(ctx, start=0, end=None):
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    rows = [s for s in ctx.index.get("shots", [])
            if s["end"] > start and s["start"] < end]
    if not rows:
        return f"No shots between {start}s and {end}s."
    lines = []
    for s in rows:
        cap = s.get("caption") or {}
        desc = "; ".join(x for x in (cap.get("action"), cap.get("setting"),
                                     cap.get("people")) if x)
        ost = cap.get("on_screen_text")
        if ost:
            desc += f'; on-screen text: "{ost}"'
        lines.append(f"[#{s['id']} {_fmt_t(s['start'])}-{_fmt_t(s['end'])}] "
                     f"{desc or '(no visual caption)'}")
    return _cap("\n".join(lines))


def find_silences(ctx, min_seconds=0.7):
    try:
        min_s = max(0.1, float(min_seconds))
    except (TypeError, ValueError):
        return "REJECTED: min_seconds must be a number."
    sil = [s for s in ctx.index.get("silences", [])
           if s[1] - s[0] >= min_s]
    if not sil:
        return f"No silences of {min_s}s or longer."
    words = ctx.index.get("words", [])
    lines = []
    for s, e in sil[:100]:
        before = next((w["w"] for w in reversed(words) if w["t1"] <= s + 0.05),
                      None)
        after = next((w["w"] for w in words if w["t0"] >= e - 0.05), None)
        ctxt = ""
        if before or after:
            ctxt = f" — after '{before or '(start)'}', before '{after or '(end)'}'"
        lines.append(f"{_fmt_t(s)}-{_fmt_t(e)} ({e - s:.2f}s, midpoint "
                     f"{_fmt_t((s + e) / 2)}){ctxt}")
    note = f"\n({len(sil) - 100} more not shown)" if len(sil) > 100 else ""
    return _cap(f"{len(sil)} silences >= {min_s}s:\n" + "\n".join(lines) + note)


def list_assets(ctx, kind=None):
    """Project files the user has uploaded or the system has produced.
    kind 'audio' (the pipeline's extracted copy of the source's own audio,
    used for transcription) is deliberately excluded everywhere — offering
    it as 'music' just doubles the speaker's voice under itself."""
    kinds = {"music": ["music"], "image": ["image_ref"],
             "clip": ["video_clip"], "render": ["render"],
             "all": ["music", "image_ref", "video_clip",
                     "render", "original"]}
    sel = kinds.get((kind or "music").strip().lower())
    if not sel:
        return ("REJECTED: kind must be one of "
                f"{', '.join(sorted(kinds))}.")
    rows = ctx.db.run(dbx.assets_by_kinds, ctx.project_id, sel)
    if not rows:
        if sel == kinds["music"]:
            return ("No audio uploaded to this project — but the built-in "
                    "libraries are always available: list_music_library() "
                    "for background tracks, list_sfx_library() for one-shot "
                    "sound effects. Only ask the user to attach a file "
                    "(paperclip button in chat, mp3/wav/m4a) if they want a "
                    "specific sound the libraries do not have.")
        return f"No {kind} assets in this project."
    lines = []
    for a in rows:
        m = a.get("meta") or {}
        dur = f", {a['duration_s']:.1f}s" if a.get("duration_s") else ""
        cap = f" — {m['caption'][:120]}" if m.get("caption") else ""
        lines.append(f"[{a['kind']}] storage_key={a['storage_key']} "
                     f"\"{m.get('filename', '?')}\"{dur}{cap}")
    return _cap("\n".join(lines))


def look_at(ctx, start, end, question):
    if not llm.vision_available():
        return ("Visual inspection unavailable (no vision model configured). "
                "Decide from the transcript, silences, and shot captions.")
    try:
        s, e = ctx.clamp(start), ctx.clamp(end)
    except ValueError as err:
        return f"REJECTED: {err}"
    if e <= s:
        e = min(ctx.duration, s + 1.0)
    try:
        proxy = ctx.proxy_path()
    except Exception as err:
        return f"Cannot fetch frames right now ({err}). Decide from the index."
    n = 4 if e - s > 1.5 else 2
    frames, frame_names = [], []
    for i in range(n):
        t = s + (e - s) * (i + 0.5) / n
        fp = os.path.join(ctx.workdir, f"look_{int(t * 100)}.jpg")
        try:
            media.frame_at(proxy, t, fp)
            frames.append(fp)
            frame_names.append(f"proxy frame @{t:.2f}s")
        except media.MediaError:
            pass
    if not frames:
        return "Could not extract frames for that range."
    try:
        has_frame = bool((ctx.latest_edl()["json"].get("frame") or {})
                         .get("ratio"))
    except Exception:
        has_frame = False
    src_note = ("These frames are from the SOURCE footage — the output "
                "frame (crop/letterbox) is applied later at render, so do "
                "not judge aspect ratio here. " if has_frame else "")
    prompt = (f"{src_note}These are {len(frames)} frames sampled evenly from "
              f"{s:.2f}s to {e:.2f}s of a video. Question from the editor: "
              f"{question}\nAnswer concisely and concretely.")
    answer = llm.ask_vision(prompt, frames, purpose="vision_look",
                            image_names=frame_names)
    return _cap(answer or "The vision model did not return an answer; "
                          "proceed using the transcript and shot captions.")


def _asset_local_path(ctx, asset):
    local = ctx._asset_locals.get(asset["id"])
    if not local:
        local = os.path.join(ctx.workdir, f"asset_{asset['id']}"
                             + os.path.splitext(asset["storage_key"])[1])
        storage.download_to(asset["storage_key"], local)
        ctx._asset_locals[asset["id"]] = local
    return local


def look_at_asset(ctx, asset_key, question, start=0, end=None):
    """Frames from an UPLOADED clip or image (not the main video) — THE way
    to pick which moment of a long clip to splice in with insert_media."""
    if not llm.vision_available():
        return ("Visual inspection unavailable (no vision model configured). "
                "Ask the user which part of the clip to use.")
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref"))
    if err:
        return err
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    try:
        local = _asset_local_path(ctx, asset)
    except Exception as e:
        return f"Cannot fetch that asset right now ({e})."
    if asset["kind"] == "image_ref":
        answer = llm.ask_vision(
            f"This is the uploaded image '{name}'. Question from the "
            f"editor: {question}\nAnswer concisely and concretely.",
            [local], purpose="vision_look", image_names=[asset_key])
        return _cap(answer or "The vision model did not return an answer.")
    dur = _asset_media_duration(ctx, asset)
    try:
        s = round(min(max(float(start or 0), 0.0), dur), 2)
        e = round(min(max(float(end), s), dur), 2) if end is not None else dur
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers of seconds."
    if e <= s:
        e = min(dur, s + 1.0)
    n = 6 if e - s > 20 else 4
    frames, frame_names = [], []
    for i in range(n):
        t = s + (e - s) * (i + 0.5) / n
        fp = os.path.join(ctx.workdir, f"alook_{asset['id']}_{int(t * 10)}.jpg")
        try:
            media.frame_at(local, t, fp, width=640)
            frames.append(fp)
            frame_names.append(f"clip '{name}' frame @{t:.2f}s")
        except media.MediaError:
            pass
    if not frames:
        return "Could not extract frames from that clip."
    labels = ", ".join(f"{s + (e - s) * (i + 0.5) / n:.1f}s"
                       for i in range(len(frames)))
    answer = llm.ask_vision(
        f"These are {len(frames)} frames sampled from the uploaded clip "
        f"'{name}' ({dur:.0f}s long), at {labels}. Question from the "
        f"editor: {question}\nRefer to moments by those timestamps; answer "
        "concisely.", frames, purpose="vision_look", image_names=frame_names)
    return _cap((answer or "The vision model did not return an answer.")
                + f"\n(clip is {dur:.1f}s long; call again with a narrower "
                  "start/end to zoom into a region)")


# ------------------------------------------------------------------ #
#  WRITE tools                                                         #
# ------------------------------------------------------------------ #

def _merge_touching(spans):
    spans = sorted([list(x) for x in spans], key=lambda x: x[0])
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _write_keep(ctx, new_keep, desc, snap_to_words=False,
                check_regression=False):
    """Shared tail for every keep-modifying write: optional outward word
    snapping, the version write, then mid-word boundary warnings (and, for
    full replacements, mechanical regression warnings) appended to a still
    SUCCESSFUL result."""
    words = ctx.index.get("words", [])
    silences = ctx.index.get("silences", [])
    if snap_to_words and words:
        new_keep = audit.snap_keep_to_words(new_keep, words, ctx.duration)
    new_keep = [x for x in new_keep if x[1] - x[0] >= 0.05]
    if not new_keep:
        return "REJECTED: nothing would survive that keep list."
    prev = ctx.latest_edl()
    prev_keep = prev["json"]["keep"]
    edl = dict(prev["json"])
    edl["keep"] = new_keep
    # Inserts sit at keep boundaries; when the keep list changes, re-snap
    # each to the nearest boundary of the NEW keep so the edit stays valid.
    if edl.get("inserts"):
        bounds = keep_boundaries(new_keep)
        edl["inserts"] = [
            {**ins, "at_output_s": min(bounds,
                                       key=lambda b: abs(b - ins["at_output_s"]))}
            for ins in edl["inserts"]]
    # Everything below lives in PROGRAM time, which this write is changing. A
    # stale item that no longer fits would fail validation and reject the whole
    # write — so a pre-existing zoom could make an unrelated cut impossible.
    # Each collection follows its own anchor:
    #   zooms      - CONTENT-anchored ("push in on the skyline"): remap through
    #                the source so the zoom stays on the moment it was placed
    #                on; drop it when that footage is cut away.
    #   sfx        - CONTENT-anchored one-shot ("a whoosh on that cut"): remap
    #                the POINT through the source; drop it when that moment is
    #                cut away. Output-time units do NOT decide the anchor —
    #                zoom start/end are output time too. What the item is
    #                attached to decides it.
    #   music /    - PROGRAM-anchored ("music under the whole video", "narrate
    #   voiceover    at 10s"): clamp to the new program length.
    #   regions    - PROGRAM-anchored censor window: clamp (drop if outside).
    region_notes = []
    prog = output_duration(new_keep) + sum(
        float(i["duration_s"]) for i in edl.get("inserts") or [])
    old_tl = Timeline(prev_keep, prev["json"].get("inserts") or [])
    new_tl = Timeline(new_keep, edl.get("inserts") or [])

    fx = dict(edl.get("effects") or {})
    fx_changed = False
    if fx.get("zooms"):
        kept_zooms = []
        for z in fx["zooms"]:
            z = dict(z)
            moved = timeline_mod.remap_program_span(
                old_tl, new_tl, float(z["start"]), float(z["end"]))
            if moved is None:
                # Endpoints inside a spliced insert have no source time; only
                # a genuinely cut-away zoom maps to nothing.
                if old_tl.out_to_src(float(z["start"])) is None or \
                        old_tl.out_to_src(float(z["end"])) is None:
                    kept_zooms.append(z)
                    continue
                region_notes.append(
                    f"note: zoom {z.get('id')} was removed — the footage it "
                    "was on is no longer in the edit.")
                fx_changed = True
                continue
            ns, ne = moved
            if ne - ns < 0.2:
                region_notes.append(
                    f"note: zoom {z.get('id')} was removed — only "
                    f"{ne - ns:.2f}s of the footage it was on survives the "
                    "cut.")
                fx_changed = True
                continue
            if (ns, ne) != (z["start"], z["end"]):
                region_notes.append(
                    f"note: zoom {z.get('id')} moved to {ns}-{ne}s (output "
                    "time) so it stays on the same footage.")
                z["start"], z["end"] = ns, ne
                fx_changed = True
            kept_zooms.append(z)
        if fx_changed:
            fx["zooms"] = kept_zooms
    if fx.get("regions"):
        kept_regs = []
        for r in fx["regions"]:
            r = dict(r)
            if r.get("end") is not None and r["end"] > prog:
                if (r.get("start") or 0.0) >= prog - 0.05:
                    region_notes.append(
                        f"note: censor region {r.get('id')} was removed — "
                        "its time window falls entirely outside the "
                        "shortened edit.")
                    fx_changed = True
                    continue
                r["end"] = round(prog, 2)
                region_notes.append(
                    f"note: censor region {r.get('id')}'s time window now "
                    f"ends at {r['end']}s to fit the shortened edit.")
                fx_changed = True
            kept_regs.append(r)
        if fx_changed:
            fx["regions"] = kept_regs
    if fx_changed:
        edl["effects"] = fx

    if edl.get("music"):
        kept_music = []
        for m in edl["music"]:
            m = dict(m)
            if m["end"] > prog:
                if m["start"] >= prog - 0.1:
                    region_notes.append(
                        f"note: music {m.get('id')} was removed — it starts "
                        "after the end of the shortened edit.")
                    continue
                m["end"] = round(prog, 2)
                region_notes.append(
                    f"note: music {m.get('id')} now ends at {m['end']}s to "
                    "fit the shortened edit.")
            kept_music.append(m)
        edl["music"] = kept_music
    if edl.get("voiceover"):
        kept_vo = []
        for v in edl["voiceover"]:
            v = dict(v)
            if v["start_output_s"] > max(0.0, prog - 0.05):
                region_notes.append(
                    f"note: voiceover {v.get('id')} was removed — it starts "
                    "after the end of the shortened edit.")
                continue
            kept_vo.append(v)
        edl["voiceover"] = kept_vo
    if edl.get("sfx"):
        # CONTENT-anchored, like a zoom — NOT program-anchored like music. The
        # prompt tells the agent to land a whoosh ON a cut point and an impact
        # ON the reveal, so the sound belongs to a moment in the footage and
        # has to follow it. Left in program time it silently drifts by the
        # length of every cut made before it: trim 10s off the front and the
        # whoosh that was on the cut now fires 10s into the next take, with no
        # note, while write_edl still reports success.
        #
        # A point, not a span, so remap_program_span is no use here — a
        # zero-length span maps to no output pieces and returns None. Map the
        # point itself through the source.
        kept_sfx = []
        for s in edl["sfx"]:
            s = dict(s)
            at = float(s["at"])
            src = old_tl.out_to_src(at)
            # No source time means the point sits inside a spliced insert;
            # those keep their program position.
            new_at = new_tl.src_to_out(src) if src is not None else at
            if new_at is None:
                region_notes.append(
                    f"note: sound effect {s.get('id')} was removed — the "
                    "moment it was placed on is no longer in the edit.")
                continue
            if abs(new_at - at) > 0.01:
                region_notes.append(
                    f"note: sound effect {s.get('id')} moved to "
                    f"{round(new_at, 2)}s so it stays on the same moment.")
            s["at"] = round(new_at, 2)
            # A point past the end of a shortened edit is dropped, not
            # clamped: clamping would pile every orphan onto the last frame.
            # Without this the sfx bounds check in validate_edl rejects the
            # whole CUT — the user asks to trim the end and is told the edit
            # is invalid, over a sound they never mentioned.
            if s["at"] > max(0.0, prog - 0.05):
                region_notes.append(
                    f"note: sound effect {s.get('id')} was removed — it sits "
                    "after the end of the shortened edit.")
                continue
            kept_sfx.append(s)
        edl["sfx"] = kept_sfx

    result = ctx.write_edl(edl, desc)
    if not result.startswith("EDL v"):
        return result
    if region_notes:
        result += "\n" + "\n".join(region_notes)
    warn = audit.boundary_warning_lines(new_keep, words, silences,
                                        ctx.duration)
    if snap_to_words:
        warn = []   # snapping guarantees word-clean boundaries
    if check_regression:
        warn += audit.regression_warnings(prev_keep, new_keep, ctx.index)
    # A write that silently drops most of the kept footage is almost always
    # the model chasing something the user never asked for — make the scale
    # of the loss impossible to miss (keep_segments AND cut_range alike).
    prev_dur = output_duration(prev_keep)
    new_dur = output_duration(new_keep)
    if prev_dur > 1.0 and new_dur < prev_dur * 0.5:
        warn.append(
            f"WARNING (large drop): this removed "
            f"{prev_dur - new_dur:.1f}s of the {prev_dur:.1f}s that was "
            f"kept ({100 - 100 * new_dur / prev_dur:.0f}% of the edit). "
            "If the user did not EXPLICITLY ask to shorten the video "
            "this much, put the footage back with keep_segments using "
            "the previous list from get_edl.")
    if warn:
        result += "\n" + "\n".join(warn)
    return result


def keep_segments(ctx, segments, snap_to_words=False):
    if not isinstance(segments, list) or not segments:
        return "REJECTED: segments must be a non-empty array of [start, end]."
    cleaned = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, (list, tuple)) or len(seg) != 2:
            return f"REJECTED: segments[{i}] must be [start, end], got {seg}."
        try:
            s, e = ctx.clamp(seg[0]), ctx.clamp(seg[1])
        except ValueError as err:
            return f"REJECTED: segments[{i}]: {err}"
        cleaned.append([s, e])
    merged = _merge_touching(cleaned)
    kept = output_duration(merged)
    return _write_keep(
        ctx, merged,
        f"keep set to {len(merged)} segment(s), {kept}s of "
        f"{ctx.duration}s survives",
        snap_to_words=bool(snap_to_words), check_regression=True)


def cut_range(ctx, start, end, snap_to_words=False):
    try:
        s, e = ctx.clamp(start), ctx.clamp(end)
    except ValueError as err:
        return f"REJECTED: {err}"
    if e - s < 0.05:
        return "REJECTED: the range to cut must be at least 0.05s."
    cur = ctx.latest_edl()["json"]["keep"]
    new = [list(x) for x in audit.subtract_spans(cur, [[s, e]])]
    if not new:
        return ("REJECTED: cutting {:.2f}-{:.2f} would remove everything "
                "that's currently kept.".format(s, e))
    return _write_keep(ctx, new, f"cut {s}-{e}s ({e - s:.2f}s removed)",
                       snap_to_words=bool(snap_to_words))


def restore_range(ctx, start, end, snap_to_words=False):
    try:
        s, e = ctx.clamp(start), ctx.clamp(end)
    except ValueError as err:
        return f"REJECTED: {err}"
    if e - s < 0.05:
        return "REJECTED: the range to restore must be at least 0.05s."
    cur = ctx.latest_edl()["json"]["keep"]
    new = _merge_touching([list(x) for x in cur] + [[s, e]])
    return _write_keep(ctx, new, f"restored {s}-{e}s to the edit",
                       snap_to_words=bool(snap_to_words))


# Non-lexical hesitation sounds only — safe to remove without changing meaning.
# Deliberately EXCLUDED from the default: words that are sometimes fillers but
# often meaningful — "like"/"you know"/"basically" (mangle sentences) and the
# back-channel affirmations "mm"/"mmm"/"hmm"/"uh-huh" (cutting them deletes a
# speaker's "yes"). The caller can still target any of these via a custom list.
FILLER_WORDS_DEFAULT = ("um", "umm", "ummm", "uh", "uhh", "uhm", "er", "err",
                        "erm", "ah", "ahh")


def _norm_word(w):
    return re.sub(r"[^a-z]", "", str(w or "").lower())


def cut_silences(ctx, min_silence_s=0.5, padding_s=0.12):
    """One-call silence trim: cut every detected pause at least min_silence_s
    long, keeping padding_s of breathing room around speech, snapped to word
    edges. Replaces the fragile find_silences -> N× cut_range plan."""
    try:
        min_s = max(0.15, float(min_silence_s))
    except (TypeError, ValueError):
        return "REJECTED: min_silence_s must be a number of seconds."
    try:
        pad = max(0.0, float(padding_s))
    except (TypeError, ValueError):
        return "REJECTED: padding_s must be a number of seconds."
    sil = [s for s in ctx.index.get("silences", []) if s[1] - s[0] >= min_s]
    if not sil:
        return (f"No silences of {min_s}s or longer were detected — the video "
                "is already tight, so nothing was cut.")
    cuts = []
    for s, e in sil:
        cs, ce = round(s + pad, 2), round(e - pad, 2)
        if ce - cs >= 0.1:
            cuts.append([cs, ce])
    if not cuts:
        return (f"Found {len(sil)} silence(s), but each is too short to trim "
                f"once {pad}s of padding is kept around speech. Nothing was "
                "cut (lower padding_s to trim more aggressively).")
    cur = ctx.latest_edl()["json"]["keep"]
    new = [list(x) for x in audit.subtract_spans(cur, cuts)]
    if not new:
        return ("REJECTED: cutting every detected silence would remove the "
                "whole video. Inspect find_silences and cut a narrower set.")
    removed = output_duration(cur) - output_duration(new)
    return _write_keep(
        ctx, new,
        f"cut {len(cuts)} silence gap(s) >= {min_s}s ({removed:.1f}s removed, "
        f"{pad}s kept around speech)",
        snap_to_words=True)


def remove_filler_words(ctx, words=None):
    """One-call filler removal: cut every 'um'/'uh'/etc. from the edit using
    the real word timestamps. Deterministic — no estimation. A custom `words`
    entry may be a single word OR a multi-word phrase ("you know"), matched as
    a consecutive run of transcript words."""
    raw = words if isinstance(words, list) and words \
        else list(FILLER_WORDS_DEFAULT)
    singles, phrases = set(), []
    for entry in raw:
        toks = [t for t in (_norm_word(t) for t in str(entry).split()) if t]
        if not toks:
            continue
        if len(toks) == 1:
            singles.add(toks[0])
        else:
            phrases.append(toks)
    if not singles and not phrases:
        return "REJECTED: provide at least one filler word to remove."
    all_words = ctx.index.get("words", [])
    if not all_words:
        return ("REJECTED: this video has no transcript (no speech detected), "
                "so there are no filler words to remove.")
    norm = [_norm_word(w.get("w")) for w in all_words]
    cuts, hits = [], {}
    for idx, tok in enumerate(norm):
        if tok in singles:
            cuts.append([round(all_words[idx]["t0"], 2),
                         round(all_words[idx]["t1"], 2)])
            hits[tok] = hits.get(tok, 0) + 1
    for ph in phrases:
        n = len(ph)
        for start in range(0, len(norm) - n + 1):
            if norm[start:start + n] == ph:
                cuts.append([round(all_words[start]["t0"], 2),
                             round(all_words[start + n - 1]["t1"], 2)])
                key = " ".join(ph)
                hits[key] = hits.get(key, 0) + 1
    if not cuts:
        wanted = sorted(singles) + [" ".join(p) for p in phrases]
        return (f"No filler words {wanted} were found in the "
                "transcript, so nothing was removed.")
    cuts = _merge_touching(cuts)
    cur = ctx.latest_edl()["json"]["keep"]
    new = [list(x) for x in audit.subtract_spans(cur, cuts)]
    if not new:
        return ("REJECTED: removing those words would remove the whole "
                "video — check your custom word list.")
    summary = ", ".join(f"'{k}'×{v}" for k, v in sorted(hits.items()))
    return _write_keep(
        ctx, new,
        f"removed {len(cuts)} filler-word span(s) ({summary})")


def _parse_style(style):
    """Validate a style dict -> normalized dict, None for absent, or an
    error string. Legacy string styles ('default') mean absent."""
    if style is None or isinstance(style, str) or style == {}:
        return None
    if not isinstance(style, dict):
        return ("ERR: style must be an object like "
                '{"color":"#FF0000","size":"l","position":"top"}')
    try:
        return CaptionStyle.model_validate(style).model_dump()
    except Exception as e:
        return (f"ERR: bad style: {str(e)[:160]}. Use "
                '{"color":"#RRGGBB","size":"s|m|l|xl",'
                '"position":"bottom|top|middle","dynamic":true|false} '
                '(all fields optional).')


def add_captions(ctx, mode=None, items=None, style=None,
                 max_words_per_caption=None, emphasis_words=None):
    edl = dict(ctx.latest_edl()["json"])
    parsed_style = _parse_style(style)
    if isinstance(parsed_style, str):
        return "REJECTED: " + parsed_style[5:]
    if emphasis_words is not None:
        if not isinstance(emphasis_words, list) \
                or not all(isinstance(w, str) for w in emphasis_words):
            return ("REJECTED: emphasis_words must be an array of strings "
                    "(words from the transcript to emphasize).")
    if items:
        if not isinstance(items, list):
            return "REJECTED: items must be an array of {text,start,end}."
        norm = []
        for i, it in enumerate(items):
            if not isinstance(it, dict) or "text" not in it:
                return f"REJECTED: items[{i}] must be {{text,start,end}}."
            item_style = _parse_style(it.get("style")) or parsed_style
            if isinstance(item_style, str):
                return f"REJECTED: items[{i}]: {item_style[5:]}"
            try:
                norm.append({"text": str(it["text"]),
                             "start": ctx.clamp(it.get("start", 0)),
                             "end": ctx.clamp(it.get("end", 0)),
                             "style": item_style})
            except ValueError as err:
                return f"REJECTED: items[{i}]: {err}"
        edl["captions"] = norm
        return ctx.write_edl(edl, f"{len(norm)} manual caption(s) set")
    if mode in (None, "", "from_transcript"):
        mw = None
        if max_words_per_caption is not None:
            try:
                mw = int(max_words_per_caption)
            except (TypeError, ValueError):
                return "REJECTED: max_words_per_caption must be an integer."
        preset = (parsed_style or {}).get("preset")
        premium = preset and preset != "classic"
        # karaoke groups larger than the hard max read as a wall of text —
        # clamp the STORED value so EDL, diff line and reply all match what
        # actually renders, and disclose the clamp. Premium presets chunk
        # with their own budgets, so the clamp is legacy-dynamic only.
        karaoke_note = ""
        if mw and not premium and (parsed_style or {}).get("dynamic") \
                and mw > KARAOKE_HARD_MAX:
            karaoke_note = (f"\nNote: dynamic (karaoke) captions group at "
                            f"most {KARAOKE_HARD_MAX} words per line — "
                            f"using {KARAOKE_HARD_MAX} instead of {mw}.")
            mw = KARAOKE_HARD_MAX
        if not premium and (parsed_style or {}).get("dynamic") \
                and (parsed_style or {}).get("animation"):
            karaoke_note += ("\nNote: dynamic karaoke captions animate "
                             "word-by-word already — the 'animation' "
                             "entrance style only applies to static "
                             "captions and is ignored here.")
        if premium and (parsed_style or {}).get("dynamic"):
            karaoke_note += (f"\nNote: preset '{preset}' drives its own "
                             "word-by-word animation — the 'dynamic' flag "
                             "is ignored while a preset is set.")
        if premium and (parsed_style or {}).get("animation") \
                and preset != "elegant":
            karaoke_note += (f"\nNote: preset '{preset}' animates word-by-"
                             "word — the 'animation' entrance style only "
                             "applies to static looks and is ignored here.")
        if emphasis_words and not premium:
            karaoke_note += ("\nNote: emphasis_words only take effect with "
                            "a premium preset (podcast/beast/karaoke/"
                            "elegant) — pass style {preset:'podcast'} to "
                            "use them.")
        if (parsed_style or {}).get("uppercase") is not None and not premium:
            karaoke_note += ("\nNote: uppercase only applies with a premium "
                             "preset — the classic look renders the "
                             "transcript as spoken.")
        if preset == "elegant" \
                and (parsed_style or {}).get("animation") == "slide_up":
            karaoke_note += ("\nNote: premium captions place text "
                             "explicitly, which replaces 'slide_up' with a "
                             "fade entrance.")
        # Honesty gate: from_transcript captions can only show words that
        # exist AND survive the cut. A real music-heavy upload transcribed to
        # ONE hallucinated word that the edit then cut — the agent told the
        # user captions were on and the render showed nothing.
        all_words = ctx.index.get("words") or []
        keep_spans = edl.get("keep") or []
        visible = sum(
            1 for w in all_words
            if any(s - 0.05 <= (float(w["t0"]) + float(w["t1"])) / 2 <= e + 0.05
                   for s, e in keep_spans))
        if not all_words:
            karaoke_note += (
                "\nWARNING: the transcript is EMPTY — nothing was "
                "transcribed from this video, so these captions will show NO "
                "text at all. Tell the user honestly that no clear speech "
                "was detected (music-only videos transcribe to nothing) "
                "instead of claiming captions were added.")
        elif visible == 0:
            karaoke_note += (
                f"\nWARNING: none of the transcript's {len(all_words)} "
                "word(s) fall inside the kept footage — these captions will "
                "not be visible in this cut. Either the speech was cut out, "
                "or the video has almost no transcribable speech. Tell the "
                "user honestly.")
        elif visible < 5:
            karaoke_note += (
                f"\nNote: only {visible} transcribed word(s) fall inside the "
                "kept footage, so captions will be very sparse — if this "
                "video is mostly music, say so to the user.")
        cfg = {"mode": "from_transcript",
               "max_words_per_caption": mw,
               "style": parsed_style}
        if emphasis_words:
            cfg["emphasis_words"] = emphasis_words
        edl["captions"] = cfg
        desc = "captions from transcript enabled"
        if premium:
            desc += f", preset {preset}"
        if mw:
            desc += f", <= {mw} words each"
        if emphasis_words:
            desc += f", {len(emphasis_words)} emphasis words"
        if parsed_style:
            desc += f", style {parsed_style}"
        return ctx.write_edl(edl, desc) + karaoke_note
    if mode == "off":
        edl["captions"] = None
        return ctx.write_edl(edl, "captions removed")
    return ("REJECTED: mode must be 'from_transcript' or 'off', or pass "
            "items=[{text,start,end}].")


def _parse_partial_style(style):
    """Validate a PARTIAL style patch, returning only the provided keys
    (normalized), or an 'ERR: ...' string. Unlike _parse_style this never
    fills defaults, so merging cannot reset fields the user didn't mention."""
    if not isinstance(style, dict) or not style:
        return ('ERR: style must be a non-empty object with any of '
                '{"preset":"podcast|beast|karaoke|elegant|stacked|iridescent|chrome|editorial|fashion|luxe|impact|classic",'
                '"color":"#RRGGBB","size":"s|m|l|xl","size_scale":0.5-3.0,'
                '"position":"bottom|top|middle","uppercase":true|false,'
                '"dynamic":true|false,"highlight_color":"#RRGGBB",'
                '"animation":"fade|pop|slide_up|punch|blur_in|whip|flash|rise|drop",'
                '"font":"<bundled family>","effect":"chroma|chrome|glow",'
                '"layout":"stack|flow","leading":0.5-2.2,'
                '"emphasis":"big|huge|accent|pop|box|serif|chrome|glow|chroma",'
                '"emphasis_scale":1.0-3.0}')
    # Mirrors captions.STYLE_KEYS (+ dynamic/uppercase, which are booleans
    # handled separately there). A field missing HERE is rejected outright;
    # a field missing from STYLE_KEYS is accepted and then silently ignored.
    unknown = sorted(set(style) - {"color", "size", "size_scale", "position",
                                   "dynamic", "highlight_color", "animation",
                                   "preset", "uppercase", "font", "effect",
                                   "layout", "leading", "emphasis",
                                   "emphasis_scale"})
    if unknown:
        return (f"ERR: unknown style field(s) {unknown} — the style fields are "
                "preset, color, size, size_scale, position, uppercase, "
                "dynamic, highlight_color, animation, font, effect, layout, "
                "leading, emphasis and emphasis_scale. preset picks a look "
                "(podcast/beast/karaoke/elegant/stacked/iridescent/chrome/editorial/fashion/luxe/impact/classic); "
                "font names a bundled family (e.g. 'Playfair Display Black'); "
                "effect layers chroma/chrome/glow onto emphasised words; "
                "layout 'stack' gives each line its own position, which is "
                "what lets leading go below 1.0 so lines overlap; emphasis "
                "chooses what emphasis words get ('big' = size only, no "
                "colour change); emphasis_scale is how much bigger they go.")
    try:
        validated = CaptionStyle.model_validate(style).model_dump()
    except Exception as e:
        return (f"ERR: bad style: {str(e)[:160]}. Use "
                '{"preset":"podcast|beast|karaoke|elegant|stacked|iridescent|chrome|editorial|fashion|luxe|impact|classic",'
                '"color":"#RRGGBB","size":"s|m|l|xl",'
                '"position":"bottom|top|middle","dynamic":true|false,'
                '"highlight_color":"#RRGGBB","leading":0.5-2.2,'
                '"emphasis_scale":1.0-3.0,"animation":"fade|pop|slide_up|punch|blur_in|whip|flash|rise|drop"}.')
    return {k: validated[k] for k in style}


def merge_caption_style(captions, partial):
    """Merge a partial style into an existing captions value (from_transcript
    dict or manual item list). Returns the new captions value.

    Applying a premium preset ADOPTS the preset's own placement unless the
    patch names one: stored styles auto-filled position:'bottom' for as long
    as styling has existed, and that stale default would pin every preset to
    the bottom of the frame on existing projects."""
    drop_pos = partial.get("preset") and partial["preset"] != "classic" \
        and "position" not in partial
    if isinstance(captions, dict):
        new = dict(captions)
        st = dict(captions.get("style") or {})
        st.update(partial)
        if drop_pos:
            st.pop("position", None)
        new["style"] = st
        return new
    out = []
    # dynamic word-pop (and its highlight color) only exists for
    # from_transcript captions — writing it into manual items would let the
    # reply claim an effect the renderer ignores.
    item_partial = {k: v for k, v in partial.items()
                    if k not in ("dynamic", "highlight_color")}
    for it in captions:
        nit = dict(it)
        st = dict(it.get("style") or {})
        st.update(item_partial)
        if drop_pos:
            st.pop("position", None)
        nit["style"] = st
        out.append(nit)
    return out


def set_caption_style(ctx, style=None, emphasis_words=None):
    if emphasis_words is not None:
        if not isinstance(emphasis_words, list) \
                or not all(isinstance(w, str) for w in emphasis_words):
            return ("REJECTED: emphasis_words must be an array of strings "
                    "(words from the transcript to emphasize).")
    partial = {}
    if style not in (None, {}):
        partial = _parse_partial_style(style)
        if isinstance(partial, str):
            return "REJECTED: " + partial[5:]
    elif emphasis_words is None:
        return ("REJECTED: pass style with the fields to change, "
                "emphasis_words (with a premium preset), or both.")
    edl = dict(ctx.latest_edl()["json"])
    caps = edl.get("captions")
    if not caps:
        return ("REJECTED: no captions exist yet — call "
                "add_captions(mode='from_transcript') first (you can pass "
                "a style there directly).")
    merged = merge_caption_style(caps, partial)
    # the EFFECTIVE premium preset after the patch ('classic' = legacy)
    eff_preset = None
    if isinstance(merged, dict):
        eff_preset = (merged.get("style") or {}).get("preset")
        if eff_preset == "classic":
            eff_preset = None
    emph_note = ""
    if emphasis_words is not None:
        if isinstance(merged, dict):
            merged["emphasis_words"] = emphasis_words or None
            if emphasis_words and not eff_preset:
                emph_note = ("\nNote: emphasis_words only take effect with "
                             "a premium preset (podcast/beast/karaoke/"
                             "elegant) — set style {preset:'podcast'} to "
                             "use them.")
        else:
            emph_note = ("\nNote: emphasis_words apply to from_transcript "
                         "captions only — manual caption items ignore them.")
    # turning karaoke on with a stored group size above the render's hard
    # max: clamp the stored value so state and output agree, and say so.
    karaoke_note = ""
    if isinstance(merged, dict) and partial.get("dynamic") \
            and not eff_preset \
            and (merged.get("max_words_per_caption") or 0) > KARAOKE_HARD_MAX:
        karaoke_note = (f"\nNote: dynamic (karaoke) captions group at most "
                        f"{KARAOKE_HARD_MAX} words per line — "
                        f"max_words_per_caption lowered from "
                        f"{merged['max_words_per_caption']} to "
                        f"{KARAOKE_HARD_MAX}.")
        merged["max_words_per_caption"] = KARAOKE_HARD_MAX
    if partial.get("animation"):
        eff_style = (merged.get("style") or {}) if isinstance(merged, dict) \
            else {}
        if eff_preset and eff_preset != "elegant":
            karaoke_note += (f"\nNote: preset '{eff_preset}' animates "
                             "word-by-word — the 'animation' entrance "
                             "style only applies to static looks and is "
                             "ignored here.")
        elif eff_preset == "elegant" \
                and partial["animation"] == "slide_up":
            karaoke_note += ("\nNote: premium captions place text "
                            "explicitly, which replaces 'slide_up' with a "
                            "fade entrance.")
        elif not eff_preset and eff_style.get("dynamic"):
            karaoke_note += ("\nNote: dynamic karaoke captions animate "
                             "word-by-word already — the 'animation' "
                             "entrance style only applies to static "
                             "captions and is ignored while dynamic is on.")
    if partial.get("uppercase") is not None and not eff_preset:
        karaoke_note += ("\nNote: uppercase only applies with a premium "
                         "preset — the classic look renders the transcript "
                         "as spoken.")
    if partial.get("dynamic") and eff_preset:
        karaoke_note += (f"\nNote: preset '{eff_preset}' drives its own "
                         "word-by-word animation — the 'dynamic' flag is "
                         "ignored while a preset is set.")
    edl["captions"] = merged
    desc = f"caption style updated: {json.dumps(partial)}" if partial \
        else f"caption emphasis words set ({len(emphasis_words or [])})"
    result = ctx.write_edl(edl, desc)
    result += karaoke_note + emph_note
    if isinstance(caps, list) and ({"dynamic", "highlight_color"}
                                   & set(partial)):
        result += ("\nNote: dynamic karaoke captions (and highlight_color) "
                   "only apply to from_transcript captions — manual caption "
                   "items ignore those fields.")
    return result


def _resolve_music(ctx, storage_key):
    """(track, error) for a music reference.

    Two disjoint doors. A `library:` reference is looked up in the bundled
    CC0 catalog by EXACT membership and never touches the assets table;
    anything else falls through to the project-asset guard below, which is
    unchanged — including the check that catches the pipeline's own extracted
    speech track, the cause of the original inaudible-music bug."""
    if music_library.is_library_ref(storage_key):
        t = music_library.resolve(storage_key)
        if not t:
            have = ", ".join(x["slug"] for x in music_library.CATALOG[:10])
            return None, (
                f"REJECTED: '{storage_key}' is not a track in the built-in "
                f"library. Call list_music_library() and use a slug it "
                f"returns — never invent one. Known slugs: {have or 'none'}.")
        return {"name": t["title"], "duration_s": t.get("duration_s"),
                "library": True}, None

    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio "
            "track (a transcription artifact), not background music — "
            "mixing it in would only double the speaker's voice under "
            "itself, near-inaudibly. Use a real music file instead: "
            "list_music_library() for a built-in track, or "
            "list_assets(kind='music') for the user's own uploads.")
    if not asset or asset["kind"] != "music":
        avail = ctx.db.run(
            lambda conn: _music_assets(conn, ctx.project_id))
        hint = ("Available music storage_keys: " +
                "; ".join(a["storage_key"] for a in avail)
                if avail else "No music uploaded to this project — call "
                              "list_music_library() for built-in tracks.")
        return None, f"REJECTED: '{storage_key}' is not a music asset here. {hint}"
    return {"name": os.path.basename(storage_key),
            "duration_s": asset.get("duration_s"), "library": False}, None


def list_music_library(ctx, mood=None):
    """Browse the built-in CC0 tracks. Every one is cleared for use in an
    exported video, so no upload is needed to score an edit."""
    if not music_library.CATALOG:
        return ("The built-in music library is empty in this deployment. "
                "Use list_assets(kind='music') for the user's own uploads, "
                "or ask them to attach a file.")
    m = (mood or "").strip().lower()
    if m and m not in music_library.MOODS:
        return (f"REJECTED: unknown mood '{mood}'. Available moods: "
                + ", ".join(music_library.MOODS))
    hits = music_library.browse(m or None)
    if not hits:
        return (f"No '{m}' tracks. Available moods: "
                + ", ".join(sorted({t['mood'] for t in music_library.CATALOG})))
    head = (f"{len(hits)} built-in track(s)"
            + (f" for mood '{m}'" if m else "") +
            ". Pass the library: reference to add_music.\n")
    return head + "\n".join(
        f"  library:{t['slug']} — {music_library.describe(t)}" for t in hits)


def add_music(ctx, storage_key, start=None, end=None, gain_db=-18.0,
              duck=True, offset_s=None, fade_in_s=None, fade_out_s=None,
              loop=True):
    track, err = _resolve_music(ctx, storage_key)
    if err:
        return err
    edl = dict(ctx.latest_edl()["json"])
    # Clamp against the FINAL program duration (kept footage + inserts), not
    # just the kept footage — otherwise music can never reach the end of a
    # video that has clips/images spliced in. Matches add_zoom / add_voiceover.
    out_dur = program_duration(edl)
    # "Add some music" usually means UNDER THE WHOLE THING. Defaulting to the
    # full program means the agent doesn't have to invent numbers for the
    # commonest request, and can't quietly score only the first 15 seconds.
    if start is None:
        start = 0.0
    if end is None:
        end = out_dur
    try:
        s = round(min(max(float(start), 0.0), max(0.0, out_dur - 0.1)), 2)
        e = round(min(max(float(end), s + 0.1), out_dur), 2)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers (OUTPUT-timeline seconds)."
    try:
        g = float(gain_db)
    except (TypeError, ValueError):
        return "REJECTED: gain_db must be a number."
    span = e - s
    try:
        off = max(0.0, float(offset_s)) if offset_s is not None else None
    except (TypeError, ValueError):
        return "REJECTED: offset_s must be a number (seconds into the track)."
    # An offset past the end of the track would render pure silence, so the
    # renderer ignores it. Reject rather than store a number we know will be
    # discarded — otherwise get_edl shows an offset the render never applies.
    _td = track.get("duration_s")
    if off and _td and off >= _td - 0.05:
        return (f"REJECTED: offset_s {off:.1f}s is at or past the end of "
                f"'{track['name']}' ({_td:.0f}s) — it would play silence. "
                f"Pick an offset below {_td:.0f}s.")
    # Music that starts and stops dead sounds like a mistake. Fade by default;
    # the agent can pass 0 to defeat it.
    try:
        fi = 1.0 if fade_in_s is None else max(0.0, float(fade_in_s))
        fo = 2.0 if fade_out_s is None else max(0.0, float(fade_out_s))
    except (TypeError, ValueError):
        return "REJECTED: fade_in_s/fade_out_s must be numbers (seconds)."
    fi, fo = min(fi, span / 2), min(fo, span / 2)

    music = [dict(m) for m in (edl.get("music") or [])]
    item = {"id": _next_item_id(music, "mus"), "storage_key": storage_key,
            "start": s, "end": e, "gain_db": g, "duck": bool(duck),
            "offset_s": off, "fade_in_s": fi or None,
            "fade_out_s": fo or None, "loop": True if loop else None}
    music.append(item)
    edl["music"] = music
    res = ctx.write_edl(
        edl, f"music '{track['name']}' at {s}-{e}s "
             f"(output timeline), {g}dB, duck={bool(duck)} [{item['id']}]")

    # Tell the agent what the track can actually cover, so it reports the
    # truth rather than assuming the span got filled.
    tdur = track.get("duration_s")
    if tdur and not str(res).startswith("REJECTED"):
        covered = tdur - (off or 0.0)
        if covered < span - 0.05:
            res += (f"\nNote: the track is {tdur:.0f}s"
                    + (f" ({covered:.0f}s from the {off:.0f}s offset)"
                       if off else "")
                    + f" but the span is {span:.0f}s — "
                    + ("it will repeat to fill it." if loop else
                       "it will fall SILENT for the rest. Pass loop=true "
                       "to fill the span."))
    dup_vo = [v.get("id") for v in (edl.get("voiceover") or [])
              if v.get("asset_key") == storage_key]
    if dup_vo and not str(res).startswith("REJECTED"):
        res += (f"\nWARNING: this same file is also active as voiceover "
                f"{', '.join(dup_vo)} — it will play TWICE. If you meant to "
                f"replace it, call remove_voiceover('{dup_vo[0]}').")
    return res


def remove_music(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(m) for m in (edl.get("music") or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id") or "?" for m in items) or "none"
        return (f"REJECTED: no music with id '{id}'. Existing music ids: "
                f"{have}. Call get_edl to see them.")
    edl["music"] = [m for m in items if m.get("id") != id]
    return ctx.write_edl(
        edl, f"removed music {id} "
             f"('{_music_name(hit['storage_key'])}', "
             f"{hit['start']}-{hit['end']}s)")


def _music_name(key):
    """Display name for a music reference. Library refs aren't paths, so
    basename() would print the raw 'library:slug' at the user."""
    t = music_library.resolve(key)
    if t:
        return t["title"]
    return os.path.basename(key or "?")


def _track_name(key):
    """Display name for ANY audio reference — music, sfx or upload. Both
    bundled schemes resolve to a real title; everything else is a path."""
    for lib in (music_library, sfx_library):
        t = lib.resolve(key)
        if t:
            return t["title"]
    return os.path.basename(key or "?")


def _resolve_sfx(ctx, storage_key):
    """(sound, error) for an sfx reference.

    A structural twin of _resolve_music, and deliberately just as strict. Two
    disjoint doors: an `sfx:` reference is EXACT-membership lookup in the
    bundled pack and never touches the assets table; anything else must be a
    project-owned audio asset. There is no third door, and no prefix matching:
    the renderer downloads whatever key it is handed with no project scoping,
    so a loose check here is a read primitive over the whole bucket.

    Uploaded sounds arrive as kind 'music' — an uploaded audio file is just an
    audio file, and whether it is a bed or a one-shot is an EDL decision, not
    an asset kind. So there is no separate 'sfx' upload kind to keep in sync.
    """
    if sfx_library.is_library_ref(storage_key):
        s = sfx_library.resolve(storage_key)
        if not s:
            have = ", ".join(x["slug"] for x in sfx_library.CATALOG[:12])
            return None, (
                f"REJECTED: '{storage_key}' is not a sound in the built-in "
                f"pack. Call list_sfx_library() and use a slug it returns — "
                f"never invent one. Known slugs: {have or 'none'}.")
        return {"name": s["title"], "duration_s": s.get("duration_s"),
                "library": True}, None

    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio track "
            "(a transcription artifact), not a sound effect. Use "
            "list_sfx_library() for a built-in sound, or "
            "list_assets(kind='music') for the user's own uploads.")
    if not asset or asset["kind"] != "music":
        return None, (
            f"REJECTED: '{storage_key}' is not an audio asset in this "
            "project. Call list_sfx_library() for the built-in pack, or "
            "list_assets(kind='music') for the user's uploads.")
    return {"name": os.path.basename(storage_key),
            "duration_s": asset.get("duration_s"), "library": False}, None


def list_sfx_library(ctx, category=None):
    """Browse the built-in sound-effects pack — the clicks, whooshes, impacts
    and risers that carry short-form video. Every one is ours outright, so no
    upload is needed."""
    if not sfx_library.CATALOG:
        return ("The built-in sound-effects pack is empty in this "
                "deployment. Use list_assets(kind='music') for the user's own "
                "uploads, or ask them to attach a sound.")
    c = (category or "").strip().lower()
    if c and c not in sfx_library.CATEGORIES:
        return (f"REJECTED: unknown category '{category}'. Available: "
                + ", ".join(sfx_library.CATEGORIES))
    hits = sfx_library.browse(c or None)
    if not hits:
        return (f"No '{c}' sounds. Available categories: "
                + ", ".join(sorted({t["category"] for t in sfx_library.CATALOG})))
    head = (f"{len(hits)} built-in sound(s)"
            + (f" in category '{c}'" if c else "") +
            ". Pass the sfx: reference to add_sfx.\n")
    return head + "\n".join(
        f"  sfx:{t['slug']} — {sfx_library.describe(t)}" for t in hits)


def add_sfx(ctx, storage_key, at, gain_db=-6.0):
    """Place a one-shot sound at a point in the program timeline."""
    sound, err = _resolve_sfx(ctx, storage_key)
    if err:
        return err
    try:
        at = float(at)
    except (TypeError, ValueError):
        return f"REJECTED: at must be a number of seconds, got {at!r}."
    try:
        gain_db = float(gain_db)
    except (TypeError, ValueError):
        return f"REJECTED: gain_db must be a number, got {gain_db!r}."
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if at < 0 or at > max(0.0, prog - 0.05):
        return (f"REJECTED: at={at}s is outside the program "
                f"(0 to {round(prog, 2)}s). Sound effects are placed in "
                "program time — the edited timeline, not source time.")
    items = [dict(s) for s in (edl.get("sfx") or [])]
    # Lowest free index, not len+1: after removing sx1 from [sx1, sx2], len+1
    # is "sx2" — already taken — and a suffix loop would mint "sx2x".
    taken = {s.get("id") for s in items}
    n = 1
    while f"sx{n}" in taken:
        n += 1
    sid = f"sx{n}"
    items.append({"id": sid, "storage_key": storage_key,
                  "at": round(at, 2), "gain_db": gain_db})
    edl["sfx"] = items
    note = ""
    dur = sound.get("duration_s")
    # An honest heads-up rather than a silent truncation: the renderer's amix
    # is duration=first, so a tail running past the program end is simply cut.
    if dur and at + dur > prog + 0.05:
        note = (f" NOTE: '{sound['name']}' is {dur:.2f}s and the program ends "
                f"at {round(prog, 2)}s, so its tail will be cut short.")
    return ctx.write_edl(
        edl, f"added sfx '{sound['name']}' at {round(at, 2)}s "
             f"({gain_db:+g}dB) as {sid}") + note


def remove_sfx(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(s) for s in (edl.get("sfx") or [])]
    hit = next((s for s in items if s.get("id") == id), None)
    if not hit:
        have = ", ".join(s.get("id") or "?" for s in items) or "none"
        return (f"REJECTED: no sfx with id '{id}'. Existing sfx ids: {have}. "
                "Call get_edl to see them.")
    edl["sfx"] = [s for s in items if s.get("id") != id]
    return ctx.write_edl(
        edl, f"removed sfx {id} ('{_track_name(hit['storage_key'])}' "
             f"at {hit['at']}s)")


def move_sfx(ctx, id, at):
    """Retime a sound without changing which sound it is or how loud."""
    try:
        at = float(at)
    except (TypeError, ValueError):
        return f"REJECTED: at must be a number of seconds, got {at!r}."
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(s) for s in (edl.get("sfx") or [])]
    hit = next((s for s in items if s.get("id") == id), None)
    if not hit:
        have = ", ".join(s.get("id") or "?" for s in items) or "none"
        return (f"REJECTED: no sfx with id '{id}'. Existing sfx ids: {have}.")
    prog = program_duration(edl)
    if at < 0 or at > max(0.0, prog - 0.05):
        return (f"REJECTED: at={at}s is outside the program "
                f"(0 to {round(prog, 2)}s).")
    old = hit["at"]
    hit["at"] = round(at, 2)
    edl["sfx"] = items
    return ctx.write_edl(
        edl, f"moved sfx {id} ('{_track_name(hit['storage_key'])}') "
             f"{old}s -> {hit['at']}s")


def swap_music(ctx, id, storage_key):
    """Change WHICH track plays, keeping its position, level and fit —
    'no, use a different song'."""
    track, err = _resolve_music(ctx, storage_key)
    if err:
        return err
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(m) for m in (edl.get("music") or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id") or "?" for m in items) or "none"
        return (f"REJECTED: no music with id '{id}'. Existing music ids: "
                f"{have}. Call get_edl to see them.")
    if hit.get("storage_key") == storage_key:
        return (f"NO CHANGE — music {id} is already '{track['name']}'. "
                "Do NOT tell the user you changed the track.")
    old = _music_name(hit.get("storage_key"))
    hit["storage_key"] = storage_key
    # An offset was measured into the OLD track — "start at the chorus" points
    # somewhere meaningless in a different song. Drop it rather than carry a
    # number that silently means something else now.
    dropped_offset = hit.get("offset_s")
    hit["offset_s"] = None
    edl["music"] = items
    res = ctx.write_edl(edl, f"music {id}: '{old}' -> '{track['name']}'")
    if dropped_offset and not str(res).startswith("REJECTED"):
        res += (f"\nNote: the {dropped_offset}s start-offset was cleared — it "
                "pointed into the old track. Set it again if you want one.")
    tdur, span = track.get("duration_s"), (hit["end"] - hit["start"])
    if tdur and tdur < span - 0.05 and not hit.get("loop") \
            and not str(res).startswith("REJECTED"):
        res += (f"\nNote: '{track['name']}' is {tdur:.0f}s but the span is "
                f"{span:.0f}s — it will fall silent for the rest unless you "
                "set loop=true with set_music_fit.")
    return res


def set_music_fit(ctx, id, start=None, end=None, offset_s=None,
                  fade_in_s=None, fade_out_s=None, loop=None, duck=None):
    """Retime or refit EXISTING music in place. Anything left unset is left
    alone — this is the tool for 'start the music later', 'make it fade out',
    'loop it to the end', without remove + re-add losing the other settings."""
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(m) for m in (edl.get("music") or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id") or "?" for m in items) or "none"
        return (f"REJECTED: no music with id '{id}'. Existing music ids: "
                f"{have}. Call get_edl to see them.")
    out_dur = program_duration(edl)
    before = dict(hit)
    try:
        if start is not None:
            hit["start"] = round(
                min(max(float(start), 0.0), max(0.0, out_dur - 0.1)), 2)
        if end is not None:
            hit["end"] = round(
                min(max(float(end), hit["start"] + 0.1), out_dur), 2)
        if hit["end"] <= hit["start"]:
            return "REJECTED: end must be after start."
        span = hit["end"] - hit["start"]
        if offset_s is not None:
            _o = max(0.0, float(offset_s))
            # Same rule as add_music: never store an offset the renderer will
            # throw away, or get_edl reports a setting the audio doesn't have.
            _tk, _ = _resolve_music(ctx, hit["storage_key"])
            _td = (_tk or {}).get("duration_s")
            if _o and _td and _o >= _td - 0.05:
                return (f"REJECTED: offset_s {_o:.1f}s is at or past the end "
                        f"of the track ({_td:.0f}s) — it would play silence. "
                        f"Pick an offset below {_td:.0f}s.")
            hit["offset_s"] = _o or None
        if fade_in_s is not None:
            hit["fade_in_s"] = min(max(0.0, float(fade_in_s)), span / 2) or None
        if fade_out_s is not None:
            hit["fade_out_s"] = min(max(0.0, float(fade_out_s)), span / 2) or None
    except (TypeError, ValueError):
        return ("REJECTED: start/end/offset_s/fade_in_s/fade_out_s must be "
                "numbers (seconds).")
    if loop is not None:
        hit["loop"] = True if loop else None
    if duck is not None:
        hit["duck"] = bool(duck)
    if hit == before:
        return (f"NO CHANGE — music {id} already has those settings. Do NOT "
                "tell the user you changed anything.")
    edl["music"] = items
    changed = ", ".join(
        f"{k}={hit.get(k)}" for k in
        ("start", "end", "offset_s", "fade_in_s", "fade_out_s", "loop", "duck")
        if hit.get(k) != before.get(k))
    res = ctx.write_edl(
        edl, f"music {id} ('{_music_name(hit['storage_key'])}') refit: "
             f"{changed}")
    track, _ = _resolve_music(ctx, hit["storage_key"])
    tdur = (track or {}).get("duration_s")
    span = hit["end"] - hit["start"]
    covered = (tdur - (hit.get("offset_s") or 0.0)) if tdur else None
    if covered is not None and covered < span - 0.05 and not hit.get("loop") \
            and not str(res).startswith("REJECTED"):
        res += (f"\nNote: the track only covers {covered:.0f}s of the "
                f"{span:.0f}s span and will fall SILENT for the rest — pass "
                "loop=true if you want it to fill.")
    return res


def set_audio_gain(ctx, kind, id, gain_db):
    """Change the loudness of an EXISTING music, sfx or voiceover item."""
    if kind not in ("music", "sfx", "voiceover"):
        return "REJECTED: kind must be 'music', 'sfx' or 'voiceover'."
    try:
        g = round(float(gain_db), 1)
    except (TypeError, ValueError):
        return "REJECTED: gain_db must be a number (dB, e.g. -12)."
    g = min(max(g, GAIN_MIN_DB), GAIN_MAX_DB)
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(m) for m in (edl.get(kind) or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id") or "?" for m in items) or "none"
        return (f"REJECTED: no {kind} with id '{id}'. Existing {kind} ids: "
                f"{have}. Call get_edl to see them.")
    old = hit.get("gain_db", 0.0)
    hit["gain_db"] = g
    edl[kind] = items
    key = hit.get("storage_key") or hit.get("asset_key") or "?"
    return ctx.write_edl(
        edl, f"{kind} {id} ('{_track_name(key)}') gain "
             f"{old:+.1f}dB -> {g:+.1f}dB")


def _music_assets(conn, project_id):
    # kind 'audio' is the extracted source-audio track (transcription
    # artifact) — never offer it as music.
    with conn.cursor() as cur:
        cur.execute("""SELECT storage_key, meta FROM assets
                       WHERE project_id = %s AND kind = 'music'
                       ORDER BY id DESC LIMIT 20""", (project_id,))
        return cur.fetchall()


def set_volume(ctx, start, end, gain_db):
    try:
        s, e = ctx.clamp(start), ctx.clamp(end)
        g = float(gain_db)
    except (TypeError, ValueError) as err:
        return f"REJECTED: {err}"
    if e <= s:
        return "REJECTED: end must be greater than start."
    edl = dict(ctx.latest_edl()["json"])
    vol = list(edl.get("volume") or [])
    vol.append({"start": s, "end": e, "gain_db": g})
    edl["volume"] = vol
    return ctx.write_edl(edl, f"volume {g:+.1f}dB on {s}-{e}s (source time)")


def set_frame(ctx, ratio, mode="crop"):
    try:
        frame = Frame.model_validate({"ratio": str(ratio),
                                      "mode": str(mode or "crop")})
    except Exception:
        return ('REJECTED: ratio must be one of source, 16:9, 9:16, 1:1, 4:5 '
                'and mode one of crop, pad, pad_blur. Example: '
                'set_frame("9:16", "crop") for TikTok.')
    edl = dict(ctx.latest_edl()["json"])
    if frame.ratio == "source":
        edl["frame"] = None
        return ctx.write_edl(edl, "output frame back to the source ratio")
    edl["frame"] = frame.model_dump()
    return ctx.write_edl(
        edl, f"output frame set to {frame.ratio} ({frame.mode})")


def set_color_grade(ctx, preset):
    p = (preset or "").strip().lower()
    if p in ("none", "off"):
        p = None
    elif p not in GRADE_PRESETS:
        return ("REJECTED: preset must be one of "
                f"{', '.join(GRADE_PRESETS)} — or 'none' to clear.")
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    fx["grade"] = p
    edl["effects"] = fx
    return ctx.write_edl(edl, f"color grade set to {p or 'none'}")


ZOOM_MODES = ("punch", "ease", "push_in", "pull_out")
ZOOM_MODE_DESC = {"punch": "punch-in", "ease": "eased",
                  "push_in": "Ken Burns push-in",
                  "pull_out": "Ken Burns pull-out"}


def add_zoom(ctx, start, end, strength=0.25, mode=None):
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 2)
        e = round(min(max(float(end), s), prog), 2)
        st = round(min(max(float(strength if strength is not None else 0.25),
                           0.05), 1.0), 2)
    except (TypeError, ValueError):
        return ("REJECTED: start/end/strength must be numbers. start/end are "
                "OUTPUT-timeline seconds; strength 0.05-1.0 (0.25 = 25% "
                "punch-in).")
    if e - s < 0.2:
        return "REJECTED: a zoom needs at least 0.2s."
    zmode = (mode or "punch").strip().lower()
    if zmode not in ZOOM_MODES:
        return (f"REJECTED: mode must be one of {', '.join(ZOOM_MODES)}. "
                "punch = instant step in/out; ease = smooth ramp in and "
                "out; push_in / pull_out = continuous Ken Burns drift "
                "across the window.")
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    item = {"id": _next_item_id(zooms, "zm"), "start": s, "end": e,
            "strength": st}
    if zmode != "punch":
        item["mode"] = zmode
    zooms.append(item)
    fx["zooms"] = zooms
    edl["effects"] = fx
    return ctx.write_edl(
        edl, f"{ZOOM_MODE_DESC[zmode]} zoom {int(st * 100)}% on {s}-{e}s "
             f"(output time) [{item['id']}]")


def remove_zoom(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    hit = next((z for z in zooms if z.get("id") == id), None)
    if not hit:
        have = ", ".join(z.get("id", "?") for z in zooms) or "none"
        return (f"REJECTED: no zoom with id '{id}'. Existing zooms: {have}. "
                "Call get_edl to see them.")
    fx["zooms"] = [z for z in zooms if z.get("id") != id]
    edl["effects"] = fx
    return ctx.write_edl(
        edl, f"removed zoom {id} ({hit['start']}-{hit['end']}s)")


def set_fades(ctx, fade_in_s=None, fade_out_s=None):
    if fade_in_s is None and fade_out_s is None:
        return ("REJECTED: pass fade_in_s and/or fade_out_s in seconds "
                "(0 clears a fade).")
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    bits = []
    try:
        if fade_in_s is not None:
            v = float(fade_in_s)
            fx["fade_in_s"] = 0.0 if v <= 0 else round(min(max(v, 0.1),
                                                           5.0), 2)
            bits.append(f"in {fx['fade_in_s']}s" if fx["fade_in_s"]
                        else "in cleared")
        if fade_out_s is not None:
            v = float(fade_out_s)
            fx["fade_out_s"] = 0.0 if v <= 0 else round(min(max(v, 0.1),
                                                            5.0), 2)
            bits.append(f"out {fx['fade_out_s']}s" if fx["fade_out_s"]
                        else "out cleared")
    except (TypeError, ValueError):
        return "REJECTED: fade_in_s/fade_out_s must be numbers of seconds."
    edl["effects"] = fx
    return ctx.write_edl(edl, "fade to/from black: " + ", ".join(bits))


def set_transitions(ctx, style, duration_s=0.3):
    p = (style or "").strip().lower()
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    if p in ("none", "off"):
        if not fx.get("transition"):
            return ("NO CHANGE: there are no transitions to remove. Do NOT "
                    "tell the user you changed anything.")
        fx["transition"] = None
        edl["effects"] = fx
        return ctx.write_edl(edl, "transitions removed (hard cuts again)")
    if p not in TRANSITION_STYLES:
        return (f"REJECTED: style must be one of "
                f"{', '.join(TRANSITION_STYLES)} — or 'none' to clear. "
                "dip_black = quick dip through black at every cut; "
                "dip_white = a white flash. True crossfades (overlapping "
                "footage) are not supported — say so if asked.")
    try:
        d = round(min(max(float(duration_s if duration_s is not None
                                else 0.3), TRANSITION_MIN_S),
                      TRANSITION_MAX_S), 2)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds (0.1-1.0)."
    fx["transition"] = {"style": p, "duration_s": d}
    edl["effects"] = fx
    n_cuts = max(0, len(edl.get("keep") or []) - 1) \
        + len(edl.get("inserts") or [])
    return ctx.write_edl(
        edl, f"transitions: {d}s {p.replace('_', '-')} at every cut "
             f"(~{n_cuts} junction{'s' if n_cuts != 1 else ''})")


REGION_MODES = ("blur", "pixelate", "black")


def blur_region(ctx, x, y, w, h, mode="blur", start=None, end=None):
    p = (mode or "blur").strip().lower()
    if p not in REGION_MODES:
        return (f"REJECTED: mode must be one of {', '.join(REGION_MODES)}. "
                "blur = soft blur (default), pixelate = mosaic, black = "
                "solid bar.")
    try:
        rx, ry = float(x), float(y)
        rw, rh = float(w), float(h)
    except (TypeError, ValueError):
        return ("REJECTED: x, y, w, h must be numbers — FRACTIONS of the "
                "frame (0-1). x,y is the TOP-LEFT corner: (0,0) is the "
                "frame's top-left. Example, a username in the top-right "
                "corner: x=0.6, y=0.02, w=0.38, h=0.1.")
    if not (0 <= rx <= 1 and 0 <= ry <= 1 and 0 < rw <= 1 and 0 < rh <= 1):
        return ("REJECTED: x, y, w, h are FRACTIONS of the frame (0-1), "
                "not pixels or seconds. x=0.6, y=0.02, w=0.38, h=0.1 covers "
                "the top-right corner.")
    if min(rw, 1.0 - rx) < 0.02 or min(rh, 1.0 - ry) < 0.02:
        return ("REJECTED: that rectangle falls (almost) entirely outside "
                "the frame, so it would censor nothing. x,y is the box's "
                "TOP-LEFT corner ((0,0) = the frame's top-left) — for a box "
                "touching the right edge use x = 1 - w; for the bottom, "
                "y = 1 - h.")
    if (start is None) != (end is None):
        return ("REJECTED: pass both start and end (output-timeline "
                "seconds), or neither to censor the whole video.")
    item = {"id": None, "x": round(rx, 3), "y": round(ry, 3),
            "w": round(rw, 3), "h": round(rh, 3)}
    if p != "blur":
        item["mode"] = p
    if start is not None:
        try:
            item["start"] = round(float(start), 2)
            item["end"] = round(float(end), 2)
        except (TypeError, ValueError):
            return "REJECTED: start/end must be numbers of seconds."
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    regions = [dict(r) for r in (fx.get("regions") or [])]
    item["id"] = _next_item_id(regions, "rg")
    regions.append(item)
    fx["regions"] = regions
    edl["effects"] = fx
    span = (f" from {item['start']}s to {item['end']}s (output time)"
            if start is not None else " for the whole video")
    result = ctx.write_edl(
        edl, f"{p} region at x={item['x']},y={item['y']} "
             f"size {item['w']}x{item['h']} (frame fractions){span} "
             f"[{item['id']}]")
    if result.startswith("EDL v"):
        result += ("\nThe rectangle is FIXED on screen — render_preview and "
                   "CHECK the sheet: if the text still shows anywhere, "
                   "remove_blur this region and place a bigger one.")
    return result


def remove_blur(ctx, id=None):
    if id is not None and not str(id).strip():
        return ("REJECTED: id is empty. Pass a real region id from "
                "get_edl, or omit id entirely to remove ALL regions.")
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    regions = [dict(r) for r in (fx.get("regions") or [])]
    if not regions:
        return ("NO CHANGE: there are no censor regions to remove. Do NOT "
                "tell the user you changed anything.")
    if id:
        hit = next((r for r in regions if r.get("id") == id), None)
        if not hit:
            have = ", ".join(r.get("id", "?") for r in regions)
            return (f"REJECTED: no censor region with id '{id}'. Existing: "
                    f"{have}. Call get_edl to see them, or omit id to "
                    "remove all.")
        fx["regions"] = [r for r in regions if r.get("id") != id]
        desc = f"removed censor region {id}"
    else:
        fx["regions"] = []
        desc = f"removed all {len(regions)} censor region(s)"
    edl["effects"] = fx
    return ctx.write_edl(edl, desc)


def _next_item_id(items, prefix):
    n = 1
    taken = {it.get("id") for it in items}
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _resolve_media_asset(ctx, asset_key, kinds):
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, asset_key)
    if asset and asset["kind"] == "audio" and "audio" not in kinds:
        return None, ("REJECTED: that file is the video's OWN extracted "
                      "audio track (a transcription artifact) — it is not "
                      "user content and must not be mixed back in. Ask the "
                      "user to attach the file you actually need.")
    if not asset or asset["kind"] not in kinds:
        avail = ctx.db.run(dbx.assets_by_kinds, ctx.project_id, list(kinds))
        hint = ("Available storage_keys: "
                + "; ".join(a["storage_key"] for a in avail[:12])
                if avail else "Nothing of that type is uploaded to this "
                              "project yet — ask the user to attach or "
                              "upload one.")
        return None, (f"REJECTED: '{asset_key}' is not a "
                      f"{'/'.join(kinds)} asset in this project. {hint}")
    return asset, None


def _asset_media_duration(ctx, asset):
    """Duration of a clip/audio asset, probing once on first use if the
    browser couldn't provide it (and persisting the result)."""
    if asset.get("duration_s"):
        return float(asset["duration_s"])
    local = os.path.join(ctx.workdir, f"probe_{asset['id']}"
                         + os.path.splitext(asset["storage_key"])[1])
    storage.download_to(asset["storage_key"], local)
    try:
        info = media.probe(local)
        ctx.db.run(dbx.update_asset_probe, asset["id"], info["duration"],
                   info["width"], info["height"], info["fps"],
                   asset.get("sha256"))
        return float(info["duration"])
    except media.MediaError:
        dur = media.probe_audio_duration(local)
        ctx.db.run(dbx.update_asset_probe, asset["id"], dur, None, None,
                   None, asset.get("sha256"))
        return float(dur)


INSERT_NEEDS_WINDOW_S = 15.0    # clips longer than this need an explicit window


INSERT_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")


def insert_media(ctx, asset_key, at_output_s, duration_s=None,
                 clip_start_s=None, motion=None):
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref"))
    if err:
        return err
    kind = "image" if asset["kind"] == "image_ref" else "video"
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — a position in the "
                "FINAL edited video, in seconds.")
    if motion is not None:
        motion = str(motion).strip().lower() or None
    if motion:
        if kind != "image":
            return ("REJECTED: motion is only for IMAGE inserts (a Ken "
                    "Burns move on a still) — video clips already move. "
                    "Drop the motion argument for clips.")
        if motion not in INSERT_MOTIONS:
            return (f"REJECTED: motion must be one of "
                    f"{', '.join(INSERT_MOTIONS)}.")
    off = 0.0
    if kind == "image":
        try:
            dur = round(min(max(float(duration_s if duration_s is not None
                                       else 3.0), 0.2), 60.0), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."
    else:
        clip_dur = _asset_media_duration(ctx, asset)
        if duration_s is None and clip_dur > INSERT_NEEDS_WINDOW_S:
            return (f"REJECTED: '{name}' is {clip_dur:.0f}s long — splicing "
                    "ALL of it in is almost never what the user wants. Pass "
                    "duration_s (2-8s is typical for a b-roll insert) and "
                    "clip_start_s to choose WHICH part of the clip to use. "
                    "Call look_at_asset first to see frames and pick the "
                    "right moment.")
        try:
            dur = round(min(max(float(duration_s), 0.2), clip_dur,
                            MAX_INSERT_DURATION_S), 2) \
                if duration_s is not None else round(
                    min(clip_dur, MAX_INSERT_DURATION_S), 2)
            off = round(max(float(clip_start_s), 0.0), 2) \
                if clip_start_s is not None else 0.0
        except (TypeError, ValueError):
            return ("REJECTED: duration_s and clip_start_s must be numbers "
                    "of seconds.")
        if off + dur > clip_dur + 0.05:
            return (f"REJECTED: the window {off}-{round(off + dur, 2)}s runs "
                    f"past the end of the clip ({clip_dur:.1f}s). Use "
                    f"clip_start_s <= {max(0.0, round(clip_dur - dur, 2))}.")

    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]

    if not ctx.has_main_video or is_canvas_program(edl):
        # Canvas program: there is no keep timeline to splice into — the clip/
        # image IS program content. Append it at the requested program position;
        # validate_edl lays all inserts end-to-end in at_output_s order. The
        # FIRST placed asset fixes the output frame (canvas) to match its
        # aspect, replacing the seeded default — otherwise a vertical short on a
        # no-video project would render pillar-boxed on the 16:9 default.
        if not inserts or not edl.get("canvas"):
            edl["keep"] = []
            edl["canvas"] = _canvas_for_asset(ctx, asset)
        item = {"id": _next_item_id(inserts, "ins"), "asset_key": asset_key,
                "kind": kind, "at_output_s": round(max(0.0, at), 2),
                "duration_s": dur}
        if kind == "video" and off:
            item["source_start_s"] = off
        if motion:
            item["motion"] = motion
        edl["inserts"] = inserts + [item]
        window = (f" (using clip {off:.1f}-{round(off + dur, 2):.1f}s)"
                  if off else "")
        moved = f" with a Ken Burns {motion} move" if motion else ""
        desc = (f"placed {kind} '{name}' ({dur}s){window}{moved} on the "
                f"canvas [{item['id']}]")
        return ctx.write_edl(edl, desc)

    keep = [list(x) for x in edl["keep"]]
    tl = Timeline(keep, inserts)
    at = round(min(max(at, 0.0), tl.out_duration), 2)
    pre_bounds = keep_boundaries(keep)
    final_of = {b: b + sum(d for a2, d in tl.ins if a2 <= b + 1e-6)
                for b in pre_bounds}
    nearest_b = min(pre_bounds, key=lambda b: abs(final_of[b] - at))
    note_bits = []
    if abs(final_of[nearest_b] - at) <= 0.25:
        target_pre = nearest_b          # close enough — use the boundary
    else:
        src = tl.out_to_src(at)
        if src is None:
            # requested point falls inside an existing insert
            target_pre = nearest_b
            note_bits.append(
                f"snapped from {at}s to the nearest segment boundary — the "
                "requested point is inside another insert")
        else:
            # split the containing keep segment so the insert lands exactly
            # there; move the split to a word edge so no word is clipped
            hit = next((w for w in ctx.index.get("words", [])
                        if w["t0"] < src < w["t1"]), None)
            if hit:
                src = hit["t0"] if src - hit["t0"] <= hit["t1"] - src \
                    else hit["t1"]
            src = round(src, 2)
            seg_i = next((i for i, (s, e) in enumerate(keep)
                          if s + 0.05 < src < e - 0.05), None)
            if seg_i is None:
                target_pre = nearest_b
            else:
                s0, e0 = keep[seg_i]
                keep[seg_i:seg_i + 1] = [[s0, src], [src, e0]]
                edl["keep"] = keep
                target_pre = keep_boundaries(keep)[seg_i + 1]
                note_bits.append(
                    f"split the take at source {src}s (a word edge) so the "
                    "insert lands mid-talk exactly where asked")
    final_at = round(target_pre + sum(d for a2, d in tl.ins
                                      if a2 <= target_pre + 1e-6), 2)
    item = {"id": _next_item_id(inserts, "ins"), "asset_key": asset_key,
            "kind": kind, "at_output_s": target_pre, "duration_s": dur}
    if kind == "video" and off:
        item["source_start_s"] = off
    if motion:
        item["motion"] = motion
    edl["inserts"] = inserts + [item]
    window = (f" (using clip {off:.1f}-{round(off + dur, 2):.1f}s)"
              if off else "")
    moved = f" with a Ken Burns {motion} move" if motion else ""
    desc = (f"inserted {kind} '{name}' ({dur}s){window}{moved} at "
            f"{final_at}s of the edited video [{item['id']}]")
    if note_bits:
        desc += " — " + "; ".join(note_bits)
    result = ctx.write_edl(edl, desc)
    if result.startswith("EDL v"):
        result += ("\nNote: captions cover the main footage only — inserted "
                   "media is not transcribed or captioned.")
    return result


def _drop_orphaned_sfx(edl):
    """Drop sound effects that no longer fit the program, returning notes.

    Shrinking the program by any route other than a keep change — removing a
    spliced insert, shortening one — leaves sfx bounded by the OLD program
    length. validate_edl then rejects the whole write, so a user clicking x on
    an insert is told their edit is invalid over a sound they never mentioned.
    """
    items = edl.get("sfx") or []
    if not items:
        return []
    prog = program_duration(edl)
    kept, notes = [], []
    for s in items:
        if float(s["at"]) > max(0.0, prog - 0.05):
            notes.append(f"note: sound effect {s.get('id')} was removed — it "
                         "sits after the end of the shortened edit.")
            continue
        kept.append(s)
    edl["sfx"] = kept
    return notes


def remove_insert(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    hit = next((i for i in inserts if i.get("id") == id), None)
    if not hit:
        have = ", ".join(i.get("id", "?") for i in inserts) or "none"
        return (f"REJECTED: no insert with id '{id}'. Existing inserts: "
                f"{have}. Call get_edl to see them.")
    edl["inserts"] = [i for i in inserts if i.get("id") != id]
    notes = _drop_orphaned_sfx(edl)
    res = ctx.write_edl(
        edl, f"removed insert {id} "
             f"('{os.path.basename(hit['asset_key'])}', {hit['duration_s']}s) "
             "— prior timing restored")
    if notes and res.startswith("EDL v"):
        res += "\n" + "\n".join(notes)
    return res


def add_voiceover(ctx, asset_key, start_output_s=0.0, gain_db=0.0,
                  duck_others=True):
    asset, err = _resolve_media_asset(ctx, asset_key, ("music",))
    if err:
        return err
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        start = round(min(max(float(start_output_s), 0.0),
                          max(0.0, prog - 0.1)), 2)
        g = float(gain_db)
    except (TypeError, ValueError):
        return ("REJECTED: start_output_s and gain_db must be numbers "
                "(start is a position in the FINAL edited video).")
    vos = [dict(v) for v in (edl.get("voiceover") or [])]
    item = {"id": _next_item_id(vos, "vo"), "asset_key": asset_key,
            "start_output_s": start, "gain_db": g,
            "duck_others": bool(duck_others)}
    edl["voiceover"] = vos + [item]
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    res = ctx.write_edl(
        edl, f"voiceover '{name}' from {start}s (output time), {g:+.1f}dB, "
             f"ducking other audio {DUCK_NOTE if bool(duck_others) else 'off'}"
             f" [{item['id']}]")
    dup_mus = [m.get("id") or "?" for m in (edl.get("music") or [])
               if m.get("storage_key") == asset_key]
    if dup_mus and not str(res).startswith("REJECTED"):
        res += (f"\nWARNING: this same file is also active as music "
                f"{', '.join(dup_mus)} — it will play TWICE. Background "
                f"music belongs in music items, not voiceover.")
    return res


DUCK_NOTE = "-12dB while it speaks"


def remove_voiceover(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    vos = [dict(v) for v in (edl.get("voiceover") or [])]
    hit = next((v for v in vos if v.get("id") == id), None)
    if not hit:
        have = ", ".join(v.get("id", "?") for v in vos) or "none"
        return (f"REJECTED: no voiceover with id '{id}'. Existing: {have}.")
    edl["voiceover"] = [v for v in vos if v.get("id") != id]
    return ctx.write_edl(
        edl, f"removed voiceover {id} "
             f"('{os.path.basename(hit['asset_key'])}')")


IMAGE_ASPECTS = ("16:9", "9:16", "1:1", "4:3", "3:4")


def _nearest_image_aspect(w, h):
    if not (w and h):
        return "16:9"
    r = float(w) / float(h)
    return min((("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0),
                ("4:3", 4 / 3), ("3:4", 3 / 4)),
               key=lambda a: abs(a[1] - r))[0]


def _default_image_aspect(ctx):
    """Aspect for a generated image when the model doesn't pass one: the
    output frame if set (so full-frame inserts fill it), else the canvas of a
    no-main-video program, else the nearest supported aspect to the source
    video."""
    edl = None
    try:
        edl = ctx.latest_edl()["json"]
        ratio = (edl.get("frame") or {}).get("ratio")
    except Exception:
        ratio = None
    if ratio in IMAGE_ASPECTS:
        return ratio
    if ratio == "4:5":
        return "3:4"
    if not ctx.has_main_video:
        # match the canvas the program will render on
        cv = (edl or {}).get("canvas") or {}
        if cv.get("width") and cv.get("height"):
            return _nearest_image_aspect(cv["width"], cv["height"])
        return ctx.canvas_ratio if ctx.canvas_ratio in IMAGE_ASPECTS else "16:9"
    v = ctx.index["video"]
    return _nearest_image_aspect(v.get("width"), v.get("height"))


def _canvas_for_asset(ctx, asset):
    """Canvas geometry (width/height/fps/bg_color) derived from the first asset
    placed on a no-main-video program, so the output frame matches its content.
    Falls back to probing the file, then to the context's default aspect."""
    w = asset.get("width") or (asset.get("meta") or {}).get("width")
    h = asset.get("height") or (asset.get("meta") or {}).get("height")
    fps = DEFAULT_CANVAS_FPS
    if not (w and h) or asset["kind"] != "image_ref":
        try:
            info = media.probe(_asset_local_path(ctx, asset))
            w, h = w or info.get("width"), h or info.get("height")
            if asset["kind"] != "image_ref" and info.get("fps"):
                fps = max(1.0, min(float(info["fps"]), 60.0))
        except Exception:
            pass
    ratio = (_nearest_canvas_ratio(w, h) if (w and h)
             else (ctx.canvas_ratio or "16:9"))
    cw, ch = CANVAS_DIMS.get(ratio, CANVAS_DIMS["16:9"])
    return {"width": cw, "height": ch, "fps": round(fps, 2),
            "bg_color": "#000000"}


def _nearest_canvas_ratio(w, h):
    r = float(w) / float(h)
    return min((("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0),
                ("4:5", 4 / 5), ("4:3", 4 / 3)),
               key=lambda a: abs(a[1] - r))[0]


def generate_image(ctx, prompt, from_video_time_s=None, from_asset_key=None,
                   aspect=None):
    """Create an image with AI: pure text-to-image, restyle a frame of the
    main video, or restyle an uploaded image. The result becomes a project
    image asset the model must then insert_media to actually use."""
    if not llm.image_available():
        return ("Image generation is unavailable (no image model "
                "configured). Tell the user honestly and offer the "
                "non-generative alternatives instead.")
    p = (prompt or "").strip()
    if not p:
        return ("REJECTED: prompt is empty — describe the image to create, "
                "or the change to make to the frame/image.")
    if from_video_time_s is not None and from_asset_key:
        return ("REJECTED: pass EITHER from_video_time_s (restyle a frame "
                "of the main video) OR from_asset_key (restyle an uploaded "
                "image), not both.")
    if (from_video_time_s is not None or from_asset_key) \
            and not llm.image_edit_available():
        return ("REJECTED: the current image model can only GENERATE an image "
                "from a text description — it cannot restyle an existing frame "
                "or uploaded image. Either describe the whole image you want "
                "(no from_video_time_s / from_asset_key) and it'll be created "
                "fresh, or tell the user restyling isn't available. Be honest "
                "about the difference.")
    if len(ctx.images_generated) >= config.MAX_GENERATED_IMAGES_PER_TURN:
        return (f"REJECTED: already generated "
                f"{config.MAX_GENERATED_IMAGES_PER_TURN} images this turn "
                "(the per-turn limit). Insert what you have, or continue "
                "in the next message.")
    if aspect is not None:
        aspect = str(aspect).strip()
        if aspect not in IMAGE_ASPECTS:
            return (f"REJECTED: aspect must be one of "
                    f"{', '.join(IMAGE_ASPECTS)}.")
    over = _gen_budget_reject(ctx, config.IMAGE_PRICE_USD, "generate an image")
    if over:
        return over

    n = len(ctx.images_generated) + 1
    out_path = os.path.join(ctx.workdir, f"gen_{n}.png")
    if from_video_time_s is not None:
        try:
            t = ctx.clamp(from_video_time_s)
        except ValueError as err:
            return f"REJECTED: {err}"
        try:
            frame_path = os.path.join(ctx.workdir, f"gen_src_{n}.jpg")
            media.frame_at(ctx.proxy_path(), t, frame_path, quality=2)
        except Exception as e:
            return f"Could not extract the frame at {t}s ({str(e)[:160]})."
        ok, err = llm.edit_image(frame_path, p, out_path,
                                 image_name=f"proxy frame @{t:.2f}s")
        source_desc = f"made by restyling the source frame at {t}s"
    elif from_asset_key:
        asset, err = _resolve_media_asset(ctx, from_asset_key, ("image_ref",))
        if err:
            return err
        try:
            local = _asset_local_path(ctx, asset)
        except Exception as e:
            return f"Cannot fetch that image right now ({str(e)[:160]})."
        name = (asset.get("meta") or {}).get("filename") or \
            os.path.basename(from_asset_key)
        ok, err = llm.edit_image(local, p, out_path, image_name=name)
        source_desc = f"made by restyling the uploaded image '{name}'"
    else:
        aspect = aspect or _default_image_aspect(ctx)
        ok, err = llm.generate_image(p, out_path, aspect=aspect)
        source_desc = f"generated from the text prompt ({aspect})"
    if not ok:
        return (f"Image generation FAILED: {err}. If this looks like a "
                "content-policy rejection, reword the prompt; otherwise "
                "try once more or tell the user it didn't work — do NOT "
                "claim an image was created.")

    try:
        from PIL import Image
        with Image.open(out_path) as im:
            width, height = im.size
    except Exception:
        width = height = None
    key = f"generated/{ctx.project_id}/{uuid.uuid4().hex[:12]}.png"
    try:
        storage.upload_file(out_path, key, "image/png")
    except Exception as e:
        return (f"The image was generated but could not be saved to "
                f"storage ({str(e)[:160]}). Try again.")
    caption = f"AI-generated image ({source_desc}): {p[:300]}"
    ctx.db.run(dbx.insert_asset, ctx.project_id, "image_ref", key,
               bytes_=os.path.getsize(out_path), width=width, height=height,
               meta={"filename": f"generated-{n}.png", "caption": caption,
                     "generated": True,
                     "model": (config.IMAGE_EDIT_MODEL
                               if (from_video_time_s is not None
                                   or from_asset_key)
                               else config.IMAGE_GEN_MODEL)})
    ctx.images_generated.append({"storage_key": key, "prompt": p[:200]})
    dims = f" ({width}x{height})" if width else ""
    if not ctx.has_main_video:
        # No main video: the image becomes program content itself, not an
        # overlay on footage — place it to build the canvas program.
        return (f"Generated image saved: storage_key={key}{dims} — "
                f"{source_desc}. It is NOT in your program yet: place it with "
                f"insert_media(asset_key='{key}', at_output_s=0, "
                "duration_s=3, motion='zoom_in') to make it a full-frame "
                "moment on the canvas, or check it first with look_at_asset.")
    return (f"Generated image saved: storage_key={key}{dims} — "
            f"{source_desc}. It is NOT in the video yet: splice it in with "
            f"insert_media(asset_key='{key}', at_output_s=..., "
            "duration_s=2-4, motion='zoom_in'), or check it first with "
            "look_at_asset. It will appear as a full-frame still moment — "
            "the moving footage itself is not modified.")


def _log_generation(ctx, purpose, model, prompt, key, cost_usd):
    """Record an external (non-LLM) generation to llm_calls so the final credit
    charge (db.charge_turn_credits sums response.cost_usd) and the admin Model
    I/O tab both see it. Returns True iff the row persisted — the caller only
    adds to gen_extra_cost_usd on success, so the in-turn cap (running_credits)
    and the final charge can never disagree. Never breaks the turn."""
    try:
        ctx.db.run(dbx.insert_llm_call, ctx.project_id, ctx.job["id"], purpose,
                   model, {"model": model, "prompt": (prompt or "")[:500]},
                   {"storage_key": key, "cost_usd": round(float(cost_usd), 4)},
                   None, None)
        return True
    except Exception:
        return False


def _gen_budget_reject(ctx, projected_usd, what):
    """Refuse a PAID external generation the user cannot afford, BEFORE spending
    real money at the provider. running_credits + this generation's cost must
    fit the turn's credit budget (balance + grace). Returns a REJECTED string
    or None. Unlike token spend (which the loop self-corrects between calls),
    fal/ElevenLabs charges are irreversible real USD, so they need a pre-check."""
    if ctx.credit_budget is None:
        return None
    projected = round(float(projected_usd) / 0.01, 2)
    if ctx.running_credits() + projected > ctx.credit_budget:
        return (f"REJECTED: not enough credits to {what} (it costs about "
                f"{projected:.0f} credits and the balance won't cover it). Tell "
                "the user honestly they're out of credits — they refresh daily, "
                "or upgrading adds a bigger monthly pool.")
    return None


def generate_sfx(ctx, prompt, at, duration_s=None, gain_db=-6.0):
    """Generate a one-shot sound effect from a text description and place it at
    a moment in the program (program-time seconds)."""
    if not eleven.sound_gen_available():
        return ("Sound generation is unavailable (no sound provider "
                "configured). You can still drop a sound from the built-in "
                "pack with add_sfx / list_sfx_library. Tell the user honestly.")
    p = (prompt or "").strip()
    if not p:
        return "REJECTED: prompt is empty — describe the sound to create."
    if len(ctx.sfx_generated) >= config.MAX_GENERATED_SFX_PER_TURN:
        return (f"REJECTED: already generated {config.MAX_GENERATED_SFX_PER_TURN} "
                "sounds this turn (the per-turn limit). Place what you have.")
    try:
        at = float(at)
    except (TypeError, ValueError):
        return f"REJECTED: at must be a number of seconds, got {at!r}."
    try:
        gain_db = float(gain_db)
    except (TypeError, ValueError):
        return f"REJECTED: gain_db must be a number, got {gain_db!r}."
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    # Nothing to place a sound onto yet — reject BEFORE spending money at the
    # provider (validate_edl would reject the write afterwards, orphaning a
    # paid-for sound and charging the user for it).
    if prog <= 0:
        return ("REJECTED: there's no program yet to place a sound on. Add or "
                "generate a clip or image first, then add the sound.")
    if at < 0 or at > max(0.0, prog - 0.05):
        return (f"REJECTED: at={at}s is outside the program (0 to "
                f"{round(prog, 2)}s). Sounds are placed in program time — the "
                "edited timeline.")
    over = _gen_budget_reject(ctx, config.SFX_PRICE_USD, "generate a sound")
    if over:
        return over
    n = len(ctx.sfx_generated) + 1
    out_path = os.path.join(ctx.workdir, f"gensfx_{n}.mp3")
    ok, err = eleven.generate_sfx(p, out_path, duration_s=duration_s)
    if not ok:
        return (f"Sound generation FAILED: {err}. Reword the prompt or tell the "
                "user it didn't work — do NOT claim a sound was created.")
    key = f"generated_sfx/{ctx.project_id}/{uuid.uuid4().hex[:12]}.mp3"
    try:
        storage.upload_file(out_path, key, "audio/mpeg")
    except Exception as e:
        return (f"The sound was generated but could not be saved to storage "
                f"({str(e)[:140]}). Try again.")
    items = [dict(s) for s in (edl.get("sfx") or [])]
    taken = {s.get("id") for s in items}
    k = 1
    while f"sx{k}" in taken:
        k += 1
    sid = f"sx{k}"
    items.append({"id": sid, "storage_key": key, "at": round(at, 2),
                  "gain_db": gain_db})
    edl["sfx"] = items
    result = ctx.write_edl(
        edl, f"generated + placed AI sound '{p[:40]}' at {round(at, 2)}s "
             f"({gain_db:+g}dB) as {sid}")
    # Only bill once the sound is actually in the edit. Tie the in-turn cap and
    # the final charge to the SAME success boundary so they never diverge.
    if result.startswith("EDL v"):
        ctx.sfx_generated.append({"storage_key": key, "prompt": p[:200]})
        if _log_generation(ctx, "sfx_gen",
                           config.ELEVEN_SFX_MODEL or "elevenlabs-sfx",
                           p, key, config.SFX_PRICE_USD):
            ctx.gen_extra_cost_usd += config.SFX_PRICE_USD
    return result


def generate_video(ctx, prompt, from_image_asset_key=None, duration_s=5):
    """Generate a video clip with AI (text-to-video, or animate an existing
    image via from_image_asset_key). Saved as a project clip the model then
    places with insert_media — like generate_image, it is NOT in the program
    until inserted."""
    if not videogen.video_gen_available():
        return ("Video generation is unavailable (no video provider "
                "configured). Offer the honest alternatives instead: an "
                "uploaded clip, or a generated IMAGE placed as a full-frame "
                "moment (generate_image + insert_media).")
    p = (prompt or "").strip()
    if not p:
        return "REJECTED: prompt is empty — describe the video to create."
    if len(ctx.videos_generated) >= config.MAX_GENERATED_VIDEOS_PER_TURN:
        return (f"REJECTED: already generated "
                f"{config.MAX_GENERATED_VIDEOS_PER_TURN} videos this turn "
                "(the per-turn limit). Place what you have.")
    try:
        est_seconds = min(max(float(duration_s or 5), 1.0),
                          config.VIDEO_MAX_SECONDS)
    except (TypeError, ValueError):
        est_seconds = 5.0
    over = _gen_budget_reject(ctx, videogen.price_for(est_seconds),
                              "generate a video")
    if over:
        return over
    image_url = None
    if from_image_asset_key:
        asset, err = _resolve_media_asset(ctx, from_image_asset_key,
                                          ("image_ref",))
        if err:
            return err
        try:
            image_url = storage.presign_get(asset["storage_key"], expires=3600)
        except Exception as e:
            return (f"Could not prepare the source image for animation "
                    f"({str(e)[:140]}). Try again.")
    n = len(ctx.videos_generated) + 1
    out_path = os.path.join(ctx.workdir, f"genvid_{n}.mp4")
    ok, err, seconds = videogen.generate_video(p, out_path, image_url=image_url,
                                               duration_s=duration_s)
    if not ok:
        return (f"Video generation FAILED: {err}. Try again or tell the user it "
                "didn't work — do NOT claim a clip was created.")
    key = f"generated_video/{ctx.project_id}/{uuid.uuid4().hex[:12]}.mp4"
    try:
        storage.upload_file(out_path, key, "video/mp4")
    except Exception as e:
        return (f"The video was generated but could not be saved to storage "
                f"({str(e)[:140]}). Try again.")
    try:
        dur = media.probe(out_path).get("duration") or seconds
    except Exception:
        dur = seconds
    ctx.db.run(dbx.insert_asset, ctx.project_id, "video_clip", key,
               bytes_=os.path.getsize(out_path), duration_s=dur,
               meta={"filename": f"generated-video-{n}.mp4",
                     "caption": f"AI-generated video: {p[:300]}",
                     "generated": True, "model": config.VIDEO_GEN_MODEL})
    cost = videogen.price_for(seconds)
    ctx.videos_generated.append({"storage_key": key, "prompt": p[:200],
                                 "seconds": seconds})
    # Bill only if the cost row persisted, so running_credits (in-turn cap) and
    # charge_turn_credits (final charge, which reads that row) stay in lockstep.
    if _log_generation(ctx, "video_gen", config.VIDEO_GEN_MODEL, p, key, cost):
        ctx.gen_extra_cost_usd += cost
    animated = (" (animated from the source image)" if from_image_asset_key
                else "")
    return (f"Generated {seconds:.0f}s video saved{animated}: storage_key={key} "
            f"({round(dur, 1)}s). It is NOT in your program yet: place it with "
            f"insert_media(asset_key='{key}', at_output_s=...), trimming with "
            "duration_s/clip_start_s if you only want part, or check it first "
            "with look_at_asset.")


# ── Fetching media from a link ───────────────────────────────────────────────

# What the model may pass as as_kind, and the asset kind each maps to. The
# hint only steers the DOWNLOAD (it is cheaper to pull audio-only when a song
# was asked for); ffprobe still decides what the file actually is, because a
# hint that overrode the decoder would let the agent file a video as music and
# hand the renderer something it cannot use.
_FETCH_KIND_HINTS = {
    "clip": url_media.KIND_VIDEO, "video": url_media.KIND_VIDEO,
    "music": url_media.KIND_AUDIO, "audio": url_media.KIND_AUDIO,
    "song": url_media.KIND_AUDIO, "image": url_media.KIND_IMAGE,
    "photo": url_media.KIND_IMAGE, "picture": url_media.KIND_IMAGE,
}

# How to actually USE each kind once it has landed. Returned to the model so
# the fetch and the placement are one thought — the round-26 lesson from
# generate_image, whose result string had to spell out "it is NOT in the video
# yet" before the agent stopped reporting a generated image as an edit.
_FETCH_NEXT_STEP = {
    url_media.KIND_VIDEO:
        "splice it in with insert_media(asset_key='{key}', at_output_s=..., "
        "duration_s=...), or look at it first with look_at_asset",
    url_media.KIND_AUDIO:
        "score the edit with add_music(storage_key='{key}')",
    url_media.KIND_IMAGE:
        "splice it in with insert_media(asset_key='{key}', at_output_s=..., "
        "duration_s=2-4, motion='zoom_in'), or check it with look_at_asset",
}


def _clean_url(raw):
    """Pull a bare URL out of what a model typically passes.

    Models hand over `<https://x>`, `[title](https://x)` and trailing
    punctuation from the sentence they copied it out of. Stripping these is
    not politeness — a URL with a stray `)` on the end 404s, and the user is
    told their working link is broken."""
    u = (raw or "").strip()
    if u.startswith("[") and "](" in u:                 # markdown link
        u = u.split("](", 1)[1]
    u = u.strip("<>").strip()
    u = u.rstrip(").,;'\"")
    return u.strip()


def fetch_url(ctx, url, as_kind=None):
    """Download media from a link and register it as a project asset."""
    if not config.URL_FETCH_ENABLED:
        return ("REJECTED: this deployment cannot download media from links. "
                "Ask the user to upload the file instead.")
    url = _clean_url(url)
    if not url:
        return "REJECTED: fetch_url needs a url."

    prefer = None
    if as_kind is not None:
        prefer = _FETCH_KIND_HINTS.get(str(as_kind).strip().lower())
        if prefer is None:
            return ("REJECTED: as_kind must be one of clip, music, image — "
                    "or omit it and the file type is detected.")

    n = len(ctx.urls_fetched) + 1
    if n > config.MAX_FETCHED_URLS_PER_TURN:
        return (f"REJECTED: {config.MAX_FETCHED_URLS_PER_TURN} links already "
                "fetched this turn, which is the limit. Use what you have, or "
                "ask the user to send the rest in another message.")

    # A fresh directory per ATTEMPT, not per success. Numbering it by
    # len(urls_fetched) meant a FAILED fetch (rejected for size or duration,
    # or killed mid-download) left its bytes behind and the next attempt in
    # the same turn reused the very same directory — where _extract's
    # "largest file in the folder" pick would then hand back the PREVIOUS
    # link's media, registered under this link's title. Silently returning
    # someone the wrong video is the one failure the honesty layer cannot see.
    workdir = os.path.join(ctx.workdir, f"fetch_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        got = url_media.fetch(url, workdir, prefer=prefer)
    except url_media.FetchMediaError as e:
        # Every failure here is a sentence written to be shown to a user
        # ("Private video", "over the 50 MB limit"). The instruction to not
        # claim success matters: a download failure is the exact shape of
        # turn where the model is most tempted to say "added your song".
        #
        # Clean up on the way out. A failed fetch leaves partial yt-dlp
        # fragments behind, and because a failure does NOT increment the
        # counter, the next attempt this turn reuses this very directory —
        # where a stale fragment would then be a candidate for the
        # largest-file pick.
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not download that link — {e}. Tell the user that "
                "plainly and suggest they upload the file instead. Do NOT "
                "claim anything was added.")
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not download that link ({str(e)[:200]}). Tell the "
                "user it did not work. Do NOT claim anything was added.")

    kind, path = got["kind"], got["path"]
    key = url_media.storage_key(ctx.project_id, kind, path)
    try:
        storage.upload_file(path, key, url_media.content_type(path))
    except Exception as e:
        return (f"Downloaded that {url_media.KIND_LABEL[kind]} but could not "
                f"save it to storage ({str(e)[:160]}). Do NOT claim it was "
                "added; try again.")
    finally:
        # Reclaim the bytes immediately. Four 500 MB fetches in one turn would
        # otherwise sit on the worker's ephemeral disk alongside the proxy and
        # every render temp — and this box has run out of disk before.
        #
        # The whole per-fetch directory, not just the file we uploaded: when
        # yt-dlp cannot merge, it leaves the separate audio and video streams
        # behind, and those are the two biggest files of the lot.
        shutil.rmtree(workdir, ignore_errors=True)

    ctx.db.run(dbx.insert_asset, ctx.project_id, kind, key,
               bytes_=got.get("bytes"), duration_s=got.get("duration_s"),
               width=got.get("width"), height=got.get("height"),
               fps=got.get("fps"),
               meta={"filename": got["filename"],
                     "fetched": True,
                     "source_url": got["source_url"],
                     "extractor": got.get("extractor"),
                     "title": got.get("title"),
                     "uploader": got.get("uploader")})
    ctx.urls_fetched.append({"storage_key": key, "kind": kind,
                             "url": got["source_url"],
                             "filename": got["filename"]})

    bits = []
    if got.get("duration_s"):
        bits.append(f"{got['duration_s']:.0f}s")
    if got.get("width") and got.get("height"):
        bits.append(f"{got['width']}x{got['height']}")
    if kind == url_media.KIND_VIDEO and got.get("has_audio") is False:
        bits.append("no audio")
    detail = f" ({', '.join(bits)})" if bits else ""
    nxt = _FETCH_NEXT_STEP[kind].format(key=key)
    return (f"Downloaded \"{got['filename']}\"{detail} as a "
            f"{url_media.KIND_LABEL[kind]}: storage_key={key}. It is saved to "
            f"the project but NOT in the video yet — {nxt}.")


# ------------------------------------------------------------------ #
#  META tools                                                          #
# ------------------------------------------------------------------ #

def get_edl(ctx):
    row = ctx.latest_edl()
    return _cap(f"EDL v{row['version']} "
                f"({describe_edl(row['json'], ctx.duration)}):\n"
                + json.dumps(row["json"], indent=1)[:8000])


def render_preview(ctx):
    row = ctx.latest_edl()
    version = row["version"]
    if version in ctx.rendered_versions and \
            (ctx.last_preview or {}).get("edl_version") == version:
        return (f"Preview v{version} is already rendered and attached — "
                "no need to render again.")
    job_id = ctx.db.run(dbx.enqueue_job, ctx.project_id, ctx.job["user_id"],
                        "preview", {"edl_version": version})
    deadline = time.time() + config.PREVIEW_WAIT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1)
        j = ctx.db.run(dbx.get_job, job_id)
        if j["state"] == "done":
            result = j.get("result") or {}
            ctx.last_preview = result
            ctx.rendered_versions.add(version)
            out_dur = result.get("duration_s")
            if result.get("cached"):
                # Nothing new was encoded and no new file appeared — saying
                # "rendered and playing" here made the agent re-claim success
                # to a user who was reporting the player NOT updating.
                note = (f"Preview v{version} was ALREADY rendered — the "
                        f"existing {out_dur}s file is current and unchanged. "
                        "Re-rendering cannot change what the user sees; if "
                        "they say the video looks wrong or missing, the EDL "
                        "itself needs to change (or the problem is on their "
                        "screen, not in the render).")
            else:
                note = (f"Preview v{version} rendered: {out_dur}s "
                        f"(source {ctx.duration}s). It is attached to the "
                        "chat and the player updates to it automatically.")
            # A cached result is byte-identical to a render that was already
            # self-checked — re-running the paid vision call would bill the
            # user's turn for confirming an unchanged file.
            check = None if result.get("cached") else _self_check(ctx, result)
            if check:
                ctx.last_selfcheck = check
                note += f" Visual self-check: {check}"
            mw = result.get("midword_audit") or []
            if mw:
                note += (" MID-WORD AUDIT: " + "; ".join(mw[:5])
                         + " — snap these boundaries to word edges "
                           "(get_words) and re-render.")
            # Caption audit on what actually survived the cut: captions are
            # usually enabled BEFORE later cuts, so the add-time warning
            # can't see speech that a later keep_segments removed. A real
            # edit shipped "podcast captions" whose only transcribed word
            # was cut — the user saw an unchanged video.
            try:
                caps = row["json"].get("captions")
                if isinstance(caps, dict) \
                        and caps.get("mode") == "from_transcript":
                    _ctl = Timeline(row["json"]["keep"],
                                    row["json"].get("inserts") or [])
                    if not _ctl.kept_words(ctx.index.get("words", [])):
                        note += (" CAPTION AUDIT: captions are ON but ZERO "
                                 "transcribed words survive this cut — the "
                                 "render shows no caption text. Tell the "
                                 "user honestly (music-only videos "
                                 "transcribe to almost nothing).")
            except Exception:
                pass
            # Repetition audit on what actually survived the cut — the agent
            # must not tell the user repetitions are gone when they are not.
            try:
                edl = row["json"]
                tl = Timeline(edl["keep"], edl.get("inserts") or [])
                reps = find_repeated_phrases(
                    tl.kept_words(ctx.index.get("words", [])))
                if reps:
                    flagged = "; ".join(
                        f"'{t}' at " + ", ".join(f"{x}s" for x in times)
                        for t, times in reps[:4])
                    note += (f" REPETITION AUDIT: the output still repeats "
                             f"{flagged} — verify with get_kept_transcript "
                             "and cut the weaker take if these are true "
                             "repeats.")
            except Exception:
                pass
            return note
        if j["state"] == "failed":
            return (f"Preview render FAILED: {j.get('error')}. "
                    "Inspect the EDL (get_edl) and fix the invalid part, "
                    "then render again.")
    return ("Preview render is taking too long — it may still finish and "
            "attach to the chat. Summarize your edit for the user now.")


def _frame_context(edl):
    """One sentence of output-frame context for vision prompts, so letterbox
    bars on pad renders don't read as 'broken black frames'."""
    frame = (edl or {}).get("frame") or {}
    ratio, mode = frame.get("ratio"), frame.get("mode")
    if not ratio:
        return ""
    if mode in ("pad", "pad_blur"):
        bg = "blurred" if mode == "pad_blur" else "solid black"
        return (f"The output frame is {ratio} letterboxed ({bg} bars around "
                f"a smaller image are EXPECTED and are NOT broken frames; "
                f"dark footage can make whole thumbnails look near-black). ")
    return f"The output frame is tightly center-cropped to {ratio}. "


def _self_check(ctx, result):
    sheet_key = result.get("sheet_key")
    if not sheet_key or not llm.vision_available():
        return None
    local = os.path.join(ctx.workdir, "result_sheet.jpg")
    try:
        storage.download_to(sheet_key, local)
    except Exception:
        return None
    try:
        frame_note = _frame_context(ctx.latest_edl()["json"])
    except Exception:
        frame_note = ""
    return llm.ask_vision(
        frame_note +
        "This is a 3x3 contact sheet sampled evenly from an automatically "
        "edited video. In one or two sentences: does anything look broken "
        "(unexpected black frames, half-cut faces mid-action, missing "
        "captions if text was expected)? If it looks fine, say "
        "'looks clean'.",
        [local], max_tokens=200, purpose="vision_selfcheck",
        image_names=[sheet_key])


def ask_user(ctx, question):
    q = (question or "").strip()
    if not q:
        return "REJECTED: question is empty."
    raise AskUser(q[:600])


# ------------------------------------------------------------------ #
#  Registry + OpenAI schemas                                           #
# ------------------------------------------------------------------ #

def _seg_schema():
    return {"type": "array",
            "items": {"type": "array",
                      "items": {"type": "number"},
                      "minItems": 2, "maxItems": 2}}


# ONE definition of the caption-style properties, shared by every tool that
# accepts a style. These used to be duplicated per tool, which is exactly how
# a field could reach add_captions' schema but not set_caption_style's — the
# agent would then be told a field does not exist on the very tool it uses to
# restyle EXISTING captions. Keep in step with captions.STYLE_KEYS,
# schemas.CaptionStyle and _parse_partial_style's allowlist.
CAPTION_PRESETS = ["podcast", "beast", "karaoke", "elegant",
                   "stacked", "iridescent", "chrome", "editorial",
                   "fashion", "luxe", "impact", "classic"]
CAPTION_FONTS = ["Inter Display Black", "Inter Display ExtraBold",
                 "Inter Display Bold", "Anton", "Bebas Neue", "Archivo Black",
                 "Poppins Black", "Syne ExtraBold", "Playfair Display Black",
                 "Instrument Serif", "DM Serif Display", "Montserrat"]
CAPTION_ANIMS = ["fade", "pop", "slide_up", "punch", "blur_in", "whip",
                 "flash", "rise", "drop"]
_STYLE_PROPS = {
    "preset": {"type": "string", "enum": CAPTION_PRESETS},
    "color": {"type": "string"},
    "size": {"type": "string", "enum": ["s", "m", "l", "xl"]},
    "size_scale": {"type": "number"},
    "position": {"type": "string", "enum": ["bottom", "top", "middle"]},
    "uppercase": {"type": "boolean"},
    "dynamic": {"type": "boolean"},
    "highlight_color": {"type": "string"},
    "animation": {"type": "string", "enum": CAPTION_ANIMS},
    "font": {"type": "string", "enum": CAPTION_FONTS},
    "effect": {"type": "string", "enum": ["chroma", "chrome", "glow"]},
    "layout": {"type": "string", "enum": ["stack", "flow"]},
    "leading": {"type": "number"},
    "emphasis": {"type": "string",
                 "enum": ["big", "huge", "accent", "pop", "box", "serif",
                          "chrome", "glow", "chroma", "none"]},
    "emphasis_scale": {"type": "number"},
}

TOOLS = {
    "get_video_info": (get_video_info, "Video metadata plus index and EDL "
                       "summary. Call this first.", {}),
    "get_transcript": (get_transcript, "Sentence-level SOURCE transcript "
                       "with timestamps for a time range (source seconds). "
                       "For word-exact timing use get_words; for what the "
                       "current EDIT keeps, use get_kept_transcript.",
                       {"start": {"type": "number"},
                        "end": {"type": "number"}}),
    "get_kept_transcript": (get_kept_transcript, "The transcript the CURRENT "
                            "edit actually keeps, in program time with "
                            "matching source spans, plus automatic "
                            "repeated-phrase detection. ALWAYS call this "
                            "after cutting repetitions or tightening — it is "
                            "how you verify nothing repeated survived.", {}),
    "get_words": (get_words, "Word-level timestamps [{t0-t1 word}] for a "
                  "range of up to 60s (source seconds). THE source of truth "
                  "for cut points inside a sentence — never estimate word "
                  "timing from sentence ranges.",
                  {"start": {"type": "number"},
                   "end": {"type": "number"}}),
    "search_transcript": (search_transcript, "Find where something is said "
                          "(substring + fuzzy over sentences).",
                          {"query": {"type": "string"}}),
    "get_shots": (get_shots, "Shot list with visual captions for a time "
                  "range.", {"start": {"type": "number"},
                             "end": {"type": "number"}}),
    "find_silences": (find_silences, "Silences of at least min_seconds, with "
                      "midpoints and surrounding words — cut points should "
                      "snap to these midpoints or word boundaries.",
                      {"min_seconds": {"type": "number"}}),
    "list_assets": (list_assets, "Files in this project. kind='music' lists "
                    "uploaded music (use its storage_key with add_music or "
                    "add_voiceover); 'clip' lists uploaded video clips and "
                    "'image' reference images (use with insert_media); "
                    "'render' past renders; 'all' everything.",
                    {"kind": {"type": "string"}}),
    "look_at": (look_at, "Ask the vision model about up to 4 frames from a "
                "range of the MAIN video. Use for taste/visual questions. The "
                "transcript is accurate, so read speech from get_words / the "
                "transcript — don't use look_at to lip-read or guess a word.",
                {"start": {"type": "number"},
                 "end": {"type": "number"},
                 "question": {"type": "string"}}),
    "look_at_asset": (look_at_asset, "Ask the vision model about frames from "
                      "an UPLOADED clip or image (storage_key from "
                      "list_assets). THE way to choose which moment of a "
                      "long clip to splice in: ask e.g. 'at which timestamps "
                      "is the tool's page actually visible?' over the whole "
                      "clip, then call again on a narrower start/end, then "
                      "insert_media with clip_start_s at the chosen moment.",
                      {"asset_key": {"type": "string"},
                       "question": {"type": "string"},
                       "start": {"type": "number"},
                       "end": {"type": "number"}}),
    "keep_segments": (keep_segments, "REPLACE the whole keep list: the parts "
                      "of the SOURCE video that survive, [[start,end],...] "
                      "in seconds. Everything else is cut. Use only for "
                      "wholesale restructuring, always after get_edl — for "
                      "local fixes prefer cut_range/restore_range. "
                      "snap_to_words:true moves boundaries outward to word "
                      "edges so no word is clipped.",
                      {"segments": _seg_schema(),
                       "snap_to_words": {"type": "boolean"}}),
    "cut_range": (cut_range, "Remove ONE source-time range from the current "
                  "keep set (a local edit — the rest of the edit is "
                  "untouched). Creates a new EDL version. snap_to_words:true "
                  "keeps neighbouring words whole.",
                  {"start": {"type": "number"}, "end": {"type": "number"},
                   "snap_to_words": {"type": "boolean"}}),
    "restore_range": (restore_range, "Add a previously-cut source-time range "
                      "back into the keep set (undo one cut without touching "
                      "the rest). Creates a new EDL version.",
                      {"start": {"type": "number"}, "end": {"type": "number"},
                       "snap_to_words": {"type": "boolean"}}),
    "cut_silences": (cut_silences, "ONE-CALL silence trim — THE tool for "
                     "'cut the silences' / 'tighten this up' / 'remove the "
                     "dead air'. Cuts every detected pause at least "
                     "min_silence_s long (default 0.5s), keeping padding_s "
                     "(default 0.12s) of breathing room around speech and "
                     "snapping to word edges so no word is clipped. Do this "
                     "in one call instead of many cut_range calls; then "
                     "get_kept_transcript to verify.",
                     {"min_silence_s": {"type": "number"},
                      "padding_s": {"type": "number"}}),
    "remove_filler_words": (remove_filler_words, "ONE-CALL filler removal — "
                            "THE tool for 'remove the ums' / 'cut the uhs' / "
                            "'take out the filler words'. Cuts every um, uh, "
                            "er, hmm, etc. using the exact word timestamps "
                            "(deterministic, never estimated). Pass a custom "
                            "`words` list to target different tokens (e.g. "
                            "[\"like\",\"you know\"]) — the default set is "
                            "only the safe non-word hesitations.",
                            {"words": {"type": "array",
                                       "items": {"type": "string"}}}),
    "add_captions": (add_captions, "Burned captions. mode='from_transcript' "
                     "(word-timed from the real transcript, recommended) or "
                     "mode='off', or items=[{text,start,end,style?}] (source "
                     "seconds) for text the user dictates. "
                     "PREMIUM PRESETS (style.preset) are the headline "
                     "feature — professionally designed looks with real "
                     "fonts: 'podcast' (the viral podcast-reel look: bold "
                     "white words land on screen as spoken, keywords light "
                     "up in the accent color, get a highlight box or serif "
                     "italics, numbers render HUGE — the default choice for "
                     "premium/viral/TikTok captions), 'beast' (loud "
                     "MrBeast-style: ALL-CAPS impact font, centered, the "
                     "spoken word pops in the accent color), 'karaoke' (an "
                     "accent box follows each spoken word), 'elegant' "
                     "(calm lower-third, serif-italic accents — "
                     "interviews/luxury), 'classic' (plain legacy look). "
                     "With a preset, ALSO pass emphasis_words: 10-25 "
                     "impact words picked from the REAL transcript (money "
                     "words: numbers, outcomes, emotional peaks, names — "
                     "1-2 per sentence, verbatim as spoken); they get the "
                     "emphasis treatments wherever they appear. "
                     "highlight_color sets the accent (default warm "
                     "yellow); uppercase overrides the preset's casing; "
                     "position bottom/top/middle overrides its placement. "
                     "Other style fields: color '#RRGGBB', size s|m|l|xl "
                     "(presets are already big at 'm'), size_scale "
                     "0.5-3.0, dynamic:true (legacy karaoke, no preset), "
                     "animation fade|pop|slide_up (static captions only), "
                     "max_words_per_caption 1-12. Example — premium reel "
                     "captions: {mode:'from_transcript', style:{preset:"
                     "'podcast'}, emphasis_words:['money','22','future',"
                     "'opportunities']}. Example — dictated title card: "
                     "{items:[{text:'CHAPTER ONE', start:0, end:2.5, "
                     "style:{preset:'beast'}}]}. Stack presets (stacked/"
                     "iridescent/chrome/fashion/luxe/editorial/impact) compose "
                     "the phrase across lines of very different SIZES; font "
                     "picks a bundled family, emphasis 'big' enlarges keywords "
                     "WITHOUT recolouring them, leading below 1.0 overlaps "
                     "the lines, effect adds chroma/chrome/glow.",
                     {"mode": {"type": "string"},
                      "style": {"type": "object",
                                 "properties": _STYLE_PROPS},
                      "max_words_per_caption": {"type": "integer"},
                      "emphasis_words": {"type": "array",
                                         "items": {"type": "string"}},
                      "items": {"type": "array",
                                "items": {"type": "object"}}}),
    "list_music_library": (list_music_library, "Browse the BUILT-IN "
                           "royalty-free music library — tracks that are "
                           "always available with nothing uploaded. Returns "
                           "'library:<slug>' references to pass to "
                           "add_music. Optionally filter by mood: "
                           + ", ".join(music_library.MOODS) + ".",
                           {"mood": {"type": "string",
                                     "enum": list(music_library.MOODS)}}),
    "add_music": (add_music, "Mix music under the edit as BACKGROUND MUSIC "
                  "(default -18dB, ducked under speech). storage_key is "
                  "either a 'library:<slug>' from list_music_library() or an "
                  "exact key from list_assets(kind='music') (the user's own "
                  "uploads) — never invent one. start/end are OUTPUT-timeline "
                  "seconds and DEFAULT TO THE WHOLE VIDEO, so omit them for "
                  "'add some music'. Fades in/out by default. loop=true (the "
                  "default) repeats a short track to fill the span; "
                  "offset_s starts partway into the track, e.g. to skip a "
                  "slow intro. duck=true lowers music 12dB under speech.",
                  {"storage_key": {"type": "string"},
                   "start": {"type": "number"},
                   "end": {"type": "number"},
                   "gain_db": {"type": "number"},
                   "duck": {"type": "boolean"},
                   "offset_s": {"type": "number"},
                   "fade_in_s": {"type": "number"},
                   "fade_out_s": {"type": "number"},
                   "loop": {"type": "boolean"}}),
    "list_sfx_library": (list_sfx_library, "Browse the BUILT-IN sound-effects "
                        "pack — clicks, whooshes, impacts, risers, stings. "
                        "Always available with nothing uploaded. Returns "
                        "'sfx:<slug>' references to pass to add_sfx. "
                        "Optionally filter by category: "
                        + ", ".join(sfx_library.CATEGORIES) + ".",
                        {"category": {"type": "string",
                                      "enum": list(sfx_library.CATEGORIES)}}),
    "add_sfx": (add_sfx, "Punctuate a MOMENT with a one-shot sound effect — a "
                "whoosh on a cut, a click on a beat, an impact on a reveal. "
                "storage_key is either an 'sfx:<slug>' from "
                "list_sfx_library() or an exact key from "
                "list_assets(kind='music') — never invent one. `at` is an "
                "OUTPUT-timeline second (the edited program, not source "
                "time). This is NOT background music: it plays once, for as "
                "long as the sound is, and never ducks. Default -6dB.",
                {"storage_key": {"type": "string"},
                 "at": {"type": "number"},
                 "gain_db": {"type": "number"}}),
    "move_sfx": (move_sfx, "Retime an existing sound effect — 'the whoosh is "
                 "too early'. Keeps which sound and how loud. id from "
                 "get_edl.",
                 {"id": {"type": "string"}, "at": {"type": "number"}}),
    "remove_sfx": (remove_sfx, "Delete a sound effect by id (from get_edl).",
                   {"id": {"type": "string"}}),
    "swap_music": (swap_music, "Replace the TRACK of an existing music item "
                   "while keeping its position, level and fit — THE tool for "
                   "'use a different song' / 'try something more upbeat'. "
                   "id from get_edl; storage_key as for add_music.",
                   {"id": {"type": "string"},
                    "storage_key": {"type": "string"}}),
    "set_music_fit": (set_music_fit, "Retime or refit EXISTING music in "
                      "place — 'start the music later', 'let it run to the "
                      "end', 'fade it out', 'loop it', 'stop it ducking'. "
                      "Anything you omit is left alone. Use this instead of "
                      "remove+re-add, which loses the other settings. For "
                      "loudness use set_audio_gain.",
                      {"id": {"type": "string"},
                       "start": {"type": "number"},
                       "end": {"type": "number"},
                       "offset_s": {"type": "number"},
                       "fade_in_s": {"type": "number"},
                       "fade_out_s": {"type": "number"},
                       "loop": {"type": "boolean"},
                       "duck": {"type": "boolean"}}),
    "remove_music": (remove_music, "Remove one background-music item by its "
                     "id (see get_edl). Use this to cut the music entirely "
                     "or before re-adding it with a different range.",
                     {"id": {"type": "string"}}),
    "set_audio_gain": (set_audio_gain, "Change the loudness of an EXISTING "
                       "music, sound-effect or voiceover item without "
                       "re-adding it — THE tool for 'lower the music' / "
                       "'make the narration quieter' / 'that whoosh is too "
                       "loud'. kind: 'music', 'sfx' or 'voiceover'; id from "
                       "get_edl; gain_db e.g. -12.",
                       {"kind": {"type": "string",
                                 "enum": ["music", "sfx", "voiceover"]},
                        "id": {"type": "string"},
                        "gain_db": {"type": "number"}}),
    "set_caption_style": (set_caption_style, "Change how existing captions "
                          "LOOK without touching their text or timing. Pass "
                          "only the fields to change: 'make the captions "
                          "premium/viral' -> {\"style\":{\"preset\":"
                          "\"podcast\"}} (see add_captions for the preset "
                          "menu: podcast/beast/karaoke/elegant/classic), "
                          "'make it red' -> {\"style\":{\"color\":"
                          "\"#FF0000\"}}, 'center the captions' -> "
                          '{"style":{"position":"middle"}}, '
                          "'bigger / more dynamic captions' -> "
                          '{"style":{"size":"xl","dynamic":true}} '
                          "(dynamic = legacy karaoke without a preset; "
                          "presets animate on their own). "
                          "highlight_color changes the accent of "
                          "emphasized/spoken words; uppercase forces "
                          "casing; emphasis_words (top-level arg, with a "
                          "preset) replaces the emphasized keyword list. "
                          "For fine size control that the s|m|l|xl buckets "
                          "can't hit pass size_scale (0.5-3.0; 1.5 = 50% "
                          "bigger). Works for from_transcript and manual "
                          "captions; errors helpfully if no captions exist "
                          "yet.",
                          {"style": {"type": "object",
                                     "properties": _STYLE_PROPS},
                           "emphasis_words": {"type": "array",
                                              "items": {"type":
                                                        "string"}}}),
    "set_volume": (set_volume, "Volume automation on the ORIGINAL footage's "
                   "audio (the speaker) over a SOURCE-time span. NOT for "
                   "music or voiceover loudness — use set_audio_gain for "
                   "those.",
                   {"start": {"type": "number"}, "end": {"type": "number"},
                    "gain_db": {"type": "number"}}),
    "set_frame": (set_frame, "Set the output aspect ratio for every render. "
                  "ratio: source, 16:9, 9:16, 1:1 or 4:5; mode: crop "
                  "(center-crop, default), pad (black bars) or pad_blur "
                  "(blurred backdrop). Never upscales beyond the source's "
                  "pixels. Example — 'make it 9:16 for TikTok': "
                  "set_frame(\"9:16\", \"crop\").",
                  {"ratio": {"type": "string",
                             "enum": ["source", "16:9", "9:16", "1:1",
                                      "4:5"]},
                   "mode": {"type": "string",
                            "enum": ["crop", "pad", "pad_blur"]}}),
    "insert_media": (insert_media, "Splice an uploaded video clip or image "
                     "INTO the edit at ANY position in the FINAL edited "
                     "video — mid-take positions split the take cleanly at a "
                     "word edge, so 'in the middle of the talk' works "
                     "exactly. Call list_assets(kind='clip') or kind='image' "
                     "first and pass the exact storage_key. duration_s: how "
                     "long the insert plays (image default 3.0s; REQUIRED "
                     "for clips longer than 15s — never splice a long "
                     "recording whole). clip_start_s: where in the source "
                     "clip the window starts — use look_at_asset to pick "
                     "the right moment. motion (images only): 'zoom_in', "
                     "'zoom_out', 'pan_left' or 'pan_right' gives the still "
                     "a slow Ken Burns move instead of sitting frozen — use "
                     "it whenever the user wants an image to feel animated. "
                     "Inserted media is NOT transcribed — captions cover "
                     "the main footage only.",
                     {"asset_key": {"type": "string"},
                      "at_output_s": {"type": "number"},
                      "duration_s": {"type": "number"},
                      "clip_start_s": {"type": "number"},
                      "motion": {"type": "string",
                                 "enum": ["zoom_in", "zoom_out",
                                          "pan_left", "pan_right"]}}),
    "remove_insert": (remove_insert, "Remove one spliced insert by its id "
                      "(see get_edl) — the surrounding timing is restored "
                      "exactly. If an insert landed wrong, remove it BEFORE "
                      "re-inserting, or the old one stays in the video.",
                      {"id": {"type": "string"}}),
    "generate_image": (generate_image, "Create an image with AI — from a "
                       "text prompt alone, by RESTYLING A FRAME of the main "
                       "video (from_video_time_s, e.g. 'give this character "
                       "a long Ariana Grande-style ponytail'), or by "
                       "restyling an uploaded image (from_asset_key). The "
                       "result is saved as a project image asset; it "
                       "appears in the video ONLY after you insert_media "
                       "its storage_key (typically 2-4s with a Ken Burns "
                       "motion). It lands as a full-frame STILL moment — "
                       "it does not modify or track the moving footage. "
                       "For 'put X on the character': pick the best moment "
                       "(get_shots / look_at), restyle that frame, insert "
                       "it right there, and tell the user it's a "
                       "freeze-frame moment. aspect (text-to-image only) "
                       "defaults to the output frame / source ratio.",
                       {"prompt": {"type": "string"},
                        "from_video_time_s": {"type": "number"},
                        "from_asset_key": {"type": "string"},
                        "aspect": {"type": "string",
                                   "enum": ["16:9", "9:16", "1:1",
                                            "4:3", "3:4"]}}),
    "generate_sfx": (generate_sfx, "Create a one-shot sound effect with AI "
                     "from a text description ('a deep cinematic whoosh', 'an "
                     "old camera shutter', 'glass shattering') and place it at "
                     "a MOMENT in the program. Use this when the built-in pack "
                     "(list_sfx_library) has nothing close — otherwise prefer "
                     "the pack, it's instant and free. `at` is an OUTPUT-"
                     "timeline second. duration_s is optional (0.5-22s; omit "
                     "to let it pick a natural length). Costs credits per "
                     "sound. Default -6dB.",
                     {"prompt": {"type": "string"},
                      "at": {"type": "number"},
                      "duration_s": {"type": "number"},
                      "gain_db": {"type": "number"}}),
    "generate_video": (generate_video, "Generate a VIDEO clip with AI — real "
                       "moving footage — from a text prompt, or animate an "
                       "existing image by passing from_image_asset_key (a "
                       "generated or uploaded image's storage_key). The clip "
                       "is saved as a project asset; it reaches the program "
                       "ONLY after you insert_media its storage_key. This is "
                       "the tool for 'make me a video of X' / 'bring this "
                       "photo to life'. It is SLOW (tens of seconds to a few "
                       "minutes) and costs credits per second, so use it "
                       "deliberately. duration_s ~5s is typical.",
                       {"prompt": {"type": "string"},
                        "from_image_asset_key": {"type": "string"},
                        "duration_s": {"type": "number"}}),
    "fetch_url": (fetch_url, "Download media from a LINK the user gave you "
                  "and save it as a project asset — a video, a song, or an "
                  "image. Works with direct file links (Dropbox, Drive, a "
                  "CDN, a stock library) and with page links (YouTube, "
                  "TikTok, Vimeo, SoundCloud). Use this whenever the user "
                  "pastes a URL for something they want in the edit; never "
                  "tell them to upload a file you could have fetched. The "
                  "file type is detected automatically — pass as_kind only "
                  "to force audio-only from a video page ('music'). The "
                  "result is saved to the project but is NOT in the video "
                  "until you add it with insert_media (clip/image) or "
                  "add_music (audio).",
                  {"url": {"type": "string"},
                   "as_kind": {"type": "string",
                               "enum": ["clip", "music", "image"]}}),
    "set_color_grade": (set_color_grade, "Apply a color-grade preset to the "
                        "whole video (captions stay unstyled): vibrant, "
                        "warm, cool, bw, vintage, cinematic — or 'none' to "
                        "clear. THE tool when the user asks for a filter / "
                        "look / mood.",
                        {"preset": {"type": "string",
                                    "enum": ["vibrant", "warm", "cool", "bw",
                                             "vintage", "cinematic",
                                             "none"]}}),
    "add_zoom": (add_zoom, "Zoom on a time range of the FINAL edited video "
                 "(output seconds) — the standard retention effect for "
                 "emphasis on a key line. strength 0.05-1.0 (default 0.25 = "
                 "25% closer). mode: 'punch' (default, instant step), "
                 "'ease' (smoothly ramps in and out — use when the user "
                 "wants it subtle/animated), 'push_in' / 'pull_out' "
                 "(continuous Ken Burns drift across the whole window — "
                 "use for slow cinematic movement). Use 1-3 short zooms at "
                 "emphatic moments, not wall-to-wall.",
                 {"start": {"type": "number"}, "end": {"type": "number"},
                  "strength": {"type": "number"},
                  "mode": {"type": "string",
                           "enum": ["punch", "ease", "push_in",
                                    "pull_out"]}}),
    "remove_zoom": (remove_zoom, "Remove one zoom by its id (see "
                    "get_edl).", {"id": {"type": "string"}}),
    "set_fades": (set_fades, "Fade from black at the start and/or to black "
                  "at the end (video + audio). Seconds; 0 clears. Example: "
                  "set_fades(fade_in_s=0.5, fade_out_s=0.8).",
                  {"fade_in_s": {"type": "number"},
                   "fade_out_s": {"type": "number"}}),
    "set_transitions": (set_transitions, "Transitions at EVERY cut point "
                        "and insert boundary: a quick dip through black "
                        "(style 'dip_black') or a white flash "
                        "('dip_white'), duration_s 0.1-1.0 (default 0.3). "
                        "'none' removes them (hard cuts again). THE tool "
                        "when the user asks for transitions between "
                        "clips/cuts. True crossfades (overlapping footage) "
                        "are NOT supported — offer a dip instead and say "
                        "so.",
                        {"style": {"type": "string",
                                   "enum": ["dip_black", "dip_white",
                                            "none"]},
                         "duration_s": {"type": "number"}}),
    "blur_region": (blur_region, "Blur, pixelate or black-out a fixed "
                    "RECTANGLE of the original footage — THE tool to "
                    "remove/censor a burned-in username, gamertag, "
                    "watermark, logo or other on-screen text (pixels can't "
                    "be erased, but this hides them). x,y = TOP-LEFT corner "
                    "and w,h = size, all as FRACTIONS (0-1) of the SOURCE "
                    "frame — exactly the frames look_at shows you; a 9:16 "
                    "or other output reframe moves the censored footage "
                    "with it automatically, and spliced-in clips/images are "
                    "never censored. Example — a username in the top-right "
                    "corner: x=0.6, y=0.02, w=0.38, h=0.1. FIRST look_at "
                    "the video asking exactly where the text sits (corner? "
                    "edge? how big?), then blur_region, then render_preview "
                    "and CHECK the sheet — if text still shows, remove_blur "
                    "and place a bigger region. start/end (output seconds) "
                    "optionally limit when it applies; omit both for the "
                    "whole video. mode: 'blur' (soft, default), 'pixelate' "
                    "(mosaic), 'black' (solid bar). The rectangle does NOT "
                    "track motion — text that moves with the camera may "
                    "leave it; verify and tell the user honestly.",
                    {"x": {"type": "number"}, "y": {"type": "number"},
                     "w": {"type": "number"}, "h": {"type": "number"},
                     "mode": {"type": "string",
                              "enum": ["blur", "pixelate", "black"]},
                     "start": {"type": "number"},
                     "end": {"type": "number"}}),
    "remove_blur": (remove_blur, "Remove one censor region by its id (see "
                    "get_edl), or ALL censor regions when id is omitted.",
                    {"id": {"type": "string"}}),
    "add_voiceover": (add_voiceover, "Lay an uploaded audio file OVER the "
                      "whole program from start_output_s (a position in the "
                      "FINAL edited video, default 0). duck_others (default "
                      "true) lowers all other audio 12dB while it plays. "
                      "Use a storage_key from list_assets(kind='music').",
                      {"asset_key": {"type": "string"},
                       "start_output_s": {"type": "number"},
                       "gain_db": {"type": "number"},
                       "duck_others": {"type": "boolean"}}),
    "remove_voiceover": (remove_voiceover, "Remove one voiceover by its id "
                         "(see get_edl).", {"id": {"type": "string"}}),
    "get_edl": (get_edl, "Current EDL JSON and version.", {}),
    "render_preview": (render_preview, "Render the current EDL as a fast "
                       "480p preview from the proxy, attach it to chat, and "
                       "get a visual self-check. ALWAYS call this before "
                       "your final summary.", {}),
    "ask_user": (ask_user, "Ask the user ONE specific question and wait for "
                 "their reply (ends this turn). Only for taste calls tools "
                 "cannot answer.", {"question": {"type": "string"}}),
}

REQUIRED_ARGS = {
    "search_transcript": ["query"],
    "look_at": ["start", "end", "question"],
    "look_at_asset": ["asset_key", "question"],
    "keep_segments": ["segments"],
    "cut_range": ["start", "end"],
    "restore_range": ["start", "end"],
    "set_caption_style": [],
    # start/end default to the whole program, so "add some music" needs only
    # a track.
    "add_music": ["storage_key"],
    "list_music_library": [],
    "list_sfx_library": [],
    "add_sfx": ["storage_key", "at"],
    "move_sfx": ["id", "at"],
    "remove_sfx": ["id"],
    "swap_music": ["id", "storage_key"],
    "set_music_fit": ["id"],
    "remove_music": ["id"],
    "set_audio_gain": ["kind", "id", "gain_db"],
    "set_volume": ["start", "end", "gain_db"],
    "set_frame": ["ratio"],
    "insert_media": ["asset_key", "at_output_s"],
    "remove_insert": ["id"],
    "set_color_grade": ["preset"],
    "add_zoom": ["start", "end"],
    "remove_zoom": ["id"],
    "set_transitions": ["style"],
    "blur_region": ["x", "y", "w", "h"],
    "add_voiceover": ["asset_key"],
    "remove_voiceover": ["id"],
    "generate_image": ["prompt"],
    "generate_sfx": ["prompt", "at"],
    "generate_video": ["prompt"],
    "fetch_url": ["url"],
    "ask_user": ["question"],
}

# The loop uses this to build TURN FACTS: a write "succeeded" when its result
# is a version diff line (write_edl's "EDL vX -> vY: ..." format).
# generate_image and fetch_url are here for the capabilities digest; their
# successes are tracked separately via ctx.images_generated / ctx.urls_fetched
# (neither writes the EDL — they create an ASSET the agent then places).
WRITE_TOOLS = {"keep_segments", "cut_range", "restore_range",
               "cut_silences", "remove_filler_words", "add_captions",
               "set_caption_style", "add_music", "remove_music",
               "swap_music", "set_music_fit",
               "add_sfx", "move_sfx", "remove_sfx",
               "set_audio_gain", "set_volume", "set_frame",
               "insert_media", "remove_insert", "add_voiceover",
               "remove_voiceover", "set_color_grade", "add_zoom",
               "remove_zoom", "set_fades", "set_transitions",
               "blur_region", "remove_blur", "generate_image",
               "generate_sfx", "generate_video", "fetch_url"}


def _tool_disabled(name):
    """Tools whose backing service is not configured are hidden entirely —
    the model must never see (or advertise) a capability that would only
    return 'unavailable'."""
    if name == "generate_image":
        return not llm.image_available()
    if name == "generate_sfx":
        return not eleven.sound_gen_available()
    if name == "generate_video":
        return not videogen.video_gen_available()
    if name == "fetch_url":
        return not config.URL_FETCH_ENABLED
    # Same rule for the music library: a deployment whose image shipped no
    # tracks must not advertise one, or the agent offers music it cannot
    # deliver and then has to walk it back.
    if name == "list_music_library":
        return not music_library.CATALOG
    if name == "list_sfx_library":
        return not sfx_library.CATALOG
    return False


def capabilities_digest():
    """One line per WRITE tool, generated from the registry at turn start —
    the model checks requests against this before promising anything, and it
    can never go stale because nobody maintains it by hand."""
    lines = []
    for name, (_fn, desc, props) in TOOLS.items():
        if name not in WRITE_TOOLS or _tool_disabled(name):
            continue
        params = ", ".join(props.keys())
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {name}({params}): {first}.")
    return "\n".join(lines)


def openai_tools():
    out = []
    for name, (_fn, desc, props) in TOOLS.items():
        if _tool_disabled(name):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": props,
                               "required": REQUIRED_ARGS.get(name, [])},
            },
        })
    return out


def execute(ctx, name, args):
    """Dispatch one tool call. Returns a string for the model (AskUser
    propagates)."""
    entry = TOOLS.get(name)
    if not entry:
        return (f"Unknown tool '{name}'. Available: "
                + ", ".join(TOOLS))
    fn = entry[0]
    try:
        return fn(ctx, **(args or {}))
    except AskUser:
        raise
    except TypeError as e:
        return (f"REJECTED: bad arguments for {name}: {e}. "
                "Check the tool's parameter names.")
    except Exception as e:
        return f"Tool {name} errored: {str(e)[:300]}. Try a different approach."
