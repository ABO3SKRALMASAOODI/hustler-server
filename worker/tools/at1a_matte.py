"""Track 1A acceptance (ROADMAP Wave 1 §1A): the program matte on the
footage library. LOCAL and INTERNAL: renders the VFL fixtures (never
product storage), compares against the reference reels (internal only),
writes metrics JSON and boards under --out.

    MATTE_RVM_MODEL=... PYTHONPATH=.. python tools/at1a_matte.py \
        --out <dir> [--case AT-1A-1 ...] [--preview]

Every device is written as apply_edit_batch operations (edit_batch.
apply_batch), exactly as a stale MCP client would: the behind-subject
graphics carry no measured mask; edit_batch names the program matte and
the renderer draws them through it.

Per case the runner measures the matte.person sidecars of the window on
the very file it renders (local launcher, the pinned RVM), renders the
program (and a "matte view" of the same program: every behind-subject
composite replaced by its mask laid over the picture), and reports:

  fallbacks          behind-subject items the render drew in front (or as
                     their face-safe slam) instead of behind the subject
  occlusion_iou      per item: IoU of the glyph pixels the render HIDES and
                     the glyph pixels under the program matte (> 0.5)
  matte_flicker      qa_metrics.matte_flicker on the program matte inside
                     each item's window (6 px band, at render resolution)
  edge_chatter       qa_metrics.edge_chatter on the same frames
  cut_switch         (cases with a cut) the matte's frame-to-frame change
                     at the cut frame vs the picture's
"""

import argparse
import copy
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

THIEL = ("/Users/masaoodi/Documents/Valmera/.valmera/fixtures/baselines/v5/"
         "thiel_final8.json")


# ── setup: the window, its sidecars, its index ───────────────────────────

def setup(vfl_id, name, edl_src, out, ranges=None):
    """(edl on the render file's clock, render file, index, info, timings)
    with the window's matte.person sidecars measured on that file."""
    import matte_track
    import media
    import scenes
    import sidecars
    from benchmarks.refbench import vfl as vflmod
    w = vflmod.window(vfl_id, name)
    edl_f, path, q, nudged, mode = vflmod.render_source(w, edl_src)
    info = media.probe(path)
    sha = media.sha256_file(path)
    shots = [s.start if hasattr(s, "start") else s.get("start")
             for s in scenes.detect_shots(path, float(info["duration"]))]
    shots = [float(s) for s in shots if s and float(s) > 0.0]
    src = sidecars.source_from(sha, path, info, 0, shots)
    if ranges is None:
        ranges = [(max(0.0, a - 1.0), min(src.duration_s, b + 1.0))
                  for a, b in edl_f["keep"]]
    t0 = time.time()
    h = sidecars.ensure("matte.person", src, ranges,
                        params=matte_track.sidecar_params())
    sidecars._launcher().join()
    wall = round(time.time() - t0, 1)
    index = {"words": [], "shots": [{"start": s} for s in shots],
             "video": {"width": info["width"], "height": info["height"],
                       "fps": info["fps"], "duration": info["duration"],
                       "sha256": sha}}
    metas = []
    for c in h.chunks:
        st, m, _f = sidecars._states_one(h, c)
        metas.append({"t0": c.t0, "t1": c.t1, "status": st,
                      "frames": (m or {}).get("frames"),
                      "ms_per_frame": (m or {}).get("ms_per_frame"),
                      "cpu_ms_per_frame": (m or {}).get("cpu_ms_per_frame"),
                      "qa": {k: v for k, v in ((m or {}).get("qa")
                                               or {}).items()
                             if k != "coverage"}})
    return {"edl": edl_f, "path": path, "index": index, "info": info,
            "q": q, "mode": mode, "shots": shots, "sidecar_wall_s": wall,
            "chunks": metas, "window": w}


def apply_ops(edl, ops, duration):
    import edit_batch
    from schemas import validate_edl
    base = validate_edl(edl, duration).model_dump()
    return edit_batch.apply_batch(base, ops, duration, set())


# ── renders ──────────────────────────────────────────────────────────────

