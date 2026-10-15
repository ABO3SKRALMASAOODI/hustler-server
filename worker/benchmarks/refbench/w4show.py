"""Wave 4 showcases (ROADMAP "Wave 4 showcase"), internal boards only.

    python -m benchmarks.refbench.w4show W4-S1

W4-S1 "Visual essay" (genre F): "how a machine learns to move" on V13
(Optimus, Starship, NASA): a LiDAR point-cloud scan of the walker, echo
trails on its stride, depth of field, archival-style cutaways graded with
grain, a 2.5D hold on the last frame, one line of type. No music (agents
never choose it: the owner lays his track), no zoom, no sound.

W4-S2 (cinematic essay) is AT-4C-1's render, W4-S3 (every aspect, every
language) AT-4E-1 and AT-4E-2, W4-S4 (product explainer) AT-4B-1 and
AT-4B-3; their boards live under boards/wave4/4C, 4E and 4B. W4-S5 (the
10x reel from ten chats) needs the in-house agent and is not built here.
"""

import copy
import json
import os
import sys
import time

import numpy as np

BOARDS = os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4/showcase")
SCRATCH = os.path.expanduser("~/Documents/Valmera/.valmera/engines/w4/show")


def w4_s1():
    from . import at4a, at4c, vfl
    os.makedirs(SCRATCH, exist_ok=True)
    win = vfl.window("V13", "optimus_walk")
    ref, info = at4a._source(win)
    keep = (0.5, 14.5)
    st = [at4a.ensure("depth.small", ref, [keep]),
          at4a.ensure("matte.person", ref, [keep])]
    launch = vfl.window("V13", "starship_launch_4k60")["path"]
    sun = vfl.window("V13", "nasa_prominence_60p")["path"]
    locs = {"media/7/starship.mp4": launch, "media/7/sun.mp4": sun}
    credit = {"provider": "owner library", "license":
              "Owner footage (internal showcase)", "title": "V13 pool"}
    edl = {"keep": [list(keep)],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5},
           "motion": [{"id": "t1", "template": "hook_title", "start": 0.3,
                       "end": 2.0, "params": {
                           "text": "HOW A MACHINE LEARNS TO MOVE"}}],
           "fx": [
               {"id": "pc", "type": "depth", "stage": "world", "start": 0.0,
                "end": 2.2, "kind": "point_cloud", "focus": {"on":
                                                           "subject"},
                "backdrop": "black", "move": {"path": "orbit_left",
                                              "amount": 0.35},
                "device": "depth_point_cloud", "seed": 7},
               {"id": "br1", "type": "broll", "stage": "screen",
                "start": 4.2, "end": 6.4, "source": {
                    "kind": "asset", "asset_key": "media/7/starship.mp4",
                    "offset_s": 3.0}, "grain": 0.3, "soften": 0.2,
                "credit": credit, "moment": "the machine that flies",
                "device": "broll_cutaway"},
               {"id": "ec", "type": "time", "stage": "world", "start": 7.0,
                "end": 8.2, "kind": "echo", "echo": {"count": 4,
                                                     "spacing_f": 2,
                                                     "decay": 0.55},
                "device": "time_echo"},
               {"id": "df", "type": "depth", "stage": "world", "start": 8.4,
                "end": 10.2, "kind": "dof", "focus": {"on": "subject"},
                "blur": 0.7, "device": "depth_dof"},
               {"id": "br2", "type": "broll", "stage": "screen",
                "start": 10.4, "end": 12.0, "source": {
                    "kind": "asset", "asset_key": "media/7/sun.mp4",
                    "offset_s": 2.0}, "fit": "frame", "grain": 0.35,
                "credit": credit, "moment": "energy",
                "device": "broll_archival"},
               {"id": "hold", "type": "depth", "stage": "world",
                "start": 12.2, "end": 14.0, "kind": "parallax",
                "move": {"path": "drift_left", "amount": 0.35},
                "device": "depth_parallax"}]}
    idx = at4a.index_for(win, ref)
    out = os.path.join(SCRATCH, "W4-S1.mp4")
    r = at4c.render(edl, win["path"], idx, out, locs)
    import qa_metrics
    fps = qa_metrics.probe(out)["fps"]
    ts = (0.8, 1.8, 3.0, 5.2, 7.6, 9.2, 11.2, 13.0)
    got, fr = qa_metrics.read_frames(out, frames={int(t * fps) for t in ts})
    tiles = [(f"{k / fps:.1f}s", at4c._thumb(np.asarray(f), 360))
             for k, f in zip(got, fr)]
    b = at4c.board(os.path.join(BOARDS, "W4-S1", "board.jpg"),
                   [("W4-S1 visual essay — scan, flight, stride, focus, "
                     "energy, hold (no music, no zoom, no sound)", tiles)],
                   "W4-S1 'how a machine learns to move' (genre F)")
    import motion_critic
    crit = motion_critic.critique(out, r["edl"], idx, deep=False,
                                  request_text="a visual essay")
    res = {"render": out, "board": b, "report": r["report"],
           "sidecars": st, "critic": {k: crit.get(k) for k in
                                      ("score", "genre")},
           "findings": [(f.get("code"), f.get("severity"), f.get("t"))
                        for f in crit.get("findings") or []]}
    os.makedirs(os.path.join(BOARDS, "W4-S1"), exist_ok=True)
    with open(os.path.join(BOARDS, "W4-S1", "result.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print(json.dumps(res, indent=1, default=str)[:3000])
    return res


CASES = {"W4-S1": w4_s1}


def main(argv):
    for c in argv or list(CASES):
        CASES[c]()


if __name__ == "__main__":
    main(sys.argv[1:])
