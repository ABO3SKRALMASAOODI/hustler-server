from flask import Blueprint, request
from models import update_user_subscription_status
from datetime import datetime

paddle_webhook = Blueprint('paddle_webhook', __name__)

@paddle_webhook.route('/webhook/paddle', methods=['POST'])
def handle_webhook():
    payload = request.get_json(force=True)
    print("🔔 Full webhook payload received:", payload)

    event_type = payload.get('event_type')
    data = payload.get('data', {})

    # Process only relevant event types
    if event_type not in (
        'transaction.completed',
        'transaction.paid',
        'subscription.created',
        'subscription.updated',
        'subscription.canceled',
        'subscription.payment_failed',
        'subscription.payment_refunded'
    ):
        print(f"ℹ️ Ignoring irrelevant event: {event_type}")
        return 'OK', 200

    custom_data = data.get('custom_data') or {}
    user_id = custom_data.get('user_id')

    if not user_id:
        print(f"❌ User ID missing in event {event_type}. Ignoring.")
        return 'OK', 200

    subscription_id = data.get('subscription_id') or data.get('id')

    # Activate subscription on payment or creation
    if event_type in ('transaction.completed', 'transaction.paid', 'subscription.created', 'subscription.updated'):
        expiry_date_str = data.get('next_billed_at')
        expiry_date = None

        if expiry_date_str:
            try:
                expiry_date = datetime.fromisoformat(expiry_date_str.replace("Z", "+00:00"))
            except Exception as e:
                print(f"⚠️ Failed to parse next_billed_at date: {e}")

        update_user_subscription_status(user_id, True, expiry_date, subscription_id)
        print(f"✅ User {user_id} subscription activated or updated. Expiry: {expiry_date}, Subscription ID: {subscription_id}")

    # Deactivate subscription on cancellation or payment failure
    elif event_type in ('subscription.canceled', 'subscription.payment_failed', 'subscription.payment_refunded'):
        update_user_subscription_status(user_id, False, None)
        print(f"⚠️ User {user_id} subscription deactivated due to {event_type}")

    return 'OK', 200
