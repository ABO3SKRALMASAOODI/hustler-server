"""A short payoff must retain its music gesture even when no phrase join fits."""
import os
import sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from score import music


def test_late_payoff_has_a_measurable_hole_and_return_without_recutting_bars():
    plan = dict(drop_at=None, end_hit_s=28.55, beat_s=.55, spans=[], build_at=None)
    marks = dict(words=[(0, 28)], payoff_s=25.633, accents=[])
    slow, fast, notes = music.flow(2917, .01, marks, plan, music.PROFILE, 0, 0)
    curve = slow+fast
    p = int(marks['payoff_s']/.01)
    assert curve[p+2]-np.min(curve[p-50:p]) > 12
    assert notes['payoff_flow_s'] == marks['payoff_s']
    assert any(abs(h['t']-marks['payoff_s']) < .01 for h in notes['hits'])
    assert plan['drop_at'] is None


def test_no_invented_payoff_when_none_is_given_or_it_lands_outside_the_music():
    plan = dict(drop_at=None, end_hit_s=28.55, beat_s=.55, spans=[], build_at=None)
    for p in (None, -1, 29):
        _, _, notes = music.flow(3000,.01,dict(words=[(0,28)],payoff_s=p),plan,music.PROFILE,0,0)
        assert 'payoff_flow_s' not in notes
        assert not any(h.get('before')=='drop' for h in notes['holes'])
