from flask import Blueprint, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app
import os

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


# ── Track a page visit ────────────────────────────────────────────────────────
@admin_bp.route('/track', methods=['POST'])
def track_visit():
    data = request.get_json() or {}
    page = data.get('page', '/')
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO page_visits (page, ip, user_agent) VALUES (%s, %s, %s)",
                (page, request.remote_addr, request.headers.get('User-Agent', '')[:300])
            )
            conn.commit()
    finally:
        conn.close()
    return jsonify({'ok': True}), 200


# ── Overview KPIs ─────────────────────────────────────────────────────────────
@admin_bp.route('/overview', methods=['GET'])
@admin_required
def overview():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Total users
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1")
            total_users = cur.fetchone()['total']

            # New users today
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at::date = CURRENT_DATE")
            new_users_today = cur.fetchone()['total']

            # New users this week
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '7 days'")
            new_users_week = cur.fetchone()['total']

            # Total subscribed
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_subscribed = 1")
            total_subscribed = cur.fetchone()['total']

            # New subscriptions today
            cur.execute("""
                SELECT COUNT(*) AS total FROM users 
                WHERE is_subscribed = 1 AND updated_at::date = CURRENT_DATE
            """ if False else """
                SELECT COUNT(*) AS total FROM users 
                WHERE is_subscribed = 1
            """)
            # New subs this week — approximate from subscription_expiry logic
            cur.execute("""
                SELECT COUNT(*) AS total FROM users 
                WHERE is_subscribed = 1 AND subscription_expiry > NOW() AND subscription_expiry < NOW() + INTERVAL '32 days'
            """)
            new_subs_week = cur.fetchone()['total']

            # Plan breakdown
            cur.execute("""
                SELECT plan, COUNT(*) AS count FROM users 
                WHERE is_verified = 1
                GROUP BY plan
            """)
            plan_breakdown = {r['plan']: r['count'] for r in cur.fetchall()}

            # MRR estimate
            plan_prices = {'plus': 20, 'pro': 50, 'ultra': 100}
            mrr = sum(plan_prices.get(p, 0) * c for p, c in plan_breakdown.items())

            # Total jobs
            cur.execute("SELECT COUNT(*) AS total FROM jobs")
            total_jobs = cur.fetchone()['total']

            # Jobs today
            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE created_at::date = CURRENT_DATE")
            jobs_today = cur.fetchone()['total']

            # Jobs running now
            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'running'")
            jobs_running = cur.fetchone()['total']

            # Jobs failed today
            cur.execute("SELECT COUNT(*) AS total FROM jobs WHERE state = 'failed' AND created_at::date = CURRENT_DATE")
            jobs_failed_today = cur.fetchone()['total']

            # Total page visits today
            cur.execute("SELECT COUNT(*) AS total FROM page_visits WHERE visited_at::date = CURRENT_DATE")
            visits_today = cur.fetchone()['total']

            # Total visits this week
            cur.execute("SELECT COUNT(*) AS total FROM page_visits WHERE visited_at >= NOW() - INTERVAL '7 days'")
            visits_week = cur.fetchone()['total']

            # Total credits consumed today
            cur.execute("SELECT COALESCE(SUM(credits_used), 0) AS total FROM job_credits WHERE created_at::date = CURRENT_DATE")
            credits_today = float(cur.fetchone()['total'])

            # Total tokens consumed
            cur.execute("SELECT COALESCE(SUM(tokens_used), 0) AS total FROM job_credits")
            total_tokens = int(cur.fetchone()['total'])

        return jsonify({
            'users': {
                'total': total_users,
                'new_today': new_users_today,
                'new_week': new_users_week,
            },
            'subscriptions': {
                'total': total_subscribed,
                'new_week': new_subs_week,
                'plan_breakdown': plan_breakdown,
                'mrr': mrr,
            },
            'jobs': {
                'total': total_jobs,
                'today': jobs_today,
                'running': jobs_running,
                'failed_today': jobs_failed_today,
            },
            'visits': {
                'today': visits_today,
                'week': visits_week,
            },
            'credits': {
                'consumed_today': credits_today,
                'total_tokens': total_tokens,
            }
        }), 200
    finally:
        conn.close()


