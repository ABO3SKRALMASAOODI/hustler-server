"""OpenAI out of credit (15 Nov 2026): every OpenAI role has a working fallback.

The credit ran out (HTTP 429 insufficient_quota) and is not refilled before 30 Nov. One latch
(worker/openai_credit.py) shuts OpenAI for 15 minutes after a credit refusal, re-probes after it,
alerts the admin once; each role then answers without OpenAI: the index's picture captions and the
first film's source screen on Haiku, other helpers skipped, the Director's checks on their Haiku
fallback, checks named on OpenAI on the turn's own model, footage heard by the box's own
faster-whisper, the second transcript recognizer skipped."""
import json
import os
import subprocess
import time
from types import SimpleNamespace as NS

import httpx
import pytest

import config
import llm
import openai_credit

QUOTA = ("Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to "
         "continue using the API.', 'type': 'insufficient_quota', 'code': 'insufficient_quota'}}")


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTAGE_TRANSCRIPT_CACHE", str(tmp_path/"cache"))
    openai_credit.reopen()
    monkeypatch.setattr(config, "OPENAI_CREDIT_LATCH_S", 900.0, raising=False)
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    yield openai_credit
    openai_credit.reopen()


def _completion(text, model):
    return NS(choices=[NS(message=NS(content=text, tool_calls=[]), finish_reason="stop")],
              usage=NS(prompt_tokens=10, completion_tokens=5, completion_tokens_details=None,
                       prompt_tokens_details=None, answered_model=model))


class _OpenAIClient:
    """Duck-types the OpenAI SDK client on api.openai.com."""

    base_url = "https://api.openai.com/v1"
    api_key = "sk-test"

    def __init__(self, error=None, reply="ok"):
        self.calls = []
        outer = self

        class _C:
            def create(self, model=None, messages=None, **kw):
                outer.calls.append(model)
                if outer.error:
                    raise RuntimeError(outer.error)
                return _completion(outer.reply, model)
        self.error, self.reply = error, reply
        self.chat = NS(completions=_C())

    def with_options(self, **kw):
        return self


# ------------------------------------------------------------------ the gate


def test_a_credit_refusal_latches_and_the_latch_reprobes(gate, monkeypatch):
    assert gate.available()
    assert not gate.note("Rate limit reached for gpt-6-luna in organization", "x", status=429)
    assert gate.available()                    # a rate limit is not the wallet
    assert gate.note(QUOTA, "vision", status=429)
    assert not gate.available()
    with pytest.raises(gate.CreditOut):
        gate.check("x")
    now = time.monotonic()
    monkeypatch.setattr(openai_credit.time, "monotonic", lambda: now + 901)
    assert gate.available()                    # 15 min on, one real call re-probes


def test_the_admin_is_alerted_once_never_per_call(gate, monkeypatch):
    import claude_trial
    sent = []
    monkeypatch.setattr(openai_credit, "_alert", openai_credit._real_alert)
    monkeypatch.setattr(openai_credit, "_queue_alert",
                        lambda key, subject, html: sent.append((key, "openai_credit")) or True)
    for where in ("vision", "concierge", "footage_hear", "director", "vision"):
        gate.note(QUOTA, where, status=429)
    assert sent == [(gate.alert_key(), "openai_credit")]
    assert sent[0][0].startswith("openai_credit_out:")


# ------------------------------------------------------------------ llm: the one-shot helpers


def test_a_latched_openai_call_is_never_sent(gate):
    client = _OpenAIClient()
    gate.trip("test", QUOTA)
    with pytest.raises(gate.CreditOut):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert client.calls == []


def test_a_credit_refusal_latches_every_openai_role_at_once(gate):
    client = _OpenAIClient(error=QUOTA)
    with pytest.raises(gate.CreditOut):
        llm.create_with_dialect(client, "gpt-6-luna", [{"role": "user", "content": "hi"}], max_tokens=10)
    assert client.calls == ["gpt-6-luna"]        # not retried into the same refusal
    assert not gate.available()


@pytest.fixture
def eyes(gate, monkeypatch, tmp_path):
    """ask_vision on the shared OpenAI vision client, with a Claude lane that answers."""
    import claude_lane
    openai = _OpenAIClient(reply="[]")
    monkeypatch.setattr(llm, "vision_available", lambda plan=None: True)
    monkeypatch.setattr(llm, "vision_client_for", lambda plan: (openai, "gpt-6-luna"))
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    haiku = []

    def create(model, messages, **kw):
        haiku.append(model)
        return _completion('{"reviewed_all_frames": true}', model)
    monkeypatch.setattr(claude_lane, "create", create)
    picture = tmp_path / "sheet.jpg"
    picture.write_bytes(b"\xff\xd8\xff\xd9")
    return NS(openai=openai, haiku=haiku, picture=str(picture))


