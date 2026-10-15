"""Track 4F acceptance: the motion critic against seeded defects.

    cd worker && python -m benchmarks.refbench.at4f AT-4F-1 [--only late_word]
    cd worker && python -m benchmarks.refbench.at4f AT-4F-2

AT-4F-1 (ROADMAP Wave 4 / 4F): 30 seeded defects — late words, flickering
mattes, wrong-lead cuts (a cut inside a sounding word), 6+ flashes per
second, a glyph over a face — each rendered through the real renderer, and
clean renders of the same windows; the critic must find >= 90 % of the
defects (the finding's code, near its time) with <= 10 % false positives
on the clean renders (any finding of the five seeded codes).

AT-4F-2: 22 white hits on a 16th grid of the owner-named track's beat grid
(129 BPM) over a talking window: the edit is flagged, the flash-safe plan
keeps the downbeat hits, and the render of the thinned edit measures <= 3
flashes in every second.

Real footage: the VFL windows with stored ASR (V01, V02, V03, V04). The
flicker cases use the track-1A synthetic subject (its matte known
exactly) with a matte jittered frame to frame — the only way to seed a
flickering matte on purpose. Internal only: renders and the verdict go to
$VALMERA_REFBENCH_OUT/AT-4F-*/, boards to .valmera/refbench/boards/wave4/.
"""

import copy
import json
import os
import random
import subprocess
import sys
import time

from . import out_dir

BOARDS = os.environ.get("VALMERA_W4_BOARDS") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4")
CATEGORIES = ("late_word", "matte_flicker", "cut_mid_word", "flash_rate",
              "type_on_face")
WINDOWS = (("V01", "thiel_ai_bigger"), ("V02", "thiel_flying_cars"),
           ("V04", "rogan_gamers"), ("V03", "jobs_fonts"))
CLIP_S = 8.0


# ── footage ──────────────────────────────────────────────────────────────

def _win(vfl, name):
    from . import vfl as vflmod
    return vflmod.window(vfl, name)


def _words(win):
    from . import engine
    return engine.asr_words(win)


def _content_words(win, a, b, min_len=4):
    out = []
    for w in _words(win):
        tok = "".join(c for c in str(w["w"]) if c.isalnum())
        if len(tok) >= min_len and a + 0.6 <= w["t0"] <= b - 1.6:
            out.append({**w, "tok": tok})
    return out


def _base(win, a, b, aspect="9:16"):
    from . import engine
    return engine.window_edl(win, aspect, None, [[a, b]])


def _render(edl, win, out, work, *, preview=True):
    from . import engine
    from schemas import validate_edl
    dur = float(win["file_span"][1] - win["file_span"][0])
    e = validate_edl(copy.deepcopy(edl), dur).model_dump()
    engine.render(e, win, out, work, preview=preview)
    return e


def _index(win):
    from . import engine
    return engine.index_for(win)


def _span(win, k):
    """A CLIP_S window inside the fixture, the k-th one (file clock)."""
    from . import vfl as vflmod
    a0 = vflmod.file_time(win, win["t"][0])
    b0 = vflmod.file_time(win, win["t"][1])
    lo = float(a0) + 1.0
    hi = float(b0) - CLIP_S - 0.5
    step = max(1.0, (hi - lo) / 6.0)
    a = round(lo + (k % 6) * step, 2)
    return a, round(a + CLIP_S, 2)


# ── seeded cases ─────────────────────────────────────────────────────────
# Each case: {id, cat, defect, t (program s of the defect or None), build}
# where build(work) -> (edl, win, index, kw) renders through _render.

def _late_word(k, defect):
    vfl, name = WINDOWS[k % 3]
    win = _win(vfl, name)
    a, b = _span(win, k)
    words = _content_words(win, a, b)
    if not words:
        return None
    w = words[min(len(words) - 1, (k * 3) % len(words))]
    fps = float(win["fps"])
    late_f = (4, 6, 8, 5, 7, 10)[k % 6]
    t_w = w["t0"] - a
    start = t_w + late_f / fps if defect else t_w - 0.02
    edl = _base(win, a, b)
    edl["motion"] = [{"id": "slam", "template": "word_slam",
                      "start": round(max(0.0, start), 2),
                      "end": round(max(0.0, start) + 1.3, 2),
                      "params": {"text": w["tok"].upper(), "y": 0.74,
                                 "entrance": "pop"}}]
    return {"edl": edl, "win": win, "t": t_w,
            "note": f"'{w['tok']}' at {t_w:.2f}s, slam "
                    f"{'+%d f late' % late_f if defect else 'on the word'}"}


