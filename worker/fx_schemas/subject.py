"""Subject FX and background operations: EDL.fx tags ``subject`` and
``background`` (track 1B; worker/devices/INTERFACES_W1.md §3.2).

Pinned by the Wave 1 interface pass. Track 1B owns this file: it may add
optional fields (default None, so stored items keep validating), tighten a
``check``, implement ``flash_times`` and flip ``LIVE``; renaming or removing
a pinned field or kind is a round-lead change.

Everything bounded by the person matte lives here, on either side of it
(``scope``): a fill, a poster field, a glow, an outline, a negate or grade
inside or outside the subject, colour isolation by the matte, echoes and
clones, and the background swapped behind the subject. Both types compile
from the program matte M and the picture P (``fxgraph.request_matte`` /
``program_matte``) at the ``world`` stage (rides zooms) or ``screen``.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from . import (FxColour, FxItemBase, FxRegion, FxSource, live,
               region_check, source_check)

TRACK = "1b"
# Track 1B flips this when subject_fx.py compiles both types.
LIVE = True

# flash_safety reading (FxItemBase.flash_times): a picture change that
# returns within this long is a flash (an on/off pair inside one Harding
# second, Wave 0's flash_risk counter). A strobe swaps every ``hold_f``
# PROGRAM frames, so how often it flashes depends on the render rate, which
# flash_times does not know: its changes are counted at the FASTEST rate a
# program renders at (renderer caps finals at 60 fps). A 60p final then
# never passes the limit uncounted (at an assumed 30, hold_f 12 read 2.5
# flashes/s where a 59.94 final plays 5); a 25/30 fps program is counted
# conservatively, and a strobe is usually asked for (an opt-in) anyway.
SHORT_FLASH_S = 1.0
ASSUMED_FPS = 60.0

SUBJECT_KINDS = ("fill", "poster", "glow", "outline", "negate", "exposure",
                 "isolate", "echo")
BACKGROUND_KINDS = ("solid", "gradient", "blur", "radial_blur", "source",
                    "strobe")
ECHO_MODES = ("echo", "clone", "twin", "trail")


class SubjectEcho(BaseModel):
    """Echo / clone / twin / trail: copies of the subject from ``offsets_f``
    frames EARLIER (alphamerged with the matte at t - k), each at
    ``opacity`` (decaying by ``decay`` per copy), optionally tinted."""
    mode: Literal["echo", "clone", "twin", "trail"] = "echo"
    offsets_f: List[int] = Field(min_length=1, max_length=8)
    opacity: float = Field(default=0.6, ge=0.05, le=1.0, allow_inf_nan=False)
    decay: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    tint: Optional[FxColour] = None

    @model_validator(mode="after")
    def _offsets(self):
        if any(not 1 <= k <= 240 for k in self.offsets_f):
            raise ValueError("echo offsets_f are 1..240 frames")
        if len(set(self.offsets_f)) != len(self.offsets_f):
            raise ValueError("echo offsets_f must differ")
        return self


class SubjectFx(FxItemBase):
    """One subject effect over [start, end).

    kind      fill      subject flat ``colour`` (white | black | accent |
                        #hex); ``decay_f`` frames of exponential fall-off
              poster    subject on a flat ``field`` colour; ``keep_footage``
                        False draws a flat silhouette in ``colour``
              glow      dilated, blurred matte tinted ``colour``, screened;
                        ``radius`` is a share of frame height
              outline   a ``width_px`` stroke in ``colour`` around the matte
              negate    the picture negated inside (scope subject) or
                        outside (scope background) the matte
              exposure  ``ev`` / ``saturation`` inside or outside
              isolate   colour kept on ``scope`` and the rest mono;
                        ``hue`` ('auto' | #hex) joins a hue key from 1E by
                        ``hue_op`` (union | intersect)
              echo      ``echo`` copies of the subject from earlier frames
    scope     which side of the matte the effect changes
    intensity 0..1 mix of the effect over the picture
    """
    STAGES: ClassVar[tuple] = ("world", "screen")
    COMPILER: ClassVar[str] = "subject_fx:COMPILER"

    type: Literal["subject"] = "subject"
    kind: Literal["fill", "poster", "glow", "outline", "negate", "exposure",
                  "isolate", "echo"]
    scope: Literal["subject", "background"] = "subject"
    colour: Optional[FxColour] = None
    intensity: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    decay_f: int = Field(default=0, ge=0, le=24)
    field: Optional[FxColour] = None
    keep_footage: bool = True
    radius: Optional[float] = Field(default=None, ge=0.002, le=0.15,
                                    allow_inf_nan=False)

    @field_validator("radius", mode="before")
    @classmethod
    def _radius_in_pixels(cls, v):
        # The radius is a fraction of the frame height, but agents write it
        # in pixels (radius: 30 was refused with a pydantic error, 25 Oct
        # 2026). A value above 1 is pixels of a 1080p frame, kept in range.
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1:
            return max(0.002, min(0.15, float(v) / 1080.0))
        return v
    width_px: Optional[float] = Field(default=None, ge=0.5, le=24.0,
                                      allow_inf_nan=False)
    ev: Optional[float] = Field(default=None, ge=-4.0, le=4.0,
                                allow_inf_nan=False)
    saturation: Optional[float] = Field(default=None, ge=0.0, le=2.0,
                                        allow_inf_nan=False)
    hue: Optional[str] = Field(default=None, max_length=7)
    hue_op: Optional[Literal["union", "intersect"]] = None
    echo: Optional[SubjectEcho] = None
    region: Optional[FxRegion] = None

    @model_validator(mode="after")
    def _kind_fields(self):
        k = self.kind
        if k == "poster" and self.field is None:
            raise ValueError("a poster names its field colour")
        if k == "echo" and self.echo is None:
            raise ValueError("an echo names its echo offsets")
        if k != "echo" and self.echo is not None:
            raise ValueError("echo belongs to kind echo")
        if k != "poster" and (self.field is not None
                              or not self.keep_footage):
            raise ValueError("field and keep_footage belong to kind poster")
        if self.hue is not None:
            if k != "isolate":
                raise ValueError("hue belongs to kind isolate")
            h = self.hue.strip().lower()
            if h != "auto" and not (len(h) == 7 and h.startswith("#") and
                                    all(c in "0123456789abcdef"
                                        for c in h[1:])):
                raise ValueError("hue is 'auto' or #rrggbb")
            self.hue = h
        if self.hue_op is not None and self.hue is None:
            raise ValueError("hue_op needs a hue")
        if k in ("fill", "poster", "glow", "outline", "echo") and \
                self.scope != "subject":
            raise ValueError(f"kind {k} draws the subject (scope subject)")
        if (self.ev is not None or self.saturation is not None) and \
                k != "exposure":
            raise ValueError("ev and saturation belong to kind exposure")
        if self.radius is not None and k != "glow":
            raise ValueError("radius belongs to kind glow")
        if self.width_px is not None and k != "outline":
            raise ValueError("width_px belongs to kind outline")
        if self.decay_f and k != "fill":
            raise ValueError("decay_f belongs to kind fill")
        return self

    def check(self, ctx):
        if ctx.canvas:
            raise ValueError("subject effects need footage with a person "
                             "(this program is a canvas)")
        region_check(self.region, ctx, f"fx {self.id}")

    def flash_times(self):
        """A fill is a flash of the subject (counted, conservatively, as a
        full-frame flash: a close subject covers most of a 9:16 frame); a
        short negate, poster or strong exposure change is one too. Glows,
        outlines, isolation and echoes change no large area abruptly."""
        if self.start is None or self.end is None:
            return []
        short = (self.end - self.start) <= SHORT_FLASH_S + 1e-9
        k = self.kind
        if k == "fill":
            return [self.start]
        if k in ("negate", "poster") and short:
            return [self.start]
        if k == "exposure" and short and abs(self.ev or 0.0) >= 1.0:
            return [self.start]
        return []

    def describe(self):
        bits = [self.kind, self.scope]
        if self.colour:
            bits.append(self.colour)
        if self.kind == "echo" and self.echo is not None:
            bits.append(f"{self.echo.mode} -" + ",".join(
                str(k) for k in self.echo.offsets_f) + "f")
        if self.hue:
            bits.append(f"hue {self.hue}")
        if self.device:
            bits.append(self.device)
        return f"{super().describe()} ({', '.join(bits)})"


class BackgroundFx(FxItemBase):
    """The background behind the subject over [start, end).

    kind  solid | gradient (``colour`` → ``colour2`` at ``angle``) |
          blur | radial_blur (``radius``) | source (``source``: a shot, an
          asset or a screen recording) | strobe (``sources`` swapped every
          ``hold_f`` frames)
    hold_subject  the subject cutout frozen at program second ``hold_t``
                  (default ``start``) while the background changes under it
                  (several items sharing ``hold_t`` keep one frozen frame)
    """
    STAGES: ClassVar[tuple] = ("world", "screen")
    COMPILER: ClassVar[str] = "subject_fx:COMPILER"

    type: Literal["background"] = "background"
    kind: Literal["solid", "gradient", "blur", "radial_blur", "source",
                  "strobe"]
    colour: Optional[FxColour] = None
    colour2: Optional[FxColour] = None
    angle: Optional[float] = Field(default=None, ge=0.0, lt=360.0,
                                   allow_inf_nan=False)
    radius: Optional[float] = Field(default=None, ge=0.002, le=0.2,
                                    allow_inf_nan=False)
    source: Optional[FxSource] = None
    sources: Optional[List[FxSource]] = Field(default=None, min_length=2,
                                              max_length=16)
    hold_f: int = Field(default=2, ge=1, le=12)
    hold_subject: bool = False
    hold_t: Optional[float] = Field(default=None, ge=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _kind_fields(self):
        k = self.kind
        if k in ("solid", "gradient") and self.colour is None:
            raise ValueError(f"a {k} background names its colour")
        if k == "gradient" and self.colour2 is None:
            raise ValueError("a gradient background names colour2")
        if k != "gradient" and (self.colour2 is not None
                                or self.angle is not None):
            raise ValueError("colour2 and angle belong to a gradient")
        if k == "source" and self.source is None:
            raise ValueError("a source background names its source")
        if k != "source" and self.source is not None:
            raise ValueError("source belongs to kind source")
        if k == "strobe" and not self.sources:
            raise ValueError("a strobe background names its sources")
        if k != "strobe" and self.sources is not None:
            raise ValueError("sources belong to kind strobe")
        if self.radius is not None and k not in ("blur", "radial_blur"):
            raise ValueError("radius belongs to a blur")
        if self.hold_t is not None and not self.hold_subject:
            raise ValueError("hold_t needs hold_subject")
        return self

    def check(self, ctx):
        if ctx.canvas:
            raise ValueError("a background swap needs footage with a person "
                             "(this program is a canvas)")
        for src in [self.source] + list(self.sources or []):
            source_check(src, ctx, f"fx {self.id}")
        if self.hold_t is not None and (
                ctx.render_fragment
                or self.hold_t >= float(ctx.program_s)):
            # hold_t is a PROGRAM second, and only start/end follow the
            # program when it changes: a keep edit re-anchors them
            # (timeline._remap_fx) and a stitched or proof window rebases
            # them onto its own clock (stitch._window_fx), never hold_t. A
            # hold_t past the end would then refuse every later write, and
            # inside a window it names another moment: the subject is held
            # on the item's own first frame instead (the default).
            self.hold_t = None

    def flash_times(self):
        """A plate strobe changes most of the frame on every swap: each
        image change is a flash (counted at ASSUMED_FPS, the fastest render
        rate). Any other background swap shorter than SHORT_FLASH_S is one
        flash."""
        if self.start is None or self.end is None:
            return []
        if self.kind == "strobe":
            step = self.hold_f / ASSUMED_FPS
            out, t = [], self.start
            while t < self.end - 1e-9:
                out.append(round(t, 4))
                t += step
            return out
        if (self.end - self.start) <= SHORT_FLASH_S + 1e-9:
            return [self.start]
        return []

    def describe(self):
        bits = [self.kind]
        if self.hold_subject:
            bits.append("subject held"
                        + (f" @{self.hold_t:g}s" if self.hold_t is not None
                           else ""))
        if self.device:
            bits.append(self.device)
        return f"{super().describe()} ({', '.join(bits)})"


FX_MEMBERS = (SubjectFx, BackgroundFx) if live(LIVE, TRACK) else ()
