"""Ticket #11 (30 Oct 2026): the request the subscribe wall held runs once the
payment is confirmed, exactly once, whichever arrives first: Paddle's webhook
or the Studio coming back.

These tests guard the wiring with fakes (the statements themselves run on
real PostgreSQL in test_held_request_postgres.py and end to end in
.valmera/week-1029/round3/t11/e2e_held_request.py):
  * the webhook resumes after real money only, off the request thread;
  * a blank-canvas request is enqueued once under the project lock;
  * a typed request goes through the ordinary send path with the Studio's
    own replay id, so the two can never both insert it;
  * MCP-only plans never get a Studio turn; their assistant is told;
  * the Studio that comes back after the webhook is told it is starting.
"""
import contextlib
import os
import sys
import threading

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify  # noqa: E402

from routes import mcp, video  # noqa: E402
from routes import paddle_webhook as webhook  # noqa: E402
from video_services import held_request  # noqa: E402

WALL = {"id": 154609, "session_id": 3, "project_id": 5249,
        "created_at": None,
        "meta": {"kind": "subscription_plans", "surface": "blank_canvas",
                 "blocked_prompt": {"message_id": 154608,
                                    "text": "Record my website as a demo"}}}
TYPED = {"id": 55, "session_id": 3, "project_id": 9, "created_at": None,
         "meta": {"kind": "subscription_plans", "surface": "chat",
                  "blocked_prompt": {"text": "Cut the pauses",
                                     "attachments": [3]}}}


class Cur:
    """The few statements resume() sends itself, by their opening words."""

    def __init__(self, agent_live=False, taken=False):
        self.agent_live, self.taken = agent_live, taken
        self.sql, self.marks = [], []
        self._one = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self._one = None
        if flat.startswith("SELECT pg_advisory_xact_lock"):
            pass
        elif flat.startswith("SELECT 1 FROM chat_messages WHERE id"):
            self._one = None if self.taken else {"?": 1}
        elif "type = 'agent_turn'" in flat:
            self._one = {"?": 1} if self.agent_live else None
        elif flat.startswith("UPDATE chat_messages"):
            patch = params[0].adapted
            if self.taken:
                return
            self.marks.append((params[1], patch))
            self.taken = True
            self._one = {"id": params[1]}
        else:
            raise AssertionError(flat)

    def fetchone(self):
        return self._one


@pytest.fixture
def world(monkeypatch):
    """resume()'s collaborators in routes.video, recorded."""
    state = {"cur": Cur(), "held": WALL, "gated": False, "studio": True,
             "credits": True, "running": 0, "enqueued": [], "events": [],
             "sent": [], "send_answer": None}

    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("C", (), {"cursor": lambda self: state["cur"],
                             "commit": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_subscribe_gate_applies",
                        lambda *_: state["gated"])
    monkeypatch.setattr(held_request, "pending",
                        lambda cur, uid, window_h=24, project_id=None:
                        state["held"])
    monkeypatch.setattr(held_request, "studio_lane_open",
                        lambda *_: state["studio"])
    monkeypatch.setattr(video, "_running_jobs_count",
                        lambda *_: state["running"])
    monkeypatch.setattr(video, "check_and_reserve",
                        lambda *a, **k: state["credits"])
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        state["enqueued"].append((p, t, payload)) or 777)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: state["events"].append((a, k)))

    def send(uid, pid, data):
        state["sent"].append((uid, pid, data))
        return state["send_answer"] or (
            jsonify({"queued": True, "message_id": 901, "job_id": 778}), 200)
    monkeypatch.setattr(video, "send_as_user", send)
    app = Flask(__name__)
    with app.app_context():
        yield state


# ── a blank-canvas / website request (already a chat message) ──────────────

def test_a_held_website_request_runs_once_as_an_agent_turn(world):
    out = held_request.resume(2647, "transaction.completed")
    assert out["resumed"] is True and out["job_id"] == 777
    assert out["message_id"] == 154608 and out["project_id"] == 5249
    assert world["enqueued"] == [(5249, "agent_turn", {
        "message_id": 154608, "replayed_blocked": True,
        "replay_origin": "transaction.completed"})]
    (plans_id, patch), = world["cur"].marks
    assert plans_id == 154609 and patch["replay_job_id"] == 777
    assert patch["replay_origin"] == "transaction.completed"
    # The lock comes before the re-check and the enqueue.
    sql = world["cur"].sql
    assert sql[0].startswith("SELECT pg_advisory_xact_lock")
    assert world["events"][0][0][2] == "blocked_prompt_replayed"
    # Paddle's second event (or the Studio) finds it taken: no second turn.
    again = held_request.resume(2647, "transaction.paid")
    assert again == {"project_id": 5249, "plans_id": 154609,
                     "resumed": False, "reason": "already_resumed"}
    assert len(world["enqueued"]) == 1


