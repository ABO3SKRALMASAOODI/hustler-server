"""First-film source geometry: no speculative crop without shot-local subjects."""


def _overlaps(start, end, lo, hi):
    # A held frame has src=[t,t]. It belongs to the shot beginning at a
    # cut, never the preceding shot; an empty interval overlap loses it.
    return lo <= start < hi if start == end else lo < end and hi > start


def _boxes(sample):
    boxes = list(sample.get("faces") or [])
    for key in ("face", "person", "body"):
        box = sample.get(key)
        if isinstance(box, (list, tuple)) and len(box) == 4:
            boxes.append(box)
    return [b for b in boxes if len(b) == 4 and 0 <= b[0] < b[2] <= 1
            and 0 <= b[1] < b[3] <= 1]


def missing_shot_boxes(index, keep):
    """The fallback reframer may not infer a subject across source cuts."""
    if not index.get("_first_film_source_guard"):
        return False
    samples = (index.get("spatial") or {}).get("samples") or []
    shots = index.get("shots") or [{"start": 0, "end": index["video"]["duration"]}]
    return any(not any(s["start"] <= row["t"] < s["end"] and _boxes(row)
                       for row in samples)
               for s in shots if any(s["start"] < b and s["end"] > a for a, b in keep))


def static_text_needs_fit(perception, start, end, width, height):
    """A measured still with persistent text wider than the possible crop.

    This is geometry, not an interpretation of the slide or its words. A
    missing motion reading is not evidence that the source is a still.
    """
    per = perception or {}
    shots = [s for s in per.get("shots") or []
             if s.get("t") and _overlaps(start, end, *s["t"])]
    if not shots or any(s.get("change") is None or s["change"] > .01 or
                        s.get("faces_seen") is None or s["faces_seen"] > 0
                        for s in shots):
        return False
    boxes = [r["box"] for r in per.get("text") or []
             if r.get("box") and len(r["box"]) == 4 and r.get("t") and
             _overlaps(start, end, *r["t"]) and r.get("seen", 0) >= 3]
    return any(b[2] - b[0] > width + .005 or b[3] - b[1] > height + .005
               for b in boxes)


# Printed text a crop must keep whole: a line the Director's perception saw
# in this many frames, or a printed LINE (one row of text, its words joined
# across word gaps) wider than the crop window in at least this share of the
# interval's samples (a screen recording's UI rows, a slide's lines, an
# upload's own subtitles). Separate pieces of text far apart on the frame
# (a channel bug and a date stamp, HUD corners, two street signs) are not
# one line: each fits a crop on its own and the footage keeps its crop.
TEXT_SEEN = 3
SPREAD_SHARE = .6
SPREAD_ROWS = 3
SPREAD_AROUND_S = 4.0
ROW_OVERLAP = .5        # two boxes share a row: this share of the lower one's height
WORD_GAP_H = 1.2        # ... and sit at most this many line heights apart


def _line_boxes(row):
    """A spatial sample's line-shaped text boxes (geometry only)."""
    out = []
    for b in row.get("text") or []:
        try:
            x0, y0, x1, y1 = (float(v) for v in b[:4])
        except (TypeError, ValueError):
            continue
        w, h = x1 - x0, y1 - y0
        if w > 0 and 0 < h <= .2:
            out.append((x0, y0, x1, y1))
    return out


def printed_lines(row):
    """The sample's text boxes joined into printed lines: boxes on one row
    (vertical overlap) whose horizontal gap is a word gap (a line height or
    so). Returns [(x0, y0, x1, y1)]. Geometry only."""
    boxes = sorted(_line_boxes(row))
    lines = []
    for b in boxes:
        for i, l in enumerate(lines):
            h = min(b[3] - b[1], l[3] - l[1])
            overlap = min(b[3], l[3]) - max(b[1], l[1])
            gap = b[0] - l[2]
            if overlap >= ROW_OVERLAP * h and gap <= WORD_GAP_H * max(
                    b[3] - b[1], l[3] - l[1]):
                lines[i] = (min(l[0], b[0]), min(l[1], b[1]),
                            max(l[2], b[2]), max(l[3], b[3]))
                break
        else:
            lines.append(b)
    return lines


