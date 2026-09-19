"""An EDL commit and its job attribution are one database transaction."""

import db as dbx
import pytest


class _Cursor:
    def __init__(self):
        self.queries = []
        self._next = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.queries.append((compact, params))
        if compact.startswith("SELECT MAX(version)"):
            self._next.append({"version": 7})
        elif compact.startswith("INSERT INTO edls"):
            self._next.append({"version": 8})
        elif compact.startswith("SELECT payload->'mutation_receipt'"):
            self._next.append({"receipt": None})

    def fetchone(self):
        return self._next.pop(0)


class _Conn:
    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur


def test_insert_edl_persists_job_receipt_before_returning():
    conn = _Conn()

    version = dbx.insert_edl(
        conn, 14, {"keep": [[0, 5]]}, "agent",
        job_id=91, mutation_tool="set_caption_fixes", before_version=7)

    assert version == 8
    update = next(row for row in conn.cur.queries
                  if row[0].startswith("UPDATE video_jobs"))
    receipt = update[1][0].adapted
    assert receipt == {
        "job_id": 91, "before_version": 7, "after_version": 8,
        "tool": "set_caption_fixes", "committed": True,
    }
    assert update[1][1] == 91


def test_stale_worker_cannot_overwrite_a_newer_direct_edit():
    conn = _Conn()
    with pytest.raises(ValueError, match="version changed"):
        dbx.insert_edl(conn, 14, {"keep": [[0, 5]]}, "agent", before_version=6)
    assert not any(sql.startswith("INSERT") for sql, _ in conn.cur.queries)
