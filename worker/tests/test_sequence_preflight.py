import json
from types import SimpleNamespace

import sequence_preflight as sp


REQUEST = 'Unboxing step by step: open the box first, finish on the assembled machine.'


def _index():
    # The camera filmed the hero before the box. Output must follow the brief.
    return {'visual_storyboard': {'evidence': [
        {'representative_t': 2, 'semantic_description': 'Assembled coffee grinder on the counter'},
        {'representative_t': 12, 'semantic_description': 'Closed box being opened'}]}}


def _reply(earlier='block_2', later='block_1', quote='open the box first', confidence=.99):
    return json.dumps({'findings': [{'earlier': earlier, 'later': later,
        'request_quote': quote, 'reason': 'The assembled machine precedes the box opening.',
        'confidence': confidence}]})


def test_observations_follow_output_order_not_source_time():
    rows = sp.evidence({'keep': [[10, 15], [0, 5]]}, _index())
    assert rows[0]['observations'] == ['Closed box being opened']
    assert rows[1]['observations'] == ['Assembled coffee grinder on the counter']


def test_only_grounded_explicit_inversions_can_refuse_a_preview():
    rows = sp.evidence({'keep': [[0, 5], [10, 15]]}, _index())
    assert sp.grounded_findings(_reply(), rows, REQUEST)
    for answer in (_reply(earlier='invented'), _reply(quote='invented requested order'),
                   _reply(confidence=.8), _reply(earlier='block_1', later='block_2'), 'bad JSON'):
        assert not sp.grounded_findings(answer, rows, REQUEST)


def test_one_review_per_unchanged_version_and_none_for_plain_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(sp.llm, 'ask_text', lambda *a, **k: calls.append(1) or {'text': _reply()})
    ctx = SimpleNamespace(index=_index())
    row = {'version': 1, 'json': {'keep': [[0, 5], [10, 15]]}}
    assert 'No full preview' in sp.check(ctx, row, REQUEST)
    assert sp.check(ctx, row, REQUEST)
    assert calls == [1]
    assert sp.check(ctx, row, 'Make the captions larger') is None


def test_order_refusal_happens_before_render_enqueue(monkeypatch):
    import agent_tools as at
    row = {'version': 1, 'json': {'keep': [[0, 5], [10, 15]]}}
    ctx = SimpleNamespace(index=_index(), user_message=REQUEST,
        latest_edl=lambda: row, rendered_versions=set(), last_preview=None,
        failed_preview_versions={})
    monkeypatch.setattr(at, '_claude_lane_turn', lambda _: True)
    monkeypatch.setattr(at, '_verify_plan_for', lambda *_: None)
    monkeypatch.setattr(sp.llm, 'ask_text', lambda *a, **k: {'text': _reply()})
    # No db/enqueue seam exists on this context: a full render cannot begin.
    result = at.render_preview(ctx, complete=True, quality='approval')
    assert 'No full preview was started' in result


def test_cleanup_cannot_silently_monopolize_a_cold_open_request():
    request = 'Build a 60s cold open, then logo, then the full episode.'
    assert sp.cleanup_reminder(request, ['cut_range'] * 6)
    assert not sp.cleanup_reminder(request, ['cut_range'] * 7)
    assert not sp.cleanup_reminder(request, ['cut_range'] * 5 + ['insert_media'])
    assert not sp.cleanup_reminder('Only remove silences.', ['cut_range'] * 6)


def test_sequence_check_preserves_the_existing_render_allowance():
    import agent_loop
    ctx = SimpleNamespace(trial_preview_cap=2, trial_previews=0)
    agent_loop._count_trial_preview(ctx, sp.REFUSAL_PREFIX + ' Correct the order.')
    assert ctx.trial_previews == 0
    assert agent_loop._trial_render_refusal(ctx, 'keep_segments') is None
    for version in range(1, 3):
        ctx.last_preview = {'edl_version': version, 'asset_id': version}
        agent_loop._count_trial_preview(ctx, 'Preview rendered and attached')
    assert ctx.trial_previews == 2
    assert agent_loop._trial_render_refusal(ctx, 'render_preview').startswith('REJECTED')


def test_inserted_logo_report_is_cached_and_a_changed_brief_is_rechecked(monkeypatch):
    calls = []
    request = 'Cold open, logo before full episode.'
    monkeypatch.setattr(sp.llm, 'ask_text', lambda *a, **k: calls.append(1) or {
        'text': _reply(earlier='block_3', quote='logo before full episode')})
    ctx = SimpleNamespace(index=_index(), project_id=1,
        db=SimpleNamespace(run=lambda *_: {'meta': {'filename': 'brand-logo.png'}}))
    row = {'version': 1, 'json': {'keep': [[0, 5], [10, 15]], 'inserts': [{
        'id': 'logo', 'asset_key': 'logo.png', 'kind': 'image',
        'at_output_s': 10, 'duration_s': 2}]}}
    assert sp.check(ctx, row, request)
    assert sp.check(ctx, row, request)
    assert calls == [1]
    assert sp.check(ctx, row, request + ' Keep all speech.')
    assert calls == [1, 1]
