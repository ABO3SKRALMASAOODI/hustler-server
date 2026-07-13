import hashlib
import hmac
import os
import time

from flask import Blueprint, request
from models import update_user_subscription_status
from datetime import datetime

paddle_webhook = Blueprint('paddle_webhook', __name__)

PLAN_CREDITS = {
    'plus':  800,
    'pro':   2400,
    'ultra': 5000,
    'titan': 10000,
    'ace':   30000,
    'free':  0,
}

# Paddle signs every webhook (Paddle-Signature: "ts=...;h1=...", where h1 is
# HMAC-SHA256 of "ts:raw_body" with the endpoint's secret key from
# Paddle > Developer tools > Notifications). Without verification anyone who
# reads the URL can grant themselves any plan. Enforced when
# PADDLE_WEBHOOK_SECRET is set; until then requests pass with a loud warning
# so payments don't break before the env var is configured.
PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET", "")


def _verify_paddle_signature(req):
    header = req.headers.get("Paddle-Signature", "")
    parts = dict(p.split("=", 1) for p in header.split(";") if "=" in p)
    ts, h1 = parts.get("ts"), parts.get("h1")
    if not ts or not h1:
        return False
    try:
        if abs(time.time() - int(ts)) > 300:   # stale/replayed event
            return False
    except ValueError:
        return False
    signed = f"{ts}:".encode() + req.get_data()
    expected = hmac.new(PADDLE_WEBHOOK_SECRET.encode(), signed,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, h1)


@paddle_webhook.route('/webhook/paddle', methods=['POST'])
def handle_webhook():
    if PADDLE_WEBHOOK_SECRET:
        if not _verify_paddle_signature(request):
            print("⛔ Paddle webhook rejected: bad or missing signature")
            return 'Invalid signature', 403
    else:
        print("⚠️  PADDLE_WEBHOOK_SECRET is not set — webhook signature "
              "NOT verified. Set it in the Render env ASAP.")
    payload = request.get_json(force=True)
    print("🔔 Webhook received:", payload.get('event_type'))

    event_type = payload.get('event_type')
    data = payload.get('data', {})

    if event_type not in (
        'transaction.completed', 'transaction.paid',
        'subscription.created', 'subscription.updated',
        'subscription.canceled', 'subscription.payment_failed',
        'subscription.payment_refunded'
    ):
        return 'OK', 200

    custom_data = data.get('custom_data') or {}
    user_id = custom_data.get('user_id')
    if not user_id:
        return 'OK', 200

    plan = custom_data.get('plan', 'plus')
    billing = custom_data.get('billing', 'monthly')
    subscription_id = data.get('subscription_id') or data.get('id')
    # Unknown plan names grant nothing (they used to default to 1000
    # credits, which a forged custom_data string could mint).
    if plan not in PLAN_CREDITS:
        print(f"⛔ Webhook with unknown plan '{plan}' — granting 0 credits")
    monthly_credits = PLAN_CREDITS.get(plan, 0)

    if event_type in ('transaction.completed', 'transaction.paid',
                      'subscription.created', 'subscription.updated'):
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(
                    expiry_date_str.replace("Z", "+00:00")
                )
            except Exception as e:
                print(f"⚠️ Date parse error: {e}")

        update_user_subscription_status(
            user_id, True, expiry_date, subscription_id,
            plan, monthly_credits
        )
        print(f"✅ User {user_id} on plan {plan} ({billing}) activated. Credits: {monthly_credits}")

    elif event_type in ('subscription.canceled', 'subscription.payment_failed',
                        'subscription.payment_refunded'):
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        print(f"⚠️ User {user_id} reverted to free")

    return 'OK', 200