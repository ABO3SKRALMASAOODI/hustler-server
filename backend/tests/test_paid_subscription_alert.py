"""The founder gets one honest email when a new subscription pays.

These tests stay at the outbox boundary: eligibility, durable dedupe SQL, and
the failure/retry state machine. Brevo itself is mocked; a provider outage must
never turn a Paddle webhook into a failed activation.
"""

import hashlib
import hmac
import os
import sys
import time

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paid_subscription_alert as alerts                 # noqa: E402
from routes import paddle_webhook as webhook             # noqa: E402


PRICE = {
    "id": "pri_pro_monthly",
    "billing_cycle": {"interval": "month", "frequency": 1},
}


def _transaction(amount="3000", subscription="sub_123", txn="txn_123",
                 cycle=PRICE, origin="web"):
    data = {
        "id": txn,
        "subscription_id": subscription,
        "origin": origin,
        "status": "completed",
        "created_at": "2026-08-16T12:00:00Z",
        "details": {"totals": {
            "grand_total": amount,
            "currency_code": "USD",
        }},
        "items": [{"price": cycle}] if cycle else [],
    }
    if subscription is None:
        data.pop("subscription_id")
    return data


class Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def close(self):
        self.closed = True


class Conn:
    def __init__(self, *cursor_rows):
        self.cursors = [Cursor(rows) for rows in cursor_rows]
        self.used = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def cursor(self):
        cur = self.cursors.pop(0) if self.cursors else Cursor()
        self.used.append(cur)
        return cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def test_only_positive_completed_subscription_transactions_are_eligible():
    conn = Conn()
    assert alerts.enqueue(conn, 7, "ai_pro", _transaction("0"),
                          "transaction.completed") is None
    assert alerts.enqueue(conn, 7, "ai_pro", _transaction("3000"),
                          "transaction.created") is None
    assert alerts.enqueue(conn, 7, "ai_pro", _transaction("3000"),
                          "transaction.paid") is None
    # A positive standalone/one-off transaction is revenue, but it is not a
    # new subscription and must not produce this particular email.
    assert alerts.enqueue(
        conn, 7, "ai_pro",
        _transaction("3000", subscription=None, cycle=None, origin="api"),
        "transaction.completed") is None
    assert conn.used == []


def test_a_recurring_price_without_subscription_id_waits_for_completed():
    data = _transaction(subscription=None, cycle=PRICE)
    assert not alerts._is_subscription_transaction(data)
    assert alerts._dedupe_key(data) is None


def test_billing_period_comes_from_paddle_price_not_client_hint():
    yearly = {
        "id": "pri_pro_yearly",
        "billing_cycle": {"interval": "year", "frequency": 1},
    }
    data = _transaction(cycle=yearly)
    data["custom_data"] = {"billing": "monthly"}
    assert alerts._billing_period(data) == "yearly"
    assert alerts._billing_period(_transaction()) == "monthly"


def test_enqueue_is_one_atomic_insert_with_both_dedupe_dimensions():
    # Cursor 1: user facts. Cursor 2: INSERT ... RETURNING.
    conn = Conn(
        [("buyer@example.com", None, "google")],
        [("sub:sub_123",)],
    )
    key = alerts.enqueue(conn, 7, "ai_pro", _transaction(),
                         "transaction.completed")
    assert key == "sub:sub_123"
    assert conn.committed == 1
    sql, params = conn.used[1].executed[0]
    assert "INSERT INTO founder_subscription_alerts" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert params[0] == "sub:sub_123"
    assert params[1] == "txn_123"
    assert params[2] == "sub_123"
    assert params[7] == 3000
    assert params[9] == "transaction.completed"


def test_paid_then_completed_or_a_renewal_does_not_kick_a_second_send(monkeypatch):
    kicked = []
    monkeypatch.setattr(alerts, "kick", kicked.append)

    paid = Conn()
    first = Conn([("buyer@example.com", None, "email")],
                 [("sub:sub_123",)])
    duplicate = Conn([("buyer@example.com", None, "email")], [])
    renewal = Conn([("buyer@example.com", None, "email")], [])

    # Paddle emits paid before completed. It is deliberately ignored; the
    # final completed event is the one that creates the durable alert.
    assert not alerts.enqueue_and_kick(
        paid, 7, "ai_pro", _transaction(), "transaction.paid")
    assert alerts.enqueue_and_kick(
        first, 7, "ai_pro", _transaction(), "transaction.completed")
    # These fake conflicts model transaction_id UNIQUE for completed and
    # subscription_id UNIQUE for a later renewal transaction.
    assert not alerts.enqueue_and_kick(
        duplicate, 7, "ai_pro", _transaction(), "transaction.completed")
    assert not alerts.enqueue_and_kick(
        renewal, 7, "ai_pro", _transaction(txn="txn_renewal"),
        "transaction.completed")
    assert kicked == ["sub:sub_123"]


