"""A broken intermediate timeline must never become the delivered export."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop  # noqa: E402
import db as dbx  # noqa: E402


class _Ctx:
    def __init__(self, user_message="build the full school story"):
        self.project_id = 9
        self.job = {"id": 77}
        self.user_message = user_message
        self.turn_start_edl = {
            "version": 2, "json": {"keep": [[0.0, 192.0]]}}
        self.current = {"version": 4, "json": {"keep": [[12.0, 12.08]]}}
        self.versions_written = [3, 4]
        self.last_change = {"video": [[0, 1]]}
        self.last_preview = {"edl_version": 4}
        self.last_preview_failure = {"error": "bad"}
        self.rendered_versions = set()
        self.terminal_preview_recovery = None
        self.last_visual_critic = None
        self.last_story_review = None
        self.last_audio_review = None
        self.last_taste = []

    def latest_edl(self):
        return self.current


class _Db:
    def __init__(self, ctx):
        self.ctx = ctx

    def run(self, fn, *args):
        if fn is dbx.get_edl_version:
            return {"version": args[1], "json": {"keep": [[0.0, 40.0]]}}
        assert fn is dbx.insert_edl
        self.ctx.current = {"version": 5, "json": args[1]}
        return 5


def test_catastrophic_collapse_restores_turn_baseline_append_only():
    ctx = _Ctx()
    recovered = agent_loop._recover_catastrophic_timeline_collapse(ctx, _Db(ctx))
    assert recovered["from_version"] == 4
    assert recovered["to_version"] == 5
    assert ctx.current["json"] == {"keep": [[0.0, 192.0]]}
    assert ctx.versions_written == [3, 4, 5]
    assert ctx.last_preview is None


def test_explicit_micro_edit_is_not_second_guessed():
    ctx = _Ctx("make it exactly 0.08 seconds")
    assert agent_loop._recover_catastrophic_timeline_collapse(ctx, _Db(ctx)) is None
    assert ctx.current["version"] == 4


def test_terminal_deterministic_failure_restores_last_rendered_version():
    ctx = _Ctx()
    ctx.last_preview_failure = {
        "kind": "deterministic_ffmpeg", "agent_repairable": True,
        "error": "black-frame check failed",
    }
    ctx.rendered_versions = {3}
    db = _Db(ctx)

    recovered = agent_loop._restore_terminal_preview_failure(ctx, db)

    assert recovered["failed_version"] == 4
    assert recovered["restored_from_version"] == 3
    assert ctx.current["json"] == {"keep": [[0.0, 40.0]]}
    assert ctx.last_preview_failure is None


def test_transient_preview_failure_never_rolls_back_a_valid_edit():
    ctx = _Ctx()
    ctx.last_preview_failure = {
        "kind": "transient_infrastructure", "agent_repairable": False,
        "error": "HTTP 503",
    }
    assert agent_loop._restore_terminal_preview_failure(ctx, _Db(ctx)) is None
    assert ctx.current["version"] == 4
