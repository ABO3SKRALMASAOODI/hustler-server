"""Visual evidence follows the encoded picture, while audio checks stay fresh."""
import copy
from pathlib import Path

import pytest

import renderer
from schemas import canvas_edl


@pytest.fixture
def render_sequence(monkeypatch, tmp_path):
    edl = canvas_edl()
    edl['inserts'] = [dict(id='ins1', kind='video', asset_key='clip.mp4',
                          at_output_s=0, duration_s=4)]
    edl['captions'] = {'mode': 'from_transcript'}
    changed = copy.deepcopy(edl)
    changed['master'] = {'loudness': 'social'}
    rows = {1: dict(version=1, json=edl), 2: dict(version=2, json=changed)}
    assets, uploads, deleted, builds, audio_checks = [], [], [], [], []
    cancel = [False]
    monkeypatch.setattr(renderer.config, 'TMP_DIR', str(tmp_path))
    monkeypatch.setattr(renderer, '_caption_index_fp', lambda *a: 'same-captions')
    monkeypatch.setattr(renderer, 'caption_review_times', lambda *a, **k: [1., 2.])

    def render(edl, index, source, output, workdir, **kwargs):
        Path(output).write_bytes(b'valid-test-artifact')
        return 4.

    def reuse(previous, current, asset, index, source, workdir, output, **kwargs):
        if not renderer.render_plan.can_reuse_picture(previous, current):
            return None
        return render(current, index, source, output, workdir)

    monkeypatch.setattr(renderer, 'render_edl', render)
    monkeypatch.setattr(renderer, '_reuse_picture_with_new_audio', reuse)
    monkeypatch.setattr(renderer, '_verify_render', lambda *a, **k: None)
    monkeypatch.setattr(renderer.media, 'probe', lambda p: dict(width=320, height=180, fps=30))
    monkeypatch.setattr(renderer.storage, 'exists', lambda key: True)
    monkeypatch.setattr(renderer.storage, 'upload_file', lambda p, key, mime: uploads.append(key))
    monkeypatch.setattr(renderer.storage, 'delete_keys', lambda keys: deleted.extend(keys))

    def build(video, path, *_args, **_kwargs):
        builds.append(path)
        Path(path).write_bytes(b'review-image')

    def measure(*args, **kwargs):
        audio_checks.append(len(audio_checks) + 1)
        return {'measurement': audio_checks[-1]}

    monkeypatch.setattr(renderer.sheets, 'build_result_sheet', build)
    monkeypatch.setattr(renderer.sheets, 'build_frames_sheet', build)
    monkeypatch.setattr(renderer.audio_qc, 'measure', measure)
    monkeypatch.setattr(renderer.screening, 'plan', lambda *a, **k:
        [{'time_s': 1., 'reason': 'opening'}, *(k.get('extra_frames') or [])])

    class DB:
        def run(self, fn, *args, **kwargs):
            name = fn.__name__
            if name == 'user_is_paid': return True
            if name == 'video_settings': return {}
            if name == 'get_edl_version': return rows[args[1]]
            if name in {'latest_asset', 'find_render_asset'}: return None
            if name == 'latest_render_asset': return assets[-1] if assets else None
            if name == 'set_progress': return not (cancel[0] and args[1] == 96)
            if name == 'insert_asset':
                assets.append(dict(id=len(assets)+1, storage_key=args[2], **kwargs))
                return len(assets)
            raise AssertionError(name)

    def run(version, **payload):
        return renderer.run_render_job(DB(), dict(id=version, project_id=1,
            user_id=1, type='preview', payload=dict(edl_version=version,
            quality='approval', audio_model_review=False, **payload)))

    return run, rows, assets, uploads, deleted, builds, audio_checks, cancel


def test_sound_change_reuses_visual_pages_but_measures_new_audio(render_sequence):
    run, rows, assets, uploads, deleted, builds, audio, cancel = render_sequence
    first = run(1)
    old_builds, old_uploads = len(builds), len(uploads)
    second = run(2)
    assert len(builds) == old_builds
    assert len(uploads) == old_uploads + 1  # only the new playable video
    for field in ('sheet_key', 'screening_pages', 'caption_pages'):
        assert second[field] == first[field]
    assert second['audio_qc'] != first['audio_qc']
    assert len(audio) == 2
    assert assets[-1]['meta']['edl_version'] == 2
    assert second['timings']['reused_visual_evidence_pages'] == 3


def test_new_requested_visual_moment_rebuilds_screening(render_sequence):
    run, rows, assets, uploads, deleted, builds, audio, cancel = render_sequence
    first = run(1)
    second = run(2, screening_frames=[{'time_s':3., 'reason':'new request'}])
    assert second['sheet_key'] == first['sheet_key']
    assert second['caption_pages'] == first['caption_pages']
    assert second['screening_pages'][0]['key'] != first['screening_pages'][0]['key']
    assert len(second['screening_pages'][0]['frames']) == 2


def test_visual_change_does_not_reuse_review_images(render_sequence):
    run, rows, assets, uploads, deleted, builds, audio, cancel = render_sequence
    first = run(1)
    rows[2]['json']['frame'] = {'ratio':'1:1'}
    second = run(2)
    assert second['sheet_key'] != first['sheet_key']
    assert second['screening_pages'] != first['screening_pages']
    assert 'reused_visual_evidence_pages' not in second['timings']


def test_cancelled_remix_preserves_prior_render_and_evidence(render_sequence):
    run, rows, assets, uploads, deleted, builds, audio, cancel = render_sequence
    run(1)
    old_keys = set(uploads)
    cancel[0] = True
    with pytest.raises(renderer.dbx.JobLeaseLost):
        run(2)
    assert not old_keys.intersection(deleted)
    assert set(deleted) == set(uploads) - old_keys
    assert len(assets) == 1


def test_evidence_layout_and_missing_keys_refuse_reuse():
    pages = [{'key':'old.jpg', 'times':[1.,2.]}]
    assert not renderer._matching_evidence_pages(pages, [[1.],[2.]], 'times')
    assert not renderer._matching_evidence_pages([{'times':[1.,2.]}], [[1.,2.]], 'times')
