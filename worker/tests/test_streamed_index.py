"""Large completed uploads must index without staging their entire original."""
import hashlib
import io
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import config
import indexer
import media
import remote
import storage


class Body(io.BytesIO):
    def iter_chunks(self, chunk_size):
        assert chunk_size <= 8 * 1024 ** 2
        while chunk := self.read(chunk_size):
            yield chunk


def object_client(monkeypatch, data, declared=None):
    body = Body(data)
    monkeypatch.setattr(storage, "client", lambda: SimpleNamespace(
        get_object=lambda **kw: {"Body": body, "ContentLength": (
            len(data) if declared is None else declared)}))
    return body


def test_streamed_hash_matches_full_file_and_closes_body(monkeypatch):
    data = b"source bytes" * 900000
    body = object_client(monkeypatch, data)
    progress = []
    assert storage.sha256_object("key", len(data), progress.append) == (
        hashlib.sha256(data).hexdigest())
    assert body.closed and progress == sorted(progress) and progress[-1] == 1


@pytest.mark.parametrize("actual,declared,expected", [(3, 4, 4), (3, 3, 4), (5, 4, 4)])
def test_incomplete_or_changed_upload_cannot_enter_hash_cache(
        monkeypatch, actual, declared, expected):
    body = object_client(monkeypatch, b"x" * actual, declared)
    with pytest.raises(RuntimeError, match="upload"):
        storage.sha256_object("key", expected)
    assert body.closed


def test_cancelled_hash_closes_storage_connection(monkeypatch):
    body = object_client(monkeypatch, b"source")
    checks = iter([False, True])
    with pytest.raises(RuntimeError, match="cancelled"):
        storage.sha256_object("key", 6, cancelled_cb=lambda: next(checks))
    assert body.closed


@pytest.mark.parametrize("kind,size,allowed", [
    ("original", 9 * 1024 ** 3, True),
    ("video_clip", 14 * 1024 ** 3, True),
    ("music", 9 * 1024 ** 3, False),
    (None, 9 * 1024 ** 3, False),
    ("original", 20 * 1024 ** 3, True),
    # Above INDEX_STREAM_MAX_GB the product's upload cap is the ceiling:
    # a 60 GB OBS recording (1922) streams too (25 Oct 2026).
    ("original", 20 * 1024 ** 3 + 1, True),
    ("original", int(config.MAX_UPLOAD_GB * 1024 ** 3), True),
    ("original", int(config.MAX_UPLOAD_GB * 1024 ** 3) + 1, False),
    ("original", 0, False),
])
def test_only_known_bounded_video_uploads_use_streaming_admission(
        monkeypatch, kind, size, allowed):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://example.invalid")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES", {"index"})
    monkeypatch.setattr(config, "CLOUDFLARE_MAX_INDEX_INPUT_BYTES", 8 * 1024 ** 3)
    assert remote._cloudflare_selected({"id": 1, "type": "index",
        "_execution_shape": {"asset_kind": kind, "total_bytes": size}}) is allowed


def test_streaming_still_refuses_insufficient_scratch_before_reading(monkeypatch):
    monkeypatch.setattr(storage, "presign_get", lambda *a, **kw: "https://example.invalid")
    monkeypatch.setattr(media, "probe", lambda src: {"duration": 10800})
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: 1024 ** 3)
    monkeypatch.setattr(storage, "sha256_object", lambda *a, **kw: pytest.fail("read too early"))
    with pytest.raises(storage.WorkdirTooSmall):
        indexer._stream_index_source(None, {"id": 1}, {"storage_key": "key"}, ".")


def test_streaming_a_longer_source_plans_for_the_first_three_hours(monkeypatch):
    """Over the limit is no longer a refusal (2530: 3.16 h, four attempts):
    the index covers the first MAX_DURATION_S, and scratch is planned for
    that span, before anything is read in full."""
    monkeypatch.setattr(storage, "presign_get", lambda *a, **kw: "https://example.invalid")
    monkeypatch.setattr(media, "probe", lambda src: {
        "duration": config.MAX_DURATION_S + 600, "has_audio": True})
    planned = []
    monkeypatch.setattr(indexer, "_streamed_scratch_plan",
                        lambda duration, has_audio, workdir:
                        planned.append(duration) or (4000, 1))
    monkeypatch.setattr(storage, "sha256_object", lambda *a, **kw: "sha")
    src, sha, info, kbps = indexer._stream_index_source(
        None, {"id": 1}, {"storage_key": "key", "bytes": 1}, ".")
    assert planned == [config.MAX_DURATION_S]
    assert info["duration"] == config.MAX_DURATION_S
    assert info["limit_s"] == config.MAX_DURATION_S
    assert info["source_duration"] == config.MAX_DURATION_S + 600


