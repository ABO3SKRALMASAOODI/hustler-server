"""AT-4F-2: flash-safe mode on a strobing 16th grid (track 4F).

    cd worker && python -m benchmarks.refbench.at4f AT-4F-2

Reference moment 19@4.30-17.27: white hits on a 16th grid. Setup here: the
V01 talking window under the owner-named track (National Treasures, 129
BPM; internal fixture, provenance ``user_named``), from 4 s before the
first drop; 22 white hits on the track's 16th grid in bursts that open on
each downbeat. Pass: the edit is flagged (peak > 3 per second, no opt-in);
the flash-safe plan keeps every downbeat hit; the render of the thinned
edit measures <= 3 flashes in every second (photosens on the rendered
frames).
"""

import json
import os
import time

from . import fixtures_dir, out_dir

MUSIC = "music/drake-national-treasures"
OFFSET_S = 18.0          # 4.2 s before drop 1 (22.19 s)
DUR_S = 13.0
HITS = 22


def _grid():
    with open(os.path.join(fixtures_dir(), MUSIC, "analysis.json")) as f:
        a = json.load(f)
    beats = [float(b["t"]) for b in a["beats"]]
    downs = [float(t) for t in a.get("downbeats_s") or []]
    return beats, downs


def build():
    from . import at4f
    from schemas import validate_edl
    win = at4f._win("V01", "thiel_ai_bigger")
    a, b = at4f._span(win, 0)
    b = a + DUR_S
    beats, downs = _grid()
    lo, hi = OFFSET_S, OFFSET_S + DUR_S
    pb = [t - lo for t in beats if lo <= t < hi]
    pd = [t - lo for t in downs if lo <= t < hi]
    six = []
    for i in range(len(pb) - 1):
        step = (pb[i + 1] - pb[i]) / 4.0
        six += [pb[i] + k * step for k in range(4)]
    fps = float(win["fps"])
    hits = []
    for d in pd:
        burst = [t for t in six if d - 1e-3 <= t < d + 4 * (60 / 129.0) / 4
                 + 1e-3][:4]
        hits += burst
    hits = sorted(set(round(t, 3) for t in hits))[:HITS]
    fx = []
    for k, t in enumerate(hits):
        f = int(round(t * fps))
        fx.append({"id": f"wh{k:02d}", "type": "accent",
                   "stage": "pre_captions", "start": round(f / fps, 2),
                   "end": round((f + 1) / fps, 2), "kind": "white_hit",
                   "device": "accent_white_hit"})
    song = os.path.join(fixtures_dir(), MUSIC,
                        "national-treasures.48k.f32.wav")
    edl = at4f._base(win, a, b)
    edl["music"] = [{"id": "m1", "storage_key": "local/music/nt.wav",
                     "start": 0.0, "end": DUR_S, "offset_s": OFFSET_S,
                     "gain_db": -12.0, "duck": True,
                     "provenance": "user_named"}]
    edl["fx"] = fx
    dur = float(win["file_span"][1] - win["file_span"][0])
    edl = validate_edl(edl, dur).model_dump()
    rmap = {"version": 1, "source": "music", "clock": "media",
            "beats": pb, "downbeats": pd}
    return win, edl, {"local/music/nt.wav": song}, {"m1": rmap}, hits


def run():
    import copy
    import flash_safety
    import photosens
    from . import engine
    od = os.path.join(out_dir(), "AT-4F-2")
    os.makedirs(od, exist_ok=True)
    win, edl, locals_, maps, hits = build()
    fps = float(win["fps"])
    before = flash_safety.assess(edl, fps=fps)
    plan = photosens.safe_plan(edl, fps=fps, maps=maps)
    thinned = copy.deepcopy(edl)
    gone = {op["id"] for op in plan["ops"]}
    thinned["fx"] = [i for i in thinned["fx"] if i["id"] not in gone]
    out_raw = os.path.join(od, "strobe_raw.mp4")
    out_safe = os.path.join(od, "strobe_safe.mp4")
    t = time.time()
    engine.render(edl, win, out_raw, os.path.join(od, "w_raw"),
                  preview=True, locals_=locals_)
    engine.render(thinned, win, out_safe, os.path.join(od, "w_safe"),
                  preview=True, locals_=locals_)
    wall = time.time() - t
    prog = DUR_S
    raw = photosens.analyse(path=out_raw, end_s=prog)
    safe = photosens.analyse(path=out_safe, end_s=prog)
    downbeat_ids = {e["id"] for e in plan["kept"] if e.get("downbeat")}
    starts = [float(i["start"]) for i in edl["fx"]]
    n_down = sum(1 for d in maps["m1"]["downbeats"]
                 if any(abs(s - d) <= 0.06 for s in starts))
    verdict = {
        "case": "AT-4F-2", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hits": len(edl["fx"]),
        "downbeats_in_window": len(maps["m1"]["downbeats"]),
        "downbeats_with_a_hit": n_down,
        "edit_peak_before": before["peak_per_s"],
        "edit_flagged": bool(before["over_limit"]),
        "kept": len(plan["kept"]), "thinned": len(plan["thinned"]),
        "kept_downbeat_hits": len(downbeat_ids),
        "edit_peak_after": plan["peak_after"],
        "render_peak_before": raw["peak_per_s"],
        "render_peak_after": safe["peak_per_s"],
        "render_over_after": safe["over"], "wall_s": round(wall, 1),
        "pass": bool(before["over_limit"]) and len(downbeat_ids) == n_down
        and safe["peak_per_s"] <= 3}
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(verdict, f, indent=1)
    print(json.dumps(verdict, indent=1))
    return verdict
