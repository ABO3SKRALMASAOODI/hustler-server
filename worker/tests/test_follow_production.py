"""Face-follow on the PRODUCTION lanes (Oct 10 2026).

Commit f332b83 followed the speaker locally and never in production: the
MCP and agent lanes are Cloudflare standard-1 containers (0.5 vCPU), where
the face track over 43 s of kept footage needed more than the 60 s budget
(jobs 62747/62748: 81 s and 74 s of wall clock, 36.6 and 35.0 CPU-s), and
_follow_samples swallowed the abandoned measurement and wrote a still card
and crop without a word. These tests drive the production code path — a
real ToolContext over a database that hands out the proxy row, the proxy
leased from the media cache, a slow detector standing in for the 0.5 vCPU
lane — and pin:

* never silent: a follow that could not be measured says so, and why, in
  the tool result, and counts a metric (follow_unmeasured_<why>);
* the coarse 2 fps pass (full detections) alone is a track: a budget that
  runs out in the fine pass still follows;
* nothing measured is thrown away: the next call over the same footage
  reads it back (follow_track_cached) or resumes where the last stopped;
* the batch media lane (follow.run_faces_job) races the local pass, its
  samples are used when it wins, and its failure falls back to the local
  pass; it is reachable only through Cloudflare (the "faces" job type);
* measure reports every failure mode (no proxy, no OpenCV, no cascades, a
  decode that yields nothing, a stalled decode killed at the budget).
"""
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_tools
import config
import db as dbx
import follow
import media_cache
import remote
import storage
from schemas import default_edl, validate_edl

FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
SRC = 20.0
KEY = "proxies/9/0123abcd.mp4"


def _x_at(t):
    """The 'face' walks from x=.30 to x=.62 between 8 and 10 s."""
    return .30 + .32 * min(1.0, max(0.0, (t - 8.0) / 2.0))


@pytest.fixture(scope="module")
def walk_proxy(tmp_path_factory):
    """A 640x360 'proxy' shaped like production's (H.264, ~8 s GOP, 30 fps)
    of a bright 'face' on a dark stage that steps right at 8-10 s."""
    path = tmp_path_factory.mktemp("followprod") / "proxy.mp4"
    x = r"(0.30+0.32*min(1\,max(0\,(t-8)/2)))*640-22"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=0x202020:s=640x360:r=30:d={SRC}", "-f", "lavfi", "-i",
         f"color=c=white:s=44x56:r=30:d={SRC}", "-filter_complex",
         f"[0][1]overlay=x='{x}':y=70:eval=frame",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-g", "250",
         "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _blob(gray, roi=None):
    """The bright 'face' in a gray frame as [(box, 0)] (fractions)."""
    h, w = gray.shape[:2]
    x0 = y0 = 0
    img = gray
    if roi is not None:
        x0, y0 = max(0, int(roi[0] * w)), max(0, int(roi[1] * h))
        img = gray[y0:min(h, int(np.ceil(roi[3] * h))), x0:min(w, int(np.ceil(roi[2] * w)))]
    ys, xs = np.nonzero(img > 160)
    if len(xs) < 20:
        return []
    return [([round((xs.min() + x0) / w, 4), round((ys.min() + y0) / h, 4),
              round((xs.max() + 1 + x0) / w, 4), round((ys.max() + 1 + y0) / h, 4)], 0)]


def _wait(seconds):
    """A real pause (test_units replaces time.sleep for the whole session)."""
    if seconds > 0:
        threading.Event().wait(seconds)


class _Detector:
    """follow.detect stand-in: finds the bright box; ``full_s``/``roi_s``
    seconds per call stand in for Haar on a 0.5 vCPU lane."""

    def __init__(self, full_s=0.0, roi_s=0.0):
        self.full_s, self.roi_s = full_s, roi_s
        self.full = self.roi = 0

    def __call__(self, gray, cv2=None, cascades=None, face_px=None, roi=None):
        if roi is None:
            self.full += 1
            _wait(self.full_s)
        else:
            self.roi += 1
            _wait(self.roi_s)
        return _blob(gray, roi)


