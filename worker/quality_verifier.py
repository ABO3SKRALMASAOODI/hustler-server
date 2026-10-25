"""Durable change manifests and deterministic edit verification.

The model may choose the treatment, but it may not choose whether the result
is checked.  Every immutable EDL write gets a manifest; every complete preview
gets a version-stamped record.  Rules here identify concrete corruption or a
missing evidence contract, not subjective effect quotas.

Only ERROR findings are unresolved repairs (status ``repair_required``, the
Studio export gate, continuation slices).  Craft heuristics — taste notes,
zoom/SFX purpose metadata, mechanical-pattern and reframe-coverage checks —
are recorded with severity ``advisory``: durable evidence the editor can act
on ("advisory: keep if intentional"), never a reason a premium, dense edit
cannot complete or export.

Wave 0 (track 0C) adds two owner gates here. The DEGRADED gate: a render
whose ``devices`` report lists a creative device at any rung other than
``full`` (a substitute, a reduced rung or an omit) records one blocking
``device_degraded`` finding per device, so the export stays blocked until
the degradation is acknowledged (justify_verification_findings) or fixed —
never shipped silently. Flash safety: more than
``config.FLASH_SAFE_MAX_PER_S`` full-frame flashes in any second is a
blocking ``flash_rate_exceeded`` finding unless the user opted in, and an
opted-in program is flagged ``contains_flashing`` on the record.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime

import request_scope
from schemas import program_duration
from timeline import Timeline


MANIFEST_VERSION = 1
VERIFICATION_VERSION = 1
NON_JUSTIFIABLE_FINDINGS = {
    "complete_preview_missing", "caption_render_evidence_missing",
    "corrupt_glyph", "music_starts_after_program", "invalid_music_span",
    "requested_duration_outside_target", "invisible_manual_caption",
    "requested_transitions_missing", "invisible_motion_graphic",
    "captions_against_request", "requested_clips_unused",
    "order_against_request",
}

# Craft heuristics, not defects. They stay in the durable record (and in the
# editor's render result) but never block completion or export: a dense,
# premium reel with ten aimed zooms and thirteen purposeful sounds used to
# fail verification on exactly these codes.
ADVISORY_FINDINGS = {
    "zoom_missing_purpose", "zoom_unmeasured_target",
    "zoom_crosses_shots_without_targets", "mechanical_zoom_pattern",
    "scene_unaware_reframe", "duplicate_broll_window",
    "music_missing_treatment_purpose", "sfx_missing_trigger",
    "mechanical_sfx_pattern", "taste_advisory", "sound_off_gap",
    "earn_its_place", "speaker_stutter",
}
ADVISORY_LABEL = "advisory: keep if intentional"
DEGRADED_FINDING = "device_degraded"
FLASH_FINDING = "flash_rate_exceeded"


_DEPARTMENTS = {
    "keep": "story", "speed": "story", "inserts": "broll",
    "overlays": "broll", "captions": "captions", "caption_mutes": "captions",
    "texts": "graphics", "vectors": "graphics", "motion": "graphics",
    "music": "music",
    "sfx": "sfx", "voiceover": "audio", "volume": "audio",
    "frame": "reframe", "effects": "motion_color", "source_clean": "cleanup",
    "patches": "cleanup",
}


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


def json_record(value):
    """Copy durable evidence without losing database timestamp types."""
    def encode(item):
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        raise TypeError(f"Unsupported verification value: {type(item).__name__}")
    return json.loads(json.dumps(value, default=encode))


def _new_items(before, after, key):
    old = {str(row.get("id")): row for row in before.get(key) or []
           if isinstance(row, dict) and row.get("id")}
    return [row for row in after.get(key) or []
            if not isinstance(row, dict) or not row.get("id")
            or str(row.get("id")) not in old
            or _canon(row) != _canon(old.get(str(row.get("id"))))]


def _effects(edl):
    return edl.get("effects") or {}


def build_change_manifest(project_id, version, previous, current, change,
                          description=None, tool=None):
    changed_keys = sorted(key for key in set(previous) | set(current)
                          if _canon(previous.get(key)) != _canon(current.get(key)))
    departments = sorted({_DEPARTMENTS.get(key, "timeline")
                          for key in changed_keys})
    if _canon(_effects(previous).get("zooms")) != _canon(
            _effects(current).get("zooms")):
        departments.append("zoom")
    departments = sorted(set(departments))
    global_effects = []
    if change and change.get("global"):
        global_effects.append("whole_program_visual_or_audio_state")
    for key in ("captions", "frame"):
        if key in changed_keys:
            global_effects.append(key)
    if "effects" in changed_keys:
        before_fx, after_fx = _effects(previous), _effects(current)
        for key in ("grade", "grade_custom", "transition", "fade_in_s",
                    "fade_out_s", "frame_shifts"):
            if _canon(before_fx.get(key)) != _canon(after_fx.get(key)):
                global_effects.append(key)

    expected = []
    failure_modes = []
    required = ["schema_and_timeline_validation", "complete_studio_preview"]
    for department in departments:
        if department == "captions":
            expected.append("readable captions synchronized to surviving speech")
            failure_modes += ["overflow_or_corrupt_glyphs", "face_or_ui_collision",
                              "panel_text_geometry_mismatch"]
            required += ["caption_layout_states", "caption_transcript_coverage"]
        elif department in {"zoom", "reframe", "motion_color"}:
            expected.append("motivated framing keeps its intended target visible")
            failure_modes += ["empty_or_clipped_crop", "stale_target_after_shot_change",
                              "mechanical_repetition"]
            required += ["every_affected_visual_cluster", "path_extremes_and_shot_boundaries"]
        elif department == "broll":
            expected.append("inserted media depicts the intended beat and joins cleanly")
            failure_modes += ["irrelevant_or_duplicate_media", "bad_entry_or_exit_junction",
                              "watermark_or_incompatible_rendition"]
            required += ["real_downloaded_frames", "entry_exit_junctions"]
        elif department in {"music", "sfx", "audio"}:
            expected.append("authored sound serves a named narrative window")
            failure_modes += ["late_or_short_music", "repetitive_unmotivated_sfx",
                              "dialogue_masking_or_bad_fades"]
            required += ["actual_audio_opening_speech_peaks_transitions_ending"]
        elif department == "story":
            expected.append("surviving program remains coherent without clipped words")
            failure_modes += ["midword_cut", "duplicate_source", "missing_payoff"]
            required += ["kept_transcript_and_story_review"]

    material = {"project_id": int(project_id), "edl_version": int(version),
                "changed_keys": changed_keys, "change": change or {},
                "description": description or "", "tool": tool or ""}
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_id": "cm_" + hashlib.sha256(
            _canon(material).encode("utf-8")).hexdigest()[:20],
        "project_id": int(project_id), "edl_version": int(version),
        "description": str(description or "")[:500],
        "tool": tool,
        "departments_changed": departments,
        "changed_keys": changed_keys,
        "output_ranges": list((change or {}).get("out_ranges") or []),
        "source_ranges": list((change or {}).get("source_ranges") or []),
        "asset_ranges": list((change or {}).get("asset_ranges") or []),
        "global_effects": sorted(set(global_effects)),
        "expected_behavior": list(dict.fromkeys(expected)),
        "potential_failure_modes": list(dict.fromkeys(failure_modes)),
        "required_verification_evidence": list(dict.fromkeys(required)),
    }


def _finding(code, department, message, evidence=None, repair=None,
             severity=None):
    if severity is None:
        severity = "advisory" if code in ADVISORY_FINDINGS else "error"
    return {"code": code, "department": department, "severity": severity,
            "message": message, "evidence": evidence or {},
            "repair": repair}


def is_blocking(row):
    """Whether one durable finding is a real defect that must be repaired."""
    if not isinstance(row, dict):
        return True
    return row.get("severity", "error") != "advisory" and \
        row.get("code") not in ADVISORY_FINDINGS


def _motion_strings(value):
    """Every string inside a motion graphic's params (copy, labels, lists)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _motion_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _motion_strings(item)


