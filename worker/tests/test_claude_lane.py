"""The Claude trial lane's adapter (worker/claude_lane.py).

The loop speaks OpenAI chat shapes; this module must translate them to the
Messages API losslessly, cache at the stability boundaries, translate the
answer back, meter its exact cost, and fail CLOSED on any billing or key
problem (never the paid OpenAI wallet)."""

import json
from types import SimpleNamespace

import pytest

import claude_lane
import claude_trial
import config
import llm
import model_prices


TOOLS = [{"type": "function", "function": {
    "name": "keep_segments", "description": "Keep these ranges.",
    "parameters": {"type": "object", "properties": {
        "segments": {"type": "array"}}}}}]


def _png_url():
    return "data:image/jpeg;base64,QUJD"


def test_leading_systems_become_the_cached_system_prompt():
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "system", "content": "capabilities"},
        {"role": "system", "content": "project"},
        {"role": "user", "content": "cut it"},
    ], TOOLS, "claude-haiku-5-5")
    assert [b["text"] for b in system] == ["kernel", "capabilities", "project"]
    assert msgs == [{"role": "user", "content": [
        {"type": "text", "text": "cut it"}]}]
    assert tools[0]["name"] == "keep_segments"
    assert tools[0]["input_schema"]["type"] == "object"


def test_tool_calls_and_results_pair_up():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "system", "content": "k"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "keep_segments", "arguments": '{"segments": [[0, 4]]}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "OK v2"},
        {"role": "user", "content": [
            {"type": "text", "text": "frame"},
            {"type": "image_url", "image_url": {"url": _png_url()}}]},
    ], TOOLS, "claude-haiku-5-5")
    assert msgs[1]["role"] == "assistant"
    use = msgs[1]["content"][0]
    assert use == {"type": "tool_use", "id": "c1", "name": "keep_segments",
                   "input": {"segments": [[0, 4]]}}
    turn = msgs[2]["content"]
    assert turn[0] == {"type": "tool_result", "tool_use_id": "c1",
                       "content": "OK v2"}
    assert turn[1] == {"type": "text", "text": "frame"}
    assert turn[2]["type"] == "image"
    assert turn[2]["source"] == {"type": "base64", "media_type": "image/jpeg",
                                 "data": "QUJD"}


def test_orphans_are_repaired_never_sent():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "user", "content": "go"},
        # compaction removed the call but left its result
        {"role": "tool", "tool_call_id": "gone", "content": "old result"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function",
             "function": {"name": "keep_segments", "arguments": "{}"}}]},
        # and this call never got a result
    ], TOOLS, "claude-haiku-5-5")
    flat = json.dumps(msgs)
    assert '"tool_use_id": "gone"' not in flat
    assert "Earlier tool result: old result" in flat
    last = msgs[-1]
    assert last["role"] == "user"
    assert last["content"][0]["tool_use_id"] == "c2"
    assert last["content"][0]["is_error"] is True


def test_mid_conversation_system_notes_are_placed_legally():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "user", "content": "filmstrip"},
        {"role": "system", "content": "state block"},   # followed by a user
        {"role": "user", "content": "request"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "keep_segments", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "OK"},
        {"role": "system", "content": "finishing status"},  # last: legal
    ], TOOLS, "claude-haiku-5-5")
    # the illegal one became a marked text block inside the user turn
    first = msgs[0]["content"]
    assert any(b.get("text", "").endswith("state block") and
               b["text"].startswith(claude_lane._SYSTEM_NOTE) for b in first)
    assert msgs[-1] == {"role": "system", "content": "finishing status"}
    # roles strictly alternate between user and assistant before it
    roles = [m["role"] for m in msgs[:-1]]
    assert all(a != b for a, b in zip(roles, roles[1:]))


def test_models_without_mid_system_support_get_text_notes():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "user", "content": "x"},
        {"role": "system", "content": "note"},
    ], TOOLS, "claude-sonnet-5")
    assert all(m["role"] != "system" for m in msgs)


