"""Track 4A acceptance (AT-4A-1 .. AT-4A-6): depth, 2.5D and time.

    cd worker && <python with torch> -m benchmarks.refbench.at4a AT-4A-1 [...]

Runs with a Python that has torch (the depth.small sidecar runs here
exactly as on the batch lane: analysis_jobs.run_sidecar_job, a child
process, the pinned weights) and the RVM matte model; sidecars land in a
local store ($VALMERA_W4_STORE) that the renders read like production
reads R2. Renders go through renderer.render_edl on the VFL fixtures.

  AT-4A-1  red point-cloud runner (01@0.958-3.79) on V12 walk-on: temporal
           depth stability, points only where depth and matte are valid
  AT-4A-2  push through type in Z (16@9.2-10.1, opt-in) on V01: the near
           plane defocuses monotonically
  AT-4A-3  depth of field behind the subject (40@~5-14.3) on V11 60p:
           subject Laplacian variance kept >= 90 %
  AT-4A-4  freeze posters with parallax (13@3.44-18.32) on V01: visible
           inpaint <= 2 % of the frame
  AT-4A-5  slow motion (31, 34, 02@8.57) on V13 Starship 4K60: decimated
           to 30p and interpolated back, VMAF >= 85 against the native
           60p frames; an eased ramp lands within 1 frame of its curve
  AT-4A-6  rack focus on a word (V06: Elon -> the boom mic): lands on the
           word's onset +-1 frame

Internal only: references, fixtures, renders, boards never enter git or
product storage. Boards: ~/Documents/Valmera/.valmera/refbench/boards/wave4/4A.
"""

import copy
import json
import os
import subprocess
import sys
import time

import numpy as np

from . import fixtures_dir, out_dir, reference_path

BOARDS = os.environ.get("VALMERA_W4_BOARDS") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4/4A")
STORE = os.environ.get("VALMERA_W4_STORE") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/engines/w4/store")


# ── plumbing: sidecars on the fixture, renders, boards ──────────────────

def _win(vfl, name):
    from . import vfl as vflmod
    return vflmod.window(vfl, name)


def _source(win):
    import media
    import qa_metrics
    import sidecars
    info = qa_metrics.probe(win["path"])
    sha = media.sha256_file(win["path"])
    return sidecars.source_from(sha, win["path"], {
        "duration": info["duration"], "fps": info["fps"],
        "width": info["width"], "height": info["height"]}, 7, []), info


def ensure(kind, ref, ranges):
    """Run the sidecar job for ``kind`` over SOURCE ``ranges`` of ``ref``
    into the local store (the production runner, in a child process)."""
    import analysis_jobs
    import sidecars
    sidecars.use_store(sidecars.LocalStore(STORE))
    sp = sidecars.spec(kind)
    params = sidecars.resolve_params(kind, None)
    spans = sidecars.chunks_for(ref.shots, ref.duration_s, ranges)

    class _Quiet:  # the handle only: this function runs the job itself
        def submit(self, *a, **k):
            return None

        def inflight(self, key):
            return False

        def join(self, timeout=None):
            return True

    prev = sidecars.use_launcher(_Quiet())
    try:
        h = sidecars.ensure(kind, ref, ranges)
    finally:
        sidecars.use_launcher(prev)
    states = sidecars.chunk_status(h)
    todo = [s for s, c in zip(spans, h.chunks)
            if states.get(c.key) != "ready"]
    if not todo:
        return {"cached": True}
    payload = {"kind": kind, "source": {
        "sha256": ref.sha256, "storage_key": ref.storage_key,
        "duration_s": ref.duration_s, "fps": ref.fps, "width": ref.width,
        "height": ref.height, "project_id": 7, "shots": list(ref.shots)},
        "chunks": [{"key": sidecars.sidecar_key(
            kind, ref.sha256, sp["model_id"], sp["model_version"], params,
            s), "t0": s[0], "t1": s[1]} for s in todo],
        "params": params, "model_id": sp["model_id"],
        "model_version": sp["model_version"], "format": sp["format"],
        "attempt": 1, "slot": 0}
    t = time.time()
    res = analysis_jobs.run_sidecar_job(None, {"payload": payload})
    return {"ok": res.get("ok"), "s": round(time.time() - t, 1),
            "why": res.get("why")}


