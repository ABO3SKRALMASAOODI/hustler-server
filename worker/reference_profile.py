"""Measured editorial grammar for an indexed style-reference video.

The index already paid to detect shots, audio structure, motion and spatial
content.  This module turns those facts into the relationships an editor
needs—cadence, contrast, musical lock, speech/text density and composition—so
"make mine like this" is not reduced to copying a color or guessing from four
frames.  It never recommends raw feature counts: a 15-second reference and a
five-minute source should share hierarchy and rhythm *shape*, not 17 cuts.
"""

import math

import spatial


REFERENCE_PROFILE_VERSION = 1


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percentile(values, pct):
    clean = sorted(_float(v) for v in values if _float(v) >= 0)
    if not clean:
        return None
    rank = (len(clean) - 1) * float(pct) / 100.0
    lo, hi = int(math.floor(rank)), int(math.ceil(rank))
    if lo == hi:
        return clean[lo]
    frac = rank - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def _spread(items, count):
    if not items or count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (count - 1))]
            for i in range(count)]


def _shot_profile(shots, duration):
    windows = []
    for row in shots or []:
        try:
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            windows.append((start, end))
    windows.sort()
    lengths = [end - start for start, end in windows]
    quarters = [[] for _ in range(4)]
    for start, end in windows:
        q = min(3, max(0, int(((start + end) / 2.0) /
                              max(duration, 0.01) * 4)))
        quarters[q].append(end - start)
    medians = [round(_percentile(rows, 50), 2) if rows else None
               for rows in quarters]
    return {
        "shots": len(windows),
        "cuts": max(0, len(windows) - 1),
        "cuts_per_10s": round(max(0, len(windows) - 1) /
                              max(duration, 0.01) * 10.0, 2),
        "shot_p10_s": (round(_percentile(lengths, 10), 2)
                       if lengths else None),
        "shot_median_s": (round(_percentile(lengths, 50), 2)
                          if lengths else None),
        "shot_p90_s": (round(_percentile(lengths, 90), 2)
                       if lengths else None),
        "median_by_quarter_s": medians,
        "cut_times_s": [round(start, 3) for start, _end in windows[1:]],
    }


def _beat_profile(cuts, perception):
    beats = sorted(_float(v, -1) for v in (perception.get("beats") or [])
                   if _float(v, -1) >= 0)
    bpm = _float(perception.get("bpm")) or None
    confidence = _float(perception.get("bpm_conf"))
    base = {"bpm": round(bpm, 2) if bpm else None,
            "confidence": round(confidence, 3),
            "cuts_on_full_beat": None,
            "cuts_on_full_or_half_beat": None,
            "full_beat_random_baseline": None,
            "half_beat_random_baseline": None,
            "relationship": "not_measured"}
    if len(beats) < 3 or not cuts:
        return base
    half = sorted(beats + [(a + b) / 2.0 for a, b in zip(beats, beats[1:])])
    full_observed = sum(1 for cut in cuts
                        if min(abs(cut - beat) for beat in beats) <= 0.10) \
        / len(cuts)
    half_observed = sum(1 for cut in cuts
                        if min(abs(cut - beat) for beat in half) <= 0.10) \
        / len(cuts)
    interval = (beats[-1] - beats[0]) / max(1, len(beats) - 1)
    full_baseline = min(1.0, 0.20 / max(interval, 0.01))
    half_baseline = min(1.0, 0.20 / max(interval / 2.0, 0.01))
    # Full and half beats are both legitimate editing grids, but a fast half-
    # beat grid can cover most of the clock by chance. Judge the stronger
    # lift over its OWN random baseline so 100% alignment at 120 BPM means
    # something only when cuts also favor the full beats.
    lift = max(full_observed - full_baseline,
               half_observed - half_baseline)
    if confidence < 0.25:
        relationship = "tempo_uncertain"
    elif lift >= 0.25:
        relationship = "strong_phrase_or_grid_lock"
    elif lift >= 0.10:
        relationship = "selective_musical_lock"
    else:
        relationship = "visually_or_speech_led_not_grid_locked"
    base.update({
        "cuts_on_full_beat": round(full_observed, 3),
        "cuts_on_full_or_half_beat": round(half_observed, 3),
        "full_beat_random_baseline": round(full_baseline, 3),
        "half_beat_random_baseline": round(half_baseline, 3),
        "relationship": relationship,
    })
    return base


def _energy_profile(perception):
    energy = [_float(v) for v in (perception.get("energy") or [])]
    if not energy:
        return {"shape": "not_measured", "quarter_relative_db": []}
    groups = [[] for _ in range(4)]
    for i, value in enumerate(energy):
        q = min(3, int(i / max(1, len(energy)) * 4))
        groups[q].append(value)
    means = [sum(group) / len(group) if group else min(energy)
             for group in groups]
    floor = min(means)
    relative = [round(value - floor, 1) for value in means]
    peak = max(range(4), key=lambda i: means[i])
    spread = max(means) - min(means)
    if spread < 2.0:
        shape = "steady"
    elif peak == 0:
        shape = "front_loaded"
    elif peak == 3:
        shape = "builds_to_finish"
    elif peak == 2:
        shape = "late_peak_then_release"
    else:
        shape = "early_peak_then_release"
    return {"shape": shape, "peak_quarter": peak + 1,
            "quarter_relative_db": relative}