def test_the_first_films_source_screen_looks_with_haiku_while_openai_is_out(eyes, gate):
    gate.trip("test", QUOTA)
    answer = llm.ask_vision("review", [eyes.picture], purpose="first_film_source_screen")
    assert answer == '{"reviewed_all_frames": true}'
    assert eyes.openai.calls == [] and eyes.haiku == ["claude-haiku-5-5"]


def test_the_refusal_itself_is_answered_by_haiku_in_the_same_call(eyes, gate):
    eyes.openai.error = QUOTA
    answer = llm.ask_vision("captions", [eyes.picture], purpose="visual_index_semantics")
    assert answer == '{"reviewed_all_frames": true}'
    assert eyes.openai.calls == ["gpt-6-luna"] and eyes.haiku == ["claude-haiku-5-5"]
    assert not gate.available()


def test_an_optional_look_is_skipped_with_no_call(eyes, gate):
    gate.trip("test", QUOTA)
    assert llm.ask_vision("look", [eyes.picture], purpose="vision_reframe") is None
    assert eyes.openai.calls == [] and eyes.haiku == []


def test_the_greeting_falls_to_its_plain_reply_with_no_openai_call(gate, monkeypatch):
    openai = _OpenAIClient()
    monkeypatch.setattr(llm, "claude_trial_lane", lambda: None)
    monkeypatch.setattr(llm, "client", lambda: openai)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    gate.trip("test", QUOTA)
    assert llm.ask_text("s", "u", purpose="index_greet") is None
    assert openai.calls == []


def test_haiku_purposes_are_the_films_needs_only():
    assert set(config.OPENAI_OUT_HAIKU_PURPOSES) == {
        "first_film_source_screen", "visual_index_semantics", "visual_index_semantics_fallback",
        "vision_index", "search_descriptions",
        # #1278: the looks a customer's search or edit reads
        "vision_visual_search", "commons_ai_items", "broll_rendition_review", "vision_look",
        "vision_screen"}


def test_stock_search_descriptions_look_with_haiku_while_openai_is_out(eyes, gate):
    """#1108: OpenAI out of credit left every MCP search_stock result undescribed, so nothing
    was placeable and the customer's search failed four times in a row."""
    eyes.openai.error = QUOTA
    answer = llm.ask_vision("describe", [eyes.picture], purpose="search_descriptions")
    assert answer == '{"reviewed_all_frames": true}'
    assert eyes.openai.calls == ["gpt-6-luna"] and eyes.haiku == ["claude-haiku-5-5"]


# ------------------------------------------------------------------ the Director's OpenAI rung


