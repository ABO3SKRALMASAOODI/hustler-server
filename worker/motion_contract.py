"""Map an authored motion language onto evidence in the executable EDL.

The blueprint names treatment-specific motifs and binds them to story beats;
the EDL contains renderer operations on several different clocks.  This module
joins the two without turning taste into an effect quota:

* a beat is judged only when its measured source span can be mapped to the
  assembled program;
* a motif is fulfilled only by an overlapping authored event in one of the
  motif's own domains that explicitly carries that motif's provenance;
* ``hold`` is first-class authored stillness and fails only when explicit
  choreographic motion overlaps it;
* natural movement in the source is not invented as EDL evidence, and plain
  cuts are not counted as animation.

The visual critic still decides whether the movement is tasteful and whether
it resembles the promised behavior.  These pure checks answer the narrower,
load-bearing question: did the promised movement actually get authored at the
promised moment?
"""

import json

import captions as captionlib
import director
from timeline import Timeline, program_blocks, transition_junctions


_EPS = 0.08
TEMPORAL_EFFECT_KINDS = frozenset({
    "chromatic", "dream_blur", "flash", "glow", "halation",
    "motion_blur", "shake", "vhs",
})


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _span(start, end):
    start, end = _number(start), _number(end)
    if start is None or end is None or end - start <= 1e-3:
        return None
    return (start, end)


def _curve_window(value):
    """Local time window only when a keyframe curve truly changes value."""
    if not isinstance(value, list) or len(value) < 2:
        return None
    points = []
    for keyframe in value:
        if isinstance(keyframe, dict):
            t, v = _number(keyframe.get("t")), _number(keyframe.get("v"))
        else:
            t, v = (_number(getattr(keyframe, "t", None)),
                    _number(getattr(keyframe, "v", None)))
        if t is not None and v is not None:
            points.append((t, v))
    if len(points) < 2 or max(v for _t, v in points) - \
            min(v for _t, v in points) <= 1e-6:
        return None
    return min(t for t, _v in points), max(t for t, _v in points)


def _motion_window(motion):
    if not isinstance(motion, dict):
        return None
    windows = [_curve_window(value) for value in motion.values()]
    windows = [window for window in windows if window]
    if not windows:
        return None
    return min(window[0] for window in windows), \
        max(window[1] for window in windows)


def _add(events, domain, kind, start, end, item_id=None, detail=None,
         motion_motif=None):
    window = _span(start, end)
    if not window:
        return
    events.append({
        "domain": domain,
        "kind": kind,
        "start": round(window[0], 3),
        "end": round(window[1], 3),
        "id": str(item_id)[:80] if item_id else None,
        "detail": str(detail)[:140] if detail else None,
        "motion_motif": str(motion_motif)[:48] if motion_motif else None,
    })


