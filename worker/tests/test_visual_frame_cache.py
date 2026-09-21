from types import SimpleNamespace as NS

import pytest
from PIL import Image

import frameserve
import visual_frame_cache as cache


@pytest.fixture
def setup(tmp_path, monkeypatch):
    objects = {}
    decoded = []
    def download(key, path, **kwargs):
        if key not in objects:
            raise FileNotFoundError(key)
        with open(path, 'wb') as f:
            f.write(objects[key])
    def upload(path, key, content_type):
        with open(path, 'rb') as f:
            objects[key] = f.read()
    monkeypatch.setattr(cache.storage, 'download_to', download)
    monkeypatch.setattr(cache.storage, 'upload_file', upload)
    def extract(ctx, asset, times, width, tag, measure_motion):
        decoded.append((list(times), width, measure_motion))
        paths = []
        for i, t in enumerate(times):
            path = str(tmp_path / f'extract_{i}.jpg')
            Image.new('RGB', (16, 16), (int(t)*20, 0, 0)).save(path)
            paths.append((i, path))
        return paths, None
    ctx = NS(project_id=2443, workdir=str(tmp_path), metrics={})
    asset = dict(sha256='source-content-hash', storage_key='uploads/2443/clip.mp4')
    return ctx, asset, extract, decoded, objects


def test_overlapping_looks_decode_only_missing_and_reuse_real_pixels_in_new_worker(setup, tmp_path):
    ctx, asset, extract, decoded, objects = setup
    first, error = cache.frames(ctx, asset, [1, 2], 640, 'look', False, extract)
    first_bytes = open(first[0][1], 'rb').read()
    second, _ = cache.frames(ctx, asset, [2, 3, 2], 640, 'look', False, extract)
    assert [d[0] for d in decoded] == [[1, 2], [3]]
    assert open(first[0][1], 'rb').read() == first_bytes  # scratch overwritten, cache isn't
    assert second[0][1] == second[2][1]
    new_dir = tmp_path / 'new-worker'
    new_dir.mkdir()
    ctx.workdir = str(new_dir)
    third, _ = cache.frames(ctx, asset, [1, 3], 640, 'look', False, extract)
    assert len(decoded) == 2
    assert len(third) == 2
    assert open(third[0][1], 'rb').read() == first_bytes
    assert all(k.startswith('thumbs/2443/') for k in objects)


def test_resolution_content_and_project_are_separate_cache_identities(setup):
    ctx, asset, extract, decoded, _ = setup
    cache.frames(ctx, asset, [1], 640, 'look', False, extract)
    cache.frames(ctx, asset, [1], 1920, 'look', False, extract)
    cache.frames(ctx, {**asset, 'sha256': 'changed'}, [1], 640, 'look', False, extract)
    ctx.project_id = 999
    cache.frames(ctx, asset, [1], 640, 'look', False, extract)
    assert len(decoded) == 4


def test_corrupt_cache_reextracts_and_missing_frames_are_not_fabricated(setup, tmp_path):
    ctx, asset, extract, decoded, objects = setup
    pairs, _ = cache.frames(ctx, asset, [1], 640, 'look', False, extract)
    for path in tmp_path.glob('source_frame*'):
        path.unlink()
    for key in objects:
        objects[key] = b'not a jpeg'
    pairs, error = cache.frames(ctx, asset, [1], 640, 'look', False, extract)
    assert pairs and not error and len(decoded) == 2
    def failed(*args):
        return [], 'executor unavailable'
    pairs, error = cache.frames(ctx, asset, [2], 640, 'look', False, failed)
    assert not pairs and error == 'executor unavailable'
    assert len(objects) == 1
    partial, error = cache.frames(ctx, asset, [1, 2], 640, 'look', False, failed)
    assert [i for i, path in partial] == [0]


def test_cache_is_optional_and_motion_measurement_is_not_skipped(setup, monkeypatch):
    ctx, asset, extract, decoded, _ = setup
    def unavailable(*args, **kwargs):
        raise OSError('cache unavailable')
    monkeypatch.setattr(cache.storage, 'download_to', unavailable)
    monkeypatch.setattr(cache.storage, 'upload_file', unavailable)
    pairs, error = cache.frames(ctx, asset, [1], 640, 'look', False, extract)
    assert pairs and not error
    cache.frames(ctx, asset, [1], 640, 'look', True, extract)
    cache.frames(ctx, {**asset, 'sha256': None}, [1], 640, 'look', False, extract)
    assert len(decoded) == 3 and decoded[1][2] is True


@pytest.mark.parametrize('size,motion,stream', [(100*1024*1024, False, True),
    (1024, False, False), (100*1024*1024, True, False), (None, False, False)])
def test_sparse_large_source_seeks_stream_but_full_motion_analysis_stages(tmp_path, monkeypatch, size, motion, stream):
    inputs, downloads, measured = [], [], []
    monkeypatch.setattr(frameserve.config, 'TMP_DIR', str(tmp_path))
    monkeypatch.setattr(frameserve.storage, 'object_bytes', lambda key: size)
    monkeypatch.setattr(frameserve.storage, 'presign_get', lambda key: 'https://storage.test/signed-source')
    monkeypatch.setattr(frameserve.storage, 'download_to', lambda key, path: downloads.append(path))
    monkeypatch.setattr(frameserve.storage, 'upload_file', lambda *a: None)
    monkeypatch.setattr(frameserve.media, 'frame_at', lambda path, *a, **k: inputs.append(path))
    monkeypatch.setattr(frameserve.media, 'probe', lambda path: {'duration': 10})
    monkeypatch.setattr(frameserve.motion_judge, 'analyze_video', lambda path, dur: measured.append(path) or {'version': 1})
    result = frameserve.run_frames_job(None, {'project_id': 2443, 'payload': {
        'storage_key': 'uploads/2443/video.mp4', 'times': [1, 3], 'motion_profile': motion}})
    assert len(result['keys']) == 2
    assert len(downloads) == (0 if stream else 1)
    assert inputs[0].startswith('https://') is stream
    assert bool(measured) is motion
