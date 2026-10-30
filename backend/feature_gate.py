"""Shorts and Website demos come with Pro and Max, in the Studio.

THIS FILE EXISTS TWICE, byte-identical: backend/feature_gate.py and
worker/feature_gate.py (a test asserts it). It needs only a DB-API
connection and its twins claude_plans.py and claude_credit.py.

Owner rule (30 Oct 2026, verbatim): "clicking on shorts or website demo in
trial or plus must show an upgrade card no podcasts or recordings for those
plans excluding the mcp".

WHO is gated (``reason``):
  * "trial": an account without a plan while the free trial runs for it
    (app_kv ``free_result`` switched on for it, or it is one of its
    ``test_user_ids``, and the shared Claude credit line is open). With the
    trial off or AUTO-OFF a free account meets today's subscribe wall, which
    this module never moves.
  * "plus": a NEW Plus subscriber who has the Studio through owner decision
    D1 (claude_plans.studio_included: Studio in Plus switched on, the credit
    line open, subscribed after the cutoff).
NOT gated: every MCP call on any plan (only Studio surfaces read this),
grandfathered subscribers, Pro, Max, the other paid plans, MCP Connect (its
Studio is the MCP panel already), the admin account and the trial's
excluded accounts (the owner's agent account).

WHAT is gated for them, in the Studio only: podcast/long-video -> shorts
(make_shorts, the Shorts board's Make shorts, the SHORTS mode, the short
editors) and website recordings (record_website, record_website_demo,
showcase_demo, the trial's URL First cut). Each refusal answers with the
same upgrade card (``card``: the plans that include these features, from
the server's own offer, never hardcoded) and is never charged. Their own
footage keeps the normal First cut and edits.
"""

import json
import re
from datetime import datetime, timezone

MESSAGE_KIND = "feature_upgrade"
CODE = "feature_upgrade"
# The refused turn's outcome: not "blocked" (the ops intake files every
# blocked Studio request as a customer-blocked P0; this is the product's
# answer, not a failure) and never billable.
OUTCOME = "upgrade_required"
FEATURES = ("shorts", "website_demo")

# The Studio agent's tools behind the gate, by feature. MCP calls run the
# same tools and are never gated (agent_tools gates agent_turn jobs only).
TOOL_FEATURES = {
    "make_shorts": "shorts",
    "record_website": "website_demo",
    "record_website_demo": "website_demo",
    "showcase_demo": "website_demo",
}

# routes/video.MCP_ONLY_PLANS (a test asserts they are equal): the plans
# whose own Studio has no Shorts and no Website demos. Every other plan in
# the server's offer includes them.
MCP_ONLY_PLANS = frozenset({"mcp_connect", "plus_2026"})
ADMIN_EMAIL = "thevalmera@gmail.com"

# free_trial.DEFAULTS' account lists (a test asserts they are equal).
TRIAL_INTERNAL_IDS = (22, 29, 46, 61, 1058)
TRIAL_EXCLUDED_IDS = (60,)
TRIAL_KEY = "free_result"


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _ids(values):
    out = set()
    for value in values or []:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _parse_time(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def trial_flag(conn):
    """The free trial's row (free_trial.settings' keys this gate needs)."""
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (TRIAL_KEY,))
        raw = _v(cur.fetchone(), "value", 0)
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "on": data.get("on") is True or str(data.get("on")).lower() == "true",
        "start_at": _parse_time(data.get("start_at")),
        "test_user_ids": _ids(data.get("test_user_ids")),
        "internal_user_ids": _ids(data.get("internal_user_ids")
                                  if "internal_user_ids" in data
                                  else TRIAL_INTERNAL_IDS),
        "excluded_user_ids": _ids(data.get("excluded_user_ids")
                                  if "excluded_user_ids" in data
                                  else TRIAL_EXCLUDED_IDS),
    }