def motion_events(edl, index=None):
    """Return explicit choreographic events on the final-program clock."""
    edl = edl if isinstance(edl, dict) else {}
    events = []
    try:
        blocks = program_blocks(edl)
    except Exception:
        blocks = []
    duration = _number(blocks[-1]["out_end"], 0.0) if blocks else 0.0

    effects = edl.get("effects") or {}
    for row in effects.get("zooms") or []:
        if isinstance(row, dict):
            _add(events, "camera", "zoom", row.get("start"), row.get("end"),
                 row.get("id"), row.get("mode") or "punch",
                 row.get("motion_motif"))

    for row in effects.get("frame_shifts") or []:
        if not isinstance(row, dict):
            continue
        at = _number(row.get("at"))
        length = _number(row.get("duration_s"), 0.8)
        if at is not None and length is not None:
            _add(events, "camera", "frame_shift", at, at + length,
                 row.get("id"), row.get("ratio"), row.get("motion_motif"))

    fade_in = _number(effects.get("fade_in_s"))
    fade_out = _number(effects.get("fade_out_s"))
    if fade_in and fade_in > 0:
        _add(events, "effect", "fade_in", 0.0, min(duration, fade_in))
    if fade_out and fade_out > 0 and duration > 0:
        _add(events, "effect", "fade_out", max(0.0, duration - fade_out),
             duration)

    for row in effects.get("stylize") or []:
        if not isinstance(row, dict) or \
                row.get("kind") not in TEMPORAL_EFFECT_KINDS:
            continue
        # A whole-program grain/look is finishing, not a beat-level motion
        # event. Bounded temporal effects are deliberate choreography.
        if row.get("start") is not None and row.get("end") is not None:
            _add(events, "effect", "stylize", row.get("start"),
                 row.get("end"), row.get("id"), row.get("kind"),
                 row.get("motion_motif"))
    for row in effects.get("custom") or []:
        if not isinstance(row, dict):
            continue
        if row.get("start") is not None and row.get("end") is not None:
            _add(events, "effect", "custom", row.get("start"),
                 row.get("end"), row.get("id"), row.get("label"),
                 row.get("motion_motif"))

    transition = effects.get("transition") or {}
    if isinstance(transition, dict) and transition.get("style") and blocks:
        try:
            junctions = transition_junctions(edl, index or {}, len(blocks))
        except Exception:
            junctions = set(range(max(0, len(blocks) - 1)))
        length = max(0.1, _number(transition.get("duration_s"), 0.3))
        for junction in sorted(junctions):
            if 0 <= junction < len(blocks) - 1:
                at = _number(blocks[junction].get("out_end"))
                if at is not None:
                    _add(events, "transition", "transition",
                         max(0.0, at - length), min(duration, at + length),
                         f"junction-{junction + 1}", transition.get("style"),
                         transition.get("motion_motif"))

    for collection, domain in (("texts", "type"), ("vectors", "graphic")):
        for row in edl.get(collection) or []:
            if not isinstance(row, dict):
                continue
            start, end = _number(row.get("start")), _number(row.get("end"))
            if start is None or end is None or end <= start:
                continue
            local = _motion_window(row.get("motion"))
            if local:
                _add(events, domain, "keyframes", start + local[0],
                     min(end, start + local[1]), row.get("id"),
                     row.get("kind") or row.get("template"),
                     row.get("motion_motif"))
            if collection == "texts":
                edge = min(0.45, max(0.1, (end - start) * 0.25))
                if row.get("entrance") not in (None, "none"):
                    _add(events, domain, "entrance", start,
                         min(end, start + edge), row.get("id"),
                         row.get("entrance"), row.get("motion_motif"))
                if row.get("exit") not in (None, "none"):
                    _add(events, domain, "exit", max(start, end - edge), end,
                         row.get("id"), row.get("exit"),
                         row.get("motion_motif"))

    caption_track = edl.get("captions")
    caption_mode = captionlib.motion_mode(caption_track)
    if caption_mode and index:
        try:
            caption_timeline = Timeline(
                edl.get("keep") or [], edl.get("inserts") or [],
                edl.get("speed") or [])
            caption_events, _global_style = captionlib.compiled_events(
                edl, index, caption_timeline)
            for event_index, event in enumerate(caption_events, 1):
                event_mode = (event.get("motion_mode")
                              if isinstance(caption_track, list)
                              else caption_mode)
                if not event_mode:
                    continue
                start, end = (_number(event.get("start")),
                              _number(event.get("end")))
                if start is None or end is None:
                    continue
                if event_mode == "entrance":
                    end = min(end, start + min(
                        0.45, max(0.1, (end - start) * 0.25)))
                _add(events, "type", "caption_motion", start, end,
                     f"caption-{event_index}", event_mode,
                     (caption_track.get("motion_motif")
                      if isinstance(caption_track, dict)
                      else event.get("motion_motif")))
        except Exception:
            # Missing/malformed transcript evidence cannot prove motion and
            # must not make the complete edit crash; the beat remains missing
            # or not_judged and the normal caption audit reports its own lane.
            pass

    for row in edl.get("overlays") or []:
        if not isinstance(row, dict):
            continue
        start = _number(row.get("start"))
        length = _number(row.get("duration_s"))
        if start is None or length is None or length <= 0:
            continue
        end = start + length
        motion = {key: row.get(key) for key in
                  ("x", "y", "scale", "rotation", "opacity")}
        local = _motion_window(motion)
        if local:
            _add(events, "media", "overlay_keyframes", start + local[0],
                 min(end, start + local[1]), row.get("id"), None,
                 row.get("motion_motif"))
        edge = min(0.45, max(0.1, length * 0.25))
        if row.get("entrance"):
            _add(events, "media", "overlay_entrance", start,
                 min(end, start + edge), row.get("id"), row.get("entrance"),
                 row.get("motion_motif"))
        if row.get("exit"):
            _add(events, "media", "overlay_exit", max(start, end - edge),
                 end, row.get("id"), row.get("exit"),
                 row.get("motion_motif"))
        screen = row.get("screen") or {}
        if isinstance(screen, dict) and screen:
            _add(events, "media", "screen_track", start, end, row.get("id"),
                 None, row.get("motion_motif"))

    by_insert = {str(row.get("id")): row for row in (edl.get("inserts") or [])
                 if isinstance(row, dict) and row.get("id")}
    for block in blocks:
        if block.get("kind") != "insert":
            continue
        row = by_insert.get(str(block.get("id"))) or {}
        if row.get("motion"):
            _add(events, "media", "insert_motion", block.get("out_start"),
                 block.get("out_end"), block.get("id"), row.get("motion"),
                 row.get("motion_motif"))

    try:
        timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                            edl.get("speed") or [])
        for row in edl.get("speed") or []:
            if not isinstance(row, dict):
                continue
            factor = _number(row.get("factor"), 1.0)
            if factor is None or abs(factor - 1.0) <= 1e-6:
                continue
            for start, end in timeline.span_to_out(
                    _number(row.get("start"), 0.0),
                    _number(row.get("end"), 0.0)):
                _add(events, "camera", "speed", start, end, row.get("id"),
                     f"{factor:g}x", row.get("motion_motif"))
    except Exception:
        pass

    return sorted(events, key=lambda row: (row["start"], row["end"],
                                            row["domain"], row["kind"]))


