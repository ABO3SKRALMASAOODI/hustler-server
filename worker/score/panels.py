"""A reframe window over a collage fills the frame with one photo, never the page between photos (#1830).

An upload that is a collage slideshow (photos laid out on a cream or white page, one file) reframed
to 9:16 takes a narrow window of each 16:9 frame. Placed blind (the middle, or where no face was
found), the window sits across the gutter: project 6305's last 4.4 s showed two thin strips of photo
split by cream, with cream bands above and below, and the film ended on a blank cream frame.

Measured, never judged. A frame is a collage page when one flat colour (a fill, not a filmed sky or
wall, which shade and grain) covers a large share of it AND what is not that colour is laid out as photo panels: rectangles (upright or tilted). Dark studio
footage has a dominant colour too (a black backdrop, dark shirts and hair), but what stands out of it
is a face, hands, a lit shelf: shapes that are not panels. So:
- a frame whose picture is mostly not panels is footage, and its window stays exactly as framed;
- a window holding a shape that is not a panel (a person) or a face outside the photos stays where it
  was aimed;
- a window not already filled by one photo is set inside one photo panel, measured on frames across
  the overlay's whole source span (a slideshow that changes keeps the window on that photo throughout),
  at the window's own aspect, centred on a face in that photo when there is one, enlarged at most
  ZOOM_MAX. When no photo holds still across the span, the window slides at its own size to where the
  picture is. The report names each move.
"""
import time

import numpy as np

ANALYSIS_W = 320
PAGE_SHARE_MIN = 0.22       # the dominant colour must cover this share of the frame to be a page
PAGE_TOL = 26.0             # 0-255 RGB distance under which a pixel is the page
PAGE_FLAT = 0.85            # a laid-out page is one flat fill: this share of its pixels sit within FLAT_TOL of
FLAT_TOL = 8.0              # its colour (6305's cream: 0.93-0.97). A sky, a wall, a backdrop filmed by a camera
                            # shades and grains (0.26-0.73 on the 41 reference reels)
QUANT = 16                  # colour bins for the dominant colour
PANEL_FILL = 0.88           # a shape filling this much of its tightest (rotated) rectangle is a photo panel;
                            # an ellipse (a head) fills 0.785, a person's silhouette far less
PANEL_MIN = 0.002           # shapes smaller than this share of the frame are specks (text, noise)
PANELS_MIN = 0.75           # the share of a frame's picture that must be panels for the frame to be a collage
FILL_TARGET = 0.03          # a photo smaller than this share of the frame is not a window's target
SUBJECT_IN_WINDOW = 0.03    # a window this much covered by a shape that is not a panel is aimed at a subject
FILLED = 0.92               # a window this much covered by one photo already fills the frame with it
STEADY = 0.6                # a photo is the same photo across frames when this much of it overlaps
ZOOM_MAX = 1.8              # the most a window is enlarged to fill it with one photo
WINDOW_OK = 0.35            # with no steady photo: a window holding this share of picture stays
GAIN_MIN = 0.25             # with no steady photo: a slide must win at least this much picture share
STEPS = 48                  # positions tried along each free axis
SAMPLES = (0.15, 0.5, 0.85)  # where across an overlay's source span its frames are measured
BUDGET_S = 10.0             # all of a film's measurements together


class Page:
    """One measured collage frame, in frame fractions: `pic` where any picture is, `other` where picture is
    not a panel, `panels` the upright photo rectangles, `faces` the faces found on the frame."""

    def __init__(self, pic, other, panels=(), faces=()):
        self.pic, self.other = pic, other
        self.shape = pic.shape
        self.panels, self.faces = list(panels), list(faces)


def _cv2():
    try:
        import cv2
        return cv2
    except Exception:  # noqa: BLE001 - without OpenCV no frame is read as a collage, and every window stays
        return None