def render(S, edl, out, *, preview=False, matte_view=False, live=True):
    """(render path, wall s, motion warnings, placements)."""
    import matte_track
    import motion_layer
    import renderer
    from benchmarks.refbench import ink
    from schemas import validate_edl
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    saved = motion_layer.composite_behind
    saved_live = matte_track.LIVE
    if matte_view:
        def _show(parts, vlabel, mask_label, j, motion=None, **kw):
            # each item's mask over the picture inside its own window only
            en = ""
            if motion:
                it = motion[1]
                en = (f":enable='gte(t,{float(it['start']):.4f})*"
                      f"lt(t,{float(it['end']):.4f})'")
            parts.append(f"[{mask_label}]format=yuv420p[mv{j}m]")
            parts.append(f"[{vlabel}][mv{j}m]overlay=0:0:eof_action=pass"
                         f"{en}[mv{j}]")
            return f"mv{j}"
        motion_layer.composite_behind = _show
    matte_track.LIVE = live
    warnings = []
    tok = motion_layer.collect_warnings(warnings)
    saved_run = renderer._render_media_run
    if matte_view:
        # the matte is measured from a LOSSLESS encode (the codec's edge
        # noise is not the matte's)
        def _lossless(cmd, **kw):
            cmd = list(cmd)
            if "-crf" in cmd:
                cmd[cmd.index("-crf") + 1] = "0"
            for flag in ("-maxrate", "-bufsize"):
                while flag in cmd:
                    i = cmd.index(flag)
                    del cmd[i:i + 2]
            return saved_run(cmd, **kw)
        renderer._render_media_run = _lossless
    t0 = time.time()
    try:
        with ink.capture_overlays() as placed:
            e = validate_edl(edl, float(S["info"]["duration"])).model_dump()
            renderer.render_edl(e, S["index"], S["path"], out, work,
                                preview)
    finally:
        renderer._render_media_run = saved_run
        motion_layer.stop_collecting(tok)
        motion_layer.composite_behind = saved
        matte_track.LIVE = saved_live
    return out, round(time.time() - t0, 1), warnings, placed


def fallbacks(warnings):
    return [w for w in warnings
            if "instead of behind" in w or "not drawn" in w
            or "display slam above the picture" in w]


# ── metrics ──────────────────────────────────────────────────────────────

def _frames(path, frames, gray=False):
    import qa_metrics
    idx, fr = qa_metrics.read_frames(path, frames=set(frames), gray=gray)
    return dict(zip(idx, fr))


def occlusion(render_path, matte_path, rec, fps, step=2):
    """Per-item: IoU of the glyph pixels the render hides and the glyph
    pixels under the program matte (> 0.5), pooled over the window's
    frames; plus the per-frame values and the item's visible share."""
    from benchmarks.refbench import ink
    f0 = int(np.ceil(rec["start"] * fps)) + 2
    f1 = int(np.floor(rec["end"] * fps)) - 2
    frames = list(range(f0, f1, step))
    R = _frames(render_path, frames)
    M = _frames(matte_path, frames, gray=True)
    inter = union = 0
    per, vis = [], []
    for f in frames:
        if f not in R or f not in M:
            continue
        H, W = R[f].shape[:2]
        g = ink.glyphs(rec, f, fps, (H, W))
        if g is None:
            continue
        glyph, shown = ink.visible(R[f], g)
        if glyph.sum() < 200:
            continue
        hidden = glyph & ~shown
        m = M[f]
        m = (m[:, :, 0] if m.ndim == 3 else m) > 127
        expect = glyph & m
        i = int((hidden & expect).sum())
        u = int((hidden | expect).sum())
        inter += i
        union += u
        per.append(round(i / u, 4) if u else 1.0)
        vis.append(round(float(shown.sum()) / float(glyph.sum()), 4))
    return {"iou": round(inter / union, 4) if union else None,
            "frames": len(per), "per_frame_min": min(per) if per else None,
            "visible_share_mean": round(float(np.mean(vis)), 4)
            if vis else None}


def matte_quality(matte_path, rec, fps, render_path=None):
    """matte_flicker / edge_chatter of the program matte in an item's
    window (frames of the matte-view render, gray, 0..1).

    ``matte_flicker`` is qa_metrics' number over every edge pixel, which on
    a talking, gesturing speaker is mostly the speaker MOVING.
    ``matte_flicker_static`` is the ROADMAP §4.5 protocol ("on a static
    subject"): the same mean |delta alpha| in the same 6 px band, counted
    only where the picture itself did not change between the two frames
    (|delta luma| < 6) and never across a picture cut — the matte changing
    where the footage did not."""
    import qa_metrics
    f0 = int(np.ceil(rec["start"] * fps)) + 1
    f1 = int(np.floor(rec["end"] * fps)) - 1
    M = _frames(matte_path, range(f0, f1), gray=True)
    ks = sorted(M)
    masks = [(M[f][:, :, 0] if M[f].ndim == 3 else M[f]).astype(np.float32)
             / 255.0 for f in ks]
    fl = qa_metrics.matte_flicker(masks=masks)
    ch = qa_metrics.edge_chatter(masks=masks, fps=fps)
    out = {"matte_flicker": round(float(fl["value"]), 5)
           if fl["value"] is not None else None,
           "edge_chatter": round(float(ch["value"]), 6)
           if ch["value"] is not None else None, "frames": len(masks)}
    if render_path:
        R = _frames(render_path, ks, gray=True)
        vals = []
        for (a, ma), (b, mb) in zip(zip(ks, masks), zip(ks[1:], masks[1:])):
            if a not in R or b not in R:
                continue
            ra = (R[a][..., 0] if R[a].ndim == 3 else R[a]).astype(np.int16)
            rb = (R[b][..., 0] if R[b].ndim == 3 else R[b]).astype(np.int16)
            if float(np.abs(ra - rb).mean()) > 20:      # a picture cut
                continue
            band = (qa_metrics._dilate(ma >= .5, 3)
                    & ~qa_metrics._erode(ma >= .5, 3))
            sel = band & (np.abs(ra - rb) < 6)
            if sel.sum() > 100:
                vals.append(float(np.abs(ma - mb)[sel].mean()))
        out["matte_flicker_static"] = round(float(np.mean(vals)), 5) \
            if vals else None
    return out