def _text_corruption_findings(edl):
    findings = []
    texts = [row.get("text") for row in edl.get("texts") or []]
    texts += [value for row in edl.get("motion") or []
              if isinstance(row, dict)
              for value in _motion_strings(row.get("params") or {})]
    captions = edl.get("captions")
    if isinstance(captions, list):
        texts += [row.get("text") for row in captions]
    elif isinstance(captions, dict):
        texts += [value for pair in captions.get("text_fixes") or []
                  for value in pair]
        texts += [value for rule in captions.get("corrections") or []
                  for value in (rule.get("from"), rule.get("to"))]
    for text in texts:
        value = str(text or "")
        if "\ufffd" in value or "\x00" in value:
            findings.append(_finding(
                "corrupt_glyph", "captions_graphics",
                "Text contains replacement/null glyphs and cannot ship.",
                {"text": value[:160]}, "replace the corrupt text and render-check it"))
    return findings


def _zoom_findings(edl, index):
    findings = []
    # cut steps (conceal_jump_cuts) are hygiene, not moves: their purpose,
    # measured aim and equal strength are by construction
    zooms = [z for z in _effects(edl).get("zooms") or []
             if not (isinstance(z, dict) and z.get("cut_step"))]
    try:
        timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                            edl.get("speed") or [])
    except Exception:
        timeline = None
    shots = list((index or {}).get("shots") or [])
    for zoom in zooms:
        zid = zoom.get("id") or "unknown"
        purpose = str(zoom.get("purpose") or "").strip()
        evidence_ids = list(zoom.get("target_evidence_ids") or [])
        if not purpose:
            findings.append(_finding(
                "zoom_missing_purpose", "zoom",
                f"Zoom {zid} has no recorded narrative purpose.", {"id": zid},
                "record the visible/narrative trigger or remove the zoom"))
        if not zoom.get("target_measured") and not evidence_ids:
            findings.append(_finding(
                "zoom_unmeasured_target", "zoom",
                f"Zoom {zid} has no measured target evidence.", {"id": zid},
                "inspect the target over the whole window, retarget, or remove"))
        if timeline and shots:
            samples = []
            start, end = float(zoom.get("start", 0)), float(zoom.get("end", 0))
            for i in range(max(3, int(math.ceil(end - start)) + 1)):
                out_t = start + (end - start) * i / max(1, max(3, int(math.ceil(end-start))+1)-1)
                src_t = timeline.out_to_src(out_t)
                if src_t is None:
                    samples.append("insert")
                    continue
                shot_id = next((shot.get("id") for shot in shots
                                if float(shot.get("start", 0)) <= src_t
                                <= float(shot.get("end", 0)) + .02), None)
                samples.append(shot_id)
            identities = {value for value in samples if value is not None}
            if len(identities) > 1 and not (zoom.get("mode") == "path"
                                            and len(zoom.get("path") or []) >= len(identities)):
                findings.append(_finding(
                    "zoom_crosses_shots_without_targets", "zoom",
                    f"Zoom {zid} crosses {len(identities)} shot/asset states without "
                    "shot-specific target paths.", {"states": sorted(map(str, identities))},
                    "split at shot boundaries, add shot-specific path targets, widen, or remove"))
    if len(zooms) >= 3:
        ordered = sorted(zooms, key=lambda row: float(row.get("start", 0)))
        gaps = [round(float(b.get("start", 0)) - float(a.get("start", 0)), 2)
                for a, b in zip(ordered, ordered[1:])]
        strengths = [round(float(row.get("strength", 0)), 2) for row in ordered]
        assets = [str(row.get("purpose") or "") for row in ordered]
        mechanical = (max(gaps) - min(gaps) <= .12
                      and max(strengths) - min(strengths) <= .03)
        if mechanical and len(set(assets)) < len(assets):
            findings.append(_finding(
                "mechanical_zoom_pattern", "zoom",
                "Zooms repeat at equal spacing/strength without distinct purposes.",
                {"ids": [row.get("id") for row in ordered], "gaps": gaps},
                "keep only motivated moments or record distinct evidence-backed purposes"))
    return findings


def _reframe_findings(edl, index):
    frame = edl.get("frame") or {}
    if frame.get("ratio") in (None, "source") or frame.get("mode") != "crop":
        return []
    shots = list((index or {}).get("shots") or [])
    # a measured face-following span (frame.follow) is shot-specific framing
    track = list(frame.get("focus_track") or []) + [
        sp for sp in frame.get("follow") or [] if isinstance(sp, dict)]
    timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                        edl.get("speed") or [])
    active = [(shot, max(float(shot.get("start", 0)), s),
               min(float(shot.get("end", 0)), e))
              for shot in shots for s, e in timeline.segs
              if min(float(shot.get("end", 0)), e) >
              max(float(shot.get("start", 0)), s) + .001]
    if len({(sh.get("start"), sh.get("end")) for sh, _, _ in active}) <= 1:
        return []
    # Only ordinary, stationary covers hide the source for their entire span.
    # PNG alpha, screen pins, animation and rotation can expose the source.
    covers = sorted((float(ov.get("start", 0)), float(ov.get("start", 0)) +
                     float(ov.get("duration_s", 0))) for ov in edl.get("overlays") or []
                    if ov.get("fit") == "cover" and ov.get("kind") == "video"
                    and ov.get("opacity") in (None, 1, 1.0)
                    and ov.get("x", .5) == .5 and ov.get("y", .5) == .5
                    and not any(ov.get(key) for key in
                                ("entrance", "exit", "screen", "rotation", "motion_motif")))

    def hidden(a, b):
        cursor = a
        for left, right in covers:
            if left > cursor + .001:
                break
            cursor = max(cursor, right)
        return cursor >= b - .001

    uncovered = []
    for shot, start, end in active:
        visible_ranges = timeline.span_to_out(start, end)
        if visible_ranges and all(hidden(a, b) for a, b in visible_ranges):
            continue
        cursor = start
        for row in sorted(track, key=lambda row: float(row.get("t0", 0))):
            left, right = float(row.get("t0", 0)), float(row.get("t1", 0))
            if left > cursor + .001:
                break
            if right > cursor:
                cursor = right
        if cursor < end - .001:
            uncovered.append({"shot_id": shot.get("id"),
                              "source_range": [start, end],
                              "timeline_ranges": timeline.span_to_out(start, end)})
    if uncovered:
        return [_finding(
            "scene_unaware_reframe", "reframe",
            f"Global crop lacks shot-specific focus evidence for {len(uncovered)} scene(s).",
            {"shot_ids": list(dict.fromkeys(r["shot_id"] for r in uncovered)),
             "intervals": uncovered},
            "measure every scene and author focus_track spans or use a safe fit mode")]
    return []


