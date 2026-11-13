"""#1075 (p5722, 13 Nov 2026): the request brief read a free customer's
request as "clarify", and protected_lanes refused every write with "Clarify
the conflicting request before changing the edit". The editor was never told
what that meant: cut_range and auto_reframe were refused three times each,
three requests delivered nothing, and the customer deleted the project. Five
customers met the mode in ten days. A check repairs or delivers with an honest
note; it never parks the customer's request. Asking stays the editor's own
choice through ask_user."""
import hashlib
from types import SimpleNamespace as NS

import pytest

import request_contract

ASK = "make it vertical and shorter but keep everything they say"


def _ctx(contract=None):
    return NS(request_contract_required=True, verification_request=ASK,
              user_message=ASK, semantic_contract=contract, index={},
              request_contract_history=[],
              latest_edl=lambda: {"version": 1, "json": {"keep": [[0, 60]]}},
              editing_metrics={}, operator_instruction=None,
              request_preservation_baseline=None, request_edit_origin=None)


def _digest():
    return hashlib.sha256(ASK.encode()).hexdigest()


def test_the_brief_offers_no_mode_that_parks_the_request():
    sources = request_contract._customer_sources(ASK, [])
    for schema in (request_contract.CONTRACT_SCHEMA,
                   request_contract._compile_schema(sources)):
        assert schema["properties"]["mode"]["enum"] == ["edit", "inspect"]


def test_a_conflicting_reading_is_read_not_parked():
    text = request_contract.COMPILE
    assert "never parked" in text and "latest words win" in text


def test_a_clarify_answer_is_refused_by_the_schema_and_never_locks_writes(monkeypatch):
    """A model that still answers clarify fails validation; after the retry
    the editor goes on with an open brief, writes allowed."""
    answers = []

    def call(_ctx, _name, schema, _system, _evidence):
        value = {"mode": "clarify", "music_requested": False,
                 "timing_policy": "edit", "output_count": 1, "changed_lanes": [],
                 "requirements": [{"description": "Which one?",
                                   "action": "inspect", "source_ref": 0}]}
        answers.append(value)
        request_contract._validate(value, schema)
        return value
    monkeypatch.setattr(request_contract, "_call", call)
    ctx = _ctx()
    contract = request_contract.ensure(ctx)
    assert len(answers) == 2
    assert contract["mode"] == "open"
    assert request_contract.protected_lanes(ctx) == set()


def test_a_kept_clarify_brief_from_before_the_fix_lets_the_editor_write():
    """The exact state p5722 met: a stored brief in clarify mode. The old code
    raised here on every write."""
    contract = {"mode": "clarify", "music_requested": False,
                "timing_policy": "edit", "output_count": 1, "changed_lanes": [],
                "requirements": [{"id": "r1", "action": "inspect", "lanes": [],
                                  "quote": ASK, "inherited": False,
                                  "description": "Ask which they meant."}],
                "request_digest": _digest(), "schema_version": 1}
    ctx = _ctx(contract)
    assert request_contract.ensure(ctx)["mode"] == "edit"
    assert request_contract.protected_lanes(ctx) == set()


def test_inspect_still_answers_without_writing():
    contract = {"mode": "inspect", "music_requested": False,
                "timing_policy": "preserve", "output_count": 1,
                "changed_lanes": [], "requirements": [],
                "request_digest": _digest(), "schema_version": 1}
    with pytest.raises(ValueError):
        request_contract.protected_lanes(_ctx(contract))
