"""Regression cases from the September 8 subscriber incident audit."""

import copy
import pytest

import config
import db
import media
import remote
import renderer
from schemas import default_edl


def test_continuation_resets_admission_budget_but_keeps_edit_state():
    executed = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql, args): executed.append((sql, args))
        def fetchone(self):
            return {"id": 43} if "INSERT" in executed[-1][0] else None
    class Conn:
        def cursor(self): return Cursor()
    payload = {"cloudflare_busy_deferrals": 5, "cloudflare_busy_deferred": True,
               "execution_policy": "redesign", "message_id": 12,
               "continuation_state": {"versions_written": [4, 5]}}
    before = copy.deepcopy(payload)
    assert db.enqueue_agent_continuation(Conn(), 7, 3, 42, 6, payload) == 43
    stored = executed[-1][1][-1].adapted
    assert "cloudflare_busy_deferrals" not in stored
    assert "cloudflare_busy_deferred" not in stored
    assert stored["continuation_state"] == payload["continuation_state"]
    assert payload == before


def test_capacity_wait_reuses_unlaunched_identity_and_checks_lease(monkeypatch):
    now = [0.0]
    seen = []
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep", lambda delay: now.__setitem__(0, now[0]+delay))
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "CLOUDFLARE_BUSY_WAIT_S", 20)
    class Probe:
        def run(self, fn, *args):
            assert fn is db.lease_is_current
            return True
        def reset(self): pass
    monkeypatch.setattr(db, "Db", Probe)
    def launch(job):
        seen.append(remote._cloudflare_call_id(job))
        if len(seen) < 3: raise remote.CloudflareCapacityBusy("shard is busy")
        return {"done": True}
    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    job = {"id": 42, "project_id": 7, "type": "mcp_tool", "total_claims": 2}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"done": True}
    assert len(seen) == 3 and len(set(seen)) == 1
    assert now[0] == 6


def test_capacity_wait_does_not_retry_ambiguous_or_terminal_launch(monkeypatch):
    calls = []
    def launch(job):
        calls.append(job)
        raise remote.RemoteExecutorError("connection lost after acceptance")
    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    with pytest.raises(remote.RemoteExecutorError):
        remote._run_cloudflare_with_capacity_wait({"id": 42})
    assert len(calls) == 1


@pytest.mark.parametrize("lease", [True, False])
def test_capacity_wait_is_bounded_and_stops_on_lost_ownership(monkeypatch, lease):
    now, calls = [0.0], []
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep", lambda t: now.__setitem__(0, now[0]+t))
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "CLOUDFLARE_BUSY_WAIT_S", 5)
    class Probe:
        def run(self, *args): return lease
        def reset(self): pass
    monkeypatch.setattr(db, "Db", Probe)
    def busy(job):
        calls.append(job)
        raise remote.CloudflareCapacityBusy("busy")
    monkeypatch.setattr(remote, "_run_cloudflare", busy)
    error = remote.CloudflareCapacityBusy if lease else db.JobLeaseLost
    with pytest.raises(error):
        remote._run_cloudflare_with_capacity_wait({"id": 42, "total_claims": 1})
    assert now[0] == (5 if lease else 2)
    assert len(calls) == (3 if lease else 1)


def test_canvas_proof_keeps_the_partially_visible_fourth_insert():
    import stitch
    from timeline import Timeline
    edl = default_edl(0)
    edl.update(keep=[], canvas={"width": 1920, "height": 1080}, inserts=[
        {"id": f"ins{i}", "asset_key": f"clip{i}.mp4", "kind": "video",
         "at_output_s": 0.0, "duration_s": duration}
        for i, duration in enumerate([3, 3, 4, 8, 4.1, 6.9, 10, 5.99, 5.76, 9.4])])
    window = stitch.window_edl(edl, Timeline([], edl["inserts"]), 0, 17.89)
    assert [i["duration_s"] for i in window["inserts"]] == [3, 3, 4, 7.89]
    assert Timeline([], window["inserts"]).out_duration == pytest.approx(17.89)


def test_large_short_original_is_streamed_only_on_cloudflare(monkeypatch):
    asset = {"duration_s": 266.821, "bytes": 3271642709}
    monkeypatch.setenv("EXECUTOR_PROVIDER", "cloudflare")
    assert renderer._stream_cloudflare_source(asset)
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    assert not renderer._stream_cloudflare_source(asset)


