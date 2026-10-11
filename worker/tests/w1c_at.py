"""Track 1C acceptance harness (local footage; ROADMAP Wave 1 §1C ATs).

Runs in a CHILD process (``FINAL_CRF=0``: lossless finals, so decoded frames
are the filtergraph's own) and renders every 1C case of
``benchmarks/refbench/cases/1C`` from its YAML — the very apply_edit_batch
operations a stale MCP client would send — then measures it:

  AT-1C-1  footage inside the glyphs vs a direct crop of the fill (PSNR),
           the field outside, the I window onto the program, eased growth
  AT-1C-2  difference word = |plate - ink| (luma), |Lc| >= 45 share
  AT-1C-3  ghost outline = hidden glyph ∧ matte (within 1 px), its width
           and opacity (through motion_layer.composite_behind, the call
           1A's renderer makes — patched in here until 1A merges)
  AT-1C-4  glass: nothing changes outside the regions; determinism
  AT-1C-5  the next shot inside the letters on its own clock (PSNR vs the
           asset), the window full on the cut, duration and audio exact
  AT-1C-6  inside the quad = the documented transform, outside ΔE2000
  AT-1C-7  no pixel darker than the picture under screen / add light
  AT-1C-perf  CPU and wall ratio of four composites vs the plain graphics
           (a separate child with FINAL_CRF=18)

    FINAL_CRF=0 python w1c_at.py <out.json> [case ...]

Asset fills and inserts use the fixture files named in a case's
``edl.assets`` (local only; never product storage). With
``W1C_BOARDS=<dir>`` each case also writes a 2-up board against its
reference moment (internal only).
"""

import contextlib
import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import numpy as np                                             # noqa: E402

CASES = os.path.join(WORKER, "benchmarks", "refbench", "cases", "1C")


# ── plumbing ─────────────────────────────────────────────────────────────

def load_case(cid):
    from benchmarks.refbench import spec
    return spec.load(os.path.join(CASES, f"{cid}.yaml"))


def probe(path):
    s = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets",
         "-of", "json", path], capture_output=True, check=True).stdout)
    s = s["streams"][0]
    a, b = s["r_frame_rate"].split("/")
    return {"width": s["width"], "height": s["height"],
            "fps": float(a) / float(b),
            "frames": int(s.get("nb_read_packets") or 0)}


def frames(path, idx, fmt="rgb24"):
    pr = probe(path)
    W, H = pr["width"], pr["height"]
    ch = {"rgb24": 3, "gray": 1}[fmt]
    idx = sorted(set(int(k) for k in idx))
    sel = "+".join(f"eq(n\\,{k})" for k in idx)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                          f"select='{sel}',format={fmt}", "-fps_mode",
                          "passthrough", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    n = W * H * ch
    arr = np.frombuffer(raw, np.uint8)
    out = {}
    for j, k in enumerate(idx):
        a = arr[j * n:(j + 1) * n]
        out[k] = a.reshape(H, W, ch) if ch > 1 else a.reshape(H, W)
    return out


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


def erode(b, r=1):
    b = b.copy()
    for _ in range(r):
        b = b & np.roll(b, 1, 0) & np.roll(b, -1, 0) & np.roll(b, 1, 1) & \
            np.roll(b, -1, 1)
    return b


def dilate(b, r=1):
    b = b.copy()
    for _ in range(r):
        b = b | np.roll(b, 1, 0) | np.roll(b, -1, 0) | np.roll(b, 1, 1) | \
            np.roll(b, -1, 1)
    return b


def first_frame(s, fps):
    return int(math.ceil(round(float(s), 4) * round(float(fps), 3) - 1e-9))


def clip_rgba(rec, k):
    """Frame k (by index) of a placed motion clip as w x h RGBA."""
    w, h = rec["w"], rec["h"]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", rec["path"], "-vf",
         f"select=eq(n\\,{int(k)}),format=rgba,crop=min(iw\\,{w}):"
         f"min(ih\\,{h}):0:0,pad={w}:{h}:0:0:color=black@0",
         "-fps_mode", "passthrough", "-frames:v", "1", "-f", "rawvideo",
         "-"], capture_output=True, check=True).stdout
    if len(raw) < w * h * 4:
        return None
    return np.frombuffer(raw[:w * h * 4], np.uint8).reshape(h, w, 4)