def test_conversation_must_start_with_user_and_not_end_on_assistant():
    _, msgs, _ = claude_lane.to_anthropic([
        {"role": "assistant", "content": "earlier reply"},
    ], TOOLS, "claude-haiku-5-5")
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["role"] == "user"


def test_cache_breakpoints_sit_on_stability_boundaries():
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "system", "content": "capabilities"},
        {"role": "system", "content": "trial note"},
        {"role": "system", "content": "project"},
        {"role": "user", "content": "go"},
    ], TOOLS * 2, "claude-haiku-5-5")
    claude_lane._mark_cache(system, msgs, tools)
    marks = (sum("cache_control" in t for t in tools)
             + sum("cache_control" in b for b in system)
             + sum("cache_control" in b for m in msgs for b in m["content"]))
    assert marks <= 4
    assert "cache_control" in tools[-1] and "cache_control" not in tools[0]
    assert "cache_control" in system[1] and "cache_control" in system[-1]
    assert "cache_control" in msgs[-1]["content"][-1]


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-opus-5-5"])
def test_repeated_editing_is_cached_regardless_of_model(model):
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "kernel"},
        {"role": "system", "content": "project"},
        {"role": "user", "content": "go"},
    ], TOOLS, model)
    claude_lane._mark_cache(system, msgs, tools, model)
    assert "cache_control" in system[-1]
    assert "cache_control" in tools[-1]
    assert "cache_control" in msgs[-1]["content"][-1]


def test_one_call_planner_explicitly_caches_only_shared_prefix():
    system = [{"type": "text", "text": text}
              for text in ("kernel", "capabilities", "unique project")]
    msgs = [{"role": "user", "content": [{"type": "text", "text": "unique frames"}]}]
    tools = [{"name": "edit", "input_schema": {"type": "object"}}]
    claude_lane._mark_cache(system, msgs, tools, "claude-opus-5-5", "shared_only")
    assert "cache_control" in system[1]
    assert "cache_control" not in system[-1]
    assert "cache_control" not in msgs[0]["content"][0]


def test_a_one_shot_critic_caches_only_its_shared_instructions():
    # 1 Nov 2026: the preview critic read 0% of what it wrote, because the
    # last-message breakpoint covered that preview's unique frames.
    system, msgs, tools = claude_lane.to_anthropic([
        {"role": "system", "content": "critic instructions"},
        {"role": "user", "content": "frames of this preview"},
    ], None, "claude-haiku-5-5")
    claude_lane._mark_cache(system, msgs, tools, "claude-haiku-5-5")
    assert "cache_control" in system[0]
    assert not any("cache_control" in b for m in msgs for b in m["content"])


def test_large_observation_batch_keeps_previous_read_with_four_breakpoints():
    import copy
    original = [{"role": "system", "content": s} for s in ("kernel", "directory", "project")]
    original.append({"role": "user", "content": "inspect"})
    before = claude_lane.to_anthropic(original, TOOLS, "claude-haiku-5-5")
    grown = copy.deepcopy(original) + [
        {"role": "assistant", "content": "Looking at the evidence."},
        {"role": "user", "content": [
            {"type": "text", "text": f"observation {i}"} for i in range(25)]}]
    after = claude_lane.to_anthropic(grown, TOOLS, "claude-haiku-5-5")
    diagnostics = []
    for system, msgs, tools in (before, after):
        claude_lane._mark_cache(system, msgs, tools, "claude-haiku-5-5")
        diagnostics.append(claude_lane._cache_diagnostics({
            "model": "claude-haiku-5-5", "system": system,
            "messages": msgs, "tools": tools}))
    prior_messages = {p["sha256"] for p in diagnostics[0]["prefixes"] if p["kind"] == "messages"}
    current_messages = {p["sha256"] for p in diagnostics[1]["prefixes"] if p["kind"] == "messages"}
    assert prior_messages & current_messages  # explicit entry, outside tail's lookback
    assert len(diagnostics[1]["prefixes"]) == 4
    assert "cache_control" not in after[2][-1]  # spare tools slot used by anchor
    assert "cache_control" in after[1][-1]["content"][-1]  # also writes next tail
    assert grown[-1]["content"][0] == {"type": "text", "text": "observation 0"}


