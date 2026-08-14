"""Independent semantic review is scoped, grounded, and version-safe."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop
import agent_tools
import story_critic


def _index():
    sentences = [
        {"t0": 0, "t1": 7, "speaker": 0,
         "text": "What mistake nearly ended the company?"},
        {"t0": 8, "t1": 18, "speaker": 1,
         "text": "We launched before customers could finish checkout."},
        {"t0": 19, "t1": 31, "speaker": 1,
         "text": "That failure cost us our first major contract."},
        {"t0": 32, "t1": 44, "speaker": 1,
         "text": "Then we watched every customer session."},
        {"t0": 65, "t1": 76, "speaker": 0,
         "text": "So what did you change after seeing that?"},
        {"t0": 77, "t1": 90, "speaker": 1,
         "text": "We rebuilt checkout around the failed sessions."},
        {"t0": 91, "t1": 106, "speaker": 1,
         "text": "Completion doubled and the next client stayed."},
    ]
    words = []
    for sentence in sentences:
        tokens = sentence["text"].split()
        step = (sentence["t1"] - sentence["t0"]) / len(tokens)
        for i, token in enumerate(tokens):
            words.append({"w": token, "t0": sentence["t0"] + i * step,
                          "t1": sentence["t0"] + (i + .8) * step})
    return {"video": {"duration": 120}, "speakers": 2,
            "sentences": sentences, "words": words}


def test_story_review_is_only_for_real_speech_edit_decisions():
    index = _index()
    assert not story_critic.should_review(
        {"keep": [[0, 120]]}, index, "podcast_conversation")
    assert not story_critic.should_review(
        {"keep": [[8, 44], [65, 106]]}, index, "action_sports_gameplay")
    assert story_critic.should_review(
        {"keep": [[8, 44], [65, 106]]}, index, "podcast_conversation")
    # A single selected highlight from a long source is still a story edit.
    assert story_critic.should_review(
        {"keep": [[8, 44]]}, index, "talking_head_social")


def test_story_evidence_contains_program_order_and_cut_boundary_context():
    evidence = story_critic.build_evidence(
        {"keep": [[8, 44], [77, 106]]}, _index(),
        "podcast_conversation", "make a coherent standalone short",
        {"brief": "failure to recovery", "must_keep": ["resolution"],
         "treatment": "failure-to-proof conversation",
         "decision_basis": ["the question supplies required setup"],
         "coherence_rules": ["preserve question, failure and payoff"],
         "alternatives_rejected": ["cold answer loses necessary context"],
         "sequence_map": [
             {"role": "failure", "anchor": "launched before checkout",
              "purpose": "make the mistake concrete",
              "visual": "stay on the speaker",
              "sound": "voice only", "energy": .55,
              "source_start_s": 8, "source_end_s": 31},
             {"role": "resolution", "anchor": "completion doubled",
              "purpose": "resolve the lesson",
              "visual": "hold the reaction", "sound": "music resolves",
              "energy": .4},
         ]})

    assert "program 5.00s" in evidence["program_transcript"]
    assert "We launched before customers" in evidence["program_transcript"]
    assert "Completion doubled" in evidence["program_transcript"]
    boundary = evidence["source_context_around_every_keep_boundary"]
    assert "What mistake nearly ended" in boundary
    assert "CUT" in boundary
    assert "So what did you change" in boundary
    assert evidence["direction"]["brief"] == "failure to recovery"
    assert evidence["direction"]["treatment"] == \
        "failure-to-proof conversation"
    assert evidence["direction"]["decision_basis"] == [
        "the question supplies required setup"]
    assert evidence["direction"]["coherence_rules"] == [
        "preserve question, failure and payoff"]
    assert evidence["direction"]["sequence_map"][0]["role"] == "failure"
    assert evidence["direction"]["sequence_map"][1]["sound"] == \
        "music resolves"


def test_story_report_requires_grounded_high_confidence_major_finding():
    report = story_critic.parse_report(
        '{"verdict":"pass","summary":"answer lost its setup",'
        '"findings":[{"severity":"major",'
        '"category":"missing_context","program_s":0,'
        '"evidence":"program opens with Because, while source 0-7s asks '
        'the required question","repair":"extend first keep to 0s",'
        '"confidence":0.93}]}')
    assert report["verdict"] == "repair"
    assert "extend first keep" in story_critic.repair_lines(report)[0]

    weak = dict(report, findings=[dict(report["findings"][0],
                                       confidence=.55)])
    assert story_critic.repair_lines(weak) == []
    assert story_critic.parse_report("not json") is None


def test_story_review_uses_bounded_text_call(monkeypatch):
    seen = {}

    def ask(system, user, **kwargs):
        seen.update(system=system, user=user, kwargs=kwargs)
        return {"text": '{"verdict":"pass","summary":"complete arc",'
                        '"findings":[]}'}

    monkeypatch.setattr(story_critic.llm, "ask_text", ask)
    report = story_critic.review(
        {"keep": [[8, 44], [65, 106]]}, _index(),
        "podcast_conversation", "make one standalone short")

    assert report["verdict"] == "pass"
    assert report["story_critic_v"] == 1
    assert seen["kwargs"]["purpose"] == "independent_story_critic"
    assert seen["kwargs"]["max_tokens"] == 1100
    assert "assembled transcript" in seen["system"]


def test_story_repair_joins_existing_quality_pushback(monkeypatch):
    monkeypatch.setattr(agent_loop.config, "AGENT_TURN_TIMEOUT_S", 600)

    class Ctx:
        last_preview = {"edl_version": 9}
        last_visual_critic = {}
        last_audio_review = None
        last_story_review = {"edl_version": 9, "verdict": "repair",
                             "findings": [{
                                 "severity": "major",
                                 "category": "missing_context",
                                 "program_s": 0.0,
                                 "evidence": "the answer lost its question",
                                 "repair": "extend the first keep",
                                 "confidence": .94,
                             }]}

        def latest_edl(self):
            return {"version": 9}

    messages = []
    assert agent_loop._quality_repair_pushback(
        Ctx(), messages, agent_loop.time.monotonic(), set())
    assert "answer lost its question" in messages[0]["content"]


def test_story_review_attempt_is_not_multiplied_on_same_version(monkeypatch):
    calls = []
    monkeypatch.setattr(
        agent_tools.story_critic, "review",
        lambda *_a, **_k: (calls.append(1) or
                            {"verdict": "pass", "findings": []}))
    monkeypatch.setattr(agent_tools.story_critic, "should_review",
                        lambda *_a, **_k: True)

    class Ctx:
        sight_out = False
        index = _index()
        edit_plan = {"format": "podcast conversation"}
        has_main_video = True
        user_message = "make a coherent short"
        story_reviewed_versions = set()
        last_story_review = None
        editing_metrics = {}

    ctx = Ctx()
    row = {"version": 4, "json": {"keep": [[8, 44], [65, 106]]}}
    first = agent_tools._review_program_story(ctx, row)
    second = agent_tools._review_program_story(ctx, row)

    assert first["edl_version"] == 4
    assert second == first
    assert calls == [1]
