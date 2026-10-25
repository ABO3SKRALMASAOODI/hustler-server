"""Large uploads index from object storage within the batch lane's real disk.

25 Oct 2026, project 4746: a paying customer's 7,585,435,149-byte stream VOD
failed three index jobs in 20 seconds with "this job needs about 15.5 GB of
scratch space ... and only 11.8 GB is free". The source sat between the
download ceiling a 20 GB standard-4 could hold if all of its disk were
scratch (8 GiB) and what it can hold beside its ~8 GB image (11.8 GB free),
so it was downloaded and refused. Each failure posted the same apology.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import config
import indexer
import main as worker_main
import media
import remote
import storage

GIB = 1024 ** 3
# What a fresh batch (standard-4) container reports free under /tmp/valmera:
# the "11.8 GB" of jobs 108884-108886 (the message divides by 1024 ** 3).
BATCH_FREE = int(11.8 * GIB)
THREE_HOURS = 3 * 3600.0
VOD_BYTES = 7_585_435_149


@pytest.fixture
def disk(monkeypatch):
    """A workdir on a real disk with a settable amount of free space."""
    state = {"free": BATCH_FREE, "fstype": "overlay"}
    monkeypatch.setattr(storage, "free_workdir_bytes",
                        lambda path=None: state["free"])
    monkeypatch.setattr(storage, "workdir_fstype",
                        lambda path=None: state["fstype"])
    monkeypatch.setattr(storage, "evict_source_cache", lambda *a, **kw: 0)
    return state


# ── which uploads stream ─────────────────────────────────────────────────

@pytest.mark.parametrize("size", [VOD_BYTES, 5 * GIB, 8 * GIB, 14 * GIB,
                                  20 * GIB])
def test_large_originals_and_clips_index_from_storage(size):
    assert config.index_source_should_stream("original", size)
    assert config.index_source_should_stream("video_clip", size)


def test_small_uploads_and_non_video_keep_the_local_download():
    assert not config.index_source_should_stream("original", 1 * GIB)
    assert not config.index_source_should_stream("original", 0)
    assert not config.index_source_should_stream("music", 9 * GIB)
    assert not config.index_source_should_stream(None, 9 * GIB)


def test_no_upload_size_is_too_big_to_download_and_too_small_to_stream():
    """The gap that failed project 4746, closed for every size up to 20 GiB:
    each size either streams or fits a fresh batch container as a download
    (the source plus WORKDIR_HEADROOM for its proxy and wav)."""
    size = 64 * 1024 ** 2
    while size <= 20 * GIB:
        streamed = config.index_source_should_stream("original", size)
        assert streamed or size * config.WORKDIR_HEADROOM <= BATCH_FREE, size
        size += 64 * 1024 ** 2


def test_the_customer_vod_is_admitted_to_cloudflare(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://x.invalid")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES", {"index"})
    shape = {"assets": 1, "max_bytes": VOD_BYTES, "asset_kind": "original",
             "total_bytes": VOD_BYTES, "max_duration_s": 0.0}
    assert remote._cloudflare_selected(
        {"id": 108884, "type": "index", "_execution_shape": shape})
    twenty = dict(shape, total_bytes=20 * GIB, max_bytes=20 * GIB)
    assert remote._cloudflare_selected(
        {"id": 1, "type": "index", "_execution_shape": twenty})


# ── the scratch budget is what the index writes ──────────────────────────

def test_scratch_need_counts_written_outputs_not_the_source():
    need = config.index_stream_scratch_bytes(THREE_HOURS, True, 4000)
    proxy = THREE_HOURS * (4000 + 128) * 125 + 2 * 4000 * 125
    audio = 2 * THREE_HOURS * 32000          # 16-kHz wav + its FLAC
    frames = 3 * config.INDEX_STREAM_FRAME_BYTES_PER_HOUR
    assert need == int(proxy + audio + frames
                       + config.INDEX_STREAM_FIXED_SCRATCH_BYTES)
    # About 8.4 GB for three hours at the proxy's bitrate CEILING, whatever
    # the original weighs (real long-source proxies run p99 ~1 Mbps).
    assert 8.2e9 < need < 8.6e9
    silent = config.index_stream_scratch_bytes(THREE_HOURS, False, 4000)
    assert silent < need


@pytest.mark.parametrize("size", [VOD_BYTES, 14 * GIB, 20 * GIB])
def test_a_three_hour_upload_of_any_size_fits_a_batch_container(disk, size):
    """Large-file budget: up to 20-GB originals at the 3-hour limit keep the
    full proxy rate and stay well under the lane's free disk."""
    assert config.index_source_should_stream("original", size)
    kbps, need = indexer._streamed_scratch_plan(THREE_HOURS, True, "/scratch")
    assert kbps == config.INDEX_STREAM_PROXY_MAXRATE_KBPS
    # A worst-case bound (every byte at the bitrate ceiling, margin included)
    # that still leaves the lane 2.5 GB.
    assert need < BATCH_FREE - 2.5e9
    # The old rule (source x 2.2) asked for 15.5 GiB for the 7.6-GB VOD.
    assert need < 0.65 * VOD_BYTES * config.WORKDIR_HEADROOM
    two_hours = indexer._streamed_scratch_plan(7200.0, True, "/scratch")[1]
    assert two_hours < 0.6 * BATCH_FREE


