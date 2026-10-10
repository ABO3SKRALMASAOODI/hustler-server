"""Engine-only stage probes (track 0B; INTERFACES.md §4.1 tag ``fx_probe``).

A probe item drives one FX stage through the real path — validation, the
render-path strip, fxgraph.prepare, the stage hook, stitch windows, remap —
with a deliberately boring effect, so the seams can be proven bit-exact
(AT-0B-1/2/3/5) and fault-injected (AT-0C-3) without any creative device.

``AUTHORABLE = False``: it validates and renders, ``edit_batch`` refuses to
author it and ``apply_edit_batch`` never advertises it, so a probe can never
become a back-door creative device.

REGISTERED ONLY ON PROBE LANES (``VALMERA_FX_PROBE=1``): the probe test
processes, refbench and the fault suite. Production lanes leave it out, which
keeps the registry exactly as inert as the interface pass pinned it
(tests/test_w0_seams.py: no fx type registered, ``/health`` advertises no fx
feature). Flipping it to unconditional is one line once the round lead
re-pins those seam assertions.

Like every fx_schemas member this imports pydantic, the standard library and
its own package only (the backend loads schemas.py by path).
"""

import os
from typing import ClassVar, List, Literal, Optional

from pydantic import Field

from . import FX_STAGES, PICTURE_STAGES, FxItemBase

PROBE_ENV = "VALMERA_FX_PROBE"

# op -> the stages it can run at.
PROBE_OPS = {
    "noop": FX_STAGES,                 # identity through each stage's real plumbing
    "negate": PICTURE_STAGES,          # timeline path (enable on frame index)
    "negate_window": PICTURE_STAGES,   # split/trim/re-pad/overlay window path
    "region_negate": PICTURE_STAGES,   # precomputed gray rect mask + maskedmerge
    "blend_screen": PICTURE_STAGES,    # bbox-local gbrp blend
    "matte_view": PICTURE_STAGES,      # asks for the person matte (stub: absent)
    "dissolve": ("junction",),         # xfade over the source handles
    "gain": ("music_bus",),            # volume on one music item's bus
}


class FxProbe(FxItemBase):
    AUTHORABLE: ClassVar[bool] = False
    # fxgraph resolves the compiler from this string at render time
    # ("<worker module>:<object>"), so this package stays import-free.
    COMPILER: ClassVar[str] = "fxgraph:PROBE"

    type: Literal["fx_probe"] = "fx_probe"
    op: Literal["noop", "negate", "negate_window", "region_negate",
                "blend_screen", "matte_view", "dissolve", "gain"] = "noop"
    # region ops: [x0, y0, x1, y1] fractions of the frame
    rect: Optional[List[float]] = Field(default=None, min_length=4,
                                        max_length=4)
    # junction ops: frames each side of the cut
    frames: Optional[int] = Field(default=None, ge=1, le=60)
    # music_bus gain
    gain_db: Optional[float] = Field(default=None, ge=-60.0, le=12.0,
                                     allow_inf_nan=False)

    def check(self, ctx):
        allowed = PROBE_OPS[self.op]
        if self.stage not in allowed:
            raise ValueError(f"probe op {self.op} runs at "
                             f"{', '.join(allowed)}, not {self.stage}")
        if self.rect is not None:
            x0, y0, x1, y1 = self.rect
            if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
                raise ValueError("rect must be [x0, y0, x1, y1] fractions "
                                 "with x0 < x1 and y0 < y1")
        if self.op in ("region_negate", "blend_screen") and self.rect is None:
            raise ValueError(f"probe op {self.op} needs a rect")

    def describe(self):
        return f"{super().describe()} ({self.op})"


FX_MEMBERS = ((FxProbe,)
              if os.environ.get(PROBE_ENV, "").strip() == "1" else ())
