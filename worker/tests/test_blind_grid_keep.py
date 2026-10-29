"""Ticket #9: a uniform keep grid on long, nearly wordless footage is flagged."""

import agent_tools


class _Ctx:
    def __init__(self, duration, n_words):
        self.duration = duration
        self.index = {"words": [{"word": "gg", "start": i, "end": i + .3}
                                for i in range(n_words)]}


def _grid(n=19, start=60.0, step=195.0, length=37.0):
    return [[start + i * step, start + i * step + length + (i % 3)]
            for i in range(n)]


def test_ticket9_roblox_grid_is_flagged():
    # 63-minute source, 174 spoken words, 19 chunks of 35-40 s.
    note = agent_tools._blind_grid_note(_Ctx(3780.0, 174), _grid())
    assert "BLIND GRID" in note and "find_visual_moments" in note


def test_story_cut_of_the_same_footage_is_not_flagged():
    keep = [[30, 50], [300, 390], [700, 712], [1500, 1620], [2400, 2430],
            [3000, 3150]]
    assert agent_tools._blind_grid_note(_Ctx(3780.0, 174), keep) == ""


def test_talking_footage_and_short_sources_are_not_flagged():
    assert agent_tools._blind_grid_note(_Ctx(3780.0, 9000), _grid()) == ""
    assert agent_tools._blind_grid_note(
        _Ctx(600.0, 10), _grid(n=8, start=0, step=70, length=30)) == ""


def test_keep_segments_appends_the_note(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep",
                        lambda ctx, merged, msg, **kw: "EDL v2: " + msg)
    ctx = _Ctx(3780.0, 174)
    ctx.clamp = float
    res = agent_tools.keep_segments(ctx, _grid())
    assert res.startswith("EDL v2") and "BLIND GRID" in res
