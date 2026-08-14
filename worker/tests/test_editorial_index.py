"""The editorial map joins cached senses without adding a model call."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import editorial_index                                        # noqa: E402


def _index():
    words = [
        {"w": "Start", "t0": .5, "t1": .9},
        {"w": "quietly", "t0": 1.0, "t1": 1.6},
        {"w": "Show", "t0": 2.8, "t1": 3.2},
        {"w": "the", "t0": 3.3, "t1": 3.5},
        {"w": "proof", "t0": 3.6, "t1": 4.1},
        {"w": "Now", "t0": 8.2, "t1": 8.6},
        {"w": "land", "t0": 8.7, "t1": 9.2},
    ]
    return {
        "video": {"duration": 12.0, "width": 1080, "height": 1920,
                  "has_audio": True},
        "shots": [
            {"id": 1, "start": 0.0, "end": 3.0},
            {"id": 2, "start": 3.0, "end": 8.0},
            {"id": 3, "start": 8.0, "end": 12.0},
        ],
        "words": words,
        "sentences": [
            {"id": "s1", "text": "Start quietly", "t0": .5, "t1": 1.7,
             "wi0": 0, "wi1": 1, "speaker": 0},
            {"id": "s2", "text": "Show the proof", "t0": 2.8, "t1": 4.2,
             "wi0": 2, "wi1": 4, "speaker": 0},
            {"id": "s3", "text": "Now land", "t0": 8.2, "t1": 9.3,
             "wi0": 5, "wi1": 6, "speaker": 0},
        ],
        "silences": [[6.0, 8.05]],
        "perception": {
            "energy_bin_s": .5,
            "energy": ([-28.0] * 5 + [-21.0, -14.0, -8.0, -5.0] +
                       [-12.0] * 7 + [-25.0] * 4 + [-9.0] * 4),
            "beats": [i * .5 for i in range(25)],
            "vb_env_fps": 2.0,
            "vb_env": ([.1, .2, .2, .1, .1, .2, .9, .5, .2, .1, .1, .1] +
                       [.05] * 12),
        },
        "spatial": {
            "sample_step_s": 3.0,
            "samples": [
                {"t": 1.0, "faces": [[.2, .1, .5, .7]], "text": [],
                 "dense_ui": False, "mean_luma": 100, "std_luma": 30},
                {"t": 3.5, "faces": [], "text": [[.1, .1, .9, .2]],
                 "dense_ui": True, "mean_luma": 110, "std_luma": 35},
                {"t": 8.8, "faces": [], "text": [], "dense_ui": False,
                 "mean_luma": 2, "std_luma": 1},
            ],
        },
        "motion": {
            "version": 2, "sampling_mode": "distributed_windows",
            "sections": [
                {"start_s": 0.0, "end_s": 5.0, "samples": 20,
                 "intensity": "gentle", "mean_frame_change": 2.0,
                 "p90_frame_change": 4.0, "freeze_share": .2},
                {"start_s": 8.0, "end_s": 12.0, "samples": 16,
                 "intensity": "high", "mean_frame_change": 9.0,
                 "p90_frame_change": 16.0, "freeze_share": .05},
            ],
        },
    }


def test_cross_modal_rows_share_one_exact_source_clock():
    result = editorial_index.build(_index())
    s1, s2, s3 = result["rows"]

    assert result["measured"] == {
        "speech": True, "shots": True, "audio": True, "beats": True,
        "vocal_stress": True, "spatial": True, "section_motion": True,
    }
    assert s1["shots"]["ids"] == [1]
    assert s1["picture"]["face_presence"] == 1.0
    assert s2["shots"]["ids"] == [1, 2]
    assert s2["shots"]["changes_s"] == [3.0]
    assert s2["picture"]["dense_ui_presence"] == 1.0
    assert s2["audio"]["trend"] == "rising"
    assert s2["motion"]["intensity"] == "gentle"
    assert "scene_change_inside" in s2["tags"]
    assert "dense_ui" in s2["tags"]
    assert s3["pauses"]["speech_gap_before_s"] == 4.0
    assert "pause_before" in s3["tags"]
    assert s3["picture"]["blank_presence"] == 1.0
    assert s3["motion"]["intensity"] == "high"
    assert "high_motion" in s3["tags"]


def test_focus_filters_evidence_without_reordering_it():
    result = editorial_index.build(_index())

    assert [row["id"] for row in editorial_index.query(
        result, focus="faces")] == ["s1"]
    assert [row["id"] for row in editorial_index.query(
        result, focus="ui")] == ["s2"]
    peaks = editorial_index.query(result, focus="peaks")
    assert [row["t0"] for row in peaks] == sorted(row["t0"] for row in peaks)


def test_silent_footage_falls_back_to_shots_instead_of_empty_map():
    index = _index()
    index["sentences"] = []
    index["words"] = []
    result = editorial_index.build(index)

    assert [row["kind"] for row in result["rows"]] == ["shot"] * 3
    assert result["rows"][1]["id"] == 2
    assert result["rows"][1]["picture"]["dense_ui_presence"] > 0


def test_flat_energy_is_neutral_not_falsely_a_peak():
    index = _index()
    index["perception"]["energy"] = [-12.0] * 24
    result = editorial_index.build(index)

    assert all(row["audio"]["level"] == "medium" for row in result["rows"])
    assert all("energy_peak" not in row["tags"] for row in result["rows"])


def test_agent_tool_is_bounded_honest_and_available_in_compact_catalog():
    ctx = agent_tools.ToolContext(
        None, {"id": 1}, {"id": 7, "chat_session_id": 9}, _index(), "/tmp")
    out = agent_tools.get_editorial_map(ctx, focus="peaks", limit=2)

    assert "EDITORIAL EVIDENCE MAP" in out
    assert "does NOT recognize the full picture or prescribe effects" in out
    assert "scene change@3" in out
    assert "Continue with get_editorial_map" in out
    assert "get_editorial_map" in agent_tools.compact_tool_names(ctx)
    fn, description, schema = agent_tools.TOOLS["get_editorial_map"]
    assert fn is agent_tools.get_editorial_map
    assert "cross-modal" in description
    assert schema["focus"]["enum"] == sorted(editorial_index.FOCI)
    assert agent_tools.REQUIRED_ARGS["get_editorial_map"] == []


def test_agent_map_does_not_repeat_complete_transcript_already_in_state():
    ctx = agent_tools.ToolContext(
        None, {"id": 1}, {"id": 7, "chat_session_id": 9}, _index(), "/tmp")
    ctx.full_transcript_in_context = True

    out = agent_tools.get_editorial_map(ctx, focus="all")

    assert "Sentence text is omitted" in out
    assert "Start quietly" not in out
    assert "[s1 " in out
