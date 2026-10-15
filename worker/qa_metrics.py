"""Acceptance metrics for rendered devices (ROADMAP §4.5, INTERFACES §8.3).

Every number a device card's ``verify`` list, a refbench case or a proof
loop quotes comes from here, so "measured, not eyeballed" means one
definition per word:

    METRICS = {name: fn}
    fn(**inputs, bar=None) -> {"value": float, "pass": bool | None,
                               "detail": {...}}

``bar`` overrides the default bar of §4.5. It is ``"<=1"``, ``">= 0.92"``,
``("<=", 1)`` or a bare number (read with the metric's own direction).
``pass`` is ``None`` when a metric has no default bar and none was given
(``cost_ratio``), or when the caller opted out (``flash_risk`` with
``opted_in=True`` reports and flags instead of failing).

Pinned names (do not rename; cards and cases cite them):
    land_err_f, matte_iou, matte_flicker, edge_chatter, glyph_visible,
    legibility, reproj_rms_px, region_locality, duration_exact, flash_risk,
    determinism, cost_ratio.
Device-specific metrics (``fill_iou`` ...) belong to the track that needs
them; that track adds them with ``register`` from its own module.

Inputs are plain numpy arrays (masks as float 0..1 or bool, frames as uint8)
or media paths; the media helpers decode with the ``ffmpeg`` on PATH.
Nothing here imports the worker's config, a model or a browser: the module
runs on every lane, in CI and inside refbench.

``flash_risk`` (the Harding-style counter on RENDERED frames) and
``flash_safety.plan`` (0C's planning-time thinner) are separate on purpose.
"""

import hashlib
import json
import math
import operator
import re
import subprocess

import numpy as np

METRICS = {}
# §4.5 default bars: (op, value). Missing -> no default (pass is None).
DEFAULT_BARS = {
    "land_err_f": ("<=", 1.0),
    "matte_iou": (">=", 0.92),
    "matte_flicker": ("<=", 0.035),
    "edge_chatter": ("<=", 0.002),
    "glyph_visible": (">=", 0.65),
    "legibility": (">=", 60.0),
    "reproj_rms_px": ("<=", 1.5),
    "region_locality": ("<=", 0.0),
    "duration_exact": ("<=", 0.0),
    "flash_risk": ("<=", 3.0),
    "determinism": ("<=", 0.0),
}
PINNED = ("land_err_f", "matte_iou", "matte_flicker", "edge_chatter",
          "glyph_visible", "legibility", "reproj_rms_px", "region_locality",
          "duration_exact", "flash_risk", "determinism", "cost_ratio")

_OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt,
        ">": operator.gt, "==": operator.eq}
_VERIFY_RE = re.compile(
    r"^\s*([a-z][a-z0-9_]*)\s*(?:(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?))?\s*$")


def register(name, default_bar=None):
    """Decorator: add a metric to METRICS. A device track registers its own
    names from its own module; re-registering a pinned name is refused."""
    if not re.match(r"^[a-z][a-z0-9_]{1,47}$", name or ""):
        raise ValueError(f"bad metric name {name!r}")

    def deco(fn):
        if name in METRICS and METRICS[name] is not fn:
            raise ValueError(f"metric {name!r} is already registered")
        METRICS[name] = fn
        if default_bar is not None:
            DEFAULT_BARS[name] = _norm_bar(default_bar, name)
        return fn
    return deco


def parse_verify(text):
    """'land_err_f<=1' -> ('land_err_f', '<=', 1.0); 'flash_risk' ->
    ('flash_risk', None, None). Raises ValueError on anything else."""
    m = _VERIFY_RE.match(str(text or ""))
    if not m:
        raise ValueError(f"not a verify expression: {text!r}")
    name, op, val = m.group(1), m.group(2), m.group(3)
    return name, op, (float(val) if val is not None else None)


def known(text):
    """True when a verify expression names a registered metric."""
    try:
        return parse_verify(text)[0] in METRICS
    except ValueError:
        return False


def _norm_bar(bar, name=None):
    if bar is None:
        return DEFAULT_BARS.get(name)
    if isinstance(bar, (tuple, list)) and len(bar) == 2:
        op, val = bar
        if op not in _OPS:
            raise ValueError(f"bad bar operator {op!r}")
        return (op, float(val))
    if isinstance(bar, (int, float)):
        op = (DEFAULT_BARS.get(name) or ("<=", 0))[0]
        return (op, float(bar))
    m = re.match(r"^\s*(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?)\s*$", str(bar))
    if not m:
        raise ValueError(f"bad bar {bar!r}")
    return (m.group(1), float(m.group(2)))


