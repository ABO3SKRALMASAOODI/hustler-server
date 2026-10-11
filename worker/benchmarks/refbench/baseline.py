"""The Wave 0 baseline board (ROADMAP "Wave 0 showcase").

Scores today's best output -- the three v5 showcase shorts -- against their
nearest references, on the same source windows every later wave re-renders:

    BASE-1  Thiel,      V02 840.47-880.51  vs 05@0.23-12.23, 13@0.2-17.7
    BASE-2  Jobs,       V03 1691.66-1783.84 vs 02@0-18.7, 35@~0-10.7
    BASE-3  Elon/Rogan, V04 115.5-160.1    vs 09@1.27-21.6

For each short:
  1. the v5 file itself (``render.use``) is profiled with refsig (cuts,
     punctuation, B&W, layout, text boxes, occluded text, flash rate) and
     the standing checks (loudness, owner rules, collisions);
  2. the v5 EDL (``<fixtures>/baselines/v5``) is re-rendered locally on the
     VFL fixture twice -- as shipped, and as a CONTROL with every motion
     graphic removed -- so each device's ink can be separated from the
     picture: legibility (APCA Lc) per graphic, landing error per word_slam
     against its spoken word, brand integrity vs the control. The local
     re-render is checked against the v5 file (PSNR) before it is trusted;
  3. each reference window gets the same signature;
  4. boards: 3-up stills (v5 | ref A | ref B), 4 s side-by-side clips,
     landing strips, a signature table, and board.json with every number.

    python -m benchmarks.refbench baseline BASE-1 BASE-2 BASE-3 \\
        --out ~/Documents/Valmera/.valmera/refbench/boards/wave0

Internal only (reference frames are on the boards).
"""

import copy
import json
import os
import re
import time

import numpy as np

import qa_metrics
import refsig

from . import boards, fixtures_dir, reference_path, spec, standing
from . import vfl as vflmod


# ── EDL clock shift (source seconds -> fixture file seconds) ─────────────

def shift_index(index, off, win):
    """The index on the fixture clock: words and the spatial samples
    (faces, burned-in text: what captions and keep-out place around) moved
    by the same offset as the EDL; samples outside the fixture dropped."""
    words = [{**w, "t0": round(w["t0"] - off, 3),
              "t1": round(w["t1"] - off, 3)}
             for w in index.get("words") or []]
    out = {k: v for k, v in index.items()
           if k not in ("words", "video", "spatial")}
    out["words"] = words
    out["video"] = {"width": win["width"], "height": win["height"],
                    "duration": round(win["file_span"][1]
                                      - win["file_span"][0], 3),
                    "fps": win["fps"]}
    sp = index.get("spatial")
    if isinstance(sp, dict) and isinstance(sp.get("samples"), list):
        span = win["file_span"][1] - win["file_span"][0]
        sp = dict(sp)
        sp["samples"] = [{**x, "t": round(float(x["t"]) - off, 3)}
                         for x in sp["samples"]
                         if 0.0 <= float(x["t"]) - off <= span]
        out["spatial"] = sp
    else:
        out["spatial"] = None
    return out


def _localise_inserts(edl, bdir, *, sfx_at_is_hit=False):
    """Map the showcase build's assets to local files; returns the
    renderer's asset_locals:
      - ``local:`` insert keys (freeze stills) -> the copies kept with the
        baseline;
      - owner-approved library SFX keys -> the library recording in the
        worker (``sound_library``), placed exactly as the showcase build
        placed it (``sfx_at_is_hit``: ``at`` is the hit, re-placed with
        ``sound_library.place``)."""
    locals_ = {}
    for ins in edl.get("inserts") or []:
        k = ins.get("asset_key") or ""
        if k.startswith("local:"):
            locals_[k] = os.path.join(bdir, os.path.basename(k[6:]))
    if edl.get("sfx"):
        import sound_library
        for s in edl["sfx"]:
            key = s.get("storage_key") or ""
            sid = sound_library.id_for_key(key)
            if not sid:
                raise RuntimeError(f"baseline sfx {key!r} is not an "
                                   "owner-approved library recording")
            locals_[key] = sound_library.path(sid)
            if sfx_at_is_hit:
                pl = sound_library.place(sid, s["at"], dur_s=s.get("dur_s"))
                s["at"] = pl["at"]
                if pl.get("offset_s"):
                    s["offset_s"] = pl["offset_s"]
                if pl.get("dur_s"):
                    s["dur_s"] = pl["dur_s"]
    return locals_