def clip_alpha(rec, k, W, H):
    """Alpha (H x W) of a placed motion clip at its frame k."""
    a = clip_rgba(rec, k)
    full = np.zeros((H, W), np.uint8)
    if a is not None:
        full[rec["y"]:rec["y"] + rec["h"], rec["x"]:rec["x"] + rec["w"]] = \
            a[..., 3]
    return full


def psnr(a, b, mask):
    mse = float(((a.astype(float) - b.astype(float)) ** 2)[mask].mean())
    return 99.0 if mse < 1e-9 else round(10 * math.log10(255 ** 2 / mse), 2)


# ── case -> EDL -> renders ───────────────────────────────────────────────

class Run:
    def __init__(self, cid, root):
        from benchmarks.refbench import engine, vfl as vflmod
        self.cid = cid
        self.case = load_case(cid)
        self.dir = os.path.join(root, cid)
        os.makedirs(self.dir, exist_ok=True)
        spec_edl = self.case.get("edl") or {}
        self.locals = {}
        for key, ref in (spec_edl.get("assets") or {}).items():
            self.locals[key] = vflmod.fixture_path(ref["vfl"], ref["window"])
        base = spec_edl.get("base", "vfl_default")
        if base.startswith("catalog:"):
            # a house stand-in (fixtures/catalog.json), on its file clock
            self.src = _catalog_path(base.split(":", 1)[1])
            self.win = None
            edl = {"keep": spec_edl["keep_file"],
                   "frame": {"ratio": self.case["aspect"], "mode": "crop"}}
            self.duration = probe_duration(self.src)
            pr = probe(self.src)
            self.index = {"words": [], "video": {
                "width": pr["width"], "height": pr["height"],
                "duration": self.duration, "fps": pr["fps"]}}
        else:
            self.win = engine.window_for_case(self.case)
            self.src = self.win["path"]
            edl = engine.base_edl(self.case, self.win)
            self.duration = float(self.win["file_span"][1]
                                  - self.win["file_span"][0])
            self.index = engine.index_for(self.win)
        edl["captions"] = None
        if spec_edl.get("inserts"):
            edl["inserts"] = copy.deepcopy(spec_edl["inserts"])
        from schemas import validate_edl
        self.base = validate_edl(edl, self.duration).model_dump()
        self.ops = [op for op in spec_edl.get("ops") or []
                    if "harness" not in op]
        self.harness = [op for op in spec_edl.get("ops") or []
                        if "harness" in op]

    def edl(self, ops=None, mutate=None):
        import edit_batch
        keys = {k: {"storage_key": k, "duration_s": 600.0}
                for k in self.locals}
        ops = copy.deepcopy(self.ops if ops is None else ops)
        if mutate:
            ops = [mutate(op) for op in ops]
            ops = [op for op in ops if op]
        e = edit_batch.apply_batch(self.base, ops, self.duration, keys) \
            if ops else copy.deepcopy(self.base)
        return e

    def render(self, edl, name, locals_=None):
        import renderer
        from benchmarks.refbench import ink
        wd = os.path.join(self.dir, "work_" + name)
        os.makedirs(wd, exist_ok=True)
        out = os.path.join(self.dir, name + ".mp4")
        placed = {}
        c0 = os.times()
        t0 = time.time()
        with ink.capture_overlays() as rec:
            renderer.render_edl(copy.deepcopy(edl), self.index, self.src, out,
                                wd, False, want_wm=False,
                                asset_locals=dict(self.locals,
                                                  **(locals_ or {})),
                                suppress_outro=True)
        placed.update(rec)
        c1 = os.times()
        cpu = sum(c1[:4]) - sum(c0[:4])
        pr = probe(out)
        for r in placed.values():
            r["fps"] = pr["fps"]
        return {"path": out, "wall": round(time.time() - t0, 2),
                "cpu": round(cpu, 2), "placements": placed, **pr}


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def _catalog_path(item_id):
    from benchmarks.refbench import fixtures_dir
    with open(os.path.join(fixtures_dir(), "catalog.json")) as f:
        cat = json.load(f)
    for it in cat.get("items") or []:
        if it.get("id") == item_id:
            return os.path.join(fixtures_dir(), it["primary"]["path"])
    raise KeyError(item_id)


