"""The Journeys view (owner, 31 Oct 2026): "I need to understand what
happened with the user and what the product did and what the user wanted and
whether we made that or not."

The Reviewer's structured fields (asks / why_short / next_actions / the two
asset ids, inside session_reviews.signals), the funnel counted in order, one
story per session, and the route that signs links on every request.
No database: scripted cursors, as in test_outcome_loop.py.
"""
import json
import os
import sys
from datetime import timedelta

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ops_loop import journeys, sessions, store  # noqa: E402
from test_outcome_loop import (NOW, T0, Cur, _admin, _data,  # noqa: E402
                               _discover)


# ── The Reviewer's structured fields ─────────────────────────────────────

def test_journey_fields_are_validated_with_actionable_messages():
    with pytest.raises(store.StoreError, match="needs `asks`"):
        store.check_journey({"why_short": "x"})
    with pytest.raises(store.StoreError, match="status"):
        store.check_journey({"asks": [{"text": "a", "status": "maybe"}],
                             "why_short": "x"})
    with pytest.raises(store.StoreError, match="20 or fewer"):
        store.check_journey({"asks": [{"text": "a", "status": "done",
                                       "note": " ".join(["w"] * 21)}],
                             "why_short": "x"})
    with pytest.raises(store.StoreError, match="25 or fewer"):
        store.check_journey({"asks": [{"text": "a", "status": "done"}],
                             "why_short": " ".join(["w"] * 26)})
    with pytest.raises(store.StoreError, match="why_short is required"):
        store.check_journey({"asks": [{"text": "a", "status": "done"}]})
    with pytest.raises(store.StoreError, match="asset id"):
        store.check_journey({"asks": [{"text": "a", "status": "done"}],
                             "why_short": "x", "source_asset_id": "abc"})
    got = store.check_journey({
        "asks": [{"text": "vertical", "status": "Partial"},
                 {"text": "captions", "status": "not done", "note": "none"}],
        "why_short": "Vertical but no captions.",
        "next_actions": "pressed Download, met the wall",
        "delivered_asset_id": "38211"})
    assert [a["status"] for a in got["asks"]] == ["partly", "not_done"]
    assert got["next_actions"] == ["pressed Download, met the wall"]
    assert got["delivered_asset_id"] == 38211 and got["source_asset_id"] is None
    assert got["journey_format"] == 2


def test_annotate_merges_into_signals_and_keeps_the_verdict():
    cur = Cur([{"id": 4, "session_key": "p1-2"},
               {"id": 4, "session_key": "p1-2"}])
    row = store.annotate(cur, "p1-2", {"asks": [{"text": "a",
                                                 "status": "done"}],
                                       "why_short": "fine"},
                         backfilled_from="reasoning")
    assert row["id"] == 4
    sql, p = cur.sql[-1]
    assert sql.strip().startswith("UPDATE session_reviews")
    assert "signals = COALESCE(signals" in sql
    for col in ("verdict", "score", "reasoning", "review_count"):
        assert f"{col} =" not in sql
    assert json.loads(p["j"])["backfilled_from"] == "reasoning"


