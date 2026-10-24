"""Corrected numbers for the classic admin page (the owner's original design).

The classic page keeps its layout; these helpers only change where its
numbers come from:

* visitors are PEOPLE (admin_metrics.visitors): robots, the owner's own
  browsers, Meta link previews and outreach link checks are left out and
  reported beside the count instead of inside it;
* days are admin-timezone days (Dubai midnight to midnight), like every
  other admin number;
* outreach links (`?r=<code>`) are traced from the CRM's code to the people
  who opened it and the accounts that signed up with it. Valmera never
  stores who a code was sent to; the CRM owns that mapping.
"""
import dataclasses
from datetime import date, timedelta

from acquisition import UNTAGGED_CAMPAIGNS, campaign_label
from admin_metrics import db, defs, outreach, ranges, visitors

LEFT_OUT = ("robot", "link_preview", "internal", "no_signal")


def _people_since():
    """The first WHOLE admin day people were counted. The tracker started at
    20:07 Dubai on 3 Oct 2026; that evening is not a day, and dividing the
    whole day's signups by four hours of visitors read as 140 per 100."""
    day = ranges.local_day(defs.VISITS_SINCE)
    start = ranges.local_midnight(day)
    if start < defs.VISITS_SINCE:
        start = ranges.local_midnight(day + timedelta(days=1))
    return start


PEOPLE_SINCE = _people_since()


def tracked_part(period):
    """The part of `period` since people were counted (None if none)."""
    if period.end is not None and period.end <= PEOPLE_SINCE:
        return None
    if period.start is None or period.start < PEOPLE_SINCE:
        return dataclasses.replace(period, start=PEOPLE_SINCE)
    return period


def fully_tracked(period):
    return period.start is not None and period.start >= PEOPLE_SINCE


def left_out(cls):
    """What was not counted as a person, in the owner's words."""
    if cls is None:
        return None
    return {"robots": int(cls["robot"]),
            "link_checks": int(cls["link_preview"]),
            "internal": int(cls["internal"]),
            "no_signal": int(cls["no_signal"]),
            "total": int(sum(cls[k] for k in LEFT_OUT))}


def classify(cur, period):
    part = tracked_part(period)
    return visitors.classify(cur, part) if part is not None else None


def _trend(now, before):
    if not before:
        return None
    return round((now - before) / before * 100.0, 1)


def overview_visits(cur):
    """Visits card: people today and over 7 days, page views beside them."""
    today = ranges.parse({"range": "today"})
    week = ranges.parse({"range": "7d"})
    prev = week.compare()
    t = classify(cur, today)
    w = classify(cur, week)
    p = classify(cur, prev) if fully_tracked(prev) else None
    cur.execute("""
        SELECT count(*) FILTER (WHERE visited_at >= %(t0)s) AS views_today,
               count(*) AS views_week
          FROM analytics_page_visits
         WHERE visited_at >= %(w0)s""",
                {"t0": ranges.naive(today.start), "w0": ranges.naive(week.start)})
    v = cur.fetchone() or {}
    people_today = t["person"] if t else None
    people_week = w["person"] if w else None
    return {
        "today": int(v.get("views_today") or 0),
        "week": int(v.get("views_week") or 0),
        "unique_today": people_today,
        "unique_week": people_week,
        "trend_week": _trend(people_week or 0, p["person"]) if p else None,
        "excluded_today": left_out(t),
        "excluded_week": left_out(w),
        "measure": "people",
        "people_since": defs.iso(PEOPLE_SINCE),
        "timezone": ranges.TZ_NAME,
    }


def first_visit_day(cur):
    def compute():
        cur.execute("SELECT min(visited_at) AS t FROM analytics_page_visits")
        row = cur.fetchone() or {}
        return ranges.local_day(row.get("t")) if row.get("t") else None
    return db.cached_value("classic_first_visit_day", 3600, compute)