_QUIET = {}


def _quiet_points(win, a, b, min_s=0.08, db=-38.0):
    """File-clock centres of quiet stretches (rendered speech pauses: the
    ASR's word spans abut, so pauses are read from the audio)."""
    import motion_critic
    key = win["path"]
    if key not in _QUIET:
        _QUIET[key] = motion_critic.audio_rms(win["path"])
    lv, fs = _QUIET[key]
    if lv is None:
        return []
    out, run = [], None
    for i, v in enumerate(lv):
        t = i * fs
        quiet = v < db
        if quiet and run is None:
            run = t
        elif not quiet and run is not None:
            if t - run >= min_s and a + 0.6 <= (run + t) / 2 <= b - 1.0:
                out.append(round((run + t) / 2, 2))
            run = None
    return out


def _cut_mid_word(k, defect):
    vfl, name = WINDOWS[k % 4]
    win = _win(vfl, name)
    a, b = _span(win, k + 1)
    if defect:
        words = [w for w in _content_words(win, a, b, min_len=5)
                 if w["t1"] - w["t0"] >= 0.25]
        if len(words) < 2:
            return None
        w = words[(k * 2) % len(words)]
        cut = round(w["t0"] + 0.45 * (w["t1"] - w["t0"]), 2)
        resume = round(cut + 0.6, 2)
        note = f"cut at {cut - a:.2f}s inside '{w['w']}'"
    else:
        # a cut in a real pause, resuming in a later pause: what a clean,
        # word-safe cut looks like
        qs = _quiet_points(win, a, b)
        if len(qs) < 2:
            return None
        cut = qs[(k * 2) % len(qs)]
        later = [q for q in qs if q >= cut + 0.8]
        if not later:
            return None
        resume = later[0]
        note = f"cut at {cut - a:.2f}s in a pause, resume in a pause"
    edl = _base(win, a, b)
    edl["keep"] = [[a, cut], [resume, b]]
    return {"edl": edl, "win": win, "t": cut - a, "note": note}


def _flashes(k, defect):
    vfl, name = WINDOWS[k % 4]
    win = _win(vfl, name)
    a, b = _span(win, k + 2)
    fps = float(win["fps"])
    rate = (6, 7, 8, 6, 9, 6)[k % 6] if defect else (1, 2, 1, 2, 2, 1)[k % 6]
    kind = ("negative", "white_hit", "negative", "white_hit", "negative",
            "white_hit")[k % 6]
    t0 = 1.5 + 0.3 * (k % 3)
    hits = []
    n = int(rate * 1.6)
    for i in range(n):
        f = int(round((t0 + i / float(rate)) * fps))
        s = round(f / fps, 2)
        hits.append({"id": f"hit{i}", "type": "accent",
                     "stage": "pre_captions", "start": s,
                     "end": round((f + 1) / fps, 2), "kind": kind,
                     "device": f"accent_{kind}"})
    edl = _base(win, a, b)
    edl["fx"] = hits
    return {"edl": edl, "win": win, "t": t0,
            "note": f"{rate}/s {kind} hits from {t0:.1f}s"}


def _face(k, defect):
    vfl, name = WINDOWS[k % 3]
    win = _win(vfl, name)
    a, b = _span(win, k + 3)
    words = _content_words(win, a, b)
    if not words:
        return None
    w = words[(k * 5) % len(words)]
    t_w = w["t0"] - a
    # the face sits around y 0.30-0.45 of these 9:16 crops; the clean
    # case keeps the slam in the lower band
    y = (0.36, 0.40, 0.33, 0.38, 0.42, 0.35)[k % 6] if defect else 0.76
    edl = _base(win, a, b)
    edl["motion"] = [{"id": "slam", "template": "word_slam",
                      "start": round(t_w - 0.02, 2),
                      "end": round(t_w + 1.6, 2),
                      "params": {"text": w["tok"].upper(), "y": y,
                                 "entrance": "pop"}}]
    return {"edl": edl, "win": win, "t": t_w,
            "note": f"slam '{w['tok']}' at y {y:.2f}"}


