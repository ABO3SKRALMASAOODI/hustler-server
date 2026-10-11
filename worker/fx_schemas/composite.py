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

Every authoring rule lives here (field constraints, validators, ``check``),
never only in a verb: a stale MCP client writing the same item through
apply_edit_batch is validated exactly like ``set_composite`` /
``add_region_effect`` (INTERFACES_W1.md §5).

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from . import FxColour, FxItemBase, FxRegion, FxSource, live, region_check

TRACK = "1c"
# Track 1C: compositor.py compiles ``region`` items and motion_layer honours
# ``MotionItem.composite`` (INTERFACES_W1.md §0.4: flipped in the commit
# that ships the cards and the department verbs).
LIVE = True

BLENDS = ("normal", "screen", "add", "multiply", "overlay", "softlight",
          "difference", "exclusion", "lighten", "darken", "auto")
REGION_EFFECTS = ("negate", "solarise", "mono", "posterize", "threshold",
                  "blur", "pixelate", "exposure", "tint", "ribbed_glass",
                  "refraction", "heat")
# Displacement materials: maps built once in numpy (displacement.py).
MATERIALS = ("ribbed_glass", "refraction", "heat")
# Effects whose change of luminance can read as a flash when they cover a
# large share of the frame for a moment (flash_times).
FLASHY_EFFECTS = ("negate", "solarise", "threshold", "exposure")
# A ``program`` fill is the picture itself, ``offset_s`` away: the render
# holds that many seconds of frames in memory (a 1080x1920 frame is ~3 MB),
# so the offset is bounded. Longer offsets are another shot: ``source``.
PROGRAM_FILL_MAX_S = 2.0
# Share of the frame a region must cover before a short luminance effect in
# it counts as a full-frame flash (flash_safety reads flash_times).
FLASH_AREA = 0.25
# A region effect at most this long that covers FLASH_AREA is a flash.
FLASH_MAX_S = 0.5


class MotionGhost(BaseModel):
    """The stroke-only pass (motion/lib/paint.js) laid OVER the subject only
    where matte ∧ glyph, so letters a behind-subject graphic hides still
    read. ``width_px``: the outline's width in pixels at a 1080-px short
    side (2.5 = a 2-3 px line on a 1080 x 1920 or 1920 x 1080 frame);
    ``threshold``: the hidden share of a glyph that switches it on (0: on
    whenever anything is hidden)."""
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
    field      a flat colour around the glyphs (fill_mode inside only): the
               title-card look — footage in the letters on black — in one
               item; None = the picture around them
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
    field: Optional[FxColour] = None
    ghost: Optional[Union[Literal["auto", "off"], MotionGhost]] = None
    stage: Optional[Literal["world", "screen"]] = None
    region: Optional[FxRegion] = None

    @model_validator(mode="after")
    def _fields(self):
        if self.fill_mode == "outside" and self.fill is None:
            raise ValueError("fill_mode outside needs a fill")
        if self.region is not None and self.region.shape == "glyph":
            raise ValueError("a graphic cannot be clipped to a glyph region")
        if self.field is not None and self.fill_mode == "outside":
            raise ValueError("field belongs to fill_mode inside (outside "
                             "already fills around the glyphs)")
        f = self.fill
        if f is not None and f.kind == "program" and \
                abs(float(f.offset_s)) > PROGRAM_FILL_MAX_S:
            raise ValueError(f"a program fill is at most "
                             f"{PROGRAM_FILL_MAX_S:g} s away (offset_s); "
                             "use a source fill for another moment")
        if f is not None and f.kind in ("next", "previous") and \
                f.offset_s:
            raise ValueError(f"a {f.kind} fill is aligned to its cut; it "
                             "takes no offset_s")
        return self


class RegionFx(FxItemBase):
    """One effect inside a region over [start, end).

    effect    negate | solarise | mono | posterize | threshold | blur |
              pixelate | exposure (``ev``) | tint (``colour``) |
              ribbed_glass | refraction | heat (displacement materials:
              maps built once in numpy, never per-frame expressions)
    strength  0..1 mix of the effect inside the region
    scale     the material's pattern scale (ribs, cells, ripples) or the
              pixelate block size, 1 = the designed default
    colour    tint's colour; solarise's duotone colour (magenta solarise)
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
                "ribbed_glass", "refraction", "heat", "pixelate", "blur"):
            raise ValueError("scale belongs to a material, blur or pixelate")
        if self.stage in ("world", "screen", "pre_captions", "over_type") \
                and (self.start is None or self.end is None):
            raise ValueError("a region effect needs start and end")
        if self.device is not None and not str(self.device).startswith(
                "region_"):
            # the cards that write region items are track 1C's region_*
            # cards (INTERFACES_W1.md §3.6); composite_* cards write
            # MotionItem.composite, never an fx item
            raise ValueError(f"a region item's device is a region_* card, "
                             f"not {self.device!r}")
        return self

    def check(self, ctx):
        region_check(self.region, ctx, f"fx {self.id}")

    def _area(self):
        """The share of the frame the region covers when that is known
        statically (rect / ellipse / polygon boxes, invert included); None
        for anchored shapes (glyph, face, card) and keyed shapes."""
        r = self.region
        if r.shape in ("rect", "ellipse") and r.box is not None:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in r.box)
            a = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            if r.shape == "ellipse":
                a *= 0.785398
        elif r.shape == "polygon" and r.points:
            pts = [(min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))))
                   for x, y in r.points]
            a = abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1]
                        for i in range(len(pts)))) / 2.0
        else:
            return None
        return 1.0 - a if r.invert else a

    def flash_times(self):
        """A luminance effect (negate, solarise, threshold, a +-1 EV or
        stronger exposure) over at least a quarter of the frame for at most
        half a second reads as one full-frame flash at ``start``. Anything
        smaller, softer, longer, or bounded by an anchor that is at most a
        glyph, a face or a card, is no flash (flash_safety reads this)."""
        if self.effect not in FLASHY_EFFECTS or self.start is None:
            return []
        if self.effect == "exposure" and abs(float(self.ev or 0.0)) < 1.0:
            return []
        if self.strength < 0.5:
            return []
        if float(self.end) - float(self.start) > FLASH_MAX_S + 1e-6:
            return []
        area = self._area()
        if area is None:
            # a card region is most of the frame on a card program
            return [float(self.start)] if self.region.shape == "card" else []
        return [float(self.start)] if area >= FLASH_AREA else []

    def describe(self):
        return (f"{super().describe()} ({self.effect} in "
                f"{self.region.shape}"
                f"{' ' + self.region.ref if self.region.ref else ''})")


_ON = live(LIVE, TRACK)
FX_MEMBERS = (RegionFx,) if _ON else ()
FIELD_TYPES = {"MotionItem.composite": MotionComposite} if _ON else {}