def test_a_tight_lane_lowers_the_proxy_rate_instead_of_refusing(disk):
    disk["free"] = 7 * GIB
    kbps, need = indexer._streamed_scratch_plan(THREE_HOURS, True, "/scratch")
    assert config.INDEX_STREAM_PROXY_MIN_KBPS <= kbps \
        < config.INDEX_STREAM_PROXY_MAXRATE_KBPS
    assert need <= disk["free"]
    # The highest rate that fits: one more kbps would not.
    over = int(config.index_stream_scratch_bytes(THREE_HOURS, True, kbps + 2)
               * config.INDEX_STREAM_SCRATCH_MARGIN)
    assert over > disk["free"] or kbps + 2 > config.INDEX_STREAM_PROXY_MAXRATE_KBPS


def test_a_lane_without_room_still_refuses_before_reading(disk):
    disk["free"] = 2 * GIB
    with pytest.raises(storage.WorkdirTooSmall, match="scratch space"):
        indexer._streamed_scratch_plan(THREE_HOURS, True, "/scratch")


def test_cloud_run_ram_scratch_guard_is_kept(disk):
    """On a memory-backed workdir the same bytes are RAM: the plan adds a
    reserve for the decoders, encoder and model, and still refuses when the
    container's memory cannot hold it."""
    on_disk = indexer._streamed_scratch_plan(3600.0, True, "/scratch")[1]
    disk["fstype"] = "tmpfs"
    in_ram = indexer._streamed_scratch_plan(3600.0, True, "/scratch")[1]
    assert in_ram == on_disk + config.INDEX_STREAM_RAM_RESERVE_BYTES
    disk["free"] = 4 * GIB
    with pytest.raises(storage.WorkdirTooSmall, match="Cloud Run"):
        indexer._streamed_scratch_plan(THREE_HOURS, True, "/scratch")


def test_the_render_source_cache_is_reclaimed_before_anything_is_refused(
        monkeypatch, tmp_path):
    cache = tmp_path / "srccache"
    cache.mkdir()
    (cache / "old_source.mp4").write_bytes(b"x" * 4096)
    (cache / "busy.mp4.partab12").write_bytes(b"x" * 16)
    leased = cache / "leased.mp4"
    leased.write_bytes(b"y" * 1024)
    os.link(leased, tmp_path / "job_hardlink.mp4")
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "workdir_fstype", lambda path=None: "overlay")
    full = int(config.index_stream_scratch_bytes(THREE_HOURS, True, 4000)
               * config.INDEX_STREAM_SCRATCH_MARGIN)
    # The cache is what stands between the job and its full-rate proxy.
    monkeypatch.setattr(storage, "free_workdir_bytes", lambda path=None: (
        full - 1 if (cache / "old_source.mp4").exists() else full + GIB))
    kbps, _need = indexer._streamed_scratch_plan(THREE_HOURS, True, "/s")
    assert kbps == config.INDEX_STREAM_PROXY_MAXRATE_KBPS
    assert sorted(p.name for p in cache.iterdir()) == ["busy.mp4.partab12"]
    # A render still holding the cached file keeps its bytes.
    assert (tmp_path / "job_hardlink.mp4").read_bytes() == b"y" * 1024


def test_evicting_reports_only_bytes_actually_released(tmp_path):
    cache = tmp_path / "srccache"
    cache.mkdir()
    (cache / "a.mp4").write_bytes(b"a" * 100)
    (cache / "b.mp4").write_bytes(b"b" * 50)
    os.link(cache / "b.mp4", tmp_path / "held.mp4")
    assert storage.evict_source_cache(str(cache)) == 100
    assert storage.evict_source_cache(str(tmp_path / "missing")) == 0