def _media_findings(edl):
    findings = []
    rows = []
    for kind in ("inserts", "overlays"):
        for row in edl.get(kind) or []:
            rows.append((kind, row))
    by_key = {}
    for kind, row in rows:
        by_key.setdefault(row.get("asset_key"), []).append((kind, row))
    for key, uses in by_key.items():
        if not key or len(uses) < 2:
            continue
        windows = {(kind, round(float(row.get("source_start_s") or 0), 2),
                    str(row.get("purpose") or "")) for kind, row in uses}
        if len(windows) < len(uses):
            findings.append(_finding(
                "duplicate_broll_window", "broll",
                "The same media rendition/source window is reused without a distinct purpose.",
                {"asset_key": key, "ids": [row.get("id") for _kind, row in uses]},
                "choose a diverse candidate/source window or justify an intentional motif"))
    return findings


def _cleanup_findings(edl):
    findings = []
    for patch in edl.get("patches") or []:
        for region in patch.get("regions") or []:
            try:
                area = float(region.get("w") or 0) * float(region.get("h") or 0)
            except (TypeError, ValueError):
                continue
            if area < .08:
                continue
            findings.append(_finding(
                "destructive_cleanup_region", "cleanup",
                (f"Cleanup region {region.get('id') or patch.get('id')} "
                 f"reconstructs {area * 100:.1f}% of every covered frame; "
                 "a region this large can replace the subject or scene."),
                {"patch_id": patch.get("id"), "region": region,
                 "frame_area_fraction": round(area, 4)},
                ("remove or tightly remeasure the cleanup region, then inspect "
                 "its full time span frame by frame")))
    return findings


def _caption_findings(edl):
    captions = edl.get("captions")
    if not isinstance(captions, list):
        return []
    timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                        edl.get("speed") or [])
    findings = []
    for index, item in enumerate(captions):
        try:
            visible = timeline.span_to_out(
                float(item.get("start")), float(item.get("end")))
        except (AttributeError, TypeError, ValueError):
            visible = []
        if visible:
            continue
        findings.append(_finding(
            "invisible_manual_caption", "captions",
            (f"Manual caption {index + 1} is entirely outside the kept "
             "source ranges, so it compiles to no visible pixels."),
            {"caption_index": index, "caption": item,
             "kept_source_ranges": edl.get("keep") or []},
            ("remove the phantom caption or remap its start/end to surviving "
             "source seconds; use designed text for output-clock titles")))
    return findings


def _audio_findings(edl):
    findings = []
    duration = float(program_duration(edl) or 0.0)
    for row in edl.get("music") or []:
        if row.get("mute"):
            continue
        mid = row.get("id") or "music"
        start, end = float(row.get("start", 0)), float(row.get("end", 0))
        if start >= max(0.0, duration - .05):
            findings.append(_finding(
                "music_starts_after_program", "music",
                f"Music {mid} starts at/after the program ending.",
                {"start": start, "program_end": duration},
                "move it into the intended span or remove it"))
        if end <= start or end > duration + .06:
            findings.append(_finding(
                "invalid_music_span", "music",
                f"Music {mid} has a span inconsistent with the program.",
                {"start": start, "end": end, "program_end": duration},
                "repair its start/end/fades and verify the ending"))
        if not str(row.get("purpose") or "").strip():
            findings.append(_finding(
                "music_missing_treatment_purpose", "music",
                f"Music {mid} was not tied to the treatment/energy arc.", {"id": mid},
                "audition against the treatment and record its intended role"))
    sfx = list(edl.get("sfx") or [])
    for row in sfx:
        if not str(row.get("purpose") or "").strip():
            findings.append(_finding(
                "sfx_missing_trigger", "sfx",
                f"SFX {row.get('id')} has no visible/narrative transition trigger.",
                {"at": row.get("at"), "asset_key": row.get("storage_key")},
                "name the on-screen/narrative trigger or remove the sound"))
    # A motion graphic's owned cue stack ('mg_<id>_sfxN', e.g. one tick per
    # list item) is ONE designed sound moment that moves with its graphic,
    # so it counts once in the repetition pattern below.
    seen_owners = set()
    collapsed = []
    for row in sorted(sfx, key=lambda row: float(row.get("at", 0))):
        sid = str(row.get("id") or "")
        owner = (sid.rsplit("_sfx", 1)[0]
                 if sid.startswith("mg_") and "_sfx" in sid else None)
        if owner:
            if owner in seen_owners:
                continue
            seen_owners.add(owner)
        collapsed.append(row)
    sfx = collapsed
    if len(sfx) >= 3:
        ordered = sorted(sfx, key=lambda row: float(row.get("at", 0)))
        gaps = [round(float(b.get("at", 0)) - float(a.get("at", 0)), 2)
                for a, b in zip(ordered, ordered[1:])]
        same_asset = len({row.get("storage_key") for row in ordered}) == 1
        if same_asset and max(gaps) - min(gaps) <= .12:
            findings.append(_finding(
                "mechanical_sfx_pattern", "sfx",
                "One SFX asset repeats at mechanical intervals.",
                {"ids": [row.get("id") for row in ordered], "gaps": gaps},
                "remove decorative hits or vary only when an intentional motif has distinct triggers"))
    return findings


_PROGRAM_DURATION_RE = re.compile(
    r"\b(\d{1,4}(?:\.\d+)?)(?:\s*(?:-|–|—|to)\s*"
    r"(\d{1,4}(?:\.\d+)?))?\s*[-–]?\s*(s|sec(?:ond)?s?|min(?:ute)?s?|h|hr?s?|hours?)\b", re.I)
_DURATION_CEILING = r"(?:max(?:imum)?(?: of)?|at most|no more than|no longer than|up to|under|less than)"


def request_text_for(ctx):
    """Hard request constraints come from the customer, not repair prose."""
    customer = getattr(ctx, "verification_request", None)
    return customer if customer is not None else getattr(ctx, "user_message", "")


# Nouns a duration can describe when it is the SOURCE, not the requested
# programme: "from this 4.5-minute talking-head video", "Cut this 45 minute
# podcast", "I have a 10 minute video". Production read every one of those as
# a hard programme length (1105's real prompt became 264.6-275.4 s; "a 30
# second reel from this 10 minute video" became 588-612 s) and then forced
# repair_required with "rebuild the story to the requested duration".
_SOURCE_NOUN = (r"(?:video|videos|podcast|stream|livestream|live|interview|"
                r"recording|clip|clips|footage|talk|episode|vlog|source|"
                r"session|webinar|lecture|movie|film|match|game|gameplay|"
                r"upload|file|conversation|presentation|speech|sermon|class|"
                r"meeting|call|show|broadcast|wedding|ceremony|vod)")