def _op(run, item_id):
    return next(op for op in run.ops if op["id"] == item_id)


def _without(key):
    def m(op):
        v = op.get("value")
        if isinstance(v, dict) and key in v:
            op = copy.deepcopy(op)
            op["value"].pop(key)
        return op
    return m


def _zero_opacity(op):
    """The same graphics drawn transparent: the reference for "outside,
    untouched" (every motion graphic puts the program in an alpha pixel
    format group, which a render with no graphic never is)."""
    op = copy.deepcopy(op)
    if op["layer"] == "motion":
        op["value"]["composite"] = {"opacity": 0.0}
    elif op["layer"] == "fx":
        return None
    return op


# ── the cases ────────────────────────────────────────────────────────────

def at_1c_1(run):
    r = run.render(run.edl(), "case")
    c = run.render(run.edl(mutate=_zero_opacity), "zero")
    W, H, fps = r["width"], r["height"], r["fps"]
    rec = r["placements"]["tw1"]
    tw1 = _op(run, "tw1")["value"]
    off = float(tw1["composite"]["fill"]["offset_s"])
    fill = run.locals[tw1["composite"]["fill"]["asset_key"]]
    direct = os.path.join(run.dir, "direct_fill.mp4")
    ar = W / H
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{off:.3f}",
                    "-t", "3", "-i", fill, "-vf",
                    f"setpts=PTS-STARTPTS,fps={fps:.3f},crop='min(iw,ih*"
                    f"{ar:.6f})':'min(ih,iw/{ar:.6f})',scale={W}:{H},"
                    "setsar=1", "-c:v", "libx264", "-qp", "0", "-pix_fmt",
                    "yuv420p", direct], check=True, capture_output=True)
    f0 = first_frame(rec["start"], fps)
    out = {"inside_psnr_db": [], "outside_max": [], "window": []}
    for k in (f0 + 6, f0 + 22, f0 + 37, f0 + 47):
        a = clip_alpha(rec, k - f0, W, H)
        core = erode(a == 255, 2)
        R = frames(r["path"], [k], "gray")[k]
        X = frames(direct, [k - f0], "gray")[k - f0]
        out["inside_psnr_db"].append(psnr(R, X, core))
        far = ~dilate(a > 0, 2)
        out["outside_max"].append(int(R[far].max()))
    rec2 = r["placements"]["tw2"]
    g0, g1 = first_frame(rec2["start"], fps), first_frame(rec2["end"], fps)
    areas = [float((clip_alpha(rec2, k - g0, W, H) > 127).mean())
             for k in range(g0, g1)]
    for k in (g0 + 5, g0 + 25):
        a = clip_alpha(rec2, k - g0, W, H)
        R = frames(r["path"], [k], "gray")[k].astype(int)
        C = frames(c["path"], [k], "gray")[k].astype(int)
        inside, outside = erode(a == 255, 3), ~dilate(a > 0, 3)
        out["window"].append({
            "frame": k, "inside_mean_vs_program": round(float(
                np.abs(R - C)[inside].mean()), 3),
            "outside_max": int(R[outside].max())})
    t_open = float(_op(run, "tw2")["value"]["params"]["open_at"]) * \
        (rec2["end"] - rec2["start"]) + rec2["start"]
    ko = first_frame(t_open, fps) - g0
    s = np.sqrt(np.maximum(np.array(areas[ko:]), 1e-9))
    full = int(np.argmax(s >= 0.999)) if (s >= 0.999).any() else len(s)
    seg = np.log(s[:max(2, full)])
    prog = (seg - seg[0]) / max(1e-9, seg[-1] - seg[0])
    n = len(prog)
    out["eased"] = {"frames": n,
                    "p15": round(float(prog[int(0.15 * (n - 1))]), 3),
                    "p85": round(float(prog[int(0.85 * (n - 1))]), 3),
                    "monotone": bool(np.all(np.diff(s[:max(2, full)])
                                            >= -1e-4))}
    out["renders"] = {"case": r["path"], "wall_s": r["wall"]}
    return out


