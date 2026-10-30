"""R1 chaos (30 Oct 2026): the dispatcher's side of the box's memory
verdict (box/admission.memory_envelope, tested in box/tests/
test_admission_oom.py): an OOM-killed preview re-runs once on the batch
lane; an OOM-killed final is not replayed and says its limit."""

import importlib.util
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BOX_DIR = os.path.join(HERE, "..", "..", "box")
sys.path.insert(0, os.path.join(HERE, ".."))

import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import main as worker_main  # noqa: E402
import remote  # noqa: E402


@pytest.fixture
def adm(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "adm.sqlite"))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"box_r1w_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                        "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the dispatcher's side of the verdict ─────────────────────────────────

class _EscalateDb:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kw):
        self.calls.append((fn, args))
        return fn is dbx.escalate_render_lane or None


def test_the_dispatcher_moves_an_oom_preview_to_the_batch_lane(adm):
    env = adm.memory_envelope("preview", False, "5g")
    job = {"id": 11, "type": "preview", "payload": {
        "execution_provider": "box"}}
    with pytest.raises(Exception) as caught:
        remote._interpret_executor_data(env, job)
    err = caught.value
    decision = failure_policy.decision_for(err, "preview")
    assert decision.kind == "executor_memory"
    db = _EscalateDb()
    assert worker_main._escalate_oom_render(db, job, err, decision, 3)
    fn, args = db.calls[0]
    assert fn is dbx.escalate_render_lane and args[-1] == "batch"


def test_an_oom_final_tells_the_customer_its_limit_not_a_retry(adm):
    env = adm.memory_envelope("final", False, "10g")
    job = {"id": 12, "type": "final", "project_id": 2, "payload": {}}
    with pytest.raises(Exception) as caught:
        remote._interpret_executor_data(env, job)
    decision = failure_policy.decision_for(caught.value, "final")
    assert not decision.retryable
    assert not failure_policy.is_infrastructure(caught.value, decision)