def render_pair(case, workdir):
    """Re-render the case's baseline EDL on its VFL fixture: (full, control,
    edl, index, win, wall, placements, clock). ``placements`` are the motion
    clips the renderer overlaid for the full render (``ink``), kept beside
    it as local_full.placements.json; ``clock`` is the fixture clock shift
    (``vfl.edl_to_fixture``). A render is reused only while its EDL is
    byte-identical (``<render>.edl.sha256``)."""
    import renderer
    from . import ink
    from schemas import validate_edl
    base = case["edl"]["base"].split(":", 1)[1]
    bdir = os.path.join(fixtures_dir(), "baselines", "v5")
    with open(os.path.join(bdir, base)) as fh:
        specd = json.load(fh)
    win = vflmod.window(*case["edl"]["window_ref"].split("/"))
    with open(os.path.join(bdir, case["edl"]["index"])) as fh:
        index = json.load(fh)
    edl, src_path, off, nudged, mode = vflmod.render_source(win,
                                                           specd["edl"])
    left = vflmod.unshifted(win, specd["edl"], edl)
    if left:
        raise RuntimeError(f"source-clock fields not moved to the fixture "
                           f"clock: {left[:8]}")
    asset_locals = _localise_inserts(
        edl, bdir, sfx_at_is_hit=bool(specd.get("sfx_at_is_hit", True)))
    idx = shift_index(index, off, win)
    dur = float(win["file_span"][1] - win["file_span"][0])
    edl = validate_edl(edl, dur).model_dump(exclude_none=True)
    control = copy.deepcopy(edl)
    control["motion"] = []
    os.makedirs(workdir, exist_ok=True)
    full_p = os.path.join(workdir, "local_full.mp4")
    ctrl_p = os.path.join(workdir, "local_control.mp4")
    pl_p = full_p[:-4] + ".placements.json"
    wall = {}
    import hashlib
    for name, e, p in (("full", edl, full_p), ("control", control, ctrl_p)):
        sig = hashlib.sha256(json.dumps(
            [e, os.path.basename(src_path), os.environ.get("FINAL_CRF")],
            sort_keys=True,
            default=str).encode()).hexdigest()
        sig_p = p + ".edl.sha256"
        cached = os.path.exists(p) and os.path.exists(sig_p) and \
            open(sig_p).read().strip() == sig
        if cached and (name != "full" or os.path.exists(pl_p)):
            wall[name] = None
            continue
        t = time.time()
        wd = os.path.join(workdir, f"w_{name}")
        os.makedirs(wd, exist_ok=True)
        with ink.capture_overlays() as placed:
            renderer.render_edl(e, idx, src_path, p, wd, preview=False,
                                want_wm=True, asset_locals=asset_locals)
        if name == "full":
            ink.save_placements(placed, pl_p)
        with open(sig_p, "w") as fh:
            fh.write(sig)
        wall[name] = round(time.time() - t, 1)
    clock = {"q": off, "file_t0": win["file_t0"], "mode": mode,
             "nudged": nudged}
    return (full_p, ctrl_p, edl, idx, win, wall,
            ink.load_placements(pl_p), clock)


