"""15 Nov 2026, the independent check of the OpenAI-out fallbacks: four production defects.

1. The first film's picture check named gpt-6-sol but sent it through claude_lane.create, so
   Anthropic answered 404 and every cutaway was removed whenever OpenAI was funded. Each request
   below is asserted by provider, client and model id: OpenAI's model on api.openai.com, Haiku on
   the Claude lane, and no OpenAI id ever reaches Anthropic (nor a Claude id api.openai.com).
2. An upload failed outright when no model could review the source screen; now the index
   completes and the first film is cut from the whole source, recorded.
3. A credit refusal on a one-shot helper was six HTTP requests (the SDK's own retries); now one.
4. The openai_credit alert was marked sent before its queue insert was confirmed.

No paid call: every client and transport is mocked."""
import json
import time
from types import SimpleNamespace as NS

import httpx
import pytest

import claude_lane
import config
import first_cut_models
import llm
import openai_credit

QUOTA_BODY = json.dumps({"error": {
    "message": "You have no credits remaining. Add credits to continue using the API.",
    "type": "insufficient_quota", "code": "insufficient_quota"}}).encode()
QUOTA = "Error code: 429 - " + QUOTA_BODY.decode()


@pytest.fixture
def gate(monkeypatch):
    openai_credit.reopen()
    monkeypatch.setattr(config, "OPENAI_CREDIT_LATCH_S", 900.0, raising=False)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    yield openai_credit
    openai_credit.reopen()


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _openai_http(monkeypatch, handler):
    """api.openai.com/v1/responses for score.call_openai, served by handler (no network)."""
    from score import call_openai
    real = httpx.Client
    monkeypatch.setattr(call_openai.config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(call_openai.config, "OPENAI_BASE_URL", "https://api.openai.com/v1",
                        raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(call_openai.llm, "fence", lambda model: None)
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _answer(text):
    return httpx.Response(200, content=_sse(
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.completed", "response": {
            "id": "resp_1", "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 10}}}))


@pytest.fixture
def broll(monkeypatch, tmp_path):
    """A first film with one cutaway; the Claude lane's SDK records every request it would send."""
    import media_cache
    import first_cut_visual as fcv
    from tests.test_first_cut_visual import _EdlCtx, _talk_index
    ctx = _EdlCtx(_talk_index())
    ctx.workdir = str(tmp_path)
    ctx.edl["fx"] = [{"id": "br1", "type": "broll", "start": 4., "end": 4.5, "media": "video",
                      "source": {"asset_key": "a/clip.mp4", "offset_s": 9.}}]
    monkeypatch.setattr(media_cache, "lease", lambda *a: "clip.mp4")
    monkeypatch.setattr(fcv.subprocess, "run", lambda argv, **kw: NS(stdout=b"jpg"))
    anthropic = []

    def create(model, messages, **kw):
        anthropic.append(model)
        assert claude_lane.is_claude_model(model), f"{model} sent to Anthropic"
        return NS(choices=[NS(message=NS(content='{"keep":[1]}'))],
                  usage=NS(provider_cost_usd=0.0001))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    removed = []

    def run(ctx_, steps, name, args, started):
        removed.append(args["ids"])
        ctx_.edl["fx"] = [it for it in ctx_.edl["fx"] if it["id"] not in args["ids"]]
    return NS(ctx=ctx, fcv=fcv, anthropic=anthropic, removed=removed, run=run)


# ------------------------------------------------------------------ 1. the picture check's provider


def test_with_openai_funded_the_picture_check_goes_to_openai_and_keeps_the_cutaway(
        gate, broll, monkeypatch):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append((req.url.host, req.url.path, body["model"],
                     req.headers["authorization"]))
        return _answer('{"keep":[1],"drop":[]}')
    _openai_http(monkeypatch, handler)
    spend = {"usd": 0.0}
    kept = broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, spend)
    assert kept == [(4., 4.5)] and broll.removed == []
    assert sent == [("api.openai.com", "/v1/responses", "gpt-6-sol", "Bearer sk-test")]
    assert broll.anthropic == []                 # gpt-6-sol never reaches Anthropic
    assert spend["usd"] > 0


def test_the_picture_check_images_reach_openai_as_input_images(gate, broll, monkeypatch):
    seen = []

    def handler(req):
        user = [m for m in json.loads(req.content)["input"] if m["role"] == "user"][0]
        seen.extend(p["type"] for p in user["content"])
        return _answer('{"keep":[1]}')
    _openai_http(monkeypatch, handler)
    broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, {"usd": 0.0})
    assert seen.count("input_image") == 3 and "input_text" in seen


def test_with_openai_latched_the_check_runs_on_haiku_and_sends_openai_nothing(
        gate, broll, monkeypatch):
    sent = []
    _openai_http(monkeypatch, lambda req: sent.append(1) or _answer("{}"))
    gate.trip("test", QUOTA)
    assert broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, {"usd": 0.0}) == [(4., 4.5)]
    assert sent == [] and broll.anthropic == ["claude-haiku-5-5"]


@pytest.mark.parametrize("status,body", [(429, QUOTA_BODY), (500, b'{"error":"boom"}'),
                                         (404, b'{"error":{"type":"not_found_error"}}')])
def test_an_openai_failure_is_answered_by_haiku_in_the_same_check(gate, broll, monkeypatch,
                                                                  status, body):
    sent = []
    _openai_http(monkeypatch, lambda req: sent.append(1) or httpx.Response(status, content=body))
    assert broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, {"usd": 0.0}) == [(4., 4.5)]
    assert sent == [1] and broll.anthropic == ["claude-haiku-5-5"]
    assert gate.available() is (status != 429)   # only the credit refusal latches


