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


class _Cur:
    """Answers the projects query, then the assets query."""
    def __init__(self, projects, assets):
        self.answers = [projects, assets]
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.answers.pop(0)


def asset(id_, kind, at, project_id=1, **kw):
    row = {"id": id_, "project_id": project_id, "kind": kind,
           "storage_key": f"k/{id_}", "duration_s": 30, "width": 1080,
           "height": 1920, "variant": None, "edl_version": None,
           "filename": None, "purged_at": None,
           "created_at": T + timedelta(seconds=at)}
    row.update(kw)
    return row


def test_each_row_carries_its_upload_and_what_it_got_signed():
    """Owner, 20 Nov: the list shows each session's uploaded video and the
    delivered one at once, as Journeys did, without opening the chat."""
    row = {"key": "1:a", "project_id": 1, "started_at": S_iso(0),
           "ended_at": S_iso(600)}
    cur = _Cur([{"id": 1, "root": 1}],
               [asset(10, "original", -60, filename="talk.mov"),
                asset(11, "proxy", -50),
                asset(12, "render", 120, variant="preview", edl_version="2"),
                asset(13, "render", 300, variant="final", edl_version="3"),
                asset(14, "render", 5000, variant="final")])   # a later session's
    found = L.media(cur, [row])
    m = found["1:a"]
    assert m["uploaded"]["asset_id"] == 10 and m["uploaded"]["play_key"] == "k/11"   # plays the proxy
    assert m["got"]["asset_id"] == 13 and m["got"]["kind"] == "export"
    assert m["renders"] == 2
    assert L.media_keys(found) == ["k/11", "k/13"]
    rows = L.attach_media([row], found, {"k/11": "https://u/11", "k/13": "https://u/13"})
    assert rows[0]["uploaded"]["url"] == "https://u/11" and "play_key" not in rows[0]["uploaded"]
    assert rows[0]["got"]["url"] == "https://u/13" and rows[0]["got"]["edl_version"] == 3


def test_a_session_that_delivered_nothing_says_so():
    row = {"key": "1:b", "project_id": 1, "started_at": S_iso(0),
           "ended_at": S_iso(60)}
    found = L.media(_Cur([{"id": 1, "root": 1}], [asset(10, "original", -5)]), [row])
    rows = L.attach_media([row], found, {"k/10": "https://u/10"})
    assert rows[0]["uploaded"]["url"] == "https://u/10" and rows[0]["got"] is None
    assert L.media(_Cur([], []), []) == {}


def S_iso(seconds):
    return (T + timedelta(seconds=seconds)).isoformat()