def index_for(win, ref, words=()):
    return {"words": list(words), "silences": [], "sentences": [],
            "shots": [{"start": 0.0,
                       "thumb_key": f"tiles/7/{ref.sha256}/s0.jpg"}],
            "video": {"width": win["width"], "height": win["height"],
                      "fps": win["fps"],
                      "duration": round(win["file_span"][1]
                                        - win["file_span"][0], 3),
                      "sha256": ref.sha256}}


def render(edl, win, index, out, *, preview=False, final_lossless=False):
    import fxgraph
    import renderer
    import sidecars
    from schemas import validate_edl
    sidecars.use_store(sidecars.LocalStore(STORE))
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    dur = float(win["file_span"][1] - win["file_span"][0])
    e = validate_edl(copy.deepcopy(edl), dur).model_dump()
    rows = []
    tok = fxgraph.collect(rows)
    t = time.time()
    try:
        renderer.render_edl(e, index, win["path"], out, work, preview)
    finally:
        fxgraph.stop_collecting(tok)
    return {"wall_s": round(time.time() - t, 2),
            "report": fxgraph.merge_report(rows), "edl": e, "work": work}


def frames(path, size=None, gray=False, idx=None):
    import qa_metrics
    got = qa_metrics.read_frames(path, size=size, gray=gray,
                                 frames=set(idx) if idx else None)
    return dict(zip(*got))


def lap(a):
    import cv2
    return float(cv2.Laplacian(np.asarray(a, np.float32), cv2.CV_32F).var())


def board(case_id, rows, name="board.png"):
    from . import at1e
    d = os.path.join(BOARDS, case_id)
    os.makedirs(d, exist_ok=True)
    return at1e.sheet(rows, os.path.join(d, name))


def ref_still(ref, t, height):
    from . import boards
    rp = reference_path(ref)
    return boards.grab(rp, t, height) if rp else None


def _h(rgb, h):
    from . import at1e
    return at1e._h(rgb, h)


def program_maps(win, ref, edl, t0, t1, fps, W, H, kind="depth.small"):
    """Program-space maps (depth or matte) the compiler would read, for
    measurement."""
    import depth_track
    import sidecars
    from timeline import Timeline
    sidecars.use_store(sidecars.LocalStore(STORE))
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    a, b = tl.out_to_src(t0), tl.out_to_src(t1 - 0.01)
    if kind == "depth.small":
        clip, _st = depth_track.fetch(ref, [(a - 0.2, b + 0.2)],
                                      os.path.join(STORE, "_fetch"))
    else:
        import matte_track
        import qa_metrics
        spans = sidecars.chunks_for(ref.shots, ref.duration_s,
                                    [(a - 0.2, b + 0.2)])
        h = matte_track.read_handle("matte.person", ref, spans)
        ts, ds = [], []
        for f in sidecars.fetch(h, os.path.join(STORE, "_fetch")):
            fpsm = float((f.get("meta") or {}).get("fps") or 30.0)
            idx, fr = qa_metrics.read_frames(f["path"], gray=True)
            for k, g in zip(idx, fr):
                t = f["t0"] + k / fpsm
                if a - 0.2 <= t <= b + 0.2:
                    ts.append(t)
                    ds.append(np.asarray(g, np.float32) / 255.0)
        clip = depth_track.DepthClip(np.asarray(ts), np.stack(ds)) \
            if ts else None
    out = []
    n = int(round((t1 - t0) * fps))
    for i in range(n):
        t = t0 + i / fps
        out.append(depth_track.to_program(clip, edl, tl, t, W, H,
                                          ref.width, ref.height))
    return out


def verdict(case, metrics, extra=None, passed=None):
    od = os.path.join(out_dir(), case)
    os.makedirs(od, exist_ok=True)
    v = {"case": case, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "metrics": metrics, **(extra or {})}
    # an unmeasured metric is a failure, never a silent pass
    v["pass"] = passed if passed is not None else all(
        m.get("pass") is True for m in metrics.values())
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, default=str)
    print(json.dumps({k: v[k] for k in ("case", "metrics", "pass")},
                     indent=1, default=str))
    return v