class _Db:
    """The two queries the follow path makes in production."""

    def __init__(self):
        self.metrics = []
        self.calls = []

    def run(self, fn, *args):
        self.calls.append(fn.__name__)
        if fn is dbx.latest_asset and args[1] == "proxy":
            return {"id": 1, "kind": "proxy", "storage_key": KEY}
        if fn is dbx.bump_metric:
            self.metrics.append(args[0])
            return None
        raise AssertionError(f"unexpected db call {fn.__name__}")


class _ProdCtx(agent_tools.ToolContext):
    """A real ToolContext (proxy_path: db row -> media_cache.lease) whose EDL
    lives in memory."""

    def __init__(self, workdir, keep=((4.0, 7.0), (7.4, 12.0), (12.5, 16.0))):
        index = {"video": {"width": 640, "height": 360, "fps": 30.0,
                           "duration": SRC},
                 "shots": [{"id": 1, "start": 0.0, "end": SRC}],
                 "spatial": {"v": 1, "samples": []}, "words": []}
        os.makedirs(workdir, exist_ok=True)          # mcp_exec makes it
        super().__init__(_Db(), {"id": 7, "user_id": 3}, {"id": 9,
                         "chat_session_id": None}, index, str(workdir))
        e = default_edl(SRC)
        e["keep"] = [list(k) for k in keep]
        self._edl = validate_edl(e, SRC).model_dump()
        self.written = []

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), SRC).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written) + 1}: {desc}"


@pytest.fixture
def prod(monkeypatch, tmp_path, walk_proxy):
    """Production conditions: the proxy row from the database, the file from
    the media cache (counted), no media lane unless a test adds one, a fresh
    FACE_STORE."""
    leases = []

    def lease(key, dest, name):
        assert key == KEY
        leases.append(key)
        out = os.path.join(dest, name)
        if not os.path.exists(out):
            shutil.copyfile(walk_proxy, out)
        return out
    monkeypatch.setattr(media_cache, "lease", lease)
    monkeypatch.setattr(storage, "download_to", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the proxy comes from the media cache")))
    monkeypatch.setattr(remote, "faces_available", lambda: False)
    follow.FACE_STORE.clear()
    agent_tools._FACES_INFLIGHT.clear()
    yield leases
    follow.FACE_STORE.clear()
    agent_tools._FACES_INFLIGHT.clear()


def _reframe(ctx):
    return agent_tools.set_frame(ctx, "9:16", "crop", .3, .5, _measured=True,
                                 _follow=True)


# ── never silent ──────────────────────────────────────────────────────────

@FFMPEG
def test_a_lane_too_slow_to_measure_says_so_and_counts_it(prod, monkeypatch, tmp_path):
    """Job 62748's conditions: the measurement outruns its budget. The crop
    keeps one aim — and the result and a metric say so."""
    monkeypatch.setattr(follow, "detect", _Detector(full_s=.08))
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.6)
    ctx = _ProdCtx(tmp_path)
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"] is None
    assert "FOLLOW: not measured — the face track did not finish in time" in res
    assert "does NOT follow the speaker" in res and "resumes the measurement" in res
    assert ctx.db.metrics == ["follow_unmeasured_budget"]
    assert ctx.editing_metrics["follow_unmeasured_budget"] == 1
    assert prod == [KEY]                        # the proxy came from the cache


@FFMPEG
def test_a_missing_proxy_is_reported_not_swallowed(prod, monkeypatch, tmp_path):
    def gone(key, dest, name):
        raise RuntimeError("object proxies/9/0123abcd.mp4 does not exist")
    monkeypatch.setattr(media_cache, "lease", gone)
    monkeypatch.setattr(storage, "download_to", gone)
    ctx = _ProdCtx(tmp_path)
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"] is None
    assert "FOLLOW: not measured — the proxy video could not be opened" in res
    assert "does not exist" in res
    assert ctx.db.metrics == ["follow_unmeasured_no_proxy"]


