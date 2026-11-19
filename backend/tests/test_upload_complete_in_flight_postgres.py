"""A retried complete that meets the first one still assembling waits for it.

#1602 (18 Nov 2026, replay vfy60-p5592-1118): 8.3 GB in 496 parts. The first
/uploads/complete outlasted the proxy in front of the API, so the page saw a
500 and retried. Storage had consumed the upload id and the object was not
readable yet: the retry answered 400 "the multipart upload id no longer
exists", which the page never retries, and the upload showed as failed. 37 s
later the first call registered the asset.

CI: QUEUE_TEST_DATABASE_URL; locally any throwaway database.
"""
import os
import sys
import threading
import time

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402

URL = os.getenv("QUEUE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated PostgreSQL")

KEY = "originals/5592/8gb-source.mp4"
_REAL_LOCK = video._one_complete_per_key


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class _SlowStore:
    """Storage whose complete consumes the upload id at once and shows the
    object only when the assembly ends, as the replay's did."""

    def __init__(self):
        self.assembling = threading.Event()
        self.finish = threading.Event()
        self.object_bytes = None
        self.completes = 0

    def finish_multipart(self, key, upload_id, parts):
        self.completes += 1
        if self.completes > 1:
            return {"ok": False, "gone": True, "code": "NoSuchUpload",
                    "message": "An error occurred (NoSuchUpload)"}
        self.assembling.set()
        assert self.finish.wait(30)
        self.object_bytes = 8_300_000_000
        return {"ok": True, "repaired": []}

    def head_bytes(self, key, fast=False):
        return self.object_bytes


@pytest.fixture
def store(monkeypatch):
    s = _SlowStore()
    events = []
    monkeypatch.setattr(video.storage, "finish_multipart", s.finish_multipart)
    monkeypatch.setattr(video.storage, "head_bytes", s.head_bytes)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append((a, k)))
    monkeypatch.setattr(
        video, "_one_complete_per_key",
        lambda key, wait_s=None: _REAL_LOCK(key, wait_s, db_url=URL))
    s.events = events
    return s


def _complete(out, name):
    out[name] = video._finish_multipart(
        2918, 5592, KEY, "u1", [{"part_number": 1, "etag": '"e"'}],
        {"kind": "original", "stage": "complete_multipart"})


def test_a_retry_waits_for_the_complete_still_assembling(store):
    out = {}
    first = threading.Thread(target=_complete, args=(out, "first"))
    first.start()
    assert store.assembling.wait(10)
    retry = threading.Thread(target=_complete, args=(out, "retry"))
    retry.start()
    time.sleep(1.0)
    assert "retry" not in out            # it waits; the old code answered 400
    store.finish.set()
    first.join(10)
    retry.join(10)
    assert out == {"first": None, "retry": None}
    assert not [e for e in store.events if "upload_failed" in e[0]]


def test_an_assembly_longer_than_the_wait_is_a_retry_not_a_failure(
        store, monkeypatch):
    monkeypatch.setattr(video, "UPLOAD_COMPLETE_WAIT_S", 0.5)
    out = {}
    first = threading.Thread(target=_complete, args=(out, "first"))
    first.start()
    assert store.assembling.wait(10)
    _complete(out, "retry")
    payload, status = out["retry"]
    assert status == 503 and payload["retry"] and payload["upload_kept"]
    assert store.completes == 1          # storage was not asked twice
    assert not [e for e in store.events if "upload_failed" in e[0]]
    store.finish.set()
    first.join(10)
    _complete(out, "again")              # the page's next retry
    assert out["first"] is None and out["again"] is None


def test_a_database_that_does_not_answer_never_stops_a_complete(
        store, monkeypatch):
    monkeypatch.setattr(
        video, "_one_complete_per_key", lambda key, wait_s=None: _REAL_LOCK(
            key, wait_s, db_url="postgresql://127.0.0.1:1/none"))
    store.finish.set()
    out = {}
    _complete(out, "only")
    assert out["only"] is None and store.completes == 1
