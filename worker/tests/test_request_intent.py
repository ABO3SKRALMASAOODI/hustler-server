"""Deterministic contracts for a user's latest correction."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools                                             # noqa: E402
import request_intent                                          # noqa: E402


def test_no_captions_is_multilingual_and_blocks_caption_tools():
    for text in ("No captions please", "sin subtítulos",
                 "sem legendas", "字幕なしに戻して"):
        assert request_intent.no_captions(text)

    ctx = SimpleNamespace(user_message="字幕なしに戻して")
    blocked = agent_tools.execute(ctx, "set_caption_style", {"preset": "beast"})
    assert blocked.startswith("REJECTED:")

    blocked = agent_tools.execute(ctx, "add_captions", {"mode": "auto"})
    assert blocked.startswith("REJECTED:")


def test_caption_removal_remains_allowed(monkeypatch):
    ctx = SimpleNamespace(user_message="no subtitles")
    monkeypatch.setitem(agent_tools.TOOLS, "add_captions",
                        (lambda _ctx, **kwargs: kwargs["mode"], None))
    assert agent_tools.execute(ctx, "add_captions", {"mode": "off"}) == "off"


def test_preservation_reset_and_source_reference_contracts():
    preserve = request_intent.request_contract(
        "Keep the original voice and timing unchanged; only fix brightness")
    assert "PRESERVATION LOCK" in preserve
    assert "generic polish" in preserve

    reset = request_intent.request_contract("Reset everything and start over")
    assert "RESET FIRST" in reset

    source = request_intent.request_contract(
        "Create this from scratch using my uploaded source clips")
    assert "SOURCE-MATERIAL CHECK" in source
    assert "STYLE REFERENCE" in source

    reference = request_intent.request_contract(
        "Usando el video que subí como referencia, crea un nuevo edit")
    assert "SOURCE-MATERIAL CHECK" in reference
    assert "never counts as source footage" in reference


def test_latest_message_always_has_priority():
    contract = request_intent.request_contract("make the text blue")
    assert "final user message has highest priority" in contract


def test_business_briefs_are_commercial_music_contexts():
    for text in (
        "Turn this into a premium Instagram ad for our startup",
        "Make a corporate documentary about the company",
        "This is a branded product promo for a client",
    ):
        assert request_intent.commercial_use(text)
    assert not request_intent.commercial_use(
        "Make a cozy personal birthday montage for my family")