@pytest.mark.parametrize("change,reason", [
    ({"gated": True}, "subscription_required"),
    ({"held": None}, "nothing_held"),
    ({"studio": False}, "mcp_only"),
    ({"running": 3}, "busy_capacity"),
    ({"credits": False}, "insufficient_credits"),
])
def test_what_stops_a_resume_leaves_the_request_waiting(world, change, reason):
    world.update(change)
    out = held_request.resume(2647, "transaction.completed")
    assert out["resumed"] is False and out["reason"] == reason
    assert world["enqueued"] == [] and world["sent"] == []
    assert world["cur"].marks == []


def test_a_live_agent_turn_waits_instead_of_racing_it(world):
    world["cur"] = Cur(agent_live=True)
    out = held_request.resume(2647, "transaction.completed")
    assert out["reason"] == "busy" and world["enqueued"] == []


def test_resume_never_raises(world, monkeypatch):
    def boom(*_a):
        raise RuntimeError("db down")
    monkeypatch.setattr(video, "_subscribe_gate_applies", boom)
    assert held_request.resume(1, "transaction.completed") == {
        "resumed": False, "reason": "error"}


# ── a typed request (the wall returned it to the composer) ─────────────────

def test_a_typed_request_goes_through_the_ordinary_send_path(world):
    world["held"] = TYPED
    out = held_request.resume(8, "transaction.completed")
    assert world["sent"] == [(8, 9, {"text": "Cut the pauses",
                                     "attachments": [3],
                                     "client_msg_id": "replay-55"})]
    assert out["resumed"] is True and out["reason"] == "sent"
    assert out["message_id"] == 901 and out["job_id"] == 778
    (plans_id, patch), = world["cur"].marks
    assert plans_id == 55 and patch["replay_message_id"] == 901


def test_the_studios_own_replay_and_the_webhook_insert_it_once(world):
    # The Studio sent replay-55 first: the send path answers duplicate.
    world["held"] = TYPED
    world["send_answer"] = (jsonify({"queued": True, "message_id": 901,
                                     "duplicate": True}), 200)
    out = held_request.resume(8, "transaction.completed")
    assert out["resumed"] is False and out["reason"] == "already_resumed"
    assert [e for e in world["events"]] == []


def test_a_refused_send_is_not_stamped_so_the_studio_can_still_run_it(world):
    world["held"] = TYPED
    world["send_answer"] = (jsonify({"code": "busy_capacity"}), 429)
    out = held_request.resume(8, "transaction.completed")
    assert out == {"project_id": 9, "plans_id": 55, "resumed": False,
                   "reason": "busy_capacity"}
    assert world["cur"].marks == []


def test_a_walled_upload_waits_for_its_analysis(world):
    world["held"] = TYPED
    world["send_answer"] = (jsonify({"queued": True, "message_id": 902,
                                     "waiting_for_index": True}), 200)
    out = held_request.resume(8, "transaction.completed")
    assert out["resumed"] is True and out["reason"] == "waiting_for_index"


# ── send_as_user IS the ordinary send path ─────────────────────────────────

def _send_path(monkeypatch, gated):
    enqueued = []

    class C:
        def __init__(self):
            self._one = None

        def execute(self, sql, params=None):
            flat = " ".join(sql.split())
            if flat.startswith("SELECT COUNT(*) AS n FROM chat_messages"):
                self._one = {"n": 0}
            elif flat.startswith("INSERT INTO chat_messages"):
                self._one = {"id": 901}
            else:
                self._one = None

        def fetchone(self):
            return self._one

        def fetchall(self):
            return []
    cur = C()

    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("Conn", (), {"cursor": lambda self: cur,
                                "commit": lambda self: None,
                                "rollback": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 9, "chat_session_id": 3})
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video, "_active_original",
                        lambda *_: {"id": 41, "sha256": "s",
                                    "meta": {}, "kind": "original"})
    monkeypatch.setattr(video, "_media_purged", lambda *_: None)
    monkeypatch.setattr(video, "_index_row", lambda *_: {"id": 1})
    monkeypatch.setattr(video, "_latest_edl", lambda *_: {"version": 2,
                                                          "json": {}})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: gated)
    monkeypatch.setattr(video.plan_gate, "needs_plan", lambda *_: False)
    monkeypatch.setattr(video, "check_and_reserve", lambda *a, **k: True)
    monkeypatch.setattr(video, "_running_jobs_count", lambda *_: 0)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    monkeypatch.setattr(video, "_record_plans_message",
                        lambda *a, **k: {"id": 55})
    monkeypatch.setattr(video, "_queue_depth_notice", lambda *a, **k: None)
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        enqueued.append((t, payload)) or 778)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = Flask(__name__)
    with app.app_context():        # no request context: the server's send
        out = video.send_as_user(8, 9, {"text": "Cut the pauses",
                                        "client_msg_id": "replay-55"})
    body, status = held_request._body(out)
    return body, status, enqueued


