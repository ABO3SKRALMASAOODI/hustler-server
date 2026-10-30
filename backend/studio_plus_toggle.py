"""The owner's switch for "Studio in Plus" (owner decision D1, 30 Oct 2026).

Owner: "include the studio in the plus, but that would be with the trial
release. So we can turn it off if we wanted. Or if we run out of credits, it
turns off automatically. just like the trial thing."

It mirrors the free-trial switch (trial_toggle.py) exactly; the admin's
"Claude features" card shows both, side by side:

* **ON**: Plus subscribers whose subscription started after the release
  cutoff (``studio_for_all_plans.cutoff_at``) have the Studio AI editor, on
  Claude (``plan_models``: claude-haiku-5-5 for Plus, Pro and Max) through the credited key,
  metered into the same cycle ledger as the trial, and charged credits per
  turn like every subscriber (worker db.charge_turn_credits). Every other
  existing subscriber, grandfathered Plus included, is unchanged.
* **OFF**: the owner switched it off. Plus is MCP-only again for everyone,
  within 30 seconds (the Studio gate, the held-request resume and the plan
  cards read claude_plans.studio_included / plus_studio_live).
* **AUTO-OFF with the reason**: the owner left it on, but the shared Claude
  credit line (claude_credit.py: this cycle's spend reached the line, or
  Anthropic refused for billing or the key) or its ledger (migration 040) is
  not there. Plus is MCP-only by itself, and it comes back by itself with the
  credit line's next-cycle probe, exactly like the trial.

The switch writes ONLY ``app_kv.studio_for_all_plans.on`` (plus who and
when; the first ON also fixes ``cutoff_at`` at that moment if the row has
none, so everyone subscribed before it stays grandfathered). It never writes
``claude_credit_state``: pressing ON while AUTO-OFF records the owner's
intent and the card keeps saying AUTO-OFF until the credit is back.
"""
import json
from datetime import datetime, timezone

import claude_credit
import claude_plans

KEY = claude_plans.KEY
PLAN = "plus_2026"
# Filled in by the first ON when the row lacks them. Owner decision (30 Oct
# 2026): every new subscriber's Studio model is claude-haiku-5-5, for Plus,
# Pro and Max alike (not Sonnet); written out so the row says it.
NEW_SUBSCRIBER_MODEL = "claude-haiku-5-5"
RELEASE_VALUES = {"studio_plans": [PLAN],
                  "plan_models": {PLAN: NEW_SUBSCRIBER_MODEL,
                                  "pro_2026": NEW_SUBSCRIBER_MODEL,
                                  "max_2026": NEW_SUBSCRIBER_MODEL}}
REASON_TEXT = {
    claude_credit.REASON_BUDGET:
        "this cycle's Claude spend reached the line",
    claude_credit.REASON_BILLING:
        "Anthropic refused a call for billing or credit",
    claude_credit.REASON_AUTH: "Anthropic refused the key",
    "ledger_missing": "the Claude ledger (migration 040) is missing",
    "cutoff_missing": "the release cutoff (cutoff_at) is missing",
    "plus_not_listed": "the row's studio_plans does not list plus_2026",
}


