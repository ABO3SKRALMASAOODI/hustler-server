"""The overview attention card reports current user-facing incidents only."""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.admin_video import (  # noqa: E402
    ATTENTION_ACTIONABLE_JOB_TYPES,
    ATTENTION_MEDIA_SUCCESSORS,
    ATTENTION_REPLACEMENT_STATES,
    _attention_media_supersession_sql,
)


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
