"""Soundtrack choice uses waveform evidence, not catalog titles alone."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import music_judge  # noqa: E402
import schemas  # noqa: E402


def test_audition_windows_cover_track_opening_body_and_ending():
    assert music_judge.audition_windows(120) == [
        (0.0, 6.0), (47.88, 53.88), (114.0, 120.0)]
    assert music_judge.audition_windows(5) == [(0.0, 5.0)]


def test_actual_listener_can_choose_music_or_keep_the_edit_dry():
    ids = ["jamendo:1", "openverse:2"]
    chosen = music_judge.listener_choice(
        '{"choice":"jamendo:1","reason":"clean pulse under speech"}', ids)
    assert chosen == {"choice": "jamendo:1", "abstain": False,
                      "reason": "clean pulse under speech"}
    dry = music_judge.listener_choice(
        '{"choice":"none","reason":"both tracks cheapen the confession"}',
        ids)
    assert dry["abstain"] and dry["choice"] is None
    assert music_judge.listener_choice(
        '{"choice":"invented","reason":"not in slate"}', ids) is None


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


def test_actual_listener_hears_three_phase_reels_and_can_abstain(
        monkeypatch, tmp_path):
    hits = {
        "jamendo:1": {"id": "jamendo:1", "title": "Dark Pulse",
                      "provider": "jamendo"},
        "openverse:2": {"id": "openverse:2", "title": "Warm Bed",
                        "provider": "openverse"},
    }
    ctx = SimpleNamespace(
        _music_hits=hits, _music_auditions={}, edit_plan={},
        user_message="keep the confession intimate",
        index={"words": [{"w": "hello"}]}, job={}, project_id=7,
        workdir=str(tmp_path), editing_metrics={})
    windows_seen = []
    call = {}

    monkeypatch.setattr(
        agent_tools.music_search, "download",
        lambda _hit, dst: open(dst, "wb").write(b"audio"))
    monkeypatch.setattr(agent_tools.music_search, "probe_duration_s",
                        lambda _path: 120.0)
    monkeypatch.setattr(
        agent_tools.perception, "analyze_audio",
        lambda *_a, **_k: {"bpm": 100, "bpm_conf": .7,
                            "dynamic_range_db": 9,
                            "spectral_centroid_hz": 1800,
                            "midband_ratio": .2, "bass_ratio": .15,
                            "energy": [-12.0] * 30})
    monkeypatch.setattr(agent_tools.llm, "audio_review_available", lambda: True)

    def montage(_src, windows, dst):
        windows_seen.append(windows)
        open(dst, "wb").write(b"montage")

    def listen(prompt, paths, labels, **kwargs):
        call.update(prompt=prompt, paths=paths, labels=labels, kwargs=kwargs)
        return ('{"choice":"none","reason":'
                '"both tracks cheapen the intimate confession"}')

    monkeypatch.setattr(agent_tools.media, "extract_audio_windows", montage)
    monkeypatch.setattr(agent_tools.llm, "ask_audio", listen)

    out = agent_tools.audition_music_candidates(
        ctx, ["jamendo:1", "openverse:2"], brief="intimate confession")
    assert windows_seen == [music_judge.audition_windows(120)] * 2
    assert all("0-6s" in label and "114-120s" in label
               for label in call["labels"])
    assert "opening, body and ending" in call["prompt"]
    assert "KEEP THE EDIT DRY" in out
    assert ctx._last_music_listener_choice["abstain"] is True
    assert ctx.editing_metrics["music_abstentions"] == 1
    assert ctx.editing_metrics["editorial_decisions"] == [{
        "kind": "music_cast", "sequence": 1,
        "candidate_ids": ["jamendo:1", "openverse:2"],
        "decision": "none",
        "reason": "both tracks cheapen the intimate confession",
        "review_stage": "actual_audio",
        "source": "independent_listener",
    }]


def test_music_purpose_survives_edl_schema_and_tool_contract():
    item = schemas.MusicItem(
        storage_key="music/one.mp3", start=0, end=30,
        purpose="introduce tension, then release at the proof")
    assert item.purpose.startswith("introduce tension")
    desc = schemas.describe_edl({
        "keep": [[0, 30]],
        "music": [item.model_dump(exclude_none=True)],
    })
    assert "introduce tension" in desc
    assert "purpose" in agent_tools.TOOLS["add_music"][2]


def test_audition_implementation_remains_but_is_not_live_catalog_surface():
    assert callable(agent_tools.audition_music_candidates)
    assert "audition_music_candidates" not in agent_tools.TOOLS
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


def test_research_music_implementation_is_retained_but_retired_from_catalog():
    assert callable(agent_tools.research_music)
    assert "research_music" not in agent_tools.TOOLS
