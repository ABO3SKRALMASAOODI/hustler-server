"""Round 96 — Socket 1: the agent writes its own ffmpeg chain.

Every visual effect used to be a hand-built EDL node + a renderer rule + a
tool ("the treadmill"). effects.custom is the open-ended stylize: the agent
writes the chain, the EDL still says where and when, and three nets stand
where the menu used to — custom_chain_error (structure: one chain, no graph
syntax, no file access; shared with validate_edl so both services agree),
the add tool's DRY RUN on real pixels (parse + geometry/rate + measured
cost), and the agent's eyes on the next preview.

Pins:
  * schema: a good chain validates; graph syntax, file args, denied filter
    names, unbalanced quotes, dup ids and half windows reject; legacy
    signatures are untouched by the field and a stored chain changes the
    signature (so the write is never a NO CHANGE).
  * graph: unwindowed chains splice inline; windowed chains go through
    split -> chain -> overlay enable=between(...), because the agent's
    chain may contain filters with no timeline support.
  * stitch: a windowed custom change re-encodes only its window; an
    unwindowed one forces the full render; window_edl shifts the item into
    the piece's local clock.
  * edl_diff: the flash lands on the window; verify_plan claims the effect
    must be visibly applied there.
  * remap: a windowed chain follows its footage through an upstream cut.
  * the tool: executor-gate refusal, ffmpeg's own error on a broken chain,
    geometry/rate rejection, and the happy path writing effects.custom.
  * render_edl end to end: negate windowed 2-5s — inverted inside the
    window, untouched outside it.

LIVE ffmpeg; the pixel tests are skipped where there is none.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np                                              # noqa: E402

import agent_tools                                              # noqa: E402
import edl_diff                                                 # noqa: E402
import remote                                                   # noqa: E402
import schemas                                                  # noqa: E402
import stitch                                                   # noqa: E402
import timeline as timeline_mod                                 # noqa: E402
from renderer import build_filtergraph, render_edl              # noqa: E402
from timeline import Timeline                                   # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


print("== 1. schema ==")

GOOD = {"id": "cf1", "chain": "hue=s=0.3,noise=alls=8:allf=t",
        "start": 2.0, "end": 5.0, "label": "muted grain"}
e = schemas.validate_edl(
    {"keep": [[0, 10]], "effects": {"custom": [GOOD]}}, 10.0)
check("a custom-filter EDL validates", len(e.effects.custom) == 1)
for bad, why in (
        (dict(GOOD, chain="split[a][b];[a][b]overlay"), "graph syntax"),
        (dict(GOOD, chain="drawtext=textfile=/etc/passwd"), "file arg"),
        (dict(GOOD, chain="movie=/tmp/x.mp4"), "denied filter name"),
        (dict(GOOD, chain="drawtext=text='oops"), "unbalanced quote"),
        (dict(GOOD, chain="hue=s=0," * 200), "over-long chain"),
        (dict(GOOD, start=2.0, end=None), "half a window"),
        (dict(GOOD, start=8.0, end=25.0), "span past the program"),):
    try:
        schemas.validate_edl(
            {"keep": [[0, 10]], "effects": {"custom": [bad]}}, 10.0)
        check(f"rejects {why}", False)
    except schemas.EDLValidationError:
        check(f"rejects {why}", True)
try:
    schemas.validate_edl(
        {"keep": [[0, 10]],
         "effects": {"custom": [GOOD, dict(GOOD)]}}, 10.0)
    check("rejects duplicate ids", False)
except schemas.EDLValidationError:
    check("rejects duplicate ids", True)
legacy = schemas.validate_edl({"keep": [[0, 10]]}, 10.0)
check("legacy signatures untouched by the field",
      "custom" not in schemas.edl_signature(legacy.model_dump()))
check("a stored chain moves the signature (write is never NO CHANGE)",
      schemas.edl_signature(e.model_dump())
      != schemas.edl_signature(legacy.model_dump()))
check("describe_edl names the look",
      "muted grain" in schemas.describe_edl(e.model_dump(), 10.0))

print("== 2. graph emission ==")

tl = Timeline([[0.0, 10.0]], [], [])
BASE = {"keep": [[0.0, 10.0]], "volume": [], "music": []}
g = build_filtergraph(
    dict(BASE, effects={"custom": [dict(GOOD)]}),
    12.0, True, tl, None, [], {"words": [], "silences": []}, True,
    W=320, H=180, fps=30.0)
check("windowed chain goes through a split branch", "split[cusA0][cusB0]" in g)
check("the branch runs the agent's chain",
      "[cusB0]hue=s=0.3,noise=alls=8:allf=t[cusP0]" in g)
check("composited back only inside the window",
      "overlay=eof_action=pass:enable='between(t,2.000,5.000)'" in g)
g2 = build_filtergraph(
    dict(BASE, effects={"custom": [{"id": "cf1", "chain": "negate",
                                    "start": None, "end": None}]}),
    12.0, True, tl, None, [], {"words": [], "silences": []}, True,
    W=320, H=180, fps=30.0)
check("unwindowed chain splices inline", "]negate[vcusf0]" in g2)
check("no split for an unwindowed chain", "cusA0" not in g2)

print("== 3. stitch ==")

prev = schemas.validate_edl(dict(BASE), 10.0).model_dump()
new = schemas.validate_edl(
    dict(BASE, effects={"custom": [dict(GOOD)]}), 10.0).model_dump()
tlp, tln = Timeline(prev["keep"], [], []), Timeline(new["keep"], [], [])
wins, why = stitch.plan(prev, new, tlp, tln, 10.0, 10.0)
check("a windowed custom change is stitchable", why is None and wins)
check("...and re-encodes only around its window",
      all(a >= 0.5 and b <= 7.0 for a, b in wins))
uw = schemas.validate_edl(
    dict(BASE, effects={"custom": [{"id": "cf1", "chain": "negate"}]}),
    10.0).model_dump()
wins, why = stitch.plan(prev, uw, tlp, Timeline(uw["keep"], [], []),
                        10.0, 10.0)
check("an unwindowed chain forces the full render",
      wins is None and "custom" in (why or ""))
we = stitch.window_edl(new, tln, 1.0, 6.0)
cf = (we.get("effects") or {}).get("custom") or []
check("window_edl shifts the chain into the piece's clock",
      len(cf) == 1 and cf[0]["start"] == 1.0 and cf[0]["end"] == 4.0)

print("== 4. edl_diff ==")

ch = edl_diff.change_ranges(prev, new)
check("the flash lands on the window",
      ch and any(a <= 2.1 and b >= 4.9 for a, b in ch.get("out_ranges", [])))
claims = edl_diff.verify_plan(prev, new)
check("verify_plan claims the effect must be visible there",
      claims and any("muted grain" in c for _, c in claims))

print("== 5. remap through a cut ==")

edl_rm = {"keep": [[10.0, 30.0]], "inserts": [],
          "effects": {"custom": [{"id": "cf1", "chain": "negate",
                                  "start": 10.0, "end": 15.0,
                                  "label": "inverted"}]}}
old_tl = Timeline([[0.0, 30.0]], [], [])
new_tl = Timeline([[10.0, 30.0]], [], [])
notes = timeline_mod.remap_program_items(edl_rm, old_tl, new_tl)
moved = edl_rm["effects"]["custom"][0]
check("the chain follows its footage through the upstream cut",
      moved["start"] == 0.0 and moved["end"] == 5.0)
check("...and the move is disclosed",
      any("custom filter cf1" in n for n in notes))

print("== 6. the tool: rejections that need no ffmpeg ==")

r = agent_tools.add_custom_filter(None, "negate;[x]split")
check("graph syntax rejects before touching the project",
      r.startswith("REJECTED") and "no ';'" in r)
r = agent_tools.add_custom_filter(None, "curves=psfile=/etc/passwd")
check("file access rejects", r.startswith("REJECTED") and "files" in r)


class CFCtx:
    def __init__(self, d, src=None):
        self.workdir = d
        self.duration = 8.0
        self.has_main_video = True
        self.project_id = 1
        self._src = src
        self._edl = {"version": 3, "json": schemas.default_edl(8.0)}
        self.written = None

    def proxy_path(self):
        if not self._src:
            raise RuntimeError("no proxy in this test")
        return self._src

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = schemas.validate_edl(edl, 8.0).model_dump()
        self._edl = {"version": self._edl["version"] + 1,
                     "json": self.written}
        return f"EDL v3 -> v4: {desc}"


_real_supports = remote.executor_supports
d = tempfile.mkdtemp(prefix="cf_")
try:
    remote.executor_supports = lambda f, **k: False
    agent_tools._CUSTOM_FEATURE_CACHE.update(at=0.0, ok=None)
    r = agent_tools.add_custom_filter(CFCtx(d), "negate", 2, 5)
    check("a stale executor refuses the WRITE, with the operator step",
          r.startswith("REJECTED") and "DEPLOY_EXECUTOR" in r)
finally:
    remote.executor_supports = _real_supports
    agent_tools._CUSTOM_FEATURE_CACHE.update(at=0.0, ok=None)

if not HAVE_FFMPEG:
    print("== 7-8 skipped (no ffmpeg) ==")
    print(f"\nALL {PASS} CHECKS PASSED")
    sys.exit(0)

print("== 7. the tool: dry run on real pixels ==")

src = os.path.join(d, "src.mp4")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
     "-i", "color=c=0x2244AA:size=320x180:rate=30:duration=8",
     "-f", "lavfi", "-i", "sine=frequency=200:duration=8",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
    check=True)

remote.executor_supports = lambda f, **k: True
agent_tools._CUSTOM_FEATURE_CACHE.update(at=0.0, ok=None)
try:
    ctx = CFCtx(d, src)
    r = agent_tools.add_custom_filter(ctx, "notarealfilter=1", 2, 5)
    check("a broken chain returns ffmpeg's own error",
          r.startswith("REJECTED") and "ffmpeg rejected" in r)
    r = agent_tools.add_custom_filter(ctx, "scale=160:90", 2, 5)
    check("a geometry-changing chain rejects",
          r.startswith("REJECTED") and "geometry" in r)
    r = agent_tools.add_custom_filter(ctx, "fps=60", 2, 5)
    check("a rate-changing chain rejects",
          r.startswith("REJECTED") and "RATE" in r)
    r = agent_tools.add_custom_filter(ctx, "negate", 2, 5, label="inverted")
    check("a good chain writes effects.custom", r.startswith("EDL v")
          and ctx.written["effects"]["custom"][0]["chain"] == "negate")
    check("the result sends the agent to LOOK", "LOOK at" in r)
    r = agent_tools.remove_custom_filter(ctx, "cf1")
    check("remove_custom_filter drops it", r.startswith("EDL v")
          and not (ctx.written.get("effects") or {}).get("custom"))
finally:
    remote.executor_supports = _real_supports
    agent_tools._CUSTOM_FEATURE_CACHE.update(at=0.0, ok=None)

print("== 8. render_edl end to end ==")


def red_mean(path, t):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
         "-frames:v", "1", "-vf", "format=rgb24,extractplanes=r",
         "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    return float(a.mean()) if a.size else -1.0


edl = schemas.default_edl(8.0)
edl["effects"] = {"custom": [{"id": "cf1", "chain": "negate",
                              "start": 2.0, "end": 5.0,
                              "label": "inverted"}]}
idx = {"video": {"duration": 8.0, "width": 320, "height": 180, "fps": 30.0},
       "words": [], "sentences": [], "silences": []}
out = os.path.join(d, "out.mp4")
render_edl(edl, idx, src, out, d, preview=True)
r1, r3, r6 = red_mean(out, 1.0), red_mean(out, 3.5), red_mean(out, 6.5)
check(f"untouched before the window (red {r1:.0f})", 0 <= r1 < 90)
check(f"inverted inside the window (red {r3:.0f})", r3 > 160)
check(f"untouched after the window (red {r6:.0f})", 0 <= r6 < 90)

print(f"\nALL {PASS} CHECKS PASSED")
