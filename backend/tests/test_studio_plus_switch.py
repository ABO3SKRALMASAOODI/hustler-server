"""Studio in Plus behind the owner's switch and the trial's credit line
(owner decision D1, 30 Oct 2026).

Owner: "include the studio in the plus, but that would be with the trial
release. So we can turn it off if we wanted. Or if we run out of credits, it
turns off automatically. just like the trial thing."

Proved here, over one in-memory model of the rows these modules read:
  * the switch (studio_plus_toggle): OFF by default; ON fixes the release
    cutoff once and keeps every other key; OFF; ON while AUTO-OFF stays
    AUTO-OFF; the admin routes are admin-only and validate the body;
  * AUTO-OFF and back: the shared Claude credit line (claude_credit) at the
    line, on a billing refusal or without its ledger turns Studio-in-Plus off
    for new Plus subscribers at once; the credit line back on brings it back;
  * grandfathered subscribers (Plus included) get EXACTLY today's gate, body
    and balance in every state;
  * a new Plus subscriber: the Studio while ON, the plain "not included right
    now" refusal and the MCP path while OFF or AUTO-OFF;
  * the plan cards (the Studio offer and the public /billing/plan-includes)
    describe Plus with the Studio only while it is live, prices unchanged;
  * held requests: the resume runs a Studio turn while ON and goes to the MCP
    hand-off (ticket #11) while OFF or AUTO-OFF;
  * the automatic First cut: an MCP-only plan never gets one.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import claude_plans  # noqa: E402
import credits  # noqa: E402
import studio_plus_toggle  # noqa: E402

UTC = timezone.utc
NOW = datetime.now(UTC)
CUTOFF = NOW - timedelta(days=1)
ROOT = Path(__file__).resolve().parents[1]

GRANDFATHERED = 5   # Plus since before the cutoff
NEW_PLUS = 8        # Plus since after the cutoff
NEW_PRO = 9
NEW_MCP = 10        # MCP Connect since after the cutoff

TODAY_BODY = {"error": "You’re on the MCP plan. Upgrade to use the Valmera AI editor.",
              "code": "mcp_only", "connect_url": "/mcp/connect",
              "upgrade_url": "/subscribe"}


class World:
    """users, payments, app_kv and the Claude ledger, by statement shape. The
    cursor is its own connection (claude_plans / claude_credit expect one)."""

    def __init__(self, studio=None, credit_state=None, credit_cfg=None,
                 ledger=True, cycle_usd=0.0, paid=(0.0, 0)):
        self.kv = {}
        if studio is not None:
            self.kv["studio_for_all_plans"] = json.dumps(studio)
        if credit_state is not None:
            self.kv["claude_credit_state"] = json.dumps(credit_state)
        if credit_cfg is not None:
            self.kv["claude_credit"] = json.dumps(credit_cfg)
        self.ledger = ledger
        self.cycle_usd = cycle_usd
        self.paid = paid
        self.users = {
            GRANDFATHERED: {"plan": "plus_2026", "is_subscribed": 1, "sub": "s5"},
            NEW_PLUS: {"plan": "plus_2026", "is_subscribed": 1, "sub": "s8"},
            NEW_PRO: {"plan": "pro_2026", "is_subscribed": 1, "sub": "s9"},
            NEW_MCP: {"plan": "mcp_connect", "is_subscribed": 1, "sub": "s10"},
        }
        self.payments = [(GRANDFATHERED, CUTOFF - timedelta(days=20), 1500),
                         (NEW_PLUS, CUTOFF + timedelta(hours=2), 1500),
                         (NEW_PRO, CUTOFF + timedelta(hours=3), 5000),
                         (NEW_MCP, CUTOFF + timedelta(hours=4), 2500)]
        self.snapshots = {}
        # D1V: accounts whose own assistant made an MCP call since the wall
        self.mcp_work = set()
        self.writes = []
        self.marked = []
        self._row = None
        self._uid = None

    # one object is the connection and the cursor
    @property
    def connection(self):
        return self

    def cursor(self):
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _before(self, uid, cutoff):
        return any(p[0] == uid and p[2] > 0 and p[1] < cutoff
                   for p in self.payments)

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        p = params or ()
        self._row = None
        if q.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")) or \
                "pg_advisory_xact_lock" in q:
            return
        if q.startswith("SELECT to_regclass('public.trial_budgets')"):
            self._row = {"a": "t" if self.ledger else None,
                         "b": "t" if self.ledger else None}
        elif q.startswith("SELECT to_regclass"):
            self._row = {"t": "t"}
        elif q.startswith("SELECT value, updated_at FROM app_kv"):
            v = self.kv.get(p[0])
            self._row = None if v is None else {"value": v, "updated_at": NOW}
        elif q.startswith("SELECT value FROM app_kv"):
            v = self.kv.get(p[0])
            self._row = None if v is None else {"value": v}
        elif q.startswith("INSERT INTO app_kv"):
            self.kv[p[0]] = p[1]
            self.writes.append(p[0])
        elif "information_schema.columns" in q:
            self._row = {"x": 1} if self.ledger else None
        elif "GREATEST(spent_usd - extras_usd" in q:
            self._row = {"usd": self.cycle_usd}
        elif q.startswith("SELECT COALESCE(SUM(paid_usd), 0) AS usd"):
            self._row = {"usd": self.paid[0], "calls": self.paid[1]}
        elif "FROM trial_budgets" in q:
            self._row = {"cycle": 0, "all_time": 0, "exhausted": 0, "spent": 0}
        elif "meta ? 'free_result'" in q:
            self._row = {"n": 0}
        elif q.startswith("SELECT 1 FROM payments"):
            uid, cutoff = p
            self._row = {"x": 1} if self._before(int(uid), cutoff) else None
        elif "FROM admin_subscription_snapshots s JOIN users u" in q:
            uid, cutoff = p
            start = self.snapshots.get(int(uid))
            self._row = {"x": 1} if start and start < cutoff else None
        elif q.startswith("SELECT plan, is_subscribed FROM users"):
            u = self.users.get(int(p[0])) or {}
            self._row = {"plan": u.get("plan"),
                         "is_subscribed": u.get("is_subscribed")}
        elif q.startswith("SELECT plan FROM users WHERE id"):
            self._row = {"plan": (self.users.get(int(p[0])) or {}).get("plan")}
        elif "type = 'mcp_tool'" in q:
            self._row = {"x": 1} if int(p[0]) in self.mcp_work else None
        elif q.startswith("SELECT COUNT(*) AS n FROM users u"):
            plan, cutoff = p[0], p[1]
            self._row = {"n": sum(
                1 for uid, u in self.users.items()
                if u["plan"] == plan and u["is_subscribed"]
                and not self._before(uid, cutoff)
                and not (self.snapshots.get(uid)
                         and self.snapshots[uid] < cutoff))}
        elif q.startswith("UPDATE chat_messages"):
            self.marked.append(p[1])
            self._row = {"id": p[1]}
        else:
            raise AssertionError(f"unexpected SQL: {q[:160]}")

    def fetchone(self):
        return self._row


def _studio(on=True, **extra):
    return dict({"on": on, "cutoff_at": CUTOFF.isoformat()}, **extra)


ON = dict(studio=_studio())
OFF = dict(studio=_studio(on=False))
AUTO_BUDGET = dict(studio=_studio(), cycle_usd=191.0)
AUTO_BILLING = dict(studio=_studio(), credit_state={
    "state": "off", "reason": "billing_error",
    "since": NOW.isoformat(), "cycle_end": "2026-11-08T00:00:00+00:00"})
NO_LEDGER = dict(studio=_studio(), ledger=False)
STATES = {"on": ON, "off": OFF, "auto_budget": AUTO_BUDGET,
          "auto_billing": AUTO_BILLING, "no_ledger": NO_LEDGER}


@pytest.fixture
def app():
    from app import create_app
    application = create_app()
    with application.app_context():
        yield application


def _gate(world, uid):
    from routes import video
    out = video._studio_ai_gate_unrecorded(world, uid)
    if out is None:
        return None
    response, status = out
    return status, response.get_json()


# ------------------------------------------------------------- the switch

def test_off_by_default_and_on_fixes_the_release_cutoff_once():
    w = World()
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "off" and st["owner_on"] is False
    assert st["reason_text"] == "switched off by the owner"
    assert st["cutoff_at"] is None and st["accounts"]["new_plus"] is None
    assert st["model"] == "claude-haiku-5-5"
    assert st["spend"]["budget_usd"] == 200.0 and st["takes_effect_s"] == 30
    when = NOW.replace(microsecond=0)
    value = studio_plus_toggle.switch(w, True, now=when)
    assert value["on"] is True and value["cutoff_at"] == when.isoformat()
    assert value["studio_plans"] == ["plus_2026"]
    assert value["plan_models"] == {"plus_2026": "claude-haiku-5-5"}
    assert w.writes == ["studio_for_all_plans"]       # never the credit state
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "on" and st["lanes"]["plus_2026"] == "claude-haiku-5-5"
    # OFF keeps the cutoff; the next ON never moves it
    studio_plus_toggle.switch(w, False, now=when + timedelta(hours=1))
    assert studio_plus_toggle.status(w, now=NOW)["state"] == "off"
    again = studio_plus_toggle.switch(w, True, now=when + timedelta(days=2))
    assert again["cutoff_at"] == when.isoformat()


def test_switching_keeps_every_other_key_and_never_replaces_a_set_value():
    w = World(studio={"on": False, "cutoff_at": CUTOFF.isoformat(),
                      "test_user_ids": [22], "plan_models": {
                          "plus_2026": "claude-sonnet-5-5"}})
    value = studio_plus_toggle.switch(w, True, now=NOW)
    assert value["test_user_ids"] == [22]
    assert value["cutoff_at"] == CUTOFF.isoformat()
    assert value["plan_models"] == {"plus_2026": "claude-sonnet-5-5"}
    assert value["updated_by"] == "admin" and value["switched_at"]


def test_an_unreadable_cutoff_is_fixed_by_the_next_on():
    """D1V: a hand-written cutoff claude_plans cannot parse keeps the row
    OFF (parse_flag); the card said "comes back when the switch is pressed
    again", but pressing kept the bad value. The ON now fixes it once."""
    w = World(studio={"on": True, "cutoff_at": "next week"})
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "auto_off" and st["reason"] == "cutoff_missing"
    studio_plus_toggle.switch(w, False, now=NOW)
    when = NOW.replace(microsecond=0)
    value = studio_plus_toggle.switch(w, True, now=when)
    assert value["cutoff_at"] == when.isoformat()
    assert studio_plus_toggle.status(w, now=NOW)["state"] == "on"


