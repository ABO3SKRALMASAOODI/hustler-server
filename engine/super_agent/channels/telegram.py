"""
Telegram channel adapter — Telegram Bot API.

Handles:
  - Sending messages (text, markdown, buttons)
  - Setting up webhooks
  - Parsing incoming updates

Requires: Bot token from @BotFather
"""

import os
import json
import logging
import requests

log = logging.getLogger("telegram_channel")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramChannel:

    @staticmethod
    def send_message(bot_token, chat_id, text, parse_mode="Markdown", reply_markup=None):
        """
        Send a text message via Telegram Bot API.

        bot_token: The bot token from @BotFather
        chat_id: Telegram chat ID (user, group, or channel)
        text: Message text (supports Markdown)
        """
        url = f"{TELEGRAM_API_BASE.format(token=bot_token)}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],  # Telegram limit
            "parse_mode": parse_mode,
        }

        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()
            if data.get("ok"):
                return {"ok": True, "message_id": data["result"]["message_id"]}
            else:
                return {"error": f"Telegram API error: {data.get('description', 'Unknown')}"}
        except Exception as e:
            return {"error": f"Telegram send failed: {str(e)[:300]}"}

    @staticmethod
    def send_with_buttons(bot_token, chat_id, text, buttons):
        """Send a message with inline keyboard buttons."""
        keyboard = []
        for btn in buttons:
            if isinstance(btn, str):
                keyboard.append([{"text": btn, "callback_data": btn[:64]}])
            elif isinstance(btn, dict):
                keyboard.append([{
                    "text": btn.get("text", ""),
                    "callback_data": btn.get("data", btn.get("text", ""))[:64],
                }])

        reply_markup = {"inline_keyboard": keyboard}
        return TelegramChannel.send_message(bot_token, chat_id, text, reply_markup=reply_markup)

    @staticmethod
    def set_webhook(bot_token, webhook_url):
        """Set the webhook URL for a Telegram bot."""
        url = f"{TELEGRAM_API_BASE.format(token=bot_token)}/setWebhook"
        try:
            resp = requests.post(url, json={"url": webhook_url}, timeout=15)
            return resp.json()
        except Exception as e:
            return {"error": str(e)[:300]}

    @staticmethod
    def delete_webhook(bot_token):
        """Remove the webhook for a Telegram bot."""
        url = f"{TELEGRAM_API_BASE.format(token=bot_token)}/deleteWebhook"
        try:
            resp = requests.post(url, timeout=15)
            return resp.json()
        except Exception as e:
            return {"error": str(e)[:300]}

    @staticmethod
    def get_bot_info(bot_token):
        """Get bot username and info."""
        url = f"{TELEGRAM_API_BASE.format(token=bot_token)}/getMe"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if data.get("ok"):
                return data["result"]
            return {"error": data.get("description", "Failed")}
        except Exception as e:
            return {"error": str(e)[:300]}

    @staticmethod
    def parse_update(update):
        """
        Parse an incoming Telegram update.
        Returns: {chat_id, from_id, from_name, text, message_id, type}
        """
        result = {
            "chat_id": None,
            "from_id": None,
            "from_name": "",
            "text": "",
            "message_id": None,
            "type": "message",
        }

        # Regular message
        msg = update.get("message") or update.get("edited_message")
        if msg:
            result["chat_id"] = msg["chat"]["id"]
            result["from_id"] = msg.get("from", {}).get("id")
            first = msg.get("from", {}).get("first_name", "")
            last = msg.get("from", {}).get("last_name", "")
            result["from_name"] = f"{first} {last}".strip()
            result["text"] = msg.get("text", "")
            result["message_id"] = msg.get("message_id")
            result["type"] = "message"
            return result

        # Callback query (button press)
        callback = update.get("callback_query")
        if callback:
            result["chat_id"] = callback["message"]["chat"]["id"]
            result["from_id"] = callback["from"]["id"]
            first = callback["from"].get("first_name", "")
            last = callback["from"].get("last_name", "")
            result["from_name"] = f"{first} {last}".strip()
            result["text"] = callback.get("data", "")
            result["message_id"] = callback["message"].get("message_id")
            result["type"] = "callback"
            return result

        return result