def test_large_project_admission_counts_staged_assets(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://example.com")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_TYPES", {"preview", "index", "filmstrip"})
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    job = {"id": 42, "type": "preview", "_execution_shape": {
        "total_bytes": 4705026827, "original_bytes": 3271642709,
        "max_duration_s": 266.821}}
    assert remote._cloudflare_selected(job)
    assert not remote._cloudflare_selected({**job, "type": "index"})
    assert not remote._cloudflare_selected({**job, "type": "filmstrip"})
    assert remote._cloudflare_selected({**job, "type": "filmstrip",
        "_execution_shape": {**job["_execution_shape"], "proxy_bytes": 35849928}})
    assert not remote._cloudflare_selected({**job, "_execution_shape": {
        "total_bytes": 10 * 1024 ** 3, "original_bytes": 1024 ** 3}})


def test_approval_preview_recovers_in_same_job_without_downgrading_final(monkeypatch):
    seen = []
    def run(_db, job):
        seen.append((job["id"], renderer._PREVIEW_QUALITY.get()))
        if renderer._PREVIEW_QUALITY.get() == "approval":
            raise media.MediaError("ffmpeg failed (exit -9)")
        return {"render_asset_id": 123, "edl_version": 4}
    monkeypatch.setattr(renderer, "_run_render_job", run)
    job = {"id": 42, "type": "preview", "payload": {"quality": "approval"}}
    out = renderer.run_render_job(None, job)
    assert out["render_asset_id"] == 123 and out["quality"] == "draft"
    assert seen == [(42, "approval"), (42, "draft")]
    assert renderer._PREVIEW_QUALITY.get() == "draft"
    with pytest.raises(media.MediaError):
        renderer.run_render_job(None, {**job, "type": "final"})


def test_pending_original_serves_proxy_draft_but_keeps_final_blocked(monkeypatch):
    class WorkerDb:
        def run(self, fn, *args):
            if fn is db.user_is_paid: return True
            if fn is db.video_settings: return {}
            if fn is db.get_edl_version: return {"json": default_edl(10)}
            if fn is db.latest_asset:
                return {"sha256": "source", "storage_key": args[-1],
                        "meta": {"upload_state": "pending", "upload_progress": .4}}
            if fn is db.find_render_asset:
                return {"id": 123, "storage_key": "draft", "duration_s": 10,
                        "meta": {"quality": "draft", "src_sha256": "source"}}
            if fn is db.get_index_by_sha: return {"json": {}}
            raise AssertionError(fn)
    monkeypatch.setattr(renderer.storage, "exists", lambda _: True)
    for name in ["outro_current", "shaping_current", "transitions_current",
                 "music_tail_current", "watermark_current",
                 "_audio_model_review_cache_compatible"]:
        monkeypatch.setattr(renderer, name, lambda *args: True)
    job = {"id": 42, "project_id": 7, "user_id": 3, "type": "preview",
           "payload": {"edl_version": 4, "quality": "approval"}}
    result = renderer.run_render_job(WorkerDb(), job)
    assert result["quality"] == "draft" and result["render_asset_id"] == 123
    with pytest.raises(RuntimeError, match="still uploading"):
        renderer.run_render_job(WorkerDb(), {**job, "type": "final"})


@pytest.mark.parametrize("fps", ["60000/1001", "24000/1001"])
def test_short_overlay_never_truncates_longer_program(tmp_path, monkeypatch, fps):
    """A finite secondary must not end the source's video stream."""
    source, overlay, out = [str(tmp_path / name) for name in
                            ("source.mp4", "overlay.mp4", "out.mp4")]
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               f"testsrc2=s=160x90:r={fps}:d=40", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=40", "-c:v", "libx264",
               "-preset", "ultrafast", "-c:a", "aac", "-shortest", source])
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "color=red:s=160x90:r=30:d=1.1", "-c:v", "libx264",
               "-preset", "ultrafast", overlay])
    edl = default_edl(40)
    edl["keep"] = [[10, 18], [21, 38]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["overlays"] = [{"id": "ov1", "kind": "video", "asset_key": "clip.mp4",
                        "start": 6.4, "duration_s": 6.71, "fit": "cover"}]
    duration = renderer.render_edl(
        edl, {"video": {"duration": 40}, "words": []}, source, out,
        str(tmp_path), True, suppress_outro=True,
        asset_locals={"clip.mp4": overlay})
    assert duration == pytest.approx(25, abs=.2)


@pytest.mark.parametrize("preview", [True, False])
def test_mixed_format_clip_does_not_restart_program_or_audio(tmp_path, preview):
    source, overlay, out = [str(tmp_path / name) for name in
                            ("source.mp4", "overlay.mp4", "out.mp4")]
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "testsrc2=s=160x90:r=30:d=20", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=20", "-c:v", "libx264",
               "-preset", "ultrafast", "-c:a", "aac", "-shortest", source])
    pieces = []
    for i, pixel_format in enumerate(["yuvj420p", "yuv420p"]):
        piece = tmp_path / f"part{i}.h264"
        media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                   f"color={'red' if i == 0 else 'blue'}:s=160x90:r=30:d=1.5",
                   "-c:v", "libx264", "-preset", "ultrafast",
                   "-pix_fmt", pixel_format, "-color_range", "pc" if i == 0 else "tv",
                   "-colorspace", "bt470bg" if i == 0 else "bt709",
                   "-color_primaries", "bt709", "-color_trc", "bt709",
                   "-f", "h264", str(piece)])
        pieces.append(piece.read_bytes())
    joined = tmp_path / "mixed.h264"
    joined.write_bytes(b"".join(pieces))
    media.run(["ffmpeg", "-y", "-v", "error", "-r", "30", "-i",
               str(joined), "-c:v", "copy", overlay])
    edl = default_edl(20)
    edl["keep"] = [[0, 8], [11, 18]]
    edl["overlays"] = [{"id": "mixed", "kind": "video", "asset_key": "mixed.mp4",
                        "start": 6.4, "duration_s": 3, "fit": "cover"}]
    duration = renderer.render_edl(edl, {"video": {"duration": 20}, "words": []},
        source, out, str(tmp_path), preview, suppress_outro=True,
        asset_locals={"mixed.mp4": overlay})
    assert duration == pytest.approx(15, abs=.15)
    assert media.probe_audio_duration(out) == pytest.approx(15, abs=.15)
    import cv2
    capture = cv2.VideoCapture(out)
    try:
        for second, channel in [(6.7, 2), (8.5, 0)]:
            capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = capture.read()
            assert ok
            means = frame.mean(axis=(0, 1))
            assert means[channel] > 150  # Red then blue survives the format change.
            assert means[1] < 50
    finally:
        capture.release()
