import hashlib
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

import psycopg2
from dotenv import load_dotenv

# Load local development configuration before importing route modules that
# intentionally snapshot deployment settings at import time. Render injects
# environment variables directly, but the previous order made the same valid
# settings in backend/.env look absent to Paddle and other integrations.
load_dotenv()

from flask import Flask
from flask_cors import CORS
from routes.auth import auth_bp
from routes.verify_email import verify_bp
from routes.paddle import paddle_bp as paddle_checkout_bp
from routes.paddle_webhook import paddle_webhook
from routes.admin import admin_bp
from routes.google_auth import google_auth_bp
from models import close_db
from routes.github import github_bp
from routes.deploy import deploy_bp
from routes.supabase_mgmt import supabase_bp
from routes.stripe_mgmt import stripe_bp
from routes.ai_proxy import ai_proxy_bp
from routes.planner import planner_bp
from routes.newsletter import newsletter_bp, start_newsletter_scheduler
from routes.video import video_bp
from routes.admin_video import admin_video_bp
from routes.onboarding import onboarding_bp
from routes.mcp import mcp_bp
from routes.mcp_oauth import mcp_oauth_bp
from routes.phone_status import phone_status_bp
from security_config import database_credential_status, secret_ok


DATABASE_REQUIRED_RELATIONS = (
    "users", "projects", "assets", "indexes", "edls", "video_jobs",
    "payments", "client_events", "remote_executions", "mcp_tokens",
    "mcp_oauth_clients", "mcp_oauth_tokens", "mcp_catalog",
)
_DATABASE_HEALTH_TTL_S = 30
_database_health_cache = {}
_database_health_lock = threading.Lock()

_DEFAULT_CORS_ORIGINS = (
    "https://valmera.io",
    "https://www.valmera.io",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def cors_allowed_origins():
    """Return explicit browser origins; never turn a typo into a wildcard."""
    configured = (os.environ.get("CORS_ALLOWED_ORIGINS") or "").split(",")
    candidates = [*_DEFAULT_CORS_ORIGINS, os.environ.get("FRONTEND_URL"),
                  *configured]
    origins = []
    for candidate in candidates:
        value = str(candidate or "").strip().rstrip("/")
        if not value or value == "*":
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or \
                parsed.username or parsed.password or parsed.query or \
                parsed.fragment or parsed.path:
            continue
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return origins


def database_runtime_status(dsn):
    """Bounded, cached reachability/schema status with no error disclosure."""
    fingerprint = hashlib.sha256(str(dsn or "").encode()).hexdigest()
    now = time.monotonic()
    cached = _database_health_cache.get(fingerprint)
    if cached and now - cached[0] < _DATABASE_HEALTH_TTL_S:
        return cached[1]

    with _database_health_lock:
        now = time.monotonic()
        cached = _database_health_cache.get(fingerprint)
        if cached and now - cached[0] < _DATABASE_HEALTH_TTL_S:
            return cached[1]
        conn = None
        try:
            conn = psycopg2.connect(
                dsn,
                connect_timeout=3,
                options=("-c default_transaction_read_only=on "
                         "-c statement_timeout=3000"),
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT " + ", ".join(
                        "to_regclass(%s)" for _ in DATABASE_REQUIRED_RELATIONS),
                    tuple("public." + name
                          for name in DATABASE_REQUIRED_RELATIONS),
                )
                relations = cur.fetchone()
            status = ("ready" if relations
                      and all(relation is not None for relation in relations)
                      else "schema_incomplete")
        except Exception:
            status = "unreachable"
        finally:
            if conn is not None:
                conn.close()
        if len(_database_health_cache) >= 4:
            oldest = min(_database_health_cache,
                         key=lambda key: _database_health_cache[key][0])
            _database_health_cache.pop(oldest, None)
        _database_health_cache[fingerprint] = (time.monotonic(), status)
        return status


def _configured_app_secret():
    value = (os.environ.get("SECRET_KEY") or "").strip()
    return value if secret_ok(value, 32) else None


