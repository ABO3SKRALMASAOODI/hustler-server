"""Track 2C: rhythm, syllables, lyrics and deterministic listening.

  * 2C-1 on a synthetic trap-like track (CI) and the owner's music fixture
    (local): tempo within 1%, beats F >= 0.90, downbeats F >= 0.80 (±70 ms),
    the drop within a beat, the pre-drop gap's edges within 30 ms, kicks
    found where they are;
  * 2C-5: with no track the user supplied, analyze_music refuses and offers
    the speech clock (never chooses music);
  * syllables split like speech ("de-cades", "a-ny-thing") and land on the
    sound; lyrics align to recognised words; the cache keys by audio sha;
  * defects 25-27: untranscribed sound is reported, a cut's fragment is cut
    away, a short never opens mid-question.
"""

import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import audio_checks                                       # noqa: E402
import lyrics_align                                       # noqa: E402
import rhythm                                             # noqa: E402
import syllables                                          # noqa: E402
from localfix import fixture, local_only                  # noqa: E402
from rhythm import dsp                                    # noqa: E402

SR = dsp.SR


# ── a synthetic track with known structure ───────────────────────────────

def _kick(n):
    t = np.arange(n) / SR
    f = 50.0 + 90.0 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 18)


def _snare(n, rng):
    t = np.arange(n) / SR
    return (rng.standard_normal(n) * 0.6 + np.sin(2 * np.pi * 190 * t)) * \
        np.exp(-t * 28)


def _hat(n, rng):
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    for _ in range(3):                                # bright: mostly > 6 kHz
        x = np.diff(np.concatenate([[0.0], x]))
    return x * np.exp(-t * 90) * 0.25


