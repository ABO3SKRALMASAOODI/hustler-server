"""GATING PROOF (T1, 30 Oct 2026): with EVERY trial flag on, an existing
subscriber's Studio turn runs on exactly today's lanes, models and effort.

Owner rule (DECISIONS.md, D1): "EXISTING subscribers keep exactly their
current lanes and behaviour (grandfathered by start date)". Engine changes on
the trial branch reach only trial users and NEW subscribers.

Every flag is on at once: app_kv free_result (on, Haiku, test accounts),
studio_for_all_plans (on, cutoff yesterday, Claude lane, credit parity), the
credit switch on, the Anthropic lane configured. The flag reads go through the
real modules (claude_trial, claude_plans, claude_credit) over a small SQL
fake, so nothing here is stubbed except the database rows themselves.

    cd worker && PYTHONPATH=.. python -m pytest tests/test_trial_gating_proof.py -q
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import agent_loop
import claude_lane
import claude_trial
import config
import llm

CUTOFF = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
EVERY_FLAG_ON = {
    "free_result": json.dumps({
        "on": True, "model": "claude-haiku-5-5", "per_account_usd_cap": 0.12,
        "pool_usd_cap": 170, "monthly_usd_cap": 170, "daily_count_cap": 40,
        "daily_usd_cap": 15, "trial_requests": True, "payer_first_cut": True,
        "test_user_ids": [29, 46, 61, 1058]}),
    "studio_for_all_plans": json.dumps({
        "on": True, "cutoff_at": CUTOFF, "claude_lane": True,
        "credit_parity": True, "test_user_ids": [22],
        "plan_models": {"plus_2026": "claude-haiku-5-5",
                        "pro_2026": "claude-haiku-5-5",
                        "max_2026": "claude-haiku-5-5"}}),
    "claude_credit": json.dumps({"cycle_budget_usd": 170,
                                 "safety_margin_usd": 10}),
    "claude_credit_state": json.dumps({"state": "on"}),
    "upload_proxy": json.dumps({"on": True}),
}
EXISTING, NEW = 501, 777
# Every plan a current subscriber can be on (shopfront and grandfathered).
PLANS = ("plus_2026", "pro_2026", "max_2026", "advanced", "ai", "ai_pro",
         "ai_max", "mcp_connect", "mcp", "plus", "pro", "ultra", "titan",
         "ace")


class Conn:
    def __init__(self, kv):
        self.kv = kv
        self._row = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        if q.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            self._row = None
        elif q.startswith("SELECT to_regclass"):
            self._row = {"t": "t"}
        elif q.startswith("SELECT value FROM app_kv"):
            value = self.kv.get(params[0])
            self._row = {"value": value} if value is not None else None
        elif "information_schema.columns" in q:
            self._row = {"x": 1}
        elif "FROM trial_budget_days" in q:
            self._row = {"usd": 12.0}
        elif q.startswith("SELECT 1 FROM payments"):
            # The existing subscriber paid before the cutoff; the new one
            # has no payment before it.
            self._row = {"x": 1} if int(params[0]) == EXISTING else None
        elif q.startswith("SELECT 1 FROM admin_subscription_snapshots"):
            self._row = None
        elif "FROM projects WHERE id" in q:
            self._row = {"kind": None}
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


class Db:
    def __init__(self, kv):
        self.conn = Conn(kv)

    def run(self, fn, *args):
        return fn(self.conn, *args)


@pytest.fixture
def every_flag_on(monkeypatch):
    claude_trial.reset_flag_cache()
    claude_lane.reopen()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(claude_lane, "anthropic", object())
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-openai-key")
    yield Db(dict(EVERY_FLAG_ON))
    claude_trial.reset_flag_cache()
    claude_lane.reopen()


def _lanes(subscribed, plan):
    return [(lane["name"], lane["model"])
            for lane in llm.agent_lanes_for(subscribed, plan)]


def _todays(plan):
    """Today's contracted lanes, computed with every trial flag OFF."""
    claude_trial.reset_flag_cache()
    return _lanes(True, plan)


def test_every_flag_really_is_on(every_flag_on):
    db = every_flag_on
    assert claude_trial.flag(db)["on"] is True
    assert claude_trial.credit_status(db)["on"] is True
    assert claude_lane.available()


@pytest.mark.parametrize("plan", PLANS)
def test_an_existing_subscriber_keeps_todays_lane_model_and_effort(
        every_flag_on, plan):
    db = every_flag_on
    todays = _todays(plan)
    job = {"id": 1, "user_id": EXISTING, "project_id": 9}
    # 1. Not a trial turn (the trial lane is for never-paid accounts only).
    decision = claude_trial.resolve(db, EXISTING, True, project_id=9)
    assert decision.kind == claude_trial.Decision.NOT_TRIAL
    # 2. Not the new-subscriber Claude lane (grandfathered by start date).
    assert agent_loop._claude_paid_lane(db, job, True, plan, {}) is None
    # 3. So the turn's lanes are exactly today's, model for model.
    assert _lanes(True, plan) == todays
    assert all(not model.startswith("claude-") for _, model in todays)
    # 4. Same reasoning effort as today.
    assert llm.editor_reasoning_effort(plan) == \
        llm.editor_reasoning_effort(plan, iteration=0)


def test_the_proof_is_sensitive_a_new_subscriber_does_move(every_flag_on):
    """The same setup moves a subscriber who joined after the cutoff, so the
    test above would fail if grandfathering broke."""
    db = every_flag_on
    job = {"id": 2, "user_id": NEW, "project_id": 10}
    got = agent_loop._claude_paid_lane(db, job, True, "pro_2026", {})
    assert got and got["lane"]["model"] == "claude-haiku-5-5"
    assert got["credit_model"] == "gpt-6.1-sol"


def test_existing_subscriber_first_cut_is_unchanged(every_flag_on):
    """No automatic First cut: the indexer starts one only for a project the
    backend admitted (projects.meta.free_result), and backend/free_trial
    admits a subscriber only when claude_plans says NEW (backend test
    test_free_trial.py and test_trial_gating_proof.py)."""
    import first_cut

    class Conn2(Conn):
        def execute(self, sql, params=None):
            q = " ".join(sql.split())
            if q.startswith("SELECT meta FROM projects"):
                self._row = {"meta": {}}
                return
            return Conn.execute(self, sql, params)
    assert first_cut.project_admission(Conn2({}), 9) is None