def _director_http(monkeypatch, handler):
    from score import call_openai
    real = httpx.Client
    monkeypatch.setattr(call_openai.config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(call_openai.config, "OPENAI_BASE_URL", "https://api.openai.com/v1", raising=False)
    monkeypatch.setattr(call_openai.llm, "fence", lambda model: None)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_the_director_rung_refuses_at_once_and_latches_on_a_credit_429(gate, monkeypatch):
    from score import call
    sent = []
    body = json.dumps({"error": {"type": "insufficient_quota", "code": "insufficient_quota",
                                 "message": "You have no credits remaining."}}).encode()
    _director_http(monkeypatch, lambda req: sent.append(1) or httpx.Response(429, content=body))
    with pytest.raises(call.Refused):
        call.stream("gpt-6-sol", "s", "c", max_tokens=5, effort="low", purpose="named_check",
                    deadline=time.monotonic() + 10)
    assert len(sent) == 1 and not gate.available()
    with pytest.raises(call.Refused):            # latched: nothing is sent
        call.stream("gpt-6-sol", "s", "c", max_tokens=5, effort="low", purpose="named_check",
                    deadline=time.monotonic() + 10)
    assert len(sent) == 1


def test_the_footage_engine_does_not_retry_a_refusal(gate, monkeypatch):
    from footage import ask
    from score import call
    tries = []

    def refused(*a, **k):
        tries.append(1)
        raise call.Refused("OpenAI credit out")
    monkeypatch.setattr(call, "stream", refused)
    monkeypatch.setattr(ask.time, "sleep", lambda s: pytest.fail("slept before a retry"))
    with pytest.raises(call.Refused):
        ask.ask("s", "c", "footage_look", budget_s=30)
    assert tries == [1]


def test_the_named_picture_check_answers_on_haiku_when_openai_is_out(gate, monkeypatch):
    import broll_source
    from score import call
    models = []

    def stream(model, *a, **k):
        models.append(model)
        if model.startswith("gpt-"):
            raise call.Refused("OpenAI credit out")
        return call.Result('{"fit": []}', None, "end_turn", 0.1, 0.2, 0.001)
    monkeypatch.setattr(call, "stream", stream)
    text, _ = broll_source._ask("s", "c", "named_check", 20.0, 100, model="gpt-6-sol")
    assert text == '{"fit": []}' and models == ["gpt-6-sol", broll_source.NAMED_MODEL]
    assert broll_source.NAMED_MODEL.startswith("claude-haiku")


# ------------------------------------------------------------------ checks named on OpenAI inside a turn


def test_a_check_named_on_openai_runs_on_the_turns_own_model(gate, monkeypatch):
    from score import asks
    own = NS(is_claude_lane=True)
    seen = []

    def create(client, model, messages, **kw):
        seen.append((client, model))
        if model.startswith("gpt-"):
            raise gate.CreditOut("out")
        call = NS(function=NS(name="authorize_edit_delta", arguments='{"allowed": true}'))
        return NS(choices=[NS(message=NS(tool_calls=[call]), finish_reason="tool_calls")],
                  usage=NS(prompt_tokens=1, completion_tokens=1))
    monkeypatch.setattr(llm, "create_with_dialect", create)
    monkeypatch.setattr(llm, "provider_cost_usd", lambda usage, model: 0.0)
    monkeypatch.setattr(llm, "client", lambda: _OpenAIClient())
    ctx = NS(llm_client=own, agent_model="claude-haiku-5-5")
    value, u = asks._call(ctx, "authorize_edit_delta", {}, "s", "c", model="gpt-6-sol")
    assert value == {"allowed": True} and u["model"] == "claude-haiku-5-5"
    assert seen[-1] == (own, "claude-haiku-5-5")
    gate.trip("test", QUOTA)                    # latched: OpenAI is not even asked
    seen.clear()
    asks._call(ctx, "authorize_edit_delta", {}, "s", "c", model="gpt-6-sol")
    assert seen == [(own, "claude-haiku-5-5")]


# ------------------------------------------------------------------ footage hearing on the box


def _tone(path, seconds=30):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=300:duration={seconds}", "-ac", "1", "-ar", "16000", "-y", path],
                   check=True)


class _Whisper:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, wav, **kw):
        self.calls.append((wav, kw))
        return iter(self.segments), NS(language="en")


def test_footage_is_heard_locally_when_openai_is_out(gate, monkeypatch, tmp_path):
    import transcribe as asr
    from footage import hear
    src = str(tmp_path / "src.wav")
    _tone(src)
    whisper = _Whisper([NS(start=1.0, end=3.0, text=" the race starts"),
                        NS(start=14.0, end=16.0, text=" he overtakes"),
                        NS(start=26.0, end=28.0, text=" checkered flag")])
    monkeypatch.setattr(asr, "get_model", lambda: whisper)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("OpenAI was called"))
    gate.trip("test", QUOTA)
    rows, usd = hear.transcribe(src, 30.0)
    assert usd == 0.0 and len(whisper.calls) == 1        # one local pass, $0
    assert [r["text"] for r in rows if r["text"]] == ["the race starts", "he overtakes", "checkered flag"]
    assert all(r["t0"] <= {"the race starts": 2, "he overtakes": 15, "checkered flag": 27}[r["text"]] < r["t1"]
               for r in rows if r["text"])
    assert whisper.calls[0][1]["beam_size"] == 1 and whisper.calls[0][1]["vad_filter"] is True


def test_a_credit_429_mid_source_moves_the_rest_to_local_hearing(gate, monkeypatch, tmp_path):
    import transcribe as asr
    from footage import hear
    monkeypatch.setenv("FOOTAGE_HEAR_PROVIDER", "openai")
    src = str(tmp_path / "src.wav")
    _tone(src)
    whisper = _Whisper([NS(start=2.0, end=4.0, text="local words")])
    monkeypatch.setattr(asr, "get_model", lambda: whisper)
    posts = []

    def post(*a, **k):
        posts.append(1)
        return NS(status_code=429, text='{"error": {"type": "insufficient_quota"}}')
    monkeypatch.setattr(httpx, "post", post)
    rows, usd = hear.transcribe(src, 30.0, workers=1)
    assert len(posts) == 1                                # the refusal is not retried
    assert not gate.available()
    assert any(r["text"] == "local words" for r in rows)


def test_local_hearing_is_bounded_per_source(gate, monkeypatch, tmp_path):
    from footage import hear
    src = str(tmp_path / "src.wav")
    _tone(src, 30)
    monkeypatch.setattr(hear, "LOCAL_MAX_S", 12.0)
    whisper = _Whisper([NS(start=1.0, end=2.0, text="early")])
    got = hear.local_hear(src, [0.0, 12.0, 24.0, 30.0], [0, 1, 2], str(tmp_path), model=whisper)
    assert got == {0: "early"}                            # pieces past the cap stay unheard


