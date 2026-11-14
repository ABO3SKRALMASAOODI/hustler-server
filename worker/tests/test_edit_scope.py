import copy
import json
from types import SimpleNamespace as NS

import pytest
import edit_scope
from score import asks


@pytest.mark.parametrize('editor', ['gpt-6.1-sol', 'claude-haiku-5-5'])
def test_scope_checks_real_delta_on_both_editors(monkeypatch, editor):
    before = {'frame': {'ratio': '9:16', 'fit': 'cover'}, 'texts': [{'id': 'title', 'text': 'A PAPER SNOWFLAKE'}]}
    after = {'frame': {'ratio': '9:16', 'fit': 'pad_blur'}, 'texts': []}
    seen = []
    def check(ctx, name, schema, system, payload, **kwargs):
        data = json.loads(payload)
        assert data['changes']['texts']['before'] == before['texts']
        assert data['changes']['frame']['after']['fit'] == 'pad_blur'
        assert kwargs['model'] == 'gpt-6-sol'
        seen.append(name)
        return {'allowed': False, 'reason': 'The requested replacement does not require a reframe or title deletion.'}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(ask_check_required=True, user_message='Use my replacement character image',
             turn_start_edl={'json': before}, agent_model=editor)
    assert 'reframe' in edit_scope.write_refusal(ctx, before, after)
    assert edit_scope.write_refusal(ctx, before, after)
    assert len(seen) == 1
    assert edit_scope.write_refusal(ctx, after, before) is None


def test_scope_checker_failure_cannot_authorize_unrelated_work(monkeypatch):
    monkeypatch.setattr(asks, '_call', lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    ctx = NS(ask_check_required=True, user_message='Change the character')
    assert 'Keep the existing film' in edit_scope.write_refusal(ctx, {'texts': [1]}, {'texts': []})


def test_requested_change_allowed(monkeypatch):
    monkeypatch.setattr(asks, '_call', lambda *a, **k: ({'allowed': True, 'reason': 'Requested'}, {}))
    ctx = NS(ask_check_required=True, user_message='Remove the title')
    assert edit_scope.write_refusal(ctx, {'texts': [1]}, {'texts': []}) is None


@pytest.mark.parametrize('failure', ['unmet', 'check_unavailable', 'ledger_unavailable'])
def test_unmet_edit_restores_exact_edl_and_existing_preview_atomically(monkeypatch, failure):
    import db
    before = {'keep': [[0, 4.566]], 'frame': {'fit': 'cover'}, 'texts': [{'text': 'A PAPER SNOWFLAKE'}]}
    after = dict(before, frame={'fit': 'pad_blur'}, texts=[])
    saved = []
    def insert(conn, pid, edl, by, **kw):
        assert kw['before_version'] == 9
        saved.append(('edl', copy.deepcopy(edl)))
        return 10
    monkeypatch.setattr(db, 'insert_edl', insert)
    monkeypatch.setattr(db, 'find_render_asset', lambda *a: {'kind': 'render', 'storage_key': 'original-preview.mp4', 'meta': {'edl_version': 8, 'variant': 'preview'}})
    monkeypatch.setattr(db, 'insert_asset', lambda *a, **k: saved.append(('preview', a[3], k['meta'])))
    monkeypatch.setattr(asks, 'stored_preview', lambda *a: {'edl_version': 8, 'screening_pages': ['before.jpg']})
    ctx = NS(turn_start_edl={'json': before, 'version': 8}, latest_edl=lambda: {'json': after, 'version': 9},
             project_id=1, versions_written=[9], db=NS(run=lambda fn: fn(object())),
             ask_check_required=True)
    ledger = {'asks': [{'id': 'a1', 'kind': 'edit'}]}
    verdict = {'asks': [{'id': 'a1', 'state': 'not_done'}], 'edl_version': 9}
    if failure != 'unmet':
        verdict = None
    if failure == 'ledger_unavailable':
        ledger = None
    assert edit_scope.restore_unmet(ctx, ledger, verdict)
    assert saved[0] == ('edl', before)
    assert saved[1][1] == 'original-preview.mp4'
    assert saved[1][2]['edl_version'] == 10
    assert ctx.last_preview['screening_pages'] == ['before.jpg']


@pytest.mark.parametrize('state', ['done', 'partly'])
def test_partial_requested_delivery_is_retained(state):
    ctx = NS(turn_start_edl={'json': {'keep': [[0, 4]]}})
    assert not edit_scope.restore_unmet(ctx, {'asks': [{'id': 'a1', 'kind': 'edit'}]},
                                       {'asks': [{'id': 'a1', 'state': state}]})


def test_reply_discloses_actual_delta_even_when_main_ask_failed(monkeypatch):
    def check(ctx, name, schema, system, payload, **kw):
        data = json.loads(payload)
        assert data['retained_changes']['texts']['after'] == []
        return {'reply': 'The replacement failed. The title was removed.', 'covered': ['texts']}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(turn_start_edl={'json': {'texts': [{'text': 'Title'}]}},
             latest_edl=lambda: {'json': {'texts': []}})
    assert 'title was removed' in edit_scope.disclose(ctx, 'The replacement failed.')


@pytest.mark.parametrize('check_available', [True, False])
def test_unchanged_failed_turn_offers_real_still_alternative(monkeypatch, check_available):
    offer = 'I can show your uploaded reference as a still; it will not animate.'
    def check(ctx, name, schema, system, payload, **kw):
        data = json.loads(payload)
        assert data['retained_changes'] == {}
        assert data['real_alternatives'][0]['offer'] == offer
        if not check_available:
            raise TimeoutError()
        return {'reply': 'The film is unchanged. ' + offer,
                'covered': [], 'offered_alternative': 0}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(turn_start_edl={'json': {'texts': [{'text': 'Title'}]}},
             latest_edl=lambda: {'json': {'texts': [{'text': 'Title'}]}},
             edit_alternatives=[{'offer': offer}])
    reply = edit_scope.disclose(ctx, 'Animation failed.')
    assert 'unchanged' in reply
    assert offer in reply


def test_disclosure_cannot_silently_drop_available_alternative(monkeypatch):
    monkeypatch.setattr(asks, '_call', lambda *a, **k: (
        {'reply': 'Animation failed.', 'covered': [], 'offered_alternative': None}, {}))
    ctx = NS(turn_start_edl={'json': {}}, latest_edl=lambda: {'json': {}},
             edit_alternatives=[{'offer': 'I can use your still reference.'}])
    assert 'I can use your still reference.' in edit_scope.disclose(ctx, 'Animation failed.')
