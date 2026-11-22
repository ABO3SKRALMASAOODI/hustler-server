"""Type in depth: a word or a lockup that stands behind the speaker. The engine already draws the depth sandwich
(a word with ``behind: true`` is drawn, then the person again through his matte) on any frame whose base shot is the
take and whose scene carries a ``matte`` asset. What this module adds is the matte when the scene has none, made now
on the take's own frames (RVM, signal: who is a person, never what anything means), and the seat: the head's own
box, so the letters pass behind it.

The type stays screen-locked while the take pushes in under it, as the references set it.
"""
import json
import os
import subprocess

import numpy as np

from . import kit as K
from ..alien import sense as asense

MATTE = os.path.join("palette", "matte.mp4")
RVM_IN = (1280, 720)          # the frame RVM reads (16:9 takes; others keep their shape at this width)
RVM_DS = 0.375                # its internal size: about 480 px wide, enough for a person who fills a podcast frame


def take_like(l1, sh):
    """Is this base shot the take (or a cut of the very file the take is)? A shot of ``own:N`` that points at the
    take's own file is the take: it can be drawn as ``main`` without a pixel changing."""
    if not sh or not sh.get("src"):
        return False
    pic = str(sh.get("pic"))
    if pic == "main" or pic.startswith("own:cut"):
        return True
    assets = l1.get("assets") or {}
    return bool(assets.get("main")) and (assets.get(pic) or {}).get("path") == assets["main"].get("path")


def take_shots(l1, a, z):
    return [sh for sh in l1.get("items") or [] if sh.get("k") == "shot" and sh["f"][0] < z and sh["f"][1] > a and take_like(l1, sh)]


