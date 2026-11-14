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


def apply(l1, index, perception):
    if not index.get("_first_film_source_guard"):
        return []
    v = index["video"]
    ratio = (l1["frame"][0] / l1["frame"][1]) * (v["height"] / v["width"])
    w, h = max(1., ratio), max(1., 1 / ratio)
    full = [(1 - w) / 2, (1 - h) / 2, (1 + w) / 2, (1 + h) / 2]
    samples = list((index.get("spatial") or {}).get("samples") or [])
    samples += (perception or {}).get("samples") or []
    shots = index.get("shots") or [{"start": 0, "end": v["duration"]}]
    report = []
    for item in l1.get("items") or []:
        if item.get("pic") != "main" or not item.get("src"):
            continue
        start, end = item["src"]
        if start < 0 or end > v["duration"] + 1e-6:
            raise ValueError("first film reads outside cleared source footage")
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
        if missing:
            item["crop"] = [list(full) for _ in rects] if track else list(full)
            item["source_full_frame"] = True
            reason = "full_frame_missing_subject_boxes"
        else:
            # A subject wholly outside a proposed crop must count too.
            # Keep the authored move only if every observed subject fits.
            safe = all(c[0] <= b[0] and c[1] <= b[1] and c[2] >= b[2]
                       and c[3] >= b[3] for c in rects for b in boxes)
            if not safe:
                item["crop"] = [list(full) for _ in rects] if track else list(full)
                item["source_full_frame"] = True
            reason = "subject_boxes_contained" if safe else "full_frame_subject_outside_crop"
        report.append({"id": item["id"], "source": [start, end], "reason": reason,
                       "subject_boxes": len(boxes), "crop": item.get("crop")})
    return report
