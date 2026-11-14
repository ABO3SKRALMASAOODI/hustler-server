"""The switch: app_kv.director, read per job (Director DESIGN 8, BUILD R1).

Off by default. A bad row means off. mode "on" opens the film to new trial accounts, or to a percent
of them; test_user_ids opens it to named trial accounts while everyone else stays on the chain.
A subscriber is admitted only as `payer_first` (round six, 14 Nov): the automatic First cut of a paying account's
project, which runs on today's chain while the owner's trial is off. `payers: false` in the row puts those films back
on the chain; nothing about a trial account changes with it.

engine (lane D, 12 Nov): which renderer draws a Director film whose scene plan was kept.
"off" (default, and what any bad value reads as): today's renderer only. "shadow": today's renderer
delivers; the engine draws the same plan afterwards on the side, never shown, for timings and faults.
"on": the engine draws, today's renderer is the fallback in the same job. engine_user_ids opens "on"
to named accounts while the rest stay on the row's engine mode.
"""
import hashlib
import json

KEY = "director"
MAX_SOURCE_S = 180.0
# Seconds from the First cut job's start. landed-to-first-result stays within 1.3 x 253 = 329 s:
# analysis of a clip under 3 minutes is 54-70 s, the render about 65-110 s. See director/first-result.
DEADLINE_S = 150.0
# The Scout: 22 s lost a film its whole Bin (p5565, 13 Nov: a video call with a slide of text; Haiku had written nothing
# by 22 s, the film went out with no wish, no picture and no hook). It writes up to 14 wishes now and reads a board;
# 9 to 16 s is its usual time, and a late Scout costs the film everything Supply would have found.
SCOUT_S, SUPPLY_S = 32.0, 44.0     # supply: the look every Bin picture passes (lane B, 12 Nov), the fetch (13 Nov), and since round five up to 14 pictures a film
FILM_USD = 0.70            # what one film may spend: the dearest slice run was $0.56, and a Look and a Scout are under a cent
DEFAULTS = {"mode": "off", "percent": 100, "test_user_ids": [], "deadline_s": DEADLINE_S, "max_source_s": MAX_SOURCE_S, "film_usd": FILM_USD,
            "models": {"scout": "claude-haiku-5-5", "direct": "claude-opus-5-5"}, "effort": {"direct": "low"},
            "engine": "off", "engine_user_ids": [], "payers": True}
ENGINE_MODES = ("off", "shadow", "on")
# OpenAI models a Director row may name for its test_user_ids only (lane Q, 13 Nov; prices in model_prices).
OPENAI_MODELS = ("gpt-5.5", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-luna")
# Effort, measured on copies through this path (director/first-result): at medium the slice's Scores ended
# 68-148 s after the call and a p5302 replay was still writing at 131 s, past the deadline, so about half
# the films would fall to the chain after the wait. At low, Direct took 19-31 s and its repair 12-22 s on
# all three sessions. Which effort scores higher is the Gauntlet's question: the row's effort.direct sets it.


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
    if not all(m.startswith("claude-") or m in OPENAI_MODELS for m in out["models"].values()):
        return dict(DEFAULTS)
    if not all(m.startswith("claude-") for m in out["models"].values()):
        # OpenAI is no customer rung until the Gauntlet clears it: a row naming an OpenAI model admits its
        # test_user_ids only (mode off, no percent), whatever its mode says.
        out["mode"], out["percent"] = "off", 0
    effort = data.get("effort") if isinstance(data.get("effort"), dict) else {}
    e = str(effort.get("direct") or DEFAULTS["effort"]["direct"])
    out["effort"] = {"direct": e if e in ("low", "medium", "high", "xhigh", "max") else DEFAULTS["effort"]["direct"]}
    out.update(_engine(data))
    out["payers"] = data.get("payers") is not False
    return out


def _engine(data):
    """The engine switch reads on its own: a bad Director field never turns it on, a bad engine field is off."""
    mode = data.get("engine")
    try:
        ids = [int(v) for v in data.get("engine_user_ids") or []]
    except (TypeError, ValueError):
        ids = []
    return {"engine": mode if mode in ENGINE_MODES else "off", "engine_user_ids": ids}


def engine_mode(cfg, user_id=None):
    """off | shadow | on for this job's account."""
    if user_id is not None and int(user_id) in (cfg.get("engine_user_ids") or []):
        return "on"
    mode = cfg.get("engine")
    return mode if mode in ENGINE_MODES else "off"


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


def admit(cfg, user_id, *, trial_active, subscribed, engine_on, source_s, url=None, payer_first=False):
    """(True, "") when this job may try a film, else (False, why). A trial scope is the account rule
    (never paid, admitted project). A subscriber is reached by one door only: payer_first, the automatic
    First cut of a paying account's project (the caller says so), while the row is on for everyone and
    does not say `payers: false`. The percent is the trial's and is not read for it."""
    if subscribed:
        if not payer_first or not cfg.get("payers", True) or url:
            return False, "subscribed"
        if cfg["mode"] != "on" and user_id not in cfg["test_user_ids"]:
            return False, "off"
        if not source_s or float(source_s) > cfg["max_source_s"]:
            return False, "source_length"
        return True, ""
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
