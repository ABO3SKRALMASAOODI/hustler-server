"""The overview attention card reports current user-facing incidents only."""

import os
import sys
import inspect

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.admin_video import (  # noqa: E402
    ATTENTION_ACTIONABLE_JOB_TYPES,
    ATTENTION_MEDIA_SUCCESSORS,
    ATTENTION_REPLACEMENT_STATES,
    _attention_media_supersession_sql,
    video_projects,
    video_reliability,
)
from routes.video import shorts_board  # noqa: E402


def test_internal_attempts_do_not_claim_active_operator_attention():
    assert set(ATTENTION_ACTIONABLE_JOB_TYPES) == {
        "index", "preview", "final", "agent_turn", "shorts_plan",
    }
    assert "preview_check" not in ATTENTION_ACTIONABLE_JOB_TYPES
    assert "mcp_tool" not in ATTENTION_ACTIONABLE_JOB_TYPES
    assert "filmstrip" not in ATTENTION_ACTIONABLE_JOB_TYPES


def test_media_supersession_only_accepts_same_or_stronger_deliverable():
    assert ATTENTION_MEDIA_SUCCESSORS == {
        "preview": ("preview", "final"),
        "final": ("final",),
    }

    sql = _attention_media_supersession_sql("failed", "candidate")
    assert "failed.type = 'preview'" in sql
    assert "candidate.type IN ('preview', 'final')" in sql
    assert "failed.type = 'final'" in sql
    assert "candidate.type IN ('final')" in sql
    assert "preview_check" not in sql


def test_only_operational_attempt_states_can_replace_a_failure():
    assert ATTENTION_REPLACEMENT_STATES == (
        "queued", "running", "done", "failed")
    assert "cancelled" not in ATTENTION_REPLACEMENT_STATES
    assert "superseded" not in ATTENTION_REPLACEMENT_STATES


def test_reliability_is_one_explicit_rolling_day_query():
    source = inspect.getsource(inspect.unwrap(video_reliability))
    assert source.count("cur.execute(") == 1
    assert "INTERVAL '24 hours'" in source
    assert '"hours": 24' in source
    assert '"pct"' in source and '"total"' in source
    assert "metrics_counters" not in source


def test_project_rows_expose_conversion_and_tool_outcomes():
    source = inspect.getsource(inspect.unwrap(video_projects))
    for field in ("tool_calls", "tool_failed", "tool_rejected",
                  "customer_paid", "converted_project"):
        assert field in source
    assert "pa.status = 'completed'" in source
    assert "pa.amount_cents > 0" in source
    assert "subscription_upload_locked" in source


def test_shorts_board_uses_one_child_snapshot_query():
    source = inspect.getsource(inspect.unwrap(shorts_board))
    # latest scout + one child-card snapshot + parent source/preview
    assert source.count("cur.execute(") == 3
    assert "LEFT JOIN LATERAL" in source
    assert "boot_id" in source and "preview_asset_id" in source