@FFMPEG
def test_a_card_that_cannot_follow_says_so(prod, monkeypatch, tmp_path):
    monkeypatch.setattr(follow, "detect", _Detector(full_s=.08))
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.6)
    ctx = _ProdCtx(tmp_path)
    res = agent_tools.set_picture_card(ctx, "c", 0, 8, box=[.04, .2, .96, .55],
                                       fit="crop")
    card = ctx._edl["effects"]["picture_cards"][0]
    assert card["follow"] is None
    assert "FOLLOW: not measured" in res and "The card does NOT follow" in res
    assert "it holds one framing per shot" in res
    assert ctx.db.metrics == ["follow_unmeasured_budget"]


# ── the production path works ─────────────────────────────────────────────

@FFMPEG
def test_the_production_path_follows_a_moving_speaker(prod, monkeypatch, tmp_path):
    det = _Detector()
    monkeypatch.setattr(follow, "detect", det)
    ctx = _ProdCtx(tmp_path)
    res = _reframe(ctx)
    spans = ctx._edl["frame"]["follow"]
    assert spans and "FOLLOWS the speaker inside 1 shot" in res, res
    assert follow.centre_at(spans[0], 6.0)[0] < .4 < follow.centre_at(spans[0], 14.0)[0]
    assert ctx.db.metrics == ["follow_track_local"]
    # half the samples were full detections (2 fps), the rest region searches
    assert det.full and det.roi and abs(det.full - det.roi) <= det.full * .3


@FFMPEG
def test_the_coarse_track_alone_is_enough_to_follow(prod, monkeypatch, tmp_path):
    """A budget that runs out in the fine pass still leaves a complete 2 fps
    track of full detections: the crop follows."""
    monkeypatch.setattr(follow, "detect", _Detector(full_s=0.0, roi_s=.25))
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 2.5)
    ctx = _ProdCtx(tmp_path)
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"], res
    assert "FOLLOW: not measured" not in res


@FFMPEG
def test_the_next_call_reads_the_track_back_and_a_cut_short_one_resumes(
        prod, monkeypatch, tmp_path):
    det = _Detector(full_s=.08)
    monkeypatch.setattr(follow, "detect", det)
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.5)     # (a 1 s floor)
    first = _ProdCtx(tmp_path / "a")
    assert "did not finish in time" in _reframe(first)
    done_first = det.full
    assert done_first > 0
    # a NEW context (MCP contexts are evicted between calls) resumes: only
    # the footage the first call did not reach is decoded and detected
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 60.0)
    second = _ProdCtx(tmp_path / "b")
    res = _reframe(second)
    assert second._edl["frame"]["follow"], res
    total_coarse = sum(int((b - a) * follow.MIN_MEASURE_FPS) + 1
                       for a, b in ((4.0, 7.0), (7.4, 12.0), (12.5, 16.0)))
    assert det.full <= total_coarse + 4          # nothing measured twice
    # a third call over the same footage measures nothing at all
    before = det.full + det.roi
    third = _ProdCtx(tmp_path / "c")
    _reframe(third)
    assert det.full + det.roi == before
    assert third.db.metrics == ["follow_track_cached"]
    assert third._edl["frame"]["follow"] == second._edl["frame"]["follow"]


@FFMPEG
def test_the_card_and_the_crop_share_one_measurement(prod, monkeypatch, tmp_path):
    """Production measured the card's windows, then the crop's (0.03 s
    longer) from scratch — and lost both to the budget."""
    det = _Detector()
    monkeypatch.setattr(follow, "detect", det)
    ctx = _ProdCtx(tmp_path)
    agent_tools.set_picture_card(ctx, "c", 0, 10.5, box=[.04, .2, .96, .55], fit="crop")
    assert ctx._edl["effects"]["picture_cards"][0]["follow"]
    n = det.full + det.roi
    _reframe(ctx)
    assert ctx._edl["frame"]["follow"]
    # only the crop's extra 0.6 s (15.4-16 s) is measured
    assert det.full + det.roi <= n + 4
    assert ctx.db.metrics == ["follow_track_local", "follow_track_local"]


# ── the media lane ────────────────────────────────────────────────────────

