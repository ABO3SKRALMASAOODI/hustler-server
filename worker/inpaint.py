"""Round 39 — finding burned-in text, and removing it (or an object) FOR REAL.

Two capabilities live here, and they exist because of the same recurring
support case: a user uploads footage that already has captions burned into the
pixels and asks for them gone, or asks for them in a different font.

Before this the honest answer was "pixels cannot be un-burned": the agent could
only hide them behind a blur or a black bar, and it had to GUESS the rectangle
from a contact sheet, which it regularly got wrong — a bar floating above the
text, or half of it still showing. Both halves of that are fixed here:

  detect_text_regions()  measures where the text actually is, from the frames,
                         and returns rectangles in frame fractions. Nothing is
                         estimated by a vision model any more.

  clean_video()          repaints those pixels: the text is gone and the
                         picture behind it is reconstructed, not smudged.

HOW THE REPAINT WORKS (and why it is usually invisible)

A burned caption is thin, high-contrast ink over background that is still
visible in OTHER frames — the words change, so a pixel covered by a stroke of
"E" at 3.0s is clean background at 3.4s. So:

  pass 1  sample frames across the region's time span, build a per-pixel
          MEDIAN of every sample in which that pixel was NOT ink. That median
          is a real photograph of what is behind the text ("the plate"), and
          it is only trusted where enough clean samples agree and the shot is
          static enough for them to line up.
  pass 2  stream every frame; where this frame has ink, paste the plate.
          Pixels the plate never saw (a caption that never moves, an object
          that is always there) fall back to cv2.inpaint, which reconstructs
          from the surrounding pixels — imperfect on busy detail, excellent on
          the thin strokes that captions are made of.

Everything is feathered, so no rectangle edge is visible in the result. The
output keeps the source's duration, frame rate and audio, so every timestamp
the index, transcript and EDL already hold stays valid: the cleaned file is a
drop-in replacement for the source, which is exactly how the renderer uses it.
"""
import os
import subprocess
import warnings

import cv2
import numpy as np

import config
import media

# Text-line geometry, as fractions of frame height/width. A burned caption is
# never a 4px sliver and never half the frame tall; these bounds are what keep
# a detailed background (foliage, brickwork, a crowd) from voting as "text".
MIN_LINE_H = 0.012
MAX_LINE_H = 0.28
MIN_LINE_W = 0.035
MIN_ASPECT = 1.4
# A frame where "text" covers this much of the picture is not a frame with
# text on it — it is a frame the response threshold failed on.
MAX_INK_FRAC = 0.22
# Fraction of sampled frames a pixel must be inside a text line to count as a
# stable burned-in region. Captions come and go with speech, so this is low;
# the geometry filters above do the discriminating.
DEFAULT_MIN_VOTES = 0.22
DEFAULT_SAMPLES = 28

# Plate quality gates.
PLATE_MIN_CLEAN = 3          # clean samples needed before a pixel is trusted
PLATE_STATIC_MAD = 7.0       # mean abs deviation (0-255) for "same shot"


def _odd(n, lo=3):
    n = int(round(n))
    if n < lo:
        n = lo
    return n if n % 2 else n + 1


def _grab(path, t, width, height):
    """One BGR frame at t seconds, at DISPLAY geometry.

    Input-seek (-ss before -i) so a sample 15 minutes in costs a seek, not a
    15-minute decode. scale to the probed display size for the same reason
    probe() reports it: ffmpeg auto-rotates before filters, so this frame is
    what a player shows, and every rectangle derived from it is in the same
    coordinate space the renderer and the agent use.
    """
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
           "-frames:v", "1", "-vf", f"scale={width}:{height}",
           "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,
                          timeout=config.FFMPEG_TIMEOUT_S)
    need = width * height * 3
    if len(proc.stdout) < need:
        return None
    return np.frombuffer(proc.stdout[:need], np.uint8).reshape(height, width, 3)


def _stroke_response(gray, scale):
    """Ink response: bright-on-dark AND dark-on-bright strokes.

    A top-hat alone finds white captions on dark footage and misses black
    captions on bright footage; the max of top-hat and black-hat finds both,
    and finds white text with a black outline (the most common style) twice
    over.
    """
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (scale, scale))
    top = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k)
    bot = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
    return cv2.max(top, bot)


def ink_mask(bgr, *, dilate=2, min_response=28):
    """Per-pixel mask of TEXT-LIKE ink in a frame or crop.

    min_response is the floor that keeps Otsu honest: on a flat, textless crop
    Otsu still splits the noise down the middle and returns a confident mask of
    nothing. Requiring real local contrast first means a clean frame returns a
    clean mask.
    """
    if bgr.size == 0:
        return np.zeros(bgr.shape[:2], np.uint8)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    resp = _stroke_response(gray, _odd(max(3, h * 0.10), 3))
    if float(resp.max()) < min_response:
        return np.zeros(gray.shape, np.uint8)
    _, m = cv2.threshold(resp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m[resp < min_response] = 0
    if dilate:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                       iterations=dilate)
    return m


