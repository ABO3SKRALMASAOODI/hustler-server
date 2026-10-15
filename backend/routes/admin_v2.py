"""/admin/v2: the rebuilt admin API (plan §6.4–6.6).

Every endpoint is GET, read-only, behind admin_required, time-limited (R5) and
answers `{meta, data}` or `{error: {code, message, retryable}}` — never an
HTML error page and never a 0 that is really a failure (G4). Summary-type
endpoints compute each section in its own try (R8): a failed section is
reported in meta.section_errors and marked status 'error', and the rest of
the page still renders.
"""
import dataclasses
import logging
from datetime import datetime, timedelta, timezone

import psycopg2
from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from acquisition import CHANNELS, NOT_RECORDED_REASONS
from admin_metrics import (attention as attention_mod, cache,
                           channels_report, costs as costs_mod, customers,
                           db, defs, funnel as funnel_mod, health, insights,
                           live as live_mod, money, outreach, projects,
                           ranges, registry, visitors)
from admin_metrics.ranges import RangeError
from routes.admin import admin_required, gzip_large_json

admin_v2_bp = Blueprint("admin_v2", __name__, url_prefix="/admin/v2")
admin_v2_bp.after_request(gzip_large_json)
log = logging.getLogger(__name__)

TTL = {"lifetime": 300, "meta": 3600, "summary": 60, "trend": 300, "acquisition": 300,
       "outreach": 300, "pages": 300, "funnel": 300, "cohorts": 300,
       "revenue": 300, "health": 60, "waits": 300, "attention": 60,
       "billing_problems": 60, "engagement": 300, "audience": 300,
       "exports": 120, "revenue_costs": 300, "activity": 60,
       "customers": 60, "projects": 60}

# Stale-while-revalidate (R3): an answer up to this old is served at once
# while a fresh one is computed in the background, so a page never waits on
# a report it has already seen. The page shows how old it is (generated_at).
STALE_S = 30 * 60


# ── Errors (R5) ──────────────────────────────────────────────────────────
class ApiError(Exception):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable = retryable


def _error(status, code, message, retryable=False):
    return jsonify({"error": {"code": code, "message": message,
                              "retryable": retryable}}), status


@admin_v2_bp.errorhandler(Exception)
def _handle(e):
    if isinstance(e, ApiError):
        return _error(e.status, e.code, e.message, e.retryable)
    if isinstance(e, (RangeError, projects.BadRequest)):
        return _error(400, "bad_request", str(e))
    if isinstance(e, HTTPException):
        code = "not_found" if e.code == 404 else "bad_request"
        return _error(e.code or 500, code, e.description or "Request failed.")
    if isinstance(e, psycopg2.errors.QueryCanceled):
        return _error(503, "timeout", "This report took too long. Try again, "
                      "or choose a shorter period.", True)
    if isinstance(e, psycopg2.OperationalError):
        log.warning("admin v2: database unavailable (%s)", type(e).__name__)
        return _error(503, "unavailable", "The database is busy or "
                      "restarting. Try again in a moment.", True)
    log.exception("admin v2 request failed")
    return _error(500, "internal", "The server hit an error building this "
                  "page.", True)


@admin_v2_bp.teardown_request
def _close(exc=None):
    db.close(exc)


# ── Request helpers ──────────────────────────────────────────────────────
def _args():
    return request.args


def _period(default, allow_all=False):
    return ranges.parse(_args(), default=default, allow_all=allow_all)


def _include_owner():
    v = _args().get("include_owner", "0")
    if v not in ("0", "1"):
        raise ApiError(400, "bad_request", "include_owner must be 0 or 1.")
    return v == "1"


def _fresh():
    return _args().get("fresh") == "1"


def _paging(default=50, maximum=100):
    try:
        page = int(_args().get("page", 1))
        per_page = int(_args().get("per_page", default))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_request", "page and per_page must be numbers.")
    if page < 1 or per_page < 1:
        raise ApiError(400, "bad_request", "page and per_page start at 1.")
    return page, min(per_page, maximum)


def _int_arg(name, default, lo, hi):
    try:
        v = int(_args().get(name, default))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_request", f"{name} must be a number.")
    if v < lo or v > hi:
        raise ApiError(400, "bad_request", f"{name} must be {lo}–{hi}.")
    return v


def _scope_meta(include_owner):
    epoch = datetime.strptime(defs.METRICS_EPOCH, "%Y-%m-%d").date()
    since = f"{epoch.day} {ranges.MONTHS[epoch.month - 1]} {epoch.year}"
    tests = defs.TEST_ACCOUNTS
    if include_owner:
        label = f"Customers since {since} plus your account"
    elif tests:
        label = (f"Customers since {since} · your account and {tests} test "
                 f"account{'s' if tests != 1 else ''} excluded")
    else:
        label = f"Customers since {since} · your account excluded"
    return {"label": label, "customers_since": defs.METRICS_EPOCH,
            "excluded_accounts": len(defs.EXCLUDE_EMAILS),
            "include_owner": include_owner}


def _tracking_meta(cur=None):
    return db.cached_value("tracking_meta", 300,
                           lambda: _compute_tracking_meta(cur or db.cursor()))


def _compute_tracking_meta(cur):
    def interaction():
        if not db.has_column(cur, "page_visits", "interacted"):
            return None
        cur.execute("""SELECT min(visited_at) AS t FROM page_visits
                        WHERE visited_at >= %s AND interacted""",
                    (ranges.naive(defs.VISITS_SINCE),))
        return (cur.fetchone() or {}).get("t")
    out = {"visits_since": defs.iso(defs.VISITS_SINCE),
           "sources_since": defs.iso(defs.SOURCES_SINCE),
           "signup_link_since": defs.iso(defs.SIGNUP_LINK_SINCE),
           "interaction_since": None, "reasons_since": None,
           "snapshots_since": None}
    try:
        out["interaction_since"] = defs.iso(
            db.cached_value("interaction_since", 600, interaction))
        out["reasons_since"] = defs.iso(channels_report.reasons_since(cur))
        snap = money.snapshots_since(cur)
        out["snapshots_since"] = snap.isoformat() if snap else None
    except psycopg2.Error:
        pass
    return out


def _meta(period, include_owner=False, errors=None, computed_at=None,
          cache_age=0, cur=None):
    now = datetime.now(timezone.utc)
    return {
        "generated_at": defs.iso(computed_at or now),
        "cache_age_s": int(cache_age or 0),
        "timezone": ranges.TZ_NAME,
        "utc_offset": ranges.utc_offset(now),
        "range": period.meta() if period is not None else None,
        "compare": period.compare_meta() if period is not None else None,
        "scope": _scope_meta(include_owner),
        "tracking": _tracking_meta(cur),
        "section_errors": list(errors or []),
    }


