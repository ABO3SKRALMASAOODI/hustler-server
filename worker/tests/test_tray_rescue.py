"""Round 101 — the tray nobody submitted.

From the last-50-user audit (Aug 9 2026). The staging tray asks for a Submit
press before an upload becomes the project's main footage. When that press
never comes the studio is permanently dead: no `original`, no index, no
filmstrip, no greeting — an upload the user watched succeed and a page that
then does nothing, forever. Ten users in three weeks ended their entire
session there (3.5% of everyone who opened a project), and eight of them had
staged exactly ONE file, where a tray has one possible arrangement and
nothing whatsoever to arrange.

db.rescue_abandoned_trays commits such a tray from the reaper. What has to be
true, and is pinned here:

  * it commits the WHOLE tray the way the Submit button does — first video to
    main footage, everything else keeping its arrangement — because a rescue
    that revived the project and dropped the user's other four uploads is a
    second bug wearing the fix's clothes;
  * it re-checks under the project lock, so a real submit that lands between
    the scan and the write wins;
  * it only ever touches a project with nothing to lose (no original, no
    index job ever, no asset activity in the quiet window).

Run:  python -m pytest tests/test_tray_rescue.py -q     (from worker/)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                 # noqa: E402
import db as dbx                                              # noqa: E402


class FakeCur:
    """Enough psycopg2 cursor to run the real SQL flow: the scan returns the
    scripted targets, each SELECT is answered by shape, and every write is
    recorded verbatim for the assertions."""

    def __init__(self, state):
        self.s = state
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.s["sql"].append((q, params))
        self._rows = []
        self.rowcount = 0
        if "SELECT DISTINCT s.project_id" in q:
            self._rows = list(self.s["targets"])
        elif "FOR UPDATE" in q:
            self.s["locked"].append(params[0])
        elif "kind = 'original' LIMIT 1" in q:
            self._rows = [{"?": 1}] if self.s["has_original"] else []
        elif "COALESCE(meta->>'staged','') = 'true' ORDER BY" in q:
            self._rows = list(self.s["tray"])
        elif q.startswith("UPDATE assets SET kind = 'original'"):
            self.s["promoted"].append(params[0])
            self.rowcount = 1
        elif q.startswith("UPDATE assets SET meta"):
            self.s["patched"].append((params[1], params[0].adapted))
            self.rowcount = 1
        elif "INSERT INTO video_jobs" in q:
            self.s["indexed"].append(params[2].adapted["asset_id"])
            self._rows = [{"id": 900 + len(self.s["indexed"])}]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, state):
        self.s = state

    def cursor(self):
        return FakeCur(self.s)


def _state(tray, has_original=False):
    return {"targets": [{"project_id": 461, "user_id": 408}],
            "tray": tray, "has_original": has_original,
            "sql": [], "locked": [], "promoted": [], "patched": [],
            "indexed": []}


CLIP_A = {"id": 3191, "kind": "video_clip", "duration_s": 6.96}
CLIP_B = {"id": 3195, "kind": "video_clip", "duration_s": 1.44}
CLIP_C = {"id": 3196, "kind": "video_clip", "duration_s": 3.6}
IMAGE = {"id": 3200, "kind": "image_ref", "duration_s": None}
MUSIC = {"id": 3201, "kind": "music", "duration_s": 120.0}


def test_single_file_tray_becomes_the_footage():
    """The eight-of-ten case: one file, one possible outcome."""
    st = _state([CLIP_A])
    out = dbx.rescue_abandoned_trays(FakeConn(st))
    assert st["promoted"] == [3191]
    assert st["indexed"] == [3191]
    assert out == [(461, 408, 3191, 901)]


def test_the_whole_tray_is_committed_not_just_the_first():
    """Project 461 in production: three clips staged, none submitted. The
    rescue must land all three — the first as footage, the rest arranged."""
    st = _state([CLIP_A, CLIP_B, CLIP_C])
    dbx.rescue_abandoned_trays(FakeConn(st))
    assert st["promoted"] == [3191]
    # every video still gets its perception pass, footage included
    assert st["indexed"] == [3191, 3195, 3196]
    places = {aid: patch for aid, patch in st["patched"]}
    assert places[3195]["tray_place"] == {"order": 1, "before_main": False,
                                          "duration_s": 1.44}
    assert places[3196]["tray_place"]["order"] == 2
    # ...and they leave the tray, or the UI would still show them staged
    assert all(p["staged"] is None for p in places.values())


def test_a_clip_ahead_of_the_footage_is_marked_before_main():
    """Tray order is the user's arrangement: an image dropped in front of the
    first video plays before it, exactly as a real submit records."""
    st = _state([IMAGE, CLIP_A, MUSIC])
    dbx.rescue_abandoned_trays(FakeConn(st))
    assert st["promoted"] == [3191]
    places = {aid: patch for aid, patch in st["patched"]}
    assert places[3200]["tray_place"]["before_main"] is True
    # music is unstaged and indexed, but is not timeline-placed
    assert "tray_place" not in places[3201]
    assert 3201 in st["indexed"]


def test_a_submit_that_lands_first_wins():
    """The scan is not the decision. If an `original` exists by the time the
    lock is taken, the tray was really submitted and the rescue stands down."""
    st = _state([CLIP_A], has_original=True)
    out = dbx.rescue_abandoned_trays(FakeConn(st))
    assert out == [] and not st["promoted"] and not st["indexed"]
    assert st["locked"] == [461]        # it did take the lock before deciding


def test_an_images_only_tray_is_left_alone():
    """No video means no main footage to promote — a canvas program is the
    real submit's job, not a rescue's."""
    st = _state([IMAGE])
    assert dbx.rescue_abandoned_trays(FakeConn(st)) == []
    assert not st["promoted"] and not st["patched"]


def test_the_scan_only_selects_projects_with_nothing_to_lose():
    """The guards are the whole safety argument, so they are pinned as text:
    no original, no index job ever, and no asset activity inside the quiet
    window."""
    st = _state([CLIP_A])
    dbx.rescue_abandoned_trays(FakeConn(st))
    scan = st["sql"][0][0]
    assert "o.kind = 'original'" in scan
    assert "j.type = 'index'" in scan
    assert "q.created_at > NOW() - make_interval(secs => %s)" in scan
    assert st["sql"][0][1] == (config.TRAY_RESCUE_AFTER_S,)
    assert config.TRAY_RESCUE_AFTER_S >= 120
