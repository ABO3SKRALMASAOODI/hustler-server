"""Track 0A: the sidecar store, launcher and read API (INTERFACES.md §2).

CI-safe: a local store, the real child loop with stand-in models
(tests/w0a_support.py), synthetic clips from ffmpeg's lavfi.
"""

import json
import os
import threading
import time

import pytest

import sidecars
import w0a_support


@pytest.fixture
def local(monkeypatch, tmp_path):
    store, launcher = w0a_support.install(monkeypatch, tmp_path)
    yield store, launcher
    w0a_support.uninstall(launcher)


@pytest.fixture
def clip(tmp_path):
    return w0a_support.make_clip(str(tmp_path / "proxy.mp4"), dur=6.0)


def _src(path, **kw):
    return w0a_support.source(path, **kw)


# ── keys, params, kinds ─────────────────────────────────────────────────

def test_params_fold_into_one_key_whoever_asks():
    src = _src("p.mp4", dur=40.0, shots=(10.0,))
    sidecars.use_store(False)
    try:
        a = sidecars.ensure("faces.yunet", src, [(0, 12)])
        b = sidecars.ensure("faces.yunet", src, [(0, 12)],
                            params={"fps": 10, "det_w": 320.0})
        c = sidecars.ensure("faces.yunet", src, [(0, 12)],
                            params={"fps": 5})
    finally:
        sidecars.use_store(None)
    assert [x.key for x in a.chunks] == [x.key for x in b.chunks]
    assert [x.key for x in a.chunks] != [x.key for x in c.chunks]
    # the key is the pinned formula over the model record's id/version
    sp = sidecars.spec("faces.yunet")
    assert a.chunks[0].key == sidecars.sidecar_key(
        "faces.yunet", src.sha256, sp["model_id"], sp["model_version"],
        sidecars.resolve_params("faces.yunet"), (0.0, 10.0))
    assert sp["model_id"] == "face_detection_yunet_2023mar"
    assert sidecars.spec("matte.person")["model_id"] == "rvm_mobilenetv3_fp32"


def test_unknown_kinds_raise_and_reserved_kinds_are_inert():
    src = _src("p.mp4")
    with pytest.raises(ValueError):
        sidecars.ensure("matte.persons", src, [(0, 1)])
    with pytest.raises(ValueError):
        sidecars.ensure("faces.yunet", src, [(0, 1)], priority="urgent")
    h = sidecars.ensure("depth.small", src, [(0, 4)])
    assert sidecars.status(h) == "absent" and sidecars.fetch(h, "/x") == []
    assert sidecars.estimate_s(h) == 0.0


def test_without_a_store_everything_is_absent_and_nothing_runs(monkeypatch):
    calls = []
    sidecars.use_store(False)
    sidecars.use_launcher(sidecars._Launcher(
        execute=lambda s, p: calls.append(p)))
    try:
        h = sidecars.ensure("matte.person", _src("p.mp4", dur=30.0),
                            [(0, 30)])
        assert sidecars.status(h) == "absent"
        assert sidecars.wait([h], 0) == {c.key: "absent" for c in h.chunks}
        assert sidecars.summary("ab" * 32) == {}
        assert sidecars.estimate_s(h) > 0
    finally:
        sidecars.use_store(None)
        sidecars.use_launcher(None)
    assert calls == []


def test_source_from_assets_and_index_sha():
    src = sidecars.source_from("cd" * 32, "proxies/1/x.mp4",
                               {"duration": 61.5, "fps": 29.97,
                                "width": 960, "height": 540}, 9,
                               [{"start": 0.0}, {"start": 12.25},
                                {"start": 40.0}])
    assert src.shots == (12.25, 40.0) and src.duration_s == 61.5
    idx = {"tile_keys": [f"tiles/12/{'ef' * 32}/tile_000.jpg"]}
    assert sidecars.index_source_sha(idx) == "ef" * 32
    assert sidecars.index_source_sha({"tile_keys": []}) is None


# ── the pipeline end to end (local store, real child loop) ───────────────

