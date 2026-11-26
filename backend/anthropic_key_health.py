"""Monitor the credential actually used by editing, independently of model routing."""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import requests

import anthropic_wallet
import claude_credit
import provider_creds

log = logging.getLogger(__name__)

# A probe with no verdict (an overload, a rate limit, a 5xx, the network) is asked again this soon,
# never a day later, and never changes what is known about the key.
RETRY_AFTER = timedelta(minutes=10)
_BILLING_WORDS = ("credit balance", "billing")
_unread_logged = set()


class ExpiryUnknown(Exception):
    """The key's expiry could not be read: the advance warning cannot fire."""


def _expiry(key):
    """The live key's expires_at (None: it never expires). Raises ExpiryUnknown when the admin
    metadata cannot be read, so the gap is visible instead of silent."""
    admin = os.environ.get("ANTHROPIC_ADMIN_API_KEY")
    key_id = os.environ.get("ANTHROPIC_API_KEY_ID")
    if not (admin and key_id):
        raise ExpiryUnknown("ANTHROPIC_ADMIN_API_KEY and ANTHROPIC_API_KEY_ID are not both set")
    response = requests.get(
        f"https://api.anthropic.com/v1/organizations/api_keys/{key_id}",
        headers={"x-api-key": admin, "anthropic-version": "2023-06-01"},
        timeout=(3, 10), allow_redirects=False)
    if not response.ok:
        raise ExpiryUnknown(f"the admin API answered HTTP {response.status_code}")
    data = response.json()
    prefix, _, suffix = str(data.get("partial_key_hint") or "").partition("...")
    if not (prefix and suffix and key.startswith(prefix) and key.endswith(suffix)):
        raise ExpiryUnknown("ANTHROPIC_API_KEY_ID names a different key than the one editing uses")
    return data.get("expires_at")


def _probe(key):
    """True: the key answers. False: Anthropic refused it (key, permission or billing). None: no
    verdict (an overload, a rate limit, a 5xx or the network), so the next tick asks again."""
    try:
        response = requests.post("https://api.anthropic.com/v1/messages",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                 json={"model": os.getenv("CLAUDE_CREDIT_PROBE_MODEL", "claude-haiku-5-5"),
                                       "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 8},
                                 timeout=(3, 20), allow_redirects=False)
    except requests.RequestException:
        return None
    if response.status_code == 200:
        return True
    if response.status_code in (401, 402, 403):
        return False
    if response.status_code == 400 and any(w in response.text.lower() for w in _BILLING_WORDS):
        return False
    return None


def _when(stamp):
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def check_key(key, state, now, metadata=_expiry, probe=_probe):
    fingerprint = hashlib.sha256(str(key or "").encode()).hexdigest()
    state = dict(state) if state.get("fingerprint") == fingerprint else {"fingerprint": fingerprint}
    checked, retry = state.get("checked_at"), state.get("retry_at")
    if (not checked or now - _when(checked) >= timedelta(days=1)) and (not retry or now >= _when(retry)):
        try:
            state["expires_at"] = metadata(key)
            state.pop("expiry_unknown", None)
        except (ExpiryUnknown, requests.RequestException, ValueError) as exc:
            reason = str(exc)[:200] or type(exc).__name__
            state["expiry_unknown"] = reason
            if reason not in _unread_logged:
                _unread_logged.add(reason)
                log.warning("Anthropic key expiry is not monitored: %s", reason)
        expiry = state.get("expires_at")
        # The live call always runs: known expiry metadata says nothing about a revoked key or an
        # empty balance. A key past its expiry has failed whatever the probe said.
        ok = probe(key) if key else False
        if expiry and _when(expiry) <= now:
            ok = False
        if ok is None:
            state["retry_at"] = (now + RETRY_AFTER).isoformat()
        else:
            failed_before = state.get("ok") is False
            state.update(checked_at=now.isoformat(), ok=ok)
            state.pop("retry_at", None)
            if not ok and not failed_before:
                return state, ("Anthropic live key probe failed", f"Probe failed at {now:%Y-%m-%d %H:%M UTC}. "
                               "Check billing and rotate the key in the console and in provider_api_keys.")
    expiry = state.get("expires_at")
    if expiry and expiry != state.get("warned_expiry") and _when(expiry) - now <= timedelta(days=3):
        state["warned_expiry"] = expiry
        return state, ("Anthropic key expiry warning", f"The key expires at {expiry}. "
                       "Rotate it in the console and in provider_api_keys.")
    return state, None


def tick():
    from claude_trial import _connect
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('anthropic_key_health')) AS locked")
            if not cur.fetchone()["locked"]:
                return
            previous = anthropic_wallet._read(cur, "anthropic_key_health")
            now = datetime.now(timezone.utc)
            state, alert = check_key(provider_creds.key("ANTHROPIC_API_KEY"), previous, now)
            if state != previous:
                anthropic_wallet._put(cur, "anthropic_key_health", state)
            if alert:
                claude_credit.queue_alert(cur, f"key_health:{state['fingerprint'][:16]}:{now:%Y%m%dT%H%M%S}",
                                          "anthropic_key_health", alert[0], alert[1])
        conn.commit()
    finally:
        conn.close()
