"""Retry decisions must buy reliability, never replay known-bad work."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import db as dbx  # noqa: E402
import failure_policy  # noqa: E402
import media  # noqa: E402
from schemas import EDLValidationError  # noqa: E402


def test_wall_clock_timeout_is_not_retried_unchanged():
    d = failure_policy.classify(
        media.MediaError("ffmpeg killed: wall-clock 3000s exceeded"), "final")
    assert d.kind == "render_budget_exceeded"
    assert d.retryable is False
    assert d.max_attempts == 0


def test_preview_timeout_can_be_repaired_on_a_new_edl_version():
    d = failure_policy.classify(
        media.MediaError("ffmpeg killed: wall-clock 1500s exceeded"),
        "preview")
    assert d.retryable is False
    assert d.agent_repairable is True


def test_invalid_edl_gets_agent_repair_not_same_job_retry():
    d = failure_policy.classify(EDLValidationError("invalid keep range"),
                                "preview")
    assert d.kind == "invalid_edl"
    assert d.retryable is False
    assert d.agent_repairable is True


def test_wrong_render_length_gets_new_edl_repair_not_same_bytes_retry():
    d = failure_policy.classify(dbx.PermanentJobError(
        "preview render duration check failed: render is the wrong length"),
        "preview")
    assert d.retryable is False
    assert d.agent_repairable is True


def test_transient_connection_gets_exactly_one_second_run():
    d = failure_policy.classify(
        RuntimeError("upstream connection reset by peer"), "preview")
    assert d.kind == "transient_infrastructure"
    assert d.retryable is True
    assert d.max_attempts == 2


def test_lost_lease_never_resurrects_work():
    d = failure_policy.classify(dbx.JobLeaseLost("superseded"), "preview")
    assert d.kind == "lease_lost"
    assert d.retryable is False


def test_attached_executor_decision_survives_exception_reclassification():
    original = failure_policy.FailureDecision(
        "invalid_edl", False, 0, True)
    err = failure_policy.attach(
        dbx.PermanentJobError("remote wrapper"), original,
        original.payload("bad frame"))
    assert failure_policy.decision_for(err, "preview") == original


def test_black_frame_safety_failure_is_deterministic():
    d = failure_policy.classify(
        media.MediaError(
            "final render black-frame check failed: output is 100% black"),
        "final")
    assert d.kind == "invalid_edl"
    assert d.retryable is False