def test_ensure_returns_at_once_and_chunks_become_ready(local, clip,
                                                        tmp_path):
    store, launcher = local
    src = _src(clip, shots=(3.0,))
    t = time.monotonic()
    h = sidecars.ensure("faces.yunet", src, [(0, 6)], priority="interactive")
    assert time.monotonic() - t < 0.5                 # writes never wait
    assert [(c.t0, c.t1) for c in h.chunks] == [(0.0, 3.0), (3.0, 6.0)]
    assert set(sidecars.chunk_status(h).values()) <= {"pending", "ready"}
    states = sidecars.wait([h], 60)
    assert set(states.values()) == {"ready"}
    assert sidecars.status(h) == "ready"
    rows = sidecars.fetch(h, str(tmp_path / "got"))
    assert [r["t0"] for r in rows] == [0.0, 3.0]
    data = json.load(open(rows[0]["path"]))
    assert data["fps"] == 10.0 and len(data["samples"]) == 30
    assert data["samples"][1]["t"] == pytest.approx(0.1)
    face = data["samples"][0]["faces"][0]
    assert face[:4] == pytest.approx([0.25, 0.2, 0.75, 0.6], abs=0.01)
    m = rows[0]["meta"]
    for k in ("key", "kind", "model_id", "model_version", "params",
              "src_range", "fps", "frames", "width", "height", "format",
              "bytes", "created_at", "qa"):
        assert k in m
    assert m["qa"]["coverage"] == 1.0 and m["format"] == "faces.json.v1"
    # data and manifest never collide for a JSON kind
    names = sorted(os.listdir(os.path.join(
        store.root, *sidecars.kind_dir("faces.yunet", src.sha256)
        .split("/"))))
    assert any(n.endswith(".faces.json") for n in names)
    assert not any(n.endswith(".pending.json") for n in names)
    # a repeat request is a cache hit: nothing launches, and it is fast
    calls = launcher.stats["calls"]
    sidecars.use_store(store)                       # a cold reader process
    t = time.monotonic()
    again = sidecars.ensure("faces.yunet", src, [(0, 6)])
    assert sidecars.status(again) == "ready"
    assert time.monotonic() - t < 1.0
    assert launcher.stats["calls"] == calls
    assert sidecars.summary(src.sha256) == {
        "faces.yunet": {"ready": 2, "pending": 0, "failed": 0}}
    assert sidecars.estimate_s(again) == 0.0


def test_matte_chunks_reset_at_cuts_warm_up_inside_a_shot(local, clip,
                                                          tmp_path):
    store, launcher = local
    # One cut at 2.0: both chunks start at a cut, so neither warms up.
    src = _src(clip, shots=(2.0,))
    h = sidecars.ensure("matte.person", src, [(0, 6)])
    sidecars.wait([h], 90)
    assert sidecars.status(h) == "ready"
    rows = sidecars.fetch(h, str(tmp_path / "m"))
    assert [(r["t0"], r["t1"]) for r in rows] == [(0.0, 2.0), (2.0, 6.0)]
    for r in rows:
        m = r["meta"]
        assert m["warmup_s"] == 0.0                  # both start at a cut
        assert m["fps"] == 25.0
        assert m["frames"] == round((r["t1"] - r["t0"]) * 25)
        assert m["width"] == 320 and m["height"] == 180
        assert set(m["qa"]) == {"flicker", "edge_chatter", "coverage"}
        assert len(m["qa"]["coverage"]) == m["frames"]
        assert 0.05 < m["qa"]["coverage"][0] < 0.2   # the white square
    import media
    probe = media.probe(rows[1]["path"])
    assert int(probe["width"]) == 320
    # a long shot splits into equal chunks; only a split INSIDE a shot gets
    # the discarded warm-up, and never one that would cross a cut
    grid = sidecars.chunk_ranges((), 40.0)
    assert grid[1][0] == pytest.approx(13.333, abs=1e-3)
    import analysis_jobs
    w = analysis_jobs._warmups(
        sidecars.SourceRef("cd" * 32, "x", 40.0, 25.0, 320, 180,
                           shots=(13.6,)),
        [sidecars.ChunkRef("a", 0.0, 13.333),
         sidecars.ChunkRef("b", 13.333, 13.6),
         sidecars.ChunkRef("c", 20.0, 33.0)], 1.0)
    assert w == {"a": 0.0, "b": 1.0, "c": 1.0}
    w2 = analysis_jobs._warmups(
        sidecars.SourceRef("cd" * 32, "x", 40.0, 25.0, 320, 180,
                           shots=(19.6,)),
        [sidecars.ChunkRef("c", 20.0, 33.0)], 1.0)
    assert w2 == {"c": 0.4}


