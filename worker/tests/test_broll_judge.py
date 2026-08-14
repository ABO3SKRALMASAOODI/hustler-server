"""Sequence casting lets a clean base shot beat weak search results."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import broll_judge  # noqa: E402


CANDIDATES = [
    {"moment_id": "hook", "candidate_id": "broll:1:hook:pexels:1",
     "provider_result_id": "pexels:1", "purpose": "prove the crowded market"},
    {"moment_id": "hook", "candidate_id": "broll:1:hook:pixabay:2",
     "provider_result_id": "pixabay:2", "purpose": "prove the crowded market"},
    {"moment_id": "payoff", "candidate_id": "broll:2:payoff:pexels:1",
     "provider_result_id": "pexels:1", "purpose": "show release"},
]


def test_parse_accepts_abstention_and_scoped_winner():
    report = broll_judge.parse_report("""
    prose before
    {"selections":[
      {"moment_id":"hook","decision":"none","candidate_id":"made-up",
       "confidence":0.91,"visible_evidence":"Both tiles are generic crowds",
       "sequence_reason":"The speaker is more credible","concern":""},
      {"moment_id":"payoff","decision":"use",
       "candidate_id":"broll:2:payoff:pexels:1","confidence":0.82,
       "visible_evidence":"A clearly relieved person is visible",
       "sequence_reason":"A human-scale release answers the hook",
       "concern":"motion remains unverified"}],
     "sequence":{"coherence":"strong","evidence":"speaker then release"}}
    """, CANDIDATES)

    assert report["selections"][0]["decision"] == "none"
    assert report["selections"][0]["candidate_id"] is None
    assert report["selections"][1]["candidate_id"] == \
        "broll:2:payoff:pexels:1"
    assert report["unjudged_moments"] == []
    assert report["sequence"]["coherence"] == "strong"
    text = broll_judge.summary(report)
    assert "KEEP BASE PICTURE / NO B-ROLL" in text
    assert "downloaded rendition" in text


def test_parse_rejects_cross_moment_or_unknown_candidate_ids():
    report = broll_judge.parse_report("""
    {"selections":[
      {"moment_id":"hook","decision":"use",
       "candidate_id":"broll:2:payoff:pexels:1","confidence":0.9,
       "visible_evidence":"wrong moment"},
      {"moment_id":"payoff","decision":"use",
       "candidate_id":"unknown","confidence":0.8,
       "visible_evidence":"not on board"}],
     "sequence":{"coherence":"strong","evidence":"claim"}}
    """, CANDIDATES)
    assert report is None


def test_duplicate_underlying_shot_is_reported_not_silently_reused():
    report = broll_judge.parse_report("""
    {"selections":[
      {"moment_id":"hook","decision":"use",
       "candidate_id":"broll:1:hook:pexels:1","confidence":0.8,
       "visible_evidence":"crowd visible"},
      {"moment_id":"payoff","decision":"use",
       "candidate_id":"broll:2:payoff:pexels:1","confidence":0.7,
       "visible_evidence":"same crowd visible"}],
     "sequence":{"coherence":"mixed","evidence":"repeats one image"}}
    """, CANDIDATES)
    assert report["duplicate_selections"] == [{
        "provider_result_id": "pexels:1", "moments": ["hook", "payoff"]}]
    assert "repetition warning" in broll_judge.summary(report)


def test_review_supplies_none_as_a_real_candidate(monkeypatch, tmp_path):
    seen = {}
    board = tmp_path / "board.jpg"
    board.write_bytes(b"jpeg")
    monkeypatch.setattr(broll_judge.llm, "vision_available", lambda: True)

    def fake_vision(prompt, paths, **kwargs):
        seen["prompt"] = prompt
        seen["paths"] = paths
        seen["purpose"] = kwargs["purpose"]
        return """{"selections":[{"moment_id":"hook","decision":"none",
        "candidate_id":null,"confidence":0.88,
        "visible_evidence":"generic crowds add no specific proof"}],
        "sequence":{"coherence":"not_judged","evidence":"one moment"}}"""

    monkeypatch.setattr(broll_judge.llm, "ask_vision", fake_vision)
    report = broll_judge.review(str(board), CANDIDATES[:2], "restrained founder")
    assert report["selections"][0]["decision"] == "none"
    assert "KEEPING THE BASE SPEAKER/PRODUCT/SCENE" in seen["prompt"]
    assert "A returned search result is not an obligation" in seen["prompt"]
    assert seen["paths"] == [str(board)]
    assert seen["purpose"] == "broll_sequence_cast"


def test_downloaded_rendition_rejects_visible_mismatch():
    report = broll_judge.parse_rendition_report("""
    {"decision":"reject","confidence":0.94,
     "visible_evidence":"All four frames show an empty generic office; the named product never appears",
     "useful_part":"none of the sampled span",
     "concerns":["no promised subject","visible stock watermark"]}
    """)
    assert report["decision"] == "reject"
    assert report["confidence"] == .94
    assert report["concerns"] == [
        "no promised subject", "visible stock watermark"]
    text = broll_judge.rendition_summary(report)
    assert "REJECT" in text
    assert "Do not place this rendition" in text


def test_rendition_review_names_actual_bytes_and_abstains_from_motion(
        monkeypatch, tmp_path):
    frame = tmp_path / "actual.jpg"
    frame.write_bytes(b"jpeg")
    seen = {}
    monkeypatch.setattr(broll_judge.llm, "vision_available", lambda: True)

    def fake_vision(prompt, paths, **kwargs):
        seen["prompt"] = prompt
        seen["purpose"] = kwargs["purpose"]
        return """{"decision":"uncertain","confidence":0.6,
        "visible_evidence":"the product appears once but the useful action is not sampled",
        "useful_part":"middle sample","concerns":["motion unproven"]}"""

    monkeypatch.setattr(broll_judge.llm, "ask_vision", fake_vision)
    report = broll_judge.review_rendition(
        [str(frame)], ["3.2s"], {"purpose": "show the product working"})
    assert report["decision"] == "uncertain"
    assert "ACTUAL DOWNLOADED rendition" in seen["prompt"]
    assert "Do not infer audio, licensing" in seen["prompt"]
    assert seen["purpose"] == "broll_rendition_review"