def trial_switched_on(flag, user_id, now=None):
    """free_trial.switched_on: the flag and the clock (a test account is on
    while the flag is off). Excluded accounts are never trial accounts."""
    uid = int(user_id)
    if uid in flag["excluded_user_ids"]:
        return False
    if uid in flag["test_user_ids"]:
        return True
    if not flag["on"]:
        return False
    if uid in flag["internal_user_ids"]:
        return True
    start = flag["start_at"]
    return start is None or (now or datetime.now(timezone.utc)) >= start


def reason(conn, user_id, now=None):
    """"trial", "plus" or None (not gated). Reads; never writes."""
    if user_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT is_subscribed, plan, email FROM users "
                    "WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
    if row is None:
        return None
    subscribed = bool(_v(row, "is_subscribed", 0))
    plan = str(_v(row, "plan", 1) or "")
    email = str(_v(row, "email", 2) or "").lower()
    if email == ADMIN_EMAIL:
        return None
    import claude_plans
    if subscribed:
        if plan in MCP_ONLY_PLANS and claude_plans.studio_included(
                conn, user_id, plan, True):
            return "plus"
        return None
    if not trial_switched_on(trial_flag(conn), user_id, now):
        return None
    return "trial" if claude_plans.lane_open(conn, now=now) else None


def safe_reason(conn, user_id):
    """``reason`` inside a savepoint: any error reads as not gated (today's
    behaviour) and leaves the caller's transaction usable."""
    import claude_plans
    return claude_plans.safe(conn, reason, user_id, default=None)


def tool_feature(name):
    return TOOL_FEATURES.get(str(name or ""))


# ------------------------------------------------------------------ #
#  Typed requests                                                     #
# ------------------------------------------------------------------ #
# Deliberately narrow: a typed request is answered with the card before any
# model runs only when it plainly asks for several shorts or for a website
# recording. Anything else runs as usual, and the Studio agent's gated tools
# answer with the same card if it reaches for them (agent_tools).

_COUNT = (r"(?:\d{1,2}|a\s+few|few|several|some|multiple|many|a\s+couple"
          r"(?:\s+of)?|two|three|four|five|six|seven|eight|nine|ten|"
          r"twelve|fifteen|twenty)")
_SHORT_WORDS = r"(?:shorts|reels|tiktoks|youtube\s+shorts|short\s+clips)"
_SOURCE_WORDS = (r"(?:podcast|interview|episode|livestream|live\s+stream|"
                 r"stream|webinar|talk|long\s+video|long-form|longform|"
                 r"full\s+video|whole\s+video)")
_SHORTS_PATTERNS = (
    re.compile(r"\b" + _COUNT + r"\s+(?:[\w'-]+\s+){0,3}?" + _SHORT_WORDS
               + r"\b", re.I),
    re.compile(r"\b(?:into|to|as)\s+(?:[\w'-]+\s+){0,2}?" + _SHORT_WORDS
               + r"\b", re.I),
    re.compile(r"\b(?:make|create|cut|generate|extract|pull|clip|chop|"
               r"split|get|give\s+me|find)\s+(?:[\w'-]+\s+){0,2}?"
               + _SHORT_WORDS + r"\b", re.I),
    re.compile(r"\b(?:shorts|reels|clips|tiktoks|highlights)\s+(?:from|out\s+of)"
               r"\s+(?:[\w'-]+\s+){0,2}?" + _SOURCE_WORDS + r"\b", re.I),
    re.compile(r"\b" + _SOURCE_WORDS + r"\b.{0,40}\b(?:into|to)\s+"
               r"(?:[\w'-]+\s+){0,2}?(?:shorts|reels|clips|tiktoks)\b",
               re.I),
)
_URL = re.compile(r"(?:https?://|\bwww\.)[^\s<>\"')]+", re.I)
_SITE_WORDS = re.compile(
    r"\b(?:my|our|the|this|a)\s+(?:web\s*site|site|landing\s+page|"
    r"web\s*page|web\s+app|saas|app's\s+site)\b", re.I)
_RECORD_WORDS = re.compile(
    r"\b(?:record|recording|screen[\s-]*record\w*|screen\s*capture|capture|"
    r"demo|walk[\s-]*through|showcase|scroll\w*|tour)\b", re.I)
