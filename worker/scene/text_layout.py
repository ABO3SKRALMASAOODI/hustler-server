"""Seat the final composed words after palette expansion, on their rendered ink."""

from collections import defaultdict
import math

import numpy as np

from .palette.kit import item_box
from .plan import A
from .runtime import plane_hom


def proof_bounds(card, frame, width, height):
    """The projected evidence page, or its cited line when the camera fills the phone."""
    at = card["f"][0]
    x, y, scale, rot, rx, ry = (A(card.get(k, v), frame, at)
                                 for k, v in (("x", .5), ("y", .5), ("s", 1), ("rot", 0), ("rx", 0), ("ry", 0)))
    transform = plane_hom(x*width, y*height, scale, math.radians(rot), rx, ry, width, height, card.get("persp", 1.2))
    def project(box):
        l, t, r, b = box
        corners = np.array([[(u-.5)*card["w"]*width, (v-.5)*card["h"]*height, 1]
                            for u, v in ((l,t), (r,t), (l,b), (r,b))]) @ transform.T
        xy = corners[:, :2] / np.maximum(corners[:, 2:], 1e-6) / [width, height]
        return (*xy.min(axis=0), *xy.max(axis=0))
    page = project([0, 0, 1, 1])
    return project(card["proof_line"]) if page[3] - page[1] > .74 else page


def share_display_copy(items):
    """Give identical lettering one owner per frame across caption, display and palette layers."""
    used = defaultdict(dict)
    # A grouped device (a counter's steps, a typewriter's states, a reading card's rows) is one performance: its own
    # lettering is never hidden here, and an ungrouped display copy of the same text gives way to it on the frames
    # it shows (26 Nov: a counter and a display row of the same figure were both on screen). The caption keeps its
    # seat: the palette hands it off on the device's own clock.
    grouped = defaultdict(set)
    for it in items:
        if it.get("k") == "word" and it.get("t") and it.get("text_group"):
            lo, hi = it["f"]
            alpha = it.get("op", 1)
            grouped[" ".join(it["t"].split()).casefold()].update(
                f for f in range(max(lo, it.get("on", lo)), hi) if A(alpha, f, lo) > .01)
    words = [it for it in items if it.get("k") == "word" and it.get("t") and not it.get("text_group")]
    # The caption already belongs to its sentence. Retain it in that seat when a second
    # type device happens to request exactly the same word or figure.
    for it in sorted(words, key=lambda row: (row.get("role") != "cap", -row.get("z", 0))):
        text = " ".join(it["t"].split()).casefold()
        origin = it.get("v"), it.get("role")
        lo, hi = it["f"]
        alpha = it.get("op", 1)
        changed = None
        for f in range(max(lo, it.get("on",lo)), hi):
            if A(alpha,f,lo) <= .01:
                continue
            owner = used[text].setdefault(f, origin)
            if (owner != origin and not (owner[1] == origin[1] == "cap")) or (f in grouped[text] and origin[1] != "cap"):
                if changed is None:
                    changed = [A(alpha,q,lo) for q in range(lo,hi)]
                changed[f-lo] = 0
        if changed is not None:
            it["op"] = changed