def test_signed_media_urls_are_removed_before_failure_logs_are_truncated():
    log = "Cannot open https://bucket.invalid/private.mp4?X-Amz-Signature=secret"
    assert "secret" not in media.stderr_tail([log], limit=35)
    assert "[media URL]" in media.stderr_tail([log])


def test_streamed_proxy_has_a_real_encoder_bitrate_bound(monkeypatch):
    commands = []
    monkeypatch.setattr(media, "run", lambda cmd, **kw: commands.append(cmd))
    media._encode_proxy("https://example.invalid/video", "proxy.mp4", 30,
                        False, True, maxrate_kbps=4000)
    cmd = commands[0]
    assert cmd[cmd.index("-maxrate") + 1] == "4000k"
    assert cmd[cmd.index("-bufsize") + 1] == "8000k"


def test_lost_lease_stops_hashing_and_closes_the_read(monkeypatch):
    body = object_client(monkeypatch, b"source")

    def lost_lease(fraction):
        raise indexer.dbx.JobLeaseLost("lost lease")

    with pytest.raises(indexer.dbx.JobLeaseLost):
        storage.sha256_object("key", 6, progress_cb=lost_lease)
    assert body.closed


@pytest.fixture
def remote_video(tmp_path):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("requires real ffmpeg")
    source = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
        "-shortest", str(source)], check=True, capture_output=True)
    data, reads = source.read_bytes(), []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            start, end = 0, len(data) - 1
            range_ = self.headers.get("Range")
            if range_:
                start_, end_ = range_.removeprefix("bytes=").split("-")
                start, end = int(start_), int(end_) if end_ else end
            reads.append((start, end))
            self.send_response(206 if range_ else 200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(end - start + 1))
            if range_:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            self.wfile.write(data[start:end + 1])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/source.mp4", data, reads
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("kind", ["original", "video_clip"])
def test_real_streamed_video_finishes_index_without_original_on_disk(
        monkeypatch, tmp_path, remote_video, kind):
    url, data, reads = remote_video
    monkeypatch.setattr(config, "CLOUDFLARE_MAX_INDEX_INPUT_BYTES", 1)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.setattr(storage, "presign_get", lambda *a, **kw: url)
    object_client(monkeypatch, data)
    monkeypatch.setattr(storage, "download_to", lambda *a, **kw: pytest.fail("staged original"))
    monkeypatch.setattr(storage, "upload_file", lambda *a, **kw: None)
    monkeypatch.setattr(indexer, "_finish_setup", lambda *a, **kw: None)
    monkeypatch.setattr(indexer, "_transcribe_wav", lambda *a, **kw: ([], [], "en"))
    monkeypatch.setattr(indexer.motion_judge, "analyze_video", lambda *a: None)
    monkeypatch.setattr(indexer.source_layout, "analyze", lambda *a, **kw: None)
    monkeypatch.setattr(indexer.visual_index, "build", lambda *a, **kw: None)
    monkeypatch.setattr(indexer.spatial, "augment_with_shot_frames", lambda *a, **kw: None)
    monkeypatch.setattr(indexer, "_build_and_upload_tiles", lambda *a, **kw: (["tile"], 2, None))
    import sidecars
    monkeypatch.setattr(sidecars, "prefetch_after_index", lambda *a, **kw: None)

    class Db:
        def __init__(self):
            self.index = None
            self.progress = []

        def run(self, fn, *args, **kw):
            if fn is indexer.dbx.get_asset:
                return {"id": 3, "kind": kind, "storage_key": "original/key.mp4", "bytes": len(data)}
            if fn is indexer.dbx.get_project:
                return {"chat_session_id": 5}
            if fn is indexer.dbx.set_progress:
                self.progress.append(args[1])
                return True
            if fn is indexer.dbx.upsert_index:
                self.index = args[2]
            if fn is indexer.dbx.insert_asset:
                return 10
            return None

    db = Db()
    result = indexer.run_index_job(db, {"id": 7, "project_id": 2,
        "user_id": 1, "payload": {"asset_id": 3}})
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["cached"] is False
    assert db.index["video"]["width"] == 640
    assert db.index["video"]["duration"] == pytest.approx(2, abs=.1)
    assert db.index["tile_keys"] == ["tile"]
    assert reads and db.progress == sorted(db.progress)
    assert not (tmp_path / "scratch" / "index_7").exists()
