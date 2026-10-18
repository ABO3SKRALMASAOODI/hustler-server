"""Kit V4 marketing API. Account-service mail never imports this module.

No automatic retries of writes: a timed-out broadcast may already be sending.
Errors deliberately omit response bodies, API keys and subscriber addresses.
"""
import os
import time

import requests


class KitError(RuntimeError):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


class KitClient:
    def __init__(self, api_key=None, session=None):
        self.api_key = api_key or os.getenv("KIT_API_KEY", "")
        if not self.api_key:
            raise KitError("kit_not_configured")
        self.session = session or requests.Session()
        self.last_request = 0.0

    def request(self, method, path, *, params=None, data=None):
        # Personal API keys allow 120 calls per rolling minute. Leave headroom.
        delay = 0.55 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        try:
            result = self.session.request(
                method, "https://api.kit.com/v4/" + path.lstrip("/"),
                headers={"X-Kit-Api-Key": self.api_key, "Accept": "application/json"},
                params=params, json=data, timeout=(3.05, 20), allow_redirects=False,
            )
        except requests.RequestException:
            raise KitError("kit_request_failed", uncertain=method != "GET") from None
        if not 200 <= result.status_code < 300:
            raise KitError("kit_http_" + str(result.status_code),
                           uncertain=method != "GET" and result.status_code >= 500)
        if result.status_code == 204:
            return {}
        try:
            body = result.json()
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except ValueError:
            raise KitError("kit_invalid_response", uncertain=method != "GET") from None

    def pages(self, path, key, **params):
        params = {"per_page": 1000, **params}
        seen = set()
        for _ in range(200):
            result = self.request("GET", path, params=params)
            rows = result.get(key)
            pagination = result.get("pagination")
            if not isinstance(rows, list) or not isinstance(pagination, dict):
                raise KitError("kit_invalid_pagination")
            yield from rows
            if pagination.get("has_next_page") is False:
                return
            cursor = pagination.get("end_cursor")
            if not cursor or cursor in seen:
                raise KitError("kit_invalid_pagination")
            seen.add(cursor)
            params["after"] = cursor
        raise KitError("kit_pagination_limit")

    def account(self):
        return self.request("GET", "account")["account"]

    def subscribers(self):
        return list(self.pages("subscribers", "subscribers", status="all", slim="true"))

    def tag_members(self, tag_id):
        return list(self.pages(f"tags/{int(tag_id)}/subscribers", "subscribers", status="all"))

    def subscribe(self, email):
        # Kit upserts cannot reactivate cancelled/bounced/complained subscribers.
        return self.request("POST", "subscribers", data={"email_address": email, "state": "active"})["subscriber"]

    def tag(self, tag_id, subscriber_id):
        return self.request("POST", f"tags/{int(tag_id)}/subscribers/{int(subscriber_id)}", data={})

    def untag(self, tag_id, subscriber_id):
        return self.request("DELETE", f"tags/{int(tag_id)}/subscribers/{int(subscriber_id)}")

    def unsubscribe(self, subscriber_id):
        return self.request("POST", f"subscribers/{int(subscriber_id)}/unsubscribe", data={})

    def broadcasts(self):
        return list(self.pages("broadcasts", "broadcasts"))

    def broadcast(self, broadcast_id):
        return self.request("GET", f"broadcasts/{int(broadcast_id)}")["broadcast"]

    def create_broadcast(self, *, description, subject, content, preview_text,
                         tag_id, template_id, sender, send_at):
        return self.request("POST", "broadcasts", data={
            "description": description, "subject": subject, "content": content,
            "preview_text": preview_text, "email_template_id": int(template_id),
            "email_address": sender, "public": False, "send_at": send_at,
            "subscriber_filter": [{"all": [{"type": "tag", "ids": [int(tag_id)]}]}],
        })["broadcast"]


def check_free_account(account, *, expected_id, sender):
    """Fail closed on a different account, paid/trial plan or unverified sender."""
    if str(account.get("id")) != str(expected_id):
        raise KitError("kit_account_mismatch")
    plan = account.get("plan") or {}
    if account.get("plan_type") not in {"free", "newsletter"} or plan.get("on_trial") is not False:
        raise KitError("kit_free_plan_required")
    address = next((a for a in account.get("sending_addresses", [])
                    if a.get("email_address", "").lower() == sender.lower()), {})
    if not address.get("is_verified") or not address.get("is_dmarc_configured"):
        raise KitError("kit_sender_not_verified")
    return min(9999, int(plan.get("subscriber_limit") or 0))
