import os
import sys
import time

import pytest
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import resource_usage  # noqa: E402
import executor_runtime  # noqa: E402
import db as dbx  # noqa: E402


def test_cgroup_v2_snapshot_and_delta_cover_child_process_boundary(tmp_path):
    (tmp_path / "memory.current").write_text(str(3 * 1024 * 1024))
    (tmp_path / "memory.peak").write_text(str(7 * 1024 * 1024))
    (tmp_path / "memory.max").write_text(str(16 * 1024 * 1024))
    (tmp_path / "cpu.stat").write_text("usage_usec 1250000\n")
    (tmp_path / "pids.current").write_text("9\n")
    (tmp_path / "pids.peak").write_text("14\n")
    start = resource_usage.snapshot(str(tmp_path))

    (tmp_path / "memory.current").write_text(str(4 * 1024 * 1024))
    (tmp_path / "memory.peak").write_text(str(9 * 1024 * 1024))
    (tmp_path / "cpu.stat").write_text("usage_usec 3750000\n")
    usage = resource_usage.usage_since(start, str(tmp_path))

    assert usage["container_memory_current_mib"] == 4.0
    assert usage["container_memory_peak_mib"] == 9.0
    assert usage["container_memory_limit_mib"] == 16.0
    assert usage["container_cpu_s"] == 2.5
    assert usage["container_pids_peak"] == 14


def test_missing_cgroup_files_are_nonfatal(tmp_path):
    assert resource_usage.snapshot(str(tmp_path)) == {}
    assert resource_usage.usage_since({}, str(tmp_path)) == {}


def test_sampler_captures_child_peak_when_kernel_has_no_peak_file(tmp_path):
    current = tmp_path / "memory.current"
    current.write_text(str(2 * 1024 * 1024))
    sampler = resource_usage.MemorySampler(str(tmp_path), interval_s=0.01)
    current.write_text(str(11 * 1024 * 1024))
    # Under the full CPU-heavy worker suite the sampler thread can be starved
    # for longer than an arbitrary 120 ms. Wait for the behavior this test is
    # asserting (the background sampler observed the peak), with a real upper
    # bound so a broken sampler still fails promptly.
    deadline = time.monotonic() + 2.0
    while sampler._peak_bytes != 11 * 1024 * 1024 \
            and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sampler._peak_bytes == 11 * 1024 * 1024
    current.write_text(str(3 * 1024 * 1024))
    assert sampler.finish() == 11.0
    assert sampler.finish() == 11.0


def test_synchronous_tools_emit_cost_and_whole_container_telemetry(
        monkeypatch, capsys):
    class FakeDb:
        def run(self, *_args, **_kwargs):
            raise AssertionError("a synchronous frame read needs no job lease")

        @staticmethod
        def reset():
            pass

    monkeypatch.setattr(
        executor_runtime, "LeasedDb", lambda *_args: FakeDb())
    monkeypatch.setattr(
        executor_runtime.resource_usage, "snapshot", lambda: {"start": 1})
    monkeypatch.setattr(
        executor_runtime.resource_usage, "usage_since",
        lambda _start: {"container_memory_peak_mib": 612.5,
                        "container_cpu_s": 3.25})

    class Sampler:
        @staticmethod
        def finish():
            return 700.0

    monkeypatch.setattr(
        executor_runtime.resource_usage, "MemorySampler", Sampler)
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "light")
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1")

    submitted_at = time.time() - 2
    response = executor_runtime.execute(
        {"id": None, "type": "frames", "project_id": 9,
         "dispatch_submitted_at": submitted_at,
         "payload": {"execution_provider": "cloudflare"}},
        {"frames": lambda _db, _job: {"ok": True}})

    assert response["result"] == {"ok": True}
    assert response["job_completed"] is False
    assert response["execution"]["execution_class"] == "light_media"
    assert response["execution"]["provider_start_s"] >= 1.9
    assert response["execution"]["dispatch_provider"] == "cloudflare"
    assert response["execution"]["provider_fallback"] is True
    assert len(response["execution"]["executor_code_version"]) == 12
    assert response["execution"]["executor_adapter_version"] == "unknown"
    output = capsys.readouterr().out
    assert '"container_memory_peak_mib":612.5' in output
    assert '"container_memory_sampled_peak_mib":700.0' in output
    assert '"compute_profile":"modal-light-1core-1-4g-global"' in output
    assert '"job_id":null' in output