def check(value, bar):
    """Does ``value`` meet ``bar`` ((op, v) or a string)? None if no bar."""
    if bar is None:
        return None
    op, v = _norm_bar(bar)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return bool(_OPS[op](float(value), v))


def bar_text(bar):
    if bar is None:
        return None
    op, v = _norm_bar(bar)
    return f"{op}{v:g}"


def _result(name, value, bar, detail, passed=Ellipsis):
    b = _norm_bar(bar, name)
    if passed is Ellipsis:
        passed = check(value, b)
    out = {"value": (None if value is None else float(value)),
           "pass": passed, "detail": detail}
    if b is not None:
        out["bar"] = bar_text(b)
    return out


def evaluate(name, bar=None, **inputs):
    """METRICS[name](**inputs, bar=bar) with a clear error for unknown names."""
    if name not in METRICS:
        raise KeyError(f"unknown metric {name!r}; known: {sorted(METRICS)}")
    return METRICS[name](bar=bar, **inputs)


# ── media helpers (ffmpeg on PATH; no worker imports) ─────────────────────

def probe(path):
    """{width, height, fps, frames (estimate), duration, has_audio}."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,nb_frames,"
         "start_time:format=duration,start_time", "-of", "json", path],
        capture_output=True, check=True).stdout
    j = json.loads(out)
    info = {"width": None, "height": None, "fps": None, "frames": None,
            "duration": float(j.get("format", {}).get("duration") or 0.0),
            "has_audio": False, "start": 0.0,
            "format_start": float(j.get("format", {}).get("start_time")
                                  or 0.0)}
    for s in j.get("streams", []):
        if s.get("codec_type") == "video" and info["width"] is None:
            info["width"], info["height"] = s.get("width"), s.get("height")
            try:
                n, d = str(s.get("r_frame_rate", "0/1")).split("/")
                info["fps"] = float(n) / float(d) if float(d) else None
            except (ValueError, ZeroDivisionError):
                info["fps"] = None
            try:
                info["frames"] = int(s.get("nb_frames"))
            except (TypeError, ValueError):
                info["frames"] = None
            try:
                info["start"] = float(s.get("start_time") or 0.0)
            except (TypeError, ValueError):
                info["start"] = 0.0
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    if info["frames"] is None and info["fps"] and info["duration"]:
        info["frames"] = int(round(info["fps"] * info["duration"]))
    return info


RGB_READ = "neighbor+full_chroma_int+accurate_rnd"
RGB_RESIZE = "area+full_chroma_int+accurate_rnd"


def read_frames(path, *, size=None, gray=False, rect=None, start_frame=0,
                count=None, frames=None, ss=None, seek=False):
    """Decode frames as uint8 arrays.

    size: (w, h) output size (after rect crop); rect: (x, y, w, h) source
    crop; gray: one channel. ``frames`` (a set of indices) keeps only those
    (still one sequential decode); ``start_frame``/``count`` bound the range.
    ``ss`` seeks the input first (seconds; frame indices then count from it).
    ``seek=True`` with ``start_frame`` seeks straight to that frame of a
    constant-frame-rate file (indices stay absolute) instead of decoding
    everything before it.
    Returns (indices, list_of_arrays).
    """
    info = probe(path)
    base = 0
    if seek and start_frame > 0 and info["fps"] and ss is None:
        # frames are the stream's own (passthrough, no duplication), so
        # frame k sits at start + k/fps. An input -ss is measured from the
        # FILE's start (the earliest stream), so the video's own offset
        # from it is added; seek half a frame before the frame.
        ss = (info["start"] - info["format_start"]) + \
            (start_frame - 0.5) / info["fps"]
        base = start_frame
    W, H = info["width"], info["height"]
    vf = []
    if rect:
        x, y, w, h = [int(v) for v in rect]
        if (x | y | w | h) & 1:
            # crop on a 4:2:0 / 4:2:2 stream rounds an odd offset or size
            # DOWN to the chroma grid (607 px wide comes out 606), and every
            # frame read after that is a skewed slice of the stream: crop
            # odd rects on full-resolution pixels instead
            vf.append("format=gray" if gray else
                      f"scale=flags={RGB_READ},format=rgb24")
        vf.append(f"crop={w}:{h}:{x}:{y}")
        W, H = w, h
    if size:
        W, H = int(size[0]), int(size[1])
        vf.append(f"scale={W}:{H}:flags={'area' if gray else RGB_RESIZE}")
    elif not gray:
        # the RGB conversion in the graph, with accurate rounding: left to
        # the output's own converter, ffmpeg 8.x (the production lanes)
        # read every frame ~2 levels dark (BT.709 reference: -2.2 / 9.0:
        # -0.2), which moved colour metrics (AT-1E-6) between versions.
        # Chroma is taken per sample (neighbor), as the stream holds it:
        # an interpolated read smears 4:2:0 chroma over edges that a
        # fringe metric then counts (AT-1E-1)
        vf.append(f"scale=flags={RGB_READ}")
    pix = "gray" if gray else "rgb24"
    ch = 1 if gray else 3
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if ss:
        cmd += ["-ss", f"{float(ss):.6f}"]
    cmd += ["-i", path]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    # passthrough: frame k is the stream's k-th decoded frame. The default
    # constant-rate muxing duplicates the first frame of a stream that
    # starts after t=0 (most trimmed files), which would shift every index.
    cmd += ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix,
            "-"]
    want = set(frames) if frames is not None else None
    last = None
    if want:
        last = max(want)
    elif count is not None:
        last = start_frame + count - 1
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = W * H * ch
    idx, out, i = [], [], base
    try:
        while True:
            if last is not None and i > last:
                break
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            if i >= start_frame and (want is None or i in want):
                a = np.frombuffer(buf, np.uint8).reshape(
                    (H, W) if gray else (H, W, 3))
                idx.append(i)
                out.append(a.copy())
            i += 1
    finally:
        p.kill()
        p.wait()
    return idx, out


def frame_md5s(path, *, vf=None):
    """Per-frame md5 of the decoded video (ffmpeg framemd5), in order."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "framemd5", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout.decode()
    return [ln.rsplit(",", 1)[-1].strip() for ln in out.splitlines()
            if ln and not ln.startswith("#")]