# ── boards ───────────────────────────────────────────────────────────────

def board(cdir, name, ours, ref_id, pairs, title, footer=()):
    """A grid: our frames over the reference's (internal only)."""
    from PIL import Image
    from benchmarks.refbench import boards, reference_path
    rp = reference_path(ref_id)
    H = 480
    tiles = []
    for t_ours, t_ref in pairs:
        a = boards.grab(ours, t_ours, H)
        b = boards.grab(rp, t_ref, H) if rp else np.zeros_like(a)
        tiles.append((a, b, t_ours, t_ref))
    wa = max(t[0].shape[1] for t in tiles)
    wb = max(t[1].shape[1] for t in tiles)
    gap = 12
    Wd = gap + len(tiles) * (max(wa, wb) + gap)
    Hd = 70 + 2 * (H + 40) + 40 + 26 * max(1, len(footer))
    img = Image.new("RGB", (Wd, Hd), boards.BG)
    boards._caption(img, [title], 10, x=gap, size=24, color=boards.ACCENT)
    for k, (a, b, ta, tb) in enumerate(tiles):
        x = gap + k * (max(wa, wb) + gap)
        boards._caption(img, [f"Valmera @{ta:.2f}s"], 44, x=x, size=16)
        img.paste(Image.fromarray(a), (x, 66))
        boards._caption(img, [f"ref {ref_id} @{tb:.2f}s (internal)"],
                        66 + H + 8, x=x, size=16)
        img.paste(Image.fromarray(b), (x, 66 + H + 34))
    boards._caption(img, list(footer), 66 + 2 * (H + 40), x=gap, size=18)
    p = os.path.join(cdir, f"{name}.png")
    img.save(p)
    return p


def strip(path, t0, t1, out, n=8, height=360):
    """n frames of [t0, t1] side by side."""
    from PIL import Image
    from benchmarks.refbench import boards
    ts = [t0 + (t1 - t0) * k / max(1, n - 1) for k in range(n)]
    frames = [boards.grab(path, t, height) for t in ts]
    w = frames[0].shape[1]
    img = Image.new("RGB", (n * (w + 6) + 6, height + 40), boards.BG)
    for k, (t, f) in enumerate(zip(ts, frames)):
        img.paste(Image.fromarray(f), (6 + k * (w + 6), 34))
        boards._caption(img, [f"{t:.2f}s"], 6, x=8 + k * (w + 6), size=16)
    img.save(out)
    return out


# ── the cases ────────────────────────────────────────────────────────────

def _hero(mid, text, start, end, y=0.3, entrance="slam", **params):
    return {"action": "upsert", "layer": "motion", "id": mid, "value": {
        "id": mid, "template": "word_slam", "start": start, "end": end,
        "layer": "behind_subject",
        "params": dict({"text": text, "tier": "hero", "y": y,
                        "entrance": entrance}, **params)}}


def case_1a1(out, preview):
    """AT-1A-1: V02 840.47-880.51 as the 9:16 follow-cropped SC-Thiel
    short (the v5 framing: 7 kept spans, a crop that follows the speaker),
    three giant lockups behind the head written as apply_edit_batch ops.
    Wave 0 drew every one in front (the follow crop)."""
    base = json.load(open(THIEL))
    base = base.get("edl", base)
    src_edl = {"keep": base["keep"], "frame": base["frame"]}
    S = setup("V02", "thiel_flying_cars", src_edl, out)
    ops = [_hero("h1", "TECHNOLOGY", 1.0, 4.2, y=0.27),
           _hero("h2", "BITS", 19.5, 22.4, y=0.27),
           _hero("h3", "FLYING CARS", 30.0, 32.6, y=0.27)]
    dur = float(S["info"]["duration"])
    edl = apply_ops(S["edl"], ops, dur)
    return S, edl, {"items": ["h1", "h2", "h3"], "ref": "05",
                    "ref_pairs": [(2.6, 0.5), (20.8, 4.0), (31.6, 9.0)]}


