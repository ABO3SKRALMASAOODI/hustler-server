"""Concurrent file checks retain findings and join before publication."""
from contextvars import ContextVar
from threading import Barrier

import render_qc


def test_independent_passes_overlap_with_owned_context_and_all_findings(tmp_path, monkeypatch):
    film = tmp_path / 'film.mp4'
    film.write_bytes(b'checked by the stub decoders')
    barrier = Barrier(3, timeout=3)
    scope = ContextVar('render-scope', default=None)
    token = scope.set('render-71')
    observed = []

    def read(name, result):
        def run(*args, **kwargs):
            observed.append((name, scope.get()))
            barrier.wait()
            return result
        return run

    monkeypatch.setattr(render_qc, 'endcard_match', read('branding', {'ok': False}))
    monkeypatch.setattr(render_qc, 'face_samples', read('faces', []))
    monkeypatch.setattr(render_qc, 'type_pass', read('type', {'ink': True}))
    monkeypatch.setattr(render_qc, 'type_findings', lambda *a: {'small': ['headline']})
    monkeypatch.setattr(render_qc, '_graphic_face_tracks', lambda *a: [])
    monkeypatch.setattr(render_qc, 'graphic_faces', lambda *a: [])
    monkeypatch.setattr(render_qc, 'boundary_stall', lambda *a: [])
    monkeypatch.setattr(render_qc, 'frame_changes', lambda *a, **k: [])
    monkeypatch.setattr(render_qc, 'clipped_faces', lambda *a: [[0, 1, 'left']])
    monkeypatch.setattr(render_qc, 'cut_faces', lambda *a: [])
    monkeypatch.setattr(render_qc, 'softened_faces', lambda *a: [])
    monkeypatch.setattr(render_qc, 'reframes_off_cut', lambda *a: [])
    monkeypatch.setattr(render_qc, 'jumps', lambda *a: [])
    monkeypatch.setattr(render_qc, 'orphans', lambda *a: [])
    monkeypatch.setattr(render_qc, 'layout_glitches', lambda *a: [])
    monkeypatch.setattr(render_qc, 'findings', lambda r, p: [r['endcard'], r['clipped'], r['type']])
    monkeypatch.setattr(render_qc, 'advice', lambda *a: [])
    monkeypatch.setattr(render_qc, 'source_limit_advice', lambda *a: [])
    plan = dict(fps=30, W=720, H=1280, program_s=2, endcard=True,
                type={'items': ['headline']})
    try:
        result = render_qc.check(str(film), plan)
    finally:
        scope.reset(token)
    assert sorted(observed) == [(n, 'render-71') for n in ('branding', 'faces', 'type')]
    assert not barrier.broken
    assert result['skipped'] == []
    assert result['findings'] == [{'ok': False}, [[0, 1, 'left', False]],
                                  {'small': ['headline'], 'face': []}]
    assert set(result['phase_seconds']) == {'branding', 'faces', 'type', 'temporal'}
