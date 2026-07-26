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
import inpaint
import llm
import media
import model_prices
import music_library
import perception
import sfx_library
import stock
import storage
import videogen
import timeline as timeline_mod
import url_media
import webrecord
from captions import KARAOKE_HARD_MAX
from schemas import (CANVAS_DIMS, CaptionStyle, clean_fingerprint,
                     EDLValidationError, Frame,
                     HEX_COLOR,
                     canvas_edl, clip_anim, default_edl, describe_edl,
                     DEFAULT_CANVAS_FPS,
                     edl_signature, is_canvas_program, keep_boundaries,
                     output_duration, program_duration, validate_edl,
                     MAX_INSERT_DURATION_S, GAIN_MIN_DB, GAIN_MAX_DB,
                     GRADE_PRESETS, TRANSITION_STYLES, TRANSITION_MIN_S,
                     TRANSITION_MAX_S, TRANSITION_SCOPES,
                     OVERLAY_ANIMS, OVERLAY_SCALE_MIN,
                     OVERLAY_SCALE_MAX, SPEED_FACTOR_MIN, SPEED_FACTOR_MAX,
                     STYLIZE_KINDS, TEXT_ANIMS, TEXT_FONTS, TEXT_TEMPLATES,
                     ZOOM_STRENGTH_MIN, ZOOM_STRENGTH_MAX,
                     ZOOM_PATH_MAX_POINTS)
from timeline import Timeline, card_text_window, insert_windows

# Karaoke grouping: the renderer's legacy clamp (captions.KARAOKE_HARD_MAX,
# 4) applies to EDLs with no explicit group — 3 stored prod EDLs (proj 13,
# dynamic + mw=6) render 4-word groups under it, and a stored version's
# render can never change. Group sizes up to 6 therefore ride the NEW
# captions.karaoke_group_n field, BAKED at write time by
# _bake_karaoke_group below: new writes opt in, old versions render exactly
# as always.
KARAOKE_TOOL_MAX = 6
assert KARAOKE_TOOL_MAX >= KARAOKE_HARD_MAX


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
        self._perception = None       # main video's audio analysis, cached
        self._asset_perception = {}   # asset/library key -> audio analysis
        self.last_preview = None      # set by render_preview
        self.last_selfcheck = None    # vision one-liner from the last preview
        self.versions_written = []    # EDL versions created this turn
        # Every EDL state visited this turn -> the version it was first seen
        # at. A write that lands on a state already in here is a CYCLE: the
        # turn has undone itself and is about to repeat the same attempt.
        # See write_edl for why this is reported rather than blocked.
        self._states_seen = {}
        self.rendered_versions = set()  # versions with a successful preview
        self.autorendered = False     # loop set: model skipped render_preview
        self.write_calls = []         # successful write tool names this turn
        self.images_generated = []    # assets created by generate_image
        self.sfx_generated = []       # sounds created by generate_sfx
        self.videos_generated = []    # clips created by generate_video
        self.urls_fetched = []        # assets created by fetch_url
        self.web_recordings = []      # assets created by record_website
        # Audio lifted out of an uploaded VIDEO (extract_audio, or any audio
        # tool handed a clip). A real project asset, so a turn that only did
        # this did NOT do nothing — the honesty layer reads it.
        self.audio_extracted = []
        # Stock search results are cached per TURN so add_stock_media places
        # the exact clip the model chose from, not whatever a repeat query
        # returns (providers reorder results between identical calls).
        self.stock_results = {}       # id -> search hit
        self.stock_added = []         # assets created by add_stock_media
        # USD cost of non-LLM generations this turn (sfx flat, video per-second)
        # — added to running_credits so the in-turn spend cap sees them.
        self.gen_extra_cost_usd = 0.0
        # Live per-turn model spend, for the graceful spend cap. tokens are
        # accumulated by the loop's llm recorder; images are priced flat.
        self.tokens_in = 0
        self.tokens_out = 0
        # The slice of tokens_in the provider served from its prompt cache.
        # Billed at the (far cheaper) cache rate, so the cap must know about it
        # or it would stop a turn on spend the user is never charged for.
        self.tokens_cached_in = 0
        # Same three numbers again, but split BY MODEL — a turn can legitimately
        # touch two providers (the agent on one, vision on another, and paying
        # users on a different agent model from free ones), and one blended rate
        # is wrong for at least one of them. Keyed by model id:
        #   {model: {"in": n, "out": n, "cached": n, "reasoning": n}}
        self.model_usage = {}
        self.credit_budget = None     # set by run_agent_job; None = uncapped
        # The client + model THIS turn talks to, resolved from the user's plan
        # by run_agent_job (llm.agent_client_for). Defaults keep any caller that
        # builds a context without going through run_agent_job working.
        self.llm_client = None
        self.agent_model = config.AGENT_MODEL
        # Does this user hold a plan (a trial counts)? Decides the model above
        # and what the out-of-credits message may honestly promise — a
        # subscriber's pool comes back, a free account's does not.
        self.subscribed = False
        # Which plan, and whether it is still in its 3 free days. `plan` picks
        # the provider (Frontier runs agent AND vision on the frontier model);
        # `trialing` is the third out-of-credits truth — that pool is 10% of
        # the plan and the rest is released by converting, not by waiting.
        self.plan = "free"
        self.trialing = False

    def add_usage(self, model, tokens_in, tokens_out, cached_in=0,
                  reasoning=0):
        """Record one model call's usage, for the in-turn spend cap."""
        self.tokens_in += tokens_in or 0
        self.tokens_out += tokens_out or 0
        self.tokens_cached_in += cached_in or 0
        slot = self.model_usage.setdefault(
            (model or "").strip().lower(),
            {"in": 0, "out": 0, "cached": 0, "reasoning": 0})
        slot["in"] += tokens_in or 0
        slot["out"] += tokens_out or 0
        slot["cached"] += cached_in or 0
        slot["reasoning"] += reasoning or 0

    def running_credits(self):
        """Model cost spent so far this turn, in credits, using the same
        per-model prices AND the same burn rate (model_prices.USD_PER_CREDIT)
        as db.charge_turn_credits — so the in-turn cap and the final charge
        agree. Two different divisors here would mean the cap stops a turn at a
        number the invoice never shows.

        Falls back to the flat totals when nothing has been recorded per model —
        that path only runs for callers that poke the counters directly."""
        cost = 0.0
        if self.model_usage:
            for model, u in self.model_usage.items():
                p = model_prices.price_for(model, config.PRICE_FALLBACK)
                cached = min(max(u["cached"], 0), u["in"])
                out = u["out"] + (u["reasoning"]
                                  if p.get("reasoning_separate") else 0)
                cost += ((u["in"] - cached) * p["in"]
                         + cached * p["cached_in"]
                         + out * p["out"]) / 1e6
        else:
            cached_in = min(max(self.tokens_cached_in, 0), self.tokens_in)
            cost = ((self.tokens_in - cached_in) * config.LLM_PRICE_IN_PER_M +
                    cached_in * config.LLM_PRICE_CACHED_IN_PER_M +
                    self.tokens_out * config.LLM_PRICE_OUT_PER_M) / 1e6
        cost += len(self.images_generated) * config.IMAGE_PRICE_USD
        cost += self.gen_extra_cost_usd     # generated sfx + video (real $)
        return model_prices.usd_to_credits(cost)

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
        if not self._states_seen:
            # Seed with the state the turn STARTED in, or undoing every edit
            # back to the beginning would read as progress.
            self._states_seen[edl_signature(prev["json"])] = prev["version"]
        try:
            normalized = validate_edl(new_edl_dict, self.duration).model_dump()
        except EDLValidationError as e:
            msg = f"REJECTED (EDL v{prev['version']} unchanged): {e}"
            # Is the CURRENT saved state itself invalid? Then no edit built on
            # it can ever save, and telling the agent to "fix the span" sends
            # it round a loop it cannot exit — which is what happened to a real
            # customer on 2026-07-25. Name the escape hatch instead.
            try:
                validate_edl(prev["json"], self.duration)
            except EDLValidationError:
                msg += ("\nThe SAVED edit is itself invalid against this "
                        f"source ({self.duration}s), so every write will be "
                        "rejected no matter what you change — most likely the "
                        "video was replaced with a different one. Tell the "
                        "user the old edit no longer fits this footage and "
                        "call reset_edit to start from the full video, then "
                        "rebuild what they asked for.")
            return msg
        if edl_signature(normalized) == edl_signature(prev["json"]):
            return (f"NO CHANGE — the EDL is identical to v{prev['version']}; "
                    "the requested change may need a different tool or may "
                    "not be supported. Do NOT tell the user you changed "
                    "anything.")
        sig = edl_signature(normalized)
        version = self.db.run(dbx.insert_edl, self.project_id, normalized,
                              "agent")
        self.versions_written.append(version)
        before = describe_edl(prev["json"])
        after = describe_edl(normalized, self.duration)
        line = (f"EDL v{prev['version']} -> v{version}: {change_desc}. "
                f"Before: {before}. After: {after}.")

        # Cycle detection. A turn that removes what it just added and adds it
        # back has made no progress, and left alone it will keep going: one
        # real session spent ~150 versions and most of a dollar re-placing
        # three title cards, because the thing it was reacting to came back
        # every lap.
        #
        # Reported, never blocked. A hard cap would break the legitimate case
        # (returning to an earlier state on the way somewhere new), and the
        # agent cannot see its own repetition — the diff of each individual
        # step looks like progress. The missing information is that this exact
        # state already existed, so that is what gets handed back.
        seen_at = self._states_seen.get(sig)
        if seen_at is not None:
            line += (
                f"\nLOOP DETECTED: this is the same edit as v{seen_at}, which "
                "you already produced this turn — everything since then has "
                "cancelled out. Do NOT try that sequence again; it will land "
                "here a third time. Something you believe about the edit is "
                "wrong, so verify before editing further: read get_edl for "
                "the real item positions, and if you are reacting to a render "
                "(a black frame, missing text), call look_at on that exact "
                "time to see what is actually there. If you still cannot make "
                "it work, stop and tell the user plainly what you tried and "
                "what you observed — an honest dead end costs them far less "
                "than another lap.")
        else:
            self._states_seen[sig] = version
        return line


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
    gaps, basis = _dead_air(ctx, 0.7)
    total_gap = sum(g["end"] - g["start"] for g in gaps)
    quiet = [s for s in ctx.index.get("silences", []) if s[1] - s[0] >= 0.7]
    edl = ctx.latest_edl()
    # Both numbers, always, and labelled — they diverge exactly when it
    # matters (a bed of game/music audio keeps the waveform above the noise
    # floor while nobody is talking), and reporting only the waveform one is
    # how a user with an obviously pausey gameplay clip got told there was
    # nothing to cut.
    if basis == "waveform":
        gap_txt = (f"no speech transcribed, so there are no talking pauses; "
                   f"{len(quiet)} quiet span(s) >=0.7s by waveform")
    else:
        gap_txt = (f"{len(gaps)} pause(s) in the talking >=0.7s totalling "
                   f"{total_gap:.1f}s (use these for 'cut the silences'); "
                   f"{len(quiet)} of the video's quiet-waveform spans")
    return (f"duration={v['duration']}s, {v['width']}x{v['height']} @ "
            f"{v['fps']}fps, audio={'yes' if v['has_audio'] else 'NO'}. "
            f"{len(ctx.index.get('shots', []))} shots, "
            f"{len(ctx.index.get('sentences', []))} sentences / "
            f"{len(ctx.index.get('words', []))} words, "
            f"{gap_txt}. "
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
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
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


# Adaptive response bound (was a 60s hard range reject): a wide range over
# sparse speech is perfectly answerable, so the WORD count is what's capped —
# the tail says exactly how much was withheld and how to get it.
GET_WORDS_MAX_WORDS = 400


def get_words(ctx, start=0, end=None):
    """Word-level timestamps straight from the index — the ONLY correct
    source for cut points inside a sentence."""
    start = ctx.clamp(start or 0)
    end = ctx.clamp(end if end is not None else ctx.duration)
    if end <= start:
        return "REJECTED: end must be greater than start."
    words = ctx.index.get("words", [])
    rows = [w for w in words if w["t1"] > start and w["t0"] < end]
    if not rows:
        return (f"No transcribed words between {start}s and {end}s."
                if words else
                "This video has no transcript (no speech or no audio track).")
    shown = rows[:GET_WORDS_MAX_WORDS]
    out = [f"{_fmt_t(w['t0'])}-{_fmt_t(w['t1'])} {w['w']}" for w in shown]
    tail = ""
    if len(rows) > len(shown):
        # Floor the suggested start (int()): rounding UP could skip words
        # whose t1 falls inside the rounded gap; flooring only re-shows a
        # ≤1s overlap the strict t1>start filter tolerates. The end is
        # printed exactly for the same reason.
        tail = (f"\n...{len(rows) - len(shown)} more words (up to {end}s) "
                f"not shown — narrow the range and call again "
                f"(e.g. get_words({int(shown[-1]['t1'])}, {end:g})).")
    return _cap("\n".join(out) + tail)


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
        if cap.get("subtitles"):
            desc += "; [burned-in captions visible]"
        lines.append(f"[#{s['id']} {_fmt_t(s['start'])}-{_fmt_t(s['end'])}] "
                     f"{desc or '(no visual caption)'}")
    return _cap("\n".join(lines))


def _dead_air(ctx, min_s):
    """Spans worth cutting when the user says "cut the silences", and which
    signal they came from: (gaps, basis).

    basis 'speech'   — gaps BETWEEN spoken words (audit.speech_gaps). The
                       right answer whenever the video has speech, because
                       "silence" to a user means nobody talking. Waveform
                       quiet alone misses all of it under a music/game bed.
    basis 'waveform' — no words were transcribed at all, so there is no
                       speech to find gaps between. A slideshow or a clip
                       with occasional sound still has real quiet spans, and
                       they are the only signal available, so fall back to
                       them rather than refusing a video that genuinely has
                       silences to cut.
    """
    words = ctx.index.get("words", [])
    quiet = [list(s) for s in ctx.index.get("silences", [])]
    if words:
        dur = (ctx.index.get("video") or {}).get("duration") or ctx.duration
        return audit.speech_gaps(words, dur, min_s=min_s,
                                 silences=quiet), "speech"
    return ([{"start": s, "end": e, "quiet_frac": 1.0} for s, e in quiet
             if e - s >= min_s], "waveform")


def find_silences(ctx, min_seconds=0.7):
    try:
        min_s = max(0.1, float(min_seconds))
    except (TypeError, ValueError):
        return "REJECTED: min_seconds must be a number."
    words = ctx.index.get("words", [])
    gaps, basis = _dead_air(ctx, min_s)
    if not gaps:
        if basis == "waveform":
            return ("No speech was transcribed AND the audio never drops "
                    "below the noise floor — there is nothing this tool can "
                    "call a silence. Say so plainly; do not guess at pauses "
                    "from the picture.")
        return f"No gaps in the speech of {min_s}s or longer."
    lines = []
    for g in gaps[:100]:
        s, e = g["start"], g["end"]
        before = next((w["w"] for w in reversed(words) if w["t1"] <= s + 0.05),
                      None)
        after = next((w["w"] for w in words if w["t0"] >= e - 0.05), None)
        ctxt = ""
        if before or after:
            ctxt = f" — after '{before or '(start)'}', before '{after or '(end)'}'"
        # An unquiet gap is nobody-talking-over-sound: cuttable, but the user
        # loses that sound, so it is never presented as though it were quiet.
        q = g["quiet_frac"]
        sound = "" if q >= 0.8 else (
            f" [NOT quiet — {int((1 - q) * 100)}% of this gap has audio "
            "(music/game/room); cutting it removes that sound too]")
        lines.append(f"{_fmt_t(s)}-{_fmt_t(e)} ({e - s:.2f}s, midpoint "
                     f"{_fmt_t((s + e) / 2)}){ctxt}{sound}")
    note = f"\n({len(gaps) - 100} more not shown)" if len(gaps) > 100 else ""
    head = (f"{len(gaps)} gap(s) in the speech >= {min_s}s (spans where "
            "nobody is talking — this is what 'silence' means to the user, "
            "not just a quiet waveform)") if basis == "speech" else \
        (f"No speech was transcribed in this video, so these are the "
         f"{len(gaps)} quiet-WAVEFORM span(s) >= {min_s}s")
    return _cap(head + ":\n" + "\n".join(lines) + note)


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
    out = "\n".join(lines)
    # The commonest way a user delivers a SONG is as the video they found it
    # in — a TikTok/Reel download. Say here that this works, at the moment the
    # agent is looking at the clip, rather than leaving it to guess.
    if any(a["kind"] == "video_clip" for a in rows):
        out += ("\nAny [video_clip] can also be used as SOUND ONLY — pass its "
                "storage_key straight to add_music / add_sfx / add_voiceover "
                "(or call extract_audio first). Its picture stays out of the "
                "edit entirely.")
    return _cap(out)


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
        proxy = None
        proxy_err = str(err)
    else:
        proxy_err = None
    # 6 frames over a >30s range (was 4 max): 4 samples across half a minute
    # skip whole shots; the marginal vision cost is small next to a wrong cut.
    n = 6 if e - s > 30 else (4 if e - s > 1.5 else 2)
    times = [s + (e - s) * (i + 0.5) / n for i in range(n)]

    def _sample(path, label, tag):
        """Pull `times` out of one file. Returns (frames, names, last_error)."""
        got, names, err = [], [], None
        for i, t in enumerate(times):
            fp = os.path.join(ctx.workdir, f"look_{tag}_{i}_{int(t * 100)}.jpg")
            try:
                media.frame_at(path, t, fp)
            except media.MediaError as ex:
                err = str(ex)
                continue
            got.append(fp)
            names.append(f"{label} @{t:.2f}s")
        return got, names, err

    frames, frame_names, last_err = ([], [], proxy_err)
    if proxy:
        frames, frame_names, last_err = _sample(proxy, "proxy frame", "p")
    # The proxy is a convenience, not the only copy of the footage. When it
    # yields nothing, fall back to the ORIGINAL rather than blinding the agent:
    # a whole paid turn was burned on 2026-07-25 doing 20 look_at calls that all
    # came back "Could not extract frames", while render_preview on the same
    # project worked perfectly — proving the pixels were reachable all along.
    if not frames:
        try:
            src = _original_local(ctx)
        except Exception as ex:
            last_err = last_err or str(ex)
        else:
            frames, frame_names, err2 = _sample(src, "source frame", "o")
            last_err = err2 or last_err
    if not frames:
        # Never a bare "could not" again — the reason is the whole diagnosis.
        return ("Could not extract frames for that range from either the "
                f"proxy or the original ({(last_err or 'unknown error')[:220]})."
                " The footage itself is fine for cutting and rendering — work "
                "from get_shots, the transcript and get_video_info instead, "
                "and say you could not LOOK at it rather than that the video "
                "is broken.")
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
    frames, frame_names, last_err = [], [], None
    for i in range(n):
        t = s + (e - s) * (i + 0.5) / n
        fp = os.path.join(ctx.workdir,
                          f"alook_{asset['id']}_{i}_{int(t * 10)}.jpg")
        try:
            media.frame_at(local, t, fp, width=640)
            frames.append(fp)
            frame_names.append(f"clip '{name}' frame @{t:.2f}s")
        except media.MediaError as ex:
            last_err = str(ex)
    if not frames:
        return ("Could not extract frames from that clip "
                f"({(last_err or 'unknown error')[:220]}). The clip can still "
                "be inserted — you just cannot see inside it; ask the user "
                "which part to use instead of guessing.")
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


# The shared re-anchoring pass lives in timeline.py (round 35.1) so the
# backend UI ops apply the IDENTICAL anchor policy — one implementation,
# every surface. Alias kept for the existing call sites below.
_remap_program_items = timeline_mod.remap_program_items


def _write_keep(ctx, new_keep, desc, snap_to_words=False,
                check_regression=False):
    """Shared tail for every keep-modifying write: optional outward word
    snapping, insert re-snap + program-item re-anchoring (the shared remap
    above, speed-aware), the version write, then mid-word boundary warnings
    (and, for full replacements, mechanical regression warnings) appended to
    a still SUCCESSFUL result."""
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
    speed = edl.get("speed") or []
    # Inserts sit at keep boundaries; when the keep list changes, re-snap
    # each to the nearest boundary of the NEW keep so the edit stays valid.
    # Boundaries are speed-aware — a sped segment occupies its remapped length.
    if edl.get("inserts"):
        bounds = keep_boundaries(new_keep, speed)
        edl["inserts"] = [
            {**ins, "at_output_s": min(bounds,
                                       key=lambda b: abs(b - ins["at_output_s"]))}
            for ins in edl["inserts"]]
    # Program-time items re-anchor through the shared remap; both Timelines
    # carry the (unchanged-by-this-write) speed list so their clocks agree
    # with what actually renders.
    old_tl = Timeline(prev_keep, prev["json"].get("inserts") or [],
                      prev["json"].get("speed") or [])
    new_tl = Timeline(new_keep, edl.get("inserts") or [], speed)
    region_notes = _remap_program_items(edl, old_tl, new_tl)

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
    # Gaps in the SPEECH, not dips in the waveform. A gameplay clip, a video
    # over a music bed or anything recorded in a noisy room never drops below
    # the noise floor, so the waveform test found nothing and this tool used
    # to answer "already tight" while the user watched a minute of nobody
    # talking. See audit.speech_gaps.
    gaps, basis = _dead_air(ctx, min_s)
    if not gaps:
        if basis == "waveform":
            return ("No speech was transcribed in this video AND the audio "
                    "never drops below the noise floor, so there is nothing "
                    "to cut by silence. Tell the user that plainly and ask "
                    "which parts to keep, or cut by the picture with "
                    "find_shots / look_at.")
        return (f"No gaps in the speech of {min_s}s or longer — the talking "
                "is already tight, so nothing was cut.")
    cuts, noisy = [], 0
    for g in gaps:
        cs, ce = round(g["start"] + pad, 2), round(g["end"] - pad, 2)
        if ce - cs >= 0.1:
            cuts.append([cs, ce])
            if g["quiet_frac"] < 0.8:
                noisy += 1
    if not cuts:
        return (f"Found {len(gaps)} speech gap(s), but each is too short to "
                f"trim once {pad}s of padding is kept around speech. Nothing "
                "was cut (lower padding_s to trim more aggressively).")
    cur = ctx.latest_edl()["json"]["keep"]
    new = [list(x) for x in audit.subtract_spans(cur, cuts)]
    if not new:
        return ("REJECTED: cutting every speech gap would remove the whole "
                "video. Inspect find_silences and cut a narrower set.")
    removed = output_duration(cur) - output_duration(new)
    what = "gap(s) in the speech" if basis == "speech" else "quiet span(s)"
    res = _write_keep(
        ctx, new,
        f"cut {len(cuts)} {what} >= {min_s}s ({removed:.1f}s "
        f"removed, {pad}s kept around speech)",
        snap_to_words=True)
    if noisy and res.startswith("EDL v"):
        # Never let this land as "I removed the silences" when the user is
        # about to notice their game audio / music jumping at those cuts.
        res += (f"\nHONESTY: {noisy} of those {len(cuts)} gaps were NOT "
                "quiet — nobody was talking, but there was audio there "
                "(music, game or room sound). That audio is now gone at "
                "those points. Tell the user this rather than calling them "
                "silences, and offer to restore any they want back.")
    return res


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
                and mw > KARAOKE_TOOL_MAX:
            karaoke_note = (f"\nNote: dynamic (karaoke) captions group at "
                            f"most {KARAOKE_TOOL_MAX} words per line — "
                            f"using {KARAOKE_TOOL_MAX} instead of {mw}.")
            mw = KARAOKE_TOOL_MAX
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
        edl["captions"] = _bake_karaoke_group(cfg)
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


def _bake_karaoke_group(caps):
    """Bake the karaoke group size for legacy-dynamic captions (mutates and
    returns caps). The renderer's dynamic grouping stays hard-clamped at 4
    for EDLs carrying no explicit group — the render-time meaning of
    EXISTING fields never changes — so sizes above 4 ride karaoke_group_n,
    written concretely here at write time."""
    if not isinstance(caps, dict):
        return caps
    st = caps.get("style") or {}
    preset = st.get("preset")
    legacy_dynamic = bool(st.get("dynamic")) and \
        (not preset or preset == "classic")
    mw = caps.get("max_words_per_caption")
    if legacy_dynamic and mw:
        caps["karaoke_group_n"] = min(int(mw), KARAOKE_TOOL_MAX)
    else:
        caps.pop("karaoke_group_n", None)
    return caps


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
    # any patch that LANDS in legacy-dynamic with a stored group size above
    # the karaoke max (turning dynamic on, OR switching a premium preset
    # back to classic while dynamic was already on): clamp the stored value
    # so state and output agree, and say so.
    karaoke_note = ""
    eff_dynamic = isinstance(merged, dict) and not eff_preset \
        and bool((merged.get("style") or {}).get("dynamic"))
    if eff_dynamic \
            and (merged.get("max_words_per_caption") or 0) > KARAOKE_TOOL_MAX:
        karaoke_note = (f"\nNote: dynamic (karaoke) captions group at most "
                        f"{KARAOKE_TOOL_MAX} words per line — "
                        f"max_words_per_caption lowered from "
                        f"{merged['max_words_per_caption']} to "
                        f"{KARAOKE_TOOL_MAX}.")
        merged["max_words_per_caption"] = KARAOKE_TOOL_MAX
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
    edl["captions"] = _bake_karaoke_group(merged)
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


def _asset_name(asset):
    return ((asset.get("meta") or {}).get("filename")
            or os.path.basename(asset.get("storage_key") or "?"))


def _audio_from_clip(ctx, asset):
    """(music_asset, note, error) — an uploaded VIDEO's soundtrack as a
    standalone audio asset the audio tools can use. Its picture is never
    touched, which IS the feature: "use the song off this video, not its
    scene".

    Why this exists. A user who wants a song has the song as a video, because
    that is the only file TikTok/Instagram/YouTube ever hands them. Before
    round 47 every audio tool rejected a clip's key outright ("not a music
    asset here") and the agent, believing the product could not do it, told
    the user to go and convert the file themselves. On Jul 26 2026 one did:
    they attached the same video FOUR times, were told no every time, and
    left. Nothing was missing but this — the renderer has always been able to
    read audio out of an mp4.

    The extraction is cached as a real project asset (kind 'music', with the
    source recorded in meta), so a clip resolved by add_music and then by
    get_audio_analysis in the same turn costs one ffmpeg run, and a re-upload
    of the same bytes costs none.
    """
    name = _asset_name(asset)
    cached = ctx.db.run(dbx.extracted_audio_asset, ctx.project_id,
                        asset["storage_key"], asset.get("sha256"))
    if cached:
        return cached, _sound_only_note(name, cached), None
    try:
        local = _asset_local_path(ctx, asset)
    except Exception as e:
        return None, None, (
            f"Could not read '{name}' to take its audio ({str(e)[:140]}). "
            "Do NOT claim the sound was added.")
    out = os.path.join(ctx.workdir, f"clipaudio_{asset['id']}.m4a")
    try:
        dur = media.extract_audio_track(local, out)
    except media.MediaError as e:
        if "no audio stream" in str(e):
            return None, None, (
                f"REJECTED: '{name}' has no sound in it at all — it is a "
                "silent video, so there is no audio to take from it. Tell "
                "the user that plainly and ask for the song itself, or offer "
                "a built-in track (list_music_library).")
        return None, None, (
            f"Could not take the audio out of '{name}' ({str(e)[:140]}). Do "
            "NOT claim the sound was added.")
    key = f"music/{ctx.project_id}/{uuid.uuid4().hex[:12]}.m4a"
    try:
        storage.upload_file(out, key, "audio/mp4")
    except Exception as e:
        return None, None, (
            f"Took the audio out of '{name}' but could not save it "
            f"({str(e)[:140]}). Do NOT claim the sound was added; try again.")
    stem = os.path.splitext(name)[0][:60]
    fname = f"{stem} (audio).m4a"
    row = {"id": ctx.db.run(dbx.insert_asset, ctx.project_id, "music", key,
                            duration_s=dur,
                            meta={"filename": fname,
                                  "from_asset_key": asset["storage_key"],
                                  "from_sha256": asset.get("sha256"),
                                  "extracted_from_video": True,
                                  "caption": f"sound only, taken from the "
                                             f"uploaded video '{name}'"}),
           "kind": "music", "storage_key": key, "duration_s": dur,
           "meta": {"filename": fname}}
    ctx.audio_extracted.append({"storage_key": key, "from": name})
    return row, _sound_only_note(name, row), None


def _sound_only_note(source_name, audio_asset):
    """What the agent must tell the user about a clip used as sound.

    Emitted on the cached path too: the claim being guarded is "the video is
    in your edit", and that is just as wrong on the second turn as the first.
    """
    dur = audio_asset.get("duration_s") or 0.0
    return (f"Note: '{source_name}' is a VIDEO, so its audio ({dur:.0f}s) is "
            f"what plays — lifted out as a sound-only file "
            f"({_asset_name(audio_asset)}, "
            f"storage_key={audio_asset['storage_key']}). Its picture appears "
            "NOWHERE in the edit; say that to the user rather than implying "
            "the clip itself was added.")


def extract_audio(ctx, asset_key):
    """Take ONLY the sound out of an uploaded video, as a file the audio tools
    can use. The video's picture is not shown anywhere."""
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, asset_key)
    if asset and asset["kind"] in ("original", "proxy"):
        return ("REJECTED: that is the MAIN video — its own sound is already "
                "in the edit. Use set_volume to raise or lower it; extracting "
                "it would only layer the same audio over itself.")
    if asset and asset["kind"] == "audio":
        return ("REJECTED: that file is already the main video's extracted "
                "audio (a transcription artifact) — it is not user content "
                "and must not be mixed back in.")
    if asset and asset["kind"] == "music":
        return (f"NO CHANGE — '{_asset_name(asset)}' is already an audio "
                f"file; pass storage_key={asset_key} straight to add_music, "
                "add_sfx or add_voiceover.")
    if not asset or asset["kind"] != "video_clip":
        avail = ctx.db.run(dbx.assets_by_kinds, ctx.project_id,
                           ["video_clip"])
        hint = ("Uploaded videos: " + "; ".join(a["storage_key"]
                                                for a in avail[:12])
                if avail else "No video has been uploaded to this project "
                              "besides the main one.")
        return (f"REJECTED: '{asset_key}' is not an uploaded video. {hint}")
    got, note, err = _audio_from_clip(ctx, asset)
    if err:
        return err
    dur = got.get("duration_s") or 0.0
    return (f"Audio taken from '{_asset_name(asset)}' — storage_key="
            f"{got['storage_key']} ({dur:.0f}s). Nothing is in the edit yet: "
            "pass that key to add_music (a song under the video), add_sfx (a "
            "one-shot moment) or add_voiceover (someone talking). The "
            "video's picture is NOT used — only its sound."
            + (f"\n{note}" if note else ""))


