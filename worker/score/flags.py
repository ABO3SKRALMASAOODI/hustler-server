"""The switch: app_kv.director, read per job (Director DESIGN 8, BUILD R1).

Off by default. A bad row means off. mode "on" opens the film to new trial accounts, or to a percent
of them; test_user_ids opens it to named trial accounts while everyone else stays on the chain.
Nothing here can admit a subscriber: the seam only asks about a job that holds a live trial scope.
"""
import hashlib
import json

KEY = "director"
MAX_SOURCE_S = 180.0
# Seconds from the First cut job's start. landed-to-first-result stays within 1.3 x 253 = 329 s:
# analysis of a clip under 3 minutes is 54-70 s, the render about 65-110 s. See director/first-result.
DEADLINE_S = 150.0
SCOUT_S, SUPPLY_S = 16.0, 26.0
FILM_USD = 0.70            # what one film may spend: the dearest slice run was $0.56, and a Look and a Scout are under a cent
DEFAULTS = {"mode": "off", "percent": 100, "test_user_ids": [], "deadline_s": DEADLINE_S, "max_source_s": MAX_SOURCE_S, "film_usd": FILM_USD,
            "models": {"scout": "claude-haiku-5-5", "direct": "claude-opus-5-5"}, "effort": {"direct": "medium"}}


def parse(raw):
    """app_kv text to a normalised dict. Anything unparsable is off."""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    if data.get("mode") in ("off", "on"):
        out["mode"] = data["mode"]
    else:
        return dict(DEFAULTS)
    try:
        out["percent"] = max(0, min(100, int(data.get("percent", 100))))
        out["test_user_ids"] = [int(v) for v in data.get("test_user_ids") or []]
        out["deadline_s"] = max(30.0, min(float(data.get("deadline_s", DEADLINE_S)), 240.0))
        out["max_source_s"] = max(1.0, min(float(data.get("max_source_s", MAX_SOURCE_S)), MAX_SOURCE_S))
        out["film_usd"] = max(0.1, min(float(data.get("film_usd", FILM_USD)), 2.0))
    except (TypeError, ValueError):
        return dict(DEFAULTS)
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    out["models"] = {k: str(models.get(k) or v) for k, v in DEFAULTS["models"].items()}
    if not all(m.startswith("claude-") for m in out["models"].values()):
        return dict(DEFAULTS)       # OpenAI is no customer rung until the Gauntlet clears it
    effort = data.get("effort") if isinstance(data.get("effort"), dict) else {}
    out["effort"] = {"direct": str(effort.get("direct") or "medium")}
    return out


def read_row(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (KEY,))
        row = cur.fetchone()
    if not row:
        return None
    return row.get("value") if isinstance(row, dict) else row[0]


def read(worker_db):
    """Per job, never raises. No row, no table, no database: off."""
    try:
        return parse(worker_db.run(read_row))
    except Exception:  # noqa: BLE001 - a bad row means off
        return dict(DEFAULTS)


def _bucket(user_id):
    return int(hashlib.sha256(f"director:{user_id}".encode()).hexdigest()[:8], 16) % 100


def admit(cfg, user_id, *, trial_active, subscribed, engine_on, source_s, url=None):
    """(True, "") when this job may try a film, else (False, why). A trial scope is the account rule
    (never paid, admitted project); subscribed is checked again so no path can reach a payer."""
    if subscribed:
        return False, "subscribed"
    if url:
        return False, "url"
    if not trial_active:
        return False, "not_trial"
    if not engine_on:
        return False, "account_rule"
    named = user_id in cfg["test_user_ids"]
    if cfg["mode"] != "on" and not named:
        return False, "off"
    if not source_s or float(source_s) > cfg["max_source_s"]:
        return False, "source_length"
    if not named and _bucket(user_id) >= cfg["percent"]:
        return False, "percent"
    return True, ""
