"""Track 1E acceptance (AT-1E-1 .. AT-1E-7): finishing and the Variant Forge.

    cd worker && python -m benchmarks.refbench.at1e AT-1E-1 [AT-1E-2 ...]

Each case is a spec in ``cases/1E/`` (the Wave 0 schema, INTERFACES §8.1);
its devices are ``edl.ops`` apply_edit_batch operations, so every run also
proves the stale-client path. The 0D runner has no measurer for these
device metrics, so this harness renders (lossless finals, a control render
of the base EDL), measures the case's pinned ``metrics`` and its
``device_metrics`` (below), runs the standing checks, builds the boards
against the reference moments and writes the verdict JSON.

Device metrics (defined here, measured on the rendered frames):

  bg_saturation      mean HSV saturation of everything the hue key does NOT
                     keep (3 px away from kept pixels), rendered     [1E-1]
  kept_delta_e       mean CIEDE2000 render vs control over kept pixels
  fringe_chroma      mean CIELAB chroma C* of the render in the 2-4 px
                     band outside kept pixels, over pixels that are not
                     the colour (a chroma fringe shows up here; C* is on
                     the dE scale, so the bar matches kept_delta_e's.
                     HSV saturation is NOT used: near black it reads
                     (15,10,12) as 0.33 "saturated")
  mono_before_drop   max chroma deviation (8-bit) of frames before the
                     ramp                                            [1E-2]
  ramp_monotone      1 when the ramp frames rise strictly into the drop
  drop_frame_exact   |colour-full frame - drop frame| (frames)
  audio_drop_err_f   |measured audio drop - colour drop| (frames)
  hot_palette_share  mean palette position (0..1) of the hottest 3 % of
                     the shot (the plume / the person)              [1E-3]
  edge_shift_px      phase-correlation shift of the gradient maps
  variant_cpu_s      CPU seconds for the set                        [1E-4]
  edge_precision     each variant's edges that sit on a photo edge (worst
                     over the set, 3 px; the photo's edges at full
                     sensitivity, since drawings ink weak edges too)
  edge_iou           tolerant edge IoU of the exact-edge variant (the
                     negative); recall per variant is in the detail
  subpixel_contrast  grid-frequency energy / neighbours in a 100 %
                     crop                                           [1E-5]
  barrel_pct         corner displacement of a test grid, % of the
                     half diagonal
  neutral_delta_e    mean dE2000 of the neutral patch, matched shot vs
                     hero                                           [1E-6]
  skin_delta_e       the same for the skin patch
  section_edges_on_cuts  |first/last changed frame - cut frame|, worst
                     (frames)                                       [1E-7]

Internal only: references, fixtures, renders and boards never enter git or
product storage. Boards default to
~/Documents/Valmera/.valmera/refbench/boards/wave1/1E.
"""

import copy
import json
import math
import os
import shutil
import subprocess
import sys
import time

os.environ.setdefault("FINAL_CRF", "0")     # exact comparisons: lossless

import numpy as np  # noqa: E402

from . import CASES_DIR, fixtures_dir, out_dir, reference_path  # noqa: E402

CASE_DIR = os.path.join(CASES_DIR, "1E")
BOARDS = os.environ.get("VALMERA_1E_BOARDS") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave1/1E")


# ── plumbing ─────────────────────────────────────────────────────────────

def _bar(m):
    return (m["op"], m["value"]) if "op" in m else None


def _result(value, bar, detail=None):
    import qa_metrics
    passed = qa_metrics.check(value, bar) if bar is not None else None
    out = {"value": None if value is None else float(value), "pass": passed,
           "detail": detail or {}}
    if bar is not None:
        out["bar"] = qa_metrics.bar_text(bar)
    return out


def load(case_id):
    from . import spec
    path = os.path.join(CASE_DIR, f"{case_id}.yaml")
    case = spec.load(path)
    errs = spec.validate(case)
    if errs:
        raise SystemExit(f"{case_id}: invalid spec: {errs}")
    return case


def catalog_window(item_id, path_rel):
    """A pseudo-VFL window over a house-corpus stand-in (fixtures
    catalog), on the file clock."""
    import qa_metrics
    path = os.path.join(fixtures_dir(), path_rel)
    info = qa_metrics.probe(path)
    dur = float(info["duration"])
    return {"vfl": "HC", "name": item_id, "path": path, "t": [0.0, dur],
            "file_t0": 0.0, "file_span": [0.0, dur],
            "width": info["width"], "height": info["height"],
            "fps": info["fps"], "sha256": None}


def build(case, win, *, assets=None, extra_ops=None):
    """(control, edl, locals): the window's base EDL and the case's
    apply_edit_batch ops on it (``assets``: {key: local path} the batch
    may introduce, as a project's attached media)."""
    import edit_batch
    from schemas import validate_edl
    from . import engine
    duration = float(win["file_span"][1] - win["file_span"][0])
    base = validate_edl(engine.base_edl(case, win), duration).model_dump()
    ops = [op for op in (case.get("edl") or {}).get("ops") or []
           if "harness" not in op] + list(extra_ops or [])
    edl = edit_batch.apply_batch(base, ops, duration, set(assets or {})) \
        if ops else copy.deepcopy(base)
    control = copy.deepcopy(edl)
    control["fx"] = []
    control = validate_edl(control, duration).model_dump()
    return control, edl, dict(assets or {})


DEVICE_ROWS = {}


def render(edl, win, out, workdir, *, preview=False, locals_=None):
    """engine.render, with the case's local media visible to the fx
    compilers too (a LUT asset; gradewin reads VALMERA_FX_ASSET_LOCALS on
    test lanes) and the device report rows kept per output."""
    import fxgraph
    from . import engine
    old = os.environ.get("VALMERA_FX_ASSET_LOCALS")
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps(locals_ or {})
    rows = []
    tok = fxgraph.collect(rows)
    try:
        return engine.render(edl, win, out, workdir, preview=preview,
                             locals_=locals_ or {})
    finally:
        fxgraph.stop_collecting(tok)
        DEVICE_ROWS[out] = fxgraph.merge_report(rows)
        if old is None:
            os.environ.pop("VALMERA_FX_ASSET_LOCALS", None)
        else:
            os.environ["VALMERA_FX_ASSET_LOCALS"] = old


def frames(path, idx=None, *, size=None, count=None):
    import qa_metrics
    got = qa_metrics.read_frames(path, frames=set(idx) if idx else None,
                                 size=size, count=count)
    return dict(zip(*got))


def fps_of(path):
    import qa_metrics
    return float(qa_metrics.probe(path)["fps"])


def hsv_sat(rgb):
    a = rgb.astype(np.float64)
    mx, mn = a.max(axis=-1), a.min(axis=-1)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)


