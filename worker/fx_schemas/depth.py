"""Depth, 2.5D and time: EDL.fx tags ``depth`` and ``time`` (track 4A;
worker/devices/INTERFACES_W4.md §3.2, §3.3).

* ``depth``: devices that read the depth of the shot (the ``depth.small``
  sidecar, Depth Anything V2 Small keyframes; the person matte is the
  two-plane fallback): a 2.5D camera move through a still or a frozen
  frame (``parallax``; ``ken_burns`` is its honest 2D substitute and a
  device of its own), depth of field and a rack focus that lands on a word,
  fog and a light sweep through depth, a LiDAR-style ``point_cloud`` of the
  shot, and the opt-in ``push_through`` (a camera move through a word set
  in Z — never proposed unless the user asked: it is a zoom).
* ``time``: picture-only, duration-preserving time devices — reverse,
  echo trails, step-printing (``posterize``), a stutter loop, motion
  interpolation of a slowed window (no stuttering slow motion) and an
  eased time remap. The program clock and the audio never move.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from . import FxColour, FxItemBase, FxSource, live, source_check

TRACK = "4a"
# Track 4A: depth_fx.py and timewarp.py compile both types.
LIVE = True

DEPTH_KINDS = ("parallax", "ken_burns", "dof", "rack_focus", "fog", "light",
               "point_cloud", "push_through")
MOVE_PATHS = ("push_in", "pull_out", "drift_left", "drift_right", "rise",
              "fall", "orbit_left", "orbit_right")
FOCUS_ON = ("subject", "background", "near", "far", "depth")
TIME_KINDS = ("reverse", "echo", "posterize", "stutter", "interpolate",
              "remap")
REMAP_CURVES = ("ease_in", "ease_out", "ease_in_out", "hold_then_catch")
# Kinds that draw a whole new picture (a still in 2.5D, a point cloud on
# black): a window shorter than this over the full frame reads as a flash.
FLASH_WINDOW_S = 0.5
# Fields each depth kind may carry (anything else is refused by check:
# one item says one thing).
_DEPTH_FIELDS = {
    "parallax": {"source", "fit", "move", "strength"},
    "ken_burns": {"source", "fit", "move"},
    "dof": {"focus", "blur", "strength"},
    "rack_focus": {"focus", "focus_from", "rack_at", "rack_f", "blur"},
    "fog": {"colour", "strength", "focus"},
    "light": {"colour", "strength", "move"},
    "point_cloud": {"source", "colour", "density", "scan", "trails",
                    "backdrop", "move", "strength", "focus"},
    "push_through": {"text", "colour", "blur", "move"},
}
_OPTIONAL = ("source", "fit", "move", "focus", "focus_from", "rack_at",
             "rack_f", "blur", "strength", "colour", "density", "scan",
             "trails", "backdrop", "text")
_TIME_FIELDS = {
    "reverse": set(), "echo": {"echo"}, "posterize": {"frames"},
    "stutter": {"frames", "repeats"}, "interpolate": {"method"},
    "remap": {"curve"},
}
# Picture memory a reversed window holds (frames at 1080x1920 * 1.5 B/px):
# reverse buffers its window, so it is capped.
REVERSE_MAX_S = 3.0


class DepthMove(BaseModel):
    """The camera's path through the 2.5D scene (or the frame, for
    ken_burns). ``amount`` 0.05-1 is how far it travels (parallax
    strength); ``ease`` its velocity curve."""
    path: Literal["push_in", "pull_out", "drift_left", "drift_right",
                  "rise", "fall", "orbit_left", "orbit_right"] = "push_in"
    amount: float = Field(default=0.35, ge=0.05, le=1.0, allow_inf_nan=False)
    ease: Literal["linear", "in_out", "out"] = "in_out"


class DepthFocus(BaseModel):
    """What is sharp: the person (``subject``), what is behind them
    (``background``), the nearest / farthest plane, or a ``depth`` 0-1
    (1 = near)."""
    on: Literal["subject", "background", "near", "far", "depth"] = "subject"
    depth: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                   allow_inf_nan=False)

    @model_validator(mode="after")
    def _depth(self):
        if (self.on == "depth") != (self.depth is not None):
            raise ValueError("focus on 'depth' takes depth (0-1); the other "
                             "targets take none")
        return self


class EchoSpec(BaseModel):
    count: int = Field(default=4, ge=2, le=8)
    spacing_f: int = Field(default=2, ge=1, le=6)
    decay: float = Field(default=0.55, ge=0.2, le=0.9, allow_inf_nan=False)


class DepthFx(FxItemBase):
    """A depth device over [start, end) (see the module doc)."""
    STAGES: ClassVar[tuple] = ("world", "screen")
    COMPILER: ClassVar[str] = "depth_fx:DEPTH"

    type: Literal["depth"] = "depth"
    kind: Literal["parallax", "ken_burns", "dof", "rack_focus", "fog",
                  "light", "point_cloud", "push_through"]
    source: Optional[FxSource] = None
    fit: Literal["cover", "contain"] = "cover"
    move: Optional[DepthMove] = None
    focus: Optional[DepthFocus] = None
    focus_from: Optional[DepthFocus] = None
    rack_at: Optional[float] = Field(default=None, ge=0.0,
                                     allow_inf_nan=False)
    rack_f: int = Field(default=12, ge=2, le=48)
    blur: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                  allow_inf_nan=False)
    strength: Optional[float] = Field(default=None, ge=0.0, le=1.0,
                                      allow_inf_nan=False)
    colour: Optional[FxColour] = None
    density: Optional[float] = Field(default=None, ge=0.1, le=1.0,
                                     allow_inf_nan=False)
    scan: Optional[bool] = None
    trails: Optional[int] = Field(default=None, ge=0, le=8)
    backdrop: Optional[Literal["black", "dim", "program"]] = None
    text: Optional[str] = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def _kind_fields(self):
        k = self.kind
        allowed = _DEPTH_FIELDS[k]
        for f in _OPTIONAL:
            v = getattr(self, f)
            if f == "fit" and v == "cover":
                continue
            if f == "rack_f" and v == 12:
                continue
            if v is not None and f not in allowed:
                raise ValueError(f"{f} does not belong to a {k} depth effect")
        if self.source is not None and self.source.kind not in (
                "source", "asset", "program"):
            raise ValueError("a depth effect's source is the main video "
                             "(source, src_t), a project asset or the "
                             "program itself")
        if k in ("ken_burns",) and self.source is None:
            raise ValueError("ken_burns moves a still: name its source")
        if k == "rack_focus" and (self.focus is None or
                                  self.focus_from is None):
            raise ValueError("a rack focus names focus_from and focus")
        if k == "push_through" and not self.text:
            raise ValueError("push_through flies through a word: text")
        if k == "dof" and self.focus is None:
            self.focus = DepthFocus(on="subject")
        return self

    def check(self, ctx):
        source_check(self.source, ctx, f"fx {self.id}")
        if self.rack_at is not None and self.start is not None and \
                not (self.start <= self.rack_at <= self.end):
            raise ValueError(f"fx {self.id}: rack_at {self.rack_at:g}s is "
                             "outside the item's window")
        if self.kind in ("dof", "rack_focus", "fog", "light") and \
                ctx.canvas:
            raise ValueError(f"fx {self.id}: {self.kind} reads the depth of "
                             "footage (this program is a canvas)")

    def flash_times(self):
        """A new full-frame picture (a still, a cloud on black) held under
        half a second is a flash; everything that keeps the shot is not."""
        if self.start is None or self.end is None:
            return []
        replaces = self.kind in ("parallax", "ken_burns") or (
            self.kind == "point_cloud" and (self.backdrop or "black")
            == "black")
        if replaces and (self.end - self.start) < FLASH_WINDOW_S:
            return [self.start]
        return []

    def describe(self):
        bits = [self.kind]
        if self.move is not None:
            bits.append(f"{self.move.path} {self.move.amount:g}")
        if self.focus is not None:
            bits.append(f"focus {self.focus.on}")
        if self.kind == "rack_focus" and self.rack_at is not None:
            bits.append(f"lands {self.rack_at:g}s")
        if self.text:
            bits.append(f"'{self.text}'")
        return f"{super().describe()} ({', '.join(bits)})"


class TimeFx(FxItemBase):
    """A picture-only time device over [start, end)."""
    STAGES: ClassVar[tuple] = ("world",)
    COMPILER: ClassVar[str] = "timewarp:TIME"

    type: Literal["time"] = "time"
    kind: Literal["reverse", "echo", "posterize", "stutter", "interpolate",
                  "remap"]
    echo: Optional[EchoSpec] = None
    frames: Optional[int] = Field(default=None, ge=2, le=12)
    repeats: Optional[int] = Field(default=None, ge=2, le=8)
    method: Optional[Literal["auto", "minterpolate", "blend"]] = None
    curve: Optional[Literal["ease_in", "ease_out", "ease_in_out",
                            "hold_then_catch"]] = None

    @model_validator(mode="after")
    def _kind_fields(self):
        allowed = _TIME_FIELDS[self.kind]
        for f in ("echo", "frames", "repeats", "method", "curve"):
            if getattr(self, f) is not None and f not in allowed:
                raise ValueError(f"{f} does not belong to a {self.kind} "
                                 "time effect")
        if self.kind == "remap" and self.curve is None:
            self.curve = "ease_in_out"
        return self

    def check(self, ctx):
        if self.kind == "reverse" and self.start is not None and \
                self.end - self.start > REVERSE_MAX_S + 1e-6:
            raise ValueError(f"fx {self.id}: a reverse holds its whole "
                             f"window in memory: at most {REVERSE_MAX_S:g}s")

    def flash_times(self):
        return []

    def describe(self):
        extra = self.curve or (f"{self.frames}f" if self.frames else "")
        return f"{super().describe()} ({self.kind}{', ' + extra if extra else ''})"


FX_MEMBERS = (DepthFx, TimeFx) if live(LIVE, TRACK) else ()
