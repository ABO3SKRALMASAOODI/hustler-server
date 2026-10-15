"""Track 4E: every aspect from one edit (ROADMAP 4E; INTERFACES_W4.md §5.1).

``solve(edl, ratio, index)`` re-solves an edit for one aspect master
(16:9, 4:3, 1:1, 4:5, 9:16) — nothing is scaled from another aspect's
pixels:

  frame        the crop aim (focus_x/y, focus_track, follow) is SOURCE-space
               and carries over; the ratio changes
  UI scenes    re-solved from the scenes they were solved from (``spec``):
               a new layout in the new design space, not a scaled one
  motion       every measurement is dropped (footprint, box, series) so the
               renderer measures again in the new frame: keep-out against
               the face, the caption plan, the type fit
  captions     the placement track (chosen from measured boxes in the old
               framing) is dropped: placement is re-solved at render
  overlays     a box keeps its picture's own proportions: re-fitted around
               its centre for the new frame, inside the safe area
  fx           program/source space (regions are frame fractions, anchors
               re-solve at render)

``verify(path, edl, index, ratio)`` judges a rendered master: type on a
face (the critic's faces lens), the brand (watermark + end card), and type
outside the aspect's safe area.
"""

import copy

RATIOS = ("16:9", "4:3", "1:1", "4:5", "9:16")
# Platform-safe margins per aspect (fractions): UI chrome on vertical
# platforms owns more of the bottom.
SAFE = {"9:16": (0.05, 0.08, 0.05, 0.14), "4:5": (0.05, 0.06, 0.05, 0.08),
        "1:1": (0.05, 0.05, 0.05, 0.06), "4:3": (0.05, 0.05, 0.05, 0.06),
        "16:9": (0.05, 0.05, 0.05, 0.06)}


def ratio_value(ratio):
    a, b = (float(x) for x in ratio.split(":"))
    return a / b


def design_size(ratio):
    import motion_engine
    W, H = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080),
            "4:3": (1440, 1080), "16:9": (1920, 1080)}[ratio]
    return motion_engine.design_size(W, H)


def _resolve_ui(item, ratio):
    import ui_scene
    p = item.get("params") or {}
    spec = p.get("spec") or {}
    if not spec.get("scenes"):
        return False
    W, H = design_size(ratio)
    dur = float(item["end"]) - float(item["start"])
    g = ui_scene.solve(copy.deepcopy(spec["scenes"]), dur, W, H,
                       words=spec.get("words") or [],
                       theme=spec.get("theme"), accent=spec.get("accent"))
    p = dict(p, graph=g, shown=ui_scene.caption_shown(g))
    item["params"] = p
    fp = ui_scene.footprint(g)
    if fp:
        item["footprint"] = fp           # the re-solved cards, this aspect
    return True


def _refit_box(o, old_r, new_r, ratio):
    """An overlay for the new aspect: the same picture (its own
    proportions), the same centre, the same share of the frame's SHORT
    side. ``scale`` is a share of the frame's width, so a 9:16 overlay at
    0.4 would fill a 16:9 frame's height; it is converted, not copied.
    Animated (keyframed) values and full-frame cutaways are left alone."""
    if o.get("fit") == "cover":
        return False
    sc = o.get("scale")
    if not isinstance(sc, (int, float)):
        return False
    # width px over the short side, old frame (unit height = 1)
    old_w, old_h = old_r, 1.0
    new_w, new_h = new_r, 1.0
    share = float(sc) * old_w / min(old_w, old_h)
    o["scale"] = round(min(0.95, share * min(new_w, new_h) / new_w), 4)
    return True


def solve(edl, ratio, index=None):
    """(edl for ``ratio``, notes) — see the module doc."""
    if ratio not in RATIOS:
        raise ValueError(f"ratio is one of {RATIOS}")
    e = copy.deepcopy(edl or {})
    notes = []
    v = ((index or {}).get("video") or {})
    try:
        src_r = float(v.get("width")) / float(v.get("height"))
    except (TypeError, ValueError, ZeroDivisionError):
        src_r = 16 / 9.0
    old = (e.get("frame") or {}).get("ratio") or "source"
    old_r = src_r if old == "source" else ratio_value(old)
    new_r = ratio_value(ratio)
    fr = dict(e.get("frame") or {})
    fr["ratio"] = ratio
    fr.setdefault("mode", "crop")
    e["frame"] = fr
    notes.append(f"frame {old} -> {ratio} (the aim is source-space: kept)")
    n_ui = n_mo = 0
    for m in e.get("motion") or []:
        if not isinstance(m, dict):
            continue
        for k in ("footprint", "box", "series"):
            if m.get(k) is not None:
                m.pop(k, None)
        if m.get("template") == "ui_scene":
            if _resolve_ui(m, ratio):
                n_ui += 1
        else:
            n_mo += 1
    if n_ui:
        notes.append(f"{n_ui} UI scene(s) re-solved for {ratio}")
    if n_mo:
        notes.append(f"{n_mo} motion graphic(s): measured again at render "
                     "(caption plan, type fit); the delivery verbs re-run "
                     "the face keep-out at this frame")
    caps = e.get("captions")
    if isinstance(caps, dict) and caps.get("placement_track"):
        caps.pop("placement_track", None)
        notes.append("captions: placement re-solved at render")
    n_ov = sum(1 for o in e.get("overlays") or []
               if isinstance(o, dict) and _refit_box(o, old_r, new_r, ratio))
    if n_ov:
        notes.append(f"{n_ov} overlay(s) re-fitted (own proportions kept)")
    return e, notes


# ── verification of a rendered master ────────────────────────────────────

