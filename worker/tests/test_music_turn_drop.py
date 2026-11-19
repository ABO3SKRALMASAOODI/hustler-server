"""A short whose payoff is its last sentence still drops (round1 thin-sound, 19 Nov 2026).

The payoff of a podcast short sits 0.8-3.4 s before its last hit, too close for a phrase after it, so arrange() had
no drop at all and the bed stayed flat. When the caller names the story's turn as well (opt-in, the podcast
workflow: direction_of's turn, --turn with --payoff), the drop lands on the turn and the payoff keeps its own hit.
Without the opt-in nothing changes."""
import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from score import music as MU  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")


@pytest.fixture(scope="module")
def song(tmp_path_factory):
    if shutil.which(FF) is None:
        pytest.skip("needs ffmpeg")
    from test_music_film import _wav
    return MU.analyse(_wav(tmp_path_factory.mktemp("turn"), "full", bars=40, intro_bars=8), sung=False)


def _marks(dur, turn, payoff, **kw):
    from test_music_film import _marks as base
    return dict(base(dur=dur, turn=turn, payoff=payoff), **kw)


def test_a_late_payoff_without_the_turn_opt_in_keeps_todays_plan(song):
    plan = MU.arrange(song, _marks(32.0, 14.0, 29.5))
    assert plan["ok"] and plan["drop_at"] is None and plan["drop_on"] is None


def test_a_late_payoff_with_the_story_turn_drops_on_the_turn(song):
    plan = MU.arrange(song, _marks(32.0, 14.0, 29.5, turn_drop=True))
    assert plan["ok"] and plan["drop_on"] == "turn"
    assert abs(plan["drop_at"] - 14.0) < 0.35 and plan["build_bars"] >= 2
    # the payoff keeps its own breath and hit after the drop
    nf = int(plan["end_hit_s"] / 0.01) + 50
    slow, fast, notes = MU.flow(nf, 0.01, _marks(32.0, 14.0, 29.5), plan, MU.PROFILE, 0, 0)
    assert any(h["what"] == "drop" for h in notes["hits"]) and any(h["what"] == "payoff" for h in notes["hits"])
    p = int(29.5 / 0.01)
    curve = slow + fast
    assert curve[p + 2] - np.min(curve[p - 50:p]) > 12


def test_a_payoff_with_room_still_takes_the_drop_with_the_opt_in(song):
    plan = MU.arrange(song, _marks(32.0, 8.0, 16.0, turn_drop=True))
    assert plan["ok"] and plan["drop_on"] == "payoff" and abs(plan["drop_at"] - 16.0) < 0.35


def test_sounds_on_the_drop_follow_the_mark_the_drop_landed_on():
    plan = {"drop_at": 14.2, "drop_on": "turn", "turn_s": 14.0, "payoff_s": 29.5}
    moved = MU.sounds_on_the_drop([{"event_f": 420, "start_s": 13.9}], plan, 30.0)
    assert moved[0]["event_f"] == 426
