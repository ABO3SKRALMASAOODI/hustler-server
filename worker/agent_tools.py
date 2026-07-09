"""Agent tools. Every argument is validated and clamped, every error is a
short instructive string the model can act on, every output fits the token
budget. Write tools create new EDL versions and return one-line diffs."""

import difflib
import json
import os
import re
import time

import audit
import config
import db as dbx
import llm
import media
import storage
from schemas import (CaptionStyle, EDLValidationError, Frame, describe_edl,
                     edl_signature, keep_boundaries, output_duration,
                     program_duration, validate_edl, MAX_INSERT_DURATION_S,
                     GAIN_MIN_DB, GAIN_MAX_DB)
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
        self.index = index
        self.duration = float(index["video"]["duration"])
        self.workdir = workdir
        self._proxy_local = None
        self.last_preview = None      # set by render_preview
        self.last_selfcheck = None    # vision one-liner from the last preview
        self.versions_written = []    # EDL versions created this turn
        self.rendered_versions = set()  # versions with a successful preview
        self.autorendered = False     # loop set: model skipped render_preview
        self.write_calls = []         # successful write tool names this turn

    def clamp(self, t):
        try:
            t = float(t)
        except (TypeError, ValueError):
            raise ValueError(f"'{t}' is not a number of seconds")
        return round(min(max(t, 0.0), self.duration), 2)

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
            v = self.db.run(dbx.insert_edl, self.project_id,
                            default_edl(self.duration), "agent")
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
    """Project files the user has uploaded or the system has produced."""
    kinds = {"music": ["music", "audio"], "image": ["image_ref"],
             "clip": ["video_clip"], "render": ["render"],
             "all": ["music", "audio", "image_ref", "video_clip",
                     "render", "original"]}
    sel = kinds.get((kind or "music").strip().lower())
    if not sel:
        return ("REJECTED: kind must be one of "
                f"{', '.join(sorted(kinds))}.")
    rows = ctx.db.run(dbx.assets_by_kinds, ctx.project_id, sel)
    if not rows:
        if sel == kinds["music"]:
            return ("No music files in this project. Ask the user to attach "
                    "one with the paperclip button in chat (mp3/wav/m4a).")
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
    result = ctx.write_edl(edl, desc)
    if not result.startswith("EDL v"):
        return result
    warn = audit.boundary_warning_lines(new_keep, words, silences,
                                        ctx.duration)
    if snap_to_words:
        warn = []   # snapping guarantees word-clean boundaries
    if check_regression:
        warn += audit.regression_warnings(prev_keep, new_keep, ctx.index)
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
                 max_words_per_caption=None):
    edl = dict(ctx.latest_edl()["json"])
    parsed_style = _parse_style(style)
    if isinstance(parsed_style, str):
        return "REJECTED: " + parsed_style[5:]
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
        edl["captions"] = {"mode": "from_transcript",
                           "max_words_per_caption": mw,
                           "style": parsed_style}
        desc = "captions from transcript enabled"
        if mw:
            desc += f", <= {mw} words each"
        if parsed_style:
            desc += f", style {parsed_style}"
        return ctx.write_edl(edl, desc)
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
                '{"color":"#RRGGBB","size":"s|m|l|xl",'
                '"position":"bottom|top|middle","dynamic":true|false}')
    unknown = sorted(set(style) - {"color", "size", "position", "dynamic"})
    if unknown:
        return (f"ERR: unknown style field(s) {unknown} — only color, size, "
                "position and dynamic exist (no fonts or outlines; dynamic "
                "gives animated word-by-word pop captions).")
    try:
        validated = CaptionStyle.model_validate(style).model_dump()
    except Exception as e:
        return (f"ERR: bad style: {str(e)[:160]}. Use "
                '{"color":"#RRGGBB","size":"s|m|l|xl",'
                '"position":"bottom|top|middle","dynamic":true|false}.')
    return {k: validated[k] for k in style}


def merge_caption_style(captions, partial):
    """Merge a partial style into an existing captions value (from_transcript
    dict or manual item list). Returns the new captions value."""
    if isinstance(captions, dict):
        new = dict(captions)
        st = dict(captions.get("style") or {})
        st.update(partial)
        new["style"] = st
        return new
    out = []
    # dynamic word-pop only exists for from_transcript captions — writing it
    # into manual items would let the reply claim an effect the renderer
    # ignores.
    item_partial = {k: v for k, v in partial.items() if k != "dynamic"}
    for it in captions:
        nit = dict(it)
        st = dict(it.get("style") or {})
        st.update(item_partial)
        nit["style"] = st
        out.append(nit)
    return out


