from flask import Blueprint, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app
import os
import re
from datetime import datetime, timedelta

import billing
import trial_state

admin_bp = Blueprint('admin', __name__)

# The customer population is defined once, in admin_metrics.defs, and shared
# by every admin surface (old and /admin/v2). See that module for the rules:
# accounts created before ADMIN_METRICS_EPOCH, the admin account and any
# ADMIN_EXCLUDE_EMAILS test accounts are never customers.
from admin_metrics.defs import (  # noqa: E402
    ADMIN_EMAIL, METRICS_EPOCH, EXCLUDE_EMAILS, ROBOT_UA)

# ─────────────────────────────────────────────────────────────────────────────
#  The single SQL expression for "unique visitor" on the legacy screens.
#  Uses device_id when available, falls back to ip.
# ─────────────────────────────────────────────────────────────────────────────
UNIQUE_VISITOR = "COALESCE(NULLIF(device_id, ''), ip)"


def _scope(alias="u"):
    """Boolean SQL keeping only real, post-relaunch, non-test accounts. Drop it
    into any WHERE/AND/JOIN-ON that touches the users table so old-idea signups
    and test accounts never skew a metric. `alias` is the users-table alias
    ('' for a bare `users`). Same rule as admin_metrics.defs.scope."""
    from admin_metrics.defs import scope
    return scope(alias)


def get_db(readonly=None):
    """Admin connection with time limits (R5).

    GET handlers get a read-only autocommit connection: one failed statement
    never poisons the rest of the request, and no admin page can hold a lock.
    Writes (billing sync) keep a normal transaction with a generous statement
    limit. Without these limits a slow query kept running long after the
    browser and Vercel had given up.
    """
    if readonly is None:
        try:
            readonly = request.method == 'GET'
        except RuntimeError:
            readonly = False
    if readonly:
        options = ('-c statement_timeout=8000 -c lock_timeout=2000 '
                   '-c idle_in_transaction_session_timeout=30000 '
                   '-c default_transaction_read_only=on')
    else:
        options = ('-c statement_timeout=30000 '
                   '-c idle_in_transaction_session_timeout=60000')
    conn = psycopg2.connect(current_app.config['DATABASE_URL'],
                            cursor_factory=RealDictCursor, connect_timeout=5,
                            options=options)
    if readonly:
        conn.autocommit = True
    return conn


def _classic_cached(name, ttl_s, compute, params=None):
    """Serve a classic-admin report from the in-process admin cache.

    `compute(cur)` runs on its own read-only connection, so a stale answer can
    be refreshed in the background (stale-while-revalidate) after the request
    that asked for it has already been answered. `?fresh=1` recomputes."""
    from admin_metrics import cache

    def run():
        conn = get_db(readonly=True)
        try:
            with conn.cursor() as cur:
                return compute(cur)
        finally:
            conn.close()

    try:
        fresh = request.args.get('fresh') in ('1', 'true')
    except RuntimeError:
        fresh = False
    value, _computed_at, _age = cache.get_or_compute(
        'classic:' + name, params or {}, ttl_s, run, fresh=fresh,
        stale_s=30 * 60)
    return value


def _local_day_bounds():
    """Today and the last 7 days as naive-UTC instants on admin-timezone days."""
    from admin_metrics import ranges
    today = ranges.parse({'range': 'today'})
    week = ranges.parse({'range': '7d'})
    return ranges.naive(today.start), ranges.naive(week.start)


def legacy_error_response(e):
    """JSON errors for the admin blueprints (R5): never Flask's HTML 500."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, psycopg2.errors.QueryCanceled):
        return jsonify({'error': 'This report took too long. Try again, or '
                                 'choose a shorter period.',
                        'code': 'timeout', 'retryable': True}), 503
    if isinstance(e, psycopg2.OperationalError):
        current_app.logger.warning('Admin database unavailable (%s)',
                                   type(e).__name__)
        return jsonify({'error': 'The database is busy or restarting. Try '
                                 'again in a moment.',
                        'code': 'unavailable', 'retryable': True}), 503
    current_app.logger.exception('Admin request failed')
    return jsonify({'error': 'The server hit an error building this page.',
                    'code': 'internal', 'retryable': True}), 500


admin_bp.register_error_handler(Exception, legacy_error_response)


GZIP_MIN_BYTES = 32 * 1024


def gzip_large_json(resp):
    """Compress big admin JSON (a long conversation is mostly repeated text:
    2 MB becomes about 200 KB), when the browser accepts gzip."""
    try:
        if (resp.status_code != 200 or resp.direct_passthrough
                or resp.mimetype != 'application/json'
                or 'Content-Encoding' in resp.headers
                or 'gzip' not in (request.headers.get('Accept-Encoding')
                                  or '').lower()):
            return resp
        data = resp.get_data()
        if len(data) < GZIP_MIN_BYTES:
            return resp
        import gzip
        body = gzip.compress(data, compresslevel=5)
        resp.set_data(body)
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Content-Length'] = str(len(body))
        resp.vary.add('Accept-Encoding')
    except Exception:
        pass
    return resp


admin_bp.after_request(gzip_large_json)


# ─────────────────────────────────────────────────────────────────────────────
#  MONEY (round 59)
#
#  Two numbers, and they answer different questions. Keeping them apart is the
#  whole point: the admin used to have only the first, computed off a price map
#  of three retired plans, so it read $0 while also insisting a customer had
#  converted.
#
#    _live_mrr        — what the ACTIVE, PAYING subscriptions are worth per
#                       month. Forward-looking. Excludes trials, past_due and
#                       canceled, because none of them are money.
#    _collected       — what actually hit the account, from `payments`. This is
#                       the only figure on the system that cannot be wrong
#                       about a refused card: it sums the amounts Paddle told
#                       us it captured.
# ─────────────────────────────────────────────────────────────────────────────

def _billing_ready(cur):
    """True once migration 012 has run. Every money query degrades to the old
    is_subscribed-only shape until then, rather than 500ing on a missing
    column — prod schema is applied by hand, so that window is real."""
    try:
        cur.execute("""SELECT COUNT(*) AS n FROM information_schema.columns
                        WHERE table_name = 'users'
                          AND column_name IN ('billing_status',
                                              'billing_plan',
                                              'billing_period')""")
        return (cur.fetchone()['n'] or 0) == 3
    except Exception:
        return False


def _paying_predicate(alias='u'):
    """SQL for "this subscription is genuinely being paid for".

    `billing_status = 'active'` is Paddle's own word. The NULL branch carries
    the grandfathered rows that predate the billing columns: they are
    subscribed, they never had a trial recorded, and the hourly reconciler will
    give them a real status within the hour. Counting them meanwhile is the
    honest read — they were paying yesterday.
    """
    p = f"{alias}." if alias else ""
    return (f"({p}is_subscribed = 1 AND ("
            f"{p}billing_status = 'active' OR ("
            f"{p}billing_status IS NULL AND {p}trial_status IS DISTINCT FROM 'trialing')))")


def _live_mrr(cur, scope, alias=''):
    """Monthly recurring revenue in whole USD, from the paying subscriptions."""
    if not _billing_ready(cur):
        # Pre-migration: the best available answer is plan counts at real
        # prices. Still infinitely better than the retired-plan map, which
        # could only ever return 0.
        cur.execute(f"SELECT plan, COUNT(*) AS c FROM users "
                    f"WHERE is_subscribed = 1 AND {scope} GROUP BY plan")
        return round(sum(billing.monthly_value(r['plan']) * r['c']
                         for r in cur.fetchall()), 2)
    cur.execute(f"""
        SELECT COALESCE(billing_plan, plan) AS plan,
               COALESCE(billing_period, 'monthly') AS period,
               COUNT(*) AS c
          FROM users u
         WHERE {_paying_predicate('u')} AND {scope}
         GROUP BY 1, 2
    """)
    return round(sum(billing.monthly_value(r['plan'], r['period']) * r['c']
                     for r in cur.fetchall()), 2)


def _collected(cur, since_days=None):
    """Captured USD for the same customer cohort used by MRR."""
    try:
        cur.execute("SELECT to_regclass('public.payments') AS t")
        if not cur.fetchone()['t']:
            return {}
        window = (f"AND p.occurred_at >= NOW() - INTERVAL '{int(since_days)} days'"
                  if since_days else "")
        # amount_cents is Paddle's grand_total in MINOR units — "3000" is
        # $30.00. The `> 0` is not an optimisation: a trial opens with a real
        # `completed` transaction for $0.00, so without it "completed
        # transactions" counts trials as sales.
        cur.execute(f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS cents,
                   COUNT(*) AS n
              FROM payments p JOIN users u ON u.id = p.user_id
             WHERE p.status IN ('completed', 'paid') AND p.amount_cents > 0
               AND p.currency = 'USD' AND {_scope('u')} {window}
        """)
        row = cur.fetchone()
        cur.execute(f"""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(amount_cents), 0) AS cents
              FROM payments p JOIN users u ON u.id = p.user_id
             WHERE (p.status = 'past_due' OR
                    (p.status = 'ready' AND p.error_code IS NOT NULL))
               AND p.amount_cents > 0 AND p.currency = 'USD'
               AND {_scope('u')} {window}
        """)
        failed = cur.fetchone()
        return {
            'collected_usd': round(float(row['cents'] or 0) / 100.0, 2),
            'collected_payments': row['n'] or 0,
            'failed_usd': round(float(failed['cents'] or 0) / 100.0, 2),
            'failed_payments': failed['n'] or 0,
            'currency': 'USD',
            'metrics_epoch': METRICS_EPOCH,
        }
    except Exception:
        return {}


