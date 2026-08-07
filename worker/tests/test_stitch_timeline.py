"""Round 97 — timeline-mode stitched previews: trims, cuts and audio edits
stop paying the full render.

Round 93 stitched only when the timeline structure was identical, so the
edit users make MOST — a trim — always re-rendered everything (134-206s per
preview on real projects; the round-96c churn diagnosis). Timeline mode
matches the new program span-by-span against the previous preview, copies
matched spans FROM THEIR OLD POSITIONS (per-run constant offset d), carves
out everything not provably identical (shifted output-anchored items,
junction changes, caption regroupings), re-encodes only that, and rebuilds
the whole audio track through the pruned render graph.

Pinned here:
  * atoms/match_runs: cuts, head trims, speed changes, the d bookkeeping;
  * plan_timeline gates and carves — including BOTH junction directions;
  * caption event pairing modulo d;
  * snap_parts: copies shrink to previous-preview keyframes, windows absorb
    the slack, tiny copies dissolve, the plan always covers the program;
  * _prune_graph_to_audio: reachability, dangling detection;
  * END TO END with live ffmpeg: a mid-video cut, a head trim under an
    unchanged (output-anchored) zoom, and an audio-only music change each
    stitch to the SAME picture a fresh full render produces (PSNR), with
    audio matching the reference track window-for-window (RMS).

LIVE ffmpeg; the pixel half is skipped without it.
"""

import contextlib
import io
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


def V(edl, dur=20.0):
    return schemas.validate_edl(edl, dur).model_dump()


print("== 1. atoms and run matching ==")

v1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]]})
a1 = stitch.timeline_atoms(v1, tl_of(v1))
check("two segments -> two source atoms",
      len(a1) == 2 and a1[0][2] == ("src", 0.0, 8.0, 1.0)
      and abs(a1[1][0] - 8.0) < 1e-6)

# mid-video cut: content after the cut shifts by -1.5 on the NEW clock
v2 = V({"keep": [[0.0, 5.0], [6.5, 8.0], [10.0, 18.0]]})
runs = stitch.match_runs(stitch.timeline_atoms(v1, tl_of(v1)),
                         stitch.timeline_atoms(v2, tl_of(v2)))
check("cut -> two runs: unshifted head, +1.5 shifted rest",
      len(runs) == 2 and abs(runs[0][2]) < 1e-9
      and abs(runs[0][1] - 5.0) < 1e-3
      and abs(runs[1][2] - 1.5) < 1e-9
      and abs(runs[1][0] - 5.0) < 1e-3
      and abs(runs[1][1] - 14.5) < 1e-3)

# head trim: everything shifts by +3
v3 = V({"keep": [[3.0, 8.0], [10.0, 18.0]]})
runs3 = stitch.match_runs(stitch.timeline_atoms(v1, tl_of(v1)),
                          stitch.timeline_atoms(v3, tl_of(v3)))
check("head trim -> one run at d=+3 covering the whole program",
      len(runs3) == 1 and abs(runs3[0][2] - 3.0) < 1e-9
      and abs(runs3[0][0]) < 1e-3 and abs(runs3[0][1] - 13.0) < 1e-3)

# a speed change makes those atoms unmatched
v4 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "speed": [{"id": "s1", "start": 10.0, "end": 14.0, "factor": 2.0}]})
runs4 = stitch.match_runs(stitch.timeline_atoms(v1, tl_of(v1)),
                          stitch.timeline_atoms(v4, tl_of(v4)))
cover4 = sum(b - a for a, b, _d in runs4)
check("speed change -> the sped span drops out of the matched cover",
      all(not (a < 9.9 < b) for a, b, d in runs4
          if abs(d) < 1e-9 and a > 7.9) or cover4 < 16.0 - 1.9)

print("== 2. plan_timeline: gates ==")

g1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "effects": {"grade": "vibrant"}})
w, r, why = stitch.plan_timeline(v1, g1, tl_of(v1), tl_of(g1), 16.0)
check("grade change refuses (non-timeline structural)",
      w is None and "structural" in why)

