"""
Notification Hub skill — smart notification routing across all platforms.

The agent can send notifications to the right platform at the right time.
Supports priority levels, platform preferences, and batch notifications.
"""

import json
import os
from engine.super_agent.skills.base_skill import BaseSkill


class NotificationHubSkill(BaseSkill):
    SKILL_TYPE = "notification_hub"
    DISPLAY_NAME = "Notification Hub"
    DESCRIPTION = "Smart notification routing. Send alerts to WhatsApp, Telegram, Slack, or email — the right message to the right place at the right time."
    CATEGORY = "automation"
    CONFIG_SCHEMA = {
        "default_platform": {
            "type": "string",
            "enum": ["whatsapp", "telegram", "slack", "email"],
            "description": "Default notification platform",
            "default": "email",
        },
        "whatsapp_phone_number_id": {"type": "string", "description": "WhatsApp Business phone number ID"},
        "whatsapp_recipient": {"type": "string", "description": "Default WhatsApp recipient number"},
        "telegram_bot_token": {"type": "string", "description": "Telegram bot token"},
        "telegram_chat_id": {"type": "string", "description": "Default Telegram chat ID"},
        "slack_bot_token": {"type": "string", "description": "Slack bot token"},
        "slack_channel": {"type": "string", "description": "Default Slack channel ID"},
        "email_recipient": {"type": "string", "description": "Default email recipient"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "notify",
                "description": (
                    "Send a notification to the user via their preferred platform. "
                    "Automatically routes to the configured platform (WhatsApp, Telegram, Slack, or email). "
                    "Use priority levels to indicate urgency: low (informational), normal (standard), high (important), critical (immediate action needed)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Notification message"},
                        "title": {"type": "string", "description": "Optional notification title/subject"},
                        "priority": {
                            "type": "string",
                            "enum": ["low", "normal", "high", "critical"],
                            "description": "Priority level (default: normal)",
                        },
                        "platform": {
                            "type": "string",
                            "enum": ["whatsapp", "telegram", "slack", "email", "all"],
                            "description": "Override platform. Use 'all' to send to every configured platform.",
                        },
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "notify_batch",
                "description": (
                    "Send multiple notifications at once. "
                    "Each notification can go to a different platform with different priority. "
                    "Perfect for daily digests, multi-channel alerts, or status updates."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "notifications": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"},
                                    "title": {"type": "string"},
                                    "platform": {"type": "string"},
                                    "priority": {"type": "string"},
                                },
                                "required": ["message"],
                            },
                            "description": "List of notifications to send",
                        },
                    },
                    "required": ["notifications"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        default_platform = config.get("default_platform", "email")

        def _format_message(message, title=None, priority="normal"):
            prefix = ""
            if priority == "critical":
                prefix = "CRITICAL: "
            elif priority == "high":
                prefix = "IMPORTANT: "

            if title:
                return f"{prefix}{title}\n\n{message}"
            return f"{prefix}{message}"

        def _send_to_platform(platform, message, title=None):
            """Send to a specific platform. Returns success message or error."""

            if platform == "whatsapp":
                phone_id = config.get("whatsapp_phone_number_id", "")
                recipient = config.get("whatsapp_recipient", "")
                token = config.get("whatsapp_access_token") or os.getenv("WHATSAPP_ACCESS_TOKEN")
                if not phone_id or not recipient:
                    return "WhatsApp: Not configured (missing phone_number_id or recipient)"
                from super_agent.channels.whatsapp import WhatsAppChannel
                result = WhatsAppChannel.send_message(phone_id, recipient, message, access_token=token)
                return f"WhatsApp: {'Sent' if result.get('ok') else result.get('error', 'Failed')}"

            elif platform == "telegram":
                bot_token = config.get("telegram_bot_token", "")
                chat_id = config.get("telegram_chat_id", "")
                if not bot_token or not chat_id:
                    return "Telegram: Not configured (missing bot_token or chat_id)"
                from super_agent.channels.telegram import TelegramChannel
                result = TelegramChannel.send_message(bot_token, chat_id, message)
                return f"Telegram: {'Sent' if result.get('ok') else result.get('error', 'Failed')}"

            elif platform == "slack":
                bot_token = config.get("slack_bot_token", "")
                channel = config.get("slack_channel", "")
                if not bot_token or not channel:
                    return "Slack: Not configured (missing bot_token or channel)"
                from super_agent.channels.slack import SlackChannel
                if title:
                    result = SlackChannel.send_rich_message(bot_token, channel, title, message)
                else:
                    result = SlackChannel.send_message(bot_token, channel, message)
                return f"Slack: {'Sent' if result.get('ok') else result.get('error', 'Failed')}"

            elif platform == "email":
                recipient = config.get("email_recipient", "")
                if not recipient:
                    return "Email: Not configured (missing email_recipient)"
                api_key = os.getenv("BREVO_API_KEY")
                if not api_key:
                    return "Email: BREVO_API_KEY not set"
                import requests
                resp = requests.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={"api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "sender": {"name": "Valmera Agent", "email": "support@valmera.io"},
                        "to": [{"email": recipient}],
                        "subject": title or "Agent Notification",
                        "htmlContent": f"<p>{message.replace(chr(10), '<br>')}</p>",
                    },
                    timeout=15,
                )
                return f"Email: {'Sent' if resp.status_code in (200, 201) else 'Failed'}"

            return f"Unknown platform: {platform}"

        def notify(message, title=None, priority="normal", platform=None):
            target = platform or default_platform
            formatted = _format_message(message, title, priority)

            if target == "all":
                results = []
                for p in ["whatsapp", "telegram", "slack", "email"]:
                    r = _send_to_platform(p, formatted, title)
                    if "Not configured" not in r:
                        results.append(r)
                if not results:
                    return "ERROR: No platforms configured. Set up at least one platform in the Notification Hub skill config."
                return "Notification sent:\n" + "\n".join(f"- {r}" for r in results)

            return _send_to_platform(target, formatted, title)

        def notify_batch(notifications):
            results = []
            for i, n in enumerate(notifications[:20], 1):
                msg = n.get("message", "")
                if not msg:
                    continue
                title = n.get("title")
                platform = n.get("platform") or default_platform
                priority = n.get("priority", "normal")
                formatted = _format_message(msg, title, priority)
                r = _send_to_platform(platform, formatted, title)
                results.append(f"{i}. {r}")

            return f"Batch notification results ({len(results)} sent):\n" + "\n".join(results)

        return {"notify": notify, "notify_batch": notify_batch}