def _in_process_media_lane(monkeypatch, tmp_path, delay=0.0, seen=None):
    """run_faces_remote served by follow.run_faces_job in this process (its
    own media cache copy), the way the batch lane runs it."""
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "lane"))
    def run(project_id, payload, user_id=None):
        if seen is not None:
            seen.append(payload)
        _wait(delay)
        return follow.run_faces_job(None, {"payload": payload})
    monkeypatch.setattr(remote, "faces_available", lambda: True)
    monkeypatch.setattr(remote, "run_faces_remote", run)


@FFMPEG
def test_the_media_lane_wins_the_race(prod, monkeypatch, tmp_path, walk_proxy):
    local = _Detector(full_s=.2)
    lane = []

    def detect(gray, cv2=None, cascades=None, face_px=None, roi=None):
        if threading_name() == "follow-faces-remote":
            lane.append(1)
            return _blob(gray, roi)            # 4 vCPU: no wait
        return local(gray, roi=roi)
    monkeypatch.setattr(follow, "detect", detect)
    monkeypatch.setattr(agent_tools, "FOLLOW_REMOTE_MIN_S", 1.0)
    seen = []
    _in_process_media_lane(monkeypatch, tmp_path, seen=seen)
    ctx = _ProdCtx(tmp_path)
    t = time.monotonic()
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"], res
    assert ctx.db.metrics == ["follow_track_remote"]
    assert lane and local.full < 15              # the local pass was cancelled
    assert time.monotonic() - t < 10
    assert seen[0]["storage_key"] == KEY and seen[0]["faces_version"] == follow.FACES_VERSION


def threading_name():
    return threading.current_thread().name


@FFMPEG
def test_a_failed_media_lane_falls_back_to_the_local_pass(prod, monkeypatch, tmp_path):
    monkeypatch.setattr(follow, "detect", _Detector())
    monkeypatch.setattr(agent_tools, "FOLLOW_REMOTE_MIN_S", 1.0)
    monkeypatch.setattr(remote, "faces_available", lambda: True)

    def broken(*a, **k):
        raise remote.CloudflareCapacityBusy("shard is busy")
    monkeypatch.setattr(remote, "run_faces_remote", broken)
    ctx = _ProdCtx(tmp_path)
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"], res
    assert ctx.db.metrics == ["follow_track_local"]


@FFMPEG
def test_when_both_lanes_fail_the_note_names_both(prod, monkeypatch, tmp_path):
    monkeypatch.setattr(follow, "detect", _Detector(full_s=.08))
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.6)
    monkeypatch.setattr(agent_tools, "FOLLOW_REMOTE_MIN_S", 1.0)
    monkeypatch.setattr(remote, "faces_available", lambda: True)

    def broken(*a, **k):
        raise remote.RemoteExecutorError("executor returned 500")
    monkeypatch.setattr(remote, "run_faces_remote", broken)
    res = _reframe(_ProdCtx(tmp_path))
    assert "did not finish in time" in res
    assert "the media-lane measurement failed (RemoteExecutorError: executor returned 500)" in res


@FFMPEG
def test_run_faces_job_round_trip(monkeypatch, tmp_path, walk_proxy):
    follow.FACE_STORE.clear()
    monkeypatch.setattr(follow, "detect", _Detector())
    leased = []

    def lease(key, dest, name):
        leased.append(key)
        out = os.path.join(dest, name)
        shutil.copyfile(walk_proxy, out)
        return out
    monkeypatch.setattr(media_cache, "lease", lease)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))

    def no_url(key, expires=3600):
        raise RuntimeError("no presigning here")
    monkeypatch.setattr(storage, "presign_get", no_url)
    payload = {"storage_key": KEY, "windows": [[4.0, 9.0]], "aspect": 360 / 640,
               "cuts": [], "fps": 4.0, "width": 448, "budget_s": 30,
               "faces_version": follow.FACES_VERSION}
    out = follow.run_faces_job(None, {"payload": payload})
    assert out["ok"] and out["faces_version"] == follow.FACES_VERSION
    frames = follow.unpack_frames(out["frames"])
    assert len(frames) >= 18 and all(4.0 - .05 <= t <= 9.05 for t, _d in frames)
    assert frames == follow.unpack_frames(follow.pack_frames(frames))
    assert set(out["roi_times"]) and out["report"]["status"] == "complete"
    # a repeat on the same container reads the store: no second download
    again = follow.run_faces_job(None, {"payload": payload})
    assert again["report"]["cached"] and leased == [KEY]
    with pytest.raises(ValueError, match="mid-deploy"):
        follow.run_faces_job(None, {"payload": dict(payload, faces_version=1)})
    follow.FACE_STORE.clear()