def audio_md5(path, *, start=None, end=None):
    """md5 of the decoded audio (s16le, native rate and layout), or None."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0?"]
    if start is not None:
        cmd += ["-ss", f"{float(start):.6f}"]
    if end is not None:
        cmd += ["-to", f"{float(end):.6f}"]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return hashlib.md5(raw).hexdigest() if raw else None


def loudness_lufs(path, duration=None):
    """Integrated loudness (EBU R128) of the file's audio (its first
    ``duration`` seconds when given), or None."""
    lim = ["-t", f"{float(duration):.3f}"] if duration else []
    p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner"] + lim +
                       ["-i", path, "-map", "0:a:0?", "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True)
    text = p.stderr.decode(errors="replace")
    m = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", text)
    return float(m[-1]) if m else None


# ── colour helpers ────────────────────────────────────────────────────────

def _to_lin(v):
    v = np.asarray(v, np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def luminance(rgb):
    """WCAG relative luminance of '#rrggbb', an (r, g, b) triple or an
    (..., 3) uint8 array (same formula as motion/runtime.js MG.luminance)."""
    if isinstance(rgb, str):
        s = rgb.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        rgb = tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    a = np.asarray(rgb, np.float64) / 255.0
    lin = _to_lin(a)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def apca(text_y, bg_y):
    """APCA |Lc| of text luminance on background luminance — the same
    0.0.98G constants as motion/runtime.js MG.apca, so a render is judged by
    the number its template aimed for."""
    def soft(y):
        y = max(0.0, float(y))
        return y + (0.022 - y) ** 1.414 if y < 0.022 else y
    t, b = soft(text_y), soft(bg_y)
    if b > t:
        s = (b ** 0.56 - t ** 0.57) * 1.14
        return 0.0 if s < 0.1 else (s - 0.027) * 100.0
    s = (b ** 0.65 - t ** 0.62) * 1.14
    return 0.0 if s > -0.1 else -(s + 0.027) * 100.0


def _mask(m):
    a = np.asarray(m)
    if a.dtype == bool:
        return a.astype(np.float32)
    a = a.astype(np.float32)
    if a.max(initial=0.0) > 1.0:
        a = a / 255.0
    if a.ndim == 3:
        a = a[..., 0]
    return np.clip(a, 0.0, 1.0)


def _bin(m, thr=0.5):
    return _mask(m) >= thr


def _dilate(b, r):
    """Binary dilation by a (2r+1) square, numpy only."""
    if r <= 0:
        return b.copy()
    out = b.copy()
    for axis in (0, 1):
        acc = out.copy()
        for k in range(1, r + 1):
            acc |= np.roll(out, k, axis=axis)
            acc |= np.roll(out, -k, axis=axis)
        out = acc
    return out


def _erode(b, r):
    return ~_dilate(~b, r)


# ── the pinned metrics ────────────────────────────────────────────────────

@register("land_err_f")
def land_err_f(*, events=None, targets=None, fps=None, event_times=None,
               target_times=None, search_f=12, bar=None):
    """Rendered event frame vs its target frame (§4.5: <=1, <=2 syllables).

    events / targets: frame indices (or *_times in seconds with ``fps``).
    Each target is matched to the nearest unused event within ``search_f``;
    value is the WORST absolute error in frames (inf when a target has no
    event in reach). Detail lists every pair, the unmatched targets and the
    stray events.
    """
    if event_times is not None:
        events = [int(round(float(t) * float(fps))) for t in event_times]
    if target_times is not None:
        targets = [int(round(float(t) * float(fps))) for t in target_times]
    ev = sorted(int(e) for e in (events or []))
    tg = sorted(int(t) for t in (targets or []))
    used, pairs, missing = set(), [], []
    for t in tg:
        best = None
        for i, e in enumerate(ev):
            if i in used or abs(e - t) > search_f:
                continue
            if best is None or abs(e - t) < abs(ev[best] - t):
                best = i
        if best is None:
            missing.append(t)
        else:
            used.add(best)
            pairs.append({"target": t, "event": ev[best],
                          "err_f": ev[best] - t})
    if not tg:
        value = None
    elif missing:
        value = float("inf")
    else:
        value = float(max(abs(p["err_f"]) for p in pairs))
    detail = {"pairs": pairs, "missing": missing,
              "stray": [e for i, e in enumerate(ev) if i not in used]}
    passed = None if value is None else Ellipsis
    return _result("land_err_f", value, bar, detail, passed)


@register("matte_iou")
def matte_iou(*, pred, truth, thr=0.5, kind="person", void=None, bar=None):
    """Rendered/measured mask vs labelled keyframes (person >=0.92, object
    >=0.85). pred / truth: {frame: mask} (only shared frames are scored) or
    equal-length lists. ``void``: the label's don't-care pixels in the same
    form (``labels.read_void``: motion-blurred hands, a soft hair band),
    excluded from both intersection and union. Value: mean IoU; detail has
    min and the per-frame IoUs. A frame where both masks are empty scores 1."""
    if isinstance(pred, dict) and isinstance(truth, dict):
        keys = sorted(set(pred) & set(truth))
        pairs = [(k, pred[k], truth[k]) for k in keys]
        vget = (void or {}).get if isinstance(void, dict) else \
            (lambda k: None)
    else:
        pairs = list(zip(range(len(truth)), pred, truth))
        vl = list(void) if void is not None else []
        vget = (lambda k: vl[k] if k < len(vl) else None)
    if bar is None and kind == "object":
        bar = (">=", 0.85)

    def iou(a, b):
        union = np.logical_or(a, b).sum()
        return 1.0 if union == 0 else \
            float(np.logical_and(a, b).sum()) / float(union)
    per, strict = {}, {}
    for k, p, t in pairs:
        pb, tb = _bin(p, thr), _bin(t, thr)
        if pb.shape != tb.shape:
            raise ValueError(f"frame {k}: mask shapes differ {pb.shape} vs "
                             f"{tb.shape}")
        v = vget(k)
        if v is not None:
            care = ~_bin(v)
            if care.shape != tb.shape:
                raise ValueError(f"frame {k}: void shape {care.shape} vs "
                                 f"{tb.shape}")
            strict[k] = iou(pb, tb)
            pb, tb = pb & care, tb & care
        per[k] = iou(pb, tb)
    if not per:
        return _result("matte_iou", None, bar, {"frames": 0}, None)
    vals = list(per.values())
    worst = sorted(per.items(), key=lambda kv: kv[1])[:5]
    detail = {"frames": len(vals), "min": float(min(vals)),
              "worst": [{"frame": k, "iou": round(v, 4)} for k, v in worst],
              "per_frame": {str(k): round(v, 4) for k, v in per.items()}}
    if strict:
        # every pixel counted: the void band of the shipped labels is RVM's
        # own soft edge, so this is the number that does not favour RVM
        detail["void_frames"] = len(strict)
        detail["mean_without_void_exclusion"] = round(float(np.mean(
            [strict.get(k, per[k]) for k in per])), 4)
    return _result("matte_iou", float(np.mean(vals)), bar, detail)


@register("matte_flicker")
def matte_flicker(*, masks, band_px=6, bar=None):
    """Mean |Δα| in a ``band_px`` edge band on a STATIC subject (<=0.035).

    masks: consecutive soft masks (0..1). The band is the edge of each
    frame's binarised mask widened to band_px (band_px/2 each side), unioned
    with the previous frame's, so edge creep shows as well as shimmer.
    """
    ms = [_mask(m) for m in masks]
    if len(ms) < 2:
        return _result("matte_flicker", None, bar, {"pairs": 0}, None)
    r = max(1, int(band_px) // 2)
    bands = []
    for m in ms:
        b = m >= 0.5
        bands.append(_dilate(b, r) & ~_erode(b, r))
    vals = []
    for i in range(1, len(ms)):
        band = bands[i] | bands[i - 1]
        if band.sum() == 0:
            vals.append(0.0)
            continue
        vals.append(float(np.abs(ms[i] - ms[i - 1])[band].mean()))
    return _result("matte_flicker", float(np.mean(vals)), bar,
                   {"pairs": len(vals), "max": float(max(vals)),
                    "band_px": int(band_px)})


@register("edge_chatter")
def edge_chatter(*, masks, fps, toggles=6, thr=0.5, bar=None):
    """Share of the frame whose binarised mask toggles >= ``toggles`` times
    inside any 1-second window (<=0.2% of the frame). Value: the worst
    window's share."""
    bs = np.stack([_bin(m, thr) for m in masks]).astype(np.int8)
    if bs.shape[0] < 2:
        return _result("edge_chatter", None, bar, {"frames": int(bs.shape[0])},
                       None)
    flips = (bs[1:] != bs[:-1]).astype(np.int32)        # (T-1, H, W)
    win = max(1, int(round(float(fps))))
    cs = np.concatenate([np.zeros((1,) + flips.shape[1:], np.int32),
                         np.cumsum(flips, axis=0)])
    worst, worst_t = 0.0, 0
    n = flips.shape[0]
    for t in range(0, max(1, n - win + 1)):
        cnt = cs[min(n, t + win)] - cs[t]
        share = float((cnt >= toggles).mean())
        if share > worst:
            worst, worst_t = share, t
    return _result("edge_chatter", worst, bar,
                   {"window_start_f": worst_t, "toggles": toggles,
                    "frames": int(bs.shape[0])})