def _resolve_music(ctx, storage_key):
    """(track, error) for a music reference. track['storage_key'] is the key
    the EDL must store — the caller must use it rather than what it was
    handed, because a VIDEO resolves to the audio extracted from it.

    Three doors. A `library:` reference is looked up in the bundled CC0
    catalog by EXACT membership and never touches the assets table; an
    uploaded VIDEO resolves through _audio_from_clip; anything else falls
    through to the project-asset guard below, which is unchanged — including
    the check that catches the pipeline's own extracted speech track, the
    cause of the original inaudible-music bug."""
    if music_library.is_library_ref(storage_key):
        t = music_library.resolve(storage_key)
        if not t:
            have = ", ".join(x["slug"] for x in music_library.CATALOG[:10])
            return None, (
                f"REJECTED: '{storage_key}' is not a track in the built-in "
                f"library. Call list_music_library() and use a slug it "
                f"returns — never invent one. Known slugs: {have or 'none'}.")
        return {"name": t["title"], "duration_s": t.get("duration_s"),
                "library": True, "storage_key": storage_key}, None

    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio "
            "track (a transcription artifact), not background music — "
            "mixing it in would only double the speaker's voice under "
            "itself, near-inaudibly. Use a real music file instead: "
            "list_music_library() for a built-in track, or "
            "list_assets(kind='music') for the user's own uploads.")
    if asset and asset["kind"] == "video_clip":
        got, note, err = _audio_from_clip(ctx, asset)
        if err:
            return None, err
        return {"name": _asset_name(got), "duration_s": got.get("duration_s"),
                "library": False, "storage_key": got["storage_key"],
                "note": note}, None
    if not asset or asset["kind"] != "music":
        avail = ctx.db.run(
            lambda conn: _music_assets(conn, ctx.project_id))
        hint = ("Available music storage_keys: " +
                "; ".join(a["storage_key"] for a in avail)
                if avail else "No music uploaded to this project — call "
                              "list_music_library() for built-in tracks.")
        return None, f"REJECTED: '{storage_key}' is not a music asset here. {hint}"
    return {"name": os.path.basename(storage_key),
            "duration_s": asset.get("duration_s"), "library": False,
            "storage_key": storage_key}, None


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


def _speech_overlap_s(ctx, edl, start_out, end_out):
    """Seconds of SURVIVING speech inside an OUTPUT window — the fact that
    decides whether music is a bed under a voice or the lead audio.
    Sentences are source-time; span_to_out maps what the current cut keeps."""
    sents = ctx.index.get("sentences") or []
    if not sents:
        return 0.0
    try:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
    except Exception:
        return 0.0
    total = 0.0
    for sn in sents:
        for a, b in tl.span_to_out(sn["t0"], sn["t1"]):
            total += max(0.0, min(b, end_out) - max(a, start_out))
    return total


def add_music(ctx, storage_key, start=None, end=None, gain_db=None,
              duck=None, offset_s=None, fade_in_s=None, fade_out_s=None,
              loop=True):
    track, err = _resolve_music(ctx, storage_key)
    if err:
        return err
    # What the EDL stores is the RESOLVED key: hand this tool a video and the
    # sound that plays is the audio extracted from it, never the video object.
    storage_key = track["storage_key"]
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
    # Context-aware defaults (round 36). Music under a VOICE sits low and
    # ducks; music with no speech under it IS the audio and must be heard.
    # The -18dB/duck=True pair, applied unconditionally, made every track on
    # a speech-less video near-inaudible — and the sidechain duck then dipped
    # it further on every ambient sound. Only the DEFAULTS are contextual;
    # explicit arguments always win (they just earn an advisory note).
    speech_s = _speech_overlap_s(ctx, edl, s, e)
    lead = speech_s < 1.0
    if gain_db is None:
        g = -4.0 if lead else -18.0
    else:
        try:
            g = float(gain_db)
        except (TypeError, ValueError):
            return "REJECTED: gain_db must be a number."
    if duck is None:
        duck = not lead
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
    # NEW music ducks smoothly (sidechain — the bed dips WITH the voice and
    # swells back in the gaps) instead of the legacy -12dB step. Written on
    # the item, never inferred at render, and never applied to existing music
    # items — their duck_mode stays whatever it was.
    item = {"id": _next_item_id(music, "mus"), "storage_key": storage_key,
            "start": s, "end": e, "gain_db": g, "duck": bool(duck),
            "duck_mode": "smooth" if duck else None,
            "offset_s": off, "fade_in_s": fi or None,
            "fade_out_s": fo or None, "loop": True if loop else None}
    music.append(item)
    edl["music"] = music
    res = ctx.write_edl(
        edl, f"music '{track['name']}' at {s}-{e}s "
             f"(output timeline), {g}dB, duck={bool(duck)} [{item['id']}]")
    if duck and res.startswith("EDL v"):
        res += ("\nNote: music ducks smoothly under speech (a sidechain dip "
                "that follows the voice, not a hard step) — "
                "set_music_fit(duck_mode='step') switches to the legacy duck.")
    if res.startswith("EDL v"):
        if lead and gain_db is None:
            res += ("\nNote: no speech survives under this window, so the "
                    "music was added as the LEAD audio — gain -4dB, no "
                    "ducking. If it competes with the original sound, lower "
                    "the original with set_volume rather than burying the "
                    "music.")
        elif lead and g <= -10.0:
            res += (f"\nNote: no speech survives under this window, and at "
                    f"{g:g}dB the music will sit far below the original "
                    "audio — lead music usually plays at -6..0dB. Raise it "
                    "with set_audio_gain if the user cannot hear it.")
        elif lead and duck:
            res += ("\nNote: there is no speech to duck under in this "
                    "window — the sidechain will dip the music on every "
                    "loud ORIGINAL sound instead (waves, crowd, noise). "
                    "set_music_fit(duck=false) if that is not wanted.")

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
    if track.get("note") and not str(res).startswith("REJECTED"):
        res += "\n" + track["note"]
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
             f"('{_music_name(ctx, hit['storage_key'])}', "
             f"{hit['start']}-{hit['end']}s)")


def _upload_name(ctx, key):
    """The user's OWN filename for an uploaded audio asset.

    Storage keys are random hex, so the basename fallback reports
    '7f3a91b2c4d5.m4a' back at someone who attached 'my song.mp3' — and now
    that audio can be lifted out of a video, the name is the only thing
    telling the agent (and the user) WHICH file it is talking about."""
    try:
        a = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
    except Exception:
        a = None
    return (((a or {}).get("meta") or {}).get("filename")
            or os.path.basename(key or "?"))


def _music_name(ctx, key):
    """Display name for a music reference. Library refs aren't paths, so
    basename() would print the raw 'library:slug' at the user."""
    t = music_library.resolve(key)
    if t:
        return t["title"]
    return _upload_name(ctx, key)


def _track_name(ctx, key):
    """Display name for ANY audio reference — music, sfx or upload. Both
    bundled schemes resolve to a real title; everything else is an upload."""
    for lib in (music_library, sfx_library):
        t = lib.resolve(key)
        if t:
            return t["title"]
    return _upload_name(ctx, key)


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
    An uploaded VIDEO is the third door (round 47): it resolves to the audio
    extracted from it, because "use the sound off this clip" is a thing users
    ask for and the picture is simply never used.
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
                "library": True, "storage_key": storage_key}, None

    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio track "
            "(a transcription artifact), not a sound effect. Use "
            "list_sfx_library() for a built-in sound, or "
            "list_assets(kind='music') for the user's own uploads.")
    if asset and asset["kind"] == "video_clip":
        got, note, err = _audio_from_clip(ctx, asset)
        if err:
            return None, err
        return {"name": _asset_name(got), "duration_s": got.get("duration_s"),
                "library": False, "storage_key": got["storage_key"],
                "note": note}, None
    if not asset or asset["kind"] != "music":
        return None, (
            f"REJECTED: '{storage_key}' is not an audio asset in this "
            "project. Call list_sfx_library() for the built-in pack, or "
            "list_assets(kind='music') for the user's uploads.")
    return {"name": os.path.basename(storage_key),
            "duration_s": asset.get("duration_s"), "library": False,
            "storage_key": storage_key}, None


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
    storage_key = sound["storage_key"]      # a video resolves to its audio
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
    if sound.get("note"):
        note += "\n" + sound["note"]
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
        edl, f"removed sfx {id} ('{_track_name(ctx, hit['storage_key'])}' "
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
        edl, f"moved sfx {id} ('{_track_name(ctx, hit['storage_key'])}') "
             f"{old}s -> {hit['at']}s")


def swap_music(ctx, id, storage_key):
    """Change WHICH track plays, keeping its position, level and fit —
    'no, use a different song'."""
    track, err = _resolve_music(ctx, storage_key)
    if err:
        return err
    storage_key = track["storage_key"]      # a video resolves to its audio
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
    old = _music_name(ctx, hit.get("storage_key"))
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
    if track.get("note") and not str(res).startswith("REJECTED"):
        res += "\n" + track["note"]
    return res


def set_music_fit(ctx, id, start=None, end=None, offset_s=None,
                  fade_in_s=None, fade_out_s=None, loop=None, duck=None,
                  duck_mode=None):
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
    duck_note = ""
    if duck_mode is not None:
        dm = str(duck_mode).strip().lower()
        if dm not in ("smooth", "step"):
            return ("REJECTED: duck_mode must be 'smooth' (a sidechain dip "
                    "that follows the voice) or 'step' (the legacy hard "
                    "-12dB duck).")
        # 'step' is the absence of the smooth mode, stored as None so the
        # item hashes exactly like every pre-smooth EDL.
        hit["duck_mode"] = "smooth" if dm == "smooth" else None
        if not hit.get("duck"):
            duck_note = ("\nNote: this item has duck=false, so duck_mode "
                         "has no audible effect until ducking is turned on.")
    if hit == before:
        return (f"NO CHANGE — music {id} already has those settings. Do NOT "
                "tell the user you changed anything.")
    edl["music"] = items
    changed = ", ".join(
        f"{k}={hit.get(k)}" for k in
        ("start", "end", "offset_s", "fade_in_s", "fade_out_s", "loop",
         "duck", "duck_mode")
        if hit.get(k) != before.get(k))
    res = ctx.write_edl(
        edl, f"music {id} ('{_music_name(ctx, hit['storage_key'])}') refit: "
             f"{changed}")
    if duck_note and res.startswith("EDL v"):
        res += duck_note
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
        edl, f"{kind} {id} ('{_track_name(ctx, key)}') gain "
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


def set_frame(ctx, ratio, mode="crop", focus_x=None, focus_y=None):
    payload = {"ratio": str(ratio), "mode": str(mode or "crop")}
    for k, v in (("focus_x", focus_x), ("focus_y", focus_y)):
        if v is not None:
            try:
                payload[k] = float(v)
            except (TypeError, ValueError):
                return ("REJECTED: focus_x/focus_y must be numbers 0-1 — "
                        "fractions of the SOURCE frame where the subject "
                        "sits ((0,0) = top-left). Use look_at or "
                        "auto_reframe to find the subject.")
    try:
        frame = Frame.model_validate(payload)
    except Exception:
        return ('REJECTED: ratio must be one of source, 16:9, 9:16, 1:1, 4:5 '
                'and mode one of crop, pad, pad_blur. Example: '
                'set_frame("9:16", "crop") for TikTok.')
    edl = dict(ctx.latest_edl()["json"])
    if frame.ratio == "source":
        edl["frame"] = None
        return ctx.write_edl(edl, "output frame back to the source ratio")
    edl["frame"] = frame.model_dump()
    aimed = ""
    if frame.mode == "crop" and (frame.focus_x is not None or
                                 frame.focus_y is not None):
        aimed = (f", crop centered on ({frame.focus_x if frame.focus_x is not None else 0.5:g}, "
                 f"{frame.focus_y if frame.focus_y is not None else 0.5:g}) "
                 "of the source frame")
    res = ctx.write_edl(
        edl, f"output frame set to {frame.ratio} ({frame.mode}){aimed}")
    if (res.startswith("EDL v") and frame.mode == "crop"
            and frame.focus_x is None and frame.focus_y is None
            and frame.ratio in ("9:16", "1:1", "4:5")
            and ctx.has_main_video):
        res += ("\nNote: this is a CENTER crop — if the subject is not "
                "dead-center it will sit off-frame or be cut. Call "
                "auto_reframe to aim the crop at the subject, or pass "
                "focus_x/focus_y yourself (fractions of the source frame, "
                "from look_at).")
    return res


