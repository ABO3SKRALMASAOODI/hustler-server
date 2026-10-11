"""Refbench CLI.

    python -m benchmarks.refbench validate              every case spec
    python -m benchmarks.refbench run <case-id|path>    render, measure, board,
                                                        judge packets, verdict
    python -m benchmarks.refbench baseline <case-id>... score existing renders
    python -m benchmarks.refbench vfl build|verify|list|provenance
    python -m benchmarks.refbench catalog list|verify   HC stand-ins, music
    python -m benchmarks.refbench labels bootstrap|refine|coverage|export-cvat|
                                         import-cvat|syllable-template|
                                         syllable-sheet
    python -m benchmarks.refbench at0d2                 detectors vs hand labels
    python -m benchmarks.refbench judge-import <case-dir> <lens> <reply.json>

Outputs go to $VALMERA_REFBENCH_OUT/<case>/ (verdict JSON, boards, judge
packets). Engine runs need the worker runtime (ffmpeg, Chromium for motion
items); set VALMERA_RVM_MODEL / MATTE_RVM_MODEL for person mattes.
"""

import json
import os
import sys

import numpy as np

from . import fixtures_dir, out_dir, reference_path


# ── measurement helpers over renders ─────────────────────────────────────

def _read(path, frames, size=None):
    idx, fr = __import__("qa_metrics").read_frames(path, frames=set(frames),
                                                   size=size)
    return dict(zip(idx, fr))


def diff_masks(a, b, frames, thr=24, size=None):
    """{frame: bool mask} where render ``a`` differs from render ``b``."""
    fa, fb = _read(a, frames, size), _read(b, frames, size)
    out = {}
    for f in frames:
        if f in fa and f in fb:
            d = np.abs(fa[f].astype(np.int16) - fb[f].astype(np.int16))
            out[f] = d.max(axis=2) > thr
    return out


def _item(edl, item_id):
    for layer in ("motion", "texts", "fx"):
        for it in edl.get(layer) or []:
            if it.get("id") == item_id:
                return layer, it
    raise KeyError(f"no item {item_id}")


def _word_onset(words, word, near):
    best = None
    for w in words:
        if w["w"].strip(".,!?;:\"'").lower() == word.lower():
            if best is None or abs(w["t0"] - near) < abs(best["t0"] - near):
                best = w
    if best is None:
        raise KeyError(f"word {word!r} not in the window's ASR")
    return best["t0"]


def _program_time_of_source(edl, t_src):
    from timeline import Timeline
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    return tl.src_to_out(t_src)


def _target_time(edl, words, it, tgt):
    s = float(it["start"])
    if "word" in tgt:
        from timeline import Timeline
        tl = Timeline(edl["keep"], edl.get("inserts") or [],
                      edl.get("speed") or [])
        near = tl.out_to_src(s)          # the spoken word nearest the item
        return _program_time_of_source(edl, _word_onset(words, tgt["word"],
                                                       near))
    if "cut" in tgt:
        # the program start of keep k (speed spans and inserts included)
        from timeline import Timeline
        tl = Timeline(edl["keep"], edl.get("inserts") or [],
                      edl.get("speed") or [])
        return float(tl.offsets[int(tgt["cut"])])
    return float(tgt["t"])


def _text_masks(ctx, item_id, frames):
    """(shown, full) {frame: mask} of a TEXT item from its colour probes:
    shown = render vs its colour-swapped twin; full = the same pair drawn
    in front of the subject."""
    shown = diff_masks(ctx["render"], ctx["probes"][item_id]["render"],
                       frames)
    full = diff_masks(ctx["probes"][item_id]["front"],
                      ctx["probes"][item_id]["front_probe"], frames) \
        if ctx["probes"][item_id].get("front") else shown
    return shown, full


