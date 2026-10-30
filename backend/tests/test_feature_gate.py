"""Shorts and Website demos come with Pro and Max, in the Studio.

Owner rule (30 Oct 2026, verbatim): "clicking on shorts or website demo in
trial or plus must show an upgrade card no podcasts or recordings for those
plans excluding the mcp".

Proved here (feature_gate.py + every backend Studio surface):
  * WHO: free-trial accounts while the trial runs for them, and NEW Plus
    subscribers who have the Studio through D1; never grandfathered
    subscribers, Pro, Max, MCP Connect, the admin or the excluded account;
  * WHAT: the SHORTS mode, Make shorts, the short editors, a typed shorts or
    website request, the trial's URL First cut and a held website request are
    answered with the same upgrade card (200, code feature_upgrade), never
    an error, never a job, never a charge;
  * the card's plans and prices are the server's own offer (Pro and Max:
    every offered plan that is not MCP-only), never hardcoded;
  * MCP routes never read the gate.
"""
import contextlib
import importlib.util
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import credits  # noqa: E402
import feature_gate  # noqa: E402
import free_trial  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "d1_world", ROOT / "tests" / "test_studio_plus_switch.py")
_d1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_d1)
NOW, CUTOFF = _d1.NOW, _d1.CUTOFF

TRIAL = 40           # never paid, free
GRANDFATHERED = _d1.GRANDFATHERED
NEW_PLUS = _d1.NEW_PLUS
NEW_PRO = _d1.NEW_PRO
NEW_MCP = _d1.NEW_MCP
NEW_MAX = 11
OLD_PRO = 12         # grandfathered Pro
ADMIN = 13
EXCLUDED = 60        # the owner's agent account
INTERNAL = 29


class World(_d1.World):
    """D1's rows (users, payments, app_kv, the Claude ledger) plus the
    gate's own user read."""

    def __init__(self, trial=None, **kw):
        super().__init__(**kw)
        if trial is not None:
            self.kv["free_result"] = json.dumps(trial)
        self.users.update({
            TRIAL: {"plan": "free", "is_subscribed": 0},
            NEW_MAX: {"plan": "max_2026", "is_subscribed": 1, "sub": "s11"},
            OLD_PRO: {"plan": "pro_2026", "is_subscribed": 1, "sub": "s12"},
            ADMIN: {"plan": "free", "is_subscribed": 0,
                    "email": "thevalmera@gmail.com"},
            EXCLUDED: {"plan": "free", "is_subscribed": 0},
            INTERNAL: {"plan": "free", "is_subscribed": 0},
        })
        self.payments += [(NEW_MAX, CUTOFF + timedelta(hours=5), 10000),
                          (OLD_PRO, CUTOFF - timedelta(days=3), 5000)]

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        if q.startswith("SELECT is_subscribed, plan, email FROM users"):
            u = self.users.get(int(params[0]))
            self._row = None if u is None else {
                "is_subscribed": u.get("is_subscribed"),
                "plan": u.get("plan"),
                "email": u.get("email") or f"user{params[0]}@example.com"}
            return
        return super().execute(sql, params)


TRIAL_ON = {"on": True, "test_user_ids": []}
TRIAL_OFF = {"on": False, "test_user_ids": []}
STUDIO_ON = _d1._studio()
STUDIO_OFF = _d1._studio(on=False)


def why(uid, **kw):
    return feature_gate.reason(World(**kw), uid, now=NOW)


# ------------------------------------------------------------------ who

def test_trial_accounts_are_gated_while_the_trial_runs_for_them():
    assert why(TRIAL, trial=TRIAL_ON) == "trial"
    # trial off: today's subscribe wall, which the gate never moves
    assert why(TRIAL, trial=TRIAL_OFF) is None
    assert why(TRIAL) is None
    # an internal test account is on while the flag is off (the smoke)
    assert why(TRIAL, trial=dict(TRIAL_OFF, test_user_ids=[TRIAL])) == "trial"
    # a rollout time still ahead: not yet, except internal accounts
    later = dict(TRIAL_ON, start_at=(NOW + timedelta(days=1)).isoformat())
    assert why(TRIAL, trial=later) is None
    assert why(INTERNAL, trial=later) == "trial"
    # the trial's own exclusions and the admin never are
    assert why(EXCLUDED, trial=dict(TRIAL_ON, test_user_ids=[EXCLUDED])) is None
    assert why(ADMIN, trial=TRIAL_ON) is None


