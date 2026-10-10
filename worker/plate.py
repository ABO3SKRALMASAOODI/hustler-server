"""The plate under a graphic: what the program picture looks like there.

Light type on a white shirt is the most common legibility failure in a talking
head short, and a template cannot see the footage it is drawn over. Before the
motion clips render, the renderer measures the PROGRAM picture at a few
moments inside every motion item (and inside every motion-caption cue) and
hands each composition a coarse luma grid of those moments (``MG.plate``).
Templates read the luma under their OWN laid-out boxes (``MG.plateAt`` /
``MG.need`` in worker/motion/runtime.js) and raise a scrim or a local backing
only where the plate is too bright for their ink; on a dark plate they render
exactly as before.

What is measured: the main footage at the mapped source second (or a spliced
insert at its clip second), fitted onto the canvas the way the render fits it
(renderer.fit_fractions — crop/pad/pad_blur, frame focus and focus_track, a
picture frame, an insert's own crop/fit/rotation) and seen through the shared
camera's viewport at that program second (renderer.zoom_state_at). Under a
source-fed picture card (a card or a speaker + screen stack) the plate is the
COMPOSED picture: the card's backdrop with each panel's source rect in its
box (picture_cards.card_panels at that source second — a followed or
cut-stepped card where it is then), since a graphic in the band above a card
sits on its dark backdrop, not on the footage a full-frame crop would show
there (the Elon stack's stat lines turned dark over a dark band because the
bright neon sign of the uncropped frame was measured under them). Grades,
overlays, takeovers and other graphics are not drawn: the grid is the plate as
shot, which is what the type has to survive.

Cost: moments are grouped per media file into clusters of nearby seconds and
each cluster is ONE bounded, low-resolution grayscale decode (-ss/-t, a few
frames per second), clusters running concurrently. A cluster never spans more
than MAX_SPAN_S: a final decodes a 4K original, and one decode across a whole
captioned minute would run on two threads past its timeout and lose every
moment. A long program keeps an evenly spread MAX_SAMPLES of its moments (a
caption cue without its own then reads its neighbour's), so coverage and cost
stay bounded. Every measured moment is cached on disk by source content +
moment + geometry, so a re-render after an unrelated edit decodes nothing.

Fail open: any moment that cannot be measured is None, an item with no
measured moment gets no plate, and a composition without a plate renders
byte-for-byte as it did before plates existed.
"""

import base64
import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent import futures
from contextvars import ContextVar

import motion_engine