_PROGRAM_NOUN = (r"(?:video|reel|ad|advertisement|film|montage|short|edit|"
                 r"program|programme|runtime|duration|trailer|teaser|"
                 r"highlight|highlights|recap|cut|version|clip|story|tiktok)")
# Elements inside a programme whose own length is not the programme's.
_ELEMENT_NOUN = (r"\b(?:intro|outro|title|titles|shot|shots|scene|scenes|"
                 r"segment|segments|section|sections|transition|transitions|"
                 r"hook|caption|captions|text|zoom|zooms|music|song|track|"
                 r"sfx|logo|card|pause|gap|freeze|slow[- ]?mo|each|every|"
                 r"per)\b")
_CREATE_VERB = (r"\b(?:make|making|made|create|creating|cut|cutting|trim|"
                r"shorten|keep|turn|edit|produce|give|want|need|build|render|"
                r"export|condense|compress|reduce|bring|get|deliver|assemble|"
                r"put together|compile)\b")


def _describes_source(before, after):
    """Whether a duration is the length of what the user HAS, not wants."""
    # "a trailer of this interview of 4 minutes": the interview is 4 minutes.
    if re.search(r"\b(?:interview|podcast|stream|livestream|recording|"
                 r"footage|talk|episode|lecture|webinar|match|game|meeting|"
                 r"call|speech|sermon|session|vod|conversation)\s+"
                 r"(?:of|that is|which is|lasting|running|that runs|"
                 r"that lasts)\s+(?:about\s+|around\s+|roughly\s+)?$", before):
        return True
    followed_by_source = re.match(
        r"\s*[-–]?\s*(?:long\s+)?(?:[\w/&'-]+\s+){0,3}" + _SOURCE_NOUN + r"\b",
        after)
    if not followed_by_source:
        return False
    det = (r"(?:this|that|these|those|my|our|his|her|their|your|the|a|an|"
           r"one|two|three|four|five|several)")
    adj = r"(?:(?:whole|entire|full|original|raw|long|full-length|recorded)\s+)"
    if re.search(r"\b(?:this|that|these|those|my|our|his|her|their|your)\s+"
                 + adj + r"?$", before):
        return True
    if re.search(r"\b(?:from|out of|of|in|on|into|inside|through|across|"
                 r"over)\s+(?:" + det + r"\s+)?" + adj + r"?$", before):
        return True
    return bool(re.search(
        r"\b(?:have|has|had|got|uploaded|upload|recorded|record|shot|filmed|"
        r"attached|sent|gave|given|is|was|it's|its|there's)\s+(?:"
        + det + r"\s+)?" + adj + r"?$", before))


def _program_duration_strength(text, match):
    """None (not a programme length), "strong" or "weak".

    Deliberately prefer no deterministic constraint over inventing one from
    ambiguous prose. The editorial reviewer still receives the full brief. A
    "weak" reading (a programme noun after the number with no make/cut verb
    and no explicit length cue in the clause) is recorded as advice, never a
    hard repair requirement.
    """
    before = re.split(r"[\n.!?;]", text[:match.start()])[-1].lower()
    after = re.split(r"[\n.!?;]", text[match.end():])[0].lower()
    # Recovery instructions and user briefs often explicitly prohibit an
    # earlier bad duration: "Do not reset, rebuild, or shorten it to 10s."
    # Keep the negation across commas/list items. Ambiguous mixed clauses
    # belong to the editorial reviewer, never a fabricated hard constraint.
    if re.search(r"\b(?:do not|don't|never|avoid|must not|should not|cannot)\b", before) \
            or re.search(r"\bno\s*$", before):
        return None
    if re.search(r"\b(?:at|by|after|before|from|first|last|within)\s+"
                 r"(?:(?:around|about|roughly|the)\s+)*$", before):
        return None
    if _describes_source(before, after):
        return None
    clause = re.split(r"[,:]", before)[-1] if not re.search(
        r"\b(?:length|duration|runtime|run[\s-]?time)\s*[:=-]?\s*$",
        before) else before
    element = re.search(_ELEMENT_NOUN, clause)
    verb = re.search(_CREATE_VERB, before)
    program = _PROGRAM_NOUN
    approx = r"(?:(?:about|around|roughly|approximately|approx\.?|~)\s*)?"
    ceiling = r"(?:" + _DURATION_CEILING + r"\s+)?"
    # "Target length: 60-90 seconds", "Length should be about 60 seconds",
    # "duration 45s", "runtime of 10 minutes".
    if not element and re.search(
            r"\b(?:length|duration|runtime|run[\s-]?time)\s*"
            r"(?:(?:should|must|will|needs? to|has to|has got to)\s+be\s+|"
            r"is\s+|of\s+|:\s*|=\s*|-\s*|–\s*|to\s+be\s+)?"
            + approx + ceiling + r"$", before):
        return "strong"
    if re.match(r"\s*[-–]?\s*(?:(?:vertical|horizontal|final|total|long|"
                r"instagram|tiktok|youtube|gaming|wedding|podcast|story/reel|"
                r"story|9:16|16:9|cinematic|highlight|promo|promotional|"
                r"social|travel|product)\s+){0,6}" + program + r"\b", after):
        if element and not verb:
            return None
        return "strong" if verb else "weak"
    if not element and re.match(r"\s*(?:long|in length|total|max(?:imum)?)\b",
                                after):
        return "strong"
    if re.search(r"\b" + program + r"\b[^,;]{0,35}"
                 r"(?:to|of|is|be|last|length|:|=)\s*" + approx + ceiling
                 + r"$", before) and not element:
        return "strong"
    if re.search(r"\b(?:make|cut|trim|shorten|keep)\s+"
                 r"(?:(?:this|the|my|a)\s+)?" + program + r"\s*" + ceiling
                 + r"$", before):
        return "strong"
    if re.search(r"\b" + program + r"\s+" + _DURATION_CEILING + r"\s+$", before):
        return "strong"
    if re.search(
            r"\b(?:make|cut|trim|shorten|keep|bring|get|want|need|like|"
            r"prefer)\s+(?:it|this|the whole thing|the (?:video|edit|reel|"
            r"film|final(?: video)?))\s*(?:to\s+be\s+|be\s+)?"
            r"(?:(?:down\s+)?to\s+)?" + approx + ceiling + r"$", before):
        return "strong"
    return None


def _program_duration_context(text, match):
    """Kept for callers that only need a yes/no."""
    return _program_duration_strength(text, match) is not None


