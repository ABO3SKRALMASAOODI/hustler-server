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
    p = dict(p, graph=g, shown=ui_scene.shown_texts(g))
    item["params"] = p
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
                     "(keep-out, caption plan, type fit)")
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

def safe_area_violations(path, ratio, samples=12, wm_box=None):
    """[(t, box)] of text lines drawn outside the aspect's safe area (the
    watermark's own corner excepted)."""
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
            if wm_box and fx0 >= wm_box[0] - 0.01 and fy0 >= wm_box[1] - \
                    0.01 and fx1 <= wm_box[2] + 0.01 and fy1 <= wm_box[3] \
                    + 0.01:
                continue
            if fx0 < l * 0.5 or fy0 < t * 0.5 or fx1 > 1 - r * 0.5 or \
                    fy1 > 1 - b * 0.5:
                out.append((round(k / float(info.get("fps") or 30.0), 2),
                            [round(fx0, 3), round(fy0, 3), round(fx1, 3),
                             round(fy1, 3)]))
    return out


def verify(path, edl, index, ratio, final=False, want_wm=False):
    """{faces, brand, safe_area, findings} for one rendered master."""
    import motion_critic
    res = motion_critic.critique(path, edl, index or {}, final=final,
                                 want_wm=want_wm, deep=False)
    faces = [f for f in res.get("findings") or []
             if f.get("code") == "type_on_face"]
    brand = [f for f in res.get("findings") or []
             if f.get("lens") == "brand"]
    safe = safe_area_violations(path, ratio)
    return {"faces": len(faces), "brand": len(brand),
            "safe_area": len(safe), "safe_detail": safe[:6],
            "score": res.get("score"),
            "brand_measure": (res.get("measures") or {}).get("brand"),
            "findings": [{k: f.get(k) for k in ("code", "lens", "t",
                                                 "severity", "message")}
                         for f in (faces + brand)][:8]}
