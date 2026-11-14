"""The pacing driver: when the picture changes and how every framing moves, led by the stress of the voice.

The owner, 13 Nov 2026: "things staying constant a lot of time is bad"; the cringe was bad execution, not
speed. In the references a short shows a new picture or framing every 0.5 s at the median and holds one past
2.3 s in one case in ten (week-1112/notes/VIS.md). This module gives a film that pulse from whatever pictures
it has, and executes it the way the references do:

  * a change lands on a stress peak of the voice (``stress``: the speech band's own syllable peaks, by signal),
    never on a clock;
  * a new framing is a different picture of the same source (a face, a detail, the whole), never the framing
    just left (no A-B-A), and a head is never cut by the frame;
  * a framing is never still: it pushes at one speed (the references' slow pushes are linear), or lands off
    its size and settles, or eases to a tighter framing on the strongest word, with the measured curves;
  * about one cut in eleven is hidden in a whip: the outgoing picture runs off along a line, the incoming
    one arrives along the same line and brakes; a face cut to a face keeps the eyes where they were (the
    references match 40% of face-to-face cuts);
  * whatever moves is blurred by the shutter, by exactly how far it moved (runtime, ``film.shutter``).

It reads no meaning and no model. Faces and regions are handed in (the callers have them); ``regions_of``
finds a still's busiest areas from its gradients when nobody did.

    stress = pace.stress(audio_path, fps)                         # [(frame, strength 0..1)]
    items, log = pace.drive(shots, stress, fps, (W, H), family="story", seed=sid_hash)
    l1 = pace.drive_l1(l1, work, stress, meta={asset: {"faces": [...], "regions": [...]}})
"""
import math
import os
import random
import subprocess

import numpy as np

from . import bake, looks, motion

# Seconds a framing may hold, by family: (the least, the most the driver aims for, never past). From the
# references' own holds (VIS.md "pace": median hold, p75, p90 of every film of the family).
BANDS = {
    # WF3 (26 films: a change every 0.54 s at the median, 0.9 at p75, 1.47 at p90, never past 3.6) and the owner's picks
    "story": {"hold": (0.55, 1.6), "never": 3.0, "whip": 0.05, "ease": 0.85, "shutter": 0.6},
    # WF1: a take with cut-ins. The references never punch a take with a cut (0.0 hard punches per 10 s); they ease
    # it (4.2 eased moves per 10 s, 4.3% each, 14 frames) and it is moving three frames in four.
    "podcast": {"hold": (0.9, 2.6), "never": 3.5, "whip": 0.05, "ease": 0.8, "shutter": 0.5, "ease_takes": True},
    "promo": {"hold": (0.5, 2.0), "never": 4.0, "whip": 0.04, "ease": 0.85, "shutter": 0.6},      # WF2 (15): 0.7 s at the median, 2.1 at p75, 4.0 at p90
    "long": {"hold": (2.0, 7.0), "never": 12.0, "whip": 0.0, "ease": 0.9, "shutter": 0.5},        # WF4 (7 films): 7.4 s at the median, eased 0.7 times per 10 s
}
# A whip's smear, as a share of what the picture travels in the frame: read off the references' own whip frames
# (the dip in the autocorrelation of the derivative along the travel, 100 frames: quartiles 0.06, 0.26, 0.62, p90
# 0.84). Sliding graphics sit at the bottom, sharp; pictures in a whip at the top.
WHIP_SHUTTER = 0.85
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")


# ----------------------------------------------------------------- the voice's stress, by signal

def stress_env(x, sr, fps):
    """Stress peaks of a mono signal already limited to the speech band: [(frame, strength)].
    A peak is a syllable's nucleus standing 6 dB or more over the floor a quarter second either side; its
    strength is where its prominence ranks among the peaks of the four seconds round it (0..1)."""
    x = np.asarray(x, np.float32)
    hop = int(sr // 100)
    n = len(x) // hop
    if n < 60:
        return []
    e = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-10)
    k = np.hanning(7)
    db = 20 * np.log10(np.convolve(e, k / k.sum(), "same") + 1e-6)
    w, pk = 25, []
    for i in range(w, n - w):
        if db[i] >= db[i - 6:i + 7].max() - 1e-9 and db[i] > -45:
            p = db[i] - min(db[i - w:i].min(), db[i:i + w].min())
            if p >= 6:
                if pk and (i - pk[-1][0]) < 16:
                    if p > pk[-1][1]:
                        pk[-1] = (i, float(p))
                else:
                    pk.append((i, float(p)))
    out = []
    for i, p in pk:
        near = [q for j, q in pk if abs(j - i) <= 200]
        out.append((int(round(i / 100.0 * fps)), round(float(sum(1 for q in near if q <= p) / len(near)), 3)))
    return out


