"""Layout stage helpers (Wave 3, 3E): the truthful breakdown, the brand kit.

breakdown_doc(edl, ctx)  the program's REAL structure for the breakdown
                         page: lanes of what plays when, with the true device
                         names (cards' descriptions are not used: the card
                         id and the template / type ARE the names), the cut
                         list, the program length — truthful by construction
brand_palette(path)      a logo's palette: k-means in Lab (OpenCV), the
                         dominant non-neutral colour as the accent
"""

import numpy as np

MAX_ITEMS_PER_LANE = 40


def _cuts(edl, tl):
    out, acc = [], 0.0
    for a, b in (edl or {}).get("keep") or []:
        acc += float(b) - float(a)
        out.append(round(acc, 3))
    return [c for c in out[:-1] if 0 < c < float(tl.out_duration)] \
        if tl is not None else out[:-1]


def breakdown_doc(edl, ctx):
    """{duration, start, lanes: [{name, items: [{a, b, label}]}], cuts}."""
    edl = edl or {}
    tl = getattr(ctx, "tl", None)
    dur = float(getattr(tl, "out_duration", 0.0) or 0.0)
    lanes = []

    def lane(name, items, colour=None):
        items = sorted(items, key=lambda i: i["a"])[:MAX_ITEMS_PER_LANE]
        if items:
            row = {"name": name, "items": items}
            if colour:
                row["colour"] = colour
            lanes.append(row)
    # picture: the kept source spans as program blocks
    blocks, acc = [], 0.0
    for a, b in edl.get("keep") or []:
        d = float(b) - float(a)
        blocks.append({"a": round(acc, 3), "b": round(acc + d, 3),
                       "label": f"src {float(a):.1f}s"})
        acc += d
    lane("cut", blocks, "rgba(255,255,255,.14)")
    gfx = []
    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("template") == "breakdown":
            continue
        lab = str(m.get("template") or "graphic")
        if m.get("anchor"):
            lab += f" @{(m['anchor'] or {}).get('to')}"
        gfx.append({"a": round(float(m["start"]), 3),
                    "b": round(float(m["end"]), 3), "label": lab})
    lane("graphics", gfx, "rgba(79,163,255,.45)")
    fx = []
    world = []
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        a = it.get("start") if it.get("start") is not None else it.get("at")
        b = it.get("end") if it.get("end") is not None else (
            float(a) + 0.2 if a is not None else None)
        if a is None or b is None:
            continue
        lab = it.get("device") or it.get("type")
        kind = it.get("kind") or it.get("effect") or it.get("style")
        if kind:
            lab = f"{lab} · {kind}"
        row = {"a": round(float(a), 3), "b": round(float(b), 3),
               "label": str(lab)}
        if it.get("type") in ("track", "object", "plane"):
            row["label"] = f"{it['type']}: {it.get('label') or it['id']}"
            world.append(row)
        else:
            fx.append(row)
    lane("devices", fx, "rgba(255,90,31,.45)")
    lane("world", world, "rgba(80,220,140,.4)")
    fxs = (edl.get("effects") or {})
    zooms = [{"a": round(float(z["start"]), 3), "b": round(float(z["end"]),
                                                        3),
              "label": "zoom"} for z in fxs.get("zooms") or []
             if isinstance(z, dict) and z.get("start") is not None]
    lane("camera", zooms)
    audio = []
    for mu in edl.get("music") or []:
        if isinstance(mu, dict):
            a = float(mu.get("start") or 0.0)
            b = float(mu.get("end") or dur)
            audio.append({"a": round(a, 3), "b": round(b, 3),
                          "label": "music (user)"})
    for s in edl.get("sfx") or []:
        if isinstance(s, dict) and s.get("at") is not None:
            audio.append({"a": round(float(s["at"]), 3),
                          "b": round(float(s["at"]) + 0.4, 3),
                          "label": str(s.get("id") or "sfx")})
    lane("sound", audio)
    caps = edl.get("captions") or {}
    if isinstance(caps, dict) and caps.get("mode") == "from_transcript":
        lane("captions", [{"a": 0.0, "b": round(dur, 3),
                           "label": "captions (every heard word)"}])
    return {"duration": round(dur, 3), "start": 0.0, "lanes": lanes,
            "cuts": _cuts(edl, tl)}


def brand_palette(path, k=5):
    """{"colours": [#hex...] by share, "accent": #hex, "ink": #hex} from a
    logo image (transparent pixels ignored)."""
    import cv2
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("the logo could not be read")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    alpha = img[..., 3] if img.shape[2] == 4 else None
    bgr = img[..., :3]
    small = cv2.resize(bgr, (min(256, bgr.shape[1]),
                             min(256, bgr.shape[0])),
                       interpolation=cv2.INTER_AREA)
    px = small.reshape(-1, 3)
    if alpha is not None:
        a = cv2.resize(alpha, (small.shape[1], small.shape[0]),
                       interpolation=cv2.INTER_AREA).reshape(-1)
        px = px[a > 128]
    if len(px) < k:
        raise ValueError("the logo has too few visible pixels")
    lab = cv2.cvtColor(px.reshape(-1, 1, 3).astype(np.uint8),
                       cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _c, labels, centres = cv2.kmeans(lab, k, None, crit, 3,
                                     cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.reshape(-1), minlength=k)
    order = np.argsort(-counts)
    cols = []
    for i in order:
        bgr_c = cv2.cvtColor(centres[i].reshape(1, 1, 3).astype(np.uint8),
                             cv2.COLOR_LAB2BGR).reshape(3)
        cols.append((f"#{int(bgr_c[2]):02x}{int(bgr_c[1]):02x}"
                     f"{int(bgr_c[0]):02x}", centres[i], int(counts[i])))
    # the accent: the most chromatic colour with real presence
    def chroma(c):
        return float(np.hypot(c[1] - 128.0, c[2] - 128.0))
    vivid = [c for c in cols if chroma(c[1]) > 18 and c[2] > 0.03 * len(px)]
    accent = (max(vivid, key=lambda c: chroma(c[1]))[0] if vivid
              else cols[0][0])
    ink = min(cols, key=lambda c: c[1][0])[0]
    return {"colours": [c[0] for c in cols], "accent": accent, "ink": ink}
