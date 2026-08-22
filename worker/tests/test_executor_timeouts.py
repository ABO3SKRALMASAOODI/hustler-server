"""The dispatcher timeout and the executor's request timeout are a PAIR.

`REMOTE_EXECUTOR_TIMEOUT_S` lives in worker/config.py; the executor's own cap is
a `--timeout` flag on the Cloud Run service, documented in DEPLOY_EXECUTOR.md.
If the dispatcher's number ever climbs above the executor's, a wedged job is
abandoned by the dispatcher (which requeues, because renders are idempotent)
while the executor happily keeps running the original for another half hour —
two copies of the same job on 8 vCPU, billed per instance-second.

Nothing enforces that ordering at runtime, so it is enforced here, against the
deploy doc that is the operator's only instruction for the other half.
"""

import os
import re

import config
import renderer

DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "DEPLOY_EXECUTOR.md")


def _documented_cloud_run_timeout():
    """The `--timeout N` the deploy doc tells the operator to use."""
    with open(DOC) as f:
        text = f.read()
    m = re.search(r"--timeout\s+(\d+)", text)
    assert m, "DEPLOY_EXECUTOR.md no longer specifies --timeout"
    return int(m.group(1))


def test_the_dispatcher_gives_up_before_the_executor_does():
    assert config.REMOTE_EXECUTOR_TIMEOUT_S < _documented_cloud_run_timeout(), (
        "REMOTE_EXECUTOR_TIMEOUT_S must stay under the executor's own request "
        "timeout, or a wedged job runs on after the dispatcher has requeued it")


def test_every_per_kind_timeout_also_stays_under_the_executor():
    """The pairing invariant applies to EVERY kind, not just the fallback.

    Per-kind budgets (round 57) are what let an hour-long final finish; each one
    is a fresh chance to accidentally set a number above Cloud Run's own cap and
    recreate the orphan-job bug the single constant was tuned to avoid.
    """
    cloud_run = _documented_cloud_run_timeout()
    for kind, secs in config.REMOTE_EXECUTOR_TIMEOUTS.items():
        assert secs < cloud_run, (
            f"REMOTE_EXECUTOR_TIMEOUTS[{kind!r}]={secs} must stay under the "
            f"executor's --timeout ({cloud_run})")
        assert secs < config.EXECUTOR_REQUEST_TIMEOUT_S, (
            f"REMOTE_EXECUTOR_TIMEOUTS[{kind!r}]={secs} must stay under "
            f"EXECUTOR_REQUEST_TIMEOUT_S")


def test_the_documented_flag_matches_the_mirrored_constant():
    """EXECUTOR_REQUEST_TIMEOUT_S exists so the ordering can be checked in code
    as well as against the doc. If the two drift, the code check is worthless —
    it would be comparing against a number the deployed service does not use."""
    assert config.EXECUTOR_REQUEST_TIMEOUT_S == _documented_cloud_run_timeout()


def test_a_final_gets_room_for_an_hour_long_export():
    """Measured on the executor, encodes run 0.5-1.8x realtime. A one-hour
    programme therefore needs thousands of seconds; at the old flat 1500 it
    could not finish at all, and the refusal arrived only after the wait."""
    assert config.executor_timeout_for("final") >= 3000
    assert config.executor_timeout_for("index") >= 3000


def test_modal_media_envelope_preserves_compute_after_handoff_recovery():
    assert config.MODAL_EXECUTOR_TIMEOUT_S >= (
        config.EXECUTOR_REQUEST_TIMEOUT_S
        + config.REMOTE_HANDOFF_CONFIRM_S)
    assert config.modal_timeout_for("index") \
        == config.MODAL_EXECUTOR_TIMEOUT_S


def test_modal_final_ffmpeg_budget_scales_with_authored_duration(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    long_program = 4716.58
    timeout = renderer._render_ffmpeg_timeout(False, long_program)
    assert timeout == min(
        config.FINAL_FFMPEG_TIMEOUT_MAX_S, int(long_program * 2 + 600))
    assert timeout > config.FFMPEG_TIMEOUT_S
    assert renderer._render_ffmpeg_timeout(True, long_program) == \
        config.FFMPEG_TIMEOUT_S

    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloud_run")
    assert renderer._render_ffmpeg_timeout(False, long_program) == \
        config.FFMPEG_TIMEOUT_S


def test_there_is_real_headroom_over_a_healthy_job():
    """The longest healthy job measured on this hardware is ~300s. The timeout
    is a wedge ceiling, not a leash — cutting it near real render times would
    guillotine legitimate work, which is the mistake the removed per-turn spend
    cap made."""
    assert config.REMOTE_EXECUTOR_TIMEOUT_S >= 1200


def test_the_doc_and_the_constant_are_not_wildly_apart():
    """A doc that says 1800 next to a constant of 60 would technically satisfy
    the ordering above while making every real render fail."""
    cloud_run = _documented_cloud_run_timeout()
    assert cloud_run <= 3600, "3600 is Cloud Run's hard maximum"
    assert config.REMOTE_EXECUTOR_TIMEOUT_S > cloud_run / 4
