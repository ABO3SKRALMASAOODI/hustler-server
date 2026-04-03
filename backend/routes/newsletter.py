import os
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime

newsletter_bp = Blueprint('newsletter', __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"


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
#  PUBLIC: Subscribe to newsletter
# ─────────────────────────────────────────────────────────────────────────────
@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email is required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, is_active FROM newsletter_subscribers WHERE email = %s",
            (email,)
        )
        existing = cursor.fetchone()
        if existing:
            if existing['is_active']:
                cursor.close()
                conn.close()
                return jsonify({'message': 'Already subscribed'}), 200
            else:
                cursor.execute(
                    "UPDATE newsletter_subscribers SET is_active = TRUE, subscribed_at = NOW() WHERE email = %s",
                    (email,)
                )
                conn.commit()
                cursor.close()
                conn.close()
                return jsonify({'message': 'Re-subscribed successfully'}), 200
        else:
            cursor.execute(
                "INSERT INTO newsletter_subscribers (email) VALUES (%s)",
                (email,)
            )
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'message': 'Subscribed successfully'}), 201
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC: Unsubscribe from newsletter
# ─────────────────────────────────────────────────────────────────────────────
@newsletter_bp.route('/unsubscribe', methods=['POST'])
def unsubscribe():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE newsletter_subscribers SET is_active = FALSE WHERE email = %s",
        (email,)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Unsubscribed successfully'}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: List subscribers
# ─────────────────────────────────────────────────────────────────────────────
@newsletter_bp.route('/subscribers', methods=['GET'])
@admin_required
def list_subscribers():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all')
    offset = (page - 1) * per_page

    conn = get_db()
    cursor = conn.cursor()

    where_clauses = []
    params = []

    if search:
        where_clauses.append("email ILIKE %s")
        params.append(f"%{search}%")

    if status == 'active':
        where_clauses.append("is_active = TRUE")
    elif status == 'inactive':
        where_clauses.append("is_active = FALSE")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    cursor.execute(f"SELECT COUNT(*) AS total FROM newsletter_subscribers {where_sql}", params)
    total = cursor.fetchone()['total']

    cursor.execute(
        f"SELECT id, email, is_active, subscribed_at FROM newsletter_subscribers {where_sql} ORDER BY subscribed_at DESC LIMIT %s OFFSET %s",
        params + [per_page, offset]
    )
    subscribers = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS c FROM newsletter_subscribers WHERE is_active = TRUE")
    active_count = cursor.fetchone()['c']

    cursor.close()
    conn.close()

    return jsonify({
        'subscribers': subscribers,
        'total': total,
        'active_count': active_count,
        'page': page,
        'per_page': per_page,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Delete subscriber
# ─────────────────────────────────────────────────────────────────────────────
@newsletter_bp.route('/subscribers/<int:sub_id>', methods=['DELETE'])
@admin_required
def delete_subscriber(sub_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM newsletter_subscribers WHERE id = %s", (sub_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Subscriber deleted'}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Send newsletter to all active subscribers
# ─────────────────────────────────────────────────────────────────────────────
@newsletter_bp.route('/send', methods=['POST'])
@admin_required
def send_newsletter():
    data = request.get_json()
    subject = (data.get('subject') or '').strip()
    html_content = (data.get('html_content') or '').strip()

    if not subject or not html_content:
        return jsonify({'error': 'Subject and content are required'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT email FROM newsletter_subscribers WHERE is_active = TRUE")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return jsonify({'error': 'No active subscribers'}), 400

    recipients = [{"email": r['email']} for r in rows]

    # Brevo allows max 2000 recipients per call — batch if needed
    batch_size = 2000
    total_sent = 0
    errors = []

    for i in range(0, len(recipients), batch_size):
        batch = recipients[i:i + batch_size]
        payload = {
            "sender": {
                "name": os.getenv("FROM_NAME", "Valmera"),
                "email": os.getenv("FROM_EMAIL", "support@valmera.io")
            },
            "to": [{"email": "support@valmera.io"}],
            "bcc": batch,
            "subject": subject,
            "htmlContent": html_content
        }

        headers = {
            "accept": "application/json",
            "api-key": os.getenv("BREVO_API_KEY"),
            "content-type": "application/json"
        }

        res = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
        if res.status_code == 201:
            total_sent += len(batch)
        else:
            errors.append(f"Batch {i // batch_size + 1}: {res.text}")

    if errors:
        return jsonify({
            'message': f'Sent to {total_sent}/{len(recipients)} subscribers',
            'errors': errors
        }), 207

    return jsonify({
        'message': f'Newsletter sent to {total_sent} subscribers'
    }), 200