def test_a_row_without_plus_never_says_plus_has_the_studio(app):
    """D1V: studio_plans written without plus_2026 keeps Plus MCP-only
    (cards, gate); the Studio-in-Plus row must not read ON."""
    w = World(studio=_studio(studio_plans=[]))
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "auto_off" and st["reason"] == "plus_not_listed"
    assert "plus_2026" in st["comes_back"] and st["plus_included"] is False
    assert claude_plans.plus_studio_live(w) is False
    assert _gate(w, NEW_PLUS)[0] == 403


def test_the_switch_clears_the_plan_card_cache_at_once():
    credits._D1_FLAG_CACHE.update(value=True, expires=1e18)
    studio_plus_toggle.switch(World(), False, now=NOW)
    assert credits._D1_FLAG_CACHE["value"] is None


def test_on_counts_the_new_plus_subscribers_and_the_shared_spend():
    w = World(paid=(1.25, 140), cycle_usd=3.5, **ON)
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "on"
    assert st["accounts"]["new_plus"] == 1            # 8 only, never 5
    assert st["spend"]["spent_usd"] == 3.5
    assert st["spend"]["subscribers_spent_usd"] == 1.25
    assert st["spend"]["subscribers_calls"] == 140
    assert st["spend"]["line_usd"] == 190.0


