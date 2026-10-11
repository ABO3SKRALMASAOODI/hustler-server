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


def main():
    workdir = sys.argv[1]
    os.makedirs(workdir, exist_ok=True)
    report = {}
    for name, rate in RATES.items():
        try:
            report[name] = run_rate(workdir, name, rate)
        except Exception as e:  # noqa: BLE001
            import traceback
            report[name] = {"error": f"{type(e).__name__}: {e}",
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