def auto_reframe(ctx, ratio="9:16", mode="crop"):
    """Convert the output frame AND aim the crop at the real subject: sample
    frames across the kept footage, ask the vision model where the subject
    sits, write set_frame with that focus. The honest fix for '9:16 just
    cut the middle of the screen'."""
    if str(ratio) == "source":
        return set_frame(ctx, "source")
    if str(mode or "crop") != "crop":
        # pad modes never discard picture, so there is nothing to aim.
        return set_frame(ctx, ratio, mode)
    if not ctx.has_main_video:
        return set_frame(ctx, ratio, "crop")
    if not llm.vision_available():
        res = set_frame(ctx, ratio, "crop")
        if res.startswith("EDL v"):
            res += ("\nNote: no vision model is configured, so the crop is "
                    "the plain CENTER crop — auto framing needs vision.")
        return res
    try:
        proxy = ctx.proxy_path()
    except Exception as err:
        res = set_frame(ctx, ratio, "crop")
        if res.startswith("EDL v"):
            res += (f"\nNote: could not fetch frames ({err}), so the crop "
                    "is the plain CENTER crop.")
        return res
    edl = dict(ctx.latest_edl()["json"])
    keep = edl.get("keep") or [[0.0, ctx.duration]]
    kept_total = sum(e - s for s, e in keep) or ctx.duration
    # 5 samples spread over the KEPT footage only — framing follows what the
    # viewer will actually see, not the cut material.
    frames, names = [], []
    for i in range(5):
        target = kept_total * (i + 0.5) / 5
        acc = 0.0
        t = keep[-1][1] - 0.01
        for s, e in keep:
            if acc + (e - s) >= target:
                t = s + (target - acc)
                break
            acc += e - s
        fp = os.path.join(ctx.workdir, f"reframe_{i}.jpg")
        try:
            media.frame_at(proxy, t, fp)
            frames.append(fp)
            names.append(f"kept-footage frame {i + 1} @{t:.1f}s")
        except media.MediaError:
            pass
    if not frames:
        res = set_frame(ctx, ratio, "crop")
        if res.startswith("EDL v"):
            res += ("\nNote: could not extract frames, so the crop is the "
                    "plain CENTER crop.")
        return res
    prompt = (f"These are {len(frames)} frames sampled across one video. "
              "For EACH frame, give the position of the MAIN SUBJECT "
              "(the person/face if there is one, else the visual focal "
              "point) as fractions of the frame, (0,0) = top-left. Reply "
              "with ONLY a JSON array, one object per frame in order: "
              '[{"x": 0.0-1.0, "y": 0.0-1.0}]')
    reply = llm.ask_vision(prompt, frames, purpose="vision_reframe",
                           image_names=names)
    pts = []
    for row in (llm.extract_json_array(reply) or []):
        try:
            pts.append((min(max(float(row["x"]), 0.0), 1.0),
                        min(max(float(row["y"]), 0.0), 1.0)))
        except (TypeError, ValueError, KeyError):
            continue
    if not pts:
        res = set_frame(ctx, ratio, "crop")
        if res.startswith("EDL v"):
            res += ("\nNote: the vision model gave no usable subject "
                    "positions, so the crop is the plain CENTER crop.")
        return res
    # Median, not mean: one wide establishing shot must not drag the crop
    # off every talking-head frame.
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    fx, fy = xs[len(xs) // 2], ys[len(ys) // 2]
    res = set_frame(ctx, ratio, "crop", focus_x=round(fx, 3),
                    focus_y=round(fy, 3))
    if res.startswith("EDL v"):
        res += (f"\nMeasured on {len(pts)} sampled frames: subject sits at "
                f"({fx:.2f}, {fy:.2f}) of the source frame — the crop "
                "follows it instead of the frame center. The focus is one "
                "FIXED point for the whole video (it does not track "
                "movement); if the subject moves across the frame between "
                "shots, say so honestly and offer pad_blur instead.")
    return res


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


ZOOM_MODES = ("punch", "ease", "push_in", "pull_out", "follow")
ZOOM_MODE_DESC = {"punch": "punch-in", "ease": "eased",
                  "push_in": "Ken Burns push-in",
                  "pull_out": "Ken Burns pull-out",
                  "follow": "gliding follow"}


def _parse_zoom_path(path):
    """Validate a follow-zoom path into EDL points, or return (None, reason).

    `f` is a fraction of the zoom's OWN window, so the path survives every
    later cut that moves the window (see the note on ZoomPathPoint). The
    agent may also pass `t` as a fraction — same thing under the name the
    event track uses — but never absolute seconds, which would silently
    become 1.0 after clamping and collapse the whole move into a step.
    """
    if not isinstance(path, (list, tuple)) or len(path) < 2:
        return None, ("REJECTED: mode 'follow' needs `path` — at least two "
                      "points of {f, cx, cy}, where f is 0 at the start of "
                      "the zoom's window and 1 at its end, and cx/cy are "
                      "0-1 fractions of the output frame.")
    if len(path) > ZOOM_PATH_MAX_POINTS:
        return None, (f"REJECTED: a follow path takes at most "
                      f"{ZOOM_PATH_MAX_POINTS} points; {len(path)} were "
                      "given. Use fewer waypoints — the move is interpolated "
                      "between them.")
    out, last = [], None
    for i, raw in enumerate(path, 1):
        if not isinstance(raw, dict):
            return None, f"REJECTED: path point {i} is not an object."
        try:
            f = float(raw.get("f", raw.get("t")))
            cx = float(raw["cx"])
            cy = float(raw["cy"])
        except (TypeError, ValueError, KeyError):
            return None, (f"REJECTED: path point {i} needs numeric f, cx and "
                          "cy.")
        if not (0.0 <= f <= 1.0):
            return None, (f"REJECTED: path point {i} has f={f:g}. f is a "
                          "FRACTION of the zoom window (0-1), not a time in "
                          "seconds.")
        if last is not None and f < last:
            return None, (f"REJECTED: path point {i} goes backwards in f. "
                          "Points must be in ascending order.")
        last = f
        out.append({"f": round(f, 4),
                    "cx": round(min(max(cx, 0.0), 1.0), 3),
                    "cy": round(min(max(cy, 0.0), 1.0), 3)})
    out[0]["f"] = 0.0
    out[-1]["f"] = 1.0
    return out, None


def add_zoom(ctx, start, end, strength=0.25, mode=None, cx=None, cy=None,
             path=None):
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 2)
        e = round(min(max(float(end), s), prog), 2)
        st = round(min(max(float(strength if strength is not None else 0.25),
                           ZOOM_STRENGTH_MIN), ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return ("REJECTED: start/end/strength must be numbers. start/end are "
                "OUTPUT-timeline seconds; strength 0.05-1.5 (0.25 = 25% "
                "punch-in; above 1.0 is a dramatic 2x+ punch).")
    if e - s < 0.2:
        return "REJECTED: a zoom needs at least 0.2s."
    zmode = (mode or "punch").strip().lower()
    if zmode not in ZOOM_MODES:
        return (f"REJECTED: mode must be one of {', '.join(ZOOM_MODES)}. "
                "punch = instant step in/out; ease = smooth ramp in and "
                "out; push_in / pull_out = continuous Ken Burns drift "
                "across the window; follow = ramps in and GLIDES its centre "
                "along `path` (for screen recordings and demos).")
    # Optional zoom TARGET (round 35): fractions of the output frame,
    # (0,0) = top-left. None keeps the legacy center zoom.
    tgt = {}
    for cname, cval in (("cx", cx), ("cy", cy)):
        if cval is None:
            continue
        try:
            tgt[cname] = round(min(max(float(cval), 0.0), 1.0), 3)
        except (TypeError, ValueError):
            return ("REJECTED: cx/cy must be numbers 0-1 — fractions of the "
                    "output frame ((0,0) = top-left, (0.5,0.5) = center). "
                    "Use look_at to find the subject first.")
    pts = None
    if zmode == "follow":
        pts, err = _parse_zoom_path(path)
        if err:
            return err
        if tgt:
            return ("REJECTED: a follow zoom is aimed by its `path`, not by "
                    "cx/cy — passing both would be two different answers to "
                    "where the frame should be. Put the first position in "
                    "path[0].")
    elif path:
        return (f"REJECTED: `path` only applies to mode 'follow'; this zoom "
                f"is '{zmode}'. Use mode='follow' to make the frame travel, "
                "or drop path for a fixed target.")
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    item = {"id": _next_item_id(zooms, "zm"), "start": s, "end": e,
            "strength": st}
    if zmode != "punch":
        item["mode"] = zmode
    item.update(tgt)
    if pts:
        item["path"] = pts
    zooms.append(item)
    fx["zooms"] = zooms
    edl["effects"] = fx
    if pts:
        aimed = (f", travelling ({pts[0]['cx']:g},{pts[0]['cy']:g}) → "
                 f"({pts[-1]['cx']:g},{pts[-1]['cy']:g}) across "
                 f"{len(pts)} points")
    else:
        aimed = (f", aimed at ({tgt.get('cx', 0.5):g}, {tgt.get('cy', 0.5):g})"
                 " of the frame" if tgt else "")
    return ctx.write_edl(
        edl, f"{ZOOM_MODE_DESC[zmode]} zoom {int(st * 100)}% on {s}-{e}s "
             f"(output time){aimed} [{item['id']}]")


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


# One honest line per junction style — shown in rejects and diffs so the
# agent describes what actually renders, not what a style name suggests.
TRANSITION_DESC = {
    "dip_black": "quick dip through black",
    "dip_white": "soft white fade-through",
    "whip_left": "fast leftward slide with motion blur",
    "whip_right": "fast rightward slide with motion blur",
    "zoom_punch": "accelerating push through the cut",
    "glitch": "RGB-split/noise burst around the cut",
    "flash": "additive white pop peaking on the cut",
}


def set_transitions(ctx, style, duration_s=0.3, scope="scene"):
    p = (style or "").strip().lower()
    sc = (scope or "scene").strip().lower()
    if sc not in TRANSITION_SCOPES:
        return (f"REJECTED: scope must be one of "
                f"{', '.join(TRANSITION_SCOPES)}. 'scene' (the default) puts "
                "the transition only where the footage actually changes shot "
                "or an insert splices in. 'every_cut' puts one at every "
                "junction including the jump cuts left behind by "
                "cut_silences — only right for a montage built from separate "
                "clips.")
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
                + "; ".join(f"{k} = {v}" for k, v in TRANSITION_DESC.items())
                + ". All are duration-preserving junction effects. True "
                "crossfades (overlapping footage) are not supported — say "
                "so if asked.")
    try:
        d = round(min(max(float(duration_s if duration_s is not None
                                else 0.3), TRANSITION_MIN_S),
                      TRANSITION_MAX_S), 2)
    except (TypeError, ValueError):
        return ("REJECTED: duration_s must be a number of seconds "
                f"({TRANSITION_MIN_S:g}-{TRANSITION_MAX_S:g}).")
    fx["transition"] = {"style": p, "duration_s": d, "scope": sc}
    edl["effects"] = fx
    n_cuts = max(0, len(edl.get("keep") or []) - 1) \
        + len(edl.get("inserts") or [])
    # How many junctions this ACTUALLY lands on, from the same resolver the
    # renderer uses — so the sentence the user reads and the video they watch
    # cannot disagree.
    try:
        hit = len(timeline_mod.transition_junctions(edl, ctx.index))
    except Exception:
        hit = n_cuts
    skipped = max(0, n_cuts - hit)

    if sc == "scene" and hit == 0 and n_cuts > 0:
        # Every junction is a jump cut inside one shot. Applying the transition
        # anyway is exactly the failure this scope exists to prevent, so don't
        # — and say why, with the two real options.
        return (f"NOT APPLIED: this edit has {n_cuts} junctions and every one "
                "of them is a jump cut INSIDE a single continuous shot (the "
                "cuts cut_silences left behind), not a scene change. A "
                f"{d}s {p} on each would fire a full-screen effect every "
                "couple of seconds through footage that never changes scene, "
                "which reads as broken. The EDL was NOT changed. Tell the "
                "user that, and offer either: leave the jump cuts clean (they "
                "are meant to be invisible), or set_transitions(scope="
                "'every_cut') if they really do want one on every cut.")

    note = ""
    if sc == "scene" and skipped:
        note = (f" — the other {skipped} junction"
                f"{'s are' if skipped != 1 else ' is'} a jump cut inside one "
                "continuous shot (left by cut_silences) and deliberately got "
                "NO transition; those are meant to be invisible. Use "
                "scope='every_cut' only if the user explicitly wants one on "
                "every cut.")
    where = ("every cut" if sc == "every_cut" else "scene changes")
    return ctx.write_edl(
        edl, f"transitions: {d}s {p} ({TRANSITION_DESC[p]}) at {where} — "
             f"{hit} of {n_cuts} junction{'s' if n_cuts != 1 else ''}{note}")


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


# ── Round 39: measuring burned-in text, and repainting it out ───────────────
# The most common thing users ask for on footage they did not shoot: "remove
# the captions", "change the caption font", "get rid of the username". Two
# separate failures used to make that a bad experience — the agent had to GUESS
# the rectangle from a contact sheet (and put the bar in the wrong place), and
# the only outcome available was a cover, never a removal. find_burned_text
# measures the rectangle; erase_* repaint the pixels and render from the
# repainted copy.

def _original_local(ctx):
    """The project's ORIGINAL video on local disk (downloaded once per turn).

    Every clean runs from the original, never from a previously cleaned file:
    repainting a repaint compounds the reconstruction and slowly smears the
    picture. It is also what makes an erase undoable — remove_erase re-cleans
    the remaining regions from untouched source.
    """
    if getattr(ctx, "_orig_local", None):
        return ctx._orig_local
    row = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
    if not row:
        raise RuntimeError("this project has no original video")
    local = os.path.join(ctx.workdir,
                         "orig" + os.path.splitext(row["storage_key"])[1])
    storage.download_to(row["storage_key"], local)
    ctx._orig_local = local
    ctx._orig_sha = row.get("sha256") or ""
    return local


def _clean_fp(sha, regions):
    # One implementation, shared with the renderer: it proves at render time
    # that the cleaned file is a repaint of THIS project's current video.
    return clean_fingerprint(sha, regions)


def _run_clean(ctx, regions):
    """Produce (asset_key, proxy_key, fp) for this exact region list.

    Cached on the fingerprint: re-erasing the same rectangle, or undoing one of
    three and putting it back, costs nothing the second time.
    """
    src = _original_local(ctx)
    fp = _clean_fp(getattr(ctx, "_orig_sha", ""), regions)
    key = f"cleaned/{ctx.project_id}/{fp[:16]}.mp4"
    pkey = f"cleaned/{ctx.project_id}/{fp[:16]}_proxy.mp4"
    if storage.exists(key) and storage.exists(pkey):
        return key, pkey, fp
    info = media.probe(src)
    if float(info["duration"]) > config.CLEAN_MAX_SOURCE_S:
        raise ValueError(
            f"this video is {info['duration'] / 60:.1f} min long and "
            f"repainting works frame by frame — above "
            f"{config.CLEAN_MAX_SOURCE_S / 60:.0f} min it does not finish "
            "inside one edit turn. Offer the alternatives honestly: cover the "
            "area with blur_region, or crop it out of frame with "
            "auto_reframe/set_frame.")
    # Duration is only half the cost. A 4K frame is 8x a 1080p one to decode,
    # repaint and re-encode, and two turns died of exactly this on 2026-07-25:
    # the box ran out of memory, so the WORKER died rather than the job — every
    # other user's turn went with it. Refuse honestly instead.
    mpx_s = (int(info["width"]) * int(info["height"]) / 1e6) * \
        float(info["duration"])
    if mpx_s > config.CLEAN_MAX_MPX_SECONDS:
        raise ValueError(
            f"this video is {info['width']}x{info['height']} for "
            f"{float(info['duration']) / 60:.1f} min, which is more pixels "
            "than a frame-by-frame repaint can finish inside one edit turn "
            f"(about {config.CLEAN_MAX_MPX_SECONDS / (int(info['width']) * int(info['height']) / 1e6) / 60:.0f} "
            "min at this resolution). Offer the alternatives honestly: cover "
            "the area with blur_region, or crop it out of frame with "
            "auto_reframe/set_frame. (Passing start/end does NOT help — the "
            "whole file is still re-encoded; it only narrows which frames get "
            "repainted.)")
    out = os.path.join(ctx.workdir, f"clean_{fp[:8]}.mp4")
    prox = os.path.join(ctx.workdir, f"clean_{fp[:8]}_proxy.mp4")
    stats = inpaint.clean_video(src, regions, out, prox)
    storage.upload_file(out, key, "video/mp4")
    storage.upload_file(prox, pkey, "video/mp4")
    # Asset rows are BOOKKEEPING here, not the contract: the EDL carries the
    # storage keys the renderer reads, so a repaint that is already uploaded
    # must not be thrown away because a row could not be written. (It is also
    # what stops this feature from being coupled to a schema migration —
    # assets.kind has a CHECK constraint, and 'clean_source'/'clean_proxy' are
    # only admitted by migration 007. Without it the erase still works; the
    # cleaned files just do not show in the admin's media list.)
    try:
        ctx.db.run(dbx.insert_asset, ctx.project_id, "clean_source", key,
                   bytes_=os.path.getsize(out), duration_s=stats["duration_s"],
                   width=stats["width"], height=stats["height"],
                   fps=stats["fps"],
                   meta={"filename": "cleaned-source.mp4", "clean_fp": fp,
                         "generated": True, "model": "local:inpaint",
                         "regions": len(regions)})
        ctx.db.run(dbx.insert_asset, ctx.project_id, "clean_proxy", pkey,
                   bytes_=os.path.getsize(prox), duration_s=stats["duration_s"],
                   meta={"filename": "cleaned-proxy.mp4", "clean_fp": fp,
                         "generated": True, "model": "local:inpaint"})
    except Exception as e:
        print(f"[erase] cleaned-source asset rows not recorded for project "
              f"{ctx.project_id} ({str(e)[:160]}) — the repaint itself is in "
              "storage and the EDL points at it; run migration 007",
              flush=True)
    ctx._clean_stats = stats
    # Reclaim the bytes now. A full-res cleaned copy is the size of the source
    # again, and two erases in one turn would otherwise sit on the worker's
    # ephemeral disk next to the original, the proxy and every render temp —
    # this box has run out of disk before. Storage has both objects.
    for p in (out, prox):
        try:
            os.remove(p)
        except OSError:
            pass
    return key, pkey, fp


def _apply_clean(ctx, regions, what):
    """Re-clean from the original for `regions` and write the EDL.

    `regions` is the COMPLETE list for this project (not a delta), because the
    cleaned file is one artifact: every erase re-derives it from the untouched
    original.
    """
    edl = dict(ctx.latest_edl()["json"])
    if not regions:
        edl["source_clean"] = None
        return ctx.write_edl(edl, f"restored the original pixels ({what})")
    src = _original_local(ctx)
    before = [inpaint.text_energy(src, (r["x"], r["y"], r["w"], r["h"]),
                                  samples=5) for r in regions]
    key, pkey, fp = _run_clean(ctx, regions)
    edl["source_clean"] = {"asset_key": key, "proxy_key": pkey, "fp": fp,
                           "regions": regions}
    result = ctx.write_edl(edl, what)
    if not result.startswith("EDL v"):
        return result
    # Honesty check: measure the ink in each rectangle on the file that will
    # actually be rendered. A claim that the text is gone is only made when
    # the pixels say so — and when they do not, the agent is told which
    # rectangle survived and what to try, instead of reporting success.
    local = os.path.join(ctx.workdir, "clean_check.mp4")
    try:
        storage.download_to(key, local)
        after = [inpaint.text_energy(local, (r["x"], r["y"], r["w"], r["h"]),
                                     samples=5) for r in regions]
    except Exception:
        after = None
    if after:
        lines = []
        for r, b, a in zip(regions, before, after):
            gone = (b <= 0.5) or (a <= max(1.5, b * 0.35))
            lines.append(
                f"[{r['id']}] ink {b:g} -> {a:g} "
                + ("— gone" if gone else "— STILL VISIBLE"))
        result += "\nMeasured on the repainted video: " + "; ".join(lines)
        if any("STILL" in ln for ln in lines):
            result += ("\nOne rectangle still shows ink. Widen it (outlines "
                       "and shadows sit outside the letters), or pass "
                       "fill='box' to repaint the whole rectangle instead of "
                       "the strokes. Do NOT tell the user it was removed "
                       "until this measures clean.")
        else:
            result += ("\nThe pixels are genuinely repainted — say REMOVED, "
                       "not covered. Renders now read the cleaned video; "
                       "cuts, captions and every timestamp are unchanged.")
    stats = getattr(ctx, "_clean_stats", None) or {}
    if any(p.get("escalated") for p in (stats.get("plates") or [])):
        result += ("\nThe caption sat on a solid bar, so the WHOLE bar was "
                   "repainted, not just the letters (lifting the text off a "
                   "bar would leave the bar). On a busy background that can "
                   "leave a soft patch — look at the preview and mention it "
                   "if you see one.")
    result += ("\nNEXT: render_preview so the user sees it, and if they asked "
               "for a different caption FONT, add_captions now — the frame is "
               "clear, so new captions cannot stack on the old ones.")
    return result


_SEED_PROMPT = (
    "These frames are from one video. Find every piece of text, watermark, "
    "logo or handle that is BURNED INTO the picture — permanently part of the "
    "footage, in the same place in every frame. Ignore anything that is part "
    "of the scene itself (a road sign, a book cover, a shop front, text on a "
    "screen being filmed).\n"
    "Reply with ONLY a JSON array, one object per mark:\n"
    '[{"text": "<what it reads, or a short description>", '
    '"x": <left>, "y": <top>, "w": <width>, "h": <height>}]\n'
    "x, y, w, h are FRACTIONS of the frame from the TOP-LEFT corner (0-1). "
    "Be generous: include the whole mark plus a little around it. "
    "Return [] if there is none.")


def _vision_seeded_regions(ctx, path, start, end, limit=3):
    """Ask the frames where a mark is, then measure the ink there.

    The measurement pass is what makes this trustworthy: the model only
    chooses where to look, and `snap_box_to_ink` returns None when there is no
    ink in the rectangle, so an imagined watermark produces no region at all.
    """
    if not llm.vision_available():
        return []
    try:
        dur = float(ctx.duration)
    except Exception:
        return []
    s = 0.0 if start is None else max(0.0, float(start))
    e = dur if end is None else min(dur, float(end))
    if e - s < 0.05:
        s, e = 0.0, dur
    frames = []
    for i in range(4):
        t = s + (e - s) * (i + 0.5) / 4
        fp = os.path.join(ctx.workdir, f"seed_{i}_{int(t * 100)}.jpg")
        try:
            media.frame_at(path, t, fp)
            frames.append(fp)
        except media.MediaError:
            pass
    if not frames:
        return []
    reply = llm.ask_vision(_SEED_PROMPT, frames, purpose="vision_look",
                           image_names=[f"frame {i + 1}"
                                        for i in range(len(frames))])
    out = []
    for row in (llm.extract_json_array(reply) or [])[:limit]:
        try:
            box = (float(row["x"]), float(row["y"]),
                   float(row["w"]), float(row["h"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= box[0] < 1 and 0 <= box[1] < 1
                and 0 < box[2] <= 1 and 0 < box[3] <= 1):
            continue
        try:
            snapped = inpaint.snap_box_to_ink(path, box, start=s, end=e)
        except Exception:
            snapped = None
        if snapped:
            snapped["label"] = str(row.get("text") or "burned-in text")[:80]
            out.append(snapped)
    return out


def find_burned_text(ctx, scope="all", start=None, end=None):
    """Measure where text is burned into the footage. Read-only."""
    if not ctx.has_main_video:
        return ("REJECTED: there is no main video in this project to scan.")
    sc = (scope or "all").strip().lower()
    if sc not in ("all", "captions", "watermark", "text"):
        return ("REJECTED: scope must be 'all', 'captions' (subtitle-style "
                "lines), 'watermark' (a static handle/logo) or 'text'.")
    try:
        path = ctx.proxy_path()
    except Exception:
        path = _original_local(ctx)
    try:
        regions = inpaint.detect_text_regions(
            path, start=start, end=end,
            samples=config.CLEAN_DETECT_SAMPLES)
    except Exception as e:
        return f"Could not scan the video for burned-in text ({str(e)[:160]})."
    if sc != "all":
        regions = [r for r in regions if r["kind"] == sc]
    if not regions:
        seeded = _vision_seeded_regions(ctx, path, start, end)
        if seeded:
            return ("The line-structure scan found nothing, so I LOOKED at "
                    "the frames and then measured the ink inside what the "
                    "frames showed. These rectangles are measured, not "
                    "estimated:\n"
                    + "\n".join(
                        f"{i}. {r['label']}: x={r['x']} y={r['y']} "
                        f"w={r['w']} h={r['h']} — ink in "
                        f"{int(r['coverage'] * 100)}% of the rectangle across "
                        f"{r['samples']} sampled frames"
                        for i, r in enumerate(seeded, start=1))
                    + "\nPass one of these to erase_region to repaint it out.")
        return ("No burned-in text found"
                + (f" of kind '{sc}'" if sc != "all" else "")
                + " — neither the line-structure scan nor a look at the "
                "frames turned any up. If the user insists there is some, ask "
                "WHERE it appears (corner? bottom? at which second?) and pass "
                "that rectangle to erase_region directly — do not invent one.")
    lines = []
    for i, r in enumerate(regions, start=1):
        lines.append(
            f"{i}. {r['kind']}: x={r['x']} y={r['y']} w={r['w']} h={r['h']} "
            f"— visible {r['first_s']}-{r['last_s']}s, in "
            f"{int(r['coverage'] * 100)}% of sampled frames"
            + (", content changes between frames"
               if r["changes"] > 6 else ", identical in every frame"))
    return ("Measured from the frames (not estimated — these rectangles are "
            "exact):\n" + "\n".join(lines)
            + "\nPass one of these rectangles to erase_region to repaint it "
            "out, or call erase_burned_text to erase every caption band in "
            "one pass.")


def erase_burned_text(ctx, scope="captions", start=None, end=None):
    """Detect and repaint out every burned-in region of one kind, one pass."""
    if not ctx.has_main_video:
        return "REJECTED: there is no main video in this project."
    sc = (scope or "captions").strip().lower()
    if sc not in ("all", "captions", "watermark", "text"):
        return ("REJECTED: scope must be 'captions', 'watermark', 'text' or "
                "'all'.")
    try:
        path = ctx.proxy_path()
    except Exception:
        path = _original_local(ctx)
    found = inpaint.detect_text_regions(path, start=start, end=end,
                                        samples=config.CLEAN_DETECT_SAMPLES)
    if sc != "all":
        found = [r for r in found if r["kind"] == sc]
    if not found:
        # Same second chance find_burned_text gets: look at the frames, then
        # measure the ink where they say it is. Still nothing = still nothing.
        for r in _vision_seeded_regions(ctx, path, start, end):
            found.append({"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                          "kind": sc if sc != "all" else "text"})
    if not found:
        return (f"NO CHANGE: no burned-in {sc} were found in the footage — "
                "not by the line scan and not by looking at the frames — so "
                "nothing was erased. Do NOT tell the user text was removed. "
                "Ask them where they see it and use erase_region with that "
                "rectangle.")
    edl = ctx.latest_edl()["json"]
    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    added = []
    for r in found:
        item = {"id": _next_item_id(regions, "er"),
                "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                "start": None, "end": None, "fill": "text",
                "kind": r["kind"]}
        regions.append(item)
        added.append(item)
    what = ("erased " + ", ".join(f"{a['kind']} at y={a['y']:g} [{a['id']}]"
                                  for a in added)
            + " from the source pixels")
    try:
        return _apply_clean(ctx, regions, what)
    except ValueError as e:
        return f"REJECTED: {e}"
    except Exception as e:
        return (f"The repaint failed ({str(e)[:180]}). Nothing changed — do "
                "NOT claim the text was removed.")


def erase_region(ctx, x, y, w, h, start=None, end=None, fill="text"):
    """Repaint one rectangle out of the source pixels."""
    if not ctx.has_main_video:
        return "REJECTED: there is no main video in this project."
    f = (fill or "text").strip().lower()
    if f not in ("text", "box"):
        return ("REJECTED: fill must be 'text' (repaint the letter strokes "
                "and keep the picture behind them — for captions, subtitles "
                "and watermarks) or 'box' (repaint the whole rectangle — for "
                "an object, a sticker or a logo shape).")
    try:
        rx, ry, rw, rh = float(x), float(y), float(w), float(h)
    except (TypeError, ValueError):
        return ("REJECTED: x, y, w, h must be numbers — FRACTIONS of the "
                "frame (0-1). x,y is the TOP-LEFT corner. Call "
                "find_burned_text to get exact rectangles instead of "
                "estimating them.")
    if not (0 <= rx <= 1 and 0 <= ry <= 1 and 0 < rw <= 1 and 0 < rh <= 1):
        return ("REJECTED: x, y, w, h are FRACTIONS of the frame (0-1). "
                "find_burned_text returns them in exactly this form.")
    if (start is None) != (end is None):
        return ("REJECTED: pass both start and end (SOURCE seconds — the "
                "repaint happens on the source before any cut), or neither "
                "for the whole video.")
    span = {}
    if start is not None:
        try:
            span = {"start": round(float(start), 2),
                    "end": round(float(end), 2)}
        except (TypeError, ValueError):
            return "REJECTED: start/end must be numbers of seconds."
        if span["end"] <= span["start"]:
            return "REJECTED: end must be after start."
    edl = ctx.latest_edl()["json"]
    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    item = {"id": _next_item_id(regions, "er"), "x": round(rx, 3),
            "y": round(ry, 3), "w": round(rw, 3), "h": round(rh, 3),
            "start": span.get("start"), "end": span.get("end"),
            "fill": f, "kind": None}
    regions.append(item)
    window = (f" from {item['start']}s to {item['end']}s (source time)"
              if span else " for the whole video")
    what = (f"erased the {'object' if f == 'box' else 'text'} at "
            f"x={item['x']},y={item['y']} size {item['w']}x{item['h']}"
            f"{window} from the source pixels [{item['id']}]")
    try:
        return _apply_clean(ctx, regions, what)
    except ValueError as e:
        return f"REJECTED: {e}"
    except Exception as e:
        return (f"The repaint failed ({str(e)[:180]}). Nothing changed — do "
                "NOT claim anything was removed.")


def reset_edit(ctx):
    """Throw the whole edit away and start again from the untouched source.

    The escape hatch. Every write tool validates the ENTIRE EDL before it will
    save, which is right — a half-valid timeline renders garbage — but it also
    means a single out-of-range span makes a project permanently unwritable:
    the keep fix is blocked by the volume span and the volume fix is blocked by
    the keep span, forever. A real customer's project reached exactly that
    state on 2026-07-25 (their replacement upload was shorter than the one the
    edit was built on) and the agent had to tell them it was stuck.

    This is the one write that cannot be blocked, because it does not build on
    the current state — it replaces it with a freshly generated default, which
    validates by construction.
    """
    if not ctx.has_main_video:
        return ("REJECTED: there is no main video to reset to. This project "
                "is a canvas program built from clips and images — remove the "
                "inserts you don't want instead.")
    prev = ctx.latest_edl()
    fresh = default_edl(ctx.duration)
    result = ctx.write_edl(
        fresh, "reset the edit — back to the full untouched video")
    if result.startswith("NO CHANGE"):
        return ("NO CHANGE: the edit is already the full untouched video "
                f"({ctx.duration}s, nothing cut). There was nothing to reset.")
    if not result.startswith("EDL v"):
        return result
    return (result + f"\nEverything from v{prev['version']} is gone: cuts, "
            "music, captions, inserts, effects, erases. The user's original "
            "upload is untouched and every version is still in history. Say "
            "plainly that you started over, then rebuild what they asked for.")


def remove_erase(ctx, id=None):
    """Undo one erase (or all), re-cleaning from the untouched original."""
    edl = ctx.latest_edl()["json"]
    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    if not regions:
        return ("NO CHANGE: nothing has been erased from this video's pixels. "
                "Do NOT tell the user you restored anything.")
    if id:
        hit = next((r for r in regions if r.get("id") == id), None)
        if not hit:
            have = ", ".join(r.get("id", "?") for r in regions)
            return (f"REJECTED: no erased region with id '{id}'. Existing: "
                    f"{have}. Call get_edl to see them, or omit id to restore "
                    "the whole original picture.")
        regions = [r for r in regions if r.get("id") != id]
        what = f"put back the pixels erased by {id}"
    else:
        regions, what = [], f"put back all {len(regions)} erased region(s)"
    try:
        return _apply_clean(ctx, regions, what)
    except Exception as e:
        return f"Could not rebuild the video ({str(e)[:180]}). Nothing changed."


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
        # Say WHICH kind it actually is. "nothing of that type is uploaded" on
        # a key the agent just read out of list_assets reads as "that file does
        # not exist", and the agent then tells the user their upload is missing.
        what = (f"'{asset_key}' is this project's {asset['kind']}, not a "
                f"{'/'.join(kinds)} asset" if asset
                else f"'{asset_key}' is not a {'/'.join(kinds)} asset in this "
                     "project")
        if asset and asset["kind"] in ("original", "proxy"):
            return None, (f"REJECTED: {what} — it IS the main video. Use "
                          "look_at(start, end) for the main video; "
                          "look_at_asset is only for a separately uploaded "
                          "clip or image.")
        return None, f"REJECTED: {what}. {hint}"
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
    orig_keep = [list(x) for x in keep]
    speed = edl.get("speed") or []
    tl = Timeline(keep, inserts, speed)
    at = round(min(max(at, 0.0), tl.out_duration), 2)
    pre_bounds = keep_boundaries(keep, speed)
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
                target_pre = keep_boundaries(keep, speed)[seg_i + 1]
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
    # Splicing shifts everything after the insert later (and may split a
    # take): re-anchor through the shared remap so content-anchored zooms/
    # sfx move WITH their footage instead of drifting onto the insert.
    old_tl = Timeline(orig_keep, inserts, speed)
    new_tl = Timeline(keep, edl["inserts"], speed)
    remap_notes = _remap_program_items(edl, old_tl, new_tl)
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
        if remap_notes:
            result += "\n" + "\n".join(remap_notes)
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
    # Removing an insert shifts everything after it earlier and shortens the
    # program — the SAME re-anchoring as a keep cut. The old sfx-only drop
    # left overlays/texts/windowed-stylize past the shortened end to reject
    # the whole removal, and let content-anchored zooms/sfx silently drift
    # onto different footage.
    speed = edl.get("speed") or []
    old_tl = Timeline(edl.get("keep") or [], inserts, speed)
    new_tl = Timeline(edl.get("keep") or [], edl["inserts"], speed)
    notes = _remap_program_items(edl, old_tl, new_tl)
    res = ctx.write_edl(
        edl, f"removed insert {id} "
             f"('{os.path.basename(hit['asset_key'])}', {hit['duration_s']}s) "
             "— prior timing restored")
    if notes and res.startswith("EDL v"):
        res += "\n" + "\n".join(notes)
    return res


def _resolve_audio_upload(ctx, asset_key):
    """(asset, note, error) for any UPLOAD that is to be used as sound.

    An audio file passes straight through; a VIDEO resolves to the audio taken
    out of it (its picture is never used); everything else falls through to
    _resolve_media_asset for the error, so the wording stays in one place."""
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, asset_key)
    if asset and asset["kind"] == "video_clip":
        return _audio_from_clip(ctx, asset)
    asset, err = _resolve_media_asset(ctx, asset_key, ("music",))
    return asset, None, err


def add_voiceover(ctx, asset_key, start_output_s=0.0, gain_db=0.0,
                  duck_others=True):
    asset, extract_note, err = _resolve_audio_upload(ctx, asset_key)
    if err:
        return err
    asset_key = asset["storage_key"]        # a video resolves to its audio
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
    if extract_note and not str(res).startswith("REJECTED"):
        res += "\n" + extract_note
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
#  EDL v2 write tools (round 35): speed, overlays, text, stylize,      #
#  custom grade, mastering                                             #
# ------------------------------------------------------------------ #

def _write_speed(ctx, prev, edl, desc, warn_slow=False):
    """Shared tail for speed writes: re-snap inserts to the speed-remapped
    boundaries, re-anchor program-time items through the shared remap (the
    program clock itself just changed), then write and disclose the new
    program length."""
    keep = edl["keep"]
    speed = edl.get("speed") or []
    ins_notes = []
    if edl.get("inserts"):
        # A speed change moves every downstream boundary, so re-snapping by
        # nearest VALUE can silently hop an insert to a DIFFERENT junction
        # (4x the intro and boundary 10.0 lands nearer the NEXT take's start
        # than its own remapped 2.5). The keep list is unchanged here, so
        # old and new boundary lists pair 1:1 — re-snap by junction INDEX,
        # which preserves exactly which cut the insert sits at.
        old_bounds = keep_boundaries(keep, prev["json"].get("speed") or [])
        bounds = keep_boundaries(keep, speed)
        resnapped = []
        for ins in edl["inserts"]:
            oi = min(range(len(old_bounds)),
                     key=lambda k: abs(old_bounds[k] - ins["at_output_s"]))
            new_at = bounds[oi]
            if abs(new_at - float(ins["at_output_s"])) > 0.01:
                ins_notes.append(
                    f"note: insert {ins.get('id')} now splices at "
                    f"{round(new_at, 2)}s — the same junction, on the "
                    "speed-remapped clock.")
            resnapped.append({**ins, "at_output_s": new_at})
        edl["inserts"] = resnapped
    old_tl = Timeline(prev["json"]["keep"], prev["json"].get("inserts") or [],
                      prev["json"].get("speed") or [])
    new_tl = Timeline(keep, edl.get("inserts") or [], speed)
    notes = ins_notes + _remap_program_items(edl, old_tl, new_tl)
    result = ctx.write_edl(edl, desc)
    if not result.startswith("EDL v"):
        return result
    result += (f"\nProgram length: {round(old_tl.out_duration, 2)}s -> "
               f"{round(new_tl.out_duration, 2)}s.")
    if warn_slow:
        result += ("\nWARNING: slow motion duplicates frames on this "
                   "pipeline — below 0.6x it visibly steps. Prefer 0.6-0.8x "
                   "unless the user accepts the stepping; say so honestly.")
    if notes:
        result += "\n" + "\n".join(notes)
    return result


def set_speed(ctx, start, end, factor):
    """A constant speed factor over a SOURCE-time span (like set_volume) —
    the ramp belongs to the footage it was placed on, so it survives later
    cuts without drifting."""
    if not ctx.has_main_video:
        return ("REJECTED: speed ramps address SOURCE time and need a main "
                "video — an image/clip-only program has no source clock. "
                "Choose clip windows with insert_media instead.")
    try:
        s, e = ctx.clamp(start), ctx.clamp(end)
    except ValueError as err:
        return f"REJECTED: {err}"
    if e - s < 0.2:
        return "REJECTED: a speed span needs at least 0.2s of source footage."
    try:
        f = round(min(max(float(factor), SPEED_FACTOR_MIN),
                      SPEED_FACTOR_MAX), 3)
    except (TypeError, ValueError):
        return ("REJECTED: factor must be a number 0.25-4.0 (2.0 = double "
                "speed, 0.5 = half speed; audio keeps its pitch).")
    if abs(f - 1.0) < 0.01:
        return ("REJECTED: factor 1.0 is normal speed — nothing to write. "
                "Use remove_speed(id) to undo an existing span.")
    prev = ctx.latest_edl()
    edl = dict(prev["json"])
    spans = [dict(sp) for sp in (edl.get("speed") or [])]
    # Speed spans never overlap (schema): an overlapping request REPLACES
    # what it collides with, and the result says which spans died for it.
    replaced = [sp for sp in spans
                if float(sp["end"]) > s + 1e-6 and float(sp["start"]) < e - 1e-6]
    spans = [sp for sp in spans if sp not in replaced]
    item = {"id": _next_item_id(spans, "sp"), "start": s, "end": e,
            "factor": f}
    spans.append(item)
    spans.sort(key=lambda x: float(x["start"]))
    edl["speed"] = spans
    desc = f"{f:g}x speed on source {s}-{e}s [{item['id']}]"
    if replaced:
        desc += (", replacing overlapping span(s) "
                 + ", ".join(f"{sp.get('id')} ({float(sp['factor']):g}x "
                             f"{sp['start']}-{sp['end']}s)"
                             for sp in replaced))
    return _write_speed(ctx, prev, edl, desc, warn_slow=(f < 0.6))


def remove_speed(ctx, id):
    prev = ctx.latest_edl()
    edl = dict(prev["json"])
    spans = [dict(sp) for sp in (edl.get("speed") or [])]
    hit = next((sp for sp in spans if sp.get("id") == id), None)
    if not hit:
        have = ", ".join(sp.get("id") or "?" for sp in spans) or "none"
        return (f"REJECTED: no speed span with id '{id}'. Existing speed "
                f"spans: {have}. Call get_edl to see them.")
    edl["speed"] = [sp for sp in spans if sp.get("id") != id]
    return _write_speed(
        ctx, prev, edl,
        f"removed speed span {id} ({float(hit['factor']):g}x on source "
        f"{hit['start']}-{hit['end']}s) — normal speed restored there")


def _parse_anim_float(v, name):
    """(value, error): a plain number, or a keyframe list passed through for
    the schema's _norm_anim to validate and clamp."""
    if isinstance(v, bool):
        return None, f"REJECTED: {name} must be a number, not a boolean."
    if isinstance(v, (int, float)):
        return float(v), None
    if isinstance(v, list):
        return v, None
    return None, (f"REJECTED: {name} must be a number (a fraction of the "
                  'frame) or a keyframe list like [{"t":0,"v":0.8}, '
                  '{"t":3,"v":0.2}] with t in seconds from the '
                  "overlay's own start.")


def add_overlay(ctx, asset_key, start, duration_s=None, x=0.5, y=0.5,
                scale=0.4, opacity=None, entrance=None, exit=None,
                source_start_s=None, fit=None):
    """Draw an asset OVER the program picture for a program-time window —
    picture-in-picture, a corner logo, or (fit='cover') a full-frame B-ROLL
    cutaway while the program's audio keeps playing."""
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref"))
    if err:
        return err
    kind = "image" if asset["kind"] == "image_ref" else "video"
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.4:
        return ("REJECTED: there is no program yet to overlay onto — place "
                "footage first (insert_media / keep), then add the overlay.")
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 2)
    except (TypeError, ValueError):
        return "REJECTED: start must be a number (PROGRAM-timeline seconds)."
    off = None
    clip_dur = None
    if kind == "video":
        clip_dur = _asset_media_duration(ctx, asset)
        if source_start_s is not None:
            try:
                off = round(max(0.0, float(source_start_s)), 2)
            except (TypeError, ValueError):
                return ("REJECTED: source_start_s must be a number of "
                        "seconds (a seek into the overlay clip).")
            if off >= clip_dur - 0.2:
                return (f"REJECTED: source_start_s {off}s is at/past the end "
                        f"of the clip ({clip_dur:.1f}s).")
    remaining = round(prog - s, 2)
    if duration_s is None:
        # Image overlays default to a 4s moment; video overlays play the
        # clip out (bounded by the program end) — both concrete in the EDL.
        dur = round(min(4.0, remaining), 2) if kind == "image" else \
            round(min(clip_dur - (off or 0.0), remaining), 2)
    else:
        try:
            dur = round(min(max(float(duration_s), 0.2), remaining), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."
        if kind == "video" and (off or 0.0) + dur > clip_dur + 0.05:
            return (f"REJECTED: the window {off or 0}-"
                    f"{round((off or 0.0) + dur, 2)}s runs past the end of "
                    f"the clip ({clip_dur:.1f}s).")
    if dur < 0.2:
        return "REJECTED: the overlay window is shorter than 0.2s."
    for label, v in (("entrance", entrance), ("exit", exit)):
        if v is not None and v not in OVERLAY_ANIMS:
            return (f"REJECTED: {label} must be one of "
                    f"{', '.join(OVERLAY_ANIMS)}.")
    fitv = None
    if fit is not None:
        fitv = str(fit).strip().lower()
        if fitv in ("", "none", "pip"):
            fitv = None
        elif fitv != "cover":
            return ("REJECTED: fit must be 'cover' (full-frame b-roll "
                    "cutaway) or omitted (width-fraction PIP).")
    xv, xerr = _parse_anim_float(x if x is not None else 0.5, "x")
    if xerr:
        return xerr
    yv, yerr = _parse_anim_float(y if y is not None else 0.5, "y")
    if yerr:
        return yerr
    try:
        sc = round(min(max(float(scale if scale is not None else 0.4),
                           OVERLAY_SCALE_MIN), OVERLAY_SCALE_MAX), 3)
    except (TypeError, ValueError):
        return ("REJECTED: scale must be a number — the overlay's width as "
                "a fraction of the frame width (0.05-1.0).")
    op = None
    if opacity is not None:
        try:
            op = float(opacity)
        except (TypeError, ValueError):
            return "REJECTED: opacity must be a number (0.05-1.0)."
    overlays = [dict(o) for o in (edl.get("overlays") or [])]
    item = {"id": _next_item_id(overlays, "ov"), "asset_key": asset_key,
            "kind": kind, "start": s, "duration_s": dur, "x": xv, "y": yv,
            "scale": sc, "fit": fitv, "opacity": op,
            "entrance": entrance, "exit": exit,
            "source_start_s": off if kind == "video" else None}
    overlays.append(item)
    edl["overlays"] = overlays
    moving = isinstance(xv, list) or isinstance(yv, list)
    pos = ("a keyframed drift" if moving
           else f"center ({xv:g}, {yv:g})")
    what = (f"FULL-FRAME b-roll cover" if fitv == "cover"
            else f"{sc:g}x frame width at {pos}")
    res = ctx.write_edl(
        edl, f"overlay {kind} '{name}' at {s}-{round(s + dur, 2)}s "
             f"(program time), {what} [{item['id']}]")
    if res.startswith("EDL v"):
        notes = []
        if fitv == "cover":
            notes.append("the picture fully switches to this asset for the "
                         "window while the program's AUDIO (speech, music) "
                         "keeps playing — the b-roll cutaway")
        if kind == "video":
            notes.append("the overlay's own audio does NOT play (silent "
                         "picture-in-picture) — say so if the user wants "
                         "its sound")
        notes.append("overlays render above the footage but BELOW captions, "
                     "and do not track objects in the footage")
        res += "\nNote: " + "; ".join(notes) + "."
    return res


def remove_overlay(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(o) for o in (edl.get("overlays") or [])]
    hit = next((o for o in items if o.get("id") == id), None)
    if not hit:
        have = ", ".join(o.get("id") or "?" for o in items) or "none"
        return (f"REJECTED: no overlay with id '{id}'. Existing overlays: "
                f"{have}. Call get_edl to see them.")
    edl["overlays"] = [o for o in items if o.get("id") != id]
    return ctx.write_edl(
        edl, f"removed overlay {id} "
             f"('{os.path.basename(hit['asset_key'])}', "
             f"{hit['start']}-{round(float(hit['start']) + float(hit['duration_s']), 2)}s)")


def move_overlay(ctx, id, start=None, x=None, y=None, scale=None):
    """Reposition/retime/resize an EXISTING overlay in place — only the
    fields passed change."""
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(o) for o in (edl.get("overlays") or [])]
    hit = next((o for o in items if o.get("id") == id), None)
    if not hit:
        have = ", ".join(o.get("id") or "?" for o in items) or "none"
        return (f"REJECTED: no overlay with id '{id}'. Existing overlays: "
                f"{have}. Call get_edl to see them.")
    prog = program_duration(edl)
    before = dict(hit)
    note = ""
    if start is not None:
        try:
            hit["start"] = round(min(max(float(start), 0.0),
                                     max(0.0, prog - 0.2)), 2)
        except (TypeError, ValueError):
            return "REJECTED: start must be a number (program seconds)."
        overrun = hit["start"] + float(hit["duration_s"]) - prog
        if overrun > 0.01:
            hit["duration_s"] = round(prog - hit["start"], 2)
            for prop in ("x", "y"):    # keyframes past the new end would
                if isinstance(hit.get(prop), list):     # reject the write
                    hit[prop] = clip_anim(hit[prop], hit["duration_s"])
            note = (f"\nNote: the window was shortened to "
                    f"{hit['duration_s']}s so it still ends inside the "
                    "program.")
    if x is not None:
        xv, xerr = _parse_anim_float(x, "x")
        if xerr:
            return xerr
        hit["x"] = xv
    if y is not None:
        yv, yerr = _parse_anim_float(y, "y")
        if yerr:
            return yerr
        hit["y"] = yv
    if scale is not None:
        try:
            hit["scale"] = round(min(max(float(scale), OVERLAY_SCALE_MIN),
                                     OVERLAY_SCALE_MAX), 3)
        except (TypeError, ValueError):
            return "REJECTED: scale must be a number (0.05-1.0)."
    if hit == before:
        return (f"NO CHANGE — overlay {id} already has those settings. Do "
                "NOT tell the user you changed anything.")
    edl["overlays"] = items
    changed = ", ".join(f"{k}={hit.get(k)}"
                        for k in ("start", "duration_s", "x", "y", "scale")
                        if hit.get(k) != before.get(k))
    res = ctx.write_edl(edl, f"overlay {id} moved: {changed}")
    if note and res.startswith("EDL v"):
        res += note
    return res


def add_text(ctx, text, start, end, template="title", x=None, y=None,
             size_scale=None, color=None, accent_color=None, font=None,
             entrance=None, exit=None, uppercase=None, box=None):
    """Burn a designed text template over a program-time window — titles,
    lower thirds, callouts, big numbers, quotes, chapter markers."""
    t = (text or "").strip()
    if not t:
        return "REJECTED: text is empty."
    tpl = (template or "title").strip().lower()
    if tpl not in TEXT_TEMPLATES:
        return (f"REJECTED: template must be one of "
                f"{', '.join(TEXT_TEMPLATES)}.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.4:
        return ("REJECTED: there is no program yet to put text on — place "
                "footage first, then add the text.")
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.3)), 2)
        e = round(min(max(float(end), s + 0.3), prog), 2)
    except (TypeError, ValueError):
        return ("REJECTED: start/end must be numbers (PROGRAM-timeline "
                "seconds — where in the edited video the text shows).")
    if entrance is not None and entrance not in TEXT_ANIMS:
        return (f"REJECTED: entrance must be one of "
                f"{', '.join(TEXT_ANIMS)}.")
    if exit is not None and (exit not in TEXT_ANIMS or exit == "typewriter"):
        return ("REJECTED: exit must be one of "
                + ", ".join(a for a in TEXT_ANIMS if a != "typewriter")
                + " (typewriter is entrance-only).")
    if font is not None and font not in TEXT_FONTS:
        return (f"REJECTED: font must be one of the bundled families: "
                f"{', '.join(TEXT_FONTS)}.")
    for label, v in (("x", x), ("y", y)):
        if v is not None:
            try:
                float(v)
            except (TypeError, ValueError):
                return (f"REJECTED: {label} must be a number 0-1 (fraction "
                        "of the frame).")
    if size_scale is not None:
        try:
            float(size_scale)
        except (TypeError, ValueError):
            return "REJECTED: size_scale must be a number (0.4-3.0)."
    texts = [dict(tx) for tx in (edl.get("texts") or [])]
    item = {"id": _next_item_id(texts, "tx"), "text": t[:200], "start": s,
            "end": e, "template": tpl,
            "x": float(x) if x is not None else None,
            "y": float(y) if y is not None else None,
            "size_scale": float(size_scale) if size_scale is not None else None,
            "color": color, "accent_color": accent_color, "font": font,
            "entrance": entrance, "exit": exit,
            "uppercase": bool(uppercase) if uppercase is not None else None,
            "box": bool(box) if box is not None else None}
    texts.append(item)
    edl["texts"] = texts
    return ctx.write_edl(
        edl, f"{tpl} text \"{t[:40]}\" at {s}-{e}s (program time) "
             f"[{item['id']}]")


def remove_text(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(tx) for tx in (edl.get("texts") or [])]
    hit = next((tx for tx in items if tx.get("id") == id), None)
    if not hit:
        have = ", ".join(tx.get("id") or "?" for tx in items) or "none"
        return (f"REJECTED: no text with id '{id}'. Existing texts: {have}. "
                "Call get_edl to see them.")
    edl["texts"] = [tx for tx in items if tx.get("id") != id]
    return ctx.write_edl(
        edl, f"removed {hit.get('template', 'text')} text "
             f"\"{str(hit.get('text', ''))[:24]}\" ({id})")


def set_caption_mutes(ctx, spans=None):
    """Silence burned captions over PROGRAM-time windows, leaving them on
    everywhere else. Full replacement: spans=[] clears every mute."""
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.4:
        return ("REJECTED: there is no program yet to mute captions on — "
                "place footage first.")
    if not edl.get("captions"):
        return ("REJECTED: this edit has no captions to mute. Turn them on "
                "with add_captions first, or there is nothing to hide.")
    if spans is None:
        return ("REJECTED: pass spans as a list of [start, end] PROGRAM-second "
                "pairs, or spans=[] to un-mute everything.")
    if not isinstance(spans, list):
        return "REJECTED: spans must be a list of [start, end] pairs."
    norm = []
    for i, sp in enumerate(spans):
        if not isinstance(sp, (list, tuple)) or len(sp) != 2:
            return (f"REJECTED: spans[{i}] must be a [start, end] pair of "
                    "PROGRAM-time seconds.")
        try:
            s = round(min(max(float(sp[0]), 0.0), max(0.0, prog - 0.05)), 2)
            e = round(min(max(float(sp[1]), s + 0.05), prog), 2)
        except (TypeError, ValueError):
            return f"REJECTED: spans[{i}] must be two numbers."
        norm.append([s, e])
    edl["caption_mutes"] = norm
    if not norm:
        return ctx.write_edl(edl, "caption mutes cleared — captions show "
                                  "everywhere again")
    bits = ", ".join(f"{s:g}-{e:g}s" for s, e in norm)
    result = ctx.write_edl(
        edl, f"captions muted over {len(norm)} window(s): {bits}")
    if result.startswith("EDL v"):
        # Say what it actually does, so the model does not describe this to the
        # user as "removed those words" — the speech is untouched, only the
        # burned text is hidden.
        result += ("\nNote: this hides the burned caption text over those "
                   "windows only — the audio and the cut are unchanged, and "
                   "captions still show everywhere else. Caption lines are "
                   "hidden WHOLE: a line that starts before a window and runs "
                   "into it disappears entirely (its word timings are baked, "
                   "so it cannot be cut in half). A line that merely grazes an "
                   "edge — under 0.15s inside — is kept. If that costs words "
                   "either side of the window, tighten the window to the "
                   "effect itself rather than widening it.")
    return result


# Colour cards are synthesized locally with Pillow: a blank/solid/gradient
# screen must never depend on the image-generation API (which has 404'd for
# weeks at a time — see config.IMAGE_GEN_MODEL) or on fetching a colour swatch
# off the public internet, which is what the agent resorted to before these
# tools existed.
CARD_DIMS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
             "4:3": (1440, 1080), "3:4": (1080, 1440)}