# A link to a video (an upload by link) is footage, never a website demo.
_VIDEO_HOSTS = re.compile(
    r"(?:youtube\.com|youtu\.be|vimeo\.com|tiktok\.com|instagram\.com|"
    r"twitter\.com|x\.com|facebook\.com|fb\.watch|twitch\.tv|"
    r"drive\.google\.com|dropbox\.com|loom\.com|streamable\.com|"
    r"\.mp4\b|\.mov\b|\.webm\b|\.m4v\b|\.mkv\b)", re.I)


def website_link(text):
    """The first link in ``text`` that is not a video link, else None."""
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?")
        if not _VIDEO_HOSTS.search(url):
            return url
    return None


def request_feature(text):
    """The gated feature a typed request plainly asks for, else None."""
    text = str(text or "")[:4000]
    if not text.strip():
        return None
    if _RECORD_WORDS.search(text) and (website_link(text)
                                       or _SITE_WORDS.search(text)):
        return "website_demo"
    for pattern in _SHORTS_PATTERNS:
        if pattern.search(text):
            return "shorts"
    return None


# ------------------------------------------------------------------ #
#  The upgrade card                                                   #
# ------------------------------------------------------------------ #

FEATURE_LINES = {
    "shorts": "Upgrade to turn your podcasts and long videos into shorts.",
    "website_demo": "Upgrade to record your website as a demo video.",
    None: ("Upgrade to turn podcasts into shorts and to record your "
           "website as a demo video."),
}
REASON_LINES = {
    "trial": ("Your free trial edits your own footage: upload a video to "
              "get your first cut."),
    "plus": "Plus keeps editing your own footage here and through MCP.",
}


def upgrade_plans(offer):
    """The offer's plans that include Shorts and Website demos: every plan
    the server offers except the MCP-only ones, in the server's order."""
    out = []
    for plan in (offer or {}).get("plans") or []:
        if isinstance(plan, dict) and plan.get("id") \
                and plan.get("id") not in MCP_ONLY_PLANS \
                and not plan.get("sold_out"):
            out.append(dict(plan))
    return out


def _names(plans):
    names = [str(p.get("name") or "").strip() for p in plans or []]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def title(plans=None):
    names = _names(plans)
    return ("Shorts and Website demos come with " + names + "."
            if names else
            "Shorts and Website demos come with the plans below.")


def body(feature=None, why=None):
    line = FEATURE_LINES.get(feature if feature in FEATURES else None)
    extra = REASON_LINES.get(why)
    return line + (" " + extra if extra else "")


def message_text(feature=None, why=None, plans=None):
    return title(plans) + " " + body(feature, why)


def card(feature=None, why=None, offer=None):
    """The structured upgrade card the Studio renders (the 200 body of a
    refused request, the chat message's meta, the balance's key). ``offer``
    is the server's subscribe offer (backend credits.subscribe_offer_body);
    without one the card names no plan and the Studio shows the plans of
    the balance's own card."""
    plans = upgrade_plans(offer)
    out = {
        "code": CODE, "kind": MESSAGE_KIND, "upgrade_required": True,
        "feature": feature if feature in FEATURES else None,
        "features": list(FEATURES), "reason": why,
        "title": title(plans), "text": body(feature, why),
        "texts": {f: body(f, why) for f in FEATURES},
        "message": message_text(feature, why, plans),
        "plan_ids": [p["id"] for p in plans],
    }
    if plans:
        out["subscription_offer"] = {
            "plans": plans, "subscribe_offer": True, "trial_days": 0,
            "comparison_plans": [], "feature_upgrade": True}
    return out


def message_meta(feature=None, why=None, offer=None, **extra):
    """chat_messages.meta of the card as an assistant reply."""
    c = card(feature, why, offer)
    meta = {"kind": MESSAGE_KIND, "feature_upgrade": True,
            "feature": c["feature"], "reason": why, "title": c["title"],
            "text": c["text"], "plan_ids": c["plan_ids"],
            "outcome": OUTCOME}
    if c.get("subscription_offer"):
        meta["subscription_offer"] = c["subscription_offer"]
    meta.update(extra)
    return meta
