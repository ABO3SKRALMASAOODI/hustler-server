"""Regression coverage for the systemic failures found in the MCP audit."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import renderer
import shorts
from schemas import validate_edl
from timeline import Timeline


def _timed_words(text, program_start=0.0, source_start=0.0, step=0.3):
    return [{"w": word,
             "t0": program_start + i * step,
             "t1": program_start + i * step + 0.2,
             "src_t0": source_start + i * step,
             "src_t1": source_start + i * step + 0.2}
            for i, word in enumerate(text.split())]


def test_repetition_classifier_separates_edit_duplicate_from_spoken_repeat():
    phrase = "we built this today"
    duplicated = (_timed_words(phrase, 0, 10) +
                  _timed_words(phrase, 5, 10))
    spoken = (_timed_words(phrase, 0, 10) +
              _timed_words(phrase, 5, 20))
    assert agent_tools.classify_repeated_phrases(duplicated)[0]["kind"] == \
        "edit_duplicate"
    assert agent_tools.classify_repeated_phrases(spoken)[0]["kind"] == \
        "spoken_repetition"


def test_exact_visual_asset_reuse_is_detected_across_windows_and_roles():
    edl = {
        "inserts": [{"id": "i1", "asset_key": "media/p/clip.mp4",
                     "source_start_s": 0}],
        "overlays": [{"id": "o1", "asset_key": "media/p/clip.mp4",
                      "source_start_s": 18}],
    }
    assert agent_tools._visual_asset_uses(edl, "media/p/clip.mp4") == [
        "insert i1", "overlay o1"]


def test_podcast_short_restores_the_question_before_an_isolated_answer():
    index = {"sentences": [
        {"t0": 10.0, "t1": 13.0, "speaker": 0,
         "text": "Why did you decide to start?"},
        {"t0": 13.4, "t1": 19.0, "speaker": 1,
         "text": "Because the problem kept getting worse."},
    ]}
    fixed = shorts._complete_conversation_arcs(
        [{"start": 13.4, "end": 30.0, "title": "The answer"}], index, 60)
    assert fixed[0]["start"] == 10.0
    assert fixed[0]["context_restored"] == "preceding question/setup"


def test_caption_review_samples_real_states_and_can_exceed_old_nine_tiles():
    words = [{"w": f"word{i}", "t0": i * 0.3, "t1": i * 0.3 + 0.2}
             for i in range(30)]
    edl = validate_edl({
        "keep": [[0, 12]],
        "captions": {"mode": "from_transcript", "design_version": 2,
                     "style": {"preset": "karaoke"}},
    }, 12).model_dump()
    with tempfile.TemporaryDirectory() as td:
        times = renderer.caption_review_times(
            edl, {"words": words}, td, duration=12, max_times=16)
    assert len(times) == 16
    assert times == sorted(times)


def test_voiceover_can_seek_in_place_and_social_master_has_safe_ceiling():
    edl = validate_edl({
        "keep": [[0, 10]],
        "master": {"loudness": "social"},
        "voiceover": [{"id": "vo1", "asset_key": "music/song.mp3",
                       "start_output_s": 1.0, "source_offset_s": 51.0}],
    }, 60).model_dump()
    graph = renderer.build_filtergraph(
        edl, 60.0, True, Timeline(edl["keep"]), None, [], {}, False,
        vo_inputs=[(1, edl["voiceover"][0], 20.0)])
    assert ("[1:a]atrim=start=51.000:end=71.000,"
            "asetpts=PTS-STARTPTS" in graph)
    assert "loudnorm=I=-14:TP=-2.0:LRA=11" in graph
    assert "alimiter=limit=0.75" in graph and "level=0:latency=1" in graph


def test_large_edl_section_pages_are_complete_valid_json():
    items = [{"text": f"word {i}", "start": i * 0.05,
              "end": i * 0.05 + 0.05} for i in range(300)]

    class Ctx:
        duration = 30.0

        @staticmethod
        def latest_edl():
            return {"version": 8, "json": {"keep": [[0, 30]],
                                             "captions": items}}

    page = json.loads(agent_tools.get_edl(
        Ctx(), sections=["captions"], offset=100, limit=40))
    assert page["version"] == 8
    assert len(page["sections"]["captions"]) == 40
    assert page["pagination"]["captions"]["next_offset"] == 140


def test_get_edl_accepts_natural_section_aliases_without_a_retry():
    class Ctx:
        duration = 30.0

        @staticmethod
        def latest_edl():
            return {"version": 4, "json": {
                "keep": [[0, 30]], "texts": [{"id": "tx1", "text": "Hi",
                                                "start": 0, "end": 2}],
                "effects": {"grade": "warm"}, "music": []}}

    payload = json.loads(agent_tools.get_edl(
        Ctx(), sections=["cuts", "text", "zoom", "color", "program"]))
    assert payload["sections"]["keep"] == [[0, 30]]
    assert payload["sections"]["texts"][0]["id"] == "tx1"
    assert payload["sections"]["effects"]["grade"] == "warm"
    assert "overview" in payload
    assert payload["aliases_resolved"]["cuts"] == ["keep"]
    assert payload["aliases_resolved"]["zoom"] == ["effects"]
    assert payload["aliases_resolved"]["color"] == ["effects"]

    broad = json.loads(agent_tools.get_edl(
        Ctx(), sections=["timeline", "grade", "media", "erases", "video"]))
    assert broad["sections"]["keep"] == [[0, 30]]
    assert broad["sections"]["effects"]["grade"] == "warm"
    assert "overview" in broad

    everything = json.loads(agent_tools.get_edl(Ctx(), sections="all"))
    assert set(everything["sections"]) == {
        "keep", "texts", "effects", "music"}


def test_list_assets_accepts_natural_media_kind_aliases():
    seen = []

    class Db:
        @staticmethod
        def run(_fn, _project_id, kinds):
            seen.append(kinds)
            return []

    ctx = type("Ctx", (), {"db": Db(), "project_id": 7})()
    assert not agent_tools.list_assets(ctx, "video").startswith("REJECTED")
    assert seen[-1] == ["video_clip"]
    assert not agent_tools.list_assets(ctx, "photos").startswith("REJECTED")
    assert seen[-1] == ["image_ref"]
