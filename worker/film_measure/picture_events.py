"""taste_ceil: TASTE.md (week-1112) ceilings for rubric v3.1, measured with
signal processing only. No model, no word list, nothing read from what a
customer asked.

Measured here, per 10 s window (hop 5 s) of the programme:
  * every cut is classed from the frames either side of it:
      step  the picture after is a zoom of the picture before (a crop of the same take)
      jump  the same framing continues (a jump cut, no step)
      new   anything else: a new picture
  * speech edits (paired only): a cut at a join in the source's sound, pinned to
    +-0.2 s by edit_delta's per-frame map (fine_joins), else the 3 s run interval.
    A step on a speech edit hides the jump and is allowed, one per edit.
  * placed sounds (paired only): hits of sound that the aligned source does not have,
    counted only where the source at that instant (each 20 ms frame aligned on its
    own) is not speech-shaped; hits within 1 s are one gesture (edit_delta.audio_added).
  * flashes; paired, a flash the upload already has at that source time is not counted.
  * asked (TASTE 4.5): devices the customer asked for are out of every count. Which
    departments or moments were asked is read from the request by a model (the
    Scout's ledger, or the judge reading it), passed as {dept: "all" | [times]}.
The supply class (TASTE 4.1) comes from new pictures per 10 s (NP10) and the
programme length; `bed` is added sound over half the programme.

Ceilings (TASTE 5.4):
  unearned_devices   any window over the class allowance of TASTE 4.2 by a factor of 2   cap 3
  worse_than_upload  a harm of TASTE 4.4 in the delivery and not in the upload            cap 2
                     measured: h7 density (any window over the allowance, paired only);
                     the judge may also declare it (h1-h6, h8, h9 seen on the board)
  type_said_twice, type_outside_safe_area   declared by the judge                         cap 6
"""
import math

import cv2
import numpy as np

CAPS = {"unearned_devices": 3.0, "worse_than_upload": 2.0,
        "type_said_twice": 6.0, "type_outside_safe_area": 6.0}
WIN, HOP = 10.0, 5.0


def _ncc_best(big, small):
    if small.shape[0] < 8 or small.shape[1] < 8 or small.shape[0] > big.shape[0] or small.shape[1] > big.shape[1]:
        return -1.0
    r = cv2.matchTemplate(big, small, cv2.TM_CCOEFF_NORMED)
    return float(r.max())


def cut_kind(a, b):
    """a, b: grayscale frames (float or uint8) either side of a cut."""
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    h, w = a.shape
    if min(h, w) < 24:
        return "new", 0.0
    a8, b8 = a.astype(np.uint8), b.astype(np.uint8)
    if a.std() < 4 or b.std() < 4:          # a flat frame: a card, black, a flash
        return "new", 0.0
    # Same framing: correlation over a small shift search (handheld, a nudge).
    m = max(2, int(0.06 * min(h, w)))
    direct = _ncc_best(a8, b8[m:h - m, m:w - m])
    best, best_s = -1.0, 1.0
    for s in np.geomspace(1.05, 1.7, 14):
        th, tw = int(round(h / s)), int(round(w / s))
        # b is a zoom-in of a: b shrunk by 1/s is a window of a (and the reverse for a zoom-out)
        c1 = _ncc_best(a8, cv2.resize(b8, (tw, th), interpolation=cv2.INTER_AREA)[1:-1, 1:-1])
        c2 = _ncc_best(b8, cv2.resize(a8, (tw, th), interpolation=cv2.INTER_AREA)[1:-1, 1:-1])
        c = max(c1, c2)
        if c > best:
            best, best_s = c, float(s)
    if direct >= 0.80 and direct >= best - 0.03:
        return "jump", direct
    if best >= 0.72 and best >= direct + 0.05:
        return "step", best_s
    return "new", best


def cut_kinds(gray, fps, cuts):
    """[(t, kind, value)] for every cut, from cut_analysis's 96 px grey frames."""
    out = []
    if gray is None or len(gray) < 4:
        return [(float(t), "new", 0.0) for t in cuts]
    n = len(gray)
    for t in cuts:
        k = int(round(t * fps))
        i0, i1 = max(0, k - 2), min(n - 1, k + 1)
        if i1 <= i0:
            out.append((float(t), "new", 0.0)); continue
        kind, v = cut_kind(gray[i0], gray[i1])
        out.append((float(t), kind, round(float(v), 3)))
    return out



def summarise(prog, kinds, flashes, speech_joins=None, sounds=None,
              added_pct=None, paired=False, bed=None, over_voice=None):
    """Measurement only. No taste quotas, quality score or density ceiling."""
    kinds = [k for k in kinds if k[0] < prog]
    joins = sorted(tuple(j) for j in speech_joins or [])
    used, off = set(), []
    for t, kind, _ in kinds:
        if kind != "step":
            continue
        match = next((j for j in joins if j not in used and j[0] - .35 <= t <= j[1] + .35), None)
        if match is None:
            off.append(t)
        else:
            used.add(match)
    audible = [t for t in sounds or [] if t < prog] if paired else []
    return {"new_pictures": sum(k == "new" for _, k, _ in kinds),
            "off_edit_steps": len(off), "sound_times": audible,
            "raw": {"prog": prog, "kinds": kinds, "sounds": audible,
                    "flashes": [f for f in flashes if f < prog]}}
