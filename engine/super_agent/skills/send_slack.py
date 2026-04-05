"""
Slack messaging skill — send messages to Slack channels and DMs.
"""

import os
from engine.super_agent.skills.base_skill import BaseSkill


class SendSlackSkill(BaseSkill):
    SKILL_TYPE = "send_slack"
    DISPLAY_NAME = "Slack Messaging"
    DESCRIPTION = "Send messages to Slack channels and direct messages. Requires a Slack Bot Token."
    CATEGORY = "communication"
    CONFIG_SCHEMA = {
        "bot_token": {"type": "string", "description": "Slack bot token (xoxb-...)"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "send_slack",
                "description": (
                    "Send a message to a Slack channel or DM. "
                    "Use the channel ID (not name). Supports Slack mrkdwn formatting."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Slack channel or DM ID"},
                        "message": {"type": "string", "description": "Message text (Slack mrkdwn supported)"},
                        "title": {"type": "string", "description": "Optional title for rich formatting"},
                    },
                    "required": ["channel", "message"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        bot_token = config.get("bot_token", "")

        def send_slack(channel, message, title=None):
            if not bot_token:
                return "ERROR: Slack bot_token not configured. Set it in the skill config."

            from super_agent.channels.slack import SlackChannel
            if title:
                result = SlackChannel.send_rich_message(bot_token, channel, title, message)
            else:
                result = SlackChannel.send_message(bot_token, channel, message)

            if result.get("ok"):
                return f"Slack message sent to {channel} successfully."
            return f"ERROR: {result.get('error', 'Unknown error')}"

        return {"send_slack": send_slack}