def admin_required(f):
    from functools import wraps
    import jwt
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[len('Bearer '):]
        if not token:
            return jsonify({'error': 'Unauthorized'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            email = data.get('email', '')
            if email != ADMIN_EMAIL:
                return jsonify({'error': 'Forbidden'}), 403
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
#  TRACK PAGE VISIT
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/track', methods=['POST'])
def track_visit():
    """The retired first tracker. It stored IPs, sent them to a third-party
    geo service over plain HTTP and spawned a thread per hit, unauthenticated.
    The site has used /admin/journey since 3 Oct 2026: answer 410, store
    nothing, start nothing."""
    return '', 410


# ─────────────────────────────────────────────────────────────────────────────
#  OVERVIEW KPIs
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/overview', methods=['GET'])
@admin_required
def overview():
    """Business overview (the classic admin's top cards).

    "Today" and "this week" are admin-timezone (Dubai) days like every other
    admin count. Visits are PEOPLE (admin_metrics.visitors): robots, the
    owner's browsers, Meta link previews and outreach link checks are left
    out and reported beside the count. Cached for a minute and refreshed in
    the background, so the page never waits on a report it has seen."""
    return jsonify(_classic_cached('overview', 60, _overview)), 200


def _overview(cur):
    from admin_metrics import classic
    scope = _scope('')
    day0, week0 = _local_day_bounds()
    cur.execute(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE created_at >= %(day0)s) AS today,
               COUNT(*) FILTER (WHERE created_at >= %(week0)s) AS week,
               COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS month,
               COUNT(*) FILTER (WHERE created_at >= %(week0)s - INTERVAL '7 days'
                                  AND created_at < %(week0)s) AS prev_week
          FROM users WHERE is_verified = 1 AND {scope}""",
                {'day0': day0, 'week0': week0})
    u = cur.fetchone()
    total_users = u['total']

    cur.execute(f"SELECT COUNT(*) AS total FROM users WHERE is_subscribed = 1 AND {scope}")
    total_subscribed = cur.fetchone()['total']

    cur.execute(f"""
        SELECT plan, COUNT(*) AS count FROM users
         WHERE is_verified = 1 AND {scope}
         GROUP BY plan
    """)
    plan_breakdown = {r['plan']: r['count'] for r in cur.fetchall()}

    # MRR over PAYING subscriptions only (a trial is not revenue).
    mrr = _live_mrr(cur, scope)
    conversion_rate = round((total_subscribed / max(1, total_users)) * 100, 1)

    def trend(current, previous):
        if previous == 0:
            return 100 if current > 0 else 0
        return round(((current - previous) / previous) * 100, 1)

    out = {
        'meta': {
            'metrics_epoch': METRICS_EPOCH,
            'excluded_emails': len(EXCLUDE_EMAILS),
            'timezone': 'Asia/Dubai',
        },
        'users': {
            'total': total_users,
            'new_today': u['today'],
            'new_week': u['week'],
            'new_month': u['month'],
            'trend_week': trend(u['week'], u['prev_week']),
        },
        'subscriptions': {
            'total': total_subscribed,
            'plan_breakdown': plan_breakdown,
            'mrr': mrr,
            'conversion_rate': conversion_rate,
        },
        'visits': classic.overview_visits(cur),
    }
    # The headline split the owner checks against Paddle's dashboard:
    # renewing / cancelled-with-access / payment failing, and both MRRs.
    out['subscriptions']['paddle'] = _paddle_split(cur)
    return out


def _paddle_split(cur):
    """Paddle's subscriptions split by state (admin_metrics.subscriptions);
    a failure hides the split, never the page around it."""
    from admin_metrics import subscriptions as subs
    try:
        return subs.paddle_split(cur)
    except Exception:
        current_app.logger.warning("paddle subscription split unavailable",
                                   exc_info=True)
        return {'available': False}


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Registrations (all video-era time, UTC days)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/registrations', methods=['GET'])
@admin_required
def chart_registrations():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(c.count, 0) AS count
                FROM generate_series(DATE '{epoch}', CURRENT_DATE, '1 day') AS d
                LEFT JOIN (
                    SELECT created_at::date AS dt, COUNT(*) AS count
                    FROM users WHERE is_verified = 1 AND {scope}
                    GROUP BY created_at::date
                ) c ON c.dt = d::date
                ORDER BY d
            """.format(epoch=METRICS_EPOCH, scope=_scope('')))
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows],
                        'scope_start': METRICS_EPOCH, 'timezone': 'UTC'}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Cumulative registration growth (all video-era time)
#
#  The "how fast am I growing" curve: one point per day from the metrics
#  epoch to now, each carrying that day's signups and the running total.
#  generate_series fills the zero days so flat stretches are visibly flat
#  instead of silently skipped, and the running SUM is over the filled
#  series so the curve never dips on a quiet day.
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/growth', methods=['GET'])
@admin_required
def chart_growth():
    """Daily verified signups and the running total since the metrics epoch,
    one point per admin-timezone (Dubai) day, zero days included."""
    return jsonify(_classic_cached('growth', 300, _growth)), 200


def _growth(cur):
    from admin_metrics import ranges
    cur.execute("""
        SELECT
            TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
            COALESCE(c.count, 0) AS added,
            SUM(COALESCE(c.count, 0)) OVER (ORDER BY d::date) AS total
        FROM generate_series(DATE '{epoch}', %(today)s::date, '1 day') AS d
        LEFT JOIN (
            SELECT (created_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS dt,
                   COUNT(*) AS count
            FROM users WHERE is_verified = 1 AND {scope}
            GROUP BY 1
        ) c ON c.dt = d::date
        ORDER BY d
    """.format(epoch=METRICS_EPOCH, scope=_scope('')),
                {'today': ranges.local_today(), 'tz': ranges.TZ_NAME})
    return {'data': [{'day': r['day'], 'added': int(r['added']),
                      'total': int(r['total'])} for r in cur.fetchall()],
            'timezone': ranges.TZ_NAME}


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Visits (30d)
# ─────────────────────────────────────────────────────────────────────────────