def measure(case, ctx):
    """Compute every metric the case lists from the run context:
    ctx = {edl, render, control, fps, win, notes, placements, probes,
           wall_s, wall_control_s, outro_s, program_s[, render2]}.

    Motion items are measured from the renderer's own clip (``ink``); text
    items from colour-probe twins (``engine.colour_probe``): both isolate
    the item's ink exactly, so a caption the item mutes or another layer
    never counts as type."""
    import qa_metrics
    from . import ink
    from .engine import asr_words
    res = {}
    fps = ctx["fps"]
    edl = ctx["edl"]
    words = asr_words(ctx["win"])
    for m in case.get("metrics", []):
        name = m["name"]
        bar = (m["op"], m["value"]) if "op" in m else None
        try:
            if name == "land_err_f":
                layer, it = _item(edl, m["item"])
                s, e = float(it["start"]), float(it["end"])
                t_tgt = _target_time(edl, words, it, m.get("target", {}))
                kind = m.get("event", "ink_onset")
                det = {}
                if kind == "ink_onset" and layer == "motion":
                    ev, det = ink.landing_frame(
                        ctx["render"], ctx["placements"][m["item"]], fps)
                    ev = [] if ev is None else [ev]
                elif kind == "ink_onset":
                    frames = list(range(max(0, int((s - 0.3) * fps)),
                                        int(min(e, s + 1.2) * fps) + 1))
                    shown, _full = _text_masks(ctx, m["item"], frames)
                    areas = {f: float(v.mean()) for f, v in shown.items()}
                    peak = max(areas.values()) if areas else 0.0
                    ev = [min(f for f, a in areas.items()
                              if a >= 0.6 * peak)] if peak > 0 else []
                else:                                  # flash_peak
                    frames = list(range(max(0, int((s - 0.3) * fps)),
                                        int(e * fps) + 2))
                    fr = _read(ctx["render"], frames, size=(96, 170))
                    lum = {f: float(qa_metrics.luminance(x).mean())
                           for f, x in fr.items()}
                    ev = [max(lum, key=lum.get)] if lum else []
                r = qa_metrics.land_err_f(
                    events=ev, targets=[int(round(t_tgt * fps))], bar=bar)
                r["detail"].update(target_t=round(t_tgt, 3), event_kind=kind,
                                   **{k: v for k, v in det.items()
                                      if k != "curve"})
            elif name in ("glyph_visible", "legibility"):
                layer, it = _item(edl, m["item"])
                s, e = float(it["start"]), float(it["end"])
                mid = int(round((s + 0.55 * (e - s)) * fps))
                frames = [mid - 2, mid, mid + 2]
                if layer == "motion":
                    pl = ctx["placements"][m["item"]]
                    img = _read(ctx["render"], frames)
                    per = [ink.item_metrics(img[f], pl, f, fps,
                                            role=m.get("role", "payload"),
                                            bar=bar)
                           for f in frames if f in img]
                    by = {p["frame"]: p for p in per if p and p.get(name)}
                    if not by:
                        raise RuntimeError("the item shows no glyphs")
                    at = mid if mid in by else sorted(by)[0]
                    r = dict(by[at][name])       # the mid frame decides
                    r["detail"] = dict(r.get("detail") or {}, frame=at,
                                       frames=frames, per_frame={
                                           str(f): round(p[name]["value"], 4)
                                           for f, p in sorted(by.items())})
                else:
                    shown, full = _text_masks(ctx, m["item"], frames)
                    if name == "glyph_visible":
                        fm, vm = full.get(mid), shown.get(mid)
                        r = qa_metrics.glyph_visible(
                            glyphs=ink.letters(fm), subject=fm & ~vm,
                            ghosted=bool(m.get("ghosted")), bar=bar)
                        r["detail"]["frame_shares"] = [
                            round(float((shown[f] & full[f]).sum())
                                  / max(1.0, float(full[f].sum())), 4)
                            for f in frames if f in shown and f in full]
                    else:
                        img = _read(ctx["render"], [mid])[mid]
                        r = qa_metrics.legibility(
                            frame=img, glyph_mask=shown[mid],
                            exclude=qa_metrics._dilate(shown[mid], 2),
                            role=m.get("role", "payload"), bar=bar)
                    r["detail"]["frame"] = mid
            elif name in ("matte_flicker", "edge_chatter"):
                note = next(n for n in ctx["notes"]
                            if n.get("item") == m["item"])
                info = qa_metrics.probe(note["mask"])
                _, mk = qa_metrics.read_frames(note["mask"], gray=True)
                masks = [x.astype(np.float32) / 255.0 for x in mk]
                r = (qa_metrics.matte_flicker(masks=masks, bar=bar)
                     if name == "matte_flicker" else
                     qa_metrics.edge_chatter(masks=masks, fps=info["fps"],
                                             bar=bar))
                r["detail"]["mask"] = os.path.basename(note["mask"])
            elif name == "flash_risk":
                n = int(round(ctx["program_s"] * fps))
                _, fr = qa_metrics.read_frames(ctx["render"], size=(160, 90),
                                               count=n)
                r = qa_metrics.flash_risk(
                    frames=fr, fps=fps,
                    opted_in="flash_optin" in case.get("allow", []), bar=bar)
            elif name == "duration_exact":
                r = qa_metrics.duration_exact(with_fx=ctx["render"],
                                              without_fx=ctx["control"],
                                              bar=bar)
            elif name == "determinism":
                r = qa_metrics.determinism(paths=[ctx["render"],
                                                  ctx["render2"]], bar=bar)
            elif name == "cost_ratio":
                r = qa_metrics.cost_ratio(with_s=ctx["wall_s"],
                                          without_s=ctx["wall_control_s"],
                                          bar=bar)
            else:
                # the case asked for it: a bar nobody measured fails the
                # case (verdict.unmeasured_metrics), a bare name is reported
                r = {"value": None, "pass": None,
                     "detail": {"why": f"refbench has no measurer for {name} "
                                       "in engine cases yet"}}
        except Exception as ex:
            r = {"value": None, "pass": False,
                 "detail": {"error": f"{ex.__class__.__name__}: {ex}"[:300]}}
        if bar is not None and r.get("bar") is None:
            r["bar"] = qa_metrics.bar_text(bar)
        res[name if name not in res else f"{name}#{len(res)}"] = r
    return res


