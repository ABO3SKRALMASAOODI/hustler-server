"""A preview's row is written before its checks: the Studio plays the row the
moment it exists, and the checks can take as long as the encode. The checks
still run, still reach the editor, and still drive First cut's repairs."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import first_cut
import renderer
from schemas import canvas_edl


CORNER = "GRAPHIC ON THE CORNER MARK: 'mg2' draws from y 0.1"


@pytest.fixture
def job(monkeypatch, tmp_path):
    edl = canvas_edl()
    edl['inserts'] = [dict(id='ins1', kind='video', asset_key='clip.mp4',
                           at_output_s=0, duration_s=4)]
    state = SimpleNamespace(assets=[], patches=[], deleted=[], order=[],
                            lost_at=None, patch_fails=False)
    monkeypatch.setattr(renderer.config, 'TMP_DIR', str(tmp_path))
    monkeypatch.setattr(renderer, '_caption_index_fp', lambda *a: 'fp')
    monkeypatch.setattr(renderer, 'render_edl', lambda edl, index, src, out,
                        workdir, **k: Path(out).write_bytes(b'video') and 4.)
    monkeypatch.setattr(renderer, '_verify_render', lambda *a, **k: None)
    monkeypatch.setattr(renderer.media, 'probe',
                        lambda p: dict(width=720, height=1280, fps=30))
    monkeypatch.setattr(renderer.storage, 'exists', lambda key: True)
    monkeypatch.setattr(renderer.storage, 'upload_file', lambda *a: None)
    monkeypatch.setattr(renderer.storage, 'delete_keys',
                        lambda keys: state.deleted.extend(keys))
    monkeypatch.setattr(renderer.sheets, 'build_result_sheet',
                        lambda v, path, *a, **k: Path(path).write_bytes(b'x'))
    monkeypatch.setattr(renderer.sheets, 'build_frames_sheet',
                        lambda v, path, *a, **k: Path(path).write_bytes(b'x'))
    monkeypatch.setattr(renderer.screening, 'plan', lambda *a, **k: [])

    def qc(*a, **k):
        state.order.append(('picture_qc', len(state.assets)))
        return {'findings': [CORNER]}

    def sound(*a, **k):
        state.order.append(('audio_qc', len(state.assets)))
        return {'lufs': -14}

    monkeypatch.setattr(renderer, 'picture_qc', qc)
    monkeypatch.setattr(renderer.audio_qc, 'measure', sound)

    class DB:
        def run(self, fn, *args, **kwargs):
            name = fn.__name__
            if name == 'user_is_paid':
                return False
            if name == 'video_settings':
                return {}
            if name == 'get_edl_version':
                return dict(version=args[1], json=edl)
            if name in {'latest_asset', 'find_render_asset',
                        'latest_render_asset'}:
                return None
            if name == 'set_progress':
                return args[1] != state.lost_at
            if name == 'insert_asset':
                state.order.append(('row', len(state.assets)))
                state.assets.append(dict(id=len(state.assets) + 1,
                                         storage_key=args[2], **kwargs))
                return len(state.assets)
            if name == 'update_asset_meta':
                if state.patch_fails:
                    raise RuntimeError('database went away')
                state.patches.append(args)
                state.assets[args[0] - 1]['meta'].update(args[1])
                return None
            raise AssertionError(name)

    def run(kind='preview', **payload):
        return renderer.run_render_job(DB(), dict(
            id=7, project_id=1, user_id=1, type=kind,
            payload=dict(edl_version=1, audio_model_review=False, **payload)))

    state.run = run
    return state


def test_the_row_is_written_before_the_checks_run(job):
    result = job.run(quality='approval')
    assert job.order == [('row', 0), ('picture_qc', 1), ('audio_qc', 1)]
    # the checks' result updates the row that is already on screen
    assert job.patches[0][0] == 1
    meta = job.assets[0]['meta']
    assert meta['picture_qc']['findings'] == [CORNER]
    assert meta['audio_qc'] == {'lufs': -14}
    assert meta['listen_keys'] == [] and meta['listen_clips'] == []
    assert result['render_asset_id'] == 1


def test_the_rows_first_write_says_the_checks_have_not_reported(job):
    seen = {}
    real = job.run

    def peek(*a, **k):
        seen.update(job.assets[0]['meta'])
        return {'findings': []}

    renderer.picture_qc, keep = peek, renderer.picture_qc
    try:
        real()
    finally:
        renderer.picture_qc = keep
    assert seen and 'picture_qc' not in seen and 'audio_qc' not in seen
    assert seen['variant'] == 'preview' and seen['render_job_id'] == 7


def test_the_timings_name_the_checks_and_when_the_picture_appeared(job):
    timings = job.run()['timings']
    assert timings['qc_s'] >= 0
    assert 0 <= timings['visible_s']
    assert 'register_s' in timings


def test_the_editor_still_gets_the_findings_on_the_jobs_result(job):
    assert job.run()['picture_qc']['findings'] == [CORNER]


def test_first_cut_still_clears_the_corner_mark_from_those_findings(
        job, monkeypatch):
    calls = []
    monkeypatch.setattr(first_cut.agent_tools, 'execute',
                        lambda ctx, name, args: calls.append((name, args))
                        or 'ok')
    findings = job.run()['picture_qc']['findings']
    # render_preview's note carries the findings verbatim
    note = 'Preview v1 rendered. PICTURE CHECK (measured on the rendered ' \
           'frames): ' + '; '.join(findings)
    assert first_cut._clear_corner_mark(SimpleNamespace(), [], note) is True
    assert ('remove_motion_graphic', {'id': 'mg2'}) in calls


def test_a_final_keeps_its_row_behind_its_checks(job):
    """A final's checks can hold the export, so it is not visible first."""
    result = job.run('final')
    assert job.order[0] == ('picture_qc', 0) and job.order[-1] == ('row', 0)
    assert job.patches == []
    assert job.assets[0]['meta']['picture_qc']['findings'] == [CORNER]
    assert result['timings']['qc_s'] >= 0


