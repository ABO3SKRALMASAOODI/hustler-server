"""The backend's OpenAI credit gate (15 Nov 2026): latch on a credit refusal, one alert, re-probe."""
import pytest

import openai_credit


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    openai_credit.reopen()
    yield
    openai_credit.reopen()


def test_credit_refusal_latches_and_alerts_once(monkeypatch):
    alerts = []
    monkeypatch.setattr(openai_credit, "_alert", lambda where, detail: alerts.append(where))
    body = '{"error":{"type":"insufficient_quota","message":"You have no credits remaining."}}'
    assert openai_credit.note(body, "concierge", status=429)
    assert not openai_credit.available()
    assert openai_credit.note(body, "checkout_readiness", status=429)
    assert len(alerts) == 2        # _alert itself dedupes; the real one is checked below


def test_alert_is_queued_once_per_month(monkeypatch):
    sent = []

    class Cur:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, sql, args=None):
            if args:
                sent.append(args[0])
        def fetchone(self):
            return {"t": "trial_alerts"}

    class Conn:
        def cursor(self):
            return Cur()
        def commit(self):
            pass
        def close(self):
            pass
    import claude_trial
    monkeypatch.setattr(claude_trial, "_connect", lambda: Conn())
    monkeypatch.setattr(openai_credit, "_alert", openai_credit._real_alert)
    for _ in range(5):
        openai_credit.trip("concierge", "insufficient_quota")
    assert sent == [openai_credit.alert_key()]
    assert sent[0].startswith("openai_credit_out:")


def test_alert_is_marked_sent_only_after_a_committed_insert(monkeypatch):
    """15 Nov 2026: the key was marked before the insert, so one failed insert silenced the month."""
    sent, commits = [], []
    state = {"fail": True, "table": True}

    class Cur:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, sql, args=None):
            if args:
                if state["fail"]:
                    raise RuntimeError("connection reset")
                sent.append(args[0])
        def fetchone(self):
            return {"t": "trial_alerts" if state["table"] else None}

    class Conn:
        def cursor(self):
            return Cur()
        def commit(self):
            commits.append(1)
        def close(self):
            pass
    import claude_trial
    monkeypatch.setattr(claude_trial, "_connect", lambda: Conn())
    monkeypatch.setattr(openai_credit, "_alert", openai_credit._real_alert)
    openai_credit.trip("concierge", "insufficient_quota")          # insert fails
    assert openai_credit.alert_key() not in openai_credit._alerted and sent == []
    state.update(fail=False, table=False)
    openai_credit.trip("concierge", "insufficient_quota")          # no table: not sent
    assert openai_credit.alert_key() not in openai_credit._alerted
    state["table"] = True
    openai_credit.trip("concierge", "insufficient_quota")          # committed
    openai_credit.trip("concierge", "insufficient_quota")          # already sent
    assert sent == [openai_credit.alert_key()] and commits == [1]
    assert openai_credit.alert_key() in openai_credit._alerted


def test_rate_limit_is_not_credit():
    assert not openai_credit.note("Rate limit reached for requests per min", "concierge", status=429)
    assert openai_credit.available()


def test_latch_expires_for_the_reprobe(monkeypatch):
    monkeypatch.setattr(openai_credit, "_alert", lambda *a: None)
    monkeypatch.setenv("OPENAI_CREDIT_LATCH_S", "900")
    openai_credit.trip("concierge", "insufficient_quota")
    assert not openai_credit.available()
    now = openai_credit.time.monotonic()
    monkeypatch.setattr(openai_credit.time, "monotonic", lambda: now + 901)
    assert openai_credit.available()


def test_concierge_skips_openai_while_latched(monkeypatch):
    from routes import video
    monkeypatch.setattr(video.provider_creds, "key", lambda name: "k")
    monkeypatch.setattr(openai_credit, "_alert", lambda *a: None)
    assert video._concierge_openai in video._concierge_providers()
    openai_credit.trip("concierge", "insufficient_quota")
    assert video._concierge_providers() == [video._concierge_claude]