def text_needs_fit(perception, start, end, width, height, samples=None):
    """Measured printed text wider (or taller) than the possible crop, in
    footage with no person to frame: the whole picture is fitted instead.

    Geometry, not an interpretation of the words. It holds for a still slide
    and for a moving screen recording alike (p6312: a 16:9 tutorial whose
    subtitle line and UI rows were centre-cropped to 32% of their width). A
    face or a missing face reading keeps the subject crop, and so does text
    in pieces that each fit the crop.
    """
    per = perception or {}
    shots = [s for s in per.get("shots") or []
             if s.get("t") and _overlaps(start, end, *s["t"])]
    if any(s.get("faces_seen") is None or s["faces_seen"] > 0 for s in shots):
        return False
    def within(a, b):
        return [r for r in samples or [] if r.get("t") is not None
                and a <= float(r["t"]) <= b]
    rows = within(start, end) if end > start else within(start - .75, start + .75)
    if sum(1 for r in rows if "text" in r) < SPREAD_ROWS:
        # Index samples sit seconds apart; a one-second piece reads the
        # printed layout around it (a page or a screen holds for seconds).
        mid = (start + end) / 2
        rows = within(min(start, mid - SPREAD_AROUND_S), max(end, mid + SPREAD_AROUND_S))
    if any(_boxes(r) for r in rows):
        return False
    if not shots and not (rows and all("faces" in r for r in rows)):
        return False
    boxes = [r["box"] for r in per.get("text") or []
             if r.get("box") and len(r["box"]) == 4 and r.get("t") and
             _overlaps(start, end, *r["t"]) and r.get("seen", 0) >= TEXT_SEEN]
    if any(b[2] - b[0] > width + .005 or b[3] - b[1] > height + .005
           for b in boxes):
        return True
    read = [r for r in rows if "text" in r]      # samples that measured text
    wide = sum(1 for r in read if any(
        l[2] - l[0] > width + .005 or l[3] - l[1] > height + .005
        for l in printed_lines(r)))
    return len(read) >= SPREAD_ROWS and wide >= SPREAD_SHARE * len(read)


def kept_text_needs_fit(index, keep=None, ratio="9:16"):
    """text_needs_fit over the footage an edit keeps, for a frame of
    ``ratio``: True when the source text a crop would cut runs through at
    least half of the kept seconds. The legacy first-cut chain and the
    fallback reframer read the same indexed measurements as the Director."""
    index = index or {}
    prepared = index.get("director_preparation") or {}
    per = prepared.get("perception") if prepared.get("version") == 1 else None
    video = index.get("video") or {}
    try:
        fw, fh = (float(n) for n in str(ratio).split(":"))
        relative = (fw / fh) * (float(video["height"]) / float(video["width"]))
        spans = [(float(a), float(b)) for a, b in keep or []] or \
            [(0., float(video["duration"]))]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    if relative >= 1.:
        return False            # the frame is wider than the source: no side crop
    rows = (index.get("spatial") or {}).get("samples") or []
    need = sum(b - a for a, b in spans if text_needs_fit(
        per, a, b, min(1., relative), min(1., 1 / relative), rows))
    return need > 0 and need >= .5 * sum(b - a for a, b in spans)



def _named_detail(item, views, rects, ratio, shots, boxes):
    """Keep the model's shot-local detail when its measured geometry is safe.

    Saliency is not a subject decision: desktop chrome can be more salient
    than the preview that the editor selected. A named view carries the
    existing Supply model's box and source interval, not a new model call.
    """
    view = (views or {}).get(str(item.get("view")))
    if not view:
        return False
    box = view.get("box") or []
    start, end = item["src"]
    if (len(box) != 4 or not 0 <= box[0] < box[2] <= 1
            or not 0 <= box[1] < box[3] <= 1
            or start < view["t0"] - .01 or end > view["t1"] + .01
            or (start == end and not view["t0"] <= start < view["t1"])
            or not any(s["start"] <= start and end <= s["end"] and
                       _overlaps(start, end, s["start"], s["end"]) for s in shots)):
        return False
    for c in rects:
        if not (0 <= c[0] < c[2] <= 1 and 0 <= c[1] < c[3] <= 1
                and abs((c[2]-c[0])/(c[3]-c[1])-ratio) < .005
                and c[0] <= box[0] and c[1] <= box[1]
                and c[2] >= box[2] and c[3] >= box[3]):
            return False
        # An intentional detail may omit another subject, but cannot slice
        # through a subject that intersects its window.
        for b in boxes:
            intersects = min(c[2], b[2]) > max(c[0], b[0]) and min(c[3], b[3]) > max(c[1], b[1])
            if intersects and not (c[0] <= b[0] and c[1] <= b[1]
                                   and c[2] >= b[2] and c[3] >= b[3]):
                return False
    return True


