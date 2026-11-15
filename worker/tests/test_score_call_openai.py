"""Lane Q: the Director's OpenAI rung (score/call_openai.py) and its one seam in score.call.stream."""
import json
import time

import httpx
import pytest

from score import call, call_openai, flags


def _admit(cfg, user_id, **kw):
    base = dict(trial_active=True, subscribed=False, engine_on=True, source_s=30.0, url=None)
    base.update(kw)
    return flags.admit(cfg, user_id, **base)


def test_a_row_naming_an_openai_model_admits_only_its_test_accounts():
    cfg = flags.parse('{"mode": "on", "percent": 100, "test_user_ids": [5], "models": {"direct": "gpt-5.5"}}')
    assert cfg["models"]["direct"] == "gpt-5.5"
    assert cfg["mode"] == "off"
    assert _admit(cfg, 5) == (True, "")
    assert all(_admit(cfg, u)[0] is False for u in range(6, 300))      # no customer, whatever the row's mode
    assert _admit(cfg, 5, subscribed=True) == (False, "subscribed")


def test_an_unknown_model_is_still_a_bad_row_and_claude_rows_are_unchanged():
    assert flags.parse('{"mode": "on", "models": {"direct": "grok-4.6"}}') == flags.DEFAULTS
    on = flags.parse('{"mode": "on"}')
    assert on["mode"] == "on" and on["percent"] == 100 and on["models"] == flags.DEFAULTS["models"]


