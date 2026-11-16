"""p5518 (ticket #77): a stopped edit cycle can still take back what it added."""
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import request_contract


def test_nothing_stops_new_work_after_a_count_of_renders():
    """p5518/p5533: a stopped cycle refused new work; the stop is gone and
    every write tool runs (owner, 16 Nov 2026)."""
    assert not hasattr(agent_loop, '_trial_render_refusal')
    assert not hasattr(agent_loop, '_count_trial_preview')


def _reply_ctx():
    return SimpleNamespace(job={'id': 1}, agent_model='m')


def test_a_reply_in_the_customers_language_is_left_alone(monkeypatch):
    monkeypatch.setattr(agent_loop.llm, 'create_with_dialect',
                        lambda *a, **k: pytest.fail('no rewrite expected'))
    text = 'أزلنا الشريط السفلي، لكن الشعار على الملابس ما زال ظاهرا.'
    assert agent_loop._enforce_reply_language(
        _reply_ctx(), None, [], [], text, 'شيل العلامة المائية', {}) == text


def test_preserve_of_the_source_never_locks_this_edits_lane(monkeypatch):
    """p5520: 'natural colours' locked the grade we added."""
    ctx = SimpleNamespace(
        request_contract_required=True, semantic_contract=None,
        user_message='Restore my original colours, remove the grade.',
        verification_request=None, request_contract_history=[],
        index={}, latest_edl=lambda: {'json': {'effects': {'grade': 'cinematic'}}})
    row = dict(description='Natural colour of the source', source_ref=0,
               action='preserve', lanes=['grade'], relative_to='source_footage')
    keep = dict(description='Keep the song', source_ref=0, action='preserve',
                lanes=['audio_mix'], relative_to='current_edit')
    monkeypatch.setattr(request_contract, '_call', lambda *a, **k: {
        'mode': 'edit', 'music_requested': False, 'timing_policy': 'edit',
        'output_count': 1, 'requirements': [row, keep]})
    contract = request_contract.ensure(ctx)
    assert 'grade' not in request_contract.protected_lanes(ctx)
    assert 'audio_mix' in request_contract.protected_lanes(ctx)
    assert all('relative_to' not in r for r in contract['requirements'])


def test_exposing_baked_cuts_is_not_a_cut_change():
    import scope_guard
    before = {'keep': [[0.0, 30.0]]}
    after = {'keep': [[0.0, 10.0], [10.0, 30.0]]}
    assert scope_guard.preservation_violations(
        before, after, protected={'cuts', 'timeline_timing'}) == []
    real = {'keep': [[0.0, 10.0], [12.0, 30.0]]}
    assert scope_guard.preservation_violations(
        before, real, protected={'cuts'})


def test_a_dissolve_junction_counts_as_a_delivered_transition():
    import quality_verifier
    import reply_grounding
    edl = {'keep': [[0, 5], [5, 10]], 'effects': {},
           'fx': [{'id': 'jn1', 'type': 'junction', 'style': 'dissolve', 'at': 5.0}]}
    assert reply_grounding.has_rendered_transitions(edl, {})
    assert not reply_grounding.has_rendered_transitions({**edl, 'fx': []}, {})
    source = open(quality_verifier.__file__).read()
    assert '"junction"' in source