def at_1c_2(run):
    import qa_metrics
    r = run.render(run.edl(), "case")
    z = run.render(run.edl(mutate=_zero_opacity), "zero")
    W, H, fps = r["width"], r["height"], r["fps"]
    rec = r["placements"]["w1"]
    f0 = first_frame(rec["start"], fps)
    res = []
    ap = np.vectorize(lambda t, b: abs(qa_metrics.apca(t, b)))
    for k in (f0 + 10, f0 + 40, f0 + 70):
        core = erode(clip_alpha(rec, k - f0, W, H) == 255, 2)
        R = frames(r["path"], [k])[k].astype(float)
        Z = frames(z["path"], [k])[k].astype(float)
        # the clip's own ink, pixel by pixel (the template shades its type)
        cl = clip_rgba(rec, k - f0)[..., :3]
        ink = np.zeros((H, W, 3), float)
        ink[rec["y"]:rec["y"] + rec["h"], rec["x"]:rec["x"] + rec["w"]] = cl
        want = np.abs(Z - ink)
        wy = want @ np.array([0.299, 0.587, 0.114])
        Ry = R @ np.array([0.299, 0.587, 0.114])
        yerr = np.abs(Ry - wy)[core]
        lt = qa_metrics.luminance(R.astype(np.uint8))[core]
        lb = qa_metrics.luminance(Z.astype(np.uint8))[core]
        lc = ap(lt[::3], lb[::3])
        res.append({"frame": k, "glyph_px": int(core.sum()),
                    "luma_mae": round(float(yerr.mean()), 2),
                    "luma_p50": round(float(np.median(yerr)), 2),
                    "lc45_share": round(float((lc >= 45).mean()), 4),
                    "lc_median": round(float(np.median(lc)), 1)})
    return {"frames": res, "renders": {"case": r["path"],
                                       "wall_s": r["wall"]}}


@contextlib.contextmanager
def composite_behind_switch():
    """1A's seam (INTERFACES_W1.md §1.6.2), simulated: the renderer's
    inline behind-subject composite rewritten into exactly what
    motion_layer.composite_behind emits for a composite with a ghost."""
    import compositor
    import motion_layer
    import renderer
    real_build = renderer.build_filtergraph
    real_overlay = motion_layer.overlay_clip
    seen = {}

    def overlay(parts, vlabel, idx, item, clip, fps, tag, **kw):
        if tag.startswith("mgs"):
            seen[int(tag[3:])] = (item, clip, fps)
        return real_overlay(parts, vlabel, idx, item, clip, fps, tag, **kw)

    def build(*a, **kw):
        seen.clear()
        parts = real_build(*a, **kw).split(";")
        for j, (item, clip, fps) in seen.items():
            side = getattr(clip, "composite", None)
            if not (side and side.stroke and side.ghost):
                continue
            i = parts.index(f"[bhf{j}][bhm{j}]alphamerge[bhfa{j}]")
            parts[i:i + 1] = [f"[bhm{j}]split[bhm{j}s][bhm{j}g]",
                              f"[bhf{j}][bhm{j}s]alphamerge[bhfa{j}]"]
            k = next(n for n, p in enumerate(parts)
                     if p.endswith(f"[vbh{j}]"))
            parts[k] = parts[k][:-len(f"[vbh{j}]")] + f"[vbh{j}pre]"
            extra = []
            out = compositor.emit_ghost(extra, f"vbh{j}pre", f"bhm{j}g",
                                        item, clip, fps, j)
            extra.append(f"[{out}]null[vbh{j}]")
            parts[k + 1:k + 1] = extra
        return ";".join(parts)
    renderer.build_filtergraph = build
    motion_layer.overlay_clip = overlay
    try:
        yield seen
    finally:
        renderer.build_filtergraph = real_build
        motion_layer.overlay_clip = real_overlay


