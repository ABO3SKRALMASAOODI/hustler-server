"""Track 0B: the RhythmMap contract and the speech prosody grid.

Pins the shape (INTERFACES.md §4.6), the 60 KB rule, the "never invent a
grid" rule, the clocks (source / media -> program), binding resolution,
and the prosody grid's onsets on synthetic speech. AT-0B-6 (stress-peak F1
on V01 149.44-187.0) runs local-only against the labels in the fixture
store.
"""

import json
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import prosody_grid                                          # noqa: E402
import rhythm_types as rt                                    # noqa: E402
from localfix import fixture, local_only                     # noqa: E402
from timeline import Timeline                                # noqa: E402


# ── shape ────────────────────────────────────────────────────────────────

def test_an_empty_map_has_the_pinned_shape():
    m = rt.empty("speech")
    assert set(m) == {"version", "source", "clock", "ref", "producer",
                      "bpm", "beats", "downbeats", "onsets", "sections",
                      "drops", "gaps", "confidence"}
    assert m["clock"] == "source" and rt.empty("music")["clock"] == "media"
    assert set(m["ref"]) == {"storage_key", "sha256", "music_id"}
    rt.validate(m)
    with pytest.raises(ValueError):
        rt.empty("lyrics")


@pytest.mark.parametrize("mutate,match", [
    (lambda m: m.update(version=2), "version"),
    (lambda m: m.update(clock="wall"), "clock"),
    (lambda m: m.update(producer="nobody"), "producer"),
    (lambda m: m.update(beats=[2.0, 1.0]), "sorted"),
    (lambda m: m.update(onsets=[{"t": 1.0, "cls": "kick",
                                  "strength": 0.5}]), "not a speech"),
    (lambda m: m.update(onsets=[{"t": 1.0, "cls": "word",
                                  "strength": 1.5}]), "0..1"),
    (lambda m: m.update(sections=[{"t0": 1, "t1": 2, "label": "drop",
                                    "energy": 0.5}]), "not a speech"),
    (lambda m: m.update(gaps=[{"t0": 2, "t1": 1}]), "t0 < t1"),
    (lambda m: m.update(confidence=2), "confidence"),
])
def test_validate_rejects_off_contract_maps(mutate, match):
    m = rt.empty("speech", producer="prosody_grid@1")
    mutate(m)
    with pytest.raises(ValueError, match=match):
        rt.validate(m)


def test_compact_keeps_maps_under_60kb_dropping_least_useful_first():
    m = rt.empty("speech", producer="prosody_grid@1")
    m["onsets"] = sorted(
        [{"t": i * 0.01, "cls": "syllable", "strength": 0.3}
         for i in range(3000)]
        + [{"t": i * 0.5 + 0.001, "cls": "stress", "strength": 0.9}
           for i in range(60)], key=lambda o: o["t"])
    assert rt.size_bytes(m) > rt.MAX_BYTES
    c = rt.compact(m)
    assert rt.size_bytes(c) <= rt.MAX_BYTES
    assert {o["cls"] for o in c["onsets"]} == {"stress"}
    rt.validate(c)


# ── music producer ───────────────────────────────────────────────────────

def _perception(conf=0.8):
    energy = [-6.0] * 20 + [-40.0] * 3 + [-3.0] * 10
    return {"v": 3, "bpm": 120.0, "bpm_conf": conf,
            "beats": [0.5 * i for i in range(32)], "energy_bin_s": 0.5,
            "energy": energy}


def test_the_beat_grid_becomes_a_music_map_with_gaps_and_drops():
    m = rt.from_beat_grid(_perception(), ref={"music_id": "m1"})
    rt.validate(m)
    assert m["producer"] == "perception.beat_grid@3"
    assert m["bpm"] == 120.0 and len(m["beats"]) == 32
    assert m["gaps"] == [{"t0": 10.0, "t1": 11.5}]
    assert m["drops"] == [11.5]                 # silence, then the hit
    assert m["downbeats"] == []                 # no meter: none invented
    assert m["confidence"] == 0.8


def test_low_confidence_means_no_grid():
    m = rt.from_beat_grid(_perception(conf=0.1))
    assert m["beats"] == [] and m["bpm"] is None
    assert m["confidence"] <= 0.3


# ── clocks ───────────────────────────────────────────────────────────────

def test_speech_maps_move_through_the_cut():
    m = rt.empty("speech", producer="prosody_grid@1")
    m["onsets"] = [{"t": 2.0, "cls": "word", "strength": 0.5},
                   {"t": 6.0, "cls": "stress", "strength": 0.9},
                   {"t": 12.0, "cls": "word", "strength": 0.4}]
    m["sections"] = [{"t0": 3.0, "t1": 12.0, "label": "phrase",
                      "energy": 0.5}]
    tl = Timeline([[0.0, 4.0], [10.0, 14.0]], [], [])
    p = rt.to_program(m, tl)
    assert p["clock"] == "program"
    assert [o["t"] for o in p["onsets"]] == [2.0, 6.0]     # 6.0 cut away
    assert [(s["t0"], s["t1"]) for s in p["sections"]] == [(3.0, 4.0),
                                                           (4.0, 6.0)]


