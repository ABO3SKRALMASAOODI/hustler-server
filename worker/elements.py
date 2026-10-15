"""Engine data for Wave 3 pages (3D elements, 3E layouts).

ROADMAP Wave 3 / 3D ("emitters from matte-edge samples, sprite
choreography, pose -> pictogram") and 3E (the truthful breakdown, slot
spill); contract in devices/INTERFACES_W3.md §6.

``augment(item, ctx)`` runs in the renderer just before the motion engine
renders an item (world_layer.augment). It hands the page what only the
engine knows, as underscore params (never authored, never stored):

  _edge   el_particles emit="edge": the subject's outline in DESIGN px at
          10 samples a second over the item's window — [{t, p: [[x, y, nx,
          ny], ...]}] (t in item seconds; n = the outward normal), from
          the person matte through the render's geometry
  _pose   el_pictogram mode="pose": the body's joints in design px at 15
          samples a second — [{t, j: [[x, y, vis] x 33]}]
  _edl    breakdown: the program's real structure (layouts.breakdown_doc)
  _spill  device_stage: the screen content's mean colour per 1/10 s

Bounded: every list is decimated so a document stays far under the 200 KB
cap. Any failure leaves the item as it was (the page draws its own
fallback: particles from the point, a library cycle, an empty timeline).
"""

import math

import numpy as np

EDGE_FPS = 10.0
EDGE_POINTS = 56
POSE_FPS = 15.0


def augment(item, ctx):
    tpl = item.get("template")
    params = item.get("params") or {}
    extra = {}
    if tpl == "el_particles" and params.get("emit") == "edge":
        e = edge_samples(item, ctx)
        if e:
            extra["_edge"] = e
    elif tpl == "el_pictogram" and params.get("mode") == "pose":
        p = pose_samples(item, ctx)
        if p:
            extra["_pose"] = p
    elif tpl == "breakdown":
        try:
            import layouts
            extra["_edl"] = layouts.breakdown_doc(ctx.edl, ctx)
        except Exception as e:  # noqa: BLE001
            print(f"[world] breakdown data unavailable ({str(e)[:120]})",
                  flush=True)
    elif tpl == "device_stage":
        try:
            import slots
            sp = slots.spill(item, ctx)
            if sp:
                extra["_spill"] = sp
        except Exception:  # noqa: BLE001
            pass
    if not extra:
        return item
    return dict(item, params=dict(params, **extra))


def _design(ctx):
    import motion_engine
    return motion_engine.design_size(ctx.W, ctx.H)


def edge_samples(item, ctx):
    """[{t, p: [[x, y, nx, ny], ...]}] design px, item seconds."""
    import cv2
    import world_layer
    s, e = float(item["start"]), float(item["end"])
    reader = world_layer._mask_reader(ctx, "person", windows=[(s, e)])
    if reader is None or ctx.tl is None:
        return None
    dw, dh = _design(ctx)
    geo = ctx.geometry("world")
    out = []
    n = max(1, int(math.ceil((e - s) * EDGE_FPS)))
    phase = float(item.get("phase_s") or 0.0)
    for k in range(n + 1):
        t = s + k / EDGE_FPS
        tr = ctx.tl.out_to_src(min(t, ctx.out_duration - 1e-3))
        if tr is None:
            continue
        A = world_layer.program_affine(geo, t, tr)
        m = reader.at(tr - ctx.clock_offset) if A is not None else None
        if m is None or m.max() < 128:
            continue
        mh, mw = m.shape
        cs, _h = cv2.findContours((m > 127).astype(np.uint8),
                                  cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cs:
            continue
        c = max(cs, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
        if len(c) < 8:
            continue
        idx = np.linspace(0, len(c) - 1, EDGE_POINTS).astype(int)
        pts = []
        for i in idx:
            a, b = c[(i - 3) % len(c)], c[(i + 3) % len(c)]
            tx, ty = b - a
            L = math.hypot(tx, ty) or 1.0
            nx, ny = ty / L, -tx / L          # outward for a CCW contour
            x, y = c[i]
            # mask px -> source fraction -> output fraction -> design px
            fx = (A[0, 0] * (x / mw) + A[0, 2])
            fy = (A[1, 1] * (y / mh) + A[1, 2])
            pts.append([round(fx * dw, 1), round(fy * dh, 1),
                        round(nx, 2), round(ny, 2)])
        # outward test: the normal should point away from the centroid
        cxm, cym = c[:, 0].mean(), c[:, 1].mean()
        flip = sum(1 for (x, y, nx, ny), i in zip(pts, idx)
                   if (c[i][0] - cxm) * nx + (c[i][1] - cym) * ny < 0)
        if flip > len(pts) / 2:
            pts = [[x, y, -nx, -ny] for x, y, nx, ny in pts]
        out.append({"t": round(k / EDGE_FPS + phase, 2), "p": pts})
    return out or None


def pose_samples(item, ctx):
    """[{t, j: [[x, y, vis] x 33]}] design px, item seconds."""
    import world_layer
    from anchors import query
    s, e = float(item["start"]), float(item["end"])
    if ctx.tl is None or ctx.source is None:
        return None
    a = ctx.tl.out_to_src(s)
    b = ctx.tl.out_to_src(max(s, e - 0.02))
    if a is None or b is None:
        return None
    off = ctx.clock_offset
    samples, _sh, _m = query.load("landmarks.pose", ctx.source,
                                  [(min(a, b) - off - 0.2,
                                    max(a, b) - off + 0.2)])
    if not samples:
        return None
    dw, dh = _design(ctx)
    geo = ctx.geometry("world")
    out = []
    n = max(1, int(math.ceil((e - s) * POSE_FPS)))
    times = np.array([float(x["t"]) for x in samples])
    for k in range(n + 1):
        t = s + k / POSE_FPS
        tr = ctx.tl.out_to_src(min(t, ctx.out_duration - 1e-3))
        if tr is None:
            continue
        A = world_layer.program_affine(geo, t, tr)
        if A is None:
            continue
        i = int(np.argmin(np.abs(times - (tr - off))))
        if abs(times[i] - (tr - off)) > 0.15:
            continue
        people = samples[i].get("people") or []
        if not people:
            continue
        P = np.array(people[0]["p"], np.float64).reshape(-1, 3)
        j = [[round((A[0, 0] * x + A[0, 2]) * dw, 1),
              round((A[1, 1] * y + A[1, 2]) * dh, 1), round(v, 2)]
             for x, y, v in P]
        out.append({"t": round(k / POSE_FPS, 3), "j": j})
    return out or None
