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
                           db, defs, funnel as funnel_mod, health, live as
                           live_mod, money, outreach, projects, ranges,
                           registry, visitors)
from admin_metrics.ranges import RangeError
from routes.admin import admin_required

admin_v2_bp = Blueprint("admin_v2", __name__, url_prefix="/admin/v2")
log = logging.getLogger(__name__)

TTL = {"meta": 3600, "summary": 60, "trend": 300, "acquisition": 300,
       "outreach": 300, "pages": 300, "funnel": 300, "cohorts": 300,
       "revenue": 300, "health": 60, "waits": 300, "attention": 60,
       "billing_problems": 60}


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


def _cached(name, params, compute):
    """Run compute() through the in-process cache unless it reported errors."""
    value, computed_at, age = cache.get_or_compute(
        name, params, TTL.get(name, 0), compute, fresh=_fresh())
    if value.get("errors"):
        cache.clear_key(name, params)
    return value, computed_at, age


class Sections:
    """R8: each section in its own try; failures become section_errors."""

    def __init__(self):
        self.errors = []

    def run(self, name, fn, default=None):
        try:
            return fn()
        except Exception as e:
            if isinstance(e, psycopg2.errors.QueryCanceled):
                msg = "This part took too long and was skipped."
            elif isinstance(e, psycopg2.Error):
                msg = "The database couldn't answer this part."
            else:
                msg = "This part couldn't be computed."
            log.warning("admin v2 section %s failed: %s", name,
                        type(e).__name__)
            self.errors.append({"section": name, "message": msg})
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


ACTIVE_UNION = """
    SELECT p.user_id, {day_a} AS day FROM assets a
      JOIN projects p ON p.id = a.project_id
     WHERE a.kind = 'original' AND a.created_at >= %(start_tz)s
       AND a.created_at < %(end_tz)s
    UNION ALL
    SELECT p.user_id, {day_m} FROM chat_messages cm
      JOIN projects p ON p.chat_session_id = cm.session_id
     WHERE cm.role = 'user' AND cm.created_at >= %(start)s
       AND cm.created_at < %(end)s
    UNION ALL
    SELECT vj.user_id, {day_j} FROM video_jobs vj
     WHERE vj.type = 'mcp_tool' AND vj.payload->>'mutation' = 'true'
       AND vj.created_at >= %(start_tz)s AND vj.created_at < %(end_tz)s
    UNION ALL
    SELECT vj.user_id, {day_e} FROM video_jobs vj
     WHERE vj.type = 'final' AND vj.state = 'done'
       AND vj.updated_at >= %(start_tz)s AND vj.updated_at < %(end_tz)s"""


