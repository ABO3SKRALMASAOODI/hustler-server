"""The free taste, and the wall at the end of it.

One rule decides whether a visitor can run an agent turn (round 50): they hold
a plan, OR they still have a credit left of the free grant. These tests pin the
edges of that rule because both directions are expensive to get wrong — closing
early bills someone who was promised 50 free credits, and opening late hands
out unmetered model spend.

    cd backend && python -m pytest tests -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credits                                          # noqa: E402
import plan_gate                                        # noqa: E402


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **kw):
        pass

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _Conn:
    """Hands back one canned users row. `row=None` means "no such user";
    raise=True simulates the database being unreachable."""

    def __init__(self, row, raise_=False):
        self._row = row
        self._raise = raise_

    def cursor(self):
        if self._raise:
            raise RuntimeError("connection lost")
        return _Cur(self._row)


def _user(subscribed, balance):
    # RealDictCursor shape — the routes that call this use it.
    return {"is_subscribed": 1 if subscribed else 0,
            "credits_balance": balance}


def test_the_grant_is_zero():
    assert credits.FREE_GRANT_CREDITS == 0


def test_free_account_with_credits_passes():
    assert plan_gate.needs_plan(_Conn(_user(False, 50)), 1) is False
    assert plan_gate.needs_plan(_Conn(_user(False, 1)), 1) is False


def test_free_account_out_of_credits_is_gated():
    assert plan_gate.needs_plan(_Conn(_user(False, 0)), 1) is True
    # Under one whole credit cannot fund a turn (min_credits=1.0 everywhere
    # that spends), so the wall is 1.0, not 0 — a fractional remainder would
    # otherwise let a turn start that the credit check then refuses.
    assert plan_gate.needs_plan(_Conn(_user(False, 0.4)), 1) is True


def test_subscribers_are_never_gated():
    # Includes a trialling account: Paddle creates the subscription at checkout
    # and only charges on day 3, so is_subscribed is true from day zero. Their
    # empty-pool wall is trial_cap_reached, which says something different.
    assert plan_gate.needs_plan(_Conn(_user(True, 0)), 1) is False


def test_it_fails_open():
    # A database hiccup must never lock a paying customer out of work they
    # already paid for. Worst case of failing open is one free agent turn.
    assert plan_gate.needs_plan(_Conn(None, raise_=True), 1) is False
    assert plan_gate.needs_plan(_Conn(None), 1) is False


def test_gate_response_carries_the_grant():
    # The studio quotes "that's your 50" from this, so the frontend never
    # hardcodes a number that can drift from credits.py.
    body, status = plan_gate.gate_response(lambda d: d)
    assert status == 402
    assert body["code"] == "plan_required"
    assert body["free_credits"] == credits.FREE_GRANT_CREDITS


def test_free_plan_limit_is_the_grant():
    assert credits.PLAN_MONTHLY_LIMITS["free"] == 0
    assert credits.FREE_GRANT_CREDITS == 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