def psnr_check(a, b, *, height=240, good_db=40.0):
    """Frame-by-frame PSNR (dB, at ``height`` px) of render ``a`` against
    ``b`` over the shorter file: mean, min, the share of frames >= good_db,
    the share that is identical and the frames below good_db."""
    ia = qa_metrics.probe(a)
    w = int(round(ia["width"] * height / ia["height"] / 2)) * 2
    _, fa = qa_metrics.read_frames(a, size=(w, height))
    _, fb = qa_metrics.read_frames(b, size=(w, height))
    vals = []
    for x, y in zip(fa, fb):
        mse = float(((x.astype(np.float64) - y.astype(np.float64)) ** 2)
                    .mean())
        vals.append(99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse))
    v = np.array(vals) if vals else np.array([0.0])
    return {"mean_db": round(float(v.mean()), 2),
            "min_db": round(float(v.min()), 2), "frames": len(vals),
            "frames_a": len(fa), "frames_b": len(fb),
            "share_ge_40db": round(float((v >= good_db).mean()), 4),
            "share_identical": round(float((v >= 99.0).mean()), 4),
            "frames_below_40db": [int(i) for i in np.nonzero(v < good_db)[0]
                                  ][:40]}


def _starred(text):
    m = re.findall(r"\*([^*]+)\*", text or "")
    first = (m[0] if m else (text or "")).split("/")[0].strip()
    tok = re.sub(r"[^\w%'-]", " ", first).split()
    return tok[0] if tok else None


def _norm(w):
    """Compare words without case, punctuation or '%' (ASR splits '32%'
    into '32' '%')."""
    return re.sub(r"[^\w]", "", (w or "").lower())


def device_metrics(full, edl, idx, placements, *, fps):
    """Per motion item, from the renderer's own clip of that item (``ink``):
    legibility (APCA Lc of each ink colour against the composited plate
    around it), glyph visibility, and for word-bound word_slams the landing
    error against the starred word's spoken onset."""
    from timeline import Timeline
    from . import ink
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    out = []
    for it in edl.get("motion") or []:
        s, e = float(it["start"]), float(it["end"])
        params = it.get("params") or {}
        rec = {"id": it["id"], "template": it["template"],
               "start": s, "end": e, "layer": it.get("layer"),
               "tier": params.get("tier"), "entrance": params.get("entrance"),
               "text": params.get("text")}
        place = placements.get(it["id"])
        if not place:
            rec["why"] = "not overlaid by the renderer (empty or demoted)"
            out.append(rec)
            continue
        mid = int(round((s + 0.6 * (e - s)) * fps))
        try:
            fr = dict(zip(*qa_metrics.read_frames(full, frames={mid})))[mid]
            m = ink.item_metrics(fr, place, mid, fps)
            if m and m.get("legibility"):
                lg = m["legibility"]
                rec["legibility_lc"] = round(lg["value"], 1)
                rec["legibility_pass"] = lg["pass"]
                rec["legibility_detail"] = lg["detail"]
            if m and m.get("glyph_visible"):
                rec["glyph_visible"] = round(m["glyph_visible"]["value"], 4)
            if m:
                rec["glyph_px"], rec["shown_px"] = m["glyph_px"], \
                    m["shown_px"]
                rec["measured_frame"] = mid
        except Exception as ex:
            rec["legibility_error"] = f"{ex.__class__.__name__}: {ex}"[:160]
        if it["template"] == "word_slam":
            word = _starred(params.get("text"))
            rec["target_word"] = word
            if params.get("tier") == "hook":
                rec["land_err_f"] = None
                rec["land_note"] = ("hook tier: the opening headline, not "
                                    "bound to a spoken word")
                out.append(rec)
                continue
            cand = []
            for w in idx.get("words") or []:
                t_out = tl.src_to_out(w["t0"])
                if t_out is None or not (s - 1.5 <= t_out <= e + 0.5):
                    continue
                if word and _norm(w["w"]) == _norm(word):
                    cand.append(t_out)
            if cand:
                tgt = min(cand, key=lambda t: abs(t - s))
                ev, det = ink.landing_frame(full, place, fps)
                le = qa_metrics.land_err_f(
                    events=[ev] if ev is not None else [],
                    targets=[int(round(tgt * fps))])
                rec["land_err_f"] = le["value"]
                rec["land_pairs"] = le["detail"]["pairs"]
                rec["word_onset_t"] = round(tgt, 3)
                rec["landing_frame"] = ev
                rec["landing_detail"] = {k: v for k, v in det.items()
                                         if k != "curve"}
            else:
                rec["land_err_f"] = None
                rec["land_note"] = "starred word not spoken near the item"
        out.append(rec)
    return out