@pytest.mark.parametrize("state", ["auto_budget", "auto_billing", "no_ledger"])
def test_the_trial_gate_follows_the_credit_line(state):
    """AUTO-OFF: the trial is closed, so a free account meets today's wall."""
    kw = dict(_d1.STATES[state])
    kw.pop("studio", None)
    assert why(TRIAL, trial=TRIAL_ON, **kw) is None


def test_new_plus_subscribers_with_the_studio_are_gated():
    assert why(NEW_PLUS, studio=STUDIO_ON) == "plus"
    # grandfathered Plus, Pro, Max, MCP Connect: never
    for uid in (GRANDFATHERED, NEW_PRO, NEW_MAX, OLD_PRO, NEW_MCP):
        for studio in (STUDIO_ON, STUDIO_OFF):
            assert why(uid, studio=studio, trial=TRIAL_ON) is None, uid
    # Studio in Plus off or AUTO-OFF: Plus is MCP-only again and the Studio's
    # own mcp_only panel answers first; the gate itself stays out of it
    assert why(NEW_PLUS, studio=STUDIO_OFF) is None
    assert why(NEW_PLUS, **_d1.AUTO_BUDGET) is None


def test_any_doubt_reads_as_not_gated():
    class Broken(World):
        def execute(self, sql, params=None):
            if "FROM users" in sql:
                raise RuntimeError("db down")
            return super().execute(sql, params)
    assert feature_gate.safe_reason(Broken(trial=TRIAL_ON), TRIAL) is None
    assert feature_gate.reason(World(trial=TRIAL_ON), None) is None
    assert feature_gate.reason(World(trial=TRIAL_ON), 99999) is None


def test_the_module_is_byte_identical_and_agrees_with_its_sources():
    assert (ROOT / "feature_gate.py").read_bytes() == \
        (ROOT.parent / "worker" / "feature_gate.py").read_bytes()
    from routes import video
    assert feature_gate.MCP_ONLY_PLANS == video.MCP_ONLY_PLANS
    assert set(feature_gate.TRIAL_INTERNAL_IDS) == \
        set(free_trial.DEFAULTS["internal_user_ids"])
    assert set(feature_gate.TRIAL_EXCLUDED_IDS) == \
        set(free_trial.DEFAULTS["excluded_user_ids"])
    assert feature_gate.ADMIN_EMAIL == "thevalmera@gmail.com"
    assert set(feature_gate.TOOL_FEATURES) == {
        "make_shorts", "record_website", "record_website_demo",
        "showcase_demo"}


# ------------------------------------------------------------- requests

@pytest.mark.parametrize("text,feature", [
    ("Record my website https://valmera.io/ as a 20-second vertical (9:16) "
     "demo: scroll the page from top to bottom at a steady pace, then make "
     "it a polished showcase video.", "website_demo"),
    ("record my website", "website_demo"),
    ("a demo of my app at www.example.com", "website_demo"),
    ("make 5 shorts from this podcast", "shorts"),
    ("turn this podcast into shorts", "shorts"),
    ("cut this interview into reels", "shorts"),
    ("give me some shorts", "shorts"),
    ("Create 3 TikToks out of this", "shorts"),
    ("make the captions bigger", None),
    ("add 3 b-roll clips", None),
    ("remove the clips where I stutter", None),
    ("make it a 30 second short", None),
    ("make this a vertical short with captions", None),
    ("edit it like the reels I love", None),
    ("https://youtube.com/watch?v=abc please edit this", None),
    ("put my website https://foo.com on the end card", None),
    ("scroll text at the end", None),
    ("", None),
])
def test_which_typed_requests_are_plainly_gated(text, feature):
    assert feature_gate.request_feature(text) == feature