# ── the version handshake on the read side ──────────────────────────────

def _manifest_path(store, src, kind, key):
    return os.path.join(store.root, *sidecars.manifest_key(
        kind, src.sha256, key).split("/"))


def test_unknown_model_version_or_format_reads_as_absent(local, clip):
    store, launcher = local
    src = _src(clip)
    h = sidecars.ensure("faces.yunet", src, [(0, 6)])
    sidecars.wait([h], 60)
    launcher.join(30)
    key = h.chunks[0].key
    path = _manifest_path(store, src, "faces.yunet", key)
    good = json.load(open(path))
    for tamper in ({"model_version": "2099"}, {"format": "faces.json.v9"},
                   {"manifest_version": 2}, {"key": "0" * 64}):
        bad = dict(good, **tamper)
        json.dump(bad, open(path, "w"))
        sidecars.use_store(store)                  # drop this reader's cache
        sidecars.use_launcher(sidecars._Launcher(
            execute=lambda s, p: {"chunks": {}}))  # nothing may relaunch it
        assert sidecars.chunk_status(h)[key] != "ready"
    json.dump(good, open(path, "w"))
    sidecars.use_store(store)
    assert sidecars.chunk_status(h)[key] == "ready"


def test_data_without_a_manifest_is_absent(local, clip):
    store, _launcher = local
    src = _src(clip)
    key = sidecars.chunk_key("faces.yunet", src.sha256, None, (0.0, 6.0))
    store.put_bytes(sidecars.data_key("faces.yunet", src.sha256, key),
                    b'{"samples": []}')
    h = sidecars.Handle("faces.yunet", src.sha256,
                        (sidecars.ChunkRef(key, 0.0, 6.0),))
    assert sidecars.chunk_status(h) == {key: "absent"}


def test_pending_lease_and_failed_retry_window(local):
    store, _launcher = local
    src = _src("x.mp4")
    k1, k2, k3 = ("1" * 64, "2" * 64, "3" * 64)
    now = time.time()
    store.put_bytes(sidecars.pending_key("faces.yunet", src.sha256, k1),
                    json.dumps({"lease_until": now + 60}).encode())
    store.put_bytes(sidecars.pending_key("faces.yunet", src.sha256, k2),
                    json.dumps({"lease_until": now - 1}).encode())
    store.put_bytes(sidecars.failed_key("faces.yunet", src.sha256, k3),
                    json.dumps(sidecars.failed_marker("oom", 2)).encode())
    h = sidecars.Handle("faces.yunet", src.sha256, tuple(
        sidecars.ChunkRef(k, i, i + 1) for i, k in enumerate((k1, k2, k3))))
    assert sidecars.chunk_status(h) == {k1: "pending", k2: "absent",
                                        k3: "failed"}
    assert sidecars.status(h) == "pending"
    expired = sidecars.failed_marker("oom", 2)
    expired["retry_after"] = now - 1
    store.put_bytes(sidecars.failed_key("faces.yunet", src.sha256, k3),
                    json.dumps(expired).encode())
    sidecars.use_store(store)
    assert sidecars.chunk_status(h)[k3] == "absent"   # eligible again
    once = sidecars.failed_marker("oom", 1)            # retryable, 1 attempt
    store.put_bytes(sidecars.failed_key("faces.yunet", src.sha256, k3),
                    json.dumps(once).encode())
    sidecars.use_store(store)
    assert sidecars.chunk_status(h)[k3] == "absent"


