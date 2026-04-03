import os
import requests
from flask import Blueprint, request, jsonify, current_app
from functools import wraps
import jwt

newsletter_bp = Blueprint('newsletter', __name__)

ADMIN_EMAIL = "thevalmera@gmail.com"
BREVO_BASE = "https://api.brevo.com/v3"
NEWSLETTER_LIST_NAME = "Valmera Newsletter"


def _brevo_headers():
    return {
        "accept": "application/json",
        "api-key": os.getenv("BREVO_API_KEY"),
        "content-type": "application/json",
    }


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header[len('Bearer '):]
        if not token:
            return jsonify({'error': 'Unauthorized'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            if data.get('email', '') != ADMIN_EMAIL:
                return jsonify({'error': 'Forbidden'}), 403
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


def _get_or_create_list():
    """Get the newsletter list ID from Brevo, creating it if it doesn't exist."""
    headers = _brevo_headers()

    # Fetch all lists
    res = requests.get(f"{BREVO_BASE}/contacts/lists", headers=headers, params={"limit": 50, "offset": 0})
    if res.status_code == 200:
        for lst in res.json().get("lists", []):
            if lst["name"] == NEWSLETTER_LIST_NAME:
                return lst["id"]

    # Create if not found
    res = requests.post(f"{BREVO_BASE}/contacts/lists", headers=headers, json={
        "name": NEWSLETTER_LIST_NAME,
        "folderId": 1,
    })
    if res.status_code == 201:
        return res.json().get("id")

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC: Subscribe to newsletter
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/subscribe', methods=['POST'])
def subscribe():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email is required'}), 400

    list_id = _get_or_create_list()
    if not list_id:
        return jsonify({'error': 'Newsletter service unavailable'}), 500

    headers = _brevo_headers()

    # Add or update contact in Brevo and assign to list
    payload = {
        "email": email,
        "listIds": [list_id],
        "updateEnabled": True,
    }
    res = requests.post(f"{BREVO_BASE}/contacts", headers=headers, json=payload)

    if res.status_code in (201, 204):
        return jsonify({'message': 'Subscribed successfully'}), 200

    # If contact already exists, add to list
    if res.status_code == 400 and "already exist" in res.text.lower():
        add_res = requests.post(
            f"{BREVO_BASE}/contacts/lists/{list_id}/contacts/add",
            headers=headers,
            json={"emails": [email]},
        )
        if add_res.status_code in (200, 201, 204):
            return jsonify({'message': 'Subscribed successfully'}), 200

    return jsonify({'error': 'Failed to subscribe'}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC: Unsubscribe from newsletter
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/unsubscribe', methods=['POST'])
def unsubscribe():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    list_id = _get_or_create_list()
    if not list_id:
        return jsonify({'error': 'Newsletter service unavailable'}), 500

    headers = _brevo_headers()
    res = requests.post(
        f"{BREVO_BASE}/contacts/lists/{list_id}/contacts/remove",
        headers=headers,
        json={"emails": [email]},
    )

    if res.status_code in (200, 201, 204):
        return jsonify({'message': 'Unsubscribed successfully'}), 200

    return jsonify({'error': 'Failed to unsubscribe'}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Get subscriber list + count
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/subscribers', methods=['GET'])
@admin_required
def get_subscribers():
    list_id = _get_or_create_list()
    if not list_id:
        return jsonify({'error': 'Newsletter service unavailable'}), 500

    headers = _brevo_headers()
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))

    res = requests.get(
        f"{BREVO_BASE}/contacts/lists/{list_id}/contacts",
        headers=headers,
        params={"limit": limit, "offset": offset},
    )

    if res.status_code == 200:
        data = res.json()
        contacts = data.get("contacts", [])
        return jsonify({
            'subscribers': [{"email": c["email"], "id": c.get("id")} for c in contacts],
            'total': data.get("count", len(contacts)),
        }), 200

    return jsonify({'subscribers': [], 'total': 0}), 200


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Send newsletter to all subscribers
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/send', methods=['POST'])
@admin_required
def send_newsletter():
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject') or '').strip()
    html_content = (data.get('htmlContent') or '').strip()

    if not subject:
        return jsonify({'error': 'Subject is required'}), 400
    if not html_content:
        return jsonify({'error': 'HTML content is required'}), 400

    list_id = _get_or_create_list()
    if not list_id:
        return jsonify({'error': 'Newsletter service unavailable'}), 500

    headers = _brevo_headers()
    sender_email = os.getenv("FROM_EMAIL", "support@valmera.io")
    sender_name = os.getenv("FROM_NAME", "Valmera")

    # Add unsubscribe footer to HTML content
    unsubscribe_footer = (
        '<br/><hr style="border:none;border-top:1px solid #333;margin:32px 0 16px"/>'
        '<p style="font-size:12px;color:#888;text-align:center;">'
        'You received this because you subscribed to the Valmera newsletter.<br/>'
        'To unsubscribe, reply to this email with "unsubscribe" or visit '
        '<a href="https://valmera.io" style="color:#dc2626;">valmera.io</a>.'
        '</p>'
    )
    full_html = html_content + unsubscribe_footer

    # Create an email campaign
    campaign_payload = {
        "name": f"Newsletter: {subject[:60]}",
        "subject": subject,
        "sender": {"name": sender_name, "email": sender_email},
        "type": "classic",
        "htmlContent": full_html,
        "recipients": {"listIds": [list_id]},
    }

    res = requests.post(f"{BREVO_BASE}/emailCampaigns", headers=headers, json=campaign_payload)

    if res.status_code != 201:
        error_detail = res.json().get("message", "Unknown error") if res.headers.get("content-type", "").startswith("application/json") else res.text
        return jsonify({'error': f'Failed to create campaign: {error_detail}'}), 500

    campaign_id = res.json().get("id")

    # Send the campaign immediately
    send_res = requests.post(f"{BREVO_BASE}/emailCampaigns/{campaign_id}/sendNow", headers=headers)

    if send_res.status_code in (200, 204):
        return jsonify({'message': 'Newsletter sent successfully', 'campaignId': campaign_id}), 200

    # If sendNow fails, try to return useful info
    error_detail = ""
    try:
        error_detail = send_res.json().get("message", "")
    except Exception:
        error_detail = send_res.text
    return jsonify({'error': f'Campaign created but failed to send: {error_detail}', 'campaignId': campaign_id}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN: Get past campaigns
# ─────────────────────────────────────────────────────────────────────────────

@newsletter_bp.route('/campaigns', methods=['GET'])
@admin_required
def get_campaigns():
    headers = _brevo_headers()
    limit = int(request.args.get('limit', 20))
    offset = int(request.args.get('offset', 0))

    res = requests.get(
        f"{BREVO_BASE}/emailCampaigns",
        headers=headers,
        params={"type": "classic", "limit": limit, "offset": offset, "sort": "desc"},
    )

    if res.status_code == 200:
        data = res.json()
        campaigns = []
        for c in data.get("campaigns", []):
            campaigns.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "subject": c.get("subject"),
                "status": c.get("status"),
                "sentDate": c.get("sentDate") or c.get("scheduledAt"),
                "stats": c.get("statistics", {}).get("globalStats", {}),
            })
        return jsonify({'campaigns': campaigns, 'total': data.get("count", 0)}), 200

    return jsonify({'campaigns': [], 'total': 0}), 200
