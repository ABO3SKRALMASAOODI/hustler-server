"""Uploads that vanish, 6 Nov 2026. 2837 renamed a 7.8 GB file with 5.47 GB in
storage and started from zero; 2123 uploaded a file with no moov and got six
failed analyses and a stranded project; 18 of 23 accounts that lost an upload
left no trace. Pinned here on the routes and the intake.

    cd backend && python -m pytest tests/test_upload_silent_loss.py -q
"""
import contextlib
import os
import struct
import sys
from datetime import datetime, timezone

import pytest
from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mp4probe                                         # noqa: E402
import storage                                          # noqa: E402
from ops_loop import intake                             # noqa: E402
from routes import video                                # noqa: E402
from video_services import project_state                # noqa: E402

CONTENT = "ab" * 32


def box(btype, size):
    return struct.pack(">I4s", size, btype)


# 2123's DJI_0564.MP4, its real top level, read from storage on 6 Nov 2026.
CUT_SIZE = 15_728_640
CUT = {0: box(b"ftyp", 28), 28: box(b"wide", 8), 36: box(b"mdat", 590_450_793)}
FINISHED = {**CUT, 590_450_829: box(b"moov", 40_570)}


def reader(boxes, reads=None):
    def fetch(offset, length):
        if reads is not None:
            reads.append(offset)
        return (boxes.get(offset) or b"\0" * 8).ljust(length, b"\0")[:length]
    return fetch


def test_the_customers_cut_file_is_named_from_its_own_boxes():
    reads = []
    found = mp4probe.file_state(reader(CUT, reads), CUT_SIZE)
    assert found == {"state": "unfinished", "present": CUT_SIZE,
                     "expected": 590_450_829}
    assert reads == [0, 28, 36], "three header reads on any size of file"


def test_the_finished_file_is_complete():
    assert mp4probe.file_state(reader(FINISHED), 590_491_399)["state"] == \
        "complete"


def test_moov_first_is_complete_and_a_cut_moov_is_not():
    head = {0: box(b"ftyp", 32), 32: box(b"moov", 1000)}
    assert mp4probe.file_state(reader(head), 500_000)["state"] == "complete"
    assert mp4probe.file_state(reader(head), 500)["state"] == "unfinished"