# ── Registrations over time (last 30 days) ────────────────────────────────────
@admin_bp.route('/charts/registrations', methods=['GET'])
@admin_required
def chart_registrations():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    TO_CHAR(created_at::date, 'YYYY-MM-DD') AS day,
                    COUNT(*) AS count
                FROM users
                WHERE is_verified = 1 AND created_at >= NOW() - INTERVAL '30 days'
                GROUP BY created_at::date
                ORDER BY created_at::date
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ── Jobs over time (last 30 days) ─────────────────────────────────────────────
@admin_bp.route('/charts/jobs', methods=['GET'])
@admin_required
def chart_jobs():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    TO_CHAR(created_at::date, 'YYYY-MM-DD') AS day,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE state = 'completed') AS completed,
                    COUNT(*) FILTER (WHERE state = 'failed') AS failed
                FROM jobs
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY created_at::date
                ORDER BY created_at::date
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ── Page visits over time (last 30 days) ──────────────────────────────────────
@admin_bp.route('/charts/visits', methods=['GET'])
@admin_required
def chart_visits():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    TO_CHAR(visited_at::date, 'YYYY-MM-DD') AS day,
                    COUNT(*) AS count
                FROM page_visits
                WHERE visited_at >= NOW() - INTERVAL '30 days'
                GROUP BY visited_at::date
                ORDER BY visited_at::date
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ── Credits consumed over time ────────────────────────────────────────────────
@admin_bp.route('/charts/credits', methods=['GET'])
@admin_required
def chart_credits():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    TO_CHAR(created_at::date, 'YYYY-MM-DD') AS day,
                    ROUND(SUM(credits_used)::numeric, 2) AS credits,
                    SUM(tokens_used) AS tokens
                FROM job_credits
                WHERE created_at >= NOW() - INTERVAL '30 days'
                GROUP BY created_at::date
                ORDER BY created_at::date
            """)
            rows = cur.fetchall()
        return jsonify({'data': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ── Users table ───────────────────────────────────────────────────────────────
@admin_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    search   = request.args.get('search', '').strip()
    offset   = (page - 1) * per_page

    conn = get_db()
    try:
        with conn.cursor() as cur:
            where = "WHERE is_verified = 1"
            params = []
            if search:
                where += " AND email ILIKE %s"
                params.append(f'%{search}%')

            cur.execute(f"SELECT COUNT(*) AS total FROM users {where}", params)
            total = cur.fetchone()['total']

            cur.execute(f"""
                SELECT 
                    id, email, plan, is_subscribed,
                    credits_balance, credits_daily, credits_monthly,
                    created_at, subscription_expiry,
                    (SELECT COUNT(*) FROM jobs WHERE jobs.user_id = users.id) AS job_count
                FROM users {where}
                ORDER BY created_at DESC
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


# ── Jobs table ────────────────────────────────────────────────────────────────
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
                    u.email AS user_email,
                    COALESCE(SUM(jc.credits_used), 0) AS credits_used,
                    COALESCE(SUM(jc.tokens_used), 0) AS tokens_used
                FROM jobs j
                LEFT JOIN users u ON u.id = j.user_id
                LEFT JOIN job_credits jc ON jc.job_id = j.job_id
                {where}
                GROUP BY j.job_id, j.title, j.state, j.created_at, j.updated_at, u.email
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


# ── Top users by usage ────────────────────────────────────────────────────────
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
                    ROUND(COALESCE(SUM(jc.credits_used), 0)::numeric, 2) AS credits_used
                FROM users u
                LEFT JOIN jobs j ON j.user_id = u.id
                LEFT JOIN job_credits jc ON jc.job_id = j.job_id
                WHERE u.is_verified = 1
                GROUP BY u.id, u.email, u.plan
                ORDER BY credits_used DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
        return jsonify({'users': [dict(r) for r in rows]}), 200
    finally:
        conn.close()


# ── Recent activity feed ──────────────────────────────────────────────────────
@admin_bp.route('/activity', methods=['GET'])
@admin_required
def recent_activity():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Last 20 registrations
            cur.execute("""
                SELECT 'register' AS type, email AS label, created_at AS ts
                FROM users WHERE is_verified = 1
                ORDER BY created_at DESC LIMIT 10
            """)
            regs = cur.fetchall()

            # Last 10 jobs
            cur.execute("""
                SELECT 
                    CASE WHEN state = 'completed' THEN 'job_done'
                         WHEN state = 'failed'    THEN 'job_fail'
                         ELSE 'job_start' END AS type,
                    CONCAT(u.email, ' → ', j.title) AS label,
                    j.created_at AS ts
                FROM jobs j
                LEFT JOIN users u ON u.id = j.user_id
                ORDER BY j.created_at DESC LIMIT 10
            """)
            job_rows = cur.fetchall()

            # Last 10 subscriptions
            cur.execute("""
                SELECT 'subscribe' AS type, 
                       CONCAT(email, ' (', plan, ')') AS label,
                       subscription_expiry AS ts
                FROM users
                WHERE is_subscribed = 1 AND subscription_expiry IS NOT NULL
                ORDER BY subscription_expiry DESC LIMIT 10
            """)
            subs = cur.fetchall()

        # Merge and sort
        all_events = [dict(r) for r in list(regs) + list(job_rows) + list(subs)]
        all_events.sort(key=lambda x: str(x.get('ts', '')), reverse=True)

        return jsonify({'events': all_events[:30]}), 200
    finally:
        conn.close()