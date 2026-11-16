import copy
import json
from types import SimpleNamespace as NS

import pytest
import edit_scope
from score import asks


@pytest.mark.parametrize('failure', ['unmet', 'check_unavailable', 'ledger_unavailable', 'harm_restore'])
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
    if failure == 'harm_restore':
        # Actual p5533 check: the curtain partly worked, but a shirt hole
        # required restoration and could not be assigned a removable item id.
        verdict['asks'][0].update(state='partly', line='The curtain covers most of the background.')
        verdict['unasked'] = [{'what': 'The composite damages clothing.',
                              'evidence': 'A dark hole in the white shirt at 13.2s.',
                              'items': [], 'fix': 'restore'}]
    elif failure != 'unmet':
        verdict = None
    if failure == 'ledger_unavailable':
        ledger = None
    assert edit_scope.restore_unmet(ctx, ledger, verdict)
    assert saved[0] == ('edl', before)
    assert saved[1][1] == 'original-preview.mp4'
    assert saved[1][2]['edl_version'] == 10
    assert ctx.last_preview['screening_pages'] == ['before.jpg']
    if failure == 'harm_restore':
        assert ctx.ask_verdict is None  # the discarded composite proves nothing about the restored film
        assert ctx.unmet_edit_restored['harm'][0]['fix'] == 'restore'


@pytest.mark.parametrize('state', ['done', 'partly'])
def test_partial_requested_delivery_is_retained(state):
    ctx = NS(turn_start_edl={'json': {'keep': [[0, 4]]}})
    assert not edit_scope.restore_unmet(ctx, {'asks': [{'id': 'a1', 'kind': 'edit'}]},
                                       {'asks': [{'id': 'a1', 'state': state}]})


def test_old_restore_finding_cannot_roll_back_a_newer_film():
    ctx = NS(turn_start_edl={'json': {'fx': []}, 'version': 8},
             latest_edl=lambda: {'json': {'fx': [{'id': 'repaired'}]}, 'version': 10})
    verdict = {'edl_version': 9, 'asks': [{'id': 'a1', 'state': 'partly'}],
               'unasked': [{'fix': 'restore', 'evidence': 'Old shirt hole'}]}
    assert not edit_scope.restore_unmet(ctx, {'asks': [{'id': 'a1', 'kind': 'edit'}]}, verdict)


def test_restored_reply_explains_discarded_damage_without_claiming_success(monkeypatch):
    harm = [{'fix': 'restore', 'what': 'The attempted composite damaged clothing.',
             'evidence': 'A hole in the shirt.'}]
    def check(ctx, name, schema, system, payload, **kw):
        data = json.loads(payload)
        assert data['rollback_harm'] == harm
        assert data['retained_changes'] == {}
        return {'reply': 'The background attempt damaged the shirt, so I restored your previous film. The background change was not kept.',
                'covered': [], 'offered_alternative': None}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(turn_start_edl={'json': {'fx': []}}, latest_edl=lambda: {'json': {'fx': []}},
             unmet_edit_restored={'harm': harm})
    assert 'was not kept' in edit_scope.disclose(ctx, 'Could not verify.')


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
    assert ctx.retained_reply_facts['fields'] == ['unchanged', 'alternative']
    assert offer in ctx.retained_reply_facts['description']


def test_disclosure_cannot_silently_drop_available_alternative(monkeypatch):
    monkeypatch.setattr(asks, '_call', lambda *a, **k: (
        {'reply': 'Animation failed.', 'covered': [], 'offered_alternative': None}, {}))
    ctx = NS(turn_start_edl={'json': {}}, latest_edl=lambda: {'json': {}},
             edit_alternatives=[{'offer': 'I can use your still reference.'}])
    assert 'I can use your still reference.' in edit_scope.disclose(ctx, 'Animation failed.')


def test_unchanged_failure_keeps_the_honest_clean_original_question(monkeypatch):
    reply = 'الفيديو لم يتغير. هل يمكنك رفع النسخة الأصلية من دون العلامة؟'
    verdict = {'asks': [{'id': 'a1', 'state': 'not_done',
                        'seen': 'The mark remains across the face.',
                        'line': 'العلامة ما زالت ظاهرة.'}]}
    def check(ctx, name, schema, system, payload, **kw):
        if name == 'disclose_retained_changes':
            return {'reply': reply, 'covered': [], 'offered_alternative': None}, {}
        assert name == 'record_true_reply'
        return {'wrong': [], 'reply': '', 'covered': ['unchanged']}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(turn_start_edl={'json': {}}, latest_edl=lambda: {'json': {}},
             ask_verdict=verdict)
    out = edit_scope.disclose(ctx, reply)
    assert asks.true_reply(ctx, {'asks': [{'id': 'a1', 'said': 'Remove the mark'}]},
                           verdict, out) == reply


@pytest.mark.parametrize('relevant', [True, False])
def test_unsupported_reply_can_offer_a_real_upload_without_failed_tool_call(monkeypatch, relevant):
    def check(ctx, name, schema, system, payload, **kw):
        data = json.loads(payload)
        assert data['alternatives_optional'] is True
        assert data['real_alternatives'][0]['assets'][0]['asset_key'] == 'uploaded-reference.png'
        return {'reply': 'The film is unchanged.' + (' I can show the reference as a still.' if relevant else ''),
                'covered': [], 'offered_alternative': 0 if relevant else None}, {}
    monkeypatch.setattr(asks, '_call', check)
    ctx = NS(turn_start_edl={'json': {}}, latest_edl=lambda: {'json': {}},
             available_still_assets=[{'asset_key': 'uploaded-reference.png'}],
             ask_verdict={'asks': [{'id': 'a1', 'state': 'not_done'}]},
             user_message='Use the hair-down reference in the animation' if relevant else 'Remove the wind noise')
    reply = edit_scope.disclose(ctx, 'The requested operation is unavailable.')
    assert 'unchanged' in reply
    assert ('as a still' in reply) is relevant



def test_no_second_model_authorizes_the_editors_writes():
    """Owner, 16 Nov: the editing model decides; edit_scope keeps recovery and disclosure only."""
    import agent_tools
    import inspect
    assert not hasattr(edit_scope, "write_refusal") and not hasattr(edit_scope, "SYSTEM")
    assert "edit_scope.write_refusal" not in inspect.getsource(agent_tools)