def test_contract_compilation_caches_schema_and_instructions_not_unique_payload():
    system = [{"type": "text", "text": "compile request"}]
    msgs = [{"role": "user", "content": [{"type": "text", "text": "customer request"}]}]
    tools = [{"name": "record_request_contract", "input_schema": {"type": "object"}}]
    claude_lane._mark_cache(system, msgs, tools, "claude-haiku-5-5")
    assert "cache_control" in tools[0] and "cache_control" in system[0]
    assert "cache_control" not in msgs[0]["content"][0]


def test_central_recorder_retains_reads_for_non_editor_callers():
    rows = []
    llm.set_recorder(lambda purpose, request, response, usage: rows.append(response))
    try:
        usage = claude_lane._Usage("claude-haiku-5-5", SimpleNamespace(
            input_tokens=17, output_tokens=25, cache_read_input_tokens=2000,
            cache_creation_input_tokens=40))
        usage.prompt_cache = {"version": 1, "prefixes": [{"kind": "system", "sha256": "hash"}]}
        for purpose in ("vision_visual_search", "shorts_plan", "mcp"):
            llm.record(purpose, {"model": "claude-haiku-5-5"}, {"content": "ok"}, usage)
        assert len(rows) == 3
        for row in rows:
            assert row["cached_in"] == 2000
            assert row["cache_write_in"] == 40
            assert row["uncached_in"] == 17
            assert row["prompt_cache"] == usage.prompt_cache
            assert row["provider_cost_usd"] == usage.provider_cost_usd
    finally:
        llm.set_recorder(None)


def test_first_cut_and_indexing_recorders_persist_usage_and_carry_scope():
    import first_cut
    import indexer
    rows = []
    worker_db = SimpleNamespace(run=lambda fn, *args: rows.append(args))
    job = {"id": 101, "project_id": 202}
    scope = object()
    recorders = [first_cut._ScopeRecorder(worker_db, job),
                 indexer._TrialRecorder(scope, worker_db, job)]
    assert recorders[1].claude_trial_scope is scope
    usage = SimpleNamespace(prompt_tokens=1050, completion_tokens=20,
                            prompt_tokens_details={"cached_tokens": 1000})
    try:
        for recorder in recorders:
            llm.set_recorder(recorder)
            # Use an unset lane scope: recording must never meter twice or
            # alter the customer charge; create() owns provider metering.
            recorder.claude_trial_scope = None
            llm.record("vision", {"model": "claude-haiku-5-5"}, {"content": "ok"}, usage)
        assert len(rows) == 2
        for row in rows:
            assert row[:4] == (202, 101, "vision", "claude-haiku-5-5")
            assert row[5]["cached_in"] == 1000
            assert row[6:] == (1050, 20)
    finally:
        llm.set_recorder(None)


def _message(blocks, stop="tool_use", **usage):
    u = dict(input_tokens=20, output_tokens=50, cache_read_input_tokens=1000,
             cache_creation_input_tokens=100)
    u.update(usage)
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(**u), _request_id="req_1")


def test_answers_come_back_in_chat_shape_with_exact_cost():
    msg = _message([
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Cutting."),
        SimpleNamespace(type="tool_use", id="t1", name="keep_segments",
                        input={"segments": [[1, 2]]})])
    out = claude_lane.from_anthropic(msg, "claude-haiku-5-5")
    choice = out.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Cutting."
    call = choice.message.tool_calls[0]
    assert (call.id, call.function.name) == ("t1", "keep_segments")
    assert json.loads(call.function.arguments) == {"segments": [[1, 2]]}
    u = out.usage
    assert u.prompt_tokens == 1120 and u.completion_tokens == 50
    assert llm.cached_input_tokens(u) == 1000
    expected = (20 * 0.10 + 1000 * 0.01 + 100 * 0.125 + 50 * 0.50) / 1e6
    assert u.provider_cost_usd == pytest.approx(expected)
    assert llm.provider_cost_usd(u, "claude-haiku-5-5") == pytest.approx(expected)


