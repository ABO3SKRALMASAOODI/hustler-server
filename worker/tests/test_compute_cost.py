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


def test_modal_profiles_include_right_sized_memory_and_idle_tail(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "preview")
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1")
    timings = compute_cost.annotate_request({}, 12)
    assert timings["compute_profile"] \
        == "modal-preview-2core-2-4g-global"
    assert timings["configured_idle_tail_s"] == 10
    assert timings["gross_compute_usd_reserved"] \
        < timings["gross_compute_usd_ceiling"]
    assert timings["gross_compute_usd_with_tail_ceiling"] \
        > timings["gross_compute_usd_ceiling"]
    assert compute_cost.MODAL_PROFILES["egress"][1:3] == (2, 32)


def test_eu_profile_uses_us_equivalent_shape_and_labels_region(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "index-eu")
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1.5")
    timings = compute_cost.annotate_request({}, 12)
    assert timings["compute_profile"] \
        == "modal-index-eu-4core-4-16g-pinned-eu"
    assert timings["compute_region_class"] == "pinned-eu"


def test_unpinned_profile_avoids_us_region_surcharge(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setenv("MODAL_EXECUTOR_PROFILE", "batch")
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1.5")
    pinned = compute_cost.annotate_request({}, 100)
    monkeypatch.setenv("MODAL_PRICING_MULTIPLIER", "1")
    global_profile = compute_cost.annotate_request({}, 100)
    assert global_profile["gross_compute_usd_reserved"] \
        == round(pinned["gross_compute_usd_reserved"] / 1.5, 6)
    assert global_profile["compute_region_class"] == "global"
