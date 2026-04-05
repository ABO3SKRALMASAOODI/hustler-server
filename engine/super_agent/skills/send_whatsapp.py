"""
WhatsApp messaging skill — send messages to WhatsApp contacts.
"""

import json
import os
from engine.super_agent.skills.base_skill import BaseSkill


class SendWhatsAppSkill(BaseSkill):
    SKILL_TYPE = "send_whatsapp"
    DISPLAY_NAME = "WhatsApp Messaging"
    DESCRIPTION = "Send messages to WhatsApp contacts. Requires WhatsApp Business API integration."
    CATEGORY = "communication"
    CONFIG_SCHEMA = {
        "phone_number_id": {"type": "string", "description": "WhatsApp Business phone number ID"},
        "access_token": {"type": "string", "description": "Meta access token (or use env var)"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "send_whatsapp",
                "description": (
                    "Send a WhatsApp message to a phone number. "
                    "The number must include the country code without + (e.g., 14155552671). "
                    "Messages are limited to 4096 characters."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient phone number with country code (no +)"},
                        "message": {"type": "string", "description": "Message text to send"},
                    },
                    "required": ["to", "message"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        phone_number_id = config.get("phone_number_id", "")
        access_token = config.get("access_token") or os.getenv("WHATSAPP_ACCESS_TOKEN")

        def send_whatsapp(to, message):
            if not phone_number_id:
                return "ERROR: WhatsApp phone_number_id not configured. Set it in the skill config."
            if not access_token:
                return "ERROR: WhatsApp access token not configured."

            from super_agent.channels.whatsapp import WhatsAppChannel
            result = WhatsAppChannel.send_message(phone_number_id, to, message, access_token=access_token)
            if result.get("ok"):
                return f"WhatsApp message sent to {to} successfully."
            return f"ERROR: {result.get('error', 'Unknown error')}"

        return {"send_whatsapp": send_whatsapp}
