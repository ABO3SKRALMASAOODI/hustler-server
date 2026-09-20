from copy import deepcopy
from types import SimpleNamespace

import agent_tools
import quality_verifier
from schemas import default_edl


def _codes(edl, index=None):
    return {row["code"] for row in
            quality_verifier.deterministic_findings(edl, index or {})}


def test_manifest_names_departments_ranges_and_required_evidence():
    before = default_edl(20)
    after = deepcopy(before)
    after["sfx"] = [{"id": "sx1", "storage_key": "audio/click.wav",
                     "at": 4.0, "gain_db": -6, "purpose": "button click"}]
    manifest = quality_verifier.build_change_manifest(
        7, 2, before, after,
        {"out_ranges": [[3.8, 4.3]], "source_ranges": []},
        "added click", "add_sfx")
    assert manifest["edl_version"] == 2
    assert "sfx" in manifest["departments_changed"]
    assert manifest["output_ranges"] == [[3.8, 4.3]]
    assert "actual_audio_opening_speech_peaks_transitions_ending" in \
        manifest["required_verification_evidence"]


def test_unmotivated_repetitive_sfx_is_publish_blocking():
    edl = default_edl(20)
    edl["sfx"] = [
        {"id": f"sx{i}", "storage_key": "audio/whoosh.wav", "at": at,
         "gain_db": -6, "purpose": None}
        for i, at in enumerate((2, 5, 8), 1)]
    codes = _codes(edl)
    assert "sfx_missing_trigger" in codes
    assert "mechanical_sfx_pattern" in codes


def test_repetitive_unmeasured_zooms_are_detected():
    edl = default_edl(20)
    edl["effects"] = {"zooms": [
        {"id": f"zm{i}", "start": at, "end": at + 1,
         "strength": .2, "mode": "ease", "target_measured": False}
        for i, at in enumerate((2, 5, 8), 1)]}
    codes = _codes(edl)
    assert "zoom_missing_purpose" in codes
    assert "zoom_unmeasured_target" in codes
    assert "mechanical_zoom_pattern" in codes


def test_scene_change_requires_scene_aware_reframe():
    edl = default_edl(20)
    edl["frame"] = {"ratio": "9:16", "mode": "crop",
                    "focus_x": .5, "focus_y": .5}
    index = {"shots": [{"id": 1, "start": 0, "end": 10},
                       {"id": 2, "start": 10, "end": 20}]}
    assert "scene_unaware_reframe" in _codes(edl, index)
    edl["frame"]["focus_track"] = [
        {"t0": 0, "t1": 10, "x": .3, "y": .5},
        {"t0": 10, "t1": 20, "x": .8, "y": .5}]
    assert "scene_unaware_reframe" not in _codes(edl, index)


def test_duplicate_broll_window_is_not_rationalized():
    edl = default_edl(20)
    edl["inserts"] = [
        {"id": "in1", "asset_key": "stock/a.mp4", "kind": "video",
         "at_output_s": 2, "duration_s": 2, "source_start_s": 0},
        {"id": "in2", "asset_key": "stock/a.mp4", "kind": "video",
         "at_output_s": 8, "duration_s": 2, "source_start_s": 0},
    ]
    assert "duplicate_broll_window" in _codes(edl)


def test_large_frame_reconstruction_requires_direct_review():
    edl = default_edl(20)
    edl["patches"] = [{
        "id": "pa1", "asset_key": "patches/p.mp4", "src_start": 0,
        "src_end": 20, "regions": [{"id": "er1", "x": .3, "y": .25,
                                      "w": .4, "h": .35, "start": 0,
                                      "end": 20, "fill": "box"}],
    }]
    assert "destructive_cleanup_region" in _codes(edl)


def test_manual_caption_entirely_in_a_cut_is_not_allowed_to_look_verified():
    edl = default_edl(20)
    edl["keep"] = [[12, 20]]
    edl["captions"] = [{"text": "HOOK", "start": 0, "end": 2}]

    findings = quality_verifier.deterministic_findings(edl)

    invisible = [row for row in findings
                 if row["code"] == "invisible_manual_caption"]
    assert len(invisible) == 1
    assert invisible[0]["evidence"]["kept_source_ranges"] == [[12, 20]]


