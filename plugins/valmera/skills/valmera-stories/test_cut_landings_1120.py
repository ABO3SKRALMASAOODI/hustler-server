"""Round1 sound-events-missing (20 Nov 2026, second round): every footage clip's own measured cuts reach the sound design.

A clip's inner cuts (clip_motion's c["cuts"]) ride on its asset as `cut` landings beside the export's own picture and title
landings, so a played window that shows the next take arriving is an event (score.sound.landing_events); it is sounded
only when that take arrives moving by content (span_motion >= MOTION_FLOOR). Signal only, no model.

    $PY -m pytest -q test_cut_landings_1120.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "_shell")]
import film as F  # noqa: E402
import reel as R  # noqa: E402


def _clip(**kw):
    curve = [0.2] * 30 + [0.0] + [3.0] * 14 + [0.2] * 30          # 15 a second: still, a cut at 2.0 s, a moving take, still
    return dict(path="/x/crowd.mp4", seconds=5.0, cuts=[2.0, 3.0], motion=dict(curve=curve, p90=3.0), **kw)


def test_a_clips_measured_cuts_become_landings_moving_only_where_the_take_moves():
    lands = R.cut_landings(_clip())
    assert lands == [dict(t=2.0, kind="cut", moving=True), dict(t=3.0, kind="cut")]
    assert R.with_landings(dict(path="/x/p.mp4", seconds=4.0)) == dict(path="/x/p.mp4", seconds=4.0)    # nothing measured


def test_the_exports_own_landing_is_that_cut_and_is_kept_as_it_is():
    lands = R.cut_landings(_clip(landings=[dict(t=2.05, kind="picture")]))
    assert lands == [dict(t=2.05, kind="picture"), dict(t=3.0, kind="cut")]


def test_the_asset_carries_them_and_the_sound_design_reads_the_moving_one():
    plan = F.Plan({}, {})
    plan.video("v00", R.with_landings(_clip(w=1920, h=1080)))
    assert plan.assets["v00"]["landings"][0] == dict(t=2.0, kind="cut", moving=True)
    l1 = dict(fps=30, dur_f=300, assets=plan.assets, items=[dict(id="a", k="shot", pic="v00", f=[0, 60], src=[1.0, 3.0])])
    shown = F.landings_shown(l1)
    assert [(e["f"], e["kind"], e["moving"]) for e in shown] == [(30, "cut", True)]