def test_a_64_bit_mdat_is_walked_past():
    size = 8 * 1024 ** 3
    large = struct.pack(">I4sQ", 1, b"mdat", size)
    boxes = {0: box(b"ftyp", 32), 32: large, 32 + size: box(b"moov", 512)}
    assert mp4probe.file_state(reader(boxes), 32 + size + 512)["state"] == \
        "complete"
    assert mp4probe.file_state(reader(boxes), 32 + size // 2)["state"] == \
        "unfinished"


@pytest.mark.parametrize("head,total", [
    (b"\x1a\x45\xdf\xa3" + b"\0" * 12, 1000),      # WebM / Matroska
    (b"RIFF\0\0\0\0AVI LIST", 1000),               # AVI
    (b"\0" * 16, 1000),                            # zeros
    (b"", 0),
])
def test_what_it_does_not_recognise_it_does_not_judge(head, total):
    found = mp4probe.file_state(lambda o, n: head[:n], total)
    assert found["state"] == "unknown"


def test_a_read_failure_or_broken_box_is_no_proof_of_anything():
    assert mp4probe.file_state(lambda o, n: None, 1000)["state"] == "unknown"
    broken = {0: box(b"ftyp", 32), 32: struct.pack(">I4s", 3, b"mdat")}
    assert mp4probe.file_state(reader(broken), 1000)["state"] == "unknown"

    class Exploding:
        def get_range_at(self, *a):
            raise RuntimeError("store down")
    assert mp4probe.state_of_key(Exploding(), "k", 1000)["state"] == "unknown"


def test_the_sentence_says_what_arrived_and_what_to_do():
    said = mp4probe.unfinished_message("DJI_0564.MP4",
                                       mp4probe.file_state(reader(CUT), CUT_SIZE))
    assert "DJI_0564.MP4 isn't finished" in said
    assert "only 15 MB of about 563 MB arrived" in said
    assert "This project is ready for it" in said
    assert "moov" not in said and "atom" not in said


# ── the file keeps its place whatever it is called ──────────────────────────

class _S3:
    """R2's view: open multipart uploads and their parts."""

    def __init__(self, nbytes, open_keys=()):
        self.part_size = storage.part_size_for(nbytes)
        self.open = {k: f"up-{k}" for k in open_keys}
        self.created = []
        self.objects = {}

    def list_multipart_uploads(self, Bucket, Prefix):
        return {"Uploads": [{"Key": k, "UploadId": u, "Initiated": "1"}
                            for k, u in self.open.items()
                            if k.startswith(Prefix)]}

    def list_parts(self, Bucket, Key, UploadId, MaxParts=1000,
                   PartNumberMarker=None):
        nums = [n for n in range(1, 327) if n > (PartNumberMarker or 0)]
        page = nums[:MaxParts]
        return {"Parts": [{"PartNumber": n, "ETag": f'"e{n}"',
                           "Size": self.part_size} for n in page],
                "IsTruncated": len(nums) > len(page),
                "NextPartNumberMarker": page[-1] if page else None}

    def create_multipart_upload(self, Bucket, Key, ContentType):
        self.created.append(Key)
        return {"UploadId": "fresh"}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://r2.test/{Params['Key']}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise RuntimeError("404")
        return {"ContentLength": self.objects[Key]}


class _Cur:
    def __init__(self, assets=()):
        self.assets = set(assets)
        self._one = None
        self._all = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self._one = None
        if s.startswith("SELECT 1 FROM assets WHERE project_id = %s AND "
                        "storage_key"):
            self._one = {"?column?": 1} if params[1] in self.assets else None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


@pytest.fixture
def api(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(video.video_bp)
    events = []
    cur = _Cur()

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p, "chat_session_id": 55})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: False)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video, "record_client_event",
                        lambda u, p, kind, detail=None, **k:
                        events.append((kind, detail)))
    monkeypatch.setenv("S3_BUCKET", "b")
    fake = {}

    def use(s3, assets=()):
        fake["s3"] = s3
        cur.assets = set(assets)
        monkeypatch.setattr(storage, "store_client",
                            lambda *a, **k: fake["s3"])
        monkeypatch.setattr(storage, "store_bucket", lambda *a: "b")
        return s3

    def post(nbytes, **body):
        fn = video.create_upload.__wrapped__
        with app.test_request_context(
                f"/projects/5499/uploads", method="POST",
                json={"filename": "x.mp4", "bytes": nbytes, **body}):
            out = fn(2837, 5499)
        return (out[0].get_json(), out[1]) if isinstance(out, tuple) \
            else (out.get_json(), 200)

    return type("Api", (), {"use": staticmethod(use), "post": staticmethod(post),
                            "events": events})


NBYTES = 7_845_957_336


def stem(project=5499, kind="original", content=CONTENT, nbytes=NBYTES):
    return storage.content_key_stem(project, kind, content, nbytes)


def test_a_renamed_file_continues_from_what_storage_holds(api):
    """Event 34960: same 7,845,957,336 bytes, new name, upload_started."""
    s3 = api.use(_S3(NBYTES, [stem() + ".mp4"]))
    out, status = api.post(NBYTES, filename="PARTIDO CHACARITA-CHICAGO.mp4",
                           content_id=CONTENT)
    assert status == 200
    assert out["resumed"] is True and out["upload_id"] == f"up-{stem()}.mp4"
    assert out["storage_key"] == stem() + ".mp4"
    assert len(out["completed_parts"]) == 326
    assert out["bytes_done"] == 326 * s3.part_size == 5_469_372_416
    assert s3.created == [], "no second upload of the same bytes"
    kinds = [k for k, _ in api.events]
    assert kinds == ["upload_resumed"], "this is not a new upload"
    assert api.events[0][1]["by_content"] is True


def test_a_new_extension_is_still_the_same_upload(api):
    api.use(_S3(NBYTES, [stem() + ".mov"]))
    out, _ = api.post(NBYTES, filename="clip.mp4", content_id=CONTENT)
    assert out["resumed"] is True and out["storage_key"] == stem() + ".mov"


