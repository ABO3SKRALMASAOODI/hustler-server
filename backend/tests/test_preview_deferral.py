"""One encode per burst of clicks — and never zero.

Project 246's session was scissors, delete, scissors, delete: nineteen EDL
versions and SIXTEEN preview encodes at 33-65s each, roughly eleven minutes of
ffmpeg to arrive at one video. Every render but the last was of a timeline the
user had already changed, and the studio could already PLAY those edits itself
off the proxy (round 58) while it waited for them.

So a timing edit defers its encode and the studio asks for it once, when the
clicking stops. The danger in that is obvious and is what these tests are
mostly about: a deferral that nobody ever redeems is an edit with no render,
which is the failure the /state self-heal exists to prevent. The heal is
suppressed for exactly the version a client says it is drafting, and for no
longer than the client keeps saying so.

    cd backend && python -m pytest tests/test_preview_deferral.py -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402


def edl_row(version=7, created_by="user"):
    return {"version": version, "created_by": created_by, "json": {}}


# ── what a write does ───────────────────────────────────────────────────────

def test_a_plain_write_still_renders_immediately():
    assert video._preview_plan(None, False) == "enqueue"


def test_a_drafting_client_gets_a_deferral():
    assert video._preview_plan(None, True) == "defer"


def test_an_existing_encode_is_adopted_even_when_deferral_was_asked_for():
    """A twin costs nothing and is the EXACT picture. Deferring instead would
    make the user wait 4 seconds for a render that already exists."""
    assert video._preview_plan(42, True) == "adopt"
    assert video._preview_plan(42, False) == "adopt"


# ── what the safety net does ────────────────────────────────────────────────

def test_the_heal_still_fires_for_an_ordinary_client():
    """No `drafting` at all — an older studio, or any client between edits."""
    assert video._should_heal_preview(edl_row(7), True, None) is True


def test_the_heal_is_suppressed_for_the_version_being_drafted():
    assert video._should_heal_preview(edl_row(7), True, 7) is False


def test_the_heal_fires_for_a_DIFFERENT_version_than_the_one_drafted():
    """The client is drafting v6 but the newest is v7 — a poll that raced an
    edit, or a stale claim. The newest edit is what must have a render, so the
    net does its job."""
    assert video._should_heal_preview(edl_row(7), True, 6) is True


def test_a_closed_tab_stops_suppressing_immediately():
    """The suppression lives entirely in the request. Nothing is stored, so a
    client that goes away cannot leave a version permanently unrendered — the
    very next poll from anywhere heals it."""
    assert video._should_heal_preview(edl_row(7), True, 7) is False
    assert video._should_heal_preview(edl_row(7), True, None) is True


def test_agent_versions_are_not_healed_while_their_turn_lives():
    """Standing policy: a live or completed agent turn owns its own preview
    (it renders one, or the worker auto-renders), and healing those would
    speculatively render every project whose last EDL was the agent's
    opening version."""
    assert video._should_heal_preview(edl_row(7, "agent"), True, None) is False
    assert video._should_heal_preview(edl_row(7, "agent"), True, 7) is False


def test_a_dead_turns_orphan_versions_are_healed():
    """Round 67b: the one hole in that contract. A turn KILLED mid-flight (a
    deploy restart, an OOM) leaves the versions it already wrote with no
    preview and no job — project 298 sat on 'Updating your preview…' forever
    over a stale video while its turn's corpse said 'Worker died'. When the
    newest agent_turn is FAILED there is no turn left to race: the net covers
    agent versions too (drafting suppression still applies)."""
    assert video._should_heal_preview(edl_row(7, "agent"), True, None,
                                      agent_orphaned=True) is True
    assert video._should_heal_preview(edl_row(7, "agent"), True, 7,
                                      agent_orphaned=True) is False
    # ...and the flag never bypasses the index gate
    assert video._should_heal_preview(edl_row(7, "agent"), False, None,
                                      agent_orphaned=True) is False


def test_nothing_is_healed_before_the_video_is_indexed_or_without_an_edl():
    assert video._should_heal_preview(edl_row(7), False, None) is False
    assert video._should_heal_preview(None, True, None) is False


def test_drafting_zero_is_not_confused_with_absent():
    """Version numbers start at 1, so 0 can only be a malformed claim — it must
    behave like no claim, not like a match. (`if drafting:` would have been the
    bug; the comparison is against the version.)"""
    assert video._should_heal_preview(edl_row(1), True, 0) is True


# ── round 94: versions written outside any turn (the MCP surface) ───────────
#
# A remove_erase written over MCP left project 372's studio on "Updating your
# preview…" forever: created_by='agent', the newest agent_turn 'done' (from
# hours earlier), no mcp_tool job live, and the old heal only extended to
# FAILED turns. _agent_version_orphaned is the complete ownership answer.

def test_a_failed_turn_orphans_immediately():
    assert video._agent_version_orphaned("failed", None, False) is True
    assert video._agent_version_orphaned("failed", "done", False) is True


def test_a_live_turn_or_mcp_call_owns_its_render():
    assert video._agent_version_orphaned("running", None, True) is False
    assert video._agent_version_orphaned("queued", None, True) is False
    assert video._agent_version_orphaned("done", "running", True) is False
    assert video._agent_version_orphaned("done", "queued", True) is False


def test_an_idle_mcp_written_version_orphans_after_the_grace_window():
    """Both lanes terminal, version aged past 45s -> heal (the 372 case)."""
    assert video._agent_version_orphaned("done", "done", True) is True
    assert video._agent_version_orphaned(None, "done", True) is True


def test_the_grace_window_protects_a_thinking_mcp_session():
    """Between tool calls an MCP session has NO live job row — a fresh
    version must not be healed out from under the model that is about to
    render it."""
    assert video._agent_version_orphaned("done", "done", False) is False
    assert video._agent_version_orphaned(None, None, False) is False
