"""Regressions from the automated projects 501-505 production audit."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_prompt  # noqa: E402
import agent_tools  # noqa: E402
from schemas import default_edl  # noqa: E402


class _Ctx:
    has_main_video = True
    duration = 20.0

    def __init__(self, message, job=None):
        self.user_message = message
        self.job = job or {"type": "agent_turn"}
        self._row = {"version": 26, "json": default_edl(self.duration)}
        self.writes = []

    def latest_edl(self):
        return self._row

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        return "EDL v26 -> v27"


def test_rephrased_brief_cannot_destroy_a_valid_edit():
    ctx = _Ctx("Make this a premium 30-50 second startup documentary reel")
    result = agent_tools.reset_edit(ctx)
    assert result.startswith("REJECTED: reset_edit would throw away")
    assert ctx.writes == []


def test_explicit_reset_still_works():
    ctx = _Ctx("Start over from scratch and make a cleaner reel")
    result = agent_tools.reset_edit(ctx)
    assert result.startswith("EDL v26 -> v27")
    assert len(ctx.writes) == 1


def test_direct_mcp_reset_is_itself_explicit():
    ctx = _Ctx("", job={"type": "mcp_tool"})
    result = agent_tools.reset_edit(ctx)
    assert result.startswith("EDL v26 -> v27")
    assert len(ctx.writes) == 1


def test_core_contract_preserves_work_and_bans_caption_stacking():
    prompt = agent_prompt.CORE_PROMPT
    assert "PRESERVE WORK BETWEEN MESSAGES" in prompt
    assert "Never stack new transcript captions" in prompt
    assert "filmstrip itself" in prompt
