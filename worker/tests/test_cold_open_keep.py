"""P0 #77 (2 Nov 2026, user 2661, project 5266): a cold open, then the episode.

"A 60-second cold open at 00:00, then the full cleaned-up episode from the
beginning" was listed to keep_segments as [teaser pieces..., the episode].
Every episode piece overlapped or ran behind the teaser, so the EPISODE became
66 muted replays cut to one shot each: "keep set to 4 segment(s), 66.3s of
3696.68s survives (+66 repeat(s), 2116.36s)" of a 3647 s episode. The agent
reverted it 14 times over two requests and both ended partial.

And the second request was a stale brief: a replayed duplicate message
(client_msg_id replay-*) that never got its own turn was auto-resumed by a
re-index 17 h and four answered requests later.
"""
import inspect
import json
import re

import pytest

import agent_tools
import db as dbx

TEASER = [[16.29, 27.0], [129.28, 133.03], [3594.23, 3597.59]]
# the cleaned episode as the agent listed it: forward pieces over the source
EPISODE = [[0.0, 91.77], [92.09, 127.19], [127.75, 132.47], [133.03, 181.47]] + [
    [round(200.0 + 60 * i, 2), round(258.0 + 60 * i, 2)] for i in range(57)]
EPISODE.append([3620.0, 3696.68])
MAIN = "originals/5266/podcast.mp4"


class _Db:
    def run(self, fn, *a, **k):
        assert fn is agent_tools.dbx.latest_asset
        return {"storage_key": MAIN, "width": 1920, "height": 1080, "meta": {}}


class _Ctx:
    def __init__(self, inserts=None):
        self.duration = 3696.68
        self.project_id = 5266
        self.user_message = ""
        self.verification_request = None
        self.index = {"words": [{"w": "How", "t0": 16.4, "t1": 16.6},
                                {"w": "three", "t0": 129.5, "t1": 129.8}]}
        self.db = _Db()
        self.versions = [{"version": 247, "json": {
            "keep": [list(x) for x in EPISODE], "inserts": inserts or [],
            "speed": [], "frame": {}}}]

    def clamp(self, v):
        return float(v)

    def latest_edl(self):
        return self.versions[-1]

    def write_edl(self, edl, desc):
        if edl == self.versions[-1]["json"]:
            return f"NO CHANGE — the EDL is identical to v{self.versions[-1]['version']}"
        v = self.versions[-1]["version"] + 1
        self.versions.append({"version": v, "json": json.loads(json.dumps(edl))})
        return f"EDL v{v}: {desc}"


def _fake_write_keep(ctx, merged, desc, **kw):
    edl = dict(ctx.latest_edl()["json"])
    edl["keep"] = [list(x) for x in merged]
    return ctx.write_edl(edl, desc)


def test_a_teaser_then_the_episode_keeps_the_episode_and_opens_on_the_teaser():
    base, replays = agent_tools._plan_keep_repeats(TEASER + EPISODE)
    assert base == agent_tools._merge_touching(EPISODE)
    assert replays == [(agent_tools.COLD_OPEN_AT, x) for x in TEASER]


def test_loops_are_not_cold_opens():
    stand, touch = [100.69, 102.53], [164.25, 166.58]
    base, replays = agent_tools._plan_keep_repeats([stand] * 5 + [touch])
    assert base == [stand, touch] and all(a == 0 for a, _ in replays)
    base, replays = agent_tools._plan_keep_repeats([[50, 52], [50, 52], [10, 12]])
    assert replays == [(0, [50.0, 52.0]), (0, [10.0, 12.0])]


def test_the_cold_open_plays_first_with_its_sound_and_the_episode_whole(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    logo = {"id": "ins1", "asset_key": "uploads/5266/logo.mp4", "kind": "video",
            "at_output_s": 0.0, "duration_s": 4.0, "fit": "contain"}
    ctx = _Ctx(inserts=[logo])
    # the cold open shows source the episode shows again: it is never one shot
    ctx.index["shots"] = [{"start": 0.0}, {"start": 20.0}, {"start": 131.0}]
    ctx.index["video"] = {"fps": 25}
    res = agent_tools.keep_segments(ctx, TEASER + EPISODE)
    assert res.startswith("EDL v248") and "COLD OPEN KEPT" in res
    assert "repeat(s)" not in res.split("\n")[0]
    edl = ctx.latest_edl()["json"]
    assert edl["keep"] == agent_tools._merge_touching(EPISODE)
    ins = edl["inserts"]
    cold = [i for i in ins if i["id"].startswith("cold")]
    assert [(i["source_start_s"], i["duration_s"]) for i in cold] == [
        (16.29, 10.71), (129.28, 3.75), (3594.23, 3.36)]
    assert all(i["at_output_s"] == 0.0 and not i.get("mute") for i in cold)
    # list order is play order at a shared boundary: teaser, then the logo
    assert ins[:3] == cold and ins[3]["id"] == "ins1"
    episode_s = sum(e - s for s, e in agent_tools._merge_touching(EPISODE))
    assert agent_tools.program_duration(edl) == pytest.approx(
        episode_s + 17.82 + 4.0, abs=0.05)


def test_an_episode_cleanup_keeps_the_cold_open_and_a_new_one_replaces_it(monkeypatch):
    monkeypatch.setattr(agent_tools, "_write_keep", _fake_write_keep)
    ctx = _Ctx()
    agent_tools.keep_segments(ctx, TEASER + EPISODE)
    cleaned = [list(x) for x in EPISODE]
    cleaned[5] = [cleaned[5][0] + 1.0, cleaned[5][1]]
    res = agent_tools.keep_segments(ctx, cleaned)
    assert res.startswith("EDL v") and "Removed" not in res
    assert len([i for i in ctx.latest_edl()["json"]["inserts"]
                if i["id"].startswith("cold")]) == 3
    agent_tools.keep_segments(ctx, [[16.29, 27.0]] + cleaned)
    cold = [i for i in ctx.latest_edl()["json"]["inserts"]
            if i["id"].startswith("cold")]
    assert [i["source_start_s"] for i in cold] == [16.29]


def test_a_saved_brief_older_than_an_answered_message_is_never_pending():
    for fn in (dbx.pending_user_message, dbx.resolve_pending_auto_resume):
        src = inspect.getsource(fn)
        assert re.search(r"\(j\.payload->>'message_id'\)::bigint > m\.id", src)


class _Cur:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return None


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self):
        return self.cur


def test_the_pending_query_binds_every_placeholder():
    conn = _Conn()
    assert dbx.pending_user_message(conn, 5266, 77) is None
    sql, params = conn.cur.calls[-1]
    assert sql.count("%s") == len(params) == 3 and params == (77, 5266, 5266)