def at_1c_3(run):
    import cv2
    from benchmarks.refbench import engine
    edl_g = run.edl()
    os.makedirs(os.path.join(run.dir, "work_matte"), exist_ok=True)
    note = engine.behind_matte(edl_g, "motion", "hero", run.win,
                               os.path.join(run.dir, "work_matte"),
                               run.locals)
    edl_n = copy.deepcopy(edl_g)
    for m in edl_n["motion"]:
        if m["id"] == "hero":
            m["composite"] = dict(m["composite"], ghost="off")
    from schemas import validate_edl
    edl_g = validate_edl(edl_g, run.duration).model_dump()
    edl_n = validate_edl(edl_n, run.duration).model_dump()
    with composite_behind_switch() as seen:
        r = run.render(edl_g, "case")
        ghosted = sorted(seen)
    n = run.render(edl_n, "noghost")
    W, H, fps = r["width"], r["height"], r["fps"]
    it = next(m for m in edl_g["motion"] if m["id"] == "hero")
    g = it["composite"]["ghost"]
    # the stroke clip the compositor rendered, from its own job
    import motion_engine
    import motion_templates
    piece = {k: v for k, v in it.items() if k != "composite"}
    css = float(g["width_px"]) * min(W, H) / float(W)
    sj = motion_templates.build_job(dict(piece, params=dict(
        piece["params"], _paint={"mode": "stroke", "width": round(css, 3)})),
        W, H, fps)
    sc = motion_engine.render_jobs([sj], os.path.join(run.dir, "work_s"))[0]
    srec = {"path": sc.path, "x": sc.x, "y": sc.y, "w": sc.w, "h": sc.h,
            "fps": fps}
    f0 = first_frame(it["start"], fps)
    out = []
    for k in (f0 + 25, f0 + 50, f0 + 75):
        R = frames(r["path"], [k])[k].astype(float)
        N = frames(n["path"], [k])[k].astype(float)
        d = np.abs(R - N).max(-1)
        stroke = d > 12
        sa = clip_alpha(srec, k - f0, W, H)
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{k / fps - it['start']:.3f}",
             "-i", note["mask"], "-frames:v", "1", "-vf",
             f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True).stdout
        subj = np.frombuffer(raw, np.uint8).reshape(H, W) >= 128
        expect = (sa >= 64) & subj
        expect_core = (sa >= 200) & erode(subj, 3)
        prec = float((stroke & dilate(expect, 1)).sum()) / max(1,
                                                             stroke.sum())
        rec = float((expect_core & dilate(stroke, 1)).sum()) / max(
            1, expect_core.sum())
        band = (d > 3).astype(np.uint8)
        cs, _ = cv2.findContours(band, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        length = sum(cv2.arcLength(c, True) for c in cs) / 2.0
        peak = np.percentile(d[stroke], 75) if stroke.any() else 1.0
        width = float(np.clip(d / max(peak, 1e-6), 0, 1)[band > 0].sum()) \
            / max(1.0, length)
        ink = np.array([242, 240, 234], float)
        core = (sa >= 250) & erode(subj, 3)
        op = ((R - N) / np.maximum(1, ink - N))[core].mean(-1)
        out.append({"frame": k, "precision_1px": round(prec, 4),
                    "recall_1px": round(rec, 4), "width_px": round(width, 2),
                    "opacity": round(float(np.median(op)), 3)})
    return {"frames": out, "ghosted_items": ghosted,
            "matte": {k: v for k, v in note["stats"].items()
                      if k in ("engine", "coverage", "frames")},
            "renders": {"case": r["path"], "noghost": n["path"]}}


def at_1c_4(run):
    r = run.render(run.edl(), "case")
    r2 = run.render(run.edl(), "case2")
    nofx = run.render(run.edl([op for op in run.ops if op["layer"] != "fx"]),
                      "nofx")
    W, H, fps = r["width"], r["height"], r["fps"]
    glass = _op(run, "glass")["value"]
    x0, y0, x1, y1 = glass["region"]["box"]
    feather = float(glass["region"].get("feather_px") or 0) * 2 * H / 1080
    inside = np.zeros((H, W), bool)
    inside[max(0, int(y0 * H - feather - 2)):int(y1 * H + feather + 2),
           max(0, int(x0 * W - feather - 2)):int(x1 * W + feather + 2)] = True
    gk = [first_frame(glass["start"], fps) + 5,
          first_frame(glass["end"], fps) - 5]
    out = {"outside_panel_max": [], "inside_panel_mean": [],
           "outside_glyph_max": []}
    for k in gk:
        R = frames(r["path"], [k], "gray")[k].astype(int)
        B = frames(nofx["path"], [k], "gray")[k].astype(int)
        out["outside_panel_max"].append(int(np.abs(R - B)[~inside].max()))
        out["inside_panel_mean"].append(round(float(
            np.abs(R - B)[inside].mean()), 2))
    rec = r["placements"]["pw"]
    w0 = first_frame(rec["start"], fps)
    for k in (w0 + 10, w0 + 40):
        a = clip_alpha(rec, k - w0, W, H)
        R = frames(r["path"], [k], "gray")[k].astype(int)
        B = frames(nofx["path"], [k], "gray")[k].astype(int)
        out["outside_glyph_max"].append(int(np.abs(R - B)[~dilate(
            a > 0, 2)].max()))
    m1, m2 = frame_md5s(r["path"]), frame_md5s(r2["path"])
    out["determinism_frames_differing"] = sum(a != b for a, b in
                                             zip(m1, m2))
    out["renders"] = {"case": r["path"], "wall_s": r["wall"]}
    return out


def at_1c_5(run):
    r = run.render(run.edl(), "case")
    c = run.render(run.edl([]), "control")
    # the reveal alone (the glyph mono effect left out) for the fill PSNR
    rv = run.render(run.edl([op for op in run.ops if op["layer"] != "fx"]),
                    "reveal")
    W, H, fps = r["width"], r["height"], r["fps"]
    out = {"frames": [r["frames"], c["frames"]],
           "audio_identical": audio_md5(r["path"]) == audio_md5(c["path"])}
    rec = r["placements"]["tw"]
    f0, f1 = first_frame(rec["start"], fps), first_frame(rec["end"], fps)
    ins = run.case["edl"]["inserts"][0]
    asset = run.locals[ins["asset_key"]]
    # the insert's clip second shown at program frame k: clip0 + (t - cut)
    cut = float(ins["at_output_s"])
    ar = W / H
    ref = os.path.join(run.dir, "direct_next.mp4")
    lead = cut - rec["start"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss",
                    f"{float(ins['source_start_s']) - lead:.3f}", "-t",
                    f"{lead + 0.5:.3f}", "-i", asset, "-vf",
                    f"setpts=PTS-STARTPTS,fps={fps:.3f},crop='min(iw,ih*"
                    f"{ar:.6f})':'min(ih,iw/{ar:.6f})',scale={W}:{H},"
                    "setsar=1", "-c:v", "libx264", "-qp", "0", "-pix_fmt",
                    "yuv420p", ref], check=True, capture_output=True)
    out["inside_psnr_db"] = []
    t_open = float(_op(run, "tw")["value"]["params"]["open_at"]) * \
        (rec["end"] - rec["start"]) + rec["start"]
    for k in (f0 + 4, f0 + 14):
        if k / fps >= t_open:
            continue
        a = clip_alpha(rec, k - f0, W, H)
        core = erode(a == 255, 2)
        R = frames(rv["path"], [k], "gray")[k]
        X = frames(ref, [k - f0], "gray")[k - f0]
        out["inside_psnr_db"].append(psnr(R, X, core))
        out.setdefault("outside_field_max_dev", []).append(int(np.abs(
            frames(rv["path"], [k])[k].astype(int)
            - np.array([0xb3, 0x12, 0x1f]))[~dilate(a > 0, 2)].max()))
    n_clip = probe(rec["path"])["frames"]
    areas = [float((clip_alpha(rec, k - f0, W, H) > 127).mean())
             for k in range(f0, min(f1, f0 + n_clip))]
    out["window_area_first_last"] = [round(areas[0], 4),
                                     round(areas[-1], 4)]
    ko = first_frame(t_open, fps) - f0
    out["window_monotone"] = bool(np.all(np.diff(areas[ko:]) >= -0.01))
    a = frames(r["path"], [f1 - 1, f1, f1 + 1], "gray")
    jump = float(np.abs(a[f1].astype(int) - a[f1 - 1].astype(int)).mean())
    step = float(np.abs(a[f1 + 1].astype(int) - a[f1].astype(int)).mean())
    out["cut_continuity"] = {"jump_at_cut": round(jump, 2),
                             "next_step": round(step, 2)}
    out["renders"] = {"case": r["path"], "wall_s": r["wall"]}
    return out


