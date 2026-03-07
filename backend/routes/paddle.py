from flask import Blueprint, request, jsonify
import requests
import os
import jwt

paddle_bp = Blueprint('paddle', __name__)

PLANS_LIVE = {
    'plus':  {'price_id': 'pri_01jxj6smtjkfsf22hdr4swyr9j', 'monthly_credits': 1000},
    'pro':   {'price_id': 'pri_01kk4k4y8c3ygxd620vcxg6ph1', 'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4k83cwpmf1jsctgdvhm0n6', 'monthly_credits': 5000},
}

PLANS_SANDBOX = {
    'plus':  {'price_id': 'pri_01jw8722trngfyz12kq158vrz7', 'monthly_credits': 1000},
    'pro':   {'price_id': 'pri_01kk4wvnbxb7nbh426bnk62xa2', 'monthly_credits': 2400},
    'ultra': {'price_id': 'pri_01kk4wwr07ce0xp8x4kvdgt8kg', 'monthly_credits': 5000},
}

import os as _os
PLANS = PLANS_SANDBOX if _os.environ.get('PADDLE_MODE') == 'sandbox' else PLANS_LIVE

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

# ── Create checkout session ───────────────────────────────────────────────────
@paddle_bp.route('/paddle/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        user_id, user_email = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
    if not user_id:
        return jsonify({"error": "Missing token"}), 401

    plan = request.json.get('plan', 'plus')
    print(f'🎯 Plan requested: {plan}, body: {request.json}')
    if plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400

    price_id = PLANS[plan]['price_id']
    body = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "customer": {"email": user_email} if user_email else {},
        "custom_data": {"user_id": user_id, "plan": plan},
        "collection_mode": "automatic",
        "checkout": {"success_url": "https://thehustlerbot.com/studio"}
    }
    response = requests.post(f"{get_paddle_base()}/transactions", headers=paddle_headers(), json=body)
    print("🔁 Paddle API Response:", response.text)
    if response.status_code != 201:
        return jsonify({"error": "Failed to create checkout session", "details": response.text}), 500
    data = response.json()
    checkout_url = data["data"]["checkout"]["url"]
    return jsonify({"checkout_url": checkout_url})

# ── Upgrade / downgrade ───────────────────────────────────────────────────────
@paddle_bp.route('/paddle/change-plan', methods=['POST'])
def change_plan():
    try:
        user_id, _ = decode_token(request.headers.get('Authorization'))
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    new_plan = request.json.get('plan')
    if new_plan not in PLANS:
        return jsonify({"error": "Invalid plan"}), 400

    from models import get_user_subscription_id
    subscription_id = get_user_subscription_id(user_id)
    if not subscription_id:
        return jsonify({"error": "No active subscription"}), 400

    # Get current subscription to find item id
    sub_res = requests.get(f"{get_paddle_base()}/subscriptions/{subscription_id}", headers=paddle_headers())
    if sub_res.status_code != 200:
        return jsonify({"error": "Could not fetch subscription"}), 500
    sub_data = sub_res.json()['data']
    item_id = sub_data['items'][0]['price']['id']

    body = {
        "items": [{"price_id": PLANS[new_plan]['price_id'], "quantity": 1}],
        "proration_billing_mode": "do_not_bill"  # take effect next cycle
    }
    res = requests.patch(f"{get_paddle_base()}/subscriptions/{subscription_id}", headers=paddle_headers(), json=body)
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
