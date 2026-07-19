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

    conn.commit()

    # ✅ Send BEFORE counting the request against the 5/day limit — a send that
    # Brevo rejects (e.g. the "unrecognised IP" 401 that took email delivery
    # down) must not burn the user's quota or pretend a code went out.
    if not send_code_to_email(email, code):
        cursor.close()
        conn.close()
        return jsonify({'error': 'We could not send your verification email. Please try again shortly.'}), 502

    cursor.execute("INSERT INTO code_request_logs (email) VALUES (%s)", (email,))
    conn.commit()
    cursor.close()
    conn.close()

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

# (debug email-codes route removed — it let unauthenticated callers dump
# every pending verification code and hijack signups)

# ✅ Utility — the single place a code email is actually sent.
def send_code_to_email(email, code):
    """Send a 6-digit code via Brevo. Returns True on success, False on failure.

    On failure it logs the REAL Brevo response (status + body) so an outage is
    diagnosable from the Render logs instead of silent. The classic failure —
    which once took email delivery down for weeks — is HTTP 401 "unrecognised
    IP address": Brevo's Authorised-IPs wall blocking Render's egress IP. Fix at
    app.brevo.com/security/authorised_ips (disable the wall, or add Render's
    outbound IPs). Callers must honour the return value and NOT tell the user a
    code was sent when this returns False.
    """
    # Default to the authenticated domain. thehustlerbot.com is NOT DKIM/SPF
    # authenticated in Brevo, so codes sent from it get spam-foldered even
    # though the API returns 201; valmera.io is authenticated → inbox-grade.
    payload = {
        "sender": {
            "name": os.getenv("FROM_NAME", "Valmera"),
            "email": os.getenv("FROM_EMAIL", "support@valmera.io")
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

    try:
        res = requests.post("https://api.brevo.com/v3/smtp/email",
                            json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        current_app.logger.error("Brevo send to %s failed (network): %s", email, e)
        return False

    if res.status_code != 201:
        current_app.logger.error(
            "Brevo send to %s failed: HTTP %s %s",
            email, res.status_code, (res.text or "")[:500])
        return False

    return True

@verify_bp.route('/cleanup-old-code-logs')
def cleanup_old_code_logs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM code_request_logs WHERE sent_at < NOW() - INTERVAL '3 DAYS'")
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Old code logs cleaned up'})