def reading_window(row, ratio, subject, view=None):
    """Frame a measured line or named detail with its adjacent lines and subjects."""
    lines = printed_lines({"text": row.get("text_geometry")})
    if not lines or not row.get("visible_text") or ratio >= 1:
        return None
    # Edge detectors also outline windows and photographs. A text line has
    # a baseline and is wider than it is tall in the original landscape.
    lines = [b for b in lines if b[2]-b[0] > 1.4*(b[3]-b[1])]
    if not lines:
        return None
    target = view or max(lines, key=lambda b: b[2]-b[0])
    cx = (target[0]+target[2])/2
    if row.get("dense_ui"):
        # A column of chat and the sidebar are separate reading surfaces.
        # A named dialog selects its column even when it is off centre.
        lines = [b for b in lines if b[0] <= cx <= b[2]]
    boxes = lines + [target] + subject
    return enclosing_window(boxes, ratio, minimum=.22)


def enclosing_window(boxes, ratio, minimum):
    """A portrait window can move off centre and grow to include both people."""
    left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
    top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
    width = min(1., max(minimum, right-left+.025, ratio*(bottom-top+.05)))
    height = width / ratio
    x = max(0., min(1.-width, (left+right-width)/2))
    y = max(0., min(1.-height, (top+bottom-height)/2)) if height <= 1 else (1.-height)/2
    return [x, y, x+width, y+height]