# ------------------------------------------------------------------ the second transcript recognizer


def test_the_second_recognizer_is_skipped_while_openai_is_out(gate, monkeypatch):
    import transcript_verify
    monkeypatch.setattr(config, "TRANSCRIPT_VERIFY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1", raising=False)
    monkeypatch.setattr(llm, "client", lambda: pytest.fail("OpenAI was asked"))
    words = [{"w": "hello", "t0": 0.0, "t1": 0.4, "confidence": 0.5}]
    gate.trip("test", QUOTA)
    warnings = []
    assert transcript_verify.verify(words, "/nonexistent.wav", "en", warnings) is words
    assert warnings == []


# ------------------------------------------------------------------ the edit rung and the shell's music


def test_the_edit_rung_goes_straight_to_haiku_while_latched(gate, monkeypatch):
    import edit_rung
    edit_rung.reopen()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("OpenAI was called"))
    gate.trip("test", QUOTA)
    got = edit_rung.create("gpt-6.1-sol", [{"role": "user", "content": "x"}],
                           fallback=lambda model: model)
    assert got == "claude-haiku-5-5"
    edit_rung.clear_turn()


def test_the_shell_music_pick_keeps_the_signal_choice_while_latched(gate, monkeypatch):
    import music_film
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert music_film.openai_call() is not None
    gate.trip("test", QUOTA)
    assert music_film.openai_call() is None


def test_the_first_films_picture_check_runs_on_haiku_while_openai_is_out(gate, monkeypatch, tmp_path):
    import claude_lane
    import media_cache
    import first_cut_visual as fcv
    from tests.test_first_cut_visual import _EdlCtx, _talk_index
    ctx = _EdlCtx(_talk_index())
    ctx.workdir = str(tmp_path)
    item = {'id': 'br1', 'type': 'broll', 'start': 4., 'end': 4.5,
            'media': 'video', 'source': {'asset_key': 'a/clip.mp4', 'offset_s': 9.}}
    ctx.edl['fx'] = [dict(item)]
    monkeypatch.setattr(fcv.first_cut_models, 'available', lambda model: True)
    monkeypatch.setattr(media_cache, 'lease', lambda *a: 'clip.mp4')
    monkeypatch.setattr(fcv.subprocess, 'run', lambda argv, **kw: NS(stdout=b'jpg'))
    models = []

    def create(model, messages, **kw):
        models.append(model)
        return NS(choices=[NS(message=NS(content='{"keep":[1]}'))], usage=NS(provider_cost_usd=0))
    monkeypatch.setattr(claude_lane, 'create', create)
    gate.trip("test", QUOTA)
    assert fcv.verify_broll(ctx, [], 0, lambda *a: None, {'usd': 0}) == [(4., 4.5)]
    assert models == ['claude-haiku-5-5']


def test_the_directors_last_look_still_looks_on_haiku_while_openai_is_out(gate, monkeypatch, tmp_path):
    """Its pictures are checked on Haiku, not dropped unchecked or let in unchecked."""
    from score import call, supply
    monkeypatch.setattr(supply, 'pair_board', lambda *a, **k: 2)
    monkeypatch.setattr(supply.brief, 'image_block', lambda *a: {'type': 'text', 'text': 'board'})
    answer = {'pairs': [{'n': 1, 'post': 'film', 'exact_match': True, 'why': 'exact object'},
                        {'n': 2, 'post': 'film', 'exact_match': False, 'why': 'a lookalike'}]}
    models = []

    def stream(model, *a, **k):
        models.append(model)
        if model.startswith('gpt-'):
            gate.trip('director', QUOTA)
            raise call.Refused('OpenAI credit out')
        return NS(text=json.dumps(answer), usd=0)
    monkeypatch.setattr(supply.call, 'stream', stream)
    items = [dict(id=i, wish=i, path='test.jpg', named='shark', what='meat') for i in range(2)]
    wishes = [dict(at=0, want='shark meat'), dict(at=1, want='shark')]
    kept, _, _ = supply.check_fit(str(tmp_path), items, wishes, time.monotonic() + 5)
    assert [x['id'] for x in kept] == [0]
    assert models == [supply.CHECK_MODEL, 'claude-haiku-5-5']
    models.clear()
    monkeypatch.setattr(supply.call, 'stream', lambda m, *a, **k: models.append(m) or (_ for _ in ()).throw(call.Refused('no')))
    assert supply.check_fit(str(tmp_path), items, wishes, time.monotonic() + 5)[0] == []   # Haiku down: nothing unchecked enters