def test_faces_runs_only_on_the_cloudflare_batch_lane(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://cf.example")
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({"matte", "faces"}))
    assert remote.faces_available()
    assert remote._cloudflare_lane("faces") == "batch"
    assert "faces" in config.CLOUDFLARE_SYNCHRONOUS_TYPES
    sent = []
    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait",
                        lambda job: sent.append(job) or {"ok": True})
    assert remote.run_faces_remote(9, {"x": 1}, user_id=3) == {"ok": True}
    assert sent[0]["type"] == "faces" and sent[0]["id"] is None
    # a stale type list (Render, Modal) never routes it anywhere else
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES", frozenset({"matte"}))
    assert not remote.faces_available()
    with pytest.raises(remote.RemoteExecutorError):
        remote.run_faces_remote(9, {"x": 1})


def test_the_default_type_lists_and_the_executor_carry_faces():
    import http_server
    assert "faces" in config.CLOUDFLARE_SYNCHRONOUS_TYPES or \
        os.getenv("CLOUDFLARE_SYNCHRONOUS_TYPES")
    assert http_server.COMPUTE_RUNNERS["faces"] is follow.run_faces_job
    assert config.execution_class_for("faces") == "heavy_media"
    ts = (Path(__file__).resolve().parents[1] / "cloudflare" / "src" / "index.ts").read_text()
    assert ts.count('"faces"') >= 4            # batch, synchronous, both env lists


# ── measure's own report ──────────────────────────────────────────────────

def test_measure_reports_every_failure_mode(monkeypatch, tmp_path):
    rep = {}
    assert follow.measure(None, [(0, 5)], .5625, report=rep) == []
    assert rep["status"] == "failed" and rep["why"] == "no_proxy"
    follow.measure(str(tmp_path / "x.mp4"), [(0, 0.01)], .5625, report=rep)
    assert rep["why"] == "empty"
    follow.measure(str(tmp_path / "x.mp4"), [(0, follow.MAX_MEASURE_S + 5)], .5625,
                   report=rep)
    assert rep["why"] == "too_long"
    real = tmp_path / "real.bin"
    real.write_bytes(b"not a video at all")
    import subject
    monkeypatch.setattr(subject, "_cv2", lambda: None)
    follow.measure(str(real), [(0, 5)], .5625, report=rep)
    assert rep["why"] == "no_opencv"
    monkeypatch.undo()
    monkeypatch.setattr(subject, "_cascades", lambda cv2: [])
    follow.measure(str(real), [(0, 5)], .5625, report=rep)
    assert rep["why"] == "no_cascades"


@FFMPEG
def test_an_undecodable_proxy_is_a_decode_failure(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 4096)
    rep = {}
    assert follow.measure(str(bad), [(0, 5)], .5625, report=rep) == []
    assert rep["why"] == "decode" and rep["detail"]


def test_a_stalled_decode_is_killed_at_the_budget(monkeypatch, tmp_path):
    """A decoder that never writes a frame cannot hold the call past its
    budget (the read would block forever without the watchdog)."""
    fake = tmp_path / "bin"
    fake.mkdir()
    script = fake / "ffmpeg"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    rep = {}
    t = time.monotonic()
    out = follow.measure(str(video), [(0, 5)], .5625, budget_s=1.0, report=rep)
    assert time.monotonic() - t < 5
    assert out == [] and rep["why"] in ("budget", "decode")
    assert rep["status"] in ("partial", "failed")