def test_the_admin_read_counts_on_an_autocommit_connection():
    """The admin GET runs read-only autocommit, where SAVEPOINT is an error
    (seen on the local stack: the count showed "unknown")."""
    class Autocommit(World):
        autocommit = True

        def execute(self, sql, params=None):
            assert not sql.lstrip().startswith(("SAVEPOINT", "RELEASE",
                                                "ROLLBACK")), sql
            return super().execute(sql, params)
    st = studio_plus_toggle.status(Autocommit(**ON), now=NOW)
    assert st["accounts"]["new_plus"] == 1


@pytest.mark.parametrize("state,reason,text,back", [
    ("auto_budget", "budget", "reached the line", "next cycle"),
    ("auto_billing", "billing_error", "billing", "next cycle"),
    ("no_ledger", "ledger_missing", "migration 040", "migration 040"),
])
def test_auto_off_names_the_reason_and_when_it_comes_back(state, reason, text,
                                                          back):
    st = studio_plus_toggle.status(World(**STATES[state]), now=NOW)
    assert st["state"] == "auto_off" and st["owner_on"] is True
    assert st["reason"] == reason
    assert text in st["reason_text"] and back in st["comes_back"]


def test_on_while_auto_off_stays_auto_off_and_never_writes_the_credit():
    w = World(studio=_studio(on=False), credit_state={
        "state": "off", "reason": "budget"})
    studio_plus_toggle.switch(w, True, now=NOW)
    st = studio_plus_toggle.status(w, now=NOW)
    assert st["state"] == "auto_off" and st["owner_on"] is True
    assert "claude_credit_state" not in w.writes


