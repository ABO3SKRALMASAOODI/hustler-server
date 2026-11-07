"""p5518 (ticket #77): a stopped edit cycle can still take back what it added."""
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import preview_budget
import request_contract


def stopped(version=17):
    row = {'version': version, 'json': {}}
    ctx = SimpleNamespace(latest_edl=lambda: row, trial_preview_cap=2,
                          trial_previews=3, trial_preview_versions=[6, 10, 13],
                          last_preview={'edl_version': version, 'asset_id': 1},
                          verification_records={}, rendered_versions=(),
                          trial_preview_repairs={'history': [
                              {'version': 13, 'remaining': 1, 'findings': []}]},
                          row=row)
    return ctx


def rendered(ctx, version):
    ctx.row['version'] = version
    ctx.last_preview = {'edl_version': version, 'asset_id': 2}
    agent_loop._count_trial_preview(ctx, 'Preview rendered')


def test_undo_tools_are_real_write_tools():
    assert preview_budget.UNDO_TOOLS <= set(agent_tools.WRITE_TOOLS)
    assert 'reset_edit' not in preview_budget.UNDO_TOOLS
    assert 'remove_filler_words' not in preview_budget.UNDO_TOOLS


def test_new_work_stays_refused_but_the_turn_can_undo_and_show_it():
    ctx = stopped()
    assert agent_loop._trial_render_refusal(ctx, 'blur_region').startswith('REJECTED')
    assert agent_loop._trial_render_refusal(ctx, 'remove_blur') is None
    # nothing was actually removed yet: no render of the same version
    assert 'REJECTED' in agent_loop._trial_render_refusal(ctx, 'render_preview')
    ctx.row['version'] = 18
    assert agent_loop._trial_render_refusal(ctx, 'render_preview') is None
    assert agent_loop._trial_render_refusal(ctx, 'add_text').startswith('REJECTED')
    rendered(ctx, 18)
    assert ctx.trial_preview_repairs.get('undo_from') is None
    assert ctx.trial_preview_repairs['undo_renders'] == 1
    assert 'REJECTED' in agent_loop._trial_render_refusal(ctx, 'render_preview')


def test_a_failed_undo_render_can_be_retried():
    ctx = stopped()
    agent_loop._trial_render_refusal(ctx, 'remove_blur')
    ctx.row['version'] = 18
    assert agent_loop._trial_render_refusal(ctx, 'render_preview') is None
    # the render failed: last_preview never moved
    agent_loop._count_trial_preview(ctx, 'TRANSIENT_FAILURE')
    assert agent_loop._trial_render_refusal(ctx, 'render_preview') is None


def test_undo_is_bounded_and_survives_a_checkpoint():
    ctx = stopped()
    for version in (18, 19):
        assert agent_loop._trial_render_refusal(ctx, 'remove_erase') is None
        ctx.row['version'] = version
        assert agent_loop._trial_render_refusal(ctx, 'render_preview') is None
        rendered(ctx, version)
    assert agent_loop._trial_render_refusal(ctx, 'remove_erase').startswith('REJECTED')
    resumed = stopped(19)
    preview_budget.restore(resumed, preview_budget.checkpoint(ctx))
    assert agent_loop._trial_render_refusal(resumed, 'remove_erase').startswith('REJECTED')


def test_an_open_undo_survives_a_checkpoint():
    ctx = stopped()
    agent_loop._trial_render_refusal(ctx, 'remove_blur')
    ctx.row['version'] = 18
    resumed = stopped(17)
    resumed.row['version'] = 18
    preview_budget.restore(resumed, preview_budget.checkpoint(ctx))
    assert agent_loop._trial_render_refusal(resumed, 'render_preview') is None


def test_the_refusal_names_the_way_back():
    assert 'remove_blur' in agent_loop._trial_render_refusal(stopped(), 'add_text')


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
