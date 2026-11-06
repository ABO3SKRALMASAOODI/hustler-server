"""The sealed provider keys, and a concierge no dry wallet can silence
(6 Nov 2026, .valmera/week-1106/reliability/URGENT.md).

From 4 Nov the OpenAI wallet behind Render's key was empty, and the concierge
answered 24 messages from 8 accounts with one canned line; a customer who
typed a request before uploading anything got "Tell me what to make" and no
agent ran. The funded key now arrives through a sealed app_kv row that
outranks the env, and when one provider cannot answer another does.
"""

import json
import os
import pathlib
import sys
import types

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import provider_creds                                        # noqa: E402
import provider_readiness as readiness                       # noqa: E402
import storage_creds as sealed                               # noqa: E402
from routes import video as v                                # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
FUNDED = "sk-funded-key-that-must-never-show"
ANTHROPIC = "sk-ant-second-provider-key"
DEAD = "sk-the-old-dead-key"
LEGACY = "legacy-r2-secret-0123456789abcdef"
EXECUTOR = "executor-bearer-secret-fedcba9876543210"
SECRETS = {"S3_SECRET_ACCESS_KEY": LEGACY, "REMOTE_EXECUTOR_SECRET": EXECUTOR}
ASK = [{"role": "user", "content": "Record my website https://x.test as a "
        "20-second vertical demo"}]


def _row(values=None):
    return sealed.seal(values or {"OPENAI_API_KEY": FUNDED,
                                  "ANTHROPIC_API_KEY": ANTHROPIC},
                       SECRETS, spec=provider_creds.SPEC)


def _keys(raw, env):
    return provider_creds.Keys(fetch=lambda: raw, env=env, background=False,
                               spec=provider_creds.SPEC, what="provider")


def test_the_two_copies_are_identical():
    assert (ROOT / "backend/provider_creds.py").read_bytes() == \
        (ROOT / "worker/provider_creds.py").read_bytes()


def test_the_storage_row_format_is_unchanged():
    """The live row was sealed before Spec existed: the defaults must still
    open it, and nothing but S3_NEXT_* names ever comes out."""
    values = {n: "x" for n in sealed.REQUIRED}
    raw = sealed.seal(values, SECRETS)
    assert json.loads(raw)["info"] == "valmera-storage-next-v1"
    got, kek = sealed.open_row(raw, {"S3_SECRET_ACCESS_KEY": LEGACY})
    assert got == values and kek == "S3_SECRET_ACCESS_KEY"


def test_the_row_opens_under_either_secret():
    for name, secret in SECRETS.items():
        got = _keys(_row(), {name: secret}).values()
        assert got == {"OPENAI_API_KEY": FUNDED,
                       "ANTHROPIC_API_KEY": ANTHROPIC}


def test_the_two_kinds_of_row_never_open_as_each_other():
    with pytest.raises(sealed.CredentialsError):
        sealed.open_row(_row(), {"S3_SECRET_ACCESS_KEY": LEGACY})
    raw = sealed.seal({n: "x" for n in sealed.REQUIRED}, SECRETS)
    with pytest.raises(sealed.CredentialsError):
        sealed.open_row(raw, {"S3_SECRET_ACCESS_KEY": LEGACY},
                        spec=provider_creds.SPEC)


def test_a_name_outside_the_row_never_comes_out():
    plain = sealed.clean_values({"OPENAI_API_KEY": FUNDED, "PADDLE": "x"},
                                provider_creds.SPEC.fields)
    assert plain == {"OPENAI_API_KEY": FUNDED}


@pytest.fixture
def row(monkeypatch):
    """This process, with the dead key in its env and the row delivered."""
    live = _keys(_row(), {"S3_SECRET_ACCESS_KEY": LEGACY})
    monkeypatch.setattr(provider_creds, "CREDENTIALS", live)
    monkeypatch.setenv("OPENAI_API_KEY", DEAD)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(v, "_concierge_client", None)
    monkeypatch.setattr(v, "_concierge_built", None)
    readiness._cache.clear()
    yield live
    readiness._cache.clear()


