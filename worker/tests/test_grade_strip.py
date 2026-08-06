"""Round 91 — the grade contact strip: color-only iterations stop paying for
full renders.

Job 2647 spent 409 of its 549 seconds running FIVE full preview renders of
the same ~80s program while tuning a look — every iteration re-encoded every
frame to change a per-pixel color map, then the agent judged six tiles of the
result. render_preview now short-circuits a COLOR-ONLY change into a ~2s
contact strip: the same program moments pulled from the proxy with the NEW
color applied to the stills. The full preview still renders exactly once —
when the model asks again without touching the color, or at turn end via the
auto-render.

Pins:
  * edl_diff.color_only_change — the gate. Grade/grade_custom-only diffs
    pass; anything structural (keep, zooms, stylize, texts, captions) or a
    no-change diff fails.
  * _grade_chain_of mirrors the render's chain (preset first, then custom).
  * the shortcut's own gates: blind agent -> None, no previous render ->
    None, same chain twice -> None (settled: the model gets its render),
    canvas program -> None.
  * end to end with a real proxy: the strip delivers frames, sets
    last_strip_chain, and the returned text says the full preview is NOT
    rendered yet.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import edl_diff                                                # noqa: E402
import llm                                                     # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


BASE = {"keep": [[0.0, 8.0]],
        "effects": {"grade": "warm",
                    "zooms": [{"id": "z1", "start": 1.0, "end": 3.0,
                               "strength": 0.4}]}}


def with_fx(**fx):
    e = {"keep": [[0.0, 8.0]],
         "effects": dict(BASE["effects"], **fx)}
    return e


print("== 1. color_only_change ==")

check("grade swap alone passes",
      edl_diff.color_only_change(BASE, with_fx(grade="cinematic")))
check("grade_custom added passes",
      edl_diff.color_only_change(BASE, with_fx(
          grade_custom={"temperature": 0.4})))
check("grade removed passes",
      edl_diff.color_only_change(BASE, with_fx(grade=None)))
check("keep change fails",
      not edl_diff.color_only_change(
          BASE, dict(with_fx(grade="cinematic"), keep=[[0.0, 6.0]])))
check("zoom change fails",
      not edl_diff.color_only_change(BASE, with_fx(
          grade="cinematic",
          zooms=[{"id": "z1", "start": 1.0, "end": 4.0, "strength": 0.4}])))
check("stylize change fails (strip can't show temporal effects)",
      not edl_diff.color_only_change(BASE, with_fx(
          grade="cinematic", stylize=[{"kind": "shake", "intensity": 0.5}])))
check("identical EDLs fail (nothing changed)",
      not edl_diff.color_only_change(BASE, BASE))

print("== 2. the chain mirror ==")

check("preset only",
      agent_tools._grade_chain_of({"effects": {"grade": "vibrant"}})
      == "eq=saturation=1.35:contrast=1.08")
check("preset + custom composes in render order",
      agent_tools._grade_chain_of(
          {"effects": {"grade": "vibrant",
                       "grade_custom": {"contrast": 1.1}}})
      == "eq=saturation=1.35:contrast=1.08,eq=contrast=1.100")
check("no color -> empty",
      agent_tools._grade_chain_of({"effects": {}}) == "")


class StripCtx:
    """Just enough ToolContext for _grade_strip_shortcut."""

    def __init__(self, workdir, proxy, prev_edl, new_edl, seeing=True):
        self.workdir = workdir
        self._proxy = proxy
        self.direct_sight = seeing
        self.sight_out = False
        self.agent_model = "test-model"
        self.db = self
        self.project_id = 1
        self.last_preview = {"edl_version": 1}
        self._prev = {"version": 1, "json": prev_edl}
        self._row = {"version": 2, "json": new_edl}
        self.strip_count = 0
        self.last_strip_chain = None
        self.pending_images = []

    def run(self, fn, *a, **k):
        name = getattr(fn, "__name__", "")
        if name == "get_edl_version":
            return self._prev
        return None

    def proxy_path(self):
        if not self._proxy:
            raise RuntimeError("no proxy")
        return self._proxy


print("== 3. shortcut gates and delivery ==")

if not HAVE_FFMPEG:
    print("  -- skipped (no ffmpeg on this machine)")
else:
    d = tempfile.mkdtemp(prefix="gstrip_")
    proxy = os.path.join(d, "proxy.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=9",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", proxy], check=True)

    new = with_fx(grade="cinematic")

    # Real seeing-agent path is gated on llm.agent_sees; force it on for the
    # test, whatever model name the env carries.
    _orig_sees = llm.agent_sees
    llm.agent_sees = lambda m: True
    try:
        ctx = StripCtx(d, proxy, BASE, new, seeing=False)
        check("blind agent falls through to the real render",
              agent_tools._grade_strip_shortcut(ctx, ctx._row) is None)

        ctx = StripCtx(d, proxy, BASE, new)
        ctx.last_preview = None
        ctx_run = ctx.run

        def run_no_prev(fn, *a, **k):
            return None
        ctx.run = run_no_prev
        check("no previous render falls through",
              agent_tools._grade_strip_shortcut(ctx, ctx._row) is None)
        ctx.run = ctx_run

        ctx = StripCtx(d, proxy, BASE,
                       dict(new, keep=[[0.0, 6.0]]))
        check("structural change falls through",
              agent_tools._grade_strip_shortcut(ctx, ctx._row) is None)

        ctx = StripCtx(d, proxy, BASE, {"effects": {"grade": "cinematic"}})
        check("canvas / keepless program falls through",
              agent_tools._grade_strip_shortcut(ctx, ctx._row) is None)

        ctx = StripCtx(d, proxy, BASE, new)
        out = agent_tools._grade_strip_shortcut(ctx, ctx._row)
        check("strip delivered for a color-only change", out is not None)
        check("strip says the full preview is NOT rendered yet",
              "NOT rendered" in out)
        check("strip queued a picture for the agent's eyes",
              len(ctx.pending_images) == 1)
        check("strip remembers its chain", ctx.last_strip_chain
              == agent_tools._grade_chain_of(new))
        check("strip counted", ctx.strip_count == 1)

        out2 = agent_tools._grade_strip_shortcut(ctx, ctx._row)
        check("same colors again -> None (settled, render for real)",
              out2 is None)
    finally:
        llm.agent_sees = _orig_sees
        shutil.rmtree(d, ignore_errors=True)

print(f"\nALL {PASS} CHECKS PASSED")