def case_1a2(out, preview):
    """AT-1A-2: V05 43.69-74.44, a two-camera interview with camera cuts
    inside the kept span; a word behind the head across a cut (12@12.875's
    word carried across the cut)."""
    src_edl = {"keep": [[43.69, 74.44]],
               "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5,
                         "focus_y": 0.4}}
    S = setup("V05", "welt_cut", src_edl, out)
    # the first camera cut a second or more inside the kept span
    k0, k1 = S["edl"]["keep"][0]
    cuts = [c for c in S["shots"] if k0 + 1.5 < c < k1 - 2.0]
    cut_prog = round(cuts[0] - k0, 2) if cuts else 6.0
    ops = [_hero("h1", "FREEDOM", round(cut_prog - 1.6, 2),
                 round(cut_prog + 1.8, 2), y=0.3, entrance="pop")]
    edl = apply_ops(S["edl"], ops, float(S["info"]["duration"]))
    return S, edl, {"items": ["h1"], "ref": "12",
                    "ref_pairs": [(cut_prog - 0.3, 12.8),
                                  (cut_prog + 0.3, 13.2)],
                    "cut_prog": cut_prog, "cuts_src": cuts,
                    "labels": ("V05", "welt_cut")}


def case_1a3(out, preview):
    """AT-1A-3: V01 149.44-187.0, a push 1.00 -> 1.26 the user asked for
    and a giant word SCREEN-LOCKED behind the head (composite.stage
    'screen': after the camera, through the screen-space matte). Wave 0's
    behind graphics rode the zoom."""
    # the head whole in the 9:16 crop, looking into the frame's left
    # third: the word's wall
    src_edl = {"keep": [[150.4, 162.4]],
               "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.46,
                         "focus_y": 0.4}}
    S = setup("V01", "thiel_ai_bigger", src_edl, out)
    dur = float(S["info"]["duration"])
    zoom = {"action": "set", "layer": "effects", "value": {
        "zooms": [{"id": "z1", "start": 1.0, "end": 9.3, "mode": "push_in",
                   "strength": 0.26}]}}
    hero = _hero("h1", "BIGGER", 1.0, 9.3, y=0.13, entrance="pop",
                 role="condensed")
    hero["value"]["composite"] = {"stage": "screen"}
    world = _hero("h2", "BIGGER", 1.0, 9.3, y=0.13, entrance="pop",
                  role="condensed")
    edl = apply_ops(S["edl"], [zoom, hero], dur)
    edl_world = apply_ops(S["edl"], [zoom, world], dur)
    return S, edl, {"items": ["h1"], "ref": "05",
                    "ref_pairs": [(1.5, 1.2), (5.0, 5.0), (9.0, 9.0)],
                    "world_edl": edl_world, "drift_item": "h1"}


def case_1a4(out, preview):
    """AT-1A-4: V08 840-870, a close-up whose subject fills 60-70% of the
    frame — refused by Wave 0's coverage cap; 09's continuous lockups."""
    src_edl = {"keep": [[842.0, 856.0]],
               "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5,
                         "focus_y": 0.4}}
    S = setup("V08", "garry_closeup", src_edl, out)
    # a close-up leaves the frame's top corners as the only wall: the
    # words sit high, the hair crossing their lower half (05's lockups)
    ops = [_hero("h1", "FOUNDERS", 0.6, 4.8, y=0.13, entrance="pop"),
           _hero("h2", "BUILD", 5.4, 9.6, y=0.13, entrance="pop")]
    edl = apply_ops(S["edl"], ops, float(S["info"]["duration"]))
    return S, edl, {"items": ["h1", "h2"], "ref": "09",
                    "ref_pairs": [(2.0, 2.0), (7.0, 8.0)],
                    "labels": ("V08", "garry_closeup"), "portrait": True}


JOBS = ("/Users/masaoodi/Documents/Valmera/.valmera/fixtures/baselines/v5/"
        "jobs_final8.json")


def case_1a5(out, preview):
    """AT-1A-5: V03 1691.66-1783.84 (480p archival) inside the v5 picture
    card (source rect, follow): a word behind Jobs inside the card — the
    matte maps through the card's source rect. Wave 0: 'a source-fed
    picture card re-frames its footage'."""
    base = json.load(open(JOBS))
    base = base.get("edl", base)
    src_edl = {"keep": base["keep"][:4], "frame": base["frame"],
               "effects": {"picture_cards": base["effects"]
                           ["picture_cards"]}}
    S = setup("V03", "jobs_fonts", src_edl, out)
    prog = sum(b - a for a, b in S["edl"]["keep"])
    for c in S["edl"]["effects"]["picture_cards"]:
        c["end"] = round(min(float(c["end"]), prog), 2)
    ops = [_hero("h1", "FONTS", 1.0, 5.6, y=0.42, entrance="pop",
                 width=0.7)]
    edl = apply_ops(S["edl"], ops, float(S["info"]["duration"]))
    return S, edl, {"items": ["h1"], "ref": "35",
                    "ref_pairs": [(2.0, 2.0), (4.5, 6.0)],
                    "labels": ("V03", "jobs_fonts")}


CASES = {"AT-1A-1": case_1a1, "AT-1A-2": case_1a2, "AT-1A-3": case_1a3,
         "AT-1A-4": case_1a4, "AT-1A-5": case_1a5}


