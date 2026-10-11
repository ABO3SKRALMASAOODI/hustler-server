"""Shared fixtures for track 0A's tests (tests/test_w0a_*.py).

CI has no model files, so the sidecar CHILD process is replaced by a small
wrapper that installs stand-in models and then runs the real child loop
(analysis_jobs._child_main): the real ffmpeg decode, mask encode, QA, JSON
writing, publishing and manifest-last ordering are exercised; only the
forward pass is a luma threshold (matte) or a fixed box (faces).
"""

import os
import subprocess
import sys
import textwrap

WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_CHILD = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, {worker!r})
    import numpy as np
    import analysis_jobs, personseg

    class _Stream:
        def step(self, frame, cv2):
            g = frame.mean(axis=2).astype(np.float32) / 255.0
            return (g > 0.5).astype(np.float32)

    personseg.rvm_session_at = lambda path, threads=0: object()
    personseg.rvm_stream_on = lambda sess, w, h: _Stream()
    personseg.segment = lambda frame, cv2: np.full((320, 320), 0.25, np.float32)

    import cv2

    class _Det:
        def __init__(self, size):
            self.size = size
        def setInputSize(self, size):
            self.size = size
        def detect(self, img):
            h, w = img.shape[:2]
            row = np.array([[0.25 * w, 0.2 * h, 0.5 * w, 0.4 * h]
                            + [0.4 * w, 0.3 * h] * 5 + [0.97]], np.float32)
            return 1, row

    cv2.FaceDetectorYN.create = staticmethod(
        lambda model, cfg, size, *a, **k: _Det(size))

    crash_after = int(os.getenv("W0A_FAKE_CRASH_AFTER", "0") or 0)
    if crash_after:
        real = dict(analysis_jobs._CHILD)
        done = []
        def wrap(fn):
            def run(spec, ch):
                if len(done) >= crash_after:
                    os._exit(3)
                done.append(1)
                return fn(spec, ch)
            return run
        for k, fn in real.items():
            analysis_jobs._CHILD[k] = wrap(fn)
    poison = os.getenv("W0A_POISON_KEY", "")
    if poison:
        mark = os.environ["W0A_POISON_MARK"]
        always = os.getenv("W0A_POISON_ALWAYS") == "1"
        for k, fn in dict(analysis_jobs._CHILD).items():
            def run(spec, ch, fn=fn):
                if ch["key"] == poison and (always or not os.path.exists(mark)):
                    open(mark, "a").write("killed;")
                    import signal
                    os.kill(os.getpid(), signal.SIGKILL)   # the shard dies
                return fn(spec, ch)
            analysis_jobs._CHILD[k] = run
    sys.exit(analysis_jobs._child_main(sys.argv[2]))
''')


def write_fake_child(tmp_path):
    path = os.path.join(str(tmp_path), "fake_child.py")
    with open(path, "w") as f:
        f.write(FAKE_CHILD.format(worker=WORKER))
    return path


def make_clip(path, dur=6.0, fps=25, w=320, h=180):
    """A synthetic clip: a white square crossing a dark frame."""
    subprocess.check_call([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"color=c=0x202020:s={w}x{h}:r={fps}:d={dur}",
        "-f", "lavfi", "-i", f"color=c=white:s={w // 4}x{h // 2}:r={fps}:d={dur}",
        "-filter_complex",
        f"[0][1]overlay=x='(W-w)*t/{dur}':y=(H-h)/2:shortest=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps),
        "-movflags", "+faststart", path])
    return path


FAKE_ENGINES = {
    "matte.person": {"name": "rvm_mobilenetv3",
                     "model_id": "rvm_mobilenetv3_fp32",
                     "model_version": "v1.0.0", "path": "/fake/rvm.onnx",
                     "threads": 1, "fallback": False},
    "faces.yunet": {"name": "yunet",
                    "model_id": "face_detection_yunet_2023mar",
                    "model_version": "2023mar", "path": "/fake/yunet.onnx",
                    "threads": 1, "fallback": False},
}


def install(monkeypatch, tmp_path, engines=FAKE_ENGINES, workers=3,
            execute=None):
    """A local store, the fake child, stand-in engines and a fresh launcher.
    Returns (store, launcher)."""
    import analysis_jobs
    import sidecars
    store = sidecars.LocalStore(os.path.join(str(tmp_path), "store"))
    child = write_fake_child(tmp_path)
    monkeypatch.setattr(analysis_jobs, "child_command",
                        lambda spec: [sys.executable, child, "--child", spec])
    monkeypatch.setattr(analysis_jobs, "_engine",
                        lambda kind, fi: dict(engines[kind])
                        if engines.get(kind) else None)
    sidecars.use_store(store)

    def run_local(source, payload):
        return analysis_jobs.run_sidecar_job(
            None, {"id": None, "type": "sidecar", "payload": payload})
    launcher = sidecars._Launcher(execute=execute or run_local,
                                  workers=workers)
    sidecars.use_launcher(launcher)
    return store, launcher


def uninstall(launcher=None):
    import sidecars
    if launcher is not None:
        launcher.join(60)
    sidecars.use_store(None)
    sidecars.use_launcher(None)


def source(path, dur=6.0, fps=25.0, w=320, h=180, shots=(), sha="ab" * 32):
    import sidecars
    return sidecars.SourceRef(sha256=sha, storage_key=path, duration_s=dur,
                              fps=fps, width=w, height=h, project_id=7,
                              shots=tuple(shots))
