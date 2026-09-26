"""Regressions from subscriber projects 2535/2592: valid long media failed."""
import pytest

import config
import failure_policy
import main as worker_main
import media
import remote
import renderer
from test_failure_notes import _FakeDb


def enable(monkeypatch):
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_ENABLED', True)
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_URL', 'https://example.invalid')
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_PERCENT', 100)
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_TYPES', {'index', 'preview'})


def test_subscriber_4_86gb_upload_can_use_batch_indexer(monkeypatch):
    enable(monkeypatch)
    job = {'id': 41613, 'type': 'index', 'project_id': 2592,
           '_execution_shape': {'assets': 1, 'total_bytes': 4859648332,
                                'max_duration_s': 6238.783}}
    assert remote.desired_execution_provider(job) == 'cloudflare'
    # The smaller interactive lane keeps its conservative staging ceiling.
    assert not remote._cloudflare_selected(dict(job, type='preview'))


@pytest.mark.parametrize('size', [None, 0, -1, 9 * 1024 ** 3])
def test_unknown_or_oversized_index_is_not_admitted(monkeypatch, size):
    enable(monkeypatch)
    assert not remote._cloudflare_selected({
        'id': 1, 'type': 'index', '_execution_shape': {'total_bytes': size}})


@pytest.mark.parametrize('preview', [True, False])
def test_cloudflare_long_render_has_time_for_healthy_progress(monkeypatch, preview):
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloudflare')
    # Production was still encoding at 0.9x when the flat 3000s cap killed it.
    timeout = renderer._render_ffmpeg_timeout(preview, 2882.5)
    assert timeout >= int(2882.5 * 2 + 600)
    kind = 'preview' if preview else 'final'
    assert timeout <= config.cloudflare_timeout_for(kind) - 600


@pytest.mark.parametrize('kind', ['preview', 'preview_check', 'final'])
def test_wall_clock_budget_is_not_permission_to_rewrite_customer_edit(kind):
    decision = failure_policy.classify(media.MediaError(
        'ffmpeg killed: wall-clock 3000s exceeded; last progress 2875/2877s (99.9%)'), kind)
    assert not decision.retryable
    assert not decision.agent_repairable


@pytest.mark.parametrize('kind,error', [
    ('index', 'no remote executor is configured for index'),
    ('preview', 'ffmpeg killed: wall-clock 3000s exceeded'),
    ('final', 'ffmpeg killed: wall-clock 3000s exceeded'),
])
def test_infrastructure_failure_note_preserves_upload_and_edit(kind, error):
    fake = _FakeDb()
    worker_main._notify_failure(fake, {'id': 7, 'type': kind, 'project_id': 3,
        'payload': {'source': 'user_edit'}}, RuntimeError(error))
    assert len(fake.messages) == 1
    note = fake.messages[0][2].lower()
    assert 'saved' in note
    for misleading in ('uploading it again', 'different format', 'needs to be repaired',
                       'hit retry', 'make another edit', 'safety check', 'ffmpeg'):
        assert misleading not in note


def test_proof_render_keeps_its_shorter_lease(monkeypatch):
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloudflare')
    token = renderer._RENDER_JOB_TYPE.set('preview_check')
    try:
        assert renderer._render_ffmpeg_timeout(True, 7200) <= (
            config.cloudflare_timeout_for('preview_check') - 60)
    finally:
        renderer._RENDER_JOB_TYPE.reset(token)


def test_short_render_budget_and_legacy_http_limit_are_preserved(monkeypatch):
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloudflare')
    assert renderer._render_ffmpeg_timeout(True, 30) == config.FFMPEG_TIMEOUT_S
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloud_run')
    assert renderer._render_ffmpeg_timeout(False, 7200) == config.FFMPEG_TIMEOUT_S


def test_configured_small_provider_envelope_is_respected(monkeypatch):
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloudflare')
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_TIMEOUTS', {'preview': 3600})
    assert renderer._render_ffmpeg_timeout(True, 7200) <= 3000


def test_index_admission_retains_the_operator_limit(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(config, 'CLOUDFLARE_MAX_INDEX_INPUT_BYTES', 4 * 1024 ** 3)
    assert not remote._cloudflare_selected({'id': 1, 'type': 'index',
        '_execution_shape': {'total_bytes': 4859648332}})


def test_renderer_restores_job_context_after_failure(monkeypatch):
    monkeypatch.setattr(renderer, '_run_render_job', lambda *a: (_ for _ in ()).throw(RuntimeError('stopped')))
    with pytest.raises(RuntimeError, match='stopped'):
        renderer.run_render_job(None, {'type': 'preview_check', 'payload': {}})
    assert renderer._RENDER_JOB_TYPE.get() is None


def test_runaway_graph_still_allows_a_real_edit_repair():
    d = failure_policy.classify(media.MediaError('runaway encode'), 'preview')
    assert d.agent_repairable
    assert not d.retryable