def _m(value, bar_op, bar, detail=None):
    ok = None
    if value is not None:
        ok = value <= bar if bar_op == "<=" else value >= bar
    return {"value": None if value is None else round(float(value), 4),
            "bar": f"{bar_op}{bar}", "pass": ok, "detail": detail or {}}


# ── AT-4A-1: the point-cloud runner ─────────────────────────────────────

def at_4a_1():
    win = _win("V12", "walkon")
    ref, info = _source(win)
    keep = [16.0, 22.0]
    st = [ensure("depth.small", ref, [tuple(keep)]),
          ensure("matte.person", ref, [tuple(keep)])]
    od = os.path.join(out_dir(), "AT-4A-1")
    os.makedirs(od, exist_ok=True)
    base = {"keep": [keep], "frame": {"ratio": "9:16", "mode": "crop",
                                      "focus_x": 0.52}}
    cloud = dict(base, fx=[{"id": "pc", "type": "depth", "stage": "world",
                            "start": 1.0, "end": 4.0, "kind": "point_cloud",
                            "focus": {"on": "subject"}, "backdrop": "black",
                            "move": {"path": "orbit_left", "amount": 0.35},
                            "device": "depth_point_cloud", "seed": 1}])
    still = copy.deepcopy(cloud)
    still["fx"][0]["move"] = {"path": "orbit_left", "amount": 0.05}
    idx = index_for(win, ref)
    r_ctl = render(base, win, idx, os.path.join(od, "control.mp4"))
    r = render(cloud, win, idx, os.path.join(od, "cloud.mp4"))
    r2 = render(still, win, idx, os.path.join(od, "cloud_still.mp4"))
    import qa_metrics
    pi = qa_metrics.probe(os.path.join(od, "cloud.mp4"))
    W, H, fps = pi["width"], pi["height"], pi["fps"]
    dmaps = program_maps(win, ref, r["edl"], 1.0, 4.0, fps, W, H)
    mmaps = program_maps(win, ref, r["edl"], 1.0, 4.0, fps, W, H,
                         kind="matte.person")
    import depth_track
    subj = [m > 0.5 if m is not None else None for m in mmaps]
    stab = []
    for a, b, m in zip(dmaps, dmaps[1:], subj):
        if a is None or b is None or m is None or m.sum() < 50:
            continue
        stab.append(float(np.abs(b - a)[m].mean()))
    stability = float(np.mean(stab)) if stab else None
    # points outside the (dilated) matte, on the near-still orbit
    import cv2
    fr = frames(os.path.join(od, "cloud_still.mp4"), gray=True)
    f0 = int(round(1.0 * fps))
    outside = []
    for k in range(f0 + 2, f0 + int(3.0 * fps) - 2, 5):
        m = subj[k - f0] if 0 <= k - f0 < len(subj) else None
        if m is None or k not in fr:
            continue
        g = np.asarray(fr[k], np.float32)
        pts = g > 40
        grow = cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        if pts.sum():
            outside.append(float((pts & ~grow).sum() / pts.sum()))
    out_share = float(np.mean(outside)) if outside else None
    rgb = frames(os.path.join(od, "cloud.mp4"),
                 idx=[f0 + 10, f0 + 40, f0 + 70])
    ctl = frames(os.path.join(od, "control.mp4"), idx=[f0 + 40])
    rows = [("AT-4A-1 point-cloud runner (V12 walk-on, 9:16) — ours", [
        (f"t={k / fps:.2f}s", _h(rgb[k], 360)) for k in sorted(rgb)]
        + [("control", _h(ctl[f0 + 40], 360))])]
    rs = ref_still("01", 1.708, 360)
    if rs is not None:
        rows.append(("reference 01@1.708 (internal)", [("ref", rs)]))
    b = board("AT-4A-1", rows)
    rung = {x["id"]: x for x in r["report"]}["pc"]
    return verdict("AT-4A-1", {
        "depth_stability": _m(stability, "<=", 0.06,
                              {"frames": len(stab)}),
        "points_outside_matte": _m(out_share, "<=", 0.05),
    }, {"rung": rung, "sidecars": st, "board": b,
        "wall_s": {"control": r_ctl["wall_s"], "cloud": r["wall_s"]}})