def test_stream_plan_drives_the_proxy_encoder(monkeypatch, disk):
    monkeypatch.setattr(storage, "presign_get", lambda *a, **kw: "https://r2/x")
    monkeypatch.setattr(media, "probe", lambda src: {
        "duration": THREE_HOURS, "has_audio": True})
    monkeypatch.setattr(storage, "sha256_object", lambda *a, **kw: "f" * 64)
    disk["free"] = 7 * GIB
    src, sha, info, kbps = indexer._stream_index_source(
        None, {"id": 1}, {"storage_key": "originals/4746/a.mp4",
                          "bytes": VOD_BYTES}, "/scratch")
    assert (src, sha) == ("https://r2/x", "f" * 64)
    assert kbps == indexer._streamed_scratch_plan(
        THREE_HOURS, True, "/scratch")[0] < 4000


# ── routing: large indexes run where the disk is ─────────────────────────

# Cloudflare Containers instance types (vCPU, GiB memory, GB disk).
_DISK_GB = {"lite": 2, "basic": 4, "standard-1": 8, "standard-2": 12,
            "standard-3": 16, "standard-4": 20}


def test_index_runs_only_on_the_lane_with_the_most_disk():
    raw = (pathlib.Path(__file__).resolve().parents[1]
           / "cloudflare" / "wrangler.jsonc").read_text()
    containers = json.loads(re.sub(r"(?m)^\s*//.*$", "", raw))["containers"]
    disk_of = {c["class_name"]: _DISK_GB[c["instance_type"]]
               for c in containers}
    assert remote._cloudflare_lane("index") == "batch"
    assert disk_of["ValmeraBatch"] == max(disk_of.values()) == 20
    # Index never fails over to a smaller lane.
    assert remote._cloudflare_failover_lanes({"type": "index"}) == ()


# ── streaming input ──────────────────────────────────────────────────────

def test_http_inputs_resume_after_a_dropped_connection():
    url = "https://bucket.r2.invalid/originals/4746/a.mp4?X-Amz-Signature=s"
    args = media.input_args(url)
    assert args[-2:] == ["-i", url]
    for flag in ("-reconnect", "-reconnect_on_network_error", "-rw_timeout"):
        assert flag in args
    assert media.input_args("/tmp/valmera/src.mp4") == [
        "-i", "/tmp/valmera/src.mp4"]


@pytest.fixture
def flaky_remote_video(tmp_path):
    """An mp4 served with Range support whose first long response is cut
    off half-way, the way a dropped connection ends a long read. The moov
    atom is at the front (as in a phone or OBS recording), so the reader is
    inside that response when it ends."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("requires real ffmpeg")
    source = tmp_path / "original.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=25:duration=4", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    str(source)],
                   check=True, capture_output=True)
    data = source.read_bytes()
    log = {"reads": [], "cut": False}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            start, end = 0, len(data) - 1
            range_ = self.headers.get("Range")
            if range_:
                a, b = range_.removeprefix("bytes=").split("-")
                start, end = int(a), int(b) if b else end
            body = data[start:end + 1]
            with lock:
                log["reads"].append((start, end))
                cut = not log["cut"] and len(body) > 64 * 1024
                log["cut"] = log["cut"] or cut
            self.send_response(206 if range_ else 200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            if range_:
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            if cut:
                self.wfile.write(body[:len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/o.mp4", data, log
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_streamed_wav_and_proxy_survive_a_dropped_connection(
        flaky_remote_video, tmp_path):
    url, data, log = flaky_remote_video
    work = tmp_path / "work"
    work.mkdir()
    wav = work / "audio.wav"
    media.extract_wav(url, str(wav))
    assert log["cut"], "the first read was not interrupted"
    assert media.probe_audio_duration(str(wav)) == pytest.approx(4, abs=0.1)
    proxy = work / "proxy.mp4"
    log["cut"] = False                     # drop the proxy's read too
    media.make_proxy(url, str(proxy), 25.0, False, True, duration=4.0,
                     maxrate_kbps=1500)
    assert log["cut"]
    got = media.probe(str(proxy))
    assert got["video_duration"] == pytest.approx(4, abs=0.15)
    assert got["duration"] == pytest.approx(4, abs=0.15)
    assert got["height"] == min(config.PROXY_HEIGHT, 360)
    # Only the outputs were written; the original never touched the disk.
    assert sorted(p.name for p in work.iterdir()) == ["audio.wav", "proxy.mp4"]
    assert all(p.stat().st_size < len(data) for p in work.iterdir()
               if p.name == "proxy.mp4")
    assert any(start > 0 for start, _end in log["reads"])


# ── one honest note per failed upload analysis ───────────────────────────

class _NotesDb:
    """add_job_failure_message's dedupe, modelled in memory."""

    def __init__(self):
        self.messages = []

    def run(self, fn, *args):
        if fn is worker_main.dbx.get_project:
            return {"chat_session_id": 4851}
        if fn is worker_main.dbx.add_job_failure_message:
            session, content, job_id, *rest = args
            asset = rest[1] if len(rest) > 1 else None
            for m in self.messages:
                if m["job"] == job_id or (asset is not None
                                          and m["asset"] == asset
                                          and m["content"] == content):
                    return None
            self.messages.append({"session": session, "content": content,
                                  "job": job_id, "asset": asset,
                                  "kind": rest[0] if rest else "job_failed"})
            return len(self.messages)
        raise AssertionError(fn)


