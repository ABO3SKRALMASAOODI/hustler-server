"""Ticket #60 (12 Nov 2026): an original that landed after its finish call
failed must not block every export as "still uploading (100% done)".

The replay of the customer's 8.3 GB, 45-minute upload: storage answered the
multipart finish with errors while it was still assembling the object, the
object appeared seconds later, and nothing flipped `upload_state`, so
/render/final answered 409 original_uploading forever. The export gate now
asks storage and adopts a landed original through original-ready's checks.
"""
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask                                  # noqa: E402

from routes import video                                 # noqa: E402

ORIGINAL = {"id": 901, "storage_key": "originals/5/abc.mp4",
            "meta": {"upload_state": "pending", "upload_progress": 1.0}}


def _wire(monkeypatch, head, ready_status=200):
    calls, events = [], []
    monkeypatch.setattr(video.storage, "head_bytes", lambda k: head)

    def fake_ready(user_id, project_id, data):
        calls.append((user_id, project_id, data))
        return {"upload_state": "ready"}, ready_status
    monkeypatch.setattr(video, "_original_ready", fake_ready)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a[2]))
    return calls, events


def test_a_landed_original_is_adopted_through_original_ready(monkeypatch):
    calls, events = _wire(monkeypatch, head=8_308_435_294)
    assert video._adopt_landed_original(7, 5, ORIGINAL) is True
    assert calls == [(7, 5, {"asset_id": 901})]
    assert "upload_original_reconciled" in events


def test_an_upload_still_in_flight_stays_pending(monkeypatch):
    # A multipart object does not exist until storage assembles it.
    calls, _ = _wire(monkeypatch, head=None)
    assert video._adopt_landed_original(7, 5, ORIGINAL) is False
    assert calls == [], "nothing to adopt: original-ready must not run"


def test_an_object_that_fails_the_checks_is_not_adopted(monkeypatch):
    calls, events = _wire(monkeypatch, head=1234, ready_status=400)
    assert video._adopt_landed_original(7, 5, ORIGINAL) is False
    assert calls and "upload_original_reconciled" not in events


def test_a_storage_error_is_never_worse_than_before(monkeypatch):
    def boom(k):
        raise RuntimeError("storage down")
    monkeypatch.setattr(video.storage, "head_bytes", boom)
    assert video._adopt_landed_original(7, 5, ORIGINAL) is False
    assert video._adopt_landed_original(7, 5, None) is False


def test_the_export_gate_asks_storage_before_refusing():
    """In _request_final the storage check runs before the original_uploading
    refusal, and only for a pending original."""
    src = open(video.__file__).read()
    body = src[src.index("def _request_final("):]
    gate = body.index('"code": "original_uploading"')
    check = body.rindex(
        "_adopt_landed_original(user_id, project_id, original)", 0, gate)
    refusal = body.rindex('if meta.get("upload_state") == "pending":', 0, gate)
    assert check < refusal, "storage must be asked before the refusal"
    assert 'meta.get("upload_state") == "pending"' in body[check - 120:check]
