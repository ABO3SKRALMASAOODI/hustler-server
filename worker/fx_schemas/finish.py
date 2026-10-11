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

from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import FxColour, FxItemBase, FxRegion, live, region_check

TRACK = "1e"
# Track 1E flips this when gradewin.py and finish.py compile both types.
LIVE = False

FINISH_KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear",
                "film", "thermal", "xray", "blueprint", "line_art",
                "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                "night_vision")
# make_variant_set still kinds (assets, not fx items).
VARIANT_KINDS = ("photo", "negative", "line", "blueprint", "thermal", "xray",
                 "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                 "night_vision", "halftone")
PROTECT = ("skin", "brand")


class GradeLut(BaseModel):
    """A LUT applied by the grade: an imported ``.cube`` (≤ 65³) or a
    shot-match cube (33³), stored as a project asset."""
    asset_key: str = Field(min_length=1, max_length=400)
    strength: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    origin: Literal["import", "match"] = "import"
    # match: the program second of the hero frame it was matched to
    match_t: Optional[float] = Field(default=None, ge=0.0,
                                     allow_inf_nan=False)


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

    def describe(self):
        bits = [f for f in ("saturation", "ev", "contrast", "lut", "isolate",
                            "split_tone") if getattr(self, f) is not None]
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

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.matte_weighted and self.kind not in ("thermal", "xray"):
            raise ValueError("matte_weighted belongs to thermal or xray")
        return self

    def check(self, ctx):
        region_check(self.region, ctx, f"fx {self.id}")

    def describe(self):
        return f"{super().describe()} ({self.kind})"


FX_MEMBERS = (GradeWindowFx, FinishFx) if live(LIVE, TRACK) else ()