def test_the_card_shows_both_switches_on_one_shared_line():
    w = World(**ON)
    w.kv["free_result"] = json.dumps({"on": False})
    out = studio_plus_toggle.features(w, now=NOW)
    assert out["trial"]["state"] == "off"
    assert out["studio_plus"]["state"] == "on"
    assert out["spend"]["budget_usd"] == out["trial"]["spend"]["budget_usd"]
    # the line going AUTO-OFF takes both down together
    w.cycle_usd = 195.0
    w.kv["free_result"] = json.dumps({"on": True})
    out = studio_plus_toggle.features(w, now=NOW)
    assert out["trial"]["state"] == "auto_off"
    assert out["studio_plus"]["state"] == "auto_off"


def test_the_routes_are_admin_only_and_validate_the_body():
    src = (ROOT / "routes" / "admin_video.py").read_text()
    for route in ('"/admin/video/claude-features", methods=["GET"]',
                  '"/admin/video/studio-plus", methods=["GET"]',
                  '"/admin/video/studio-plus", methods=["POST"]'):
        body = src.split(f"@admin_video_bp.route({route})", 1)[1]
        assert body.lstrip().startswith("@admin_required"), route
    post = src.split('"/admin/video/studio-plus", methods=["POST"])', 1)[1]
    post = post.split("\n\n\n", 1)[0]
    assert 'isinstance(body.get("on"), bool)' in post
    assert 'studio_plus_toggle.switch(conn, body["on"]' in post


