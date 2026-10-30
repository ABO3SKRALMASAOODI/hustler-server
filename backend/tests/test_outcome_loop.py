"""The outcome loop (owner design, 25 Oct 2026): sessions found from the
database, judged by Claude Code sessions, tickets per cause, the admin views
and the digest's outcome section.

No database: rows are built in memory and cursors are scripted fakes. The
discovery SQL was run read-only against production (ops_review.py pending).
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cockpit_alerts as alerts                             # noqa: E402
from ops_loop import EXCLUDED_USER_IDS, sessions, store, views  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
T0 = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)


class Cur:
    """Records statements; answers fetchone/fetchall from a script."""

    def __init__(self, answers=()):
        self.sql, self.answers = [], list(answers)

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def _next(self):
        return self.answers.pop(0) if self.answers else None

    def fetchone(self):
        a = self._next()
        return a[0] if isinstance(a, list) and a else a

    def fetchall(self):
        a = self._next()
        return a if isinstance(a, list) else ([] if a is None else [a])


# ── The owner's rules ────────────────────────────────────────────────────

def test_the_owners_internal_ids_are_excluded():
    for uid in (22, 28, 29, 45, 46, 49, 60, 61, 62, 68, 108, 398, 1058):
        assert uid in EXCLUDED_USER_IDS


def test_no_language_model_is_called_from_the_loop():
    # The judging is done by Claude Code sessions on the owner's Mac.
    for path in (ROOT / "backend" / "ops_loop").glob("*.py"):
        text = path.read_text().lower()
        for banned in ("openai", "anthropic", "chat.completions", "llm_call",
                       "messages.create"):
            assert banned not in text, (path.name, banned)


def test_the_migration_is_additive_lock_bounded_and_complete():
    src = (ROOT / "backend" / "migrations" / "041_outcome_loop.sql").read_text()
    assert "BEGIN;" in src and "COMMIT;" in src
    assert "SET LOCAL lock_timeout" in src
    import re
    statements = [ln.strip().upper() for ln in src.splitlines()
                  if not ln.strip().startswith("--")]
    for ln in statements:
        assert not re.match(r"^(DROP|UPDATE|DELETE|ALTER|TRUNCATE)\b", ln), ln
    for table in ("session_reviews", "tickets", "ticket_reviews",
                  "ops_review_keys"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src
    for col in ("verdict", "score", "reasoning", "root_cause", "evidence",
                "reviewed_at", "severity", "fix_sha", "regression_check",
                "recurrence_count", "closed_at", "request_ids"):
        assert col in src
    assert "'delivered_good', 'delivered_poor'" in src
    assert "'not_delivered'" in src
    assert "tickets_fixed_has_proof" in src
    # The old draft's backend AI judge is gone.
    assert "outcome_verdicts" not in src and "cost_usd" not in src


def test_the_store_writes_only_its_own_tables():
    for ok in ("INSERT INTO session_reviews (x) VALUES (1)",
               "UPDATE tickets SET status = 'open'",
               "INSERT INTO ticket_reviews (a) VALUES (1)",
               "DELETE FROM ops_review_keys WHERE TRUE"):
        assert store.guard(ok) == ok
    for bad in ("UPDATE users SET credits_monthly = 1",
                "INSERT INTO payments (x) VALUES (1)",
                "DELETE FROM chat_messages",
                "UPDATE public.users SET plan = 'max_2026'",
                "INSERT INTO app_kv (key) VALUES ('x')",
                "SELECT 1"):
        with pytest.raises(store.StoreError):
            store.guard(bad)


def test_every_store_write_goes_through_the_guard():
    src = (ROOT / "backend" / "ops_loop" / "store.py").read_text()
    import re
    raw = re.findall(r'cur\.execute\(\s*f?"+\s*(INSERT|UPDATE|DELETE)', src)
    assert raw == [], "a write bypasses store.guard"
    assert len(re.findall(r'_w\(cur,', src)) >= 8


def test_cli_and_runbooks_name_only_the_ops_tables_as_writes():
    cli = (ROOT / "backend" / "scripts" / "ops_review.py").read_text()
    assert "ONLY PRODUCTION WRITE PATH" in cli
    for banned in ("send_email", "brevo_delivery", "import billing",
                   "routes.paddle", "credits_monthly", "UPDATE users"):
        assert banned not in cli


# ── Sessions ─────────────────────────────────────────────────────────────

def _data():
    """One paying user's project 10: an MCP session at 15:00, a paywall-only
    request the next morning, and a second project with a failed index."""
    def m(id, role, at, content="", **kw):
        d = {"id": id, "session_id": 100, "project_id": 10, "role": role,
             "created_at": at, "content": content, "kind": None,
             "error": None, "feedback": None, "tool": None, "source": None,
             "tool_status": None, "args": None, "job": None, "stage": None,
             "subscribe_gate": None, "request_message_id": None,
             "blocked_prompt": None}
        d.update(kw)
        return d

    def j(id, type, start, mins, state="done", project=10, **kw):
        d = {"id": id, "project_id": project, "user_id": 7, "type": type,
             "state": state, "error": None, "attempts": 1,
             "created_at": start, "updated_at": start + timedelta(minutes=mins),
             "message_id": None, "source": None, "quality": None,
             "tool": None, "args": None, "root_agent_job_id": None,
             "render_asset_id": None, "sheet_key": None,
             "verify_sheet_key": None, "caption_sheet_key": None,
             "result_quality": None, "duration_s": None, "outcome": None,
             "status": None, "honesty": None, "is_error": False,
             "tool_status": None, "text": None, "edl_version": None,
             "superseded_by": None, "clips": None, "credits_charged": None}
        d.update(kw)
        return d

    later = T0 + timedelta(hours=18)
    return {
        "projects": {
            10: {"id": 10, "user_id": 7, "kind": "edit", "title": "Stream",
                 "parent_project_id": None, "chat_session_id": 100,
                 "created_at": T0, "root": 10},
            11: {"id": 11, "user_id": 7, "kind": "edit", "title": "Big",
                 "parent_project_id": None, "chat_session_id": 101,
                 "created_at": T0, "root": 11},
        },
        "users": {7: {"id": 7, "created_at": T0 - timedelta(hours=2),
                      "plan": "plus_2026", "billing_plan": "plus_2026",
                      "is_subscribed": 1, "billing_status": "active",
                      "trial_status": None, "paying": True,
                      "real_customer": True,
                      "last_seen_at": T0 + timedelta(hours=1)}},
        "messages": [
            m(1, "activity", T0 + timedelta(minutes=1), "list_assets()",
              tool="list_assets", source="mcp", tool_status="success"),
            m(2, "assistant", T0 + timedelta(minutes=50), "Here is your cut",
              feedback="down"),
            m(3, "user", later, "make it vertical"),
            m(4, "assistant", later + timedelta(seconds=3),
              "You're on the free plan.", kind="subscription_plans",
              subscribe_gate="true", blocked_prompt="make it vertical"),
        ],
        "jobs": [
            j(20, "mcp_tool", T0, 1, tool="keep_segments",
              text="EDL v2 -> v3"),
            j(21, "mcp_tool", T0 + timedelta(minutes=10), 1, tool="look_at",
              is_error=True, text="TRANSIENT_FAILURE: no frame"),
            j(22, "preview", T0 + timedelta(minutes=20), 5,
              render_asset_id="500"),
            j(30, "index", T0 + timedelta(minutes=5), 1, state="failed",
              project=11, error="needs 15.5 GB of scratch, 11.8 GB free"),
        ],
        "events": [
            {"id": 900, "user_id": 7, "project_id": 10,
             "kind": "download_triggered",
             "created_at": T0 + timedelta(minutes=55), "detail": "{}"},
        ],
        "assets": [
            {"id": 500, "project_id": 10, "kind": "render",
             "storage_key": "media/10/22-x.mp4", "bytes": 10, "duration_s": 30,
             "width": 720, "height": 1280, "quality": "draft",
             "variant": "preview", "sheet_key": "media/10/22-x_s.jpg",
             "edl_version": "3", "filename": None, "purged_at": None,
             "created_at": T0 + timedelta(minutes=25)},
            {"id": 400, "project_id": 10, "kind": "proxy",
             "storage_key": "proxies/10/a.mp4", "bytes": 5, "duration_s": 600,
             "width": 404, "height": 720, "quality": None, "variant": None,
             "sheet_key": None, "edl_version": None, "filename": None,
             "purged_at": None, "created_at": T0 - timedelta(minutes=5)},
        ],
        "live": set(), "payments": [], "subscription": [],
    }


def _discover(data, now=NOW, **kw):
    """discover() with load() and candidate_roots() replaced by `data`."""
    orig_load, orig_roots = sessions.load, sessions.candidate_roots
    sessions.load = lambda cur, roots, since, now: data
    sessions.candidate_roots = lambda cur, since, ids=None: sorted(
        data["projects"])
    try:
        return sessions.discover(None, T0 - timedelta(days=1), now, **kw)
    finally:
        sessions.load, sessions.candidate_roots = orig_load, orig_roots


def test_windows_split_on_a_quiet_gap_and_paywall_only_is_not_reviewed():
    data = _data()
    ws = sessions.windows(data, 10)
    assert len(ws) == 2                       # 15:00 burst, next morning
    found, stats = _discover(data)
    keys = {s["key"]: s for s in found}
    assert sessions.session_key(10, T0) in keys
    # The morning request met only the subscribe wall: counted, not judged.
    assert stats["paywalled_only"] == 1
    # The failed index in project 11 is a session (the product failed them).
    assert sessions.session_key(11, T0 + timedelta(minutes=5)) in keys
    s = keys[sessions.session_key(10, T0)]
    assert s["surface"] == "mcp" and s["settled"]


def test_running_work_or_recent_activity_is_not_settled():
    data = _data()
    data["live"] = {10}
    found, stats = _discover(data)
    assert sessions.session_key(10, T0) not in {s["key"] for s in found}
    assert stats["unsettled"] >= 1
    found, _ = _discover(_data(), now=T0 + timedelta(minutes=70))
    assert sessions.session_key(10, T0) not in {s["key"] for s in found}


def test_the_fingerprint_moves_with_ratings_and_what_the_user_did_next():
    base = {s["key"]: s["fingerprint"] for s in _discover(_data())[0]}
    data = _data()
    data["messages"][1]["feedback"] = "up"
    assert {s["key"]: s["fingerprint"] for s in _discover(data)[0]} != base
    data = _data()
    data["events"].append({"id": 901, "user_id": 7, "project_id": 10,
                           "kind": "export_clicked",
                           "created_at": T0 + timedelta(minutes=90),
                           "detail": "{}"})
    again = {s["key"]: s["fingerprint"] for s in _discover(data)[0]}
    assert again[sessions.session_key(10, T0)] != \
        base[sessions.session_key(10, T0)]


def test_context_has_the_timeline_errors_renders_and_next_actions():
    found, _ = _discover(_data())
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    calls = []

    def presign(ids, keys):
        calls.append((ids, keys))
        return {"assets": {str(i): f"https://r2/{i}" for i in ids},
                "keys": {k: f"https://r2/{k}" for k in keys}}
    ctx = sessions.context(s, None, presign)
    assert ctx["evidence_prefix"] == f"ops/evidence/{s['key']}/"
    tools = [t["tool"] for t in ctx["timeline"]]
    # MCP calls come from their jobs once, not again from the chat mirror.
    assert tools == ["keep_segments", "look_at"]
    assert any("look_at" in e["what"] for e in ctx["errors"])
    r = ctx["renders"][0]
    assert r["asset_id"] == 500 and r["url"] == "https://r2/500"
    assert r["sheet_urls"]["sheet"] == "https://r2/media/10/22-x_s.jpg"
    assert ctx["sources"][0]["url"] == "https://r2/400"
    assert ctx["next_summary"]["downloaded"] is True
    assert ctx["next_summary"]["thumbs_down"] is True
    assert ctx["user"]["paying"] and ctx["user"]["new_user"]
    json.dumps(ctx)                          # the context file is JSON


def test_a_prompt_held_by_the_subscribe_wall_is_part_of_the_request():
    data = _data()
    # Make the morning window reviewable: the index failed after the wall.
    later = T0 + timedelta(hours=18)
    data["jobs"].append({**data["jobs"][3], "id": 31, "project_id": 10,
                         "created_at": later + timedelta(minutes=1),
                         "updated_at": later + timedelta(minutes=2)})
    data["messages"].pop(2)                  # only the wall's copy is left
    found, _ = _discover(data, now=later + timedelta(hours=2))
    s = [x for x in found if x["key"] == sessions.session_key(10, later +
                                                               timedelta(seconds=3))][0]
    ctx = sessions.context(s)
    assert ctx["requests"][0]["text"] == "make it vertical"
    assert ctx["requests"][0]["held_by_subscribe_wall"] is True


def test_a_failed_presign_never_loses_the_context():
    found, _ = _discover(_data())

    def broken(ids, keys):
        raise RuntimeError("backend down")
    ctx = sessions.context(found[0], None, broken)
    assert "backend down" in ctx["url_error"]


# ── Verdicts and tickets ─────────────────────────────────────────────────

def _session():
    found, _ = _discover(_data())
    return [x for x in found if x["key"] == sessions.session_key(10, T0)][0]


def _evidence(s, n=1):
    return [{"kind": "contact_sheet", "caption": "the delivered cut",
             "storage_key": f"ops/evidence/{s['key']}/sheet-{i}.jpg"}
            for i in range(n)]


def test_a_verdict_needs_pictures_under_the_sessions_own_folder():
    s = _session()
    cur = Cur([{"id": 1, "review_count": 1}])
    with pytest.raises(store.StoreError, match="evidence"):
        store.write_verdict(cur, s, "delivered_good", 8, "fine", [])
    with pytest.raises(store.StoreError, match="under"):
        store.write_verdict(cur, s, "delivered_good", 8, "fine",
                            [{"kind": "frame", "caption": "x",
                              "storage_key": "ops/evidence/p99-1/x.jpg"}])
    with pytest.raises(store.StoreError, match="root-cause"):
        store.write_verdict(cur, s, "delivered_poor", 3, "bad", _evidence(s))
    with pytest.raises(store.StoreError, match="score"):
        store.write_verdict(cur, s, "delivered_good", 11, "x", _evidence(s))
    with pytest.raises(store.StoreError, match="scores 6-10"):
        store.write_verdict(cur, s, "delivered_good", 4, "x", _evidence(s))
    with pytest.raises(store.StoreError, match="scores 1-3"):
        store.write_verdict(cur, s, "not_delivered", 8, "x", _evidence(s),
                            root_cause="r")
    row = store.write_verdict(cur, s, "delivered_poor", 3, "cuts the payoff",
                              _evidence(s, 2), root_cause="story selection")
    assert row == {"id": 1, "review_count": 1}
    sql, p = cur.sql[-1]
    assert sql.strip().startswith("INSERT INTO session_reviews")
    assert p["user"] == 7 and p["project"] == 10 and p["fp"] == \
        s["fingerprint"]
    assert len(json.loads(p["evidence"])) == 2
    assert json.loads(p["ids"])["jobs"]


def test_an_unsettled_session_cannot_be_judged():
    s = _session()
    s["settled"] = False
    with pytest.raises(store.StoreError, match="not settled"):
        store.write_verdict(Cur(), s, "delivered_good", 8, "x", _evidence(s))


def test_ticket_transitions():
    closed = NOW - timedelta(days=1)
    open_p1 = {"status": "open", "severity": "P1", "closed_at": None}
    assert store.decide(None, NOW, "P1") == "open"
    assert store.decide(open_p1, NOW, "P2") == "attach"
    assert store.decide(open_p1, NOW, "P0") == "escalate"
    fixed = {"status": "fixed", "severity": "P1", "closed_at": closed}
    # The cause came back AFTER the fix: reopen, recurrence + 1.
    assert store.decide(fixed, NOW, "P1") == "reopen"
    # A session from before the fix, reviewed late: attach, do not reopen.
    assert store.decide(fixed, closed - timedelta(hours=1), "P1") == \
        "attach_closed"
    wont = {"status": "wont_fix", "severity": "P2", "closed_at": closed}
    assert store.decide(wont, NOW, "P2") == "attach_closed"


def test_reopening_a_fixed_cause_counts_the_recurrence():
    review = {"id": 5, "started_at": NOW, "settled_at": NOW}
    ticket = {"id": 9, "cause_key": "index-scratch-too-small",
              "status": "fixed", "severity": "P1", "fix_sha": "abc1234",
              "root_cause": "disk", "regression_check": "t::x",
              "closed_at": NOW - timedelta(days=2)}
    cur = Cur([review, None, ticket, {"ticket_id": 9},
               dict(ticket, status="open", recurrence_count=1)])
    t, action = store.open_or_attach(cur, "index-scratch-too-small",
                                     "Index fails", "P1", session_key="k")
    assert action == "reopened"
    stmts = [q for q, _ in cur.sql]
    assert any("recurrence_count = recurrence_count + 1" in q for q in stmts)
    assert any("INSERT INTO ticket_reviews" in q for q in stmts)
    hist = [p for q, p in cur.sql if "recurrence_count + 1" in q][0][1]
    assert json.loads(hist)[0]["previous_fix_sha"] == "abc1234"


def test_a_cause_is_a_slug_and_closing_needs_proof():
    with pytest.raises(store.StoreError, match="slug"):
        store.open_or_attach(Cur(), "The index failed!", "x", "P1",
                             session_key="k")
    ticket = {"id": 3, "status": "open"}
    with pytest.raises(store.StoreError, match="regression_check"):
        store.close(Cur([ticket]), 3, "fixed", root_cause="r",
                    fix_sha="abc1234")
    with pytest.raises(store.StoreError, match="fix_sha"):
        store.close(Cur([ticket]), 3, "fixed", root_cause="r",
                    fix_sha="not a sha", regression_check="t")
    with pytest.raises(store.StoreError, match="reason"):
        store.close(Cur([ticket]), 3, "wont_fix")
    cur = Cur([ticket, dict(ticket, status="fixed")])
    store.close(cur, 3, "fixed", root_cause="disk sized by RAM",
                fix_sha="abc1234,fe:def5678",
                regression_check="tests/test_x.py::y")
    assert any("status = 'fixed'" in q for q, _ in cur.sql)


def test_minted_keys_store_only_a_hash():
    cur = Cur()
    key, exp = store.mint_key(cur)
    assert len(key) >= 40 and exp > datetime.now(timezone.utc)
    inserted = [p for q, p in cur.sql if "INSERT INTO ops_review_keys" in q][0]
    assert key not in json.dumps(inserted, default=str)
    import hashlib
    assert inserted[0] == hashlib.sha256(key.encode()).hexdigest()


# ── Views ────────────────────────────────────────────────────────────────

def test_signups_by_admin_day_with_today_7_and_30():
    today = datetime(2026, 10, 25).date()
    rows = [{"day": today, "n": 5, "unverified": 2},
            {"day": today - timedelta(days=1), "n": 7, "unverified": 0},
            {"day": today - timedelta(days=6), "n": 1, "unverified": 0},
            {"day": today - timedelta(days=20), "n": 10, "unverified": 1}]
    got = views.signups(Cur([rows]), now=NOW)
    assert (got["today"], got["yesterday"], got["d7"], got["d30"]) == \
        (5, 7, 13, 23)
    assert len(got["daily"]) == 30 and got["unverified_today"] == 2
    sql = Cur([rows])
    views.signups(sql, now=NOW)
    q = sql.sql[0][0]
    assert "is_verified = 1" in q and "ANY(%(internal)s)" in q


def test_tickets_are_grouped_by_root_cause_open_first():
    rows = [
        {"id": 1, "cause_key": "a-cause", "title": "A", "severity": "P2",
         "status": "fixed", "root_cause": "executor disk sized by RAM",
         "fix_sha": "abc1234", "regression_check": "t", "recurrence_count": 0,
         "first_seen": NOW, "last_seen": NOW, "closed_at": NOW,
         "created_at": NOW, "updated_at": NOW, "reviews": []},
        {"id": 2, "cause_key": "b-cause", "title": "B", "severity": "P0",
         "status": "open", "root_cause": "executor disk sized by RAM",
         "fix_sha": None, "regression_check": None, "recurrence_count": 1,
         "first_seen": NOW, "last_seen": NOW, "closed_at": None,
         "created_at": NOW, "updated_at": NOW,
         "reviews": [{"id": 5, "user_id": 7, "evidence": [
             {"kind": "frame", "caption": "x", "storage_key": "ops/evidence/k/x.jpg"}]}]},
        {"id": 3, "cause_key": "c-cause", "title": "C", "severity": "P1",
         "status": "open", "root_cause": None, "fix_sha": None,
         "regression_check": None, "recurrence_count": 0,
         "first_seen": NOW, "last_seen": NOW, "closed_at": None,
         "created_at": NOW, "updated_at": NOW, "reviews": []},
    ]
    cur = Cur([{"r": "x", "t": "y", "l": "z"}, rows])
    got = views.tickets(cur, presign=lambda k: "https://r2/" + k)
    names = [g["root_cause"] for g in got["groups"]]
    assert names[0] == "executor disk sized by RAM"     # has an open P0
    assert names[1] == "c-cause"                         # no root cause yet
    g = got["groups"][0]
    assert [t["id"] for t in g["tickets"]] == [2, 1]
    assert g["severity"] == "P0" and g["recurrence_count"] == 1
    assert g["tickets"][0]["reviews"][0]["evidence"][0]["url"] == \
        "https://r2/ops/evidence/k/x.jpg"


def test_views_say_when_the_tables_do_not_exist_yet():
    cur = Cur([{"r": None, "t": None, "l": None}])
    assert views.sessions(cur)["loop_ready"] is False


# ── Routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from flask import Flask
    from routes import admin_ops
    app = Flask("t")
    app.config.update(SECRET_KEY="k" * 40, DATABASE_URL="postgresql://stub/s")
    app.register_blueprint(admin_ops.admin_ops_bp)
    return app, app.test_client(), admin_ops


def _admin(app):
    import jwt
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": "1", "email": "thevalmera@gmail.com",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        app.config["SECRET_KEY"], algorithm="HS256")}


def test_admin_views_are_admin_only(client, monkeypatch):
    app, c, mod = client
    for path in ("/admin/video/ops/summary", "/admin/video/ops/sessions",
                 "/admin/video/ops/tickets"):
        assert c.get(path).status_code == 401

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass
    monkeypatch.setattr(mod, "_connect", lambda: Conn())
    monkeypatch.setattr(mod.views, "summary", lambda cur: {"signups": {}})
    assert c.get("/admin/video/ops/summary", headers=_admin(app)).json == \
        {"signups": {}}
    assert c.get("/admin/video/ops/sessions?verdict=bogus",
                 headers=_admin(app)).status_code == 400


def test_the_cli_helpers_need_a_valid_key(client, monkeypatch):
    app, c, mod = client
    monkeypatch.setattr(mod, "_ops_key", lambda: (False, None))
    assert c.post("/ops/review/presign", json={}).status_code == 401
    assert c.post("/ops/review/evidence?session=p1-2&name=x.jpg",
                  data=b"\xff\xd8\xff").status_code == 401
    # An admin browser token is not an ops key.
    assert c.get("/ops/review/whoami", headers=_admin(app)).status_code == 401


def test_evidence_accepts_one_real_image_under_the_session(client,
                                                           monkeypatch):
    app, c, mod = client
    put = []

    class S3:
        def put_object(self, **kw):
            put.append(kw)
    monkeypatch.setattr(mod, "_ops_key", lambda: (True, NOW))
    monkeypatch.setattr(mod.storage, "is_configured", lambda: True)
    monkeypatch.setattr(mod.storage, "client", lambda *a, **k: S3())
    monkeypatch.setattr(mod.storage, "bucket", lambda: "b")
    monkeypatch.setattr(mod.storage, "presign_get",
                        lambda key, expires=None, **k: "https://r2/" + key)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    ok = c.post("/ops/review/evidence?session=p4746-1792941458"
                "&name=index-error.jpg", data=jpeg)
    assert ok.status_code == 200
    assert ok.json["storage_key"] == \
        "ops/evidence/p4746-1792941458/index-error.jpg"
    assert put[0]["ContentType"] == "image/jpeg"
    for q, body in (("session=../x&name=a.jpg", jpeg),
                    ("session=p1-2&name=../a.jpg", jpeg),
                    ("session=p1-2&name=a.jpg", b"<html>not an image"),
                    ("session=p1-2&name=a.png", jpeg),
                    ("session=p1-2&name=a.svg", b"<svg/>")):
        assert c.post("/ops/review/evidence?" + q, data=body).status_code \
            == 400, q
    assert len(put) == 1


def test_presign_refuses_keys_outside_the_media_prefixes(client,
                                                         monkeypatch):
    app, c, mod = client
    monkeypatch.setattr(mod, "_ops_key", lambda: (True, NOW))
    monkeypatch.setattr(mod.storage, "is_configured", lambda: True)
    monkeypatch.setattr(mod.storage, "presign_get",
                        lambda key, expires=None, **k: "https://r2/" + key)
    bad = c.post("/ops/review/presign",
                 json={"keys": ["retention/manifests/x.json.gz"]})
    assert bad.status_code == 400
    good = c.post("/ops/review/presign",
                  json={"keys": ["media/1/a_s.jpg",
                                 "ops/evidence/p1-2/a.jpg"]})
    assert good.status_code == 200 and len(good.json["keys"]) == 2


def test_the_blueprint_is_registered_and_probed():
    app_src = (ROOT / "backend" / "app.py").read_text()
    assert "app.register_blueprint(admin_ops_bp)" in app_src
    probe = (ROOT / "backend" / "scripts" / "admin_probe.py").read_text()
    for path in ("/admin/video/ops/summary", "/admin/video/ops/sessions",
                 "/admin/video/ops/tickets"):
        assert path in probe


# ── The digest ───────────────────────────────────────────────────────────

FUNNEL = {"day": "2026-10-24",
          "start": datetime(2026, 10, 23, 20, tzinfo=timezone.utc),
          "end": datetime(2026, 10, 24, 20, tzinfo=timezone.utc)}


def _facts():
    return {
        "day": "2026-10-24", "loop_ready": True,
        "results": {"judged": 4, "good": 1, "poor": 2, "not_delivered": 1,
                    "rate_pct": 25.0, "avg_score": 4.5},
        "results_7d": {"judged": 9, "good": 5, "rate_pct": 55.6},
        "failures": {
            "total": 5, "customers": 2,
            "by_ticket": [{"id": 3, "severity": "P0", "status": "fixing",
                           "title": "index job failed: scratch space",
                           "failures": 4, "users": 1, "commits": [],
                           "url": views.TICKET_URL + "3"},
                          {"id": 4, "severity": "P1", "status": "fixed",
                           "title": "Upload refused: over size cap",
                           "failures": 1, "users": 1,
                           "commits": views.commit_links("abcdef1234567"),
                           "url": views.TICKET_URL + "4"}],
            "fixed": [{"id": 1, "title": "Look-at frame misses",
                       "root_cause": "seek past the end",
                       "commits": views.commit_links("abcdef1234567,fe:9876543"),
                       "regression_check": "tests/test_x.py::y",
                       "url": views.TICKET_URL + "1"}],
            "unhandled": [{"id": 3, "title": "index job failed",
                           "waiting_min": 41, "url": views.TICKET_URL + "3"}],
            "open": 2, "fixing": 1, "p0_active": 1},
        "people": {"signups": 23, "active": 31, "returning": 4},
        "people_7d": {"signups": 164, "active": 120, "returning": 3},
        "infra": {"queue": {"queued": 0, "running": 2,
                            "oldest_queued_min": None, "jobs_24h": 410,
                            "failed_24h": 5, "p90_wait_s_24h": 12.0},
                  "boxes": [{"name": "seattle-1", "load1": 2.5, "cpus": 8,
                             "mem_used_pct": 41, "disk_used_pct": 30,
                             "running_jobs": 2, "stale": False,
                             "reported_at": "2026-10-24T19:59:00+00:00"}],
                  "costs": {"yesterday_usd": 3.1, "cycle_to_date_usd": 60.2,
                            "cycle_projected_usd": 98.0}},
        "thumbnails": [{"url": "https://r2/ops/evidence/k/a.jpg?sig=1&x=2",
                        "caption": "index error reply",
                        "verdict": "not_delivered", "score": 1,
                        "session_key": "k"}]}


def test_digest_answers_the_four_questions_in_plain_words():
    subject, body = alerts.digest_email(_facts())
    assert subject == ("Valmera daily · 2026-10-24 · 25% good of 4 judged · "
                       "5 failures · 23 signups")
    titles = ["Did customers get good results?",
              "What failed, and what was fixed automatically",
              "Signups and active customers", "Infrastructure"]
    assert [t for t in titles if t in body] == titles
    assert body.index(titles[0]) < body.index(titles[1]) < \
        body.index(titles[2]) < body.index(titles[3])
    # Results with the sessions judged, and the proof pictures.
    assert "25% good" in body and "1 of 4 sessions judged" in body
    assert "56% good of 9 sessions" in body
    assert '<img src="https://r2/ops/evidence/k/a.jpg?sig=1&amp;x=2"' in body
    # Failures with links, what was fixed automatically and by which commit.
    assert "5 failures</b> hit 2 customers" in body
    assert 'href="https://valmera.io/admin?tab=tickets&amp;ticket=3"' in body
    assert "Fixed automatically" in body and \
        "hustler-server/commit/abcdef1234567" in body and \
        "startup_frontend/commit/9876543" in body
    assert "Waiting for a person" in body and "41 min" in body
    # People and infrastructure.
    assert "23 signups" in body and "31 active customers" in body
    assert "4 of them signed up earlier" in body and "3 returning" in body
    assert "load 2.5/8 CPUs" in body and "0 waiting, 2 running" in body
    assert "$3.1 yesterday" in body
    # Cut: money, funnel, urgent accounts, trial spend, the factory.
    for gone in ("MRR", "Committed", "Cash", "Funnel", "Most urgent",
                 "Free trial", "Podcast factory", "refund", "Refund",
                 "pause", "checkout"):
        assert gone not in body, gone


def test_digest_still_goes_out_with_missing_parts():
    facts = {"day": "2026-10-24", "loop_ready": False, "results": None,
             "results_7d": None, "failures": None, "people": None,
             "people_7d": None, "infra": None, "thumbnails": []}
    subject, body = alerts.digest_email(facts)
    assert "not live" in body and "not reporting health yet" in body
    assert "no sessions judged" in subject

    class Broken:
        def execute(self, *a):
            raise RuntimeError("no table")

        def fetchone(self):
            return None
    got = alerts.digest_facts(Broken(), NOW, presign=None)
    assert got["day"] == "2026-10-24"
    assert set(got["errors"]) >= {"people", "infra"}


def test_digest_facts_pick_one_picture_per_session_problems_first():
    ready = {"r": "x", "t": "y", "l": "z"}
    counts = {"reviewed": 2, "day_n": 2, "day_good": 1, "week_n": 2,
              "week_good": 1}
    pics = [{"id": 1, "session_key": "a", "verdict": "not_delivered",
             "score": 1, "evidence": [
                 {"storage_key": "ops/evidence/a/1.jpg", "caption": "c1"},
                 {"storage_key": "ops/evidence/a/2.jpg", "caption": "c2"}]},
            {"id": 2, "session_key": "b", "verdict": "delivered_good",
             "score": 8, "evidence": [
                 {"storage_key": "ops/evidence/b/1.jpg", "caption": "c3"}]}]
    cur = Cur([ready, counts, [], [], {"n": 1}, pics])
    got = views.digest_facts(cur, FUNNEL["start"], FUNNEL["end"],
                             presign=lambda k: "u:" + k)
    assert [p["url"] for p in got["thumbnails"]] == \
        ["u:ops/evidence/a/1.jpg", "u:ops/evidence/b/1.jpg"]
    assert got["day"]["rate_pct"] == 50.0


def test_opening_a_project_neither_starts_nor_extends_a_session():
    data = _data()
    j = dict(data["jobs"][2], id=40, type="filmstrip",
             created_at=NOW - timedelta(minutes=5),
             updated_at=NOW - timedelta(minutes=4), render_asset_id=None)
    data["jobs"].append(j)
    found, _ = _discover(data)
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    assert s["settled"]                      # the filmstrip did not extend it
    ctx = sessions.context(s)
    assert any(e["kind"] == "project_reopened" for e in ctx["next"])
    assert ctx["next_summary"]["came_back_at"] is not None
    assert ctx["next_summary"]["left"] is False


def test_mcp_ask_user_is_not_an_error_and_a_download_url_is_a_delivery():
    data = _data()
    data["jobs"].append(dict(data["jobs"][0], id=23, tool="ask_user",
                             is_error=True, tool_status=None,
                             text="PREREQUISITE: ASK THE USER YOURSELF"))
    data["jobs"].append(dict(data["jobs"][0], id=24, tool="download_url",
                             created_at=T0 + timedelta(minutes=40),
                             updated_at=T0 + timedelta(minutes=40),
                             text=None))
    data["events"] = []
    found, _ = _discover(data)
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    ctx = sessions.context(s)
    assert [t["tool"] for t in ctx["timeline"] if t["error"]] == ["look_at"]
    assert ctx["next_summary"]["exported"] and ctx["next_summary"]["downloaded"]


def test_a_draft_preview_link_is_a_download_not_an_export():
    """Ticket #4 (30 Oct): five download_url calls of DRAFT previews read as
    "exported" in the session summary, so the Reviewer and the digest saw
    exports that never happened."""
    data = _data()
    data["jobs"].append(dict(data["jobs"][0], id=24, tool="download_url",
                             created_at=T0 + timedelta(minutes=40),
                             updated_at=T0 + timedelta(minutes=40),
                             receipt_type="complete_preview", text=None))
    data["events"] = []
    found, _ = _discover(data)
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    summary = sessions.context(s)["next_summary"]
    assert summary["exported"] is False
    assert summary["downloaded"] is True
    assert summary["downloaded_draft_only"] is True
    data["jobs"].append(dict(data["jobs"][-1], id=25,
                             receipt_type="final_export"))
    found, _ = _discover(data)
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    summary = sessions.context(s)["next_summary"]
    assert summary["exported"] is True
    assert summary["downloaded_draft_only"] is False


def test_signed_urls_never_reach_a_context_file():
    data = _data()
    data["jobs"][1]["text"] = ("no frame at 580 s of x.mp4?X-Amz-Algorithm="
                               "AWS4&X-Amz-Credential=abc%2F2026&X-Amz-"
                               "Signature=deadbeef). The clip can")
    found, _ = _discover(data)
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    raw = json.dumps(sessions.context(s))
    assert "X-Amz" not in raw and "deadbeef" not in raw
    assert "?<signed-url>" in raw


def test_a_later_download_is_not_leaving():
    data = _data()
    data["users"][7]["last_seen_at"] = T0 - timedelta(days=1)
    data["messages"] = data["messages"][:2]          # no later request
    data["jobs"] = data["jobs"][:3]                  # no other project
    data["projects"].pop(11)
    data["events"][0]["created_at"] = T0 + timedelta(minutes=80)
    found, _ = _discover(data)
    ctx = sessions.context(found[0])
    assert ctx["next_summary"]["downloaded"] is True
    assert ctx["next_summary"]["left"] is False
    data["events"] = []
    found, _ = _discover(data)
    assert sessions.context(found[0])["next_summary"]["left"] is True
