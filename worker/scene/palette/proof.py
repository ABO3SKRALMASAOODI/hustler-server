"""A proof object: the document, the page or the screen the claim comes from, lying tilted on a desk with depth of
field, then lifted flat while the camera pushes into the line that proves the claim, and a highlighter drawn
across it (the references: a profile 'Backed by Y Combinator' on a tilted card, Forbes on a desk).

The line is measured on the picture (``sense.text_boxes``: the most prominent line of text) unless the ask names
its box. A picture with no line of text (a photograph) is pushed into its largest face, else its brightest light, and
carries no highlighter: a mark is laid only across a line that is there. This is the scene palette's proof object; the marketing workflow's own proof.py is another thing."""
from ._util import arg, base_at, decel, ease, frames, make_room, pictures, uid

LAYER = "rhythm"


def _doc(l1, ask, a, sense):
    assets = l1.get("assets") or {}
    for p in arg(ask, "pics") or []:
        if (assets.get(p) or {}).get("kind") == "image":
            return p
    sh = base_at(l1, a)
    if sh and (assets.get(sh.get("pic")) or {}).get("kind") == "image":
        return sh["pic"]
    ims = pictures(l1, ask, kinds=("image",))
    for p in ims:                       # the still that reads most like a page
        if sense.text_boxes(p):
            return p
    return ims[0] if ims else None


def _interest(sense, pic):
    """(box, how) the push goes into when the ask names no box: the most prominent line of text measured on the picture
    ("measured"), else the largest face ("face"), else the picture's brightest light ("light"). Fractions."""
    tb = sense.text_boxes(pic)
    if tb:
        return list(tb[0]), "measured"
    try:
        fc = sense.faces(pic)
    except Exception:      # noqa: BLE001 - no face read: the light
        fc = []
    if fc:
        x0, y0, x1, y1 = max(fc, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))[:4]
        px, py = (x1 - x0) * 0.6, (y1 - y0) * 0.5
        return [max(x0 - px, 0.0), max(y0 - py, 0.0), min(x1 + px, 1.0), min(y1 + py, 1.0)], "face"
    cx, cy = sense.bright(pic)
    return [max(cx - 0.22, 0.0), max(cy - 0.16, 0.0), min(cx + 0.22, 1.0), min(cy + 0.16, 1.0)], "light"


