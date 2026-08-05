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
  2. The turn deadline was checked only BETWEEN iterations, so a tool could
     start with five minutes left and run for seven. A tool whose measured
     cost cannot fit is now refused before it starts.
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
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The repaint/vision modules pull OpenCV; nothing under test touches it, and a
# stub keeps this file runnable on a box without the native wheel.
sys.modules.setdefault("cv2", types.ModuleType("cv2"))

import agent_loop                                            # noqa: E402
import agent_tools                                           # noqa: E402
import config                                                # noqa: E402
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


print("\n== a tool that cannot finish is refused before it starts ==")


def at(elapsed, name, timings=None):
    """The guard's answer `elapsed` seconds into a turn."""
    return agent_loop._too_slow_to_start(name, time.monotonic() - elapsed,
                                         timings or {"tools": {}})


check("a fast tool is never guarded", at(880, "add_text"), None)
# render_preview is what the reserve is being reserved FOR — guarding it would
# take away the turn's only way to hand over what it built.
check("render_preview is never guarded", at(880, "render_preview"), None)
check("an erase early in the turn runs", at(10, "erase_region"), None)

# Project 360's third pass began at t=413s. Under the 900s ceiling there is
# room for it, and with previews at the proof budget the turn now lands
# instead of being guillotined — the guard is the backstop, not the plan.
check("erase at t=413 of a 900s turn is allowed",
      at(413, "erase_region"), None)

late = at(600, "erase_region")
check("erase with 300s left is refused", bool(late), True)
check("the refusal names the tool", "erase_region" in (late or ""), True)
check("it forbids a retry (the cost is the pass, not the args)",
      "Do NOT retry" in (late or ""), True)
check("it states nothing changed", "Nothing was changed" in (late or ""), True)
check("it tells the agent to land and name the next step",
      "render_preview" in (late or "") and "continue" in (late or ""), True)

# What THIS turn measured outranks the cold table: a first pass that cost 420s
# means the next one costs at least that.
measured = {"tools": {"erase_region": {"n": 1, "s": 420.0}}}
check("a measured 420s pass blocks the next one at t=413",
      bool(at(413, "erase_region", measured)), True)
check("the cold table alone would have waved it through",
      at(413, "erase_region"), None)

_res = config.AGENT_TURN_RESERVE_S
config.AGENT_TURN_RESERVE_S = 0
check("reserve=0 disables the guard entirely",
      at(600, "erase_region"), None)
config.AGENT_TURN_RESERVE_S = _res

check("the ceiling is above every guarded estimate",
      max(agent_loop.SLOW_TOOL_COST_S.values())
      + config.AGENT_TURN_RESERVE_S < config.AGENT_TURN_TIMEOUT_S, True)
# The two in-turn fetch/generate ceilings must still fit under the wall.
check("VIDEO_POLL_TIMEOUT_S still fits",
      config.VIDEO_POLL_TIMEOUT_S < config.AGENT_TURN_TIMEOUT_S, True)
check("FETCH_TIMEOUT_S still fits",
      config.FETCH_TIMEOUT_S < config.AGENT_TURN_TIMEOUT_S, True)


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
check("the guard is wired into dispatch, computed once",
      "no_time = (None if args is None else" in _loop, True)
check("the refusal is fed back as this call's tool result",
      "result = no_time" in _loop, True)


print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("round 88: all checks passed")