def test_music_after_ending_and_missing_treatment_are_detected():
    edl = default_edl(10)
    edl["music"] = [{"id": "mus1", "storage_key": "music/a.mp3",
                     "start": 10, "end": 11, "gain_db": -18,
                     "duck": True, "purpose": None}]
    codes = _codes(edl)
    assert "music_starts_after_program" in codes
    assert "music_missing_treatment_purpose" in codes


def test_verification_record_cannot_pass_without_complete_preview():
    edl = default_edl(10)
    manifest = quality_verifier.build_change_manifest(
        1, 2, edl, edl, {}, "verify", None)
    pending = quality_verifier.build_verification_record(
        1, 2, manifest, edl, {}, preview={})
    assert pending["status"] == "repair_required"
    assert "complete_preview_missing" in {
        row["code"] for row in pending["unresolved_findings"]}
    passed = quality_verifier.build_verification_record(
        1, 2, manifest, edl, {},
        preview={"edl_version": 2, "duration_s": 10, "storage_key": "p.mp4"})
    assert passed["status"] == "passed"


def test_explicit_duration_request_blocks_wrong_length_program():
    edl = default_edl(42.67)
    target = quality_verifier.requested_duration_target(
        "And edit as you like and make it of like 18 sec video")
    assert target["min_s"] < 18 < target["max_s"]
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, {},
        preview={"edl_version": 2, "duration_s": 42.67},
        request_text="And edit as you like and make it of like 18 sec video")
    assert "requested_duration_outside_target" in {
        row["code"] for row in record["unresolved_findings"]}

    repaired = default_edl(18.2)
    record = quality_verifier.build_verification_record(
        1, 3, {}, repaired, {},
        preview={"edl_version": 3, "duration_s": 18.2},
        request_text="And edit as you like and make it of like 18 sec video")
    assert "requested_duration_outside_target" not in {
        row["code"] for row in record["unresolved_findings"]}


def test_effect_timestamp_is_not_mistaken_for_program_duration():
    assert quality_verifier.requested_duration_target(
        "Add a zoom at 18 seconds and fade it out") is None


def test_explicit_transition_request_cannot_pass_with_no_transition():
    edl = default_edl(29)
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, {},
        preview={"edl_version": 2, "duration_s": 29},
        request_text="Add subtle cinematic transitions between the clips")
    assert "requested_transitions_missing" in {
        row["code"] for row in record["unresolved_findings"]}

    edl["effects"] = {"transition": {
        "style": "dip_black", "duration_s": .2, "scope": "scene",
        "junctions": [1, 4]}}
    repaired = quality_verifier.build_verification_record(
        1, 3, {}, edl, {},
        preview={"edl_version": 3, "duration_s": 29},
        request_text="Add subtle cinematic transitions between the clips")
    assert "requested_transitions_missing" not in {
        row["code"] for row in repaired["unresolved_findings"]}


def test_explicit_no_transitions_is_not_misread_as_a_missing_request():
    edl = default_edl(10)
    assert "requested_transitions_missing" not in {
        row["code"] for row in quality_verifier.deterministic_findings(
            edl, request_text="Do not add transitions; use hard cuts")}


def test_avoid_excessive_transitions_still_requires_a_restrained_treatment():
    edl = default_edl(10)
    assert "requested_transitions_missing" in {
        row["code"] for row in quality_verifier.deterministic_findings(
            edl, request_text="Add transitions, but avoid excessive transitions")}


def test_transition_avoidance_lists_do_not_invent_positive_requirements():
    for request in (
            "Use hard cuts, movement-based cuts, and occasional match cuts.\n"
            "Avoid:\n- cheesy transitions\n- glitch effects\n- excessive zoom effects",
            "Avoid cheesy transitions.",
            "Transitions should not be flashy.",
            "No stock footage. No cheesy transitions. Keep it editorial."):
        assert "requested_transitions_missing" not in {
            row["code"] for row in quality_verifier.deterministic_findings(
                default_edl(32), request_text=request)}


def test_transition_request_respects_later_blanket_refusal():
    for request in ("Add smooth transitions. Actually use no transitions.",
                    "Use transitions. Do not add any transitions after all."):
        assert not quality_verifier._requested_transitions(request)
    assert quality_verifier._requested_transitions(
        "No transitions. Actually add subtle transitions between shots.")
    assert quality_verifier._requested_transitions(
        "Add subtle transitions. No cheesy transitions.")


