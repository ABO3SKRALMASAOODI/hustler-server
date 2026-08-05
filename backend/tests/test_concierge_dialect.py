"""The concierge must never be silenced by our own SDK.

Aug 4-5 2026: every pre-index reply in the product was the canned English
template — 8 of 8 concierge calls, 5 projects, a 100% failure rate — and the
row in llm_calls said why:

    {"error": "Completions.create() got an unexpected keyword argument
               'max_completion_tokens'"}

CONCIERGE_MODEL follows AGENT_MODEL, which had become gpt-5.6-luna. Reasoning
models reject the classic `max_tokens`, so _concierge_reply adapts and retries
with `max_completion_tokens` — but backend/requirements.txt pinned
openai==1.39.0, whose Completions.create() has no such parameter. The retry
raised TypeError, the outer except swallowed it, and the visitor got a fixed
English line back.

The damage was not "a missing nicety". eralfkile123@gmail.com pasted a
1,900-character brief for a travel-agency reel and was answered with "Your
uploads are staged"; mjdjbran0@gmail.com wrote in Arabic and
melanieflores8642@gmail.com in Spanish, and both got English. The concierge is
the first thing a visitor ever talks to.

Two independent fixes, one test each:
  1. openai is pinned >= the worker's 1.59.9 (which is why agent turns were
     healthy the whole time this was broken).
  2. A TypeError on the ADAPTED call now drops the token cap and asks again,
     so a future SDK/model mismatch costs a token limit, never the answer.

    cd backend && python -m pytest tests/test_concierge_dialect.py -q
"""

import os
import re
import sys
import types

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video as v                                # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPLY = "أهلاً! ابعث الفيديو وأنا أحرّره."


def _fake_client(sdk_knows_mct, calls):
    """A reasoning model behind an SDK that does or does not know the
    parameter that model demands."""
    class _Resp:
        choices = [types.SimpleNamespace(
            message=types.SimpleNamespace(
                content='{"act": false, "reply": "%s"}' % _REPLY))]
        usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=5)

    def create(**kw):
        calls.append([k for k in ("max_tokens", "max_completion_tokens")
                      if k in kw])
        if "max_tokens" in kw:
            raise Exception("Unsupported parameter: 'max_tokens' is not "
                            "supported with this model. Use "
                            "'max_completion_tokens' instead.")
        if "max_completion_tokens" in kw and not sdk_knows_mct:
            raise TypeError("Completions.create() got an unexpected keyword "
                            "argument 'max_completion_tokens'")
        return _Resp()

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create)))


@pytest.fixture
def concierge(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def _run(sdk_knows_mct):
        calls = []
        monkeypatch.setattr(v, "_concierge_client",
                            _fake_client(sdk_knows_mct, calls))
        text, meta, rec, act = v._concierge_reply(
            "no_video", [{"role": "user", "content": "مرحبا"}], [],
            can_act=True)
        return text, meta, calls
    return _run


def test_modern_sdk_answers_on_the_adapted_retry(concierge):
    """The normal path: max_tokens refused, max_completion_tokens accepted."""
    text, meta, calls = concierge(True)
    assert calls == [["max_tokens"], ["max_completion_tokens"]]
    assert meta["kind"] != "canned"
    assert text == _REPLY


def test_an_sdk_too_old_for_the_retry_still_answers(concierge):
    """THE REGRESSION. With openai 1.39.0 the adapted retry is impossible;
    before the fix that TypeError became a canned English reply."""
    text, meta, calls = concierge(False)
    assert calls == [["max_tokens"], ["max_completion_tokens"], []]
    assert meta["kind"] != "canned", \
        "an SDK that cannot express the token cap must cost the CAP, not the reply"
    assert text == _REPLY


def test_a_real_provider_failure_still_falls_back(monkeypatch):
    """The fallback is not being removed — only the case where WE were the
    thing that failed. A provider that genuinely refuses still gets the
    canned line rather than an exception reaching the request."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def create(**kw):
        raise Exception("403 permission-denied: out of credits")

    monkeypatch.setattr(v, "_concierge_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create))))
    text, meta, rec, act = v._concierge_reply("no_video", [], [])
    assert meta["kind"] == "canned"
    assert act is False
    assert "403" not in text, "provider internals never reach a user"


def test_openai_pin_is_at_least_the_workers():
    """The root cause was a version SKEW between two services calling the same
    provider the same way. Pin them together or it comes back."""
    def _pin(path):
        src = open(os.path.join(REPO, path), encoding="utf-8").read()
        m = re.search(r"^openai==(\d+)\.(\d+)\.(\d+)", src, re.M)
        assert m, f"no openai pin in {path}"
        return tuple(int(g) for g in m.groups())

    backend, worker = _pin("backend/requirements.txt"), _pin("worker/requirements.txt")
    assert backend >= worker, \
        f"backend openai {backend} is older than the worker's {worker}"
    assert backend >= (1, 59, 9), \
        "openai must accept max_completion_tokens (added well after 1.39.0)"


def test_typing_extensions_satisfies_the_sdk():
    """openai 1.59.9 floors typing-extensions at 4.11; the old 4.9.0 pin made
    the bump unresolvable and would have failed the Render build."""
    src = open(os.path.join(REPO, "backend/requirements.txt"),
               encoding="utf-8").read()
    m = re.search(r"^typing_extensions==(\d+)\.(\d+)", src, re.M)
    assert m and tuple(int(g) for g in m.groups()) >= (4, 11)
