"""
Slack channel adapter — Slack Web API + Events API.

Handles:
  - Sending messages (text, blocks, attachments)
  - Verifying Slack request signatures
  - Parsing incoming events

Requires: Slack Bot Token (xoxb-...) from Slack App
"""

import os
import hmac
import hashlib
import time
import json
import logging
import requests

log = logging.getLogger("slack_channel")

SLACK_API_BASE = "https://slack.com/api"


class SlackChannel:

    @staticmethod
    def send_message(bot_token, channel_id, text, blocks=None, thread_ts=None):
        """
        Send a message to a Slack channel or DM.

        bot_token: The bot's xoxb-... token
        channel_id: Slack channel or DM ID
        text: Fallback text (shown in notifications)
        blocks: Optional Block Kit blocks for rich formatting
        thread_ts: Optional thread timestamp for threaded replies
        """
        url = f"{SLACK_API_BASE}/chat.postMessage"
        payload = {
            "channel": channel_id,
            "text": text[:4000],
        }

        if blocks:
            payload["blocks"] = json.dumps(blocks) if isinstance(blocks, list) else blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                return {"ok": True, "ts": data.get("ts"), "channel": data.get("channel")}
            else:
                return {"error": f"Slack API error: {data.get('error', 'Unknown')}"}
        except Exception as e:
            return {"error": f"Slack send failed: {str(e)[:300]}"}

    @staticmethod
    def send_rich_message(bot_token, channel_id, title, text, color="#cc0000", fields=None):
        """Send a message with attachment formatting."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title[:150]},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text[:3000]},
            },
        ]

        if fields:
            field_blocks = []
            for f in fields[:10]:
                field_blocks.append({
                    "type": "mrkdwn",
                    "text": f"*{f.get('title', '')}*\n{f.get('value', '')}",
                })
            blocks.append({"type": "section", "fields": field_blocks})

        return SlackChannel.send_message(bot_token, channel_id, text, blocks=blocks)

    @staticmethod
    def verify_signature(payload_bytes, timestamp, signature, signing_secret=None):
        """
        Verify Slack request signature.
        Returns True if valid.
        """
        secret = signing_secret or os.getenv("SLACK_SIGNING_SECRET", "")
        if not secret:
            return True  # Skip if not configured

        # Check timestamp freshness (< 5 minutes)
        try:
            if abs(time.time() - float(timestamp)) > 300:
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{payload_bytes.decode('utf-8')}"
        computed = "v0=" + hmac.new(
            secret.encode(), sig_basestring.encode(), hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    @staticmethod
    def parse_event(payload):
        """
        Parse an incoming Slack Events API payload.
        Returns: {channel_id, user_id, text, ts, thread_ts, type, event_type}
        """
        result = {
            "channel_id": None,
            "user_id": None,
            "text": "",
            "ts": None,
            "thread_ts": None,
            "type": "message",
            "event_type": None,
        }

        event = payload.get("event", {})
        result["event_type"] = event.get("type")

        if event.get("type") == "message" and not event.get("bot_id"):
            # Only process human messages, not bot messages
            result["channel_id"] = event.get("channel")
            result["user_id"] = event.get("user")
            result["text"] = event.get("text", "")
            result["ts"] = event.get("ts")
            result["thread_ts"] = event.get("thread_ts")
            result["type"] = "message"

        elif event.get("type") == "app_mention":
            result["channel_id"] = event.get("channel")
            result["user_id"] = event.get("user")
            result["text"] = event.get("text", "")
            result["ts"] = event.get("ts")
            result["thread_ts"] = event.get("thread_ts")
            result["type"] = "mention"

        return result

    @staticmethod
    def get_user_info(bot_token, user_id):
        """Get Slack user profile info."""
        url = f"{SLACK_API_BASE}/users.info"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {bot_token}"},
                params={"user": user_id},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                user = data["user"]
                return {
                    "name": user.get("real_name", user.get("name", "")),
                    "email": user.get("profile", {}).get("email", ""),
                }
            return {"error": data.get("error", "Failed")}
        except Exception as e:
            return {"error": str(e)[:300]}
