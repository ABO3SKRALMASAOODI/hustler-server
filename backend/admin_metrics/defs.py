"""The populations and rules every admin number shares.

G2 (customer): a verified account created on/after ADMIN_METRICS_EPOCH that is
not the admin account and not listed in ADMIN_EXCLUDE_EMAILS. Every business
number uses customer('u'). Operations pages may add the owner's account with
include_owner=1, never the test accounts.

All values that reach SQL from here are operator constants (environment) that
are validated before being inlined; request input never passes through here.
"""
import os
import re
from datetime import datetime, timezone

ADMIN_EMAIL = "thevalmera@gmail.com"

_EPOCH = os.getenv("ADMIN_METRICS_EPOCH", "2026-07-06").strip()
METRICS_EPOCH = _EPOCH if re.match(r"^\d{4}-\d{2}-\d{2}$", _EPOCH) \
    else "2026-07-06"

# The admin's own account is never a customer; ADMIN_EXCLUDE_EMAILS adds test
# accounts on top (comma-separated) and never replaces the admin exclusion.
EXCLUDE_EMAILS = [ADMIN_EMAIL.lower()] + [
    e.strip().lower() for e in os.getenv("ADMIN_EXCLUDE_EMAILS", "").split(",")
    if e.strip() and e.strip().lower() != ADMIN_EMAIL.lower()]


def _quote(value):
    return "'" + value.replace("'", "''") + "'"


EXCLUDED_SQL = ",".join(_quote(e) for e in EXCLUDE_EMAILS)
TEST_ACCOUNTS = len(EXCLUDE_EMAILS) - 1


def _p(alias):
    return f"{alias}." if alias else ""


def scope(alias="u"):
    """Post-relaunch, not admin, not a listed test account (no verified test)."""
    p = _p(alias)
    return (f"{p}created_at >= DATE '{METRICS_EPOCH}' "
            f"AND LOWER({p}email) NOT IN ({EXCLUDED_SQL})")


def customer(alias="u"):
    """G2: the one customer population used by every business number."""
    p = _p(alias)
    return f"({p}is_verified = 1 AND {scope(alias)})"


def owner(alias="u"):
    return f"(LOWER({_p(alias)}email) = {_quote(ADMIN_EMAIL.lower())})"


def population(alias="u", include_owner=False):
    """Customers, optionally plus the owner's own account (operations pages)."""
    if include_owner:
        return f"({customer(alias)} OR {owner(alias)})"
    return customer(alias)


def is_internal_email(email):
    return (email or "").strip().lower() in EXCLUDE_EMAILS


def success(alias="p"):
    """G5: a successful payment. A $0 'completed' row is a trial, not money."""
    p = _p(alias)
    return f"({p}status IN ('completed','paid') AND {p}amount_cents > 0)"


def failed_payment(alias="p"):
    p = _p(alias)
    return (f"(({p}status = 'past_due' OR ({p}status = 'ready' "
            f"AND {p}error_code IS NOT NULL)) AND {p}amount_cents > 0)")


def paying(alias="u"):
    """Paddle says active (or a pre-billing-column grandfathered row)."""
    p = _p(alias)
    return (f"({p}is_subscribed = 1 AND ({p}billing_status = 'active' OR "
            f"({p}billing_status IS NULL AND {p}trial_status IS DISTINCT FROM "
            f"'trialing')))")


def failing(alias="u"):
    return f"({_p(alias)}billing_status IN ('past_due','paused'))"


# ── Visitors ─────────────────────────────────────────────────────────────
# One robot list for the write path (website_analytics) and every report.
ROBOT_UA = (r"bot|crawler|spider|slurp|headless|notebooklm|vercel-screenshot|"
            r"GoogleOther|Google-InspectionTool|Google-Read-Aloud|Lighthouse|"
            r"PageSpeed|Claude/|ChatGPT-User|OAI-SearchBot|Perplexity|"
            r"facebookexternalhit|meta-external|aweme|Bytespider|"
            r"Nexus 5X Build/MMB29P")
ROBOT_UA_RE = re.compile(ROBOT_UA, re.I)
# Meta opens every link in a DM to build a preview, from these referrers, at
# the moment the message is SENT. A coded single-page load from one of these
# hosts with no human signal is that preview, not a person.
PREVIEW_REFERRERS = ("www.facebook.com", "m.facebook.com", "l.facebook.com",
                     "lm.facebook.com")
# A browser with one page and no interaction but this many active seconds is
# still a person: the activity clock stops 30 s after the last input, so an
# untouched page cannot pass it.
PERSON_ACTIVE_SECONDS = 30
INTERNAL_DEVICE_IDS = tuple(
    d.strip() for d in os.getenv("ADMIN_INTERNAL_DEVICE_IDS", "").split(",")
    if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", d.strip() or "x"))

# ── Jobs ─────────────────────────────────────────────────────────────────
USER_FACING_JOB_TYPES = ("index", "preview", "final", "agent_turn",
                         "shorts_plan")
JOB_LABELS = {
    "index": "Video analysis", "preview": "Preview", "final": "Export",
    "agent_turn": "Edit request", "shorts_plan": "Shorts plan",
    "mcp_tool": "AI app edit", "filmstrip": "Filmstrip",
    "preview_check": "Preview check",
}
JOB_FAILED_LABELS = {
    "index": "Analysis failed", "preview": "Preview failed",
    "final": "Export failed", "agent_turn": "Edit request failed",
    "shorts_plan": "Shorts plan failed",
}

# ── Tracking start dates (G7) ────────────────────────────────────────────
# Facts about history, read once from production and frozen here so every
# request does not rescan page_visits to rediscover them.
VISITS_SINCE = datetime(2026, 10, 3, 16, 7, 35, tzinfo=timezone.utc)
SOURCES_SINCE = datetime(2026, 10, 7, 12, 34, 12, tzinfo=timezone.utc)
SIGNUP_LINK_SINCE = datetime(2026, 10, 3, 19, 29, 50, tzinfo=timezone.utc)
CHECKOUT_SINCE = datetime(2026, 10, 2, 8, 47, 8, tzinfo=timezone.utc)


def iso(value):
    """ISO 8601 UTC with Z for an aware or naive-UTC datetime; None stays None."""
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def usd(cents):
    return round(float(cents or 0) / 100.0, 2)


def pct(num, den, digits=1):
    if not den:
        return None
    return round(100.0 * float(num or 0) / float(den), digits)
