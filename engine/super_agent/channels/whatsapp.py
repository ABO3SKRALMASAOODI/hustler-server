"""
WhatsApp channel adapter — Meta Cloud API.

Handles:
  - Sending messages (text, templates)
  - Verifying webhook signatures
  - Parsing incoming webhook payloads

Requires env vars:
  WHATSAPP_ACCESS_TOKEN — Meta permanent access token
  WHATSAPP_VERIFY_TOKEN — Custom string for webhook verification
"""

import os
import json
import hmac
import hashlib
import logging
import requests

log = logging.getLogger("whatsapp_channel")

META_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppChannel:

    @staticmethod
    def send_message(phone_number_id, to_number, text, access_token=None):
        """
        Send a text message via WhatsApp Business API.

        phone_number_id: The WhatsApp Business phone number ID
        to_number: Recipient phone (with country code, no +)
        text: Message body
        access_token: Meta access token (falls back to env var)
        """
        token = access_token or os.getenv("WHATSAPP_ACCESS_TOKEN")
        if not token:
            return {"error": "WHATSAPP_ACCESS_TOKEN not configured"}

        url = f"{META_API_BASE}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": text[:4096]},  # WhatsApp limit
        }

        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                msg_id = data.get("messages", [{}])[0].get("id", "")
                return {"ok": True, "message_id": msg_id}
            else:
                error = data.get("error", {}).get("message", resp.text[:300])
                return {"error": f"WhatsApp API error: {error}"}
        except Exception as e:
            return {"error": f"WhatsApp send failed: {str(e)[:300]}"}

    @staticmethod
    def send_interactive(phone_number_id, to_number, body_text, buttons, access_token=None):
        """Send an interactive message with buttons."""
        token = access_token or os.getenv("WHATSAPP_ACCESS_TOKEN")
        if not token:
            return {"error": "WHATSAPP_ACCESS_TOKEN not configured"}

        url = f"{META_API_BASE}/{phone_number_id}/messages"
        button_list = []
        for i, btn in enumerate(buttons[:3]):  # Max 3 buttons
            button_list.append({
                "type": "reply",
                "reply": {"id": f"btn_{i}", "title": btn[:20]},  # Max 20 chars
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text[:1024]},
                "action": {"buttons": button_list},
            },
        }

        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            return resp.json()
        except Exception as e:
            return {"error": str(e)[:300]}

    @staticmethod
    def verify_webhook(request_args):
        """
        Handle Meta webhook verification (GET request).
        Returns the hub.challenge if verification succeeds.
        """
        verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "valmera_whatsapp_verify")
        mode = request_args.get("hub.mode")
        token = request_args.get("hub.verify_token")
        challenge = request_args.get("hub.challenge")

        if mode == "subscribe" and token == verify_token:
            return challenge
        return None

    @staticmethod
    def verify_signature(payload_bytes, signature_header, app_secret=None):
        """Verify the X-Hub-Signature-256 header from Meta."""
        secret = app_secret or os.getenv("WHATSAPP_APP_SECRET", "")
        if not secret or not signature_header:
            return True  # Skip verification if no secret configured

        expected = "sha256=" + hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    def parse_webhook(payload):
        """
        Parse an incoming WhatsApp webhook payload.
        Returns list of parsed messages: [{from_number, text, message_id, timestamp}]
        """
        messages = []
        try:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

                    for msg in value.get("messages", []):
                        parsed = {
                            "phone_number_id": phone_number_id,
                            "from_number": msg.get("from", ""),
                            "message_id": msg.get("id", ""),
                            "timestamp": msg.get("timestamp", ""),
                            "type": msg.get("type", "text"),
                        }

                        if msg.get("type") == "text":
                            parsed["text"] = msg.get("text", {}).get("body", "")
                        elif msg.get("type") == "interactive":
                            reply = msg.get("interactive", {}).get("button_reply", {})
                            parsed["text"] = reply.get("title", "")
                        elif msg.get("type") == "image":
                            parsed["text"] = msg.get("image", {}).get("caption", "[Image received]")
                        else:
                            parsed["text"] = f"[{msg.get('type', 'unknown')} message received]"

                        if parsed["text"]:
                            messages.append(parsed)
        except Exception as e:
            log.error(f"Failed to parse WhatsApp webhook: {e}")

        return messages