def test_without_any_checker_the_unchecked_picture_is_still_removed(gate, broll, monkeypatch):
    monkeypatch.setattr(claude_lane, "available", lambda: False)
    gate.trip("test", QUOTA)
    assert broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, {"usd": 0.0}) == []
    assert broll.removed == [["br1"]] and broll.anthropic == []


def test_the_claude_lane_refuses_an_openai_id_before_any_request(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "_sdk", lambda: calls.append(1))
    for model in ("gpt-6-sol", "gpt-6-luna", "grok-4.5"):
        with pytest.raises(claude_lane.ClaudeLaneWrongModel):
            claude_lane.create(model, [{"role": "user", "content": "x"}], max_tokens=5)
    assert calls == []
    assert not isinstance(claude_lane.ClaudeLaneWrongModel("x"), claude_lane.ClaudeLaneUnavailable)


def test_a_rung_model_still_answers_on_openai_through_the_claude_lane(monkeypatch):
    import edit_rung
    seen = []
    monkeypatch.setattr(edit_rung, "create", lambda model, *a, **k: seen.append(model) or "ok")
    assert claude_lane.create("gpt-6.1-sol", [{"role": "user", "content": "x"}]) == "ok"
    assert seen == ["gpt-6.1-sol"]


def test_first_cut_scouts_never_offer_anthropic_an_openai_id(monkeypatch):
    import claude_trial
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    assert first_cut_models.available("claude-haiku-5-5")
    assert not first_cut_models.available("gpt-6-sol")
    monkeypatch.setattr(claude_trial, "flag", lambda db: {"model": "gpt-6-sol"})
    monkeypatch.setattr(claude_trial, "credit_status", lambda db: {"on": True})
    assert first_cut_models.paid_scout(None) is None


def test_a_claude_id_is_never_sent_to_api_openai_com(gate):
    from tests.test_openai_credit import _OpenAIClient
    client = _OpenAIClient()
    with pytest.raises(ValueError, match="Claude model"):
        llm.create_with_dialect(client, "claude-haiku-5-5", [{"role": "user", "content": "x"}],
                                max_tokens=5)
    assert client.calls == []


# ------------------------------------------------------------------ 2. upload without a source screen


