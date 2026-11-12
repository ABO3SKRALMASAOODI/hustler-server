#!/usr/bin/env python3
"""The alien passes: what is drawn on the engine's film of the pictures, before the type.

    $PY passes.py voice_light --film alien/pass1.mp4 --l1 alien/l1.json --dir alien --out alien/lit.mp4

voice_light (ALIEN L2, Voice-Lit Room): the room falls to near black and the only light is a warm lamp
at the speaker's mouth whose brightness is the level of the film's own sound on that frame. Pictures and
cards glow with the same lamp. Signal only: face, matte and RMS. The pass measures its own film and
writes ``measure.json``: a claim is a number.
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import l1pass as L  # noqa: E402
import sidecars as S  # noqa: E402

AMB = 0.03                                         # what is left of the room
COOL = np.array([1.0, 0.93, 0.86], np.float32)     # BGR: the dark is a little blue
WARM = np.array([0.60, 0.84, 1.0], np.float32)     # BGR: the lamp is a voice, warm
PEAK = 1.45                                        # the lamp at the mouth on the loudest phrase, times the take's own exposure
BACK = 0.16                                        # how much of the lamp reaches the wall behind him
RELEASES = (7.0, 9.0, 12.0, 16.0, 22.0, 30.0)


def writer(path, w, h, fps=S.FPS, crf=14):
    cmd = [S.FF, "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def lamp_envelope(lev):
    """The slowest-needed release: the lamp follows phrases, and never flashes more than the health rule allows."""
    for rel in RELEASES:
        env = S.envelope(lev, attack_f=2.0, release_f=rel)
        if S.flashes_per_second(0.5 * np.clip(env, 0, 1) ** 1.4) <= 2.5:
            break
    return env, rel


def voice_light(film, l1, adir, out):
    n = int(l1["dur_f"])
    W, H = l1["frame"]
    cuts = L.cuts(l1)
    tp, mp = os.path.join(adir, "track.npy"), os.path.join(adir, "matte.npy")
    if not os.path.exists(tp):
        np.save(tp, S.track(S.faces(film, n), cuts, n))
    T = np.load(tp)
    if not os.path.exists(mp):
        S.matte(film, cuts, n, mp)
    M = np.load(mp, mmap_mode="r")
    lev = S.level(film, n)
    env, rel = lamp_envelope(lev)
    slow = S.envelope(lev, attack_f=3.0, release_f=24.0)       # a picture breathes with the phrase, it does not blink with the syllable
    np.save(os.path.join(adir, "level.npy"), lev)
    cards = L.cards(l1)
    bounds, expo = S.shot_bounds(cuts, n), {}
    gy, gx = np.mgrid[0:S.GH, 0:S.GW].astype(np.float32)
    lut = (np.arange(256, dtype=np.float32) / 255.0) ** 2.2
    wr = writer(out + ".part.mp4", W, H)
    lum = np.zeros(n, np.float32)
    lit_px = np.zeros(n, np.float32)
    lum_in, lamp = np.zeros(n, np.float32), np.zeros(n, np.float32)
    i = -1
    for i, fr in enumerate(S.frames_of(film, W, H)):
        if i >= n:
            break
        e = float(min(env[i], 1.15)) ** 1.4
        ep = 0.3 + 0.7 * float(min(slow[i], 1.0)) ** 1.2
        if not np.isnan(T[i, 0]):                                  # a person: the lamp sits at his mouth
            mx, my = T[i, 4] * S.GW, T[i, 5] * S.GH
            R = max(0.9 * T[i, 3] * S.GH, 36.0)
            fall = 1.0 / (1.0 + ((gx - mx) ** 2 + (gy - my) ** 2) / (R * R))
            person = cv2.GaussianBlur(M[i].astype(np.float32) / 255.0, (0, 0), 1.2)
            light = PEAK * e * fall * (BACK + (1.0 - BACK) * person)
        else:                                                      # a picture: it glows with the same lamp,
            a0 = next((a for a, b in bounds if a <= i < b), 0)     # brought to the take's exposure first
            if a0 not in expo:
                g0 = lut[fr[::8, ::8]].mean(axis=2)
                expo[a0] = float(np.clip(0.55 / max(np.percentile(g0, 97), 1e-3), 1.0, 3.0))
            r2 = ((gx / S.GW - 0.5) ** 2 + (gy / S.GH - 0.45) ** 2)
            light = 0.95 * expo[a0] * ep * (1.0 - 0.9 * r2)
        for a, b, box in cards:                                    # a card in the room is lit by the voice too
            if a <= i < b:
                x0, y0, x1, y1 = [int(round(v)) for v in (box[0] * S.GW, box[1] * S.GH, box[2] * S.GW, box[3] * S.GH)]
                sub = light[max(y0, 0):y1, max(x0, 0):x1]
                np.maximum(sub, 0.85 * ep, out=sub)
        light = cv2.resize(light.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)[:, :, None]
        x = lut[fr] * (AMB * COOL + light * WARM)
        hi = x > 0.8                                               # a soft shoulder instead of a clip
        x[hi] = 0.8 + 0.2 * np.tanh((x[hi] - 0.8) / 0.2)
        o = (np.clip(x, 0, 1) ** (1 / 2.2) * 255.0 + 0.5).astype(np.uint8)
        wr.stdin.write(o.tobytes())
        g = o[::8, ::8].astype(np.float32).mean(axis=2) / 255.0
        lum[i] = float((g ** 2.2).mean())
        lum_in[i] = float(lut[fr[::8, ::8]].mean())
        lamp[i] = e if not np.isnan(T[i, 0]) else ep
        lit_px[i] = float((g > 0.2).mean())
    wr.stdin.close()
    if wr.wait() != 0:
        raise RuntimeError("the encoder failed on the lit film")
    if i + 1 < n:
        raise RuntimeError(f"the film gave {i + 1} frames, the plan has {n}")
    os.replace(out + ".part.mp4", out)
    speak = env > 0.5
    quiet = env < 0.05
    face = ~np.isnan(T[:, 0])                                    # the take's own frames: brightness against the sound's raw level
    c = float(np.corrcoef(lum[face], lev[face])[0, 1]) if face.sum() > 10 and lum[face].std() > 0 and lev[face].std() > 0 else 0.0
    # the claim, shot by shot: how much of its own picture each frame keeps, against the lamp
    gain = lum / np.maximum(lum_in, 1e-4)
    num = den = 0.0
    for a, b in bounds:
        if b - a >= 15 and lamp[a:b].std() > 0.02 and lum_in[a:b].mean() > 2e-4 and gain[a:b].std() > 0:
            num += float(np.corrcoef(gain[a:b], lamp[a:b])[0, 1]) * (b - a)
            den += b - a
    in_shot = num / den if den else 0.0
    scale = 1.0 / max(float(np.percentile(lum, 99.5)), 1e-6) * 0.5
    m = dict(device="voice_light", alien="L2", frames=n, release_f=rel, faces_tracked=round(float((~np.isnan(T[:, 0])).mean()), 3),
             corr_light_lamp_in_shot=round(in_shot, 3), corr_brightness_level_on_the_take=round(c, 3),
             lum_speaking=round(float(lum[speak].mean()) if speak.any() else 0.0, 4),
             lum_pause=round(float(lum[quiet].mean()) if quiet.any() else 0.0, 4),
             pause_frames=int(quiet.sum()), lit_area_speaking=round(float(lit_px[speak].mean()) if speak.any() else 0.0, 3),
             flashes_per_s=S.flashes_per_second(np.clip(lum * scale, 0, 1)))
    m["ok"] = bool(m["corr_light_lamp_in_shot"] >= 0.8 and m["flashes_per_s"] <= 3.0 and (not quiet.any() or m["lum_pause"] < 0.35 * max(m["lum_speaking"], 1e-6)))
    return m


YAW_DEG, CAM_ZOOM, CAM_SHIFT, PARALLAX = 7.0, 1.15, 0.05, 0.026      # the second camera: a few degrees off axis, a little closer
CAM_HOLD = (75, 135)                                                    # it holds 2.5 to 4.5 s, like a real cut


def camera_spans(n, bounds, T, lev, onsets, busy):
    """Where the second camera is on: [(a, b, side)]. It opens the film on the first long stretch of the take, comes
    back once on the longest stretch left, and never holds the last two seconds: the film ends on the true frame.
    A cut lands on the most stressed word in reach: the loudest onset, from the sound's own level."""
    def stress(a, b):
        cand = [o for o in onsets if a <= o < b]
        return max(cand, key=lambda o: float(lev[o:o + 4].mean())) if cand else (a + b) // 2

    free = []
    for a, b in bounds:
        if np.isnan(T[a:b, 0]).mean() > 0.2:
            continue
        cur = a
        for i in range(a, b + 1):
            if i == b or busy[i]:
                if i - cur >= 150:
                    free.append((cur, i))
                cur = i + 1
    spans, side = [], 1
    if free:
        a, b = free[0]
        cut = stress(a + CAM_HOLD[0], min(a + CAM_HOLD[1], b - 60, n - 60))
        if cut - a >= 60:
            spans.append((a, cut, side))
            side = -side
            free[0] = (cut + 90, b)
    rest = [(a, min(b, n - 60)) for a, b in free if min(b, n - 60) - a >= 210]
    if rest and n >= 600:                                   # a second visit only when the film is long enough to earn it
        a, b = max(rest, key=lambda r: r[1] - r[0])
        start = stress(a + 60, max(a + 61, b - CAM_HOLD[1] - 30))
        stop = stress(start + CAM_HOLD[0], min(start + CAM_HOLD[1], b - 45))
        if stop - start >= 60:
            spans.append((start, stop, side))
    return sorted(spans)


