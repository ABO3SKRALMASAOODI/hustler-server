"""Customer wording and action types survive the real compiler boundary."""
from types import SimpleNamespace as NS

import pytest

import request_contract as rc


def context(text, conversation=()):
    return NS(request_contract_required=True, verification_request=text,
              user_message=text, request_contract_history=list(conversation),
              index={}, latest_edl=lambda: {"version": 1, "json": {}},
              semantic_contract=None)


def wire(action="change", lanes=(), source=0, mode="edit"):
    return {"mode": mode, "music_requested": False, "timing_policy": "edit",
            "output_count": 1, "requirements": [{"description": "Requested work",
                "source_ref": source, "action": action,
                **({"lanes": list(lanes)} if lanes or action in {"preserve", "avoid"} else {})}]}


@pytest.mark.parametrize("text", [
    'Conserva las voces originales — sin subtítulos.',
    '“FINAL” faylını yoxla; subtitrləri dəyişmə.',
    'حافظ على الصوت الأصلي.',
])
def test_customer_quote_is_resolved_from_source_not_regenerated(monkeypatch, text):
    ctx = context(text)
    def provider(_ctx, _name, schema, _system, evidence):
        assert evidence["customer_sources"] == {0: text}
        result = wire()
        rc._validate(result, schema)
        return result
    monkeypatch.setattr(rc, "_call", provider)
    contract = rc.ensure(ctx)
    assert contract["requirements"][0]["quote"] == text
    assert contract["requirements"][0]["id"] == "r1"


def test_prior_customer_direction_is_available_but_assistant_promises_are_not(monkeypatch):
    ctx = context("2", [{"role": "user", "content": "Keep the original audio muted."},
                        {"role": "assistant", "content": "I will add arbitrary new effects."}])
    def provider(_ctx, _name, schema, _system, evidence):
        assert list(evidence["customer_sources"].values()) == ["2", "Keep the original audio muted."]
        result = wire("preserve", ["source_audio"], source=1)
        rc._validate(result, schema)
        return result
    monkeypatch.setattr(rc, "_call", provider)
    assert rc.ensure(ctx)["requirements"][0]["quote"] == "Keep the original audio muted."


@pytest.mark.parametrize("action,lanes", [
    ("change", ["music"]), ("inspect", ["cuts"]), ("avoid", ["effects"]),
])
def test_invalid_protection_shapes_cannot_pass_the_provider_schema(action, lanes):
    schema = rc._compile_schema({0: "Customer direction"})
    with pytest.raises(ValueError):
        rc._validate(wire(action, lanes), schema)


@pytest.mark.parametrize("action,lanes", [
    ("change", []), ("inspect", []), ("preserve", ["timeline_timing"]),
    ("avoid", ["music"]), ("avoid", []),
])
def test_allowed_action_shapes_remain_available(action, lanes):
    rc._validate(wire(action, lanes), rc._compile_schema({0: "Customer direction"}))


def test_source_reference_cannot_escape_real_customer_instructions():
    with pytest.raises(ValueError):
        rc._validate(wire(source=2), rc._compile_schema({0: "Customer direction"}))


def test_inspection_criteria_never_become_editing_work(monkeypatch):
    ctx = context("Review whether the original voice is preserved; do not edit.")
    monkeypatch.setattr(rc, "_call", lambda *args: wire("preserve", ["source_audio"], mode="inspect"))
    contract = rc.ensure(ctx)
    assert contract["requirements"][0]["action"] == "inspect"
    assert contract["requirements"][0]["lanes"] == []
    with pytest.raises(ValueError, match="analysis"):
        rc.protected_lanes(ctx)


def test_large_customer_briefs_do_not_expand_schema_enumerations():
    schema = rc._compile_schema(rc._customer_sources("\n".join(f"Direction {i}" for i in range(1500)), []))
    rc._validate(wire(source=1499), schema)
    assert len(__import__("json").dumps(schema)) < 10000


def test_trial_provider_keeps_strict_shapes_and_local_bounds():
    import claude_lane
    schema = rc._compile_schema({0: "Preserve original voice."})
    tool = {"type": "function", "function": {"name": "record_request_contract",
            "parameters": schema, "strict": True}}
    _, _, translated = claude_lane.to_anthropic(
        [{"role": "user", "content": "Record the brief."}], [tool], "claude-haiku-5-5")
    assert translated[0]["strict"] is True
    wire_schema = translated[0]["input_schema"]
    for candidate in (schema, wire_schema):
        with pytest.raises(ValueError):
            rc._validate(wire("change", ["music"]), candidate)
    assert schema["properties"]["output_count"]["maximum"] == 100
    assert "maximum" not in wire_schema["properties"]["output_count"]
    with pytest.raises(ValueError):
        rc._validate(wire(source=10), schema)
    properties = {"type": "object", "properties": {"maximum": {"type": "number", "minimum": 0}}}
    assert "maximum" in claude_lane._strict_schema(properties)["properties"]


def test_company_repair_stays_on_anthropic_even_without_openai_credentials(monkeypatch):
    import llm, claude_lane, config
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(llm, "paid_editor_lanes", lambda *args: pytest.fail("OpenAI recovery route"))
    monkeypatch.setattr(llm, "agent_client_for", lambda *args, **kwargs:
                        pytest.fail("Failed ordinary route selected for company repair"))
    lanes = llm.agent_lanes_for(False, "free", operator_repair=True)
    assert len(lanes) == 1 and lanes[0]["provider"] == "anthropic"
    assert lanes[0]["model"].startswith("claude-")
    assert llm.editor_reasoning_effort("free", 8, operator_repair=True) == "high"


def test_recovery_auxiliary_calls_use_the_same_provider_and_clear_after_turn(monkeypatch):
    import llm, claude_lane, claude_trial
    monkeypatch.setattr(claude_trial, "current", lambda: None)
    lane = claude_lane.lane("claude-opus-5-5", name="claude_recovery")
    llm.set_turn_plan("free", project_id=9, lane=lane)
    try:
        assert llm.claude_trial_lane() == (lane["client"], lane["model"])
        assert llm.vision_client_for("free") == (lane["client"], lane["model"])
        assert llm.agent_client_for(True, "advanced") == (lane["client"], lane["model"])
    finally:
        llm.clear_turn_plan()
    assert llm.claude_trial_lane() is None and llm.turn_lane() is None


def test_unavailable_anthropic_does_not_silently_restore_openai_recovery(monkeypatch):
    import llm, claude_lane
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    with pytest.raises(RuntimeError, match="Anthropic recovery"):
        llm.agent_lanes_for(True, "advanced", operator_repair=True)


@pytest.mark.parametrize("on", [True, False])
def test_company_recovery_respects_the_existing_anthropic_credit_line(monkeypatch, on):
    import agent_loop, claude_trial
    monkeypatch.setattr(claude_trial, "credit_status", lambda db: {"on": on})
    if on:
        agent_loop._require_recovery_credit_line(object())
    else:
        with pytest.raises(RuntimeError, match="credit line is paused"):
            agent_loop._require_recovery_credit_line(object())
