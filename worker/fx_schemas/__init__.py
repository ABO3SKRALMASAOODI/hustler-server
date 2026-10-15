"""The EDL.fx registry: one discriminated union, filled by dropping files here.

Wave 0 interface pass (worker/devices/INTERFACES.md §4). This package is
loaded BY schemas.py (`_load_fx_registry`), by file path, so it must stay as
pure as schemas.py itself: pydantic and the standard library only, never a
worker module. The backend loads schemas.py under the name ``worker_schemas``
without the worker directory on sys.path; a member module that imported
``config`` or ``renderer`` would break every Studio edit.

A member module is any ``fx_schemas/<name>.py`` whose name does not start
with ``_``. It exports:

  FX_MEMBERS   tuple of FxItemBase subclasses. Each sets
               ``type: Literal["<tag>"] = "<tag>"`` with a tag unique across
               the registry, and narrows ``STAGES`` to the stages it can run
               at. Optional ``check(self, ctx)`` raises ValueError for
               EDL-level problems; optional ``describe(self)`` is the one-line
               summary the agent reads in diffs.
  FIELD_TYPES  optional {slot: annotation} for the inert slots below. Each
               slot has exactly one owner; the annotation is wrapped in
               Optional[...] = None by schemas.py.

Members import shared pieces relatively (``from . import FxItemBase``) so the
same file works under the worker's ``fx_schemas`` and the backend's
per-host package name.

Wave 1 (worker/devices/INTERFACES_W1.md §2): the shared value models every
Wave 1 member reuses — ``FxColour``, ``FxRegion`` and ``FxSource`` — and the
``live`` switch. A Wave 1 member module exports its classes only while its
owning track has switched it on (``LIVE = True`` in that module) or the
preview switch is set (``VALMERA_FX_PREVIEW=1`` or a comma list of track ids
such as ``1b,1d``; tests and refbench only). Until then the registry is
exactly what it was, so the skeleton changes nothing a customer can observe.
"""

import os
import re
from typing import Annotated, ClassVar, List, Literal, Optional, Union

from pydantic import AfterValidator, BaseModel, Field, model_validator

# Graph order (ROADMAP Wave 0 / 0B). Pinned: renaming a stage is an
# interface change, never a track-local edit.
FX_STAGES = ("world", "screen", "pre_captions", "over_type", "junction",
             "music_bus")
# Stages whose items occupy a [start, end) program window.
PICTURE_STAGES = ("world", "screen", "pre_captions", "over_type")

# Typed slots on existing EDL models. Unowned slots accept only None.
FIELD_SLOTS = ("MotionItem.composite", "MotionItem.anchor",
               "TransitionSpec.per_junction", "MusicItem.automation")

# What a device can bind to (A4: devices store intent, not pixels).
BIND_ON = ("time", "word", "syllable", "stress", "phrase_end", "beat",
           "downbeat", "onset", "cut", "section", "drop", "gap")


class FxBinding(BaseModel):
    """The moment that earned an fx item. ``start``/``end`` (or ``at``) are
    the solved program times; ``bind`` is what a re-solve (keep change, song
    swap, new aspect) recomputes them from.

    on      what kind of moment
    ref     its identity in the clock named by ``rhythm``: a transcript word
            index (word/syllable/stress/phrase_end), a RhythmMap list index
            (beat/downbeat/onset/section/drop/gap), a cut's SOURCE second
            (cut), or a program second (time)
    rhythm  which clock: "speech" (the prosody grid) or a MusicItem id
    offset_f  whole frames relative to the moment (-1 = a frame early)
    """
    on: Literal["time", "word", "syllable", "stress", "phrase_end", "beat",
                "downbeat", "onset", "cut", "section", "drop", "gap"]
    ref: Optional[Union[int, float, str]] = None
    rhythm: Optional[str] = Field(default=None, max_length=80)
    offset_f: int = Field(default=0, ge=-240, le=240)