def label_iou(S, vfl_id, name):
    """matte_iou (qa_metrics, person bar 0.92) of the sidecar chunks the
    render read against the window's person labels (source space, label
    void excluded), the firming curve applied as the renderer applies it."""
    import cv2
    import matte_track
    import qa_metrics
    import sidecars
    from benchmarks.refbench import labels
    man = labels.read_manifest(vfl_id, name, "person")
    if not man:
        return None
    src = sidecars.source_from(S["index"]["video"]["sha256"], S["path"],
                               S["info"], 0, S["shots"])
    rows = sidecars.ready_chunks("matte.person", src.sha256, src.shots,
                                 src.duration_s, [(0.0, src.duration_s)],
                                 params=matte_track.sidecar_params())
    h = matte_track.read_handle("matte.person", src,
                                [(c.t0, c.t1) for c, _m in rows],
                                matte_track.sidecar_params())
    files = sidecars.fetch(h, os.path.join(os.path.dirname(S["path"]),
                                           "lbl_chunks"))
    fs = float(man.get("file_start") or 0.0)
    pred, truth, void = {}, {}, {}
    lut = np.clip(((np.arange(256) / 255.0) - matte_track.FIRM_LO)
                  / (matte_track.FIRM_HI - matte_track.FIRM_LO), 0, 1)
    lut = lut * lut * (3 - 2 * lut)
    for fr, meta in sorted(man["frames"].items()):
        t = float(meta["file_time"]) - fs
        f = next((x for x in files if x["t0"] - 1e-6 <= t < x["t1"]), None)
        if f is None:
            continue
        fm = float(f["meta"]["fps"])
        j = int(round((t - f["t0"]) * fm))
        _i, mk = qa_metrics.read_frames(f["path"], frames={j}, gray=True)
        if not mk:
            continue
        m = mk[0][:, :, 0] if mk[0].ndim == 3 else mk[0]
        lab = labels.read_mask(vfl_id, name, int(fr))
        if lab is None:
            continue
        m = cv2.resize(m, (lab.shape[1], lab.shape[0]),
                       interpolation=cv2.INTER_LINEAR)
        pred[int(fr)] = lut[m]
        truth[int(fr)] = lab
        v = labels.read_mask(vfl_id, name, int(fr), label="person_void")
        if v is not None:
            void[int(fr)] = v
    if not pred:
        return None
    r = qa_metrics.matte_iou(pred=pred, truth=truth, void=void or None)
    return {"matte_iou": round(float(r["value"]), 4),
            "min": round(float(r["detail"].get("min") or 0), 4)
            if r.get("detail") else None, "frames": len(pred)}


def cut_switch(render_path, matte_path, cut_prog, fps, reach=6):
    """The frame the picture cuts on (largest frame difference near the
    cut) and the frame the matte switches on (largest 1 - IoU change)."""
    c = int(round(cut_prog * fps))
    want = list(range(c - reach - 1, c + reach + 1))
    R = _frames(render_path, want, gray=True)
    M = _frames(matte_path, want, gray=True)
    dp, dm = {}, {}
    for f in want[1:]:
        if f in R and f - 1 in R:
            dp[f] = float(np.abs(R[f].astype(np.int16)
                                 - R[f - 1].astype(np.int16)).mean())
        if f in M and f - 1 in M:
            a = (M[f][..., 0] if M[f].ndim == 3 else M[f]) > 127
            b = (M[f - 1][..., 0] if M[f - 1].ndim == 3 else M[f - 1]) > 127
            u = (a | b).sum()
            dm[f] = 1.0 - (float((a & b).sum()) / u if u else 1.0)
    fp = max(dp, key=dp.get) if dp else None
    fmm = max(dm, key=dm.get) if dm else None
    srt = sorted(dm.values(), reverse=True)
    return {"picture_cut_frame": fp, "matte_switch_frame": fmm,
            "matte_change_at_cut": round(dm.get(fp, 0.0), 4),
            "matte_change_next_largest": round(srt[1], 4)
            if len(srt) > 1 else None,
            "picture_diff_at_cut": round(dp.get(fp, 0.0), 2)}


def glyph_drift(render_path, matte_path, rec, fps, step=3, reach=4):
    """Per frame, the (dx, dy) that best re-registers the item's unhidden
    glyph pixels on the render (0, 0 = the type has not moved from where
    its clip was placed). Returns the max |offset| in px."""
    from benchmarks.refbench import ink
    f0 = int(np.ceil(rec["start"] * fps)) + 3
    f1 = int(np.floor(rec["end"] * fps)) - 3
    frames = list(range(f0, f1, step))
    R = _frames(render_path, frames)
    M = _frames(matte_path, frames, gray=True)
    worst, offs = 0, []
    for f in frames:
        if f not in R or f not in M:
            continue
        H, W = R[f].shape[:2]
        g = ink.glyphs(rec, f, fps, (H, W))
        if g is None:
            continue
        m = (M[f][..., 0] if M[f].ndim == 3 else M[f]) > 60
        from qa_metrics import _erode, _dilate
        sel = _erode(g["alpha"] >= 250, 2) & ~_dilate(m, 4)
        ys, xs = np.nonzero(sel)
        if len(xs) < 300:
            continue
        col = g["rgb"][ys, xs].astype(np.int16)
        best = None
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                yy = np.clip(ys + dy, 0, H - 1)
                xx = np.clip(xs + dx, 0, W - 1)
                e = float(np.abs(R[f][yy, xx].astype(np.int16) - col)
                          .mean())
                if best is None or e < best[0]:
                    best = (e, dx, dy)
        offs.append((f, best[1], best[2]))
        worst = max(worst, abs(best[1]), abs(best[2]))
    return {"max_px": worst, "frames": len(offs),
            "nonzero": [o for o in offs if o[1] or o[2]][:10]}