# Bump when the grid's meaning changes (cells, geometry, statistic): it is
# part of every cached moment's key.
PLATE_VERSION = 1
COLS = 18                 # grid columns over the 1080-wide design space
DECODE_W = 320            # decoded frame width (px) before fitting
SAMPLE_FPS = 8            # frames per second kept from a cluster decode
CLUSTER_GAP_S = 3.0       # moments closer than this share one decode
MAX_SPAN_S = 6.0          # ...but one decode never covers more than this
MAX_SAMPLES = 160         # per render, after de-duplication (evenly spread)
DECODE_TIMEOUT_S = 40.0
BUDGET_S = 60.0           # the whole probe; anything later is unmeasured
WORKERS = max(2, min(4, (os.cpu_count() or 4) // 2))   # x 2 decoder threads
CACHE_DIR = os.path.join(motion_engine.CACHE_DIR, "plates")
CACHE_MAX_FILES = 20000   # ~2 KB each; oldest go first

# The main source's identity for the moment cache, set by the render job
# (the original's sha256 + any cleaned-source key). A preview measures the
# proxy and a final the original — the same picture at another resolution —
# so with it set, an export reuses the preview's measurements instead of
# decoding a 4K original again. Unset, a file is identified by its bytes.
SOURCE_ID = ContextVar("plate_source_id", default=None)


def source_scope(source_id):
    """Identify the main source for this context; returns the reset token."""
    return SOURCE_ID.set(source_id or None)


def end_source_scope(token):
    SOURCE_ID.reset(token)


def grid_rows(W, H, cols=COLS):
    return max(1, int(round(cols * float(H) / float(W))))


def encode_grid(grid):
    """A grid as the composition receives it: base64 of its row-major bytes
    (~4x smaller than a JSON list — a caption segment carries one per cue,
    and a document has a size cap)."""
    return base64.b64encode(bytes(max(0, min(255, int(v))) for v in grid)).decode("ascii")


def _ffmpeg():
    return motion_engine._ffmpeg()


def fingerprint(path, _memo={}, _lock=threading.Lock()):
    """Content identity of a media file: size + head/tail digest. A final's
    original is re-downloaded per job (new mtime, same bytes), so mtime is
    not part of it."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime_ns)
    with _lock:
        if key in _memo:
            return _memo[key]
    h = hashlib.sha1(str(st.st_size).encode())
    try:
        with open(path, "rb") as f:
            h.update(f.read(1 << 18))
            if st.st_size > (1 << 19):
                f.seek(-(1 << 18), os.SEEK_END)
                h.update(f.read(1 << 18))
    except OSError:
        return None
    fp = h.hexdigest()[:24]
    with _lock:
        _memo[key] = fp
    return fp


def _cache_key(fp, local_t, geom):
    raw = json.dumps([PLATE_VERSION, fp, round(float(local_t), 3), geom],
                     sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:32]


def _cache_get(key):
    try:
        with open(os.path.join(CACHE_DIR, key + ".json")) as f:
            g = json.load(f)
        return g if isinstance(g, list) else None
    except Exception:  # noqa: BLE001 — a miss
        return None


def _cache_put(key, grid):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        p = os.path.join(CACHE_DIR, key + ".json")
        tmp = p + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            json.dump(grid, f)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 — caching is best effort
        pass


def _cache_prune(max_files=CACHE_MAX_FILES):
    """Keep the moment cache bounded (it lives beside the motion clip cache,
    which prunes only its own clips): the oldest moments go first."""
    try:
        with os.scandir(CACHE_DIR) as it:
            files = [(e.stat().st_mtime, e.path) for e in it
                     if e.name.endswith(".json")]
        if len(files) <= max_files:
            return
        files.sort()
        for _m, p in files[:len(files) - max_files]:
            try:
                os.remove(p)
            except OSError:
                pass
    except Exception:  # noqa: BLE001 — best effort
        pass


def _clusters(times, gap=CLUSTER_GAP_S, span=MAX_SPAN_S):
    out = []
    for t in sorted(set(times)):
        if out and t - out[-1][-1] <= gap and t - out[-1][0] <= span:
            out[-1].append(t)
        else:
            out.append([t])
    return out


def _spread(n, k):
    """k indices out of range(n), evenly spread, first and last included."""
    if n <= k:
        return list(range(n))
    if k <= 1:
        return [0]
    return sorted({int(round(i * (n - 1) / (k - 1))) for i in range(k)})


def _pgm_frames(data):
    """[(w, h, bytes)] from a concatenated binary-PGM (P5, 8-bit) stream."""
    out, i, n = [], 0, len(data)
    while i < n:
        fields, j = [], i
        while len(fields) < 4 and j < n:
            while j < n and data[j:j + 1].isspace():
                j += 1
            if data[j:j + 1] == b"#":
                while j < n and data[j:j + 1] != b"\n":
                    j += 1
                continue
            k = j
            while k < n and not data[k:k + 1].isspace():
                k += 1
            fields.append(data[j:k])
            j = k
        if len(fields) < 4 or fields[0] != b"P5":
            break
        try:
            w, h, mx = int(fields[1]), int(fields[2]), int(fields[3])
        except ValueError:
            break
        start = j + 1               # exactly one whitespace byte after maxval
        end = start + w * h * (2 if mx > 255 else 1)
        if mx > 255 or end > n:
            break
        out.append((w, h, data[start:end]))
        i = end
    return out


def decode_gray(path, times, width=DECODE_W, deadline=None):
    """{t: PIL 'L' image} for media seconds ``times`` of one video file:
    one bounded low-res grayscale decode per cluster of nearby moments,
    SAMPLE_FPS frames per second, nearest frame per moment."""
    from PIL import Image
    out = {}
    lock = threading.Lock()

    def one(cl):
        if deadline is not None and time.monotonic() >= deadline:
            return              # over budget: the rest stay unmeasured
        a = max(0.0, cl[0] - 0.5 / SAMPLE_FPS)
        span = cl[-1] - a + 1.0 / SAMPLE_FPS
        # PGM frames carry their own size, so nothing has to predict what the
        # scaler (rotation, odd sizes, anamorphic pixels) produced.
        cmd = [_ffmpeg(), "-v", "error", "-nostdin", "-threads", "2",
               "-ss", f"{a:.3f}", "-t", f"{span:.3f}", "-i", path,
               "-an", "-sn", "-dn",
               "-vf", f"fps={SAMPLE_FPS},scale={int(width)}:-2:flags=area,"
                      "format=gray",
               "-f", "image2pipe", "-c:v", "pgm", "-"]
        left = DECODE_TIMEOUT_S if deadline is None else \
            max(1.0, min(DECODE_TIMEOUT_S, deadline - time.monotonic()))
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=left)
        except Exception:  # noqa: BLE001 — unmeasured, never fatal
            return
        if r.returncode != 0 or not r.stdout:
            return
        frames = _pgm_frames(r.stdout)
        if not frames:
            return
        for t in cl:
            k = min(len(frames) - 1, max(0, int(round((t - a) * SAMPLE_FPS))))
            w, h, buf = frames[k]
            with lock:
                out[t] = Image.frombytes("L", (w, h), buf)

    cls = _clusters(times)
    with futures.ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(cls)))) as pool:
        list(pool.map(one, cls))
    return out


def canvas_grid(img, src_size, W, H, *, mode=None, focus=None, crop=None,
                picture=None, rotation=0, zoom=(1.0, 0.5, 0.5), cols=COLS,
                card=None):
    """The program picture's luma grid (row-major ints 0-255, cols x rows)
    for one decoded source frame placed on the W x H canvas the way the
    render places it. ``card`` (a source-fed picture card's spec with its
    ``panels_at``: [(box, source rect)] at this moment) composes the card
    instead: its backdrop, each panel's rect of the frame in its box."""
    from PIL import Image, ImageFilter
    import renderer   # lazy: renderer imports the motion layer
    rows = grid_rows(W, H, cols)
    rotation = int(rotation or 0) % 360
    if rotation == 90:
        img = img.transpose(Image.ROTATE_270)
    elif rotation == 180:
        img = img.transpose(Image.ROTATE_180)
    elif rotation == 270:
        img = img.transpose(Image.ROTATE_90)
    sw, sh = src_size if src_size else img.size
    if rotation in (90, 270):
        sw, sh = sh, sw
    if crop and len(crop) == 4:
        w, h = img.size
        img = img.crop((round(float(crop[0]) * w), round(float(crop[1]) * h),
                        max(round(float(crop[0]) * w) + 1, round(float(crop[2]) * w)),
                        max(round(float(crop[1]) * h) + 1, round(float(crop[3]) * h))))
        sw = max(1.0, sw * (float(crop[2]) - float(crop[0])))
        sh = max(1.0, sh * (float(crop[3]) - float(crop[1])))
        mode = "pad"
    CW, CH = cols * 6, rows * 6
    if card:
        canvas = _card_canvas(img, card, CW, CH)
        return list(canvas.resize((cols, rows), Image.BOX).tobytes())
    px, py, pw, ph = renderer.picture_pixels(CW, CH, picture)
    kind, x0, y0, x1, y1 = renderer.fit_fractions(sw, sh, pw, ph, mode, focus)
    w, h = img.size
    if kind == "crop":
        part = img.crop((int(x0 * w), int(y0 * h),
                         max(int(x0 * w) + 1, int(round(x1 * w))),
                         max(int(y0 * h) + 1, int(round(y1 * h)))))
        content = part.resize((pw, ph), Image.BOX)
    else:
        if (mode or "") == "pad_blur":
            _k, bx0, by0, bx1, by1 = renderer.fit_fractions(sw, sh, pw, ph, "crop")
            content = img.crop((int(bx0 * w), int(by0 * h),
                                max(int(bx0 * w) + 1, int(round(bx1 * w))),
                                max(int(by0 * h) + 1, int(round(by1 * h))))) \
                .resize((pw, ph), Image.BOX).filter(ImageFilter.GaussianBlur(3))
        else:
            content = Image.new("L", (pw, ph), 0)
        fw = max(1, int(round((x1 - x0) * pw)))
        fh = max(1, int(round((y1 - y0) * ph)))
        content.paste(img.resize((fw, fh), Image.BOX),
                      (int(round(x0 * pw)), int(round(y0 * ph))))
    canvas = content
    if (px, py, pw, ph) != (0, 0, CW, CH):
        canvas = Image.new("L", (CW, CH), 0)
        canvas.paste(content, (px, py))
    z, cx, cy = zoom
    if z > 1.005:
        vx0, vy0 = (1.0 - 1.0 / z) * cx, (1.0 - 1.0 / z) * cy
        canvas = canvas.crop((int(vx0 * CW), int(vy0 * CH),
                              max(int(vx0 * CW) + 1, int(round((vx0 + 1.0 / z) * CW))),
                              max(int(vy0 * CH) + 1, int(round((vy0 + 1.0 / z) * CH))))) \
            .resize((CW, CH), Image.BOX)
    return list(canvas.resize((cols, rows), Image.BOX).tobytes())


def _card_canvas(img, card, CW, CH):
    """A source-fed picture card composed on a CW x CH luma canvas: its
    backdrop (picture_cards._fill — the gradient or flat colour and the
    vignette — or, for a 'blur' backdrop, what picture_cards._blur_chain
    draws: the first panel's footage cover-scaled to the canvas, blurred and
    its luma dimmed toward 16 by background_dim) with each panel's source
    rect fitted into its box."""
    from PIL import Image, ImageFilter
    import picture_cards
    w, h = img.size

    def rect_px(rect):
        x0, y0 = int(float(rect[0]) * w), int(float(rect[1]) * h)
        return (x0, y0, max(x0 + 1, int(round(float(rect[2]) * w))),
                max(y0 + 1, int(round(float(rect[3]) * h))))
    style = card.get("background_style")
    if style == "blur":
        dim = card.get("background_dim")
        k = 1.0 - float(picture_cards.BLUR_DIM_DEFAULT if dim is None else dim)
        first = next((r for _b, r in card.get("panels_at") or [] if r), None)
        part = img.crop(rect_px(first)) if first else img
        pw, ph = part.size
        s = max(CW / float(pw), CH / float(ph))
        rw, rh = max(CW, int(round(pw * s))), max(CH, int(round(ph * s)))
        x0, y0 = (rw - CW) // 2, (rh - CH) // 2
        canvas = part.resize((rw, rh), Image.BOX).crop((x0, y0, x0 + CW, y0 + CH)) \
            .filter(ImageFilter.GaussianBlur(max(1.0, min(CW, CH) / 32.0))) \
            .point(lambda v: max(0, min(255, int(round(16 + (v - 16) * k)))))
    else:
        try:
            rgb = picture_cards._fill(CW, CH, card)
            lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
            canvas = Image.fromarray(lum.clip(0, 255).astype("uint8"), "L")
        except Exception:  # noqa: BLE001 — a dark backdrop, as cards default
            canvas = Image.new("L", (CW, CH), 16)
    for box, rect in card.get("panels_at") or []:
        if not box or not rect:
            continue
        bx0, by0 = int(round(float(box[0]) * CW)), int(round(float(box[1]) * CH))
        bw = max(1, int(round((float(box[2]) - float(box[0])) * CW)))
        bh = max(1, int(round((float(box[3]) - float(box[1])) * CH)))
        canvas.paste(img.crop(rect_px(rect)).resize((bw, bh), Image.BOX), (bx0, by0))
    return canvas


def _card_at(edl, t, src_t):
    """The source-fed picture card live at program second ``t`` as the plate
    sees it ({spec..., panels_at}), or None. Its fade in/out counts as live:
    a plate that reads the backdrop a few frames early is harmless."""
    import picture_cards
    for cd in ((edl.get("effects") or {}).get("picture_cards")) or []:
        try:
            if not (isinstance(cd, dict) and picture_cards.source_fed(cd)
                    and float(cd["start"]) - 1e-6 <= t < float(cd["end"]) - 1e-6):
                continue
            panels = picture_cards.card_panels(cd, src_t)
        except (KeyError, TypeError, ValueError):
            continue
        if panels and all(r for _b, r in panels):
            keep = {k: cd.get(k) for k in ("box", "background", "background_color2",
                                           "background_style", "background_dim",
                                           "vignette")}
            return dict(keep, panels_at=[[[round(float(v), 4) for v in b],
                                          [round(float(v), 4) for v in r]]
                                         for b, r in panels])
    return None


class Probe:
    """Measures the program picture for one render. Call it with program
    seconds; it returns a grid (or None) per second."""

    def __init__(self, edl, tl, src_path, src_size, W, H, out_duration,
                 frame_mode=None, frame_focus=None, insert_locals=None,
                 cols=COLS):
        self.edl = edl or {}
        self.tl = tl
        self.src = src_path
        self.src_size = src_size
        self.W, self.H = int(W), int(H)
        self.out_duration = float(out_duration)
        self.mode = frame_mode
        self.focus = frame_focus
        self.insert_locals = dict(insert_locals or {})
        self.cols = cols
        frame = self.edl.get("frame") if isinstance(self.edl.get("frame"), dict) else {}
        self.track = (frame or {}).get("focus_track") or []
        self.picture = (frame or {}).get("picture")
        self.zooms = ((self.edl.get("effects") or {}).get("zooms")) or []
        self._blocks = None
        self.stats = {"samples": 0, "cached": 0, "decoded": 0, "seconds": 0.0}

    @property
    def rows(self):
        return grid_rows(self.W, self.H, self.cols)

    def _focus_at(self, src_t):
        """(focus, mode) of the main footage at a source second — the
        renderer's per-block _frame_for, evaluated at one moment (a crop
        that follows the speaker, at that moment of its path)."""
        focus, mode = self._static_focus_at(src_t)
        if mode == "crop":
            import follow
            span = follow.span_at(follow.frame_spans(self.edl), src_t)
            if span:
                return follow.centre_at(span, src_t), mode
        return focus, mode

    def _static_focus_at(self, src_t):
        for sp in self.track:
            try:
                if float(sp.get("t0", 0)) <= src_t <= float(sp.get("t1", 0)):
                    fx, fy = sp.get("x"), sp.get("y")
                    m = sp.get("mode") or self.mode
                    if fx is None and fy is None:
                        return self.focus, m
                    f0 = self.focus or (None, None)
                    return ((fx if fx is not None else f0[0],
                             fy if fy is not None else f0[1]), m)
            except (TypeError, ValueError, AttributeError):
                continue
        return self.focus, self.mode

    def _insert_at(self, t):
        if self._blocks is None:
            import timeline as timeline_mod
            try:
                self._blocks = [b for b in timeline_mod.program_blocks(self.edl)
                                if b["kind"] == "insert"]
            except Exception:  # noqa: BLE001
                self._blocks = []
        for b in self._blocks:
            if b["out_start"] - 1e-6 <= t < b["out_end"] - 1e-6:
                return b
        return None

    def _plan(self, t):
        """(path, media_t, is_still, geometry dict) of program second t, or
        None when it cannot be measured."""
        import renderer
        zoom = renderer.zoom_state_at(self.zooms, t, self.out_duration,
                                      size=(self.W, self.H))
        zoom = tuple(round(float(v), 4) for v in zoom)
        src_t = self.tl.out_to_src(t) if self.tl is not None else None
        if src_t is not None and self.src:
            focus, mode = self._focus_at(src_t)
            geom = {"mode": mode, "focus": list(focus) if focus else None,
                    "picture": self.picture, "zoom": zoom}
            card = _card_at(self.edl, t, src_t)
            if card:
                geom = {"card": card}
            sid = SOURCE_ID.get()
            return (self.src, float(src_t), False, geom, self.src_size,
                    f"src:{sid}" if sid else None)
        b = self._insert_at(t)
        if not b:
            return None
        item = next((i for i in self.edl.get("inserts") or []
                     if i.get("id") == b.get("id")), None) or {}
        path = self.insert_locals.get(b.get("asset_key"))
        if not path or not os.path.exists(path):
            return None
        still = (b.get("media") == "image")
        local = 0.0 if still else float(b.get("clip_start_s") or 0.0) + \
            (t - float(b["out_start"])) * float(b.get("rate") or 1.0)
        geom = {"mode": item.get("fit") or self.mode, "focus": None,
                "crop": item.get("crop"), "rotation": item.get("rotation") or 0,
                "picture": self.picture, "zoom": zoom}
        return path, local, still, geom, None, None

    def __call__(self, times):
        t_start = time.monotonic()
        deadline = t_start + BUDGET_S
        times = list(times)
        out = [None] * len(times)
        # a long program: an even spread across all of it, never just its
        # first MAX_SAMPLES moments (the tail would lose its backings)
        keep = set(_spread(len(times), MAX_SAMPLES))
        plans = []
        for i, t in enumerate(times):
            p = None
            if i in keep:
                try:
                    p = self._plan(float(t))
                except Exception:  # noqa: BLE001 — that moment is unmeasured
                    p = None
            plans.append(p)
        # grids live in the design space: a preview (540x960) and a final
        # (1080x1920) of one edit share every measured moment
        geo_key = [self.cols, self.rows]
        todo = {}                 # path -> [(i, media_t, still, geom, size, key)]
        for i, p in enumerate(plans):
            if not p:
                continue
            path, mt, still, geom, size, ident = p
            fp = ident or fingerprint(path)
            key = _cache_key(fp, mt, [geo_key, geom]) if fp else None
            hit = _cache_get(key) if key else None
            if hit is not None and len(hit) == self.cols * self.rows:
                out[i] = hit
                self.stats["cached"] += 1
                continue
            todo.setdefault(path, []).append((i, mt, still, geom, size, key))
        for path, rows in todo.items():
            if time.monotonic() > deadline:
                break
            frames = {}
            try:
                if any(r[2] for r in rows):
                    from PIL import Image
                    with Image.open(path) as im:
                        still_img = im.convert("L")
                        still_img.thumbnail((DECODE_W * 2, DECODE_W * 2))
                        frames.update({r[1]: still_img for r in rows if r[2]})
                vids = [r[1] for r in rows if not r[2]]
                if vids:
                    frames.update(decode_gray(path, vids, deadline=deadline))
            except Exception:  # noqa: BLE001
                continue
            for i, mt, _still, geom, size, key in rows:
                img = frames.get(mt)
                if img is None:
                    continue
                try:
                    g = canvas_grid(img, size, self.W, self.H, mode=geom.get("mode"),
                                    focus=geom.get("focus"), crop=geom.get("crop"),
                                    picture=geom.get("picture"),
                                    rotation=geom.get("rotation") or 0,
                                    zoom=geom.get("zoom") or (1.0, 0.5, 0.5),
                                    cols=self.cols, card=geom.get("card"))
                except Exception:  # noqa: BLE001
                    continue
                out[i] = g
                self.stats["decoded"] += 1
                if key:
                    _cache_put(key, g)
        if self.stats["decoded"]:
            _cache_prune()
        self.stats["samples"] += len(keep)
        self.stats["seconds"] += time.monotonic() - t_start
        return out