@FFMPEG
def test_group_shots_are_judged_on_full_detections_only(prod, monkeypatch, tmp_path):
    """A fine sample searches around its neighbours' faces only — it cannot
    see a second person elsewhere, so a two-shot would read as a single.
    Group shots are judged on the coarse (full-frame) samples alone."""
    monkeypatch.setattr(follow, "detect", _Detector())
    judged = []
    real = follow.face_counts

    def counts(frames):
        judged.extend(t for t, _d in frames)
        return real(frames)
    monkeypatch.setattr(follow, "face_counts", counts)
    ctx = _ProdCtx(tmp_path)
    _reframe(ctx)
    frames, roi = follow.FACE_STORE.samples(follow.FACE_STORE.key(KEY), [(0.0, SRC)])
    assert roi and judged and not set(judged) & roi
    assert len(judged) == len(frames) - len(roi)


def test_the_store_knows_what_is_covered():
    st = follow.FaceStore()
    key = st.key("proxies/1/a.mp4")
    assert st.uncovered(key, [(10.0, 14.0)]) == [(10.0, 14.0)]
    st.add(key, [(10.0 + i * .5, []) for i in range(5)])          # 10.0 .. 12.0
    left = st.uncovered(key, [(10.0, 14.0)])
    assert len(left) == 1 and 12.0 < left[0][0] < 12.2 and left[0][1] == 14.0
    st.add(key, [(12.5 + i * .5, []) for i in range(4)])          # 12.5 .. 14.0
    assert st.uncovered(key, [(10.0, 14.0)]) == []
    st.add(key, [(11.0, [([.1, .1, .2, .2], 0)])], roi_times=[11.0])
    frames, roi = st.samples(key, [(10.0, 14.0)])
    # a full detection is never replaced by a region search of the same time
    assert dict(frames)[11.0] == [] and 11.0 not in roi
    assert st.uncovered(st.key("proxies/1/a.mp4", width=320), [(10, 11)])  # other geometry


def test_detect_maps_a_region_search_back_to_the_whole_frame(monkeypatch):
    """Boxes of a region search are fractions of the WHOLE frame, and the
    size band narrows the scales only (the frame is never shrunk)."""
    calls = []

    class Cascade:
        def detectMultiScale(self, img, **kw):
            calls.append((img.shape, kw))
            return [(10, 20, 30, 40)]
    import subject
    cv2 = subject._cv2()
    if cv2 is None:
        pytest.skip("OpenCV required")
    gray = np.zeros((200, 400), np.uint8)
    out = follow.detect(gray, cv2, [Cascade()], face_px=40, roi=[.5, .25, 1.0, 1.0])
    assert calls[0][0] == (150, 200)                 # the region, full resolution
    assert calls[0][1]["minSize"] == (24, 24) and calls[0][1]["maxSize"] == (88, 88)
    (box, look), = out
    assert box == [round((200 + 10) / 400, 4), round((50 + 20) / 200, 4),
                   round((200 + 40) / 400, 4), round((50 + 60) / 200, 4)]
    # without a region or size: subject._faces_in's own settings, unchanged
    calls.clear()
    follow.detect(gray, cv2, [Cascade()])
    assert calls[0][0] == (200, 400) and "maxSize" not in calls[0][1]


# ── review fixes (Oct 10 2026) ────────────────────────────────────────────

@FFMPEG
def test_a_kept_sliver_never_leaves_the_track_incomplete(prod, monkeypatch, tmp_path):
    """A kept fragment shorter than a sample step that no grid frame falls
    inside (a word kept between two cuts, a sliver beside a camera cut) got
    no sample: the track never counted as complete, every call reported
    'the proxy could not be decoded' and the edit never followed."""
    monkeypatch.setattr(follow, "detect", _Detector())
    keep = ((4.0, 7.0), (7.6, 7.72), (8.4, 12.0), (12.5, 16.0))
    ctx = _ProdCtx(tmp_path, keep=keep)
    res = _reframe(ctx)
    assert ctx._edl["frame"]["follow"], res
    assert "FOLLOW: not measured" not in res
    assert ctx.db.metrics == ["follow_track_local"]
    assert follow.FACE_STORE.uncovered(follow.FACE_STORE.key(KEY), keep) == []
    frames, _roi = follow.FACE_STORE.samples(follow.FACE_STORE.key(KEY), [(7.6, 7.72)])
    assert frames and frames[0][1]                 # measured, with the face


