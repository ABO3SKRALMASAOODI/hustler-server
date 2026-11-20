"""The bed held under every word on the story's own arc (music-mix lane, 20 Nov 2026).

The ten Cuban shorts climbed one template: the song's quiet half 8-13 dB under the voice, its loud last third 1.5-3.4
dB under, the bed over the voice on 4-23% of voiced 400 ms frames. These read the engine on signals alone: K levels
per 10 ms frame for the ride itself, a synthetic song and a synthetic voice for track(), a fake model for the reading."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import music_film as MF  # noqa: E402
from score import music as MU  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
HOP = 0.01


def _scene(dur=40.0, climb_db=12.0, quiet_every=7):
    """K levels of a film: words of 0.4 s every 0.5 s at the speech level (every quiet_every-th 9 dB quieter), and a
    song that climbs climb_db from its first second to its last (the template the Cuban shorts played)."""
    nf = int(dur / HOP)
    t = np.arange(nf) * HOP
    words, vk = [], np.full(nf, -120.0)
    k, w0 = 0, 0.3
    while w0 + 0.4 < dur - 3.0:
        words.append((w0, w0 + 0.4))
        lvl = -23.0 - (9.0 if quiet_every and k % quiet_every == quiet_every - 1 else 0.0)
        i0 = int(round(w0 / HOP))
        vk[i0:i0 + 40] = lvl + 1.5 * np.sin(np.arange(40) / 3.0)
        w0 += 0.5
        k += 1
    mk = -32.0 + climb_db * t / dur + 2.0 * np.sin(2 * np.pi * t * 2.0)          # a beat on top of the climb
    spoken = np.zeros(nf, bool)
    for a, b in words:
        spoken[int(round(a / HOP)):int(round(a / HOP)) + 40] = True
    return t, mk, vk, spoken, words


def _hold(marks, profile=None, **kw):
    t, mk, vk, spoken, words = _scene(**kw)
    P = dict(MU.profile_for("podcast"), **(profile or {}))
    g = np.full(len(t), 4.0)                                       # one global level: the start about 5 dB under the voice
    marks = dict({"dur_s": len(t) * HOP, "words": words}, **marks)
    g2, notes = MU.hold_under_words(t, mk, g, vk, np.ones(len(t), bool), spoken, words, marks, {}, P, -23.0)
    return t, mk, vk, words, g, g2, notes, P


def _gaps(vk, m, words, a=0.0, b=1e9, speech_only=True):
    rows = MU.word_gaps(vk, m, [w for w in words if a <= w[0] < b])
    return np.array([r[3] for r in rows if not speech_only or r[1] > -27.0])


def test_the_customer_directors_bed_is_unchanged():
    assert "hold" not in MU.PROFILE                                # bake_bed rides on PROFILE: the customer's bed as before
    assert MU.profile_for("podcast")["hold"] is True
    for kind in ("stories", "marketing", "longform"):
        assert not MU.profile_for(kind).get("hold")


def test_a_song_that_climbs_is_held_level_under_the_words():
    marks = {"turn_s": 18.0, "payoff_s": 30.0}
    t, mk, vk, words, g, g2, notes, P = _hold(marks)
    before = [float(np.median(_gaps(vk, mk + g, words, a, a + 12.3))) for a in (0.0, 12.3, 24.6)]
    after = [float(np.median(_gaps(vk, mk + g2, words, a, a + 12.3))) for a in (0.0, 12.3, 24.6)]
    assert before[0] - before[2] > 6.0 and before[2] < 0.0         # the template: the last third over the voice
    assert abs(after[0] - after[2]) < 3.5                          # held: the arc's parts, not the song's climb
    every = _gaps(vk, mk + g2, words)
    assert every.min() >= P["hold_floor_db"] - 0.6                 # no spoken word has the bed within the floor of it
    assert 4.0 <= float(np.median(every)) <= 7.0                   # about 5 dB under the voice over the film
    assert notes["by"] == "default" and notes["arc"] == MU.ARC_DEFAULT


def test_no_400ms_window_of_speech_has_the_bed_over_the_voice():
    t, mk, vk, words, g, g2, notes, P = _hold({"turn_s": 18.0, "payoff_s": 30.0}, climb_db=18.0)
    w4 = 40
    v4 = MU._pmean_db(vk, w4)
    m4 = MU._pmean_db(mk + g2, w4)
    on = np.zeros(len(t), bool)
    for a, b in words:
        on[int(a / HOP):int(b / HOP)] = True
    p90 = float(np.percentile(v4[v4 > -70], 90))
    speech = on & (v4 > p90 - P["hold_deep_db"])
    assert float(np.mean(m4[speech] > v4[speech])) <= 0.005
    assert notes["voiced_over"] <= 0.005 and notes["words_over"] <= 0.03


def test_the_voice_itself_says_where_it_speaks_not_only_the_stamps():
    """A stretch the word stamps miss (a word's tail, a word whisper never wrote) is still voice: the bed is held
    under it as under a stamped word."""
    t, mk, vk, spoken, words = _scene(climb_db=18.0, quiet_every=0)
    missed = [w for w in words if 26.0 <= w[0] < 30.0]
    stamped = [w for w in words if w not in missed]
    P = MU.profile_for("podcast")
    g = np.full(len(t), 4.0)
    sp = np.zeros(len(t), bool)
    for a, b in stamped:
        sp[int(round(a / HOP)):int(round(a / HOP)) + 40] = True
    marks = {"dur_s": len(t) * HOP, "words": stamped, "turn_s": 18.0, "payoff_s": 30.0}
    g2, notes = MU.hold_under_words(t, mk, g, vk, np.ones(len(t), bool), sp, stamped, marks, {}, P, -23.0)
    v4, m4 = MU._pmean_db(vk, 40), MU._pmean_db(mk + g2, 40)
    sel = (t >= 26.0) & (t < 29.5) & (v4 > -35.0)
    assert sel.sum() > 100 and float(np.mean(m4[sel] > v4[sel])) <= 0.01
    assert float(np.median((v4 - m4)[sel])) >= P["hold_floor_db"]


def test_the_floor_reads_the_phrase_so_the_bed_does_not_follow_each_word():
    """Words 4 dB apart inside one phrase: the bed holds one level under the phrase, it does not dip on the quieter
    words and rise on the louder ones (a pump). Read word by word, each quiet word would have pulled it 2 dB under."""
    t, mk, vk, spoken, words = _scene(climb_db=0.0, quiet_every=0)
    for k, (a, b) in enumerate(words):
        i0 = int(round(a / HOP))
        vk[i0:i0 + 40] += (-4.0 if k % 2 else 0.0)
    mk = np.full(len(t), -29.0)                                     # a level song, a little closer than its depth
    P = MU.profile_for("podcast")
    marks = {"dur_s": len(t) * HOP, "words": words, "turn_s": 18.0, "payoff_s": 30.0,
             "story_arc": {"setup": "mid", "build": "mid", "payoff": "mid", "close": "mid"}}
    g2, notes = MU.hold_under_words(t, mk, np.zeros(len(t)), vk, np.ones(len(t), bool), spoken, words, marks, {}, P, -23.0)
    lv = [float(np.mean(g2[int(a / HOP):int(b / HOP)])) for a, b in words[10:-10]]
    loud, quiet = np.mean(lv[0::2]), np.mean(lv[1::2])
    assert abs(loud - quiet) < 0.5                                 # the same bed under the loud word and the quiet one
    assert float(np.std(lv)) < 1.0


def test_each_part_sits_at_the_depth_its_story_chose():
    quiet, _ = _part_depths({"setup": "low", "build": "mid", "payoff": "high", "close": "mid"})
    loud, _ = _part_depths({"setup": "high", "build": "mid", "payoff": "low", "close": "mid"})
    assert quiet["setup"] - loud["setup"] > 1.8                    # a dry setup told straight sits further under
    assert loud["payoff"] - quiet["payoff"] > 2.5                  # a payoff the story keeps quiet stays quiet
    assert quiet["setup"] > quiet["payoff"]                        # and the story's own arc rises into its payoff


def _part_depths(arc, close_s=None):
    marks = {"turn_s": 18.0, "payoff_s": 30.0}
    if arc:
        marks["story_arc"] = arc
    if close_s is not None:
        marks["close_s"] = close_s
    t, mk, vk, words, g, g2, notes, P = _hold(marks, quiet_every=0)
    assert notes["by"] == ("story" if arc else "default") and notes["arc"] == (arc or MU.ARC_DEFAULT)
    out = {}
    for name, a, b in [("setup", 0.0, 18.0), ("payoff", 30.0, close_s or 40.0), ("close", close_s or 40.0, 40.0)]:
        gp = _gaps(vk, mk + g2, words, a, b)
        out[name] = float(np.median(gp)) if len(gp) else None
    return out, notes


def test_the_read_arc_is_heard_against_the_default():
    """The model's arc moves the bed, not one template: a low setup sits at least 2 dB further under the voice than
    the default's mid one, a low close at least 2 dB further under than a mid close (no re-centring cancels it)."""
    low = {"setup": "low", "build": "mid", "payoff": "high", "close": "low"}
    got, _ = _part_depths(low, close_s=35.0)
    base, _ = _part_depths(None, close_s=35.0)
    assert got["setup"] - base["setup"] >= 2.0
    assert got["close"] - base["close"] >= 2.0
    assert abs(got["payoff"] - base["payoff"]) < 1.0                # where the arcs agree, the bed agrees


def test_a_quiet_close_stays_quiet_after_the_last_word():
    def tail_level(close):
        marks = {"turn_s": 18.0, "payoff_s": 30.0, "story_arc": {"setup": "mid", "build": "mid", "payoff": "mid", "close": close}}
        t, mk, vk, words, g, g2, notes, P = _hold(marks, climb_db=16.0)
        last = max(b for _a, b in words)
        m4 = MU._pmean_db(mk + g2, 40)
        return float(np.max(m4[t > last + 0.6]))
    quiet, rings = tail_level("low"), tail_level("mid")
    assert quiet <= -23.0 - (MU.UNDER_VOICE["podcast"] + MU.ARC_DB["low"]) + 0.5
    assert rings - quiet > 3.0                                     # a confident close lets the song ring out


def test_the_story_parts_come_from_its_marks():
    words = [(x, x + 0.3) for x in np.arange(0.5, 40.0, 0.5)]
    parts = MU.arc_parts({"turn_s": 12.0, "payoff_s": 30.0, "close_s": 35.0}, {}, words, 42.0)
    assert [p[0] for p in parts] == ["setup", "build", "payoff", "close"]
    assert parts[1][1] == 12.0 and parts[2][1] == 30.0 and parts[3][1] == 35.0 and parts[3][2] == pytest.approx(39.8)
    one = MU.arc_parts({"turn_s": 20.0, "payoff_s": None}, {}, words, 42.0)           # one mark: the story pays off there
    assert [p[0] for p in one] == ["setup", "build", "payoff"] and one[2][1] == 20.0
    none = MU.arc_parts({"turn_s": None, "payoff_s": None}, {}, words, 42.0)
    assert [p[0] for p in none] == ["setup"]
    bad = MU.arc_parts({"turn_s": 12.0, "payoff_s": 30.0, "close_s": 29.0}, {}, words, 42.0)   # a close before its payoff is none
    assert [p[0] for p in bad] == ["setup", "build", "payoff"]
    assert MU.story_arc({"story_arc": {"setup": "loud", "close": "low"}}) == dict(MU.ARC_DEFAULT, close="low")
    # no close read: the close is the voice's last phrase after the payoff (its last pause of a breath or more)
    gap = [(x, x + 0.3) for x in np.arange(0.5, 36.0, 0.5)] + [(36.6, 36.9), (37.0, 37.3), (37.4, 37.7), (37.8, 38.2)]
    tail = MU.arc_parts({"turn_s": 12.0, "payoff_s": 30.0}, {}, gap, 42.0)
    assert [p[0] for p in tail] == ["setup", "build", "payoff", "close"] and tail[3][1] == pytest.approx(36.6)
    assert MU.last_phrase(words, 30.0, 39.8) is None                 # one run of words: no phrase to call the close


# ----------------------------------------------------------------- the reading and its way to the mix

def test_the_reading_gives_the_storys_arc_and_close_and_drops_what_cannot_be():
    lines = [(0.0, "I was the fat kid."), (20.0, "Nobody picked me."), (38.0, "So yeah, I was not somebody who stood out.")]
    seen = []

    def model(system, user, schema):
        seen.append(user)
        assert set(schema["required"]) >= {"arc", "close_s"}
        return {"turn_s": 20.0, "payoff_s": 30.0, "close_s": 38.0, "mood": "gentle, nostalgic",
                "arc": {"setup": "low", "build": "mid", "payoff": "mid", "close": "low"}}
    got = MF.story_marks(lines, 46.0, model, brief="Gentle, nostalgic music building to quiet resolve.")
    assert got["arc"] == {"setup": "low", "build": "mid", "payoff": "mid", "close": "low"} and got["close_s"] == 38.0
    assert "quiet resolve" in seen[0]                              # the brief's own music line reaches the reading
    bad = MF.story_marks(lines, 46.0, lambda *a: {"turn_s": 20.0, "payoff_s": 30.0, "close_s": 25.0, "mood": "x",
                                                  "arc": {"setup": "loud", "build": "mid"}})
    assert bad["close_s"] is None and bad["arc"] == {"build": "mid"}


def test_the_score_hands_the_read_arc_to_the_mix(monkeypatch, tmp_path):
    got = {}
    monkeypatch.setattr(MF, "lines_of", lambda l1, index=None: [(0.0, "One."), (10.0, "Two."), (20.0, "Three.")])
    monkeypatch.setattr(MU, "marks_from_l1", lambda l1, index=None, turn_s=None, payoff_s=None: {
        "dur_s": 30.0, "words": [(0.5, 0.9), (10.0, 10.4)], "cuts": [], "landings": [], "accents": [], "turn_s": turn_s,
        "payoff_s": payoff_s, "end_hit_s": None, "quiet": []})
    monkeypatch.setattr(MF, "choose", lambda f, marks, cache, used=(), call=None, profile=None: (
        {"cid": "pb-1", "title": "Light", "arc": True, "entry": {"point": None}, "sung": False}, "/x.mp3", []))

    def mix(*a, **kw):
        got.update(kw)
        return {"plan": {"ok": True}}
    monkeypatch.setattr(MU, "mix", mix)

    def model(system, user, schema):
        if "arc" in schema.get("properties", {}):
            return {"turn_s": 10.0, "payoff_s": 20.0, "close_s": 25.0, "mood": "light, mischievous",
                    "arc": {"setup": "low", "build": "mid", "payoff": "high", "close": "low"}}
        return {"order": [1], "why": "x"}
    l1 = {"fps": 30, "dur_f": 900, "items": []}
    rep = MF.score(l1, str(tmp_path), film=None, out=None, kind="podcast", call=model, brief="b")
    assert got["story_arc"] == {"setup": "low", "build": "mid", "payoff": "high", "close": "low", "close_s": 25.0}
    assert rep["marks"]["arc"] == got["story_arc"] and rep["marks"]["mood"] == "light, mischievous"


def test_the_choice_reads_the_briefs_mood_first():
    assert "light, funny or mischievous" in MF.STORY_SYSTEM and "never an earnest, heroic" in MF.STORY_SYSTEM
    assert "mood the brief asks for first" in MF.RANK_SYSTEM
    film = {"kind": "podcast", "duration_s": 40.0, "turns": [], "pace": {}, "brief": "x" * 1200 + " The music should be light and playful."}
    assert "light and playful" in MF._story_film_text(film)       # the brief's last sentence is its music line


# ----------------------------------------------------------------- a real song under a voice

@pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")
def test_track_holds_a_real_song_under_a_voice(tmp_path):
    from rhythm import dsp
    from test_w2c_rhythm import synth
    y, _truth = synth(bpm=120.0, bars=40, intro_bars=10)                      # a quiet intro, then the full song: a climb
    raw = tmp_path / "s.f32"
    np.asarray(y, np.float32).tofile(raw)
    path = str(tmp_path / "s.wav")
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(dsp.SR), "-ac", "1", "-i", str(raw), "-ac", "2", "-ar", "48000", path], check=True)
    dur = 36.0
    rng = np.random.default_rng(3)
    n = int(dur * MU.SR)
    voice = np.zeros(n, np.float32)
    words, w0, k = [], 0.4, 0
    while w0 + 0.35 < dur - 2.5:
        a, b = int(w0 * MU.SR), int((w0 + 0.35) * MU.SR)
        amp = 0.06 if k % 6 else 0.025
        voice[a:b] = rng.standard_normal(b - a).astype(np.float32) * amp * np.hanning(b - a).astype(np.float32) ** 0.3
        words.append((w0, w0 + 0.35))
        w0 += 0.45
        k += 1
    marks = {"dur_s": dur, "words": words, "cuts": [], "turn_s": 14.0, "payoff_s": 24.0, "end_hit_s": None, "quiet": []}
    song = MU.analyse(path, sung=False)
    held_p = MU.profile_for("podcast")
    plan = MU.arrange(song, dict(marks, arc=True), profile=held_p)
    assert plan["ok"]
    stereo = np.stack([voice, voice], 1)
    music, rep = MU.track(path, song, plan, marks, voice=stereo, profile=held_p)
    plain, rep0 = MU.track(path, song, plan, marks, voice=stereo, profile=dict(held_p, hold=False))
    assert "hold" in rep and "hold" not in rep0 and rep["broadband_gain_db"] == 0.0
    vk = MU._k_frames(stereo, 480)

    def over(m):
        mk = MU._k_frames(m, 480)
        rows = MU.word_gaps(vk, mk, words)
        return sum(1 for r in rows if r[3] < 0 and r[1] > float(np.median([x[1] for x in rows])) - 10.0), rows
    held_over, rows = over(music)
    assert held_over == 0
    assert 3.5 <= float(np.median([r[3] for r in rows])) <= 8.0