SIG_KEYS = ("shots", "asl_s", "negatives", "white_flashes",
            "colour_flashes", "bw_frames", "strobe_sections",
            "type_on_black", "flash_rate_max_per_s")


def summarize_sig(sig, t0=None, t1=None):
    fps = sig["fps"]
    a = int(round((t0 or 0) * fps))
    b = int(round(t1 * fps)) if t1 else sig["frames"]
    n = max(1, b - a)
    dur = n / fps

    def cnt(fs):
        return len([f for f in fs if a <= f < b])
    cuts = cnt(sig["cuts"]["f"])
    tb = sig.get("text_boxes") or {}
    tframes = {int(k): v for k, v in (tb.get("frames") or {}).items()
               if a <= int(k) < b}
    words_on = sum(len(v) for v in tframes.values())
    occ = sig.get("occluded_text") or {}
    return {
        "window_s": [round(a / fps, 2), round(b / fps, 2)],
        "duration_s": round(dur, 2),
        "cuts": cuts, "cuts_per_10s": round(10 * cuts / dur, 2),
        "asl_s": round(dur / (cuts + 1), 2),
        "negative_frames": cnt(sig["negatives"]["f"]),
        "negative_runs": len([r for r in sig["negatives"]["runs"]
                              if a <= r[0] < b]),
        "white_flash_frames": cnt(sig["white_flashes"]["f"]),
        "colour_flash_frames": cnt(sig["colour_flashes"]["f"]),
        "bw_share": round(cnt(sig["bw"]["f"]) / n, 3),
        "strobe_sections": len([s for s in sig["strobe_sections"]
                                if a <= s["f"][0] < b]),
        "type_on_black_share": round(cnt(sig["type_on_black"]["f"]) / n, 3),
        "text_on_screen_share": (round(len([k for k, v in tframes.items()
                                            if v]) / max(1, len(tframes)), 3)
                                 if tframes else None),
        "text_boxes_per_sample": (round(words_on / max(1, len(tframes)), 2)
                                  if tframes else None),
        "occluded_text_samples": (len([f for f in occ.get("f", [])
                                       if a <= f < b])
                                  if occ.get("available") else None),
        "layout": sig["layout"]["layout"],
        "flash_rate_max_per_s": sig.get("flash_rate_max_per_s"),
    }


def signature(path, *, ocr=True, person=True, cache=None):
    """refsig.analyze of a file, cached beside the board while the cached
    signature's version is current and the file is the same."""
    sha = vflmod.sha256_file(path)
    if cache and os.path.exists(cache):
        with open(cache) as fh:
            sig = json.load(fh)
        if sig.get("version") == refsig.SIG_VERSION and \
                sig.get("sha256") == sha:
            return sig
    sig = refsig.analyze(path, ocr=ocr, person=person, ocr_every_s=0.5)
    sig["sha256"] = sha
    if cache:
        with open(cache, "w") as fh:
            json.dump(sig, fh)
    return sig


