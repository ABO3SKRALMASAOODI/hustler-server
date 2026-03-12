from flask import Blueprint, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app
import os
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)

ADMIN_EMAIL = "thehustlerbot@gmail.com"


def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)


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
#  TRACK PAGE VISIT (unchanged — public endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/track', methods=['POST'])
def track_visit():
    data = request.get_json() or {}
    page = data.get('page', '/')
    referrer = data.get('referrer', '')[:500]
    session_id = data.get('session_id', '')[:64]
    time_on_page = int(data.get('time_on_page', 0))
    device_id = data.get('device_id', '')[:64]
    ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    user_agent = request.headers.get('User-Agent', '')[:300]

    def _parse_referrer(ref):
        if not ref or ref == '':
            return 'direct'
        ref_lower = ref.lower()
        if any(x in ref_lower for x in ['google.', 'bing.', 'yahoo.', 'duckduckgo.', 'baidu.']):
            return 'search'
        if any(x in ref_lower for x in ['facebook.', 'twitter.', 'x.com', 'instagram.', 'linkedin.', 'tiktok.', 'reddit.', 'youtube.']):
            return 'social'
        if 'thehustlerbot.com' in ref_lower:
            return 'internal'
        return 'referral'

    def _parse_device(ua):
        ua_lower = ua.lower()
        if any(x in ua_lower for x in ['iphone', 'android', 'mobile', 'blackberry', 'windows phone']):
            return 'mobile'
        if any(x in ua_lower for x in ['ipad', 'tablet']):
            return 'tablet'
        return 'desktop'

    def _parse_browser(ua):
        ua_lower = ua.lower()
        if 'edg/' in ua_lower or 'edge/' in ua_lower:
            return 'Edge'
        if 'opr/' in ua_lower or 'opera' in ua_lower:
            return 'Opera'
        if 'chrome/' in ua_lower and 'chromium' not in ua_lower:
            return 'Chrome'
        if 'firefox/' in ua_lower:
            return 'Firefox'
        if 'safari/' in ua_lower and 'chrome' not in ua_lower:
            return 'Safari'
        return 'Other'

    def _save(app, page, ip, user_agent, referrer, session_id, time_on_page, device_id):
        bot_signatures = [
            'vercel-screenshot', 'googlebot', 'bingbot', 'slurp', 'duckduckbot',
            'baiduspider', 'yandexbot', 'sogou', 'exabot', 'facebot',
            'ia_archiver', 'semrushbot', 'ahrefsbot', 'mj12bot', 'dotbot',
            'petalbot', 'bytespider', 'gptbot', 'claudebot', 'ccbot',
        ]
        ua_lower = user_agent.lower()
        if any(bot in ua_lower for bot in bot_signatures):
            return

        country = 'Unknown'
        try:
            import urllib.request as _ur
            import json as _json
            with _ur.urlopen(
                f'http://ip-api.com/json/{ip}?fields=status,country',
                timeout=2
            ) as r:
                geo = _json.loads(r.read())
                if geo.get('status') == 'success':
                    country = geo.get('country', 'Unknown')
        except Exception:
            pass

        referrer_source = _parse_referrer(referrer)
        device_type = _parse_device(user_agent)
        browser = _parse_browser(user_agent)

        with app.app_context():
            conn = psycopg2.connect(app.config['DATABASE_URL'], cursor_factory=RealDictCursor)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO page_visits
                           (page, ip, user_agent, country, referrer, referrer_source,
                            session_id, device_type, browser, time_on_page, device_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (page, ip, user_agent, country, referrer, referrer_source,
                         session_id, device_type, browser, time_on_page, device_id)
                    )
                    conn.commit()
            finally:
                conn.close()

    import threading
    app = current_app._get_current_object()
    threading.Thread(
        target=_save,
        args=(app, page, ip, user_agent, referrer, session_id, time_on_page, device_id),
        daemon=True
    ).start()

    return jsonify({'ok': True}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  OVERVIEW KPIs — Enhanced with more metrics
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/overview', methods=['GET'])
@admin_required
def overview():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # ── Users ──
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1")
            total_users = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at::date = CURRENT_DATE")
            new_users_today = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '7 days'")
            new_users_week = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '30 days'")
            new_users_month = cur.fetchone()['total']

            # Previous period comparisons for trends
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '14 days' AND created_at < NOW() - INTERVAL '7 days'")
            prev_week_users = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '60 days' AND created_at < NOW() - INTERVAL '30 days'")
            prev_month_users = cur.fetchone()['total']

            # ── Subscriptions ──
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_subscribed = 1")
            total_subscribed = cur.fetchone()['total']

            # Plan breakdown
            cur.execute("""
                SELECT plan, COUNT(*) AS count FROM users
                WHERE is_verified = 1
                GROUP BY plan
            """)
            plan_breakdown = {r['plan']: r['count'] for r in cur.fetchall()}

            plan_prices = {'plus': 20, 'pro': 50, 'ultra': 100}
            mrr = sum(plan_prices.get(p, 0) * c for p, c in plan_breakdown.items())

            # Conversion rate: subscribed / total verified
            conversion_rate = round((total_subscribed / max(1, total_users)) * 100, 1)

            # ── Jobs ──
            cur.execute("SELECT COUNT(*) AS total FROM jobs")
            total_jobs = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE created_at::date = CURRENT_DATE")
            jobs_today = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE created_at >= NOW() - INTERVAL '7 days'")
            jobs_week = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'running'")
            jobs_running = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'failed' AND created_at::date = CURRENT_DATE")
            jobs_failed_today = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'completed'")
            jobs_completed_total = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'failed'")
            jobs_failed_total = cur.fetchone()['total']

            success_rate = round((jobs_completed_total / max(1, jobs_completed_total + jobs_failed_total)) * 100, 1)

            # ── Visits ──
            cur.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_total FROM page_visits WHERE visited_at::date = CURRENT_DATE")
            _r = cur.fetchone(); visits_today = _r['total']; unique_today = _r['unique_total']

            cur.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_total FROM page_visits WHERE visited_at >= NOW() - INTERVAL '7 days'")
            _r = cur.fetchone(); visits_week = _r['total']; unique_week = _r['unique_total']

            cur.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_total FROM page_visits WHERE visited_at >= NOW() - INTERVAL '30 days'")
            _r = cur.fetchone(); visits_month = _r['total']; unique_month = _r['unique_total']

            # Previous week visits for trend
            cur.execute("SELECT COUNT(*) AS total FROM page_visits WHERE visited_at >= NOW() - INTERVAL '14 days' AND visited_at < NOW() - INTERVAL '7 days'")
            prev_week_visits = cur.fetchone()['total']

            # ── Credits ──
            cur.execute("SELECT COALESCE(SUM(credits_used), 0) AS total FROM job_credits WHERE created_at::date = CURRENT_DATE")
            credits_today = float(cur.fetchone()['total'])

            cur.execute("SELECT COALESCE(SUM(credits_used), 0) AS total FROM job_credits WHERE created_at >= NOW() - INTERVAL '7 days'")
            credits_week = float(cur.fetchone()['total'])

            cur.execute("SELECT COALESCE(SUM(tokens_used), 0) AS total FROM job_credits")
            total_tokens = int(cur.fetchone()['total'])

            cur.execute("SELECT COALESCE(SUM(tokens_used), 0) AS total FROM job_credits WHERE created_at::date = CURRENT_DATE")
            tokens_today = int(cur.fetchone()['total'])

            # Avg credits per job
            cur.execute("""
                SELECT COALESCE(AVG(total_cred), 0) AS avg_credits
                FROM (
                    SELECT job_id, SUM(credits_used) AS total_cred
                    FROM job_credits
                    GROUP BY job_id
                ) sub
            """)
            avg_credits_per_job = round(float(cur.fetchone()['avg_credits']), 2)

            # ── Compute trends (percentage change vs previous period) ──
            def trend(current, previous):
                if previous == 0:
                    return 100 if current > 0 else 0
                return round(((current - previous) / previous) * 100, 1)

            users_trend = trend(new_users_week, prev_week_users)
            visits_trend = trend(visits_week, prev_week_visits)

        return jsonify({
            'users': {
                'total': total_users,
                'new_today': new_users_today,
                'new_week': new_users_week,
                'new_month': new_users_month,
                'trend_week': users_trend,
            },
            'subscriptions': {
                'total': total_subscribed,
                'plan_breakdown': plan_breakdown,
                'mrr': mrr,
                'conversion_rate': conversion_rate,
            },
            'jobs': {
                'total': total_jobs,
                'today': jobs_today,
                'week': jobs_week,
                'running': jobs_running,
                'failed_today': jobs_failed_today,
                'completed_total': jobs_completed_total,
                'failed_total': jobs_failed_total,
                'success_rate': success_rate,
            },
            'visits': {
                'today': visits_today,
                'unique_today': unique_today,
                'week': visits_week,
                'unique_week': unique_week,
                'month': visits_month,
                'unique_month': unique_month,
                'trend_week': visits_trend,
            },
            'credits': {
                'consumed_today': credits_today,
                'consumed_week': credits_week,
                'total_tokens': total_tokens,
                'tokens_today': tokens_today,
                'avg_per_job': avg_credits_per_job,
            }
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Registrations (30d)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/registrations', methods=['GET'])
@admin_required
def chart_registrations():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(c.count, 0) AS count
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT created_at::date AS dt, COUNT(*) AS count
                    FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY created_at::date
                ) c ON c.dt = d::date
                ORDER BY d
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Jobs (30d)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/jobs', methods=['GET'])
@admin_required
def chart_jobs():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(j.total, 0) AS total,
                    COALESCE(j.completed, 0) AS completed,
                    COALESCE(j.failed, 0) AS failed
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        created_at::date AS dt,
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE state = 'completed') AS completed,
                        COUNT(*) FILTER (WHERE state = 'failed') AS failed
                    FROM jobs WHERE created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY created_at::date
                ) j ON j.dt = d::date
                ORDER BY d
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Visits (30d)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/visits', methods=['GET'])
@admin_required
def chart_visits():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(v.total, 0) AS count,
                    COALESCE(v.unique_visitors, 0) AS unique_visitors
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT visited_at::date AS dt,
                        COUNT(*) AS total,
                        COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_visitors
                    FROM page_visits WHERE visited_at >= NOW() - INTERVAL '30 days'
                    GROUP BY visited_at::date
                ) v ON v.dt = d::date
                ORDER BY d
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — Credits (30d)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/credits', methods=['GET'])
@admin_required
def chart_credits():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(c.credits, 0) AS credits,
                    COALESCE(c.tokens, 0) AS tokens
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        created_at::date AS dt,
                        ROUND(SUM(credits_used)::numeric, 2) AS credits,
                        SUM(tokens_used) AS tokens
                    FROM job_credits WHERE created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY created_at::date
                ) c ON c.dt = d::date
                ORDER BY d
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS — MRR Over Time (30d)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/charts/mrr', methods=['GET'])
@admin_required
def chart_mrr():
    """
    Approximate MRR over the last 30 days by counting active subscribers per day.
    Uses subscription_expiry to determine if a user was subscribed on a given day.
    """
    plan_prices = {'plus': 20, 'pro': 50, 'ultra': 100}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(SUM(CASE
                        WHEN u.plan = 'plus' THEN 20
                        WHEN u.plan = 'pro' THEN 50
                        WHEN u.plan = 'ultra' THEN 100
                        ELSE 0
                    END), 0) AS mrr,
                    COUNT(u.id) AS subscribers
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN users u ON u.is_subscribed = 1
                    AND u.subscription_expiry > d::date
                    AND u.created_at <= d::date + INTERVAL '1 day'
                GROUP BY d
                ORDER BY d
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  REVENUE ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/revenue', methods=['GET'])
@admin_required
def revenue_analytics():
    plan_prices = {'plus': 20, 'pro': 50, 'ultra': 100}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Current MRR
            cur.execute("SELECT plan, COUNT(*) AS count FROM users WHERE is_subscribed = 1 GROUP BY plan")
            plan_counts = {r['plan']: r['count'] for r in cur.fetchall()}
            mrr = sum(plan_prices.get(p, 0) * c for p, c in plan_counts.items())

            # ARR
            arr = mrr * 12

            # Average revenue per user (all verified)
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1")
            total_verified = cur.fetchone()['total']
            arpu = round(mrr / max(1, total_verified), 2)

            # Average revenue per paying user
            total_paying = sum(plan_counts.get(p, 0) for p in ['plus', 'pro', 'ultra'])
            arppu = round(mrr / max(1, total_paying), 2)

            # LTV estimate (assume 6 month avg retention)
            ltv = round(arppu * 6, 2)

            # Conversion funnel: visitors -> registered -> subscribed
            cur.execute("SELECT COUNT(DISTINCT ip) AS total FROM page_visits WHERE visited_at >= NOW() - INTERVAL '30 days'")
            unique_visitors_30d = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '30 days'")
            registered_30d = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_subscribed = 1 AND created_at >= NOW() - INTERVAL '30 days'")
            subscribed_30d = cur.fetchone()['total']

            # Revenue by plan
            revenue_by_plan = {}
            for plan, count in plan_counts.items():
                price = plan_prices.get(plan, 0)
                revenue_by_plan[plan] = {
                    'count': count,
                    'revenue': price * count,
                    'price': price,
                }

        return jsonify({
            'mrr': mrr,
            'arr': arr,
            'arpu': arpu,
            'arppu': arppu,
            'ltv_estimate': ltv,
            'total_paying': total_paying,
            'total_verified': total_verified,
            'revenue_by_plan': revenue_by_plan,
            'funnel': {
                'visitors_30d': unique_visitors_30d,
                'registered_30d': registered_30d,
                'subscribed_30d': subscribed_30d,
                'visitor_to_register': round((registered_30d / max(1, unique_visitors_30d)) * 100, 1),
                'register_to_subscribe': round((subscribed_30d / max(1, registered_30d)) * 100, 1),
            }
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  AI / ENGINE PERFORMANCE
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/engine-stats', methods=['GET'])
@admin_required
def engine_stats():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Average tokens per job
            cur.execute("""
                SELECT
                    COALESCE(AVG(total_tokens), 0) AS avg_tokens,
                    COALESCE(AVG(total_credits), 0) AS avg_credits,
                    COALESCE(MAX(total_tokens), 0) AS max_tokens,
                    COALESCE(MIN(total_tokens), 0) AS min_tokens
                FROM (
                    SELECT job_id,
                        SUM(tokens_used) AS total_tokens,
                        SUM(credits_used) AS total_credits
                    FROM job_credits
                    GROUP BY job_id
                ) sub
            """)
            token_stats = cur.fetchone()

            # Token breakdown by type (input vs output vs cache)
            cur.execute("""
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS total_input,
                    COALESCE(SUM(output_tokens), 0) AS total_output,
                    COALESCE(SUM(cache_write_tokens), 0) AS total_cache_write,
                    COALESCE(SUM(cache_read_tokens), 0) AS total_cache_read,
                    COALESCE(SUM(tokens_used), 0) AS total_all
                FROM job_credits
            """)
            token_breakdown = cur.fetchone()

            # Jobs by hour of day (last 30 days)
            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM created_at) AS hour,
                    COUNT(*) AS count
                FROM jobs
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY hour
                ORDER BY hour
            """)
            hourly = [{'hour': int(r['hour']), 'count': r['count']} for r in cur.fetchall()]

            # Success rate over last 7 days (daily)
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(j.completed, 0) AS completed,
                    COALESCE(j.failed, 0) AS failed,
                    CASE WHEN COALESCE(j.completed, 0) + COALESCE(j.failed, 0) > 0
                        THEN ROUND(COALESCE(j.completed, 0)::numeric /
                            (COALESCE(j.completed, 0) + COALESCE(j.failed, 0)) * 100, 1)
                        ELSE 100
                    END AS success_rate
                FROM generate_series(NOW() - INTERVAL '7 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        created_at::date AS dt,
                        COUNT(*) FILTER (WHERE state = 'completed') AS completed,
                        COUNT(*) FILTER (WHERE state = 'failed') AS failed
                    FROM jobs WHERE created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY created_at::date
                ) j ON j.dt = d::date
                ORDER BY d
            """)
            daily_success = [dict(r) for r in cur.fetchall()]

            # Average turns per job (number of credit entries)
            cur.execute("""
                SELECT COALESCE(AVG(turns), 0) AS avg_turns
                FROM (
                    SELECT job_id, COUNT(*) AS turns
                    FROM job_credits
                    GROUP BY job_id
                ) sub
            """)
            avg_turns = round(float(cur.fetchone()['avg_turns']), 1)

            # Token cost trend (last 30 days)
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(t.input_tokens, 0) AS input_tokens,
                    COALESCE(t.output_tokens, 0) AS output_tokens,
                    COALESCE(t.cache_read, 0) AS cache_read,
                    COALESCE(t.cache_write, 0) AS cache_write
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        created_at::date AS dt,
                        SUM(input_tokens) AS input_tokens,
                        SUM(output_tokens) AS output_tokens,
                        SUM(cache_read_tokens) AS cache_read,
                        SUM(cache_write_tokens) AS cache_write
                    FROM job_credits WHERE created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY created_at::date
                ) t ON t.dt = d::date
                ORDER BY d
            """)
            token_trend = [dict(r) for r in cur.fetchall()]

        return jsonify({
            'avg_tokens_per_job': round(float(token_stats['avg_tokens']), 0),
            'avg_credits_per_job': round(float(token_stats['avg_credits']), 2),
            'max_tokens_job': int(token_stats['max_tokens']),
            'min_tokens_job': int(token_stats['min_tokens']),
            'avg_turns_per_job': avg_turns,
            'token_breakdown': {
                'input': int(token_breakdown['total_input']),
                'output': int(token_breakdown['total_output']),
                'cache_write': int(token_breakdown['total_cache_write']),
                'cache_read': int(token_breakdown['total_cache_read']),
                'total': int(token_breakdown['total_all']),
            },
            'hourly_distribution': hourly,
            'daily_success_rate': daily_success,
            'token_trend': token_trend,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  USER BEHAVIOR / RETENTION COHORTS
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/retention', methods=['GET'])
@admin_required
def retention_cohorts():
    """
    Weekly cohort retention: for users who registered in each week,
    what % created a job in subsequent weeks.
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH cohorts AS (
                    SELECT
                        id AS user_id,
                        DATE_TRUNC('week', created_at)::date AS cohort_week
                    FROM users
                    WHERE is_verified = 1
                      AND created_at >= NOW() - INTERVAL '8 weeks'
                ),
                activity AS (
                    SELECT
                        user_id,
                        DATE_TRUNC('week', created_at)::date AS activity_week
                    FROM jobs
                    WHERE created_at >= NOW() - INTERVAL '8 weeks'
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
            # Users by engagement level (jobs in last 7 days)
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
                        COUNT(j.job_id) AS job_count
                    FROM users u
                    LEFT JOIN jobs j ON j.user_id = u.id AND j.created_at >= NOW() - INTERVAL '7 days'
                    WHERE u.is_verified = 1
                    GROUP BY u.id
                ) sub
                GROUP BY segment
            """)
            segments = {r['segment']: r['user_count'] for r in cur.fetchall()}

            # DAU (distinct users who created a job today)
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) AS dau
                FROM jobs WHERE created_at::date = CURRENT_DATE
            """)
            dau = cur.fetchone()['dau']

            # WAU
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) AS wau
                FROM jobs WHERE created_at >= NOW() - INTERVAL '7 days'
            """)
            wau = cur.fetchone()['wau']

            # MAU
            cur.execute("""
                SELECT COUNT(DISTINCT user_id) AS mau
                FROM jobs WHERE created_at >= NOW() - INTERVAL '30 days'
            """)
            mau = cur.fetchone()['mau']

            # DAU/MAU ratio (stickiness)
            stickiness = round((dau / max(1, mau)) * 100, 1)

            # Average jobs per active user (last 7 days)
            cur.execute("""
                SELECT COALESCE(AVG(cnt), 0) AS avg_jobs
                FROM (
                    SELECT user_id, COUNT(*) AS cnt
                    FROM jobs WHERE created_at >= NOW() - INTERVAL '7 days'
                    GROUP BY user_id
                ) sub
            """)
            avg_jobs_per_user = round(float(cur.fetchone()['avg_jobs']), 1)

            # DAU over last 14 days
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(a.dau, 0) AS dau
                FROM generate_series(NOW() - INTERVAL '14 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT created_at::date AS dt, COUNT(DISTINCT user_id) AS dau
                    FROM jobs WHERE created_at >= NOW() - INTERVAL '14 days'
                    GROUP BY created_at::date
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
#  PAGE ANALYTICS — which pages are most visited
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/page-analytics', methods=['GET'])
@admin_required
def page_analytics():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Top pages (last 30 days)
            cur.execute("""
                SELECT page,
                    COUNT(*) AS views,
                    COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_visitors
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                GROUP BY page
                ORDER BY views DESC
                LIMIT 10
            """)
            top_pages = [dict(r) for r in cur.fetchall()]

            # Unique visitors by day
            cur.execute("""
                SELECT
                    TO_CHAR(d::date, 'YYYY-MM-DD') AS day,
                    COALESCE(v.unique_visitors, 0) AS unique_visitors,
                    COALESCE(v.total_views, 0) AS total_views
                FROM generate_series(NOW() - INTERVAL '30 days', NOW(), '1 day') AS d
                LEFT JOIN (
                    SELECT
                        visited_at::date AS dt,
                        COUNT(DISTINCT ip) AS unique_visitors,
                        COUNT(*) AS total_views
                    FROM page_visits WHERE visited_at >= NOW() - INTERVAL '30 days'
                    GROUP BY visited_at::date
                ) v ON v.dt = d::date
                ORDER BY d
            """)
            visitor_trend = [dict(r) for r in cur.fetchall()]

            # Hourly visit pattern
            cur.execute("""
                SELECT
                    EXTRACT(HOUR FROM visited_at) AS hour,
                    COUNT(*) AS views
                FROM page_visits
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
#  REAL-TIME — Currently active sessions (approximation)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/realtime', methods=['GET'])
@admin_required
def realtime():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Active visitors in last 5 minutes
            cur.execute("""
                SELECT COUNT(DISTINCT ip) AS active_now
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '5 minutes'
            """)
            active_now = cur.fetchone()['active_now']

            # Active visitors in last 15 minutes
            cur.execute("""
                SELECT COUNT(DISTINCT ip) AS active_15m
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '15 minutes'
            """)
            active_15m = cur.fetchone()['active_15m']

            # Currently running jobs with user info
            cur.execute("""
                SELECT j.job_id, j.title, j.created_at,
                       u.email AS user_email, u.plan
                FROM jobs j
                LEFT JOIN users u ON u.id = j.user_id
                WHERE j.state = 'running'
                ORDER BY j.created_at DESC
            """)
            running_jobs = [dict(r) for r in cur.fetchall()]

            # Recent page hits (last 2 minutes, for live feed)
            cur.execute("""
                SELECT page, ip, visited_at, user_agent
                FROM page_visits
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
#  USERS TABLE (enhanced)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    search   = request.args.get('search', '').strip()
    plan_filter = request.args.get('plan', '').strip()
    sort     = request.args.get('sort', 'created_at').strip()
    order    = request.args.get('order', 'desc').strip()
    offset   = (page - 1) * per_page

    allowed_sorts = {'created_at', 'email', 'plan', 'credits_balance', 'job_count'}
    if sort not in allowed_sorts:
        sort = 'created_at'
    if order not in ('asc', 'desc'):
        order = 'desc'

    conn = get_db()
    try:
        with conn.cursor() as cur:
            where = "WHERE u.is_verified = 1"
            params = []
            if search:
                where += " AND u.email ILIKE %s"
                params.append(f'%{search}%')
            if plan_filter:
                where += " AND u.plan = %s"
                params.append(plan_filter)

            cur.execute(f"SELECT COUNT(*) AS total FROM users u {where}", params)
            total = cur.fetchone()['total']

            sort_col = f"u.{sort}" if sort != 'job_count' else 'job_count'

            cur.execute(f"""
                SELECT
                    u.id, u.email, u.plan, u.is_subscribed,
                    u.credits_balance, u.credits_daily, u.credits_monthly,
                    u.created_at, u.subscription_expiry,
                    (SELECT COUNT(*) FROM jobs WHERE jobs.user_id = u.id) AS job_count,
                    (SELECT COALESCE(SUM(jc.credits_used), 0) FROM job_credits jc
                     INNER JOIN jobs j2 ON j2.job_id = jc.job_id
                     WHERE j2.user_id = u.id) AS total_credits_used,
                    (SELECT MAX(j3.created_at) FROM jobs j3 WHERE j3.user_id = u.id) AS last_active
                FROM users u {where}
                ORDER BY {sort_col} {order}
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            rows = cur.fetchall()

        return jsonify({
            'users': [dict(r) for r in rows],
            'total': total,
            'page': page,
            'per_page': per_page,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  JOBS TABLE (enhanced)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/jobs', methods=['GET'])
@admin_required
def list_jobs():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    state    = request.args.get('state', '')
    offset   = (page - 1) * per_page

    conn = get_db()
    try:
        with conn.cursor() as cur:
            where  = ""
            params = []
            if state:
                where = "WHERE j.state = %s"
                params.append(state)

            cur.execute(f"SELECT COUNT(*) AS total FROM jobs j {where}", params)
            total = cur.fetchone()['total']

            cur.execute(f"""
                SELECT
                    j.job_id, j.title, j.state, j.created_at, j.updated_at,
                    u.email AS user_email, u.plan AS user_plan,
                    COALESCE(SUM(jc.credits_used), 0) AS credits_used,
                    COALESCE(SUM(jc.tokens_used), 0) AS tokens_used,
                    COUNT(jc.id) AS turns
                FROM jobs j
                LEFT JOIN users u ON u.id = j.user_id
                LEFT JOIN job_credits jc ON jc.job_id = j.job_id
                {where}
                GROUP BY j.job_id, j.title, j.state, j.created_at, j.updated_at, u.email, u.plan
                ORDER BY j.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            rows = cur.fetchall()

        return jsonify({
            'jobs': [dict(r) for r in rows],
            'total': total,
            'page': page,
            'per_page': per_page,
        }), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  TOP USERS (enhanced)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/top-users', methods=['GET'])
@admin_required
def top_users():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    u.email, u.plan,
                    COUNT(DISTINCT j.job_id) AS jobs,
                    ROUND(COALESCE(SUM(jc.credits_used), 0)::numeric, 2) AS credits_used,
                    COALESCE(SUM(jc.tokens_used), 0) AS tokens_used,
                    MAX(j.created_at) AS last_active
                FROM users u
                LEFT JOIN jobs j ON j.user_id = u.id
                LEFT JOIN job_credits jc ON jc.job_id = j.job_id
                WHERE u.is_verified = 1
                GROUP BY u.id, u.email, u.plan
                ORDER BY credits_used DESC
                LIMIT 15
            """)
            rows = cur.fetchall()
        return jsonify({'users': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  ACTIVITY FEED (enhanced)
# ─────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/activity', methods=['GET'])
@admin_required
def recent_activity():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 'register' AS type, email AS label, created_at AS ts
                FROM users WHERE is_verified = 1
                ORDER BY created_at DESC LIMIT 10
            """)
            regs = cur.fetchall()

            cur.execute("""
                SELECT
                    CASE WHEN state = 'completed' THEN 'job_done'
                         WHEN state = 'failed'    THEN 'job_fail'
                         ELSE 'job_start' END AS type,
                    CONCAT(u.email, ' → ', j.title) AS label,
                    j.created_at AS ts
                FROM jobs j
                LEFT JOIN users u ON u.id = j.user_id
                ORDER BY j.created_at DESC LIMIT 15
            """)
            job_rows = cur.fetchall()

            cur.execute("""
                SELECT 'subscribe' AS type,
                       CONCAT(email, ' (', plan, ')') AS label,
                       subscription_expiry AS ts
                FROM users
                WHERE is_subscribed = 1 AND subscription_expiry IS NOT NULL
                ORDER BY subscription_expiry DESC LIMIT 10
            """)
            subs = cur.fetchall()

        all_events = [dict(r) for r in list(regs) + list(job_rows) + list(subs)]
        all_events.sort(key=lambda x: str(x.get('ts', '')), reverse=True)

        return jsonify({'events': all_events[:40]}), 200
    finally:
        conn.close()


@admin_bp.route('/country-stats', methods=['GET'])
def country_stats():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT country,
                    COUNT(*) as visits,
                    COUNT(DISTINCT COALESCE(device_id, ip)) as unique_visitors
                FROM page_visits
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
            # Referrer source breakdown
            cur.execute("""
                SELECT referrer_source, COUNT(*) AS visits
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND referrer_source IS NOT NULL
                GROUP BY referrer_source
                ORDER BY visits DESC
            """)
            referrer_breakdown = [dict(r) for r in cur.fetchall()]

            # Device type breakdown — unique devices only
            cur.execute("""
                SELECT device_type,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT COALESCE(device_id, ip)) AS unique_devices
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND device_type IS NOT NULL
                GROUP BY device_type
                ORDER BY visits DESC
            """)
            device_breakdown = [dict(r) for r in cur.fetchall()]

            # Browser breakdown
            cur.execute("""
                SELECT browser, COUNT(*) AS visits
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                  AND browser IS NOT NULL
                GROUP BY browser
                ORDER BY visits DESC
            """)
            browser_breakdown = [dict(r) for r in cur.fetchall()]

            # Average session duration (seconds)
            cur.execute("""
                SELECT COALESCE(AVG(duration), 0) AS avg_duration,
                       COALESCE(AVG(pages), 0) AS avg_pages
                FROM (
                    SELECT session_id,
                        EXTRACT(EPOCH FROM (MAX(visited_at) - MIN(visited_at))) AS duration,
                        COUNT(*) AS pages
                    FROM page_visits
                    WHERE session_id IS NOT NULL
                      AND visited_at >= NOW() - INTERVAL '30 days'
                    GROUP BY session_id
                    HAVING COUNT(*) > 1
                ) sub
            """)
            session_row = cur.fetchone()

            # Average time on page (excluding 0s entries which are entry pings)
            cur.execute("""
                SELECT page, ROUND(AVG(time_on_page)) AS avg_time, COUNT(*) AS visits
                FROM page_visits
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
