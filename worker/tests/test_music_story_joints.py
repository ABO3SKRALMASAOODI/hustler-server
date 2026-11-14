"""A narrated story's bed breathes at its joints (valmera-stories, 14 Nov 2026): only when the scene carries joints;
without them the level is exactly as before."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import music  # noqa: E402


def _plan():
    return dict(drop_at=None, end_hit_s=29.0, beat_s=.55, spans=[], build_at=None)


def test_without_joints_the_level_is_unchanged():
    marks = dict(words=[(0.5, 9.0), (10.0, 28.0)], payoff_s=None, accents=[4.0, 15.0])
    a = music.flow(3000, .01, dict(marks), _plan(), music.PROFILE, 0, 0)
    b = music.flow(3000, .01, dict(marks, joints=None), _plan(), music.PROFILE, 0, 0)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]) and a[2] == b[2]
    assert np.max(a[0]) > 0                      # the accent swell is still there for other films


def test_joints_cut_holes_that_reenter_on_the_word_and_accents_notch():
    joints = [dict(kind="turn", last_word_t=8.9, next_first_word_t=10.0), dict(kind="close", last_word_t=21.5, next_first_word_t=22.4),
              dict(kind="voice", t0=13.0, t1=14.5)]
    marks = dict(words=[(0.5, 8.9), (10.0, 21.5), (22.4, 28.0)], payoff_s=None, accents=[6.0], joints=joints)
    slow, fast, notes = music.flow(3000, .01, marks, _plan(), music.PROFILE, 0, 0)
    curve = slow + fast
    i = lambda s: int(s / .01)  # noqa: E731
    holes = [h for h in notes["holes"] if h["before"] in ("turn", "close")]
    assert len(holes) == 2 and all(0.5 <= h["len_s"] <= 2.3 and h["db"] >= 12 for h in holes)
    assert curve[i(9.5)] - curve[i(10.05)] <= -10                       # deep inside the hole, back on the word
    assert curve[i(16.0)] - curve[i(24.0)] >= 8                         # the close's bed stays down
    assert curve[i(14.0)] <= curve[i(12.0)] - 15                        # dipped under the real voice
    assert notes["notches"] == [6.0] and curve[i(5.95)] < curve[i(5.0)] - 9 and abs(curve[i(6.02)] - curve[i(5.0)]) < 0.5


def test_optional_joint_fields_set_the_close_bed_the_depth_and_a_swell():
    """Round 2: a joint may carry its own depth (db), a close its dry bed (bed_db), and a swell joint lifts the bed into
    a type page; joints without the fields are exactly the defaults."""
    i = lambda s: int(s / .01)  # noqa: E731
    base = [dict(kind="turn", last_word_t=8.9, next_first_word_t=10.0), dict(kind="close", last_word_t=21.5, next_first_word_t=22.4)]
    words = [(0.5, 8.9), (10.0, 21.5), (22.4, 28.0)]
    s0, f0, _ = music.flow(3000, .01, dict(words=words, payoff_s=None, accents=[], joints=base), _plan(), music.PROFILE, 0, 0)
    joints = [dict(base[0], db=22.0), dict(base[1], bed_db=20.0), dict(kind="swell", t=15.0, rise_s=2.5, db=10.0)]
    s1, f1, notes = music.flow(3000, .01, dict(words=words, payoff_s=None, accents=[], joints=joints), _plan(), music.PROFILE, 0, 0)
    c0, c1 = s0 + f0, s1 + f1
    assert c1[i(9.7)] <= c0[i(9.7)] - 5                                  # a deeper hole
    assert c1[i(16.0)] - c1[i(24.0)] >= 20 - 1e-6 or c1[i(23.0)] <= c0[i(23.0)] - 10    # the close's bed 20 dB down
    assert c1[i(15.0)] - c1[i(12.4)] >= 8                                 # the swell rises 8 dB and more into its mark
    assert notes["swells"] and notes["close_bed_db"] == -20.0
    s2, f2, n2 = music.flow(3000, .01, dict(words=words, payoff_s=None, accents=[], joints=[dict(j) for j in base]), _plan(), music.PROFILE, 0, 0)
    assert np.array_equal(s0, s2) and np.array_equal(f0, f2)
