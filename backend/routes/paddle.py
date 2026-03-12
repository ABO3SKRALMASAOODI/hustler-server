from flask import Blueprint, request, jsonify
import requests
import os
import jwt
import datetime

paddle_bp = Blueprint('paddle', __name__)

# ── Plan definitions ──────────────────────────────────────────────────────────

PLANS_LIVE = {
    'plus':  {'price_id': 'pri_01jxj6smtjkfsf22hdr4swyr9j', 'yearly_price_id': 'pri_01kkekq1hcvzvyhh3ffk3nk291', 'monthly_credits': 800},
    'pro':   {'price_id': 'pri_01kk4k4y8c3ygxd620vcxg6ph1', 'yearly_price_id': 'pri_01kkeksjv9pf2nc1gphj67m8ae', 'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4k83cwpmf1jsctgdvhm0n6', 'yearly_price_id': 'pri_01kkektygjg89gywskyj1dycx2', 'monthly_credits': 5000},
    'titan': {'price_id': 'pri_01kkekbegh2q5x3kxn28afbw5d', 'yearly_price_id': 'pri_01kkekf5ksjq5dqbfpxakf1g23', 'monthly_credits': 10000},
    'ace':   {'price_id': 'pri_01kkekgt4zv65t59yw7ybz8w01', 'yearly_price_id': 'pri_01kkekj0am5yfqxx933c6d4tck', 'monthly_credits': 30000},
}

PLANS_SANDBOX = {
    'plus':  {'price_id': 'pri_01jw8722trngfyz12kq158vrz7', 'yearly_price_id': 'SANDBOX_PLUS_YEARLY_TODO',  'monthly_credits': 800},
    'pro':   {'price_id': 'pri_01kk4wvnbxb7nbh426bnk62xa2', 'yearly_price_id': 'SANDBOX_PRO_YEARLY_TODO',   'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4wwr07ce0xp8x4kvdgt8kg', 'yearly_price_id': 'SANDBOX_ULTRA_YEARLY_TODO', 'monthly_credits': 5000},
    'titan': {'price_id': 'SANDBOX_TITAN_MONTHLY_TODO',      'yearly_price_id': 'SANDBOX_TITAN_YEARLY_TODO', 'monthly_credits': 10000},
    'ace':   {'price_id': 'SANDBOX_ACE_MONTHLY_TODO',        'yearly_price_id': 'SANDBOX_ACE_YEARLY_TODO',   'monthly_credits': 30000},
}

PLANS = PLANS_SANDBOX if os.environ.get('PADDLE_MODE') == 'sandbox' else PLANS_LIVE


def get_paddle_base():
    is_sandbox = os.environ.get('PADDLE_MODE') == 'sandbox'
    return "https://sandbox-api.paddle.com" if is_sandbox else "https://api.paddle.com"


def paddle_headers():
    return {
        "Authorization": f"Bearer {os.environ['PADDLE_API_KEY']}",
        "Content-Type": "application/json"
    }


def decode_token(auth_header):
    if not auth_header:
        return None, None
    token = auth_header.split(" ")[1]
    payload = jwt.decode(token, os.environ['SECRET_KEY'], algorithms=["HS256"])
    return payload.get('sub'), payload.get('email')


def _is_within_24h(created_at):
    """Check if user account was created within the last 24 hours."""
    if not created_at:
        return False
    now = datetime.datetime.utcnow()
    if isinstance(created_at, str):
        created_at = datetime.datetime.fromisoformat(created_at)
    # Make both offset-naive for comparison
    if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)
    return (now - created_at).total_seconds() < 86400


# ── Create checkout session ───────────────────────────────────────────────────

