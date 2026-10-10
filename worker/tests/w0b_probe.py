"""Probe-lane render harness for the track 0B acceptance tests.

Runs in a CHILD process started with ``VALMERA_FX_PROBE=1`` (the engine-only
``fx_probe`` type is registered only there; see fx_schemas/core.py), renders
the cases a test describes through the real ``renderer.render_edl`` /
``renderer._stitched_preview`` path, and writes plain JSON results the
parent test asserts on.

Every encode is made LOSSLESS (x264 ``-crf 0``) so decoded frames are the
filtergraph's own frames: a lossy encoder would smear a 1-frame change over
its GOP and hide exactly what these tests pin.

    python w0b_probe.py <spec.json> <out.json>

spec: {"cases": [{"name", "src", "idx", "edl", "preview", "want_wm",
                  "asset_locals", "faults", "dump_frames", "graph",
                  "stitch": {"prev_edl": ..., "prev_out": name}}]}
"""

import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)


def _lossless(run):
    def wrapped(cmd, **kw):
        cmd = list(cmd)
        if "-crf" in cmd:
            cmd[cmd.index("-crf") + 1] = "0"
        for flag in ("-maxrate", "-bufsize"):
            while flag in cmd:
                i = cmd.index(flag)
                del cmd[i:i + 2]
        return run(cmd, **kw)
    return wrapped


def frame_md5s(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                          "0:v:0", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return [ln.split(",")[-1].strip() for ln in out.splitlines()
            if ln and not ln.startswith("#")]


def audio_md5(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                        "0:a:0", "-f", "md5", "-"], capture_output=True,
                       text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def dump_frames(path, frames, dest):
    """Decoded yuv420p planes of the given frame indexes, as raw files."""
    out = {}
    for k in frames:
        f = os.path.join(dest, f"{os.path.basename(path)}.{k}.yuv")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                        f"select=eq(n\\,{int(k)})", "-frames:v", "1",
                        "-pix_fmt", "yuv420p", "-f", "rawvideo", f],
                       check=True)
        out[str(k)] = f
    return out


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams",
                          "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of",
                          "json", path], capture_output=True, text=True,
                         check=True).stdout
    s = json.loads(out)["streams"][0]
    return {"width": s["width"], "height": s["height"],
            "r_frame_rate": s["r_frame_rate"]}


def run_case(case, workroot, renderer, fxgraph, faultinject, stitch):
    name = case["name"]
    wd = os.path.join(workroot, name)
    os.makedirs(wd, exist_ok=True)
    out = os.path.join(workroot, f"{name}.mp4")
    rows, graphs, windows = [], [], []
    tok = fxgraph.collect(rows)
    real_build = renderer.build_filtergraph
    real_snap = stitch.snap_windows

    def build(*a, **kw):
        g = real_build(*a, **kw)
        graphs.append(g)
        return g

    def snap(*a, **kw):
        w = real_snap(*a, **kw)
        windows.append(w)
        return w
    renderer.build_filtergraph = build
    stitch.snap_windows = snap
    res = {"name": name, "error": None}
    try:
        with faultinject.inject(*(case.get("faults") or [])):
            if case.get("stitch"):
                st = case["stitch"]
                prev_local = os.path.join(workroot, f"{st['prev_out']}.mp4")
                real_cached = renderer._cached_source
                renderer._cached_source = (
                    lambda k: prev_local if k == "prev/key.mp4" else None)
                try:
                    dur = renderer._stitched_preview(
                        0, {"version": 2, "json": case["edl"]},
                        {"version": 1, "json": st["prev_edl"]},
                        {"storage_key": "prev/key.mp4"}, case["idx"],
                        case["src"], wd, {}, out,
                        preview=case.get("preview", True))
                finally:
                    renderer._cached_source = real_cached
            else:
                dur = renderer.render_edl(
                    json.loads(json.dumps(case["edl"])), case["idx"],
                    case["src"], out, wd,
                    preview=case.get("preview", True),
                    want_wm=case.get("want_wm", False),
                    asset_locals=case.get("asset_locals") or {},
                    suppress_outro=case.get("suppress_outro", False))
        res["duration"] = dur
        if dur is not None and os.path.exists(out):
            res["frames"] = frame_md5s(out)
            res["audio"] = audio_md5(out)
            res["probe"] = probe(out)
            if case.get("dump_frames"):
                res["dumps"] = dump_frames(out, case["dump_frames"], wd)
        res["windows"] = windows[-1] if windows else None
    except Exception as e:  # noqa: BLE001 — the parent asserts on it
        res["error"] = f"{type(e).__name__}: {str(e)[-600:]}"
    finally:
        fxgraph.stop_collecting(tok)
        renderer.build_filtergraph = real_build
        stitch.snap_windows = real_snap
    res["report"] = fxgraph.merge_report(rows)
    if case.get("graph"):
        res["graphs"] = graphs
    return res


def main(spec_path, out_path):
    import faultinject
    import fxgraph
    import renderer
    import schemas
    import stitch
    assert "fx_probe" in schemas.FX_TYPES, "run with VALMERA_FX_PROBE=1"
    renderer._render_media_run = _lossless(renderer._render_media_run)
    if os.environ.get("W0B_BREAK_PROBE") == "1":
        # an fx graph ffmpeg rejects when it runs (the fallback test)
        real_stage = type(fxgraph.PROBE).stage

        def broken(self, ctx, items):
            out = real_stage(self, ctx, items)
            ctx.parts.append(f"[{out}]negate=no_such_option=1[{out}x]")
            return f"{out}x"
        type(fxgraph.PROBE).stage = broken
    spec = json.load(open(spec_path))
    workroot = spec["workroot"]
    os.makedirs(workroot, exist_ok=True)
    results = {}
    for case in spec["cases"]:
        results[case["name"]] = run_case(case, workroot, renderer, fxgraph,
                                         faultinject, stitch)
    with open(out_path, "w") as f:
        json.dump(results, f)


def run(cases, workroot, timeout=1800):
    """Parent side: run ``cases`` in a probe-lane child; returns results."""
    os.makedirs(workroot, exist_ok=True)
    spec = os.path.join(workroot, "spec.json")
    out = os.path.join(workroot, "results.json")
    with open(spec, "w") as f:
        json.dump({"workroot": workroot, "cases": cases}, f)
    env = dict(os.environ, VALMERA_FX_PROBE="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [WORKER, os.path.dirname(WORKER)]
        + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, os.path.abspath(__file__), spec, out],
                       cwd=WORKER, env=env, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"probe child failed:\n{r.stdout[-3000:]}\n"
                           f"{r.stderr[-3000:]}")
    with open(out) as f:
        return json.load(f)


def make_source(path, *, w=640, h=360, rate="25", dur=12.0, freq=440):
    """A synthetic source: testsrc2 picture (every frame different), sine
    audio, H.264 with a 1 s GOP."""
    if os.path.exists(path):
        return path
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:r={rate}:d={dur}", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:sample_rate=48000:"
                    f"duration={dur}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "12", "-g", "25", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", path], check=True)
    return path


def make_tone(path, dur=30.0, freq=660):
    if os.path.exists(path):
        return path
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:sample_rate=48000:duration={dur}",
                    "-c:a", "aac", path], check=True)
    return path


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
