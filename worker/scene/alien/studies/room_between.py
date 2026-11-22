"""Pull the room tone apart along its own waveform to expose the listener inside."""

import itertools

import cv2
import numpy as np

from .. import sense
from . import material as m


def quiet_gap(level, lo, hi):
    floor = max(0.09, float(np.percentile(level[lo:hi], 28)) * 1.8)
    quiet = np.r_[False, level[lo:hi] < floor, False].astype(int)
    starts = np.flatnonzero(np.diff(quiet) == 1)
    ends = np.flatnonzero(np.diff(quiet) == -1)
    if not len(starts):
        return lo, hi
    i = np.argmax(ends - starts)
    return lo + int(starts[i]), lo + int(ends[i])


def plan(l1, work, ask):
    fps, (w, h) = l1["fps"], l1["frame"]
    lo, hi = ask["beats"]
    level = sense.level(l1, work)
    quiet_start, quiet_end = quiet_gap(
        level, max(0, lo - int(0.3 * fps)), min(l1["dur_f"], hi + int(0.25 * fps))
    )
    # A breath can lie entirely inside the reverse angle. Find the original
    # cut first, so the two skins of the opening actually belong to the speaker.
    begin, end = max(1, lo - round(0.55 * fps)), min(l1["dur_f"], lo + round(0.55 * fps))
    stream = m.take_frames(l1, work, begin - 1, end)
    # Keep the matching full-resolution frames from this decode. Seeking the
    # compressed source a second time can land one frame inside the new shot.
    _, cut, before, after = max(
        (
            (cv2.absdiff(cv2.resize(prev, (72, 40)), cv2.resize(curr, (72, 40))).mean(), frame, prev, curr)
            for frame, (prev, curr) in enumerate(itertools.pairwise(stream), begin)
        ),
        key=lambda candidate: candidate[0],
    )
    a = max(0, cut - 2)
    b = min(hi, cut + max(round(0.72 * fps), quiet_end - quiet_start))
    z = min(l1["dur_f"], b + round(0.22 * fps))
    shot = sense.shot_at(l1, quiet_start)
    raw = sense._band(
        sense.path_of(l1, work, shot["pic"]),
        sense.src_time(shot, quiet_start, fps),
        (quiet_end - quiet_start) / fps,
    )
    rms = np.array([np.sqrt(np.mean(x * x)) for x in np.array_split(raw, min(180, len(raw)))])
    contour = np.interp(np.linspace(0, 1, w), np.linspace(0, 1, len(rms)), rms)
    contour = h * (0.46 + 0.16 * contour / max(0.00001, np.percentile(contour, 95)))
    yy = np.arange(h)[:, None]

    def pictures():
        for f, live in zip(range(a, z), m.take_frames(l1, work, a, z)):
            opening = float(m.ease((f - a) / max(1, b - a))) * h * 0.78
            dy = int(opening)
            out = live.copy()
            # Both halves are cut from the outgoing photograph. The live reverse angle
            # remains in sync behind them; the top and bottom leave the frame entirely.
            for sign in (-1, 1):
                off = sign * dy
                mask = (yy < contour + off) if sign < 0 else (yy >= contour + off)
                mask &= (yy - off >= 0) & (yy - off < h)
                out = np.where(mask[:, :, None], np.roll(before, off, axis=0), out)
                if 0 < dy < h * 0.7:
                    points = np.int32(np.column_stack([np.arange(w), contour + off]))
                    cv2.polylines(out, [points], False, (216, 197, 160), max(1, w // 650), cv2.LINE_AA)
            yield out

    return m.plate(
        l1,
        work,
        ask,
        a,
        z,
        pictures(),
        {
            "quiet_frames": [quiet_start, quiet_end],
            "accent_frame": a + round((b - a) * 0.50),
            "reverse_cut": cut,
            "open_frame": b,
            "mean_reverse_delta": round(float(cv2.absdiff(before, after).mean()), 2),
        },
    )
