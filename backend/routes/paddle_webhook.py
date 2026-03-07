from flask import Blueprint, request
from models import update_user_subscription_status
from datetime import datetime

paddle_webhook = Blueprint('paddle_webhook', __name__)

PLAN_CREDITS = {
    'plus':  1000,
    'pro':   2400,
    'ultra': 5000,
    'free':  0,
}

@paddle_webhook.route('/webhook/paddle', methods=['POST'])
def handle_webhook():
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
    subscription_id = data.get('subscription_id') or data.get('id')
    monthly_credits = PLAN_CREDITS.get(plan, 1000)

    if event_type in ('transaction.completed', 'transaction.paid', 'subscription.created', 'subscription.updated'):
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(expiry_date_str.replace("Z", "+00:00"))
            except Exception as e:
                print(f"⚠️ Date parse error: {e}")
        update_user_subscription_status(user_id, True, expiry_date, subscription_id, plan, monthly_credits)
        print(f"✅ User {user_id} on plan {plan} activated. Credits: {monthly_credits}")

    elif event_type in ('subscription.canceled', 'subscription.payment_failed', 'subscription.payment_refunded'):
        update_user_subscription_status(user_id, False, None, None, 'free', 0)
        print(f"⚠️ User {user_id} reverted to free")

    return 'OK', 200
