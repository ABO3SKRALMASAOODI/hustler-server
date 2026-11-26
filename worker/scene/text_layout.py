"""Seat the final composed words after palette expansion, on their rendered ink."""

from collections import defaultdict
from itertools import groupby
import math

import numpy as np

from .palette.kit import MARK, backplate_box, item_box, metrics
from .motion import Cam
from .plan import A, AR
from .runtime import plane_hom


def shared_source_credits(views):
    """One continuous publisher credit across adjacent views of the same cited event."""
    runs = []
    for _, group in groupby(views, key=lambda view: (view[2], view[3])):
        run = []
        for view in group:
            if not run or run[-1][1] != view[0]:
                run = []
                runs.append(run)
            run.append(view)
    credits = []
    for run in runs:
        if len(run) > 1:
            events = {view[4] for view in run if view[4]}
            label = events.pop() if len(events) == 1 else run[0][3]
            credits.append((run[0][0], run[-1][1], label))
    return credits


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
    return project(card["proof_line"]) if card.get("proof_line") and page[3] - page[1] > .74 else page


def speaker_seats(scene, metadata):
    """Keep the active tracked face inside the final animated crop, including device moves."""
    from bisect import bisect_left
    seats = {}
    width, height = scene["frame"]
    for shot in scene["items"]:
        meta = metadata.get(shot.get("pic"))
        panel = shot.get("speaker_panel")
        if (shot["k"] != "shot" and not panel) or not meta or not meta.get("faces"):
            continue
        lo, hi = shot["f"]
        w, h = (shot["w"], shot["h"]) if panel else (1., 1.)
        cam = Cam(meta["pw"], meta["ph"], width*w/(height*h), width*w, meta.get("max_up", 3))
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
            face = [(x-l)/(right-l),(y-top)/(bot-top),(r-l)/(right-l),(bottom-top)/(bot-top)]
            if panel:
                scale = A(shot.get("s",1),f,lo)
                cx, cy = A(shot["x"],f,lo), A(shot["y"],f,lo)
                face = [cy+(q-.5)*h*scale if i%2 else cx+(q-.5)*w*scale for i,q in enumerate(face)]
            seats[f] = face
        shot["crop"] = crops
    return seats


def share_display_copy(items):
    """Give identical lettering one owner per frame across caption, display and palette layers."""
    used = defaultdict(dict)
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
            if owner != origin and not (owner[1] == origin[1] == "cap"):
                if changed is None:
                    changed = [A(alpha,q,lo) for q in range(lo,hi)]
                changed[f-lo] = 0
        if changed is not None:
            it["op"] = changed


