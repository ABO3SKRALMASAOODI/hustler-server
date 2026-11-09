"""P0 #60 (9 Nov 2026): the Anthropic wallet ran dry, every subscriber's
Studio request ended blocked, and after a top-up only the 3-hourly probe
could reopen paid editing. The request's own gate now probes (at most once
every 5 minutes across the fleet) and runs on Claude when it succeeds."""

import json

import pytest

import agent_loop
import claude_credit
import claude_lane
import claude_plans
import claude_trial
import config

ON = json.dumps({"on": True, "cutoff_at": "2026-11-02T00:00:00Z"})
JOB = {"id": 1, "user_id": 8}


class _Db:
    def __init__(self, claim):
        self.claim = claim
        self.after = []

    def run(self, fn, *args):
        if fn is claude_plans.read_flag:
            return claude_plans.parse_flag(ON)
        if fn is claude_plans.applies:
            return True
        if fn is claude_credit.claim_on_demand_probe:
            return self.claim
        if fn is claude_credit.after_probe:
            self.after.append(args)
            return "recorded"
        raise AssertionError(fn)


@pytest.fixture
def billing_off(monkeypatch):
    claude_trial.reset_flag_cache()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-anthropic-key-XYZ")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    credit = {"on": False, "reason": "billing_error", "last_probe_ok": False}
    monkeypatch.setattr(claude_trial, "credit_status",
                        lambda worker_db, max_age_s=20.0: dict(credit))
    yield credit
    claude_lane.reopen()
    claude_trial.reset_flag_cache()


def test_a_successful_probe_runs_the_request_on_claude(billing_off, monkeypatch):
    calls = []

    def probe():
        calls.append(1)
        billing_off["last_probe_ok"] = True      # after_probe recorded it
        return True, "stop_reason=end_turn"

    monkeypatch.setattr(claude_lane, "probe", probe)
    claude_lane._close("billing: credit balance is too low")
    db = _Db({"action": "probe", "may_switch_on": False})
    got = agent_loop._claude_paid_lane(db, JOB, True, "plus_2026", {})
    assert got and got["lane"]["name"] == "claude_paid"
    assert calls == [1] and db.after[0][1:] == (True, "")
    assert claude_lane.available()


def test_a_refused_probe_or_no_claim_stays_blocked(billing_off, monkeypatch):
    monkeypatch.setattr(claude_lane, "probe",
                        lambda: (False, "BadRequestError 400: credit balance"))
    db = _Db({"action": "probe", "may_switch_on": False})
    assert agent_loop._claude_paid_lane(db, JOB, True, "plus_2026", {}) is None
    assert db.after[0][1] is False
    monkeypatch.setattr(claude_lane, "probe",
                        lambda: pytest.fail("probed without a claim"))
    assert agent_loop._claude_paid_lane(_Db(None), JOB, True, "plus_2026",
                                        {}) is None


def test_the_probe_never_leaks_the_key(monkeypatch):
    class Boom(Exception):
        status_code = 400

    class Client:
        def with_options(self, **kw):
            return self

        @property
        def messages(self):
            raise Boom("credit balance is too low")

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    monkeypatch.setattr(claude_lane, "_sdk", lambda: Client())
    ok, err = claude_lane.probe()
    assert ok is False and "sk-ant" not in err and "400" in err
