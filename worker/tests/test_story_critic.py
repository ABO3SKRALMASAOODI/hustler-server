"""Independent semantic review is scoped, grounded, and version-safe."""

import json
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
    joins = evidence["assembled_edit_joins"]
    assert "target=join:keep-1->keep-2" in joins
    assert "Then we watched every customer session" in joins
    assert "We rebuilt checkout around the failed sessions" in joins
    assert evidence["assembled_edit_joins_sampled_evenly"] is False


def test_story_report_requires_grounded_high_confidence_major_finding():
    report = story_critic.parse_report(
        '{"verdict":"pass","summary":"answer lost its setup",'
        '"findings":[{"severity":"major",'
        '"category":"missing_context","program_s":0,'
        '"target_id":"keep-1",'
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
    assert report["story_critic_v"] == 3
    assert seen["kwargs"]["purpose"] == "independent_story_critic"
    assert seen["kwargs"]["max_tokens"] == 1100
    assert "assembled transcript" in seen["system"]


def test_story_reviewer_reconstructs_dialogue_from_multiple_uploaded_clips():
    clip_a = _index()
    clip_b = _index()
    assets = {"clip-a": clip_a, "clip-b": clip_b}
    edl = {
        "keep": [],
        "inserts": [
            {"id": "ins-a", "asset_key": "clip-a", "kind": "video",
             "at_output_s": 0, "duration_s": 18,
             "source_start_s": 8, "rate": 2.0},
            {"id": "ins-b", "asset_key": "clip-b", "kind": "video",
             "at_output_s": 0, "duration_s": 29,
             "source_start_s": 77, "rate": 1.0},
        ],
    }
    assert story_critic.should_review(
        edl, {}, "podcast_conversation", assets)
    evidence = story_critic.build_evidence(
        edl, {}, "podcast_conversation",
        "assemble one coherent conversation", asset_indexes=assets)
    transcript = evidence["program_transcript"]
    assert "insert ins-a CLIP" in transcript
    assert "insert ins-b CLIP" in transcript
    # Clip A starts at source 8s and runs 2x, so its first sentence midpoint
    # (13s) lands at output 2.5s. Clip B follows the 18s first insert.
    assert "program 2.50s | insert ins-a CLIP" in transcript
    assert "program 24.50s | insert ins-b CLIP" in transcript
    assert transcript.index("We launched before customers") < \
        transcript.index("We rebuilt checkout")
    boundary = evidence["source_context_around_every_keep_boundary"]
    assert "insert ins-a CLIP chosen=8.00-44.00" in boundary
    assert "CUT" in boundary


def test_muted_insert_dialogue_is_not_claimed_as_program_story():
    assets = {"muted": _index(), "audible": _index()}
    edl = {
        "keep": [],
        "inserts": [
            {"id": "muted", "asset_key": "muted", "kind": "video",
             "at_output_s": 0, "duration_s": 44, "mute": True},
            {"id": "audible", "asset_key": "audible", "kind": "video",
             "at_output_s": 0, "duration_s": 44},
        ],
    }
    evidence = story_critic.build_evidence(
        edl, {}, "podcast_conversation", asset_indexes=assets)
    assert "insert muted CLIP" not in evidence["program_transcript"]
    assert "insert audible CLIP" in evidence["program_transcript"]


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
                                 "target_id": "keep-1",
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


def test_story_evidence_never_invents_cut_sentence_words():
    evidence = story_critic.build_evidence(
        {"keep": [[12, 18], [77, 90]]}, _index(),
        "podcast_conversation")

    transcript = evidence["program_transcript"]
    assert "customers could finish checkout" in transcript
    assert "We launched before customers" not in transcript
    assert "CUT-IN INSIDE SENTENCE" in transcript
    assert "PARTIAL; exact surviving words" in \
        evidence["source_context_around_every_keep_boundary"]


def test_story_evidence_exposes_middle_sentence_hole_as_two_fragments():
    evidence = story_critic.build_evidence(
        {"keep": [[8, 12], [14, 18], [77, 90]]}, _index(),
        "podcast_conversation")

    transcript = evidence["program_transcript"]
    assert "We launched before customers could finish checkout" not in \
        transcript
    assert transcript.count("CUT-OFF INSIDE SENTENCE") == 1
    assert transcript.count("CUT-IN INSIDE SENTENCE") == 1
    assert "We launched before" in transcript
    assert "could finish checkout" in transcript


def test_inserted_clip_uses_exact_surviving_words_and_real_target():
    assets = {"clip-a": _index()}
    edl = {
        "keep": [],
        "inserts": [{
            "id": "ins-a", "asset_key": "clip-a", "kind": "video",
            "at_output_s": 0, "duration_s": 6,
            "source_start_s": 12, "rate": 1.0,
        }],
    }
    evidence = story_critic.build_evidence(
        edl, {}, "podcast_conversation", asset_indexes=assets)

    assert "customers could finish checkout" in evidence["program_transcript"]
    assert "We launched before customers" not in evidence["program_transcript"]
    assert "CUT-IN INSIDE SENTENCE" in evidence["program_transcript"]
    assert [row["id"] for row in evidence["repair_targets"]] == [
        "insert:ins-a"]


def test_story_repair_requires_real_target_and_program_clock(monkeypatch):
    answers = iter([
        # A plausible but invented target is advisory only.
        '{"verdict":"repair","summary":"missing setup","findings":['
        '{"severity":"major","category":"broken_question_answer",'
        '"program_s":0,"target_id":"keep-99",'
        '"evidence":"answer begins without the nearby question",'
        '"repair":"extend the first excerpt","confidence":0.96}]}',
        # A real target with an impossible output clock is non-actionable.
        '{"verdict":"repair","summary":"missing setup","findings":['
        '{"severity":"major","category":"broken_question_answer",'
        '"program_s":999,"target_id":"keep-1",'
        '"evidence":"answer begins without the nearby question",'
        '"repair":"extend the first excerpt","confidence":0.96}]}',
    ])
    monkeypatch.setattr(
        story_critic.llm, "ask_text",
        lambda *_args, **_kwargs: {"text": next(answers)})
    edl = {"keep": [[8, 44], [77, 106]]}

    invented = story_critic.review(
        edl, _index(), "podcast_conversation")
    impossible_time = story_critic.review(
        edl, _index(), "podcast_conversation")

    assert invented["findings"][0]["target_id"] is None
    assert impossible_time["findings"][0]["target_id"] is None
    assert story_critic.repair_lines(invented) == []
    assert story_critic.repair_lines(impossible_time) == []


def test_story_repair_carries_exact_boundary_and_safe_source_range(
        monkeypatch):
    monkeypatch.setattr(
        story_critic.llm, "ask_text", lambda *_args, **_kwargs: {"text":
        '{"verdict":"repair","summary":"broken join","findings":['
        '{"severity":"major","category":"referent_without_antecedent",'
        '"program_s":36,"target_id":"join:keep-1->keep-2",'
        '"suggested_source_start_s":65,"suggested_source_end_s":106,'
        '"evidence":"the second excerpt begins with an unresolved that",'
        '"repair":"restore the question before the answer",'
        '"confidence":0.94}]}'})

    report = story_critic.review(
        {"keep": [[8, 44], [77, 106]]}, _index(),
        "podcast_conversation")
    repair = story_critic.repair_lines(report)[0]

    assert "target=join:keep-1->keep-2" in repair
    assert "suggested_source=65.00-106.00s" in repair


def test_assembled_join_preserves_question_answer_speakers_and_order():
    evidence = story_critic.build_evidence(
        {"keep": [[65, 76], [77, 90]]}, _index(),
        "podcast_conversation")

    join = evidence["assembled_edit_joins"]
    assert "target=join:keep-1->keep-2" in join
    assert "LEFT keep-1 S0" in join
    assert "So what did you change after seeing that?" in join
    assert "RIGHT keep-2 S1" in join
    assert "We rebuilt checkout around the failed sessions." in join


def test_micro_cut_story_evidence_bounds_targets_without_hiding_sampling():
    sentences = []
    words = []
    keep = []
    for n in range(220):
        t0 = float(n * 2)
        sentences.append({
            "t0": t0, "t1": t0 + 1, "speaker": n % 2,
            "text": f"thought {n}",
        })
        words.append({"w": f"thought-{n}", "t0": t0 + .1,
                      "t1": t0 + .9})
        keep.append([t0, t0 + 1])
    index = {"video": {"duration": 440}, "sentences": sentences,
             "words": words}

    evidence = story_critic.build_evidence(
        {"keep": keep}, index, "podcast_conversation")

    assert evidence["program_sampled_evenly"] is True
    assert evidence["assembled_edit_joins_sampled_evenly"] is True
    assert evidence["repair_targets_sampled_to_shown_evidence"] is True
    # Targets are only supplied for the bounded evidence the model can see;
    # this does not constrain how many cuts the EDL may contain.
    assert len(evidence["repair_targets"]) <= 600
    packed = story_critic._packed_review_evidence(evidence)
    encoded = json.dumps(packed, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) <= 30000
    assert packed["program_sampled_evenly"] is True
    assert packed["assembled_edit_joins_sampled_evenly"] is True
    assert packed["repair_targets_sampled_to_shown_evidence"] is True