def _asset_spans(edl, asset_key, source_start, source_end):
    spans = []
    try:
        blocks = program_blocks(edl)
    except Exception:
        blocks = []
    for block in blocks:
        if block.get("kind") != "insert" or \
                block.get("asset_key") != asset_key:
            continue
        clip_start = _number(block.get("clip_start_s"), 0.0)
        rate = max(0.001, _number(block.get("rate"), 1.0))
        clip_end = clip_start + (
            _number(block.get("out_end"), 0.0) -
            _number(block.get("out_start"), 0.0)) * rate
        lo, hi = max(source_start, clip_start), min(source_end, clip_end)
        if hi - lo > 1e-3:
            out_start = _number(block.get("out_start"), 0.0) + \
                (lo - clip_start) / rate
            spans.append((out_start, out_start + (hi - lo) / rate))

    for row in edl.get("overlays") or []:
        if not isinstance(row, dict) or row.get("asset_key") != asset_key:
            continue
        out_start = _number(row.get("start"))
        length = _number(row.get("duration_s"))
        if out_start is None or length is None or length <= 0:
            continue
        clip_start = _number(row.get("source_start_s"), 0.0)
        clip_end = clip_start + length
        lo, hi = max(source_start, clip_start), min(source_end, clip_end)
        if hi - lo > 1e-3:
            mapped = out_start + lo - clip_start
            spans.append((mapped, mapped + hi - lo))
    return spans


def _mapped_spans(edl, beat):
    source = _span(beat.get("source_start_s"), beat.get("source_end_s"))
    if not source:
        return []
    asset_key = beat.get("source_asset_key")
    if asset_key:
        spans = _asset_spans(edl, asset_key, source[0], source[1])
    else:
        try:
            timeline = Timeline(edl.get("keep") or [],
                                edl.get("inserts") or [],
                                edl.get("speed") or [])
            spans = timeline.span_to_out(source[0], source[1])
        except Exception:
            spans = []
    return [[round(start, 3), round(end, 3)] for start, end in spans
            if end - start > 1e-3]


def sequence_output_spans(edl, beat):
    """Public file-aware mapping shared by visual and audio evidence lanes."""
    return _mapped_spans(edl if isinstance(edl, dict) else {},
                         beat if isinstance(beat, dict) else {})


def _overlap(event, span):
    return min(event["end"], span[1]) - max(event["start"], span[0]) > _EPS