CARD_DIRECTIONS = ("vertical", "horizontal", "diagonal", "radial")


def _hex_rgb(color):
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _color_card_asset(ctx, color, color2=None, direction="vertical"):
    """Get (or create) a project image asset that is a flat colour (color2
    None) or a two-colour gradient, at the output aspect. Cached per
    (project, colour(s), direction, aspect) so a repeated card uploads one
    image, not many."""
    aspect = _default_image_aspect(ctx)
    w, h = CARD_DIMS.get(aspect, CARD_DIMS["16:9"])
    c1 = color.upper()
    c2 = color2.upper() if color2 else None
    ck = (c1, c2, direction if c2 else None, aspect)
    hit = getattr(ctx, "_card_assets", None)
    if hit is None:
        hit = ctx._card_assets = {}
    if ck in hit:
        return hit[ck], None
    from PIL import Image
    try:
        if not c2:
            img = Image.new("RGB", (w, h), _hex_rgb(c1))
            label, kind = f"Solid {c1}", "solid"
        else:
            img = _gradient_image(w, h, _hex_rgb(c1), _hex_rgb(c2), direction)
            label, kind = f"{c1}->{c2} {direction} gradient", "gradient"
        tag = c1.lstrip("#") + (("-" + c2.lstrip("#")) if c2 else "")
        path = os.path.join(ctx.workdir, f"card_{tag}_{w}x{h}.png")
        img.save(path)
    except Exception as e:
        return None, f"Could not build the colour card ({str(e)[:120]})."
    key = f"generated/{ctx.project_id}/card-{uuid.uuid4().hex[:12]}.png"
    try:
        storage.upload_file(path, key, "image/png")
    except Exception as e:
        return None, (f"The card was built but could not be saved to storage "
                      f"({str(e)[:120]}). Try again.")
    ctx.db.run(dbx.insert_asset, ctx.project_id, "image_ref", key,
               bytes_=os.path.getsize(path), width=w, height=h,
               meta={"filename": f"color-card-{tag}.png",
                     "caption": f"{label} card ({w}x{h})",
                     "generated": True, "model": f"local:{kind}"})
    hit[ck] = key
    return key, None


def _gradient_image(w, h, rgb1, rgb2, direction):
    """A two-colour linear/radial gradient. numpy-vectorised (already a hard
    dependency of the worker) so a 1080x1920 card builds in a few ms."""
    import numpy as np
    from PIL import Image
    if direction == "radial":
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
        t = np.clip(d / np.sqrt(2.0), 0.0, 1.0)
    elif direction == "horizontal":
        t = np.tile(np.linspace(0.0, 1.0, w), (h, 1))
    elif direction == "diagonal":
        gx = np.linspace(0.0, 1.0, w)[None, :]
        gy = np.linspace(0.0, 1.0, h)[:, None]
        t = np.clip((gx + gy) / 2.0, 0.0, 1.0)
    else:                                            # vertical (default)
        t = np.tile(np.linspace(0.0, 1.0, h), (w, 1)).T
    t = t[..., None]
    c1 = np.array(rgb1, dtype=np.float32)
    c2 = np.array(rgb2, dtype=np.float32)
    arr = (c1 * (1.0 - t) + c2 * t).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


# Back-compat alias: add_title_card was written against the solid-only helper.
def _solid_card_asset(ctx, color):
    return _color_card_asset(ctx, color)


def add_color_screen(ctx, at_output_s, duration_s=2.0, color="#000000",
                     color2=None, direction="vertical", motion=None):
    """Cut to a full-frame SOLID or GRADIENT colour screen for a moment, then
    return to the footage — no text, no image generation. Built locally."""
    c1 = (color or "#000000").strip().upper()
    if not HEX_COLOR.match(c1):
        return "REJECTED: color must be #RRGGBB hex, e.g. #FFFFFF for white."
    c2 = None
    if color2:
        c2 = str(color2).strip().upper()
        if not HEX_COLOR.match(c2):
            return ("REJECTED: color2 must be #RRGGBB hex (the gradient's "
                    "second colour), or omit it for a flat fill.")
    direction = (direction or "vertical").strip().lower()
    if direction not in CARD_DIRECTIONS:
        return (f"REJECTED: direction must be one of "
                f"{', '.join(CARD_DIRECTIONS)}.")
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — where in the FINAL "
                "edited video the screen cuts in, in seconds.")
    try:
        dur = round(min(max(float(duration_s), 0.2), 30.0), 2)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds."
    if motion is not None:
        motion = str(motion).strip().lower() or None
        if motion and motion not in INSERT_MOTIONS:
            return (f"REJECTED: motion must be one of "
                    f"{', '.join(INSERT_MOTIONS)}.")

    key, err = _color_card_asset(ctx, c1, c2, direction)
    if err:
        return err
    placed = insert_media(ctx, key, at, duration_s=dur, motion=motion)
    if not placed.startswith("EDL v"):
        return placed
    look = (f"a {direction} {c1}->{c2} gradient" if c2 else f"solid {c1}")
    named = {"#FFFFFF": " (white)", "#000000": " (black)"}.get(c1, "")
    result = placed.split(". Before:")[0]
    result += (f"\nThis is {look}{named if not c2 else ''} — a full-frame "
               "colour screen with NO text (add_text over it, or use "
               "add_title_card if you wanted a titled card). Spoken-word "
               "captions do not appear on it (inserted media is never "
               "captioned), so nothing overlaps.")
    return result


# ── Corrupt-screen "glitch" moments (round 37) ──────────────────────────────
# A full-frame digital-corruption BEAT the agent drops between sections — the
# promo/meme "the signal breaks, then we cut to the next thing" transition
# (podcast -> CORRUPT -> punchline). Like the colour cards, the clip is
# SYNTHESIZED locally with ffmpeg from lavfi noise sources: it never touches
# the image/video-generation APIs and never depends on the source footage, so
# it is always available and costs no generation credits. Each style is one
# opinionated filtergraph rendered at a low internal resolution and
# neighbour-upscaled to the output frame — the chunky blockiness IS the
# aesthetic and keeps the geq passes cheap on the 1-vCPU worker. The clip
# carries a matching static/hiss audio burst (unless sound=False) so the
# corruption is HEARD as well as seen: the renderer plays an insert's own audio
# over its window (media.probe -> has_ins_audio), so during the glitch the
# program audio drops out and the static plays alone, then footage resumes.
CORRUPT_STYLES = ("digital", "vhs", "static")
CORRUPT_FPS = 20
CORRUPT_MIN_S = 0.2
CORRUPT_MAX_S = 6.0


def _corrupt_filtergraph(style, ow, oh, dur, intensity, sound):
    """Return (filter_complex_str, has_audio) for one glitch style at output
    ow×oh. intensity 0-1 scales displacement / RGB split / snow / band
    strength. Commas inside geq/mod/pow expressions are backslash-escaped so
    the graph survives being passed as a single subprocess argv (no shell)."""
    k = max(0.0, min(1.0, float(intensity)))
    # internal render size: cap the long edge ~640 so the per-pixel geq passes
    # stay cheap; the neighbour upscale to output gives the chunky look.
    sc = 640.0 / max(ow, oh)
    iw = max(2, int(round(ow * sc / 2)) * 2)
    ih = max(2, int(round(oh * sc / 2)) * 2)
    a_amp = round(0.25 + 0.4 * k, 3)
    if style == "vhs":
        band = round(60 + 60 * k)                 # tracking-band brightness
        split = round(4 + 10 * k)                 # chroma bleed
        v = (
            f"color=c=black:s={iw}x{ih}:r={CORRUPT_FPS}:d={dur},"
            f"format=yuv420p[bg];"
            f"nullsrc=s=48x27:r={CORRUPT_FPS}:d={dur},format=yuv420p,"
            f"geq=lum='120+random(1)*70':cb='random(2)*255':cr='random(3)*255',"
            f"scale={iw}:{ih}:flags=neighbor[base];"
            f"[bg][base]blend=all_mode=normal:all_opacity=0.8[m1];"
            f"[m1]geq=lum='lum(X\\,Y)+{band}*exp(-pow((Y-mod(T*300\\,{ih}))"
            f"/22\\,2))':cb='cb(X\\,Y)':cr='cr(X\\,Y)'[m2];"
            f"[m2]geq=lum='lum(X\\,Y)*(0.72+0.28*abs(sin(Y*1.6)))':"
            f"cb='cb(X\\,Y)':cr='cr(X\\,Y)'[m3];"
            f"[m3]rgbashift=rh={split}:bh=-{split}[m4];"
            f"[m4]scale={ow}:{oh}:flags=neighbor,format=yuv420p[vout]"
        )
        acolor = "brown"
    elif style == "static":
        roll = round(120 + 120 * k)               # rolling hold-bar speed
        v = (
            f"nullsrc=s={iw}x{ih}:r={CORRUPT_FPS}:d={dur},format=yuv420p,"
            f"geq=lum='random(1)*255':cb='128+(random(2)-0.5)*50':"
            f"cr='128+(random(3)-0.5)*50'[snow];"
            f"[snow]geq=lum='lum(X\\,Y)*(0.55+0.45*abs(sin((Y+mod(T*{roll}"
            f"\\,{ih}))*0.6)))':cb='cb(X\\,Y)':cr='cr(X\\,Y)'[m1];"
            f"[m1]scale={ow}:{oh}:flags=neighbor,format=yuv420p[vout]"
        )
        acolor = "white"
    else:                                         # digital (default)
        shift = round(70 + 120 * k)               # horizontal tear amount
        split = round(6 + 16 * k)                 # RGB split
        bh = max(2, int(round(32 * oh / ow / 2)) * 2)   # block grid, output AR
        band_h = max(6, round(ih / 30.0))         # tear-band height
        addop = round(0.06 + 0.12 * k, 3)         # snow overlay opacity
        tear = f"{shift}*sin(floor(Y/{band_h})*2.3+T*16)"
        v = (
            f"nullsrc=s=32x{bh}:r={CORRUPT_FPS}:d={dur},format=gbrp,"
            f"geq=r='random(1)*255':g='random(2)*255':b='random(3)*255',"
            f"scale={iw}:{ih}:flags=neighbor[blk];"
            f"[blk]geq=r='r(mod(X+{tear}\\,W)\\,Y)':"
            f"g='g(mod(X+{tear}\\,W)\\,Y)':b='b(mod(X+{tear}\\,W)\\,Y)'[torn];"
            f"nullsrc=s={iw}x{ih}:r={CORRUPT_FPS}:d={dur},format=gbrp,"
            f"geq=r='random(4)*255':g='random(4)*255':b='random(4)*255'[snow];"
            f"[torn][snow]blend=all_mode=addition:all_opacity={addop}[m];"
            f"[m]rgbashift=rh={split}:bh=-{split}:gv=3[m2];"
            f"[m2]scale={ow}:{oh}:flags=neighbor,format=yuv420p[vout]"
        )
        acolor = "pink"
    if sound:
        v += (f";anoisesrc=d={dur}:c={acolor}:a={a_amp},"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo[aout]")
    return v, sound


def _corrupt_glitch_asset(ctx, style, intensity, dur, sound):
    """Get (or create) a project video_clip that is a synthesized glitch clip.
    Cached per (style, intensity bucket, duration, sound, aspect) so a repeated
    corrupt-screen beat renders and uploads one clip, not many."""
    aspect = _default_image_aspect(ctx)
    ow, oh = CARD_DIMS.get(aspect, CARD_DIMS["16:9"])
    kb = round(max(0.0, min(1.0, float(intensity))), 1)      # 0.0..1.0 buckets
    ck = (style, kb, dur, bool(sound), aspect)
    hit = getattr(ctx, "_corrupt_assets", None)
    if hit is None:
        hit = ctx._corrupt_assets = {}
    if ck in hit:
        return hit[ck], None
    fg, has_a = _corrupt_filtergraph(style, ow, oh, dur, kb, sound)
    path = os.path.join(ctx.workdir, f"glitch_{style}_{uuid.uuid4().hex[:8]}.mp4")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-filter_complex", fg, "-map", "[vout]"]
    if has_a:
        cmd += ["-map", "[aout]", "-c:a", "aac"]
    cmd += ["-t", str(dur), "-r", str(CORRUPT_FPS), "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
            "-shortest", path]
    try:
        media.run(cmd)
    except Exception as e:
        return None, f"Could not build the corrupt screen ({str(e)[:140]})."
    try:
        real_dur = media.probe(path).get("duration") or dur
    except Exception:
        real_dur = dur
    key = f"generated_video/{ctx.project_id}/glitch-{uuid.uuid4().hex[:12]}.mp4"
    try:
        storage.upload_file(path, key, "video/mp4")
    except Exception as e:
        return None, (f"The corrupt screen was built but could not be saved to "
                      f"storage ({str(e)[:140]}). Try again.")
    ctx.db.run(dbx.insert_asset, ctx.project_id, "video_clip", key,
               bytes_=os.path.getsize(path), duration_s=real_dur,
               width=ow, height=oh,
               meta={"filename": f"corrupt-{style}.mp4",
                     "caption": f"{style} corrupt-screen glitch ({dur}s)",
                     "generated": True, "model": f"local:glitch-{style}"})
    hit[ck] = key
    return key, None


def add_corrupt_screen(ctx, at_output_s, duration_s=0.6, style="digital",
                       intensity=0.7, sound=True):
    """Cut to a full-frame CORRUPT / glitch screen for a beat, then return to
    the footage — a datamosh-style transition between sections. Synthesized
    locally (no image/video generation), so it is always available and free."""
    st = (style or "digital").strip().lower()
    if st in ("glitch", "datamosh", "corruption", "digital_glitch"):
        st = "digital"
    if st in ("snow", "noise", "tv", "no_signal"):
        st = "static"
    if st not in CORRUPT_STYLES:
        return (f"REJECTED: style must be one of {', '.join(CORRUPT_STYLES)} "
                "(digital = datamosh macroblocks + tearing; vhs = tracking "
                "band + scanlines; static = TV snow / no-signal).")
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — where in the FINAL "
                "edited video the corrupt screen cuts in, in seconds.")
    try:
        dur = round(min(max(float(duration_s), CORRUPT_MIN_S),
                        CORRUPT_MAX_S), 1)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds."
    try:
        k = max(0.0, min(1.0, float(intensity)))
    except (TypeError, ValueError):
        return "REJECTED: intensity must be a number 0-1."
    snd = bool(sound)

    key, err = _corrupt_glitch_asset(ctx, st, k, dur, snd)
    if err:
        return err
    placed = insert_media(ctx, key, at, duration_s=dur)
    if not placed.startswith("EDL v"):
        return placed
    heard = ("a burst of static/hiss plays over it" if snd
             else "it is silent")
    result = placed.split(". Before:")[0]
    result += (f"\nThis is a {st} corrupt-screen glitch ({dur}s) — a full-frame "
               f"digital-corruption beat; {heard}. It is inserted media, so no "
               "spoken-word captions appear on it (nothing overlaps). Great as "
               "a punchy transition BETWEEN sections; keep it short (0.3-1s "
               "reads as a hit, longer starts to feel broken). Raise intensity "
               "for a harsher break, lower it for a subtle flicker.")
    return result


