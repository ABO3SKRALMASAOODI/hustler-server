"""D1 (owner rule, strategy-1025/DECISIONS.md): EXISTING subscribers keep
exactly their current behaviour. The trial branch's engine changes in SHARED
tool code (the phone-layout capture, the automatic tour, talking-head crops,
hover push-ins, picture-QC persistence) reach only a free-trial turn, a NEW
subscriber on the Claude lane, or a First cut for such an account
(agent_tools.engine_on). QI, 30 Oct 2026."""
import ast
import os
from types import SimpleNamespace

import pytest

import agent_tools
import remote
import webrecord


def _ctx(kind):
    ctx = SimpleNamespace(project_id=7, user_id=11, workdir="/tmp")
    if kind == "trial":
        ctx.claude_trial = object()
    elif kind == "new_subscriber":
        ctx.claude_paid = {"lane": object()}
    elif kind == "first_cut":
        ctx.first_cut_q1 = True
    elif kind == "grandfathered":
        ctx.claude_trial = None
        ctx.claude_paid = None
        ctx.first_cut_q1 = False
    return ctx


ENGINE = ["trial", "new_subscriber", "first_cut"]
MAIN = ["grandfathered", "mcp"]


@pytest.mark.parametrize("kind", ENGINE)
def test_engine_accounts_get_the_engine(kind):
    assert agent_tools.engine_on(_ctx(kind))


@pytest.mark.parametrize("kind", MAIN)
def test_grandfathered_and_mcp_keep_main(kind):
    assert not agent_tools.engine_on(_ctx(kind))


def _main_tool_descriptions():
    """record_website_demo's description as main ships it, read from the
    source text main left untouched around it (the tour sentence is the
    trial branch's only change to it)."""
    desc = agent_tools.TOOLS["record_website_demo"][1]
    return desc.replace(agent_tools._TOUR_STEP_TEXT, agent_tools._NO_TOUR_TEXT)


def test_the_tour_step_is_advertised_to_engine_lanes_only():
    def desc(**kw):
        return next(t["function"]["description"]
                    for t in agent_tools.openai_tools(**kw)
                    if t["function"]["name"] == "record_website_demo")
    plain = desc()                  # MCP and every grandfathered turn
    assert "tour" not in plain
    assert "{do:'goto', url:'…'}. Add `seconds`" in plain
    assert plain == _main_tool_descriptions()
    assert "{do:'tour', by:2}" in desc(engine=True)


def test_agent_loop_asks_for_the_accounts_schema():
    """Both schema requests in the agent loop pass the account's engine bit,
    so a grandfathered turn gets main's catalogue."""
    src = open(os.path.join(os.path.dirname(agent_tools.__file__),
                            "agent_loop.py")).read()
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "openai_tools"]
    assert calls
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "engine" in kw
        assert ast.unparse(kw["engine"]) == "agent_tools.engine_on(ctx)"


def _seen_css(monkeypatch):
    seen = []

    def fake(url, workdir, orientation, wall_s, cursor, body, css=None):
        seen.append(css)
        return {}
    monkeypatch.setattr(webrecord, "_record", fake)
    return seen


def test_a_grandfathered_capture_films_mains_viewport(monkeypatch):
    seen = _seen_css(monkeypatch)
    webrecord.record("https://acme.io", "/tmp", orientation="portrait")
    webrecord.record_demo("https://acme.io", "/tmp", [{"do": "scroll"}],
                          orientation="portrait")
    assert seen == [webrecord._DESKTOP_VIEWPORTS] * 2
    # main: the frame size in CSS pixels at density 1
    w, h = webrecord._VIEWPORTS["portrait"]
    assert webrecord._DESKTOP_VIEWPORTS.get("portrait", (w, h, 1.0)) == \
        (1080, 1920, 1.0)


def test_engine_captures_film_the_phone_layout(monkeypatch):
    seen = _seen_css(monkeypatch)
    webrecord.record("https://acme.io", "/tmp", orientation="portrait",
                     phone_layout=True)
    webrecord.record_demo("https://acme.io", "/tmp", [{"do": "scroll"}],
                          orientation="portrait", phone_layout=True)
    webrecord.record_demo("https://acme.io", "/tmp", [{"do": "tour", "by": 2}],
                          orientation="portrait", phone_layout=True)
    # None = _CSS_VIEWPORTS (C1's 432-wide phone layout)
    assert seen == [None, None, webrecord._TOUR_VIEWPORTS]


@pytest.mark.parametrize("kind,want", [("trial", True), ("new_subscriber", True),
                                       ("first_cut", True),
                                       ("grandfathered", False), ("mcp", False)])
def test_the_capture_payload_carries_the_accounts_layout(monkeypatch, kind,
                                                         want):
    sent = {}
    monkeypatch.setattr(remote, "capture_available", lambda: True)

    def run(project_id, payload, user_id=None):
        sent.update(payload)
        return {"storage_key": "k", "problems": []}
    monkeypatch.setattr(remote, "run_capture_remote", run)
    got, err = agent_tools._run_capture(_ctx(kind), "demo", "https://acme.io",
                                        steps=[], orientation="portrait")
    assert err is None and got["storage_key"] == "k"
    assert sent["phone_layout"] is want


def test_the_executor_honours_the_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(webrecord, "local_available", lambda: True)
    monkeypatch.setattr(webrecord.config, "WEB_RECORD_ENABLED", True,
                        raising=False)

    def demo(url, workdir, steps, orientation="landscape", phone_layout=False):
        calls.append(phone_layout)
        raise webrecord.WebRecordError("stop here")
    monkeypatch.setattr(webrecord, "record_demo", demo)
    for flag in (None, True):
        payload = {"mode": "demo", "url": "https://acme.io", "steps": []}
        if flag is not None:
            payload["phone_layout"] = flag
        with pytest.raises(webrecord.WebRecordError):
            webrecord.run_capture_job(None, {"project_id": 1,
                                             "payload": payload})
    assert calls == [False, True]


@pytest.mark.parametrize("kind", ENGINE)
def test_engine_talking_heads_are_cropped(kind):
    big = [[0.4, 0.2, 0.6, 0.5]] * 5
    assert agent_tools._crops_talking_head(_ctx(kind), big, 0.9)


@pytest.mark.parametrize("kind", MAIN)
def test_grandfathered_auto_reframe_keeps_mains_detail_rule(kind):
    big = [[0.4, 0.2, 0.6, 0.5]] * 5
    assert not agent_tools._crops_talking_head(_ctx(kind), big, 0.9)


@pytest.mark.parametrize("kind,kinds", [("trial", ("click", "hover")),
                                        ("grandfathered", ("click",)),
                                        ("mcp", ("click",))])
def test_hover_push_ins_are_the_engines(kind, kinds):
    assert agent_tools._pointed_kinds(_ctx(kind)) == kinds


def test_picture_qc_persistence_is_the_engines():
    line = ["the subject drifts left 0.12 at 4.1 s"]
    gf, tr = _ctx("grandfathered"), _ctx("trial")
    for _ in range(agent_tools.PQ_PERSIST_RENDERS + 1):
        keep_gf, persisted_gf = agent_tools._picture_qc_split(gf, line)
        keep_tr, persisted_tr = agent_tools._picture_qc_split(tr, line)
    # main: a picture finding stays blocking however often it repeats
    assert keep_gf == line and persisted_gf == []
    assert keep_tr == [] and persisted_tr == line