def evaluate(blueprint, edl, index=None):
    """Return beat-level execution evidence and structural contradictions."""
    bp = director.normalize_blueprint(blueprint)
    motion_language = (bp or {}).get("motion_language") or {}
    sequence = (bp or {}).get("sequence_map") or []
    motion_mode = ((((bp or {}).get("department_plan") or {}).get("motion")
                    or {}).get("mode"))
    active = bool(motion_language and sequence and motion_mode == "author")
    if not active:
        return {"active": False, "beats": [], "gaps": [], "events": [],
                "mapped_beats": 0, "judged_beats": 0,
                "fulfilled_beats": 0}

    motifs = {row["id"]: row for row in motion_language.get("motifs") or []}
    events = motion_events(edl, index=index)
    rows, gaps = [], []
    for beat_index, beat in enumerate(sequence, 1):
        spans = _mapped_spans(edl, beat)
        motif_id = beat.get("motion_motif")
        motif = motifs.get(motif_id) or {}
        domains = set(motif.get("domains") or [])
        overlaps = [event for event in events
                    if any(_overlap(event, span) for span in spans)]
        domain_events = overlaps if motif_id == "hold" else [
            event for event in overlaps if event["domain"] in domains]
        relevant = domain_events if motif_id == "hold" else [
            event for event in domain_events
            if event.get("motion_motif") == motif_id]
        status = "not_judged"
        if spans:
            if motif_id == "hold":
                status = "contradicted" if relevant else "fulfilled"
            else:
                status = "fulfilled" if relevant else "missing"
        row = {
            "beat": beat_index,
            "role": beat.get("role"),
            "purpose": beat.get("purpose") or beat.get("anchor"),
            "motion_motif": motif_id,
            "motif_behavior": (motif.get("behavior") if motif_id != "hold"
                               else motion_language.get("stillness_rule")),
            "motif_trigger": (motif.get("trigger") if motif_id != "hold"
                              else beat.get("purpose") or
                              beat.get("anchor")),
            "domains": sorted(domains),
            "output_spans": spans,
            "events": relevant,
            "unbound_or_other_motif_events": [
                event for event in domain_events if event not in relevant],
            "status": status,
        }
        rows.append(row)
        if status == "missing":
            where = ", ".join(f"{start:g}-{end:g}s" for start, end in spans)
            domain_text = "/".join(sorted(domains)) or "declared"
            message = (
                f"motion beat {beat_index} ({beat.get('role') or 'planned beat'}) "
                f"promises motif {motif_id!r} in {domain_text} at output "
                f"{where}, but no overlapping authored event in those domains "
                f"is bound to that motif")
            gaps.append({"department": "motion", "mode": "author",
                         "beat": beat_index, "kind": "missing_motif",
                         "message": message})
        elif status == "contradicted":
            where = ", ".join(f"{start:g}-{end:g}s" for start, end in spans)
            event_text = ", ".join(
                f"{event['kind']} {event.get('id') or ''}".strip()
                + f" ({event['domain']})" for event in relevant[:4])
            message = (
                f"motion beat {beat_index} ({beat.get('role') or 'planned beat'}) "
                f"is bound to deliberate hold at output {where}, but overlaps "
                f"authored {event_text}")
            gaps.append({"department": "motion", "mode": "author",
                         "beat": beat_index, "kind": "hold_contradiction",
                         "message": message})

    judged = [row for row in rows if row["status"] != "not_judged"]
    fulfilled = [row for row in rows if row["status"] == "fulfilled"]
    return {
        "active": True,
        "beats": rows,
        "gaps": gaps,
        "events": events,
        "mapped_beats": sum(bool(row["output_spans"]) for row in rows),
        "judged_beats": len(judged),
        "fulfilled_beats": len(fulfilled),
    }


def execution_gaps(blueprint, edl, index=None):
    return evaluate(blueprint, edl, index=index)["gaps"]


def evidence_block(blueprint, edl, index=None):
    """Compact reviewer context; never claims visual quality from structure."""
    report = evaluate(blueprint, edl, index=index)
    if not report["active"]:
        return ""
    rows = []
    for beat in report["beats"]:
        events = [
            {key: event.get(key) for key in
             ("domain", "kind", "start", "end", "id", "detail",
              "motion_motif")
             if event.get(key) is not None}
            for event in beat["events"][:8]]
        rows.append({
            "beat": beat["beat"], "role": beat["role"],
            "motif": beat["motion_motif"], "domains": beat["domains"],
            "behavior": beat.get("motif_behavior"),
            "trigger": beat.get("motif_trigger"),
            "output_spans": beat["output_spans"], "status": beat["status"],
            "overlapping_authored_events": events,
            "unbound_or_other_motif_events": [
                {key: event.get(key) for key in
                 ("domain", "kind", "start", "end", "id", "detail",
                  "motion_motif") if event.get(key) is not None}
                for event in beat["unbound_or_other_motif_events"][:8]],
        })
    payload = {
        "meaning": ("exact motif-bound overlap proves execution presence "
                    "only; judge the promised behavior, trigger, path, "
                    "settle, composition and taste from ordered rendered "
                    "states; stills cannot prove interpolation smoothness"),
        "mapped_beats": report["mapped_beats"],
        "fulfilled_beats": report["fulfilled_beats"],
        "gaps": len(report["gaps"]),
        "beats": rows,
    }
    return "MOTION EXECUTION EVIDENCE: " + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"))[:6500]
