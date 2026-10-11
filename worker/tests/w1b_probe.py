"""Render harness for track 1B's tests (subject FX and backgrounds).

Runs in a CHILD process where track 1B's types are registered (they are
live; ``VALMERA_FX_PREVIEW=1b`` keeps this harness working on a tree where
they are not), with the track-1A / 1E stand-ins of ``w1b_stub1a`` installed
per case, and renders every case through the real ``renderer.render_edl``
with LOSSLESS encodes (x264 crf 0), so decoded frames are the filtergraph's
own frames.

    python w1b_probe.py <spec.json> <out.json>

spec: {"workroot", "cases": [{"name", "src", "src_fps", "idx", "edl",
       "matte" (source-space gray video or null), "matte_status",
       "matte_windows", "hue" (bool), "preview", "want_wm",
       "asset_locals", "faults", "dump_frames", "png_frames", "graph",
       "suppress_outro"}]}
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import w0b_probe        # noqa: E402  (frame_md5s, dump_frames, probe, ...)


def png_frames(path, frames, dest):
    out = {}
    for k in frames:
        f = os.path.join(dest, f"{os.path.basename(path)}.{k}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                        f"select=eq(n\\,{int(k)})", "-frames:v", "1", f],
                       check=True)
        out[str(k)] = f
    return out


def run_case(case, workroot):
    import faultinject
    import fxgraph
    import renderer
    import subject_fx
    import w1b_stub1a
    name = case["name"]
    wd = os.path.join(workroot, name)
    os.makedirs(wd, exist_ok=True)
    out = os.path.join(workroot, f"{name}.mp4")
    rows, graphs = [], []
    tok = fxgraph.collect(rows)
    real_build = renderer.build_filtergraph

    def build(*a, **kw):
        g = real_build(*a, **kw)
        graphs.append(g)
        return g
    renderer.build_filtergraph = build
    patch = w1b_stub1a.Patch()
    if case.get("matte"):
        w1b_stub1a.install_matte(
            patch, case["matte"], float(case.get("src_fps") or 25.0),
            windows=case.get("matte_windows"),
            status=case.get("matte_status", "ready"),
            src_start=float(case.get("src_start") or 0.0))
    if case.get("hue"):
        w1b_stub1a.install_hue(patch)
    locals_ = case.get("asset_locals") or {}
    atok = subject_fx.ASSET_LOCALS.set(locals_)
    res = {"name": name, "error": None}
    import resource
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t = time.time()
    try:
        edl = json.loads(json.dumps(case["edl"]))
        if case.get("ops"):
            # the stale-client path: apply_edit_batch operations
            import edit_batch
            import schemas
            dur_s = float(case["idx"]["video"]["duration"])
            edl = schemas.validate_edl(edl, dur_s).model_dump()
            edl = edit_batch.apply_batch(edl, case["ops"], dur_s,
                                         set(locals_))
            res["edl"] = edl
        with faultinject.inject(*(case.get("faults") or [])):
            dur = renderer.render_edl(
                edl, case["idx"],
                case["src"], out, wd, preview=case.get("preview", True),
                want_wm=case.get("want_wm", False), asset_locals=locals_,
                suppress_outro=case.get("suppress_outro", False))
        res["wall_s"] = round(time.time() - t, 3)
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        res["cpu_s"] = round((ru1.ru_utime - ru0.ru_utime)
                             + (ru1.ru_stime - ru0.ru_stime), 3)
        res["duration"] = dur
        if dur is not None and os.path.exists(out):
            res["path"] = out
            res["frames"] = w0b_probe.frame_md5s(out)
            res["audio"] = w0b_probe.audio_md5(out)
            res["probe"] = w0b_probe.probe(out)
            if case.get("dump_frames"):
                res["dumps"] = w0b_probe.dump_frames(out, case["dump_frames"],
                                                     wd)
            if case.get("png_frames"):
                res["pngs"] = png_frames(out, case["png_frames"], wd)
    except Exception as e:  # noqa: BLE001 — the parent asserts on it
        res["error"] = f"{type(e).__name__}: {str(e)[-800:]}"
    finally:
        fxgraph.stop_collecting(tok)
        renderer.build_filtergraph = real_build
        subject_fx.ASSET_LOCALS.reset(atok)
        patch.undo()
    res["report"] = fxgraph.merge_report(rows)
    if case.get("graph"):
        res["graphs"] = graphs
    return res


def main(spec_path, out_path):
    import renderer
    import schemas
    assert "subject" in schemas.FX_TYPES, \
        "track 1B is not registered (set VALMERA_FX_PREVIEW=1b)"
    if os.environ.get("W1B_LOSSY") != "1":
        renderer._render_media_run = w0b_probe._lossless(
            renderer._render_media_run)
    spec = json.load(open(spec_path))
    workroot = spec["workroot"]
    os.makedirs(workroot, exist_ok=True)
    results = {}
    for case in spec["cases"]:
        results[case["name"]] = run_case(case, workroot)
    with open(out_path, "w") as f:
        json.dump(results, f)


def run(cases, workroot, timeout=3600, env=None):
    """Parent side: run ``cases`` in a child; returns {name: result}."""
    os.makedirs(workroot, exist_ok=True)
    spec = os.path.join(workroot, "spec.json")
    out = os.path.join(workroot, "results.json")
    with open(spec, "w") as f:
        json.dump({"workroot": workroot, "cases": cases}, f)
    e = dict(os.environ)
    e.update(env or {})
    e.setdefault("VALMERA_FX_PREVIEW", "1b")
    e.pop("VALMERA_FX_PROBE", None)
    e["PYTHONPATH"] = os.pathsep.join(
        [WORKER, os.path.dirname(WORKER)]
        + [p for p in e.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, os.path.abspath(__file__), spec, out],
                       cwd=WORKER, env=e, capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"w1b child failed:\n{r.stdout[-3000:]}\n"
                           f"{r.stderr[-3000:]}")
    with open(out) as f:
        return json.load(f)


def yuv(path, W, H):
    """(Y, U, V) int arrays of a raw yuv420p dump."""
    import numpy as np
    a = np.fromfile(path, np.uint8).astype(int)
    y = a[:W * H].reshape(H, W)
    u = a[W * H:W * H + (W // 2) * (H // 2)].reshape(H // 2, W // 2)
    v = a[W * H + (W // 2) * (H // 2):].reshape(H // 2, W // 2)
    return y, u, v


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