def _lab(rgb):
    """sRGB 0-255 -> CIELAB (D65)."""
    c = rgb.astype(float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def delta_e2000(a, b):
    L1, a1, b1 = np.moveaxis(_lab(a), -1, 0)
    L2, a2, b2 = np.moveaxis(_lab(b), -1, 0)
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dL, dC = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dH = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lm, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    hm = (h1p + h2p) / 2
    hm = np.where(np.abs(h1p - h2p) > 180, hm + 180, hm) % 360
    hm = np.where(C1p * C2p == 0, h1p + h2p, hm)
    T = (1 - 0.17 * np.cos(np.radians(hm - 30))
         + 0.24 * np.cos(np.radians(2 * hm))
         + 0.32 * np.cos(np.radians(3 * hm + 6))
         - 0.20 * np.cos(np.radians(4 * hm - 63)))
    Sl = 1 + 0.015 * (Lm - 50) ** 2 / np.sqrt(20 + (Lm - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cmp, 1 + 0.015 * Cmp * T
    Rt = -2 * np.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7)) * \
        np.sin(np.radians(60 * np.exp(-((hm - 275) / 25) ** 2)))
    return np.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2
                   + Rt * (dC / Sc) * (dH / Sh))


def _solarise_duotone(rgb, hexv):
    """The documented solarise-with-colour map, in numpy."""
    luma = rgb.astype(float) @ np.array([0.2126, 0.7152, 0.0722])
    tri = np.where(luma < 128, 2 * luma, 2 * (255 - luma))
    c = np.array([int(hexv[i:i + 2], 16) for i in (1, 3, 5)], float)
    lo = tri[..., None] * c / 128
    hi = c + (tri[..., None] - 128) * (255 - c) / 127
    return np.clip(np.where(tri[..., None] < 128, lo, hi), 0, 255)