def glyph_drift_vs(render_path, control_path, matte_path, rec, fps, step=3,
                   reach=4):
    """Per frame, the (dx, dy) that best registers the render's type band
    (glyphs + anti-aliased edges + 2 px, the subject excluded) on the
    CONTROL — the same graphic drawn in front, i.e. screen-locked by
    construction. Flat glyph interiors make an absolute match ambiguous
    by the erosion (the control itself reads +-2 px against its own
    clip); against the control 0 means the type sits exactly where a
    screen overlay sits."""
    from benchmarks.refbench import ink
    from qa_metrics import _dilate
    f0 = int(np.ceil(rec["start"] * fps)) + 3
    f1 = int(np.floor(rec["end"] * fps)) - 3
    frames = list(range(f0, f1, step))
    R = _frames(render_path, frames)
    C = _frames(control_path, frames)
    M = _frames(matte_path, frames, gray=True)
    worst, offs = 0, []
    for f in frames:
        if f not in R or f not in C or f not in M:
            continue
        H, W = R[f].shape[:2]
        g = ink.glyphs(rec, f, fps, (H, W))
        if g is None:
            continue
        m = (M[f][..., 0] if M[f].ndim == 3 else M[f]) > 60
        sel = _dilate(g["alpha"] >= 128, 2) & ~_dilate(m, 4 + reach)
        ys, xs = np.nonzero(sel)
        if len(xs) < 300:
            continue
        want = C[f][ys, xs].astype(np.int16)
        best = None
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                yy = np.clip(ys + dy, 0, H - 1)
                xx = np.clip(xs + dx, 0, W - 1)
                e = float(np.abs(R[f][yy, xx].astype(np.int16) - want)
                          .mean())
                if best is None or e < best[0] - 1e-9 or (
                        abs(e - best[0]) <= 1e-9
                        and abs(dx) + abs(dy) < abs(best[1]) + abs(best[2])):
                    best = (e, dx, dy)
        offs.append((f, best[1], best[2]))
        worst = max(worst, abs(best[1]), abs(best[2]))
    return {"max_px": worst, "frames": len(offs),
            "nonzero": [o for o in offs if o[1] or o[2]][:10]}