def _respond(data, period=None, include_owner=False, errors=None,
             computed_at=None, cache_age=0):
    return jsonify({"meta": _meta(period, include_owner, errors, computed_at,
                                  cache_age),
                    "data": data})


def _cached(name, params, compute, swr=True):
    """Run compute() through the in-process cache unless it reported errors.

    swr: serve an expired answer at once and refresh it in the background.
    compute() must then not read the request (it may run after it ended)."""
    value, computed_at, age = cache.get_or_compute(
        name, params, TTL.get(name, 0), compute, fresh=_fresh(),
        stale_s=STALE_S if swr else 0,
        keep=lambda v: not v.get("errors"))
    if value.get("errors"):
        cache.clear_key(name, params)
    return value, computed_at, age


class Sections:
    """R8: each section in its own try; failures become section_errors."""

    def __init__(self):
        self.errors = []
        self.results = {}

    def fail(self, name, e):
        if isinstance(e, psycopg2.errors.QueryCanceled):
            msg = "This part took too long and was skipped."
        elif isinstance(e, psycopg2.Error):
            msg = "The database couldn't answer this part."
        else:
            msg = "This part couldn't be computed."
        log.warning("admin v2 section %s failed: %s", name, type(e).__name__)
        self.errors.append({"section": name, "message": msg})

    def run(self, name, fn, default=None):
        try:
            return fn()
        except Exception as e:
            self.fail(name, e)
            return default

    def parallel(self, tasks):
        """Run [(name, fn(cur))] at once on their own connections (R10)."""
        self.results = db.parallel(tasks)
        return self.results

    def get(self, name, default=None):
        """A parallel task's value, or `default` (recorded as an error)."""
        ok, value = self.results.get(name, (False, RuntimeError("not run")))
        if ok:
            return value
        self.fail(name, value)
        return default


def _href(base, period, extra=""):
    q = f"range={period.key}"
    if period.key == "custom":
        q += f"&from={period.from_day}&to={period.to_day}"
    return f"{base}?{q}{extra}"


def _spark(series, days):
    return [{"date": d.isoformat(), "value": series.get(d)} for d in days]


# ── Shared counts ────────────────────────────────────────────────────────
def signups_count(cur, period):
    cur.execute(f"""SELECT count(*) AS n FROM users u
                     WHERE {defs.customer('u')}
                       AND u.created_at >= %(start)s AND u.created_at < %(end)s""",
                period.params())
    return int(cur.fetchone()["n"])


def signups_by_day(cur, period):
    day = ranges.local_date_sql("u.created_at", False)
    cur.execute(f"""SELECT {day} AS day, count(*) AS n FROM users u
                     WHERE {defs.customer('u')}
                       AND u.created_at >= %(start)s AND u.created_at < %(end)s
                     GROUP BY 1""", period.params())
    return {r["day"]: int(r["n"]) for r in cur.fetchall()}


ACTIVE_UNION = insights.ACTIVE_UNION
_active_sql = insights.active_sql


def active_customers(cur, period):
    cur.execute(f"""SELECT count(DISTINCT a.user_id) AS n
                      FROM ({_active_sql(False)}) a
                      JOIN users u ON u.id = a.user_id
                     WHERE {defs.customer('u')}""", period.params())
    return int(cur.fetchone()["n"])


def active_by_day(cur, period):
    cur.execute(f"""SELECT a.day, count(DISTINCT a.user_id) AS n
                      FROM ({_active_sql(True)}) a
                      JOIN users u ON u.id = a.user_id
                     WHERE {defs.customer('u')} GROUP BY 1""", period.params())
    return {r["day"]: int(r["n"]) for r in cur.fetchall()}


def exports_count(cur, period):
    cur.execute(f"""SELECT count(*) AS n, count(DISTINCT vj.user_id) AS people
                      FROM video_jobs vj JOIN users u ON u.id = vj.user_id
                     WHERE vj.type = 'final' AND vj.state = 'done'
                       AND vj.updated_at >= %(start_tz)s
                       AND vj.updated_at < %(end_tz)s
                       AND {defs.customer('u')}""", period.params())
    r = cur.fetchone()
    return int(r["n"]), int(r["people"])


def exports_by_day(cur, period):
    day = ranges.local_date_sql("vj.updated_at", True)
    cur.execute(f"""SELECT {day} AS day, count(*) AS n
                      FROM video_jobs vj JOIN users u ON u.id = vj.user_id
                     WHERE vj.type = 'final' AND vj.state = 'done'
                       AND vj.updated_at >= %(start_tz)s
                       AND vj.updated_at < %(end_tz)s
                       AND {defs.customer('u')} GROUP BY 1""", period.params())
    return {r["day"]: int(r["n"]) for r in cur.fetchall()}


def _visitor_status(period):
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return "unavailable", "Visitors are counted from 3 Oct 2026."
    if period.start is not None and period.start < defs.VISITS_SINCE:
        return "partial", "Visitors are counted from 3 Oct 2026; earlier days " \
                          "are not included."
    return "ok", None


def _tracked_part(period):
    """The part of `period` since visits were tracked (None if none)."""
    if period.end is not None and period.end <= defs.VISITS_SINCE:
        return None
    if period.start is not None and period.start >= defs.VISITS_SINCE:
        return period
    return dataclasses.replace(period, start=defs.VISITS_SINCE)


def _signed_in_known(period):
    """Visits say whether the person was signed in only since the tracker
    release that also sends `interacted` (migration 031 + frontend). Before
    that every row reads FALSE, which is unknown, not zero (G4)."""
    since = (_tracking_meta() or {}).get("interaction_since")
    if not since or period.start is None:
        return False
    try:
        at = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    except ValueError:
        return False
    return period.start >= at


