"""The chat text informs judgment but never derives tool permissions."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import request_intent                                          # noqa: E402


def test_request_contract_is_language_and_keyword_invariant():
    messages = (
        "No captions please",
        "Reset everything and start over",
        "Make a professional branded promo",
        "字幕なしに戻して",
        "",
    )
    contracts = {request_intent.request_contract(text) for text in messages}
    assert len(contracts) == 1
    contract = contracts.pop()
    assert "final user message has highest priority" in contract
    assert "use any available editing tool" in contract
    assert "no keyword or regex grants or withholds" in contract
    assert "permission to write the EDL" in contract


def test_regex_permission_helpers_are_gone():
    for name in ("no_captions", "preservation_requested",
                 "broad_polish_requested", "commercial_use",
                 "explicit_reset_requested"):
        assert not hasattr(request_intent, name)


def test_dispatch_does_not_block_tools_from_chat_keywords(monkeypatch):
    called = []

    def fake_caption(_ctx, **kwargs):
        called.append(kwargs)
        return "dispatched"

    original = agent_tools.TOOLS["add_captions"]
    monkeypatch.setitem(agent_tools.TOOLS, "add_captions",
                        (fake_caption, original[1], original[2]))
    ctx = SimpleNamespace(user_message="no captions; reset nothing")
    assert agent_tools.execute(ctx, "add_captions", {"mode": "auto"}) == \
        "dispatched"
    assert called == [{"mode": "auto"}]