@register("glyph_visible")
def glyph_visible(*, glyphs, subject, ghosted=False, letter_bar=0.40,
                  bar=None):
    """Visible share per glyph for behind-subject type: word >=0.65 and every
    letter >=0.40, or ghosted (a ghost outline shows the hidden part).

    glyphs: {label: alpha mask} or a list of masks (one per letter), all at
    the subject mask's size. subject: soft mask (1 = subject in front).
    Value: the word's visible share (alpha-weighted).
    """
    s = _mask(subject)
    items = glyphs.items() if isinstance(glyphs, dict) else \
        enumerate(glyphs)
    per, tot_a, tot_v = {}, 0.0, 0.0
    for k, g in items:
        a = _mask(g)
        area = float(a.sum())
        if area <= 0:
            continue
        vis = float((a * (1.0 - s)).sum())
        per[str(k)] = vis / area
        tot_a += area
        tot_v += vis
    if tot_a <= 0:
        return _result("glyph_visible", None, bar, {"glyphs": 0}, None)
    word = tot_v / tot_a
    low = {k: round(v, 4) for k, v in per.items() if v < letter_bar}
    b = _norm_bar(bar, "glyph_visible")
    passed = check(word, b) and (not low or bool(ghosted))
    return _result("glyph_visible", word, bar,
                   {"per_glyph": {k: round(v, 4) for k, v in per.items()},
                    "below_letter_bar": low, "letter_bar": letter_bar,
                    "ghosted": bool(ghosted)}, passed)


