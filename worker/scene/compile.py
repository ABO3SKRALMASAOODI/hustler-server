"""E1: scene compile (DESIGN 4.2). EDL plus scene in; a plan the runtime can draw out.

Order: read and check; probe sources; bake every curve to arrays; lay type into the atlas; choose
passes; estimate cost and degrade past a budget; write the pull list; cut segments and hash them.
No GL and no model: this runs anywhere, and the same input gives the same bytes.
"""
import collections
import copy
import hashlib
import json
import os

import numpy as np

from . import bake, reader, segments
from . import type_atlas as TA
from .faults import SceneFault
from .plan import ANIM, ANIM_RECT, KINDS, PLAN_VERSION, Plan, canon, eligible, fingerprint, scene_of, sized

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_VERSION = "scene-1"
# what strict mode refuses to draw around: these change the picture a customer was promised
STRICT = ("missing", "not installed", "lacks a glyph", "no font here", "without exactly one")


def shaders_sha():
    d = os.path.join(HERE, "glsl")
    h = hashlib.sha256()
    for f in sorted(os.listdir(d)):
        if f.endswith(".glsl"):
            h.update(f.encode())
            h.update(open(os.path.join(d, f), "rb").read())
    return h.hexdigest()[:12]


def _f32(x):
    """Round a baked list through Float32, so a plan drawn from memory and one read back from
    curves.bin hold the same numbers."""
    return np.asarray(x, np.float32).astype(np.float64).tolist()


def _bake_item(it, warn):
    n = it["f"][1] - it["f"][0]
    for k in ANIM.get(it["k"], ()) + ANIM_RECT.get(it["k"], ()):
        if k not in it:
            continue
        v = bake.resolve(it[k], n)
        if isinstance(v, list) and v and (isinstance(v[0], (list, tuple)) or k in ANIM.get(it["k"], ())):
            if len(v) != n:
                warn.append(f"{it['id']}.{k}: {len(v)} values for {n} frames")
            v = _f32(v)
        it[k] = v
    f = it.get("fill")
    if isinstance(f, dict) and "crop" in f:
        c = bake.resolve(f["crop"], n)
        it["fill"] = dict(f, crop=_f32(c) if c and isinstance(c[0], (list, tuple)) else c)


def _rates(doc):
    """Read each video at the film's rate, unless a shot runs slower than real time."""
    fps = float(doc["fps"])
    for a in doc["assets"].values():
        a["rate"] = fps
    for it in doc["items"]:
        its = [it] + ([dict(it["fill"], f=it["f"])] if it["k"] == "word" and isinstance(it.get("fill"), dict) else [])
        for q in its:
            a = doc["assets"].get(q.get("pic"))
            if not a or a.get("kind") != "video" or not q.get("src"):
                continue
            t0, t1 = q["src"]
            real = (q["f"][1] - q["f"][0]) / fps
            if t1 != t0 and abs(t1 - t0) / max(real, 1e-6) < 0.75:
                a["rate"] = max(a["rate"], min(round(a.get("fps", fps)), 120))


def _need(doc):
    """Read each video no larger than the film needs. For every use of a source, how many of its
    texels fall on one output pixel at the tightest crop; where that is more than one, the reader
    scales it down so the tightest use is one to one, and wider uses still minify on the GPU. A 4K
    upload in a 720 preview is then decoded, scaled and uploaded at a ninth of the bytes, with
    nothing lost that the frame could show."""
    import math
    W, H = doc["frame"]
    need = {}
    for it in doc["items"]:
        refs = []
        if it["k"] in ("shot", "join"):
            refs.append((it.get("pic"), W, H, it.get("crop")))
        elif it["k"] == "card":
            sc = it.get("s", 1)
            smax = max(sc) if isinstance(sc, list) else float(sc)
            refs.append((it.get("pic"), float(it.get("w", 0.5)) * W * smax, float(it.get("h", 0.5)) * H * smax, it.get("crop")))
        elif it["k"] == "word" and isinstance(it.get("fill"), dict) and it["fill"].get("pic"):
            refs.append((it["fill"]["pic"], W, H, it["fill"].get("crop")))
        for aid, ow, oh, crop in refs:
            a = doc["assets"].get(aid)
            if not a or a.get("kind") != "video":
                continue
            nw, nh = a["w"], a["h"]
            for c in ([None] if crop is None else (crop if isinstance(crop[0], list) else [crop])):
                if c is None:                                   # the default crop: centred cover
                    ta, ra = nw / nh, ow / max(oh, 1e-6)
                    cw, ch = (ra / ta, 1.0) if ta > ra else (1.0, ta / ra)
                else:
                    cw, ch = max(c[2] - c[0], 1e-3), max(c[3] - c[1], 1e-3)
                need[aid] = max(need.get(aid, 0.0), ow / (cw * nw), oh / (ch * nh))
    for aid, k in need.items():
        a = doc["assets"][aid]
        if k < 1.0:
            a["w"], a["h"] = max(int(math.ceil(a["w"] * k / 2)) * 2, 2), max(int(math.ceil(a["h"] * k / 2)) * 2, 2)


