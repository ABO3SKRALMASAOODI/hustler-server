"""Compact cross-modal evidence for editorial decisions.

The source index already measures four useful clocks independently:
transcribed sentences/words, shot boundaries, acoustic perception and sparse
spatial samples.  Asking an editor model to retrieve and mentally join all
four costs tokens and, more importantly, makes it easy to align a good idea
to the wrong second.  This module performs that join deterministically.

It deliberately does *not* decide what is tasteful.  A loud word, a face or
a scene change is evidence, not an instruction to add an effect.  The output
is source-time provenance that a director can combine with the brief and the
actual pixels supplied by filmstrips / ``look_at``.

No decoder or model is used here.  Building the map is cheap enough to cache
per ToolContext and works immediately with every v10 index already stored.
"""

from __future__ import annotations

import bisect
import math

import perception


EDITORIAL_INDEX_VERSION = 1
FOCI = frozenset({"all", "story", "visual", "energy", "faces", "ui",
                  "quiet", "peaks"})


def _number(value, default=0.0):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _overlaps(row, start, end):
    return _number(row.get("t1", row.get("end"))) > start and \
        _number(row.get("t0", row.get("start"))) < end


def _duration(index):
    video = index.get("video") or {}
    duration = _number(video.get("duration"))
    if duration > 0:
        return duration
    ends = []
    for key in ("sentences", "shots"):
        for row in index.get(key) or []:
            ends.append(_number(row.get("t1", row.get("end"))))
    return max(ends or [0.0])


def _span_energy(perception_sidecar, start, end, global_values):
    values = perception_sidecar.get("energy") or []
    bin_s = max(0.01, _number(perception_sidecar.get("energy_bin_s"), 0.5))
    picked = [
        _number(value, -80.0) for i, value in enumerate(values)
        if (i + 1) * bin_s > start and i * bin_s < end
    ]
    if not picked:
        return {"measured": False}
    mean = sum(picked) / len(picked)
    peak = max(picked)
    # Values are dB relative to the source peak (0 is loudest).  Relative
    # labels adapt to each recording instead of pretending -12 dB means the
    # same narrative intensity for a phone memo and a mastered song.
    ordered = sorted(global_values or picked)
    low = ordered[max(0, int((len(ordered) - 1) * 0.25))]
    high = ordered[max(0, int((len(ordered) - 1) * 0.75))]
    if high - low < 1.0:
        level = "medium"
    else:
        level = "high" if mean >= high else "low" if mean <= low else "medium"
    delta = picked[-1] - picked[0]
    trend = "rising" if delta >= 3.0 else "falling" if delta <= -3.0 \
        else "steady"
    return {"measured": True, "mean_db": round(mean, 1),
            "peak_db": round(peak, 1), "level": level, "trend": trend,
            "delta_db": round(delta, 1)}


def _span_beats(perception_sidecar, start, end):
    beats = sorted(_number(x) for x in perception_sidecar.get("beats") or [])
    if not beats:
        return {"measured": False}
    lo, hi = bisect.bisect_left(beats, start), bisect.bisect_right(beats, end)
    near = min((abs(x - start) for x in beats[max(0, lo - 1):lo + 2]),
               default=None)
    return {"measured": True, "count": max(0, hi - lo),
            "start_distance_s": round(near, 3) if near is not None else None,
            "start_on_beat": bool(near is not None and near <= 0.12)}


def _shot_evidence(shots, start, end):
    rows = [row for row in shots if
            _number(row.get("end")) > start and
            _number(row.get("start")) < end]
    ids = [row.get("id") for row in rows]
    changes = [_number(row.get("start")) for row in rows
               if start + 0.08 < _number(row.get("start")) < end - 0.08]
    return {"ids": ids, "changes_s": [round(x, 2) for x in changes],
            "count": len(rows)}