# ── commands ──────────────────────────────────────────────────────────────

def cmd_validate(argv):
    from . import spec, vfl
    data = vfl.load()
    bad = 0
    for p in spec.all_case_paths():
        case = spec.load(p)
        errs = spec.validate(case, vfl=data)
        print(f"{'ok ' if not errs else 'BAD'} {os.path.relpath(p)}")
        for e in errs:
            print(f"      - {e}")
        bad += bool(errs)
    return 1 if bad else 0


def _board(case, cdir, render, *, program_s, fps, landing_t=None):
    from . import boards
    b = case.get("board") or {}
    refs = case.get("reference") or []
    out = {"images": []}
    ref = refs[0] if refs else None
    rp = reference_path(ref["ref"]) if ref else None
    for i, t in enumerate(b.get("stills", [])[:4]):
        rt = (ref.get("board_t") or ref.get("t", [0])[0]) if ref else 0
        if rp:
            p = boards.still_2up(
                (render, float(t), f"Valmera ({case['id']})"),
                (rp, float(rt), f"reference {ref['ref']} (internal)"),
                os.path.join(cdir, f"still_{i}.png"),
                title=case.get("title", ""),
                footer=[f"beyond: {case.get('beyond', '')}"])
        else:
            p = boards.contact(render, os.path.join(cdir, f"still_{i}.png"),
                               n=6, t0=float(t) - 0.5, t1=float(t) + 0.5)
        out["images"].append(p)
    clip = b.get("clip")
    if clip and rp:
        rt = (ref.get("board_t") or ref.get("t", [0])[0])
        dur = float(clip[1]) - float(clip[0])
        out["clip"] = boards.clip_2up(
            (render, float(clip[0]), float(clip[1]), f"Valmera {case['id']}"),
            (rp, float(rt), float(rt) + dur, f"ref {ref['ref']} (internal)"),
            os.path.join(cdir, "side_by_side.mp4"))
    if landing_t is not None:
        out["strip"] = boards.strip(render, landing_t,
                                    os.path.join(cdir, "landing_strip.png"),
                                    label=f"{case['id']} landing")
        out["images"].append(out["strip"])
    return out


