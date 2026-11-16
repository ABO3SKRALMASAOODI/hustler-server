import copy
import json
from types import SimpleNamespace as NS

import pytest
import edit_scope
from score import asks


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
    """Owner, 16 Nov: the editing model decides; edit_scope keeps disclosure only,
    and nothing the editor saved is rolled back after the turn."""
    import agent_tools
    import agent_loop
    import inspect
    assert not hasattr(edit_scope, "write_refusal") and not hasattr(edit_scope, "SYSTEM")
    assert not hasattr(edit_scope, "restore_unmet")
    assert "edit_scope.write_refusal" not in inspect.getsource(agent_tools)
    assert "restore_unmet" not in inspect.getsource(agent_loop)
