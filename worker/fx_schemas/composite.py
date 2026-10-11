"""Footage-aware compositor: EDL.fx tag ``region`` and the typed slot
``MotionItem.composite`` (track 1C; worker/devices/INTERFACES_W1.md §3.3).

Pinned by the Wave 1 interface pass. Track 1C owns this file: it may add
optional fields (default None), tighten a ``check``, implement
``flash_times`` and flip ``LIVE``; renaming or removing a pinned field is a
round-lead change.

* ``region``: any effect chain inside a rect, ellipse, (keyframed) polygon,
  glyph, face or card region, via a precomputed gray mask and maskedmerge.
  Pixels outside the region are the picture's own (region_locality = 0).
* ``MotionItem.composite``: how a motion graphic joins the picture — blend
  mode, footage inside its glyphs (``fill``), the ghost stroke pass where a
  behind-subject graphic hides its letters, the stage a behind-subject item
  composites at, and a clipping region. ``composite: None`` is exactly
  today's composite, byte for byte; nothing is automatic for items that do
  not carry one.

The ``type_window`` tag stays reserved for 1C but is NOT registered in
Wave 1: footage inside letters, letters as windows and the cross-cut reveal
are ``composite.fill`` on the letters' own motion item (one item to write
and remove, through a verb or apply_edit_batch alike).

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from . import FxColour, FxItemBase, FxRegion, FxSource, live, region_check

TRACK = "1c"
# Track 1C flips this when compositor.py compiles ``region`` items and
# motion_layer honours ``MotionItem.composite``.
LIVE = False

BLENDS = ("normal", "screen", "add", "multiply", "overlay", "softlight",
          "difference", "exclusion", "lighten", "darken", "auto")
REGION_EFFECTS = ("negate", "solarise", "mono", "posterize", "threshold",
                  "blur", "pixelate", "exposure", "tint", "ribbed_glass",
                  "refraction", "heat")


class MotionGhost(BaseModel):
    """The stroke-only pass (motion/lib/paint.js) laid OVER the subject only
    where matte ∧ glyph, so letters a behind-subject graphic hides still
    read. ``threshold``: the hidden share of a glyph that switches it on."""
    opacity: float = Field(default=0.45, ge=0.2, le=0.8, allow_inf_nan=False)
    width_px: float = Field(default=2.5, ge=1.0, le=8.0, allow_inf_nan=False)
    threshold: float = Field(default=0.25, ge=0.0, le=1.0,
                             allow_inf_nan=False)


class MotionComposite(BaseModel):
    """``MotionItem.composite`` (all optional; None = today's composite).

    blend      normal | screen | add | multiply | overlay | softlight |
               difference | exclusion | lighten | darken | auto (difference
               over busy plates, screen over dark ones — plate.py); computed
               bbox-local; legibility is measured on the result
    opacity    0..1 over the whole graphic
    fill       footage drawn inside the graphic's alpha (glyphs) instead of
               its own colour; ``fill_mode`` outside = the glyphs become
               windows onto the picture and the fill covers the rest
    ghost      'auto' (MotionGhost defaults) | 'off' | MotionGhost: the
               stroke pass where a behind_subject item hides its glyphs
    stage      behind_subject items only: world (rides zooms, today) or
               screen (screen-locked under a camera move, composited after
               the camera through the screen-space matte); other layers
               ignore it
    region     clip the graphic to a region
    """
    blend: Literal["normal", "screen", "add", "multiply", "overlay",
                   "softlight", "difference", "exclusion", "lighten",
                   "darken", "auto"] = "normal"
    opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                     allow_inf_nan=False)
    fill: Optional[FxSource] = None
    fill_mode: Literal["inside", "outside"] = "inside"
    ghost: Optional[Union[Literal["auto", "off"], MotionGhost]] = None
    stage: Optional[Literal["world", "screen"]] = None
    region: Optional[FxRegion] = None

    @model_validator(mode="after")
    def _fields(self):
        if self.fill_mode == "outside" and self.fill is None:
            raise ValueError("fill_mode outside needs a fill")
        if self.region is not None and self.region.shape == "glyph":
            raise ValueError("a graphic cannot be clipped to a glyph region")
        return self


class RegionFx(FxItemBase):
    """One effect inside a region over [start, end).

    effect    negate | solarise | mono | posterize | threshold | blur |
              pixelate | exposure (``ev``) | tint (``colour``) |
              ribbed_glass | refraction | heat (displacement materials:
              maps built once in numpy, never per-frame expressions)
    strength  0..1 mix of the effect inside the region
    scale     the material's pattern scale (ribs, cells, ripples)
    """
    STAGES: ClassVar[tuple] = ("world", "screen", "pre_captions",
                               "over_type")
    COMPILER: ClassVar[str] = "compositor:REGION"

    type: Literal["region"] = "region"
    effect: Literal["negate", "solarise", "mono", "posterize", "threshold",
                    "blur", "pixelate", "exposure", "tint", "ribbed_glass",
                    "refraction", "heat"]
    region: FxRegion
    strength: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    colour: Optional[FxColour] = None
    ev: Optional[float] = Field(default=None, ge=-4.0, le=4.0,
                                allow_inf_nan=False)
    scale: Optional[float] = Field(default=None, ge=0.1, le=10.0,
                                   allow_inf_nan=False)

    @model_validator(mode="after")
    def _effect_fields(self):
        if self.effect == "tint" and self.colour is None:
            raise ValueError("a tint names its colour")
        if self.colour is not None and self.effect not in ("tint",
                                                           "solarise"):
            raise ValueError("colour belongs to tint or solarise")
        if self.ev is not None and self.effect != "exposure":
            raise ValueError("ev belongs to exposure")
        if self.scale is not None and self.effect not in (
                "ribbed_glass", "refraction", "heat", "pixelate"):
            raise ValueError("scale belongs to a material or pixelate")
        return self

    def check(self, ctx):
        region_check(self.region, ctx, f"fx {self.id}")

    def describe(self):
        return (f"{super().describe()} ({self.effect} in "
                f"{self.region.shape})")


_ON = live(LIVE, TRACK)
FX_MEMBERS = (RegionFx,) if _ON else ()
FIELD_TYPES = {"MotionItem.composite": MotionComposite} if _ON else {}
