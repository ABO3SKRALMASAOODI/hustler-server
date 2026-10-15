"""Arrangement on the music bus: EDL.fx tag ``arrange`` (Wave 2, track 2D;
worker/devices/INTERFACES_W2.md §5).

Shapes the USER's track on its own bus (after its fit and fades, before the
mix), always bound to a moment of the track's RhythmMap or of the speech:

  hole     a gain hole with 10 ms fades (a stop, a stutter gap)
  hold     the held silence before the payoff (the breath before the drop)
  filter   a low-pass (or high-pass) sweep that OPENS on a moment: the
           band-limited intro that blooms on the drop
  stutter  a 1/8 or 1/16 slice repeated over the window
  montage_intent / fit_intent   records only (no sound): what plan_to_music
           and shape_music solved, so a song swap re-plans deterministically

Every item names its music item (``music_id``) and that item must have a
user provenance (agents never choose music: arrangement never applies to a
track nobody chose). Pure pydantic (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal, Optional

from pydantic import Field, model_validator

from . import FxItemBase, live

TRACK = "2d"
LIVE = True

KINDS = ("hole", "hold", "filter", "stutter", "montage_intent", "fit_intent")
RECORDS = ("montage_intent", "fit_intent")
USER_PROVENANCE = ("user_upload", "user_named", "user_picked_library")


class ArrangeFx(FxItemBase):
    STAGES: ClassVar[tuple] = ("music_bus",)
    COMPILER: ClassVar[str] = "arrangement:ARRANGE"

    type: Literal["arrange"] = "arrange"
    stage: Literal["music_bus"] = "music_bus"
    kind: Literal["hole", "hold", "filter", "stutter", "montage_intent",
                  "fit_intent"]
    # filter
    filter: Optional[Literal["lowpass", "highpass"]] = None
    from_hz: Optional[float] = Field(default=None, ge=20.0, le=20000.0)
    to_hz: Optional[float] = Field(default=None, ge=20.0, le=20000.0)
    open_s: Optional[float] = Field(default=None, ge=0.0, le=4.0)
    # hole / hold
    fade_ms: Optional[float] = Field(default=None, ge=2.0, le=200.0)
    # stutter
    slice: Optional[Literal["1/8", "1/16"]] = None
    slice_s: Optional[float] = Field(default=None, gt=0.01, le=2.0)
    # records
    params: Optional[dict] = None

    @model_validator(mode="after")
    def _fields(self):
        if self.kind not in RECORDS:
            if self.start is None or self.end is None:
                raise ValueError(f"an arrange {self.kind} needs start and "
                                 "end (program seconds)")
            if self.end - self.start < 0.02:
                raise ValueError("an arrange window is at least 0.02 s")
        if self.kind == "filter" and not self.filter:
            raise ValueError("a filter arrange names lowpass or highpass")
        if self.kind == "stutter" and not (self.slice or self.slice_s):
            raise ValueError("a stutter arrange names its slice (1/8, 1/16 "
                             "or slice_s)")
        if self.params is not None and len(str(self.params)) > 4000:
            raise ValueError("arrange params are a small record (<= 4 KB)")
        return self

    def check(self, ctx):
        mid = self.music_id
        item = next((m for m in getattr(ctx.edl, "music", None) or []
                     if getattr(m, "id", None) == mid), None)
        if item is None:
            raise ValueError(f"arrange '{self.id}': no music item '{mid}'")
        prov = getattr(item, "provenance", None) or "legacy"
        if prov not in USER_PROVENANCE:
            raise ValueError(
                f"arrange '{self.id}': music '{mid}' was not supplied, named "
                "or picked by the user (provenance "
                f"{prov}); the arrangement only shapes the user's own track")
        if self.kind not in RECORDS and self.end is not None and \
                ctx.program_s and self.start >= ctx.program_s:
            raise ValueError(f"arrange '{self.id}' starts after the program")

    def flash_times(self):
        return []

    def describe(self):
        if self.kind in RECORDS:
            return f"arrange {self.kind} [{self.id}] music {self.music_id}"
        what = self.kind if self.kind != "filter" else \
            f"{self.filter} sweep"
        return (f"arrange {what} [{self.id}] music {self.music_id} "
                f"{self.start:g}-{self.end:g}s")


FX_MEMBERS = (ArrangeFx,) if live(LIVE, TRACK) else ()
