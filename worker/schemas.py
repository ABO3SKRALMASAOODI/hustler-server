"""Typed EDL + index schemas.

All timestamps everywhere are SECONDS as floats. The EDL is pure data — the
agent edits it through validated tools, the renderer turns it into ffmpeg
filtergraphs. A TypeScript mirror of the EDL type lives in the frontend repo
at src/types/edl.ts — keep the two in sync.
"""

import hashlib
import json
import re
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

# SINGLE source of truth for the index pipeline version — bump it HERE, by
# commit, whenever index OUTPUT changes (transcriber switch, segmentation
# rules). The backend loads this module (see backend/routes/video.py) and the
# worker's config re-exports it, so the two services can never disagree. It
# used to be an env var set separately on each service: the two drifted for a
# full day (Jul 16-17 2026) and every project open triggered a 30-90 min
# re-index that STILL wrote the old version — an infinite loop that starved
# two real customers' jobs. Constants deploy atomically; env vars don't.
# v8: transcription changed output (round 67) — brand keyterm biasing removed
# from customer ASR (it hallucinated "Valmera." as a real customer's entire
# transcript) and a sparse-result fallback re-runs whisper when Deepgram
# returns (near-)zero words for real-length audio (Arabic → "Portuguese",
# 0 words). Existing zero-word indexes MUST rebuild or those users stay
# uncaptionable forever.
# v9: clock-sampled moments + diarized transcription (round 69).
# v10: THE VISUAL INDEX IS PICTURES, NOT PROSE. The vision-captioning stage is
# gone; in its place every video (main footage AND uploaded clips) gets a
# filmstrip of labeled tiles — 2x2 grids of frames sampled ~1s apart (see
# worker/tiles.py) whose storage keys live in `tile_keys`. The agent reads the
# tiles with its own eyes each turn instead of reading second-hand captions.
# `moments`/`shots[].caption` remain readable on old rows but are no longer
# produced. Existing indexes must rebuild to gain the strip.
PIPELINE_VERSION = 10

MIN_SPAN_S = 0.05
GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 12.0
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Widened 12 -> 16 (round 35) and enforcement changed from reject to clamp:
# an out-of-range grouping is a taste choice to trim, not an impossible state.
MAX_WORDS_PER_CAPTION = 16
# Continuous caption-size fine-tune multiplier bounds (see CaptionStyle).
CAPTION_SIZE_SCALE_MIN = 0.5
CAPTION_SIZE_SCALE_MAX = 3.0


class EDLValidationError(ValueError):
    """Raised with a short, instructive, model-readable message."""


def _r(t):
    return round(float(t), 2)


# ------------------------------------------------------------------ #
#  EDL v2 — the universal keyframe primitive                           #
# ------------------------------------------------------------------ #
# Any AnimFloat field accepts either a plain number (constant — exactly what
# every EDL ever written stores, so signatures are untouched) or a list of
# keyframes. `t` is seconds from the ELEMENT's own start; `ease` describes
# the curve INTO this keyframe from the previous one. The renderer compiles
# keyframes to ffmpeg expressions; tools clamp values through _norm_anim.

EASINGS = ("linear", "in", "out", "in_out", "hold")


class Keyframe(BaseModel):
    t: float
    v: float
    # None = linear (kept None so a linear keyframe adds no signature noise)
    ease: Optional[Literal["linear", "in", "out", "in_out", "hold"]] = None


# NOTE: float FIRST — pydantic must prefer the scalar branch for numbers.
AnimFloat = Union[float, List[Keyframe]]


def is_animated(v):
    return isinstance(v, list)


def anim_value(v, t):
    """Evaluate an AnimFloat at element-local time t (python-side mirror of
    the renderer's expression compiler — used by tools and tests)."""
    if not isinstance(v, list):
        return float(v)
    kfs = [(k["t"], k["v"], k.get("ease")) if isinstance(k, dict)
           else (k.t, k.v, k.ease) for k in v]
    if not kfs:
        return 0.0
    if t <= kfs[0][0]:
        return float(kfs[0][1])
    for i in range(1, len(kfs)):
        t0, v0, _ = kfs[i - 1]
        t1, v1, ease = kfs[i]
        if t <= t1:
            if t1 - t0 <= 1e-9 or ease == "hold":
                return float(v0) if t < t1 else float(v1)
            p = (t - t0) / (t1 - t0)
            if ease == "in":
                p = p * p
            elif ease == "out":
                p = p * (2 - p)
            elif ease == "in_out":
                p = p * p * (3 - 2 * p)
            return float(v0 + (v1 - v0) * p)
    return float(kfs[-1][1])


def anim_bounds(v):
    """(min, max) an AnimFloat can reach — for range validation."""
    if not isinstance(v, list):
        return float(v), float(v)
    vals = [(k["v"] if isinstance(k, dict) else k.v) for k in v] or [0.0]
    return float(min(vals)), float(max(vals))


def _norm_anim(v, name, lo, hi, max_t=None, max_kfs=24):
    """Validate + clamp an AnimFloat in place. Constants clamp to [lo, hi];
    keyframe times must be sorted, non-negative and within max_t; values
    clamp. Returns the normalized value (a float, or a list of Keyframe)."""
    if not isinstance(v, list):
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise EDLValidationError(f"{name} must be a number or keyframes.")
        return round(min(max(f, lo), hi), 4)
    if not v:
        raise EDLValidationError(f"{name}: keyframe list is empty.")
    if len(v) > max_kfs:
        raise EDLValidationError(
            f"{name}: {len(v)} keyframes — at most {max_kfs}.")
    kfs = []
    last_t = -1e9
    for i, k in enumerate(v):
        k = k if isinstance(k, Keyframe) else Keyframe.model_validate(k)
        k.t = round(float(k.t), 3)
        if k.t < 0:
            raise EDLValidationError(f"{name}[{i}].t is negative.")
        if max_t is not None and k.t > max_t + 0.01:
            raise EDLValidationError(
                f"{name}[{i}].t {k.t} exceeds the element's own length "
                f"({round(max_t, 2)}s — keyframe times are LOCAL).")
        if k.t <= last_t + 1e-9:
            raise EDLValidationError(
                f"{name}: keyframe times must be strictly increasing.")
        last_t = k.t
        k.v = round(min(max(float(k.v), lo), hi), 4)
        if k.ease == "linear":
            k.ease = None       # canonical: default drops from signatures
        kfs.append(k)
    if len(kfs) == 1:
        return kfs[0].v         # one keyframe is a constant
    return kfs


def clip_anim(v, new_dur):
    """Trim an AnimFloat to a shortened element duration.

    Every site that shrinks an element's duration_s must run its keyframed
    properties through this — _norm_anim hard-rejects keyframes past the
    element's length, so an untrimmed curve makes validate_edl reject the
    WHOLE later write (a keep cut, an insert removal) over a keyframe the
    user never mentioned. Returns v unchanged when nothing exceeds new_dur
    (signature-stable for untouched items). Otherwise: keyframes at
    t <= new_dur survive, the curve's value AT new_dur is appended as the
    final keyframe (sampled with the incoming keyframe's ease, so the
    truncated ramp bends exactly like the original up to the cut), and a
    single surviving point collapses to a constant."""
    if not isinstance(v, list) or not v:
        return v
    def _t(k):
        return float((k.get("t") if isinstance(k, dict) else k.t) or 0.0)
    if all(_t(k) <= new_dur + 0.01 for k in v):
        return v
    kept = [dict(k) if isinstance(k, dict) else
            {"t": k.t, "v": k.v, "ease": k.ease}
            for k in v if _t(k) <= new_dur + 1e-9]
    incoming = next((k for k in v if _t(k) > new_dur + 1e-9), None)
    kf = {"t": round(max(0.0, new_dur), 3),
          "v": round(float(anim_value(v, new_dur)), 4)}
    ease = (incoming.get("ease") if isinstance(incoming, dict)
            else incoming.ease) if incoming is not None else None
    if ease:
        kf["ease"] = ease
    kept.append(kf)
    if len(kept) < 2:
        return kf["v"]
    return kept


# ------------------------------------------------------------------ #
#  EDL                                                                 #
# ------------------------------------------------------------------ #