def _table_png(rows, cols, out, title):
    from PIL import Image, ImageDraw
    f = boards._font(20)
    fb = boards._font(22)
    cw = [300] + [230] * len(cols)
    W = sum(cw) + 40
    H = 80 + 34 * (len(rows) + 1)
    img = Image.new("RGB", (W, H), boards.BG)
    d = ImageDraw.Draw(img)
    d.text((20, 18), title, fill=boards.ACCENT, font=boards._font(26))
    x = 20
    for j, c in enumerate(["metric"] + cols):
        d.text((x, 62), str(c), fill=boards.ACCENT, font=fb)
        x += cw[j]
    for i, (name, vals) in enumerate(rows):
        y = 62 + 34 * (i + 1)
        if i % 2 == 0:
            d.rectangle([14, y - 4, W - 14, y + 28], fill=(30, 30, 34))
        x = 20
        d.text((x, y), name, fill=boards.FG, font=f)
        x += cw[0]
        for j, v in enumerate(vals):
            d.text((x, y), "—" if v is None else str(v), fill=boards.FG,
                   font=f)
            x += cw[j + 1]
    img.save(out)
    return out


def _three_up(items, out, *, height=640, title="", footer=()):
    """items: [(path, t, label)] -> one PNG, frames side by side."""
    from PIL import Image
    frames = [boards.grab(p, t, height) for p, t, _l in items]
    gap, top = 20, 72
    W = sum(f.shape[1] for f in frames) + gap * (len(frames) + 1)
    H = top + height + 30 + 28 * max(1, len(footer))
    img = Image.new("RGB", (W, H), boards.BG)
    boards._caption(img, [title], 12, x=gap, size=24, color=boards.ACCENT)
    x = gap
    for f, (_p, t, lab) in zip(frames, items):
        img.paste(Image.fromarray(f), (x, top))
        boards._caption(img, [f"{lab} @{t:.2f}s"], 44, x=x, size=17)
        x += f.shape[1] + gap
    boards._caption(img, list(footer), top + height + 10, x=gap, size=18)
    img.save(out)
    return out