def at_1c_6(run):
    import compositor
    r = run.render(run.edl(), "case")
    c = run.render(run.edl([]), "control")
    W, H, fps = r["width"], r["height"], r["fps"]
    sol = _op(run, "sol")["value"]
    out = {"inside_mae": [], "outside_de2000_max": [],
           "outside_de2000_p99": []}
    for t in (0.5, 1.5, 2.4):
        k = first_frame(t, fps)
        pts = compositor.keyed_points(sol["region"], k / fps - sol["start"])
        im = compositor._draw("polygon", pts, W, H)
        poly = np.asarray(im) > 127
        R = frames(r["path"], [k])[k]
        C = frames(c["path"], [k])[k]
        inside = erode(poly, 4)
        want = _solarise_duotone(C, sol["colour"])
        out["inside_mae"].append(round(float(np.abs(
            R.astype(float) - want)[inside].mean()), 2))
        # the feather (2 px at 1080 -> a gaussian of ~3.6 px here) and the
        # 4:2:0 chroma cell are the edge; beyond them nothing may move
        outside = ~dilate(poly, 12)
        de = delta_e2000(R[outside], C[outside])
        out["outside_de2000_max"].append(round(float(de.max()), 3))
        out["outside_de2000_p99"].append(round(float(np.percentile(de, 99)),
                                               3))
    out["renders"] = {"case": r["path"], "wall_s": r["wall"]}
    return out


