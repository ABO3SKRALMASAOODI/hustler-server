"""The story path plays a song by its own arc (owner, 20 Nov 2026, on the Mark Cuban short: "literally the scene is
repeating just like something is glitching ... the music should really give you some sort of goosebumps").

The general arrangement fills a film from a song's fullest bars, so a song whose quiet opening and build sit before
its peak was played A-B-A-B from its loud middle, the payoff's "drop" being the opening's bars again. arrange(arc=True),
the workflows' story path only, plays one mostly contiguous run instead: the quieter part under the setup, the song's
own build under the turn, its peak landing on the payoff, never a bar twice. The default (the customer Director's bed)
is unchanged. Signal only: a synthetic bar map, no audio."""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import music_film as MF  # noqa: E402
from score import music as MU  # noqa: E402


def _song(levels, drops=(), bar_s=2.0, seed=3):
    """A bar map like analyse() writes: levels in dB per bar, a drop (an entrance) at the bars named, the bars of one
    part alike."""
    k = np.asarray(levels, np.float64)
    lo, hi = np.percentile(k, 10), np.percentile(k, 95)
    part = np.digitize(k, [-20.0, -14.0])
    rng = np.random.default_rng(seed)
    n = len(k)
    sim = 0.35 + 0.45 * (part[:, None] == part[None, :]) + 0.05 * rng.random((n, n))
    sim = (sim + sim.T) / 2
    np.fill_diagonal(sim, 1.0)
    bars = []
    for i, x in enumerate(k):
        bars.append({"i": i, "t0": i * bar_s, "t1": (i + 1) * bar_s, "k": float(x), "low": float(x) - 6.0, "hit": 6.0 if i in drops else 1.0,
                     "voc": 0.0, "e": float(np.clip((x - lo) / max(hi - lo, 1e-6), 0, 1)), "windup": False, "sec": None,
                     "sec_start": i in drops, "drop": i in drops})
    return {"dur_s": n * bar_s, "bpm": 120.0, "beat_s": bar_s / 4, "bar_s": bar_s, "steady": True, "bars": bars, "sim": sim.tolist(),
            "drops": [i * bar_s for i in drops], "gaps": [], "sections": []}


# a cue that opens quiet, has a louder first theme, builds and peaks at bar 34, then falls away
LEVELS = [-24.0] * 6 + [-19.0] * 10 + [-11.0] * 4 + [-17.0] * 6 + [-16.0, -15.0, -14.0, -14.0, -13.0, -13.0, -12.0, -12.0] \
    + [-9.0] * 12 + [-18.0] * 14
DROPS = (16, 34)


def _marks():
    words = [(t, t + 0.4) for t in np.arange(0.5, 49.7, 0.6)]
    return {"dur_s": 51.7, "words": words, "cuts": [], "landings": [], "accents": [], "turn_s": 20.8, "payoff_s": 45.1,
            "end_hit_s": None, "quiet": [], "turn_drop": True}


def test_the_story_path_plays_the_song_by_its_own_arc_into_the_payoff():
    song = _song(LEVELS, DROPS)
    plan = MU.arrange(song, dict(_marks(), arc=True), profile=MU.profile_for("podcast"))
    assert plan["ok"] and plan["arc"]["on"]
    seq = [b["bar"] for b in plan["bars_played"]]
    assert len(seq) == len(set(seq)), seq                                  # never a bar twice
    a = plan["arc"]
    assert a["repeats"] == 0 and a["rise_db"] >= 6.0 and a["peak_before_turn"] == 0, a
    drop = next(b for b in plan["bars_played"] if b["role"] == "drop")
    assert drop["bar"] == 34                                               # the song's own peak lands on the payoff
    assert a["joins"] <= 1 and MF.carries(plan, True, arc=True) < 2.0


def test_the_general_arrangement_is_unchanged_and_its_loop_is_counted_on_the_story_path():
    song = _song(LEVELS, DROPS)
    m = _marks()
    plan = MU.arrange(song, m, profile=MU.profile_for("podcast"))
    assert plan == MU.arrange(song, dict(m), profile=MU.profile_for("podcast"), arc=False)   # the default: no arc unless asked
    assert not plan["arc"]["on"]
    # measured the same way, whatever it played: a repeated bar or peak material before the turn is a story-path defect
    looped = dict(plan, arc={"repeats": 6, "peak_before_turn": 4, "rise_db": 0.5, "joins": 6})
    assert MF.carries(looped, True, arc=True) >= MF.carries(looped, True) + 9.0 + 4.0


def test_a_film_with_no_story_marks_rises_through_one_run():
    song = _song(LEVELS, DROPS)
    m = dict(_marks(), turn_s=None, payoff_s=None, turn_drop=False)
    plan = MU.arrange(song, dict(m, arc=True), profile=MU.profile_for("stories"))
    seq = [b["bar"] for b in plan["bars_played"]]
    assert plan["ok"] and plan["arc"]["on"] and seq == list(range(seq[0], seq[0] + len(seq)))
    assert plan["arc"]["rise_db"] > 0


def test_a_song_too_short_for_a_run_is_arranged_the_general_way():
    song = _song(LEVELS[:20], (16,))
    plan = MU.arrange(song, dict(_marks(), arc=True), profile=MU.profile_for("podcast"))
    assert plan["ok"] and not plan["arc"]["on"]
    assert MF.carries(plan, True, arc=True) > MF.carries(plan, True)       # its loop now counts against it in the audition


def test_story_arranges_a_continuous_phrase_when_equal_level_bars_have_rests():
    # A plucked attack can give an otherwise empty bar the same average as a sustained one.
    song = _song([-17.] * 60 + [-9.] * 10, (60,))
    marks = dict(_marks(), arc=True)
    original = MU.arrange(song, marks, profile=MU.profile_for("stories"))
    sparse = {b["bar"] for b in original["bars_played"][:4]}
    for i in sparse:
        song["bars"][i]["rest_s"] = .9
    arranged = MU.arrange(song, marks, profile=MU.profile_for("stories"))
    assert not sparse.intersection(b["bar"] for b in arranged["bars_played"])


def test_story_rides_a_soft_phrase_inside_a_playing_bar(monkeypatch):
    # The bar mean includes its loud attack. A half-second phrase between attacks fell out of the bed.
    sr = MU.SR
    m = dict(_marks(), dur_s=12., turn_s=None, payoff_s=None, words=[], turn_drop=False)
    song = _song(LEVELS, DROPS)
    plan = MU.arrange(song, m, profile=MU.profile_for("stories"), arc=True)
    t = np.arange(12 * sr) / sr
    mono = .1 * np.sin(2 * np.pi * 400 * t)
    mono[(t > 2.4) & (t < 2.95)] *= 10 ** (-13/20)
    signal = np.stack((mono, mono), axis=1).astype(np.float32)
    monkeypatch.setattr(MU, "_lay", lambda *args: (signal, round(plan["end_hit_s"] * sr)))
    wet, _ = MU.track("", song, plan, m, profile=MU.profile_for("stories"), song_pcm=signal)
    def power(a, b):
        return np.mean(wet[round(a*sr):round(b*sr)] ** 2)
    assert 0 < 10*np.log10(power(2.1, 2.3) / power(2.55, 2.77)) < 10