def _concurrently(cur, tasks):
    """{name: fn(cursor)} for [(name, fn)], on parallel read-only
    connections (admin_metrics.db.parallel) inside the app, in order on `cur`
    otherwise (tests, scripts). A task's error is raised, as it would be
    sequentially."""
    from flask import current_app
    try:
        enabled = current_app.config.get("ADMIN_PARALLEL_SECTIONS", True)
    except RuntimeError:
        enabled = False
    if not enabled or len(tasks) < 2:
        return {name: fn(cur) for name, fn in tasks}
    from admin_metrics import cache
    got = db.parallel(tasks, workers=len(tasks),
                      timeout_ms=cache.statement_timeout_ms(db.READ_TIMEOUT_MS))
    out = {}
    for name, _fn in tasks:
        ok, value = got[name]
        if not ok:
            raise value
        out[name] = value
    return out


def visits_report(cur):
    """The "Page visits & signups" card, one row per admin-timezone day.

    `unique_visitors` is people (null before people were counted, never 0);
    `legacy_visitors` is the older browser count, kept only for days before
    3 Oct 2026 so history stays visible but is never mixed into people.
    Signups are verified customer accounts (null before the metrics epoch).
    """
    today = ranges.local_today()
    epoch = date.fromisoformat(defs.METRICS_EPOCH)
    first = first_visit_day(cur) or epoch
    first = min(first, epoch)
    tracked_from = ranges.local_day(PEOPLE_SINCE)
    whole = ranges.make_period("custom", first, today)
    p = whole.params()
    day_v = ranges.local_date_sql("visited_at", tz_aware=False)
    views_sql = f"""
        SELECT {day_v} AS day, count(*) AS views,
               count(DISTINCT COALESCE(NULLIF(device_id, ''), ip)) AS browsers
          FROM analytics_page_visits
         WHERE visited_at >= %(start)s AND visited_at < %(end)s
         GROUP BY 1"""

    def legacy_views(c):
        # Days before people were counted never change: read them once a
        # process (6 h), and only the counted days on every refresh.
        def compute():
            c.execute(views_sql, ranges.make_period(
                "custom", first, tracked_from - timedelta(days=1)).params())
            return {r["day"]: dict(r) for r in c.fetchall()}
        if first >= tracked_from:
            return {}
        return db.cached_value(
            f"classic_legacy_views:{first.isoformat()}", 6 * 3600, compute)

    def recent_views(c):
        if today < tracked_from:
            return {}
        c.execute(views_sql, ranges.make_period(
            "custom", max(first, tracked_from), today).params())
        return {r["day"]: dict(r) for r in c.fetchall()}

    def signups_by_day(c):
        day_u = ranges.local_date_sql("u.created_at", tz_aware=False)
        c.execute(f"""
            SELECT {day_u} AS day, count(*) AS n FROM users u
             WHERE {defs.customer('u')}
               AND u.created_at >= %(start)s AND u.created_at < %(end)s
             GROUP BY 1""", p)
        return {r["day"]: int(r["n"]) for r in c.fetchall()}

    def people_days(c):
        if today < tracked_from:
            return {}
        return visitors.classify_by_day(
            c, ranges.make_period("custom", tracked_from, today))

    windows = {}    # key -> (period, memo); periods whose counted part is
    seen = {}       # the same are asked once
    for key, n in (("7", 7), ("30", 30), ("90", 90), ("all", None)):
        start_day = first if n is None else today - timedelta(days=n - 1)
        period = ranges.make_period("custom", max(start_day, first), today)
        part = tracked_part(period)
        memo = (part.start, part.end) if part is not None else None
        windows[key] = (period, memo)
        seen.setdefault(memo, period)
    tasks = [("legacy", legacy_views), ("recent", recent_views),
             ("signups", signups_by_day), ("people_days", people_days)]
    memo_names = {}
    for i, (memo, period) in enumerate(seen.items()):
        memo_names[memo] = f"window{i}"
        tasks.append((f"window{i}",
                      lambda c, period=period: classify(c, period)))
    # Independent questions on parallel read-only connections: the report
    # costs its slowest query, not the sum of seven (4-5 s, 24 Oct 2026).
    got = _concurrently(cur, tasks)
    views = dict(got["legacy"])
    views.update(got["recent"])
    signups = got["signups"]
    people_by_day = got["people_days"]
    classified = {memo: got[name] for memo, name in memo_names.items()}

    rows = []
    for i in range((today - first).days + 1):
        d = first + timedelta(days=i)
        v = views.get(d) or {}
        cls = people_by_day.get(d)
        people = int(cls["person"]) if cls else None
        s = signups.get(d, 0) if d >= epoch else None
        lo = left_out(cls)
        rows.append({
            "day": d.isoformat(),
            "count": int(v.get("views") or 0),
            "unique_visitors": people,
            "legacy_visitors": (int(v.get("browsers") or 0)
                                if d < tracked_from else None),
            "left_out": lo["total"] if lo else None,
            "robots": lo["robots"] if lo else None,
            "link_checks": lo["link_checks"] if lo else None,
            "signups": s,
            "conversion_rate": (round(100.0 * s / people, 1)
                                if people and s is not None else None),
        })

    periods = {}
    for key, (period, memo) in windows.items():
        cls = classified[memo]
        in_range = [r for r in rows if r["day"] >= period.from_day.isoformat()]
        tracked_rows = [r for r in in_range if r["unique_visitors"] is not None]
        tracked_signups = sum(r["signups"] or 0 for r in tracked_rows)
        people = int(cls["person"]) if cls else None
        periods[key] = {
            "start": period.from_day.isoformat(),
            "views": sum(r["count"] for r in in_range),
            "unique_visitors": people,
            "people_from": (max(period.from_day, tracked_from).isoformat()
                            if cls else None),
            "fully_tracked": period.from_day >= tracked_from,
            "signups": sum(r["signups"] or 0 for r in in_range),
            "signups_while_tracked": tracked_signups,
            "conversion_rate": (round(100.0 * tracked_signups / people, 1)
                                if people else None),
            "left_out": left_out(cls),
            "signup_start": max(period.from_day, epoch).isoformat(),
        }
    return {
        "data": rows, "periods": periods, "totals": periods["all"],
        "timezone": ranges.TZ_NAME,
        "signup_scope_start": defs.METRICS_EPOCH,
        "people_since": tracked_from.isoformat(),
        "note": ("Visitors are people: a browser that clicked, scrolled, "
                 "opened two pages or stayed over 30 seconds. Robots, your own "
                 "browsers, Meta link previews and outreach link checks are "
                 "left out and counted separately. People are counted from "
                 f"{tracked_from.isoformat()}; earlier days show the older "
                 "browser count, which included robots and link previews. "
                 "Days run midnight to midnight, Dubai time."),
    }


