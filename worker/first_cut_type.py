"""Place automatically chosen captions clear of the upload's printed words.

The first-cut chain chooses its own anchor; it is not a customer-pinned
coordinate. Use the existing spatial measurements and final camera geometry
before passing that anchor to add_captions. No words or font sizes change.
"""
import math

import caption_place
import keepout
from timeline import Timeline


def place(edl, index, style, W, H):
    """Return a stable anchor when source text occupies the automatic one.

    Keep a clear original anchor. If it collides, use a band that fits the
    unchanged caption block and clears every sampled face and source-text
    box. A crowded frame stays explicit failure evidence, never missing text.
    """
    y = style.get("anchor_y")
    if y is None or not edl.get("keep"):
        return dict(style), {"moved": False, "reason": "no automatic anchor"}
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed") or [])
    duration = float(tl.out_duration)
    if duration <= 0:
        return dict(style), {"moved": False, "reason": "empty timeline"}
    measured = dict(edl, captions={"mode": "from_transcript", "style": style})
    one, tall, motion = caption_place.block_metrics(measured, W, H)
    lo, hi = caption_place.natural_block(float(y), one, tall, motion)
    col = caption_place.column(W, H)
    text = []
    # Bound placement work for whole-upload edits, while covering the full
    # program rather than silently checking only its opening minute.
    steps = min(keepout.MAX_TRACK, max(2, math.ceil(duration * 2)))
    for i in range(steps):
        t = max(0.0, duration - .001) * i / (steps - 1)
        # text_boxes maps source words through zooms and picture windows,
        # unlike a source-coordinate band score at one shot midpoint.
        text.extend(caption_place.text_boxes(edl, index, tl, W, H, t, t))
    if not caption_place.hits(lo, hi, text, col):
        return dict(style), {"moved": False, "reason": "source text clear"}
    faces = keepout.zones_of(keepout.face_track(edl, index, W, H, 0, duration))
    mark = caption_place.watermark_box(W, H, edl)
    zones = text + faces + ([mark] if mark else [])
    safe = (.10, .80) if H / max(W, 1) >= 1.6 else (.04, .94)
    choices = []
    above = one / 2 if motion else tall / 2
    for a, b in caption_place.free_bands(zones, safe, col):
        # Leave air above and below the unchanged reading block.
        a, b = a + .01, b - .01
        if b - a < tall:
            continue
        anchor = min(max(float(y), a + above), b - tall + above)
        choices.append((abs(anchor - float(y)), anchor))
    if not choices:
        return dict(style), {"moved": False, "clear": False,
                             "reason": "no band fits unchanged captions"}
    anchor = round(min(choices)[1], 4)
    return dict(style, anchor_y=anchor), {
        "moved": True, "clear": True, "from": y, "to": anchor,
        "reason": "automatic caption block clears sampled source text and faces"}