def test_haiku_long_prompt_rate_card_applies_to_the_whole_request():
    short = model_prices.claude_usage_cost("claude-haiku-5-5", 1000, 100,
                                           90000, 0)
    long = model_prices.claude_usage_cost("claude-haiku-5-5", 1000, 100,
                                          100000, 0)
    assert short == pytest.approx((1000 * .1 + 90000 * .01 + 100 * .5) / 1e6)
    assert long == pytest.approx((1000 * .5 + 100000 * .05 + 100 * 2.5) / 1e6)
    # an unknown claude model never prices at zero
    assert model_prices.claude_usage_cost("claude-new-9", 1000, 0) > 0


def test_stop_reasons_and_refusals_map_to_the_loop_vocabulary():
    out = claude_lane.from_anthropic(_message(
        [SimpleNamespace(type="text", text="x")], stop="max_tokens"),
        "claude-haiku-5-5")
    assert out.choices[0].finish_reason == "length"
    refused = _message([], stop="refusal")
    refused.stop_details = SimpleNamespace(explanation="declined")
    out = claude_lane.from_anthropic(refused, "claude-haiku-5-5")
    assert out.choices[0].message.refusal == "declined"
    assert out.choices[0].message.content == "declined"
    assert not llm.empty_agent_response(out.choices[0].message,
                                        out.choices[0].finish_reason)


class _Stream:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.result


class _FakeSdk:
    def __init__(self, results):
        self.results = list(results)
        self.bodies = []
        self.messages = self

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        self.bodies.append(body)
        return _Stream(self.results.pop(0))


class _StatusError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


@pytest.fixture
def lane(monkeypatch):
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    metered, refused, paid = [], [], []
    monkeypatch.setattr(claude_trial, "meter",
                        lambda model, usd, kind="llm": metered.append(usd))
    monkeypatch.setattr(claude_trial, "meter_paid",
                        lambda model, usd: paid.append(usd))
    monkeypatch.setattr(claude_trial, "provider_refused",
                        lambda kind, exc: refused.append(kind))
    monkeypatch.setattr(claude_lane.time, "sleep", lambda s: None)
    yield SimpleNamespace(metered=metered, refused=refused, paid=paid,
                          use=lambda sdk: monkeypatch.setattr(
                              claude_lane, "_sdk", lambda: sdk))
    claude_lane.reopen()


def test_the_shim_is_a_drop_in_client_and_meters_each_call(lane):
    sdk = _FakeSdk([_message([SimpleNamespace(type="text", text="done")],
                             stop="end_turn")])
    lane.use(sdk)
    client = llm.without_sdk_retries(claude_lane.ClaudeLaneClient())
    resp = client.chat.completions.create(
        model="claude-haiku-5-5",
        messages=[{"role": "system", "content": "k"},
                  {"role": "user", "content": "hi"}],
        tools=TOOLS, max_completion_tokens=4000, temperature=0.2,
        reasoning_effort="low", service_tier="priority")
    assert resp.choices[0].message.content == "done"
    body = sdk.bodies[0]
    assert body["max_tokens"] == 4000
    assert body["output_config"] == {"effort": "low"}
    assert resp.usage.prompt_cache["prefixes"]
    assert "temperature" not in body and "service_tier" not in body
    # no trial scope on this thread: a subscriber's call, metered into the
    # credit cycle's ledger (claude_credit), never into a trial budget
    assert lane.metered == [] and len(lane.paid) == 1 and lane.paid[0] > 0
    scope = claude_trial.Scope(5, 1.0, 0.0, "claude-haiku-5-5")
    claude_trial.activate(scope)
    try:
        lane.use(_FakeSdk([_message([SimpleNamespace(type="text", text="ok")],
                                    stop="end_turn")]))
        client.chat.completions.create(
            model="claude-haiku-5-5",
            messages=[{"role": "user", "content": "hi"}])
    finally:
        claude_trial.deactivate()
    assert len(lane.metered) == 1 and lane.metered[0] > 0
    assert len(lane.paid) == 1