def test_disabled_alerts_neither_queue_nor_start_a_thread(monkeypatch):
    monkeypatch.setenv("PAID_ALERTS_ENABLED", "0")
    monkeypatch.setattr(
        alerts, "enqueue", lambda *_: pytest.fail("must not enqueue when disabled"))
    monkeypatch.setattr(
        alerts, "kick", lambda *_: pytest.fail("must not send when disabled"))
    assert not alerts.enqueue_and_kick(
        Conn(), 7, "ai_pro", _transaction(), "transaction.completed")


@pytest.mark.parametrize(
    ("event_type", "amount", "alerted"),
    [("transaction.completed", "3000", True),
     ("transaction.completed", "0", False),
     ("transaction.paid", "3000", False)],
)
def test_webhook_wires_the_alert_only_after_real_money(
        monkeypatch, event_type, amount, alerted):
    """Protect the route wiring, not just the outbox helper in isolation."""
    from flask import Flask

    db = object()
    calls = []
    grant_calls = []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "get_db", lambda: db)
    monkeypatch.setattr(webhook, "_user_id_by_subscription", lambda _: None)
    monkeypatch.setattr(webhook, "_user_id_by_customer_email", lambda _: 7)
    monkeypatch.setattr(webhook, "_plan_from_data", lambda _: "ai_pro")
    monkeypatch.setattr(
        webhook, "_trial_aware_grant",
        lambda *_, **kw: (
            grant_calls.append(kw["payment_grant"])
            or (2000, 20, False, "paid")))
    monkeypatch.setattr(webhook, "update_user_subscription_status", lambda *_a, **_k: None)
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **_kwargs: {
            "recorded": True, "newly_paid": int(amount) > 0,
            "amount_cents": int(amount)})
    monkeypatch.setattr(webhook.billing, "record_recovery", lambda *_: None)
    monkeypatch.setattr(webhook.trial_state, "record_paid_conversion", lambda *_: None)
    monkeypatch.setattr(webhook, "_record_discount_use", lambda *_: None)
    monkeypatch.setattr(
        webhook.paid_subscription_alert, "enqueue_and_kick",
        lambda *args: calls.append(args))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": event_type,
        "data": _transaction(amount),
    })
    assert response.status_code == 200
    assert grant_calls == [int(amount) > 0]
    assert bool(calls) is alerted
    if alerted:
        assert calls[0][0] is db
        assert calls[0][1:3] == (7, "ai_pro")
        assert calls[0][4] == "transaction.completed"


def test_webhook_fails_closed_when_signature_secret_is_missing(monkeypatch):
    from flask import Flask

    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setattr(
        webhook, "get_db",
        lambda: pytest.fail("an unsigned webhook must not touch the database"))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.completed",
        "data": _transaction("3000"),
    })

    assert response.status_code == 503
    assert b"signing is not configured" in response.data


def test_signature_verification_accepts_any_rotation_signature(monkeypatch):
    from flask import Flask, request

    secret = "pdl_ntfset_test_secret"
    raw = b'{"event_type":"transaction.completed"}'
    ts = str(int(time.time()))
    valid = hmac.new(secret.encode(), f"{ts}:".encode() + raw,
                     hashlib.sha256).hexdigest()
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", secret)

    app = Flask(__name__)
    with app.test_request_context(
            "/webhook/paddle", method="POST", data=raw,
            headers={"Paddle-Signature":
                     f"ts={ts};h1={valid};h1={'0' * 64}"}):
        assert webhook._verify_paddle_signature(request) is True


def test_signature_verification_rejects_stale_or_malformed_headers(
        monkeypatch):
    from flask import Flask, request

    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    app = Flask(__name__)
    headers = [
        "not-a-signature",
        f"ts={int(time.time()) - 301};h1={'0' * 64}",
        f"ts=not-an-integer;h1={'0' * 64}",
    ]
    for header in headers:
        with app.test_request_context(
                "/webhook/paddle", method="POST", data=b"{}",
                headers={"Paddle-Signature": header}):
            assert webhook._verify_paddle_signature(request) is False