def requested_duration_target(request_text):
    """Return an explicit requested program-duration envelope, if present.

    This intentionally ignores bare timestamps such as "at 18 seconds" and
    the length of the source ("from this 10 minute video"). It only
    recognizes a duration range or language that asks to make/cut a video to
    a named length, so an effect placement does not become a false
    whole-program constraint. The latest strong instruction wins ("Make a
    30-40 second ad. Actually make it 25 seconds."); ``advisory`` is True
    when only a weak reading exists.
    """
    text = str(request_text or "")
    strong, weak = [], []
    for m in _PROGRAM_DURATION_RE.finditer(text):
        strength = _program_duration_strength(text, m)
        if strength == "strong":
            strong.append(m)
        elif strength == "weak":
            weak.append(m)
    if not strong and not weak:
        return None
    match = (strong or weak)[-1]
    advisory = not strong
    unit = match.group(3).lower()
    multiplier = 3600 if unit.startswith('h') else 60 if unit.startswith('min') else 1
    if match.group(2):
        lo, hi = float(match.group(1)) * multiplier, float(match.group(2)) * multiplier
        if 0.2 <= lo <= 86400 and 0.2 <= hi <= 86400:
            return {"min_s": min(lo, hi), "max_s": max(lo, hi),
                    "approximate": False, "request": match.group(0),
                    "advisory": advisory}
        return None
    target = float(match.group(1)) * multiplier
    if not 0.2 <= target <= 86400:
        return None
    if re.search(r"\b" + _DURATION_CEILING + r"\s*$",
                 text[:match.start()], re.I):
        # A maximum is a ceiling, not a request to pad a shorter complete
        # thought up to the limit. Only frame-rounding tolerance applies in
        # _request_findings, never the +/- 2% exact-target envelope below.
        return {"min_s": 0.2, "max_s": target,
                "approximate": False, "request": match.group(0),
                "advisory": advisory}
    nearby = text[max(0, match.start() - 24):match.end() + 10].lower()
    approximate = any(word in nearby for word in
                      ("about", "around", "roughly", "approximately", "like",
                       "approx", "~"))
    tolerance = max(1.5, target * 0.10) if approximate else max(0.35, target * .02)
    return {"min_s": max(0.2, target - tolerance),
            "max_s": target + tolerance, "target_s": target,
            "approximate": approximate, "request": match.group(0),
            "advisory": advisory}


def _requested_transitions(request_text):
    """Require affirmative intent before imposing a transition presence gate.

    A mention in an avoidance list is not a request. Keep this deliberately
    narrow; the visual reviewer still receives the full editorial brief.
    Later blanket refusals supersede an earlier request, while avoiding a
    particular style does not cancel an explicit request for another style.
    """
    requested = False
    # A negation governs the whole comma list that follows it in its
    # sentence: "Do not change the color, add any transitions, add music, or
    # alter the footage" (1794, request 36118) is a list of things NOT to do;
    # splitting it at the commas read "add any transitions" as a request.
    sentences = re.split(r"[.!?;\n]+|\b(?:but|however)\b",
                         str(request_text or "").lower())
    clauses = []
    for sentence in sentences:
        neg = re.search(r"\b(?:do not|don't|dont|never|must not|mustn't|"
                        r"should not|shouldn't|without|avoid|nothing like)\b",
                        sentence)
        if neg and re.search(r"\btransitions?\b", sentence[neg.end():]) \
                and "," in sentence[neg.end():]:
            head = sentence[:neg.start()]
            clauses.extend(re.split(r",+", head))
            # the governed list: only a blanket "any/all transitions" in it
            # is a refusal; nothing in it is a request
            governed = sentence[neg.start():]
            if re.search(r"\b(?:any|all)\s+transitions?\b|"
                         r"\b(?:add|use|include|apply)\s+transitions?\b",
                         governed):
                clauses.append("no transitions")
            continue
        clauses.extend(re.split(r",+", sentence))
    for clause in clauses:
        if not re.search(r"\btransitions?\b", clause):
            continue
        if re.search(
                r"\b(?:no|without|remove|avoid)\s+"
                r"(?:(?:any|all|the)\s+)?transitions?\b|"
                r"\b(?:do not|don't|never)\s+(?:add|use|include|apply)\s+"
                r"(?:(?:any|all|the)\s+)?transitions?\b", clause):
            requested = False
            continue
        if re.search(r"\b(?:avoid|no|not|without|don't|never)\b", clause):
            continue
        if re.search(r"\b(?:add|use|include|apply|want|need|with)\s+"
                     r"(?:[a-z-]+\s+){0,5}transitions?\b", clause):
            requested = True
    return requested


def _request_findings(edl, request_text, media_keys=None):
    """The customer's own explicit constraints (the request contract).

    ``media_keys``: the customer's supplied clips/photos, passed only when
    the brief asks to use all of them (agent_loop._request_media_keys)."""
    findings = []
    target = requested_duration_target(request_text)
    if target:
        actual = float(program_duration(edl) or 0.0)
        if not target["min_s"] - .02 <= actual <= target["max_s"] + .02:
            # A weak reading (no make/cut verb, no length cue) advises; it
            # never forces a rebuild of a finished edit to a guessed length.
            findings.append(_finding(
                ("requested_duration_unclear" if target.get("advisory")
                 else "requested_duration_outside_target"), "story",
                (f"The program is {actual:.2f}s, outside the user's "
                 + ("possible" if target.get("advisory") else "explicit")
                 + f" duration target ({target['min_s']:.2f}-"
                 f"{target['max_s']:.2f}s)."),
                {"actual_s": round(actual, 3), "target": target},
                "rebuild the story to the requested duration and render-check it",
                severity="advisory" if target.get("advisory") else None))

    # A visual pass can say a montage looks clean while a concrete requested
    # treatment is simply absent. This happened on a paid user's ferry edit:
    # the complete preview passed even though the EDL contained no transition
    # at all. Keep the detector deliberately narrow and honor explicit
    # negation; this is contract fulfillment, not style preference.
    # "Use all 50 clips" (1918) / "out of all the clips I gave you, trimmed
    # one" (1912): coverage of the supplied media is part of the contract.
    every, _n = request_scope.wants_all_clips(request_text)
    if every and media_keys:
        used, available = request_scope.clip_coverage(edl, media_keys)
        if available and used < max(1, int(available * 0.9 + 0.999)) \
                and available - used >= 1:
            findings.append(_finding(
                "requested_clips_unused", "story",
                (f"The user asked to use every supplied clip, but the edit "
                 f"uses {used} of {available}."),
                {"used": used, "available": available},
                "place the unused clips (list_assets shows them) so every "
                "one appears, then render-check it"))
    if request_scope.wants_chronological(request_text) \
            and not request_scope.keep_in_source_order(edl):
        findings.append(_finding(
            "order_against_request", "story",
            "The user asked for the footage in its original order, but the "
            "kept segments are reordered.",
            {"keep": list((edl.get("keep") or [])[:20])},
            "restore source order for the kept segments and render-check it"))
    # "no subtitles" (1924: "not shorts but everything, in order, not
    # jumbled, and no subtitles" after a captioned, reordered cut).
    if edl.get("captions") and request_scope.wants_no_captions(request_text):
        findings.append(_finding(
            "captions_against_request", "captions",
            "The user asked for no subtitles/captions, but the latest EDL "
            "has captions on.",
            {"request_excerpt": str(request_text or "")[:300]},
            "turn captions off (add_captions mode='off') and render-check "
            "it"))
    ask = str(request_text or "").lower()
    if _requested_transitions(ask) \
            and not ((edl.get("effects") or {}).get("transition")):
        findings.append(_finding(
            "requested_transitions_missing", "motion",
            "The user explicitly requested transitions, but the latest EDL "
            "contains no transition treatment.",
            {"request_excerpt": ask[:500]},
            "add a restrained transition treatment and render-check it"))
    return findings


