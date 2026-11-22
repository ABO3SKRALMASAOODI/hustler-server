"""match: cuts that keep the eye where it was while the world changes. A chain of faces registered on the eyes (the
eyes' midpoint on one seat, the face one height, growing a little through the chain), and, for pictures with no face,
a position match: each picture's busiest region set on the same seat. The rhyme device (alien.rhyme) falls back to it
when the film's pictures hold too few round shapes.

Faces come from the caller (the pool's records carry the footage engine's YuNet boxes and eyes) or from ``sense``
when it offers ``faces(path, t)``; regions from the pictures' own gradients (pace.regions_of). No model is read here.

ask {"dev": "match", "at": [a, z], "pics": [...], "cuts": [frames] | "n": k, "seat": [x, y] (default 0.5, 0.36),
     "face_h": 0.30 (a face's height in the frame), "grow": 1.12 (across the chain), "mono": bool}
"""
from .. import pace
from . import picks as K


def _faces(pic, sense_):
    if pic.faces:
        return pic.faces
    return K.sense_faces(sense_, pic.aid, (pic.rng or [0.0])[0] if pic.video else 0.0)


def _region(pic):
    st = K.still_of(pic, size=(160, 160) if pic.w >= pic.h else (120, 213))
    if st is None:
        return None
    try:
        r = pace.regions_of(st, n=1, size=0.4)
    except Exception:      # noqa: BLE001
        return None
    return r[0] if r else None


def registered(pic, W, H, seat, face_h, sense_=None, by="any"):
    """(crop, how): the framing that sets this picture's eyes (or its busiest region) on the seat, or (None, why).
    by: "eyes" (faces only), "position" (regions only) or "any" (eyes where there is a face)."""
    cam = K.cam_of(pic, W, H, 3.0)
    faces = _faces(pic, sense_) if by != "position" else []
    if faces:
        f = max(faces, key=lambda q: q[2] * q[3])
        if f[3] * pic.h >= 40:
            eyes = f[4] if len(f) > 4 else None
            crop = cam.on_face(f[:4], face_h, eyes_y=seat[1], eyes=eyes, x_at=seat[0])
            return crop, "eyes"
    if by == "eyes":
        return None, "no face"
    r = _region(pic)
    if r is None:
        return None, "unreadable"
    focus = ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
    return cam.cover(focus=focus, at=tuple(seat), zoom=1.25), "position"


def plan(l1, work, ask, sense_=None, pool=None, by="any"):
    W, H, fps = K.geometry(l1)
    args = K.args_of(ask)
    a, z = K.span(l1, args, default_s=1.6, least=6)
    cuts = sorted(int(f) for f in (args.get("cuts") or []) if a < int(f) < z)
    pics = K.pictures(l1, work, args, pool, sense_=sense_, least=max(len(cuts) + 1, 3))
    if not cuts:
        n = int(args.get("n") or min(max(len(pics), 2), max((z - a) // 6, 2)))
        cuts = [a + int(round((z - a) * i / n)) for i in range(1, n)]
    starts, ends = [a] + cuts, cuts + [z]
    n = len(starts)
    if len(pics) < 2:
        return {"skip": "a match needs two pictures"}
    seat = tuple(float(v) for v in (args.get("seat") or (0.5, 0.36)))
    face_h = float(args.get("face_h", 0.30))
    grow = float(args.get("grow", 1.12))
    framed = []
    for pic in pics:
        crop, how = registered(pic, W, H, seat, face_h, sense_, by)
        if crop is not None:
            framed.append((0 if how == "eyes" else 1, pic, crop, how))
    framed.sort(key=lambda t: t[0])                 # the faces first: the eyes hold the seat; then the positions
    if len(framed) < 2:
        return {"skip": "fewer than two pictures could be registered" + (" on their eyes" if by == "eyes" else "")}
    rng = K.rng_of(l1, args)
    keep, gone, was = K.carve(l1, a, z)
    look = K.inherit(was)
    items, marks, chain = [], [], []
    mono = args.get("mono")
    mono = (n >= 3) if mono is None else bool(mono)
    for i, (s, e) in enumerate(zip(starts, ends)):
        _, pic, crop, how = framed[i % len(framed)]
        m = e - s
        u0, u1 = (s - a) / max(z - a, 1), (e - a) / max(z - a, 1)
        cam = K.cam_of(pic, W, H, 3.0)
        crops = []
        for j in range(m):
            u = u0 + (u1 - u0) * j / max(m - 1, 1)
            k = 1.0 / (grow ** (u ** 1.4))             # the seat holds; the picture grows into the last cut
            ex, ey = crop[0] + (crop[2] - crop[0]) * seat[0], crop[1] + (crop[3] - crop[1]) * seat[1]
            c = K.scale_crop(crop, k, ex, ey)
            if cam.up(c, W) > 3.0:
                c = crop
            crops.append(K.r5(c))
        it = dict(look, id=f"{args.get('id', 'match')}.m{i}", k="shot", f=[s, e], pic=pic.aid, crop=crops)
        if mono:
            it["treat"] = {"mode": "mono"}
        items += K.clocked(it, pic, fps, rng, i)
        if i:
            marks.append({"f": s, "what": "match cut", "kind": "cut", "strength": 0.5})
        chain.append({"pic": pic.aid, "how": how})
    used = list({it["pic"]: None for it in items})
    return {"items": keep + items, "drop": gone, "assets": {k: v for k, v in K.new_assets([p for _, p, _, _ in framed]).items() if k in used},
            "events": marks, "seat": {"x": seat[0], "y": seat[1], "f": [a, z]},
            "log": {"chain": chain, "eyes": sum(1 for c in chain if c["how"] == "eyes"), "mono": mono}}


def rhyme_eyes(l1, work, ask, sense_=None, pool=None):
    """The rhyme's first fallback: the chain cut on the eyes alone (faces registered, no position matches)."""
    return plan(l1, work, ask, sense_, pool, by="eyes")


def match_pos(l1, work, ask, sense_=None, pool=None):
    """The rhyme's second fallback: every picture set on its busiest region, the same seat through every cut."""
    return plan(l1, work, ask, sense_, pool, by="position")


ALIASES = {"match_cut": "match", "eye_match": "rhyme_eyes", "eyes": "rhyme_eyes", "face_chain": "rhyme_eyes",
           "position_match": "match_pos", "shape_match": "match_pos"}

DEVICES = {
    "rhyme_eyes": {"plan": rhyme_eyes, "layer": "rhythm", "needs": ("pictures", "faces"), "fallback": ("match_pos",), "span": True, "words": ("cuts[]",),
                   "card": "rhyme_eyes at until pics[] cuts[]|n seat face_h grow. A chain of faces cut on the eyes: each face's eyes on one seat, one height, growing through every cut"},
    "match_pos": {"plan": match_pos, "layer": "rhythm", "needs": ("pictures",), "fallback": ("burst",), "span": True, "words": ("cuts[]",),
                  "card": "match_pos at until pics[] cuts[]|n seat grow. A position-match chain: every picture's busiest region on one seat, so the eye stays put while the world changes"},
    "match": {"plan": plan, "layer": "rhythm", "needs": ("pictures",), "fallback": ("burst",), "span": True, "words": ("cuts[]",),
              "card": "match at until pics[] cuts[]|n seat face_h grow mono. A match-cut chain: faces registered on the eyes (or pictures on their busiest region) on one seat, growing through every cut; 'again', sameness, the turn"},
}