def test_a_video_link_is_footage_not_a_website():
    assert feature_gate.website_link("https://youtu.be/abc") is None
    assert feature_gate.website_link("https://cdn.x.com/a.mp4") is None
    assert feature_gate.website_link("see https://acme.io.") == "https://acme.io"


# ----------------------------------------------------------------- card

def test_the_card_offers_the_servers_plans_that_include_the_features():
    offer = credits.subscribe_offer_body(World())
    card = feature_gate.card("shorts", "trial", offer)
    assert card["code"] == "feature_upgrade" and card["upgrade_required"]
    assert card["plan_ids"] == ["pro_2026", "max_2026"]
    by_id = {p["id"]: p for p in offer["plans"]}
    assert card["subscription_offer"]["plans"] == [by_id["pro_2026"],
                                                   by_id["max_2026"]]
    assert card["title"] == "Shorts and Website demos come with Pro and Max."
    assert "shorts" in card["text"] and "free trial" in card["text"]
    assert set(card["texts"]) == {"shorts", "website_demo"}
    plus = feature_gate.card("website_demo", "plus", offer)
    assert plus["text"].startswith("Upgrade to record your website")
    assert "Plus keeps editing your own footage" in plus["text"]


def test_the_card_follows_the_offer_never_a_hardcoded_plan():
    offer = {"plans": [
        {"id": "plus_2026", "name": "Plus", "monthly": 15},
        {"id": "pro_2026", "name": "Pro", "monthly": 70},
        {"id": "max_2026", "name": "Max", "monthly": 140, "sold_out": True}]}
    card = feature_gate.card(None, "trial", offer)
    assert card["plan_ids"] == ["pro_2026"]
    assert card["subscription_offer"]["plans"][0]["monthly"] == 70
    assert card["title"] == "Shorts and Website demos come with Pro."
    # the held catalogue (MCP Connect + Advanced Editor): Advanced only
    held = {"plans": [{"id": "mcp_connect", "name": "MCP Connect"},
                      {"id": "advanced", "name": "Advanced Editor"}]}
    assert feature_gate.card("shorts", "plus", held)["plan_ids"] == ["advanced"]
    # no offer: names no plan
    bare = feature_gate.card("shorts", "trial", None)
    assert bare["plan_ids"] == [] and "subscription_offer" not in bare
    assert bare["title"].endswith("come with the plans below.")
    src = (ROOT / "feature_gate.py").read_text()
    assert "pro_2026" not in src and "max_2026" not in src
    assert '"Pro"' not in src and "$" not in src


# --------------------------------------------------------------- routes

class Cursor:
    """Answers statements by their opening words; records writes."""

    def __init__(self):
        self.sql, self.inserts, self.updates = [], [], []
        self._one, self.next_id = None, 500

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.sql.append(q)
        self._one = None
        if q.startswith("SELECT COUNT(*) AS n FROM chat_messages"):
            self._one = {"n": 0}
        elif q.startswith("INSERT INTO chat_messages"):
            self.next_id += 1
            meta = params[-1].adapted if hasattr(params[-1], "adapted") \
                else params[-1]
            role = "user" if "'user'" in q else "assistant"
            self.inserts.append({"id": self.next_id, "role": role,
                                 "content": params[1], "meta": meta})
            self._one = {"id": self.next_id}
        elif q.startswith("UPDATE"):
            self.updates.append((q, params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []


@pytest.fixture
def routes(monkeypatch):
    from routes import video
    cursor = Cursor()
    state = {"why": "trial", "gated": True, "enqueued": [], "events": [],
             "reserved": [], "concierge": []}

    @contextlib.contextmanager
    def fake_vdb(db_url=None):
        yield type("C", (), {"cursor": lambda self: cursor,
                             "commit": lambda self: None,
                             "rollback": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user", lambda *_: {
        "id": 9, "chat_session_id": 3, "kind": "edit", "meta": {}})
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video, "_feature_gate_reason",
                        lambda cur, uid: state["why"])
    monkeypatch.setattr(video, "_subscribe_gate_applies",
                        lambda *_: state["gated"])
    monkeypatch.setattr(video, "_latest_edl", lambda *_: {"version": 4})
    monkeypatch.setattr(video, "_subscribe_offer_body",
                        lambda: credits.subscribe_offer_body(World()))
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: state["events"].append(
                            (a[2], (a[4] if len(a) > 4 else k.get("detail")))))
    monkeypatch.setattr(video, "check_and_reserve",
                        lambda *a, **k: state["reserved"].append(a) or True)
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        state["enqueued"].append((t, payload)) or 777)
    monkeypatch.setattr(video, "_trial_url_direct",
                        lambda *a, **k: state["enqueued"].append(
                            ("agent_turn", {"first_cut": True})) or 778)
    monkeypatch.setattr(video.threading, "Thread", lambda *a, **k: type(
        "T", (), {"start": lambda self: state["concierge"].append(k)})())
    state["cursor"] = cursor
    state["video"] = video
    return state


