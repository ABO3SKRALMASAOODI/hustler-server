"""Track 1F proof loop on real renders (motion_qa.proof, LocalRenderer).

Runs in a CHILD process with ``VALMERA_FX_PROBE=1``: the engine-only
``fx_probe`` type (0B) renders a frame-exact negate and a dissolve across a
cut, so the proof is measured end to end — the changed-section preview
path, rendered with and without the devices — without any Wave 1 compiler.
Synthetic sources (ffmpeg testsrc2 + a tone) at 25, 29.97, 24 and 59.94 fps;
no footage, CI-safe.

    VALMERA_FX_PROBE=1 python w1f_proof_probe.py <workdir>

Prints one JSON report on its last line.
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import media        # noqa: E402
import motion_qa    # noqa: E402
import schemas      # noqa: E402

RATES = {"25": "25", "29.97": "30000/1001", "24": "24", "59.94": "60000/1001"}


def source(workdir, name, rate, seconds=4.0):
    path = os.path.join(workdir, f"src_{name}.mp4")
    if not os.path.exists(path):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"testsrc2=size=320x180:rate={rate}", "-f", "lavfi", "-i",
             "sine=frequency=330:sample_rate=48000", "-t", str(seconds),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "12",
             "-c:a", "aac", "-shortest", path], check=True)
    return path


def run_rate(workdir, name, rate):
    src = source(workdir, name, rate)
    info = media.probe(src)
    fps = float(info["fps"])
    index = {"video": {"duration": info["duration"], "width": info["width"],
                       "height": info["height"], "fps": fps,
                       "has_audio": True}, "words": []}
    f3 = round(fps, 3)

    def t(f):
        return round(f / f3, 2)
    end = round(info["duration"] - 0.1, 2)
    cut_src = 2.2
    edl = {"keep": [[0.0, 1.6], [cut_src, end]],
           "fx": [{"id": "n1", "type": "fx_probe", "stage": "pre_captions",
                   "op": "negate", "start": t(12), "end": t(13)},
                  {"id": "n2", "type": "fx_probe", "stage": "world",
                   "op": "negate", "start": t(int(2.6 * fps)),
                   "end": t(int(2.6 * fps) + 1)},
                  {"id": "j1", "type": "fx_probe", "stage": "junction",
                   "op": "dissolve", "at": 1.6, "frames": 3}]}
    edl = schemas.validate_edl(edl, info["duration"]).model_dump()
    wd = os.path.join(workdir, f"proof_{name}")
    r = motion_qa.LocalRenderer(index, src, wd)
    t0 = time.monotonic()
    res = motion_qa.proof(None, edl, ids=["n1", "n2", "j1"], renderer=r,
                          index=index, workdir=wd, cards={})
    wall = time.monotonic() - t0
    out = {"fps": fps, "wall_s": round(wall, 2), "pass": res["pass"],
           "notes": res["notes"], "window": res["render"]["window"],
           "reel_fps": res["render"].get("fps"), "devices": {}}
    for d in res["devices"]:
        out["devices"][d["id"]] = {
            "landing_f": d["landing_f"], "planned_f": d["planned_f"],
            "event_f": d["event_f"], "basis": d["basis"],
            "strip": bool(d.get("strip") and os.path.exists(d["strip"])),
            "onion": bool(d.get("onion") and os.path.exists(d["onion"])),
            "metrics": {k: {"value": v.get("value"), "pass": v.get("pass")}
                        for k, v in d["metrics"].items()}}
    return out


def long_junction(workdir, rate="29.97", frame=True):
    """A junction far longer than the -3..+6 strip (9 frames each side of
    its cut): its landing is the centre of its WHOLE run, so the proof
    renders and reads all of it. ``frame`` False: a frameless program, whose
    reference without its only fx item renders on another clock — the
    audio is then not compared, and the proof says so."""
    src = source(workdir, "lj", rate)
    info = media.probe(src)
    fps = float(info["fps"])
    index = {"video": {"duration": info["duration"], "width": info["width"],
                       "height": info["height"], "fps": fps,
                       "has_audio": True}, "words": []}
    end = round(info["duration"] - 0.1, 2)
    edl = {"keep": [[0.0, 1.6], [2.2, end]],
           "fx": [{"id": "jl", "type": "fx_probe", "stage": "junction",
                   "op": "dissolve", "at": 1.6, "frames": 9}]}
    if frame:
        edl["frame"] = {"ratio": "16:9", "mode": "crop"}
    edl = schemas.validate_edl(edl, info["duration"]).model_dump()
    wd = os.path.join(workdir, f"proof_long_junction_{int(frame)}")
    res = motion_qa.proof(None, edl, ids=["jl"],
                          renderer=motion_qa.LocalRenderer(index, src, wd),
                          index=index, workdir=wd, cards={})
    d = res["devices"][0]
    return {"pass": res["pass"], "landing_f": d["landing_f"],
            "planned_f": d["planned_f"], "event_f": d["event_f"],
            "window": res["render"]["window"], "notes": res["notes"],
            "metrics": {k: {"value": v.get("value"), "pass": v.get("pass"),
                            "detail": v.get("detail")}
                        for k, v in d["metrics"].items()}}


class _NoWithout(motion_qa.LocalRenderer):
    def render(self, edl, ranges, tag):
        if tag == "without":
            raise RuntimeError("reference render unavailable")
        return super().render(edl, ranges, tag)


def temporal(workdir):
    src = source(workdir, "25", "25")
    info = media.probe(src)
    index = {"video": {"duration": info["duration"], "width": 320,
                       "height": 180, "fps": 25.0, "has_audio": True},
             "words": []}
    edl = schemas.validate_edl(
        {"keep": [[0.0, 3.8]], "fx": [
            {"id": "n1", "type": "fx_probe", "stage": "pre_captions",
             "op": "negate", "start": 1.0, "end": 1.04}]},
        info["duration"]).model_dump()
    wd = os.path.join(workdir, "proof_temporal")
    res = motion_qa.proof(None, edl, ids=["n1"],
                          renderer=_NoWithout(index, src, wd), index=index,
                          workdir=wd, cards={})
    d = res["devices"][0]
    return {"basis": d["basis"], "event_f": d["event_f"],
            "planned_f": d["planned_f"], "notes": res["notes"],
            "land": d["metrics"]["land_err_f"]["value"]}


def jobs(workdir):
    """The production path: proof_jobs on a ToolContext whose queue runs
    each preview_check payload through renderer._render_changed_sections
    (what the executor does) and hands back the concatenated reel and its
    proof_segments; the reference is the previous version, which lacks the
    devices on the same timeline."""
    import renderer
    import storage
    src = source(workdir, "25", "25")
    info = media.probe(src)
    index = {"video": {"duration": info["duration"], "width": 320,
                       "height": 180, "fps": 25.0, "has_audio": True},
             "words": []}
    base = schemas.validate_edl({"keep": [[0.0, 3.8]],
                                 "frame": {"ratio": "16:9", "mode": "crop"}},
                                info["duration"]).model_dump()
    cur = schemas.validate_edl(dict(base, fx=[
        {"id": "n1", "type": "fx_probe", "stage": "pre_captions",
         "op": "negate", "start": 1.0, "end": 1.04},
        {"id": "n2", "type": "fx_probe", "stage": "world", "op": "negate",
         "start": 2.6, "end": 2.64}]), info["duration"]).model_dump()
    versions = {4: base, 5: cur}
    wd = os.path.join(workdir, "proof_jobs")
    os.makedirs(wd, exist_ok=True)
    queue = {}

    def run_job(jid, payload):
        out = os.path.join(wd, f"job{jid}.mp4")
        segs = []
        jwd = os.path.join(wd, f"job{jid}")
        os.makedirs(jwd, exist_ok=True)
        renderer._render_changed_sections(
            jid, {"json": versions[payload["edl_version"]]}, index, src, jwd,
            {}, out, payload["check_ranges"], [],
            raw_pages=payload["check_pages"], proof_segments=segs)
        return {"render_asset_id": 1000 + jid, "proof_segments": segs,
                "edl_version": payload["edl_version"]}, out

    class DB:
        def run(self, fn, *a):
            name = fn.__name__
            if name == "edl_history":
                return [{"version": 5}, {"version": 4}]
            if name == "get_edl_version":
                return {"version": a[1], "json": versions[a[1]]}
            if name == "get_or_enqueue_preview_check_job":
                jid = len(queue) + 1
                queue[jid] = run_job(jid, a[2])
                return jid, True
            if name == "get_job":
                return {"state": "done", "result": queue[a[0]][0]}
            if name == "get_asset":
                jid = a[0] - 1000
                return {"storage_key": queue[jid][1]}
            raise AssertionError(name)

    class Ctx:
        db = DB()
        project_id = 3
        workdir = wd
        job = {"id": 9, "user_id": 2, "type": "agent_turn", "payload": {}}
        project = {"id": 3}
        index = None

        def latest_edl(self):
            return {"version": 5, "json": cur}
    real = storage.download_to
    storage.download_to = lambda key, dest: __import__("shutil").copy(
        key, dest)
    try:
        res = motion_qa.proof_jobs(Ctx(), cur, index=index, cards={},
                                   previous_edl=base, budget_s=60)
    finally:
        storage.download_to = real
    return {"pass": res["pass"], "notes": res["notes"],
            "jobs": res["render"].get("jobs"),
            "devices": {d["id"]: {"landing_f": d["landing_f"],
                                  "event_f": d["event_f"],
                                  "basis": d["basis"]}
                        for d in res["devices"]}}


def vfl_window(workdir):
    """Local only: a 12 s, 9:16 program of V02 (the SC-Thiel window) with
    two frame-exact negates placed on the planner's own stressed words; the
    rendered landing of each is also counted on an independent full render
    of the program (the frame whose negative best matches its
    neighbours)."""
    import numpy as np
    import device_planner
    import qa_metrics
    import renderer
    sys.path.insert(0, os.path.join(WORKER))
    from benchmarks.refbench import engine, vfl
    w = vfl.window("V02", "thiel_flying_cars")
    dur = round(w["file_span"][1] - w["file_span"][0], 3)
    index = engine.index_for(w)
    base = engine.window_edl(w, "9:16")
    a = base["keep"][0][0] + 20.0
    base["keep"] = [[round(a, 2), round(a + 12.0, 2)]]
    base = schemas.validate_edl(base, dur).model_dump()
    plan = device_planner.moments(base, index)
    fps = plan["fps"]
    picks = sorted((m for m in plan["moments"]
                    if m["kind"] in ("stress", "word")),
                   key=lambda m: -m["strength"])
    chosen = []
    for m in picks:
        if all(abs(m["t"] - c["t"]) > 2.0 for c in chosen):
            chosen.append(m)
        if len(chosen) == 2:
            break
    fx = []
    for k, m in enumerate(chosen):
        f = device_planner.frame_of(m["t"], fps)
        fx.append({"id": f"d{k}", "type": "fx_probe",
                   "stage": ("world", "pre_captions")[k], "op": "negate",
                   "start": device_planner.time_of(f, fps),
                   "end": device_planner.time_of(f + 1, fps),
                   "bind": m["bind"]})
    edl = schemas.validate_edl(dict(base, fx=fx), dur).model_dump()
    wd = os.path.join(workdir, "vfl")
    t0 = time.monotonic()
    res = motion_qa.proof(None, edl, renderer=motion_qa.LocalRenderer(
        index, w["path"], wd), index=index, workdir=wd, cards={},
        previous_edl=base)
    wall = time.monotonic() - t0
    full = os.path.join(wd, "full.mp4")
    renderer.render_edl(json.loads(json.dumps(edl)), index, w["path"], full,
                        os.path.join(wd, "fullwd"), preview=True,
                        suppress_outro=True)
    finfo = qa_metrics.probe(full)
    manual = {}
    for it in fx:
        f0 = device_planner.frame_of(it["start"], finfo["fps"])
        idx, frames = qa_metrics.read_frames(full,
                                             frames=set(range(f0 - 4, f0 + 5)))
        fr = dict(zip(idx, frames))

        def score(k):
            inv = 255 - fr[k].astype(np.int16)
            return np.abs(inv - fr[k - 1]).mean() + np.abs(inv - fr[k + 1]).mean()
        manual[it["id"]] = min((k for k in fr if k - 1 in fr and k + 1 in fr),
                               key=score)
    return {"wall_s": round(wall, 2), "pass": res["pass"],
            "devices": {d["id"]: {"event_f": d["event_f"],
                                  "planned_f": d["planned_f"],
                                  "manual_f": manual[d["id"]]}
                        for d in res["devices"]}}


def main():
    workdir = sys.argv[1]
    os.makedirs(workdir, exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[2] == "vfl":
        print(json.dumps({"vfl": vfl_window(workdir)}, default=str))
        return
    report = {}
    for name, rate in RATES.items():
        try:
            report[name] = run_rate(workdir, name, rate)
        except Exception as e:  # noqa: BLE001
            import traceback
            report[name] = {"error": f"{type(e).__name__}: {e}",
                            "trace": traceback.format_exc()[-2500:]}
    try:
        report["jobs"] = jobs(workdir)
    except Exception as e:  # noqa: BLE001
        import traceback
        report["jobs"] = {"error": str(e),
                          "trace": traceback.format_exc()[-2500:]}
    for key, framed in (("long_junction", True),
                        ("long_junction_frameless", False)):
        try:
            report[key] = long_junction(workdir, frame=framed)
        except Exception as e:  # noqa: BLE001
            import traceback
            report[key] = {"error": str(e),
                           "trace": traceback.format_exc()[-2500:]}
    try:
        report["temporal"] = temporal(workdir)
    except Exception as e:  # noqa: BLE001
        import traceback
        report["temporal"] = {"error": str(e),
                              "trace": traceback.format_exc()[-2500:]}
    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