def run(case_id, out_root, *, log=print):
    path = spec.find(case_id)
    case = spec.load(path)
    errs = spec.validate(case)
    if errs:
        raise SystemExit(f"{case_id}: {errs}")
    name = case["edl"]["name"]
    odir = os.path.join(out_root, name)
    work = os.path.join(odir, "work")
    os.makedirs(work, exist_ok=True)
    v5 = os.path.expanduser(case["render"]["use"])
    info = qa_metrics.probe(v5)
    fps = info["fps"]
    t0 = time.time()
    log(f"[{case_id}] signature of v5")
    sig_v5 = signature(v5, cache=os.path.join(work, "sig_v5.json"))
    import config
    outro = float(config.OUTRO_DURATION_S)
    program_s = round(info["duration"] - outro, 3)
    log(f"[{case_id}] local re-render (full + control)")
    full, ctrl, edl, idx, win, wall, placed, clock = render_pair(case, work)
    repro = psnr_check(full, v5)
    log(f"[{case_id}] reproduction PSNR vs v5 {repro}")
    devs = device_metrics(full, edl, idx, placed, fps=fps)
    # standing checks on the v5 file itself
    st = {}
    st["loudness"] = standing.loudness(v5, program_s)
    st["owner_rules"] = standing.owner_rules(edl, case.get("allow"),
                                             render=v5, program_s=program_s)
    # collisions are judged on the LOCAL re-render, where the control tells
    # Valmera's graphics apart from text burned into the footage
    st["no_collisions"] = standing.no_collisions(full, program_s=program_s,
                                                 control=ctrl)
    # v5 itself has no control render: brand integrity is measured on the
    # (lossless) local re-render against the same edit without graphics
    ok, det = standing.brand_integrity(
        full, ctrl, outro_s=outro,
        device_windows=[(d["start"], d["end"]) for d in devs])
    det["note"] = ("measured on the local re-render (reproduction above) "
                   "vs the same edit without motion graphics")
    st["brand_integrity"] = (ok, det)
    refs = []
    for r in case["reference"]:
        rp = reference_path(r["ref"])
        sig_r = signature(rp, cache=os.path.join(work,
                                                 f"sig_ref{r['ref']}.json"))
        refs.append({"ref": r["ref"], "t": r["t"], "approx": r.get("approx",
                                                                  False),
                     "path": rp, "sig": summarize_sig(sig_r, *r["t"])})
    v5_sum = summarize_sig(sig_v5, 0, program_s)
    # boards
    log(f"[{case_id}] boards")
    moments = case["board"]["stills"]
    paths = {"stills": [], "clips": [], "strips": []}
    for k, t in enumerate(moments):
        items = [(v5, float(t), "v5 (today)")]
        for r in refs:
            rt = r["t"][0] + (r["t"][1] - r["t"][0]) * (k + 0.5) / len(
                moments)
            items.append((r["path"], rt, f"ref {r['ref']} (internal)"))
        p = os.path.join(odir, f"{name}_still_{k + 1}.png")
        _three_up(items, p, title=f"{case_id} {case['title']}",
                  footer=[f"beyond: {case['beyond']}"])
        paths["stills"].append(p)
    c0, c1 = case["board"]["clip"]
    for r in refs:
        p = os.path.join(odir, f"{name}_vs_ref{r['ref']}.mp4")
        boards.clip_2up((v5, float(c0), float(c1), "v5 (today)"),
                        (r["path"], float(r["t"][0]),
                         float(r["t"][0]) + float(c1) - float(c0),
                         f"ref {r['ref']} (internal)"), p, height=720,
                        audio="left")
        paths["clips"].append(p)
    for dv in devs:
        if dv.get("land_pairs"):
            ev = dv["land_pairs"][0]["event"] / fps
            p = os.path.join(odir, f"{name}_strip_{dv['id']}.png")
            boards.strip(full, ev, p, label=f"{dv['id']} ({dv['template']}) "
                         f"on '{dv.get('target_word')}': event frame vs "
                         f"word onset {dv['land_pairs'][0]['err_f']:+d} f")
            paths["strips"].append(p)
    rows = []
    keys = ("duration_s", "cuts", "cuts_per_10s", "asl_s", "negative_runs",
            "white_flash_frames", "colour_flash_frames", "bw_share",
            "strobe_sections", "type_on_black_share", "text_on_screen_share",
            "text_boxes_per_sample", "occluded_text_samples", "layout",
            "flash_rate_max_per_s")
    for k in keys:
        rows.append((k, [v5_sum.get(k)] + [r["sig"].get(k) for r in refs]))
    lufs_v5 = st["loudness"][1].get("lufs")
    rows.append(("program LUFS", [lufs_v5] + [
        round(qa_metrics.loudness_lufs(r["path"]), 1) for r in refs]))
    lc = [d.get("legibility_lc") for d in devs
          if d.get("legibility_lc") is not None]
    rows.append(("graphics min APCA Lc", [round(min(lc), 1) if lc else None]
                 + [None] * len(refs)))
    le = [d["land_err_f"] for d in devs if d.get("land_err_f") is not None]
    rows.append(("word_slam max land err (f)",
                 [max(le) if le else None] + [None] * len(refs)))
    table = _table_png(rows, ["v5"] + [f"ref {r['ref']}" for r in refs],
                       os.path.join(odir, f"{name}_signature.png"),
                       f"{case_id} {case['title']}: signature (refsig)")
    board = {"case": case_id, "title": case["title"], "v5": v5,
             "v5_sha256": vflmod.sha256_file(v5),
             "program_s": program_s, "fps": fps,
             "reproduction_psnr_vs_v5": repro,
             "fixture_clock": clock,
             "local_render_wall_s": wall,
             "signature_v5": v5_sum,
             "references": [{k: r[k] for k in ("ref", "t", "approx", "sig")}
                            for r in refs],
             "devices": devs,
             "standing": {k: v[0] for k, v in st.items()},
             "standing_detail": {k: v[1] for k, v in st.items()},
             "graphics_legibility_min_lc": round(min(lc), 1) if lc else None,
             "graphics_below_lc60": len([x for x in lc if x < 60]),
             "graphics": len(devs),
             "word_slam_land_err_max_f": max(le) if le else None,
             "boards": {**paths, "signature": table},
             "owner_blind_rating": None,
             "wall_s": round(time.time() - t0, 1)}
    with open(os.path.join(odir, "board.json"), "w") as fh:
        json.dump(board, fh, indent=1, default=str)
    log(f"[{case_id}] done in {board['wall_s']} s")
    return board


