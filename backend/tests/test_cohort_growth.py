"""Cohort growth distinguishes no comparison from genuine zero growth."""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes.admin_video import _attach_growth, _growth_percent  # noqa: E402


def _row(signups, uploaded=0, messaged=0, exported=0, lead_in=False):
    return {"signed_up": signups, "uploaded": uploaded,
            "messaged": messaged, "exported": exported,
            "lead_in": lead_in}


def test_period_growth_is_computed_for_each_product_stage():
    rows = [_row(10, 8, 5, 2), _row(15, 8, 10, 1)]
    _attach_growth(rows)
    assert rows[0]["growth"]["signed_up"] is None
    assert rows[1]["growth"] == {
        "signed_up": 50.0,
        "uploaded": 0.0,
        "messaged": 100.0,
        "exported": -50.0,
    }


def test_lead_in_and_zero_denominator_are_not_fake_growth():
    rows = [_row(0, lead_in=True), _row(7, 5), _row(0, 0)]
    _attach_growth(rows)
    assert all(v is None for v in rows[1]["growth"].values())
    assert rows[2]["growth"]["signed_up"] == -100.0
    assert rows[2]["growth"]["uploaded"] == -100.0


def test_growth_after_a_real_zero_is_unknown_not_infinite():
    rows = [_row(4, 0), _row(8, 3)]
    _attach_growth(rows)
    assert rows[1]["growth"]["signed_up"] == 100.0
    assert rows[1]["growth"]["uploaded"] is None


def test_week_to_date_growth_uses_equal_elapsed_windows():
    assert _growth_percent(125, 88) == 42.0
    assert _growth_percent(88, 88) == 0.0
    assert _growth_percent(4, 0) is None
