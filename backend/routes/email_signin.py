"""One-use email sign-in codes; accounts are created only after verification."""
import datetime
import hashlib
import hmac
import re
import secrets

import jwt
from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import generate_password_hash

from email_delivery import CODE_SECONDS, MAX_DELIVERY_SECONDS, utc, verification_window
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
    challenge = "d2_" + secrets.token_urlsafe(30)
    code = f"{secrets.randbelow(1000000):06d}"
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            # Short global lock makes the burst guard safe across API workers;
            # it is released BEFORE contacting the email provider.
            cur.execute("SELECT pg_advisory_xact_lock(829301)")
            cur.execute("DELETE FROM email_signin_challenges WHERE created_at < NOW() - INTERVAL '2 days'")
            cur.execute("""SELECT COUNT(*) FILTER (WHERE email = %s AND (sent OR NOT consumed)) AS daily,
                              COUNT(*) FILTER (WHERE email = %s AND created_at > NOW() - INTERVAL '60 seconds') AS recent,
                              COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 minute') AS burst
                           FROM email_signin_challenges WHERE created_at > NOW() - INTERVAL '24 hours'""", (email, email))
            counts = cur.fetchone()
            if counts["daily"] >= 5 or counts["recent"] or counts["burst"] >= 30:
                retry = 3600 if counts["daily"] >= 5 else 60
                return jsonify(error="Please wait before requesting another code, or continue with Google.", retry_after=retry), 429, {"Retry-After": str(retry)}
            cur.execute("""INSERT INTO email_signin_challenges(id, email, code_hash, expires_at)
                           VALUES (%s, %s, %s, NOW() + %s * INTERVAL '1 second')""",
                        (challenge, email, code_digest(challenge, code), CODE_SECONDS))
        conn.commit()
        sent = send_code_to_email(email, code, challenge=challenge, delivery_aware=True)
        with conn.cursor() as cur:
            cur.execute("UPDATE email_signin_challenges SET sent = %s, consumed = %s WHERE id = %s", (bool(sent), not sent, challenge))
        conn.commit()
        if not sent:
            return jsonify(error="We couldn't send your code. Continue with Google, or try email again shortly."), 503
        return jsonify(challenge=challenge, expires_in=CODE_SECONDS, retry_after=60,
                       delivery_window=CODE_SECONDS, pending_expires_in=MAX_DELIVERY_SECONDS + CODE_SECONDS)
    except Exception as exc:
        if conn:
            conn.rollback()
        current_app.logger.error("Email sign-in start failed (%s)", type(exc).__name__)
        return jsonify(error="Email sign-in is temporarily unavailable. Continue with Google or try again shortly."), 503
    finally:
        if conn:
            conn.close()


def challenge_ids(data):
    challenge = data.get("challenge")
    previous = data.get("previous_challenges", [])
    if not isinstance(previous, list) or len(previous) > 4:
        raise ValueError("Request a new code to continue.")
    ids = [challenge, *previous]
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value) for value in ids):
        raise ValueError("Request a new code to continue.")
    return list(dict.fromkeys(ids))


def usable(row):
    return row["sent"] and not row["consumed"] and row["attempts"] < 5


def window_for(row, *, check_delivery=False):
    now = datetime.datetime.now(datetime.timezone.utc)
    if row["valid_time"] and not check_delivery:
        return {"state": "pending", "valid": True, "retryable": False,
                "expires_in": max(0, int((utc(row["expires_at"]) - now).total_seconds())),
                "valid_until": utc(row["expires_at"])}
    if row["id"].startswith("d2_"):
        return verification_window(row["id"], row["email"], row["created_at"], row["expires_at"], now)
    return {"state": "pending" if row["valid_time"] else "expired", "valid": bool(row["valid_time"]),
            "retryable": False, "expires_in": max(0, int((utc(row["expires_at"]) - now).total_seconds())),
                "valid_until": utc(row["expires_at"])}


@email_signin_bp.post("/email-code/status")
def delivery_status():
    try:
        ids = challenge_ids(payload())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT *, expires_at > NOW() AS valid_time FROM email_signin_challenges WHERE id = %s", (ids[0],))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not usable(row):
        return jsonify(state="expired", expires_in=0), 200
    window = window_for(row, check_delivery=True)
    return jsonify(state=window["state"], expires_in=window["expires_in"], retry_after=20)


@email_signin_bp.post("/email-code/finish")
def finish():
    try:
        data = payload()
        ids = challenge_ids(data)
        code = data.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("Enter the six-digit code from your email.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT *, expires_at > NOW() AS valid_time FROM email_signin_challenges WHERE id = ANY(%s)", (ids,))
            rows = cur.fetchall()
        # Never hold a database transaction/row lock during provider requests.
        conn.rollback()
        by_id = {row["id"]: row for row in rows}
        latest = by_id.get(ids[0])
        if not latest or any(row["email"] != latest["email"] for row in rows):
            return jsonify(error="Request a new code to continue.", restart=True), 400
        candidates = [by_id[cid] for cid in ids if cid in by_id and usable(by_id[cid])]
        if not candidates:
            return jsonify(error="This code has already been used or tried too many times. Request a new code.", restart=True), 400
        matching = [row for row in candidates if hmac.compare_digest(row["code_hash"], code_digest(row["id"], code))]
        accepted, deadline, retryable = None, None, False
        for row in matching:
            window = window_for(row)
            retryable = retryable or window["retryable"]
            if window["valid"]:
                accepted = row["id"]
                deadline = window["valid_until"]
                break
        if matching and not accepted:
            if retryable:
                return jsonify(error="We're still checking delivery of this delayed email. Keep this code and try again shortly.", restart=False), 503
            return jsonify(error="That code has expired. Use your newer code or request another one.", restart=len(candidates) == 1), 400
        with conn.cursor() as cur:
            # Stable lock order prevents overlapping resend groups deadlocking.
            cur.execute("""SELECT *, expires_at > NOW() AS valid_time FROM email_signin_challenges
                           WHERE id = ANY(%s) ORDER BY id FOR UPDATE""", (ids,))
            locked = {row["id"]: row for row in cur.fetchall()}
            if not accepted:
                eligible = [row for row in locked.values() if usable(row)]
                cur.execute("""UPDATE email_signin_challenges SET attempts = attempts + 1
                               WHERE id = ANY(%s) AND attempts < 5 AND NOT consumed""", ([row["id"] for row in eligible],))
                conn.commit()
                return jsonify(error="That code isn't correct. Check your email and try again.",
                               restart=not eligible or all(row["attempts"] >= 4 for row in eligible)), 400
            row = locked.get(accepted)
            if (not row or not usable(row) or row["email"] != latest["email"]
                    or not hmac.compare_digest(row["code_hash"], code_digest(row["id"], code))
                    or datetime.datetime.now(datetime.timezone.utc) >= deadline):
                return jsonify(error="This code expired or has already been used. Request a new code.", restart=True), 400
            user_id, email, plan, signup = verified_account(cur, row["email"], "email")
            # Consume every challenge in this browser's resend group atomically.
            cur.execute("UPDATE email_signin_challenges SET consumed = TRUE WHERE id = ANY(%s)", (ids,))
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