def _motion_findings(edl):
    """A motion graphic that starts at/after the program end draws nothing.

    Program-clock graphics survive a later cut unchanged, so shortening the
    story can strand a title or card past the last frame (and its owned
    sounds with it). That is an invalid span, like a caption in a cut.
    """
    findings = []
    duration = float(program_duration(edl) or 0.0)
    for row in edl.get("motion") or []:
        if not isinstance(row, dict):
            continue
        try:
            start = float(row.get("start", 0))
        except (TypeError, ValueError):
            continue
        if duration > 0 and start >= duration - .05:
            findings.append(_finding(
                "invisible_motion_graphic", "graphics",
                (f"Motion graphic {row.get('id') or '?'} starts at "
                 f"{start:.2f}s, at/after the {duration:.2f}s program "
                 "ending, so it never appears."),
                {"id": row.get("id"), "start": start,
                 "program_end": duration},
                ("move it onto the beat it belongs to with "
                 "set_motion_graphic, or remove it")))
    return findings


def _sound_off_findings(edl, index):
    """Speech a sound-off viewer cannot read: a spoken span longer than
    caption_carry.SOUND_OFF_GAP_S with neither a caption nor a graphic or
    text on screen showing it (a whole-window mute over words the graphic
    does not carry, a graphic that leaves no caption band clear, a manual
    caption mute). One advisory naming the spans and their fix."""
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript") \
            or not (index or {}).get("words") or not edl.get("keep"):
        return []
    try:
        import caption_carry
        timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                            edl.get("speed") or [])
        gaps = caption_carry.sound_off_gaps(edl, index, timeline)
    except Exception as exc:  # noqa: BLE001 — an advisory never breaks review
        print(f"[verify] sound-off check skipped: {type(exc).__name__}", flush=True)
        return []
    if not gaps:
        return []
    shown = "; ".join(
        f"{g['start']:.2f}-{g['end']:.2f}s \"{g['said']}\" ({g['cause']}: {g['fix']})"
        for g in gaps[:3])
    more = f" (+{len(gaps) - 3} more)" if len(gaps) > 3 else ""
    total = sum(g["duration_s"] for g in gaps)
    return [_finding(
        "sound_off_gap", "captions",
        (f"Sound-off gap: {len(gaps)} spoken span(s) ({total:.1f}s) have neither a "
         f"caption nor a graphic showing them — {shown}{more}."),
        {"gaps": gaps[:12], "min_gap_s": caption_carry.SOUND_OFF_GAP_S},
        ("keep every spoken word readable: leave graphics' mute_captions unset so "
         "they hide only the words they show, and keep graphics off the caption "
         "band"))]


def _negation_findings(edl, index):
    """A negation heard but hidden while the rest of its sentence reaches
    the screen (caption_carry.hidden_negations): the screen says the
    opposite of what was said, so it blocks like any unfaithful claim.
    CHM 2013 run (23 Oct 2026, 4505 v31): a hook-tier slam owned the
    captions over 'Um, I don't think' and the next caption read "it's
    good." for "I don't think it's good"; every audit passed it."""
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript") \
            or not (index or {}).get("words") or not edl.get("keep"):
        return []
    try:
        import caption_carry
        timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                            edl.get("speed") or [])
        negs = caption_carry.hidden_negations(edl, index, timeline)
    except Exception as exc:  # noqa: BLE001 — a check never breaks review
        print(f"[verify] negation check skipped: {type(exc).__name__}", flush=True)
        return []
    return [_finding(
        "negation_hidden", "captions",
        (f"Negation hidden at {g['at']:.2f}s: said \"{g['said']}\" but the "
         f"screen shows \"{g['reads']}\" without '{g['negation']}' — "
         f"{g['cause'].split(' — ', 1)[-1]}."),
        {"negation": g}, g["fix"]) for g in negs]


def _repeat_findings(edl, index):
    """Words heard twice in a row in the kept programme (audit.
    repeated_phrases). A repeated phrase ('you don't you don't') or a repeat
    a cut made (the end of one kept span said again by the next: 'to be |
    to be') is a defect the edit can remove: blocking. A single word the
    speaker doubles inside one take ('we we') is an advisory: often too
    tight to cut cleanly. Earnings run (w1023): 4563 v21's record said
    passed over both of its restarts; 4562 shipped eight 'we we' / 'the the'
    doubles in 30 s with nothing said."""
    words = (index or {}).get("words") or []
    keep = edl.get("keep") or []
    if not words or not keep:
        return []
    try:
        import audit
        hits = audit.repeated_phrases(keep, words, audit.ducked_spans(edl))
        tl = Timeline(keep, edl.get("inserts") or [], edl.get("speed") or [])
    except Exception as exc:  # noqa: BLE001 — a check never breaks review
        print(f"[verify] repeat check skipped: {type(exc).__name__}", flush=True)
        return []

    def at(h):
        t = tl.src_to_out(float(h["first"][0]))
        return f"{t:.2f}s" if t is not None else f"source {h['first'][0]:.2f}"

    out = []
    for h in hits:
        if h["n"] < 2 and not h["across_join"]:
            continue
        if h["across_join"]:
            fix = (f"the cut repeats it: end the kept span before "
                   f"{h['first'][0]:.2f} (the first '{h['text'].split()[0]}') "
                   f"or start the next after {h['second'][1]:.2f}")
        else:
            fix = (f"cut source {h['cut'][0]:.2f}-{h['cut'][1]:.2f} (the "
                   "first saying) with cut_range or keep_segments")
        out.append(_finding(
            "repeated_phrase", "story",
            (f"Repeated words: \"{h['text']}\" at {at(h)} (source "
             f"{h['first'][0]:.2f}-{h['second'][1]:.2f}) — {fix}."),
            {"repeat": h},
            "cut one saying (check_speech names it), or justify a deliberate "
            "repetition from the audio"))
    single = [h for h in hits if h["n"] == 1 and not h["across_join"]]
    if single:
        shown = "; ".join(f"\"{h['text']}\" at {at(h)} (cut "
                          f"{h['cut'][0]:.2f}-{h['cut'][1]:.2f})"
                          for h in single[:6])
        more = f" (+{len(single) - 6} more)" if len(single) > 6 else ""
        out.append(_finding(
            "speaker_stutter", "story",
            (f"Speaker stutter: {len(single)} doubled word(s) heard — "
             f"{shown}{more}. Cut the first saying where a quiet gap "
             "allows it; a hook or payoff line should be clean."),
            {"repeats": single[:12]}, ADVISORY_LABEL))
    return out