def at_1c_7(run):
    r = run.render(run.edl(), "case")
    z = run.render(run.edl(mutate=_zero_opacity), "zero")
    p = run.render(run.edl(mutate=_without("composite")), "plain")
    fps = r["fps"]
    res = {}
    for op in run.ops:
        it = op["value"]
        ks = list(range(first_frame(it["start"], fps),
                        first_frame(it["end"], fps)))
        R = frames(r["path"], ks, "gray")
        Z = frames(z["path"], ks, "gray")
        P = frames(p["path"], ks, "gray")
        res[it["id"]] = {
            "blend": it["composite"]["blend"],
            "max_darker": max(int((Z[k].astype(int) - R[k].astype(int))
                                  .max()) for k in ks),
            "px_darker_gt2": sum(int(((Z[k].astype(int) - R[k].astype(int))
                                      > 2).sum()) for k in ks),
            "normal_paint_max_darker": max(int(
                (Z[k].astype(int) - P[k].astype(int)).max()) for k in ks),
            "max_brighter": max(int((R[k].astype(int) - Z[k].astype(int))
                                    .max()) for k in ks)}
    return {"items": res, "renders": {"case": r["path"]}}


def at_1c_perf(run):
    plain = run.edl(mutate=lambda op: None if op["layer"] == "fx"
                    else _without("composite")(op))
    fx = run.edl()
    runs = {"plain": [], "fx": []}
    for rnd in range(2):
        for name, edl in (("plain", plain), ("fx", fx)):
            r = run.render(edl, f"{name}{rnd}")
            runs[name].append({"wall": r["wall"], "cpu": r["cpu"]})
    best = {k: {"wall": min(x["wall"] for x in v),
                "cpu": min(x["cpu"] for x in v)} for k, v in runs.items()}
    return {"best": best, "runs": runs,
            "ratio_cpu": round(best["fx"]["cpu"] / best["plain"]["cpu"], 3),
            "ratio_wall": round(best["fx"]["wall"] / best["plain"]["wall"],
                                3)}


AT = {"AT-1C-1": at_1c_1, "AT-1C-2": at_1c_2, "AT-1C-3": at_1c_3,
      "AT-1C-4": at_1c_4, "AT-1C-5": at_1c_5, "AT-1C-6": at_1c_6,
      "AT-1C-7": at_1c_7, "AT-1C-perf": at_1c_perf}


def main(out_path, cases):
    root = os.environ.get("W1C_AT_DIR") or os.path.join(
        os.path.dirname(out_path), "w1c_at")
    results = {}
    for cid in cases or [c for c in AT if c != "AT-1C-perf"]:
        try:
            run = Run(cid, root)
            t = time.time()
            results[cid] = AT[cid](run)
            results[cid]["wall_total_s"] = round(time.time() - t, 1)
        except Exception as e:  # noqa: BLE001 — the parent asserts on it
            import traceback
            results[cid] = {"error": f"{type(e).__name__}: {e}",
                            "trace": traceback.format_exc()[-2000:]}
    with open(out_path, "w") as f:
        json.dump(results, f, indent=1)


def run_child(out_path, cases=(), crf="0", timeout=3600):
    env = dict(os.environ, FINAL_CRF=crf)
    env["PYTHONPATH"] = os.pathsep.join(
        [WORKER, os.path.dirname(WORKER)]
        + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, os.path.abspath(__file__), out_path,
                        *cases], cwd=WORKER, env=env, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"w1c_at child failed:\n{r.stdout[-3000:]}\n"
                           f"{r.stderr[-3000:]}")
    with open(out_path) as f:
        return json.load(f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
