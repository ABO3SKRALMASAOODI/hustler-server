"""A Studio editor releases its writer, then reconnects to the exact Shorts job."""
import json
from types import SimpleNamespace
import pytest
import agent_loop
import agent_tools
import db
import trial_reply


def context():
    row = {'id': 12, 'type': 'shorts_plan', 'project_id': 7, 'user_id': 60,
           'state': 'queued', 'payload': {'parent_agent_job_id': 10}}
    calls = []

    def run(fn, *args):
        calls.append(fn)
        if fn is db.has_active_job:
            return False
        if fn is db.enqueue_job:
            assert args[:3] == (7, 60, 'shorts_plan')
            row['payload'] = args[3]
            return 12
        if fn is db.get_job:
            assert args == (12,)
            return row
        if fn is db.get_project:
            return {'id': 7, 'user_id': 60, 'meta': {
                'shorts': {'clips': [{'child_project_id': 8}]}}}
        raise AssertionError(fn)

    ctx = SimpleNamespace(project_id=7, project={'id': 7, 'user_id': 60},
        job={'id': 10, 'user_id': 60, 'type': 'agent_turn'},
        db=SimpleNamespace(run=run), has_main_video=True, duration=180.,
        index={'words': [{'w': 'Hello', 't0': 0., 't1': .5}]},
        versions_written=[], rendered_versions=set(), last_preview=None,
        turn_tool_outcomes=[], write_attempts=0)
    return ctx, row, calls


def test_same_serialized_job_is_checkpointed_and_reconnected_without_reenqueue():
    ctx, row, calls = context()
    agent_tools.make_shorts(ctx, count=3)
    assert row['payload']['parent_agent_job_id'] == 10
    assert agent_loop._serial_background_handoff(ctx) == 12
    checkpoint = json.loads(json.dumps({'serial_job_id': ctx.serial_job_id}))
    ctx.turn_tool_outcomes = [{'tool': 'wait_for_job', 'kind': 'prerequisite',
                              'pending_job_id': 12}]
    row['state'] = 'done'
    assert 'cards are ready' in agent_loop._resume_serial_background(ctx, checkpoint)
    assert ctx.turn_tool_outcomes[0]['resolved'] is True
    assert ctx.shorts_jobs_completed == [12]
    assert calls.count(db.enqueue_job) == 1
    assert agent_loop._turn_completion(ctx)[0] == 'awaiting_user'
    handoff = 'The three source-story cards are ready. Choose Edit on each card to finish it.'
    assert trial_reply.finish_for_ctx(ctx, handoff)[0] == handoff
    assert agent_loop._continuation_work_slices(
        {'work_slices': 2}, 'awaiting serial background job') == 2


@pytest.mark.parametrize('change', [
    {'project_id': 9}, {'user_id': 61}, {'type': 'preview'},
    {'payload': {'parent_agent_job_id': 11}}, {'state': 'failed'}])
def test_handoff_checks_exact_owner_parent_and_job_type(change):
    ctx, row, _ = context()
    ctx.serial_job_id = 12
    row.update(change)
    assert agent_loop._serial_background_handoff(ctx) is None


def test_failed_background_work_remains_a_failure():
    ctx, row, _ = context()
    row.update(state='failed', error='provider unavailable')
    note = agent_loop._resume_serial_background(ctx, {'serial_job_id': 12})
    assert 'UNAVAILABLE' in note
    assert not getattr(ctx, 'shorts_jobs_completed', None)
    assert agent_loop._turn_completion(ctx)[0] != 'awaiting_user'


def test_wait_on_own_serial_child_yields_without_sleeping(monkeypatch):
    ctx, row, _ = context()
    monkeypatch.setattr(agent_tools.time, 'sleep', lambda _: pytest.fail('writer deadlock'))
    assert 'checkpoint' in agent_tools.wait_for_job(ctx, 12)
    assert ctx.serial_job_id == 12