def _wallets(monkeypatch, *, openai="sk-openai", failover=True, frontier_explicit=False,
             paid_explicit=False):
    """The box's provider config: FRONTIER_* and PAID_* inherit the Grok Imagine key (IMAGE_API_KEY,
    same api.x.ai URL) unless set for themselves."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", openai)
    monkeypatch.setattr(config, "IMAGE_API_KEY", "xai-image")
    for prefix, explicit in (("FRONTIER", frontier_explicit), ("PAID", paid_explicit)):
        monkeypatch.setattr(config, prefix + "_BASE_URL", "https://api.x.ai/v1")
        monkeypatch.setattr(config, prefix + "_API_KEY", f"xai-{prefix.lower()}" if explicit else "xai-image")
        monkeypatch.setattr(config, prefix + "_API_KEY_EXPLICIT", explicit)
    monkeypatch.setattr(config, "PAID_FAILOVER_BASE_URL", "https://failover.example/v1" if failover else "")
    monkeypatch.setattr(config, "PAID_FAILOVER_API_KEY", "fo-key" if failover else "")
    monkeypatch.setattr(config, "PAID_FAILOVER_MODEL", "failover-model" if failover else "")
    for name, tag in (("client", "openai"), ("paid_failover_client", "failover"),
                      ("frontier_client", "frontier"), ("paid_client", "paid")):
        monkeypatch.setattr(llm, name, lambda tag=tag: tag)


def test_recovery_never_spends_the_image_wallet_and_asks_the_funded_failover_first(monkeypatch):
    """pr179 review: with only IMAGE_API_KEY and OPENAI_API_KEY on the box, every refused Claude call went
    first to grok-4.5 on the Grok Imagine wallet (FRONTIER_API_KEY inherits it), ahead of the
    separately funded R5 failover; draining it would stop image generation for every customer."""
    _wallets(monkeypatch)
    assert list(llm.provider_recovery_clients()) == [
        ("failover-model", "failover"), (config.EDITOR_MODEL, "openai")]
    _wallets(monkeypatch, failover=False)
    assert list(llm.provider_recovery_clients()) == [(config.EDITOR_MODEL, "openai")]
    # an xAI key set for its own lane is the owner's choice: asked, after the funded wallets
    _wallets(monkeypatch, frontier_explicit=True, paid_explicit=True)
    assert [tag for _, tag in llm.provider_recovery_clients()] == ["failover", "openai", "frontier", "paid"]


def test_the_box_config_infers_no_editing_wallet_from_the_image_key():
    """The real config import with the box's variables only (box-provision.yml): no xAI recovery wallet."""
    import os
    import subprocess
    import sys
    worker = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FRONTIER_", "PAID_", "IMAGE_", "VISION_", "OPENAI_", "ANTHROPIC_"))}
    env.update(IMAGE_API_KEY="xai-image-test", OPENAI_API_KEY="sk-openai-test",
               PYTHONPATH=os.pathsep.join([worker, os.path.dirname(worker)]))
    out = subprocess.run(
        [sys.executable, "-c", "import config, llm; print(config.FRONTIER_API_KEY == config.IMAGE_API_KEY, "
         "config.FRONTIER_API_KEY_EXPLICIT, config.PAID_API_KEY_EXPLICIT, "
         "[m for m, _ in llm.provider_recovery_clients()])"],
        cwd=worker, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip().splitlines()[-1] == "True False False ['gpt-6-luna']"


@pytest.mark.parametrize(("status", "kind"), [(401, "auth"), (402, "billing")])
def test_refused_request_refreshes_then_finishes_on_the_funded_failover(lane, monkeypatch, status, kind):
    refused = _StatusError(status, "provider refusal")
    sdk = _FakeSdk([refused, refused])
    lane.use(sdk)
    _wallets(monkeypatch)
    requests = []
    def complete(client_obj, model, messages, **kwargs):
        requests.append((client_obj, model, messages, kwargs["tools"]))
        return SimpleNamespace(usage=SimpleNamespace(), choices=["finished"])
    monkeypatch.setattr(llm, "create_with_dialect", complete)
    messages = [{"role": "user", "content": "cut the pauses"}]
    result = claude_lane.create("claude-haiku-5-5", messages, tools=TOOLS)
    assert result.choices == ["finished"]
    assert requests == [("failover", "failover-model", messages, TOOLS)]
    assert len(sdk.bodies) == 2 and lane.refused == [kind]
    assert claude_lane.available()
    assert result.usage.answered_model == "failover-model"


class _RetryCounting:
    """An OpenAI-SDK-shaped client: with_options records what each call was allowed."""

    def __init__(self):
        self.options = []

    def with_options(self, **kw):
        self.options.append(kw)
        return self


def test_recovery_calls_send_one_request_each_and_a_rate_limit_reaches_the_loop(lane, monkeypatch):
    """Finding 7: the recovery clients carry LLM_MAX_RETRIES (5); a slow or 429ing provider was retried
    inside the SDK for minutes before the next wallet was asked or the loop could wait."""
    refused = _StatusError(401, "invalid x-api-key")
    first, second = _RetryCounting(), _RetryCounting()
    monkeypatch.setattr(llm, "provider_recovery_clients",
                        lambda: iter([("grok-4.5", first), ("gpt-6-luna", second)]))
    seen = []

    def complete(client_obj, model, messages, **kwargs):
        seen.append((model, kwargs.get("deadline"), kwargs.get("timeout")))
        if model == "grok-4.5":
            raise _StatusError(503, "upstream unavailable")
        return SimpleNamespace(usage=SimpleNamespace(), choices=["finished"])
    monkeypatch.setattr(llm, "create_with_dialect", complete)
    lane.use(_FakeSdk([refused, refused]))
    import time
    t0 = time.monotonic()
    out = claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert out.choices == ["finished"]
    assert first.options == [{"max_retries": 0}] and second.options == [{"max_retries": 0}]
    # one deadline for the whole chain, the Claude call's own bound; no fresh timeout per wallet
    assert [m for m, _, _ in seen] == ["grok-4.5", "gpt-6-luna"] and seen[0][1] == seen[1][1]
    assert seen[0][2] is None
    assert t0 + config.CLAUDE_TRIAL_TIMEOUT_S <= seen[0][1] <= time.monotonic() + config.CLAUDE_TRIAL_TIMEOUT_S
    # the chain ends on a rate limit: the 429 itself, so the loop's budget-aware wait runs
    limited = _StatusError(429, "rate_limit_error")
    monkeypatch.setattr(llm, "provider_recovery_clients", lambda: iter([("grok-4.5", first)]))
    monkeypatch.setattr(llm, "create_with_dialect",
                        lambda *a, **k: (_ for _ in ()).throw(limited))
    lane.use(_FakeSdk([refused, refused]))
    with pytest.raises(_StatusError) as err:
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}], timeout=40)
    assert err.value is limited


