"""The dispatcher must be able to SAY that the executor is running other code.

Round 55. Two production incidents (round 53's hidden downloads, round 55's
wrong-length exports) were both one service running a build older than the fix,
and in both the diagnosis came from database forensics rather than from anything
the system said. These tests pin the two properties that make the answer cheap,
and the one property that keeps the check from becoming the next outage.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote                                          # noqa: E402
import version                                         # noqa: E402


def test_fingerprint_is_stable_and_short():
    a = version.code_version()
    assert a and a != "unknown"
    assert a == version.code_version()          # cached, and idempotent
    assert len(a) == 12


def test_fingerprint_covers_the_files_that_broke_production():
    """The window must include the modules whose drift caused each incident.

    Round 55's executor was missing a change in renderer.py (the output clock)
    and one in schemas.py (a new stylize kind). If either file were outside the
    fingerprint's scope, this whole module would have watched that outage
    happen and reported everything as fine.
    """
    names = {os.path.basename(p) for p in version._source_files()}
    for required in ("renderer.py", "schemas.py", "indexer.py", "config.py",
                     "filmstrip.py", "timeline.py"):
        assert required in names, f"{required} is outside the fingerprint"


def test_provider_deployment_adapters_do_not_create_false_shared_code_skew():
    names = {os.path.basename(p) for p in version._source_files()}
    assert "modal_app.py" not in names
    assert "setup_modal_executor.py" not in names


def test_report_carries_the_paired_constants():
    rep = version.version_report()
    assert rep["code_version"] == version.code_version()
    for key in ("pipeline_version", "outro_version", "transition_version",
                "timeline_media_version"):
        assert isinstance(rep[key], int)


def _health(monkeypatch, body):
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(remote, "executor_health", lambda timeout=20: body)


def test_matching_versions_report_no_skew(monkeypatch):
    _health(monkeypatch, {"code_version": version.code_version()})
    assert remote.check_executor_version(quiet=True) == ""


def test_different_version_is_named_with_both_sides(monkeypatch):
    _health(monkeypatch, {"code_version": "deadbeef1234"})
    note = remote.check_executor_version(quiet=True)
    assert "deadbeef1234" in note and version.code_version() in note
    assert "DEPLOY_EXECUTOR" in note          # says what to DO about it


def test_unreachable_executor_is_not_reported_as_skew(monkeypatch):
    """An executor we cannot reach has a different problem, with its own loud
    failure path. Claiming skew from a timeout would be a guess presented as a
    fact, and the next real skew warning would be worth less for it."""
    def boom(timeout=20):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(remote, "executor_health", boom)
    assert remote.check_executor_version(quiet=True) == ""


def test_unknown_fingerprint_never_claims_skew(monkeypatch):
    """"unknown" means we failed to read source, not that the code differs."""
    _health(monkeypatch, {"code_version": "unknown"})
    assert remote.check_executor_version(quiet=True) == ""


def test_skew_is_appended_to_a_failing_job_error(monkeypatch):
    """The sentence has to land where a human already looks — the job error in
    the admin list, beside "the render is the wrong length"."""
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_SECRET", "s")

    class Resp:
        status_code = 200

        def json(self):
            return {"error": "final render duration check failed: output is "
                             "158.58s but the edit is 150.48s"}

    monkeypatch.setattr(remote.requests, "post",
                        lambda *a, **k: Resp())
    monkeypatch.setattr(remote, "executor_health",
                        lambda timeout=20: {"code_version": "0000stale000"})

    with pytest.raises(remote.RemoteExecutorError) as ei:
        remote._run_remote({"id": 1, "type": "final", "project_id": 1,
                            "user_id": 1, "attempts": 1, "payload": {}})
    msg = str(ei.value)
    assert "wrong length" not in msg or "158.58s" in msg
    assert "0000stale000" in msg          # names the build that failed


def test_version_check_never_blocks_dispatch(monkeypatch):
    """THE ROUND-53 INVARIANT. A skewed executor must still be USED. A check
    that can only withhold is how 41 finished exports became undownloadable."""
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(remote.config, "REMOTE_EXECUTOR_SECRET", "s")
    monkeypatch.setattr(remote, "executor_health",
                        lambda timeout=20: {"code_version": "0000stale000"})
    remote.check_executor_version(quiet=True)      # skew is now known

    class Resp:
        status_code = 200

        def json(self):
            return {"result": {"ok": True}}

    monkeypatch.setattr(remote.requests, "post", lambda *a, **k: Resp())
    out = remote.run_render_remote(None, {"id": 2, "type": "final",
                                          "project_id": 1, "user_id": 1,
                                          "attempts": 1, "payload": {}})
    assert out == {"ok": True}
