"""First-film source geometry: no speculative crop without shot-local subjects."""


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
             if s.get("t") and s["t"][0] < end and s["t"][1] > start]
    if not shots or any(s.get("change") is None or s["change"] > .01 or
                        s.get("faces_seen") is None or s["faces_seen"] > 0
                        for s in shots):
        return False
    boxes = [r["box"] for r in per.get("text") or []
             if r.get("box") and len(r["box"]) == 4 and r.get("t") and
             r["t"][0] < end and r["t"][1] > start and r.get("seen", 0) >= 3]
    return any(b[2] - b[0] > width + .005 or b[3] - b[1] > height + .005
               for b in boxes)


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
            or not any(s["start"] <= start and end <= s["end"] for s in shots)):
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


def apply(l1, index, perception, *, views=None):
    if not index.get("_first_film_source_guard"):
        return []
    v = index["video"]
    ratio = (l1["frame"][0] / l1["frame"][1]) * (v["height"] / v["width"])
    w, h = min(1., ratio), min(1., 1 / ratio)
    def centered(point):
        x = min(1. - w / 2, max(w / 2, point[0]))
        y = min(1. - h / 2, max(h / 2, point[1]))
        return [x - w / 2, y - h / 2, x + w / 2, y + h / 2]
    full = centered((.5, .5))
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
    report = []
    for item in l1.get("items") or []:
        if item.get("pic") != "main" or not item.get("src"):
            continue
        start, end = item["src"]
        if start < 0 or end > end_limit + 1e-6:
            raise ValueError(f"first film reads outside cleared source footage: "
                             f"{start:.6f}-{end:.6f}, limit {end_limit:.6f}")
        if static_text_needs_fit(perception, start, end, w, h):
            # Lowering already supports a source rectangle extending past
            # the canvas: the on-spine source is fitted over its blurred
            # backdrop. Do not override that geometry with a saliency crop.
            fit_w, fit_h = max(1., ratio), max(1., 1 / ratio)
            item["crop"] = [.5 - fit_w / 2, .5 - fit_h / 2,
                            .5 + fit_w / 2, .5 + fit_h / 2]
            item["source_full_frame"] = True
            palette = (perception or {}).get("palette") or []
            item["source_fit_background"] = palette[0].get("hex", "#202020") if palette else "#202020"
            report.append({"id": item["id"], "source": [start, end],
                           "reason": "static_source_text_fit", "crop": item["crop"]})
            continue
        overlaps = [s for s in shots if s["start"] < end and s["end"] > start]
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
                nearby = sorted(local, key=lambda s: (abs(s["t"]-t), not bool(_boxes(s))))
                if not nearby:
                    nearby = sorted((s for s in samples if shot and
                        shot["start"] <= s["t"] < shot["end"] and s.get("focus")),
                        key=lambda s: abs(s["t"]-t))
                row = nearby[0] if nearby and _boxes(nearby[0]) else None
                if row is not None:
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
                points.append(centered(point))
            item["crop"] = points if track else points[0]
        item.pop("source_full_frame", None)
        report.append({"id": item["id"], "source": [start, end], "reason": reason,
                       "subject_boxes": len(boxes), "crop": item.get("crop")})
    return report
