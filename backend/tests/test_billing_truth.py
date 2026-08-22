"""A refused payment is not a conversion, and revenue is not a plan count.

Round 59. Every test here is a defect that was live on prod on Jul 29 2026:

  * user 205 read `converted`, subscribed, holding 2,000 credits, while Paddle
    held his subscription at `past_due` after refusing $30.00 for
    `not_enough_balance`;
  * the revenue panel read $0 for every one of them, because its price map
    covered only plus/pro/ultra — three retired plans nobody is on;
  * two live trials held 3,924.80 and 2,000.00 credits against allowances of
    400 and 200.

    cd backend && python -m pytest tests -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing                                           # noqa: E402
import billing_sync                                      # noqa: E402
import credits                                           # noqa: E402


# ── The money maths ──────────────────────────────────────────────────────────

def test_every_live_plan_has_a_price():
    """The bug in one assertion. The admin's map was {plus, pro, ultra}; the
    plans anyone can actually buy are ai / ai_pro / ai_max, so MRR could only
    ever sum to zero no matter how much money came in."""
    for plan in ('ai', 'ai_pro', 'ai_max'):
        assert billing.monthly_value(plan) > 0, (
            f"{plan} is purchasable and priced at 0 — this is exactly the "
            f"shape of the bug that made revenue read $0")


def test_prices_match_the_pricing_page():
    assert billing.monthly_value('ai') == 15
    assert billing.monthly_value('ai_pro') == 30
    assert billing.monthly_value('ai_max') == 50


def test_yearly_is_amortised_not_counted_whole():
    """A yearly Creator is $150 a year, which is $12.50 of MRR — not $150
    (twelve times the truth) and not $15 (the monthly sticker)."""
    assert billing.monthly_value('ai', 'yearly') == 12.5
    assert billing.monthly_value('ai_pro', 'yearly') == 25.0
    assert billing.monthly_value('ai_max', 'yearly') == pytest.approx(41.67, abs=0.01)


def test_an_unknown_plan_is_worth_nothing_not_a_crash():
    assert billing.monthly_value('nonsense') == 0
    assert billing.monthly_value(None) == 0


def test_price_map_covers_every_plan_that_can_be_granted():
    """PLAN_CREDITS is what the webhook will grant. Anything grantable but
    unpriced is a customer who pays and books as $0 — the same class of silent
    hole the retired map was."""
    from routes.paddle_webhook import PLAN_CREDITS
    missing = [p for p in PLAN_CREDITS if p not in billing.PLAN_PRICES_USD]
    assert not missing, f"grantable but unpriced: {missing}"


# ── Reading Paddle's amounts ────────────────────────────────────────────────

def test_grand_total_is_minor_units():
    """Paddle sends "3000" for $30.00. Reading it as dollars would report every
    charge at 100x, and reading it as an int of dollars at 1/100th."""
    cents, cur = billing.transaction_amount(
        {"details": {"totals": {"grand_total": "3000",
                                "currency_code": "USD"}}})
    assert cents == 3000
    assert cur == "USD"


def test_a_trial_opens_with_a_completed_zero_dollar_transaction():
    """THE reason `status == 'completed'` was never a revenue test. Every trial
    on this platform starts with a real, signed, completed transaction for
    $0.00 — four of the five 'completed' transactions on prod were these."""
    cents, _ = billing.transaction_amount(
        {"status": "completed",
         "details": {"totals": {"grand_total": "0", "currency_code": "USD"}}})
    assert cents == 0


def test_missing_totals_do_not_raise():
    assert billing.transaction_amount({}) == (0, "USD")
    assert billing.transaction_amount(
        {"details": {"totals": {"grand_total": None}}})[0] == 0


def test_the_decline_reason_is_the_last_attempt():
    """Paddle appends an attempt per retry. The first one is stale by
    definition; quoting it would describe a decline that has been superseded."""
    data = {"payments": [
        {"status": "error", "error_code": "expired_card"},
        {"status": "error", "error_code": "not_enough_balance"},
    ]}
    assert billing.payment_error_code(data) == "not_enough_balance"


def test_a_successful_payment_has_no_decline_reason():
    assert billing.payment_error_code(
        {"payments": [{"status": "captured", "error_code": None}]}) is None


def test_the_real_decline_is_said_in_words():
    """`not_enough_balance` is the decline this whole round was built around."""
    assert "balance" in billing.decline_message("not_enough_balance").lower()
    # And an unknown code never guesses a cause. Telling somebody their card
    # was declined when their bank merely blocked it sends them to cancel a
    # card that works.
    generic = billing.decline_message("some_new_paddle_code")
    assert "didn't go through" in generic


# ── The statuses ────────────────────────────────────────────────────────────

def test_trialing_is_live_but_never_paying():
    """The distinction the revenue panel did not make. A trialling account is
    `is_subscribed` from the moment of checkout and has been charged nothing."""
    assert 'trialing' in billing.LIVE_STATUSES
    assert 'trialing' not in billing.PAYING_STATUSES


def test_past_due_is_a_failure_not_a_grant():
    """The guard in the webhook keys off this. Paddle keeps sending
    subscription.updated while a subscription is in dunning, and those events
    carry the paid price id — so if past_due were not a failing status they
    would walk into the grant branch and re-fund the pool after every lift."""
    assert 'past_due' in billing.FAILING_STATUSES
    assert 'paused' in billing.FAILING_STATUSES
    assert 'past_due' not in billing.PAYING_STATUSES


# ── The trial cap ───────────────────────────────────────────────────────────

def test_trial_allowance_is_a_tenth_of_the_plan():
    assert credits.trial_allowance('ai') == 100
    assert credits.trial_allowance('ai_pro') == 200
    assert credits.trial_allowance('ai_max') == 500


class _Cur:
    """Records what was executed and returns canned rows in order."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return []

    def close(self):
        pass

    @property
    def description(self):
        return []


