"""Round 88 — where an agent turn's twelve minutes actually went.

Three prod turns hit the wall in a week (jobs 2353, 2491, 2521) and in none of
them was the MODEL slow. Job 2521 (abdotamimaaltaher@gmail.com, project 360,
Aug 5 2026) made every edit it ever made in its first 95 seconds of model time
and then spent 762 seconds inside three sequential inpaint passes — 120s, 162s,
425s — because each one re-derives the whole cleaned source from the untouched
original. The user, writing in Arabic throughout, got an English "that took
longer than I allow myself" and left. Job 2353 spent 823 of its 863 seconds
inside five of them.

Measured over every agent turn in production (video_jobs.result->'timings'):

    render_preview   435 calls   15,793s    36.3s mean   <- more than all others
    look_at          487          4,999     10.3
    erase_region      11          1,979    179.9         (425s worst)
    look_at_asset     89          1,189     13.4
    add_text_behind   53          1,152     21.7
    erase_burned_text  2            356    178.0

and over 379 real previews: 27.3s mean, of which ENCODE is 19.2s — scaling with
the FOOTAGE, not the edit (a 186s video: 144.6s; a 140s video: 205.5s), because
a preview was rendered at the source's full frame and full rate.

Four changes, tested here:

  1. A preview is a PROOF, not a deliverable. Capped at a 1280 long edge and
     30fps, a 1080x1920@60 reel goes from 124 Mpx/s to 28 — and nothing that
     reads a preview wanted those pixels (the studio plays it in a panel; the
     agent reads it as 480x270 contact-sheet tiles; look_at samples the proxy,
     never the render). Exports are untouched.
  2. THE INPAINT RUNS AT THE SCALE OF THE HOLE. cv2.inpaint(TELEA) diffuses
     inward from a hole's boundary: its cost grows with the hole's area, the
     detail it can invent does not. er3 was a ~822,000-pixel 'box' hole
     TELEA'd 330 times at 1080p — 606ms a frame. Diffused at the scale its
     own smoothness justifies it is 96ms, and only masked pixels are ever
     taken from the scaled result, so the picture around it is bit-exact.
     Thin caption strokes are under the budget and are untouched.

     An earlier draft of this round refused slow tools when the turn was
     running out instead. That is not a fix — it is the agent being told it
     may not do its job, and it was removed. Make the work fast; do not take
     the work away.
  3. A rectangle fully covered by a later, wider one is dropped. Every repaint
     redoes every region forever, so project 360 finished carrying a dead er2
     inside er3 as a tax on all its future passes.
  4. add_text's clamp is spoken. On project 363 six captions were written for
     a 19s reel while the program was still the bare 1.6s clip; all six
     silently collapsed into 1.3-1.6s, read back as successes, and cost that
     turn 29 add_text and 17 remove_text calls to undo.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                                   # noqa: E402
import numpy as np                                           # noqa: E402

import agent_tools                                           # noqa: E402
import config                                                # noqa: E402
import inpaint                                               # noqa: E402
import renderer                                              # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f" {label}: {got!r}"
          + ("" if ok else f" (want {want!r})"))
    if not ok:
        fails.append(label)


print("== the preview renders at the proof budget ==")
# The exact shape of project 360's footage.
check("1080x1920@60 -> 720x1280@30",
      renderer.preview_geometry(1080, 1920, 60.0), (720, 1280, 30.0))
check("1920x1080@60 landscape",
      renderer.preview_geometry(1920, 1080, 60.0), (1280, 720, 30.0))
# A cap, never a resize: anything already under it is left exactly alone.
check("1080x1080 square untouched",
      renderer.preview_geometry(1080, 1080, 30.0), (1080, 1080, 30.0))
check("540x960@24 never up-scaled",
      renderer.preview_geometry(540, 960, 24.0), (540, 960, 24.0))

w, h, fps = renderer.preview_geometry(1080, 1920, 60.0)
check("aspect ratio preserved", round(w / h, 4), round(1080 / 1920, 4))
check("dims stay even (x264 needs it)", (w % 2, h % 2), (0, 0))
check("throughput cut at least 4x",
      (1080 * 1920 * 60) / (w * h * fps) >= 4.0, True)

_cap = config.PREVIEW_MAX_LONG_EDGE
config.PREVIEW_MAX_LONG_EDGE = 0
check("cap off restores the source frame exactly",
      renderer.preview_geometry(1080, 1920, 60.0)[:2], (1080, 1920))
config.PREVIEW_MAX_LONG_EDGE = _cap


print("\n== the inpaint runs at the scale of the hole ==")
# Project 360's er3: a 0.9x0.45 'box' repaint of a 1080x1920 video is a
# ~822,000-pixel hole, TELEA'd once per frame for 330 frames. That ONE call
# took 425 seconds. Measured here: 606ms -> 96ms per frame.
band = np.zeros((864, 972, 3), np.uint8)
band[:] = 128
big = np.zeros((864, 972), bool)
big[8:-8, 8:-8] = True                       # ~822k masked pixels
check("a big hole is scaled down",
      int(big.sum()) > config.INPAINT_MAX_PX, True)

t0 = time.perf_counter()
scaled = inpaint._telea(band, big)
t_new = time.perf_counter() - t0
t0 = time.perf_counter()
cv2.inpaint(np.ascontiguousarray(band), big.astype(np.uint8) * 255, 3,
            cv2.INPAINT_TELEA)
t_old = time.perf_counter() - t0
print(f"  -- er3's shape: {t_old*1000:.0f}ms -> {t_new*1000:.0f}ms "
      f"({t_old/max(t_new,1e-9):.1f}x)")
check("the big hole got materially faster", t_old / max(t_new, 1e-9) > 2.0, True)

# THE INVARIANT THAT MAKES IT SAFE: only masked pixels ever come from the
# resized result, so the surrounding picture is bit-exact at any scale.
noise = np.random.default_rng(3).integers(0, 255, (864, 972, 3), dtype=np.uint8)
check("pixels outside the mask are bit-exact",
      np.array_equal(inpaint._telea(noise, big)[~big], noise[~big]), True)

# Thin strokes — a caption's letters — are where boundary detail IS the
# output. They are under the budget, so they take the untouched full-res path.
strokes = np.zeros((505, 852), bool)
strokes[240:260, 100:750] = True             # ~13k px
check("a thin-stroke mask stays under budget",
      int(strokes.sum()) <= config.INPAINT_MAX_PX, True)
check("and comes out identical to plain TELEA",
      np.array_equal(
          inpaint._telea(noise[:505, :852], strokes),
          cv2.inpaint(np.ascontiguousarray(noise[:505, :852]),
                      strokes.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)),
      True)

# Just over the budget the two resizes cost more than the TELEA they save, so
# the scaled path is only taken when the saving is real.
mid = np.zeros((505, 852), bool)
mid[60:445, 60:792] = True                   # ~280k px, k ~= 0.65
edge = np.zeros((505, 852), bool)
edge[100:400, 100:600] = True                # 150k px, k ~= 0.89 -> full res
check("k just under 1 takes the full-res path",
      np.array_equal(
          inpaint._telea(noise[:505, :852], edge),
          cv2.inpaint(np.ascontiguousarray(noise[:505, :852]),
                      edge.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)),
      True)
check("a clearly-worth-it mask does not",
      np.array_equal(
          inpaint._telea(noise[:505, :852], mid),
          cv2.inpaint(np.ascontiguousarray(noise[:505, :852]),
                      mid.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)),
      False)

_b = config.INPAINT_MAX_PX
config.INPAINT_MAX_PX = 0
check("budget=0 restores plain full-res TELEA everywhere",
      np.array_equal(
          inpaint._telea(band, big),
          cv2.inpaint(np.ascontiguousarray(band), big.astype(np.uint8) * 255,
                      3, cv2.INPAINT_TELEA)), True)
config.INPAINT_MAX_PX = _b

check("no pre-flight tool guard survives — slowness is fixed, not refused",
      "_too_slow_to_start" in open(
          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "agent_loop.py"), encoding="utf-8").read(),
      False)


print("\n== the superseded rectangle is dropped ==")
# Verbatim from project 360's final EDL (v27).
er1 = {"id": "er1", "x": .102, "y": .398, "w": .789, "h": .263,
       "start": None, "end": None, "fill": "text"}
er2 = {"id": "er2", "x": .1, "y": .35, "w": .8, "h": .35,
       "start": 13.0, "end": 18.0, "fill": "text"}
er3 = {"id": "er3", "x": .05, "y": .3, "w": .9, "h": .45,
       "start": 13.0, "end": 18.5, "fill": "box"}
check("er3 subsumes er2 (the dead rectangle)",
      agent_tools._subsumed_by(er2, er3), True)
check("er3 does NOT subsume er1 — er1 covers the whole video",
      agent_tools._subsumed_by(er1, er3), False)
check("the narrower pass never eats the wider one",
      agent_tools._subsumed_by(er3, er2), False)
check("a 'box' is never dropped for a same-sized 'text'",
      agent_tools._subsumed_by(dict(er2, fill="box"),
                               dict(er3, fill="text")), False)
check("an identical rectangle and window subsumes",
      agent_tools._subsumed_by(er2, dict(er2, id="erX")), True)
check("a shorter window does not subsume",
      agent_tools._subsumed_by(er2, dict(er3, start=14.0, end=17.0)), False)
check("a whole-video repaint subsumes a windowed one",
      agent_tools._subsumed_by(er2, dict(er3, start=None, end=None)), True)


print("\n== the clamp is spoken, not swallowed ==")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "agent_tools.py"), encoding="utf-8").read()
# Join adjacent string literals so a phrase can be asserted as the AGENT reads
# it, not as the source happens to be wrapped.
_flat = re.sub(r'"\s*\n\s*"', "", _src)
check("add_text warns when the window was clamped",
      "CLAMPED: you asked for" in _flat, True)
check("the warning names the cause, not just the fact",
      "place the media first" in _flat, True)
check("it rides on the write result the agent reads back",
      "f\"[{item['id']}]\") + clamped" in _src, True)

_loop = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "agent_loop.py"), encoding="utf-8").read()
check("the dispatch loop has no cost-based tool refusal in it",
      "no_time" in _loop or "SLOW_TOOL_COST_S" in _loop, False)


print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("round 88: all checks passed")