def add_title_card(ctx, text, at_output_s, duration_s=2.2, template="title",
                   bg_color="#000000", color=None, accent_color=None,
                   font=None, size_scale=None, entrance=None, exit=None,
                   subtitle=None):
    """Cut to a standalone full-frame card showing only this text, then return
    to the footage. One operation: builds the blank card, splices it into the
    program, and centres the text on it."""
    t = (text or "").strip()
    if not t:
        return "REJECTED: text is empty — a title card needs its line."
    tpl = (template or "title").strip().lower()
    if tpl not in TEXT_TEMPLATES:
        return (f"REJECTED: template must be one of "
                f"{', '.join(TEXT_TEMPLATES)}.")
    bg = (bg_color or "#000000").strip().upper()
    if not HEX_COLOR.match(bg):
        return "REJECTED: bg_color must be #RRGGBB hex, e.g. #000000."
    try:
        dur = round(min(max(float(duration_s), 0.6), 15.0), 2)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds."
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — where in the FINAL "
                "edited video the card cuts in, in seconds.")

    key, err = _solid_card_asset(ctx, bg)
    if err:
        return err
    before = {i.get("id") for i in (ctx.latest_edl()["json"].get("inserts")
                                    or [])}
    placed = insert_media(ctx, key, at, duration_s=dur)
    if not placed.startswith("EDL v"):
        return placed                      # REJECTED / failure, verbatim

    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    item = next((i for i in inserts if i.get("id") not in before), None)
    if item is None:
        return (placed + "\nThe card is placed, but its program position "
                "could not be resolved — add the text with add_text.")
    # Program window of THIS insert, resolved by id: two cards can share a
    # position, and picking by tuple made that a guess.
    windows = insert_windows(
        inserts, Timeline([list(k) for k in (edl.get("keep") or [])],
                          inserts, edl.get("speed") or []))
    win = windows.get(item["id"])
    if win is None:
        # The card IS placed; only its program window is unresolved. Say so
        # and let the agent add the text rather than crashing the tool on an
        # edit that partly succeeded.
        return (placed + "\nThe card is placed, but its program position "
                "could not be resolved — add the text with add_text.")
    card_start, card_end = win
    card_len = round(card_end - card_start, 2)

    texts = [dict(tx) for tx in (edl.get("texts") or [])]
    ts, te = card_text_window(card_start, card_end)
    made = []
    item_tx = {"id": _next_item_id(texts, "tx"), "text": t[:200],
               "start": ts, "end": te, "template": tpl,
               "x": 0.5, "y": 0.5,        # dead centre: it owns the frame
               "size_scale": float(size_scale) if size_scale is not None
               else None,
               "color": color, "accent_color": accent_color, "font": font,
               "entrance": entrance, "exit": exit,
               "uppercase": None, "box": None,
               # Bound to the card, not to program time: every later insert
               # moves this card, and the words must move with it.
               "anchor_insert": item["id"]}
    texts.append(item_tx)
    made.append(f"{tpl} \"{t[:40]}\"")
    sub = (subtitle or "").strip()
    if sub:
        texts.append({"id": _next_item_id(texts, "tx"), "text": sub[:200],
                      "start": ts, "end": te, "template": "subtitle",
                      "x": 0.5, "y": 0.635, "size_scale": None,
                      "color": color, "accent_color": accent_color,
                      "font": None, "entrance": entrance, "exit": exit,
                      "uppercase": None, "box": None,
                      "anchor_insert": item["id"]})
        made.append(f"subtitle \"{sub[:30]}\"")
    edl["texts"] = texts
    result = ctx.write_edl(
        edl, f"centred {' + '.join(made)} on the {bg} card at "
             f"{card_start}-{card_end}s [{item_tx['id']}]")
    if result.startswith("EDL v"):
        result += (f"\nThe card is a real cut in the program: the footage "
                   f"pauses for {card_len:g}s at {card_start}s and the frame "
                   "shows ONLY this text. Spoken-word captions do not appear "
                   "on it (inserted media is not transcribed), so nothing "
                   "overlaps — no caption mute is needed. Everything after "
                   f"{card_start}s shifted {card_len:g}s later.\n"
                   f"The words are BOUND to this card ({item['id']}): adding, "
                   "moving or removing any other card moves them with it "
                   "automatically, and remove_insert takes the text with it. "
                   "Place your cards in ANY order and do NOT re-place a card "
                   "because a later one shifted it — it did not come loose.")
    return result


def add_stylize(ctx, kind, start=None, end=None, intensity=None):
    """A windowed finishing effect on the program picture."""
    k = (kind or "").strip().lower()
    if k not in STYLIZE_KINDS:
        return (f"REJECTED: kind must be one of {', '.join(STYLIZE_KINDS)}.")
    if (start is None) != (end is None):
        return ("REJECTED: pass both start and end (program seconds), or "
                "neither for the whole video.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    s = e = None
    if start is not None:
        try:
            s = round(min(max(float(start), 0.0), max(0.0, prog - 0.1)), 2)
            e = round(min(max(float(end), s + 0.1), prog), 2)
        except (TypeError, ValueError):
            return "REJECTED: start/end must be numbers (program seconds)."
    inten = None
    if intensity is not None:
        try:
            inten = round(min(max(float(intensity), 0.05), 1.0), 3)
        except (TypeError, ValueError):
            return "REJECTED: intensity must be a number 0.05-1.0."
    fx = dict(edl.get("effects") or {})
    sts = [dict(sx) for sx in (fx.get("stylize") or [])]
    item = {"id": _next_item_id(sts, "st"), "kind": k, "start": s, "end": e,
            "intensity": inten}
    sts.append(item)
    fx["stylize"] = sts
    edl["effects"] = fx
    window = (f" on {s}-{e}s (program time)" if s is not None
              else " on the whole video")
    shown = inten if inten is not None else 0.5
    return ctx.write_edl(
        edl, f"stylize {k}{window}, intensity {shown:g} [{item['id']}]")


def remove_stylize(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    sts = [dict(sx) for sx in (fx.get("stylize") or [])]
    hit = next((sx for sx in sts if sx.get("id") == id), None)
    if not hit:
        have = ", ".join(sx.get("id") or "?" for sx in sts) or "none"
        return (f"REJECTED: no stylize effect with id '{id}'. Existing: "
                f"{have}. Call get_edl to see them.")
    fx["stylize"] = [sx for sx in sts if sx.get("id") != id]
    edl["effects"] = fx
    return ctx.write_edl(edl, f"removed stylize {hit['kind']} ({id})")


# (lo, hi, neutral) per custom-grade axis — the neutral value IS the absence
# of the control, so passing it clears the axis (schema normalizes the same).
_GRADE_AXES = {"exposure": (-1.0, 1.0, 0.0), "contrast": (0.5, 1.6, 1.0),
               "saturation": (0.0, 2.0, 1.0), "temperature": (-1.0, 1.0, 0.0),
               "tint": (-1.0, 1.0, 0.0)}


def set_grade_custom(ctx, exposure=None, contrast=None, saturation=None,
                     temperature=None, tint=None):
    """Continuous color controls, merged axis-by-axis into
    effects.grade_custom — None leaves an axis alone, its neutral clears it."""
    vals = {"exposure": exposure, "contrast": contrast,
            "saturation": saturation, "temperature": temperature,
            "tint": tint}
    if all(v is None for v in vals.values()):
        return ("REJECTED: pass at least one axis — exposure -1..1, "
                "contrast 0.5..1.6 (1.0 neutral), saturation 0..2 (1.0 "
                "neutral), temperature -1 (cool)..1 (warm), tint -1 "
                "(green)..1 (magenta). An axis's neutral value clears it.")
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    # model_dump stores untouched axes as explicit None — drop them so an
    # all-cleared grade reads as empty here, not as a dict of Nones.
    gc = {k: v for k, v in (fx.get("grade_custom") or {}).items()
          if v is not None}
    bits = []
    for axis, v in vals.items():
        if v is None:
            continue                      # leave that axis alone
        lo, hi, neutral = _GRADE_AXES[axis]
        try:
            fv = round(min(max(float(v), lo), hi), 3)
        except (TypeError, ValueError):
            return f"REJECTED: {axis} must be a number ({lo:g} to {hi:g})."
        if abs(fv - neutral) < 1e-6:
            gc.pop(axis, None)
            bits.append(f"{axis} cleared")
        else:
            gc[axis] = fv
            bits.append(f"{axis} {fv:+g}")
    fx["grade_custom"] = gc or None
    edl["effects"] = fx
    res = ctx.write_edl(edl, "custom grade: " + ", ".join(bits))
    if res.startswith("EDL v"):
        if not gc:
            res += "\nCustom grade fully cleared (all axes neutral)."
        elif fx.get("grade"):
            res += (f"\nNote: applied AFTER the '{fx['grade']}' preset grade "
                    "— the two compose (captions/graphics are never graded).")
    return res


def set_master_loudness(ctx, enabled):
    """Toggle -14 LUFS output mastering (edl['master'])."""
    on = bool(enabled)
    edl = dict(ctx.latest_edl()["json"])
    edl["master"] = {"loudness": "social"} if on else None
    if not on:
        return ctx.write_edl(
            edl, "master loudness removed (the mix ships at its natural "
                 "level)")
    res = ctx.write_edl(edl, "master loudness: social (-14 LUFS)")
    if res.startswith("EDL v"):
        res += ("\nThe final mix is normalized to -14 LUFS / -1.5 dBTP (the "
                "social/streaming loudness target) on PREVIEW and EXPORT — "
                "what the user approves is what ships. It changes loudness, "
                "not the balance between voice/music/sfx.")
    return res


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
    projected = model_prices.usd_to_credits(projected_usd)
    if ctx.running_credits() + projected > ctx.credit_budget:
        return (f"REJECTED: not enough credits to {what} (it costs about "
                f"{projected:.0f} credits and the balance won't cover it). Tell "
                "the user honestly they're out of credits. Do NOT promise "
                "a daily refresh — the free allowance is granted once; "
                "starting a plan is what unlocks more.")
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
    # A model that animates a still bills for the still too — quote the real
    # total, or the user is charged for something the pre-check said fit.
    projected = videogen.price_for(est_seconds)
    if not from_image_asset_key and videogen.needs_image():
        projected += config.IMAGE_PRICE_USD
    over = _gen_budget_reject(ctx, projected, "generate a video")
    if over:
        return over
    image_url = None
    seeded_note = ""
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
    elif videogen.needs_image():
        # The configured model animates a still. Rather than submitting a call
        # that cannot succeed (which is what produced two silent failures for
        # real users), paint the first frame and animate that — the capability
        # the user asked for, delivered through the pipe that actually works.
        if not llm.image_available():
            return ("Video generation here works by animating a still image, "
                    "and image generation is not configured either — so a "
                    "clip cannot be made from a text description alone. Say "
                    "that honestly and offer the alternatives: an uploaded "
                    "clip, or a generated image placed as a full-frame moment.")
        seed_path = os.path.join(ctx.workdir,
                                 f"vidseed_{len(ctx.videos_generated) + 1}.png")
        ok, ierr = llm.generate_image(p, seed_path,
                                      aspect=_default_image_aspect(ctx))
        if not ok:
            return (f"Video generation FAILED before it started: the first "
                    f"frame could not be generated ({ierr}). Do NOT claim a "
                    "clip was created.")
        seed_key = f"generated/{ctx.project_id}/{uuid.uuid4().hex[:12]}.png"
        try:
            storage.upload_file(seed_path, seed_key, "image/png")
            image_url = storage.presign_get(seed_key, expires=3600)
        except Exception as e:
            return (f"The first frame was generated but could not be staged "
                    f"for animation ({str(e)[:140]}). Try again.")
        if _log_generation(ctx, "image_gen", config.IMAGE_GEN_MODEL, p,
                           seed_key, config.IMAGE_PRICE_USD):
            ctx.gen_extra_cost_usd += config.IMAGE_PRICE_USD
        seeded_note = (" The clip was made by generating a first frame from "
                       "your description and animating it, so it is new "
                       "footage — not your original shot re-rendered.")
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
            "with look_at_asset." + seeded_note)


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


# ── Stock b-roll ─────────────────────────────────────────────────────────────

def _project_frame(ctx):
    """(orientation, width, height) of the project's OUTPUT frame.

    Both stock tools key off this: the search asks the provider for the right
    orientation, and the download picks a rendition that covers these pixels.
    Falls back to 16:9 1080p, which is what an un-set frame renders as.
    """
    try:
        edl = ctx.latest_edl()["json"]
        ratio = ((edl.get("frame") or {}).get("ratio")
                 or (edl.get("canvas") or {}).get("ratio") or "")
    except Exception:
        ratio = ""
    if ratio == "9:16":
        return "portrait", 1080, 1920
    if ratio == "1:1":
        return "square", 1080, 1080
    if ratio == "4:5":
        return "portrait", 1080, 1350
    return "landscape", 1920, 1080


def search_stock(ctx, query, kind="video", orientation=None, count=6):
    """Search the stock libraries. Returns candidates only — nothing is
    downloaded and nothing enters the edit until add_stock_media is called."""
    if not stock.available():
        return ("REJECTED: stock footage is not available on this "
                "deployment. Tell the user and offer the alternatives: they "
                "can upload their own clip, or you can generate a still.")
    q = (query or "").strip()
    if not q:
        return "REJECTED: search_stock needs a query, e.g. 'busy city street'."
    if kind not in (stock.KIND_VIDEO, stock.KIND_PHOTO):
        return "REJECTED: kind must be 'video' or 'photo'."
    if orientation is None:
        orientation = _project_frame(ctx)[0]
    elif str(orientation).strip().lower() not in ("landscape", "portrait",
                                                  "square"):
        return ("REJECTED: orientation must be landscape, portrait or square "
                "— or omit it to match the project's output frame.")
    else:
        orientation = str(orientation).strip().lower()

    try:
        hits = stock.search(q, kind=kind, orientation=orientation,
                            count=count)
    except stock.StockError as e:
        return (f"Stock search failed — {e}. Tell the user plainly and offer "
                "to use their own footage instead. Do NOT invent results.")
    except Exception as e:
        return (f"Stock search failed ({str(e)[:160]}). Do NOT invent "
                "results; tell the user it did not work.")

    if not hits:
        return (f"No stock {kind}s matched \"{q}\" ({orientation}). Try a "
                "simpler or more visual phrase (\"city traffic\" rather than "
                "\"the hustle of modern life\"), or ask the user for a clip. "
                "Do NOT claim you added anything.")

    # Cached on the ctx so add_stock_media can take an id and not re-search —
    # and, more importantly, so it downloads the EXACT result the model chose
    # rather than whatever a second identical query happens to return.
    for h in hits:
        ctx.stock_results[h["id"]] = h
    return (f"{len(hits)} stock {kind}(s) for \"{q}\" ({orientation}):\n"
            + stock.summarize(hits)
            + "\n\nNothing is downloaded or in the video yet. Pick the ONE "
              "that best matches what the user asked for and call "
              "add_stock_media(id=...). Prefer a clip whose description "
              "actually depicts the subject over one that merely shares a "
              "keyword.")


def add_stock_media(ctx, id):
    """Download a chosen search result and register it as a project asset."""
    if not stock.available():
        return "REJECTED: stock footage is not available on this deployment."
    sid = (id or "").strip()
    item = ctx.stock_results.get(sid)
    if not item:
        return ("REJECTED: unknown stock id. Call search_stock first and pass "
                "an id exactly as it appears in those results.")
    if len(ctx.stock_added) >= config.MAX_STOCK_PER_TURN:
        return (f"REJECTED: {config.MAX_STOCK_PER_TURN} stock clips already "
                "added this turn, which is the limit. Place what you have.")

    _, want_w, want_h = _project_frame(ctx)
    is_video = item.get("kind") == stock.KIND_VIDEO
    kind = url_media.KIND_VIDEO if is_video else url_media.KIND_IMAGE
    workdir = os.path.join(ctx.workdir, f"stock_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, f"stock.{'mp4' if is_video else 'jpg'}")
    try:
        stock.download(item, path, want_w, want_h)
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not download that stock clip ({str(e)[:180]}). Pick a "
                "different result or tell the user it did not work. Do NOT "
                "claim anything was added.")

    # ffprobe decides what this file actually IS — a provider's promise of an
    # mp4 is a hint, exactly as in url_media. A truncated download that still
    # wrote bytes would otherwise reach the renderer as a valid asset.
    try:
        info = media.probe(path) if is_video else None
    except Exception:
        info = None
    if is_video and not (info and info.get("width")):
        shutil.rmtree(workdir, ignore_errors=True)
        return ("That stock file downloaded but is not a readable video. Pick "
                "a different result. Do NOT claim anything was added.")

    key = url_media.storage_key(ctx.project_id, kind, path)
    try:
        storage.upload_file(path, key, url_media.content_type(path))
    except Exception as e:
        return (f"Downloaded the stock clip but could not save it "
                f"({str(e)[:160]}). Do NOT claim it was added; try again.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    dur = (info or {}).get("duration") or item.get("duration_s")
    w = (info or {}).get("width") or item.get("picked_width") or item.get("width")
    h = (info or {}).get("height") or item.get("picked_height") or item.get("height")
    desc = (item.get("description") or "stock clip")[:60]
    fname = f"{desc}.{'mp4' if is_video else 'jpg'}"
    ctx.db.run(dbx.insert_asset, ctx.project_id, kind, key,
               bytes_=None, duration_s=dur, width=w, height=h,
               fps=(info or {}).get("fps"),
               meta={"filename": fname, "stock": True,
                     "provider": item.get("provider"),
                     "stock_id": sid,
                     "credit": item.get("credit"),
                     "page_url": item.get("page_url"),
                     "source_url": item.get("source_url"),
                     "description": item.get("description")})
    ctx.stock_added.append({"storage_key": key, "id": sid,
                            "provider": item.get("provider")})

    bits = []
    if w and h:
        bits.append(f"{w}x{h}")
    if dur:
        bits.append(f"{float(dur):.0f}s")
    if item.get("credit"):
        bits.append(f"by {item['credit']} / {item.get('provider')}")
    detail = f" ({', '.join(bits)})" if bits else ""
    place = ("add_overlay(fit='cover') to cut away to it while the speech "
             "keeps running, or insert_media to splice it in and add time"
             if is_video else
             "insert_media to hold it on screen, or add_overlay(fit='cover')")
    return (f"Added stock {'clip' if is_video else 'image'} \"{desc}\""
            f"{detail} to the project: storage_key={key}. It is SILENT and "
            f"NOT in the video yet — place it with {place}. Cover overlays of "
            "2-6s read best; start it on the words that mention the subject.")


def _capture_precheck(ctx, url, orientation):
    """Everything a capture must settle before a browser is worth starting.
    Returns (url, orientation, None) or (None, None, rejection)."""
    if not webrecord.available():
        return None, None, (
            "REJECTED: website recording is not available on this "
            "deployment. Offer the user the alternative: they can screen-"
            "record the page themselves and upload the file.")
    url = _clean_url(url)
    if not url:
        return None, None, "REJECTED: a url is required."
    # Runaway backstop, same contract as MAX_FETCHED_URLS_PER_TURN: each
    # capture is individually wall-clock-bounded; this only stops a loop.
    if len(ctx.web_recordings) >= 3:
        return None, None, (
            "REJECTED: 3 pages already recorded this turn, which is the "
            "limit. Place what you have, or ask the user to continue in "
            "another message.")
    # Default the viewport to the shape the capture will LAND in — the
    # project's output frame — so a 9:16 edit gets a phone-shaped page
    # capture instead of a squashed desktop one.
    if orientation is None:
        edl = ctx.latest_edl()["json"]
        ratio = ((edl.get("frame") or {}).get("ratio")
                 or (edl.get("canvas") or {}).get("ratio") or "")
        orientation = ("portrait" if ratio == "9:16"
                       else "square" if ratio == "1:1" else "landscape")
    elif str(orientation).strip().lower() not in ("landscape", "portrait",
                                                  "square"):
        return None, None, (
            "REJECTED: orientation must be landscape, portrait or "
            "square — or omit it to match the project's output frame.")
    else:
        orientation = str(orientation).strip().lower()
    return url, orientation, None


def _store_capture(ctx, url, got, kind_word):
    """Upload a finished capture, register the asset, remember the event
    track ON THE ASSET. Returns (storage_key, name, None) or (None, None,
    failure text).

    The events are written into the asset's meta, not just onto ctx: a
    recording made this turn is very often placed in the NEXT one ("actually,
    put the demo at the start"), and a track that only lived in turn memory
    would be gone exactly when showcase_demo needed it.
    """
    path = got["path"]
    key = url_media.storage_key(ctx.project_id, url_media.KIND_VIDEO, path)
    try:
        storage.upload_file(path, key, url_media.content_type(path))
    except Exception as e:
        return None, None, (
            f"Recorded the page but could not save the capture "
            f"({str(e)[:160]}). Do NOT claim it was added; try again.")

    from urllib.parse import urlparse as _up
    domain = (_up(got.get("final_url") or url).hostname or "site")
    fname = f"{domain} {kind_word}.mp4"
    ctx.db.run(dbx.insert_asset, ctx.project_id, url_media.KIND_VIDEO, key,
               bytes_=None, duration_s=got.get("duration_s"),
               width=got.get("width"), height=got.get("height"),
               fps=30.0,
               meta={"filename": fname, "recorded": True,
                     "source_url": got.get("final_url") or url,
                     "page_title": got.get("page_title"),
                     "demo_events": got.get("events") or []})
    ctx.web_recordings.append({"storage_key": key, "url": url,
                               "events": got.get("events") or []})
    return key, (got.get("page_title") or domain), None


def record_website(ctx, url, duration_s=None, orientation=None, scroll=True):
    """Record a scrolling screen capture of a live web page (headless
    browser) and register it as a project video asset."""
    url, orientation, rej = _capture_precheck(ctx, url, orientation)
    if rej:
        return rej
    try:
        dur = float(duration_s) if duration_s is not None else 12.0
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds (4-30)."

    workdir = os.path.join(ctx.workdir, f"webrec_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        got = webrecord.record(url, workdir, duration_s=dur,
                               orientation=orientation,
                               scroll=bool(scroll))
    except webrecord.WebRecordError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not record that page — {e}. Tell the user plainly "
                "and offer the alternative (they screen-record it and "
                "upload). Do NOT claim anything was recorded or added.")
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not record that page ({str(e)[:200]}). Tell the "
                "user it did not work. Do NOT claim anything was added.")

    try:
        key, name, fail = _store_capture(ctx, url, got, "capture")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if fail:
        return fail
    return (f"Recorded \"{name}\" — "
            f"{got['duration_s']:.1f}s at {got['width']}x{got['height']} "
            f"(the page loads, holds, then smooth-scrolls to the bottom): "
            f"storage_key={key}. It is saved to the project but NOT in the "
            "video yet — splice it with insert_media, or lay it over the "
            "footage with add_overlay (fit='cover' for a full-frame "
            "cutaway while the speech continues). The capture is SILENT.")


def record_website_demo(ctx, url, steps, orientation=None):
    """Drive a live web page through a scripted walkthrough with a visible
    cursor, record it, and register it as a project video asset."""
    url, orientation, rej = _capture_precheck(ctx, url, orientation)
    if rej:
        return rej

    workdir = os.path.join(ctx.workdir, f"webdemo_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        got = webrecord.record_demo(url, workdir, steps,
                                    orientation=orientation)
    except webrecord.WebRecordError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not record that walkthrough — {e}. Tell the user "
                "plainly what went wrong. Do NOT claim anything was recorded "
                "or added.")
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not record that walkthrough ({str(e)[:200]}). Tell "
                "the user it did not work. Do NOT claim anything was added.")

    try:
        key, name, fail = _store_capture(ctx, url, got, "demo")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if fail:
        return fail

    events = got.get("events") or []
    clicks = sum(1 for e in events if e["kind"] == "click")
    problems = got.get("problems") or []
    # Every problem is reported verbatim. A demo where two of six clicks
    # missed is still a usable recording, but the user has to be told which
    # two — a summary that says "recorded the walkthrough" and nothing else
    # is the lie this project keeps having to unpick.
    trouble = ""
    if problems:
        trouble = ("\nWHAT DID NOT WORK (tell the user, do not hide it): "
                   + "; ".join(problems[:6]) + ".")
    return (
        f"Recorded a walkthrough of \"{name}\" — "
        f"{got['duration_s']:.1f}s at {got['width']}x{got['height']}, "
        f"{clicks} click(s), with the cursor visible on screen: "
        f"storage_key={key}.\n"
        f"EVENT TRACK (seconds into THIS capture, positions as 0-1 fractions "
        f"of its frame): {webrecord.summarize_events(events) or '(none)'}\n"
        "It is saved to the project but NOT in the video yet. The one-call "
        "way to place it is showcase_demo(asset_key) — that splices it in "
        "and uses the track above to glide a zoom onto each click and land a "
        "click sound on the exact frame. Place it by hand with insert_media "
        "only if the user wants something other than that. The capture is "
        "SILENT." + trouble)


# ---------------------------------------------------------------- #
#  Turning a demo capture into an edit                               #
# ---------------------------------------------------------------- #

# A click every few seconds is a demo; a click every half second is a form
# being filled, and zooming at each one would be a strobe. Runs are broken
# when the gap exceeds this, and when the page NAVIGATES — a new page has to
# be seen whole before it is worth pushing into.
DEMO_RUN_GAP_S = 4.5
DEMO_MAX_ZOOMS = 8
DEMO_MAX_SFX = 16
# How far ahead of a click the push starts and how long it holds after the
# last one. Arriving with the pointer and leaving a beat after the result is
# what makes a demo readable rather than frantic.
DEMO_ZOOM_LEAD_S = 0.55
DEMO_ZOOM_TAIL_S = 1.1


def _capture_point_mapper(ctx, edl, asset):
    """Map a point in the CAPTURE's frame to a point in the OUTPUT frame.

    A capture recorded at the project's orientation is the same shape as the
    output and this is the identity. It is not always: the user can reframe
    the project after recording, or ask for a landscape capture in a vertical
    edit. The renderer center-crops (or pads) inserts to fit, and a zoom
    aimed with unmapped coordinates would then miss the button by exactly the
    bars — visibly, and in the one shot where precision is the whole point.
    """
    _orient, ow, oh = _project_frame(ctx)
    aw = float(asset.get("width") or 0)
    ah = float(asset.get("height") or 0)
    mode = ((edl.get("frame") or {}).get("mode") or "crop")
    if not (aw > 0 and ah > 0 and ow and oh):
        return lambda x, y: (x, y)
    pick = min if mode in ("pad", "pad_blur") else max
    scale = pick(ow / aw, oh / ah)
    dw, dh = aw * scale, ah * scale
    ox, oy = (ow - dw) / 2.0, (oh - dh) / 2.0

    def mapper(x, y):
        return (min(max((ox + x * dw) / ow, 0.0), 1.0),
                min(max((oy + y * dh) / oh, 0.0), 1.0))
    return mapper


def _demo_zoom_runs(clicks):
    """Group clicks into the runs one continuous zoom should cover."""
    runs, current = [], []
    for ev in clicks:
        if current and (ev["t"] - current[-1]["t"]) > DEMO_RUN_GAP_S:
            runs.append(current)
            current = []
        current.append(ev)
    if current:
        runs.append(current)
    return runs


