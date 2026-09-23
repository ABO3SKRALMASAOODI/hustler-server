"""The live request must recover without replaying side effects or losing ownership."""
from types import SimpleNamespace

import pytest

import agent_tools as tools
import db as dbx
from tool_outcome import from_legacy


def context():
    return SimpleNamespace(
        project_id=7, project={'id': 7, 'user_id': 60},
        job={'id': 10, 'user_id': 60},
        latest_edl=lambda: {'version': 3, 'json': {}},
        db=SimpleNamespace(run=lambda *_: None),
        _executing_tool=None,
    )


@pytest.mark.parametrize('name', ['make_shorts', 'extract_audio', 'render_preview'])
def test_lost_reply_after_side_effect_never_blindly_replays(monkeypatch, name):
    accepted = []

    def fn(ctx):
        accepted.append('accepted')
        raise ConnectionError('connection reset after acceptance')

    monkeypatch.setitem(tools.TOOLS, name, (fn, '', {}))
    ctx = context()
    result = tools.execute(ctx, name, {})
    assert len(accepted) == 1
    assert ctx.last_structured_tool_outcome['idempotent'] is False
    assert 'state' in result.lower()


def test_safe_read_recovers_a_transient_connection_once(monkeypatch):
    calls = []

    def fn(ctx):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError('connection reset')
        return 'Current EDL v3'

    monkeypatch.setitem(tools.TOOLS, 'get_edl', (fn, '', {}))
    assert tools.execute(context(), 'get_edl', {}) == 'Current EDL v3'
    assert len(calls) == 2


def test_deterministic_read_error_does_not_retry(monkeypatch):
    calls = []

    def fn(ctx):
        calls.append(1)
        raise ValueError('invalid argument: section name')

    monkeypatch.setitem(tools.TOOLS, 'get_edl', (fn, '', {}))
    ctx = context()
    tools.execute(ctx, 'get_edl', {})
    assert len(calls) == 1
    assert ctx.last_structured_tool_outcome['retryable'] is False


def test_lease_loss_on_read_retry_aborts_the_old_executor(monkeypatch):
    calls = []

    def fn(ctx):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError('connection reset')
        raise dbx.JobLeaseLost('the caller no longer owns this request')

    monkeypatch.setitem(tools.TOOLS, 'get_edl', (fn, '', {}))
    ctx = context()
    with pytest.raises(dbx.JobLeaseLost):
        tools.execute(ctx, 'get_edl', {})
    assert ctx._executing_tool is None


def test_disabled_batch_tool_is_absent_from_every_agent_loading_surface():
    ctx = context()
    assert tools._tool_disabled('edit_shorts')
    assert 'edit_shorts' not in tools.capability_directory()
    assert 'edit_shorts' not in {t['function']['name'] for t in tools.openai_tools()}
    assert 'unavailable' in tools.load_tools(ctx, names=['edit_shorts']).lower()
    assert 'edit_shorts' not in tools.load_tools(ctx, domains=['shorts'])
    assert 'edit_shorts' not in tools.execute(ctx, 'nonexistent_tool', {})


@pytest.mark.parametrize('failure,status', [
    ({'error': 'invalid frame', 'agent_repairable': True, 'retryable': False},
     'correction_needed'),
    ({'error': 'provider budget exceeded', 'retryable': False}, 'unavailable'),
    # The queue reports failed only after its own physical retries ended.
    ({'error': 'connection reset', 'retryable': True}, 'unavailable'),
])
def test_preview_failure_retains_the_actionable_failure_type(failure, status):
    result = tools._failed_preview_message(3, failure)
    assert from_legacy(result).status == status


def test_agent_can_wait_for_its_existing_render_without_enqueueing(monkeypatch):
    states = iter(['running', 'done'])
    calls = []
    ctx = context()
    ctx.turn_tool_outcomes = [
        {'tool': 'render_preview', 'kind': 'prerequisite', 'pending_job_id': 11},
        {'tool': 'wait_for_job', 'kind': 'prerequisite', 'pending_job_id': 12}]

    def run(fn, *args):
        calls.append(fn)
        assert fn is dbx.get_job
        return {'id': 11, 'project_id': 7, 'user_id': 60, 'type': 'preview',
                'state': next(states), 'progress': 70,
                'result': {'edl_version': 3, 'render_asset_id': 99}}

    ctx.db.run = run
    monkeypatch.setattr(tools.time, 'sleep', lambda *_: None)
    result = tools.wait_for_job(ctx, 11)
    assert 'finished' in result.lower() and '99' in result
    assert 'render_preview' in result
    assert len(calls) == 2
    assert 'wait_for_job' in tools.planning_tool_names()
    assert ctx.spec_preview_jobs == {3: 11}
    assert ctx.turn_tool_outcomes[0]['resolved'] is True
    assert 'resolved' not in ctx.turn_tool_outcomes[1]


def test_superseded_job_is_never_claimed_as_rendered():
    ctx = context()
    ctx.db.run = lambda *_: {
        'id': 11, 'project_id': 7, 'user_id': 60, 'type': 'preview',
        'state': 'done', 'result': {'superseded_by': 4}}
    result = tools.wait_for_job(ctx, 11)
    assert 'did not produce a preview' in result
    assert not getattr(ctx, 'spec_preview_jobs', {})


