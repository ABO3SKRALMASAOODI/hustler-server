"""P0 #60 (24 Nov 2026): a rotated Anthropic key reaches every process.

The key was rotated; the sealed provider_api_keys row held the new key at
17:21:00 UTC, but every box process kept the revoked key from its
environment until its container was recreated, so a subscriber's four
requests (17:14-17:23) ended on "I can't reach the editing model".

Now config.ANTHROPIC_API_KEY follows the sealed row (as the OpenAI key
already did), the lane's client is rebuilt when the key changes, and an auth
refusal re-reads the row and runs the same request once on a newly rotated
key instead of closing the lane."""

from types import SimpleNamespace

import pytest

import claude_lane
import claude_trial
import config
import provider_creds


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


class _Refused(Exception):
    def __init__(self):
        super().__init__("Error code: 401 - authentication_error: "
                         "invalid x-api-key")
        self.status_code = 401


def _ok():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")],
        stop_reason="end_turn", model="claude-haiku-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=2,
                              cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))


class _FakeAnthropic:
    """anthropic.Anthropic stand-in: a revoked key is refused."""
    built = []

    def __init__(self, api_key=None, **kw):
        self.api_key = api_key
        self.messages = self
        _FakeAnthropic.built.append(api_key)

    def with_options(self, **kw):
        return self

    def stream(self, **body):
        return _Stream(_Refused() if self.api_key == "old-key" else _ok())


class _Creds:
    """provider_creds.CREDENTIALS stand-in: the row, re-read on invalidate."""

    def __init__(self, row, after_refresh=None):
        import threading
        self.row = dict(row)
        self.after_refresh = after_refresh
        self.refreshed = 0
        self._lock = threading.Lock()

    def values(self):
        return self.row

    def invalidate(self):
        return True

    def _refresh(self):
        self.refreshed += 1
        if self.after_refresh is not None:
            self.row = dict(self.after_refresh)


@pytest.fixture
def lane(monkeypatch):
    claude_lane.reopen()
    _FakeAnthropic.built = []
    monkeypatch.setattr(claude_lane, "anthropic",
                        SimpleNamespace(Anthropic=_FakeAnthropic))
    monkeypatch.setattr(claude_lane, "_client", None)
    monkeypatch.setattr(claude_lane, "_client_key", None)
    monkeypatch.setattr(config, "_ANTHROPIC_ENV_KEY", "old-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Other tests may have left a plain module attribute shadowing the live
    # read; this file tests the live read.
    if "ANTHROPIC_API_KEY" in vars(config):
        monkeypatch.delattr(config, "ANTHROPIC_API_KEY")
    refused = []
    monkeypatch.setattr(claude_trial, "provider_refused",
                        lambda kind, exc: refused.append(kind))
    monkeypatch.setattr(claude_trial, "meter", lambda *a, **k: None)
    monkeypatch.setattr(claude_trial, "meter_paid", lambda *a, **k: None)
    monkeypatch.setattr(claude_lane.time, "sleep", lambda s: None)
    yield SimpleNamespace(refused=refused, monkeypatch=monkeypatch)
    claude_lane.reopen()


def _use_row(monkeypatch, creds):
    monkeypatch.setattr(provider_creds, "CREDENTIALS", creds)


def test_the_sealed_row_outranks_the_environment(lane):
    _use_row(lane.monkeypatch, _Creds({"ANTHROPIC_API_KEY": "row-key"}))
    assert config.ANTHROPIC_API_KEY == "row-key"
    _use_row(lane.monkeypatch, _Creds({}))
    assert config.ANTHROPIC_API_KEY == "old-key"      # no row: the env


def test_a_rotated_key_builds_a_new_client_without_a_restart(lane):
    creds = _Creds({"ANTHROPIC_API_KEY": "old-key"})
    _use_row(lane.monkeypatch, creds)
    first = claude_lane._sdk()
    assert claude_lane._sdk() is first                # same key: reused
    creds.row = {"ANTHROPIC_API_KEY": "new-key"}
    second = claude_lane._sdk()
    assert second is not first and second.api_key == "new-key"


def test_auth_refusal_on_a_just_rotated_key_runs_the_request_on_the_new_key(lane):
    creds = _Creds({"ANTHROPIC_API_KEY": "old-key"},
                   after_refresh={"ANTHROPIC_API_KEY": "new-key"})
    _use_row(lane.monkeypatch, creds)
    out = claude_lane.create("claude-haiku-5-5",
                             [{"role": "user", "content": "cut it faster"}])
    assert out is not None
    assert creds.refreshed == 1
    assert lane.refused == []        # the retry on the new key answered: no false alarm
    assert claude_lane.available()
    assert _FakeAnthropic.built == ["old-key", "new-key"]


def test_auth_refusal_with_no_new_key_is_still_reported(lane):
    creds = _Creds({"ANTHROPIC_API_KEY": "old-key"})  # the row has no news
    _use_row(lane.monkeypatch, creds)
    with pytest.raises(claude_lane.ClaudeLaneUnavailable):
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "x"}])
    assert lane.refused == ["auth"]
    assert creds.refreshed == 1