def test_a_first_upload_gets_the_key_of_its_content(api):
    s3 = api.use(_S3(NBYTES))
    out, _ = api.post(NBYTES, content_id=CONTENT)
    assert out["storage_key"] == stem() + ".mp4"
    assert s3.created == [stem() + ".mp4"]
    assert [k for k, _ in api.events] == ["upload_started"]


def test_the_same_bytes_in_another_project_or_size_are_another_upload():
    assert stem(5499) != stem(5500)
    assert stem(nbytes=NBYTES) != stem(nbytes=NBYTES + 1)
    assert stem().startswith("originals/5499/")
    assert stem(kind="clip").startswith("clips/5499/")


def test_every_part_arrived_and_the_tab_died_before_complete(api):
    s3 = api.use(_S3(NBYTES))
    s3.objects[stem() + ".mp4"] = NBYTES
    out, _ = api.post(NBYTES, content_id=CONTENT)
    assert out["assembled"] is True and out["upload_id"] is None
    assert s3.created == [] and [k for k, _ in api.events] == ["upload_resumed"]


def test_content_already_landed_here_gets_its_own_key(api):
    s3 = api.use(_S3(NBYTES), assets=[stem() + ".mp4"])
    out, _ = api.post(NBYTES, content_id=CONTENT)
    assert out["storage_key"] != stem() + ".mp4"
    assert len(s3.created) == 1


@pytest.mark.parametrize("content_id", [None, "", "short", "z" * 64])
def test_no_usable_identity_is_the_old_path(api, content_id):
    s3 = api.use(_S3(NBYTES, [stem() + ".mp4"]))
    out, _ = api.post(NBYTES, content_id=content_id)
    assert not out.get("resumed")
    assert s3.created and s3.created[0] != stem() + ".mp4"


def test_a_small_file_has_nothing_to_resume(api):
    s3 = api.use(_S3(1000))
    out, _ = api.post(1000, content_id=CONTENT)
    assert out["mode"] == "single" and s3.created == []


# ── a file that cannot open never becomes an asset ──────────────────────────

class _CompleteCur(_Cur):
    def __init__(self):
        super().__init__()
        self.inserted, self.enqueued = [], []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self._one = None
        if s.startswith("INSERT INTO assets"):
            self.inserted.append(params)
            self._one = {"id": 1}
        elif s.startswith("INSERT INTO video_jobs"):
            self.enqueued.append(params)
            self._one = {"id": 2}
        elif "chat_session_id FROM projects" in s:
            self._one = {"id": 5498, "chat_session_id": 9}


@pytest.fixture
def landing(monkeypatch):
    cur = _CompleteCur()
    events, deleted = [], []

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p, "chat_session_id": 9})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: False)
    monkeypatch.setattr(video, "_upload_wall", lambda *a: False)
    monkeypatch.setattr(video, "_trial_admission", lambda *a: False)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    state = type("S", (), {"size": 0, "boxes": {}})
    monkeypatch.setattr(video.storage, "head_bytes", lambda k: state.size)
    monkeypatch.setattr(video.storage, "get_range",
                        lambda k, n=64: b"\0\0\0\x1cftypisom")
    monkeypatch.setattr(video.storage, "content_matches_kind", lambda h, k: True)
    monkeypatch.setattr(video.storage, "get_range_at",
                        lambda k, o, n: reader(state.boxes)(o, n))
    monkeypatch.setattr(video.storage, "delete_key", deleted.append)
    monkeypatch.setattr(video, "record_client_event",
                        lambda u, p, kind, detail=None, **k:
                        events.append((kind, detail)))
    return type("L", (), {"cur": cur, "events": events, "deleted": deleted,
                           "state": state})


def test_a_cut_file_is_refused_with_nothing_left_behind(landing):
    landing.state.size, landing.state.boxes = CUT_SIZE, CUT
    out, status = video.complete_upload_core(2123, 5498, {
        "storage_key": "originals/5498/3c071d485b9f.mp4", "kind": "original",
        "filename": "DJI_0564.MP4"})
    assert status == 422
    assert out["code"] == "unfinished_file"
    assert out["bytes_present"] == CUT_SIZE
    assert out["bytes_expected"] == 590_450_829
    assert "isn't finished" in out["error"]
    assert landing.cur.inserted == [] and landing.cur.enqueued == []
    assert landing.deleted == ["originals/5498/3c071d485b9f.mp4"]
    kind, detail = landing.events[-1]
    assert kind == "upload_rejected" and detail["device_side"] is True


