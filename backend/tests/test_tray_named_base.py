import contextlib
from types import SimpleNamespace

from flask import Flask
from routes import video
from video_services.media_selection import named_base, contains_alternate_versions


def _asset(aid, name):
    return {'id': aid, 'kind': 'video_clip', 'duration_s': 42,
            'storage_key': f'clips/{aid}.mp4', 'meta': {'filename': name}}


ASSETS = [_asset(1, 'ad-interrogation-FINAL.mp4'),
          _asset(2, 'ad-interrogation-NO-CAPTIONS.mp4'), _asset(3, 'shot-03.mp4')]
BRIEF = ('Upload ad-interrogation-NO-CAPTIONS.mp4. Do not use FINAL. Add captions. '
         'Upload ad-interrogation-FINAL.mp4 once the polish finishes for analysis.')


def test_named_edit_base_wins_over_a_later_reference_and_upload_order():
    assert named_base(ASSETS, BRIEF)['id'] == 2
    assert contains_alternate_versions(ASSETS)
    assert named_base(ASSETS, "Don't use ad-interrogation-FINAL.mp4.") is None


def test_unrelated_clips_and_explicit_combination_keep_tray_order():
    assert named_base(ASSETS, 'Compare ad-interrogation-FINAL.mp4 and ad-interrogation-NO-CAPTIONS.mp4.') is None
    assert not contains_alternate_versions([_asset(1, 'opening.mp4'), _asset(2, 'closing.mp4')])
    assert not contains_alternate_versions(ASSETS, 'Combine both versions in order.')


def test_tray_promotes_the_named_base_without_appending_alternate_edits(monkeypatch):
    patches, jobs, queries = {}, [], []
    cur = SimpleNamespace(execute=lambda sql, args=(): queries.append((sql, args)))

    @contextlib.contextmanager
    def db():
        yield SimpleNamespace(cursor=lambda: cur)

    monkeypatch.setattr(video, 'vdb', db)
    monkeypatch.setattr(video, '_project_for_user', lambda *_: {'chat_session_id': 12})
    monkeypatch.setattr(video, '_tray_rows', lambda *_: ASSETS)
    monkeypatch.setattr(video, '_active_original', lambda *_: None)
    monkeypatch.setattr(video, '_latest_edl', lambda *_: None)
    monkeypatch.setattr(video, '_upload_wall', lambda *_: False)
    monkeypatch.setattr(video, '_index_job_for_asset', lambda *_: None)
    monkeypatch.setattr(video, '_asset_meta_patch', lambda _c, aid, patch: patches.update({aid: patch}))

    def enqueue(_c, pid, uid, kind, payload):
        jobs.append(payload)
        return len(jobs)

    monkeypatch.setattr(video, '_enqueue', enqueue)
    app = Flask(__name__)
    with app.test_request_context(json={'order': [1, 2, 3], 'brief': BRIEF}):
        result = video.tray_submit.__wrapped__(7, 5).get_json()
    assert result['promoted_asset_id'] == 2
    assert result['selection_pool'] == 2
    assert all(not patch.get('tray_place') for patch in patches.values())
    assert len(jobs) == 3
    assert next(j for j in jobs if j['asset_id'] == 2)['selection_pool'] == 2
    assert any("SET kind = 'original'" in sql and args == (2,) for sql, args in queries)
