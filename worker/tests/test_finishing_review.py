"""A known defect stays actionable across writes; clean work can finish."""
from types import SimpleNamespace as NS
import json

import finishing_review
import llm
from schemas import canvas_edl


def context():
    edl = canvas_edl('16:9')
    edl['keep'] = []
    edl['inserts'] = [dict(id=f'ins{i}', kind='video', asset_key=f'clip{i}',
                         at_output_s=i*4, duration_s=4, source_start_s=0)
                      for i in range(11)]
    edl['effects'] = {'transition': dict(style='dip_black', duration_s=.35, scope='scene')}
    row = {'version': 73, 'json': edl}
    ctx = NS(versions_written=[73], index={'video': {'width': 1920, 'height': 1080}},
             verification_request='Make a wedding video', verification_records={},
             latest_edl=lambda: row)
    return ctx, row


def test_real_transition_defect_is_found_before_render_and_survives_unrelated_write():
    ctx, row = context()
    messages = []
    finishing_review.refresh(ctx, messages)
    assert "10 'dip_black' transitions" in messages[-1]['content']
    assert "set_transitions('none')" in messages[-1]['content']
    row['version'] += 1
    row['json']['effects']['grade'] = 'warm'
    finishing_review.refresh(ctx, messages)
    assert len(messages) == 1
    assert 'v74' in messages[0]['content']
    assert "10 'dip_black' transitions" in messages[0]['content']


def test_repair_requires_current_preview_and_pass_does_not_reopen_the_defect():
    ctx, row = context()
    finding = {'message': finishing_review.current_findings(ctx, row)[0]}
    ctx.verification_records[73] = dict(complete_preview_passed=True,
                                      unresolved_findings=[finding])
    row['version'] = 74
    row['json']['effects']['transition'] = None
    note = finishing_review.directive(ctx)
    assert 'prior-version findings' in note
    assert 'Not yet verified' in note
    ctx.verification_records[74] = dict(status='passed', complete_preview_passed=True,
                                      unresolved_findings=[])
    note = finishing_review.directive(ctx)
    assert 'finish now with the saved result' in note
    assert 'dip_black' not in note


def test_current_justified_finding_is_not_reopened():
    ctx, row = context()
    findings = [{'message': f} for f in finishing_review.current_findings(ctx, row)]
    ctx.verification_records[73] = dict(status='justified', complete_preview_passed=True,
                                      findings=findings, unresolved_findings=[])
    assert 'finish now' in finishing_review.directive(ctx)


def test_new_user_turn_is_not_closed_by_an_old_pass():
    ctx, row = context()
    ctx.versions_written = []
    ctx.verification_records[73] = dict(status='passed', complete_preview_passed=True,
                                      unresolved_findings=[])
    assert finishing_review.directive(ctx) is None


def test_missing_preview_is_not_instructed_to_repair_itself_before_render():
    ctx, row = context()
    row['json']['effects']['transition'] = None
    ctx.verification_records[73] = dict(status='pending', unresolved_findings=[
        dict(code='complete_preview_missing', message='render one complete preview')])
    assert finishing_review.directive(ctx) is None


def test_critic_reasoning_starvation_gets_one_accounted_bounded_retry(monkeypatch):
    calls, records = [], []
    monkeypatch.setattr(llm.config, 'OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(llm, 'client', lambda: object())
    monkeypatch.setattr(llm, 'record', lambda *args: records.append(args))
    def create(*args, **kwargs):
        calls.append(kwargs)
        text = '' if len(calls) == 1 else '{"verdict":"pass","findings":[]}'
        usage = NS(completion_tokens=1100, prompt_tokens=100,
                   completion_tokens_details=NS(reasoning_tokens=1100))
        return NS(choices=[NS(message=NS(content=text))], usage=usage)
    monkeypatch.setattr(llm, 'create_with_dialect', create)
    result = llm.ask_text('review', 'evidence', max_tokens=1100,
                          reasoning_effort='low', retry_empty=True)
    assert 'pass' in result['text']
    assert [c['max_tokens'] for c in calls] == [1100, 3300]
    assert len(records) == 2
    assert records[0][2]['starved_at_max_tokens'] == 1100


def test_empty_nonstarved_critic_does_not_retry_or_invent_a_pass(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.config, 'OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(llm, 'client', lambda: object())
    monkeypatch.setattr(llm, 'record', lambda *args: None)
    def create(*args, **kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=''))],
                  usage=NS(completion_tokens=0, prompt_tokens=1))
    monkeypatch.setattr(llm, 'create_with_dialect', create)
    assert llm.ask_text('review', 'evidence', retry_empty=True) is None
    assert len(calls) == 1