def _flicker_source(root):
    """The 1A synthetic subject (8 s, 25 fps, 640x360) and its index."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "tests"))
    import media
    import sidecars
    import w1a_support as sup
    os.makedirs(root, exist_ok=True)
    src = os.path.join(root, "synth_src.mp4")
    if not os.path.exists(src):
        sup.make_source(src)
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [])
    return src, ref, info


def _write_jittered(store_root, ref, amp_px, seed):
    """matte.person chunks of the synthetic rectangle whose width jitters
    +-amp_px every frame (amp 0: the exact matte)."""
    import sidecars
    import w1a_support as sup
    sp = sidecars.spec("matte.person")
    resolved = sidecars.resolve_params("matte.person", None)
    store = sidecars.LocalStore(store_root)
    os.makedirs(store_root, exist_ok=True)
    sw, sh = int(ref.width), int(ref.height)
    mw = min(sw, int(resolved.get("max_w") or 960))
    mw -= mw % 2
    mh = int(round(sh * mw / float(sw)))
    mh -= mh % 2
    k = mw / float(sw)
    fps = min(float(resolved.get("fps_max") or 30.0), float(ref.fps))
    rnd = random.Random(seed)
    for t0, t1 in sidecars.chunk_ranges(ref.shots, ref.duration_s):
        key = sidecars.sidecar_key("matte.person", ref.sha256,
                                   sp["model_id"], sp["model_version"],
                                   resolved, (t0, t1))
        n = max(1, int(round((t1 - t0) * fps)))
        import numpy as np
        rw, rh, y = 80, 160, 100
        buf = []
        for j in range(n):
            t = t0 + j / fps
            jit = rnd.choice((-1, 1)) * amp_px * (0.5 + rnd.random()) \
                if amp_px else 0.0
            x0 = (40.0 + t * 60.0) * k - jit
            x1 = x0 + rw * k + 2 * jit
            fr = np.zeros((mh, mw), np.uint8)
            ya, yb = int(round(y * k)), int(round((y + rh) * k))
            xa, xb = max(0, int(round(x0))), min(mw, int(round(x1)))
            if xb > xa:
                fr[ya:yb, xa:xb] = 255
            buf.append(fr.tobytes())
        tmp = os.path.join(store_root, f"tmp_{key[:10]}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-s", f"{mw}x{mh}", "-r",
                        f"{fps:.6f}", "-i", "-", "-c:v", "libx264", "-crf",
                        "12", "-pix_fmt", "yuv420p", tmp],
                       input=b"".join(buf), check=True)
        store.put_file(sidecars.data_key("matte.person", ref.sha256, key),
                       tmp, "video/mp4")
        manifest = {
            "manifest_version": sidecars.MANIFEST_VERSION, "key": key,
            "kind": "matte.person", "model_id": sp["model_id"],
            "model_version": sp["model_version"], "params": resolved,
            "src_range": [t0, t1], "fps": round(fps, 6), "frames": n,
            "width": mw, "height": mh, "format": sp["format"],
            "ext": sp["ext"], "bytes": os.path.getsize(tmp),
            "created_at": time.time(),
            "qa": {"flicker": 0.01, "edge_chatter": 0.0, "coverage": []},
            "engine": "rvm_mobilenetv3", "source_sha": ref.sha256,
            "warmup_s": 0.0}
        store.put_bytes(sidecars.manifest_key("matte.person", ref.sha256,
                                              key),
                        json.dumps(manifest).encode("utf-8"))
        os.remove(tmp)


def _flicker(k, defect):
    kind = ("outline", "glow", "outline", "fill_poster", "glow", "outline")[k % 6]
    amp = (3, 4, 5, 6, 3, 8)[k % 6] if defect else 0
    return {"synthetic": True, "kind": kind, "amp": amp, "seed": 100 + k,
            "t": 2.0, "note": f"subject {kind}, matte jitter +-{amp}px"}


BUILDERS = {"late_word": _late_word, "cut_mid_word": _cut_mid_word,
            "flash_rate": _flashes, "type_on_face": _face,
            "matte_flicker": _flicker}


def cases(only=None, n_defect=6, n_clean=4):
    out = []
    for cat in CATEGORIES:
        if only and cat not in only:
            continue
        for k in range(n_defect):
            out.append({"id": f"{cat}-d{k}", "cat": cat, "defect": True,
                        "k": k})
        for k in range(n_clean):
            out.append({"id": f"{cat}-c{k}", "cat": cat, "defect": False,
                        "k": k})
    return out


# ── run ──────────────────────────────────────────────────────────────────

def _run_synthetic(case, spec, work):
    import renderer
    import sidecars
    from schemas import validate_edl
    root = os.path.join(work, "synth")
    src, ref, info = _flicker_source(root)
    store = os.path.join(work, f"store_{case['id']}")
    _write_jittered(store, ref, spec["amp"], spec["seed"])
    prev = sidecars.use_store(sidecars.LocalStore(store))
    try:
        index = {"words": [], "shots": [],
                 "video": {"width": info["width"], "height": info["height"],
                           "fps": info["fps"], "duration": info["duration"],
                           "sha256": ref.sha256}}
        kind = spec["kind"]
        extra = {"colour": "black"} if kind == "outline" else \
            {"colour": "accent"} if kind == "glow" else {}
        if kind == "fill_poster":
            kind, extra = "poster", {"field": "accent",
                                     "keep_footage": False}
        edl = {"keep": [[0.0, 8.0]], "fx": [{
            "id": "sx", "type": "subject", "stage": "world", "start": 2.0,
            "end": 5.0, "kind": kind, **extra,
            "device": f"subject_{kind}"}]}
        e = validate_edl(edl, 8.0).model_dump()
        out = os.path.join(work, f"{case['id']}.mp4")
        renderer.render_edl(e, index, src, out, os.path.join(
            work, f"w_{case['id']}"), True)
        return out, e, index
    finally:
        sidecars.use_store(prev)


def run_case(case, work):
    import motion_critic
    spec = BUILDERS[case["cat"]](case["k"], case["defect"])
    if spec is None:
        return {**case, "skipped": "no usable word in the window"}
    t = time.time()
    if spec.get("synthetic"):
        out, edl, index = _run_synthetic(case, spec, work)
    else:
        out = os.path.join(work, f"{case['id']}.mp4")
        edl = _render(spec["edl"], spec["win"], out,
                      os.path.join(work, f"w_{case['id']}"))
        index = _index(spec["win"])
    render_s = time.time() - t
    t = time.time()
    res = motion_critic.critique(out, edl, index, deep=True)
    crit_s = time.time() - t
    seeded = [f for f in res["findings"] if f["code"] == case["cat"]]
    near = [f for f in seeded if f.get("t") is None or spec.get("t") is None
            or abs(float(f["t"]) - float(spec["t"])) <= 1.0]
    any_seeded_code = [f for f in res["findings"]
                       if f["code"] in CATEGORIES]
    return {**case, "note": spec.get("note"), "render": out,
            "detected": bool(near), "found": [
                {k: f.get(k) for k in ("code", "t", "message")}
                for f in any_seeded_code],
            "false_positive": (not case["defect"]) and bool(any_seeded_code),
            "render_s": round(render_s, 1), "critic_s": round(crit_s, 1)}


def at_4f_1(only=None):
    od = os.path.join(out_dir(), "AT-4F-1")
    os.makedirs(od, exist_ok=True)
    rows = []
    for case in cases(only):
        try:
            r = run_case(case, od)
        except Exception as e:  # noqa: BLE001
            r = {**case, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        rows.append(r)
        flag = ("SKIP" if r.get("skipped") else "ERR" if r.get("error") else
                ("hit" if r.get("detected") else "MISS") if case["defect"]
                else ("FP" if r.get("false_positive") else "clean"))
        print(f"{case['id']:22s} {flag:5s} {r.get('note') or ''} "
              f"{r.get('error') or ''}", flush=True)
    d = [r for r in rows if r["defect"] and not r.get("skipped")
         and not r.get("error")]
    c = [r for r in rows if not r["defect"] and not r.get("skipped")
         and not r.get("error")]
    det = sum(1 for r in d if r["detected"])
    fp = sum(1 for r in c if r["false_positive"])
    by = {}
    for r in d:
        by.setdefault(r["cat"], [0, 0])
        by[r["cat"]][0] += int(r["detected"])
        by[r["cat"]][1] += 1
    verdict = {"case": "AT-4F-1", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "defects": len(d), "detected": det,
               "detection_rate": round(det / max(1, len(d)), 3),
               "clean": len(c), "false_positives": fp,
               "fp_rate": round(fp / max(1, len(c)), 3),
               "by_category": by,
               "pass": det >= 0.9 * len(d) and fp <= 0.1 * max(1, len(c)),
               "rows": rows}
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(verdict, f, indent=1, default=str)
    print(json.dumps({k: v for k, v in verdict.items() if k != "rows"},
                     indent=1))
    return verdict


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    if a.case == "AT-4F-1":
        at_4f_1(a.only)
    elif a.case == "AT-4F-2":
        from . import at4f2
        at4f2.run()
    else:
        raise SystemExit(f"unknown case {a.case}")


if __name__ == "__main__":
    main()