def test_claude_blocks_become_responses_parts():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    got = call_openai.parts([{"type": "text", "text": "hello"}, img])
    assert got == [{"type": "input_text", "text": "hello"},
                   {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD", "detail": "high"}]
    assert call_openai.instructions([{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                                     {"type": "text", "text": "b"}]) == "a\n\nb"
    assert call_openai.body("gpt-5.5", "s", "c", 100, "max", "director_direct")["reasoning"] == {"effort": "xhigh"}


def test_the_seam_sends_an_openai_model_to_the_rung_and_never_to_the_claude_lane(monkeypatch):
    seen = {}

    def fake(model, system, content, **kw):
        seen.update(model=model, purpose=kw["purpose"])
        return call.Result("ok", None, "end_turn", 0.1, 0.2, 0.0)
    monkeypatch.setattr(call_openai, "stream", fake)
    monkeypatch.setattr(call.claude_lane, "configured", lambda: pytest.fail("the Claude lane was asked"))
    r = call.stream("gpt-6-sol", "sys", [{"type": "text", "text": "x"}], max_tokens=10, effort="low",
                    purpose="director_direct", deadline=time.monotonic() + 5)
    assert r.text == "ok" and seen == {"model": "gpt-6-sol", "purpose": "director_direct"}


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


_REAL_CLIENT = httpx.Client


def _client_with(monkeypatch, handler):
    real = _REAL_CLIENT
    monkeypatch.setattr(call_openai.config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(call_openai.config, "OPENAI_BASE_URL", "https://api.openai.com/v1", raising=False)
    monkeypatch.setattr(call_openai.llm, "fence", lambda model: None)
    recorded = []
    monkeypatch.setattr(call_openai.llm, "record", lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return recorded


def test_a_streamed_answer_is_read_metered_and_priced(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True and body["model"] == "gpt-5.5"
        return httpx.Response(200, content=_sse(
            {"type": "response.output_text.delta", "delta": "Score "},
            {"type": "response.output_text.delta", "delta": "line"},
            {"type": "response.completed", "response": {"id": "resp_1", "status": "completed", "usage": {
                "input_tokens": 1000, "input_tokens_details": {"cached_tokens": 400}, "output_tokens": 100}}}))
    recorded = _client_with(monkeypatch, handler)
    r = call.stream("gpt-5.5", "sys", "go", max_tokens=500, effort="low", purpose="director_direct",
                    deadline=time.monotonic() + 10)
    assert r.text == "Score line" and r.stop == "end_turn"
    assert r.usd == pytest.approx((600 * 5.0 + 400 * 0.5 + 100 * 30.0) / 1e6)
    assert r.usage.cache_read_input_tokens == 400 and r.usage.input_tokens == 600 and r.usage.output_tokens == 100
    assert recorded and recorded[0][0] == "director_direct"


def test_a_cut_answer_reads_as_max_tokens_and_an_http_error_is_raised(monkeypatch):
    _client_with(monkeypatch, lambda req: httpx.Response(200, content=_sse(
        {"type": "response.output_text.delta", "delta": "half"},
        {"type": "response.incomplete", "response": {"status": "incomplete", "usage": {"input_tokens": 10, "output_tokens": 5}}})))
    r = call.stream("gpt-6-sol", "s", "c", max_tokens=5, effort="low", purpose="p", deadline=time.monotonic() + 10)
    assert r.stop == "max_tokens" and r.text == "half"
    _client_with(monkeypatch, lambda req: httpx.Response(429, content=b"slow down"))
    with pytest.raises(RuntimeError, match="429"):
        call.stream("gpt-6-sol", "s", "c", max_tokens=5, effort="low", purpose="p", deadline=time.monotonic() + 10)


def test_no_openai_key_refuses(monkeypatch):
    monkeypatch.setattr(call_openai.config, "OPENAI_API_KEY", "", raising=False)
    with pytest.raises(call.Refused):
        call.stream("gpt-6-sol", "s", "c", max_tokens=5, effort="low", purpose="p", deadline=time.monotonic() + 10)


def test_director_uses_configured_local_responses_endpoint(monkeypatch):
    import openai_budget
    import openai_credit
    monkeypatch.setenv("CODEX_PROXY_LOCAL", "1")
    monkeypatch.setattr(openai_budget, "reserve", lambda *a, **k: pytest.fail("local call reserved paid credit"))
    monkeypatch.setattr(openai_credit, "available", lambda: False)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        assert request.headers["authorization"] == "Bearer local-codex-only"
        return httpx.Response(200, content=_sse(
            {"type": "response.output_text.delta", "delta": "local score"},
            {"type": "response.completed", "response": {
                "status": "completed", "usage": {"input_tokens": 4, "output_tokens": 2}}}))

    _client_with(monkeypatch, handler)
    monkeypatch.setattr(call_openai.config, "OPENAI_BASE_URL", "http://127.0.0.1:8787/v1/")
    monkeypatch.setattr(call_openai.config, "OPENAI_API_KEY", "local-codex-only")
    result = call.stream("gpt-6-sol", "s", "c", max_tokens=10, effort="low",
                         purpose="director_scout", deadline=time.monotonic() + 5)
    assert result.text == "local score"
    assert result.usd == result.usage.provider_cost_usd == 0.0
    assert seen == ["http://127.0.0.1:8787/v1/responses"]


@pytest.mark.parametrize('base', ['http://127.0.0.1:8787/v1/', 'https://proxy.example/v1'])
def test_director_uses_the_configured_responses_host(monkeypatch, base):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        assert str(request.url) == base.rstrip('/') + '/responses'
        assert request.headers['authorization'] == 'Bearer local-codex-only'
        return httpx.Response(200, content=_sse(
            {'type': 'response.output_text.delta', 'delta': 'Score'},
            {'type': 'response.completed', 'response': {'status': 'completed',
                'usage': {'input_tokens': 10, 'output_tokens': 1}}}))
    _client_with(monkeypatch, handler)
    monkeypatch.setattr(call_openai.config, 'OPENAI_BASE_URL', base)
    monkeypatch.setattr(call_openai.config, 'OPENAI_API_KEY', 'local-codex-only')
    result = call.stream('gpt-6-sol', 's', 'c', max_tokens=5, effort='low',
                         purpose='p', deadline=time.monotonic() + 10)
    assert result.text == 'Score' and len(seen) == 1


def test_empty_responses_host_refuses_before_transport(monkeypatch):
    monkeypatch.setattr(call_openai.config, 'OPENAI_API_KEY', 'local-codex-only')
    monkeypatch.setattr(call_openai.config, 'OPENAI_BASE_URL', '')
    monkeypatch.setattr(httpx, 'Client', lambda **kw: pytest.fail('transport must not start'))
    with pytest.raises(call.Refused, match='no base URL'):
        call.stream('gpt-6-sol', 's', 'c', max_tokens=5, effort='low',
                    purpose='p', deadline=time.monotonic() + 10)


def test_director_tier_is_explicit_and_keeps_model_reasoning_and_budget(monkeypatch):
    monkeypatch.setattr(call_openai.config, 'OPENAI_SERVICE_TIER', '')
    monkeypatch.setattr(call_openai.config, 'DIRECTOR_SERVICE_TIER', '')
    before = call_openai.body('gpt-6.1-sol', 's', 'c', 9000, 'high', 'director_direct')
    assert 'service_tier' not in before
    monkeypatch.setattr(call_openai.config, 'DIRECTOR_SERVICE_TIER', 'fast')
    after = call_openai.body('gpt-6.1-sol', 's', 'c', 9000, 'high', 'director_direct')
    assert after.pop('service_tier') == 'fast'
    assert after == before


@pytest.mark.parametrize('served,multiplier', [('fast', 2), ('priority', 2), ('default', 1)])
def test_stream_cost_uses_actual_top_level_serving_tier(monkeypatch, served, multiplier):
    usage = {'input_tokens': 1000, 'input_tokens_details': {'cached_tokens': 400}, 'output_tokens': 100}
    monkeypatch.setattr(call_openai.config, 'DIRECTOR_SERVICE_TIER', 'fast')
    def handler(request):
        assert json.loads(request.content)['service_tier'] == 'fast'
        return httpx.Response(200, content=_sse(
            {'type': 'response.output_text.delta', 'delta': 'Score'},
            {'type': 'response.completed', 'response': {
                'id': 'resp_tier', 'status': 'completed', 'service_tier': served, 'usage': usage}}))
    _client_with(monkeypatch, handler)
    result = call.stream('gpt-6.1-sol', 's', 'c', max_tokens=500, effort='low',
                         purpose='director_direct', deadline=time.monotonic()+10)
    base = call_openai.Usage('gpt-6.1-sol', usage).provider_cost_usd
    assert result.usage.service_tier == served
    assert result.usd == pytest.approx(multiplier * base)


@pytest.mark.parametrize("url", ["https://api.openai.com/v1/responses",
    "http://127.0.0.1.evil.test:8787/v1/responses",
    "http://127.0.0.1:8787/paid", "http://127.0.0.1:8888/v1/responses",
    "http://127.0.0.1:8787/v1/responses?forward=paid"])
def test_subscription_flag_cannot_exempt_another_transport(monkeypatch, url):
    monkeypatch.setenv("CODEX_PROXY_LOCAL", "1")
    assert not call_openai.subscription_endpoint(url)


def test_local_endpoint_without_subscription_optin_remains_metered(monkeypatch):
    monkeypatch.delenv("CODEX_PROXY_LOCAL", raising=False)
    assert not call_openai.subscription_endpoint("http://127.0.0.1:8787/v1/responses")


def test_local_transport_failure_does_not_create_unknown_paid_billing(monkeypatch):
    import openai_budget
    _client_with(monkeypatch, lambda req: httpx.Response(503, content=b"adapter busy"))
    monkeypatch.setenv("CODEX_PROXY_LOCAL", "1")
    monkeypatch.setattr(call_openai.config, "OPENAI_BASE_URL", "http://127.0.0.1:8787/v1")
    monkeypatch.setattr(openai_budget, "reserve", lambda *a, **k: pytest.fail("paid admission"))
    journal = []
    monkeypatch.setattr(call_openai.openai_cache, "record", lambda *a, **kw: journal.append(kw))
    with pytest.raises(RuntimeError, match="503"):
        call.stream("gpt-6-sol", "s", "c", max_tokens=10, effort="low",
                    purpose="local_test", deadline=time.monotonic()+5)
    assert journal[-1]["usd"] == 0.0
    assert journal[-1]["billing_unconfirmed"] is False


@pytest.mark.parametrize("status,body,unconfirmed", [
    (429, b'{"error":{"code":"insufficient_quota","message":"credit_balance_exhausted"}}', False),
    (400, b'{"error":{"message":"bad request"}}', False),
    (503, b"upstream busy", True),
])
def test_a_rejection_before_generation_is_journaled_as_zero_not_unknown_billing(monkeypatch, tmp_path, status, body, unconfirmed):
    """15 Nov 2026: a 429 credit refusal was journaled as unconfirmed billing, and openai_budget then refused every Mac
    workflow and replay call for the rest of the day. A pre-generation rejection bills nothing; only an error whose
    billing is truly unknown stays unconfirmed (and still closes the day)."""
    import openai_budget
    import openai_credit
    _client_with(monkeypatch, lambda req: httpx.Response(status, content=body))
    monkeypatch.setattr(openai_credit, "available", lambda: True)
    monkeypatch.setattr(openai_credit, "note", lambda *a, **k: status == 429)
    journal = []
    monkeypatch.setattr(call_openai.openai_cache, "record", lambda *a, **kw: journal.append(kw))
    with pytest.raises((RuntimeError, call.Refused)):
        call.stream("gpt-6-sol", "s", "c", max_tokens=10, effort="low", purpose="p", deadline=time.monotonic() + 5)
    row = journal[-1]
    assert row["billing_unconfirmed"] is unconfirmed and (row["usd"] is None) is unconfirmed
    log = tmp_path / "spend.jsonl"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    log.write_text(json.dumps(dict(ts=day + "T10:00:00Z", **row)) + "\n")
    monkeypatch.setenv("VALMERA_OPENAI_SPEND_LOG", str(log))
    if unconfirmed:
        with pytest.raises(openai_budget.OverBudget):
            openai_budget.legacy_workflow_total(day)
    else:
        assert openai_budget.legacy_workflow_total(day) == 0
