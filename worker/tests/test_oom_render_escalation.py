"""An OOM-killed render runs once more on the 12 GiB batch lane (job 111131)."""
import types
import main
import remote
import failure_policy


def test_lane_honours_force_lane_only_for_valid_failover():
    job = {"type": "preview", "payload": {"force_lane": "batch"}}
    assert remote._cloudflare_lane_for(job) == "batch"
    assert remote._cloudflare_lane_for({"type": "preview", "payload": {}}) == "interactive"
    # an agent turn has no failover lane: a stray force_lane is ignored
    assert remote._cloudflare_lane_for({"type": "agent_turn", "payload": {"force_lane": "batch"}}) == "agent"


def _decision(kind):
    return types.SimpleNamespace(kind=kind, retryable=False, max_attempts=0,
                                 agent_repairable=False)


class FakeDb:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args):
        self.calls.append((fn.__name__, args))
        return True


def test_oom_preview_escalates_once():
    db = FakeDb()
    job = {"id": 7, "type": "preview", "payload": {}}
    assert main._escalate_oom_render(db, job, RuntimeError("oom"), _decision("executor_memory"), 3)
    assert db.calls[0][0] == "escalate_render_lane" and db.calls[0][1][-1] == "batch"
    # already escalated: the ordinary policy decides
    job2 = {"id": 8, "type": "preview", "payload": {"force_lane": "batch"}}
    assert not main._escalate_oom_render(FakeDb(), job2, RuntimeError("oom"), _decision("executor_memory"), 3)


def test_other_failures_and_types_are_not_escalated():
    assert not main._escalate_oom_render(FakeDb(), {"id": 1, "type": "preview", "payload": {}},
                                         RuntimeError("x"), _decision("invalid_edl"), 1)
    assert not main._escalate_oom_render(FakeDb(), {"id": 1, "type": "agent_turn", "payload": {}},
                                         RuntimeError("x"), _decision("executor_memory"), 1)