# ── retries, degrade, priorities (AT-0A-3 at unit level) ────────────────

def test_a_lost_chunk_retries_once_elsewhere_then_only_its_window_degrades(
        monkeypatch, tmp_path, clip):
    import analysis_jobs
    store = None
    killed = {"key": None}
    seen = []

    def execute(source, payload):
        seen.append((payload["attempt"], payload["slot"],
                     [c["key"] for c in payload["chunks"]]))
        keys = [c["key"] for c in payload["chunks"]]
        if killed["key"] in keys:
            # the shard dies mid-chunk: no result envelope at all
            raise RuntimeError("Cloudflare lost the executor during this "
                               "sidecar (container exited)")
        return analysis_jobs.run_sidecar_job(None, {"payload": payload})

    store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                          execute=execute)
    try:
        src = _src(clip, shots=(2.0, 4.0))
        grid = sidecars.chunks_for(src.shots, src.duration_s, [(0, 6)])
        assert len(grid) == 3
        # matte groups one chunk per call: kill the middle one every time
        killed["key"] = sidecars.chunk_key("matte.person", src.sha256, None,
                                           grid[1])
        h = sidecars.ensure("matte.person", src, [(0, 6)])
        assert launcher.join(120)
        st = sidecars.chunk_status(h)
        assert st[killed["key"]] == "failed"
        assert [v for k, v in st.items() if k != killed["key"]] == \
            ["ready", "ready"]
        tries = [(a, s) for a, s, keys in seen if killed["key"] in keys]
        assert [a for a, _s in tries] == [1, 2]
        assert tries[0][1] != tries[1][1]            # elsewhere
        assert launcher.stats["retries"] == 1
        rows = sidecars.fetch(h, str(tmp_path / "f"))
        assert [(r["t0"], r["t1"]) for r in rows] == [(0.0, 2.0), (4.0, 6.0)]
        assert sidecars.status(h) == "failed"
        marker = json.loads(store.get_bytes(sidecars.failed_key(
            "matte.person", src.sha256, killed["key"])))
        assert marker["attempts"] == 2 and marker["reason"] == "lost"
        # a second ensure inside the retry window does not relaunch it
        calls = launcher.stats["calls"]
        sidecars.ensure("matte.person", src, [(0, 6)])
        launcher.join(30)
        assert launcher.stats["calls"] == calls
    finally:
        w0a_support.uninstall(launcher)


def test_a_refused_launch_is_requeued_not_counted(monkeypatch, tmp_path,
                                                  clip):
    import analysis_jobs
    import remote
    state = {"n": 0}

    def execute(source, payload):
        state["n"] += 1
        if state["n"] == 1:
            raise remote.CloudflareCapacityBusy("shard is busy")
        return analysis_jobs.run_sidecar_job(None, {"payload": payload})

    _store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                           execute=execute)
    monkeypatch.setattr(sidecars._Launcher, "MAX_REQUEUES", 3)
    try:
        # no back-off in the test
        orig = launcher.submit

        def submit(*a, **kw):
            kw["delay"] = 0.0
            return orig(*a, **kw)
        launcher.submit = submit
        h = sidecars.ensure("faces.yunet", _src(clip), [(0, 6)])
        assert launcher.join(60)
        assert sidecars.status(h) == "ready"
        assert launcher.stats["requeues"] == 1
        assert launcher.stats["retries"] == 0
    finally:
        w0a_support.uninstall(launcher)


def test_interactive_runs_before_queued_prefetch(monkeypatch, tmp_path):
    order = []
    gate = threading.Event()

    def execute(source, payload):
        if not gate.is_set():
            gate.wait(10)
        order.append(payload["priority"])
        return {"chunks": {c["key"]: {"status": "ready"}
                           for c in payload["chunks"]}}

    _store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                           execute=execute, workers=1)
    try:
        src = _src("x.mp4", dur=90.0)
        sidecars.ensure("matte.person", src, [(0, 15)], priority="backfill")
        time.sleep(0.2)                              # first call is running
        sidecars.ensure("matte.person", src, [(30, 45)], priority="prefetch")
        sidecars.ensure("matte.person", src, [(60, 75)],
                        priority="interactive")
        gate.set()
        assert launcher.join(30)
        assert order == ["backfill", "interactive", "prefetch"]
    finally:
        w0a_support.uninstall(launcher)