class CaptionStyle(BaseModel):
    """Burn style. color is #RRGGBB; the renderer converts it to the .ass
    &HBBGGRR order. Defaults match the pre-style captions exactly, so EDLs
    written before styling existed render unchanged."""
    color: str = "#FFFFFF"
    size: Literal["s", "m", "l", "xl"] = "m"
    # Continuous fine-tune multiplier on top of the `size` bucket (0.5-3.0).
    # Magnitudes belong on a continuous scale, not a 4-value enum — this is the
    # knob for "a little bigger" / "way bigger" without jumping buckets. The
    # `size` enum stays as the coarse curated menu (and as an alias so old
    # EDLs keep working). Optional so pre-round-13 EDLs keep their signatures.
    size_scale: Optional[float] = None
    # None (not 'bottom') so premium presets can apply their own default
    # placement when the agent didn't choose one; None renders as bottom on
    # the legacy path. Old EDLs stored an explicit 'bottom' and are untouched.
    position: Optional[Literal["bottom", "top", "middle"]] = None
    # Premium caption look (worker/captions.py PRESETS): podcast (reveal
    # stack with keyword emphasis), beast (loud Anton karaoke), karaoke
    # (box follows the spoken word), elegant (serif-accented lower third).
    # 'classic' = the legacy look explicitly. None = legacy (signature-safe).
    preset: Optional[Literal[
        # original four (single-Dialogue "flow" emission)
        "podcast", "beast", "karaoke", "elegant",
        # round 67: one word at a time, centred, glowing (the modern
        # single-word look — the only preset that defaults position middle)
        "spotlight",
        # composed looks (per-line "stack" emission): scale-led hierarchy,
        # tight/overlapping leading, layered text effects
        "stacked", "iridescent", "chrome", "editorial", "fashion", "luxe",
        "impact",
        # round 99b: the mixed-face lyric edit — lowercase Poppins phrases
        # centred mid-frame, stressed word huge in white italic serif
        "lyric",
        "classic"]] = None
    # Force upper/lower case in premium presets; None = the preset's default.
    uppercase: Optional[bool] = None
    # karaoke word-by-word captions; Optional so pre-round-7 EDLs keep their
    # signatures (None-valued keys are stripped by edl_signature).
    dynamic: Optional[bool] = None
    # color of the actively-spoken word in dynamic mode; Optional for the
    # same signature reason. None renders the default highlight.
    highlight_color: Optional[str] = None
    # entrance animation for STATIC captions (fade/pop/slide_up); dynamic
    # karaoke captions animate word-by-word already, so animation is ignored
    # there. Optional so pre-round-9 EDLs keep their signatures.
    # "none" (round 71) explicitly turns a preset's animation OFF — distinct
    # from None, which lets the preset's own animation apply.
    animation: Optional[Literal["none", "fade", "pop", "slide_up", "punch",
                                "blur_in", "whip", "flash", "rise",
                                "drop"]] = None

    # ── Composer fields (premium presets only) ───────────────────────────
    # Each MUST also appear in captions.STYLE_KEYS and in agent_tools'
    # _parse_partial_style allowlist. A field declared in only some of those
    # places is dropped silently: pydantic ignores undeclared fields, so the
    # EDL signature never changes, write_edl reports "NO CHANGE", no render
    # runs — and the agent tells the user the new look was applied.
    # Explicit font family. Must be one of the families bundled in
    # worker/fonts (their INTERNAL name — Google ships heavy weights as
    # separate families, so it is "Poppins Black", not "Poppins").
    font: Optional[Literal[
        "Inter Display Black", "Inter Display ExtraBold", "Inter Display Bold",
        "Anton", "Bebas Neue", "Archivo Black", "Poppins Black",
        "Syne ExtraBold", "Playfair Display Black", "Instrument Serif",
        "DM Serif Display", "Montserrat"]] = None
    # Layered text effect applied to emphasised words (or all words when the
    # preset sets it globally).
    effect: Optional[Literal["chroma", "chrome", "glow"]] = None
    # "stack" gives every line its own position (enables leading < 1, i.e.
    # deliberately overlapping lines, and per-line horizontal stagger).
    layout: Optional[Literal["stack", "flow"]] = None
    # Line spacing multiplier, 0.5-2.2. Below 1.0 consecutive lines OVERLAP.
    leading: Optional[float] = None
    # Which treatment emphasis words receive. "big" is size-only — the
    # reference look, where one white word is twice its white neighbours.
    emphasis: Optional[Literal["big", "huge", "accent", "pop", "box", "serif",
                               "script", "chrome", "glow", "chroma",
                               "none"]] = None
    # How much larger an emphasised word renders, 1.0-3.0.
    emphasis_scale: Optional[float] = None

    @field_validator("leading")
    @classmethod
    def _leading_range(cls, v):
        if v is None:
            return v
        if not (0.5 <= float(v) <= 2.2):
            raise ValueError(
                f"leading {v} must be between 0.5 and 2.2 (below 1.0 the "
                "lines deliberately overlap)")
        return float(v)

    @field_validator("emphasis_scale")
    @classmethod
    def _emph_scale_range(cls, v):
        if v is None:
            return v
        if not (1.0 <= float(v) <= 3.0):
            raise ValueError(
                f"emphasis_scale {v} must be between 1.0 and 3.0")
        return float(v)

    @field_validator("color")
    @classmethod
    def _color_hex(cls, v):
        v = (v or "").strip()
        if not HEX_COLOR.match(v):
            raise ValueError(
                f"color '{v}' must be #RRGGBB hex, e.g. #FF0000 for red")
        return v.upper()

    @field_validator("highlight_color")
    @classmethod
    def _hl_hex(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not HEX_COLOR.match(v):
            raise ValueError(
                f"highlight_color '{v}' must be #RRGGBB hex, e.g. #FFE14D")
        return v.upper()

    @field_validator("size_scale")
    @classmethod
    def _size_scale_range(cls, v):
        if v is None:
            return v
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError("size_scale must be a number between 0.5 and 3.0")
        # 1.0 is the neutral default — normalize it back to None so it never
        # shows up as a change in edl_signature (same convention as the other
        # optional fields whose no-op value collapses to None).
        if abs(v - 1.0) < 1e-6:
            return None
        return round(min(max(v, CAPTION_SIZE_SCALE_MIN),
                         CAPTION_SIZE_SCALE_MAX), 3)


def _coerce_style(v):
    # Legacy EDLs stored style as the string "default" — treat any string
    # as "use defaults" instead of failing to load old versions.
    if isinstance(v, str) or v == {}:
        return None
    return v


class CaptionItem(BaseModel):
    text: str
    start: float   # source-timeline seconds
    end: float
    style: Optional[CaptionStyle] = None   # per-item override

    _style = field_validator("style", mode="before")(_coerce_style)


class CaptionsFromTranscript(BaseModel):
    mode: Literal["from_transcript"] = "from_transcript"
    # Chunk word-timed captions into groups of at most N words. Timing always
    # comes from the real word timestamps in the index — never invented.
    max_words_per_caption: Optional[int] = None
    # Karaoke (legacy-dynamic) group size, BAKED at write time. The renderer
    # historically clamped dynamic grouping at 4 regardless of
    # max_words_per_caption; 3 stored prod EDLs (proj 13 v3-5, mw=6) rely on
    # that clamp, so the render-time interpretation of EXISTING fields can
    # never change. Raising the cap therefore rides a NEW field: tools write
    # the concrete group size here (<= 8), old EDLs leave it None and render
    # exactly as always. None never reaches the signature.
    karaoke_group_n: Optional[int] = None
    style: Optional[CaptionStyle] = None
    # Keywords the premium presets emphasize (accent color / highlight box /
    # serif italic) wherever they appear in the transcript. Chosen by the
    # agent from the REAL transcript; words containing digits are always
    # emphasized. Ignored without a preset. None/[] = no keyword emphasis.
    emphasis_words: Optional[List[str]] = None
    # Round 52 — spelling/capitalization corrections for the burned words,
    # [[from, to], ...]. TEXT ONLY: word timings are never touched, so a fix
    # cannot desync a karaoke preset. None (never []) so an EDL written before
    # this existed keeps its exact signature and its cached render.
    text_fixes: Optional[List[List[str]]] = None

    _style = field_validator("style", mode="before")(_coerce_style)

    @field_validator("text_fixes")
    @classmethod
    def _fixes_norm(cls, v):
        if not v:
            return None
        out = []
        for pair in v:
            try:
                src, dst = str(pair[0]).strip(), str(pair[1]).strip()
            except (IndexError, TypeError, ValueError):
                continue
            # Same-word-count pairs only: see captions.apply_text_fixes for
            # why a 2-into-1 replacement cannot be honoured.
            if src and dst and len(src.split()) == len(dst.split()):
                out.append([src[:80], dst[:80]])
        return out[:80] or None

    @field_validator("emphasis_words")
    @classmethod
    def _emph_norm(cls, v):
        if v is None:
            return None
        words = [str(w).strip() for w in v if str(w).strip()]
        # bounded so a runaway list can't bloat the EDL; [] collapses to
        # None so it never shows as a change in edl_signature.
        return words[:60] or None


class MusicItem(BaseModel):
    # id is optional so pre-round-6 EDLs (whose music items have none) stay
    # valid and signature-compatible; new items always get one.
    id: Optional[str] = None
    storage_key: str
    # Music is new content with no source-time meaning, so start/end are
    # positions in the OUTPUT (edited) timeline. Documented in the tool spec.
    start: float
    end: float
    gain_db: float = -18.0
    duck: bool = True
    # Round 25 — music FITTING. Every one of these defaults to None on
    # purpose: _sig_canon drops nested None keys, so an EDL written before
    # these fields existed hashes identically to a fresh dump that carries
    # them. A non-None default (e.g. loop: bool = True) would change the
    # signature of every music item ever written and re-render them all.
    offset_s: Optional[float] = None    # seek INTO the track (start on the drop)
    fade_in_s: Optional[float] = None   # the item's own fade, not the program's
    fade_out_s: Optional[float] = None
    loop: Optional[bool] = None         # opt-IN; None/False both mean "don't"
    # Round 35 — smooth speech ducking (sidechain compression: the music
    # dips WITH the voice and swells back in the gaps, instead of the legacy
    # -12dB step). Opt-in per item by add_music so every music item written
    # before this field renders exactly as it always did.
    duck_mode: Optional[Literal["smooth"]] = None
    # Round 79i — the piece stays on the timeline but does not sound: the
    # A/B verb (mute one alternative, hear the other) and half of "put one
    # split below the other". The renderer skips muted pieces before
    # fetching them; None (every earlier EDL) keeps signatures and renders.
    mute: Optional[bool] = None


class SfxItem(BaseModel):
    """A one-shot sound effect at a POINT in the output timeline.

    Deliberately not a MusicItem with a short span. Music is a bed: it has a
    duration, it loops, and it ducks under speech. An sfx is a transient — it
    plays for exactly as long as the file is, it must never duck (a whoosh
    that dips under the very word it is punctuating is not an accent), and it
    has no meaningful end the agent could set.

    id is REQUIRED, unlike MusicItem.id. That field is Optional only to keep
    pre-round-6 EDLs (whose music items predate ids) valid and
    signature-compatible; there is no legacy sfx EDL, so there is no reason to
    inherit the escape hatch.
    """
    id: str
    storage_key: str
    at: float        # position in the OUTPUT (edited) timeline
    # -6dB is the pack's house level: sounds are normalized to -16 LUFS, so
    # this sits an accent clearly above a -18dB music bed without fighting
    # speech. It must match add_sfx's default AND the renderer's fallback —
    # three layers, one number, or the EDL and the render disagree.
    gain_db: float = -6.0


class VolumeItem(BaseModel):
    start: float   # source-timeline seconds
    end: float
    gain_db: float


FRAME_RATIOS = ("source", "16:9", "9:16", "1:1", "4:5")


class FocusSpan(BaseModel):
    """One window of the SOURCE clock where the crop aims at (x, y).

    Round 100 — the wall bug. auto_reframe measured the subject as ONE median
    point for the whole video, and on a two-person podcast (speakers left and
    right of frame, shots alternating) the median lands BETWEEN them: every
    Aug 8 shorts child cropped to x≈0.58 — the wall. A crop that follows the
    footage has to be allowed to move at cuts, so the frame carries a track
    of source-time spans, each with its own aim. The renderer resolves each
    kept segment's crop from the span covering that segment's midpoint (the
    seeder splits keep segments at shot boundaries so a span change lands
    exactly on a cut, where a reframe reads as an edit instead of a slide)."""
    t0: float
    t1: float
    x: Optional[float] = None
    y: Optional[float] = None

    @field_validator("x", "y")
    @classmethod
    def _clamp_xy(cls, v):
        if v is None:
            return None
        return round(min(max(float(v), 0.0), 1.0), 3)


class Frame(BaseModel):
    """Output frame. ratio 'source' keeps the original dimensions; anything
    else is achieved by crop (center-crop + scale), pad (fit + black bars) or
    pad_blur (fit over a blurred scaled copy). Never upscales beyond the
    source's pixel budget — see renderer.frame_dims.

    focus_x/focus_y (round 36): where the SUBJECT sits in the source frame,
    as fractions 0-1 — the crop window is centered on that point instead of
    the frame middle, which is what makes a 16:9 -> 9:16 conversion follow an
    off-center speaker instead of chopping them in half. None = 0.5 (the
    legacy center crop) and is dropped from signatures, so every stored EDL
    renders byte-identically. Only meaningful for mode 'crop'.

    focus_track (round 100): per-source-window aims that OVERRIDE focus_x/y
    for kept segments whose midpoint falls inside a span — the moving-subject
    answer a single point cannot give (see FocusSpan). None/[] keeps the
    single-point behaviour byte-identically, and _sig_canon drops the None so
    every stored EDL's signature is unchanged."""
    ratio: Literal["source", "16:9", "9:16", "1:1", "4:5"] = "source"
    mode: Literal["crop", "pad", "pad_blur"] = "crop"
    focus_x: Optional[float] = None
    focus_y: Optional[float] = None
    focus_track: Optional[List[FocusSpan]] = None

    @field_validator("focus_x", "focus_y")
    @classmethod
    def _clamp_focus(cls, v):
        if v is None:
            return None
        return round(min(max(float(v), 0.0), 1.0), 3)


MAX_INSERT_DURATION_S = 600.0


class InsertItem(BaseModel):
    """A clip or image spliced into the program at a keep-segment boundary
    (insert_media splits a keep segment when asked to land mid-segment, so
    any program position is reachable). at_output_s is a position in the
    PRE-INSERT output timeline (the keep list alone), so items are stable
    when other inserts change. duration_s is always concrete: the tool
    resolves it (image default 3.0s, short clips their full length).
    source_start_s picks WHERE in the source clip the window starts;
    Optional so pre-round-8 EDLs keep their signatures.
    motion is a Ken Burns move for IMAGE inserts only (a still that slowly
    zooms or pans instead of sitting frozen); Optional for signatures.
    rate (round 76) plays the spliced clip FASTER (or slower) in place —
    "don't shorten the editing screens, speed them up". duration_s stays
    the OUTPUT length of the block; the clip consumes duration_s*rate of
    source from source_start_s, video via setpts, audio pitch-corrected via
    atempo. None (the default on every EDL written before round 76) renders
    byte-identically to 1.0, so old signatures and cached renders hold.
    crop (round 77) shows ONE REGION of the clip as the scene —
    [x0, y0, x1, y1] fractions of the source frame — letterboxed to the
    canvas (black bars) instead of fighting the surroundings with zoom. A
    16:9 window can NEVER hold a 2.6:1 UI strip (a full editing timeline)
    without also holding whatever sits above it; cropping the insert is the
    honest way to show "the whole timeline, nothing else, static". None
    renders byte-identically to the uncropped legacy chain."""
    id: str
    asset_key: str
    kind: Literal["video", "image"]
    at_output_s: float
    duration_s: float
    source_start_s: Optional[float] = None
    motion: Optional[Literal["zoom_in", "zoom_out",
                             "pan_left", "pan_right"]] = None
    rate: Optional[float] = None
    crop: Optional[List[float]] = None
    # mute (round 78): the spliced scene plays SILENT — its own audio is
    # dropped and the block renders over the shared anullsrc, exactly like a
    # clip that never had a track. There was NO way to silence an insert
    # before this ("mute all scenes" could only reach the main footage via
    # set_volume), which on an all-inserts program meant no way at all.
    # None (every pre-round-78 EDL) keeps signatures and renders identically.
    mute: Optional[bool] = None
    # fit (round 79): how THIS insert maps onto the canvas — overriding the
    # program-wide frame mode for one scene. The default cover-crop is right
    # for footage but beheads any still whose aspect fights the canvas (a
    # 9:16 logo card on a 16:9 program shows only its middle band — the user
    # called it "corrupted"). 'pad' shows the WHOLE picture on black bars,
    # 'pad_blur' on a blurred backdrop, 'crop' forces the cover-crop. None
    # (every pre-round-79 EDL) renders byte-identically to the legacy chain.
    fit: Optional[Literal["crop", "pad", "pad_blur"]] = None

    @field_validator("rate")
    @classmethod
    def _clamp_rate(cls, v):
        if v is None:
            return None
        return min(max(float(v), INSERT_RATE_MIN), INSERT_RATE_MAX)

    @field_validator("crop")
    @classmethod
    def _chk_crop(cls, v):
        if v is None:
            return None
        if not isinstance(v, (list, tuple)) or len(v) != 4:
            raise ValueError("crop must be [x0, y0, x1, y1] fractions of "
                             "the source frame")
        x0, y0, x1, y1 = (min(max(float(a), 0.0), 1.0) for a in v)
        if x1 - x0 < 0.1 or y1 - y0 < 0.1:
            raise ValueError("crop region must span at least 10% of the "
                             "frame on each axis")
        return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


class VoiceoverItem(BaseModel):
    """Audio laid over the program. start_output_s is a position in the
    FINAL program timeline (after inserts). duck_others lowers program audio
    12dB while the voiceover is active."""
    id: str
    asset_key: str
    start_output_s: float
    gain_db: float = 0.0
    duck_others: bool = True


GRADE_PRESETS = ("vibrant", "warm", "cool", "bw", "vintage", "cinematic")
# Round 76: how fast a spliced scene may play in place. atempo pitch-corrects
# cleanly in this band (two chained stages below 0.5); past 4x a screen
# recording reads as a glitch, below 0.25 as a freeze.
INSERT_RATE_MIN = 0.25
INSERT_RATE_MAX = 4.0
ZOOM_STRENGTH_MIN = 0.05
# Widened from 1.0 (round 35), 1.5 (round 75), 2.5 (round 76). The launch
# video's chat bubbles sit ~0.01 of the frame apart, so excluding the
# NEIGHBOURING message from a bubble close-up needs a ~0.19-tall viewport —
# strength ~4.2. That is a 745px window from 4K source: soft in a 4K export,
# fine at 1080p, and the subject at that magnification is a text bubble
# filling half the screen. The cap is the ceiling of what aimed shots may
# ask for — the audit's hard-punch rule still polices center zooms.
ZOOM_STRENGTH_MAX = 4.5
FADE_MAX_S = 10.0


# Round 45. A follow-zoom's path is stored as FRACTIONS OF ITS OWN WINDOW,
# not as absolute program times. That is not a style choice: zooms are
# content-anchored, so remap_program_items moves start/end whenever an
# unrelated cut shifts the footage underneath them. Absolute path times would
# be silently stranded by that move (or would need a second, parallel remap
# that could disagree with the first). Fractions ride along for free — a
# window that moves or is trimmed carries its whole path with it, exactly.
ZOOM_PATH_MAX_POINTS = 24


class ZoomPathPoint(BaseModel):
    """One waypoint of a traveling zoom: at `f` (0 = window start, 1 = window
    end) the zoom is centred on (cx, cy) in output-frame fractions.

    s (round 51) is the zoom STRENGTH at this waypoint, and it only exists for
    mode 'path' — a 'follow' zoom holds one strength for its whole window and
    ramps it at the edges. Optional and None-by-default on purpose: _sig_canon
    drops nested None keys, so every follow path written by showcase_demo since
    round 45 dumps exactly the keys it always did and keeps its cached render.
    """
    f: float
    cx: float
    cy: float
    s: Optional[float] = None


class ZoomItem(BaseModel):
    """A zoom over a FINAL-program time range (output seconds). mode:
    'punch' (default, instant step in/out), 'ease' (smoothly ramps in and
    out inside the window), 'push_in' / 'pull_out' (continuous Ken Burns
    drift across the whole window), 'follow' (round 45: ramps in like ease
    and GLIDES its centre along `path` while held). Optional so pre-round-9
    EDLs keep their signatures.

    cx/cy (round 35): the zoom TARGET as fractions of the output frame
    (0,0 = top-left). None = center, which is exactly what every earlier
    zoom rendered — so old EDLs keep both their signatures and their look.

    path (round 45): meaningful with mode 'follow' and mode 'path'. Two or
    more points in ascending `f`. This is what makes a screen-recording zoom
    watchable — a static punch onto one button has to cut out before the next
    one, while a traveling zoom stays in and moves, which is how a hand-made
    product demo reads.

    mode 'path' (round 51) is the KEYFRAMED traveling zoom: the strength moves
    between the waypoints too (each carries its own `s`), and no ramp is added
    at the window edges — the frame is exactly where and how close the
    keyframes say, at the times they say. 'follow' is the simpler shape
    showcase_demo writes: one strength, ramped in and out, centre travelling.
    Both render through worker/travel.py; there is one traveling zoom.

    ease (round 51) selects the curve BETWEEN waypoints for mode 'path':
    'cubic_in_out' (the default the tool writes) settles at each keyframe,
    'linear' holds a constant velocity through them. None on a 'follow' zoom
    means the round-45 linear interpolation, byte-for-byte.

    rect (round 72) is PROVENANCE, not render input: when add_zoom framed a
    region ([x0, y0, x1, y1] output-frame fractions), the solved viewport is
    already baked into cx/cy/strength — the renderer never reads rect. It is
    stored so get_edl shows WHAT the zoom was framing, which is what a later
    turn needs to adjust it. None on every zoom written before round 72, and
    _sig_canon drops None keys, so old signatures and cached renders hold.
    """
    id: str
    start: float
    end: float
    strength: float = 0.25
    mode: Optional[Literal["punch", "ease", "push_in", "pull_out",
                           "follow", "path"]] = None
    cx: Optional[float] = None
    cy: Optional[float] = None
    path: Optional[List[ZoomPathPoint]] = None
    ease: Optional[Literal["cubic_in_out", "linear"]] = None
    rect: Optional[List[float]] = None


# Round 35: the junction library grew past the two dips. Every style is
# duration-preserving BY CONSTRUCTION (each block animates within its own
# footage around the junction; audio concat is untouched) — that property is
# why no timeline math anywhere changes when transitions change.
#   whip_left/whip_right — the frame whips off in that direction with a
#     motion smear; the next block whips in.
#   zoom_punch — the outgoing block accelerates INTO the cut (fast push-in),
#     the incoming block lands from a slight over-zoom.
#   glitch — an RGB-split / noise burst on the frames around the cut.
#   flash — a white flash that peaks exactly on the cut (dip_white's louder
#     sibling: additive flash, not a fade-through).
TRANSITION_STYLES = ("dip_black", "dip_white", "whip_left", "whip_right",
                     "zoom_punch", "glitch", "flash")
TRANSITION_MIN_S = 0.1
TRANSITION_MAX_S = 1.5


# WHICH junctions a transition lands on. This exists because "every cut" was
# the only option and it was wrong for the commonest edit there is.
#
# A talking-head video that has been through cut_silences has one junction per
# removed pause — 45 of them in a real 3.9-minute prod job — and essentially
# all of them sit INSIDE one continuous shot: same framing, same subject, same
# background, the speaker's head half a word further along. That is a jump cut,
# and the whole point of a jump cut is that it is invisible. Putting a 0.2s
# whip pan on each one fires a full-screen effect every two seconds through
# footage that never changed scene, which is what a real user got and correctly
# called broken.
#
#   "scene"     — junctions where the footage genuinely CHANGES: the two sides
#                 come from different shots in the index, or one side is an
#                 insert (b-roll, title card, generated clip). This is what
#                 people mean by "add transitions".
#   "every_cut" — the old behaviour. Legitimate for a montage assembled from
#                 many separate clips; it has to be asked for.
TRANSITION_SCOPES = ("scene", "every_cut")


class TransitionSpec(BaseModel):
    """A junction effect at scene changes (or, opt-in, at every cut).
    Duration-preserving (video animates out/in around each junction; audio is
    untouched), so no timeline math changes anywhere."""
    style: Literal["dip_black", "dip_white", "whip_left", "whip_right",
                   "zoom_punch", "glitch", "flash"]
    duration_s: float = 0.3
    # Absent (older EDLs) reads as "scene". Those EDLs were all written before
    # scope existed, so every one of them carries the defect above; defaulting
    # them to the fixed behaviour repairs them on their next render rather than
    # grandfathering a bug. Already-rendered outputs are untouched — renders
    # are cached by EDL fingerprint and the stored JSON does not change.
    scope: Literal["scene", "every_cut"] = "scene"


REGION_MODES = ("blur", "pixelate", "black")
REGION_MIN_FRAC = 0.01


def _coerce_mode(v):
    # the TS mirror allows mode: null for "default"; accept it here too
    return v or "blur"


class RegionItem(BaseModel):
    """A fixed rectangle of the SOURCE footage that is blurred, pixelated or
    blacked out — censoring burned-in usernames, watermarks, on-screen text.
    x/y (top-left corner) and w/h are FRACTIONS of the SOURCE frame (0-1) —
    the space look_at frames are in — so the same region works on the
    preview proxy and the full-res final, and an output reframe (crop/pad)
    carries the censored footage with it. The renderer applies regions per
    kept source segment; spliced-in clips/images are never censored.
    start/end optionally limit it to a FINAL-program time window (like
    zooms); both None means the whole video. The rectangle does not track
    motion — text that moves with the camera can leave it."""
    id: str
    mode: Literal["blur", "pixelate", "black"] = "blur"
    x: float
    y: float
    w: float
    h: float
    start: Optional[float] = None
    end: Optional[float] = None

    _mode = field_validator("mode", mode="before")(_coerce_mode)


# ── Stylize effects (round 35) ───────────────────────────────────────────
# Windowed finishing effects on the program picture. Each is one opinionated,
# render-tested filter chain; intensity is 0-1 with a per-kind neutral
# default. start/end are FINAL-program seconds; both None = whole program.
# CONTENT-anchored (like zooms): a stylized moment follows its footage
# through later cuts.
# Round 52 adds the three that were missing and that users asked for by name
# five times in one day — "improve clarity", "sharpen the image", "make it HD",
# "mejorar la calidad", "enhance the whole video". The agent had nothing to
# offer and reached for contrast/saturation instead, which is a filter, not
# clarity. sharpen/denoise are RESTORATION (they make the picture read better
# without pretending to add resolution); motion_blur is the real thing the
# montage briefs meant, which had been faked with dream_blur — a soft dreamy
# haze over the WHOLE frame, the opposite of motion.
STYLIZE_KINDS = ("grain", "vignette", "glow", "chromatic", "dream_blur",
                 "vhs", "flash", "shake", "sharpen", "denoise", "motion_blur",
                 "stabilize")


class StylizeItem(BaseModel):
    id: str
    kind: Literal["grain", "vignette", "glow", "chromatic", "dream_blur",
                  "vhs", "flash", "shake", "sharpen", "denoise", "motion_blur",
                  "stabilize"]
    start: Optional[float] = None
    end: Optional[float] = None
    intensity: Optional[float] = None      # None = the kind's default (0.5)


# ── Custom filter chains (round 96) ─────────────────────────────────────────
# The open-ended sibling of stylize: the agent WRITES the ffmpeg chain itself
# instead of picking from a hand-built menu, so a look nobody anticipated
# ("VHS but with a green phosphor trail") stops requiring a new tool, a new
# enum and a deploy. The freedom is scoped to the PAYLOAD of one node — the
# EDL still says where and when, so remove/undo/diff/stitch keep working.
#
# The chain is ONE filter chain on the single program stream: commas only.
# Graph syntax (';', '[labels]') is rejected so a chain can never restructure
# the surrounding filtergraph it gets spliced into, and file/device access is
# rejected because a filter argument must never read or write the machine.
# Everything else — whether it parses, what it costs, what it looks like —
# is judged by the add tool's dry run on real pixels, not by this regex.
CUSTOM_FILTER_MAX_CHARS = 700
# Filter NAMES with reach beyond the frame: file/device/IPC access, external
# libraries with their own loaders, or wall-clock stalls dressed as filters.
_CUSTOM_DENY_NAMES = (
    "movie", "amovie", "subtitles", "ass", "sendcmd", "asendcmd",
    "zmq", "azmq", "frei0r", "frei0r_src", "ocv", "coreimage",
    "removelogo", "signature", "vidstabdetect", "vidstabtransform",
    "realtime", "arealtime", "loop", "aloop",
)
_CUSTOM_DENY_NAME_RE = re.compile(
    r"(?:^|,)\s*(" + "|".join(_CUSTOM_DENY_NAMES) + r")\s*(?:=|,|$|@)",
    re.IGNORECASE)
# Any `...file=` / `...filename=` argument (textfile, fontfile, psfile,
# stats_file, ...) is a path on the render machine. There is no legitimate
# one: fonts come from fontconfig, LUTs from set_color_grade, text from
# drawtext's inline arg.
_CUSTOM_FILE_ARG_RE = re.compile(r"\w*file(?:name)?\s*=", re.IGNORECASE)


def custom_chain_error(chain):
    """Why this chain may not be stored, or None when it is acceptable.

    Shared word for word by validate_edl (which the executor also runs) and
    the add tool, so a chain can never validate on one service and reject on
    the other."""
    if not isinstance(chain, str) or not chain.strip():
        return ("chain must be a non-empty ffmpeg video filter chain, e.g. "
                "\"hue=s=0.4,noise=alls=10:allf=t\".")
    c = chain.strip()
    if len(c) > CUSTOM_FILTER_MAX_CHARS:
        return (f"chain is {len(c)} chars — the cap is "
                f"{CUSTOM_FILTER_MAX_CHARS}. A look this long should be two "
                "filters, not twenty: simplify it.")
    if "\n" in c or "\r" in c:
        return "chain must be a single line."
    if ";" in c or "[" in c or "]" in c:
        return ("chain must be ONE filter chain on the single program "
                "stream — filters separated by commas, no ';' and no "
                "'[labels]'. To limit it in time pass start/end; to branch "
                "and recombine, compose two separate custom filters instead.")
    if c.count("'") % 2 == 1:
        return ("chain has an unbalanced single quote — every ' must be "
                "closed.")
    if _CUSTOM_FILE_ARG_RE.search(c):
        return ("chain may not reference files (textfile=, fontfile=, "
                "psfile=, ...) — filter arguments must be inline. Fonts come "
                "from the system, text goes in drawtext's text= arg.")
    m = _CUSTOM_DENY_NAME_RE.search(c)
    if m:
        return (f"the filter '{m.group(1)}' is not allowed — it reaches "
                "outside the frame (files, devices, IPC or wall-clock "
                "stalls). Build the look from pixel filters only.")
    return None


class CustomFilterItem(BaseModel):
    """One agent-written filter chain applied to the program picture.

    start/end are FINAL-program seconds; both None = the whole program.
    Content-anchored like stylize: the windowed moment follows its footage
    through later cuts. label is the agent's short human name for the look
    ("VHS green phosphor") — what diffs and the timeline show instead of the
    raw chain."""
    id: str
    chain: str
    start: Optional[float] = None
    end: Optional[float] = None
    label: Optional[str] = None


class GradeCustom(BaseModel):
    """Continuous color controls applied to all footage AFTER the preset
    grade (captions/graphics are never graded). All optional; a value of
    None means 'leave that axis alone', so a custom grade that only warms
    the image says only that."""
    exposure: Optional[float] = None       # -1..1 (maps to eq brightness)
    contrast: Optional[float] = None       # 0.5..1.6 (1.0 neutral)
    saturation: Optional[float] = None     # 0..2   (1.0 neutral)
    temperature: Optional[float] = None    # -1 (cool) .. 1 (warm)
    tint: Optional[float] = None           # -1 (green) .. 1 (magenta)
    # Round 82: "more light and remove shadows" is how a real user asked for
    # a lift — the agent translated it to shadows=0.35 and the tool rejected
    # the axis, so half their request was silently dropped. These are the
    # tonal-region axes every consumer grading UI has.
    shadows: Optional[float] = None        # -1 (deepen) .. 1 (lift darks)
    highlights: Optional[float] = None     # -1 (recover) .. 1 (brighten)


# ── The floating window (round 51) ──────────────────────────────────────────
# The single most-requested look for a screen recording: the picture inset a
# little, its corners rounded, a soft shadow under it, floating on a colour or
# gradient backdrop. It is applied at the very END of the picture chain — after
# captions and graphics — so the "window" is the FINISHED video, which is what
# the look means and what makes it predictable (everything the user sees inside
# the frame scales together, nothing is half-in and half-out).
#
# It is ONE overlay of ONE pre-built RGBA plate: backdrop and shadow are baked
# into the plate's opaque pixels and the picture area is a rounded transparent
# hole, so the rounded corners, the shadow and the gradient cost exactly one
# composite per frame instead of a geq pass. That matters on the 1-vCPU box.
SCREEN_FRAME_INSET_MIN = 0.02
SCREEN_FRAME_INSET_MAX = 0.35
SCREEN_FRAME_RADIUS_MAX = 0.25


class ScreenFrame(BaseModel):
    """inset: how much of the frame the backdrop takes, as a fraction of the
    output width (0.08 = the picture is 92% as wide, centred). radius: corner
    rounding as a fraction of the INSET PICTURE's short side. shadow: 0 = none,
    1 = heavy. background / background2 + direction reuse the colour-card
    gradient renderer — one gradient implementation, shared with
    add_color_screen."""
    inset: float = 0.08
    radius: float = 0.04
    shadow: float = 0.5
    background: str = "#0B0B0B"
    background2: Optional[str] = None
    direction: Literal["vertical", "horizontal", "diagonal", "radial"] = \
        "vertical"


# ── Mid-video aspect change (round 51) ──────────────────────────────────────
# "Go vertical for this bit." A rendered file has ONE resolution for its whole
# duration — that is the container, not a choice — so a mid-video aspect change
# is an animated MATTE inside the fixed canvas: the bars close in (or open out)
# over `duration_s`, and the picture optionally pushes in by exactly the amount
# that keeps the subject the same size on screen as the frame narrows.
#
# The bars are drawbox with time expressions (one filter, four numbers per
# frame), not a second render at another size. Nothing about the timeline
# changes, which is why this can never desync audio or move a caption.
FRAME_SHIFT_RATIOS = ("source", "16:9", "9:16", "1:1", "4:5", "4:3")
FRAME_SHIFT_MIN_S = 0.1
FRAME_SHIFT_MAX_S = 4.0


class FrameShift(BaseModel):
    """At `at` (output seconds) the visible frame morphs to `ratio` over
    `duration_s` and stays there until the next shift (or the end).
    `zoom` pushes the picture in as the frame narrows so the subject holds
    its size; `color` is what the new bars are filled with."""
    id: str
    at: float
    ratio: Literal["source", "16:9", "9:16", "1:1", "4:5", "4:3"]
    duration_s: float = 0.8
    zoom: bool = True
    color: str = "#000000"


class Effects(BaseModel):
    """Whole-program visual effects. grade is a color-grade preset applied
    to all footage (never to burned captions); zooms are punch-in/eased/
    Ken Burns windows; fades are to/from black at the very start/end
    (video + audio); transition dips through black/white at every cut;
    regions censor fixed rectangles (Optional so pre-round-12 EDLs keep
    their signatures); stylize is the windowed finishing-effect stack and
    grade_custom the continuous color controls (both round 35, Optional
    for the same signature reason)."""
    grade: Optional[Literal["vibrant", "warm", "cool", "bw", "vintage",
                            "cinematic"]] = None
    zooms: List[ZoomItem] = Field(default_factory=list)
    fade_in_s: Optional[float] = None
    fade_out_s: Optional[float] = None
    transition: Optional[TransitionSpec] = None
    regions: Optional[List[RegionItem]] = None
    stylize: Optional[List[StylizeItem]] = None
    grade_custom: Optional[GradeCustom] = None
    # Round 51. Both Optional-None (never an empty list default): _sig_canon
    # drops nested None keys but keeps an empty list, so a `[]` default here
    # would change the signature of every EDL that has any effects at all and
    # re-render the lot.
    screen_frame: Optional[ScreenFrame] = None
    frame_shifts: Optional[List[FrameShift]] = None
    # Round 96 — agent-written filter chains (the open-ended stylize).
    # Optional-None for the same signature reason as screen_frame above.
    custom: Optional[List[CustomFilterItem]] = None


# Canvas (round 34) — output geometry for a program that has NO main source
# video to probe: an image-only / clip-only / generated timeline. When a
# project HAS a main video, its geometry comes from probing that video and
# `canvas` stays None; the `keep` list is the program. When there is no main
# video, `keep` is empty and `canvas` supplies the output frame — the program
# is then the ordered `inserts` (clips/images) laid end-to-end on that canvas,
# reusing the existing insert-concat machinery. Optional everywhere so every
# EDL ever written (which had no `canvas` key) hashes identically.
CANVAS_MIN_PX = 16
CANVAS_MAX_PX = 4096
CANVAS_FPS_MIN = 1.0
CANVAS_FPS_MAX = 60.0
DEFAULT_CANVAS_FPS = 30.0
# Canonical pixel frames per output ratio, used when a canvas program is born
# from a chosen aspect (a generated image / a first clip). 1080 on the long
# edge is the render target the proxy/finals already assume.
CANVAS_DIMS = {
    "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
    "4:5": (1080, 1350), "4:3": (1440, 1080),
}


class Canvas(BaseModel):
    """Output frame for a no-main-video program. width/height are the final
    pixel dimensions; fps the output rate; bg_color the fill behind gaps and
    letterboxing. Present iff the EDL's program is built purely from
    inserts/overlays on a synthetic base (keep is empty)."""
    width: int
    height: int
    fps: float = DEFAULT_CANVAS_FPS
    bg_color: str = "#000000"


# ── Overlays (round 35): the layered-track primitive ─────────────────────
# An overlay draws an asset OVER the program picture for a window of program
# time — picture-in-picture b-roll, a corner inset, a logo, a full cover
# with opacity. x/y are the overlay's CENTER as fractions of the output
# frame and are keyframeable (slide/drift moves); scale is the overlay's
# width as a fraction of the frame width. PROGRAM-anchored: keep changes
# clamp overlays to the new program rather than remapping through source
# (an overlay covers a span of the *edit*, not a moment of the footage).
OVERLAY_ANIMS = ("fade", "slide_left", "slide_right", "slide_up")
OVERLAY_SCALE_MIN = 0.05
OVERLAY_SCALE_MAX = 1.0


# ── The screen takeover (round 55) ──────────────────────────────────────────
# "You're filming a laptop; push into the screen and let what's ON the screen
# become the whole video." Every piece of this already existed separately — an
# overlay, a targeted zoom, a spliced clip — and putting them side by side is
# exactly what does NOT work: an overlay is drawn ABOVE the zoom, so the
# content sits flat on the glass while the shot pushes past it, and the cut to
# full-screen lands as a jump because nothing guarantees the two frames match.
#
# What makes it read as one move is that the content is CORNER-PINNED to the
# screen and the pin travels with the push. The corners are a real per-frame
# projective map (ffmpeg's `perspective`, eval=frame), so an angled screen is
# skewed onto the glass and UNSKEWS as the frame arrives — the flattening IS
# the transition. Three consequences worth knowing before changing any of it:
#
#   * The camera push and the pin are ONE resolver (renderer._screen_lock_terms).
#     A separate ZoomItem beside the overlay could be remapped by a later cut
#     while the overlay was only clamped, and the content would slide off the
#     screen it is pinned to. The zoom does not exist as its own item at all.
#   * The pin ENDS on the identity map — the last frame of the takeover is the
#     asset rendered 1:1 at full frame, pixel-identical to the first frame of
#     the clip that follows it. That is what makes the handoff invisible, and
#     it is why the takeover needs no crossfade to hide a scale mismatch.
#   * `perspective` clamps samples outside the source to its EDGE pixels, so
#     the asset is padded with a transparent border and the destination quad
#     is grown by exactly that border's share. Without the border the whole
#     frame comes out opaque (the base disappears); without the compensation
#     the handoff is off by the border's width.
SCREEN_TAKEOVER_MIN_S = 0.4
SCREEN_TAKEOVER_MAX_S = 5.0
# Below this the screen is too small a target: the push would be a >12x blowup
# of the base, which is mush long before the content covers it.
SCREEN_QUAD_MIN_FRAC = 0.08


class ScreenLock(BaseModel):
    """Pins this overlay into a quadrilateral of the PROGRAM picture (a device
    screen in the shot) and rides the push into it.

    corners: 8 numbers — x0,y0,x1,y1,x2,y2,x3,y3 as fractions of the OUTPUT
    frame, in ffmpeg's `perspective` order: top-left, top-right, BOTTOM-LEFT,
    bottom-right. That order is deliberately not the intuitive clockwise one —
    it is kept identical to the filter's so there is exactly one convention
    between the tool, the detector and the graph and no re-ordering step that
    could silently transpose two corners into a bow-tie.

    push: how far the camera travels, 0-1 of the distance that makes the quad
    fill the frame. At 1 the push alone delivers the takeover and the content
    never leaves the glass; below 1 the content finishes the journey itself
    over the tail of the window.

    ease: the shape of the move. 'smooth' (smoothstep) is the default and the
    right answer nearly always; 'accelerate' dives; 'linear' is for matching
    an external move.
    """
    corners: List[float]
    push: float = 1.0
    ease: Literal["smooth", "accelerate", "linear"] = "smooth"
    # Round 71g: land=False turns OFF the through-cut momentum settle (the
    # brief overshoot past full frame after the handoff). Default None keeps
    # the settle AND the signature of every takeover already written — a
    # real user read the settle as "it zooms in the third scene then
    # returns" and there was no way to ask for a dead-flat landing.
    land: Optional[bool] = None
    # Round 63: the filmed screen WOBBLES — the shot is handheld — and a pin
    # rigid at one measured quad slides visibly against the glass it claims to
    # hold. corner_path is the screen's measured motion through the window:
    # entries of [t_rel, x0,y0,x1,y1,x2,y2,x3,y3] (window-relative seconds,
    # ascending, same corner order as `corners`), tracked by optical flow on
    # the executor. The renderer lerps corners between entries; `corners`
    # itself stays the ARRIVAL-frame quad, which is what the camera geometry
    # is computed from. Absent (every EDL before round 63) = static pin,
    # byte-identical behaviour to round 55.
    corner_path: Optional[List[List[float]]] = None
    # Round 65: where the corners CAME FROM — "matched" (the content's own
    # pixels found on the glass by feature homography: exact rotation and
    # keystone), "measured" (screendet), "read" (vision estimate), "user".
    # The renderer keys the content's appearance on this: matched corners
    # mean the glass is already showing this very content, so the pin lives
    # on it from the window's start; anything less trustworthy keeps the
    # round-64 late dissolve. Must be a schema field or validate_edl silently
    # drops it (the round-60 lesson) and every takeover reads as untrusted.
    corners_source: Optional[str] = None


class OverlayItem(BaseModel):
    id: str
    asset_key: str
    kind: Literal["video", "image"]
    start: float                    # FINAL-program seconds
    duration_s: float
    x: AnimFloat = 0.5
    y: AnimFloat = 0.5
    scale: float = 0.4
    # fit 'cover' (round 36): the overlay fills the WHOLE output frame
    # (scaled up + cropped, x/y/scale ignored) — the b-roll cutaway mode:
    # picture switches to the overlay while the program's audio keeps
    # playing. None = the legacy width-fraction PIP and is dropped from
    # signatures, so stored EDLs render byte-identically.
    fit: Optional[Literal["cover"]] = None
    opacity: Optional[float] = None      # 0.05-1.0; None = fully opaque
    rotation: Optional[float] = None     # degrees, static
    source_start_s: Optional[float] = None   # video overlays: seek into clip
    entrance: Optional[Literal["fade", "slide_left", "slide_right",
                               "slide_up"]] = None
    exit: Optional[Literal["fade", "slide_left", "slide_right",
                           "slide_up"]] = None
    # Video overlays are silent v1 (their audio never mixes) — a PIP that
    # suddenly talks over the program is almost never what "add b-roll"
    # means, and the honest tool result says so.
    #
    # Round 55. When set, this overlay is CORNER-PINNED into the footage and
    # carries its own camera push (see ScreenLock). x/y/scale/rotation/fit are
    # all ignored — the pin owns the geometry — and the renderer emits it from
    # a different branch. None on every overlay ever written, and _sig_canon
    # drops nested None keys, so no stored EDL changes signature.
    screen: Optional[ScreenLock] = None


# ── Text overlays (round 35): the motion-graphics layer ──────────────────
# Rendered by libass via a SECOND .ass file burned after captions, from the
# parameterized templates in worker/graphics.py — title cards, lower thirds,
# callouts, big numbers, quotes, chapter markers. PROGRAM-anchored.
TEXT_TEMPLATES = ("title", "subtitle", "lower_third", "callout",
                  "big_number", "quote", "chapter")
TEXT_ANIMS = ("none", "fade", "pop", "slide_up", "blur_in", "whip", "rise",
              "drop", "typewriter")
TEXT_FONTS = ("Inter Display Black", "Inter Display ExtraBold",
              "Inter Display Bold", "Anton", "Bebas Neue", "Archivo Black",
              "Poppins Black", "Syne ExtraBold", "Playfair Display Black",
              "Instrument Serif", "DM Serif Display", "Montserrat",
              # round 79 — the site's own wordmark face (the navbar renders
              # "Valmera" in Plus Jakarta Sans 800), already bundled for the
              # watermark; exposing it lets brand text match the product.
              "Plus Jakarta Sans ExtraBold")


class TextItem(BaseModel):
    id: str
    text: str
    start: float                    # FINAL-program seconds
    end: float
    template: Literal["title", "subtitle", "lower_third", "callout",
                      "big_number", "quote", "chapter"] = "title"
    x: Optional[float] = None       # fractions of frame; None = template's
    y: Optional[float] = None
    size_scale: Optional[float] = None      # 0.4-3.0 on the template's size
    color: Optional[str] = None             # #RRGGBB
    accent_color: Optional[str] = None
    font: Optional[Literal["Inter Display Black", "Inter Display ExtraBold",
                           "Inter Display Bold", "Anton", "Bebas Neue",
                           "Archivo Black", "Poppins Black", "Syne ExtraBold",
                           "Playfair Display Black", "Instrument Serif",
                           "DM Serif Display", "Montserrat",
                           "Plus Jakarta Sans ExtraBold"]] = None
    # "none" = instant: full opacity at frame one, gone at the last frame.
    entrance: Optional[Literal["none", "fade", "pop", "slide_up", "blur_in",
                               "whip", "rise", "drop", "typewriter"]] = None
    exit: Optional[Literal["none", "fade", "pop", "slide_up", "blur_in",
                           "whip", "rise", "drop"]] = None
    uppercase: Optional[bool] = None
    box: Optional[bool] = None      # backing panel behind the text
    # Round 40 — the text OWNS a spliced card rather than a span of the edit.
    # Set to an insert id by add_title_card. Plain program-anchored texts
    # leave it None (and _sig_canon drops nested None keys, so every text
    # written before this field hashes identically — no re-renders).
    #
    # Why it exists: a card's program position moves whenever ANY earlier
    # insert is added, moved, resized or removed, but a program-anchored
    # text does not move with it. The card then renders BLANK and its words
    # land on the footage — which is exactly what a real session hit, where
    # three title cards were added and removed twelve times chasing a black
    # frame the agent could see but not explain. timeline.remap_program_items
    # re-derives an anchored text from its card's new window instead.
    anchor_insert: Optional[str] = None
    # Round 60 — the words go BEHIND the moving subject.
    #
    # Set by add_text_behind, which measures the subject out of the shot and
    # stores a grayscale mask clip. The renderer draws this text on a copy of
    # its own picture and then lays the masked subject back over the words; the
    # subject's pixels are the render's own, so a mask measured on the 540p
    # proxy composites correctly into a 4K export.
    #
    # SOURCE seconds, not program seconds, and that is the whole point: the
    # mask is pixels of particular footage, so the text that owns it is
    # CONTENT-anchored (timeline.remap_program_items moves it through the source
    # like a zoom, and drops it when that footage is cut). A program-anchored
    # window would slide off its own matte the first time anything upstream was
    # trimmed, and the subject would be cut out of the wrong second of video.
    behind: Optional["SubjectMatte"] = None


class SubjectMatte(BaseModel):
    """Where the subject-mask clip is, and which footage it was measured from."""
    asset_key: str
    src_start: float
    src_end: float
    fp: str                                 # derivation fingerprint (cache key)
    coverage: Optional[float] = None        # mean share of frame that moved
    fps: Optional[float] = None
    # How the mask was made (round 64): "person" = segmentation model on the
    # executor, "plate" = the photometric fallback. Ride-along metadata so a
    # cache hit can speak honestly about what it is serving.
    method: Optional[str] = None


# ── Speed spans (round 35): time remapping ───────────────────────────────
# A speed factor over a SOURCE-time range (like volume automation): factor
# 2.0 plays that footage at double speed, 0.5 at half. SOURCE-anchored: the
# ramp belongs to the footage it was placed on. Audio keeps its pitch
# (atempo). Slow motion duplicates frames (no synthetic interpolation on
# this hardware) — the tools say so below 0.6x.
SPEED_FACTOR_MIN = 0.25
SPEED_FACTOR_MAX = 4.0


class SpeedSpan(BaseModel):
    id: str
    start: float                    # SOURCE seconds
    end: float
    factor: float


class Master(BaseModel):
    """Output mastering. loudness 'social' normalizes the final mix to
    -14 LUFS / -1.5 dBTP (the streaming/social target) via loudnorm —
    applied to preview AND final so what the user approves is what ships."""
    loudness: Optional[Literal["social"]] = None


class StemMix(BaseModel):
    """Round 97 (#7): rebalance the ORIGINAL footage's music vs its speech.

    The renderer swaps the source audio for the two separated stems (Demucs
    two-stem: vocals / everything-else), each at its own gain, wherever the
    graph would have read the original track. The keys are written by the
    separate_music tool after materializing the stems (cached per source
    sha, so a video is separated once, ever). Removing the node restores the
    untouched original — separation artifacts are only ever in the signal
    path while the user wants the split.

    -60 dB is an effective mute; 0 dB is untouched. Small positive gains are
    allowed (bring the voice up), bounded so a typo cannot ship a blowout.
    """
    vocals_key: str
    accomp_key: str
    voice_gain_db: float = 0.0
    music_gain_db: float = 0.0

    @field_validator("voice_gain_db", "music_gain_db")
    @classmethod
    def _bounded(cls, v):
        if not (-60.0 <= v <= 6.0):
            raise ValueError("stem gains must be between -60 and +6 dB")
        return v


CLEAN_FILLS = ("text", "box")


def patch_fingerprint(src_sha, regions, window):
    """Identity of one repainted WINDOW (round 92): which video, which
    rectangles, which span. Content-addresses the patch clips so re-erasing
    the same thing is a storage hit, the export can find (or rebuild) the
    full-res twin deterministically, and a replaced upload is detected the
    same way clean_fingerprint detects it for a whole cleaned source."""
    payload = json.dumps(
        {"w": [round(float(window[0]), 2), round(float(window[1]), 2)],
         "r": [{k: r.get(k) for k in
                ("x", "y", "w", "h", "start", "end", "fill")}
               for r in (regions or [])]}, sort_keys=True)
    return hashlib.sha1(f"{src_sha}|patch|{payload}".encode()).hexdigest()


def clean_fingerprint(src_sha, regions, cursor=None):
    """Identity of a repainted source: (which video, which rectangles).

    Shared by the tool that WRITES the cleaned file and the renderer that
    READS it, so the renderer can prove the file it is about to render is a
    repaint of the video this project currently holds. That check is what
    stops a replaced upload from rendering the OLD footage: the EDL survives a
    replace, so without it a project whose captions were erased would keep
    rendering the erased copy of the video the user just swapped out.
    """
    payload = json.dumps([{k: r.get(k) for k in
                           ("x", "y", "w", "h", "start", "end", "fill")}
                          for r in (regions or [])], sort_keys=True)
    # Round 51: the cursor pass is a SECOND way the source can be derived, and
    # it shares this one identity because there is one derived file. Appending
    # only when a cursor pass exists is what keeps every fingerprint written
    # before round 51 identical — the cleaned objects already in storage stay
    # addressable, so no erase silently re-runs on the next render.
    if cursor:
        payload += json.dumps({k: cursor.get(k) for k in
                               ("scale", "smoothing", "click_highlight",
                                "click_times")}, sort_keys=True)
    return hashlib.sha1(((src_sha or "") + payload).encode()).hexdigest()


class PatchItem(BaseModel):
    """One repainted WINDOW of the source, stored as a short patch clip the
    renderer overlays on the source clock (round 92).

    asset_key is the PROXY-resolution patch — built inside the erase call in
    seconds, and what previews composite. full_key is its full-resolution
    twin for exports; usually absent at write time and materialized by the
    final render (content-addressed via fp, so the export can derive the key
    and build it exactly once). regions ride along so the export can rebuild
    the same repaint at full res, remove_erase can list/undo by id, and fp —
    patch_fingerprint(src sha, regions, window) — ties the clips to the
    upload they repaint: a replaced video renders as itself, never under a
    stale patch.
    """
    id: str
    asset_key: str
    full_key: Optional[str] = None
    fp: str
    src_start: float
    src_end: float
    regions: List["CleanRegion"] = Field(default_factory=list)


class CleanRegion(BaseModel):
    """One rectangle that was REPAINTED OUT of the source pixels.

    Unlike a RegionItem (which the renderer blurs or bars at render time),
    this is already gone: the erase tool wrote a cleaned copy of the source
    and the render reads that copy. The record is kept so the agent can list
    what was erased, undo one of them (which re-cleans from the ORIGINAL —
    never from an already-cleaned file, which would compound the repaint), and
    so the fingerprint below can prove the cleaned file matches this EDL.

    fill 'text' repaints only the letter strokes and keeps the picture behind
    them; 'box' repaints the whole rectangle, for an object rather than ink.
    """
    id: str
    x: float
    y: float
    w: float
    h: float
    start: Optional[float] = None
    end: Optional[float] = None
    fill: Literal["text", "box"] = "text"
    kind: Optional[str] = None          # what the detector called it


# ── The cursor pass (round 51) ──────────────────────────────────────────────
# On a screen recording the pointer is the narrator, and at 1080p scaled into a
# phone-sized player it is roughly four pixels of grey. This finds it in the
# SOURCE frames, smooths the hand jitter out of its path, and redraws it big.
#
# SOURCE time throughout — click_times are moments in the footage, not in the
# edit, so the pass survives every later cut for free (it is baked into the
# derived source, which every kept segment reads through).
CURSOR_SCALE_MIN = 1.0
CURSOR_SCALE_MAX = 4.0
CURSOR_MAX_CLICKS = 60


class CursorPass(BaseModel):
    """scale: how many times bigger the redrawn pointer is. smoothing: 0 = the
    detected path untouched, 1 = heavily filtered (a one-euro filter, so fast
    deliberate moves stay sharp while a resting hand stops shaking).
    click_highlight draws an expanding ring at each time in click_times."""
    scale: float = 2.0
    smoothing: float = 0.5
    click_highlight: bool = True
    click_times: List[float] = Field(default_factory=list)
    # Recorded by the pass, never sent by a caller: the fraction of frames the
    # pointer was actually located in. The tool reports it verbatim rather than
    # claiming a clean result — a recording with no visible cursor (a phone
    # screen capture, a tap-driven demo) has to say so.
    found_frac: Optional[float] = None


class SourceClean(BaseModel):
    """Pointer to the repainted source this EDL renders from.

    asset_key replaces the original for FINAL renders and proxy_key replaces
    the 540p proxy for PREVIEWS, so what the user approves in the preview is
    what ships. fp is a fingerprint of (original sha, regions): the renderer
    refuses a cleaned file that does not match this EDL's regions rather than
    silently rendering someone else's repaint.
    """
    asset_key: str
    proxy_key: Optional[str] = None
    fp: str
    regions: List[CleanRegion] = Field(default_factory=list)
    # Round 51 — the cursor pass. It lives HERE, next to the erase regions,
    # rather than in a second `source_cursor` field, because there is exactly
    # one derived source file and the renderer must never have to decide which
    # of two derivations wins. The passes chain in a fixed order (repaint,
    # then cursor), both re-derive from the untouched original, and one
    # fingerprint identifies the result. None on every pre-round-51 EDL, and
    # _sig_canon drops nested None, so signatures are untouched.
    cursor: Optional[CursorPass] = None


class EDL(BaseModel):
    # keep is empty ONLY for a canvas program (image/clip-only, no main video);
    # otherwise it is the non-empty cut list of the one main video.
    keep: List[List[float]]
    canvas: Optional[Canvas] = None
    captions: Optional[Union[CaptionsFromTranscript, List[CaptionItem]]] = None
    music: List[MusicItem] = Field(default_factory=list)
    sfx: List[SfxItem] = Field(default_factory=list)
    volume: List[VolumeItem] = Field(default_factory=list)
    frame: Optional[Frame] = None
    inserts: List[InsertItem] = Field(default_factory=list)
    voiceover: List[VoiceoverItem] = Field(default_factory=list)
    effects: Optional[Effects] = None
    # round 35 — every field below is empty/None on every EDL written before
    # it existed, and edl_signature drops empty values, so historical
    # signatures are untouched.
    overlays: List[OverlayItem] = Field(default_factory=list)
    texts: List[TextItem] = Field(default_factory=list)
    speed: List[SpeedSpan] = Field(default_factory=list)
    master: Optional[Master] = None
    # Round 97: music/voice rebalance of the original audio via separated
    # stems — see StemMix. Audio-only: a change here never re-encodes video.
    stem_mix: Optional[StemMix] = None
    # round 37 — PROGRAM-time windows where burned captions are suppressed.
    # Captions were all-or-nothing before this: a full-frame effect or a title
    # treatment that covers the frame still had the spoken-word captions burned
    # over it, and the only way out was turning captions off for the WHOLE
    # video. Inserts already create caption-free time (they are not
    # transcribed, so kept_words maps around them) — this covers the OVERLAY
    # case, where the speaker keeps talking under the effect.
    # Empty list, so edl_signature drops it and every pre-round-37 EDL keeps
    # its signature and its cached renders.
    caption_mutes: List[List[float]] = Field(default_factory=list)
    # round 39 — burned-in text/objects repainted OUT of the source pixels.
    # None on every EDL written before it existed, and edl_signature drops
    # None, so historical signatures and their cached renders are untouched.
    source_clean: Optional[SourceClean] = None
    # round 92 — repainted WINDOWS overlaid on the source clock, replacing
    # the whole-file clean pass for new erases: cost scales with the erased
    # span, not the video (job 2685 spent 965s of a 1057s turn re-deriving a
    # 39s file seven times, then timed out). Empty list, so edl_signature
    # drops it and every earlier EDL keeps its signature and cached renders.
    patches: List[PatchItem] = Field(default_factory=list)


def default_edl(duration):
    return EDL(keep=[[0.0, _r(duration)]]).model_dump()


def canvas_edl(ratio="16:9", fps=DEFAULT_CANVAS_FPS, bg_color="#000000"):
    """The minimal EDL for a program with no main video: an empty keep list and
    a canvas of the chosen aspect. Visual content arrives as inserts."""
    w, h = CANVAS_DIMS.get(ratio, CANVAS_DIMS["16:9"])
    return EDL(keep=[], canvas=Canvas(width=w, height=h, fps=_r(fps),
                                      bg_color=bg_color)).model_dump()


def is_canvas_program(edl_dict):
    """True when this EDL is a no-main-video program (empty keep + a canvas)."""
    return not (edl_dict.get("keep") or []) and bool(edl_dict.get("canvas"))


def output_duration(keep):
    return round(sum(e - s for s, e in keep), 2)


def _span_of(sp):
    if isinstance(sp, dict):
        return float(sp["start"]), float(sp["end"]), float(sp["factor"])
    return float(sp.start), float(sp.end), float(sp.factor)


def speed_pieces(s, e, speed):
    """Split ONE keep span into constant-rate pieces [(ps, pe, factor)].
    speed is the EDL's speed list (source-time spans); pieces outside every
    span run at 1.0. The single source of truth for time-remap math — the
    Timeline, the renderer and the duration helpers all call this."""
    if not speed:
        return [(s, e, 1.0)]
    cuts = {round(s, 4), round(e, 4)}
    for sp in speed:
        a, b, _f = _span_of(sp)
        if b > s + 1e-6 and a < e - 1e-6:
            cuts.add(round(min(max(a, s), e), 4))
            cuts.add(round(min(max(b, s), e), 4))
    pts = sorted(cuts)
    out = []
    for i in range(len(pts) - 1):
        ps, pe = pts[i], pts[i + 1]
        if pe - ps < 1e-4:
            continue
        mid = (ps + pe) / 2.0
        f = 1.0
        for sp in speed:
            a, b, fac = _span_of(sp)
            if a - 1e-6 <= mid <= b + 1e-6:
                f = fac
                break
        out.append((ps, pe, f))
    return out or [(s, e, 1.0)]


def sped_len(s, e, speed):
    """Output seconds one keep span occupies once speed is applied."""
    return sum((pe - ps) / f for ps, pe, f in speed_pieces(s, e, speed))


def keep_boundaries(keep, speed=None):
    """Output-time positions (pre-insert timeline) where a splice may sit:
    0, each segment join, and the end. Speed-aware: a sped segment occupies
    its remapped length."""
    bounds, acc = [0.0], 0.0
    for s, e in keep:
        acc = round(acc + sped_len(s, e, speed), 2)
        bounds.append(acc)
    return bounds


def program_duration(edl_dict):
    """Final program length: kept footage (speed-remapped) plus inserts."""
    speed = edl_dict.get("speed") or []
    dur = sum(sped_len(s, e, speed) for s, e in edl_dict["keep"])
    for ins in edl_dict.get("inserts") or []:
        dur += float(ins["duration_s"])
    return round(dur, 2)


def _check_span(name, s, e, max_end, min_len=MIN_SPAN_S):
    if s < 0 or e < 0:
        raise EDLValidationError(f"{name}: negative time ({s}, {e}). "
                                 "Times are seconds from 0.")
    if e - s < min_len:
        raise EDLValidationError(
            f"{name}: span [{s}, {e}] is shorter than {min_len}s.")
    if max_end is not None and e > max_end + 0.01:
        raise EDLValidationError(
            f"{name}: end {e} exceeds the limit {round(max_end, 2)}s.")


def quad_bbox(corners):
    """(x, y, w, h) of a screen quad's axis-aligned bounds, in frame
    fractions. The one place the 8-number layout is unpacked."""
    xs = corners[0::2]
    ys = corners[1::2]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def quad_is_sane(corners):
    """A quad the perspective filter can actually invert: no crossed sides, no
    corner folded past another. Returns (ok, why).

    The check is the SIGN of the cross product at each corner walked in
    boundary order (TL -> TR -> BR -> BL, which is NOT the storage order — see
    ScreenLock). All four the same sign means convex and consistently wound; a
    mixed sign means the caller handed over a bow-tie, which `perspective`
    renders as a torn smear rather than refusing, so it has to be caught here.
    """
    x0, y0, x1, y1, x2, y2, x3, y3 = corners
    pts = [(x0, y0), (x1, y1), (x3, y3), (x2, y2)]     # boundary order
    signs = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx, cy = pts[(i + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-7:
            return False, "three of its corners are on one line"
        signs.append(cross > 0)
    if len(set(signs)) != 1:
        return False, ("its corners are crossed — the order is top-left, "
                       "top-right, BOTTOM-LEFT, bottom-right")
    return True, ""


def _check_screen_lock(lock, name, window_s):
    if not isinstance(lock.corners, list) or len(lock.corners) != 8:
        raise EDLValidationError(
            f"{name}.corners must be exactly 8 numbers — x0,y0,x1,y1,x2,y2,"
            "x3,y3 as fractions of the output frame (top-left, top-right, "
            "bottom-left, bottom-right).")
    try:
        vals = [float(v) for v in lock.corners]
    except (TypeError, ValueError):
        raise EDLValidationError(f"{name}.corners must all be numbers.")
    for v in vals:
        if v != v or v in (float("inf"), float("-inf")):
            raise EDLValidationError(f"{name}.corners has a non-finite value.")
        if not (-0.5 <= v <= 1.5):
            raise EDLValidationError(
                f"{name}.corners are FRACTIONS of the frame (0-1); got {v}. "
                "Pass pixel coordinates divided by the frame width/height.")
    lock.corners = [round(v, 4) for v in vals]
    ok, why = quad_is_sane(lock.corners)
    if not ok:
        raise EDLValidationError(f"{name}.corners do not form a usable "
                                 f"quadrilateral: {why}.")
    _x, _y, qw, qh = quad_bbox(lock.corners)
    if qw < SCREEN_QUAD_MIN_FRAC or qh < SCREEN_QUAD_MIN_FRAC:
        raise EDLValidationError(
            f"{name}: the screen is only {qw:.2f}x{qh:.2f} of the frame — "
            f"below {SCREEN_QUAD_MIN_FRAC} the push has to blow the shot up "
            "more than 12x to reach it, which is mush. Start the takeover "
            "from a closer shot of the screen.")
    lock.push = round(min(max(float(lock.push), 0.0), 1.0), 3)
    if lock.corner_path is not None:
        if not isinstance(lock.corner_path, list) or len(lock.corner_path) < 2:
            raise EDLValidationError(
                f"{name}.corner_path needs at least 2 entries of "
                "[t, x0..y3] — or omit it for a static pin.")
        prev_t = -1e9
        cleaned = []
        for i, entry in enumerate(lock.corner_path):
            if not isinstance(entry, (list, tuple)) or len(entry) != 9:
                raise EDLValidationError(
                    f"{name}.corner_path[{i}] must be [t, x0,y0,...y3] "
                    "(9 numbers).")
            try:
                tt = float(entry[0])
                q = [float(v) for v in entry[1:]]
            except (TypeError, ValueError):
                raise EDLValidationError(
                    f"{name}.corner_path[{i}] must all be numbers.")
            if tt < -0.001 or tt > window_s + 0.05:
                raise EDLValidationError(
                    f"{name}.corner_path[{i}] t={tt} is outside the takeover "
                    f"window (0-{window_s}s, window-relative).")
            if tt <= prev_t:
                raise EDLValidationError(
                    f"{name}.corner_path times must strictly ascend.")
            prev_t = tt
            for v in q:
                if v != v or v in (float("inf"), float("-inf")) \
                        or not (-0.5 <= v <= 1.5):
                    raise EDLValidationError(
                        f"{name}.corner_path[{i}] corners must be finite "
                        "fractions of the frame.")
            ok, why = quad_is_sane(q)
            if not ok:
                raise EDLValidationError(
                    f"{name}.corner_path[{i}] is not a usable quad: {why}.")
            cleaned.append([round(tt, 3)] + [round(v, 5) for v in q])
        lock.corner_path = cleaned
    if not (SCREEN_TAKEOVER_MIN_S - 0.01 <= window_s
            <= SCREEN_TAKEOVER_MAX_S + 0.01):
        raise EDLValidationError(
            f"{name}: the takeover runs {window_s}s — it must be "
            f"{SCREEN_TAKEOVER_MIN_S}-{SCREEN_TAKEOVER_MAX_S}s. Under half a "
            "second reads as a cut, over five it stalls.")


def validate_edl(data, duration=None):
    """Parse + validate an EDL dict.

    Two shapes are valid: a MAIN-VIDEO program (non-empty `keep`, validated
    against `duration` = the source video's length) or a CANVAS program (empty
    `keep` + a `canvas`, for an image/clip-only timeline with no main video —
    `duration` is then ignored). Returns a normalized EDL (times rounded to
    0.01s). Raises EDLValidationError with a message the agent can act on.
    """
    try:
        edl = EDL.model_validate(data)
    except Exception as e:
        raise EDLValidationError(f"EDL shape invalid: {str(e)[:300]}")

    canvas_prog = not edl.keep
    if canvas_prog:
        # No main video: the program is built on a canvas from inserts alone.
        if edl.canvas is None:
            raise EDLValidationError(
                "keep must contain at least one [start, end] span "
                "(or provide a canvas for an image/clip-only program).")
        c = edl.canvas
        c.width, c.height = int(c.width), int(c.height)
        if not (CANVAS_MIN_PX <= c.width <= CANVAS_MAX_PX) or \
           not (CANVAS_MIN_PX <= c.height <= CANVAS_MAX_PX):
            raise EDLValidationError(
                f"canvas width/height must be within "
                f"[{CANVAS_MIN_PX}, {CANVAS_MAX_PX}] px.")
        c.fps = round(float(c.fps), 2)
        if not (CANVAS_FPS_MIN <= c.fps <= CANVAS_FPS_MAX):
            raise EDLValidationError(
                f"canvas fps {c.fps} outside [{CANVAS_FPS_MIN}, {CANVAS_FPS_MAX}].")
        if not HEX_COLOR.match(c.bg_color or ""):
            raise EDLValidationError(
                f"canvas bg_color {c.bg_color!r} must be #RRGGBB.")
        if not edl.inserts:
            raise EDLValidationError(
                "a canvas program needs at least one insert (a clip or image) "
                "— add visual content before music/sfx/captions.")
        # Source-timeline-only features are meaningless without a main video.
        if edl.volume:
            raise EDLValidationError(
                "volume automation needs a main video (it addresses source "
                "time); not available on an image/clip-only program.")
        if isinstance(edl.captions, CaptionsFromTranscript):
            raise EDLValidationError(
                "from_transcript captions need a transcribed main video; on an "
                "image/clip-only program pass explicit caption items instead.")
        if edl.effects and edl.effects.regions:
            raise EDLValidationError(
                "censor regions address the source frame of a main video; not "
                "available on an image/clip-only program.")
        if edl.frame is not None and edl.frame.ratio != "source":
            raise EDLValidationError(
                "the output aspect of a canvas program is fixed by its canvas, "
                "not set_frame — choose the aspect when you place content "
                "instead.")
        if edl.speed:
            raise EDLValidationError(
                "speed ramps address source time and need a main video; not "
                "available on an image/clip-only program.")
        edl.keep = keep = []
        speed_dump = []
        out_dur = 0.0
    else:
        # A keep list is present: this is a main-video program; a stray canvas
        # never coexists with one.
        if duration is None:
            raise EDLValidationError(
                "internal: a main-video EDL (non-empty keep) must be validated "
                "against the source video duration.")
        edl.canvas = None
        keep = []
        for i, span in enumerate(edl.keep):
            if len(span) != 2:
                raise EDLValidationError(
                    f"keep[{i}] must be [start, end], got {span}.")
            s, e = _r(span[0]), _r(span[1])
            _check_span(f"keep[{i}]", s, e, duration)
            keep.append([s, e])

        keep.sort(key=lambda x: x[0])
        for i in range(1, len(keep)):
            if keep[i][0] < keep[i - 1][1] - 0.001:
                raise EDLValidationError(
                    f"keep segments overlap: [{keep[i-1][0]}, {keep[i-1][1]}] and "
                    f"[{keep[i][0]}, {keep[i][1]}]. Segments must be sorted and "
                    "non-overlapping.")
        edl.keep = keep

        # Speed spans: SOURCE-time ranges, sorted, non-overlapping, factors
        # clamped. Validated before durations because everything program-
        # bounded below (music, sfx, zooms, overlays, texts) must be checked
        # against the REMAPPED program length.
        if edl.speed:
            seen_sp = set()
            for i, sp in enumerate(edl.speed):
                if not sp.id or sp.id in seen_sp:
                    raise EDLValidationError(
                        f"speed[{i}].id must be non-empty and unique.")
                seen_sp.add(sp.id)
                sp.start, sp.end = _r(sp.start), _r(sp.end)
                _check_span(f"speed[{i}]", sp.start, sp.end, duration,
                            min_len=0.2)
                sp.factor = round(min(max(float(sp.factor),
                                          SPEED_FACTOR_MIN),
                                      SPEED_FACTOR_MAX), 3)
                if abs(sp.factor - 1.0) < 0.01:
                    raise EDLValidationError(
                        f"speed[{i}].factor {sp.factor} is 1.0 — that is no "
                        "change; remove the span instead.")
            edl.speed.sort(key=lambda x: x.start)
            for i in range(1, len(edl.speed)):
                if edl.speed[i].start < edl.speed[i - 1].end - 0.001:
                    raise EDLValidationError(
                        f"speed spans overlap: "
                        f"[{edl.speed[i-1].start}, {edl.speed[i-1].end}] and "
                        f"[{edl.speed[i].start}, {edl.speed[i].end}].")
        speed_dump = [s.model_dump() for s in edl.speed]
        out_dur = round(sum(sped_len(s, e, speed_dump) for s, e in keep), 2)

    # Captions on a canvas program are positioned in PROGRAM time (bounded by
    # the concatenated inserts); on a main-video program they are source time.
    cap_bound = (round(sum(max(0.0, float(i.duration_s)) for i in edl.inserts), 2)
                 if canvas_prog else duration)

    if isinstance(edl.captions, list):
        norm = []
        for i, c in enumerate(edl.captions):
            s, e = _r(c.start), _r(c.end)
            _check_span(f"captions[{i}]", s, e, cap_bound)
            if not c.text.strip():
                raise EDLValidationError(f"captions[{i}] has empty text.")
            norm.append(CaptionItem(text=c.text.strip(), start=s, end=e,
                                    style=c.style))
        edl.captions = norm
    elif isinstance(edl.captions, CaptionsFromTranscript):
        mw = edl.captions.max_words_per_caption
        if mw is not None:
            edl.captions.max_words_per_caption = \
                min(max(int(mw), 1), MAX_WORDS_PER_CAPTION)
        kg = edl.captions.karaoke_group_n
        if kg is not None:
            edl.captions.karaoke_group_n = min(max(int(kg), 1), 8)

    # Frame: 'source' is the absence of a frame — normalize so old EDLs and
    # explicit-source EDLs compare identical.
    if edl.frame is not None and edl.frame.ratio == "source":
        edl.frame = None

    # Inserts: concrete durations, unique ids, and every splice point must
    # sit exactly on a keep boundary (the tools snap; this is the backstop).
    # Boundaries are speed-aware: a sped segment occupies its remapped length.
    bounds = keep_boundaries(keep, speed_dump)
    seen_ids = set()
    for i, ins in enumerate(edl.inserts):
        ins.at_output_s = _r(ins.at_output_s)
        ins.duration_s = _r(ins.duration_s)
        if not ins.id or ins.id in seen_ids:
            raise EDLValidationError(
                f"inserts[{i}].id must be non-empty and unique.")
        seen_ids.add(ins.id)
        if not ins.asset_key:
            raise EDLValidationError(f"inserts[{i}].asset_key is empty.")
        if not (0.2 <= ins.duration_s <= MAX_INSERT_DURATION_S):
            raise EDLValidationError(
                f"inserts[{i}].duration_s {ins.duration_s} outside "
                f"[0.2, {MAX_INSERT_DURATION_S:.0f}].")
        if ins.source_start_s is not None:
            ins.source_start_s = _r(ins.source_start_s)
            if ins.source_start_s < 0:
                raise EDLValidationError(
                    f"inserts[{i}].source_start_s must be >= 0.")
            if ins.kind == "image" or ins.source_start_s == 0.0:
                ins.source_start_s = None   # meaningless / default
        if ins.motion is not None and ins.kind != "image":
            raise EDLValidationError(
                f"inserts[{i}].motion is only supported on image inserts "
                "(a Ken Burns move on a still) — video clips already move.")
        if ins.at_output_s < 0:
            raise EDLValidationError(
                f"inserts[{i}].at_output_s {ins.at_output_s} must be >= 0.")
        if not canvas_prog:
            # Main-video program: an insert splices at a keep-segment boundary.
            nearest = min(bounds, key=lambda b: abs(b - ins.at_output_s))
            if abs(nearest - ins.at_output_s) > 0.02:
                raise EDLValidationError(
                    f"inserts[{i}].at_output_s {ins.at_output_s} is not on a "
                    f"keep-segment boundary — nearest boundary is {nearest}. "
                    "Inserts splice BETWEEN kept segments (or at the start/end).")
            ins.at_output_s = nearest
    edl.inserts.sort(key=lambda x: x.at_output_s)
    if canvas_prog:
        # No keep boundaries — the ordered inserts ARE the program. Lay them
        # end-to-end (gapless concat) so the timeline is deterministic; the
        # agent reorders by choosing at_output_s.
        acc = 0.0
        for ins in edl.inserts:
            ins.at_output_s = _r(acc)
            acc += ins.duration_s

    prog_dur = out_dur + sum(x.duration_s for x in edl.inserts)

    seen_ids = set()
    for i, vo in enumerate(edl.voiceover):
        vo.start_output_s = _r(vo.start_output_s)
        if not vo.id or vo.id in seen_ids:
            raise EDLValidationError(
                f"voiceover[{i}].id must be non-empty and unique.")
        seen_ids.add(vo.id)
        if not vo.asset_key:
            raise EDLValidationError(f"voiceover[{i}].asset_key is empty.")
        if not (0 <= vo.start_output_s <= max(0.0, prog_dur - 0.05)):
            raise EDLValidationError(
                f"voiceover[{i}].start_output_s {vo.start_output_s} outside "
                f"the program (0 to {round(prog_dur, 2)}s).")
        if not (GAIN_MIN_DB <= vo.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"voiceover[{i}].gain_db {vo.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")

    seen_music_ids = set()
    for i, m in enumerate(edl.music):
        if m.id is not None:
            if not m.id or m.id in seen_music_ids:
                raise EDLValidationError(
                    f"music[{i}].id must be non-empty and unique.")
            seen_music_ids.add(m.id)
        m.start, m.end = _r(m.start), _r(m.end)
        # Music lives on the FINAL program timeline (incl. inserts) but —
        # round 79f — may extend PAST the program's end: the timeline is a
        # workbench, and the unused remainder of a song is material the
        # user splits, slips and trims, not an error. The renderer clamps
        # the mix (and the fade-out) to the program, and skips items lying
        # entirely beyond it, so nothing past the video is ever heard.
        if not (0.0 <= m.start < m.end):
            raise EDLValidationError(
                f"music[{i}] span {m.start}-{m.end} is not a valid range.")
        if m.end - m.start < 0.05:
            raise EDLValidationError(
                f"music[{i}] span {m.start}-{m.end} is shorter than 0.05s.")
        if m.end > prog_dur + 3600:
            raise EDLValidationError(
                f"music[{i}].end {m.end} is more than an hour past the "
                f"program ({round(prog_dur, 2)}s) — that is a stray value, "
                "not a workbench tail.")
        if not (GAIN_MIN_DB <= m.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"music[{i}].gain_db {m.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")
        # Fitting fields. Normalize to None when they carry no meaning, so a
        # zero fade and an absent fade produce the SAME signature instead of
        # looking like an edit that renders nothing different.
        #
        # loop is OFF unless explicitly set. Making it default-on would change
        # the audio of EDLs written before it existed WITHOUT a new version —
        # so a cached render and a fresh render of the same version would
        # disagree. add_music turns it on for new music instead, where the
        # change is attached to a version the user can see.
        if not m.loop:
            m.loop = None
        if m.offset_s is not None:
            if m.offset_s < 0:
                raise EDLValidationError(
                    f"music[{i}].offset_s {m.offset_s} must be >= 0.")
            m.offset_s = _r(m.offset_s) or None
        span = max(0.0, m.end - m.start)
        for fname in ("fade_in_s", "fade_out_s"):
            fv = getattr(m, fname)
            if fv is None:
                continue
            if fv < 0:
                raise EDLValidationError(
                    f"music[{i}].{fname} {fv} must be >= 0.")
            # A fade longer than half the span would still be rendered (the
            # renderer clamps), but storing the clamped value keeps the EDL
            # honest about what the viewer will actually hear.
            setattr(m, fname, _r(min(fv, span / 2)) or None)

    seen_sfx_ids = set()
    for i, s in enumerate(edl.sfx):
        if not s.id or s.id in seen_sfx_ids:
            raise EDLValidationError(
                f"sfx[{i}].id must be non-empty and unique.")
        seen_sfx_ids.add(s.id)
        if not s.storage_key:
            raise EDLValidationError(f"sfx[{i}].storage_key is empty.")
        s.at = _r(s.at)
        # A point event, so NOT _check_span: that helper requires a two-ended
        # span of at least MIN_SPAN_S and would reject every sfx ever written
        # with "span [x, x] is shorter than 0.05s".
        if s.at < 0:
            raise EDLValidationError(
                f"sfx[{i}].at {s.at} is negative. Times are seconds from 0.")
        # Bounded by the FINAL program (incl. inserts), like voiceover and
        # music — not by the source duration, which a heavily-cut edit is far
        # shorter than. An sfx past the end renders to nothing while the EDL
        # goes on claiming it exists.
        if s.at > max(0.0, prog_dur - 0.05):
            raise EDLValidationError(
                f"sfx[{i}].at {s.at} is past the end of the program "
                f"({round(prog_dur, 2)}s).")
        if not (GAIN_MIN_DB <= s.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"sfx[{i}].gain_db {s.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")
    # Canonical order, so re-emitting the same set of sounds in a different
    # order is not a new signature (and therefore not a pointless re-render).
    edl.sfx.sort(key=lambda x: (x.at, x.id))

    for i, v in enumerate(edl.volume):
        v.start, v.end = _r(v.start), _r(v.end)
        _check_span(f"volume[{i}]", v.start, v.end, duration)
        # BELOW THE FLOOR MEANS SILENCE, NOT AN ERROR (round 61).
        #
        # "remove the sound of the video" is close to the simplest request this
        # product gets, and the obvious way to express it is a very negative
        # gain. A real turn on 2026-07-30 asked for -100 dB and got
        # "volume[0].gain_db -100.0 outside [-60.0, 12.0]" — a rejection, for
        # asking for MORE of exactly the thing the parameter does.
        #
        # GAIN_MIN_DB is already inaudible (-60 dB is 0.1% amplitude), so there
        # is no difference to hear between it and -100: clamping delivers what
        # was asked for. Going OVER the top is still refused, because too loud
        # is a real defect and silently capping it would hide a clipped mix.
        if v.gain_db < GAIN_MIN_DB:
            v.gain_db = GAIN_MIN_DB
        elif v.gain_db > GAIN_MAX_DB:
            raise EDLValidationError(
                f"volume[{i}].gain_db {v.gain_db} is above the "
                f"{GAIN_MAX_DB} dB ceiling.")

    # Overlays: program-time windows, keyframeable position, clamped scale.
    seen_ov = set()
    for i, ov in enumerate(edl.overlays):
        if not ov.id or ov.id in seen_ov:
            raise EDLValidationError(
                f"overlays[{i}].id must be non-empty and unique.")
        seen_ov.add(ov.id)
        if not ov.asset_key:
            raise EDLValidationError(f"overlays[{i}].asset_key is empty.")
        ov.start = _r(ov.start)
        ov.duration_s = _r(ov.duration_s)
        if ov.start < 0 or ov.start > max(0.0, prog_dur - 0.1):
            raise EDLValidationError(
                f"overlays[{i}].start {ov.start} outside the program "
                f"(0 to {round(prog_dur, 2)}s).")
        if not (0.2 <= ov.duration_s <= max(0.2, prog_dur - ov.start + 0.01)):
            raise EDLValidationError(
                f"overlays[{i}].duration_s {ov.duration_s} must be 0.2s to "
                f"the end of the program ({round(prog_dur - ov.start, 2)}s).")
        ov.x = _norm_anim(ov.x, f"overlays[{i}].x", -0.5, 1.5,
                          max_t=ov.duration_s)
        ov.y = _norm_anim(ov.y, f"overlays[{i}].y", -0.5, 1.5,
                          max_t=ov.duration_s)
        ov.scale = round(min(max(float(ov.scale), OVERLAY_SCALE_MIN),
                             OVERLAY_SCALE_MAX), 3)
        if ov.opacity is not None:
            ov.opacity = round(min(max(float(ov.opacity), 0.05), 1.0), 3)
            if ov.opacity >= 0.999:
                ov.opacity = None       # fully opaque = the default
        if ov.rotation is not None:
            ov.rotation = round(float(ov.rotation) % 360.0, 1) or None
        if ov.source_start_s is not None:
            ov.source_start_s = _r(ov.source_start_s)
            if ov.source_start_s < 0:
                raise EDLValidationError(
                    f"overlays[{i}].source_start_s must be >= 0.")
            if ov.kind == "image" or ov.source_start_s == 0.0:
                ov.source_start_s = None
        if ov.screen is not None:
            _check_screen_lock(ov.screen, f"overlays[{i}].screen",
                               ov.duration_s)
    edl.overlays.sort(key=lambda o: (o.start, o.id))

    # Text overlays: program-time windows, template-driven geometry.
    seen_tx = set()
    for i, tx in enumerate(edl.texts):
        if not tx.id or tx.id in seen_tx:
            raise EDLValidationError(
                f"texts[{i}].id must be non-empty and unique.")
        seen_tx.add(tx.id)
        if not (tx.text or "").strip():
            raise EDLValidationError(f"texts[{i}].text is empty.")
        tx.text = tx.text.strip()[:200]
        tx.start, tx.end = _r(tx.start), _r(tx.end)
        _check_span(f"texts[{i}]", tx.start, tx.end, prog_dur, min_len=0.3)
        for fname in ("x", "y"):
            fv = getattr(tx, fname)
            if fv is not None:
                setattr(tx, fname, round(min(max(float(fv), 0.0), 1.0), 3))
        if tx.size_scale is not None:
            tx.size_scale = round(min(max(float(tx.size_scale), 0.4), 3.0), 3)
            if abs(tx.size_scale - 1.0) < 1e-6:
                tx.size_scale = None
        for cname in ("color", "accent_color"):
            cv = getattr(tx, cname)
            if cv is not None:
                cv = cv.strip()
                if not HEX_COLOR.match(cv):
                    raise EDLValidationError(
                        f"texts[{i}].{cname} '{cv}' must be #RRGGBB hex.")
                setattr(tx, cname, cv.upper())
        if tx.behind is not None:
            b = tx.behind
            if not b.asset_key:
                raise EDLValidationError(
                    f"texts[{i}].behind.asset_key is empty — a text can only "
                    "sit behind the subject when a measured mask exists for "
                    "it (add_text_behind writes one).")
            if not b.fp:
                raise EDLValidationError(f"texts[{i}].behind.fp is empty.")
            b.src_start, b.src_end = _r(b.src_start), _r(b.src_end)
            # SOURCE span, so it is bounded by the video and NOT by prog_dur.
            _check_span(f"texts[{i}].behind", b.src_start, b.src_end,
                        duration, min_len=0.2)
            if b.fps is not None:
                b.fps = round(min(max(float(b.fps), 1.0), 120.0), 3)
            if b.coverage is not None:
                b.coverage = round(min(max(float(b.coverage), 0.0), 1.0), 4)
            if canvas_prog:
                # No main video means no shot to measure a subject out of, and
                # nothing the mask's source seconds could refer to.
                raise EDLValidationError(
                    f"texts[{i}].behind needs a main video — a clip/image "
                    "canvas program has no source footage to cut a subject "
                    "out of.")
    edl.texts.sort(key=lambda t: (t.start, t.id))

    # Caption mutes: PROGRAM-time windows, same clock as texts/stylize. Sorted
    # and merged, so overlapping asks collapse instead of stacking duplicates
    # (the renderer only cares whether a caption falls inside ANY window).
    if edl.caption_mutes:
        spans = []
        for i, mu in enumerate(edl.caption_mutes):
            if len(mu) != 2:
                raise EDLValidationError(
                    f"caption_mutes[{i}] must be a [start, end] pair.")
            s, e = _r(mu[0]), _r(mu[1])
            _check_span(f"caption_mutes[{i}]", s, e, prog_dur, min_len=0.05)
            spans.append([s, e])
        spans.sort()
        merged = [spans[0]]
        for s, e in spans[1:]:
            if s <= merged[-1][1] + 0.001:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        edl.caption_mutes = merged

    if edl.master is not None and edl.master.loudness is None:
        edl.master = None       # empty master is the absence of mastering

    if edl.effects is not None:
        fx = edl.effects
        seen_z = set()
        for i, z in enumerate(fx.zooms):
            if not z.id or z.id in seen_z:
                raise EDLValidationError(
                    f"effects.zooms[{i}].id must be non-empty and unique.")
            seen_z.add(z.id)
            z.start, z.end = _r(z.start), _r(z.end)
            # zooms live in the FINAL program timeline (incl. inserts)
            _check_span(f"effects.zooms[{i}]", z.start, z.end, prog_dur,
                        min_len=0.2)
            z.strength = round(min(max(float(z.strength), ZOOM_STRENGTH_MIN),
                                   ZOOM_STRENGTH_MAX), 2)
            if z.mode == "punch":
                z.mode = None       # the default — keep signatures canonical
            # Zoom target: fractions of the output frame; None (or an
            # explicit center) renders the legacy center zoom.
            for cname in ("cx", "cy"):
                cv = getattr(z, cname)
                if cv is not None:
                    cv = round(min(max(float(cv), 0.0), 1.0), 3)
                    setattr(z, cname, None if abs(cv - 0.5) < 1e-6 else cv)
            # Round 45: the follow path. A path on any other mode would be
            # carried in the EDL and silently ignored by the renderer, which
            # is worse than rejecting it — the agent would believe it had
            # placed a move that never renders.
            if z.mode in ("follow", "path"):
                pts = z.path or []
                if len(pts) < 2:
                    raise EDLValidationError(
                        f"effects.zooms[{i}]: mode '{z.mode}' needs a path of "
                        "at least 2 points.")
                if len(pts) > ZOOM_PATH_MAX_POINTS:
                    raise EDLValidationError(
                        f"effects.zooms[{i}]: a travelling path is limited to "
                        f"{ZOOM_PATH_MAX_POINTS} points.")
                last_f = None
                for j, pt in enumerate(pts):
                    pt.f = round(min(max(float(pt.f), 0.0), 1.0), 4)
                    pt.cx = round(min(max(float(pt.cx), 0.0), 1.0), 3)
                    pt.cy = round(min(max(float(pt.cy), 0.0), 1.0), 3)
                    if pt.s is not None:
                        pt.s = round(min(max(float(pt.s), 0.0),
                                         ZOOM_STRENGTH_MAX), 3)
                    if last_f is not None and pt.f < last_f:
                        raise EDLValidationError(
                            f"effects.zooms[{i}].path[{j}]: points must be "
                            "in ascending f (0 = window start, 1 = end).")
                    last_f = pt.f
                if pts[0].f > 0.0 or pts[-1].f < 1.0:
                    # Clamp rather than reject: a path that does not span its
                    # whole window still renders correctly (clip() holds the
                    # end values), and the ends are what the eye reads.
                    pts[0].f = 0.0
                    pts[-1].f = 1.0
                # A per-waypoint strength on a 'follow' zoom would be carried
                # and ignored (follow holds one strength by definition) — the
                # same silent-no-op this block already refuses for `path`.
                if z.mode == "follow" and any(p.s is not None for p in pts):
                    raise EDLValidationError(
                        f"effects.zooms[{i}]: per-point strength (`s`) needs "
                        "mode 'path'; a 'follow' zoom holds one strength for "
                        "its window.")
            elif z.path:
                raise EDLValidationError(
                    f"effects.zooms[{i}]: `path` only applies to mode "
                    "'follow' or 'path'; this zoom is "
                    f"'{z.mode or 'punch'}'.")
            if z.ease is not None and z.mode != "path":
                raise EDLValidationError(
                    f"effects.zooms[{i}]: `ease` only applies to mode 'path'.")
        fx.zooms.sort(key=lambda z: z.start)
        if fx.transition is not None:
            tr = fx.transition
            tr.duration_s = _r(min(max(float(tr.duration_s),
                                       TRANSITION_MIN_S), TRANSITION_MAX_S))
        for name in ("fade_in_s", "fade_out_s"):
            val = getattr(fx, name)
            if val is not None:
                val = _r(val)
                if val == 0.0:
                    val = None          # 0 clears the fade
                else:
                    val = _r(min(max(val, 0.1), FADE_MAX_S))
                setattr(fx, name, val)
        if fx.stylize is not None:
            seen_st = set()
            for i, st in enumerate(fx.stylize):
                if not st.id or st.id in seen_st:
                    raise EDLValidationError(
                        f"effects.stylize[{i}].id must be non-empty and "
                        "unique.")
                seen_st.add(st.id)
                if (st.start is None) != (st.end is None):
                    raise EDLValidationError(
                        f"effects.stylize[{i}]: pass both start and end "
                        "(program seconds), or neither for the whole video.")
                if st.start is not None:
                    st.start, st.end = _r(st.start), _r(st.end)
                    _check_span(f"effects.stylize[{i}]", st.start, st.end,
                                prog_dur)
                if st.intensity is not None:
                    st.intensity = round(min(max(float(st.intensity),
                                                 0.05), 1.0), 3)
                    if abs(st.intensity - 0.5) < 1e-6:
                        st.intensity = None     # the default — canonical
            fx.stylize.sort(key=lambda s: (s.start or 0.0, s.id))
            if not fx.stylize:
                fx.stylize = None
        if fx.custom is not None:
            seen_cf = set()
            for i, cf in enumerate(fx.custom):
                if not cf.id or cf.id in seen_cf:
                    raise EDLValidationError(
                        f"effects.custom[{i}].id must be non-empty and "
                        "unique.")
                seen_cf.add(cf.id)
                err = custom_chain_error(cf.chain)
                if err:
                    raise EDLValidationError(f"effects.custom[{i}]: {err}")
                cf.chain = cf.chain.strip()
                if (cf.start is None) != (cf.end is None):
                    raise EDLValidationError(
                        f"effects.custom[{i}]: pass both start and end "
                        "(program seconds), or neither for the whole video.")
                if cf.start is not None:
                    cf.start, cf.end = _r(cf.start), _r(cf.end)
                    _check_span(f"effects.custom[{i}]", cf.start, cf.end,
                                prog_dur)
            fx.custom.sort(key=lambda c: (c.start or 0.0, c.id))
            if not fx.custom:
                fx.custom = None
        if fx.grade_custom is not None:
            gc = fx.grade_custom
            _GC_BOUNDS = {"exposure": (-1.0, 1.0), "contrast": (0.5, 1.6),
                          "saturation": (0.0, 2.0), "temperature": (-1.0, 1.0),
                          "tint": (-1.0, 1.0), "shadows": (-1.0, 1.0),
                          "highlights": (-1.0, 1.0)}
            _GC_NEUTRAL = {"exposure": 0.0, "contrast": 1.0,
                           "saturation": 1.0, "temperature": 0.0, "tint": 0.0,
                           "shadows": 0.0, "highlights": 0.0}
            for fname, (lo, hi) in _GC_BOUNDS.items():
                fv = getattr(gc, fname)
                if fv is not None:
                    fv = round(min(max(float(fv), lo), hi), 3)
                    if abs(fv - _GC_NEUTRAL[fname]) < 1e-6:
                        fv = None       # neutral = the absence of the control
                    setattr(gc, fname, fv)
            if all(getattr(gc, f) is None for f in _GC_BOUNDS):
                fx.grade_custom = None
        if fx.regions is not None:
            seen_r = set()
            for i, rg in enumerate(fx.regions):
                if not rg.id or rg.id in seen_r:
                    raise EDLValidationError(
                        f"effects.regions[{i}].id must be non-empty and "
                        "unique.")
                seen_r.add(rg.id)
                # clamp the rectangle into the frame instead of rejecting —
                # the agent estimates corners visually and small overshoots
                # are always safe to trim
                rg.x = round(min(max(float(rg.x), 0.0), 1.0 - REGION_MIN_FRAC), 3)
                rg.y = round(min(max(float(rg.y), 0.0), 1.0 - REGION_MIN_FRAC), 3)
                rg.w = round(min(max(float(rg.w), 0.0), 1.0 - rg.x), 3)
                rg.h = round(min(max(float(rg.h), 0.0), 1.0 - rg.y), 3)
                if rg.w < REGION_MIN_FRAC or rg.h < REGION_MIN_FRAC:
                    raise EDLValidationError(
                        f"effects.regions[{i}]: the rectangle is too small "
                        "or falls outside the frame — x/y/w/h are fractions "
                        "of the frame (0-1), w and h at least 0.01.")
                if (rg.start is None) != (rg.end is None):
                    raise EDLValidationError(
                        f"effects.regions[{i}]: pass both start and end "
                        "(program seconds), or neither for the whole video.")
                if rg.start is not None:
                    rg.start, rg.end = _r(rg.start), _r(rg.end)
                    _check_span(f"effects.regions[{i}]", rg.start, rg.end,
                                prog_dur)
            if not fx.regions:
                fx.regions = None       # [] is the absence of regions
        if fx.screen_frame is not None:
            sf = fx.screen_frame
            sf.inset = round(min(max(float(sf.inset), SCREEN_FRAME_INSET_MIN),
                                 SCREEN_FRAME_INSET_MAX), 3)
            sf.radius = round(min(max(float(sf.radius), 0.0),
                                  SCREEN_FRAME_RADIUS_MAX), 3)
            sf.shadow = round(min(max(float(sf.shadow), 0.0), 1.0), 3)
            for cname in ("background", "background2"):
                cv = getattr(sf, cname)
                if cv is None:
                    continue
                cv = str(cv).strip().upper()
                if not HEX_COLOR.match(cv):
                    raise EDLValidationError(
                        f"effects.screen_frame.{cname} must be #RRGGBB hex.")
                setattr(sf, cname, cv)
        if fx.frame_shifts is not None:
            seen_fs = set()
            for i, fsh in enumerate(fx.frame_shifts):
                if not fsh.id or fsh.id in seen_fs:
                    raise EDLValidationError(
                        f"effects.frame_shifts[{i}].id must be non-empty and "
                        "unique.")
                seen_fs.add(fsh.id)
                fsh.at = _r(min(max(float(fsh.at), 0.0), max(0.0, prog_dur)))
                fsh.duration_s = _r(min(max(float(fsh.duration_s),
                                            FRAME_SHIFT_MIN_S),
                                        FRAME_SHIFT_MAX_S))
                fsh.color = str(fsh.color).strip().upper()
                if not HEX_COLOR.match(fsh.color):
                    raise EDLValidationError(
                        f"effects.frame_shifts[{i}].color must be #RRGGBB "
                        "hex.")
            fx.frame_shifts.sort(key=lambda s: (s.at, s.id))
            # Two shifts inside one another's morph would have the bars
            # animating to two places at once; the second would win per frame
            # in an order nobody can predict from the EDL.
            for a, b in zip(fx.frame_shifts, fx.frame_shifts[1:]):
                if b.at < a.at + a.duration_s - 1e-6:
                    raise EDLValidationError(
                        f"effects.frame_shifts[{b.id}] starts at {b.at}s, "
                        f"inside the {a.duration_s}s morph that "
                        f"{a.id} begins at {a.at}s. Aspect changes cannot "
                        "overlap — move it later or shorten the first.")
            if not fx.frame_shifts:
                fx.frame_shifts = None
        # all-empty effects is the absence of effects — normalize so old
        # EDLs and cleared-effects EDLs compare identical.
        if fx.grade is None and not fx.zooms and fx.fade_in_s is None \
                and fx.fade_out_s is None and fx.transition is None \
                and fx.regions is None and fx.stylize is None \
                and fx.grade_custom is None and fx.screen_frame is None \
                and fx.frame_shifts is None and fx.custom is None:
            edl.effects = None

    if edl.source_clean is not None:
        sc = edl.source_clean
        if not sc.asset_key or not sc.fp:
            raise EDLValidationError(
                "source_clean needs both asset_key and fp — an EDL that "
                "points at a repainted source without saying WHICH repaint "
                "would render whatever file happened to be there.")
        seen_c = set()
        for i, cr in enumerate(sc.regions):
            if not cr.id or cr.id in seen_c:
                raise EDLValidationError(
                    f"source_clean.regions[{i}].id must be non-empty and "
                    "unique.")
            seen_c.add(cr.id)
            cr.x = round(min(max(float(cr.x), 0.0), 1.0 - REGION_MIN_FRAC), 3)
            cr.y = round(min(max(float(cr.y), 0.0), 1.0 - REGION_MIN_FRAC), 3)
            cr.w = round(min(max(float(cr.w), 0.0), 1.0 - cr.x), 3)
            cr.h = round(min(max(float(cr.h), 0.0), 1.0 - cr.y), 3)
            if cr.w < REGION_MIN_FRAC or cr.h < REGION_MIN_FRAC:
                raise EDLValidationError(
                    f"source_clean.regions[{i}]: the rectangle is too small "
                    "or falls outside the frame — x/y/w/h are fractions of "
                    "the frame (0-1), w and h at least 0.01.")
            if (cr.start is None) != (cr.end is None):
                raise EDLValidationError(
                    f"source_clean.regions[{i}]: pass both start and end "
                    "(SOURCE seconds), or neither for the whole video.")
            if cr.start is not None:
                # SOURCE time, not program time: the repaint happens on the
                # source file before a single cut is applied, so its window
                # cannot be checked against the program duration.
                cr.start, cr.end = _r(cr.start), _r(cr.end)
                if cr.end <= cr.start:
                    raise EDLValidationError(
                        f"source_clean.regions[{i}]: end must be after start.")
        if sc.cursor is not None:
            cu = sc.cursor
            cu.scale = round(min(max(float(cu.scale), CURSOR_SCALE_MIN),
                                 CURSOR_SCALE_MAX), 2)
            cu.smoothing = round(min(max(float(cu.smoothing), 0.0), 1.0), 3)
            times = sorted({_r(max(0.0, float(t)))
                            for t in (cu.click_times or [])})
            if len(times) > CURSOR_MAX_CLICKS:
                raise EDLValidationError(
                    f"source_clean.cursor: at most {CURSOR_MAX_CLICKS} click "
                    f"times ({len(times)} given).")
            cu.click_times = times
            if cu.found_frac is not None:
                cu.found_frac = round(min(max(float(cu.found_frac), 0.0),
                                          1.0), 3)
        if not sc.regions and sc.cursor is None:
            # A derived source with nothing deriving it is the absence of one.
            # (The tools clear the whole field, but the UI ops write EDLs too.)
            raise EDLValidationError(
                "source_clean has neither regions nor a cursor pass — clear "
                "the field instead of pointing at a derivation that says "
                "nothing was done.")

    seen_p = set()
    for i, pt in enumerate(edl.patches or []):
        if not pt.id or pt.id in seen_p:
            raise EDLValidationError(
                f"patches[{i}].id must be non-empty and unique.")
        seen_p.add(pt.id)
        if not pt.asset_key or not pt.fp:
            raise EDLValidationError(
                f"patches[{i}] needs asset_key and fp — a patch without its "
                "clip and fingerprint would overlay whatever file happened "
                "to be there.")
        pt.src_start, pt.src_end = _r(max(0.0, float(pt.src_start))), \
            _r(float(pt.src_end))
        if pt.src_end <= pt.src_start:
            raise EDLValidationError(
                f"patches[{i}]: src_end must be after src_start.")
        if not pt.regions:
            raise EDLValidationError(
                f"patches[{i}] carries no regions — the export could never "
                "rebuild its full-res twin. Remove the patch instead.")

    return edl


def _sig_canon(v):
    # Nested None-valued keys are dropped too, so items written before an
    # optional field existed (e.g. music without 'id') compare equal to
    # re-validated dumps that carry the field as None.
    if isinstance(v, dict):
        return {k: _sig_canon(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_sig_canon(x) for x in v]
    return v


def edl_signature(edl_dict):
    """Canonical string form of an EDL for byte-identity comparison (no-op
    write detection). Assumes the dict is already validate_edl-normalized.
    Keys with empty values are dropped so EDLs written before a field existed
    (no 'frame'/'inserts' key) compare equal to fresh dumps that carry the
    field's empty default."""
    canon = {k: _sig_canon(v) for k, v in edl_dict.items()
             if v not in (None, [], {})}
    return json.dumps(canon, sort_keys=True, separators=(",", ":"))


def _style_desc(style):
    if not style:
        return ""
    s = style if isinstance(style, dict) else style.model_dump()
    bits = []
    if s.get("preset") and s["preset"] != "classic":
        bits.append(f"preset {s['preset']}")
    if s.get("color") and s["color"] != "#FFFFFF":
        bits.append(s["color"])
    if s.get("size") and s["size"] != "m":
        bits.append(f"size {s['size']}")
    if s.get("size_scale"):
        bits.append(f"scale {s['size_scale']}x")
    if s.get("position") and s["position"] != "bottom":
        bits.append(s["position"])
    if s.get("uppercase") is not None:
        bits.append("uppercase" if s["uppercase"] else "mixed-case")
    if s.get("dynamic"):
        bits.append("dynamic")
    if s.get("animation"):
        bits.append(f"anim {s['animation']}")
    return f" ({', '.join(bits)})" if bits else ""


def describe_edl(edl_dict, duration=None):
    """One-line human summary used in diffs and activity messages."""
    edl = EDL.model_validate(edl_dict)
    if not edl.keep and edl.canvas is not None:
        # Canvas program (no main video): the program IS the inserts on the
        # canvas, so "0 segments kept" would misdescribe it to the agent.
        n_ins = len(edl.inserts)
        parts = [f"canvas {edl.canvas.width}x{edl.canvas.height}",
                 f"{n_ins} clip{'s' if n_ins != 1 else ''} "
                 f"({round(program_duration(edl_dict), 1)}s)"]
    else:
        parts = [f"{len(edl.keep)} segment{'s' if len(edl.keep) != 1 else ''}",
                 f"{output_duration(edl.keep)}s kept"]
        if duration:
            parts[-1] += f" of {round(duration, 1)}s"
    if isinstance(edl.captions, CaptionsFromTranscript):
        d = "captions: transcript"
        if edl.captions.max_words_per_caption:
            d += f" <= {edl.captions.max_words_per_caption} words"
        if edl.captions.emphasis_words:
            d += f", {len(edl.captions.emphasis_words)} emphasis words"
        parts.append(d + _style_desc(edl.captions.style))
    elif isinstance(edl.captions, list):
        parts.append(f"captions: {len(edl.captions)} manual")
    if edl.frame:
        parts.append(f"frame {edl.frame.ratio} ({edl.frame.mode})")
    if edl.inserts:
        parts.append(f"inserts x{len(edl.inserts)} "
                     f"(+{round(sum(i.duration_s for i in edl.inserts), 1)}s)")
    if edl.voiceover:
        parts.append(f"voiceover x{len(edl.voiceover)}")
    if edl.music:
        # Spell out the fit, not just the count. This string is the diff the
        # agent reads back and paraphrases to the user, so a fit-only change
        # (loop / offset / fade / level) that renders differently must LOOK
        # different here — otherwise the agent sees an identical before and
        # after, and either doubts a change that really happened or reports
        # one it cannot see.
        bits = []
        for m in edl.music:
            f = []
            if m.loop:
                f.append("looped")
            if m.offset_s:
                f.append(f"from {m.offset_s}s in")
            if m.fade_in_s or m.fade_out_s:
                f.append("faded")
            if m.gain_db != -18.0:
                f.append(f"{m.gain_db:g}dB")
            if not m.duck:
                f.append("no duck")
            elif m.duck_mode == "smooth":
                f.append("smooth duck")
            bits.append("/".join(f) or "plain")
        parts.append(f"music x{len(edl.music)} ({', '.join(bits)})")
    if edl.sfx:
        # Per-item, for the same reason as music above: "sfx x3" is identical
        # before and after moving one of them, so the agent would read its own
        # successful edit as a no-op.
        bits = []
        for s in edl.sfx:
            key = s.storage_key.split(":")[-1].split("/")[-1]
            g = f" {s.gain_db:+g}dB" if s.gain_db else ""
            bits.append(f"{key}@{s.at:g}s{g}")
        parts.append(f"sfx x{len(edl.sfx)} ({', '.join(bits)})")
    if edl.volume:
        parts.append(f"volume x{len(edl.volume)}")
    if edl.speed:
        # Spelled out per span: a speed change that renders differently must
        # LOOK different in this diff, or the agent can't see its own edit.
        bits = [f"{sp.factor:g}x@{sp.start:g}-{sp.end:g}s"
                for sp in edl.speed]
        parts.append(f"speed x{len(edl.speed)} ({', '.join(bits)})")
    if edl.overlays:
        bits = []
        for ov in edl.overlays:
            name = ov.asset_key.split("/")[-1]
            anim = "*" if (is_animated(ov.x) or is_animated(ov.y)) else ""
            if ov.screen is not None:
                # A pinned overlay's scale means nothing — say what it IS, so
                # the agent reading its own diff can tell a takeover from a PIP.
                bits.append(f"{name}@{ov.start:g}s screen-takeover "
                            f"{ov.duration_s:g}s")
            else:
                bits.append(f"{name}@{ov.start:g}s {ov.scale:g}w{anim}")
        parts.append(f"overlays x{len(edl.overlays)} ({', '.join(bits)})")
    if edl.texts:
        bits = [f"{tx.template} \"{tx.text[:24]}\"@{tx.start:g}-{tx.end:g}s"
                for tx in edl.texts]
        parts.append(f"text x{len(edl.texts)} ({', '.join(bits)})")
    if edl.caption_mutes:
        bits = [f"{s:g}-{e:g}s" for s, e in edl.caption_mutes]
        parts.append(f"captions muted ({', '.join(bits)})")
    if edl.effects:
        fx = edl.effects
        bits = []
        if fx.grade:
            bits.append(f"grade {fx.grade}")
        if fx.grade_custom:
            gc = fx.grade_custom
            axes = [f"{n[:4]} {getattr(gc, n):+g}" for n in
                    ("exposure", "contrast", "saturation", "temperature",
                     "tint", "shadows", "highlights")
                    if getattr(gc, n) is not None]
            bits.append("custom grade (" + ", ".join(axes) + ")")
        if fx.zooms:
            tgt = sum(1 for z in fx.zooms
                      if z.cx is not None or z.cy is not None)
            travel = sum(1 for z in fx.zooms if z.mode in ("follow", "path"))
            bits.append(f"zoom x{len(fx.zooms)}"
                        + (f" ({tgt} targeted)" if tgt else "")
                        + (f" ({travel} travelling)" if travel else ""))
        fades = [n for n, v in (("in", fx.fade_in_s),
                                ("out", fx.fade_out_s)) if v]
        if fades:
            bits.append("fade " + "/".join(fades))
        if fx.transition:
            bits.append(f"transitions {fx.transition.style} "
                        f"{fx.transition.duration_s}s"
                        + (" at every cut"
                           if fx.transition.scope == "every_cut"
                           else " at scene changes"))
        if fx.regions:
            bits.append("censor region x" + str(len(fx.regions)))
        if fx.stylize:
            names = [s.kind + (f"@{s.start:g}-{s.end:g}s"
                               if s.start is not None else "")
                     for s in fx.stylize]
            bits.append("stylize " + "+".join(names))
        if fx.custom:
            names = [f"'{(c.label or c.chain[:24]).strip()}' [{c.id}]"
                     + (f"@{c.start:g}-{c.end:g}s"
                        if c.start is not None else "")
                     for c in fx.custom]
            bits.append("custom filter " + ", ".join(names))
        if fx.screen_frame:
            sf = fx.screen_frame
            bits.append(
                f"floating frame (inset {int(sf.inset * 100)}%, radius "
                f"{sf.radius:g}, on {sf.background}"
                + (f"->{sf.background2} {sf.direction}" if sf.background2
                   else "") + ")")
        if fx.frame_shifts:
            bits.append("aspect shifts: " + ", ".join(
                f"{s.ratio}@{s.at:g}s over {s.duration_s:g}s"
                for s in fx.frame_shifts))
        parts.append(", ".join(bits))
    if edl.master and edl.master.loudness:
        parts.append(f"mastered ({edl.master.loudness} loudness)")
    if edl.source_clean and edl.source_clean.cursor:
        cu = edl.source_clean.cursor
        parts.append(
            f"cursor enhanced ({cu.scale:g}x"
            + (f", {len(cu.click_times)} click ripple(s)"
               if cu.click_highlight and cu.click_times else "")
            + (f", found in {int(cu.found_frac * 100)}% of frames"
               if cu.found_frac is not None else "") + ")")
    if edl.source_clean and edl.source_clean.regions:
        # Named as ERASED, never as "censored"/"blurred": these pixels are
        # repainted in the file the render reads, and describing that as a
        # cover would put a lie in every EDL summary the agent quotes back.
        erased = ", ".join(
            f"{(r.kind or 'region')} [{r.id}]" for r in edl.source_clean.regions)
        parts.append(f"erased from the source: {erased}")
    if edl.patches:
        # Round 92 window patches — same honesty rule as above: the pixels
        # ARE repainted wherever these windows play.
        pat = ", ".join(
            f"{(r.kind or 'region')} [{r.id}]"
            + (f" @{p.src_start:g}-{p.src_end:g}s")
            for p in edl.patches for r in p.regions)
        parts.append(f"erased from the source (windowed): {pat}")
    return ", ".join(parts)


# ------------------------------------------------------------------ #
#  Index                                                               #
# ------------------------------------------------------------------ #

# Tokens tagged `filler` on the way out of the transcriber (round 69).
#
# Deliberately TIGHTER than agent_tools.FILLER_WORDS_DEFAULT, which is what
# remove_filler_words cuts when the user asks. The two lists answer different
# questions and the cost of a false positive is not the same: removing a word
# is an explicit instruction the user can see and undo, while this tag decides
# what is silently withheld from burned-in captions. "ah" and "er" are real
# words in real languages, so they are removable-on-request but never tagged.
FILLER_TOKENS = frozenset((
    "um", "umm", "ummm", "uh", "uhh", "uhhh", "uhm", "erm", "mmm",
))


def is_filler_token(token):
    """True when this transcript token is a hesitation sound, not a word."""
    return re.sub(r"[^a-z]", "", str(token or "").lower()) in FILLER_TOKENS


class Word(BaseModel):
    w: str
    t0: float
    t1: float
    # Speaker index from ASR diarization (round 69), 0-based, or None when
    # the engine does not diarize (whisper) — NEVER 0 as a stand-in, because
    # "everything is speaker 0" and "nobody knows who spoke" are different
    # facts and only one of them can be acted on.
    speaker: Optional[int] = None
    # Hesitation sound rather than a word. In the index so remove_filler_words
    # has real spans to cut; excluded from burned-in caption text so the
    # default look is not "So, um, uh, yeah".
    filler: bool = False


def clamp_word_times(words, duration):
    """Clamp transcription word timings into [0, duration].

    ASR on music-heavy audio hallucinates timings past the end of the file —
    a real 16.65s upload produced one 'word' spanning 15.36-34.72s. Captions
    built from such a word can never render (the program ends first), cuts
    snapped to it point at footage that doesn't exist, and the transcript
    panel shows a timestamp the player can't seek to. Words starting at or
    beyond the end are dropped; ends are clamped. Accepts Word models or
    plain {w,t0,t1} dicts and returns the same shape it was given."""
    if not duration or duration <= 0:
        return list(words)
    out = []
    for w in words:
        is_model = hasattr(w, "t0")
        t0 = float(w.t0 if is_model else w["t0"])
        t1 = float(w.t1 if is_model else w["t1"])
        if t0 >= duration - 0.01:
            continue
        t0 = max(0.0, t0)
        t1 = min(t1, float(duration))
        if t1 <= t0:
            t1 = min(float(duration), t0 + 0.05)
        if is_model:
            w = w.model_copy(update={"t0": round(t0, 3), "t1": round(t1, 3)})
        else:
            w = dict(w, t0=round(t0, 3), t1=round(t1, 3))
        out.append(w)
    return out


class Sentence(BaseModel):
    id: str          # "s1", "s2", ...
    text: str
    t0: float
    t1: float
    wi0: int         # index into words[]
    wi1: int         # inclusive
    # The speaker of this sentence, or None when undiarized. group_sentences
    # breaks on a speaker change, so a sentence never spans two people.
    speaker: Optional[int] = None


class ShotCaption(BaseModel):
    setting: str = ""
    people: str = ""
    action: str = ""
    on_screen_text: str = ""
    # Round 36: does this shot show SUBTITLE-style caption text burned into
    # the footage (spoken-word captions, not signs/UI)? Detected by the same
    # vision pass that writes the fields above. Default False so indexes
    # from before this field load unchanged — absence means "not checked",
    # which the summary treats the same as "no".
    subtitles: bool = False


class Shot(BaseModel):
    id: int
    start: float
    end: float
    thumb_key: Optional[str] = None
    # DERIVED, since round 69: the caption of the moment nearest this shot's
    # midpoint. Every existing reader of this field is unchanged; what changed
    # is that it is no longer the ONLY thing the index knows about the picture.
    caption: Optional[ShotCaption] = None


class Moment(BaseModel):
    """What is on screen at one sampled instant (round 69).

    Sampled on a clock, not per shot — see worker/visual.py. `shot` is the
    shot this instant falls inside, kept so a moment can always be related
    back to the scene structure the renderer and transitions care about.
    """
    t: float
    shot: int
    caption: Optional[ShotCaption] = None


class VideoInfo(BaseModel):
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool
    vfr_normalized: bool = False


class VideoIndex(BaseModel):
    version: int = 1
    video: VideoInfo
    shots: List[Shot] = Field(default_factory=list)
    # Time-sampled visual track (round 69). Empty on indexes built before it,
    # which read back exactly as they did — shots still carry their captions.
    moments: List[Moment] = Field(default_factory=list)
    words: List[Word] = Field(default_factory=list)
    sentences: List[Sentence] = Field(default_factory=list)
    silences: List[List[float]] = Field(default_factory=list)
    sheet_keys: List[str] = Field(default_factory=list)
    # Filmstrip tiles (v10): storage keys of the labeled 2x2 frame grids the
    # agent reads directly, in time order, plus the sampling step used. Empty
    # on pre-v10 rows (which read back unchanged).
    tile_keys: List[str] = Field(default_factory=list)
    tile_step_s: Optional[float] = None
    # Distinct speakers the transcriber diarized. 0 means "not diarized"
    # (whisper, or an index built before round 69), never "nobody spoke".
    speakers: int = 0
    # Whisper-detected language code (e.g. "en", "es"). Optional so cached
    # indexes from before this field render unchanged; used for caption
    # font/style decisions and admin analytics.
    language: Optional[str] = None
    # Non-fatal degradations recorded during indexing (scene/silence/vision
    # failures, capped vision sampling). Surfaced in admin so a partially
    # degraded index is visible instead of silently worse.
    warnings: List[str] = Field(default_factory=list)
    # Perception sidecar (round 35): beat grid / energy envelope / speech-
    # stress data from worker/perception.py. Deliberately an opaque dict with
    # its OWN version key ("v": perception.PERCEPTION_VERSION), lazily
    # computed and upserted the first time a tool needs it — NOT part of the
    # indexer's output contract, so shipping or changing it never bumps
    # PIPELINE_VERSION and never triggers a re-index. Declared here so any
    # code path that round-trips an index through this model preserves it.
    perception: Optional[dict] = None
