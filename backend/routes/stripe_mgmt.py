from flask import Blueprint, request, jsonify
from routes.auth import token_required, get_db
import os, json, time

stripe_bp = Blueprint('stripe', __name__)

OUTPUTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "outputs"))

# ── Base URL for preview routes ───────────────────────────────────────────────
BACKEND_BASE = os.getenv("BACKEND_BASE_URL", "https://entrepreneur-bot-backend.onrender.com")


def _get_redirect_base(job_id):
    """
    Determine the correct base URL for Stripe success/cancel redirects.
    If the project is published, use the published domain.
    Otherwise, use the preview-raw URL so it works in the iframe new-tab flow.
    """
    # Check if published
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT published_url FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
        conn.close()
        if row and row.get("published_url"):
            return row["published_url"].rstrip("/")
    except Exception:
        pass

    # Fall back to preview-raw URL (serves the actual app HTML, not the wrapper)
    return f"{BACKEND_BASE}/auth/preview-raw/{job_id}"


def _rewrite_redirect_url(url, job_id):
    """
    Rewrite a success/cancel URL to point to the correct base.
    Handles cases like:
      - window.location.origin + "/success"  → https://backend/auth/preview-raw/<job_id>/success
      - https://myapp.thehustlerbot.com/success → kept as-is (published domain)
      - /success → rewritten to full URL
    """
    if not url:
        return url

    redirect_base = _get_redirect_base(job_id)

    # If URL is already pointing to a published domain, leave it alone
    if url.startswith("https://") and "thehustlerbot.com" in url and "entrepreneur-bot" not in url:
        return url

    # Extract the path portion
    # Handle full URLs like https://entrepreneur-bot-backend.onrender.com/success
    if url.startswith("http"):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
    else:
        path = url.lstrip("/")

    # Strip any existing preview path prefix if present
    # e.g. "auth/preview-raw/abc123/success" → "success"
    preview_prefix = f"auth/preview-raw/{job_id}/"
    if path.startswith(preview_prefix):
        path = path[len(preview_prefix):]
    preview_prefix2 = f"auth/preview/{job_id}/"
    if path.startswith(preview_prefix2):
        path = path[len(preview_prefix2):]

    return f"{redirect_base}/{path}"


@stripe_bp.route('/job/<job_id>/enable-stripe', methods=['POST'])
@token_required
def enable_stripe(user_id, job_id):
    """User submits their Stripe keys — stored in meta.json for this job."""
    data = request.get_json() or {}
    publishable_key = data.get("publishable_key", "").strip()
    secret_key      = data.get("secret_key", "").strip()

    if not publishable_key or not secret_key:
        return jsonify({"error": "Both publishable_key and secret_key are required"}), 400
    if not publishable_key.startswith("pk_"):
        return jsonify({"error": "Invalid publishable key (must start with pk_)"}), 400
    if not secret_key.startswith("sk_"):
        return jsonify({"error": "Invalid secret key (must start with sk_)"}), 400

    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    meta_path = os.path.join(job_folder, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            pass

    meta["stripe_enabled"]         = True
    meta["stripe_publishable_key"] = publishable_key
    meta["stripe_secret_key"]      = secret_key

    with open(meta_path, "w") as f:
        json.dump(meta, f)

    # Signal the waiting agent
    approved_path = os.path.join(job_folder, "stripe_approved.json")
    with open(approved_path, "w") as f:
        json.dump({"approved": True, "ts": time.time()}, f)

    return jsonify({"ok": True}), 200


@stripe_bp.route('/job/<job_id>/stripe-denied', methods=['POST'])
@token_required
def stripe_denied(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    req_path = os.path.join(job_folder, "stripe_requested.json")
    if os.path.exists(req_path):
        os.remove(req_path)

    denied_path = os.path.join(job_folder, "stripe_denied.json")
    with open(denied_path, "w") as f:
        json.dump({"denied": True, "ts": time.time()}, f)

    return jsonify({"ok": True}), 200


@stripe_bp.route('/job/<job_id>/stripe-status', methods=['GET'])
@token_required
def stripe_status(user_id, job_id):
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    meta_path = os.path.join(job_folder, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            pass

    return jsonify({
        "stripe_enabled":         meta.get("stripe_enabled", False),
        "stripe_publishable_key": meta.get("stripe_publishable_key", ""),
    }), 200


@stripe_bp.route('/job/<job_id>/create-payment-intent', methods=['POST'])
def create_payment_intent(job_id):
    """
    Proxy endpoint called by generated apps.
    The app never sees the secret key — it only calls this endpoint.
    No auth token required (called from generated app frontend).
    """
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    meta_path = os.path.join(job_folder, "meta.json")
    if not os.path.exists(meta_path):
        return jsonify({"error": "Stripe not configured"}), 400

    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except Exception:
        return jsonify({"error": "Failed to read config"}), 500

    secret_key = meta.get("stripe_secret_key", "")
    if not secret_key:
        return jsonify({"error": "Stripe not configured for this project"}), 400

    data   = request.get_json() or {}
    amount = data.get("amount")      # in cents
    currency = data.get("currency", "usd")
    metadata = data.get("metadata", {})

    if not amount or not isinstance(amount, int) or amount < 50:
        return jsonify({"error": "amount must be an integer >= 50 (cents)"}), 400

    try:
        import stripe as stripe_lib
        stripe_lib.api_key = secret_key
        intent = stripe_lib.PaymentIntent.create(
            amount=amount,
            currency=currency,
            metadata=metadata,
            automatic_payment_methods={"enabled": True},
        )
        return jsonify({"client_secret": intent.client_secret}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@stripe_bp.route('/job/<job_id>/create-checkout-session', methods=['POST'])
def create_checkout_session(job_id):
    """
    Creates a Stripe Checkout Session for one-time or subscription payments.
    Called by generated apps — secret key stays server-side.
    Rewrites success/cancel URLs to route back to the correct preview or published URL.
    """
    job_folder = os.path.join(OUTPUTS_DIR, job_id)
    if not os.path.isdir(job_folder):
        return jsonify({"error": "Job not found"}), 404

    meta_path = os.path.join(job_folder, "meta.json")
    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except Exception:
        return jsonify({"error": "Failed to read config"}), 500

    secret_key = meta.get("stripe_secret_key", "")
    if not secret_key:
        return jsonify({"error": "Stripe not configured for this project"}), 400

    data         = request.get_json() or {}
    line_items   = data.get("line_items", [])
    mode         = data.get("mode", "payment")       # payment | subscription
    success_url  = data.get("success_url", "")
    cancel_url   = data.get("cancel_url", "")

    if not line_items or not success_url or not cancel_url:
        return jsonify({"error": "line_items, success_url, cancel_url are required"}), 400

    # ── Rewrite redirect URLs to point to correct preview/published base ──
    success_url = _rewrite_redirect_url(success_url, job_id)
    cancel_url  = _rewrite_redirect_url(cancel_url, job_id)

    try:
        import stripe as stripe_lib
        stripe_lib.api_key = secret_key
        session = stripe_lib.checkout.Session.create(
            line_items=line_items,
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return jsonify({"url": session.url, "session_id": session.id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500