def test_wait_spends_its_budget_and_the_timeout_fault_spends_none(local):
    import faultinject
    store, _launcher = local
    src = _src("x.mp4")
    key = "9" * 64
    store.put_bytes(sidecars.pending_key("faces.yunet", src.sha256, key),
                    json.dumps({"lease_until": time.time() + 60}).encode())
    h = sidecars.Handle("faces.yunet", src.sha256,
                        (sidecars.ChunkRef(key, 0, 1),))
    t = time.monotonic()
    assert sidecars.wait([h], 0.6) == {key: "pending"}
    assert 0.5 <= time.monotonic() - t < 3.0
    with faultinject.inject("sidecar_timeout"):
        t = time.monotonic()
        assert sidecars.wait([h], 90) == {key: "pending"}
        assert time.monotonic() - t < 0.5
    t = time.monotonic()
    assert sidecars.wait([h], 0) == {key: "pending"}   # previews never wait
    assert time.monotonic() - t < 0.5


def test_estimate_reflects_the_measured_rate_and_shards():
    sidecars.use_store(False)
    try:
        src = _src("x.mp4", dur=60.0)
        h = sidecars.ensure("matte.person", src, [(0, 60)])
        # 4 chunks x 15 s x 30 fps x 128 ms over 3 shards: 2 waves of
        # ~57.6 s (plus warm-ups) and the call overhead
        est = sidecars.estimate_s(h)
        assert 130 <= est <= 160
        f = sidecars.ensure("faces.yunet", src, [(0, 60)])
        assert sidecars.estimate_s(f) < 30
    finally:
        sidecars.use_store(None)


def test_a_fallback_chunk_is_fetched_with_its_own_model(monkeypatch, tmp_path,
                                                         clip):
    import analysis_jobs
    engines = dict(w0a_support.FAKE_ENGINES)
    engines["matte.person"] = {"name": "u2net", "model_id": "u2net_human_seg",
                               "model_version": "sha256:0123456789abcdef",
                               "path": "/fake/u2net.onnx", "threads": 1,
                               "fallback": True}
    store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                          engines=engines)
    try:
        src = _src(clip)
        h = sidecars.ensure("matte.person", src, [(0, 6)])
        assert launcher.join(90)
        assert sidecars.status(h) == "failed"         # RVM never ran
        rows = sidecars.fetch(h, str(tmp_path / "fb"))
        assert len(rows) == 1
        assert rows[0]["meta"]["model_id"] == "u2net_human_seg"
        assert rows[0]["meta"]["key"] != h.chunks[0].key
        assert launcher.stats["retries"] == 0         # not retryable
        assert analysis_jobs.U2NET_ID == "u2net_human_seg"
    finally:
        w0a_support.uninstall(launcher)


# ── reading faces without launching ─────────────────────────────────────

def test_faces_near_reads_ready_samples_only(local, clip):
    store, launcher = local
    src = _src(clip, shots=(3.0,))
    assert sidecars.faces_near(src.sha256, src.shots, 6.0, 1.0) is None
    calls = launcher.stats["calls"]
    assert launcher.stats["calls"] == calls          # a read launches nothing
    h = sidecars.ensure("faces.yunet", src, [(0, 6)])
    sidecars.wait([h], 60)
    got = sidecars.faces_near(src.sha256, list(src.shots), 6.0, 4.03)
    assert got and got[0][0] == pytest.approx([0.25, 0.2, 0.75, 0.6],
                                              abs=0.01)
    assert got[0][1] == pytest.approx(0.97, abs=1e-3)


# ── prefetch policy ─────────────────────────────────────────────────────