def test_the_row_outranks_the_dead_env_value(row):
    assert provider_creds.key("OPENAI_API_KEY") == FUNDED
    assert provider_creds.key("ANTHROPIC_API_KEY") == ANTHROPIC


def test_no_row_leaves_the_env_exactly_as_it_was(monkeypatch):
    monkeypatch.setattr(provider_creds, "CREDENTIALS",
                        _keys(None, {"S3_SECRET_ACCESS_KEY": LEGACY}))
    monkeypatch.setenv("OPENAI_API_KEY", DEAD)
    assert provider_creds.key("OPENAI_API_KEY") == DEAD


def test_status_names_the_source_and_never_a_value(row):
    report = provider_creds.status()
    assert report["openai"] == "app_kv" and report["anthropic"] == "app_kv"
    for secret in (FUNDED, ANTHROPIC, DEAD, LEGACY):
        assert secret not in json.dumps(report)


def test_the_concierge_client_follows_the_row(row, monkeypatch):
    assert v._concierge_llm().api_key == FUNDED
    monkeypatch.setattr(provider_creds, "key", lambda n, env=None: "sk-new")
    assert v._concierge_llm().api_key == "sk-new"


def test_checkout_readiness_reads_the_row(row, monkeypatch):
    seen = []

    class Ready:
        ok = True

        def json(self):
            return {"status": "completed", "output": [1]}

    def post(url, headers=None, **_kw):
        seen.append(headers["Authorization"])
        return Ready()
    monkeypatch.setattr(readiness.requests, "post", post)
    assert readiness.advanced_readiness()["ready"] is True
    assert seen == ["Bearer " + FUNDED]


# ------------------------------------------------------------------ #
#  The concierge                                                       #
# ------------------------------------------------------------------ #

