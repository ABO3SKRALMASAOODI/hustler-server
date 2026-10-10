"""Track 0A: the executor-side sidecar runner (analysis_jobs.py).

The version handshake, model pins, child isolation (OOM, crash, timeout),
manifest-last publishing and the u2net fallback rung. CI-safe through the
stand-in child of tests/w0a_support.py; the real-model checks at the end run
only where the pinned model files exist (YUNET_MODEL / MATTE_RVM_MODEL).
"""

import json
import os

import pytest

import analysis_jobs
import sidecars
import w0a_support


@pytest.fixture
def local(monkeypatch, tmp_path):
    store, launcher = w0a_support.install(monkeypatch, tmp_path)
    yield store
    w0a_support.uninstall(launcher)


@pytest.fixture
def clip(tmp_path):
    return w0a_support.make_clip(str(tmp_path / "proxy.mp4"), dur=6.0)


def _payload(kind, src, spans, **over):
    sp = sidecars.spec(kind)
    params = sidecars.resolve_params(kind, over.pop("params", None))
    out = {"kind": kind,
           "source": {"sha256": src.sha256, "storage_key": src.storage_key,
                      "duration_s": src.duration_s, "fps": src.fps,
                      "width": src.width, "height": src.height,
                      "project_id": 7, "shots": list(src.shots)},
           "chunks": [{"key": sidecars.sidecar_key(
               kind, src.sha256, sp["model_id"], sp["model_version"], params,
               span), "t0": span[0], "t1": span[1]} for span in spans],
           "params": params, "model_id": sp["model_id"],
           "model_version": sp["model_version"], "format": sp["format"],
           "attempt": 1, "slot": 0}
    out.update(over)
    return out


def _run(payload):
    return analysis_jobs.run_sidecar_job(None, {"payload": payload})


def _files(store):
    out = []
    for root, _d, files in os.walk(store.root):
        out += files
    return sorted(out)


def test_the_handshake_refuses_another_model_or_format(local, clip):
    src = w0a_support.source(clip)
    for over in ({"model_version": "v0.9"}, {"model_id": "rvm_resnet50"},
                 {"format": "mask.h264.v0"}):
        res = _run(_payload("matte.person", src, [(0.0, 6.0)], **over))
        assert res["skew"] is True and res["ok"] is False
        assert all(r["reason"] == "executor_skew" and not r["retryable"]
                   for r in res["chunks"].values())
    assert _files(local) == []                      # nothing was written


def test_injected_skew_takes_the_same_path(local, clip):
    import faultinject
    src = w0a_support.source(clip)
    with faultinject.inject("executor_skew"):
        res = _run(_payload("faces.yunet", src, [(0.0, 6.0)]))
    assert res["skew"] and _files(local) == []


def test_a_key_that_does_not_match_its_range_is_refused(local, clip):
    src = w0a_support.source(clip)
    p = _payload("faces.yunet", src, [(0.0, 6.0)])
    p["chunks"][0]["t1"] = 5.0
    res = _run(p)
    assert list(res["chunks"].values())[0]["reason"] == "bad_key"
    assert _files(local) == []


def test_unknown_kind_or_no_chunks_is_a_bad_request(local):
    with pytest.raises(ValueError):
        _run({"kind": "depth.small", "chunks": [{"key": "x"}]})
    with pytest.raises(ValueError):
        _run({"kind": "faces.yunet", "chunks": []})


def test_publish_order_and_manifest_fields(local, clip, monkeypatch):
    store = local
    order = []
    real_file, real_bytes = store.put_file, store.put_bytes
    monkeypatch.setattr(store, "put_file", lambda k, p, ct="": (
        order.append(("data", k)), real_file(k, p, ct))[1])
    monkeypatch.setattr(store, "put_bytes", lambda k, b, ct="": (
        order.append(("json", k)), real_bytes(k, b, ct))[1])
    src = w0a_support.source(clip)
    res = _run(_payload("faces.yunet", src, [(0.0, 3.0), (3.0, 6.0)]))
    assert res["ok"] and set(r["status"] for r in res["chunks"].values()) \
        == {"ready"}
    writes = [(kind, k.rsplit("/", 1)[1]) for kind, k in order]
    assert writes[0][0] == "data" and writes[1][0] == "json"
    assert writes[0][1].endswith(".faces.json")
    assert writes[1][1].endswith(".json") and \
        not writes[1][1].endswith(".faces.json")
    key = next(iter(res["chunks"]))
    m = json.loads(store.get_bytes(sidecars.manifest_key(
        "faces.yunet", src.sha256, key)))
    assert m["manifest_version"] == 1 and m["src_range"] == [0.0, 3.0]
    assert m["frames"] == 30 and m["fps"] == 10.0
    assert m["width"] == 320 and m["height"] == 180
    assert m["bytes"] == len(store.get_bytes(sidecars.data_key(
        "faces.yunet", src.sha256, key)))
    # a second run finds them ready and writes nothing
    order.clear()
    again = _run(_payload("faces.yunet", src, [(0.0, 3.0), (3.0, 6.0)]))
    assert all(r.get("cached") for r in again["chunks"].values())
    assert order == []


