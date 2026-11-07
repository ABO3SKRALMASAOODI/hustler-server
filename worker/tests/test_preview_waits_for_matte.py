"""p5533 (ticket #77): a preview of a matte effect waits for its matte."""
from types import SimpleNamespace

import render_dependencies
import sidecars
from tools_ext import subject_tools


def ctx_for(monkeypatch):
    seen = {}
    def ensure(ctx, edl, tl, windows, wait_s=0):
        seen.update(windows=windows, wait_s=wait_s)
        return ""
    monkeypatch.setattr(subject_tools, "_ensure_matte", ensure)
    return SimpleNamespace(request_contract_required=False), seen


def test_a_subject_effect_waits_past_a_lone_preview_budget(monkeypatch):
    ctx, seen = ctx_for(monkeypatch)
    row = {"version": 4, "json": {"keep": [[0.0, 24.8]], "fx": [
        {"id": "sx1", "type": "subject", "start": 0.0, "end": 24.8},
        {"id": "r", "type": "region", "start": 0.0, "end": 24.8}]}}
    assert render_dependencies.before_preview(ctx, row) is None
    assert seen == {"windows": [(0.0, 24.8)], "wait_s": 90.0}


def test_an_edit_with_no_matte_effect_does_not_wait(monkeypatch):
    ctx, seen = ctx_for(monkeypatch)
    row = {"version": 7, "json": {"keep": [[0.0, 24.8]], "fx": [
        {"id": "r", "type": "region", "start": 0.0, "end": 24.8}]}}
    assert render_dependencies.before_preview(ctx, row) is None
    assert seen == {}


def test_the_wait_reaches_the_sidecar_and_never_refuses(monkeypatch):
    """A matte still measuring after the wait is a note, not an error."""
    waited = []
    monkeypatch.setattr(sidecars, "wait", lambda h, s: waited.append(s))
    monkeypatch.setattr(sidecars, "ensure", lambda *a, **k: "handle")
    monkeypatch.setattr(sidecars, "status", lambda h: "pending")
    monkeypatch.setattr(sidecars, "estimate_s", lambda h: 60)
    import fxgraph
    monkeypatch.setattr(fxgraph, "peer_fn",
                        lambda name: (lambda *a: "source"))
    monkeypatch.setattr(fxgraph, "peer", lambda name, default=None: None)
    tl = SimpleNamespace(out_to_src=lambda t: t)
    ctx = SimpleNamespace(
        project_id=1, index={},
        db=SimpleNamespace(run=lambda fn, *a: {"sha256": "x",
                                               "storage_key": "k"}))
    note = subject_tools._ensure_matte(ctx, {}, tl, [(0.0, 24.8)], wait_s=90)
    assert waited == [90]
    assert "Measuring the subject" in note
