"""Shorts mode (round 99) — the pure logic of the pipeline.

The plan parser/validator, the reference→style mappings, the count
heuristic, and the wiring facts main.py relies on (job type registered,
charged like a turn, notes present). No DB, no LLM, no media.

Run from worker/:  python3 -m pytest tests/test_shorts.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shorts                                                # noqa: E402


# ------------------------------------------------------------------ helpers
def test_default_count_scales_with_duration_and_caps():
    assert shorts._default_count(120) == 1          # 2 min -> still 1
    assert shorts._default_count(1500) == 5         # 25 min -> 5
    assert shorts._default_count(6 * 3600) == shorts.config.SHORTS_MAX_CLIPS


def test_json_from_tolerates_fences_and_prose():
    assert shorts._json_from('```json\n{"a": 1}\n```') == {"a": 1}
    assert shorts._json_from('Here you go: {"clips": []}') == {"clips": []}
    assert shorts._json_from("no json here") is None
    assert shorts._json_from("") is None


# ------------------------------------------------------------------ style
def test_caption_preset_mapping():
    pick = shorts._pick_caption_preset
    assert pick({"captions": {"style_words": "word-by-word karaoke pop"}}) \
        == "karaoke"
    assert pick({"captions": {"style_words": "bold yellow impact hype"}}) \
        == "beast"
    assert pick({"captions": {"style_words": "thin elegant serif"}}) \
        == "elegant"
    assert pick({"captions": {"style_words": "clean white phrases"}}) \
        == "podcast"
    assert pick(None) == "podcast"                  # no vision -> safe default


def test_grade_mapping_only_returns_real_presets():
    from schemas import GRADE_PRESETS
    cases = ["black and white", "teal cinematic", "warm golden", "cool blue",
             "vintage film", "vibrant punchy", "natural", ""]
    for words in cases:
        got = shorts._pick_grade({"grade_words": words})
        assert got is None or got in GRADE_PRESETS
    assert shorts._pick_grade({"grade_words": "black and white"}) == "bw"
    assert shorts._pick_grade({"grade_words": "natural look"}) is None


# ------------------------------------------------------------------ the plan
def _fake_plan(monkeypatch, clips):
    monkeypatch.setattr(shorts, "_ask_json",
                        lambda *a, **k: {"clips": clips})


def _index(sentences):
    return {"sentences": sentences,
            "video": {"duration": 600.0}}


SENTS = [{"t0": float(i * 30), "t1": float(i * 30 + 28),
          "text": f"sentence number {i} with a full thought in it",
          "speaker": None} for i in range(20)]


def test_plan_drops_short_long_and_overlapping_clips(monkeypatch):
    _fake_plan(monkeypatch, [
        {"start": 0, "end": 4, "title": "too short", "score": 99},
        {"start": 30, "end": 58, "title": "keeper", "score": 90},
        {"start": 35, "end": 60, "title": "overlaps keeper", "score": 80},
        {"start": 120, "end": 260, "title": "too long gets trimmed",
         "score": 70},
        {"start": 400, "end": 430, "title": "second keeper", "score": 60},
    ])
    out = shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                             None, {}, False, "free")
    titles = [c["title"] for c in out]
    assert "too short" not in titles
    assert "keeper" in titles and "second keeper" in titles
    assert "overlaps keeper" not in titles
    trimmed = next(c for c in out if c["title"].startswith("too long"))
    assert trimmed["end"] - trimmed["start"] <= shorts.CLIP_MAX_S
    # orders are assigned contiguously by score
    assert [c["order"] for c in out] == list(range(len(out)))


def test_plan_respects_requested_count(monkeypatch):
    _fake_plan(monkeypatch, [
        {"start": i * 60.0, "end": i * 60.0 + 30, "title": f"c{i}",
         "score": 100 - i} for i in range(8)])
    out = shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                             None, {"count": 3}, False, "free")
    assert len(out) == 3
    assert [c["score"] for c in out] == sorted(
        [c["score"] for c in out], reverse=True)


def test_plan_without_speech_fails_honestly(monkeypatch):
    _fake_plan(monkeypatch, [])
    try:
        shorts._plan_clips(None, {"user_id": 1}, _index([]), 600.0,
                           None, {}, False, "free")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "no transcribed speech" in str(e)


def test_transcript_block_truncates():
    sents = [{"t0": i, "t1": i + 1, "text": "word " * 30, "speaker": 0}
             for i in range(3000)]
    block = shorts._transcript_block({"sentences": sents}, max_chars=5000)
    assert len(block) <= 5000 + 30
    assert "truncated" in block


# ------------------------------------------------------------------ wiring
def test_job_type_is_registered_everywhere():
    import main
    assert "shorts_plan" in main.AGENT_TYPES
    assert main.RUNNERS.get("shorts_plan") is shorts.run_shorts_plan
    assert "shorts_plan" in main.FAIL_NOTES
    assert "shorts_plan" in main.REAPER_NOTES


def test_make_shorts_is_an_agent_tool():
    import agent_tools
    assert "make_shorts" in agent_tools.TOOLS
    assert "make_shorts" in agent_tools.REQUIRED_ARGS
    # It never writes the EDL, so the honesty layer must not count it.
    assert "make_shorts" not in agent_tools.WRITE_TOOLS


def test_flat_clip_charge_rides_the_turn_charge():
    """charge_turn_credits grew extra_credits — the shorts surcharge must be
    additive and never able to go negative."""
    import inspect
    import db as dbx
    sig = inspect.signature(dbx.charge_turn_credits)
    assert "extra_credits" in sig.parameters
    assert sig.parameters["extra_credits"].default == 0.0
