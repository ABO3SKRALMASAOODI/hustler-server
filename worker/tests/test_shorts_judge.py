"""The shorts casting pass is grounded, abstention-capable and bounded."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shorts_judge


def _index():
    return {"sentences": [
        {"t0": 0, "t1": 6, "speaker": 0,
         "text": "What nearly killed the company?"},
        {"t0": 7, "t1": 18, "speaker": 1,
         "text": "We launched before checkout worked."},
        {"t0": 19, "t1": 31, "speaker": 1,
         "text": "That mistake cost our first contract."},
        {"t0": 32, "t1": 44, "speaker": 1,
         "text": "We rebuilt it and completion doubled."},
        {"t0": 50, "t1": 58, "speaker": 0,
         "text": "What happened next?"},
    ]}


def _clips():
    return [
        {"start": 0, "end": 44, "title": "The launch mistake", "order": 0},
        {"start": 19, "end": 31, "title": "A quote", "order": 1},
    ]


def test_report_rejects_unknown_ids_and_requires_grounded_decisions():
    report = shorts_judge.parse_report(
        '{"summary":"one real story", "decisions":['
        '{"id":"clip_1","verdict":"keep","confidence":0.91,'
        '"evidence":"0-44s contains question, failure and result",'
        '"reason":"the arc resolves"},'
        '{"id":"invented","verdict":"reject","confidence":0.99,'
        '"evidence":"not supplied","reason":"hallucinated"}]}',
        ["clip_1", "clip_2"])
    assert [row["id"] for row in report["decisions"]] == ["clip_1"]
    assert shorts_judge.parse_report("not json", ["clip_1"]) is None


def test_evidence_marks_exact_window_and_cut_context():
    evidence = shorts_judge.build_evidence(
        [_clips()[1]], _index(), "founder lesson")
    text = evidence["candidates"][0]["exact_window_and_boundary_context"]
    assert "That mistake cost" in text and "KEPT" in text
    assert "We launched before" in text and "CONTEXT/CUT" in text
    assert "We rebuilt it" in text and "CONTEXT/CUT" in text


def test_decisive_rejection_removes_fragment_but_uncertainty_does_not():
    report = {"decisions": [
        {"id": "clip_1", "verdict": "uncertain", "confidence": .7,
         "evidence": "transcript is noisy", "reason": "cannot prove it"},
        {"id": "clip_2", "verdict": "reject", "confidence": .94,
         "evidence": "opens after the question and ends before the result",
         "reason": "isolated quote without progression"},
    ]}
    kept, rejected = shorts_judge.apply_report(_clips(), report)
    assert rejected == 1
    assert [row["title"] for row in kept] == ["The launch mistake"]
    assert kept[0]["order"] == 0
    assert kept[0]["selection_review"]["verdict"] == "uncertain"


def test_review_is_one_bounded_fresh_context_call(monkeypatch):
    seen = {}

    def ask(system, user, **kwargs):
        seen.update(system=system, user=user, kwargs=kwargs)
        return {"text": '{"summary":"keep one","decisions":['
                '{"id":"clip_1","verdict":"keep","confidence":0.9,'
                '"evidence":"question to measurable result",'
                '"reason":"complete progression"},'
                '{"id":"clip_2","verdict":"reject","confidence":0.95,'
                '"evidence":"only the consequence sentence",'
                '"reason":"no setup or payoff"}]}' }

    monkeypatch.setattr(shorts_judge.llm, "ask_text", ask)
    report = shorts_judge.review(_clips(), _index(), "founder lesson")
    assert len(report["decisions"]) == 2
    assert seen["kwargs"]["purpose"] == "independent_shorts_cast"
    assert seen["kwargs"]["max_tokens"] == 1800
    assert "may reject every candidate" in seen["system"]