def test_source_screen_without_a_model_is_unavailable_and_recorded(monkeypatch, tmp_path):
    import source_screen
    from PIL import Image
    from score import boards
    rows = []
    monkeypatch.setattr(boards, "grab", lambda *a, **k: Image.new("RGB", (40, 60)))
    monkeypatch.setattr(source_screen.llm, "ask_vision", lambda *a, **k: None)
    monkeypatch.setattr(source_screen.llm, "record", lambda *a, **k: rows.append(a))
    with pytest.raises(source_screen.Unavailable):
        source_screen.build("proxy.mp4", 10., 30., [{"start": 0, "end": 10.}], "sha",
                            str(tmp_path), video_info={"duration": 10., "fps": 30.})
    assert rows and rows[0][0] == "first_film_source_screen"
    assert rows[0][2]["fallback"] == source_screen.UNREVIEWED


def test_a_lane_stop_inside_the_look_is_also_unavailable(monkeypatch, tmp_path):
    import source_screen
    from PIL import Image
    from score import boards

    def stopped(*a, **k):
        raise claude_lane.ClaudeLaneUnavailable("Claude lane is closed (credit balance)")
    monkeypatch.setattr(boards, "grab", lambda *a, **k: Image.new("RGB", (40, 60)))
    monkeypatch.setattr(source_screen.llm, "ask_vision", stopped)
    monkeypatch.setattr(source_screen.llm, "record", lambda *a, **k: None)
    with pytest.raises(source_screen.Unavailable, match="ClaudeLaneUnavailable"):
        source_screen.build("proxy.mp4", 10., 30., [], "sha", str(tmp_path),
                            video_info={"duration": 10., "fps": 30.})


def test_the_first_film_runs_on_the_whole_source_when_no_model_reviewed_it(monkeypatch, tmp_path):
    import first_cut
    import source_screen

    def unavailable(*a, **k):
        raise source_screen.Unavailable("no model answered")
    monkeypatch.setattr(source_screen, "build", unavailable)
    index = {"video": {"duration": 20., "fps": 30.}, "shots": []}
    ctx = NS(index=index, duration=20., proxy_path=lambda: "proxy.mp4")
    assert first_cut.bound_to_source_screen(ctx, {"video_sha256": "s"}, str(tmp_path)) is False
    assert ctx.duration == 20. and "_first_film_source_guard" not in ctx.index


def test_a_reviewed_source_still_bounds_the_first_film(monkeypatch, tmp_path):
    import first_cut
    import source_screen
    evidence = {"version": 1, "status": "verified", "safe_end_s": 9.9, "source_duration_s": 20.}
    monkeypatch.setattr(source_screen, "build", lambda *a, **k: evidence)
    monkeypatch.setattr(source_screen, "clear_start_state", lambda ctx: None)
    index = {"video": {"duration": 20., "fps": 30.}, "shots": [], "words": [], "sentences": []}
    ctx = NS(index=index, duration=20., proxy_path=lambda: "proxy.mp4")
    assert first_cut.bound_to_source_screen(ctx, {"video_sha256": "s"}, str(tmp_path)) is True
    assert ctx.duration == 9.9 and ctx.index["_first_film_source_guard"]


def test_an_answered_but_unusable_review_still_holds_the_first_film(monkeypatch, tmp_path):
    """Only a missing model is the plain fallback; a verdict the gate cannot clear is unchanged."""
    import first_cut
    import source_screen

    def bad(*a, **k):
        raise ValueError("source screen evidence is incomplete")
    monkeypatch.setattr(source_screen, "build", bad)
    ctx = NS(index={"video": {"duration": 20., "fps": 30.}}, duration=20.,
             proxy_path=lambda: "proxy.mp4")
    with pytest.raises(ValueError, match="incomplete"):
        first_cut.bound_to_source_screen(ctx, {}, str(tmp_path))


