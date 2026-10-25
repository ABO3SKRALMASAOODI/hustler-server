"""New subscribers on the Claude lane and the Studio for Plus (D1), behind
app_kv.studio_for_all_plans (default OFF). Existing subscribers are
grandfathered by subscription start date and keep EXACTLY today's lanes."""
import json
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_plans  # noqa: E402

CUTOFF = "2026-11-02T00:00:00+00:00"
UTC = timezone.utc


class Conn:
    """users + payments + admin_subscription_snapshots + app_kv by shape."""

    def __init__(self, flag=None, payments=(), snapshots=(), sub_id="sub_1"):
        self.flag = flag
        self.payments = list(payments)        # (user_id, when, amount, status)
        self.snapshots = list(snapshots)      # (subscription_id, started_at)
        self.sub_id = sub_id
        self._row = None
        self.sql = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.sql.append(q)
        self._row = None
        if q.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            return
        if q.startswith("SELECT to_regclass"):
            self._row = {"t": "x"}
        elif q.startswith("SELECT value FROM app_kv"):
            self._row = {"value": self.flag} if self.flag else None
        elif q.startswith("SELECT 1 FROM payments"):
            uid, cutoff = params
            hit = [p for p in self.payments if p[0] == uid and p[2] > 0
                   and p[3] in ("completed", "paid") and p[1] < cutoff]
            self._row = {"x": 1} if hit else None
        elif "FROM admin_subscription_snapshots" in q:
            uid, cutoff = params
            hit = [s for s in self.snapshots
                   if s[0] == self.sub_id and s[1] < cutoff]
            self._row = {"x": 1} if hit else None
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _on(**extra):
    return json.dumps({"on": True, "cutoff_at": CUTOFF, **extra})


def test_off_by_default_and_on_needs_a_cutoff():
    assert claude_plans.parse_flag(None)["on"] is False
    assert claude_plans.parse_flag(json.dumps({"on": True}))["on"] is False
    flag = claude_plans.parse_flag(_on())
    assert flag["on"] is True and flag["claude_lane"] is True
    assert flag["studio_plans"] == frozenset({"plus_2026"})
    # MCP Connect can never be turned into a Studio plan
    flag = claude_plans.parse_flag(_on(studio_plans=["plus_2026", "mcp_connect"]))
    assert "mcp_connect" not in flag["studio_plans"]


def test_existing_subscribers_are_grandfathered_by_start_date():
    before = [(7, _t("2026-10-20T10:00:00"), 1500, "completed")]
    conn = Conn(flag=_on(), payments=before)
    assert claude_plans.applies(conn, 7, True) is False
    assert claude_plans.studio_included(conn, 7, "plus_2026", True) is False
    # a Paddle snapshot that started before the cutoff grandfathers too
    conn = Conn(flag=_on(), snapshots=[("sub_1", _t("2026-10-01T00:00:00"))])
    assert claude_plans.applies(conn, 7, True) is False
    # a refunded / zero / failed payment before the cutoff is not a start
    conn = Conn(flag=_on(), payments=[
        (7, _t("2026-10-20T10:00:00"), 0, "completed"),
        (7, _t("2026-10-21T10:00:00"), 1500, "past_due")])
    assert claude_plans.applies(conn, 7, True) is True


def test_a_subscriber_who_joined_after_the_release_gets_the_offer():
    after = [(8, _t("2026-11-03T09:00:00"), 1500, "completed")]
    conn = Conn(flag=_on(), payments=after)
    assert claude_plans.applies(conn, 8, True) is True
    assert claude_plans.studio_included(conn, 8, "plus_2026", True) is True
    # never for MCP Connect, never for an account without a plan
    assert claude_plans.studio_included(conn, 8, "mcp_connect", True) is False
    assert claude_plans.applies(conn, 8, False) is False


def test_flag_off_changes_nobody_except_internal_test_accounts():
    after = [(8, _t("2026-11-03T09:00:00"), 1500, "completed")]
    off = json.dumps({"on": False, "cutoff_at": CUTOFF, "test_user_ids": [22]})
    conn = Conn(flag=off, payments=after)
    assert claude_plans.applies(conn, 8, True) is False
    assert claude_plans.applies(conn, 22, True) is True


def test_a_lookup_error_reads_as_grandfathered():
    class Broken(Conn):
        def execute(self, sql, params=()):
            if "FROM payments" in sql:
                raise RuntimeError("db hiccup")
            return super().execute(sql, params)

    assert claude_plans.grandfathered(Broken(flag=_on()), 8,
                                      _t("2026-11-02T00:00:00")) is True


def test_models_per_plan_have_price_parity_until_the_bake_off_picks():
    flag = claude_plans.parse_flag(_on())
    assert claude_plans.lane_model(flag, "plus_2026") == "claude-haiku-5-5"
    assert claude_plans.lane_model(flag, "pro_2026") == "claude-sonnet-5-5"
    assert claude_plans.lane_model(flag, "unknown") == "claude-haiku-5-5"
    picked = claude_plans.parse_flag(_on(plan_models={
        "max_2026": "claude-opus-5-5", "pro_2026": "gpt-6-luna"}))
    assert claude_plans.lane_model(picked, "max_2026") == "claude-opus-5-5"
    # never another provider's model on this lane
    assert claude_plans.lane_model(picked, "pro_2026") == "claude-sonnet-5-5"
    assert claude_plans.contracted_model("plus_2026") == "gpt-6-luna"
    assert claude_plans.contracted_model("max_2026") == "gpt-6.1-sol"


def test_safe_keeps_the_callers_transaction_usable():
    def boom(conn):
        raise RuntimeError("x")

    conn = Conn(flag=_on())
    assert claude_plans.safe(conn, boom, default="fallback") == "fallback"
    assert any(q.startswith("ROLLBACK TO SAVEPOINT") for q in conn.sql)


def test_the_studio_gate_refuses_plus_today_and_admits_a_new_plus_subscriber():
    from routes import video

    class Cur:
        def __init__(self, conn):
            self.connection = conn

        def execute(self, sql, *a):
            assert "SELECT plan, is_subscribed FROM users" in sql

        def fetchone(self):
            return {"plan": "plus_2026", "is_subscribed": True}

    from app import create_app
    with create_app().app_context():
        old = Cur(Conn(flag=_on(), payments=[
            (5, _t("2026-10-20T10:00:00"), 1500, "completed")]))
        refused = video._studio_ai_gate_unrecorded(old, 5)
        assert refused is not None and refused[1] == 403
        new = Cur(Conn(flag=_on(), payments=[
            (5, _t("2026-11-03T10:00:00"), 1500, "completed")]))
        assert video._studio_ai_gate_unrecorded(new, 5) is None
        off = Cur(Conn(flag=None, payments=[
            (5, _t("2026-11-03T10:00:00"), 1500, "completed")]))
        assert video._studio_ai_gate_unrecorded(off, 5) is not None


def test_with_the_flag_off_the_offer_cards_are_exactly_todays(monkeypatch):
    import credits
    monkeypatch.setattr(credits, "_read_studio_for_all_plans",
                        lambda conn=None: False)
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    body = credits.subscribe_offer_body()
    assert "studio_ai_plan_ids" not in body and "plan_notes" not in body
    monkeypatch.setattr(credits, "_read_studio_for_all_plans",
                        lambda conn=None: True)
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    body = credits.subscribe_offer_body()
    assert "plus_2026" in body["studio_ai_plan_ids"]
    assert [p["monthly"] for p in body["plans"]] == [
        p["monthly"] for p in credits.subscribe_offer_body()["plans"]]
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
