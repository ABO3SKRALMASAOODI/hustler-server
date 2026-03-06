import os
import jwt
import requests
from flask import Blueprint, request, jsonify, current_app
import psycopg2
from psycopg2.extras import RealDictCursor
from flask_cors import CORS

paypal_bp = Blueprint('paypal', __name__)
CORS(paypal_bp, supports_credentials=True)

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_API_URL = "https://api-m.paypal.com"  # Use sandbox URL if testing

# Helper to verify JWT token
def verify_token(token):
    try:
        payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=["HS256"])
        return payload['sub']
    except Exception:
        return None

# Helper to connect to database
def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)

# Route to handle subscription confirmation
@paypal_bp.route('/subscription', methods=['POST'])
def handle_subscription():
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"success": False, "message": "Missing token"}), 401

    token = auth_header.split(" ")[1]
    user_id = verify_token(token)
    if not user_id:
        return jsonify({"success": False, "message": "Invalid token"}), 401

    data = request.get_json()
    subscription_id = data.get('subscriptionId')
    if not subscription_id:
        return jsonify({"success": False, "message": "Missing subscription ID"}), 400

    # OPTIONAL: Verify subscription with PayPal
    access_token = get_paypal_access_token()
    if not access_token:
        return jsonify({"success": False, "message": "PayPal auth failed"}), 500

    subscription_details = get_subscription_details(subscription_id, access_token)
    if not subscription_details or subscription_details.get("status") not in ["ACTIVE", "APPROVAL_PENDING"]:
        return jsonify({"success": False, "message": "Invalid or inactive subscription"}), 400

    # Mark user as subscribed
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_subscribed = 1 WHERE id = %s", (user_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(e)
        return jsonify({"success": False, "message": "Database error"}), 500

    return jsonify({"success": True, "message": "Subscription confirmed"}), 200

# Helper to get PayPal access token
def get_paypal_access_token():
    try:
        response = requests.post(f"{PAYPAL_API_URL}/v1/oauth2/token",
                                 headers={"Accept": "application/json"},
                                 auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
                                 data={"grant_type": "client_credentials"})
        if response.status_code == 200:
            return response.json().get("access_token")
    except Exception as e:
        print(e)
    return None

# Helper to get subscription details
def get_subscription_details(subscription_id, access_token):
    try:
        response = requests.get(f"{PAYPAL_API_URL}/v1/billing/subscriptions/{subscription_id}",
                                headers={"Authorization": f"Bearer {access_token}",
                                         "Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(e)
    return None