def source_text_boxes(src_path, edl, index, ratio):
    """Frame-fraction boxes, in the program frame of ``ratio``, of text
    burned into the SOURCE (a channel logo, a lower third): the footage's
    own marks are not this edit's type and never count against it."""
    try:
        import inpaint
        regs = inpaint.detect_text_regions(src_path, samples=8)
    except Exception:  # noqa: BLE001
        return []
    v = (index or {}).get("video") or {}
    try:
        sr = float(v.get("width")) / float(v.get("height"))
    except (TypeError, ValueError, ZeroDivisionError):
        return []
    r = ratio_value(ratio)
    fr = (edl or {}).get("frame") or {}
    fx = float(fr.get("focus_x") if fr.get("focus_x") is not None else 0.5)
    fy = float(fr.get("focus_y") if fr.get("focus_y") is not None else 0.5)
    # the crop window in source fractions
    if r < sr:
        cw, ch = r / sr, 1.0
    else:
        cw, ch = 1.0, sr / r
    x0 = min(max(0.0, fx - cw / 2.0), 1.0 - cw)
    y0 = min(max(0.0, fy - ch / 2.0), 1.0 - ch)
    out = []
    for g in regs:
        bx0, by0 = (g["x"] - x0) / cw, (g["y"] - y0) / ch
        bx1 = (g["x"] + g["w"] - x0) / cw
        by1 = (g["y"] + g["h"] - y0) / ch
        if bx1 > 0 and by1 > 0 and bx0 < 1 and by0 < 1:
            out.append([bx0 - 0.01, by0 - 0.01, bx1 + 0.01, by1 + 0.01])
    return out


def safe_area_violations(path, ratio, samples=12, wm_box=None,
                         ignore=()):
    """[(t, box)] of text lines drawn outside the aspect's safe area (the
    watermark's own corner and the footage's own burned-in marks
    (``ignore`` boxes) excepted)."""
    import numpy as np
    import broll_score
    import qa_metrics
    info = qa_metrics.probe(path)
    W, H = int(info["width"]), int(info["height"])
    dur = float(info["duration"])
    l, t, r, b = SAFE.get(ratio, (0.05, 0.05, 0.05, 0.06))
    out = []
    n = max(1, int(dur * float(info.get("fps") or 30.0)))
    idx = sorted({int(n * (k + 0.5) / samples) for k in range(samples)})
    got, frames = qa_metrics.read_frames(path, frames=set(idx))
    for k, f in zip(got, frames):
        f = np.asarray(f)
        for x, y, w, h, _tl in broll_score._text_boxes(f):
            fx0, fy0 = x / W, y / H
            fx1, fy1 = (x + w) / W, (y + h) / H
            area = max(1e-9, (fx1 - fx0) * (fy1 - fy0))

            def share(g):
                ix = max(0.0, min(fx1, g[2]) - max(fx0, g[0]))
                iy = max(0.0, min(fy1, g[3]) - max(fy0, g[1]))
                return ix * iy / area
            # a line the detector merged with Valmera's mark or with the
            # footage's own burned-in logo is theirs, not the edit's
            if any(share(g) >= 0.25 for g in list(ignore or ())
                   + ([wm_box] if wm_box else [])):
                continue
            if fx0 < l * 0.5 or fy0 < t * 0.5 or fx1 > 1 - r * 0.5 or \
                    fy1 > 1 - b * 0.5:
                out.append((round(k / float(info.get("fps") or 30.0), 2),
                            [round(fx0, 3), round(fy0, 3), round(fx1, 3),
                             round(fy1, 3)]))
    return out


def _wm_box(path):
    """The free-tier mark's corner (robot + its two lines) in frame
    fractions: Valmera's own mark, placed exactly as rendered — never a
    safe-area violation of the edit's type."""
    try:
        import qa_metrics
        import renderer
        info = qa_metrics.probe(path)
        W, H = int(info["width"]), int(info["height"])
        g = renderer.watermark_geometry(W, H)
        x1 = g["x_out"] + 24 * g["fontsize"] * 0.62
        return [g["margin_x"] / W - 0.01, g["margin_y"] / H - 0.01,
                x1 / W + 0.01, (g["margin_y"] + g["rh"]) / H + 0.02]
    except Exception:  # noqa: BLE001
        return None


def verify(path, edl, index, ratio, final=False, want_wm=False,
           src_path=None):
    """{faces, brand, safe_area, findings} for one rendered master."""
    import motion_critic
    import qa_metrics
    res = motion_critic.critique(path, edl, index or {}, final=final,
                                 want_wm=want_wm, deep=False)
    # the faces lens is one of the critic's deep passes: run it on its own
    # here (a master's whole point is that type stays off the face)
    try:
        faces = [f for f in motion_critic.lens_faces(
            path, edl, qa_metrics.probe(path)) or []
            if f.get("code") == "type_on_face"]
    except Exception as e:  # noqa: BLE001 — reported, never silent
        faces = []
        res.setdefault("errors", []).append(f"faces: {str(e)[:120]}")
    brand = [f for f in res.get("findings") or []
             if f.get("lens") == "brand"]
    ignore = source_text_boxes(src_path, edl, index, ratio) if src_path \
        else []
    wm = _wm_box(path) if want_wm else None
    safe = safe_area_violations(path, ratio, ignore=ignore, wm_box=wm)
    return {"faces": len(faces), "brand": len(brand),
            "safe_area": len(safe), "safe_detail": safe[:6],
            "score": res.get("score"), "errors": res.get("errors") or [],
            "brand_measure": (res.get("measures") or {}).get("brand"),
            "findings": [{k: f.get(k) for k in ("code", "lens", "t",
                                                 "severity", "message")}
                         for f in (faces + brand)][:8]}