def letter_height(mask, min_px=30):
    """Median height (px) of the connected components of a glyph mask: the
    type's letter size, whatever the size of the block it sits in."""
    import cv2
    m = np.asarray(mask).astype(np.uint8)
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, connectivity=8)
    hs = [int(st[k, cv2.CC_STAT_HEIGHT]) for k in range(1, n)
          if st[k, cv2.CC_STAT_AREA] >= min_px]
    if not hs:
        ys = np.nonzero(m)[0]
        return int(ys.max() - ys.min() + 1) if len(ys) else 0
    return int(np.median(hs))


def ink_groups(frame, glyph_mask, *, max_groups=3, min_share=0.1,
               min_sep=60.0):
    """Split glyph pixels into colour groups (a red stat and its white
    label are two inks, each judged on its own): 2-means on RGB, kept when
    both halves hold >= ``min_share`` and their centres differ by
    >= ``min_sep``, applied recursively up to ``max_groups``."""
    f = np.asarray(frame, np.float64)
    g = _bin(glyph_mask)
    groups = [g]
    while len(groups) < max_groups:
        best = None
        for i, m in enumerate(groups):
            px = f[m]
            if len(px) < 100:
                continue
            c = np.stack([np.percentile(px, 10, axis=0),
                          np.percentile(px, 90, axis=0)])
            for _ in range(8):
                lab = (((px[:, None, :] - c[None]) ** 2).sum(axis=2)
                       ).argmin(axis=1)
                if lab.min() == lab.max():
                    break
                c = np.stack([px[lab == k].mean(axis=0) for k in (0, 1)])
            share = min((lab == 0).mean(), (lab == 1).mean())
            sep = float(np.sqrt(((c[0] - c[1]) ** 2).sum()))
            if share * m.sum() / g.sum() >= min_share and sep >= min_sep:
                if best is None or sep > best[0]:
                    best = (sep, i, lab)
        if best is None:
            break
        _sep, i, lab = best
        m = groups.pop(i)
        ys, xs = np.nonzero(m)
        for k in (0, 1):
            sub = np.zeros_like(m)
            sub[ys[lab == k], xs[lab == k]] = True
            groups.append(sub)
    return groups


