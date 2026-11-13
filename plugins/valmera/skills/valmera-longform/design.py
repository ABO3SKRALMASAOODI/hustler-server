"""Designed pictures for the long-form films: where no photograph can show what is said, a page made of the story's own
material. Every word and figure on a page comes from the script's verified facts (the writer cites the fact; the page
prints its quote, its year, its value); nothing here writes copy.

    document(...)   the source itself: a clipped page carrying the verified quote word for word, the key words underlined,
                    the page's own address at its foot (the film's receipt)
    map_page(...)   the places on NASA's Blue Marble (public domain, Wikimedia Commons), land and sea read from its pixels,
                    pins at their coordinates, the route drawn between them
    timeline(...), chart(...), number(...), quote(...)   pages of type for dates, comparisons, figures and spoken words

The look is one paper desk (cream, ink, one red), in the line of The Sudden Theory (`sYSIyYjC3dg`). Images are written
at the size the engine draws them; the engine adds the shadow, the motion and the grain.
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PAPER = (234, 227, 211)
PAPER_DK = (222, 213, 194)
INK = (28, 27, 25)
INK_SOFT = (110, 104, 94)
RED = (200, 55, 45)
NIGHT = (21, 20, 19)
FONTS = {}


def font(name, px):
    k = (name, int(px))
    if k not in FONTS:
        here = os.path.dirname(os.path.abspath(__file__))
        d = here
        path = None
        for _ in range(6):
            d = os.path.dirname(d)
            p = os.path.join(d, "worker", "fonts", name)
            if os.path.exists(p):
                path = p
                break
        path = path or os.path.join("/Users/masaoodi/Documents/Valmera/worktrees/skill-main/worker/fonts", name)
        FONTS[k] = ImageFont.truetype(path, int(px))
    return FONTS[k]


def paper(w, h, seed=1, base=PAPER, dark=False):
    """A sheet: base tone, low-frequency mottling and fine fibres, darker at the edges."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), NIGHT if dark else base)
    small = Image.effect_noise((max(w // 24, 8), max(h // 24, 8)), 22).resize((w, h), Image.BICUBIC)
    fine = Image.effect_noise((w, h), 10)
    tone = Image.merge("RGB", [small] * 3)
    im = Image.blend(im, Image.composite(im, Image.new("RGB", (w, h), PAPER_DK if not dark else (34, 32, 30)), small), 0.35)
    im = Image.blend(im, Image.merge("RGB", [fine] * 3), 0.035)
    d = ImageDraw.Draw(im)
    for _ in range(int(w * h / 9000)):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        a = rnd.uniform(0, math.pi)
        L = rnd.uniform(6, 26)
        c = (205, 196, 178) if not dark else (44, 42, 39)
        d.line([x, y, x + L * math.cos(a), y + L * math.sin(a)], fill=c, width=1)
    del tone
    # edges darker
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle([int(w * 0.04), int(h * 0.04), int(w * 0.96), int(h * 0.96)], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) * 0.06))
    edge = Image.new("RGB", (w, h), (200, 190, 170) if not dark else (10, 10, 9))
    return Image.composite(im, edge, mask)


def wrap(text, f, max_w):
    lines, cur = [], ""
    for w_ in str(text).split():
        t = (cur + " " + w_).strip()
        if f.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def _save(im, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if im.mode == "RGBA":
        im.save(out)
    else:
        im.save(out, quality=92)
    return dict(path=out, w=im.size[0], h=im.size[1])


# ----------------------------------------------------------------- the source itself

def document(quote, title, url, mark=None, date=None, out="doc.png", seed=1):
    """A clipped page: the site's name and the page's title at its head, the quote in a book face, `mark` (a few words of
    the quote) underlined in red, the address at its foot. RGBA with a slightly irregular edge."""
    W_, H_ = 1100, 1400
    im = paper(W_, H_, seed).convert("RGBA")
    d = ImageDraw.Draw(im)
    host = (url or "").split("//")[-1].split("/")[0].replace("www.", "")
    d.text((90, 92), host.upper(), font=font("InterDisplay-Bold.ttf", 30), fill=RED)
    d.line([90, 140, W_ - 90, 140], fill=INK, width=3)
    tf = font("DMSerifDisplay-Regular.ttf", 64)
    y = 170
    for ln in wrap(title, tf, W_ - 180)[:2]:
        d.text((90, y), ln, font=tf, fill=INK)
        y += 78
    if date:
        d.text((90, y + 6), str(date), font=font("InterDisplay-Bold.ttf", 28), fill=INK_SOFT)
        y += 50
    y += 30
    room = H_ - 170 - y
    px = 92
    while True:
        qf = font("InstrumentSerif-Regular.ttf", px)
        lines = wrap("“" + str(quote).strip() + "”", qf, W_ - 180)
        if len(lines) * px * 1.22 <= room or px <= 36:
            break
        px -= 4
    lh = int(px * 1.22)
    mk = [w.strip(".,;:\"'“”").lower() for w in str(mark or "").split()]
    mk_set = set(mk)
    for ln in lines[:int(room / lh)]:
        x = 90
        for w_ in ln.split(" "):
            wd = qf.getlength(w_ + " ")
            d.text((x, y), w_, font=qf, fill=INK)
            if mk_set and w_.strip(".,;:\"'“”").lower() in mk_set:
                d.line([x, y + int(px * 1.08), x + qf.getlength(w_), y + int(px * 1.08)], fill=RED, width=max(4, px // 12))
            x += wd
        y += lh
    d.line([90, H_ - 130, W_ - 90, H_ - 130], fill=INK_SOFT, width=1)
    sf = font("InterDisplay-Bold.ttf", 24)
    u = (url or "")[:90]
    d.text((90, H_ - 110), u, font=sf, fill=INK_SOFT)
    # an irregular clipped edge
    rnd = random.Random(seed)
    m = Image.new("L", (W_, H_), 0)
    md = ImageDraw.Draw(m)
    pts = [(rnd.uniform(0, 6), 0), (W_ - rnd.uniform(0, 6), 0)]
    for k in range(1, 40):
        pts.append((W_ - rnd.uniform(0, 8), H_ * k / 40))
    pts.append((W_ - rnd.uniform(0, 6), H_))
    pts.append((rnd.uniform(0, 6), H_))
    for k in range(39, 0, -1):
        pts.append((rnd.uniform(0, 8), H_ * k / 40))
    md.polygon(pts, fill=255)
    im.putalpha(m)
    return _save(im, out)


# ----------------------------------------------------------------- the map

_WORLD = {}


def _world(path):
    if path not in _WORLD:
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(path)
        im.draft("RGB", (12000, 6000))          # a JPEG decodes at half size: 33 px a degree, a third of the memory
        _WORLD[path] = im.convert("RGB")
    return _WORLD[path]


def map_page(world_path, places, route=None, label=None, out="map.png", W_=3840, H_=2160, seed=3):
    """The places on the world, in the desk's palette. places: [{name, lat, lon}]. Returns the image record and the
    frame fractions of each place (for the camera's path)."""
    import numpy as np
    wd = _world(world_path)
    ww, wh = wd.size
    lats = [float(p["lat"]) for p in places]
    lons = [float(p["lon"]) for p in places]
    cx, cy = (min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2
    span_x = max(max(lons) - min(lons), 1.0) * 1.7 + 6
    span_y = max(max(lats) - min(lats), 1.0) * 1.7 + 4
    span_x = max(span_x, span_y * W_ / H_)
    span_x = min(max(span_x, 12.0), 360.0)
    span_y = span_x * H_ / W_
    x0 = (cx - span_x / 2 + 180) / 360 * ww
    y0 = (90 - (cy + span_y / 2)) / 180 * wh
    x1 = (cx + span_x / 2 + 180) / 360 * ww
    y1 = (90 - (cy - span_y / 2)) / 180 * wh
    y0, y1 = max(y0, 0), min(y1, wh)
    crop = wd.crop((int(x0), int(y0), int(x1), int(y1))).resize((W_ // 2, H_ // 2), Image.LANCZOS)
    a = np.asarray(crop, np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    sea = ((b - r) > 18) & (b > g - 8)
    m = Image.fromarray((~sea * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(2.2)).resize((W_, H_), Image.BICUBIC)
    land = np.asarray(m, np.float32) / 255.0 > 0.5
    relief = np.asarray(crop.convert("L").resize((W_, H_), Image.BICUBIC), np.float32) / 255.0
    base = np.asarray(paper(W_, H_, seed), np.float32)
    red = np.array(RED, np.float32)
    shade = (0.82 + 0.3 * relief)[..., None]
    land_c = np.clip(red * shade, 0, 255)
    img = np.where(land[..., None], land_c, base)
    im = Image.fromarray(img.astype("uint8"))
    edge = Image.fromarray((land * 255).astype("uint8")).filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    im.paste(Image.new("RGB", (W_, H_), (120, 30, 24)), (0, 0), edge)
    d = ImageDraw.Draw(im)
    # graticule every 5 or 10 degrees, faint
    step = 10 if span_x > 40 else 5 if span_x > 15 else 2
    for lon in range(int(math.floor((cx - span_x / 2) / step) * step), int(cx + span_x / 2) + step, step):
        x = (lon - (cx - span_x / 2)) / span_x * W_
        d.line([x, 0, x, H_], fill=(198, 188, 168), width=2)
    for lat in range(int(math.floor((cy - span_y / 2) / step) * step), int(cy + span_y / 2) + step, step):
        y = ((cy + span_y / 2) - lat) / span_y * H_
        d.line([0, y, W_, y], fill=(198, 188, 168), width=2)

    def xy(p):
        return ((float(p["lon"]) - (cx - span_x / 2)) / span_x * W_, ((cy + span_y / 2) - float(p["lat"])) / span_y * H_)

    pts = {p["name"]: xy(p) for p in places}
    if route and route[0] in pts and route[1] in pts:
        (ax, ay), (bx, by) = pts[route[0]], pts[route[1]]
        n = 60
        mx, my = (ax + bx) / 2, (ay + by) / 2 - 0.18 * math.hypot(bx - ax, by - ay)
        curve = [((1 - t) ** 2 * ax + 2 * (1 - t) * t * mx + t * t * bx, (1 - t) ** 2 * ay + 2 * (1 - t) * t * my + t * t * by)
                 for t in [k / n for k in range(n + 1)]]
        for k in range(0, n, 2):
            d.line([curve[k], curve[k + 1]], fill=INK, width=9)
    lf = font("Anton-Regular.ttf", 76)
    for name, (x, y) in pts.items():
        d.ellipse([x - 26, y - 26, x + 26, y + 26], fill=(250, 246, 236), outline=INK, width=8)
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=INK)
        tw = lf.getlength(name.upper())
        tx = x + 44 if x + 44 + tw < W_ - 60 else x - 44 - tw     # the label never covers its own pin
        tx = min(max(tx, 40), W_ - tw - 60)
        ty = min(max(y - 52, 40), H_ - 140)
        d.rectangle([tx - 18, ty - 6, tx + tw + 18, ty + 92], fill=(250, 246, 236), outline=INK, width=4)
        d.text((tx, ty), name.upper(), font=lf, fill=INK)
    if label:
        bf = font("Anton-Regular.ttf", 120)
        d.rectangle([110, H_ - 300, 110 + bf.getlength(label.upper()) + 70, H_ - 130], fill=INK)
        d.text((145, H_ - 290), label.upper(), font=bf, fill=(250, 246, 236))
    rec = _save(im, out)
    rec["points"] = {k: (v[0] / W_, v[1] / H_) for k, v in pts.items()}
    return rec


# ----------------------------------------------------------------- pages of type

def _page(W_=1920, H_=1080, seed=5, dark=False):
    return paper(W_, H_, seed, dark=dark)


def timeline(points, out, seed=7):
    """Years on a rule, each with its label; the last point in red (where the story is now)."""
    W_, H_ = 1920, 1080
    im = _page(W_, H_, seed)
    d = ImageDraw.Draw(im)
    pts = sorted(points, key=lambda p: float(p.get("year") or 0))
    y = 600
    d.line([150, y, W_ - 150, y], fill=INK, width=6)
    yf, lf = font("Anton-Regular.ttf", 96), font("InstrumentSerif-Italic.ttf", 46)
    n = len(pts)
    for i, p in enumerate(pts):
        x = 230 + (W_ - 460) * (i / max(n - 1, 1))
        c = RED if i == n - 1 else INK
        d.ellipse([x - 20, y - 20, x + 20, y + 20], fill=c)
        yr = str(p.get("year"))
        d.text((x - yf.getlength(yr) / 2, y - 170), yr, font=yf, fill=c)
        for k, ln in enumerate(wrap(p.get("label") or "", lf, 330)[:2]):
            d.text((x - lf.getlength(ln) / 2, y + 50 + k * 52), ln, font=lf, fill=INK)
    return _save(im, out)


def chart(title, bars, out, seed=9):
    """Bars of the story's own figures, each printed as the source prints it; the largest in red."""
    W_, H_ = 1920, 1080
    im = _page(W_, H_, seed)
    d = ImageDraw.Draw(im)
    tf = font("Anton-Regular.ttf", 84)
    d.text(((W_ - tf.getlength(str(title).upper())) / 2, 120), str(title).upper(), font=tf, fill=INK)
    vals = [abs(float(b.get("value") or 0)) for b in bars]
    mx = max(vals + [1e-9])
    lf, vf = font("InterDisplay-Bold.ttf", 40), font("Anton-Regular.ttf", 72)
    n = len(bars)
    top, bottom = 300, 920
    row = (bottom - top) / max(n, 1)
    for i, b in enumerate(bars):
        y = top + i * row
        frac = vals[i] / mx
        c = RED if vals[i] == mx else INK
        d.text((150, y + 10), str(b.get("label") or "")[:40], font=lf, fill=INK_SOFT)
        bw = max(12, (W_ - 700) * frac)
        d.rectangle([150, y + 64, 150 + bw, y + 64 + row * 0.42], fill=c)
        d.text((170 + bw, y + 54), str(b.get("print") or b.get("value")), font=vf, fill=c)
    return _save(im, out)


def number(print_, label, out, seed=11, dark=False):
    W_, H_ = 1920, 1080
    im = _page(W_, H_, seed, dark=dark)
    d = ImageDraw.Draw(im)
    s = str(print_)
    px = 360
    f = font("Anton-Regular.ttf", px)
    while f.getlength(s) > W_ * 0.8 and px > 120:
        px -= 20
        f = font("Anton-Regular.ttf", px)
    d.text(((W_ - f.getlength(s)) / 2, 300), s, font=f, fill=RED)
    lf = font("InstrumentSerif-Italic.ttf", 84)
    for k, ln in enumerate(wrap(label or "", lf, W_ * 0.75)[:2]):
        d.text(((W_ - lf.getlength(ln)) / 2, 300 + px * 1.2 + k * 92), ln, font=lf, fill=(234, 227, 211) if dark else INK)
    return _save(im, out)


def quote(text, who, out, seed=13):
    W_, H_ = 1920, 1080
    im = _page(W_, H_, seed, dark=True)
    d = ImageDraw.Draw(im)
    px = 92
    f = font("InstrumentSerif-Italic.ttf", px)
    lines = wrap("“" + str(text).strip() + "”", f, W_ * 0.74)
    while len(lines) > 5 and px > 56:
        px -= 8
        f = font("InstrumentSerif-Italic.ttf", px)
        lines = wrap("“" + str(text).strip() + "”", f, W_ * 0.74)
    y = (H_ - len(lines) * px * 1.12) / 2 - 40
    for ln in lines:
        d.text(((W_ - f.getlength(ln)) / 2, y), ln, font=f, fill=(240, 233, 218))
        y += px * 1.12
    wf = font("InterDisplay-Bold.ttf", 40)
    w_ = "— " + str(who or "").upper()
    d.text(((W_ - wf.getlength(w_)) / 2, y + 40), w_, font=wf, fill=RED)
    return _save(im, out)
