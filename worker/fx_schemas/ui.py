"""UI scene media: EDL.fx tag ``ui_media`` (track 4B; worker/devices/
INTERFACES_W4.md §3.4).

Real footage inside a UI scene: a recording plays in a ``media`` slot of a
``ui_scene`` motion item (the scene graph solved by worker/ui_scene.py).
The compiler draws the footage into the slot's solved rectangle, frame by
frame (it follows every morph), with the slot's rounded corners, at the
``pre_captions`` stage — UNDER the scene's chrome, which leaves the slot
open. ``check`` refuses an unknown motion item, a motion item that is not a
UI scene, or a slot its graph does not have.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

from typing import ClassVar, Literal

from pydantic import Field

from . import FxItemBase, FxSource, live, source_check

TRACK = "4b"
# Track 4B: ui_media.py compiles the type.
LIVE = True


class UiMediaFx(FxItemBase):
    """Footage in a UI scene's media slot over [start, end)."""
    STAGES: ClassVar[tuple] = ("pre_captions",)
    COMPILER: ClassVar[str] = "ui_media:UI_MEDIA"

    type: Literal["ui_media"] = "ui_media"
    motion_id: str = Field(min_length=1, max_length=80)
    slot: str = Field(min_length=1, max_length=80)
    source: FxSource
    fit: Literal["cover", "contain"] = "cover"

    def check(self, ctx):
        if self.source.kind not in ("asset", "source"):
            raise ValueError(f"fx {self.id}: a slot plays a project asset or "
                             "the main video (source, src_t)")
        source_check(self.source, ctx, f"fx {self.id}")
        mo = None
        for m in getattr(ctx.edl, "motion", None) or []:
            if getattr(m, "id", None) == self.motion_id:
                mo = m
                break
        if mo is None:
            raise ValueError(f"fx {self.id}: no motion item "
                             f"'{self.motion_id}'")
        if getattr(mo, "template", None) != "ui_scene":
            raise ValueError(f"fx {self.id}: '{self.motion_id}' is not a UI "
                             "scene")
        graph = (getattr(mo, "params", None) or {}).get("graph") or {}
        slots = {o.get("id") for o in graph.get("objects") or []
                 if isinstance(o, dict) and o.get("kind") == "media"}
        if self.slot not in slots:
            raise ValueError(f"fx {self.id}: the UI scene has no media slot "
                             f"'{self.slot}' (slots: "
                             f"{', '.join(sorted(slots)) or 'none'})")
        if self.start is None or self.end is None or \
                self.start < float(mo.start) - 1e-6 or \
                self.end > float(mo.end) + 1e-6:
            raise ValueError(f"fx {self.id}: the footage plays inside its "
                             "scene's window")

    def flash_times(self):
        return []

    def describe(self):
        return f"{super().describe()} (slot {self.slot} of " \
               f"{self.motion_id})"


FX_MEMBERS = (UiMediaFx,) if live(LIVE, TRACK) else ()
