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
    monkeypatch.setattr(fxgraph, "peer", lambda name, *args, default=None: None)
    tl = SimpleNamespace(out_to_src=lambda t: t)
    ctx = SimpleNamespace(
        project_id=1, index={},
        db=SimpleNamespace(run=lambda fn, *a: {"sha256": "x",
                                               "storage_key": "k"}))
    note = subject_tools._ensure_matte(ctx, {}, tl, [(0.0, 24.8)], wait_s=90)
    assert waited == [90]
    assert "Measuring the subject" in note


def test_background_warms_the_exact_refined_matte_the_renderer_reads(monkeypatch):
    import fxgraph
    import matte_track
    edl = {'keep': [[0, 24.8]], 'fx': [{'type': 'background', 'start': 0, 'end': 24.8}]}
    seen = {}
    monkeypatch.setattr(fxgraph, 'peer_fn', lambda name: lambda *a: 'source')
    def params(name, actual_edl, default=None):
        assert actual_edl is edl
        return matte_track.sidecar_params(actual_edl)
    monkeypatch.setattr(fxgraph, 'peer', params)
    def ensure(*a, **kw):
        seen.update(kw)
        return 'handle'
    monkeypatch.setattr(sidecars, 'ensure', ensure)
    monkeypatch.setattr(sidecars, 'status', lambda h: 'ready')
    ctx = SimpleNamespace(project_id=1, index={},
        db=SimpleNamespace(run=lambda *a: {'sha256': 'source', 'storage_key': 'proxy'}))
    subject_tools._ensure_matte(ctx, edl, SimpleNamespace(out_to_src=lambda t: t), [(0, 24.8)])
    assert seen['params'] == matte_track.sidecar_params(edl)
    assert seen['params']['refine']


def test_pending_background_cannot_render_a_substitute_as_the_requested_swap(monkeypatch):
    row = {'version': 4, 'json': {'keep': [[0, 24.8]],
        'fx': [{'type': 'background', 'start': 0, 'end': 24.8}]}}
    ctx = SimpleNamespace(request_contract_required=False, last_preview={}, rendered_versions=set())
    monkeypatch.setattr(render_dependencies, '_wait_for_matte',
        lambda *a: {'status': 'absent', 'windows': [{'status': 'pending'}]})
    assert render_dependencies.before_preview(ctx, row).startswith('IN_PROGRESS:')
    monkeypatch.setattr(render_dependencies, '_wait_for_matte',
        lambda *a: {'status': 'ready', 'windows': [{'status': 'ready'}]})
    assert render_dependencies.before_preview(ctx, row) is None
