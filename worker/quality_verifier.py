"""Durable change manifests and deterministic edit verification.

The model may choose the treatment, but it may not choose whether the result
is checked.  Every immutable EDL write gets a manifest; every complete preview
gets a version-stamped record.  Rules here identify concrete corruption or a
missing evidence contract, not subjective effect quotas.
"""

from __future__ import annotations

import hashlib
import json
import math

from schemas import program_duration
from timeline import Timeline


MANIFEST_VERSION = 1
VERIFICATION_VERSION = 1
NON_JUSTIFIABLE_FINDINGS = {
    "complete_preview_missing", "caption_render_evidence_missing",
    "corrupt_glyph", "music_starts_after_program", "invalid_music_span",
}


_DEPARTMENTS = {
    "keep": "story", "speed": "story", "inserts": "broll",
    "overlays": "broll", "captions": "captions", "caption_mutes": "captions",
    "texts": "graphics", "vectors": "graphics", "music": "music",
    "sfx": "sfx", "voiceover": "audio", "volume": "audio",
    "frame": "reframe", "effects": "motion_color", "source_clean": "cleanup",
    "patches": "cleanup",
}


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str, ensure_ascii=False)


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
             severity="error"):
    return {"code": code, "department": department, "severity": severity,
            "message": message, "evidence": evidence or {},
            "repair": repair}


def _text_corruption_findings(edl):
    findings = []
    texts = [row.get("text") for row in edl.get("texts") or []]
    captions = edl.get("captions")
    if isinstance(captions, list):
        texts += [row.get("text") for row in captions]
    elif isinstance(captions, dict):
        texts += [value for pair in captions.get("text_fixes") or []
                  for value in pair]
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
    zooms = list(_effects(edl).get("zooms") or [])
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
    track = list(frame.get("focus_track") or [])
    if len(shots) <= 1:
        return []
    uncovered = []
    for shot in shots:
        mid = (float(shot.get("start", 0)) + float(shot.get("end", 0))) / 2
        if not any(float(row.get("t0", 0)) <= mid <= float(row.get("t1", 0))
                   for row in track):
            uncovered.append(shot.get("id"))
    if uncovered:
        return [_finding(
            "scene_unaware_reframe", "reframe",
            f"Global crop lacks shot-specific focus evidence for {len(uncovered)} scene(s).",
            {"shot_ids": uncovered[:40]},
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


def deterministic_findings(edl, index=None):
    return (_text_corruption_findings(edl)
            + _zoom_findings(edl, index or {})
            + _reframe_findings(edl, index or {})
            + _media_findings(edl)
            + _audio_findings(edl))


def build_verification_record(project_id, version, manifest, edl, index,
                              preview=None, proof_ranges=None,
                              visual_findings=None, audio_findings=None,
                              story_findings=None, repairs=None,
                              justified=None):
    findings = deterministic_findings(edl, index)
    for department, rows in (("visual_review", visual_findings or []),
                             ("audio_review", audio_findings or []),
                             ("story_review", story_findings or [])):
        for row in rows:
            findings.append(_finding(
                "review_finding", department, str(row)[:1000],
                severity="error", repair="repair or explicitly justify from direct evidence"))
    preview = preview or {}
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
    justified_codes = {str(row.get("code")) for row in (justified or [])
                       if isinstance(row, dict)}
    justified_ids = {str(row.get("finding_id")) for row in (justified or [])
                     if isinstance(row, dict) and row.get("finding_id")}
    unresolved = [row for row in findings
                  if row["code"] not in justified_codes
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
            "verify_frames", "sheet_key", "caption_sheet_key", "caption_pages")
            if preview.get(key) is not None},
        "findings": findings,
        "unresolved_findings": unresolved,
        "repairs": list(repairs or []), "justifications": justifications,
    }


def justify_findings(record, finding_ids, justification, evidence_ids=None):
    """Return a new record with direct-evidence false positives resolved.

    This never mutates an EDL or erases a finding. The durable record keeps the
    original finding plus who/what justified it, so repairs remain preferable
    and reviewable.
    """
    updated = json.loads(json.dumps(record or {}))
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
