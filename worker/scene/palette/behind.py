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


def behind(l1, work, ask, sense=None):
    """A word (and a small line over it) set huge behind the speaker's head: the head passes in front of the letters."""
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
    font = o.get("font") or pal["font"]
    v = K.fit(K.voice(font, 100, track=-2, shadow=0.0), t, float(o.get("width", 0.94)) * W, cap_px=float(o.get("cap", 0.24)) * H)
    _, b = K.ink_box(v, t)
    bh = (b[3] - b[1]) / H
    faces = S.faces_over(land, z)
    if faces:
        f = max(faces, key=lambda q: (q[2] - q[0]) * (q[3] - q[1]))
        base = f[1] + float(o.get("depth", 0.12)) * (f[3] - f[1])
    else:
        base = 0.36 + bh / 2
    base = max(base, K.MARK[3] + 0.015 + bh)
    small = cased(" ".join(str(o.get("small") or "").split()), o, "upper")
    x, y = K.place(v, t, 0.5, base, W, H)
    tone, _ = S.ground([0.03, base - bh, 0.97, base], land, z)
    ink = o.get("ink") or (K.DARK if tone > 0.62 else (pal["accent"] if o.get("accent") else pal["ink"]))
    n = z - land
    vn = vname(ask, "bh")
    it = K.word(f"{ask['id']}.bh", (land, z), t, vn, x, [round(y + d, 6) for d in K.ease_out(n, 0.028, 0.0, frames=7)], ink, z=4,
                role="graphic", on=land, behind=True,
                op=[round(min(1.0, (q + 1) / 3.0) * (1.0 if q < n - 4 else max(0.0, (n - q - 1) / 4.0)), 3) for q in range(n)],
                s=[round(1.0 + 0.04 * max(0.0, 1 - q / 8.0), 5) for q in range(n)])
    items = [it] + list(got.get("items") or [])
    voices = {vn: v}
    if small:
        vs = K.mono(max(K.FLOOR_PX + 4, min(48.0, v["px"] * 0.2 / K.SCALE)))
        sx, sy = K.place(vs, small, 0.5, base - bh - 0.018, W, H)
        voices[vn + "s"] = vs
        items.append(K.word(f"{ask['id']}.bs", (land, z), small, vn + "s", sx, sy, pal["accent"] if tone < 0.55 else K.DARK, z=4, role="graphic", on=land,
                            behind=True, op=it["op"]))
    return {"items": items, "assets": got.get("assets") or {}, "voices": voices, "marks": _marks(land, "word behind", 0.8),
            "seat": {"x": 0.5, "y": base, "w": (b[2] - b[0]) / W, "h": bh, "f": [land, z]},
            "log": {"t": t, "matte": got.get("log"), "faces": len(faces), "base": round(base, 4)}}


DEVICES = {
    "behind": {"plan": behind, "layer": 3, "needs": ("take", "matte"), "fallback": ("marquee", "verb"),
               "card": 'behind at until t small ink depth. A word set huge behind the speaker: his head passes in front of the letters (the matte is made when the film has none)',
               "amplifies": "the theme or the name the whole moment is about, when the speaker is on screen"},
}
