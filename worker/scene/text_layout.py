"""Seat the final composed words after palette expansion, on their rendered ink."""

from collections import defaultdict
import math

import numpy as np

from .palette.kit import MARK, item_box
from .motion import Cam
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


def speaker_seats(scene, metadata):
    """Keep the active tracked face inside the final animated crop, including device moves."""
    from bisect import bisect_left
    seats = {}
    width, height = scene["frame"]
    for shot in scene["items"]:
        meta = metadata.get(shot.get("pic"))
        if shot["k"] != "shot" or not meta or not meta.get("faces"):
            continue
        lo, hi = shot["f"]
        cam = Cam(meta["pw"], meta["ph"], width / height, width, meta.get("max_up", 3))
        track = meta.get("track") or []
        times = [row[0] for row in track]
        fx, fy, fw, fh = max(meta["faces"], key=lambda r:r[2]*r[3])[:4]
        crops = []
        for f in range(lo, hi):
            if track:
                a, b = shot.get("src", [lo/scene["fps"], hi/scene["fps"]])
                t = a + (b-a)*(f-lo)/(hi-lo)
                j = min(bisect_left(times,t),len(track)-1)
                prev = max(0,j-1)
                u = min(1,max(0,(t-times[prev])/max(1e-6,times[j]-times[prev])))
                x,y,r,bottom = [v+(w-v)*u for v,w in zip(track[prev][1:],track[j][1:])]
            else:
                x,y,r,bottom = fx,fy,fx+fw,fy+fh
            crop = shot.get("crop") or [0,0,1,1]
            l,top,right,bot = crop[f-lo] if isinstance(crop[0],list) else crop
            # Anchor even when a palette pan has already taken the head's centre outside the crop.
            cx,cy = (x+r)/2,(y+bottom)/2
            if not (l<cx<right and top<cy<bot):
                l,top,right,bot = cam.rect(cx,cy,right-l)
            l,top,right,bot = cam.keep_whole([l,top,right,bot],[(x,y,r-x,bottom-y)])
            crops.append([l,top,right,bot])
            seats[f] = [(x-l)/(right-l),(y-top)/(bot-top),(r-l)/(right-l),(bottom-top)/(bot-top)]
        shot["crop"] = crops
    return seats


def resolve(scene, speaker_layout=None):
    width, height = scene["frame"]
    voices = scene["voices"]
    occupied = defaultdict(lambda: [MARK])
    faces = speaker_seats(scene, speaker_layout or {})
    for frame, face in faces.items():
        occupied[frame].append(face)
    for card in scene["items"]:
        if not card.get("inset_seat"):
            continue
        lo, hi = card["f"]
        if not any(f in faces for f in range(lo, hi)):
            continue
        # Keep the incoming tile in its own band until the cut hands it the screen.
        bottom = max(face[3] for f, face in faces.items() if lo <= f < hi)
        top = min(face[1] for f, face in faces.items() if lo <= f < hi)
        lower = .96-bottom > top-MARK[3]
        edge = bottom+.015 if lower else top-.015
        room = max(.01, .98-edge if lower else edge-MARK[3]-.01)
        scales = [min(A(card.get("s",1),f,lo),room/card["h"]) for f in range(lo,hi)]
        card.update(s=scales, y=[edge + (1 if lower else -1)*card["h"]*s/2 for s in scales])
        for f, scale in zip(range(lo,hi),scales):
            x,y = A(card["x"],f,lo), A(card["y"],f,lo)
            w,h = card["w"]*scale/2,card["h"]*scale/2
            occupied[f].append((x-w,y-h,x+w,y+h))
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
    fixed = {f: list(boxes) for f,boxes in occupied.items()}
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
            if low <= 0 <= high and not any(a < 0 < b for a, b in intervals):
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
        # Make a column in the largest vertical band left by fixed faces, evidence and the mark.
        free = []
        top = .05
        for _, y0, _, y1 in sorted(fixed.get(f, [MARK]), key=lambda box: box[1]):
            if y0 > top:
                free.append((top, min(.94,y0-.006)))
            top = max(top,y1+.006)
        if top < .94:
            free.append((top,.94))
        begin,end = max(free, key=lambda r:r[1]-r[0], default=(.05,.94))
        slot = (end-begin) / len(stack)
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
                    + begin
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
