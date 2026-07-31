"""A chat message sent from an older edit state continues from THAT state.

The studio lets you step back through the edit history. Before this, a message
sent while stepped back ran the agent on the NEWEST version — the user watched
one timeline while the reply described another. post_message now branches by
append (same contract as the user-op route's base_version since round 59):
a copy of the state on screen becomes the newest version, with the existing
encode adopted, before the turn is queued.

    cd backend && python -m pytest tests/test_chat_branching.py -q
"""

import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402
from routes.video import wschemas                       # noqa: E402


class FakeCursor:
    """Scripted cursor: queues of fetchone/fetchall results, in call order,
    with every executed statement recorded for assertions."""

    def __init__(self, ones=None, alls=None):
        self.ones = list(ones or [])
        self.alls = list(alls or [])
        self.executed = []          # (sql, params)
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.ones.pop(0)

    def fetchall(self):
        return self.alls.pop(0)

    def sql_containing(self, fragment):
        return [(s, p) for s, p in self.executed if fragment in s]


def _edl(duration=10.0):
    return wschemas.default_edl(duration)


def test_branch_appends_a_copy_and_adopts_the_existing_encode():
    base = {"version": 3, "json": _edl(), "created_by": "user"}
    cur = FakeCursor(
        ones=[
            {"version": 21},                             # INSERT edls
            {"storage_key": "renders/p/x.mp4", "bytes": 5, "duration_s": 9.5,
             "width": 960, "height": 540, "fps": 30.0,
             "meta": {"variant": "preview", "edl_version": 3}},  # twin row
            {"id": 77},                                  # INSERT assets
        ],
        alls=[
            # _preview_twin: renders newest-first, then the edls they render
            [{"id": 9, "meta": {"variant": "preview", "edl_version": 3}}],
            [{"version": 3, "json": _edl()}],
        ],
    )
    v = video._branch_edl(cur, "sess-1", 246, base)
    assert v == 21

    # the copy is created_by='user' — going back IS a user action, and the
    # /state self-heal only covers user versions
    ins = cur.sql_containing("INSERT INTO edls")
    assert len(ins) == 1 and "'user'" in ins[0][0]

    # queued previews of older versions are retired, exactly like the user-op
    # route's sweep
    sweep = cur.sql_containing("UPDATE video_jobs")
    assert len(sweep) == 1 and sweep[0][1][-1] == 21

    # the on-screen state's encode is adopted: a pointer, canonical object id
    adopt = cur.sql_containing("INSERT INTO assets")
    assert len(adopt) == 1
    meta = adopt[0][1][-1].adapted           # psycopg2 Json wrapper
    assert meta["edl_version"] == 21
    assert meta["reused_from_asset_id"] == 9

    # the branch announces itself in the chat, stamped for the rollback filter
    act = cur.sql_containing("INSERT INTO chat_messages")
    assert len(act) == 1
    content, act_meta = act[0][1][1], act[0][1][2].adapted
    assert "went back to edit state v3" in content
    assert act_meta["edl_version"] == 21
    assert act_meta["branched_from"] == 3
    assert act_meta["tool"] == "user_edit"


def test_branch_without_an_existing_encode_still_branches():
    """No twin — the version was never rendered. The branch still happens;
    the /state self-heal renders it (created_by='user', no covering job)."""
    base = {"version": 2, "json": _edl(), "created_by": "agent"}
    cur = FakeCursor(
        ones=[{"version": 8}],
        alls=[[]],                  # _preview_twin: no renders at all
    )
    assert video._branch_edl(cur, "sess-1", 1, base) == 8
    assert len(cur.sql_containing("INSERT INTO assets")) == 0
    assert len(cur.sql_containing("INSERT INTO chat_messages")) == 1