def _nearest_spatial_samples(sidecar, start, end):
    samples = sorted((sidecar or {}).get("samples") or [],
                     key=lambda row: _number(row.get("t")))
    inside = [row for row in samples if start <= _number(row.get("t")) <= end]
    if inside or not samples:
        return inside, False
    midpoint = (start + end) / 2.0
    nearest = min(samples, key=lambda row: abs(_number(row.get("t")) - midpoint))
    step = max(1.0, _number((sidecar or {}).get("sample_step_s"), 2.0))
    # A sparse sample can describe the same take, but never let the first
    # frame stand in for a sentence several minutes away.
    if abs(_number(nearest.get("t")) - midpoint) <= max(2.0, step * 0.75):
        return [nearest], True
    return [], False


def _spatial_evidence(sidecar, start, end):
    samples, nearest = _nearest_spatial_samples(sidecar, start, end)
    n = len(samples)
    if not n:
        return {"measured": False, "samples": 0}
    face_hits = [row for row in samples if row.get("faces")]
    text_hits = [row for row in samples if row.get("text")]
    ui_hits = [row for row in samples if row.get("dense_ui")]
    blank_hits = [row for row in samples
                  if _number(row.get("mean_luma"), 100.0) < 12.0 and
                  _number(row.get("std_luma"), 100.0) < 7.0]
    centers = []
    sizes = []
    max_faces = 0
    for row in face_hits:
        faces = row.get("faces") or []
        max_faces = max(max_faces, len(faces))
        for box in faces:
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            x0, y0, x1, y1 = (_number(x) for x in box[:4])
            centers.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
            sizes.append(max(0.0, x1 - x0) * max(0.0, y1 - y0))
    position = None
    if centers:
        x = sum(row[0] for row in centers) / len(centers)
        position = "left" if x < 0.4 else "right" if x > 0.6 else "center"
    return {
        "measured": True,
        "samples": n,
        "nearest_sample": nearest,
        "sample_times_s": [round(_number(row.get("t")), 2)
                           for row in samples[:6]],
        "face_presence": round(len(face_hits) / n, 2),
        "max_faces": max_faces,
        "face_position": position,
        "mean_face_frame_share": (round(sum(sizes) / len(sizes), 3)
                                  if sizes else None),
        "text_presence": round(len(text_hits) / n, 2),
        "dense_ui_presence": round(len(ui_hits) / n, 2),
        "blank_presence": round(len(blank_hits) / n, 2),
    }


def _motion_evidence(profile, start, end):
    profile = profile or {}
    sections = [row for row in profile.get("sections") or []
                if _number(row.get("end_s")) > start and
                _number(row.get("start_s")) < end]
    if not sections:
        # v1 profiles measured only the opening. Preserve honest usefulness
        # inside that window, but never project it onto later source seconds.
        window = _number(profile.get("analyzed_window_s"))
        if not profile.get("sections") and profile.get("intensity") \
                and start < window:
            return {"measured": True, "sampling": "legacy_opening_only",
                    "intensity": profile.get("intensity"),
                    "mean_frame_change": profile.get("mean_frame_change"),
                    "p90_frame_change": profile.get("p90_frame_change"),
                    "freeze_share": profile.get("freeze_share"),
                    "windows": [[0.0, round(window, 2)]]}
        return {"measured": False}
    order = {"frozen_or_nearly_static": 0, "gentle": 1,
             "moderate": 2, "high": 3}
    strongest = max(sections,
                    key=lambda row: order.get(row.get("intensity"), -1))
    weights = [max(1, int(row.get("samples") or 1) - 1) for row in sections]
    total = float(sum(weights))

    def weighted(key):
        return sum(_number(row.get(key)) * weight
                   for row, weight in zip(sections, weights)) / total

    return {
        "measured": True,
        "sampling": profile.get("sampling_mode") or "section",
        "intensity": strongest.get("intensity"),
        "mean_frame_change": round(weighted("mean_frame_change"), 2),
        "p90_frame_change": round(weighted("p90_frame_change"), 2),
        "freeze_share": round(weighted("freeze_share"), 3),
        "windows": [[round(_number(row.get("start_s")), 2),
                     round(_number(row.get("end_s")), 2)] for row in sections],
    }