def people_metric(cur, period, with_spark=True, href=True):
    status, note = _visitor_status(period)
    cls = visitors.classify(cur, period)
    comp = period.compare()
    prev = visitors.classify(cur, comp) if comp is not None else None
    breakdown = []
    if cls is not None:
        breakdown = [registry.item("link_previews", cls["link_preview"]),
                     registry.item("robots", cls["robot"]),
                     registry.item("no_signal_loads", cls["no_signal"]),
                     registry.item("internal", cls["internal"]),
                     registry.item("returning_customers",
                                   cls["signed_in"]
                                   if _signed_in_known(period) else None)]
    spark = []
    if with_spark and period.to_day:
        sp = ranges.trailing_days(14, period.to_day)
        by_day = visitors.classify_by_day(cur, sp)
        spark = [{"date": d.isoformat(),
                  "value": (by_day.get(d) or {}).get("person")
                  if by_day.get(d) is not None else None}
                 for d in sp.day_list()]
    comparable = prev is not None and _visitor_status(comp)[0] == "ok"
    return registry.metric(
        "people", cls["person"] if cls else None,
        prev["person"] if (prev and comparable) else None,
        status=status if cls else "unavailable", note=note,
        breakdown=breakdown, spark=spark,
        href=_href("/admin/growth", period) if href else None), cls, prev


# ── 1. meta ──────────────────────────────────────────────────────────────
@admin_v2_bp.route("/meta", methods=["GET"])
@admin_required
def meta():
    import os
    data = {
        "revision": (os.environ.get("RENDER_GIT_COMMIT") or None),
        "server_time": defs.iso(datetime.now(timezone.utc)),
        "timezone": ranges.TZ_NAME, "utc_offset": ranges.utc_offset(),
        "ranges": [{"key": k, "label": ranges.RANGE_LABELS[k]}
                   for k in ranges.RANGE_KEYS],
        "channels": [{"key": k, "label": label, "order": i + 1}
                     for i, (k, label) in enumerate(CHANNELS)],
        "not_recorded_reasons": [{"key": k, "label": label}
                                 for k, label in NOT_RECORDED_REASONS],
        "definitions": registry.definitions(),
    }
    return _respond(data)


# ── 2. summary (Today) ───────────────────────────────────────────────────
SUMMARY_ORDER = ("people", "signups", "signup_rate", "first_upload",
                 "first_edit", "first_export", "new_paying", "paying_now",
                 "mrr", "cash", "exports", "active_customers")


def _spark_of(days, series, default=0):
    return [{"date": d.isoformat(), "value": series.get(d, default)}
            for d in days]