def _type(doc, warn, tick=None):
    """Every word to a sprite in the atlas; every glyph junction to a mask. Returns (pages, masks)."""
    from concurrent.futures import ThreadPoolExecutor
    W = doc["frame"][0]
    lines, note = {}, (lambda m: m in warn or warn.append(m))
    for it in doc["items"]:
        if it["k"] != "word":
            continue
        sc = it.get("s", 1)
        s_max = max(sc) if isinstance(sc, list) else float(sc)
        ss = TA.supersample(s_max)
        br = it.get("blur", 0) or 0
        b_max = float(max(br) if isinstance(br, list) else br) * W / 1080.0
        fx = bool(it.get("stroke") or it.get("grow"))
        key = TA.sprite_key(it["t"], it.get("v"), ss, b_max) + ("|fx" if fx else "")
        if key not in lines:
            lines[key] = TA.outline_sprite(it["t"], doc["voices"].get(it.get("v")), W, ss, b_max, note, extra_pad=int(TA.SPREAD) + 2 if fx else 0)
        it["spr"] = key
    keys = sorted(lines)

    def one(k):
        r = TA.raster_sprite(lines[k])
        if tick:
            tick()
        return r
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as ex:
        built = dict(zip(keys, ex.map(one, keys)))
    pages, meta = TA.pack(built) if built else ([], {})
    doc["sprites"] = meta
    masks, mm, x = [], {}, 0
    for it in doc["items"]:
        if it["k"] == "join" and it.get("mode") == "glyph":
            key = f"{it.get('v')}|{it.get('t', '.')}"
            if key not in mm:
                arr, m = TA.build_mask(it.get("t", "."), doc["voices"].get(it.get("v")), note)
                mm[key] = dict(m, ax=x)
                masks.append(arr)
                x += arr.shape[1]
            it["mask"] = key
    doc["masks"] = mm
    sheet = None
    if masks:
        h = max(a.shape[0] for a in masks)
        sheet = np.zeros((h, x), np.uint16)
        for a, m in zip(masks, mm.values()):
            sheet[:a.shape[0], m["ax"]:m["ax"] + a.shape[1]] = a
    return pages, sheet


# ------------------------------------------------------------------ passes and cost

def program(plan):
    """Per segment (or the whole film), how many frames each op draws: the frame program in counts.
    This is what the cost estimate multiplies, and what a reader of plan.json sees the film is made of."""
    spans = plan.segments or [{"i": 0, "f": [0, plan.dur]}]
    fl = plan.film
    lens = bool(fl.get("halation") or fl.get("bloom"))
    out = []
    for s in spans:
        a, b = s["f"]
        c = collections.Counter()
        for it in plan.items:
            lo, hi = max(it["f"][0], a), min(it["f"][1], b)
            if hi <= lo:
                continue
            n = hi - lo
            k = it["k"]
            if k == "shot":
                c["plate" if it.get("pic") == "plate" else "src"] += n
                if it.get("treat"):
                    c["treat"] += n
            elif k == "card":
                c["blit_card"] += n
            elif k == "word":
                c["blit_word"] += n
            elif k == "flash":
                c["flash"] += n
            elif k == "join":
                c["join"] += n
            if it.get("behind"):
                c["under"] += n
        c["finish"] = (b - a) if (fl.get("grain") or fl.get("vignette") or lens or fl.get("mesh") or fl.get("raster")) else 0
        c["lens"] = (b - a) if lens else 0
        c["out"] = b - a
        out.append({"i": s.get("i", 0), "f": [a, b], "ops": dict(sorted(c.items()))})
    return out


def cost_table(tier):
    t = json.load(open(os.path.join(HERE, "cost_table.json")))
    return t.get(tier) or t["mac"]


def estimate(plan, tier="mac"):
    """Seconds to draw the film on this tier, from the measured per-op table (ms per frame at
    1080x1920; fill-bound ops scale with the frame's area)."""
    t = cost_table(tier)
    area = plan.W * plan.H / (1080 * 1920)
    ms = 0.0
    for seg in program(plan):
        for op, n in seg["ops"].items():
            ms += n * float(t.get(op, 0.0)) * area
    return round(ms / 1000.0 + float(t.get("start", 0.0)), 2)


DEGRADE = (("lens", "bloom and halation off"), ("easu", "edge-directed upscale off"),
           ("word_blur", "type blur-ins off"), ("under", "behind-the-subject items drawn in front"))


