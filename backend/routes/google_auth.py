"""
Google OAuth 2.0 login flow.

Flow:
  1. Frontend hits GET /auth/google/login  → redirects user to Google
  2. Google redirects to GET /auth/google/callback?code=...
  3. We exchange code for profile, create/find user, return JWT via redirect
     to frontend with token in URL fragment.
"""

import os
import jwt
import datetime
import requests
from flask import Blueprint, redirect, request, current_app
from werkzeug.security import generate_password_hash
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
    """Build the callback URI — matches what's registered in Google Console."""
    base = os.getenv("BACKEND_URL", "https://entrepreneur-bot-backend.onrender.com")
    return f"{base}/auth/google/callback"


# ── Step 1: Redirect to Google ────────────────────────────────────────────────

@google_auth_bp.route("/google/login")
def google_login():
    client_id    = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = _get_redirect_uri()

    params = (
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&access_type=offline"
        f"&prompt=select_account"
    )
    return redirect(GOOGLE_AUTH_URL + params)


# ── Step 2: Google calls us back ──────────────────────────────────────────────

@google_auth_bp.route("/google/callback")
def google_callback():
    frontend_url = os.getenv("FRONTEND_URL", "https://thehustlerbot.com")
    error_redirect = f"{frontend_url}/signin?error=google_failed"

    code = request.args.get("code")
    if not code:
        return redirect(error_redirect)

    # Exchange code for access token
    client_id     = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri  = _get_redirect_uri()

    token_resp = requests.post(GOOGLE_TOKEN_URL, data={
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }, timeout=10)

    if not token_resp.ok:
        print(f"[google] token exchange failed: {token_resp.text}")
        return redirect(error_redirect)

    access_token = token_resp.json().get("access_token")
    if not access_token:
        return redirect(error_redirect)

    # Fetch user profile
    user_resp = requests.get(
        GOOGLE_USER_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10
    )
    if not user_resp.ok:
        return redirect(error_redirect)

    profile = user_resp.json()
    email   = profile.get("email")
    name    = profile.get("name", "")

    if not email:
        return redirect(error_redirect)

    # Create or find user in DB
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()

            if user:
                # Existing user — if previously unverified, verify them now
                if user["is_verified"] == 0:
                    cur.execute(
                        "UPDATE users SET is_verified = 1 WHERE email = %s",
                        (email,)
                    )
                    conn.commit()
                user_id = user["id"]
                plan    = user.get("plan", "free") or "free"
            else:
                # New user — create with a random unusable password
                # is_verified = 1 because Google already verified their email
                dummy_pw = generate_password_hash(os.urandom(32).hex())
                cur.execute(
                    """
                    INSERT INTO users (email, password, is_verified, credits_daily, credits_balance)
                    VALUES (%s, %s, 1, 20, 20)
                    RETURNING id
                    """,
                    (email, dummy_pw)
                )
                row     = cur.fetchone()
                user_id = row["id"]
                plan    = "free"
                conn.commit()

        # Issue JWT — same format as regular login
        token = jwt.encode({
            "sub":   str(user_id),
            "email": email,
            "exp":   datetime.datetime.utcnow() + datetime.timedelta(days=7),
        }, current_app.config["SECRET_KEY"], algorithm="HS256")

        # Redirect to frontend with token + plan in URL fragment
        # Frontend reads these and stores them in localStorage
        return redirect(
            f"{frontend_url}/login?google=1"
            f"#token={token}&plan={plan}&email={requests.utils.quote(email)}"
        )

    except Exception as e:
        print(f"[google] DB error: {e}")
        conn.rollback()
        return redirect(error_redirect)
    finally:
        conn.close()