def resolve(scene):
    share_display_copy(scene["items"])
    width, height = scene["frame"]
    voices = scene["voices"]
    occupied = defaultdict(list)
    overflow = set()
    active = defaultdict(list)
    for layer in scene["items"]:
        if layer.get("source_ink") or layer.get("proof_line"):
            lo, hi = layer["f"]
            for f in range(lo, hi):
                if A(layer.get("op", 1), f, lo) > .01:
                    occupied[f].extend(layer.get("source_ink", []))
                    if layer.get("proof_line"):
                        occupied[f].append(proof_bounds(layer, f, width, height))
    words, groups = [], {}
    for layer in scene["items"]:
        glyph = layer["k"] == "join" and layer.get("mode") == "glyph"
        if not glyph and layer["k"] != "word":
            continue
        group = layer.get("text_group")
        if group is None and not glyph:
            words.append(layer)
            continue
        key = group or layer["id"]
        g = groups.setdefault(
            key, {"id": key, "role": "registered", "layers": [], "boxes": {}}
        )
        g["layers"].append(layer)
        lo, hi = layer["f"]
        measure = (
            dict(layer, k="word", x=layer.get("x0", 0.5), y=layer.get("y0", 0.5))
            if glyph
            else layer
        )
        for f in range(lo, hi):
            if A(layer.get("a" if glyph else "op", 1), f, lo) <= 0.01:
                continue
            box = item_box(measure, voices, width, height, f)
            if box:
                old = g["boxes"].get(f, box)
                g["boxes"][f] = (
                    min(old[0], box[0]),
                    min(old[1], box[1]),
                    max(old[2], box[2]),
                    max(old[3], box[3]),
                )
    for g in groups.values():
        if g["boxes"]:
            g["f"] = [min(g["boxes"]), max(g["boxes"]) + 1]
            words.append(g)

    def frame_box(it, f):
        if "layers" not in it:
            return item_box(it, voices, width, height, f)
        box = it["boxes"].get(f)
        if box:
            lo = it["f"][0]
            dx, dy, scale = (
                A(it.get(k, v), f, lo) for k, v in (("x", 0), ("y", 0), ("s", 1))
            )
            return tuple(
                0.5 + (n - 0.5) * scale + (dy if i % 2 else dx)
                for i, n in enumerate(box)
            )

    # One constant translation over an item's life whenever possible. Earlier
    # words at the same priority keep their seat as the next word types on.
    words.sort(
        key=lambda it: (
            {"registered": -1, "cap": 0, "big": 2, "stat": 3}.get(it.get("role"), 1),
            it["f"][0],
            -it.get("z", 0),
            it["id"],
        )
    )
    for it in words:
        lo, hi = it["f"]
        boxes = {}
        for f in range(lo, hi):
            if A(it.get("op", 1), f, lo) > 0.01:
                box = frame_box(it, f)
                if box:
                    boxes[f] = box
                    active[f].append(it)
        if not boxes:
            continue

        def available(frames):
            intervals = []
            low, high = -1.0, 1.0
            for f in frames:
                x0, y0, x1, y1 = boxes[f]
                low, high = max(low, 0.04 - y0), min(high, 0.94 - y1)
                for left, top, right, bottom in occupied[f]:
                    if x0 < right and left < x1:
                        intervals.append((top - y1 - 0.006, bottom - y0 + 0.006))
            if not any(a < 0 < b for a, b in intervals):
                return 0.0
            candidates = {low, high}
            for a, b in intervals:
                candidates.update((a, b))
            return next(
                (
                    v
                    for v in sorted(candidates, key=abs)
                    if low <= v <= high and all(not a < v < b for a, b in intervals)
                ),
                None,
            )

        shift = available(boxes)
        # A crowded moving composition can require a different seat on a cut.
        dy = {}
        for f, (x0, y0, x1, y1) in boxes.items():
            offset = shift if shift is not None else available([f])
            if offset is None:
                # A full-width stack fills the vertical area. Try a free column.
                for dx in sorted(
                    {
                        edge
                        for l, t, r, b in occupied[f]
                        for edge in (l - x1 - 0.006, r - x0 + 0.006)
                    },
                    key=abs,
                ):
                    if (
                        0.01 <= x0 + dx
                        and x1 + dx <= 0.99
                        and all(
                            x1 + dx <= l or r <= x0 + dx or y1 <= t or b <= y0
                            for l, t, r, b in occupied[f]
                        )
                    ):
                        if not isinstance(it.get("x"), list):
                            it["x"] = [it.get("x", 0)] * (hi - lo)
                        it["x"][f - lo] += dx
                        x0, x1 = x0 + dx, x1 + dx
                        break
                else:
                    overflow.add(f)
                offset = 0.0
            dy[f] = offset
            # Leave a line of breathing room around display lettering at phone size.
            margin = 0.027 if "layers" in it else 0.0
            occupied[f].append((x0, y0 + offset - margin, x1, y1 + offset + margin))
        if any(dy.values()):
            ys = [A(it.get("y", 0), f, lo) + dy.get(f, 0.0) for f in range(lo, hi)]
            it["y"] = ys[0] if min(ys) == max(ys) else ys
        it["contrast_shadow"] = it.get("role") in ("cap", "stat", "big")

    # When neither axis has room, stack the whole frame in priority order.
    # Preserve each word's motion at all other frames.
    for f in sorted(overflow):
        stack = active[f]
        slot = 0.86 / len(stack)
        for row, it in enumerate(stack):
            x0, y0, x1, y1 = frame_box(it, f)
            scale = min(
                1.0, 0.93 * slot / max(y1 - y0, 1e-6), 0.94 / max(x1 - x0, 1e-6)
            )
            lo, hi = it["f"]
            for key, val in (
                ("s", A(it.get("s", 1), f, lo) * scale),
                ("x", A(it.get("x", 0), f, lo) + 0.5 - (x0 + x1) / 2),
                (
                    "y",
                    A(it.get("y", 0), f, lo)
                    + 0.05
                    + (row + 0.5) * slot
                    - (y0 + y1) / 2,
                ),
            ):
                if not isinstance(it.get(key), list):
                    it[key] = [it.get(key, 1 if key == "s" else 0)] * (hi - lo)
                it[key][f - lo] = val

    # Move all layers of a stencil by the same transform. Separating the dark
    # cutout, bright rim and live footage mask makes one word read as two.
    for g in groups.values():
        if not any(k in g for k in ("x", "y", "s")):
            continue
        origin = g["f"][0]
        for layer in g["layers"]:
            lo, hi = layer["f"]
            for coord, offset in (("x", "x"), ("y", "y"), ("s", "s")):
                key = coord + "0" if layer["k"] == "join" and coord != "s" else coord
                base = layer.get(key, 1 if key == "s" else 0)
                values = []
                for f in range(lo, hi):
                    scale = A(g.get("s", 1), f, origin)
                    n = A(base, f, lo)
                    values.append(
                        n * scale
                        if key == "s"
                        else 0.5 + (n - 0.5) * scale + A(g.get(offset, 0), f, origin)
                    )
                layer[key] = values[0] if min(values) == max(values) else values
