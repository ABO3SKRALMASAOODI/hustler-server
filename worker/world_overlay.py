"""look_at(overlay='anchors'): what the world verbs can grab, drawn on the
delivered frame (Wave 3, 3A-3C; motion_qa.apply_overlay calls ``draw``).

Faces (the named points, the head ellipse), hands (the 21-point skeleton,
fingertips ringed), the body (shoulders, arms, hips) and the edit's world
definitions (object prompts, plane quads, track seeds) — each labelled with
the name the verbs take (``face.iris_l``, ``hand.index_tip``, ``plane:pl1``).
Where a sidecar is not measured the label says "not measured", so the agent
knows a write will say "measuring" and render static until it lands.

Browserless: PIL + numpy over READY sidecars (a status read, never a
launch).
"""

import numpy as np

_HAND_EDGES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7),
               (7, 8), (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14),
               (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20),
               (0, 17))
_POSE_EDGES = ((11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23),
               (12, 24), (23, 24))
FACE_COL, HAND_COL, POSE_COL, DEF_COL = ((255, 210, 60), (60, 230, 255),
                                         (180, 255, 120), (255, 80, 200))


def draw(ctx, path, out_path, out_t, edl):
    """Draw the anchors at PROGRAM second ``out_t`` on the frame at
    ``path``; returns (out_path, label suffix)."""
    from PIL import Image, ImageDraw
    import world_layer
    import world_defs
    from anchors import query
    from timeline import Timeline
    if out_t is None:
        return path, " [anchors: this moment is not in the edit]"
    edl = edl or {}
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed") or [])
    im = Image.open(path).convert("RGB")
    W, H = im.size
    try:
        import matte_track
        src = matte_track.read_source(ctx.index)
    except Exception:  # noqa: BLE001
        src = None
    wc = world_layer.Ctx(edl, W, H, 30.0, tl=tl, source=src,
                         index=ctx.index, out_duration=tl.out_duration)
    tr = tl.out_to_src(float(out_t))
    if tr is None or src is None:
        return path, " [anchors: no main footage at this moment]"
    A = world_layer.program_affine(wc.geometry("world"), float(out_t), tr)
    if A is None:
        return path, " [anchors: the footage is not on screen]"

    def P(x, y):
        return (float((A[0, 0] * x + A[0, 2]) * W),
                float((A[1, 1] * y + A[1, 2]) * H))
    d = ImageDraw.Draw(im)
    r = max(2, W // 300)
    seen = []

    def nearest(kind):
        samples, _sh, meta = query.load(kind, src, [(tr - 0.5, tr + 0.5)])
        best = None
        for s in samples:
            dt = abs(float(s["t"]) - tr)
            if dt <= 0.2 and (best is None or dt < best[0]):
                best = (dt, s)
        return (best[1] if best else None), meta
    s, meta = nearest("landmarks.face")
    if s is None:
        seen.append("faces not measured")
    for f in (s or {}).get("faces") or []:
        names = (meta or {}).get("names") or query.FACE_NAMES
        for i, n in enumerate(names):
            x, y = P(f["p"][2 * i], f["p"][2 * i + 1])
            d.ellipse([x - r, y - r, x + r, y + r], outline=FACE_COL)
        cx, cy, rx, ry, _deg = f["he"]
        a, b = P(cx - rx, cy - ry), P(cx + rx, cy + ry)
        d.ellipse([a[0], a[1], b[0], b[1]], outline=FACE_COL, width=2)
        d.text(P(cx + rx, cy - ry), "head / face.*", fill=FACE_COL)
    s, _m = nearest("landmarks.hand")
    if s is None:
        seen.append("hands not measured")
    for h in (s or {}).get("hands") or []:
        p = np.array(h["p"]).reshape(-1, 2)
        pts = [P(x, y) for x, y in p]
        for i, j in _HAND_EDGES:
            d.line([pts[i], pts[j]], fill=HAND_COL, width=2)
        for k in (4, 8):
            x, y = pts[k]
            d.ellipse([x - 3 * r, y - 3 * r, x + 3 * r, y + 3 * r],
                      outline=HAND_COL, width=2)
        d.text((pts[8][0] + 4 * r, pts[8][1] - 4 * r),
               f"hand.index_tip ({h.get('side')})", fill=HAND_COL)
    s, _m = nearest("landmarks.pose")
    for pp in (s or {}).get("people") or []:
        p = np.array(pp["p"]).reshape(-1, 3)
        for i, j in _POSE_EDGES:
            if p[i, 2] > 0.5 and p[j, 2] > 0.5:
                d.line([P(*p[i, :2]), P(*p[j, :2])], fill=POSE_COL, width=2)
    for fid, it in world_defs.definitions(edl).items():
        if not float(it["start"]) <= float(out_t) < float(it["end"]):
            continue
        st = world_defs.status(it, tl, src)
        tag = f"{it['type']}:{fid}" + ("" if st in ("ready", "static")
                                       else f" ({st})")
        if it["type"] == "plane":
            q = [P(x, y) for x, y in it["quad"]]
            d.line(q + [q[0]], fill=DEF_COL, width=3)
            d.text(q[0], tag, fill=DEF_COL)
        elif it["type"] == "object":
            bx = it["prompt"].get("box")
            if bx:
                a, b = P(bx[0], bx[1]), P(bx[2], bx[3])
                d.rectangle([a[0], a[1], b[0], b[1]], outline=DEF_COL,
                            width=3)
                d.text(a, tag, fill=DEF_COL)
        elif it["type"] == "track":
            sd = it["seed"]
            x, y = P(sd.get("x", 0.5), sd.get("y", 0.5)) if not sd.get(
                "box") else P((sd["box"][0] + sd["box"][2]) / 2,
                              (sd["box"][1] + sd["box"][3]) / 2)
            d.ellipse([x - 4 * r, y - 4 * r, x + 4 * r, y + 4 * r],
                      outline=DEF_COL, width=3)
            d.text((x + 5 * r, y), tag, fill=DEF_COL)
    im.save(out_path, quality=88)
    note = f" [anchors: {', '.join(seen)}]" if seen else " [anchors]"
    return out_path, note
