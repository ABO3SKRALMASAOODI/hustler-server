from copy import deepcopy
import ask_ledger
import reply_grounding as rg
import trial_reply as tr
from test_unmet_ask_hold import _ctx
import agent_loop

BASE = {'keep': [[0, 30]], 'effects': {}}


def test_single_shot_preset_never_claims_transitions():
    after = deepcopy(BASE)
    after['effects']['transition'] = {'style': 'zoom_punch', 'scope': 'scene'}
    facts = tr.Facts(before=BASE, after=after, wrote=True, previewed=True,
                     user_text='Add effects and captions')
    reply, _ = tr.finish('Your edit is updated.', facts)
    assert 'added transitions' not in reply
    assert tr.unbacked('I added transitions.', facts) == 'absent:transition'


def test_transition_requires_real_scene_junctions():
    edl = {'keep': [[0, 10], [12, 30]], 'effects': {'transition': {'style': 'zoom_punch'}}}
    assert not rg.has_rendered_transitions(edl, {'shots': [{'id': 1, 'start': 0, 'end': 30}]})
    assert rg.has_rendered_transitions(edl, {'shots': [{'id': 1, 'start': 0, 'end': 11}, {'id': 2, 'start': 11, 'end': 30}]})
    edl['effects']['transition']['style'] = 'cut'
    assert not rg.has_rendered_transitions(edl)


def test_missing_transition_is_held_and_disclosed_before_preview():
    ask = 'Make a fishing film with cinematic transitions and music.'
    assert ask_ledger.ledger(ask, BASE, BASE)['transitions'] == 'not_done'
    ctx = _ctx(BASE)
    hold = agent_loop._unmet_ask_hold(ctx, 'render_preview', {'complete': True}, ask)
    assert hold and 'set_transitions' in hold
    reply, report = tr.finish('Still open from your request: The user explicitly requested transitions, but the latest EDL contains no transition treatment.', tr.Facts(before=BASE, after=BASE, wrote=True, user_text=ask))
    assert "didn't add transitions" in reply
    assert 'transitions' in report['unmet']


def test_new_preset_does_not_reintroduce_false_claim_through_ask_ledger():
    before = {'keep': [[0, 10], [12, 30]], 'effects': {}}
    after = deepcopy(before)
    after['effects']['transition'] = {'style': 'zoom_punch'}
    index = {'shots': [{'id': 1, 'start': 0, 'end': 30}]}
    reply, report = tr.finish('I added transitions.', tr.Facts(
        before=before, after=after, index=index, wrote=True,
        user_text='Add transitions.'))
    assert report.get('unmet') == ['transitions']
    assert "didn't add transitions" in reply
    assert 'I added transitions.' not in reply
