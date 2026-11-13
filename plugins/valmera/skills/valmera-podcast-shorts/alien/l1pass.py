"""The plan between the Director and the engine: placement is repaired, and the film is drawn in two passes.

Pass 1 is the Director's pictures (shots, cards, words that stand behind the person) with the film's
mixed sound. The alien passes work on that film. Pass 2 lays the type and the flashes over the result,
so type is never relit and never dimmed.

Everything here reads numbers of the baked plan (L1 contract v1). Nothing reads a meaning.
"""
import copy
import os

SAFE = (0.05, 0.10, 0.95, 0.80)          # TASTE 4.3 P2: x0, y0, x1, y1 for all added type and cards
CHROME_X, CHROME_Y = 0.86, 0.55          # and left of x 0.86 between y 0.55 and 0.80
ASC, DESC = 0.78, 0.24                   # a line's reach above and below its baseline, in font sizes


def _font_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    out = []
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.isdir(os.path.join(d, "worker", "fonts")):
            out.append(os.path.join(d, "worker", "fonts"))
            break
    out.append("/Users/masaoodi/Documents/Valmera/.valmera/week-1106/director/slice/fonts_extra")
    return out


_FONTS = {}


def text_w(voice, text, W):
    """Width of a word in frame fractions, measured with the voice's own font file."""
    from PIL import ImageFont
    px = float(voice.get("px") or 48)
    key = (voice.get("font"), round(px))
    if key not in _FONTS:
        f = None
        for d in _font_dirs():
            p = os.path.join(d, str(voice.get("font")))
            if os.path.exists(p):
                f = ImageFont.truetype(p, int(round(px)))
                break
        _FONTS[key] = f
    f = _FONTS[key]
    w = f.getlength(text) if f else 0.55 * px * len(text)
    return (w + float(voice.get("track") or 0) / 100.0 * px * len(text)) / W


def _span(v):
    return (min(v), max(v)) if isinstance(v, list) else (v, v)


def _rest(v):
    """Where an animated value comes to rest: a settle or a rise ends on the item's own place."""
    return v[-1] if isinstance(v, list) else v


def _shift(v, d):
    return [round(x + d, 5) for x in v] if isinstance(v, list) else round(v + d, 5)


def word_box(it, l1):
    """[x0, y0, x1, y1] a word can touch over its life."""
    W, H = l1["frame"]
    v = l1["voices"].get(it.get("v")) or {}
    s = float(_rest(it.get("s", 1)))
    px = float(v.get("px") or 48) * s
    x, y = float(_rest(it["x"])), float(_rest(it["y"]))
    return [x, y - ASC * px / H, x + text_w(v, it.get("t") or "", W) * s, y + DESC * px / H]


def groups(l1):
    """Words that are read together move together: a caption chunk, or the words of one display line."""
    out = {}
    for it in l1["items"]:
        if it.get("k") != "word":
            continue
        key = it.get("chunk") or it["id"].rsplit(".", 1)[0]
        out.setdefault(key, []).append(it)
    return out


def held_card(it):
    """A card that sits beside the picture, not a transition piece and not a full-frame insert."""
    a, b = it["f"]
    return it.get("k") == "card" and b - a >= 12 and it.get("w", 1) < 0.9 and it.get("h", 1) < 0.9


def card_box(it):
    s = float(_rest(it.get("s", 1)))
    x, y = float(_rest(it["x"])), float(_rest(it["y"]))
    hw, hh = it["w"] * s / 2, it["h"] * s / 2
    return [x - hw, y - hh, x + hw, y + hh]


def _move_into_safe(box):
    """(dx, dy) that brings a box inside the safe area; a box wider than the area is centred on it."""
    x0, y0, x1, y1 = box
    sx0, sy0, sx1, sy1 = SAFE
    if y0 < CHROME_Y + 0.25 and y1 > CHROME_Y and x1 - x0 <= CHROME_X - sx0 and (x0 + x1) / 2 > 0.51:
        sx1 = CHROME_X                       # a seat right of centre keeps clear of the platform's buttons; a centred line stays centred
    dx = dy = 0.0
    if x1 - x0 > sx1 - sx0:
        dx = (sx0 + sx1) / 2 - (x0 + x1) / 2
    elif x1 > sx1:
        dx = sx1 - x1
    elif x0 < sx0:
        dx = sx0 - x0
    if y1 - y0 > sy1 - sy0:
        dy = (sy0 + sy1) / 2 - (y0 + y1) / 2
    elif y1 > sy1:
        dy = sy1 - y1
    elif y0 < sy0:
        dy = sy0 - y0
    return dx, dy


