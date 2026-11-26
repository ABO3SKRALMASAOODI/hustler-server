"""Monitor the credential actually used by editing, independently of model routing."""
import hashlib
import os
from datetime import datetime, timedelta, timezone

import requests

import anthropic_wallet
import claude_credit
import provider_creds


def _expiry(key):
    admin = os.environ.get("ANTHROPIC_ADMIN_API_KEY")
    key_id = os.environ.get("ANTHROPIC_API_KEY_ID")
    if admin and key_id:
        response = requests.get(
            f"https://api.anthropic.com/v1/organizations/api_keys/{key_id}",
            headers={"x-api-key": admin, "anthropic-version": "2023-06-01"},
            timeout=(3, 10), allow_redirects=False)
        if response.ok:
            data = response.json()
            prefix, _, suffix = str(data.get("partial_key_hint") or "").partition("...")
            if prefix and suffix and key.startswith(prefix) and key.endswith(suffix):
                return data.get("expires_at")
    return None


def _probe(key):
    response = requests.post("https://api.anthropic.com/v1/messages",
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                             json={"model": os.getenv("CLAUDE_CREDIT_PROBE_MODEL", "claude-haiku-5-5"),
                                   "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 8},
                             timeout=(3, 20), allow_redirects=False)
    return response.status_code == 200


def check_key(key, state, now, metadata=_expiry, probe=_probe):
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    state = dict(state) if state.get("fingerprint") == fingerprint else {"fingerprint": fingerprint}
    checked = state.get("checked_at")
    if not checked or now - datetime.fromisoformat(checked) >= timedelta(days=1):
        try:
            expiry = metadata(key)
        except (requests.RequestException, ValueError):
            expiry = None
        try:
            ok = bool(expiry or (key and probe(key)))
        except requests.RequestException:
            ok = False
        failed_before = state.get("ok") is False
        state.update(checked_at=now.isoformat(), expires_at=expiry, ok=ok)
        if not ok and not failed_before:
            return state, ("Anthropic live key probe failed", f"Probe failed at {now:%Y-%m-%d %H:%M UTC}. "
                           "Check billing and rotate the key in the console and in provider_api_keys.")
    expiry = state.get("expires_at")
    if expiry and expiry != state.get("warned_expiry") and (
            datetime.fromisoformat(expiry.replace("Z", "+00:00")) - now <= timedelta(days=3)):
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
            state, alert = check_key(provider_creds.key("ANTHROPIC_API_KEY"), previous,
                                     datetime.now(timezone.utc))
            if state != previous:
                anthropic_wallet._put(cur, "anthropic_key_health", state)
            if alert:
                claude_credit.queue_alert(cur, f"key_health:{state['fingerprint']}:{state['checked_at']}",
                                          "anthropic_key_health", alert[0], alert[1])
        conn.commit()
    finally:
        conn.close()