@register("legibility")
def legibility(*, ink=None, plate=None, frame=None, glyph_mask=None,
               role="payload", plate_pct=None, band_px=None, exclude=None,
               groups=None, bar=None):
    """APCA Lc of the type against its plate (payload >=60, connectors >=45).

    Either ``ink`` ('#rrggbb' or rgb) with ``plate`` (an (h, w, 3) uint8
    region the type sits on), or a rendered ``frame`` plus the type's
    ``glyph_mask``. With a frame, the glyphs are split into colour groups
    (``ink_groups``, or ``groups`` given): each group's ink is its median
    colour and its plate the ring of pixels around it, from 6% to 25% of
    the letter height (``letter_height``; ``band_px`` overrides the outer
    radius) outside the glyphs, never on any glyph pixel or on ``exclude``
    (the type's own anti-aliased edges). The plate is judged at its worst
    for that ink: the 90th luminance percentile under light ink, the 10th
    under dark. Value: the lowest group's Lc.
    """
    if bar is None:
        bar = (">=", 45.0) if role in ("connector", "connectors") else \
            (">=", 60.0)
    if frame is not None and glyph_mask is not None:
        f = np.asarray(frame)
        g = _bin(glyph_mask)
        if not g.any():
            return _result("legibility", None, bar, {"why": "no glyphs"},
                           None)
        lh = max(4, letter_height(g))
        r_out = int(band_px or max(3, round(0.25 * lh)))
        r_in = max(1, min(r_out - 1, int(round(0.06 * lh))))
        blocked = _dilate(g | (_bin(exclude) if exclude is not None
                               else np.zeros_like(g)), r_in)
        per = []
        for m in (groups or ink_groups(f, g)):
            m = _bin(m)
            if not m.any():
                continue
            ring = _dilate(m, r_out) & ~blocked
            plate_px = f[ring]
            ink_rgb = np.median(f[m], axis=0)
            r = _legib_one(float(luminance(ink_rgb)),
                           luminance(plate_px.astype(np.float64))
                           if len(plate_px) else np.array([0.0]),
                           plate_pct)
            r.update(ink_rgb=[int(v) for v in ink_rgb],
                     share=round(float(m.sum()) / float(g.sum()), 3),
                     plate_px=int(len(plate_px)))
            per.append(r)
        worst = min(per, key=lambda r: r["lc"])
        detail = {k: v for k, v in worst.items() if k != "lc"}
        detail.update(role=role, letter_h=lh, ring_px=[r_in, r_out],
                      groups=per if len(per) > 1 else None)
        return _result("legibility", worst["lc"], bar, detail)
    if ink is None or plate is None:
        raise ValueError("legibility needs ink+plate or frame+glyph_mask")
    pl = np.asarray(plate)
    plate_y = luminance(pl.reshape(-1, 3).astype(np.float64)) \
        if pl.ndim >= 2 and pl.shape[-1] == 3 else \
        np.asarray(pl, np.float64).reshape(-1)
    r = _legib_one(float(luminance(ink)), plate_y, plate_pct)
    detail = {k: v for k, v in r.items() if k != "lc"}
    detail["role"] = role
    return _result("legibility", r["lc"], bar, detail)


def _legib_one(ink_y, plate_y, plate_pct=None):
    med = float(np.median(plate_y))
    light_ink = ink_y >= med
    pct = plate_pct if plate_pct is not None else (90 if light_ink else 10)
    worst_bg = float(np.percentile(plate_y, pct))
    return {"lc": abs(apca(ink_y, worst_bg)), "ink_y": round(ink_y, 4),
            "plate_y_median": round(med, 4),
            "plate_y_worst": round(worst_bg, 4), "pct": pct,
            "lc_median_plate": round(abs(apca(ink_y, med)), 2)}