def degrade(doc, step):
    fl = doc.setdefault("film", {})
    if step == "lens":
        fl.pop("bloom", None)
        fl.pop("halation", None)
    elif step == "easu":
        fl["no_easu"] = True
    elif step == "word_blur":
        for it in doc["items"]:
            if it["k"] == "word":
                it.pop("blur", None)
    elif step == "under":
        for it in doc["items"]:
            it.pop("behind", None)


# ------------------------------------------------------------------ compile

def compile(doc, work, budget_s=None, tier="mac", segment=True, strict=False, tick=None, size=None):   # noqa: A001
    """EDL plus scene to a Plan. ``strict`` raises SceneFault where a lenient compile would draw
    around a hole and say so in ``plan.warn``. ``budget_s`` degrades, never fails. ``size`` draws
    the same scene at another frame size of the same shape."""
    ok, why = eligible(doc)
    if not ok:
        raise SceneFault("ineligible", "; ".join(why[:6]), reasons=why)
    l1 = scene_of(doc)
    if size:
        try:
            l1 = sized(l1, size)
        except ValueError as e:
            raise SceneFault("ineligible", str(e))
    warn = []
    d = {"plan": PLAN_VERSION, "engine": ENGINE_VERSION, "shaders": shaders_sha(), "sws": reader.SWS, "sid": l1.get("sid"), "seed": int(l1.get("seed") or 0),
         "fps": float(l1.get("fps") or 30), "frame": [int(x) for x in (l1.get("frame") or [1080, 1920])], "dur_f": int(l1["dur_f"]),
         "film": copy.deepcopy(l1.get("film") or {}), "voices": copy.deepcopy(l1.get("voices") or {}),
         "spine": copy.deepcopy(l1.get("spine") or []), "sound": copy.deepcopy(l1.get("sound") or [])}
    d["items"] = [copy.deepcopy(it) for it in (l1.get("items") or []) if it.get("k") in KINDS]
    for it in d["items"]:
        it["f"] = [int(it["f"][0]), int(it["f"][1])]
    order = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    d["items"].sort(key=lambda i: (i["f"][0], order[i["k"]], i.get("z", 0), str(i["id"])))
    # sources
    d["assets"] = {}
    for aid, a in sorted((l1.get("assets") or {}).items()):
        p = a.get("path") or ""
        ab = p if os.path.isabs(p) else os.path.join(work, p)
        if not os.path.exists(ab):
            warn.append(f"asset {aid} missing: {p}")
            continue
        o = {"kind": a.get("kind"), "path": p, "sha": a.get("sha") or fingerprint(ab)}
        if a.get("kind") == "video":
            o.update(reader.probe(ab))
            if not o["w"] or not o["h"]:
                warn.append(f"asset {aid} missing: no picture in {p}")
                continue
            o["w"], o["h"] = reader.fit(o["w"], o["h"])
        d["assets"][aid] = o
    _rates(d)
    for it in d["items"]:
        _bake_item(it, warn)
        if it["k"] in ("shot", "card", "join") and it.get("pic") not in (None, "plate") and it["pic"] not in d["assets"]:
            warn.append(f"asset missing for {it['id']}: {it['pic']}")
    _need(d)
    pages, masks = _type(d, warn, tick)
    steps = []
    plan = None
    for step in (None,) + tuple(s for s, _ in DEGRADE):
        if step:
            degrade(d, step)
            steps.append(step)
        d["segments"] = segments.cut(d) if segment else []
        plan = Plan(copy.deepcopy(d), work, pages, masks)
        d["cost_s"] = estimate(plan, tier)
        if budget_s is None or d["cost_s"] <= budget_s:
            break
    d["degraded"] = steps
    d["program"] = program(plan)
    full = plan.demand(range(d["dur_f"]))
    pull = collections.defaultdict(list)
    for f in sorted(full):
        for aid, n in full[f]:
            r = pull[aid]
            if r and r[-1][1] in (n, n - 1):
                r[-1][1] = max(r[-1][1], n)
            elif not r or not (r[-1][0] <= n <= r[-1][1]):
                r.append([n, n])
    d["pull"] = {k: v for k, v in sorted(pull.items())}
    d["warnings"] = warn
    head = {k: d[k] for k in ("plan", "engine", "shaders", "sws", "seed", "fps", "frame", "dur_f", "film", "voices", "items", "spine", "sound")}
    head["assets"] = {k: {x: y for x, y in a.items() if x != "abs"} for k, a in d["assets"].items()}
    d["sha"] = hashlib.sha256(canon(head).encode()).hexdigest()[:24]
    if segment:
        segments.stamp(d)
    if strict:
        bad = [w for w in warn if any(s in w for s in STRICT)]
        if bad:
            raise SceneFault("plan", "; ".join(bad[:6]), warnings=bad)
    return Plan(d, work, pages, masks)
