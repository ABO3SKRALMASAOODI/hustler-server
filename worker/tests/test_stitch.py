"""Round 93 — stitched previews: re-encode the changed seconds, copy the rest.

"Why is it still rendering the preview?" — because every preview re-encoded
the whole program no matter how small the edit. This pins the machinery that
makes previews O(change):

  * plan() gates: structural/audio/caption-config changes refuse; text-only
    and zoom-only changes yield the right windows.
  * window_edl maps output windows through cuts AND speed spans into a
    standalone EDL whose own length is exactly the window.
  * shift_ass keeps only whole events, shifted; a straddling event refuses.
  * snap_windows honours keyframes AND forbidden zones.
  * END TO END: v1 (cuts + zoom + captions + title) renders fully; v2 adds
    one text. The stitched v2 must equal a FRESH FULL RENDER of v2 —
    duration equal, the new text visible in its window, copied regions and
    re-encoded regions both close to the reference (PSNR), audio intact.

LIVE ffmpeg; pixel half skipped without it.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np                                              # noqa: E402

import renderer                                                 # noqa: E402
import schemas                                                  # noqa: E402
import stitch                                                   # noqa: E402
from timeline import Timeline                                   # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def tl_of(edl):
    return Timeline(edl["keep"], edl.get("inserts") or [],
                    edl.get("speed") or [])


BASE = {
    "keep": [[0.0, 8.0], [10.0, 18.0]],
    "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                           "strength": 0.4}]},
    "texts": [{"id": "tx1", "text": "HELLO", "start": 1.0, "end": 3.0,
               "template": "title"}],
}

print("== 1. the gate ==")

v2 = {**BASE, "texts": BASE["texts"] + [
    {"id": "tx2", "text": "NEW", "start": 12.0, "end": 14.0,
     "template": "title"}]}
w, why = stitch.plan(BASE, v2, tl_of(BASE), tl_of(v2), 20.0, 16.0)
check("text added -> one window around it",
      w is not None and len(w) == 1 and 11.0 <= w[0][0] <= 12.0
      and 14.0 <= w[0][1] <= 15.0)

cut = {**BASE, "keep": [[0.0, 8.0], [10.0, 17.0]]}
w, why = stitch.plan(BASE, cut, tl_of(BASE), tl_of(cut), 20.0, 15.0)
check("timeline change refuses", w is None and "structural" in why)

aud = {**BASE, "music": [{"id": "m1", "storage_key": "k", "start": 0.0,
                          "end": 16.0, "gain_db": -18}]}
w, why = stitch.plan(BASE, aud, tl_of(BASE), tl_of(aud), 20.0, 16.0)
check("audio-only change refuses (audio is copied)",
      w is None and "structural" in why)

same = {**BASE}
w, why = stitch.plan(BASE, same, tl_of(BASE), tl_of(same), 20.0, 16.0)
check("identical EDLs refuse", w is None)

print("== 2. window mapping through cuts and speed ==")

sp = {**BASE, "speed": [{"id": "s1", "start": 10.0, "end": 14.0,
                         "factor": 2.0}]}
tl = tl_of(sp)                       # out: [0,8)=src 0-8, then 10-14@2x = 2s
we = stitch.window_edl(sp, tl, 7.0, 10.5)
wtl = tl_of(we)
check("windowed EDL spans exactly the window",
      abs(wtl.out_duration - 3.5) < 0.01)
check("speed span carried with factor",
      we["speed"] and we["speed"][0]["factor"] == 2.0)
check("zoom outside the window dropped", not we["effects"]["zooms"])

we2 = stitch.window_edl(v2, tl_of(v2), 11.0, 15.0)
check("text inside the window shifted",
      we2["texts"] and we2["texts"][0]["id"] == "tx2"
      and abs(we2["texts"][0]["start"] - 1.0) < 0.01)

print("== 3. ass shifting and snapping ==")

d = tempfile.mkdtemp(prefix="stitch_")
ass = os.path.join(d, "a.ass")
open(ass, "w").write(
    "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,D,one\n"
    "Dialogue: 0,0:00:12.00,0:00:13.50,D,two\n")
out_ass = os.path.join(d, "b.ass")
check("whole events shift", stitch.shift_ass(ass, out_ass, 11.0, 15.0)
      and "0:00:01.00,0:00:02.50" in open(out_ass).read())
check("a STATIC straddling event clamps to the boundary",
      stitch.shift_ass(ass, out_ass, 12.5, 15.0) is True
      and "0:00:00.00,0:00:01.00" in open(out_ass).read())
kara = os.path.join(d, "k.ass")
open(kara, "w").write(
    "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    "Dialogue: 0,0:00:12.00,0:00:13.50,D,{\\k50}wo{\\k100}rd\n")
check("a KARAOKE straddling event refuses",
      stitch.shift_ass(kara, out_ass, 12.5, 15.0) is False)
fad = os.path.join(d, "f.ass")
open(fad, "w").write(
    "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    "Dialogue: 0,0:00:12.00,0:00:13.50,D,{\\fad(120,80)}hi\n")
check("a finished fade-in is stripped from the clamped copy",
      stitch.shift_ass(fad, out_ass, 12.5, 15.0) is True
      and "\\fad" not in open(out_ass).read())
check("a fade-in still in flight refuses",
      stitch.shift_ass(fad, out_ass, 12.05, 15.0) is False)

kfs = [0.0, 1.6, 3.2, 4.8, 6.4, 8.0, 9.6, 11.2, 12.8, 14.4]
snapped = stitch.snap_windows([(5.0, 7.0)], kfs, 16.0)
check("windows snap outward to keyframes", snapped == [(4.8, 8.0)])
snapped = stitch.snap_windows([(5.0, 7.0)], kfs, 16.0,
                              forbidden=[(4.7, 4.9)])
check("a forbidden boundary walks further out", snapped == [(3.2, 8.0)])

if not HAVE_FFMPEG:
    shutil.rmtree(d, ignore_errors=True)
    print("== 4 skipped (no ffmpeg) ==")
    print(f"\nALL {PASS} CHECKS PASSED")
    sys.exit(0)

print("== 4. end to end: stitched == fresh full render ==")

# This machine's ffmpeg has no libass (subtitles filter), so the pixel run
# uses a ZOOM as the delta — text/caption shifting is pinned above in units
# and verified on production (whose build burns text) in the deploy round.
src = os.path.join(d, "src.mp4")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error",
     "-f", "lavfi", "-i",
     "testsrc2=size=320x180:rate=30:duration=20",
     "-f", "lavfi", "-i", "sine=frequency=300:duration=20",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
    check=True)

IDX = {"video": {"duration": 20.0, "width": 320, "height": 180, "fps": 30.0},
       "words": [], "sentences": [], "silences": []}

BASE_PX = {"keep": [[0.0, 8.0], [10.0, 18.0]],
           "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                                  "strength": 0.4}]}}
V2_PX = {"keep": [[0.0, 8.0], [10.0, 18.0]],
         "effects": {"zooms": [
             {"id": "z1", "start": 2.0, "end": 4.0, "strength": 0.4},
             {"id": "z2", "start": 12.0, "end": 14.0, "strength": 0.8}]}}
v1 = schemas.validate_edl(BASE_PX, 20.0).model_dump()
v2 = schemas.validate_edl(V2_PX, 20.0).model_dump()

r1 = os.path.join(d, "r1.mp4")
renderer.render_edl(v1, IDX, src, r1, d, preview=True)
r2 = os.path.join(d, "r2.mp4")
renderer.render_edl(v2, IDX, src, r2, d, preview=True)

s2 = os.path.join(d, "s2.mp4")
_real_cache = renderer._cached_source
renderer._cached_source = lambda k: r1 if k == "prev/key.mp4" else None
try:
    out_dur = renderer._stitched_preview(
        0, {"version": 2, "json": v2}, {"version": 1, "json": v1},
        {"storage_key": "prev/key.mp4"}, IDX, src, d, {}, s2)
finally:
    renderer._cached_source = _real_cache

check("stitch produced a file", out_dur is not None and os.path.exists(s2))
check("stitched length matches the reference",
      abs(renderer.media.duration_of(s2)
          - renderer.media.duration_of(r2)) < 0.15)


def frame(path, t):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v",
         "1", "-vf", "format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float32)


def psnr(a, b):
    if a.size != b.size or not a.size:
        return 0.0
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse < 1e-6 else 10.0 * np.log10(255.0 * 255.0 / mse)


for t, where in ((2.0, "copied head"), (9.0, "copied middle"),
                 (13.0, "re-encoded window"), (15.5, "copied tail")):
    p = psnr(frame(s2, t), frame(r2, t))
    check(f"{where} matches the reference at {t}s (PSNR {p:.1f})", p > 30.0)

# the NEW zoom actually landed in the stitched window
w_alone = frame(s2, 13.0)
w_prev = frame(r1, 13.0)
check("the new zoom changed the window's pixels vs v1",
      psnr(w_alone, w_prev) < 40.0)

astream = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
     "stream=codec_name,duration", "-of", "csv=p=0", s2],
    capture_output=True, text=True).stdout
check("audio stream survived the stitch", "aac" in astream)

shutil.rmtree(d, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASSED")
