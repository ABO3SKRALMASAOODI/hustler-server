"""LayoutDoc v1: the solved typeset, handed to the page (INTERFACES_W2 §3.2).

``page_params(item, W, H, fps)`` is the motion_templates derived-params
seam for template ``typeset``: it solves the item's INTENT (params: text,
voice, layout, depth, verb, palette, ...) on the canvas the clip is about to
render on and returns ``{"_layout": doc}``. The same function runs at write
time on browserless lanes (``solve_item``), so the write's footprint, the
keep-out and the render agree by construction, and a 4:5 or 16:9 master
re-solves from the same item (A4: intent, not pixels).

Run fields are design pixels (motion_engine.design_size), baseline-left
origins; the page draws them and moves them, nothing else.
"""

import hashlib
import json
import math
import threading

from . import roles as R
from . import shaping
from . import solver as S
from . import voices as V

VERSION = 1
MAX_DOC_BYTES = 60_000
DEFAULT_INK = "#f5f1ea"
DEFAULT_ACCENT = "#e8202a"
VERBS = ("auto", "cut", "fade", "blur_resolve", "settle", "decay",
         "scramble_lock", "mosaic_resolve", "stretch_smear", "duration_wipe",
         "strobe_flip", "emboss_ink", "echo_stack", "outline_fill",
         "glyph_morph", "lamp", "typewriter", "drop")
# How long before its spoken onset each entrance verb starts moving, so the
# word is RESOLVED on the onset frame (2B-1: blur reaches 0 on the onset).
LEAD_S = {"cut": 0.0, "fade": 0.12, "blur_resolve": 0.28, "settle": 0.0,
          "scramble_lock": 0.36, "mosaic_resolve": 0.3,
          "stretch_smear": 0.22, "duration_wipe": 0.0, "strobe_flip": 0.0,
          "emboss_ink": 0.2, "echo_stack": 0.0, "outline_fill": 0.24,
          "glyph_morph": 0.45, "lamp": 0.0, "typewriter": 0.0, "drop": 0.2,
          "decay": 0.0}
MATERIALS = ("auto", "flat", "glow", "neon", "crt", "outline", "ghost",
             "emboss", "texture")
EXITS = ("cut", "fade", "decay", "hold")
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 256


def _hexcol(v, default):
    s = str(v or "").strip().lower()
    if len(s) == 7 and s.startswith("#"):
        try:
            int(s[1:], 16)
            return s
        except ValueError:
            pass
    return default


def onsets_of(item, n_words):
    """Per printed word, its spoken onset in item seconds (None: unsaid),
    from the item's ``reading`` (caption_carry: reads_onsets templates get
    one row of the printed words in reading order)."""
    rows = ((item or {}).get("reading") or {}).get("rows") or []
    flat = [v for row in rows for v in row]
    out = [None] * n_words
    for i in range(min(n_words, len(flat))):
        out[i] = flat[i]
    return out