def _earn_its_place_findings(edl, index, request_text=None):
    """edit_review's advisory "earn its place" notes on a short (hook,
    graphic budget, payoff, identification, trim, restraint), folded into
    ONE advisory finding: the leading notes in the message, every note with
    its full fix in the evidence. Never blocking, never raising."""
    try:
        import edit_review
        notes = edit_review.review(edl, index, request_text=request_text)
    except Exception as exc:  # noqa: BLE001 — an advisory never breaks review
        print(f"[verify] earn-its-place review skipped: {type(exc).__name__}",
              flush=True)
        return []
    if not notes:
        return []
    return [_finding(
        "earn_its_place", "craft", edit_review.summary(notes),
        {"review_version": edit_review.REVIEW_VERSION,
         "notes": [{key: note[key] for key in ("code", "at", "message", "fix")}
                   for note in notes[:16]]},
        "act on the notes that hurt this short; " + ADVISORY_LABEL)]


def device_findings(preview):
    """The DEGRADED gate (INTERFACES.md §6.3): one blocking finding per
    device the render did not draw at full quality, from the render's own
    ``devices`` report (attached by the renderer from the fxgraph plan)."""
    findings = []
    for row in (preview or {}).get("devices") or []:
        if not isinstance(row, dict) or row.get("rung") == "full":
            continue
        name = row.get("device") or row.get("id") or "device"
        rung = row.get("rung") or "omit"
        what = {"omit": "was left out", "substitute": "rendered as a "
                "substitute", "reduced": "rendered at a reduced rung"}.get(
                    rung, f"rendered at rung {rung}")
        findings.append(_finding(
            DEGRADED_FINDING, "devices",
            (f"Device {name} ({row.get('id')}, {row.get('family') or '?'}) "
             f"{what}: {row.get('reason') or 'no reason given'}. Fix the "
             "cause and re-render, or justify this finding (say what "
             "degraded and why that is acceptable)."),
            {"device": row.get("device"), "item_id": row.get("id"),
             "family": row.get("family"), "stage": row.get("stage"),
             "rung": rung, "reason": row.get("reason")},
            ("fix the cause (warm the sidecar, re-render) or acknowledge it: "
             "justify_verification_findings naming what degraded and why "
             "that is acceptable — an export never ships a degraded device "
             "silently")))
    return findings


def unacknowledged_degradations(record, rows):
    """The DEGRADED gate at export: the rows of a FINAL render's ``devices``
    report that the version's verification record has not acknowledged.

    An acknowledgement (justify_verification_findings on a device_degraded
    finding) covers that device instance at that rung only: a final that
    degrades further (substitute -> omit) or degrades a device the preview
    drew in full is not covered, so the final path holds it instead of
    shipping it silently. Empty when nothing degraded."""
    record = record or {}
    if isinstance(record.get("record"), dict):
        record = record["record"]
    by_id = {f.get("finding_id"): f for f in record.get("findings") or []
             if isinstance(f, dict)}
    acked = set()
    for j in record.get("justifications") or []:
        if not isinstance(j, dict) or j.get("code") != DEGRADED_FINDING:
            continue
        ev = (by_id.get(j.get("finding_id")) or {}).get("evidence") or {}
        acked.add((ev.get("item_id"), ev.get("device"), ev.get("rung")))
    out = []
    for row in rows or ():
        if not isinstance(row, dict) or row.get("rung") == "full":
            continue
        if (row.get("id"), row.get("device"), row.get("rung")) not in acked:
            out.append(row)
    return out


def hold_final(record, rows, *, project_id=None, version=None):
    """The final-export DEGRADED gate (Wave 1, 1F): ``rows`` are a FINAL
    render's device rows that ``unacknowledged_degradations`` held. Returns
    the verification record with one blocking ``device_degraded`` finding
    per held row (marked ``final``), so the editor can fix the cause or
    acknowledge exactly that instance with justify_verification_findings
    (finding id or the code ``device_degraded``). Idempotent: a row already
    recorded is not added twice. Never mutates ``record``."""
    record = record or {}
    if isinstance(record.get("record"), dict):
        record = record["record"]
    updated = json_record(record) if record else {
        "verification_version": VERIFICATION_VERSION,
        "project_id": int(project_id) if project_id is not None else None,
        "edl_version": int(version) if version is not None else None,
        "findings": [], "unresolved_findings": [], "justifications": [],
        "advisories": [], "repairs": []}
    findings = list(updated.get("findings") or [])
    unresolved = list(updated.get("unresolved_findings") or [])
    have = {row.get("finding_id") for row in findings}
    for row in device_findings({"devices": rows}):
        row["evidence"]["final"] = True
        row["message"] = "Final export: " + row["message"]
        row["finding_id"] = "vf_" + hashlib.sha256(
            _canon(row).encode("utf-8")).hexdigest()[:16]
        if row["finding_id"] in have:
            continue
        have.add(row["finding_id"])
        findings.append(row)
        unresolved.append(row)
    updated["findings"] = findings
    updated["unresolved_findings"] = unresolved
    if unresolved:
        updated["status"] = "repair_required"
    return updated


def final_hold_message(held):
    """One line for the export path: what a held final degraded."""
    names = ", ".join(f"{r.get('device') or r.get('id')} ({r.get('rung')})"
                      for r in held[:4])
    return ("This export is held: " + names + (" and more" if len(held) > 4
                                               else "")
            + " rendered below full quality. Ask the editor to fix it, or to "
              "confirm it is acceptable, then export again.")


def flash_assessment(edl, index=None, request_text=None):
    """flash_safety.assess for the record; None when it cannot be read."""
    try:
        import flash_safety
        return flash_safety.assess(edl, index, request_text)
    except Exception:  # noqa: BLE001 — a reading failure never blocks
        return None


def _flash_findings(edl, index=None, request_text=None):
    a = flash_assessment(edl, index, request_text)
    if not a or not a["over_limit"] or a["opted_in"]:
        return []
    # Opt-in is read from THIS request only; the editor sees the whole
    # conversation. Flashing the user asked for in an earlier message, or
    # flashing that is the user's own existing edit, is acknowledged with
    # justify_verification_findings (naming that request), never thinned away.
    return [_finding(
        FLASH_FINDING, "motion_color",
        (f"Up to {a['peak_per_s']} full-frame flashes fall inside one second "
         f"(the flash-safe limit is {a['max_per_s']}) and this request does "
         "not ask for flashing: thin them if you added them unasked; if the "
         "user asked for it earlier or it is their own edit, keep it and "
         "justify this finding naming that request."),
        a, ("if you added them unasked, thin the flashes to at most "
            f"{a['max_per_s']} per second (flash_safety.plan); if the user "
            "asked for this flashing (in this or an earlier message) or it is "
            "their own edit, keep it and justify_verification_findings "
            "quoting that request — the export is then flagged "
            "contains_flashing"))]


def deterministic_findings(edl, index=None, request_text=None,
                           media_keys=None):
    return (_text_corruption_findings(edl)
            + _caption_findings(edl)
            + _sound_off_findings(edl, index or {})
            + _negation_findings(edl, index or {})
            + _motion_findings(edl)
            + _zoom_findings(edl, index or {})
            + _reframe_findings(edl, index or {})
            + _media_findings(edl)
            + _cleanup_findings(edl)
            + _audio_findings(edl)
            + _request_findings(edl, request_text, media_keys)
            + _flash_findings(edl, index or {}, request_text)
            + _earn_its_place_findings(edl, index or {}, request_text)
            + _repeat_findings(edl, index or {}))