def _luna_on(monkeypatch, handler):
    """The real OpenAI SDK client (LLM_MAX_RETRIES) on a mock transport, as the only recovery wallet, and
    a fake clock both modules read: each request takes the time handler says, nothing really waits."""
    import time as real
    import httpx
    import openai_credit
    from openai import OpenAI
    now, slept = [1000.0], []
    fake = type(real)("fake_time")
    fake.__dict__.update({k: getattr(real, k) for k in dir(real) if not k.startswith("_")})
    fake.monotonic = lambda: now[0]
    fake.sleep = lambda s: (slept.append(s), now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(llm, "time", fake)
    monkeypatch.setattr(claude_lane, "time", fake)
    monkeypatch.setattr(llm, "fence", lambda model: None)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    openai_credit.reopen()
    sent = []

    def serve(request):
        sent.append(request.extensions["timeout"]["read"])
        return handler(request, now)
    luna = OpenAI(base_url="https://api.openai.com/v1", api_key="sk-test", max_retries=config.LLM_MAX_RETRIES,
                  http_client=httpx.Client(transport=httpx.MockTransport(serve)))
    monkeypatch.setattr(llm, "provider_recovery_clients", lambda: iter([("gpt-6-luna", luna)]))
    return now, slept, sent


def test_a_hung_recovery_wallet_ends_inside_the_claude_call_bound(lane, monkeypatch):
    """pr179 review: create_with_dialect retried api.openai.com five more times at 300 s each, so one step
    could block for about 30 minutes before the agent loop heard of it."""
    import httpx

    def hang(request, now):
        now[0] += request.extensions["timeout"]["read"]
        raise httpx.ReadTimeout("timed out", request=request)
    now, slept, sent = _luna_on(monkeypatch, hang)
    lane.use(_FakeSdk([_StatusError(401, "invalid x-api-key")] * 2))
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert sent == [config.CLAUDE_TRIAL_TIMEOUT_S] and slept == []
    assert now[0] - 1000.0 <= config.CLAUDE_TRIAL_TIMEOUT_S


def test_a_recovery_429_reaches_the_loop_after_one_request(lane, monkeypatch):
    """pr179 review: a 429 'try again in 60s' was slept on 5 x 60 s inside create_with_dialect (no lease
    heartbeat) before the loop's budget-aware wait saw it."""
    import httpx
    import openai

    def limited(request, now):
        return httpx.Response(429, headers={"retry-after": "60"}, json={"error": {
            "message": "Rate limit reached for gpt-6-luna. Please try again in 60s.",
            "type": "requests", "code": "rate_limit_exceeded"}})
    now, slept, sent = _luna_on(monkeypatch, limited)
    lane.use(_FakeSdk([_StatusError(401, "invalid x-api-key")] * 2))
    with pytest.raises(openai.RateLimitError) as err:
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert llm.is_rate_limit_error(err.value)
    assert len(sent) == 1 and slept == []


def test_a_recovery_blip_is_asked_again_inside_the_same_deadline(lane, monkeypatch):
    """The outcome is kept: a quick 503 is repeated after the SDK's short backoff, on the time left."""
    import httpx
    replies = [httpx.Response(503, json={"error": {"message": "overloaded"}}),
               httpx.Response(200, json={
                   "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-6-luna",
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "done"}}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})]
    now, slept, sent = _luna_on(monkeypatch, lambda request, now: replies.pop(0))
    lane.use(_FakeSdk([_StatusError(401, "invalid x-api-key")] * 2))
    out = claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert out.choices[0].message.content == "done"
    assert slept == [0.5] and sent == [config.CLAUDE_TRIAL_TIMEOUT_S, config.CLAUDE_TRIAL_TIMEOUT_S - 0.5]