def ycc(rgb):
    import lutlib
    return lutlib.rgb_to_ycc8(rgb.astype(np.float64) / 255.0)


def dilate(mask, r):
    import cv2
    k = np.ones((2 * r + 1, 2 * r + 1), np.uint8)
    return cv2.dilate(mask.astype(np.uint8), k) > 0


def md5_list(path):
    import qa_metrics
    return qa_metrics.frame_md5s(path)


def board_dir(case_id):
    d = os.path.join(BOARDS, case_id)
    os.makedirs(d, exist_ok=True)
    return d


def ref_still(ref, t, height):
    from . import boards
    rp = reference_path(ref)
    return boards.grab(rp, t, height) if rp else None


def sheet(rows, out, *, label_h=28, gap=8):
    """A board from rows of (label, [(caption, rgb array), ...])."""
    from PIL import Image, ImageDraw
    from . import boards
    width = max(sum(im.shape[1] + gap for _c, im in row) + gap
                for _l, row in rows)
    height = sum(max(im.shape[0] for _c, im in row) + 2 * label_h + gap
                 for _l, row in rows) + gap
    img = Image.new("RGB", (width, height), (14, 14, 18))
    d = ImageDraw.Draw(img)
    f = boards._font(18)
    y = gap
    for label, row in rows:
        d.text((gap, y), label, fill=(240, 196, 80), font=f)
        y += label_h
        x = gap
        hmax = max(im.shape[0] for _c, im in row)
        for cap, im in row:
            img.paste(Image.fromarray(im.astype(np.uint8)), (x, y))
            d.text((x + 4, y + im.shape[0] + 4), cap, fill=(230, 230, 230),
                   font=boards._font(14))
            x += im.shape[1] + gap
        y += hmax + label_h + gap
    img.save(out)
    return out


def _h(rgb, h):
    from PIL import Image
    w = max(2, int(round(rgb.shape[1] * h / rgb.shape[0])))
    return np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize(
        (w, h), Image.LANCZOS))


