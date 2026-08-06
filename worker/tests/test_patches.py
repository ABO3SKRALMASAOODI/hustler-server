"""Round 92 — erase becomes window-sized PATCHES.

Job 2685 (the session that triggered this): a user asked for nicer captions
on a video that arrived with captions burned in. The agent erased them — the
right move — and the whole-file clean pass re-derived a 39s video seven
times, 965s of a 1057s turn, which then TIMED OUT. The user read a failure.

New contract: an erase repaints only its own window into a short patch clip
(seconds of work), the renderer overlays it on the source clock BEFORE
segments/zoom/grade, and the export materializes a full-res twin
content-addressed by fingerprint. Earlier erases are never re-derived.

Pins:
  * build_patch repaints ink inside the window and NOTHING else, snapped to
    the frame grid; a box burned into the source measures dark after.
  * the graph: patch inputs chain onto the source stream via
    setpts -> scale2ref -> overlay(enable=window), before any trim.
  * render_edl end to end: the burned box is GONE at patched seconds and
    STILL THERE outside the window — the overlay replaces exactly its span.
  * _patch_groups merges overlapping windows, keeps disjoint ones separate.
  * the tool path: erase_region with a window writes a patches entry via a
    real local build; remove_erase on a patch id drops it instantly.
  * schema: validation rejects bad patches; legacy EDL signatures are
    untouched by the new field.

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
import inpaint                                                  # noqa: E402
import schemas                                                  # noqa: E402
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

GOOD = {"id": "pa1", "asset_key": "patches/1/x.mp4", "fp": "abc",
        "src_start": 2.0, "src_end": 5.0,
        "regions": [{"id": "er1", "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2,
                     "fill": "text"}]}
e = schemas.validate_edl({"keep": [[0, 10]], "patches": [GOOD]}, 10.0)
check("a patch EDL validates", len(e.patches) == 1)
for bad, why in (
        (dict(GOOD, src_end=2.0), "empty window"),
        (dict(GOOD, regions=[]), "no regions"),
        (dict(GOOD, asset_key=""), "no clip"),):
    try:
        schemas.validate_edl({"keep": [[0, 10]], "patches": [bad]}, 10.0)
        check(f"rejects {why}", False)
    except schemas.EDLValidationError:
        check(f"rejects {why}", True)
try:
    schemas.validate_edl({"keep": [[0, 10]], "patches": [GOOD, GOOD]}, 10.0)
    check("rejects duplicate ids", False)
except schemas.EDLValidationError:
    check("rejects duplicate ids", True)
legacy = schemas.validate_edl({"keep": [[0, 10]]}, 10.0)
check("legacy signatures untouched by the field",
      "patches" not in schemas.edl_signature(legacy.model_dump()))

print("== 2. grouping ==")

g = agent_tools._patch_groups(
    [{"start": 2.0, "end": 4.0}, {"start": 4.5, "end": 6.0},
     {"start": 20.0, "end": 21.0}], 30.0)
check("overlapping windows merge (pad joins 4.0 and 4.5)",
      len(g) == 2 and g[0][0][1] >= 6.0 and len(g[0][1]) == 2)
check("far windows stay separate", g[1][0][0] > 18.0)
g2 = agent_tools._patch_groups([{"start": None, "end": None}], 30.0)
check("unwindowed region spans the video", g2[0][0] == (0.0, 30.0))

print("== 3. graph emission ==")

tl = Timeline([[0.0, 10.0]], [], [])
graph = build_filtergraph(
    {"keep": [[0.0, 10.0]], "volume": [], "music": [], "effects": {}},
    12.0, True, tl, None, [], {"words": [], "silences": []}, True,
    W=320, H=180, fps=30.0,
    patch_inputs=[(1, {"id": "pa1", "src_start": 2.0, "src_end": 5.0})])
check("patch input shifts onto the source clock",
      "setpts=PTS+2.000/TB" in graph)
check("patch pinned to the main stream's size", "scale2ref" in graph)
check("overlay bounded to the window",
      "overlay=eof_action=pass:enable='between(t,2.000,5.000)'" in graph)

if not HAVE_FFMPEG:
    print("== 4-6 skipped (no ffmpeg) ==")
    print(f"\nALL {PASS} CHECKS PASSED")
    sys.exit(0)

d = tempfile.mkdtemp(prefix="patch_")
src = os.path.join(d, "src.mp4")
# 8s clip, solid blue, with a WHITE BOX burned across the middle the whole
# time — the stand-in for burned captions.
subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
     "-i", "color=c=0x2244AA:size=320x180:rate=30:duration=8",
     "-f", "lavfi", "-i", "sine=frequency=200:duration=8",
     "-vf", "drawbox=x=80:y=60:w=160:h=40:color=white:t=fill",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
    check=True)

REG = [{"id": "er1", "x": 0.25, "y": 0.33, "w": 0.5, "h": 0.23,
        "fill": "box", "start": 2.0, "end": 5.0}]

print("== 4. build_patch ==")

patch = os.path.join(d, "patch.mp4")
stats = inpaint.build_patch(src, REG, (2.0, 5.0), patch)
check("patch covers its window (~3s)",
      2.8 <= (stats["src_end"] - stats["src_start"]) <= 3.2)
check("frames were repainted", stats["frames_touched"] > 60)


def box_mean(path, t):
    """Mean brightness of the box interior at t — white ~235, repaint ~65.
    (text_energy is the wrong ruler here: it measures STROKE texture, and
    the repaint's matched grain scores higher than flat white. On real
    footage the agent's measure compares like against like; on a flat
    synthetic, brightness is the honest signal.)"""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path,
         "-frames:v", "1", "-vf", "crop=140:30:90:65,format=gray",
         "-f", "rawvideo", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    return float(a.mean()) if a.size else -1.0


b_src = box_mean(src, 3.0)
b_pat = box_mean(patch, 3.0 - stats["src_start"])
check(f"the box is repainted away (brightness {b_src:.0f} -> {b_pat:.0f})",
      b_src > 180 and b_pat < 120)

print("== 5. render_edl with the patch ==")

edl = schemas.default_edl(8.0)
edl["patches"] = [{"id": "pa1", "asset_key": "patches/1/p.mp4",
                   "fp": "testfp", "src_start": stats["src_start"],
                   "src_end": stats["src_end"], "regions": REG}]
idx = {"video": {"duration": 8.0, "width": 320, "height": 180, "fps": 30.0},
       "words": [], "sentences": [], "silences": []}
out = os.path.join(d, "out.mp4")
render_edl(edl, idx, src, out, d, preview=True,
           patch_locals={"pa1": patch})


w1, w3, w6 = box_mean(out, 1.0), box_mean(out, 3.5), box_mean(out, 6.5)
check(f"box present before the window (bright {w1:.0f})", w1 > 180)
check(f"box GONE inside the window (dark {w3:.0f})", w3 < 120)
check(f"box back after the window (bright {w6:.0f})", w6 > 180)

print("== 6. the tool path ==")


class PatchCtx:
    def __init__(self):
        self.workdir = d
        self.duration = 8.0
        self.has_main_video = True
        self.project_id = 1
        self.db = self
        self.index = {"video": {"duration": 8.0, "width": 320,
                                "height": 180, "fps": 30.0}}
        self.job = {"user_id": 1}
        self._edl = {"version": 3, "json": schemas.default_edl(8.0)}
        self.written = None

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = schemas.validate_edl(edl, 8.0).model_dump()
        self._edl = {"version": self._edl["version"] + 1,
                     "json": self.written}
        return f"EDL v3 -> v4: {desc}"

    def run(self, fn, *a, **k):
        name = getattr(fn, "__name__", "")
        if name == "latest_asset":
            if a[1] == "original":
                return {"storage_key": "orig/x.mp4", "sha256": "shatest"}
            return {"storage_key": "prox/x.mp4"}
        if name == "insert_asset":
            return 1
        return None

    def proxy_path(self):
        return src


uploads = {}
_orig_sto = (agent_tools.storage.exists, agent_tools.storage.upload_file)
_orig_remote = agent_tools.remote.clean_available
agent_tools.storage.exists = lambda k: False
agent_tools.storage.upload_file = lambda l, k, c=None: uploads.update({k: l})
agent_tools.remote.clean_available = lambda: False
try:
    ctx = PatchCtx()
    ctx._original_row = None
    res = agent_tools.erase_region(ctx, x=0.25, y=0.33, w=0.5, h=0.23,
                                   start=2.0, end=5.0, fill="box")
    check("erase_region writes a patch entry",
          res.startswith("EDL v") and len(ctx.written["patches"]) == 1)
    # The verdict itself is content-dependent (on flat synthetic the stroke
    # metric misreads matched grain — the box-brightness checks above are
    # the ground truth here); what this pins is that the measurement RAN and
    # was reported, so the agent always sees numbers, never a bare claim.
    check("the honesty measure rode along",
          "Measured on the repainted window" in res and "ink" in res)
    check("the patch clip was uploaded",
          any(k.startswith("patches/") for k in uploads))
    pid = ctx.written["patches"][0]["id"]
    res2 = agent_tools.remove_erase(ctx, id=pid)
    check("remove_erase drops the patch instantly",
          res2.startswith("EDL v") and ctx.written["patches"] == [])
finally:
    (agent_tools.storage.exists, agent_tools.storage.upload_file) = _orig_sto
    agent_tools.remote.clean_available = _orig_remote
    shutil.rmtree(d, ignore_errors=True)

print(f"\nALL {PASS} CHECKS PASSED")