class FxItemBase(BaseModel):
    """Fields every EDL.fx item carries. Members add their own ``kind`` and
    parameters. Times are PROGRAM seconds rounded to 0.01 like every EDL time;
    renderers convert with frame = round(t * fps) and treat ``end`` as
    exclusive, so a 1-frame item at 59.94 fps is still exact.

    Picture stages need start/end. ``junction`` items name the cut they
    decorate by ``at`` (its program second). ``music_bus`` items name their
    MusicItem by ``music_id``.
    """
    STAGES: ClassVar[tuple] = FX_STAGES
    # False for engine/test types (0B's stage probes): they validate and
    # render, but edit_batch refuses to author them and apply_edit_batch
    # does not advertise the fx layer for them.
    AUTHORABLE: ClassVar[bool] = True

    id: str = Field(min_length=1, max_length=80)
    type: str
    stage: Literal["world", "screen", "pre_captions", "over_type",
                   "junction", "music_bus"]
    start: Optional[float] = Field(default=None, allow_inf_nan=False)
    end: Optional[float] = Field(default=None, allow_inf_nan=False)
    at: Optional[float] = Field(default=None, allow_inf_nan=False)
    music_id: Optional[str] = Field(default=None, max_length=80)
    bind: Optional[FxBinding] = None
    # Device card id that wrote the item (ledger, budgets, degrade report).
    device: Optional[str] = Field(default=None, max_length=80)
    seed: Optional[int] = Field(default=None, ge=0, le=2 ** 31 - 1)
    purpose: Optional[str] = Field(default=None, max_length=300)

    def check(self, ctx):
        """Member hook: EDL-level validation. ``ctx`` is an FxCheckContext.
        Every authoring rule for an item lives here or in the field
        constraints, never in a verb: a stale MCP client writing the same
        item through apply_edit_batch is validated exactly like the verb
        (INTERFACES_W1.md §5)."""
        return None

    def flash_times(self):
        """Member hook for flash safety (worker/flash_safety.py): the
        program seconds of every FULL-FRAME flash this item plays, or None
        for "one flash at ``start`` when its device card is flash-flagged"
        (the Wave 0 reading). A strobe returns each image change; an item
        that flashes only inside a region smaller than a quarter of the
        frame returns []."""
        return None

    def describe(self):
        if self.stage == "junction":
            where = f"cut@{self.at:g}s" if self.at is not None else "cut"
        elif self.stage == "music_bus":
            where = f"music {self.music_id}"
        else:
            where = (f"{self.start:g}-{self.end:g}s"
                     if self.start is not None and self.end is not None
                     else "")
        return f"{self.type} [{self.id}] {self.stage} {where}".rstrip()


class FxCheckContext:
    """What a member's ``check`` may read. ``edl`` is the validated EDL model
    (other layers already normalized), ``program_s`` the program length,
    ``source_s`` the source duration (None for a canvas program)."""

    def __init__(self, edl, program_s, source_s, canvas, render_fragment):
        self.edl = edl
        self.program_s = program_s
        self.source_s = source_s
        self.canvas = canvas
        self.render_fragment = render_fragment


# ── Wave 1 shared value models (INTERFACES_W1.md §2) ────────────────────
# Pinned by the round lead. Members reuse them instead of defining their own,
# so one region, one colour and one footage reference mean the same thing in
# every device, in every verb and through apply_edit_batch.

PREVIEW_ENV = "VALMERA_FX_PREVIEW"
W1_TRACKS = ("1a", "1b", "1c", "1d", "1e", "1f")


def live(flag, track):
    """Does a Wave 1 member module register its classes?

    ``flag`` is the module's own ``LIVE`` constant: the owning track flips it
    to True when its compiler lands (one line, in a file it owns). The
    preview switch ``VALMERA_FX_PREVIEW`` ("1" = every track, or a comma
    list such as "1b,1d") registers a module early for tests and refbench;
    production never sets it."""
    if flag:
        return True
    raw = os.environ.get(PREVIEW_ENV, "").strip().lower()
    if not raw:
        return False
    if raw in ("1", "all", "true", "yes", "on"):
        return True
    return str(track).strip().lower() in {p.strip() for p in raw.split(",")}