def resolve(scene, speaker_layout=None):
    share_display_copy(scene["items"])
    width, height = scene["frame"]
    voices = scene["voices"]
    # Palette underlines are drawn on the measured glyphs before the final layout.
    # Preserve their position within the line through every later translation and scale.
    hosts = {it["id"]: it for it in scene["items"] if it["k"] == "word"}
    underlines = []
    for effect in scene["items"]:
        word = hosts.get(effect.get("underline_for"))
        if word:
            frames = range(*effect["f"])
            before = [item_box(word, voices, width, height, f) for f in frames]
            underlines.append((effect, word, frames, before))
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
    # A speaker panel opened for a stack reserves the strip directly beneath
    # its live image. Follow the measured edge through the pullback and release.
    panel_edges = defaultdict(dict)
    for card in scene["items"]:
        if card.get("panel_type"):
            lo, hi = card["f"]
            panel_edges[card["panel_type"]].update({
                f: A(card["y"],f,lo) + card["h"]*A(card.get("s",1),f,lo)/2
                for f in range(lo,hi)
            })
    overflow = set()
    active = defaultdict(list)
    document_baselines = {}
    for layer in scene["items"]:
        if layer.get("source_ink") or layer.get("proof_line") or layer.get("source_frame"):
            lo, hi = layer["f"]
            for f in range(lo, hi):
                if A(layer.get("op", 1), f, lo) > .01:
                    occupied[f].extend(layer.get("source_ink", []))
                    if "caption_baseline" in layer:
                        document_baselines[f] = layer["caption_baseline"]
                    if layer.get("proof_line") or layer.get("source_frame"):
                        occupied[f].append(proof_bounds(layer, f, width, height))
    fixed = {f: list(boxes) for f,boxes in occupied.items()}
    # Choose the reading baseline for the continuous interview picture. Word
    # timings and horizontal positions still come from the original page.
    reading_seat = {}
    for _, run in groupby(enumerate(sorted(faces)), lambda pair: pair[1]-pair[0]):
        frames = [frame for _,frame in run]
        baseline = min(.91, max(.79, max(faces[f][3] for f in frames)+.045))
        reading_seat.update(dict.fromkeys(frames, baseline))

    # A caption may start on the face and finish on the source document. Carry
    # the whole page to the document's reading seat, including its earlier words.
    document_pages = {}
    if document_baselines:
        for token in scene["items"]:
            if token.get("role") == "cap" and "chunk" in token:
                seats = [document_baselines[f] for f in range(*token["f"]) if f in document_baselines]
                if seats:
                    chunk = token["chunk"]
                    document_pages[chunk] = max(document_pages.get(chunk, 0), max(seats))

    def ink_box(layer, f):
        measure = backplate_box if layer.get("backplate") else item_box
        return measure(layer, voices, width, height, f)

    words, groups = [], {}
    for layer in scene["items"]:
        glyph = layer["k"] == "join" and layer.get("mode") == "glyph"
        if not glyph and layer["k"] != "word":
            continue
        group = layer.get("text_group")
        caption = not glyph and layer.get("role") == "cap"
        if caption:
            layer["contrast_shadow"] = True
        if caption and "chunk" in layer:
            lo, hi = layer["f"]
            if layer["chunk"] in document_pages:
                layer["y"] = document_pages[layer["chunk"]]
            elif lo in reading_seat:
                layer["y"] = reading_seat[lo]
            group = ("caption", layer["chunk"])
        if caption and group is None:
            # The authored line types on word by word. All of its words leave on
            # the same frame and must share a translation, even across a cut.
            group = ("caption", layer["f"][1], layer.get("v"))
        if group is None and not glyph:
            words.append(layer)
            continue
        key = group or layer["id"]
        g = groups.setdefault(
            key, {"id": str(key), "role": "cap" if caption else "registered", "layers": [], "boxes": {}}
        )
        g["layers"].append(layer)
        if caption and "chunk" in layer and (layer["chunk"] in document_pages or layer["f"][0] in reading_seat):
            g["reading"] = True
        lo, hi = layer["f"]
        measure = (
            dict(layer, k="word", x=layer.get("x0", 0.5), y=layer.get("y0", 0.5))
            if glyph
            else layer
        )
        for f in range(lo, hi):
            if A(layer.get("a" if glyph else "op", 1), f, lo) <= 0.01:
                continue
            box = ink_box(measure, f)
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
            return ink_box(it, f)
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
            -2 if it.get("reading") else {"registered": -1, "cap": 0, "big": 2, "stat": 3}.get(it.get("role"), 1),
            it["f"][0],
            -it.get("z", 0),
            it["id"],
        )
    )
    for it in words:
        lo, hi = it["f"]
        boxes = {}
        display_on_take = it.get("role") == "big" and any(f in faces for f in range(lo, hi))
        if it.get("role") == "cap" and "layers" in it:
            # Fit the whole spoken page once, including accent overshoot and stroke.
            # A page crossing a picture cut keeps the same scale on both shots.
            extents = it["boxes"].values()
            left, right = min(b[0] for b in extents), max(b[2] for b in extents)
            if left < .03 or right > .97:
                scale = min(1., .94 / max(right-left, 1e-6))
                x = max(.03-(.5+(left-.5)*scale), min(0., .97-(.5+(right-.5)*scale)))
                centre_y = (min(b[1] for b in extents)+max(b[3] for b in extents))/2
                it.update(s=scale, x=x, y=(centre_y-.5)*(1-scale))
        if display_on_take:
            # Measure the moving ink, including the overshoot of a palette word.
            # Leave an editorial margin on both sides of a full-width headline.
            extents = [frame_box(it, f) for f in range(lo, hi)]
            widest = max((box[2] - box[0] for box in extents if box), default=0)
            if widest > .86:
                base = it.get("s", 1)
                it["s"] = [A(base, f, lo) * .86 / widest for f in range(lo, hi)]
        for f in range(lo, hi):
            if A(it.get("op", 1), f, lo) > 0.01:
                box = frame_box(it, f)
                if box:
                    boxes[f] = box
                    active[f].append(it)
        if not boxes:
            continue

        panel = panel_edges.get(it["id"], {})
        def available(frames):
            intervals = []
            low, high = -1.0, 1.0
            for f in frames:
                x0, y0, x1, y1 = boxes[f]
                top_margin = MARK[3] + .018 if display_on_take else .04
                top_margin = max(top_margin, panel.get(f, 0) + .008)
                low, high = max(low, top_margin - y0), min(high, .94 - y1)
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
        if shift is None and it.get("role") == "registered":
            # Fit the complete moving lockup in one seat across its lifetime.
            # Scale about the centre of its ink, including the entrance overshoot.
            cx = (min(b[0] for b in boxes.values())+max(b[2] for b in boxes.values()))/2
            cy = (min(b[1] for b in boxes.values())+max(b[3] for b in boxes.values()))/2
            original = boxes
            for size in (.90, .80, .70, .60, .50):
                boxes = {f:tuple(c+(q-c)*size for q,c in zip(b,(cx,cy,cx,cy))) for f,b in original.items()}
                shift = available(boxes)
                if shift is not None:
                    it.update(s=size, x=(cx-.5)*(1-size), y=(cy-.5)*(1-size))
                    break
            else:
                boxes = original
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
            cx, cy = (x0+x1)/2, (y0+y1)/2
            tx, ty = A(it.get("x",0),f,lo), A(it.get("y",0),f,lo)
            # A group's scale pivots at screen centre; a single sprite scales at its own ink centre.
            if "layers" in it:
                cx = .5+(cx-.5-tx)*scale+tx
                cy = .5+(cy-.5-ty)*scale+ty
            for key, val in (
                ("s", A(it.get("s", 1), f, lo) * scale),
                ("x", tx + .5 - cx),
                (
                    "y",
                    ty
                    + begin
                    + (row + 0.5) * slot
                    - cy,
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
            # A word sprite scales about its ink centre, not its pen. Move that
            # centre through the group transform, preserving the layer's own motion.
            px, py = metrics(voices[layer["v"]], layer["t"])["c"]
            pivot = {"x":px/width, "y":py/height}
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
                        else 0.5 + (n + pivot[coord] - 0.5) * scale - pivot[coord] + A(g.get(offset, 0), f, origin)
                    )
                layer[key] = values[0] if min(values) == max(values) else values

    for effect, word, frames, before in underlines:
        rects = []
        for f, old in zip(frames, before):
            rect = AR(effect["rect"], f, frames.start)
            new = item_box(word, voices, width, height, f)
            if old and new and old[2] > old[0] and old[3] > old[1]:
                rect = [new[i%2] + (value-old[i%2])*(new[i%2+2]-new[i%2])/(old[i%2+2]-old[i%2])
                        for i,value in enumerate(rect)]
            rects.append(rect)
        effect["rect"] = rects
    # Keep etched footage recognizable and its overlapping titles legible. Bound the
    # line treatment to an open band between the final text and cited source regions.
    for effect in scene["items"]:
        if effect.get("k") != "flash" or effect.get("mode") != "line":
            continue
        lo, hi = effect["f"]
        curves = []
        for f in range(lo, hi):
            left, top, right, bottom = AR(effect.get("rect"), f, lo) or [0.,0.,1.,1.]
            exclusions = list(fixed.get(f, []))
            exclusions += [frame_box(layer, f) for layer in active.get(f, [])]
            intervals = sorted((max(top,t-.015), min(bottom,b+.015)) for l,t,r,b in exclusions
                               if left < r and l < right and top < b+.015 and t-.015 < bottom)
            gaps, start = [], top
            for t,b in intervals:
                if start < t:
                    gaps.append((start,t))
                start = max(start,b)
            gaps.append((start,bottom))
            t,b = max(gaps, key=lambda span: span[1]-span[0])
            curves.append([left,t,right,b])
        if curves:
            effect["rect"] = curves[0] if all(c==curves[0] for c in curves) else curves
