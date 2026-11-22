"""Use the speaker's index fingers as opposite ends of a moving orrery."""

from pathlib import Path

import cv2
import numpy as np
from anchors.hands import HandTracker

from . import material as m


def hand_track(l1, work, a, z, model):
    tracker = HandTracker(str(model), max_hands=2, min_det=0.35, min_presence=0.35, threads=1)
    tracker.det_every = 1
    seen = []
    for f, fr in enumerate(m.take_frames(l1, work, a, z), a):
        if (f - a) % 3:
            continue
        small = cv2.resize(fr, (fr.shape[1] // 2, fr.shape[0] // 2))
        hs = tracker.step(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
        if len(hs) >= 2:
            pts = sorted([np.asarray(hand["pts"])[8, :2] * 2 for hand in hs], key=lambda p: p[0])
            seen.append((f, *pts[0], *pts[-1]))
    if len(seen) < 2:
        raise ValueError("The gesture needs two visible hands")
    samples = np.array(seen)
    tracks = np.column_stack([np.interp(np.arange(a, z), samples[:, 0], col) for col in samples.T[1:]])
    return tracks, len(seen)


def plan(l1, work, ask):
    fps, (w, h) = l1["fps"], l1["frame"]
    start, solar, galaxy, last = ask["beats"]
    a = max(0, start - round(0.32 * fps))
    z = min(l1["dur_f"], last + round(0.4 * fps))
    tracks, count = hand_track(l1, work, a, z, Path(ask["models"]) / "hand_landmarker.task")
    mid = m.moments(l1, work, [galaxy])[0]
    # Stars originate from optically distinct points in the actual room.
    corners = cv2.goodFeaturesToTrack(cv2.cvtColor(mid, cv2.COLOR_RGB2GRAY), 160, 0.018, 10)
    stars = corners.reshape(-1, 2)
    colours = [tuple(int(x) for x in mid[int(y), int(x)]) for x, y in stars]
    phase = np.linspace(0, 2 * np.pi, 180)

    def pictures():
        for j, live in enumerate(m.take_frames(l1, work, a, z)):
            f = j + a
            left, right = tracks[j].reshape(2, 2)
            origin = (left + right) / 2
            axis = (right - left) / 2
            perp = np.array([-axis[1], axis[0]])
            grow = m.ease((f - a) / (0.45 * fps))
            release = m.ease((z - f - 1) / (0.32 * fps))
            if np.linalg.norm(axis) < 4:
                yield live
                continue
            out = live.copy()
            # A translucent optical volume; the fingers remain in front of its rim.
            paths = []
            for ratio, slope in [(1, 0.32), (0.66, -0.44), (0.39, 0.54)]:
                pts = (
                    origin
                    + (np.outer(np.cos(phase), axis) + np.outer(np.sin(phase), perp) * slope) * ratio * grow
                )
                paths.append(np.int32(pts))
            tint = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(tint, paths[0], 140, cv2.LINE_AA)
            aa = tint.astype(np.float32)[:, :, None] / 255
            out = np.uint8(out * (1 - aa) + np.array([13, 24, 37]) * aa)
            for k, points in enumerate(paths):
                cv2.polylines(out, [points], True, (237, 211 - k * 15, 156), max(1, w // 600), cv2.LINE_AA)
                pos = (int(j * (1 + k * 0.35)) + k * 49) % 179
                cv2.circle(out, points[pos], max(3, w // 185 - k), (245, 220, 164), -1, cv2.LINE_AA)
            density = float(m.ease((f - solar) / max(1, galaxy - solar)))
            if density:
                for (x, y), col in zip(stars, colours):
                    u, v = (x / w - 0.5) * 1.65, (y / h - 0.5) * 1.65
                    if u * u + v * v > 1:
                        continue
                    pt = np.int32(origin + axis * u + perp * v * 0.32)
                    cv2.circle(
                        out,
                        pt,
                        max(1, round(2.2 * density)),
                        tuple(min(255, i + 105) for i in col),
                        -1,
                        cv2.LINE_AA,
                    )
            for tip in (left, right):
                mask = np.zeros((h, w), np.uint8)
                cv2.circle(mask, np.int32(tip), max(5, w // 92), 255, -1, cv2.LINE_AA)
                np.copyto(out, live, where=mask[:, :, None] > 0)
            yield m.mix(live, out, grow * release)

    spans = np.linalg.norm(tracks[:, 2:] - tracks[:, :2], axis=1)
    return m.plate(
        l1,
        work,
        ask,
        a,
        z,
        pictures(),
        {
            "accent_frame": galaxy,
            "two_hand_samples": count,
            "room_stars": len(stars),
            "measured_span_px": [round(float(spans.min())), round(float(spans.max()))],
        },
    )
