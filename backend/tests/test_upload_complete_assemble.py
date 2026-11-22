"""A multipart completion is sent to storage once and waits for the assembly.

#1602 (VERIFY replay p0-vfy-uploads-1119, 19 Nov 2026): 8.3 GB in 496 parts.
Storage took over 60 s to assemble them. The general client (60 s read, 3
attempts) re-sent CompleteMultipartUpload after 60 s; storage had consumed
the upload id when the assembly began, so every re-send answered
NoSuchUpload while the first was still building the object, and the upload
was reported failed (400) over an object that landed seconds later.
"""
import contextlib
import os
import sys
import threading
import time

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage                                           # noqa: E402
from routes import video                                 # noqa: E402

KEY = "originals/5592/8gb-source.mp4"


def _env(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret-1602")
    monkeypatch.setenv("S3_REGION", "auto")


def test_assemble_client_never_resends_and_outwaits_a_long_assembly(monkeypatch):
    _env(monkeypatch)
    c = storage.client(assemble=True)
    cfg = c.meta.config
    assert cfg.retries["total_max_attempts"] == 1   # sent once, never re-sent
    assert cfg.read_timeout >= 900
    # The general client keeps its bounded profile for every other call.
    g = storage.client()
    assert g is not c
    assert g.meta.config.read_timeout == storage.S3_READ_TIMEOUT_S


def test_complete_multipart_uses_the_assemble_client(monkeypatch):
    seen = []

    class _C:
        def complete_multipart_upload(self, **kw):
            seen.append(kw["UploadId"])

    def fake_store_client(name, public=False, fast=False, assemble=False):
        assert assemble, "a completion must not use a client that re-sends it"
        return _C()

    monkeypatch.setattr(storage, "store_client", fake_store_client)
    monkeypatch.setattr(storage, "store_bucket", lambda name: "b")
    storage.complete_multipart(KEY, "u-1", [{"part_number": 1, "etag": "e"}],
                               store="legacy")
    assert seen == ["u-1"]


def test_retry_meeting_a_consumed_id_succeeds_when_the_object_lands(monkeypatch):
    """The id is gone and the object is not readable yet: the call looks
    again and answers success when the assembly finishes, never 400."""
    landed = {"bytes": None}

    @contextlib.contextmanager
    def no_lock(key, wait_s=None, db_url=None):
        yield True

    monkeypatch.setattr(video, "_one_complete_per_key", no_lock)
    monkeypatch.setattr(video.storage, "finish_multipart", lambda *a: {
        "ok": False, "gone": True, "code": "NoSuchUpload",
        "message": "An error occurred (NoSuchUpload)"})
    monkeypatch.setattr(video.storage, "head_bytes",
                        lambda key, fast=False: landed["bytes"])
    events = []
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a))
    monkeypatch.setattr(video, "UPLOAD_COMPLETE_WAIT_S", 5.0)

    def finish_assembly():
        time.sleep(0.6)
        landed["bytes"] = 8_308_435_294

    threading.Thread(target=finish_assembly, daemon=True).start()
    t0 = time.monotonic()
    out = video._finish_multipart(2918, 5592, KEY, "u-1",
                                  [{"part_number": 1, "etag": "e"}],
                                  {"kind": "original"})
    assert out is None
    assert time.monotonic() - t0 < 4.0
    assert not [e for e in events if "upload_failed" in e]


def test_a_truly_gone_upload_still_says_so_after_the_wait(monkeypatch):
    @contextlib.contextmanager
    def no_lock(key, wait_s=None, db_url=None):
        yield True

    monkeypatch.setattr(video, "_one_complete_per_key", no_lock)
    monkeypatch.setattr(video.storage, "finish_multipart", lambda *a: {
        "ok": False, "gone": True, "code": "NoSuchUpload", "message": "x"})
    monkeypatch.setattr(video.storage, "head_bytes", lambda key, fast=False: None)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    monkeypatch.setattr(video, "UPLOAD_COMPLETE_WAIT_S", 0.3)
    payload, status = video._finish_multipart(
        1, 5592, KEY, "u-1", [], {"kind": "original"})
    assert status == 400 and payload["upload_kept"] is False
