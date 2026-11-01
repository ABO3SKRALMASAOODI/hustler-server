"""Ticket #93 (1 Nov 2026): a paying customer's 62-minute episode with a
five-part request was blocked "no longer making progress" after slices
130876, 130958 and 130996. Each of them ended while the request's own
complete preview of the current version was still rendering (~24 min per
render on that source: jobs 130886 and 131022), so render waits were counted
as stagnant slices, and the request stopped the second before v90's preview
landed. A boundary that is waiting on its own render is the same
"awaiting complete preview" pause the reply path already takes."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop  # noqa: E402
import db as dbx  # noqa: E402


class _Ctx:
    project_id = 5266

    def __init__(self, version, written=(90,), rendered=()):
        self._version = version
        self.versions_written = list(written)
        self.rendered_versions = set(rendered)

    def latest_edl(self):
        return {"version": self._version, "json": {}}


class _DB:
    def __init__(self, pending=None, boom=False):
        self.pending, self.boom, self.calls = pending, boom, []

    def run(self, fn, *args):
        self.calls.append((fn, args))
        if self.boom:
            raise RuntimeError("db down")
        assert fn is dbx.pending_preview_job
        return self.pending


def test_a_running_preview_of_the_written_version_is_a_render_wait():
    db = _DB(pending=131022)
    assert agent_loop._awaiting_own_preview(_Ctx(90), db)
    assert db.calls == [(dbx.pending_preview_job, (5266, 90))]


def test_no_render_wait_without_a_pending_render_of_our_own_write():
    # nothing rendering
    assert not agent_loop._awaiting_own_preview(_Ctx(90), _DB(pending=None))
    # the version already rendered in this request
    assert not agent_loop._awaiting_own_preview(
        _Ctx(90, rendered=(90,)), _DB(pending=131022))
    # this request wrote nothing: someone else's render is not our wait
    assert not agent_loop._awaiting_own_preview(
        _Ctx(90, written=()), _DB(pending=131022))
    # a database fault keeps the stagnation stop working
    assert not agent_loop._awaiting_own_preview(_Ctx(90), _DB(boom=True))


def test_the_boundary_waits_before_counting_a_stagnant_slice():
    src = inspect.getsource(agent_loop)
    i = src.index("if _awaiting_own_preview(ctx, worker_db):")
    j = src.index('"recovering unresolved work",')
    k = src.index('blocker = ("I stopped because the edit was no longer making progress"')
    assert src.index('else "productive work remains",') < i < j < k
    branch = src[i:i + 1200]
    assert '"awaiting complete preview",' in branch
    # neither progress nor stagnation: the repeat count is carried, not bumped
    assert '_cont.get("blocker_repeats")' in branch
    assert "stagnant=None" in branch
    # and it is not a work slice
    assert "awaiting complete preview" in agent_loop._NON_PRODUCTIVE_CONTINUATION_REASONS
    assert agent_loop._continuation_work_slices(
        {"work_slices": 3}, "awaiting complete preview") == 3


def test_the_93_chain_is_no_longer_blocked_at_its_third_render_wait():
    # 130876 -> 130958 -> 130996: each boundary saw its own render running.
    state = {"stagnant_job_ids": []}
    for job_id in (130876, 130958, 130996):
        state["stagnant_job_ids"] = agent_loop._stagnant_job_ids(
            state, job_id, None)
    assert state["stagnant_job_ids"] == []