def main(argv):
    import argparse
    # brand integrity and collisions compare the local re-render with its
    # control frame by frame: render both LOSSLESS (config reads FINAL_CRF
    # at import), so a change in one frame cannot re-decide the encode of
    # the frames after it (the end card included)
    os.environ.setdefault("FINAL_CRF", "0")
    ap = argparse.ArgumentParser(prog="refbench baseline")
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--out", default=os.path.expanduser(
        "~/Documents/Valmera/.valmera/refbench/boards/wave0"))
    a = ap.parse_args(argv)
    res = [run(c, a.out) for c in a.cases]
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump({b["case"]: {k: b[k] for k in (
            "title", "program_s", "reproduction_psnr_vs_v5",
            "graphics_legibility_min_lc", "graphics_below_lc60", "graphics",
            "word_slam_land_err_max_f", "standing", "signature_v5")}
            for b in res}, fh, indent=1, default=str)
    overview(res, os.path.join(a.out, "wave0_overview.png"))
    rating = os.path.join(a.out, "owner_blind_rating.json")
    if not os.path.exists(rating):
        with open(rating, "w") as fh:
            json.dump({"_how": "the owner rates each v5 short 1-10 without "
                               "seeing these numbers; later waves re-render "
                               "the same windows and are rated the same way",
                       "ratings": {b["case"]: None for b in res},
                       "rated_by": None, "date": None}, fh, indent=1)
    return 0


def overview(boards_, out):
    """One PNG: per short, its middle still and the numbers that matter."""
    from PIL import Image
    rows = []
    for b in boards_:
        st = b["boards"]["stills"]
        im = Image.open(st[len(st) // 2]).convert("RGB")
        im = im.resize((int(im.width * 520 / im.height), 520))
        sig = b["signature_v5"]
        rep = b["reproduction_psnr_vs_v5"]
        refs = ", ".join(f"ref {r['ref']}: {r['sig']['cuts_per_10s']} "
                         f"cuts/10s, text {r['sig']['text_on_screen_share']}"
                         for r in b["references"])
        lines = [
            f"{b['case']}  {b['title']}  ({b['program_s']} s program)",
            f"graphics {b['graphics']}: min APCA Lc "
            f"{b['graphics_legibility_min_lc']} (bar 60), "
            f"{b['graphics_below_lc60']} below; word-bound slams land within "
            f"{b['word_slam_land_err_max_f']} f",
            f"v5: {sig['cuts_per_10s']} cuts/10s, ASL {sig['asl_s']} s, "
            f"text on screen {sig['text_on_screen_share']}, punctuation "
            f"{sig['negative_runs'] + sig['white_flash_frames']}, "
            f"layout {sig['layout']}",
            refs,
            "standing: " + ", ".join(
                f"{k} " + ("ok" if v else "FAIL" if v is False else "n/a")
                for k, v in b["standing"].items()),
            f"local re-render vs v5: {rep['mean_db']} dB mean, "
            f"{round(100 * rep.get('share_ge_40db', 0), 1)}% of frames "
            ">= 40 dB",
            "owner blind rating: (pending)"]
        canvas = Image.new("RGB", (im.width + 40, 520 + 40 + 28 * len(lines)),
                           boards.BG)
        canvas.paste(im, (20, 20))
        boards._caption(canvas, lines, 540, x=20, size=19)
        rows.append(canvas)
    W = max(r.width for r in rows)
    sheet = Image.new("RGB", (W, sum(r.height for r in rows)), boards.BG)
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    sheet.save(out)
    return out