def units(l1):
    """What moves as one thing: the words of a line or chunk, with the plate they stand on (a chip's card)."""
    out = {key: list(ws) for key, ws in groups(l1).items()}
    for it in l1["items"]:
        if held_card(it) and not it.get("behind"):
            key = it["id"] if it["id"] in out else it["id"].rsplit(".", 1)[0]
            out.setdefault(key if key in out else it["id"], []).append(it)
    return out


def unit_box(items, l1):
    boxes = [word_box(it, l1) if it.get("k") == "word" else card_box(it) for it in items]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


EDGE = 0.008                             # a caption on a card's edge: its descenders this far above the card's top


def repair_placement(l1):
    """TASTE P2 as a repair: type and held cards outside the safe area move inside. Never dropped for this.
    Type that would then stand on a moved card takes the card's top edge as its seat (P1: the seat changes
    only onto the edge of a card that is on screen)."""
    moved = []
    U = units(l1)
    for key, items in U.items():
        box = unit_box(items, l1)
        dx, dy = _move_into_safe(box)
        if abs(dx) > 1e-4 or abs(dy) > 1e-4:
            for it in items:
                it["x"], it["y"] = _shift(it["x"], dx), _shift(it["y"], dy)
            moved.append(dict(id=key, dx=round(dx, 4), dy=round(dy, 4), was=[round(v, 3) for v in box]))
    went = {m["id"] for m in moved}
    for key, items in U.items():
        card = next((it for it in items if it.get("k") == "card"), None)
        if card is None or key not in went or any(it.get("k") == "word" for it in items):
            continue
        cb = card_box(card)
        for k2, ws in U.items():
            if any(it.get("k") != "word" for it in ws):
                continue
            a, b = min(w["f"][0] for w in ws), max(w["f"][1] for w in ws)
            both = min(b, card["f"][1]) - max(a, card["f"][0])
            box = unit_box(ws, l1)
            if both * 2 < b - a or box[2] <= cb[0] or box[0] >= cb[2] or box[3] <= cb[1] - 0.004 or box[1] >= cb[3]:
                continue
            dy = cb[1] - EDGE - box[3]
            for w in ws:
                w["y"] = _shift(w["y"], dy)
            moved.append(dict(id=k2, dx=0.0, dy=round(dy, 4), rides=key))
    return moved


def outside_safe(l1, tol=0.004):
    """What is still outside after the repair, measured the same way: the check, not the repair's own word."""
    bad = []
    sx0, sy0, sx1, sy1 = SAFE
    for key, items in units(l1).items():
        box = unit_box(items, l1)
        if box[2] - box[0] > sx1 - sx0 or box[3] - box[1] > sy1 - sy0:
            continue                                   # wider than the area: centred on it
        if box[0] < sx0 - tol or box[1] < sy0 - tol or box[2] > sx1 + tol or box[3] > sy1 + tol:
            bad.append(dict(id=key, box=[round(v, 3) for v in box]))
    return bad