def _call(view, body=None, **kw):
    app = Flask(__name__)
    with app.test_request_context(json=body or {}):
        out = view.__wrapped__(**kw)
    response, status = out if isinstance(out, tuple) else (out, 200)
    return response.get_json(), status


def _assert_card(body, status, feature):
    assert status == 200
    assert body["code"] == "feature_upgrade" and body["feature_upgrade"]
    assert body["queued"] is False and body["ok"] is False
    card = body["upgrade"]
    assert card["feature"] == feature
    assert card["plan_ids"] == ["pro_2026", "max_2026"]
    assert "error" not in body


@pytest.mark.parametrize("why_", ["trial", "plus"])
@pytest.mark.parametrize("text,feature", [
    ("make 5 shorts from this podcast", "shorts"),
    ("Record my website https://acme.io as a 20-second vertical (9:16) "
     "demo: scroll the page from top to bottom at a steady pace, then make "
     "it a polished showcase video.", "website_demo"),
])
def test_a_typed_gated_request_gets_the_card_and_nothing_runs(
        routes, monkeypatch, why_, text, feature):
    v = routes["video"]
    routes["why"] = why_
    routes["gated"] = why_ == "trial"
    monkeypatch.setattr(v, "_active_original", lambda *_: {"id": 41,
                                                           "sha256": "s"})
    monkeypatch.setattr(v, "_index_row", lambda *_: {"id": 1})
    body, status = _call(v.post_message, {"text": text, "client_msg_id": "c1"},
                         user_id=8, project_id=9)
    _assert_card(body, status, feature)
    assert routes["enqueued"] == [] and routes["concierge"] == []
    assert routes["reserved"] == []                    # no credit touched
    user, card = routes["cursor"].inserts
    assert user["role"] == "user" and user["content"] == text
    assert user["meta"]["client_msg_id"] == "c1"
    assert card["role"] == "assistant"
    assert card["meta"]["kind"] == "feature_upgrade"
    assert card["meta"]["request_message_id"] == user["id"]
    assert card["meta"]["edl_version"] == 4
    assert card["meta"]["outcome"] == "upgrade_required"
    assert card["meta"]["subscription_offer"]["plans"][0]["id"] == "pro_2026"
    assert card["content"].startswith("Shorts and Website demos come with Pro "
                                      "and Max.")
    assert routes["events"][-1][0] == "feature_upgrade_shown"
    assert body["message_id"] == user["id"]


def test_the_same_request_from_an_ungated_account_runs_as_before(routes,
                                                                monkeypatch):
    v = routes["video"]
    routes["why"], routes["gated"] = None, False
    monkeypatch.setattr(v, "_active_original", lambda *_: {"id": 41,
                                                           "sha256": "s"})
    monkeypatch.setattr(v, "_index_row", lambda *_: {"id": 1})
    monkeypatch.setattr(v, "_edl_at", lambda *_: None, raising=False)
    monkeypatch.setattr(v.plan_gate, "needs_plan", lambda *a: False)
    monkeypatch.setattr(v, "_running_jobs_count", lambda *_: 0)
    monkeypatch.setattr(v, "_queue_depth_notice", lambda *a: None)
    monkeypatch.setattr(v.os, "getenv", lambda k, d=None: "x"
                        if k == "OPENAI_API_KEY" else os.environ.get(k, d))
    body, status = _call(v.post_message,
                         {"text": "make 5 shorts from this podcast"},
                         user_id=8, project_id=9)
    assert status == 200 and body.get("code") != "feature_upgrade"
    assert [t for t, _ in routes["enqueued"]] == ["agent_turn"]