def test_the_routes_answer_through_flask(app, monkeypatch):
    import jwt
    from contextlib import contextmanager
    from routes import admin_video
    w = World(**OFF)
    w.kv["free_result"] = json.dumps({"on": False})

    @contextmanager
    def fake_adb():
        yield w
    monkeypatch.setattr(admin_video, "adb", fake_adb)
    client = app.test_client()
    key = app.config["SECRET_KEY"]
    admin = {"Authorization": "Bearer " + jwt.encode(
        {"sub": "1", "email": "thevalmera@gmail.com"}, key, algorithm="HS256")}
    other = {"Authorization": "Bearer " + jwt.encode(
        {"sub": "5", "email": "someone@example.com"}, key, algorithm="HS256")}
    assert client.get("/admin/video/claude-features",
                      headers=other).status_code == 403
    assert client.post("/admin/video/studio-plus", json={"on": "yes"},
                       headers=admin).status_code == 400
    body = client.get("/admin/video/claude-features", headers=admin).get_json()
    assert body["studio_plus"]["state"] == "off"
    assert body["trial"]["state"] == "off"
    r = client.post("/admin/video/studio-plus", json={"on": True},
                    headers=admin)
    assert r.status_code == 200 and r.get_json()["state"] == "on"
    assert json.loads(w.kv["studio_for_all_plans"])["on"] is True
    r = client.post("/admin/video/studio-plus", json={"on": False},
                    headers=admin)
    assert r.get_json()["state"] == "off"
    assert client.get("/admin/video/studio-plus",
                      headers=admin).get_json()["owner_on"] is False


# ------------------------------------------- who gets it, in every state

@pytest.mark.parametrize("state", sorted(STATES))
def test_grandfathered_plus_is_exactly_today_in_every_state(app, state):
    w = World(**STATES[state])
    assert _gate(w, GRANDFATHERED) == (403, TODAY_BODY)
    assert claude_plans.studio_included(w, GRANDFATHERED, "plus_2026",
                                        True) is False
    assert claude_plans.paused_for(w, GRANDFATHERED, "plus_2026",
                                   True) is False
    # Pro and Max are never refused by this gate
    assert _gate(w, NEW_PRO) is None


def test_grandfathered_plus_without_any_flag_row_is_today(app):
    assert _gate(World(), GRANDFATHERED) == (403, TODAY_BODY)
    assert _gate(World(), NEW_PLUS) == (403, TODAY_BODY)


def test_a_new_plus_subscriber_has_the_studio_while_on(app):
    w = World(**ON)
    assert _gate(w, NEW_PLUS) is None
    assert claude_plans.studio_included(w, NEW_PLUS, "plus_2026", True)
    assert claude_plans.plus_studio_live(w) is True
    # MCP Connect stays MCP-only whatever the switch says
    status, body = _gate(w, NEW_MCP)
    assert status == 403 and body == TODAY_BODY


@pytest.mark.parametrize("state", ["off", "auto_budget", "auto_billing",
                                   "no_ledger"])
def test_off_or_auto_off_a_new_plus_subscriber_is_mcp_only_and_told_so(app,
                                                                       state):
    w = World(**STATES[state])
    status, body = _gate(w, NEW_PLUS)
    assert status == 403 and body["code"] == "mcp_only"
    assert body["studio_ai_paused"] is True
    assert body["error"] == credits.PLUS_STUDIO_PAUSED_TEXT
    assert body["connect_url"] == "/mcp/connect"
    assert "credit" not in body["error"].lower()      # no billing talk
    assert claude_plans.plus_studio_live(w) is False


def test_auto_off_and_back(app):
    w = World(**ON)
    assert _gate(w, NEW_PLUS) is None
    w.cycle_usd = 190.0                                # the line
    assert _gate(w, NEW_PLUS)[0] == 403
    # the next cycle's probe switched the credit line back on (claude_credit
    # .switch_on writes state on; the spend counts from the new cycle)
    w.cycle_usd = 0.4
    w.kv["claude_credit_state"] = json.dumps(
        {"state": "on", "reason": "probe succeeded in a new cycle"})
    assert _gate(w, NEW_PLUS) is None
    assert studio_plus_toggle.status(w, now=NOW)["state"] == "on"


def test_a_snapshot_before_the_cutoff_grandfathers_too(app):
    w = World(**ON)
    w.payments = [x for x in w.payments if x[0] != NEW_PLUS]
    w.snapshots[NEW_PLUS] = CUTOFF - timedelta(days=3)
    assert _gate(w, NEW_PLUS) == (403, TODAY_BODY)


