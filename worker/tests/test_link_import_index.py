"""A pasted link becomes the main video: the index job fetches it first.

25 Oct 2026: nine users pasted a YouTube or Drive link into a project with no
video and none got a video in. The backend registers the link on the
original (meta.link_src, no bytes yet); run_index_job fetches it into the
asset's key, with main-video limits, before anything reads the key.

    cd worker && PYTHONPATH=.. python -m pytest tests/test_link_import_index.py -q
"""
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                  # noqa: E402
import db as dbx                                               # noqa: E402
import indexer                                                 # noqa: E402
import storage                                                 # noqa: E402
import url_media                                               # noqa: E402


class _Db:
    def __init__(self):
        self.calls = []

    def run(self, fn, *args, **kw):
        self.calls.append((fn, args))
        return None


def test_main_video_limits_apply_only_inside_the_block():
    assert url_media._limit("max_bytes", 1) == 1
    with url_media.main_video_limits():
        assert url_media._limit("max_bytes", 1) == config.LINK_IMPORT_MAX_BYTES
        assert url_media._limit("max_duration_s", 1) == \
            config.LINK_IMPORT_MAX_DURATION_S
        assert url_media._limit("timeout_s", 1) == config.LINK_IMPORT_TIMEOUT_S
    assert url_media._limit("timeout_s", 7) == 7
    # A whole recording, not a 500 MB / 1 h clip.
    assert config.LINK_IMPORT_MAX_BYTES > config.FETCH_MAX_BYTES
    assert config.LINK_IMPORT_MAX_DURATION_S >= config.MAX_DURATION_S


def test_a_fetched_link_lands_at_the_originals_key(monkeypatch, tmp_path):
    seen = {}

    def fake_fetch(url, workdir, prefer=None):
        seen["limits"] = url_media._limit("max_bytes", None)
        seen["prefer"] = prefer
        path = os.path.join(workdir, "dl.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 2048)
        return {"path": path, "kind": url_media.KIND_VIDEO, "title": "Match 4",
                "extractor": "Youtube"}

    uploads = []
    monkeypatch.setattr(url_media, "fetch", fake_fetch)
    monkeypatch.setattr(storage, "upload_file",
                        lambda path, key, ctype=None: uploads.append(
                            (key, os.path.getsize(path))))
    db = _Db()
    indexer._import_link_source(
        db, {"id": 5, "storage_key": "originals/7/abc.mp4"},
        "https://youtu.be/x", str(tmp_path))
    assert uploads == [("originals/7/abc.mp4", 2048)]
    assert seen == {"limits": config.LINK_IMPORT_MAX_BYTES,
                    "prefer": url_media.KIND_VIDEO}
    fn, args = db.calls[-1]
    assert fn is dbx.asset_link_imported
    assert args == (5, 2048, "Match 4.mp4")
    assert not os.path.exists(os.path.join(tmp_path, "link", "dl.mp4"))


def test_a_link_that_is_not_a_video_is_an_honest_final_answer(monkeypatch,
                                                                tmp_path):
    monkeypatch.setattr(url_media, "fetch", lambda *a, **k: {
        "path": "x", "kind": url_media.KIND_AUDIO})
    with pytest.raises(dbx.PermanentJobError, match="not a video"):
        indexer._import_link_source(_Db(), {"id": 5, "storage_key": "k"},
                                    "https://soundcloud.com/a", str(tmp_path))
    monkeypatch.setattr(url_media, "fetch", lambda *a, **k: (_ for _ in ()).throw(
        url_media.FetchMediaError("Private video")))
    with pytest.raises(dbx.PermanentJobError, match="Private video"):
        indexer._import_link_source(_Db(), {"id": 5, "storage_key": "k"},
                                    "https://youtu.be/x", str(tmp_path))


def test_the_index_job_fetches_before_reading_the_key():
    src = inspect.getsource(indexer.run_index_job)
    fetch = src.index("_import_link_source(")
    assert fetch < src.index("dedup_src")
    assert fetch < src.index("index_source_should_stream")
    assert "upload_meta.get(\"link_src\")" in src


def test_link_originals_route_as_a_known_download_and_never_auto_run_a_bare_link():
    shape = inspect.getsource(dbx.project_execution_shape)
    assert "meta ? 'link_src'" in shape
    assert "link_only" in inspect.getsource(dbx.pending_user_message)
