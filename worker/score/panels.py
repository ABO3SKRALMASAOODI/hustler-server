"""A reframe window over a collage lands on a photo, not on the page between photos (#1830).

An upload that is a collage slideshow (photos laid out on a cream or white page, one file) reframed
to 9:16 takes a narrow window of each 16:9 frame. Placed blind (the middle, or where no face was
found), the window can sit on the page's gutter: project 6305's last 4.4 s were cream with two thin
strips of photo, and the film ended on a blank cream frame.

Measured, never judged: the page colour is the frame's dominant colour when it covers a large share
of the frame; a pixel holds picture when it is far from that colour. A window whose picture share is
low slides, at the same size, to where the share is highest, and the report names each move. Frames
with no dominant page (ordinary footage) are left exactly as framed.
"""
import time

import numpy as np

ANALYSIS_W = 320
PAGE_SHARE_MIN = 0.22       # the dominant colour must cover this share of the frame to be a page
PAGE_TOL = 26.0             # 0-255 RGB distance under which a pixel is the page
QUANT = 16                  # colour bins for the dominant colour
WINDOW_OK = 0.35            # a window holding this share of picture is left where it is: only a window
                            # that is mostly page moves (a person before a plain wall fills more than this)
GAIN_MIN = 0.25             # a move must win at least this much picture share
STEPS = 48                  # positions tried along each free axis
BUDGET_S = 10.0             # all of a film's measurements together


def picture_mask(rgb):
    """(mask, page_share): True where a pixel holds picture. mask is None when the frame has no
    dominant flat page (ordinary footage)."""
    a = np.asarray(rgb, dtype=np.float32)
    if a.ndim != 3 or a.shape[2] < 3 or a.size == 0:
        return None, 0.0
    a = a[:, :, :3]
    q = (a // QUANT).astype(np.int32)
    codes = q[:, :, 0] * 256 + q[:, :, 1] * 16 + q[:, :, 2]
    vals, counts = np.unique(codes, return_counts=True)
    top = vals[int(np.argmax(counts))]
    sel = codes == top
    page = a[sel].mean(axis=0)
    far = np.sqrt(((a - page) ** 2).sum(axis=2)) > PAGE_TOL
    share = 1.0 - float(far.mean())
    if share < PAGE_SHARE_MIN:
        return None, share
    return far, share


def window_share(mask, crop):
    h, w = mask.shape
    x0, y0 = int(round(crop[0] * w)), int(round(crop[1] * h))
    x1, y1 = max(x0 + 1, int(round(crop[2] * w))), max(y0 + 1, int(round(crop[3] * h)))
    return float(mask[y0:y1, x0:x1].mean())


def best_window(mask, crop):
    """(crop, share_before, share_after): the same-size window with the most picture."""
    before = window_share(mask, crop)
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    best, best_share = list(crop), before
    xs = np.linspace(0.0, max(0.0, 1.0 - cw), STEPS) if cw < 0.999 else [crop[0]]
    ys = np.linspace(0.0, max(0.0, 1.0 - ch), STEPS) if ch < 0.999 else [crop[1]]
    for x in xs:
        for y in ys:
            c = [float(x), float(y), float(x) + cw, float(y) + ch]
            s = window_share(mask, c)
            if s > best_share + 1e-6:
                best, best_share = c, s
    return [round(v, 4) for v in best], before, best_share


def place(mask, crop):
    """The crop to use and a note, or (crop, None) when it stays."""
    if mask is None or crop is None:
        return crop, None
    new, before, after = best_window(mask, crop)
    if before >= WINDOW_OK or after - before < GAIN_MIN:
        return crop, None
    return new, (before, after)


def repair_windows(overlays, path_of, rep, grab=None, budget_s=BUDGET_S):
    """Slide every main-upload video overlay's source_crop off a collage's page onto its photos.
    path_of(asset_key) -> local file or None. Never raises; what it moved is in rep['collage_windows']."""
    if grab is None:
        from .boards import grab
    t0 = time.monotonic()
    masks = {}
    for ov in overlays:
        try:
            crop = ov.get("source_crop")
            if ov.get("kind") != "video" or not crop:
                continue
            if (crop[2] - crop[0]) > 0.98 and (crop[3] - crop[1]) > 0.98:
                continue
            path = path_of(ov.get("asset_key"))
            if not path:
                continue
            if time.monotonic() - t0 > budget_s:
                rep["collage_windows_budget"] = True
                break
            t = float(ov.get("source_start_s") or 0.0) + min(0.5, float(ov.get("duration_s") or 0.0) / 2)
            key = (path, round(t, 1))
            if key not in masks:
                im = grab(path, t, width=ANALYSIS_W)
                masks[key] = picture_mask(im)[0] if im is not None else None
            new, moved = place(masks[key], [float(v) for v in crop])
            if moved:
                ov["source_crop"] = new
                rep.setdefault("collage_windows", []).append(
                    {"id": ov.get("id"), "from": [round(float(v), 4) for v in crop], "to": new,
                     "picture_before": round(moved[0], 3), "picture_after": round(moved[1], 3)})
        except Exception as e:  # noqa: BLE001 - a window that cannot be measured stays as framed
            rep.setdefault("collage_window_errors", []).append(f"{type(e).__name__}: {str(e)[:80]}")
    return overlays