def set_caption_style(ctx, style):
    partial = _parse_partial_style(style)
    if isinstance(partial, str):
        return "REJECTED: " + partial[5:]
    edl = dict(ctx.latest_edl()["json"])
    caps = edl.get("captions")
    if not caps:
        return ("REJECTED: no captions exist yet — call "
                "add_captions(mode='from_transcript') first (you can pass "
                "a style there directly).")
    edl["captions"] = merge_caption_style(caps, partial)
    result = ctx.write_edl(
        edl, f"caption style updated: {json.dumps(partial)}")
    if isinstance(caps, list) and "dynamic" in partial:
        result += ("\nNote: dynamic word-by-word captions only apply to "
                   "from_transcript captions — manual caption items ignore "
                   "the dynamic flag.")
    return result


def add_music(ctx, storage_key, start, end, gain_db=-18.0, duck=True):
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if not asset or asset["kind"] not in ("music", "audio"):
        avail = ctx.db.run(
            lambda conn: _music_assets(conn, ctx.project_id))
        hint = ("Available music storage_keys: " +
                "; ".join(a["storage_key"] for a in avail)
                if avail else "No music files uploaded to this project yet — "
                              "ask the user to upload one.")
        return f"REJECTED: '{storage_key}' is not a music asset here. {hint}"
    edl = dict(ctx.latest_edl()["json"])
    out_dur = output_duration(edl["keep"])
    try:
        s = round(min(max(float(start), 0.0), max(0.0, out_dur - 0.1)), 2)
        e = round(min(max(float(end), s + 0.1), out_dur), 2)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers (OUTPUT-timeline seconds)."
    try:
        g = float(gain_db)
    except (TypeError, ValueError):
        return "REJECTED: gain_db must be a number."
    music = [dict(m) for m in (edl.get("music") or [])]
    item = {"id": _next_item_id(music, "mus"), "storage_key": storage_key,
            "start": s, "end": e, "gain_db": g, "duck": bool(duck)}
    music.append(item)
    edl["music"] = music
    res = ctx.write_edl(
        edl, f"music '{os.path.basename(storage_key)}' at {s}-{e}s "
             f"(output timeline), {g}dB, duck={bool(duck)} [{item['id']}]")
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
             f"('{os.path.basename(hit['storage_key'])}', "
             f"{hit['start']}-{hit['end']}s)")


def set_audio_gain(ctx, kind, id, gain_db):
    """Change the loudness of an EXISTING music or voiceover item."""
    if kind not in ("music", "voiceover"):
        return "REJECTED: kind must be 'music' or 'voiceover'."
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
        edl, f"{kind} {id} ('{os.path.basename(key)}') gain "
             f"{old:+.1f}dB -> {g:+.1f}dB")