@pytest.mark.parametrize("resolution", [None, "unavailable"])
def test_webhook_never_trusts_browser_claim_when_payer_is_unverified(
        monkeypatch, resolution):
    from flask import Flask

    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_user_id_by_subscription", lambda _: None)
    if resolution == "unavailable":
        def lookup(_customer_id):
            raise webhook._PayerIdentityUnavailable("provider timeout")
    else:
        lookup = lambda _customer_id: None
    monkeypatch.setattr(webhook, "_user_id_by_customer_email", lookup)
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args: pytest.fail("unverified identity must not be recorded"))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.completed",
        "data": {
            **_transaction("3000"),
            "customer_id": "ctm_attacker",
            "custom_data": {"user_id": "999"},
        },
    })

    assert response.status_code == 503


def test_known_subscription_resolves_without_paddle_customer_round_trip(
        monkeypatch):
    monkeypatch.setattr(webhook, "_user_id_by_subscription", lambda _: 7)
    monkeypatch.setattr(
        webhook, "_user_id_by_customer_email",
        lambda _: pytest.fail("known renewals need no customer API call"))

    assert webhook._verified_payer_user_id(
        {"customer_id": "ctm_123"}, "sub_123") == 7


def test_active_subscription_event_preserves_spent_credits(monkeypatch):
    monkeypatch.setattr(webhook, "get_db", lambda: object())
    monkeypatch.setattr(
        webhook.trial_state, "is_recorded_trial", lambda *_args: False)

    grant = webhook._trial_aware_grant(
        7, "ai_pro", "sub_123", "subscription.updated",
        {"status": "active", "items": [{"price": PRICE}]})

    assert grant[0] == 2000
    assert grant[2] is True
    assert "unchanged" in grant[3]


def test_first_paid_transaction_refreshes_the_pool(monkeypatch):
    monkeypatch.setattr(webhook, "get_db", lambda: object())
    monkeypatch.setattr(
        webhook.trial_state, "is_recorded_trial", lambda *_args: True)

    grant = webhook._trial_aware_grant(
        7, "ai_pro", "sub_123", "transaction.completed",
        {"items": [{"price": PRICE}]}, payment_grant=True)

    assert grant[:3] == (2000, webhook.SUB_DAILY_CREDITS, False)


@pytest.mark.parametrize("missing", ["price", "subscription"])
def test_unrepresentable_paid_grant_is_retried_without_mutation(
        monkeypatch, missing):
    from flask import Flask

    data = _transaction()
    if missing == "price":
        data["items"] = [{"price": {"id": "pri_not_in_release"}}]
    else:
        data.pop("subscription_id")
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_: 7)
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrepresentable grant must not consume its paid transition"))
    monkeypatch.setattr(
        webhook, "update_user_subscription_status",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrepresentable grant must not mutate entitlement"))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.completed", "data": data})

    assert response.status_code == 503


def test_unlinked_interim_paid_event_waits_without_retry_storm(monkeypatch):
    """Paddle may omit subscription_id until completed processing finishes.
    The interim event must not consume the paid transition or return 503."""
    from flask import Flask

    data = _transaction(subscription=None)
    data["status"] = "paid"
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(
        webhook, "_verified_payer_user_id",
        lambda *_args: pytest.fail("an unlinked interim event is deferred"))
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **_kwargs: pytest.fail(
            "the transition must remain unconsumed until completed"))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.paid", "data": data})

    assert response.status_code == 200


def test_completed_one_time_charge_is_ledgered_without_subscription_grant(
        monkeypatch):
    from flask import Flask

    data = _transaction(subscription=None, cycle=None, origin="api")
    ledger_calls = []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_args: 7)
    monkeypatch.setattr(webhook, "get_db", lambda: object())
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **kwargs: (
            ledger_calls.append(kwargs)
            or {"recorded": True, "newly_paid": True,
                "amount_cents": 3000}))
    monkeypatch.setattr(
        webhook, "update_user_subscription_status",
        lambda *_args, **_kwargs: pytest.fail(
            "a one-time charge cannot grant a subscription"))

    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.completed", "data": data})

    assert response.status_code == 200
    assert ledger_calls == [{"report_transition": True}]


def test_standalone_failed_transaction_commits_only_its_ledger(monkeypatch):
    from flask import Flask

    class Db:
        committed = 0

        def commit(self):
            self.committed += 1

    db = Db()
    record_calls = []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_: 7)
    monkeypatch.setattr(webhook, "get_db", lambda: db)
    monkeypatch.setattr(
        webhook.billing, "record_transaction",
        lambda *_args, **kwargs: (
            record_calls.append(kwargs)
            or {"recorded": True, "newly_paid": False,
                "amount_cents": 3000}))
    monkeypatch.setattr(
        webhook.billing, "record_failure",
        lambda *_args, **_kwargs: pytest.fail(
            "a standalone charge must not alter subscription entitlement"))

    data = _transaction(subscription=None)
    data["status"] = "past_due"
    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": "transaction.payment_failed", "data": data})

    assert response.status_code == 200
    assert record_calls == [{"report_transition": True, "commit": False}]
    assert db.committed == 1