def test_music_maps_follow_start_offset_and_loop():
    m = rt.from_beat_grid(_perception())
    item = {"id": "m1", "start": 2.0, "end": 12.0, "offset_s": 1.0}
    p = rt.to_program(m, None, item)
    assert p["beats"][0] == 2.0 and p["beats"][-1] < 12.0
    assert p["beats"][1] - p["beats"][0] == pytest.approx(0.5)
    looped = rt.to_program(m, None, dict(item, loop=True, offset_s=0.0,
                                         start=0.0, end=40.0), track_dur=16.0)
    assert 16.0 in looped["beats"] and 32.0 in looped["beats"]


def test_bindings_resolve_to_frames_on_the_program_clock():
    words = [{"w": "a", "t0": 1.0, "t1": 1.3}, {"w": "b", "t0": 11.0,
                                                 "t1": 11.4}]
    tl = Timeline([[0.0, 4.0], [10.0, 14.0]], [], [])
    speech = rt.empty("speech", producer="prosody_grid@1")
    speech["onsets"] = [{"t": 5.0, "cls": "stress", "strength": 0.9}]
    music = rt.to_program(rt.from_beat_grid(_perception()), None,
                          {"start": 0.0, "end": 16.0})
    maps = {"speech": speech, "m1": music}
    r = rt.resolve_bind
    assert r({"on": "time", "ref": 2.0}, fps=25) == 2.0
    assert r({"on": "word", "ref": 1}, fps=25, timeline=tl, words=words) \
        == 5.0
    assert r({"on": "phrase_end", "ref": 1}, fps=25, timeline=tl,
             words=words) == pytest.approx(5.4)
    assert r({"on": "word", "ref": 1, "offset_f": -1}, fps=25,
             timeline=tl, words=words) == 4.96
    assert r({"on": "onset", "ref": 0, "rhythm": "speech"}, fps=25,
             maps=maps) == 5.0
    assert r({"on": "beat", "ref": 3, "rhythm": "m1"}, fps=25,
             maps=maps) == 1.52             # 1.5 s on the 25 fps grid
    assert r({"on": "cut", "ref": 10.0}, fps=25, timeline=tl) == 4.0
    assert r({"on": "word", "ref": 9}, fps=25, timeline=tl,
             words=words) is None
    assert r({"on": "beat", "ref": 0, "rhythm": "nope"}, fps=25,
             maps=maps) is None
    assert rt.nearest([1.0, 2.0, 3.0], 2.2) == 1
    assert rt.nearest([1.0], 9.0, max_dist=1.0) is None


# ── prosody grid ─────────────────────────────────────────────────────────

def _speech(n=24, pause_every=6):
    words, t = [], 10.0
    vocab = ["the", "growth", "of", "internet", "was", "bigger", "not",
             "civilization"]
    for i in range(n):
        tok = vocab[i % len(vocab)]
        d = 0.18 + 0.04 * prosody_grid.syllable_count(tok)
        w = tok + ("." if (i + 1) % pause_every == 0 else "")
        words.append({"w": w, "t0": round(t, 3), "t1": round(t + d, 3),
                      "p": 0.95})
        t += d + (0.5 if (i + 1) % pause_every == 0 else 0.04)
    return words


def test_the_prosody_grid_marks_words_stress_phrases_and_pauses():
    words = _speech()
    scores = [0.9 if w["w"].strip(".") in ("growth", "bigger",
                                           "civilization") else 0.35
              for w in words]
    m = prosody_grid.build(words, scores=scores, ref={"sha256": "abc"})
    rt.validate(m)
    assert m["source"] == "speech" and m["clock"] == "source"
    assert m["bpm"] is None and m["beats"] == [] and m["downbeats"] == []
    cls = {}
    for o in m["onsets"]:
        cls.setdefault(o["cls"], []).append(o)
    assert len(cls["word"]) == len(words)
    stressed = {o["t"] for o in cls["stress"]}
    picked = [w["w"].strip(".") for w in words if w["t0"] in stressed]
    assert picked and set(picked) <= {"growth", "bigger", "civilization"}
    assert len(cls["phrase_end"]) == len(m["sections"]) == 4
    assert len(m["gaps"]) == 3 and len(cls["pause_end"]) == 3
    assert all(s["label"] == "phrase" for s in m["sections"])
    assert "syllable" in cls                     # multi-syllable words
    assert m["confidence"] > 0.7
    # function words are never stress candidates; negations are
    assert not prosody_grid.is_candidate("the")
    assert prosody_grid.is_candidate("not")
    assert prosody_grid.is_candidate("internet")


def test_without_acoustics_the_grid_says_so():
    m = prosody_grid.build(_speech())
    assert m["confidence"] < 0.5
    rt.validate(m)
    assert prosody_grid.build([])["onsets"] == []
    odd = [{"w": "x", "t0": "nan?", "t1": 1}, {"w": "y", "t0": 2.0,
                                               "t1": 1.0}]
    assert prosody_grid.build(odd)["onsets"] == []