def test_executor_failure_preserves_runner_stage_timings(monkeypatch):
    class FakeDb:
        @staticmethod
        def reset():
            pass

    monkeypatch.setattr(
        executor_runtime, "LeasedDb", lambda *_args: FakeDb())
    monkeypatch.setattr(
        executor_runtime.resource_usage, "snapshot", lambda: {})
    monkeypatch.setattr(
        executor_runtime.resource_usage, "usage_since", lambda _start: {})

    class Sampler:
        @staticmethod
        def finish():
            return None

    monkeypatch.setattr(
        executor_runtime.resource_usage, "MemorySampler", Sampler)

    def fail(_db, _job):
        error = RuntimeError("synthetic encode failure")
        error.runner_timings = {
            "download_s": 12.5, "encode_s": 3000.1,
            "failed_stage": "encode_s"}
        raise error

    response = executor_runtime.execute(
        {"id": None, "type": "final", "project_id": 9}, {"final": fail})

    assert response["error"] == "synthetic encode failure"
    assert response["timings"]["download_s"] == 12.5
    assert response["timings"]["encode_s"] == 3000.1
    assert response["timings"]["failed_stage"] == "encode_s"


def test_remote_executor_waits_for_exact_durable_call_ownership(monkeypatch):
    statuses = iter(["pending", "owned"])
    calls = []

    class FakeDb:
        def run(self, fn, *args):
            assert fn is dbx.confirm_remote_execution_ownership
            calls.append(args)
            return next(statuses)

    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setattr(executor_runtime.time, "sleep", lambda _s: None)
    executor_runtime._confirm_provider_ownership(FakeDb(), {
        "id": 42, "total_claims": 4, "provider_call_id": "fc-owner",
    })
    assert calls == [
        (42, 4, "modal", "fc-owner"),
        (42, 4, "modal", "fc-owner"),
    ]
    assert executor_runtime.config.REMOTE_HANDOFF_CONFIRM_S \
        >= executor_runtime.config.REMOTE_HANDOFF_PERSIST_S + 15.0


def test_remote_executor_waits_through_transient_database_recovery(
        monkeypatch):
    outcomes = iter([
        psycopg2.OperationalError("database recovering"),
        psycopg2.InterfaceError("connection closed"),
        "owned",
    ])
    calls = []

    class FakeDb:
        def run(self, fn, *args):
            assert fn is dbx.confirm_remote_execution_ownership
            calls.append(args)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setattr(executor_runtime.time, "sleep", lambda _s: None)

    executor_runtime._confirm_provider_ownership(FakeDb(), {
        "id": 42, "total_claims": 4, "provider_call_id": "fc-owner",
    })

    assert calls == [(42, 4, "modal", "fc-owner")] * 3


def test_superseded_provider_call_stops_before_runner(monkeypatch):
    class FakeDb:
        @staticmethod
        def run(fn, *_args):
            assert fn is dbx.confirm_remote_execution_ownership
            return ("superseded:state=failed;claim_match=True;"
                    "provider_match=True;call_match=True")

    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    with pytest.raises(dbx.JobLeaseLost) as raised:
        executor_runtime._confirm_provider_ownership(FakeDb(), {
            "id": 42, "total_claims": 4, "provider_call_id": "cf-loser",
        })
    assert "state=failed" in str(raised.value)
    assert "call_match=True" in str(raised.value)