def test_alert_content_escapes_database_and_paddle_values():
    subject, content = alerts._alert_content(
        7, "ai_pro", "monthly", 3000, "USD",
        _transaction(txn='<script>alert("x")</script>'),
        "transaction.completed",
        {"email": "<img src=x onerror=alert(1)>", "auth_provider": "google"},
    )
    assert subject == "💰 New paid subscriber — Pro"
    assert "<script>" not in content
    assert "<img src=x" not in content
    assert "&lt;script&gt;" in content
    assert "&lt;img src=x" in content


def test_claim_is_atomic_and_can_recover_a_crashed_sender():
    conn = Conn([{
        "dedupe_key": "sub:sub_123",
        "subject": "subject",
        "html_content": "body",
        "attempt_count": 2,
    }])
    row = alerts._claim(conn, "sub:sub_123")
    assert row["attempt_count"] == 2
    sql, params = conn.used[0].executed[0]
    assert "UPDATE founder_subscription_alerts" in sql
    assert "status = 'sending'" in sql
    assert "attempt_count = attempt_count + 1" in sql
    assert "last_attempt_at < NOW()" in sql
    assert params == ("sub:sub_123",)
    assert conn.committed == 1


@pytest.mark.parametrize(
    ("attempt", "seconds"),
    [(1, 300), (2, 900), (3, 3600), (4, 21600), (5, 86400), (50, 86400)],
)
def test_retry_backoff_is_bounded_to_daily(attempt, seconds):
    assert alerts._retry_seconds(attempt) == seconds


def test_brevo_failure_is_recorded_for_retry_not_raised(monkeypatch):
    conn = Conn(
        [{
            "dedupe_key": "sub:sub_123",
            "subject": "subject",
            "html_content": "body",
            "attempt_count": 1,
        }],
        [],
    )
    monkeypatch.setattr(alerts, "send_founder_alert_now", lambda *_: False)
    assert alerts.deliver("sub:sub_123", conn=conn) is False
    finish_sql, finish_params = conn.used[1].executed[0]
    assert "status = 'failed'" in finish_sql
    assert "next_attempt_at" in finish_sql
    assert "attempt_count = %s" in finish_sql
    assert finish_params == (300, "sub:sub_123", 1)
    assert conn.committed == 2


def test_brevo_success_marks_the_row_sent(monkeypatch):
    conn = Conn(
        [{
            "dedupe_key": "sub:sub_123",
            "subject": "subject",
            "html_content": "body",
            "attempt_count": 1,
        }],
        [],
    )
    calls = []
    monkeypatch.setattr(
        alerts, "send_founder_alert_now",
        lambda subject, content: calls.append((subject, content)) or True)
    assert alerts.deliver("sub:sub_123", conn=conn) is True
    assert calls == [("subject", "body")]
    finish_sql, finish_params = conn.used[1].executed[0]
    assert "status = 'sent'" in finish_sql
    assert "sent_at = NOW()" in finish_sql
    assert "attempt_count = %s" in finish_sql
    assert finish_params == ("sub:sub_123", 1)


def test_immediate_send_survives_database_outage(monkeypatch):
    monkeypatch.setattr(
        alerts, "_connect",
        lambda: (_ for _ in ()).throw(RuntimeError("database offline")))
    assert alerts.deliver("sub:sub_123") is False


def test_retry_scan_reports_database_outage(monkeypatch):
    monkeypatch.setattr(
        alerts, "_connect",
        lambda: (_ for _ in ()).throw(RuntimeError("database offline")))
    assert alerts.retry_due() == {
        "due": 0,
        "sent": 0,
        "error": "database offline",
    }


def test_migration_suppresses_existing_subscriptions_and_enforces_uniqueness():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "migrations", "021_founder_subscription_alerts.sql")
    sql = open(path, encoding="utf-8").read()
    assert "transaction_id   TEXT NOT NULL UNIQUE" in sql
    assert "subscription_id  TEXT UNIQUE" in sql
    assert "'suppressed'" in sql
    assert "FROM payments p" in sql
    assert "p.status = 'completed'" in sql
    assert "p.amount_cents > 0" in sql
    assert "WHERE u.subscription_id IS NOT NULL" not in sql
    assert "ON CONFLICT DO NOTHING" in sql

    required_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "migrations",
        "022_founder_alert_subscription_required.sql")
    required_sql = open(required_path, encoding="utf-8").read()
    assert "ALTER COLUMN subscription_id SET NOT NULL" in required_sql
