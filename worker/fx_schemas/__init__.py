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
"""

from typing import ClassVar, List, Literal, Optional, Union

from pydantic import BaseModel, Field

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
        """Member hook: EDL-level validation. ``ctx`` is an FxCheckContext."""
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


__all__ = ["FX_STAGES", "PICTURE_STAGES", "FIELD_SLOTS", "BIND_ON",
           "FxBinding", "FxItemBase", "FxCheckContext", "List"]