@admin_bp.route('/charts/visits', methods=['GET'])
@admin_required
def chart_visits():
    """"Page visits & signups": people per admin day (robots, own browsers,
    link previews and outreach link checks left out and counted beside),
    page views, verified signups and the signup rate per 100 people."""
    from admin_metrics import classic
    return jsonify(_classic_cached('visits', 300, classic.visits_report)), 200


@admin_bp.route('/journeys', methods=['GET'])
@admin_required
def website_journeys():
    from website_reports import journey_report
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='8s'")
            return jsonify(journey_report(cur, _scope('u'))), 200
    finally:
        conn.close()


@admin_bp.route('/journey', methods=['POST'])
def track_journey():
    from website_analytics import edge_country, save_visit
    if request.content_length and request.content_length > 16384:
        return jsonify({'error': 'payload too large'}), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'invalid payload'}), 400
    try:
        stored = save_visit(data, request.headers.get('User-Agent',''),
                            edge_country(request.headers))
        return jsonify({'stored': stored}), 200
    except ValueError:
        return jsonify({'error': 'invalid tracking fields'}), 400
    except Exception:
        current_app.logger.warning('Website measurement temporarily unavailable')
        return jsonify({'stored': False}), 503


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — MRR Over Time (30d)
# ─────────────────────────────────────────────────────────────────────────────


