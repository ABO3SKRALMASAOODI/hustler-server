"""
Telegram messaging skill — send messages via Telegram bot.
"""

import os
from engine.super_agent.skills.base_skill import BaseSkill


class SendTelegramSkill(BaseSkill):
    SKILL_TYPE = "send_telegram"
    DISPLAY_NAME = "Telegram Messaging"
    DESCRIPTION = "Send messages via a Telegram bot. Requires a bot token from @BotFather."
    CATEGORY = "communication"
    CONFIG_SCHEMA = {
        "bot_token": {"type": "string", "description": "Telegram bot token from @BotFather"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "send_telegram",
                "description": (
                    "Send a message via Telegram. Supports Markdown formatting. "
                    "You need the chat_id of the recipient (user or group)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "string", "description": "Telegram chat ID (user, group, or channel)"},
                        "message": {"type": "string", "description": "Message text (Markdown supported)"},
                    },
                    "required": ["chat_id", "message"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        bot_token = config.get("bot_token", "")

        def send_telegram(chat_id, message):
            if not bot_token:
                return "ERROR: Telegram bot_token not configured. Set it in the skill config."

            from super_agent.channels.telegram import TelegramChannel
            result = TelegramChannel.send_message(bot_token, chat_id, message)
            if result.get("ok"):
                return f"Telegram message sent to chat {chat_id} successfully."
            return f"ERROR: {result.get('error', 'Unknown error')}"

        return {"send_telegram": send_telegram}
