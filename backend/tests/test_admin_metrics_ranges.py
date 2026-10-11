"""G1/G3: admin days are Dubai days, and comparisons are fair."""
from datetime import date, datetime, timezone

import pytest

from admin_metrics import ranges

# 18:20 UTC on 10 Oct 2026 is 22:20 in Dubai (UTC+4, no DST).
NOW = datetime(2026, 10, 10, 18, 20, 5, tzinfo=timezone.utc)
LATE = datetime(2026, 10, 10, 21, 30, tzinfo=timezone.utc)   # 01:30 on 11 Oct


def test_today_starts_at_dubai_midnight_and_ends_now():
    p = ranges.parse({"range": "today"}, now=NOW)
    assert p.from_day == p.to_day == date(2026, 10, 10)
    assert p.start == datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    assert p.end == NOW and p.partial and p.days == 1
    m = p.meta()
    assert m["start_utc"] == "2026-10-09T20:00:00Z"
    assert m["end_utc"] == "2026-10-10T18:20:05Z"


def test_today_rolls_over_at_dubai_midnight_not_utc_midnight():
    p = ranges.parse({"range": "today"}, now=LATE)
    assert p.from_day == date(2026, 10, 11)
    assert p.start == datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)


def test_compare_is_cut_at_the_same_elapsed_time():
    c = ranges.parse({"range": "today"}, now=NOW).compare()
    assert c.label == "vs yesterday at this time"
    assert c.start == datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
    assert c.end == datetime(2026, 10, 9, 18, 20, 5, tzinfo=timezone.utc)
    week = ranges.parse({"range": "7d"}, now=NOW)
    assert week.from_day == date(2026, 10, 4) and week.days == 7
    wc = week.compare()
    assert wc.label == "vs previous 7 days"
    assert (wc.from_day, wc.to_day) == (date(2026, 9, 27), date(2026, 10, 3))
    assert wc.end == datetime(2026, 10, 3, 18, 20, 5, tzinfo=timezone.utc)


def test_complete_past_ranges_end_at_midnight():
    y = ranges.parse({"range": "yesterday"}, now=NOW)
    assert y.end == datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    assert not y.partial and y.compare().label == "vs the day before"
    lm = ranges.parse({"range": "last_month"}, now=NOW)
    assert (lm.from_day, lm.to_day) == (date(2026, 9, 1), date(2026, 9, 30))
    mtd = ranges.parse({"range": "mtd"}, now=NOW)
    assert mtd.from_day == date(2026, 10, 1) and mtd.days == 10


def test_naive_bounds_for_timestamp_columns_and_aware_for_timestamptz():
    params = ranges.parse({"range": "today"}, now=NOW).params()
    assert params["start"] == datetime(2026, 10, 9, 20, 0)
    assert params["start"].tzinfo is None
    assert params["start_tz"].tzinfo is not None
    assert params["tz"] == "Asia/Dubai"


@pytest.mark.parametrize("args, message", [
    ({"range": "bogus"}, "Unknown range"),
    ({"range": "custom", "from": "2026-10-05"}, "'to'"),
    ({"range": "custom", "from": "2026-10-05", "to": "2026-10-01"}, "on or before"),
    ({"range": "custom", "from": "2026-10-01", "to": "2026-10-12"}, "future"),
    ({"range": "custom", "from": "2025-01-01", "to": "2026-10-01"}, "at most 400"),
    ({"range": "all"}, "only available on lists"),
])
def test_invalid_ranges_are_plain_400s(args, message):
    with pytest.raises(ranges.RangeError, match=message):
        ranges.parse(args, now=NOW)


def test_custom_and_all():
    p = ranges.parse({"range": "custom", "from": "2026-10-01",
                      "to": "2026-10-09"}, now=NOW)
    assert p.label == "1–9 Oct" and p.days == 9 and not p.partial
    assert ranges.span_label(date(2026, 9, 28), date(2026, 10, 3)) == \
        "28 Sep – 3 Oct"
    a = ranges.parse({"range": "all"}, allow_all=True, now=NOW)
    assert a.is_all and a.meta()["from"] is None and a.compare() is None


def test_day_sql_converts_each_column_kind_once():
    assert ranges.local_date_sql("x", tz_aware=True) == \
        "((x) AT TIME ZONE %(tz)s)::date"
    assert "AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s" in \
        ranges.local_date_sql("x", tz_aware=False)
    assert ranges.utc_offset(NOW) == "+04:00"
    assert ranges.week_label(date(2026, 10, 5)) == "Week of 5 Oct"
