"""Owner-controlled economy mode for the optional trial planning pass.

Use the worker's existing planner_model override, so this also controls
already-deployed executors without restarting customer jobs. Paid routing,
trial admission, credit limits and normal edit verification are independent.
"""
import json
import os
from datetime import datetime, timezone

import free_trial
import trial_toggle

DEFAULT_MODEL = os.getenv("CLAUDE_TRIAL_PLANNER_MODEL", "claude-opus-5-5").strip()


def _model(value):
    name = str(value or "").strip()
    return name if name.lower().startswith("claude-") else None


def status(conn):
    with conn.cursor() as cur:
        stored, _ = trial_toggle._stored_flag(cur)
    model = _model(stored.get("planner_model", DEFAULT_MODEL))
    return {
        "owner_on": bool(model), "state": "on" if model else "off",
        "mode": "quality" if model else "economy",
        "model": model,
        "restore_model": _model(stored.get("supervision_restore_model"))
            or _model(DEFAULT_MODEL) or "claude-opus-5-5",
        "scope": "trial_planning", "takes_effect_s": 30,
        "changed_at": stored.get("supervision_changed_at"),
    }


def switch(conn, on, actor="admin", now=None):
    if type(on) is not bool:
        raise ValueError("on must be a boolean")
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # Same lock as the trial switch and pacing: preserve concurrent caps.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('free_result_switch'))")
        stored, _ = trial_toggle._stored_flag(cur)
        value = dict(stored)
        previous = _model(stored.get("planner_model", DEFAULT_MODEL))
        restore = (previous or _model(stored.get("supervision_restore_model"))
                   or _model(DEFAULT_MODEL) or "claude-opus-5-5")
        value.update(planner_model=restore if on else "off",
                     supervision_restore_model=restore,
                     supervision_mode="quality" if on else "economy",
                     supervision_changed_at=now.isoformat(),
                     supervision_changed_by=str(actor)[:80])
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (free_trial.KEY, json.dumps(value, sort_keys=True)))
    return value