# ----------------------------------------------------- the balance payload

def _balance_keys(w, uid):
    """credits.get_balance's D1 block, alone (the rest of the payload is the
    pools and is unchanged)."""
    out = {}
    if claude_plans.safe(w, claude_plans.studio_included, uid, "plus_2026",
                         True, default=False):
        out["studio_ai_included"] = True
    elif claude_plans.safe(w, claude_plans.paused_for, uid, "plus_2026", True,
                           default=False):
        out["studio_ai_paused"] = True
        out["studio_ai_notice"] = credits.PLUS_STUDIO_PAUSED_TEXT
    return out


def test_the_balance_says_which_plus_this_account_has():
    assert _balance_keys(World(**ON), NEW_PLUS) == {"studio_ai_included": True}
    assert _balance_keys(World(**OFF), NEW_PLUS)["studio_ai_paused"] is True
    assert _balance_keys(World(**AUTO_BUDGET), NEW_PLUS)["studio_ai_paused"]
    for state in STATES.values():
        assert _balance_keys(World(**state), GRANDFATHERED) == {}
    # the code in credits.get_balance is exactly this block
    src = (ROOT / "credits.py").read_text()
    assert 'out["studio_ai_paused"] = True' in src
    assert "claude_plans.paused_for" in src


# --------------------------------------------------------- the plan cards

@pytest.mark.parametrize("state,live", [("on", True), ("off", False),
                                        ("auto_budget", False),
                                        ("auto_billing", False),
                                        ("no_ledger", False)])
def test_the_offer_describes_plus_truthfully_in_each_state(state, live):
    w = World(**STATES[state])
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    before = [dict(p) for p in credits.subscribe_offer_body(World())["plans"]]
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    body = credits.subscribe_offer_body(w)
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    assert ("plus_2026" in (body.get("studio_ai_plan_ids") or [])) is live
    assert ("plan_notes" in body) is live
    assert body["plans"] == before                      # prices, credits


def test_the_public_plan_includes_endpoint(app, monkeypatch):
    client = app.test_client()
    monkeypatch.setattr(credits, "_read_studio_for_all_plans",
                        lambda conn=None: True)
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    r = client.get("/billing/plan-includes")
    body = r.get_json()
    assert r.status_code == 200 and body["plus_studio"] is True
    assert "plus_2026" in body["studio_ai_plan_ids"]
    assert "monthly" not in json.dumps(body)            # no prices here
    monkeypatch.setattr(credits, "_read_studio_for_all_plans",
                        lambda conn=None: False)
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)
    body = client.get("/billing/plan-includes").get_json()
    assert body == {"plus_studio": False}
    credits._D1_FLAG_CACHE.update(value=None, expires=0.0)


# ------------------------------------------------------- held requests

@pytest.fixture
def held_world(app, monkeypatch):
    from contextlib import contextmanager
    from routes import video
    from video_services import held_request
    box = {"world": World(**ON), "sent": []}

    @contextmanager
    def fake_vdb(db_url=None):
        yield box["world"]
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(held_request, "pending", lambda *a, **k: {
        "id": 77, "project_id": 800, "created_at": NOW,
        "meta": {"kind": "subscription_plans", "surface": "chat",
                 "blocked_prompt": {"text": "make it a 20 s vertical demo"}}})
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)

    def send(uid, pid, body):
        box["sent"].append((uid, pid, body["client_msg_id"]))
        from flask import jsonify
        return jsonify({"queued": True, "message_id": 901, "job_id": 4401})
    monkeypatch.setattr(video, "send_as_user", send)
    return box


def test_held_request_runs_as_a_studio_turn_while_on(held_world):
    from video_services import held_request
    out = held_request.resume(NEW_PLUS, "transaction.paid")
    assert out["resumed"] is True and out["job_id"] == 4401
    assert held_world["sent"] == [(NEW_PLUS, 800, "replay-77")]


