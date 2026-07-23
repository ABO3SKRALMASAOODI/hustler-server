"""Typed EDL + index schemas.

All timestamps everywhere are SECONDS as floats. The EDL is pure data — the
agent edits it through validated tools, the renderer turns it into ffmpeg
filtergraphs. A TypeScript mirror of the EDL type lives in the frontend repo
at src/types/edl.ts — keep the two in sync.
"""

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
PIPELINE_VERSION = 7

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
        # composed looks (per-line "stack" emission): scale-led hierarchy,
        # tight/overlapping leading, layered text effects
        "stacked", "iridescent", "chrome", "editorial", "fashion", "luxe",
        "impact",
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
    animation: Optional[Literal["fade", "pop", "slide_up", "punch",
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
                               "chrome", "glow", "chroma", "none"]] = None
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

    _style = field_validator("style", mode="before")(_coerce_style)

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


class Frame(BaseModel):
    """Output frame. ratio 'source' keeps the original dimensions; anything
    else is achieved by crop (center-crop + scale), pad (fit + black bars) or
    pad_blur (fit over a blurred scaled copy). Never upscales beyond the
    source's pixel budget — see renderer.frame_dims."""
    ratio: Literal["source", "16:9", "9:16", "1:1", "4:5"] = "source"
    mode: Literal["crop", "pad", "pad_blur"] = "crop"


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
    zooms or pans instead of sitting frozen); Optional for signatures."""
    id: str
    asset_key: str
    kind: Literal["video", "image"]
    at_output_s: float
    duration_s: float
    source_start_s: Optional[float] = None
    motion: Optional[Literal["zoom_in", "zoom_out",
                             "pan_left", "pan_right"]] = None


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
ZOOM_STRENGTH_MIN = 0.05
# Widened from 1.0 (round 35): a 1.5 strength is a 2.5x punch — bold but
# real; the old cap existed because center-only zooms past 2x looked lost,
# and targeted zooms (cx/cy) don't.
ZOOM_STRENGTH_MAX = 1.5
FADE_MAX_S = 10.0


class ZoomItem(BaseModel):
    """A zoom over a FINAL-program time range (output seconds). mode:
    'punch' (default, instant step in/out), 'ease' (smoothly ramps in and
    out inside the window), 'push_in' / 'pull_out' (continuous Ken Burns
    drift across the whole window). Optional so pre-round-9 EDLs keep
    their signatures.

    cx/cy (round 35): the zoom TARGET as fractions of the output frame
    (0,0 = top-left). None = center, which is exactly what every earlier
    zoom rendered — so old EDLs keep both their signatures and their look.
    """
    id: str
    start: float
    end: float
    strength: float = 0.25
    mode: Optional[Literal["punch", "ease", "push_in", "pull_out"]] = None
    cx: Optional[float] = None
    cy: Optional[float] = None


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


class TransitionSpec(BaseModel):
    """A junction effect at EVERY cut/insert boundary. Duration-preserving
    (video animates out/in around each junction; audio is untouched), so no
    timeline math changes anywhere."""
    style: Literal["dip_black", "dip_white", "whip_left", "whip_right",
                   "zoom_punch", "glitch", "flash"]
    duration_s: float = 0.3


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
STYLIZE_KINDS = ("grain", "vignette", "glow", "chromatic", "dream_blur",
                 "vhs", "flash", "shake")


class StylizeItem(BaseModel):
    id: str
    kind: Literal["grain", "vignette", "glow", "chromatic", "dream_blur",
                  "vhs", "flash", "shake"]
    start: Optional[float] = None
    end: Optional[float] = None
    intensity: Optional[float] = None      # None = the kind's default (0.5)


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


class OverlayItem(BaseModel):
    id: str
    asset_key: str
    kind: Literal["video", "image"]
    start: float                    # FINAL-program seconds
    duration_s: float
    x: AnimFloat = 0.5
    y: AnimFloat = 0.5
    scale: float = 0.4
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


# ── Text overlays (round 35): the motion-graphics layer ──────────────────
# Rendered by libass via a SECOND .ass file burned after captions, from the
# parameterized templates in worker/graphics.py — title cards, lower thirds,
# callouts, big numbers, quotes, chapter markers. PROGRAM-anchored.
TEXT_TEMPLATES = ("title", "subtitle", "lower_third", "callout",
                  "big_number", "quote", "chapter")
TEXT_ANIMS = ("fade", "pop", "slide_up", "blur_in", "whip", "rise", "drop",
              "typewriter")
TEXT_FONTS = ("Inter Display Black", "Inter Display ExtraBold",
              "Inter Display Bold", "Anton", "Bebas Neue", "Archivo Black",
              "Poppins Black", "Syne ExtraBold", "Playfair Display Black",
              "Instrument Serif", "DM Serif Display", "Montserrat")


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
                           "DM Serif Display", "Montserrat"]] = None
    entrance: Optional[Literal["fade", "pop", "slide_up", "blur_in", "whip",
                               "rise", "drop", "typewriter"]] = None
    exit: Optional[Literal["fade", "pop", "slide_up", "blur_in", "whip",
                           "rise", "drop"]] = None
    uppercase: Optional[bool] = None
    box: Optional[bool] = None      # backing panel behind the text


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
        # music positions live in the FINAL program timeline (incl. inserts)
        _check_span(f"music[{i}]", m.start, m.end, prog_dur)
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
        if not (GAIN_MIN_DB <= v.gain_db <= GAIN_MAX_DB):
            raise EDLValidationError(
                f"volume[{i}].gain_db {v.gain_db} outside "
                f"[{GAIN_MIN_DB}, {GAIN_MAX_DB}].")

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
    edl.texts.sort(key=lambda t: (t.start, t.id))

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
        if fx.grade_custom is not None:
            gc = fx.grade_custom
            _GC_BOUNDS = {"exposure": (-1.0, 1.0), "contrast": (0.5, 1.6),
                          "saturation": (0.0, 2.0), "temperature": (-1.0, 1.0),
                          "tint": (-1.0, 1.0)}
            _GC_NEUTRAL = {"exposure": 0.0, "contrast": 1.0,
                           "saturation": 1.0, "temperature": 0.0, "tint": 0.0}
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
        # all-empty effects is the absence of effects — normalize so old
        # EDLs and cleared-effects EDLs compare identical.
        if fx.grade is None and not fx.zooms and fx.fade_in_s is None \
                and fx.fade_out_s is None and fx.transition is None \
                and fx.regions is None and fx.stylize is None \
                and fx.grade_custom is None:
            edl.effects = None

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
            bits.append(f"{name}@{ov.start:g}s {ov.scale:g}w{anim}")
        parts.append(f"overlays x{len(edl.overlays)} ({', '.join(bits)})")
    if edl.texts:
        bits = [f"{tx.template} \"{tx.text[:24]}\"@{tx.start:g}-{tx.end:g}s"
                for tx in edl.texts]
        parts.append(f"text x{len(edl.texts)} ({', '.join(bits)})")
    if edl.effects:
        fx = edl.effects
        bits = []
        if fx.grade:
            bits.append(f"grade {fx.grade}")
        if fx.grade_custom:
            gc = fx.grade_custom
            axes = [f"{n[:4]} {getattr(gc, n):+g}" for n in
                    ("exposure", "contrast", "saturation", "temperature",
                     "tint") if getattr(gc, n) is not None]
            bits.append("custom grade (" + ", ".join(axes) + ")")
        if fx.zooms:
            tgt = sum(1 for z in fx.zooms
                      if z.cx is not None or z.cy is not None)
            bits.append(f"zoom x{len(fx.zooms)}"
                        + (f" ({tgt} targeted)" if tgt else ""))
        fades = [n for n, v in (("in", fx.fade_in_s),
                                ("out", fx.fade_out_s)) if v]
        if fades:
            bits.append("fade " + "/".join(fades))
        if fx.transition:
            bits.append(f"transitions {fx.transition.style} "
                        f"{fx.transition.duration_s}s")
        if fx.regions:
            bits.append("censor region x" + str(len(fx.regions)))
        if fx.stylize:
            names = [s.kind + (f"@{s.start:g}-{s.end:g}s"
                               if s.start is not None else "")
                     for s in fx.stylize]
            bits.append("stylize " + "+".join(names))
        parts.append(", ".join(bits))
    if edl.master and edl.master.loudness:
        parts.append(f"mastered ({edl.master.loudness} loudness)")
    return ", ".join(parts)


# ------------------------------------------------------------------ #
#  Index                                                               #
# ------------------------------------------------------------------ #

class Word(BaseModel):
    w: str
    t0: float
    t1: float


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


class ShotCaption(BaseModel):
    setting: str = ""
    people: str = ""
    action: str = ""
    on_screen_text: str = ""


class Shot(BaseModel):
    id: int
    start: float
    end: float
    thumb_key: Optional[str] = None
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
    words: List[Word] = Field(default_factory=list)
    sentences: List[Sentence] = Field(default_factory=list)
    silences: List[List[float]] = Field(default_factory=list)
    sheet_keys: List[str] = Field(default_factory=list)
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
