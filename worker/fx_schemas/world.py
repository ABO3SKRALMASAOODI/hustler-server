"""Into the world: EDL.fx tags ``track``, ``object``, ``plane``, ``light`` and
the typed slot ``MotionItem.anchor`` (Wave 3; devices/INTERFACES_W3.md §3-4).

* Definitions (``track`` 3A, ``object`` 3B, ``plane`` 3C) say WHERE
  something is in the footage: a seed at one SOURCE second, in SOURCE frame
  fractions, over the program window in which it matters. They draw
  nothing; the world compositor (world_layer.py) and the mask providers
  (``object:<id>``, ``plane:<id>``) read them. Stored on the source clock
  and in source fractions, so a reframe, a new aspect or a keep change
  never re-measures (ROADMAP A4).
* ``light`` (3D) is a light device on the person matte: trails, a figure
  dissolving into light, a rim.
* ``MotionItem.anchor`` attaches a motion graphic to the world: a face part,
  the head, a hand part, a body joint, a tracked point, an object or a
  plane — and says what passes in front of it.

Every authoring rule lives here (field constraints, validators, ``check``),
never only in a verb: a stale MCP client writing the same items through
apply_edit_batch is validated exactly like the department verbs.
Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

import re
from typing import ClassVar, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from . import FxColour, FxItemBase, live

TRACK = "w3"
# Wave 3 ships its definitions, the light device and the anchor slot in
# one commit with their compilers, cards and verbs (INTERFACES_W3.md §0.5).
LIVE = True

FACE_PARTS = ("eye_l", "eye_r", "iris_l", "iris_r", "nose", "mouth",
              "chin", "forehead", "ear_l", "ear_r", "brow_l", "brow_r")
HAND_PARTS = ("index_tip", "thumb_tip", "middle_tip", "palm", "wrist",
              "pinch", "frame")
POSE_JOINTS = ("nose", "shoulder_l", "shoulder_r", "chest", "elbow_l",
               "elbow_r", "wrist_l", "wrist_r", "hip_l", "hip_r", "knee_l",
               "knee_r", "ankle_l", "ankle_r")
REF_KINDS = ("track", "object", "plane")
SURFACES = ("floor", "wall", "desk", "table", "screen", "ceiling", "other")
_ID = r"[A-Za-z0-9_-]{1,80}"
TO_RE = re.compile(
    r"^(screen|head|face|face\.(" + "|".join(FACE_PARTS) + r")"
    r"|hand\.(" + "|".join(HAND_PARTS) + r")"
    r"|pose\.(" + "|".join(POSE_JOINTS) + r")"
    r"|(track|object|plane):" + _ID + r")$")
OCCLUDER_RE = re.compile(r"^(person|hands|object:" + _ID + r")$")
# Bound of 3B (ROADMAP): an object is measured over at most this window.
OBJECT_MAX_S = 6.0


def _xy(v, what):
    if len(v) != 2 or not all(-0.5 <= float(c) <= 1.5 for c in v):
        raise ValueError(f"{what} is [x, y] in frame fractions")
    return [float(c) for c in v]


def _box(v, what):
    if len(v) != 4:
        raise ValueError(f"{what} is [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (float(c) for c in v)
    if not (-0.5 <= x0 < x1 <= 1.5 and -0.5 <= y0 < y1 <= 1.5):
        raise ValueError(f"{what} is [x0, y0, x1, y1] frame fractions with "
                         "x0 < x1 and y0 < y1")
    return [x0, y0, x1, y1]


def _src_check(src_t, ctx, what):
    if ctx.source_s is None:
        raise ValueError(f"{what} needs a main video (a canvas program has "
                         "no footage to find it in)")
    if float(src_t) >= float(ctx.source_s):
        raise ValueError(f"{what}: src_t {float(src_t):g}s is past the end "
                         f"of the main video ({float(ctx.source_s):g}s)")


class _Definition(FxItemBase):
    STAGES: ClassVar[tuple] = ("world",)
    COMPILER: ClassVar[str] = "world_defs:COMPILER"
    PREFIX: ClassVar[str] = ""

    label: Optional[str] = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _window(self):
        if self.start is None or self.end is None:
            raise ValueError(f"a {self.type} definition needs start and end "
                             "(the program window where it is used)")
        if self.device is not None and not str(self.device).startswith(
                self.PREFIX):
            raise ValueError(f"a {self.type} item's device is a "
                             f"{self.PREFIX}* card, not {self.device!r}")
        return self

    def flash_times(self):
        return []


class TrackSeed(BaseModel):
    """Where a tracked point (or a small region) is at SOURCE second
    ``src_t``: ``x``, ``y`` or ``box``, SOURCE frame fractions."""
    src_t: float = Field(ge=0.0, allow_inf_nan=False)
    x: Optional[float] = Field(default=None, ge=-0.5, le=1.5,
                               allow_inf_nan=False)
    y: Optional[float] = Field(default=None, ge=-0.5, le=1.5,
                               allow_inf_nan=False)
    box: Optional[List[float]] = None

    @model_validator(mode="after")
    def _fields(self):
        if self.box is not None:
            self.box = _box(self.box, "seed.box")
            if self.x is not None or self.y is not None:
                raise ValueError("a seed is x, y or box, not both")
        elif self.x is None or self.y is None:
            raise ValueError("a seed needs x and y (or a box)")
        return self


class TrackFx(_Definition):
    """A point tracked through its shot (``track.points``): a doodle's tip,
    a callout's target, a tracked region's centre. ``radius`` (frame
    fractions) is the patch the tracker follows."""
    PREFIX: ClassVar[str] = "anchor_"
    type: Literal["track"] = "track"
    seed: TrackSeed
    radius: float = Field(default=0.012, ge=0.003, le=0.1,
                          allow_inf_nan=False)

    def check(self, ctx):
        _src_check(self.seed.src_t, ctx, f"track {self.id}")

    def describe(self):
        return (f"{super().describe()} (point"
                f"{' ' + self.label if self.label else ''})")


class ObjectPrompt(BaseModel):
    """The object at SOURCE second ``src_t``: a ``box`` and/or ``points``
    [[x, y, 1|0], ...] (1 = on the object, 0 = not), SOURCE fractions."""
    src_t: float = Field(ge=0.0, allow_inf_nan=False)
    box: Optional[List[float]] = None
    points: Optional[List[List[float]]] = Field(default=None, min_length=1,
                                                max_length=5)

    @model_validator(mode="after")
    def _fields(self):
        if self.box is None and not self.points:
            raise ValueError("an object prompt needs a box or points")
        if self.box is not None:
            self.box = _box(self.box, "prompt.box")
        for p in self.points or []:
            if len(p) not in (2, 3):
                raise ValueError("a prompt point is [x, y] or [x, y, 1|0]")
            _xy(p[:2], "a prompt point")
            if len(p) == 3 and p[2] not in (0, 1):
                raise ValueError("a prompt point's label is 1 (object) or 0")
        return self


class ObjectFx(_Definition):
    """An object segmented through its window (``objects.sam2``): the laptop,
    the bottle, the boom mic. Its program-space mask is ``object:<id>``;
    graphics name it as an occluder (``anchor.occluders``) or attach to it.
    ``depth`` auto measures in front of / behind the person from the mattes
    (a person-matte hole shaped like the object = in front)."""
    PREFIX: ClassVar[str] = "object_"
    type: Literal["object"] = "object"
    prompt: ObjectPrompt
    depth: Literal["auto", "front", "behind"] = "auto"

    @model_validator(mode="after")
    def _bounds(self):
        if self.start is not None and self.end is not None and \
                float(self.end) - float(self.start) > OBJECT_MAX_S + 1e-6:
            raise ValueError(f"an object is measured over at most "
                             f"{OBJECT_MAX_S:g} s (split a longer window "
                             "into two objects with their own prompts)")
        return self

    def check(self, ctx):
        _src_check(self.prompt.src_t, ctx, f"object {self.id}")

    def describe(self):
        return (f"{super().describe()} (object"
                f"{' ' + self.label if self.label else ''})")


class PlaneFx(_Definition):
    """A surface: ``quad`` TL, TR, BR, BL in the plane's own orientation
    (TL->TR along its width), SOURCE fractions at SOURCE second ``src_t``.
    ``motion`` auto measures whether the camera moves (``camera.motion``)
    and tracks the plane (``planes.h``) only when it does."""
    PREFIX: ClassVar[str] = "plane_"
    type: Literal["plane"] = "plane"
    src_t: float = Field(ge=0.0, allow_inf_nan=False)
    quad: List[List[float]] = Field(min_length=4, max_length=4)
    surface: Literal["floor", "wall", "desk", "table", "screen", "ceiling",
                     "other"] = "other"
    motion: Literal["auto", "static", "tracked"] = "auto"

    @field_validator("quad")
    @classmethod
    def _quad(cls, v):
        q = [_xy(p, "a quad corner") for p in v]
        # a usable quad: convex, non-degenerate, corners in order
        area = 0.0
        sign = 0
        for i in range(4):
            ax, ay = q[i]
            bx, by = q[(i + 1) % 4]
            cx, cy = q[(i + 2) % 4]
            cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
            if abs(cross) < 1e-9:
                raise ValueError("a plane quad has three corners in a line")
            s = 1 if cross > 0 else -1
            if sign and s != sign:
                raise ValueError("a plane quad must be convex, corners in "
                                 "order TL, TR, BR, BL")
            sign = s
            area += ax * by - bx * ay
        if abs(area) / 2.0 < 0.0004:
            raise ValueError("a plane quad is too small (under 0.04% of the "
                             "frame)")
        return q

    def check(self, ctx):
        _src_check(self.src_t, ctx, f"plane {self.id}")

    def describe(self):
        return (f"{super().describe()} ({self.surface} plane"
                f"{' ' + self.label if self.label else ''})")


class LightFx(FxItemBase):
    """Light on the person matte (3D). kind:
      trail     the subject leaves light trails (lagfun, screen-blended)
      dissolve  the subject turns into light over ``duration_s`` from start
                (glow up, then gone; the room stays) — pair it with
                el_particles embers emitted from the subject's edge
      rim       a rim light traced round the subject's edge
    ``colour`` the light's colour; ``intensity`` 0..1; ``decay`` the
    trail's persistence (0.80 short .. 0.97 long)."""
    STAGES: ClassVar[tuple] = ("world", "screen")
    COMPILER: ClassVar[str] = "fx_light:COMPILER"

    type: Literal["light"] = "light"
    kind: Literal["trail", "dissolve", "rim"]
    colour: FxColour = "white"
    intensity: float = Field(default=0.8, ge=0.0, le=1.0,
                             allow_inf_nan=False)
    decay: Optional[float] = Field(default=None, ge=0.5, le=0.99,
                                   allow_inf_nan=False)
    duration_s: Optional[float] = Field(default=None, ge=0.2, le=10.0,
                                        allow_inf_nan=False)

    @model_validator(mode="after")
    def _fields(self):
        if self.start is None or self.end is None:
            raise ValueError("a light device needs start and end")
        if self.decay is not None and self.kind != "trail":
            raise ValueError("decay belongs to a trail")
        if self.duration_s is not None and self.kind != "dissolve":
            raise ValueError("duration_s belongs to a dissolve")
        if self.device is not None and not str(self.device).startswith(
                "light_"):
            raise ValueError(f"a light item's device is a light_* card, not "
                             f"{self.device!r}")
        return self

    def flash_times(self):
        # light stays on the subject: never a full-frame flash
        return []

    def describe(self):
        return f"{super().describe()} (light {self.kind})"


