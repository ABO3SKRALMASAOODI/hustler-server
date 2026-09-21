"""Large uploaded libraries must not reject small, streamable editing jobs."""
import execution_inputs as inputs
import failure_policy
import filmstrip
import remote
import config
import db
import storage


def asset(key, size, kind='video_clip', duration=20):
    return dict(storage_key=key, bytes=size, kind=kind, duration_s=duration)


def library():
    return ([asset(f'clip/{i}.mp4', 350_000_000) for i in range(54)]
            + [asset('proxy.mp4', 418633, 'proxy'),
               asset('original.mp4', 7574191, 'original')])


def test_large_library_small_preview_routes_to_configured_executor(monkeypatch):
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_ENABLED', True)
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_URL', 'https://example.invalid')
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_PERCENT', 100)
    monkeypatch.setattr(config, 'CLOUDFLARE_EXECUTOR_TYPES', {'preview'})
    shape = inputs.job_shape(library(), {'keep': [[0, 9.51]]}, 'preview', {})
    assert shape['staged_bytes'] == 418633
    assert shape['assets'] == 1
    assert remote.desired_execution_provider(
        {'id': 1, 'type': 'preview', '_execution_shape': shape}) == 'cloudflare'


def test_render_counts_actual_insert_audio_and_nested_mask():
    rows = library() + [asset('music.wav', 8_000_000, 'music'),
                        asset('mask.png', 2_000_000, 'image_ref')]
    edl = {'keep': [[0, 3]], 'inserts': [{'asset_key': 'clip/0.mp4'}],
           'music': [{'storage_key': 'music.wav'}],
           'texts': [{'behind': {'asset_key': 'mask.png'}}]}
    shape = inputs.job_shape(rows, edl, 'final', {})
    assert shape['staged_bytes'] == 7574191 + 8_000_000 + 2_000_000


def test_capacity_never_treats_missing_metadata_as_free():
    for rows in ([], [asset('unknown.mp4', None)]):
        shape = inputs.job_shape(rows, {'canvas': {'width': 100},
                                  'inserts': [{'asset_key': 'unknown.mp4'}]}, 'final', {})
        assert shape['staged_bytes'] > config.CLOUDFLARE_MAX_INPUT_BYTES


def test_filmstrip_admission_matches_bounded_parallel_downloads():
    rows = [asset(f'{i}.png', 20_000_000, 'image_ref') for i in range(54)]
    rows += [asset('proxy.mp4', 418633, 'proxy')]
    shape = inputs.job_shape(rows, {}, 'filmstrip', {})
    assert shape['staged_bytes'] == 20_000_000 * inputs.FILMSTRIP_WORKERS
    assert shape['assets'] == inputs.FILMSTRIP_ASSETS + 1
    assert inputs.job_shape(library(), {}, 'filmstrip', {})['staged_bytes'] == 418633


def test_large_filmstrip_input_streams_without_downloading(monkeypatch, tmp_path):
    monkeypatch.setenv('EXECUTOR_PROVIDER', 'cloudflare')
    monkeypatch.setattr(filmstrip.storage, 'object_bytes', lambda k: 350_000_000)
    monkeypatch.setattr(filmstrip.storage, 'presign_get', lambda *a, **kw: 'https://example.invalid/clip')
    def forbidden(*a):
        raise AssertionError('whole source must not download')
    monkeypatch.setattr(filmstrip.storage, 'download_to', forbidden)
    assert filmstrip._local_for_ref('clip.mp4', str(tmp_path), 'x').startswith('https://')


def test_absent_executor_does_not_retry_the_identical_job():
    for error in ('no remote executor is configured for preview',
                  'Modal or Cloudflare is required for filmstrip compute'):
        decision = failure_policy.classify(RuntimeError(error), 'preview')
        assert decision.kind == 'executor_unavailable'
        assert not decision.retryable


def test_admission_resolves_only_used_unregistered_objects(monkeypatch):
    from contextlib import nullcontext
    rows = library() + [asset('unused-unknown.mp4', None)]
    edl = {'keep': [[0, 3]], 'music': [{'storage_key': 'legacy-music/track.mp3'}]}
    class Cursor:
        def execute(self, *args):
            pass
        def fetchall(self):
            return rows
    class Conn:
        def cursor(self):
            return nullcontext(Cursor())
    monkeypatch.setattr(db, 'get_edl_version', lambda c, p, v: {'json': edl} if v == 4 else None)
    looked_up = []
    def object_bytes(key):
        looked_up.append(key)
        return 8_000_000
    monkeypatch.setattr(storage, 'object_bytes', object_bytes)
    shape = db.project_execution_shape(Conn(), 2443, job_type='preview',
                                      payload={'edl_version': 4}, resolve_unknown=True)
    assert shape['staged_bytes'] == 418633 + 8_000_000
    assert looked_up == ['legacy-music/track.mp3']