def _line_mask(bgr):
    """Mask of text LINES in a full frame — glyphs merged, noise dropped."""
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resp = _stroke_response(gray, _odd(H * 0.018))
    resp = cv2.GaussianBlur(resp, (3, 3), 0)
    if float(resp.max()) < 30:
        return np.zeros((H, W), np.uint8)
    _, b = cv2.threshold(resp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b[resp < 24] = 0
    if b.mean() > MAX_INK_FRAC * 255:
        return np.zeros((H, W), np.uint8)
    # Glyphs -> words -> lines. The kernel is wide and short on purpose: text
    # is a horizontal run of ink with vertical gaps between lines, and closing
    # with a tall kernel merges a caption into the picture above it.
    kx = cv2.getStructuringElement(cv2.MORPH_RECT,
                                   (_odd(W * 0.022, 9), _odd(H * 0.006, 3)))
    lines = cv2.morphologyEx(b, cv2.MORPH_CLOSE, kx)
    out = np.zeros((H, W), np.uint8)
    cnts, _ = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (MIN_LINE_H * H <= h <= MAX_LINE_H * H):
            continue
        if w < MIN_LINE_W * W or w > 0.99 * W:
            continue
        if w / float(h) < MIN_ASPECT:
            continue
        # Ink density inside the box, measured on the PRE-closing response so
        # it describes strokes, not the merge. The floor drops stray edges (a
        # roofline, a sliver of UI). There is deliberately no tight ceiling:
        # burned captions are set bold with a heavy outline or shadow, which is
        # most of the line box once the strokes are dilated — an earlier 0.75
        # ceiling threw away two of every four real caption frames. Flat
        # graphic blocks are not the risk a ceiling would catch anyway (a flat
        # fill has almost NO top-hat response inside it, so it scores low);
        # dense texture is handled by MAX_INK_FRAC and by temporal voting.
        dens = float(b[y:y + h, x:x + w].mean()) / 255.0
        if not (0.08 <= dens <= 0.97):
            continue
        out[y:y + h, x:x + w] = 255
    return out


def detect_text_regions(path, *, start=None, end=None, samples=DEFAULT_SAMPLES,
                        min_votes=DEFAULT_MIN_VOTES, max_regions=6):
    """Where is burned-in text in this video?

    Returns a list of dicts with the rectangle in FRAME FRACTIONS (the same
    coordinate space blur_region/erase_region take), the time span it was seen
    over, and a kind: 'captions' (wide, changes between samples — subtitle
    text), 'watermark' (small, identical in every sample — a logo or handle) or
    'text' (anything else: a headline, a UI label, a sign).

    Deliberately measured on the video itself rather than asked of a vision
    model: the model's rectangles were the thing that kept landing in the wrong
    place, and a wrong rectangle is worse than none — it censors the picture
    and leaves the text.
    """
    info = media.probe(path)
    W, H = int(info["width"]), int(info["height"])
    dur = float(info["duration"])
    s = 0.0 if start is None else max(0.0, float(start))
    e = dur if end is None else min(dur, float(end))
    if e - s < 0.05:
        s, e = 0.0, dur
    n = max(6, min(int(samples), 60))
    # Sample inside the span, never exactly on its edges: the first and last
    # frame of a clip are the two most likely to be a fade, a black frame or a
    # title card, and both would skew every vote.
    step = (e - s) / float(n + 1)
    times = [s + step * (i + 1) for i in range(n)]

    votes = np.zeros((H, W), np.float32)
    seen = []                       # (t, line_mask) for time spans + motion
    for t in times:
        f = _grab(path, t, W, H)
        if f is None:
            continue
        m = _line_mask(f)
        seen.append((t, m, f))
        votes += (m > 0)
    if not seen:
        return []
    hot = (votes >= max(1.0, min_votes * len(seen))).astype(np.uint8) * 255
    if not hot.any():
        return []
    # Merge neighbouring lines (a two-line caption, a stacked handle) into one
    # region, so the agent gets one rectangle to erase rather than five.
    hot = cv2.morphologyEx(
        hot, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (_odd(W * 0.05, 9), _odd(H * 0.05, 9))))
    cnts, _ = cv2.findContours(hot, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 0.0006 * W * H:
            continue
        # Pad: outlines, drop shadows and antialiasing live just outside the
        # ink, and a rectangle that clips them leaves a visible ghost.
        px, py = int(round(w * 0.03 + 4)), int(round(h * 0.12 + 4))
        x0, y0 = max(0, x - px), max(0, y - py)
        x1, y1 = min(W, x + w + px), min(H, y + h + py)
        box = (x0, y0, x1, y1)

        active = [(t, m) for t, m, _f in seen
                  if m[y0:y1, x0:x1].mean() > 12]
        if not active:
            continue
        crops = [f[y0:y1, x0:x1] for _t, _m, f in seen
                 if _m[y0:y1, x0:x1].mean() > 12]
        # Does the CONTENT change between samples? Subtitles do (new words);
        # a baked-in logo does not. This is what separates "captions" from
        # "watermark" without asking a model to guess.
        churn = 0.0
        for a, b in zip(crops, crops[1:]):
            churn = max(churn, float(np.mean(cv2.absdiff(
                cv2.cvtColor(a, cv2.COLOR_BGR2GRAY),
                cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)))))
        fw, fh = (x1 - x0) / float(W), (y1 - y0) / float(H)
        cx = (x0 + x1) / 2.0 / W
        wide = fw > 0.30 and abs(cx - 0.5) < 0.22
        if wide and churn > 6.0:
            kind = "captions"
        elif fw < 0.30 and fh < 0.16 and churn <= 6.0:
            kind = "watermark"
        else:
            kind = "text"
        out.append({
            "kind": kind,
            "x": round(x0 / W, 4), "y": round(y0 / H, 4),
            "w": round(fw, 4), "h": round(fh, 4),
            "first_s": round(min(t for t, _ in active), 2),
            "last_s": round(max(t for t, _ in active), 2),
            "coverage": round(len(active) / float(len(seen)), 3),
            "changes": round(churn, 1),
            "box_px": box,
        })
    out.sort(key=lambda r: -(r["w"] * r["h"] * r["coverage"]))
    return out[:max_regions]