def test_a_refused_film_call_recovers_and_never_closes_the_lane(lane, monkeypatch):
    """#1796 / finding 1: score.call.stream latched the lane for 10 minutes on one refused lookup,
    and every later create, trial resolve and paid lane in the process skipped Anthropic and its
    recovery. Now the film call re-reads the key, asks once more, then answers on a recovery
    provider inside its deadline, and the next call asks Anthropic again."""
    import time
    from score import call
    refused = _StatusError(401, "invalid x-api-key (key expired)")
    lane.use(_FakeSdk([refused, refused]))
    refreshed, recorded = [], []
    monkeypatch.setattr(claude_lane, "_refresh_live_key", lambda: refreshed.append(1))
    monkeypatch.setattr(claude_trial, "guard_call", lambda *a: None)
    monkeypatch.setattr(llm, "record", lambda purpose, req, resp, usage=None: recorded.append((req, resp)))
    sent = []

    def complete(client_obj, model, messages, **kwargs):
        sent.append((model, messages, kwargs))
        return SimpleNamespace(
            id="r1", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, cost_in_usd_ticks=40_000_000),
            choices=[SimpleNamespace(finish_reason="stop",
                                     message=SimpleNamespace(content='{"subjects": []}'))])
    monkeypatch.setattr(llm, "provider_recovery_clients", lambda: iter([("grok-4.5", _RetryCounting())]))
    monkeypatch.setattr(llm, "create_with_dialect", complete)
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    r = call.stream("claude-haiku-5-5", "name subjects", [image, {"type": "text", "text": "Oscar Piastri"}],
                    max_tokens=600, effort="low", purpose="named_subjects", thinking=False,
                    deadline=time.monotonic() + 10)
    assert r.text == '{"subjects": []}' and r.stop == "end_turn"
    assert r.usage.output_tokens == 20 and abs(r.usd - 0.004) < 1e-9
    assert refreshed == [1] and lane.refused == ["auth"]
    model, messages, kwargs = sent[0]
    assert model == "grok-4.5" and 0 < kwargs["deadline"] - time.monotonic() <= 10
    assert "timeout" not in kwargs
    assert messages[0] == {"role": "system", "content": "name subjects"}
    assert messages[1]["content"][0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert recorded[0][0]["model"] == "grok-4.5" and recorded[0][1]["provider_fallback"] is True
    assert claude_lane.available()
    # the next call reaches Anthropic: nothing latched
    ok = _FakeSdk([_message([SimpleNamespace(type="text", text="ok")], stop="end_turn")])
    lane.use(ok)
    assert claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}]
                              ).choices[0].message.content == "ok"
    assert len(ok.bodies) == 1