def test_critic_retry_exhaustion_stops_after_two_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.config, 'OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(llm, 'client', lambda: object())
    monkeypatch.setattr(llm, 'record', lambda *args: None)
    def create(*args, **kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content=''))],
                  usage=NS(completion_tokens=kwargs['max_tokens'], prompt_tokens=1))
    monkeypatch.setattr(llm, 'create_with_dialect', create)
    assert llm.ask_text('review', 'evidence', retry_empty=True) is None
    assert len(calls) == 2


def test_live_loop_repairs_known_metadata_before_render_then_finishes(monkeypatch):
    import agent_loop
    import agent_tools
    from test_director_blueprint import _tool_ctx

    ctx, db = _tool_ctx()
    _sample_ctx, row = context()
    db.rows = [row]
    ctx.versions_written = [73]
    ctx.verification_request = 'Make a wedding video'
    ctx.user_message = ctx.verification_request
    ctx.turn_start_edl = row
    ctx.turn_baseline_digest = 'before'
    ctx.over_budget = lambda: False
    ctx.adopted_steer_job_ids = set()
    ctx.verification_records[73] = dict(status='repair_required', complete_preview_passed=True,
        unresolved_findings=[dict(message="10 'dip_black' transitions", code='review_finding')])
    ctx._loaded_tool_domains = {'motion'}
    monkeypatch.setattr(agent_loop, '_build_messages', lambda *a, **k: [{'role': 'user', 'content': ctx.user_message}])
    monkeypatch.setattr(agent_loop, '_adopt_steering_messages', lambda *a: 1)
    monkeypatch.setattr(agent_loop, '_activity', lambda *a, **k: None)
    monkeypatch.setattr(agent_loop.llm, 'responses_available', lambda *a: False)
    monkeypatch.setattr(agent_loop.llm, 'record', lambda *a: None)
    monkeypatch.setattr(agent_loop, '_auto_render_if_needed', lambda *a: (ctx.latest_edl(), ''))
    monkeypatch.setattr(agent_loop, '_enforce_honesty', lambda _ctx, _client, _messages, _tools, draft, *a, **k: draft)
    monkeypatch.setattr(agent_loop, '_enforce_reply_language', lambda _ctx, _client, _messages, _tools, draft, **k: draft)
    calls = []
    def create(**kwargs):
        notes = [m['content'] for m in kwargs['messages'] if isinstance(m.get('content'), str)
                 and m['content'].startswith(finishing_review.DIRECTIVE_PREFIX)]
        assert len(notes) == 1
        calls.append(notes[0])
        if len(calls) == 1:
            assert "10 'dip_black' transitions" in notes[0]
            name, args = 'set_transitions', {'style': 'none'}
        elif len(calls) == 2:
            assert 'prior-version findings' in notes[0]
            name, args = 'render_preview', {'complete': True}
        else:
            assert len(calls) == 3
            assert 'finish now' in notes[0]
            return NS(choices=[NS(message=NS(content='Your wedding film is ready in the preview.', tool_calls=[]), finish_reason='stop')], usage=None)
        return NS(choices=[NS(message=NS(content='', tool_calls=[NS(id=f'call{len(calls)}',
            function=NS(name=name, arguments=json.dumps(args)))]), finish_reason='tool_calls')], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    execute = agent_tools.execute
    def checked_execute(_ctx, name, args):
        if name != 'render_preview':
            result = execute(_ctx, name, args)
            assert result.startswith('EDL v73 -> v74'), result
            return result
        version = ctx.latest_edl()['version']
        assert not (ctx.latest_edl()['json']['effects'] or {}).get('transition')
        ctx.rendered_versions.add(version)
        ctx.last_preview = dict(edl_version=version, duration_s=44, asset_id=99)
        ctx.last_taste, ctx.last_taste_version = [], version
        ctx.last_visual_critic = dict(verdict='pass', findings=[], edl_version=version)
        ctx.verification_records[version] = dict(status='passed', complete_preview_passed=True,
                                               unresolved_findings=[])
        return 'Preview rendered and verified.'
    monkeypatch.setattr(agent_tools, 'execute', checked_execute)
    original_run = db.run
    db.run = lambda fn, *args: original_run(fn, *args) or 0
    result = agent_loop._run_loop(ctx, db, {'id': 3, 'project_id': 9, 'user_id': 8,
        'payload': {'operator_repair': True}}, 2, {'id': 1, 'content': ctx.user_message})
    assert result['export_ready'] is True
    assert result['steps'] == 2
    assert len(calls) == 3