m1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "music": [{"id": "m1", "storage_key": "k", "start": 0.0,
                   "end": 16.0, "gain_db": -18}]})
w, r, why = stitch.plan_timeline(v1, m1, tl_of(v1), tl_of(m1), 16.0)
check("audio-only change -> zero windows, full copy cover",
      w == [] and r and abs(sum(b - a for a, b, _d in r) - 16.0) < 0.01)

f1 = V({"keep": [[0.0, 8.0], [10.0, 17.0]],
        "effects": {"fade_out_s": 0.6}})
v1f = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
         "effects": {"fade_out_s": 0.6}})
w, r, why = stitch.plan_timeline(v1f, f1, tl_of(v1f), tl_of(f1), 15.0)
check("a fade-out on a length-changed program refuses",
      w is None and "fade" in why)

print("== 3. plan_timeline: carves ==")

# an UNCHANGED output-anchored zoom over SHIFTED footage must re-encode
z1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 11.0, "end": 13.0,
                               "strength": 0.4}]}})
z2 = V({"keep": [[3.0, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 11.0, "end": 13.0,
                               "strength": 0.4}]}})
w, r, why = stitch.plan_timeline(z1, z2, tl_of(z1), tl_of(z2), 13.0)
check("unchanged zoom over shifted footage -> its span re-encodes",
      w is not None and any(a <= 11.0 and b >= 13.0 for a, b in w))
check("...and the copies exclude it",
      all(b <= 11.01 or a >= 12.99 for a, b, _d in r))

# junction carving, both directions
t1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "effects": {"transition": {"style": "dip_black",
                                   "duration_s": 0.4, "scope": "scene"}}})
t2 = V({"keep": [[0.0, 5.0], [6.5, 8.0], [10.0, 18.0]],
        "effects": {"transition": {"style": "dip_black",
                                   "duration_s": 0.4, "scope": "scene"}}})
w, r, why = stitch.plan_timeline(t1, t2, tl_of(t1), tl_of(t2), 14.5)
check("a NEW cut under a transition carves its junction zone",
      w is not None and any(a <= 5.0 <= b for a, b in w))
# old junction at prev 8.0 maps to new 6.5 and STILL exists -> no carve there
check("a surviving junction stays copyable",
      not any(a < 6.4 and b > 6.6 and not (a <= 5.0 <= b) for a, b in w))
w, r, why = stitch.plan_timeline(t2, t1, tl_of(t2), tl_of(t1), 16.0)
check("a REMOVED junction carves where its pixels would smear",
      w is not None and any(a <= 5.0 <= b for a, b in w))

print("== 4. caption pairing modulo d ==")

prev_ev = [(2.0, 3.0, "D|hello"), (11.5, 12.5, "D|world")]
new_ev = [(2.0, 3.0, "D|hello"), (10.0, 11.0, "D|world")]
bad = stitch.caption_mismatch_spans(
    [(0.0, 5.0, 0.0), (5.0, 14.5, 1.5)], prev_ev, new_ev, 14.5)
check("shifted-by-d captions pair up -> nothing carved", bad == [])
new_ev2 = new_ev + [(7.0, 8.0, "D|extra")]
bad = stitch.caption_mismatch_spans(
    [(0.0, 5.0, 0.0), (5.0, 14.5, 1.5)], prev_ev, new_ev2, 14.5)
check("an event only ONE program burns is carved",
      any(a <= 7.0 and b >= 8.0 for a, b in bad))
new_ev3 = [(2.0, 3.0, "D|hello"), (10.0, 11.0, "D|worlds")]
bad = stitch.caption_mismatch_spans(
    [(0.0, 5.0, 0.0), (5.0, 14.5, 1.5)], prev_ev, new_ev3, 14.5)
check("a reworded event carves both sides' spans",
      any(a <= 10.0 and b >= 11.0 for a, b in bad))

print("== 5. snap_parts ==")

kfs = [0.0, 1.6, 3.2, 4.8, 6.4, 8.0, 9.6, 11.2, 12.8, 14.4, 16.0]
parts = stitch.snap_parts(
    [(4.5, 5.5)], [(0.0, 4.5, 0.0), (5.5, 14.5, 1.5)],
    kfs, 14.5, [], [])