# --------------------------------------------------------------------------- #
#  Removal                                                                     #
# --------------------------------------------------------------------------- #

class _Region:
    """One rectangle to repaint, with its background plate.

    Two rectangles, not one. The INNER box is what gets repainted — the user's
    (or the detector's) rectangle. The processing crop is that box plus a
    margin, because cv2.inpaint reconstructs from the pixels around a hole: if
    the crop and the hole are the same rectangle there is no "around", and a
    solid object came back completely untouched (found by the object-removal
    test — it inpainted a 100%-masked crop and returned it unchanged).
    """

    def __init__(self, spec, W, H):
        self.W, self.H = W, H
        self.set_box(
            max(0, min(W - 2, int(round(float(spec["x"]) * W)))),
            max(0, min(H - 2, int(round(float(spec["y"]) * H)))),
            int(round((float(spec["x"]) + float(spec["w"])) * W)),
            int(round((float(spec["y"]) + float(spec["h"])) * H)))
        self.start = spec.get("start")
        self.end = spec.get("end")
        # 'text' repaints only the glyphs (keeps the picture behind them);
        # 'box' repaints the whole rectangle (an object, a logo shape, a word
        # printed on a sign — anything that is not thin ink on a background).
        self.fill = "box" if str(spec.get("fill") or "text") == "box" else "text"
        self.escalated = False    # stroke repaint -> whole box
        self.plate = None
        self.plate_ok = None        # bool mask: plate is trustworthy here
        self.static = False

    def set_box(self, bx0, by0, bx1, by1):
        """(Re)place the inner rectangle and rebuild the processing crop.

        Used once at construction, and again when the region turns out to sit
        on a backing bar that is bigger than the text line: the margin has to
        end up OUTSIDE the bar, or inpaint reconstructs the hole from more bar
        and the bar survives its own removal.
        """
        W, H = self.W, self.H
        bx0 = max(0, min(W - 2, int(bx0)))
        by0 = max(0, min(H - 2, int(by0)))
        self.bx0, self.by0 = bx0, by0
        self.bx1 = max(bx0 + 2, min(W, int(bx1)))
        self.by1 = max(by0 + 2, min(H, int(by1)))
        pad_x = max(12, int(round((self.bx1 - self.bx0) * 0.10)))
        pad_y = max(12, int(round((self.by1 - self.by0) * 0.35)))
        self.x0, self.y0 = max(0, self.bx0 - pad_x), max(0, self.by0 - pad_y)
        self.x1, self.y1 = min(W, self.bx1 + pad_x), min(H, self.by1 + pad_y)
        self.ix0, self.iy0 = self.bx0 - self.x0, self.by0 - self.y0
        self.ix1, self.iy1 = self.bx1 - self.x0, self.by1 - self.y0

    def active(self, t):
        if self.start is None:
            return True
        return float(self.start) - 1e-6 <= t <= float(self.end) + 1e-6

    def crop(self, frame):
        """The PADDED band — what gets processed and written back."""
        return frame[self.y0:self.y1, self.x0:self.x1]

    def mask_for(self, band, dilate=3):
        """Mask in padded-band coordinates. Never marks a pixel outside the
        inner box: the margin is context to reconstruct FROM, and erasing ink
        the user did not point at (the next line of a two-line caption, a sign
        behind the subject) would be a change they never asked for.

        `dilate` is wider when EXCLUDING pixels from the background plate than
        when repainting: a glyph's antialiased edge is not caught by the
        threshold, and letting those half-lit pixels into the median painted a
        faint ghost of the words back into the picture.
        """
        m = np.zeros(band.shape[:2], np.uint8)
        inner = band[self.iy0:self.iy1, self.ix0:self.ix1]
        if self.fill == "box":
            m[self.iy0:self.iy1, self.ix0:self.ix1] = 255
        else:
            m[self.iy0:self.iy1, self.ix0:self.ix1] = ink_mask(inner,
                                                               dilate=dilate)
        return m


