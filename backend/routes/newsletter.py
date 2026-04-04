import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app
from functools import wraps
import jwt

newsletter_bp = Blueprint('newsletter', __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"
BREVO_BASE = "https://api.brevo.com/v3"


def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)


def _brevo_headers():
    return {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json",
    }


def admin_required(f):
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
            if data.get('email', '') != ADMIN_EMAIL:
                return jsonify({'error': 'Forbidden'}), 403
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Get all registered users (the recipients)
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/subscribers', methods=['GET'])
@admin_required
def get_subscribers():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS count FROM users WHERE is_verified = 1")
    total = cursor.fetchone()['count']

    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))

    cursor.execute(
        "SELECT email, plan, created_at FROM users WHERE is_verified = 1 ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset)
    )
    users = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({
        'subscribers': [{"email": u['email'], "plan": u.get('plan', 'free'), "joined": str(u.get('created_at', ''))} for u in users],
        'total': total,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Send newsletter to all verified users
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/send', methods=['POST'])
@admin_required
def send_newsletter():
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    html_content = (data.get('htmlContent') or '').strip()

    if not subject:
        return jsonify({'error': 'Subject is required'}), 400
    if not html_content:
        return jsonify({'error': 'HTML content is required'}), 400

    # Fetch all verified user emails from DB
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM users WHERE is_verified = 1")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return jsonify({'error': 'No verified users to send to'}), 400

    emails = [r['email'] for r in rows]

    headers = _brevo_headers()
    sender_email = os.getenv("FROM_EMAIL", "support@valmera.io")
    sender_name = os.getenv("FROM_NAME", "Valmera")

    # Add unsubscribe footer
    unsubscribe_footer = (
        '<br/><hr style="border:none;border-top:1px solid #333;margin:32px 0 16px"/>'
        '<p style="font-size:12px;color:#888;text-align:center;">'
        'You received this because you have an account on '
        '<a href="https://valmera.io" style="color:#dc2626;">Valmera</a>.'
        '</p>'
    )
    full_html = html_content + unsubscribe_footer

    # Send in batches of 50 using Brevo transactional email
    # (Brevo allows up to 50 recipients in the "to" field per call)
    BATCH_SIZE = 50
    sent = 0
    failed = 0

    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i:i + BATCH_SIZE]
        # Use BCC so users don't see each other's emails
        # First email goes in "to", rest in "bcc" — or send individually
        # Brevo transactional: send to each batch via messageVersions for isolation
        # Simplest: use "to" with one recipient per call is cleanest but slow
        # Best approach: use Brevo's batch with BCC
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": sender_email}],  # send to self
            "bcc": [{"email": e} for e in batch],
            "subject": subject,
            "htmlContent": full_html,
        }

        res = requests.post(f"{BREVO_BASE}/smtp/email", json=payload, headers=headers)
        if res.status_code == 201:
            sent += len(batch)
        else:
            failed += len(batch)

    return jsonify({
        'message': f'Newsletter sent to {sent} users' + (f' ({failed} failed)' if failed else ''),
        'sent': sent,
        'failed': failed,
        'total': len(emails),
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Get past campaigns (from Brevo)
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/campaigns', methods=['GET'])
@admin_required
def get_campaigns():
    headers = _brevo_headers()
    limit = int(request.args.get('limit', 20))
    offset = int(request.args.get('offset', 0))

    # Get transactional email events for our newsletter subject lines
    res = requests.get(
        f"{BREVO_BASE}/smtp/statistics/aggregatedReport",
        headers=headers,
    )

    stats = {}
    if res.status_code == 200:
        stats = res.json()

    return jsonify({
        'stats': {
            'delivered': stats.get('delivered', 0),
            'opens': stats.get('uniqueOpens', 0),
            'clicks': stats.get('uniqueClicks', 0),
            'blocked': stats.get('blocked', 0),
        }
    }), 200