def test_the_cli_reads_the_journey_from_the_json_spec(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import ops_review
    a = ops_review.parser().parse_args(
        ["verdict", "--json", "v.json", "--why-short", "flag wins",
         "--next-action", "left", "--delivered-asset", "9"])
    j = ops_review._journey(a, {"asks": [{"text": "x", "status": "done"}],
                                "why_short": "spec", "source_asset_id": 3})
    assert j["why_short"] == "flag wins" and j["source_asset_id"] == 3
    assert j["delivered_asset_id"] == 9 and j["next_actions"] == ["left"]
    b = ops_review.parser().parse_args(["annotate", "--session", "p1-2",
                                        "--backfilled-from", "reasoning"])
    assert b.fn is ops_review.cmd_annotate


# ── Labels ───────────────────────────────────────────────────────────────

def test_shapes_and_plans_read_the_way_the_owner_says_them():
    assert journeys.shape(1080, 1920) == "9:16 vertical"
    assert journeys.shape(1920, 1080) == "16:9 wide"
    assert journeys.shape(1080, 1080) == "1:1 square"
    assert journeys.shape(1972, 1080) == "1972x1080 wide"
    assert journeys.shape(None, 5) is None
    assert journeys.plan_label({"plan": "free"}, trial=True) == "Free trial"
    assert journeys.plan_label({"billing_plan": "plus_2026", "paying": True}) \
        == "New Plus (paying)"
    assert journeys.plan_label({"billing_plan": "mcp_connect",
                                "paying": True}) == "MCP Connect (paying)"
    assert journeys.plan_label({"billing_plan": "ai", "paying": True}) == \
        "Paying · Creator"
    assert journeys.device_label({"device_type": "phone",
                                  "device_browser": "Safari"}) == \
        "Phone · Safari"


# ── The funnel, counted in order ─────────────────────────────────────────

def test_the_funnel_counts_in_order_and_keeps_the_out_of_order_count():
    at = NOW - timedelta(hours=3)
    users = [{"id": i, "email": f"u{i}@x", "created_at": at, "last_seen_at":
              at, "plan": "free", "billing_plan": None,
              "billing_status": None, "is_subscribed": 0,
              "device_type": "phone", "device_browser": "Safari",
              "paying": False} for i in (1, 2, 3, 4)]
    up = [{"user_id": u, "at": at + timedelta(minutes=5)} for u in (1, 2, 3)]
    cut = [{"user_id": u, "at": at + timedelta(minutes=9)} for u in (1, 2)]
    asked = [{"user_id": 1, "at": at + timedelta(minutes=12)},
             {"user_id": 4, "at": at + timedelta(minutes=1)}]   # MCP, no upload
    ready = {"r": "x", "t": "y", "l": "z"}
    sat = [{"user_id": 1, "at": at + timedelta(minutes=30)}]
    dl = [{"user_id": 2, "at": at + timedelta(minutes=40)}]  # no satisfying review
    cur = Cur([users, up, cut, asked, ready, sat, dl, [], [{"user_id": 1}]])
    f = journeys.funnel(cur, NOW, "24h")
    n = {s["key"]: s["n"] for s in f["steps"]}
    assert n == {"signed_up": 4, "uploaded": 3, "first_cut": 2, "asked": 1,
                 "satisfied": 1, "download": 0, "subscribed": 0}
    by = {s["key"]: s for s in f["steps"]}
    assert by["asked"]["any_order_n"] == 2
    assert by["download"]["any_order_n"] == 1
    assert by["uploaded"]["drop"] == 1 and by["signed_up"]["drop"] is None
    assert by["first_cut"]["stopped_ids"] == [2]
    assert f["people"]["1"]["plan"] == "Free trial"
    assert f["people"]["4"]["uploaded_at"] is None
    assert f["people"]["2"]["download_at"]


# ── One story per session ────────────────────────────────────────────────

def _story(review=None, extra=()):
    found, _ = _discover(_data())
    s = [x for x in found if x["key"] == sessions.session_key(10, T0)][0]
    ctx = sessions.context(s)
    return journeys.story(s, ctx, review, {"email": "a@b", "device_type":
                                           "laptop", "device_browser":
                                           "Safari"}, set(), list(extra))


def test_a_story_has_the_upload_the_delivery_and_the_mcp_calls():
    st = _story()
    assert st["who"]["email"] == "a@b" and st["who"]["device"] == \
        "Laptop · Safari"
    assert st["who"]["plan"] == "New Plus (paying)"
    assert st["uploaded"]["play_key"] == "proxies/10/a.mp4"
    assert st["uploaded"]["shape"] == "9:16 vertical"
    assert st["got"]["asset_id"] == 500 and st["got"]["kind"] == "preview"
    assert st["got"]["play_key"] == "media/10/22-x.mp4"
    # Over MCP the user's words stay in their AI app: the calls are shown.
    assert st["surface"] == "mcp" and not st["automatic_first_cut"]
    assert [c["tool"] for c in st["mcp_calls"]] == ["keep_segments",
                                                    "look_at"]
    assert st["mcp_calls"][1]["error"] is True
    assert st["review"] is None
    labels = [i["label"] for i in st["next"]]
    assert "Preview ready (30 s) · v3" in labels
    assert "Downloaded the file" in labels


def test_older_reviews_say_so_and_new_ones_carry_their_asks():
    base = {"id": 3, "session_key": "k", "verdict": "delivered_poor",
            "score": 4, "reasoning": "long", "root_cause": "r",
            "request_summary": "s", "evidence": [], "reviewer": "claude-opus",
            "review_count": 1, "reviewed_at": NOW,
            "tickets": [{"id": 9, "status": "fixed", "fix_sha": "abc1234",
                         "title": "t", "severity": "P1",
                         "closed_at": NOW.isoformat()}]}
    old = _story(dict(base, signals={"paying": True}))
    assert old["review"]["format"] == "older" and old["review"]["asks"] == []
    assert old["review"]["tickets"][0]["commits"][0]["sha"] == "abc1234"
    new = _story(dict(base, signals={
        "journey_format": 2, "why_short": "Vertical, no captions.", "cause_class": "tool",
        "asks": [{"text": "vertical", "status": "done", "note": None}],
        "delivered_asset_id": 500}))
    assert old["review"]["cause_class"] is None
    assert new["review"]["cause_class"] == "tool"
    assert new["review"]["format"] == "v2"
    assert new["review"]["why_short"] == "Vertical, no captions."
    assert new["got"]["asset_id"] == 500


def test_watched_events_collapse_to_the_furthest_point_per_version():
    ev = [{"id": i, "user_id": 7, "project_id": 10,
           "kind": "free_result_watched_pct",
           "created_at": T0 + timedelta(minutes=30 + i),
           "detail": json.dumps({"pct": pct, "version": 3})}
          for i, pct in enumerate((50, 90))]
    st = _story(extra=ev)
    watched = [i for i in st["next"] if i["kind"] == "watched"]
    assert [w["label"] for w in watched] == ["Watched 90% of v3"]


def test_links_are_signed_at_request_time_and_keys_never_leave():
    rep = {"stories": [{"uploaded": {"play_key": "proxies/1/a.mp4"},
                        "got": {"play_key": None, "purged": True},
                        "review": {"evidence": [{"storage_key":
                                                 "ops/evidence/k/a.jpg"}]}}]}
    keys = journeys.storage_keys(rep)
    assert keys == ["ops/evidence/k/a.jpg", "proxies/1/a.mp4"]
    out = journeys.attach_urls(rep, {k: "https://s/" + k for k in keys})
    st = out["stories"][0]
    assert st["uploaded"] == {"url": "https://s/proxies/1/a.mp4"}
    assert st["got"]["url"] is None
    assert st["review"]["evidence"][0]["url"].endswith("a.jpg")
    assert rep["stories"][0]["uploaded"]["play_key"]   # the cache is untouched


@pytest.fixture
def client():
    from flask import Flask
    from routes import admin_ops
    app = Flask("t")
    app.config.update(SECRET_KEY="k" * 40, DATABASE_URL="postgresql://stub/s")
    app.register_blueprint(admin_ops.admin_ops_bp)
    return app, app.test_client(), admin_ops


def test_the_journeys_route_is_admin_only_and_signs_each_request(
        client, monkeypatch):
    app, c, mod = client
    assert c.get("/admin/video/ops/journeys").status_code == 401
    assert c.get("/admin/video/ops/journeys?window=30d",
                 headers=_admin(app)).status_code == 400
    from admin_metrics import cache
    seen = {}

    def fake_report(endpoint, params, compute, fresh=False, logger=None):
        seen.update(endpoint=endpoint, params=params, fresh=fresh)
        return {"funnel": {}, "stories": [
            {"uploaded": {"play_key": "proxies/1/a.mp4"}, "got": None,
             "review": None}], "computed_at": "x"}
    monkeypatch.setattr(cache, "shared_report", fake_report)
    monkeypatch.setattr(mod, "_admin_presign", lambda k: "https://s/" + k)
    r = c.get("/admin/video/ops/journeys?window=24h&fresh=1",
              headers=_admin(app))
    assert r.status_code == 200
    assert seen == {"endpoint": "/admin/video/ops/journeys",
                    "params": {"window": "24h", "version": 3}, "fresh": True}
    body = r.get_json()
    assert body["stories"][0]["uploaded"] == {"url":
                                              "https://s/proxies/1/a.mp4"}
    assert "play_key" not in json.dumps(body)
    monkeypatch.setattr(cache, "shared_report",
                        lambda *a, **k: {"pending": True, "retry_after_s": 5})
    assert c.get("/admin/video/ops/journeys",
                 headers=_admin(app)).get_json()["pending"] is True


# ── Why a session has no video (owner, 20 Nov 2026) ─────────────────────

def _ctx(**kw):
    base = {"surface": "studio", "settled": True, "timeline": [], "errors": [],
            "jobs": [], "requests": [], "earlier_requests": [], "replies": []}
    base.update(kw)
    return base


def _call(tool, error=False, text=""):
    return {"via": "mcp", "tool": tool, "error": error, "text": text,
            "status": "error" if error else "ok"}


def test_a_story_with_a_video_has_no_why():
    assert _story()["why"] is None


def test_an_mcp_app_that_never_asked_for_a_video_is_not_our_failure():
    w = journeys.why_no_video(_ctx(surface="mcp", timeline=[
        _call("get_transcript"), _call("search_stock"), _call("search_stock"),
        _call("add_stock_media", True, "TRANSIENT_FAILURE: 187 MB")]), None)
    assert w["kind"] == "app_never_asked" and w["fault"] == "none"
    assert "never asked Valmera to render or export" in w["line"]
    assert "search_stock ×2" in w["line"]


def test_an_mcp_render_that_failed_is_ours_with_its_words():
    w = journeys.why_no_video(_ctx(surface="mcp", timeline=[
        _call("keep_segments"),
        _call("render_preview", True, "render failed: source missing")]), None)
    assert w["kind"] == "product_failed" and w["fault"] == "ours"
    assert "render_preview" in w["line"] and "source missing" in w["line"]


def test_the_wall_the_failure_and_the_silent_agent_each_say_so():
    wall = journeys.why_no_video(_ctx(
        requests=[{"text": "cut it", "held_by_subscribe_wall": True}],
        replies=[{"paywall": True, "text": "plans"}]), None)
    assert wall["kind"] == "paywall" and wall["fault"] == "wall"
    failed = journeys.why_no_video(_ctx(
        requests=[{"text": "cut it"}], jobs=[{"type": "agent_turn", "state": "failed"}],
        errors=[{"what": "agent_turn job 7 failed", "detail": "timeout"}]), None)
    assert failed["kind"] == "product_failed" and "agent_turn job 7 failed: timeout" in failed["line"]
    quiet = journeys.why_no_video(_ctx(
        requests=[{"text": "make it punchy"}], jobs=[{"type": "agent_turn", "state": "done"}],
        replies=[{"paywall": False, "text": "Which part should I cut?"}]), None)
    assert quiet["kind"] == "agent_no_video" and quiet["fault"] == "ours"
    assert "Which part should I cut?" in quiet["line"]
    assert journeys.why_no_video(_ctx(settled=False), None)["kind"] == "running"
    assert journeys.why_no_video(_ctx(), None)["kind"] == "upload_only"


def test_a_customers_habit_is_one_line_on_each_of_their_cards():
    sts = ([{"who": {"user_id": 7}, "why": {"kind": "app_never_asked"}}] * 4
           + [{"who": {"user_id": 7}, "why": None}]
           + [{"who": {"user_id": 8}, "why": {"kind": "paywall"}}] * 2)
    pat = journeys.customer_patterns(sts, "7d")
    assert pat[7] == ("This customer: 5 sessions in 7 days: 4 their AI app never "
                      "asked for a video; 1 delivered a video.")
    assert 8 not in pat                                   # fewer than 3 sessions

def test_plan_use_counts_the_session_and_preserves_fractional_charges():
    jobs = [{"credits_charged": n} for n in ("0.25", 119.75, None, "invalid", "NaN")]
    paid = {"plan": "ai", "paying": True}
    assert journeys.plan_use(jobs, paid, None) == {
        "credits": 120, "plan_credits": 1000, "share": 0.12}
    assert journeys.plan_use(jobs, paid, 2000)["share"] == 0.06
    assert journeys.plan_use(jobs, dict(paid, trial_status="trialing"), 0)["share"] == 1.2
    assert journeys.plan_use(jobs, {"plan": "free"}, 250)["share"] == 0.48
    from model_prices import USD_PER_CREDIT
    trial = journeys.plan_use(jobs, {"plan": "free"}, 0, 250 * USD_PER_CREDIT)
    assert trial["plan_credits"] == 250 and trial["share"] == 0.48
    assert journeys.plan_use(jobs, {"plan": "free"}, 0) is None
    assert journeys.plan_use(jobs, {}, None) is None
    assert journeys.plan_use([{"credits_charged": 0}], paid, 1000) is None


def test_warmed_route_serves_complete_snapshot_and_honors_refresh(client, monkeypatch):
    import time
    app, c, mod = client
    snapshot = {"stories": [{"key": "one"}], "cache_age_s": 10}
    app.extensions["journey_snapshots"] = {"7d": (time.monotonic() - 5, snapshot)}
    calls = []
    def fetch(window, fresh):
        calls.append((window, fresh))
        return {"pending": True}
    monkeypatch.setattr(mod, "_journey_snapshot", fetch)
    body = c.get(mod.JOURNEYS_ENDPOINT, headers=_admin(app)).get_json()
    assert body["stories"] == snapshot["stories"] and body["cache_age_s"] >= 15
    assert not calls
    c.get(mod.JOURNEYS_ENDPOINT + "?fresh=1", headers=_admin(app))
    app.extensions["journey_snapshots"]["7d"] = (time.monotonic() - 150, snapshot)
    c.get(mod.JOURNEYS_ENDPOINT, headers=_admin(app))
    assert calls == [("7d", True), ("7d", False)]


def test_folds_only_empty_sessions_using_all_calls():
    s = sessions.Session(surface="mcp", settled=True)
    s.rows = {"jobs": [{"type": "mcp_tool", "tool": "get_transcript"}]}
    ctx = _ctx()
    def fold(src=None, got=None):
        return journeys.fold_kind(s, ctx, got, src, {"kind": "upload_only"})
    assert fold() == "mcp"
    s.rows["jobs"].insert(0, {"type": "mcp_tool", "tool": "deliver_aspects"})
    assert fold() is None
    s["surface"], s["settled"], s.rows["jobs"] = "studio", False, []
    assert fold() == "running"
    ctx["requests"] = [{"text": "edit"}]
    assert fold() is None
    ctx["requests"], s["settled"] = [], True
    assert fold(src={"asset_id": 1}) == "upload"
    assert fold() is None
    assert fold(got={"asset_id": 2}) is None
    ctx["earlier_requests"] = [{"text": "continued"}]
    assert fold(src={"asset_id": 1}) is None


def test_session_chat_endpoint_replays_exact_turns_and_signs_at_open(client, monkeypatch):
    from types import SimpleNamespace
    app, c, mod = client
    base = "/admin/video/ops/session_chat"
    key = sessions.session_key(10, T0)
    assert c.get(base).status_code == 401
    headers = _admin(app)
    assert c.get(base + "?key=oops", headers=headers).status_code == 400
    s = sessions.Session(key=key, started_at=T0, ended_at=NOW, surface="studio")
    s.data = {"projects": {10: {}, 11: {}}}
    s.rows = {"messages": [], "jobs": [{"type": "mcp_tool", "id": 8, "state": "done"}]}
    s.next_events = [{"id": 5, "project_id": 10, "kind": "upload_failed"}]
    long = "The complete original text.\n" * 350
    def msg(i, role, minute, version=None, **fields):
        return dict(dict(id=i, role=role, created_at=T0+timedelta(minutes=minute),
                         project_id=10, content=long, version=version, preview_id=None,
                         held=None, error=None), **fields)
    msgs = [msg(1, "user", 0), msg(2, "assistant", 2, 1, preview_id=41),
            msg(3, "assistant", 4, 2), msg(4, "assistant", 5, 3, error="failed")]
    def asset(i, pid, version, minute, purged=False):
        return dict(id=i, project_id=pid, version=version, duration_s=12.3,
                    variant="preview", purged=purged, storage_key=f"renders/{i}",
                    created_at=T0 + timedelta(minutes=minute))
    renders = [asset(41, 10, 1, 1), asset(42, 10, 2, 3, True),
               asset(43, 11, 2, 3), asset(44, 10, 3, 6)]
    call = dict(id=8, created_at=T0, tool="get_transcript", args={"start": 0},
                text=long, error=False, state="done", preview_id=None)
    failure = dict(created_at=T0+timedelta(minutes=7), kind="upload_failed", text="Offline")
    def connection():
        return SimpleNamespace(cursor=lambda: Cur([msgs, [call], renders, [failure]]),
                               close=lambda: None)
    monkeypatch.setattr(mod, "_connect", connection)
    monkeypatch.setattr(sessions, "discover", lambda *a, **kw: ([s], {}))
    assert c.get(base + "?key=p10-0", headers=headers).status_code == 404
    url = base + "?key=" + key
    for suffix in ("first", "second"):
        monkeypatch.setattr(mod, "_admin_presign", lambda k: k + "/" + suffix)
        response = c.get(url, headers=headers)
        assert response.status_code == 200 and response.cache_control.no_store
        entries = response.json["entries"]
        assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)
        replies = [e for e in entries if e["role"] == "assistant"]
        assert replies[0]["text"] == long
        assert replies[0]["video"]["url"] == "renders/41/" + suffix
        assert replies[0]["video"]["duration_s"] == 12.3
        assert replies[1]["video"]["purged"] and replies[1]["video"]["url"] is None
        assert replies[2]["error"] and "video" not in replies[2]
        assert entries[-1]["text"] == "Offline"
        tool = next(e for e in entries if e["role"] == "tool")
        assert tool["tool"] == "get_transcript" and tool["text"] == long and tool["args"] == {"start": 0}
