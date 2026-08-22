import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import executor_canary_gate as gate  # noqa: E402


def _sample(provider, *, cold, end_to_end=100, runner=80, cost=.01,
            state="done", dispatch=None, failure_kind="",
            artifact_complete=True):
    return {
        "type": "preview_check", "provider": provider,
        "dispatch_provider": dispatch or provider,
        "provider_fallback": bool(dispatch and dispatch != provider),
        "state": state, "success": state == "done",
        "failure_kind": failure_kind,
        "hazard": failure_kind in gate.HAZARD_KINDS,
        "artifact_complete": artifact_complete,
        "input_bytes": 100 * 1024 ** 2, "duration_s": 240,
        "cache_hit": False, "cold": cold,
        "runner_s": runner, "end_to_end_s": end_to_end,
        "cost_usd": cost,
    }


def _passing_samples():
    rows = []
    for cold in (True, False):
        rows.extend([
            _sample("modal", cold=cold, end_to_end=100, runner=80, cost=.02),
            _sample("cloudflare", cold=cold, end_to_end=95, runner=78,
                    cost=.015),
        ])
    return rows


def test_gate_passes_only_like_for_like_faster_cheaper_complete_samples():
    report = gate.evaluate(
        _passing_samples(), job_types=("preview_check",), min_samples=2,
        min_cohort_samples=1)
    assert report["advance_allowed"] is True
    assert report["failures"] == []


def test_slow_cloudflare_fails_end_to_end_and_runner_gates():
    rows = _passing_samples()
    for row in rows:
        if row["provider"] == "cloudflare":
            row["end_to_end_s"] = 120
            row["runner_s"] = 100
    report = gate.evaluate(
        rows, job_types=("preview_check",), min_samples=2,
        min_cohort_samples=1)
    codes = {row["code"] for row in report["failures"]}
    assert "p95_end_to_end_ratio" in codes
    assert "p95_runner_ratio" in codes


def test_fallback_hazard_and_incomplete_artifact_fail_closed():
    rows = _passing_samples()
    rows.append(_sample("modal", cold=False, dispatch="cloudflare"))
    rows.append(_sample("cloudflare", cold=False, state="failed",
                        failure_kind="executor_capacity"))
    rows.append(_sample("cloudflare", cold=False,
                        artifact_complete=False))
    report = gate.evaluate(
        rows, job_types=("preview_check",), min_samples=2,
        min_cohort_samples=1)
    codes = {row["code"] for row in report["failures"]}
    assert "cloudflare_prelaunch_fallback" in codes
    assert "cloudflare_hazard" in codes
    assert "incomplete_cloudflare_artifact" in codes


def test_missing_shape_or_provider_telemetry_cannot_pass():
    rows = _passing_samples()
    rows[0]["provider"] = ""
    rows[1]["input_bytes"] = None
    report = gate.evaluate(
        rows, job_types=("preview_check",), min_samples=2,
        min_cohort_samples=1)
    codes = {row["code"] for row in report["failures"]}
    assert "missing_or_invalid_provider_telemetry" in codes
    assert "insufficient_provider_samples" in codes or \
        "insufficient_matched_cohort" in codes


def test_sample_parser_requires_cold_start_and_artifact_evidence():
    row = {
        "type": "preview_check", "state": "done", "error": None,
        "payload": {"execution_provider": "cloudflare",
                    "execution_shape": {"total_bytes": 10,
                                        "max_duration_s": 20}},
        "result": {"render_asset_id": 7, "changed_ranges": [[1, 2]],
                   "timings": {"compute_provider": "cloudflare",
                               "dispatch_provider": "cloudflare",
                               "queue_wait_s": 2, "provider_start_s": 3,
                               "total_s": 5, "container_first_input": True,
                               "gross_compute_usd_with_tail_ceiling": .01}},
    }
    sample = gate.sample_from_row(row)
    assert sample["end_to_end_s"] == 10
    assert sample["cold"] is True
    assert sample["artifact_complete"] is True


def test_missing_or_contradictory_remote_ownership_blocks_advance():
    report = gate.evaluate(
        _passing_samples(), job_types=("preview_check",), min_samples=2,
        min_cohort_samples=1)
    gate.apply_remote_health(report, {
        "ledger_present": True, "expired_active": 1,
        "terminal_job_active": 0, "duplicate_call_ids": 0})
    assert report["advance_allowed"] is False
    assert any(row["code"] == "expired_active_remote_execution"
               for row in report["failures"])