def showcase_demo(ctx, asset_key, at_output_s=None, zoom_strength=0.4,
                  click_sounds=True, zooms=True):
    """Splice a recorded website demo in and cut it like a product video:
    one gliding zoom per run of clicks, a click sound on each click."""
    asset, err = _resolve_media_asset(ctx, asset_key, ("video_clip",))
    if err:
        return err
    meta = asset.get("meta") or {}
    events = meta.get("demo_events") or []
    if not events:
        # Fall back to this turn's memory before giving up — an asset written
        # before demo_events existed still has its track in ctx.
        for rec in ctx.web_recordings:
            if rec.get("storage_key") == asset_key:
                events = rec.get("events") or []
                break
    if not events:
        return ("REJECTED: that asset has no demo event track, so there is "
                "nothing to sync to. showcase_demo only works on a capture "
                "made by record_website_demo. Place this one with "
                "insert_media and add zooms yourself.")
    try:
        strength = round(min(max(float(zoom_strength), ZOOM_STRENGTH_MIN),
                             ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return "REJECTED: zoom_strength must be a number (0.05-1.5)."

    edl0 = ctx.latest_edl()["json"]
    prog_before = program_duration(edl0)
    at = prog_before if at_output_s is None else at_output_s
    try:
        at = float(at)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — where in the FINAL "
                "edited video the demo goes, in seconds. Omit it to append "
                "the demo at the end.")

    clip_dur = _asset_media_duration(ctx, asset)
    before = {i.get("id") for i in (edl0.get("inserts") or [])}
    placed = insert_media(ctx, asset_key, at, duration_s=clip_dur)
    if not placed.startswith("EDL v"):
        return placed                      # REJECTED / failure, verbatim

    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    item = next((i for i in inserts if i.get("id") not in before), None)
    windows = insert_windows(
        inserts, Timeline([list(k) for k in (edl.get("keep") or [])],
                          inserts, edl.get("speed") or [])) if item else {}
    win = windows.get((item or {}).get("id"))
    if win is None:
        return (placed + "\nThe demo is placed, but its position in the "
                "program could not be resolved, so no zooms or sounds were "
                "synced. Add them with add_zoom / add_sfx.")
    base, demo_end = win
    prog = program_duration(edl)
    to_frame = _capture_point_mapper(ctx, edl, asset)

    demo_len = max(0.0, demo_end - base)

    def prog_t(t):
        """Capture time -> program time, clamped inside the demo's window."""
        return round(min(max(base + float(t), base), demo_end - 0.02), 2)

    def inside(ev):
        """Events past the end of the placed window are DROPPED, not clamped.
        Clamping would stack every late click's sound on the final frame —
        a burst of noise at the cut, which reads as a bug rather than as the
        truncation it actually is."""
        return float(ev.get("t", 0.0)) <= demo_len + 0.05

    dropped_late = sum(1 for e in events if not inside(e))
    events = [e for e in events if inside(e)]
    if dropped_late:
        notes_late = (f"{dropped_late} event(s) fell past the end of the "
                      "placed clip and were not synced")
    else:
        notes_late = ""

    fx = dict(edl.get("effects") or {})
    zlist = [dict(z) for z in (fx.get("zooms") or [])]
    sfx_items = [dict(s) for s in (edl.get("sfx") or [])]
    made_zooms, made_sfx = 0, 0
    notes = [notes_late] if notes_late else []

    clicks = [e for e in events if e.get("kind") == "click"
              and "x" in e and "y" in e]
    if zooms and clicks:
        # A navigation resets the run: the viewer needs the whole new page
        # before being pushed into a corner of it.
        navs = [e["t"] for e in events if e.get("kind") == "nav"]
        runs = []
        for run in _demo_zoom_runs(clicks):
            split, current = [], []
            for ev in run:
                if current and any(current[-1]["t"] < n <= ev["t"]
                                   for n in navs):
                    split.append(current)
                    current = []
                current.append(ev)
            if current:
                split.append(current)
            runs.extend(split)
        for run in runs:
            if made_zooms >= DEMO_MAX_ZOOMS:
                notes.append(
                    f"stopped at {DEMO_MAX_ZOOMS} zooms — the rest of the "
                    "clicks still have their sounds")
                break
            s = prog_t(run[0]["t"] - DEMO_ZOOM_LEAD_S)
            e = prog_t(run[-1]["t"] + DEMO_ZOOM_TAIL_S)
            if e - s < 0.45:
                continue
            # Non-overlapping by construction: two zoom windows that touch
            # would step the centre while both are pushed in, which reads as
            # a jump cut inside a move.
            if zlist and zlist[-1].get("end", 0) > s - 0.15:
                s = round(zlist[-1]["end"] + 0.15, 2)
                if e - s < 0.45:
                    continue
            span = e - s
            pts = []
            for ev in run:
                cx, cy = to_frame(ev["x"], ev["y"])
                f = min(max((prog_t(ev["t"]) - s) / span, 0.0), 1.0)
                if pts and f - pts[-1]["f"] < 0.02:
                    continue          # same instant: one waypoint is enough
                pts.append({"f": round(f, 4), "cx": round(cx, 3),
                            "cy": round(cy, 3)})
            if not pts:
                continue
            # Hold the first and last positions to the window edges so the
            # push-in starts and the pull-out ends ON the thing being shown.
            head = dict(pts[0]); head["f"] = 0.0
            tail = dict(pts[-1]); tail["f"] = 1.0
            pts = [head] + [p for p in pts if 0.0 < p["f"] < 1.0] + [tail]
            item_z = {"id": _next_item_id(zlist, "zm"), "start": s, "end": e,
                      "strength": strength}
            if len(pts) >= 2:
                item_z["mode"] = "follow"
                item_z["path"] = pts
            else:
                item_z["mode"] = "ease"
                item_z["cx"], item_z["cy"] = pts[0]["cx"], pts[0]["cy"]
            zlist.append(item_z)
            made_zooms += 1

    if click_sounds:
        for ev in events:
            if made_sfx >= DEMO_MAX_SFX:
                notes.append(f"stopped at {DEMO_MAX_SFX} sounds")
                break
            kind = ev.get("kind")
            if kind == "click":
                slug, gain = "click", -13.0
            elif kind == "nav":
                slug, gain = "pop", -16.0
            elif kind == "scroll":
                slug, gain = "swipe", -20.0
            else:
                continue
            key = sfx_library.ref(slug)
            sound, serr = _resolve_sfx(ctx, key)
            if serr:
                continue           # a pack without this sound: skip silently
            t = prog_t(ev["t"])
            if t > max(0.0, prog - 0.05):
                continue
            taken = {s.get("id") for s in sfx_items}
            n = 1
            while f"sx{n}" in taken:
                n += 1
            sfx_items.append({"id": f"sx{n}", "storage_key": key,
                              "at": t, "gain_db": gain})
            made_sfx += 1

    fx["zooms"] = zlist
    edl["effects"] = fx
    edl["sfx"] = sfx_items
    written = ctx.write_edl(
        edl, f"cut the demo like a product video: {made_zooms} gliding "
             f"zoom(s) onto the clicks and {made_sfx} synced sound(s) across "
             f"{base}-{demo_end}s")
    extra = ("\nNOTE: " + "; ".join(notes) + "." if notes else "")
    return (placed + "\n" + written + extra
            + f"\nThe demo runs {base}-{demo_end}s in the edit. Tell the user "
            "what it shows, and that the clicks are zoomed and sounded. "
            "Adjust any single zoom with remove_zoom / add_zoom.")


# ------------------------------------------------------------------ #
#  META tools                                                          #
# ------------------------------------------------------------------ #

def get_edl(ctx):
    row = ctx.latest_edl()
    # 20000 chars (was 8000): the EDL now carries overlays/texts/speed/
    # stylize too, and the old cap silently amputated exactly the
    # collections a v2 edit needs to see. The explicit budget matters —
    # _cap's default (TOOL_OUTPUT_CHAR_BUDGET) would undo the raise.
    return _cap(f"EDL v{row['version']} "
                f"({describe_edl(row['json'], ctx.duration)}):\n"
                + json.dumps(row["json"], indent=1)[:20000], budget=20500)


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
                                    row["json"].get("inserts") or [],
                                    row["json"].get("speed") or [])
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
                tl = Timeline(edl["keep"], edl.get("inserts") or [],
                              edl.get("speed") or [])
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
        edl = ctx.latest_edl()["json"]
        frame_note = _frame_context(edl)
        fx_note = _deliberate_fx_note(edl)
    except Exception:
        frame_note = fx_note = ""
    return llm.ask_vision(
        frame_note + fx_note +
        "This is a 3x3 contact sheet sampled evenly from an automatically "
        "edited video. In one or two sentences: does anything look broken "
        "(unexpected black frames, half-cut faces mid-action, missing "
        "captions if text was expected)? Frames showing a DELIBERATE effect "
        "listed above are expected, not defects — but say so plainly if one "
        "of them looks OVERDONE for this footage (harsh, cheap, or so strong "
        "the shot underneath is lost). If it looks fine, say 'looks clean'.",
        [local], max_tokens=200, purpose="vision_selfcheck",
        image_names=[sheet_key])


def _deliberate_fx_note(edl):
    """One line naming the full-frame effects THIS edit applied on purpose.

    Without it the reviewer reads its own edit as damage: a 0.5s corrupt
    screen came back as "0:05 shows heavy colour glitch/artifacts" and a
    flash transition as "0:04 is a washed-out/white frame" — true, alarming,
    and both intentional. Naming them turns the check from a false alarm into
    the useful question: is this effect too much for this footage?
    """
    fx = edl.get("effects") or {}
    bits = []
    kinds = sorted({(s.get("kind") or "") for s in (fx.get("stylize") or [])}
                   & {"flash", "chromatic", "vhs", "glitch", "shake",
                      "dream_blur", "glow"})
    if kinds:
        bits.append(", ".join(kinds) + " stylize passes")
    style = ((fx.get("transition") or {}).get("style") or "")
    if style in ("flash", "glitch", "whip_left", "whip_right", "zoom_punch"):
        bits.append(f"'{style}' transitions at every cut")
    # Inserts carry only asset_key — the synthesized ones are named by the
    # tool that built them (generated_video/<pid>/glitch-*.mp4,
    # generated/<pid>/card-*.png), which is what makes them recognisable here.
    keys = [str(i.get("asset_key") or "") for i in (edl.get("inserts") or [])]
    if any("glitch-" in k for k in keys):
        bits.append("a corrupt-screen glitch insert")
    if any("/card-" in k for k in keys):
        bits.append("a full-frame colour card")
    if not bits:
        return ""
    return ("Deliberate effects in this edit: " + "; ".join(bits) + ". ")


def ask_user(ctx, question):
    q = (question or "").strip()
    if not q:
        return "REJECTED: question is empty."
    raise AskUser(q[:600])


# ------------------------------------------------------------------ #
#  Perception + director tools (round 35)                              #
# ------------------------------------------------------------------ #
# Perception feeds DECISIONS, never renders: these tools read the measured
# beat grid / energy envelope / word stress and write CONCRETE timestamps
# into the EDL. The renderer never consults perception, so a render stays
# reproducible from (EDL version, source sha, index words) alone.

def _get_perception(ctx):
    """The main video's perception sidecar (beats/energy/stress), cached on
    the ctx after the first call and persisted on the index row by
    perception.get_or_compute_for_index — the first call streams the proxy's
    audio once; every later call (this turn or any future one) is a read."""
    if ctx._perception is not None:
        return ctx._perception
    original = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
    if not original or not original.get("sha256"):
        raise perception.PerceptionError("no indexed main video")
    index_row = ctx.db.run(dbx.get_index_by_sha, original["sha256"])
    if not index_row:
        raise perception.PerceptionError("no index row for this video")
    p = perception.get_or_compute_for_index(ctx.db, dbx, index_row,
                                            ctx.proxy_path())
    ctx._perception = p
    return p


def _describe_tempo(p):
    bpm, conf = p.get("bpm"), float(p.get("bpm_conf") or 0.0)
    if not bpm:
        return (f"tempo: no detectable musical pulse (confidence "
                f"{conf:.2f}) — do not beat-sync anything to this audio.")
    verdict = ("reliable" if conf >= 0.7 else
               "usable" if conf >= 0.5 else
               "LOW — the pulse is weak; beat_align_cuts will refuse")
    return f"tempo: {bpm:g} BPM (confidence {conf:.2f} — {verdict})"


def _describe_beats(p):
    beats = p.get("beats") or []
    if not beats:
        return "beats: none detected."
    head = ", ".join(f"{b:g}" for b in beats[:8])
    return f"beats: {len(beats)} on the grid; first: {head}s"


def _largest_energy_rise(p, window_s=6.0, min_db=6.0):
    """(rise_end_t, rise_db) — the biggest energy climb within a rolling
    window, reported at the moment the climb PEAKS (a riser should resolve
    exactly there). None when the track never climbs min_db."""
    energy = p.get("energy") or []
    bin_s = float(p.get("energy_bin_s") or 0.5)
    if not energy:
        return None
    win = max(1, int(round(window_s / bin_s)))
    best_i, best_rise = None, min_db
    for i in range(len(energy)):
        low = min(energy[max(0, i - win):i + 1])
        rise = energy[i] - low
        if rise > best_rise:
            best_i, best_rise = i, rise
    if best_i is None:
        return None
    return round((best_i + 0.5) * bin_s, 2), round(best_rise, 1)


def _describe_energy(p):
    energy = p.get("energy") or []
    bin_s = float(p.get("energy_bin_s") or 0.5)
    if not energy:
        return "energy: no envelope."
    loud_i = max(range(len(energy)), key=lambda i: energy[i])
    quiet_i = min(range(len(energy)), key=lambda i: energy[i])
    line = (f"energy: loudest around {round((loud_i + 0.5) * bin_s, 1)}s "
            f"(the 0dB peak), quietest around "
            f"{round((quiet_i + 0.5) * bin_s, 1)}s "
            f"({energy[quiet_i]:g}dB below peak)")
    rise = _largest_energy_rise(p)
    if rise:
        line += f"; biggest rise: +{rise[1]:g}dB climbing into {rise[0]:g}s"
    return line + _flatline_note(p)


def _flatline_note(p):
    """'This file is not really music' — said plainly, when the measurement
    says so.

    A track whose whole body sits on a dead-flat level tens of dB under one
    brief burst has a noise floor, not dynamics. It happened to a real user on
    Jul 26 2026: a 2-minute mp3 from a link converter decoded as one 1-second
    blast at 2.2s and 117s of flat -67dB. Every tool then reported perfectly
    true, perfectly useless facts ("beats: none detected") and the agent had
    no way to know the FILE was the problem, so it kept trying to work with
    it. Naming it is the difference between a dead end and a 10-second fix."""
    energy = p.get("energy") or []
    if len(energy) < 20:
        return ""
    body = sorted(energy)[:int(len(energy) * 0.98)]      # drop the top 2%
    if not body:
        return ""
    mid = body[len(body) // 2]
    spread = body[int(len(body) * 0.9)] - body[int(len(body) * 0.1)]
    if mid < -40.0 and spread < 6.0:
        return (f". WARNING — this file is almost certainly BROKEN, not just "
                f"quiet: everything except one brief burst sits on a flat "
                f"level {abs(mid):.0f}dB below it (only {spread:.1f}dB of "
                f"variation across the whole file). Real music never looks "
                f"like this. Tell the user the audio file did not convert "
                f"properly and ask them to re-upload it (or offer a built-in "
                f"track) — do NOT try to beat-match or score against it")
    return ""


def _asset_audio_analysis(ctx, asset_key):
    """Tempo/beats/energy for a music reference — a project upload (cached
    on the asset's meta) or a bundled library track (cached per turn)."""
    if music_library.is_library_ref(asset_key):
        t = music_library.resolve(asset_key)
        if not t:
            return (f"REJECTED: '{asset_key}' is not a track in the built-in "
                    "library. Call list_music_library() and use a slug it "
                    "returns.")
        p = ctx._asset_perception.get(asset_key)
        if p is None:
            try:
                p = perception.analyze_audio(
                    music_library.local_path(asset_key))
            except Exception as e:
                return (f"Audio analysis failed for that track "
                        f"({str(e)[:160]}).")
            ctx._asset_perception[asset_key] = p
        name = t["title"]
    else:
        asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, asset_key)
        # A VIDEO the user wants the song from is analyzed like any other
        # track — beat-aligning cuts to it is the whole point of asking.
        if asset and asset["kind"] == "video_clip":
            asset, _note, err = _audio_from_clip(ctx, asset)
            if err:
                return err
            asset_key = asset["storage_key"]
        if not asset or asset["kind"] != "music":
            return (f"REJECTED: '{asset_key}' is not a music asset in this "
                    "project. Analyze uploads from list_assets(kind='music') "
                    "or a library: reference.")
        p = ctx._asset_perception.get(asset_key)
        if p is None:
            try:
                local = _asset_local_path(ctx, asset)
                p = perception.get_or_compute_for_asset(ctx.db, dbx, asset,
                                                        local)
            except Exception as e:
                return (f"Audio analysis failed for that file "
                        f"({str(e)[:160]}).")
            ctx._asset_perception[asset_key] = p
        name = (asset.get("meta") or {}).get("filename") or \
            os.path.basename(asset_key)
    out = (f"Audio analysis of '{name}' (times are seconds INTO THE "
           "TRACK — e.g. an add_music offset_s to start on the drop):\n"
           "- " + "\n- ".join([_describe_tempo(p), _describe_beats(p),
                               _describe_energy(p)])
           + "\n(word-stress analysis applies to the main video only)")
    # Track seconds are useless for PLACING anything — every write tool takes
    # program seconds. When this track is already in the edit, hand over the
    # grid in the timeline's own units so the agent can cut/hit on it without
    # doing offset+loop arithmetic in its head (and getting it wrong).
    edl = ctx.latest_edl()["json"]
    if any(m.get("storage_key") == asset_key for m in (edl.get("music") or [])):
        prog_beats, label, err = _music_program_beats(ctx, edl, key=asset_key)
        if not err and prog_beats:
            shown = ", ".join(f"{b:g}" for b in prog_beats[:48])
            out += (f"\n- IN PROGRAM TIME (what every write tool takes) this "
                    f"track's beats land at: {shown}"
                    + (f" … {len(prog_beats)} in total"
                       if len(prog_beats) > 48 else "")
                    + f". Grid: {label}. Use these for keep_segments, "
                      "add_sfx or add_zoom; beat_align_cuts snaps existing "
                      "cuts to them for you.")
    return _cap(out)


def get_audio_analysis(ctx, asset_key=None):
    """READ: the measured musical/energy structure of the source audio (or
    of a music asset when asset_key is passed)."""
    if asset_key:
        return _asset_audio_analysis(ctx, asset_key)
    if not ctx.has_main_video:
        return ("REJECTED: there is no main video to analyze on this "
                "image/clip-only program. Pass asset_key to analyze an "
                "uploaded music file or a library: track instead.")
    try:
        p = _get_perception(ctx)
    except Exception as e:
        return (f"Audio analysis unavailable for this video "
                f"({str(e)[:160]}). Decide from the transcript, silences "
                "and shot captions instead.")
    lines = [_describe_tempo(p), _describe_beats(p), _describe_energy(p)]
    words = ctx.index.get("words") or []
    if words:
        idxs = perception.top_stressed_words(p, words, count=8)
        scores = perception.word_stress(p, words)
        if idxs:
            lines.append(
                "top stressed words (measured vocal emphasis): "
                + ", ".join(f"'{words[i]['w']}' @{words[i]['t0']:.2f}s "
                            f"({scores[i]:.2f})" for i in idxs))
        else:
            lines.append("no clearly stressed words found.")
        cov = perception.stress_coverage_s(p)
        if any(float(w["t0"]) >= cov for w in words):
            lines.append(
                f"NOTE: stress analysis covers the first {cov / 60:.0f} "
                "minutes only — words after that carry NO measured stress "
                "(they are excluded above, not scored low).")
    else:
        lines.append("no transcript — word-stress analysis n/a.")
    return _cap("Audio analysis of the SOURCE (all times are SOURCE "
                "seconds — program positions shift with the cut):\n- "
                + "\n- ".join(lines))