@paddle_bp.route('/paddle/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        user_id, user_email = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    data = request.json or {}
    plan = data.get('plan', 'plus')
    billing = data.get('billing', 'monthly')  # 'monthly' or 'yearly'
    use_promo = data.get('use_promo', False)   # 24hr first-month discount

    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400

    # Pick the right price ID based on billing interval
    if billing == 'yearly':
        price_id = PLANS[plan]['yearly_price_id']
    else:
        price_id = PLANS[plan]['price_id']

    body = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "customer": {"email": user_email} if user_email else {},
        "custom_data": {"user_id": user_id, "plan": plan, "billing": billing},
        "collection_mode": "automatic",
        "checkout": {"success_url": "https://thehustlerbot.com/purchase-success"}
    }

    # Apply 24-hour promo: 50% off first month only (monthly plans only)
    if use_promo and billing == 'monthly':
        # Check if user is actually within 24h of registration
        import psycopg2
        from psycopg2.extras import RealDictCursor
        try:
            conn = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=RealDictCursor)
            with conn.cursor() as cur:
                cur.execute("SELECT created_at FROM users WHERE id = %s", (int(user_id),))
                row = cur.fetchone()
            conn.close()

            if row and _is_within_24h(row.get("created_at")):
                # Inline discount: 50% off, first billing period only
                body["discount"] = {
                    "description": "Welcome offer - 50% off first month",
                    "type": "percentage",
                    "amount": "50",
                    "recur": False,
                }
                print(f"🎉 Applying 24h promo for user {user_id}")
            else:
                print(f"⏰ User {user_id} not eligible for 24h promo")
        except Exception as e:
            print(f"⚠️ Promo check failed: {e}")

    print(f'🎯 Checkout: plan={plan}, billing={billing}, promo={use_promo}')

    response = requests.post(
        f"{get_paddle_base()}/transactions",
        headers=paddle_headers(),
        json=body
    )
    print("🔁 Paddle API Response:", response.text)

    if response.status_code != 201:
        return jsonify({"error": "Failed to create checkout session", "details": response.text}), 500

    resp_data = response.json()
    checkout_url = resp_data["data"]["checkout"]["url"]
    return jsonify({"checkout_url": checkout_url})


# ── Check promo eligibility ──────────────────────────────────────────────────

@paddle_bp.route('/paddle/promo-status', methods=['GET'])
def promo_status():
    """Return whether the user is eligible for the 24h first-registration promo."""
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"eligible": False}), 200
    if not user_id:
        return jsonify({"eligible": False}), 200

    import psycopg2
    from psycopg2.extras import RealDictCursor
    try:
        conn = psycopg2.connect(os.environ['DATABASE_URL'], cursor_factory=RealDictCursor)
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM users WHERE id = %s", (int(user_id),))
            row = cur.fetchone()
        conn.close()

        if not row or not row.get("created_at"):
            return jsonify({"eligible": False}), 200

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.datetime.fromisoformat(created_at)
        if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
            created_at = created_at.replace(tzinfo=None)

        now = datetime.datetime.utcnow()
        elapsed = (now - created_at).total_seconds()

        if elapsed < 86400:
            remaining = int(86400 - elapsed)
            return jsonify({
                "eligible": True,
                "seconds_remaining": remaining,
                "created_at": created_at.isoformat(),
            }), 200
        else:
            return jsonify({"eligible": False}), 200

    except Exception as e:
        print(f"⚠️ Promo status check error: {e}")
        return jsonify({"eligible": False}), 200


# ── Upgrade / downgrade ───────────────────────────────────────────────────────

@paddle_bp.route('/paddle/change-plan', methods=['POST'])
def change_plan():
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    data = request.json or {}
    new_plan = data.get('plan')
    billing = data.get('billing', 'monthly')

    if new_plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription"}), 400

    # Get current subscription to find item id
    sub_res = requests.get(
        f"{get_paddle_base()}/subscriptions/{subscription_id}",
        headers=paddle_headers()
    )
    if sub_res.status_code != 200:
        return jsonify({"error": "Could not fetch subscription"}), 500

    # Pick the right price ID
    if billing == 'yearly':
        new_price_id = PLANS[new_plan]['yearly_price_id']
    else:
        new_price_id = PLANS[new_plan]['price_id']

    body = {
        "items": [{"price_id": new_price_id, "quantity": 1}],
        "proration_billing_mode": "do_not_bill"
    }
    res = requests.patch(
        f"{get_paddle_base()}/subscriptions/{subscription_id}",
        headers=paddle_headers(),
        json=body
    )
    if res.status_code not in (200, 202):
        return jsonify({"error": "Failed to change plan", "details": res.text}), 500

    return jsonify({"message": f"Plan will change to {new_plan} at next billing cycle."})


# ── Cancel subscription ───────────────────────────────────────────────────────

@paddle_bp.route('/paddle/cancel-subscription', methods=['POST'])
def cancel_subscription():
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription found"}), 400

    res = requests.post(
        f"{get_paddle_base()}/subscriptions/{subscription_id}/cancel",
        headers=paddle_headers(),
        json={"effective_from": "next_billing_period"}
    )
    if res.status_code not in (200, 204):
        return jsonify({"error": "Failed to cancel", "details": res.text}), 500

    return jsonify({"message": "Subscription will cancel at end of billing period."})