def test_send_as_user_runs_every_gate_of_a_typed_message(monkeypatch):
    body, status, enqueued = _send_path(monkeypatch, gated=False)
    assert status == 200 and body["message_id"] == 901
    assert enqueued == [("agent_turn", {"message_id": 901})]
    # The same function refuses exactly as a typed message would.
    body, status, enqueued = _send_path(monkeypatch, gated=True)
    assert status == 402 and enqueued == []


def test_a_browser_send_still_reads_the_request_body():
    import inspect
    src = inspect.getsource(video.post_message)
    assert "data if data is not None else (request.get_json() or {})" in src
    assert "THE SUBSCRIBE GATE" in src


# ── the Paddle webhook: after real money, off the request thread ───────────

def _webhook(monkeypatch, event_type, amount):
    kicks = []

    class Db:
        def commit(self):
            pass
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _r: True)
    monkeypatch.setattr(webhook, "get_db", lambda: Db())
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *_: 2647)
    monkeypatch.setattr(webhook, "_plan_from_data", lambda _: "pro_2026")
    monkeypatch.setattr(webhook, "_trial_aware_grant",
                        lambda *_, **kw: (1500, 20, False, "paid"))
    monkeypatch.setattr(webhook, "update_user_subscription_status",
                        lambda *a, **k: None)
    monkeypatch.setattr(webhook.billing, "record_transaction",
                        lambda *_a, **_k: {"recorded": True,
                                           "newly_paid": int(amount) > 0,
                                           "amount_cents": int(amount)})
    monkeypatch.setattr(webhook.billing, "record_recovery", lambda *_: None)
    monkeypatch.setattr(webhook.billing, "set_status", lambda *_a, **_k: None)
    monkeypatch.setattr(webhook.trial_state, "record_paid_conversion",
                        lambda *_: None)
    monkeypatch.setattr(webhook.trial_state, "sync_from_subscription",
                        lambda *_: None)
    monkeypatch.setattr(webhook, "_record_new_annual_period", lambda *_: None)
    monkeypatch.setattr(webhook, "_retire_upgrade_source", lambda *_: True)
    monkeypatch.setattr(webhook, "_record_discount_use", lambda *_: None)
    monkeypatch.setattr(webhook.paid_subscription_alert, "enqueue_and_kick",
                        lambda *a: None)
    monkeypatch.setattr(webhook.held_request, "kick_after_payment",
                        lambda app, uid, origin: kicks.append((app, uid,
                                                               origin)))
    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    data = {"id": "txn_1", "subscription_id": "sub_1", "status": "completed",
            "items": [{"price": {"id": "pri_x"}}],
            "details": {"totals": {"grand_total": amount}},
            "billing_period": {"ends_at": "2026-11-30T00:00:00Z"}}
    response = app.test_client().post("/webhook/paddle", json={
        "event_type": event_type, "data": data})
    return response.status_code, kicks, app


@pytest.mark.parametrize("event_type,amount,kicked", [
    ("transaction.completed", "1500", True),
    ("transaction.paid", "1500", True),
    ("transaction.completed", "0", False),        # a $0 trial opening
])
def test_the_webhook_resumes_only_after_real_money(monkeypatch, event_type,
                                                    amount, kicked):
    monkeypatch.setattr(webhook.billing, "transaction_amount",
                        lambda data: (int(amount), "USD"))
    status, kicks, app = _webhook(monkeypatch, event_type, amount)
    assert status == 200
    if kicked:
        (kick_app, uid, origin), = kicks
        assert kick_app is app and uid == 2647 and origin == event_type
    else:
        assert kicks == []


def test_a_subscription_event_alone_never_resumes(monkeypatch):
    status, kicks, _ = _webhook(monkeypatch, "subscription.created", "1500")
    assert status == 200 and kicks == []