class MotionAnchor(BaseModel):
    """``MotionItem.anchor``: the graphic attached to the world.

    to         screen | head | face | face.<part> | hand.<part> |
               pose.<joint> | track:<fx id> | object:<fx id> | plane:<fx id>
    which      any | left | right | 0..7 — which face, hand or person
               (left/right ON SCREEN)
    lock       world (rides the camera) | screen (ignores camera moves)
    pin        the point of the graphic's box set on the anchor ([0.5, 0.5]
               = its centre)
    offset     [dx, dy] from the anchor: in anchor sizes (face height, hand
               span, plane fraction) when follow has "scale", else frame
               fractions
    follow     what the graphic takes from the anchor: position (always),
               scale, rotation
    place      planes only: [u0, v0, u1, v1] on the plane (default fitted)
    occluders  what passes in front of the graphic: person, hands,
               object:<fx id>. None = the default: a plane on a floor, desk
               or table puts the person in front; anything else nothing
    shadow     planes only: real shadows darken the graphic
    smooth     0..1 One-Euro smoothing (0 raw, 1 very steady)
    lost       tracking lost: hold (last pose), hide (fade out), static
               (where it was written)
    """
    to: str = Field(min_length=3, max_length=90)
    which: Union[Literal["any", "left", "right"], int] = "any"
    lock: Literal["world", "screen"] = "world"
    pin: Optional[List[float]] = None
    offset: Optional[List[float]] = None
    follow: Optional[List[Literal["position", "scale", "rotation"]]] = None
    place: Optional[List[float]] = None
    occluders: Optional[List[str]] = Field(default=None, max_length=6)
    shadow: Optional[bool] = None
    smooth: float = Field(default=0.5, ge=0.0, le=1.0, allow_inf_nan=False)
    lost: Literal["hold", "hide", "static"] = "hold"

    @model_validator(mode="after")
    def _fields(self):
        if not TO_RE.match(self.to):
            raise ValueError(
                f"anchor.to {self.to!r}: screen, head, face, face.<part> "
                f"({', '.join(FACE_PARTS)}), hand.<part> "
                f"({', '.join(HAND_PARTS)}), pose.<joint> "
                f"({', '.join(POSE_JOINTS)}), track:<id>, object:<id> or "
                "plane:<id>")
        if isinstance(self.which, int) and not 0 <= self.which <= 7:
            raise ValueError("anchor.which is any, left, right or 0..7")
        if self.pin is not None:
            if len(self.pin) != 2 or not all(-1.0 <= float(v) <= 2.0
                                             for v in self.pin):
                raise ValueError("anchor.pin is [x, y] fractions of the "
                                 "graphic's box")
        if self.offset is not None:
            if len(self.offset) != 2 or not all(-3.0 <= float(v) <= 3.0
                                                for v in self.offset):
                raise ValueError("anchor.offset is [dx, dy] (-3..3)")
        is_plane = self.to.startswith("plane:")
        if self.place is not None:
            if not is_plane:
                raise ValueError("anchor.place belongs to a plane anchor")
            self.place = _box(self.place, "anchor.place")
        if self.shadow is not None and not is_plane:
            raise ValueError("anchor.shadow belongs to a plane anchor")
        for o in self.occluders or []:
            if not OCCLUDER_RE.match(str(o)):
                raise ValueError(f"anchor.occluders: {o!r} is not person, "
                                 "hands or object:<id>")
        if self.follow is not None and "position" not in self.follow:
            self.follow = ["position"] + list(self.follow)
        return self

    def refs(self):
        """[(kind, fx id)] of the definitions this anchor names."""
        out = []
        m = re.match(r"^(track|object|plane):(.+)$", self.to)
        if m:
            out.append((m.group(1), m.group(2)))
        for o in self.occluders or []:
            if str(o).startswith("object:"):
                out.append(("object", str(o).split(":", 1)[1]))
        return out

    def check(self, edl, item=None):
        """EDL-level: every definition this anchor names exists, with the
        right type (schemas.validate_edl calls this)."""
        have = {getattr(f, "id", None): getattr(f, "type", None)
                for f in getattr(edl, "fx", None) or []}
        for kind, fid in self.refs():
            if fid not in have:
                raise ValueError(
                    f"names {kind} {fid!r}, which is not in this edit "
                    "(add the definition first, or detach: anchor null)")
            if have[fid] != kind:
                raise ValueError(f"{fid!r} is a {have[fid]}, not a {kind}")


_ON = live(LIVE, TRACK)
FX_MEMBERS = (TrackFx, ObjectFx, PlaneFx, LightFx) if _ON else ()
FIELD_TYPES = {"MotionItem.anchor": MotionAnchor} if _ON else {}