def _has_backing_bar(path, region, W, H, dur, samples=3):
    """Is this caption sitting on a solid bar rather than straight on the
    picture? (News lower-thirds and one of CapCut's default styles.)

    It matters because repainting only the letter strokes would lift the TEXT
    off the bar and leave the bar — a grey rectangle floating in the shot,
    which measures as "no ink" and would otherwise be reported as a clean
    removal. Detected by comparing the region's non-ink pixels against the
    margin just outside it: a bar is both far from its surroundings in colour
    and unnaturally flat.
    """
    s = 0.0 if region.start is None else float(region.start)
    e = dur if region.end is None else float(region.end)
    step = max(0.05, (max(e, s + 0.1) - s) / float(samples + 1))
    votes = 0
    seen = 0
    for i in range(samples):
        f = _grab(path, s + step * (i + 1), W, H)
        if f is None:
            continue
        band = region.crop(f)
        inner = band[region.iy0:region.iy1, region.ix0:region.ix1]
        if inner.size == 0:
            continue
        ink = ink_mask(inner, dilate=3) > 0
        if ink.all():
            continue
        seen += 1
        keep = ~ink
        in_px = inner[keep].astype(np.float32)
        margin = band.astype(np.float32).copy()
        margin[region.iy0:region.iy1, region.ix0:region.ix1] = np.nan
        out_px = margin[~np.isnan(margin[:, :, 0])]
        if in_px.size < 50 or out_px.size < 50:
            continue
        med = np.median(in_px, axis=0)
        # Flatness as the SHARE of non-ink pixels near their own median, not
        # as a standard deviation: the mask never catches every antialiased
        # letter edge, and a handful of leftover white pixels put the sd of a
        # perfectly flat bar at 82 — which read as "busy picture" and let the
        # bar survive. A share is unmoved by those few outliers.
        flat = float(np.mean(np.all(np.abs(in_px - med) < 24, axis=1)))
        gap = float(np.abs(med - np.median(out_px.reshape(-1, 3),
                                           axis=0)).mean())
        # Measured separation on real styles: a bar scores gap 60-70 /
        # flat 0.58-0.82, captions straight on the picture score gap 1-7 /
        # flat ~0.40.
        if gap > 25.0 and flat > 0.50:
            votes += 1
    return seen > 0 and votes >= max(1, seen - 1)


def _grow_to_bar(path, region, W, H, dur):
    """Expand the rectangle to the full extent of the backing bar.

    The detector measures the TEXT LINE; the bar behind it is taller and
    usually wider. Repainting only the line leaves two dark strips, and — worse
    — the reconstruction would be sampled from the bar itself. The bar is found
    by colour: from a sample frame, take the median of the region's non-ink
    pixels (that IS the bar), mask everything close to it in a generously
    expanded crop, and take the connected blob that contains the text.
    """
    # Sampled across the span and UNIONED, never from one frame: the bar is as
    # wide as the line it carries, so a bar measured on the shortest caption
    # leaves both ends of the longest one standing in the picture.
    s = 0.0 if region.start is None else float(region.start)
    e = dur if region.end is None else float(region.end)
    e = max(e, s + 0.1)
    step = (e - s) / 6.0
    found = []
    for i in range(5):
        got = _bar_box_at(path, region, W, H, s + step * (i + 1))
        if got:
            found.append(got)
    if not found:
        return
    x0 = min(b[0] for b in found)
    y0 = min(b[1] for b in found)
    x1 = max(b[2] for b in found)
    y1 = max(b[3] for b in found)
    # +3px: a bar's own edge is antialiased against the picture, and leaving
    # that one-pixel rim behind draws a perfect outline of the thing that was
    # just removed.
    region.set_box(x0 - 3, y0 - 3, x1 + 3, y1 + 3)


