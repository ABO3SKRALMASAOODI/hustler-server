"""An interrupted upload carries on from the parts storage already holds.

FAILURES.md (25 Oct 2026): 51 of 661 video uploaders in 60 days started a
transfer that never landed, and 33 restarted the same file from zero (2540: a
5.3 GB recording restarted at 09:22 and 09:53, never landed). Completed parts
were still in the bucket under the same upload id; nothing asked for them.

Also pinned: the whole-file relay's 64 MB ceiling is gone (parts are relayed
one at a time), the 14 GB upload cap is gone (100 GB), and WAV audio up to
500 MB is accepted.

    cd backend && python -m pytest tests/test_upload_resume.py -q
"""
import contextlib
import io
import os
import sys

import pytest
from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage                                          # noqa: E402
from routes import video                                # noqa: E402

MIB = 1024 * 1024


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class _S3:
    """One open multipart upload with some parts stored."""

    def __init__(self, nbytes, part_size, stored, gone=False, sizes=None,
                 assembled=None):
        self.nbytes, self.part_size, self.gone = nbytes, part_size, gone
        self.stored = {n: f'"etag{n}"' for n in stored}
        self.sizes = sizes or {}
        self.assembled = assembled
        self.presigned, self.uploaded = [], []

    def _size(self, n):
        return self.sizes.get(n, storage.expected_part_bytes(
            self.nbytes, self.part_size, n))

    def list_parts(self, Bucket, Key, UploadId, MaxParts=1000,
                   PartNumberMarker=None):
        if self.gone:
            raise _ClientError("NoSuchUpload")
        nums = sorted(n for n in self.stored if n > (PartNumberMarker or 0))
        page = nums[:MaxParts]
        return {"Parts": [{"PartNumber": n, "ETag": self.stored[n],
                           "Size": self._size(n)} for n in page],
                "IsTruncated": len(nums) > len(page),
                "NextPartNumberMarker": page[-1] if page else None}

    def head_object(self, Bucket, Key):
        if self.assembled is None:
            raise _ClientError("404")
        return {"ContentLength": self.assembled}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append(Params["PartNumber"])
        return f"https://r2.test/{Params['Key']}?part={Params['PartNumber']}"

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        if self.gone:
            raise _ClientError("NoSuchUpload")
        data = Body.read()
        self.uploaded.append((PartNumber, len(data)))
        return {"ETag": f'"relayed{PartNumber}"'}


@pytest.fixture
def s3(monkeypatch):
    holder = {}

    def use(fake):
        holder["s3"] = fake
        monkeypatch.setattr(storage, "client", lambda *a, **k: fake)
        monkeypatch.setenv("S3_BUCKET", "b")
        return fake
    return use


# ── storage.resume_multipart ────────────────────────────────────────────────

def test_resume_presigns_only_the_parts_storage_does_not_hold(s3):
    nbytes = 5_300_000_000                       # 2540's recording
    ps = storage.part_size_for(nbytes)
    n_parts = (nbytes + ps - 1) // ps
    fake = s3(_S3(nbytes, ps, stored=range(1, 201)))
    out = storage.resume_multipart("originals/7/a.mp4", "u1", nbytes,
                                   "video/mp4", part_size=ps)
    assert out["resumed"] and out["upload_id"] == "u1"
    assert out["part_size"] == ps
    assert len(out["part_urls"]) == n_parts
    assert [p["part_number"] for p in out["completed_parts"]] == \
        list(range(1, 201))
    assert all(p["url"] is None for p in out["part_urls"][:200])
    assert all(p["url"] for p in out["part_urls"][200:])
    assert fake.presigned == list(range(201, n_parts + 1))
    assert out["bytes_done"] == 200 * ps


def test_a_short_or_wrong_sized_part_is_sent_again(s3):
    nbytes = 100 * MIB
    ps = storage.part_size_for(nbytes)
    fake = s3(_S3(nbytes, ps, stored=[1, 2, 3], sizes={2: 1234}))
    out = storage.resume_multipart("originals/7/a.mp4", "u1", nbytes,
                                   part_size=ps)
    assert [p["part_number"] for p in out["completed_parts"]] == [1, 3]
    assert 2 in fake.presigned


def test_an_unknown_part_size_falls_back_to_the_issued_geometry(s3):
    nbytes = 900 * MIB
    s3(_S3(nbytes, storage.part_size_for(nbytes), stored=[1]))
    out = storage.resume_multipart("originals/7/a.mp4", "u1", nbytes,
                                   part_size=3)          # not issuable
    assert out["part_size"] == storage.part_size_for(nbytes)
    assert out["completed_parts"][0]["part_number"] == 1


def test_an_upload_that_finished_before_complete_is_reported_assembled(s3):
    nbytes = 200 * MIB
    s3(_S3(nbytes, storage.part_size_for(nbytes), stored=[], gone=True,
           assembled=nbytes))
    out = storage.resume_multipart("originals/7/a.mp4", "u1", nbytes)
    assert out["assembled"] is True and out["upload_id"] is None


