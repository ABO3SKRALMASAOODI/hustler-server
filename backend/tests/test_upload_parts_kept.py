"""A failed multipart completion never throws the uploaded parts away.

CHM 2013 run (23 Oct 2026, project 4348, storage key
originals/4348/dcce5a5320da.mp4): upload_finish with one mistyped ETag in a
16-part, 437 MB parts list answered InvalidPart, the failure path aborted
the multipart upload, the retry with the corrected list answered
NoSuchUpload, and every part had to be PUT again after a fresh
upload_start.

    cd backend && python -m pytest tests/test_upload_parts_kept.py -q
"""
import contextlib
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage                                          # noqa: E402
from routes import video                                # noqa: E402


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class _S3:
    """One open multipart upload: the parts storage holds, and what each
    complete / abort / list call did."""

    def __init__(self, n=16, gone=False):
        self.parts = {i: f'"etag{i:02d}"' for i in range(1, n + 1)}
        self.gone, self.completed, self.aborted, self.calls = gone, None, False, []

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self.calls.append("complete")
        if self.gone or self.aborted:
            raise _ClientError("NoSuchUpload")
        got = MultipartUpload["Parts"]
        nums = [p["PartNumber"] for p in got]
        if nums != sorted(nums):
            raise _ClientError("InvalidPartOrder")
        for p in got:
            if self.parts.get(p["PartNumber"], "").strip('"') != \
                    str(p["ETag"]).strip('"'):
                raise _ClientError("InvalidPart")
        self.completed = nums

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.calls.append("abort")
        self.aborted = True

    def list_parts(self, Bucket, Key, UploadId, MaxParts=1000,
                   PartNumberMarker=None):
        self.calls.append("list")
        if self.gone or self.aborted:
            raise _ClientError("NoSuchUpload")
        nums = sorted(n for n in self.parts if n > (PartNumberMarker or 0))
        page = nums[:MaxParts]
        return {"Parts": [{"PartNumber": n, "ETag": self.parts[n],
                           "Size": 26 * 2 ** 20} for n in page],
                "IsTruncated": len(nums) > len(page),
                "NextPartNumberMarker": page[-1] if page else None}


def _listed(s3, slip=None, drop=()):
    out = []
    for n, tag in sorted(s3.parts.items()):
        if n in drop:
            continue
        out.append({"part_number": n,
                    "etag": tag.replace("etag", "etqg") if n == slip else tag})
    return out


def _use(monkeypatch, s3):
    monkeypatch.setattr(storage, "client", lambda *a, **k: s3)
    monkeypatch.setattr(storage, "bucket", lambda: "valmera-media")


def test_a_mistyped_etag_completes_from_storages_own_parts(monkeypatch):
    s3 = _S3()
    _use(monkeypatch, s3)
    res = storage.finish_multipart("originals/4348/dcce5a5320da.mp4", "u1",
                                   _listed(s3, slip=7))
    assert res == {"ok": True, "repaired": [7]}
    assert s3.completed == list(range(1, 17))
    assert "abort" not in s3.calls


def test_a_missing_part_keeps_the_upload_and_names_it(monkeypatch):
    s3 = _S3()
    del s3.parts[9]                      # its PUT never landed
    _use(monkeypatch, s3)
    res = storage.finish_multipart("k", "u1", _listed(_S3(), slip=None))
    assert not res["ok"] and not res["gone"]
    assert res["missing"] == [9] and res["stored"] == 15
    text = storage.finish_failure_text(res)
    assert "KEPT" in text and "part(s) 9" in text and "SAME" in text
    assert "abort" not in s3.calls and not s3.aborted
    # the caller PUTs part 9 and retries with the same upload id
    s3.parts[9] = '"etag09"'
    assert storage.finish_multipart("k", "u1", _listed(s3))["ok"]


def test_a_part_left_out_of_the_list_is_named(monkeypatch):
    # a slip AND a part left out: storage's ETags are not used, since the
    # list does not name the parts storage holds
    s3 = _S3()
    _use(monkeypatch, s3)
    res = storage.finish_multipart("k", "u1", _listed(s3, slip=3, drop=(16,)))
    assert not res["ok"] and res["unlisted"] == [16]
    assert "list every part" in storage.finish_failure_text(res)
    assert not s3.aborted


def test_a_gone_upload_says_start_again(monkeypatch):
    s3 = _S3(gone=True)
    _use(monkeypatch, s3)
    res = storage.finish_multipart("k", "u1", _listed(_S3()))
    assert res["gone"] and "upload_start" in storage.finish_failure_text(res)


def test_a_transient_failure_never_aborts(monkeypatch):
    s3 = _S3()

    def flaky(**kw):
        raise TimeoutError("read timeout")
    s3.complete_multipart_upload = flaky
    _use(monkeypatch, s3)
    res = storage.finish_multipart("k", "u1", _listed(s3))
    assert not res["ok"] and not res["gone"]
    assert not s3.aborted


class _UploadCur:
    def __init__(self):
        self._one, self._next, self.enqueued = None, 31600, []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT id FROM projects WHERE id"):
            self._one = {"id": params[0]}
        elif s.startswith("INSERT INTO assets"):
            self._next += 1
            self._one = {"id": self._next}
        elif s.startswith("INSERT INTO video_jobs"):
            self._next += 1
            self.enqueued.append(params[2])
            self._one = {"id": self._next}
        else:
            self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []


def _core(monkeypatch, s3, parts):
    cur, events = _UploadCur(), []

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    _use(monkeypatch, s3)
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p, "chat_session_id": 55})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: False)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a[2]))
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video.storage, "head_bytes",
                        lambda k, fast=False: 437_539_635 if s3.completed
                        else None)
    monkeypatch.setattr(video.storage, "max_upload_bytes", lambda: 10 ** 11)
    monkeypatch.setattr(video.storage, "get_range",
                        lambda k, n=64: b"\x00" * 4 + b"ftypisom")
    monkeypatch.setattr(video.storage, "content_matches_kind",
                        lambda h, k: True)
    monkeypatch.setattr(video.storage, "PROXY_FIRST_UPLOADS", False)
    out, status = video.complete_upload_core(60, 4348, {
        "storage_key": "originals/4348/dcce5a5320da.mp4", "kind": "original",
        "filename": "chm2013.mp4", "upload_id": "u1", "parts": parts})
    return out, status, cur, events


def test_upload_finish_with_a_slip_is_indexed_not_aborted(monkeypatch):
    s3 = _S3()
    out, status, cur, events = _core(monkeypatch, s3, _listed(s3, slip=4))
    assert status == 200, out
    assert out["parts_repaired"] == [4]
    assert cur.enqueued == ["index"]
    assert "upload_parts_repaired" in events and not s3.aborted


def test_upload_finish_missing_a_part_is_kept_for_the_retry(monkeypatch):
    s3 = _S3()
    del s3.parts[12]
    out, status, cur, _ = _core(monkeypatch, s3, _listed(_S3()))
    assert status == 400
    assert out["upload_kept"] is True and out["missing_parts"] == [12]
    assert "SAME storage_key and upload_id" in out["error"]
    assert not s3.aborted and cur.enqueued == []