def test_a_url_pasted_by_a_trial_account_gets_the_card_not_a_recording(
        routes, monkeypatch):
    v = routes["video"]
    monkeypatch.setattr(v, "_active_original", lambda *_: None)
    monkeypatch.setattr(v, "_url_fetch_enabled", lambda: False)
    body, status = _call(v.post_message, {"text": "https://acme.io"},
                         user_id=8, project_id=9)
    _assert_card(body, status, "website_demo")
    # neither the URL First cut nor the concierge ran
    assert routes["enqueued"] == [] and routes["concierge"] == []
    card = routes["cursor"].inserts[-1]
    assert card["meta"]["kind"] == "feature_upgrade"
    assert card["meta"]["reason"] == "trial"


def test_a_url_from_a_free_account_with_the_trial_off_is_todays_path(
        routes, monkeypatch):
    v = routes["video"]
    routes["why"] = None
    monkeypatch.setattr(v, "_active_original", lambda *_: None)
    monkeypatch.setattr(v, "_url_fetch_enabled", lambda: False)
    body, status = _call(v.post_message, {"text": "https://acme.io"},
                         user_id=8, project_id=9)
    assert body.get("code") != "feature_upgrade"
    # today's order: the trial's URL First cut is tried (it admits nobody
    # with the trial off), else the concierge answers
    assert routes["enqueued"] == [("agent_turn", {"first_cut": True})]


def test_the_trials_url_first_cut_never_records_for_a_gated_account(
        routes, monkeypatch):
    v = routes["video"]
    admitted = []
    monkeypatch.setattr(free_trial, "admit_upload",
                        lambda *a, **k: admitted.append(a) or {"kind": "trial"})
    monkeypatch.setattr(v, "_message_text", lambda *_: "https://acme.io")
    cur = routes["cursor"]
    assert v._trial_url_first_cut(cur, 8, 9, 12) is None
    assert admitted == [] and routes["enqueued"] == []
    routes["why"] = None
    assert v._trial_url_first_cut(cur, 8, 9, 12) == 777
    assert routes["enqueued"][-1][1]["url"] == "https://acme.io"


@pytest.mark.parametrize("why_", ["trial", "plus"])
def test_the_shorts_mode_stays_an_edit_with_the_card(routes, why_):
    v = routes["video"]
    routes["why"] = why_
    body, status = _call(v.set_project_mode, {"kind": "shorts"},
                         user_id=8, project_id=9)
    _assert_card(body, status, "shorts")
    assert body["kind"] == "edit"
    assert not any("UPDATE projects SET kind" in q
                   for q in routes["cursor"].sql)
    # back to a normal edit is always allowed
    body, status = _call(v.set_project_mode, {"kind": "edit"},
                         user_id=8, project_id=9)
    assert status == 200 and body == {"ok": True, "kind": "edit"}


def test_the_shorts_mode_for_pro_max_and_grandfathered_is_unchanged(routes):
    v = routes["video"]
    routes["why"] = None
    body, status = _call(v.set_project_mode, {"kind": "shorts"},
                         user_id=8, project_id=9)
    assert status == 200 and body == {"ok": True, "kind": "shorts"}
    assert any("UPDATE projects SET kind" in q for q in routes["cursor"].sql)


@pytest.mark.parametrize("why_", ["trial", "plus"])
def test_make_shorts_on_the_board_gets_the_card(routes, monkeypatch, why_):
    v = routes["video"]
    routes["why"] = why_
    monkeypatch.setattr(v, "_active_original", lambda *_: {
        "id": 41, "sha256": "s", "duration_s": 3600})
    monkeypatch.setattr(v, "_index_row", lambda *_: {
        "json": {"video": {"duration": 3600.0}}})
    body, status = _call(v.start_shorts, {}, user_id=8, project_id=9)
    _assert_card(body, status, "shorts")
    assert routes["enqueued"] == [] and routes["reserved"] == []