def reproject_map(d, fx, fy, side, k):
    """Depth-image reprojection on the geometry grid: for every pixel of the new view, the x it is read from.
    Far pixels are laid first and near ones over them; what the move uncovers mirrors the farther side."""
    gh, gw = d.shape
    y0, x0 = int(np.clip(fy, 2, gh - 3)), int(np.clip(fx, 2, gw - 3))
    dref = float(np.median(d[y0 - 2:y0 + 3, x0 - 2:x0 + 3]))
    disp = (-side * k * PARALLAX * gw) * (d - dref)
    order = np.argsort(d, axis=None)
    ys, xs = np.unravel_index(order, d.shape)
    tgt = xs + disp.ravel()[order]
    tx = np.clip(np.rint(tgt), 0, gw - 1).astype(np.int32)
    bx = np.full(d.shape, np.nan, np.float32)
    bd = np.full(d.shape, np.inf, np.float32)
    bx[ys, tx] = xs - (tgt - tx)
    bd[ys, tx] = d.ravel()[order]
    hole = np.isnan(bx)
    if hole.any():
        idx = np.broadcast_to(np.arange(gw, dtype=np.int32), d.shape)
        left = np.maximum.accumulate(np.where(hole, -1, idx), axis=1)
        right = np.minimum.accumulate(np.where(hole, gw, idx)[:, ::-1], axis=1)[:, ::-1]
        rows = np.broadcast_to(np.arange(gh, dtype=np.int32)[:, None], d.shape)
        lc, rc = np.clip(left, 0, gw - 1), np.clip(right, 0, gw - 1)
        dl = np.where(left >= 0, bd[rows, lc], np.inf)
        dr = np.where(right < gw, bd[rows, rc], np.inf)
        far_right = dr <= dl
        fill = np.where(far_right, bx[rows, rc] + (right - idx), bx[rows, lc] - (idx - left))
        fill = np.where(np.isfinite(fill), fill, idx)
        bx = np.where(hole, fill, bx)
    return bx.astype(np.float32), float(hole.mean()), disp