def _fill_onsets(onsets, duration):
    """Words without an onset (unsaid, or no transcript) build evenly
    between their neighbours so the lockup still reads in order."""
    n = len(onsets)
    out = list(onsets)
    known = [(i, t) for i, t in enumerate(out) if t is not None]
    if not known:
        span = max(0.2, min(duration * 0.5, 0.18 * n))
        return [round(span * i / max(1, n), 3) for i in range(n)], [None] * n
    for i in range(n):
        if out[i] is not None:
            continue
        prev = max(((j, t) for j, t in known if j < i), default=None)
        nxt = min(((j, t) for j, t in known if j > i), default=None)
        if prev and nxt:
            out[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (
                nxt[0] - prev[0])
        elif prev:
            out[i] = prev[1] + 0.12 * (i - prev[0])
        else:
            out[i] = max(0.0, nxt[1] - 0.12 * (nxt[0] - i))
    durs = []
    for i in range(n):
        nxt = out[i + 1] if i + 1 < n else None
        durs.append(round(max(0.08, (nxt - out[i]) if nxt is not None
                              else 0.45), 3))
    return [round(min(max(0.0, t), max(0.0, duration - 0.04)), 3)
            for t in out], durs


def _accent_runs(placed, v, params):
    """The ONE accent: starred/emphasis words first, else the voice's
    preferred roles, strongest (largest) first — one word, or one starred
    phrase."""
    acc = [p for p in placed if p.tok.role == "accent"]
    if acc:
        return {id(p) for p in acc}
    for role in v.get("accent_on") or []:
        cands = [p for p in placed if p.tok.role == role or (
            role in ("content", "whisper") and p.tok.rung == role)]
        if cands:
            if role in ("content", "whisper"):
                return {id(p) for p in cands}
            best = max(cands, key=lambda p: p.size)
            return {id(best)}
    return set()


def build_doc(params, *, W, H, fps=30.0, duration=3.0, onsets=None,
              faces=(), watermark=None, captions=None, graphics=(),
              prefer=None, budget_s=None):
    """The LayoutDoc for typeset ``params`` on a W x H design canvas, or a
    doc with ``runs: []`` and ``solver_report.error`` when nothing fits."""
    words = R.tokens(params.get("text"))
    if not words:
        return {"version": VERSION, "canvas": {"w": W, "h": H}, "runs": [],
                "solver_report": {"error": "no words"}}
    n = len(words)
    on, durs = _fill_onsets(list(onsets or [None] * n)[:n] +
                            [None] * max(0, n - len(onsets or [])),
                            float(duration))
    voice_name = str(params.get("voice") or "auto")
    if voice_name not in V.VOICES:
        voice_name = V.DEFAULT_VOICE
    fam = str(params.get("layout") or "auto")
    depth = str(params.get("depth") or "front")
    if depth == "ghost":
        depth = "behind"
    if budget_s is None:
        try:
            import config
            budget_s = float(config.TYPESET_SOLVE_BUDGET_S)
        except Exception:  # noqa: BLE001
            budget_s = 2.0
    if prefer is None:
        from . import memory
        prefer = memory.prefer_of(params)
    lay, v, toks, space, rep = S.solve(
        words, W=W, H=H, voice_name=voice_name, family=fam, depth=depth,
        onsets=on, durations=durs, faces=faces, watermark=watermark,
        captions=captions, graphics=graphics,
        emphasis=params.get("emphasis") or None,
        seed=int(params.get("seed") or 0),
        region=(params.get("region") if params.get("region") not in
                (None, "", "auto") else None),
        prefer=prefer, budget_s=budget_s)
    doc = {"version": VERSION,
           "canvas": {"w": W, "h": H, "fps": fps,
                      "aspect": round(W / max(1.0, H), 4)},
           "voice": (V.DEFAULT_VOICE if rep.get("voice_fallback")
                     else voice_name), "runs": [],
           "keepouts_used": space.keepouts_used(), "solver_report": rep}
    if lay is None:
        rep["error"] = "no layout fits the free space at a legible size"
        return doc
    ink = _hexcol(params.get("ink"), v["palette"].get("ink") or DEFAULT_INK)
    accent = _hexcol(params.get("accent"), DEFAULT_ACCENT
                     if v["palette"].get("accent") == "accent"
                     else v["palette"].get("accent") or DEFAULT_ACCENT)
    material = str(params.get("material") or "auto")
    if material not in MATERIALS or material == "auto":
        material = v.get("material", "flat")
    verb_req = str(params.get("verb") or "auto")
    exit_verb = str(params.get("exit") or "cut")
    if exit_verb not in EXITS:
        exit_verb = "cut"
    accents = _accent_runs(lay.placed, v, params)
    flip = float(params.get("flip_at") or 0.0)
    order = sorted(lay.placed, key=lambda p: (on[p.tok.i], p.tok.i))
    dur = float(duration)
    quote = lay.family == "quote_window"
    for k, p in enumerate(order):
        t = p.tok
        rung = t.rung
        verb = v["verbs"].get(rung, "cut")
        if verb_req != "auto" and verb_req in LEAD_S and (
                rung == "hero" or verb_req in ("lamp", "duration_wipe",
                                               "typewriter")):
            verb = verb_req
        t_in = max(0.0, on[t.i] - LEAD_S.get(verb, 0.0))
        if quote:
            nxt = [q for q in order if on[q.tok.i] > on[t.i] + 1e-6]
            t_out = on[nxt[0].tok.i] if nxt else dur
        else:
            t_out = dur
        sh = t.shaped(p.size)
        chars = [round(p.x + cx + p.track_px * ci, 2)
                 for ci, (cx, _a) in enumerate(sh.chars)]
        run = {
            "id": f"r{t.i}", "text": t.text, "shows_words": [t.i],
            "font": t.spec.as_dict(), "css": t.spec.css(),
            "size_px": round(p.size, 2),
            "tracking_em": round(p.track_px / max(1e-6, p.size), 4),
            "x": round(p.x, 2), "y": round(p.y, 2),
            "chars_x": chars,
            "ink": [round(v_, 1) for v_ in p.ink],
            "cap": round(sh.cap, 2),
            "rot_deg": round(p.rot, 2),
            "rot_c": [round(p.cx, 1), round(p.cy, 1)] if p.cx is not None
            else None,
            "role": t.role, "rung": rung, "line": p.line,
            "colour": accent if id(p) in accents else ink,
            "alt": ink if id(p) in accents else accent,
            "material": material if rung == "hero" else (
                "flat" if material in ("neon", "crt", "glow") else material),
            "layer": "behind_subject" if rep.get("depth") == "behind"
            else "above",
            "onset": round(on[t.i], 3), "dur": round(durs[t.i] or 0.4, 3),
            "t_in": round(t_in, 3), "t_out": round(t_out, 3),
            "verb": verb, "exit": exit_verb,
            "flip_at": round(flip, 3) if flip > 0 else None,
        }
        if p.glyphs:
            run["path"] = {"kind": "arc", "glyphs": p.glyphs}
            for k in ("cx", "cy", "rx", "ry"):
                if k in lay.opts:
                    run["path"][k] = round(float(lay.opts[k]), 2)
        if verb == "glyph_morph":
            run["morph"] = _morph_paths(t, p, sh)
        doc["runs"].append(run)
    doc["runs"].sort(key=lambda r: (r["line"], r["x"]))
    doc["solver_report"]["glyph_visible_est"] = _visible(lay, space, rep)
    doc["solver_report"]["min_clearance_pct"] = round(100.0 * min(
        space.clearance(p.ink) for p in lay.placed) / W, 2)
    doc["solver_report"]["ladder"] = _ladder(lay)
    doc["family"] = lay.family
    return _cap(doc)


def _visible(lay, space, rep):
    if rep.get("depth") != "behind":
        return 1.0
    vis = [1.0 - space.subject_share(p.ink) for p in lay.placed]
    return round(min(vis), 3) if vis else 1.0


def _ladder(lay):
    caps = [p.tok.shaped(p.size).cap for p in lay.placed]
    if not caps:
        return None
    return round(max(caps) / max(1e-6, min(caps)), 2)


def _morph_paths(tok, p, sh):
    """Glyph outlines for glyph_morph (glyph_outlines.py samples them to
    equal point counts; the page lerps)."""
    try:
        import glyph_outlines
        return glyph_outlines.morph_doc(tok.spec, tok.text, p.size, p.x, p.y,
                                        p.track_px)
    except Exception:  # noqa: BLE001
        return None


def _cap(doc):
    blob = json.dumps(doc, separators=(",", ":"))
    if len(blob) <= MAX_DOC_BYTES:
        return doc
    for r in doc["runs"]:
        r.pop("morph", None)
    blob = json.dumps(doc, separators=(",", ":"))
    if len(blob) > MAX_DOC_BYTES:
        doc["keepouts_used"] = []
    return doc


# ── the seam (motion_templates.build_job) ────────────────────────────────

def _fresh_faces(item, W, H):
    fp = (item or {}).get("footprint") or {}
    try:
        ar = float(fp.get("ar"))
    except (TypeError, ValueError):
        return []
    if abs(ar - W / float(H)) > 0.02:
        return []
    out = []
    for f in fp.get("faces") or []:
        try:
            b = [float(x) for x in f]
        except (TypeError, ValueError):
            continue
        if len(b) == 4 and b[2] > b[0] and b[3] > b[1]:
            out.append(tuple(b))
    return out[:4]


def _watermark(W, H):
    try:
        import caption_place
        wm = caption_place.watermark_box(W, H)
        if wm:
            return tuple(wm)
    except Exception:  # noqa: BLE001
        pass
    return (0.0, 0.0, 0.42, 0.13) if H / max(1.0, W) >= 1.6 else \
        (0.0, 0.0, 0.22, 0.17)


def solve_item(item, out_w, out_h, fps=30.0, faces=None):
    """The LayoutDoc of a typeset motion item on an output canvas (the
    design canvas of out_w x out_h). Cached in-process by content."""
    import motion_engine
    W, H = motion_engine.design_size(out_w, out_h)
    params = dict((item or {}).get("params") or {})
    full = float(item.get("full_duration_s") or (
        float(item["end"]) - float(item["start"])))
    words = R.tokens(params.get("text"))
    on = onsets_of(item, len(words))
    if faces is None:
        faces = _fresh_faces(item, out_w, out_h)
    wm = _watermark(out_w, out_h)
    key = hashlib.sha256(json.dumps(
        [params, W, H, round(fps, 3), round(full, 3), on, faces, wm,
         VERSION], sort_keys=True, default=str).encode()).hexdigest()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    prefer = None
    doc = build_doc(params, W=W, H=H, fps=fps, duration=full, onsets=on,
                    faces=faces, watermark=wm, prefer=prefer)
    if not doc.get("runs") and params.get("layout") not in (None, "",
                                                             "auto"):
        # a named family that does not fit THIS canvas (another aspect, a
        # face that moved) falls back to the solver's own choice: a lockup
        # whose words the captions gave up is never left undrawn
        asked = params.get("layout")
        doc = build_doc(dict(params, layout="auto"), W=W, H=H, fps=fps,
                        duration=full, onsets=on, faces=faces, watermark=wm,
                        prefer=prefer)
        doc.setdefault("solver_report", {})["layout_fallback"] = (
            f"{asked} did not fit this canvas")
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = doc
    return doc


def page_params(item, out_w, out_h, fps):
    """motion_templates.build_job's derived params for a typeset item."""
    return {"_layout": solve_item(item, out_w, out_h, fps)}


def bbox_fraction(doc):
    """The union ink box of a doc's runs in canvas fractions, or None."""
    runs = (doc or {}).get("runs") or []
    if not runs:
        return None
    W = float(doc["canvas"]["w"])
    H = float(doc["canvas"]["h"])
    x0 = min(r["ink"][0] for r in runs) / W
    y0 = min(r["ink"][1] for r in runs) / H
    x1 = max(r["ink"][2] for r in runs) / W
    y1 = max(r["ink"][3] for r in runs) / H
    return [round(max(0.0, x0), 4), round(max(0.0, y0), 4),
            round(min(1.0, x1), 4), round(min(1.0, y1), 4)]