def build_verification_record(project_id, version, manifest, edl, index,
                              preview=None, proof_ranges=None,
                              visual_findings=None, audio_findings=None,
                              story_findings=None, repairs=None,
                              justified=None, request_text=None,
                              advisory_findings=None, prior_records=None,
                              media_keys=None):
    """Version-stamped verification. ``visual/audio/story_findings`` are
    review lines that already passed their reviewer's blocking bar; taste
    notes and other craft observations go in ``advisory_findings`` and are
    recorded without ever making the version ``repair_required``.

    ``prior_records``: this request's records for earlier versions. A
    reviewer category they already asked the editor to repair is advisory
    here (request_scope.demote_recurring): one repair per category, so a
    reviewer that re-raises the same complaint after every repair cannot
    hold a finished edit open."""
    findings = deterministic_findings(edl, index, request_text=request_text,
                                      media_keys=media_keys)
    for department, rows in (("visual_review", visual_findings or []),
                             ("audio_review", audio_findings or []),
                             ("story_review", story_findings or [])):
        for row in rows:
            findings.append(_finding(
                "review_finding", department, str(row)[:1000],
                severity="error", repair="repair or explicitly justify from direct evidence"))
    for row in advisory_findings or []:
        findings.append(_finding(
            "taste_advisory", "craft", str(row)[:1000],
            severity="advisory", repair=ADVISORY_LABEL))
    findings = request_scope.demote_recurring(findings, prior_records, version)
    preview = preview or {}
    findings += device_findings(preview)
    complete_preview = (int(preview.get("edl_version") or -1) == int(version)
                        and not preview.get("scope") == "changes"
                        and bool(preview.get("storage_key")
                                 or preview.get("asset_id")
                                 or preview.get("duration_s") is not None))
    if not complete_preview:
        findings.append(_finding(
            "complete_preview_missing", "render",
            "Latest EDL version has no complete Studio preview evidence.",
            {"preview": {key: preview.get(key) for key in
                         ("edl_version", "scope", "storage_key", "asset_id")}},
            "render one complete preview and review it"))
    if edl.get("captions") and not (
            preview.get("caption_pages") or preview.get("caption_sheet_key")):
        findings.append(_finding(
            "caption_render_evidence_missing", "captions",
            "Captions have no rendered risk-state review pages.", {},
            "render caption QA across distinct layouts/backgrounds and inspect it"))
    for row in findings:
        row.setdefault(
            "finding_id",
            "vf_" + hashlib.sha256(_canon(row).encode("utf-8")).hexdigest()[:16],
        )
    # Independent critics can report the same concrete defect through more
    # than one evidence path.  Keep one durable finding per stable ID so the
    # agent repairs it once instead of spending continuation slices clearing
    # duplicate copies of the same issue.
    deduped = []
    seen_finding_ids = set()
    for row in findings:
        if row["finding_id"] in seen_finding_ids:
            continue
        seen_finding_ids.add(row["finding_id"])
        deduped.append(row)
    findings = deduped
    justified_codes = {str(row.get("code")) for row in (justified or [])
                       if isinstance(row, dict)}
    justified_ids = {str(row.get("finding_id")) for row in (justified or [])
                     if isinstance(row, dict) and row.get("finding_id")}
    unresolved = [row for row in findings
                  if is_blocking(row)
                  and row["code"] not in justified_codes
                  and row["finding_id"] not in justified_ids]
    justifications = list(justified or [])
    return {
        "verification_version": VERIFICATION_VERSION,
        "project_id": int(project_id), "edl_version": int(version),
        "manifest_id": (manifest or {}).get("manifest_id"),
        "status": ("justified" if not unresolved and justifications
                   else "passed" if not unresolved else "repair_required"),
        "deterministic_checks_passed": not any(
            row["department"] not in {"visual_review", "audio_review", "story_review"}
            for row in unresolved),
        "complete_preview_passed": complete_preview,
        "proof_ranges": list(proof_ranges or []),
        "render_evidence": {key: preview.get(key) for key in (
            "asset_id", "storage_key", "edl_version", "duration_s", "audio_qc",
            "picture_qc", "audio_model_review", "verify_frames", "sheet_key",
            "caption_sheet_key", "caption_pages", "devices")
            if preview.get(key) is not None},
        # The program flashes past the flash-safe limit. It reaches export
        # only through the user's opt-in (this request) or an acknowledged
        # flash_rate_exceeded finding, so it is flagged — the same reading
        # the backend export gate stamps on the final job.
        "contains_flashing": bool((flash_assessment(
            edl, index or {}, request_text) or {}).get("over_limit")),
        "findings": findings,
        "unresolved_findings": unresolved,
        "advisories": [row for row in findings if not is_blocking(row)],
        "repairs": list(repairs or []), "justifications": justifications,
    }


def justify_findings(record, finding_ids, justification, evidence_ids=None):
    """Return a new record with direct-evidence false positives resolved.

    This never mutates an EDL or erases a finding. The durable record keeps the
    original finding plus who/what justified it, so repairs remain preferable
    and reviewable.
    """
    record = record or {}
    # Database reads wrap the actual document with status/updated_at.
    if isinstance(record.get("record"), dict):
        record = record["record"]
    updated = json_record(record)
    requested = {str(value).strip() for value in (finding_ids or [])
                 if str(value).strip()}
    reason = str(justification or "").strip()
    if not requested:
        raise ValueError("finding_ids must name at least one unresolved finding")
    if len(reason) < 20:
        raise ValueError("justification must state concrete direct evidence")
    unresolved = list(updated.get("unresolved_findings") or [])
    matches = [row for row in unresolved
               if str(row.get("finding_id")) in requested
               or str(row.get("code")) in requested]
    if not matches:
        already = updated.get("justifications") or []
        if all(any((value == str(row.get("finding_id")) or value == str(row.get("code")))
                   and row.get("justification") == reason[:1200]
                   for row in already) for value in requested):
            return updated
        raise ValueError("none of those findings are unresolved on this EDL version")
    required_repairs = sorted({row.get("code") for row in matches
                               if row.get("code") in NON_JUSTIFIABLE_FINDINGS})
    if required_repairs:
        raise ValueError("these deterministic findings require repair: "
                         + ", ".join(required_repairs))
    existing = list(updated.get("justifications") or [])
    for row in matches:
        existing.append({
            "finding_id": row.get("finding_id"),
            "code": row.get("code"),
            "justification": reason[:1200],
            "evidence_ids": [str(value) for value in (evidence_ids or [])][:40],
        })
    matched_ids = {row.get("finding_id") for row in matches}
    updated["unresolved_findings"] = [
        row for row in unresolved if row.get("finding_id") not in matched_ids
    ]
    updated["justifications"] = existing
    if not updated["unresolved_findings"]:
        updated["status"] = "justified"
        updated["deterministic_checks_passed"] = True
    else:
        updated["status"] = "repair_required"
    return updated
