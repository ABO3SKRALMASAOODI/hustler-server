"""
Gmail skill — read and send emails via Gmail API.

The agent can check inbox, search emails, read specific messages,
and compose/send new emails. Uses OAuth2 tokens.
"""

import json
import os
import base64
import requests
from email.mime.text import MIMEText
from engine.super_agent.skills.base_skill import BaseSkill


class GmailSkill(BaseSkill):
    SKILL_TYPE = "gmail"
    DISPLAY_NAME = "Gmail"
    DESCRIPTION = "Read inbox, search emails, and send messages via Gmail. Your agent becomes your email assistant."
    CATEGORY = "productivity"
    CONFIG_SCHEMA = {
        "access_token": {"type": "string", "description": "Google OAuth2 access token"},
        "refresh_token": {"type": "string", "description": "Google OAuth2 refresh token"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "check_inbox",
                "description": (
                    "Check recent emails in the Gmail inbox. "
                    "Returns subject, sender, snippet, and date for recent messages. "
                    "Use this to give the user a summary of their inbox."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "max_results": {"type": "integer", "description": "Number of emails to fetch (1-20, default 10)"},
                        "query": {"type": "string", "description": "Optional Gmail search query (e.g., 'is:unread', 'from:boss@company.com', 'subject:invoice')"},
                    },
                },
            },
            {
                "name": "read_email",
                "description": (
                    "Read the full content of a specific email by its message ID. "
                    "Use this after check_inbox to read important messages."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID (from check_inbox results)"},
                    },
                    "required": ["message_id"],
                },
            },
            {
                "name": "send_gmail",
                "description": (
                    "Compose and send an email via Gmail. "
                    "Always confirm with the user before sending."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body (plain text)"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        access_token = config.get("access_token", "")
        refresh_token = config.get("refresh_token", "")

        def _get_token():
            nonlocal access_token
            if access_token:
                return access_token
            if refresh_token:
                client_id = os.getenv("GOOGLE_CLIENT_ID", "")
                client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
                if client_id and client_secret:
                    resp = requests.post("https://oauth2.googleapis.com/token", data={
                        "client_id": client_id, "client_secret": client_secret,
                        "refresh_token": refresh_token, "grant_type": "refresh_token",
                    }, timeout=10)
                    if resp.status_code == 200:
                        access_token = resp.json().get("access_token", "")
                        return access_token
            return None

        def _headers():
            token = _get_token()
            if not token:
                return None
            return {"Authorization": f"Bearer {token}"}

        def check_inbox(max_results=10, query=""):
            headers = _headers()
            if not headers:
                return "ERROR: Gmail not authenticated. Configure access_token or refresh_token."

            max_results = min(max(max_results, 1), 20)
            params = {"maxResults": max_results}
            if query:
                params["q"] = query

            try:
                resp = requests.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    headers=headers, params=params, timeout=15,
                )
                if resp.status_code != 200:
                    return f"ERROR: Gmail API returned {resp.status_code}"

                msg_list = resp.json().get("messages", [])
                if not msg_list:
                    return "No emails found."

                lines = ["**Recent Emails:**\n"]
                for msg_ref in msg_list[:max_results]:
                    msg_resp = requests.get(
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                        headers=headers, params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                        timeout=10,
                    )
                    if msg_resp.status_code != 200:
                        continue

                    msg_data = msg_resp.json()
                    headers_list = msg_data.get("payload", {}).get("headers", [])
                    subject = next((h["value"] for h in headers_list if h["name"] == "Subject"), "No subject")
                    sender = next((h["value"] for h in headers_list if h["name"] == "From"), "Unknown")
                    date = next((h["value"] for h in headers_list if h["name"] == "Date"), "")
                    snippet = msg_data.get("snippet", "")[:100]

                    lines.append(f"- **{subject}**")
                    lines.append(f"  From: {sender}")
                    lines.append(f"  {snippet}")
                    lines.append(f"  ID: `{msg_ref['id']}`\n")

                return "\n".join(lines)
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def read_email(message_id):
            headers = _headers()
            if not headers:
                return "ERROR: Gmail not authenticated."

            try:
                resp = requests.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    headers=headers, params={"format": "full"}, timeout=15,
                )
                if resp.status_code != 200:
                    return f"ERROR: {resp.status_code}"

                msg = resp.json()
                headers_list = msg.get("payload", {}).get("headers", [])
                subject = next((h["value"] for h in headers_list if h["name"] == "Subject"), "No subject")
                sender = next((h["value"] for h in headers_list if h["name"] == "From"), "Unknown")
                date = next((h["value"] for h in headers_list if h["name"] == "Date"), "")

                # Extract body
                body = ""
                payload = msg.get("payload", {})
                if payload.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
                else:
                    for part in payload.get("parts", []):
                        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                            body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                            break

                if len(body) > 5000:
                    body = body[:5000] + "\n... [truncated]"

                return f"**Subject:** {subject}\n**From:** {sender}\n**Date:** {date}\n\n{body}"
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def send_gmail(to, subject, body):
            headers = _headers()
            if not headers:
                return "ERROR: Gmail not authenticated."

            try:
                message = MIMEText(body)
                message["to"] = to
                message["subject"] = subject
                raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

                resp = requests.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"raw": raw},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    return f"Email sent to {to}: '{subject}'"
                return f"ERROR: {resp.status_code}: {resp.text[:300]}"
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        return {
            "check_inbox": check_inbox,
            "read_email": read_email,
            "send_gmail": send_gmail,
        }
