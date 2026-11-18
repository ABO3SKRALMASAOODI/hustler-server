"""The admin Sessions list (owner, 18 Nov 2026): every session of the
window, newest first, and plainly which kind it is (Studio edited or not,
MCP, Shorts, Website demo), from the product's own rows."""
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_loop import session_list as L  # noqa: E402

T = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)


def job(type_, at=0, dur=60, state="done", **kw):
    row = {"id": at, "project_id": 1, "type": type_, "state": state,
           "created_at": T + timedelta(seconds=at),
           "updated_at": T + timedelta(seconds=at + dur), "tool": None,
           "message_id": None, "first_cut": False, "has_url": False,
           "receipt_type": None, "error": None}
    row.update(kw)
    return row


def msg(role, at=0, **kw):
    row = {"id": 1000 + at, "role": role, "created_at": T + timedelta(seconds=at),
           "kind": None, "error": None, "tool": None, "source": None,
           "feature": None, "wall_surface": None, "subscribe_gate": None,
           "content": "make it punchy" if role == "user" else None,
           "project_id": 1}
    row.update(kw)
    return row


def edl(version, at, by="agent"):
    return {"project_id": 1, "version": version, "created_by": by,
            "created_at": T + timedelta(seconds=at)}


def kind(msgs, jobs, edls=(), proj_kind="edit"):
    n = L._edit_versions({1}, T, T + timedelta(seconds=600), jobs, list(edls))
    return L.kind_of(proj_kind, msgs, jobs, n)


def test_studio_request_that_changed_the_edit_is_edited():
    k = kind([msg("user", 0)],
             [job("agent_turn", 5, message_id="1000")],
             [edl(1, 5), edl(2, 30)])
    assert k["surface"] == "studio" and k["edited"] and k["why"] is None
    assert k["asks"] == 1


def test_first_cut_alone_is_not_an_edit():
    k = kind([], [job("index", 0), job("agent_turn", 100, first_cut=True)],
             [edl(1, 101), edl(2, 120), edl(3, 140)])
    assert not k["edited"]
    assert k["why"] == "first cut only, no request"


def test_upload_only_and_the_wall():
    assert kind([], [job("index", 0)])["why"] == "upload only"
    k = kind([msg("user", 0),
              msg("assistant", 2, kind="subscription_plans",
                  subscribe_gate="true", wall_surface="blank_canvas")], [])
    assert not k["edited"]
    assert k["why"] == "asked on a blank canvas, held by the subscribe wall"


def test_request_whose_turn_kept_the_base_version_is_not_edited():
    # The agent turn writes the untouched base (version 1) as it starts.
    k = kind([msg("user", 0)], [job("agent_turn", 5, message_id="1000")],
             [edl(1, 5)])
    assert not k["edited"]
    assert k["why"] == "asked, the agent ran but the edit did not change"


def test_mcp_edited_and_not_edited():
    k = kind([msg("activity", 0, source="mcp")],
             [job("mcp_tool", 0, tool="apply_edit_batch")], [edl(1, 10)])
    assert k["surface"] == "mcp" and k["edited"]
    k = kind([msg("activity", 0, source="mcp")],
             [job("mcp_tool", 0, tool="watch_video")])
    assert k["surface"] == "mcp" and not k["edited"]


def test_users_own_timeline_edit_counts():
    k = kind([], [job("preview", 0)], [edl(4, 10, by="user")])
    assert k["edited"]


def test_website_demo_and_shorts_from_rows_not_words():
    k = kind([msg("user", 0)], [job("agent_turn", 5, has_url=True)])
    assert k["surface"] == "website_demo"
    k = kind([msg("user", 0), msg("assistant", 2, kind="feature_upgrade",
                                 feature="website_demo")], [])
    assert k["surface"] == "website_demo"
    assert k["why"] == "asked, answered with the upgrade card"
    assert kind([msg("user", 0)], [], proj_kind="shorts")["surface"] == "shorts"
    # The customer's words alone never decide the kind.
    k = kind([msg("user", 0, content="record my website https://x.io")], [])
    assert k["surface"] == "studio"


def test_earlier_request_answered_here_counts_as_an_ask():
    k = kind([], [job("agent_turn", 5, message_id="77")], [edl(3, 20)])
    assert k["asks"] == 1 and k["edited"]


def _row(i, surface, edited):
    return {"key": f"p{i}", "surface": surface, "edited": edited,
            "started_at": f"2026-11-{10 + i:02d}T00:00:00+00:00"}


def test_page_filters_counts_and_pages_newest_first():
    rows = [_row(i, s, e) for i, (s, e) in enumerate(
        [("studio", True), ("studio", False), ("mcp", True), ("mcp", False),
         ("shorts", False)] * 5)]
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    rep = {"days": 7, "since": "x", "sessions": rows}
    out = L.page(rep, per_page=10)
    assert out["total"] == out["all_total"] == 25 and out["pages"] == 3
    assert len(out["sessions"]) == 10
    assert out["counts"]["all"] == {"total": 25, "edited": 10}
    assert out["counts"]["mcp"] == {"total": 10, "edited": 5}
    last = L.page(rep, per_page=10, page=3)
    assert len(last["sessions"]) == 5
    only = L.page(rep, surface="studio", edited=True)
    assert only["total"] == 5
    assert all(r["surface"] == "studio" and r["edited"] for r in only["sessions"])
    assert L.page(rep, edited=False)["total"] == 15
