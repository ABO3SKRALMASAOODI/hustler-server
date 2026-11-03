"""Pending measurements cannot consume an approval or reuse a substitute."""
from types import SimpleNamespace as NS

import agent_tools
import render_dependencies as deps
import sidecars
import world_layer
from tools_ext import world_tools


def setup(monkeypatch, status):
    row = {"version": 40, "json": {"keep": [[0, 10]], "motion": [{"template": "typeset"}]}}
    ctx = NS(request_contract_required=True, latest_edl=lambda: row,
             rendered_versions={40}, last_preview={"edl_version": 40},
             render_dependency_state=None)
    source = NS(sha256="source")
    monkeypatch.setattr(world_tools, "_world_ctx", lambda *_: NS(source=source))
    monkeypatch.setattr(world_tools, "_launch_source", lambda *_: source)
    monkeypatch.setattr(world_layer, "requests", lambda *_: [("objects.sam2", [(3, 8.5)], {"id": "performer"})])
    monkeypatch.setattr(world_layer, "warm", lambda *args, **kw: {"objects.sam2": status})
    monkeypatch.setattr(sidecars, "spec", lambda *_: {"model_id": "sam2", "model_version": "1"})
    return ctx, row


def test_pending_mask_waits_before_any_render_is_enqueued(monkeypatch):
    ctx, row = setup(monkeypatch, "running")
    assert agent_tools.render_preview(ctx, complete=True).startswith("IN_PROGRESS:")
    assert ctx.rendered_versions == {40}
    assert ctx.render_dependency_state["ready"] is False


def test_ready_mask_invalidates_only_the_old_substitute(monkeypatch):
    ctx, row = setup(monkeypatch, "ready")
    assert deps.before_preview(ctx, row) is None
    key = ctx.render_dependency_key
    assert key and ctx.rendered_versions == set()
    ctx.last_preview["render_dependency_key"] = key
    ctx.rendered_versions.add(40)
    assert deps.before_preview(ctx, row) is None
    assert ctx.render_dependency_key == key and ctx.rendered_versions == {40}


def test_measurement_failure_is_not_reported_as_a_finished_effect(monkeypatch):
    ctx, row = setup(monkeypatch, "failed")
    assert deps.before_preview(ctx, row).startswith("ERROR:")
    assert ctx.render_dependency_key is None


def test_render_identity_includes_the_ready_inputs():
    row = {"version": 40, "json": {"keep": [[0, 10]]}}
    first = agent_tools._render_signature(row, "preview")
    assert first == agent_tools._render_signature(row, "preview", dependency_key=None)
    assert first != agent_tools._render_signature(row, "preview", dependency_key="measured")
