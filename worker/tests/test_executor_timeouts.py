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
