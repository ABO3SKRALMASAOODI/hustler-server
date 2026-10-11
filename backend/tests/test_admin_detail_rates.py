"""Growth → a source's detail rows (Google, ChatGPT, a campaign) carry a
signup rate over the same days as the source's own rate.

Visitors are classified from 3 Oct but signups only have a source from 7 Oct
12:34 UTC. Over a period that starts earlier, the frontend used to divide a
detail's signups by its people over the whole period: on 11 Oct, Search read
25.5% and Google, under it, 12.7%.
"""
from datetime import date, datetime, timezone

from admin_metrics import channels_report, ranges, visitors

SINCE = datetime(2026, 10, 7, 12, 34, 12, tzinfo=timezone.utc)
GOOGLE = {"source": "www.google.com", "medium": "organic", "at": 1}
BING = {"source": "www.bing.com", "medium": "organic", "at": 1}


def _signup(uid, touch, created=datetime(2026, 10, 9, 10, 0)):
    return {"id": uid, "created_at": created, "has_row": True,
            "attribution": {"first": touch, "last": touch},
            "tracking": "tracked", "device_id": None, "told": None,
            "paid": False, "cents": 0, "first_page": None}


def _person(i, touch):
    return {"device_id": f"d{i}",
            "first_attribution": {"first": touch, "last": touch},
            "first_referrer": touch["source"], "first_page": "/"}


def _report(monkeypatch, first_day):
    whole = [_person(i, GOOGLE) for i in range(8)] + [_person(9, BING)]
    since_sources = whole[:2] + whole[-1:]       # 2 Google, 1 Bing
    monkeypatch.setattr(channels_report, "signup_rows", lambda cur, p: [
        _signup(1, GOOGLE), _signup(2, BING),
        # Before sources were recorded: never part of a rate.
        _signup(3, None, created=datetime(2026, 10, 2, 10, 0))])
    monkeypatch.setattr(visitors, "people_rows", lambda cur, p: (
        since_sources if p.start is not None and p.start >= SINCE else whole))
    monkeypatch.setattr(visitors, "classify",
                        lambda cur, p: {"link_preview": 0})
    period = ranges.make_period(
        "custom", first_day, date(2026, 10, 10),
        now=datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc))
    data = channels_report.acquisition_report(None, period, "first")
    return {c["channel"]: c for c in data["channels"]}, data


def test_detail_rates_use_the_channel_rate_window(monkeypatch):
    by, data = _report(monkeypatch, date(2026, 10, 1))
    assert data["rate_since"] == "2026-10-07T12:34:12Z"
    search = by["search"]
    assert search["people"] == 9                     # the whole period
    assert search["signup_rate"] == 66.7             # 2 signups ÷ 3 people
    details = {d["key"]: d for d in search["details"]}
    assert details["google"]["people"] == 8          # the whole period
    assert details["google"]["signup_rate"] == 50.0  # 1 ÷ 2 since 7 Oct
    assert details["bing"]["signup_rate"] == 100.0   # 1 ÷ 1
    for d in by["not_recorded"]["details"]:
        assert d["signup_rate"] is None


def test_detail_rates_without_a_window_use_the_whole_period(monkeypatch):
    by, data = _report(monkeypatch, date(2026, 10, 8))
    assert data["rate_since"] is None
    details = {d["key"]: d for d in by["search"]["details"]}
    assert details["google"]["signup_rate"] == 50.0  # 1 ÷ 2 (period from 8 Oct)