def punch_in_on_emphasis(ctx, count=4, strength=0.35):
    """Punch zooms on the most vocally stressed KEPT words, in ONE version.
    Every timestamp is a real word time mapped through the current cut —
    nothing is estimated."""
    if not ctx.has_main_video:
        return ("REJECTED: needs the main video — an image/clip-only "
                "program has no speech to find emphasis in.")
    words = ctx.index.get("words") or []
    if not words:
        return ("REJECTED: this video has no transcript, so there are no "
                "stressed words to punch in on. Place zooms by hand with "
                "add_zoom instead.")
    try:
        n = min(max(int(count), 1), 8)
    except (TypeError, ValueError):
        return "REJECTED: count must be an integer (1-8)."
    try:
        st = round(min(max(float(strength), ZOOM_STRENGTH_MIN),
                       ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return "REJECTED: strength must be a number (0.05-1.5)."
    try:
        p = _get_perception(ctx)
    except Exception as e:
        return (f"Audio analysis unavailable ({str(e)[:160]}) — place zooms "
                "by hand with add_zoom instead.")
    edl = dict(ctx.latest_edl()["json"])
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    prog = round(tl.out_duration, 2)
    scores = perception.word_stress(p, words)
    picked = []          # (word, program_t0)
    for i in sorted(range(len(words)), key=lambda k: -scores[k]):
        if len(picked) >= n:
            break
        w = words[i]
        if len((w["w"] or "").strip("\"'.,!?;:")) < 3:
            continue                     # tiny function words aren't emphasis
        mid = (float(w["t0"]) + float(w["t1"])) / 2.0
        if tl.src_to_out(mid) is None:
            continue                     # the word is cut — skip it
        pt = tl.src_to_out(float(w["t0"]))
        if pt is None:
            pt = tl.src_to_out(mid)
        pt = round(pt, 2)
        if any(abs(pt - q[1]) < 4.0 for q in picked):
            continue                     # spaced >= 4s apart in program time
        picked.append((w, pt))
    if not picked:
        return ("No stressed words survive the current cut with 4s spacing "
                "— nothing was written. Place zooms by hand with add_zoom "
                "if you still want them. Do NOT tell the user zooms were "
                "added.")
    picked.sort(key=lambda q: q[1])
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    placed = []
    for w, pt in picked:
        # 60ms early so the punch lands ON the word's attack, not after it.
        s = round(max(0.0, pt - 0.06), 2)
        e = round(min(prog, s + 0.9), 2)
        if e - s < 0.2:
            continue                     # the word sits at the very end
        item = {"id": _next_item_id(zooms, "zm"), "start": s, "end": e,
                "strength": st}
        zooms.append(item)
        placed.append((w, pt, item["id"]))
    if not placed:
        return ("No placeable emphasis moments — the stressed words all sit "
                "at the very end of the program. Nothing was written.")
    fx["zooms"] = zooms
    edl["effects"] = fx
    res = ctx.write_edl(
        edl, f"{len(placed)} punch zoom(s) ({int(st * 100)}%) on the most "
             "vocally stressed words")
    if res.startswith("EDL v"):
        res += ("\nPunch-ins (program time, from measured vocal stress):\n"
                + "\n".join(f"  '{w['w']}' @ {pt}s [{zid}]"
                            for w, pt, zid in placed))
    return res


def _pick_sfx(category, tag=None):
    """Deterministic pick from the bundled pack: the alphabetically-first
    sound in the category (tag matches first when a tag is given) — the same
    inputs always place the same sound, so re-running a pass is a NO CHANGE,
    not a reshuffle."""
    hits = [t for t in sfx_library.CATALOG if t.get("category") == category]
    if tag:
        tagged = [t for t in hits if tag in (t.get("tags") or ())]
        hits = tagged or hits
    hits = sorted(hits, key=lambda t: t["slug"])
    return hits[0] if hits else None


def sound_design_pass(ctx, intensity="medium"):
    """Deterministic sound design in ONE version: whooshes on cut junctions,
    one impact on the strongest stressed word, one riser resolving into the
    biggest energy rise — all from the built-in pack, all disclosed."""
    if not sfx_library.CATALOG:
        return ("REJECTED: this deployment ships no built-in sound pack, so "
                "there is nothing to place. Sounds the user uploads can "
                "still be placed by hand with add_sfx.")
    if not ctx.has_main_video:
        return ("REJECTED: the sound-design pass reads the main video's cut "
                "junctions and audio analysis — on an image/clip-only "
                "program place sounds by hand with add_sfx.")
    budgets = {"light": 2, "medium": 4, "strong": 6}
    level = (intensity or "medium").strip().lower()
    if level not in budgets:
        return ("REJECTED: intensity must be light (2 placements), medium "
                "(4) or strong (6).")
    budget = budgets[level]
    edl = dict(ctx.latest_edl()["json"])
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    prog = round(tl.out_duration, 2)
    if prog < 3.0:
        return "REJECTED: the program is too short for a sound-design pass."
    existing = [float(sx["at"]) for sx in (edl.get("sfx") or [])]
    placements = []      # (sound, at, why)

    def _clear(at):
        # never stack within 1.5s of an existing sound or another placement
        return (all(abs(at - x) >= 1.5 for x in existing)
                and all(abs(at - q[1]) >= 1.5 for q in placements))

    p = None
    try:
        p = _get_perception(ctx)
    except Exception:
        pass                # junction whooshes still work without analysis

    # ONE impact on THE strongest stressed surviving word — if that exact
    # moment already carries a sound there is no impact this pass. Walking
    # down the list instead would make every re-run invent a new hit on a
    # progressively weaker word, which is a reshuffle, not idempotence.
    words = ctx.index.get("words") or []
    impact = _pick_sfx("impact")
    if p is not None and words and impact and len(placements) < budget:
        scores = perception.word_stress(p, words)
        for i in sorted(range(len(words)), key=lambda k: -scores[k]):
            w = words[i]
            if len((w["w"] or "").strip("\"'.,!?;:")) < 3:
                continue
            pt = tl.src_to_out(float(w["t0"]))
            if pt is None or pt > prog - 0.2:
                continue
            if _clear(round(pt, 2)):
                placements.append(
                    (impact, round(pt, 2),
                     f"impact on the stressed word '{w['w']}'"))
            break
    # One riser ending exactly INTO the largest energy rise.
    riser = _pick_sfx("riser")
    if p is not None and riser and len(placements) < budget:
        rise = _largest_energy_rise(p)
        if rise:
            rt = tl.src_to_out(rise[0])
            rdur = float(riser.get("duration_s") or 2.0)
            # The riser must FIT before the rise (rt >= its length), or the
            # start clamps to 0 and the sound resolves at rdur — later than
            # the rise the disclosure line claims. When it doesn't fit,
            # place nothing rather than describe a moment that isn't real.
            if rt is not None and rt >= rdur:
                at = round(rt - rdur, 2)
                if _clear(at):
                    placements.append(
                        (riser, at, f"riser resolving into the "
                                    f"+{rise[1]:g}dB rise at "
                                    f"{round(rt, 2)}s"))
    # Whooshes on internal cut junctions with the remaining budget. The 5s
    # cadence counts EXISTING sounds too — otherwise a re-run would fill the
    # very junctions the first run's spacing deliberately skipped.
    whoosh = _pick_sfx("transition", tag="whoosh")
    if whoosh:
        for j in tl.offsets[1:]:     # segment joins in final program time
            if len(placements) >= budget:
                break
            at = round(j, 2)
            if at < 0.5 or at > prog - 0.5:
                continue             # skip the junction at t=0 / the end
            if any(abs(at - x) < 5.0 for x in existing) or \
                    any(abs(at - q[1]) < 5.0 for q in placements):
                continue             # spaced >= 5s from every other accent
            placements.append((whoosh, at, "whoosh on the cut junction"))
    if not placements:
        return ("NO CHANGE: nothing to place — every candidate moment is "
                "within 1.5s of an existing sound, or there are no cut "
                "junctions/analysis to work from. Place sounds by hand with "
                "add_sfx if you still want them. Do NOT tell the user sound "
                "design was added.")
    items = [dict(sx) for sx in (edl.get("sfx") or [])]
    detail = []
    for sound, at, why in sorted(placements, key=lambda q: q[1]):
        taken = {sx.get("id") for sx in items}
        k = 1
        while f"sx{k}" in taken:
            k += 1
        items.append({"id": f"sx{k}",
                      "storage_key": sfx_library.ref(sound["slug"]),
                      "at": at, "gain_db": -6.0})
        detail.append(f"  sx{k}: '{sound['title']}' @ {at}s — {why}")
    edl["sfx"] = items
    res = ctx.write_edl(
        edl, f"sound-design pass ({level}): {len(detail)} placement(s) "
             "from the built-in pack")
    if res.startswith("EDL v"):
        res += "\nPlacements (program time):\n" + "\n".join(detail)
    return res


def _music_program_beats(ctx, edl, bpm=None, every_s=None, key=None):
    """(beats in PROGRAM seconds, label, error) for the SONG the viewer hears.

    "Cut it to the beat" means the beat of the music that is playing. When a
    song is laid over footage, the footage's own transients are not the beat —
    aligning to them is aligning to the wrong sound entirely, and on Jul 26
    2026 that is what happened: a user asked for cuts on a 1-second pulse, the
    tool measured the DRINKS FOOTAGE (80.7 BPM, confidence 0.15), refused, and
    the song sitting in the same EDL was never looked at.

    bpm/every_s is the user TELLING us the tempo ("there's a beat every
    second"). That is data, not a guess, so it skips the confidence gate — the
    gate exists to stop US inventing a pulse, never to overrule the person who
    can hear the track. Phase comes from where the music starts.
    """
    items = [m for m in (edl.get("music") or [])
             if float(m.get("end") or 0) > float(m.get("start") or 0)
             and (key is None or m.get("storage_key") == key)]
    if not items and not (bpm or every_s):
        return None, None, ("REJECTED: there is no music in this edit to cut "
                            "to. Add a track with add_music first, or pass "
                            "bpm/every_s if the user told you the tempo.")
    item = max(items, key=lambda m: float(m["end"]) - float(m["start"])) \
        if items else None
    prog = program_duration(edl)
    lo = float(item["start"]) if item else 0.0
    hi = min(float(item["end"]), prog) if item else prog

    if bpm or every_s:
        try:
            period = (60.0 / float(bpm)) if bpm else float(every_s)
        except (TypeError, ValueError, ZeroDivisionError):
            return None, None, ("REJECTED: bpm/every_s must be numbers "
                                "(bpm 40-220, or every_s 0.15-4).")
        if not (0.15 <= period <= 4.0):
            return None, None, (f"REJECTED: that works out to a beat every "
                                f"{period:.2f}s. Expected 0.15-4s "
                                f"(bpm 15-400) — check the units.")
        n = int((hi - lo) / period) + 1
        beats = [round(lo + k * period, 3) for k in range(n)
                 if lo + k * period <= hi + 1e-6]
        src = "the tempo you were given" if not item else \
            f"the tempo you were given, phased to the music at {lo:g}s"
        return beats, f"{60.0 / period:.4g} BPM ({src})", None

    p = ctx._asset_perception.get(item["storage_key"])
    if p is None:
        try:
            if music_library.is_library_ref(item["storage_key"]):
                p = perception.analyze_audio(
                    music_library.local_path(item["storage_key"]))
            else:
                asset = ctx.db.run(dbx.asset_by_key, ctx.project_id,
                                   item["storage_key"])
                if not asset:
                    return None, None, (
                        "Could not analyze the music in this edit — its file "
                        "is not a project asset any more.")
                p = perception.get_or_compute_for_asset(
                    ctx.db, dbx, asset, _asset_local_path(ctx, asset))
        except Exception as e:
            return None, None, (f"Could not analyze the music in this edit "
                                f"({str(e)[:160]}).")
        ctx._asset_perception[item["storage_key"]] = p
    conf = float(p.get("bpm_conf") or 0.0)
    track_beats = p.get("beats") or []
    if not track_beats or conf < 0.5:
        return None, None, (
            f"REJECTED: the music's own pulse is not clear enough to cut to "
            f"(bpm {p.get('bpm') or 'none'}, confidence {conf:.2f}; needs "
            f">= 0.5){_flatline_note(p)}. Do NOT pretend to sync. If the "
            "user can hear the beat, ask them how often it lands (or pass "
            "every_s/bpm if they already told you) and this will use it.")
    # Track seconds -> program seconds. The renderer loops at the demuxer and
    # trims [offset_s, offset_s + span) out of the repeated stream, so a beat
    # at track time t is heard at start - offset + t + k*track_length.
    off = float(item.get("offset_s") or 0.0)
    # Length of the analyzed audio, straight from the envelope it produced —
    # the perception sidecar carries no duration of its own, and the asset
    # row's can be absent on a library track.
    dur = (len(p.get("energy") or []) * float(p.get("energy_bin_s") or 0.5)
           or track_beats[-1] + 1.0)
    beats, cycle = [], 0
    while True:
        base = lo - off + cycle * dur
        if base > hi:
            break
        for t in track_beats:
            pt = base + t
            if lo - 1e-6 <= pt <= hi + 1e-6:
                beats.append(round(pt, 3))
        if not item.get("loop") or cycle > 200:
            break
        cycle += 1
    if not beats:
        return None, None, ("The music's beat grid does not overlap the part "
                            "of the program it plays over.")
    return beats, (f"{p.get('bpm'):g} BPM measured from the music "
                   f"(confidence {conf:.2f})"), None


def _speed_factor_at(tl, seg_i, t):
    """Playback factor applied at source time t inside segment seg_i, so a
    move of d PROGRAM seconds becomes d*factor SOURCE seconds."""
    try:
        for ps, pe, f in tl.pieces[seg_i]:
            if ps - 1e-6 <= t <= pe + 1e-6:
                return float(f) or 1.0
    except (IndexError, TypeError, ValueError):
        pass
    return 1.0


def _beat_align_to_music(ctx, edl, beats, label, tol):
    """Slide each internal cut so it LANDS on a beat of the music.

    Program-time, not source-time: what the viewer hears at a cut is decided
    by how much footage precedes it, so the thing to adjust is the END of the
    span before the junction. Junctions are processed left to right and the
    timeline is recomputed each time, because moving one shifts every later
    one — the reason a single pass over source times cannot do this.
    """
    cur = [list(x) for x in (edl.get("keep") or [])]
    if len(cur) < 2:
        return ("NO CHANGE: a single kept span has no internal cut boundaries "
                "to align (its start and end never move). Make cuts first. Do "
                "NOT tell the user the cuts were beat-aligned.")
    inserts, speeds = edl.get("inserts") or [], edl.get("speed") or []
    words = ctx.index.get("words") or []
    moved = skipped_tol = skipped_word = already = 0
    for i in range(len(cur) - 1):
        tl = Timeline(cur, inserts, speeds)
        p_at = tl.src_to_out(cur[i][1])
        if p_at is None:
            continue
        b = min(beats, key=lambda t: abs(t - p_at))
        d = b - p_at
        if abs(d) < 0.02:
            already += 1
            continue
        if abs(d) > tol:
            skipped_tol += 1
            continue
        cand = round(cur[i][1] + d * _speed_factor_at(tl, i, cur[i][1]), 2)
        if audit.word_at_boundary(words, cand):
            skipped_word += 1
            continue
        # Never invert the span, and never let it swallow the cut gap after
        # it — closing a gap silently restores footage the user removed.
        if not (cur[i][0] + 0.1 <= cand <= cur[i + 1][0] - 0.1):
            skipped_tol += 1
            continue
        cur[i][1] = cand
        moved += 1
    if not moved:
        return (f"NO CHANGE: no cut moved — {already} already on the beat, "
                f"{skipped_tol} had no beat within {tol}s (or the move would "
                f"collide with the next span), {skipped_word} would land "
                f"inside a word. Do NOT tell the user the cuts were "
                f"beat-aligned.")
    res = _write_keep(
        ctx, cur,
        f"beat-aligned {moved} cut{'' if moved == 1 else 's'} to {label} "
        f"(tolerance {tol}s)")
    if res.startswith("EDL v"):
        res += (f"\nMoved {moved} cut{'' if moved == 1 else 's'} onto the "
                f"beat; skipped {skipped_word} (would land inside a word) and "
                f"{skipped_tol} (no beat within {tol}s / would collide); "
                f"{already} already landed on it. Grid: {label}.")
    return res


def beat_align_cuts(ctx, tolerance_s=0.35, source=None, bpm=None,
                    every_s=None):
    """Move each INTERNAL keep boundary to the nearest beat within tolerance
    — never the program's first start or last end, never into a word."""
    if not ctx.has_main_video:
        return ("REJECTED: needs a main video with cut boundaries — an "
                "image/clip-only program has none.")
    try:
        tol = min(max(float(tolerance_s), 0.05), 1.0)
    except (TypeError, ValueError):
        return "REJECTED: tolerance_s must be a number of seconds."
    edl = ctx.latest_edl()["json"]
    want = (source or "").strip().lower() or None
    if want not in (None, "music", "video"):
        return ("REJECTED: source must be 'music' (the song the viewer hears) "
                "or 'video' (the footage's own audio).")
    # Auto: if a song is playing, THAT is the beat the user means.
    if want == "music" or bpm or every_s or (
            want is None and (edl.get("music") or [])):
        beats, label, err = _music_program_beats(ctx, edl, bpm, every_s)
        if err:
            return err
        return _beat_align_to_music(ctx, edl, beats, label, tol)
    try:
        p = _get_perception(ctx)
    except Exception as e:
        return f"Audio analysis unavailable ({str(e)[:160]})."
    bpm, conf = p.get("bpm"), float(p.get("bpm_conf") or 0.0)
    beats = p.get("beats") or []
    if not bpm or not beats or conf < 0.5:
        return ("REJECTED: the footage's own audio has no pulse clear enough "
                f"to cut to (bpm {bpm or 'none'}, confidence {conf:.2f}; "
                "beat-aligning needs >= 0.5). 'Syncing' cuts to a pulse that "
                "isn't really there would be a lie. Two real routes: add the "
                "song they want and call this again (it then cuts to the "
                "MUSIC), or — if the user has told you the tempo ('there's a "
                "beat every second') — pass every_s/bpm and it will use "
                "theirs. Never invent one yourself.")
    cur = ctx.latest_edl()["json"]["keep"]
    if len(cur) < 2:
        return ("NO CHANGE: a single kept span has no internal cut "
                "boundaries to align (its start and end never move). Make "
                "cuts first. Do NOT tell the user the cuts were "
                "beat-aligned.")
    words = ctx.index.get("words") or []
    new_keep = [list(x) for x in cur]
    moved = skipped_tol = skipped_word = already = 0
    for i in range(len(new_keep)):
        for j in (0, 1):
            if (i == 0 and j == 0) or (i == len(new_keep) - 1 and j == 1):
                continue          # the program's first start / last end
            b = new_keep[i][j]
            cand = min(beats, key=lambda t: abs(t - b))
            delta = abs(cand - b)
            if delta < 0.02:
                already += 1
                continue
            if delta > tol:
                skipped_tol += 1
                continue
            if audit.word_at_boundary(words, cand):
                skipped_word += 1
                continue
            # The move must not invert this span or collide with a neighbor
            # — and a cut GAP must never close: without the 0.1s margin,
            # both edges of a narrow cut (a removed filler is ~2x the
            # default tolerance) could snap to the SAME beat, silently
            # restoring the removed footage as touching spans.
            if j == 0:
                lo = (new_keep[i - 1][1] + 0.1) if i > 0 else 0.0
                hi = new_keep[i][1] - 0.1
            else:
                lo = new_keep[i][0] + 0.1
                hi = ((new_keep[i + 1][0] - 0.1)
                      if i < len(new_keep) - 1 else ctx.duration)
            if not (lo - 1e-6 <= cand <= hi + 1e-6):
                skipped_tol += 1
                continue
            new_keep[i][j] = round(cand, 2)
            moved += 1
    if not moved:
        return (f"NO CHANGE: no boundary moved — {already} already on the "
                f"beat, {skipped_tol} had no beat within {tol}s (or the "
                f"move would collide with a neighbouring span), "
                f"{skipped_word} would land inside a word. Do NOT tell the "
                "user the cuts were beat-aligned.")
    res = _write_keep(
        ctx, new_keep,
        f"beat-aligned {moved} internal cut boundar"
        f"{'y' if moved == 1 else 'ies'} to the {bpm:g} BPM grid "
        f"(tolerance {tol}s)")
    if res.startswith("EDL v"):
        res += (f"\nMoved {moved}; skipped {skipped_word} (would land "
                f"inside a word) and {skipped_tol} (no beat within {tol}s "
                f"/ neighbour collision); {already} already on the beat. "
                f"BPM {bpm:g}, confidence {conf:.2f}.")
    return res


def _emphasis_candidates(ctx):
    """(words, detail_lines, note): emphasis candidates from the REAL
    transcript — measured vocal stress + digit words + rare words.
    Deterministic; stress silently degrades to nothing when analysis fails
    (the note says so)."""
    words = ctx.index.get("words") or []
    if not words:
        return [], [], ""
    seen, out = set(), []

    def _add(tok):
        t = str(tok or "").strip("\"'.,!?;:").strip()
        k = _norm_token(t)
        if not k or k in seen:
            return False
        seen.add(k)
        out.append(t)
        return True

    lines, note = [], ""
    stressed = []
    try:
        p = _get_perception(ctx)
        scores = perception.word_stress(p, words)
        for i in perception.top_stressed_words(p, words, count=10):
            stressed.append((words[i], scores[i]))
        cov = perception.stress_coverage_s(p)
        if any(float(w["t0"]) >= cov for w in words):
            note = (f"\n(stress analysis covers the first {cov / 60:.0f} "
                    "minutes only — words after that were never measured "
                    "and are excluded from the stressed list)")
    except Exception as e:
        note = (f"\n(vocal-stress analysis unavailable: {str(e)[:120]} — "
                "the list is built from digits/rarity only)")
    if stressed:
        lines.append("vocally stressed (measured): "
                     + ", ".join(f"'{w['w']}' @{w['t0']:.1f}s ({sc:.2f})"
                                 for w, sc in stressed))
        for w, _sc in stressed:
            _add(w["w"])
    digits = [w["w"].strip("\"'.,!?;:") for w in words
              if any(ch.isdigit() for ch in w["w"]) and _add(w["w"])]
    if digits:
        lines.append("numbers (presets emphasize digits automatically, "
                     "still worth listing): " + ", ".join(digits[:12]))
    freq = {}
    for w in words:
        k = _norm_token(w["w"])
        if k:
            freq[k] = freq.get(k, 0) + 1
    rare = []
    for w in words:
        if len(out) >= 25:
            break
        k = _norm_token(w["w"])
        if k and len(k) >= 6 and freq[k] == 1 and _add(w["w"]):
            rare.append(w["w"].strip("\"'.,!?;:"))
    if rare:
        lines.append("rare/distinctive: " + ", ".join(rare[:12]))
    return out[:25], lines, note


def suggest_emphasis(ctx):
    """READ: candidate emphasis words, verbatim from the transcript."""
    if not ctx.has_main_video:
        return ("REJECTED: needs a transcribed main video — an "
                "image/clip-only program has no transcript.")
    out, lines, note = _emphasis_candidates(ctx)
    if not out:
        return ("This video has no transcript (or no usable words) — there "
                "is nothing to emphasize.")
    return _cap("Emphasis candidates (verbatim from the REAL transcript):\n"
                + "\n".join("- " + ln for ln in lines)
                + "\nPass to add_captions / set_caption_style as "
                  "emphasis_words: " + json.dumps(out) + note)


# ONE-CALL looks (apply_look): each composes caption style + grade + custom
# grade + transitions + fades + stylize as plain EDL DATA — every component
# is an ordinary field the user could have set one call at a time, and the
# result names each one. A key absent = leave that axis alone; "grade": None
# = explicitly clear it. sound_design/smooth_duck are REPORTED suggestions
# only — apply_look never touches keep, music or sfx.
LOOKS = {
    "hype": {"captions": {"preset": "beast", "size": "xl"},
             "grade": "vibrant", "transition": ("zoom_punch", 0.25),
             "fade_out_s": 0.6, "sound_design": "medium"},
    "clean": {"captions": {"preset": "podcast"}, "grade": None,
              "fade_in_s": 0.5, "fade_out_s": 0.5, "smooth_duck": True},
    "cinematic": {"captions": {"preset": "elegant"}, "grade": "cinematic",
                  "grade_custom": {"temperature": 0.1},
                  "fade_in_s": 1.0, "fade_out_s": 1.0,
                  "transition": ("dip_black", 0.4)},
    "luxury": {"captions": {"preset": "luxe"}, "grade": "warm",
               "grade_custom": {"temperature": 0.15},
               "fade_in_s": 0.8, "fade_out_s": 0.8},
    "meme": {"captions": {"preset": "impact", "size": "xl"},
             "transition": ("flash", 0.15), "stylize": ("grain", 0.3)},
}


def apply_look(ctx, name):
    """Compose one look — captions/grade/transitions/fades/stylize — in a
    single EDL version, reporting every component it set."""
    n = (name or "").strip().lower()
    look = LOOKS.get(n)
    if not look:
        return (f"REJECTED: unknown look '{name}'. Looks: "
                + ", ".join(sorted(LOOKS))
                + ". Each composes captions, grade, transitions, fades and "
                  "stylize in one version.")
    edl = dict(ctx.latest_edl()["json"])
    set_bits, notes = [], []
    cap_patch = look.get("captions")
    if cap_patch:
        if not ctx.has_main_video or not (ctx.index.get("words") or []):
            notes.append("captions skipped — no transcribed main video, so "
                         "there is nothing to caption.")
        else:
            caps = edl.get("captions")
            if isinstance(caps, dict) and caps.get("emphasis_words"):
                emphasis, emph_src = caps["emphasis_words"], "kept existing"
            else:
                emphasis = _emphasis_candidates(ctx)[0]
                emph_src = "picked from the transcript"
            merged = (merge_caption_style(caps, dict(cap_patch)) if caps
                      else {"mode": "from_transcript",
                            "max_words_per_caption": None,
                            "style": dict(cap_patch)})
            bit = f"captions preset '{cap_patch['preset']}'"
            if cap_patch.get("size"):
                bit += f" size {cap_patch['size']}"
            if isinstance(merged, dict):
                if emphasis:
                    merged["emphasis_words"] = emphasis
                    bit += f", {len(emphasis)} emphasis words ({emph_src})"
            else:
                bit += (" (manual caption items restyled; emphasis words "
                        "apply to transcript captions only)")
            edl["captions"] = merged
            set_bits.append(bit)
    fx = dict(edl.get("effects") or {})
    if "grade" in look:
        fx["grade"] = look["grade"]
        set_bits.append(f"grade {look['grade'] or 'cleared'}")
    if look.get("grade_custom"):
        gc = dict(fx.get("grade_custom") or {})
        gc.update(look["grade_custom"])
        fx["grade_custom"] = gc
        set_bits.append("custom grade " + ", ".join(
            f"{k} {v:+g}" for k, v in look["grade_custom"].items()))
    if look.get("transition"):
        tst, tdur = look["transition"]
        fx["transition"] = {"style": tst, "duration_s": tdur}
        set_bits.append(f"transitions {tst} {tdur}s")
    for fk, label in (("fade_in_s", "fade in"), ("fade_out_s", "fade out")):
        if fk in look:
            fx[fk] = look[fk]
            set_bits.append(f"{label} {look[fk]}s")
    if look.get("stylize"):
        skind, sint = look["stylize"]
        sts = [dict(sx) for sx in (fx.get("stylize") or [])]
        if any(sx.get("kind") == skind and sx.get("start") is None
               for sx in sts):
            notes.append(f"stylize {skind} was already on the whole video — "
                         "left as is.")
        else:
            sts.append({"id": _next_item_id(sts, "st"), "kind": skind,
                        "start": None, "end": None, "intensity": sint})
            fx["stylize"] = sts
            set_bits.append(f"stylize {skind} {sint:g}")
    edl["effects"] = fx
    if not set_bits:
        return ("NO CHANGE: every component of that look is already in "
                "place (or not applicable here). Do NOT tell the user you "
                "changed anything.")
    res = ctx.write_edl(edl, f"look '{n}': " + "; ".join(set_bits))
    if res.startswith("EDL v"):
        if notes:
            res += "\n" + "\n".join("Note: " + x for x in notes)
        # sound_design_pass places BUNDLED sounds — on a deployment that
        # ships no pack the tool is hidden, so suggesting it here would
        # advertise a capability that can only reject.
        if not sfx_library.CATALOG:
            res += "\napply_look never touches cuts, music or sfx."
        elif look.get("sound_design"):
            res += ("\nSound design is a separate call — run "
                    f"sound_design_pass('{look['sound_design']}') for the "
                    "audio accents this look pairs with (apply_look never "
                    "touches cuts, music or sfx).")
        else:
            res += ("\napply_look never touches cuts, music or sfx — sound "
                    "design is a separate call (sound_design_pass).")
        if look.get("smooth_duck") and edl.get("music"):
            res += ("\nNote: existing music items were NOT touched — for "
                    "the smooth speech duck this look pairs with, call "
                    "set_music_fit(id, duck_mode='smooth') on them.")
    return res


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
    "get_words": (get_words, "Word-level timestamps [{t0-t1 word}] for any "
                  "source-time range (the response caps at 400 words and "
                  "says how to page for the rest). THE source of truth "
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
                     "max_words_per_caption 1-16. Example — premium reel "
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
    "add_music": (add_music, "Mix music into the edit. The defaults are "
                  "CONTEXT-AWARE: under speech the track sits low as a bed "
                  "(-18dB, ducked); when NO speech survives under the window "
                  "the music is the LEAD audio (-4dB, no ducking) so the "
                  "user actually hears it. Pass gain_db/duck only to "
                  "override that. storage_key is either a 'library:<slug>' "
                  "from list_music_library() or an exact key from "
                  "list_assets(kind='music') (the user's own uploads) — "
                  "never invent one. start/end are OUTPUT-timeline seconds "
                  "and DEFAULT TO THE WHOLE VIDEO, so omit them for 'add "
                  "some music'. Fades in/out by default. loop=true (the "
                  "default) repeats a short track to fill the span; "
                  "offset_s starts partway into the track, e.g. to skip a "
                  "slow intro. Ducking is SMOOTH by default (a sidechain dip "
                  "that follows the voice; set_music_fit(duck_mode='step') "
                  "restores the legacy hard -12dB duck).",
                  {"storage_key": {"type": "string"},
                   "start": {"type": "number"},
                   "end": {"type": "number"},
                   "gain_db": {"type": "number"},
                   "duck": {"type": "boolean"},
                   "offset_s": {"type": "number"},
                   "fade_in_s": {"type": "number"},
                   "fade_out_s": {"type": "number"},
                   "loop": {"type": "boolean"}}),
    "extract_audio": (extract_audio, "Take ONLY the sound out of an uploaded "
                      "VIDEO and save it as an audio file — THE answer to "
                      "'use the song from this clip', 'put this video's audio "
                      "on my video', 'I want the sound but not the picture'. "
                      "Users hand you songs as videos because a TikTok or "
                      "Reel download is the only file they have; that is "
                      "normal and it works. The clip's picture is never "
                      "shown. asset_key is a [video_clip] storage_key from "
                      "list_assets. Returns a new storage_key for add_music / "
                      "add_sfx / add_voiceover — nothing is in the edit until "
                      "you place it. Passing a clip's key DIRECTLY to those "
                      "tools does the same thing in one step; call this when "
                      "you want the file first (e.g. to get_audio_analysis "
                      "its beats). If the clip is silent it says so — never "
                      "claim a sound was added.",
                      {"asset_key": {"type": "string"}}),
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
                      "Anything you omit is left alone. duck_mode: 'smooth' "
                      "= a sidechain dip that follows the voice and swells "
                      "back in the gaps; 'step' = the legacy hard -12dB "
                      "duck. Use this instead of remove+re-add, which loses "
                      "the other settings. For loudness use set_audio_gain.",
                      {"id": {"type": "string"},
                       "start": {"type": "number"},
                       "end": {"type": "number"},
                       "offset_s": {"type": "number"},
                       "fade_in_s": {"type": "number"},
                       "fade_out_s": {"type": "number"},
                       "loop": {"type": "boolean"},
                       "duck": {"type": "boolean"},
                       "duck_mode": {"type": "string",
                                     "enum": ["smooth", "step"]}}),
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
                  "(default), pad (black bars) or pad_blur (blurred "
                  "backdrop). focus_x/focus_y aim the CROP at the subject "
                  "(fractions of the source frame, (0,0) = top-left) — "
                  "without them the crop is the dead-center window, which "
                  "chops an off-center speaker. For 'make it 9:16' on real "
                  "footage PREFER auto_reframe, which measures the subject "
                  "and sets the focus for you. Never upscales beyond the "
                  "source's pixels.",
                  {"ratio": {"type": "string",
                             "enum": ["source", "16:9", "9:16", "1:1",
                                      "4:5"]},
                   "mode": {"type": "string",
                            "enum": ["crop", "pad", "pad_blur"]},
                   "focus_x": {"type": "number"},
                   "focus_y": {"type": "number"}}),
    "auto_reframe": (auto_reframe, "Convert the output frame AND aim the "
                     "crop at the real subject: samples frames across the "
                     "kept footage, asks the vision model where the subject "
                     "sits, and writes set_frame with that focus. THE tool "
                     "for 'make it 9:16 / vertical / for TikTok' on real "
                     "footage — a plain center crop chops off-center "
                     "subjects. The focus is one fixed point (it does not "
                     "track motion); it reports the measured position so "
                     "you can judge and adjust with set_frame focus_x/"
                     "focus_y.",
                     {"ratio": {"type": "string",
                                "enum": ["9:16", "1:1", "4:5", "16:9",
                                         "source"]},
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
    "search_stock": (search_stock, "SEARCH A STOCK LIBRARY for b-roll the "
                     "user does not have — 'a shot of a busy city', 'ocean "
                     "waves', 'someone typing'. Returns candidates ONLY: "
                     "nothing is downloaded and nothing enters the video. "
                     "kind 'video' (default) or 'photo'. orientation "
                     "defaults to the project's output frame, so a 9:16 edit "
                     "gets vertical footage. Use short, VISUAL queries — "
                     "'city traffic at night' beats 'the pace of modern "
                     "life'. Then call add_stock_media with the best id.",
                     {"query": {"type": "string"},
                      "kind": {"type": "string",
                               "enum": ["video", "photo"]},
                      "orientation": {"type": "string",
                                      "enum": ["landscape", "portrait",
                                               "square"]},
                      "count": {"type": "integer"}}),
    "add_stock_media": (add_stock_media, "DOWNLOAD one search_stock result "
                        "and save it as a project asset. `id` must be an id "
                        "from a search_stock result in THIS turn. The clip "
                        "is SILENT and is NOT in the video yet — place it "
                        "with add_overlay(fit='cover') for a cutaway that "
                        "keeps the speech running, or insert_media to splice "
                        "it in. Always tell the user which shot you used.",
                        {"id": {"type": "string"}}),
    "record_website": (record_website, "RECORD A LIVE WEB PAGE as video: a "
                       "headless browser opens the URL at the project's "
                       "aspect, holds the top, smooth-scrolls to the bottom "
                       "and holds — the classic product-demo pan — and the "
                       "capture becomes a project video asset. THE tool for "
                       "'show my website / landing page / this product page "
                       "in the edit'. duration_s 4-30 (default 12). "
                       "orientation defaults to the project's output frame. "
                       "scroll=false just holds the top of the page. The "
                       "capture is SILENT and shows the PUBLIC page (no "
                       "logins, no clicks); place it with insert_media or "
                       "add_overlay(fit='cover').",
                       {"url": {"type": "string"},
                        "duration_s": {"type": "number"},
                        "orientation": {"type": "string",
                                        "enum": ["landscape", "portrait",
                                                 "square"]},
                        "scroll": {"type": "boolean"}}),
    "record_website_demo": (
        record_website_demo,
        "RECORD THE BROWSER USING A SITE — the showcase capture. A headless "
        "browser opens the URL with a VISIBLE cursor drawn on screen and "
        "works through `steps` you write: it glides the pointer to a "
        "button, clicks it, waits for the page to react, "
        "types into fields at human speed, scrolls between sections. THE "
        "tool for 'record yourself using my product', a launch/demo video, "
        "or 'show how it works', where record_website only pans down a "
        "static page. Steps are objects: {do:'click', text:'Start free "
        "trial'} (text = the VISIBLE label; or selector: a CSS selector), "
        "{do:'type', selector:'input[type=email]', text:'you@example.com'}, "
        "{do:'scroll', to:'Pricing'} or {do:'scroll', by:800}, "
        "{do:'hover', text:'Plans'}, {do:'press', key:'Enter'}, "
        "{do:'wait', seconds:1.5}, {do:'goto', url:'…'}. Add "
        "`seconds` to any step to hold longer after it. It returns an EVENT "
        "TRACK — every click, scroll and keystroke timestamped with its "
        "position in the frame — then place it with showcase_demo, which "
        "uses that track. It records the PUBLIC site as a visitor sees it "
        "and will not type into password or payment fields. Write 4-10 "
        "steps that tell one story; a demo that clicks everything shows "
        "nothing.",
        {"url": {"type": "string"},
         "steps": {"type": "array", "items": {"type": "object"}},
         "orientation": {"type": "string",
                         "enum": ["landscape", "portrait", "square"]}}),
    "showcase_demo": (
        showcase_demo,
        "PLACE A RECORDED DEMO AND CUT IT LIKE A PRODUCT VIDEO — one call. "
        "Splices the capture into the edit, then uses its event track to "
        "glide a zoom onto each click (the frame pushes in and TRAVELS "
        "between buttons instead of cutting in and out) and land a click "
        "sound on the exact frame of each press, a soft pop on each page "
        "change and a swipe under each scroll. Only works on a capture from "
        "record_website_demo. at_output_s defaults to the END of the "
        "current edit; zoom_strength 0.05-1.5 (0.4 default — screen text "
        "needs a real push to read); set zooms=false or click_sounds=false "
        "to place it plainly. Use insert_media instead only when the user "
        "wants the raw capture with no treatment.",
        {"asset_key": {"type": "string"},
         "at_output_s": {"type": "number"},
         "zoom_strength": {"type": "number"},
         "click_sounds": {"type": "boolean"},
         "zooms": {"type": "boolean"}}),
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
                 "emphasis on a key line. strength 0.05-1.5 (default 0.25 = "
                 "25% closer; above 1.0 is a dramatic 2x+ punch). mode: "
                 "'punch' (default, instant step), 'ease' (smoothly ramps "
                 "in and out — use when the user wants it subtle/animated), "
                 "'push_in' / 'pull_out' (continuous Ken Burns drift across "
                 "the whole window — use for slow cinematic movement). "
                 "cx/cy (0-1 fractions of the output frame, (0,0) = "
                 "top-left) AIM the zoom at a subject instead of the "
                 "center — find it with look_at first; omit both for the "
                 "classic center zoom. Use 1-3 short zooms at emphatic "
                 "moments, not wall-to-wall; for automatic zooms on the "
                 "strongest spoken words use punch_in_on_emphasis.",
                 {"start": {"type": "number"}, "end": {"type": "number"},
                  "strength": {"type": "number"},
                  "mode": {"type": "string",
                           "enum": ["punch", "ease", "push_in",
                                    "pull_out"]},
                  "cx": {"type": "number"},
                  "cy": {"type": "number"}}),
    "remove_zoom": (remove_zoom, "Remove one zoom by its id (see "
                    "get_edl).", {"id": {"type": "string"}}),
    "set_fades": (set_fades, "Fade from black at the start and/or to black "
                  "at the end (video + audio). Seconds; 0 clears. Example: "
                  "set_fades(fade_in_s=0.5, fade_out_s=0.8).",
                  {"fade_in_s": {"type": "number"},
                   "fade_out_s": {"type": "number"}}),
    "set_transitions": (set_transitions, "Transitions at SCENE CHANGES — "
                        "junctions where the footage actually changes shot, "
                        "or where an insert (b-roll, title card, generated "
                        "clip) splices in. All duration-preserving junction "
                        "effects (footage never overlaps, timing never "
                        "changes). IMPORTANT — after cut_silences a "
                        "talking-head video has one junction per removed "
                        "pause, and nearly all of them are JUMP CUTS inside "
                        "one continuous shot: same framing, same subject, "
                        "the speaker's head half a word further along. A "
                        "jump cut works by being invisible. Putting a whip "
                        "or a dip on each one fires a full-screen effect "
                        "every couple of seconds through footage that never "
                        "changed scene, and it reads as broken — a real user "
                        "shipped 45 whips through one continuous shot and "
                        "said so. scope defaults to 'scene' and handles this "
                        "for you; the result tells you how many junctions it "
                        "actually landed on and how many it skipped, so "
                        "report THAT number, not the cut count. Only pass "
                        "scope='every_cut' when the user explicitly wants "
                        "one on every cut, or the edit is a montage "
                        "assembled from separate clips. Styles: 'dip_black' "
                        "= quick dip "
                        "through black (calm, universal); 'dip_white' = "
                        "soft white fade-through; 'whip_left'/'whip_right' "
                        "= fast directional slide with motion blur "
                        "(energetic vlogs/reels); 'zoom_punch' = "
                        "accelerating push through the cut (hype, sports); "
                        "'glitch' = RGB-split/noise burst (tech, gaming); "
                        "'flash' = additive white pop peaking ON the cut "
                        "(beat-synced edits). duration_s 0.1-1.5 (default "
                        "0.3; keep whip/flash short, 0.15-0.4). 'none' "
                        "removes them (hard cuts again). True crossfades "
                        "(overlapping footage) are NOT supported — offer "
                        "one of these instead and say so.",
                        {"style": {"type": "string",
                                   "enum": list(TRANSITION_STYLES)
                                   + ["none"]},
                         "duration_s": {"type": "number"},
                         "scope": {"type": "string",
                                   "enum": list(TRANSITION_SCOPES),
                                   "description":
                                       "'scene' (default) = only where the "
                                       "footage changes shot or an insert "
                                       "splices in. 'every_cut' = every "
                                       "junction including silence-removal "
                                       "jump cuts; ask for it only when the "
                                       "user explicitly wants that."}}),
    "blur_region": (blur_region, "Put a VISIBLE censor over a fixed "
                    "RECTANGLE of the original footage — blur, mosaic or a "
                    "black bar. Use it when the user WANTS the covering to "
                    "show: a face, a document, a phone number, a plate. To "
                    "make something GO AWAY instead — burned-in captions, a "
                    "watermark, a username, an object — use erase_region / "
                    "erase_burned_text, which repaint the pixels and rebuild "
                    "the picture behind them; a blur where the user asked "
                    "for removal reads as a workaround. x,y = TOP-LEFT corner "
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
    "find_burned_text": (find_burned_text, "MEASURE where text is burned into "
                         "the footage — subtitle bands, watermarks, handles, "
                         "on-screen labels. Reads the actual frames and "
                         "returns EXACT rectangles as frame fractions, plus "
                         "when each is visible. Use this INSTEAD of "
                         "estimating a rectangle from look_at: an estimated "
                         "box is what puts a bar next to the text instead of "
                         "over it. Read-only. scope: 'captions' (wide "
                         "subtitle lines whose words change), 'watermark' (a "
                         "small mark identical in every frame), 'text', or "
                         "'all' (default). start/end limit the scan to a "
                         "source-time window.",
                         {"scope": {"type": "string",
                                    "enum": ["all", "captions", "watermark",
                                             "text"]},
                          "start": {"type": "number"},
                          "end": {"type": "number"}}),
    "erase_burned_text": (erase_burned_text, "TRULY REMOVE burned-in captions "
                          "(or watermarks) — one call: it measures every "
                          "matching region and REPAINTS THE PIXELS, "
                          "reconstructing the picture that was behind the "
                          "text. This is real removal, not a blur or a bar: "
                          "say 'removed'. Use it for 'remove the captions' / "
                          "'take the subtitles off' / 'get rid of the "
                          "watermark' on footage that arrived with text "
                          "burned in, and BEFORE add_captions when the user "
                          "wants a different caption font or style — with the "
                          "old text gone, new captions cannot stack on it. "
                          "Cuts, timings, transcript and captions are "
                          "unaffected; the video is unchanged except that the "
                          "text is gone. scope defaults to 'captions'.",
                          {"scope": {"type": "string",
                                     "enum": ["all", "captions", "watermark",
                                              "text"]},
                           "start": {"type": "number"},
                           "end": {"type": "number"}}),
    "erase_region": (erase_region, "TRULY REMOVE whatever is inside one "
                     "rectangle — repaints those pixels and reconstructs the "
                     "background, so the thing is GONE, not covered. Use it "
                     "for a word, a sign, a sticker, a logo, a person's name "
                     "on screen, or any object the user wants taken out. "
                     "x,y = TOP-LEFT corner, w,h = size, all FRACTIONS (0-1) "
                     "of the SOURCE frame — get them from find_burned_text "
                     "rather than estimating. fill: 'text' (default — "
                     "repaints only the letter strokes and keeps the picture "
                     "behind them; best for captions/handles) or 'box' "
                     "(repaints the whole rectangle; use for an OBJECT or a "
                     "solid graphic). start/end (SOURCE seconds) limit it to "
                     "a window; omit both for the whole video. Reconstruction "
                     "is excellent for thin text and for anything on a steady "
                     "shot; a large object on a moving, detailed background "
                     "can leave a soft patch — the result is measured and "
                     "reported back to you, so check it before you promise "
                     "anything.",
                     {"x": {"type": "number"}, "y": {"type": "number"},
                      "w": {"type": "number"}, "h": {"type": "number"},
                      "start": {"type": "number"},
                      "end": {"type": "number"},
                      "fill": {"type": "string", "enum": ["text", "box"]}}),
    "reset_edit": (reset_edit, "Throw the whole edit away and start again "
                   "from the full untouched source video. Use it when the "
                   "user asks to start over, and as the LAST RESORT when a "
                   "write tool keeps rejecting the EDL for a reason you "
                   "cannot fix from inside it (a span that no longer fits the "
                   "source). Destructive: it drops every cut, caption, track "
                   "and effect, so say so before and after.", {}),
    "remove_erase": (remove_erase, "Undo an erase: put the original pixels "
                     "back for one erased region by its id (see get_edl), or "
                     "for ALL of them when id is omitted. Always rebuilds "
                     "from the untouched original.",
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
    "set_speed": (set_speed, "Speed up or slow down a SOURCE-time range of "
                  "the main video (like set_volume, start/end are SOURCE "
                  "seconds — the ramp stays on its footage through later "
                  "cuts, and music/overlays/zooms/sfx are re-anchored "
                  "automatically). factor 0.25-4.0: 2.0 = double speed, "
                  "0.5 = half; audio keeps its pitch. Slow motion below "
                  "0.6x visibly steps (frames are duplicated, not "
                  "synthesized) — the tool warns; prefer 0.6-0.8x. A span "
                  "that overlaps an existing one REPLACES it (disclosed). "
                  "THE tool for 'speed up the boring part' / 'slow-mo that "
                  "moment'.",
                  {"start": {"type": "number"},
                   "end": {"type": "number"},
                   "factor": {"type": "number"}}),
    "remove_speed": (remove_speed, "Remove one speed span by its id (see "
                     "get_edl) — that footage returns to normal speed and "
                     "program-time items re-anchor automatically.",
                     {"id": {"type": "string"}}),
    "add_overlay": (add_overlay, "Draw an image or video clip OVER the "
                    "program picture for a window of PROGRAM time — "
                    "picture-in-picture, a corner logo, or fit='cover' for "
                    "a FULL-FRAME B-ROLL CUTAWAY: the picture switches to "
                    "the asset while the program's audio (the speaker, the "
                    "music) keeps playing — THE way to show what the "
                    "speaker is talking about without touching the timing. "
                    "asset_key from list_assets (kind='clip'/'image') or a "
                    "generated/fetched/recorded asset. duration_s defaults: "
                    "image 4s, video the clip's length (bounded by the "
                    "program end); b-roll reads best at 2-6s. x/y = the "
                    "overlay's CENTER as fractions of the frame (ignored "
                    "with fit='cover') — pass a keyframe list [{t,v}] (t = "
                    "seconds from the overlay's own start) for a slow "
                    "drift/slide. scale = overlay width as a fraction of "
                    "the frame width (0.05-1.0, default 0.4; ignored with "
                    "fit='cover'). opacity 0.05-1.0 (omit = opaque). "
                    "entrance/exit: fade, slide_left, slide_right, "
                    "slide_up. source_start_s seeks into a video overlay. "
                    "HONEST LIMITS: a video overlay's audio does NOT play "
                    "(silent), overlays render above footage but BELOW "
                    "captions (captions stay visible over b-roll), and "
                    "they do NOT track objects in the footage. "
                    "insert_media PAUSES the talk and adds time; "
                    "fit='cover' does not — pick by whether the speech "
                    "should continue.",
                    {"asset_key": {"type": "string"},
                     "start": {"type": "number"},
                     "duration_s": {"type": "number"},
                     "x": {"type": ["number", "array"]},
                     "y": {"type": ["number", "array"]},
                     "scale": {"type": "number"},
                     "fit": {"type": "string", "enum": ["cover"]},
                     "opacity": {"type": "number"},
                     "entrance": {"type": "string",
                                  "enum": list(OVERLAY_ANIMS)},
                     "exit": {"type": "string",
                              "enum": list(OVERLAY_ANIMS)},
                     "source_start_s": {"type": "number"}}),
    "move_overlay": (move_overlay, "Reposition/retime/resize an EXISTING "
                     "overlay — 'move the logo to the other corner', 'make "
                     "the PIP smaller'. Only the fields you pass change. id "
                     "from get_edl.",
                     {"id": {"type": "string"},
                      "start": {"type": "number"},
                      "x": {"type": ["number", "array"]},
                      "y": {"type": ["number", "array"]},
                      "scale": {"type": "number"}}),
    "remove_overlay": (remove_overlay, "Remove one overlay by its id (see "
                       "get_edl).", {"id": {"type": "string"}}),
    "add_text": (add_text, "Burn a designed motion-graphics TEXT template "
                 "over a PROGRAM-time window — separate from captions "
                 "(spoken words) and overlays (media). Templates: 'title' "
                 "(big centered opening card), 'subtitle' (support line "
                 "under a title), 'lower_third' (name/context bar, "
                 "interviews), 'callout' (short pointed label), "
                 "'big_number' (a huge stat — '10x', '$40K'), 'quote' (a "
                 "quoted line), 'chapter' (section marker). x/y override "
                 "the template's position (fractions of the frame); "
                 "size_scale 0.4-3.0; color/accent_color '#RRGGBB'; font "
                 "from the bundled families (exact name, e.g. 'Anton'); "
                 "entrance/exit: fade, pop, slide_up, blur_in, whip, rise, "
                 "drop, plus 'typewriter' (entrance only); uppercase forces "
                 "casing; box adds a backing panel. Use for text the user "
                 "dictates — titles, labels, stats; spoken-word captions "
                 "stay with add_captions.",
                 {"text": {"type": "string"},
                  "start": {"type": "number"},
                  "end": {"type": "number"},
                  "template": {"type": "string",
                               "enum": list(TEXT_TEMPLATES)},
                  "x": {"type": "number"},
                  "y": {"type": "number"},
                  "size_scale": {"type": "number"},
                  "color": {"type": "string"},
                  "accent_color": {"type": "string"},
                  "font": {"type": "string", "enum": list(TEXT_FONTS)},
                  "entrance": {"type": "string", "enum": list(TEXT_ANIMS)},
                  "exit": {"type": "string",
                           "enum": [a for a in TEXT_ANIMS
                                    if a != "typewriter"]},
                  "uppercase": {"type": "boolean"},
                  "box": {"type": "boolean"}}),
    "remove_text": (remove_text, "Remove one text element by its id (see "
                    "get_edl).", {"id": {"type": "string"}}),
    "add_title_card": (add_title_card, "Cut to a STANDALONE full-frame card "
                       "showing only this text, then return to the footage — "
                       "the 'show the term on a blank screen' move. One call "
                       "does all of it: builds the solid-colour card, splices "
                       "it into the program at at_output_s, and centres the "
                       "text on it. Because the card is a real cut (not an "
                       "overlay), spoken-word captions never appear on it, so "
                       "nothing overlaps. at_output_s is PROGRAM seconds and "
                       "everything after it shifts later by duration_s "
                       "(2-3s reads well). bg_color is the card colour "
                       "('#000000' default); subtitle adds a smaller second "
                       "line under the title. Use add_text instead when the "
                       "text should sit OVER the footage rather than replace "
                       "it.",
                       {"text": {"type": "string"},
                        "at_output_s": {"type": "number"},
                        "duration_s": {"type": "number"},
                        "template": {"type": "string",
                                     "enum": list(TEXT_TEMPLATES)},
                        "bg_color": {"type": "string"},
                        "color": {"type": "string"},
                        "accent_color": {"type": "string"},
                        "font": {"type": "string", "enum": list(TEXT_FONTS)},
                        "size_scale": {"type": "number"},
                        "entrance": {"type": "string", "enum": list(TEXT_ANIMS)},
                        "exit": {"type": "string",
                                 "enum": [a for a in TEXT_ANIMS
                                          if a != "typewriter"]},
                        "subtitle": {"type": "string"}}),
    "add_color_screen": (add_color_screen, "Cut to a full-frame SOLID or "
                         "GRADIENT colour screen for a moment, then return to "
                         "the footage — NO text, NO image generation, built "
                         "instantly. Use for a plain white/black flash, a "
                         "coloured interstitial, or a gradient backdrop. "
                         "color is '#RRGGBB' (default black; '#FFFFFF' is "
                         "white); pass color2 for a two-colour gradient with "
                         "direction vertical/horizontal/diagonal/radial. "
                         "at_output_s is PROGRAM seconds; everything after it "
                         "shifts later by duration_s. motion adds a slow Ken "
                         "Burns push on the screen. For a card WITH a word on "
                         "it use add_title_card; to put text over this screen "
                         "afterwards, add_text at the same window.",
                         {"at_output_s": {"type": "number"},
                          "duration_s": {"type": "number"},
                          "color": {"type": "string"},
                          "color2": {"type": "string"},
                          "direction": {"type": "string",
                                        "enum": list(CARD_DIRECTIONS)},
                          "motion": {"type": "string",
                                     "enum": list(INSERT_MOTIONS)}}),
    "add_corrupt_screen": (add_corrupt_screen, "Cut to a full-frame CORRUPT / "
                           "glitch screen for a beat, then return to the "
                           "footage — a datamosh-style transition BETWEEN "
                           "sections (e.g. podcast -> CORRUPT -> the next "
                           "scene). Synthesized locally like the colour cards: "
                           "NO image or video generation, always available, "
                           "costs no generation credits. style: 'digital' "
                           "(vivid datamosh macroblocks + horizontal tearing, "
                           "the default), 'vhs' (tracking band + scanlines + "
                           "chroma bleed), or 'static' (TV snow / no-signal). "
                           "at_output_s is PROGRAM seconds; everything after "
                           "shifts later by duration_s. Keep it SHORT (0.3-1s "
                           "reads as a hit; longer feels genuinely broken). "
                           "intensity 0-1 = how harsh (default 0.7). sound "
                           "(default true) plays a matching static/hiss burst "
                           "over the glitch; set false for a silent flicker. "
                           "No captions ever land on it (inserted media).",
                           {"at_output_s": {"type": "number"},
                            "duration_s": {"type": "number"},
                            "style": {"type": "string",
                                      "enum": list(CORRUPT_STYLES)},
                            "intensity": {"type": "number"},
                            "sound": {"type": "boolean"}}),
    "set_caption_mutes": (set_caption_mutes, "Hide the burned spoken-word "
                          "captions over specific PROGRAM-time windows, "
                          "leaving them on everywhere else — for when a "
                          "full-frame effect, a graphic or a text treatment "
                          "would otherwise have captions burned across it. "
                          "spans is the COMPLETE list of muted windows as "
                          "[[start, end], ...] in program seconds; it "
                          "REPLACES the existing list, and spans=[] turns "
                          "every caption back on. The audio and the cut are "
                          "untouched — only the burned text is hidden. Not "
                          "needed for inserted media or title cards (they "
                          "are never captioned to begin with).",
                          {"spans": {"type": "array",
                                     "items": {"type": "array",
                                               "items": {"type": "number"}}}}),
    "add_stylize": (add_stylize, "Layer a windowed finishing effect on the "
                    "program picture: 'grain' (film grain), 'vignette' "
                    "(darkened corners), 'glow' (soft bloom), 'chromatic' "
                    "(RGB fringe), 'dream_blur' (soft dreamy diffusion), "
                    "'vhs' (tape look), 'flash' (strobe pop), 'shake' "
                    "(camera shake). start/end are PROGRAM seconds — omit "
                    "both for the whole video. intensity 0.05-1.0 (default "
                    "0.5). Content-anchored: a stylized moment follows its "
                    "footage through later cuts. One or two layered "
                    "effects read as a look; five read as a broken TV.",
                    {"kind": {"type": "string",
                              "enum": list(STYLIZE_KINDS)},
                     "start": {"type": "number"},
                     "end": {"type": "number"},
                     "intensity": {"type": "number"}}),
    "remove_stylize": (remove_stylize, "Remove one stylize effect by its id "
                       "(see get_edl).", {"id": {"type": "string"}}),
    "set_grade_custom": (set_grade_custom, "Continuous color controls on "
                         "all footage, applied AFTER the preset grade (the "
                         "two compose — 'cinematic but warmer' = preset "
                         "cinematic + temperature 0.2): exposure -1..1, "
                         "contrast 0.5..1.6 (1.0 neutral), saturation 0..2 "
                         "(1.0 neutral), temperature -1 (cool)..1 (warm), "
                         "tint -1 (green)..1 (magenta). Pass ONLY the axes "
                         "to change; an axis's neutral value clears it; "
                         "all axes neutral clears the whole custom grade. "
                         "Captions and graphics are never graded.",
                         {"exposure": {"type": "number"},
                          "contrast": {"type": "number"},
                          "saturation": {"type": "number"},
                          "temperature": {"type": "number"},
                          "tint": {"type": "number"}}),
    "set_master_loudness": (set_master_loudness, "enabled=true normalizes "
                            "the FINAL MIX to -14 LUFS / -1.5 dBTP (the "
                            "social/streaming loudness target) on preview "
                            "AND export — the fix for 'the export sounds "
                            "quiet on TikTok/YouTube'. It changes loudness, "
                            "not the voice/music/sfx balance. false removes "
                            "mastering.",
                            {"enabled": {"type": "boolean"}}),
    "get_audio_analysis": (get_audio_analysis, "READ: measured musical/"
                           "energy analysis of the source audio (cached "
                           "after the first call): tempo (BPM + confidence "
                           "— below 0.5 the pulse is unreliable and "
                           "beat_align_cuts refuses), the beat grid, where "
                           "the loudest/quietest sections and the biggest "
                           "energy rise sit, and the most vocally STRESSED "
                           "words with timestamps. Times are SOURCE "
                           "seconds. Call before beat_align_cuts / "
                           "punch_in_on_emphasis / sound_design_pass, or "
                           "to answer 'what's the tempo'. Pass asset_key "
                           "(an uploaded music file or library: track) to "
                           "analyze that instead — e.g. to find the drop "
                           "for add_music offset_s.",
                           {"asset_key": {"type": "string"}}),
    "punch_in_on_emphasis": (punch_in_on_emphasis, "ONE-CALL emphasis "
                             "zooms: writes punch zooms on the N most "
                             "vocally STRESSED words that survive the "
                             "current cut (stress measured from the audio, "
                             "times from the real word timestamps — never "
                             "guessed), spaced >=4s apart, in one EDL "
                             "version. count 1-8 (default 4); strength "
                             "0.05-1.5 (default 0.35). The result lists "
                             "each word + program time — report those to "
                             "the user. THE tool for 'add zooms on the "
                             "important moments'.",
                             {"count": {"type": "integer"},
                              "strength": {"type": "number"}}),
    "sound_design_pass": (sound_design_pass, "ONE-CALL sound design from "
                          "the built-in pack, in one version: a whoosh on "
                          "cut junctions (spaced >=5s), one impact on the "
                          "strongest stressed word, one riser resolving "
                          "INTO the biggest energy rise. intensity: "
                          "'light' (2 placements), 'medium' (4), 'strong' "
                          "(6). Never stacks within 1.5s of an existing "
                          "sound. The result lists every placement (sound "
                          "@ time) — report them. For hand-placed accents "
                          "use add_sfx; for custom sounds generate_sfx.",
                          {"intensity": {"type": "string",
                                         "enum": ["light", "medium",
                                                  "strong"]}}),
    "beat_align_cuts": (beat_align_cuts, "THE tool for 'cut to the beat'. "
                        "Slides each INTERNAL cut (never the program's first "
                        "start / last end) onto the nearest beat within "
                        "tolerance_s (default 0.35s), skipping any move that "
                        "would land inside a word. WHICH beat: when the edit "
                        "has music it uses the SONG the viewer hears, in "
                        "program time — that is what 'the beat' means; "
                        "source='video' forces the footage's own audio "
                        "instead. If the USER tells you the tempo ('there's "
                        "a beat every second', 'it's 120 BPM'), pass "
                        "every_s=1 or bpm=120 — their tempo is data and "
                        "skips the confidence gate. With no music, no stated "
                        "tempo and no clear pulse in the footage it refuses "
                        "honestly rather than 'syncing' to noise — never "
                        "invent a tempo yourself. Cuts must already exist: "
                        "this MOVES boundaries, it does not create them (to "
                        "cut ON every beat, build the spans with "
                        "keep_segments from the beat times get_audio_analysis "
                        "reports, then call this to tighten them). One EDL "
                        "version; reports moved/skipped counts.",
                        {"tolerance_s": {"type": "number"},
                         "source": {"type": "string",
                                    "enum": ["music", "video"]},
                         "bpm": {"type": "number"},
                         "every_s": {"type": "number"}}),
    "suggest_emphasis": (suggest_emphasis, "READ: candidate emphasis words "
                         "from the REAL transcript — the most vocally "
                         "stressed words (measured), words with digits, "
                         "and rare/distinctive words — as a verbatim list "
                         "to pass to add_captions / set_caption_style "
                         "emphasis_words.", {}),
    "apply_look": (apply_look, "ONE-CALL aesthetic: composes caption "
                   "preset + grade + custom grade + transitions + fades + "
                   "stylize in a single EDL version and reports every "
                   "component it set. Looks: 'hype' (beast xl captions, "
                   "vibrant grade, zoom_punch cuts, closing fade), 'clean' "
                   "(podcast captions, ungraded, gentle fades), "
                   "'cinematic' (elegant captions, cinematic grade + "
                   "slight warmth, 1s fades, dip_black), 'luxury' (luxe "
                   "captions, warm grade + temperature lift, long fades), "
                   "'meme' (impact xl captions, flash cuts, grain). "
                   "Preserves existing emphasis_words, else picks them "
                   "from the transcript. Never touches cuts, music or sfx "
                   "— offer sound_design_pass separately for audio "
                   "accents. Every component can be adjusted afterwards "
                   "with its own tool.",
                   {"name": {"type": "string",
                             "enum": sorted(LOOKS)}}),
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
    "extract_audio": ["asset_key"],
    "add_sfx": ["storage_key", "at"],
    "move_sfx": ["id", "at"],
    "remove_sfx": ["id"],
    "swap_music": ["id", "storage_key"],
    "set_music_fit": ["id"],
    "remove_music": ["id"],
    "set_audio_gain": ["kind", "id", "gain_db"],
    "set_volume": ["start", "end", "gain_db"],
    "set_frame": ["ratio"],
    "auto_reframe": ["ratio"],
    "record_website": ["url"],
    "record_website_demo": ["url", "steps"],
    "showcase_demo": ["asset_key"],
    "search_stock": ["query"],
    "add_stock_media": ["id"],
    "insert_media": ["asset_key", "at_output_s"],
    "remove_insert": ["id"],
    "set_color_grade": ["preset"],
    "add_zoom": ["start", "end"],
    "remove_zoom": ["id"],
    "set_transitions": ["style"],
    "blur_region": ["x", "y", "w", "h"],
    "erase_region": ["x", "y", "w", "h"],
    "add_voiceover": ["asset_key"],
    "remove_voiceover": ["id"],
    "set_speed": ["start", "end", "factor"],
    "remove_speed": ["id"],
    "add_overlay": ["asset_key", "start"],
    "move_overlay": ["id"],
    "remove_overlay": ["id"],
    "add_text": ["text", "start", "end"],
    "remove_text": ["id"],
    "add_title_card": ["text", "at_output_s"],
    "add_color_screen": ["at_output_s"],
    "add_corrupt_screen": ["at_output_s"],
    "set_caption_mutes": ["spans"],
    "add_stylize": ["kind"],
    "remove_stylize": ["id"],
    "set_grade_custom": [],
    "set_master_loudness": ["enabled"],
    "get_audio_analysis": [],
    "punch_in_on_emphasis": [],
    "sound_design_pass": [],
    "beat_align_cuts": [],
    "suggest_emphasis": [],
    "apply_look": ["name"],
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
               "swap_music", "set_music_fit", "extract_audio",
               "add_sfx", "move_sfx", "remove_sfx",
               "set_audio_gain", "set_volume", "set_frame", "auto_reframe",
               "record_website", "record_website_demo", "showcase_demo",
               "add_stock_media",
               "insert_media", "remove_insert", "add_voiceover",
               "remove_voiceover", "set_color_grade", "add_zoom",
               "remove_zoom", "set_fades", "set_transitions",
               "blur_region", "remove_blur",
               "erase_burned_text", "erase_region", "remove_erase",
               "reset_edit",
               "set_speed", "remove_speed",
               "add_overlay", "move_overlay", "remove_overlay",
               "add_text", "remove_text",
               "add_title_card", "add_color_screen", "add_corrupt_screen",
               "set_caption_mutes",
               "add_stylize", "remove_stylize",
               "set_grade_custom", "set_master_loudness",
               "punch_in_on_emphasis", "sound_design_pass",
               "beat_align_cuts", "apply_look",
               "generate_image",
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
    if name in ("record_website", "record_website_demo"):
        return not webrecord.available()
    # showcase_demo can only ever operate on a capture the demo recorder
    # made, so where that is off it has nothing to act on and must not be
    # advertised — the same honest-off contract as every other tool here.
    if name == "showcase_demo":
        return not webrecord.available()
    if name in ("search_stock", "add_stock_media"):
        return not stock.available()
    # Same rule for the music library: a deployment whose image shipped no
    # tracks must not advertise one, or the agent offers music it cannot
    # deliver and then has to walk it back.
    if name == "list_music_library":
        return not music_library.CATALOG
    if name == "list_sfx_library":
        return not sfx_library.CATALOG
    # The director pass places bundled sounds — with no pack shipped it can
    # only reject, so it must not be advertised (same rule as the libraries).
    if name == "sound_design_pass":
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
        # Cross-references to a HIDDEN tool must vanish with it, or the
        # model is pointed at a capability that no longer exists this
        # deployment (apply_look's description names sound_design_pass).
        if not sfx_library.CATALOG:
            desc = desc.replace(
                "Never touches cuts, music or sfx — offer sound_design_pass "
                "separately for audio accents. ",
                "Never touches cuts, music or sfx. ").replace(
                "Call before beat_align_cuts / punch_in_on_emphasis / "
                "sound_design_pass, or ",
                "Call before beat_align_cuts / punch_in_on_emphasis, or ")
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
