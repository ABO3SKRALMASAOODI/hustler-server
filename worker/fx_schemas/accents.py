"""Punctuation, strobes and junctions: EDL.fx tags ``accent``, ``strobe``
and ``junction`` (track 1D; worker/devices/INTERFACES_W1.md §3.4).

Pinned by the Wave 1 interface pass. Track 1D owns this file: it may add
optional fields (default None), tighten a ``check``, implement
``flash_times`` and flip ``LIVE``; renaming or removing a pinned field,
kind or style is a round-lead change.

* ``accent``: a 1–N-frame punctuation frame. Its STAGE is its scope:
  ``pre_captions`` changes the picture only (type untouched),
  ``over_type`` inverts the type too (ref 04). Never below the aspect bars,
  the screen-frame plate, the watermark or the end card. The accent plays
  at ``intensity`` over [start, end) (its peak), then — white_hit and
  colour_hit only — falls off over the next ``decay_f`` frames to exactly
  0 (``decay_strengths``: ref 02@13.667's 1-frame peak and 3–5-frame
  decay, measured).
* ``strobe``: a timeline-neutral full-frame cover cycling stills, clip
  frames or 1E variants with 1–8-frame holds; audio and the timeline are
  untouched.
* ``junction``: a duration-preserving cut device at the program cut ``at``
  (the outgoing block borrows d/2 of source past its out-point, the
  incoming d/2 before its in-point; audio untouched). EVERY per-cut style
  lives here — ``TransitionSpec.per_junction`` stays unowned and None in
  Wave 1 (one representation per cut).

``matte_flash`` (a subject fill on the hit) is NOT an accent kind: the verb
writes a ``subject`` item (1B, kind fill) for it.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

import math
from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import (FxColour, FxItemBase, FxRegion, FxSource, live,
               region_check, source_check)

TRACK = "1d"
# Track 1D flips this when accents.py, strobe.py and junctions_v2.py compile
# their types.
LIVE = True

ACCENT_KINDS = ("negative", "white_hit", "colour_hit", "threshold",
                "posterize", "mono", "mirror", "hue_step", "rgb_slice",
                "box_invert")
# xfade built-ins | mask wipes (precomputed gray mask clip + maskedmerge) |
# composites and smears.
XFADE_STYLES = ("dissolve", "fadegrays", "pixelize", "radial", "slices",
                "wipe_left", "wipe_right", "wipe_up", "wipe_down")
MASK_STYLES = ("iris", "columns", "staircase", "box_steps", "bands",
               "letter_window", "matte_cut")
OTHER_STYLES = ("double_exposure", "pixel_stretch", "hit_flash", "whip_up",
                "whip_down", "zoom_blur", "radial_blur", "datamosh")
JUNCTION_STYLES = XFADE_STYLES + MASK_STYLES + OTHER_STYLES

# ── flash reading (flash_safety reads FxItemBase.flash_times) ───────────
# Accent kinds that swing the luminance of what they cover: a FULL-FRAME
# one is a flash in the Harding sense. mono, mirror, hue_step, posterize
# and rgb_slice re-colour or re-arrange the picture without a luminance
# swing; box_invert covers boxes (counted only when they cover >= 25%).
FLASH_KINDS = ("negative", "white_hit", "colour_hit", "threshold")
# A flash smaller than this share of the frame is local (Harding: 25%).
LOCAL_FLASH_SHARE = 0.25
# flash_times() has no program rate: frame-based devices (strobe holds,
# box steps) are read at this nominal rate. Within a frame of the truth at
# 24–60 fps, which is all a one-second flash count needs.
NOMINAL_FPS = 30.0
# white_hit / colour_hit fall-off. Measured on ref 02@13.667 inside its card
# (strength after the 1-frame peak .87, .66, .375, .16, .05, then 0): the
# light lingers a frame and then drains — a gaussian shoulder, not an
# exponential (exp(-k/2) gave .61, .37, .22, .14 and then a visible step to
# 0). decay_strengths is that shoulder for any ``decay_f`` = D, normalised to
# land exactly on 0 the frame after the last decay frame, so a hit never ends
# on a step: frame k (1..D) reads (g(k) - g(D+1)) / (1 - g(D+1)) with
# g(k) = exp(-(2k / (D+1))^2). D = 5 gives .89, .63, .36, .15, .04 (ref 02);
# the ~2-frame half-life the roadmap names ("tau ≈ 2") at every D.
DECAY_TAU_F = 2.0
# Default boxes for box_invert, bands for rgb_slice.
DEFAULT_BOXES = 3
DEFAULT_SLICES = 6


def region_share(region):
    """The share of the frame a region covers, or None when it is not
    known from the item alone (a card, a face, a glyph: anchors solved at
    render). Static and first-key boxes / polygons only."""
    if region is None:
        return 1.0
    shape = region.shape
    if shape in ("rect", "ellipse"):
        if region.box is not None:
            x0, y0, x1, y1 = (float(v) for v in region.box)
        else:
            (x0, y0), (x1, y1) = region.keys[0].points[:2]
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(1.0, x1), min(1.0, y1)
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if shape == "ellipse":
            area *= math.pi / 4.0
    elif shape == "polygon":
        pts = region.points if region.points else region.keys[0].points
        area = 0.0
        for i in range(len(pts)):
            xa, ya = pts[i]
            xb, yb = pts[(i + 1) % len(pts)]
            area += xa * yb - xb * ya
        area = min(1.0, abs(area) / 2.0)
    else:
        return None
    return 1.0 - area if region.invert else area


def decay_strengths(intensity, decay_f):
    """Strength of each decay frame after the peak (frames 1..decay_f): the
    measured ref-02 shoulder above, reaching 0 on frame decay_f + 1."""
    d = int(decay_f or 0)
    if d <= 0:
        return []

    def g(k):
        return math.exp(-(2.0 * k / (d + 1)) ** 2)
    end = g(d + 1)
    return [float(intensity) * (g(k) - end) / (1.0 - end)
            for k in range(1, d + 1)]


class AccentFx(FxItemBase):
    """One punctuation accent over [start, end) (usually 1–2 frames).

    kind      negative | white_hit (1-frame peak, ``decay_f`` frames of
              fall-off, ``decay_strengths``) | colour_hit (``colour``; None
              = the outgoing shot's dominant hue) | threshold | posterize |
              mono | mirror | hue_step | rgb_slice (``bands`` swaprect
              bands) | box_invert (``bands`` seeded boxes, ``seed``)
    region    limit it to a region (a card, a face, a rect ...)
    """
    STAGES: ClassVar[tuple] = ("pre_captions", "over_type")
    COMPILER: ClassVar[str] = "accents:ACCENT"

    type: Literal["accent"] = "accent"
    kind: Literal["negative", "white_hit", "colour_hit", "threshold",
                  "posterize", "mono", "mirror", "hue_step", "rgb_slice",
                  "box_invert"]
    intensity: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    decay_f: int = Field(default=0, ge=0, le=12)
    colour: Optional[FxColour] = None
    bands: Optional[int] = Field(default=None, ge=2, le=32)
    region: Optional[FxRegion] = None

    @model_validator(mode="after")
    def _kind_fields(self):
        k = self.kind
        if self.colour is not None and k != "colour_hit":
            raise ValueError("colour belongs to colour_hit")
        if self.bands is not None and k not in ("rgb_slice", "box_invert"):
            raise ValueError("bands belong to rgb_slice or box_invert")
        if self.decay_f and k not in ("white_hit", "colour_hit"):
            raise ValueError("decay_f belongs to white_hit or colour_hit")
        return self

    def check(self, ctx):
        if self.at is not None:
            raise ValueError(f"fx {self.id}: an accent is placed by start "
                             "and end, not at")
        if self.intensity <= 0.0:
            raise ValueError(f"fx {self.id}: an accent with intensity 0 "
                             "changes nothing")
        region_check(self.region, ctx, f"fx {self.id}")

    def flash_times(self):
        """One flash at ``start`` for a luminance-swinging kind that covers
        a quarter of the frame or more (a card counts: picture cards are
        far larger); [] for re-colouring kinds and local hits."""
        if self.start is None:
            return []
        share = region_share(self.region)
        if share is not None and share < LOCAL_FLASH_SHARE:
            return []
        if self.kind in FLASH_KINDS:
            return [float(self.start)]
        if self.kind == "box_invert" and self.region is None:
            # seeded boxes cover about a third of the frame (accents.py)
            return [float(self.start)]
        return []

    def describe(self):
        scope = "all" if self.stage == "over_type" else "picture"
        bits = [self.kind, f"scope {scope}"]
        if self.region is not None:
            bits.append(f"in {self.region.shape}")
        if self.decay_f:
            bits.append(f"decay {self.decay_f}f")
        if self.intensity < 1.0:
            bits.append(f"at {self.intensity:g}")
        return f"{super().describe()} ({', '.join(bits)})"


class StrobeFrame(BaseModel):
    """One strobe image: a still or clip frame (``source``) held ``hold_f``
    frames, optionally negated."""
    source: FxSource
    hold_f: int = Field(default=2, ge=1, le=8)
    negate: bool = False


def strobe_order(n_images, pattern, seed, steps):
    """The image index shown at each of ``steps`` holds: ``sequence``
    cycles 0..n-1, ``pingpong`` runs 0..n-1..1 back and forth, ``random``
    draws a seeded order (no image twice in a row). Pure and deterministic
    (the compiler and flash_times read the same order)."""
    n = int(n_images)
    if n <= 0 or steps <= 0:
        return []
    if n == 1:
        return [0] * steps
    if pattern == "pingpong":
        cycle = list(range(n)) + list(range(n - 2, 0, -1))
        return [cycle[k % len(cycle)] for k in range(steps)]
    if pattern == "random":
        state = (int(seed or 0) * 2654435761 + 97) & 0xFFFFFFFF
        out, prev = [], None
        for _ in range(steps):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            pick = state % (n - 1) if prev is not None else state % n
            if prev is not None and pick >= prev:
                pick += 1
            out.append(pick)
            prev = pick
        return out
    return [k % n for k in range(steps)]


def strobe_schedule(frames, pattern, seed, n_frames):
    """[(image index, first frame, frames held)] filling ``n_frames``
    window frames: each image held its own ``hold_f`` (the last one cut at
    the window's end)."""
    holds = [int(f.hold_f if hasattr(f, "hold_f") else f["hold_f"])
             for f in frames]
    out, at, k = [], 0, 0
    # enough holds to cover the window even at the shortest hold
    order = strobe_order(len(holds), pattern, seed,
                         max(1, int(n_frames) // max(1, min(holds)) + 2))
    while at < int(n_frames) and k < len(order):
        i = order[k]
        n = min(holds[i], int(n_frames) - at)
        out.append((i, at, n))
        at += n
        k += 1
    return out


class StrobeFx(FxItemBase):
    """A frame-exact strobe over [start, end): ``frames`` cycled in
    ``pattern`` order (``random`` is seeded by ``seed``), each image
    pre-scaled once and registered on the program box (``fit``)."""
    STAGES: ClassVar[tuple] = ("pre_captions", "over_type")
    COMPILER: ClassVar[str] = "strobe:STROBE"

    type: Literal["strobe"] = "strobe"
    frames: List[StrobeFrame] = Field(min_length=1, max_length=64)
    pattern: Literal["sequence", "pingpong", "random"] = "sequence"
    fit: Literal["cover", "contain", "register"] = "cover"

    def check(self, ctx):
        if self.at is not None:
            raise ValueError(f"fx {self.id}: a strobe is placed by start and "
                             "end, not at")
        for i, fr in enumerate(self.frames):
            source_check(fr.source, ctx, f"fx {self.id} frame {i}")
            if fr.source.kind in ("next", "previous"):
                raise ValueError(f"fx {self.id}: a strobe frame is a still, "
                                 "a clip frame, the program or a colour")

    def flash_times(self):
        """Every image change, read at the nominal rate: a strobe IS a
        train of full-frame changes, and flash safety counts each one."""
        if self.start is None or self.end is None:
            return []
        n = max(1, int(round((float(self.end) - float(self.start))
                             * NOMINAL_FPS)))
        sched = strobe_schedule(self.frames, self.pattern, self.seed, n)
        out, prev = [], None
        for i, f0, _n in sched:
            if i != prev:
                out.append(round(float(self.start) + f0 / NOMINAL_FPS, 4))
            prev = i
        return out

    def describe(self):
        holds = sorted({f.hold_f for f in self.frames})
        hold = "/".join(str(h) for h in holds)
        return (f"{super().describe()} ({len(self.frames)} images, "
                f"{hold}-frame holds, {self.pattern})")


class JunctionOrigin(BaseModel):
    """Where an iris / letter window / zoom blur starts.

    kind  centre | face (``ref`` face index, default the largest) |
          glyph (``ref`` = motion item id) | point (``point`` [x, y]
          frame fractions)
    """
    kind: Literal["centre", "face", "glyph", "point"] = "centre"
    ref: Optional[str] = Field(default=None, max_length=80)
    point: Optional[List[float]] = Field(default=None, min_length=2,
                                         max_length=2)

    @model_validator(mode="after")
    def _fields(self):
        if self.kind == "point":
            if self.point is None or not all(0.0 <= float(v) <= 1.0
                                             for v in self.point):
                raise ValueError("a point origin is [x, y] frame fractions")
        elif self.point is not None:
            raise ValueError("point belongs to a point origin")
        if self.kind == "glyph" and not self.ref:
            raise ValueError("a glyph origin names its motion item by ref")
        if self.kind in ("centre", "point") and self.ref is not None:
            raise ValueError(f"a {self.kind} origin takes no ref")
        return self


# Styles an origin steers, and styles a count / direction shapes.
ORIGIN_STYLES = ("iris", "box_steps", "letter_window", "zoom_blur",
                 "radial_blur", "pixel_stretch")
COUNT_STYLES = ("columns", "bands", "box_steps", "slices")
DIRECTION_STYLES = ("columns", "bands", "staircase", "slices", "wipe_left",
                    "wipe_right", "wipe_up", "wipe_down", "whip_up",
                    "whip_down")
# box_steps: default steps; each step lands with a white fill in its box.
DEFAULT_BOX_STEPS = 3


def box_step_shares(count):
    """The frame share each box_steps step covers, smallest first (the last
    step is the whole frame): ref 14@15.30–16.07 grows ≈ 22% → 55% → 85%
    → full."""
    n = max(2, int(count or DEFAULT_BOX_STEPS))
    return [round(0.22 + (0.85 - 0.22) * k / max(1, n - 1), 4)
            for k in range(n)] + [1.0]


class JunctionFx(FxItemBase):
    """A cut device at program second ``at`` (stage ``junction``).

    style       XFADE_STYLES | MASK_STYLES | OTHER_STYLES (above)
    duration_s  the whole window, d/2 each side of the cut
    origin      iris / letter_window / zoom_blur / radial_blur centre
    count       columns, bands, box_steps, slices
    direction   wipes, staircases, whips
    colour      hit_flash (default white)
    """
    STAGES: ClassVar[tuple] = ("junction",)
    COMPILER: ClassVar[str] = "junctions_v2:JUNCTION"

    type: Literal["junction"] = "junction"
    style: Literal["dissolve", "fadegrays", "pixelize", "radial", "slices",
                   "wipe_left", "wipe_right", "wipe_up", "wipe_down",
                   "iris", "columns", "staircase", "box_steps", "bands",
                   "letter_window", "matte_cut", "double_exposure",
                   "pixel_stretch", "hit_flash", "whip_up", "whip_down",
                   "zoom_blur", "radial_blur", "datamosh"]
    duration_s: float = Field(default=0.4, ge=0.04, le=2.0,
                              allow_inf_nan=False)
    origin: Optional[JunctionOrigin] = None
    count: Optional[int] = Field(default=None, ge=2, le=32)
    direction: Optional[Literal["left", "right", "up", "down", "in",
                                "out"]] = None
    colour: Optional[FxColour] = None

    @model_validator(mode="after")
    def _style_fields(self):
        s = self.style
        if s == "letter_window" and (self.origin is None
                                     or self.origin.kind != "glyph"):
            raise ValueError("letter_window names its letters: origin kind "
                             "glyph with the motion item id")
        if self.count is not None and s not in ("columns", "bands",
                                                "box_steps", "slices"):
            raise ValueError("count belongs to columns, bands, box_steps or "
                             "slices")
        if self.colour is not None and s != "hit_flash":
            raise ValueError("colour belongs to hit_flash")
        return self

    def check(self, ctx):
        if self.start is not None or self.end is not None:
            raise ValueError(f"fx {self.id}: a junction names its cut by at, "
                             "not start/end")
        if self.origin is not None and self.origin.kind == "glyph":
            ids = {m.id for m in (getattr(ctx.edl, "motion", None) or [])}
            if self.origin.ref not in ids:
                raise ValueError(f"fx {self.id}: origin names motion item "
                                 f"{self.origin.ref!r}, which is not in "
                                 "this edit")

    def flash_times(self):
        """hit_flash: one full-frame flash on the cut. matte_cut: counted
        as one too — without a person matte on the render its rung is the
        built-in flash_junction substitute, a white flash on the cut, and
        the export gate reads the EDL, not the render. box_steps: every
        step whose box covers a quarter of the frame or more lands with a
        white fill, the last one full-frame (junctions_v2 times them over
        the window's middle 70%).
        Every other style is a transition, not a flash."""
        if self.at is None:
            return []
        at = float(self.at)
        if self.style in ("hit_flash", "matte_cut"):
            return [at]
        if self.style == "box_steps":
            # every step lands with its box filled white — the last one is
            # the whole frame — on the frames junctions_v2 lands them
            # (wipe_masks.box_steps: box_step_frames over ALL the shares)
            shares = box_step_shares(self.count)
            n = max(2, int(round(self.duration_s * NOMINAL_FPS)))
            out = []
            for k, frame in enumerate(box_step_frames(len(shares), n)):
                if shares[k] >= LOCAL_FLASH_SHARE:
                    out.append(round(at - self.duration_s / 2.0
                                     + frame / NOMINAL_FPS, 4))
            return out
        return []

    def describe(self):
        bits = [self.style, f"{self.duration_s:g}s"]
        if self.origin is not None and self.origin.kind != "centre":
            bits.append(f"from {self.origin.kind}"
                        + (f" {self.origin.ref}" if self.origin.ref else ""))
        return f"{super().describe()} ({', '.join(bits)})"


def box_step_frames(n_steps, n_frames):
    """Window frame on which each box step lands: the steps share the
    window's middle 70%, the first at 15% (the incoming shot opens inside
    a small box while the outgoing one still plays around it)."""
    n_steps, n_frames = int(n_steps), max(2, int(n_frames))
    a, b = 0.15 * n_frames, 0.85 * n_frames
    return [int(round(a + (b - a) * k / max(1, n_steps))) for k in
            range(n_steps)]


FX_MEMBERS = ((AccentFx, StrobeFx, JunctionFx) if live(LIVE, TRACK)
              else ())