def test_a_film_call_on_a_just_rotated_key_answers_on_claude_without_an_alert(lane, monkeypatch):
    import time
    from score import call

    class _Text:
        text_stream = ["ok"]
        request_id = "req_2"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return _message([], stop="end_turn")
    sdk = _FakeSdk([_StatusError(401, "invalid x-api-key")])
    sdk.stream = lambda **body: (sdk.bodies.append(body),
                                 _Stream(sdk.results.pop(0)) if sdk.results else _Text())[1]
    lane.use(sdk)
    monkeypatch.setattr(claude_lane, "_refresh_live_key", lambda: None)
    monkeypatch.setattr(claude_trial, "guard_call", lambda *a: None)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    monkeypatch.setattr(llm, "provider_recovery_clients",
                        lambda: pytest.fail("the retry on the live key answered"))
    r = call.stream("claude-haiku-5-5", "s", "c", max_tokens=50, effort="low", purpose="named_subjects",
                    thinking=False, deadline=time.monotonic() + 10)
    assert r.text == "ok" and len(sdk.bodies) == 2 and lane.refused == []


def test_overloaded_is_retried_but_rate_limits_reach_the_loop(lane):
    ok = _message([SimpleNamespace(type="text", text="ok")], stop="end_turn")
    sdk = _FakeSdk([_StatusError(529, "overloaded"), ok])
    lane.use(sdk)
    assert claude_lane.create("claude-haiku-5-5", [
        {"role": "user", "content": "x"}]).choices[0].message.content == "ok"
    limited = _StatusError(429, "rate_limit_error")
    lane.use(_FakeSdk([limited]))
    with pytest.raises(_StatusError) as err:
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])
    assert llm.is_rate_limit_error(err.value)


def test_no_key_means_closed_never_another_wallet(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert not claude_lane.configured()
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5", [{"role": "user", "content": "x"}])


def test_forced_tool_choice_degrades_on_models_that_reject_it():
    assert claude_lane._tool_choice("required", "claude-sonnet-5-5") is None
    assert claude_lane._tool_choice("required", "claude-haiku-5-5") == {"type": "any"}
    assert claude_lane._tool_choice("none", "claude-sonnet-5-5") == {"type": "none"}


def test_lane_dict_carries_no_key():
    lane = claude_lane.lane("claude-haiku-5-5")
    assert lane["provider"] == "anthropic"
    assert lane["api_key"] == "anthropic"
    assert not llm.responses_available(lane["model"], lane["base_url"],
                                       effort="high")