def run(case_id, out, preview):
    import qa_metrics
    cdir = os.path.join(out, case_id)
    os.makedirs(cdir, exist_ok=True)
    S, edl, spec = CASES[case_id](cdir, preview)
    res = {"case": case_id, "preview": preview, "mode": S["mode"],
           "sidecar_wall_s": S["sidecar_wall_s"], "chunks": S["chunks"]}
    rp, wall, warns, placed = render(S, edl, os.path.join(cdir, "render.mp4"),
                                     preview=preview)
    mp, wall_m, _w, _p = render(S, edl, os.path.join(cdir, "matte.mp4"),
                                preview=preview, matte_view=True)
    w0, wall0, warns0, _p0 = render(S, edl,
                                    os.path.join(cdir, "wave0.mp4"),
                                    preview=preview, live=False)
    # the control: the same graphics drawn IN FRONT (no matte at all), so
    # the wall-time ratio is the matte's cost, not the graphics'
    ctrl = copy.deepcopy(edl)
    for m in ctrl.get("motion") or []:
        if m.get("layer") == "behind_subject":
            m["layer"] = "above_captions"
            m.pop("behind", None)
            m.pop("composite", None)
    cp, wall_c, _wc, _pc = render(S, ctrl, os.path.join(cdir, "control.mp4"),
                                  preview=preview)
    fps = float(qa_metrics.probe(rp)["fps"])
    res.update(render_wall_s=wall, control_wall_s=wall_c,
               cost_ratio=round(wall / wall_c, 3) if wall_c else None,
               wave0_wall_s=wall0,
               fallbacks=fallbacks(warns), wave0_fallbacks=fallbacks(warns0),
               items={})
    for iid in spec["items"]:
        rec = placed.get(iid)
        if rec is None:
            res["items"][iid] = {"error": "not overlaid"}
            continue
        r = occlusion(rp, mp, rec, fps)
        r.update(matte_quality(mp, rec, fps, rp))
        res["items"][iid] = r
    if spec.get("drift_item"):
        iid = spec["drift_item"]
        rec, crec = placed.get(iid), _pc.get(iid)
        if rec and crec:
            res["items"][iid]["glyph_drift"] = glyph_drift_vs(
                rp, cp, mp, rec, fps)
            # the absolute read, on the render and on the control (the
            # flat-glyph ambiguity: the control is screen-locked)
            res["items"][iid]["glyph_drift_abs"] = {
                "render": glyph_drift(rp, mp, rec, fps)["max_px"],
                "control": glyph_drift(cp, mp, crec, fps)["max_px"]}
    if spec.get("labels"):
        res["label_iou"] = label_iou(S, *spec["labels"])
    if spec.get("cut_prog") is not None:
        res["cut_switch"] = cut_switch(rp, mp, spec["cut_prog"], fps)
        res["cut_prog"] = spec["cut_prog"]
    if spec.get("world_edl") is not None:
        wp, _w, _ww, wplaced = render(S, spec["world_edl"],
                                      os.path.join(cdir, "world.mp4"),
                                      preview=preview)
        wmp, _w2, _x, _y = render(S, spec["world_edl"],
                                  os.path.join(cdir, "world_matte.mp4"),
                                  preview=preview, matte_view=True)
        wrec = wplaced.get("h2")
        if wrec:
            res["world_stage_drift"] = glyph_drift(wp, wmp, wrec, fps,
                                                   reach=40)
    if spec.get("portrait"):
        res["write_path"] = portrait_write(S, spec)
    pairs = spec.get("ref_pairs") or []
    res["boards"] = {
        "vs_reference": board(cdir, "board", rp, spec["ref"], pairs,
                              f"{case_id}: program matte behind the head "
                              "(top) vs reference (bottom)",
                              footer=[f"fallbacks {len(res['fallbacks'])} "
                                      f"(Wave 0: {len(res['wave0_fallbacks'])})"]),
        "wave0": board(cdir, "board_wave0", w0, spec["ref"], pairs,
                       f"{case_id}: the same EDL rendered the Wave 0 way"),
    }
    for iid in spec["items"]:
        rec = placed.get(iid)
        if rec:
            res["boards"][f"strip_{iid}"] = strip(
                rp, rec["start"] + 0.1, rec["end"] - 0.1,
                os.path.join(cdir, f"strip_{iid}.png"))
            res["boards"][f"matte_{iid}"] = strip(
                mp, rec["start"] + 0.1, rec["end"] - 0.1,
                os.path.join(cdir, f"matte_{iid}.png"))
    with open(os.path.join(cdir, "result.json"), "w") as fh:
        json.dump(res, fh, indent=1)
    return res


def portrait_write(S, spec):
    """The write path on the close-up: matte.measure_and_build on the
    window as add_text_behind measures it — refused for its coverage in
    Wave 0, kept (portrait) in Wave 1 and judged by the words' visible
    share."""
    import matte
    import renderer
    a, b = S["edl"]["keep"][0]
    fr = S["edl"].get("frame") or {}
    # the output framing, at proxy height: agent_tools._matte_geometry
    W, H = renderer.frame_dims(float(S["info"]["width"]),
                               float(S["info"]["height"]), fr.get("ratio"))
    k = 540.0 / float(H)
    mw, mh = int(round(W * k / 2)) * 2, int(round(H * k / 2)) * 2
    fit = renderer.frame_fit_filter(
        fr.get("mode") or "crop", mw, mh,
        (fr.get("focus_x"), fr.get("focus_y")), pad_color="black")
    out = {}
    for portrait in (False, True):
        p = os.path.join(os.path.dirname(S["path"]),
                         f"portrait_{int(portrait)}.mp4")
        st = matte.measure_and_build(S["path"], p, a + 1.0, 4.0,
                                     box=(0.08, 0.06, 0.84, 0.14),
                                     extra_vf=fit, width=mw, height=mh,
                                     allow_portrait=portrait)
        out["wave1" if portrait else "wave0"] = {
            k: st.get(k) for k in ("ok", "coverage", "coverage_max",
                                   "text_visible_mean", "portrait",
                                   "engine")} | ({"why": st.get("why")[:90]}
                                                 if st.get("why") else {})
    return out