def _pause_evidence(sentences, row_i, silences):
    row = sentences[row_i]
    t0, t1 = _number(row.get("t0")), _number(row.get("t1"))
    before = t0 - _number(sentences[row_i - 1].get("t1")) if row_i else t0
    after = (_number(sentences[row_i + 1].get("t0")) - t1
             if row_i + 1 < len(sentences) else None)
    quiet = []
    for raw in silences:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        a, b = _number(raw[0]), _number(raw[1])
        if b > t0 - 0.5 and a < t1 + 0.5:
            quiet.append([round(a, 2), round(b, 2)])
    return {"speech_gap_before_s": round(max(0.0, before), 2),
            "speech_gap_after_s": (round(max(0.0, after), 2)
                                   if after is not None else None),
            "waveform_quiet_nearby": quiet[:4]}


def _word_stress_by_sentence(index, perception_sidecar, sentences=None):
    words = index.get("words") or []
    sentences = sentences if sentences is not None else \
        (index.get("sentences") or [])
    if not words or not (perception_sidecar or {}).get("vb_env"):
        return [{} for _ in sentences]
    scores = perception.word_stress(perception_sidecar, words)
    out = []
    for sentence in sentences:
        try:
            wi0, wi1 = int(sentence.get("wi0")), int(sentence.get("wi1"))
            indexes = range(max(0, wi0), min(len(words), wi1 + 1))
        except (TypeError, ValueError):
            a, b = _number(sentence.get("t0")), _number(sentence.get("t1"))
            indexes = [i for i, word in enumerate(words)
                       if _number(word.get("t1")) > a and
                       _number(word.get("t0")) < b]
        indexes = list(indexes)
        if not indexes:
            out.append({})
            continue
        best = max(indexes, key=lambda i: scores[i])
        out.append({"max": scores[best], "word": words[best].get("w"),
                    "at_s": round(_number(words[best].get("t0")), 2)})
    return out


def _tags(row):
    tags = []
    audio = row["audio"]
    picture = row["picture"]
    pauses = row["pauses"]
    if audio.get("level") == "high":
        tags.append("energy_peak")
    if audio.get("level") == "low":
        tags.append("low_energy")
    if audio.get("trend") in ("rising", "falling"):
        tags.append(f"energy_{audio['trend']}")
    if _number(audio.get("vocal_stress")) >= 0.72:
        tags.append("vocal_emphasis")
    if row["shots"].get("changes_s"):
        tags.append("scene_change_inside")
    if picture.get("face_presence", 0) > 0:
        tags.append("face")
    if picture.get("dense_ui_presence", 0) > 0:
        tags.append("dense_ui")
    elif picture.get("text_presence", 0) > 0:
        tags.append("source_text")
    if picture.get("blank_presence", 0) > 0:
        tags.append("blank_frame")
    motion = row.get("motion") or {}
    if motion.get("intensity") == "high":
        tags.append("high_motion")
    elif motion.get("intensity") == "frozen_or_nearly_static":
        tags.append("near_static")
    if _number(pauses.get("speech_gap_before_s")) >= 0.65:
        tags.append("pause_before")
    if _number(pauses.get("speech_gap_after_s")) >= 0.65:
        tags.append("pause_after")
    return tags


