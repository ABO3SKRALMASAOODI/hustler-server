"""Motion motifs are executable beat contracts, not style adjectives."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import director  # noqa: E402
import motion_contract  # noqa: E402


def _language(domains=("camera", "type")):
    return {
        "principle": "the frame converges only when evidence becomes clear",
        "density": .4,
        "intensity": .55,
        "contrast": .8,
        "stillness_rule": "setup beats settle so proof has contrast",
        "motifs": [{
            "id": "proof_lock",
            "behavior": "elements ease toward the proof and resolve together",
            "trigger": "a concrete result becomes legible",
            "domains": list(domains),
        }],
    }


def _plan(beats, domains=("camera", "type"), mode="author"):
    return director.create_blueprint(
        steps=["execute the motion treatment"],
        motion_language=_language(domains),
        sequence_map=beats,
        department_plan={
            "motion": {"mode": mode,
                       "purpose": "make proof feel causally earned"},
        })


def _beat(start=None, end=None, motif="proof_lock", **extra):
    row = {
        "role": extra.pop("role", "proof"),
        "purpose": extra.pop("purpose", "land the evidence"),
        "motion_motif": motif,
        **extra,
    }
    if start is not None:
        row.update(source_start_s=start, source_end_s=end)
    return row


def test_main_source_motif_and_hold_map_to_output_evidence():
    plan = _plan([
        _beat(0, 2, "hold", role="setup"),
        _beat(4, 6),
    ])
    edl = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "z1", "start": 4.2, "end": 5.8,
                                 "mode": "ease", "strength": .15,
                                 "motion_motif": "proof_lock"}]},
    }
    report = motion_contract.evaluate(plan, edl)

    assert report["active"] is True
    assert report["mapped_beats"] == 2
    assert report["fulfilled_beats"] == 2
    assert report["gaps"] == []
    assert report["beats"][0]["status"] == "fulfilled"
    assert report["beats"][1]["events"][0]["domain"] == "camera"


def test_motif_requires_an_event_in_its_declared_domain():
    plan = _plan([_beat(4, 6)], domains=("type",))
    edl = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "z1", "start": 4.2, "end": 5.8,
                                 "motion_motif": "proof_lock"}]},
    }
    report = motion_contract.evaluate(plan, edl)

    assert report["beats"][0]["status"] == "missing"
    assert report["beats"][0]["events"] == []
    assert report["gaps"][0]["kind"] == "missing_motif"
    assert "in type" in report["gaps"][0]["message"]


def test_hold_rejects_explicit_motion_but_not_a_neighboring_event():
    plan = _plan([_beat(1, 3, "hold", role="setup")])
    conflict = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "z1", "start": 1.2, "end": 2.4}]},
    }
    report = motion_contract.evaluate(plan, conflict)
    assert report["beats"][0]["status"] == "contradicted"
    assert report["gaps"][0]["kind"] == "hold_contradiction"

    neighbor = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "z2", "start": 3.01, "end": 4.0}]},
    }
    assert motion_contract.execution_gaps(plan, neighbor) == []


def test_auxiliary_insert_uses_asset_local_clock_and_playback_rate():
    asset = "projects/7/assets/proof.mp4"
    plan = _plan([_beat(11, 13, source_asset_key=asset)], domains=("media",))
    edl = {
        "keep": [[0, 5], [5, 10]],
        "inserts": [{
            "id": "ins1", "asset_key": asset, "kind": "video",
            "at_output_s": 5, "duration_s": 2, "source_start_s": 10,
            "rate": 2, "motion": "zoom_in",
            "motion_motif": "proof_lock",
        }],
        "effects": {},
    }
    report = motion_contract.evaluate(plan, edl)

    assert report["beats"][0]["output_spans"] == [[5.5, 6.5]]
    assert report["beats"][0]["status"] == "fulfilled"
    assert report["beats"][0]["events"][0]["kind"] == "insert_motion"


def test_auxiliary_overlay_maps_its_clip_clock_and_keyframes():
    asset = "projects/7/assets/screen.mp4"
    plan = _plan([_beat(21, 23, source_asset_key=asset)], domains=("media",))
    edl = {
        "keep": [[0, 10]],
        "overlays": [{
            "id": "ov1", "asset_key": asset, "kind": "video",
            "start": 3, "duration_s": 4, "source_start_s": 20,
            "x": [{"t": 0, "v": .3}, {"t": 4, "v": .7}],
            "y": .5, "scale": .5, "motion_motif": "proof_lock",
        }],
        "effects": {},
    }
    report = motion_contract.evaluate(plan, edl)

    assert report["beats"][0]["output_spans"] == [[4.0, 6.0]]
    assert report["beats"][0]["status"] == "fulfilled"
    assert report["beats"][0]["events"][0]["kind"] == \
        "overlay_keyframes"


def test_untimed_beat_is_honestly_not_judged_not_failed():
    plan = _plan([_beat()])
    report = motion_contract.evaluate(plan, {"keep": [[0, 10]],
                                              "effects": {}})

    assert report["mapped_beats"] == 0
    assert report["beats"][0]["status"] == "not_judged"
    assert report["gaps"] == []


def test_constant_keyframes_do_not_falsely_prove_motion():
    plan = _plan([_beat(2, 4)], domains=("type",))
    edl = {
        "keep": [[0, 10]],
        "texts": [{
            "id": "t1", "text": "PROOF", "start": 2, "end": 4,
            "template": "title",
            "motion": {"scale": [{"t": 0, "v": 1},
                                  {"t": 2, "v": 1}]},
        }],
        "effects": {},
    }
    assert motion_contract.execution_gaps(plan, edl)[0]["kind"] == \
        "missing_motif"


def test_exact_compiled_caption_windows_can_fulfill_type_motion():
    plan = _plan([_beat(2, 4)], domains=("type",))
    edl = {
        "keep": [[0, 10]],
        "captions": {"mode": "from_transcript", "design_version": 2,
                     "style": {"preset": "karaoke"},
                     "motion_motif": "proof_lock"},
        "effects": {},
    }
    index = {"words": [
        {"w": "measured", "t0": 2.1, "t1": 2.7},
        {"w": "proof", "t0": 2.8, "t1": 3.5},
    ]}
    report = motion_contract.evaluate(plan, edl, index=index)

    assert report["beats"][0]["status"] == "fulfilled"
    assert report["beats"][0]["events"][0]["kind"] == "caption_motion"
    assert report["beats"][0]["events"][0]["detail"] == "continuous"


def test_static_captions_do_not_falsely_break_a_hold():
    plan = _plan([_beat(2, 4, "hold")])
    index = {"words": [
        {"w": "stay", "t0": 2.1, "t1": 2.6},
        {"w": "still", "t0": 2.7, "t1": 3.4},
    ]}
    static = {
        "keep": [[0, 10]],
        "captions": {"mode": "from_transcript",
                     "style": {"preset": "classic", "animation": "none"}},
        "effects": {},
    }
    assert motion_contract.execution_gaps(plan, static, index=index) == []

    animated = {
        **static,
        "captions": {"mode": "from_transcript",
                     "style": {"preset": "karaoke"}},
    }
    gaps = motion_contract.execution_gaps(plan, animated, index=index)
    assert gaps[0]["kind"] == "hold_contradiction"
    assert "caption_motion" in gaps[0]["message"]


def test_evidence_block_distinguishes_structure_from_visual_taste():
    plan = _plan([_beat(4, 6)])
    edl = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "z1", "start": 4, "end": 6,
                                 "motion_motif": "proof_lock"}]},
    }
    block = motion_contract.evidence_block(plan, edl)

    assert block.startswith("MOTION EXECUTION EVIDENCE:")
    assert '"status":"fulfilled"' in block
    assert '"behavior":"elements ease toward the proof' in block
    assert '"trigger":"a concrete result becomes legible"' in block
    assert "proves execution presence only" in block


def test_preserve_mode_never_creates_motion_execution_quota():
    plan = _plan([_beat(4, 6)], mode="preserve")
    report = motion_contract.evaluate(plan, {"keep": [[0, 10]],
                                              "effects": {}})
    assert report["active"] is False
    assert report["gaps"] == []


def test_unbound_overlap_cannot_accidentally_fulfill_a_motif():
    plan = _plan([_beat(4, 6)], domains=("camera",))
    edl = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{"id": "nearby", "start": 4, "end": 6}]},
    }

    report = motion_contract.evaluate(plan, edl)

    assert report["beats"][0]["status"] == "missing"
    assert report["beats"][0]["events"] == []
    assert report["beats"][0]["unbound_or_other_motif_events"][0]["id"] == \
        "nearby"
    assert "bound to that motif" in report["gaps"][0]["message"]


def test_event_bound_to_a_different_declared_motif_cannot_fulfill():
    language = _language(("camera",))
    language["motifs"].append({
        "id": "support_drift",
        "behavior": "a restrained supporting move",
        "trigger": "context arrives before proof",
        "domains": ["camera"],
    })
    plan = director.create_blueprint(
        steps=["execute the motion treatment"],
        motion_language=language,
        sequence_map=[_beat(4, 6)],
        department_plan={
            "motion": {"mode": "author", "purpose": "make proof land"},
        })
    edl = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{
            "id": "wrong-cause", "start": 4, "end": 6,
            "motion_motif": "support_drift",
        }]},
    }

    beat = motion_contract.evaluate(plan, edl)["beats"][0]

    assert beat["status"] == "missing"
    assert beat["unbound_or_other_motif_events"][0]["motion_motif"] == \
        "support_drift"


def test_manual_caption_provenance_survives_exact_compilation_per_item():
    plan = _plan([_beat(2, 4)], domains=("type",))
    edl = {
        "keep": [[0, 10]],
        "captions": [{
            "text": "PROOF", "start": 2, "end": 4,
            "style": {"animation": "pop"},
            "motion_motif": "proof_lock",
        }, {
            "text": "STATIC", "start": 6, "end": 8,
            "style": {"animation": "none"},
            "motion_motif": "proof_lock",
        }],
        "effects": {},
    }

    report = motion_contract.evaluate(plan, edl, index={"words": []})

    assert report["beats"][0]["status"] == "fulfilled"
    caption_events = [row for row in report["events"]
                      if row["kind"] == "caption_motion"]
    assert len(caption_events) == 1
    assert caption_events[0]["motion_motif"] == "proof_lock"
