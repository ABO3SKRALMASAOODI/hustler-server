"""Finishing and the Variant Forge: EDL.fx tags ``grade_window`` and
``finish`` (track 1E; worker/devices/INTERFACES_W1.md §3.5).

Pinned by the Wave 1 interface pass. Track 1E owns this file: it may add
optional fields (default None), tighten a ``check`` and flip ``LIVE``;
renaming or removing a pinned field or kind is a round-lead change.

* ``grade_window``: a grade over a shot or section with N-frame ramps
  (mono → colour on a drop), colour isolation by HUE (``isolate``; keeping
  colour on the SUBJECT is a 1B ``subject`` item of kind isolate), an
  imported ``.cube`` LUT or a shot-match LUT (``lut.asset_key``), a split
  tone, and skin / brand protection.
* ``finish``: a picture finish over [start, end) — LCD re-photography, CRT
  and LED dot matrices, halftone and xerox, band tear, film stock — and the
  registered variants played live (thermal, X-ray, blueprint, line art,
  solarise, posterise, kaleidoscope, pixel stretch, night vision).
  ``matte_weighted`` thermal / X-ray read the person matte (1A).

Variant STILLS (``make_variant_set``) are project assets, not fx items: a
strobe (1D) or a fill (1C) names them by ``asset_key``.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

import re
from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import FxColour, FxItemBase, FxRegion, live, region_check

TRACK = "1e"
# Track 1E: gradewin.py and finish.py compile both types.
LIVE = True

FINISH_KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear",
                "film", "thermal", "xray", "blueprint", "line_art",
                "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                "night_vision")
# make_variant_set still kinds (assets, not fx items).
VARIANT_KINDS = ("photo", "negative", "line", "blueprint", "thermal", "xray",
                 "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                 "night_vision", "halftone")
PROTECT = ("skin", "brand")
# Palettes a finish kind may name (None = its default).
FINISH_PALETTES = {
    "thermal": ("iron", "rainbow", "white_hot", "black_hot"),
    "halftone": ("newsprint", "red", "blue", "black"),
    "night_vision": ("phosphor", "goggles"),
    "blueprint": ("blue", "dark"),
    "line_art": ("paper", "ink"),
}
# Finish kinds that flip the picture's luminance: a short full-frame window
# of one is a flash (flash_safety reads flash_times).
LUMA_FLIP_KINDS = ("thermal", "xray", "blueprint", "solarise", "line_art",
                   "night_vision", "halftone", "xerox")
# A window shorter than this, over most of the frame, reads as a flash.
FLASH_WINDOW_S = 0.5
FLASH_AREA = 0.25
_HEX = re.compile(r"^#[0-9a-f]{6}$")


def _region_area(region):
    """Frame share a region covers (the largest keyframe), or None when it
    is an anchor (glyph, face, card: never a full-frame flash)."""
    if region is None:
        return 1.0
    if region.invert:
        return 1.0
    boxes = []
    if region.shape in ("rect", "ellipse"):
        if region.box:
            boxes.append(region.box)
        boxes += [[*k.points[0], *k.points[1]] for k in region.keys or []]
        k = 1.0 if region.shape == "rect" else 0.785
        return max((max(0.0, min(1.0, b[2]) - max(0.0, b[0])) *
                    max(0.0, min(1.0, b[3]) - max(0.0, b[1])) * k
                    for b in boxes), default=0.0)
    if region.shape == "polygon":
        polys = ([region.points] if region.points else []) + \
            [k.points for k in region.keys or []]
        best = 0.0
        for pts in polys:
            a = 0.0
            for i in range(len(pts)):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % len(pts)]
                a += x0 * y1 - x1 * y0
            best = max(best, abs(a) / 2)
        return min(1.0, best)
    return None


class GradeLut(BaseModel):
    """A LUT applied by the grade: an imported ``.cube`` (≤ 65³) or a
    shot-match cube (33³), stored as a project asset."""
    asset_key: str = Field(min_length=1, max_length=400)
    strength: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    origin: Literal["import", "match"] = "import"
    # match: the program second of the hero frame it was matched to
    match_t: Optional[float] = Field(default=None, ge=0.0,
                                     allow_inf_nan=False)

    @model_validator(mode="after")
    def _a_cube(self):
        # import_lut / match_grade store canonical ``.cube`` assets; any
        # other project media (a video) is never fetched as a LUT
        if not self.asset_key.strip().lower().endswith(".cube"):
            raise ValueError("lut.asset_key names a .cube asset (import_lut "
                             "or match_grade stores one)")
        return self


class HueKey(BaseModel):
    """Colour kept by hue (hsvhold/colorhold); everything else goes mono.
    ``hue`` 'auto' is solved from the subject region at write time and
    stored as #rrggbb."""
    hue: str = Field(min_length=4, max_length=7)
    tolerance: float = Field(default=0.12, ge=0.01, le=0.5,
                             allow_inf_nan=False)
    softness: float = Field(default=0.1, ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _hue(self):
        h = self.hue.strip().lower()
        if not (len(h) == 7 and h.startswith("#") and
                all(c in "0123456789abcdef" for c in h[1:])):
            raise ValueError("a stored hue is #rrggbb ('auto' is solved by "
                             "the verb before it is written)")
        self.hue = h
        return self


class SplitTone(BaseModel):
    shadows: FxColour
    highlights: FxColour
    balance: float = Field(default=0.0, ge=-1.0, le=1.0, allow_inf_nan=False)


class GradeWindowFx(FxItemBase):
    """A grade over [start, end), ramped in over ``ramp_in_f`` frames and out
    over ``ramp_out_f`` (a 3-frame ramp that ENDS on a drop frame is
    ``ramp_in_f: 3`` with ``start`` three frames before it)."""
    STAGES: ClassVar[tuple] = ("world", "screen")
    COMPILER: ClassVar[str] = "gradewin:GRADE"

    type: Literal["grade_window"] = "grade_window"
    saturation: Optional[float] = Field(default=None, ge=0.0, le=2.0,
                                        allow_inf_nan=False)
    ev: Optional[float] = Field(default=None, ge=-4.0, le=4.0,
                                allow_inf_nan=False)
    contrast: Optional[float] = Field(default=None, ge=0.25, le=2.0,
                                      allow_inf_nan=False)
    temperature: Optional[float] = Field(default=None, ge=-1.0, le=1.0,
                                         allow_inf_nan=False)
    tint: Optional[float] = Field(default=None, ge=-1.0, le=1.0,
                                  allow_inf_nan=False)
    lut: Optional[GradeLut] = None
    isolate: Optional[HueKey] = None
    split_tone: Optional[SplitTone] = None
    protect: Optional[List[Literal["skin", "brand"]]] = Field(
        default=None, max_length=2)
    ramp_in_f: int = Field(default=0, ge=0, le=48)
    ramp_out_f: int = Field(default=0, ge=0, le=48)
    region: Optional[FxRegion] = None
    # The hue ``protect: [brand]`` keeps (#rrggbb, solved from the Look at
    # write time; without it brand protection protects nothing).
    brand_hue: Optional[str] = Field(default=None, min_length=7,
                                     max_length=7)

    @model_validator(mode="after")
    def _brand_hue(self):
        if self.brand_hue is not None:
            h = self.brand_hue.strip().lower()
            if not _HEX.match(h):
                raise ValueError("brand_hue is #rrggbb")
            self.brand_hue = h
        return self

    @model_validator(mode="after")
    def _changes_something(self):
        if all(getattr(self, f) is None for f in (
                "saturation", "ev", "contrast", "temperature", "tint", "lut",
                "isolate", "split_tone")):
            raise ValueError("a grade window changes at least one of "
                             "saturation, ev, contrast, temperature, tint, "
                             "lut, isolate or split_tone")
        return self

    def check(self, ctx):
        region_check(self.region, ctx, f"fx {self.id}")
        n = int(round((self.end - self.start) * 100)) if \
            self.start is not None and self.end is not None else 0
        if n and (self.ramp_in_f or self.ramp_out_f):
            # ramps are whole frames inside the window (at <= 60 fps a
            # frame is >= 1.67 cs)
            if (self.ramp_in_f + self.ramp_out_f) * 1.67 > n + 1e-6:
                raise ValueError(f"fx {self.id}: ramps of "
                                 f"{self.ramp_in_f}+{self.ramp_out_f} frames "
                                 "do not fit in the window")

    def flash_times(self):
        """A short full-frame exposure jump is a flash; a colour change
        (mono, a hue key, a LUT) is not. Ramps do not exempt it: a window
        under half a second goes up AND back down inside it, which is the
        opposing pair of transitions a flash is, eased or not."""
        if self.start is None or self.end is None:
            return []
        area = _region_area(self.region)
        short = (self.end - self.start) < FLASH_WINDOW_S
        if area is not None and area >= FLASH_AREA and short and \
                abs(self.ev or 0.0) >= 1.0:
            return [self.start]
        return []

    def describe(self):
        bits = [f for f in ("saturation", "ev", "contrast", "lut", "isolate",
                            "split_tone") if getattr(self, f) is not None]
        if self.isolate is not None:
            bits[bits.index("isolate")] = f"keep {self.isolate.hue}"
        if self.ramp_in_f or self.ramp_out_f:
            bits.append(f"ramps {self.ramp_in_f}/{self.ramp_out_f}f")
        return f"{super().describe()} ({', '.join(bits)})"


class FinishFx(FxItemBase):
    """A picture finish or a live variant over [start, end).

    strength        0..1 mix
    scale           pattern scale (subpixels, dots, bands, grain)
    palette         thermal / night_vision / blueprint palette name
    matte_weighted  thermal and X-ray read the person matte (1A); absent
                    matte = the unweighted look (reported)
    """
    STAGES: ClassVar[tuple] = ("world", "screen", "pre_captions",
                               "over_type")
    COMPILER: ClassVar[str] = "finish:FINISH"

    type: Literal["finish"] = "finish"
    kind: Literal["lcd", "crt", "led", "halftone", "xerox", "band_tear",
                  "film", "thermal", "xray", "blueprint", "line_art",
                  "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                  "night_vision"]
    strength: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    scale: Optional[float] = Field(default=None, ge=0.1, le=10.0,
                                   allow_inf_nan=False)
    palette: Optional[str] = Field(default=None, max_length=40)
    matte_weighted: bool = False
    region: Optional[FxRegion] = None
    # pixel_stretch: where the smeared column/row sits (0..1 of the frame)
    # and which way it smears
    origin: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                    allow_inf_nan=False)
    direction: Optional[Literal["left", "right", "up", "down"]] = None
    # whole-frame eases into and out of the finish
    ramp_in_f: int = Field(default=0, ge=0, le=48)
    ramp_out_f: int = Field(default=0, ge=0, le=48)

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.matte_weighted and self.kind not in ("thermal", "xray"):
            raise ValueError("matte_weighted belongs to thermal or xray")
        if self.palette is not None:
            allowed = FINISH_PALETTES.get(self.kind)
            if not allowed:
                raise ValueError(f"a {self.kind} finish takes no palette")
            if self.palette not in allowed:
                raise ValueError(f"palette for {self.kind} is one of "
                                 f"{', '.join(allowed)}")
        if (self.origin is not None or self.direction is not None) and \
                self.kind != "pixel_stretch":
            raise ValueError("origin and direction belong to pixel_stretch")
        return self

    def check(self, ctx):
        region_check(self.region, ctx, f"fx {self.id}")

    def flash_times(self):
        """A luminance-flipping finish (thermal, X-ray, solarise ...) held
        for under half a second over most of the frame is a flash, ramped
        or not (it flips and flips back inside half a second)."""
        if self.start is None or self.end is None:
            return []
        area = _region_area(self.region)
        if self.kind in LUMA_FLIP_KINDS and area is not None and \
                area >= FLASH_AREA and self.strength >= 0.5 and \
                (self.end - self.start) < FLASH_WINDOW_S:
            return [self.start]
        return []

    def describe(self):
        extra = f", {self.palette}" if self.palette else ""
        return f"{super().describe()} ({self.kind}{extra})"


FX_MEMBERS = (GradeWindowFx, FinishFx) if live(LIVE, TRACK) else ()