def proof_object(l1, work, ask, sense):
    a, b = frames(l1, ask, default_len=66, min_len=30)
    pic = _doc(l1, ask, a, sense)
    if pic is None:
        return {"skip": "no document or still to lay on the desk"}
    asset = l1["assets"][pic]
    W, H = (l1.get("frame") or [1080, 1920])[:2]
    iw, ih = asset.get("w"), asset.get("h")
    if not iw or not ih:
        img = sense.picture(pic, long_side=800)
        ih, iw = img.shape[:2]
    box = arg(ask, "box")
    found = "asked"
    if not box:
        box, found = _interest(sense, pic)
    bx0, by0, bx1, by1 = [float(v) for v in box]
    line = found in ("asked", "measured")       # a line of text to push into and mark; else a face or the light
    # the card: the picture's shape, as wide as the frame allows
    w = 0.84
    h = w * W / (iw / ih) / H
    if h > 0.62:
        h = 0.62
        w = h * H * (iw / ih) / W
    # the push: the line fills the frame's width; a face or the light is pushed into, never past a close-up
    S = min(max(0.86 / max((bx1 - bx0) * w, 1e-3), 1.0), 3.4 if line else 2.0)
    xe = 0.5 - ((bx0 + bx1) / 2 - 0.5) * w * S
    ye = 0.5 - ((by0 + by1) / 2 - 0.5) * h * S
    n = b - a
    p1, p2 = int(n * 0.42), int(n * 0.72)
    tilt0 = (float(arg(ask, "rx", 52.0)), float(arg(ask, "ry", -18.0)), float(arg(ask, "rot", -9.0)))
    mid = (tilt0[0] * 0.42, tilt0[1] * 0.4, tilt0[2] * 0.35)

    def run(v0, v1, v2, fn1=decel, fn2=ease):
        return [round(x, 5) for x in fn1(p1, v0, v1) + fn2(p2 - p1, v1, v2) + [v2] * (n - p2)]
    rx, ry, rot = run(tilt0[0], mid[0], 0.0), run(tilt0[1], mid[1], 0.0), run(tilt0[2], mid[2], 0.0)
    s = run(0.8, 0.9, S)
    x = run(0.5, 0.5, xe)
    y = run(0.58, 0.52, ye)
    drop, keep = make_room(l1, a, b, "pd_pf")
    desk = arg(ask, "desk", "plate")
    if desk == "blur":
        base = {"id": uid(l1, "pd_pfd"), "k": "shot", "f": [a, b], "pic": pic, "blur": 38.0,
                "grade": {"lift": 0, "gamma": 1, "gain": 0.45, "sat": 0.6, "contrast": 1, "temp": 0, "tint": 0}}
    else:
        base = {"id": uid(l1, "pd_pfd"), "k": "shot", "f": [a, b], "pic": "plate",
                "plate": {"c0": arg(ask, "c0", "#3A3029"), "c1": arg(ask, "c1", "#0E0B09"), "grain": 0.05}}
    card = {"id": uid(l1, "pd_pfc"), "k": "card", "f": [a, b], "pic": pic, "crop": [0, 0, 1, 1], "x": x, "y": y, "w": round(w, 5),
            "h": round(h, 5), "s": s, "rx": rx, "ry": ry, "rot": rot, "radius": 0.004, "z": -10,
            "dof": {"blur": float(arg(ask, "dof", 3.0)), "focus": [(bx0 + bx1) / 2, (by0 + by1) / 2]},
            "shadow": {"dx": 0.012, "dy": 0.03, "blur": 0.035, "a": 0.6}}
    items = keep + [base, card]
    evs = [{"f": a, "kind": "paper", "strength": 0.4}]
    if line and arg(ask, "mark", True) and n - p2 >= 6:
        fx0, fx1 = xe + (bx0 - 0.5) * w * S, xe + (bx1 - 0.5) * w * S
        fy0, fy1 = ye + (by0 - 0.5) * h * S, ye + (by1 - 0.5) * h * S
        pad = 0.15 * (fy1 - fy0)
        m = min(10, n - p2)
        grow = decel(m, 0.0, 1.0, 3.0) + [1.0] * (n - p2 - m)
        rect = [[round(fx0 - 0.01, 5), round(fy0 - pad, 5), round(fx0 - 0.01 + (fx1 - fx0 + 0.02) * max(g, 0.01), 5), round(fy1 + pad, 5)]
                for g in grow]
        items.append({"id": uid(l1, "pd_pfm"), "k": "flash", "f": [a + p2, b], "mode": "mark", "rect": rect, "c": arg(ask, "c", "#FFD84A"),
                      "z": -5})
        evs.append({"f": a + p2, "kind": "mark", "strength": 0.6})
    return {"items": items, "drop": drop, "log": {"pic": pic, "box": [round(v, 4) for v in (bx0, by0, bx1, by1)], "box_from": found,
                                                   "push": round(S, 3)}, "events": evs}


DEVICES = {
    "proof_object": {"plan": proof_object, "needs": ("still",), "fallback": ("parallax", "leak"),
                     "card": "f(>=1 s) pics[document|screenshot|profile] box[x0,y0,x1,y1]? desk(plate|blur) mark. The source lies "
                             "tilted on a desk with depth of field, lifts flat as the camera pushes into the line that proves the "
                             "claim (measured on it), a highlighter across it; a picture with no line: the push goes to its face or light. For a claim with a source: a headline, a filing, a post, a profile"},
}
