import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import compute_cost  # noqa: E402


def test_batch_profile_is_cheaper_than_identical_heavy_request():
    request, batch = {}, {}
    compute_cost.annotate_request(request, 100, "executor", "valmera-executor")
    compute_cost.annotate_batch(batch, 100, 8, 16)
    assert batch["gross_compute_usd_ceiling"] \
        < request["gross_compute_usd_ceiling"]


def test_preview_and_agent_profiles_are_named_from_service():
    assert compute_cost.request_profile(
        "executor", "valmera-executor-preview")[0] == "preview-4cpu-8g"
    assert compute_cost.request_profile(
        "agent_executor", "valmera-agent")[0] \
        == "agent-1cpu-2g-concurrency4"
    assert compute_cost.request_profile(
        "executor", "valmera-executor-batch")[0] \
        == "request-batch-8cpu-16g"


def test_fast_batch_request_service_costs_less_than_heavy_fallback():
    heavy, right_sized = {}, {}
    compute_cost.annotate_request(
        heavy, 100, "executor", "valmera-executor")
    compute_cost.annotate_request(
        right_sized, 100, "executor", "valmera-executor-batch")
    assert right_sized["gross_compute_usd_ceiling"] \
        < heavy["gross_compute_usd_ceiling"]


def test_modal_preview_cost_ceiling_is_lower_than_cloud_run(monkeypatch):
    cloud, modal = {}, {}
    compute_cost.annotate_request(
        cloud, 3600, "executor", "valmera-executor-preview")
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "preview")
    compute_cost.annotate_request(modal, 3600)
    assert modal["compute_provider"] == "modal"
    assert modal["gross_compute_usd_ceiling"] \
        < cloud["gross_compute_usd_ceiling"]


def test_modal_cost_annotation_records_actual_region(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "batch")
    monkeypatch.setenv("MODAL_REGION", "us-west-2")
    monkeypatch.setenv("MODAL_CLOUD_PROVIDER", "aws")
    timings = compute_cost.annotate_request({}, 12)
    assert timings["compute_region"] == "us-west-2"
    assert timings["compute_cloud_provider"] == "aws"
