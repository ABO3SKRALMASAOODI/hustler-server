"""A spoken promise physically signs twice. Vowels drive a calligraphic nib."""

import cv2
import numpy as np

from .. import sense
from . import material as m


def signature(samples, w, h):
    # Each local vowel contributes a loop. Strength opens the nib, pitch inclines it.
    win, hop = 480, 80
    padded = np.pad(samples, (win // 2, win // 2))
    chunks = np.lib.stride_tricks.sliding_window_view(padded, win)[::hop]
    energy = np.sqrt(np.mean(chunks**2, axis=1))
    taper = np.hanning(win)
    pitch = []
    for chunk in chunks:
        corr = np.correlate(chunk * taper, chunk * taper, "full")[win - 1 :]
        lag = 26 + np.argmax(corr[26:115])
        pitch.append(8000 / lag)
    pressure = np.minimum(1.0, energy / max(np.percentile(energy, 90), 0.0001))
    phase = np.cumsum(np.asarray(pitch) * (0.01 / 12))
    t = np.linspace(0, 1, len(chunks))
    theta = phase * 2 * np.pi
    arch = pressure * (0.5 - 0.5 * np.cos(theta))
    x = (t + 0.065 * arch + 0.057 * pressure * np.sin(theta)) * w
    y = (-1.08 * arch - 0.12 * t + 0.12 * pressure * np.sin(theta)) * h
    return np.column_stack([x, y]), pressure


def plan(l1, work, ask):
    fps, (w, h) = l1["fps"], l1["frame"]
    starts, ends = ask["beats"], ask["ends"]
    a, z = max(0, starts[0] - round(0.36 * fps)), min(l1["dur_f"], ends[-1] + round(0.42 * fps))
    waves = []
    for s, e in zip(starts, ends):
        shot = sense.shot_at(l1, s)
        raw = sense._band(
            sense.path_of(l1, work, shot["pic"]), sense.src_time(shot, s, fps), (e - s) / fps, 8000
        )
        waves.append(signature(raw, 0.68 * w, 0.19 * h))
    stock = m.paper((h, w), (229, 222, 201))
    ink = (28, 54, 49)
    for y in (0.47, 0.83):
        cv2.line(
            stock, (int(0.15 * w), int(y * h)), (int(0.87 * w), int(y * h)), (157, 155, 136), 1, cv2.LINE_AA
        )

    def pictures():
        for f in range(a, z):
            page = stock.copy()
            for i, ((coords, pressure), s, e) in enumerate(zip(waves, starts, ends)):
                path = np.int32(coords + [0.16 * w, (0.46 + i * 0.36) * h])
                count = int(np.clip((f - s + 1) / (e - s), 0, 1) * (len(path) - 1))
                for j in range(count):
                    cv2.line(
                        page,
                        path[j],
                        path[j + 1],
                        ink if i == 0 else (121, 53, 38),
                        max(1, round(w * (0.0008 + 0.003 * pressure[j]))),
                        cv2.LINE_AA,
                    )
            yield page

    voices = {"signature.title": m.serif(h, 0.095), "signature.number": m.sans(h, 0.034)}
    items = [m.word(ask["id"] + ".title", ask["line"], "signature.title", a, z, 0.15, 0.19, "#263D36")]
    for i, s in enumerate(starts):
        items.append(
            m.word(
                ask["id"] + str(i),
                str(i + 1).zfill(2),
                "signature.number",
                s,
                z,
                0.075,
                0.465 + i * 0.36,
                "#696451",
            )
        )
    return m.plate(
        l1,
        work,
        ask,
        a,
        z,
        pictures(),
        {"utterance_frames": list(zip(starts, ends)), "nib_samples": [len(c) for c, _ in waves]},
        items,
        voices,
    )