def _v(row, key, index=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[index]


def _stored(cur):
    cur.execute("SELECT value, updated_at FROM app_kv WHERE key = %s", (KEY,))
    row = cur.fetchone()
    raw = _v(row, "value", 0)
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        value = {}
    return (value if isinstance(value, dict) else {}), _v(row, "updated_at", 1)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _new_plus_count(conn, cutoff):
    """Plus subscribers right now whose subscription began after the cutoff
    (claude_plans.grandfathered's rule, as one count). None = unknown.

    The admin GET runs on a read-only autocommit connection, where a
    SAVEPOINT is an error; only a transaction (the POST's re-read) needs one
    so a failed count cannot poison it."""
    if cutoff is None:
        return None
    guard = not getattr(conn, "autocommit", False)
    try:
        with conn.cursor() as cur:
            if guard:
                cur.execute("SAVEPOINT studio_plus_count")
            cur.execute("SELECT to_regclass('public.admin_subscription_snapshots') AS t")
            snapshots = bool(_v(cur.fetchone(), "t", 0))
            cur.execute(
                """SELECT COUNT(*) AS n FROM users u
                    WHERE u.plan = %s AND COALESCE(u.is_subscribed::int, 0) = 1
                      AND NOT EXISTS (
                          SELECT 1 FROM payments p
                           WHERE p.user_id = u.id AND p.amount_cents > 0
                             AND p.status IN ('completed', 'paid')
                             AND COALESCE(p.occurred_at, p.created_at) < %s)"""
                + ("""
                      AND NOT EXISTS (
                          SELECT 1 FROM admin_subscription_snapshots s
                           WHERE s.subscription_id = u.subscription_id
                             AND s.started_at < %s)""" if snapshots else ""),
                (PLAN, cutoff, cutoff) if snapshots else (PLAN, cutoff))
            n = int(_v(cur.fetchone(), "n", 0) or 0)
            if guard:
                cur.execute("RELEASE SAVEPOINT studio_plus_count")
            return n
    except Exception:
        if guard:
            try:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK TO SAVEPOINT studio_plus_count")
                    cur.execute("RELEASE SAVEPOINT studio_plus_count")
            except Exception:
                pass
        return None


def _paid_spend(conn, start, end):
    """Subscribers' Claude spend and calls this cycle (the paid half of the
    shared ledger). (None, None) when the ledger is missing."""
    if not claude_credit.ledger_ready(conn):
        return None, None
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(paid_usd), 0) AS usd,
                              COALESCE(SUM(paid_calls), 0) AS calls
                         FROM trial_budget_days
                        WHERE day >= %s AND day < %s""",
                    (start[:10], end[:10]))
        row = cur.fetchone()
    return (round(float(_v(row, "usd", 0) or 0), 4),
            int(_v(row, "calls", 1) or 0))


def status(conn, now=None):
    """Everything the card shows for Studio in Plus. Read-only."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        stored, updated_at = _stored(cur)
    flag = claude_plans.parse_flag(stored)
    owner_on = (stored.get("on") is True
                or str(stored.get("on")).lower() == "true")
    ledger = claude_credit.ledger_ready(conn)
    credit = claude_credit.status(conn, now=now)
    reason = credit.get("reason")
    if not owner_on:
        state, reason = "off", None
    elif flag["cutoff_at"] is None:
        state, reason = "auto_off", "cutoff_missing"
    elif PLAN not in flag["studio_plans"]:
        # D1V: a hand-written studio_plans without Plus keeps Plus MCP-only
        # (claude_plans.plus_studio_live); the card must not say ON.
        state, reason = "auto_off", "plus_not_listed"
    elif not ledger:
        state, reason = "auto_off", "ledger_missing"
    elif not credit.get("on"):
        state = "auto_off"
    else:
        state, reason = "on", None
    if state == "off":
        why = "switched off by the owner"
    elif state == "auto_off":
        why = REASON_TEXT.get(reason, str(reason or "the credit switch is off"))
    else:
        why = None
    comes_back = None
    if state == "auto_off":
        comes_back = (
            "at the next probe once the key works"
            if reason == claude_credit.REASON_AUTH else
            "after migration 040 is applied" if reason == "ledger_missing"
            else "when the switch is pressed again" if reason == "cutoff_missing"
            else "when plus_2026 is back in studio_plans"
            if reason == "plus_not_listed"
            else f"by itself in the next cycle ({credit['cycle_end'][:10]})")
    paid_usd, paid_calls = _paid_spend(conn, credit["cycle_start"],
                                       credit["cycle_end"])
    lanes = ({plan: claude_plans.lane_model(flag, plan)
              for plan in ("plus_2026", "pro_2026", "max_2026")}
             if flag["claude_lane"] else {})
    return {
        "state": state,
        "owner_on": owner_on,
        "reason": reason if state == "auto_off" else None,
        "reason_text": why,
        "comes_back": comes_back,
        "since": credit.get("since") if state == "auto_off" else None,
        "plus_included": PLAN in flag["studio_plans"],
        "cutoff_at": (flag["cutoff_at"].isoformat()
                      if flag["cutoff_at"] else None),
        "model": claude_plans.lane_model(flag, PLAN) if flag["claude_lane"]
        else None,
        "claude_lane": flag["claude_lane"],
        "lanes": lanes,
        "credit_parity": flag["credit_parity"],
        "spend": {"spent_usd": credit.get("spent_usd"),
                  "budget_usd": credit.get("budget_usd"),
                  "line_usd": credit.get("line_usd"),
                  "cycle_start": credit.get("cycle_start"),
                  "cycle_end": credit.get("cycle_end"),
                  "subscribers_spent_usd": paid_usd,
                  "subscribers_calls": paid_calls},
        "accounts": {"new_plus": _new_plus_count(conn, flag["cutoff_at"])},
        "test_user_ids": sorted(flag["test_user_ids"]),
        "ledger_ready": ledger,
        "updated_at": _iso(updated_at),
        "updated_by": stored.get("updated_by"),
        "takes_effect_s": 30,
    }


def switch(conn, on, actor="admin", now=None):
    """Write the owner's choice into app_kv.studio_for_all_plans (merged;
    every other key kept). Returns the new stored row. The caller commits."""
    now = now or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock("
                    "hashtext('studio_for_all_plans_switch'))")
        stored, _ = _stored(cur)
        value = dict(stored)
        value["on"] = bool(on)
        if on:
            # A cutoff claude_plans cannot read is no cutoff (parse_flag keeps
            # the row OFF): the first ON that works fixes it here (D1V).
            if claude_plans._parse_time(value.get("cutoff_at")) is None:
                # The release cutoff: everyone subscribed before the first ON
                # is grandfathered (today's lanes and plan rules, exactly).
                value["cutoff_at"] = now.isoformat()
            for key, default in RELEASE_VALUES.items():
                value.setdefault(key, default)
        value["updated_by"] = str(actor)[:80]
        value["switched_at"] = now.isoformat()
        cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                       VALUES (%s, %s, NOW())
                       ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = NOW()""",
                    (KEY, json.dumps(value, sort_keys=True)))
    # This process's plan cards read the new row at once; the others (and the
    # worker, which reads the row every turn) within 30 s.
    try:
        import credits
        credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    except Exception:  # pragma: no cover
        pass
    return value


def features(conn, now=None):
    """The "Claude features" card: both switches and the shared line."""
    import trial_toggle
    now = now or datetime.now(timezone.utc)
    trial = trial_toggle.status(conn, now=now)
    studio = status(conn, now=now)
    return {"trial": trial, "studio_plus": studio,
            "spend": studio["spend"], "takes_effect_s": 30}