def robot_yuv(render, control, *, frames_=None):
    """The free-tier mark in the delivered 4:2:0 planes: max |dY| over the
    robot's opaque pixels and max |dC| over chroma samples whose four luma
    pixels are all opaque robot, render vs control. (The RGB standing check
    interpolates chroma from samples OUTSIDE the robot when it decodes, so a
    grade under the mark moves its RGB by a level or two although not one
    plane sample of the mark changed.)"""
    import qa_metrics
    from . import standing
    info = qa_metrics.probe(render)
    W, H = info["width"], info["height"]
    core = standing.robot_core(W, H, erode=1)
    if core is None:
        return None
    x, y, m, _rgb = core
    n = len(qa_metrics.frame_md5s(render))
    picks = frames_ or list(range(0, max(1, n - 160), 15))

    def planes(path, f):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-vf",
             f"select=eq(n\\,{f})", "-frames:v", "1", "-pix_fmt",
             "yuv420p", "-f", "rawvideo", "-"], capture_output=True,
            check=True).stdout
        a = np.frombuffer(raw, np.uint8).astype(int)
        Y = a[:W * H].reshape(H, W)
        U = a[W * H:W * H + W * H // 4].reshape(H // 2, W // 2)
        V = a[W * H + W * H // 4:].reshape(H // 2, W // 2)
        return Y, U, V
    full = np.zeros((H, W), bool)
    full[y:y + m.shape[0], x:x + m.shape[1]] = m
    cm = full[::2, ::2] & full[1::2, ::2] & full[::2, 1::2] & full[1::2, 1::2]
    dy = dc = 0
    for f in picks:
        a, b = planes(render, f), planes(control, f)
        dy = max(dy, int(np.abs(a[0] - b[0])[full].max()))
        dc = max(dc, int(max(np.abs(a[1] - b[1])[cm].max(),
                             np.abs(a[2] - b[2])[cm].max())))
    return {"frames": len(picks), "luma_max_abs_diff": dy,
            "chroma_max_abs_diff": dc, "robot_px": int(full.sum()),
            "chroma_samples": int(cm.sum())}


def finish_verdict(case, cdir, *, render_path, control_path, edl, metrics,
                   program_s, outro_s=0.0, wall_s=None, board=None,
                   extra=None):
    from . import standing, verdict
    windows = [(float(i["start"]), float(i["end"])) for i in edl.get("fx")
               or [] if i.get("start") is not None]
    st, st_detail = standing.run_all(case, render_path, control=control_path,
                                     edl=edl, outro_s=outro_s,
                                     device_windows=windows,
                                     program_s=program_s)
    if case["render"]["variant"] == "final" and control_path:
        try:
            st_detail["brand_integrity_yuv"] = robot_yuv(render_path,
                                                         control_path)
        except Exception as e:  # noqa: BLE001
            st_detail["brand_integrity_yuv"] = {"error": str(e)[:200]}
    devices = DEVICE_ROWS.get(render_path) or []
    not_full = [r for r in devices if r.get("rung") != "full"]
    if not_full:
        # a device that did not render at full strength fails the case
        # loudly: the numbers above would describe a degraded render
        metrics["devices_full"] = _result(float(len(not_full)), ("<=", 0),
                                          {"rows": not_full})
    v = verdict.build(case, render_path=render_path,
                      variant=case["render"]["variant"], wall_s=wall_s,
                      metrics=metrics, standing=st, standing_detail=st_detail,
                      devices=devices,
                      board=board or {}, extra=dict(extra or {}, metric_detail={
                          k: m.get("detail") for k, m in metrics.items()}))
    p = verdict.write(v, cdir)
    print(f"{case['id']}: pass={v['pass']} -> {p}")
    for k, m in v["metrics"].items():
        print(f"   {k:<22} {m['value']} bar {m['bar']} pass {m['pass']}")
    print(f"   standing {v['standing']}")
    return v


def _metric_table(case):
    return {m["name"]: m for m in (case.get("metrics") or [])
            + (case.get("device_metrics") or [])}


def _render_pair(case, win, cdir, *, assets=None, preview=None, twice=True):
    """Control, render and (for determinism) a second render."""
    preview = (case["render"]["variant"] == "preview") if preview is None \
        else preview
    control, edl, locals_ = build(case, win, assets=assets)
    work = os.path.join(cdir, "work")
    cpath = os.path.join(cdir, "control.mp4")
    rpath = os.path.join(cdir, "render.mp4")
    _d, wall_c = render(control, win, cpath, work + "/c", preview=preview,
                        locals_=locals_)
    prog, wall = render(edl, win, rpath, work + "/a", preview=preview,
                        locals_=locals_)
    r2 = None
    if twice:
        r2 = os.path.join(cdir, "render_2.mp4")
        render(edl, win, r2, work + "/a2", preview=preview, locals_=locals_)
    import renderer
    outro_s = renderer.outro_seconds(preview)
    with open(os.path.join(cdir, "edl.json"), "w") as f:
        json.dump(edl, f, indent=1)
    return {"control": control, "edl": edl, "cpath": cpath, "rpath": rpath,
            "r2": r2, "wall": wall, "wall_c": wall_c, "outro_s": outro_s,
            "program_s": float(prog) - outro_s, "locals": locals_}


def _pinned(case, run, fps):
    """The pinned metrics a 1E case lists (region_locality over the
    frames outside every fx window, determinism, duration_exact,
    cost_ratio, flash_risk)."""
    import qa_metrics
    want = {m["name"]: m for m in case.get("metrics") or []}
    out = {}
    n_prog = int(round(run["program_s"] * fps))
    inside = set()
    for it in run["edl"].get("fx") or []:
        if it.get("start") is None:
            continue
        a = int(math.floor(float(it["start"]) * fps + 0.5))
        b = int(math.floor(float(it["end"]) * fps + 0.5))
        inside |= set(range(a, b))
    if "region_locality" in want:
        ra, rb = md5_list(run["rpath"]), md5_list(run["cpath"])
        outside = [i for i in range(min(n_prog, len(ra), len(rb)))
                   if i not in inside]
        diff = [i for i in outside if ra[i] != rb[i]]
        out["region_locality"] = _result(
            0.0 if not diff else 255.0, _bar(want["region_locality"]),
            {"frames_outside_windows": len(outside),
             "changed_outside": diff[:20],
             "method": "frame md5 of lossless renders outside every fx "
                       "window (and outside the program the end card)"})
    if "determinism" in want and run.get("r2"):
        out["determinism"] = qa_metrics.determinism(
            paths=[run["rpath"], run["r2"]], bar=_bar(want["determinism"]))
    if "duration_exact" in want:
        out["duration_exact"] = qa_metrics.duration_exact(
            with_fx=run["rpath"], without_fx=run["cpath"],
            bar=_bar(want["duration_exact"]))
    if "cost_ratio" in want:
        out["cost_ratio"] = qa_metrics.cost_ratio(
            with_s=run["wall"], without_s=run["wall_c"],
            bar=_bar(want["cost_ratio"]))
    if "flash_risk" in want:
        _, fr = qa_metrics.read_frames(run["rpath"], size=(160, 90),
                                       count=n_prog)
        out["flash_risk"] = qa_metrics.flash_risk(
            frames=fr, fps=fps, opted_in="flash_optin" in case.get("allow",
                                                                    []),
            bar=_bar(want["flash_risk"]))
    return out


# ── AT-1E-1: B&W world, one colour kept ─────────────────────────────────

def keep_metrics(ctrl, rend, key):
    """bg_saturation, kept_delta_e, fringe_chroma on one frame pair."""
    import lutlib
    c = ycc(ctrl)
    w = lutlib.hue_weight(key, c[..., 1], c[..., 2], c[..., 0])
    kept = w > 0.95
    near = dilate(w > 0.05, 3)
    bg = ~near
    sat = hsv_sat(rend)
    out = {"bg_saturation": float(sat[bg].mean()) if bg.any() else None,
           "bg_saturation_p99": float(np.percentile(sat[bg], 99))
           if bg.any() else None,
           "kept_share": float(kept.mean())}
    if kept.sum() > 50:
        de = lutlib.delta_e2000(lutlib.rgb_to_lab(rend[kept] / 255.0),
                                lutlib.rgb_to_lab(ctrl[kept] / 255.0))
        out["kept_delta_e"] = float(de.mean())
    # fringe: colour leaking OUT of the kept object onto pixels that are
    # not the colour at all (source weight < 0.02), 2-4 px from it — past
    # the object's own anti-aliased edge and the 4:2:0 chroma sample
    band = dilate(kept, 4) & ~dilate(kept, 1) & (w < 0.02)
    if band.sum() > 50:
        lab = lutlib.rgb_to_lab(rend[band] / 255.0)
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        out["fringe_chroma"] = float(chroma.mean())
        out["fringe_chroma_p95"] = float(np.percentile(chroma, 95))
        out["fringe_hsv_saturation"] = float(sat[band].mean())
        out["fringe_px"] = int(band.sum())
    return out


def run_keep(case, win, cdir, *, hue, tolerance, label, softness=0.1):
    import gradewin
    run = _render_pair(case, win, cdir)
    fps = fps_of(run["rpath"])
    gw = next(i for i in run["edl"]["fx"] if i["type"] == "grade_window")
    a = int(math.floor(float(gw["start"]) * fps + 0.5))
    b = int(math.floor(float(gw["end"]) * fps + 0.5))
    sample = list(range(a + 3, b - 3, max(1, (b - a) // 12)))
    cf, rf = frames(run["cpath"], sample), frames(run["rpath"], sample)
    key = gradewin.hue_key(hue, tolerance, softness)
    per = [keep_metrics(cf[i], rf[i], key) for i in sample if i in rf]
    agg = {}
    for k in ("bg_saturation", "kept_delta_e", "fringe_chroma"):
        vals = [p[k] for p in per if p.get(k) is not None]
        agg[k] = (max(vals) if k != "kept_delta_e" else float(np.mean(vals))
                  ) if vals else None
    table = _metric_table(case)
    metrics = _pinned(case, run, fps)
    for k, v in agg.items():
        if k in table:
            metrics[k] = _result(v, _bar(table[k]), {
                "frames": len(per), "per_frame": [round(p.get(k) or 0, 4)
                                                  for p in per],
                "aggregate": "max over frames" if k != "kept_delta_e"
                else "mean over frames"})
    bd = board_dir(case["id"])
    rows = []
    for t in case["board"]["stills"]:
        f = int(round(t * fps))
        got_c = frames(run["cpath"], [f])[f]
        got_r = frames(run["rpath"], [f])[f]
        rows.append((f"{label} @ {t:.2f}s", [("control", _h(got_c, 360)),
                                             ("keep " + hue, _h(got_r, 360))]))
    ref = (case.get("reference") or [{}])[0]
    if ref:
        rs = [ref_still(ref["ref"], t, 360) for t in (1.0, 6.0, 12.0)]
        rows.append((f"reference {ref['ref']} (internal)",
                     [(f"{ref['ref']}@{t:g}", im) for t, im in
                      zip((1.0, 6.0, 12.0), rs) if im is not None]))
    b = sheet(rows, os.path.join(bd, f"{label}_board.png"))
    return run, metrics, {"images": [b]}, fps


# ── AT-1E-2: mono until the drop, colour landing ON it ──────────────────

def chroma_dev(rgb):
    c = ycc(rgb)
    return np.abs(c[..., 1] - 128) + np.abs(c[..., 2] - 128)


def audio_drop_s(path, near, *, cutoff=150, window=0.6):
    """The sub-bass entry near ``near`` (s): the 10 ms hop whose low-band
    energy rises most (>= 12 dB) over the median of the preceding 300 ms
    (the drop, not the pickup out of the pre-drop gap)."""
    sr = 8000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map",
                          "0:a:0", "-af", f"lowpass=f={cutoff},lowpass="
                          f"f={cutoff}", "-ac", "1", "-ar", str(sr), "-f",
                          "f32le", "-"], capture_output=True,
                         check=True).stdout
    x = np.frombuffer(raw, np.float32)
    hop = sr // 100
    e = np.array([float(np.mean(x[i:i + hop] ** 2) + 1e-12)
                  for i in range(0, len(x) - hop, hop)])
    db = 10 * np.log10(e)
    lo, hi = int((near - window) * 100), int((near + window) * 100)
    best, rise = None, 12.0
    for i in range(max(30, lo), min(len(db), hi)):
        r = db[i] - np.median(db[i - 30:i])
        if r >= rise:
            best, rise = i, r
    return None if best is None else best / 100.0


# ── AT-1E-3: thermal flip ───────────────────────────────────────────────

def palette_position(rgb, palette="iron"):
    """Per-pixel position 0..1 in the thermal palette (nearest entry)."""
    import finish
    tabs = finish._palette_tables(finish._THERMAL[palette])
    pal = np.stack([np.array(t) for t in tabs], axis=1)        # 256 x 3
    flat = rgb.reshape(-1, 3).astype(np.float32)
    best = np.zeros(len(flat), np.int32)
    bestd = np.full(len(flat), np.inf, np.float32)
    for i in range(0, 256, 1):
        d = ((flat - pal[i]) ** 2).sum(axis=1)
        m = d < bestd
        best[m], bestd[m] = i, d[m]
    return (best / 255.0).reshape(rgb.shape[:2])


def gradient_shift(a_gray, b_gray):
    import cv2

    def grad(g):
        g = g.astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
        return cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 2)
    (dx, dy), resp = cv2.phaseCorrelate(grad(a_gray), grad(b_gray))
    return float(math.hypot(dx, dy)), float(resp)


# ── AT-1E-4: registered variants ────────────────────────────────────────

# drawings simplify (judged by precision); the rest keep every edge
DRAWINGS = ("line", "blueprint")
# variants whose edges are the photo's exactly (a pure inversion): judged
# by tolerant IoU too. Thermal softens and remaps tones by design (a sensor
# look), so like the drawings it is judged by precision and shift.
EXACT_EDGES = ("negative",)

def edge_scores(photo, variant, tol=3):
    import cv2

    def edges(img):
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return cv2.Canny(cv2.GaussianBlur(g, (5, 5), 1.2), 40, 110) > 0
    a, b = edges(photo), edges(variant)
    da, db = dilate(a, tol), dilate(b, tol)
    recall = float((a & db).sum() / max(1, a.sum()))
    iou = float((da & db).sum() / max(1, (da | db).sum()))
    return recall, iou


def edge_precision(photo, variant, tol=3):
    """Share of the VARIANT's own edges that sit on a photo edge (within
    ``tol`` px): a drawing (line art, blueprint) simplifies, so it is judged
    by whether every line it draws is a real edge, not by how many it
    leaves out."""
    import cv2

    def edges(img, lo, hi):
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return cv2.Canny(cv2.GaussianBlur(g, (5, 5), 1.2), lo, hi) > 0
    # the photo's edges at full sensitivity: a drawing also inks the weak
    # edges (a seam, a panel line) a default Canny leaves out
    a, b = edges(photo, 10, 30), edges(variant, 40, 110)
    return float((b & dilate(a, tol)).sum() / max(1, b.sum()))


# ── AT-1E-5: screens ────────────────────────────────────────────────────

def lit_crop(gray, size=256, step=64):
    """(y0, x0) of the brightest ``size`` crop: a 100 % crop of the
    picture's content, not of a black void (a UI page is mostly black)."""
    h, w = gray.shape
    best, at = -1.0, (max(0, h // 2 - size // 2), max(0, w // 2 - size // 2))
    for y0 in range(0, max(1, h - size + 1), step):
        for x0 in range(0, max(1, w - size + 1), step):
            m = float(gray[y0:y0 + size, x0:x0 + size].mean())
            if m > best:
                best, at = m, (y0, x0)
    return at


def grid_contrast(gray, pitch, at=None):
    """Energy at the screen grid's frequency over its spectral neighbours
    in a 256 px crop at ``at`` (default: the centre; > 3: the subpixel
    pattern is visible at 100 %)."""
    h, w = gray.shape
    y0, x0 = at or (h // 2 - 128, w // 2 - 128)
    crop = gray[y0:y0 + 256, x0:x0 + 256].astype(np.float64)
    crop -= crop.mean()
    win = np.hanning(256)[:, None] * np.hanning(256)[None, :]
    spec = np.abs(np.fft.fftshift(np.fft.fft2(crop * win)))
    prof = spec.sum(axis=0)
    c = 128
    k = int(round(256 / pitch))
    band = prof[c + k - 2:c + k + 3].max()
    neigh = np.median(np.concatenate([prof[c + k - 12:c + k - 4],
                                      prof[c + k + 4:c + k + 12]]))
    return float(band / max(neigh, 1e-9))


def record_ui(url, out, *, seconds=4.0, fps=30, css=(540, 960), scale=2):
    """FX-UI: a scrolling capture of an owned web page (headless Chromium,
    one screenshot a frame, the page scrolled a little each frame), as an
    H.264 clip. Internal test input only."""
    import tempfile
    from playwright.sync_api import sync_playwright
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fxui_")
    n = int(seconds * fps)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": css[0], "height": css[1]},
                        device_scale_factor=scale)
        pg.goto(url, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(1500)
        for i in range(n):
            pg.evaluate(f"window.scrollTo(0, {int(i * 6)})")
            pg.screenshot(path=os.path.join(tmp, f"f{i:04d}.png"))
        b.close()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps),
                    "-i", os.path.join(tmp, "f%04d.png"), "-c:v", "libx264",
                    "-crf", "10", "-pix_fmt", "yuv420p", "-movflags",
                    "+faststart", out], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def barrel_pct(kind, W, H, workdir):
    """TV distortion (%) of finish ``kind``: one bright horizontal line 8 %
    from the top of a W x H chart, drawn through the finish; its bow (the
    line's height at the centre minus at 15 % / 85 % of the width) over
    the frame height. Luma-weighted centroids, so LED dots and a CRT bezel
    do not move the measurement."""
    import finish
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (0, 0, 0))
    y0 = int(H * 0.08)
    ImageDraw.Draw(im).rectangle([0, y0 - 3, W, y0 + 3],
                                 fill=(255, 255, 255))
    src = os.path.join(workdir, f"hline_{W}x{H}.png")
    im.save(src)
    g, maps = finish.still_graph(kind, W, H, {"id": f"hline_{kind}"},
                                 workdir)
    out = os.path.join(workdir, f"hline_{kind}.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src]
                   + sum((["-i", m] for m in maps), [])
                   + ["-filter_complex", g, "-map", "[out]", "-frames:v",
                      "1", out], check=True)
    a = np.asarray(Image.open(out).convert("L")).astype(np.float64)

    def centroid(xc):
        band = a[:int(H * 0.3), max(0, xc - 8):xc + 8].mean(axis=1)
        band = np.where(band > band.max() * 0.35, band, 0.0)
        ys = np.arange(len(band))
        return float((band * ys).sum() / max(band.sum(), 1e-9))
    yc = centroid(W // 2)
    ye = (centroid(int(W * 0.15)) + centroid(int(W * 0.85))) / 2
    return 100.0 * abs(yc - ye) / H, {"y_centre": round(yc, 2),
                                      "y_edges": round(ye, 2)}


# ── runners ─────────────────────────────────────────────────────────────

def at_1e_1(case):
    from . import engine
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    op = case["edl"]["ops"][0]["value"]["isolate"]
    run, metrics, board, fps = run_keep(case, win, cdir, hue=op["hue"],
                                        tolerance=op.get("tolerance", 0.12),
                                        softness=op.get("softness", 0.1),
                                        label="V04")
    extra = {}
    hc = case.get("final_standin")
    if hc:
        # HC-D (red coat, grey square): the roadmap's final footage, here
        # through the verb's own 'auto' hue solver on the shot
        import lutlib
        hwin = catalog_window(hc["id"], hc["path"])
        hdir = os.path.join(cdir, "hcd")
        os.makedirs(hdir, exist_ok=True)
        import gradewin
        fr = [list(frames(hwin["path"], [int(hwin["fps"] * t)]).values())[0]
              for t in hc["solve_t"]]
        hue, share = gradewin.hue_from_frames(fr)
        tol, soft = gradewin.key_from_frames(fr, hue)
        hcase = copy.deepcopy(case)
        hcase["id"] = case["id"]
        hcase["edl"] = {"base": "vfl_default", "keep": hc["keep"], "ops": [
            {"action": "upsert", "layer": "fx", "id": "gw_coat", "value": {
                "id": "gw_coat", "type": "grade_window", "stage": "world",
                "start": 0.0, "end": round(hc["keep"][0][1]
                                           - hc["keep"][0][0], 2),
                "isolate": {"hue": hue, "tolerance": tol,
                            "softness": soft},
                "device": "grade_isolate_hue"}}]}
        hcase["board"] = {"stills": hc["stills"]}
        hrun, hm, hboard, _ = run_keep(hcase, hwin, hdir, hue=hue,
                                       tolerance=tol, softness=soft,
                                       label="HC-D")
        extra["final_standin"] = {
            "item": hc["id"], "auto_hue": hue, "share": share,
            "auto_tolerance": tol, "auto_softness": soft,
            "metrics": {k: v["value"] for k, v in hm.items()},
            "pass": {k: v["pass"] for k, v in hm.items()},
            "board": hboard["images"]}
        for k, v in hm.items():
            if k in ("bg_saturation", "kept_delta_e", "fringe_chroma"):
                metrics[f"hcd_{k}"] = v
        board["images"] += hboard["images"]
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board=board, extra=extra)


def at_1e_2(case):
    from . import engine
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    mus = case["music_asset"]
    assets = {mus["key"]: os.path.join(fixtures_dir(), mus["path"])}
    run = _render_pair(case, win, cdir, assets=assets)
    fps = fps_of(run["rpath"])
    gw = next(i for i in run["edl"]["fx"] if i["type"] == "grade_window")
    drop_f = int(math.floor(float(gw["end"]) * fps + 0.5))
    ramp = int(gw.get("ramp_out_f") or 0)
    idx = list(range(max(0, drop_f - ramp - 30), drop_f + 6))
    cf, rf = frames(run["cpath"], idx), frames(run["rpath"], idx)
    share = {}
    for i in idx:
        c, r = chroma_dev(cf[i]), chroma_dev(rf[i])
        share[i] = float(r.mean() / max(c.mean(), 1e-9))
    before = [i for i in idx if i < drop_f - ramp]
    ramp_fs = list(range(drop_f - ramp, drop_f))
    # the free-tier mark is composited after every creative stage and
    # keeps its own colour: it is not part of the graded picture
    from . import standing
    H, W = rf[idx[0]].shape[:2]
    x0, y0, x1, y1 = standing.watermark_roi(W, H)
    pic = np.ones((H, W), bool)
    pic[y0:y1, x0:x1] = False
    mono = max(float(chroma_dev(rf[i])[pic].max()) for i in before)
    mono_ok = mono
    rises = [share[i] for i in ramp_fs]
    monotone = 1.0 if all(a < b for a, b in zip(
        [0.0] + rises, rises + [1.0])) else 0.0
    md5r, md5c = md5_list(run["rpath"]), md5_list(run["cpath"])
    first_full = next((i for i in range(drop_f - ramp - 1, drop_f + 6)
                       if md5r[i] == md5c[i] and all(
                           md5r[j] == md5c[j] for j in range(i, i + 5))),
                      None)
    drop_err = None if first_full is None else abs(first_full - drop_f)
    a_drop = audio_drop_s(run["rpath"], float(gw["end"]))
    a_err = None if a_drop is None else abs(round(a_drop * fps) - drop_f)
    table = _metric_table(case)
    metrics = _pinned(case, run, fps)
    vals = {"mono_before_drop": mono_ok, "ramp_monotone": monotone,
            "drop_frame_exact": drop_err, "audio_drop_err_f": a_err}
    for k, v in vals.items():
        if k in table:
            metrics[k] = _result(v, _bar(table[k]), {
                "drop_frame": drop_f, "ramp_frames": ramp_fs,
                "colour_share": {str(i): round(share[i], 4) for i in idx
                                 if i >= drop_f - ramp - 2},
                "first_full_colour_frame": first_full,
                "audio_drop_s": a_drop})
    bd = board_dir(case["id"])
    from . import boards
    strip = boards.strip(run["rpath"], drop_f / fps,
                         os.path.join(bd, "drop_strip.png"), before=5,
                         after=4, label="frames around the drop: mono, "
                                        "3-frame ease, colour ON the drop")
    ref = case["reference"][0]
    rr = boards.strip(reference_path(ref["ref"]), ref.get("board_t", 3.64),
                      os.path.join(bd, "ref_strip.png"), before=5, after=4,
                      label=f"reference {ref['ref']} (internal)")
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board={"images": [strip, rr]})


def at_1e_3(case):
    from . import engine
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    run = _render_pair(case, win, cdir)
    fps = fps_of(run["rpath"])
    fi = next(i for i in run["edl"]["fx"] if i["type"] == "finish")
    a = int(math.floor(float(fi["start"]) * fps + 0.5))
    b = int(math.floor(float(fi["end"]) * fps + 0.5))
    idx = list(range(a + 2, b - 1, max(1, (b - a) // 6)))
    cf, rf = frames(run["cpath"], idx), frames(run["rpath"], idx)
    hot, shifts = [], []
    import cv2
    for i in idx:
        lum = cv2.cvtColor(cf[i], cv2.COLOR_RGB2GRAY).astype(np.float64)
        thr = np.percentile(lum, 97)
        pos = palette_position(rf[i], fi.get("palette") or "iron")
        hot.append(float(pos[lum >= thr].mean()))
        s, _r = gradient_shift(lum, pos * 255.0)
        shifts.append(s)
    table = _metric_table(case)
    metrics = _pinned(case, run, fps)
    if "flash_added" in table:
        import qa_metrics
        n = int(round(run["program_s"] * fps))
        got = {}
        for name, path in (("render", run["rpath"]),
                           ("control", run["cpath"])):
            _, fr = qa_metrics.read_frames(path, size=(160, 90), count=n)
            got[name] = qa_metrics.flash_risk(frames=fr, fps=fps)
        added = got["render"]["detail"]["transitions"] - \
            got["control"]["detail"]["transitions"]
        metrics["flash_added"] = _result(
            max(0, added), _bar(table["flash_added"]),
            {"render": got["render"]["value"],
             "control": got["control"]["value"],
             "transitions": [got["render"]["detail"]["transitions"],
                             got["control"]["detail"]["transitions"]]})
    for k, v in (("hot_palette_share", min(hot)),
                 ("edge_shift_px", max(shifts))):
        if k in table:
            metrics[k] = _result(v, _bar(table[k]), {
                "per_frame": [round(x, 3) for x in (hot if k.startswith(
                    "hot") else shifts)]})
    bd = board_dir(case["id"])
    mid = idx[len(idx) // 2]
    rows = [("V13 starship: control | thermal flip",
             [("control", _h(cf[mid], 360)), ("thermal", _h(rf[mid], 360))])]
    rs = [(f"{r['ref']}@{r.get('board_t', r['t'][0]):g}",
           ref_still(r["ref"], r.get("board_t", r["t"][0]), 360))
          for r in case["reference"]]
    rows.append(("references (internal)", [x for x in rs
                                           if x[1] is not None]))
    board = sheet(rows, os.path.join(bd, "thermal_board.png"))
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board={"images": [board]})


def at_1e_4(case):
    import variants
    from . import engine
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    t = float(case["variant_t"])
    frame = os.path.join(cdir, "frame.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss",
                    f"{t - win['file_t0']:.3f}", "-i", win["path"],
                    "-frames:v", "1", frame], check=True)
    kinds = tuple(case["variant_kinds"])
    t0 = time.time()
    paths, stats = variants.render_variants(frame, kinds,
                                            os.path.join(cdir, "set"),
                                            seed=1, threads=1)
    wall = time.time() - t0
    again, _s2 = variants.render_variants(frame, kinds,
                                          os.path.join(cdir, "set2"),
                                          seed=1, threads=1)
    same = sum(open(paths[k], "rb").read() != open(again[k], "rb").read()
               for k in kinds)
    from PIL import Image
    imgs = {k: np.asarray(Image.open(p).convert("RGB"))
            for k, p in paths.items()}
    scores = {}
    for k in kinds:
        if k == "photo":
            continue
        r, iou = edge_scores(imgs["photo"], imgs[k])
        pr = edge_precision(imgs["photo"], imgs[k])
        s, _ = gradient_shift(np.asarray(Image.open(paths["photo"]).convert(
            "L")), np.asarray(Image.open(paths[k]).convert("L")))
        scores[k] = {"recall": round(r, 4), "precision": round(pr, 4),
                     "iou": round(iou, 4), "shift_px": round(s, 3),
                     "drawing": k in DRAWINGS}
    table = _metric_table(case)
    metrics = {}
    if "determinism" in table:
        metrics["determinism"] = _result(float(same), _bar(table[
            "determinism"]), {"differing_variants": same})
    exact = [v for k, v in scores.items() if k in EXACT_EDGES]
    vals = {"variant_cpu_s": stats["cpu_s"],
            "edge_precision": min(v["precision"] for v in scores.values()),
            "edge_iou": min(v["iou"] for v in exact) if exact else None,
            "edge_shift_px": max(v["shift_px"] for v in scores.values())}
    for k, v in vals.items():
        if k in table:
            metrics[k] = _result(v, _bar(table[k]), {"per_variant": scores,
                                                     "wall_s": round(wall, 2),
                                                     "size": [stats["W"],
                                                              stats["H"]]})
    bd = board_dir(case["id"])
    rows = [("V13 Optimus: the registered set (one frame)",
             [(k, _h(imgs[k], 300)) for k in kinds])]
    ref = case["reference"][0]
    rp = reference_path(ref["ref"])
    if rp:
        from . import boards
        rs = [(f"{ref['ref']}@{tt:.2f}", boards.grab(rp, tt, 300))
              for tt in (0.33, 0.46, 0.58, 0.71, 0.83)]
        rows.append((f"reference {ref['ref']} (internal)", rs))
    board = sheet(rows, os.path.join(bd, "variant_set_board.png"))
    v = {"case": case["id"], "metrics": metrics, "board": [board],
         "stats": stats, "scores": scores}
    from . import verdict
    vv = verdict.build(case, render_path=None, variant="still",
                       wall_s=round(wall, 2), metrics=metrics,
                       standing={"render_ok": True}, board={"images":
                                                           [board]},
                       extra={"metric_detail": {k: m["detail"] for k, m in
                                                metrics.items()}})
    p = verdict.write(vv, cdir)
    print(f"{case['id']}: pass={vv['pass']} -> {p}")
    for k, m in vv["metrics"].items():
        print(f"   {k:<22} {m['value']} bar {m['bar']} pass {m['pass']}")
    return vv


def at_1e_5(case):
    """LCD over a UI capture (FX-UI) and CRT / LED over the archival V03
    window; patterns measured at 100 %, barrel on a grid chart."""
    from . import engine
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    table = _metric_table(case)
    metrics, rows, extra = {}, [], {}
    # (a) the V03 window: CRT and LED from the case ops
    win = engine.window_for_case(case)
    run = _render_pair(case, win, cdir)
    fps = fps_of(run["rpath"])
    metrics.update(_pinned(case, run, fps))
    bd = board_dir(case["id"])
    for it in run["edl"]["fx"]:
        a = int(math.floor(float(it["start"]) * fps + 0.5))
        b = int(math.floor(float(it["end"]) * fps + 0.5))
        m = (a + b) // 2
        c, r = frames(run["cpath"], [m])[m], frames(run["rpath"], [m])[m]
        rows.append((f"V03 {it['kind']} @ {m / fps:.2f}s",
                     [("control", _h(c, 360)), (it["kind"], _h(r, 360)),
                      ("100% crop", r[r.shape[0] // 2 - 180:
                                      r.shape[0] // 2 + 180,
                                      r.shape[1] // 2 - 180:
                                      r.shape[1] // 2 + 180])]))
    # (b) FX-UI: a capture of valmera.io through the LCD finish
    ui = case.get("ui_capture")
    if ui:
        ui = os.path.join(fixtures_dir(), ui)
        if not os.path.exists(ui) and case.get("ui_url"):
            record_ui(case["ui_url"], ui)
    if ui and os.path.exists(ui):
        import qa_metrics
        uwin = {"vfl": "FX-UI", "name": "valmera_io", "path": ui,
                "t": [0.0, qa_metrics.probe(ui)["duration"]],
                "file_t0": 0.0,
                "file_span": [0.0, qa_metrics.probe(ui)["duration"]],
                "width": qa_metrics.probe(ui)["width"],
                "height": qa_metrics.probe(ui)["height"],
                "fps": qa_metrics.probe(ui)["fps"], "sha256": None}
        ucase = copy.deepcopy(case)
        ucase["aspect"] = "source"
        ucase["edl"] = {"base": "vfl_default", "ops": case["ui_ops"]}
        udir = os.path.join(cdir, "ui")
        os.makedirs(udir, exist_ok=True)
        urun = _render_pair(ucase, uwin, udir, twice=False)
        ufps = fps_of(urun["rpath"])
        it = urun["edl"]["fx"][0]
        m = int((float(it["start"]) + float(it["end"])) / 2 * ufps)
        c, r = frames(urun["cpath"], [m])[m], frames(urun["rpath"], [m])[m]
        import cv2
        H, W = r.shape[:2]
        pitch = max(3.0, 4.6 * min(W, H) / 1080.0 * float(it.get("scale")
                                                         or 1.0))
        cg = cv2.cvtColor(c, cv2.COLOR_RGB2GRAY)
        at = lit_crop(cg)
        gc = grid_contrast(cv2.cvtColor(r, cv2.COLOR_RGB2GRAY), pitch, at)
        gc0 = grid_contrast(cg, pitch, at)
        if "subpixel_contrast" in table:
            metrics["subpixel_contrast"] = _result(gc, _bar(
                table["subpixel_contrast"]), {"control": gc0,
                                              "pitch_px": pitch,
                                              "crop_at": list(at)})
        cy, cx = at[0] + 128, at[1] + 128
        y0, x0 = max(0, cy - 200), max(0, cx - 200)
        rows.append(("FX-UI (valmera.io capture): control | LCD | 100% crop",
                     [("control", _h(c, 480)), ("lcd", _h(r, 480)),
                      ("100% crop", r[y0:y0 + 400, x0:x0 + 400])]))
        extra["ui_edl"] = os.path.join(udir, "edl.json")
    # barrel (TV distortion) on a line chart, every screen kind
    worst = 0.0
    bdetail = {}
    for kind in ("lcd", "crt", "led"):
        got = barrel_pct(kind, 1080, 1920, cdir)
        if got:
            bdetail[kind] = round(got[0], 3)
            worst = max(worst, got[0])
    if "barrel_pct" in table:
        metrics["barrel_pct"] = _result(worst, _bar(table["barrel_pct"]),
                                        {"per_kind": bdetail})
    refs = []
    for r in case["reference"]:
        im = ref_still(r["ref"], r.get("board_t", r["t"][0]), 360)
        if im is not None:
            refs.append((f"{r['ref']}@{r.get('board_t', r['t'][0]):g}", im))
    rows.append(("references (internal)", refs))
    board = sheet(rows, os.path.join(bd, "screens_board.png"))
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board={"images": [board]}, extra=extra)


def class_mask(rgb, cls):
    """Pixels of one colour class in a frame: 'neutral' (near-achromatic
    mid tones), 'skin' (the luma-gated skin cluster) or a hue key."""
    import lutlib
    c = ycc(rgb)
    if cls["class"] == "neutral":
        rad = np.hypot(c[..., 1] - 128, c[..., 2] - 128)
        return (rad < 6) & (c[..., 0] > 40) & (c[..., 0] < 225)
    if cls["class"] == "skin":
        return lutlib.skin_weight(c[..., 1], c[..., 2], c[..., 0]) > 0.6
    key = lutlib.HueKey(cls["hue"], cls.get("tolerance", 0.08))
    return lutlib.hue_weight(key, c[..., 1], c[..., 2], c[..., 0]) > 0.9


def class_lab(frames_, cls):
    import lutlib
    px = np.concatenate([f[class_mask(f, cls)] for f in frames_])
    if len(px) < 200:
        return None, 0
    return lutlib.rgb_to_lab(px.reshape(-1, 3) / 255.0).mean(axis=0), \
        len(px)


def class_delta_e(lab_a, lab_b, cls):
    """dE2000 between two class means; a neutral compares its CAST (a*, b*)
    only: the hero's black T-shirt and the shot's white shirt are both
    neutral at different brightness."""
    import lutlib
    a, b = np.array(lab_a, float), np.array(lab_b, float)
    if cls["class"] == "neutral":
        a[0] = b[0] = 50.0
    return float(lutlib.delta_e2000(a, b))


def at_1e_6(case):
    """Shot match: the V14 shot (same JRE set, another day) matched to the
    V04 hero by the verb's own math (lutlib.match_lut on sampled frames),
    rendered through grade_window.lut, patches compared in CIELAB."""
    import lutlib
    from . import engine, vfl as vflmod
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)                 # V14: to be matched
    hero = case["hero"]
    hwin = vflmod.window(hero["vfl"], hero["window"])
    ht = [vflmod.file_time(hwin, t) for t in hero["t"]]
    hf = []
    for t in ht:
        f = int(round(t * hwin["fps"]))
        hf.append(list(frames(hwin["path"], [f]).values())[0])
    keep = case["edl"]["keep"][0]
    tf = []
    for k in range(5):
        t = vflmod.file_time(win, keep[0] + (keep[1] - keep[0]) * (k + 0.5)
                             / 5)
        f = int(round(t * win["fps"]))
        tf.append(list(frames(win["path"], [f]).values())[0])
    method = case.get("method", "mkl")
    region = case.get("stats_region")

    def px(fr):
        if not region:
            return fr.reshape(-1, 3)
        x0, y0, x1, y1 = region
        h, w = fr.shape[:2]
        return fr[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] \
            .reshape(-1, 3)
    src = np.concatenate([px(f) for f in tf])
    ref = np.concatenate([px(f) for f in hf])
    lut = lutlib.match_lut(src, ref, method=method, limit=40.0)
    cube = os.path.join(cdir, "match.cube")
    with open(cube, "w") as f:
        f.write(lutlib.cube_text(lut, title="AT-1E-6 match"))
    key = "generated/at1e6/match.cube"
    run = _render_pair(case, win, cdir, assets={key: cube})
    fps = fps_of(run["rpath"])
    patches = case["patches"]
    before, after, counts = {}, {}, {}
    n = int(run["program_s"] * fps)
    idx = list(range(3, max(4, n - 3), max(1, n // 10)))
    cfr, rfr = frames(run["cpath"], idx), frames(run["rpath"], idx)
    for name, cls in patches.items():
        hl, nh = class_lab(hf, cls)
        cb, nc = class_lab([cfr[i] for i in idx], cls)
        ca, _n = class_lab([rfr[i] for i in idx], cls)
        counts[name] = {"hero_px": nh, "shot_px": nc}
        if hl is None or cb is None or ca is None:
            before[name] = after[name] = None
            continue
        before[name] = class_delta_e(cb, hl, cls)
        after[name] = class_delta_e(ca, hl, cls)
    table = _metric_table(case)
    metrics = _pinned(case, run, fps)
    for name in patches:
        k = f"{name}_delta_e"
        if k in table:
            metrics[k] = _result(after[name], _bar(table[k]), {
                "before": None if before[name] is None else
                round(before[name], 3),
                "after": None if after[name] is None else
                round(after[name], 3), "method": method,
                "pixels": counts[name]})
    bd = board_dir(case["id"])
    mid = idx[len(idx) // 2]
    rows = [("hero (V04) | V14 as shot | V14 matched",
             [("hero", _h(hf[0], 360)), ("V14 control", _h(cfr[mid], 360)),
              ("V14 matched", _h(rfr[mid], 360))])]
    board = sheet(rows, os.path.join(bd, "shot_match_board.png"))
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board={"images": [board]},
                          extra={"before": before, "after": after})


def at_1e_7(case):
    """Sections on V13: a B&W section, a kaleidoscope and a pixel stretch,
    each starting and ending on cut frames."""
    from . import engine
    from timeline import Timeline
    cdir = os.path.join(out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    run = _render_pair(case, win, cdir)
    fps = fps_of(run["rpath"])
    import renderer
    tl = Timeline(run["edl"]["keep"], [], [])
    # the render's own cut frames: the block clock starts block j on
    # first_frame_at(T_j)
    cuts = [renderer.first_frame_at(float(o), fps) for o in tl.offsets[1:]]
    windows = {}
    for it in run["edl"]["fx"]:
        windows[it["id"]] = (
            int(math.floor(float(it["start"]) * fps + 0.5)),
            int(math.floor(float(it["end"]) * fps + 0.5)))
    md5r, md5c = md5_list(run["rpath"]), md5_list(run["cpath"])
    worst = 0
    det = {}
    for it in run["edl"]["fx"]:
        a = int(math.floor(float(it["start"]) * fps + 0.5))
        b = int(math.floor(float(it["end"]) * fps + 0.5))
        others = {f for k, (x, y) in windows.items() if k != it["id"]
                  for f in range(x, y)}
        changed = [i for i in range(max(0, a - 5), min(len(md5r), b + 5))
                   if md5r[i] != md5c[i] and i not in others]
        first, last = (changed[0], changed[-1]) if changed else (None, None)
        ca = min(cuts, key=lambda c: abs(c - a)) if cuts else a
        err = abs((first if first is not None else -10 ** 6) - ca)
        # where the item is PLANNED to end on a cut, its last changed frame
        # must be the one before that cut
        cb = min(cuts, key=lambda c: abs(c - b)) if cuts else None
        if cb is not None and abs(cb - b) <= 1:
            err = max(err, abs((last + 1 if last is not None else -10 ** 6)
                               - cb))
        det[it["id"]] = {"first": first, "last": last, "cut_in": ca,
                         "cut_out": cb, "err": err}
        worst = max(worst, err)
    table = _metric_table(case)
    metrics = _pinned(case, run, fps)
    if "section_edges_on_cuts" in table:
        metrics["section_edges_on_cuts"] = _result(
            worst, _bar(table["section_edges_on_cuts"]),
            {"cuts": cuts, "items": det})
    bd = board_dir(case["id"])
    rows = []
    for it in run["edl"]["fx"]:
        a = int(math.floor(float(it["start"]) * fps + 0.5))
        b = int(math.floor(float(it["end"]) * fps + 0.5))
        m = (a + b) // 2
        c, r = frames(run["cpath"], [m])[m], frames(run["rpath"], [m])[m]
        lab = it.get("kind") or ("B&W" if it.get("saturation") == 0 else
                                 "grade")
        rows.append((f"{lab} [{it['start']:.2f}, {it['end']:.2f})",
                     [("control", _h(c, 300)), (lab, _h(r, 300))]))
    refs = []
    for r in case["reference"]:
        im = ref_still(r["ref"], r.get("board_t", r["t"][0]), 300)
        if im is not None:
            refs.append((f"{r['ref']}@{r.get('board_t', r['t'][0]):g}", im))
    rows.append(("references (internal)", refs))
    from . import boards
    clip = None
    board = sheet(rows, os.path.join(bd, "sections_board.png"))
    try:
        clip = boards.contact(run["rpath"], os.path.join(
            bd, "sections_contact.png"), n=12, cols=6, height=200)
    except Exception:  # noqa: BLE001
        clip = None
    return finish_verdict(case, cdir, render_path=run["rpath"],
                          control_path=run["cpath"], edl=run["edl"],
                          metrics=metrics, program_s=run["program_s"],
                          outro_s=run["outro_s"], wall_s=run["wall"],
                          board={"images": [board] + ([clip] if clip
                                                       else [])})


RUNNERS = {"AT-1E-1": at_1e_1, "AT-1E-2": at_1e_2, "AT-1E-3": at_1e_3,
           "AT-1E-4": at_1e_4, "AT-1E-5": at_1e_5, "AT-1E-6": at_1e_6,
           "AT-1E-7": at_1e_7}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv) or sorted(RUNNERS)
    ok = True
    for cid in argv:
        v = RUNNERS[cid](load(cid))
        ok &= v.get("pass") is not False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