def people_window(days):
    """The last `days` admin days, cut to when people were first counted."""
    today = ranges.local_today()
    return tracked_part(ranges.make_period(
        "custom", today - timedelta(days=days - 1), today))


def people_count(cur, days):
    """People over the last `days` admin days (from 3 Oct 2026 at the
    earliest), with the UTC start of the window actually counted."""
    part = people_window(days)
    if part is None:
        return None, None
    cls = visitors.classify(cur, part)
    return int(cls["person"]) if cls else None, part.start


# ── Outreach: the CRM's code → people → signups ──────────────────────────
def _campaign(raw):
    raw = (raw or "").strip()
    return "" if raw.lower() in UNTAGGED_CAMPAIGNS else raw


def outreach_codes(cur, days, limit=300):
    """Every outreach code opened (or signed up with) in the last `days`.

    One row per code: when it was first/last opened, how many PEOPLE opened
    it, how many link checks/previews, how many pages those people read, and
    the accounts that signed up through it (first-touch or latest-touch)
    with whether they paid. Codes opened only by link checks are counted in
    the totals but not listed one by one.
    """
    today = ranges.local_today()
    period = ranges.make_period("custom", today - timedelta(days=days - 1),
                                today)
    part = tracked_part(period)
    rows = outreach.coded_browsers(cur, part) if part is not None else []
    signups = outreach.coded_signups(cur, period)
    codes = {}

    def bucket(code):
        b = codes.get(code)
        if b is None:
            b = codes[code] = {"code": code, "campaign": "", "network": "",
                               "first_at": None, "last_at": None,
                               "people": set(), "link_checks": set(),
                               "other": set(), "pages_by_people": 0,
                               "signups": []}
        return b

    for r in rows:
        if not r.get("code"):
            continue
        b = bucket(r["code"])
        b["campaign"] = b["campaign"] or _campaign(r.get("campaign"))
        b["network"] = b["network"] or (r.get("network") or "")
        b["first_at"] = min(filter(None, [b["first_at"], r.get("first_at")]),
                            default=None)
        b["last_at"] = max(filter(None, [b["last_at"], r.get("last_at")]),
                           default=None)
        if r["class"] == "person":
            if r["device_id"] not in b["people"]:
                b["pages_by_people"] += int(r.get("browser_pages") or 0)
            b["people"].add(r["device_id"])
        elif r["class"] == "link_preview":
            b["link_checks"].add(r["device_id"])
        else:
            b["other"].add(r["device_id"])
    for s in signups:
        if not s.get("code"):
            continue
        b = bucket(s["code"])
        b["campaign"] = b["campaign"] or _campaign(s.get("campaign"))
        b["signups"].append({
            "id": s["id"], "email": s["email"],
            "joined_at": defs.iso(s["created_at"]),
            "paid": bool(s["paid"]), "status": s["status"],
            "collected_usd": defs.usd(s["cents"])})

    out = []
    for b in codes.values():
        out.append({
            "code": b["code"], "campaign": b["campaign"] or "untagged",
            "campaign_label": campaign_label(b["campaign"]),
            "network": (b["network"] or "instagram").title(),
            "first_opened_at": defs.iso(b["first_at"]),
            "last_opened_at": defs.iso(b["last_at"]),
            "people": len(b["people"]),
            "link_checks": len(b["link_checks"]),
            "other_loads": len(b["other"]),
            "pages_by_people": b["pages_by_people"],
            "signups": sorted(b["signups"], key=lambda s: s["joined_at"] or ""),
            "paid": sum(1 for s in b["signups"] if s["paid"]),
        })
    totals = {
        "codes_opened": len(out),
        "codes_opened_by_people": sum(1 for r in out if r["people"]),
        "codes_only_link_checks": sum(1 for r in out if not r["people"]
                                      and not r["signups"]
                                      and r["link_checks"]),
        "people": sum(r["people"] for r in out),
        "link_checks": sum(r["link_checks"] for r in out),
        "signups": sum(len(r["signups"]) for r in out),
        "paid": sum(r["paid"] for r in out),
    }
    listed = [r for r in out if r["people"] or r["signups"]]
    listed.sort(key=lambda r: (len(r["signups"]), r["paid"], r["people"],
                               r["last_opened_at"] or ""), reverse=True)
    campaigns = {}
    for r in out:
        c = campaigns.setdefault(r["campaign"], {
            "campaign": r["campaign"], "campaign_label": r["campaign_label"],
            "codes": 0, "people": 0, "link_checks": 0, "signups": 0,
            "paid": 0})
        c["codes"] += 1
        c["people"] += r["people"]
        c["link_checks"] += r["link_checks"]
        c["signups"] += len(r["signups"])
        c["paid"] += r["paid"]
    return {
        "days": days, "from": period.from_day.isoformat(),
        "people_from": (ranges.local_day(part.start).isoformat()
                        if part is not None else None),
        "codes": listed[:limit], "truncated": len(listed) > limit,
        "campaigns": sorted(campaigns.values(),
                            key=lambda c: (c["signups"], c["people"]),
                            reverse=True),
        "totals": totals,
        "note": ("A code is one recipient's link (valmera.io/?r=<code>). "
                 "People opened it and did something; link checks are the "
                 "scanners Instagram and mail run when a message is sent. "
                 "Signups count accounts whose first or latest tracked visit "
                 "carried the code. Who a code was sent to lives in the CRM."),
    }
