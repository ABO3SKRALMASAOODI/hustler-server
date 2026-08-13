"""Agent tools. Every argument is validated and clamped, every error is a
short instructive string the model can act on, every output fits the token
budget. Write tools create new EDL versions and return one-line diffs."""

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid

import audio_qc
import audit
import captions as caplib
import config
import db as dbx
import inpaint
import llm
import matte
import graphics
import personseg
import media
import model_prices
import music_search
import net_fetch
import perception
import preview_critic
import quality_gate
# The takeover's geometry (how far the camera travels, where it aims) is
# renderer arithmetic, and the tool has to quote the SAME numbers the graph
# will use — importing the resolver is the only way those two cannot drift.
import renderer
import sfx_search
import sheets
import song_find
import spatial
import stock
import storage
import subject
import taste
import videogen
import cursor as cursorlib
import screendet
import screenframe
import screenmatch
import edl_diff
import timeline as timeline_mod
import tracker
import travel
import url_media
import remote
import ytaccess
import visual
import webrecord
from captions import CAPTION_DESIGN_VERSION, KARAOKE_HARD_MAX
from schemas import (CANVAS_DIMS, CaptionStyle, clean_fingerprint,
                     custom_chain_error, patch_fingerprint,
                     EDLValidationError, Frame,
                     HEX_COLOR,
                     canvas_edl, clip_anim, default_edl, describe_edl,
                     DEFAULT_CANVAS_FPS,
                     edl_signature, is_canvas_program, keep_boundaries,
                     output_duration, program_duration, validate_edl,
                     GAIN_MIN_DB, GAIN_MAX_DB,
                     INSERT_RATE_MIN, INSERT_RATE_MAX,
                     GRADE_PRESETS, TRANSITION_STYLES, TRANSITION_MIN_S,
                     TRANSITION_MAX_S, TRANSITION_SCOPES,
                     OVERLAY_ANIMS, OVERLAY_SCALE_MIN,
                     OVERLAY_SCALE_MAX, SPEED_FACTOR_MIN, SPEED_FACTOR_MAX,
                     STYLIZE_KINDS, TEXT_ANIMS, TEXT_FONTS, TEXT_TEMPLATES,
                     ZOOM_STRENGTH_MIN, ZOOM_STRENGTH_MAX,
                     CURSOR_SCALE_MIN, CURSOR_SCALE_MAX,
                     FRAME_SHIFT_RATIOS, FRAME_SHIFT_MIN_S, FRAME_SHIFT_MAX_S,
                     SCREEN_FRAME_INSET_MIN, SCREEN_FRAME_INSET_MAX,
                     SCREEN_FRAME_RADIUS_MAX,
                     SCREEN_QUAD_MIN_FRAC, SCREEN_TAKEOVER_MIN_S,
                     SCREEN_TAKEOVER_MAX_S, quad_bbox, quad_is_sane)
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
        self._spatial = None          # face/text/UI track, cached
        self._asset_perception = {}   # asset/library key -> audio analysis
        self._music_hits = {}         # search_music results this turn, by id
        self._sfx_hits = {}           # search_sfx results this turn, by id
        self.last_preview = None      # set by render_preview
        # Frames a look tool captured for whoever is DOING THE EDITING this
        # step (round 67): [(label, jpeg_path)].
        #
        # Two consumers, and they were not equal until round 83e. The agent
        # loop injects them as image parts in a user message right after the
        # tool results (direct_sight, gated on llm.agent_sees). The MCP
        # surface could not — "an MCP tool call's result is text", said the
        # comment that used to live here — so an outside model got a PARAGRAPH
        # from our vision model describing frames it never saw. That was true
        # of the plumbing, never of the protocol: a tools/call result carries
        # image content perfectly well. sight_out says the caller takes the
        # pictures themselves, and mcp_exec drains them into the reply.
        self.pending_images = []
        # Exact moments whose pixels were actually delivered this turn. These
        # remain useful provenance for quality notes, never tool permission.
        self._looked_source_times = set()
        self._looked_output_times = set()
        self._looked_asset_times = {}
        self._pending_looked_source_times = set()
        self._pending_looked_output_times = set()
        self._pending_looked_asset_times = {}
        self.direct_sight = False
        self.sight_out = False
        # The edit plan the agent recorded for THIS turn (set_edit_plan,
        # round 98): {"brief": str|None, "steps": [str]}. Working memory,
        # not EDL state — it rides continuation passes so a resumed turn
        # finishes what was planned instead of re-deciding it, and the
        # activity feed shows the user what the editor intends to do.
        self.edit_plan = None
        self.last_audio_qc_findings = []
        # EDL versions this turn already enqueued a SPECULATIVE preview for
        # (round 98) — the loop's fire-ahead encode. Bounds repeats and lets
        # render_preview adopt instead of re-enqueueing.
        self.spec_enqueued = set()
        self.last_selfcheck = None    # vision one-liner from the last preview
        # Craft findings from the most recent REAL preview render, and the EDL
        # version they were measured on. They are returned as evidence and
        # handoff metadata, never as write, reply, preview, or export locks.
        self.last_taste = []
        self.last_taste_version = None
        self.last_visual_critic = None
        # What the user asked for THIS turn, verbatim. Read only to SUPPRESS
        # taste findings (round 52): a fade from black is a defect on a reel
        # right up until the moment somebody asks for one, and a critic that
        # argues with an explicit instruction is worse than no critic.
        self.user_message = ""
        self.versions_written = []    # EDL versions created this turn
        # The last write's structural diff (edl_diff.change_ranges) plus the
        # version it produced — attached to that write's activity row so the
        # studio can flash the changed output ranges. Consumed (cleared) by
        # the loop the moment it is attached; never blocks a write.
        self.last_change = None
        # Every EDL state visited this turn -> the version it was first seen
        # at. A write that lands on a state already in here is a CYCLE: the
        # turn has undone itself and is about to repeat the same attempt.
        # See write_edl for why this is reported rather than blocked.
        self._states_seen = {}
        self.rendered_versions = set()  # versions with a successful preview
        # A failed physical render is evidence about one immutable EDL
        # version.  Never enqueue that same version again; a repair is a new
        # EDL write and therefore a new version/job/cache key.
        self.failed_preview_versions = {}
        self.last_preview_failure = None
        # Short proof reels cover only the output seconds changed since the
        # last complete preview. They are evidence for the editor and must not
        # masquerade as the complete preview the Studio player adopts.
        self.checked_versions = set()
        self.last_preview_check = None
        # A speculative changed-section proof may finish during the model's
        # next reasoning call. Keep its exact row so render_preview adopts it
        # instead of paying for the same proof twice.
        self.spec_preview_check_jobs = {}
        # A speculative preview may become terminal before the model reaches
        # render_preview. Remember its row, not merely that it was enqueued,
        # so the later tool call adopts the success/failure instead of
        # creating a second physical job for the same immutable version.
        self.spec_preview_jobs = {}
        self.autorendered = False     # loop set: model skipped render_preview
        # Round 91 grade contact strips: iterating a color against ~2s strips
        # instead of full renders. last_strip_chain is the grade chain the
        # most recent strip showed — asking to render the SAME colors again
        # means the model has settled, so THAT call gets the real render.
        # autorendering marks the turn-end honesty render, which must always
        # be real: its result is for the USER, and a strip's pending image
        # would have no next step to be seen in.
        self.last_strip_chain = None
        self.strip_count = 0
        self.autorendering = False
        self.write_calls = []         # successful write tool names this turn
        self.images_generated = []    # assets created by generate_image
        self.videos_generated = []    # clips created by generate_video
        self.urls_fetched = []        # assets created by fetch_url
        self.web_recordings = []      # assets created by record_website
        # Audio lifted out of an uploaded VIDEO (extract_audio, or any audio
        # tool handed a clip). A real project asset, so a turn that only did
        # this did NOT do nothing — the honesty layer reads it.
        self.audio_extracted = []
        # Online music/SFX downloads are real project assets too. Track them
        # separately from extraction so outcome billing can distinguish a
        # useful fetch-only turn from one whose provider call failed.
        self.audio_fetched = []
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
        #   {model: {"in": n, "out": n, "cached": n, "reasoning": n,
        #            "audio_in": n, "audio_out": n}}
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
        # Agent-loop outcome accounting.  A tool refusal is often our model
        # dialect or capability mismatch, not value delivered to the user.
        # The loop records the outer call (a recipe counts once, not once per
        # staged operation) and uses these facts to avoid charging a turn that
        # attempted an edit but produced no edit, asset, or proof.
        self.turn_tool_outcomes = []
        self.write_attempts = 0

    def add_usage(self, model, tokens_in, tokens_out, cached_in=0,
                  reasoning=0, audio_in=0, audio_out=0):
        """Record one model call's usage, for the in-turn spend cap."""
        self.tokens_in += tokens_in or 0
        self.tokens_out += tokens_out or 0
        self.tokens_cached_in += cached_in or 0
        slot = self.model_usage.setdefault(
            (model or "").strip().lower(),
            {"in": 0, "out": 0, "cached": 0, "reasoning": 0,
             "audio_in": 0, "audio_out": 0})
        slot["in"] += tokens_in or 0
        slot["out"] += tokens_out or 0
        slot["cached"] += cached_in or 0
        slot["reasoning"] += reasoning or 0
        slot["audio_in"] += audio_in or 0
        slot["audio_out"] += audio_out or 0

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
                audio_in = min(max(u.get("audio_in", 0), 0), u["in"])
                audio_out = min(max(u.get("audio_out", 0), 0), u["out"])
                cached = min(max(u["cached"], 0), u["in"] - audio_in)
                out = (u["out"] - audio_out
                       + (u["reasoning"]
                          if p.get("reasoning_separate") else 0))
                cost += ((u["in"] - audio_in - cached) * p["in"]
                         + cached * p["cached_in"]
                         + audio_in * p["audio_in"]
                         + out * p["out"]
                         + audio_out * p["audio_out"]) / 1e6
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
        advisories = quality_gate.advisory_findings(
            prev["json"], normalized, self.user_message)
        if edl_signature(normalized) == edl_signature(prev["json"]):
            return (f"NO CHANGE — the EDL is identical to v{prev['version']}; "
                    "the requested change may need a different tool or may "
                    "not be supported. Do NOT tell the user you changed "
                    "anything.")
        sig = edl_signature(normalized)
        version = self.db.run(dbx.insert_edl, self.project_id, normalized,
                              "agent")
        self.versions_written.append(version)
        chg = edl_diff.change_ranges(prev["json"], normalized)
        self.last_change = dict(chg, edl_version=version) if chg else None
        before = describe_edl(prev["json"])
        after = describe_edl(normalized, self.duration)
        line = (f"EDL v{prev['version']} -> v{version}: {change_desc}. "
                f"Before: {before}. After: {after}.")
        if advisories:
            line += "\n" + quality_gate.advisory_message(version, advisories)

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
                f"\nLOOP ADVISORY: this is the same edit as v{seen_at}, which "
                "you already produced this turn — everything since then has "
                "cancelled out. This does not block another write or forbid "
                "an intentional revisit. Consider checking get_edl or the "
                "relevant rendered moment, then repeat, change route, or keep "
                "this state according to your judgment.")
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

def program_name_of(ctx):
    """asset_key -> display filename for the scene map (timeline.
    describe_program), cached on the ctx so a turn does at most one DB
    lookup per distinct key. Returns None for unknown keys — the caller
    falls back to the key's basename."""
    cache = getattr(ctx, "_asset_names", None)
    if cache is None:
        cache = ctx._asset_names = {}

    def name_of(key):
        if key not in cache:
            try:
                a = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
                cache[key] = (a.get("meta") or {}).get("filename") \
                    if a else None
            except Exception:
                cache[key] = None
        return cache[key]
    return name_of


def _program_map(ctx, edl_json):
    """The viewer-ordered scene listing for this EDL, or ''."""
    try:
        return timeline_mod.describe_program(edl_json, program_name_of(ctx))
    except Exception:
        return ""


def get_video_info(ctx):
    if not ctx.has_main_video:
        edl = ctx.latest_edl()
        ins = edl["json"].get("inserts") or []
        prog = _program_map(ctx, edl["json"])
        return ("No main video in this project — this is a blank canvas. Build "
                "the program from generated or uploaded images/clips: create "
                "with generate_image / generate_video, then place with "
                "insert_media. "
                f"Current EDL v{edl['version']}: {len(ins)} placed "
                f"clip{'s' if len(ins) != 1 else ''}, "
                f"{program_duration(edl['json'])}s total."
                + (f"\n{prog}" if prog else ""))
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
    words = ctx.index.get("words", [])
    n_spk = ctx.index.get("speakers") or 0
    spk_txt = (f", {n_spk} speakers (labelled S0..S{n_spk - 1} in "
               "get_transcript)" if n_spk > 1 else "")
    fillers = [w for w in words if w.get("filler")]
    n_fill = len(fillers)
    shown_fillers = ", ".join(
        f"'{w.get('w', '')}' @{float(w.get('t0', 0)):g}-"
        f"{float(w.get('t1', 0)):g}s" for w in fillers[:8])
    if n_fill > 8:
        shown_fillers += f", +{n_fill - 8} more"
    fill_txt = (
        f", {n_fill} filler sound(s) ({shown_fillers}) — "
        "remove_filler_words() already has these exact indexed spans; "
        "do NOT call get_words first"
        if n_fill else "")
    n_mom = len(ctx.index.get("moments") or [])
    mom_txt = (f", {n_mom} sampled frames described (get_shots shows what is "
               "on screen over time)" if n_mom else "")
    prog = _program_map(ctx, edl["json"])
    return (f"duration={v['duration']}s, {v['width']}x{v['height']} @ "
            f"{v['fps']}fps, audio={'yes' if v['has_audio'] else 'NO'}. "
            f"{len(ctx.index.get('shots', []))} shots{mom_txt}, "
            f"{len(ctx.index.get('sentences', []))} sentences / "
            f"{len(words)} words{spk_txt}{fill_txt}, "
            f"{gap_txt}. "
            f"Current EDL v{edl['version']}: "
            f"{describe_edl(edl['json'], v['duration'])}."
            + (f"\n{prog}" if prog else ""))


def get_transcript(ctx, start=0, end=None, asset_key=None):
    if asset_key:
        # Round 84: every uploaded clip/music file is indexed too — read ITS
        # transcript by the asset's sha. Times are CLIP seconds.
        asset = ctx.db.run(
            lambda conn: dbx.asset_by_key(conn, ctx.project_id, asset_key))
        if not asset:
            return f"REJECTED: no asset with storage_key {asset_key!r}."
        row = asset.get("sha256") and \
            ctx.db.run(dbx.get_index_by_sha, asset["sha256"])
        idx = (row or {}).get("json") or {}
        sents = idx.get("sentences") or []
        if not sents:
            return ("That upload has no transcript (no speech, no audio "
                    "track, or its analysis has not finished yet).")
        multi = (idx.get("speakers") or 0) > 1
        out = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}]"
               + (f" S{s['speaker']}:" if multi
                  and s.get("speaker") is not None else "")
               + f" {s['text']}" for s in sents]
        return _cap(f"Transcript of {asset_key} (CLIP seconds):\n"
                    + "\n".join(out),
                    budget=config.TRANSCRIPT_CHAR_BUDGET)
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
    # Only label speakers when there is more than one — "S0:" on every line of
    # a solo talking head is noise the model pays for on every read.
    multi = (ctx.index.get("speakers") or 0) > 1
    out = [f"[{s['id']} {_fmt_t(s['t0'])}-{_fmt_t(s['t1'])}]"
           + (f" S{s['speaker']}:" if multi and s.get("speaker") is not None
              else "")
           + f" {s['text']}"
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


def classify_repeated_phrases(out_words, shingle=4):
    """Separate edit duplication from words the speaker actually repeated.

    Timeline.kept_words carries the original source clock. If the same phrase
    appears twice in program time with the same source start, an EDL segment
    was duplicated. Distinct source starts mean the speaker said it twice;
    that may be rhetorical, a stutter, or two takes and is not proof of an
    editing bug.
    """
    rows = [( _norm_token(w.get("w")), w) for w in out_words]
    rows = [(token, word) for token, word in rows if token]
    results = []
    for phrase, _times in find_repeated_phrases(out_words, shingle=shingle):
        needle = phrase.split()
        occ = []
        for i in range(len(rows) - len(needle) + 1):
            if [token for token, _word in rows[i:i + len(needle)]] != needle:
                continue
            word = rows[i][1]
            occ.append({"program_s": round(float(word.get("t0", 0.0)), 3),
                        "source_s": (round(float(word["src_t0"]), 3)
                                     if word.get("src_t0") is not None
                                     else None)})
        source_times = [x["source_s"] for x in occ if x["source_s"] is not None]
        duplicated_source = any(
            abs(a - b) <= 0.08
            for i, a in enumerate(source_times)
            for b in source_times[i + 1:])
        kind = ("edit_duplicate" if duplicated_source else
                "spoken_repetition" if len(source_times) >= 2 else
                "ambiguous")
        results.append({"phrase": phrase, "kind": kind,
                        "occurrences": occ})
    return results


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
    reps = classify_repeated_phrases(out_words)
    if reps:
        duplicate = [r for r in reps if r["kind"] == "edit_duplicate"]
        spoken = [r for r in reps if r["kind"] == "spoken_repetition"]
        parts = []
        if duplicate:
            parts.append("\nEDIT DUPLICATIONS (same source phrase used more "
                         "than once—repair the EDL):\n" + "\n".join(
                f"  '{r['phrase']}' at " + ", ".join(
                    f"{o['program_s']}s" for o in r["occurrences"])
                for r in duplicate[:6]))
        if spoken:
            parts.append("\nSPOKEN REPETITIONS (distinct source moments—not "
                         "proof of an edit bug; preserve rhetorical repeats "
                         "unless the brief asks to tighten them):\n" + "\n".join(
                f"  '{r['phrase']}' at " + ", ".join(
                    f"{o['program_s']}s" for o in r["occurrences"])
                for r in spoken[:6]))
        note = ("\nPOSSIBLE REPETITIONS (classified by source provenance):"
                + "".join(parts))
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
    multi = (ctx.index.get("speakers") or 0) > 1
    out = [f"{_fmt_t(w['t0'])}-{_fmt_t(w['t1'])} {w['w']}"
           + (f" [S{w['speaker']}]" if multi and w.get("speaker") is not None
              else "")
           + (" [filler]" if w.get("filler") else "")
           for w in shown]
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


# How many lines of visual timeline one get_shots call may return. Past this
# the shortest moments are dropped with a pointer to a narrower range — an
# unbounded timeline on a long video would bury the transcript in the same
# context window.
SHOT_TIMELINE_MAX_LINES = 60


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
    # Round 69: shots are SCENE structure — on a locked-off talking head there
    # is exactly one of them, and one line describing 19 minutes of footage was
    # the whole of what the editor could see. The sampled timeline below says
    # what is on screen over time, collapsed so that each line is a CHANGE.
    tl = visual.timeline_lines(ctx.index.get("moments") or [], start, end,
                               max_lines=SHOT_TIMELINE_MAX_LINES)
    if tl:
        step = config.VISUAL_SAMPLE_S
        lines.append(f"\nWHAT IS ON SCREEN OVER TIME (sampled ~every {step:g}s, "
                     "consecutive identical frames merged — a span means "
                     "nothing changed through it):")
        lines += tl
    elif not any(s.get("caption") for s in rows):
        # v10 indexes carry no captions by design: the PICTURE is the
        # filmstrip in your context and look_at. These timings are the cut
        # geometry only — never pick scenes or zoom targets from them alone.
        lines.append(
            "\nThese are scene-change timings only. WHAT IS ON SCREEN is in "
            "your filmstrip tiles (timestamps under each frame) — read those, "
            "or look_at exact times for a closer view, before choosing "
            "anything visual.")
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
            return ("No audio uploaded to this project — but you can FIND "
                    "music yourself: search_music looks up tracks online "
                    "(by vibe/genre) and fetch_music downloads one, ready "
                    "for add_music; fetch_url ingests any music link the "
                    "user pastes (song URL, YouTube, SoundCloud...); "
                    "search_sfx finds one-shot sound effects. Only "
                    "ask the user to attach a file (paperclip button in "
                    "chat, mp3/wav/m4a) for a track the web cannot reach — "
                    "e.g. a trending platform sound, which only they can "
                    "provide.")
        return f"No {kind} assets in this project."
    lines = []
    for a in rows:
        m = a.get("meta") or {}
        dur = f", {a['duration_s']:.1f}s" if a.get("duration_s") else ""
        cap = f" — {m['caption'][:120]}" if m.get("caption") else ""
        role = m.get("role") or ""
        role_bit = (f" ROLE={role} — STYLE REFERENCE, not footage: "
                    "look_at_asset / extract_audio, never insert_media"
                    if role in ("edit_reference", "shorts_reference")
                    else "")
        lines.append(f"[{a['kind']}] storage_key={a['storage_key']} "
                     f"\"{m.get('filename', '?')}\"{dur}{cap}{role_bit}")
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


def _deliver_frames(ctx, frames, labels, question, subject_line):
    """The round-67 direct-sight tail shared by look_at / look_at_asset.

    When the agent model itself reads images (llm.agent_sees), the captured
    frames are assembled into ONE timestamp-labeled picture (or passed as the
    single frame) and queued on ctx.pending_images — the loop injects it into
    the AGENT'S OWN context right after this tool result, so the editor sees
    the footage with its own eyes instead of reading a second-hand
    description. A blind agent model (or a queueing failure) falls back to
    the separate vision provider exactly as before round 67."""
    # Round 72: every delivered frame carries a faint tenths grid with edge
    # labels — burned onto COPIES, sources untouched. Aimed tools (add_zoom
    # cx/cy/rect, add_zoom_path, blur_region, text placement) take fractions
    # of the frame, and an unmarked picture made those numbers eyeball
    # guesses: a real edit aimed a zoom at (0.13, 0.48) for a message that
    # sat at y=0.78. On the grid a coordinate is READ, not estimated — and
    # the fallback vision provider gets the same calibration.
    try:
        gridded = [sheets.overlay_coord_grid(
            fp, os.path.join(ctx.workdir,
                             f"grid_{uuid.uuid4().hex[:8]}.jpg"))
            for fp in frames]
    except Exception:
        gridded = frames
    # sight_out (MCP): the model on the other end reads the pictures itself,
    # so there is nothing to gate on OUR agent model — and nothing to pay a
    # vision model for. direct_sight (the in-house loop): only if this model
    # can take image parts at all.
    if getattr(ctx, "sight_out", False) or \
            (getattr(ctx, "direct_sight", False)
             and llm.agent_sees(ctx.agent_model)):
        try:
            if len(gridded) == 1:
                sheet = gridded[0]
            else:
                sheet = os.path.join(
                    ctx.workdir, f"look_sheet_{uuid.uuid4().hex[:8]}.jpg")
                sheets.build_timestamp_sheet(list(zip(labels, gridded)),
                                             sheet)
            ctx.pending_images.append(
                (f"{subject_line} — {', '.join(labels)}", sheet))
            return (f"Captured {len(frames)} frame(s): {', '.join(labels)}. "
                    "The picture follows this message — LOOK AT IT YOURSELF "
                    "and answer from what you see"
                    + (f" (your question: {question})" if question else "")
                    + ". Timestamps are printed under each tile. The faint "
                    "grid marks TENTHS of the frame ((0,0) = top-left, "
                    "labels .2/.4/.6/.8): read zoom aims, rects and "
                    "positions straight off it.")
        except Exception as ex:
            print(f"[look] direct-sight assembly failed ({ex}); "
                  "falling back to the vision provider", flush=True)
    if not llm.vision_available():
        return ("Visual inspection unavailable (no vision model configured). "
                "Decide from the transcript, silences, and shot captions.")
    answer = llm.ask_vision(
        f"{subject_line}. Frames: {', '.join(labels)}. Question from the "
        f"editor: {question or 'describe what is on screen, concretely'}\n"
        "A faint white grid on each frame marks tenths ((0,0) = top-left, "
        "edge labels .2/.4/.6/.8) — give any position or box as 0-1 "
        "fractions read from it.\n"
        "Answer concisely and concretely.",
        gridded, purpose="vision_look", image_names=labels)
    return _cap(answer or "The vision model did not return an answer; "
                          "proceed using the transcript and shot captions.")


def _fit_and_zoom_frame(workdir, idx, fp, t, canvas, mode, focus, zooms,
                        prog_end, is_main, crop=None, fit=None):
    """The round-72 geometry step of _look_at_output: one decoded frame ->
    what the RENDER shows at that output second. Fit first (the same
    cover-crop / letterbox _normalize_video applies, mirrored by
    renderer.fit_fractions — focus reaches only main footage, exactly like
    the render), then the shared zoompan's viewport at t
    (renderer.zoom_state_at) cropped and blown back up. Returns
    (path, label_suffix); the caller keeps the raw frame on any failure — a
    look degrades, it never dies.

    crop (round 77): the block's InsertItem.crop — the render cuts that
    region out FIRST and letterboxes it, so the preview must do exactly the
    same or the agent aims zooms at pixels the viewer never sees."""
    from PIL import Image, ImageFilter

    z, zcx, zcy = renderer.zoom_state_at(zooms, t, prog_end)
    img = Image.open(fp).convert("RGB")
    changed = False
    if fit:                          # round 79 — per-insert fit override,
        mode = fit                   # exactly like the renderer's imode
    if crop and len(crop) == 4:
        w, h = img.size
        img = img.crop((round(float(crop[0]) * w), round(float(crop[1]) * h),
                        round(float(crop[2]) * w),
                        round(float(crop[3]) * h)))
        mode = "pad"                 # the render letterboxes a cropped strip
        changed = True
    if canvas:
        w, h = img.size
        ow = 640
        oh = max(2, round(ow * canvas[1] / canvas[0]))
        kind, x0, y0, x1, y1 = renderer.fit_fractions(
            w, h, canvas[0], canvas[1], mode, focus if is_main else None)
        if x1 - x0 < 0.999 or y1 - y0 < 0.999:
            if kind == "crop":
                img = img.crop((round(x0 * w), round(y0 * h),
                                round(x1 * w), round(y1 * h))) \
                         .resize((ow, oh), Image.LANCZOS)
            else:
                if (mode or "crop") == "pad_blur":
                    _bk, bx0, by0, bx1, by1 = renderer.fit_fractions(
                        w, h, canvas[0], canvas[1], "crop", None)
                    base = img.crop((round(bx0 * w), round(by0 * h),
                                     round(bx1 * w), round(by1 * h))) \
                        .resize((ow, oh), Image.LANCZOS) \
                        .filter(ImageFilter.GaussianBlur(14))
                else:
                    base = Image.new("RGB", (ow, oh), (0, 0, 0))
                fg = img.resize((max(1, round(ow * (x1 - x0))),
                                 max(1, round(oh * (y1 - y0)))),
                                Image.LANCZOS)
                base.paste(fg, (round(ow * x0), round(oh * y0)))
                img = base
            changed = True
    suffix = ""
    if z > 1.005:
        w, h = img.size
        vx0 = (1.0 - 1.0 / z) * zcx
        vy0 = (1.0 - 1.0 / z) * zcy
        img = img.crop((round(vx0 * w), round(vy0 * h),
                        round((vx0 + 1.0 / z) * w),
                        round((vy0 + 1.0 / z) * h))) \
                 .resize((w, h), Image.LANCZOS)
        suffix = f" [{z:.2f}x zoom on screen]"
        changed = True
    if not changed:
        return fp, suffix
    out = os.path.join(workdir, f"lookout_geo{idx}.jpg")
    img.save(out, "JPEG", quality=88)
    return out, suffix


def _look_at_output(ctx, output_times, question):
    """Frames of the ASSEMBLED PROGRAM at output seconds — round 71.

    The user watches the OUTPUT, and until now the agent could only see the
    source: look_at sampled the main video's clock, so a spliced insert (the
    user's "second scene") was invisible, and after cuts every output second
    named a different source second. This resolves each requested output time
    through the current EDL — a time inside kept footage samples the main
    video at the mapped source second; a time inside an insert samples the
    inserted clip itself at the right offset — and labels every tile with the
    scene it belongs to, so what comes back IS what the viewer sees there.

    Round 72: "what the viewer sees" now includes the GEOMETRY. Each frame
    is fitted onto the output canvas the way the render fits it (cover-crop
    by default, letterbox for frame mode 'pad'), and any zoom active at that
    second is applied by cropping the same viewport the zoompan will show —
    so an aimed zoom's framing is visible BEFORE a render instead of being
    discovered from the user's complaint. Captions, texts, grades and
    overlays still burn in at render only."""
    if not isinstance(output_times, (list, tuple)) or not output_times:
        return ("REJECTED: output_times must be a non-empty array of "
                "OUTPUT seconds of the edited video, e.g. "
                "output_times=[0.5, 4.2].")
    edl = ctx.latest_edl()
    try:
        blocks = timeline_mod.program_blocks(edl["json"])
    except Exception as ex:
        return f"REJECTED: could not map the program ({ex})."
    if not blocks:
        return ("REJECTED: the program is empty — nothing is kept and "
                "nothing is inserted, so there is no output to look at.")
    prog_end = blocks[-1]["out_end"]
    keep = edl["json"].get("keep") or []
    tl = Timeline(keep, edl["json"].get("inserts"), edl["json"].get("speed"))
    try:
        wants = [min(max(float(t), 0.0), max(0.0, prog_end - 0.05))
                 for t in output_times]
    except (TypeError, ValueError):
        return "REJECTED: output_times must be numbers of seconds."

    def _block_at(t):
        for b in blocks:
            if b["out_start"] - 1e-6 <= t < b["out_end"] + 1e-6:
                return b
        return blocks[-1]

    name_of = program_name_of(ctx)
    # Plan every sample first, then decode: main-video times go through the
    # proxy in one loop, insert times batch into ONE executor call per asset.
    plan = []                        # (idx, kind, payload, label)
    per_asset = {}                   # asset_key -> [(idx, local_t, label)]
    for idx, t in enumerate(wants):
        b = _block_at(t)
        if b["kind"] == "footage":
            src = tl.out_to_src(t)
            if src is None:
                src = b["src_start"]
            plan.append((idx, "main", src,
                         f"@out {t:.2f}s = scene {b['n']} "
                         f"(main footage @{src:.2f}s)"))
        else:
            local = float(b.get("clip_start_s") or 0.0) \
                + (t - b["out_start"]) * float(b.get("rate") or 1.0)
            label = (f"@out {t:.2f}s = scene {b['n']} "
                     f"('{(name_of(b['asset_key']) or b['asset_key'].split('/')[-1])[:40]}'"
                     f" @{local:.2f}s)")
            per_asset.setdefault(b["asset_key"], []).append(
                (idx, local, label))

    results = {}                     # idx -> (path, label)
    main_err = None
    mains = [(i, s, lb) for i, k, s, lb in plan if k == "main"]
    if mains:
        try:
            path = ctx.proxy_path()
        except Exception:
            try:
                path = _original_local(ctx)
            except Exception as ex:
                path, main_err = None, str(ex)
        if path:
            for i, s, lb in mains:
                fp = os.path.join(ctx.workdir, f"lookout_m{i}.jpg")
                try:
                    media.frame_at(path, s, fp)
                    results[i] = (fp, lb)
                except media.MediaError as ex:
                    main_err = str(ex)
    ins_err = None
    for key, entries in per_asset.items():
        asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
        if not asset:
            ins_err = f"insert asset {key} not found"
            continue
        if asset["kind"] == "image_ref":
            try:
                local = _asset_local_path(ctx, asset)
                for i, _lt, lb in entries:
                    results[i] = (local, lb)
            except Exception as ex:
                ins_err = str(ex)
            continue
        dur = _asset_media_duration(ctx, asset)
        ts = [min(max(lt, 0.0), max(0.0, dur - 0.05)) for _i, lt, _lb in entries]
        pairs, err = _asset_frames(ctx, asset, ts, width=640, tag="lookout")
        ins_err = err or ins_err
        for j, fp in pairs:
            i, _lt, lb = entries[j]
            results[i] = (fp, lb)

    if not results:
        why = "; ".join(x for x in (main_err, ins_err) if x) or "unknown error"
        return (f"Could not extract output frames ({why[:220]}). The edit "
                "itself is fine — fall back to look_at(times=...) on the "
                "source and look_at_asset on the inserted clips.")
    fxz = (edl["json"].get("effects") or {}).get("zooms") or []
    frame_cfg = edl["json"].get("frame") or {}
    vid = (ctx.index or {}).get("video") or {}
    canvas = None
    if vid.get("width") and vid.get("height"):
        canvas = renderer.frame_dims(vid["width"], vid["height"],
                                     frame_cfg.get("ratio"))
    gmode = frame_cfg.get("mode", "crop") if frame_cfg else "crop"
    gfocus = ((frame_cfg.get("focus_x"), frame_cfg.get("focus_y"))
              if frame_cfg.get("focus_x") is not None
              or frame_cfg.get("focus_y") is not None else None)
    tko = [(float(o.get("start") or 0.0),
            float(o.get("start") or 0.0) + float(o.get("duration_s") or 0.0))
           for o in (edl["json"].get("overlays") or []) if o.get("screen")]
    frames, labels = [], []
    for i in sorted(results):
        fp, lb = results[i]
        t = wants[i]
        try:
            blk = _block_at(t)
            fp, sfx = _fit_and_zoom_frame(
                ctx.workdir, i, fp, t, canvas, gmode, gfocus, fxz,
                prog_end, blk["kind"] == "footage",
                crop=blk.get("crop"), fit=blk.get("fit"))
        except Exception as ex:
            print(f"[look] output geometry skipped ({ex})", flush=True)
            sfx = ""
        if any(a - 1e-6 <= t <= b + 1e-6 for a, b in tko):
            sfx += (" [inside a screen-takeover window — its push/pin is "
                    "not shown here]")
        frames.append(fp)
        labels.append(lb + sfx)
    if frames:
        bucket = ("_pending_looked_output_times"
                  if getattr(ctx, "direct_sight", False)
                  else "_looked_output_times")
        seen = getattr(ctx, bucket, None)
        if seen is None:
            seen = set()
            setattr(ctx, bucket, seen)
        seen.update(float(wants[i]) for i in results)
    missing = len(wants) - len(frames)
    out = _deliver_frames(
        ctx, frames, labels, question,
        f"Frames of the ASSEMBLED PROGRAM (EDL v{edl['version']} output "
        f"timeline, {prog_end:g}s) in TRUE output geometry — canvas fit and "
        "any zoom active at each moment are applied (a tile says so in its "
        "label); captions/texts/grades/overlays still burn in at render "
        "and are not shown")
    if any("zoom on screen" in lb for lb in labels):
        # Round 74: an agent read a rect off a tile that was already zoomed
        # and aimed the next zoom at those numbers — which are SCREEN
        # coordinates of the magnified view, not frame coordinates. Say it
        # at the moment it matters.
        out += ("\nA tile marked with a zoom shows the viewer's FRAMED "
                "shot — its grid reads SCREEN coordinates of the magnified "
                "view, NOT aim coordinates. To aim (cx/cy/rect), read "
                "positions off an UNZOOMED tile — ask for a moment outside "
                "the zoom window.")
    if missing:
        out += f"\n({missing} requested time(s) could not be decoded)"
    return _cap(out)


def look_at(ctx, times=None, question="", start=None, end=None,
            output_times=None, rendered=False):
    """Round 67: the agent's own eyes. Pass any source `times` and
    the exact frames at those moments come back as ONE labeled picture in the
    agent's own context. start/end still work as a range and sample evenly.
    Round 71: `output_times` samples the ASSEMBLED PROGRAM instead — output
    seconds of the current edit, inserts included."""
    # The assembled-program path deliberately omits effects that only exist
    # after ffmpeg composition. rendered=true resolves the preview for the
    # CURRENT immutable EDL version and inspects those real pixels instead,
    # including captions, overlays, grades and designed text.
    if rendered:
        row = ctx.latest_edl()
        asset = ctx.db.run(dbx.find_render_asset, ctx.project_id, "preview",
                           row["version"])
        if not asset:
            return (f"REJECTED: EDL v{row['version']} has no completed preview "
                    "to inspect. Call render_preview, then call "
                    "look_at(rendered=true, output_times=[...]). A past "
                    "version is not accepted as evidence for the current edit.")
        wants = output_times or times
        if wants:
            if not isinstance(wants, (list, tuple)):
                return "REJECTED: output_times must be an array of seconds."
            return look_at_asset(
                ctx, asset["storage_key"], question=question,
                times=list(wants))
        return look_at_asset(
            ctx, asset["storage_key"], question=question,
            start=0 if start is None else start, end=end)

    # An empty array is "not asked", not a request: the model fills every
    # schema field, and `times=[2, 6], output_times=[]` burned nine straight
    # rejections in one session (project 382, 2026-08-07) under the old
    # `is not None` — with the valid source times discarded each time.
    if output_times:
        return _look_at_output(ctx, output_times, question)
    if times:
        if not isinstance(times, (list, tuple)):
            return ("REJECTED: times must be an array of source "
                    "seconds, e.g. times=[3.2, 17.8].")
        try:
            times = [ctx.clamp(t) for t in times]
        except (ValueError, TypeError) as err:
            return f"REJECTED: {err}"
        s, e = min(times), max(times)
    elif start is not None and end is not None:
        try:
            s, e = ctx.clamp(start), ctx.clamp(end)
        except ValueError as err:
            return f"REJECTED: {err}"
        if e <= s:
            e = min(ctx.duration, s + 1.0)
        # 6 frames over a >30s range (was 4 max): 4 samples across half a
        # minute skip whole shots; the marginal cost is small next to a wrong
        # cut.
        n = 6 if e - s > 30 else (4 if e - s > 1.5 else 2)
        times = [s + (e - s) * (i + 0.5) / n for i in range(n)]
    else:
        return ("REJECTED: pass times=[...] (exact source seconds to look "
                "at), a start/end range, or output_times=[...] (OUTPUT "
                "seconds of the assembled program). An empty array counts "
                "as not passed.")
    try:
        proxy = ctx.proxy_path()
    except Exception as err:
        proxy = None
        proxy_err = str(err)
    else:
        proxy_err = None

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
            names.append(f"@{t:.2f}s")
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
    bucket = ("_pending_looked_source_times"
              if getattr(ctx, "direct_sight", False)
              else "_looked_source_times")
    seen = getattr(ctx, bucket, None)
    if seen is None:
        seen = set()
        setattr(ctx, bucket, seen)
    for label in frame_names:
        try:
            seen.add(float(label.lstrip("@").rstrip("s")))
        except (TypeError, ValueError):
            continue
    try:
        has_frame = bool((ctx.latest_edl()["json"].get("frame") or {})
                         .get("ratio"))
    except Exception:
        has_frame = False
    src_note = (" (SOURCE footage — the output frame crop/letterbox is "
                "applied later at render, so do not judge aspect ratio here)"
                if has_frame else "")
    return _deliver_frames(
        ctx, frames, frame_names, question,
        f"Frames from the MAIN video, {s:.2f}s-{e:.2f}s{src_note}")


def _asset_local_path(ctx, asset):
    local = ctx._asset_locals.get(asset["id"])
    if not local:
        local = os.path.join(ctx.workdir, f"asset_{asset['id']}"
                             + os.path.splitext(asset["storage_key"])[1])
        storage.download_to(asset["storage_key"], local)
        ctx._asset_locals[asset["id"]] = local
    return local


def _asset_frames(ctx, asset, times, width=640, tag="alook"):
    """Local jpeg paths for `times` of an UPLOADED asset — [(i, path)], err.

    Decoded on the EXECUTOR when one is configured (round 62). An uploaded
    asset has no proxy — the only copy is the user's original, routinely 4K
    HEVC off a phone — and one decoded 4K frame costs ~240 MB resident even
    single-threaded. This runs inside an agent turn, on the dispatcher, and
    on 2026-07-30 (job 1452) it OOM-killed that process one hour after round
    61b shipped for the same class of death; the customer read "I lost my
    connection". A remote FAILURE does not fall back to a local decode —
    that reproduces the crash on the box we know is too small (round 61b).
    The local loop below exists only for the single-box deployment with no
    executor at all, where it is what always shipped.
    """
    times = [round(float(t), 3) for t in times]
    if remote.frames_available():
        try:
            got = remote.run_frames_remote(
                ctx.project_id,
                {"storage_key": asset["storage_key"], "times": times,
                 "width": width},
                user_id=ctx.job.get("user_id")) or {}
        except Exception as ex:
            return [], f"frame extraction failed on the render service ({ex})"
        keys = got.get("keys") or []
        pairs, errs = [], [str(e) for e in (got.get("errors") or [])]
        for i, k in enumerate(keys):
            if not k:
                continue
            fp = os.path.join(ctx.workdir, f"{tag}_{asset['id']}_{i}.jpg")
            try:
                storage.download_to(k, fp)
            except Exception as ex:
                errs.append(str(ex))
                continue
            pairs.append((i, fp))
        try:
            storage.delete_keys([k for k in keys if k])
        except Exception:
            pass                     # scratch objects; never worth a failure
        return pairs, (None if pairs else
                       ("; ".join(errs)[:220] or "no frames came back"))
    # No executor configured: one box is all there is, decode here.
    try:
        local = _asset_local_path(ctx, asset)
    except Exception as ex:
        return [], f"cannot fetch that asset right now ({ex})"
    pairs, last_err = [], None
    for i, t in enumerate(times):
        fp = os.path.join(ctx.workdir, f"{tag}_{asset['id']}_{i}.jpg")
        try:
            media.frame_at(local, t, fp, width=width)
            pairs.append((i, fp))
        except media.MediaError as ex:
            last_err = str(ex)
    return pairs, (None if pairs else (last_err or "unknown error"))


def look_at_asset(ctx, asset_key, question="", start=0, end=None, times=None):
    """Frames from an UPLOADED clip or image (not the main video) — THE way
    to pick which moment of a long clip to splice in with insert_media.
    Round 67: the frames land in the agent's own context (see
    _deliver_frames); pass times=[...] for exact moments."""
    # Renders are inspectable too (round 66): "check it yourself" is a real
    # user sentence, and the render self-check is a 3x3 sheet of the whole
    # programme — too coarse to catch a strobing mask or a one-frame flash.
    # Pointing this at a finished render with a narrow start/end samples
    # frame-accurately around the moment in question.
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref", "render"))
    if err:
        return err
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    if asset["kind"] == "image_ref":
        try:
            local = _asset_local_path(ctx, asset)
        except Exception as e:
            return f"Cannot fetch that asset right now ({e})."
        bucket = ("_pending_looked_asset_times"
                  if getattr(ctx, "direct_sight", False)
                  else "_looked_asset_times")
        seen = getattr(ctx, bucket, None)
        if seen is None:
            seen = {}
            setattr(ctx, bucket, seen)
        seen.setdefault(str(asset_key), set()).add(0.0)
        return _deliver_frames(ctx, [local], [name[:60] or "image"], question,
                               f"The uploaded image '{name}'")
    dur = _asset_media_duration(ctx, asset)
    if times is not None:
        if not isinstance(times, (list, tuple)) or not times:
            return ("REJECTED: times must be a non-empty array of seconds "
                    "into the clip, e.g. times=[3.2, 17.8].")
        try:
            times = sorted(round(min(max(float(t), 0.0), dur), 3)
                           for t in times)
        except (TypeError, ValueError):
            return "REJECTED: times must be numbers of seconds."
    else:
        try:
            s = round(min(max(float(start or 0), 0.0), dur), 2)
            e = round(min(max(float(end), s), dur), 2) \
                if end is not None else dur
        except (TypeError, ValueError):
            return "REJECTED: start/end must be numbers of seconds."
        if e <= s:
            e = min(dur, s + 1.0)
        n = 6 if e - s > 20 else 4
        times = [s + (e - s) * (i + 0.5) / n for i in range(n)]
    # The decode happens on the executor when one is configured — an uploaded
    # clip has no proxy, and 4K seeks on the dispatcher are what killed job
    # 1452's whole turn (see _asset_frames).
    pairs, err = _asset_frames(ctx, asset, times, width=640, tag="alook")
    frames = [fp for _, fp in pairs]
    frame_names = [f"@{times[i]:.2f}s" for i, _ in pairs]
    if not frames:
        return ("Could not extract frames from that clip "
                f"({(err or 'unknown error')[:220]}). The clip can still "
                "be inserted — you just cannot see inside it; ask the user "
                "which part to use instead of guessing.")
    bucket = ("_pending_looked_asset_times"
              if getattr(ctx, "direct_sight", False)
              else "_looked_asset_times")
    seen = getattr(ctx, bucket, None)
    if seen is None:
        seen = {}
        setattr(ctx, bucket, seen)
    seen.setdefault(str(asset_key), set()).update(
        float(times[i]) for i, _fp in pairs)
    out = _deliver_frames(
        ctx, frames, frame_names, question,
        f"Frames from '{name}' ({asset['kind']}, {dur:.0f}s long)")
    return _cap(out + f"\n(clip is {dur:.1f}s long; call again with times "
                      "or a narrower start/end to zoom into a region)")


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
    # Inserts sit at keep boundaries; when the keep list changes they have to
    # move to the boundary of the NEW keep that is in front of the SAME
    # footage, or the edit no longer validates. Shared with the backend UI ops
    # (timeline.resnap_inserts) so a cut made in the studio and a cut made in
    # chat move a spliced clip the same way.
    ins_notes = []
    if edl.get("inserts"):
        edl["inserts"], ins_notes = timeline_mod.resnap_inserts(
            edl["inserts"], prev_keep, new_keep, speed, speed)
    # Program-time items re-anchor through the shared remap; both Timelines
    # carry the (unchanged-by-this-write) speed list so their clocks agree
    # with what actually renders.
    old_tl = Timeline(prev_keep, prev["json"].get("inserts") or [],
                      prev["json"].get("speed") or [])
    new_tl = Timeline(new_keep, edl.get("inserts") or [], speed)
    region_notes = ins_notes + _remap_program_items(edl, old_tl, new_tl)

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
    # Round 69: the transcriber TAGS hesitations now, so a default run cuts
    # what the engine itself heard as a filler rather than only the spellings
    # in our list. A custom list stays exactly what the user asked for — the
    # tag must never quietly cut words they did not name.
    use_tag = not (isinstance(words, list) and words)
    cuts, hits = [], {}
    for idx, tok in enumerate(norm):
        if tok in singles or (use_tag and all_words[idx].get("filler")):
            cuts.append([round(all_words[idx]["t0"], 2),
                         round(all_words[idx]["t1"], 2)])
            hits[tok or "(hesitation)"] = hits.get(tok or "(hesitation)", 0) + 1
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


def _get_spatial(ctx):
    """Cached face/text/UI track for the main source."""
    if getattr(ctx, "_spatial", None) is not None:
        return ctx._spatial
    indexed = (getattr(ctx, "index", None) or {}).get("spatial")
    if isinstance(indexed, dict) and indexed.get("v") == \
            spatial.SPATIAL_VERSION:
        ctx._spatial = indexed
        return indexed
    original = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
    if not original or not original.get("sha256"):
        raise spatial.SpatialError("no indexed main video")
    index_row = ctx.db.run(dbx.get_index_by_sha, original["sha256"])
    if not index_row:
        raise spatial.SpatialError("no index row for this video")
    ctx._spatial = spatial.get_or_compute_for_index(
        ctx.db, dbx, index_row, ctx.proxy_path(), ctx.workdir)
    return ctx._spatial


def _source_box_to_output(ctx, edl, source_t, box):
    """A source-frame box clipped/mapped into the rendered output frame."""
    video = ctx.index.get("video") or {}
    try:
        sw, sh = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return None
    frame = edl.get("frame") or {}
    W, H = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    kind, fx0, fy0, fx1, fy1 = renderer.fit_fractions(
        sw, sh, W, H, _frame_mode_at_source(edl, source_t),
        _frame_focus_at_source(edl, source_t))
    x0, y0, x1, y1 = (float(v) for v in box)
    if kind == "crop":
        x0, y0, x1, y1 = (max(x0, fx0), max(y0, fy0),
                          min(x1, fx1), min(y1, fy1))
        if x1 <= x0 or y1 <= y0:
            return None
        return [round((x0 - fx0) / max(fx1 - fx0, 1e-9), 4),
                round((y0 - fy0) / max(fy1 - fy0, 1e-9), 4),
                round((x1 - fx0) / max(fx1 - fx0, 1e-9), 4),
                round((y1 - fy0) / max(fy1 - fy0, 1e-9), 4)]
    return [round(fx0 + x0 * (fx1 - fx0), 4),
            round(fy0 + y0 * (fy1 - fy0), 4),
            round(fx0 + x1 * (fx1 - fx0), 4),
            round(fy0 + y1 * (fy1 - fy0), 4)]


_CAPTION_ZONES = {
    "top": (0.04, 0.31),
    "middle": (0.31, 0.68),
    # Stops above feed-platform chrome; captions.py applies the exact margin.
    "bottom": (0.66, 0.88),
}


def _caption_source_text_boxes(sample):
    """Text evidence reliable enough to constrain caption composition.

    Dense UI keeps every detected region: even an unlabeled panel is content
    the editor should not cover. Natural footage is different. MSER can group
    jacket seams, a microphone and chair edges into tall pseudo-"text" boxes;
    the production mixed-shot canary produced three such boxes in three face
    frames and forced the whole edit back to pad_blur. Real word/subtitle lines
    are horizontally line-shaped. Keep those, but never let a tall natural
    texture overrule a measured face and shot-specific framing.
    """
    boxes = list(sample.get("text") or [])
    if sample.get("dense_ui"):
        return boxes
    reliable = []
    for box in boxes:
        try:
            w = float(box[2]) - float(box[0])
            h = float(box[3]) - float(box[1])
        except (TypeError, ValueError, IndexError):
            continue
        aspect = w / max(h, 1e-9)
        # Normal word lines are thin; a broad title/lower-third block may be
        # taller but is still unmistakably horizontal. Near-square/tall
        # groups are the natural-texture false positives this gate removes.
        line_shaped = h <= 0.13 and aspect >= 2.1
        title_shaped = h <= 0.20 and aspect >= 3.0
        if w >= 0.10 and h >= 0.008 and (line_shaped or title_shaped):
            reliable.append(box)
    return reliable


def _box_zone_overlap(box, zone):
    x0, y0, x1, y1 = box
    zy0, zy1 = zone
    inter = max(0.0, min(y1, zy1) - max(y0, zy0)) * max(0.0, x1 - x0)
    area = max(1e-9, (x1 - x0) * (y1 - y0))
    return inter / area


def _caption_picture_bounds(ctx, edl, source_t):
    """Visible foreground-picture bounds in output-frame fractions.

    A pad/pad_blur render has a full output canvas but only part of it is the
    actual shot.  Caption composition must stay attached to that foreground
    picture instead of selecting an apparently empty blurred band.
    """
    video = ctx.index.get("video") or {}
    try:
        sw, sh = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return (0.0, 1.0)
    frame = edl.get("frame") or {}
    W, H = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    kind, _x0, y0, _x1, y1 = renderer.fit_fractions(
        sw, sh, W, H, _frame_mode_at_source(edl, source_t),
        _frame_focus_at_source(edl, source_t))
    return (float(y0), float(y1)) if kind == "pad" else (0.0, 1.0)


def _caption_zones_for_bounds(bounds):
    y0, y1 = bounds
    height = max(0.05, y1 - y0)
    return {
        "top": (y0 + 0.04 * height, y0 + 0.31 * height),
        "middle": (y0 + 0.31 * height, y0 + 0.68 * height),
        "bottom": (y0 + 0.66 * height, y0 + 0.88 * height),
    }


def _caption_anchor_for(position, bounds):
    y0, y1 = bounds
    height = max(0.05, y1 - y0)
    rel = {"top": 0.16, "middle": 0.50, "bottom": 0.80}.get(position, 0.80)
    return round(min(max(y0 + rel * height, 0.05), 0.95), 4)


def _safe_caption_position(faces, text, dense_ui, preferred="bottom",
                           zones=None):
    """Pick a clean vertical band, or None when every band is occupied."""
    base = {"bottom": 0.0, "top": 0.25, "middle": 0.8}
    # Honor the visual grammar as a preference, not as permission to write on
    # a face. A spotlight preset starts in the middle; other looks start low.
    if preferred in base:
        base[preferred] -= 0.2
    score = dict(base)
    for pos, zone in (zones or _CAPTION_ZONES).items():
        for box in faces:
            ov = _box_zone_overlap(box, zone)
            cy = (box[1] + box[3]) / 2.0
            score[pos] += 7.0 * ov + (2.5 if zone[0] <= cy <= zone[1] else 0)
        for box in text:
            score[pos] += 4.5 * _box_zone_overlap(box, zone)
        if dense_ui:
            score[pos] += 0.8
    winner = min(score, key=score.get)
    return (winner, score) if score[winner] <= 2.6 else (None, score)


def _stabilize_caption_positions(picks, switch_cost=1.15):
    """Viterbi smoothing for measured caption bands, per kept source span.

    A band remains forbidden whenever its collision score is unsafe.  Among
    safe bands, however, the caption pays a small cost to jump top↔bottom, so
    one noisy sample cannot make typography bounce while a materially safer
    shot still overrides the inertia.  The final track is baked into the EDL;
    the renderer performs no visual inference.
    """
    if not picks:
        return picks
    start = 0
    while start < len(picks):
        span = picks[start]["span"]
        end = start + 1
        while end < len(picks) and picks[end]["span"] == span:
            end += 1
        group = picks[start:end]
        history = []
        costs = {}
        for ri, row in enumerate(group):
            safe = {band: float(score) for band, score in row["scores"].items()
                    if float(score) <= 2.6}
            if not safe:
                history.append({None: (0.0, None)})
                costs = {}
                row["position"] = None
                continue
            step = {}
            if not costs:
                for band, score in safe.items():
                    step[band] = (score, None)
            else:
                for band, score in safe.items():
                    options = [(prev_cost + (0.0 if prev == band else switch_cost),
                                prev)
                               for prev, (prev_cost, _back) in costs.items()]
                    best_cost, best_prev = min(options)
                    step[band] = (score + best_cost, best_prev)
            history.append(step)
            costs = step
        # Backtrack each contiguous safe run (unsafe rows reset the chain).
        cursor = len(group) - 1
        while cursor >= 0:
            if group[cursor].get("position") is None and \
                    not any(float(v) <= 2.6
                            for v in group[cursor]["scores"].values()):
                cursor -= 1
                continue
            run_end = cursor
            while cursor >= 0 and any(float(v) <= 2.6
                                      for v in group[cursor]["scores"].values()):
                cursor -= 1
            run_start = cursor + 1
            state = min(history[run_end], key=lambda b: history[run_end][b][0])
            for idx in range(run_end, run_start - 1, -1):
                group[idx]["position"] = state
                state = history[idx][state][1]
                if state is None and idx > run_start:
                    # Defensive fallback for a malformed/reset history; the
                    # independently safest choice is still collision-safe.
                    state = min(history[idx - 1],
                                key=lambda b: history[idx - 1][b][0])
        start = end
    return picks


def _caption_placement_track(ctx, edl, sidecar, preferred="bottom"):
    """Measured source-time placement spans plus unsafe/analyzed counts."""
    keep = [(float(a), float(b)) for a, b in (edl.get("keep") or [])]
    picks = []
    for sample in (sidecar or {}).get("samples") or []:
        t = float(sample.get("t", 0))
        span = next(((a, b) for a, b in keep if a <= t <= b), None)
        if span is None:
            continue
        faces = [m for box in sample.get("faces") or []
                 if (m := _source_box_to_output(ctx, edl, t, box))]
        text_boxes = [m for box in _caption_source_text_boxes(sample)
                      if (m := _source_box_to_output(ctx, edl, t, box))]
        bounds = _caption_picture_bounds(ctx, edl, t)
        pos, scores = _safe_caption_position(
            faces, text_boxes, bool(sample.get("dense_ui")), preferred,
            zones=_caption_zones_for_bounds(bounds))
        picks.append({"t": t, "span": span, "position": pos,
                      "face": bool(faces), "text": bool(text_boxes),
                      "scores": scores, "picture_bounds": bounds})
    if not picks:
        return [], 0, 0
    picks = _stabilize_caption_positions(picks)
    out, unsafe = [], 0
    for i, pick in enumerate(picks):
        a, b = pick["span"]
        prev_t = picks[i - 1]["t"] if i > 0 and picks[i - 1]["span"] == \
            pick["span"] else a
        next_t = picks[i + 1]["t"] if i + 1 < len(picks) and \
            picks[i + 1]["span"] == pick["span"] else b
        t0 = max(a, (prev_t + pick["t"]) / 2.0 if i > 0 else a)
        t1 = min(b, (pick["t"] + next_t) / 2.0
                 if i + 1 < len(picks) else b)
        if t1 - t0 < 0.05:
            continue
        measured_unsafe = pick["position"] is None
        if measured_unsafe:
            unsafe += 1
            # Never turn a difficult frame into missing words.  The least
            # obstructed measured band remains more honest than silently
            # deleting the caption; callers are told it was a fallback and
            # the rendered-caption contact sheet makes it visually auditable.
            pick["position"] = min(pick["scores"], key=pick["scores"].get)
        reason = ("least-obstructed fallback; all bands occupied"
                  if measured_unsafe else
                  "avoids face and source text" if pick["face"] and pick["text"]
                  else "avoids face" if pick["face"]
                  else "avoids source text" if pick["text"]
                  else "clean frame band")
        row = {"t0": round(t0, 2), "t1": round(t1, 2),
               "position": pick["position"],
               "anchor_y": _caption_anchor_for(
                   pick["position"], pick.get("picture_bounds") or (0.0, 1.0)),
               "reason": reason}
        if out and out[-1]["position"] == row["position"] and \
                out[-1].get("anchor_y") == row.get("anchor_y") and \
                out[-1]["reason"] == row["reason"] and \
                row["t0"] - out[-1]["t1"] <= 0.08:
            out[-1]["t1"] = row["t1"]
        else:
            out.append(row)
    return out, unsafe, len(picks)


def _fixed_text_band(ctx, edl, out_start, out_end, preferred="middle",
                     lock=False):
    """Choose one measured-safe band for a designed text window.

    Caption placement may change shot by shot; a title/callout is one visual
    object and therefore needs one stable band for its whole lifetime.  This
    aggregates the same face/source-text evidence over the object's exact
    PROGRAM window and rejects a band when it collides in more than 20% of
    measured frames. ``lock`` means an explicit user y-coordinate may not be
    silently moved to another band.
    """
    try:
        sidecar = _get_spatial(ctx)
    except Exception as exc:
        return None, 0, 0, str(exc)[:160]
    try:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
    except Exception as exc:
        return None, 0, 0, f"could not map timeline: {str(exc)[:120]}"

    rows = []

    def _scores(source_t, sample):
        faces = [mapped for box in (sample.get("faces") or [])
                 if (mapped := _source_box_to_output(
                     ctx, edl, source_t, box)) is not None]
        text_boxes = [mapped for box in _caption_source_text_boxes(sample)
                      if (mapped := _source_box_to_output(
                          ctx, edl, source_t, box)) is not None]
        _pos, scores = _safe_caption_position(
            faces, text_boxes, bool(sample.get("dense_ui")), preferred)
        return scores

    for sample in (sidecar or {}).get("samples") or []:
        try:
            source_t = float(sample.get("t"))
        except (TypeError, ValueError):
            continue
        program_t = tl.src_to_out(source_t)
        if program_t is None or program_t < out_start - 0.06 or \
                program_t > out_end + 0.06:
            continue
        rows.append(_scores(source_t, sample))

    # A global sidecar is deliberately bounded, so a two-second title on a
    # long video can fall between samples. Measure the exact authored window
    # instead of interpreting "no nearby sample" as "empty frame". The
    # per-turn cache is capped: kinetic text can request many phrases, but it
    # may never turn exact collision checks into an unbounded seek loop.
    window = max(0.0, float(out_end) - float(out_start))
    fractions = [0.5] if window <= 2.5 else (
        [0.25, 0.75] if window <= 8.0 else [0.15, 0.5, 0.85])
    targets = []
    for frac in fractions:
        output_t = float(out_start) + window * frac
        source_t = tl.out_to_src(output_t)
        if source_t is not None:
            targets.append(float(source_t))
    if len(rows) < 2 and targets:
        cache = getattr(ctx, "_exact_spatial_samples", None)
        if cache is None:
            cache = {}
            setattr(ctx, "_exact_spatial_samples", cache)
        try:
            proxy = ctx.proxy_path()
        except Exception:
            proxy = None
        for source_t in targets:
            key = round(source_t, 2)
            sample = cache.get(key)
            if sample is None and proxy and len(cache) < 12:
                fp = os.path.join(
                    ctx.workdir, f"text_band_{len(cache)}_{int(key * 100)}.jpg")
                try:
                    media.frame_at(proxy, source_t, fp, width=360)
                    sample = spatial.analyze_frame(fp)
                    cache[key] = sample
                except Exception:
                    sample = None
            if sample is not None:
                rows.append(_scores(source_t, sample))

    # An inserted card/asset has no main-source time. It is not evidence of a
    # collision, so retain the authored band and let the independent preview
    # critic inspect that separate asset. Main-footage windows are measured.
    if not rows and targets:
        return (None, 0, 0,
                "no exact frame could be measured for this text window")
    if not rows:
        return preferred, 0, 0, None

    aggregate = {
        band: sum(row[band] for row in rows) / len(rows)
        for band in _CAPTION_ZONES
    }
    band = preferred if lock else min(aggregate, key=aggregate.get)
    unsafe = sum(1 for row in rows if row[band] > 2.6)
    if unsafe / len(rows) > 0.20:
        return None, len(rows), unsafe, None
    return band, len(rows), unsafe, None


def _direct_caption_style(ctx, edl):
    """Choose one coherent caption grammar from measurable format + brief.

    Variety is between projects, not random style-switching inside a video.
    The mapping is deterministic so identical footage/briefs render the same,
    but a sports reel, luxury film, podcast and long tutorial no longer all
    fall through to the same generic classic subtitle.
    """
    plan = getattr(ctx, "edit_plan", None) or {}
    ask = " ".join(filter(None, [
        str(getattr(ctx, "user_message", "") or ""),
        str(plan.get("brief") or ""), str(plan.get("format") or ""),
        str(plan.get("intent") or ""), str(plan.get("style_family") or ""),
        " ".join(plan.get("must_keep") or []),
        " ".join(plan.get("must_avoid") or []),
    ])).casefold()
    video = (getattr(ctx, "index", None) or {}).get("video") or {}
    out_dur = max(0.1, program_duration(edl))
    words = (getattr(ctx, "index", None) or {}).get("words") or []
    kept = edl.get("keep") or []
    visible_words = [w for w in words if any(
        float(a) - 0.05 <= (float(w.get("t0", 0)) +
                            float(w.get("t1", 0))) / 2.0 <= float(b) + 0.05
        for a, b in kept)]
    wpm = len(visible_words) * 60.0 / out_dur
    ratio = ((edl.get("frame") or {}).get("ratio") or "source")
    if ratio == "source":
        try:
            vertical = float(video.get("height")) > float(video.get("width")) * 1.05
        except (TypeError, ValueError):
            vertical = False
    else:
        try:
            rw, rh = (float(x) for x in str(ratio).split(":"))
            vertical = rh > rw * 1.05
        except (TypeError, ValueError):
            vertical = False
    short_form = vertical and out_dur <= 180.0
    speakers = int((getattr(ctx, "index", None) or {}).get("speakers") or 0)

    def hit(*needles):
        return any(n in ask for n in needles)

    if hit("classic subtitles", "legacy subtitles", "basic subtitles",
           "captions بسيطة"):
        return {"preset": "classic", "size": "m"}, \
            "the brief explicitly asks for the legacy subtitle treatment"
    if hit("documentary subtitles", "closed captions", "accessible captions",
           "subtitle panel", "boxed subtitles", "long-form subtitles"):
        return {"preset": "documentary"}, \
            "the brief calls for durable long-form readability on a backing panel"
    if hit("news", "broadcast", "newscast", "headline", "lower third captions"):
        return {"preset": "broadcast", "highlight_color": "#5DD6FF"}, \
            "news/explainer language calls for a clear left-aligned lower-third"
    if hit("retro", "vintage", "throwback", "old school", "poster"):
        return {"preset": "retro"}, \
            "the requested retro treatment needs condensed outlined display type"
    if hit("neon", "cyber", "cyberpunk", "electric glow"):
        return {"preset": "neon", "highlight_color": "#7DEBFF"}, \
            "the requested neon treatment needs one coherent cool glow accent"
    if hit("plain captions", "simple captions", "minimal captions", "clean",
           "aesthetic", "premium", "nice captions", "good captions",
           "beautiful captions"):
        return {"preset": "clean", "emphasis": "big"}, \
            "the brief asks for restrained premium typography with size-only hierarchy"
    if hit("luxury", "luxurious", "premium brand", "expensive", "jewelry",
           "jewellery", "watch ad"):
        return {"preset": "luxe", "highlight_color": "#E2BE72"}, \
            "luxury/product language calls for restrained serif type and gold accents"
    if hit("fashion", "editorial", "runway", "magazine", "beauty campaign"):
        return {"preset": "fashion", "highlight_color": "#FF5B91"}, \
            "fashion/editorial content benefits from a wide magazine-like display face"
    if hit("sports", "gym", "workout", "gaming", "gameplay", "hype",
           "meme", "high energy", "energetic"):
        return {"preset": "impact", "highlight_color": "#B7FF3C"}, \
            "high-energy footage needs compact, forceful type that survives motion"
    if hit("lyrics", "lyric", "song edit", "music video", "singing"):
        return {"preset": "lyric", "highlight_color": "#E2BE72"}, \
            "music/lyric footage calls for phrase-led display type and a distinct stressed word"
    if hit("wedding", "cinematic", "travel film", "calm",
           "emotional", "storytelling"):
        return {"preset": "editorial", "highlight_color": "#F2D1A0"}, \
            "calm/cinematic footage needs air and a quiet serif hierarchy"
    if hit("tutorial", "screen recording", "software", "saas", "tech",
           "product demo", "explainer", "educational", "business"):
        return {"preset": "clean", "emphasis": "big"}, \
            "tutorial/business speech needs a restrained, highly legible hierarchy"
    if hit("podcast", "interview", "talking head", "reel", "viral",
           "creator", "cool captions") or speakers >= 2:
        preset = "clean" if wpm < 175 else "stacked"
        return {"preset": preset, "emphasis": "big"}, \
            "the talking-head format calls for readable white type and semantic hierarchy"
    if short_form:
        preset = "stacked" if wpm >= 165 else "clean"
        return {"preset": preset, "emphasis": "big"}, \
            f"the measured vertical short-form pace is {wpm:.0f} words/minute"
    if out_dur <= 90.0:
        return {"preset": "clean", "emphasis": "big"}, \
            "a short landscape piece benefits from clean, restrained typography"
    return {"preset": "documentary"}, \
        "long-form footage benefits from readable subtitles on a stable contrast panel"


_CAPTION_STOPWORDS = {
    # Function words should almost never become the visual hero. The list is
    # intentionally multilingual but small; unknown languages still work via
    # length/digit/end-of-phrase scoring instead of being rejected.
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "him", "his", "how", "i", "if", "in", "is",
    "it", "its", "just", "me", "my", "no", "not", "of", "on", "or",
    "our", "she", "so", "that", "the", "their", "them", "then", "there",
    "they", "this", "to", "up", "us", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
    "de", "del", "el", "ella", "en", "es", "esta", "este", "la", "las",
    "lo", "los", "para", "pero", "por", "que", "se", "sin", "su", "un",
    "una", "y", "yo", "le", "les", "des", "du", "et", "la", "le",
    "les", "mais", "ou", "pour", "que", "qui", "sur", "un", "une",
}


def _caption_token_key(value):
    """Unicode-safe caption matching (Arabic/CJK/etc. must not collapse)."""
    return re.sub(r"[^\w']+", "", str(value or "").casefold(),
                  flags=re.UNICODE).strip("_")


def _auto_caption_emphasis(ctx, edl, limit=25):
    """Pick sparse, semantic emphasis from the words that survive this EDL.

    This is a renderer safeguard, not a request for the model to remember an
    optional field. It selects roughly one meaningful word per 6-8 spoken
    words, covers every phrase, prefers numbers/outcomes/rare terms, and
    returns transcript spelling verbatim so matching remains honest.
    """
    keep = edl.get("keep") or []
    index_words = list(ctx.index.get("words") or [])
    stress_scores = [0.0] * len(index_words)
    # The audio sidecar is already the authority for emphasis punch-ins.  Use
    # the same measured vocal landing for typography instead of letting a
    # merely long/rare word beat what the speaker actually stressed.  The
    # chosen words are stored in the EDL, so rendering remains deterministic;
    # unavailable perception falls back to the lexical scorer below.
    try:
        if isinstance(ctx, ToolContext):
            cached = (getattr(ctx, "_perception", None)
                      or (ctx.index.get("perception")
                          if isinstance(ctx.index.get("perception"), dict)
                          else None))
            # Computing a one-hour sidecar merely to choose type hierarchy is
            # the wrong latency trade.  Short-form—the place dynamic caption
            # stress matters most—earns the one-time analysis; long footage
            # uses a sidecar only when another edit already cached it.
            if cached is not None or float(getattr(ctx, "duration", 0) or 0) <= 180:
                stress_scores = perception.word_stress(
                    cached or _get_perception(ctx), index_words)
    except Exception:
        stress_scores = [0.0] * len(index_words)
    visible = []
    for wi, w in enumerate(index_words):
        if w.get("filler"):
            continue
        try:
            mid = (float(w["t0"]) + float(w["t1"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        if keep and not any(float(a) - 0.05 <= mid <= float(b) + 0.05
                            for a, b in keep):
            continue
        raw = str(w.get("w") or "").strip()
        key = _caption_token_key(raw)
        if key:
            visible.append({"raw": raw.strip("\"'.,!?;:…()[]"),
                            "key": key, "t0": float(w["t0"]),
                            "t1": float(w["t1"]),
                            "stress": float(stress_scores[wi] or 0.0),
                            "sentence_end": raw.rstrip().endswith(
                                (".", "!", "?", "…"))})
    if not visible:
        return []
    freq = {}
    for w in visible:
        freq[w["key"]] = freq.get(w["key"], 0) + 1

    def score(w, index, group):
        raw, key = w["raw"], w["key"]
        if key in _CAPTION_STOPWORDS or len(key) <= 1:
            return -20.0
        s = 0.0
        if any(ch.isdigit() for ch in raw):
            s += 12.0
        if any(ch in raw for ch in "$€£¥%"):
            s += 5.0
        s += min(len(key), 12) * 0.22
        if len(key) >= 7:
            s += 1.2
        if freq.get(key, 0) == 1:
            s += 1.0
        else:
            s -= min(1.5, (freq[key] - 1) * 0.35)
        # Vocal stress is the strongest non-numeric signal.  A value around
        # 0.65 is a clearly landed word under perception.word_stress; quieter
        # words still receive a gentle tie-break rather than a binary jump.
        stress = min(max(float(w.get("stress") or 0.0), 0.0), 1.0)
        s += stress * 6.0
        if stress >= 0.65:
            s += 1.5
        if index == len(group) - 1:
            s += 1.0  # phrase-ending outcome/verb often carries the landing
        return s

    # Phrase groups mirror caption reading units, not arbitrary global rarity.
    groups, cur = [], []
    for w in visible:
        if cur and (w["t0"] - cur[-1]["t1"] > 0.9 or len(cur) >= 8):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["sentence_end"]:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    target = min(limit, max(1, round(len(visible) / 7)))
    ranked_groups = []
    for gi, group in enumerate(groups):
        candidates = sorted(
            ((score(w, i, group), i, w) for i, w in enumerate(group)),
            key=lambda x: (-x[0], x[1]))
        if candidates and candidates[0][0] > -10:
            ranked_groups.append((candidates[0][0], gi, candidates[0][2]))

    # Long videos can have more phrases than the 25-word contract. Keep the
    # strongest phrase heroes, but restore chronological order in the EDL.
    selected = sorted(ranked_groups, key=lambda x: (-x[0], x[1]))[:target]
    selected.sort(key=lambda x: x[1])
    out, seen = [], set()
    for _score, _gi, w in selected:
        if w["key"] not in seen:
            seen.add(w["key"])
            out.append(w["raw"])
    return out[:limit]


def add_captions(ctx, mode=None, items=None, style=None,
                 max_words_per_caption=None, emphasis_words=None):
    edl = dict(ctx.latest_edl()["json"])
    # `position` / `anchor_y` are user-authored geometry, not mere visual
    # preferences.  The measured placement compiler used to keep running even
    # when either was explicit, then stored a per-shot placement_track whose
    # values beat the global style at render time.  The EDL could therefore
    # say position:"bottom" while the caption visibly jumped middle/top later
    # in the video.  Preserve adaptive collision avoidance only when the
    # caller leaves placement open for the product to direct.
    placement_locked = isinstance(style, dict) and any(
        style.get(key) is not None for key in ("position", "anchor_y"))
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
        directed_style_note = ""
        if parsed_style is None and (isinstance(ctx, ToolContext) or
                                     getattr(ctx, "enforce_spatial", False)):
            chosen, why = _direct_caption_style(ctx, edl)
            parsed_style = _parse_style(chosen)
            directed_style_note = (f"\nAuto-directed caption look: "
                                   f"{chosen['preset']} — {why}.")
        mw = None
        if max_words_per_caption is not None:
            try:
                mw = int(max_words_per_caption)
            except (TypeError, ValueError):
                return "REJECTED: max_words_per_caption must be an integer."
        preset = (parsed_style or {}).get("preset")
        premium = preset and preset != "classic"
        # Caption geometry is compiled from pixels, not guessed from a global
        # preset. This also catches an already-captioned source before a second
        # transcript layer is burned over it.
        placement = []
        spatial_notes = []
        # Spatial analysis improves placement but never withholds the tool.
        if isinstance(ctx, ToolContext) or getattr(ctx, "enforce_spatial", False):
            try:
                spatial_track = _get_spatial(ctx)
            except Exception as exc:
                spatial_track = None
                spatial_notes.append(
                    "\nQUALITY ADVISORY: face/source-text analysis was "
                    f"unavailable ({str(exc)[:140]}), so captions used the "
                    "requested/default position. Inspect the preview for "
                    "face or source-text collisions.")
            if spatial_track is not None:
                burned_score = spatial.burned_caption_score(
                    spatial_track, ctx.index.get("words") or [])
                if burned_score >= spatial.BURNED_CAPTION_BLOCK_SCORE:
                    spatial_notes.append(
                        "\nQUALITY ADVISORY: the source appears to carry "
                        "burned-in captions during "
                        f"{int(round(burned_score * 100))}% of sampled "
                        "speaking moments; the new layer may stack words.")
                if not placement_locked:
                    preferred = ((parsed_style or {}).get("position") or
                                 ("middle" if preset in ("spotlight", "lyric")
                                  else "bottom"))
                    placement, unsafe, analyzed = _caption_placement_track(
                        ctx, edl, spatial_track, preferred)
                    if analyzed and unsafe / analyzed > 0.35:
                        spatial_notes.append(
                            f"\nQUALITY ADVISORY: {unsafe} of {analyzed} "
                            "analyzed caption moments had no clean "
                            "top/middle/bottom band. The captions were still "
                            "committed; inspect the preview and mute or "
                            "restyle any collisions.")
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
                and (parsed_style or {}).get("animation") not in (None, "none"):
            karaoke_note += ("\nNote: dynamic karaoke captions animate "
                             "word-by-word already — the 'animation' "
                             "entrance style only applies to static "
                             "captions and is ignored here.")
        if premium and (parsed_style or {}).get("dynamic"):
            karaoke_note += (f"\nNote: preset '{preset}' drives its own "
                             "word-by-word animation — the 'dynamic' flag "
                             "is ignored while a preset is set.")
        if premium and (parsed_style or {}).get("animation") \
                not in (None, "none") and preset != "elegant":
            karaoke_note += (f"\nNote: preset '{preset}' animates word-by-"
                             "word — the 'animation' entrance style only "
                             "applies to static looks and is ignored here.")
        if emphasis_words and not premium:
            karaoke_note += ("\nNote: emphasis_words only take effect with "
                            "a premium preset — pass style "
                            "{preset:'clean'} to "
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
        # Filler words are in the index but never burned (see captions.py), so
        # this gate counts what will actually be SHOWN — otherwise a video of
        # nothing but hesitations would pass a check about visible text.
        all_words = [w for w in (ctx.index.get("words") or [])
                     if not w.get("filler")]
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
        auto_emphasis = False
        emphasis_mode = None
        if premium and emphasis_words is None:
            # A premium preset without hierarchy was the dominant production
            # failure: the model omitted an optional argument, so the most
            # important part of the design simply never activated. Make the
            # product own the default; an explicit [] still means "none".
            emphasis_words = _auto_caption_emphasis(ctx, edl)
            emphasis_mode = "auto"
            auto_emphasis = bool(emphasis_words)
            if auto_emphasis:
                karaoke_note += (f"\nAutomatically selected "
                                  f"{len(emphasis_words)} semantic emphasis "
                                  "word(s) from the kept transcript.")
        elif premium:
            emphasis_mode = "manual" if emphasis_words else "off"
        cfg = {"mode": "from_transcript",
               "design_version": CAPTION_DESIGN_VERSION,
               "max_words_per_caption": mw,
               "style": parsed_style,
               "placement_track": placement or None}
        if emphasis_words:
            cfg["emphasis_words"] = emphasis_words
        if emphasis_mode:
            cfg["emphasis_mode"] = emphasis_mode
        edl["captions"] = _bake_karaoke_group(cfg)
        desc = "captions from transcript enabled"
        if premium:
            desc += f", preset {preset}"
        if mw:
            desc += f", <= {mw} words each"
        if emphasis_words:
            desc += (f", {len(emphasis_words)} emphasis words"
                     + (" auto-selected" if auto_emphasis else ""))
        if parsed_style:
            desc += f", style {parsed_style}"
        if placement:
            desc += f", {len(placement)} measured placement span(s)"
        return (ctx.write_edl(edl, desc) + karaoke_note
                + directed_style_note + "".join(spatial_notes))
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
                '{"preset":"clean|documentary|broadcast|reels|podcast|beast|karaoke|...|classic",'
                '"color":"#RRGGBB","size":"s|m|l|xl","size_scale":0.5-3.0,'
                '"position":"bottom|top|middle","uppercase":true|false,'
                '"dynamic":true|false,"highlight_color":"#RRGGBB",'
                '"animation":"none|fade|pop|slide_up|punch|blur_in|whip|flash|rise|drop|elastic|bounce|swing|zoom_blur",'
                '"font":"<bundled family>","effect":"chroma|chrome|glow",'
                '"layout":"stack|flow","leading":0.5-2.2,'
                '"emphasis":"big|huge|accent|pop|box|serif|chrome|glow|chroma",'
                '"emphasis_scale":1.0-3.0,"outline_color":"#RRGGBB",'
                '"outline_width":0-12,"shadow":0-12,'
                '"background_color":"#RRGGBB","background_opacity":0-1,'
                '"tracking":-8-24,"text_align":"left|center|right",'
                '"anchor_y":0.05-0.95}')
    # Mirrors captions.STYLE_KEYS (+ dynamic/uppercase, which are booleans
    # handled separately there). A field missing HERE is rejected outright;
    # a field missing from STYLE_KEYS is accepted and then silently ignored.
    unknown = sorted(set(style) - {"color", "size", "size_scale", "position",
                                   "dynamic", "highlight_color", "animation",
                                   "preset", "uppercase", "font", "effect",
                                   "layout", "leading", "emphasis",
                                   "emphasis_scale", "outline_color",
                                   "outline_width", "shadow",
                                   "background_color", "background_opacity",
                                   "tracking", "text_align", "anchor_y"})
    if unknown:
        return (f"ERR: unknown style field(s) {unknown} — the style fields are "
                "preset, color, size, size_scale, position, uppercase, "
                "dynamic, highlight_color, animation, font, effect, layout, "
                "leading, emphasis, emphasis_scale, outline_color, "
                "outline_width, shadow, background_color, "
                "background_opacity, tracking, text_align and anchor_y. "
                "anchor_y is the exact vertical output-frame fraction. "
                "preset picks "
                "a look (clean/documentary/broadcast/retro/neon/podcast/"
                "reels/beast/karaoke/elegant/stacked/iridescent/chrome/editorial/"
                "fashion/luxe/impact/lyric/classic); "
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
                '{"preset":"clean|documentary|broadcast|reels|podcast|beast|karaoke|...|classic",'
                '"color":"#RRGGBB","size":"s|m|l|xl",'
                '"position":"bottom|top|middle","dynamic":true|false,'
                '"highlight_color":"#RRGGBB","leading":0.5-2.2,'
                '"emphasis_scale":1.0-3.0,"animation":"none|fade|pop|slide_up|punch|blur_in|whip|flash|rise|drop|elastic|bounce|swing|zoom_blur"}.')
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
        # A stored placement_track has higher render priority than the global
        # style.  Once the caller explicitly fixes a position/anchor, keeping
        # that adaptive track would make the accepted style update a visual
        # no-op for some shots.  None is signature-safe and validates for both
        # historical and current transcript-caption EDLs.
        if any(partial.get(key) is not None
               for key in ("position", "anchor_y")):
            new["placement_track"] = None
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


def set_caption_fixes(ctx, replacements=None, clear=False):
    """Correct the SPELLING of burned captions without touching their timing.

    "En el subtítulo tienes que escribir Dios, Ecuador, Jesús." Two users asked
    for this on the same day — one twice, in two different projects — and the
    answer was that captions burn the transcript's own words and their case
    could not be changed. It is the single most visible thing on the screen and
    the transcriber gets names wrong by design: it writes what it heard, in
    lower case, with no idea that Dios is a name.

    Timings are never touched, so word-by-word presets stay frame-accurate.
    """
    edl = dict(ctx.latest_edl()["json"])
    caps = edl.get("captions")
    if not isinstance(caps, dict) or caps.get("mode") != "from_transcript":
        return ("REJECTED: text fixes apply to from_transcript captions "
                "only — call add_captions('from_transcript') first. For "
                "captions you dictated yourself, edit the item's text.")
    if clear:
        merged = dict(caps)
        merged["text_fixes"] = None
        edl["captions"] = merged
        return ctx.write_edl(edl, "cleared caption text fixes")
    if not isinstance(replacements, list) or not replacements:
        return ("REJECTED: replacements must be a non-empty array of "
                "[wrong, right] pairs, e.g. "
                "[[\"dios\",\"Dios\"],[\"ushula\",\"Ujjwala\"]]. Pass "
                "clear=true to remove all fixes.")
    pairs, bad = [], []
    for r in replacements:
        try:
            src, dst = ((r.get("from"), r.get("to")) if isinstance(r, dict)
                        else (r[0], r[1]))
            src, dst = str(src).strip(), str(dst).strip()
        except (IndexError, KeyError, TypeError, ValueError):
            bad.append(str(r)[:40])
            continue
        if not src or not dst:
            bad.append(str(r)[:40])
        elif len(src.split()) != len(dst.split()):
            bad.append(f"'{src}' -> '{dst}'")
        else:
            pairs.append([src, dst])
    if not pairs:
        return ("REJECTED: no usable pairs. Each must be [wrong, right] with "
                "the SAME number of words on both sides (a replacement that "
                "changes the word count would have to delete a word that "
                "still has time on the clock). Rejected: "
                + "; ".join(bad[:5]) + ".")
    existing = [list(p) for p in (caps.get("text_fixes") or [])]
    by_src = {p[0].casefold(): p for p in existing}
    for p in pairs:
        by_src[p[0].casefold()] = p
    merged = dict(caps)
    merged["text_fixes"] = list(by_src.values())
    edl["captions"] = merged
    shown = ", ".join(f"'{s}'->'{d}'" for s, d in pairs[:6])
    res = ctx.write_edl(edl, f"caption text fixes: {shown}"
                             + (f" (+{len(pairs) - 6} more)"
                                if len(pairs) > 6 else ""))
    if res.startswith("EDL v"):
        res += ("\nOnly the burned TEXT changes — word timings, the audio and "
                "the cut are untouched, so word-by-word presets stay in sync. "
                "Matching ignores case and punctuation, so one pair fixes "
                "every occurrence.")
        if bad:
            res += ("\nSkipped (both sides must have the same word count): "
                    + "; ".join(bad[:4]) + ".")
    return res


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
    cleared_adaptive_placement = isinstance(caps, dict) \
        and bool(caps.get("placement_track")) \
        and any(partial.get(key) is not None
                for key in ("position", "anchor_y"))
    merged = merge_caption_style(caps, partial)
    # A style write is the migration boundary: the historical EDL stays on
    # its frozen renderer, while this newly-created version opts into the
    # prosody-aware phrase and optical-layout engine.  Manual caption items
    # have authored text/timings and therefore no transcript design version.
    if isinstance(merged, dict):
        merged["design_version"] = CAPTION_DESIGN_VERSION
    # the EFFECTIVE premium preset after the patch ('classic' = legacy)
    eff_preset = None
    if isinstance(merged, dict):
        eff_preset = (merged.get("style") or {}).get("preset")
        if eff_preset == "classic":
            eff_preset = None
    emph_note = ""
    auto_emphasis = False
    if emphasis_words is None and eff_preset and isinstance(merged, dict) \
            and not merged.get("emphasis_words") \
            and merged.get("emphasis_mode") != "off":
        auto = _auto_caption_emphasis(ctx, edl)
        merged["emphasis_mode"] = "auto"
        if auto:
            merged["emphasis_words"] = auto
            auto_emphasis = True
            emph_note = (f"\nAutomatically selected {len(auto)} semantic "
                         "emphasis word(s) from the kept transcript.")
    if emphasis_words is not None:
        if isinstance(merged, dict):
            merged["emphasis_words"] = emphasis_words or None
            merged["emphasis_mode"] = "manual" if emphasis_words else "off"
            if emphasis_words and not eff_preset:
                emph_note = ("\nNote: emphasis_words only take effect with "
                             "a premium preset — set style "
                             "{preset:'clean'} to "
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
    if "animation" in partial:
        eff_style = (merged.get("style") or {}) if isinstance(merged, dict) \
            else {}
        if partial["animation"] == "none" and eff_preset:
            karaoke_note += (f"\nAnimation is OFF for preset '{eff_preset}': "
                              "no fades, pops, slides or scale punches are "
                              "rendered. Karaoke/reveal word state can still "
                              "change exactly when each spoken word begins.")
        elif eff_preset and eff_preset != "elegant":
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
    if auto_emphasis:
        desc += f", {len(merged.get('emphasis_words') or [])} emphasis words auto-selected"
    result = ctx.write_edl(edl, desc)
    result += karaoke_note + emph_note
    if cleared_adaptive_placement:
        result += ("\nCaption placement is locked for the whole video; the "
                   "previous shot-aware position changes were removed.")
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
                "the user that plainly and ask for the song itself, or "
                "offer to find one (search_music).")
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

    Two doors. An uploaded VIDEO resolves through _audio_from_clip;
    anything else falls through to the project-asset guard below — including
    the check that catches the pipeline's own extracted speech track, the
    cause of the original inaudible-music bug. (The bundled `library:`
    scheme no longer exists: old EDLs were migrated to plain storage keys,
    and a stale library: string from ancient chat history lands in the
    honest not-a-music-asset rejection below.)"""
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio "
            "track (a transcription artifact), not background music — "
            "mixing it in would only double the speaker's voice under "
            "itself, near-inaudibly. Use a real music file instead: "
            "search_music to find one online, or "
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
                if avail else "No music uploaded to this project — "
                              "search_music finds tracks online, "
                              "fetch_music downloads one, and fetch_url "
                              "ingests any music link the user pastes.")
        return None, f"REJECTED: '{storage_key}' is not a music asset here. {hint}"
    return {"name": _asset_name(asset),
            "duration_s": asset.get("duration_s"), "library": False,
            "storage_key": storage_key}, None


def _search_hit_cache_key(ctx, lane):
    return f"search_hits:{lane}:project:{ctx.project_id}"


def _remember_search_hits(ctx, lane, hits):
    """Keep the latest small result page across agent turns, best effort."""
    try:
        ctx.db.run(dbx.kv_put, _search_hit_cache_key(ctx, lane),
                   json.dumps({h["id"]: h for h in hits[:12]}))
    except Exception:
        pass


def _recover_search_hit(ctx, lane, result_id, resolver):
    rid = str(result_id or "").strip()
    current = (getattr(ctx, f"_{lane}_hits", None) or {}).get(rid)
    if current:
        return current, None
    try:
        raw = ctx.db.run(dbx.kv_get, _search_hit_cache_key(ctx, lane))
        cached = json.loads(raw) if raw else {}
        if isinstance(cached, dict) and isinstance(cached.get(rid), dict):
            return cached[rid], None
    except Exception:
        pass
    try:
        return resolver(rid), None
    except Exception as exc:
        return None, str(exc)[:180]


def search_music(ctx, query, min_seconds=None, max_seconds=None,
                 commercial_use=None):
    """READ: find music on the open web (round 98; the bundled pack is
    deleted). Results carry license + author so the obligation travels
    with the track instead of vanishing into an export."""
    if not music_search.available():
        return ("REJECTED: music search is disabled on this deployment — "
                "use the user's uploads (list_assets(kind='music')) or "
                "ask_user for a file.")
    try:
        mn = float(min_seconds) if min_seconds is not None else None
        mx = float(max_seconds) if max_seconds is not None else None
    except (TypeError, ValueError):
        return "REJECTED: min_seconds/max_seconds must be numbers."
    # Do not infer tool permission from keywords in the chat.  Search is
    # commercial-safe by default; the agent may explicitly set false when a
    # non-commercial catalog is appropriate.
    commercial = commercial_use is not False
    try:
        hits = music_search.search(query, min_s=mn, max_s=mx,
                                   commercial_only=commercial)
    except music_search.MusicSearchError as e:
        return (f"Music search failed ({str(e)[:180]}). Try a simpler "
                "genre query, or use an uploaded file "
                "(list_assets(kind='music')).")
    relaxed_duration = False
    if not hits and (mn is not None or mx is not None):
        # A short edit needs only a short SLICE of a song, not a song whose
        # source file is itself 8-30 seconds long.  Openverse is mostly full
        # tracks, so applying the program duration to catalog results turned
        # valid queries into repeated false-empty searches.  Preserve every
        # licensing gate, relax only source duration, and tell the agent why.
        try:
            hits = music_search.search(query, commercial_only=commercial)
            relaxed_duration = bool(hits)
        except music_search.MusicSearchError as e:
            return (f"Music search failed ({str(e)[:180]}). Try a simpler "
                    "genre query, or use an uploaded file "
                    "(list_assets(kind='music')).")
    if not hits:
        return (f"No tracks matched '{query}'. Search with "
                "broader GENRE words ('dark phonk', 'lofi chill beat', "
                "'cinematic piano', 'upbeat funk') — for a SPECIFIC song "
                "find_song looks up its link, and a TRENDING platform "
                "sound only the user can provide (upload or a clip that "
                "carries it); platforms license trend audio inside their "
                "own apps only.")
    ctx._music_hits = {h["id"]: h for h in hits}
    _remember_search_hits(ctx, "music", hits)
    gate = (" Commercial-use request detected: non-commercial tracks were "
            "excluded." if commercial else "")
    duration_note = (
        " No source track matched the requested duration, so these longer "
        "tracks are candidates to trim/fit to the edit."
        if relaxed_duration else "")
    return ("Tracks found (each line carries its license terms — relay "
            "them):\n- "
            + "\n- ".join(music_search.describe(h) for h in hits)
            + "\nfetch_music(id) downloads one into the project. Then "
              "add_music lays it in — tell the user which track you "
              "chose and its license line (a CC BY credit is theirs to "
              "carry in the caption)." + duration_note + gate)


def fetch_music(ctx, id):
    """WRITE (to the project's assets): download a search_music hit and
    register it as a normal music asset, ready for add_music."""
    hit, recover_error = _recover_search_hit(
        ctx, "music", id, music_search.resolve)
    if not hit:
        return ("REJECTED: that music result id could not be recovered"
                + (f" ({recover_error})" if recover_error else "")
                + ". Call search_music and choose one of its current ids.")
    lp = os.path.join(ctx.workdir, f"musicfetch_{uuid.uuid4().hex[:8]}.mp3")
    try:
        music_search.download(hit, lp)
    except Exception as e:
        return (f"Could not download that track ({str(e)[:160]}). Try "
                "another result. Do NOT claim music was added.")
    dur = music_search.probe_duration_s(lp)
    if dur < 3.0:
        return ("REJECTED: the downloaded file is not playable audio "
                "(or under 3s) — try another result. Do NOT claim music "
                "was added.")
    key = f"music/{ctx.project_id}/{uuid.uuid4().hex[:12]}.mp3"
    try:
        storage.upload_file(lp, key, "audio/mpeg")
    except Exception as e:
        return (f"Downloaded but could not save it ({str(e)[:140]}) — try "
                "again. Do NOT claim music was added.")
    title = (hit.get("title") or "track").strip()
    artist = hit.get("artist")
    fname = ((f"{title} — {artist}" if artist else title)[:80]) + ".mp3"
    note = music_search.license_note(hit)
    ctx.db.run(dbx.insert_asset, ctx.project_id, "music", key,
               bytes_=os.path.getsize(lp), duration_s=dur,
               meta={"filename": fname, "source": hit.get("provider"),
                     "source_url": hit.get("page_url") or hit.get("_url"),
                     "license": hit.get("license"), "license_note": note,
                     "author": artist,
                     "caption": "found online by search_music"})
    ctx.audio_fetched.append(key)
    return (f"Fetched \"{title}\"{' by ' + artist if artist else ''} — "
            f"{dur:.0f}s, saved as storage_key={key}. License: {note}. "
            f"Next: add_music(storage_key='{key}') — set_music_fit retimes it, "
            "get_audio_analysis(asset_key) measures its BPM/beats for "
            "beat_align_cuts.")


def find_song(ctx, query):
    """READ: candidate web links for a song the user named. The pick is
    downloaded by fetch_url, so this tool moves no bytes itself."""
    if not song_find.available():
        return ("REJECTED: song-link search is disabled on this "
                "deployment. Ask the user to paste a link to the track "
                "(fetch_url downloads it) or upload the file.")
    if not song_find.allowed_for((getattr(ctx, "job", None) or
                                  {}).get("user_id")):
        return ("REJECTED: song-link search is not enabled for this "
                "account. Ask the user to paste a link to the track "
                "(fetch_url downloads it) or upload the file.")
    q = (query or "").strip()
    if not q:
        return "REJECTED: find_song needs the song name (artist helps)."
    try:
        hits = song_find.search(q)
    except song_find.SongFindError as e:
        return (f"Song search failed ({e}). Tell the user plainly and ask "
                "them to paste a link instead — do NOT claim anything was "
                "found or added.")
    if not hits:
        return (f"No results for \"{q}\". Check the spelling with the "
                "user, or ask them to paste a link to the track "
                "(fetch_url downloads it).")
    yt_lines = "\n- ".join(song_find.describe(h) for h in hits[:5])
    # The other catalog rides along on every search. YouTube walls
    # music-label content hardest from datacenter IPs (Aug 9: every label
    # upload tried was blocked while SoundCloud fetched clean), so the
    # escape route has to be IN HAND before the first candidate fails —
    # a second search after a wall is a tool call the model often skips.
    try:
        sc = song_find.search_soundcloud(q)[:3]
    except song_find.SongFindError:
        sc = []
    sc_lines = "\n- ".join(song_find.describe(h) for h in sc)
    _tail = ("These search results establish identity/authenticity only; "
             "they do NOT verify a usage license. A downloadable public "
             "upload is not automatically licensed for republication. "
             "Prefer the artist's own/'- Topic' channel or 'Official "
             "Audio'; avoid lyric/sped-up/loop/cover versions UNLESS their "
             "words asked for one, and NEVER a full album/mix — ONE track "
             "only (huge files are refused after minutes of download). "
             "fetch_url(url=<pick>, as_kind='music') downloads the pick "
             "ready for add_music. In your reply, tell the user exactly "
             "which version you grabbed (title + channel) so they can "
             "correct the pick.")
    # When the boot probe says THIS server is walled by YouTube (the normal
    # state without a residential proxy), lead with SoundCloud — recommending
    # a source the datacenter IP cannot reach just buys a guaranteed failed
    # download first. Self-healing: add YTDLP_PROXY and the next probe clears
    # the wall, so YouTube (better masters) leads again with no code change.
    if sc and ytaccess.youtube_walled():
        body = (f"{len(sc)} SoundCloud track(s) for \"{q}\" (this server "
                "downloads these reliably; YouTube is currently blocking "
                "this server's IP for music, so start here):\n- " + sc_lines)
        if yt_lines:
            body += ("\nYouTube candidates (usually WALLED from this server "
                     "right now — try one only if none of the SoundCloud "
                     "tracks is the song):\n- " + yt_lines)
        return (body + "\nPick the one that IS the song the user named. "
                + _tail)
    sc_block = ""
    if sc_lines:
        sc_block = ("\nSoundCloud fallbacks (this server downloads these "
                    "reliably even when YouTube walls):\n- " + sc_lines)
    return (f"{min(len(hits), 5)} candidate link(s) for \"{q}\", best "
            "guess first:\n- " + yt_lines + sc_block +
            "\nPick the one that IS the song the user named. If YouTube "
            "blocks a candidate (\"not a bot\" wall), that is per-UPLOAD — "
            "try the next candidate"
            + (", then the SoundCloud fallbacks" if sc_block else "")
            + "; don't give up on the song. " + _tail)


def _can_receive_images(ctx):
    """Whether this tool transport can put pixels in the editor's next turn."""
    return (getattr(ctx, "sight_out", False) or
            (getattr(ctx, "direct_sight", False)
             and llm.agent_sees(getattr(ctx, "agent_model", None))))


def _queue_candidate_thumbs(ctx, hits, limit=5):
    """Put the candidates' own pictures in front of the agent's eyes.

    A title is not a shot: "Elon Musk at TED" could be a podium wide, a
    blurry crowd phone-cam, or a meme edit. Each hit's thumbnail is
    downloaded small and queued on ctx.pending_images labeled with the
    hit's id, so the model picks b-roll the way an editor does — by
    LOOKING at the results grid, not reading it. Best-effort: any failed
    thumbnail just misses the sheet, and a blind deployment skips the
    downloads entirely (the text lines remain the whole story there).
    Returns how many pictures were queued."""
    # Native agent turns consume pending_images directly. MCP turns cannot
    # put binary image content into the model call, but sight_out drains the
    # same queue into the tool response as a labeled contact sheet. The old
    # direct_sight-only gate made MCP stock selection text-blind even though
    # its transport was fully capable of returning the thumbnails.
    if not _can_receive_images(ctx):
        return 0
    queued = 0
    for h in hits[:limit]:
        turl = h.get("_thumb")
        if not turl:
            continue
        local = os.path.join(ctx.workdir,
                             f"cand_{uuid.uuid4().hex[:8]}.jpg")
        try:
            net_fetch.download(turl, local, max_bytes=3 * 1024 * 1024,
                               timeout_s=15)
            # Stock hits are addressed by id; footage hits by their URL —
            # the label must be the exact string the model passes onward.
            ctx.pending_images.append((h.get("id") or h.get("url"), local))
            # Evidence provenance for the follow-up selector. Some providers
            # return no description at all; in that case this thumbnail is the
            # only factual basis for saying what the shot depicts.
            h["_thumbnail_delivered"] = True
            queued += 1
        except Exception:
            continue
    return queued


def _queue_download_review(ctx, path, kind, duration_s=None, label="media",
                           limit=4):
    """Attach representative pixels from a just-downloaded visual asset.

    Search thumbnails prove only that a candidate looked plausible. The file
    ultimately downloaded can be a different rendition, begin on a title
    card, or contain an irrelevant middle. Pulling frames *before the scratch
    directory is deleted* gives the agent direct evidence from the actual
    asset it may place. Audio deliberately skips this path.

    Returns the number of source frames queued. Review is best-effort: a
    frame-extraction problem must not turn a valid, safely stored download
    into a false failure.
    """
    if kind not in (url_media.KIND_VIDEO, url_media.KIND_IMAGE) or \
            not _can_receive_images(ctx):
        return 0
    frames, labels = [], []
    try:
        if kind == url_media.KIND_IMAGE:
            ext = os.path.splitext(path)[1] or ".img"
            local = os.path.join(
                ctx.workdir, f"review_{uuid.uuid4().hex[:8]}{ext}")
            shutil.copyfile(path, local)
            frames, labels = [local], ["full image"]
        else:
            try:
                dur = float(duration_s or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            if dur <= 0:
                try:
                    dur = float((media.probe(path) or {}).get("duration") or 0)
                except Exception:
                    dur = 0.0
            # Avoid the first/last frame, which are disproportionately black
            # slates and fades. Four spread samples reveal subject, quality,
            # shot continuity and whether this is actually useful B-roll.
            fracs = (0.08, 0.34, 0.61, 0.87)[:max(1, min(int(limit), 4))]
            times = ([min(max(dur * f, 0.0), max(0.0, dur - 0.04))
                      for f in fracs] if dur > 0.08 else [0.0])
            seen_t = set()
            for t in times:
                t = round(t, 2)
                if t in seen_t:
                    continue
                seen_t.add(t)
                local = os.path.join(
                    ctx.workdir, f"review_{uuid.uuid4().hex[:8]}.jpg")
                try:
                    media.frame_at(path, t, local, width=768)
                except Exception:
                    continue
                frames.append(local)
                labels.append(f"{t:g}s")
        if not frames:
            return 0
        _deliver_frames(
            ctx, frames, labels,
            "Verify subject, visual quality, relevance, logos/watermarks and "
            "the exact useful moment before placing this asset in the edit.",
            f"Automatic review of the downloaded {label}")
        return len(frames)
    except Exception:
        return 0


def find_footage(ctx, query):
    """READ: candidate web links for real footage of a NAMED topic. The
    pick is downloaded by fetch_url, so this tool moves no bytes itself."""
    if not song_find.footage_available():
        return ("REJECTED: web footage search is disabled on this "
                "deployment. Use search_stock for library b-roll, a link "
                "the user pastes (fetch_url), or their uploads.")
    if not song_find.footage_allowed_for((getattr(ctx, "job", None) or
                                          {}).get("user_id")):
        return ("REJECTED: web footage search is not enabled for this "
                "account. Use search_stock for library b-roll, or ask the "
                "user to paste a link (fetch_url downloads it).")
    q = (query or "").strip()
    if not q:
        return ("REJECTED: find_footage needs the topic — name the THING "
                "('spacex starship launch', 'tesla factory robots'), not "
                "a mood.")
    try:
        hits = song_find.search_footage(q)
    except song_find.SongFindError as e:
        return (f"Footage search failed ({e}). Tell the user plainly — do "
                "NOT claim footage was found. search_stock and their own "
                "uploads still work.")
    if not hits:
        return (f"No footage found for \"{q}\". Name the concrete THING "
                "shown on screen, or fall back to search_stock / a "
                "generated image.")
    lines = "\n- ".join(song_find.describe(h) for h in hits[:5])
    seen = _queue_candidate_thumbs(ctx, hits)
    eye = ("\nTheir thumbnails are attached below, labeled by link — pick "
           "by LOOKING at them: real footage of the subject, not a "
           "talking head, a graphic or a meme frame. "
           if seen else "\n")
    return (f"{min(len(hits), 5)} candidate link(s) for \"{q}\", best "
            "guess first:\n- " + lines + eye +
            "Pick REAL footage OF the subject — prefer the "
            "subject's own channel and short clips (a few minutes; long "
            "videos get refused for size); avoid reaction/commentary "
            "versions. Then fetch_url(url=<pick>, as_kind='clip') "
            "downloads it as a project clip. Next: look_at_asset to find "
            "the exact seconds worth showing, then a cutaway "
            "(add_overlay fit='cover' over the words that mention it) or "
            "insert_media. In your reply, tell the user what footage you "
            "used and where it came from (title + channel).")


def search_sfx(ctx, query, max_seconds=None):
    """READ: find a real recorded sound effect on the open web — the
    editor's whoosh/shutter/click, not a synthesized guess."""
    if not sfx_search.available():
        return ("REJECTED: sound-effect search is disabled on this "
                "deployment — place a file the user uploads (add_sfx), or "
                "take the sound off a clip they sent.")
    try:
        mx = float(max_seconds) if max_seconds is not None else None
    except (TypeError, ValueError):
        return "REJECTED: max_seconds must be a number."
    try:
        hits = sfx_search.search(query, max_s=mx)
    except sfx_search.SfxSearchError as e:
        return (f"Sound search failed ({str(e)[:180]}). Try a simpler "
                "query ('whoosh', 'camera shutter', 'pop').")
    except Exception as e:
        return (f"Sound search failed ({str(e)[:180]}). Try again or use "
                "an uploaded file.")
    if not hits:
        return (f"No sounds matched '{query}'. Use the PHYSICAL name of "
                "the sound ('whoosh', 'camera shutter', 'keyboard click', "
                "'pop', 'riser') rather than a mood word.")
    ctx._sfx_hits = {h["id"]: h for h in hits}
    _remember_search_hits(ctx, "sfx", hits)
    return ("Sounds found (each line carries its license terms — relay "
            "them):\n- "
            + "\n- ".join(sfx_search.describe(h) for h in hits)
            + "\nfetch_sfx(id) downloads one into the project. Then "
              "add_sfx places it on its moment.")


def _download_sfx_hit(ctx, hit):
    """Download/store one resolved SFX hit. Returns (asset, error_text)."""
    lp = os.path.join(ctx.workdir, f"sfxfetch_{uuid.uuid4().hex[:8]}.mp3")
    try:
        sfx_search.download(hit, lp)
    except Exception as e:
        return None, (f"Could not download that sound ({str(e)[:160]}). Try "
                      "another result. Do NOT claim a sound was added.")
    dur = music_search.probe_duration_s(lp)
    if dur <= 0.05:
        return None, ("REJECTED: the downloaded file is not playable audio — "
                      "try another result. Do NOT claim a sound was added.")
    key = f"sfx/{ctx.project_id}/{uuid.uuid4().hex[:12]}.mp3"
    try:
        storage.upload_file(lp, key, "audio/mpeg")
    except Exception as e:
        return None, (f"Downloaded but could not save it ({str(e)[:140]}) — "
                      "try again. Do NOT claim a sound was added.")
    title = (hit.get("title") or "sound").strip()
    fname = title[:80] + ".mp3"
    note = sfx_search.license_note(hit)
    byte_count = os.path.getsize(lp)
    ctx.db.run(dbx.insert_asset, ctx.project_id, "music", key,
               bytes_=byte_count, duration_s=dur,
               meta={"filename": fname, "source": hit.get("provider"),
                     "source_url": hit.get("page_url") or hit.get("_url"),
                     "license": hit.get("license"), "license_note": note,
                     "author": hit.get("author"),
                     "caption": "found online by search_sfx"})
    if not hasattr(ctx, "audio_fetched"):
        ctx.audio_fetched = []
    ctx.audio_fetched.append(key)
    try:
        os.remove(lp)
    except OSError:
        pass
    return {"title": title, "duration_s": dur, "storage_key": key,
            "license_note": note, "hit": hit}, None


def fetch_sfx(ctx, id):
    """WRITE (to the project's assets): download a search_sfx hit and
    register it as a normal audio asset, ready for add_sfx."""
    hit, recover_error = _recover_search_hit(ctx, "sfx", id,
                                             sfx_search.resolve)
    if not hit:
        return ("REJECTED: that sound result id could not be recovered"
                + (f" ({recover_error})" if recover_error else "")
                + ". Call search_sfx and choose one of its current ids.")
    asset, error = _download_sfx_hit(ctx, hit)
    if error:
        return error
    title, dur, key, note = (asset["title"], asset["duration_s"],
                             asset["storage_key"], asset["license_note"])
    return (f"Fetched \"{title}\" — {dur:g}s, saved as storage_key={key}. "
            f"License: {note}. Next: add_sfx(storage_key='{key}', "
            f"at=<moment>) — every "
            "sound lands ON a nameable visible moment.")


def add_web_sfx(ctx, query, at, gain_db=-6.0, max_seconds=None):
    """Find the best real one-shot online, store it and place it in one call."""
    if not sfx_search.available():
        return ("REJECTED: web sound-effect search is disabled. Use add_sfx "
                "with a user-uploaded audio or video asset.")
    try:
        at_n = float(at)
        gain_n = float(gain_db)
        mx = float(max_seconds) if max_seconds is not None else None
    except (TypeError, ValueError):
        return "REJECTED: at, gain_db and max_seconds must be numbers."
    try:
        prog = program_duration(ctx.latest_edl()["json"])
    except Exception as e:
        return f"REJECTED: could not read the timeline ({str(e)[:120]})."
    if at_n < 0 or at_n > max(0.0, prog - 0.05):
        return (f"REJECTED: at={at_n:g}s is outside the program "
                f"(0 to {round(prog, 2)}s).")
    try:
        hits = sfx_search.search(query, max_s=mx, count=6)
    except Exception as e:
        return (f"Sound search failed ({str(e)[:180]}). Use a physical sound "
                "name such as 'cinematic whoosh' or 'camera shutter'.")
    if not hits:
        return (f"No usable one-shot matched '{query}'. Try a more physical "
                "sound name; nothing was fetched or placed.")
    _remember_search_hits(ctx, "sfx", hits)
    errors = []
    # CDN availability varies. Try the top three ranked real recordings so a
    # dead preview URL does not derail a whole edit turn.
    for hit in hits[:3]:
        asset, error = _download_sfx_hit(ctx, hit)
        if error:
            errors.append(error)
            continue
        placed = add_sfx(ctx, asset["storage_key"], at_n, gain_n)
        if placed.startswith("REJECTED"):
            return placed
        return (placed + f" Source: \"{asset['title']}\""
                f" ({hit.get('provider')}); license: "
                f"{asset['license_note']}. Selected automatically from "
                "ranked real one-shot recordings for the exact query "
                f"'{query}'.")
    return ("Could not download any of the top ranked sounds. "
            + " ".join(errors)[:400])


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
    """Display name for a music reference — the upload/fetch filename."""
    return _upload_name(ctx, key)


def _track_name(ctx, key):
    """Display name for ANY audio reference — the upload/fetch filename
    (both bundled schemes are gone; every key is a storage object)."""
    return _upload_name(ctx, key)


def _resolve_sfx(ctx, storage_key):
    """(sound, error) for an sfx reference.

    A structural twin of _resolve_music, and deliberately just as strict:
    every reference must be a project-owned asset — no schemes, no prefix
    matching (the renderer downloads whatever key it is handed with no
    project scoping, so a loose check here is a read primitive over the
    whole bucket; the old bundled `sfx:` scheme is gone, migrated to plain
    legacy-sfx/ storage keys).

    Fetched and uploaded sounds arrive as kind 'music' — an audio file is
    just an audio file, and whether it is a bed or a one-shot is an EDL
    decision, not an asset kind. An uploaded VIDEO is the second door
    (round 47): it resolves to the audio extracted from it, because "use
    the sound off this clip" is a thing users ask for and the picture is
    simply never used.
    """
    asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, storage_key)
    if asset and asset["kind"] == "audio":
        return None, (
            "REJECTED: that file is the video's OWN extracted audio track "
            "(a transcription artifact), not a sound effect. Use "
            "search_sfx to find the real sound online, or "
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
            "project. search_sfx finds real sounds online (fetch_sfx "
            "downloads one), or list_assets(kind='music') for the user's "
            "uploads.")
    return {"name": _asset_name(asset),
            "duration_s": asset.get("duration_s"), "library": False,
            "storage_key": storage_key}, None


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


def set_frame(ctx, ratio, mode="crop", focus_x=None, focus_y=None,
              _measured=False):
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
        # Say HOW MUCH picture the crop costs. "Center crop" is abstract;
        # "this discards 75% of the width" is the fact that decides whether
        # crop was the right operation, and a user whose wide gameplay
        # recording came back as a narrow slice of itself was told neither.
        try:
            v = ctx.index.get("video") or {}
            sw, sh = float(v.get("width") or 0), float(v.get("height") or 0)
            rw, rh = (float(x) for x in frame.ratio.split(":"))
            if sw > 0 and sh > 0:
                src_ar, out_ar = sw / sh, rw / rh
                lost = 1.0 - min(src_ar, out_ar) / max(src_ar, out_ar)
                if lost > 0.3:
                    res += (f" It also DISCARDS {lost * 100:.0f}% of the "
                            f"source picture ({int(sw)}x{int(sh)} -> "
                            f"{frame.ratio}). That is the RIGHT conversion "
                            "when they asked for a Short / TikTok / Reel / "
                            "crop-fill. If they asked to KEEP the HUD / "
                            "whole frame / letterbox, switch to "
                            "mode='pad_blur' instead.")
        except (TypeError, ValueError, AttributeError):
            pass
    if (res.startswith("EDL v") and frame.mode in ("pad", "pad_blur")
            and frame.ratio in ("9:16", "1:1", "4:5")):
        res += ("\nNote: pad/pad_blur LETTERBOXES the picture. If the user "
                "asked for a Short / TikTok / Reel / crop / 9:16 fill, they "
                "wanted the footage to FILL the phone — call set_frame "
                f"(\"{frame.ratio}\", \"crop\") or auto_reframe("
                f"\"{frame.ratio}\", mode=\"crop\"). pad_blur is only when "
                "they asked to keep the whole frame (HUD, letterbox, fit).")
    return res


# Below this share of the picture's detail surviving the crop window, a crop
# is not reframing — it is truncation, and the whole frame should be FITTED
# into the new aspect instead. Calibrated on the footage that produced the
# complaint: a wide game recording keeps ~0.35 of its detail through a 9:16
# crop (HUD, minimap and score all sit outside the window), while a
# centre-weighted talking head keeps well over 0.6.
CROP_DETAIL_KEEP_MIN = 0.55


def _spatial_face_points(sidecar, windows):
    """Face centres measured across a set of source-time windows.

    One largest face per sample avoids averaging two people visible in the
    same two-shot into the wall between them. The sidecar covers up to 96
    real frames, so a long video is no longer represented by five guesses.
    Returns (points, sample_coverage).
    """
    samples = []
    for sample in (sidecar or {}).get("samples") or []:
        try:
            t = float(sample.get("t"))
        except (TypeError, ValueError):
            continue
        if any(float(a) <= t <= float(b) for a, b in windows):
            samples.append(sample)
    points = []
    for sample in samples:
        boxes = sample.get("faces") or []
        if not boxes:
            continue
        try:
            box = max(boxes, key=lambda b: (float(b[2]) - float(b[0])) *
                      (float(b[3]) - float(b[1])))
            points.append(((float(box[0]) + float(box[2])) / 2.0,
                           (float(box[1]) + float(box[3])) / 2.0))
        except (TypeError, ValueError, IndexError):
            continue
    return points, len(points) / max(1, len(samples))


def _reframe_with_track(ctx, ratio, global_pt, preserve_unmeasured=True):
    """A crop that FOLLOWS the subject across shot changes, or None when a
    single point serves (one shot, one position, or anything unmeasurable).

    Round 100 — the wall bug. On a two-person podcast the subject sits left
    in some shots and right in others; the median of five samples lands
    BETWEEN them, and every Aug 8 shorts child cropped to the wall at
    x≈0.58. One point cannot frame footage whose subject moves at cuts, so:
    measure a face PER SHOT inside the kept footage, split the keep spans at
    the shot boundaries where the aim genuinely changes (a reframe on a cut
    reads as an edit; mid-shot it reads as a slide), and write the aims as
    frame.focus_track. Never with a transition style set — those fire on
    scene junctions, which is exactly where this splits.
    """
    shots = (ctx.index or {}).get("shots") or []
    if len(shots) < 2:
        return None
    edl = dict(ctx.latest_edl()["json"])
    if ((edl.get("effects") or {}).get("transition")):
        return None
    keep = [(float(s), float(e)) for s, e in (edl.get("keep") or [])]
    if not keep:
        return None
    sidecar = getattr(ctx, "_spatial", None) or \
        ((ctx.index or {}).get("spatial") or {})
    try:
        proxy = ctx.proxy_path()
    except Exception:
        proxy = None
        if not (sidecar.get("samples") or []):
            return None

    # One measurement window PER CAMERA SHOT, even when a filler/cut split the
    # kept material inside it. Treating those fragments as different shots
    # let one single-frame Haar false positive crop the first half of a wide
    # two-person shot while the second half correctly fitted. A camera shot
    # has one composition; edit gaps inside it do not create a new one.
    wins = []
    for sh in shots:
        fragments = []
        for ks, ke in keep:
            s = max(ks, float(sh.get("start", 0.0)))
            e = min(ke, float(sh.get("end", 0.0)))
            if e - s > 0.01:
                fragments.append([s, e])
        if fragments and sum(e - s for s, e in fragments) >= 0.4:
            # Bounding the kept fragments may span a removed filler gap. That
            # gap is fine for measurement and for focus metadata (no pixels
            # from it render); the original keep list below still owns cuts.
            wins.append([fragments[0][0], fragments[-1][1]])
    if len(wins) < 2:
        return None
    measured = []
    for w in wins:
        fp = os.path.join(ctx.workdir, f"track_{len(measured)}.jpg")
        pt = None
        spatial_pts, _coverage = _spatial_face_points(sidecar, [w])
        spatial_samples = []
        for sample in (sidecar or {}).get("samples") or []:
            try:
                if w[0] <= float(sample.get("t")) <= w[1]:
                    spatial_samples.append(sample)
            except (TypeError, ValueError):
                continue
        # Repeated temporal evidence beats a fresh one-frame detector. If the
        # index saw >=2 moments in this shot and found no repeatable face,
        # auto mode must fit it; asking Haar once more reintroduced the exact
        # false positive the 96-frame sidecar was built to eliminate.
        if spatial_pts and (len(spatial_samples) < 2 or
                            len(spatial_pts) >= 2):
            pt = subject.median_point(spatial_pts)
        try:
            if pt is None and proxy and len(spatial_samples) < 2:
                media.frame_at(proxy, (w[0] + w[1]) / 2.0, fp)
                pts, method = subject.points_from_frames([fp])
                if method == "faces" and pts:
                    pt = pts[0]
        except Exception:
            pass
        measured.append([w[0], w[1], pt])
    have = [m for m in measured if m[2]]
    if not have:
        return None
    if len(have) < 2 and not (preserve_unmeasured and
                              len(have) < len(measured)):
        return None
    # A no-face shot is not permission to inherit the prior speaker's crop:
    # it may be a screen, product, wide two-shot or B-roll, and inheriting is
    # the exact mechanism that produced blank-wall/empty-car vertical cuts.
    # Auto mode FITS that shot; an explicitly requested crop keeps the old
    # nearest measured aim because the user chose edge-to-edge truncation.
    for i, m in enumerate(measured):
        if m[2] is None:
            prev = next((measured[j][2] for j in range(i - 1, -1, -1)
                         if measured[j][2]), None)
            nxt = next((measured[j][2] for j in range(i + 1, len(measured))
                        if measured[j][2]), None)
            m[2] = prev or nxt or global_pt
            m.append("pad_blur" if preserve_unmeasured else "crop")
        else:
            m.append("crop")
    spans = []
    for s, e, pt, span_mode in measured:
        if spans and abs(spans[-1]["x"] - pt[0]) < 0.06 \
                and abs(spans[-1]["y"] - pt[1]) < 0.10 \
                and spans[-1].get("mode") == span_mode \
                and abs(spans[-1]["t1"] - s) < 0.05:
            spans[-1]["t1"] = e
            continue
        spans.append({"t0": round(s, 3), "t1": round(e, 3),
                      "x": round(pt[0], 3), "y": round(pt[1], 3),
                      "mode": span_mode})
    if len(spans) < 2:
        return None                      # one aim — the single point serves
    # Composition state is not editorial state. Older revisions split `keep`
    # at every focus boundary so each ffmpeg segment had one aim. The renderer
    # now performs that split locally (without changing the EDL timeline), so
    # retaining those synthetic keep edges only creates false "mid-word cut"
    # audits at ordinary camera cuts and sends the agent into unnecessary
    # repair passes. Preserve the user's actual cuts exactly; focus_track owns
    # only framing, and renderer.build_filtergraph owns its local blocks.
    med = subject.median_point([(sp["x"], sp["y"]) for sp in spans])
    edl["frame"] = {"ratio": str(ratio), "mode": "crop",
                    "focus_x": med[0], "focus_y": med[1],
                    "focus_track": spans}
    res = ctx.write_edl(
        edl, f"output frame set to {ratio} (crop) — the crop FOLLOWS the "
             f"subject across {len(spans)} camera positions, re-aiming at "
             "shot cuts")
    if not res.startswith("EDL v"):
        return None                      # validation refused — fall back
    fitted = sum(1 for sp in spans if sp.get("mode") == "pad_blur")
    res += (f"\nMeasured per shot: the subject sits in different places in "
            f"different shots (e.g. two speakers), so ONE fixed crop would "
            f"frame the wall between them. The crop re-aims at {len(spans)} "
            "shot boundaries instead — each cut lands on the person "
            "speaking's side of the frame.")
    if fitted:
        res += (f" {fitted} shot span(s) had no measured face, so those "
                "specific spans fit the whole picture over a blurred "
                "background instead of inheriting a previous crop and "
                "showing empty/irrelevant space.")
    return res


def auto_reframe(ctx, ratio="9:16", mode="auto"):
    """Convert the output frame to `ratio` and choose HOW honestly.

    Round 55. This tool only ever cropped, and aimed the crop as well as it
    could. That is right for a person — a vertical crop of a talking head is
    what vertical video IS — and wrong for everything whose content fills the
    frame. A user handed us a wide Mobile Legends recording, asked for 9:16,
    and got back a video with two thirds of its width cut off: "the dimensions
    part is corrupted, it's not adjusting the video to the new dimension it's
    just truncating it."

    Both halves of that are now measured rather than assumed:

      * WHERE the subject is — faces (subject.points_from_frames), now behind
        a real quorum so one Haar false positive on a HUD can no longer aim
        the crop at a corner, which is exactly what happened here;
      * WHETHER a crop is the right operation at all — subject.crop_detail_kept
        integrates the picture's gradient energy over the window the renderer
        would actually take. Detail that falls outside it is content the user
        would lose.

    mode='auto' (the default) picks crop or pad_blur from those two numbers.
    An explicit mode always wins — asking for a crop gets a crop.
    """
    if str(ratio) == "source":
        return set_frame(ctx, "source")
    mode = str(mode or "auto").lower()
    if mode not in ("auto", "crop", "pad", "pad_blur"):
        return ("REJECTED: mode must be 'auto' (measure the footage and "
                "choose), 'crop' (fill the frame, cutting the sides), 'pad' "
                "or 'pad_blur' (fit the WHOLE picture into the new frame).")
    # A measured per-shot track is strictly richer evidence than another
    # generic `auto` request. Preview critics commonly ask to "re-measure" a
    # composition they dislike; if that later measurement is temporarily
    # unavailable (or its broad five-frame detail score wins), replacing a
    # correct mixed fit/crop track with one global pad_blur leaves close-ups
    # tiny. That happened in production project 637: v2 correctly fitted the
    # wide two-shot and cropped the close-up, then a repair-pass auto_reframe
    # silently flattened both to pad_blur in v3.
    #
    # Keep the high-information state when it still covers every kept source
    # interval and the requested aspect is unchanged. Restored footage outside
    # the measured track, or a new aspect ratio, still falls through and gets
    # measured normally.
    current = ctx.latest_edl()["json"]
    current_frame = current.get("frame") or {}
    track = current_frame.get("focus_track") or []
    if mode == "auto" and str(current_frame.get("ratio")) == str(ratio) \
            and track:
        coverage = sorted(
            (float(span.get("t0", 0.0)), float(span.get("t1", 0.0)))
            for span in track
            if float(span.get("t1", 0.0)) - float(span.get("t0", 0.0))
            > 0.01)

        def _covered(start, end):
            cursor = float(start)
            for left, right in coverage:
                if right <= cursor + 1e-3:
                    continue
                if left > cursor + 1e-3:
                    return False
                cursor = max(cursor, right)
                if cursor >= float(end) - 1e-3:
                    return True
            return cursor >= float(end) - 1e-3

        keep = current.get("keep") or [[0.0, ctx.duration]]
        if coverage and all(_covered(start, end) for start, end in keep):
            return ("NO CHANGE — the current frame already has a measured "
                    f"per-shot focus_track covering every kept interval at "
                    f"{ratio}. Preserved that mixed composition instead of "
                    "downgrading it to one global crop/fit; change the mode "
                    "or ratio explicitly if uniform framing is intended.")
    if mode in ("pad", "pad_blur"):
        # pad modes never discard picture, so there is nothing to aim.
        return set_frame(ctx, ratio, mode, _measured=True)
    if not ctx.has_main_video:
        return set_frame(ctx, ratio, "crop" if mode == "auto" else mode,
                         _measured=True)
    try:
        proxy = ctx.proxy_path()
    except Exception as err:
        res = set_frame(ctx, ratio,
                        "pad_blur" if mode == "auto" else "crop",
                        _measured=True)
        if res.startswith("EDL v"):
            res += (f"\nNote: could not fetch frames ({err}), so auto mode "
                    "preserved the whole picture with pad_blur instead of "
                    "guessing a crop target.")
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
        res = set_frame(ctx, ratio,
                        "pad_blur" if mode == "auto" else "crop",
                        _measured=True)
        if res.startswith("EDL v"):
            res += ("\nNote: could not extract frames, so auto mode kept "
                    "the whole picture with pad_blur instead of inventing a "
                    "center target.")
        return res

    # MEASURE FIRST (round 52). A face is found in the pixels, in milliseconds,
    # with no provider and no credits — and on the footage people actually
    # reframe (someone talking) that measurement beats asking a multimodal
    # model to estimate a coordinate. The vision path below is now the fallback
    # for footage with no face in it, not the only route: for five users on one
    # day it was the only route, it was unconfigured, and every one of them got
    # a dead-centre crop with an apology attached.
    pts, method = subject.points_from_frames(frames)
    # Prefer the structured temporal track when it has a real quorum. The
    # five local frames above still measure crop detail, but long/multi-shot
    # videos now derive WHERE from up to 180 indexed frames instead of gaps
    # tens of seconds wide.
    sidecar = getattr(ctx, "_spatial", None) or \
        ((ctx.index or {}).get("spatial") or {})
    # Existing projects predate the spatial sidecar. Compute and persist it
    # on their first real auto-reframe so they receive the same shot coverage
    # as new uploads instead of quietly falling back to five broad samples.
    if not (sidecar.get("samples") or []) and (
            isinstance(ctx, ToolContext) or
            getattr(ctx, "enforce_spatial", False)):
        try:
            sidecar = _get_spatial(ctx)
        except Exception:
            # The five frames extracted above still provide a bounded,
            # conservative fallback; auto mode fits rather than guessing when
            # those cannot prove a safe crop.
            sidecar = {}
    spatial_pts, spatial_coverage = _spatial_face_points(sidecar, keep)
    if len(spatial_pts) >= 3 and spatial_coverage >= 0.35:
        pts, method = spatial_pts, "faces_spatial"
    # Global coverage is the wrong quorum for a shot-local composition. In a
    # wide interview followed by a one-second close-up, three strong face
    # detections can be only 3/9 samples: that correctly says "most of the
    # VIDEO has no face", but it is excellent evidence that ONE SHOT does.
    # Give the per-shot compiler that repeated local evidence before the
    # no-face/global-detail branch turns the entire program into pad_blur.
    if mode == "auto" and \
            len((ctx.index or {}).get("shots") or []) >= 2 and \
            len(spatial_pts) >= 2:
        local_pt = subject.median_point(spatial_pts)
        track_res = _reframe_with_track(
            ctx, ratio, local_pt, preserve_unmeasured=True)
        if track_res is not None:
            return track_res

    def _kept(focus):
        """Share of the picture's detail the crop window would keep."""
        try:
            rw, rh = (int(x) for x in str(ratio).split(":"))
        except (TypeError, ValueError):
            return None
        try:
            return subject.crop_detail_kept(frames, rw, rh, focus=focus)
        except Exception:
            return None

    def _fit_instead(focus, why):
        """The honest answer when a crop would cut off content: fit the WHOLE
        picture into the new frame over a blurred backdrop. Nothing is lost,
        and the tool says exactly what it measured and how to override."""
        res = set_frame(ctx, ratio, "pad_blur", _measured=True)
        if res.startswith("EDL v"):
            res += ("\nFITTED, NOT CROPPED — measured, not assumed. " + why +
                    " So the whole frame is scaled into the new "
                    f"{ratio} output over a blurred copy of itself, and NO "
                    "part of the picture is cut off. Tell the user the video "
                    "was re-fitted to the new dimensions rather than "
                    "truncated, and that the soft bands are the same footage "
                    "blurred. If they want it filled edge-to-edge instead, "
                    "accepting that the sides are lost, that is "
                    f"set_frame('{ratio}', 'crop').")
        return res

    if str(method).startswith("faces"):
        pt = subject.median_point(pts)
        drift = subject.spread(pts)
        track_needed = (len((ctx.index or {}).get("shots") or []) >= 2 and
                        len(spatial_pts) >= 2 and
                        (drift > 0.15 or spatial_coverage < 0.95))
        if drift > 0.15 or track_needed:
            # The subject sits in DIFFERENT places across the samples — a
            # multi-camera conversation, a walking subject. One fixed point
            # would median between the positions (the Aug 8 wall crops), so
            # measure per shot and let the crop re-aim at cuts.
            track_res = _reframe_with_track(
                ctx, ratio, pt, preserve_unmeasured=(mode == "auto"))
            if track_res is not None:
                return track_res
        # Only judge one global crop after shot-specific composition has had
        # its chance. A wide two-shot followed by a face close-up correctly
        # has low GLOBAL detail retention: the first shot must fit while the
        # second can crop. Returning a global pad here before building the
        # mixed track made the close-up stay tiny inside the wide-shot bars.
        if mode == "auto":
            keep_score = _kept(pt)
            if keep_score is not None and keep_score < CROP_DETAIL_KEEP_MIN:
                return _fit_instead(
                    pt,
                    f"A face was found at ({pt[0]:.2f}, {pt[1]:.2f}), but "
                    f"a {ratio} crop centered there would retain only "
                    f"{keep_score * 100:.0f}% of the frame's detail. This "
                    "is likely a screen/game/wide composition where the "
                    "surrounding content matters as much as the face.")
        res = set_frame(ctx, ratio, "crop", focus_x=pt[0], focus_y=pt[1],
                        _measured=True)
        if res.startswith("EDL v"):
            measured_total = (len((sidecar or {}).get("samples") or [])
                              if method == "faces_spatial" else len(frames))
            res += (f"\nMeasured from the pixels: a face was detected in "
                    f"{len(pts)} of {measured_total} sampled frames, sitting at "
                    f"({pt[0]:.2f}, {pt[1]:.2f}) of the source frame — the "
                    "crop follows it instead of the frame center. No vision "
                    "model was needed.")
            if drift > 0.18:
                res += (f" The subject MOVES across the samples (spread "
                        f"{drift:.2f} of the frame), and the focus is one "
                        "FIXED point for the whole video — say so and offer "
                        "pad_blur if the framing looks tight anywhere.")
        return res

    # NO FACE. Before aiming a crop at a guess, ask whether a crop is the
    # right operation at all — this is the branch the gameplay recording took,
    # and the branch that used to truncate two thirds of it away.
    energy_pt = subject.median_point(pts) if pts else None
    if mode == "auto":
        keep = _kept(energy_pt or (0.5, 0.5))
        if keep is not None and keep < CROP_DETAIL_KEEP_MIN:
            return _fit_instead(
                energy_pt,
                f"No face is in this footage, and a {ratio} crop of it would "
                f"keep only {keep * 100:.0f}% of the picture's detail — the "
                "content runs to the edges of the frame (a game HUD, a screen "
                "recording, a wide scene), so cropping would cut off things "
                "the viewer needs rather than reframe them.")

    if not llm.vision_available():
        # No face and no vision: the gradient-energy centroid is still a
        # measurement of where the picture's detail is, which beats the middle
        # of the frame — but it is a weaker claim and is described as one.
        pt = energy_pt
        res = set_frame(ctx, ratio, "crop",
                        focus_x=pt[0] if pt else None,
                        focus_y=pt[1] if pt else None, _measured=True)
        if res.startswith("EDL v"):
            if pt:
                res += (f"\nNo face was found in the sampled frames and no "
                        f"vision model is configured, so the crop is aimed at "
                        f"where the picture's DETAIL sits ({pt[0]:.2f}, "
                        f"{pt[1]:.2f}) rather than at the frame center. Tell "
                        "the user it is an estimate and offer set_frame with "
                        "your own focus_x/focus_y, or pad_blur, if the "
                        "framing misses.")
            else:
                res += ("\nCould not measure a subject and no vision model is "
                        "configured, so this is the plain CENTER crop — say "
                        "so, and offer pad_blur (which keeps the whole "
                        "picture) for footage where the center is wrong.")
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
        res = set_frame(ctx, ratio,
                        "pad_blur" if mode == "auto" else "crop",
                        _measured=True)
        if res.startswith("EDL v"):
            res += ("\nNote: the vision model gave no usable subject "
                    "positions, so auto mode preserved the whole frame with "
                    "pad_blur instead of guessing a center crop.")
        return res
    # Median, not mean: one wide establishing shot must not drag the crop
    # off every talking-head frame.
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    fx, fy = xs[len(xs) // 2], ys[len(ys) // 2]
    # Vision naming a focal point does not make a crop the right operation:
    # asked where to look in a game frame it will happily point at the
    # character, and the HUD around it still gets cut off. The same
    # measurement gates this branch.
    if mode == "auto":
        keep = _kept((fx, fy))
        if keep is not None and keep < CROP_DETAIL_KEEP_MIN:
            return _fit_instead(
                (fx, fy),
                f"The vision model put the focal point at ({fx:.2f}, "
                f"{fy:.2f}), but a {ratio} crop aimed there would still keep "
                f"only {keep * 100:.0f}% of the picture's detail — this "
                "footage fills its frame edge to edge.")
    res = set_frame(ctx, ratio, "crop", focus_x=round(fx, 3),
                    focus_y=round(fy, 3), _measured=True)
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
# Round 72: the air a rect-framed zoom leaves around its region — the
# viewport shows the rect plus this fraction of the rect's own size on each
# side, so "zoom into the message" lands as a composed close-up with
# breathing room, not an edge-to-edge crop of the box.
ZOOM_RECT_MARGIN = 0.12


def _solve_zoom_rect(rect, strength=None):
    """(err, strength, cx, cy, (rx0, ry0, rx1, ry1)) — ONE viewport solver
    behind add_zoom's rect and add_zoom_path's rect keyframes (round 75).

    The pin space (cx/cy) parameterizes every in-frame viewport, but pins a
    POINT in place — so framing a region near an edge takes viewport math no
    model should be doing by hand: solve z to fit the rect with margin
    (unless a strength is given), centre the viewport as far as the frame
    allows, derive the pin. strength 0 (a travelling zoom's seamless entry/
    exit) still needs an AIM, so the pin is solved at the rect's fit-zoom
    while the returned strength stays whatever the caller decides."""
    if isinstance(rect, str):
        # Round 77: a list argument that crossed a STALE MCP session arrives
        # as its JSON text (the frozen schema types the new param as a
        # string and the transport stringifies the value). Scalars already
        # survive that trip — float("1.6") parses — so lists get the same
        # tolerance instead of a rejection nobody can act on.
        try:
            rect = json.loads(rect)
        except ValueError:
            pass
    try:
        rx0, ry0, rx1, ry1 = (float(v) for v in rect)
    except (TypeError, ValueError):
        return ("REJECTED: rect must be [x0, y0, x1, y1] — fractions of "
                "the output frame ((0,0) = top-left), read off the grid "
                "on a look_at frame.", None, None, None, None)
    rx0, ry0 = min(max(rx0, 0.0), 1.0), min(max(ry0, 0.0), 1.0)
    rx1, ry1 = min(max(rx1, 0.0), 1.0), min(max(ry1, 0.0), 1.0)
    if rx1 - rx0 < 0.02 or ry1 - ry0 < 0.02:
        return ("REJECTED: rect is empty or inverted — [x0, y0, x1, y1] "
                "needs x0 < x1 and y0 < y1 (at least 0.02 apart).",
                None, None, None, None)
    rw, rh = rx1 - rx0, ry1 - ry0
    fit = 1.0 / (max(rw, rh) * (1.0 + 2.0 * ZOOM_RECT_MARGIN))
    fit = min(max(fit, 1.0 + ZOOM_STRENGTH_MIN), 1.0 + ZOOM_STRENGTH_MAX)
    st = round(fit - 1.0, 2) if strength is None else strength
    z = 1.0 + st if st > 0 else fit
    half = 0.5 / z
    vx0 = min(max((rx0 + rx1) / 2.0 - half, 0.0), 1.0 - 2.0 * half)
    vy0 = min(max((ry0 + ry1) / 2.0 - half, 0.0), 1.0 - 2.0 * half)
    return (None, st, round(vx0 / (1.0 - 1.0 / z), 3),
            round(vy0 / (1.0 - 1.0 / z), 3),
            (rx0, ry0, rx1, ry1))


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


def add_zoom(ctx, start, end, strength=None, mode=None, cx=None, cy=None,
             path=None, rect=None):
    # Round 67 default: 15% (was 25%) — a gentle push the viewer feels
    # rather than sees. Big snaps are opt-in, not the default grammar.
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        s = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 2)
        e = round(min(max(float(end), s), prog), 2)
        st = None if strength is None else \
            round(min(max(float(strength), ZOOM_STRENGTH_MIN),
                      ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return ("REJECTED: start/end/strength must be numbers. start/end are "
                "OUTPUT-timeline seconds; strength 0.05-4.5 (0.25 = 25% "
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
    if (cx is None) != (cy is None):
        return ("REJECTED: a zoom target needs BOTH cx and cy. Inspect the "
                "exact frame with look_at and provide the measured point, or "
                "pass rect=[x0,y0,x1,y1] to frame a measured region.")
    # rect (round 72): FRAME A REGION. cx/cy pin a POINT — the renderer keeps
    # the aimed point at ITS OWN screen position while everything magnifies
    # around it, which is right for emphasis on a well-composed subject and
    # geometrically incapable of "zoom into the message": a subject near an
    # edge stays near that edge at any strength (a real launch-video edit
    # shipped the message clipped at the frame edge this way). Given the
    # region's box instead, the tool solves the viewport — strength to fit
    # it with margin (unless given), centred as far as the frame allows —
    # and derives the pin cx/cy that renders that exact window, so the
    # renderer, every stored EDL and every cached render stay untouched.
    rct = None
    overrode_cxcy = False
    defaulted_target = False
    if rect is not None:
        # BOTH given: take the rect and say so, do NOT reject (round 101).
        # This was the single most common event in the whole product — 357
        # rejections in one week, more than any other tool result of any
        # kind. Every one cost a full agent step: ~23s of a user's wait and
        # ~57k prompt tokens, to be told something the tool could resolve by
        # itself. And it IS resolvable: a rect is strictly more information
        # than a point — _solve_zoom_rect DERIVES the pin cx/cy from it — so
        # "both" has an obviously right answer rather than an ambiguous one.
        # The note in the result teaches the dialect for the next call
        # without spending a round trip on the lesson.
        if tgt:
            overrode_cxcy = True
            tgt = {}
        if zmode == "follow":
            return ("REJECTED: a follow zoom is aimed by its `path`; rect "
                    "only applies to fixed zooms (punch/ease/push_in/"
                    "pull_out).")
        err, st, scx, scy, rr = _solve_zoom_rect(rect, st)
        if err:
            return err
        rw, rh = rr[2] - rr[0], rr[3] - rr[1]
        tgt = {"cx": scx, "cy": scy}
        rct = [round(v, 3) for v in rr]
    if st is None:
        st = 0.15
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
    elif not tgt:
        tgt = {"cx": 0.5, "cy": 0.5}
        defaulted_target = True
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    item = {"id": _next_item_id(zooms, "zm"), "start": s, "end": e,
            "strength": st}
    if zmode != "punch":
        item["mode"] = zmode
    item.update(tgt)
    item["target_measured"] = not defaulted_target
    if rct:
        item["rect"] = rct
    if pts:
        item["path"] = pts
    zooms.append(item)
    fx["zooms"] = zooms
    edl["effects"] = fx
    if pts:
        aimed = (f", travelling ({pts[0]['cx']:g},{pts[0]['cy']:g}) → "
                 f"({pts[-1]['cx']:g},{pts[-1]['cy']:g}) across "
                 f"{len(pts)} points")
    elif rct:
        # The achieved shot, computed from the STORED (rounded) values —
        # what the viewer will actually see, not what was asked for.
        z = 1.0 + st

        def _scr(u, c):
            return min(max((u - (1.0 - 1.0 / z) * c) * z, 0.0), 1.0)

        aimed = (f", framing x {rct[0]:g}-{rct[2]:g} / y {rct[1]:g}-{rct[3]:g}"
                 f" at {z:.2f}x — on screen the region lands at x "
                 f"{_scr(rct[0], tgt['cx']):.2f}-{_scr(rct[2], tgt['cx']):.2f}"
                 f", y {_scr(rct[1], tgt['cy']):.2f}-"
                 f"{_scr(rct[3], tgt['cy']):.2f}")
        if strength is not None and max(rw, rh) > 1.0 / z + 1e-6:
            aimed += (f" (at this strength the region does NOT fully fit; "
                      f"strength {max(round(1.0 / max(rw, rh) - 1.0, 2), 0.05):g}"
                      " would just contain it)")
    else:
        aimed = (f", aimed at ({tgt.get('cx', 0.5):g}, {tgt.get('cy', 0.5):g})"
                 " of the frame — that point HOLDS its screen position while "
                 "everything magnifies around it (a point near an edge stays "
                 "near that edge; to cut to a framed close-up of a region, "
                 "pass rect=[x0,y0,x1,y1] instead)" if tgt else "")
    if overrode_cxcy:
        # Said once, in the result of a call that WORKED, instead of as a
        # rejection that cost the turn a step.
        aimed += (". NOTE: you passed cx/cy as well as rect — the rect wins "
                  "(it already fixes where the frame lands, and the pin is "
                  "derived from it). Pass rect alone next time")
    result = ctx.write_edl(
        edl, f"{ZOOM_MODE_DESC[zmode]} zoom {int(st * 100)}% on {s}-{e}s "
             f"(output time){aimed} [{item['id']}]")
    if defaulted_target and result.startswith("EDL v"):
        result += ("\nQUALITY ADVISORY: no target was supplied, so this zoom "
                   "uses the frame center. Inspect the preview and retarget "
                   "it if the intended subject is elsewhere.")
    return result


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


# ── The keyframed travelling zoom (round 51) ────────────────────────────────
# add_zoom aims at ONE point for a window. That is the right shape for "punch
# in on that line" and the wrong one for every screen recording, where the
# thing worth looking at moves: the cursor crosses to a button, the button
# opens a panel, the panel fills in. Cutting between static punches to follow
# that reads as three separate shots of one continuous action.
#
# showcase_demo has had this motion since round 45, but only for captures
# record_website_demo made — it is driven off that recorder's event track. The
# motion itself has nothing to do with where the footage came from, so it now
# lives in worker/travel.py and BOTH tools call it.

def add_zoom_path(ctx, keyframes, ease=None):
    """A zoom whose centre AND strength travel through a list of keyframes,
    interpolated, in output seconds."""
    if not isinstance(keyframes, list) or len(keyframes) < 2:
        return ("REJECTED: keyframes must be a list of at least two points, "
                "each {t, cx, cy, strength}. t is OUTPUT-timeline seconds; "
                "cx/cy are 0-1 fractions of the output frame ((0,0) = "
                "top-left, the same convention as add_zoom); strength is "
                "0-4.5. The window runs from the first t to the last.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    clean = []
    for i, kf in enumerate(keyframes):
        if not isinstance(kf, dict):
            return (f"REJECTED: keyframes[{i}] must be an object "
                    "{t, cx, cy, strength} or {t, rect, strength}.")
        try:
            t = float(kf["t"])
        except (KeyError, TypeError, ValueError):
            return f"REJECTED: keyframes[{i}] needs a numeric t (seconds)."
        s = kf.get("strength")
        if s is not None:
            try:
                s = float(s)
            except (TypeError, ValueError):
                return (f"REJECTED: keyframes[{i}].strength must be a number "
                        f"({ZOOM_STRENGTH_MIN}-{ZOOM_STRENGTH_MAX}), or omit "
                        "it.")
            # 0 is legal here and is NOT legal on add_zoom: it means "no push
            # at this instant", which is how a travelling zoom starts from
            # and returns to the untouched frame without a visible step.
            s = 0.0 if s <= 0.0 else min(max(s, ZOOM_STRENGTH_MIN),
                                         ZOOM_STRENGTH_MAX)
        rct = kf.get("rect")
        if rct is not None:
            # Round 75: a keyframe can name the THING to frame instead of a
            # pin. cx/cy pin a point in place, so a travelling zoom between
            # two edge subjects (two chat bubbles down the left panel) took
            # hand-derived viewport arithmetic per keyframe — exactly what
            # the agent got wrong when a launch video asked the zoom to move
            # from one message to another. Each rect goes through the same
            # solver add_zoom uses; a strength-0 entry/exit keyframe still
            # aims at its rect (the pin is solved at the rect's fit zoom).
            if kf.get("cx") is not None or kf.get("cy") is not None:
                return (f"REJECTED: keyframes[{i}] has both rect and cx/cy "
                        "— two answers to where the frame should look. Pass "
                        "ONE per keyframe.")
            err, s_fit, cx, cy, _r = _solve_zoom_rect(
                rct, s if s else None)
            if err:
                return err.replace("REJECTED:",
                                   f"REJECTED: keyframes[{i}]:", 1)
            if s is None:
                s = s_fit
        else:
            try:
                cx = float(kf["cx"])
                cy = float(kf["cy"])
            except (KeyError, TypeError, ValueError):
                return (f"REJECTED: keyframes[{i}] needs numeric cx and cy "
                        "(or a rect=[x0,y0,x1,y1]). cx/cy are fractions of "
                        "the output frame (0-1) — use look_at to find what "
                        "you are aiming at.")
            if s is None:
                s = 0.25
        clean.append({"t": t, "cx": cx, "cy": cy, "strength": s})
    clean.sort(key=lambda p: p["t"])
    # Round 77 drift check. Interpolation means the camera is IN MOTION for
    # the ENTIRE gap between two keyframes that disagree — there is no
    # implicit hold. A path that went straight from a 4.4x close-up to the
    # next beat 3s later read as a "weird premature pull" the moment it left
    # the first target: the ease starts pulling immediately, just slowly.
    # Warn (don't block): a long travel across a wide gap is sometimes the
    # intent (a slow scenic pan), but usually a missing hold keyframe.
    drifts = []
    for p, q in zip(clean, clean[1:]):
        gap = q["t"] - p["t"]
        moves = (abs(q["cx"] - p["cx"]) > 0.03
                 or abs(q["cy"] - p["cy"]) > 0.03)
        zooms_off = (q["strength"] or 0) - (p["strength"] or 0)
        if gap > 1.5 and (moves or abs(zooms_off) > 0.3):
            what = []
            if moves:
                what.append("the aim moves")
            if abs(zooms_off) > 0.3:
                what.append(f"zoom {p['strength']:g}->{q['strength']:g}")
            drifts.append(f"{p['t']:g}s->{q['t']:g}s ({gap:.1f}s: "
                          + ", ".join(what) + ")")
    drift_note = ""
    if drifts:
        drift_note = (
            "\nDRIFT CHECK: the camera is in continuous motion across "
            + "; ".join(drifts[:3])
            + ". If the frame should STAY on the earlier target until just "
              "before the later beat, REPEAT its keyframe right before that "
              "beat — a multi-second glide away from a close-up reads as a "
              "premature pull, not a hold.")
    start = round(min(max(clean[0]["t"], 0.0), max(0.0, prog - 0.2)), 2)
    end = round(min(max(clean[-1]["t"], start), prog), 2)
    if end - start < 0.2:
        return (f"REJECTED: the keyframes span {end - start:.2f}s — a zoom "
                "needs at least 0.2s. Spread the first and last t further "
                "apart.")
    ez = (ease or travel.DEFAULT_EASE).strip().lower()
    if ez not in travel.EASES:
        return (f"REJECTED: ease must be one of {', '.join(travel.EASES)}. "
                "'cubic_in_out' (default) settles at each keyframe — the "
                "frame arrives somewhere, rests, and moves on. 'linear' holds "
                "one speed straight through them, for a steady scan.")
    pts, err = travel.waypoints_to_path(clean, start, end, with_strength=True)
    if err:
        return "REJECTED: " + err

    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    item = {"id": _next_item_id(zooms, "zp"), "start": start, "end": end,
            # `strength` stays the fallback the schema requires; the per-point
            # `s` values are what actually render.
            "strength": round(max(p["s"] for p in pts), 2) or ZOOM_STRENGTH_MIN,
            "mode": "path", "ease": ez, "path": pts}
    zooms.append(item)
    fx["zooms"] = zooms
    edl["effects"] = fx
    written = ctx.write_edl(
        edl, f"keyframed zoom on {start}-{end}s (output time), "
             f"{travel.describe(item)}, {ez} [{item['id']}]")
    if not written.startswith("EDL v"):
        return written
    note = ""
    if pts[0].get("s", 0) > 0.02 or pts[-1].get("s", 0) > 0.02:
        note = ("\nNOTE: this path starts at "
                f"{int(pts[0]['s'] * 100)}% and ends at "
                f"{int(pts[-1]['s'] * 100)}% zoom, so the frame STEPS in at "
                f"{start}s and out at {end}s. That is exactly what the "
                "keyframes say — no ramp is added, because the whole point of "
                "this tool is that the frame is where you put it. For a "
                "seamless entry and exit, give the first and last keyframe "
                "strength 0.")
    return (written + note + drift_note
            + "\nThe frame travels between the keyframes; remove the whole "
              f"move with remove_zoom_path('{item['id']}').")


def remove_zoom_path(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    hit = next((z for z in zooms if z.get("id") == id), None)
    if not hit:
        paths = [z.get("id", "?") for z in zooms if z.get("mode") == "path"]
        have = ", ".join(paths) or "none"
        return (f"REJECTED: no keyframed zoom with id '{id}'. Existing "
                f"keyframed zooms: {have}. Call get_edl to see them.")
    if hit.get("mode") != "path":
        return (f"REJECTED: '{id}' is a {hit.get('mode') or 'punch'} zoom, "
                "not a keyframed path. Remove it with remove_zoom.")
    fx["zooms"] = [z for z in zooms if z.get("id") != id]
    edl["effects"] = fx
    return ctx.write_edl(
        edl, f"removed keyframed zoom {id} ({hit['start']}-{hit['end']}s)")


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

# One definition, in taste.py, shared by the tool result and the render
# audit — so what set_transitions calls "too often" and what the audit calls
# "too often" cannot drift apart.
TRANSITION_MIN_SPACING_S = taste.TRANSITION_MIN_SPACING_S

# Where a card's SUBTITLE is anchored: the same point as its title, so the two
# are one group rather than two independent guesses.
#
# This was 0.635 — a fixed fraction chosen without knowing how tall the title
# above it would wrap. On a 9:16 frame "MOSKOV CRITICAL BUILD" wraps to three
# 176px lines spanning 675-1245px, and the subtitle sat at 1167-1271px, so the
# video OPENED on two lines of text burned through each other. The height of a
# wrapped title is not knowable here (only graphics.py measures it), and that
# is exactly why nothing here should be pretending to place text relative to
# it: declaring both on the card's centre hands the layout to
# graphics._stack_concurrent, which measures both blocks and stacks them —
# centred on the card, whatever the title turns out to be.
CARD_SUBTITLE_Y = 0.5


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
                "scope='every_cut' whenever the editor intends one on every "
                "cut.")
    where = ("every cut" if sc == "every_cut" else "scene changes")
    res = ctx.write_edl(
        edl, f"transitions: {d}s {p} ({TRANSITION_DESC[p]}) at {where} — "
             f"{hit} of {n_cuts} junction{'s' if n_cuts != 1 else ''}{note}")

    # CADENCE, not just count (round 55). 'scene' bounds transitions to real
    # shot changes, which is the right rule and is not a rule about DENSITY:
    # a montage assembled from nine far-apart source spans has nine genuine
    # scene changes, so all nine qualified and a 30s edit fired a whip pan
    # every 3.3 seconds. The user's words were "look at how fast the scene
    # transitions are — it's literally putting one every second". The count
    # alone ("9 of 9") reads like success; the interval is the number that
    # shows it is not.
    if res.startswith("EDL v") and hit >= 3:
        prog = program_duration(edl)
        if prog > 0:
            every = prog / hit
            res += (f"\nCadence: {hit} transitions across {prog:.0f}s of "
                    f"programme is one every {every:.1f}s.")
            if every < TRANSITION_MIN_SPACING_S:
                res += (" THAT IS TOO OFTEN — a full-screen effect at that "
                        "rate is the effect the viewer watches instead of the "
                        "video, and it is the single most common 'this looks "
                        "broken' report. A transition should mark a change "
                        "the viewer needs to feel, not punctuate every clip. "
                        "Unless the user asked for exactly this, either drop "
                        "them (set_transitions('none') — hard cuts are the "
                        "montage default and read as faster, not slower) or "
                        "keep the edit and let the cuts do the work.")
    return res


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
    row = _original_row(ctx)
    local = os.path.join(ctx.workdir,
                         "orig" + os.path.splitext(row["storage_key"])[1])
    storage.download_to(row["storage_key"], local)
    ctx._orig_local = local
    ctx._orig_sha = row.get("sha256") or ""
    return local


def _original_row(ctx):
    """The original's asset row — storage key + sha, WITHOUT downloading the
    bytes. Round 67: the remote clean path stages the original on the
    executor, so the dispatcher must be able to fingerprint the derivation
    from the row alone."""
    row = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
    if not row:
        raise RuntimeError("this project has no original video")
    # A proxy-first upload registers the original BEFORE its bytes arrive, so
    # the project is editable while a multi-GB file streams up behind it.
    # Repainting pixels is one of the few things that genuinely needs the
    # full-resolution file, so say which one it is and what happens next —
    # "download failed" would be true and useless.
    if (row.get("meta") or {}).get("upload_state") == "pending":
        pct = int(round(float((row.get("meta") or {})
                              .get("upload_progress") or 0) * 100))
        raise RuntimeError(
            "erasing pixels needs the full-resolution original, and it is "
            f"still uploading in the background ({pct}% done). Everything "
            "else — cuts, captions, music, zooms — works right now; ask me "
            "again for this one once the upload finishes.")
    return row


def _clean_fp(sha, regions, cursor=None):
    # One implementation, shared with the renderer: it proves at render time
    # that the cleaned file is a repaint of THIS project's current video.
    return clean_fingerprint(sha, regions, cursor)


def _run_clean(ctx, regions, cursor=None):
    """Produce (asset_key, proxy_key, fp) for this exact derivation.

    Cached on the fingerprint: re-erasing the same rectangle, or undoing one of
    three and putting it back, costs nothing the second time.

    Round 51: `cursor` is a second derivation pass over the SAME file. The two
    chain in a fixed order — repaint first, then the pointer — because the
    detector must not be shown a frame with a repainted patch where the
    pointer was, and because a caption erased from under a redrawn cursor
    would have the cursor repainted away with it.
    """
    row = _original_row(ctx)
    ctx._orig_sha = row.get("sha256") or getattr(ctx, "_orig_sha", "")
    fp = _clean_fp(getattr(ctx, "_orig_sha", ""), regions, cursor)
    key = f"cleaned/{ctx.project_id}/{fp[:16]}.mp4"
    pkey = f"cleaned/{ctx.project_id}/{fp[:16]}_proxy.mp4"
    if storage.exists(key) and storage.exists(pkey):
        return key, pkey, fp
    # The length/pixel budget is the same for both passes — they are the same
    # frame-by-frame work — but the ALTERNATIVES are not, and handing a user
    # who asked to enlarge their cursor an offer to blur a rectangle is the
    # kind of non-sequitur that reads as the tool not having understood.
    alt = ("Offer the alternatives honestly: cover the area with blur_region, "
           "or crop it out of frame with auto_reframe/set_frame."
           if regions else
           "Offer what does work at this length: a zoom that follows the "
           "action (add_zoom_path), or the floating frame "
           "(set_screen_frame).")
    what = "repainting" if regions else "redrawing the cursor"
    # ROUND 67 — THE REPAINT RUNS ON THE EXECUTOR. It decodes and re-encodes
    # every frame of the user's ORIGINAL inside an agent turn: the heaviest
    # member of the job class that has OOM-killed the dispatcher repeatedly
    # (and the prime suspect in job 1557's death, minutes after a customer's
    # erase ran there). With an executor configured the dispatcher never
    # touches the original — bounds are checked from the INDEX, the executor
    # stages/repaints/uploads/measures, and a remote failure is an honest
    # refusal, never a local retry on the box we know is too small.
    if remote.clean_available():
        v = (ctx.index or {}).get("video") or {}
        r_dur = float(v.get("duration") or 0.0)
        r_w, r_h = int(v.get("width") or 0), int(v.get("height") or 0)
        if r_dur > config.CLEAN_MAX_SOURCE_S:
            raise ValueError(
                f"this video is {r_dur / 60:.1f} min long and {what} works "
                f"frame by frame — above {config.CLEAN_MAX_SOURCE_S / 60:.0f} "
                f"min it does not finish inside one edit turn. {alt}")
        if r_w and r_h and (r_w * r_h / 1e6) * r_dur > \
                config.CLEAN_MAX_MPX_SECONDS:
            raise ValueError(
                f"this video is {r_w}x{r_h} for {r_dur / 60:.1f} min, which "
                "is more pixels than a frame-by-frame pass can finish inside "
                f"one edit turn. {alt}")
        try:
            stats = remote.run_clean_remote(
                ctx.project_id,
                {"src_key": row["storage_key"], "out_key": key,
                 "proxy_key": pkey, "regions": regions, "cursor": cursor,
                 "measure": regions},
                user_id=ctx.job.get("user_id"))
        except Exception as ex:
            raise ValueError(
                f"the repaint could not run on the render service "
                f"({str(ex)[:200]}). Nothing was changed — try again in a "
                f"moment. {alt}")
        ctx._clean_stats = stats
        if cursor:
            ctx._cursor_stats = stats
        ctx._clean_measure = (stats.get("before"), stats.get("after"))
        try:
            ctx.db.run(dbx.insert_asset, ctx.project_id, "clean_source", key,
                       bytes_=stats.get("out_bytes"),
                       duration_s=stats.get("duration_s"),
                       width=stats.get("width"), height=stats.get("height"),
                       fps=stats.get("fps"),
                       meta={"filename": "cleaned-source.mp4", "clean_fp": fp,
                             "generated": True,
                             "model": ("remote:cursor" if cursor
                                       else "remote:inpaint"),
                             "regions": len(regions)})
            ctx.db.run(dbx.insert_asset, ctx.project_id, "clean_proxy", pkey,
                       bytes_=stats.get("proxy_bytes"),
                       duration_s=stats.get("duration_s"),
                       meta={"filename": "cleaned-proxy.mp4", "clean_fp": fp,
                             "generated": True, "model": "remote:inpaint"})
        except Exception as e:
            print(f"[erase] cleaned-source asset rows not recorded for "
                  f"project {ctx.project_id} ({str(e)[:160]}) — the repaint "
                  "itself is in storage and the EDL points at it; run "
                  "migration 007", flush=True)
        return key, pkey, fp
    src = _original_local(ctx)
    info = media.probe(src)
    if float(info["duration"]) > config.CLEAN_MAX_SOURCE_S:
        raise ValueError(
            f"this video is {info['duration'] / 60:.1f} min long and "
            f"{what} works frame by frame — above "
            f"{config.CLEAN_MAX_SOURCE_S / 60:.0f} min it does not finish "
            f"inside one edit turn. {alt}")
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
            "than a frame-by-frame pass can finish inside one edit turn "
            f"(about {config.CLEAN_MAX_MPX_SECONDS / (int(info['width']) * int(info['height']) / 1e6) / 60:.0f} "
            f"min at this resolution). {alt} (Passing start/end does NOT "
            "help — the whole file is still re-encoded; it only narrows which "
            "frames are touched.)")
    out = os.path.join(ctx.workdir, f"clean_{fp[:8]}.mp4")
    prox = os.path.join(ctx.workdir, f"clean_{fp[:8]}_proxy.mp4")
    if not regions and not cursor:
        # Callers all guard this, but an unbound `stats` below would be a
        # NameError inside a turn that was only ever a no-op — fail with the
        # sentence that says what actually went wrong.
        raise ValueError("nothing to derive: no erase regions and no cursor "
                         "pass. Clear source_clean instead.")
    mid = None
    if regions and cursor:
        # Two passes, so the intermediate is a full-res file on disk and never
        # two live x264 encoders (the OOM that took the whole worker down on
        # 2026-07-25). The proxy is derived once, at the end of the chain.
        mid = os.path.join(ctx.workdir, f"clean_{fp[:8]}_mid.mp4")
        stats = inpaint.clean_video(src, regions, mid)
    elif regions:
        stats = inpaint.clean_video(src, regions, out, prox)
    if cursor:
        stats = cursorlib.enhance(
            mid or src, out, prox,
            scale=float(cursor.get("scale", 2.0)),
            smoothing=float(cursor.get("smoothing", 0.5)),
            click_highlight=bool(cursor.get("click_highlight", True)),
            click_times=cursor.get("click_times") or [])
        ctx._cursor_stats = stats
        if mid:
            try:
                os.remove(mid)
            except OSError:
                pass
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
                         "generated": True,
                         "model": ("local:cursor" if cursor
                                   else "local:inpaint"),
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


_KEEP_CURSOR = object()


def _edl_cursor(edl):
    return ((edl.get("source_clean") or {}).get("cursor")) or None


def _apply_clean(ctx, regions, what, cursor=_KEEP_CURSOR, base_edl=None):
    """Re-derive the source for `regions` (+ the cursor pass) and write the EDL.

    `regions` is the COMPLETE list for this project (not a delta), because the
    derived file is one artifact: every erase re-derives it from the untouched
    original. `cursor` defaults to whatever the EDL already carries, so an
    erase never silently drops a cursor pass the user asked for two turns ago
    — and vice versa. `base_edl` lets a caller thread its own other changes
    (remove_erase clears the round-92 patch list in the same write).
    """
    edl = dict(base_edl if base_edl is not None else ctx.latest_edl()["json"])
    if cursor is _KEEP_CURSOR:
        cursor = _edl_cursor(edl)
    if not regions and not cursor:
        edl["source_clean"] = None
        return ctx.write_edl(edl, f"restored the original pixels ({what})")
    # Round 67: on the remote path the executor measures before/after itself
    # (see inpaint.run_clean_job) — the dispatcher must not decode the
    # original at all, that being the whole point of the move.
    ctx._clean_measure = None
    remote_clean = remote.clean_available()
    before = None
    if not remote_clean:
        src = _original_local(ctx)
        before = [inpaint.text_energy(src, (r["x"], r["y"], r["w"], r["h"]),
                                      samples=5) for r in regions]
    key, pkey, fp = _run_clean(ctx, regions, cursor)
    edl["source_clean"] = {"asset_key": key, "proxy_key": pkey, "fp": fp,
                           "regions": regions}
    if cursor:
        edl["source_clean"]["cursor"] = cursor
    result = ctx.write_edl(edl, what)
    if not result.startswith("EDL v"):
        return result
    # Honesty check: measure the ink in each rectangle on the file that will
    # actually be rendered. A claim that the text is gone is only made when
    # the pixels say so — and when they do not, the agent is told which
    # rectangle survived and what to try, instead of reporting success.
    # Remote path: the executor already measured both sides on its own copy
    # (a cached fingerprint hit carries no fresh numbers — that derivation
    # measured clean when it was first created, which is why it is cached).
    if getattr(ctx, "_clean_measure", None) and ctx._clean_measure[1]:
        before, after = ctx._clean_measure
    elif remote_clean:
        after = None
    else:
        local = os.path.join(ctx.workdir, "clean_check.mp4")
        try:
            storage.download_to(key, local)
            after = [inpaint.text_energy(local,
                                         (r["x"], r["y"], r["w"], r["h"]),
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
    # The scan reads the RAW source, so a mark the EDL already repaints or
    # censors keeps listing here forever. Unsaid, that listing reads as
    # "your erase failed" and invites erasing the same band again — project
    # 382 (2026-08-07) looped erase/un-erase for 23 minutes on exactly that
    # misreading while the real problem was repaint QUALITY.
    edl = ctx.latest_edl()["json"]
    erases = [dict(q) for q in
              ((edl.get("source_clean") or {}).get("regions") or [])]
    for p in (edl.get("patches") or []):
        erases += [dict(q) for q in (p.get("regions") or [])]
    censors = [dict(q) for q in
               ((edl.get("effects") or {}).get("regions") or [])]
    covered = 0
    lines = []
    for i, r in enumerate(regions, start=1):
        cover = next(
            (f"repainted by [{q.get('id')}]" for q in erases
             if _rect_cover(q, r) >= 0.5
             and _windows_overlap(q.get("start"), q.get("end"),
                                  r.get("first_s"), r.get("last_s"),
                                  ctx.duration)),
            None) or next(
            (f"censored by [{q.get('id')}]" for q in censors
             if _rect_cover(q, r) >= 0.5), None)
        if cover:
            covered += 1
        lines.append(
            f"{i}. {r['kind']}: x={r['x']} y={r['y']} w={r['w']} h={r['h']} "
            f"— visible {r['first_s']}-{r['last_s']}s, in "
            f"{int(r['coverage'] * 100)}% of sampled frames"
            + (", content changes between frames"
               if r["changes"] > 6 else ", identical in every frame")
            + (f" — ALREADY {cover}" if cover else ""))
    out = ("Measured from the frames (not estimated — these rectangles are "
           "exact):\n" + "\n".join(lines))
    if covered < len(regions):
        out += ("\nPass one of these rectangles to erase_region to repaint "
                "it out, or call erase_burned_text to erase every caption "
                "band in one pass.")
    if covered:
        out += ("\nNOTE: this scan reads the RAW source pixels, so an "
                "erased mark keeps listing here — its listing does NOT mean "
                "the erase failed. If the user still sees a covered mark in "
                "the preview, the repaint QUALITY is failing on it, and "
                "re-erasing the same band cannot fix that: switch class — "
                "fill='box' replaces the repaint, blur_region covers it, "
                "set_frame crops it away — and look_at(output_times=[...]) "
                "inside the window to judge with your own eyes.")
    return out


_PATCH_PAD_S = 0.75            # window padding: plate sampling needs frames


def _patch_groups(items, duration):
    """Group erase items into patch windows: items whose (padded) windows
    overlap share one patch clip — one decode, one repaint pass, one overlay.
    Returns [(window, [items])] in time order."""
    def win(it):
        s = it.get("start")
        e = it.get("end")
        s = 0.0 if s is None else max(0.0, float(s) - _PATCH_PAD_S)
        e = duration if e is None else min(duration,
                                           float(e) + _PATCH_PAD_S)
        return (round(s, 2), round(max(e, s + 0.2), 2))

    order = sorted(items, key=lambda it: win(it)[0])
    groups = []
    for it in order:
        s, e = win(it)
        if groups and s <= groups[-1][0][1] + 0.01:
            (gs, ge), members = groups[-1]
            groups[-1] = ((gs, max(ge, e)), members + [it])
        else:
            groups.append(((s, e), [it]))
    return groups


def _run_patch(ctx, window, group_regions):
    """Build (or find) ONE proxy-res patch clip for `window`. Returns
    (asset_key, fp, stats) — stats carries before/after ink when the clip
    was freshly built, {} when it already existed (it measured clean when it
    was first made; that is why it is cached).

    Round 92: this replaces the whole-file clean pass for erases. The clip
    repaints only [window] of the PREVIEW SOURCE — the cleaned proxy when a
    cursor pass or legacy erase exists, else the plain proxy — so what the
    patch covers is exactly what the preview shows under it. Cost scales
    with the window: job 2685's five erases re-derived a 39s file seven
    times (965s, then the turn timed out); the same erases as patches are a
    few seconds each.
    """
    row = _original_row(ctx)
    sha = row.get("sha256") or ""
    ctx._orig_sha = sha
    edl = ctx.latest_edl()["json"]
    fp = patch_fingerprint(sha, group_regions, window)
    key = f"patches/{ctx.project_id}/{fp[:16]}.mp4"
    if storage.exists(key):
        return key, fp, {}
    # The export rebuilds this patch at FULL resolution from the original —
    # refuse at erase time anything that could not finish there, so an
    # accepted erase is always an exportable one.
    v = (getattr(ctx, "index", None) or {}).get("video") or {}
    w_len = float(window[1]) - float(window[0])
    mpx_s = (float(v.get("width") or 1920) * float(v.get("height") or 1080)
             / 1e6) * w_len
    if w_len > config.CLEAN_MAX_SOURCE_S or \
            mpx_s > config.CLEAN_MAX_MPX_SECONDS:
        raise ValueError(
            f"that erase spans {w_len / 60:.1f} min of footage — repainting "
            "works frame by frame and a span this long cannot finish. Erase "
            "with start/end around the moments the mark is actually visible, "
            "or cover it with blur_region / crop it out with set_frame.")
    src_key = renderer.clean_source_key(edl, "preview", sha)
    if not src_key:
        proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
        src_key = proxy["storage_key"] if proxy else row["storage_key"]
    if remote.clean_available():
        stats = remote.run_clean_remote(
            ctx.project_id,
            {"mode": "patch", "src_key": src_key, "out_key": key,
             "regions": group_regions, "window": list(window),
             "measure": True},
            user_id=ctx.job.get("user_id"))
    else:
        local_src = ctx.proxy_path()
        out = os.path.join(ctx.workdir, f"patch_{fp[:8]}.mp4")
        mids = [max(window[0], min(window[1] - 0.05,
                                   float(r.get("start")
                                         if r.get("start") is not None
                                         else window[0]))) + 0.4
                for r in group_regions]
        before = [inpaint.text_energy(local_src,
                                      (r["x"], r["y"], r["w"], r["h"]),
                                      at=t, samples=3)
                  for r, t in zip(group_regions, mids)]
        stats = inpaint.build_patch(local_src, group_regions, window, out)
        stats["before"] = before
        stats["after"] = [
            inpaint.text_energy(out, (r["x"], r["y"], r["w"], r["h"]),
                                at=max(0.05, t - stats["src_start"]),
                                samples=3)
            for r, t in zip(group_regions, mids)]
        storage.upload_file(out, key, "video/mp4")
    try:
        ctx.db.run(dbx.insert_asset, ctx.project_id, "patch", key,
                   duration_s=round(float(window[1]) - float(window[0]), 2),
                   meta={"filename": "repaint-patch.mp4", "patch_fp": fp,
                         "generated": True,
                         "regions": len(group_regions)})
    except Exception as e:
        print(f"[erase] patch asset row not recorded ({str(e)[:120]}) — "
              "the clip itself is in storage and the EDL points at it",
              flush=True)
    return key, fp, stats or {}


def _rect_cover(a, b):
    """Fraction of rectangle b's area that rectangle a covers (both are
    frame-fraction dicts with x, y, w, h)."""
    ix = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    iy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ix <= 0 or iy <= 0:
        return 0.0
    area = float(b["w"]) * float(b["h"])
    return (ix * iy) / area if area > 0 else 0.0


def _windows_overlap(a_start, a_end, b_start, b_end, duration):
    """Do two source-clock windows overlap? None means the whole video."""
    a0 = 0.0 if a_start is None else float(a_start)
    a1 = duration if a_end is None else float(a_end)
    b0 = 0.0 if b_start is None else float(b_start)
    b1 = duration if b_end is None else float(b_end)
    return a0 <= b1 and b0 <= a1


def _superseded_patches(edl, new_items, duration):
    """Patches the new erase re-covers — the agent repainting a band AGAIN.

    Stacked repaints are how project 382 (2026-08-07) got its "corrupt
    screen": a text-fill and a 70%x30% box-fill both live over one caption
    band, and the wider slab's edges stay visible around the narrower one.
    When the agent re-erases a band its intent is REPLACE, so treat it as
    replace: a patch is superseded when every region in it is >=50%
    mutually covered by some new item over an overlapping window. Dropping
    a patch is instant (its overlay simply stops rendering) and the new
    patch repaints from the untouched source, so nothing is lost."""
    out = []
    for p in (edl.get("patches") or []):
        regs = p.get("regions") or []
        if regs and all(
                any((_rect_cover(n, o) >= 0.5 or _rect_cover(o, n) >= 0.5)
                    and _windows_overlap(n.get("start"), n.get("end"),
                                         o.get("start"), o.get("end"),
                                         duration)
                    for n in new_items)
                for o in regs):
            out.append(p)
    return out


def _apply_patches(ctx, new_items, what, drop=None):
    """Build patch clips for `new_items` and append them to the EDL.

    Existing patches are never rebuilt or re-derived — each erase call pays
    for its own windows only. `drop` lists superseded patches (the same band
    re-erased) removed in the SAME write — replace, never stack. Returns the
    write result plus the same measured-ink honesty lines _apply_clean
    produces."""
    edl = dict(ctx.latest_edl()["json"])
    all_patches = [dict(p) for p in (edl.get("patches") or [])]
    dropped = {p["id"] for p in (drop or [])}
    existing = [p for p in all_patches if p["id"] not in dropped]
    groups = _patch_groups(new_items, ctx.duration)
    entries, lines = [], []
    for window, members in groups:
        key, fp, stats = _run_patch(ctx, window, members)
        # ids count the DROPPED patches too — "replaced pa1" must never
        # name the same id as the patch that replaced it.
        pid = _next_item_id(all_patches + entries, "pa")
        entries.append({"id": pid, "asset_key": key, "fp": fp,
                        "src_start": window[0], "src_end": window[1],
                        "regions": members})
        before = stats.get("before") or []
        after = stats.get("after") or []
        for r, b, a in zip(members, before, after):
            gone = (b <= 0.5) or (a <= max(1.5, b * 0.35))
            lines.append(f"[{r['id']}] ink {b:g} -> {a:g} "
                         + ("— gone" if gone else "— STILL VISIBLE"))
        if any(p.get("escalated") for p in (stats.get("plates") or [])):
            lines.append(f"[{pid}] the text sat on a solid bar, so the "
                         "whole bar was repainted, not just the letters")
    edl["patches"] = existing + entries
    result = ctx.write_edl(edl, what)
    if not result.startswith("EDL v"):
        return result
    if dropped:
        result += ("\nReplaced overlapping repaint(s) "
                   + ", ".join(sorted(dropped))
                   + " — re-erasing a band supersedes the earlier attempt; "
                   "stacked repaints are what show slabs and seams.")
    if lines:
        result += "\nMeasured on the repainted window: " + "; ".join(lines)
        if any("STILL" in ln for ln in lines):
            result += ("\nOne rectangle still shows ink. Widen it (outlines "
                       "and shadows sit outside the letters), or pass "
                       "fill='box' to repaint the whole rectangle. Do NOT "
                       "tell the user it was removed until this measures "
                       "clean.")
        else:
            result += ("\nThe pixels are genuinely repainted — say REMOVED, "
                       "not covered. The repaint applies instantly to every "
                       "render; cuts, captions and timestamps are unchanged. "
                       "Ink is a stroke count, not a beauty check: over "
                       "MOVING marks (animated caption boxes, stickers) a "
                       "clean measurement can still ghost — look_at("
                       "output_times=[...]) inside the erased window on the "
                       "next preview before telling the user it is clean. If "
                       "it ghosts, do NOT re-erase the same band: fill='box' "
                       "replaces the repaint, blur_region covers it, "
                       "set_frame crops it away.")
    return result


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
    # Existing ids across BOTH mechanisms (legacy whole-file regions and
    # patch regions), so a new er-id never collides.
    prior = [dict(r) for r in
             ((edl.get("source_clean") or {}).get("regions") or [])]
    for p in (edl.get("patches") or []):
        prior += [dict(r) for r in (p.get("regions") or [])]
    added = []
    for r in found:
        # Round 92: window each region to when the detector actually SAW it
        # (first_s/last_s ride every detection), padded by the sampling gap —
        # a watermark visible for 8s repaints 8s of frames, not the video.
        # The same rectangle already erased (same spot, overlapping window)
        # is skipped instead of re-added: job 2685 wrote the identical
        # caption region twice because the batch never checked itself.
        w0, w1 = r.get("first_s"), r.get("last_s")
        if w0 is not None and w1 is not None:
            w0 = max(0.0, float(w0) - _PATCH_PAD_S * 2)
            w1 = min(ctx.duration, float(w1) + _PATCH_PAD_S * 2)
            if w1 - w0 >= ctx.duration - 2.0:
                w0 = w1 = None            # seen throughout: whole video
        dup = any(abs(q.get("x", 9) - r["x"]) < 0.02
                  and abs(q.get("y", 9) - r["y"]) < 0.02
                  and abs(q.get("w", 9) - r["w"]) < 0.04
                  and abs(q.get("h", 9) - r["h"]) < 0.04
                  for q in prior + added)
        if dup:
            continue
        item = {"id": _next_item_id(prior + added, "er"),
                "x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"],
                "start": None if w0 is None else round(w0, 2),
                "end": None if w1 is None else round(w1, 2),
                "fill": "text", "kind": r["kind"]}
        added.append(item)
    if not added:
        return ("NO CHANGE: every detected region is already erased — the "
                "repaint is in place. If the user still sees text THERE, "
                "the repaint QUALITY is failing on that band and repeating "
                "the same erase cannot improve it: switch class instead — "
                "erase_region the band with fill='box' (replaces the old "
                "repaint), or cover it with blur_region, or crop it away "
                "with set_frame — then look_at(output_times=[...]) inside "
                "the window to judge. If they see text somewhere ELSE, ask "
                "where and erase_region that rectangle.")
    what = ("erased " + ", ".join(f"{a['kind']} at y={a['y']:g} [{a['id']}]"
                                  for a in added)
            + " from the source pixels")
    try:
        return _apply_patches(
            ctx, added, what,
            drop=_superseded_patches(edl, added, ctx.duration))
    except ValueError as e:
        return f"REJECTED: {e}"
    except Exception as e:
        return (f"The repaint failed ({str(e)[:180]}). Nothing changed — do "
                "NOT claim the text was removed.")


def _erase_rect_item(existing, x, y, w, h, start, end, fill):
    """Validate one erase rectangle -> item dict, or an error string."""
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
    return {"id": _next_item_id(existing, "er"), "x": round(rx, 3),
            "y": round(ry, 3), "w": round(rw, 3), "h": round(rh, 3),
            "start": span.get("start"), "end": span.get("end"),
            "fill": f, "kind": None}


def _subsumed_by(old, new):
    """True when `new` repaints everything `old` did, so keeping `old` only
    costs time. The widen-and-retry loop is the normal way this happens: a
    rectangle comes back STILL VISIBLE, the agent widens it, and the narrow
    first attempt is now a strictly smaller box inside the bigger one.

    It is not cosmetic. Every region is re-applied on EVERY future repaint of
    this project — the pass always starts from the untouched original — so a
    dead rectangle is a tax on each one forever. Project 360 (Aug 5 2026)
    finished with er2 sitting entirely inside er3.

    Conservative on purpose: only a rectangle that fully contains the old one,
    over a window that fully contains its window, with a fill at least as
    aggressive ('box' repaints the whole rectangle, 'text' only the strokes).
    """
    if old.get("fill") == "box" and new.get("fill") != "box":
        return False
    if not (new["x"] <= old["x"] + 1e-6
            and new["y"] <= old["y"] + 1e-6
            and new["x"] + new["w"] >= old["x"] + old["w"] - 1e-6
            and new["y"] + new["h"] >= old["y"] + old["h"] - 1e-6):
        return False
    if new.get("start") is None:              # new covers the whole video
        return True
    if old.get("start") is None:              # old does, new does not
        return False
    return (new["start"] <= old["start"] + 1e-6
            and new["end"] >= old["end"] - 1e-6)


def erase_region(ctx, x=None, y=None, w=None, h=None, start=None, end=None,
                 fill="text", regions=None):
    """Repaint rectangle(s) out of the source pixels — several marks in ONE
    repaint pass when `regions` is used.

    The batch form exists because the pass is the cost: every call re-derives
    the whole cleaned source (that is the design — one artifact, always from
    the untouched original), so five separate calls repaint the video five
    times, each pass redoing all the earlier rectangles again. A real "remove
    all the TikTok UI" request (Aug 3 2026) did exactly that: five erases,
    fourteen minutes, and the turn hit its time budget with the user's OTHER
    request (a brightness lift) still undone. The same five rectangles in one
    call are one pass."""
    if not ctx.has_main_video:
        return "REJECTED: there is no main video in this project."
    if regions is not None and not isinstance(regions, (list, tuple)):
        return ("REJECTED: regions must be a list of "
                "{x, y, w, h, fill?, start?, end?} objects.")
    edl = ctx.latest_edl()["json"]
    existing = [dict(r) for r in
                ((edl.get("source_clean") or {}).get("regions") or [])]
    for p in (edl.get("patches") or []):
        existing += [dict(r) for r in (p.get("regions") or [])]
    if regions:
        if x is not None or y is not None or w is not None or h is not None:
            return ("REJECTED: pass EITHER one rectangle (x,y,w,h) OR "
                    "regions=[...], not both.")
        batch = []
        for i, r in enumerate(regions):
            if not isinstance(r, dict):
                return f"REJECTED: regions[{i}] must be an object."
            item = _erase_rect_item(
                existing + batch, r.get("x"), r.get("y"), r.get("w"),
                r.get("h"), r.get("start", start), r.get("end", end),
                r.get("fill", fill))
            if isinstance(item, str):
                return f"regions[{i}]: {item}"
            batch.append(item)
        if not batch:
            return "REJECTED: regions is empty — pass at least one rectangle."
        new_items = batch
    else:
        item = _erase_rect_item(existing, x, y, w, h, start, end, fill)
        if isinstance(item, str):
            return item
        new_items = [item]
    descs = []
    for it in new_items:
        window = (f" {it['start']}-{it['end']}s" if it["start"] is not None
                  else "")
        descs.append(f"{'object' if it['fill'] == 'box' else 'text'} at "
                     f"x={it['x']},y={it['y']} size {it['w']}x{it['h']}"
                     f"{window} [{it['id']}]")
    what = ("erased from the source pixels: " + "; ".join(descs)
            if len(new_items) > 1 else
            f"erased the {descs[0]} from the source pixels")
    # Round 92: new erases become window PATCHES — each call repaints only
    # its own span, so nothing here ever re-derives earlier work. The old
    # subsume bookkeeping (whose point was per-pass cost) stayed gone, but
    # a re-erase of the same band now REPLACES the superseded patch — for
    # seams, not cost: two repaints stacked over one band is what
    # "corrupted" project 382's screen.
    try:
        return _apply_patches(
            ctx, new_items, what,
            drop=_superseded_patches(edl, new_items, ctx.duration))
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
    # Invoking the tool is the decision to reset. Version history keeps the
    # operation recoverable, so a keyword matcher must not second-guess it.
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
    """Undo one erase (or all). A patch erase (round 92) is undone by simply
    dropping its overlay — instant, nothing re-derives; a legacy whole-file
    erase re-cleans from the untouched original as it always did."""
    edl = dict(ctx.latest_edl()["json"])
    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    patches = [dict(p) for p in (edl.get("patches") or [])]
    if not regions and not patches:
        return ("NO CHANGE: nothing has been erased from this video's pixels. "
                "Do NOT tell the user you restored anything.")
    if id:
        # A patch id ('pa*') or any region id INSIDE a patch drops that
        # patch; a legacy region id re-cleans without it.
        hit_patch = next(
            (p for p in patches
             if p.get("id") == id
             or any(r.get("id") == id for r in (p.get("regions") or []))),
            None)
        if hit_patch is not None:
            edl["patches"] = [p for p in patches
                              if p["id"] != hit_patch["id"]]
            return ctx.write_edl(
                edl, f"put back the pixels erased by {id} — the repaint "
                     "overlay is gone and the window shows the source again")
        hit = next((r for r in regions if r.get("id") == id), None)
        if not hit:
            have = ([r.get("id", "?") for r in regions]
                    + [p.get("id", "?") for p in patches])
            return (f"REJECTED: no erased region with id '{id}'. Existing: "
                    f"{', '.join(have)}. Call get_edl to see them, or omit "
                    "id to restore the whole original picture.")
        regions = [r for r in regions if r.get("id") != id]
        try:
            return _apply_clean(ctx, regions,
                                f"put back the pixels erased by {id}")
        except Exception as e:
            return (f"Could not rebuild the video ({str(e)[:180]}). "
                    "Nothing changed.")
    total = len(regions) + len(patches)
    what = f"put back all {total} erased region(s)"
    edl["patches"] = []
    if regions:
        # The legacy clean clears (or re-derives cursor-only) through
        # _apply_clean, and the same write carries the emptied patch list.
        try:
            return _apply_clean(ctx, [], what, base_edl=edl)
        except Exception as e:
            return (f"Could not rebuild the video ({str(e)[:180]}). "
                    "Nothing changed.")
    return ctx.write_edl(edl, what)


# ── The pointer pass (round 51) ─────────────────────────────────────────────
# "The cursor is too small" and "the cursor is too jittery" are the two things
# people say about their own screen recordings, and until now both were a no.
# The pointer is found in the source frames, its path is filtered, and it is
# redrawn bigger — into the same derived-source file the erase writes, so a
# video can be both de-captioned and cursor-enhanced without either pass
# fighting the other.

def enhance_cursor(ctx, scale=None, smoothing=None, click_highlight=None,
                   click_times=None):
    """Find the mouse pointer in the source, smooth its path and redraw it
    bigger, with a ripple at each supplied click time."""
    edl = dict(ctx.latest_edl()["json"])
    cur = dict(_edl_cursor(edl) or {})
    spec = {"scale": cur.get("scale", 2.0),
            "smoothing": cur.get("smoothing", 0.5),
            "click_highlight": cur.get("click_highlight", True),
            "click_times": list(cur.get("click_times") or [])}
    if scale is not None:
        try:
            spec["scale"] = round(min(max(float(scale), CURSOR_SCALE_MIN),
                                      CURSOR_SCALE_MAX), 2)
        except (TypeError, ValueError):
            return (f"REJECTED: scale must be a number "
                    f"({CURSOR_SCALE_MIN}-{CURSOR_SCALE_MAX}) — how many "
                    "times bigger the pointer is redrawn. 2 is the usual "
                    "answer for 'the cursor is too small'.")
    if smoothing is not None:
        try:
            spec["smoothing"] = round(min(max(float(smoothing), 0.0), 1.0), 3)
        except (TypeError, ValueError):
            return ("REJECTED: smoothing must be 0-1. 0 keeps the pointer's "
                    "real path; 1 holds a resting hand still. Fast "
                    "deliberate moves stay sharp at any setting.")
    if click_highlight is not None:
        spec["click_highlight"] = bool(click_highlight)
    if click_times is not None:
        if not isinstance(click_times, list):
            return ("REJECTED: click_times must be a list of SOURCE-video "
                    "seconds — the moments the mouse was actually pressed. "
                    "There is no way to see a click in the pixels, so these "
                    "have to be told to me: record_website_demo returns them, "
                    "and for a recording the user made, ask them (or leave "
                    "them out and just fix the size).")
        times = []
        for i, t in enumerate(click_times):
            try:
                times.append(round(max(0.0, float(t)), 2))
            except (TypeError, ValueError):
                return (f"REJECTED: click_times[{i}] is not a number of "
                        "seconds.")
        spec["click_times"] = sorted(set(times))

    if spec["scale"] <= 1.0 and spec["smoothing"] <= 0.0 \
            and not spec["click_times"]:
        return ("REJECTED: with scale 1, no smoothing and no click times this "
                "would re-encode the whole video to change nothing. Raise "
                "scale (2 is the usual answer), raise smoothing, or pass "
                "click_times.")

    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    ctx._cursor_stats = None
    try:
        key, pkey, fp = _run_clean(ctx, regions, spec)
    except Exception as e:
        return (f"Could not run the cursor pass ({str(e)[:200]}). Nothing "
                "changed — do NOT tell the user the cursor was enhanced.")
    # None means UNKNOWN, not zero: _run_clean short-circuits on a cached
    # fingerprint and never opens the video. The previously recorded fraction
    # is the right answer there (same fingerprint = same detection); with
    # neither, nothing about coverage is claimed.
    stats = getattr(ctx, "_cursor_stats", None) or {}
    found = stats.get("found_frac", cur.get("found_frac"))
    # THE HONEST FLOOR. A recording with no visible pointer (a phone capture, a
    # tap-driven demo, an app that hides the cursor) produces a file identical
    # to the source, and writing it would bill a re-encode and report a change
    # nobody can see. Refuse and say why instead.
    if found is not None and found < 0.15:
        return (f"NOT APPLIED: I could only find a mouse pointer in "
                f"{int(found * 100)}% of the frames, so there is nothing to "
                "enlarge — this looks like a recording with no visible cursor "
                "(a phone/tablet capture, or an app that hides it). Tell the "
                "user that plainly and offer what does work on this footage: "
                "a zoom that follows the action (add_zoom_path), or the "
                "floating frame (set_screen_frame). The edit is unchanged.")

    edl["source_clean"] = {"asset_key": key, "proxy_key": pkey, "fp": fp,
                           "regions": regions,
                           "cursor": dict(spec, found_frac=found)}
    bits = [f"{spec['scale']:g}x"]
    if spec["smoothing"] > 0:
        bits.append(f"smoothing {spec['smoothing']:g}")
    if spec["click_highlight"] and spec["click_times"]:
        bits.append(f"{len(spec['click_times'])} click ripple(s)")
    written = ctx.write_edl(edl, "cursor pass: " + ", ".join(bits))
    if not written.startswith("EDL v"):
        return written
    note = ""
    if found is not None and found < 0.75:
        note = (f"\nThe pointer was found in {int(found * 100)}% of frames — "
                "in the rest its position is interpolated between the frames "
                "either side, which is right for a pointer crossing a busy "
                "area and wrong if it genuinely left the screen. Look at the "
                "preview and say what you see.")
    if not spec["click_times"]:
        note += ("\nNo click ripples were added, because clicks are not "
                 "visible in a recording — nothing in the pixels tells a "
                 "press from a hover. If the user wants them, ask WHEN the "
                 "clicks happen (source seconds) and call this again with "
                 "click_times.")
    return (written + note + "\nThis is baked into the source the render "
            "reads, so every cut keeps it and no timestamp moved. Undo it "
            "with remove_cursor_enhance.")


def remove_cursor_enhance(ctx):
    edl = dict(ctx.latest_edl()["json"])
    if not _edl_cursor(edl):
        return ("NO CHANGE: the cursor on this video has not been enhanced. "
                "Do NOT tell the user you restored anything.")
    regions = [dict(r) for r in
               ((edl.get("source_clean") or {}).get("regions") or [])]
    try:
        # Re-derives from the untouched original, exactly like remove_erase —
        # never by un-drawing a cursor from an already-drawn file.
        return _apply_clean(ctx, regions, "removed the cursor pass "
                            "(the original pointer is back)", cursor=None)
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


def _blank_asset_window(ctx, asset, start, duration):
    """Objective blank-frame evidence for an indexed video-clip window."""
    sha = asset.get("sha256")
    if not sha:
        return None
    try:
        row = ctx.db.run(dbx.get_index_by_sha, sha)
        sidecar = ((row or {}).get("json") or {}).get("spatial") or {}
    except Exception:
        return None
    a, b = float(start), float(start) + float(duration)
    samples = [s for s in (sidecar.get("samples") or [])
               if a - 0.05 <= float(s.get("t") or 0.0) <= b + 0.05]
    if not samples:
        return None

    def _is_blank(sample):
        if sample.get("faces") or sample.get("text") or sample.get("dense_ui"):
            return False
        try:
            mean = float(sample.get("mean_luma"))
            spread = float(sample.get("std_luma"))
            edges = float(sample.get("edge_density") or 0.0)
        except (TypeError, ValueError):
            return False                    # old sidecar: no proof, no block
        return spread < 4.0 and edges < 0.002 and (mean < 8.0 or mean > 247.0)

    if all(_is_blank(s) for s in samples):
        return (f"all {len(samples)} measured frame(s) in clip window "
                f"{a:.1f}-{b:.1f}s are flat "
                f"{'black/near-black' if float(samples[0]['mean_luma']) < 8 else 'white/near-white'}")
    return None


INSERT_MOTIONS = ("zoom_in", "zoom_out", "pan_left", "pan_right")


def _visual_asset_uses(edl, asset_key, exclude_ids=()):
    """Report existing uses of an exact asset without restricting reuse.

    Repeating footage can be intentional, so this diagnostic is information
    for editorial review rather than a permission check or write veto.
    """
    excluded = set(exclude_ids or ())
    uses = []
    for item in edl.get("inserts") or []:
        if item.get("asset_key") == asset_key and item.get("id") not in excluded:
            uses.append(f"insert {item.get('id', '?')}")
    for item in edl.get("overlays") or []:
        if item.get("asset_key") == asset_key and item.get("id") not in excluded:
            uses.append(f"overlay {item.get('id', '?')}")
    return uses


def _dropped_motion_note(motion, at=None, dur=None):
    """The one line that replaces a rejection when `motion` rode a VIDEO
    insert. Names the tool that DOES move a clip, with the window already
    filled in, so the follow-up call needs no arithmetic."""
    if not motion:
        return ""
    where = (f" add_zoom(start={at}, end={round(at + dur, 2)}, "
             f"mode='push_in') over its window"
             if at is not None and dur is not None
             else " add_zoom over its window")
    return (f"\nNote: motion={motion!r} was ignored — it is a Ken Burns move "
            f"for STILLS, and this is a video clip that already moves. For a "
            f"camera move on top of the clip, call{where}.")


def insert_media(ctx, asset_key, at_output_s, duration_s=None,
                 clip_start_s=None, motion=None, fit="auto",
                 allow_repeat=False):
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
    # A motion asked for on a VIDEO clip is dropped, not rejected (round 101).
    # 92 rejections in a week, and every one of them threw away a placement
    # the tool could have made: the insert is entirely well-specified without
    # the motion, and the clip does move on its own — so refusing the whole
    # call punished the agent for a redundant argument, at the price of a
    # full step of the user's wait. If the agent really wants a move ON a
    # clip, add_zoom over the insert's window is the tool, and the note says
    # so where it will be read.
    dropped_motion = None
    if motion and kind != "image":
        dropped_motion, motion = motion, None
    if motion:
        if motion not in INSERT_MOTIONS:
            return (f"REJECTED: motion must be one of "
                    f"{', '.join(INSERT_MOTIONS)}.")
    fitv = str(fit or "auto").strip().lower().replace("-", "_")
    if fitv in ("auto", "safe", "contain", "fit"):
        # Lossless by default. A same-aspect asset renders identically; a
        # mismatched one gets a blurred extension instead of silently losing
        # the subject/card edges to a center crop.
        fitv = "pad_blur"
    elif fitv in ("pad", "letterbox"):
        fitv = "pad"
    elif fitv in ("pad_blur", "blur", "blurred"):
        fitv = "pad_blur"
    elif fitv in ("crop", "cover", "fill"):
        fitv = "crop"
    else:
        return ("REJECTED: fit must be 'auto' (safe whole-picture fit), "
                "'pad_blur', 'pad', or 'crop'. Use crop only after "
                "look_at_asset confirms the meaningful content survives.")
    off = 0.0
    if kind == "image":
        try:
            dur = round(max(float(duration_s if duration_s is not None
                                  else 3.0), 0.2), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."
    else:
        clip_dur = _asset_media_duration(ctx, asset)
        try:
            dur = round(min(max(float(duration_s), 0.2), clip_dur), 2) \
                if duration_s is not None else round(
                    clip_dur, 2)
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
                "duration_s": dur, "fit": fitv}
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
        return ctx.write_edl(edl, desc) + _dropped_motion_note(dropped_motion)

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
            "kind": kind, "at_output_s": target_pre, "duration_s": dur,
            "fit": fitv}
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
    return result + _dropped_motion_note(dropped_motion, final_at, dur)


def set_insert_window(ctx, id, duration_s=None, clip_start_s=None,
                      rate=None, crop=None, mute=None, fit=None,
                      rotation=None):
    """Change WHICH PART of an already-spliced clip plays, in place.

    Round 61. Nothing could edit an insert once it existed — there was
    insert_media and remove_insert and nothing between them — so "trim the clip
    you put in" and "split it" both came out as remove-then-re-add. A real user
    watched that happen twice in one session and asked why the editor kept
    taking his clip out and putting it back: two EDL versions per adjustment,
    two full preview encodes, and the block visibly vanishing from his timeline
    in between. The clip was never in doubt; only its window was.

    It stays at the same boundary, so the program's other content does not move
    for a shortening except by the length that came off the end — the same
    re-anchoring remove_insert already does, through the same shared remap.

    TO SPLIT a spliced clip: shorten it here, then insert_media the same
    asset_key at the same at_output_s with clip_start_s = the head's end. Both
    halves sit at one boundary and play in LIST order (timeline._ins_sort_key),
    so the head — shortened first, therefore still first in the list — plays
    first.
    """
    edl = dict(ctx.latest_edl()["json"])
    before = [dict(i) for i in (edl.get("inserts") or [])]
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    hit = next((i for i in inserts if i.get("id") == id), None)
    if not hit:
        have = ", ".join(i.get("id", "?") for i in inserts) or "none"
        return (f"REJECTED: no insert with id '{id}'. Existing inserts: "
                f"{have}. Call get_edl to see them.")
    if duration_s is None and clip_start_s is None and rate is None \
            and crop is None and mute is None and fit is None \
            and rotation is None:
        return ("REJECTED: give duration_s, clip_start_s, rate, crop, mute "
                "fit and/or rotation — otherwise there is nothing to change.")
    # fit (round 79): how this ONE scene maps onto the canvas. The program's
    # cover-crop default beheads a still whose aspect fights the canvas (a
    # 9:16 logo on a 16:9 program shows only its middle band); 'pad' shows
    # the whole picture on black bars, 'pad_blur' on a blurred backdrop,
    # 'crop' forces the cover-crop, ''/'auto' clears back to the default.
    fit_val = None                   # None = untouched; "clear"; or a mode
    if fit is not None:
        s = str(fit).strip().lower().replace("-", "_")
        if s in ("", "none", "null", "auto", "default", "clear"):
            fit_val = "clear"
        elif s in ("pad", "letterbox", "fit", "contain"):
            fit_val = "pad"
        elif s in ("pad_blur", "blur", "blurred"):
            fit_val = "pad_blur"
        elif s in ("crop", "cover", "fill"):
            fit_val = "crop"
        else:
            return ("REJECTED: fit must be 'pad' (whole picture, black "
                    "bars), 'pad_blur' (whole picture over a blurred "
                    "backdrop), 'crop' (fill the frame, edges cut), or "
                    "'auto' to clear the override.")
    # mute (round 78): the scene's OWN audio off — "mute that clip" had no
    # tool at all (set_volume reaches only the main footage's source time),
    # which on an all-inserts program meant no way to silence anything.
    mute_val = None                  # None = untouched; True; False
    if mute is not None:
        if isinstance(mute, str):    # stale-MCP passthrough types it string
            s = mute.strip().lower()
            if s in ("true", "1", "yes", "on"):
                mute_val = True
            elif s in ("false", "0", "no", "off", ""):
                mute_val = False
            else:
                return ("REJECTED: mute must be true or false.")
        else:
            mute_val = bool(mute)
        if hit.get("kind") == "image" and mute_val:
            return ("REJECTED: an image insert has no audio to mute — "
                    "stills always play silent.")
    # crop (round 77): the scene shows ONE REGION of the clip, letterboxed.
    # "The full timeline visible, static, with no player and no chat" is
    # geometrically impossible for a zoom — a 16:9 window that spans a 2.6:1
    # UI strip must also span what sits above it — and the answer is to
    # crop the INSERT, not to fight the viewport. Accepts [x0,y0,x1,y1]
    # fractions of the source frame, the same JSON as a string (stale MCP
    # schemas stringify new list params), or "full"/"none"/[] to clear.
    crop_val = None                  # None = untouched; "clear"; or [4]
    if crop is not None:
        cv = crop
        if isinstance(cv, str):
            s = cv.strip().lower()
            if s in ("", "none", "null", "full", "clear"):
                cv = []
            else:
                try:
                    cv = json.loads(cv)
                except ValueError:
                    return ("REJECTED: crop must be [x0, y0, x1, y1] "
                            "fractions of the clip's frame ((0,0) = "
                            "top-left), or 'full' to clear it.")
        if isinstance(cv, (list, tuple)) and len(cv) == 0:
            crop_val = "clear"
        else:
            try:
                cx0, cy0, cx1, cy1 = (min(max(float(v), 0.0), 1.0)
                                      for v in cv)
            except (TypeError, ValueError):
                return ("REJECTED: crop must be [x0, y0, x1, y1] fractions "
                        "of the clip's frame ((0,0) = top-left) — read them "
                        "off a look_at_asset grid. Pass 'full' to clear.")
            if cx1 <= cx0 or cy1 <= cy0:
                return ("REJECTED: crop needs x0<x1 and y0<y1; any positive "
                        "region size is otherwise allowed.")
            crop_val = [round(cx0, 4), round(cy0, 4),
                        round(cx1, 4), round(cy1, 4)]
    if hit.get("kind") == "image" and clip_start_s is not None:
        # A uniform image/video batch often carries the neutral seek 0. It has
        # no effect on a still, so ignoring it is safer than aborting every
        # sibling operation in an atomic recipe.
        try:
            neutral_image_seek = abs(float(clip_start_s)) < 1e-9
        except (TypeError, ValueError):
            neutral_image_seek = False
        if neutral_image_seek:
            clip_start_s = None
        else:
            return ("REJECTED: clip_start_s is for video inserts — a still "
                    "has no timeline to seek into. Use duration_s to change "
                    "how long it shows.")
    if hit.get("kind") == "image" and rate is not None:
        return ("REJECTED: rate is for video inserts — a still has no speed. "
                "Use duration_s to change how long it shows.")
    # rate (round 76): the spliced scene plays FASTER (or slower) IN PLACE —
    # "don't shorten the editing screens, speed them up". duration_s stays
    # OUTPUT seconds. rate alone keeps the clip's source window and shrinks
    # the block (10s of recording at 2x = a 5s scene, nothing lost); with
    # duration_s the block is that long and consumes duration_s*rate of clip.
    old_rate = float(hit.get("rate") or 1.0)
    r = old_rate
    if rate is not None:
        try:
            r = float(rate)
        except (TypeError, ValueError):
            return (f"REJECTED: rate must be a number "
                    f"{INSERT_RATE_MIN}-{INSERT_RATE_MAX} (1 = normal "
                    "speed, 2 = twice as fast, 0.5 = half speed).")
        r = min(max(r, INSERT_RATE_MIN), INSERT_RATE_MAX)
    # The clip's real length bounds the window. Without it a duration longer
    # than the file renders as a block the footage cannot fill.
    src_len = None
    asset = None
    if hit.get("kind") == "video":
        asset, _err = _resolve_media_asset(ctx, hit["asset_key"],
                                           ("video_clip",))
        if asset:
            try:
                src_len = float(_asset_media_duration(ctx, asset) or 0.0) or None
            except Exception:
                src_len = None
    off = float(hit.get("source_start_s") or 0.0)
    if clip_start_s is not None:
        try:
            off = max(0.0, round(float(clip_start_s), 2))
        except (TypeError, ValueError):
            return "REJECTED: clip_start_s must be a number of seconds."
    dur = float(hit["duration_s"])
    if duration_s is not None:
        try:
            dur = round(float(duration_s), 2)
        except (TypeError, ValueError):
            return "REJECTED: duration_s must be a number of seconds."
        if dur < 0.2:
            return ("REJECTED: an insert shorter than 0.2s is a single frame "
                    "nobody sees. Remove it instead if you want it gone.")
    elif rate is not None:
        # rate alone: same source window, new tempo — the block's length
        # follows so no footage is gained or lost.
        dur = round(float(hit["duration_s"]) * old_rate / r, 2)
    if hit.get("kind") == "video" and src_len:
        if off >= src_len - 0.05:
            return (f"REJECTED: clip_start_s {off}s is at or past the end of "
                    f"that clip ({round(src_len, 2)}s long).")
        room = round((src_len - off) / r, 2)
        if dur > room:
            dur = room
    rot_val = None                    # None = untouched; "clear"; or degrees
    if rotation is not None:
        aliases = {"none": 0, "clear": 0, "auto": 0, "upright": 0,
                   "cw": 90, "clockwise": 90, "right": 90,
                   "ccw": 270, "counterclockwise": 270, "left": 270,
                   "upside_down": 180, "upside-down": 180}
        try:
            rv = aliases.get(str(rotation).strip().lower(), rotation)
            rv = int(round(float(rv))) % 360
        except (TypeError, ValueError):
            return ("REJECTED: rotation must be 0, 90, 180 or 270 degrees "
                    "clockwise, or 'clear'.")
        if rv not in (0, 90, 180, 270):
            return ("REJECTED: rotation must be a quarter turn: 0, 90, 180 "
                    "or 270 degrees clockwise.")
        rot_val = "clear" if rv == 0 else rv
    old_crop = list(hit.get("crop") or [])
    old_mute = bool(hit.get("mute"))
    old_fit = hit.get("fit") or None
    old_rotation = int(hit.get("rotation") or 0)
    prev = (float(hit["duration_s"]), float(hit.get("source_start_s") or 0.0),
            old_rate, old_crop, old_mute, old_fit, old_rotation)
    hit["duration_s"] = dur
    hit["source_start_s"] = round(off, 2) or None
    if hit["source_start_s"] is None:
        hit.pop("source_start_s", None)
    if abs(r - 1.0) > 1e-6:
        hit["rate"] = round(r, 3)
    else:
        hit.pop("rate", None)
    if crop_val == "clear":
        hit.pop("crop", None)
    elif crop_val is not None:
        hit["crop"] = crop_val
    if mute_val is True:
        hit["mute"] = True
    elif mute_val is False:
        hit.pop("mute", None)
    if fit_val == "clear":
        hit.pop("fit", None)
    elif fit_val is not None:
        hit["fit"] = fit_val
    if rot_val == "clear":
        hit.pop("rotation", None)
    elif rot_val is not None:
        hit["rotation"] = rot_val
    new_crop = list(hit.get("crop") or [])
    new_mute = bool(hit.get("mute"))
    new_fit = hit.get("fit") or None
    new_rotation = int(hit.get("rotation") or 0)
    span = round(dur * r, 2)
    at_rate = f" at {r:g}x" if abs(r - 1.0) > 1e-6 else ""
    reg = ""
    if new_crop:
        rw, rh = new_crop[2] - new_crop[0], new_crop[3] - new_crop[1]
        if rw >= rh:
            bars = (f"black bars top+bottom (~{(1 - rh / rw) / 2:.0%} "
                    "each)")
        else:
            bars = (f"black bars left+right (~{(1 - rw / rh) / 2:.0%} "
                    "each)")
        reg = (f", showing ONLY region x{new_crop[0]:g}-{new_crop[2]:g} "
               f"y{new_crop[1]:g}-{new_crop[3]:g} of the frame — "
               f"letterboxed, {bars}")
    elif crop_val == "clear" and old_crop:
        reg = ", back to the full frame"
    if new_fit == "pad":
        reg += (", fitted WHOLE into the frame (letterboxed on black "
                "instead of cover-cropped)")
    elif new_fit == "pad_blur":
        reg += ", fitted whole over a blurred backdrop"
    elif new_fit == "crop":
        reg += ", cover-cropped to fill the frame"
    elif fit_val == "clear" and old_fit:
        reg += ", back to the program's default framing"
    if new_rotation != old_rotation:
        reg += f", rotated {new_rotation}° clockwise"
    if new_mute:
        reg += ", its own audio MUTED"
    elif mute_val is False and old_mute:
        reg += ", its own audio back ON"
    if (dur, off, r, new_crop, new_mute, new_fit, new_rotation) == prev:
        return (f"insert {id} already plays {off}-{round(off + span, 2)}s"
                f"{at_rate}{reg}")
    edl["inserts"] = inserts
    speed = edl.get("speed") or []
    old_tl = Timeline(edl.get("keep") or [], before, speed)
    new_tl = Timeline(edl.get("keep") or [], inserts, speed)
    notes = _remap_program_items(edl, old_tl, new_tl)
    res = ctx.write_edl(
        edl, f"insert {id} now plays {off}-{round(off + span, 2)}s of "
             f"'{os.path.basename(hit['asset_key'])}'{at_rate} "
             f"({dur}s on the timeline){reg}")
    if notes and res.startswith("EDL v"):
        res += "\n" + "\n".join(notes)
    return res


def move_insert(ctx, id, after_id=None):
    """Reorder a spliced scene — round 75.

    "Move the uploaded clip between those two scenes" had no tool: inserts
    at one boundary play in LIST order, and nothing could change the order
    or the boundary of an existing insert — the only path was remove +
    re-insert, two writes and the clip visibly vanishing from the timeline
    in between (the same complaint that produced set_insert_window in round
    61). This moves the item in place: after_id names the insert the moved
    one should play right AFTER (adopting that insert's boundary); omitted,
    it plays FIRST at its own boundary. Everything program-anchored
    re-anchors through the shared remap — including a zoom choreographed on
    the moved scene, which follows it to its new place."""
    edl = dict(ctx.latest_edl()["json"])
    before = [dict(i) for i in (edl.get("inserts") or [])]
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    hit = next((i for i in inserts if i.get("id") == id), None)
    if not hit:
        have = ", ".join(i.get("id", "?") for i in inserts) or "none"
        return (f"REJECTED: no insert with id '{id}'. Existing inserts: "
                f"{have}. Call get_edl to see them.")
    if after_id == id:
        return "REJECTED: after_id must be a DIFFERENT insert."
    rest = [i for i in inserts if i.get("id") != id]
    if after_id:
        anchor = next((i for i in rest if i.get("id") == after_id), None)
        if anchor is None:
            have = ", ".join(i.get("id", "?") for i in rest) or "none"
            return (f"REJECTED: no insert with id '{after_id}' to place "
                    f"after. Existing inserts: {have}. Call get_edl — the "
                    "scene map names each scene's insert id.")
        hit = dict(hit, at_output_s=anchor["at_output_s"])
        k = rest.index(anchor)
        new_list = rest[:k + 1] + [hit] + rest[k + 1:]
    else:
        # First at its own boundary: the sort is stable on at_output_s, so
        # list-front makes it first among its boundary-mates.
        new_list = [hit] + rest
    speed = edl.get("speed") or []
    old_tl = Timeline(edl.get("keep") or [], before, speed)
    edl["inserts"] = new_list
    new_tl = Timeline(edl.get("keep") or [], new_list, speed)
    notes = _remap_program_items(edl, old_tl, new_tl)
    win = insert_windows(new_list, new_tl).get(id)
    where = (f"it now plays {round(win[0], 2)}-{round(win[1], 2)}s of the "
             "program" if win else "moved")
    res = ctx.write_edl(
        edl, f"moved insert {id} "
             + (f"to play right after {after_id}" if after_id
                else "to play first at its boundary")
             + f" — {where}")
    if notes and res.startswith("EDL v"):
        res += "\n" + "\n".join(notes)
    return res


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


def cut_output_range(ctx, start, end):
    """Cut a span of the ASSEMBLED program — output seconds — no matter what
    plays there (round 71e).

    cut_range speaks SOURCE time and therefore cannot touch a spliced insert
    at all. A real user asked to cut 12-15s and 18-21s of the edited video —
    both inside an inserted screen recording — was told three times it was
    impossible, and then watched the agent mangle the insert's window with
    set_insert_window instead. This tool resolves the span through the
    program map: kept footage under it is cut in source time, and any insert
    it crosses is SPLIT around it (head keeps the id, tail gets a new one,
    both at the same boundary in list order — the round-61 contract) or
    removed outright when the span swallows it. One version write, and the
    shared remap re-anchors every program-time item exactly as a keep cut
    does.
    """
    prev = ctx.latest_edl()
    edl = dict(prev["json"])
    keep = [list(x) for x in (edl.get("keep") or [])]
    speed = edl.get("speed") or []
    ins_before = [dict(i) for i in (edl.get("inserts") or [])]
    tl = Timeline(keep, ins_before, speed)
    prog = tl.out_duration
    try:
        a = min(max(float(start), 0.0), prog)
        b = min(max(float(end), 0.0), prog)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers of OUTPUT seconds."
    if b - a < 0.05:
        return (f"REJECTED: the range to cut must be at least 0.05s inside "
                f"the program (which is {round(prog, 2)}s long).")

    # Kept footage under the span -> source-time cuts.
    src_cuts = []
    for (_s, _e), off, L in zip(tl.segs, tl.offsets, tl.seg_out_len):
        lo, hi = max(a, off), min(b, off + L)
        if hi - lo > 0.05:
            s0, s1 = tl.out_to_src(lo), tl.out_to_src(hi)
            if s0 is not None and s1 is not None and s1 - s0 > 0.01:
                src_cuts.append([round(s0, 3), round(s1, 3)])

    # Inserts under the span -> split / trim / remove.
    wins = insert_windows(ins_before, tl)
    new_inserts, removed, touched = [], [], []
    for item in ins_before:
        w = wins.get(item.get("id"))
        if not w:
            new_inserts.append(item)
            continue
        w0, w1 = w
        lo, hi = max(a, w0), min(b, w1)
        if hi - lo <= 0.05:
            new_inserts.append(item)
            continue
        head_len = round(lo - w0, 2)
        tail_len = round(w1 - hi, 2)
        # Slivers under 0.2s are single frames nobody sees (the
        # set_insert_window floor) — the cut swallows them.
        if head_len < 0.2 and tail_len < 0.2:
            removed.append(item["id"])
            continue
        off_c = float(item.get("source_start_s") or 0.0)
        # A rated insert covers rate x as much CLIP per output second — the
        # tail's clip offset scales, or the cut jumps the wrong footage.
        rate_c = float(item.get("rate") or 1.0)
        if head_len >= 0.2:
            head = dict(item)
            head["duration_s"] = head_len
            new_inserts.append(head)
        if tail_len >= 0.2:
            tail = dict(item)
            if head_len >= 0.2:
                tail["id"] = _next_item_id(ins_before + new_inserts, "ins")
            tail["duration_s"] = tail_len
            if item.get("kind") == "video":
                tail["source_start_s"] = round(off_c + (hi - w0) * rate_c, 2)
            new_inserts.append(tail)
        touched.append(item["id"])

    if not src_cuts and not removed and not touched:
        return (f"REJECTED: {round(a, 2)}-{round(b, 2)}s covers nothing in "
                "the program — call get_edl and read THE ASSEMBLED PROGRAM "
                "map for where things actually are.")
    new_keep = keep
    if src_cuts:
        new_keep = [list(x) for x in audit.subtract_spans(keep, src_cuts)]
        if not new_keep:
            return ("REJECTED: that would remove ALL the kept footage. Cut "
                    "a smaller span, or remove the inserts individually and "
                    "reset_edit for the footage.")
    ins_notes = []
    if new_inserts and new_keep != keep:
        new_inserts, ins_notes = timeline_mod.resnap_inserts(
            new_inserts, keep, new_keep, speed, speed)
    edl["keep"] = new_keep
    edl["inserts"] = new_inserts
    new_tl = Timeline(new_keep, new_inserts, speed)
    notes = ins_notes + _remap_program_items(edl, tl, new_tl)
    bits = []
    if src_cuts:
        bits.append("footage " + ", ".join(f"{s}-{e}s" for s, e in src_cuts)
                    + " (source)")
    if removed:
        bits.append("removed " + ", ".join(removed))
    split_ids = [i for i in touched if i not in removed]
    if split_ids:
        bits.append("split/trimmed " + ", ".join(split_ids))
    res = ctx.write_edl(
        edl, f"cut {round(a, 2)}-{round(b, 2)}s of the ASSEMBLED program "
             f"({'; '.join(bits)})")
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
                  duck_others=True, source_offset_s=None):
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
        off = round(max(0.0, float(source_offset_s or 0.0)), 2)
    except (TypeError, ValueError):
        return ("REJECTED: start_output_s, source_offset_s and gain_db must "
                "be numbers "
                "(start is a position in the FINAL edited video).")
    asset_dur = _asset_media_duration(ctx, asset)
    if off >= asset_dur - 0.05:
        return (f"REJECTED: source_offset_s {off}s is at/past the end of "
                f"'{_asset_name(asset)}' ({asset_dur:.2f}s). Pick an offset "
                "inside the file; no external trim is needed.")
    vos = [dict(v) for v in (edl.get("voiceover") or [])]
    item = {"id": _next_item_id(vos, "vo"), "asset_key": asset_key,
            "start_output_s": start, "gain_db": g,
            "duck_others": bool(duck_others)}
    if off:
        item["source_offset_s"] = off
    edl["voiceover"] = vos + [item]
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    res = ctx.write_edl(
        edl, f"voiceover '{name}' from {start}s (output time), "
             f"source offset {off:g}s, {g:+.1f}dB, "
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
        # than its own remapped 2.5). The keep list is unchanged here, so the
        # shared resnap matches every insert's own source anchor exactly and
        # preserves which cut it sits at, on the new clock.
        edl["inserts"], ins_notes = timeline_mod.resnap_inserts(
            edl["inserts"], keep, keep,
            prev["json"].get("speed") or [], speed)
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
                source_start_s=None, fit=None, allow_repeat=False):
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
    if hit.get("screen"):
        # Dropping the pin alone leaves the handoff clip cutting in cold, with
        # no push into it — a jump cut the user never asked for, from a tool
        # they used to undo something.
        return (f"REJECTED: '{id}' is a screen takeover, not a plain overlay. "
                f"Use remove_screen_takeover('{id}') — it takes the camera "
                "push and the clip it hands off to with it.")
    edl["overlays"] = [o for o in items if o.get("id") != id]
    return ctx.write_edl(
        edl, f"removed overlay {id} "
             f"('{os.path.basename(hit['asset_key'])}', "
             f"{hit['start']}-{round(float(hit['start']) + float(hit['duration_s']), 2)}s)")


# ── The screen takeover (round 55) ──────────────────────────────────────────
# "Here is a video of my laptop — zoom into the screen and let the other clip
# take over from there, smoothly." Everything the move needs already existed
# and none of it worked, because the pieces are drawn in the wrong order: an
# overlay sits ABOVE the zoom, so content dropped on the screen stays flat and
# still while the shot pushes past it, and the cut to full-screen is a jump
# because nothing makes the two frames line up. The answer is not more
# arguments on add_overlay — it is one item that owns the camera AND the
# content, so the two are derived from the same numbers. See schemas.ScreenLock
# and renderer.screen_lock_corner_paths.


def _out_frac_from_source(ctx, edl, sx, sy):
    """Map a SOURCE-frame fraction to an OUTPUT-frame fraction.

    Everything the detector measures is in source pixels; everything the pin
    consumes is in output pixels, and between them sits whatever `frame` the
    project is rendering at. A 16:9 shot reframed to 9:16 throws away 44% of
    the width — a screen quad handed straight through would be pinned to the
    wrong half of the picture. Returns None when the point is cropped away.
    """
    fr = edl.get("frame") or {}
    ratio = fr.get("ratio") or "source"
    if ratio == "source":
        return sx, sy
    info = ctx.index.get("video") or {}
    sw = float(info.get("width") or 0) or 1920.0
    sh = float(info.get("height") or 0) or 1080.0
    W, H = renderer.frame_dims(sw, sh, ratio)
    mode = fr.get("mode") or "crop"
    if mode == "crop":
        s = max(W / sw, H / sh)
        scaled_w, scaled_h = sw * s, sh * s
        fx = fr.get("focus_x")
        fy = fr.get("focus_y")
        fx = 0.5 if fx is None else float(fx)
        fy = 0.5 if fy is None else float(fy)
        x0 = min(max(scaled_w * fx - W / 2.0, 0.0), scaled_w - W)
        y0 = min(max(scaled_h * fy - H / 2.0, 0.0), scaled_h - H)
        ox = (sx * scaled_w - x0) / W
        oy = (sy * scaled_h - y0) / H
    else:                                       # pad / pad_blur: fit + bars
        s = min(W / sw, H / sh)
        scaled_w, scaled_h = sw * s, sh * s
        ox = (sx * scaled_w + (W - scaled_w) / 2.0) / W
        oy = (sy * scaled_h + (H - scaled_h) / 2.0) / H
    if not (-0.02 <= ox <= 1.02 and -0.02 <= oy <= 1.02):
        return None
    return min(max(ox, 0.0), 1.0), min(max(oy, 0.0), 1.0)


def _out_frac_from_insert(ctx, edl, asset_aspect, x, y):
    """Map a fraction of an INSERTED clip's own frame to an OUTPUT-frame
    fraction (round 62).

    A spliced clip is normalized to the output frame by the renderer —
    cover-cropped when the project's frame mode is crop, letterboxed when it
    is pad — so a screen measured in the ASSET's pixels sits somewhere else
    in the program's pixels, exactly as a reframed source does. Same contract
    as _out_frac_from_source: None when the point is cropped away.
    """
    fr = edl.get("frame") or {}
    ratio = fr.get("ratio") or "source"
    info = ctx.index.get("video") or {}
    sw = float(info.get("width") or 0) or 1920.0
    sh = float(info.get("height") or 0) or 1080.0
    if ratio == "source":
        out_aspect = sw / sh
    else:
        W, H = renderer.frame_dims(sw, sh, ratio)
        out_aspect = W / float(H)
    a, o = float(asset_aspect), float(out_aspect)
    mode = fr.get("mode") or "crop"
    if mode == "crop":                          # cover: center-crop overflow
        if a > o:                               # sides cropped
            vis = o / a
            ox = (x - (1.0 - vis) / 2.0) / vis
            oy = y
        else:                                   # top/bottom cropped
            vis = a / o
            ox = x
            oy = (y - (1.0 - vis) / 2.0) / vis
    else:                                       # pad / pad_blur: fit + bars
        if a > o:                               # bars above/below
            vis = o / a
            ox = x
            oy = y * vis + (1.0 - vis) / 2.0
        else:                                   # bars left/right
            vis = a / o
            ox = x * vis + (1.0 - vis) / 2.0
            oy = y
    if not (-0.02 <= ox <= 1.02 and -0.02 <= oy <= 1.02):
        return None
    return min(max(ox, 0.0), 1.0), min(max(oy, 0.0), 1.0)


def _src_frac_from_out(ctx, edl, ox, oy):
    """Inverse of _out_frac_from_source: an OUTPUT fraction back into the
    SOURCE frame's fractions. The tracker measures on the source/proxy pixels,
    but the pin's quad lives in output fractions — corners have to make the
    round trip through the same reframe arithmetic in both directions."""
    fr = edl.get("frame") or {}
    ratio = fr.get("ratio") or "source"
    if ratio == "source":
        return ox, oy
    info = ctx.index.get("video") or {}
    sw = float(info.get("width") or 0) or 1920.0
    sh = float(info.get("height") or 0) or 1080.0
    W, H = renderer.frame_dims(sw, sh, ratio)
    mode = fr.get("mode") or "crop"
    if mode == "crop":
        s = max(W / sw, H / sh)
        scaled_w, scaled_h = sw * s, sh * s
        fx = fr.get("focus_x")
        fy = fr.get("focus_y")
        fx = 0.5 if fx is None else float(fx)
        fy = 0.5 if fy is None else float(fy)
        x0 = min(max(scaled_w * fx - W / 2.0, 0.0), scaled_w - W)
        y0 = min(max(scaled_h * fy - H / 2.0, 0.0), scaled_h - H)
        return (ox * W + x0) / scaled_w, (oy * H + y0) / scaled_h
    s = min(W / sw, H / sh)
    scaled_w, scaled_h = sw * s, sh * s
    return ((ox * W - (W - scaled_w) / 2.0) / scaled_w,
            (oy * H - (H - scaled_h) / 2.0) / scaled_h)


def _asset_frac_from_out(ctx, edl, asset_aspect, ox, oy):
    """Inverse of _out_frac_from_insert: an OUTPUT fraction back into an
    inserted clip's own frame fractions."""
    fr = edl.get("frame") or {}
    ratio = fr.get("ratio") or "source"
    info = ctx.index.get("video") or {}
    sw = float(info.get("width") or 0) or 1920.0
    sh = float(info.get("height") or 0) or 1080.0
    if ratio == "source":
        out_aspect = sw / sh
    else:
        W, H = renderer.frame_dims(sw, sh, ratio)
        out_aspect = W / float(H)
    a, o = float(asset_aspect), float(out_aspect)
    mode = fr.get("mode") or "crop"
    if mode == "crop":
        if a > o:
            vis = o / a
            return ox * vis + (1.0 - vis) / 2.0, oy
        vis = a / o
        return ox, oy * vis + (1.0 - vis) / 2.0
    if a > o:
        vis = o / a
        return ox, (oy - (1.0 - vis) / 2.0) / vis
    vis = a / o
    return (ox - (1.0 - vis) / 2.0) / vis, oy


def _track_screen_path(ctx, edl, quad_out, start, dur, host):
    """Follow the screen through the push window so the pin can ride it.

    Returns (corner_path, arrival_quad, note):
      corner_path — [[t_rel, x0..y3], ...] in OUTPUT fractions, or None when
                    the static pin stands (tripod shot, track failed, no
                    executor for an original, a cut/ramp inside the window);
      arrival_quad — the tracked quad at the window's END (what the camera
                    geometry should aim at), or None with corner_path;
      note — one sentence for the tool reply saying what happened.

    The track runs where the pixels are cheap to decode: the executor for an
    inserted clip (its only copy is the user's original — the OOM class), the
    proxy for main footage (proxy-class work, allowed locally only as the
    no-executor fallback).
    """
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    hand = start + dur
    if host is not None:
        h_ins, w0, _w1 = host
        asset, err = _resolve_media_asset(ctx, h_ins["asset_key"],
                                          ("video_clip",))
        if err:
            return None, None, "kept a static pin (could not open the clip)"
        if not remote.track_available():
            return (None, None,
                    "kept a static pin — tracking the screen means decoding "
                    "the clip's original, which needs the executor")
        aw = float(asset.get("width") or 0)
        ah = float(asset.get("height") or 0)
        if not (aw > 0 and ah > 0):
            dims = _ensure_asset_dims(ctx, asset)
            if dims:
                aw, ah = dims * 1000.0, 1000.0
        if not (aw > 0 and ah > 0):
            return None, None, ("kept a static pin (the clip's frame shape "
                                "is unknown)")
        a0 = float(h_ins.get("source_start_s") or 0.0) + (start - w0)
        to_track = lambda x, y: _asset_frac_from_out(ctx, edl, aw / ah, x, y)
        to_out = lambda x, y: _out_frac_from_insert(ctx, edl, aw / ah, x, y)
        key = asset["storage_key"]
        local = None
    else:
        a_src = tl.out_to_src(start)
        b_src = tl.out_to_src(max(start, hand - 0.02))
        if a_src is None or b_src is None:
            return None, None, "kept a static pin (window off the footage)"
        ra, rb = tl.seg_program_range(a_src), tl.seg_program_range(b_src)
        if ra is None or rb is None or abs(ra[0] - rb[0]) > 0.001:
            return None, None, ("kept a static pin — there is a cut inside "
                                "the push window, so the screen is not one "
                                "continuous shot there")
        for sp in (edl.get("speed") or []):
            if float(sp["end"]) > a_src and float(sp["start"]) < b_src:
                return None, None, ("kept a static pin — a speed ramp covers "
                                    "the window and the track is "
                                    "frame-for-frame with the source")
        a0 = a_src
        to_track = lambda x, y: _src_frac_from_out(ctx, edl, x, y)
        to_out = lambda x, y: _out_frac_from_source(ctx, edl, x, y)
        proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
        key = proxy["storage_key"] if proxy else None
        local = None
        if not remote.track_available():
            try:
                local = ctx.proxy_path()
            except Exception:
                return None, None, "kept a static pin (no proxy to track on)"

    tq = []
    for i in range(4):
        pt = to_track(quad_out[2 * i], quad_out[2 * i + 1])
        tq.extend([float(pt[0]), float(pt[1])])
    try:
        if local is not None:
            quads, quality = tracker.track_quad(local, a0, dur, tq)
        else:
            if not key:
                return None, None, "kept a static pin (no source object)"
            res = remote.run_track_remote(
                ctx.project_id, {"storage_key": key, "start": round(a0, 3),
                                 "dur": round(dur, 3), "corners": tq})
            quads, quality = (res or {}).get("quads"), \
                (res or {}).get("quality") or {}
    except Exception as e:
        return None, None, (f"kept a static pin (tracking failed: "
                            f"{str(e)[:120]})")
    if not quads:
        why = (quality or {}).get("why") or "the track was not usable"
        return None, None, f"kept a static pin — {why}"

    path = []
    for entry in quads:
        t_rel = float(entry[0])
        q = []
        for i in range(4):
            pt = to_out(entry[1 + 2 * i], entry[2 + 2 * i])
            if pt is None:
                return None, None, ("kept a static pin — the tracked screen "
                                    "drifts outside the output frame during "
                                    "the window")
            q.extend([round(pt[0], 5), round(pt[1], 5)])
        ok, _why = quad_is_sane(q)
        if not ok:
            return None, None, "kept a static pin — the tracked quad folded"
        path.append([round(t_rel, 3)] + q)
    if path[-1][0] < dur - 0.34:
        # the track must reach (nearly) the arrival frame, or the pin would
        # lerp-hold a stale quad through the handoff
        return None, None, ("kept a static pin — the track ends "
                            f"{dur - path[-1][0]:.2f}s short of the arrival")
    arrival = path[-1][1:]
    _ax, _ay, aw_, ah_ = quad_bbox(arrival)
    if aw_ < SCREEN_QUAD_MIN_FRAC or ah_ < SCREEN_QUAD_MIN_FRAC:
        return None, None, ("kept a static pin — the screen shrinks below "
                            "usable size by the arrival frame")
    note = (f"the pin TRACKS the screen through the window "
            f"({quality.get('samples')} samples, "
            f"{quality.get('alive', 0) * 100:.0f}% of features held, "
            f"max drift {quality.get('max_excursion_frac', 0) * 100:.1f}% "
            "of the frame) — the content stays glued to the glass even as "
            "the shot wobbles")
    return path, arrival, note


def _quad_plausible_for(quad, frame_aspect, content_aspect):
    """Could this rectangle be a SCREEN showing that content?

    The wrong-pin bug this guards (round 62, project 246): the bright
    detector latched a tall shelf beside the laptop — 0.34x0.66 of the
    frame, PORTRAIT — in a shot whose takeover content was a LANDSCAPE Mac
    recording, scored 0.66 confidence, and the push flattened the recording
    onto the furniture while the actual laptop screen sat untouched. The
    contradiction was checkable and never checked: perspective
    foreshortening SHRINKS one axis (an angled landscape screen reads
    narrower, never taller than wide), so a measured region whose aspect is
    below ~0.65x the content's — or wildly above it — is not the screen the
    content belongs on. Returns (ok, why_not). quad is fractions of a frame
    whose width:height is frame_aspect.
    """
    if not content_aspect:
        return True, ""
    qx, qy, qw, qh = quad_bbox(quad)
    if qh <= 1e-6:
        return False, "the region has no height"
    qa = (qw / qh) * float(frame_aspect)
    if qa < 0.65 * content_aspect:
        return False, (f"the region is {qa:.2f}:1 — taller and narrower "
                       f"than the {content_aspect:.2f}:1 content could ever "
                       "look on a screen (an angle narrows a screen; it "
                       "does not turn it portrait)")
    if qa > 2.0 * content_aspect:
        return False, (f"the region is {qa:.2f}:1 — far wider than the "
                       f"{content_aspect:.2f}:1 content")
    return True, ""


def _asset_aspect(ctx, asset):
    """width/height of an asset when the probe has recorded them, else None.
    Never downloads — this feeds a sanity check, not a measurement."""
    try:
        aw = float(asset.get("width") or 0)
        ah = float(asset.get("height") or 0)
        return (aw / ah) if aw > 0 and ah > 0 else None
    except (TypeError, ValueError):
        return None


def _output_aspect(ctx, edl):
    canvas = edl.get("canvas") or {}
    try:
        if canvas.get("width") and canvas.get("height"):
            return float(canvas["width"]) / float(canvas["height"])
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    ratio = ((edl.get("frame") or {}).get("ratio") or "source")
    if ratio != "source" and ":" in str(ratio):
        try:
            rw, rh = (float(x) for x in str(ratio).split(":"))
            return rw / rh
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    video = (getattr(ctx, "index", None) or {}).get("video") or {}
    try:
        return float(video["width"]) / float(video["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _ensure_asset_dims(ctx, asset):
    """The asset's frame aspect, measured once and persisted.

    Browser uploads record duration only — in prod every clip row had NULL
    width/height — so the aspect that feeds _quad_plausible_for has to come
    from a probe. Done on the EXECUTOR when one is configured (a one-frame
    frames job whose probe dims ride back), because the only copy of a clip
    is the user's original. Returns the aspect or None; None just disables
    the plausibility check, it never blocks the takeover.
    """
    a = _asset_aspect(ctx, asset)
    if a:
        return a
    if asset.get("kind") != "video_clip":
        return None
    got = None
    if remote.frames_available():
        try:
            got = remote.run_frames_remote(
                ctx.project_id,
                {"storage_key": asset["storage_key"], "times": [0.1],
                 "width": 64},
                user_id=ctx.job.get("user_id")) or {}
        except Exception:
            return None
        try:
            storage.delete_keys([k for k in (got.get("keys") or []) if k])
        except Exception:
            pass
    else:
        try:
            info = media.probe(_asset_local_path(ctx, asset))
        except Exception:
            return None
        got = {"duration_s": info.get("duration"),
               "width": info.get("width"), "height": info.get("height"),
               "fps": info.get("fps")}
    try:
        w_ = float(got.get("width") or 0)
        h_ = float(got.get("height") or 0)
        if w_ > 0 and h_ > 0:
            ctx.db.run(dbx.update_asset_probe, asset["id"],
                       got.get("duration_s") or asset.get("duration_s"),
                       int(w_), int(h_), got.get("fps"),
                       asset.get("sha256"))
            asset["width"], asset["height"] = w_, h_
            return w_ / h_
    except Exception:
        pass
    return None


def _takeover_content(ctx, asset, off, dur, want_frames):
    """What the pinned CONTENT is, for the round-65 guided lock: the asset's
    key/kind plus three spread sample times (any moment works — the match
    anchors on UI chrome that never moves). Local frame paths are fetched
    only when want_frames (no executor to run the match on, or the main-
    footage path that refines locally). Best effort: a dict with empty
    frames just narrows what the refinement can try.
    """
    out = {"key": asset.get("storage_key"), "kind":
           ("image" if asset.get("kind") == "image_ref" else "video"),
           "times": [0.0], "frames": []}
    try:
        off = float(off or 0.0)
        dur = float(dur or 1.2)
        if out["kind"] == "image":
            if want_frames:
                out["frames"] = [_asset_local_path(ctx, asset)]
            return out
        clip_dur = _asset_media_duration(ctx, asset)
        hi = max(0.05, clip_dur - 0.1)
        out["times"] = sorted({round(min(max(t, 0.0), hi), 2) for t in
                               (off, off + dur, off + dur + 3.0)})
        if want_frames:
            pairs, _err = _asset_frames(ctx, asset, out["times"], width=960,
                                        tag="smtch")
            out["frames"] = [fp for _, fp in pairs]
    except Exception:
        pass
    return out


def _refine_read_with_content(ctx, frames, content, quad,
                              remote_spec=None):
    """Round 65c/d: a vision read LOCATES the glass; the content's pixels
    then NAIL it (screenmatch.refine_with_read). WHERE it runs is the
    round-61 rule: with remote_spec and an executor, the match runs there on
    2048px frames of the originals — SIFT at that size OOM-killed the
    dispatcher the one time it ran locally (job 1513) — and a remote failure
    NEVER falls back to heavy local work. Without an executor, the local
    small-frame refine is the safe best effort. None = the read stands.
    """
    if remote_spec is not None and remote.smatch_available():
        try:
            got = remote.run_smatch_remote(
                ctx.project_id,
                dict(remote_spec,
                     read_quad=[round(float(v), 4) for v in quad]),
                user_id=ctx.job.get("user_id"))
        except Exception:
            return None
        m = (got or {}).get("match")
        if not m:
            return None
        return {"corners": m["corners"], "confidence": None,
                "method": "content_match", "inliers": m["inliers"],
                "agreement": m["agreement"], "n_frames": m["n_pairs"],
                "refined_from_read": True}
    got = None
    try:
        got = screenmatch.refine_with_read(
            frames, (content or {}).get("frames") or [], quad)
    except Exception:
        got = None
    if not got:
        return None
    return {"corners": got["corners"], "confidence": None,
            "method": "content_match", "inliers": got["inliers"],
            "agreement": got["agreement"], "n_frames": got["n_pairs"],
            "refined_from_read": True}


def _detect_screen_on_insert(ctx, edl, host, clip_t, content_aspect=None,
                             content=None):
    """_detect_screen's counterpart for a screen inside a SPLICED clip.

    The frames come from the insert's own asset — via the executor when one
    is configured (_asset_frames), because the only copy of a dropped-in clip
    is the user's original. Same voting, same vision fallback, same honest
    (corners, info) | (None, reason) contract; corners come back in OUTPUT
    fractions via the insert normalization mapping above.
    """
    asset, err = _resolve_media_asset(ctx, host["asset_key"], ("video_clip",))
    if err:
        return None, f"could not open the spliced clip ({err[:120]})"
    dur = _asset_media_duration(ctx, asset)
    times = [min(max(clip_t + off, 0.0), max(0.0, dur - 0.05))
             for off in (-0.4, 0.0, 0.4)]
    pairs, ferr = _asset_frames(ctx, asset, times, width=960, tag="sdet")
    frames = [fp for _, fp in pairs]
    if not frames:
        return None, (f"could not extract frames from the spliced clip "
                      f"({(ferr or 'unknown error')[:120]})")
    # The host clip's own frame shape — needed both to sanity-check the
    # measurement below and to map the corners into output fractions after.
    aw = float(asset.get("width") or 0)
    ah = float(asset.get("height") or 0)
    if not (aw > 0 and ah > 0):
        try:
            import cv2
            ih_, iw_ = cv2.imread(frames[0]).shape[:2]
            aw, ah = float(iw_), float(ih_)
        except Exception:
            return None, ("could not determine the spliced clip's frame "
                          "shape to place the corners")
    res = screendet.find_screen(frames)
    why = None
    if res.get("error"):
        why = res["error"]
    elif res["confidence"] < screendet.MIN_CONFIDENCE:
        why = (f"the best screen-shaped region scored only "
               f"{res['confidence']:.2f} confidence")
    if not why:
        ok_p, pwhy = _quad_plausible_for(res["corners"], aw / ah,
                                         content_aspect)
        if not ok_p:
            why = f"the measured rectangle cannot be the screen: {pwhy}"
    if why:
        quad, vwhy = _vision_screen_corners(ctx, frames[len(frames) // 2])
        if quad is None:
            return None, f"{why}, and {vwhy}"
        ok, sane = quad_is_sane(quad)
        if not ok:
            return None, (f"{why}, and the corners the vision model read are "
                          f"not a usable quadrilateral ({sane})")
        ok_p, pwhy = _quad_plausible_for(quad, aw / ah, content_aspect)
        if not ok_p:
            return None, (f"{why}, and the corners the vision model read "
                          f"cannot be the screen either — {pwhy}")
        # Round 65d: the guided lock needs SIGNAL — the 960px detection
        # frames carry ~350px of dark, compressed glass, below what SIFT can
        # grip — and it needs a BIG BOX: SIFT at 2048px OOM-killed the
        # dispatcher when it ran here. Both answered by the executor smatch
        # job, which stages the host clip's original and the recording and
        # matches hi-res frames there.
        rs = None
        if content and content.get("key"):
            rs = {"filmed_key": asset["storage_key"],
                  "filmed_times": [round(max(0.0, clip_t - 0.2), 2),
                                   round(clip_t, 2)],
                  "content_key": content["key"],
                  "content_times": content.get("times") or [0.0],
                  "content_kind": content.get("kind") or "video"}
        res = (_refine_read_with_content(ctx, frames, content, quad,
                                         remote_spec=rs)
               or {"corners": quad, "confidence": None, "method": "vision",
                   "agreement": 1, "n_frames": 1, "read_not_measured": why})
    # The measured fractions are of the ASSET's frame; the pin consumes
    # OUTPUT fractions.
    out = []
    for i in range(4):
        pt = _out_frac_from_insert(ctx, edl, aw / ah,
                                   res["corners"][2 * i],
                                   res["corners"][2 * i + 1])
        if pt is None:
            return None, ("the screen in the spliced clip falls outside the "
                          "output frame — this project's reframe crops it "
                          "away")
        out.extend([round(pt[0], 4), round(pt[1], 4)])
    return out, res


_SCREEN_VISION_PROMPT = (
    "This frame is from a video. Somewhere in it there is a DEVICE SCREEN "
    "being filmed — a laptop, a monitor, a phone, a tablet, a TV. I need its "
    "four corners so I can pin a video onto the glass.\n"
    "Give me the corners of the SCREEN ITSELF — the lit display area, inside "
    "the bezel — not the whole laptop, not the whole phone body.\n"
    "Reply with ONLY a JSON array of 8 numbers, fractions of the frame from "
    "the TOP-LEFT corner (0-1), in this exact order:\n"
    "[top_left_x, top_left_y, top_right_x, top_right_y, "
    "bottom_left_x, bottom_left_y, bottom_right_x, bottom_right_y]\n"
    "The screen is usually seen at an angle, so the four corners rarely form "
    "a rectangle — give me where each corner ACTUALLY is. If there is no "
    "device screen in this frame, reply exactly: none")


def _vision_screen_corners(ctx, frame_path):
    """Read a screen's four corners off ONE frame with the vision model.

    The fallback for when the geometric detector declines (screendet refuses
    rather than guessing, which is right — a corner 2% out slides visibly once
    the push magnifies it). What it declines on is real footage: a screen with
    dark content on it, a bezel that blends into a dark desk, a hand across a
    corner.

    Before this, that refusal ended the tool call and told the AGENT to go and
    do exactly this — call look_at, read the corners, pass them back. That is
    two more round trips at ~13 seconds each, in the middle of a request the
    user described in one sentence, and the model frequently gave up and
    offered a plain cut instead. Doing the same work in the same call is
    strictly better, as long as the reply never CLAIMS a measurement: the
    caller reports "read" instead of "measured", and every sanity check the
    measured path runs still runs here.

    Returns (corners_in_source_fractions, note) or (None, reason).
    """
    if not llm.vision_available():
        return None, ("visual inspection is unavailable in this deployment, "
                      "so there is no second way to find it")
    answer = llm.ask_vision(_SCREEN_VISION_PROMPT, [frame_path],
                            purpose="vision_screen",
                            image_names=["screen corner read"])
    if not answer:
        return None, "the vision model did not answer"
    txt = str(answer).strip()
    if txt.lower().startswith("none"):
        return None, "the vision model says there is no device screen in it"
    m = re.search(r"\[[^\]]*\]", txt, re.S)
    if not m:
        return None, "the vision model's answer was not a list of corners"
    try:
        nums = [float(x) for x in json.loads(m.group(0))]
    except (ValueError, TypeError):
        return None, "the vision model's corners did not parse as numbers"
    if len(nums) != 8:
        return None, (f"the vision model returned {len(nums)} numbers, not the "
                      "8 a quadrilateral needs")
    if any(not (-0.05 <= v <= 1.05) for v in nums):
        return None, "the vision model's corners fall outside the frame"
    q = [min(max(v, 0.0), 1.0) for v in nums]
    # ORDER, which is the one mistake a language model makes here that geometry
    # cannot catch. The storage order is TL, TR, BL, BR; a model that answers
    # clockwise (TL, TR, BR, BL) or bottom-row-first hands back a quad that is
    # still convex and still consistently wound, so quad_is_sane passes it and
    # the content is pinned mirrored or upside down onto the glass. Rows and
    # columns are un-swapped by their own coordinates, which is unambiguous for
    # any screen a person could film.
    tl, tr, bl, br = q[0:2], q[2:4], q[4:6], q[6:8]
    if (tl[1] + tr[1]) > (bl[1] + br[1]) + 0.04:
        tl, tr, bl, br = bl, br, tl, tr           # bottom row given first
    if (tl[0] + bl[0]) > (tr[0] + br[0]) + 0.04:
        tl, tr, bl, br = tr, tl, br, bl           # right column given first
    return [*tl, *tr, *bl, *br], None


def _detect_screen(ctx, edl, src_t, content_aspect=None, content=None):
    """Measure the device screen around SOURCE second src_t. Returns
    (corners_in_output_fractions, info_dict) or (None, reason)."""
    try:
        path = ctx.proxy_path()
    except Exception:
        try:
            path = _original_local(ctx)
        except Exception as e:
            return None, (f"could not open the footage to look at it "
                          f"({str(e)[:120]})")
    dur = float(ctx.duration or 0.0)
    # Three frames spread over a second of the same shot: one frame can catch
    # a glare flash or a hand crossing the bezel, and how well the three AGREE
    # is the only honest confidence signal there is.
    offsets = [-0.4, 0.0, 0.4]
    frames = []
    for i, off in enumerate(offsets):
        t = min(max(src_t + off, 0.0), max(0.0, dur - 0.05))
        fp = os.path.join(ctx.workdir, f"screendet_{i}_{int(t * 100)}.jpg")
        try:
            media.frame_at(path, t, fp)
        except Exception:
            continue
        frames.append(fp)
    if not frames:
        return None, "could not extract frames from the footage at that moment"
    info = ctx.index.get("video") or {}
    src_aspect = ((float(info.get("width") or 0) or 1920.0)
                  / (float(info.get("height") or 0) or 1080.0))
    res = screendet.find_screen(frames)
    why = None
    if res.get("error"):
        why = res["error"]
    elif res["confidence"] < screendet.MIN_CONFIDENCE:
        why = (f"the best screen-shaped region scored only "
               f"{res['confidence']:.2f} confidence")
    if not why:
        # A confident measurement can still be the WRONG rectangle — a bright
        # doorway, a poster, a window. The content about to be pinned says
        # what shape the screen has to be; a region that contradicts it is
        # treated exactly like a low-confidence one and handed to the read.
        ok_p, pwhy = _quad_plausible_for(res["corners"], src_aspect,
                                         content_aspect)
        if not ok_p:
            why = f"the measured rectangle cannot be the screen: {pwhy}"
    if why:
        # The pixels declined. Ask the vision model to read the corners off the
        # middle frame rather than ending the call and asking the AGENT to do
        # the same thing over two more 13-second round trips.
        quad, vwhy = _vision_screen_corners(ctx, frames[len(frames) // 2])
        if quad is None:
            return None, f"{why}, and {vwhy}"
        ok, sane = quad_is_sane(quad)
        if not ok:
            return None, (f"{why}, and the corners the vision model read are "
                          f"not a usable quadrilateral ({sane})")
        ok_p, pwhy = _quad_plausible_for(quad, src_aspect, content_aspect)
        if not ok_p:
            return None, (f"{why}, and the corners the vision model read "
                          f"cannot be the screen either — {pwhy}")
        # Main-footage path: the filmed source is the user's ORIGINAL —
        # gigabytes — so no executor staging; the local small-frame refine
        # (proxy-grade, dispatcher-safe) is the honest best effort.
        res = (_refine_read_with_content(ctx, frames, content, quad)
               or {"corners": quad, "confidence": None, "method": "vision",
                   "agreement": 1, "n_frames": 1, "read_not_measured": why})
    out = []
    for i in range(4):
        pt = _out_frac_from_source(ctx, edl, res["corners"][2 * i],
                                   res["corners"][2 * i + 1])
        if pt is None:
            return None, ("the screen I found is outside the output frame — "
                          "this project is reframed, and the screen has been "
                          "cropped away")
        out.extend([round(pt[0], 4), round(pt[1], 4)])
    return out, res


def add_screen_takeover(ctx, asset_key, at_output_s, duration_s=None,
                        corners=None, clip_start_s=None, hold_s=None,
                        push=None, ease=None, settle=None,
                        allow_repeat=False):
    """Push into a device screen in the footage and let an asset playing ON
    that screen become the whole video, in one continuous move.

    Round 74: calling this again at an arrival where a takeover of the same
    asset already lands REPLACES that takeover. There is no edit tool for a
    takeover, so "make the transition flat/smooth/shorter" used to mean
    remove + re-add — two writes, and a re-detection that can come back
    WORSE than the pin it replaced (a real session re-measured a good
    0.30-wide laptop trapezoid into a 0.10-wide bright patch at 0.57
    confidence, 1 of 3 frames agreeing). On replace, parameters not passed
    are INHERITED from the existing takeover, and its already-accepted pin
    corners are REUSED instead of re-measured unless new `corners` are
    given. That is why every keyword here defaults to None: None means
    "keep what the takeover has" on a replace and the documented default on
    a fresh add."""
    asset, err = _resolve_media_asset(ctx, asset_key,
                                      ("video_clip", "image_ref"))
    if err:
        return err
    kind = "image" if asset["kind"] == "image_ref" else "video"
    name = (asset.get("meta") or {}).get("filename") or \
        os.path.basename(asset_key)
    if not ctx.has_main_video:
        return ("REJECTED: a screen takeover pushes into a screen that is IN "
                "the footage, and this project has no main video to push "
                "into. Place the shot of the device first.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        at = round(min(max(float(at_output_s), 0.0), prog), 2)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — the moment in the "
                "FINAL edited video where the takeover FINISHES and the asset "
                "is full screen, in seconds.")
    # Same arrival + same asset = the SAME transition, re-parameterised.
    prev_tk = next((o for o in (edl.get("overlays") or [])
                    if o.get("screen") and o.get("asset_key") == asset_key
                    and abs(float(o["start"]) + float(o["duration_s"]) - at)
                    < 0.3), None)
    replace_id = None
    if prev_tk is not None:
        replace_id = prev_tk.get("id")
        prev_scr = dict(prev_tk.get("screen") or {})
        if duration_s is None:
            duration_s = float(prev_tk["duration_s"])
        if ease is None:
            ease = prev_scr.get("ease")
        if push is None:
            push = prev_scr.get("push")
        if settle is None:
            settle = prev_scr.get("land") is not False
    # A takeover deliberately uses the same asset as its destination insert:
    # the overlay pins the opening of that clip onto the device, then hands
    # off to the full-screen inserted clip at the arrival frame. That is one
    # continuous use, not repeated B-roll. Ignore those destination inserts
    # here; ordinary insert/overlay tools still reject real reuse globally.
    continuity_ids = [i.get("id") for i in (edl.get("inserts") or [])
                      if i.get("asset_key") == asset_key]
    if prev_tk:
        continuity_ids.append(prev_tk.get("id"))
    try:
        dur = round(float(duration_s if duration_s is not None else 1.2), 2)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds."
    if not (SCREEN_TAKEOVER_MIN_S <= dur <= SCREEN_TAKEOVER_MAX_S):
        return (f"REJECTED: duration_s must be "
                f"{SCREEN_TAKEOVER_MIN_S}-{SCREEN_TAKEOVER_MAX_S}s. Under "
                "half a second the push reads as a cut; over five it stalls. "
                "1.0-1.5s is the move people mean.")
    es = str(ease or "smooth").strip().lower()
    if es not in ("smooth", "accelerate", "linear"):
        return ("REJECTED: ease must be 'smooth' (default — eases in and out, "
                "right nearly always), 'accelerate' (dives into the screen) "
                "or 'linear'.")
    try:
        pu = round(min(max(float(push if push is not None else 1.0), 0.0),
                       1.0), 3)
    except (TypeError, ValueError):
        return "REJECTED: push must be a number 0-1."
    if at - dur < 0.05:
        return (f"REJECTED: the takeover needs {dur}s of footage BEFORE "
                f"{at}s to push through, and there is only {at}s of program "
                "there. Move at_output_s later or shorten duration_s.")

    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    # Detect on the footage that is on screen at the MIDDLE of the push: the
    # start of the window is where the screen is smallest and hardest to
    # measure, and the end is where the content has already covered it.
    probe_out = max(0.0, at - dur * 0.5)
    src_t = tl.out_to_src(probe_out)
    host = None          # (insert, win_start, win_end) when the push rides one
    host_notes = []
    if src_t is None:
        # THE DEVICE SHOT CAN ITSELF BE A SPLICED-IN CLIP (round 62). A real
        # user's timeline was walk / laptop-shot clip / screen recording —
        # "transition into the laptop" — and this branch used to refuse
        # because the laptop lived in an insert. The renderer never cared:
        # the push is a program-time zoom term over whatever picture is there
        # and the pin overlays the composited stream. Only detection (frames
        # must come from the insert's own asset) and placement (the handoff
        # lands at the insert's boundary, after it in list order) are
        # insert-aware, and both are handled here.
        wins0 = insert_windows(edl.get("inserts") or [], tl)
        for cand in (edl.get("inserts") or []):
            wsp = wins0.get(cand.get("id"))
            if wsp and wsp[0] - 1e-6 <= probe_out <= wsp[1] + 1e-6:
                host = (cand, wsp[0], wsp[1])
                break
        if host is None or host[0].get("kind") != "video":
            return (f"REJECTED: {round(probe_out, 2)}s of the program is "
                    "inside a spliced-in still image — there is no moving "
                    "shot of a device there to push into. Point at_output_s "
                    "at footage or at a spliced video clip that shows the "
                    "device.")
        h_ins, w0, w1 = host
        if w1 - w0 < SCREEN_TAKEOVER_MIN_S + 0.1:
            return (f"REJECTED: the spliced clip under {round(probe_out, 2)}s "
                    f"is only {round(w1 - w0, 2)}s long — too short to push "
                    "through. Lengthen it (set_insert_window) or push into "
                    "the main footage instead.")
        # The handoff must land where the host clip ends — that is the only
        # boundary the content can take over at without cutting the host
        # short — and the push rides the host's own tail to get there.
        if dur > w1 - w0 - 0.05:
            dur = round(max(SCREEN_TAKEOVER_MIN_S, w1 - w0 - 0.05), 2)
            host_notes.append(f"shortened the push to {dur}s so it fits "
                              "inside the spliced clip")
        if abs(at - w1) > 0.01:
            host_notes.append(f"moved the arrival from {at}s to {round(w1, 2)}s "
                              "— the end of the spliced device shot, which is "
                              "where the content can take over")
            at = round(w1, 2)
        probe_out = max(0.0, at - dur * 0.5)

    detected = None
    reused_pin = False
    if corners is not None:
        quad, cerr = _parse_screen_corners(corners)
        if cerr:
            return cerr
    elif prev_tk is not None and (prev_tk.get("screen") or {}).get("corners"):
        # Replace: the pin geometry the user has already seen and accepted
        # beats a fresh measurement of the same shot.
        quad = [round(float(v), 4)
                for v in (prev_tk.get("screen") or {})["corners"]]
        reused_pin = True
    elif host is not None:
        h_ins, w0, w1 = host
        clip_t = float(h_ins.get("source_start_s") or 0.0) + (probe_out - w0)
        quad, why = _detect_screen_on_insert(
            ctx, edl, h_ins, clip_t,
            content_aspect=_ensure_asset_dims(ctx, asset),
            content=_takeover_content(
                ctx, asset, clip_start_s, dur,
                want_frames=not remote.smatch_available()))
        if quad is None:
            return (f"REJECTED: I could not find a screen in the spliced clip "
                    f"at {round(probe_out, 2)}s — {why}. BOTH ways were "
                    "tried: measuring it from the pixels and reading it with "
                    "the vision model. I will not guess a rectangle — the "
                    "corners are the whole effect. Check the moment "
                    "(look_at_asset on that clip — is the device actually "
                    "visible and big enough there?), or ask the user for the "
                    "screen's four corners and pass them as `corners` "
                    "(8 numbers, fractions of the frame: top-left, top-right, "
                    "BOTTOM-LEFT, bottom-right).")
        detected = why
    else:
        quad, why = _detect_screen(ctx, edl, src_t,
                                   content_aspect=_ensure_asset_dims(ctx,
                                                                     asset),
                                   content=_takeover_content(
                                       ctx, asset, clip_start_s, dur,
                                       want_frames=True))
        if quad is None:
            return (f"REJECTED: I could not find a screen in the frame at "
                    f"{round(probe_out, 2)}s — {why}. BOTH ways were tried: "
                    "measuring it from the pixels and reading it with the "
                    "vision model. I will not guess a rectangle — the corners "
                    "are the whole effect, and one that is 2% out slides "
                    "visibly once the push magnifies it. So: check the moment "
                    "is right (look_at that timestamp — is the device actually "
                    "on screen there, and big enough?), or ask the user where "
                    "the screen's four corners are and pass them as `corners` "
                    "(8 numbers, fractions of the frame: top-left, top-right, "
                    "BOTTOM-LEFT, bottom-right).")
        detected = why

    ok, why = quad_is_sane(quad)
    if not ok:
        return (f"REJECTED: those corners do not form a usable quadrilateral "
                f"— {why}. The order is top-left, top-right, BOTTOM-LEFT, "
                "bottom-right.")
    qx, qy, qw, qh = quad_bbox(quad)
    if qw < SCREEN_QUAD_MIN_FRAC or qh < SCREEN_QUAD_MIN_FRAC:
        return (f"REJECTED: that screen is {qw:.2f}x{qh:.2f} of the frame. "
                f"Pushing into something under {SCREEN_QUAD_MIN_FRAC} of the "
                "frame means blowing the shot up more than 12x, which is "
                "mush by the time it arrives. Start the takeover from a "
                "closer shot of the screen, or use insert_media for a plain "
                "cut to the clip.")

    # The asset must have enough footage to cover the push AND still be worth
    # cutting to. Its source time runs continuously across the handoff, which
    # is what makes the handoff invisible.
    off = 0.0
    if kind == "video":
        clip_dur = _asset_media_duration(ctx, asset)
        if clip_start_s is not None:
            try:
                off = round(max(0.0, float(clip_start_s)), 2)
            except (TypeError, ValueError):
                return ("REJECTED: clip_start_s must be a number of seconds "
                        "— where in the asset the takeover starts playing.")
        if off + dur >= clip_dur - 0.05:
            return (f"REJECTED: '{name}' is {clip_dur:.1f}s long and the "
                    f"takeover alone would consume {off + dur:.1f}s of it, "
                    "leaving nothing to cut to. Use a longer clip or a "
                    "shorter duration_s.")
    try:
        hold = round(max(0.0, float(hold_s)), 2) if hold_s is not None else None
    except (TypeError, ValueError):
        return "REJECTED: hold_s must be a number of seconds."

    # ── 0. a clip ALREADY placed at the arrival point IS the handoff ────────
    # (round 62) On the timeline this was built for — walk / device-shot clip /
    # screen recording — the content that takes over is usually already
    # sitting right after the device shot, placed by the user or an earlier
    # edit. Splicing a fresh copy would land AFTER it in list order and the
    # push would arrive on the wrong clip. So when an insert already starts at
    # the arrival frame, the takeover adopts it: the pin plays the footage of
    # that same asset that ENDS exactly on the frame the placed clip opens
    # with, and nothing is duplicated.
    adopt = None
    if host is not None:
        h_ins, w0, w1 = host
        wins_now = insert_windows(edl.get("inserts") or [], tl)
        for cand in (edl.get("inserts") or []):
            if cand.get("id") == h_ins.get("id"):
                continue
            wsp = wins_now.get(cand.get("id"))
            if wsp and abs(wsp[0] - w1) < 1e-6:
                adopt = cand
                break
        if adopt is not None and (adopt.get("asset_key") != asset_key
                                  or adopt.get("kind") != "video"
                                  or kind != "video"):
            return (f"REJECTED: a different clip already plays at "
                    f"{round(w1, 2)}s, right where this takeover would arrive "
                    f"[{adopt.get('id')}]. The push must land on the clip "
                    "that actually plays there — call again with THAT "
                    f"asset_key, or remove_insert('{adopt.get('id')}') "
                    "first.")

    adopt_notes = []
    if adopt is not None:
        inserts2 = [dict(i) for i in (edl.get("inserts") or [])]
        tgt = next(i for i in inserts2 if i.get("id") == adopt.get("id"))
        s2 = float(tgt.get("source_start_s") or 0.0)
        if clip_start_s is not None and abs((off + dur) - s2) > 0.05:
            adopt_notes.append(
                "ignored clip_start_s — the pin has to end on the exact "
                "frame the already-placed clip starts on")
        if hold_s is not None:
            adopt_notes.append("ignored hold_s — the placed clip keeps its "
                               "own length")
        if s2 >= dur - 0.01:
            # Enough of the asset exists before the placed clip's own start:
            # the pin plays the run-up and the placed clip is untouched.
            off = round(max(0.0, s2 - dur), 2)
        elif s2 >= SCREEN_TAKEOVER_MIN_S:
            adopt_notes.append(
                f"shortened the push to {round(s2, 2)}s — that is all the "
                "footage the clip has before the frame it already starts on")
            dur = round(s2, 2)
            off = 0.0
        else:
            # The placed clip starts at (nearly) the top of its source: the
            # pin has to consume the clip's own opening, playing it ON the
            # glass, and the full-screen part picks up where the pin ends.
            take = round(dur - s2, 2)
            newlen = round(float(tgt["duration_s"]) - take, 2)
            if newlen < 0.5:
                return (f"REJECTED: the placed clip [{tgt['id']}] is only "
                        f"{tgt['duration_s']}s long — a {dur}s push would "
                        "consume nearly all of it. Use a shorter duration_s.")
            off = 0.0
            tgt["source_start_s"] = round(dur, 3)
            tgt["duration_s"] = newlen
            adopt_notes.append(
                f"the clip's first {round(dur, 2)}s now plays ON the glass "
                f"during the push; its full-screen part follows for {newlen}s")
        edl["inserts"] = inserts2
        ins = tgt
        hand = round(w1, 2)
        start = round(hand - dur, 2)

        def _undo(reason):
            # Nothing has been written in adopt mode — a plain refusal IS the
            # rollback.
            return f"REJECTED: {reason} Nothing was changed."
    else:
        # ── 1. the handoff, placed FIRST ────────────────────────────────────
        # insert_media snaps to segment boundaries and to word edges, so where
        # the clip actually lands is not necessarily where it was asked to
        # land. The takeover is then built backwards from the position the
        # insert REALLY took — the pin's last frame and the clip's first frame
        # have to be the same frame, and a quarter-second snap nobody read
        # back would break exactly that.
        ins_dur = hold
        if ins_dur is None:
            ins_dur = (round(clip_dur - off - dur, 2)
                       if kind == "video" else 4.0)
        before_ids = {i.get("id") for i in (edl.get("inserts") or [])}
        before_keep = [list(k) for k in edl["keep"]]
        res = insert_media(ctx, asset_key, at, duration_s=ins_dur,
                           clip_start_s=(round(off + dur, 2)
                                         if kind == "video" else None))
        if not res.startswith("EDL v"):
            return (f"REJECTED: could not place the clip the takeover hands "
                    f"off to — {res}")
        edl = dict(ctx.latest_edl()["json"])

        def _undo(reason):
            """Put the edit back the way it was before the clip was spliced.

            insert_media has already written a version by this point, so a
            bare REJECTED here would leave the user with a clip cutting in
            cold and no push into it — a change they did not ask for, from a
            call that said it failed. The keep list goes back too, because
            insert_media may have SPLIT a take to land the clip mid-sentence.
            """
            back = dict(ctx.latest_edl()["json"])
            back["inserts"] = [i for i in (back.get("inserts") or [])
                               if i.get("id") in before_ids]
            back["keep"] = before_keep
            ctx.write_edl(back, "undid the handoff clip — the takeover could "
                                "not be built")
            return f"REJECTED: {reason} Nothing was changed."

        new_ids = {i.get("id") for i in (edl.get("inserts") or [])} \
            - before_ids
        if not new_ids:
            return _undo("the handoff clip did not survive the write.")
        # Diff the id sets rather than matching on asset_key: the same clip
        # may already be spliced in elsewhere, and picking "the last one with
        # this asset" would build the takeover around somebody else's insert.
        ins = next(i for i in edl["inserts"] if i.get("id") in new_ids)
        tl2 = Timeline(edl["keep"], edl.get("inserts") or [],
                       edl.get("speed") or [])
        windows = insert_windows(edl.get("inserts") or [], tl2)
        if ins["id"] not in windows:
            return _undo("I could not locate the clip on the timeline after "
                         "placing it.")
        hand = windows[ins["id"]][0]
        start = round(hand - dur, 2)
        if start < 0.0:
            return _undo(f"the clip snapped to the nearest cut at {hand}s, "
                         f"and a {dur}s push does not fit before it — move "
                         "at_output_s later or shorten duration_s.")

    # ── 2. the pin, built backwards from where the clip really landed ───────
    # Round 63: before the pin is written, follow the screen THROUGH the
    # window. The shot is almost always handheld, and a pin rigid at one
    # measured quad slides against the wobbling glass — the loudest "this is
    # an effect" tell the move has. Tracking failing for any reason keeps the
    # static pin, which is exactly what shipped before.
    corner_path, arrival, track_note = _track_screen_path(
        ctx, edl, quad, start, dur, host)
    if arrival:
        quad = [round(v, 4) for v in arrival]
    # Where the corners came from decides WHEN the content may appear on the
    # glass (renderer.screen_appear_window): matched corners mean the glass
    # is already showing this very content, so the pin can live on it from
    # the window's start; measured/read corners keep the late dissolve.
    if corners is not None:
        c_src = "user"
    elif reused_pin:
        # keep the provenance of the pin that was kept — a reused "matched"
        # pin keeps its content-on-glass-from-the-start behaviour.
        c_src = (prev_tk.get("screen") or {}).get("corners_source") \
            or "measured"
    elif detected and detected.get("method") == "content_match":
        c_src = "matched"
    elif detected and detected.get("read_not_measured"):
        c_src = "read"
    else:
        c_src = "measured"
    screen_spec = {"corners": quad, "push": pu, "ease": es,
                   "corners_source": c_src}
    if settle is False:
        # A dead-flat landing: full frame on the cut and STAYS there — no
        # through-cut overshoot. The default settle reads as cinematic
        # momentum to most, but a real user read it as "it zooms in the
        # third scene then returns" and asked for it gone.
        screen_spec["land"] = False
    if corner_path:
        screen_spec["corner_path"] = corner_path
    # replace_id: the takeover this call supersedes leaves in the same write
    # that lands its successor — never two pushes into one arrival.
    overlays = [dict(o) for o in (edl.get("overlays") or [])
                if o.get("id") != replace_id]
    item = {"id": _next_item_id(overlays, "tk"), "asset_key": asset_key,
            "kind": kind, "start": start, "duration_s": dur,
            "x": 0.5, "y": 0.5, "scale": 1.0, "fit": None, "opacity": None,
            "entrance": None, "exit": None,
            "source_start_s": off if kind == "video" else None,
            "screen": screen_spec}
    overlays.append(item)
    edl["overlays"] = overlays
    written = ctx.write_edl(
        edl, f"screen takeover: '{name}' pinned into the screen at "
             f"{start}-{hand}s and pushed to full frame [{item['id']}]")
    if not written.startswith("EDL v"):
        # The clip is already spliced at this point. Leaving it there would
        # hand the user a cut they never asked for from a call that reported
        # failure, so it goes back out before the rejection is returned.
        return _undo(f"the takeover would not validate — {written}")

    _cx, _cy, z_end = renderer.screen_lock_geometry(item["screen"])
    bits = [written]
    if replace_id:
        bits.append(
            f"REPLACED the takeover already arriving at {at}s "
            f"[{replace_id}] — same arrival, new parameters"
            + ("; its accepted pin corners were KEPT (pass corners=... to "
               "re-measure)" if reused_pin else
               "; its pin was re-set from the corners you passed") + ".")
    if track_note:
        bits.append(("TRACKED: " if corner_path else "") + track_note + ".")
    if host_notes:
        bits.append("Riding the spliced clip: " + "; ".join(host_notes) + ".")
    if adopt is not None:
        bits.append("Adopted the clip already placed at the arrival point "
                    f"[{ins['id']}] as the handoff — nothing was duplicated"
                    + ("; " + "; ".join(adopt_notes) if adopt_notes else "")
                    + ".")
    if detected and detected.get("method") == "content_match":
        bits.append(
            f"LOCKED TO THE CONTENT: the screen's corners come from finding "
            f"the recording's OWN pixels on the filmed glass "
            f"({detected['inliers']} feature matches agreeing, "
            f"{detected['agreement']} of {detected['n_frames']} frame pairs "
            f"concurring) — rotation and perspective are exact, and the "
            f"pinned clip grows out of the very pixels it was filmed "
            f"playing on. Because the glass already shows this content, the "
            f"clip lives on the screen from the window's start instead of "
            f"dissolving in late."
            + (" (A vision read located the glass first; the content lock "
               "then nailed the corners inside it.)"
               if detected.get("refined_from_read") else ""))
    elif detected and detected.get("read_not_measured"):
        # Honest about which of the two ways this happened. A vision read is an
        # estimate, and the whole effect lives or dies on the corners, so the
        # user is told to look at the join — never told it was measured.
        bits.append(
            f"NOTE: the corners here were READ off the frame by the vision "
            f"model, not measured from the pixels "
            f"({detected['read_not_measured']}). They occupy "
            f"{qw:.2f}x{qh:.2f} of the frame and form a sane quadrilateral, "
            f"but a corner a couple of percent out slides visibly once the "
            f"push magnifies it. Tell the user to watch the moment the picture "
            f"lands, and if the content sits off the glass, ask them where the "
            f"screen's corners are and pass them as `corners`.")
    elif detected:
        bits.append(
            f"The screen was MEASURED, not estimated: corners agreed across "
            f"{detected['agreement']} of {detected['n_frames']} sampled "
            f"frames at {detected['confidence']:.2f} confidence "
            f"({detected['method']} detector). It occupies "
            f"{qw:.2f}x{qh:.2f} of the frame.")
    if c_src == "matched":
        appear = ("The recording is already playing ON the glass — pinned to "
                  "its own filmed pixels — for the whole window, so nothing "
                  "'appears' at all; the camera simply travels into a screen "
                  "that was always showing it")
    else:
        appear = ("The glass shows what you actually filmed until the push "
                  "is nearly half done — only once the screen dominates the "
                  "frame does the clip DISSOLVE onto it (fully there before "
                  "the picture lands, so the swap happens where the room is "
                  "already gone from view)")
    # Say what the LANDING actually does. This sentence claimed the settle
    # unconditionally for a version after settle=false existed, so an agent
    # that had just turned the settle OFF read its own tool telling it the
    # zoom-past-full-frame was still there — and a user asking for a flat
    # landing was told the opposite of what was written.
    landing = ("the landing is DEAD FLAT — full frame on the cut and it "
               "stays there (settle=false), so the join reads as one clean "
               "arrival"
               if screen_spec.get("land") is False else
               "the momentum carries through the cut (a brief punch past "
               "full frame that settles), so the join sits inside one "
               "continuous motion")
    bits.append(
        f"From {start}s the camera pushes {z_end:.1f}x into the screen over "
        f"{dur}s. {appear} — "
        f"and the picture arrives full frame at exactly {hand}s, where "
        f"'{name}' cuts in and keeps playing from the same instant "
        f"({round(off + dur, 2)}s into the clip). The last frame of the "
        f"push and the first frame of the clip are the SAME frame, and "
        f"{landing}.")
    bits.append(
        "The push and the pin are one item — remove it with "
        f"remove_screen_takeover('{item['id']}'), which also takes the "
        f"handoff clip ({ins['id']}) out. Do NOT add a zoom over "
        f"{start}-{hand}s: it would move the shot out from under the content "
        "pinned to it.")
    return "\n".join(bits)


def _parse_screen_corners(corners):
    """8 numbers, or 4 [x, y] pairs, or a {x, y, w, h} rectangle."""
    if isinstance(corners, dict):
        try:
            x = float(corners["x"])
            y = float(corners["y"])
            w = float(corners["w"])
            h = float(corners["h"])
        except (KeyError, TypeError, ValueError):
            return None, ("REJECTED: a rectangle needs x, y, w and h as "
                          "fractions of the frame.")
        return [round(v, 4) for v in
                (x, y, x + w, y, x, y + h, x + w, y + h)], None
    if not isinstance(corners, (list, tuple)):
        return None, ("REJECTED: corners must be 8 numbers "
                      "(x0,y0,x1,y1,x2,y2,x3,y3), four [x, y] pairs, or a "
                      "{x, y, w, h} rectangle — all as fractions of the "
                      "frame.")
    flat = []
    for c in corners:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            flat.extend(c)
        else:
            flat.append(c)
    if len(flat) != 8:
        return None, (f"REJECTED: corners needs exactly 8 numbers "
                      f"(x0,y0,x1,y1,x2,y2,x3,y3 — top-left, top-right, "
                      f"BOTTOM-LEFT, bottom-right); got {len(flat)}.")
    try:
        vals = [round(float(v), 4) for v in flat]
    except (TypeError, ValueError):
        return None, "REJECTED: every corner coordinate must be a number."
    for v in vals:
        if not (-0.5 <= v <= 1.5):
            return None, (f"REJECTED: corners are FRACTIONS of the frame "
                          f"(0-1); got {v}. Divide pixel coordinates by the "
                          "frame width/height.")
    return vals, None


def remove_screen_takeover(ctx, id, keep_clip=False):
    """Undo a screen takeover — the pin, its camera push, and (unless
    keep_clip) the clip it handed off to."""
    edl = dict(ctx.latest_edl()["json"])
    items = [dict(o) for o in (edl.get("overlays") or [])]
    hit = next((o for o in items if o.get("id") == id and o.get("screen")),
               None)
    if not hit:
        have = ", ".join(o.get("id") or "?" for o in items if o.get("screen"))
        return (f"REJECTED: no screen takeover with id '{id}'. Existing "
                f"takeovers: {have or 'none'}. (Use remove_overlay for an "
                "ordinary overlay.) Call get_edl to see them.")
    hand = round(float(hit["start"]) + float(hit["duration_s"]), 2)
    edl["overlays"] = [o for o in items if o.get("id") != id]
    dropped = ""
    if not keep_clip:
        tl = Timeline(edl["keep"], edl.get("inserts") or [],
                      edl.get("speed") or [])
        # The handoff is the insert sitting exactly where the push ended and
        # carrying the same asset — matching on both is what stops this from
        # deleting an unrelated clip that happens to share the boundary.
        windows = insert_windows(edl.get("inserts") or [], tl)
        target = None
        for cand in (edl.get("inserts") or []):
            win = windows.get(cand.get("id"))
            if not win or abs(win[0] - hand) > 0.05:
                continue
            if cand.get("asset_key") == hit["asset_key"]:
                target = cand
                break
        if target is not None:
            edl["inserts"] = [i for i in (edl.get("inserts") or [])
                              if i.get("id") != target["id"]]
            dropped = f" and its handoff clip ({target['id']})"
    written = ctx.write_edl(
        edl, f"removed the screen takeover at {hit['start']}-{hand}s "
             f"({id}){dropped}")
    if written.startswith("EDL v") and keep_clip:
        written += ("\nThe handoff clip is still spliced in — it now arrives "
                    "as a plain cut.")
    return written


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
    if hit.get("screen"):
        # x/y/scale are not what a pinned overlay is made of, and moving its
        # start without moving the clip it hands off to desyncs the one join
        # the whole effect exists to hide.
        return (f"REJECTED: '{id}' is a screen takeover — its position and "
                "size come from the screen it is pinned to, not from x/y/"
                "scale, and its timing is locked to the clip it hands off to. "
                f"Remove it with remove_screen_takeover('{id}') and add it "
                "again at the moment you want.")
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
    # SAY when the window did not land where it was asked for. Clamping used
    # to be silent, and silence is what made it expensive: on project 363
    # (Aug 5 2026) six captions were written for a 19s reel while the program
    # was still the bare 1.6s clip, so all six collapsed into 1.3-1.6s, read
    # back as ordinary successes, and were only discovered from a preview —
    # after which the same turn spent 29 add_text and 17 remove_text calls
    # putting them back. A clamp this large is not a rounding detail, it is
    # the tool saying the program is not built yet.
    clamped = ""
    if abs(s - float(start)) > 0.05 or abs(e - float(end)) > 0.05:
        clamped = (f"\n\nCLAMPED: you asked for {float(start):g}-{float(end):g}s "
                   f"but the program is only {prog:g}s long, so this text sits "
                   f"at {s}-{e}s. If you meant it to land later, the footage "
                   "it belongs over does not exist yet — place the media "
                   "first (insert_media / keep_segments), THEN write the text "
                   "against the program those edits produce. Text does not "
                   "move when the timeline grows underneath it.")
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
    placement_note = ""
    if isinstance(ctx, ToolContext) or getattr(ctx, "enforce_spatial", False):
        default_y = float(graphics.TEMPLATES.get(tpl, {}).get("y", 0.5))
        wanted_y = float(y) if y is not None else default_y
        preferred = ("top" if wanted_y < 0.34 else
                     "bottom" if wanted_y > 0.66 else "middle")
        band, analyzed, unsafe, err = _fixed_text_band(
            ctx, edl, s, e, preferred, lock=bool(y is not None))
        if err:
            placement_note = (
                "\nQUALITY ADVISORY: face/source-text analysis was unavailable "
                f"({err}); the requested placement was still committed.")
        elif analyzed and band is None:
            band = preferred
            placement_note = (
                f"\nQUALITY ADVISORY: the requested {preferred} text band was "
                f"occupied in {unsafe} of {analyzed} measured frames across "
                f"{s}-{e}s; it was still committed at the requested/default "
                "position for the editor to judge in preview.")
        if band and y is None:
            safe_y = {"top": 0.16, "middle": 0.50, "bottom": 0.80}[band]
            if abs(safe_y - default_y) > 0.02:
                placement_note = (f"\nMeasured placement moved the {tpl} "
                                  f"from {preferred} to {band} to avoid "
                                  "faces/source text across its window.")
            y = safe_y
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
    muted_note = ""
    if edl.get("captions"):
        mutes = [list(m) for m in (edl.get("caption_mutes") or [])]
        mutes.append([s, e])
        edl["caption_mutes"] = mutes
        muted_note = ("\nTranscript captions are muted under this designed "
                      "text window so two independent word layers never "
                      "stack on screen.")
    return ctx.write_edl(
        edl, f"{tpl} text \"{t[:40]}\" at {s}-{e}s (program time) "
             f"[{item['id']}]") + clamped + placement_note + muted_note


# ── Typography choreography (round 82e) ─────────────────────────────────
# The #1 capability gap the exemplar corpus voted for: in every top
# talking-head edit, the SPEECH is carried by kinetic words appearing at
# the instant they are spoken, composed AROUND the speaker — dozens of
# placed text events per minute. One add_text call per phrase would take
# the agent 40+ tool calls and it would never attempt it; this composes
# the whole pass from the transcript in ONE call, writing ordinary
# TextItems (nothing new for the renderer, fully reversible per item).

_KINETIC_ZONES = {
    # cycling placement slots (x, y): beside/above a centered subject's
    # head, alternating sides so consecutive phrases converse across the
    # frame. The face of a centered talking head lives ~y 0.30-0.55 —
    # these keep out of it, and the render self-check shows the agent if
    # a particular framing disagrees.
    "upper": [(0.30, 0.22), (0.70, 0.20), (0.50, 0.13), (0.28, 0.32),
              (0.72, 0.30)],
    "lower": [(0.30, 0.72), (0.70, 0.74), (0.50, 0.80), (0.28, 0.66),
              (0.72, 0.68)],
    "sides": [(0.22, 0.38), (0.78, 0.36), (0.20, 0.55), (0.80, 0.55)],
}
_KINETIC_ENTRANCES = ("pop", "slide_up", "rise", "fade")


def _kinetic_phrases(words, tl, out_start, out_end):
    """Kept words inside the PROGRAM window -> [(text, start, end, stressed)]
    phrases of 1-4 words, broken at speech gaps (>0.6s source) and length."""
    timed = []
    for w in words:
        try:
            t0, t1 = float(w["t0"]), float(w["t1"])
            token = str(w.get("w") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not token:
            continue
        spans = tl.span_to_out(t0, t1)
        if not spans:
            continue
        a, b = spans[0]
        if b <= out_start or a >= out_end:
            continue
        timed.append((token, a, b, t0, t1))
    phrases, cur = [], []
    for tok in timed:
        if cur:
            gap = tok[3] - cur[-1][4]
            joined = " ".join(t[0] for t in cur)
            if gap > 0.6 or len(cur) >= 4 or len(joined) + len(tok[0]) > 24 \
                    or cur[-1][0].rstrip().endswith((".", ",", "?", "!")):
                phrases.append(cur)
                cur = []
        cur.append(tok)
    if cur:
        phrases.append(cur)
    return [(" ".join(t[0] for t in p), p[0][1], p[-1][2]) for p in phrases]


def add_kinetic_text(ctx, start=None, end=None, accent_color="#DC2626",
                     emphasis_words=None, zone="upper", color="#FFFFFF",
                     font=None, size_scale=None):
    """Choreograph the SPOKEN words onto the screen: one placed, animated
    text event per phrase, timed to the transcript, in a single pass."""
    if not ctx.has_main_video:
        return "REJECTED: there is no main video (and so no transcript)."
    words = ctx.index.get("words") or []
    if not words:
        return ("REJECTED: this video has no transcribed speech — kinetic "
                "text is built FROM the spoken words. Use add_text for "
                "designed titles instead.")
    z = (zone or "upper").strip().lower()
    if z not in _KINETIC_ZONES:
        return ("REJECTED: zone must be one of "
                + ", ".join(_KINETIC_ZONES) + ".")
    if font is not None and font not in TEXT_FONTS:
        return (f"REJECTED: font must be one of the bundled families: "
                f"{', '.join(TEXT_FONTS)}.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.4:
        return "REJECTED: there is no program yet to put text on."
    try:
        s = round(min(max(float(start if start is not None else 0.0), 0.0),
                      prog - 0.3), 2)
        e = round(min(max(float(end if end is not None else prog), s + 0.3),
                      prog), 2)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers (PROGRAM seconds)."
    try:
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
    except Exception as ex:
        return f"REJECTED: could not map the timeline ({str(ex)[:120]})."
    phrases = _kinetic_phrases(words, tl, s, e)
    if not phrases:
        return (f"REJECTED: no kept speech inside {s}-{e}s of the program "
                "— check get_kept_transcript for where the words actually "
                "are.")
    emph = {str(w).strip().lower().strip(".,!?") for w in
            (emphasis_words or []) if str(w).strip()}
    texts = [dict(tx) for tx in (edl.get("texts") or [])]
    base_scale = float(size_scale) if size_scale is not None else 0.55
    preferred_band = {"upper": "top", "lower": "bottom",
                      "sides": "middle"}[z]
    zone_for_band = {"top": "upper", "bottom": "lower",
                     "middle": "sides"}
    made, obstructed, unavailable, moved = [], 0, 0, 0
    for i, (ptext, pa, pb) in enumerate(phrases):
        nxt = phrases[i + 1][1] if i + 1 < len(phrases) else None
        hold_end = min(nxt if nxt is not None else pb + 1.4, pb + 2.2, e)
        hold_end = max(hold_end, pa + 0.5)
        item_start = round(max(s, pa - 0.05), 2)
        item_end = round(hold_end, 2)
        if isinstance(ctx, ToolContext) or getattr(ctx, "enforce_spatial",
                                                   False):
            band, analyzed, unsafe, err = _fixed_text_band(
                ctx, edl, item_start, item_end, preferred_band, lock=False)
            if err:
                unavailable += 1
                band = preferred_band
            if analyzed and band is None:
                obstructed += 1
                band = preferred_band
            band = band or preferred_band
        else:
            band = preferred_band
        actual_zone = zone_for_band[band]
        band_slots = _KINETIC_ZONES[actual_zone]
        x, y = band_slots[len(made) % len(band_slots)]
        if actual_zone != z:
            moved += 1
        stressed = bool(emph and
                        emph & {t.lower().strip(".,!?")
                                for t in ptext.split()})
        item = {"id": _next_item_id(texts, "tx"), "text": ptext[:60],
                "start": item_start, "end": item_end,
                "template": "callout",
                "x": x, "y": y,
                "size_scale": round(base_scale + (0.25 if stressed else 0.0),
                                    2),
                "color": accent_color if stressed else color,
                "accent_color": accent_color, "font": font,
                "entrance": "pop" if stressed
                else _KINETIC_ENTRANCES[i % len(_KINETIC_ENTRANCES)],
                "exit": "fade", "uppercase": None, "box": None}
        texts.append(item)
        made.append(item)
    edl["texts"] = texts
    # The spoken words are now ON screen — bottom captions repeating them
    # over the same window would print everything twice.
    muted_note = ""
    if edl.get("captions"):
        mutes = [list(m) for m in (edl.get("caption_mutes") or [])]
        mutes.extend([[item["start"], item["end"]] for item in made])
        edl["caption_mutes"] = mutes
        muted_note = (" Captions are muted over this window only under the "
                      "phrases actually placed so the words don't print "
                      "twice.")
    res = ctx.write_edl(
        edl, f"kinetic typography: {len(made)} phrase(s) choreographed to "
             f"the speech across {s}-{e}s (program time) "
             f"[{made[0]['id']}-{made[-1]['id']}]")
    if not res.startswith("EDL v"):
        return res
    sample = "; ".join(f'"{m["text"]}"@{m["start"]}s' for m in made[:4])
    res += (f"\n{len(made)} phrases placed from measured clean bands, each "
            f"appearing AT its spoken moment: {sample}..."
            f"{muted_note}")
    if moved:
        res += (f"\nMeasured composition moved {moved} phrase(s) out of "
                f"the requested {z} zone to avoid faces/source text.")
    if obstructed:
        res += (f"\nQUALITY ADVISORY: {obstructed} phrase(s) had no clean "
                "measured band and were committed in the requested zone for "
                "the editor to judge in preview.")
    if unavailable:
        res += (f"\nQUALITY ADVISORY: placement analysis was unavailable for "
                f"{unavailable} phrase(s); the requested zone was used.")
    res += ("\nEvery phrase is an ordinary text item (remove_text by id, "
            "or re-run over a window after remove_text to restyle). Render "
            "and inspect the independent visual review before delivery.")
    return res


TEXT_BEHIND_DEFAULT_S = 3.0


def _behind_text_box(item):
    """Roughly where on the frame this text will land, as (x, y, w, h)
    fractions — used ONLY to report how much of the words the subject actually
    crosses. It is a report, never a decision, so a template whose exact metrics
    differ by a few percent does not matter; what matters is telling the user
    "the subject never walks in front of these words" when that is true."""
    cx = 0.5 if item.get("x") is None else float(item["x"])
    cy = 0.5 if item.get("y") is None else float(item["y"])
    scale = float(item.get("size_scale") or 1.0)
    w = min(0.92, 0.62 * scale)
    h = min(0.5, 0.17 * scale)
    return (max(0.0, cx - w / 2), max(0.0, cy - h / 2), w, h)


def _matte_geometry(ctx, edl):
    """(fit_filter, width, height) for a mask that composites onto the render.

    The mask is measured on the PROXY (fast, and this runs on the same box as
    the agent turn) but has to be cropped EXACTLY as the picture is, so it goes
    through renderer.frame_fit_filter — the same function _normalize_video uses.
    Its target dims are the output frame scaled down to proxy height: identical
    aspect means identical crop geometry, and the renderer scales the mask up to
    WxH. Never larger than the proxy itself, so nothing is upscaled twice.
    """
    fr = edl.get("frame") or {}
    info = ctx.index.get("video") or {}
    sw = float(info.get("width") or 0) or 1920.0
    sh = float(info.get("height") or 0) or 1080.0
    W, H = renderer.frame_dims(sw, sh, fr.get("ratio"))
    mode = (fr.get("mode") or "crop") if fr.get("ratio") not in (None,
                                                                "source") \
        else "crop"
    focus = None
    if fr.get("focus_x") is not None or fr.get("focus_y") is not None:
        focus = (fr.get("focus_x"), fr.get("focus_y"))
    target_h = min(int(H), int(config.PROXY_HEIGHT))
    k = target_h / float(H)
    w = max(16, int(round(W * k / 2)) * 2)
    h = max(16, int(round(H * k / 2)) * 2)
    # pad modes must pad with BLACK here, not transparent: this is a mask, and
    # black means "not the subject" — which is exactly right for the letterbox
    # bars, where there is no picture at all.
    return renderer.frame_fit_filter(mode, w, h, focus, pad_color="black"), w, h


def add_text_behind(ctx, text, at_output_s, duration_s=None, template="title",
                    x=None, y=None, size_scale=None, color=None,
                    accent_color=None, font=None, entrance=None, exit=None,
                    uppercase=None, box=None):
    """Put words BEHIND the moving subject — the person walks in front of the
    letters. Measures the subject out of the shot and stores a mask the renderer
    composites back over the text."""
    t = (text or "").strip()
    if not t:
        return "REJECTED: text is empty."
    tpl = (template or "title").strip().lower()
    if tpl not in TEXT_TEMPLATES:
        return (f"REJECTED: template must be one of "
                f"{', '.join(TEXT_TEMPLATES)}.")
    if not ctx.has_main_video:
        return ("REJECTED: this puts words behind the SUBJECT of a shot, and "
                "there is no main video to find a subject in. On a clip/image "
                "canvas program, add_text is the ordinary title.")
    if entrance is not None and entrance not in TEXT_ANIMS:
        return f"REJECTED: entrance must be one of {', '.join(TEXT_ANIMS)}."
    if exit is not None and (exit not in TEXT_ANIMS or exit == "typewriter"):
        return ("REJECTED: exit must be one of "
                + ", ".join(a for a in TEXT_ANIMS if a != "typewriter")
                + " (typewriter is entrance-only).")
    if font is not None and font not in TEXT_FONTS:
        return (f"REJECTED: font must be one of the bundled families: "
                f"{', '.join(TEXT_FONTS)}.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.4:
        return ("REJECTED: there is no program yet to put text on — place "
                "footage first.")
    try:
        s = round(min(max(float(at_output_s), 0.0), max(0.0, prog - 0.4)), 2)
        dur = round(float(duration_s if duration_s is not None
                          else TEXT_BEHIND_DEFAULT_S), 2)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s and duration_s must be numbers — where "
                "in the EDITED video the words appear, and for how long.")
    e = round(min(s + max(dur, 0.4), prog), 2)
    if e - s < 0.4:
        return ("REJECTED: the window is under 0.4s — too short to read a "
                "title, let alone walk in front of one.")

    # ── the window has to be ONE continuous piece of ONE shot ──────────────
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    a_src = tl.out_to_src(s)
    b_src = tl.out_to_src(max(s, e - 0.02))
    if a_src is None or b_src is None:
        return (f"REJECTED: {s}-{e}s of the program is inside a spliced-in "
                "clip, not the main footage — there is no shot there to cut a "
                "subject out of. Point it at a moment where your own video is "
                "playing, or use add_text for a title over the clip.")
    ra, rb = tl.seg_program_range(a_src), tl.seg_program_range(b_src)
    if ra is None or rb is None or abs(ra[0] - rb[0]) > 0.001:
        return (f"REJECTED: there is a CUT inside {s}-{e}s. The background has "
                "to hold still for the whole window — I photograph it from the "
                "shot itself — so put the words entirely inside one take, or "
                "shorten duration_s.")
    for sp in (edl.get("speed") or []):
        if float(sp["end"]) > a_src and float(sp["start"]) < b_src:
            return (f"REJECTED: a speed ramp ({sp.get('id')}) covers that "
                    "footage, and the mask I measure is frame-for-frame with "
                    "the source — sped or slowed, it would drift off the "
                    "subject. Put the words behind footage that plays at "
                    "normal speed, or remove the ramp there first.")
    src_start, src_end = round(min(a_src, b_src), 2), round(max(a_src, b_src), 2)
    if src_end - src_start < 0.2:
        return ("REJECTED: under 0.2s of source footage is in that window.")

    # ── measure the subject (or refuse, with the number that says why) ──────
    # BIG TYPE IS THE LOOK (round 63b). Behind-subject text reads as depth
    # only when the subject crosses the MIDDLE of tall glyphs — their tops
    # and bottoms stay visible and the eye completes the letters. A small
    # line sits entirely inside the torso band, whole words vanish as the
    # person crosses, and the user reads "broken text", not depth (watched
    # happen on a real render: the agent even SHRANK the title to help, which
    # made every crossing hide more of it). So an unspecified size defaults
    # LARGE for a behind title; the caller can still pass any size_scale.
    behind_default_scale = 2.4 if tpl == "title" else None
    texts = [dict(tx) for tx in (edl.get("texts") or [])]
    item = {"id": _next_item_id(texts, "tx"), "text": t[:200], "start": s,
            "end": e, "template": tpl,
            "x": float(x) if x is not None else None,
            "y": float(y) if y is not None else None,
            "size_scale": (float(size_scale) if size_scale is not None
                           else behind_default_scale),
            "color": color, "accent_color": accent_color, "font": font,
            "entrance": entrance, "exit": exit,
            "uppercase": bool(uppercase) if uppercase is not None else None,
            "box": bool(box) if box is not None else None}
    try:
        fit, mw, mh = _matte_geometry(ctx, edl)
    except Exception as err:
        return f"REJECTED: could not work out the output geometry ({err})."
    # The planned METHOD rides the fingerprint (round 64): a mask built by
    # the photometric fallback (executor down for an afternoon) must not
    # permanently occupy the cache slot the person model would fill better.
    seg_planned = (remote.matte_available() or personseg.rvm_available()
                   or personseg.available())
    fp = hashlib.sha256(json.dumps([
        getattr(ctx, "_orig_sha", ""), src_start, src_end, fit, mw, mh,
        matte.DIFF_THRESHOLD, matte.PLATE_SAMPLES, matte.VERSION,
        "person" if seg_planned else "plate"],
        sort_keys=True).encode()).hexdigest()
    key = f"matte/{ctx.project_id}/{fp[:16]}.mp4"
    stats = None
    if storage.exists(key):
        # Cached: the same window measured before (an undo/redo, a re-worded
        # title over the same moment). The MASK is reusable — it depends only
        # on the footage — but the text-box numbers are NOT: round 63 caught
        # this path parroting "the subject crosses 11% of the text" measured
        # for a size-2.0 title onto a re-added size-1.2 one, whose smaller box
        # was in truth mostly behind the walker. So the box numbers are
        # re-measured against the cached mask itself (a 540p gray decode,
        # proxy-class work). If the mask cannot be read back, the reply says
        # nothing about the box rather than the wrong thing.
        prior = next((tx.get("behind") for tx in texts
                      if (tx.get("behind") or {}).get("fp") == fp), None)
        stats = {"ok": True, "coverage": (prior or {}).get("coverage"),
                 "fps": (prior or {}).get("fps"),
                 "method": (prior or {}).get("method")
                 or ("person" if seg_planned else "plate"),
                 "cached": True}
        try:
            mlocal = os.path.join(ctx.workdir, f"matte_c_{fp[:8]}.mp4")
            storage.download_to(key, mlocal)
            fresh = matte.box_stats(mlocal, _behind_text_box(item))
            if fresh:
                stats.update(fresh)
        except Exception:
            pass
    else:
        # The person model's forward passes run on the EXECUTOR (round 64) —
        # model compute on the dispatcher is the round-60 objection that was
        # right all along. Remote failure falls back to the photometric build
        # WITHOUT the model (that is today's dispatcher-safe work, never the
        # heavy path — the round-61b rule), and says so in the reply.
        stats = None
        fell_back = None
        if remote.matte_available() and getattr(ctx, "db", None):
            proxy_row = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
            if proxy_row:
                try:
                    stats = remote.run_matte_remote(
                        ctx.project_id,
                        {"storage_key": proxy_row["storage_key"],
                         "start": src_start, "dur": src_end - src_start,
                         "box": list(_behind_text_box(item)),
                         "extra_vf": fit, "width": mw, "height": mh,
                         "out_key": key,
                         "matte_version": matte.VERSION},
                        user_id=ctx.job.get("user_id"))
                except Exception as err:
                    fell_back = str(err)[:160]
                    stats = None
                if (stats is not None and stats.get("ok")
                        and stats.get("matte_version") != matte.VERSION):
                    # A stale executor predating the version handshake built
                    # a DIFFERENT mask and uploaded it under this version's
                    # cache key. Served, it would pin the old defects under
                    # the new fingerprint forever (the round-60 false-claim
                    # class). Delete it and fall back honestly.
                    try:
                        storage.delete_keys([key])
                    except Exception:
                        pass
                    fell_back = (f"executor built matte "
                                 f"v{stats.get('matte_version')} but this "
                                 f"code is v{matte.VERSION} — redeploy the "
                                 "executor")
                    stats = None
        if stats is None:
            try:
                src = ctx.proxy_path()
            except Exception:
                try:
                    src = _original_local(ctx)
                except Exception as err:
                    return (f"REJECTED: could not open the footage to "
                            f"measure the subject ({str(err)[:140]}).")
            out = os.path.join(ctx.workdir, f"matte_{fp[:8]}.mp4")
            try:
                stats = matte.measure_and_build(
                    src, out, src_start, src_end - src_start,
                    box=_behind_text_box(item), extra_vf=fit,
                    width=mw, height=mh,
                    allow_model=not remote.matte_available())
            except Exception as err:
                return (f"The subject measurement failed ({str(err)[:180]}). "
                        "Nothing was changed — do NOT claim the text was "
                        "added.")
            if stats.get("ok"):
                storage.upload_file(out, key, "video/mp4")
        if not stats.get("ok"):
            return (f"REJECTED: {stats.get('why') or 'the subject could not be measured'}. "
                    "Nothing was changed. add_text puts the same words on TOP "
                    "of the picture, which always works — offer that and say "
                    "plainly why the behind version will not.")
        if fell_back:
            stats["fell_back"] = fell_back

    item["behind"] = {"asset_key": key, "src_start": src_start,
                      "src_end": src_end, "fp": fp,
                      "coverage": stats.get("coverage"),
                      "fps": stats.get("fps"),
                      "method": stats.get("method")}
    texts.append(item)
    edl["texts"] = texts
    muted_note = ""
    if edl.get("captions"):
        mutes = [list(m) for m in (edl.get("caption_mutes") or [])]
        mutes.append([s, e])
        edl["caption_mutes"] = mutes
        muted_note = (" Transcript captions are muted under the depth-title "
                      "window so a second word layer cannot cover it.")
    written = ctx.write_edl(
        edl, f"{tpl} text \"{t[:40]}\" BEHIND the subject at {s}-{e}s "
             f"[{item['id']}]")
    if not written.startswith("EDL v"):
        return written
    bits = [written + muted_note]
    if stats.get("cached"):
        bits.append("The subject mask for that exact moment was already "
                    "measured, so this cost nothing to add.")
    else:
        cov = stats.get("coverage")
        eng = stats.get("engine")
        how = (("matted frame by frame by the person-matting model, which "
                "carries temporal state between frames so the mask holds "
                "steady instead of strobing")
               if eng == "rvm" else
               "found frame by frame by the person-segmentation model"
               if stats.get("method") == "person" else
               "from a background photographed out of the shot itself")
        bits.append(
            f"MEASURED on the footage: the subject covers {cov * 100:.1f}% of "
            f"the frame on average across the window (peaking at "
            f"{stats.get('coverage_max', 0) * 100:.1f}%), {how}. The words "
            f"are drawn on the picture and the subject is laid back over "
            f"them, so they pass BEHIND — this is not a fade or a "
            f"transparency.")
        if eng == "rvm":
            bits.append(
                "CONTRACT worth telling the user if furniture overlaps the "
                "words: the words go behind PEOPLE (including what they "
                "carry). Static objects — furniture, walls, parked cars — "
                "do not hide the letters; over those the words read as an "
                "ordinary title. That is deliberate: it is what keeps the "
                "occlusion rock-steady frame to frame.")
        if stats.get("fell_back"):
            bits.append(
                "NOTE: the person model was unreachable, so this mask came "
                "from the photometric fallback — it needs a still camera and "
                "can miss a dark subject on a dark background. If the render "
                "shows words printed over the subject, remove and re-add the "
                "text once the model is back.")
        if stats.get("no_cv2"):
            bits.append("NOTE: OpenCV was unavailable, so the mask edge is "
                        "unsmoothed — it may look slightly cut out.")
    # The box numbers are quoted for CACHED masks too — they were re-measured
    # against this text's own box above, because the previous text that built
    # the mask may have been a different size at a different spot.
    tc = stats.get("text_covered")
    tw = stats.get("text_width_covered")
    if tc is not None and tc < 0.02:
        bits.append(
            f"WARNING: the subject crosses only {tc * 100:.1f}% of where "
            "the words sit, so on screen this will look like an ordinary "
            "title. Tell the user, and offer to move the text (x/y) to "
            "where they actually walk, or to shift the window to when they "
            "cross the frame.")
    elif tw is not None and tw >= 0.45:
        # AREA under-sells what eyes see: 11% of the box's area can be two
        # whole letters gone, and a word missing two letters is a broken
        # word. Width interrupted maps to letters interrupted.
        bits.append(
            f"LEGIBILITY: at its peak the subject interrupts "
            f"{tw * 100:.0f}% of the line's WIDTH ({(tc or 0) * 100:.0f}% of "
            "the box's area). That much of the title is unreadable at that "
            "moment — right if the subject SWEEPS PAST (the words re-emerge), "
            "wrong if they stand there through the window. The craft fix is "
            "BIGGER type, never smaller: with tall glyphs the subject crosses "
            "the middle of the letters while their tops and bottoms stay "
            "readable — shrinking the line puts ALL of it inside the body "
            "and whole words vanish. Raise size_scale or shorten the text, "
            "and say which you did.")
    elif tc is not None:
        bits.append(
            f"The subject crosses {(tw or 0) * 100:.0f}% of the line's width "
            f"at its most ({tc * 100:.0f}% of the text's area), so the "
            "effect is visible and the title stays readable.")
    steady = ("Handheld or moving camera is fine — the subject is found in "
              "each frame on its own —"
              if stats.get("method") == "person" else
              "The camera must hold still for the window — that is what "
              "makes the background photographable —")
    bits.append(
        "It is bound to that FOOTAGE, not to a program time: a later cut moves "
        "it with the shot, and if that footage is cut away entirely the words "
        f"stay as an ordinary title. {steady} but do not add a zoom or a "
        "speed ramp over it (the mask is frame-for-frame with the source). "
        "NEXT: render_preview, and look at whether the subject's edge reads "
        "cleanly.")
    return "\n".join(bits)


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
                   "captions still show everywhere else. Transcript captions "
                   "mute at the WORD level (round 96c): the words inside the "
                   "window vanish, the words either side keep their "
                   "captions, and a group that straddles an edge splits "
                   "around it — so the window can be exactly the effect's "
                   "span. Dictated caption items (captions passed as a "
                   "list) are still hidden whole.")
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
    """A two-colour linear/radial gradient.

    Round 51 moved the implementation to worker/screenframe.py so the floating
    frame's backdrop and this card come out of ONE renderer — a second copy
    would drift, and "the same gradient as my interstitial" would stop being
    true the first time either was touched.
    """
    return screenframe.gradient_image(w, h, rgb1, rgb2, direction)


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


# ── The floating rounded window, and mid-video aspect changes (round 51) ────
# Both are OUTPUT-FRAME treatments: they change how the finished picture sits
# in the frame without touching a single timestamp. That is what makes them
# safe to apply to a finished edit and instant to preview.

def set_screen_frame(ctx, inset=None, radius=None, shadow=None,
                     background=None, background2=None, direction=None):
    """Inset the picture, round its corners, drop a shadow and float it on a
    solid or gradient backdrop. Re-applying replaces the settings."""
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    cur = dict(fx.get("screen_frame") or {})
    spec = {"inset": cur.get("inset", 0.08),
            "radius": cur.get("radius", 0.04),
            "shadow": cur.get("shadow", 0.5),
            "background": cur.get("background", "#0B0B0B"),
            "background2": cur.get("background2"),
            "direction": cur.get("direction", "vertical")}
    for name, val, lo, hi in (("inset", inset, SCREEN_FRAME_INSET_MIN,
                               SCREEN_FRAME_INSET_MAX),
                              ("radius", radius, 0.0,
                               SCREEN_FRAME_RADIUS_MAX),
                              ("shadow", shadow, 0.0, 1.0)):
        if val is None:
            continue
        try:
            spec[name] = round(min(max(float(val), lo), hi), 3)
        except (TypeError, ValueError):
            return (f"REJECTED: {name} must be a number between {lo} and "
                    f"{hi}.")
    for name, val in (("background", background),
                      ("background2", background2)):
        if val is None:
            continue
        v = str(val).strip().upper()
        if v in ("", "NONE") and name == "background2":
            spec["background2"] = None      # explicit "make it flat again"
            continue
        if not HEX_COLOR.match(v):
            return (f"REJECTED: {name} must be #RRGGBB hex — e.g. "
                    "background='#0B0B0B', background2='#2B1B4B' for a "
                    "gradient. Pass background2='none' to go back to a flat "
                    "colour.")
        spec[name] = v
    if direction is not None:
        d = str(direction).strip().lower()
        if d not in screenframe.GRADIENT_DIRECTIONS:
            return (f"REJECTED: direction must be one of "
                    f"{', '.join(screenframe.GRADIENT_DIRECTIONS)}.")
        spec["direction"] = d

    fx["screen_frame"] = spec
    edl["effects"] = fx
    look = (f"{spec['background']}->{spec['background2']} "
            f"{spec['direction']} gradient" if spec["background2"]
            else f"solid {spec['background']}")
    written = ctx.write_edl(
        edl, f"floating frame: picture inset {int(spec['inset'] * 100)}%, "
             f"corners {spec['radius']:g}, shadow {spec['shadow']:g}, on a "
             f"{look}")
    if not written.startswith("EDL v"):
        return written
    return (written + "\nThe WHOLE finished picture floats — captions, "
            "overlays and titles scale with it, because they are inside the "
            "window. Nothing about the timing changes. Remove it with "
            "remove_screen_frame.")


def remove_screen_frame(ctx):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    if not fx.get("screen_frame"):
        return ("REJECTED: there is no floating frame on this edit. Call "
                "get_edl to see what is set.")
    fx["screen_frame"] = None
    edl["effects"] = fx
    return ctx.write_edl(edl, "removed the floating frame (full-bleed again)")


def add_aspect_shift(ctx, at_output_s, ratio, duration_s=0.8, zoom=True,
                     color="#000000"):
    """Morph the visible frame to another aspect ratio mid-video, smoothly."""
    r = str(ratio or "").strip().lower()
    if r not in FRAME_SHIFT_RATIOS:
        return (f"REJECTED: ratio must be one of "
                f"{', '.join(FRAME_SHIFT_RATIOS)}. Use 'source' to open back "
                "out to the full frame.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    try:
        at = round(min(max(float(at_output_s), 0.0), max(0.0, prog)), 2)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — where in the FINAL "
                "edited video the frame starts changing, in seconds.")
    try:
        dur = round(min(max(float(duration_s), FRAME_SHIFT_MIN_S),
                        FRAME_SHIFT_MAX_S), 2)
    except (TypeError, ValueError):
        return (f"REJECTED: duration_s must be a number of seconds "
                f"({FRAME_SHIFT_MIN_S}-{FRAME_SHIFT_MAX_S}).")
    col = str(color or "#000000").strip().upper()
    if not HEX_COLOR.match(col):
        return "REJECTED: color must be #RRGGBB hex (the bars' colour)."

    _orient, ow, oh = _project_frame(ctx)
    fx = dict(edl.get("effects") or {})
    shifts = [dict(s) for s in (fx.get("frame_shifts") or [])]
    # THE HONEST REFUSAL. A shift whose target is the shape the frame is
    # already in at that moment writes an item the renderer emits nothing for —
    # a zero-delta segment costs nothing, which is exactly the problem: the
    # agent would report a change the user cannot see anywhere. So the state
    # immediately BEFORE this shift is resolved (the canvas, unless an earlier
    # shift already moved it) and compared with the target.
    target = screenframe.ratio_window(r, ow or 1280, oh or 720)
    prior = [s for s in shifts if float(s.get("at", 0.0)) <= at]
    prior.sort(key=lambda s: float(s.get("at", 0.0)))
    now = (screenframe.ratio_window(prior[-1].get("ratio"), ow or 1280,
                                    oh or 720)
           if prior else (1.0, 1.0))
    if abs(target[0] - now[0]) < 1e-4 and abs(target[1] - now[1]) < 1e-4:
        if not prior:
            return (f"REJECTED: this edit already renders at {r} "
                    f"({ow}x{oh}), so there is nothing to shift to. To change "
                    "the aspect of the WHOLE video use set_frame or "
                    "auto_reframe; use this tool to go to a DIFFERENT shape "
                    "for part of it and back.")
        return (f"REJECTED: the frame is already {r} at {at}s — "
                f"{prior[-1]['id']} put it there at {prior[-1]['at']}s. "
                "Shifting to the shape it is already in would change nothing "
                "on screen. Pick a different ratio, or move this earlier than "
                f"{prior[-1]['at']}s.")
    item = {"id": _next_item_id(shifts, "as"), "at": at, "ratio": r,
            "duration_s": dur, "zoom": bool(zoom), "color": col}
    shifts.append(item)
    fx["frame_shifts"] = shifts
    edl["effects"] = fx
    written = ctx.write_edl(
        edl, f"frame morphs to {r} at {at}s over {dur}s"
             + (" (pushing in as it narrows)" if zoom else "")
             + f" [{item['id']}]")
    if not written.startswith("EDL v"):
        return written
    return (written + f"\nThe file is still {ow}x{oh} — it has to be, a video "
            "has one resolution — so the change is the FRAME closing in over "
            f"{dur}s, which is what a smooth aspect change looks like. It "
            "stays at that shape until the next shift; add another with "
            "ratio='source' to open back out. Nothing about the timing, the "
            "audio or the captions moves. Remove it with remove_aspect_shift"
            f"('{item['id']}').")


def remove_aspect_shift(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    shifts = [dict(s) for s in (fx.get("frame_shifts") or [])]
    hit = next((s for s in shifts if s.get("id") == id), None)
    if not hit:
        have = ", ".join(s.get("id", "?") for s in shifts) or "none"
        return (f"REJECTED: no aspect shift with id '{id}'. Existing: "
                f"{have}. Call get_edl to see them.")
    rest = [s for s in shifts if s.get("id") != id]
    fx["frame_shifts"] = rest or None
    edl["effects"] = fx
    return ctx.write_edl(
        edl, f"removed the {hit['ratio']} aspect shift at {hit['at']}s ({id})")


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
                      "x": 0.5, "y": CARD_SUBTITLE_Y, "size_scale": None,
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


def _freeze_frame_asset(ctx, src_t, blur=0.0, darken=0.0):
    """Grab the frame at `src_t` from the ORIGINAL video and store it as a
    project image, optionally blurred and darkened.

    From the original, not the proxy: the proxy is a 540p analysis artefact and
    a still is looked at, not glanced past — a soft freeze frame in an
    otherwise sharp edit reads as a rendering fault.

    The treatment is baked into the PNG rather than layered at render time. A
    freeze frame is one still, so blurring it once here costs nothing, needs no
    filtergraph, and — the part that matters — cannot leak onto the moving
    footage around it, which is exactly what happened when the agent faked this
    with a windowed dream_blur over live video.
    """
    ck = (round(float(src_t), 2), round(float(blur), 2), round(float(darken), 2))
    cache = getattr(ctx, "_freeze_assets", None)
    if cache is None:
        cache = ctx._freeze_assets = {}
    if ck in cache:
        return cache[ck], None
    try:
        src = _original_local(ctx)
    except Exception:
        try:
            src = ctx.proxy_path()
        except Exception as e:
            return None, (f"Could not read the video to freeze a frame "
                          f"({str(e)[:120]}).")
    raw = os.path.join(ctx.workdir, f"freeze_{uuid.uuid4().hex[:8]}.png")
    try:
        media.frame_at(src, float(src_t), raw)
    except media.MediaError as e:
        return None, (f"Could not grab the frame at {float(src_t):.2f}s "
                      f"({str(e)[:120]}). Pick a moment inside the kept "
                      "footage.")
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        img = Image.open(raw).convert("RGB")
        if blur > 0:
            # Radius scales with the frame so the look is identical on a 540p
            # proxy grab and a 4K original.
            img = img.filter(ImageFilter.GaussianBlur(
                radius=max(1.0, min(img.size) * 0.012 * float(blur) * 4)))
        if darken > 0:
            img = ImageEnhance.Brightness(img).enhance(
                max(0.15, 1.0 - float(darken)))
        w, h = img.size
        path = os.path.join(ctx.workdir, f"freeze_t_{uuid.uuid4().hex[:8]}.png")
        img.save(path)
    except Exception as e:
        return None, f"Could not treat the frozen frame ({str(e)[:120]})."
    key = f"generated/{ctx.project_id}/freeze-{uuid.uuid4().hex[:12]}.png"
    try:
        storage.upload_file(path, key, "image/png")
    except Exception as e:
        return None, (f"The frozen frame was built but could not be saved "
                      f"({str(e)[:120]}). Try again.")
    ctx.db.run(dbx.insert_asset, ctx.project_id, "image_ref", key,
               bytes_=os.path.getsize(path), width=w, height=h,
               meta={"filename": f"freeze-{float(src_t):.2f}s.png",
                     "caption": (f"Frozen frame from {float(src_t):.2f}s"
                                 + (" (blurred)" if blur else "")
                                 + (" (darkened)" if darken else "")),
                     "generated": True, "model": "local:freeze"})
    cache[ck] = key
    return key, None


def add_freeze_frame(ctx, at_output_s, duration_s=2.5, text=None,
                     subtitle=None, blur=0.0, darken=0.0, motion="zoom_in",
                     template="title", color=None, accent_color=None,
                     font=None, audio_mode="pause"):
    """FREEZE THE PICTURE on a moment and hold it — with an optional line of
    text over the held frame.

    Three separate users asked for exactly this on one day, in the same words:
    "congela el fotograma exacto y úsalo como fondo, aplica un desenfoque
    suave, muestra la frase en el centro". The agent's answer was "no existe
    herramienta para capturar un frame del video como imagen" — and then it
    faked it with a dream_blur over LIVE footage, which is a different effect
    (the picture keeps moving under the words) and which it then had to
    disclose as not-what-was-asked in every reply.

    It is a real cut: the programme pauses on the still for `duration_s` and
    everything after shifts later, exactly like a title card — which is why
    captions never land on it and no mute is needed.
    """
    try:
        at = float(at_output_s)
    except (TypeError, ValueError):
        return ("REJECTED: at_output_s must be a number — the moment in the "
                "FINAL edited video to freeze, in seconds.")
    try:
        dur = round(min(max(float(duration_s), 0.3), 10.0), 2)
    except (TypeError, ValueError):
        return "REJECTED: duration_s must be a number of seconds."
    try:
        blur = min(max(float(blur or 0.0), 0.0), 1.0)
        darken = min(max(float(darken or 0.0), 0.0), 0.85)
    except (TypeError, ValueError):
        return "REJECTED: blur and darken must be numbers 0-1."
    audio_mode = str(audio_mode or "pause").strip().lower()
    if audio_mode not in ("pause", "continue"):
        return ("REJECTED: audio_mode must be 'pause' (insert a silent hold "
                "and shift what follows) or 'continue' (freeze only the "
                "picture while the existing speech/audio keeps running).")
    if not ctx.has_main_video:
        return ("REJECTED: there is no main video to freeze a frame from. "
                "insert_media an image instead.")
    edl = dict(ctx.latest_edl()["json"])
    prog = program_duration(edl)
    if prog <= 0.2:
        return "REJECTED: there is no program yet to freeze."
    at = round(min(max(at, 0.0), max(0.0, prog - 0.05)), 2)
    tl = Timeline([list(k) for k in (edl.get("keep") or [])],
                  edl.get("inserts") or [], edl.get("speed") or [])
    src_t = tl.out_to_src(at)
    if src_t is None:
        return (f"REJECTED: {at}s of the program does not sit on the main "
                "video (it lands on inserted media), so there is no source "
                "frame to freeze there. Pick a moment on the footage.")
    key, err = _freeze_frame_asset(ctx, src_t, blur, darken)
    if err:
        return err

    if audio_mode == "continue":
        # A full-frame still overlay replaces only the viewer's picture. The
        # program clock and every audio lane continue underneath it, which is
        # the precise visual/audio decoupling needed for a held sunset or
        # reaction frame over the last spoken phrase.
        placed = add_overlay(ctx, key, at, duration_s=dur, x=0.5, y=0.5,
                             scale=1.0, fit="cover")
        if not placed.startswith("EDL v"):
            return placed
        results = [placed]
        t = (text or "").strip()
        if t:
            results.append(add_text(
                ctx, t, at, min(at + dur, prog), template=template,
                x=0.5, y=0.46, color=color, accent_color=accent_color,
                font=font, entrance="fade", exit="fade"))
        sub = (subtitle or "").strip()
        if sub:
            results.append(add_text(
                ctx, sub, at, min(at + dur, prog), template="subtitle",
                x=0.5, y=0.66, color=color,
                entrance="fade", exit="fade"))
        good = [r for r in results if isinstance(r, str)
                and r.startswith("EDL v")]
        tail = (f"\nVisual-only freeze: the exact frame at source "
                f"{src_t:.2f}s covers {at}-{round(min(at + dur, prog), 2)}s, "
                "while the existing speech, source audio, music and program "
                "timing continue underneath it. Nothing after it shifts.")
        if motion:
            tail += (" A moving Ken Burns treatment is unavailable in "
                     "visual-only mode; this hold is intentionally still.")
        failed = [r for r in results[1:] if not str(r).startswith("EDL v")]
        if failed:
            tail += "\nText follow-up: " + " | ".join(str(r) for r in failed)
        return (good[-1] if good else placed).split(". Before:")[0] + tail

    before = {i.get("id") for i in (edl.get("inserts") or [])}
    placed = insert_media(ctx, key, at, duration_s=dur,
                          motion=(motion or None))
    if not placed.startswith("EDL v"):
        return placed
    treat = []
    if blur:
        treat.append(f"blurred {blur:g}")
    if darken:
        treat.append(f"darkened {darken:g}")
    note = (f"\nFroze the frame at {at}s of the program (source "
            f"{src_t:.2f}s) and held it for {dur:g}s"
            + (" — " + ", ".join(treat) if treat else "")
            + ". The picture is genuinely STILL (it is that exact frame, not "
            "the moving footage), everything after shifts "
            f"{dur:g}s later, and spoken-word captions never land on it.")

    t = (text or "").strip()
    if not t:
        return placed.split(". Before:")[0] + note
    # Bind the words to the frozen frame the same way a title card does, so a
    # later insert cannot leave them stranded on the moving footage.
    edl = dict(ctx.latest_edl()["json"])
    inserts = [dict(i) for i in (edl.get("inserts") or [])]
    item = next((i for i in inserts if i.get("id") not in before), None)
    windows = insert_windows(
        inserts, Timeline([list(k) for k in (edl.get("keep") or [])],
                          inserts, edl.get("speed") or [])) if item else {}
    win = windows.get(item["id"]) if item else None
    if win is None:
        return (placed.split(". Before:")[0] + note
                + " The text could not be anchored to it — add it with "
                  "add_text over the frozen window.")
    tpl = (template or "title").strip().lower()
    if tpl not in TEXT_TEMPLATES:
        tpl = "title"
    texts = [dict(tx) for tx in (edl.get("texts") or [])]
    ts, te = card_text_window(*win)
    made = []
    tx_item = {"id": _next_item_id(texts, "tx"), "text": t[:200],
               "start": ts, "end": te, "template": tpl, "x": 0.5, "y": 0.5,
               "size_scale": None, "color": color,
               "accent_color": accent_color, "font": font,
               "entrance": "fade", "exit": "fade", "uppercase": None,
               "box": None, "anchor_insert": item["id"]}
    texts.append(tx_item)
    made.append(f"{tpl} \"{t[:40]}\"")
    sub = (subtitle or "").strip()
    if sub:
        texts.append({"id": _next_item_id(texts, "tx"), "text": sub[:200],
                      "start": ts, "end": te, "template": "subtitle",
                      "x": 0.5, "y": CARD_SUBTITLE_Y, "size_scale": None,
                      "color": color, "accent_color": accent_color,
                      "font": None, "entrance": "fade", "exit": "fade",
                      "uppercase": None, "box": None,
                      "anchor_insert": item["id"]})
        made.append(f"subtitle \"{sub[:30]}\"")
    edl["texts"] = texts
    result = ctx.write_edl(edl, f"{' + '.join(made)} on the frozen frame at "
                                f"{win[0]}-{win[1]}s [{tx_item['id']}]")
    if not result.startswith("EDL v"):
        return placed.split(". Before:")[0] + note + "\n" + result
    return result.split(". Before:")[0] + note + (
        " The words are BOUND to the frozen frame, so later inserts move them "
        "with it.")


def add_stylize(ctx, kind, start=None, end=None, intensity=None):
    """A windowed finishing effect on the program picture."""
    k = (kind or "").strip().lower()
    if k not in STYLIZE_KINDS:
        return (f"REJECTED: kind must be one of {', '.join(STYLIZE_KINDS)}.")
    if (start is None) != (end is None):
        return ("REJECTED: pass both start and end (program seconds), or "
                "neither for the whole video.")
    if k == "stabilize" and start is not None:
        return ("REJECTED: stabilize analyzes motion across adjacent frames, "
                "so it must cover the whole video. Omit start/end; use "
                "intensity to control its search range.")
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


def enhance_video(ctx, sharpen=0.5, denoise=0.0, start=None, end=None):
    """"Make it clearer / sharper / better quality / HD" — the request five
    different users made in one day, to an agent that had no tool for it and
    answered with a colour grade, a contrast bump, or "the source is only
    576x1024, I can't invent pixels".

    Both halves of that answer were true and neither was useful. A phone's
    encoder genuinely smears fine detail, and a 5x5 unsharp pass genuinely
    brings it back — that is restoration, not upscaling, and it is what every
    "enhance" button in every consumer editor actually does. Denoise first when
    the footage is grainy, because sharpening noise makes it crawl.

    What it will NOT do is add resolution, and the tool says so in its result
    so the agent repeats the honest version rather than promising HD.
    """
    try:
        sh = min(max(float(sharpen if sharpen is not None else 0.0), 0.0), 1.0)
        dn = min(max(float(denoise if denoise is not None else 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        return "REJECTED: sharpen and denoise must be numbers 0-1."
    if sh <= 0.0 and dn <= 0.0:
        return ("REJECTED: pass sharpen and/or denoise above 0 — with both at "
                "0 there is nothing to apply. To UNDO an enhancement, "
                "remove_stylize its id.")
    if (start is None) != (end is None):
        return ("REJECTED: pass both start and end (program seconds), or "
                "neither for the whole video.")
    # Denoise must run BEFORE sharpen in the filter chain, and the chain order
    # is the list order, so it is added first.
    notes = []
    if dn > 0:
        res = add_stylize(ctx, "denoise", start, end, dn)
        if not res.startswith("EDL v"):
            return res
        notes.append(f"denoise {dn:g}")
    if sh > 0:
        res = add_stylize(ctx, "sharpen", start, end, sh)
        if not res.startswith("EDL v"):
            return res
        notes.append(f"sharpen {sh:g}")
    return (res + "\nEnhanced (" + ", ".join(notes) + "). This recovers "
            "detail the camera's encoder smeared and makes the picture read "
            "crisper — it does NOT add resolution, so tell the user plainly "
            "that a low-resolution source stays low-resolution. If they also "
            "asked for 'no filters', check get_edl and remove any colour "
            "grade: enhancement is not a look.")


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


# ── Custom filter chains (round 96) ─────────────────────────────────────────
# The open-ended stylize: the agent WRITES the ffmpeg chain instead of picking
# a kind from a menu, so a look nobody hand-built stops requiring a new tool
# and a deploy. Three nets stand where the menu used to:
#   1. custom_chain_error — structural: one chain, no graph syntax, no file
#      access (shared with validate_edl, so both services agree forever).
#   2. the DRY RUN below — semantic: the chain runs on the real preview
#      source before anything is stored, so "ffmpeg rejected it" arrives as
#      ffmpeg's own words in seconds, and its COST is measured, not guessed.
#   3. the agent's eyes — aesthetic: a chain that parses can still look
#      wrong, and no validator can see that; the result text sends the agent
#      to look_at the window on the next preview.

_CUSTOM_FEATURE_CACHE = {"at": 0.0, "ok": None}


def _executor_can_custom():
    """Does the render service know effects.custom? Cached 5 minutes.

    The dispatcher auto-deploys on push and the executor does not, so right
    after a deploy THIS code can validate a field the render service cannot
    draw. Storing it anyway would render previews with the effect silently
    missing — the worst failure shape we know (round 53/55). Definite "no"
    refuses the WRITE only; unreachable stays permissive (round-53 rule) and
    is retried on the next call rather than cached."""
    now = time.time()
    if _CUSTOM_FEATURE_CACHE["ok"] is not None and \
            now - _CUSTOM_FEATURE_CACHE["at"] < 300:
        return _CUSTOM_FEATURE_CACHE["ok"]
    ok = remote.executor_supports("custom_filter")
    if ok is not None:
        _CUSTOM_FEATURE_CACHE["at"] = now
        _CUSTOM_FEATURE_CACHE["ok"] = ok
    return ok


def _probe_custom_chain(ctx, chain):
    """Dry-run `chain` on the real preview source. Returns (error, ratio):
    error is an agent-actionable string (None when the chain is fine) and
    ratio the measured cost against a plain encode of the same span."""
    probe = config.CUSTOM_FILTER_PROBE_S
    if ctx.has_main_video:
        try:
            path = ctx.proxy_path()
        except Exception:
            try:
                path = _original_local(ctx)
            except Exception as e:
                return (f"could not stage footage for the dry run "
                        f"({str(e)[:140]}) — nothing was written", None)
        ss = max(0.0, min(ctx.duration / 2.0,
                          max(ctx.duration - probe, 0.0)))
        src = ["-ss", f"{ss:.2f}", "-t", f"{probe:.2f}", "-i", path]
    else:
        cv = (ctx.latest_edl()["json"].get("canvas") or {})
        w = int(cv.get("width") or 1280)
        h = int(cv.get("height") or 720)
        src = ["-f", "lavfi", "-i",
               f"color=c=0x336699:size={w}x{h}:rate=30:duration={probe:.2f}"]

    def _enc(vf, tag):
        out = os.path.join(ctx.workdir, f"cfprobe_{tag}.mp4")
        cmd = (["ffmpeg", "-y", "-v", "error"] + src
               + (["-vf", vf] if vf else [])
               + ["-an", "-c:v", "libx264", "-preset", "ultrafast",
                  "-crf", "30", out])
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=config.CUSTOM_FILTER_PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return None, None, "TIMEOUT"
        if r.returncode != 0:
            return None, None, (r.stderr or "ffmpeg failed").strip()[-400:]
        return out, time.time() - t0, None

    def _shape(path):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,avg_frame_rate",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=20)
            wd, ht, rate = (r.stdout or "").strip().split(",")[:3]
            num, _, den = rate.partition("/")
            return int(wd), int(ht), float(num) / float(den or 1)
        except Exception:
            return None

    ctrl, t_ctrl, err = _enc(None, "ctrl")
    if err:
        return (f"the footage itself failed a plain probe encode "
                f"({err[:200]}) — the problem is not your chain; try again "
                "or tell the user the preview source is unreadable", None)
    piece, t_chain, err = _enc(chain, "chain")
    if err == "TIMEOUT":
        return (f"the chain stalled: {probe:g}s of footage did not finish "
                f"encoding within {config.CUSTOM_FILTER_PROBE_TIMEOUT_S:g}s. "
                "A render with it would hang. Drop the heaviest filter "
                "(frame interpolation, huge blurs and palette ops are the "
                "usual causes) and try a simpler chain", None)
    if err:
        return ("ffmpeg rejected the chain on real footage: " + err
                + "\nThe error names the filter or option at fault — fix "
                "the chain and call again. Do NOT retry the identical "
                "string.", None)
    a, b = _shape(ctrl), _shape(piece)
    if a and b:
        if (a[0], a[1]) != (b[0], b[1]):
            return (f"the chain changes the frame geometry "
                    f"({a[0]}x{a[1]} -> {b[0]}x{b[1]}). Chains must return "
                    "the frame they receive: reframing is set_frame's job, "
                    "and the zoom/caption stages after this one assume the "
                    "program's geometry. End the chain by scaling back, or "
                    "drop the scaling filter.", None)
        if abs(a[2] - b[2]) > 0.6:
            return (f"the chain changes the frame RATE ({a[2]:g} -> "
                    f"{b[2]:g} fps). Time belongs to set_speed and the "
                    "renderer's clock — drop the fps/interpolation filter.",
                    None)
    ratio = (t_chain or 0.0) / max(t_ctrl or 0.0, 0.05)
    if ratio > config.CUSTOM_FILTER_MAX_COST:
        return (f"the chain costs {ratio:.1f}x a plain encode at preview "
                f"resolution (the cap is "
                f"{config.CUSTOM_FILTER_MAX_COST:g}x) — a render with it "
                "would crawl. Drop the heaviest filter or narrow the look "
                "to a shorter window.", None)
    return None, ratio


def add_custom_filter(ctx, chain, start=None, end=None, label=None):
    """Write-your-own-effect: one validated, dry-run ffmpeg chain."""
    err = custom_chain_error(chain)
    if err:
        return "REJECTED: " + err
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
    if _executor_can_custom() is False:
        return ("REJECTED: the render service is running a build that "
                "predates custom filter chains — storing this would make "
                "every preview render WITHOUT the effect. Compose the "
                "closest look from apply_look / add_stylize / "
                "set_grade_custom / enhance_video instead, and tell the "
                "user this exact effect needs a service update. (Operator: "
                "deploy the executor — worker/DEPLOY_EXECUTOR.md.)")
    perr, ratio = _probe_custom_chain(ctx, chain.strip())
    if perr:
        return "REJECTED: " + perr
    fx = dict(edl.get("effects") or {})
    cfs = [dict(c) for c in (fx.get("custom") or [])]
    item = {"id": _next_item_id(cfs, "cf"), "chain": chain.strip(),
            "start": s, "end": e,
            "label": ((label or "").strip()[:60] or None)}
    cfs.append(item)
    fx["custom"] = cfs
    edl["effects"] = fx
    name = item["label"] or item["chain"][:40]
    window = (f" on {s}-{e}s (program time)" if s is not None
              else " on the whole video")
    res = ctx.write_edl(
        edl, f"custom filter '{name}'{window} [{item['id']}]")
    if res.startswith("EDL v"):
        res += (f"\nThe chain passed its dry run on real footage, at "
                f"~{ratio:.1f}x the cost of a plain encode. A chain that "
                "parses can still look wrong — render_preview and LOOK at "
                "output frames inside the window before describing the "
                "effect to the user; if the look is not what you meant, "
                f"remove_custom_filter('{item['id']}') and write a better "
                "chain.")
    return res


def remove_custom_filter(ctx, id):
    edl = dict(ctx.latest_edl()["json"])
    fx = dict(edl.get("effects") or {})
    cfs = [dict(c) for c in (fx.get("custom") or [])]
    hit = next((c for c in cfs if c.get("id") == id), None)
    if not hit:
        have = ", ".join(c.get("id") or "?" for c in cfs) or "none"
        return (f"REJECTED: no custom filter with id '{id}'. Existing: "
                f"{have}. Call get_edl to see them.")
    fx["custom"] = [c for c in cfs if c.get("id") != id]
    edl["effects"] = fx
    name = hit.get("label") or (hit.get("chain") or "")[:40]
    return ctx.write_edl(edl, f"removed custom filter '{name}' ({id})")


# (lo, hi, neutral) per custom-grade axis — the neutral value IS the absence
# of the control, so passing it clears the axis (schema normalizes the same).
_GRADE_AXES = {"exposure": (-1.0, 1.0, 0.0), "contrast": (0.5, 1.6, 1.0),
               "shadows": (-1.0, 1.0, 0.0), "highlights": (-1.0, 1.0, 0.0),
               "saturation": (0.0, 2.0, 1.0), "temperature": (-1.0, 1.0, 0.0),
               "tint": (-1.0, 1.0, 0.0)}


def set_grade_custom(ctx, exposure=None, contrast=None, saturation=None,
                     temperature=None, tint=None, shadows=None,
                     highlights=None):
    """Continuous color controls, merged axis-by-axis into
    effects.grade_custom — None leaves an axis alone, its neutral clears it."""
    vals = {"exposure": exposure, "contrast": contrast,
            "saturation": saturation, "temperature": temperature,
            "tint": tint, "shadows": shadows, "highlights": highlights}
    if all(v is None for v in vals.values()):
        return ("REJECTED: pass at least one axis — exposure -1..1, "
                "contrast 0.5..1.6 (1.0 neutral), saturation 0..2 (1.0 "
                "neutral), temperature -1 (cool)..1 (warm), tint -1 "
                "(green)..1 (magenta), shadows -1..1 (positive lifts the "
                "darks), highlights -1..1 (negative recovers blown areas). "
                "An axis's neutral value clears it.")
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
        res += ("\nThe final mix is normalized to -14 LUFS with a codec-safe "
                "-2.0 dBTP ceiling on PREVIEW and EXPORT — "
                "what the user approves is what ships. It changes loudness, "
                "not the balance between voice/music/sfx.")
    return res


_STEMS_FEATURE_CACHE = {"at": 0.0, "ok": None}


def _stems_supported():
    """Same contract as the custom-filter gate (round 96): definite 'no'
    refuses, unreachable stays permissive and is retried, and the answer is
    cached briefly so a turn does not probe /health per call."""
    now = time.time()
    if _STEMS_FEATURE_CACHE["ok"] is not None and \
            now - _STEMS_FEATURE_CACHE["at"] < 300:
        return _STEMS_FEATURE_CACHE["ok"]
    ok = remote.stems_available()
    if ok is not None:
        _STEMS_FEATURE_CACHE["at"] = now
        _STEMS_FEATURE_CACHE["ok"] = ok
    return ok


def separate_music(ctx, music_gain_db=None, voice_gain_db=None):
    """Round 97 (#7): rebalance the ORIGINAL footage's music vs its speech.

    'Remove the background song but keep the talking' — the ask that was an
    honest 'impossible' while the original audio was one mixed track. The
    footage's soundtrack is separated ONCE per video (Demucs on the render
    service, cached forever by source sha), and the EDL's stem_mix node then
    plays the two sides at their own gains."""
    if not ctx.has_main_video:
        return ("This project has no main video — separation works on the "
                "original footage's own soundtrack.")
    if _stems_supported() is False:
        return ("Music/voice separation isn't available on the render "
                "service right now. I can still lower the WHOLE original "
                "track with set_volume, or mute spans — but not split the "
                "music from the speech.")
    mg = 0.0 if music_gain_db is None else float(music_gain_db)
    vg = 0.0 if voice_gain_db is None else float(voice_gain_db)
    if not (-60.0 <= mg <= 6.0 and -60.0 <= vg <= 6.0):
        return "Stem gains must be between -60 (mute) and +6 dB."
    if ctx.duration > config.STEMS_MAX_SOURCE_S:
        return (f"This video is {ctx.duration / 60:.0f} minutes long — "
                f"music separation currently supports footage up to "
                f"{config.STEMS_MAX_SOURCE_S / 60:.0f} minutes. Tell the "
                "user that plainly; set_volume still works on the whole "
                "track.")
    row = _original_row(ctx)
    sha = row.get("sha256") or ""
    if not sha:
        return ("The original file hasn't finished processing — try again "
                "in a moment.")
    vocals_key = f"stems/{sha}/vocals.m4a"
    accomp_key = f"stems/{sha}/accomp.m4a"
    if not (storage.exists(vocals_key) and storage.exists(accomp_key)):
        # First separation for this footage. Prefer the proxy's bytes — the
        # audio track is the same and the download is a fraction of a
        # multi-GB original.
        src_row = None
        try:
            src_row = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
        except Exception:
            src_row = None
        src_key = (src_row or row)["storage_key"]
        try:
            res = remote.run_stems_remote(
                ctx.project_id,
                {"src_key": src_key, "vocals_key": vocals_key,
                 "accomp_key": accomp_key},
                user_id=ctx.job.get("user_id"))
        except Exception as e:
            return (f"Separating the music failed ({str(e)[:160]}) — the "
                    "original audio is untouched. Tell the user honestly; "
                    "set_volume on the whole track still works.")
        if not (res or {}).get("ok"):
            return (f"Separating the music failed ({str(res)[:120]}) — the "
                    "original audio is untouched.")
    edl = dict(ctx.latest_edl()["json"])
    edl["stem_mix"] = {"vocals_key": vocals_key, "accomp_key": accomp_key,
                       "voice_gain_db": vg, "music_gain_db": mg}
    res = ctx.write_edl(
        edl, f"stem mix: voice {vg:+.0f} dB, music/background {mg:+.0f} dB")
    if res.startswith("EDL v"):
        res += ("\nThe original soundtrack now plays as two separated "
                "stems: speech/vocals at "
                f"{vg:+.0f} dB and music/background at {mg:+.0f} dB. This "
                "is an AUDIO-only change — the next preview is fast. "
                "remove_stem_mix restores the untouched original mix. "
                "Separation is strong but not surgical: on dense mixes a "
                "trace of the muted side can remain — listen to the "
                "preview before promising silence.")
    return res


def remove_stem_mix(ctx):
    """Restore the original mixed soundtrack (drop the stem_mix node)."""
    edl = dict(ctx.latest_edl()["json"])
    if not edl.get("stem_mix"):
        return ("No stem mix is set — the original soundtrack is already "
                "playing untouched.")
    edl["stem_mix"] = None
    return ctx.write_edl(edl, "stem mix removed — original soundtrack "
                              "restored")


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
        # A bot-walled candidate is the one failure that is NOT final: the
        # wall is per-upload, so the error itself instructs the model to
        # try the next find_song link. Bolting the generic "tell the user /
        # suggest an upload" coda onto that would bury the retry in a
        # give-up script — which is how one unlucky pick used to end the
        # whole request.
        if ytaccess.bot_walled(str(e)):
            return (f"Could not download that link — {e} "
                    "Continue the current edit NOW with any already-attached "
                    "music or clips. Do not freeze the picture waiting for "
                    "this file.")
        # DRM / premium-locked uploads are per-ITEM too, not a dead end for
        # the SONG. The official master of a chart hit is DRM-locked on
        # SoundCloud and walled on YouTube (Aug 9: "Blinding Lights" was
        # both), so a locked pick means try another candidate that is the
        # SAME track — and only when just covers or locked masters remain
        # is "upload the real file" the honest answer. Never pass a cover
        # off as the original.
        low = str(e).lower()
        if any(w in low for w in ("drm", "premium", "paid members",
                                  "purchase", "only available")):
            return (f"Could not download that link — {e} This upload is "
                    "LOCKED (DRM/premium). Try another candidate that is the "
                    "same song; if only covers or other locked masters are "
                    "left, tell the user this exact master can't be "
                    "auto-fetched and to upload the file — do NOT add a "
                    "cover/remix as if it were the original.")
        return (f"Could not download that link — {e}. Tell the user that "
                "plainly and suggest they upload the file instead. Do NOT "
                "claim anything was added. Continue the current edit NOW "
                "with any already-attached music or clips — a failed fetch "
                "is not a reason to leave the picture unchanged.")
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        return (f"Could not download that link ({str(e)[:200]}). Tell the "
                "user it did not work. Do NOT claim anything was added.")

    kind, path = got["kind"], got["path"]
    key = url_media.storage_key(ctx.project_id, kind, path)
    reviewed = 0
    try:
        storage.upload_file(path, key, url_media.content_type(path))
        reviewed = _queue_download_review(
            ctx, path, kind, got.get("duration_s"), got.get("filename") or
            url_media.KIND_LABEL[kind])
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
                     "uploader": got.get("uploader"),
                     "license": None,
                     "license_status": "unverified",
                     "license_note": ("No usage license was verified for "
                                      "this URL; downloading does not grant "
                                      "republication rights.")})
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
    review = (f" AUTOMATIC VISUAL REVIEW: {reviewed} representative "
              "frame(s) from the actual downloaded file are attached. "
              "Inspect those pixels before placing it; use look_at_asset "
              "for exact seconds."
              if reviewed else "")
    return (f"Downloaded \"{got['filename']}\"{detail} as a "
            f"{url_media.KIND_LABEL[kind]}: storage_key={key}. It is saved to "
            f"the project but NOT in the video yet — {nxt}.{review} RIGHTS CHECK: "
            "source title/uploader identify the file, but no usage license "
            "was verified; downloading it does not grant republication "
            "rights. Use it only when the user has the needed rights.")


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
    seen = _queue_candidate_thumbs(ctx, hits)
    eye = ("\n\nTheir thumbnails are attached below, labeled by id — pick "
           "by LOOKING at them, the way an editor scans a results grid: "
           "the frame that actually shows the subject, in light and color "
           "that can sit inside this edit."
           if seen else "")
    return (f"{len(hits)} stock {kind}(s) for \"{q}\" ({orientation}):\n"
            + stock.summarize(hits) + eye
            + "\n\nNothing is downloaded or in the video yet. Pick the ONE "
              "that best matches what the user asked for and call "
              "add_stock_media(id=...). Prefer a candidate that actually "
              "depicts the subject over one that merely shares a "
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
    if not (item.get("description") or "").strip() and \
            not item.get("_thumbnail_delivered"):
        return (
            "REJECTED: this provider returned no description for that result "
            "and its thumbnail could not be delivered, so there is no "
            "evidence of what the shot actually depicts. Pick a candidate "
            "whose thumbnail you can inspect or run a more specific search; "
            "do not infer the footage from its search rank."
        )

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
    dur = (info or {}).get("duration") or item.get("duration_s")
    reviewed = 0
    try:
        storage.upload_file(path, key, url_media.content_type(path))
        reviewed = _queue_download_review(
            ctx, path, kind, dur, (item.get("description") or "stock media")[:60])
    except Exception as e:
        return (f"Downloaded the stock clip but could not save it "
                f"({str(e)[:160]}). Do NOT claim it was added; try again.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

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
                     "license": item.get("license"),
                     "license_note": item.get("license_note"),
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
    review = (f" {reviewed} representative frame(s) from the downloaded "
              "rendition are attached for an automatic visual review; inspect "
              "them before placement."
              if reviewed else "")
    return (f"Added stock {'clip' if is_video else 'image'} \"{desc}\""
            f"{detail} to the project: storage_key={key}. It is SILENT and "
            f"NOT in the video yet — place it with {place}.{review} Cover overlays of "
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


# Pages that answer a datacenter IP with a wall instead of themselves. The
# capture SUCCEEDS — 8 seconds of video, correct resolution, no error anywhere —
# and what is in it is a CAPTCHA. Verified on 2026-07-30: recording
# google.com/search?q=plumber+near+me from the executor landed on
# google.com/sorry/index, which is exactly what the customer who asked for
# "a Google search for 'plumber near me'" would have got spliced into their
# video, described to them as b-roll of search results.
#
# Same family as the YouTube bot wall (round 40): it is IP-based, so there is
# nothing to fix in the browser. What there IS to fix is the claim — the agent
# must be told it filmed a wall so it can say so and offer the alternative,
# rather than reporting a clean capture.
_INTERSTITIAL_MARKS = (
    "/sorry/", "captcha", "consent.", "/consent", "unusual traffic",
    "are you a robot", "access denied", "just a moment",
    "attention required", "verify you are human", "enable javascript",
    "log in", "sign in to continue",
)


def _interstitial_problem(url, got):
    """A sentence for `problems` when the capture landed on a wall, else None."""
    final = (got.get("final_url") or "").lower()
    title = (got.get("page_title") or "").lower()
    hit = next((m for m in _INTERSTITIAL_MARKS
                if m in final or m in title), None)
    if not hit:
        return None
    where = got.get("final_url") or url
    return (f"THIS CAPTURE IS NOT THE PAGE — it landed on a bot check / "
            f"consent / login wall ({where[:120]}). Recording from a server "
            f"gets that instead of the real page for Google and many big "
            f"sites, and it is IP-based, so retrying will not help. Tell the "
            f"user plainly what is in this clip, do NOT put it in the video "
            f"as if it were the page, and offer the alternative: they screen-"
            f"record the page themselves and upload it")


def _note_interstitial(url, got):
    """Put the wall at the FRONT of problems — it is the thing that matters
    most about this clip, and problems are truncated to six."""
    p = _interstitial_problem(url, got)
    if p:
        got["problems"] = [p] + list(got.get("problems") or [])


def _run_capture(ctx, mode, url, **kw):
    """Record a web page, on the executor when there is one.

    Returns (got, None) or (None, failure_text). ROUND 61 — the browser must
    not run on the dispatcher: a headless Chromium at 1080x1920 inside an agent
    turn OOM-killed a real customer's turn on the first production use of
    record_website, and an OOM is a disappearance, so none of the honest
    "could not record that page" handling below could fire.

    The remote path is preferred but never required: with no executor
    configured this records locally exactly as it always did, which is right
    for a single-box deployment. A remote failure does NOT fall back to local —
    that would reproduce the crash it exists to avoid, on a box we already know
    is too small.
    """
    if remote.capture_available():
        payload = dict(kw, mode=mode, url=url)
        try:
            got = remote.run_capture_remote(ctx.project_id, payload,
                                            user_id=getattr(ctx, "user_id",
                                                            None))
        except remote.RemoteExecutorError as e:
            return None, str(e)
        except Exception as e:
            return None, str(e)[:200]
        if not isinstance(got, dict) or not got.get("storage_key"):
            return None, "the capture service returned nothing usable"
        _note_interstitial(url, got)
        return got, None

    workdir = os.path.join(ctx.workdir, f"webcap_{uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        if mode == "demo":
            got = webrecord.record_demo(url, workdir, kw.get("steps") or [],
                                        orientation=kw.get("orientation"))
        else:
            got = webrecord.record(url, workdir,
                                   duration_s=kw.get("duration_s"),
                                   orientation=kw.get("orientation"),
                                   scroll=bool(kw.get("scroll", True)))
        # Upload HERE, while the file still exists, so both branches hand back
        # the same shape and the workdir's lifetime lives in one place. The
        # caller is then only ever registering an object that is already in
        # storage.
        path = got.pop("path")
        key = url_media.storage_key(ctx.project_id, url_media.KIND_VIDEO, path)
        storage.upload_file(path, key, url_media.content_type(path))
        got["storage_key"] = key
        _note_interstitial(url, got)
        return got, None
    except webrecord.WebRecordError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)[:200]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _store_capture(ctx, url, got, kind_word):
    """Upload a finished capture, register the asset, remember the event
    track ON THE ASSET. Returns (storage_key, name, None) or (None, None,
    failure text).

    The events are written into the asset's meta, not just onto ctx: a
    recording made this turn is very often placed in the NEXT one ("actually,
    put the demo at the start"), and a track that only lived in turn memory
    would be gone exactly when showcase_demo needed it.
    """
    # A capture run on the executor (round 61) has already uploaded itself and
    # returns the key instead of a local path — the mp4 never crosses the wire.
    key = got.get("storage_key")
    if not key:
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

    got, err = _run_capture(ctx, "record", url, duration_s=dur,
                            orientation=orientation, scroll=bool(scroll))
    if err:
        return (f"Could not record that page — {err}. Tell the user plainly "
                "and offer the alternative (they screen-record it and "
                "upload). Do NOT claim anything was recorded or added.")

    key, name, fail = _store_capture(ctx, url, got, "capture")
    if fail:
        return fail
    # This branch never reported `problems` at all — only the demo did — so a
    # capture that filmed a bot wall came back reading exactly like a clean one.
    trouble = ""
    problems = got.get("problems") or []
    if problems:
        trouble = ("\nWHAT DID NOT WORK (tell the user, do not hide it): "
                   + "; ".join(problems[:6]) + ".")
    return (f"Recorded \"{name}\" — "
            f"{got['duration_s']:.1f}s at {got['width']}x{got['height']} "
            f"(the page loads, holds, then smooth-scrolls to the bottom): "
            f"storage_key={key}. It is saved to the project but NOT in the "
            "video yet — splice it with insert_media, or lay it over the "
            "footage with add_overlay (fit='cover' for a full-frame "
            "cutaway while the speech continues). The capture is SILENT."
            + trouble)


def record_website_demo(ctx, url, steps, orientation=None):
    """Drive a live web page through a scripted walkthrough with a visible
    cursor, record it, and register it as a project video asset."""
    url, orientation, rej = _capture_precheck(ctx, url, orientation)
    if rej:
        return rej

    got, err = _run_capture(ctx, "demo", url, steps=steps,
                            orientation=orientation)
    if err:
        return (f"Could not record that walkthrough — {err}. Tell the user "
                "plainly what went wrong. Do NOT claim anything was recorded "
                "or added.")

    key, name, fail = _store_capture(ctx, url, got, "demo")
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
                  click_sounds=False, zooms=True, click_times=None):
    """Splice a screen recording in and cut it like a product video: one
    gliding zoom per run of clicks, a click sound on each click."""
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
    # Round 51: a capture the user made themselves is the COMMON case, and
    # refusing it was the tool refusing to do its job on the footage most
    # people have. Two levels of graceful degradation, in order:
    #
    #   1. caller-supplied click_times -> timing only. A click time does not
    #      reveal WHERE the click landed, so it is enough to sync explicitly
    #      requested sounds but never enough to invent a center zoom.
    #   2. nothing at all -> place it and say plainly what was NOT synced.
    #
    # It never claims a click it was not told about.
    supplied = None
    if click_times is not None:
        if not isinstance(click_times, list):
            return ("REJECTED: click_times must be a list of seconds INTO "
                    "THE CLIP (0 = its first frame) — the moments the mouse "
                    "was pressed. Clicks cannot be seen in the pixels, so if "
                    "the user made this recording themselves, ask them when "
                    "the clicks were, or omit click_times.")
        supplied = []
        for i, t in enumerate(click_times):
            try:
                supplied.append(round(max(0.0, float(t)), 2))
            except (TypeError, ValueError):
                return (f"REJECTED: click_times[{i}] is not a number of "
                        "seconds.")
        supplied = sorted(set(supplied))
    # True when this is footage the USER supplied rather than a capture the
    # demo recorder made — decided BEFORE the synthesised events below, which
    # would otherwise make every clip look like a recorded demo.
    own_recording = not events
    if not events and supplied:
        # Synthesised events carry NO x/y — that absence is load-bearing
        # downstream, where a run with no positions becomes an eased punch
        # rather than a path to a made-up coordinate.
        events = [{"kind": "click", "t": t} for t in supplied]
    try:
        strength = round(min(max(float(zoom_strength), ZOOM_STRENGTH_MIN),
                             ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return "REJECTED: zoom_strength must be a number (0.05-4.5)."

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

    if not events:
        # Placed, and NOTHING claimed about clicks. The clip is in the edit —
        # which is what the user asked for — and the reply says exactly which
        # half of the treatment did not happen and how to get it.
        return (placed + "\nThe clip is in the edit, but there was nothing to "
                "sync to: this capture carries no click track (it was not "
                "made by record_website_demo), so NO zooms and NO click "
                "sounds were added. Do not tell the user the clicks were "
                "sounded or zoomed. To finish the treatment, either ask them "
                "WHEN the clicks happen and call showcase_demo again with "
                "click_times=[...] (seconds into the clip), or use look_at on "
                "the clip and place the movement yourself with add_zoom_path. "
                "If the pointer is hard to see, enhance_cursor makes it "
                "bigger and steadier.")

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

    # Positioned clicks drive a TRAVELLING zoom. Positionless clicks never
    # drive a zoom: timing is not spatial evidence, and a center punch can
    # magnify empty UI while the actual interaction sits at an edge.
    clicks = [e for e in events if e.get("kind") == "click"
              and "x" in e and "y" in e]
    blind_clicks = [e for e in events if e.get("kind") == "click"
                    and not ("x" in e and "y" in e)]
    if zooms and not clicks and blind_clicks:
        notes.append(
            "no zooms were added for the supplied click times: they say WHEN "
            "but not WHERE the interaction is. Inspect the clip and use "
            "add_zoom_path with measured coordinates if movement is wanted")
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
                item_z["target_measured"] = True
            zlist.append(item_z)
            made_zooms += 1

    if click_sounds:
        for ev in events:
            kind = ev.get("kind")
            # Literal UI feedback on the demo's own interaction events —
            # fixed known-good R2 objects (the migrated legacy-sfx files),
            # not project assets, and the user turns them off with
            # click_sounds=false.
            if kind == "click":
                key, gain = "legacy-sfx/click.wav", -13.0
            elif kind == "nav":
                key, gain = "legacy-sfx/pop.wav", -16.0
            elif kind == "scroll":
                key, gain = "legacy-sfx/swipe.wav", -20.0
            else:
                continue
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
    moved = "gliding" if clicks else "eased"
    written = ctx.write_edl(
        edl, f"cut the demo like a product video: {made_zooms} {moved} "
             f"zoom(s) onto the clicks and {made_sfx} synced sound(s) across "
             f"{base}-{demo_end}s")
    if not written.startswith("EDL v"):
        # Placed but nothing synced (every event fell outside the window).
        # Say so rather than returning a no-op diff the agent would read as
        # success.
        return (placed + "\nThe clip is placed, but no zoom or sound was "
                "actually added"
                + (" — " + "; ".join(notes) if notes else "")
                + ". Do not claim the clicks were zoomed or sounded.")
    extra = ("\nNOTE: " + "; ".join(notes) + "." if notes else "")
    return (placed + "\n" + written + extra
            + f"\nThe demo runs {base}-{demo_end}s in the edit. Tell the user "
            f"what it shows, and that {made_zooms} zoom(s) and {made_sfx} "
            "click sound(s) were synced — those numbers, not \"the clicks\". "
            "Adjust any single zoom with remove_zoom / add_zoom."
            + ("\nThis was the user's own recording, not a capture I made: "
               "everything above came from the click times they gave, and "
               "nothing was assumed about the rest of it."
               if own_recording else ""))


# ------------------------------------------------------------------ #
#  META tools                                                          #
# ------------------------------------------------------------------ #

def _compact_edl(row, ctx):
    edl = row["json"]
    collection_names = (
        "keep", "inserts", "music", "voiceover", "sfx", "overlays",
        "texts", "speed", "volume")
    duplicates = {}
    for coll in ("inserts", "overlays"):
        for item in edl.get(coll) or []:
            key = item.get("asset_key")
            if key:
                duplicates.setdefault(key, []).append(
                    f"{coll}:{item.get('id', '?')}")
    duplicates = {k: uses for k, uses in duplicates.items() if len(uses) > 1}
    caps = edl.get("captions")
    if isinstance(caps, dict):
        cap_summary = {
            "mode": caps.get("mode"),
            "design_version": caps.get("design_version"),
            "style": caps.get("style"),
            "max_words_per_caption": caps.get("max_words_per_caption"),
            "placement_spans": len(caps.get("placement_track") or []),
            "emphasis_words": caps.get("emphasis_words"),
        }
    elif isinstance(caps, list):
        cap_summary = {"mode": "manual", "items": len(caps)}
    else:
        cap_summary = None
    return {
        "version": row["version"],
        "description": describe_edl(edl, ctx.duration),
        "program_map": _program_map(ctx, edl) or None,
        "program_duration_s": round(program_duration(edl), 3),
        "frame": edl.get("frame"),
        "master": edl.get("master"),
        "captions": cap_summary,
        "collection_counts": {
            name: len(edl.get(name) or []) for name in collection_names
        },
        "visual_asset_duplicates": duplicates,
        "available_sections": sorted(edl.keys()),
    }


def _ass_clock_seconds(value):
    try:
        h, m, s = str(value).strip().split(":")
        return round(int(h) * 3600 + int(m) * 60 + float(s), 3)
    except (TypeError, ValueError):
        return None


def audit_captions(ctx, offset=0, limit=80):
    """Compile and mechanically audit the caption track before/after render.

    This is deliberately independent of the vision critic. It reads the same
    ASS artifact ffmpeg burns, so event starts/ends, overlaps and transcript
    coverage are evidence—not a visual model's impression from nine tiles.
    """
    row = ctx.latest_edl()
    edl = row["json"]
    if not edl.get("captions"):
        return json.dumps({"version": row["version"],
                           "status": "no captions"}, indent=1)
    try:
        off = max(0, int(offset or 0))
        lim = min(200, max(1, int(limit or 80)))
    except (TypeError, ValueError):
        return "REJECTED: offset and limit must be integers."
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed") or [])
    video = ctx.index.get("video") or {}
    try:
        sw, sh = float(video.get("width") or 1920), \
                 float(video.get("height") or 1080)
    except (TypeError, ValueError):
        sw, sh = 1920.0, 1080.0
    frame = edl.get("frame") or {}
    play_res = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    ass_path = os.path.join(ctx.workdir, f"caption_audit_v{row['version']}.ass")
    try:
        built = caplib.build_ass(edl, ctx.index, tl, ass_path,
                                 play_res=play_res)
    except Exception as exc:
        return json.dumps({"version": row["version"],
                           "status": "compile failed",
                           "error": str(exc)[:300]}, indent=1)
    if not built:
        return json.dumps({"version": row["version"],
                           "status": "caption config produced no events"},
                          indent=1)
    events = []
    with open(ass_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("Dialogue:"):
                continue
            fields = line.split(",", 9)
            if len(fields) < 10:
                continue
            start = _ass_clock_seconds(fields[1])
            end = _ass_clock_seconds(fields[2])
            if start is None or end is None:
                continue
            try:
                layer = int(fields[0].split(":", 1)[1].strip())
            except (TypeError, ValueError, IndexError):
                layer = 0
            events.append({"start": start, "end": end, "layer": layer})
    grouped = {}
    for event in events:
        key = (event["start"], event["end"])
        grouped.setdefault(key, []).append(event["layer"])
    states = [{"start": s, "end": e, "duration_s": round(e - s, 3),
               "layers": sorted(layers)}
              for (s, e), layers in sorted(grouped.items())]
    overlaps = []
    for previous, current in zip(states, states[1:]):
        amount = round(previous["end"] - current["start"], 3)
        if amount > 0.015:
            overlaps.append({"first": [previous["start"], previous["end"]],
                             "second": [current["start"], current["end"]],
                             "overlap_s": amount})
    caps = edl.get("captions")
    warnings = []
    uncovered = []
    first_late = None
    if isinstance(caps, dict) and caps.get("mode") == "from_transcript":
        words = [w for w in tl.kept_words(ctx.index.get("words") or [])
                 if not (w.get("filler") if isinstance(w, dict) else False)]
        mutes = [(float(a), float(b)) for a, b in
                 (edl.get("caption_mutes") or [])]
        for word in words:
            mid = (float(word["t0"]) + float(word["t1"])) / 2.0
            if any(a <= mid <= b for a, b in mutes):
                continue
            if not any(state["start"] - 0.011 <= mid <= state["end"] + 0.011
                       for state in states):
                uncovered.append({"word": word.get("w"),
                                  "at": round(mid, 3)})
        if words and states:
            first_late = round(max(0.0, states[0]["start"] -
                                   float(words[0]["t0"])), 3)
            if first_late > 0.08:
                warnings.append(
                    f"first caption starts {first_late:.3f}s after first kept word")
        if uncovered:
            warnings.append(f"{len(uncovered)} spoken word(s) lack caption coverage")
    if overlaps:
        warnings.append(f"{len(overlaps)} distinct visual caption state overlap(s)")
    zero_or_negative = [s for s in states if s["duration_s"] <= 0.01]
    if zero_or_negative:
        warnings.append(f"{len(zero_or_negative)} zero/negative caption state(s)")
    # Exact pixels at state changes are the high-information caption review,
    # unlike a uniform 3x3 overview. The current preview is accepted only if
    # it belongs to this exact immutable EDL version.
    candidates = []
    if states:
        stride = max(1, len(states) // 12)
        candidates.extend(s["start"] + min(0.06, s["duration_s"] / 2)
                          for s in states[::stride])
        candidates.extend((o["second"][0] + 0.01) for o in overlaps[:4])
    qa_times = sorted({round(min(max(t, 0.0), tl.out_duration), 3)
                       for t in candidates})[:16]
    render_asset = ctx.db.run(dbx.find_render_asset, ctx.project_id,
                              "preview", row["version"])
    result = {
        "version": row["version"],
        "status": "pass" if not warnings else "warnings",
        "compiler": "same ASS artifact used by ffmpeg",
        "caption_design_version": (caps.get("design_version")
                                   if isinstance(caps, dict) else None),
        "visual_state_count": len(states),
        "first_state": states[0] if states else None,
        "last_state": states[-1] if states else None,
        "first_caption_late_by_s": first_late,
        "uncovered_words": uncovered[:20],
        "uncovered_word_count": len(uncovered),
        "overlaps": overlaps[:20],
        "warnings": warnings,
        "qa_output_times": qa_times,
        "rendered_preview_available": bool(render_asset),
        "next": ("Call look_at(rendered=true, output_times=qa_output_times) "
                 "to inspect the actual caption pixels at phrase/word changes."
                 if render_asset else
                 "Call render_preview, then audit_captions again and inspect "
                 "qa_output_times with look_at(rendered=true, ...)."),
        "event_page": states[off:off + lim],
        "pagination": {"offset": off, "returned": len(states[off:off + lim]),
                       "total": len(states),
                       "next_offset": (off + len(states[off:off + lim])
                                       if off + len(states[off:off + lim]) < len(states)
                                       else None)},
    }
    return json.dumps(result, indent=1)


_EDL_SECTION_ALIASES = {
    "segments": ("keep",), "cuts": ("keep",), "text": ("texts",),
    "zooms": ("effects",), "transitions": ("effects",),
    "grades": ("effects",), "fades": ("effects",),
    "audio": ("music", "volume", "voiceover", "sfx", "stem_mix",
              "master"),
}
_EDL_OVERVIEW_ALIASES = {"program", "overview", "summary"}


def get_edl(ctx, sections=None, compact=False, offset=0, limit=100):
    """Current EDL without ever returning amputated/invalid JSON.

    Large timelines default to a compact index. Callers can then request one
    or more top-level sections, with list pagination. This replaces the old
    character slice that often cut the JSON in the middle of the exact
    captions/overlays collection an MCP caller needed to repair.
    """
    row = ctx.latest_edl()
    edl = row["json"]
    try:
        off = max(0, int(offset or 0))
        lim = min(200, max(1, int(limit or 100)))
    except (TypeError, ValueError):
        return "REJECTED: offset and limit must be integers."
    if sections is not None and not isinstance(sections, (list, tuple, str)):
        return ("REJECTED: sections must be a section name or array of names "
                f"from {sorted(edl.keys())}.")
    requested = ([sections] if isinstance(sections, str)
                 else list(sections or []))
    wanted, resolved = [], {}
    overview = False
    unknown = []
    canonical_lc = {str(key).lower(): key for key in edl}
    for raw in requested:
        label = str(raw).strip()
        low = label.lower()
        if low in canonical_lc:
            names = (canonical_lc[low],)
        elif low in _EDL_SECTION_ALIASES:
            names = tuple(n for n in _EDL_SECTION_ALIASES[low] if n in edl)
            resolved[label] = list(names)
        elif low in _EDL_OVERVIEW_ALIASES:
            overview = True
            resolved[label] = ["compact_overview"]
            continue
        else:
            unknown.append(label)
            continue
        for name in names:
            if name not in wanted:
                wanted.append(name)
    unknown = sorted(set(unknown))
    if unknown:
        return (f"REJECTED: unknown EDL section(s) {unknown}. Available: "
                f"{sorted(edl.keys())}. Accepted aliases: "
                f"{sorted(set(_EDL_SECTION_ALIASES) | _EDL_OVERVIEW_ALIASES)}.")
    if compact:
        return json.dumps(_compact_edl(row, ctx), indent=1)
    if wanted or overview:
        selected, pages = {}, {}
        for raw_name in wanted:
            name = str(raw_name)
            value = edl.get(name)
            if isinstance(value, list):
                selected[name] = value[off:off + lim]
                pages[name] = {"offset": off,
                               "returned": len(selected[name]),
                               "total": len(value),
                               "next_offset": (off + len(selected[name])
                                               if off + len(selected[name]) < len(value)
                                               else None)}
            else:
                selected[name] = value
        payload = {"version": row["version"], "sections": selected,
                   "pagination": pages}
        if overview:
            payload["overview"] = _compact_edl(row, ctx)
        if resolved:
            payload["aliases_resolved"] = resolved
        rendered = json.dumps(payload, indent=1)
        if len(rendered) > 21000:
            return json.dumps({
                "version": row["version"],
                "error": ("Requested page is too large for a reliable tool "
                          "response; request fewer sections or a smaller limit."),
                "requested_sections": wanted,
                "suggested_limit": max(1, lim // 2),
            }, indent=1)
        return rendered
    rendered = json.dumps(edl, indent=1)
    if len(rendered) <= 19000:
        header = {"version": row["version"],
                  "description": describe_edl(edl, ctx.duration),
                  "edl": edl}
        return json.dumps(header, indent=1)
    compact_payload = _compact_edl(row, ctx)
    compact_payload["notice"] = (
        "Full EDL is large, so this is a complete compact index—not truncated "
        "JSON. Call get_edl(sections=['captions']) or another named section; "
        "use offset/limit for long list sections.")
    return json.dumps(compact_payload, indent=1)


def _grade_chain_of(edl_json):
    """The global color chain this EDL renders with, as one filter string —
    "" when it has none. Preset first, then custom, matching the graph."""
    fx = edl_json.get("effects") or {}
    parts = []
    g = fx.get("grade")
    if g and g in renderer.GRADE_FILTERS:
        parts.append(renderer.GRADE_FILTERS[g])
    gc = fx.get("grade_custom") or {}
    if gc:
        c = renderer.grade_custom_chain(gc)
        if c:
            parts.append(c)
    return ",".join(parts)


def _grade_strip_shortcut(ctx, row):
    """A ~2s grade contact strip instead of a full render, when the ONLY
    change since the last render is the global color — round 91.

    A real turn spent 409s running FIVE full preview renders of the same
    80s program to tune a look (job 2647): every iteration re-encoded every
    frame to change a per-pixel color map, then the agent read six tiles off
    the result. This pulls the same six program frames straight from the
    proxy, applies the NEW color chain to the stills, and hands them to the
    agent's eyes — the judgement is identical, the cost is two seconds.

    Returns the tool-result string when the strip was delivered, None to fall
    through to the real render. The full preview still happens exactly once:
    either the model calls render_preview again without touching the color
    (settled — same chain twice means "show me the real thing"), or the
    turn-end auto-render covers it. Never raises."""
    try:
        if getattr(ctx, "autorendering", False):
            return None      # the turn-end render is FOR THE USER — always real
        if not (getattr(ctx, "sight_out", False)
                or (getattr(ctx, "direct_sight", False)
                    and llm.agent_sees(ctx.agent_model))):
            return None                      # a blind agent needs real renders
        if ctx.strip_count >= 8:
            return None
        prev_v = (ctx.last_preview or {}).get("edl_version")
        if prev_v is None:
            prev_v = ctx.db.run(dbx.latest_render_version, ctx.project_id,
                                "preview")
        if prev_v is None:
            return None
        prev = ctx.db.run(dbx.get_edl_version, ctx.project_id, int(prev_v))
        if not prev or not edl_diff.color_only_change(prev["json"],
                                                      row["json"]):
            return None
        chain = _grade_chain_of(row["json"])
        if chain == ctx.last_strip_chain:
            return None                      # settled: give them the render
        edl = row["json"]
        keep = edl.get("keep") or []
        if not keep:
            return None                      # canvas program: renders only
        tl = Timeline(keep, edl.get("inserts") or [], edl.get("speed") or [])
        if tl.out_duration <= 0.2:
            return None
        proxy = ctx.proxy_path()
        n = 6
        picks = []                           # (out_t, src_t)
        for i in range(n):
            t = tl.out_duration * (i + 0.5) / n
            s = tl.out_to_src(t)
            if s is not None:                # None = inside a spliced insert
                picks.append((t, s))
        if len(picks) < 3:
            return None                      # mostly inserts: renders only
        frames, labels = [], []
        for i, (t, s) in enumerate(picks):
            raw = os.path.join(ctx.workdir, f"gstrip_raw_{i}.jpg")
            out = os.path.join(ctx.workdir,
                               f"gstrip_{ctx.strip_count}_{i}.jpg")
            try:
                media.frame_at(proxy, s, raw)
                if chain:
                    subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", raw,
                         "-vf", chain, "-frames:v", "1", out],
                        check=True, capture_output=True, timeout=20)
                else:
                    out = raw                # colors removed: show it clean
            except Exception:
                continue
            frames.append(out)
            labels.append(f"@out {t:.1f}s")
        if len(frames) < 3:
            return None
        delivered = _deliver_frames(
            ctx, frames, labels, "",
            "GRADE CONTACT STRIP — program frames with the NEW color applied")
        ctx.last_strip_chain = chain
        ctx.strip_count += 1
        return (
            "GRADE CONTACT STRIP (~2s) instead of a full render: only the "
            "color changed since the last render, so these are the same "
            "program moments with the NEW grade applied. Judge and adjust "
            "the color from the tiles — every adjustment gets a fresh strip "
            "in seconds, where a full render takes minutes. The full "
            "preview is NOT rendered yet: when the color is right, just "
            "reply to the user and the full preview renders automatically "
            "at the end of your turn (or call render_preview again without "
            "changing the color to render it now). " + delivered)
    except Exception as e:
        print(f"[gradestrip] fell back to a full render: {e}", flush=True)
        return None


GRADE_ONLY_TOOLS = ("set_color_grade", "apply_look", "set_grade_custom")

_CHANGE_CHECK_PAD_S = 0.75
_CHANGE_CHECK_POINT_S = 2.5
_CHANGE_CHECK_MAX_WINDOW_S = 8.0
_CHANGE_CHECK_MAX_TOTAL_S = 24.0
_CHANGE_CHECK_MAX_WINDOWS = 6


def _preview_baseline(ctx, row):
    """Last proof/complete state, then immediate prior EDL as first fallback.

    This makes each proof cover the latest unchecked EDL changes rather than
    accumulating everything since the last complete file. A brand-new project
    can still make a cheap first proof from v1 -> v2; it does not need to buy a
    full preview merely to establish a visual baseline.
    """
    try:
        candidates = [
            (getattr(ctx, "last_preview_check", None) or {}).get(
                "edl_version"),
            (getattr(ctx, "last_preview", None) or {}).get("edl_version"),
            ctx.db.run(dbx.latest_render_version, ctx.project_id, "preview"),
        ]
        candidates = [int(v) for v in candidates if v is not None]
        prev_v = max(candidates) if candidates else None
        if prev_v is not None:
            if int(prev_v) == int(row["version"]):
                return None
            previous = ctx.db.run(dbx.get_edl_version, ctx.project_id,
                                  int(prev_v))
            if previous:
                return previous
        return ctx.db.run(dbx.previous_edl_version, ctx.project_id,
                          int(row["version"]))
    except Exception:
        return None


def _merge_check_ranges(ranges, duration):
    merged = []
    for a, b in sorted(ranges):
        a = max(0.0, min(float(a), duration))
        b = max(a, min(float(b), duration))
        if b - a < 0.1:
            continue
        if merged and a <= merged[-1][1] + 0.2:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _sample_long_check_range(a, b):
    """Bound a broad changed span without pretending one tile proves it all."""
    length = b - a
    if length <= _CHANGE_CHECK_MAX_WINDOW_S:
        return [[a, b]]
    width = min(3.0, _CHANGE_CHECK_MAX_WINDOW_S)
    centers = [a + width / 2.0, (a + b) / 2.0, b - width / 2.0]
    return [[max(a, c - width / 2.0), min(b, c + width / 2.0)]
            for c in centers]


def _change_check_ranges(ctx, row, plan=None):
    """Bounded NEW-output windows affected since the last complete preview.

    Global changes (caption style, grade, master settings) technically affect
    the whole program; iteration still needs evidence, not another full file,
    so sample representative windows plus any exact verify-plan moments. The
    complete turn-end/readiness preview remains the exhaustive proof.
    """
    prev = _preview_baseline(ctx, row)
    if not prev:
        return [], None
    try:
        edl = row["json"]
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        duration = float(tl.out_duration)
        if duration <= 0.15:
            return [], prev
        changed = edl_diff.change_ranges(prev["json"], edl) or {}
        raw = []
        for pair in (changed.get("out_ranges") or []):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            a, b = float(pair[0]), float(pair[1])
            if b - a < 0.05:
                half = _CHANGE_CHECK_POINT_S / 2.0
                raw.append([a - half, a + half])
            else:
                raw.extend(_sample_long_check_range(
                    a - _CHANGE_CHECK_PAD_S, b + _CHANGE_CHECK_PAD_S))
        if changed.get("global") or not raw:
            exact = [float(t) for t, _claim in (plan or [])]
            if not exact:
                n = min(4, max(1, int(round(duration / 6.0))))
                exact = [duration * (i + 0.5) / n for i in range(n)]
            half = min(1.25, max(0.5, duration / 12.0))
            raw.extend([[t - half, t + half] for t in exact])
        merged = _merge_check_ranges(raw, duration)
        # Prefer exact changed moments first and keep the proof reel bounded.
        selected, spent = [], 0.0
        for a, b in merged:
            if len(selected) >= _CHANGE_CHECK_MAX_WINDOWS:
                break
            room = _CHANGE_CHECK_MAX_TOTAL_S - spent
            if room < 0.5:
                break
            if b - a > room:
                mid = (a + b) / 2.0
                a, b = max(0.0, mid - room / 2.0), \
                    min(duration, mid + room / 2.0)
            selected.append([round(a, 3), round(b, 3)])
            spent += b - a
        return selected, prev
    except Exception:
        return [], prev


def _run_changed_preview_check(ctx, row, plan, ranges):
    """Render/wait for a short proof reel without replacing Studio preview."""
    version = int(row["version"])
    if version in ctx.checked_versions:
        return (f"Changed sections of EDL v{version} were already rendered "
                "and checked. Keep editing, or call "
                "render_preview(complete=true) once the edit is ready.")
    payload = {"edl_version": version, "check_ranges": ranges,
               "source": "agent_preview_check",
               "agent_job_id": ctx.job["id"]}
    if plan:
        payload["verify_times"] = [t for t, _ in plan]
    job_id = getattr(ctx, "spec_preview_check_jobs", {}).get(version)
    if not job_id:
        job_id, _created = ctx.db.run(
            dbx.get_or_enqueue_preview_check_job, ctx.project_id,
            ctx.job["user_id"], payload)
    deadline = time.time() + min(config.PREVIEW_WAIT_TIMEOUT_S, 300.0)
    while time.time() < deadline:
        time.sleep(1)
        job = ctx.db.run(dbx.get_job, job_id)
        if job["state"] == "done":
            result = job.get("result") or {}
            if result.get("superseded_by"):
                return ("Changed-section proof was superseded by a newer EDL "
                        "version. Check that newer edit instead.")
            ctx.last_preview_check = result
            ctx.checked_versions.add(version)
            delivered = _queue_check_frames(ctx, result, plan)
            critic = _preview_critic_report(ctx, result, plan)
            if critic is not None:
                ctx.last_visual_critic = critic
            covered = result.get("changed_ranges") or ranges
            note = (f"Changed-section proof for EDL v{version} rendered "
                    f"{result.get('duration_s')}s across {covered}. Only "
                    "the affected seconds were encoded; this proof reel did "
                    "NOT replace the complete Studio preview.")
            if delivered:
                note += (" Inspect the attached changed-moment frames now. "
                         "If anything is wrong, repair the EDL and check the "
                         "new version; do not repeat the unchanged render.")
            if critic is not None:
                note += preview_critic.summary_line(critic)
            note += (" Continue iterating cheaply. When the edit is ready, "
                     "call render_preview(complete=true) exactly once to "
                     "produce the complete user preview.")
            return note
        if job["state"] == "failed":
            failure = dict(((job.get("result") or {}).get("failure") or {}))
            err = str(failure.get("error") or job.get("error")
                      or "unknown check error")[:500]
            return (f"Changed-section proof FAILED for v{version}: {err}. "
                    "The EDL remains saved. Use another inspection route or "
                    "repair a genuinely wrong edit; do not retry this same "
                    "proof unchanged.")
    return ("Changed-section proof is still running. Continue with work that "
            "does not depend on it; do not enqueue the same proof again.")


def speculative_preview(ctx):
    """Start a changed-section proof during the next model call.

    Speculation used to enqueue a complete preview for every intermediate EDL
    and merely bounded the waste to one version.  The cheap proof is now the
    only work allowed ahead of explicit readiness; the complete preview is
    produced once by the turn-end honesty pass.
    """
    if not config.SPECULATIVE_PREVIEWS:
        return
    if ctx.write_calls and ctx.write_calls[-1] in GRADE_ONLY_TOOLS:
        return
    row = ctx.latest_edl()
    version = row["version"]
    if version in ctx.rendered_versions or version in ctx.spec_enqueued:
        return
    if len(ctx.spec_enqueued) >= config.SPECULATIVE_PREVIEWS_MAX:
        return
    plan = _verify_plan_for(ctx, row)
    ranges, _baseline = _change_check_ranges(ctx, row, plan)
    if not ranges:
        return
    payload = {"edl_version": version, "check_ranges": ranges,
               "source": "agent_preview_check",
               "agent_job_id": ctx.job["id"]}
    if plan:
        payload["verify_times"] = [t for t, _ in plan]
    job_id, _created = ctx.db.run(
        dbx.get_or_enqueue_preview_check_job, ctx.project_id,
        ctx.job["user_id"], payload)
    if not hasattr(ctx, "spec_preview_check_jobs"):
        ctx.spec_preview_check_jobs = {}
    ctx.spec_preview_check_jobs[version] = job_id
    ctx.spec_enqueued.add(version)



def render_preview(ctx, complete=False):
    row = ctx.latest_edl()
    version = row["version"]
    complete = bool(complete) or bool(getattr(ctx, "autorendering", False))
    if version in ctx.rendered_versions and \
            (ctx.last_preview or {}).get("edl_version") == version:
        return (f"Preview v{version} is already rendered and attached — "
                "no need to render again.")
    prior_failure = ctx.failed_preview_versions.get(version)
    if prior_failure:
        return _failed_preview_message(version, prior_failure, repeated=True)
    strip = None if complete else _grade_strip_shortcut(ctx, row)
    if strip:
        return strip
    # Round 81: name the output seconds this edit changed, so the render job
    # can pull a frame at each and the self-check can review the CLAIM
    # ("this should read X, behind the person") instead of nine even samples
    # of the whole programme that the edit may not even appear in.
    plan = _verify_plan_for(ctx, row)
    if not complete:
        ranges, _baseline = _change_check_ranges(ctx, row, plan)
        if ranges:
            return _run_changed_preview_check(ctx, row, plan, ranges)
    # Adopt the speculative encode of this exact version when one is already
    # queued/running (round 98) — same payload shape, same verify plan,
    # half the wait and none of the double cost.
    payload = {"edl_version": version, "source": "agent_preview",
               "agent_job_id": ctx.job["id"]}
    if plan:
        payload["verify_times"] = [t for t, _ in plan]
    job_id = ctx.spec_preview_jobs.get(version)
    if not job_id:
        job_id, _created = ctx.db.run(
            dbx.get_or_enqueue_preview_job, ctx.project_id,
            ctx.job["user_id"], payload)
    deadline = time.time() + config.PREVIEW_WAIT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(1)
        j = ctx.db.run(dbx.get_job, job_id)
        if j["state"] == "done":
            result = j.get("result") or {}
            ctx.last_preview = result
            ctx.rendered_versions.add(version)
            # Review state is evidence about one exact render.  Never let a
            # prior version's PASS/FIX survive when this reviewer is
            # unavailable or the new EDL no longer has designed audio.
            ctx.last_visual_critic = None
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
            # THE SELF-CHECK IS THE AGENT'S OWN EYES (round 84). On a real
            # (non-cached) render the frames of the changed moments + the
            # whole-video sheet are handed straight into the agent's context
            # — the editor reviews its own work directly and only finishes
            # when it has SEEN the edit is right. A blind agent model falls
            # back to the separate vision reviewer exactly as before.
            # A cached render may predate the independent reviewer entirely.
            # Its overview sheet is still real evidence, so inspect it rather
            # than assuming "reviewed when made" for legacy output.
            delivered = _queue_check_frames(ctx, result, plan)
            if delivered:
                claims = ""
                if plan:
                    claims = (" CHECK EACH CLAIM against its numbered tile: "
                              + " ".join(f"Tile {i + 1} ({t:.1f}s): {c}."
                                         for i, (t, c) in enumerate(plan)))
                note += (" The frames follow this message — LOOK AT THEM "
                         "YOURSELF before replying: the numbered tiles are "
                         "the exact moments this edit changed, the 3x3 "
                         "sheet is the whole video." + claims +
                         " If a change did not land or something looks "
                         "broken, fix that exact item and re-render — only "
                         "reply once what you SEE matches what you claim. "
                         "If a fix doesn't land, change the diagnosis or "
                         "the tool and come at it a different way — never "
                         "repeat the exact call that just failed, and never "
                         "give up while a genuinely different approach "
                         "remains.")
            # The in-house editor gets an independent second opinion. MCP is
            # different: its outside editing model already receives these
            # exact frames as image content, so paying a Valmera-funded model
            # to inspect them again would duplicate the caller's own vision.
            critic_report = _preview_critic_report(ctx, result, plan)
            critic_repairs = preview_critic.repair_lines(critic_report)
            if critic_report is not None:
                ctx.last_visual_critic = critic_report
                note += preview_critic.summary_line(critic_report)
            # The old generic reviewer remains only as an availability
            # fallback when neither the independent critic nor direct sight
            # could inspect the render.
            check = _preview_fallback_check(
                ctx, result, plan, delivered, critic_report)
            if check:
                ctx.last_selfcheck = check
                note += f" Visual self-check: {check}"
                if plan and "all landed" not in check.lower() \
                        and "looks clean" not in check.lower():
                    note += (" If a claim FAILED, fix that exact item and "
                             "re-render to confirm — and if it fails again, "
                             "attack it a different way (another tool, "
                             "another diagnosis) rather than repeating the "
                             "same call or giving up.")
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
            repetition_repairs = []
            try:
                edl = row["json"]
                tl = Timeline(edl["keep"], edl.get("inserts") or [],
                              edl.get("speed") or [])
                reps = classify_repeated_phrases(
                    tl.kept_words(ctx.index.get("words", [])))
                dupes = [r for r in reps if r["kind"] == "edit_duplicate"]
                spoken = [r for r in reps if r["kind"] == "spoken_repetition"]
                if dupes:
                    flagged = "; ".join(
                        f"'{r['phrase']}' at " + ", ".join(
                            f"{o['program_s']}s" for o in r["occurrences"])
                        for r in dupes[:4])
                    note += (f" EDIT-DUPLICATION AUDIT: the output reuses "
                             f"{flagged} — verify with get_kept_transcript "
                             "and remove the duplicated source segment.")
                    repetition_repairs.append(
                        "edit duplication: same source phrase appears twice")
                if spoken:
                    note += (f" SPOKEN-REPETITION NOTE: {len(spoken)} phrase(s) "
                             "repeat at distinct source moments; this is not "
                             "an edit-duplication defect and should not be cut "
                             "automatically.")
            except Exception:
                pass
            # Taste audit (round 52): the craft reviewer. Everything above
            # asks whether the edit is CORRECT; this asks whether it is any
            # GOOD, which is the difference between an edit that renders and
            # an edit someone wants to post. It runs on a REAL render only —
            # a cached one was reviewed when it was made.
            try:
                edl = row["json"]
                tl = Timeline(edl["keep"], edl.get("inserts") or [],
                              edl.get("speed") or [])
                findings = taste.critique(
                    edl, ctx.index, tl,
                    src_w=(ctx.index.get("video") or {}).get("width"),
                    src_h=(ctx.index.get("video") or {}).get("height"),
                    user_asked=ctx.user_message or "")
                # Published to the loop as well as printed here: a finding the
                # model can read and skip past is not a review.
                ctx.last_taste = (list(findings) + critic_repairs +
                                  repetition_repairs)
                ctx.last_taste_version = row.get("version")
                note += taste.audit_line(findings)
            except Exception:
                # Visual review must still block a bad handoff when a
                # deterministic taste rule itself happens to fail.
                ctx.last_taste = list(critic_repairs) + repetition_repairs
                ctx.last_taste_version = row.get("version")
            # The SOUND side stays deterministic: render audio_qc measures the
            # actual mix without asking a second model to listen on the
            # editor's behalf.
            aq = result.get("audio_qc") or {}
            aqf = aq.get("findings") or []
            ctx.last_audio_qc_findings = list(aqf[:4])
            if aqf:
                ctx.last_taste.extend(
                    f"audio QC: {finding}" for finding in aqf[:4])
                note += (" AUDIO CHECK: " + "; ".join(aqf[:4])
                         + " — fix these, or keep one deliberately and say "
                           "why in one clause.")
            note += audio_qc.summary_line(aq)
            return note
        if j["state"] == "failed":
            failure = dict(((j.get("result") or {}).get("failure") or {}))
            failure.setdefault("error", j.get("error") or "unknown render error")
            failure.setdefault("kind", "unknown")
            failure.setdefault("agent_repairable", False)
            ctx.failed_preview_versions[version] = failure
            ctx.last_preview_failure = failure
            return _failed_preview_message(version, failure)
    return ("Preview render is taking too long — it may still finish and "
            "attach to the chat. Summarize your edit for the user now.")


def _failed_preview_message(version, failure, repeated=False):
    err = str((failure or {}).get("error") or "unknown render error")[:500]
    repairable = bool((failure or {}).get("agent_repairable"))
    if repairable:
        lead = "The same failed version was NOT re-enqueued" if repeated else \
            "This version will NOT be retried unchanged"
        return (
            f"Preview render FAILED: v{version}: {err}. {lead}. "
            f"Inspect v{version} with get_edl, correct the invalid or too-"
            "expensive part with an editing tool so it creates a NEW EDL "
            f"version (v{version + 1} or later), then render that new version "
            "once. Do not call render_preview on this unchanged version.")
    return (
        f"Preview render FAILED: v{version}: {err}. The failure is not "
        "classified as an EDL defect, so do not rewrite the user's edit or "
        "blindly re-enqueue it. Tell the user the edit is saved and the "
        "render service needs another attempt later.")


def _frame_context(edl):
    """One sentence of output-frame context for vision prompts, so letterbox
    bars on pad renders don't read as 'broken black frames'."""
    frame = (edl or {}).get("frame") or {}
    ratio, mode = frame.get("ratio"), frame.get("mode")
    if not ratio:
        return ""
    mixed = {sp.get("mode") for sp in (frame.get("focus_track") or [])
             if sp.get("mode") and sp.get("mode") != mode}
    if mixed:
        return (f"The output frame is {ratio} with measured per-shot fit: "
                "speaker shots may crop tightly, while unmeasured/wide "
                "shots use pad_blur to preserve the whole composition. "
                "Blurred bars on those spans are EXPECTED; a blank/irrelevant "
                "crop is not. ")
    if mode in ("pad", "pad_blur"):
        bg = "blurred" if mode == "pad_blur" else "solid black"
        return (f"The output frame is {ratio} letterboxed ({bg} bars around "
                f"a smaller image are EXPECTED and are NOT broken frames; "
                f"dark footage can make whole thumbnails look near-black). ")
    return f"The output frame is tightly center-cropped to {ratio}. "


def _verify_plan_for(ctx, row):
    """[(output_second, claim)] for what this render changes vs the render
    the user last saw — round 81. Baseline is the newest previewed version
    (this turn's, else the project's), because claims are only meaningful
    against the picture the user is still watching. Never raises; [] means
    the self-check falls back to the whole-video sheet alone."""
    try:
        prev_v = (ctx.last_preview or {}).get("edl_version")
        if prev_v is None:
            prev_v = ctx.db.run(dbx.latest_render_version, ctx.project_id,
                                "preview")
        if prev_v is None or int(prev_v) == int(row["version"]):
            return []
        prev = ctx.db.run(dbx.get_edl_version, ctx.project_id, int(prev_v))
        if not prev:
            return []
        return edl_diff.verify_plan(prev["json"], row["json"])
    except Exception:
        return []


def _queue_check_frames(ctx, result, plan=None):
    """Round 84: put the render's OWN frames in front of the agent.

    Downloads the verify sheet (numbered tiles of the exact output moments
    this edit changed) and the whole-video result sheet, and queues them on
    ctx.pending_images — the loop injects them right after this tool result,
    so the editor reviews its own render with its own eyes. Returns True
    when at least one picture was queued; False falls back to the separate
    vision reviewer (blind agent model, missing sheets, storage hiccup)."""
    can_see = getattr(ctx, "sight_out", False) or \
        (getattr(ctx, "direct_sight", False)
         and llm.agent_sees(ctx.agent_model))
    if not can_see:
        return False
    queued = 0
    vkey = result.get("verify_sheet_key")
    if plan and vkey:
        vlocal = os.path.join(ctx.workdir,
                              f"verify_own_{uuid.uuid4().hex[:8]}.jpg")
        try:
            storage.download_to(vkey, vlocal)
            ctx.pending_images.append(
                ("RENDER CHECK — the exact moments this edit changed, one "
                 "numbered tile per claim", vlocal))
            queued += 1
        except Exception as e:
            print(f"[render] verify sheet fetch failed: {e}", flush=True)
    ckey = result.get("caption_sheet_key")
    if ckey:
        clocal = os.path.join(ctx.workdir,
                              f"caption_own_{uuid.uuid4().hex[:8]}.jpg")
        try:
            storage.download_to(ckey, clocal)
            ctx.pending_images.append(
                ("CAPTION QA — up to 16 real rendered caption-state changes "
                 "sampled across the edit; inspect timing, clipping, placement "
                 "and missing/overlapping words", clocal))
            queued += 1
        except Exception as e:
            print(f"[render] caption sheet fetch failed: {e}", flush=True)
    skey = result.get("sheet_key")
    if skey:
        slocal = os.path.join(ctx.workdir,
                              f"result_own_{uuid.uuid4().hex[:8]}.jpg")
        try:
            storage.download_to(skey, slocal)
            ctx.pending_images.append(
                ("RENDER OVERVIEW — 3x3 sheet sampled evenly across the "
                 "whole rendered video", slocal))
            queued += 1
        except Exception as e:
            print(f"[render] result sheet fetch failed: {e}", flush=True)
    return queued > 0


def _independent_preview_review(ctx, result, plan=None):
    """Fresh critic over edited output plus a small raw-source comparison.

    This is deliberately independent of ``ctx.pending_images``: those pixels
    go back into the authoring conversation, while these go into a stateless,
    tool-free call with an adversarial rubric. Missing storage or vision is an
    honest no-review, never a failed preview.
    """
    if not llm.vision_available():
        return None
    images, labels = [], []

    def _download(key, stem, label):
        if not key:
            return
        local = os.path.join(
            ctx.workdir, f"critic_{stem}_{uuid.uuid4().hex[:8]}.jpg")
        try:
            storage.download_to(key, local)
        except Exception as exc:
            print(f"[critic] image fetch skipped ({key}): {exc}", flush=True)
            return
        images.append(local)
        labels.append(label)

    _download(result.get("sheet_key"), "overview",
              "EDITED RENDER overview, a timestamped 3x3 sample")
    if plan:
        _download(result.get("verify_sheet_key"), "changed",
                  "EDITED RENDER changed moments, one numbered tile per claim")
    _download(result.get("caption_sheet_key"), "captions",
              "EDITED RENDER caption QA, up to 16 exact caption-state changes")

    # Compare changed moments to their corresponding RAW source tiles. First
    # and last tiles are useful for a generic overview, but on an 85-minute
    # upload they say nothing about a crop at minute 29—the exact blind spot
    # that let the authoring model share its own mistaken assumption with the
    # old reviewer. Keep this bounded to four cached JPEGs.
    raw_keys = list((ctx.index or {}).get("tile_keys") or [])
    if raw_keys:
        pick_indexes = []
        try:
            step = float((ctx.index or {}).get("tile_step_s") or 0.0)
            edl_now = ctx.latest_edl()["json"]
            tl_now = Timeline(edl_now.get("keep") or [],
                              edl_now.get("inserts") or [],
                              edl_now.get("speed") or [])
            if step > 0:
                for output_t, _claim in (plan or []):
                    source_t = tl_now.out_to_src(float(output_t))
                    if source_t is None:
                        continue
                    idx = min(max(int(source_t / (step * 4.0)), 0),
                              len(raw_keys) - 1)
                    if idx not in pick_indexes:
                        pick_indexes.append(idx)
        except Exception:
            pick_indexes = []
        if not pick_indexes:
            pick_indexes = [0]
            if len(raw_keys) > 1:
                pick_indexes.append(len(raw_keys) - 1)
        for i, idx in enumerate(pick_indexes[:4], 1):
            key = raw_keys[idx]
            _download(key, f"raw{i}",
                      f"RAW SOURCE filmstrip around changed moment {i}, "
                      "with timestamped source frames")
    if not images:
        return None

    try:
        edl = ctx.latest_edl()["json"]
    except Exception:
        edl = {}
    lines = [
        "The rendered file completed and passed deterministic duration and "
        "broad black-frame verification before these sheets were created. "
        "A black/missing/continuity claim still needs an exact tile/time and "
        "high confidence; sparse-sheet uncertainty is not a repair order.",
        f"User request: {(ctx.user_message or '')[:1000]}",
        f"Output duration: {result.get('duration_s')}s; raw source: "
        f"{getattr(ctx, 'duration', None)}s.",
        _frame_context(edl),
    ]
    edit_plan = getattr(ctx, "edit_plan", None) or {}
    if edit_plan:
        lines.append("Recorded direction: " +
                     str(edit_plan.get("brief") or "")[:300])
        anchors = "; ".join(
            x for x in (
                f"format={edit_plan.get('format')}"
                if edit_plan.get("format") else "",
                f"intent={edit_plan.get('intent')}"
                if edit_plan.get("intent") else "",
                f"style={edit_plan.get('style_family')}"
                if edit_plan.get("style_family") else "",
                ("must keep=" + ", ".join(edit_plan.get("must_keep") or []))
                if edit_plan.get("must_keep") else "",
                ("must avoid=" + ", ".join(edit_plan.get("must_avoid") or []))
                if edit_plan.get("must_avoid") else "",
            ) if x)
        if anchors:
            lines.append("Editorial anchors: " + anchors[:1000])
        lines.append("Planned moves: " + "; ".join(
            str(s)[:160] for s in (edit_plan.get("steps") or [])[:12]))
    if plan:
        lines.append("Changed-moment claims: " + "; ".join(
            f"tile {i + 1} at {t:.1f}s — {claim}"
            for i, (t, claim) in enumerate(plan)))
    fx = edl.get("effects") or {}
    zooms = fx.get("zooms") or []
    if zooms:
        lines.append("Authored zooms: " + "; ".join(
            f"{z.get('start')}-{z.get('end')}s aimed at "
            f"({z.get('cx')},{z.get('cy')}) mode={z.get('mode', 'punch')}"
            for z in zooms[:8]))
    caps = edl.get("captions") or {}
    if caps:
        lines.append("Caption layer: " + json.dumps(caps, default=str)[:900])
    return preview_critic.review(images, labels, "\n".join(x for x in lines if x))


def _preview_critic_report(ctx, result, plan=None):
    """Run paid second-opinion vision only for Valmera's in-house editor.

    MCP sets ``sight_out`` and receives the rendered sheets as image content
    in its own tool response. Its outside model is already the editor and the
    visual reviewer, so a second Valmera-funded call is pure duplication.
    """
    if getattr(ctx, "sight_out", False):
        return None
    return _independent_preview_review(ctx, result, plan)


def _preview_fallback_check(ctx, result, plan, delivered, critic_report):
    """Never route MCP pixels through a Valmera-funded fallback model."""
    if getattr(ctx, "sight_out", False):
        return None
    if delivered or critic_report is not None:
        return None
    return _self_check(ctx, result, plan)


def _self_check(ctx, result, plan=None):
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
        zoom_note = _aimed_zoom_note(edl)
    except Exception:
        frame_note = fx_note = zoom_note = ""
    # Round 81: when the render carried a verify sheet, the check reviews the
    # CLAIMS first — one numbered tile per changed moment, each with the
    # sentence that makes it falsifiable — and only then the generic damage
    # question. Same single vision call either way; a stale executor that
    # returned no verify sheet degrades to the sheet-only check unchanged.
    images, names = [local], [sheet_key]
    claims_note = ""
    vkey = result.get("verify_sheet_key")
    if plan and vkey:
        vlocal = os.path.join(ctx.workdir, "verify_sheet.jpg")
        try:
            storage.download_to(vkey, vlocal)
            images.append(vlocal)
            names.append(vkey)
            lines = " ".join(
                f"Tile {i + 1} ({t:.1f}s): {c}."
                for i, (t, c) in enumerate(plan))
            claims_note = (
                "IMAGE 2 samples ONLY the moments this edit CHANGED, one "
                "numbered tile per claim. Check each claim against its "
                f"tile: {lines} Answer per tile with LANDED or FAILED plus "
                "a few words (a FAILED verdict must say what the tile "
                "actually shows). Then review IMAGE 1. ")
        except Exception:
            pass
    sheet_intro = ("IMAGE 1 is" if claims_note else "This is") + \
        (" a 3x3 contact sheet sampled evenly from an automatically "
         "edited video.")
    return llm.ask_vision(
        frame_note + fx_note + zoom_note + claims_note + sheet_intro +
        " In one or two sentences: does anything look broken "
        "(unexpected black frames, half-cut faces mid-action, missing "
        "captions if text was expected)? Frames showing a DELIBERATE effect "
        "listed above are expected, not defects — but say so plainly if one "
        "of them looks OVERDONE for this footage (harsh, cheap, or so strong "
        "the shot underneath is lost). " +
        ("End with exactly 'all landed' if every numbered claim landed and "
         "nothing looks broken; otherwise name what failed." if claims_note
         else "If it looks fine, say 'looks clean'."),
        images, max_tokens=(320 if claims_note else 200),
        purpose="vision_selfcheck", image_names=names)


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


def _aimed_zoom_note(edl):
    """One line telling the self-check reviewer that a zoom is AIMED, and
    what a wrong aimed zoom looks like.

    Round 72: a launch-video render shipped a targeted zoom whose subject (a
    chat message) sat half-clipped at the frame edge while the shot centred
    on empty UI — and the self-check said 'looks clean', because nothing had
    told it a close-up was even intended there. Naming the window turns the
    generic damage question into the real one: is this tile a composed
    close-up of SOMETHING?"""
    fx = edl.get("effects") or {}
    zs = [z for z in (fx.get("zooms") or [])
          if z.get("cx") is not None or z.get("cy") is not None
          or z.get("path")]
    if not zs:
        return ""
    spans = "; ".join(f"{float(z['start']):.0f}-{float(z['end']):.0f}s"
                      for z in zs[:4])
    return (f"An AIMED zoom runs at {spans}: a frame inside those seconds "
            "should read as a deliberate close-up with its subject fully in "
            "frame — flag it if the subject is clipped at an edge or the "
            "close-up centres on empty space. ")


def ask_user(ctx, question):
    q = (question or "").strip()
    if not q:
        return "REJECTED: question is empty."
    raise AskUser(q[:600])


def read_skill(ctx, name):
    """Load one of the on-demand playbooks (worker/skills/*.md) into the
    turn. The catalog in the system prompt names them; content arrives when
    it is relevant instead of riding in every request."""
    import agent_prompt
    text = agent_prompt.read_skill_text(name)
    if text is None:
        names = ", ".join(agent_prompt.skill_names()) or "(none installed)"
        return (f"REJECTED: no skill named {name!r}. Available skills: "
                f"{names}.")
    return text


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
    """Tempo/beats/energy for a music reference — a project upload or
    fetched track (cached on the asset's meta)."""
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
                "or a fetched track's storage_key.")
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


def set_edit_plan(ctx, steps, brief=None, format=None, intent=None,
                  style_family=None, must_keep=None, must_avoid=None):
    """Record the turn's edit plan — working memory, not an EDL write.

    Round 98. The prompt has always demanded 'plan the edit before you touch
    it', and the plan lived nowhere: a pass that hit the step ceiling or the
    clock resumed knowing WHAT it already ran but not what it had DECIDED,
    and re-derived the edit mid-flight (sometimes differently). This makes
    the plan a first-class object: recorded once, echoed into every
    continuation pass, and shown to the user in the activity feed — the
    editor saying 'here is what I'm going to do' before doing it."""
    if not isinstance(steps, (list, tuple)) or not steps:
        return ("REJECTED: steps must be a non-empty array of short "
                "strings, one per planned move, in execution order.")
    clean = []
    for s in list(steps):
        s = str(s or "").strip()
        if s:
            clean.append(s[:140])
    if not clean:
        return "REJECTED: every step was empty."
    def _clean_constraints(value, label):
        if value is None:
            return [], None
        if not isinstance(value, (list, tuple)):
            return None, f"REJECTED: {label} must be an array of short strings."
        rows = [str(v or "").strip()[:120] for v in value]
        return [v for v in rows if v], None

    keeps, err = _clean_constraints(must_keep, "must_keep")
    if err:
        return err
    avoids, err = _clean_constraints(must_avoid, "must_avoid")
    if err:
        return err
    ctx.edit_plan = {
        "brief": (str(brief or "").strip()[:200] or None),
        "format": (str(format or "").strip()[:80] or None),
        "intent": (str(intent or "").strip()[:160] or None),
        "style_family": (str(style_family or "").strip()[:100] or None),
        "must_keep": keeps,
        "must_avoid": avoids,
        "steps": clean,
    }
    head = (f" Brief: {ctx.edit_plan['brief']}."
            if ctx.edit_plan["brief"] else "")
    anchors = []
    if ctx.edit_plan["format"]:
        anchors.append(f"format={ctx.edit_plan['format']}")
    if ctx.edit_plan["style_family"]:
        anchors.append(f"style={ctx.edit_plan['style_family']}")
    if keeps:
        anchors.append("must keep: " + "; ".join(keeps))
    if avoids:
        anchors.append("must avoid: " + "; ".join(avoids))
    anchor_note = (" Anchors: " + " | ".join(anchors) + "."
                   if anchors else "")
    return (f"Plan recorded ({len(clean)} steps).{head}{anchor_note} "
            + " ".join(f"{i + 1}) {s}" for i, s in enumerate(clean))
            + " — now execute it in big batched steps. If the edit has to "
              "change course, record the new plan the same way.")


# Pure EDL operations that can be staged against an in-memory context and
# committed as ONE version. Anything that creates/downloads an asset, starts a
# render, asks the user, or modifies pixels outside the EDL is intentionally
# absent: transactional means no external side effect can escape an abort.
RECIPE_TOOLS = frozenset({
    "keep_segments", "cut_range", "cut_output_range", "restore_range",
    "cut_silences", "remove_filler_words",
    "add_captions", "set_caption_style", "set_caption_fixes",
    "set_caption_mutes",
    "set_frame", "auto_reframe",
    "set_color_grade", "set_grade_custom", "set_transitions",
    "add_zoom", "remove_zoom", "add_zoom_path", "remove_zoom_path",
    "punch_in_on_emphasis", "set_fades",
    "set_volume", "set_speed", "remove_speed",
    # Pure timeline objects. These all stage solely through ctx.write_edl;
    # asset resolution and analysis are reads, so an aborted recipe leaves no
    # externally visible side effect. Keeping them out caused valid text,
    # insert and overlay repair plans to lose their entire batch.
    "set_insert_window", "move_insert", "remove_insert",
    "add_overlay", "move_overlay", "remove_overlay",
    "add_text", "remove_text",
    # These mutate only the in-memory EDL and reference an asset that already
    # exists. They are transaction-safe; search/fetch remain separate
    # evidence/side-effect calls. Excluding them made the agent try a correct
    # music request in a recipe, get refused, then enter needless retries.
    "add_music", "remove_music", "swap_music", "set_music_fit",
    "set_audio_gain",
    "add_stylize", "remove_stylize", "set_master_loudness",
    "enhance_video", "add_custom_filter", "remove_custom_filter",
    "beat_align_cuts",
    # reset_edit creates only a fresh in-memory EDL when called through the
    # staging context. Keeping it outside recipes made a clean rebuild begin
    # with a guaranteed refusal and a second model dispatch.
    "reset_edit",
})


# Mechanical dialect repair for ids the model can infer correctly but spell
# with the long noun instead of the EDL's compact prefix.  These mappings are
# one-to-one; they never guess which object the user meant.
_ID_PREFIX_ALIASES = {
    "music": "mus", "zoom": "zm", "text": "tx", "overlay": "ov",
    "insert": "ins", "stylize": "st", "customfilter": "cf",
    "custom_filter": "cf",
}
_ID_TOOLS = {
    "remove_music", "swap_music", "set_music_fit", "set_audio_gain",
    "remove_zoom", "remove_zoom_path", "remove_text", "remove_overlay",
    "move_overlay", "remove_insert", "move_insert", "set_insert_window",
    "remove_stylize", "remove_custom_filter", "remove_speed",
}
_IDEMPOTENT_RECIPE_REMOVES = {
    "remove_music", "remove_zoom", "remove_zoom_path", "remove_text",
    "remove_overlay", "remove_insert", "remove_stylize",
    "remove_custom_filter", "remove_speed",
}


def _canonical_object_id(value):
    raw = str(value or "").strip()
    low = raw.lower()
    for long, short in _ID_PREFIX_ALIASES.items():
        match = re.fullmatch(re.escape(long) + r"[-_ ]?(\d+)", low)
        if match:
            return short + match.group(1)
    return raw


def _normalize_tool_call(name, args):
    """Repair only unambiguous tool dialect aliases.

    Returns ``(name, args, notes)``.  This is shared by direct calls and
    recipes so batching does not mysteriously accept a different dialect.
    """
    name = str(name or "").strip()
    args = dict(args or {})
    notes = []
    if name in {"add_overlay", "insert_media", "set_insert_window"} \
            and "duration" in args and "duration_s" not in args:
        args["duration_s"] = args.pop("duration")
        notes.append("duration -> duration_s")
    if name in {"get_transcript", "get_audio_analysis"}:
        key = str(args.get("asset_key") or "").strip().lower()
        if key in {"main", "source", "original", "main_video"}:
            args.pop("asset_key", None)
            notes.append("main asset alias -> omitted")
    if name in _ID_TOOLS and "id" in args:
        fixed = _canonical_object_id(args.get("id"))
        if fixed != args.get("id"):
            notes.append(f"id {args.get('id')} -> {fixed}")
            args["id"] = fixed
    # `auto` is a real reframe mode, but it belongs to auto_reframe.  The
    # production model repeatedly combined it with set_frame and paid a
    # refusal.  Focus coordinates are deliberately dropped: auto_reframe
    # measures its own per-shot focus rather than trusting the generic 0.5.
    if name == "set_frame" and str(args.get("mode") or "").lower() == "auto":
        name = "auto_reframe"
        args.pop("focus_x", None)
        args.pop("focus_y", None)
        notes.append("set_frame(mode=auto) -> auto_reframe")
    return name, args, notes


class _RecipeContext:
    """ToolContext view whose EDL writes land in memory until final commit."""

    def __init__(self, base, edl, version):
        object.__setattr__(self, "_base", base)
        # A recipe must be unable to mutate the live DB row through a nested
        # list/dict alias even if one legacy tool edits its input in place.
        object.__setattr__(self, "_edl", json.loads(json.dumps(edl)))
        object.__setattr__(self, "_version", version)
        # add_captions must still run the real pixel gate on this proxy.
        object.__setattr__(self, "enforce_spatial", True)

    def __getattr__(self, name):
        return getattr(self._base, name)

    def latest_edl(self):
        return {"version": self._version, "json": self._edl}

    def write_edl(self, edl, desc):
        try:
            normalized = validate_edl(edl, self.duration).model_dump()
        except EDLValidationError as exc:
            return f"REJECTED while staging recipe: {exc}"
        if edl_signature(normalized) == edl_signature(self._edl):
            return "NO CHANGE while staging recipe"
        object.__setattr__(self, "_edl", normalized)
        return f"EDL v{self._version} -> staged: {desc}."


def apply_edit_recipe(ctx, operations, brief=None):
    """Stage several ordinary edit tools and atomically commit one EDL."""
    if not isinstance(operations, list) or not operations:
        return ("REJECTED: operations must be a non-empty array of "
                "{tool:'name', args:{...}} objects.")
    row = ctx.latest_edl()
    stage = _RecipeContext(ctx, row["json"], row["version"])
    notes, plan_steps = [], []
    for i, op in enumerate(operations, 1):
        if not isinstance(op, dict):
            return f"REJECTED: operation {i} is not an object; nothing changed."
        name = str(op.get("tool") or "").strip()
        args = op.get("args") or {}
        if isinstance(args, dict):
            name, args, repairs = _normalize_tool_call(name, args)
        else:
            repairs = []
        if name not in RECIPE_TOOLS:
            allowed = ", ".join(sorted(RECIPE_TOOLS))
            return (f"REJECTED: operation {i} uses '{name}', which is not a "
                    "transaction-safe recipe tool. Nothing changed. Allowed: "
                    f"{allowed}.")
        if not isinstance(args, dict):
            return (f"REJECTED: operation {i} args must be an object; "
                    "nothing changed.")
        result = execute(stage, name, args)
        result_kind = tool_result_kind(result)
        if not isinstance(result, str) or result_kind in {"refused", "failed"}:
            # Removing an object is idempotent inside an atomic repair batch.
            # A stale id must not discard eleven valid sibling operations; the
            # requested end state (that object absent) is already true.
            if name in _IDEMPOTENT_RECIPE_REMOVES and \
                    isinstance(result, str) and result.startswith("REJECTED:") \
                    and " with id '" in result and "Existing" in result:
                notes.append(f"{i}) {name}: already absent")
                continue
            return (f"RECIPE ABORTED at operation {i} ({name}); no EDL version "
                    f"was created.\n{result}")
        if result.startswith("NO CHANGE"):
            notes.append(f"{i}) {name}: no change"
                         + (f" ({'; '.join(repairs)})" if repairs else ""))
        else:
            notes.append(f"{i}) {name}: staged"
                         + (f" ({'; '.join(repairs)})" if repairs else ""))
            plan_steps.append(name)
    if edl_signature(stage._edl) == edl_signature(row["json"]):
        return ("NO CHANGE — the complete recipe resolves to the current EDL; "
                "nothing was committed.\n" + "\n".join(notes))
    label = (str(brief or "").strip()[:160] or
             ", ".join(plan_steps) or "edit recipe")
    committed = ctx.write_edl(
        stage._edl,
        f"atomically applied recipe '{label}' ({len(plan_steps)} change(s))")
    if committed.startswith("EDL v"):
        existing = dict(getattr(ctx, "edit_plan", None) or {})
        existing["brief"] = existing.get("brief") or label
        # The original direction remains the binding contract across repair
        # passes and continuations. Replacing its steps with the latest tool
        # names made a later repair forget "auto/per-shot framing" and erase
        # a correct focus_track. Keep intent and execution history separately.
        if not existing.get("steps"):
            existing["steps"] = [f"completed: {name}"
                                 for name in plan_steps]
        completed = list(existing.get("completed_tools") or [])
        for name in plan_steps:
            if name not in completed:
                completed.append(name)
        existing["completed_tools"] = completed
        ctx.edit_plan = existing
        committed += "\nRecipe operations:\n" + "\n".join(notes)
    return committed


def _declared_mix_state(ctx, edl):
    """Deterministic authored roles; no perceptual model can relabel these."""
    if not isinstance(edl, dict):
        return None

    def asset_label(key):
        try:
            asset = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
        except Exception:
            asset = None
        meta = (asset or {}).get("meta") or {}
        return (meta.get("filename") or meta.get("title") or
                os.path.basename(key or "?"))[:80]

    return {
        "music": [{"id": item.get("id"),
                   "file": asset_label(item.get("storage_key")),
                   "window": [item.get("start"), item.get("end")],
                   "source_offset_s": item.get("offset_s") or 0,
                   "gain_db": item.get("gain_db"),
                   "duck": item.get("duck")}
                  for item in (edl.get("music") or [])],
        "voiceover": [{"id": item.get("id"),
                       "file": asset_label(item.get("asset_key")),
                       "start_output_s": item.get("start_output_s"),
                       "source_offset_s": item.get("source_offset_s") or 0,
                       "gain_db": item.get("gain_db"),
                       "duck_others": item.get("duck_others")}
                      for item in (edl.get("voiceover") or [])],
        "sfx": [{"id": item.get("id"), "at": item.get("at"),
                 "gain_db": item.get("gain_db")}
                for item in (edl.get("sfx") or [])],
        "master_loudness": (edl.get("master") or {}).get("loudness"),
    }


def audit_audio_mix(ctx):
    """Authored audio roles and offsets, separate from subjective listening."""
    row = ctx.latest_edl()
    edl = row["json"]
    state = _declared_mix_state(ctx, edl) or {}
    music_keys = {m.get("storage_key"): m.get("id")
                  for m in (edl.get("music") or [])}
    vo_keys = {v.get("asset_key"): v.get("id")
               for v in (edl.get("voiceover") or [])}
    doubled = [{"asset_key": key, "music_id": music_keys[key],
                "voiceover_id": vo_keys[key]}
               for key in sorted(set(music_keys) & set(vo_keys))]
    warnings = []
    if doubled:
        warnings.append("same audio asset is active as both music and voiceover")
    if not state.get("music") and state.get("voiceover"):
        warnings.append(
            "EDL has voiceover but no music; if this file is actually a song, "
            "remove_voiceover and add_music instead—the roles mix differently")
    if not any(state.get(k) for k in ("music", "voiceover", "sfx")):
        warnings.append("no designed audio layers are authored")
    preview = ctx.last_preview or {}
    result = {
        "version": row["version"],
        "authored_roles_are_ground_truth": True,
        "mix": state,
        "duplicate_cross_role_assets": doubled,
        "warnings": warnings,
        "latest_preview_matches_version": preview.get("edl_version") == row["version"],
        "latest_preview_audio_qc": (preview.get("audio_qc")
                                    if preview.get("edl_version") == row["version"]
                                    else None),
        "next": ("Use render_preview for deterministic AUDIO CHECK evidence; "
                 "the authored role remains EDL ground truth."),
    }
    return json.dumps(result, indent=1)


def get_audio_analysis(ctx, asset_key=None):
    """READ: the measured musical/energy structure of the source audio (or
    of a music asset when asset_key is passed)."""
    if asset_key:
        return _asset_audio_analysis(ctx, asset_key)
    if not ctx.has_main_video:
        return ("REJECTED: there is no main video to analyze on this "
                "image/clip-only program. Pass asset_key to analyze an "
                "uploaded or fetched music file instead.")
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


def _frame_focus_at_source(edl, source_t):
    """The crop focus the renderer uses for a source moment."""
    frame = edl.get("frame") or {}
    base = (frame.get("focus_x"), frame.get("focus_y"))
    for span in frame.get("focus_track") or []:
        try:
            if float(span.get("t0")) <= source_t <= float(span.get("t1")):
                return (span.get("x") if span.get("x") is not None else base[0],
                        span.get("y") if span.get("y") is not None else base[1])
        except (TypeError, ValueError):
            continue
    return base


def _frame_mode_at_source(edl, source_t):
    """The fit/crop mode the renderer uses for a source moment."""
    frame = edl.get("frame") or {}
    base = frame.get("mode") or "crop"
    for span in frame.get("focus_track") or []:
        try:
            if float(span.get("t0")) <= source_t <= float(span.get("t1")):
                return span.get("mode") or base
        except (TypeError, ValueError):
            continue
    return base


def _source_point_to_output(ctx, edl, source_t, point):
    """Map a measured source-frame point into the rendered output frame."""
    video = ctx.index.get("video") or {}
    try:
        sw, sh = float(video["width"]), float(video["height"])
    except (KeyError, TypeError, ValueError):
        return None
    frame = edl.get("frame") or {}
    W, H = renderer.frame_dims(sw, sh, frame.get("ratio") or "source")
    fit = renderer.fit_fractions(
        sw, sh, W, H, _frame_mode_at_source(edl, source_t),
        _frame_focus_at_source(edl, source_t))
    kind, x0, y0, x1, y1 = fit
    x, y = point
    if kind == "crop":
        # The point may genuinely have been cropped out. A professional edit
        # does not zoom toward an invisible subject and hope it comes back.
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return None
        x = (x - x0) / max(x1 - x0, 1e-9)
        y = (y - y0) / max(y1 - y0, 1e-9)
    else:
        x = x0 + x * (x1 - x0)
        y = y0 + y * (y1 - y0)
    return round(min(max(x, 0.0), 1.0), 3), \
        round(min(max(y, 0.0), 1.0), 3)


def _face_at_source_moments(ctx, edl, moments):
    """{source_t: (output_x, output_y)} measured from exact proxy frames.

    Audio emphasis decides *when* a punch might be useful; it cannot decide
    *where* to aim it. Only a detected face is accepted here. Gradient energy
    is a useful crop fallback but not strong enough evidence to magnify.
    """
    try:
        proxy = ctx.proxy_path()
    except Exception:
        return {}
    out = {}
    for i, source_t in enumerate(moments):
        fp = os.path.join(ctx.workdir,
                          f"emphasis_face_{i}_{int(source_t * 100)}.jpg")
        try:
            media.frame_at(proxy, source_t, fp, width=640)
        except media.MediaError:
            continue
        points, method = subject.points_from_frames([fp])
        if method != "faces" or not points:
            continue
        mapped = _source_point_to_output(ctx, edl, source_t, points[0])
        if mapped is not None:
            out[source_t] = mapped
    return out


def punch_in_on_emphasis(ctx, count=3, strength=0.14):
    """Punch zooms on the most vocally stressed KEPT words, in ONE version.
    Every timestamp is a real word time mapped through the current cut —
    nothing is estimated.

    Round 67 defaults: 3 zooms at 14% (was 4 at 35%). A 35% snap on a
    talking head is the 'abrupt noob zoom' the owner traced through real
    edits — modern emphasis is 105-115%, felt more than seen. Bigger values
    remain an editorial choice."""
    if not ctx.has_main_video:
        return ("REJECTED: needs the main video — an image/clip-only "
                "program has no speech to find emphasis in.")
    words = ctx.index.get("words") or []
    if not words:
        return ("REJECTED: this video has no transcript, so there are no "
                "stressed words to punch in on. Place zooms by hand with "
                "add_zoom instead.")
    try:
        n = max(int(count), 1)
    except (TypeError, ValueError):
        return "REJECTED: count must be a positive integer."
    try:
        st = round(min(max(float(strength), ZOOM_STRENGTH_MIN),
                       ZOOM_STRENGTH_MAX), 2)
    except (TypeError, ValueError):
        return "REJECTED: strength must be a number (0.05-4.5)."
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
    picked = []          # (word, program_t0, source_mid)
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
        picked.append((w, pt, mid))
    if not picked:
        return ("No stressed words survive the current cut "
                "— nothing was written. Place zooms by hand with add_zoom "
                "if you still want them. Do NOT tell the user zooms were "
                "added.")
    picked.sort(key=lambda q: q[1])
    targets = _face_at_source_moments(ctx, edl, [q[2] for q in picked])
    fx = dict(edl.get("effects") or {})
    zooms = [dict(z) for z in (fx.get("zooms") or [])]
    placed = []
    for w, pt, source_mid in picked:
        measured = source_mid in targets
        target = targets.get(source_mid) or (0.5, 0.5)
        # 60ms early so the punch lands ON the word's attack, not after it.
        s = round(max(0.0, pt - 0.06), 2)
        e = round(min(prog, s + 0.9), 2)
        if e - s < 0.2:
            continue                     # the word sits at the very end
        item = {"id": _next_item_id(zooms, "zm"), "start": s, "end": e,
                "strength": st, "cx": target[0], "cy": target[1],
                "target_measured": measured}
        zooms.append(item)
        placed.append((w, pt, item["id"], target, measured))
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
                + "\n".join(f"  '{w['w']}' @ {pt}s, "
                            f"{'face target' if measured else 'center fallback'} "
                            f"({target[0]:g},{target[1]:g}) [{zid}]"
                            for w, pt, zid, target, measured in placed))
        fallback_count = sum(1 for *_rest, measured in placed
                             if not measured)
        if fallback_count:
            res += (f"\nQUALITY ADVISORY: {fallback_count} punch-in(s) had "
                    "no detected face and were committed at frame center; "
                    "inspect and retarget them if desired.")
    return res


# The pass's palette: one fixed, tasteful prompt per accent kind, matching
# the audio skill's soft/airy/sub preference. Fixed strings on purpose —
# re-running the pass on the same edit asks the provider for the same
# sounds, which is as close to determinism as generation gets (the MOMENTS
# stay fully deterministic either way).
def _music_program_beats(ctx, edl, bpm=None, every_s=None, key=None):
    """(beats in PROGRAM seconds, label, error) for the SONG the viewer hears.

    "Cut it to the beat" means the beat of the music that is playing. When a
    song is laid over footage, the footage's own transients are not the beat —
    aligning to them is aligning to the wrong sound entirely, and on Jul 26
    2026 that is what happened: a user asked for cuts on a 1-second pulse, the
    tool measured the DRINKS FOOTAGE (80.7 BPM, confidence 0.15), refused, and
    the song sitting in the same EDL was never looked at.

    bpm/every_s can supply a tempo directly ("there's a beat every second").
    A measured low-confidence grid is also usable and is reported as a quality
    advisory rather than withheld. Phase comes from where the music starts.
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
            asset = ctx.db.run(dbx.asset_by_key, ctx.project_id,
                               item["storage_key"])
            if not asset:
                return None, None, (
                    "Could not analyze the music in this edit — its file "
                    "is not a project asset any more. Fetch or upload a "
                    "fresh track and swap_music to it first.")
            p = perception.get_or_compute_for_asset(
                ctx.db, dbx, asset, _asset_local_path(ctx, asset))
        except Exception as e:
            return None, None, (f"Could not analyze the music in this edit "
                                f"({str(e)[:160]}).")
        ctx._asset_perception[item["storage_key"]] = p
    conf = float(p.get("bpm_conf") or 0.0)
    track_beats = p.get("beats") or []
    if not track_beats:
        return None, None, (
            f"REJECTED: the music's own pulse is not clear enough to cut to "
            f"(bpm {p.get('bpm') or 'none'}, confidence {conf:.2f})"
            f"{_flatline_note(p)}. If the "
            "user can hear the beat, ask them how often it lands (or pass "
            "every_s/bpm if they already told you) and this will use it.")
    # Track seconds -> program seconds. The renderer loops at the demuxer and
    # trims [offset_s, offset_s + span) out of the repeated stream, so a beat
    # at track time t is heard at start - offset + t + k*track_length.
    off = float(item.get("offset_s") or 0.0)
    # Length of the analyzed audio, straight from the envelope it produced —
    # the perception sidecar carries no duration of its own.
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
    confidence_note = ("; LOW-CONFIDENCE QUALITY ADVISORY" if conf < 0.5
                       else "")
    return beats, (f"{float(p.get('bpm') or 0):g} BPM measured from the music "
                   f"(confidence {conf:.2f}{confidence_note})"), None


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
        tol = max(float(tolerance_s), 0.01)
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
    if not bpm or not beats:
        return ("REJECTED: the footage's own audio has no pulse clear enough "
                f"to cut to (bpm {bpm or 'none'}, confidence {conf:.2f}; "
                "no beat grid was detected). Two real routes: add the "
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
        if conf < 0.5:
            res += ("\nQUALITY ADVISORY: the detected pulse has low "
                    "confidence; the alignment was still committed for the "
                    "editor to judge in preview.")
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
        k = _caption_token_key(t)
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
        k = _caption_token_key(w["w"])
        if k:
            freq[k] = freq.get(k, 0) + 1
    rare = []
    for w in words:
        if len(out) >= 25:
            break
        k = _caption_token_key(w["w"])
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
    "clean": {"captions": {"preset": "clean", "emphasis": "big"},
              "grade": None,
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
            emphasis_off = (isinstance(caps, dict)
                            and caps.get("emphasis_mode") == "off")
            if emphasis_off:
                emphasis, emph_src = [], "kept explicitly disabled"
            elif isinstance(caps, dict) and caps.get("emphasis_words"):
                emphasis, emph_src = caps["emphasis_words"], "kept existing"
            else:
                emphasis = _emphasis_candidates(ctx)[0]
                emph_src = "picked from the transcript"
            merged = (merge_caption_style(caps, dict(cap_patch)) if caps
                      else {"mode": "from_transcript",
                            "design_version": CAPTION_DESIGN_VERSION,
                            "max_words_per_caption": None,
                            "style": dict(cap_patch)})
            bit = f"captions preset '{cap_patch['preset']}'"
            if cap_patch.get("size"):
                bit += f" size {cap_patch['size']}"
            if isinstance(merged, dict):
                merged["design_version"] = CAPTION_DESIGN_VERSION
                if emphasis:
                    merged["emphasis_words"] = emphasis
                    merged["emphasis_mode"] = (caps.get("emphasis_mode")
                                               if isinstance(caps, dict)
                                               and caps.get("emphasis_mode")
                                               else "auto")
                    bit += f", {len(emphasis)} emphasis words ({emph_src})"
                elif emphasis_off:
                    merged["emphasis_words"] = None
                    merged["emphasis_mode"] = "off"
                    bit += ", keyword hierarchy kept off"
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
        res += "\napply_look never touches cuts, music or sfx."
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
CAPTION_PRESETS = ["clean", "documentary", "broadcast", "retro", "neon",
                   "podcast", "reels", "beast", "karaoke", "elegant", "spotlight",
                   "stacked", "iridescent", "chrome", "editorial",
                   "fashion", "luxe", "impact", "lyric", "classic"]
def make_shorts(ctx, count=None, style_note=None):
    """Kick off the shorts pipeline for THIS project — the chat-path twin of
    the studio's Make shorts button. The heavy work runs as its own
    shorts_plan job so this turn can answer immediately; the board on the
    project shows the clips as they land."""
    if getattr(ctx, "project", {}).get("parent_project_id"):
        return (f"REJECTED: this project IS a generated short (from the "
                f"board of project {ctx.project['parent_project_id']}) — do "
                "not cut shorts from a short. Edit THIS clip with the "
                "normal tools, or use edit_shorts to change several "
                "siblings at once (it reaches the parent board from here).")
    if not ctx.has_main_video:
        return ("REJECTED: shorts are cut from the MAIN video and this "
                "project has none yet — ask the user to upload their long "
                "video first.")
    if ctx.duration < 60.0:
        return (f"DIRECT SHORT: this {ctx.duration:.0f}s source already fits "
                "one short, so do not start the multi-clip extractor. "
                "Continue THIS turn and edit it directly from the user's "
                "brief: use keep_segments only if tightening is requested, "
                "auto_reframe for 9:16 when requested, then the normal "
                "caption/effect/audio tools. This is a routing instruction, "
                "not a failure — do the edit now.")
    has_speech = bool(ctx.index.get("words") or [])
    has_visual_plan = bool(ctx.index.get("tile_keys") or []) \
        and llm.vision_available()
    if not has_speech and not has_visual_plan:
        return ("REJECTED: this video has neither usable speech nor a "
                "visual filmstrip reviewer available, so there is no honest "
                "evidence for choosing highlight windows. The user can edit "
                "this video directly, or retry shorts when visual review is "
                "available.")
    if ctx.db.run(dbx.has_active_job, ctx.project_id, "shorts_plan"):
        return ("A shorts run is ALREADY working on this project — tell the "
                "user their clips are on the way on the Shorts board; do "
                "not start another.")
    payload = {"source": "agent"}
    try:
        if count:
            payload["count"] = max(1, min(int(count),
                                          config.SHORTS_MAX_CLIPS))
    except (TypeError, ValueError):
        pass
    if style_note:
        payload["style_note"] = str(style_note)[:400]
    job_id = ctx.db.run(dbx.enqueue_job, ctx.project_id,
                        ctx.job["user_id"], "shorts_plan", payload)
    evidence = ("whole transcript" if has_speech else
                "full-video visual filmstrips")
    return (f"Shorts run started as job {job_id}. It reviews the {evidence}, "
            "picks the "
            "strongest self-contained moments, and builds each one as its "
            "own project — reframed to 9:16, captioned, with emphasis "
            "punch-ins — then renders them. The user watches it happen on "
            "this project's Shorts board (top of the video pane). Tell "
            "them it's underway and where to look; do NOT wait for it in "
            "an in-house turn. An MCP caller can poll this exact run with "
            f"wait_for_job(job_id={job_id}) or shorts_status.")


def _shorts_children(ctx):
    """The parent board's clips carrying a live child project, in board
    order. [] when this project is not a shorts board."""
    meta = (ctx.project.get("meta") or {})
    clips = ((meta.get("shorts") or {}).get("clips")) or []
    return [c for c in clips if c.get("child_project_id")]


def _shorts_board(ctx):
    """(board_project_id, live_clips, parent_row_or_None) — the Shorts board
    this project belongs to. A shorts parent IS the board; a generated short
    resolves THROUGH parent_project_id to its parent's board, so board tools
    work from either side of the family.

    That resolution is the whole point (2026-08-10): an MCP model sitting on
    generated short 423 called edit_shorts, was told "no shorts on its
    board", and concluded projects could only be switched in the studio app —
    while the board was one parent_project_id away. A tool that knows where
    the board lives must go there, not send the model on an errand."""
    kids = _shorts_children(ctx)
    if kids:
        return ctx.project["id"], kids, None
    parent_id = ctx.project.get("parent_project_id")
    if not parent_id:
        return ctx.project["id"], [], None
    parent = ctx.db.run(dbx.get_project, parent_id)
    # Same-owner check is belt and braces — children are created under the
    # parent's user — but a board reached by id must never cross accounts.
    if not parent or parent.get("user_id") != ctx.project.get("user_id"):
        return ctx.project["id"], [], None
    clips = (((parent.get("meta") or {}).get("shorts") or {})
             .get("clips")) or []
    return parent["id"], [c for c in clips if c.get("child_project_id")], \
        parent


def edit_shorts(ctx, instruction, shorts=None):
    """Fan ONE instruction out to the child shorts on this family's board —
    each child gets the instruction in its own chat and runs its own edit
    turn on its own timeline. THE tool for "do X to all the shorts", and it
    works from the PARENT or from INSIDE any generated short (the board
    resolves through parent_project_id). History: 2026-08-09, 'add the
    interstellar music to all of them' was answered by laying the track
    under the 85-minute ORIGINAL — the parent agent had no way to reach its
    children; 2026-08-10, the same request from a child was rejected with
    'no shorts on its board' and the session stalled on navigation."""
    board_pid, kids, board_parent = _shorts_board(ctx)
    if not kids:
        if ctx.project.get("parent_project_id"):
            return ("REJECTED: this is a generated short and its parent "
                    f"board (project {ctx.project['parent_project_id']}) "
                    "has no live clips yet — the board may still be "
                    "building or was cleared. Check shorts_status.")
        return ("REJECTED: this project has no shorts on its board. "
                "make_shorts cuts them first; if the user wants THIS "
                "video edited, use the normal editing tools.")
    text = (instruction or "").strip()
    if not text:
        return ("REJECTED: instruction is empty — pass the edit you want "
                "every short to apply, in plain words (as if the user "
                "typed it into that short's chat).")
    picks = kids
    if shorts not in (None, "", "all", ["all"]):
        if not isinstance(shorts, list):
            shorts = [shorts]
        want = set()
        for x in shorts:
            try:
                want.add(int(x))
            except (TypeError, ValueError):
                return ("REJECTED: shorts must be 'all' or a list of card "
                        "numbers as shown on the board (1-based).")
        picks = [c for i, c in enumerate(kids, 1) if i in want]
        if not picks:
            return (f"REJECTED: no board cards match {sorted(want)} — the "
                    f"board has {len(kids)} short(s), numbered 1-"
                    f"{len(kids)}.")

    # Children were seeded sharing only the original+proxy. Any music, clip
    # or image that landed on the BOARD PARENT afterwards (a fetched track,
    # an upload) rides along by storage key, so "use the song I added here"
    # works inside every child without eight re-downloads. When this call
    # comes from INSIDE a generated short, that child's own assets travel
    # too — the track the user fetched while sitting on clip 7 is exactly
    # the one "add it to all of them" means.
    src_ids = [board_pid] if board_pid == ctx.project_id \
        else [board_pid, ctx.project_id]

    def _carry_assets(conn):
        with conn.cursor() as cur:
            cur.execute("""SELECT * FROM assets
                           WHERE project_id = ANY(%s)
                             AND kind IN ('music', 'video_clip', 'image_ref')
                             AND COALESCE(meta->>'role', '') !=
                                 'shorts_reference'
                           ORDER BY id""", (src_ids,))
            parent_assets = cur.fetchall()
        carried = 0
        for c in picks:
            child_id = c["child_project_id"]
            with conn.cursor() as cur:
                cur.execute("""SELECT storage_key FROM assets
                               WHERE project_id = %s""", (child_id,))
                have = {r["storage_key"] for r in cur.fetchall()}
            for a in parent_assets:
                if a["storage_key"] in have:
                    continue
                meta = dict(a.get("meta") or {})
                meta.pop("staged", None)
                meta.pop("tray_pos", None)
                meta["shared_from_project"] = a["project_id"]
                dbx.insert_asset(
                    conn, child_id, a["kind"], a["storage_key"],
                    bytes_=a.get("bytes"), duration_s=a.get("duration_s"),
                    width=a.get("width"), height=a.get("height"),
                    fps=a.get("fps"), sha256=a.get("sha256"), meta=meta)
                carried += 1
        return carried

    def _dispatch(conn):
        sent = []
        for c in picks:
            child_id = c["child_project_id"]
            with conn.cursor() as cur:
                cur.execute("""SELECT chat_session_id FROM projects
                               WHERE id = %s""", (child_id,))
                row = cur.fetchone()
            if not row or not row["chat_session_id"]:
                continue
            mid = dbx.add_message(
                conn, row["chat_session_id"], "user", text,
                {"from_parent": board_pid,
                 "batch_tool": "edit_shorts"})
            # Round-100 stacking: an already-QUEUED turn re-aims at the
            # newest message on claim, so a second job row would only
            # double-run the stack. A RUNNING turn needs the follow-up.
            with conn.cursor() as cur:
                cur.execute("""SELECT 1 FROM video_jobs
                               WHERE project_id = %s AND type = 'agent_turn'
                                 AND state = 'queued' LIMIT 1""",
                            (child_id,))
                queued = cur.fetchone() is not None
            jid = None
            if not queued:
                jid = dbx.enqueue_job(conn, child_id, ctx.job["user_id"],
                                      "agent_turn", {"message_id": mid,
                                                     "from_parent":
                                                     board_pid})
            sent.append((c.get("title") or f"short {child_id}", jid))
        return sent

    carried = ctx.db.run(_carry_assets)
    sent = ctx.db.run(_dispatch)
    if not sent:
        return ("REJECTED: none of the selected shorts could take the "
                "instruction (their projects are missing chats) — tell the "
                "user to re-cut the board.")
    names = "; ".join(
        f"“{t}”" + (f" (job {jid})" if jid else " (stacked onto its queued "
                                              "turn)")
        for t, jid in sent[:8])
    via = ""
    if board_parent is not None:
        via = (f"(Resolved from this generated short to its parent board, "
               f"project {board_pid} “{board_parent.get('title') or ''}” — "
               "no need to switch projects.) ")
    return (f"{via}DELEGATED to Valmera's in-house agents for {len(sent)} "
            f"short(s): {names}. This call did not edit their EDLs directly. "
            "Each agent is running the instruction as its own edit turn on its own "
            "timeline and re-renders when done — the board's cards update "
            "as they land. "
            + (f"{carried} shared asset(s) (music/clips/images) were "
               f"copied into the shorts first, so the instruction can use "
               f"them by name. " if carried else "")
            + "Each short's turn bills like a normal message. An MCP "
            "caller can follow a specific clip with wait_for_job(job_id) "
            "or the whole board with shorts_status. Tell the user what "
            "was delegated and that the shorts are updating on the board — do "
            "NOT wait for them in this turn, and do NOT also edit the "
            "timeline you are on unless they asked for that video too.")


CAPTION_FONTS = ["Inter Display Black", "Inter Display ExtraBold",
                 "Inter Display Bold", "Anton", "Bebas Neue", "Archivo Black",
                 "Poppins Black", "Syne ExtraBold", "Playfair Display Black",
                 "Instrument Serif", "DM Serif Display", "Montserrat",
                 "Plus Jakarta Sans"]
CAPTION_ANIMS = ["none", "fade", "pop", "slide_up", "punch", "blur_in",
                 "whip", "flash", "rise", "drop", "elastic", "bounce",
                 "swing", "zoom_blur"]
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
                          "script", "chrome", "glow", "chroma", "none"]},
    "emphasis_scale": {"type": "number"},
    "outline_color": {"type": "string"},
    "outline_width": {"type": "number"},
    "shadow": {"type": "number"},
    "background_color": {"type": "string"},
    "background_opacity": {"type": "number"},
    "tracking": {"type": "number"},
    "text_align": {"type": "string",
                   "enum": ["left", "center", "right"]},
    "anchor_y": {"type": "number", "minimum": 0.05, "maximum": 0.95},
}

TOOLS = {
    "get_video_info": (get_video_info, "Video metadata plus index and EDL "
                       "summary. Call this first.", {}),
    "get_transcript": (get_transcript, "Sentence-level SOURCE transcript "
                       "with timestamps for a time range (source seconds). "
                       "For word-exact timing use get_words; for what the "
                       "current EDIT keeps, use get_kept_transcript. Pass "
                       "asset_key to read an UPLOADED clip's or song's own "
                       "transcript instead (clip seconds).",
                       {"start": {"type": "number"},
                        "end": {"type": "number"},
                        "asset_key": {"type": "string"}}),
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
    "get_shots": (get_shots, "Shot boundaries (scene changes — where "
                  "transitions may land) for a time range. The PICTURE "
                  "itself is in your filmstrips and look_at.",
                  {"start": {"type": "number"},
                   "end": {"type": "number"}}),
    "read_skill": (read_skill, "Load a focused editing playbook (captions, "
                   "zooms, audio, transitions, ...) into this turn. The "
                   "SKILLS list in your instructions names them. Read the "
                   "matching skill before your first edit of that kind — "
                   "batch it with your other reading calls.",
                   {"name": {"type": "string"}}),
    "set_edit_plan": (set_edit_plan, "Record YOUR edit plan for this "
                      "request, then EXECUTE it in the SAME turn — one "
                      "short line per move, in order. This is a note to "
                      "yourself, not a proposal for the user to approve. "
                      "A concrete brief is already permission to cut; do "
                      "not stop after recording the plan. Record format, "
                      "intent, style_family, must_keep and must_avoid as "
                      "structured anchors — not only a vague 'make it "
                      "engaging' brief. Call it in the same batch as your "
                      "reads on any multi-step edit: the plan survives "
                      "auto-continuations (a resumed pass finishes what "
                      "was PLANNED instead of re-deciding). Re-call to "
                      "replace when the edit legitimately changes course. "
                      "Not an EDL write.",
                      {"steps": {"type": "array",
                                 "items": {"type": "string"}},
                       "brief": {"type": "string"},
                       "format": {"type": "string"},
                       "intent": {"type": "string"},
                       "style_family": {"type": "string"},
                       "must_keep": {"type": "array",
                                     "items": {"type": "string"}},
                       "must_avoid": {"type": "array",
                                      "items": {"type": "string"}}}),
    "apply_edit_recipe": (
        apply_edit_recipe,
        "Atomically apply ONE OR MORE transaction-safe EDL operations in "
        "one tool call and create exactly one new version. Pass operations "
        "as [{tool:'set_frame', args:{ratio:'9:16'}}, ...]. Every operation "
        "is staged against the result of the previous one; if any operation "
        "is structurally invalid, the whole recipe is aborted and NOTHING "
        "changes. Quality findings are committed as advisories. Use this for the large write batch "
        "after planning instead of spending one model round trip per simple "
        "edit. Asset downloads/generation, rendering, and user questions are "
        "intentionally unavailable inside recipes.",
        {"operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Reject invented recipe names at generation time instead
                    # of spending a model/tool round to discover the list.
                    "tool": {"type": "string",
                             "enum": sorted(RECIPE_TOOLS)},
                    "args": {"type": "object"},
                },
                "required": ["tool", "args"],
            }},
         "brief": {"type": "string"}}),
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
    "look_at": (look_at, "YOUR OWN EYES on the footage. Pass times=[...] "
                "(1-8 exact source seconds of the MAIN video) and the "
                "frames at those moments come back as ONE timestamp-labeled "
                "picture in your own context — you see the footage yourself "
                "and judge it directly (composition, where the subject is, "
                "clear space for text, what a moment looks like). Every "
                "frame carries a faint tenths grid ((0,0) = top-left): READ "
                "aim points, cx/cy and rects off its labels instead of "
                "estimating. start/end "
                "still work as a range sampled evenly. OR pass "
                "output_times=[...] to see the ASSEMBLED PROGRAM instead: "
                "output seconds of the current edit, resolved through the "
                "EDL — kept footage AND spliced inserts both sample "
                "correctly, each tile labeled with its scene number, in "
                "TRUE output geometry (canvas fit and any active zoom "
                "applied — so you can SEE an aimed zoom's framing before "
                "rendering) — THE "
                "way to check what the viewer sees at a moment of the "
                "EDITED video ('the second scene') without rendering. Look "
                "as often as you need — there is no cap on looking; before "
                "aiming anything and before disputing what a user saw, "
                "look. Batch the moments you need into ONE call with "
                "several times rather than a string of separate calls. The "
                "filmstrips already gave you the whole video at a glance — "
                "use look_at for the CLOSER look: exact framing, small "
                "text, a precise instant. The transcript is accurate, so "
                "read speech from "
                "get_words / the transcript — never look to lip-read or "
                "guess a word. IMPORTANT: the assembled geometry view omits "
                "burn-ins. Set rendered=true after render_preview to inspect "
                "the CURRENT preview's real pixels—including captions, text, "
                "overlays and grade. In rendered mode output_times (or times) "
                "are output seconds and any useful set can be batched for "
                "caption QA.",
                {"times": {"type": "array", "items": {"type": "number"}},
                 "output_times": {"type": "array",
                                  "items": {"type": "number"}},
                 "question": {"type": "string"},
                 "start": {"type": "number"},
                 "end": {"type": "number"},
                 "rendered": {"type": "boolean"}}),
    "look_at_asset": (look_at_asset, "YOUR OWN EYES on an UPLOADED clip or "
                      "image, or a finished RENDER (storage_key from "
                      "list_assets; kind='render' lists past previews/"
                      "finals). Same contract as look_at: pass times=[...] "
                      "(seconds into the clip) and the frames arrive as one "
                      "labeled picture you read yourself, with the same "
                      "tenths grid for reading positions. THE way to choose "
                      "which moment of a long clip to splice in — one call "
                      "over the whole clip, then insert_media with "
                      "clip_start_s at the moment you saw. On a RENDER it is "
                      "how you CHECK YOUR OWN WORK at exact moments — "
                      "narrow times sample frame-accurately, so use it to "
                      "verify a transition junction or an effect the user "
                      "questions before claiming it is fine.",
                      {"asset_key": {"type": "string"},
                       "times": {"type": "array",
                                 "items": {"type": "number"}},
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
                  "keeps neighbouring words whole. SOURCE seconds of the "
                  "main video ONLY — when the user gives times of the "
                  "EDITED video ('cut 12-15 of the video'), or the span "
                  "sits inside an inserted clip, use cut_output_range.",
                  {"start": {"type": "number"}, "end": {"type": "number"},
                   "snap_to_words": {"type": "boolean"}}),
    "cut_output_range": (cut_output_range, "Cut a span of the ASSEMBLED "
                         "program — OUTPUT seconds, the clock the viewer "
                         "and the scene map use — no matter what plays "
                         "there. Kept footage under the span is cut in "
                         "source time; an inserted clip it crosses is "
                         "SPLIT around it (or removed when fully covered); "
                         "one version write, everything re-anchored. THE "
                         "tool for 'cut 12-15 of the video' / 'cut that "
                         "part of the second scene' — never answer that "
                         "cutting inside an insert is impossible, and "
                         "never fake it with set_insert_window (that "
                         "changes WHICH part plays, it cannot remove a "
                         "middle). One range per call; batch several "
                         "calls for several ranges.",
                         {"start": {"type": "number"},
                          "end": {"type": "number"}}),
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
                     "PREMIUM PRESETS (style.preset) are professionally "
                     "designed looks with real fonts. SAFE DEFAULTS: "
                     "'clean' (white Plus Jakarta Sans, complete short "
                     "phrases, size-only hierarchy), 'documentary' "
                     "(restrained subtitles on a translucent contrast "
                     "panel), and 'broadcast' (left-aligned news/explainer "
                     "lower third). SOCIAL/CREATIVE: 'reels' (FLAGSHIP "
                     "short-form system: tight two-line hierarchy, warm hero "
                     "word and multi-stage elastic word landings), 'podcast' (bold "
                     "white words land on screen as spoken, keywords light "
                     "up in the accent color, get a highlight box or serif "
                     "italics, numbers render HUGE), 'beast' (loud "
                     "MrBeast-style: ALL-CAPS impact font, centered, the "
                     "spoken word pops in the accent color), 'karaoke' (an "
                     "accent box follows each spoken word), 'spotlight' "
                     "(ONE glowing word at a time, centred, uppercase — the "
                     "modern single-word look for hype/motivation/fast "
                     "talking; the ONLY preset that belongs mid-frame), "
                     "'elegant' (calm lower-third, serif-italic accents — "
                     "interviews/luxury), 'lyric' (phrase-led music/quote "
                     "typography), plus stacked/iridescent/chrome/editorial/"
                     "fashion/luxe/impact/retro/neon composed looks; "
                     "'classic' is the "
                     "plain legacy look. If style is omitted, the tool "
                     "chooses a coherent preset deterministically "
                     "from the measured format, speech pace and brief — it "
                     "does not make every project wear the same caption skin. "
                     "PLACEMENT: multi-word presets default to the BOTTOM, "
                     "clear of the face — do not move them to 'middle'; "
                     "only a single-word-at-a-time look may sit centred. "
                     "With a preset, sparse emphasis is AUTO-SELECTED from "
                     "the KEPT transcript when emphasis_words is omitted; "
                     "measured vocal stress leads on short-form/cached audio, "
                     "with numbers and semantic outcome words as fallback. "
                     "pass a verbatim list only when specific words are "
                     "required, or [] to explicitly disable hierarchy. "
                     "highlight_color sets the accent (default warm "
                     "yellow); uppercase overrides the preset's casing; "
                     "position bottom/top/middle overrides its placement "
                     "and LOCKS that band for the whole video (no shot-by-"
                     "shot position changes); anchor_y similarly locks an "
                     "exact vertical frame fraction. Omit both to let "
                     "collision-aware placement adapt by shot. "
                     "Other style fields: color '#RRGGBB', size s|m|l|xl "
                     "(presets are already big at 'm'), size_scale "
                     "0.5-3.0, dynamic:true (legacy karaoke, no preset), "
                     "animation fade|pop|slide_up|punch|blur_in|whip|flash|"
                     "rise|drop|elastic|bounce|swing|zoom_blur, or 'none' to turn a "
                     "preset's animation OFF (instant words), "
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
                     "the lines, effect adds chroma/chrome/glow. Production "
                     "controls include outline_color/outline_width, shadow, "
                     "background_color/background_opacity, tracking and "
                     "text_align.",
                     {"mode": {"type": "string"},
                      "style": {"type": "object",
                                 "properties": _STYLE_PROPS},
                      "max_words_per_caption": {"type": "integer"},
                      "emphasis_words": {"type": "array",
                                         "items": {"type": "string"}},
                      "items": {"type": "array",
                                "items": {"type": "object"}}}),
    "search_music": (search_music, "Search the web's music catalogs by "
                     "genre/vibe words — 'dark phonk', 'lofi chill beat', "
                     "'cinematic piano', 'upbeat funk'. Search by what the "
                     "video IS, not a generic mood. Results carry title, "
                     "artist, duration and the exact license terms (public "
                     "domain, credit required, or NON-COMMERCIAL-ONLY) — "
                     "relay the terms. Business/ad briefs automatically "
                     "exclude non-commercial tracks; set commercial_use "
                     "true when an ambiguous brief also needs that safety. "
                     "min_seconds/max_seconds filter length. For a "
                     "SPECIFIC song, a pasted LINK is fetch_url's job; a "
                     "TRENDING platform sound only the user can provide "
                     "(upload or a clip carrying it) — say so instead of "
                     "substituting silently.",
                     {"query": {"type": "string"},
                      "min_seconds": {"type": "number"},
                      "max_seconds": {"type": "number"},
                      "commercial_use": {"type": "boolean"}}),
    "fetch_music": (fetch_music, "Download ONE search_music result (by its "
                    "id) into the project as a normal music asset — "
                    "returns the storage_key for add_music. Repeat the "
                    "license line to the user when it "
                    "carries an obligation (credit, or "
                    "non-commercial-only).",
                    {"id": {"type": "string"}}),
    "find_song": (find_song, "Find web links for a SPECIFIC song the user "
                  "NAMED ('add Blinding Lights by The Weeknd') — the case "
                  "search_music cannot serve, because named commercial "
                  "tracks are not in the open catalogs. Returns candidate "
                  "links best-guess first; pick the real thing (artist's "
                  "own/'- Topic' channel or 'Official Audio'; no "
                  "lyric/sped-up/loop/cover versions unless asked; never "
                  "a full album/mix — one track only), then "
                  "fetch_url(url, as_kind='music') downloads the pick. "
                  "Always tell the user which version you grabbed. Search "
                  "can verify the likely recording, NOT a usage license; a "
                  "public/downloadable upload does not grant republication "
                  "rights, so disclose that. For a "
                  "genre/vibe request use search_music instead; for a "
                  "trending platform sound only the user can provide the "
                  "file.",
                  {"query": {"type": "string"}}),
    "add_music": (add_music, "Mix music into the edit. The defaults are "
                  "CONTEXT-AWARE: under speech the track sits low as a bed "
                  "(-18dB, ducked); when NO speech survives under the window "
                  "the music is the LEAD audio (-4dB, no ducking) so the "
                  "user actually hears it. Pass gain_db/duck only to "
                  "override that. storage_key is an exact key from "
                  "list_assets(kind='music') — the user's own uploads or a "
                  "track fetch_music just downloaded — never invent one. "
                  "Choose from the user's direction, metadata, context and "
                  "measured get_audio_analysis evidence. "
                  "start/end are OUTPUT-timeline seconds "
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
    "find_footage": (find_footage, "Find REAL footage of a NAMED topic on "
                     "the web — the b-roll move: the speaker mentions Elon "
                     "Musk, the cut shows his rocket. Query the concrete "
                     "THING ('spacex starship launch', 'tesla factory'), "
                     "not a mood — search_stock covers generic visuals "
                     "('busy city'), THIS covers named people, companies, "
                     "products, events. Returns candidate links best-guess "
                     "first; pick real footage of the subject (short "
                     "clips), then fetch_url(url, as_kind='clip'), "
                     "look_at_asset for the right seconds, and a cutaway "
                     "or insert at the mention. Tell the user what footage "
                     "you used (title + channel).",
                     {"query": {"type": "string"}}),
    "search_sfx": (search_sfx, "Search the web for a REAL recorded sound "
                   "effect — the editor's whoosh, camera shutter, UI "
                   "click, pop, riser. Query by the sound's PHYSICAL name "
                   "('whoosh', 'camera shutter', 'keyboard click'), not a "
                   "mood. Results carry duration and license terms "
                   "(public domain, credit, or NON-COMMERCIAL-ONLY) — "
                   "relay the terms. max_seconds caps length (default "
                   "15s; one-shots are seconds long).",
                   {"query": {"type": "string"},
                    "max_seconds": {"type": "number"}}),
    "fetch_sfx": (fetch_sfx, "Download ONE search_sfx result (by its id) "
                  "into the project — returns the storage_key for "
                  "add_sfx. Repeat the "
                  "license line to the user when it carries an obligation.",
                  {"id": {"type": "string"}}),
    "add_web_sfx": (add_web_sfx, "ONE-CALL on-demand sound design: search "
                    "real Openverse/Freesound recordings, rank clean "
                    "physical one-shots above loops/music/ambience, fetch "
                    "the best available result, and place it at an exact "
                    "OUTPUT-timeline second. Use for 'add a cinematic "
                    "whoosh at 3.2s', 'put a shutter on this cut', or any "
                    "specific requested sound. The result reports the real "
                    "source and license. gain_db defaults -6; max_seconds "
                    "defaults 15.",
                    {"query": {"type": "string"},
                     "at": {"type": "number"},
                     "gain_db": {"type": "number"},
                     "max_seconds": {"type": "number"}}),
    "add_sfx": (add_sfx, "Punctuate a MOMENT with a one-shot sound effect — a "
                "whoosh on a cut, a click on a beat, an impact on a reveal. "
                "Choose it when the brief, format, timing, or your editorial "
                "judgment says sound design improves the cut. Analysis, "
                "metadata, and deterministic preview AUDIO CHECK are useful "
                "evidence; uncertainty is something to judge rather than "
                "a reason the tool becomes unavailable. "
                "storage_key is an exact key from fetch_sfx or "
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
                          "\"clean\"}} (see add_captions for the preset "
                          "menu: clean/documentary/broadcast/podcast/beast/"
                          "karaoke/spotlight/elegant/"
                          "stacked/.../classic), "
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
                          "An explicit position or anchor_y locks captions "
                          "there for the whole video and removes any old "
                          "shot-aware placement track — use this when the "
                          "user says captions must stop moving. "
                          "For fine size control that the s|m|l|xl buckets "
                          "can't hit pass size_scale (0.5-3.0; 1.5 = 50% "
                          "bigger). Outline, shadow, backing panel, tracking "
                          "and text alignment are independently editable. "
                          "Works for from_transcript and manual "
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
    "auto_reframe": (auto_reframe, "THE tool for 'make it 9:16 / vertical / "
                     "for TikTok'. It samples frames across the kept footage "
                     "and MEASURES two things before writing the frame: where "
                     "the subject is (faces found in the pixels; vision or a "
                     "detail-energy estimate only when there is no face), and "
                     "whether a crop is the right operation at all — how much "
                     "of the picture's detail would survive the crop window. "
                     "With mode='auto' (default) footage with a subject gets "
                     "a crop aimed at it, and footage whose content runs to "
                     "the edges (gameplay, screen recordings, wide scenes) is "
                     "FITTED into the new frame over a blurred backdrop so "
                     "nothing is cut off — cropping those is the 'it just "
                     "truncated my video instead of adjusting it' complaint. "
                     "Pass mode explicitly to force one. Read what it reports "
                     "and repeat THAT.",
                     {"ratio": {"type": "string",
                                "enum": ["9:16", "1:1", "4:5", "16:9",
                                         "source"]},
                      "mode": {"type": "string",
                               "enum": ["auto", "crop", "pad", "pad_blur"]}}),
    "insert_media": (insert_media, "Splice an uploaded video clip or image "
                     "INTO the edit at ANY position in the FINAL edited "
                     "video — mid-take positions split the take cleanly at a "
                     "word edge, so 'in the middle of the talk' works "
                     "exactly. NEVER splice a clip the user sent as a STYLE "
                     "REFERENCE ('watch this', 'make it like this', 'use "
                     "this as reference', 'recreate this', a YouTube they "
                     "asked you to study) — look_at_asset it, extract_audio "
                     "/ add_music if they want THAT song, and rebuild the "
                     "look from the MAIN gameplay/footage. If the studio "
                     "already placed a reference on the timeline, "
                     "remove_insert it. "
                     "Call list_assets(kind='clip') or kind='image' "
                     "first and pass the exact storage_key. duration_s: how "
                     "long the insert plays (image default 3.0s; a video "
                     "defaults to its available length). clip_start_s: where in the source "
                     "clip the window starts — use look_at_asset to pick "
                     "the right moment. motion (images only): 'zoom_in', "
                     "'zoom_out', 'pan_left' or 'pan_right' gives the still "
                     "a slow Ken Burns move instead of sitting frozen — use "
                     "it whenever the user wants an image to feel animated. "
                     "Inserted media is NOT transcribed — captions cover "
                     "the main footage only. fit defaults to 'auto': the "
                     "WHOLE asset is preserved over a blurred extension, so "
                     "a portrait card cannot be center-cropped into an empty "
                     "middle band. fit='crop' fills edge-to-edge. Assets may "
                     "be reused whenever the edit benefits from repetition "
                     "or a different source window.",
                     {"asset_key": {"type": "string"},
                      "at_output_s": {"type": "number"},
                      "duration_s": {"type": "number"},
                      "clip_start_s": {"type": "number"},
                      "fit": {"type": "string",
                              "enum": ["auto", "crop", "pad", "pad_blur"]},
                      "motion": {"type": "string",
                                 "enum": ["zoom_in", "zoom_out",
                                          "pan_left", "pan_right"]}}),
    "set_insert_window": (set_insert_window, "Change which part of an "
                          "already-spliced clip plays, IN PLACE — duration_s "
                          "for how long it runs, clip_start_s for where in the "
                          "clip it starts, rate for how FAST it plays. USE "
                          "THIS instead of remove_insert + "
                          "insert_media to trim or re-window a clip that is "
                          "already on the timeline: removing and re-adding "
                          "costs two edit versions and two renders, and the "
                          "user watches their clip disappear and come back. "
                          "rate (0.25-4, round 76) is THE tool for 'speed up "
                          "that scene instead of cutting it': rate alone "
                          "keeps the clip window and shortens the block (a "
                          "10s screen recording at rate 2 becomes a 5s scene "
                          "with nothing lost, audio pitch-corrected); with "
                          "duration_s the block is duration_s long and "
                          "consumes duration_s*rate of clip. "
                          "TO SPLIT a spliced clip in two: shorten it here to "
                          "the first part, then insert_media the same "
                          "asset_key at the SAME at_output_s with clip_start_s "
                          "set to where the first part ended — the two halves "
                          "play in the order you created them. "
                          "crop=[x0,y0,x1,y1] (round 77) shows ONE REGION of "
                          "the clip as the whole scene, letterboxed (black "
                          "bars) — THE tool for 'show the full timeline "
                          "strip/panel, nothing else, static': a zoom's 16:9 "
                          "window can never hold a wide UI strip without also "
                          "holding what sits above it, so crop the insert "
                          "instead and leave the zoom wide over it. "
                          "Fractions of the CLIP's frame, read off a "
                          "look_at_asset grid; pass 'full' to clear. "
                          "mute=true (round 78) silences the scene's OWN "
                          "audio — THE answer to 'mute that clip' / 'mute "
                          "all scenes' (set_volume only reaches the main "
                          "footage; muting every scene = set_volume on the "
                          "kept spans + mute on each video insert). "
                          "mute=false brings it back. "
                          "fit (round 79) sets how THIS scene maps onto the "
                          "canvas: 'pad' shows the WHOLE picture letterboxed "
                          "on black — THE fix for a portrait image or clip "
                          "that the default cover-crop beheads ('the image "
                          "looks corrupted / cut off') — 'pad_blur' fits it "
                          "over a blurred backdrop, 'crop' forces the "
                          "cover-crop, 'auto' clears the override. "
                          "rotation repairs THIS scene clockwise by "
                          "0/90/180/270 degrees — use it for one sideways "
                          "phone clip instead of a whole-program custom "
                          "filter.",
                          {"id": {"type": "string"},
                           "duration_s": {"type": "number"},
                           "clip_start_s": {"type": "number"},
                           "rate": {"type": "number"},
                           "crop": {"type": "array",
                                    "items": {"type": "number"}},
                           "mute": {"type": "boolean"},
                           "fit": {"type": "string",
                                   "enum": ["pad", "pad_blur", "crop",
                                            "auto"]},
                           "rotation": {"type": ["integer", "string"]}}),
    "move_insert": (move_insert, "MOVE A SPLICED SCENE — reorder an inserted "
                    "clip between any other scenes, in place. after_id is "
                    "the insert it should play right AFTER (the scene map "
                    "in get_edl names each scene's insert id); omit it to "
                    "play FIRST at its boundary. THE tool for 'move this "
                    "clip between those two scenes' / 'put the uploaded "
                    "video after the intro' — never remove + re-insert, "
                    "which costs two versions and the user watches the clip "
                    "vanish. Everything anchored to the moved scenes "
                    "(zooms, takeovers, texts) re-anchors and follows.",
                    {"id": {"type": "string"},
                     "after_id": {"type": "string"}}),
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
                  "add_music (audio). Fetching verifies neither ownership "
                  "nor license: the returned RIGHTS CHECK must be relayed; "
                  "a downloadable file is not permission to republish.",
                  {"url": {"type": "string"},
                   "as_kind": {"type": "string",
                               "enum": ["clip", "music", "image"]}}),
    "search_stock": (search_stock, "SEARCH for b-roll images/clips the "
                     "user does not have. Two kinds of query work: generic "
                     "visuals ('a busy city', 'ocean waves') AND — with "
                     "kind='photo' — REAL topical subjects ('Elon Musk', "
                     "'SpaceX Starship', a company, a product) from "
                     "Wikimedia/Flickr's photo record; relay each photo's "
                     "license line when it carries an obligation. For real "
                     "topical VIDEO use find_footage instead. Returns "
                     "a VISUALLY REVIEWABLE, provider-diverse grid (Pexels/"
                     "Pixabay/Openverse when configured), not a homogeneous "
                     "first-provider dump. Candidates ONLY: nothing is downloaded and nothing "
                     "enters the video. kind 'video' (default) or 'photo'. "
                     "orientation defaults to the project's output frame, "
                     "so a 9:16 edit gets vertical footage. Then call "
                     "add_stock_media with the best id.",
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
                        "it in. Representative frames from the ACTUAL "
                        "downloaded rendition are automatically attached; "
                        "inspect them before placement. Always tell the user "
                        "which shot you used.",
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
        "PLACE A SCREEN RECORDING AND CUT IT LIKE A PRODUCT VIDEO — one call. "
        "Splices the clip into the edit, then puts a zoom on each run of "
        "POSITIONED clicks. Click sounds are optional; choose them when they "
        "serve the edit. "
        "Works on ANY video clip, not just a record_website_demo capture. On "
        "a capture I made, the event track is exact: the frame pushes in and "
        "TRAVELS between the buttons, with a soft pop on each page change and "
        "a swipe under each scroll. On a recording the USER made, pass "
        "click_times=[...] (seconds into the clip) to supply timing. Clicks "
        "cannot be located from timing alone, so no zoom is invented without "
        "a position. With neither, it "
        "still places the clip and tells you plainly that nothing was synced. "
        "at_output_s defaults to the END of the current edit; zoom_strength "
        "0.05-4.5 (0.4 default — screen text needs a real push to read); set "
        "zooms=false to place it plainly; click_sounds=true is opt-in. Follow "
        "up "
        "with add_zoom_path to make the frame travel on a user recording, and "
        "enhance_cursor if the pointer is too small to follow.",
        {"asset_key": {"type": "string"},
         "at_output_s": {"type": "number"},
         "zoom_strength": {"type": "number"},
         "click_sounds": {"type": "boolean"},
         "zooms": {"type": "boolean"},
         "click_times": {"type": "array", "items": {"type": "number"}}}),
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
                 "emphasis on a key line. strength 0.05-4.5 (default 0.15; "
                 "above 1.0 is a dramatic 2x+ punch). mode: "
                 "'punch' (default, instant step), 'ease' (smoothly ramps "
                 "in and out — use when the user wants it subtle/animated), "
                 "'push_in' / 'pull_out' (continuous Ken Burns drift across "
                 "the whole window — use for slow cinematic movement). TWO "
                 "ways to aim, and they answer different requests: "
                 "rect=[x0,y0,x1,y1] (fractions of the output frame, read "
                 "off look_at's grid) FRAMES A REGION — the tool solves "
                 "strength and centre so that box fills the frame with "
                 "margin, THE way to 'zoom into the message / that button / "
                 "this panel', and its result reports where the region "
                 "lands on screen. cx/cy instead PIN A POINT: that point "
                 "keeps its exact screen position while everything "
                 "magnifies around it — right for emphasis on a subject "
                 "that is already well-composed, and wrong for framing a "
                 "thing near an edge (an edge point stays at the edge at "
                 "any strength — it never slides to centre). Pass rect OR "
                 "cx/cy, not both; if both arrive the rect wins (it already "
                 "determines the centre) and the call still succeeds. "
                 "Omitting all targets uses the frame center and returns a "
                 "quality advisory. Coordinates may come from look_at, the "
                 "filmstrip, user direction, or the editor's own judgment; "
                 "no prior evidence call is required. Use as many zooms "
                 "at emphatic moments, not wall-to-wall; for automatic "
                 "zooms on the strongest spoken words use "
                 "punch_in_on_emphasis. And if the zoom should MOVE while "
                 "pushed in — 'then move it to X', 'keep it and go to the "
                 "next message', 'follow the cursor' — that is ONE "
                 "add_zoom_path (its keyframes take rect too), never a "
                 "chain of static zooms.",
                 {"start": {"type": "number"}, "end": {"type": "number"},
                  "strength": {"type": "number"},
                  "mode": {"type": "string",
                           "enum": ["punch", "ease", "push_in",
                                    "pull_out"]},
                  "cx": {"type": "number"},
                  "cy": {"type": "number"},
                  "rect": {"type": "array",
                           "items": {"type": "number"}}}),
    "remove_zoom": (remove_zoom, "Remove one zoom by its id (see "
                    "get_edl).", {"id": {"type": "string"}}),
    "add_zoom_path": (
        add_zoom_path,
        "A ZOOM THAT MOVES — THE tool for 'make the zoom follow the cursor' "
        "/ 'move the zoom between buttons' / 'stay zoomed and then move to "
        "the next thing' on ANY footage, including a screen recording the "
        "user made themselves. Any request where ONE zoom should hold, "
        "travel, or visit several subjects in sequence is THIS tool — never "
        "a chain of static add_zoom calls, which cut out and back instead "
        "of moving. keyframes is a list of at least two points, each "
        "{t, rect, strength} or {t, cx, cy, strength}: t is OUTPUT-timeline "
        "seconds; rect=[x0,y0,x1,y1] FRAMES the thing to look at there "
        "(fractions of the frame from look_at's grid — the same solver as "
        "add_zoom rect, so edge subjects come out framed, and omitting "
        "strength on a rect keyframe picks the strength that fits it); "
        "cx/cy instead PIN a point ((0,0) = top-left). strength 0-4.5 "
        "interpolates between keyframes, so the frame can push in as it "
        "arrives and ease out as it leaves; to HOLD on a subject, repeat "
        "its keyframe at the hold's start and end times. The window runs "
        "from the first t to the last. NO ramp is added at the edges: give "
        "the first and last keyframe strength 0 for a seamless entry and "
        "exit (a strength-0 rect keyframe still aims where the move is "
        "going). ease: 'cubic_in_out' (default — settles at each keyframe, "
        "the right answer for stopping at buttons) or 'linear' (constant "
        "speed, for a steady scan across a wide screenshot). It re-anchors "
        "across later cuts exactly like add_zoom, so cutting elsewhere "
        "never strands it. Remove the whole move with remove_zoom_path.",
        {"keyframes": {"type": "array", "items": {"type": "object"}},
         "ease": {"type": "string", "enum": ["cubic_in_out", "linear"]}}),
    "remove_zoom_path": (
        remove_zoom_path,
        "Remove one keyframed travelling zoom by its id (see get_edl). Use "
        "remove_zoom for ordinary punch/ease zooms.",
        {"id": {"type": "string"}}),
    "enhance_cursor": (
        enhance_cursor,
        "MAKE THE MOUSE POINTER BIGGER AND STEADIER — THE tool for 'the "
        "cursor is too small' / 'too jittery' on a screen recording. It finds "
        "the pointer in the source frames, repaints the original out, and "
        "redraws it at `scale`x (1-4; 2 is the usual answer) along a path "
        "filtered to remove hand tremor — `smoothing` 0-1, where fast "
        "deliberate moves stay sharp at any setting. click_times is a list of "
        "SOURCE-video seconds that get an expanding ripple: I CANNOT see "
        "clicks in the pixels (nothing distinguishes a press from a hover), "
        "so either pass the times record_website_demo reported, or ask the "
        "user when the clicks were — never guess them. Set "
        "click_highlight=false to skip the ripples. This bakes into the "
        "source copy the render reads, so every cut keeps it and no timestamp "
        "moves; it reports what fraction of frames the pointer was actually "
        "found in and refuses outright on footage that has no visible cursor. "
        "Undo with remove_cursor_enhance.",
        {"scale": {"type": "number"}, "smoothing": {"type": "number"},
         "click_highlight": {"type": "boolean"},
         "click_times": {"type": "array", "items": {"type": "number"}}}),
    "remove_cursor_enhance": (
        remove_cursor_enhance,
        "Put the original mouse pointer back (re-derives from the untouched "
        "source).", {}),
    "set_screen_frame": (
        set_screen_frame,
        "THE tool for 'that floating rounded window on a gradient' look — the "
        "standard treatment for a screen recording or app demo. The finished "
        "picture is inset, its corners rounded, a soft shadow dropped under "
        "it, and it floats on a solid colour or a two-colour gradient. "
        "inset 0.02-0.35 (0.08 default — how much room the backdrop gets); "
        "radius 0-0.25 as a fraction of the picture's short side; shadow 0-1; "
        "background/background2 are #RRGGBB (pass background2 for a gradient, "
        "or 'none' to go flat) with direction vertical/horizontal/diagonal/"
        "radial — the same gradient renderer add_color_screen uses, so a "
        "backdrop can match an interstitial exactly. Calling it again edits "
        "the settings rather than stacking. It applies to the WHOLE finished "
        "picture (captions and overlays scale with it, because they are "
        "inside the window) and changes no timing at all. Remove with "
        "remove_screen_frame.",
        {"inset": {"type": "number"}, "radius": {"type": "number"},
         "shadow": {"type": "number"}, "background": {"type": "string"},
         "background2": {"type": "string"},
         "direction": {"type": "string",
                       "enum": ["vertical", "horizontal", "diagonal",
                                "radial"]}}),
    "remove_screen_frame": (
        remove_screen_frame,
        "Remove the floating rounded window — the picture goes back to "
        "full-bleed.", {}),
    "add_aspect_shift": (
        add_aspect_shift,
        "CHANGE THE ASPECT RATIO MID-VIDEO, SMOOTHLY — THE tool for 'go "
        "vertical for this bit' / 'squeeze to square here and back'. At "
        "at_output_s the visible frame MORPHS to `ratio` over duration_s "
        "(0.1-4s, 0.8 default) with an eased close-in, and stays there until "
        "the next shift; add another with ratio='source' to open back out. "
        "The rendered file keeps ONE resolution — it has to, that is what a "
        "video file is — so the change is the frame itself closing in, which "
        "is exactly what a smooth aspect change looks like and is why it "
        "cannot desync audio or move a caption. zoom=true (default) pushes "
        "the picture in as the frame narrows so the subject holds its size. "
        "color is the bars' colour. For changing the aspect of the WHOLE "
        "video use set_frame or auto_reframe instead. Remove one with "
        "remove_aspect_shift.",
        {"at_output_s": {"type": "number"},
         "ratio": {"type": "string",
                   "enum": ["source", "16:9", "9:16", "1:1", "4:5", "4:3"]},
         "duration_s": {"type": "number"}, "zoom": {"type": "boolean"},
         "color": {"type": "string"}}),
    "remove_aspect_shift": (
        remove_aspect_shift,
        "Remove one mid-video aspect change by its id (see get_edl).",
        {"id": {"type": "string"}}),
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
                        "report THAT number, not the cut count. Pass "
                        "scope='every_cut' whenever that stronger treatment "
                        "serves the edit. Styles: 'dip_black' "
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
                                       "jump cuts; use it whenever that is "
                                       "the intended treatment."}}),
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
    "erase_region": (erase_region, "TRULY REMOVE whatever is inside a "
                     "rectangle — repaints those pixels and reconstructs the "
                     "background, so the thing is GONE, not covered. Use it "
                     "for a word, a sign, a sticker, a logo, a person's name "
                     "on screen, or any object the user wants taken out. "
                     "SEVERAL marks (a watermark AND a handle AND a caption "
                     "bar) go in ONE call as regions=[{x,y,w,h,fill?,start?,"
                     "end?}, ...]. The repaint costs time proportional to "
                     "the WINDOW you erase, not the video — so pass "
                     "start/end around when the mark is actually visible and "
                     "the erase lands in seconds; earlier erases are never "
                     "redone. x,y = TOP-LEFT corner, w,h = size, all "
                     "FRACTIONS (0-1) "
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
                      "fill": {"type": "string", "enum": ["text", "box"]},
                      "regions": {"type": "array", "items": {
                          "type": "object", "properties": {
                              "x": {"type": "number"},
                              "y": {"type": "number"},
                              "w": {"type": "number"},
                              "h": {"type": "number"},
                              "fill": {"type": "string",
                                       "enum": ["text", "box"]},
                              "start": {"type": "number"},
                              "end": {"type": "number"}},
                          "required": ["x", "y", "w", "h"]}}}),
    "reset_edit": (reset_edit, "Start again from the full untouched source "
                   "video whenever that is the best route for the brief or "
                   "current edit. The tool creates a new version, so every "
                   "prior cut remains recoverable in version history. It "
                   "drops every current cut, caption, track and effect; state "
                   "honestly when you chose to rebuild from source.", {}),
    "remove_erase": (remove_erase, "Undo an erase: put the original pixels "
                     "back for one erased region by its id (see get_edl), or "
                     "for ALL of them when id is omitted. Instant for "
                     "window-patch erases; legacy whole-file erases rebuild "
                     "from the untouched original.",
                     {"id": {"type": "string"}}),
    "add_voiceover": (add_voiceover, "Lay an uploaded audio file OVER the "
                      "whole program from start_output_s (a position in the "
                      "FINAL edited video, default 0). duck_others (default "
                      "true) lowers all other audio 12dB while it plays. "
                      "source_offset_s seeks into the file in place (use it "
                      "to start a narration/song excerpt at the right moment; "
                      "never create an externally trimmed workaround). Use a "
                      "storage_key from list_assets(kind='music').",
                      {"asset_key": {"type": "string"},
                       "start_output_s": {"type": "number"},
                       "source_offset_s": {"type": "number"},
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
                    "should continue. Assets may be reused whenever the edit "
                    "benefits from repetition or a different source window.",
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
    "add_screen_takeover": (
        add_screen_takeover,
        "PUSH INTO A SCREEN IN THE SHOT AND LET WHAT IS ON IT BECOME THE "
        "WHOLE VIDEO — THE tool for 'zoom into the laptop and continue with "
        "the other scene', 'make it go into the phone screen', 'transition "
        "into the monitor smoothly', and every request that describes the "
        "camera travelling INTO a device and the content taking over. It is "
        "ONE continuous move, not a zoom plus a cut: the asset is corner-"
        "pinned onto the glass so it plays ON the screen inside the shot, the "
        "camera pushes in, the picture flattens out of the screen into the "
        "full frame, and the clip cuts in on the SAME frame the push ends on "
        "— which is why the join cannot be seen. Do NOT build this out of "
        "add_zoom + insert_media: an overlay is drawn ABOVE the zoom, so the "
        "content sits flat and still while the shot pushes past it, and the "
        "cut lands as a jump. "
        "at_output_s is where in the FINAL video the takeover FINISHES and "
        "the asset is full screen (the push happens in the duration_s before "
        "it). The device shot may be the MAIN footage or a SPLICED-IN video "
        "clip: point at_output_s inside an inserted clip that shows the "
        "device and the push rides that clip's tail, arriving exactly where "
        "it ends (I snap there and say so). "
        "duration_s 0.4-5, default 1.2 — 1.0-1.5 is the move people "
        "mean. I find the corners THREE ways, in order of trust: first I "
        "MATCH the content's own pixels against the filmed glass (the "
        "laptop was almost always filmed displaying that very recording — "
        "a feature homography gives exact corners INCLUDING rotation and "
        "keystone, and the pinned clip then grows out of the very pixels it "
        "was filmed playing on, living on the glass from the window's "
        "start); else I MEASURE a screen-shaped region from the pixels; "
        "else I READ the corners with the vision model. When the corners "
        "are matched the content is on the glass the whole window; when "
        "they are only measured or read, the glass shows what was FILMED "
        "until the push is ~half done and the content dissolves on late "
        "(a scene switch visible in a wide shot of the room is the #1 "
        "thing users call 'not smooth'), fully there before the picture "
        "lands. Momentum carries through the cut either way (a brief "
        "settle past full frame); ease='accelerate' dives with speed "
        "peaking at the cut. "
        "Pass `corners` only to override all of that (8 numbers x0,y0,x1,y1,"
        "x2,y2,x3,y3 as FRACTIONS of the frame in the order top-left, "
        "top-right, BOTTOM-LEFT, bottom-right — or a {x,y,w,h} rectangle). "
        "clip_start_s picks where in the asset the takeover starts playing; "
        "hold_s is how long the asset stays full screen afterwards (default: "
        "the rest of it). push 0-1 is how far the camera travels (1 = all "
        "the way, the default — there is no further zoom past 1; a push that "
        "feels weak is usually a short duration_s, so lengthen the move "
        "instead). ease: 'smooth' (default), 'accelerate', "
        "'linear'. settle:false turns OFF the through-cut momentum (the "
        "brief zoom past full frame after the handoff that settles back) — "
        "use it when the user says the video 'keeps zooming after the "
        "transition' or 'zooms then returns', or asks for a dead-flat "
        "landing. "
        "It REFUSES rather than guessing when it cannot measure the screen, "
        "and refuses when the screen is under 8% of the frame (the push "
        "would be a >12x blowup). TO CHANGE AN EXISTING TAKEOVER (flat "
        "landing, different ease/length), call this again at the SAME "
        "arrival: it REPLACES that takeover in one write — parameters you "
        "omit are inherited, and its accepted pin corners are reused "
        "instead of re-measured (pass corners to force a re-measure). "
        "remove_screen_takeover is only for taking the transition OUT.",
        {"asset_key": {"type": "string"},
         "at_output_s": {"type": "number"},
         "duration_s": {"type": "number"},
         "corners": {"type": "array", "items": {"type": "number"}},
         "clip_start_s": {"type": "number"},
         "hold_s": {"type": "number"},
         "push": {"type": "number"},
         "ease": {"type": "string",
                  "enum": ["smooth", "accelerate", "linear"]},
         "settle": {"type": "boolean"}}),
    "remove_screen_takeover": (
        remove_screen_takeover,
        "Undo a screen takeover by its id (see get_edl): the corner pin, the "
        "camera push and the clip it handed off to all go together. Pass "
        "keep_clip=true to leave the clip spliced in as a plain cut.",
        {"id": {"type": "string"}, "keep_clip": {"type": "boolean"}}),
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
                 "entrance/exit: 'none' (INSTANT — the text is simply there "
                 "at frame one and simply gone at the end, no animation at "
                 "all; use when the user wants no effect), fade, pop, "
                 "slide_up, blur_in, whip, rise, drop, plus 'typewriter' "
                 "(entrance only); uppercase forces "
                 "casing; box adds a backing panel.Use for text the user "
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
    "add_kinetic_text": (add_kinetic_text, "CHOREOGRAPH THE SPOKEN WORDS "
                         "onto the screen in ONE pass — the signature move "
                         "of top creator reels: each phrase of the "
                         "transcript appears AT the instant it is spoken, "
                         "placed in the empty space around the speaker, "
                         "alternating sides, animated, and holding until "
                         "the next phrase replaces it. Use it for 'edit "
                         "this' talking-head footage, promo/educator "
                         "reels, and whenever the house style calls for "
                         "speech-carried typography — instead of dozens "
                         "of add_text calls. start/end (PROGRAM seconds) "
                         "scope it; default whole program. emphasis_words "
                         "get the accent color, a size bump and a pop. "
                         "zone: 'upper' (beside/above the head — default), "
                         "'lower', 'sides'. Mutes bottom captions over its "
                         "window so words never print twice. Each phrase "
                         "is a normal text item — inspect with get_edl, "
                         "remove_text by id, or remove and re-run to "
                         "restyle. AFTER rendering, LOOK at the frames: "
                         "wrong zone for this framing -> re-run with "
                         "another zone.",
                         {"start": {"type": "number"},
                          "end": {"type": "number"},
                          "accent_color": {"type": "string"},
                          "emphasis_words": {"type": "array",
                                             "items": {"type": "string"}},
                          "zone": {"type": "string",
                                   "enum": list(_KINETIC_ZONES)},
                          "color": {"type": "string"},
                          "font": {"type": "string",
                                   "enum": list(TEXT_FONTS)},
                          "size_scale": {"type": "number"}}),
    "add_text_behind": (add_text_behind, "Put words BEHIND the moving subject "
                        "— the person walks IN FRONT of the letters, the way a "
                        "title painted on the street or the wall behind them "
                        "would. This is the 'text behind me walking' / 'name "
                        "behind the subject' move, and it is a REAL depth "
                        "composite, not a fade: a person-matting model that "
                        "carries temporal state between frames cuts the "
                        "subject out of every frame — dark clothes on a dark "
                        "wall, handheld wobble and a moving camera are all "
                        "fine, and the mask holds steady instead of "
                        "flickering — and the renderer lays them back over "
                        "the words. PEOPLE occlude the words (with whatever "
                        "they carry); static objects — furniture, walls — do "
                        "NOT: over those the words read as an ordinary "
                        "title, which is what keeps the occlusion steady. "
                        "Say so if the user asks about an object. Same styling arguments as "
                        "add_text "
                        "(template/x/y/size_scale/color/font/entrance/exit); "
                        "at_output_s + duration_s are where in the EDITED video "
                        "the words appear. REQUIREMENTS I check and refuse on, "
                        "so read the reply: a PERSON must be visible in the "
                        "window (nothing to go behind otherwise — I say so and "
                        "you offer add_text instead), they must not fill most "
                        "of the frame (the words would never be visible), "
                        "the window must be inside ONE take with no cut in it, "
                        "and no speed ramp over that footage. I also report how "
                        "much of the text the subject actually crosses — if "
                        "that is near zero the user will see a plain title, so "
                        "move the text or the window. BIG TYPE IS THE LOOK: "
                        "the subject should cross the MIDDLE of tall glyphs "
                        "with their tops and bottoms staying readable — that "
                        "is what reads as depth. A small line sits entirely "
                        "inside the body and whole words vanish, so titles "
                        "default to size_scale 2.4 here; NEVER shrink the "
                        "text to 'fix' hidden letters — enlarge it or shorten "
                        "the phrase. Do NOT put a zoom or a "
                        "stabilize pass over the window. Remove it with "
                        "remove_text like any other text.",
                        {"text": {"type": "string"},
                         "at_output_s": {"type": "number"},
                         "duration_s": {"type": "number"},
                         "template": {"type": "string",
                                      "enum": list(TEXT_TEMPLATES)},
                         "x": {"type": "number"},
                         "y": {"type": "number"},
                         "size_scale": {"type": "number"},
                         "color": {"type": "string"},
                         "accent_color": {"type": "string"},
                         "font": {"type": "string", "enum": list(TEXT_FONTS)},
                         "entrance": {"type": "string",
                                      "enum": list(TEXT_ANIMS)},
                         "exit": {"type": "string",
                                  "enum": [a for a in TEXT_ANIMS
                                           if a != "typewriter"]},
                         "uppercase": {"type": "boolean"},
                         "box": {"type": "boolean"}}),
    "remove_text": (remove_text, "Remove one text element by its id (see "
                    "get_edl) — including one placed behind the subject.",
                    {"id": {"type": "string"}}),
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
    "set_caption_fixes": (set_caption_fixes, "Correct the SPELLING or "
                          "capitalization of burned captions: replacements is "
                          "an array of [wrong, right] pairs, e.g. "
                          "[[\"dios\",\"Dios\"],[\"ushula\",\"Ujjwala\"]]. "
                          "Use it whenever a user says a caption spells a "
                          "name wrong, or when the transcript lower-cases "
                          "names that must be capitalized (people, places, "
                          "brands, religious names). Matching ignores case "
                          "and punctuation and fixes every occurrence; both "
                          "sides must have the SAME word count. Word timings "
                          "are never touched. clear=true removes all fixes.",
                          {"replacements": {"type": "array",
                                            "items": {"type": "array",
                                                      "items": {"type":
                                                                "string"}}},
                           "clear": {"type": "boolean"}}),
    "add_freeze_frame": (add_freeze_frame, "FREEZE the picture on a moment "
                         "and hold it, optionally with a line of text over "
                         "the held frame — the 'pearl' / power-phrase move: "
                         "the frame stops, blurs and darkens behind big "
                         "centred words, then the video continues. "
                         "at_output_s is the moment in the EDITED video to "
                         "freeze; duration_s 2-4s reads well; blur 0-1 and "
                         "darken 0-0.85 treat the still (0.45/0.35 is the "
                         "classic look, 0/0 keeps it clean); motion "
                         "zoom_in/zoom_out/pan_left/pan_right gives the still "
                         "a slow drift so it does not sit dead; text + "
                         "subtitle are burned centred and BOUND to the frozen "
                         "frame. audio_mode='pause' (default) is a real cut: "
                         "the program pauses and everything after shifts. "
                         "audio_mode='continue' freezes ONLY the picture as "
                         "a full-frame cover while the original speech, "
                         "music and timeline continue — use it when a visual "
                         "must hold over an ongoing phrase without stretching "
                         "or desynchronizing the audio.",
                         {"at_output_s": {"type": "number"},
                          "duration_s": {"type": "number"},
                          "text": {"type": "string"},
                          "subtitle": {"type": "string"},
                          "blur": {"type": "number"},
                          "darken": {"type": "number"},
                          "motion": {"type": "string",
                                     "enum": list(INSERT_MOTIONS)},
                          "template": {"type": "string",
                                       "enum": list(TEXT_TEMPLATES)},
                          "color": {"type": "string"},
                          "accent_color": {"type": "string"},
                          "font": {"type": "string",
                                   "enum": list(TEXT_FONTS)},
                          "audio_mode": {"type": "string",
                                         "enum": ["pause", "continue"]}}),
    "add_stylize": (add_stylize, "Layer a windowed finishing effect on the "
                    "program picture: 'grain' (film grain), 'vignette' "
                    "(darkened corners), 'glow' (soft bloom), 'halation' "
                    "(warm red-orange highlight bloom like exposed film), 'chromatic' "
                    "(RGB fringe), 'dream_blur' (soft dreamy diffusion), "
                    "'vhs' (tape look), 'flash' (strobe pop), 'shake' "
                    "(adds camera shake), 'sharpen' / 'denoise' (picture "
                    "quality — prefer enhance_video, which orders them "
                    "correctly), 'motion_blur' (real frame blending; only "
                    "reads on movement, does nothing on a static shot), "
                    "'stabilize' (smooths modest HANDHELD wobble via deshake; "
                    "mirrored edges may soften and it cannot fix a whip, a "
                    "walk or rolling shutter; say that rather than promising "
                    "stabilization). Stabilize is whole-video only. For other "
                    "kinds start/end are PROGRAM seconds — omit "
                    "both for the whole video. intensity 0.05-1.0 (default "
                    "0.5). Content-anchored: a stylized moment follows its "
                    "footage through later cuts. One or two layered "
                    "effects read as a look; five read as a broken TV.",
                    {"kind": {"type": "string",
                              "enum": list(STYLIZE_KINDS)},
                     "start": {"type": "number"},
                     "end": {"type": "number"},
                     "intensity": {"type": "number"}}),
    "enhance_video": (enhance_video, "PICTURE QUALITY, not a look — the right "
                      "answer to 'make it clearer / sharper / better quality "
                      "/ HD / enhance this'. sharpen 0-1 (default 0.5) "
                      "recovers detail the camera's encoder smeared; denoise "
                      "0-1 (default 0) cleans grainy low-light footage and "
                      "should be raised BEFORE sharpening noisy video. "
                      "start/end are PROGRAM seconds; omit both for the whole "
                      "video. It cannot add resolution — say that plainly "
                      "instead of promising HD from a small source. Never "
                      "answer a clarity request with a colour grade.",
                      {"sharpen": {"type": "number"},
                       "denoise": {"type": "number"},
                       "start": {"type": "number"},
                       "end": {"type": "number"}}),
    "remove_stylize": (remove_stylize, "Remove one stylize effect by its id "
                       "(see get_edl).", {"id": {"type": "string"}}),
    "add_custom_filter": (add_custom_filter, "WRITE YOUR OWN ffmpeg video "
                          "filter chain and apply it to the program picture "
                          "— for the look no preset makes. Presets FIRST: "
                          "apply_look / add_stylize / set_color_grade / "
                          "enhance_video cover the common asks; reach here "
                          "when the user wants something none of them says "
                          "(a CRT phosphor look, posterize, a slow hue "
                          "drift, selective channel work). Rules: ONE chain "
                          "on the single video stream — filters separated "
                          "by commas, NO ';' or '[labels]', no file access "
                          "— and it must keep the frame's size and rate. "
                          "The chain is DRY-RUN on the real footage before "
                          "it stores: a broken chain returns ffmpeg's own "
                          "error (fix it, never retry the identical "
                          "string); an over-heavy one returns its measured "
                          "cost. start/end are PROGRAM seconds (omit both "
                          "= whole video; windowed moments follow their "
                          "footage through later cuts). label = short "
                          "human name for the look ('CRT green') shown in "
                          "diffs. Example chain: \"hue=s=0.3,"
                          "curves=green='0/0 0.5/0.6 1/1',"
                          "noise=alls=8:allf=t\". After it lands, LOOK at "
                          "the window on the next preview — a chain that "
                          "parses can still look wrong.",
                          {"chain": {"type": "string"},
                           "start": {"type": "number"},
                           "end": {"type": "number"},
                           "label": {"type": "string"}}),
    "remove_custom_filter": (remove_custom_filter, "Remove one custom "
                             "filter chain by its id (see get_edl).",
                             {"id": {"type": "string"}}),
    "set_grade_custom": (set_grade_custom, "Continuous color controls on "
                         "all footage, applied AFTER the preset grade (the "
                         "two compose — 'cinematic but warmer' = preset "
                         "cinematic + temperature 0.2): exposure -1..1, "
                         "contrast 0.5..1.6 (1.0 neutral), saturation 0..2 "
                         "(1.0 neutral), temperature -1 (cool)..1 (warm), "
                         "tint -1 (green)..1 (magenta), shadows -1..1 "
                         "(positive LIFTS the dark regions — the answer to "
                         "'brighten the shadows / too dark in the corners'), "
                         "highlights -1..1 (negative RECOVERS bright areas). "
                         "'More light' = exposure up; 'remove/soften the "
                         "shadows' = shadows up. Pass ONLY the axes "
                         "to change; an axis's neutral value clears it; "
                         "all axes neutral clears the whole custom grade. "
                         "Captions and graphics are never graded.",
                         {"exposure": {"type": "number"},
                          "contrast": {"type": "number"},
                          "saturation": {"type": "number"},
                          "temperature": {"type": "number"},
                          "tint": {"type": "number"},
                          "shadows": {"type": "number"},
                          "highlights": {"type": "number"}}),
    "separate_music": (separate_music, "Rebalance the ORIGINAL footage's "
                       "music vs its speech — the answer to 'remove the "
                       "background music but keep the talking', 'the song "
                       "is too loud under his voice', 'keep only the "
                       "music'. The soundtrack is separated into "
                       "speech/vocals + everything-else (once per video, "
                       "then cached) and each side plays at its own gain: "
                       "music_gain_db=-60 mutes the music, "
                       "voice_gain_db=-60 mutes the speech, -12 ducks, 0 "
                       "leaves untouched (range -60..+6). This changes "
                       "ONLY the original footage's own audio — added "
                       "music tracks are add_music/remove_music, NOT this. "
                       "Not surgical on dense mixes — check the preview "
                       "before promising total silence.",
                       {"music_gain_db": {"type": "number"},
                        "voice_gain_db": {"type": "number"}}),
    "remove_stem_mix": (remove_stem_mix, "Restore the original mixed "
                        "soundtrack (undo separate_music).", {}),
    "set_master_loudness": (set_master_loudness, "enabled=true normalizes "
                            "the FINAL MIX to -14 LUFS with a codec-safe "
                            "-2 dBTP target plus a latency-compensated hard "
                            "ceiling on preview "
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
                           "punch_in_on_emphasis, or "
                           "to answer 'what's the tempo'. Pass asset_key "
                           "(an uploaded or fetched music file) to "
                           "analyze that instead — e.g. to find the drop "
                           "for add_music offset_s.",
                           {"asset_key": {"type": "string"}}),
    "audit_audio_mix": (audit_audio_mix, "Deterministic audit of the CURRENT "
                        "EDL's authored music, voiceover and SFX roles, files, "
                        "program windows, source offsets, gains, ducking and "
                        "mastering. Detects the same asset playing twice or a "
                        "likely song misfiled as voiceover. This state is "
                        "ground truth; deterministic preview AUDIO CHECK can "
                        "measure the rendered mix without relabeling roles.",
                        {}),
    "punch_in_on_emphasis": (punch_in_on_emphasis, "ONE-CALL emphasis "
                             "zooms: writes punch zooms on the N most "
                             "vocally STRESSED words that survive the "
                             "current cut (stress measured from the audio, "
                             "times from the real word timestamps — never "
                             "guessed), in one EDL "
                             "version. count is any positive integer (default 3); strength "
                             "0.05-4.5 (default 0.14 — a gentle push the "
                             "viewer feels rather than sees). Face targets "
                             "are used when detected; otherwise center "
                             "fallbacks commit with advisories. The result lists "
                             "each word + program time — report those to "
                             "the user. THE tool for 'add zooms on the "
                             "important moments'.",
                             {"count": {"type": "integer"},
                              "strength": {"type": "number"}}),
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
                   "(clean white size-led captions, ungraded, gentle fades), "
                   "'cinematic' (elegant captions, cinematic grade + "
                   "slight warmth, 1s fades, dip_black), 'luxury' (luxe "
                   "captions, warm grade + temperature lift, long fades), "
                   "'meme' (impact xl captions, flash cuts, grain). "
                   "Preserves existing emphasis_words, else picks them "
                   "from the transcript. Never touches cuts, music or sfx "
                   "— place accents with add_sfx. "
                   "Every component can be adjusted afterwards "
                   "with its own tool.",
                   {"name": {"type": "string",
                             "enum": sorted(LOOKS)}}),
    "get_edl": (get_edl, "Current EDL JSON and version. Large timelines "
                "return a compact index instead of invalid truncated JSON. "
                "Request top-level sections such as ['captions','overlays'] "
                "and paginate list sections with offset/limit. Natural "
                "aliases are accepted without a retry: cuts/segments -> "
                "keep, text -> texts, zooms/transitions/grades/fades -> "
                "effects, audio -> all audio sections, and program/overview/"
                "summary -> the compact program overview. compact=true "
                "always returns counts, caption state and duplicate assets.",
                {"sections": {"type": ["array", "string"],
                              "items": {"type": "string"}},
                 "compact": {"type": "boolean"},
                 "offset": {"type": "integer"},
                 "limit": {"type": "integer"}}),
    "audit_captions": (audit_captions, "Mechanically compile and audit the "
                       "CURRENT caption track using the exact ASS artifact "
                       "ffmpeg burns. Reports first-caption lateness, missing "
                       "spoken-word coverage, true distinct-state overlaps, "
                       "exact event pages and up to 16 high-information "
                       "output times for rendered pixel QA. Call after adding "
                       "or restyling captions and after render_preview; this "
                       "is stronger timing evidence than a visual critic.",
                       {"offset": {"type": "integer"},
                        "limit": {"type": "integer"}}),
    "render_preview": (render_preview, "Verify the current EDL efficiently. "
                       "During iteration (default complete=false), render "
                       "only the output seconds affected since the last "
                       "complete preview and inspect their proof frames; the "
                       "short proof reel never replaces the Studio player. "
                       "When the entire edit is ready, call ONCE with "
                       "complete=true to render and attach the complete 480p "
                       "preview. Valmera's in-house agent automatically does "
                       "that complete render at turn end. When only COLOR changed "
                       "since the last render, this returns a ~2s grade "
                       "contact strip instead of re-encoding the program — "
                       "iterate the look against the strip; the complete "
                       "readiness render still happens exactly once.",
                       {"complete": {"type": "boolean",
                                     "description": "False/default: changed sections only. True: one complete readiness preview."}}),
    "ask_user": (ask_user, "Ask the user a specific question and wait for "
                 "their reply (ends this turn). Use whenever a material "
                 "choice genuinely belongs to the user.",
                 {"question": {"type": "string"}}),
    "make_shorts": (make_shorts, "Cut this LONG video into multiple "
                    "finished vertical shorts — a background run that picks "
                    "the strongest self-contained moments from the "
                    "transcript, builds each as its own project (9:16, "
                    "captions, punch-ins), and renders them onto the "
                    "project's Shorts board. THE tool for 'make me shorts/"
                    "clips/reels from this'. It returns the background "
                    "planner job ID; MCP callers can poll it with "
                    "wait_for_job or shorts_status. count caps how many; "
                    "style_note forwards the user's styling words to the "
                    "planner.",
                    {"count": {"type": "integer"},
                     "style_note": {"type": "string"}}),
    "edit_shorts": (edit_shorts, "DELEGATION ONLY — this does NOT directly "
                    "edit any EDL. It forwards ONE instruction to Valmera's "
                    "separate in-house agent in each selected child's chat; "
                    "each agent then runs its own billed edit turn. An MCP "
                    "caller whose user asked that caller to edit personally "
                    "must instead open_short/open_project each child and use "
                    "the normal editing tools directly. Use this only when "
                    "agent delegation is intended. NEVER apply a request "
                    "about the generated shorts to the long parent timeline. "
                    "Works from the shorts "
                    "PARENT or from INSIDE any generated short — it "
                    "resolves the board through the parent automatically, "
                    "so never ask anyone to switch projects first. "
                    "Write the instruction as the user would type it "
                    "('add <track> as a ducked music bed', 'make the "
                    "captions one word at a time'); board-parent and "
                    "current-project music/clip/image assets are shared "
                    "into the shorts automatically so the instruction can "
                    "name them. shorts: 'all' (default) or a list of board "
                    "card numbers (1-based).",
                    {"instruction": {"type": "string"},
                     "shorts": {"type": "array",
                                "items": {"type": "integer"}}}),
}

REQUIRED_ARGS = {
    "search_transcript": ["query"],
    # times OR start/end — validated in the tool, so neither is "required".
    "look_at": [],
    "look_at_asset": ["asset_key"],
    "keep_segments": ["segments"],
    "cut_range": ["start", "end"],
    "restore_range": ["start", "end"],
    "set_caption_style": [],
    # start/end default to the whole program, so "add some music" needs only
    # a track.
    "add_music": ["storage_key"],
    "search_music": ["query"],
    "find_song": ["query"],
    "fetch_music": ["id"],
    "set_edit_plan": ["steps"],
    "apply_edit_recipe": ["operations"],
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
    "add_zoom_path": ["keyframes"],
    "remove_zoom_path": ["id"],
    "enhance_cursor": [],
    "remove_cursor_enhance": [],
    "set_screen_frame": [],
    "remove_screen_frame": [],
    "add_aspect_shift": ["at_output_s", "ratio"],
    "remove_aspect_shift": ["id"],
    "search_stock": ["query"],
    "add_stock_media": ["id"],
    "insert_media": ["asset_key", "at_output_s"],
    "set_insert_window": ["id"],
    "move_insert": ["id"],
    "remove_insert": ["id"],
    "set_color_grade": ["preset"],
    "add_zoom": ["start", "end"],
    "remove_zoom": ["id"],
    "set_transitions": ["style"],
    "blur_region": ["x", "y", "w", "h"],
    "erase_region": [],
    "add_voiceover": ["asset_key"],
    "remove_voiceover": ["id"],
    "set_speed": ["start", "end", "factor"],
    "remove_speed": ["id"],
    "add_overlay": ["asset_key", "start"],
    "move_overlay": ["id"],
    "remove_overlay": ["id"],
    "add_screen_takeover": ["asset_key", "at_output_s"],
    "remove_screen_takeover": ["id"],
    "add_text": ["text", "start", "end"],
    "add_kinetic_text": [],
    "add_text_behind": ["text", "at_output_s"],
    "remove_text": ["id"],
    "add_title_card": ["text", "at_output_s"],
    "add_color_screen": ["at_output_s"],
    "add_corrupt_screen": ["at_output_s"],
    "set_caption_mutes": ["spans"],
    "add_stylize": ["kind"],
    "add_custom_filter": ["chain"],
    "remove_custom_filter": ["id"],
    "add_freeze_frame": ["at_output_s"],
    "set_caption_fixes": [],
    "enhance_video": [],
    "remove_stylize": ["id"],
    "set_grade_custom": [],
    "set_master_loudness": ["enabled"],
    "get_audio_analysis": [],
    "punch_in_on_emphasis": [],
    "search_sfx": ["query"],
    "find_footage": ["query"],
    "fetch_sfx": ["id"],
    "add_web_sfx": ["query", "at"],
    "beat_align_cuts": [],
    "suggest_emphasis": [],
    "apply_look": ["name"],
    "generate_image": ["prompt"],
    "generate_video": ["prompt"],
    "fetch_url": ["url"],
    "ask_user": ["question"],
    "read_skill": ["name"],
    "make_shorts": [],
    "edit_shorts": ["instruction"],
}

# The loop uses this to build TURN FACTS: a write "succeeded" when its result
# is a version diff line (write_edl's "EDL vX -> vY: ..." format).
# generate_image and fetch_url are here for the capabilities digest; their
# successes are tracked separately via ctx.images_generated / ctx.urls_fetched
# (neither writes the EDL — they create an ASSET the agent then places).
WRITE_TOOLS = {"apply_edit_recipe",
               "keep_segments", "cut_range", "cut_output_range",
               "restore_range",
               "cut_silences", "remove_filler_words", "add_captions",
               "add_kinetic_text",
               "set_caption_style", "add_music", "remove_music",
               "swap_music", "set_music_fit", "extract_audio",
               "add_sfx", "add_web_sfx", "move_sfx", "remove_sfx",
               "set_audio_gain", "set_volume", "set_frame", "auto_reframe",
               "record_website", "record_website_demo", "showcase_demo",
               "add_stock_media",
               "insert_media", "set_insert_window", "remove_insert",
               "add_voiceover",
               "remove_voiceover", "set_color_grade", "add_zoom",
               "remove_zoom", "add_zoom_path", "remove_zoom_path",
               "enhance_cursor", "remove_cursor_enhance",
               "set_screen_frame", "remove_screen_frame",
               "add_aspect_shift", "remove_aspect_shift",
               "set_fades", "set_transitions",
               "blur_region", "remove_blur",
               "erase_burned_text", "erase_region", "remove_erase",
               "reset_edit",
               "set_speed", "remove_speed",
               "add_overlay", "move_overlay", "remove_overlay",
               "add_screen_takeover", "remove_screen_takeover",
               "add_text", "add_text_behind", "remove_text",
               "add_title_card", "add_color_screen", "add_corrupt_screen",
               "set_caption_mutes",
               "add_stylize", "add_freeze_frame", "enhance_video",
               "set_caption_fixes",
               "remove_stylize",
               "set_grade_custom", "set_master_loudness",
               "separate_music", "remove_stem_mix",
               "punch_in_on_emphasis", "fetch_sfx",
               "beat_align_cuts", "apply_look",
               "generate_image",
               "generate_video", "fetch_url"}


def _tool_disabled(name, model=None):
    """Tools whose backing service is not configured are hidden entirely —
    the model must never see (or advertise) a capability that would only
    return 'unavailable'."""
    if name == "generate_image":
        return not llm.image_available()
    if name == "generate_video":
        return not videogen.video_gen_available()
    if name == "fetch_url":
        return not config.URL_FETCH_ENABLED
    if name in ("record_website", "record_website_demo"):
        return not webrecord.available()
    if name in ("search_stock", "add_stock_media"):
        return not stock.available()
    # Stem separation exists only where the demucs image layer does; a
    # definite "no" from the render service hides the tool entirely
    # (round-53 honest-off), while "unreachable" keeps it visible — unknown
    # is not "no", and the call itself refuses gracefully.
    if name in ("separate_music", "remove_stem_mix"):
        return _stems_supported() is False
    # Live music search (round 98) — the bundled library's replacement.
    if name in ("search_music", "fetch_music"):
        return not music_search.available()
    # Named-song link finding rides the fetch/extractor path; per-ACCOUNT
    # narrowing happens inside the tool (schemas are per-deployment).
    if name == "find_song":
        return not song_find.available()
    if name == "find_footage":
        return not song_find.footage_available()
    if name in ("search_sfx", "fetch_sfx", "add_web_sfx"):
        return not sfx_search.available()
    return False


def capabilities_digest():
    """One line per WRITE tool from the live registry. NOT sent to the model
    since round 71f (it duplicated the tool schemas at ~13k chars per call)
    — it survives as the honest-off probe the test suite pins: a tool whose
    backing service is unconfigured must be absent here exactly as it is
    absent from the schemas."""
    lines = []
    for name, (_fn, desc, props) in TOOLS.items():
        if name not in WRITE_TOOLS or _tool_disabled(name):
            continue
        params = ", ".join(props.keys())
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {name}({params}): {first}.")
    return "\n".join(lines)


def capability_names():
    """Just the deployed write-tool names (round 71f). The CAPABILITIES
    message shrank to this: every name here arrives with its FULL contract
    in the tool schemas the same request carries — both for the in-house
    agent and for MCP, whose catalog() ships openai_tools() alongside — so
    the old first-sentence-per-tool digest was ~13k chars of pure
    duplication in every call of every turn."""
    return [n for n in TOOLS if n in WRITE_TOOLS and not _tool_disabled(n)]


def _compact_description(description):
    """A post-plan reminder, not a second copy of the full handbook."""
    text = " ".join(str(description or "").split())
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    if len(first) <= 240:
        return first
    return first[:237].rsplit(" ", 1)[0] + "..."


def openai_tools(model=None, compact=False):
    """`model` is the agent model this schema is for, so per-model honest-off
    (a provider that has refused audio parts) can hide a tool the same way an
    unconfigured service does. Omitted by MCP and the tests, where the
    deployment-wide answer is the right one."""
    out = []
    edits = llm.image_edit_available()
    # When THIS model reads frames itself (direct sight), look_at's `question`
    # is dead weight: it is only echoed back into the model's own next turn —
    # it answers by LOOKING, not by having phrased a question. The argument is
    # the vision PROMPT only on the blind fallback path (an external vision
    # provider), so it stays in the schema exactly when the model cannot see.
    # Measured over 10 days: filled on 553/554 look_at calls at ~175 chars
    # each — pure narration the model paid to write and then re-read.
    sees = model is not None and llm.agent_sees(model)
    for name, (_fn, desc, props) in TOOLS.items():
        if _tool_disabled(name, model):
            continue
        if sees and name in ("look_at", "look_at_asset"):
            props = {k: v for k, v in props.items() if k != "question"}
        # HONEST-OFF AT THE ARGUMENT LEVEL (round 101). generate_image was
        # the fourth most-rejected tool — 61 calls in a week asking it to
        # restyle a frame or an upload against an image model that can only
        # text-to-image. The whole capability lives in two arguments, so the
        # honest schema drops those two arguments and the sentences that
        # advertise them; a tool the agent cannot misread costs no steps to
        # be told no.
        if name == "generate_image" and not edits:
            props = {k: v for k, v in props.items()
                     if k not in ("from_video_time_s", "from_asset_key")}
            desc = ("Create an image with AI from a TEXT PROMPT. This image "
                    "model cannot restyle or edit an existing frame or "
                    "upload — it only makes new images from a description, "
                    "so describe the whole picture you want. The result is "
                    "saved as a project image asset; it appears in the video "
                    "ONLY after you insert_media its storage_key (typically "
                    "2-4s with a Ken Burns motion). It lands as a full-frame "
                    "STILL moment — it does not modify or track the moving "
                    "footage. aspect defaults to the output frame / source "
                    "ratio.")
        if compact:
            desc = _compact_description(desc)
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


def _count_tool_outcome(ctx, counter):
    """Reliability bookkeeping: how often tools refuse or fail, incremented
    at the dispatch chokepoint so the in-house loop and MCP callers are both
    counted (admin reads it from metrics_counters). Its own transaction, its
    own try — bookkeeping must never touch the turn."""
    try:
        ctx.db.run(dbx.bump_metric, counter)
    except Exception:
        pass


def tool_result_kind(result):
    """Stable outer-call classification for billing/outcome telemetry."""
    if not isinstance(result, str):
        return "success"
    text = result.strip()
    first = text.splitlines()[0] if text else ""
    low = first.lower()
    if text.startswith(("REJECTED", "RECIPE ABORTED", "Unknown tool")):
        return "refused"
    # Tool failures are not written in one historical dialect: some begin
    # "Could not…", while others say "the image was generated but could not
    # be saved" or "audio analysis unavailable". Classify the first line by
    # meaning so those turns neither charge nor enter another blind retry.
    if first.startswith(("Tool ", "FAILED")) or re.search(
            r"\b(failed|could not|unavailable|errored)\b", low):
        return "failed"
    if text.startswith("NO CHANGE"):
        return "no_change"
    return "success"


def execute(ctx, name, args):
    """Dispatch one tool call. Returns a string for the model (AskUser
    propagates)."""
    entry = TOOLS.get(name)
    if not entry:
        _count_tool_outcome(ctx, "tool_refused")
        return (f"Unknown tool '{name}'. Available: "
                + ", ".join(TOOLS))
    name, args, _repairs = _normalize_tool_call(name, args)
    # Normalization may route set_frame(mode=auto) to auto_reframe, so resolve
    # the registry entry after dialect repair rather than before it.
    entry = TOOLS.get(name)
    if not entry:
        _count_tool_outcome(ctx, "tool_refused")
        return (f"Unknown tool '{name}'. Available: "
                + ", ".join(TOOLS))
    fn = entry[0]
    try:
        out = fn(ctx, **args)
    except AskUser:
        raise
    except TypeError as e:
        _count_tool_outcome(ctx, "tool_refused")
        return (f"REJECTED: bad arguments for {name}: {e}. "
                "Check the tool's parameter names.")
    except Exception as e:
        _count_tool_outcome(ctx, "tool_failed")
        return f"Tool {name} errored: {str(e)[:300]}. Try a different approach."
    kind = tool_result_kind(out)
    if kind == "refused":
        _count_tool_outcome(ctx, "tool_refused")
    elif kind == "failed":
        _count_tool_outcome(ctx, "tool_failed")
    return out
