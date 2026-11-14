"""Frames on boards: one ffmpeg seek per frame, so this works on a path or a presigned URL."""
import io
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "InterDisplay-Bold.ttf")
BG, FG, DIM = (14, 14, 18), (240, 240, 244), (160, 160, 170)
_fonts = {}


def font(size):
    if size not in _fonts:
        try:
            _fonts[size] = ImageFont.truetype(FONT, size)
        except OSError:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def grab(path, t, width=None, height=None):
    """One frame as a PIL image, or None."""
    scale = f"scale={width}:-2" if width else f"scale=-2:{height}"
    # One frame has no frame-level parallelism. Auto decoder/encoder pools
    # multiplied by every board cell throttle the 1.5-vCPU Director slots.
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-threads", "1",
           "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
           "-vf", scale, "-filter_threads", "1", "-f", "image2pipe",
           "-vcodec", "mjpeg", "-threads:v", "1", "-q:v", "3", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def fit(im, box_w, box_h):
    """Centred on a dark cell, keeping the whole frame."""
    out = Image.new("RGB", (box_w, box_h), BG)
    w, h = im.size
    s = min(box_w / w, box_h / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    out.paste(im.resize((nw, nh), Image.LANCZOS), ((box_w - nw) // 2, (box_h - nh) // 2))
    return out


def save(img, path, quality=80):
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)


# ------------------------------------------------------------------ designed pictures
# A picture made of the words themselves, for a moment no real picture shows: the number said, the two
# sides compared, the steps, the dates. Only what a spec holds is drawn, and a spec holds only what was
# said (broll_source.design_specs). Deterministic: the same spec draws the same pixels on any worker, so
# stock.download can make it again on the egress lane from the spec alone.

DESIGN_KINDS = ("number", "versus", "list", "bars", "timeline", "term")
_FONT_DIR = os.path.dirname(FONT)
PAGE, INK, SOFT = "#101013", "#F3F0EA", "#A9A6A0"


def face(name, size):
    key = (name, size)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
        except OSError:
            _fonts[key] = font(size)
    return _fonts[key]


def _rgb(hx, fallback):
    try:
        s = str(hx).lstrip("#")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if len(s) == 6 else _rgb(fallback, "#101013")
    except ValueError:
        return _rgb(fallback, "#101013")


def _wrap(d, text, fnt, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and d.textlength(t, font=fnt) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _fit(d, text, name, width, start, floor, max_lines=1):
    """The largest size at which text sits in width on at most max_lines lines."""
    size = start
    while size > floor:
        f = face(name, size)
        lines = _wrap(d, text, f, width)
        if len(lines) <= max_lines and all(d.textlength(x, font=f) <= width for x in lines):
            return f, lines
        size -= 4
    f = face(name, floor)
    return f, _wrap(d, text, f, width)[:max_lines]


def _block(d, x, y, lines, fnt, fill, lead=1.08, anchor_w=None):
    asc, desc = fnt.getmetrics()
    step = int((asc + desc) * lead)
    for ln in lines:
        dx = x if anchor_w is None else x + (anchor_w - d.textlength(ln, font=fnt)) / 2
        d.text((dx, y), ln, font=fnt, fill=fill)
        y += step
    return y


def design_spec(raw):
    """A model's spec, made safe to draw: a known kind, short strings, at most five parts. None when empty."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in DESIGN_KINDS:
        return None
    parts = []
    for p in raw.get("parts") or []:
        if not isinstance(p, dict):
            continue
        label = " ".join(str(p.get("label") or "").split())[:60]
        value = " ".join(str(p.get("value") or "").split())[:24]
        n = p.get("n")
        n = float(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else None
        if label or value:
            parts.append({"label": label, "value": value, "n": n})
    need = {"number": 1, "term": 1, "versus": 2, "list": 2, "bars": 2, "timeline": 2}[kind]
    if len(parts) < need:
        return None
    if kind == "bars" and sum(1 for p in parts if p["n"] is not None and p["n"] >= 0) < 2:
        return None
    return {"kind": kind, "title": " ".join(str(raw.get("title") or "").split())[:48], "parts": parts[:5 if kind != "versus" else 2],
            "accent": str(raw.get("accent") or "#E8B23A")[:7], "page": str(raw.get("page") or PAGE)[:7]}


def designed(spec, W=1080, H=1920):
    """The designed picture for a spec (from design_spec) at W x H. Type sits in the middle band: the top
    eighth and the bottom quarter stay quiet for the platform's buttons and the film's captions."""
    spec = design_spec(spec) if spec and "page" not in spec else spec
    if not spec:
        raise ValueError("no drawable spec")
    page, acc = _rgb(spec["page"], PAGE), _rgb(spec["accent"], "#E8B23A")
    ink, soft = _rgb(INK, INK), _rgb(SOFT, SOFT)
    deep = tuple(int(c * 0.55) for c in page)
    fall = Image.linear_gradient("L").resize((W, H))          # a quiet fall of light, top to bottom
    im = Image.composite(Image.new("RGB", (W, H), deep), Image.new("RGB", (W, H), page), fall)
    d = ImageDraw.Draw(im)
    s = min(W, H) / 1080.0
    mx = int(W * (0.09 if H >= W else 0.12))
    top, bottom = int(H * (0.14 if H >= W else 0.10)), int(H * (0.74 if H >= W else 0.82))
    width = W - 2 * mx
    kind, parts, title = spec["kind"], spec["parts"], spec["title"]
    blocks = []                              # (height, draw(y)) so the whole piece centres in the band

    def text_block(text, name, start, floor, fill, max_lines=1, lead=1.05, centre=False):
        f, lines = _fit(d, text, name, width, int(start * s), int(floor * s), max_lines)
        asc, desc = f.getmetrics()
        h = int((asc + desc) * lead) * len(lines)
        return h, (lambda y, f=f, lines=lines: _block(d, mx, y, lines, f, fill, lead, width if centre else None))

    gap = int(44 * s)
    if title:
        blocks.append(text_block(title.upper(), "InterDisplay-Bold.ttf", 50, 30, acc, 2))
    if kind in ("number", "term"):
        p = parts[0]
        big = p["value"] or p["label"]
        blocks.append(text_block(big, "InterDisplay-Black.ttf", 400 if kind == "number" else 190, 70, ink, 1 if kind == "number" else 2, 0.98))
        if p["value"] and p["label"]:
            blocks.append(text_block(p["label"], "InterDisplay-Bold.ttf", 84, 40, soft, 3, 1.12))
    elif kind == "versus":
        for i, p in enumerate(parts[:2]):
            if i:
                rule_h = int(46 * s)
                blocks.append((rule_h, lambda y: (d.line((mx, y + rule_h // 2, mx + int(120 * s), y + rule_h // 2), fill=acc, width=max(2, int(5 * s))), y + rule_h)[1]))
            if p["value"]:
                blocks.append(text_block(p["value"], "InterDisplay-Black.ttf", 260, 64, ink if i == 0 else acc, 1, 0.98))
            blocks.append(text_block(p["label"], "InterDisplay-Bold.ttf", 80 if p["value"] else 130, 36, soft if p["value"] else ink, 2, 1.1))
    elif kind in ("list", "timeline"):
        for i, p in enumerate(parts):
            mark = p["value"] if kind == "timeline" and p["value"] else str(i + 1)
            fm = face("InterDisplay-Black.ttf", int(86 * s))
            col = int(max(d.textlength(m, font=fm) for m in [(q["value"] if kind == "timeline" and q["value"] else str(j + 1)) for j, q in enumerate(parts)]) + 30 * s)
            f, lines = _fit(d, p["label"], "InterDisplay-Bold.ttf", width - col, int(80 * s), int(36 * s), 2)
            asc, desc = f.getmetrics()
            h = max(int((asc + desc) * 1.08) * len(lines), int(sum(fm.getmetrics()) * 1.0)) + int(26 * s)

            def draw(y, mark=mark, f=f, lines=lines, col=col, fm=fm, h=h):
                d.text((mx, y), mark, font=fm, fill=acc)
                _block(d, mx + col, y + int(6 * s), lines, f, ink, 1.08)
                return y + h
            blocks.append((h, draw))
    elif kind == "bars":
        vals = [p for p in parts if p["n"] is not None and p["n"] >= 0][:5]
        top_n = max(p["n"] for p in vals) or 1.0
        for i, p in enumerate(vals):
            fl = face("InterDisplay-Bold.ttf", int(64 * s))
            fv = face("InterDisplay-Black.ttf", int(84 * s))
            lab = _wrap(d, p["label"], fl, width)[:1] or [""]
            bar_h = int(80 * s)
            h = int(sum(fl.getmetrics()) * 1.1) + bar_h + int(34 * s)

            def draw(y, p=p, lab=lab, fl=fl, fv=fv, bar_h=bar_h, h=h, first=(i == 0)):
                d.text((mx, y), lab[0], font=fl, fill=soft)
                y2 = y + int(sum(fl.getmetrics()) * 1.1)
                val = p["value"] or f"{p['n']:g}"
                room = width - d.textlength(val, font=fv) - int(24 * s)
                L = max(int(10 * s), int(room * p["n"] / top_n))
                d.rectangle((mx, y2, mx + L, y2 + bar_h), fill=acc if first else ink)
                d.text((mx + L + int(20 * s), y2 + (bar_h - sum(fv.getmetrics())) // 2), val, font=fv, fill=ink)
                return y + h
            blocks.append((h, draw))
    total = sum(h for h, _ in blocks) + gap * max(0, len(blocks) - 1)
    y = top + max(0, (bottom - top - total) // 2)
    for h, draw in blocks:
        draw(y)
        y += h + gap
    return im