def _active_sql(by_day):
    if by_day:
        parts = dict(day_a=ranges.local_date_sql("a.created_at", True),
                     day_m=ranges.local_date_sql("cm.created_at", False),
                     day_j=ranges.local_date_sql("vj.created_at", True),
                     day_e=ranges.local_date_sql("vj.updated_at", True))
    else:
        parts = dict(day_a="NULL::date", day_m="NULL::date",
                     day_j="NULL::date", day_e="NULL::date")
    return ACTIVE_UNION.format(**parts)


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
@admin_v2_bp.route("/summary", methods=["GET"])
@admin_required
def summary():
    period = _period("today")

    def compute():
        cur = db.cursor()
        s = Sections()
        comp = period.compare()
        spark_period = ranges.trailing_days(14, period.to_day)
        days = spark_period.day_list()
        kpis = {}

        people = s.run("people", lambda: people_metric(cur, period),
                       (registry.error_metric("people"), None, None))
        kpis["people"] = people[0]
        people_now, people_prev = people[1], people[2]

        def signups_kpi():
            v = signups_count(cur, period)
            p = signups_count(cur, comp)
            sp = signups_by_day(cur, spark_period)
            return registry.metric(
                "signups", v, p, spark=[{"date": d.isoformat(),
                                         "value": sp.get(d, 0)} for d in days],
                href=_href("/admin/customers", period, "&date_field=joined"))
        kpis["signups"] = s.run("signups", signups_kpi,
                                registry.error_metric("signups"))

        def rate_kpi():
            sg = kpis["signups"]
            pp = kpis["people"]
            if sg["status"] == "error" or pp["status"] == "error":
                return registry.error_metric("signup_rate")
            status, note = _visitor_status(period)
            # People are only counted since visits were tracked, so the
            # signups on top of the fraction must cover the same time. Over
            # a period that starts earlier the rate is computed over the
            # tracked part only (never 357%).
            value, prev = None, None
            if status == "partial":
                tracked = _tracked_part(period)
                value = defs.pct(signups_count(cur, tracked), pp["value"]) \
                    if pp["value"] else None
                note = ("Signups since 3 Oct ÷ people since 3 Oct: visitors "
                        "are counted from 3 Oct 2026, so earlier signups are "
                        "left out of this rate.")
            elif status == "ok":
                value = defs.pct(sg["value"], pp["value"]) \
                    if pp["value"] else None
                prev = defs.pct(sg["previous"], pp["previous"]) \
                    if pp.get("previous") else None
            if value is None and status == "ok":
                status, note = "unavailable", "No people visited yet."
            return registry.metric("signup_rate", value, prev, status=status,
                                   note=note,
                                   href=_href("/admin/funnel", period))
        kpis["signup_rate"] = s.run("signup_rate", rate_kpi,
                                    registry.error_metric("signup_rate"))

        def new_paying_kpi():
            sp = money.new_paying_by_day(cur, spark_period)
            return registry.metric(
                "new_paying", money.new_paying(cur, period),
                money.new_paying(cur, comp),
                spark=[{"date": d.isoformat(), "value": sp.get(d, 0)}
                       for d in days],
                href=_href("/admin/customers", period,
                           "&date_field=first_paid"))
        kpis["new_paying"] = s.run("new_paying", new_paying_kpi,
                                   registry.error_metric("new_paying"))

        def cash_kpi():
            c = money.cash(cur, period)
            pc = money.cash(cur, comp)
            sp = money.cash_by_day(cur, spark_period)
            return registry.metric(
                "cash", c["usd"], pc["usd"],
                note=(f"{c['non_usd']} payments in other currencies are not "
                      "in this total." if c["non_usd"] else None),
                breakdown=[registry.item("payments", c["payments"]),
                           registry.item("non_usd_payments", c["non_usd"])],
                spark=[{"date": d.isoformat(),
                        "value": (sp.get(d) or (0.0, 0))[0]} for d in days],
                href=_href("/admin/revenue", period) + "#payments")
        kpis["cash"] = s.run("cash", cash_kpi, registry.error_metric("cash"))

        def mrr_kpi():
            mrr, paying, _ = money.mrr_and_paying(cur)
            prev = money.mrr_at(cur, comp.to_day) if comp else None
            hist = money.mrr_history(cur, spark_period)
            spark = []
            if hist["available"]:
                by = {r["date"]: r["mrr_usd"] for r in hist["rows"]}
                spark = [{"date": d.isoformat(), "value": by.get(d.isoformat())}
                         for d in days]
            return registry.metric(
                "mrr", mrr, prev,
                note=(None if prev is not None else
                      "No comparison yet: MRR history starts when daily "
                      "billing snapshots begin."),
                breakdown=[registry.item("paying_now", paying)], spark=spark,
                href="/admin/revenue")
        kpis["mrr"] = s.run("mrr", mrr_kpi, registry.error_metric("mrr"))

        def active_kpi():
            sp = active_by_day(cur, spark_period)
            return registry.metric(
                "active_customers", active_customers(cur, period),
                active_customers(cur, comp),
                spark=[{"date": d.isoformat(), "value": sp.get(d, 0)}
                       for d in days],
                href=_href("/admin/customers", period, "&date_field=active"))
        kpis["active_customers"] = s.run(
            "active_customers", active_kpi,
            registry.error_metric("active_customers"))

        def exports_kpi():
            n, people_n = exports_count(cur, period)
            pn, _ = exports_count(cur, comp)
            sp = exports_by_day(cur, spark_period)
            return registry.metric(
                "exports", n, pn,
                breakdown=[registry.item("exporters", people_n)],
                spark=[{"date": d.isoformat(), "value": sp.get(d, 0)}
                       for d in days],
                href=_href("/admin/projects", period, "&date_field=exported"))
        kpis["exports"] = s.run("exports", exports_kpi,
                                registry.error_metric("exports"))

        def health_items():
            failing = money.status_counts(cur)["payment_failing"]
            failed = money.failed_payments(cur, period)["payments"]
            return health.health_items(cur, period, failing, failed)
        items = s.run("health", health_items, [])

        channels = s.run("channels",
                         lambda: channels_report.summary_channels(cur, period),
                         None)
        order = ("people", "signups", "signup_rate", "new_paying", "cash",
                 "mrr", "active_customers", "exports")
        return {"data": {"kpis": [kpis[k] for k in order], "health": items,
                         "channels": channels},
                "errors": s.errors}

    value, computed_at, age = _cached("summary", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


# ── 3. trend ─────────────────────────────────────────────────────────────
TREND_METRICS = ("people", "signups", "new_paying", "cash", "link_previews")


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
        cur = db.cursor()
        s = Sections()
        # Six extra days so the first point has a full 7-day average.
        wide = ranges.make_period("custom", period.from_day - timedelta(days=6),
                                  period.to_day)
        days = wide.day_list()
        # A series the page didn't ask for is null ("not computed"), never 0.
        people = s.run("people", lambda: visitors.classify_by_day(cur, wide),
                       {}) if ("people" in wanted or "link_previews" in wanted) \
            else {}
        signups = s.run("signups", lambda: signups_by_day(cur, wide), None) \
            if "signups" in wanted else None
        paying = s.run("new_paying", lambda: money.new_paying_by_day(cur, wide),
                       None) if "new_paying" in wanted else None
        cash = s.run("cash", lambda: money.cash_by_day(cur, wide), None) \
            if "cash" in wanted else None
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
            })
        avg = {"people_7d": _avg7([r["people"] for r in rows]),
               "signups_7d": _avg7([r["signups"] for r in rows]),
               "cash_7d": _avg7([r["cash_usd"] for r in rows])}
        rows, avg = rows[6:], {k: v[6:] for k, v in avg.items()}
        grain = "day"
        if period.days and period.days > 120:
            grain = "week"
            rows = _weekly(rows, ("people", "link_previews", "signups",
                                  "new_paying", "cash_usd"))
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
    data = customers.list_customers(db.cursor(), period, _args(), page,
                                    per_page)
    return _respond(data, period)