def test_a_missing_model_fails_without_retry(monkeypatch, tmp_path, clip):
    engines = dict(w0a_support.FAKE_ENGINES, **{"faces.yunet": None})
    store, launcher = w0a_support.install(monkeypatch, tmp_path,
                                          engines=engines)
    try:
        src = w0a_support.source(clip)
        res = _run(_payload("faces.yunet", src, [(0.0, 6.0)]))
        r = list(res["chunks"].values())[0]
        assert r["reason"] == "model_missing" and not r["retryable"]
        assert r["marked"] is True
        marker = json.loads(store.get_bytes(sidecars.failed_key(
            "faces.yunet", src.sha256, list(res["chunks"])[0])))
        assert marker["reason"] == "model_missing"
        h = sidecars.ensure("faces.yunet", src, [(0, 6)])
        launcher.join(30)
        assert sidecars.status(h) == "failed"
        assert launcher.stats["retries"] == 0
    finally:
        w0a_support.uninstall(launcher)


def test_the_real_engine_picker_honours_pins_and_the_fault(monkeypatch,
                                                           tmp_path):
    import config
    import faultinject
    aj = analysis_jobs
    fake = tmp_path / "face_detection_yunet_2023mar.onnx"
    fake.write_bytes(b"not the pinned model")
    monkeypatch.setattr(config, "YUNET_MODEL", str(fake))
    path, why = aj.model_ready("yunet")
    assert path is None and "does not match" in why
    monkeypatch.setattr(config, "YUNET_MODEL", str(tmp_path / "absent.onnx"))
    assert aj.model_ready("yunet")[0] is None
    assert aj._engine("faces.yunet", faultinject) is None
    monkeypatch.setattr(aj, "model_ready", lambda name: ("/m.onnx", None))
    assert aj._engine("faces.yunet", faultinject)["model_id"] == \
        "face_detection_yunet_2023mar"
    with faultinject.inject("model_missing:faces.yunet"):
        assert aj._engine("faces.yunet", faultinject) is None
    with faultinject.inject("model_missing"):
        assert aj._engine("matte.person", faultinject) is None


def test_an_oom_killed_child_costs_only_unfinished_chunks(local, clip):
    import faultinject
    src = w0a_support.source(clip)
    with faultinject.inject("oom"):
        res = _run(_payload("matte.person", src, [(0.0, 3.0), (3.0, 6.0)]))
    assert {r["reason"] for r in res["chunks"].values()} == {"oom"}
    assert all(r["retryable"] for r in res["chunks"].values())
    assert not any(f.endswith(".json") and ".pending" not in f
                   and ".failed" not in f for f in _files(local))


def test_a_child_that_dies_after_one_chunk_keeps_that_chunk(local, clip,
                                                            monkeypatch):
    monkeypatch.setenv("W0A_FAKE_CRASH_AFTER", "1")
    src = w0a_support.source(clip)
    res = _run(_payload("faces.yunet", src, [(0.0, 3.0), (3.0, 6.0)]))
    statuses = [res["chunks"][c]["status"] for c in res["chunks"]]
    assert statuses == ["ready", "failed"]
    second = list(res["chunks"].values())[1]
    assert second["reason"] == "crash" and second["retryable"]
    assert "exited 3" in second["why"]


def test_a_child_over_budget_is_killed_and_retryable(local, clip):
    src = w0a_support.source(clip)
    res = _run(_payload("matte.person", src, [(0.0, 6.0)], budget_s=0.001))
    r = list(res["chunks"].values())[0]
    assert r["status"] == "failed" and r["reason"] in ("timeout",)
    assert r["retryable"]


def test_a_source_that_cannot_be_decoded_fails_the_chunk(local, tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 4096)
    src = w0a_support.source(str(bad))
    res = _run(_payload("faces.yunet", src, [(0.0, 6.0)]))
    r = list(res["chunks"].values())[0]
    assert r["status"] == "failed" and r["reason"] == "decode"


def test_the_runner_is_the_registered_sidecar_job_kind():
    import http_server
    import jobs_ext
    kinds = jobs_ext.load()
    assert kinds["sidecar"].runner == "analysis_jobs:run_sidecar_job"
    assert kinds["sidecar"].lane == "batch" and kinds["sidecar"].synchronous
    assert http_server.COMPUTE_RUNNERS["sidecar"].__name__ == \
        "lazy_run_sidecar_job"


# ── the pinned models themselves (local only) ───────────────────────────

_YUNET = os.environ.get("YUNET_MODEL", "")
_RVM = os.environ.get("MATTE_RVM_MODEL", "")


@pytest.mark.skipif(not (_YUNET and os.path.exists(_YUNET)),
                    reason="local-only: needs the pinned YuNet file")
def test_real_yunet_matches_its_pin_and_runs(tmp_path):
    import cv2
    import numpy as np
    path, why = analysis_jobs.model_ready("yunet")
    assert path, why
    det = cv2.FaceDetectorYN.create(path, "", (320, 180), 0.6, 0.3, 50)
    _ok, faces = det.detect(np.zeros((180, 320, 3), np.uint8))
    assert faces is None


@pytest.mark.skipif(not (_RVM and os.path.exists(_RVM)),
                    reason="local-only: needs the pinned RVM file")
def test_real_rvm_matches_its_pin_and_runs():
    import cv2
    import numpy as np
    import personseg
    path, why = analysis_jobs.model_ready("rvm_mobilenetv3")
    assert path, why
    stream = personseg.rvm_stream_on(personseg.rvm_session_at(path, 2),
                                     320, 180)
    a = stream.step(np.zeros((180, 320, 3), np.uint8), cv2)
    assert a.shape == (180, 320) and float(a.max()) < 0.5