def _faces(cv2, a):
    """Faces on the frame as [x0, y0, x1, y1] fractions; [] when no detector is available."""
    try:
        from subject import _cascades, _distinct, _faces_in
    except Exception:  # noqa: BLE001
        return []
    gray = cv2.cvtColor(np.ascontiguousarray(a).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    return [[x / w, y / h, (x + fw) / w, (y + fh) / h]
            for (x, y, fw, fh) in _distinct(_faces_in(cv2, _cascades(cv2), gray))]


def picture_mask(rgb):
    """(Page, page_share), or (None, page_share) when the frame is not a collage page: no dominant flat
    colour, or what stands out of it is not laid out as photo panels (ordinary footage)."""
    a = np.asarray(rgb)
    if a.ndim != 3 or a.shape[2] < 3 or a.size == 0:
        return None, 0.0
    a = a[:, :, :3]
    af = a.astype(np.float32)
    q = (af // QUANT).astype(np.int32)
    codes = q[:, :, 0] * 256 + q[:, :, 1] * 16 + q[:, :, 2]
    vals, counts = np.unique(codes, return_counts=True)
    top = vals[int(np.argmax(counts))]
    page = af[codes == top].mean(axis=0)
    dist = np.sqrt(((af - page) ** 2).sum(axis=2))
    far = dist > PAGE_TOL
    share = 1.0 - float(far.mean())
    if share < PAGE_SHARE_MIN or not far.any():
        return None, share
    if float((dist <= FLAT_TOL).mean()) < PAGE_FLAT * share:
        return None, share             # a filmed backdrop, not a laid-out page
    cv2 = _cv2()
    if cv2 is None:
        return None, share
    m8 = cv2.morphologyEx(far.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = far.shape
    other = np.zeros((h, w), dtype=np.uint8)
    panels, panel_area, other_area = [], 0.0, 0.0
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < PANEL_MIN * h * w:
            continue
        (_cx, _cy), (rw, rh), _ang = cv2.minAreaRect(c)
        if area / max(rw * rh, 1.0) >= PANEL_FILL:
            panel_area += area
            x, y, bw, bh = cv2.boundingRect(c)
            if area / max(bw * bh, 1.0) >= PANEL_FILL:          # upright: a window can sit inside it
                panels.append([x / w, y / h, (x + bw) / w, (y + bh) / h])
        else:
            other_area += area
            cv2.drawContours(other, [c], -1, 1, thickness=-1)
    if panel_area <= 0 or panel_area / (panel_area + other_area) < PANELS_MIN:
        return None, share
    return Page(far, other.astype(bool), panels, _faces(cv2, a)), share


def _px(shape, crop):
    h, w = shape[:2]
    x0, y0 = int(round(crop[0] * w)), int(round(crop[1] * h))
    x1, y1 = max(x0 + 1, int(round(crop[2] * w))), max(y0 + 1, int(round(crop[3] * h)))
    return x0, y0, x1, y1


def window_share(mask, crop):
    x0, y0, x1, y1 = _px(mask.shape, crop)
    return float(mask[y0:y1, x0:x1].mean())


def _pages(m):
    if m is None:
        return []
    return [p for p in (m if isinstance(m, (list, tuple)) else [m]) if p is not None]


def _area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _meet(a, b):
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _inside(pt, r):
    return r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3]


def steady_photos(pages):
    """The photo rectangles that hold still on every measured frame (their common part), largest first."""
    out = []
    for p in pages[0].panels:
        if _area(p) < FILL_TARGET:
            continue
        common = list(p)
        for q in pages[1:]:
            best = max(q.panels, key=lambda r: _area(_meet(p, r)), default=None)
            if best is None or _area(_meet(p, best)) < STEADY * min(_area(p), _area(best)):
                common = None
                break
            common = _meet(common, best)
        if common and _area(common) >= STEADY * _area(p):
            out.append(common)
    return sorted(out, key=_area, reverse=True)


def fill_window(photo, crop, shape, faces=()):
    """The window at `crop`'s own pixel aspect that fills itself with `photo`: as large as fits inside the
    photo (never enlarged more than ZOOM_MAX), centred on a face in it when there is one."""
    h, w = shape[:2]
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    aspect = (cw * w) / max(ch * h, 1e-6)                 # the window's width over height, in pixels
    pw, ph = photo[2] - photo[0], photo[3] - photo[1]
    wh = min(ph, pw * w / (aspect * h))                   # the tallest window inside the photo
    wh = max(wh, ch / ZOOM_MAX)
    ww = wh * aspect * h / w
    ww, wh = min(ww, 1.0), min(wh, 1.0)
    inside = [f for f in faces if _inside(((f[0] + f[2]) / 2, (f[1] + f[3]) / 2), photo)]
    if inside:
        f = max(inside, key=_area)
        cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
    else:
        cx, cy = (photo[0] + photo[2]) / 2, (photo[1] + photo[3]) / 2
    # stay inside the photo where the window fits it, and inside the frame always
    lo_x, hi_x = (photo[0], photo[2]) if ww <= pw + 1e-6 else (0.0, 1.0)
    lo_y, hi_y = (photo[1], photo[3]) if wh <= ph + 1e-6 else (0.0, 1.0)
    x0 = min(max(cx - ww / 2, lo_x, 0.0), min(hi_x, 1.0) - ww)
    y0 = min(max(cy - wh / 2, lo_y, 0.0), min(hi_y, 1.0) - wh)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    return [round(x0, 4), round(y0, 4), round(x0 + ww, 4), round(y0 + wh, 4)]


def best_window(m, crop):
    """(crop, share_before, share_after): the same-size window with the most picture on every measured
    frame (its worst frame counts)."""
    pages = _pages(m)
    score = lambda c: min(window_share(p.pic, c) for p in pages)  # noqa: E731
    before = score(crop)
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    best, best_share = list(crop), before
    xs = np.linspace(0.0, max(0.0, 1.0 - cw), STEPS) if cw < 0.999 else [crop[0]]
    ys = np.linspace(0.0, max(0.0, 1.0 - ch), STEPS) if ch < 0.999 else [crop[1]]
    for x in xs:
        for y in ys:
            c = [float(x), float(y), float(x) + cw, float(y) + ch]
            s = score(c)
            if s > best_share + 1e-6:
                best, best_share = c, s
    return [round(v, 4) for v in best], before, best_share


def _photo_share(pages, crop):
    """How much of the window the one photo that covers most of it covers, on its worst frame."""
    out = 1.0
    for p in pages:
        out = min(out, max((_area(_meet(crop, r)) / max(_area(crop), 1e-9) for r in p.panels), default=0.0))
    return out


def place(m, crop):
    """The crop to use and a note (picture share before, after), or (crop, None) when it stays. `m` is one
    Page or a list of them (frames across the overlay)."""
    pages = _pages(m)
    if not pages or crop is None:
        return crop, None
    if any(window_share(p.other, crop) >= SUBJECT_IN_WINDOW for p in pages):
        return crop, None              # the window holds a person or another shape that is not a panel: aimed at it
    for p in pages:                    # a face that is not in a photo is a person on the page: the window is theirs
        for f in p.faces:
            mid = ((f[0] + f[2]) / 2, (f[1] + f[3]) / 2)
            if _inside(mid, crop) and not any(_inside(mid, r) for r in p.panels):
                return crop, None
    before = min(window_share(p.pic, crop) for p in pages)
    if _photo_share(pages, crop) >= FILLED:
        return crop, None
    photos = steady_photos(pages)
    if photos:
        # the photo the window already shows most of, the largest on a tie
        target = max(photos, key=lambda r: (round(_area(_meet(crop, r)), 3), _area(r)))
        faces = [f for p in pages[:1] for f in p.faces]
        new = fill_window(target, crop, pages[0].shape, faces)
        after = min(window_share(p.pic, new) for p in pages)
        if after > before + 1e-6 or _photo_share(pages, new) > _photo_share(pages, crop) + 0.05:
            return new, (before, after)
        return crop, None
    new, before, after = best_window(pages, crop)
    if before >= WINDOW_OK or after - before < GAIN_MIN:
        return crop, None
    return new, (before, after)


def _times(ov):
    t0 = float(ov.get("source_start_s") or 0.0)
    span = float(ov.get("duration_s") or 0.0) * float(ov.get("rate") or 1.0)
    if span < 0.6:
        return [t0 + span / 2]
    return [t0 + f * span for f in SAMPLES]


def repair_windows(overlays, path_of, rep, grab=None, budget_s=BUDGET_S, frames=None, t_start=None):
    """Set every main-upload video overlay's source_crop on one of a collage's photos, off its page.
    path_of(asset_key) -> local file or None. Never raises; what it moved is in rep['collage_windows'].
    Returns the ids it moved."""
    if grab is None:
        from .boards import grab
    t0 = time.monotonic() if t_start is None else t_start
    frames = {} if frames is None else frames
    moved_ids = []
    for ov in overlays:
        if rep.get("collage_windows_budget"):
            break
        try:
            crop = ov.get("source_crop")
            if ov.get("kind") != "video" or not crop:
                continue
            if (crop[2] - crop[0]) > 0.98 and (crop[3] - crop[1]) > 0.98:
                continue
            path = path_of(ov.get("asset_key"))
            if not path:
                continue
            crop = [float(v) for v in crop]
            pages, measured = [], 0
            for t in _times(ov):
                left = budget_s - (time.monotonic() - t0)
                if left <= 0:
                    rep["collage_windows_budget"] = True
                    break
                key = (path, round(t, 1))
                if key not in frames:
                    im = grab(path, t, width=ANALYSIS_W, timeout=max(1.0, left))
                    frames[key] = picture_mask(im)[0] if im is not None else False
                page = frames[key]
                if page is False:
                    continue
                measured += 1
                if page is not None:
                    pages.append(page)
            if rep.get("collage_windows_budget"):
                break
            # a window moves only when every measured frame is a collage page
            if not pages or len(pages) < measured:
                continue
            new, moved = place(pages, crop)
            if moved:
                ov["source_crop"] = new
                moved_ids.append(ov.get("id"))
                rep.setdefault("collage_windows", []).append(
                    {"id": ov.get("id"), "from": [round(v, 4) for v in crop], "to": new,
                     "picture_before": round(moved[0], 3), "picture_after": round(moved[1], 3),
                     "frames": len(pages)})
        except Exception as e:  # noqa: BLE001 - a window that cannot be measured stays as framed
            rep.setdefault("collage_window_errors", []).append(f"{type(e).__name__}: {str(e)[:80]}")
    return moved_ids


def repair_spine(probes, path_of, rep, grab=None, budget_s=BUDGET_S, frames=None, t_start=None):
    """The spine's own shots of a collage, shown through the film's base window (`probes`: video overlays
    of main at the spine's source time, carrying that window as source_crop). Returns the ones whose
    window moved onto a photo: laid over the spine they show the same moment, filled with one photo
    (a video overlay is silent: the spine's sound plays on)."""
    moved = set(repair_windows(probes, path_of, rep, grab=grab, budget_s=budget_s, frames=frames, t_start=t_start))
    out = [p for p in probes if p.get("id") in moved]
    if out:
        rep["collage_spine_shots"] = len(out)
    return out