check("copies shrink to keyframes, the window absorbs the slack",
      parts is not None
      and parts[0] == ("copy", 0.0, 3.2, 0.0)
      and parts[1][0] == "win"
      and abs(parts[1][1] - 3.2) < 1e-6 and abs(parts[1][2] - 6.5) < 1e-6
      and parts[2][0] == "copy"
      and abs(parts[2][1] - 6.5) < 1e-6
      and abs(parts[2][2] - 14.5) < 1e-6
      and abs(parts[2][3] - 1.5) < 1e-9)
check("the plan covers the whole program",
      abs(sum((p[2] - p[1]) for p in parts) - 14.5) < 0.01)
parts = stitch.snap_parts(
    [(0.0, 4.9), (5.6, 14.5)], [(4.9, 5.6, 0.7)], kfs, 14.5, [], [])
check("a copy too small to keep dissolves — and a stitch that would "
      "re-encode everything says so (full render)", parts is None)
check("no keyframes -> no plan", stitch.snap_parts(
    [(4.5, 5.5)], [(0.0, 4.5, 0.0)], [], 14.5, [], []) is None)

print("== 6. the audio-graph prune ==")

g = ("[0:v]scale=100:100[v1];[v1]format=yuv420p[vout];"
     "[0:a]atrim=0:5[a1];[a1]volume=2[aout]")
p = renderer._prune_graph_to_audio(g)
check("video chains drop, audio chains survive",
      "scale" not in p and "atrim" in p and "volume" in p
      and p.endswith("[aout]"))
g2 = ("[0:a]asplit=2[a1][a2];[a1]volume=1[aout]")
try:
    renderer._prune_graph_to_audio(g2)
    dangling_raised = False
except Exception:
    dangling_raised = True
check("a dangling audio output refuses the prune", dangling_raised)

print("== 6b. a stitched preview that fails verification falls back "
      "IN-JOB ==")

import inspect                                                  # noqa: E402
_src = inspect.getsource(renderer.run_render_job)
check("the verify call is guarded", "except media.MediaError as ve" in _src)
check("...only a STITCH gets the second chance",
      "if stitched_from is None:" in _src and "raise" in _src)
check("...the fallback is a full render inside the SAME job",
      "running the full render in-job" in _src)
check("...which is then verified too",
      _src.count("_verify_render(") >= 2)

if not HAVE_FFMPEG:
    print("== 7-9 skipped (no ffmpeg) ==")
    print(f"\nALL {PASS} CHECKS PASSED")
    sys.exit(0)

print("== 7. end to end: a mid-video cut stitches to the full render ==")

d = tempfile.mkdtemp(prefix="stitch_tl_")
src = os.path.join(d, "src.mp4")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error",
     "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=20",
     "-f", "lavfi", "-i",
     "aevalsrc=sin(2*PI*220*t)*(0.2+0.8*abs(sin(2*PI*0.25*t))):s=48000",
     "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
     src], check=True)

IDX = {"video": {"duration": 20.0, "width": 320, "height": 180, "fps": 30.0},
       "words": [], "sentences": [], "silences": []}

E1 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                               "strength": 0.4}]}})
E2 = V({"keep": [[0.0, 5.0], [6.5, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                               "strength": 0.4}]}})

r1 = os.path.join(d, "r1.mp4")
renderer.render_edl(E1, IDX, src, r1, d, preview=True)
r2 = os.path.join(d, "r2.mp4")
renderer.render_edl(E2, IDX, src, r2, d, preview=True)


def stitched(prev_edl, new_edl, prev_file, out_name):
    out = os.path.join(d, out_name)
    real = renderer._cached_source
    renderer._cached_source = \
        lambda k: prev_file if k == "prev/key.mp4" else None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            out_dur = renderer._stitched_preview(
                0, {"version": 2, "json": new_edl},
                {"version": 1, "json": prev_edl},
                {"storage_key": "prev/key.mp4"}, IDX, src, d, {}, out)
    finally:
        renderer._cached_source = real
    sys.stdout.write(buf.getvalue())
    return out, out_dur, buf.getvalue()