def test_the_index_completes_when_no_model_can_review_the_source_screen(monkeypatch, tmp_path):
    """run_index_job end to end on a real 4 s clip (live ffmpeg), as a first film's index, with
    no model answering the source screen: the index is saved, carries the recorded fallback, and
    the job does not fail."""
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs ffmpeg")
    import indexer
    import source_screen
    import storage
    import transcribe
    from schemas import Word
    clip = tmp_path / "orig.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=4", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(clip)], check=True)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(storage, "download_to", lambda key, local: shutil.copy(clip, local))
    monkeypatch.setattr(storage, "upload_file", lambda *a, **k: None)
    monkeypatch.setattr(storage, "exists", lambda key: key == "orig/key.mp4")
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, warnings, progress_cb=None:
                        ([Word(w="hello", t0=0.2, t1=0.6, speaker=0)], "en"))
    monkeypatch.setattr(indexer, "_finish_setup", lambda *a, **k: None)
    monkeypatch.setattr(indexer, "storyboard_after_ready", lambda *a, **k: True)
    looks = []
    monkeypatch.setattr(source_screen.llm, "ask_vision",
                        lambda *a, **k: looks.append(k.get("purpose")))   # None: nobody answered
    saved = {}

    class Db:
        def run(self, fn, *a, **k):
            name = getattr(fn, "__name__", "")
            if name == "get_asset":
                return {"id": 7, "kind": "video", "storage_key": "orig/key.mp4",
                        "duration_s": None, "width": None, "height": None}
            if name == "get_project":
                return {"id": 1, "chat_session_id": 5}
            if name == "insert_asset":
                return 99
            if name == "upsert_index":
                saved["index"] = a[2]
            return None
    result = indexer.run_index_job(Db(), {"id": 42, "project_id": 1, "user_id": 3,
                                          "payload": {"asset_id": 7}})
    assert result["cached"] is False
    assert looks == ["first_film_source_screen"]
    assert saved["index"].get("source_screen") is None
    assert any(source_screen.UNREVIEWED in w for w in saved["index"]["warnings"])


# ------------------------------------------------------------------ 3. one request per credit refusal


def _sdk_client(monkeypatch, handler):
    """The real OpenAI SDK client of llm.client() (LLM_MAX_RETRIES=5), on a mock transport."""
    from openai import OpenAI
    client = OpenAI(base_url="https://api.openai.com/v1", api_key="sk-test",
                    max_retries=config.LLM_MAX_RETRIES,
                    http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "fence", lambda model: None)
    return client


def _chat(text="ok"):
    return httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-6-luna",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})


def test_a_credit_refusal_is_one_request_then_the_latch(gate, monkeypatch):
    assert config.LLM_MAX_RETRIES >= 1
    sent = []
    client = _sdk_client(monkeypatch, lambda req: sent.append(req.url.path) or httpx.Response(
        429, content=QUOTA_BODY, headers={"content-type": "application/json"}))
    t0 = time.monotonic()
    with pytest.raises(gate.CreditOut):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}],
                                max_tokens=10)
    assert sent == ["/v1/chat/completions"]
    assert not gate.available() and time.monotonic() - t0 < 2


def test_the_reprobe_after_the_latch_is_one_request(gate, monkeypatch):
    sent = []
    client = _sdk_client(monkeypatch, lambda req: sent.append(1) or httpx.Response(
        429, content=QUOTA_BODY, headers={"content-type": "application/json"}))
    gate.trip("test", QUOTA)
    now = time.monotonic()
    monkeypatch.setattr(openai_credit.time, "monotonic", lambda: now + 901)
    assert gate.available()
    with pytest.raises(gate.CreditOut):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}],
                                max_tokens=10)
    assert sent == [1] and not gate.available()


@pytest.mark.parametrize("first", [
    httpx.Response(429, json={"error": {"message": "Rate limit reached for gpt-6-luna",
                                        "type": "requests", "code": "rate_limit_exceeded"}}),
    httpx.Response(503, json={"error": {"message": "overloaded"}}),
])
def test_a_rate_limit_or_outage_is_still_retried(gate, monkeypatch, first):
    replies = [first, _chat("fine")]
    sent = []
    client = _sdk_client(monkeypatch, lambda req: sent.append(1) or replies.pop(0))
    resp = llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}],
                                   max_tokens=10)
    assert resp.choices[0].message.content == "fine" and sent == [1, 1]
    assert gate.available()


def test_transient_retries_stop_at_the_configured_count(gate, monkeypatch):
    sent = []
    client = _sdk_client(monkeypatch, lambda req: sent.append(1) or httpx.Response(
        500, json={"error": {"message": "server error"}}))
    with pytest.raises(Exception):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}],
                                max_tokens=10)
    assert len(sent) == 1 + config.LLM_MAX_RETRIES