def ink_mask(render, control, thr=40):
    """The type's INK in ``render`` given the same frame without the
    graphic (``control``): pixels the graphic changed by more than ``thr``,
    split by Otsu on the change magnitude when the change is bimodal, so a
    darkening scrim or soft shadow (moderate change) is the plate the ink
    sits on, not ink. Returns (ink, changed) bool masks."""
    d = np.abs(np.asarray(render, np.int16) - np.asarray(control, np.int16))
    if d.ndim == 3:
        d = d.max(axis=2)
    changed = d > thr
    vals = d[changed]
    if vals.size < 50:
        return changed, changed
    hist = np.bincount(vals, minlength=256).astype(np.float64)
    tot = hist.sum()
    w0 = np.cumsum(hist)
    m0 = np.cumsum(hist * np.arange(256))
    mt = m0[-1]
    w1 = tot - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w0 / tot - m0) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = 0
    k = int(np.argmax(between))
    hi, lo = vals[vals > k], vals[vals <= k]
    # bimodal: two well separated populations, both substantial
    if hi.size >= 0.08 * vals.size and lo.size >= 0.08 * vals.size and \
            hi.mean() - lo.mean() >= 50:
        return changed & (d > k), changed
    return changed, changed


@register("reproj_rms_px")
def reproj_rms_px(*, pred, truth, frame_h=None, at_h=1080, bar=None):
    """RMS distance of pinned-plane corners at 1080p (<=1.5 px).
    pred / truth: matching (N, 2) point lists in pixels of a frame
    ``frame_h`` tall (scaled to ``at_h``)."""
    p = np.asarray(pred, np.float64).reshape(-1, 2)
    t = np.asarray(truth, np.float64).reshape(-1, 2)
    if p.shape != t.shape or not len(p):
        raise ValueError("pred and truth need the same non-empty shape")
    k = float(at_h) / float(frame_h) if frame_h else 1.0
    d = np.sqrt(((p - t) ** 2).sum(axis=1)) * k
    return _result("reproj_rms_px", float(np.sqrt((d ** 2).mean())), bar,
                   {"points": int(len(d)), "max_px": float(d.max())})


@register("region_locality")
def region_locality(*, with_fx, without_fx, region, tol=0, bar=None):
    """|Δ| outside the effect region (must be 0).

    with_fx / without_fx: equal-length lists of frames (uint8, same size);
    region: one mask for every frame or a list (1 = inside the effect).
    Compare LOSSLESS renders (or the graph output before encoding): a lossy
    encode re-decides macroblocks next to any change, so outside pixels move
    by codec noise that is not the device's doing. Value: the largest
    absolute channel difference outside the region.
    """
    regs = region if isinstance(region, (list, tuple)) else \
        [region] * len(with_fx)
    worst, changed, total = 0, 0, 0
    worst_f = None
    for i, (a, b) in enumerate(zip(with_fx, without_fx)):
        a = np.asarray(a, np.int16)
        b = np.asarray(b, np.int16)
        out = ~_bin(regs[i])
        d = np.abs(a - b)
        if d.ndim == 3:
            d = d.max(axis=2)
        d = d[out]
        if d.size:
            m = int(d.max())
            if m > worst:
                worst, worst_f = m, i
            changed += int((d > tol).sum())
            total += int(d.size)
    return _result("region_locality", float(worst), bar,
                   {"worst_frame": worst_f,
                    "changed_px_share": (changed / total) if total else 0.0,
                    "tol": tol})


@register("duration_exact")
def duration_exact(*, with_fx, without_fx, check_audio=True, bar=None):
    """Program length and audio across junctions: ±0 frames and audio
    bit-identical (decoded PCM). with_fx / without_fx: render paths.
    Value: |Δ frames|; pass needs 0 and identical audio."""
    na = len(frame_md5s(with_fx))
    nb = len(frame_md5s(without_fx))
    detail = {"frames_with": na, "frames_without": nb}
    same_audio = True
    if check_audio:
        ha, hb = audio_md5(with_fx), audio_md5(without_fx)
        same_audio = ha == hb
        detail.update(audio_md5_with=ha, audio_md5_without=hb,
                      audio_identical=same_audio)
    value = float(abs(na - nb))
    b = _norm_bar(bar, "duration_exact")
    return _result("duration_exact", value, bar, detail,
                   bool(check(value, b) and same_audio))