# Colours: a named role or a hex value. "accent" is the Look's accent,
# resolved at render (never a pixel value stored for a role).
COLOUR_NAMES = ("white", "black", "accent")
_HEX = re.compile(r"^#[0-9a-f]{6}$")


def _colour(value):
    v = str(value or "").strip().lower()
    if v in COLOUR_NAMES or _HEX.match(v):
        return v
    raise ValueError(f"colour must be one of {', '.join(COLOUR_NAMES)} or "
                     "#rrggbb")


FxColour = Annotated[str, AfterValidator(_colour)]

# Region shapes. Geometry is in FRAME FRACTIONS of the stage's space (world
# or screen), and anchors (glyph, face, card) are preferred over boxes: a
# device stores intent, the renderer solves pixels per render and aspect
# (ROADMAP A4). The person matte is NOT a region shape: anything bounded by
# the subject is a ``subject`` item (1B), never a region.
REGION_SHAPES = ("rect", "ellipse", "polygon", "glyph", "face", "card")


class FxRegionKey(BaseModel):
    """One keyframe of a moving rect/ellipse/polygon. ``t`` is seconds from
    the item's start; ``points`` is [[x0, y0], [x1, y1]] for rect/ellipse
    (the box's corners) or the polygon's vertices."""
    t: float = Field(ge=0, le=600, allow_inf_nan=False)
    points: List[List[float]] = Field(min_length=2, max_length=32)
    ease: Literal["linear", "in", "out", "in_out", "hold"] = "linear"


class FxRegion(BaseModel):
    """Where an effect applies. 255 inside, black outside; ``invert`` swaps.

    shape     rect | ellipse   ``box`` [x0, y0, x1, y1] or ``keys``
              polygon          ``points`` (3-32 vertices) or ``keys``
              glyph            ``ref`` = a motion item id: its clip's alpha
              face             ``ref`` = None (every face) or a face index
              card             the picture card's inner rect (rounded)
    feather_px  edge softness in pixels at a 1080-px-high frame (scaled)
    """
    shape: Literal["rect", "ellipse", "polygon", "glyph", "face", "card"]
    box: Optional[List[float]] = Field(default=None, min_length=4,
                                       max_length=4)
    points: Optional[List[List[float]]] = Field(default=None, min_length=3,
                                                max_length=32)
    keys: Optional[List[FxRegionKey]] = Field(default=None, min_length=2,
                                              max_length=64)
    ref: Optional[str] = Field(default=None, max_length=80)
    invert: bool = False
    feather_px: float = Field(default=0.0, ge=0.0, le=64.0,
                              allow_inf_nan=False)

    @model_validator(mode="after")
    def _shape_fields(self):
        s = self.shape
        if s in ("rect", "ellipse"):
            if (self.box is None) == (self.keys is None):
                raise ValueError(f"a {s} region takes box or keys (one)")
            if self.points is not None or self.ref is not None:
                raise ValueError(f"a {s} region takes no points or ref")
            if self.box is not None:
                _check_box(self.box)
            for k in self.keys or []:
                if len(k.points) != 2:
                    raise ValueError(f"a {s} keyframe is [[x0, y0], [x1, y1]]")
                _check_box([*k.points[0], *k.points[1]])
        elif s == "polygon":
            if (self.points is None) == (self.keys is None):
                raise ValueError("a polygon region takes points or keys (one)")
            if self.box is not None or self.ref is not None:
                raise ValueError("a polygon region takes no box or ref")
            for pts in ([self.points] if self.points else []) + \
                    [k.points for k in self.keys or []]:
                _check_points(pts, 3)
        else:
            if self.box is not None or self.points is not None or \
                    self.keys is not None:
                raise ValueError(f"a {s} region takes no box, points or keys")
            if s == "glyph" and not self.ref:
                raise ValueError("a glyph region names its motion item by ref")
            if s == "card" and self.ref is not None:
                raise ValueError("a card region takes no ref")
            if s == "face" and self.ref is not None and \
                    not str(self.ref).isdigit():
                raise ValueError("a face region's ref is a face index")
        if self.keys:
            ts = [k.t for k in self.keys]
            if ts != sorted(ts) or len(set(ts)) != len(ts):
                raise ValueError("region keys must have increasing t")
        return self