def _music_assets(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("""SELECT storage_key, meta FROM assets
                       WHERE project_id = %s AND kind IN ('music','audio')
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


def _next_item_id(items, prefix):
    n = 1
    taken = {it.get("id") for it in items}
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def _resolve_media_asset(ctx, asset_key, kinds):
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, asset_key)
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


def insert_media(ctx, asset_key, at_output_s, duration_s=None):
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref"))
    if err:
        return err
    kind = "image" if asset["kind"] == "image_ref" else "video"
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — a position in the "
                "FINAL edited video, in seconds.")
    if kind == "image":
        try:
            dur = round(min(max(float(duration_s if duration_s is not None
                                       else 3.0), 0.2), 60.0), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."
    else:
        clip_dur = _asset_media_duration(ctx, asset)
        try:
            dur = round(min(max(float(duration_s), 0.2), clip_dur,
                            MAX_INSERT_DURATION_S), 2) \
                if duration_s is not None else round(
                    min(clip_dur, MAX_INSERT_DURATION_S), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."

    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    # Snap the requested FINAL-time position to the keep boundary whose
    # final-time position is nearest (inserts splice between segments).
    tl = Timeline(edl["keep"], inserts)
    pre_bounds = keep_boundaries(edl["keep"])
    final_of = {b: b + sum(d for a, d in tl.ins if a <= b + 1e-6)
                for b in pre_bounds}
    target_pre = min(pre_bounds, key=lambda b: abs(final_of[b] - at))
    snapped = abs(final_of[target_pre] - at) > 0.05
    item = {"id": _next_item_id(inserts, "ins"), "asset_key": asset_key,
            "kind": kind, "at_output_s": target_pre, "duration_s": dur}
    edl["inserts"] = inserts + [item]
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    desc = (f"inserted {kind} '{name}' ({dur}s) at "
            f"{round(final_of[target_pre], 2)}s of the edited video "
            f"[{item['id']}]")
    if snapped:
        desc += (f" (snapped from {round(at, 2)}s to the nearest segment "
                 "boundary)")
    result = ctx.write_edl(edl, desc)
    if result.startswith("EDL v"):
        result += ("\nNote: captions cover the main footage only — inserted "
                   "media is not transcribed or captioned.")
    return result


def remove_insert(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    hit = next((i for i in inserts if i.get("id") == id), None)
    if not hit:
        have = ", ".join(i.get("id", "?") for i in inserts) or "none"
        return (f"REJECTED: no insert with id '{id}'. Existing inserts: "
                f"{have}. Call get_edl to see them.")
    edl["inserts"] = [i for i in inserts if i.get("id") != id]
    return ctx.write_edl(
        edl, f"removed insert {id} "
             f"('{os.path.basename(hit['asset_key'])}', {hit['duration_s']}s) "
             "— prior timing restored")


def add_voiceover(ctx, asset_key, start_output_s=0.0, gain_db=0.0,
                  duck_others=True):
    asset, err = _resolve_media_asset(ctx, asset_key, ("music", "audio"))
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
            note = (f"Preview v{version} rendered: {out_dur}s "
                    f"(source {ctx.duration}s). It is attached to the chat "
                    f"and playing in the user's workspace.")
            check = _self_check(ctx, result)
            if check:
                ctx.last_selfcheck = check
                note += f" Visual self-check: {check}"
            mw = result.get("midword_audit") or []
            if mw:
                note += (" MID-WORD AUDIT: " + "; ".join(mw[:5])
                         + " — snap these boundaries to word edges "
                           "(get_words) and re-render.")
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
                "range. Use for taste/visual questions the transcript can't "
                "answer.", {"start": {"type": "number"},
                            "end": {"type": "number"},
                            "question": {"type": "string"}}),
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
    "add_captions": (add_captions, "Burned captions. mode='from_transcript' "
                     "(word-timed from the real transcript, recommended) or "
                     "mode='off', or items=[{text,start,end,style?}] (source "
                     "seconds) for text the user dictates. Optional style "
                     "{color:'#RRGGBB', size:'s|m|l|xl', "
                     "position:'bottom|top|middle', dynamic:true} "
                     "and max_words_per_caption (1-12) to show short, "
                     "punchy caption chunks. dynamic:true = animated "
                     "word-by-word pop captions (TikTok style) — THE choice "
                     "when the user wants big/dynamic/viral captions; pair "
                     "with size 'xl'. Example — 'captions 3 words max, red, "
                     "at the top': {mode:'from_transcript', "
                     "max_words_per_caption:3, style:{color:'#FF0000', "
                     "position:'top'}}. Example — one manual title card: "
                     "{items:[{text:'CHAPTER ONE', start:0, end:2.5, "
                     "style:{size:'l'}}]}. There are NO other style fields — "
                     "fonts and outline colors are not supported; say so if "
                     "asked.",
                     {"mode": {"type": "string"},
                      "style": {"type": "object",
                                "properties": {
                                    "color": {"type": "string"},
                                    "size": {"type": "string",
                                             "enum": ["s", "m", "l", "xl"]},
                                    "position": {"type": "string",
                                                 "enum": ["bottom", "top",
                                                          "middle"]},
                                    "dynamic": {"type": "boolean"}}},
                      "max_words_per_caption": {"type": "integer"},
                      "items": {"type": "array",
                                "items": {"type": "object"}}}),
    "add_music": (add_music, "Mix a project music file under the edit as "
                  "BACKGROUND MUSIC (default -18dB, ducked under speech). "
                  "Call list_assets(kind='music') first and pass the exact "
                  "storage_key it returns — if none exist, ask the user to "
                  "attach a file instead of guessing. start/end are "
                  "OUTPUT-timeline seconds (position in the finished video). "
                  "duck=true lowers music 12dB under speech.",
                  {"storage_key": {"type": "string"},
                   "start": {"type": "number"},
                   "end": {"type": "number"},
                   "gain_db": {"type": "number"},
                   "duck": {"type": "boolean"}}),
    "remove_music": (remove_music, "Remove one background-music item by its "
                     "id (see get_edl). Use this to cut the music entirely "
                     "or before re-adding it with a different range.",
                     {"id": {"type": "string"}}),
    "set_audio_gain": (set_audio_gain, "Change the loudness of an EXISTING "
                       "music or voiceover item without re-adding it — THE "
                       "tool for 'lower the music' / 'make the narration "
                       "quieter'. kind: 'music' or 'voiceover'; id from "
                       "get_edl; gain_db e.g. -12.",
                       {"kind": {"type": "string",
                                 "enum": ["music", "voiceover"]},
                        "id": {"type": "string"},
                        "gain_db": {"type": "number"}}),
    "set_caption_style": (set_caption_style, "Change how existing captions "
                          "LOOK without touching their text or timing. Pass "
                          "only the fields to change: e.g. 'make it red' -> "
                          '{"style":{"color":"#FF0000"}}, \'center the '
                          'captions\' -> {"style":{"position":"middle"}}, '
                          "'bigger / more dynamic captions' -> "
                          '{"style":{"size":"xl","dynamic":true}} '
                          "(dynamic = animated word-by-word pop). Works for "
                          "from_transcript and manual captions; errors "
                          "helpfully if no captions exist yet.",
                          {"style": {"type": "object",
                                     "properties": {
                                         "color": {"type": "string"},
                                         "size": {"type": "string",
                                                  "enum": ["s", "m", "l",
                                                           "xl"]},
                                         "position": {"type": "string",
                                                      "enum": ["bottom",
                                                               "top",
                                                               "middle"]},
                                         "dynamic": {"type":
                                                     "boolean"}}}}),
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
                     "INTO the edit at a position in the FINAL edited video "
                     "(snapped to the nearest segment boundary — the result "
                     "notes any snap). Call list_assets(kind='clip') or "
                     "kind='image' first and pass the exact storage_key. "
                     "duration_s: required intent for images (default 3.0s); "
                     "for clips defaults to the full clip. Inserted media is "
                     "NOT transcribed — captions cover the main footage "
                     "only.",
                     {"asset_key": {"type": "string"},
                      "at_output_s": {"type": "number"},
                      "duration_s": {"type": "number"}}),
    "remove_insert": (remove_insert, "Remove one spliced insert by its id "
                      "(see get_edl) — the surrounding timing is restored "
                      "exactly.", {"id": {"type": "string"}}),
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
    "keep_segments": ["segments"],
    "cut_range": ["start", "end"],
    "restore_range": ["start", "end"],
    "set_caption_style": ["style"],
    "add_music": ["storage_key", "start", "end"],
    "remove_music": ["id"],
    "set_audio_gain": ["kind", "id", "gain_db"],
    "set_volume": ["start", "end", "gain_db"],
    "set_frame": ["ratio"],
    "insert_media": ["asset_key", "at_output_s"],
    "remove_insert": ["id"],
    "add_voiceover": ["asset_key"],
    "remove_voiceover": ["id"],
    "ask_user": ["question"],
}

# The loop uses this to build TURN FACTS: a write "succeeded" when its result
# is a version diff line (write_edl's "EDL vX -> vY: ..." format).
WRITE_TOOLS = {"keep_segments", "cut_range", "restore_range", "add_captions",
               "set_caption_style", "add_music", "remove_music",
               "set_audio_gain", "set_volume", "set_frame",
               "insert_media", "remove_insert", "add_voiceover",
               "remove_voiceover"}


def capabilities_digest():
    """One line per WRITE tool, generated from the registry at turn start —
    the model checks requests against this before promising anything, and it
    can never go stale because nobody maintains it by hand."""
    lines = []
    for name, (_fn, desc, props) in TOOLS.items():
        if name not in WRITE_TOOLS:
            continue
        params = ", ".join(props.keys())
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {name}({params}): {first}.")
    return "\n".join(lines)


def openai_tools():
    out = []
    for name, (_fn, desc, props) in TOOLS.items():
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
