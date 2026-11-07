"""A compiled L1 lowered onto today's EDL (Director DESIGN 2.7, project: shot).

Nothing here interprets a film. It reads numbers from L1 and writes the nearest item today's
renderer has. What today's renderer cannot draw is left out and named in the report, never
faked. Under today's minimums (overlay 0.2 s, text 0.3 s) items merge. Those minimums bind
parents only.

Stills are baked with Pillow and numpy into <work>/today/. The EDL carries local keys; the
caller swaps each for a project storage key (score.film) before the write.
"""
import collections
import hashlib
import json
import math
import os
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import score.compiler as score
import score.shape as shape

OV_MIN_S, TX_MIN_S = 0.2, 0.3      # today's minimums: schemas.validate_edl
KF_MAX = 20                        # keyframes on one curve, under schemas.ANIM_MAX_KEYFRAMES
FAMILY = {"Anton-Regular": ("Anton", False), "BebasNeue-Regular": ("Bebas Neue", False),
          "ArchivoBlack-Regular": ("Archivo Black", False), "Poppins-Black": ("Poppins Black", False),
          "Syne-ExtraBold": ("Syne ExtraBold", False), "PlayfairDisplay-Black": ("Playfair Display Black", False),
          "InstrumentSerif-Regular": ("Instrument Serif", False), "InstrumentSerif-Italic": ("Instrument Serif", True),
          "DMSerifDisplay-Regular": ("DM Serif Display", False), "DMSerifDisplay-Italic": ("DM Serif Display", True),
          "Montserrat-Bold": ("Montserrat", False), "InterDisplay-Black": ("Inter Display Black", False),
          "InterDisplay-ExtraBold": ("Inter Display ExtraBold", False), "InterDisplay-Bold": ("Inter Display Bold", False),
          "PlusJakartaSans-ExtraBold": ("Plus Jakarta Sans ExtraBold", False)}
ACCENT = {"neg": "negative", "threshold": "threshold", "mono": "mono", "posterise": "posterize"}
# A burn is the engine's luma burn: the picture's near-white share 76, 61, 50 ... 2 % over 7 frames.
# Today's renderer has no luma burn. Lowered as white_hit at full strength it drew an opaque white box
# over the type and full-white frames (p5374 today, rated broken_render). The nearest honest item is a
# light: a full frame lifts BURN_PEAK of the way to white on one frame and drains over BURN_DECAY_F,
# and a burn on a rect is a soft glow still under the type, fading on the engine's own curve.
BURN_PEAK, BURN_DECAY_F, GLOW_GAIN = 0.35, 6, 0.55
EDGE_OUTLINE = 2.2         # libass outline at the caption's play resolution, for a word marked `edge`


def r2(x):
    return round(float(x), 2)


