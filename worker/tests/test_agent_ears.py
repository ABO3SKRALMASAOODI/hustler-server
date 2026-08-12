"""Proxy listening is retired: no paid model hears for the editor."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import llm                                                     # noqa: E402


def test_proxy_audio_reviewer_code_is_removed():
    assert not hasattr(llm, "audio_review_available")
    assert not hasattr(llm, "ask_audio")


def test_listen_to_is_not_in_any_tool_catalog():
    assert "listen_to" not in agent_tools.TOOLS
    assert "listen_to" not in {
        tool["function"]["name"] for tool in agent_tools.openai_tools()
    }


def test_stale_listen_call_is_rejected_before_execution():
    class Ctx:
        pass

    result = agent_tools.execute(Ctx(), "listen_to", {"times": [1.0]})
    assert result.startswith("Unknown tool 'listen_to'")