# ── AT-4A-2: push through type in Z ──────────────────────────────────────

def at_4a_2():
    win = _win("V01", "thiel_ai_bigger")
    ref, info = _source(win)
    keep = [8.0, 14.0]
    od = os.path.join(out_dir(), "AT-4A-2")
    os.makedirs(od, exist_ok=True)
    edl = {"keep": [keep], "frame": {"ratio": "9:16", "mode": "crop",
                                     "focus_x": 0.55},
           "fx": [{"id": "pz", "type": "depth", "stage": "world",
                   "start": 1.0, "end": 2.6, "kind": "push_through",
                   "text": "BIGGER", "device": "depth_push_through"}]}
    idx = index_for(win, ref)
    r = render(edl, win, idx, os.path.join(od, "push.mp4"))
    import glob
    import qa_metrics
    clips = glob.glob(os.path.join(r["work"], "w4d_*.mkv"))
    alphas = []
    if clips:
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", clips[0], "-vf",
                            "format=rgba,alphaextract,format=gray", "-f",
                            "rawvideo", "-"], capture_output=True).stdout
        pi = qa_metrics.probe(clips[0])
        w, h = pi["width"], pi["height"]
        n = len(p) // (w * h)
        alphas = [np.frombuffer(p[k * w * h:(k + 1) * w * h], np.uint8)
                  .reshape(h, w) for k in range(n)]
    sharp = [lap(a) for a in alphas]
    # once the word reaches the lens (its sharpness peak) it defocuses
    # monotonically as the camera passes through: a rise of more than 2% of
    # the peak after it is a violation (sub-percent wobble on an almost
    # invisible plane is encoder noise, not a re-focus)
    pk = int(np.argmax(sharp)) if sharp else 0
    peak = sharp[pk] if sharp else 0.0
    tail = sharp[pk:]
    viol = sum(1 for a, b in zip(tail, tail[1:]) if b > a + 0.02 * peak)
    pi = qa_metrics.probe(os.path.join(od, "push.mp4"))
    fps = pi["fps"]
    f0 = int(round(1.0 * fps))
    ks = [f0 + 5, f0 + 18, f0 + 28, f0 + 36]
    rgb = frames(os.path.join(od, "push.mp4"), idx=ks)
    rows = [("AT-4A-2 push through BIGGER in Z (opt-in camera move) — ours",
             [(f"t={k / fps:.2f}s", _h(rgb[k], 360)) for k in sorted(rgb)])]
    rs = ref_still("16", 9.6, 360)
    if rs is not None:
        rows.append(("reference 16@9.6 (internal)", [("ref", rs)]))
    b = board("AT-4A-2", rows)
    return verdict("AT-4A-2", {
        "defocus_monotone_violations": _m(viol, "<=", 0,
                                          {"tail_frames": len(tail),
                                           "sharpness": [round(s, 1)
                                                         for s in sharp]}),
    }, {"board": b, "report": r["report"]})


# ── AT-4A-3: subject sharp under depth of field ─────────────────────────