def test_every_window_gets_a_sample():
    """_sample_plan: every k-th grid frame inside a window is a full
    detection; a fine frame with no full neighbour in its shot becomes one;
    a window no grid frame falls inside takes the nearest frame of its own
    shot, recorded at its edge; one no frame of its shot reaches is
    measured as nobody there."""
    # grid 9.5 + i/4: 10.0 (i=2), 10.25, ... ; k=2 (coarse = even i)
    full, fine, empty = follow._sample_plan(
        9.5, 12.0, 4.0, 2, [(10.0, 10.5), (10.8, 10.85), (11.2, 11.3)], cuts=[])
    rec = {t for ts in full.values() for t in ts}
    assert {10.0, 10.5} <= rec and fine == {3: 10.25}
    assert 10.8 in rec                           # 10.75, recorded at the sliver's start
    assert 11.25 in rec                          # isolated odd frame: full
    assert empty == []
    # a sliver right before a cut whose only frame within a step lies past it
    full, fine, empty = follow._sample_plan(
        9.5, 11.0, 4.0, 2, [(10.3, 10.4)], cuts=[10.4, 10.45])
    assert not any(10.3 <= t <= 10.4 for ts in full.values() for t in ts) \
        or empty == []
    full, fine, empty = follow._sample_plan(
        9.5, 11.0, 4.0, 2, [(10.42, 10.44)], cuts=[10.41, 10.45])
    assert empty == [10.43] and not full and not fine


def test_a_short_fragment_near_a_sample_is_covered():
    st = follow.FaceStore()
    key = st.key("proxies/1/b.mp4")
    st.add(key, [(10.0 + i * .5, []) for i in range(5)])            # 10.0 .. 12.0
    assert st.uncovered(key, [(12.3, 12.42)]) == []                # within a step
    assert st.uncovered(key, [(13.0, 13.1)]) == [(13.0, 13.1)]      # nothing near


class _NoChangeCtx(_ProdCtx):
    """write_edl answers NO CHANGE for an identical EDL, as production's does."""

    def write_edl(self, edl, desc):
        new = validate_edl(dict(edl), SRC).model_dump()
        if new == self._edl:
            return f"NO CHANGE — the EDL is identical to v{len(self.written) + 1}"
        return super().write_edl(edl, desc)


@FFMPEG
def test_calling_the_tool_again_resumes_on_the_same_session(prod, monkeypatch, tmp_path):
    """An MCP session keeps one context between calls. The result of a
    follow that ran out of time says 'calling the tool again resumes' — so
    the exact same call must neither be refused by the replay guard nor
    answered from a remembered failure; and when it still cannot follow,
    a NO CHANGE answer must still say so."""
    det = _Detector(full_s=.08)
    monkeypatch.setattr(follow, "detect", det)
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.5)
    ctx = _NoChangeCtx(tmp_path)
    args = {"id": "c", "start": 0, "end": 8, "box": [.04, .2, .96, .55], "fit": "crop"}
    first = agent_tools.execute(ctx, "set_picture_card", dict(args))
    assert "did not finish in time" in first and "resumes the measurement" in first
    assert ctx._tool_call_token is None              # cleared after the call
    # still out of time: the same card again — the result still says so
    monkeypatch.setattr(follow, "detect", _Detector(full_s=10.0))
    again = agent_tools.execute(ctx, "set_picture_card", dict(args))
    assert "NO CHANGE" in again and "does NOT follow the speaker" in again, again
    # time enough now: the same exact call resumes and follows
    monkeypatch.setattr(follow, "detect", det)
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 60.0)
    resumed = agent_tools.execute(ctx, "set_picture_card", dict(args))
    assert "NO CHANGE" not in resumed, resumed
    assert "framed from a dense face track" in resumed
    assert ctx.db.metrics == ["follow_unmeasured_budget", "follow_unmeasured_budget",
                              "follow_track_local"]
    # and a finished write is protected by the replay guard again
    assert "NO CHANGE" in agent_tools.execute(ctx, "set_picture_card", dict(args))