def test_a_gone_upload_raises_upload_gone(s3):
    nbytes = 200 * MIB
    s3(_S3(nbytes, storage.part_size_for(nbytes), stored=[], gone=True))
    with pytest.raises(storage.UploadGone):
        storage.resume_multipart("originals/7/a.mp4", "u1", nbytes)


# ── storage.relay_part ─────────────────────────────────────────────────────

def test_relay_part_writes_one_part_of_any_file(s3):
    fake = s3(_S3(40 * 1024 ** 3, 41 * MIB, stored=[]))
    body = io.BytesIO(b"x" * (3 * MIB))
    etag = storage.relay_part("originals/7/a.mp4", "u1", 812, body,
                              declared_bytes=3 * MIB)
    assert etag == '"relayed812"'
    assert fake.uploaded == [(812, 3 * MIB)]


def test_relay_part_refuses_a_part_over_the_relay_cap(s3):
    s3(_S3(1, 1, stored=[]))
    with pytest.raises(storage.RelayTooLarge):
        storage.relay_part("originals/7/a.mp4", "u1", 1, io.BytesIO(b""),
                           declared_bytes=storage.RELAY_MAX_BYTES + 1)


# ── the routes ─────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, originals=()):
        self.originals = list(originals)
        self._all = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT storage_key FROM assets"):
            self._all = [{"storage_key": k} for k in self.originals]
        else:
            self._all = []

    def fetchone(self):
        return self._all[0] if self._all else None

    def fetchall(self):
        return self._all


def _route(monkeypatch, fn, cur, *, gated=False, json=None, query="",
           data=None):
    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    events = []
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p, "chat_session_id": 5})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: gated)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append((a, k)))
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    app = Flask(__name__)
    kw = {"json": json} if json is not None else {"data": data or b""}
    with app.test_request_context("/x" + query, method="POST", **kw):
        response = fn.__wrapped__(user_id=60, project_id=7)
    if isinstance(response, tuple):
        response, status = response
    else:
        status = response.status_code
    return status, response.get_json(), events


def test_resume_route_returns_the_missing_parts(monkeypatch, s3):
    nbytes = 2 * 1024 ** 3
    ps = storage.part_size_for(nbytes)
    s3(_S3(nbytes, ps, stored=[1, 2, 3, 4]))
    status, body, events = _route(
        monkeypatch, video.resume_upload, _Cur(),
        json={"storage_key": "originals/7/a.mp4", "upload_id": "u1",
              "filename": "game.mp4", "bytes": nbytes, "part_size": ps,
              "kind": "original"})
    assert status == 200, body
    assert body["resumed"] and len(body["completed_parts"]) == 4
    assert body["relay_max_bytes"] == storage.RELAY_MAX_BYTES
    assert events and events[0][0][2] == "upload_resumed"
    assert "upload_resumed" in video.CLIENT_EVENT_KINDS


def test_resume_route_refuses_another_projects_key(monkeypatch, s3):
    s3(_S3(1, 1, stored=[]))
    status, body, _ = _route(
        monkeypatch, video.resume_upload, _Cur(),
        json={"storage_key": "originals/8/a.mp4", "upload_id": "u1",
              "filename": "a.mp4", "bytes": 100 * MIB})
    assert status == 400


def test_resume_route_answers_gone_so_the_client_starts_fresh(monkeypatch, s3):
    s3(_S3(100 * MIB, 8 * MIB, stored=[], gone=True))
    status, body, _ = _route(
        monkeypatch, video.resume_upload, _Cur(),
        json={"storage_key": "originals/7/a.mp4", "upload_id": "u1",
              "filename": "a.mp4", "bytes": 100 * MIB})
    assert status == 410 and body["gone"] is True


def test_resume_keeps_the_subscribe_wall_exactly(monkeypatch, s3):
    """A gated account whose project already holds an original mints no
    further transfer, resumed or not; its own first original carries on."""
    nbytes = 100 * MIB
    s3(_S3(nbytes, storage.part_size_for(nbytes), stored=[1]))
    req = {"storage_key": "originals/7/second.mp4", "upload_id": "u1",
           "filename": "b.mp4", "bytes": nbytes}
    status, body, _ = _route(monkeypatch, video.resume_upload,
                             _Cur(["originals/7/first.mp4"]), gated=True,
                             json=req)
    assert status == 402 and body["subscribe_gated"] is True
    status, _body, _ = _route(monkeypatch, video.resume_upload,
                              _Cur(), gated=True, json=req)
    assert status == 200


def test_relay_part_route_streams_one_part(monkeypatch, s3):
    fake = s3(_S3(1, 1, stored=[]))
    status, body, _ = _route(
        monkeypatch, video.relay_upload_part, _Cur(),
        query="?key=originals/7/a.mp4&upload_id=u1&part_number=3",
        data=b"y" * 1000)
    assert status == 200, body
    assert body == {"ok": True, "part_number": 3, "etag": '"relayed3"'}
    assert fake.uploaded == [(3, 1000)]