def run_case(path_or_id, *, out=None, determinism=False):
    import renderer
    from . import engine, judges, spec, standing, verdict
    path = path_or_id if os.path.exists(path_or_id) else spec.find(path_or_id)
    case = spec.load(path)
    errs = spec.validate(case)
    if errs:
        raise SystemExit(f"{case.get('id')}: invalid spec: {errs}")
    cdir = os.path.join(out or out_dir(), case["id"])
    os.makedirs(cdir, exist_ok=True)
    win = engine.window_for_case(case)
    work = os.path.join(cdir, "work")
    control_edl, edl, locals_, notes = engine.build(case, win, work)
    preview = case["render"]["variant"] == "preview"
    outro_s = renderer.outro_seconds(preview)
    rpath = os.path.join(cdir, "render.mp4")
    cpath = os.path.join(cdir, "control.mp4")
    placements = {}
    _, wall_c = engine.render(control_edl, win, cpath, work + "/c",
                              preview=preview)
    prog, wall = engine.render(edl, win, rpath, work + "/a", preview=preview,
                               locals_=locals_, placements=placements)
    program_s = float(prog) - outro_s
    ctx = {"edl": edl, "control_edl": control_edl, "render": rpath,
           "control": cpath, "fps": float(__import__("qa_metrics").probe(
               rpath)["fps"]), "win": win, "notes": notes, "wall_s": wall,
           "wall_control_s": wall_c, "outro_s": outro_s,
           "program_s": program_s, "placements": placements, "probes": {}}
    # colour-probe twins for every TEXT item a type metric names
    text_ids = {m["item"] for m in case["metrics"]
                if m.get("item") and m["name"] in (
                    "glyph_visible", "legibility", "land_err_f")
                and _item(edl, m["item"])[0] == "texts"}
    for tid in sorted(text_ids):
        pr = {"render": os.path.join(cdir, f"probe_{tid}.mp4")}
        engine.render(engine.colour_probe(edl, tid), win, pr["render"],
                      work + f"/p_{tid}", preview=preview, locals_=locals_)
        if _item(edl, tid)[1].get("behind"):
            fr = engine.front_of(edl)
            pr["front"] = os.path.join(cdir, f"front_{tid}.mp4")
            pr["front_probe"] = os.path.join(cdir, f"front_probe_{tid}.mp4")
            engine.render(fr, win, pr["front"], work + f"/f_{tid}",
                          preview=preview, locals_=locals_)
            engine.render(engine.colour_probe(fr, tid), win,
                          pr["front_probe"], work + f"/fp_{tid}",
                          preview=preview, locals_=locals_)
        ctx["probes"][tid] = pr
    if determinism or any(m["name"] == "determinism"
                          for m in case["metrics"]):
        ctx["render2"] = os.path.join(cdir, "render_2.mp4")
        engine.render(edl, win, ctx["render2"], work + "/a2",
                      preview=preview, locals_=locals_)
    metrics = measure(case, ctx)
    dev_windows = []
    for layer in ("motion", "texts"):
        for it in edl.get(layer) or []:
            dev_windows.append((float(it["start"]), float(it["end"])))
    st, st_detail = standing.run_all(case, rpath, control=cpath, edl=edl,
                                     outro_s=outro_s,
                                     device_windows=dev_windows,
                                     program_s=program_s)
    landing = None
    lm = metrics.get("land_err_f")
    if lm and lm.get("detail", {}).get("pairs"):
        landing = lm["detail"]["pairs"][0]["event"] / ctx["fps"]
    board = _board(case, cdir, rpath, program_s=program_s, fps=ctx["fps"],
                   landing_t=landing)
    judges.packets(case, cdir, board=board, metrics=metrics, standing=st,
                   edl_summary=standing.edl_summary(edl))
    v = verdict.build(case, render_path=rpath,
                      variant=case["render"]["variant"], wall_s=wall,
                      metrics=metrics, standing=st, standing_detail=st_detail,
                      judges=judges.collect(case, cdir), board=board,
                      extra={"notes": notes, "metric_detail": {
                          k: v.get("detail") for k, v in metrics.items()},
                          "control_wall_s": wall_c, "program_s": program_s,
                          "window": f"{win['vfl']}/{win['name']}",
                          "placements": placements})
    p = verdict.write(v, cdir)
    with open(os.path.join(cdir, "edl.json"), "w") as fh:
        json.dump(edl, fh, indent=1)
    print(f"{case['id']}: pass={v['pass']} -> {p}")
    for k, m in v["metrics"].items():
        print(f"   {k:<16} {m['value']} bar {m['bar']} pass {m['pass']}")
    print(f"   standing {v['standing']}")
    return v


def cmd_run(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="refbench run")
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--determinism", action="store_true")
    ap.add_argument("--lossy", action="store_true",
                    help="keep the production CRF (comparisons then carry "
                         "codec drift: brand_integrity reports None and "
                         "region_locality needs its own tolerance)")
    a = ap.parse_args(argv)
    if not a.lossy:
        # Exact comparisons (watermark ROI |d| = 0, region locality, end-card
        # md5) need LOSSLESS renders: a lossy encode re-decides every
        # macroblock after a change for the rest of the GOP. config reads
        # FINAL_CRF at import, so set it before the renderer loads.
        os.environ.setdefault("FINAL_CRF", "0")
    ok = True
    for c in a.cases:
        v = run_case(c, out=a.out, determinism=a.determinism)
        ok &= v["pass"] is not False
    return 0 if ok else 1


def cmd_judge_import(argv):
    """judge-import <case-dir> <lens> <reply.json>: store one lens judge's
    reply and recompute the case verdict's pass."""
    from . import judges, spec, verdict
    case_dir, lens, reply = argv[:3]
    with open(reply) as fh:
        v = judges.parse(fh.read(), lens)
    p = os.path.join(case_dir, f"judge_{lens}.verdict.json")
    with open(p, "w") as fh:
        json.dump(v, fh, indent=1)
    print(p)
    cid = os.path.basename(os.path.normpath(case_dir))
    cpath = spec.find(cid)
    if cpath and os.path.exists(os.path.join(case_dir,
                                             f"{cid}.verdict.json")):
        out = verdict.rejudge(spec.load(cpath), case_dir)
        print(f"{cid}: pass={out['pass']} judges={len(out['judges'])}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "help"
    if cmd == "vfl":
        from . import vfl
        return vfl.main(argv)
    if cmd == "labels":
        from . import labels
        return labels.main(argv)
    if cmd == "at0d2":
        from . import at0d2
        return at0d2.main(argv)
    if cmd == "catalog":
        from . import catalog
        return catalog.main(argv)
    if cmd == "validate":
        return cmd_validate(argv)
    if cmd == "run":
        return cmd_run(argv)
    if cmd == "baseline":
        from . import baseline
        return baseline.main(argv)
    if cmd == "judge-import":
        return cmd_judge_import(argv)
    print(__doc__)
    return 2
