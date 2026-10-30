"""The story review runs while the preview renders (31 Oct 2026): same
inputs, same call, same report; recorded on the turn's own thread."""
import threading
import time
import types

import agent_tools
import llm
import story_critic


def _ctx(**kw):
    ctx = types.SimpleNamespace(
        project_id=7, claude_trial=None, claude_paid=None, index={},
        user_message="make the captions bigger", edit_plan=None,
        has_main_video=True, editing_metrics={}, metrics={},
        job={"id": 1, "payload": {}})
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


ROW = {"version": 16, "json": {"keep": [[0, 10]]}}


def _wire(monkeypatch, delay=0.2):
    calls = []
    main = threading.get_ident()

    monkeypatch.setattr(story_critic, "should_review", lambda *a, **k: True)
    monkeypatch.setattr(agent_tools, "program_cast_inputs",
                        lambda ctx, edl: ("talking", None))
    monkeypatch.setattr(agent_tools.director, "editorial_family",
                        lambda *a, **k: "talking_head")

    def review(edl, index, family, user_request="", plan=None,
               asset_indexes=None):
        time.sleep(delay)
        calls.append({"thread": threading.get_ident() != main,
                      "family": family, "request": user_request,
                      "plan": llm.turn_plan()})
        llm.record("independent_story_critic", {"model": "m"}, {"text": "{}"})
        return {"verdict": "pass", "findings": []}
    monkeypatch.setattr(story_critic, "review", review)
    monkeypatch.setattr(story_critic, "scope_to_source", lambda r, e: r)
    return calls


def _recorder(rows):
    main = threading.get_ident()

    def rec(purpose, request, response, usage):
        rows.append((purpose, threading.get_ident() == main))
    return rec


def test_a_claude_lane_review_starts_with_the_render(monkeypatch):
    calls = _wire(monkeypatch)
    rows = []
    llm.set_recorder(_recorder(rows))
    llm.set_turn_plan("plus_2026", 7)
    try:
        ctx = _ctx(claude_paid={"lane": {}})
        started = time.monotonic()
        agent_tools._prefetch_story_review(ctx, ROW)
        assert time.monotonic() - started < 0.15      # did not wait for it
        time.sleep(0.3)                                # the "render"
        t0 = time.monotonic()
        report = agent_tools._review_program_story(ctx, ROW)
        assert time.monotonic() - t0 < 0.15            # already answered
    finally:
        llm.set_recorder(None)
        llm.clear_turn_plan()
    assert report["verdict"] == "pass" and report["edl_version"] == 16
    assert report["editorial_family"] == "talking_head"
    assert len(calls) == 1 and calls[0]["thread"]
    assert calls[0]["plan"] == "plus_2026"
    assert calls[0]["request"] == "make the captions bigger"
    # the model-I/O row was written on the turn's own thread
    assert rows == [("independent_story_critic", True)]
    assert ctx.last_story_review is report
    # the same version is never reviewed twice
    assert agent_tools._review_program_story(ctx, ROW) is report
    assert len(calls) == 1


def test_grandfathered_lanes_review_after_the_render_as_before(monkeypatch):
    calls = _wire(monkeypatch, delay=0)
    ctx = _ctx()
    agent_tools._prefetch_story_review(ctx, ROW)
    assert not getattr(ctx, "_story_prefetch", None)
    assert calls == []
    report = agent_tools._review_program_story(ctx, ROW)
    assert report["verdict"] == "pass"
    assert len(calls) == 1 and not calls[0]["thread"]


def test_prepared_trial_turns_still_have_no_story_review(monkeypatch):
    calls = _wire(monkeypatch, delay=0)
    ctx = _ctx(claude_trial=object(), second_opinions_off=True)
    agent_tools._prefetch_story_review(ctx, ROW)
    assert agent_tools._review_program_story(ctx, ROW) is None
    assert calls == []


def test_a_failed_render_still_records_the_call(monkeypatch):
    _wire(monkeypatch, delay=0.05)
    rows = []
    llm.set_recorder(_recorder(rows))
    try:
        ctx = _ctx(claude_trial=object())
        agent_tools._prefetch_story_review(ctx, ROW)
        agent_tools._drain_story_prefetch(ctx)
    finally:
        llm.set_recorder(None)
    assert rows == [("independent_story_critic", True)]
    assert not ctx._story_prefetch


def test_no_review_needed_is_remembered(monkeypatch):
    calls = _wire(monkeypatch, delay=0)
    monkeypatch.setattr(story_critic, "should_review", lambda *a, **k: False)
    ctx = _ctx(claude_trial=object())
    agent_tools._prefetch_story_review(ctx, ROW)
    assert ctx._story_prefetch == {16: None}
    assert agent_tools._review_program_story(ctx, ROW) is None
    assert calls == []
