"""Admin periods (G1, G3): calendar days in ADMIN_TIMEZONE, compared fairly.

A period is a set of whole admin-timezone days. Bounds are computed here as
UTC instants and used as `col >= start AND col < end`, so indexes still work:
`start`/`end` are naive UTC for `timestamp` columns (stored in UTC) and
`start_tz`/`end_tz` are aware for `timestamptz` columns. A period that
includes today ends *now*; its comparison is the immediately preceding period
of equal length cut at the same elapsed time ("vs yesterday at this time").
"""
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from admin_metrics import defs

RANGE_KEYS = ("today", "yesterday", "7d", "30d", "90d", "mtd", "last_month",
              "custom", "all")
RANGE_LABELS = {
    "today": "Today", "yesterday": "Yesterday", "7d": "Last 7 days",
    "30d": "Last 30 days", "90d": "Last 90 days", "mtd": "This month",
    "last_month": "Last month", "custom": "Custom", "all": "Any time",
}
MAX_CUSTOM_DAYS = 400
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")


class RangeError(ValueError):
    """A plain-language 400: the message is shown to the owner as-is."""


def _zone():
    name = (os.getenv("ADMIN_TIMEZONE") or "Asia/Dubai").strip()
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return "Asia/Dubai", ZoneInfo("Asia/Dubai")


TZ_NAME, TZ = _zone()


def utc_now():
    return datetime.now(timezone.utc)


def local_today(now=None):
    return (now or utc_now()).astimezone(TZ).date()


def local_midnight(day):
    """The UTC instant at which `day` starts in the admin timezone."""
    return datetime.combine(day, time(0), tzinfo=TZ).astimezone(timezone.utc)


def local_day(instant):
    if instant is None:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(TZ).date()


def utc_offset(now=None):
    off = (now or utc_now()).astimezone(TZ).utcoffset() or timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def day_label(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


def span_label(first, last):
    if first == last:
        return day_label(first)
    if first.year == last.year and first.month == last.month:
        return f"{first.day}–{last.day} {MONTHS[last.month - 1]}"
    return f"{day_label(first)} – {day_label(last)}"


def week_label(monday):
    return f"Week of {day_label(monday)}"


def naive(instant):
    return None if instant is None else \
        instant.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass
class Period:
    key: str
    label: str
    from_day: date = None
    to_day: date = None
    start: datetime = None          # aware UTC; None for 'all'
    end: datetime = None            # aware UTC, never after now; None = open
    partial: bool = False
    compare_label: str = None
    extra: dict = field(default_factory=dict)

    @property
    def days(self):
        if self.from_day is None or self.to_day is None:
            return None
        return (self.to_day - self.from_day).days + 1

    @property
    def is_all(self):
        return self.start is None

    def day_list(self):
        if self.from_day is None:
            return []
        return [self.from_day + timedelta(days=i) for i in range(self.days)]

    def params(self):
        """SQL parameters: naive bounds for timestamp, aware for timestamptz."""
        end = self.end or utc_now()
        start = self.start or datetime(2000, 1, 1, tzinfo=timezone.utc)
        return {"start": naive(start), "end": naive(end),
                "start_tz": start, "end_tz": end, "tz": TZ_NAME}

    def meta(self):
        if self.is_all and self.key == "all":
            return {"key": "all", "label": self.label, "from": None,
                    "to": None, "days": None, "partial": False,
                    "start_utc": None, "end_utc": None}
        return {"key": self.key, "label": self.label,
                "from": self.from_day.isoformat() if self.from_day else None,
                "to": self.to_day.isoformat() if self.to_day else None,
                "days": self.days, "partial": self.partial,
                "start_utc": defs.iso(self.start), "end_utc": defs.iso(self.end)}

    def compare(self):
        """G3: the preceding period of equal length, cut at the same time."""
        if self.is_all or self.days is None:
            return None
        length = timedelta(days=self.days)
        start_local = self.start.astimezone(TZ).replace(tzinfo=None) - length
        end_local = self.end.astimezone(TZ).replace(tzinfo=None) - length
        start = start_local.replace(tzinfo=TZ).astimezone(timezone.utc)
        end = end_local.replace(tzinfo=TZ).astimezone(timezone.utc)
        return Period(key="compare", label=self.compare_label,
                      from_day=self.from_day - length,
                      to_day=self.to_day - length,
                      start=start, end=end, partial=self.partial)

    def compare_meta(self):
        c = self.compare()
        if c is None:
            return None
        return {"label": c.label, "from": c.from_day.isoformat(),
                "to": c.to_day.isoformat(), "start_utc": defs.iso(c.start),
                "end_utc": defs.iso(c.end)}


def _compare_label(key, days):
    if key == "today":
        return "vs yesterday at this time"
    if days == 1:
        return "vs the day before"
    return f"vs previous {days} days"


def make_period(key, first, last, now=None, label=None):
    now = now or utc_now()
    start = local_midnight(first)
    natural_end = local_midnight(last + timedelta(days=1))
    end = min(natural_end, now)
    days = (last - first).days + 1
    return Period(key=key, label=label or RANGE_LABELS.get(key, key),
                  from_day=first, to_day=last, start=start, end=end,
                  partial=natural_end > now,
                  compare_label=_compare_label(key, days))


def _parse_day(value, name):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise RangeError(f"'{name}' must be a date like 2026-10-01.")


def parse(args, default="30d", allow_all=False, now=None):
    """Read range/from/to from request args into a Period (400 on misuse)."""
    now = now or utc_now()
    key = (args.get("range") or default).strip().lower()
    if key not in RANGE_KEYS:
        raise RangeError("Unknown range. Use today, yesterday, 7d, 30d, 90d, "
                         "mtd, last_month, custom or all.")
    today = local_today(now)
    if key == "all":
        if not allow_all:
            raise RangeError("This page needs a date range; 'all' is only "
                             "available on lists.")
        return Period(key="all", label=RANGE_LABELS["all"])
    if key == "today":
        return make_period(key, today, today, now)
    if key == "yesterday":
        day = today - timedelta(days=1)
        return make_period(key, day, day, now)
    if key in ("7d", "30d", "90d"):
        n = int(key[:-1])
        return make_period(key, today - timedelta(days=n - 1), today, now)
    if key == "mtd":
        return make_period(key, today.replace(day=1), today, now)
    if key == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return make_period(key, last.replace(day=1), last, now)
    first = _parse_day(args.get("from"), "from")
    last = _parse_day(args.get("to"), "to")
    if last > today:
        raise RangeError("'to' can't be in the future.")
    if first > last:
        raise RangeError("'from' must be on or before 'to'.")
    if (last - first).days + 1 > MAX_CUSTOM_DAYS:
        raise RangeError(f"Choose at most {MAX_CUSTOM_DAYS} days.")
    return make_period("custom", first, last, now,
                       label=span_label(first, last))


def trailing_days(n, end_day=None, now=None):
    """A Period of the n days ending end_day (default today), for sparklines."""
    now = now or utc_now()
    end_day = end_day or local_today(now)
    return make_period("spark", end_day - timedelta(days=n - 1), end_day, now)


def local_date_sql(column, tz_aware):
    """SQL bucketing a column into admin-timezone days (G1)."""
    if tz_aware:
        return f"(({column}) AT TIME ZONE %(tz)s)::date"
    return f"(({column}) AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date"