def test_envelope_scores_read_the_word_not_its_loud_neighbour():
    fps = 8.613
    env = [0.3] * 200                 # running speech
    # stressed words every ~1.5 s, among them one at 10.0-10.3 s (the bins
    # centred inside it), and a quiet function word right after it
    for t0 in (4.0, 5.5, 7.0, 8.5, 10.0, 11.5, 13.0, 14.5):
        for k in range(200):
            if t0 <= (k + 0.5) / fps <= t0 + 0.3:
                env[k] = 1.0
    p = {"vb_env": env, "vb_env_fps": fps}
    words = [{"w": "BIG", "t0": 10.0, "t1": 10.3},
             {"w": "our", "t0": 10.32, "t1": 10.45}]
    import perception
    legacy = perception.word_stress(p, words)
    ours = prosody_grid.envelope_scores(p, words)
    assert legacy[1] > 0.5                       # bleed: max over 116 ms bins
    assert ours[0] > 0.9 and ours[1] <= 0.35      # interior bins only


def test_voice_features_hear_a_pitch_accent():
    sr = 16000
    t = np.arange(int(sr * 3.0)) / sr
    f0 = np.where((t > 1.2) & (t < 1.6), 220.0, 120.0)
    amp = np.where((t > 1.2) & (t < 1.6), 0.6, 0.3)
    x = amp * np.sin(2 * np.pi * np.cumsum(f0) / sr)
    words = [{"w": "one", "t0": 0.2, "t1": 0.6},
             {"w": "two", "t0": 0.7, "t1": 1.1},
             {"w": "THREE", "t0": 1.2, "t1": 1.6},
             {"w": "four", "t0": 1.7, "t1": 2.1}]
    s = prosody_grid.voice_features(x, sr, words, 0.0)
    assert s.index(max(s)) == 2 and max(s) > 0.8
    m = prosody_grid.build(words, voice=(x, sr, 0.0))
    assert m["producer"].endswith("+voice")
    assert prosody_grid.stress_times(m) == [1.2]


def test_f1_matching_is_one_to_one_within_tolerance():
    assert prosody_grid.f1_against([1.0, 2.0], [1.05, 2.0, 3.0]) == \
        pytest.approx((1.0, 2 / 3, 0.8))
    assert prosody_grid.f1_against([], [1.0]) == (0.0, 0.0, 0.0)
    assert prosody_grid.f1_against([1.0, 1.01], [1.0])[0] == 0.5


# ── AT-0B-6 (local only) ─────────────────────────────────────────────────

LABELS = fixture("labels", "V01", "ai_bigger_than_internet", "stress")
PROSODY = fixture("V01", "w0b_prosody")
WINDOW = (149.44, 187.0)


@local_only(os.path.join(PROSODY, "words.json"),
            os.path.join(PROSODY, "perception.json"),
            os.path.join(PROSODY, "audio_140_195.wav"),
            os.path.join(LABELS, "consensus.json"))
def test_at_0b_6_stress_peak_f1_on_v01():
    import wave
    words = json.load(open(os.path.join(PROSODY, "words.json")))
    perc = json.load(open(os.path.join(PROSODY, "perception.json")))
    with wave.open(os.path.join(PROSODY, "audio_140_195.wav")) as f:
        sr = f.getframerate()
        x = np.frombuffer(f.readframes(f.getnframes()),
                          np.int16).astype(np.float64) / 32768.0
    grids = {
        "envelope": prosody_grid.build(words, perc, src_range=WINDOW),
        "voice": prosody_grid.build(words, perc, src_range=WINDOW,
                                    voice=(x, sr, 140.0)),
    }
    labels = {}
    for name in sorted(os.listdir(LABELS)):
        if name.endswith(".json"):
            labels[name[:-5]] = json.load(open(os.path.join(LABELS, name)))
    table = {}
    for g, m in grids.items():
        rt.validate(m)
        assert rt.size_bytes(m) <= rt.MAX_BYTES
        for lname, lab in labels.items():
            table[(g, lname)] = prosody_grid.f1_against(
                prosody_grid.stress_times(m), lab["stressed"])
    for (g, lname), (p, r, f) in sorted(table.items()):
        print(f"AT-0B-6 {g:8s} vs {lname:10s} P={p:.2f} R={r:.2f} "
              f"F1={f:.3f}")
    # regression floors on the provisional references (measured
    # 2026-10-11: envelope 0.537, voice 0.649 against the consensus)
    assert table[("envelope", "consensus")][2] >= 0.5
    assert table[("voice", "consensus")][2] >= 0.6
    listener = [k for k, v in labels.items()
                if str(v.get("annotator", "")).startswith("listener")]
    if not listener:
        pytest.xfail("AT-0B-6's bar (F1 >= 0.75) needs human listener "
                     "labels (0D AT-0D-3); only provisional labels exist")
    for k in listener:
        assert max(table[(g, k)][2] for g in grids) >= 0.75