@pytest.mark.parametrize("state", ["off", "auto_budget", "auto_billing"])
def test_held_request_goes_to_the_mcp_hand_off_while_off(held_world, state):
    from video_services import held_request
    held_world["world"] = World(**STATES[state])
    out = held_request.resume(NEW_PLUS, "transaction.paid")
    assert out == {"project_id": 800, "plans_id": 77, "resumed": False,
                   "reason": "mcp_only"}
    assert held_world["sent"] == []
    # ...and the customer's own assistant is the one shown the request
    assert held_request.studio_lane_open(held_world["world"], NEW_PLUS) is False


def test_held_request_lane_for_a_grandfathered_plus_is_always_mcp(held_world):
    from video_services import held_request
    for state in STATES.values():
        held_world["world"] = World(**state)
        assert held_request.studio_lane_open(held_world["world"],
                                             GRANDFATHERED) is False


def test_held_request_the_assistant_already_made_never_runs_after_on(
        held_world):
    """D1V: the payment landed while Studio in Plus was off (handed to the
    assistant), the assistant made it, then the owner switched ON: neither
    the Studio's replay nor a webhook retry runs it a second time."""
    from video_services import held_request
    held_world["world"] = World(**ON)
    held_world["world"].mcp_work.add(NEW_PLUS)
    out = held_request.resume(NEW_PLUS, "studio")
    assert out == {"project_id": 800, "plans_id": 77, "resumed": False,
                   "reason": "assistant_took_over"}
    assert held_world["sent"] == []


@pytest.mark.parametrize("state", sorted(STATES))
def test_the_assistant_is_shown_a_plus_request_in_every_state(held_world,
                                                              state):
    """D1V: the hand-off follows the plan, not the switch at this instant,
    so a lane that opens after the payment never drops the request; a Pro
    subscriber's request is the Studio's, as before."""
    from video_services import held_request
    world = World(**STATES[state])
    held_world["world"] = world
    for uid in (NEW_PLUS, GRANDFATHERED, NEW_MCP):
        note = held_request.for_assistant(world, uid)
        assert note and note["text"].startswith("WAITING REQUEST"), uid
    assert held_request.for_assistant(world, NEW_PRO) is None
    world.mcp_work.add(NEW_PLUS)
    assert held_request.for_assistant(world, NEW_PLUS) is None


# --------------------------------------------------- the automatic First cut

def test_an_mcp_only_plan_never_gets_an_automatic_first_cut():
    import free_trial
    from test_free_trial import DB, _fresh, _on
    every = dict(trial_requests=True, payer_first_cut=True,
                 model="claude-haiku-5-5", per_account_usd_cap=0.12)
    studio_on = json.dumps({"on": True, "cutoff_at": CUTOFF.isoformat()})
    studio_off = json.dumps({"on": False, "cutoff_at": CUTOFF.isoformat()})
    credit = json.dumps({"state": "on"})

    def db(plan, studio, credit_state=credit, cycle_usd=0.0):
        d = DB(stored=_on(**every), studio=studio, paid_before=False,
               credit_state=credit_state, cycle_usd=cycle_usd)
        d.user = dict(d.user, plan=plan)
        return d
    _fresh()
    got = free_trial.admit_upload(db("plus_2026", studio_on), 901, NEW_PLUS,
                                  subscribed=True)
    assert got and got["kind"] == "payer"
    _fresh()
    assert free_trial.admit_upload(db("plus_2026", studio_off), 902,
                                   NEW_PLUS, subscribed=True) is None
    _fresh()
    assert free_trial.admit_upload(db("mcp_connect", studio_on), 903,
                                   NEW_MCP, subscribed=True) is None
    _fresh()
    got = free_trial.admit_upload(db("pro_2026", studio_on), 904, NEW_PRO,
                                  subscribed=True)
    assert got and got["kind"] == "payer"


def test_both_copies_of_claude_plans_are_identical():
    worker = ROOT.parent / "worker" / "claude_plans.py"
    assert worker.read_bytes() == (ROOT / "claude_plans.py").read_bytes()