class _Conn:
    def __init__(self, rows):
        self._cur = _Cur(rows)
        self.committed = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass


def test_a_trial_holding_ten_times_its_allowance_is_clamped():
    """The live leak. Trial allowance on ai_pro is 400; this account held
    3,924.80 — roughly $19.60 of model spend on someone who had paid nothing
    and had already scheduled a cancellation.

    The clamp takes the OVERAGE off the balance rather than recomputing it, so
    credits the user legitimately spent during the trial are not handed back.
    """
    conn = _Conn([{"credits_monthly": 3924.80, "credits_balance": 3924.80}])
    before, after = billing_sync._clamp_trial_credits(conn, 212, 'ai_pro')
    assert before == 3924.80
    assert after == 200.0
    sql, params = conn._cur.executed[-1]
    assert "credits_monthly" in sql and "GREATEST(0, credits_balance - %s)" in sql
    assert params[0] == 200          # the shopfront trial allowance
    assert params[2] == pytest.approx(3724.80)   # the overage removed


def test_a_legacy_trial_keeps_the_allowance_it_was_sold():
    """An in-flight $50 Pro trial was granted 400. The new shopfront
    allowance is 200 — clamping to that would cut a live trial in half."""
    conn = _Conn([{
        "credits_monthly": 400.0,
        "credits_balance": 380.0,
        "credits_monthly_limit": 400,
    }])
    assert billing_sync._clamp_trial_credits(conn, 1, 'ai_pro') is None
    assert conn.committed == 0


def test_a_trial_inside_its_allowance_is_left_alone():
    conn = _Conn([{"credits_monthly": 80.0, "credits_balance": 90.0}])
    assert billing_sync._clamp_trial_credits(conn, 1, 'ai') is None
    assert conn.committed == 0


def test_a_plan_with_no_allowance_is_never_clamped_to_zero():
    """mcp grants 0 credits on purpose — the customer brings their own model.
    Clamping it would be arithmetic on a pool that does not exist."""
    conn = _Conn([{"credits_monthly": 0, "credits_balance": 20.0}])
    assert billing_sync._clamp_trial_credits(conn, 60, 'mcp') is None


# ── Never downgrade on silence ──────────────────────────────────────────────