def create_app():
    app = Flask(__name__)

    CORS(app,
         origins=cors_allowed_origins(),
         allow_headers=["Content-Type", "Authorization"],
         methods=["GET", "POST", "OPTIONS", "PUT", "DELETE", "PATCH"],
         send_wildcard=False,
         always_send=False,
         vary_header=True)

    # Round 79 — which code is this service actually running? The worker and
    # executor answer that (/health carries code_version); this service could
    # not, and a stale deploy was only discoverable by a user-facing
    # validation error quoting last week's schema. Render injects the commit.
    @app.route("/healthz")
    def healthz():
        import os as _os
        database_url = _os.environ.get("DATABASE_URL")
        direct_database_url = (_os.environ.get("DIRECT_DATABASE_URL") or "")
        paddle_mode = (_os.environ.get("PADDLE_MODE") or "").strip().lower()
        database_credential = database_credential_status(database_url)
        direct_database_credential = (
            database_credential_status(direct_database_url)
            if direct_database_url.strip() else "not_configured")
        checks = {
            "secret_key": "configured" if _configured_app_secret()
                          else "missing",
            "paddle_webhook_signing": (
                "configured" if secret_ok(
                    _os.environ.get("PADDLE_WEBHOOK_SECRET"), 16)
                else "missing"),
            "paddle_api": (
                "configured" if secret_ok(
                    _os.environ.get("PADDLE_API_KEY"), 16)
                else "missing"),
            "database_credential": database_credential,
            "database_runtime": (
                database_runtime_status(database_url)
                if database_credential == "rotated" else "not_checked"),
            "direct_database_credential": direct_database_credential,
            "direct_database_runtime": (
                database_runtime_status(direct_database_url)
                if direct_database_credential == "rotated"
                else ("not_configured"
                      if direct_database_credential == "not_configured"
                      else "not_checked")),
            "paddle_environment": (
                "sandbox" if paddle_mode == "sandbox" else "production"),
        }
        payload = {
            "status": ("ok" if (
                           checks["secret_key"] == "configured"
                           and checks["paddle_webhook_signing"] == "configured"
                           and checks["paddle_api"] == "configured"
                           and checks["database_credential"] == "rotated"
                           and checks["database_runtime"] == "ready"
                           and checks["direct_database_credential"]
                               in ("rotated", "not_configured")
                           and checks["direct_database_runtime"]
                               in ("ready", "not_configured")
                           and checks["paddle_environment"] == "production")
                       else "degraded"),
            "role": "backend",
            "commit": (_os.environ.get("RENDER_GIT_COMMIT")
                       or "unknown")[:12],
            "checks": checks,
        }
        # Release certification must observe this process and this database,
        # not a stale edge/proxy response from before a deploy or outage.
        return (
            payload,
            200 if payload["status"] == "ok" else 503,
            {
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    configured_secret = _configured_app_secret()
    # A fixed fallback made every JWT/session forgeable on a misconfigured
    # deployment. A random process-local key makes the mistake noisy (healthz
    # is degraded and sessions do not survive workers/restarts) but never
    # silently grants an attacker a known signing key.
    app.config['SECRET_KEY'] = configured_secret or secrets.token_urlsafe(48)
    app.config['DATABASE_URL'] = os.getenv("DATABASE_URL")
    app.teardown_appcontext(close_db)

    # ── Blueprints ────────────────────────────────────────────────────
    app.register_blueprint(auth_bp,             url_prefix='/auth')
    app.register_blueprint(verify_bp,           url_prefix='/verify')
    app.register_blueprint(paddle_checkout_bp)
    app.register_blueprint(paddle_webhook)
    app.register_blueprint(admin_bp,            url_prefix='/admin')
    app.register_blueprint(google_auth_bp,       url_prefix='/auth')
    app.register_blueprint(github_bp, url_prefix='/auth')
    app.register_blueprint(deploy_bp)
    app.register_blueprint(supabase_bp,  url_prefix='/supabase')
    app.register_blueprint(stripe_bp,    url_prefix='/stripe')
    app.register_blueprint(ai_proxy_bp)
    app.register_blueprint(planner_bp)
    app.register_blueprint(newsletter_bp, url_prefix='/newsletter')
    app.register_blueprint(video_bp)
    app.register_blueprint(admin_video_bp)
    # No url_prefix: this blueprint owns routes under BOTH /onboarding and
    # /admin, so the prefixes live on the routes themselves.
    app.register_blueprint(onboarding_bp)
    # MCP: the editor as tools for an outside model. No UI anywhere; reachable
    # only with a token the admin account minted (see routes/mcp.py).
    app.register_blueprint(mcp_bp)
    # ...and the OAuth server that lets claude.ai add it as a connector at all.
    # No url_prefix: RFC 9728/8414 discovery documents MUST sit at the domain
    # root, or the client never finds them.
    app.register_blueprint(mcp_oauth_bp)
    # Founder-only read-only metrics for the signed Valmera iPhone widgets.
    app.register_blueprint(phone_status_bp)

    # ── Automated newsletter / lifecycle emails ───────────────────────
    # Started once per gunicorn worker; the advisory lock inside the tick
    # guarantees only one worker actually sends on any fire.
    try:
        start_newsletter_scheduler(app)
    except Exception as e:
        app.logger.error("could not start newsletter scheduler: %s", e)

    # ── Billing reconciliation (round 59) ─────────────────────────────
    # Hourly: make every subscription's DB state match Paddle's, and nudge
    # anyone whose card was refused. Webhooks are the fast path and not a
    # record — when one is dropped nothing else was ever going to notice, which
    # is how the admin came to show a "converted" customer whose payment Paddle
    # had refused. Same advisory-lock pattern as the newsletter tick.
    try:
        from billing_sync import start_billing_scheduler
        start_billing_scheduler(app)
    except Exception as e:
        app.logger.error("could not start billing sync scheduler: %s", e)

    # New paid-subscriber alerts are persisted before Brevo is called. The
    # fast path sends immediately; this small scheduler recovers a Render
    # recycle or temporary email outage without touching billing state.
    try:
        from paid_subscription_alert import start_scheduler
        start_scheduler(app)
    except Exception as e:
        app.logger.error("could not start paid alert scheduler: %s", e)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