def second_camera(film, l1, adir, out):
    """ALIEN K1, Second Camera: a one-camera take cuts to an angle that was never shot. The same frames, seen from a
    few degrees off axis: the picture is reprojected through its own depth, so the room slides behind him as it would
    for a real second camera, and the cuts between the two land on stressed words. Depth, faces and level: signal only."""
    n = int(l1["dur_f"])
    W, H = l1["frame"]
    cuts = L.cuts(l1)
    bounds = S.shot_bounds(cuts, n)
    tp = os.path.join(adir, "track.npy")
    if not os.path.exists(tp):
        np.save(tp, S.track(S.faces(film, n), cuts, n))
    T = np.load(tp)
    D = np.load(os.path.join(adir, "depth.npy")).astype(np.float32)
    kf = np.load(os.path.join(adir, "depth_kf.npy"))
    lev = S.level(film, n)
    onsets = sorted({int(it.get("on", it["f"][0])) for it in l1["items"] if it.get("k") == "word" and it.get("role") == "cap"})
    busy = np.zeros(n + 1, bool)
    for a, b, _ in L.cards(l1):                                         # a card is not in the room: no second angle while one stands
        busy[max(a - 2, 0):min(b + 2, n)] = True
    spans = camera_spans(n, L.take_stretches(l1), T, lev, onsets, busy)
    for a0, b0 in bounds:                                               # depth steadied along each shot, so the parallax does not shimmer
        ks = np.where((kf >= a0) & (kf < b0))[0]
        for j in range(1, len(ks)):
            D[ks[j]] = 0.45 * D[ks[j]] + 0.55 * D[ks[j - 1]]
    gh, gw = D.shape[1:]
    kx = np.tan(np.radians(YAW_DEG)) / (1.7 * W)
    wr = writer(out + ".part.mp4", W, H)
    holes, par = [], []
    idy = np.broadcast_to(np.arange(H, dtype=np.float32)[:, None], (H, W)).copy()
    idx_full = np.broadcast_to(np.arange(W, dtype=np.float32)[None, :], (H, W)).copy()
    gxi = np.broadcast_to(np.arange(gw, dtype=np.float32)[None, :], (gh, gw))
    i = -1
    for i, fr in enumerate(S.frames_of(film, W, H)):
        if i >= n:
            break
        sp = next((s for s in spans if s[0] <= i < s[1]), None)
        if sp is None or np.isnan(T[i, 0]):
            wr.stdin.write(fr.tobytes())
            continue
        a, b, side = sp
        t = i - a
        k = (1.0 + 0.18 * (1.0 - min(t / 8.0, 1.0)) ** 3) * (1.0 + 0.05 * np.sin(2 * np.pi * t / 150.0))    # it settles, then breathes a little
        d = cv2.medianBlur(D[int(np.argmin(np.abs(kf - i)))], 5)
        seg = T[a:b]
        fx, fy = float(np.nanmedian(seg[:, 0] + seg[:, 2] / 2)), float(np.nanmedian(seg[:, 1] + seg[:, 3] / 2))   # a tripod: one aim for the span
        bx, hole, disp = reproject_map(d, fx * gw, fy * gh, side, k)
        holes.append(hole)
        par.append(abs(float(np.percentile(disp, 95)) - float(np.percentile(disp, 5))) * W / gw)
        # the move as a smooth field: a hard step at the silhouette would stair on the 4x grid, so it is eased over a few pixels
        shift = cv2.GaussianBlur(bx - gxi, (0, 0), 1.3)
        mx = idx_full + cv2.resize(shift, (W, H), interpolation=cv2.INTER_CUBIC) * (W / gw)
        cx, cy = fx * W, fy * H
        F = (np.array([[1, 0, cx - side * CAM_SHIFT * W], [0, 1, cy], [0, 0, 1]], np.float64)
             @ np.array([[1, 0, 0], [0, 1, 0], [side * kx, 0, 1]], np.float64)
             @ np.diag([CAM_ZOOM, CAM_ZOOM, 1.0])
             @ np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], np.float64))
        mapx = cv2.warpPerspective(mx, F, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        mapy = cv2.warpPerspective(idy, F, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        o = cv2.remap(fr, mapx, mapy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        wr.stdin.write(o.tobytes())
    wr.stdin.close()
    if wr.wait() != 0:
        raise RuntimeError("the encoder failed on the second camera")
    if i + 1 < n:
        raise RuntimeError(f"the film gave {i + 1} frames, the plan has {n}")
    os.replace(out + ".part.mp4", out)
    on = sum(b - a for a, b, _ in spans)
    m = dict(device="second_camera", alien="K1", frames=n, spans=[[int(a), int(b), int(s)] for a, b, s in spans], share=round(on / max(n, 1), 3),
             yaw_deg=YAW_DEG, zoom=CAM_ZOOM, parallax_px=round(float(np.mean(par)) if par else 0.0, 1),
             uncovered_pct=round(100 * float(np.mean(holes)) if holes else 0.0, 2), cuts=len(spans) * 2 - (1 if spans and spans[0][0] == 0 else 0))
    m["ok"] = bool(spans and m["parallax_px"] >= 6.0 and m["uncovered_pct"] < 6.0)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["voice_light", "second_camera"])
    ap.add_argument("--film", required=True)
    ap.add_argument("--l1", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    l1 = json.load(open(a.l1))
    m = {"voice_light": voice_light, "second_camera": second_camera}[a.cmd](a.film, l1, a.dir, a.out)
    mp = os.path.join(a.dir, f"measure_{a.cmd}.json")
    json.dump(m, open(mp + ".part", "w"), indent=1)
    os.replace(mp + ".part", mp)
    print(json.dumps(m))


if __name__ == "__main__":
    main()
