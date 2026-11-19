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


def test_a_payoff_with_no_turn_at_all_drops_with_the_last_hit_in_the_films_tail(song):
    # the payoff is the last sentence (last word ends 32.3 s) and nothing names or reads a turn
    base = MU.arrange(song, _marks(34.0, None, 29.5))
    assert base["ok"] and base["drop_at"] is None                                  # today: no room for a phrase after it
    plan = MU.arrange(song, _marks(34.0, None, 29.5, turn_drop=True))
    assert plan["ok"] and plan["drop_on"] == "payoff" and abs(plan["drop_at"] - 29.5) < 0.35
    assert plan["end_hit_s"] > base["end_hit_s"] and plan["end_hit_s"] - plan["drop_at"] >= 4 * 0.5 - 1e-6
    assert plan["end_hit_s"] + 0.5 <= 34.0 - MU.PROFILE["end_gap_s"] + 1e-6       # it rings before the end card's gap


def test_a_turn_too_early_to_build_from_hands_the_drop_to_the_payoff_in_the_tail(song):
    plan = MU.arrange(song, _marks(34.0, 1.0, 29.5, turn_drop=True))
    assert plan["ok"] and plan["drop_on"] == "payoff" and abs(plan["drop_at"] - 29.5) < 0.35


# ----------------------------------------------------------------- music_film.score: the caller's flag and the reading

def _scene(dur=34.0):
    fps = 30
    words = [{"t0": 0.4 + k * 0.5, "t1": 0.8 + k * 0.5, "w": f"w{k}"} for k in range(int((dur - 2) / 0.5))]
    l1 = {"fps": fps, "dur_f": int(dur * fps), "spine": [{"f": [0, int(dur * fps)], "src": [0.0, dur]}], "items": [], "sound": []}
    return l1, {"words": words}


def _run_score(monkeypatch, tmp_path, **kw):
    import music_film as MF
    seen = {}

    def facts(kind, marks, brief="", mood=""):
        seen["mood"] = mood
        return {"kind": kind, "mood": mood}

    def choose(f, marks, cache, used=(), call=None, profile=None):
        seen["marks"] = dict(marks)
        return None, None, []
    monkeypatch.setattr(MF, "facts", facts)
    monkeypatch.setattr(MF, "choose", choose)
    asked = []

    def call(system, user, schema):
        asked.append(user)
        return {"turn_s": 14.0, "payoff_s": 25.0, "mood": "wry, dry"}
    l1, index = _scene()
    rep = MF.score(l1, str(tmp_path), kind="podcast", cache=str(tmp_path), call=call, index=index, **kw)
    return rep, seen, asked


def test_both_marks_from_the_caller_still_read_the_films_mood_for_the_track_choice(monkeypatch, tmp_path):
    rep, seen, asked = _run_score(monkeypatch, tmp_path, turn_s=19.6, payoff_s=29.5, turn_drop=True)
    assert len(asked) == 1 and rep["marks"]["mood"] == "wry, dry" and seen["mood"] == "wry, dry"
    assert rep["marks"]["turn_s"] == 19.6 and rep["marks"]["payoff_s"] == 29.5        # the caller's marks stand
    assert seen["marks"]["turn_drop"] is True and seen["marks"]["turn_s"] == 19.6


def test_a_direction_with_no_story_turn_drops_on_the_turn_the_model_read(monkeypatch, tmp_path, song):
    rep, seen, _ = _run_score(monkeypatch, tmp_path, payoff_s=29.5, turn_drop=True)
    m = seen["marks"]
    assert m["turn_drop"] is True and m["turn_s"] == 14.0 and m["payoff_s"] == 29.5 and rep["marks"]["turn_drop"] is True
    plan = MU.arrange(song, m)
    assert plan["ok"] and plan["drop_on"] == "turn" and abs(plan["drop_at"] - 14.0) < 0.35


def test_without_the_flag_the_customer_arrangement_is_todays(monkeypatch, tmp_path, song):
    rep, seen, _ = _run_score(monkeypatch, tmp_path, payoff_s=29.5)
    assert "turn_drop" not in seen["marks"] and rep["marks"]["turn_drop"] is False
    rep2, seen2, _ = _run_score(monkeypatch, tmp_path, turn_s=19.6, payoff_s=29.5)
    assert "turn_drop" not in seen2["marks"]                                           # both marks alone are no opt-in
    assert MU.arrange(song, seen2["marks"])["drop_at"] is None
