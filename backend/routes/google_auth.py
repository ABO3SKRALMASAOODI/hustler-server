"""
Google OAuth 2.0 login flow.

Flow:
  1. Frontend hits GET /auth/google/login  → redirects user to Google
  2. Google redirects to GET /auth/google/callback?code=...
  3. We exchange code for profile, create/find user, store token with
     a one-time code, redirect frontend with just the short code.
  4. Frontend exchanges the code for the real token via POST /auth/google/exchange.
"""

import os
import jwt
import secrets
import base64
import hashlib
from urllib.parse import urlencode
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from routes.email_signin import verified_account, auth_token
import requests
from flask import Blueprint, redirect, request, current_app, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor


google_auth_bp = Blueprint("google_auth", __name__)

# ── Google OAuth endpoints ────────────────────────────────────────────────────
GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"


def get_db():
    return psycopg2.connect(
        current_app.config["DATABASE_URL"],
        cursor_factory=RealDictCursor
    )


def _get_redirect_uri():
    """The exact owned-domain URI registered for the existing Google client."""
    return os.getenv("GOOGLE_REDIRECT_URI", "https://valmera.io/api-backend/auth/google/callback")


def _state_signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="google-login")


@google_auth_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.endpoint == "google_auth.google_callback":
        response.delete_cookie("__Host-valmera-google", secure=True, httponly=True, samesite="Lax", path="/")
    return response


@google_auth_bp.route("/google/login")
def google_login():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        return redirect("https://valmera.io/login?error=google_failed")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"client_id": client_id, "redirect_uri": _get_redirect_uri(),
              "response_type": "code", "scope": "openid email profile",
              "prompt": "select_account", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    response = redirect(GOOGLE_AUTH_URL + "?" + urlencode(params))
    response.set_cookie("__Host-valmera-google", _state_signer().dumps({"state": state, "verifier": verifier}),
                        secure=True, httponly=True, samesite="Lax", max_age=600, path="/")
    return response


# ── Step 2: Google calls us back ──────────────────────────────────────────────

@google_auth_bp.route("/google/callback")
def google_callback():
    frontend_url = os.getenv("FRONTEND_URL", "https://valmera.io")
    error_redirect = f"{frontend_url}/login?error=google_failed"

    code = request.args.get("code")
    state = request.args.get("state", "")
    try:
        saved = _state_signer().loads(request.cookies.get("__Host-valmera-google", ""), max_age=600)
        if not code or not state or not secrets.compare_digest(state, saved["state"]):
            return redirect(error_redirect)
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return redirect(error_redirect)

    try:
        token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            "code": code, "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": _get_redirect_uri(), "grant_type": "authorization_code",
            "code_verifier": saved["verifier"],
        }, timeout=10)
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return redirect(error_redirect)
        user_resp = requests.get(GOOGLE_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        user_resp.raise_for_status()
        profile = user_resp.json()
        email = profile.get("email")
        if not email or profile.get("verified_email") is not True:
            return redirect(error_redirect)
    except (requests.RequestException, ValueError, TypeError):
        current_app.logger.warning("Google identity exchange failed")
        return redirect(error_redirect)

    # Create or find user in DB
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            user_id, email, plan, is_signup = verified_account(cur, email, "google")
        token = auth_token(user_id, email)

        # Store token with a short-lived one-time code
        one_time_code = secrets.token_urlsafe(32)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM google_auth_codes WHERE created_at < NOW() - INTERVAL '5 minutes'")
            cur.execute(
                "INSERT INTO google_auth_codes (code, token, plan, email, is_signup) VALUES (%s, %s, %s, %s, %s)",
                (one_time_code, token, plan, email, is_signup)
            )
            conn.commit()

        # Use path segment instead of query params — Safari blocks query param access
        return redirect(f"{frontend_url}/google-callback/{one_time_code}")

    except Exception as e:
        current_app.logger.error("Google account completion failed (%s)", type(e).__name__)
        if conn:
            conn.rollback()
        return redirect(error_redirect)
    finally:
        if conn:
            conn.close()


# ── Step 3: Frontend exchanges one-time code for token ────────────────────────

@google_auth_bp.route("/google/exchange", methods=["POST"])
def google_exchange():
    """Exchange a one-time code for the actual JWT token."""
    data = request.get_json() or {}
    code = data.get("code")
    if not code:
        return jsonify({"error": "Missing code"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM google_auth_codes WHERE code = %s AND created_at > NOW()-INTERVAL '5 minutes' RETURNING token, plan, email, is_signup",
                (code,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Invalid or expired code"}), 400

            conn.commit()

            if row.get("is_signup"):
                from website_analytics import record_signup
                claims = jwt.decode(row["token"], current_app.config["SECRET_KEY"], algorithms=["HS256"])
                record_signup(int(claims["sub"]), data.get("analytics"))
            return jsonify({
                "token": row["token"],
                "plan":  row["plan"],
                "email": row["email"],
            })
    except Exception as e:
        current_app.logger.error("Google code exchange failed (%s)", type(e).__name__)
        return jsonify({"error": "Server error"}), 500
    finally:
        if conn:
            conn.close()