def test_a_vision_reprobe_is_one_openai_request_then_haiku(gate, monkeypatch, tmp_path):
    sent = []
    client = _sdk_client(monkeypatch, lambda req: sent.append(1) or httpx.Response(
        429, content=QUOTA_BODY, headers={"content-type": "application/json"}))
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for", lambda plan: (client, "gpt-6-luna"))
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    haiku = []

    def create(model, messages, **kw):
        haiku.append(model)
        from tests.test_openai_credit import _completion
        return _completion('{"reviewed_all_frames": true}', model)
    monkeypatch.setattr(claude_lane, "create", create)
    picture = tmp_path / "sheet.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xd9")
    answer = llm.ask_vision("review", [str(picture)], purpose="first_film_source_screen")
    assert answer == '{"reviewed_all_frames": true}'
    assert sent == [1] and haiku == ["claude-haiku-5-5"]


# ------------------------------------------------------------------ 4. the alert is marked only when queued


def test_the_alert_is_marked_sent_only_after_a_confirmed_insert(gate, monkeypatch):
    monkeypatch.setattr(openai_credit, "_alert", openai_credit._real_alert)
    outcomes = [RuntimeError("db down"), False, True]
    tries = []

    def queue(key, subject, html):
        tries.append(key)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(openai_credit, "_queue_alert", queue)
    gate.note(QUOTA, "vision", status=429)       # insert failed: not marked
    assert gate.alert_key() not in gate._alerted
    gate.note(QUOTA, "vision", status=429)       # not confirmed: not marked
    assert gate.alert_key() not in gate._alerted
    gate.note(QUOTA, "concierge", status=429)    # confirmed
    assert gate.alert_key() in gate._alerted
    gate.note(QUOTA, "director", status=429)     # sent once: no further insert
    assert tries == [gate.alert_key()] * 3


def test_the_queue_confirms_an_insert_or_a_row_another_process_queued(monkeypatch):
    import claude_trial
    rows = {"t": True, "exists": True}

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            self.sql = sql

        def fetchone(self):
            if "to_regclass" in self.sql:
                return {"t": "trial_alerts" if rows["t"] else None}
            return (1,) if rows["exists"] else None

    class Conn:
        def cursor(self):
            return Cur()

    class Db:
        def run(self, fn, *a):
            return fn(Conn(), *a)
    monkeypatch.setattr(claude_trial, "_metering_db", lambda: Db())
    inserted = [True]
    monkeypatch.setattr(claude_trial, "queue_alert", lambda conn, *a: inserted[0])
    assert openai_credit._queue_alert("k", "s", "h") is True
    inserted[0] = False                          # ON CONFLICT: another process queued it
    assert openai_credit._queue_alert("k", "s", "h") is True
    rows["exists"] = False                       # neither inserted nor present
    assert openai_credit._queue_alert("k", "s", "h") is False
    rows["t"] = False                            # no table
    assert openai_credit._queue_alert("k", "s", "h") is False


# ------------------------------------------------------------------ 15 Nov follow-ups from the independent check


@pytest.mark.parametrize("answer", ["", "I could not decide about these pictures.", '{"keep": [1'])
def test_an_openai_answer_without_a_verdict_is_checked_on_haiku_not_dropped(gate, broll, monkeypatch, answer):
    """An OpenAI answer with no verdict (empty, prose, cut off) is no answer: Haiku checks, the cutaway survives."""
    _openai_http(monkeypatch, lambda req: _answer(answer))
    assert broll.fcv.verify_broll(broll.ctx, [], 0, broll.run, {"usd": 0.0}) == [(4., 4.5)]
    assert broll.anthropic == ["claude-haiku-5-5"] and broll.removed == []


def test_unreadable_source_screen_frames_are_unavailable_not_a_failed_first_film(monkeypatch, tmp_path):
    import source_screen
    from score import boards
    monkeypatch.setattr(boards, "grab", lambda *a, **k: None)
    monkeypatch.setattr(source_screen.llm, "ask_vision", lambda *a, **k: pytest.fail("no frames, no review"))
    with pytest.raises(source_screen.Unavailable, match="could not be read"):
        source_screen.build("proxy.mp4", 10., 30., [], "sha", str(tmp_path), video_info={"duration": 10., "fps": 30.})