def at_4a_3():
    win = _win("V11", "karp_stage_60p")
    ref, info = _source(win)
    keep = [4.0, 10.0]
    st = [ensure("depth.small", ref, [tuple(keep)]),
          ensure("matte.person", ref, [tuple(keep)])]
    od = os.path.join(out_dir(), "AT-4A-3")
    os.makedirs(od, exist_ok=True)
    # the 9:16 crop on Karp (the left chair), as an editor frames him
    base = {"keep": [keep], "frame": {"ratio": "9:16", "mode": "crop",
                                      "focus_x": 0.3}}
    dof = dict(base, fx=[{"id": "df", "type": "depth", "stage": "world",
                          "start": 1.0, "end": 4.0, "kind": "dof",
                          "focus": {"on": "subject"}, "blur": 0.8,
                          "device": "depth_dof"}])
    idx = index_for(win, ref)
    rc = render(base, win, idx, os.path.join(od, "control.mp4"))
    rd = render(dof, win, idx, os.path.join(od, "dof.mp4"))
    import cv2
    import qa_metrics
    pi = qa_metrics.probe(os.path.join(od, "dof.mp4"))
    W, H, fps = pi["width"], pi["height"], pi["fps"]
    mmaps = program_maps(win, ref, rd["edl"], 1.0, 4.0, fps, W, H,
                         kind="matte.person")
    f0 = int(round(1.0 * fps))
    ks = list(range(f0 + 5, f0 + int(3 * fps) - 5, 10))
    a = frames(os.path.join(od, "control.mp4"), gray=True, idx=ks)
    b = frames(os.path.join(od, "dof.mp4"), gray=True, idx=ks)
    keep_s, bg_s = [], []
    for k in ks:
        m = mmaps[k - f0] if 0 <= k - f0 < len(mmaps) else None
        if m is None or m.sum() < 200 or k not in a or k not in b:
            continue
        core = cv2.erode((m > 0.5).astype(np.uint8),
                         np.ones((7, 7), np.uint8)) > 0
        bg = cv2.dilate((m > 0.5).astype(np.uint8),
                        np.ones((15, 15), np.uint8)) == 0
        la = cv2.Laplacian(np.asarray(a[k], np.float32), cv2.CV_32F)
        lb = cv2.Laplacian(np.asarray(b[k], np.float32), cv2.CV_32F)
        keep_s.append(float(lb[core].var() / max(1e-6, la[core].var())))
        bg_s.append(float(lb[bg].var() / max(1e-6, la[bg].var())))
    rgb = frames(os.path.join(od, "dof.mp4"), idx=[ks[len(ks) // 2]])
    ctl = frames(os.path.join(od, "control.mp4"), idx=[ks[len(ks) // 2]])
    k = ks[len(ks) // 2]
    rows = [("AT-4A-3 depth of field, subject kept (V11 Karp 60p)", [
        ("control", _h(ctl[k], 400)), ("dof", _h(rgb[k], 400))])]
    rs = ref_still("40", 8.0, 400)
    if rs is not None:
        rows.append(("reference 40@~8 (internal)", [("ref", rs)]))
    bd = board("AT-4A-3", rows)
    return verdict("AT-4A-3", {
        "subject_laplacian_kept": _m(min(keep_s) if keep_s else None, ">=",
                                     0.9, {"per_frame": [round(x, 3)
                                                         for x in keep_s]}),
        "background_laplacian_kept": _m(float(np.mean(bg_s)) if bg_s
                                        else None, "<=", 0.6),
    }, {"board": bd, "sidecars": st,
        "rung": {x["id"]: x for x in rd["report"]}.get("df"),
        "wall_s": {"control": rc["wall_s"], "dof": rd["wall_s"]}})


# ── AT-4A-4: freeze posters with parallax ───────────────────────────────

def at_4a_4():
    import depth_fx
    import depth_track
    win = _win("V01", "thiel_3334")
    ref, info = _source(win)
    stills = [6.0, 22.0, 41.0]
    st = [ensure("depth.small", ref, [(t - 0.3, t + 0.3)]) for t in stills]
    od = os.path.join(out_dir(), "AT-4A-4")
    os.makedirs(od, exist_ok=True)
    W, H = 540, 960
    moves = [{"path": "push_in", "amount": 0.4, "ease": "in_out"},
             {"path": "drift_left", "amount": 0.35, "ease": "in_out"},
             {"path": "orbit_left", "amount": 0.4, "ease": "in_out"}]
    vis, rows = [], []
    import sidecars
    sidecars.use_store(sidecars.LocalStore(STORE))
    for t, mv in zip(stills, moves):
        clip, _s = depth_track.fetch(ref, [(t - 0.3, t + 0.3)],
                                     os.path.join(STORE, "_fetch"))
        img = depth_fx._decode_frame(win["path"], t)
        d = clip.at(t) if clip is not None else None
        if img is None or d is None:
            vis.append(None)
            continue
        import cv2
        d = cv2.resize(d, (img.shape[1], img.shape[0]))
        pic = depth_fx.cover_crop(img, W, H)
        dep = depth_fx.cover_crop(np.dstack([d] * 3).astype(np.float32), W,
                                  H)[..., 0]
        layers, hole = depth_fx.build_layers(pic, np.clip(dep, 0, 1))
        n = 60
        v = depth_fx.visible_inpaint(layers, hole, W, H, mv, n)
        vis.append(v)
        out = os.path.join(od, f"poster_{int(t)}.mkv")
        w = depth_fx.ClipWriter(out, W, H, 25.0, "rgb24")
        depth_fx.render_parallax(layers, W, H, n, mv, w)
        w.close()
        fr = frames(out, idx=[0, 30, 59])
        rows.append((f"V01 frame {t:g}s, {mv['path']} — visible inpaint "
                     f"{100 * v:.2f}%", [(f"f{k}", _h(fr[k], 320))
                                         for k in sorted(fr)]))
    rs = ref_still("13", 5.0, 320)
    if rs is not None:
        rows.append(("reference 13@5.0 (internal)", [("ref", rs)]))
    bd = board("AT-4A-4", rows)
    good = [v for v in vis if v is not None]
    return verdict("AT-4A-4", {
        "visible_inpaint_max": _m(max(good) if good else None, "<=", 0.02,
                                  {"per_still": vis}),
    }, {"board": bd, "sidecars": st})


# ── AT-4A-5: interpolation and the eased ramp ───────────────────────────

def _interp_vmaf(src, ss, dur, od, tag, step=2):
    """(mean VMAF of the synthesised frames, min, wall_s): native 60p vs the
    time device's chain run on the same shot slowed 0.5x (every other
    frame, each shown twice: what a 0.5x speed span leaves in a 60p
    program) — mpdecimate drops the repeats, minterpolate synthesises the
    in-betweens."""
    native = os.path.join(od, f"native60_{tag}.mkv")
    dup = os.path.join(od, f"dup_{tag}.mkv")
    interp = os.path.join(od, f"interp60_{tag}.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(ss), "-t",
                    str(dur), "-i", src, "-vf",
                    "scale=1280:720:flags=area,fps=60000/1001", "-an",
                    "-c:v", "ffv1", native], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", native, "-vf",
                    f"select='not(mod(n,{step}))',"
                    f"setpts=N*{step}/(60000/1001)/TB,fps=60000/1001",
                    "-c:v", "ffv1", dup], check=True)
    import qa_metrics
    import timewarp
    nd = int(qa_metrics.probe(dup).get("frames") or 0) or int(
        round(dur * 59.94))
    rate = 60000 / 1001.0
    # the product chain (timewarp.interp_chain): the repeat pattern a 0.5x
    # span leaves is exact — every even frame is a new source frame, due
    # at its own program time
    uniq = list(range(0, nd, step))
    times = [k / rate for k in uniq]
    chain = timewarp.interp_chain(uniq, times, rate, nd, "setpts=PTS")
    t = time.time()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dup, "-vf",
                    "setpts=N/(60000/1001)/TB," + chain, "-c:v", "ffv1",
                    interp], check=True)
    wall = time.time() - t
    log = os.path.join(od, f"vmaf_{tag}.json")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", interp, "-i",
                    native, "-lavfi",
                    "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS"
                    f"[b];[a][b]libvmaf=log_fmt=json:log_path={log}:"
                    "n_threads=4:shortest=1", "-f", "null", "-"],
                   check=True)
    with open(log) as f:
        vm = json.load(f)
    fr = [x["metrics"]["vmaf"] for x in vm["frames"]]
    last = (len(fr) - 1) // step * step
    synth = [v for i, v in enumerate(fr[:last]) if i % step]
    return float(np.mean(synth)), float(np.min(synth)), wall, len(fr)


def at_4a_5():
    win = _win("V13", "starship_launch_4k60")
    od = os.path.join(out_dir(), "AT-4A-5")
    os.makedirs(od, exist_ok=True)
    src = win["path"]
    # two shots without a cut (refsig cuts at 2.62/5.19 and 6.96/9.96):
    # the tower rising, and the aerial smoke ("slow-motion dust")
    shots = [(2.75, 2.3, "tower"), (7.1, 2.75, "smoke")]
    per = {}
    for ss, dur, tag in shots:
        per[tag] = _interp_vmaf(src, ss, dur, od, tag)
    per4 = {}
    for ss, dur, tag in shots:
        per4[tag] = _interp_vmaf(src, ss, dur, od, tag + "_x4", step=4)
    synth_mean = float(np.mean([v[0] for v in per.values()]))
    interp_s = sum(v[2] for v in per.values())
    synth = [v[0] for v in per.values()]
    pooled = {k: {"mean": round(v[0], 2), "min": round(v[1], 2),
                  "frames": v[3]} for k, v in per.items()}
    frames_v = [0]
    # the eased ramp: speed spans from add_time_effect's builder, rendered
    from tools_ext import depth_tools
    from timeline import Timeline
    keep = [[2.0, 8.0]]
    edl = {"keep": keep}
    spans = depth_tools._speed_ramp_ops(edl, 1.0, 4.0, 0.4)
    edl["speed"] = spans
    tl = Timeline(keep, [], spans)
    idx = {"words": [], "video": {"width": win["width"],
                                  "height": win["height"],
                                  "fps": win["fps"],
                                  "duration": round(win["file_span"][1]
                                                    - win["file_span"][0],
                                                    3)}}
    out = os.path.join(od, "ramp.mp4")
    rr = render(dict(edl, frame={"ratio": "16:9"}), win, idx, out)
    import qa_metrics
    pi = qa_metrics.probe(out)
    fps = pi["fps"]
    errs = []
    srcf = frames(src, size=(160, 90), gray=True,
                  idx=range(int(1.5 * 59.94), int(9.0 * 59.94)))
    keys = sorted(srcf)
    sarr = np.stack([np.asarray(srcf[k], np.float32) for k in keys])
    got = frames(out, size=(160, 90), gray=True,
                 idx=range(int(0.5 * fps), int(5.0 * fps), 9))
    for k, g in sorted(got.items()):
        t_out = k / fps
        want = tl.out_to_src(t_out)
        if want is None:
            continue
        d = np.abs(sarr - np.asarray(g, np.float32)).mean(axis=(1, 2))
        best = keys[int(np.argmin(d))] / 59.94
        errs.append(abs(best - want) * fps)       # in program frames
    return verdict("AT-4A-5", {
        "vmaf_synth_mean": _m(synth_mean, ">=", 85.0, {"shots": pooled}),
        "vmaf_synth_mean_x4": _m(float(np.mean([v[0] for v in
                                               per4.values()])), ">=", 85.0,
                                 {"shots": {k: {"mean": round(v[0], 2),
                                                "min": round(v[1], 2)}
                                            for k, v in per4.items()}}),
        "ramp_curve_err_f": _m(max(errs) if errs else None, "<=", 1.0,
                               {"samples": [round(e, 2) for e in errs]}),
    }, {"interp_wall_s": round(interp_s, 1), "ramp": spans,
        "ramp_render_s": rr["wall_s"]})


# ── AT-4A-6: rack focus on a word ───────────────────────────────────────

def _asr(path, t0, t1):
    from faster_whisper import WhisperModel
    m = WhisperModel("small.en", device="cpu", compute_type="int8")
    # decoded here (16 kHz mono float): the bundled PyAV's open() does not
    # take faster-whisper's metadata_errors argument
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.float32)
    seg, _info = m.transcribe(audio, word_timestamps=True,
                              clip_timestamps=[t0, t1])
    out = []
    for s in seg:
        for w in s.words or []:
            out.append({"w": w.word.strip(), "t0": round(w.start, 3),
                        "t1": round(w.end, 3), "p": round(w.probability,
                                                          3)})
    return out


