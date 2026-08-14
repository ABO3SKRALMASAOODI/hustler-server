"""Sound design compares actual transient shape before placement."""

import math
import os
import struct
import sys
import wave
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402
import perception  # noqa: E402
import schemas  # noqa: E402
import sfx_judge  # noqa: E402


def _features(**overrides):
    base = {
        "duration_s": 1.0, "active_duration_s": .9,
        "leading_silence_s": 0, "attack_s": .2,
        "peak_time_s": .45, "peak_position": .5, "tail_s": .4,
        "crest_db": 7, "spectral_centroid_hz": 2200,
        "bass_ratio": .08, "midband_ratio": .4,
        "strong_event_count": 1,
    }
    base.update(overrides)
    return base


def test_riser_and_impact_requests_prefer_different_real_shapes():
    early_bass = ({"id": "impact", "title": "Deep Impact Hit"},
                  _features(attack_s=.03, peak_position=.08,
                            bass_ratio=.32, active_duration_s=.7))
    slow_build = ({"id": "riser", "title": "Cinematic Riser Build"},
                  _features(attack_s=1.5, peak_position=.88,
                            active_duration_s=2.6, duration_s=2.8,
                            bass_ratio=.06))
    assert sfx_judge.rank([slow_build, early_bass],
                          "deep impact on logo reveal")[0]["id"] == "impact"
    assert sfx_judge.rank([early_bass, slow_build],
                          "riser building into reveal")[0]["id"] == "riser"


def test_transient_analyzer_accepts_sub_two_second_one_shot(tmp_path):
    path = tmp_path / "impact.wav"
    sr, duration = 22050, 1.0
    values = []
    for i in range(int(sr * duration)):
        t = i / sr
        if t < .10:
            sample = 0.0
        else:
            age = t - .10
            sample = .8 * math.exp(-age * 7.0) * (
                .75 * math.sin(2 * math.pi * 110 * age)
                + .25 * math.sin(2 * math.pi * 1700 * age))
        values.append(max(-32767, min(32767, int(sample * 32767))))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sr)
        out.writeframes(struct.pack("<" + "h" * len(values), *values))

    measured = perception.analyze_transient(str(path))
    assert .8 <= measured["duration_s"] <= 1.1
    assert measured["attack_s"] < .2
    assert measured["crest_db"] > 1
    assert measured["bass_ratio"] > 0
    assert measured["strong_event_count"] >= 1


def test_audition_tool_ranks_cached_waveforms_and_denies_hearing_claim():
    ctx = SimpleNamespace(
        _sfx_hits={
            "openverse:a": {"id": "openverse:a", "title": "Impact Hit"},
            "openverse:b": {"id": "openverse:b", "title": "Riser"}},
        _sfx_auditions={
            "openverse:a": _features(attack_s=.03, peak_position=.08,
                                      bass_ratio=.3),
            "openverse:b": _features(attack_s=1.4, peak_position=.9,
                                      active_duration_s=2.5)},
        db=SimpleNamespace(run=lambda *args, **kwargs: None), project_id=1,
        workdir="/tmp")
    out = agent_tools.audition_sfx_candidates(
        ctx, ["openverse:b", "openverse:a"], "bass impact on title reveal")
    assert "language model did not hear" in out
    assert out.index("openverse:a") < out.index("openverse:b")
    assert agent_tools.REQUIRED_ARGS["audition_sfx_candidates"] == [
        "ids", "purpose"]


def test_listener_choice_accepts_structured_candidate_or_silence():
    ids = ["openverse:a", "openverse:b"]
    picked = sfx_judge.listener_choice(
        '{"choice":"openverse:b","reason":"cleaner, shorter tail"}', ids)
    dry = sfx_judge.listener_choice(
        '{"choice":"none","reason":"both make the quiet reveal cheaper"}',
        ids)

    assert picked == {"choice": "openverse:b", "abstain": False,
                      "reason": "cleaner, shorter tail"}
    assert dry == {"choice": None, "abstain": True,
                   "reason": "both make the quiet reveal cheaper"}
    assert sfx_judge.listener_choice(
        '{"choice":"none","reason":"quiet wins"}', ids,
        allow_none=False) is None


def test_listener_choice_abstains_on_ambiguous_comparison():
    ids = ["openverse:a", "openverse:b"]
    answer = ("openverse:a is tighter, while openverse:b is softer; either "
              "could work")
    assert sfx_judge.listener_choice(answer, ids) is None


def test_sfx_purpose_is_durable_optional_editorial_provenance():
    edl = schemas.default_edl(10.0)
    edl["sfx"] = [{"id": "sx1", "storage_key": "sfx/1/click.mp3",
                   "at": 3.2, "gain_db": -6,
                   "purpose": "cursor confirms the primary action"}]
    validated = schemas.validate_edl(edl, 10.0).model_dump()

    assert validated["sfx"][0]["purpose"] == \
        "cursor confirms the primary action"
    assert "cursor confirms the primary action"[:32] in \
        schemas.describe_edl(validated, 10.0)
    assert "purpose" in agent_tools.TOOLS["add_sfx"][2]
