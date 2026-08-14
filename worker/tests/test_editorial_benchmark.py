"""Finished-edit benchmarks are blinded, two-order and evidence-separated."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import editorial_benchmark as bench  # noqa: E402


def _report(winner, dimension="visual_coherence", evidence="specific tile"):
    return {
        "overall_winner": winner,
        "confidence": 0.91,
        "decisive_evidence": evidence,
        "dimensions": {
            dimension: {
                "winner": winner, "evidence": evidence, "confidence": 0.88,
            }
        },
    }


def test_parser_keeps_only_grounded_known_dimensions():
    answer = "prose before\n```json\n" + json.dumps({
        "overall_winner": "left",
        "confidence": 1.7,
        "decisive_evidence": "tile 3 preserves the product",
        "dimensions": {
            "visual_coherence": {
                "winner": "left", "evidence": "consistent frame system",
                "confidence": .87,
            },
            "invented_score": {
                "winner": "right", "evidence": "more effects",
                "confidence": .99,
            },
            "typography": {
                "winner": "left", "evidence": "", "confidence": .9,
            },
        },
    }) + "\n```"
    got = bench.parse_judgment(answer, bench.VISUAL_DIMENSIONS)
    assert got["overall_winner"] == "left"
    assert got["confidence"] == 1.0
    assert set(got["dimensions"]) == {"visual_coherence"}


def test_consensus_maps_reversed_order_back_to_canonical_sides():
    # Original LEFT wins. In the reversed presentation it is labeled RIGHT.
    got = bench.consensus(
        _report("left"), _report("right"), ["visual_coherence"])
    assert got["overall_winner"] == "left"
    assert got["dimensions"]["visual_coherence"]["winner"] == "left"
    assert got["positional_consistency"] is True
    assert got["confidence"] == .91


def test_position_bias_becomes_insufficient_instead_of_a_fake_win():
    # The judge picked whichever item appeared first in both calls.
    got = bench.consensus(
        _report("left"), _report("left"), ["visual_coherence"])
    assert got["overall_winner"] == "insufficient"
    assert got["dimensions"]["visual_coherence"]["winner"] == \
        "insufficient"
    assert set(got["positional_disagreements"]) == {
        "visual_coherence", "overall"}


def test_visual_benchmark_runs_both_orders_and_keeps_edits_blinded(
        monkeypatch):
    calls = []

    monkeypatch.setattr(bench.llm, "vision_available", lambda: True)

    def fake_vision(prompt, paths, **kwargs):
        calls.append((paths, kwargs["image_names"], prompt))
        # Original-left page is visibly named only LEFT/RIGHT, never reference
        # or candidate. Its filename lets this test simulate stable judgment.
        winner = "left" if paths[0] == "left-page.jpg" else "right"
        return json.dumps(_report(winner))

    monkeypatch.setattr(bench.llm, "ask_vision", fake_vision)
    got = bench.compare_visual(
        ["left-page.jpg"], ["opening and event tiles"],
        ["right-page.jpg"], ["opening and event tiles"],
        family="talking_head_social", brief="make a credible founder reel")

    assert len(calls) == 2
    assert calls[0][0] == ["left-page.jpg", "right-page.jpg"]
    assert calls[1][0] == ["right-page.jpg", "left-page.jpg"]
    assert all("human" not in " ".join(names).lower()
               and "candidate" not in " ".join(names).lower()
               for _paths, names, _prompt in calls)
    assert got["overall_winner"] == "left"
    assert got["positional_consistency"] is True
    assert "feature" in calls[0][2].lower()


def test_evaluate_pair_keeps_channels_and_human_judgment_separate(
        monkeypatch):
    visual = bench.consensus(
        _report("left"), _report("right"), ["visual_coherence"])
    story = bench.consensus(
        _report("right", "context_integrity"),
        _report("left", "context_integrity"), ["context_integrity"])
    monkeypatch.setattr(bench, "compare_visual", lambda *a, **k: visual)
    monkeypatch.setattr(bench, "compare_story", lambda *a, **k: story)
    monkeypatch.setattr(bench, "compare_audio", lambda *a, **k: None)

    result = bench.evaluate_pair({
        "id": "podcast-01", "family": "podcast_conversation",
        "brief": "one coherent exchange", "human_winner": "left",
        "left": {"visual_paths": ["a"], "story_text": "A"},
        "right": {"visual_paths": ["b"], "story_text": "B"},
    })
    assert result["human_winner"] == "left"
    assert result["evidence_coverage"] == ["visual", "story"]
    assert result["channels"]["visual"]["overall_winner"] == "left"
    assert result["channels"]["story"]["overall_winner"] == "right"

    summary = bench.summarize([result])
    assert summary["channels"]["visual"]["left"] == 1
    assert summary["channels"]["story"]["right"] == 1
    assert summary["channels"]["audio"]["judged"] == 0
    assert summary["human_model_overall_agreements"] == {
        "visual:agree": 1, "story:differ": 1}


def test_audio_window_labels_follow_the_side_when_order_reverses(monkeypatch):
    calls = []
    monkeypatch.setattr(bench.llm, "audio_review_available", lambda: True)

    def fake_audio(_prompt, paths, labels, **_kwargs):
        calls.append((paths, labels))
        winner = "left" if paths[0] == "left.mp3" else "right"
        return json.dumps(_report(
            winner, dimension="audio_coherence", evidence="heard excerpt"))

    monkeypatch.setattr(bench.llm, "ask_audio", fake_audio)
    got = bench.compare_audio(
        "left.mp3", "right.mp3", left_label="0-6; 20-26",
        right_label="0-6; 18-24")

    assert calls == [
        ((["left.mp3", "right.mp3"]),
         ["LEFT — 0-6; 20-26", "RIGHT — 0-6; 18-24"]),
        ((["right.mp3", "left.mp3"]),
         ["LEFT — 0-6; 18-24", "RIGHT — 0-6; 20-26"]),
    ]
    assert got["overall_winner"] == "left"
