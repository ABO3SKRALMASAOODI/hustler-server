"""Motion provenance is authored explicitly without becoming an effect cap."""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import director  # noqa: E402
import motion_contract  # noqa: E402
from schemas import EDLValidationError, edl_signature, validate_edl  # noqa: E402


def _language():
    return {
        "principle": "movement converges only when evidence lands",
        "density": .4,
        "intensity": .5,
        "contrast": .8,
        "stillness_rule": "the setup remains still",
        "motifs": [{
            "id": "proof_push",
            "behavior": "ease the active layer toward the proof",
            "trigger": "a measurable claim resolves",
            "domains": ["camera", "type"],
        }],
    }


def _plan():
    return director.create_blueprint(
        steps=["author proof movement"],
        sequence_map=[{
            "role": "proof", "purpose": "land the result",
            "source_start_s": 2, "source_end_s": 5,
            "motion_motif": "proof_push",
        }],
        motion_language=_language(),
        department_plan={
            "motion": {"mode": "author", "purpose": "land the result"},
        })


class _Ctx:
    has_main_video = True

    def __init__(self, edl=None, plan=None, index=None):
        self._row = {"version": 1, "json": copy.deepcopy(edl or {
            "keep": [[0, 10]], "effects": {},
        })}
        self.edit_plan = plan
        self.index = index or {"words": []}
        self.writes = 0

    def latest_edl(self):
        return self._row

    def write_edl(self, edl, desc):
        self.writes += 1
        old = self._row["version"]
        self._row = {"version": old + 1, "json": copy.deepcopy(edl)}
        return f"EDL v{old} -> v{old + 1}: {desc}"

    @staticmethod
    def clamp(value):
        return round(min(max(float(value), 0.0), 10.0), 2)


def test_motion_authoring_tool_stores_only_a_declared_non_hold_motif():
    ctx = _Ctx(plan=_plan())

    result = agent_tools.add_zoom(
        ctx, 2.2, 4.8, mode="ease", motion_motif="PROOF_PUSH")

    assert result.startswith("EDL v1 -> v2")
    assert ctx.latest_edl()["json"]["effects"]["zooms"][0][
        "motion_motif"] == "proof_push"
    assert agent_tools.add_zoom(
        _Ctx(plan=_plan()), 2, 4, motion_motif="invented_move").startswith(
            "REJECTED: motion_motif 'invented_move' is not declared")
    assert agent_tools.add_zoom(
        _Ctx(plan=_plan()), 2, 4, motion_motif="hold").startswith(
            "REJECTED: 'hold'")


def test_generic_binder_closes_only_the_exact_existing_motion_object():
    ctx = _Ctx(edl={
        "keep": [[0, 10]],
        "effects": {"zooms": [{
            "id": "zm1", "start": 2.2, "end": 4.8,
            "strength": .15, "mode": "ease",
        }]},
    }, plan=_plan())
    assert motion_contract.execution_gaps(
        ctx.edit_plan, ctx.latest_edl()["json"])

    result = agent_tools.bind_motion_motif(
        ctx, "zoom", "proof_push", id="zm1")

    assert result.startswith("EDL v1 -> v2")
    assert motion_contract.execution_gaps(
        ctx.edit_plan, ctx.latest_edl()["json"]) == []
    cleared = agent_tools.bind_motion_motif(
        ctx, "zoom", "clear", id="zm1")
    assert cleared.startswith("EDL v2 -> v3")
    assert "motion_motif" not in ctx.latest_edl()["json"]["effects"][
        "zooms"][0]


def test_binder_rejects_static_objects_instead_of_minting_fake_evidence():
    ctx = _Ctx(edl={
        "keep": [[0, 10]], "effects": {},
        "texts": [{
            "id": "tx1", "text": "PROOF", "start": 2, "end": 5,
            "template": "title", "entrance": "none", "exit": "none",
        }],
    }, plan=_plan())

    result = agent_tools.bind_motion_motif(
        ctx, "text", "proof_push", id="tx1")

    assert result.startswith("REJECTED:")
    assert "does not currently produce renderer-visible timed motion" in result
    assert ctx.writes == 0


def test_caption_tool_binds_exact_animated_track_and_rejects_static_track():
    words = [{"w": "measured", "t0": 2.1, "t1": 2.8},
             {"w": "proof", "t0": 2.9, "t1": 3.6}]
    animated = _Ctx(plan=_plan(), index={"words": words})

    result = agent_tools.add_captions(
        animated, mode="from_transcript", style={"preset": "karaoke"},
        motion_motif="proof_push")

    assert result.startswith("EDL v1 -> v2")
    caps = animated.latest_edl()["json"]["captions"]
    assert caps["motion_motif"] == "proof_push"
    assert motion_contract.execution_gaps(
        animated.edit_plan, animated.latest_edl()["json"],
        index=animated.index) == []

    static = _Ctx(plan=_plan(), index={"words": words})
    rejected = agent_tools.add_captions(
        static, mode="from_transcript",
        style={"preset": "classic", "animation": "none"},
        motion_motif="proof_push")
    assert rejected.startswith("REJECTED: motion_motif can only bind")
    assert static.writes == 0


def test_manual_caption_binding_marks_only_cards_that_really_animate():
    ctx = _Ctx(plan=_plan())
    result = agent_tools.add_captions(
        ctx,
        items=[
            {"text": "MOVE", "start": 2, "end": 4,
             "style": {"animation": "pop"}},
            {"text": "HOLD", "start": 5, "end": 7,
             "style": {"animation": "none"}},
        ],
        motion_motif="proof_push")

    assert result.startswith("EDL v1 -> v2")
    items = ctx.latest_edl()["json"]["captions"]
    assert items[0]["motion_motif"] == "proof_push"
    assert "motion_motif" not in items[1]


def test_optional_provenance_is_legacy_signature_safe_and_never_a_quota():
    legacy = {
        "keep": [[0, 10]],
        "effects": {"zooms": [{
            "id": "zm1", "start": 2, "end": 4, "strength": .15,
        }]},
    }
    normalized = validate_edl(copy.deepcopy(legacy), 10).model_dump()
    explicit_none = copy.deepcopy(normalized)
    explicit_none["effects"]["zooms"][0]["motion_motif"] = None

    assert edl_signature(normalized) == edl_signature(explicit_none)
    report = motion_contract.evaluate(None, normalized)
    assert report["active"] is False
    assert report["gaps"] == []

    invalid = copy.deepcopy(legacy)
    invalid["effects"]["zooms"][0]["motion_motif"] = "hold"
    with pytest.raises(EDLValidationError):
        validate_edl(invalid, 10)
