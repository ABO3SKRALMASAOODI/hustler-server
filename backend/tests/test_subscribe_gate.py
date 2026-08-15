"""Unsubscribed accounts see subscription cards AFTER one real edit.

The first indexed agent turn still runs. The next prompt is the wall.
Active trials pass because they are subscribed.
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing                                           # noqa: E402
import credits                                           # noqa: E402
from routes import video                                 # noqa: E402
from routes.paddle_webhook import (                      # noqa: E402
    PRICE_CREDITS, PRICE_TO_PLAN, PLAN_CREDITS, credits_for_price,
)


class _Cur:
    """Sequential canned rows: users lookup, then the completed-edit probe."""

    def __init__(self, user, edited=False):
        self._user = user
        self._edited = edited
        self.sql = None
        self._step = 0

    def execute(self, sql, *a, **kw):
        self.sql = sql
        self._step += 1

    def fetchone(self):
        if self._step <= 1:
            return self._user
        return {"?column?": 1} if self._edited else None


def test_first_turn_is_not_gated():
    cur = _Cur({"is_subscribed": 0, "email": "new@example.com"}, edited=False)
    assert video._subscribe_gate_applies(cur, 1) is False


def test_unsubscribed_user_is_gated_after_a_real_edit():
    cur = _Cur({"is_subscribed": 0, "email": "new@example.com"}, edited=True)
    assert video._subscribe_gate_applies(cur, 1) is True
    assert "video_jobs" in (cur.sql or "")
    assert "result->>'status' = 'replied'" in cur.sql
    assert "result->>'outcome' IN ('fulfilled', 'partial')" in cur.sql


def test_deterministic_final_failure_requires_a_new_edit():
    assert video._deterministic_final_failure({
        "result": {"failure": {"kind": "invalid_edl", "retryable": False}},
        "error": "final render black-frame check failed",
    }) is True
    assert video._deterministic_final_failure({
        "result": {"failure": {"kind": "transient_infrastructure",
                                "retryable": True}},
        "error": "HTTP 503",
    }) is False


def test_subscriber_and_trial_pass():
    # A live trial is is_subscribed=1 from checkout day zero. No second
    # query: the gate never looks for an edit on a subscriber.
    cur = _Cur({"is_subscribed": 1, "email": "trial@example.com"}, edited=True)
    assert video._subscribe_gate_applies(cur, 1) is False


def test_admin_passes():
    from routes.admin import ADMIN_EMAIL
    cur = _Cur({"is_subscribed": 0, "email": ADMIN_EMAIL}, edited=True)
    assert video._subscribe_gate_applies(cur, 1) is False


def test_offer_body_is_subscribe_not_trial():
    body = video._subscribe_offer_body()
    assert body["subscribe_offer"] is True
    assert body["trial_days"] == 0
    assert body["code"] == "trial_offer"
    by_id = {p["id"]: p for p in body["plans"]}
    assert by_id["ai"]["monthly"] == 15
    assert by_id["ai"]["credits"] == 1000
    assert by_id["ai_pro"]["monthly"] == 30
    assert by_id["ai_pro"]["credits"] == 2000
    assert by_id["ai_max"]["monthly"] == 50
    assert by_id["ai_max"]["credits"] == 5000
    assert "trial" not in body["error"].lower()


def test_shopfront_credits_are_half():
    assert PLAN_CREDITS["ai"] == 1000
    assert PLAN_CREDITS["ai_pro"] == 2000
    assert PLAN_CREDITS["ai_max"] == 5000
    assert credits.PLAN_MONTHLY_LIMITS["ai"] == 1000
    assert billing.PLAN_PRICES_USD["ai"]["monthly"] == 15


def test_legacy_trial_prices_still_grant_the_old_pool():
    old_creator = "pri_01kyde25cwqf7t2bk1ekky2pyp"
    old_frontier = "pri_01kyg21hzbbz360kn0ptjnpdar"
    assert PRICE_TO_PLAN[old_creator] == "ai"
    assert PRICE_TO_PLAN[old_frontier] == "ai_max"
    assert credits_for_price(old_creator, "ai") == 2000
    assert credits_for_price(old_frontier, "ai_max") == 10000
    new_creator = "pri_01m00w4aa9nqj2r3x30jkagbq0"
    assert PRICE_CREDITS[new_creator] == 1000
    assert credits_for_price(new_creator, "ai") == 1000


if __name__ == "__main__":                                  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
