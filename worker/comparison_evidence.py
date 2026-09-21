"""Reuse immutable upload storyboard pixels for broad media comparisons.

Exact-timestamp/native-resolution looks still decode the requested source.
This path reports the real indexed clocks, never relabels a nearby frame as
the requested clock, and falls back when the persisted evidence is incomplete.
"""

import hashlib
import math
import os

from PIL import Image

import storage


def indexed_frames(index, sample_times, workdir):
    storyboard = (index or {}).get("visual_storyboard") or {}
    if storyboard.get("version") != 1:
        return None
    locations = {}
    for sheet in storyboard.get("sheets") or []:
        ids = sheet.get("evidence_ids") or []
        if sheet.get("key"):
            for slot, evidence_id in enumerate(ids):
                locations[evidence_id] = (sheet["key"], slot, len(ids))
    candidates = []
    for row in storyboard.get("evidence") or []:
        try:
            at = float(row["representative_t"])
        except (KeyError, TypeError, ValueError):
            continue
        evidence_id = row.get("evidence_id")
        if math.isfinite(at) and at >= 0 and evidence_id in locations:
            candidates.append((at, evidence_id))
    if not candidates:
        return None
    selected = []
    for target in sample_times:
        if not candidates:
            break  # An index may collapse static footage to fewer clusters.
        nearest = min(candidates, key=lambda row: abs(row[0] - target))
        selected.append(nearest)
        candidates.remove(nearest)
    selected.sort()
    frames = []
    try:
        os.makedirs(workdir, exist_ok=True)
        for at, evidence_id in selected:
            key, slot, count = locations[evidence_id]
            digest = hashlib.sha256(key.encode()).hexdigest()[:24]
            sheet_path = os.path.join(workdir, f"comparison_sheet_{digest}.jpg")
            frame_path = os.path.join(workdir, f"comparison_{digest}_{slot}.jpg")
            if not os.path.isfile(frame_path):
                if not os.path.isfile(sheet_path):
                    storage.download_to(key, sheet_path)
                with Image.open(sheet_path) as sheet:
                    # v1 has 400x225 frames and a 34px label, in 2 or 3
                    # columns. Derive columns from the immutable image, not
                    # today's configurable page size. Reject unknown layouts.
                    cols = sheet.width // 400
                    if (cols not in (2, 3) or sheet.width != cols * 400
                            or sheet.height != math.ceil(count / cols) * 259):
                        return None
                    x, y = slot % cols * 400, slot // cols * 259
                    sheet.crop((x, y, x + 400, y + 225)).convert("RGB").save(
                        frame_path, "JPEG", quality=90)
            with Image.open(frame_path) as frame:
                frame.verify()
            frames.append((at, frame_path, evidence_id))
    except Exception:
        # Object-store misses and corrupt/partial cache files are optional
        # cache failures. The caller decodes the source through its usual path.
        return None
    return frames