@admin_bp.route('/charts/mrr', methods=['GET'])
@admin_required
def chart_mrr():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # The three plans hardcoded here — plus/pro/ultra — are all
            # RETIRED, so this chart drew a flat zero for every real customer.
            # Prices come from billing.PLAN_PRICES_USD now, injected as a VALUES
            # list so there is still only one place they are written down.
            price_rows = ",".join(
                f"('{p}',{billing.monthly_value(p, 'monthly')},"
                f"{billing.monthly_value(p, 'yearly')})"
                for p in billing.PLAN_PRICES_USD)
            # Pre-migration the billing columns do not exist; fall back to
            # `plan` at the monthly price rather than 500ing the chart.
            plan_col = ("COALESCE(u.billing_plan, u.plan)" if _billing_ready(cur)
                        else "u.plan")
            period_col = ("COALESCE(u.billing_period, 'monthly')"
                          if _billing_ready(cur) else "'monthly'")
            cur.execute("""
                WITH prices(plan, monthly, yearly) AS (VALUES """ + price_rows + """)
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(CASE WHEN """ + period_col + """ = 'yearly'
                                      THEN p.yearly ELSE p.monthly END), 0) AS mrr,
                    COUNT(u.id) AS subscribers
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN users u ON u.is_subscribed = 1
                    AND u.subscription_expiry > d::date
                    AND u.created_at <= d::date + INTERVAL '1 day'
                    AND """ + _scope('u') + """
                LEFT JOIN prices p ON p.plan = """ + plan_col + """
                GROUP BY d
                ORDER BY d
            """)
            rows = cur.fetchall()
        # NUMERIC arrives as Decimal and was serialised as a string ("40"),
        # which the chart cannot plot: the card drew an empty frame.
        return jsonify({'data': [{'day': r['day'],
                                  'mrr': float(r['mrr'] or 0),
                                  'subscribers': int(r['subscribers'] or 0)}
                                 for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  REVENUE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/revenue', methods=['GET'])
@admin_required
def revenue_analytics():
    return jsonify(_classic_cached('revenue', 120, _revenue)), 200


def _revenue(cur):
    scope = _scope('')  # real post-relaunch customers only
    ready = _billing_ready(cur)

    # Everyone the app treats as subscribed, INCLUDING trials — this
    # is the population breakdown, not the revenue.
    cur.execute(f"SELECT plan, COUNT(*) AS count FROM users WHERE is_subscribed = 1 AND {scope} GROUP BY plan")
    plan_counts = {r['plan']: r['count'] for r in cur.fetchall()}

    # ── The revenue ────────────────────────────────────────────────
    # `plan_prices` used to be {'plus': 20, 'pro': 50, 'ultra': 100} —
    # three plans nobody can buy and nobody live is on — so MRR was
    # structurally 0, and it counted every trialling account as though
    # it were paying. Both halves are fixed: real prices from
    # billing.PLAN_PRICES_USD, and only genuinely-active subscriptions.
    mrr = _live_mrr(cur, scope)
    arr = round(mrr * 12, 2)

    cur.execute(f"SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND {scope}")
    total_verified = cur.fetchone()['total']
    arpu = round(mrr / max(1, total_verified), 2)

    # The paying head-count has to be the SAME population the MRR came
    # from, or ARPPU is one plan's revenue divided by another plan's
    # customers.
    if ready:
        cur.execute(f"""SELECT COUNT(*) AS n FROM users u
                         WHERE {_paying_predicate('u')} AND {scope}""")
        total_paying = cur.fetchone()['n']
    else:
        total_paying = sum(plan_counts.values())
    arppu = round(mrr / max(1, total_paying), 2)
    ltv = round(arppu * 6, 2)

    # Trials and declines, counted separately and never as revenue.
    trialing = past_due = 0
    if ready:
        cur.execute(f"""
            SELECT COALESCE(billing_status, 'unknown') AS s,
                   COUNT(*) AS n
              FROM users u
             WHERE u.is_subscribed = 1 AND {scope}
             GROUP BY 1""")
        by_status = {r['s']: r['n'] for r in cur.fetchall()}
        trialing = by_status.get('trialing', 0)
        past_due = by_status.get('past_due', 0) + by_status.get('paused', 0)
    collected = _collected(cur)
    collected_30d = _collected(cur, since_days=30)

    # Visitors are people (robots, own browsers, link previews and outreach
    # link checks left out). People are counted from 3 Oct 2026, so a 30-day
    # window that starts earlier says where its count begins.
    from admin_metrics import classic, defs as mdefs, ranges as mranges
    unique_visitors_30d, visitors_from = classic.people_count(cur, 30)

    # Registered and subscribed are counted over the SAME window as the
    # visitors, so each step's rate compares one population with the next.
    funnel_start = (mranges.naive(visitors_from) if visitors_from
                    else datetime.utcnow() - timedelta(days=30))
    cur.execute(f"SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND {scope} AND created_at >= %s", (funnel_start,))
    registered_30d = cur.fetchone()['total']

    cur.execute(f"SELECT COUNT(*) AS total FROM users WHERE is_subscribed = 1 AND {scope} AND created_at >= %s", (funnel_start,))
    subscribed_30d = cur.fetchone()['total']

    # Per plan: how many hold it, how many of those are actually
    # PAYING, and what that is worth. The `count`/`paying` split is the
    # thing the old panel could not show — a plan with four holders and
    # zero payers looked identical to one with four payers.
    revenue_by_plan = {}
    if ready:
        cur.execute(f"""
            SELECT COALESCE(billing_plan, plan) AS plan,
                   COALESCE(billing_period, 'monthly') AS period,
                   COUNT(*) AS n
              FROM users u
             WHERE {_paying_predicate('u')} AND {scope}
             GROUP BY 1, 2""")
        paying_rows = cur.fetchall()
    else:
        paying_rows = [{'plan': p, 'period': 'monthly', 'n': c}
                       for p, c in plan_counts.items()]
    paying_by_plan = {}
    for r in paying_rows:
        b = paying_by_plan.setdefault(r['plan'], {'paying': 0, 'revenue': 0.0})
        b['paying'] += r['n']
        b['revenue'] += billing.monthly_value(r['plan'], r['period']) * r['n']
    for plan, count in plan_counts.items():
        b = paying_by_plan.get(plan, {'paying': 0, 'revenue': 0.0})
        revenue_by_plan[plan] = {
            'count': count,
            'paying': b['paying'],
            'revenue': round(b['revenue'], 2),
            'price': billing.monthly_value(plan),
        }

    return {
        'mrr': mrr,
        'arr': arr,
        'arpu': arpu,
        'arppu': arppu,
        'ltv_estimate': ltv,
        'total_paying': total_paying,
        'total_verified': total_verified,
        'revenue_by_plan': revenue_by_plan,
        # Subscribed but NOT revenue. Broken out so "6 subscribers, $0
        # collected" reads as the sentence it is instead of a contradiction.
        'trialing': trialing,
        'past_due': past_due,
        # What actually landed in the account, from the payments ledger.
        'collected': collected,
        'collected_30d': collected_30d,
        'billing_ready': ready,
        'funnel': {
            'visitors_30d': unique_visitors_30d,
            'registered_30d': registered_30d,
            'subscribed_30d': subscribed_30d,
            'visitor_to_register': (round((registered_30d / max(1, unique_visitors_30d)) * 100, 1)
                                    if unique_visitors_30d else None),
            'register_to_subscribe': round((subscribed_30d / max(1, registered_30d)) * 100, 1),
            'visitors_measure': 'people',
            'from': mdefs.iso(visitors_from) if visitors_from else None,
            'from_day': (mranges.local_day(visitors_from).isoformat()
                         if visitors_from else None),
        },
        # Lifetime payments by plan launch (July / September / October).
        'plan_cohorts': _plan_cohorts(cur, scope),
        # Renewing MRR and Paddle MRR, with the renewing / cancelled /
        # failing split they come from (per Paddle subscription).
        'paddle': _paddle_split(cur),
    }


# Plan launches, newest first: each launch's subscribers stay one cohort.
PLAN_COHORTS = (
    ('October 2026 plans', ('plus_2026', 'pro_2026', 'max_2026')),
    ('September 2026 plans', ('mcp_connect', 'advanced', 'ultimate')),
    ('July 2026 plans', ('ai', 'ai_pro', 'ai_max')),
)


def _plan_cohorts(cur, scope):
    """Customers who ever paid, by the plan they paid for, per plan launch.

    A customer's plan is the plan on their latest successful payment (else
    their billing plan). Lifetime USD and payment counts come from the
    payments ledger; paying / payment failing / cancelled is their state now.
    """
    from admin_metrics import defs, money
    cur.execute(f"""
        WITH paid AS (
            SELECT p.user_id, COUNT(*) AS payments,
                   COALESCE(SUM(p.amount_cents) FILTER (
                       WHERE p.currency = 'USD'), 0) AS cents,
                   (ARRAY_AGG(p.plan ORDER BY COALESCE(p.occurred_at,
                                                       p.created_at) DESC)
                        FILTER (WHERE p.plan IS NOT NULL))[1] AS paid_plan
              FROM payments p
             WHERE {defs.success('p')}
             GROUP BY p.user_id
        )
        SELECT COALESCE(paid.paid_plan, u.billing_plan, u.plan) AS plan,
               {money.STATUS_SQL} AS status,
               COUNT(*) AS customers, SUM(paid.payments) AS payments,
               SUM(paid.cents) AS cents
          FROM paid JOIN users u ON u.id = paid.user_id
         WHERE {_scope('u')}
         GROUP BY 1, 2""")
    empty = {'customers': 0, 'paying': 0, 'payment_failing': 0,
             'canceled': 0, 'payments': 0, 'collected_usd': 0.0}
    by_plan = {}
    for r in cur.fetchall():
        plan = r['plan'] or 'free'
        b = by_plan.setdefault(plan, dict(empty, plan=plan))
        n = int(r['customers'] or 0)
        b['customers'] += n
        b['payments'] += int(r['payments'] or 0)
        b['collected_usd'] = round(
            b['collected_usd'] + float(r['cents'] or 0) / 100.0, 2)
        if r['status'] in ('paying', 'payment_failing', 'canceled'):
            b[r['status']] += n
    cohorts, seen = [], set()
    for label, plans in PLAN_COHORTS:
        seen.update(plans)
        cohorts.append({'label': label, 'plans': [
            by_plan.get(p, dict(empty, plan=p)) for p in plans]})
    older = [b for p, b in by_plan.items() if p not in seen]
    if older:
        cohorts.append({'label': 'Older plans', 'plans': older})
    return cohorts


# ─────────────────────────────────────────────────────────────────────────────
#  BILLING — what Paddle says, what we say, and where they disagree
#
#  Built because on Jul 29 2026 the two disagreed on three separate things at
#  once and there was no screen anywhere that would have shown it. The point of
#  this endpoint is that the disagreement is the FIRST thing on it.
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/billing', methods=['GET'])
@admin_required
def billing_overview():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            ready = _billing_ready(cur)
            out = {'billing_ready': ready,
                   'grace_days': billing.PAID_GRACE_DAYS,
                   'max_dunning_emails': billing.MAX_DUNNING_EMAILS}
            if not ready:
                out['note'] = ('migrations/012_billing_truth.sql has not been '
                               'applied — run it in the Render shell')
                return jsonify(out), 200

            # Declines, most recent first. `dunning_email_count` is how many
            # times we have asked them to fix it.
            cur.execute("""
                SELECT id, email, plan, billing_plan, billing_status,
                       payment_failed_at, payment_failed_reason,
                       payment_failed_count, dunning_emailed_at,
                       dunning_email_count, subscription_id,
                       credits_balance, trial_status
                  FROM users
                 WHERE billing_status IN ('past_due', 'paused')
                 ORDER BY payment_failed_at DESC NULLS LAST
                 LIMIT 100""")
            out['declines'] = [dict(r) for r in cur.fetchall()]

            # ── THE CONTRADICTION LIST ────────────────────────────────────
            # Every row here is a place our DB and Paddle cannot both be
            # right. Empty is the goal; anything in it is a bug or an unrun
            # reconcile, and either way it is the thing to look at first.
            cur.execute("""
                SELECT id, email, plan, is_subscribed, billing_status,
                       trial_status, credits_monthly, credits_balance,
                       subscription_id, billing_synced_at,
                       CASE
                         WHEN billing_status = 'canceled' AND is_subscribed = 1
                           THEN 'Paddle says canceled but the account is still subscribed'
                         WHEN billing_status IN ('past_due','paused') AND credits_monthly > 0
                           THEN 'payment refused but the monthly pool is still funded'
                         WHEN trial_status = 'converted'
                              AND NOT EXISTS (SELECT 1 FROM payments p
                                               WHERE p.user_id = users.id
                                                 AND p.status = 'completed'
                                                 AND p.amount_cents > 0)
                           THEN 'marked converted with no payment on record'
                         WHEN billing_status = 'not_in_paddle'
                           THEN 'Paddle does not recognise this subscription id'
                         WHEN is_subscribed = 1 AND subscription_id IS NULL
                           THEN 'subscribed with no Paddle subscription id'
                       END AS problem
                  FROM users
                 WHERE (billing_status = 'canceled' AND is_subscribed = 1)
                    OR (billing_status IN ('past_due','paused') AND credits_monthly > 0)
                    OR (billing_status = 'not_in_paddle')
                    OR (trial_status = 'converted'
                        AND NOT EXISTS (SELECT 1 FROM payments p
                                         WHERE p.user_id = users.id
                                           AND p.status = 'completed'
                                           AND p.amount_cents > 0))
                    OR (is_subscribed = 1 AND subscription_id IS NULL)
                 ORDER BY id""")
            out['contradictions'] = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT p.id, p.user_id, u.email, p.transaction_id,
                       p.subscription_id, p.plan, p.status, p.amount_cents,
                       p.currency, p.origin, p.error_code, p.occurred_at
                  FROM payments p LEFT JOIN users u ON u.id = p.user_id
                 ORDER BY COALESCE(p.occurred_at, p.created_at) DESC
                 LIMIT 100""")
            out['payments'] = [dict(r) for r in cur.fetchall()]

            out['collected'] = _collected(cur)
            out['collected_30d'] = _collected(cur, since_days=30)
            cur.execute("""SELECT MAX(billing_synced_at) AS t FROM users
                            WHERE billing_synced_at IS NOT NULL""")
            out['last_synced_at'] = cur.fetchone()['t']
        return jsonify(out), 200
    finally:
        conn.close()


@admin_bp.route('/billing/sync', methods=['POST'])
@admin_required
def billing_sync_now():
    """Reconcile every subscription against Paddle, right now.

    The same code the hourly tick runs, so what it fixes on demand is exactly
    what it would have fixed on its own — there is no second implementation to
    drift. `full=1` also re-pulls every transaction, which is what you want
    once, to backfill the payments ledger from before it existed.
    """
    import billing_sync
    full = request.args.get('full') in ('1', 'true', 'yes')
    conn = get_db()
    try:
        if not billing.columns_ready(conn):
            return jsonify({'error': 'migrations/012_billing_truth.sql has '
                                     'not been applied'}), 409
        result = billing_sync.reconcile_all(conn, full=full)
        return jsonify(result), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  USER BEHAVIOR / RETENTION COHORTS
# ─────────────────────────────────────────────────────────────────────────────


@admin_bp.route('/retention', methods=['GET'])
@admin_required
def retention_cohorts():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Cohorts are gated to real post-relaunch accounts (scope enforces
            # created_at >= the metrics epoch), so only "last month onward"
            # shows and old-idea / test accounts are excluded. Activity is read
            # from video_jobs — the LIVE product — because new users never touch
            # the retired app-builder `jobs` table; measuring `jobs` would show
            # every real cohort as 0% retained.
            cur.execute("""
                WITH cohorts AS (
                    SELECT
                        id AS user_id,
                        DATE_TRUNC('week', created_at)::date AS cohort_week
                    FROM users
                    WHERE is_verified = 1
                      AND """ + _scope('') + """
                ),
                activity AS (
                    SELECT
                        user_id,
                        DATE_TRUNC('week', created_at)::date AS activity_week
                    FROM video_jobs
                    WHERE created_at >= DATE '""" + METRICS_EPOCH + """'
                    GROUP BY user_id, DATE_TRUNC('week', created_at)::date
                )
                SELECT
                    TO_CHAR(c.cohort_week, 'YYYY-MM-DD') AS cohort,
                    COUNT(DISTINCT c.user_id) AS cohort_size,
                    COUNT(DISTINCT CASE WHEN a.activity_week = c.cohort_week THEN c.user_id END) AS week_0,
                    COUNT(DISTINCT CASE WHEN a.activity_week = c.cohort_week + INTERVAL '1 week' THEN c.user_id END) AS week_1,
                    COUNT(DISTINCT CASE WHEN a.activity_week = c.cohort_week + INTERVAL '2 weeks' THEN c.user_id END) AS week_2,
                    COUNT(DISTINCT CASE WHEN a.activity_week = c.cohort_week + INTERVAL '3 weeks' THEN c.user_id END) AS week_3,
                    COUNT(DISTINCT CASE WHEN a.activity_week = c.cohort_week + INTERVAL '4 weeks' THEN c.user_id END) AS week_4
                FROM cohorts c
                LEFT JOIN activity a ON a.user_id = c.user_id
                GROUP BY c.cohort_week
                ORDER BY c.cohort_week
            """)
            rows = cur.fetchall()

        cohorts = []
        for r in rows:
            size = r['cohort_size']
            cohorts.append({
                'cohort': r['cohort'],
                'size': size,
                'retention': [
                    round((r['week_0'] / max(1, size)) * 100, 1),
                    round((r['week_1'] / max(1, size)) * 100, 1),
                    round((r['week_2'] / max(1, size)) * 100, 1),
                    round((r['week_3'] / max(1, size)) * 100, 1),
                    round((r['week_4'] / max(1, size)) * 100, 1),
                ]
            })

        return jsonify({'cohorts': cohorts}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  USER ENGAGEMENT SCORES
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/engagement', methods=['GET'])
@admin_required
def engagement():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Engagement reads the LIVE product (video_jobs) and only real
            # post-relaunch accounts — measuring the retired `jobs` table showed
            # every current user as dormant, and old-idea/test accounts inflated
            # the segment counts.
            scope_u = _scope('u')
            cur.execute("""
                SELECT
                    CASE
                        WHEN job_count >= 10 THEN 'power'
                        WHEN job_count >= 3 THEN 'active'
                        WHEN job_count >= 1 THEN 'casual'
                        ELSE 'dormant'
                    END AS segment,
                    COUNT(*) AS user_count
                FROM (
                    SELECT u.id,
                        COUNT(j.id) AS job_count
                    FROM users u
                    LEFT JOIN video_jobs j ON j.user_id = u.id AND j.created_at >= NOW() - INTERVAL '7 days'
                    WHERE u.is_verified = 1 AND """ + scope_u + """
                    GROUP BY u.id
                ) sub
                GROUP BY segment
            """)
            segments = {r['segment']: r['user_count'] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(DISTINCT j.user_id) AS dau FROM video_jobs j JOIN users u ON u.id = j.user_id WHERE j.created_at >= CURRENT_DATE AND j.created_at < CURRENT_DATE + 1 AND " + scope_u)
            dau = cur.fetchone()['dau']

            cur.execute("SELECT COUNT(DISTINCT j.user_id) AS wau FROM video_jobs j JOIN users u ON u.id = j.user_id WHERE j.created_at >= NOW() - INTERVAL '7 days' AND " + scope_u)
            wau = cur.fetchone()['wau']

            cur.execute("SELECT COUNT(DISTINCT j.user_id) AS mau FROM video_jobs j JOIN users u ON u.id = j.user_id WHERE j.created_at >= NOW() - INTERVAL '30 days' AND " + scope_u)
            mau = cur.fetchone()['mau']

            stickiness = round((dau / max(1, mau)) * 100, 1)

            cur.execute("""
                SELECT COALESCE(AVG(cnt), 0) AS avg_jobs
                FROM (
                    SELECT j.user_id, COUNT(*) AS cnt
                    FROM video_jobs j JOIN users u ON u.id = j.user_id
                    WHERE j.created_at >= NOW() - INTERVAL '7 days' AND """ + scope_u + """
                    GROUP BY j.user_id
                ) sub
            """)
            avg_jobs_per_user = round(float(cur.fetchone()['avg_jobs']), 1)

            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(a.dau, 0) AS dau
                FROM generate_series(NOW() - INTERVAL '14 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT j.created_at::date AS dt, COUNT(DISTINCT j.user_id) AS dau
                    FROM video_jobs j JOIN users u ON u.id = j.user_id
                    WHERE j.created_at >= NOW() - INTERVAL '14 days' AND """ + scope_u + """
                    GROUP BY j.created_at::date
                ) a ON a.dt = d::date
                ORDER BY d
            """)
            dau_trend = [dict(r) for r in cur.fetchall()]

        return jsonify({
            'segments': segments,
            'dau': dau,
            'wau': wau,
            'mau': mau,
            'stickiness': stickiness,
            'avg_jobs_per_active_user': avg_jobs_per_user,
            'dau_trend': dau_trend,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  PAGE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/page-analytics', methods=['GET'])
@admin_required
def page_analytics():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT page,
                    COUNT(*) AS views,
                    COUNT(DISTINCT {UNIQUE_VISITOR}) AS unique_visitors
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                GROUP BY page
                ORDER BY views DESC
                LIMIT 10
            """)
            top_pages = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(v.unique_visitors, 0) AS unique_visitors,
                    COALESCE(v.total_views, 0) AS total_views
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        visited_at::date AS dt,
                        COUNT(DISTINCT {UNIQUE_VISITOR}) AS unique_visitors,
                        COUNT(*) AS total_views
                    FROM analytics_page_visits WHERE visited_at >= NOW() - INTERVAL '30 days'
                    GROUP BY visited_at::date
                ) v ON v.dt = d::date
                ORDER BY d
            """)
            visitor_trend = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM visited_at) AS hour,
                    COUNT(*) AS views
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '7 days'
                GROUP BY hour
                ORDER BY hour
            """)
            hourly = [{'hour': int(r['hour']), 'views': r['views']} for r in cur.fetchall()]

        return jsonify({
            'top_pages': top_pages,
            'visitor_trend': visitor_trend,
            'hourly_pattern': hourly,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  REAL-TIME
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/realtime', methods=['GET'])
@admin_required
def realtime():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT COUNT(DISTINCT {UNIQUE_VISITOR}) AS active_now
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '5 minutes'
            """)
            active_now = cur.fetchone()['active_now']

            cur.execute(f"""
                SELECT COUNT(DISTINCT {UNIQUE_VISITOR}) AS active_15m
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '15 minutes'
            """)
            active_15m = cur.fetchone()['active_15m']

            # Live video-editor work (the retired app builder's `jobs`
            # table is no longer read).
            cur.execute("""
                SELECT vj.id AS job_id,
                       COALESCE(p.title, 'project ' || vj.project_id)
                           || ' · ' || vj.type AS title,
                       vj.created_at, u.email AS user_email, u.plan
                FROM video_jobs vj
                LEFT JOIN users u ON u.id = vj.user_id
                LEFT JOIN projects p ON p.id = vj.project_id
                WHERE vj.state = 'running'
                ORDER BY vj.created_at DESC
                LIMIT 50
            """)
            running_jobs = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT page, ip, visited_at, user_agent, country
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '2 minutes'
                ORDER BY visited_at DESC
                LIMIT 20
            """)
            recent_hits = [dict(r) for r in cur.fetchall()]

        return jsonify({
            'active_now': active_now,
            'active_15m': active_15m,
            'running_jobs': running_jobs,
            'recent_hits': recent_hits,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  USERS TABLE
# ─────────────────────────────────────────────────────────────────────────────

# The Users list's filters. Each is one plain question about a customer.
USER_FILTERS = {
    'all': 'TRUE',
    # Paying now, or a renewal payment is being retried.
    'subscribed': "u_status IN ('paying', 'payment_failing')",
    # Completed at least one positive payment, ever.
    'paid': "u_status IN ('paying', 'payment_failing', 'canceled')",
    # Paid before and no longer subscribed.
    'canceled': "u_status = 'canceled'",
    # Finished at least one export.
    'exported': 'u_exports > 0',
    # Never paid.
    'free': "u_status = 'free'",
}
# Plus one filter per subscriber state (admin_metrics.subscriptions:
# renewing, cancelled_access, payment_failing, ended), answered by the same
# summarize() that draws each row's status, so a filter and its rows agree.


@admin_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    """Every verified customer, newest first, with what happened to them.

    One row says: how they signed up, where they came from (tracked source
    with campaign and outreach code, and what they told us), whether they
    edited, exported and paid, and their subscription state today. The
    retired app builder's `jobs` table is no longer read: "Edits" are video
    edit requests. Filters answer the owner's questions directly: who
    subscribed, who paid, who cancelled, who exported, who never paid."""
    from admin_metrics import money, subscriptions as subs
    try:
        page = max(1, int(request.args.get('page', 1)))
        per_page = max(10, min(200, int(request.args.get('per_page', 50))))
    except (TypeError, ValueError):
        page, per_page = 1, 50
    search = request.args.get('search', '').strip()
    plan_filter = request.args.get('plan', '').strip()
    flt = request.args.get('filter', 'all').strip().lower()
    if flt not in USER_FILTERS and flt not in subs.FILTER_STATES:
        flt = 'all'
    sort = request.args.get('sort', 'created_at').strip()
    order = 'asc' if request.args.get('order', 'desc').strip() == 'asc' \
        else 'desc'
    sorts = {'created_at': 'u.created_at', 'email': 'u.email',
             'plan': 'u.plan', 'credits_balance': 'u.credits_balance',
             'last_active': 'last_active', 'paid': 'paid_cents',
             'exports': 'u_exports', 'job_count': 'job_count'}
    sort_col = sorts.get(sort, 'u.created_at')
    offset = (page - 1) * per_page

    conn = get_db()
    try:
        trial_ready = trial_state.columns_ready(conn)
        with conn.cursor() as cur:
            where = f"WHERE u.is_verified = 1 AND {_scope('u')}"
            params = []
            if search:
                where += " AND u.email ILIKE %s"
                params.append(f'%{search}%')
            if plan_filter:
                where += " AND u.plan = %s"
                params.append(plan_filter)
            trial_cols = ("""
                    u.trial_status, u.trial_started_at, u.trial_ends_at,
                    u.trial_canceled_at, u.trial_plan,
            """ if trial_ready else "")
            base = f"""
                SELECT u.*, {money.STATUS_SQL} AS u_status,
                       (SELECT COUNT(*) FROM video_jobs vf
                         WHERE vf.user_id = u.id AND vf.type = 'final'
                           AND vf.state = 'done') AS u_exports
                  FROM users u {where}"""
            states = {}
            try:
                states = subs.account_states(cur)
            except Exception:
                current_app.logger.warning("subscriber states unavailable",
                                           exc_info=True)

            def cond(name, alias):
                if name in subs.FILTER_STATES:
                    return subs.ids_in(states, name, alias)
                return (USER_FILTERS[name]
                        .replace('u_status', f'{alias}.u_status')
                        .replace('u_exports', f'{alias}.u_exports'))

            # Counts for the filter buttons and the header, over the same
            # search (the filter itself never changes them).
            names = (('subscribed', 'paid', 'canceled', 'exported', 'free')
                     + tuple(subs.FILTER_STATES))
            cur.execute(f"""
                WITH b AS ({base})
                SELECT COUNT(*) AS all_,
                       {", ".join(f"COUNT(*) FILTER (WHERE {cond(n, 'b')}) AS {n}"
                                  for n in names)}
                  FROM b""", params)
            c = cur.fetchone() or {}
            counts = {'all': c.get('all_') or 0}
            counts.update({k: c.get(k) or 0 for k in names})
            total = counts.get(flt, counts['all'])

            day0, week0 = _local_day_bounds()
            cur.execute(f"""SELECT COUNT(*) FILTER (WHERE u.created_at >= %s) AS today,
                                   COUNT(*) FILTER (WHERE u.created_at >= %s) AS week
                              FROM users u WHERE u.is_verified = 1 AND {_scope('u')}""",
                        (day0, week0))
            fresh = cur.fetchone() or {}

            cur.execute(f"""
                WITH b AS ({base})
                SELECT
                    u.id, u.email, u.plan, u.is_subscribed,
                    u.subscription_id, u.payment_failed_at,
                    u.billing_synced_at,
                    to_jsonb(u)->>'billing_period' AS billing_period,
                    to_jsonb(u)->>'billing_plan' AS billing_plan,
                    to_jsonb(u)->>'billing_status' AS billing_status,
                    u.is_verified, u.auth_provider,
                    {trial_cols}
                    u.credits_balance, u.credits_daily, u.credits_monthly,
                    u.created_at, u.subscription_expiry,
                    u.device_type, u.device_browser, u.last_seen_at,
                    u.u_status AS status, u.u_exports AS exports,
                    (SELECT ws.attribution FROM website_signups ws
                      WHERE ws.user_id = u.id) AS acquisition,
                    (SELECT o.channel FROM onboarding_responses o
                      WHERE o.user_id = u.id) AS channel,
                    (SELECT o.channel_other FROM onboarding_responses o
                      WHERE o.user_id = u.id) AS channel_other,
                    (SELECT o.use_case FROM onboarding_responses o
                      WHERE o.user_id = u.id) AS use_case,
                    (SELECT COUNT(*) FROM projects p
                      WHERE p.user_id = u.id
                        AND p.parent_project_id IS NULL) AS projects,
                    -- Edit requests in the video editor (the retired app
                    -- builder is not read any more).
                    (SELECT COUNT(*) FROM video_jobs vj
                      WHERE vj.user_id = u.id
                        AND vj.type = 'agent_turn') AS job_count,
                    -- Credits straight off the ledger by user_id. Never write
                    -- a literal percent sign in this string (psycopg2 reads
                    -- it as a placeholder).
                    (SELECT COALESCE(SUM(jc.credits_used), 0)
                       FROM job_credits jc WHERE jc.user_id = u.id)
                        AS total_credits_used,
                    pay.first_paid_at, pay.last_paid_at, pay.payments,
                    COALESCE(pay.cents, 0) AS paid_cents,
                    GREATEST(u.last_seen_at,
                             (SELECT MAX(vj.created_at) FROM video_jobs vj
                               WHERE vj.user_id = u.id)) AS last_active
                FROM b u
                LEFT JOIN LATERAL (
                    SELECT MIN(COALESCE(p.occurred_at, p.created_at)) AS first_paid_at,
                           MAX(COALESCE(p.occurred_at, p.created_at)) AS last_paid_at,
                           COUNT(*) AS payments,
                           SUM(p.amount_cents) FILTER (WHERE p.currency = 'USD') AS cents
                      FROM payments p
                     WHERE p.user_id = u.id AND p.status IN ('completed', 'paid')
                       AND p.amount_cents > 0) pay ON TRUE
                WHERE {cond(flt, 'u')}
                ORDER BY {sort_col} {order} NULLS LAST, u.id DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            rows = [dict(r) for r in cur.fetchall()]
            # Each row's one subscriber status (same as Subscribers/People).
            journeys = {}
            try:
                journeys = subs.for_customers(
                    cur, [r for r in rows if r['id'] in states])
            except Exception:
                current_app.logger.warning("user renewal lookup failed",
                                           exc_info=True)

        users = []
        for row in rows:
            row['paid_usd'] = round(float(row.pop('paid_cents') or 0) / 100.0, 2)
            for k in ('subscription_id', 'payment_failed_at',
                      'billing_synced_at'):
                row.pop(k, None)
            j = journeys.get(row['id']) or {}
            row.update({k: j.get(k) for k in (
                'renewal_state', 'renewal_at', 'subscriber_kind',
                'returned_at')})
            if not j and states:
                row['renewal_state'] = 'none'
            users.append(row)
        return jsonify({
            'users': users,
            'total': total,
            'real_total': counts['all'],
            'counts': counts,
            'new_today': fresh.get('today') or 0,
            'new_week': fresh.get('week') or 0,
            'filter': flt,
            'page': page,
            'per_page': per_page,
            'timezone': 'Asia/Dubai',
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  TOP USERS
# ─────────────────────────────────────────────────────────────────────────────


@admin_bp.route('/top-users', methods=['GET'])
@admin_required
def top_users():
    """Customers ranked by credits used, with their video edit requests.
    Only the video editor is read (the retired app builder is not)."""
    def compute(cur):
        cur.execute("""
            SELECT u.id, u.email, u.plan,
                   (SELECT COUNT(*) FROM video_jobs vj
                     WHERE vj.user_id = u.id
                       AND vj.type = 'agent_turn') AS jobs,
                   ROUND(COALESCE(SUM(jc.credits_used), 0)::numeric, 2)
                       AS credits_used,
                   COALESCE(SUM(jc.tokens_used), 0) AS tokens_used,
                   (SELECT MAX(vj.created_at) FROM video_jobs vj
                     WHERE vj.user_id = u.id) AS last_active
              FROM users u
              LEFT JOIN job_credits jc ON jc.user_id = u.id
             WHERE u.is_verified = 1 AND """ + _scope('u') + """
             GROUP BY u.id, u.email, u.plan
             ORDER BY credits_used DESC
             LIMIT 15
        """)
        return {'users': [dict(r, credits_used=float(r['credits_used'] or 0),
                               tokens_used=int(r['tokens_used'] or 0))
                          for r in cur.fetchall()]}
    return jsonify(_classic_cached('top_users', 120, compute)), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ACTIVITY FEED
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/activity', methods=['GET'])
@admin_required
def recent_activity():
    """What just happened, from the live product only: signups, payments
    (first payment = subscribed, later ones = renewals), cancellations,
    exports and failed edit requests. The retired app builder's jobs are
    gone, and a subscription is dated by its payment, not by its expiry."""
    def compute(cur):
        from admin_metrics import defs
        scope_u = _scope('u')
        cur.execute(f"""
            SELECT 'register' AS type, u.email AS label, u.created_at AS ts
              FROM users u WHERE u.is_verified = 1 AND {scope_u}
             ORDER BY u.created_at DESC LIMIT 12""")
        events = [dict(r) for r in cur.fetchall()]
        cur.execute(f"""
            SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM payments e WHERE e.user_id = p.user_id
                           AND {defs.success('e')}
                           AND COALESCE(e.occurred_at, e.created_at)
                               < COALESCE(p.occurred_at, p.created_at))
                        THEN 'subscribe' ELSE 'renewal' END AS type,
                   u.email || ' paid $' || TO_CHAR(p.amount_cents / 100.0, 'FM999990.00')
                       || ' (' || COALESCE(p.plan, u.billing_plan, u.plan) || ')' AS label,
                   COALESCE(p.occurred_at, p.created_at) AS ts
              FROM payments p JOIN users u ON u.id = p.user_id
             WHERE {defs.success('p')}
             ORDER BY COALESCE(p.occurred_at, p.created_at) DESC LIMIT 12""")
        events += [dict(r) for r in cur.fetchall()]
        from admin_metrics import db as mdb
        snap = mdb.has_table(cur, 'admin_subscription_snapshots')
        canceled_at = ("COALESCE(s.canceled_at, u.billing_synced_at)" if snap
                       else "u.billing_synced_at")
        join = ("LEFT JOIN admin_subscription_snapshots s "
                "ON s.subscription_id = u.subscription_id" if snap else "")
        cur.execute(f"""
            SELECT 'cancel' AS type,
                   u.email || ' cancelled (' || COALESCE(u.billing_plan, u.plan) || ')' AS label,
                   {canceled_at} AS ts
              FROM users u {join}
             WHERE u.billing_status = 'canceled' AND {canceled_at} IS NOT NULL
             ORDER BY 3 DESC LIMIT 8""")
        events += [dict(r) for r in cur.fetchall()]
        cur.execute(f"""
            SELECT CASE WHEN vj.state = 'done' THEN 'export' ELSE 'job_fail' END AS type,
                   u.email || ' → ' || COALESCE(p.title, 'project ' || vj.project_id)
                       || CASE WHEN vj.state = 'done' THEN '' ELSE ' (export failed)' END AS label,
                   vj.updated_at AS ts
              FROM video_jobs vj JOIN users u ON u.id = vj.user_id
              LEFT JOIN projects p ON p.id = vj.project_id
             WHERE vj.type = 'final' AND vj.state IN ('done', 'failed')
               AND {scope_u}
             ORDER BY vj.updated_at DESC LIMIT 12""")
        events += [dict(r) for r in cur.fetchall()]
        for e in events:
            ts = e.get('ts')
            if ts is not None and getattr(ts, 'tzinfo', None) is None:
                e['ts'] = defs.iso(ts)
            elif ts is not None:
                e['ts'] = defs.iso(ts)
        events.sort(key=lambda x: str(x.get('ts') or ''), reverse=True)
        return {'events': events[:40]}
    return jsonify(_classic_cached('activity', 30, compute)), 200


# ─────────────────────────────────────────────────────────────────────────────
#  COUNTRY STATS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/country-stats', methods=['GET'])
@admin_required
def country_stats():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT country,
                    COUNT(*) as visits,
                    COUNT(DISTINCT {UNIQUE_VISITOR}) as unique_visitors
                FROM analytics_page_visits
                WHERE country IS NOT NULL AND country != 'Unknown'
                  AND visited_at >= NOW() - INTERVAL '30 days'
                GROUP BY country
                ORDER BY unique_visitors DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
        return jsonify({"countries": [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION & DEVICE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/session-stats', methods=['GET'])
@admin_required
def session_stats():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT referrer_source,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT {UNIQUE_VISITOR}) AS unique_visitors
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND referrer_source IS NOT NULL
                GROUP BY referrer_source
                ORDER BY unique_visitors DESC
            """)
            referrer_breakdown = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT device_type,
                    COUNT(DISTINCT {UNIQUE_VISITOR}) AS unique_devices
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND device_type IS NOT NULL
                GROUP BY device_type
                ORDER BY unique_devices DESC
            """)
            device_breakdown = [dict(r) for r in cur.fetchall()]

            cur.execute(f"""
                SELECT browser,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT {UNIQUE_VISITOR}) AS unique_visitors
                FROM analytics_page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND browser IS NOT NULL
                GROUP BY browser
                ORDER BY unique_visitors DESC
            """)
            browser_breakdown = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT COALESCE(AVG(duration), 0) AS avg_duration,
                       COALESCE(AVG(pages), 0) AS avg_pages
                FROM (
                    SELECT session_id,
                        EXTRACT(EPOCH FROM (MAX(visited_at) - MIN(visited_at))) AS duration,
                        COUNT(*) AS pages
                    FROM analytics_page_visits
                    WHERE session_id IS NOT NULL AND session_id != ''
                      AND visited_at >= NOW() - INTERVAL '30 days'
                    GROUP BY session_id
                    HAVING COUNT(*) > 1
                ) sub
            """)
            session_row = cur.fetchone()

            cur.execute("""
                SELECT page, ROUND(AVG(time_on_page)) AS avg_time, COUNT(*) AS visits
                FROM analytics_page_visits
                WHERE time_on_page > 2
                  AND visited_at >= NOW() - INTERVAL '30 days'
                GROUP BY page
                ORDER BY visits DESC
                LIMIT 10
            """)
            page_times = [dict(r) for r in cur.fetchall()]

        return jsonify({
            'referrer_breakdown': referrer_breakdown,
            'device_breakdown': device_breakdown,
            'browser_breakdown': browser_breakdown,
            'avg_session_duration': round(float(session_row['avg_duration']), 0),
            'avg_pages_per_session': round(float(session_row['avg_pages']), 1),
            'page_times': page_times,
        }), 200
    finally:
        conn.close()


# Retired app-builder endpoints (engine-stats, builds, jobs,
# job/<id>/conversation, charts/jobs, charts/credits) were removed: they read
# the empty `jobs` table or a deleted folder, one crashed, and no screen used
# them. Their URLs now answer 404.


@admin_bp.route('/editing-provider-readiness', methods=['POST'])
@admin_required
def editing_provider_readiness():
    """Verify the model required by new Advanced checkouts, without a project."""
    from provider_readiness import advanced_readiness
    result = advanced_readiness()
    return jsonify(result), (200 if result['ready'] else 503)


@admin_bp.route('/conversion', methods=['GET'])
@admin_required
def conversion_report():
    from video_services.conversion_report import read_report
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15s'")
            return jsonify(read_report(cur, _scope('u')))
    except Exception:
        current_app.logger.exception('Conversion report unavailable')
        return jsonify({'error': 'Conversion data is temporarily unavailable. Retry shortly.'}), 503
    finally:
        conn.close()


@admin_bp.route('/acquisition', methods=['GET'])
@admin_required
def acquisition_report():
    from acquisition import report
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='8s'")
            data = report(cur, _scope('u'))
            sources = report(cur, _scope('u'), group_by='source')
            totals = report(cur, _scope('u'), group_by='all')
            data.update(sources=sources['rows'], totals=totals['rows'],
                        sources_truncated=sources['truncated'], reporting_period='all_time')
            return jsonify(data)
    finally:
        conn.close()


@admin_bp.route('/outreach-conversions', methods=['POST'])
def outreach_conversions():
    """Narrow CRM credential: aggregate counts for supplied opaque codes only."""
    import hashlib
    import hmac
    from acquisition import CODE, report
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer ') or not 32 <= len(auth[7:]) <= 128:
        return jsonify({'error': 'unauthorized'}), 401
    if request.content_length and request.content_length > 32768:
        return jsonify({'error': 'payload too large'}), 413
    data = request.get_json(silent=True)
    codes = data.get('codes') if isinstance(data, dict) else None
    if not isinstance(codes, list) or not 1 <= len(codes) <= 500 or any(not isinstance(c, str) or not CODE.fullmatch(c) for c in codes):
        return jsonify({'error': 'invalid codes'}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout='8s'")
            cur.execute("SELECT value FROM app_kv WHERE key='outreach_conversion_key_sha256'")
            row = cur.fetchone()
            digest = hashlib.sha256(auth[7:].encode()).hexdigest()
            if not row or not hmac.compare_digest(str(row['value']), digest):
                return jsonify({'error': 'unauthorized'}), 401
            # Version 2: every v1 key keeps its name and meaning (`visitors`
            # stays the raw browser count for the transition), and each row
            # adds `people` and `link_previews` (Meta's DM previews are not
            # people), plus `channel` and `campaign_label`.
            return jsonify(report(cur, _scope('u'), list(set(codes)),
                                  with_people=True))
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  OUTREACH CODES — the CRM's links traced to people and signups
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/outreach-codes', methods=['GET'])
@admin_required
def outreach_codes():
    """One row per outreach code (`valmera.io/?r=<code>`) opened or signed up
    with in the last `days` admin days: people vs link checks, pages read and
    the accounts that came through it. Read-only; the CRM is never called."""
    from admin_metrics import classic
    try:
        days = max(1, min(400, int(request.args.get('days', 30))))
    except (TypeError, ValueError):
        days = 30
    return jsonify(_classic_cached(
        'outreach_codes', 300, lambda cur: classic.outreach_codes(cur, days),
        params={'days': days})), 200

