"""
Google Calendar skill — read and create calendar events.

Uses Google Calendar API with OAuth2 service account or user tokens.
Config stores the OAuth refresh token; we exchange it for access tokens.
"""

import json
import os
import requests
from datetime import datetime, timedelta
from engine.super_agent.skills.base_skill import BaseSkill


class GoogleCalendarSkill(BaseSkill):
    SKILL_TYPE = "google_calendar"
    DISPLAY_NAME = "Google Calendar"
    DESCRIPTION = "Read upcoming events, create new events, and manage your Google Calendar. The ultimate scheduling companion."
    CATEGORY = "productivity"
    CONFIG_SCHEMA = {
        "access_token": {"type": "string", "description": "Google OAuth2 access token"},
        "refresh_token": {"type": "string", "description": "Google OAuth2 refresh token"},
        "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)", "default": "primary"},
    }

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "list_calendar_events",
                "description": (
                    "List upcoming events from Google Calendar. "
                    "Returns events for the next N days (default 7). "
                    "Great for getting an overview of the user's schedule."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {"type": "integer", "description": "Number of days ahead to look (1-30, default 7)"},
                        "max_results": {"type": "integer", "description": "Max events to return (default 20)"},
                    },
                },
            },
            {
                "name": "create_calendar_event",
                "description": (
                    "Create a new event in Google Calendar. "
                    "Specify the title, start/end times (ISO 8601), and optional description/location."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Event title"},
                        "start": {"type": "string", "description": "Start time in ISO 8601 (e.g., 2026-04-10T09:00:00)"},
                        "end": {"type": "string", "description": "End time in ISO 8601 (e.g., 2026-04-10T10:00:00)"},
                        "description": {"type": "string", "description": "Optional event description"},
                        "location": {"type": "string", "description": "Optional location"},
                        "timezone": {"type": "string", "description": "Timezone (e.g., America/New_York)", "default": "UTC"},
                    },
                    "required": ["title", "start", "end"],
                },
            },
            {
                "name": "find_free_slots",
                "description": (
                    "Find free time slots in the calendar for the next N days. "
                    "Returns available blocks between working hours (9am-6pm by default). "
                    "Perfect for scheduling meetings or finding time for tasks."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {"type": "integer", "description": "Number of days to check (1-14, default 3)"},
                        "min_duration_minutes": {"type": "integer", "description": "Minimum slot duration in minutes (default 30)"},
                        "work_start_hour": {"type": "integer", "description": "Work day start hour (0-23, default 9)"},
                        "work_end_hour": {"type": "integer", "description": "Work day end hour (0-23, default 18)"},
                    },
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):
        access_token = config.get("access_token", "")
        refresh_token = config.get("refresh_token", "")
        calendar_id = config.get("calendar_id", "primary")

        def _get_token():
            nonlocal access_token
            if access_token:
                return access_token
            if refresh_token:
                client_id = os.getenv("GOOGLE_CLIENT_ID", "")
                client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
                if client_id and client_secret:
                    resp = requests.post("https://oauth2.googleapis.com/token", data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    }, timeout=10)
                    if resp.status_code == 200:
                        access_token = resp.json().get("access_token", "")
                        return access_token
            return None

        def _headers():
            token = _get_token()
            if not token:
                return None
            return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        def list_calendar_events(days_ahead=7, max_results=20):
            headers = _headers()
            if not headers:
                return "ERROR: Google Calendar not authenticated. Configure access_token or refresh_token in skill config."

            days_ahead = min(max(days_ahead, 1), 30)
            now = datetime.utcnow()
            time_min = now.isoformat() + "Z"
            time_max = (now + timedelta(days=days_ahead)).isoformat() + "Z"

            try:
                resp = requests.get(
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers=headers,
                    params={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "maxResults": min(max_results, 50),
                        "singleEvents": True,
                        "orderBy": "startTime",
                    },
                    timeout=15,
                )

                if resp.status_code != 200:
                    return f"ERROR: Google Calendar API returned {resp.status_code}: {resp.text[:300]}"

                events = resp.json().get("items", [])
                if not events:
                    return f"No events found in the next {days_ahead} days."

                lines = [f"**Upcoming Events (next {days_ahead} days):**\n"]
                for e in events:
                    start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
                    end = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date", "")
                    title = e.get("summary", "No title")
                    location = e.get("location", "")

                    # Format nicely
                    if "T" in start:
                        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        start_str = dt.strftime("%a %b %d, %I:%M %p")
                    else:
                        start_str = start

                    line = f"- **{title}** — {start_str}"
                    if location:
                        line += f" @ {location}"
                    lines.append(line)

                return "\n".join(lines)
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def create_calendar_event(title, start, end, description="", location="", timezone="UTC"):
            headers = _headers()
            if not headers:
                return "ERROR: Google Calendar not authenticated."

            event = {
                "summary": title,
                "start": {"dateTime": start, "timeZone": timezone},
                "end": {"dateTime": end, "timeZone": timezone},
            }
            if description:
                event["description"] = description
            if location:
                event["location"] = location

            try:
                resp = requests.post(
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers=headers,
                    json=event,
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    created = resp.json()
                    return f"Event created: **{title}** on {start}. Link: {created.get('htmlLink', '')}"
                return f"ERROR: {resp.status_code}: {resp.text[:300]}"
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        def find_free_slots(days_ahead=3, min_duration_minutes=30, work_start_hour=9, work_end_hour=18):
            headers = _headers()
            if not headers:
                return "ERROR: Google Calendar not authenticated."

            days_ahead = min(max(days_ahead, 1), 14)
            now = datetime.utcnow()
            time_min = now.isoformat() + "Z"
            time_max = (now + timedelta(days=days_ahead)).isoformat() + "Z"

            try:
                resp = requests.get(
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers=headers,
                    params={
                        "timeMin": time_min, "timeMax": time_max,
                        "singleEvents": True, "orderBy": "startTime", "maxResults": 100,
                    },
                    timeout=15,
                )

                if resp.status_code != 200:
                    return f"ERROR: {resp.status_code}"

                events = resp.json().get("items", [])

                # Build busy periods
                busy = []
                for e in events:
                    s = e.get("start", {}).get("dateTime")
                    en = e.get("end", {}).get("dateTime")
                    if s and en:
                        busy.append((
                            datetime.fromisoformat(s.replace("Z", "+00:00")),
                            datetime.fromisoformat(en.replace("Z", "+00:00")),
                        ))

                # Find free slots day by day
                lines = ["**Free Time Slots:**\n"]
                for day_offset in range(days_ahead):
                    day = now.date() + timedelta(days=day_offset)
                    work_start = datetime(day.year, day.month, day.day, work_start_hour)
                    work_end = datetime(day.year, day.month, day.day, work_end_hour)

                    if work_start < now.replace(tzinfo=None):
                        work_start = now.replace(tzinfo=None, second=0, microsecond=0)

                    # Find gaps in this day
                    day_busy = sorted([
                        (max(s.replace(tzinfo=None), work_start), min(e.replace(tzinfo=None), work_end))
                        for s, e in busy
                        if s.date() == day or e.date() == day
                    ])

                    cursor = work_start
                    day_slots = []
                    for bs, be in day_busy:
                        if bs > cursor and (bs - cursor).total_seconds() >= min_duration_minutes * 60:
                            day_slots.append(f"  {cursor.strftime('%I:%M %p')} - {bs.strftime('%I:%M %p')}")
                        cursor = max(cursor, be)

                    if cursor < work_end and (work_end - cursor).total_seconds() >= min_duration_minutes * 60:
                        day_slots.append(f"  {cursor.strftime('%I:%M %p')} - {work_end.strftime('%I:%M %p')}")

                    if day_slots:
                        lines.append(f"**{day.strftime('%A, %b %d')}:**")
                        lines.extend(day_slots)

                return "\n".join(lines) if len(lines) > 1 else "No free slots found in the specified range."
            except Exception as e:
                return f"ERROR: {str(e)[:300]}"

        return {
            "list_calendar_events": list_calendar_events,
            "create_calendar_event": create_calendar_event,
            "find_free_slots": find_free_slots,
        }