def test_relay_part_route_checks_ownership_and_numbers(monkeypatch, s3):
    s3(_S3(1, 1, stored=[]))
    status, _b, _ = _route(
        monkeypatch, video.relay_upload_part, _Cur(),
        query="?key=originals/9/a.mp4&upload_id=u1&part_number=3",
        data=b"y")
    assert status == 400
    status, _b, _ = _route(
        monkeypatch, video.relay_upload_part, _Cur(),
        query="?key=originals/7/a.mp4&upload_id=u1&part_number=0",
        data=b"y")
    assert status == 400


# ── the caps ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gb", [15.6, 24.1, 33.7, 51.2, 59.6])
def test_the_large_recordings_users_were_refused_now_fit(gb):
    """1822, 1918 (paying), 1922, 2008, 2042: game/OBS/camera recordings."""
    nbytes = int(gb * 1e9)
    assert storage.validate_upload("rec.mkv", nbytes, "original")
    ps = storage.part_size_for(nbytes)
    assert (nbytes + ps - 1) // ps <= storage.MAX_TRANSFER_PARTS


def test_parts_are_unchanged_below_sixteen_gib():
    for nbytes in (20 * MIB, 1024 ** 3, 14 * 1024 ** 3, 15 * 1024 ** 3):
        raw = (nbytes + storage.TARGET_PARTS - 1) // storage.TARGET_PARTS
        old = max(storage.MIN_PART_SIZE, min(storage.MAX_PART_SIZE,
                                             -(-raw // MIB) * MIB))
        assert storage.part_size_for(nbytes) == old


def test_wav_voiceovers_and_masters_fit():
    """2097: VO1+2.wav 81.6 MB; 2127: Satellites.wav 58.5 MB."""
    for nbytes in (81_600_000, 58_500_000, 450 * MIB):
        assert storage.validate_upload("vo.wav", nbytes, "music")
    assert storage.upload_limits()["music_max_bytes"] >= 500 * MIB


# ── a proxy-first original whose tab is gone ───────────────────────────────

class _PendingCur(_Cur):
    def __init__(self, key, nbytes, state="pending"):
        super().__init__()
        self.row = {"id": 31, "bytes": nbytes,
                    "meta": {"upload_state": state}}
        self.key = key

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT id, bytes, meta FROM assets"):
            self._all = [self.row] if params[1] == self.key else []
        else:
            super().execute(sql, params)


def test_a_pending_original_resumes_without_its_upload_id(monkeypatch, s3):
    """The upload id lived only in the closed tab; storage still lists it."""
    nbytes = 4 * 1024 ** 3
    ps = storage.part_size_for(nbytes)
    fake = s3(_S3(nbytes, ps, stored=range(1, 101)))
    fake.list_multipart_uploads = lambda Bucket, Prefix: {"Uploads": [
        {"Key": Prefix, "UploadId": "old", "Initiated": "2026-10-25T09:00"},
        {"Key": Prefix + "x", "UploadId": "other", "Initiated": "z"}]}
    key = "originals/7/big.mov"
    status, body, _ = _route(
        monkeypatch, video.resume_upload, _PendingCur(key, nbytes),
        json={"storage_key": key, "filename": "big.mov", "bytes": nbytes})
    assert status == 200, body
    assert body["upload_id"] == "old"
    assert len(body["completed_parts"]) == 100


def test_a_pending_original_with_nothing_stored_starts_on_the_same_key(
        monkeypatch, s3):
    nbytes = 400 * MIB
    fake = s3(_S3(nbytes, 8 * MIB, stored=[]))
    fake.list_multipart_uploads = lambda Bucket, Prefix: {"Uploads": []}
    fake.create_multipart_upload = lambda Bucket, Key, ContentType: {
        "UploadId": "fresh"}
    key = "originals/7/big.mov"
    status, body, _ = _route(
        monkeypatch, video.resume_upload, _PendingCur(key, nbytes),
        json={"storage_key": key, "filename": "big.mov", "bytes": nbytes})
    assert status == 200, body
    assert body["upload_id"] == "fresh" and body["storage_key"] == key
    assert body["completed_parts"] == []


def test_no_upload_id_is_refused_unless_the_key_is_a_pending_original(
        monkeypatch, s3):
    s3(_S3(1, 1, stored=[]))
    key = "originals/7/big.mov"
    for cur in (_Cur(), _PendingCur(key, 100 * MIB, state="ready")):
        status, _b, _ = _route(
            monkeypatch, video.resume_upload, cur,
            json={"storage_key": key, "filename": "big.mov",
                  "bytes": 100 * MIB})
        assert status == 400


def test_a_different_file_is_not_attached_to_the_pending_original(
        monkeypatch, s3):
    s3(_S3(1, 1, stored=[]))
    key = "originals/7/big.mov"
    status, body, _ = _route(
        monkeypatch, video.resume_upload, _PendingCur(key, 100 * MIB),
        json={"storage_key": key, "filename": "other.mov",
              "bytes": 90 * MIB})
    assert status == 409 and body["code"] == "different_file"