def sample_frames(a, b):
    """Three moments of an item's life: a picture under it may be lit differently at each."""
    return sorted({min(a + 1, b - 1), (a + b) // 2, max(b - 2, a)})


def repair_faces(l1, T, cap_top=0.585):
    """TASTE P3 as a repair: display type that stands on a face (more than 15% of either box, on any of three moments
    of its life) moves under the chin, above the caption seat; when there is no room it is thinned. ``T`` is the
    face track of the picture the type will stand on (sidecars.track). Captions and chips are not touched here:
    the caption seat is solved off the face by the compiler."""
    import numpy as np
    out = []
    for key, items in list(units(l1).items()):
        ws = [it for it in items if it.get("k") == "word"]
        if not ws or any(it.get("k") == "card" for it in items) or any(w.get("role") == "cap" or w.get("behind") for w in ws):
            continue
        a, b = min(w["f"][0] for w in ws), max(w["f"][1] for w in ws)
        box = unit_box(ws, l1)
        hit, chin = False, 0.0
        for f in sample_frames(a, b):
            if f >= len(T) or np.isnan(T[f, 0]):
                continue
            fx, fy, fw, fh = [float(v) for v in T[f, :4]]
            face = [fx - 0.05 * fw, fy - 0.05 * fh, fx + 1.05 * fw, fy + 1.05 * fh]
            ix = min(box[2], face[2]) - max(box[0], face[0])
            iy = min(box[3], face[3]) - max(box[1], face[1])
            if ix > 0 and iy > 0:
                inter = ix * iy
                if inter > 0.15 * (box[2] - box[0]) * (box[3] - box[1]) or inter > 0.15 * (face[2] - face[0]) * (face[3] - face[1]):
                    hit, chin = True, max(chin, face[3])
        if not hit:
            continue
        dy = chin + 0.015 - box[1]
        if box[3] + dy <= cap_top:
            for w in ws:
                w["y"] = _shift(w["y"], dy)
            out.append(dict(id=key, moved_dy=round(dy, 4)))
        else:
            gone = {id(w) for w in ws}
            l1["items"] = [it for it in l1["items"] if id(it) not in gone and it.get("id") != f"{key}.pool"]
            out.append(dict(id=key, thinned=True, text=" ".join(w.get("t", "") for w in ws)[:60]))
    return out


def _lum(hexc):
    c = [int(hexc[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def repair_contrast(l1, gray_frames):
    """TASTE P4 as a repair, measured on the picture the type will stand on: a unit of type whose ink is under
    WCAG 4.5 (3.0 for display sizes) against what is under it has its ink flipped, and a soft dark pool under it
    when the flip alone is not enough. ``gray_frames`` maps a
    programme frame to that picture's relative luminance (a small float array)."""
    H = l1["frame"][1]
    pooled = []
    for key, items in units(l1).items():
        ws = [it for it in items if it.get("k") == "word" and not it.get("behind")]
        if not ws or any(it.get("k") == "card" for it in items) or any(w.get("grounded") for w in ws):
            continue                                   # type on a card, or type that brings its own ground, keeps its ink
        a, b = min(w["f"][0] for w in ws), max(w["f"][1] for w in ws)
        gs = [g for g in (gray_frames.get(f) for f in sample_frames(a, b)) if g is not None]
        if not gs:
            continue
        box = unit_box(ws, l1)
        gh, gw = gs[0].shape
        regs = [g[max(int(box[1] * gh), 0):max(int(box[3] * gh) + 1, 1), max(int(box[0] * gw), 0):max(int(box[2] * gw) + 1, 1)] for g in gs]
        if not regs[0].size:
            continue
        import numpy as np
        los, his = [float(np.percentile(r, 15)) for r in regs], [float(np.percentile(r, 85)) for r in regs]
        px = max(float((l1["voices"].get(w.get("v")) or {}).get("px") or 48) for w in ws)
        need = 4.5 if px / H < 0.05 else 3.0

        def ratio(ink):
            return min((max(ink, v) + 0.05) / (min(ink, v) + 0.05) for v in los + his)       # the worst moment of its life

        now = ratio(_lum(ws[0].get("ink") or "#FFFFFF"))
        if now >= need:
            continue
        fix = dict(id=key, ratio=round(now, 2), need=need)
        if min(los) > 0.30 and ratio(_lum(DARK_INK)) >= need:            # a bright ground all its life (a paper, a white wall):
            for w in ws:                                                 # dark ink reads, and no pool smudges the picture
                w["ink"] = DARK_INK
            fix["ink"] = DARK_INK
            pooled.append(fix)
            continue
        ink = "#FFFFFF"                                                  # white holds on a picture whose light moves
        for w in ws:
            w["ink"] = ink
        fix["ink"] = ink
        if ratio(1.0) < need:                                            # the flip alone is not enough: a pool under it
            l1["items"].append(dict(id=f"{key}.pool", k="flash", f=[a, b], mode="pool", a=0.72, z=0,
                                    rect=[round(max(box[0] - 0.06, 0), 4), round(max(box[1] - 0.025, 0), 4), round(min(box[2] + 0.06, 1), 4), round(min(box[3] + 0.025, 1), 4)]))
            fix["pool"] = True
        pooled.append(fix)
    l1["items"].sort(key=lambda it: it["f"][0])
    return pooled


def picture_cuts(l1):
    """Frames where the film cuts between two different pictures (the take and a found picture, two found pictures)."""
    shots = sorted((it for it in l1["items"] if it.get("k") == "shot"), key=lambda it: it["f"][0])
    return [b["f"][0] for a, b in zip(shots, shots[1:]) if a.get("pic") != b.get("pic")]


def split_captions_at(l1, cuts, least=3):
    """A caption chunk that lives across one of these frames becomes two chunks there: the ground under it changes
    (a cut to another picture, a card landing), so its ink or its seat may too, instead of one ink and a pool that
    smudges a paper. A word that lives on both sides is cut in two, each half keeping its place and its own frames
    of every per-frame list; a word within ``least`` frames of the frame goes whole to the side it lives on.
    Returns how many chunks were divided."""
    n = 0
    for c in sorted(set(int(x) for x in cuts)):
        life = {}
        for it in l1["items"]:
            if it.get("k") == "word" and it.get("role") == "cap" and not it.get("behind"):
                key = it.get("chunk") or it["id"]
                a, b = life.get(key, (it["f"][0], it["f"][1]))
                life[key] = (min(a, it["f"][0]), max(b, it["f"][1]))
        across = {k for k, (a, b) in life.items() if a + least <= c <= b - least}
        if not across:
            continue
        out = []
        for it in l1["items"]:
            key = (it.get("chunk") or it.get("id")) if it.get("k") == "word" and it.get("role") == "cap" and not it.get("behind") else None
            if key not in across:
                out.append(it)
                continue
            a, b = it["f"]
            later = f"{it['chunk']}~{c}" if it.get("chunk") else None
            if b <= c + least and a < c - least:                 # it lives before the frame
                out.append(it)
            elif a >= c - least:                                 # it lives after it
                out.append(dict(it, chunk=later) if later else it)
            elif b - c < least or c - a < least:
                out.append(it if c - a >= b - c else (dict(it, chunk=later) if later else it))
            else:
                first, second = dict(it, f=[a, c]), dict(it, f=[c, b], id=f"{it['id']}~{c}", on=c)
                if later:
                    second["chunk"] = later
                for k2, v in it.items():
                    if isinstance(v, list) and k2 != "f" and len(v) == b - a:
                        first[k2], second[k2] = v[:c - a], v[c - a:]
                out += [first, second]
        l1["items"] = sorted(out, key=lambda it: it["f"][0])
        n += len(across)
    return n


IDENTITY = dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0)
DARK_INK = "#0E0F11"


def split(l1, lit_path):
    """(pass 1, pass 2). Pass 1: pictures and the sound. Pass 2: one shot of the lit film, then flashes and type."""
    p1 = copy.deepcopy(l1)
    p1["items"] = [it for it in l1["items"] if it.get("k") not in ("word", "flash") or (it.get("k") == "word" and it.get("behind"))]
    p1["film"] = dict(l1.get("film") or {}, grain=0, vignette=0, over_type=False)
    n = int(l1["dur_f"])
    W, H = l1["frame"]
    fps = l1.get("fps", 30)
    p2 = dict((k, copy.deepcopy(v)) for k, v in l1.items() if k not in ("assets", "spine", "items", "sound", "film"))
    p2["assets"] = {"main": dict(kind="video", path=lit_path, w=W, h=H, dur=round(n / fps, 4), fps=fps)}
    p2["spine"] = []
    p2["sound"] = []
    film = l1.get("film") or {}
    p2["film"] = dict(grain=film.get("grain", 0), vignette=film.get("vignette", 0), over_type=film.get("over_type", False), grade=dict(IDENTITY))
    top = [copy.deepcopy(it) for it in l1["items"] if it.get("k") == "flash" or (it.get("k") == "word" and not it.get("behind"))]
    p2["items"] = [dict(id="alien.lit", k="shot", f=[0, n], pic="main", src=[0.0, round(n / fps, 4)], crop=[0, 0, 1, 1])] + top
    return p1, p2


def cuts(l1):
    return sorted({int(it["f"][0]) for it in l1["items"] if it.get("k") == "shot"})


def picture_shots(l1):
    """Frame spans whose shot is not the take: a library picture, a plate."""
    return [tuple(it["f"]) for it in l1["items"] if it.get("k") == "shot" and it.get("pic") != "main" and not str(it.get("pic", "")).startswith(("own:", "cut:"))]


def cards(l1):
    """Held cards as (f0, f1, box): a picture standing in the frame."""
    return [(it["f"][0], it["f"][1], card_box(it)) for it in l1["items"] if held_card(it)]


def take_stretches(l1):
    """Runs of the take in one framing: consecutive shots of the main picture with the same fixed crop. A speech edit
    or a beat boundary inside the run is still the same camera."""
    out = []
    shots = sorted((it for it in l1["items"] if it.get("k") == "shot"), key=lambda it: it["f"][0])
    for it in shots:
        crop = it.get("crop")
        fixed = isinstance(crop, list) and len(crop) == 4 and not isinstance(crop[0], list)
        key = tuple(round(float(v), 3) for v in crop) if it.get("pic") == "main" and fixed else None
        if key is not None and out and out[-1][2] == key and out[-1][1] == it["f"][0]:
            out[-1][1] = it["f"][1]
        else:
            out.append([it["f"][0], it["f"][1], key])
    return [(a, b) for a, b, key in out if key is not None]
