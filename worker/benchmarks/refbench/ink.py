"""Exact ink of a motion graphic in a render, from the renderer's own clip.

A diff against a "no graphics" control render is NOT the graphic's ink: a
graphic mutes or de-duplicates captions while it is up, so the control's
captions differ from the render's, and a previous graphic still on screen
counts as ink too. The renderer composites every motion item from a
transparent clip (``motion_layer.overlay_clip``: clip file, x, y, start), so
refbench records those placements while it renders and reads the item's
glyphs straight from the clip's alpha:

    with capture_overlays() as placements:      # {item_id: {...}}
        renderer.render_edl(...)
    save_placements(placements, path)

    glyphs(rec, frame, fps)          clip RGBA of program frame -> full-frame
                                     alpha + colour (where the item draws)
    visible(render_frame, g)         glyph pixels the render really shows
                                     (not hidden behind the subject)
    landing_frame(render, rec, fps)  first frame the item shows >= 60% of its
                                     settled glyphs in their settled colour

A glyph pixel is clip alpha >= 250 (the templates' shadows and glows are
soft: alpha < 250); anti-aliased edges (alpha 128-249) are excluded from the
plate ring but are not ink.
"""

import contextlib
import json
import os
import subprocess

import numpy as np

import qa_metrics

GLYPH_ALPHA = 250
EDGE_ALPHA = 128
MATCH_TOL = 24


@contextlib.contextmanager
def capture_overlays():
    """Record every motion item the renderer overlays: {item_id: rec}."""
    import motion_layer
    rec = {}
    orig = motion_layer.overlay_clip

    def wrapped(parts, vlabel, idx, item, clip, fps, tag, **kw):
        iid = item.get("id")
        if iid and iid not in rec and getattr(clip, "path", None):
            rec[iid] = {"path": clip.path, "x": int(clip.x),
                        "y": int(clip.y), "w": int(clip.w), "h": int(clip.h),
                        "start": float(item["start"]),
                        "end": float(item["end"]),
                        "template": item.get("template"),
                        "layer": item.get("layer")}
        # **kw: the render's fx plan (Wave 1: composites read its masks)
        return orig(parts, vlabel, idx, item, clip, fps, tag, **kw)
    motion_layer.overlay_clip = wrapped
    try:
        yield rec
    finally:
        motion_layer.overlay_clip = orig


def save_placements(rec, path):
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=1)


def load_placements(path):
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


_CLIP_CACHE = {}


def clip_frames(path):
    """All RGBA frames of a transparent clip (cached per path)."""
    if path in _CLIP_CACHE:
        return _CLIP_CACHE[path]
    info = qa_metrics.probe(path)
    w, h = info["width"], info["height"]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-fps_mode",
         "passthrough", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 4)
    arr = np.frombuffer(raw[:n * w * h * 4], np.uint8).reshape(n, h, w, 4)
    if len(_CLIP_CACHE) > 8:
        _CLIP_CACHE.clear()
    _CLIP_CACHE[path] = arr
    return arr


def clip_index(rec, frame, fps):
    """The clip frame the overlay shows at program frame ``frame`` (the
    clip's PTS are shifted by the item's start; overlay takes the latest
    clip frame at or before the main frame's time), or None outside the
    item's window."""
    t = frame / float(fps)
    if t < rec["start"] - 1e-6 or t >= rec["end"] - 1e-6:
        return None
    return int(np.floor((t - rec["start"]) * float(fps) + 1e-6))


def glyphs(rec, frame, fps, out_hw):
    """{"alpha": (H, W) uint8, "rgb": (H, W, 3) uint8} of the item at
    program frame ``frame`` placed on an ``out_hw`` canvas, or None."""
    k = clip_index(rec, frame, fps)
    if k is None:
        return None
    fr = clip_frames(rec["path"])
    if not len(fr):
        return None
    k = min(k, len(fr) - 1)          # the overlay holds the last frame
    c = fr[k]
    H, W = out_hw
    alpha = np.zeros((H, W), np.uint8)
    rgb = np.zeros((H, W, 3), np.uint8)
    x, y = rec["x"], rec["y"]
    h, w = c.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    alpha[y0:y1, x0:x1] = c[y0 - y:y1 - y, x0 - x:x1 - x, 3]
    rgb[y0:y1, x0:x1] = c[y0 - y:y1 - y, x0 - x:x1 - x, :3]
    return {"alpha": alpha, "rgb": rgb}


