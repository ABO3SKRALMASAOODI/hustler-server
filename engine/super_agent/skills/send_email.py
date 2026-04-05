"""
Send Email skill — sends emails via Brevo (reuses existing integration).
"""

import json
import os
import requests
from engine.super_agent.skills.base_skill import BaseSkill


class SendEmailSkill(BaseSkill):
    SKILL_TYPE = "send_email"
    DISPLAY_NAME = "Send Email"
    DESCRIPTION = "Send emails to specified recipients via Brevo transactional email."
    CATEGORY = "communication"
    CONFIG_SCHEMA = {
        "sender_name": {
            "type": "string",
            "description": "Name shown as the email sender",
            "default": "Valmera Agent"
        },
        "sender_email": {
            "type": "string",
            "description": "Sender email address (must be verified in Brevo)",
            "default": "support@valmera.io"
        }
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "send_email",
                "description": (
                    "Send an email to one or more recipients. "
                    "Use for notifications, reports, summaries, or any communication. "
                    "The email body supports HTML formatting."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "email": {"type": "string"},
                                    "name": {"type": "string"}
                                },
                                "required": ["email"]
                            },
                            "description": "List of recipients"
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line"
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body (supports HTML)"
                        }
                    },
                    "required": ["to", "subject", "body"]
                }
            }
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        sender_name = config.get("sender_name", "Valmera Agent")
        sender_email = config.get("sender_email", "support@valmera.io")

        def send_email(to, subject, body):
            api_key = os.getenv("BREVO_API_KEY")
            if not api_key:
                return "ERROR: Email service not configured (missing BREVO_API_KEY)."

            try:
                resp = requests.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={
                        "api-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "sender": {"name": sender_name, "email": sender_email},
                        "to": to,
                        "subject": subject,
                        "htmlContent": body,
                    },
                    timeout=15,
                )

                if resp.status_code in (200, 201):
                    return f"Email sent successfully to {len(to)} recipient(s)."
                else:
                    return f"ERROR: Brevo returned status {resp.status_code}: {resp.text[:300]}"
            except Exception as e:
                return f"ERROR: Failed to send email: {type(e).__name__}: {str(e)[:300]}"

        return {"send_email": send_email}
