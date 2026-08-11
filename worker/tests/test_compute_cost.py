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
        "executor", "valmera-executor-preview")[0] == "preview-8cpu-8g"
    assert compute_cost.request_profile(
        "agent_executor", "valmera-agent")[0] \
        == "agent-1cpu-2g-concurrency2"
