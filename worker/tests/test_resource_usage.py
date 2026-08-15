import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import resource_usage  # noqa: E402
import executor_runtime  # noqa: E402


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
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "light")
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1")

    response = executor_runtime.execute(
        {"id": None, "type": "frames", "project_id": 9},
        {"frames": lambda _db, _job: {"ok": True}})

    assert response == {"result": {"ok": True}, "job_completed": False}
    output = capsys.readouterr().out
    assert '"container_memory_peak_mib":612.5' in output
    assert '"compute_profile":"modal-light-4core-8-32g-global"' in output
    assert '"job_id":null' in output