def _openai(monkeypatch, reply=None, error=None, calls=None):
    def create(**kw):
        if calls is not None:
            calls.append(kw)
        if error:
            raise Exception(error)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=reply))],
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    monkeypatch.setattr(v, "_concierge_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create))))


def _claude(monkeypatch, reply='{"act": true, "reply": "Recording it now."}',
            status=200, calls=None):
    def post(url, headers=None, json=None, timeout=None):  # noqa: A002
        if calls is not None:
            calls.append((url, headers, json))
        return types.SimpleNamespace(
            ok=status < 400, status_code=status, text="refused",
            json=lambda: {"content": [{"type": "text", "text": reply}],
                          "usage": {"input_tokens": 90, "output_tokens": 20}})
    import requests
    monkeypatch.setattr(requests, "post", post)


DRY = ("Error code: 429 - {'error': {'message': 'You have no credits "
       "remaining.', 'type': 'insufficient_quota'}}")


def test_a_dry_openai_wallet_is_answered_by_claude(row, monkeypatch):
    calls = []
    _openai(monkeypatch, error=DRY)
    _claude(monkeypatch, calls=calls)
    text, meta, rec, act = v._concierge_reply("no_video", ASK, [],
                                              can_act=True)
    assert act is True and text == "Recording it now."
    assert meta["kind"] == "concierge" and rec["model"].startswith("claude-")
    url, headers, body = calls[0]
    assert headers["x-api-key"] == ANTHROPIC
    assert body["messages"][-1]["role"] == "user"


def test_a_dry_wallet_does_not_wait_out_a_retry(row, monkeypatch):
    calls, slept = [], []
    _openai(monkeypatch, error=DRY, calls=calls)
    _claude(monkeypatch)
    monkeypatch.setattr(v.time, "sleep", slept.append)
    v._concierge_reply("no_video", ASK, [], can_act=True)
    assert len(calls) == 1 and slept == []


def test_a_question_on_a_dry_wallet_gets_a_real_answer(row, monkeypatch):
    _openai(monkeypatch, error=DRY)
    _claude(monkeypatch, reply='{"act": false, "reply": "Photos become a '
                               'full edit: drop them in and tell me the '
                               'story."}')
    text, meta, _rec, act = v._concierge_reply(
        "no_video", [{"role": "user", "content": "what about photos?"}], [],
        can_act=True)
    assert act is False and meta["kind"] == "concierge"
    assert "Photos become" in text


def test_the_funded_provider_answers_first(row, monkeypatch):
    calls = []
    _openai(monkeypatch, reply='{"act": false, "reply": "Hello."}')
    _claude(monkeypatch, calls=calls)
    text, _meta, rec, _act = v._concierge_reply(
        "no_video", [{"role": "user", "content": "hi"}], [], can_act=True)
    assert text == "Hello." and calls == []
    assert rec["model"] == v.CONCIERGE_MODEL


def test_a_refused_claim_goes_to_the_next_provider(row, monkeypatch):
    _openai(monkeypatch, reply='{"act": false, "reply": "I\'ve trimmed it."}')
    _claude(monkeypatch, reply='{"act": false, "reply": "Send the clip."}')
    text, meta, _rec, _act = v._concierge_reply(
        "no_video", [{"role": "user", "content": "hi"}], [], can_act=True)
    assert text == "Send the clip." and meta["kind"] == "concierge"


@pytest.mark.parametrize("stage", ["indexing", "index_failed"])
def test_a_video_in_progress_still_gets_a_real_answer(row, monkeypatch, stage):
    _openai(monkeypatch, error=DRY)
    _claude(monkeypatch, reply="It's saved; I'll start when analysis ends.")
    text, meta, _rec, act = v._concierge_reply(stage, ASK, [])
    assert meta["kind"] == "concierge" and act is False and "saved" in text


def test_a_request_with_no_model_at_all_reaches_the_editor(row, monkeypatch):
    """Both wallets down: never "Tell me what to make". The ask is a request;
    the editor runs it or says plainly why not."""
    _openai(monkeypatch, error=DRY)
    _claude(monkeypatch, status=400)
    text, meta, _rec, act = v._concierge_reply("no_video", ASK, [],
                                               can_act=True)
    assert act is True and meta["kind"] == "concierge"
    assert "Tell me what to make" not in text


def test_no_keys_at_all_is_not_a_canned_line_either(monkeypatch):
    monkeypatch.setattr(provider_creds, "key", lambda n, env=None: "")
    text, meta, _rec, act = v._concierge_reply("no_video", ASK, [],
                                               can_act=True)
    assert act is True and "Tell me what to make" not in text


def test_a_tray_upload_still_gets_its_honest_line_when_nothing_answers(
        monkeypatch):
    monkeypatch.setattr(provider_creds, "key", lambda n, env=None: "")
    text, meta, _rec, act = v._concierge_reply(
        "no_video", ASK, [], can_act=False, tray_count=2)
    assert act is False and meta["kind"] == "canned" and "Submit" in text


def test_a_claude_turn_list_opens_on_the_user(row, monkeypatch):
    calls = []
    _openai(monkeypatch, error=DRY)
    _claude(monkeypatch, calls=calls)
    v._concierge_reply("no_video", [
        {"role": "assistant", "content": "Welcome."},
        {"role": "user", "content": "one"},
        {"role": "user", "content": "two"}], [], can_act=True)
    turns = calls[0][2]["messages"]
    assert [t["role"] for t in turns] == ["user"]
    assert turns[0]["content"] == "one\n\ntwo"


def test_a_claude_answer_is_on_the_credit_ledger(monkeypatch):
    import claude_credit
    import claude_plans
    import spend_ledger
    spent = []
    monkeypatch.setattr(claude_plans, "safe", lambda conn, fn, *args:
                        spent.append((fn, args[-1])))
    v._meter_concierge(object(), {"model": "claude-haiku-5-5",
                                  "prompt_tokens": 1000,
                                  "completion_tokens": 100})
    assert [fn for fn, _ in spent] == [spend_ledger.charge,
                                       claude_credit.record_paid_spend]
    assert all(usd > 0 for _, usd in spent)
    spent.clear()
    v._meter_concierge(object(), {"model": "gpt-6-luna"})
    assert [fn for fn, _ in spent] == [spend_ledger.charge]
