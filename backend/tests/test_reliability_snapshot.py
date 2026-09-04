"""The production reliability snapshot stays privacy-safe and deterministic."""

from scripts import reliability_snapshot as snapshot


def test_subscriber_failure_categories_cover_the_release_incidents():
    assert snapshot.subscriber_failure_category(
        "cannot access local variable '_metric'") == "agent_metric_closure"
    assert snapshot.subscriber_failure_category(
        "Cloudflare Container shard is busy") == "cloudflare_capacity_busy"
    assert snapshot.subscriber_failure_category(
        "changed-section piece 2 rendered 2.9s, expected 8.2s"
    ) == "preview_duration_mismatch"
    assert snapshot.subscriber_failure_category(
        "max() arg is an empty sequence") == "empty_sequence_max"
    assert snapshot.subscriber_failure_category(
        "Cloudflare call could not be recovered") == "cloudflare_unrecovered"


def test_mcp_failure_categories_separate_actionable_root_causes():
    assert snapshot.mcp_failure_category(
        "Modal workspace billing limit reached") == "modal_billing_or_limit"
    assert snapshot.mcp_failure_category(
        "container readiness mismatch role=mcp_executor"
    ) == "executor_version_mismatch"
    assert snapshot.mcp_failure_category(
        "there is no container instance that can be provided"
    ) == "cloudflare_container_unavailable"
    assert snapshot.mcp_failure_category(
        "This project's video hasn't finished analyzing yet"
    ) == "project_not_indexed"


def test_unknown_errors_remain_visible_instead_of_being_called_success():
    assert snapshot.subscriber_failure_category("new failure") == "other"
    assert snapshot.mcp_failure_category("new failure") == "other"
