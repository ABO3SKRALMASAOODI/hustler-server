"""Punctuation, strobes and junctions: EDL.fx tags ``accent``, ``strobe``
and ``junction`` (track 1D; worker/devices/INTERFACES_W1.md §3.4).

Pinned by the Wave 1 interface pass. Track 1D owns this file: it may add
optional fields (default None), tighten a ``check``, implement
``flash_times`` and flip ``LIVE``; renaming or removing a pinned field,
kind or style is a round-lead change.

* ``accent``: a 1–N-frame punctuation frame. Its STAGE is its scope:
  ``pre_captions`` changes the picture only (type untouched),
  ``over_type`` inverts the type too (ref 04). Never below the aspect bars,
  the screen-frame plate, the watermark or the end card.
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

from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import (FxColour, FxItemBase, FxRegion, FxSource, live,
               region_check, source_check)

TRACK = "1d"
# Track 1D flips this when accents.py, strobe.py and junctions_v2.py compile
# their types.
LIVE = False

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


class AccentFx(FxItemBase):
    """One punctuation accent over [start, end) (usually 1–2 frames).

    kind      negative | white_hit (1-frame peak, ``decay_f`` frames of
              exponential fall-off, tau ≈ 2) | colour_hit (``colour``; None
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
        region_check(self.region, ctx, f"fx {self.id}")

    def describe(self):
        scope = "all" if self.stage == "over_type" else "picture"
        return f"{super().describe()} ({self.kind}, scope {scope})"


class StrobeFrame(BaseModel):
    """One strobe image: a still or clip frame (``source``) held ``hold_f``
    frames, optionally negated."""
    source: FxSource
    hold_f: int = Field(default=2, ge=1, le=8)
    negate: bool = False


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
        for i, fr in enumerate(self.frames):
            source_check(fr.source, ctx, f"fx {self.id} frame {i}")
            if fr.source.kind in ("next", "previous"):
                raise ValueError(f"fx {self.id}: a strobe frame is a still, "
                                 "a clip frame, the program or a colour")

    def describe(self):
        return f"{super().describe()} ({len(self.frames)} images)"


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
        if self.origin is not None and self.origin.kind == "glyph":
            ids = {m.id for m in (getattr(ctx.edl, "motion", None) or [])}
            if self.origin.ref not in ids:
                raise ValueError(f"fx {self.id}: origin names motion item "
                                 f"{self.origin.ref!r}, which is not in "
                                 "this edit")

    def describe(self):
        return f"{super().describe()} ({self.style}, {self.duration_s:g}s)"


FX_MEMBERS = ((AccentFx, StrobeFx, JunctionFx) if live(LIVE, TRACK)
              else ())