def test_three_failed_retries_of_one_upload_post_one_kind_note():
    db = _NotesDb()
    err = storage.WorkdirTooSmall(
        "this job needs about 15.5 GB of scratch space in /tmp/valmera/"
        "index_108884 and only 11.8 GB is free. On Cloud Run that space is "
        "RAM: raise the executor's --memory (32Gi is the maximum) or mount a "
        "volume for WORKER_TMP_DIR. See worker/DEPLOY_EXECUTOR.md.")
    for job_id in (108884, 108885, 108886):
        worker_main._notify_failure(db, {
            "id": job_id, "type": "index", "project_id": 4746,
            "payload": {"asset_id": 34194, "reindex": False}}, err)
    assert len(db.messages) == 1
    note = db.messages[0]
    assert (note["job"], note["asset"]) == (108884, 34194)
    text = note["content"]
    assert "nothing is wrong with your file" in text
    assert "on our side" in text and "we're on it" in text
    for leaked in ("GB", "scratch", "Cloud Run", "--memory", "DEPLOY",
                   "service-side fix", "format"):
        assert leaked not in text
    # A different upload in the same project is a different analysis.
    worker_main._notify_failure(db, {
        "id": 108890, "type": "index", "project_id": 4746,
        "payload": {"asset_id": 34201}}, err)
    assert len(db.messages) == 2
    # A later retry that fails for a reason the customer can act on is new
    # information, told once.
    long = RuntimeError("Video is 4.5h — the limit is 3h")
    for job_id in (108891, 108892):
        worker_main._notify_failure(db, {
            "id": job_id, "type": "index", "project_id": 4746,
            "payload": {"asset_id": 34194}}, long)
    assert len(db.messages) == 3
    assert db.messages[2]["job"] == 108891
    told = db.messages[2]["content"]
    assert "about 4.5 hours" in told and "3 hours" in told
    assert "different format" not in told and "uploading it again" not in told


def test_index_failure_note_sql_dedupes_on_the_asset():
    class Cur:
        def __init__(self):
            self.sql = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.sql.append((" ".join(sql.split()), params))

        def fetchone(self):
            return None

    class Conn:
        def __init__(self):
            self.cur = Cur()

        def cursor(self):
            return self.cur

    conn = Conn()
    assert worker_main.dbx.add_job_failure_message(
        conn, 4851, "note", 108885, "job_failed", 34194) is None
    sql, params = conn.cur.sql[0]
    assert "index-failure-message:" in sql and "pg_advisory_xact_lock" in sql
    assert "sibling.type = 'index'" in sql
    assert "sibling.payload->>'asset_id' = %s" in sql
    assert "existing.content = %s" in sql
    assert params[:2] == ("4851", "34194") and params[-1] == "34194"
    assert params[6] == "108885" and params[3] == params[-2] == "note"
    # Non-index notes keep the per-job statement unchanged.
    conn = Conn()
    worker_main.dbx.add_job_failure_message(conn, 4851, "note", 7)
    assert "sibling" not in conn.cur.sql[0][0]
    # The reaper's backfill never re-tells a retried upload either.
    conn = Conn()
    conn.cur.fetchall = lambda: []
    worker_main.dbx.unnotified_terminal_failures(conn)
    scan = conn.cur.sql[0][0]
    assert "sibling.payload->>'asset_id' = j.payload->>'asset_id'" in scan


def test_died_index_reaper_note_is_scoped_to_its_upload():
    assert worker_main._index_note_scope(
        {"type": "index", "payload": {"asset_id": "34194"}}, "job_died") == (
        "job_died", 34194)
    assert worker_main._index_note_scope(
        {"type": "final", "payload": {"asset_id": 1}}, "job_died") == ()
    assert worker_main._index_note_scope(
        {"type": "index", "payload": {}}, "job_failed") == ()
