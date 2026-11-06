"""Ticket #386: a clip given by link gets a word index, like an uploaded clip.

Project 5329, 3 Nov 2026: get_words on a fetched clip was refused three times
with "not available yet" although nothing would ever analyse it (152 of 152
fetched clips in 14 days had no hash and no index).
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest                                                   # noqa: E402

import agent_tools                                              # noqa: E402
import db as dbx                                                # noqa: E402
import read_tools                                               # noqa: E402

ASSET = {"id": 7, "kind": "video_clip", "storage_key": "fetched/5/a.mp4",
         "sha256": None, "duration_s": 266.0}


class _Db:
    def __init__(self, asset=ASSET, index=None, failure=None):
        self.asset, self.index, self.failure = asset, index, failure
        self.enqueued = []

    def run(self, fn, *args, **_kw):
        name = getattr(fn, "__name__", "")
        if name == "asset_by_key":
            return self.asset
        if name == "get_index_by_sha":
            return self.index
        if name == "last_index_failure":
            return self.failure
        if name == "enqueue_asset_index":
            self.enqueued.append(args)
            return 99
        if callable(fn) and fn.__name__ == "<lambda>":
            return self.asset
        raise AssertionError(name)


def _ctx(db):
    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx.db, ctx.project_id = db, 5
    ctx.project, ctx.job = {"user_id": 3}, {"id": 1, "user_id": 3}
    ctx.index = {}
    return ctx


@pytest.mark.parametrize("read", [read_tools.get_words,
                                  read_tools.get_transcript])
def test_a_read_of_an_unanalysed_clip_starts_the_analysis(read):
    db = _Db()
    out = read(_ctx(db), asset_key=ASSET["storage_key"])
    assert out.startswith("PREREQUISITE") and "wait_for_job(job_id=99)" in out
    assert db.enqueued == [(5, 3, 7)]


@pytest.mark.parametrize("read", [read_tools.get_words,
                                  read_tools.get_transcript])
def test_the_backend_read_path_hands_it_to_the_queued_path(read):
    plain = types.SimpleNamespace(db=_Db(), project_id=5, index={})
    with pytest.raises(read_tools.NeedsQueuedPath):
        read(plain, asset_key=ASSET["storage_key"])


def test_a_failed_analysis_is_named_and_not_restarted():
    db = _Db(failure="ffmpeg could not read the file")
    out = read_tools.get_words(_ctx(db), asset_key=ASSET["storage_key"])
    assert out.startswith("UNAVAILABLE") and "ffmpeg could not" in out
    assert db.enqueued == []


def test_an_image_starts_nothing():
    db = _Db(asset=dict(ASSET, kind="image_ref"))
    out = read_tools.get_words(_ctx(db), asset_key=ASSET["storage_key"])
    assert "no reusable media index" in out and db.enqueued == []


def test_an_analysed_clip_reads_its_words():
    index = {"video": {"duration": 266.0},
             "words": [{"w": "hola", "t0": 1.0, "t1": 1.4}]}
    db = _Db(asset=dict(ASSET, sha256="abc"), index={"json": index})
    out = read_tools.get_words(_ctx(db), asset_key=ASSET["storage_key"])
    assert "hola" in out and db.enqueued == []


def test_the_analysis_never_starts_twice():
    class Cur:
        def __init__(self):
            self.rows = [{"id": 7}, {"id": 41, "state": "running",
                                     "progress": 10}]

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, _params=None):
            assert "INSERT" not in sql

        def fetchone(self):
            return self.rows.pop(0)

    conn = types.SimpleNamespace(cursor=lambda: Cur())
    # each cursor() is new: share one so both lookups see their row
    cur = Cur()
    conn.cursor = lambda: cur
    assert dbx.enqueue_asset_index(conn, 5, 3, 7) == 41


def _fetch(monkeypatch, kind):
    inserted, queued = [], []
    got = {"kind": kind, "path": "/tmp/x", "bytes": 5, "duration_s": 266.0,
           "filename": "clip.mp4", "source_url": "https://x.test/a"}

    class Db:
        def run(self, fn, *args, **kw):
            if fn is dbx.insert_asset:
                inserted.append(args)
                return 7
            if fn is dbx.enqueue_asset_index:
                queued.append(args)
                return 99
            raise AssertionError(fn)

    monkeypatch.setattr(agent_tools.config, "URL_FETCH_ENABLED", True)
    monkeypatch.setattr(agent_tools, "_execution_policy", lambda _c: "local")
    monkeypatch.setattr(agent_tools.url_media, "fetch", lambda *a, **k: got)
    monkeypatch.setattr(agent_tools.url_media, "storage_key",
                        lambda *a: "fetched/5/a.mp4")
    monkeypatch.setattr(agent_tools.url_media, "content_type", lambda _p: "v")
    monkeypatch.setattr(agent_tools.storage, "upload_file", lambda *a: None)
    monkeypatch.setattr(agent_tools, "_queue_download_review", lambda *a: 0)
    monkeypatch.setattr(agent_tools, "_mark_visual_review_pending",
                        lambda *a: None)
    monkeypatch.setattr(agent_tools.music_gate, "named_song_for",
                        lambda *a: None)
    return Db(), inserted, queued


def test_a_fetched_clip_is_queued_for_analysis(monkeypatch, tmp_path):
    db, inserted, queued = _fetch(monkeypatch, "video_clip")
    ctx = _ctx(db)
    ctx.workdir, ctx.urls_fetched = str(tmp_path), []
    out = agent_tools.fetch_url(ctx, "https://x.test/a")
    assert inserted and queued == [(5, 3, 7)]
    assert "job 99" in out


def test_a_fetched_song_waits_until_something_reads_it(monkeypatch, tmp_path):
    db, _inserted, queued = _fetch(monkeypatch, "music")
    ctx = _ctx(db)
    ctx.workdir, ctx.urls_fetched = str(tmp_path), []
    agent_tools.fetch_url(ctx, "https://x.test/a")
    assert queued == []