def test_terminal_background_failure_does_not_offer_a_fresh_retry():
    ctx = context()
    ctx.db.run = lambda *_: {
        'id': 11, 'project_id': 7, 'user_id': 60, 'type': 'shorts_plan',
        'state': 'failed', 'result': {'failure': {
            'error': 'connection reset', 'retryable': True}}}
    result = tools.wait_for_job(ctx, 11)
    assert from_legacy(result).status == 'unavailable'
    assert 'do not repeat unchanged' in result


@pytest.mark.parametrize('project_id,user_id', [(8, 60), (7, 61), (7, None)])
def test_job_wait_never_reads_another_scope(project_id, user_id):
    ctx = context()
    ctx.db.run = lambda *_: {'id': 11, 'project_id': project_id,
                            'user_id': user_id, 'state': 'done',
                            'result': {'secret': 'must not be disclosed'}}
    result = tools.wait_for_job(ctx, 11)
    assert result.startswith('UNSAFE:')
    assert 'must not be disclosed' not in result


def test_pending_wait_is_bounded_and_keeps_the_same_job(monkeypatch):
    ctx = context()
    ids = []

    def run(fn, job_id):
        ids.append(job_id)
        assert fn is dbx.get_job
        return {'id': job_id, 'project_id': 7, 'user_id': 60,
                'state': 'running', 'progress': 85}

    ctx.db.run = run
    clock = iter([0.0, 46.0])
    monkeypatch.setattr(tools.time, 'monotonic', lambda: next(clock))
    result = tools.wait_for_job(ctx, 11)
    assert result.startswith('PREREQUISITE:')
    assert ids == [11]
    assert '85%' in result and 'same ID' in result


def test_wait_stops_immediately_if_the_agent_loses_its_claim():
    ctx = context()
    ctx.job['total_claims'] = 2

    def run(fn, *args):
        assert fn is dbx.lease_is_current
        return False

    ctx.db.run = run
    with pytest.raises(dbx.JobLeaseLost):
        tools.wait_for_job(ctx, 11)


@pytest.mark.parametrize('source', ['agent_preview', 'agent_preview_check'])
def test_continuations_keep_one_render_retry_identity(source):
    ctx = context()
    ctx.job['type'] = 'agent_turn'
    ctx.job['payload'] = {}
    first = tools._child_payload(ctx, {'source': source})
    ctx.job = {'id': 12, 'type': 'agent_turn', 'user_id': 60,
               'payload': {'root_agent_job_id': 10}}
    continued = tools._child_payload(ctx, {'source': source})
    assert first['root_agent_job_id'] == continued['root_agent_job_id'] == 10
    assert 'root_agent_job_id' not in tools._child_payload(ctx, {'source': 'agent'})


@pytest.mark.parametrize('job_type', ['preview', 'preview_check'])
def test_terminal_render_is_reused_for_the_logical_request(job_type):
    class Cursor:
        def execute(self, sql, params):
            assert params == (7, job_type, '10', 'same-pixels')
            # Queue failures are terminal even when their original failure
            # was transient. Neither retryable=true nor slice age resets it.
            assert "state = 'failed'" in sql
            assert 'retryable' not in sql and 'INTERVAL' not in sql

        def fetchone(self):
            return {'id': 77}

    assert dbx._failed_render_for_request(Cursor(), 7, job_type, {
        'root_agent_job_id': 10, 'render_signature': 'same-pixels'}) == 77
    assert dbx._failed_render_for_request(None, 7, job_type, {
        'render_signature': 'same-pixels'}) is None


def test_provider_terminal_decision_is_not_replaced_with_generic_retry(monkeypatch):
    calls = []

    def fn(ctx):
        calls.append(1)
        error = RuntimeError('provider denied this request')
        error.failure_kind = 'provider_budget_exhausted'
        error.retryable = False
        error.max_attempts = 0
        raise error

    monkeypatch.setitem(tools.TOOLS, 'get_words', (fn, '', {}))
    ctx = context()
    assert tools.execute(ctx, 'get_words', {}).startswith('UNAVAILABLE:')
    assert len(calls) == 1
    assert ctx.last_structured_tool_outcome['retryable'] is False


@pytest.mark.parametrize('enqueue', [dbx.get_or_enqueue_preview_job,
                                    dbx.get_or_enqueue_preview_check_job])
@pytest.mark.parametrize('same_request', [True, False])
def test_enqueue_cannot_reset_a_continuations_terminal_render(enqueue, same_request):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params):
            self.result = None
            if 'SELECT id' in sql and "payload->>'root_agent_job_id'" in sql:
                self.result = {'id': 77} if params[2] == '10' else None
            if 'INSERT INTO video_jobs' in sql:
                assert not same_request, 'continuation tried to buy a fresh retry'
                self.result = {'id': 88}

        def fetchone(self):
            return self.result

    conn = SimpleNamespace(cursor=Cursor)
    result = enqueue(conn, 7, 60, {
        'edl_version': 3, 'render_signature': 'same-pixels',
        'root_agent_job_id': 10 if same_request else 12})
    assert result == ((77, False) if same_request else (88, True))
