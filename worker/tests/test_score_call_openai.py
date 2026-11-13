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
