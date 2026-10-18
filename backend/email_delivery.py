"""Bounded verification windows anchored to authenticated provider delivery events."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import threading
import time

import requests
from flask import current_app

CODE_SECONDS = 600
MAX_DELIVERY_SECONDS = 7200
_cache = OrderedDict()
_lock = threading.Lock()


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def delivery_tag(reference):
    # Provider tags never contain a login challenge, email address or code.
    digest = hmac.new(str(current_app.config["SECRET_KEY"]).encode(),
                      ("email-delivery:" + reference).encode(), hashlib.sha256).hexdigest()
    return "verify-" + digest


def provider_delivery(tag, email, created_at):
    """Only the exact tagged recipient's first delivery can extend a code.

    Cache briefly to bound polling; never use opens (including Apple proxies)
    as evidence of delivery. No provider payload or recipient is logged.
    """
    key = (tag, email.lower(), str(created_at))
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < 15:
            return hit[1]
    result = {"state": "unknown"}
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        return result
    try:
        response = requests.get("https://api.brevo.com/v3/smtp/statistics/events",
                                headers={"api-key": api_key, "accept": "application/json"},
                                params={"tags": tag, "email": email, "days": 3, "limit": 100},
                                timeout=(2, 4))
        if response.status_code == 200:
            events = []
            for event in response.json().get("events", []):
                tags = event.get("tag") or ""
                tags = tags.split(",") if isinstance(tags, str) else tags
                if tag not in tags or str(event.get("email", "")).lower() != email.lower():
                    continue
                at = utc(event["date"])
                if utc(created_at) <= at <= datetime.now(timezone.utc) + timedelta(seconds=5):
                    events.append((event.get("event"), at))
            delivered = [at for kind, at in events if kind == "delivered"]
            if delivered:
                result = {"state": "delivered", "delivered_at": min(delivered)}
            elif any(kind in ("hardBounces", "blocked", "invalid", "error", "softBounces") for kind, _ in events):
                result = {"state": "undeliverable"}
            else:
                result = {"state": "pending"}
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        pass
    with _lock:
        _cache[key] = (time.monotonic(), result)
        _cache.move_to_end(key)
        while len(_cache) > 256:
            _cache.popitem(last=False)
    return result


def verification_window(reference, email, created_at, expires_at, now=None):
    """A delayed email gets a short window after delivery, never unlimited life.

    Normal codes still work during their initial window if reporting is slow.
    After that, extension requires an exact provider delivery event within two
    hours of the request. Unknown provider state remains retryable, not expired.
    """
    now = utc(now or datetime.now(timezone.utc))
    created_at, expires_at = utc(created_at), utc(expires_at)
    duration = expires_at - created_at
    hard_end = created_at + timedelta(seconds=MAX_DELIVERY_SECONDS) + duration
    if now >= hard_end:
        return {"state": "expired", "valid": False, "retryable": False, "expires_in": 0}
    delivery = provider_delivery(delivery_tag(reference), email, created_at)
    if delivery["state"] == "delivered":
        arrived = delivery["delivered_at"]
        # A resend or late repeated provider event cannot renew the window.
        if arrived > created_at + timedelta(seconds=MAX_DELIVERY_SECONDS):
            return {"state": "expired", "valid": False, "retryable": False, "expires_in": 0}
        deadline = max(expires_at, arrived + duration)
        seconds = max(0, int((deadline - now).total_seconds()))
        return {"state": "delivered" if seconds else "expired", "valid": seconds > 0,
                "retryable": False, "expires_in": seconds, "valid_until": deadline}
    valid = now < expires_at
    return {"state": delivery["state"], "valid": valid,
            "retryable": not valid and delivery["state"] in ("pending", "unknown"),
            "expires_in": max(0, int((expires_at - now).total_seconds())), "valid_until": expires_at}
