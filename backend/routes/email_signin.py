"""One-use email sign-in codes; accounts are created only after verification."""
import datetime
import hashlib
import hmac
import re
import secrets

import jwt
from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import generate_password_hash

from credits import FREE_GRANT_CREDITS
from routes.verify_email import get_db, send_code_to_email

email_signin_bp = Blueprint("email_signin", __name__)


def normalized_email(value):
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", email):
        raise ValueError("Enter a valid email address.")
    local, domain = email.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
        raise ValueError("Enter a valid email address.")
    return email


def code_digest(challenge, code):
    return hmac.new(str(current_app.config["SECRET_KEY"]).encode(),
                    f"email-signin:{challenge}:{code}".encode(), hashlib.sha256).hexdigest()


def verified_account(cur, email, provider):
    """Serialize email/Google first sign-in and preserve existing paid accounts.

    Legacy accounts were case sensitive. Reuse an unambiguous case-insensitive
    match, but never silently merge two existing accounts. A preclaimed,
    unverified password must not survive proof of ownership by another method.
    """
    email = normalized_email(email)
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("verified-email:" + email,))
    cur.execute("SELECT * FROM users WHERE lower(email) = %s FOR UPDATE", (email,))
    matches = cur.fetchall()
    if len(matches) > 1:
        verified = [match for match in matches if match.get("is_verified")]
        if len(verified) == 1:
            # Legacy abandoned registrations must not block the real account.
            # Keep those rows untouched; never merge projects or balances.
            matches = verified
        else:
            raise ValueError("Please use your existing password to sign in, or contact support@valmera.io.")
    user = matches[0] if matches else None
    signup = not user or not user["is_verified"]
    if user:
        if not user["is_verified"]:
            cur.execute("UPDATE users SET is_verified = 1, password = %s, auth_provider = %s WHERE id = %s",
                        (generate_password_hash(secrets.token_urlsafe(32)), provider, user["id"]))
        return user["id"], user["email"], user.get("plan") or "free", signup
    cur.execute("""INSERT INTO users (email, password, is_verified, auth_provider,
                       credits_daily, credits_bonus, credits_monthly, credits_balance)
                   VALUES (%s, %s, 1, %s, 0, %s, 0, %s) RETURNING id""",
                (email, generate_password_hash(secrets.token_urlsafe(32)), provider,
                 FREE_GRANT_CREDITS, FREE_GRANT_CREDITS))
    return cur.fetchone()["id"], email, "free", True


def auth_token(user_id, email):
    return jwt.encode({"sub": str(user_id), "email": email,
                       "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)},
                      current_app.config["SECRET_KEY"], algorithm="HS256")


def payload():
    if request.content_length and request.content_length > 8192:
        raise ValueError("Request is too large.")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("Enter your email and try again.")
    return value


@email_signin_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@email_signin_bp.post("/email-code/start")
def start():
    try:
        email = normalized_email(payload().get("email"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    challenge = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1000000):06d}"
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Short global lock makes the burst guard safe across API workers;
            # it is released BEFORE contacting the email provider.
            cur.execute("SELECT pg_advisory_xact_lock(829301)")
            cur.execute("DELETE FROM email_signin_challenges WHERE created_at < NOW() - INTERVAL '2 days'")
            cur.execute("""SELECT COUNT(*) FILTER (WHERE email = %s) AS daily,
                              COUNT(*) FILTER (WHERE email = %s AND created_at > NOW() - INTERVAL '60 seconds') AS recent,
                              COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 minute') AS burst
                           FROM email_signin_challenges WHERE created_at > NOW() - INTERVAL '24 hours'""", (email, email))
            counts = cur.fetchone()
            if counts["daily"] >= 5 or counts["recent"] or counts["burst"] >= 30:
                retry = 3600 if counts["daily"] >= 5 else 60
                return jsonify(error="Please wait before requesting another code, or continue with Google.", retry_after=retry), 429, {"Retry-After": str(retry)}
            cur.execute("INSERT INTO email_signin_challenges(id, email, code_hash) VALUES (%s, %s, %s)",
                        (challenge, email, code_digest(challenge, code)))
        conn.commit()
        sent = send_code_to_email(email, code)
        with conn.cursor() as cur:
            cur.execute("UPDATE email_signin_challenges SET sent = %s, consumed = %s WHERE id = %s", (bool(sent), not sent, challenge))
        conn.commit()
        if not sent:
            return jsonify(error="We couldn't send your code. Continue with Google, or try email again shortly."), 503
        return jsonify(challenge=challenge, expires_in=300, retry_after=60)
    except Exception as exc:
        if conn:
            conn.rollback()
        current_app.logger.error("Email sign-in start failed (%s)", type(exc).__name__)
        return jsonify(error="Email sign-in is temporarily unavailable. Continue with Google or try again shortly."), 503
    finally:
        if conn:
            conn.close()


@email_signin_bp.post("/email-code/finish")
def finish():
    try:
        data = payload()
        challenge, code = data.get("challenge"), data.get("code")
        if not isinstance(challenge, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
            raise ValueError("Request a new code to continue.")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("Enter the six-digit code from your email.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""SELECT *, expires_at > NOW() AS valid_time FROM email_signin_challenges
                           WHERE id = %s FOR UPDATE""", (challenge,))
            row = cur.fetchone()
            if not row or not row["sent"] or row["consumed"] or not row["valid_time"] or row["attempts"] >= 5:
                return jsonify(error="This code expired or has already been used. Request a new code.", restart=True), 400
            if not hmac.compare_digest(row["code_hash"], code_digest(challenge, code)):
                cur.execute("UPDATE email_signin_challenges SET attempts = attempts + 1 WHERE id = %s", (challenge,))
                conn.commit()
                return jsonify(error="That code isn't correct. Check your email and try again.", restart=row["attempts"] >= 4), 400
            user_id, email, plan, signup = verified_account(cur, row["email"], "email")
            # Consume this challenge atomically with account creation. Other
            # challenges expire naturally, avoiding cross-challenge lock cycles.
            cur.execute("UPDATE email_signin_challenges SET consumed = TRUE WHERE id = %s", (challenge,))
            token = auth_token(user_id, email)
        conn.commit()
        if signup:
            from website_analytics import record_signup
            record_signup(user_id, data.get("analytics"))
        return jsonify(token=token, email=email, plan=plan, is_signup=signup)
    except ValueError as exc:
        if conn:
            conn.rollback()
        return jsonify(error=str(exc)), 409
    except Exception as exc:
        if conn:
            conn.rollback()
        current_app.logger.error("Email sign-in finish failed (%s)", type(exc).__name__)
        return jsonify(error="Sign-in could not finish. Please try again."), 503
    finally:
        if conn:
            conn.close()