def visible(render_frame, g, tol=MATCH_TOL):
    """(glyph, shown): bool masks of the item's glyph pixels and of those the
    render shows in the glyph's own colour (a pixel the subject covers, or
    one another layer draws over, is not shown). Glyphs are eroded by one
    pixel: a 4:2:0 encode smears colour across a glyph's edge, which is
    not occlusion."""
    glyph = qa_metrics._erode(g["alpha"] >= GLYPH_ALPHA, 1)
    d = np.abs(np.asarray(render_frame, np.int16) - g["rgb"].astype(np.int16))
    shown = glyph & (d.max(axis=2) <= tol)
    return glyph, shown


def landing_frame(render, rec, fps, *, search_s=(0.0, 1.2), settle_s=None,
                  share=0.6, size=None):
    """The frame the item LANDS: the first program frame whose render shows
    >= ``share`` x (the settled frame's shown share) of the SETTLED glyph
    pixels in their settled colour. Settled = the frame ``settle_s`` after
    the start (default: 60% through the item, capped at start + 1.0 s).
    A pop lands on its first frame; a 1.5x->1x slam when its scale is ~1.
    Returns (frame, detail)."""
    info = qa_metrics.probe(render)
    H, W = info["height"], info["width"]
    s, e = rec["start"], rec["end"]
    st = s + (settle_s if settle_s is not None else min(0.6 * (e - s), 1.0))
    f_set = int(round(st * fps))
    f0 = max(0, int(np.floor((s + search_s[0]) * fps)) - 3)
    f1 = min(int(np.ceil(min(e, s + search_s[1]) * fps)), f_set)
    want = set(range(f0, f1 + 1)) | {f_set}
    idx, frames = qa_metrics.read_frames(render, frames=want)
    byf = dict(zip(idx, frames))
    g_set = glyphs(rec, f_set, fps, (H, W))
    if g_set is None or f_set not in byf:
        return None, {"why": "item not on screen at its settle frame"}
    glyph, shown_set = visible(byf[f_set], g_set)
    ref = shown_set
    n_ref = int(ref.sum())
    if n_ref < 50:
        return None, {"why": "settled glyphs not visible"}
    col = g_set["rgb"].astype(np.int16)
    curve = {}
    for f in sorted(byf):
        if f > f_set:
            continue
        d = np.abs(byf[f].astype(np.int16) - col).max(axis=2)
        curve[f] = float(((d <= MATCH_TOL) & ref).sum()) / n_ref
    # the share the settled glyphs reach anyway (the plate under them can
    # already resemble the type colour): measured on frames BEFORE the item
    base = max([curve[f] for f in curve if f < int(np.floor(s * fps))] or
               [0.0])
    thr = base + share * (1.0 - base)
    ev = next((f for f in sorted(curve) if f >= int(np.floor(s * fps))
               and curve[f] >= thr), None)
    return ev, {"settle_frame": f_set, "base_share": round(base, 3),
                "threshold": round(thr, 3),
                "curve": {str(k): round(v, 3) for k, v in curve.items()}}


def item_metrics(render_frame, rec, frame, fps, *, role="payload",
                 bar=None):
    """legibility + glyph_visible of one item on one rendered frame."""
    H, W = render_frame.shape[:2]
    g = glyphs(rec, frame, fps, (H, W))
    if g is None:
        return None
    glyph, shown = visible(render_frame, g)
    edge = g["alpha"] >= EDGE_ALPHA
    out = {"frame": frame, "glyph_px": int(glyph.sum()),
           "shown_px": int(shown.sum())}
    if shown.sum() >= 50:
        out["legibility"] = qa_metrics.legibility(
            frame=render_frame, glyph_mask=shown, exclude=edge, role=role,
            bar=bar)
    if glyph.sum() >= 50:
        out["glyph_visible"] = qa_metrics.glyph_visible(
            glyphs=letters(glyph), subject=glyph & ~shown)
    return out


def letters(mask, min_px=30):
    """Connected glyph components (letters or touching groups): {k: mask}."""
    import cv2
    n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    return {str(k): lab == k for k in range(1, n)
            if (lab == k).sum() >= min_px}