def _spatial_profile(index):
    sidecar = index.get("spatial") or {}
    samples = sidecar.get("samples") or []
    n = max(1, len(samples))
    faces = sum(bool(row.get("faces")) for row in samples)
    text = sum(bool(row.get("text")) for row in samples)
    ui = sum(bool(row.get("dense_ui")) for row in samples)
    try:
        burned = spatial.burned_caption_score(
            sidecar, index.get("words") or [])
    except Exception:
        burned = 0.0
    return {
        "samples": len(samples),
        "face_presence": round(faces / n, 3) if samples else None,
        "text_presence": round(text / n, 3) if samples else None,
        "dense_ui_presence": round(ui / n, 3) if samples else None,
        "burned_caption_evidence": round(_float(burned), 3),
    }


def from_index(index):
    """Build a bounded JSON-safe reference profile from a clip index."""
    index = index if isinstance(index, dict) else {}
    video = index.get("video") or {}
    duration = max(0.0, _float(video.get("duration")))
    shots = _shot_profile(index.get("shots") or [], duration)
    perception = index.get("perception") or {}
    words = [row for row in (index.get("words") or [])
             if isinstance(row, dict) and str(row.get("w") or "").strip()]
    speech_s = sum(max(0.0, _float(row.get("t1")) - _float(row.get("t0")))
                   for row in words)
    motion = index.get("motion") or {}
    return {
        "version": REFERENCE_PROFILE_VERSION,
        "duration_s": round(duration, 2),
        "aspect": {
            "width": int(_float(video.get("width"))),
            "height": int(_float(video.get("height"))),
            "ratio": (round(_float(video.get("width")) /
                            max(_float(video.get("height")), 1.0), 3)
                      if video.get("width") and video.get("height") else None),
        },
        "rhythm": shots,
        "music_relation": _beat_profile(shots["cut_times_s"], perception),
        "energy": _energy_profile(perception),
        "motion": {
            key: motion.get(key) for key in
            ("analyzed_window_s", "intensity", "mean_frame_change",
             "p90_frame_change", "freeze_share", "abrupt_changes_per_10s",
             "sampling_mode", "source_coverage_s")
            if motion.get(key) is not None},
        "speech": {
            "words": len(words),
            "coverage": round(min(1.0, speech_s / max(duration, 0.01)), 3),
            "speakers": int(_float(index.get("speakers"))),
            "language": index.get("language"),
        },
        "composition": _spatial_profile(index),
    }


def describe(profile):
    """Compact measured grammar for agent context; no aesthetic invention."""
    if not isinstance(profile, dict) or not profile.get("duration_s"):
        return ""
    rhythm = profile.get("rhythm") or {}
    musical = profile.get("music_relation") or {}
    energy = profile.get("energy") or {}
    motion = profile.get("motion") or {}
    speech = profile.get("speech") or {}
    comp = profile.get("composition") or {}
    aspect = profile.get("aspect") or {}
    bits = [
        "MEASURED REFERENCE GRAMMAR — transfer its relationships and "
        "hierarchy to the user's material; do not blindly copy raw counts.",
        (f"Rhythm: {profile['duration_s']:g}s, {rhythm.get('shots', 0)} "
         f"shots / {rhythm.get('cuts_per_10s', 0):g} cuts per 10s; shot "
         f"p10/median/p90={rhythm.get('shot_p10_s')}/"
         f"{rhythm.get('shot_median_s')}/{rhythm.get('shot_p90_s')}s; "
         f"quarter medians={rhythm.get('median_by_quarter_s')}s."),
        (f"Music relationship: {musical.get('relationship')}"
         + (f", {musical.get('bpm'):g} BPM @ "
            f"{musical.get('confidence'):.2f} confidence"
            if musical.get("bpm") else "")
         + (f", {100 * musical.get('cuts_on_full_or_half_beat'):.0f}% of "
            f"cuts near full/half beats vs "
            f"{100 * musical.get('half_beat_random_baseline'):.0f}% chance; "
            f"{100 * musical.get('cuts_on_full_beat'):.0f}% on full beats vs "
            f"{100 * musical.get('full_beat_random_baseline'):.0f}% chance"
            if musical.get("cuts_on_full_or_half_beat") is not None else "")
         + "."),
        (f"Energy: {energy.get('shape')} across quarters "
         f"{energy.get('quarter_relative_db')} relative dB; motion="
         f"{str(motion.get('intensity') or 'not measured').replace('_', ' ')}"
         f" (measured {motion.get('analyzed_window_s') or 0:g}s"
         + (f" in distributed windows over "
            f"{motion.get('source_coverage_s'):g}s"
            if motion.get("sampling_mode") == "distributed_windows" and
            motion.get("source_coverage_s") else "")
         + ")."),
        (f"Content: aspect {aspect.get('width')}x{aspect.get('height')}; "
         f"speech coverage {100 * _float(speech.get('coverage')):.0f}% with "
         f"{speech.get('speakers') or 'unknown'} speaker(s); faces in "
         f"{100 * _float(comp.get('face_presence')):.0f}% of samples, text "
         f"in {100 * _float(comp.get('text_presence')):.0f}%, dense UI in "
         f"{100 * _float(comp.get('dense_ui_presence')):.0f}%, burned-caption "
         f"evidence {100 * _float(comp.get('burned_caption_evidence')):.0f}%."),
        "The attached reference filmstrip supplies the visual design evidence "
        "these measurements cannot name (typeface, layout, transitions, "
        "color motifs and what each cut reveals).",
    ]
    return "\n".join(bits)