def test_paddle_being_unreachable_is_not_a_cancellation(monkeypatch):
    """The single most dangerous thing a reconciler can do is read a timeout as
    a churn event. It must report and change nothing."""
    monkeypatch.setattr(billing_sync, "fetch_subscription",
                        lambda sub: (None, "unreachable: timed out"))
    conn = _Conn([])
    rep = billing_sync.reconcile_user(
        conn, {"id": 1, "email": "a@b.c", "subscription_id": "sub_x",
               "plan": "ai", "is_subscribed": 1})
    assert rep["error"].startswith("unreachable")
    assert rep["changes"] == []
    assert conn.committed == 0


def test_a_missing_api_key_changes_nothing(monkeypatch):
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    data, err = billing_sync.fetch_subscription("sub_x")
    assert data is None and err == "no_api_key"


def test_no_subscription_id_is_not_an_error():
    conn = _Conn([])
    rep = billing_sync.reconcile_user(
        conn, {"id": 1, "email": "a@b.c", "subscription_id": None})
    assert "error" not in rep and rep["changes"] == []


# ── Billing period ──────────────────────────────────────────────────────────

def test_yearly_is_read_off_paddles_own_billing_cycle():
    assert billing_sync._billing_period(
        {"billing_cycle": {"interval": "year", "frequency": 1}}) == "yearly"
    assert billing_sync._billing_period(
        {"billing_cycle": {"interval": "month", "frequency": 1}}) == "monthly"
    # Twelve monthly intervals is a year by any other name.
    assert billing_sync._billing_period(
        {"billing_cycle": {"interval": "month", "frequency": 12}}) == "yearly"
    # Absent cycle defaults to monthly — the common case, and the one that
    # under-states rather than over-states revenue.
    assert billing_sync._billing_period({}) == "monthly"


# ── Grace is graded by payment history ──────────────────────────────────────

def test_grace_exists_only_for_customers_who_have_actually_paid():
    """The judgement call this round turns on. Blanket grace is what let a
    refused trial keep 2,000 credits forever; no grace at all would cut off a
    two-year customer the first time their bank blocked a renewal."""
    assert billing.PAID_GRACE_DAYS > 0
    # And it is bounded — Paddle retries for 30 days, we do not fund 30 days of
    # model spend while it does.
    assert billing.PAID_GRACE_DAYS <= 7


def test_the_dunning_email_is_capped():
    assert 0 < billing.MAX_DUNNING_EMAILS <= 10


def test_the_locks_do_not_collide():
    """billing_sync and the newsletter both run on every gunicorn worker. A
    shared advisory lock would make each silently skip the other's run."""
    from routes.newsletter import TICK_LOCK_ID as NEWSLETTER_LOCK
    assert billing_sync.TICK_LOCK_ID != NEWSLETTER_LOCK


def test_billing_scheduler_keeps_session_lock_on_direct_database(
        monkeypatch):
    connected = []

    class Cursor:
        def execute(self, _sql, _params=None): pass
        def fetchone(self): return {"got": False}
        def close(self): pass

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    def connect(url, **_kwargs):
        connected.append(url)
        return Connection()

    import psycopg2
    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://pool")
    monkeypatch.setenv("DIRECT_DATABASE_URL", "postgresql://direct")

    assert billing_sync.run_billing_tick() == {
        "skipped": "another worker holds the lock"}
    assert connected == ["postgresql://direct"]


def test_newsletter_scheduler_keeps_session_lock_on_direct_database(
        monkeypatch):
    from flask import Flask
    from routes import newsletter

    connected = []
    sentinel = object()

    def connect(url, **_kwargs):
        connected.append(url)
        return sentinel

    monkeypatch.setattr(newsletter.psycopg2, "connect", connect)
    monkeypatch.setenv("DIRECT_DATABASE_URL", "postgresql://direct")
    app = Flask(__name__)
    app.config["DATABASE_URL"] = "postgresql://pool"
    with app.app_context():
        assert newsletter.get_db() is sentinel
    assert connected == ["postgresql://direct"]