def _check_points(pts, least):
    if len(pts) < least:
        raise ValueError(f"a polygon needs at least {least} vertices")
    for p in pts:
        if len(p) != 2 or not all(-0.5 <= float(v) <= 1.5 for v in p):
            raise ValueError("points are [x, y] frame fractions "
                             "(-0.5..1.5)")


def _check_box(box):
    x0, y0, x1, y1 = (float(v) for v in box)
    if not (-0.5 <= x0 < x1 <= 1.5 and -0.5 <= y0 < y1 <= 1.5):
        raise ValueError("a box is [x0, y0, x1, y1] frame fractions with "
                         "x0 < x1 and y0 < y1")


def region_check(region, ctx, what="region"):
    """EDL-level checks a member's ``check`` runs on its region: a glyph
    region names an existing motion item."""
    if region is None or region.shape != "glyph":
        return
    ids = {m.id for m in (getattr(ctx.edl, "motion", None) or [])}
    if region.ref not in ids:
        raise ValueError(f"{what}: glyph region names motion item "
                         f"{region.ref!r}, which is not in this edit")


# Footage references (fills, backgrounds, strobe frames). Media is named by
# ``asset_key`` so edit_batch.media_keys polices it like every other key.
SOURCE_KINDS = ("program", "source", "asset", "colour", "next", "previous")


class FxSource(BaseModel):
    """Picture an effect draws from.

    program   the program itself, ``offset_s`` seconds away (a time offset)
    source    the main video from SOURCE second ``src_t`` (another shot)
    asset     a project image or video (``asset_key``; a 1E variant is an
              asset), from ``offset_s`` into it; ``loop`` repeats a video
    colour    a flat ``colour``
    next      the shot after the item's cut, aligned so it continues
              seamlessly at the cut (the cross-cut reveal)
    previous  the shot before it, likewise
    """
    kind: Literal["program", "source", "asset", "colour", "next",
                  "previous"]
    offset_s: float = Field(default=0.0, ge=-60.0, le=3600.0,
                            allow_inf_nan=False)
    src_t: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)
    asset_key: Optional[str] = Field(default=None, min_length=1,
                                     max_length=400)
    colour: Optional[FxColour] = None
    loop: bool = False

    @model_validator(mode="after")
    def _kind_fields(self):
        k = self.kind
        need = {"source": "src_t", "asset": "asset_key", "colour": "colour"}
        for kind, field in need.items():
            has = getattr(self, field) is not None
            if k == kind and not has:
                raise ValueError(f"a {kind} source needs {field}")
            if k != kind and has:
                raise ValueError(f"{field} belongs to a {kind} source")
        if k != "asset" and self.loop:
            raise ValueError("loop belongs to an asset source")
        if k in ("asset",) and self.offset_s < 0:
            raise ValueError("an asset source's offset_s is >= 0")
        return self


def source_check(source, ctx, what="source"):
    """EDL-level checks for an FxSource: a ``source`` fill needs a main
    video and a time inside it."""
    if source is None or source.kind != "source":
        return
    if ctx.source_s is None:
        raise ValueError(f"{what}: a source fill needs a main video")
    if source.src_t >= float(ctx.source_s):
        raise ValueError(f"{what}: src_t {source.src_t:g}s is past the end "
                         f"of the main video ({float(ctx.source_s):g}s)")


__all__ = ["FX_STAGES", "PICTURE_STAGES", "FIELD_SLOTS", "BIND_ON",
           "FxBinding", "FxItemBase", "FxCheckContext", "List",
           "PREVIEW_ENV", "W1_TRACKS", "live", "COLOUR_NAMES", "FxColour",
           "REGION_SHAPES", "FxRegionKey", "FxRegion", "region_check",
           "SOURCE_KINDS", "FxSource", "source_check"]