def test_a_final_abandoned_during_its_checks_still_leaves_nothing(job):
    job.lost_at = 98
    with pytest.raises(renderer.dbx.JobLeaseLost):
        job.run('final')
    assert job.assets == [] and job.deleted


def test_a_preview_abandoned_during_its_checks_keeps_its_picture(job):
    """It is a good render of an immutable version, maybe on screen already."""
    job.lost_at = 98
    with pytest.raises(renderer.dbx.JobLeaseLost):
        job.run()
    assert len(job.assets) == 1 and job.deleted == []
    assert job.assets[0]['meta']['picture_qc']['findings'] == [CORNER]


def test_failing_to_store_the_checks_does_not_fail_a_visible_preview(job):
    job.patch_fails = True
    assert job.run()['picture_qc']['findings'] == [CORNER]
    assert len(job.assets) == 1


class _Waiting:
    """A row found while the job that made it is still measuring it."""

    def __init__(self, polls_running, finish):
        self.polls, self.finish, self.calls = polls_running, finish, []

    def run(self, fn, *args, **kwargs):
        self.calls.append(fn.__name__)
        if fn.__name__ == 'get_job':
            self.polls -= 1
            return {'state': 'running' if self.polls >= 0 else 'done'}
        if self.polls < 0:
            return {'id': 1, 'meta': self.finish}
        return {'id': 1, 'meta': {'render_job_id': 5}}


def test_another_job_waits_for_the_checks_before_answering_from_cache(
        monkeypatch):
    monkeypatch.setattr(renderer.time, 'sleep', lambda s: None)
    db = _Waiting(2, {'render_job_id': 5, 'picture_qc': {'findings': ['x']}})
    asset = renderer._stored_render(db, 1, 'preview', 1, 9)
    assert asset['meta']['picture_qc'] == {'findings': ['x']}
    assert db.calls.count('get_job') == 3


def test_a_row_whose_job_died_answers_without_its_checks(monkeypatch):
    monkeypatch.setattr(renderer.time, 'sleep', lambda s: None)
    db = _Waiting(0, {'render_job_id': 5})
    asset = renderer._stored_render(db, 1, 'preview', 1, 9)
    assert 'picture_qc' not in asset['meta']
    assert db.calls.count('get_job') == 1


@pytest.mark.parametrize('meta', [
    {'render_job_id': 5, 'picture_qc': None},       # checked, nothing to say
    {'render_job_id': 9},                           # this job's own retry
    {},                                             # predates the checks
])
def test_a_settled_row_is_never_waited_for(meta):
    db = _Waiting(5, {})
    db.run = lambda fn, *a, **k: (db.calls.append(fn.__name__)
                                  or {'id': 1, 'meta': meta})
    assert renderer._stored_render(db, 1, 'preview', 1, 9)['meta'] == meta
    assert 'get_job' not in db.calls


def test_no_row_means_no_wait():
    db = SimpleNamespace(run=lambda fn, *a, **k: None)
    assert renderer._stored_render(db, 1, 'preview', 1, 9) is None
