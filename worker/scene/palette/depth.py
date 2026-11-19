"""2.5D parallax on a still: the photograph split in depth (Depth Anything V2, or a measured stand-in) into two or
three layers with soft edges, the hole each nearer layer leaves filled from its surroundings, and a camera that
flies in past them, the near layer moving most. The layers are plain alpha pictures (cards), so the engine draws
them as it draws any card; the far layer is the moment's base picture.

The moment's base picture becomes the still for the asked frames (the still the ask names, else the still on
screen there, else the film's first still)."""
import hashlib
import os

import numpy as np

from ._util import arg, base_at, decel, frac_crop, frames, make_room, pictures, uid

LAYER = "rhythm"


def _choose(l1, ask, a):
    assets = l1.get("assets") or {}
    for p in arg(ask, "pics") or []:
        if (assets.get(p) or {}).get("kind") == "image":
            return p
    sh = base_at(l1, a)
    if sh and (assets.get(sh.get("pic")) or {}).get("kind") == "image":
        return sh["pic"]
    ims = pictures(l1, ask, kinds=("image",))
    return ims[0] if ims else None


def _feather(m, px):
    import cv2
    return np.clip(cv2.GaussianBlur(m.astype(np.float32), (0, 0), max(px, 0.5)), 0, 1)


def _fill(img, hole):
    """The picture with ``hole`` (0..1) filled from round it: inpainted small, laid in soft, a little grain kept."""
    import cv2
    h, w = img.shape[:2]
    k = 480 / max(h, w)
    sm = cv2.resize(img, (max(int(w * k), 8), max(int(h * k), 8)), interpolation=cv2.INTER_AREA)
    hm = cv2.resize((hole > 0.15).astype(np.uint8) * 255, (sm.shape[1], sm.shape[0]), interpolation=cv2.INTER_NEAREST)
    hm = cv2.dilate(hm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    filled = cv2.inpaint(cv2.cvtColor(sm, cv2.COLOR_RGB2BGR), hm, 7, cv2.INPAINT_TELEA)
    filled = cv2.cvtColor(cv2.GaussianBlur(filled, (0, 0), 2.2), cv2.COLOR_BGR2RGB)
    up = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    m = _feather(cv2.dilate((hole > 0.15).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))), 4)[..., None]
    return np.clip(img.astype(np.float32) * (1 - m) + up * m, 0, 255).astype(np.uint8)


def layers(img, d, n=3):
    """[(rgb, alpha)] far to near. Thresholds from the depth's own spread: the near layer is the nearest part,
    kept between a fifth and a half of the picture."""
    import cv2
    h, w = img.shape[:2]
    if d.shape != (h, w):
        d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
    d8 = (np.clip(d, 0, 1) * 255).astype(np.uint8)
    t_near, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    share = float((d8 > t_near).mean())
    if share < 0.12 or share > 0.55:
        t_near = float(np.percentile(d8, 72))
    near = (d8 > t_near).astype(np.uint8)
    near = cv2.morphologyEx(near, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    feather = max(min(h, w) / 400.0, 1.0)
    a_near = _feather(near, feather)
    if n >= 3:
        rest = d8[near == 0]
        t_mid = float(np.percentile(rest, 55)) if rest.size else float(t_near) * 0.6
        mid = ((d8 > t_mid) | (near > 0)).astype(np.uint8)
        mid = cv2.morphologyEx(mid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        if 0.08 < float(mid.mean()) - float(near.mean()):
            a_mid = _feather(mid, feather)
            far = _fill(img, a_mid)
            midc = _fill(img, a_near)
            return [(far, None), (midc, a_mid), (img, a_near)]
    return [(_fill(img, a_near), None), (img, a_near)]


def parallax(l1, work, ask, sense):
    import cv2
    from PIL import Image
    a, b = frames(l1, ask, default_len=45, min_len=15)
    pic = _choose(l1, ask, a)
    if pic is None:
        return {"skip": "no still to split in depth"}
    img = sense.picture(pic, long_side=1600)
    d = sense.depth(pic)
    if img is None or d is None:
        return {"skip": f"cannot read {pic}"}
    if d.shape != img.shape[:2]:
        d = cv2.resize(d, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    ls = layers(img, d, int(arg(ask, "layers", 3)))
    key = hashlib.sha1(f"{sense.path(pic)}:{len(ls)}".encode()).hexdigest()[:10]
    out = os.path.join(work, "palette", "layers")
    os.makedirs(out, exist_ok=True)
    W, H = (l1.get("frame") or [1080, 1920])[:2]
    ih, iw = img.shape[:2]
    crop = frac_crop(iw, ih, W, H, *(arg(ask, "centre") or (0.5, 0.45)), zoom=1.0)
    n = b - a
    import zlib
    side = float(arg(ask, "dir", 1 if zlib.crc32(f"{l1.get('seed')}:{ask.get('id')}".encode()) % 2 else -1))
    assets, items = {}, []
    drop, keep = make_room(l1, a, b, "pd_px")
    for i, (rgb, al) in enumerate(ls):
        aid = f"pal:dp{key}{i}"
        path = os.path.join(out, f"{key}_{i}.png")
        if not os.path.exists(path):
            if al is None:
                Image.fromarray(rgb).save(path, compress_level=3)
            else:
                Image.fromarray(np.dstack([rgb, (al * 255 + 0.5).astype(np.uint8)]), "RGBA").save(path, compress_level=3)
        assets[aid] = {"kind": "image", "path": path, "w": iw, "h": ih}
        depth_k = i / max(len(ls) - 1, 1)                       # 0 far .. 1 near
        if i == 0:
            push = 1.035
            cs = [frac_crop(iw, ih, W, H, (crop[0] + crop[2]) / 2 - side * 0.006 * t, (crop[1] + crop[3]) / 2, z)
                  for t, z in zip(decel(n, 0, 1, 2.5), decel(n, 1.0, push, 2.5))]
            items.append({"id": uid(l1, "pd_pxb"), "k": "shot", "f": [a, b], "pic": aid, "crop": [[round(v, 6) for v in c] for c in cs],
                          "blur": float(arg(ask, "far_blur", 1.2))})
        else:
            s_end = 1.0 + 0.07 * depth_k + 0.09 * depth_k * depth_k
            dx = side * (0.012 + 0.03 * depth_k)
            items.append({"id": uid(dict(l1, items=(l1.get("items") or []) + items), "pd_pxl"), "k": "card", "f": [a, b], "pic": aid,
                          "crop": [round(v, 6) for v in crop], "x": [round(0.5 + v, 6) for v in decel(n, 0.0, dx, 2.5)], "y": 0.5,
                          "w": 1.0, "h": 1.0, "s": [round(v, 6) for v in decel(n, 1.0, s_end, 2.5)], "z": -20 + i})
    return {"items": keep + items, "drop": drop, "assets": assets,
            "log": {"pic": pic, "layers": len(ls), "depth": sense.notes[-1] if sense.notes else "cached"},
            "events": [{"f": a, "kind": "flight", "strength": 0.5}]}


DEVICES = {
    "parallax": {"plan": parallax, "needs": ("still",), "fallback": ("proof_object", "leak"),
                 "card": "f pics[still] layers(2-3) dir. The photograph split in depth, the camera flying in past its layers, the "
                         "nearest moving most. For an archive photo, a group, a place, the past made present"},
}
