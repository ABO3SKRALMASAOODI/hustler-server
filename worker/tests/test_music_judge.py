"""Soundtrack choice uses waveform evidence, not catalog titles alone."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import music_judge  # noqa: E402


def test_speech_led_dark_brief_rewards_bass_and_low_masking_density():
    brief = "dark driving founder reel, instrumental bed under speech"
    clean = ({"id": "a", "title": "Night Drive", "provider": "x"},
             {"bpm": 128, "bpm_conf": .86, "dynamic_range_db": 10,
              "spectral_centroid_hz": 1450, "midband_ratio": .18,
              "bass_ratio": .28, "energy": [-8, -4]})
    crowded = ({"id": "b", "title": "Bright Vocal Song", "provider": "y"},
               {"bpm": 82, "bpm_conf": .4, "dynamic_range_db": 4,
                "spectral_centroid_hz": 4200, "midband_ratio": .72,
                "bass_ratio": .04, "energy": [-8, -4]})
    ranked = music_judge.rank([crowded, clean], brief, speech_led=True)
    assert [row["id"] for row in ranked] == ["a", "b"]
    assert any("masks less" in reason for reason in ranked[0]["reasons"])
    assert any("competing with speech" in reason
               for reason in ranked[1]["reasons"])


def test_audition_tool_compares_cached_real_measurements_against_blueprint(
        monkeypatch):
    hits = {
        "jamendo:1": {"id": "jamendo:1", "title": "Dark Pulse",
                      "provider": "jamendo"},
        "openverse:2": {"id": "openverse:2", "title": "Soft Vocal Song",
                        "provider": "openverse"},
    }
    ctx = SimpleNamespace(
        _music_hits=hits,
        _music_auditions={
            "jamendo:1": {"bpm": 126, "bpm_conf": .8,
                           "dynamic_range_db": 11,
                           "spectral_centroid_hz": 1500,
                           "midband_ratio": .2, "bass_ratio": .25,
                           "energy": [-7, -4]},
            "openverse:2": {"bpm": 70, "bpm_conf": .3,
                             "dynamic_range_db": 4,
                             "spectral_centroid_hz": 3800,
                             "midband_ratio": .7, "bass_ratio": .03,
                             "energy": [-7, -4]},
        },
        edit_plan={"steps": ["choose the soundtrack"],
                   "brief": "founder reel", "format": "reel",
                   "intent": "high retention", "objective": "profile visit",
                   "music_direction": "dark modern driving instrumental",
                   "sequence_map": [
                       {"role": "hook", "anchor": "the hard truth",
                        "purpose": "create tension",
                        "sound": "voice starts dry; pulse blooms after claim",
                        "energy": .55},
                       {"role": "proof", "anchor": "the measured result",
                        "purpose": "make the result land",
                        "sound": "track grows without masking dialogue",
                        "energy": .85},
                   ]},
        user_message="make it feel premium",
        index={"words": [{"w": "hello"}]},
        job={}, project_id=7, workdir="/tmp",
    )
    seen = {}
    original_rank = agent_tools.music_judge.rank

    def capture_rank(candidates, direction, **kwargs):
        seen["direction"] = direction
        return original_rank(candidates, direction, **kwargs)

    monkeypatch.setattr(agent_tools.music_judge, "rank", capture_rank)
    out = agent_tools.audition_music_candidates(
        ctx, ["openverse:2", "jamendo:1"], brief="dark pulse")
    assert "waveform was measured, not heard" in out
    assert out.index("jamendo:1") < out.index("openverse:2")
    assert "dialogue-band density" in out
    # The explicit query/brief supplements the durable sequence direction;
    # it no longer replaces it.
    assert "dark pulse" in seen["direction"]
    assert "pulse blooms after claim" in seen["direction"]
    assert "track grows without masking dialogue" in seen["direction"]


def test_audition_is_a_measurement_tool_not_a_claim_that_model_heard_audio():
    desc = agent_tools.TOOLS["audition_music_candidates"][1]
    assert "does not hear" in desc
    assert "listen_to" not in agent_tools.TOOLS


def test_research_music_searches_then_compares_current_slate(monkeypatch):
    ctx = SimpleNamespace(_music_hits={"stale": {"id": "stale"}})
    calls = []

    def fake_search(got_ctx, query, **kwargs):
        assert got_ctx._music_hits == {}
        got_ctx._music_hits = {
            f"track:{i}": {"id": f"track:{i}", "title": f"Track {i}"}
            for i in range(6)
        }
        calls.append(("search", query, kwargs))
        return "LICENSED SEARCH SLATE"

    def fake_audition(got_ctx, ids, brief=None):
        calls.append(("audition", ids, brief))
        return "MEASURED COMPARISON"

    monkeypatch.setattr(agent_tools, "search_music", fake_search)
    monkeypatch.setattr(agent_tools, "audition_music_candidates",
                        fake_audition)
    out = agent_tools.research_music(
        ctx, "dark modern pulse", brief="founder reel",
        min_seconds=20, commercial_use=True)

    assert calls[0] == (
        "search", "dark modern pulse",
        {"min_seconds": 20, "max_seconds": None, "commercial_use": True})
    assert calls[1] == (
        "audition", ["track:0", "track:1", "track:2", "track:3"],
        "founder reel")
    assert "LICENSED SEARCH SLATE" in out
    assert "MEASURED COMPARISON" in out
    assert ctx.editing_metrics["music_research_packets"] == 1


def test_research_music_never_auditions_stale_results_after_failed_search(
        monkeypatch):
    ctx = SimpleNamespace(_music_hits={"stale": {"id": "stale"}})

    monkeypatch.setattr(
        agent_tools, "search_music",
        lambda got_ctx, query, **kwargs: "No tracks matched the new query.")
    monkeypatch.setattr(
        agent_tools, "audition_music_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("stale slate must not be auditioned")))

    out = agent_tools.research_music(ctx, "no such track")
    assert out == "No tracks matched the new query."
    assert ctx._music_hits == {}


def test_research_music_is_registered_as_one_pass_editorial_evidence():
    fn, desc, schema = agent_tools.TOOLS["research_music"]
    assert fn is agent_tools.research_music
    assert "single evidence pass" in desc
    assert set(schema) >= {"query", "brief", "commercial_use"}
    assert agent_tools.REQUIRED_ARGS["research_music"] == ["query"]