s2, s2_dur, log = stitched(E1, E2, r1, "s2.mp4")
check("the cut stitched (timeline mode, not a fallback)",
      s2_dur is not None and "STITCHED preview (timeline)" in log)
check("stitched length == full render of the cut EDL",
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


for t, where in ((2.0, "unshifted copy"), (7.5, "shifted copy"),
                 (10.0, "deep shifted copy"), (14.2, "tail")):
    p = psnr(frame(s2, t), frame(r2, t))
    check(f"{where} matches the full render at {t}s (PSNR {p:.1f})", p > 30.0)


def pcm(path):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
         "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32)


def rms_profile(x, win=8000):
    n = len(x) // win
    return np.array([np.sqrt(np.mean(x[i * win:(i + 1) * win] ** 2) + 1e-6)
                     for i in range(n)])


pa, pb = pcm(s2), pcm(r2)
n = min(len(pa), len(pb))
prof_a, prof_b = rms_profile(pa[:n]), rms_profile(pb[:n])
delta_db = 20 * np.abs(np.log10((prof_a + 1e-3) / (prof_b + 1e-3)))
check(f"rebuilt audio matches the full render second-by-second "
      f"(max {delta_db.max():.2f} dB)", float(delta_db.max()) < 1.5)

print("== 8. head trim under an unchanged zoom ==")

E5 = V({"keep": [[3.0, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                               "strength": 0.4}]}})
r5 = os.path.join(d, "r5.mp4")
renderer.render_edl(E5, IDX, src, r5, d, preview=True)
s5, s5_dur, log = stitched(E1, E5, r1, "s5.mp4")
check("head trim stitched", s5_dur is not None
      and "STITCHED preview (timeline)" in log)
for t, where in ((3.0, "re-encoded zoom over shifted footage"),
                 (8.0, "shifted copy"), (12.0, "shifted tail")):
    p = psnr(frame(s5, t), frame(r5, t))
    check(f"{where} matches at {t}s (PSNR {p:.1f})", p > 30.0)
check("the zoom window really re-encoded (differs from the old preview)",
      psnr(frame(s5, 3.0), frame(r1, 3.0)) < 40.0)

print("== 9. audio-only change: music lands without re-encoding video ==")

wav = os.path.join(d, "song.wav")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
     "sine=frequency=660:duration=20", "-ar", "48000", wav], check=True)
E6 = V({"keep": [[0.0, 8.0], [10.0, 18.0]],
        "effects": {"zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                               "strength": 0.4}]},
        "music": [{"id": "m1", "storage_key": "lib/song.wav", "start": 0.0,
                   "end": 16.0, "gain_db": -6}]})
_real_music = renderer.music_source
renderer.music_source = lambda key, fetch: wav
try:
    r6 = os.path.join(d, "r6.mp4")
    renderer.render_edl(E6, IDX, src, r6, d, preview=True)
    s6, s6_dur, log = stitched(E1, E6, r1, "s6.mp4")
finally:
    renderer.music_source = _real_music
check("music-only change stitched with ZERO re-encoded windows",
      s6_dur is not None and "re-encoded 0.0s" in log)
for t in (2.0, 9.0, 15.0):
    p = psnr(frame(s6, t), frame(r6, t))
    check(f"video untouched at {t}s (PSNR {p:.1f})", p > 30.0)
pa, pb = pcm(s6), pcm(r6)
n = min(len(pa), len(pb))
prof_a, prof_b = rms_profile(pa[:n]), rms_profile(pb[:n])
delta_db = 20 * np.abs(np.log10((prof_a + 1e-3) / (prof_b + 1e-3)))
check(f"the music is IN the stitched audio, matching the reference "
      f"(max {delta_db.max():.2f} dB)", float(delta_db.max()) < 1.5)
prof_old = rms_profile(pcm(r1)[:n])
m = min(len(prof_a), len(prof_old))
check("...and the track genuinely changed vs the previous preview",
      float(np.max(np.abs(prof_a[:m] - prof_old[:m]))) > 1.0)

shutil.rmtree(d, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASSED")
