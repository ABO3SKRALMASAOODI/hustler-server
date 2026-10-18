import os
import random
import hmac
import requests
import brevo_delivery
from email_delivery import delivery_tag, legacy_reference, utc, verification_window
from routes.newsletter_content import verification_email
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta, timezone

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
    if not send_code_to_email(email, code, delivery_aware=True):
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

    if not email or not code:
        return jsonify({'error': 'Email and code are required'}), 400

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT code, created_at FROM email_codes WHERE email = %s", (email,))
        row = cursor.fetchone()
        conn.rollback()
        if not row:
            return jsonify(error='No code found for this email'), 400
        created = utc(row['created_at'])
        expires = created + timedelta(minutes=5)
        reference = legacy_reference(email, row['code'])
        # Bind the attempt counter to this exact issuance, not just the address.
        guard = 'legacy-' + delivery_tag(reference + ':' + created.isoformat())
        correct = hmac.compare_digest(str(row['code']).strip().encode(), str(code).strip().encode())
        window = None
        if correct and datetime.now(timezone.utc) >= expires:
            # Network lookup is outside the account/code transaction.
            window = verification_window(reference, email, created, expires)

        cursor.execute("SELECT code, created_at FROM email_codes WHERE email = %s FOR UPDATE", (email,))
        current = cursor.fetchone()
        if not current or str(current['code']) != str(row['code']) or utc(current['created_at']) != created:
            return jsonify(error='A newer code was requested. Use the latest email.'), 400
        cursor.execute("""INSERT INTO email_signin_challenges
                          (id,email,code_hash,created_at,expires_at,sent)
                          VALUES (%s,%s,%s,%s,%s,TRUE) ON CONFLICT (id) DO NOTHING""",
                       (guard, email.lower(), delivery_tag(reference), created, expires))
        cursor.execute("SELECT attempts, consumed FROM email_signin_challenges WHERE id = %s FOR UPDATE", (guard,))
        attempts = cursor.fetchone()
        if attempts['consumed'] or attempts['attempts'] >= 5:
            return jsonify(error='Too many attempts. Request a new code below.'), 400
        if not correct:
            cursor.execute("UPDATE email_signin_challenges SET attempts = attempts + 1 WHERE id = %s", (guard,))
            conn.commit()
            return jsonify(error='Invalid code'), 400
        if window and not window['valid']:
            if window['retryable']:
                return jsonify(error="We're still checking delivery of this delayed email. Keep this code and try again shortly."), 503
            return jsonify(error='Code has expired'), 400
        deadline = window['valid_until'] if window else expires
        if datetime.now(timezone.utc) >= deadline:
            return jsonify(error='Code has expired'), 400

        cursor.execute("UPDATE users SET is_verified = 1 WHERE email = %s AND is_verified = 0 RETURNING id", (email,))
        first_verification = cursor.fetchone()
        cursor.execute("DELETE FROM email_codes WHERE email = %s", (email,))
        cursor.execute("UPDATE email_signin_challenges SET consumed = TRUE WHERE id = %s", (guard,))
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    if first_verification:
        from website_analytics import record_signup
        record_signup(first_verification['id'], data.get('analytics'))
    return jsonify({'message': 'Email verified successfully'}), 200

# (debug email-codes route removed — it let unauthenticated callers dump
# every pending verification code and hijack signups)

# ✅ Utility — the single place a code email is actually sent.
def send_code_to_email(email, code, *, challenge=None, delivery_aware=False):
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
        **verification_email(code, minutes=10 if challenge else 5, delivery_aware=delivery_aware),
    }
    if delivery_aware:
        reference = challenge or legacy_reference(email, code)
        payload['tags'] = ['valmera-verification', delivery_tag(reference)]

    return brevo_delivery.send_email(
        payload, category="critical", logger=current_app.logger)

@verify_bp.route('/cleanup-old-code-logs')
def cleanup_old_code_logs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM code_request_logs WHERE sent_at < NOW() - INTERVAL '3 DAYS'")
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Old code logs cleaned up'})