@admin_v2_bp.route("/customers/<int:user_id>", methods=["GET"])
@admin_required
def customer_detail(user_id):
    data = customers.customer_detail(db.cursor(), user_id)
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
    data = projects.list_projects(db.cursor(), period, _args(), page, per_page)
    return _respond(data, period, include_owner=include_owner)


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
        cur = db.cursor()
        s = Sections()
        comp = period.compare()
        tiles = {}
        statuses = s.run("statuses", lambda: money.status_counts(cur), None)
        mrr_info = s.run("mrr", lambda: money.mrr_and_paying(cur), None)
        if mrr_info:
            mrr, paying, groups = mrr_info
            prev = money.mrr_at(cur, comp.to_day) if comp else None
            tiles["mrr"] = registry.metric(
                "mrr", mrr, prev, href="/admin/revenue",
                note=None if prev is not None else
                "No comparison yet: MRR history starts when daily billing "
                "snapshots begin.")
            tiles["paying_now"] = registry.metric(
                "paying_now", paying, comparable=False,
                href="/admin/customers?range=all&filter=paying")
        else:
            mrr, paying, groups = None, None, None
            tiles["mrr"] = registry.error_metric("mrr")
            tiles["paying_now"] = registry.error_metric("paying_now")

        def cash_tile():
            c, pc = money.cash(cur, period), money.cash(cur, comp)
            return registry.metric(
                "cash", c["usd"], pc["usd"],
                breakdown=[registry.item("payments", c["payments"]),
                           registry.item("non_usd_payments", c["non_usd"])],
                href=_href("/admin/revenue", period) + "#payments")
        tiles["cash"] = s.run("cash", cash_tile, registry.error_metric("cash"))
        tiles["new_paying"] = s.run(
            "new_paying", lambda: registry.metric(
                "new_paying", money.new_paying(cur, period),
                money.new_paying(cur, comp),
                href=_href("/admin/customers", period,
                           "&date_field=first_paid")),
            registry.error_metric("new_paying"))

        def stopped_tile():
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
        tiles["stopped_paying"] = s.run("stopped_paying", stopped_tile,
                                        registry.error_metric("stopped_paying"))
        tiles["payment_failing"] = (
            registry.metric("payment_failing", statuses["payment_failing"],
                            comparable=False,
                            href="/admin/customers?range=all"
                                 "&filter=payment_failing")
            if statuses else registry.error_metric("payment_failing"))

        def cash_series():
            if period.days and period.days <= 31:
                by = money.cash_by_day(cur, period)
                rows = [{"date": d.isoformat(),
                         "usd": (by.get(d) or (0.0, 0))[0],
                         "payments": (by.get(d) or (0.0, 0))[1]}
                        for d in period.day_list()]
                wide = ranges.make_period(
                    "custom", period.from_day - timedelta(days=6),
                    period.to_day)
                wb = money.cash_by_day(cur, wide)
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

        def ledger():
            out = money.ledger(cur)
            out["failed_in_range"] = money.failed_payments(cur, period)
            return out

        def costs():
            return costs_mod.revenue_costs(costs_report(_fresh()))

        data = {
            "tiles": [tiles[k] for k in ("mrr", "paying_now", "cash",
                                         "new_paying", "stopped_paying",
                                         "payment_failing")],
            "avg_plan_value": (registry.metric(
                "avg_plan_value", round(mrr / paying, 2) if paying else None,
                comparable=False) if mrr_info else
                registry.error_metric("avg_plan_value")),
            "ever_paid": (registry.metric(
                "ever_paid", statuses["ever_paid"], comparable=False,
                href="/admin/customers?range=all&filter=ever_paid")
                if statuses else registry.error_metric("ever_paid")),
            "canceled_ever": (registry.metric(
                "canceled_ever", statuses["canceled"], comparable=False,
                href="/admin/customers?range=all&filter=canceled")
                if statuses else registry.error_metric("canceled_ever")),
            "cash_series": s.run("cash_series", cash_series, None),
            "mrr_history": s.run("mrr_history",
                                 lambda: money.mrr_history(cur, period),
                                 None),
            "by_plan": s.run("by_plan", lambda: money.by_plan(
                cur, groups, mrr) if groups is not None else
                money.by_plan(cur), None),
            "ledger": s.run("ledger", ledger, None),
            "costs": s.run("costs", costs, None),
        }
        return {"data": data, "errors": s.errors}

    value, computed_at, age = _cached("revenue", dict(_args()), compute)
    return _respond(value["data"], period, errors=value["errors"],
                    computed_at=computed_at, cache_age=age)


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


# ── 25–27. health and live ───────────────────────────────────────────────
@admin_v2_bp.route("/health", methods=["GET"])
@admin_required
def health_page():
    period = _period("7d")
    include_owner = _include_owner()

    def compute():
        cur = db.cursor()
        s = Sections()
        data = {
            "engine": s.run("engine", lambda: health.engine(
                cur, include_owner), None),
            "failures": s.run("failures", lambda: health.failures(
                cur, period, include_owner), None),
            "messages_without_edit": s.run(
                "messages_without_edit", lambda: {
                    k: v for k, v in health.messages_without_edit(
                        cur, period, include_owner).items()
                    if k in ("paying", "free", "recent")}, None),
            "uploads": s.run("uploads", lambda: health.uploads(
                cur, period, include_owner), None),
            "export_steps": s.run("export_steps", lambda: health.export_steps(
                cur, period, include_owner), None),
            "waits": s.run("waits", lambda: _wait_summary(
                health.waits(cur, period, include_owner)), None),
            "projects_exported_share": s.run(
                "projects_exported_share",
                lambda: health.projects_exported_share(cur, include_owner),
                registry.error_metric("projects_exported_share")),
        }
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