@admin_v2_bp.route("/summary", methods=["GET"])
@admin_required
def summary():
    period = _period("today")

    def compute():
        s = Sections()
        comp = period.compare()
        spark_period = ranges.trailing_days(14, period.to_day)
        days = spark_period.day_list()
        _tracking_meta()            # warm before the sections fan out
        partial = _visitor_status(period)[0] == "partial"

        def t_people(cur):
            return people_metric(cur, period, with_spark=False)

        def t_people_spark(cur):
            return visitors.classify_by_day(cur, spark_period)

        def t_signups(cur):
            return {"now": signups_count(cur, period),
                    "prev": signups_count(cur, comp),
                    "spark": signups_by_day(cur, spark_period),
                    "tracked": signups_count(cur, _tracked_part(period))
                    if partial else None}

        def t_activation(cur):
            def href(kind):
                return _href("/admin/customers", period,
                             f"&date_field=first_{kind}")
            return insights.activation_metrics(cur, period, href)

        def t_paying(cur):
            return {"new": money.new_paying(cur, period),
                    "new_prev": money.new_paying(cur, comp),
                    "new_spark": money.new_paying_by_day(cur, spark_period),
                    "mrr": money.mrr_and_paying(cur),
                    "mrr_prev": money.mrr_at(cur, comp.to_day)
                    if comp else None,
                    "mrr_hist": money.mrr_history(cur, spark_period)}

        def t_cash(cur):
            return {"now": money.cash(cur, period),
                    "prev": money.cash(cur, comp),
                    "spark": money.cash_by_day(cur, spark_period)}

        def t_active(cur):
            return {"now": active_customers(cur, period),
                    "prev": active_customers(cur, comp),
                    "spark": active_by_day(cur, spark_period)}

        def t_exports(cur):
            n, people_n = exports_count(cur, period)
            pn, _ = exports_count(cur, comp)
            return {"now": n, "people": people_n, "prev": pn,
                    "spark": exports_by_day(cur, spark_period)}

        def t_health_jobs(cur):
            return (health.failures(cur, period, recent_limit=0),
                    health.uploads(cur, period, recent_limit=0))

        def t_health_edits(cur):
            return (health.messages_without_edit(cur, period, recent_limit=0),
                    health.engine(cur))

        def t_health_pay(cur):
            return (money.status_counts(cur)["payment_failing"],
                    money.failed_payments(cur, period)["payments"])

        def t_channels(cur):
            return channels_report.summary_channels(cur, period)

        s.parallel([("people", t_people), ("people_spark", t_people_spark),
                    ("activation", t_activation), ("paying", t_paying),
                    ("signups", t_signups), ("active_customers", t_active),
                    ("exports", t_exports), ("cash", t_cash),
                    ("health_jobs", t_health_jobs),
                    ("health_edits", t_health_edits),
                    ("health_pay", t_health_pay), ("channels", t_channels)])
        kpis = {}

        people = s.get("people", None)
        if people is None:
            kpis["people"] = registry.error_metric("people")
        else:
            kpis["people"] = people[0]
            by_day = s.get("people_spark", None)
            if by_day is not None and period.to_day:
                kpis["people"]["spark"] = [
                    {"date": d.isoformat(),
                     "value": (by_day.get(d) or {}).get("person")
                     if by_day.get(d) is not None else None} for d in days]

        sg = s.get("signups", None)
        kpis["signups"] = registry.metric(
            "signups", sg["now"], sg["prev"],
            spark=_spark_of(days, sg["spark"]),
            href=_href("/admin/customers", period, "&date_field=joined")) \
            if sg else registry.error_metric("signups")

        def rate_kpi():
            pp = kpis["people"]
            if sg is None or pp["status"] == "error":
                return registry.error_metric("signup_rate")
            status, note = _visitor_status(period)
            # People are only counted since visits were tracked, so the
            # signups on top of the fraction must cover the same time. Over
            # a period that starts earlier the rate is computed over the
            # tracked part only (never 357%).
            value, prev = None, None
            if status == "partial":
                value = defs.pct(sg["tracked"], pp["value"]) \
                    if pp["value"] else None
                note = ("Signups since 3 Oct ÷ people since 3 Oct: visitors "
                        "are counted from 3 Oct 2026, so earlier signups are "
                        "left out of this rate.")
            elif status == "ok":
                value = defs.pct(sg["now"], pp["value"]) \
                    if pp["value"] else None
                prev = defs.pct(sg["prev"], pp["previous"]) \
                    if pp.get("previous") else None
            if value is None and status == "ok":
                status, note = "unavailable", "No people visited yet."
            return registry.metric("signup_rate", value, prev, status=status,
                                   note=note,
                                   href=_href("/admin/funnel", period))
        kpis["signup_rate"] = s.run("signup_rate", rate_kpi,
                                    registry.error_metric("signup_rate"))

        act = s.get("activation", None)
        for key in ("first_upload", "first_edit", "first_export"):
            kpis[key] = act[key] if act else registry.error_metric(key)

        pay = s.get("paying", None)
        if pay:
            kpis["new_paying"] = registry.metric(
                "new_paying", pay["new"], pay["new_prev"],
                spark=_spark_of(days, pay["new_spark"]),
                href=_href("/admin/customers", period,
                           "&date_field=first_paid"))
            mrr, paying_n, _ = pay["mrr"]
            spark = []
            hist = pay["mrr_hist"]
            if hist["available"]:
                by = {r["date"]: r["mrr_usd"] for r in hist["rows"]}
                spark = [{"date": d.isoformat(), "value": by.get(d.isoformat())}
                         for d in days]
            kpis["mrr"] = registry.metric(
                "mrr", mrr, pay["mrr_prev"],
                note=(None if pay["mrr_prev"] is not None else
                      "No comparison yet: MRR history starts when daily "
                      "billing snapshots begin."),
                breakdown=[registry.item("paying_now", paying_n)],
                spark=spark, href="/admin/revenue")
            kpis["paying_now"] = registry.metric(
                "paying_now", paying_n, comparable=False,
                href="/admin/customers?range=all&filter=paying")
        else:
            for key in ("new_paying", "mrr", "paying_now"):
                kpis[key] = registry.error_metric(key)

        c = s.get("cash", None)
        kpis["cash"] = registry.metric(
            "cash", c["now"]["usd"], c["prev"]["usd"],
            note=(f"{c['now']['non_usd']} payments in other currencies are "
                  "not in this total." if c["now"]["non_usd"] else None),
            breakdown=[registry.item("payments", c["now"]["payments"]),
                       registry.item("non_usd_payments", c["now"]["non_usd"])],
            spark=[{"date": d.isoformat(),
                    "value": (c["spark"].get(d) or (0.0, 0))[0]}
                   for d in days],
            href=_href("/admin/revenue", period) + "#payments") \
            if c else registry.error_metric("cash")

        a = s.get("active_customers", None)
        kpis["active_customers"] = registry.metric(
            "active_customers", a["now"], a["prev"],
            spark=_spark_of(days, a["spark"]),
            href=_href("/admin/customers", period, "&date_field=active")) \
            if a else registry.error_metric("active_customers")

        e = s.get("exports", None)
        kpis["exports"] = registry.metric(
            "exports", e["now"], e["prev"],
            breakdown=[registry.item("exporters", e["people"])],
            spark=_spark_of(days, e["spark"]),
            href=_href("/admin/exports", period)) \
            if e else registry.error_metric("exports")

        hj = s.get("health_jobs", None)
        he = s.get("health_edits", None)
        hp = s.get("health_pay", None)
        items = health.health_items_from(period, hj[0], hj[1], hp[0], hp[1],
                                         he[0], he[1]) \
            if (hj and he and hp) else []
        if items == [] and not any(e["section"] == "health"
                                   for e in s.errors):
            s.errors.append({"section": "health", "message":
                             "This part couldn't be computed."})
        channels = s.get("channels", None)
        return {"data": {"kpis": [kpis[k] for k in SUMMARY_ORDER],
                         "health": items, "channels": channels},
                "errors": s.errors}

    value, computed_at, age = _cached("summary", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# Lifetime totals stay independent of the dashboard's selected day.
@admin_v2_bp.route("/lifetime", methods=["GET"])
@admin_required
def lifetime():
    period = ranges.make_period("all", datetime.fromisoformat(defs.METRICS_EPOCH).date(), ranges.local_today())

    def compute():
        sections = Sections()
        sections.parallel([
            ("signups", lambda cur: signups_by_day(cur, period)),
            ("visitors", lambda cur: visitors.classify(cur, period)),
            ("visitors_daily", lambda cur: visitors.classify_by_day(cur, period)),
        ])
        signups = sections.get("signups")
        people = sections.get("visitors")
        daily = sections.get("visitors_daily")
        running = 0
        rows = []
        for day in period.day_list():
            n = signups.get(day, 0) if signups is not None else None
            if n is not None:
                running += n
            v = daily.get(day) if daily is not None else None
            rows.append({"date": day.isoformat(), "signups": n,
                         "signups_total": running if n is not None else None,
                         "people": v["person"] if v else None})
        return {"data": {"signups": running if signups is not None else None,
                         "unique_visitors": people["person"] if people else None,
                         "rows": rows}, "errors": sections.errors}

    value, computed_at, age = _cached("lifetime", {}, compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# ── 3. trend ─────────────────────────────────────────────────────────────
TREND_METRICS = ("people", "signups", "new_paying", "cash", "link_previews",
                 "exports", "active")


def _weekly(rows, keys):
    """Sum daily rows into Monday weeks; a week with no known value is None."""
    weeks, order = {}, []
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        monday = (d - timedelta(days=d.weekday())).isoformat()
        if monday not in weeks:
            weeks[monday] = {"date": monday, **{k: [] for k in keys}}
            order.append(monday)
        for k in keys:
            if r[k] is not None:
                weeks[monday][k].append(r[k])
    return [{"date": m, **{k: (round(sum(weeks[m][k]), 2) if weeks[m][k]
                               else None) for k in keys}} for m in order]


def _avg7(values):
    out = []
    for i in range(len(values)):
        window = values[max(0, i - 6): i + 1]
        if len(window) < 7 or any(v is None for v in window):
            out.append(None)
        else:
            out.append(round(sum(window) / 7.0, 2))
    return out


@admin_v2_bp.route("/trend", methods=["GET"])
@admin_required
def trend():
    period = _period("30d")
    wanted = [m.strip() for m in (_args().get("metrics") or "").split(",")
              if m.strip()] or list(TREND_METRICS)
    if any(m not in TREND_METRICS for m in wanted):
        raise ApiError(400, "bad_request", "metrics must be from: "
                       + ", ".join(TREND_METRICS) + ".")

    def compute():
        s = Sections()
        # Six extra days so the first point has a full 7-day average.
        wide = ranges.make_period("custom", period.from_day - timedelta(days=6),
                                  period.to_day)
        days = wide.day_list()
        # A series the page didn't ask for is null ("not computed"), never 0.
        tasks = []
        if "people" in wanted or "link_previews" in wanted:
            tasks.append(("people",
                          lambda cur: visitors.classify_by_day(cur, wide)))
        if "signups" in wanted:
            tasks.append(("signups", lambda cur: signups_by_day(cur, wide)))
        if "new_paying" in wanted:
            tasks.append(("new_paying",
                          lambda cur: money.new_paying_by_day(cur, wide)))
        if "cash" in wanted:
            tasks.append(("cash", lambda cur: money.cash_by_day(cur, wide)))
        if "exports" in wanted:
            tasks.append(("exports", lambda cur: exports_by_day(cur, wide)))
        if "active" in wanted:
            tasks.append(("active", lambda cur: active_by_day(cur, wide)))
        s.parallel(tasks)
        asked = {name for name, _ in tasks}
        people = s.get("people", {}) if "people" in asked else {}
        signups = s.get("signups") if "signups" in asked else None
        paying = s.get("new_paying") if "new_paying" in asked else None
        cash = s.get("cash") if "cash" in asked else None
        exports = s.get("exports") if "exports" in asked else None
        active = s.get("active") if "active" in asked else None
        rows = []
        for d in days:
            pd = people.get(d) if people is not None else None
            rows.append({
                "date": d.isoformat(),
                "people": pd["person"] if pd else None,
                "link_previews": pd["link_preview"] if pd else None,
                "signups": None if signups is None else signups.get(d, 0),
                "new_paying": None if paying is None else paying.get(d, 0),
                "cash_usd": None if cash is None else
                (cash.get(d) or (0.0, 0))[0],
                "exports": None if exports is None else exports.get(d, 0),
                "active": None if active is None else active.get(d, 0),
            })
        avg = {"people_7d": _avg7([r["people"] for r in rows]),
               "signups_7d": _avg7([r["signups"] for r in rows]),
               "cash_7d": _avg7([r["cash_usd"] for r in rows])}
        rows, avg = rows[6:], {k: v[6:] for k, v in avg.items()}
        grain = "day"
        if period.days and period.days > 120:
            grain = "week"
            rows = _weekly(rows, ("people", "link_previews", "signups",
                                  "new_paying", "cash_usd", "exports"))
            avg = {k: [None] * len(rows) for k in avg}
        return {"data": {"grain": grain, "rows": rows, "averages": avg},
                "errors": s.errors}

    value, computed_at, age = _cached("trend", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# ── 4. attention ─────────────────────────────────────────────────────────
@admin_v2_bp.route("/attention", methods=["GET"])
@admin_required
def attention():
    limit = _int_arg("limit", 50, 1, 200)
    only = _args().get("type") or None
    if only and only not in attention_mod.TYPES:
        raise ApiError(400, "bad_request", "Unknown attention type.")

    def compute():
        errors = []
        data = attention_mod.attention(db.cursor(), limit, only, errors)
        return {"data": data, "errors": errors}

    value, computed_at, age = _cached("attention", dict(_args()), compute)
    return _respond(value["data"], errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# ── 5–8. acquisition ─────────────────────────────────────────────────────
@admin_v2_bp.route("/acquisition", methods=["GET"])
@admin_required
def acquisition():
    period = _period("30d")
    model = _args().get("model", "first")
    if model not in ("first", "last"):
        raise ApiError(400, "bad_request", "model must be first or last.")

    def compute():
        return {"data": channels_report.acquisition_report(
            db.cursor(), period, model), "errors": []}

    value, computed_at, age = _cached("acquisition", dict(_args()), compute)
    return _respond(value["data"], period, computed_at=computed_at,
                    cache_age=age)


@admin_v2_bp.route("/acquisition/outreach", methods=["GET"])
@admin_required
def acquisition_outreach():
    period = _period("30d")

    def compute():
        return {"data": outreach.campaigns(db.cursor(), period), "errors": []}

    value, computed_at, age = _cached("outreach", dict(_args()), compute)
    return _respond(value["data"], period, computed_at=computed_at,
                    cache_age=age)


@admin_v2_bp.route("/acquisition/outreach/code", methods=["GET"])
@admin_required
def acquisition_outreach_code():
    code = (_args().get("code") or "").strip()
    try:
        data = outreach.code_lookup(db.cursor(), code)
    except ValueError as e:
        raise ApiError(400, "bad_request", str(e))
    if data is None:
        raise ApiError(404, "not_found", "This link was never opened and "
                       "nobody signed up with it.")
    return _respond(data)


@admin_v2_bp.route("/acquisition/pages", methods=["GET"])
@admin_required
def acquisition_pages():
    period = _period("30d")
    limit = _int_arg("limit", 30, 1, 100)

    def compute():
        return {"data": channels_report.landing_pages(db.cursor(), period,
                                                      limit),
                "errors": []}

    value, computed_at, age = _cached("pages", dict(_args()), compute)
    return _respond(value["data"], period, computed_at=computed_at,
                    cache_age=age)


# ── 9–10. funnel ─────────────────────────────────────────────────────────
@admin_v2_bp.route("/funnel", methods=["GET"])
@admin_required
def funnel():
    period = _period("30d")

    def compute():
        cur = db.cursor()
        s = Sections()
        people = s.run("people", lambda: people_metric(
            cur, period, with_spark=False)[0], registry.error_metric("people"))
        people = dict(people, previous=None)
        data = funnel_mod.funnel(cur, period, people)
        return {"data": data, "errors": s.errors}

    value, computed_at, age = _cached("funnel", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/funnel/cohorts", methods=["GET"])
@admin_required
def funnel_cohorts():
    weeks = _int_arg("weeks", 12, 1, 52)

    def compute():
        return {"data": funnel_mod.cohorts(db.cursor(), weeks), "errors": []}

    value, computed_at, age = _cached("cohorts", {"weeks": weeks}, compute)
    return _respond(value["data"], computed_at=computed_at, cache_age=age)


# ── 11–12. customers ─────────────────────────────────────────────────────
@admin_v2_bp.route("/customers", methods=["GET"])
@admin_required
def customers_list():
    period = _period("all", allow_all=True)
    page, per_page = _paging()
    args = dict(_args())

    def compute():
        return {"data": customers.list_customers(db.cursor(), period, args,
                                                 page, per_page),
                "errors": []}

    # Short-lived and served stale while it refreshes: the list has no
    # expiring links, so a returning visit paints at once.
    value, computed_at, age = _cached("customers", args, compute)
    return _respond(value["data"], period, computed_at=computed_at,
                    cache_age=age)


@admin_v2_bp.route("/customers/<int:user_id>", methods=["GET"])
@admin_required
def customer_detail(user_id):
    from routes.admin_video import _presign
    data = customers.customer_detail(db.cursor(), user_id, _presign)
    if data is None:
        raise ApiError(404, "not_found", "No account with this id.")
    return _respond(data)


# ── 13–21. projects ──────────────────────────────────────────────────────
@admin_v2_bp.route("/projects", methods=["GET"])
@admin_required
def projects_list():
    period = _period("all", allow_all=True)
    include_owner = _include_owner()
    page, per_page = _paging()
    from routes.admin_video import _presign
    args = dict(_args())

    def compute():
        return {"data": projects.list_projects(db.cursor(), period, args, page,
                                               per_page, _presign),
                "errors": []}

    # Thumbnails are 15-minute links: cached briefly, never served stale.
    value, computed_at, age = _cached("projects", args, compute, swr=False)
    return _respond(value["data"], period, include_owner=include_owner,
                    computed_at=computed_at, cache_age=age)


def _project_or_404(cur, project_id):
    found, session = projects.project_session(cur, project_id)
    if not found:
        raise ApiError(404, "not_found", "No project with this id.")
    return session


@admin_v2_bp.route("/projects/<int:project_id>", methods=["GET"])
@admin_required
def project_detail(project_id):
    cur = db.cursor()
    db.set_timeout(cur, 15000)
    data = projects.project_detail(cur, project_id)
    if data is None:
        raise ApiError(404, "not_found", "No project with this id.")
    return _respond(data)


@admin_v2_bp.route("/projects/<int:project_id>/messages", methods=["GET"])
@admin_required
def project_messages(project_id):
    cur = db.cursor()
    session = _project_or_404(cur, project_id)
    before = _args().get("before_id")
    if before not in (None, "") and not str(before).isdigit():
        raise ApiError(400, "bad_request", "before_id must be a number.")
    limit = _int_arg("limit", 300, 1, 500)
    return _respond(projects.messages_page(cur, session, before or None,
                                           limit))


@admin_v2_bp.route("/projects/<int:project_id>/jobs/<int:job_id>",
                   methods=["GET"])
@admin_required
def project_job(project_id, job_id):
    data = projects.job_detail(db.cursor(), project_id, job_id)
    if data is None:
        raise ApiError(404, "not_found", "No such job in this project.")
    return _respond(data)


@admin_v2_bp.route("/projects/<int:project_id>/versions/<int:version>",
                   methods=["GET"])
@admin_required
def project_version(project_id, version):
    data = projects.version_detail(db.cursor(), project_id, version,
                                   _args().get("with_previous") == "1")
    if data is None:
        raise ApiError(404, "not_found", "No such version in this project.")
    return _respond(data)


@admin_v2_bp.route("/projects/<int:project_id>/assets", methods=["GET"])
@admin_required
def project_assets(project_id):
    cur = db.cursor()
    _project_or_404(cur, project_id)
    from routes.admin_video import _presign
    return _respond(projects.assets(cur, project_id, _presign))


@admin_v2_bp.route("/projects/<int:project_id>/index", methods=["GET"])
@admin_required
def project_index(project_id):
    cur = db.cursor()
    _project_or_404(cur, project_id)
    return _respond(projects.index_detail(cur, project_id))


@admin_v2_bp.route("/projects/<int:project_id>/llm_calls", methods=["GET"])
@admin_required
def project_llm_calls(project_id):
    cur = db.cursor()
    _project_or_404(cur, project_id)
    page, per_page = _paging(default=20, maximum=50)
    return _respond(projects.llm_calls(cur, project_id, page, per_page))


@admin_v2_bp.route("/projects/<int:project_id>/llm_calls/<int:call_id>",
                   methods=["GET"])
@admin_required
def project_llm_call(project_id, call_id):
    data = projects.llm_call(db.cursor(), project_id, call_id)
    if data is None:
        raise ApiError(404, "not_found", "No such model call in this project.")
    return _respond(data)


@admin_v2_bp.route("/projects/<int:project_id>/tool_outcomes",
                   methods=["GET"])
@admin_required
def project_tool_outcomes(project_id):
    cur = db.cursor()
    _project_or_404(cur, project_id)
    return _respond(projects.tool_outcomes(cur, project_id))


# ── 22–24. revenue ───────────────────────────────────────────────────────
def costs_report(fresh=False):
    """The shared 10-minute costs report (same numbers as /admin/video/costs)."""
    def compute():
        conn = db.open_readonly(db.HEAVY_TIMEOUT_MS)
        try:
            return costs_mod.compute(conn.cursor())
        finally:
            conn.close()
    return cache.shared_report("/admin/video/costs", {}, compute, fresh=fresh,
                               logger=current_app.logger)


@admin_v2_bp.route("/revenue", methods=["GET"])
@admin_required
def revenue():
    period = _period("30d")

    def compute():
        s = Sections()
        comp = period.compare()

        def t_mrr(cur):
            mrr, paying, groups = money.mrr_and_paying(cur)
            prev = money.mrr_at(cur, comp.to_day) if comp else None
            return mrr, paying, groups, prev

        def t_stopped(cur):
            v = money.stopped_paying(cur, period)
            if v is None:
                since = money.snapshots_since(cur)
                return registry.metric(
                    "stopped_paying", None, status="unavailable",
                    note=("Counted from daily billing snapshots, which start "
                          + (f"on {ranges.day_label(since)}." if since else
                             "with this release's database update. The first "
                             "number appears the day after.")))
            return registry.metric("stopped_paying", v,
                                   money.stopped_paying(cur, comp))

        def t_cash_series(cur):
            if period.days and period.days <= 31:
                wide = ranges.make_period(
                    "custom", period.from_day - timedelta(days=6),
                    period.to_day)
                wb = money.cash_by_day(cur, wide)
                rows = [{"date": d.isoformat(),
                         "usd": (wb.get(d) or (0.0, 0))[0],
                         "payments": (wb.get(d) or (0.0, 0))[1]}
                        for d in period.day_list()]
                vals = [(wb.get(d) or (0.0, 0))[0] for d in wide.day_list()]
                return {"grain": "day", "rows": rows,
                        "average": _avg7(vals)[6:]}
            by = money.cash_by_day(cur, period)
            weeks = {}
            for d in period.day_list():
                monday = d - timedelta(days=d.weekday())
                w = weeks.setdefault(monday, [0.0, 0])
                v = by.get(d) or (0.0, 0)
                w[0] = round(w[0] + v[0], 2)
                w[1] += v[1]
            rows = [{"date": m.isoformat(), "usd": v[0], "payments": v[1]}
                    for m, v in sorted(weeks.items())]
            vals = [r["usd"] for r in rows]
            avg = [round(sum(vals[max(0, i - 3): i + 1]) / 4.0, 2)
                   if i >= 3 else None for i in range(len(vals))]
            return {"grain": "week", "rows": rows, "average": avg}

        def t_by_plan(cur):
            mrr_, _paying, groups_ = money.mrr_and_paying(cur)
            return money.by_plan(cur, groups_, mrr_)

        def t_ledger(cur):
            out = money.ledger(cur)
            out["failed_in_range"] = money.failed_payments(cur, period)
            return out

        s.parallel([
            ("statuses", money.status_counts),
            ("mrr", t_mrr),
            ("cash", lambda cur: (money.cash(cur, period),
                                  money.cash(cur, comp))),
            ("new_paying", lambda cur: (money.new_paying(cur, period),
                                        money.new_paying(cur, comp))),
            ("stopped_paying", t_stopped),
            ("cash_series", t_cash_series),
            ("mrr_history", lambda cur: money.mrr_history(cur, period)),
            ("by_plan", t_by_plan),
            ("ledger", t_ledger),
        ])
        tiles = {}
        statuses = s.get("statuses", None)
        mrr_info = s.get("mrr", None)
        if mrr_info:
            mrr, paying, _groups, prev = mrr_info
            tiles["mrr"] = registry.metric(
                "mrr", mrr, prev, href="/admin/revenue",
                note=None if prev is not None else
                "No comparison yet: MRR history starts when daily billing "
                "snapshots begin.")
            tiles["paying_now"] = registry.metric(
                "paying_now", paying, comparable=False,
                href="/admin/customers?range=all&filter=paying")
        else:
            mrr, paying = None, None
            tiles["mrr"] = registry.error_metric("mrr")
            tiles["paying_now"] = registry.error_metric("paying_now")
        cash = s.get("cash", None)
        tiles["cash"] = registry.metric(
            "cash", cash[0]["usd"], cash[1]["usd"],
            breakdown=[registry.item("payments", cash[0]["payments"]),
                       registry.item("non_usd_payments", cash[0]["non_usd"])],
            href=_href("/admin/revenue", period) + "#payments") \
            if cash else registry.error_metric("cash")
        np_ = s.get("new_paying", None)
        tiles["new_paying"] = registry.metric(
            "new_paying", np_[0], np_[1],
            href=_href("/admin/customers", period, "&date_field=first_paid")) \
            if np_ else registry.error_metric("new_paying")
        tiles["stopped_paying"] = s.get(
            "stopped_paying", registry.error_metric("stopped_paying"))
        customers_n = sum(statuses.get(k, 0) for k in (
            "paying", "payment_failing", "canceled", "free")) \
            if statuses else 0
        tiles["payment_failing"] = (
            registry.metric("payment_failing", statuses["payment_failing"],
                            comparable=False,
                            href="/admin/customers?range=all"
                                 "&filter=payment_failing")
            if statuses else registry.error_metric("payment_failing"))

        data = {
            "tiles": [tiles[k] for k in ("mrr", "paying_now", "cash",
                                         "new_paying", "stopped_paying",
                                         "payment_failing")],
            "avg_plan_value": (registry.metric(
                "avg_plan_value", round(mrr / paying, 2) if paying else None,
                comparable=False) if mrr_info else
                registry.error_metric("avg_plan_value")),
            "arpu": (registry.metric(
                "arpu", round(mrr / customers_n, 2) if customers_n else None,
                comparable=False) if (mrr_info and statuses) else
                registry.error_metric("arpu")),
            "ever_paid": (registry.metric(
                "ever_paid", statuses["ever_paid"], comparable=False,
                href="/admin/customers?range=all&filter=ever_paid")
                if statuses else registry.error_metric("ever_paid")),
            "canceled_ever": (registry.metric(
                "canceled_ever", statuses["canceled"], comparable=False,
                href="/admin/customers?range=all&filter=canceled")
                if statuses else registry.error_metric("canceled_ever")),
            "cash_series": s.get("cash_series", None),
            "mrr_history": s.get("mrr_history", None),
            "by_plan": s.get("by_plan", None),
            "ledger": s.get("ledger", None),
            # Costs load on their own (/revenue/costs): the shared report can
            # take seconds when its 10-minute copy has expired.
            "costs": None,
        }
        return {"data": data, "errors": s.errors}

    value, computed_at, age = _cached("revenue", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/revenue/costs", methods=["GET"])
@admin_required
def revenue_costs():
    fresh = _fresh()

    def compute():
        return {"data": costs_mod.revenue_costs(costs_report(fresh)),
                "errors": []}

    value, computed_at, age = _cached("revenue_costs", {}, compute)
    return _respond(value["data"], computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/revenue/payments", methods=["GET"])
@admin_required
def revenue_payments():
    period = _period("all", allow_all=True)
    page, per_page = _paging()
    status = _args().get("status", "all")
    if status not in ("all", "succeeded", "failed"):
        raise ApiError(400, "bad_request",
                       "status must be all, succeeded or failed.")
    cur = db.cursor()
    where = ["TRUE"]
    params = period.params()
    if not period.is_all:
        where.append("COALESCE(p.occurred_at, p.created_at) >= %(start)s "
                     "AND COALESCE(p.occurred_at, p.created_at) < %(end)s")
    if status == "succeeded":
        where.append(defs.success("p"))
    elif status == "failed":
        where.append(defs.failed_payment("p"))
    w = " AND ".join(where)
    cur.execute(f"SELECT count(*) AS n FROM payments p WHERE {w}", params)
    total = int(cur.fetchone()["n"])
    params.update(limit=per_page, offset=(page - 1) * per_page)
    cur.execute(f"""
        SELECT p.id, COALESCE(p.occurred_at, p.created_at) AS at,
               p.amount_cents, p.currency, p.status, p.plan, p.origin,
               p.error_code, {defs.success('p')} AS ok,
               {defs.failed_payment('p')} AS failed,
               u.id AS user_id, u.email, u.created_at AS joined,
               COALESCE(u.billing_plan, u.plan) AS user_plan,
               CASE WHEN u.id IS NULL THEN NULL ELSE {money.STATUS_SQL} END
                   AS user_status
          FROM payments p LEFT JOIN users u ON u.id = p.user_id
         WHERE {w}
         ORDER BY COALESCE(p.occurred_at, p.created_at) DESC, p.id DESC
         LIMIT %(limit)s OFFSET %(offset)s""", params)
    rows = []
    epoch = datetime.strptime(defs.METRICS_EPOCH, "%Y-%m-%d")
    for r in cur.fetchall():
        note = None
        if r["user_id"] is None:
            note = "Deleted account"
        elif defs.is_internal_email(r["email"]):
            note = ("Your account" if r["email"].lower() == defs.ADMIN_EMAIL
                    else "Test account")
        elif r["joined"] and r["joined"] < epoch:
            note = "Before 6 Jul"
        rows.append({
            "id": r["id"], "at": defs.iso(r["at"]),
            "customer": ({"id": r["user_id"], "email": r["email"],
                          "plan": r["user_plan"], "status": r["user_status"]}
                         if r["user_id"] is not None else None),
            "account_note": note,
            "amount_usd": defs.usd(r["amount_cents"]),
            "currency": r["currency"], "status": r["status"],
            "status_label": customers.payment_status_label(dict(r)),
            "plan": r["plan"], "origin": r["origin"],
            "error_code": r["error_code"]})
    return _respond({"rows": rows, "page": page, "per_page": per_page,
                     "total": total}, period)


@admin_v2_bp.route("/revenue/billing-problems", methods=["GET"])
@admin_required
def revenue_billing_problems():
    def compute():
        cur = db.cursor()
        rows, last = money.billing_problems(cur)
        out = []
        for r in rows:
            title, ours, paddle = money.PROBLEM_TEXT.get(
                r["problem"], (r["problem"], "", ""))
            out.append({"customer": {"id": r["id"], "email": r["email"],
                                     "plan": r["plan"],
                                     "status": r.get("status") or "free"},
                        "problem": title, "ours": ours, "paddle": paddle,
                        "since": defs.iso(r["payment_failed_at"]
                                          or r["billing_synced_at"])})
        return {"data": {"last_checked_at": defs.iso(last), "rows": out},
                "errors": []}

    value, computed_at, age = _cached("billing_problems", {}, compute)
    return _respond(value["data"], computed_at=computed_at, cache_age=age)


# ── 28–30. engagement, audience, exports ─────────────────────────────────
@admin_v2_bp.route("/engagement", methods=["GET"])
@admin_required
def engagement():
    period = _period("30d")

    def compute():
        s = Sections()
        s.parallel([
            ("engagement", lambda cur: insights.engagement(cur, period)),
            ("top_customers", lambda cur: insights.top_customers(cur, period)),
            ("activity", lambda cur: insights.activity_feed(cur)),
        ])
        data = {"engagement": s.get("engagement", None),
                "top_customers": s.get("top_customers", None),
                "activity": s.get("activity", None)}
        return {"data": data, "errors": s.errors}

    value, computed_at, age = _cached("engagement", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/activity", methods=["GET"])
@admin_required
def activity():
    limit = _int_arg("limit", 40, 1, 100)

    def compute():
        s = Sections()
        rows = s.run("activity", lambda: insights.activity_feed(db.cursor(),
                                                                limit), None)
        return {"data": {"rows": rows}, "errors": s.errors}

    value, computed_at, age = _cached("activity", {"limit": limit}, compute)
    return _respond(value["data"], errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/audience", methods=["GET"])
@admin_required
def audience():
    period = _period("30d")

    def compute():
        s = Sections()
        data = s.run("audience", lambda: insights.audience(db.cursor(),
                                                           period), None)
        status, note = _visitor_status(period)
        return {"data": {"audience": data, "status": status, "note": note},
                "errors": s.errors}

    value, computed_at, age = _cached("audience", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/exports", methods=["GET"])
@admin_required
def exports_page():
    period = _period("30d")
    from routes.admin_video import _presign

    def compute():
        s = Sections()
        data = s.run("exports", lambda: insights.exports_report(
            db.cursor(), period, _presign), None)
        return {"data": data, "errors": s.errors}

    # Presigned links last 15 minutes, so this answer is never served stale.
    value, computed_at, age = _cached("exports", dict(_args()), compute,
                                      swr=False)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# ── 25–27. health and live ───────────────────────────────────────────────
@admin_v2_bp.route("/health", methods=["GET"])
@admin_required
def health_page():
    period = _period("7d")
    include_owner = _include_owner()

    def compute():
        s = Sections()
        s.parallel([
            ("engine", lambda cur: health.engine(cur, include_owner)),
            ("failures", lambda cur: health.failures(cur, period,
                                                     include_owner)),
            ("messages_without_edit", lambda cur: {
                k: v for k, v in health.messages_without_edit(
                    cur, period, include_owner).items()
                if k in ("paying", "free", "recent")}),
            ("uploads", lambda cur: health.uploads(cur, period,
                                                   include_owner)),
            ("export_steps", lambda cur: health.export_steps(
                cur, period, include_owner)),
            ("waits", lambda cur: _wait_summary(
                health.waits(cur, period, include_owner))),
            ("projects_exported_share",
             lambda cur: health.projects_exported_share(cur, include_owner)),
        ])
        data = {k: s.get(k, None) for k in (
            "engine", "failures", "messages_without_edit", "uploads",
            "export_steps", "waits")}
        data["projects_exported_share"] = s.get(
            "projects_exported_share",
            registry.error_metric("projects_exported_share"))
        return {"data": data, "errors": s.errors}

    value, computed_at, age = _cached("health", dict(_args()), compute)
    return _respond(value["data"], period, include_owner=include_owner,
                    errors=value["errors"], computed_at=computed_at,
                    cache_age=age)


def _wait_summary(w):
    return {"upload_median_s": w["medians"]["upload_s"],
            "analysis_median_s": w["medians"]["analysis_s"],
            "edit_median_s": w["medians"]["edit_s"],
            "points": len(w["points"])}


@admin_v2_bp.route("/health/waits", methods=["GET"])
@admin_required
def health_waits():
    period = _period("30d")
    include_owner = _include_owner()

    def compute():
        return {"data": health.waits(db.cursor(), period, include_owner),
                "errors": []}

    value, computed_at, age = _cached("waits", dict(_args()), compute)
    return _respond(value["data"], period, include_owner=include_owner,
                    computed_at=computed_at, cache_age=age)


@admin_v2_bp.route("/live", methods=["GET"])
@admin_required
def live():
    return _respond(live_mod.live(db.cursor()))
