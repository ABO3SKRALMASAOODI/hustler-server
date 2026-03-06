import os
import random
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta

verify_bp = Blueprint('verify', __name__)

def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)

# ✅ Send verification code
@verify_bp.route('/send-code', methods=['POST'])
def send_code():
    email = request.json.get('email')
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    conn = get_db()
    cursor = conn.cursor()

    # 🚫 Check if this email exceeded 5 codes in last 24h
    cursor.execute("""
    SELECT COUNT(*) AS count FROM code_request_logs
    WHERE email = %s AND sent_at > NOW() - INTERVAL '24 HOURS'
    """, (email,))
    count_today = cursor.fetchone()['count']

       
    if count_today >= 5:
        cursor.close()
        conn.close()
        return jsonify({'error': 'You have reached the maximum of 5 codes today'}), 429

    # ✅ Generate and store code with timestamp
    code = str(random.randint(100000, 999999))
    cursor.execute("""
        INSERT INTO email_codes (email, code, created_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (email) DO UPDATE SET code = EXCLUDED.code, created_at = NOW()
    """, (email, code))

    # ✅ Log the request for rate limiting
    cursor.execute("INSERT INTO code_request_logs (email) VALUES (%s)", (email,))
    conn.commit()
    cursor.close()
    conn.close()

    # ✅ Send email
    payload = {
        "sender": {
            "name": os.getenv("FROM_NAME"),
            "email": os.getenv("FROM_EMAIL")
        },
        "to": [{"email": email}],
        "subject": "Your Verification Code",
        "htmlContent": f"<p>Your code is: <strong>{code}</strong></p>"
    }

    headers = {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json"
    }

    res = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
    if res.status_code != 201:
        return jsonify({'error': 'Failed to send email'}), 500

    return jsonify({'message': 'Verification code sent'}), 200

# ✅ Verify code
@verify_bp.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.get_json()
    email = data.get('email')
    code = data.get('code')

    print("🔍 Received verification attempt for:", email, "with code:", code)

    if not email or not code:
        return jsonify({'error': 'Email and code are required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT code, created_at FROM email_codes WHERE email = %s", (email,))
    row = cursor.fetchone()

    print("🧠 Code found in DB:", row['code'] if row else "None")

    if not row:
        cursor.close()
        conn.close()
        return jsonify({'error': 'No code found for this email'}), 400

    # ✅ Check if expired (older than 5 minutes)
    if row['created_at'] < datetime.utcnow() - timedelta(minutes=5):
        cursor.close()
        conn.close()
        return jsonify({'error': 'Code has expired'}), 400

    if str(row['code']).strip() != str(code).strip():
        cursor.close()
        conn.close()
        return jsonify({'error': 'Invalid code'}), 400

    # ✅ Mark user verified & cleanup
    cursor.execute("UPDATE users SET is_verified = 1 WHERE email = %s", (email,))
    cursor.execute("DELETE FROM email_codes WHERE email = %s", (email,))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Email verified successfully'}), 200

# ✅ Debug route
@verify_bp.route('/debug/email-codes')
def debug_email_codes():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM email_codes")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(rows)

# ✅ Utility (optional)
def send_code_to_email(email, code):
    payload = {
        "sender": {
            "name": os.getenv("FROM_NAME"),
            "email": os.getenv("FROM_EMAIL")
        },
        "to": [{"email": email}],
        "subject": "Your Verification Code",
        "htmlContent": f"<p>Your code is: <strong>{code}</strong></p>"
    }

    headers = {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json"
    }

    requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)

@verify_bp.route('/cleanup-old-code-logs')
def cleanup_old_code_logs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM code_request_logs WHERE sent_at < NOW() - INTERVAL '3 DAYS'")
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Old code logs cleaned up'})