def build(index):
    """Return a JSON-safe, chronological cross-modal map for one source."""
    index = index or {}
    duration = _duration(index)
    sentences = sorted(index.get("sentences") or [],
                       key=lambda row: _number(row.get("t0")))
    shots = sorted(index.get("shots") or [],
                   key=lambda row: _number(row.get("start")))
    sidecar = index.get("perception") or {}
    spatial = index.get("spatial") or {}
    energy_values = [_number(x, -80.0) for x in sidecar.get("energy") or []]
    stresses = _word_stress_by_sentence(index, sidecar, sentences)
    rows = []
    if sentences:
        bases = [("speech", sentence, i) for i, sentence in enumerate(sentences)]
    else:
        bases = [("shot", shot, i) for i, shot in enumerate(shots)]
        if not bases and duration > 0:
            bases = [("program", {"id": "program", "start": 0.0,
                                   "end": duration}, 0)]
    for kind, base, i in bases:
        start = max(0.0, _number(base.get("t0", base.get("start"))))
        end = min(duration or float("inf"),
                  _number(base.get("t1", base.get("end"))))
        if end <= start:
            continue
        energy = _span_energy(sidecar, start, end, energy_values)
        beat = _span_beats(sidecar, start, end)
        if kind == "speech" and i < len(stresses) and stresses[i]:
            energy["vocal_stress"] = stresses[i]["max"]
            energy["stressed_word"] = stresses[i]["word"]
            energy["stress_at_s"] = stresses[i]["at_s"]
        row = {
            "id": base.get("id", f"{kind}{i + 1}"),
            "kind": kind,
            "t0": round(start, 3),
            "t1": round(end, 3),
            "text": str(base.get("text") or "").strip(),
            "speaker": base.get("speaker"),
            "shots": _shot_evidence(shots, start, end),
            "audio": {**energy, "beats": beat},
            "picture": _spatial_evidence(spatial, start, end),
            "motion": _motion_evidence(index.get("motion"), start, end),
            "pauses": (_pause_evidence(sentences, i,
                                        index.get("silences") or [])
                       if kind == "speech" else
                       {"waveform_quiet_nearby": [
                           [round(_number(raw[0]), 2), round(_number(raw[1]), 2)]
                           for raw in index.get("silences") or []
                           if isinstance(raw, (list, tuple)) and len(raw) >= 2
                           and _number(raw[1]) > start and _number(raw[0]) < end
                       ][:4]}),
        }
        row["tags"] = _tags(row)
        rows.append(row)
    measured = {
        "speech": bool(sentences),
        "shots": bool(shots),
        "audio": bool(sidecar.get("energy")),
        "beats": bool(sidecar.get("beats")),
        "vocal_stress": bool(sidecar.get("vb_env")),
        "spatial": bool(spatial.get("samples")),
        "section_motion": bool((index.get("motion") or {}).get("sections")),
    }
    return {"version": EDITORIAL_INDEX_VERSION,
            "duration_s": round(duration, 3), "measured": measured,
            "rows": rows}


def query(editorial_map, start=0.0, end=None, focus="all"):
    """Filter rows without changing chronology or inventing salience."""
    focus = str(focus or "all").strip().lower()
    if focus not in FOCI:
        raise ValueError(f"focus must be one of {', '.join(sorted(FOCI))}")
    duration = _number(editorial_map.get("duration_s"))
    start = max(0.0, _number(start))
    end = duration if end is None else min(duration, _number(end, duration))
    rows = [row for row in editorial_map.get("rows") or []
            if _overlaps(row, start, end)]
    if focus in ("all", "story"):
        return rows
    if focus == "visual":
        return [row for row in rows if row["picture"].get("measured")]
    if focus == "energy":
        return [row for row in rows if row["audio"].get("measured")]
    if focus == "faces":
        return [row for row in rows
                if row["picture"].get("face_presence", 0) > 0]
    if focus == "ui":
        return [row for row in rows
                if row["picture"].get("dense_ui_presence", 0) > 0 or
                row["picture"].get("text_presence", 0) > 0]
    if focus == "quiet":
        return [row for row in rows if
                "low_energy" in row.get("tags", []) or
                row.get("pauses", {}).get("waveform_quiet_nearby")]
    # peaks: retain every independently evidenced emphasis/change moment,
    # not a fixed top-N aesthetic quota. The response-layer bound handles
    # unusually dense programs and tells the caller how to page.
    return [row for row in rows if set(row.get("tags") or []) & {
        "energy_peak", "energy_rising", "vocal_emphasis",
        "scene_change_inside", "pause_before", "pause_after"}]


def summary(editorial_map):
    measured = editorial_map.get("measured") or {}
    rows = editorial_map.get("rows") or []
    present = [key for key, value in measured.items() if value]
    missing = [key for key, value in measured.items() if not value]
    return {"rows": len(rows), "measured": present, "missing": missing,
            "tag_counts": {tag: sum(tag in row.get("tags", []) for row in rows)
                           for tag in sorted({tag for row in rows
                                              for tag in row.get("tags", [])})}}