def test_make_shorts_for_a_paying_studio_plan_still_runs(routes, monkeypatch):
    v = routes["video"]
    routes["why"], routes["gated"] = None, False
    monkeypatch.setattr(v, "_active_original", lambda *_: {
        "id": 41, "sha256": "s", "duration_s": 3600})
    monkeypatch.setattr(v, "_index_row", lambda *_: {
        "json": {"video": {"duration": 3600.0}}})
    monkeypatch.setattr(v.plan_gate, "needs_plan", lambda *a: False)
    monkeypatch.setattr(v, "_started_jobs_count", lambda *_: 0)
    body, status = _call(v.start_shorts, {}, user_id=8, project_id=9)
    assert status == 200 and body == {"job_id": 777}
    assert routes["enqueued"][0][0] == "shorts_plan"


def test_a_short_editor_gets_the_card(routes):
    v = routes["video"]
    routes["why"] = "plus"
    body, status = _call(v.start_short_editor, {}, user_id=8, project_id=9,
                         child_project_id=10)
    _assert_card(body, status, "shorts")
    assert routes["enqueued"] == []


def test_a_held_website_request_is_answered_with_the_card(routes, monkeypatch):
    """Ticket #11's resume (a new Plus buyer whose website request the wall
    held) and the Studio's /replay-blocked: the card, once, no turn."""
    v = routes["video"]
    routes["why"] = "plus"
    monkeypatch.setattr(v, "_message_text",
                        lambda *_: "record my website https://acme.io")
    cur = routes["cursor"]
    card = v._held_feature_card(cur, 8, 3, 55, {"message_id": 12}, "webhook")
    assert card["feature"] == "website_demo" and card["reason"] == "plus"
    assert cur.inserts[-1]["meta"]["request_message_id"] == 12
    stamp = cur.updates[-1][1][0].adapted
    assert stamp["replay_refused"] == "feature_upgrade"
    assert stamp["replay_origin"] == "webhook" and stamp["replayed_at"]
    # a non-feature held request, or an ungated account: runs as before
    monkeypatch.setattr(v, "_message_text", lambda *_: "make an intro card")
    assert v._held_feature_card(cur, 8, 3, 55, {"message_id": 12}, "x") is None
    monkeypatch.setattr(v, "_message_text",
                        lambda *_: "record my website https://acme.io")
    routes["why"] = None
    assert v._held_feature_card(cur, 8, 3, 55, {"message_id": 12}, "x") is None
    # both resume paths call it before enqueueing
    from video_services import held_request
    import inspect
    resume = inspect.getsource(held_request.resume)
    assert resume.index("_held_feature_card") < resume.index('"agent_turn"')
    assert 'body.get("feature_upgrade")' in resume
    replay = inspect.getsource(v.replay_blocked)
    blank = replay[replay.index('meta.get("surface") == "blank_canvas"'):]
    assert blank.index("_held_feature_card") < blank.index("_enqueue(")


def test_the_balance_carries_the_card_only_for_gated_accounts(monkeypatch):
    from routes import auth
    w = World(trial=TRIAL_ON, studio=STUDIO_ON)
    card = auth._feature_upgrade_card(w, TRIAL)
    assert card["reason"] == "trial" and card["feature"] is None
    assert card["plan_ids"] == ["pro_2026", "max_2026"]
    assert auth._feature_upgrade_card(w, NEW_PLUS)["reason"] == "plus"
    for uid in (GRANDFATHERED, NEW_PRO, NEW_MAX, OLD_PRO, NEW_MCP, ADMIN,
                EXCLUDED):
        assert auth._feature_upgrade_card(w, uid) is None
    src = (ROOT / "routes" / "auth.py").read_text()
    route = src[src.index("def get_user_credits"):]
    route = route[:route.index("def _feature_upgrade_card")]
    assert 'info["feature_upgrade"] = card' in route


def test_mcp_never_reads_the_gate():
    src = (ROOT / "routes" / "mcp.py").read_text()
    assert "feature_gate" not in src and "_feature_gate_reason" not in src