def synth(bpm=120.0, bars=16, intro_bars=4, gap_s=0.3, seed=1):
    """(samples, truth): hats from the start, kick/snare/bass from bar
    ``intro_bars`` (the drop) after a ``gap_s`` silence; chords change on
    every bar line."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    lead = 0.5
    n = int((lead + bars * 4 * beat + 1.0) * SR)
    y = np.zeros(n)
    beats, downs, kicks = [], [], []
    chords = [220.0, 261.6, 196.0, 246.9]
    drop_t = lead + intro_bars * 4 * beat
    for b in range(bars * 4):
        t = lead + b * beat
        beats.append(round(t, 4))
        bar, pos = divmod(b, 4)
        if pos == 0:
            downs.append(round(t, 4))
        i = int(t * SR)
        k = int(0.4 * SR)
        in_gap = drop_t - gap_s <= t < drop_t
        for half in (0.0, 0.5):
            j = int((t + half * beat) * SR)
            if not in_gap and not (drop_t - gap_s <= t + half * beat < drop_t):
                y[j:j + 2000] += _hat(min(2000, n - j), rng)[:max(0, min(2000, n - j))]
        if t >= drop_t:
            if pos in (0, 2):
                y[i:i + k] += 0.9 * _kick(min(k, n - i))
                kicks.append(round(t, 4))
            if pos in (1, 3):
                y[i:i + k] += 0.5 * _snare(min(k, n - i), rng)
        # a chord pad that changes on the bar line (quiet before the drop)
        if not in_gap:
            f0 = chords[bar % 4]
            seg = np.arange(int(beat * SR)) / SR
            pad = sum(np.sin(2 * np.pi * f0 * m * seg) / m for m in (1, 1.25,
                                                                      1.5))
            amp = 0.06 if t < drop_t else 0.12
            y[i:i + len(seg)] += amp * pad[:max(0, min(len(seg), n - i))]
            if t >= drop_t and pos == 0:
                # one legato bass note per bar, from the downbeat
                bseg = np.arange(int(4 * beat * SR)) / SR
                bass = np.sin(2 * np.pi * (f0 / 4) * bseg) * 0.35
                m = max(0, min(len(bseg), n - i))
                y[i:i + m] += bass[:m]
    y = (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)
    # silence the gap completely before the drop
    y[int((drop_t - gap_s) * SR):int(drop_t * SR)] *= 0.001
    return y, {"bpm": bpm, "beats": beats, "downbeats": downs,
               "drop": drop_t, "gap": (drop_t - gap_s, drop_t),
               "kicks": kicks}


@pytest.fixture(scope="module")
def synth_map():
    y, truth = synth()
    return rhythm.analyze_samples(y), truth


def test_tempo_beats_and_bars_on_a_known_track(synth_map):
    r, truth = synth_map
    assert abs(r["bpm"] - truth["bpm"]) / truth["bpm"] <= 0.01, r["bpm"]
    f, _p, _r = rhythm.f_measure(r["beats"], truth["beats"])
    assert f >= 0.90, f
    fd, _p, _r = rhythm.f_measure(r["downbeats"], truth["downbeats"])
    assert fd >= 0.80, (fd, r["downbeats"][:6], truth["downbeats"][:6])


def test_the_drop_and_the_gap_before_it(synth_map):
    r, truth = synth_map
    beat = 60.0 / truth["bpm"]
    assert any(abs(d - truth["drop"]) <= beat for d in r["drops"]), \
        (r["drops"], truth["drop"])
    g0, g1 = truth["gap"]
    assert any(abs(g["t0"] - g0) <= 0.03 and abs(g["t1"] - g1) <= 0.03
               for g in r["gaps"]), (r["gaps"], truth["gap"])


def test_kicks_are_found_where_they_are(synth_map):
    r, truth = synth_map
    kicks = [o["t"] for o in r["onsets"] if o["cls"] == "kick"]
    assert kicks
    _f, p, rec = rhythm.f_measure(kicks, truth["kicks"], tol=0.05)
    assert p >= 0.85 and rec >= 0.8, (p, rec)


def test_the_map_is_a_valid_compact_rhythm_map(synth_map):
    import rhythm_types
    r, _t = synth_map
    rhythm_types.validate(rhythm_types.compact(r))
    assert rhythm_types.size_bytes(rhythm_types.compact(r)) <= 60_000
    assert r["producer"] == rhythm.PRODUCER and r["source"] == "music"


def test_the_map_caches_by_audio_sha(tmp_path, monkeypatch):
    import sidecars
    monkeypatch.setenv("VALMERA_SIDECAR_DIR", str(tmp_path))
    sidecars._reset_caches()
    y, _t = synth(bars=8, intro_bars=2)
    wav = tmp_path / "t.wav"
    import subprocess
    raw = tmp_path / "t.f32"
    y.tofile(raw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar",
                    str(SR), "-ac", "1", "-i", str(raw), str(wav)],
                   check=True)
    calls = []
    real = rhythm.analyze

    def counting(path, **k):
        calls.append(path)
        return real(path, **k)
    monkeypatch.setattr(rhythm, "analyze", counting)
    a = rhythm.map_for(str(wav), "ab" * 32, 9.0)
    b = rhythm.map_for(str(wav), "ab" * 32, 9.0)
    assert a["bpm"] == b["bpm"] and len(calls) == 1


@local_only(fixture("music", "drake-national-treasures",
                    "national-treasures.48k.f32.wav"))
def test_the_owners_track_meets_2c_1():
    base = fixture("music", "drake-national-treasures")
    a = json.load(open(os.path.join(base, "analysis.json")))
    import time
    t = time.monotonic()
    r = rhythm.analyze(os.path.join(base, "national-treasures.48k.f32.wav"))
    assert time.monotonic() - t <= 20.0
    assert abs(r["bpm"] - a["tempo"]["bpm"]) / a["tempo"]["bpm"] <= 0.01
    assert rhythm.f_measure(r["beats"], [b["t"] for b in a["beats"]])[0] >= 0.90
    assert rhythm.f_measure(r["downbeats"], a["downbeats_s"])[0] >= 0.80
    beat = a["tempo"]["beat_period_s"]
    for d in a["drops"][:3]:                       # D1, D2, D3
        assert any(abs(x - d["t_grid_s"]) <= beat for x in r["drops"]), d
    g1 = a["gaps"][0]
    assert any(abs(g["t0"] - g1["start_s"]) <= 0.03 and
               abs(g["t1"] - g1["end_s"]) <= 0.03 for g in r["gaps"])


# ── 2C-5: no track, no beat ──────────────────────────────────────────────

def test_without_a_user_track_the_rhythm_plan_is_refused():
    from tools_ext import rhythm_tools as rt
    from w2_support import Ctx, edl
    out = rt.analyze_music(Ctx(edl()))
    assert out.startswith("REJECTED") and "prosody grid" in out
    assert "never chosen" in out
    legacy = edl(music=[{"id": "m1", "storage_key": "media/p/x.mp3",
                         "start": 0.0, "end": 10.0}])
    assert rt.analyze_music(Ctx(legacy)).startswith("REJECTED")


# ── syllables and lyrics ─────────────────────────────────────────────────

def test_words_split_like_speech():
    assert syllables.split_word("decades") == ["de", "cades"]
    assert syllables.split_word("anything") == ["a", "ny", "thing"]
    assert syllables.split_word("trying") == ["try", "ing"]
    assert syllables.split_word("okay") == ["o", "kay"]
    assert syllables.split_word("internet") == ["in", "ter", "net"]
    assert len(syllables.split_word("civilization")) == 5
    assert syllables.split_word("generated")[-1] == "ed"


def test_syllable_onsets_land_on_their_sound():
    sr = 16000
    y = np.zeros(int(1.2 * sr), np.float32)
    rng = np.random.default_rng(3)
    for a, b, f in ((0.20, 0.42, 420.0), (0.55, 0.80, 610.0)):
        i0, i1 = int(a * sr), int(b * sr)
        t = np.arange(i1 - i0) / sr
        env = np.minimum(1.0, t / 0.02) * np.minimum(1.0, (b - a - t) / 0.03)
        y[i0:i1] += (0.4 * np.sin(2 * np.pi * f * t) * env
                     ).astype(np.float32)
    y += (0.002 * rng.standard_normal(len(y))).astype(np.float32)
    syl = syllables.word_syllables(y, sr, 0.18, 0.82, "trying")
    assert [s["text"] for s in syl] == ["try", "ing"]
    assert abs(syl[0]["t0"] - 0.20) <= 0.04 and abs(syl[1]["t0"] - 0.55) <= 0.04


def test_lyrics_align_to_a_misheard_vocal():
    lyrics = "you should know I\nbeen training all season"
    truth = [0.5, 0.8, 1.1, 1.4, 2.0, 2.3, 2.9, 3.2]
    asr = [{"w": "you", "t0": 0.52, "t1": 0.7}, {"w": "should", "t0": 0.81,
                                                    "t1": 1.0},
           {"w": "no", "t0": 1.12, "t1": 1.3}, {"w": "been", "t0": 2.01,
                                                  "t1": 2.2},
           {"w": "trainin", "t0": 2.31, "t1": 2.8}, {"w": "uh", "t0": 2.82,
                                                      "t1": 2.88},
           {"w": "all", "t0": 2.9, "t1": 3.1}, {"w": "seasons", "t0": 3.21,
                                                  "t1": 3.6}]
    rows = lyrics_align.align(lyrics, asr)
    assert [r["w"] for r in rows] == lyrics.split()
    assert rows[3]["estimated"] and rows[4]["brk"] if "brk" in rows[4] else True
    assert lyrics_align.median_error(rows, truth) <= 0.06


# ── defects 25-27 ────────────────────────────────────────────────────────

def _speech_like(spans, dur=3.0, sr=16000, seed=0):
    rng = np.random.default_rng(seed)
    y = (0.001 * rng.standard_normal(int(dur * sr))).astype(np.float32)
    for a, b, f in spans:
        i0, i1 = int(a * sr), int(b * sr)
        t = np.arange(i1 - i0) / sr
        y[i0:i1] += (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return y


def test_listening_reports_an_untranscribed_um():
    y = _speech_like([(0.3, 0.7, 300), (1.0, 1.3, 180), (1.6, 2.2, 260)])
    words = [{"w": "so", "t0": 0.3, "t1": 0.7}, {"w": "anyway", "t0": 1.6,
                                                  "t1": 2.2}]
    rep = audio_checks.listen(y, 16000, words, 0.0, 3.0)
    assert all(r["audible"] for r in rep["words"])
    um = [u for u in rep["unexplained"] if 0.95 <= u["t0"] <= 1.05]
    assert um and "filler" in um[0]["guess"]


def test_a_cut_leaves_no_fragment_of_the_word_it_cut():
    sr = 16000
    # the kept span starts at 1.0 but the cut word's tail sounds to 1.12
    y = _speech_like([(0.6, 1.12, 240), (1.4, 2.0, 300)])
    import cut_audio
    level = cut_audio.level_reader({(0.0, 3.0): y})
    words = [{"w": "really", "t0": 0.6, "t1": 0.98},
             {"w": "matters", "t0": 1.4, "t1": 2.0}]
    keep, moves = audio_checks.trim_fragments([[1.0, 2.2]], words, level)
    assert moves and moves[0]["side"] == "start"
    assert 1.12 <= keep[0][0] < 1.4, keep


def test_a_short_never_opens_mid_question():
    index = {"sentences": [
        {"t0": 1360.2, "t1": 1363.0, "text": "That was the plan."},
        {"t0": 1363.44, "t1": 1366.8, "text": "So what are the odds that it works?"},
        {"t0": 1367.1, "t1": 1372.0, "text": "Less than ten percent."}]}
    s, why = audio_checks.sentence_start(index, 1364.01)
    assert s == 1363.44 and "question" in why
    assert audio_checks.sentence_start(index, 1363.44)[1] is None
    e, why = audio_checks.sentence_end(index, 1370.5)
    assert e == 1372.0 and why