def _cell_luma(frames, grid=(32, 18)):
    """Per-frame (gh, gw) relative-luminance and red-saturation cells."""
    gw, gh = grid
    lum, red = [], []
    for f in frames:
        a = np.asarray(f, np.float64)
        if a.ndim == 2:
            a = np.repeat(a[..., None], 3, axis=2)
        h, w = a.shape[:2]
        bh, bw = max(1, h // gh), max(1, w // gw)
        a = a[:bh * gh, :bw * gw]
        y = luminance(a)
        rr = a[..., 0] / (a.sum(axis=2) + 1e-6)
        lum.append(y.reshape(gh, bh, gw, bw).mean(axis=(1, 3)))
        red.append(rr.reshape(gh, bh, gw, bw).mean(axis=(1, 3)))
    return np.stack(lum), np.stack(red)


def flash_transitions(frames, *, area=0.25, delta=0.1, dark_max=0.8,
                      grid=(32, 18)):
    """Harding-style general (and red) flash transitions on rendered frames.

    A transition is a monotonic luminance run (consecutive frames moving one
    way) over which >= ``area`` of the screen changes by >= ``delta``
    relative luminance with the darker state below ``dark_max`` (WCAG 2.3.1
    / Harding). Red transitions additionally need saturated red
    (R/(R+G+B) >= 0.8) in that area. Returns [(end_frame, +1|-1, red)].
    """
    L, R = _cell_luma(frames, grid)
    if len(L) < 2:
        return []
    mean = L.reshape(len(L), -1).mean(axis=1)
    d = np.sign(np.diff(mean))
    out = []
    i = 0
    n = len(d)
    while i < n:
        if d[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < n and d[j + 1] == d[i]:
            j += 1
        a, b = L[i], L[j + 1]
        ch = (b - a) * d[i]
        darker = np.minimum(a, b)
        hit = (ch >= delta) & (darker < dark_max)
        if hit.mean() >= area:
            redcell = (np.maximum(R[i], R[j + 1]) >= 0.8) & hit
            out.append((j + 1, int(d[i]), bool(redcell.mean() >= area)))
        i = j + 1
    return out


@register("flash_risk")
def flash_risk(*, frames=None, video=None, fps=None, opted_in=False,
               size=(160, 90), bar=None, **kw):
    """Harding-style flash count on rendered frames: the most flashes in any
    1-second window (a flash = a pair of opposing transitions). Bar: <=3.
    ``opted_in=True`` (the user asked for strobing) never fails; it reports
    ``contains_flashing`` instead. Pass ``frames`` (+ fps) or ``video``."""
    if video is not None:
        info = probe(video)
        fps = fps or info["fps"]
        _, frames = read_frames(video, size=size)
    if not frames:
        return _result("flash_risk", None, bar, {"frames": 0}, None)
    tr = flash_transitions(frames, **kw)
    win = max(1, int(round(float(fps or 30))))
    worst, worst_red, worst_at = 0, 0, None
    for start in range(0, len(frames)):
        inwin = [t for t in tr if start <= t[0] < start + win]
        flashes = len(inwin) // 2
        reds = len([t for t in inwin if t[2]]) // 2
        if flashes > worst:
            worst, worst_at = flashes, start
        worst_red = max(worst_red, reds)
    b = _norm_bar(bar, "flash_risk")
    over = not check(worst, b) or worst_red > 0 and not check(worst_red, b)
    detail = {"transitions": len(tr), "worst_window_start_f": worst_at,
              "red_flashes_max": worst_red, "contains_flashing": bool(over),
              "opted_in": bool(opted_in)}
    passed = None if opted_in else (not over)
    return _result("flash_risk", float(worst), bar, detail, passed)


@register("determinism")
def determinism(*, paths=None, md5s=None, bar=None):
    """Frame md5 across 2-3 renders of seeded devices must be identical.
    Value: frames that differ from the first render (plus any length gap)."""
    lists = md5s if md5s is not None else [frame_md5s(p) for p in paths]
    if len(lists) < 2:
        raise ValueError("determinism needs at least two renders")
    ref = lists[0]
    diffs = []
    for k, other in enumerate(lists[1:], start=1):
        n = max(len(ref), len(other))
        bad = sum(1 for i in range(n)
                  if i >= len(ref) or i >= len(other) or ref[i] != other[i])
        diffs.append(bad)
    return _result("determinism", float(max(diffs)), bar,
                   {"renders": len(lists), "frames": len(ref),
                    "differing": diffs})


@register("cost_ratio")
def cost_ratio(*, with_s, without_s, lane=None, bar=None):
    """Wall time with the device / without it, per lane. No default bar:
    each track states its own (e.g. 1C '<=1.3')."""
    if not without_s or without_s <= 0:
        raise ValueError("without_s must be > 0")
    return _result("cost_ratio", float(with_s) / float(without_s), bar,
                   {"with_s": float(with_s), "without_s": float(without_s),
                    "lane": lane})
