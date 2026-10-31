"""Ticket #46 (30 Oct, MCP user 2007): render_preview(complete=true) ran 8
full 41-min draft encodes to adjust short cursor overlays."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402

LECTURE = {"version": 11, "json": {"keep": [[0.0, 99.0], [103.0, 2464.0]]}}


class _DB:
    def __init__(self, last):
        self.last = last

    def run(self, fn, *args):
        assert fn is agent_tools.dbx.recent_complete_preview
        return self.last


def _ctx(last):
    return types.SimpleNamespace(db=_DB(last), project_id=2842, metrics={})


def _wire(monkeypatch, ranges=((296.0, 356.0),)):
    calls = []
    monkeypatch.setattr(agent_tools, "_change_check_ranges",
                        lambda *_a, **_k: (list(ranges), None))
    monkeypatch.setattr(agent_tools, "_run_changed_preview_check",
                        lambda ctx, row, plan, r: calls.append(r) or "Proof v11 rendered.")
    monkeypatch.setattr(agent_tools, "_metric", lambda *_a, **_k: None)
    return calls


def test_a_second_full_draft_of_an_unchanged_long_program_is_a_proof(monkeypatch):
    calls = _wire(monkeypatch)
    last = {"id": 38140, "edl_version": 10, "duration_s": 2460.0, "age_s": 1200}
    out = agent_tools._long_program_repeat_proof(_ctx(last), LECTURE, [])
    assert calls == [[(296.0, 356.0)]]
    assert "CHANGED SECTIONS only" in out and "export_final" in out
    assert "41-min" in out and "20 min ago" in out


def test_first_draft_short_program_or_new_length_render_in_full(monkeypatch):
    calls = _wire(monkeypatch)
    assert agent_tools._long_program_repeat_proof(_ctx(None), LECTURE, []) is None
    short = {"version": 3, "json": {"keep": [[0.0, 300.0]]}}
    last = {"id": 1, "edl_version": 2, "duration_s": 300.0, "age_s": 60}
    assert agent_tools._long_program_repeat_proof(_ctx(last), short, []) is None
    cut = {"id": 1, "edl_version": 10, "duration_s": 1800.0, "age_s": 60}
    assert agent_tools._long_program_repeat_proof(_ctx(cut), LECTURE, []) is None
    assert calls == []


def test_nothing_changed_to_prove_renders_as_asked(monkeypatch):
    _wire(monkeypatch, ranges=())
    last = {"id": 1, "edl_version": 10, "duration_s": 2460.0, "age_s": 60}
    assert agent_tools._long_program_repeat_proof(_ctx(last), LECTURE, []) is None


def test_render_preview_asks_the_guard_only_for_mcp_drafts():
    import inspect
    src = inspect.getsource(agent_tools.render_preview)
    assert "_long_program_repeat_proof(ctx, row, plan)" in src
    assert "music_gate.is_mcp(ctx)" in src
