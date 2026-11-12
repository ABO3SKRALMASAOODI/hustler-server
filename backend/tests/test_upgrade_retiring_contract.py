"""Ticket #1039: an upgrade's old contract must never take the account back.

Live on 11 Nov 2026 (user 2957): Plus paid 16:02, Pro paid 16:05:09 (new
subscription, created 16:05:10). The webhook then asked Paddle to end the old
Plus contract at its period end, and Paddle's subscription.updated for that
OLD contract (still 'active', Plus price, scheduled cancel) landed at 16:05:12.
It is a grant event, so it re-granted Plus and stored the old subscription id
over the new one. The customer kept meeting the Pro upgrade card for the
website demo they had just paid for, and the old contract's cancel on 11 Dec
would have matched the stored id and downgraded them to free.

    cd backend && python -m pytest tests/test_upgrade_retiring_contract.py -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
os.environ.setdefault("PADDLE_API_KEY", "pdl_live_test_key")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

import billing  # noqa: E402
import billing_sync  # noqa: E402
from routes import paddle  # noqa: E402
from routes import paddle_webhook as webhook  # noqa: E402


class _Db:
    def commit(self):
        pass

    def rollback(self):
        pass


def _webhook(monkeypatch, stored_subscription_id):
    grants = []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _req: True)
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_args: 2957)
    monkeypatch.setattr(webhook, "get_db", lambda: _Db())
    monkeypatch.setattr(
        webhook, "_stored_subscription_id", lambda _uid: stored_subscription_id)
    monkeypatch.setattr(
        webhook, "_trial_aware_grant",
        lambda *_args, **_kwargs: (500, 0, True, "update"))
    monkeypatch.setattr(
        webhook, "update_user_subscription_status",
        lambda *args, **kwargs: grants.append(args))
    monkeypatch.setattr(webhook.billing, "set_status", lambda *_args: None)
    monkeypatch.setattr(
        webhook.trial_state, "sync_from_subscription", lambda *_args: None)
    monkeypatch.setattr(webhook, "_record_discount_use", lambda *_args: None)
    import subscription_reporting
    monkeypatch.setattr(
        subscription_reporting, "record_snapshot", lambda *_a, **_k: False)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(webhook.paddle_webhook)
    return flask_app.test_client(), grants


def _old_plus_update(scheduled_cancel=True):
    data = {
        "id": "sub_old_plus", "status": "active",
        "next_billed_at": "2026-12-11T16:02:34Z",
        "items": [{"price": {
            "id": paddle.PLANS["plus_2026"]["price_id"],
            "billing_cycle": {"interval": "month", "frequency": 1},
        }}],
    }
    if scheduled_cancel:
        data["scheduled_change"] = {
            "action": "cancel", "effective_at": "2026-12-11T16:02:34Z"}
    return {"event_type": "subscription.updated", "data": data}


def test_retiring_old_contract_never_regrants_its_plan(monkeypatch):
    client, grants = _webhook(monkeypatch, stored_subscription_id="sub_new_pro")

    response = client.post("/webhook/paddle", json=_old_plus_update())

    assert response.status_code == 200
    assert grants == [], "the old Plus contract replaced the paid Pro one"


def test_the_current_contract_scheduled_to_cancel_still_updates(monkeypatch):
    """A customer cancelling their OWN current plan keeps it to period end:
    that event is about the stored contract and still goes through."""
    client, grants = _webhook(monkeypatch, stored_subscription_id="sub_old_plus")

    response = client.post("/webhook/paddle", json=_old_plus_update())

    assert response.status_code == 200
    assert len(grants) == 1
    assert grants[0][3] == "sub_old_plus" and grants[0][4] == "plus_2026"


def test_another_contract_without_a_scheduled_cancel_still_grants(monkeypatch):
    """A genuinely new contract (no scheduled cancel) is not ignored just
    because the account still points at an older one."""
    client, grants = _webhook(monkeypatch, stored_subscription_id="sub_new_pro")

    response = client.post(
        "/webhook/paddle", json=_old_plus_update(scheduled_cancel=False))

    assert response.status_code == 200
    assert len(grants) == 1


# ── The hourly reconciler repairs an account the old event already hit ───────

class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))
        if "FROM payments" in sql:
            self._rows = [(s,) for s in self.conn.payment_subs]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _Conn:
    def __init__(self, payment_subs):
        self.payment_subs = payment_subs
        self.sql = []
        self.committed = 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass


def _subscriptions(monkeypatch, table):
    monkeypatch.setattr(
        billing_sync, "fetch_subscription",
        lambda sid: (table[sid], None) if sid in table else (None, "not_found"))
    monkeypatch.setattr(
        billing_sync, "fetch_transactions", lambda _sid: ([], None))
    monkeypatch.setattr(billing, "subscription_has_paid", lambda *_a: True)
    statuses = []
    monkeypatch.setattr(
        billing, "set_status", lambda *args: statuses.append(args))
    monkeypatch.setattr(billing, "record_recovery", lambda *_a: None)
    monkeypatch.setattr(
        billing_sync.trial_state, "record_paid_conversion", lambda *_a: None)
    activations = []
    monkeypatch.setattr(
        billing_sync, "_activate",
        lambda *args, **kwargs: activations.append(args))
    return statuses, activations


def _sub(sid, plan, scheduled_cancel=False, status="active"):
    data = {"id": sid, "status": status,
            "next_billed_at": "2026-12-11T16:05:09Z",
            "billing_cycle": {"interval": "month", "frequency": 1},
            "items": [{"price": {"id": paddle.PLANS[plan]["price_id"]}}]}
    if scheduled_cancel:
        data["scheduled_change"] = {"action": "cancel"}
    return data


ROW = {"id": 2957, "email": "buyer@example.test",
       "subscription_id": "sub_old_plus", "plan": "plus_2026",
       "is_subscribed": 1, "billing_status": "active"}


def test_reconciler_adopts_the_paid_upgrade_contract(monkeypatch):
    statuses, activations = _subscriptions(monkeypatch, {
        "sub_old_plus": _sub("sub_old_plus", "plus_2026", scheduled_cancel=True),
        "sub_new_pro": _sub("sub_new_pro", "pro_2026"),
    })
    conn = _Conn(["sub_new_pro"])

    report = billing_sync.reconcile_user(conn, dict(ROW))

    updates = [p for s, p in conn.sql if s.startswith("UPDATE users")]
    assert len(updates) == 1
    expiry, sub_id, plan, limit, user_id = updates[0]
    assert (sub_id, plan, user_id) == ("sub_new_pro", "pro_2026", 2957)
    assert limit == webhook.credits_for_price(
        paddle.PLANS["pro_2026"]["price_id"], "pro_2026")
    # The pool the Pro payment already granted is kept, never refilled.
    update_sql = [s for s, _ in conn.sql if s.startswith("UPDATE users")][0]
    assert "credits_monthly =" not in update_sql
    assert "credits_balance" not in update_sql
    assert activations == []
    assert statuses and statuses[-1][2:4] == ("active", "pro_2026")
    assert report["plan"] == "pro_2026"
    assert any("adopted" in c for c in report["changes"])


def test_reconciler_leaves_a_plain_cancellation_alone(monkeypatch):
    """No other live paid contract: a customer who scheduled a cancel keeps
    their plan to period end, exactly as before."""
    statuses, activations = _subscriptions(monkeypatch, {
        "sub_old_plus": _sub("sub_old_plus", "plus_2026", scheduled_cancel=True),
    })
    conn = _Conn([])

    report = billing_sync.reconcile_user(conn, dict(ROW))

    assert not [s for s, _ in conn.sql if s.startswith("UPDATE users")]
    assert report["plan"] == "plus_2026"


def test_reconciler_never_adopts_a_dead_or_retiring_contract(monkeypatch):
    _subscriptions(monkeypatch, {
        "sub_old_plus": _sub("sub_old_plus", "plus_2026", scheduled_cancel=True),
        "sub_canceled": _sub("sub_canceled", "pro_2026", status="canceled"),
        "sub_also_retiring": _sub("sub_also_retiring", "max_2026",
                                  scheduled_cancel=True),
    })
    conn = _Conn(["sub_canceled", "sub_also_retiring"])

    report = billing_sync.reconcile_user(conn, dict(ROW))

    assert not [s for s, _ in conn.sql if s.startswith("UPDATE users")]
    assert report["plan"] == "plus_2026"
