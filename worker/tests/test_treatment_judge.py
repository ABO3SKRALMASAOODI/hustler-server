"""Ambiguous first treatments get evidence-bound independent judgment."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import treatment_judge  # noqa: E402


def _plan_args():
    return {
        "steps": ["build the story", "finish picture and sound"],
        "treatment": "proof before promise",
        "editorial_family": "commercial_brand",
        "decision_basis": [
            "the observed product proof is more credible than generic lifestyle imagery",
        ],
        "alternatives_rejected": [
            "a generic fast montage does not distinguish the actual product",
        ],
        "coherence_rules": [
            "picture, type and sound become denser only when proof becomes concrete",
        ],
        "sequence_map": [{
            "role": "hook", "anchor": "the product problem",
            "purpose": "create a specific unresolved question",
            "visual": "hold on the actual problem before showing proof",
            "sound": "dry opening, then introduce one restrained pulse",
            "energy": .45,
        }, {
            "role": "proof", "anchor": "the observed result",
            "purpose": "resolve the promise with visible evidence",
            "visual": "give the real product result the hero frame",
            "sound": "let the pulse resolve without a decorative impact stack",
            "energy": .75,
        }],
    }


def _ctx(message="make this an incredible Instagram reel"):
    # A no-main canvas deliberately keeps the pre-plan format cast uncertain;
    # the candidate itself may still choose an evidence-backed family.
    ctx = agent_tools.ToolContext(
        object(), {"id": 3}, {"id": 9, "chat_session_id": 2}, None, "/tmp")
    ctx.user_message = message
    return ctx


def test_parse_report_requires_exact_labeled_evidence():
    accepted = treatment_judge.parse_report(
        '{"verdict":"accept","confidence":0.91,'
        '"evidence_refs":["PLAN","B1"],'
        '"reason":"the proof route is specific","revision":""}',
        {"PLAN", "USER", "B1"})
    assert accepted == {
        "treatment_judge_v": 1,
        "verdict": "accept", "confidence": .91,
        "evidence_refs": ["PLAN", "B1"],
        "reason": "the proof route is specific", "revision": "",
    }
    assert treatment_judge.parse_report(
        '{"verdict":"accept","confidence":0.91,'
        '"evidence_refs":["PLAN","PIXELS_I_SAW"],'
        '"reason":"invented evidence","revision":""}',
        {"PLAN", "USER", "B1"}) is None
    assert treatment_judge.parse_report(
        '{"verdict":"revise","confidence":0.95,'
        '"evidence_refs":[],"reason":"unsupported",'
        '"revision":"change it"}', {"PLAN"}) is None


def test_only_high_confidence_grounded_revision_is_actionable():
    base = {"verdict": "revise", "confidence": .85,
            "evidence_refs": ["B1"], "revision": "distinguish the proof"}
    assert not treatment_judge.actionable_revision(base)
    base["confidence"] = .86
    assert treatment_judge.actionable_revision(base)
    base["evidence_refs"] = []
    assert not treatment_judge.actionable_revision(base)


def test_review_is_bounded_to_packet_and_degrades_on_outage(monkeypatch):
    seen = {}

    def ask(system, user, **kwargs):
        seen.update(system=system, user=user, kwargs=kwargs)
        return {"text": '{"verdict":"accept","confidence":0.88,'
                        '"evidence_refs":["PLAN","B1"],'
                        '"reason":"one coherent proof system",'
                        '"revision":""}'}

    monkeypatch.setattr(treatment_judge.llm, "ask_text", ask)
    report = treatment_judge.review(
        _plan_args(), "make it great", "brand family contract")
    assert report["verdict"] == "accept"
    assert seen["kwargs"]["purpose"] == "independent_treatment_judge"
    assert seen["kwargs"]["max_tokens"] == 500
    assert "must not pretend to see pixels or hear sound" in seen["system"]
    assert "Do not reward feature count" in seen["system"]
    assert '"ref":"B1"' in seen["user"]

    monkeypatch.setattr(
        treatment_judge.llm, "ask_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert treatment_judge.review(
        _plan_args(), "make it great", "contract") is None


def test_ambiguous_whole_plan_blocks_high_confidence_wrong_route(monkeypatch):
    ctx = _ctx()
    calls = []

    def revise(*_args):
        calls.append(1)
        return {"verdict": "revise", "confidence": .94,
                "evidence_refs": ["PLAN", "A1"],
                "reason": "the rejected route is not materially distinguished",
                "revision": "make actual proof the causal spine"}

    monkeypatch.setattr(agent_tools.treatment_judge, "review", revise)
    first = agent_tools.set_edit_plan(ctx, **_plan_args())
    assert first.startswith("REJECTED: independent treatment review")
    assert "No blueprint was recorded and no EDL was changed" in first
    assert ctx.edit_plan is None
    assert ctx.editing_metrics["treatment_judge_reviews"] == 1
    assert ctx.editing_metrics["treatment_judge_revisions"] == 1

    # The exact rejected candidate is not paid for twice. A changed candidate
    # gets a new fingerprint and therefore a fresh independent opinion.
    second = agent_tools.set_edit_plan(ctx, **_plan_args())
    assert second.startswith("REJECTED: independent treatment review")
    assert calls == [1]
    assert ctx.editing_metrics["treatment_judge_reviews_reused"] == 1


def test_accepted_ambiguous_treatment_is_bound_to_recorded_blueprint(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(
        agent_tools.treatment_judge, "review",
        lambda *_args: {
            "verdict": "accept", "confidence": .9,
            "evidence_refs": ["PLAN", "B1", "S2"],
            "reason": "the proof beat resolves the opening promise",
            "revision": "",
        })
    result = agent_tools.set_edit_plan(ctx, **_plan_args())
    assert result.startswith("Plan recorded as a creative blueprint")
    assert "INDEPENDENT TREATMENT REVIEW: ACCEPT" in result
    assert ctx.edit_plan["treatment"] == "proof before promise"
    assert ctx.edit_plan["sequence_map"][1]["role"] == "proof"
    assert ctx.edit_plan["department_plan"]
    assert ctx.editing_metrics["treatment_judge_accepts"] == 1
    trace = ctx.editing_metrics["editorial_decisions"][0]
    assert trace["kind"] == "treatment_review"
    assert trace["decision"] == "accept"
    assert trace["evidence"] == ["PLAN", "B1", "S2"]


def test_precise_format_and_mcp_callers_skip_extra_judge(monkeypatch):
    def unexpected(*_args):
        raise AssertionError("an unambiguous or external caller must not pay")

    monkeypatch.setattr(agent_tools.treatment_judge, "review", unexpected)

    explicit = _ctx("cut this podcast conversation into one coherent exchange")
    args = _plan_args()
    args["editorial_family"] = "podcast_conversation"
    assert agent_tools.set_edit_plan(explicit, **args).startswith(
        "Plan recorded as a creative blueprint")
    assert "treatment_judge_reviews" not in explicit.editing_metrics

    external = _ctx()
    external.sight_out = True
    assert agent_tools.set_edit_plan(external, **_plan_args()).startswith(
        "Plan recorded as a creative blueprint")
    assert "treatment_judge_reviews" not in external.editing_metrics


def test_known_source_grammar_does_not_mistake_vague_treatment_for_direction(
        monkeypatch):
    ctx = _ctx("make this beautiful and compelling")
    ctx.has_main_video = True
    ctx.index = {"video": {"duration": 20.0, "width": 1920,
                           "height": 1080, "has_audio": True}}
    ctx.duration = 20.0
    calls = []
    monkeypatch.setattr(
        agent_tools.grammar, "classify",
        lambda _index: ("podcast-conversation", {"confidence": .8}))

    def accept(*_args):
        calls.append(1)
        return {"verdict": "accept", "confidence": .88,
                "evidence_refs": ["PLAN", "B1"],
                "reason": "the candidate distinguishes a concrete proof arc",
                "revision": ""}

    monkeypatch.setattr(agent_tools.treatment_judge, "review", accept)
    result = agent_tools.set_edit_plan(ctx, **_plan_args())
    assert result.startswith("Plan recorded as a creative blueprint")
    assert calls == [1]
