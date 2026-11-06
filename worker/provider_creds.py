"""The model providers' keys, sealed in one app_kv row (6 Nov 2026).

THIS FILE EXISTS TWICE, byte for byte: backend/provider_creds.py and
worker/provider_creds.py (separate images). The tests fail when they differ.

On 4 Nov the OpenAI wallet behind the keys in Render's environment and the
box's executor.env ran dry, and every concierge reply became one canned line
to the customer. A funded key exists, and nobody should have to type it into
two dashboards and a GitHub secret to deliver it. So it travels the way the
next store's keys do (storage_creds.py, K1): one app_kv row, sealed under the
secrets every process already holds and a plain database reader does not.

* THE ROW OUTRANKS THE ENVIRONMENT. That is the difference from
  storage_creds: there a process's env wins; here the env holds the dead
  value, so a process that can open the row uses the row's value for that
  name. No row, or one it cannot open: the env, exactly as before.
* ``key(name)`` is the one read. Worker: config.OPENAI_API_KEY and
  config.VISION_API_KEY call it (config.__getattr__), so every existing
  reader follows the row. Backend: the concierge and checkout readiness.
* Same cache as storage_creds.Credentials: five minutes, a failed read keeps
  the last good keys, later refreshes run in the background, and the first
  read holds a caller at most one second.
* Delivering a key moves no customer's edit: which model edits is decided by
  llm.editing_lanes, not by which keys are live.
* Nothing here raises into a call, and no key value is ever logged or
  reported: status() carries reason words and names only.

Row: storage_creds' format under its own HKDF info, so a row of one kind can
never open as the other. Written by
.valmera/week-1106/reliability/put_provider_keys.py.
"""

import os

import storage_creds as sealed

KV_KEY = "provider_api_keys"
SPEC = sealed.Spec("valmera-provider-keys-v1",
                   ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
                   ("OPENAI_API_KEY",))


class Keys(sealed.Credentials):
    """storage_creds.Credentials for this row: always wanted, because the
    row outranks whatever the environment says."""

    def _needed(self):
        return self._fetch is not None and self._has_sealing_secret()


CREDENTIALS = Keys(spec=SPEC, what="provider",
                   log=lambda msg: print(f"[providers] {msg}", flush=True))


def install(fetch):
    CREDENTIALS.install(fetch)


def _row():
    try:
        return CREDENTIALS.values() or {}
    except Exception:  # noqa: BLE001 — never into a model call
        return {}


def key(name, env=None):
    """The value to use for ``name``: the row's, else the environment's."""
    return str(_row().get(name) or (os.environ if env is None else env)
               .get(name) or "").strip()


def status():
    """Where this process's provider keys come from: names and reason words
    only, for health routes."""
    try:
        row = _row()
        out = {name.split("_")[0].lower(): ("app_kv" if row.get(name) else
                                            "env" if os.environ.get(name)
                                            else None)
               for name in SPEC.fields}
        out["credentials"] = CREDENTIALS.status()
        return out
    except Exception as exc:  # noqa: BLE001
        return {"credentials": {"row": "error",
                                "error": type(exc).__name__}}