def at_4a_6():
    win = _win("V06", "boom_mic")
    ref, info = _source(win)
    keep = [10.0, 18.0]
    st = [ensure("depth.small", ref, [tuple(keep)]),
          ensure("matte.person", ref, [tuple(keep)])]
    words = _asr(win["path"], keep[0], keep[1])
    cands = [w for w in words if keep[0] + 2.0 <= w["t0"] <= keep[1] - 2.5
             and len(w["w"].strip(".,!?")) >= 4]
    w = cands[len(cands) // 2]
    t_word = round(w["t0"] - keep[0], 2)
    od = os.path.join(out_dir(), "AT-4A-6")
    os.makedirs(od, exist_ok=True)
    base = {"keep": [keep], "frame": {"ratio": "9:16", "mode": "crop",
                                      "focus_x": 0.5}}
    rack = dict(base, fx=[{"id": "rk", "type": "depth", "stage": "world",
                           "start": round(t_word - 1.2, 2),
                           "end": round(t_word + 1.2, 2),
                           "kind": "rack_focus",
                           "focus_from": {"on": "subject"},
                           "focus": {"on": "near"}, "rack_at": t_word,
                           "rack_f": 12, "blur": 0.9,
                           "device": "depth_rack_focus"}])
    idx = index_for(win, ref)
    rc = render(base, win, idx, os.path.join(od, "control.mp4"))
    rk = render(rack, win, idx, os.path.join(od, "rack.mp4"))
    import cv2
    import qa_metrics
    pi = qa_metrics.probe(os.path.join(od, "rack.mp4"))
    W, H, fps = pi["width"], pi["height"], pi["fps"]
    s0 = t_word - 1.2
    dmaps = program_maps(win, ref, rk["edl"], s0, t_word + 1.2, fps, W, H)
    f0 = int(round(s0 * fps))
    n = len(dmaps)
    a = frames(os.path.join(od, "control.mp4"), gray=True,
               idx=range(f0, f0 + n))
    b = frames(os.path.join(od, "rack.mp4"), gray=True,
               idx=range(f0, f0 + n))
    ratio = []
    for i in range(n):
        d = dmaps[i]
        if d is None or (f0 + i) not in a or (f0 + i) not in b:
            ratio.append(None)
            continue
        near = d >= np.percentile(d, 95)
        far = ~cv2.dilate(near.astype(np.uint8), np.ones((15, 15),
                                                         np.uint8)).astype(
            bool)
        la = cv2.Laplacian(np.asarray(a[f0 + i], np.float32), cv2.CV_32F)
        lb = cv2.Laplacian(np.asarray(b[f0 + i], np.float32), cv2.CV_32F)
        ratio.append((float(lb[near].var() / max(1e-6, la[near].var())),
                      float(lb[far].var() / max(1e-6, la[far].var()))))
    # landing: the pull is complete when the rest of the frame has made
    # 90% of its total change in sharpness (start -> end of the window);
    # a fixed threshold would catch the eased travel half way
    far = [r[1] if r else None for r in ratio]
    vals = [v for v in far if v is not None]
    land = None
    if len(vals) >= 10:
        v0 = float(np.median(vals[:5]))
        v1 = float(np.median(vals[-5:]))
        for i, v in enumerate(far):
            if v is not None and v <= v1 + 0.1 * (v0 - v1) and \
                    all(x is None or x <= v1 + 0.1 * (v0 - v1)
                        for x in far[i:i + 3]):
                land = f0 + i
                break
    want = int(round(t_word * fps))
    err = None if land is None else abs(land - want)
    ks = [f0 + 3, want - 6, want, want + 10]
    rgb = frames(os.path.join(od, "rack.mp4"), idx=ks)
    rows = [(f"AT-4A-6 rack focus Elon -> boom mic, lands on "
             f"'{w['w']}' at {t_word:.2f}s", [
                 (f"t={k / fps:.2f}s", _h(rgb[k], 360)) for k in sorted(rgb)
             ])]
    bd = board("AT-4A-6", rows)
    return verdict("AT-4A-6", {
        "land_err_f": _m(err, "<=", 1.0, {"land_f": land, "word_f": want,
                                          "ratios": ratio}),
    }, {"board": bd, "word": w, "sidecars": st,
        "rung": {x["id"]: x for x in rk["report"]}.get("rk"),
        "wall_s": {"control": rc["wall_s"], "rack": rk["wall_s"]}})


CASES = {"AT-4A-1": at_4a_1, "AT-4A-2": at_4a_2, "AT-4A-3": at_4a_3,
         "AT-4A-4": at_4a_4, "AT-4A-5": at_4a_5, "AT-4A-6": at_4a_6}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = {}
    for c in argv or sorted(CASES):
        try:
            out[c] = CASES[c]()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            out[c] = {"error": f"{type(e).__name__}: {e}"}
    return out


if __name__ == "__main__":
    main()
