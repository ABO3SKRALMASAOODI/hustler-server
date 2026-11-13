"""Letterforms as numbers, for the devices that cut, light or open a picture through a word: the box a word
takes on the frame and its coverage as an array. The engine's own shaper and rasteriser (scene.type,
scene.type_atlas), so a mask made here lands on the letters the engine draws."""
import numpy as np

from .. import type as T
from .. import type_atlas as TA


def face(voice):
    try:
        return T.Face.of(voice)
    except FileNotFoundError:
        return T.Face.get(T.FALLBACK[0])


def box(voice, text, px=None):
    """(advance, [x0, y0, x1, y1] of the ink) in pixels from the pen at the baseline's left end, y down."""
    px = float(px or voice.get("px", 58))
    lines, adv = face(voice).outline(text, px, 0.0)
    if not len(lines):
        return adv, [0.0, -px, adv, 0.0]
    return adv, [float(lines[:, [0, 2]].min()), float(lines[:, [1, 3]].min()), float(lines[:, [0, 2]].max()), float(lines[:, [1, 3]].max())]


def fit_px(voice, text, width_px, cap_px=None):
    """The font size at which a word's ink is ``width_px`` wide (never past ``cap_px``)."""
    _, b = box(voice, text, 100.0)
    px = 100.0 * width_px / max(b[2] - b[0], 1e-3)
    return float(min(px, cap_px)) if cap_px else float(px)


def raster(voice, text, px, pen, w, h, k=1.0):
    """Coverage of a word on a w x h array: pen = (x, y) of the baseline's left end in frame pixels, ``k`` the
    array's pixels per frame pixel (a light is solved small). float32, 0..1."""
    lines, _ = face(voice).outline(text, float(px) * k, 0.0)
    if not len(lines):
        return np.zeros((h, w), np.float32)
    ln = lines.copy()
    ln[:, [0, 2]] += pen[0] * k
    ln[:, [1, 3]] += pen[1] * k
    ln[:, [0, 2]] = np.clip(ln[:, [0, 2]], 0, w - 1e-3)
    ln[:, [1, 3]] = np.clip(ln[:, [1, 3]], 0, h - 1e-3)
    return np.clip(np.abs(TA.coverage(ln, w, h))[:h, :w], 0, 1).astype(np.float32)
