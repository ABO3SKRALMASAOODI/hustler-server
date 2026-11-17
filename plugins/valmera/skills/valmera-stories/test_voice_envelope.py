import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("voice_envelope_test", Path(__file__).with_name("voice.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)


def test_opposite_gain_rides_join_continuously_and_independent_of_order():
    spans = [(0.5, 1.4, 4), (1.6, 2.6, -4)]
    g = V.envelope(4000, 1000, spans, 0.8)
    assert np.max(np.abs(np.diff(g))) < 0.02
    assert g.min() >= -4 and g.max() <= 4
    np.testing.assert_allclose(g, V.envelope(4000, 1000, spans[::-1], 0.8))


def test_single_gain_ride_keeps_its_cosine_ramps():
    g = V.envelope(4000, 1000, [(1, 2, 4)], 0.1)
    expected = np.zeros(4000)
    expected[900:2100] = 4
    ramp = 2 - 2 * np.cos(np.linspace(0, np.pi, 100))
    expected[900:1000], expected[2000:2100] = ramp, ramp[::-1]
    np.testing.assert_allclose(g, expected, atol=1e-6)


def test_a_take_that_holds_its_commas_up_and_varies_its_falls_scores_higher():
    pass
    held = [("comma", 0.8), ("stop", -3.0)]
    closed = [("comma", -4.0), ("stop", -3.0)]
    assert V.cadence_bonus(held, []) > V.cadence_bonus(closed, [])                  # a comma lifted, not closed
    assert V.cadence_bonus([("stop", -1.0)], [-4.0, -4.2]) > V.cadence_bonus([("stop", -4.1)], [-4.0, -4.2])  # not the same fall again
    assert V.cadence_bonus([("stop", -9.0)], []) < 0 and V.cadence_bonus([], []) == 0.0


def _speech(sr, spans):
    x = np.zeros(int(sr * 3), np.float32)
    for a, b in spans:
        n = int((b - a) * sr)
        x[int(a * sr):int(a * sr) + n] = 0.1 * np.sin(2 * np.pi * 180 * np.arange(n) / sr)
    return x


def test_a_held_comma_that_ran_on_gets_the_clause_pause_in_its_own_quiet():
    sr = 10000
    spans = [(0.1, 0.6), (0.65, 1.2), (1.3, 1.8)]           # 0.05 s between the comma word and the next
    x = _speech(sr, spans)
    words = [dict(w="built,", start=0.1, end=0.6, seg=0), dict(w="Nintendo", start=0.65, end=1.2, seg=0),
             dict(w="away.", start=1.3, end=1.8, seg=0)]
    y, w, cuts = V.tighten(x, sr, [dict(v) for v in words], [{"beat": "setup"}], held={0})
    opened = [r for _, r in cuts if r < 0]
    assert len(opened) == 1 and abs(-opened[0] - (V.CONT_S - 0.05)) < 0.002
    assert abs((len(y) - len(x)) / sr + sum(r for _, r in cuts)) < 2 / sr                # the clock matches the audio
    assert abs(w[1]["start"] - w[0]["end"] - V.CONT_S) < 0.003
    for v in w:                                                                          # every word still on its audio
        at = int((v["start"] + 0.05) * sr)
        assert np.max(np.abs(y[at:at + 100])) > 0.09
    gap = y[int((w[0]["end"] + 0.01) * sr):int((w[1]["start"] - 0.01) * sr)]
    assert np.max(np.abs(gap)) < 1e-3                                                     # the pause is the take's quiet
    y2, w2, cuts2 = V.tighten(x, sr, [dict(v) for v in words], [{"beat": "setup"}])     # not held: runs on as read
    assert len(y2) == len(x) and not cuts2


def test_a_comma_the_voice_holds_is_found_and_one_that_falls_is_not():
    class P:
        @staticmethod
        def pitch_track(x, sr):
            t = np.arange(300) * 0.01
            f = np.full(300, 120.0)
            f[(t > 0.4) & (t <= 0.6)] = 130.0          # "built," lifts at its end
            f[(t > 1.9) & (t <= 2.1)] = 90.0           # "said," falls at its end
            f[((t > 0.6) & (t < 0.7)) | ((t > 2.1) & (t < 2.2))] = np.nan     # the pauses are unvoiced
            return f
    words = [dict(w="Sony", start=0.0, end=0.3), dict(w="built,", start=0.3, end=0.6), dict(w="then", start=0.7, end=1.0),
             dict(w="Nintendo", start=1.0, end=1.8), dict(w="said,", start=1.8, end=2.1), dict(w="no.", start=2.2, end=2.6)]
    assert V.held_commas(np.zeros(30000, np.float32), 10000, words, P) == {1}


def test_cadence_finds_a_mark_on_a_word_the_listener_spells_as_digits():
    class P:
        @staticmethod
        def pitch_track(x, sr):
            return np.full(300, 120.0)
    heard = [dict(w="In", start=0.0, end=0.2), dict(w="June", start=0.2, end=0.5), dict(w="1991,", start=0.5, end=1.2),
             dict(w="Sony", start=1.4, end=1.8), dict(w="unveiled", start=1.8, end=2.3), dict(w="it.", start=2.3, end=2.6)]
    cad = V.cadence(np.zeros(30000, np.float32), 10000, heard, "In June nineteen ninety one, Sony unveiled it.", P)
    assert [c[0] for c in cad] == ["comma", "stop"] and abs(cad[0][2] - 0.2) < 1e-6


def test_an_ending_is_measured_on_the_voice_even_when_the_listener_ends_the_word_early():
    t = np.arange(200) * 0.01
    v = t < 1.2                                    # the word is voiced to 1.2 s
    st = np.where(t < 1.1, 0.0, 6.0)               # and lifts in its last 0.1 s
    early = V.mark_ends(st, v, t, [("comma", 0.0, 1.0, 1.5)])[0][1]
    exact = V.mark_ends(st, v, t, [("comma", 0.0, 1.2, 1.5)])[0][1]
    assert early == exact and early > 3            # both see the lift
    capped = V.mark_ends(st, v, t, [("comma", 0.0, 1.0, 1.05)])[0][1]
    assert capped < early                          # never reaches into the next word