def test_the_kick_runs_resume_in_its_own_app_context(monkeypatch):
    seen = []
    from flask import has_app_context
    monkeypatch.setattr(held_request, "resume",
                        lambda uid, origin: seen.append(
                            (uid, origin, has_app_context()))
                        or {"resumed": True})
    app = Flask(__name__)
    t = held_request.kick_after_payment(app, 2647, "transaction.completed")
    assert isinstance(t, threading.Thread)
    t.join(5)
    assert seen == [(2647, "transaction.completed", True)]


# ── the Studio that comes back after the webhook ───────────────────────────

def test_replay_blocked_says_it_is_starting_when_it_already_ran(monkeypatch):
    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("C", (), {"cursor": lambda self: object(),
                             "commit": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 5249, "chat_session_id": 3})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video._held_request, "started", lambda cur, sid: {
        "job_id": 777, "message_id": 154608, "origin": "transaction.completed",
        "state": "queued", "waiting_for_index": False})
    monkeypatch.setattr(video, "_enqueue",
                        lambda *a: pytest.fail("must not enqueue again"))
    app = Flask(__name__)
    with app.test_request_context(json={}):
        body = video.replay_blocked.__wrapped__(user_id=2647,
                                                project_id=5249).get_json()
    assert body["replay"] == "started" and body["job_id"] == 777
    assert body["message_id"] == 154608


# ── MCP-only plans: the customer's own assistant is told ───────────────────

def test_open_project_and_project_state_carry_the_waiting_request(monkeypatch):
    note = {"project_id": 5249, "text": "WAITING REQUEST FROM THE STUDIO: x"}

    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("C", (), {"cursor": lambda self: object()})()
    monkeypatch.setattr(mcp, "vdb", fake_vdb)
    monkeypatch.setattr(mcp, "_project_for_user",
                        lambda *_: {"id": 5249, "title": "Website demo"})
    monkeypatch.setattr(mcp, "_set_active_project", lambda *_: None)
    monkeypatch.setattr(mcp, "_run_tool_job",
                        lambda *a, **k: "CURRENT PROJECT STATE\n...")
    monkeypatch.setattr(mcp.held_request, "for_assistant",
                        lambda cur, uid, pid=None: note)
    tok = {"user_id": 2647}
    opened = mcp._t_open_project(tok, {"project_id": 5249})
    assert opened.index("WAITING REQUEST") < opened.index("CURRENT PROJECT")
    state = mcp._t_project_state(tok, {"project_id": 5249})
    assert state.startswith("WAITING REQUEST FROM THE STUDIO")
    monkeypatch.setattr(mcp.held_request, "for_assistant",
                        lambda cur, uid, pid=None: None)
    assert mcp._t_project_state(tok, {"project_id": 5249}) == \
        "CURRENT PROJECT STATE\n..."


def test_a_failed_lookup_never_breaks_an_mcp_call(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db")
    monkeypatch.setattr(mcp.held_request, "for_assistant", boom)

    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("C", (), {"cursor": lambda self: object()})()
    monkeypatch.setattr(mcp, "vdb", fake_vdb)
    assert mcp._held_for_assistant({"user_id": 1}, 5) is None


def test_the_lane_lookup_never_counts_as_a_refusal(monkeypatch):
    # routes.video._studio_ai_gate records an mcp_only_refused event (the
    # cockpit's wrong-plan signal); a lookup from the webhook, the intake or
    # an MCP call must not add one.
    events = []
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a[2]))

    class Cur:
        def execute(self, sql, params=None):
            assert "SELECT plan, is_subscribed FROM users" in sql

        def fetchone(self):
            return {"plan": "plus_2026", "is_subscribed": 1}
    assert held_request.studio_lane_open(Cur(), 2647) is False
    assert events == []
    with Flask(__name__).app_context():
        assert video._studio_ai_gate(Cur(), 2647) is not None
    assert events == ["mcp_only_refused"]          # the real refusal still is


def test_the_studio_runs_the_held_request_the_webhook_way(monkeypatch):
    @contextlib.contextmanager
    def fake_vdb(*_a):
        yield type("C", (), {"cursor": lambda self: object(),
                             "commit": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 9, "chat_session_id": 3})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video._held_request, "started", lambda *_: None)
    calls = []
    monkeypatch.setattr(video._held_request, "resume",
                        lambda uid, origin, pid: calls.append(
                            (uid, origin, pid)) or {
                            "resumed": True, "reason": "sent",
                            "job_id": 778, "message_id": 901})
    app = Flask(__name__)
    with app.test_request_context(json={}):
        body = video.replay_blocked.__wrapped__(user_id=8,
                                                project_id=9).get_json()
    assert calls == [(8, "studio", 9)]
    assert body == {"replay": "started", "job_id": 778, "message_id": 901,
                    "origin": "studio", "waiting_for_index": False}