def test_duplicate_critic_findings_are_one_repair_record():
    edl = default_edl(10)
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, {},
        preview={"edl_version": 2, "duration_s": 10},
        visual_findings=["caption overlaps the face",
                         "caption overlaps the face"])
    rows = [row for row in record["unresolved_findings"]
            if row["department"] == "visual_review"]
    assert len(rows) == 1


def test_corrupt_caption_glyph_blocks_completion():
    edl = default_edl(10)
    edl["captions"] = [{"text": "bad \ufffd caption", "start": 0, "end": 1}]
    assert "corrupt_glyph" in _codes(edl)


def test_direct_evidence_can_justify_subjective_finding_but_not_missing_proof():
    edl = default_edl(10)
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, {}, preview={"edl_version": 2, "duration_s": 10},
        visual_findings=["crop appears empty in the critic sample"])
    review = next(row for row in record["unresolved_findings"]
                  if row["department"] == "visual_review")
    justified = quality_verifier.justify_findings(
        record, [review["finding_id"]],
        "Direct frame ve_123 shows the intended subject centered throughout.",
        ["ve_123"])
    assert justified["status"] == "justified"
    assert justified["unresolved_findings"] == []
    assert justified["justifications"][0]["evidence_ids"] == ["ve_123"]

    missing = quality_verifier.build_verification_record(
        1, 2, {}, edl, {}, preview={})
    proof = next(row for row in missing["unresolved_findings"]
                 if row["code"] == "complete_preview_missing")
    try:
        quality_verifier.justify_findings(
            missing, [proof["finding_id"]],
            "The render should not be needed because the edit is simple.")
        assert False, "a required complete preview cannot be justified away"
    except ValueError as exc:
        assert "require repair" in str(exc)


def test_failed_justification_write_does_not_mutate_live_verification_state():
    edl = default_edl(10)
    record = quality_verifier.build_verification_record(
        1, 2, {}, edl, {}, preview={"edl_version": 2, "duration_s": 10},
        visual_findings=["crop appears empty in the critic sample"])
    finding = record["unresolved_findings"][0]

    class FailingDb:
        @staticmethod
        def run(*_args):
            raise RuntimeError("database unavailable")

    ctx = SimpleNamespace(
        latest_edl=lambda: {"version": 2}, verification_records={2: record},
        db=FailingDb(), project_id=1, last_taste=[], editing_metrics={})
    result = agent_tools.justify_verification_findings(
        ctx, [finding["finding_id"]],
        "Direct frame ve_123 shows the intended subject centered.",
        ["ve_123"])
    assert result.startswith("TRANSIENT FAILURE")
    assert ctx.verification_records[2] is record
    assert ctx.verification_records[2]["status"] == "repair_required"


def test_program_length_ignores_later_scene_and_reveal_ranges():
    prompt = ('Create a 30–40 second vertical 9:16 Instagram Story/Reel ad for JAPAN APPAREL. '
              'Keep shots 3–5 seconds. By around 8–10 seconds, the viewer '
              'should clearly understand this is an ad for a premium T-shirt.')
    target = quality_verifier.requested_duration_target(prompt)
    assert (target['min_s'], target['max_s']) == (30, 40)


def test_ambiguous_ranges_do_not_become_whole_program_constraints():
    for text in ['By 8–10 seconds show the T-shirt.',
                 'Hold each shot 3–5 seconds.', 'Add text at 12–15 seconds.',
                 'Make the title last 2 seconds.',
                 'By around 8–10 seconds the ad should be clear.']:
        assert quality_verifier.requested_duration_target(text) is None


def test_latest_explicit_whole_program_target_wins():
    target = quality_verifier.requested_duration_target(
        'Make a 30–40 second ad. Actually make it 25 seconds. '
        'Show branding at 8–10 seconds.')
    assert target['target_s'] == 25


def test_negated_duration_in_repair_direction_never_replaces_customer_target():
    for prohibition in (
            "Do not reset the EDL, rebuild the montage, or shorten it to 10 seconds.",
            "Don't make it 10 seconds.",
            "Never cut it to 10 seconds.",
            "Avoid a 10 second video.",
            "No 10 second video."):
        target = quality_verifier.requested_duration_target(
            "Create a 30–40 second vertical 9:16 Instagram Story/Reel ad. " + prohibition)
        assert (target['min_s'], target['max_s']) == (30, 40)
        assert quality_verifier.requested_duration_target(prohibition) is None
