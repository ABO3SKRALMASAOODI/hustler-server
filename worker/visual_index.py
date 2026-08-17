"""Hierarchical, content-addressed visual evidence for video editing.

Uniform clocks are useful for scrubbing but waste model context: a static
talking-head can occupy scores of frames while a short cutaway is missed.
This module selects evidence from editorial boundaries, measures the pixels,
clusters near-duplicates, and emits labeled contact sheets plus a complete
text storyboard.  No distinct cluster is discarded.  Provider-sized batches
are transport pages only; the persisted logical storyboard is unbounded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from concurrent import futures

import numpy as np
from PIL import Image, ImageDraw

import config
import llm
import storage
import tiles as tilestrip


VISUAL_STORYBOARD_VERSION = 1


def _value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _clock(value):
    value = max(0.0, float(value or 0.0))
    minute, second = divmod(value, 60.0)
    hour, minute = divmod(int(minute), 60)
    if hour:
        return f"{hour}:{minute:02d}:{second:05.2f}"
    return f"{minute}:{second:05.2f}"


def _candidate_times(duration, shots, motion, spatial, sentences):
    """Return every meaningful sampling instant with provenance reasons."""
    duration = max(0.0, float(duration or 0.0))
    ceiling = max(0.0, duration - 0.03)
    rows = {}

    def add(at, reason):
        try:
            at = round(min(ceiling, max(0.0, float(at))), 2)
        except (TypeError, ValueError):
            return
        # Decode once when several evidence sources point at the same moment.
        key = next((old for old in rows if abs(old - at) <= 0.16), at)
        rows.setdefault(key, set()).add(reason)

    add(min(0.08, ceiling), "asset_start")
    add(ceiling, "asset_end")
    for shot in shots or []:
        start = float(_value(shot, "start", 0) or 0)
        end = float(_value(shot, "end", start) or start)
        shot_id = int(_value(shot, "id", 0) or 0)
        add(start + min(0.10, max(0.0, end - start) / 3),
            f"shot_{shot_id}_start")
        add((start + end) / 2, f"shot_{shot_id}_midpoint")
        if end - start >= 2.0:
            add(end - 0.10, f"shot_{shot_id}_end")
    for at in (motion or {}).get("change_times_s") or []:
        add(at, "measured_motion_change")

    # Face/text/UI layout changes are already measured on a sparse clock.
    previous = None
    for sample in (spatial or {}).get("samples") or []:
        signature = (len(sample.get("faces") or []),
                     len(sample.get("text") or []),
                     bool(sample.get("dense_ui")))
        if signature != previous:
            add(sample.get("t"), "face_text_layout_change")
            previous = signature

    # Speech-aligned frames allow semantic descriptions to be tied back to
    # what is being said. Deduplication happens after decoding, so repeated
    # talking-head sentences collapse instead of consuming model images.
    for sentence in sentences or []:
        start = float(_value(sentence, "t0", 0) or 0)
        end = float(_value(sentence, "t1", start) or start)
        add((start + end) / 2, "transcript_aligned")
    return [(at, sorted(reasons)) for at, reasons in sorted(rows.items())]


def _dhash_and_layout(path):
    with Image.open(path) as opened:
        image = opened.convert("RGB")
        gray = np.asarray(image.resize((9, 8), Image.Resampling.BILINEAR)
                          .convert("L"), dtype=np.int16)
        bits = (gray[:, 1:] > gray[:, :-1]).reshape(-1)
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        layout = np.asarray(
            image.resize((4, 4), Image.Resampling.BILINEAR),
            dtype=np.float32).reshape(-1) / 255.0
        content = np.asarray(
            image.resize((32, 18), Image.Resampling.BILINEAR),
            dtype=np.uint8).tobytes()
    frame_hash = hashlib.sha256(content).hexdigest()
    return value, layout, frame_hash


def _hamming(left, right):
    return int(left ^ right).bit_count()


def _nearest_spatial(spatial, at):
    samples = (spatial or {}).get("samples") or []
    if not samples:
        return {}
    row = min(samples, key=lambda item:
              abs(float(item.get("t", 0)) - float(at)))
    if abs(float(row.get("t", 0)) - float(at)) > 8.0:
        return {}
    return row


def _shot_for(shots, at):
    for shot in shots or []:
        start = float(_value(shot, "start", 0) or 0)
        end = float(_value(shot, "end", start) or start)
        if start <= at <= end + 0.02:
            return (int(_value(shot, "id", 0) or 0), start, end)
    return (0, at, at)


def _geometry_compatible(left, right):
    # Identical-looking pixels with a different face/UI measurement should
    # remain separate evidence: that difference can change crop safety.
    return (len(left.get("faces") or []) == len(right.get("faces") or [])
            and len(left.get("text") or []) == len(right.get("text") or [])
            and bool(left.get("dense_ui")) == bool(right.get("dense_ui")))


def _covered_ranges(members):
    spans = sorted((float(row["shot_t0"]), float(row["shot_t1"]))
                   for row in members)
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 0.20:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [[round(a, 2), round(b, 2)] for a, b in merged]


def _cluster_candidates(candidates, paths, shots, spatial, motion):
    representatives = []
    for index, (at, reasons) in enumerate(candidates):
        path = paths.get(index)
        if not path:
            continue
        try:
            dhash, layout, frame_hash = _dhash_and_layout(path)
        except Exception:
            continue
        shot_id, shot_t0, shot_t1 = _shot_for(shots, at)
        measured = _nearest_spatial(spatial, at)
        member = {"t": at, "reasons": reasons, "shot_id": shot_id,
                  "shot_t0": shot_t0, "shot_t1": shot_t1}
        found = None
        for cluster in representatives:
            if _hamming(dhash, cluster["_dhash"]) > 7:
                continue
            layout_delta = float(np.mean(np.abs(layout - cluster["_layout"])))
            if layout_delta > 0.075:
                continue
            if not _geometry_compatible(measured, cluster["_spatial"]):
                continue
            found = cluster
            break
        if found is not None:
            found["_members"].append(member)
            found["selection_reasons"] = sorted(set(
                found["selection_reasons"] + reasons))
            continue
        evidence_id = "ve_" + frame_hash[:16]
        representatives.append({
            "evidence_id": evidence_id,
            "cluster_id": "vc_" + frame_hash[:12],
            "representative_t": at,
            "source_clock": _clock(at),
            "shot_id": shot_id,
            "frame_hash": frame_hash,
            "selection_reasons": reasons,
            "faces": measured.get("faces") or [],
            "text_geometry": measured.get("text") or [],
            "dense_ui": bool(measured.get("dense_ui")),
            "composition": {
                "mean_luma": measured.get("mean_luma"),
                "std_luma": measured.get("std_luma"),
            },
            "motion_state": (motion or {}).get("intensity") or "unknown",
            "subjects": [], "actions": [], "objects": [], "setting": "",
            "visible_text": "", "semantic_description": "",
            # A compact deterministic retrieval vector. Semantic fields from
            # the captioner are searched alongside it by callers.
            "retrieval_embedding": [round(float(value), 4)
                                    for value in layout.tolist()],
            "_path": path, "_dhash": dhash, "_layout": layout,
            "_spatial": measured, "_members": [member],
        })

    for cluster in representatives:
        members = cluster.pop("_members")
        cluster["covered_ranges"] = _covered_ranges(members)
        cluster["covered_time_range"] = [
            min(row[0] for row in cluster["covered_ranges"]),
            max(row[1] for row in cluster["covered_ranges"]),
        ]
        cluster["shot_ids"] = sorted({row["shot_id"] for row in members})
        cluster["sample_count_collapsed"] = len(members)
    return representatives


def _build_sheets(evidence, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    per_sheet = max(1, int(config.VISUAL_SHEET_FRAMES or 6))
    cols = 3 if per_sheet > 4 else 2
    frame_w, frame_h, label_h = 400, 225, 34
    font = tilestrip._font(14)
    sheets = []
    for offset in range(0, len(evidence), per_sheet):
        group = evidence[offset:offset + per_sheet]
        rows = int(math.ceil(len(group) / cols))
        canvas = Image.new("RGB", (cols * frame_w,
                                    rows * (frame_h + label_h)), (10, 10, 10))
        draw = ImageDraw.Draw(canvas)
        for slot, row in enumerate(group):
            x = slot % cols * frame_w
            y = slot // cols * (frame_h + label_h)
            try:
                with Image.open(row["_path"]) as opened:
                    image = opened.convert("RGB")
                    image.thumbnail((frame_w, frame_h))
                    canvas.paste(image, (x + (frame_w - image.width) // 2,
                                         y + (frame_h - image.height) // 2))
            except Exception:
                pass
            label = f'{row["evidence_id"]}  SOURCE {row["source_clock"]}'
            draw.text((x + 6, y + frame_h + 7), label,
                      fill=(238, 238, 238), font=font)
        digest = hashlib.sha256("|".join(
            row["frame_hash"] for row in group).encode()).hexdigest()[:20]
        path = os.path.join(out_dir, f"visual_{offset // per_sheet:04d}_{digest}.jpg")
        canvas.save(path, "JPEG", quality=84)
        sheets.append({"path": path, "digest": digest,
                       "evidence_ids": [row["evidence_id"] for row in group],
                       "t0": min(row["covered_time_range"][0] for row in group),
                       "t1": max(row["covered_time_range"][1] for row in group)})
    return sheets


def _cache_key(frame_hash):
    return f"visual-evidence/frames/v{VISUAL_STORYBOARD_VERSION}/{frame_hash}.json"


def _read_cached_semantics(frame_hash, workdir):
    key = _cache_key(frame_hash)
    if not storage.exists(key):
        return None
    local = os.path.join(workdir, f"semantic_{frame_hash[:20]}.json")
    try:
        storage.download_to(key, local)
        with open(local, "r", encoding="utf-8") as handle:
            row = json.load(handle)
        return row if row.get("frame_hash") == frame_hash else None
    except Exception:
        return None


def _write_cached_semantics(row, workdir):
    frame_hash = row["frame_hash"]
    local = os.path.join(workdir, f"semantic_{frame_hash[:20]}.json")
    try:
        with open(local, "w", encoding="utf-8") as handle:
            json.dump(row, handle, separators=(",", ":"), sort_keys=True)
        storage.upload_file(local, _cache_key(frame_hash), "application/json")
    except Exception as exc:
        print(f"[visual-index] semantic cache write skipped: {exc}", flush=True)


def _semantic_prompt(rows):
    ids = ", ".join(row["evidence_id"] for row in rows)
    return (
        "You are indexing visual evidence for a professional video editor. "
        "Each contact-sheet frame is burned with its evidence_id and SOURCE "
        "clock. Return ONLY a JSON array, one object for every ID, in this "
        f"set: {ids}. Fields: evidence_id, subjects (short string array), "
        "actions (short string array), objects (short string array), setting "
        "(string), visible_text (exact text when readable), semantic_description "
        "(one concrete sentence), composition (one concise sentence). Never "
        "invent unreadable text or off-screen facts.")


def _apply_semantic(row, semantic):
    for key in ("subjects", "actions", "objects"):
        value = semantic.get(key)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            row[key] = [str(item)[:120] for item in value[:12]]
    for key in ("setting", "visible_text", "semantic_description"):
        if semantic.get(key) is not None:
            row[key] = str(semantic[key])[:1200]
    composition = semantic.get("composition")
    if composition:
        row.setdefault("composition", {})["semantic"] = str(composition)[:500]


def _caption_semantics(evidence, sheets, workdir):
    cached = {}
    with futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(evidence)))) as pool:
        jobs = {pool.submit(_read_cached_semantics, row["frame_hash"], workdir): row
                for row in evidence}
        for job, row in jobs.items():
            value = job.result()
            if value:
                cached[row["evidence_id"]] = value
                _apply_semantic(row, value)

    missing = {row["evidence_id"]: row for row in evidence
               if row["evidence_id"] not in cached}
    if not missing:
        return "cached"
    if not llm.vision_available():
        return "raw_contact_sheets_required"

    per_call = max(1, int(config.VISUAL_SEMANTIC_SHEETS_PER_CALL or 8))
    any_success = False
    for offset in range(0, len(sheets), per_call):
        page = sheets[offset:offset + per_call]
        rows = [missing[evidence_id]
                for sheet in page for evidence_id in sheet["evidence_ids"]
                if evidence_id in missing]
        if not rows:
            continue
        paths = [sheet["path"] for sheet in page]
        names = [f'visual-sheet:{sheet["digest"]}' for sheet in page]
        answer = llm.ask_vision(
            _semantic_prompt(rows), paths,
            max_tokens=max(900, len(rows) * 140),
            purpose="visual_index_semantics", image_names=names,
            reasoning_effort="low")
        if not answer:
            # ask_vision switches to the configured agent-vision fallback
            # after a proven blind provider; a second attempt also covers a
            # transient first-provider failure without making indexing fatal.
            answer = llm.ask_vision(
                _semantic_prompt(rows), paths,
                max_tokens=max(900, len(rows) * 140),
                purpose="visual_index_semantics_fallback", image_names=names,
                reasoning_effort="low")
        parsed = llm.extract_json_array(answer)
        if not parsed:
            continue
        by_id = {str(item.get("evidence_id")): item for item in parsed
                 if isinstance(item, dict) and item.get("evidence_id")}
        for row in rows:
            semantic = by_id.get(row["evidence_id"])
            if not semantic:
                continue
            _apply_semantic(row, semantic)
            cache_row = {key: row.get(key) for key in (
                "frame_hash", "subjects", "actions", "objects", "setting",
                "visible_text", "semantic_description", "composition")}
            _write_cached_semantics(cache_row, workdir)
            any_success = True
    remaining = [row for row in evidence if not row.get("semantic_description")]
    if remaining:
        return ("partial_raw_contact_sheets_required" if any_success else
                "raw_contact_sheets_required")
    return "complete"


def _chapters(evidence, sentences, duration):
    """Create navigable program groups without dropping any evidence IDs."""
    if not evidence:
        return []
    ordered = sorted(evidence, key=lambda row: row["representative_t"])
    groups, current = [], []
    for row in ordered:
        if current and (len(current) >= 10 or
                        row["representative_t"] - current[0]["representative_t"] >= 75):
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)
    chapters = []
    for index, rows in enumerate(groups, start=1):
        t0 = min(row["covered_time_range"][0] for row in rows)
        t1 = max(row["covered_time_range"][1] for row in rows)
        transcript = " ".join(str(_value(sentence, "text", ""))
                              for sentence in sentences or []
                              if float(_value(sentence, "t1", 0) or 0) >= t0
                              and float(_value(sentence, "t0", 0) or 0) <= t1)
        descriptions = [row.get("semantic_description") for row in rows
                        if row.get("semantic_description")]
        chapters.append({
            "chapter_id": f"chapter_{index:03d}", "t0": round(t0, 2),
            "t1": round(min(float(duration or t1), t1), 2),
            "evidence_ids": [row["evidence_id"] for row in rows],
            "transcript_excerpt": transcript[:1000],
            "description": " ".join(descriptions[:3])[:1000],
        })
    return chapters


def build(src_path, duration, shots, motion, spatial, sentences, sha,
          workdir, seek_ceiling=None):
    """Build and upload the full visual storyboard. Failures are non-fatal."""
    candidates = _candidate_times(duration, shots, motion, spatial, sentences)
    frame_dir = os.path.join(workdir, "visual_evidence_frames")
    paths = tilestrip.extract_frames(
        src_path, [row[0] for row in candidates], frame_dir,
        seek_ceiling=seek_ceiling,
        parallelism=max(1, config.VISUAL_FRAME_PARALLELISM), width=640)
    evidence = _cluster_candidates(candidates, paths, shots, spatial, motion)
    sheets = _build_sheets(evidence, os.path.join(workdir, "visual_sheets"))
    semantic_status = _caption_semantics(evidence, sheets, workdir)

    uploaded = []
    for page, sheet in enumerate(sheets, start=1):
        key = (f"visual-evidence/storyboards/v{VISUAL_STORYBOARD_VERSION}/"
               f"{sha}/{sheet['digest']}.jpg")
        if not storage.exists(key):
            storage.upload_file(sheet["path"], key, "image/jpeg")
        uploaded.append({"page": page, "key": key,
                         "evidence_ids": sheet["evidence_ids"],
                         "t0": round(sheet["t0"], 2),
                         "t1": round(sheet["t1"], 2)})

    # Runtime-only image/signature fields must never enter PostgreSQL/context.
    public = []
    for row in evidence:
        public.append({key: value for key, value in row.items()
                       if not key.startswith("_")})
    return {
        "version": VISUAL_STORYBOARD_VERSION,
        "selection": "shot_motion_layout_transcript_boundaries",
        "semantic_status": semantic_status,
        "candidate_frames": len(candidates),
        "distinct_clusters": len(public),
        "duplicates_collapsed": max(0, len(candidates) - len(public)),
        "sheets": uploaded,
        "sheet_keys": [row["key"] for row in uploaded],
        "evidence": public,
        "chapters": _chapters(public, sentences, duration),
    }


def compact_text(storyboard, include_all=True):
    """Stable text inventory retained after pixels leave active context."""
    if not storyboard:
        return ""
    lines = [
        (f"VISUAL STORYBOARD v{storyboard.get('version')}: "
         f"{storyboard.get('distinct_clusters', 0)} distinct clusters; "
         f"{storyboard.get('duplicates_collapsed', 0)} redundant samples "
         f"collapsed; semantic status={storyboard.get('semantic_status')}.")
    ]
    rows = storyboard.get("evidence") or []
    if not include_all:
        rows = rows[:30]
    for row in rows:
        ranges = ",".join(f"{_clock(a)}-{_clock(b)}"
                          for a, b in row.get("covered_ranges") or [])
        description = row.get("semantic_description") or (
            f"measured faces={len(row.get('faces') or [])}, "
            f"text_regions={len(row.get('text_geometry') or [])}, "
            f"motion={row.get('motion_state')}")
        lines.append(
            f"- {row.get('evidence_id')} | SOURCE {row.get('source_clock')} "
            f"| covers {ranges or 'exact instant'} | shots "
            f"{row.get('shot_ids') or [row.get('shot_id')]} | {description}")
    return "\n".join(lines)