@FFMPEG
def test_a_call_inside_one_tool_call_asks_the_lanes_once(prod, monkeypatch, tmp_path):
    """Within one call (_card_dense, then _card_follow) a failure is not
    measured twice — the second ask would double the call's time."""
    det = _Detector(full_s=.08)
    monkeypatch.setattr(follow, "detect", det)
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.5)
    ctx = _ProdCtx(tmp_path, keep=((4.0, 7.0), (7.4, 12.0)))
    ctx.index["shots"] = [{"id": 1, "start": 0.0, "end": 7.2},
                          {"id": 2, "start": 7.2, "end": SRC}]
    t = time.monotonic()
    res = agent_tools.execute(ctx, "set_picture_card",
                              {"id": "c", "start": 0, "end": 7.5,
                               "box": [.04, .2, .96, .55], "fit": "crop"})
    assert "FOLLOW: not measured" in res, res
    assert ctx.db.metrics == ["follow_unmeasured_budget"]
    assert time.monotonic() - t < 2.5


@FFMPEG
def test_a_resumed_call_waits_on_the_media_lane_job_already_running(
        prod, monkeypatch, tmp_path):
    """The first call ran out of time while its media-lane job still ran.
    The next call over that footage waits on the same job (no second batch
    shard), and its own pass stops the moment the job completes."""
    local = _Detector(full_s=.3)
    lane_started = threading.Event()

    def detect(gray, cv2=None, cascades=None, face_px=None, roi=None):
        if threading_name() == "follow-faces-remote":
            lane_started.set()
            return _blob(gray, roi)
        return local(gray, roi=roi)
    monkeypatch.setattr(follow, "detect", detect)
    monkeypatch.setattr(agent_tools, "FOLLOW_REMOTE_MIN_S", 1.0)
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 0.6)
    seen = []
    _in_process_media_lane(monkeypatch, tmp_path, delay=2.5, seen=seen)
    ctx = _ProdCtx(tmp_path)
    first = _reframe(ctx)
    assert "the media-lane measurement still running" in first, first
    assert ctx.db.metrics == ["follow_unmeasured_budget"]
    monkeypatch.setattr(follow, "MEASURE_BUDGET_S", 30.0)
    t = time.monotonic()
    again = _ProdCtx(tmp_path / "again")
    second = _reframe(again)
    assert again._edl["frame"]["follow"], second
    assert len(seen) == 1                            # one media-lane job in all
    assert again.db.metrics == ["follow_track_remote"]
    assert time.monotonic() - t < 6                  # the local pass was cancelled
    assert local.full < 30


@FFMPEG
def test_the_media_lane_range_reads_the_proxy_instead_of_staging_it(
        monkeypatch, tmp_path, walk_proxy):
    """A cold batch shard used to download the whole proxy (production's run
    to ~400 MB: 25-60 s) before measuring 40 s of it. It now range-reads a
    presigned URL (the proxy is +faststart); a stream that reads nothing
    falls back to staging it."""
    follow.FACE_STORE.clear()
    monkeypatch.setattr(follow, "detect", _Detector())
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path))
    staged = []

    def lease(key, dest, name):
        staged.append(key)
        out = os.path.join(dest, name)
        shutil.copyfile(walk_proxy, out)
        return out
    monkeypatch.setattr(media_cache, "lease", lease)
    monkeypatch.setattr(storage, "presign_get",
                        lambda key, expires=3600: f"file://{walk_proxy}")
    payload = {"storage_key": KEY, "windows": [[4.0, 9.0]], "aspect": 360 / 640,
               "cuts": [], "fps": 4.0, "width": 448, "budget_s": 30,
               "faces_version": follow.FACES_VERSION}
    out = follow.run_faces_job(None, {"payload": payload})
    assert out["ok"] and out["report"]["source"] == "stream" and staged == []
    assert len(follow.unpack_frames(out["frames"])) >= 18
    # a URL that reads nothing: the proxy is staged and measured
    follow.FACE_STORE.clear()
    monkeypatch.setattr(storage, "presign_get",
                        lambda key, expires=3600: f"file://{tmp_path}/missing.mp4")
    out = follow.run_faces_job(None, {"payload": payload})
    assert out["ok"] and out["report"]["source"] == "staged" and staged == [KEY]
    follow.FACE_STORE.clear()