def test_the_finished_file_then_lands_in_the_same_project(landing):
    landing.state.size, landing.state.boxes = 590_491_399, FINISHED
    out, status = video.complete_upload_core(2123, 5498, {
        "storage_key": "originals/5498/next.mp4", "kind": "original",
        "filename": "DJI_0564.MP4"}, studio=True)
    assert status == 200 and out["asset_id"] == 1
    assert len(landing.cur.enqueued) == 1


def test_a_format_it_cannot_read_is_never_refused(landing):
    landing.state.size = 5000
    landing.state.boxes = {0: b"\x1a\x45\xdf\xa3" + b"\0" * 12}
    _, status = video.complete_upload_core(2123, 5498, {
        "storage_key": "originals/5498/a.mkv", "kind": "original",
        "filename": "a.mkv"}, studio=True)
    assert status == 200


# ── a permanent failure is not healed ───────────────────────────────────────

def test_the_failure_kind_of_the_latest_job_is_read_for_the_heal():
    class Cur:
        def execute(self, sql, params=()):
            self.sql = " ".join(sql.split())

        def fetchone(self):
            return {"id": 137254, "state": "failed", "progress": 0,
                    "error": "x", "failure_kind": "unfinished_file",
                    "updated_at": None, "active": False, "recent_count": 1}
    cur = Cur()
    state = project_state.index_job_state(cur, 5498, 40278)
    assert state["failure_kind"] == "unfinished_file"
    assert "result->'failure'->>'kind'" in cur.sql
    assert project_state.index_job_state(cur, 5498, None)["failure_kind"] is None


def test_opening_a_project_never_requeues_a_file_that_cannot_work():
    """Jobs 137254/5/6: the heal re-ran the same bytes on every open."""
    assert {"unfinished_file", "invalid_media"} <= \
        project_state.PERMANENT_INDEX_FAILURES
    import inspect
    source = inspect.getsource(video)
    healing = source.split('heal_reason = "last index job failed"')[0][-260:]
    assert 'ij.get("failure_kind") not in PERMANENT_INDEX_FAILURES' in healing


# ── nothing is lost silently ───────────────────────────────────────────────

def test_the_two_new_beacons_are_accepted():
    assert {"upload_progress", "upload_interrupted"} <= video.CLIENT_EVENT_KINDS


def test_a_bad_file_gets_no_alarm_but_a_ticket_says_it_happened():
    from ops_loop import recovery
    base = {"id": 1, "user_id": 7, "project_id": 8,
            "at": datetime(2026, 11, 6, tzinfo=timezone.utc)}
    row = dict(base, kind="upload_rejected", detail={
        "reason": "unfinished file", "device_side": True, "bytes": 15_728_640})
    assert intake._event_failure(row)["severity"] == "P1"
    gone = dict(base, kind="upload_failed", detail={
        "reason": "Your browser can't read this file any more",
        "file_gone": True})
    severity, look = recovery.plan(intake._event_failure(gone))
    assert severity == "P1", "on a ticket at once, with no alarm yet"
    assert look, "and looked at again: a customer left stuck is a P0"


def test_a_closed_ticket_can_be_reopened_with_its_reason_kept():
    from ops_loop import store

    class Cur:
        def __init__(self, status):
            self.row = {"id": 48, "status": status, "severity": "P1",
                        "wont_fix_reason": "device-side", "fix_sha": None,
                        "closed_at": "2026-10-30"}
            self.writes = []

        def execute(self, sql, params=None):
            self.writes.append((" ".join(sql.split()), params))

        def fetchone(self):
            return self.row

    cur = Cur("wont_fix")
    assert store.reopen(cur, 48, "7 customers, 8 failures")["id"] == 48
    sql, params = cur.writes[-1]
    assert sql.startswith("UPDATE tickets SET status = 'open'")
    assert "wont_fix_reason = NULL" in sql and "closed_at = NULL" in sql
    assert "device-side" in params[0], "why it was closed stays in the history"
    with pytest.raises(store.StoreError):
        store.reopen(Cur("open"), 48, "x")
    with pytest.raises(store.StoreError):
        store.reopen(Cur("wont_fix"), 48, "")