def stress(path, fps=30.0, t0=0.0, dur=None):
    """Stress peaks of the voice in an audio or video file. ffmpeg band-limits it; nothing listens."""
    sr = 16000
    cmd = [FF, "-v", "error", "-ss", f"{t0:.3f}", "-i", path] + (["-t", f"{dur:.3f}"] if dur else []) + \
          ["-vn", "-ac", "1", "-ar", str(sr), "-af", "highpass=f=300,lowpass=f=3400", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return stress_env(np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0, sr, fps)


def stress_of_words(words, fps=30.0):
    """Stress from word timings alone ([{"t0", "t1"}] or [(t0, t1)]): a word is stressed by how long it is held
    against its neighbours and by the pause that follows it. For a script whose audio is not at hand."""
    ws = [(w["t0"], w["t1"]) if isinstance(w, dict) else (w[0], w[1]) for w in words]
    out = []
    for i, (a, b) in enumerate(ws):
        near = [y - x for x, y in ws[max(i - 4, 0):i + 5]]
        d = (b - a) / max(float(np.median(near)), 1e-3)
        gap = (ws[i + 1][0] - b) if i + 1 < len(ws) else 0.4
        out.append((int(round(a * fps)), round(min(max(0.35 * d + min(gap, 0.4) * 1.2, 0.0), 1.0), 3)))
    return out


# ----------------------------------------------------------------- a still's busy areas, by signal

def regions_of(arr, n=3, size=0.5):
    """Up to n boxes [x0, y0, x1, y1] (picture fractions) holding the most detail: gradient energy summed over
    windows ``size`` of the short side, strongest first, no two overlapping by more than a third."""
    a = np.asarray(arr)
    if a.ndim == 3:
        a = a[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    a = a.astype(np.float32)
    st = max(int(max(a.shape) / 160), 1)
    a = a[::st, ::st]
    h, w = a.shape
    g = np.zeros_like(a)
    g[1:-1, 1:-1] = np.abs(a[1:-1, 2:] - a[1:-1, :-2]) + np.abs(a[2:, 1:-1] - a[:-2, 1:-1])
    ii = np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    k = max(int(min(h, w) * size), 4)
    e = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    out = []
    e = e.copy()
    for _ in range(n):
        if e.size == 0 or e.max() <= 0:
            break
        y, x = np.unravel_index(int(np.argmax(e)), e.shape)
        out.append([x / w, y / h, (x + k) / w, (y + k) / h])
        r = int(k * 0.67)
        e[max(y - r, 0):y + r + 1, max(x - r, 0):x + r + 1] = 0
    return out


# ----------------------------------------------------------------- framings

def distinct(a, b):
    """Are two framings two pictures? Yes when one is a clear step tighter (12% or more: past the references'
    upper punch) or sits a quarter of a frame away. A sideways nudge of the same size is a jitter, not a framing."""
    wa, wb = a[2] - a[0], b[2] - b[0]
    ha, hb = a[3] - a[1], b[3] - b[1]
    if abs(math.log(wa / wb)) >= math.log(1.12):
        return True
    dx = abs((a[0] + a[2]) - (b[0] + b[2])) / (wa + wb)
    dy = abs((a[1] + a[3]) - (b[1] + b[3])) / (ha + hb)
    return dx >= 0.25 or dy >= 0.25


def _iou(a, b):
    ix = max(min(a[2], b[2]) - max(a[0], b[0]), 0.0)
    iy = max(min(a[3], b[3]) - max(a[1], b[1]), 0.0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def menu(cam, shot, out_w):
    """The framings one picture offers a frame: [(name, crop)], the whole first. Every one keeps the heads it
    shows whole and stays under the upscale limit; two that are nearly the same picture are one."""
    faces = sorted([f[:4] for f in shot.get("faces") or []], key=lambda f: -f[2] * f[3])
    eyes = {tuple(f[:4]): f[4] for f in shot.get("faces") or [] if len(f) > 4}
    big = faces[0] if faces else None
    focus = shot.get("focus") or ((big[0] + big[2] / 2, big[1] + big[3] * 0.45) if big else (0.5, 0.45))
    out = [("wide", cam.keep_whole(cam.cover(focus=focus, at=(0.5, 0.42)), faces))]

    def add(name, c):
        c = cam.keep_whole(c, faces)
        if cam.up(c, out_w) <= cam.max_up * 1.001 and all(distinct(c, q) for _, q in out):
            out.append((name, c))
    for i, f in enumerate(faces[:3]):
        if f[3] * cam.ph < 40:               # a face too small to stand a close framing
            continue
        add(f"face{i}", cam.on_face(f, motion.FACE_H[1], eyes=eyes.get(tuple(f))))
        add(f"close{i}", cam.on_face(f, motion.FACE_H[2], eyes=eyes.get(tuple(f))))
        if i == 0:
            # the same head set off centre, with the room on the side it sits away from: a second camera's framing
            add("side0", cam.on_face(f, (motion.FACE_H[0] + motion.FACE_H[1]) / 2, eyes=eyes.get(tuple(f)),
                                     x_at=0.39 if f[0] + f[2] / 2 < 0.5 else 0.61))
    for i, r in enumerate((shot.get("regions") or [])[:3]):
        add(f"detail{i}", cam.on_region(r))
    # a ladder of sizes on the subject, as far as the picture's own pixels go
    for name, k in (("step1", 1.16), ("step2", 1.36), ("step3", 1.6)):
        if len(out) < 5:
            add(name, cam.cover(focus=focus, at=(0.5, 0.42), zoom=k))
    if len(out) < 3:
        # nothing was pointed at: the picture's own thirds, a step in
        cx, cy, w = motion._cwz(out[0][1])
        for name, dx, dy, k in (("left", -0.2, 0.0, 0.66), ("right", 0.2, 0.0, 0.66), ("in", 0.0, -0.02, 0.56), ("low", 0.0, 0.16, 0.7)):
            add(name, cam.rect(cx + dx * w, cy + dy * cam.h_of(w), w * k))
    return out


def splits(a, b, stress, lo, hi):
    """Frames inside (a, b) where a shot is cut into framings: the strongest stress peak in each window of
    [lo, hi] frames after the last change, so no framing is shorter than lo or longer than hi."""
    pts, t = [], a
    while b - t > hi:
        cand = [(s, -f) for f, s in stress if t + lo <= f <= t + hi and b - f >= lo]
        if cand:
            f = -max(cand)[1]
        else:
            f = t + (lo + hi) // 2
            if b - f < lo:
                break
        pts.append(int(f))
        t = f
    return pts


def _face_on_screen(crop, face, eyes=None):
    """Where a face sits in a framing: (x of its centre, y of its eyes, its height), as shares of the frame."""
    w, h = crop[2] - crop[0], crop[3] - crop[1]
    ey = eyes if eyes is not None else face[1] + face[3] * 0.42
    return ((face[0] + face[2] / 2 - crop[0]) / w, (ey - crop[1]) / h, face[3] / h)


def _eased_take(sh, cam, M, faces, stress, a, b, lo, hi, fps, rng, log):
    """A take, re-framed the way the references re-frame one: no cut. On the stress peaks the camera eases a step
    in (4 to 7%, the references' eased moves on a take), up to three in a row or as far as the pixels go, then
    eases back out further than one step; between moves it keeps a slow push. One item, one continuous track."""
    n = b - a
    names = dict(M)
    big = max(faces, key=lambda f: f[2] * f[3]) if faces else None
    eyes = {tuple(f[:4]): f[4] for f in sh.get("faces") or [] if len(f) > 4}
    # a talking head fills a third of the frame's height in the references (WF1 0.34, quartiles 0.29 to 0.38)
    start = cam.on_face(big, 0.34, eyes=eyes.get(tuple(big))) if big else (names.get("step1") or M[0][1])
    cx, cy, w = motion._cwz(start)
    tos, cur, run_in = [], w, 0
    for f in splits(a, b, stress, lo, hi):
        size = 0.043 + 0.027 * rng.random()
        go_in = run_in < 3 and cur / (1 + size) >= cam.w_min * 1.01 and (run_in == 0 or rng.random() < 0.7)
        if go_in:
            cur, run_in = cur / (1 + size), run_in + 1
            kind = "out"
        else:
            back = max(run_in, 1) * 0.05 + size
            cur, run_in = min(cur * (1 + back), cam.w_max), 0
            kind = "io"
        fr = max(int(round((14 if kind == "out" else 18) * fps / 30.0 * (0.8 + 0.4 * rng.random()))), 6)
        tgt = cam.keep_whole(cam.rect(cx, cy, cur), faces)
        cur = tgt[2] - tgt[0]
        tos.append(dict(crop=tgt, at=max(f - a - max(fr // 5, 1), 0), frames=fr, kind=kind, over=bake.MOVE_OVER if (kind == "out" and rng.random() < bake.MOVE_OVER_SHARE) else 0.0))
        log["changes"].append(f)
        log["eased"] += 1
    it = {"id": sh["id"], "k": "shot", "f": [a, b], "pic": sh["pic"], "src": list(sh["src"])}
    it.update(sh.get("keep") or {})
    it["_cam"], it["_kw"], it["_crop"], it["_faces"], it["_name"] = cam, dict(to=tos, drift=dict(rate=0.012, dir=1)), list(start), faces, "take"
    if faces and "window" not in it:
        it["window"] = looks.window_of(max(faces, key=lambda f: f[2] * f[3]), pad=1.25)
    log["changes"].append(a)
    log["moves"].append(dict(f=[a, b], pic=sh["pic"], framing="take", to=len(tos)))
    return it


def drive(shots, stress, fps=30.0, frame=(1080, 1920), family="story", seed=0, band=None):
    """The film's pictures, cut into framings on the voice's stress and set moving.

    shots   in order, each {"id", "f": [a, b], "pic", "pw", "ph"} plus what is known of the picture:
            "faces" [[x, y, w, h(, eyes y)]] and "regions" [[x0, y0, x1, y1]] in picture fractions, "focus" [x, y],
            "src" [t0, t1] for a video, "keep" (L1 keys carried onto every framing: grade, look, treat, step),
            "lock": True for a shot to pass through untouched (its own "item")
    stress  [(frame, strength 0..1)], from ``stress``
    Returns (items, log): L1 shot items, and what was done (changes, holds, moves) for the meter.
    """
    B = dict(BANDS.get(family) or BANDS["story"], **(band or {}))
    W, H = frame
    lo, hi = int(round(B["hold"][0] * fps)), int(round(B["hold"][1] * fps))
    rng = random.Random(int(seed) ^ 0x51CE)
    stress = sorted((int(f), float(s)) for f, s in stress)
    s_at = dict(stress)
    items, log = [], {"changes": [], "moves": [], "whips": 0, "matches": 0, "eased": 0, "punches": 0}
    prev = None                      # the framing on screen before this one: dict(pic, name, crop, face)
    # band "moves_per_10s" (a caller holding the references' move rate, e.g. 4.3): a punch, an eased move or a whip is
    # placed only while the rolling 10 s count before it is under that budget. Absent: no budget, exactly as before.
    budget = B.get("moves_per_10s")
    moved = []

    def room(f):
        if budget is None:
            return True
        return sum(1 for g in moved if f - 10 * fps < g <= f) < float(budget)
    n_shots = len(shots)
    for si, sh in enumerate(shots):
        a, b = int(sh["f"][0]), int(sh["f"][1])
        if sh.get("lock") or not sh.get("pw"):
            it = sh.get("item") or {"id": sh["id"], "k": "shot", "f": [a, b], "pic": sh.get("pic")}
            items.append(it)
            log["changes"].append(a)
            prev = None
            continue
        cam = motion.Cam(sh["pw"], sh["ph"], W / H, out_w=W, max_up=float(sh.get("max_up") or motion.MAX_UP))
        faces = [f[:4] for f in sh.get("faces") or []]
        eyes = {tuple(f[:4]): f[4] for f in sh.get("faces") or [] if len(f) > 4}
        M = menu(cam, sh, W)
        if B.get("ease_takes") and sh.get("src"):
            items.append(_eased_take(sh, cam, M, faces, stress, a, b, lo, hi, fps, rng, log))
            prev = None
            continue
        pts = splits(a, b, stress, lo, hi) if len(M) >= 2 else []
        if len(M) == 2:
            pts = pts[:1]                # two framings: there and no way back, so one change
        edges = [a] + pts + [b]
        # the order of framings: never the one on screen, never the one before it
        seq, used = [], []
        for k in range(len(edges) - 1):
            cur = seq[-1] if seq else None
            before = seq[-2] if len(seq) > 1 else None
            pool = [m for m in M if m[0] != cur and m[0] != before] or [m for m in M if m[0] != cur] or M
            if k == 0:
                same = prev and prev["pic"] == sh.get("pic")
                first = [m for m in pool if m[0].startswith(("face", "close"))] if (si == 0 and faces) else [m for m in pool if m[0] == "wide"]
                if same:
                    first = [m for m in pool if m[0] != prev["name"]]
                pick = (first or pool)[0]
            else:
                c0 = dict(M)[cur]
                fresh = [m for m in pool if m[0] not in used] or pool
                # the framing least like the one on screen reads as a new picture; ties go to the seed
                fresh = sorted(fresh, key=lambda m: (round(_iou(m[1], c0), 2), rng.random()))
                pick = fresh[0]
            seq.append(pick[0])
            used.append(pick[0])
        crops = dict(M)
        for k in range(len(edges) - 1):
            f0, f1 = edges[k], edges[k + 1]
            n = f1 - f0
            name = seq[k]
            crop = list(crops[name])
            face = None
            if name.startswith(("face", "close", "side")):
                face = sorted(faces, key=lambda f: -f[2] * f[3])[int(name[-1])]
            # a face cut to a face keeps the eyes where they were
            if k == 0 and face is not None and prev and prev.get("face_at") and prev["pic"] != sh.get("pic"):
                x_at, e_at, h_at = prev["face_at"]
                if 0.14 <= h_at <= 0.6:
                    m = cam.on_face(face, h_at, eyes_y=min(max(e_at, 0.25), 0.5), eyes=eyes.get(tuple(face)), x_at=min(max(x_at, 0.3), 0.7))
                    if cam.up(m, W) <= cam.max_up:
                        crop = m
                        log["matches"] += 1
            kw = {}
            tight = (crop[2] - crop[0]) <= cam.w_min * 1.06
            wide = (crop[2] - crop[0]) >= cam.w_max * 0.985
            st = s_at.get(f0, 0.0)
            # a slow push: in on most (57% of the references'), out where there is no way in; the shorter the shot the faster
            rate = bake.DRIFT_RATE[0 if n > 2.2 * fps else 1 if n > 0.9 * fps else 2]
            d_in = (not tight) and (wide or rng.random() < 0.57)
            lr = motion.room(cam, crop)
            pan = (0.0, 0.0)
            if tight and wide:
                d_in = True              # a picture with no pixels to spare still breathes: track lets a move pass the limit by 8%
            if (lr[0] + lr[1]) > 0.06 and rng.random() < 0.45:
                # a drift across: toward the side with room, at the references' pan (0.019 frame widths a second, 0.049 at p75)
                pan = (bake.PAN_RATE[2 if n < fps else 1] * (1 if lr[1] > lr[0] else -1), 0.0)
            kw["drift"] = dict(rate=rate, dir=1 if d_in else -1, pan=pan)
            if (k > 0 or B.get("punch_first")) and st >= 0.6 and n >= int(0.4 * fps) and room(f0):
                # a cut-in on a stressed word lands a little past its size and settles
                kw["settle"] = dict(size=bake.CAM_SETTLE["size"] * (0.6 + 0.6 * st), dir=1)
                log["punches"] += 1
                if budget is not None:
                    moved.append(f0)
            # the strongest word inside a long framing: an eased move in, on the measured curve
            # (band "ease_min_s": a family that cuts faster eases short framings too, a 4-9% move; "punch_first": the
            # settle may land on a picture's first framing when its word is stressed. Absent: 1.1 s and k > 0, as before.)
            short = n < int(1.1 * fps)
            if n >= int(float(B.get("ease_min_s", 1.1)) * fps) and not tight:
                m0, m1 = (int(0.1 * fps), int(0.1 * fps)) if short else (int(0.35 * fps), int(0.45 * fps))
                inner = [(s, f) for f, s in stress if f0 + m0 <= f <= f1 - m1 and s >= B["ease"]]
                if inner and room(max(inner)[1]):
                    s_, f_ = max(inner)
                    if short:
                        size = 0.04 + 0.05 * rng.random()                                                 # 4 to 9% on a short framing
                    else:
                        size = bake.MOVE_SIZE[1] + (bake.MOVE_SIZE[2] - bake.MOVE_SIZE[1]) * rng.random()      # 6 to 12%: the eased moves' upper half
                    cx, cy, w = motion._cwz(crop)
                    tgt = cam.keep_whole(cam.rect(cx, cy, w / (1 + size)), faces)
                    fr = max(int(round(bake.MOVE_FRAMES * fps / 30.0 * (0.75 + 0.5 * rng.random()))), 5)
                    # the curve's speed peaks about a fifth of the way in: start so that lands on the peak
                    kw["to"] = dict(crop=tgt, at=max(f_ - f0 - max(fr // 5, 1), 0), frames=fr, kind="out",
                                    over=bake.MOVE_OVER if rng.random() < bake.MOVE_OVER_SHARE else 0.0)
                    kw["drift"] = dict(kw["drift"], rate=kw["drift"]["rate"] * 0.5)
                    log["eased"] += 1
                    log["changes"].append(f_)
                    if budget is not None:
                        moved.append(f_)
            it = {"id": sh["id"] if k == 0 else f"{sh['id']}.{k}", "k": "shot", "f": [f0, f1], "pic": sh["pic"]}
            it.update(sh.get("keep") or {})
            if sh.get("src"):
                t0, t1 = sh["src"]
                it["src"] = [round(t0 + (t1 - t0) * (f0 - a) / max(b - a, 1), 4), round(t0 + (t1 - t0) * (f1 - a) / max(b - a, 1), 4)]
            it["_cam"], it["_kw"], it["_crop"], it["_faces"], it["_name"] = cam, kw, crop, faces, name
            if faces and "window" not in it:
                # the look's window: the face this framing is on, else the largest one in the picture
                it["window"] = looks.window_of(face if face is not None else max(faces, key=lambda f: f[2] * f[3]))
            items.append(it)
            log["changes"].append(f0)
            log["moves"].append(dict(f=[f0, f1], pic=sh["pic"], framing=name, **{q: True for q in kw}))
            prev = dict(pic=sh.get("pic"), name=name, crop=crop,
                        face_at=_face_on_screen(crop, face, eyes.get(tuple(face))) if face is not None else None)
    # whips: about one cut in eleven between two different pictures, where the voice hits hardest and both sides have room
    cuts = []
    for i in range(1, len(items)):
        p, q = items[i - 1], items[i]
        if "_cam" in p and "_cam" in q and p["pic"] != q["pic"]:
            n0, n1 = p["f"][1] - p["f"][0], q["f"][1] - q["f"][0]
            fr = int(round(bake.WHIP["frames"] * fps / 30.0))
            if min(n0, n1) >= 3 * fr:
                cuts.append((s_at.get(q["f"][0], 0.0), rng.random(), i))
    want = int(round(B["whip"] * max(sum(1 for i in range(1, len(items)) if items[i].get("pic") != items[i - 1].get("pic")), 0)))
    last = -99
    for _, _, i in sorted(cuts, reverse=True):
        if want <= 0:
            break
        if any(abs(i - j) < 3 for j in [last]):
            continue
        p, q = items[i - 1], items[i]
        if budget is not None and not room(q["f"][0]):
            continue
        rp, rq = motion.room(p["_cam"], p["_crop"]), motion.room(q["_cam"], q["_crop"])
        # the pan runs one way through the cut: the outgoing picture needs room ahead, the incoming one behind
        best = max(((min(rp[1], rq[0]), 0.0), (min(rp[0], rq[1]), math.pi), (min(rp[3], rq[2]), math.pi / 2), (min(rp[2], rq[3]), -math.pi / 2)))
        if best[0] < 0.10:
            continue
        tr = min(bake.WHIP["travel_w"], best[0])
        fr = int(round(bake.WHIP["frames"] * fps / 30.0))
        p["_kw"]["whip_out"] = dict(frames=fr, travel=tr, ang=best[1])
        q["_kw"]["whip_in"] = dict(frames=fr, travel=tr, ang=best[1])
        p["shutter"] = q["shutter"] = WHIP_SHUTTER
        q["_kw"].pop("settle", None)
        log["whips"] += 1
        want -= 1
        last = i
        if budget is not None:
            moved.append(q["f"][0])
    for it in items:
        if "_cam" not in it:
            continue
        cam, kw, crop, faces = it.pop("_cam"), it.pop("_kw"), it.pop("_crop"), it.pop("_faces")
        it.pop("_name")
        n = it["f"][1] - it["f"][0]
        it["crop"] = motion.track(cam, n, crop, fps, faces=faces, **kw)
    ch = sorted(set(log["changes"]))
    end = max((int(s["f"][1]) for s in shots), default=0)
    start = min((int(s["f"][0]) for s in shots), default=0)
    holds = [(y - x) / fps for x, y in zip(ch, ch[1:] + [end])]
    log["changes"] = ch
    log["per10"] = round(len(ch) / max((end - start) / fps, 1e-6) * 10, 2)
    log["hold_max"] = round(max(holds), 2) if holds else 0.0
    log["hold_median"] = round(float(np.median(holds)), 2) if holds else 0.0
    log["shutter"] = B["shutter"]
    return items, log


def drive_l1(l1, work, stress, meta=None, family="story", seed=None, only=None, band=None):
    """Drive a scene's own picture shots. Every base shot on an image or video asset is cut into framings and set
    moving; plates, treated shots and anything listed in no ``only`` pass through. Words, cards, sound: untouched.

    meta    {asset id: {"faces", "regions", "focus", "pw", "ph"}}: what the caller knows of each picture. Sizes are
            read from the files when not given.
    Returns a new scene with ``film.shutter`` set (when the scene named none) and ``pace`` holding the meter.
    """
    from PIL import Image, ImageOps
    meta = meta or {}
    fps = float(l1.get("fps") or 30)
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    assets = l1.get("assets") or {}
    sizes = {}

    def size(aid):
        if aid not in sizes:
            m = meta.get(aid) or {}
            if m.get("pw"):
                sizes[aid] = (m["pw"], m["ph"])
            else:
                a = assets.get(aid) or {}
                p = a.get("path") or ""
                p = p if os.path.isabs(p) else os.path.join(work, p)
                try:
                    if a.get("kind") == "video":
                        from . import reader
                        pr = reader.probe(p)
                        sizes[aid] = (pr["w"], pr["h"])
                    else:
                        with Image.open(p) as im:
                            sizes[aid] = ImageOps.exif_transpose(im).size
                except Exception:      # noqa: BLE001 - a picture that cannot be read is left as the scene had it
                    sizes[aid] = (0, 0)
        return sizes[aid]
    shots, rest = [], []
    for it in l1.get("items") or []:
        if it.get("k") != "shot":
            rest.append(it)
            continue
        aid = it.get("pic")
        ok = aid in assets and assets[aid].get("kind") in ("image", "video") and not it.get("treat") and (only is None or it["id"] in only)
        pw, ph = size(aid) if ok else (0, 0)
        if not ok or not pw:
            shots.append({"id": it["id"], "f": it["f"], "pic": aid, "lock": True, "item": it})
            continue
        m = meta.get(aid) or {}
        keep = {k: v for k, v in it.items() if k not in ("id", "k", "f", "pic", "crop", "src", "blur")}
        shots.append({"id": it["id"], "f": it["f"], "pic": aid, "pw": pw, "ph": ph, "faces": m.get("faces"), "regions": m.get("regions"),
                      "focus": m.get("focus"), "max_up": m.get("max_up"), "src": it.get("src"), "keep": keep})
    shots.sort(key=lambda s: s["f"][0])
    sd = int(l1.get("seed") or 0) if seed is None else int(seed)
    items, log = drive(shots, stress, fps, (W, H), family, sd, band)
    out = dict(l1, items=items + rest)
    film = dict(l1.get("film") or {})
    film.setdefault("shutter", log["shutter"])
    out["film"] = film
    out["pace"] = {k: log[k] for k in ("per10", "hold_max", "hold_median", "whips", "matches", "eased", "punches")}
    return out