def apply(l1, index, perception, *, views=None):
    if not index.get("_first_film_source_guard"):
        return []
    v = index["video"]
    frame_ratio = (l1["frame"][0] / l1["frame"][1]) * (v["height"] / v["width"])
    def centered(point, ratio):
        w, h = min(1., ratio), min(1., 1 / ratio)
        x = min(1. - w / 2, max(w / 2, point[0]))
        y = min(1. - h / 2, max(h / 2, point[1]))
        return [x - w / 2, y - h / 2, x + w / 2, y + h / 2]
    samples = list((index.get("spatial") or {}).get("samples") or [])
    samples += (perception or {}).get("samples") or []
    shots = index.get("shots") or [{"start": 0, "end": v["duration"]}]
    end_limit = float(v["duration"])
    evidence = index.get("source_screen") or {}
    # The compiler quantizes its spine to output frames. At a fully cleared
    # EOF, the normal centisecond EDL tolerance cannot expose later footage.
    # Match the renderer's final boundary gate; never extend a trimmed tail.
    if (evidence.get("status") == "verified" and
            end_limit >= float(evidence.get("source_duration_s") or float("inf"))):
        end_limit += .01
    board = [r for r in (index.get("visual_storyboard") or {}).get("evidence", [])
             if r.get("representative_t") is not None and "visible_text" in r]
    report = []
    for item in l1.get("items") or []:
        if item.get("pic") != "main" or not item.get("src"):
            continue
        # A card is a separately framed picture. Its uniform settle/scale
        # animation changes size, not aspect. Using the film's aspect here
        # silently replaced a safe landscape inset with a portrait crop.
        ratio = frame_ratio
        if item.get("k") == "card":
            ratio *= float(item.get("w", .5)) / float(item.get("h", .5))
        w, h = min(1., ratio), min(1., 1 / ratio)
        full = centered((.5, .5), ratio)
        start, end = item["src"]
        if start < 0 or end > end_limit + 1e-6:
            raise ValueError(f"first film reads outside cleared source footage: "
                             f"{start:.6f}-{end:.6f}, limit {end_limit:.6f}")
        overlaps = [s for s in shots if _overlaps(start, end, s["start"], s["end"])]
        boxes, missing = [], not overlaps
        for shot in overlaps:
            # Never borrow the nearest face from across a cut.
            local = [b for s in samples if shot["start"] <= s["t"] < shot["end"]
                     for b in _boxes(s)]
            missing |= not local
            boxes.extend(local)
        crop = item.get("crop") or full
        track = isinstance(crop[0], (list, tuple))
        rects = crop if track else [crop]
        # The output stays full-bleed. A missing indexed box is not permission
        # to shrink the entire source onto black. Perception measures faces,
        # people and saliency on the actual decoded frames, without a model.
        safe = (not missing and all(0 <= c[0] < c[2] <= 1 and
                0 <= c[1] < c[3] <= 1 and abs((c[2]-c[0])/(c[3]-c[1])-ratio) < .005 and all(c[0] <= b[0] and c[1] <= b[1]
                and c[2] >= b[2] and c[3] >= b[3] for b in boxes) for c in rects))
        detail = _named_detail(item, views, rects, ratio, shots, boxes)
        safe = safe or detail
        reason = "named_detail_crop" if detail else "subject_boxes_contained"
        # A verified named view is already a model-selected source detail;
        # unrelated desktop headings must not replace it with the whole screen.
        mid = (start + end) / 2
        readings = [r for r in board if any(sh["start"] <= r["representative_t"] < sh["end"]
                                             for sh in overlaps)]
        scene = min(readings, key=lambda r: abs(r["representative_t"] - mid), default=None)
        if scene and item.get("k") == "shot" and not detail:
            view = (views or {}).get(str(item.get("view"))) or {}
            region = view.get("box") if view.get("t0", end+1) <= mid <= view.get("t1", -1) else None
            subjects = _boxes(scene)
            crop = reading_window(scene, ratio, subjects, region)
            if crop:
                item["crop"] = crop
                item["source_full_frame"] = crop[1] < 0
                report.append({"id": item["id"], "source": [start, end],
                               "reason": "measured_reading_window", "crop": crop})
                continue
        if not detail and scene is None and text_needs_fit(perception, start, end, w, h, samples):
            # Lowering already supports a source rectangle extending past
            # the canvas: the on-spine source is fitted over its blurred
            # backdrop. Do not override that geometry with a saliency crop.
            fit_w, fit_h = max(1., ratio), max(1., 1 / ratio)
            item["crop"] = [.5 - fit_w / 2, .5 - fit_h / 2,
                            .5 + fit_w / 2, .5 + fit_h / 2]
            item["source_full_frame"] = True
            report.append({"id": item["id"], "source": [start, end],
                           "reason": "static_source_text_fit", "crop": item["crop"]})
            continue
        if not safe:
            count = len(rects)
            if not track and item.get("f") and len(item["f"]) == 2:
                count = max(1, int(item["f"][1]-item["f"][0]))
                track = count > 1
            points = []
            for n in range(count):
                t = start + (end-start) * ((n + .5) / count if track else .5)
                shot = next((sh for sh in overlaps if sh["start"] <= t < sh["end"]), None)
                local = [s for s in samples if shot and shot["start"] <= s["t"] < shot["end"]
                         and abs(s["t"]-t) <= .75 and (_boxes(s) or s.get("focus"))]
                nearby = sorted(local, key=lambda s: (not bool(_boxes(s)), abs(s["t"]-t)))
                if not nearby:
                    nearby = sorted((s for s in samples if shot and
                        shot["start"] <= s["t"] < shot["end"] and (_boxes(s) or s.get("focus"))),
                        key=lambda s: (not bool(_boxes(s)), abs(s["t"]-t)))
                row = nearby[0] if nearby and _boxes(nearby[0]) else None
                if row is not None:
                    if scene and ratio < 1. and item.get("k") == "shot":
                        subjects = _boxes(row)
                        points.append(enclosing_window(subjects, ratio, minimum=ratio))
                        reason = "local_subject_crop"
                        continue
                    box = max(_boxes(row), key=lambda b: (b[2]-b[0])*(b[3]-b[1]))
                    point = [(box[0]+box[2])/2, (box[1]+box[3])/2]
                    reason = "local_subject_crop"
                elif nearby and nearby[0].get("focus"):
                    point = nearby[0]["focus"]
                    reason = "local_saliency_crop"
                else:
                    known = [s for s in samples if _boxes(s) and s["t"] <= t]
                    if not known:
                        raise ValueError("first-film crop awaits local subject or saliency evidence")
                    box = max(_boxes(max(known, key=lambda s:s["t"])),
                              key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
                    point = [(box[0]+box[2])/2, (box[1]+box[3])/2]
                    reason = "last_known_subject_crop"
                points.append(centered(point, ratio))
            item["crop"] = points if track else points[0]
        item.setdefault("crop", full)
        actual = item["crop"] if isinstance(item["crop"][0], (list, tuple)) else [item["crop"]]
        if any(c[1] < -.001 or c[3] > 1.001 for c in actual):
            item["source_full_frame"] = True
        else:
            item.pop("source_full_frame", None)
        report.append({"id": item["id"], "source": [start, end], "reason": reason,
                       "subject_boxes": len(boxes), "crop": item.get("crop")})
    return report
