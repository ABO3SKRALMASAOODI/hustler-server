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
        "journey_format": 2, "why_short": "Vertical, no captions.",
        "asks": [{"text": "vertical", "status": "done", "note": None}],
        "delivered_asset_id": 500}))
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
                    "params": {"window": "24h"}, "fresh": True}
    body = r.get_json()
    assert body["stories"][0]["uploaded"] == {"url":
                                              "https://s/proxies/1/a.mp4"}
    assert "play_key" not in json.dumps(body)
    monkeypatch.setattr(cache, "shared_report",
                        lambda *a, **k: {"pending": True, "retry_after_s": 5})
    assert c.get("/admin/video/ops/journeys",
                 headers=_admin(app)).get_json()["pending"] is True