def perf_sidecars(out, trial, params_list=(None, {"downsample_ratio": 0.375}),
                  workers=3, threads=4):
    """AT-1A-perf (1): the 60 s sidecar. AT-0A-1's footage (V01 149.44-187.0
    plus 60 s of V02 as the 960x540 proxies the production job reads),
    measured cold on ``workers`` launcher slots of ``threads`` ONNX threads
    each (a standard-4 shard each), per params set. The production
    projection scales the measured 128 ms/frame (config.py, standard-4) by
    the local CPU-ms ratio of the option to the default."""
    import config
    import media
    import scenes
    import sidecars
    config.SIDECAR_ORT_THREADS = threads
    fx = os.environ.get("VALMERA_FIXTURES_DIR") or os.path.expanduser(
        "~/Documents/Valmera/.valmera/fixtures")
    srcs = []
    for f in ("V01_ai_bigger.mp4", "V02_flying_cars_60s.mp4"):
        p = os.path.join(fx, "w0a", "proxies", f)
        info = media.probe(p)
        shots = [s.start if hasattr(s, "start") else s.get("start")
                 for s in scenes.detect_shots(p, float(info["duration"]))]
        srcs.append(sidecars.source_from(media.sha256_file(p), p, info, 0,
                                         [x for x in shots if x and x > 0]))
    res = {}
    for params in params_list:
        tag = json.dumps(params or {}, sort_keys=True)
        sidecars.use_launcher(sidecars._Launcher(workers=workers))
        t0 = time.time()
        hs = [sidecars.ensure("matte.person", s, [(0.0, s.duration_s)],
                              params=dict(params or {}, trial=trial))
              for s in srcs]
        sidecars._launcher().join()
        wall = time.time() - t0
        frames, cpu, ms = 0, [], []
        groups = []
        for h in hs:
            for c in h.chunks:
                st, m, _f = sidecars._states_one(h, c)
                m = m or {}
                n = int(m.get("frames") or 0) + int(round(
                    float(m.get("warmup_s") or 0) * float(m.get("fps") or 0)))
                frames += n
                groups.append(n)
                if m.get("cpu_ms_per_frame"):
                    cpu.append(float(m["cpu_ms_per_frame"]))
                    ms.append(float(m["ms_per_frame"]))
        loads = [0] * 3
        for n in sorted(groups, reverse=True):
            loads[loads.index(min(loads))] += n
        res[tag] = {"wall_s": round(wall, 1), "model_frames": frames,
                    "busiest_shard_frames": max(loads),
                    "cpu_ms_per_frame": round(float(np.mean(cpu)), 1)
                    if cpu else None,
                    "ms_per_frame": round(float(np.mean(ms)), 1)
                    if ms else None}
    base = res.get(json.dumps({}, sort_keys=True))
    for tag, r in res.items():
        if base and base.get("cpu_ms_per_frame") and r.get("cpu_ms_per_frame"):
            rate = 128.0 * r["cpu_ms_per_frame"] / base["cpu_ms_per_frame"]
            r["projected_ms_per_frame"] = round(rate, 1)
            r["projected_busiest_shard_s"] = round(
                r["busiest_shard_frames"] * rate / 1000.0, 1)
            r["projected_with_overhead_s"] = round(
                r["projected_busiest_shard_s"] + 20.0, 1)
    sidecars.use_launcher(None)
    return res


def perf_preview(out, reps=3):
    """AT-1A-perf (2): a 12 s window's preview with one word behind the
    head through the program matte vs the same word in front (matte
    measured already: the render's own cost)."""
    base = json.load(open(THIEL))
    base = base.get("edl", base)
    src_edl = {"keep": [[846.04, 853.0], [853.33, 856.83], [857.11, 859.0]],
               "frame": base["frame"]}
    S = setup("V02", "thiel_flying_cars", src_edl, out)
    dur = float(S["info"]["duration"])
    edl = apply_ops(S["edl"], [_hero("h1", "TECHNOLOGY", 0.5, 11.5,
                                     y=0.27)], dur)
    front = copy.deepcopy(edl)
    for m in front["motion"]:
        m["layer"] = "above_captions"
        m.pop("behind", None)
    walls = {"behind": [], "front": []}
    for k in range(reps):
        for name, e in (("front", front), ("behind", edl)):
            _p, w, _x, _y = render(S, e, os.path.join(out, f"pp_{name}_{k}"
                                                      ".mp4"), preview=True)
            walls[name].append(w)
    med = {k: float(np.median(v)) for k, v in walls.items()}
    return {"walls": walls, "median_s": med,
            "added_s": round(med["behind"] - med["front"], 2)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--case", action="append")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--trial", default=str(int(time.time())))
    a = ap.parse_args(argv)
    # 1C's composite slot (composite.stage 'screen', AT-1A-3) registered
    # in this process only (the preview switch; never production)
    os.environ.setdefault("VALMERA_FX_PREVIEW", "1c")
    if a.perf:
        pdir = os.path.join(a.out, "AT-1A-perf")
        os.makedirs(pdir, exist_ok=True)
        r = {"preview": perf_preview(pdir),
             "sidecars": perf_sidecars(pdir, a.trial)}
        with open(os.path.join(pdir, "result.json"), "w") as fh:
            json.dump(r, fh, indent=1)
        print(json.dumps(r, indent=1))
        return
    for cid in a.case or sorted(CASES):
        r = run(cid, a.out, a.preview)
        print(json.dumps({k: v for k, v in r.items()
                          if k not in ("chunks",)}, indent=1))


if __name__ == "__main__":
    main()