def _bar_box_at(path, region, W, H, t):
    """The backing bar's bounding box in ONE frame, or None."""
    f = _grab(path, t, W, H)
    if f is None:
        return None
    bh = region.by1 - region.by0
    bw = region.bx1 - region.bx0
    gx0 = max(0, region.bx0 - int(bw * 0.25) - 20)
    gx1 = min(W, region.bx1 + int(bw * 0.25) + 20)
    gy0 = max(0, region.by0 - int(bh * 1.6) - 12)
    gy1 = min(H, region.by1 + int(bh * 1.6) + 12)
    crop = f[gy0:gy1, gx0:gx1]
    inner = f[region.by0:region.by1, region.bx0:region.bx1]
    ink = ink_mask(inner, dilate=3) > 0
    if ink.all() or inner.size == 0:
        return None
    bar = np.median(inner[~ink].astype(np.float32), axis=0)
    close = (np.abs(crop.astype(np.float32) - bar).max(axis=2) < 34
             ).astype(np.uint8) * 255
    close = cv2.morphologyEx(
        close, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (_odd(bw * 0.3, 9), 5)))
    n, lab, stats_, _ = cv2.connectedComponentsWithStats(close, 8)
    cy = (region.by0 + region.by1) // 2 - gy0
    cx = (region.bx0 + region.bx1) // 2 - gx0
    lid = lab[max(0, min(lab.shape[0] - 1, cy)),
              max(0, min(lab.shape[1] - 1, cx))]
    if lid == 0:                       # centre is text, not bar: try any row
        rows = np.where(close.any(axis=1))[0]
        if not len(rows):
            return None
        lid = lab[rows[len(rows) // 2]][close[rows[len(rows) // 2]].argmax()]
    if lid == 0 or lid >= n:
        return None
    x, y, w, h, area = stats_[lid]
    if area < 0.25 * (bw * bh):        # not a bar, just a blotch
        return None
    return (gx0 + x, gy0 + y, gx0 + x + w, gy0 + y + h)


def _build_plate(path, region, W, H, dur, samples=22):
    """Photograph what is behind the ink, from frames where it is not there."""
    s = 0.0 if region.start is None else float(region.start)
    e = dur if region.end is None else float(region.end)
    e = max(e, s + 0.1)
    step = (e - s) / float(samples + 1)
    stack, masks = [], []
    for i in range(samples):
        f = _grab(path, s + step * (i + 1), W, H)
        if f is None:
            continue
        band = region.crop(f).astype(np.uint8)
        stack.append(band)
        masks.append(region.mask_for(band, dilate=5) > 0)
    if len(stack) < PLATE_MIN_CLEAN:
        return
    arr = np.stack(stack).astype(np.float32)
    ink = np.stack(masks)
    clean = ~ink
    counts = clean.sum(axis=0)
    # Is this the same picture in every sample? If the camera moves, frame A's
    # background is not frame B's, and pasting one into the other is worse than
    # inpainting. Measured only where every sample is clean, so the changing
    # TEXT can never be mistaken for a moving camera.
    everywhere = counts == len(stack)
    if everywhere.any():
        vals = arr[:, everywhere]
        region.static = bool(np.mean(np.abs(vals - vals.mean(axis=0)))
                             < PLATE_STATIC_MAD)
    masked = np.where(clean[..., None], arr, np.nan)
    # A pixel that is ink in EVERY sample (a caption that never moves, an
    # object that is always there) has no clean value to take a median of.
    # nanmedian warns and returns NaN for those; both are expected — they are
    # exactly the pixels plate_ok excludes and cv2.inpaint then reconstructs.
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(masked, axis=0)
    med = np.nan_to_num(med, nan=0.0)
    region.plate = med.astype(np.uint8)
    ok = (counts >= PLATE_MIN_CLEAN) & region.static
    # Does the plate itself still show the text? Where a pixel had only a
    # handful of clean samples, those few can carry a neighbouring word's
    # edge, and the median then paints a faint ghost of the caption back into
    # the picture — visible even when the ink measurement says it is gone.
    # Any pixel the plate is inky at is handed to cv2.inpaint instead.
    if region.plate is not None and region.plate.size:
        ghost = ink_mask(region.plate, dilate=2) > 0
        ok = ok & ~ghost
    region.plate_ok = ok


def _grain_sigma(band, mask):
    """How much high-frequency noise the UNTOUCHED part of this band carries.

    Video is never smooth: sensor noise, film grain and codec dither give every
    real frame a texture. cv2.inpaint returns a perfectly smooth fill, so a
    repainted rectangle reads as a plastic patch against its own surroundings
    even when the colour is right. Matching that texture is what makes the
    repaint disappear rather than merely lose its text.
    """
    keep = ~(mask > 0)
    if keep.sum() < 200:
        return 0.0
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hi = gray - cv2.GaussianBlur(gray, (0, 0), 1.2)
    return float(np.clip(np.std(hi[keep]), 0.0, 12.0))


def _repaint(band, mask, region, rng=None):
    """Replace the masked pixels of one band, feathered and re-grained."""
    if not mask.any():
        return band
    out = band
    m = mask > 0
    if region.plate is not None and region.plate_ok is not None:
        use = m & region.plate_ok
        if use.any():
            out = np.where(use[..., None], region.plate, out)
        m = m & ~region.plate_ok
    filled = np.zeros(mask.shape, bool)
    if m.any():
        # TELEA reconstructs from the boundary inwards — the right algorithm
        # for thin strokes and small shapes, which is what is left here.
        out = cv2.inpaint(np.ascontiguousarray(out),
                          (m.astype(np.uint8) * 255), 3, cv2.INPAINT_TELEA)
        filled = m
    sigma = _grain_sigma(band, mask)
    if sigma > 0.6 and filled.any():
        noise = (rng or np.random).normal(0.0, sigma, filled.shape)
        out = np.clip(out.astype(np.float32)
                      + noise[..., None] * filled[..., None], 0, 255)
    soft = cv2.GaussianBlur((mask > 0).astype(np.float32), (0, 0), 1.6)
    soft = np.clip(soft, 0.0, 1.0)[..., None]
    return (band.astype(np.float32) * (1.0 - soft)
            + np.asarray(out, np.float32) * soft).astype(np.uint8)


def clean_video(src, regions, out_full, out_proxy=None, *, progress_cb=None,
                crf=18, preset="veryfast"):
    """Repaint `regions` out of `src`, writing a full-res file (and optionally
    a proxy) that is a drop-in replacement for the source.

    Duration, frame rate and audio are preserved deliberately: the project's
    index, transcript, shot list and every EDL timestamp were measured against
    the original, and they all have to keep pointing at the same moment.
    """
    info = media.probe(src)
    W, H = int(info["width"]), int(info["height"])
    fps = max(1.0, min(float(info["fps"]) or 30.0, 120.0))
    dur = float(info["duration"])
    # Anamorphic material (a 16:9 picture stored in 4:3 pixels) carries a
    # non-1 pixel aspect. The cleaned file is written square-pixel, so widen
    # to the DISPLAY width here — otherwise the replacement source would be
    # the same picture horizontally squashed, and every later crop/reframe
    # would inherit the squash.
    sar = float(info.get("sar") or 1.0)
    if abs(sar - 1.0) > 0.001:
        W = max(2, int(round(W * sar / 2)) * 2)
    regs = [_Region(r, W, H) for r in regions]
    if not regs:
        raise ValueError("no regions to clean")
    for r in regs:
        # Escalate stroke-repaint to whole-rectangle when the text turns out to
        # sit on a solid bar, BEFORE the plate is built (the plate depends on
        # which pixels count as ink). Never the other way round: a caller that
        # explicitly asked for 'box' means it.
        if r.fill == "text" and _has_backing_bar(src, r, W, H, dur):
            _grow_to_bar(src, r, W, H, dur)
            r.fill = "box"
            r.escalated = True
        _build_plate(src, r, W, H, dur)

    frame_bytes = W * H * 3
    expected = max(1, int(round(dur * fps)))

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", src,
         "-vf", f"scale={W}:{H},fps={fps:.5f}",
         "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        # Capped, not proportional: `frame_bytes * 4` is 44 MB of Python-side
        # buffer at 4K, on the smallest box in the fleet, for no throughput
        # gain — the loop consumes a frame as fast as it is produced.
        bufsize=min(frame_bytes * 4, 8 << 20))

    # ONE encoder, and a bounded one. This used to be a single ffmpeg with two
    # output files, i.e. two live x264 encoders — at 1440x2560 their frame
    # buffers alone ran to ~1 GB and the container was OOM-killed, taking the
    # whole worker (and every other user's in-flight turn) down with it. The
    # proxy is derived in a second pass below, from a file that is already on
    # disk, so the two encoders are never alive at the same time.
    x264p = (f"rc-lookahead={config.CLEAN_X264_LOOKAHEAD}:sync-lookahead=0")
    enc_cmd = ["ffmpeg", "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
               "-r", f"{fps:.5f}", "-i", "pipe:0", "-i", src,
               "-map", "0:v:0", "-map", "1:a?",
               "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
               "-threads", str(config.CLEAN_X264_THREADS),
               "-x264-params", x264p,
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", out_full]
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    touched = 0
    i = 0
    last = None
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            t = i / fps
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            active = [r for r in regs if r.active(t)]
            if active:
                frame = frame.copy()
                for r in active:
                    band = r.crop(frame)
                    mask = r.mask_for(band)
                    if mask.any():
                        frame[r.y0:r.y1, r.x0:r.x1] = _repaint(band, mask, r)
                        touched += 1
            last = frame
            enc.stdin.write(frame.tobytes() if frame.flags["C_CONTIGUOUS"]
                            else np.ascontiguousarray(frame).tobytes())
            i += 1
            if progress_cb and i % 30 == 0:
                progress_cb(min(0.99, i / float(expected)))
        # A picture track can end before the container does (iOS screen
        # recordings stop emitting frames on a static screen). make_proxy pads
        # by cloning the last frame for exactly this reason; do the same, or
        # the cleaned file would be SHORTER than the source it replaces and
        # every EDL timestamp past that point would fall off the end.
        while last is not None and i < expected:
            enc.stdin.write(np.ascontiguousarray(last).tobytes())
            i += 1
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        dec.stdout.close()
        dec.wait(timeout=30)
        err = enc.stderr.read().decode("utf-8", "replace")[-800:]
        rc = enc.wait(timeout=config.FFMPEG_TIMEOUT_S)
    if rc != 0 or not os.path.exists(out_full):
        raise media.MediaError(f"clean encode failed: {err or 'no output'}")
    # Second pass, from the finished full-res file — cheap (it decodes an
    # already-small H.264 instead of holding raw frames) and, crucially, it
    # starts only once the first encoder has exited.
    if out_proxy:
        h = config.PROXY_HEIGHT
        media.run(["ffmpeg", "-y", "-v", "error", "-i", out_full,
                   "-vf", rf"scale=-2:min({h}\,floor(ih/2)*2)",
                   "-c:v", "libx264", "-preset", config.PROXY_PRESET,
                   "-crf", str(config.PROXY_CRF),
                   "-threads", str(config.CLEAN_X264_THREADS),
                   "-x264-params", x264p,
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", out_proxy])
    if progress_cb:
        progress_cb(1.0)
    return {"frames": i, "frames_touched": touched,
            "width": W, "height": H, "fps": round(fps, 3),
            "duration_s": round(dur, 3),
            "plates": [{"static": r.static,
                        "plate": r.plate is not None,
                        "fill": r.fill,
                        "escalated": r.escalated} for r in regs]}


def snap_box_to_ink(path, box, *, start=None, end=None, samples=12,
                    pad_frac=0.02):
    """Tighten a ROUGH rectangle onto the ink that is actually inside it.

    detect_text_regions is deliberately blind to a whole class of real marks:
    it votes on horizontal LINE structure, which is what a subtitle looks like,
    and a soft semi-transparent wordmark floating in the upper third never
    accumulates enough votes. A real user asked to remove a "Dream Life"
    watermark on 2026-07-25 — the detector found nothing twice, the vision model
    read it off the frames instantly, and the agent was left estimating a box
    by eye, which is the one thing round 39 exists to stop.

    So this closes the loop the other way round: vision says WHERE to look, and
    the pixels still decide the rectangle. Given a coarse box it measures the
    ink inside it across `samples` frames and returns the tight box in frame
    fractions plus the coverage, or None when there is no ink there — which is
    the honest answer when the model imagined the mark.
    """
    info = media.probe(path)
    W, H = int(info["width"]), int(info["height"])
    dur = float(info["duration"])
    s = 0.0 if start is None else max(0.0, float(start))
    e = dur if end is None else min(dur, float(end))
    if e - s < 0.05:
        s, e = 0.0, dur
    # Look a little OUTSIDE the given box: a model's rectangle clips the ends
    # of the word about as often as it overshoots, and a clipped erase leaves
    # the last letter on screen.
    gx0 = max(0, int((float(box[0]) - pad_frac) * W))
    gy0 = max(0, int((float(box[1]) - pad_frac) * H))
    gx1 = min(W, int((float(box[0]) + float(box[2]) + pad_frac) * W))
    gy1 = min(H, int((float(box[1]) + float(box[3]) + pad_frac) * H))
    if gx1 - gx0 < 4 or gy1 - gy0 < 4:
        return None
    n = max(4, min(int(samples), 30))
    step = (e - s) / float(n + 1)
    votes = np.zeros((gy1 - gy0, gx1 - gx0), np.float32)
    seen = 0
    for i in range(n):
        f = _grab(path, s + step * (i + 1), W, H)
        if f is None:
            continue
        seen += 1
        votes += (ink_mask(f[gy0:gy1, gx0:gx1], dilate=1) > 0)
    if not seen:
        return None
    # A watermark is the same pixels in every frame, so vote hard: half the
    # samples. That also throws away moving picture detail that happens to
    # look inky in one frame.
    hot = (votes >= max(2.0, 0.5 * seen)).astype(np.uint8) * 255
    if not hot.any():
        return None
    ys, xs = np.nonzero(hot)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    if (x1 - x0) < 3 or (y1 - y0) < 3:
        return None
    coverage = float(hot[y0:y1, x0:x1].mean() / 255.0)
    return {"x": round((gx0 + x0) / W, 4), "y": round((gy0 + y0) / H, 4),
            "w": round((x1 - x0) / W, 4), "h": round((y1 - y0) / H, 4),
            "coverage": round(coverage, 3), "samples": seen,
            "first_s": round(s, 2), "last_s": round(e, 2)}


def text_energy(path, box, *, at=None, samples=6):
    """Mean ink response inside `box` (x, y, w, h fractions) across samples.

    The honesty check: run it before and after a clean and the number says
    whether the text actually went away, instead of the agent claiming it did.
    """
    info = media.probe(path)
    W, H = int(info["width"]), int(info["height"])
    dur = float(info["duration"])
    x0 = max(0, int(box[0] * W))
    y0 = max(0, int(box[1] * H))
    x1 = min(W, int((box[0] + box[2]) * W))
    y1 = min(H, int((box[1] + box[3]) * H))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    times = ([float(at)] if at is not None
             else [dur * (i + 1) / (samples + 1) for i in range(samples)])
    vals = []
    for t in times:
        f = _grab(path, t, W, H)
        if f is None:
            continue
        vals.append(float(ink_mask(f[y0:y1, x0:x1], dilate=0).mean()))
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def run_clean_job(worker_db, job):
    """Executor-side runner for the erase/repaint pass (round 67 — the
    capture/frames/track/matte shape: synchronous, no row, remote failure
    never falls back to heavy local work).

    The clean pass decodes and re-encodes EVERY frame of the user's ORIGINAL,
    plus an optional full-res cursor pass — the single heaviest thing an
    agent turn could still do on the dispatcher, and the strongest suspect in
    the Jul 31 2026 worker death that ate a customer's turn minutes after her
    erase ran (job 1557: "Worker died and retries are exhausted"). Every
    other original-decoder had already moved to the executor for exactly this
    failure class; this is the last one following.

    payload: {src_key, out_key, proxy_key, regions, cursor,
              measure: [region boxes for the before/after ink check]}
    -> clean_video's stats dict plus:
       before / after   ink energies per measure box (the honesty check,
                        measured HERE so the dispatcher never decodes the
                        original or the cleaned full-res file at all)
       out_bytes / proxy_bytes   sizes for the asset bookkeeping rows
    On success both objects are already uploaded; nothing heavy rides back.
    `worker_db` is unused — this writes no rows.
    """
    import shutil
    import uuid as _uuid

    import storage
    payload = job.get("payload") or {}
    src_key = payload.get("src_key")
    out_key = payload.get("out_key")
    proxy_key = payload.get("proxy_key")
    regions = payload.get("regions") or []
    cursor_cfg = payload.get("cursor") or None
    if not src_key or not out_key or not proxy_key:
        raise ValueError("clean job needs src_key, out_key, proxy_key")
    if not regions and not cursor_cfg:
        raise ValueError("clean job needs regions and/or a cursor pass")
    workdir = os.path.join(config.TMP_DIR, f"cln_{_uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        src = os.path.join(workdir, "src" + os.path.splitext(src_key)[1])
        storage.download_to(src_key, src)
        measure = payload.get("measure") or []
        before = [text_energy(src, (b["x"], b["y"], b["w"], b["h"]),
                              samples=5) for b in measure]
        out = os.path.join(workdir, "clean.mp4")
        prox = os.path.join(workdir, "clean_proxy.mp4")
        mid = None
        stats = None
        if regions and cursor_cfg:
            # Same chaining rule as the dispatcher's local path: repaint
            # first into a full-res intermediate ON DISK (never two live
            # encoders), then the cursor pass derives the proxy at the end.
            mid = os.path.join(workdir, "clean_mid.mp4")
            stats = clean_video(src, regions, mid)
        elif regions:
            stats = clean_video(src, regions, out, prox)
        if cursor_cfg:
            import cursor as cursorlib
            stats = cursorlib.enhance(
                mid or src, out, prox,
                scale=float(cursor_cfg.get("scale", 2.0)),
                smoothing=float(cursor_cfg.get("smoothing", 0.5)),
                click_highlight=bool(cursor_cfg.get("click_highlight", True)),
                click_times=cursor_cfg.get("click_times") or [])
        after = [text_energy(out, (b["x"], b["y"], b["w"], b["h"]),
                             samples=5) for b in measure]
        storage.upload_file(out, out_key, "video/mp4")
        storage.upload_file(prox, proxy_key, "video/mp4")
        stats = dict(stats or {})
        stats.update({"before": before, "after": after,
                      "out_bytes": os.path.getsize(out),
                      "proxy_bytes": os.path.getsize(prox)})
        return stats
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