def _rvm(path, ranges, n_total, fps, out_path, size):
    """Write a matte video of the take: RVM's alpha on the frames in ``ranges`` (with a short warm-up before each),
    black elsewhere, one frame for each frame of the take."""
    import onnxruntime as ort
    model = K.model_path("rvm_mobilenetv3_fp32.onnx", "MATTE_RVM_MODEL")
    if not model:
        raise RuntimeError("no RVM model here")
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(2, min(8, (os.cpu_count() or 4) - 1))
    sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
    w, h = size
    need = np.zeros(n_total + 1, bool)
    warm = np.zeros(n_total + 1, bool)
    for a, b in ranges:
        need[max(a, 0):min(b, n_total)] = True
        warm[max(a - 8, 0):min(b, n_total)] = True
    last = int(np.nonzero(need)[0].max()) + 1 if need.any() else 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wr = subprocess.Popen([asense.ff(), "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{fps}",
                           "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-pix_fmt", "yuv420p", out_path + ".part.mp4"],
                          stdin=subprocess.PIPE)
    ds = np.array([RVM_DS], np.float32)
    rec, zero = None, np.zeros((h, w), np.uint8)
    written = 0
    try:
        # the matte runs to the last frame a word stands behind: the engine reads it only on those frames
        for i, fr in enumerate(asense.frames(path, 0.0, last + 2, fps, (w, h))):
            if i >= max(last, 1):
                break
            if not warm[i]:
                rec = None
                wr.stdin.write(zero.tobytes())
                written += 1
                continue
            if rec is None:
                rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4
            x = (fr.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
            _, pha, *rec = sess.run(None, {"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": ds})
            m = np.clip(pha[0, 0] * 255.0, 0, 255).astype(np.uint8) if need[i] else zero
            wr.stdin.write(np.ascontiguousarray(m).tobytes())
            written += 1
        for _ in range(max(last - written, 0) + 2):
            wr.stdin.write(zero.tobytes())
    finally:
        wr.stdin.close()
        wr.wait()
    if wr.returncode:
        raise RuntimeError("the matte could not be written")
    os.replace(out_path + ".part.mp4", out_path)


def ensure_matte(l1, work, a, z):
    """(assets patch, log): the scene's ``matte`` for the take's frames under [a, z), made now when the scene has
    none or has one that does not cover them. The matte lies on the take: same file, clock and geometry."""
    assets = l1.get("assets") or {}
    main = asense.path_of(l1, work, "main")
    if not main or not os.path.exists(main):
        return None, "no take to matte"
    if "matte" in assets and assets["matte"].get("path") != MATTE:
        return {}, "the scene's own matte"
    info = asense.probe(main)
    fps = float(info.get("fps") or l1.get("fps") or 30)
    n_total = int(round(float(info.get("dur") or 0) * fps))
    ranges = []
    for sh in take_shots(l1, a, z):
        lo, hi = max(sh["f"][0], a), min(sh["f"][1], z)
        t0, t1 = asense.src_time(sh, lo, l1.get("fps") or 30), asense.src_time(sh, hi, l1.get("fps") or 30)
        ranges.append((int(t0 * fps) - 2, int(t1 * fps) + 3))
    if not ranges:
        return None, "no take under the word"
    out = os.path.join(work, MATTE)
    side = out + ".json"
    have = []
    if os.path.exists(out) and os.path.exists(side):
        try:
            have = [tuple(r) for r in json.load(open(side))["ranges"]]
        except (OSError, ValueError, KeyError):
            have = []
    covered = all(any(h0 <= r0 and r1 <= h1 for h0, h1 in have) for r0, r1 in ranges)
    if not covered:
        want = sorted(set(have) | set(ranges))
        w = RVM_IN[0]
        h = int(round(w * info["h"] / max(info["w"], 1) / 2)) * 2
        _rvm(main, want, n_total, fps, out, (w, h))
        json.dump({"ranges": [list(r) for r in want], "fps": fps}, open(side, "w"))
    return {"matte": {"kind": "video", "path": MATTE}}, f"matte on {len(ranges)} stretch(es)"


def stand_behind(l1, work, a, z, ask=None):
    """What a word needs to stand behind the speaker over [a, z): the matte, and the take's shots named as the take.
    {"ok", "items", "drop", "assets", "log"}; ok False (and why) when there is no take under the word."""
    shots = take_shots(l1, a, z)
    if not shots:
        return {"ok": False, "log": "no take under the word"}
    try:
        assets, why = ensure_matte(l1, work, a, z)
    except Exception as e:      # noqa: BLE001 - no matte: the word stands in front instead
        return {"ok": False, "log": f"matte: {type(e).__name__}: {e}"}
    if assets is None:
        return {"ok": False, "log": why}
    items = []
    for sh in shots:
        if str(sh.get("pic")) != "main" and not str(sh.get("pic")).startswith("own:cut"):
            items.append(dict(sh, pic="main"))          # the same file: drawn as the take, so the matte registers
    return {"ok": True, "items": items, "assets": assets, "log": why}


def matte_frames(l1, work, assets, a, z, n=6, size=(135, 240)):
    """{programme frame: the person's matte on it, float 0..1 at ``size``}: the take's matte through each take shot's
    own crop and clock, on ``n`` frames of [a, z). {} when it cannot be read."""
    l1m = dict(l1, assets=dict(l1.get("assets") or {}, **(assets or {})))
    if "matte" not in l1m["assets"]:
        return {}
    on = dict(l1m, items=[dict(sh, pic="matte") for sh in take_shots(l1m, a, z)])
    out = {}
    for f in sorted(set(np.linspace(a, max(a, z - 1), n).astype(int).tolist())):
        try:
            got = asense.shot_frames(on, work, f, f + 1, size=size, gray=True)
        except Exception:      # noqa: BLE001 - a frame that cannot be read is not measured
            continue
        if got.get(f) is not None:
            out[f] = got[f].astype(np.float32) / 255.0
    return out


def _sat(m):
    """The summed-area table of a matte: the mean of any box in four reads."""
    return np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def _cover(sat, box):
    """Mean matte (0..1) inside a frame-fraction box."""
    h, w = sat.shape[0] - 1, sat.shape[1] - 1
    x0, x1 = int(np.clip(round(box[0] * w), 0, w)), int(np.clip(round(box[2] * w), 0, w))
    y0, y1 = int(np.clip(round(box[1] * h), 0, h)), int(np.clip(round(box[3] * h), 0, h))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(sat[y1, x1] - sat[y0, x1] - sat[y1, x0] + sat[y0, x0]) / ((x1 - x0) * (y1 - y0))


SEEN = 0.70        # each letter keeps at least this share of itself in view on its worst frame
FRONT = 0.5        # under this on the best seat behind the head, a letter would be lost: the words stand in front
DEPTH = 0.16       # the head over the lower part of the letters: the share of the word it is good for it to cover


def seat_behind(v0, t, mattes, W, H, top, bottom, base_ref, small=None, high=0.105):
    """(voice, pen x, baseline, measure) for a word set behind the speaker, chosen on the matte itself: every letter
    mostly in view on every measured frame (the head passes over the lower part of the letters, never across a whole
    glyph), as large, as central and as near ``base_ref`` as that allows. Its ink stays under ``top`` where it meets the
    watermark's columns (under ``high`` elsewhere) and its baseline over ``bottom``. ``small``: (voice, text, gap) of
    the line set over it, kept in view too. Always gives a seat: the best measured."""
    sats = [_sat(m) for m in mattes]
    best = None
    for sc in (1.0, 0.88, 0.76, 0.66, 0.57, 0.49):
        v = dict(v0, px=round(v0["px"] * sc, 2))
        m = K.metrics(v, t)
        gl = m["glyphs"] or [m["ink"]]
        _, b = K.ink_box(v, t)
        bw, bh = (b[2] - b[0]) / W, (b[3] - b[1]) / H
        sh = 0.0
        if small:
            _, sb = K.ink_box(small[0], small[1])
            sh = (sb[3] - sb[1]) / H + small[2]
        for dx in np.arange(-0.30, 0.3001, 0.05):
            cx = 0.5 + float(dx)
            if cx - bw / 2 < 0.025 or cx + bw / 2 > 0.975:
                continue
            roof = top if cx + bw / 2 > K.MARK[0] and cx - bw / 2 < K.MARK[2] else high
            lo_b = roof + bh + sh
            for base in np.arange(lo_b, max(bottom, lo_b) + 1e-6, 0.012):
                x, y = K.place(v, t, cx, float(base), W, H)
                boxes = [[x + g[0] / W, y + g[1] / H, x + g[2] / W, y + g[3] / H] for g in gl]
                if small:
                    vs, st, gap = small
                    sx, sy = K.place(vs, st, cx, float(base) - bh - gap, W, H)
                    boxes.append(K.drawn(vs, st, sx, sy, W, H))
                seen = [min(1.0 - _cover(sa, bx) for sa in sats) for bx in boxes]
                hid = float(np.mean([np.mean([_cover(sa, bx) for sa in sats]) for bx in boxes]))
                short = sum(max(0.0, SEEN - q) for q in seen)
                score = -10.0 * short + 1.2 * min(hid, DEPTH) - 0.6 * (1.0 - sc) - 0.8 * abs(float(base) - base_ref) - 0.4 * abs(dx)
                if best is None or score > best[0]:
                    best = (score, v, x, float(base), {"scale": sc, "dx": round(float(dx), 2), "seen_min": round(min(seen), 3), "hidden": round(hid, 3)})
    _, v, x, base, meas = best
    return v, x, base, meas


def behind(l1, work, ask, sense=None):
    """A word (and a small line over it) set huge behind the speaker's head: the head passes in front of the letters.
    Its seat is measured on the matte over the hold: every letter stays mostly in view and the head covers the lower
    part of the word, the way the references set it."""
    from .type import cased, text_of, vname, _marks
    o = K.opts(ask)
    W, H, fps, dur = K.size(l1)
    K.scaled(l1)
    t = cased(text_of(o), o, "upper")
    if not t:
        return {"skip": "behind needs a word"}
    a, z, hit = K.span(l1, ask, need=int(o.get("hold", 45)), text=t)
    land = hit[0][1] if hit else a
    got = stand_behind(l1, work, land, z, ask)
    if not got.get("ok"):
        return {"skip": f"behind: {got.get('log')}"}
    S = K.Sense(l1, work, sense)
    pal = K.palette_of(l1, o)
    font = pal["font"]      # palette_of resolves the ask's font word
    v = K.fit(K.voice(font, 100, track=-2, shadow=0.0), t, float(o.get("width", 0.94)) * W, cap_px=float(o.get("cap", 0.24)) * H)
    _, b = K.ink_box(v, t)
    bh = (b[3] - b[1]) / H
    faces = S.faces_over(land, z)
    if faces:
        f = max(faces, key=lambda q: (q[2] - q[0]) * (q[3] - q[1]))
        base_ref = f[1] + float(o.get("depth", 0.12)) * (f[3] - f[1])
    else:
        base_ref = 0.36 + bh / 2
    small = cased(" ".join(str(o.get("small") or "").split()), o, "upper")
    vs = K.mono(max(K.FLOOR_PX + 4, min(48.0, v["px"] * 0.2 / K.SCALE))) if small else None
    top = K.MARK[3] + 0.015
    bottom = K.caption_top(l1, land, z) - 0.03
    mattes = list(matte_frames(l1, work, got.get("assets"), land, z).values())
    meas = None
    if mattes:
        v, x, base, meas = seat_behind(v, t, mattes, W, H, top, bottom, base_ref, small=(vs, small, 0.018) if small else None)
        if meas["seen_min"] < FRONT:
            # a framing with no room behind the head for every letter (a close-up: the head fills the width, the body
            # the rest): the same words land in front, off his face, on the same word, so the payoff still reads
            from .type import verb
            words = " ".join(w for w in (small, t) if w)
            got2 = verb(l1, work, dict(ask, dev="verb", args=dict(o, t=words, verb=o.get("verb") or "punch")), sense)
            if not got2.get("skip"):
                got2["log"] = dict(got2.get("log") or {}, front="no seat behind this framing keeps every letter in view", measured=meas)
                return got2
        _, b = K.ink_box(v, t)
        bh = (b[3] - b[1]) / H
        cx = x + (b[0] + b[2]) / 2 / W
    else:                                                # no matte to read: the head's own box
        base = max(base_ref, top + bh + ((K.ink_box(vs, small)[1][3] - K.ink_box(vs, small)[1][1]) / H + 0.018 if small else 0.0))
        cx = 0.5
        x, _ = K.place(v, t, cx, base, W, H)
    y = base
    tone, _ = S.ground([0.03, base - bh, 0.97, base], land, z)
    ink = o.get("ink") or (K.DARK if tone > 0.62 else (pal["accent"] if o.get("accent") else pal["ink"]))
    n = z - land
    vn = vname(ask, "bh")
    it = K.word(f"{ask['id']}.bh", (land, z), t, vn, round(x, 6), [round(y + d, 6) for d in K.ease_out(n, 0.028, 0.0, frames=7)], ink, z=4,
                role="graphic", on=land, behind=True,
                op=[round(min(1.0, (q + 1) / 3.0) * (1.0 if q < n - 4 else max(0.0, (n - q - 1) / 4.0)), 3) for q in range(n)],
                s=[round(1.0 + 0.04 * max(0.0, 1 - q / 8.0), 5) for q in range(n)])
    items = [it] + list(got.get("items") or [])
    voices = {vn: v}
    if small:
        sx, sy = K.place(vs, small, cx, base - bh - 0.018, W, H)
        voices[vn + "s"] = vs
        items.append(K.word(f"{ask['id']}.bs", (land, z), small, vn + "s", sx, sy, pal["accent"] if tone < 0.55 else K.DARK, z=4, role="graphic", on=land,
                            behind=True, op=it["op"]))
    return {"items": items, "assets": got.get("assets") or {}, "voices": voices, "marks": _marks(land, "word behind", 0.8),
            "seat": {"x": cx, "y": base, "w": (b[2] - b[0]) / W, "h": bh, "f": [land, z]},
            "log": {"t": t, "matte": got.get("log"), "faces": len(faces), "base": round(base, 4), "measured": meas}}


DEVICES = {
    "behind": {"plan": behind, "layer": 3, "needs": ("take", "matte"), "fallback": ("marquee", "verb"),
               "card": 'behind at until t small ink depth. A word set huge behind the speaker: his head passes in front of the letters (the matte is made when the film has none)',
               "amplifies": "the theme or the name the whole moment is about, when the speaker is on screen"},
}