def rect_mid(r):
    """One rect for a constant-only target: the middle of an animated list."""
    if r is None:
        return None
    return r[len(r) // 2] if isinstance(r[0], (list, tuple)) else r


def animated(v):
    return isinstance(v, (list, tuple)) and len(set(v)) > 1


def curve(v, fps, n=KF_MAX):
    """A per-frame list to at most n keyframes on the item's own clock. Kept where the curve bends."""
    v = [float(x) for x in v]
    if len(v) < 2:
        return v[0] if v else None
    keep = [0, len(v) - 1]
    while len(keep) < min(n, len(v)):
        keep.sort()
        best, at = 0.0, None
        for a, b in zip(keep, keep[1:]):
            for i in range(a + 1, b):
                d = abs(v[i] - (v[a] + (v[b] - v[a]) * (i - a) / (b - a)))
                if d > best:
                    best, at = d, i
        if at is None or best < 1e-3:
            break
        keep.append(at)
    return [{"t": round(i / fps, 3), "v": round(v[i], 4)} for i in sorted(keep)]


def face_of(voice):
    stem = os.path.splitext(os.path.basename(voice.get("font") or ""))[0]
    return FAMILY.get(stem)


def libass_px(voice, px):
    """The size libass needs to draw a font at px to the em: it sizes against ascent + descent
    (worker/type_metrics.py), so Anton asked for at 127 draws at 84."""
    f = score._face(voice.get("font") or "", 400)
    if f is None:
        return px
    asc, desc = f.getmetrics()
    return px * (asc + desc) / 400.0


def text_w(text, voice):
    """Width by the compiler's own measure, so a centre here is the centre it solved."""
    return score.text_px(text, voice)


# ------------------------------------------------------------------ bakes, Pillow and numpy only

class Bake:
    def __init__(self, work):
        self.work = work
        self.dir = os.path.join(work, "today")
        os.makedirs(self.dir, exist_ok=True)
        self.locals = {}

    def path(self, *key):
        h = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:14]
        return f"today/{h}.png", os.path.join(self.dir, f"{h}.png")

    def done(self, key, p):
        self.locals[key] = p
        return key

    def plate(self, pl, W, H):
        c0, c1 = pl.get("c0") or "#101010", pl.get("c1") or pl.get("c0") or "#0A0A0A"
        key, p = self.path("plate", c0, c1, W, H)
        if not os.path.exists(p):
            a0, a1 = (np.array([int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], np.float32) for c in (c0, c1))
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            rad = np.clip(np.hypot((xx - W / 2) / (W * 0.75), (yy - H * 0.5) / (H * 0.7)), 0, 1)[..., None]
            # dithered: a slow 8-bit gradient bands, and the encoder makes the bands worse (the banded pink of p5302)
            noise = np.random.RandomState(int(key.split("/")[-1][:6], 16) & 0xFFFF).uniform(-1.1, 1.1, (H, W, 1))
            Image.fromarray(np.clip(a0 * (1 - rad) + a1 * rad + noise + 0.5, 0, 255).astype(np.uint8)).save(p, optimize=False)
        return self.done(key, p)

    def glow(self):
        """A soft white light, alpha only where it shines. Placed and scaled as an overlay."""
        key, p = self.path("glow", 1)
        if not os.path.exists(p):
            n = 512
            yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
            d = np.hypot((xx - n / 2) / (n / 2), (yy - n / 2) / (n / 2))
            a = np.exp(-(d * 1.7) ** 2) * (d < 1)
            rgba = np.full((n, n, 4), 255, np.uint8)
            rgba[..., 3] = (a * 255).astype(np.uint8)
            Image.fromarray(rgba).save(p)
        return self.done(key, p)

    def pool(self, W, H):
        """A soft dark ellipse. Placed and scaled as an overlay."""
        key, p = self.path("pool", W)
        if not os.path.exists(p):
            n = 512
            yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
            d = np.hypot((xx - n / 2) / (n / 2), (yy - n / 2) / (n / 2))
            a = np.exp(-(d * 1.9) ** 2) * (d < 1)
            rgba = np.zeros((n, n, 4), np.uint8)
            rgba[..., 3] = (a * 255).astype(np.uint8)
            Image.fromarray(rgba).save(p)
        return self.done(key, p)

    def type_line(self, text, voice, ink, W):
        """A joined script today's title path has no font for: the engine's own shaping, baked to a tight RGBA still.
        Returns key, width in frame fractions, ink centre x and y offset from the word's origin in pixels."""
        px = float(voice.get("px", 58))
        spec = ("type", text, voice.get("font"), px, ink)
        key, p = self.path(*spec)
        face = shape.Face.get(voice["font"])
        lines, _ = face.outline(text, px * 2)
        x0, y0, x1, y1 = lines[:, [0, 2]].min(), lines[:, [1, 3]].min(), lines[:, [0, 2]].max(), lines[:, [1, 3]].max()
        pad = 8
        ln = lines.copy()
        ln[:, [0, 2]] += pad - x0
        ln[:, [1, 3]] += pad - y0
        w, h = int(x1 - x0) + 2 * pad, int(y1 - y0) + 2 * pad
        if not os.path.exists(p):
            cov = shape.coverage(ln, w, h)
            rgba = np.zeros((h, w, 4), np.uint8)
            rgba[..., :3] = [int((ink or "#FFFFFF").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
            rgba[..., 3] = (np.clip(cov, 0, 1) * 255).astype(np.uint8)
            Image.fromarray(rgba).resize((w // 2, h // 2), Image.LANCZOS).save(p)
        return self.done(key, p), (w / 2) / W, (x0 + x1) / 4, (y0 + y1) / 4

    def card(self, it, W, H, work, asset):
        """A card's still with its corners, border and shadow. Returns key, centre shift, width fraction."""
        cw, ch = int(round(float(it.get("w", 0.5)) * W)), int(round(float(it.get("h", 0.5)) * H))
        sh, bd = it.get("shadow") or {}, it.get("border") or {}
        rad = int(round(float(it.get("radius", 0) or 0) * W))
        sig = float(sh.get("blur", 0)) * W if sh else 0.0
        pad = int(math.ceil(sig * 3 + abs(float(sh.get("dx", 0))) * W + abs(float(sh.get("dy", 0))) * W)) if sh else 0
        spec = ("card", cw, ch, rad, pad, sh, bd, it.get("plate"), it.get("pic"), rect_mid(it.get("crop")), it.get("treat"))
        key, p = self.path(*spec)
        tw = cw + 2 * pad
        if not os.path.exists(p):
            if it.get("pic") == "plate" or asset is None:
                pl = it.get("plate") or {}
                face = Image.new("RGB", (cw, ch), pl.get("c0") or "#101010")
            else:
                im = Image.open(os.path.join(work, asset["path"])).convert("RGB")
                c = rect_mid(it.get("crop")) or [0, 0, 1, 1]
                face = im.crop((int(c[0] * im.width), int(c[1] * im.height), int(c[2] * im.width), int(c[3] * im.height))).resize((cw, ch), Image.LANCZOS)
                if (it.get("treat") or {}).get("mode") == "neg":
                    face = Image.fromarray(255 - np.asarray(face))
            bw = int(round(float(bd.get("w", 0)) * W))
            if bw:
                ImageDraw.Draw(face).rectangle([0, 0, cw - 1, ch - 1], outline=bd.get("c") or "#FFFFFF", width=bw)
            canvas = Image.new("RGBA", (tw, ch + 2 * pad), (0, 0, 0, 0))
            mask = Image.new("L", (cw * 2, ch * 2), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw * 2 - 1, ch * 2 - 1], rad * 2, fill=255)
            mask = mask.resize((cw, ch), Image.LANCZOS)
            if sh:
                s = Image.new("L", canvas.size, 0)
                s.paste(mask, (pad + int(float(sh.get("dx", 0)) * W), pad + int(float(sh.get("dy", 0)) * W)))
                s = s.filter(ImageFilter.GaussianBlur(sig)).point(lambda v: int(v * float(sh.get("a", 0.5))))
                canvas.paste((0, 0, 0, 255), (0, 0), s)
            canvas.paste(face, (pad, pad), mask)
            canvas.save(p)
        return self.done(key, p), tw / W


# ------------------------------------------------------------------ the spine

def lower_spine(l1, bake, rep):
    """keep from the spine. Spans out of source order, and silence, go in as inserts. A span of the
    voice that plays out of order keeps its sound through an insert of main."""
    fps = float(l1["fps"])
    W, H = l1["frame"]
    spine = sorted(l1.get("spine") or [], key=lambda s: s["f"][0])
    keep, inserts = [], []
    prog, pre = 0, 0.0
    last_src = -1.0
    black = None

    def silence(a, b):
        nonlocal black
        if black is None:
            black = bake.plate({"c0": "#000000"}, W, H)
        inserts.append({"id": f"gap{a}", "asset_key": black, "kind": "image", "at_output_s": r2(pre), "duration_s": r2((b - a) / fps)})
        rep["silences"] = rep.get("silences", 0) + 1

    for s in spine:
        a, b = s["f"]
        if a > prog:
            silence(prog, a)
        t0, t1 = s["src"]
        if t0 >= last_src - 0.04:       # a beat written in seconds can start a frame before the last one ended: that is continuity, not a jump
            d = max(0.0, last_src - t0)         # the same length, or the programme ends short of the film
            t0, t1 = t0 + d, t1 + d
            keep.append([r2(t0), r2(t1)])
            pre += t1 - t0
            last_src = t1
        else:
            bake.locals["main"] = os.path.join(bake.work, "source.mp4")
            inserts.append({"id": f"spine{a}", "asset_key": "main", "kind": "video", "at_output_s": r2(pre), "duration_s": r2(t1 - t0), "source_start_s": r2(t0)})
            rep["out_of_order_spans"] = rep.get("out_of_order_spans", 0) + 1
        prog = b
    if prog < l1["dur_f"]:
        silence(prog, l1["dur_f"])
    return keep, inserts


def on_spine(l1, it, fps):
    """Does this shot of main show what the spine is playing at that moment?"""
    a = it["f"][0]
    for s in l1.get("spine") or []:
        if s["f"][0] <= a < s["f"][1]:
            want = s["src"][0] + (a - s["f"][0]) / fps
            return abs(want - it["src"][0]) < 0.06
    return False


# ------------------------------------------------------------------ pictures

def shot_runs(shots, n_min):
    """Shots to runs of at least n_min frames. A run takes the picture of its longest shot, first on a tie."""
    runs, cur = [], []
    for s in shots:
        cur.append(s)
        if cur[-1]["f"][1] - cur[0]["f"][0] >= n_min:
            runs.append(cur)
            cur = []
    if cur:
        if runs:
            runs[-1] += cur
        else:
            runs.append(cur)
    out = []
    for run in runs:
        main_ = max(run, key=lambda s: s["f"][1] - s["f"][0])
        out.append((dict(main_), run[0]["f"][0], run[-1]["f"][1], len(run)))
    return out


def in_window(c, win):
    """A crop in source fractions, as fractions of the renderer's own framing of the upload."""
    w, h = win[2] - win[0], win[3] - win[1]
    return [(c[0] - win[0]) / w, (c[1] - win[1]) / h, (c[2] - win[0]) / w, (c[3] - win[1]) / h]


def base_window(l1):
    """How the renderer frames the upload before any camera, in source fractions, and the EDL frame
    that says it. The Score's crops are in source fractions and the camera works on the framed
    picture: p5363's 4:3 cartoon read a 9:16 crop as a 2.6x push into the centre 9:16, and cut its
    title and characters. A crop that holds marks past the source's edge (compiler.keep_marks_whole)
    fits the upload whole on a blurred copy of itself."""
    a = (l1.get("assets") or {}).get("main") or {}
    W, H = l1["frame"]
    sw, sh = float(a.get("w") or 0), float(a.get("h") or 0)
    frame = {"ratio": "9:16", "mode": "crop"} if H > W else {"ratio": "16:9", "mode": "crop"}
    if sw <= 0 or sh <= 0:
        return [0.0, 0.0, 1.0, 1.0], frame
    target = (W / H) * (sh / sw)            # the frame's width over height, in source fractions
    if abs(target - 1.0) <= 0.02:
        return [0.0, 0.0, 1.0, 1.0], frame
    fps = float(l1["fps"])
    rects = []
    for it in l1["items"]:
        if it.get("k") == "shot" and it.get("pic") == "main" and "src" in it and it.get("crop") and on_spine(l1, it, fps):
            rects += [list(c) for c in it["crop"]] if isinstance(it["crop"][0], (list, tuple)) else [list(it["crop"])]
    past = any(c[0] < -0.004 or c[1] < -0.004 or c[2] > 1.004 or c[3] > 1.004 for c in rects)
    wide = target < 1.0                      # an upload wider than the frame: the window slides across it
    span = (target, 1.0) if wide else (1.0, 1.0 / target)
    lo = min((c[0 if wide else 1] for c in rects), default=0.5)
    hi = max((c[2 if wide else 3] for c in rects), default=0.5)
    if not past and hi - lo <= span[0 if wide else 1] + 1e-3:
        half = span[0 if wide else 1] / 2
        m = min(max((lo + hi) / 2, half), 1 - half)
        frame["focus_x" if wide else "focus_y"] = round(m, 4)
        return ([m - half, 0.0, m + half, 1.0] if wide else [0.0, m - half, 1.0, m + half]), frame
    frame["mode"] = "pad_blur"
    hb = 1.0 / target if wide else 1.0
    wb = 1.0 if wide else target
    return [0.5 - wb / 2, 0.5 - hb / 2, 0.5 + wb / 2, 0.5 + hb / 2], frame


def zoom_of(it, a, b, fps, zid, win=(0.0, 0.0, 1.0, 1.0)):
    """A crop of the live take is the camera, on the picture as the renderer frames it (`win`)."""
    crops = it.get("crop")
    if isinstance(crops[0], (list, tuple)):
        n = len(crops)
        ix = sorted({0, n - 1, *[int(round(i * (n - 1) / 7)) for i in range(8)]})
    else:
        crops, n, ix = [crops, crops], 2, [0, 1]
    crops = [in_window(c, win) for c in crops]
    pts = []
    for i in ix:
        c = crops[i]
        pts.append({"f": round(i / max(n - 1, 1), 4), "cx": round((c[0] + c[2]) / 2, 4), "cy": round((c[1] + c[3]) / 2, 4),
                    "s": round(max(1 / max(c[2] - c[0], 1e-3) - 1, 0.0), 4)})
    return {"id": zid, "start": r2(a / fps), "end": r2(b / fps), "strength": max(p["s"] for p in pts) or 0.0001, "mode": "path", "path": pts, "ease": "linear"}


def full(crop):
    c = rect_mid(crop)
    return c is None or (c[0] < 0.004 and c[1] < 0.004 and c[2] > 0.996 and c[3] > 0.996)


def still_crop(it):
    c = rect_mid(it.get("crop"))
    if c is not None:            # a crop held past the source's edge shows the whole source on a card
        c = [min(max(float(x), 0.0), 1.0) for x in c]
    return None if full(c) else [round(float(x), 4) for x in c]


def lower_pictures(l1, work, bake, rep):
    fps, W, H = float(l1["fps"]), l1["frame"][0], l1["frame"][1]
    assets = l1.get("assets") or {}
    overlays, zooms, fx = [], [], []
    shots = sorted((i for i in l1["items"] if i.get("k") == "shot"), key=lambda i: i["f"][0])
    n_min = int(math.ceil(OV_MIN_S * fps - 1e-6))
    runs = shot_runs(shots, n_min)
    rep["shots"], rep["picture_runs"] = len(shots), len(runs)
    win, rep["frame"] = base_window(l1)
    for it, a, b, merged in runs:
        pid = it["id"]
        start, dur = r2(a / fps), r2((b - a) / fps)
        pic = it.get("pic")
        if merged > 1:
            rep["merged_shots"] = rep.get("merged_shots", 0) + merged - 1
        if pic == "main" and "src" in it and on_spine(l1, it, fps):
            c = it["crop"]
            if not full(in_window(rect_mid(c), win)) or animated([tuple(x) for x in c] if isinstance(c[0], (list, tuple)) else []):
                zooms.append(zoom_of(it, a, b, fps, f"z.{pid}", win))
            continue
        ov = {"id": f"ov.{pid}", "start": start, "duration_s": dur, "fit": "cover"}
        asset = assets.get(pic)
        if pic == "plate" or asset is None:
            if pic != "plate":
                rep.setdefault("missing_pictures", []).append(pic)
            ov.update(asset_key=bake.plate(it.get("plate") or {}, W, H), kind="image")
        else:
            key = "main" if pic == "main" else pic
            bake.locals[key] = os.path.join(work, asset["path"])
            ov.update(asset_key=key, kind=asset["kind"])
            if asset["kind"] == "video":
                t0, t1 = (it.get("src") or [0, 0])[:2]
                ov["source_start_s"] = r2(t0)
                if abs((t1 - t0) - (it["f"][1] - it["f"][0]) / fps) > 0.05:
                    rep.setdefault("rate_held", []).append(pid)
            c = still_crop(it)
            if c:
                ov["source_crop"] = c
            if isinstance(it.get("crop"), list) and isinstance(it["crop"][0], (list, tuple)) and len({tuple(x) for x in it["crop"]}) > 1:
                rep["moves_held_on_pictures"] = rep.get("moves_held_on_pictures", 0) + 1
        overlays.append(ov)
        gain = float((it.get("grade") or {}).get("gain", 1.0))
        if gain < 0.85 and pic != "plate":        # an overlay has no grade today. dna.match lands as black laid over it at the right strength
            overlays.append({"id": f"dm.{pid}"[:60], "asset_key": bake.plate({"c0": "#000000"}, W, H), "kind": "image", "start": start, "duration_s": dur,
                             "fit": "cover", "opacity": round(min(1 - gain, 0.92), 3), "z_index": 1})
            rep["dimmed_pictures"] = rep.get("dimmed_pictures", 0) + 1
        treat = (it.get("treat") or {}).get("mode")
        if treat == "burn":          # the shot opens on the burn's light, never a white shot
            fx.append(burn_hit(f"tr.{pid}", a, fps))
            rep["treat_burns_as_light"] = rep.get("treat_burns_as_light", 0) + 1
        elif treat:
            kind = ACCENT.get(treat)
            if kind:
                fx.append({"id": f"tr.{pid}", "type": "accent", "kind": kind, "stage": "pre_captions", "start": start, "end": r2(b / fps)})
            else:
                rep.setdefault("treats_dropped", []).append(pid)
    return overlays, zooms, fx, runs


def lower_cards(l1, work, bake, rep):
    fps, W, H = float(l1["fps"]), l1["frame"][0], l1["frame"][1]
    assets = l1.get("assets") or {}
    out = []
    for it in (i for i in l1["items"] if i.get("k") == "card"):
        a, b = it["f"]
        if (b - a) / fps < OV_MIN_S:
            rep["cards_too_short"] = rep.get("cards_too_short", 0) + 1
            continue
        asset = assets.get(it.get("pic"))
        ov = {"id": f"cd.{it['id']}", "start": r2(a / fps), "duration_s": r2((b - a) / fps)}
        if asset is None or asset.get("kind") == "image":
            key, width = bake.card(it, W, H, work, asset)
            ov.update(asset_key=key, kind="image", scale=round(width, 4))
        else:
            bake.locals[it["pic"]] = os.path.join(work, asset["path"])
            ov.update(asset_key=it["pic"], kind="video", scale=round(float(it.get("w", 0.5)), 4), source_start_s=r2((it.get("src") or [0])[0]))
            c = still_crop(it)
            if c:
                ov["source_crop"] = c
            rep["video_cards_plain"] = rep.get("video_cards_plain", 0) + 1
        for name, key_ in (("x", "x"), ("y", "y"), ("op", "opacity"), ("rot", "rotation")):
            v = it.get(name)
            if v is None:
                continue
            if animated(v):
                ov[key_] = curve(v, fps)
            else:
                v = v[0] if isinstance(v, (list, tuple)) else v
                if not (name == "op" and float(v) >= 0.999) and not (name == "rot" and not v):
                    ov[key_] = float(v)
        s = it.get("s")
        if animated(s) and "scale" in ov:
            base = ov["scale"]
            ov["scale"] = [{"t": k["t"], "v": round(k["v"] * base, 4)} for k in curve(s, fps)]
        ov["z_index"] = int(min(max(2 + int(it.get("z", 0)), -1000), 1000))
        out.append(ov)
    return out


# ------------------------------------------------------------------ flashes

def lower_flashes(l1, bake, rep):
    fps, W, H = float(l1["fps"]), l1["frame"][0], l1["frame"][1]
    fx, vectors, pools, glows = [], [], [], []
    open_pool = {}
    for it in sorted((i for i in l1["items"] if i.get("k") == "flash"), key=lambda i: i["f"][0]):
        mode, (a, b) = it.get("mode"), it["f"]
        r = it.get("rect")
        if mode == "pool":
            k = (tuple(rect_mid(r) or (0, 0, 1, 1)), float(rect_mid([it.get("a", 1)]) if isinstance(it.get("a"), list) else it.get("a", 1)))
            if k in open_pool and open_pool[k]["b"] == a:
                open_pool[k]["b"] = b
            else:
                open_pool[k] = {"a": a, "b": b, "rect": k[0], "alpha": k[1], "id": it["id"]}
                pools.append(open_pool[k])
        elif mode == "burn":
            c = rect_mid(r)
            if c and not full(c):
                th = it.get("th") if isinstance(it.get("th"), list) else [0.24, 0.39, 0.5, 0.7, 0.86, 0.94, 0.98]
                n = max(len(th), int(math.ceil(OV_MIN_S * fps)))
                ops = [round(GLOW_GAIN * (1 - float(th[min(i, len(th) - 1)])), 4) for i in range(n)] + [0.0]
                glows.append({"id": f"gl.{it['id']}"[:60], "asset_key": bake.glow(), "kind": "image", "start": r2(a / fps), "duration_s": r2((n + 1) / fps),
                              "x": round((c[0] + c[2]) / 2, 4), "y": round((c[1] + c[3]) / 2, 4), "scale": round(min(max((c[2] - c[0]) * 1.5, 0.1), 1.6), 4),
                              "opacity": curve(ops, fps), "z_index": 3})
                rep["burns_as_glow"] = rep.get("burns_as_glow", 0) + 1
            else:
                fx.append(burn_hit(f"fl.{it['id']}"[:80], a, fps))
                rep["burns_as_light"] = rep.get("burns_as_light", 0) + 1
        elif mode in ACCENT:
            item = {"id": f"fl.{it['id']}"[:80], "type": "accent", "kind": ACCENT[mode], "stage": "pre_captions", "start": r2(a / fps), "end": r2(b / fps)}
            if item["end"] <= item["start"]:
                item["end"] = r2(item["start"] + 1 / fps)
            c = rect_mid(r)
            if c and not full(c):
                item["region"] = {"shape": "rect", "box": [round(float(x), 4) for x in c]}
            fx.append(item)
        elif mode == "fill":
            if (b - a) / fps < 0.05:
                rep["fills_too_short"] = rep.get("fills_too_short", 0) + 1
                continue
            c = rect_mid(r) or (0, 0, 1, 1)
            if (b - a) / fps < TX_MIN_S:
                # A flash cut of a frame or two. A vector is 0.3 s at least (validate_edl refused every
                # first replay's film on it), so it lands as today's flash: the picture mixed to its colour.
                ink = (it.get("c") or "#FFFFFF").upper()
                item = {"id": f"fl.{it['id']}"[:80], "type": "accent", "stage": "pre_captions", "start": r2(a / fps), "end": r2(b / fps),
                        "intensity": round(min(float(rect_mid([it.get("a", 1)]) if isinstance(it.get("a"), list) else it.get("a", 1)), 1.0), 3)}
                item.update({"kind": "white_hit"} if ink in ("#FFFFFF", "#FFF") else {"kind": "colour_hit", "colour": ink})
                if item["end"] <= item["start"]:
                    item["end"] = r2(item["start"] + 1 / fps)
                if not full(c):
                    item["region"] = {"shape": "rect", "box": [round(float(x), 4) for x in c]}
                fx.append(item)
                rep["fills_as_flash"] = rep.get("fills_as_flash", 0) + 1
                continue
            al = it.get("a", 1)
            vec = {"id": f"vc.{it['id']}"[:60], "kind": "rectangle", "start": r2(a / fps), "end": r2(b / fps), "x": round((c[0] + c[2]) / 2, 4),
                   "y": round((c[1] + c[3]) / 2, 4), "width": round(max(c[2] - c[0], 0.002), 4), "height": round(max(c[3] - c[1], 0.002), 4),
                   "color": it.get("c") or "#FFFFFF", "opacity": round(float(sum(al) / len(al) if isinstance(al, (list, tuple)) else al), 3)}
            if isinstance(r, list) and r and isinstance(r[0], (list, tuple)) and len({tuple(x) for x in r}) > 1:
                vec["motion"] = {"x": curve([(q[0] + q[2]) / 2 for q in r], fps), "y": curve([(q[1] + q[3]) / 2 for q in r], fps)}
            vectors.append(vec)
        else:
            rep.setdefault("flash_modes_dropped", collections.Counter())[mode] += 1
    overlays = []
    key = None
    for p in pools:
        dur = (p["b"] - p["a"]) / fps
        if dur < OV_MIN_S:
            rep["pools_too_short"] = rep.get("pools_too_short", 0) + 1
            continue
        key = key or bake.pool(W, H)
        x0, y0, x1, y1 = p["rect"]
        # a square still stretched to the rect's width: the ellipse keeps its centre, not its height
        overlays.append({"id": f"pl.{p['id']}"[:60], "asset_key": key, "kind": "image", "start": r2(p["a"] / fps), "duration_s": r2(dur),
                         "x": round((x0 + x1) / 2, 4), "y": round((y0 + y1) / 2, 4), "scale": round(max(x1 - x0, 0.1), 4), "opacity": round(min(p["alpha"] * 1.3, 1.0), 3), "z_index": 1})
    return fx, vectors, overlays + glows


def burn_hit(fid, a, fps):
    return {"id": fid, "type": "accent", "kind": "white_hit", "stage": "pre_captions", "intensity": BURN_PEAK, "decay_f": BURN_DECAY_F,
            "start": r2(a / fps), "end": r2((a + 1) / fps)}


# ------------------------------------------------------------------ type

def chunk_lines(words):
    """Words of a chunk in reading order, lines top to bottom."""
    ys = {}
    for w in words:
        y = w["y"][-1] if isinstance(w["y"], list) else w["y"]
        ys.setdefault(round(y, 3), []).append(w)
    out = []
    for y in sorted(ys):
        out.append(sorted(ys[y], key=lambda w: w["x"][-1] if isinstance(w["x"], list) else w["x"]))
    return out


def src_at(l1, f, fps):
    """Programme frame to source second, through the spine. None off the spine."""
    best = None
    for s in l1.get("spine") or []:
        if s["f"][0] <= f <= s["f"][1]:
            best = s["src"][0] + (f - s["f"][0]) / fps
            if f < s["f"][1]:
                return best
    return best


def lower_captions(l1, rep, src_dur=0.0):
    """Chunks as dictated items, each at its own height. The second voice falls away: one style to an item."""
    import captions as caplib
    fps, H = float(l1["fps"]), l1["frame"][1]
    groups = collections.OrderedDict()
    for it in l1["items"]:
        if it.get("k") == "word" and it.get("role") == "cap":
            base = it["id"].split(".p")[0]
            groups.setdefault(it.get("chunk") or base, {}).setdefault(base, []).append(it)
    base_size = caplib.PRESETS["clean"]["base_size"]
    f = max(l1["frame"][0] / caplib.BASE_PLAY_RES[0], H / caplib.BASE_PLAY_RES[1])
    out = []
    for cid, parts in groups.items():
        first = [p[0] for p in parts.values()]
        a = min(p["f"][0] for ps in parts.values() for p in ps)
        b = max(p["f"][1] for ps in parts.values() for p in ps)
        t0, t1 = src_at(l1, a, fps), src_at(l1, b, fps)
        if t0 is None or t1 is None:
            rep["captions_off_spine"] = rep.get("captions_off_spine", 0) + 1
            continue
        lines = chunk_lines(first)
        v = l1["voices"].get(first[0]["v"]) or l1["voices"].get("a") or {}
        px = float(v.get("px", 58))
        y = float(np.mean([(w["y"][-1] if isinstance(w["y"], list) else w["y"]) for w in first])) - 0.36 * px / H
        font = (face_of(l1["voices"].get("a", v)) or ("Inter Display Bold", False))[0]
        font = font if font != "Plus Jakarta Sans ExtraBold" else "Plus Jakarta Sans"
        style = {"preset": "clean", "font": font, "color": first[0].get("ink") or "#FFFFFF", "uppercase": False, "animation": "none",
                 "anchor_y": round(min(max(y, 0.02), 0.98), 4), "size_scale": round(min(max(libass_px(v, px) / (base_size * f), 0.5), 3.0), 3), "single_line": len(lines) == 1,
                 "emphasis": "none", "outline_width": 0.0, "shadow": 1.0}
        text = "\n".join(" ".join(w["t"] for w in ln) for ln in lines) if len(lines) > 1 else " ".join(w["t"] for w in lines[0])
        # A chunk can outlive a cut, and its ink was solved shot by shot. One item per run of one ink, so
        # dark type never carries on onto a page it was not chosen for (p5302: dark COM PROMOCOES on pink).
        longest = max(parts.values(), key=lambda ps: ps[-1]["f"][1] - ps[0]["f"][0])
        runs = []
        for w in sorted(longest, key=lambda q: q["f"][0]):
            edge = any(p.get("edge") for ps in parts.values() for p in ps if p["f"][0] < w["f"][1] and p["f"][1] > w["f"][0])
            if runs and runs[-1][2] == w.get("ink") and runs[-1][3] == edge:
                runs[-1][1] = w["f"][1]
            else:
                runs.append([w["f"][0], w["f"][1], w.get("ink"), edge])
        runs[0][0], runs[-1][1] = a, b
        merged = []
        for r in runs:
            if merged and (r[1] - r[0]) < 0.2 * fps:      # today's minimum: a sliver joins its neighbour
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        for r in merged:
            s0, s1 = src_at(l1, r[0], fps), src_at(l1, r[1], fps)
            if s0 is None or s1 is None:
                continue
            st = {**style, "color": r[2] or style["color"]}
            if r[3]:
                st.update(edge_style(st["color"]))
                rep["captions_edged"] = rep.get("captions_edged", 0) + 1
            out.append({"text": text.replace("\n", " "), "start": r2(s0), "end": r2(max(s1, s0 + 0.06)), "style": st})
    if src_dur:
        for c in out:
            c["end"] = r2(min(c["end"], src_dur))
        out = [c for c in out if c["end"] - c["start"] >= 0.06]
    rep["captions"] = len(out)
    return out


def edge_style(ink):
    """The edge a word gets when neither polarity reads on the picture under it: a dark outline round
    light type, a light one round dark type, and the shadow at full strength."""
    light = sum(int(str(ink or "#FFFFFF").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) > 3 * 128
    return {"outline_width": EDGE_OUTLINE, "outline_color": "#111111" if light else "#F2F2F2", "shadow": 1.0}


def word_chains(words, voices=None):
    """Display words to windows. The same text in the same place joins; a chain of short steps, a
    counter, keeps every step that can hold today's minimum and always lands on the last. Place
    is the centre: a counter's digits change its width."""
    voices = voices or {}

    def centre(w):
        x, y = w.get("x"), w.get("y")
        if isinstance(x, list) or isinstance(y, list):
            return json.dumps([x, y])
        v = voices.get(w.get("v"))
        return (round(float(x) + (text_w(w["t"], v) / 1080 / 2 if v else 0), 2), round(float(y), 3))
    chains = []
    for w in sorted(words, key=lambda i: (i["f"][0], i["id"])):
        last = chains[-1] if chains else None
        if last and last[-1]["f"][1] == w["f"][0] and last[-1].get("v") == w.get("v") and centre(last[-1]) == centre(w) \
                and w["id"].rsplit(".", 2)[0] == last[-1]["id"].rsplit(".", 2)[0]:
            if last[-1]["t"] == w["t"] and last[-1].get("ink") == w.get("ink") and not any(isinstance(w.get(k), list) for k in ("op", "s")):
                last[-1] = dict(last[-1], f=[last[-1]["f"][0], w["f"][1]])
            else:
                last.append(w)
        else:
            chains.append([w])
    return chains


def text_item(l1, it, a, b, rep):
    fps, W, H = float(l1["fps"]), l1["frame"][0], l1["frame"][1]
    v = l1["voices"].get(it.get("v")) or {}
    px = float(v.get("px", 58))
    w_px = text_w(it["t"], v) if v.get("font") else len(it["t"]) * px * 0.5
    face = face_of(v)
    if not face:
        rep.setdefault("fonts_replaced", []).append(v.get("font"))
        face = ("Inter Display Bold", False)

    def cx(x):
        return (float(x) * W + w_px / 2) / W

    def cy(y):
        return float(y) - 0.36 * px / H
    x0, y0 = it.get("x", 0.5), it.get("y", 0.5)
    tx = {"id": f"tx.{it['id']}"[:60], "text": it["t"], "start": r2(a / fps), "end": r2(b / fps), "template": "title", "font": face[0],
          "font_size": round(min(max(libass_px(v, px) / min(W, H), 0.012), 0.3), 4), "text_align": "center", "max_width": 0.96, "color": it.get("ink") or "#FFFFFF",
          "uppercase": False, "shadow": 1.0 if (v.get("shadow") or {}).get("a") else 0.0, "outline_width": 0.0, "entrance": "none", "exit": "none"}
    if face[1]:
        tx["italic"] = True
    if it.get("edge") and edge_style(tx["color"])["outline_color"] == "#111111":      # today's titles draw a dark outline only
        tx["outline_width"], tx["shadow"] = EDGE_OUTLINE, 1.0
        rep["texts_edged"] = rep.get("texts_edged", 0) + 1
    track = float(v.get("track", 0)) * px / 100.0
    if track:
        tx["tracking"] = round(min(max(track / max(W / 1280, H / 720), -3.0), 12.0), 2)
    mid = lambda q: q[-1] if isinstance(q, list) else q
    tx["x"], tx["y"] = round(min(max(cx(mid(x0)), 0.0), 1.0), 4), round(min(max(cy(mid(y0)), 0.0), 1.0), 4)
    motion = {}
    for name, key, fn in (("x", "x", cx), ("y", "y", cy), ("op", "opacity", float), ("s", "scale", float)):
        val = it.get(name)
        if name == "s" and animated(val) and w_px * max(val) > 0.9 * W:
            rep["settle_dropped_to_fit"] = rep.get("settle_dropped_to_fit", 0) + 1      # graphics.py wraps at the curve's widest
            continue
        if animated(val):
            n = b - a
            motion[key] = curve([fn(q) for q in (list(val) + [val[-1]] * n)[:n]], fps)
    if motion:
        tx["motion"] = motion
    if it.get("behind"):
        rep["behind_drawn_in_front"] = rep.get("behind_drawn_in_front", 0) + 1
    if it.get("blur"):
        rep["blur_in_dropped"] = rep.get("blur_in_dropped", 0) + 1
    return tx


def shown_piece(pieces, runs, mid):
    """A merged run shows its longest shot, and a word's ink was solved shot by shot. Take the ink
    of the piece over the shot today draws."""
    for it, a, b, _ in runs:
        if a <= mid < b:
            m = (it["f"][0] + it["f"][1]) // 2
            for p in pieces:
                if p["f"][0] <= m < p["f"][1]:
                    return p
    return pieces[-1]


def lower_script_type(l1, bake, rep):
    """Display type in a joined script. Today's fonts hold none of it, so it goes in as an overlay still at its solved place."""
    fps, W, H = float(l1["fps"]), l1["frame"][0], l1["frame"][1]
    out = []
    groups = collections.OrderedDict()
    for it in l1["items"]:
        if it.get("k") == "word" and it.get("role") != "cap" and score.joined_script(it["t"]):
            groups.setdefault((it["id"].split(".p")[0]), []).append(it)
    for gid, parts in groups.items():
        a, b = min(p["f"][0] for p in parts), max(p["f"][1] for p in parts)
        it = max(parts, key=lambda p: p["f"][1] - p["f"][0])
        if isinstance(it.get("op"), list) and min(it["op"]) < 0.5:        # a ghost word lands when its ink does
            a = it["f"][0] + next((i for i, v in enumerate(it["op"]) if v >= 0.9), 0)
        v = l1["voices"].get(it["v"]) or {}
        key, wf, cx, cy = bake.type_line(it["t"], v, it.get("ink"), W)
        x = float(it["x"][-1] if isinstance(it["x"], list) else it["x"])
        y = float(it["y"][-1] if isinstance(it["y"], list) else it["y"])
        out.append({"id": f"st.{gid}"[:60], "asset_key": key, "kind": "image", "start": r2(a / fps), "duration_s": r2(max((b - a) / fps, OV_MIN_S)),
                    "x": round((x * W + cx) / W, 4), "y": round((y * H + cy) / H, 4), "scale": round(min(wf, 1.0), 4), "z_index": 4})
    rep["script_type_stills"] = len(out)
    return out


def lower_texts(l1, rep, runs=None):
    fps, dur = float(l1["fps"]), l1["dur_f"]
    n_min = int(math.ceil(TX_MIN_S * fps - 1e-6))
    words = [i for i in l1["items"] if i.get("k") == "word" and i.get("role") != "cap" and not score.joined_script(i["t"])]
    out = []
    for chain in word_chains(words, l1.get("voices")):
        if len(chain) == 1:
            it = chain[0]
            a, b = it["f"]
            if b - a < n_min:
                b = min(a + n_min, dur)
                a = min(a, b - n_min)
                rep["texts_extended"] = rep.get("texts_extended", 0) + 1
            out.append(text_item(l1, it, a, b, rep))
            continue
        # steps too short for today hold together, and the last step always shows
        groups, cur = [], []
        for w in chain:
            cur.append(w)
            if cur[-1]["f"][1] - cur[0]["f"][0] >= n_min:
                groups.append(cur)
                cur = []
        if cur:
            if groups:
                groups[-1] += cur
            else:
                groups.append(cur)
        rep["counter_steps_merged"] = rep.get("counter_steps_merged", 0) + len(chain) - len(groups)
        for g in groups:
            a, b = g[0]["f"][0], g[-1]["f"][1]
            if b - a < n_min:
                b = min(a + n_min, dur)
                a = min(a, b - n_min)
            top = shown_piece(g, runs, (a + b) // 2) if runs else g[-1]
            out.append(text_item(l1, dict(g[-1], f=[a, b], op=None, s=None, ink=top.get("ink"), edge=top.get("edge")), a, b, rep))
    return no_double_type(out, rep)


def _x_overlap(a, b):
    """Share of the narrower word's width the two cover together, from an advance of 0.55 em a letter."""
    def span(q):
        w = min(len(q["text"]) * 0.55 * float(q["font_size"]), 0.96)
        return float(q["x"]) - w / 2, float(q["x"]) + w / 2
    (a0, a1), (b0, b1) = span(a), span(b)
    return max(0.0, min(a1, b1) - max(a0, b0)) / max(min(a1 - a0, b1 - b0), 1e-6)


def _clip_motion(tx):
    """Keyframes are local to the item: a shortened item keeps only the ones inside it."""
    n = tx["end"] - tx["start"]
    for k, kfs in list((tx.get("motion") or {}).items()):
        inside = [q for q in kfs if q["t"] <= n + 1e-6]
        tx["motion"][k] = inside or kfs[:1]


def no_double_type(texts, rep):
    """One gesture never draws two of its words on top of each other. A counter's steps move as they
    step, so each is its own chain, and stretched to today's 0.3 s each ran into the next: DIA 23 and
    DIA 24 drawn over one another (a p5302 replay). An earlier step ends where the next one starts on
    the same spot, and a step left under the minimum goes; the count always lands on its last."""
    groups = collections.defaultdict(list)
    for tx in texts:
        groups[".".join(tx["id"].split(".")[1:3])].append(tx)
    drop = set()
    for items in groups.values():
        items.sort(key=lambda q: (q["start"], q["id"]))
        for i, tx in enumerate(items[:-1]):
            for nxt in items[i + 1:]:
                if nxt["start"] >= tx["end"]:
                    break
                fs = max(float(tx["font_size"]), float(nxt["font_size"]))
                if abs(float(tx["y"]) - float(nxt["y"])) < 0.8 * fs and _x_overlap(tx, nxt) > 0.2:
                    tx["end"] = nxt["start"]
                    if tx["end"] - tx["start"] < TX_MIN_S - 1e-6:
                        drop.add(tx["id"])
                    else:
                        _clip_motion(tx)
                    break
    if drop:
        rep["type_steps_dropped"] = rep.get("type_steps_dropped", 0) + len(drop)
    return [tx for tx in texts if tx["id"] not in drop]


# ------------------------------------------------------------------ the film

def lower(l1, work):
    """Returns edl, asset_locals, report. Bakes stills and voice sounds into work/today."""
    rep = {"project": "shot", "sid": l1.get("sid")}
    bake = Bake(work)
    fps = float(l1["fps"])
    keep, inserts = lower_spine(l1, bake, rep)
    overlays, zooms, tr_fx, runs = lower_pictures(l1, work, bake, rep)
    flash_fx, vectors, pool_ov = lower_flashes(l1, bake, rep)
    cards = lower_cards(l1, work, bake, rep)
    grade = (l1.get("film") or {}).get("grade") or {}
    gc = {}
    if abs(float(grade.get("contrast", 1)) - 1) > 1e-3:
        gc["contrast"] = round(float(grade["contrast"]), 3)
    if abs(float(grade.get("sat", 1)) - 1) > 1e-3:
        gc["saturation"] = round(float(grade["sat"]), 3)
    if abs(float(grade.get("temp", 0))) > 1e-4:
        gc["temperature"] = round(min(max(float(grade["temp"]) / 0.10, -1), 1), 3)
    if abs(float(grade.get("gain", 1)) - 1) > 1e-3 or abs(float(grade.get("lift", 0))) > 1e-3:
        gc["exposure"] = round(min(max((float(grade.get("gain", 1)) - 1 + float(grade.get("lift", 0))) / 0.35, -1), 1), 3)
    film = l1.get("film") or {}
    stylize = []
    if film.get("grain"):             # noise alls = 5 + 25 i, so the engine's sigma in 8 bits
        stylize.append({"id": "film.grain", "kind": "grain", "intensity": round(min(max((float(film["grain"]) * 255 - 5) / 25, 0.0), 1.0), 3)})
    # today's vignette starts at PI/4.8 and its halation tints every pale wall red. Under these
    # strengths they do more than the film asks, so they stay out and the report says so
    if float(film.get("vignette") or 0) >= 0.3:
        stylize.append({"id": "film.vig", "kind": "vignette", "intensity": round(min((float(film["vignette"]) - 0.3) * 1.5, 1.0), 3)})
    elif film.get("vignette"):
        rep.setdefault("look_dropped", []).append("vignette")
    if film.get("halation"):       # today's halation turned p5297's whole first second purple, and only under the base: overlays sit above it
        rep.setdefault("look_dropped", []).append("halation")
    if film.get("bloom"):
        rep.setdefault("look_dropped", []).append("bloom")
    sfx = []
    dry = None
    for s in l1.get("sound") or []:
        if s.get("s") in ("echo", "swell"):      # made from the film's own audio: baked once, placed like any sound
            if dry is None:
                dry = shape.dry_spine(l1, os.path.join(work, "source.mp4"))
            at, smp = shape.voice_layer(dry, s, fps)
            key, p = bake.path("voice", s["id"], s["s"], s["event_f"], s["db"])
            p = p[:-4] + ".wav"
            key = key[:-4] + ".wav"
            with wave.open(p, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(shape.SR)
                w.writeframes((np.clip(smp, -1, 1) * 32767).astype(np.int16).tobytes())
            bake.done(key, p)
            sfx.append({"id": s["id"], "storage_key": key, "at": r2(max(at, 0) / shape.SR), "gain_db": 0.0, "purpose": s["s"]})
            continue
        key = f"sound:{s['s']}" if s.get("s") else f"sound:{os.path.splitext(s['file'])[0]}"
        item = {"id": s["id"], "storage_key": key, "at": r2(s["start_s"]), "gain_db": float(s.get("db", -6)), "purpose": s.get("s")}
        if s.get("trim_s"):
            item["offset_s"] = r2(s["trim_s"])
        sfx.append(item)
    # Source time never passes the source: the air after a last word can reach past its end, and
    # validate_edl refuses the whole film for a caption 0.02 s over it.
    src_dur = float(((l1.get("assets") or {}).get("main") or {}).get("dur") or 0)
    if src_dur:
        keep = [[a, r2(min(b, src_dur))] for a, b in keep if min(b, src_dur) - a > 0.04]
    prog = sum(k[1] - k[0] for k in keep) + sum(float(i.get("duration_s") or 0) for i in inserts)
    # Every overlay lane, joined-script type included: a Persian word on the last frame, held to today's
    # 0.2 s minimum, outran the programme by 0.01 s and validate_edl refused the whole film (p5374).
    script_type = lower_script_type(l1, bake, rep)
    for ov in overlays + pool_ov + cards + script_type:      # keep spans round to 0.01: the last overlay must not outrun the programme by a rounding
        if isinstance(ov.get("duration_s"), (int, float)) and float(ov["start"]) + float(ov["duration_s"]) > prog:
            room = math.floor((prog - float(ov["start"])) * 100) / 100
            if room < OV_MIN_S:                # too near the end for today's minimum: it starts earlier, it never runs past
                ov["start"] = max(0.0, math.floor((prog - OV_MIN_S) * 100) / 100)
                room = math.floor((prog - float(ov["start"])) * 100) / 100
            ov["duration_s"] = max(OV_MIN_S, room)
    # Programme time never passes the programme: the source clamp above can end it a frame early.
    lim = math.floor(prog * 100) / 100
    zooms = [dict(z, end=min(z["end"], lim)) for z in zooms if min(z["end"], lim) - z["start"] >= 0.05]
    effects = {"zooms": zooms}
    if gc:
        effects["grade_custom"] = gc
    if stylize:
        effects["stylize"] = stylize
    edl = {"keep": keep, "frame": dict(rep["frame"]),
           "inserts": inserts, "captions": lower_captions(l1, rep, src_dur), "sfx": sfx, "overlays": overlays + pool_ov + cards + script_type,
           "texts": lower_texts(l1, rep, runs), "vectors": vectors, "fx": tr_fx + flash_fx, "effects": effects, "master": {"loudness": "social"}}
    for lane, least in (("texts", TX_MIN_S), ("vectors", TX_MIN_S), ("fx", 1 / fps)):
        kept = []
        for it in edl[lane]:
            if it["end"] > lim:
                it["end"] = lim
                it["start"] = min(it["start"], r2(lim - least))
            if it["end"] - it["start"] >= least - 1e-6 and it["start"] >= 0:
                kept.append(it)
        edl[lane] = kept
    edl["sfx"] = [s for s in edl["sfx"] if float(s["at"]) <= lim - 0.05]       # validate_edl: a sound within 0.05 s of the end renders nothing
    if "flash_modes_dropped" in rep:
        rep["flash_modes_dropped"] = dict(rep["flash_modes_dropped"])
    rep["counts"] = {k: len(edl[k]) for k in ("keep", "inserts", "overlays", "texts", "vectors", "fx", "sfx", "captions")}
    rep["counts"]["zooms"] = len(zooms)
    rep["locals"] = {k: os.path.relpath(v, work) if v.startswith(os.path.abspath(work)) else v for k, v in bake.locals.items()}
    return edl, dict(bake.locals), rep
