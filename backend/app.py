from flask import Flask
from flask_cors import CORS
from routes.auth import auth_bp
from routes.verify_email import verify_bp
from routes.paddle import paddle_bp as paddle_checkout_bp
from routes.paddle_webhook import paddle_webhook
from routes.admin import admin_bp
from routes.google_auth import google_auth_bp
from models import init_db
import os
from dotenv import load_dotenv
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
from subscription_gate_hotfix import install_subscription_gate_hotfix

load_dotenv()


def create_app():
    app = Flask(__name__)

    # Empty projects stay on the lightweight concierge path. The subscription
    # wall begins once source media exists, while shorts and existing projects
    # retain their current gate behavior.
    install_subscription_gate_hotfix()

    CORS(app,
         origins="*",
         allow_headers=["Content-Type", "Authorization"],
         methods=["GET", "POST", "OPTIONS", "PUT", "DELETE", "PATCH"])

    # Round 79 — which code is this service actually running? The worker and
    # executor answer that (/health carries code_version); this service could
    # not, and a stale deploy was only discoverable by a user-facing
    # validation error quoting last week's schema. Render injects the commit.
    @app.route("/healthz")
    def healthz():
        import os as _os
        return {"status": "ok", "role": "backend",
                "commit": (_os.environ.get("RENDER_GIT_COMMIT")
                           or "unknown")[:12]}

    @app.before_request
    def handle_options():
        from flask import request, Response
        if request.method == "OPTIONS":
            r = Response()
            r.headers["Access-Control-Allow-Origin"] = "*"
            r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE, PATCH"
            return r

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE, PATCH"
        return response

    app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "supersecretkey")
    app.config['DATABASE_URL'] = os.getenv("DATABASE_URL")

    if os.getenv("SKIP_DB_INIT") == "1":
        print("⚠️  Skipping DB init (SKIP_DB_INIT=1)")
    else:
        init_db(app)

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