def test_prefetch_is_gated_and_targets_the_policy_ranges(monkeypatch):
    import config
    seen = []
    monkeypatch.setattr(sidecars, "ensure",
                        lambda kind, src, ranges, priority="interactive",
                        params=None: seen.append((kind, src, ranges,
                                                  priority)) or "h")
    info = {"duration": 300.0, "fps": 25.0, "width": 960, "height": 540}
    monkeypatch.setattr(config, "SIDECAR_PREFETCH", False)
    monkeypatch.setattr(config, "YUNET_KEEPOUT", False)
    assert sidecars.prefetch_after_index(1, "ab" * 32, "proxies/1/x.mp4",
                                         info, []) is None
    index = {"video": {"duration": 300.04}, "shots": []}
    clips = [{"order": 2, "start": 100.0, "end": 150.0},
             {"order": 1, "start": 10.0, "end": 40.0},
             {"order": 3, "start": 200.0, "end": 230.0},
             {"order": 4, "start": 250.0, "end": 280.0}]
    proxy = {"storage_key": "proxies/1/x.mp4", "duration_s": 300.0,
             "fps": 25.0, "width": 960, "height": 540}
    original = {"sha256": "ab" * 32}
    assert sidecars.prefetch_top_shorts(1, original, proxy, {}, clips) is None
    assert seen == []
    monkeypatch.setattr(config, "YUNET_KEEPOUT", True)
    assert sidecars.prefetch_after_index(1, "ab" * 32, "proxies/1/x.mp4",
                                         info, [{"start": 50.0}],
                                         duration=300.04) == "h"
    kind, src, ranges, prio = seen[-1]
    # keyed on the INDEX's duration, as every reader is
    assert (kind, ranges, prio) == ("faces.yunet", [(0.0, 300.04)],
                                    "prefetch")
    assert src.shots == (50.0,) and src.storage_key == "proxies/1/x.mp4"
    assert sidecars.prefetch_top_shorts(1, original, proxy, {}, clips) is None
    monkeypatch.setattr(config, "SIDECAR_PREFETCH", True)
    monkeypatch.setattr(config, "SIDECAR_PREFETCH_TOP_SHORTS", 3)
    assert sidecars.prefetch_top_shorts(1, original, proxy, index,
                                        clips) == "h"
    kind, src, ranges, prio = seen[-1]
    assert kind == "matte.person" and prio == "prefetch"
    assert src.duration_s == 300.04
    assert ranges == [(10.0, 40.0), (100.0, 150.0), (200.0, 230.0)]
    # never raises into the index or the shorts plan
    monkeypatch.setattr(sidecars, "ensure",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert sidecars.prefetch_after_index(1, "ab" * 32, "k", info, []) is None
    assert sidecars.prefetch_top_shorts(1, original, proxy, {}, clips) is None


def test_index_and_shorts_call_the_prefetch_once():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    idx = open(os.path.join(here, "indexer.py")).read()
    sho = open(os.path.join(here, "shorts.py")).read()
    assert idx.count("sidecars.prefetch_after_index(") == 1
    assert sho.count("sidecars.prefetch_top_shorts(") == 1


# ── the R2 store adapter ────────────────────────────────────────────────

class _FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def list_objects_v2(self, **kw):
        self.calls.append(kw)
        keys = sorted(k for k in self.objects if k.startswith(kw["Prefix"]))
        start = int(kw.get("ContinuationToken") or 0)
        page = keys[start:start + 2]
        more = start + 2 < len(keys)
        out = {"Contents": [{"Key": k, "Size": len(self.objects[k])}
                            for k in page], "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + 2)
        return out

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            e = Exception("NoSuchKey")
            e.response = {"Error": {"Code": "NoSuchKey"}}
            raise e
        import io
        return {"Body": io.BytesIO(self.objects[Key])}


def test_r2_store_pages_lists_and_reads_missing_as_none(monkeypatch):
    s3 = _FakeS3({f"sidecars/ab/x/k/{i}": b"x" * i for i in range(5)})
    store = sidecars.R2Store()
    monkeypatch.setattr(store, "client", lambda: s3)
    import config
    monkeypatch.setattr(config, "S3_BUCKET", "b")
    got = store.list("sidecars/ab/x/k/")
    assert len(got) == 5 and len(s3.calls) == 3
    assert store.get_bytes("sidecars/ab/x/k/3") == b"xxx"
    assert store.get_bytes("nope") is None


# ── AT-0A-3 with real processes: the measuring process is SIGKILLed ──────

@pytest.mark.parametrize("always", [False, True])
def test_at_0a_3_a_killed_chunk_retries_elsewhere_once(monkeypatch, tmp_path,
                                                      always):
    seen = []
    import analysis_jobs

    def execute(source, payload):
        seen.append((payload["attempt"], payload["slot"],
                     tuple(c["key"] for c in payload["chunks"])))
        return analysis_jobs.run_sidecar_job(None, {"payload": payload})

    store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                          execute=execute)
    try:
        clip = w0a_support.make_clip(str(tmp_path / "p.mp4"), dur=6.0)
        src = _src(clip, shots=(2.0, 4.0))
        spans = sidecars.chunks_for(src.shots, src.duration_s, [(0, 6)])
        poison = sidecars.chunk_key("matte.person", src.sha256, None,
                                    spans[1])
        monkeypatch.setenv("W0A_POISON_KEY", poison)
        monkeypatch.setenv("W0A_POISON_MARK", str(tmp_path / "killed"))
        monkeypatch.setenv("W0A_POISON_ALWAYS", "1" if always else "0")
        t = time.monotonic()
        h = sidecars.ensure("matte.person", src, [(0, 6)])
        assert launcher.join(120)
        wall = time.monotonic() - t
        st = sidecars.chunk_status(h)
        kills = open(tmp_path / "killed").read().count("killed")
        tries = [(a, s) for a, s, keys in seen if poison in keys]
        assert [a for a, _s in tries] == [1, 2]       # one retry, no more
        assert tries[0][1] != tries[1][1]             # on another slot
        others = [v for k, v in st.items() if k != poison]
        assert others == ["ready", "ready"]           # never collateral
        if always:
            assert kills == 2 and st[poison] == "failed"
            marker = json.loads(store.get_bytes(sidecars.failed_key(
                "matte.person", src.sha256, poison)))
            assert marker["reason"] == "oom" and marker["attempts"] == 2
            rows = sidecars.fetch(h, str(tmp_path / "f"))
            assert [(r["t0"], r["t1"]) for r in rows] == [(0.0, 2.0),
                                                          (4.0, 6.0)]
        else:
            assert kills == 1 and st[poison] == "ready"
        print(f"AT-0A-3 ({'killed twice' if always else 'killed once'}): "
              f"{len(seen)} calls, chunk states {sorted(st.values())}, "
              f"{wall:.1f}s")
    finally:
        w0a_support.uninstall(launcher)


def test_within_a_priority_the_longest_group_runs_first(monkeypatch,
                                                        tmp_path):
    """Longest-processing-time order: the request is ready when its slowest
    shard is, so long chunks start before short ones of the same priority."""
    order = []
    gate = threading.Event()

    def execute(source, payload):
        if not gate.is_set():
            gate.wait(10)
        c = payload["chunks"][0]
        order.append(round(c["t1"] - c["t0"], 3))
        return {"chunks": {c["key"]: {"status": "ready"}
                           for c in payload["chunks"]}}

    _store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                           execute=execute, workers=1)
    try:
        src = _src("x.mp4", dur=200.0, shots=(5.0, 9.0, 60.0, 74.0, 140.0))
        sidecars.ensure("matte.person", src, [(0, 5)])        # blocks the worker
        time.sleep(0.2)
        sidecars.ensure("matte.person", src, [(5, 9)])        # 4 s
        sidecars.ensure("matte.person", src, [(60, 74)])      # 14 s
        sidecars.ensure("matte.person", src, [(140, 150)])    # 15 s chunk
        gate.set()
        assert launcher.join(30)
        assert order == [5.0, 15.0, 14.0, 4.0]
    finally:
        w0a_support.uninstall(launcher)
