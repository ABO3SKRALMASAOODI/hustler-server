"""Edit variants (Wave 2, track 2F): EDL branches as cheap previews.

``branches(edl, axis, n, item_id)`` returns ``n`` EDL branches that differ
along ONE axis — ``voice`` (a typeset item set in n distinct type voices,
as far apart as the voices go) or ``pack`` (the story planner's plan under
n different format packs). Nothing is written: the user picks, the agent
applies the pick with the ordinary verb.

``raster(doc, frame)`` draws a solved LayoutDoc over a frame with PIL (the
shaped glyph positions, sizes, colours of the page; no materials or motion)
so three voices are compared as stills in seconds on any lane, without a
browser.
"""

import copy
import hashlib
import os

import numpy as np


def _font_path(fname):
    import motion_engine
    for d in (getattr(motion_engine, "MOTION_FONTS_DIR", None),
              getattr(motion_engine, "FONTS_DIR", None)):
        if d and os.path.exists(os.path.join(d, fname)):
            return os.path.join(d, fname)
    return None


def distinct_voices(text, n=3, seed=0, exclude=()):
    """``n`` voices as different from each other as the set allows: the
    first by the story (creative.pick_voice), each next one maximally far
    in family / hero share / material from those chosen."""
    import creative
    from typeset import voices as V
    first, _why = creative.pick_voice(text, seed=seed, avoid=tuple(exclude))
    chosen = [first]

    def feat(v):
        d = V.VOICES[v]
        return (str(d.get("material")), round(float(d.get("hero_share",
                                                            0.5)), 1),
                tuple(sorted(d.get("layouts") or ()))[:1])
    while len(chosen) < n:
        best = None
        for v in V.VOICES:
            if v in chosen or v in exclude:
                continue
            dist = sum(sum(a != b for a, b in zip(feat(v), feat(c)))
                       for c in chosen)
            tie = int(hashlib.sha256(f"{seed}:{v}".encode()).hexdigest()[:4],
                      16)
            key = (dist, tie)
            if best is None or key > best[0]:
                best = (key, v)
        if best is None:
            break
        chosen.append(best[1])
    return chosen


def branches(edl, axis="voice", n=3, item_id=None, seed=0):
    """[(label, edl branch)] along one axis."""
    n = max(2, min(4, int(n or 3)))
    if axis != "voice":
        raise ValueError("axis is 'voice' (packs branch through "
                         "story_planner)")
    items = [m for m in edl.get("motion") or []
             if m.get("template") == "typeset" and (item_id is None or
                                                    m.get("id") == item_id)]
    if not items:
        raise ValueError("no typeset item to vary")
    it = items[0]
    text = (it.get("params") or {}).get("text", "")
    out = []
    for v in distinct_voices(text, n, seed):
        e = copy.deepcopy(edl)
        for m in e.get("motion") or []:
            if m.get("id") == it["id"]:
                m["params"] = dict(m.get("params") or {}, voice=v,
                                   layout="auto")
                m.pop("footprint", None)
        out.append((v, e))
    return out


def raster(doc, frame_rgb):
    """The LayoutDoc drawn over ``frame_rgb`` (H x W x 3 uint8 at the
    doc's canvas size)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.fromarray(frame_rgb).convert("RGB")
    W, H = img.size
    cw, ch = doc["canvas"]["w"], doc["canvas"]["h"]
    if (W, H) != (cw, ch):
        img = img.resize((cw, ch))
    # dim the plate a little so the type reads as it will over motion
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), 0.25)
    draw = ImageDraw.Draw(img)
    for r in doc.get("runs") or []:
        path = _font_path((r.get("font") or {}).get("file") or "")
        if not path:
            continue
        try:
            font = ImageFont.truetype(path, max(6, int(round(r["size_px"]))))
            axes = (r.get("font") or {}).get("axes") or {}
            if axes:
                try:
                    font.set_variation_by_axes(
                        [axes[k] for k in sorted(axes)])
                except Exception:  # noqa: BLE001 — default instance
                    pass
        except OSError:
            continue
        col = str(r.get("colour") or "#FFFFFF")
        for ch_, x in zip(r["text"], r.get("chars_x") or []):
            draw.text((x, r["y"]), ch_, font=font, fill=col, anchor="ls")
    return np.asarray(img)


def sheet(images, labels, out_path, tile_h=640):
    """A side-by-side sheet of previews with their labels."""
    from PIL import Image, ImageDraw
    tiles = []
    for im, lab in zip(images, labels):
        t = Image.fromarray(im)
        w = int(round(t.width * tile_h / t.height))
        t = t.resize((w, tile_h))
        d = ImageDraw.Draw(t)
        d.rectangle([0, 0, w, 36], fill=(0, 0, 0))
        d.text((10, 8), lab, fill=(255, 255, 255))
        tiles.append(t)
    W = sum(t.width for t in tiles) + 8 * (len(tiles) - 1)
    out = Image.new("RGB", (W, tile_h), (20, 20, 20))
    x = 0
    for t in tiles:
        out.paste(t, (x, 0))
        x += t.width + 8
    out.save(out_path, quality=88)
    return out_path
