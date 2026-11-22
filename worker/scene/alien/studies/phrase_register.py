"""Three utterances supply three print separations. Their eyes serve as register pins."""

from pathlib import Path

import cv2
import numpy as np

from . import material as m


def register(frames, landmarks):
    target, distance, _ = landmarks[-1]
    images = []
    for frame, (origin, span, _) in zip(frames, landmarks):
        scale = float(distance / max(span, 1))
        dx, dy = target - origin * scale
        matrix = np.array([[scale, 0, dx], [0, scale, dy]], np.float32)
        images.append(
            cv2.warpAffine(frame, matrix, (frame.shape[1], frame.shape[0]), borderMode=cv2.BORDER_REFLECT_101)
        )
    return images


def plan(l1, work, ask):
    fps, (w, h) = l1["fps"], l1["frame"]
    beats = ask["beats"]
    a, z = beats[0], min(l1["dur_f"], beats[2] + round(1.13 * fps))
    frames = m.moments(l1, work, beats)
    landmarks = [m.face(fr, Path(ask["models"]) / "face_detection_yunet_2023mar.onnx") for fr in frames]
    aligned = register(frames, landmarks)
    # Rotated dot screens retain the expression of each clause; even in register
    # the mouth can never be exactly the same as a single exposure.
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    stock = m.paper((h, w), (247, 235, 211))
    inks = [np.array(c, np.float32) / 255 for c in [(35, 165, 171), (209, 68, 98), (241, 184, 46)]]
    screens = []
    for i, (fr, angle) in enumerate(zip(aligned, [15, 75, 0])):
        s, c = np.sin(np.deg2rad(angle)), np.cos(np.deg2rad(angle))
        u, v = (xx * c + yy * s) / (w * 0.0045), (-xx * s + yy * c) / (w * 0.0045)
        dot = (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v) + 2) * 0.25
        darkness = 1 - fr[:, :, i].astype(np.float32) / 255.0
        screens.append(np.clip((darkness - dot) * 5 + 0.5, 0, 1))

    def pictures():
        for f, live in zip(range(a, z), m.take_frames(l1, work, a, z)):
            out = stock.astype(np.float32)
            for i, b in enumerate(beats):
                strength = float(m.ease((f - b + 1) / (0.17 * fps)))
                if strength:
                    opacity = screens[i][:, :, None] * strength
                    out *= 1 - opacity * (1 - inks[i])
            reveal = m.ease((f - a + 1) / (0.18 * fps)) * m.ease((z - 1 - f) / (0.35 * fps))
            yield m.mix(live, np.uint8(out), reveal)

    return m.plate(
        l1,
        work,
        ask,
        a,
        z,
        pictures(),
        {
            "accent_frame": beats[2],
            "measured_eyes": [o.tolist() for o, _, _ in landmarks],
            "exposures": beats,
        },
    )
