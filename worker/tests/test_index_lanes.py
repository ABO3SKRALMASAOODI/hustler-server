"""Round 91 — the index pipeline runs as two lanes, not a ladder.

The serial pipeline spent its p50 waiting on itself: proxy (22s), whisper
(10s), shots (7s), tiles (8s), uploads (18s), one after another, for stages
whose dependency graph is two independent chains (picture needs the proxy,
sound needs the wav, neither needs the other). run_index_job now runs the
chains side by side with all DB writes kept on the job thread (Db is
one-connection-per-thread by contract).

This is the missing end-to-end test for run_index_job: a real tiny clip goes
through the REAL make_proxy / probe / extract_wav / shot detection / tile
build, with storage and transcription faked, and the finished index must
carry every field the serial pipeline produced — plus the proxy and wav
uploads, the asset rows, and monotone main-thread progress.

LIVE ffmpeg; skipped where there is none.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


if not HAVE_FFMPEG:
    print("  -- skipped entirely (no ffmpeg on this machine)")
    print("\nALL 0 CHECKS PASSED")
    sys.exit(0)

import config                                                  # noqa: E402
import indexer                                                 # noqa: E402
import media                                                   # noqa: E402
import storage                                                 # noqa: E402
import transcribe                                              # noqa: E402

d = tempfile.mkdtemp(prefix="idxlanes_")
config.TMP_DIR = os.path.join(d, "tmp")
os.makedirs(config.TMP_DIR, exist_ok=True)

clip = os.path.join(d, "orig.mp4")
subprocess.run(
    ["ffmpeg", "-y", "-v", "error",
     "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
     "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
     "-shortest", clip], check=True)

# ---- fakes ----------------------------------------------------------------

uploads = {}


def fake_download_to(key, local):
    if key != "orig/key.mp4":
        raise RuntimeError(f"unexpected download {key}")
    shutil.copy(clip, local)


def fake_upload_file(local, key, ctype=None):
    uploads[key] = os.path.getsize(local)


def fake_exists(key):
    return key == "orig/key.mp4"


# Patched globally for the run below and RESTORED in the finally — pytest
# imports every test file into one process, so a leaked patch here poisons
# whatever collects after this file (test_units' transcribe checks did).
_orig = (storage.download_to, storage.upload_file, storage.exists,
         transcribe.transcribe, indexer._finish_setup, config.TMP_DIR)
storage.download_to = fake_download_to
storage.upload_file = fake_upload_file
storage.exists = fake_exists

FAKE_WORDS = [types.SimpleNamespace(w="hello", t0=0.2, t1=0.6, speaker=0,
                                    model_dump=lambda: {})]


def fake_transcribe(wav, warnings):
    # The REAL wav must exist and be a wav — proves the sound lane ran.
    assert os.path.exists(wav) and wav.endswith(".wav")
    from schemas import Word
    return ([Word(w="hello", t0=0.2, t1=0.6, speaker=0)], "en")


transcribe.transcribe = fake_transcribe


class FakeDb:
    """Main-thread guard + call recorder standing in for worker_db."""

    def __init__(self):
        import threading
        self.thread = threading.get_ident()
        self.progress = []
        self.assets = []
        self.index_json = None
        self.finish_calls = []

    def run(self, fn, *a, **k):
        import threading
        assert threading.get_ident() == self.thread, \
            "DB touched off the job thread — Db is one-conn-per-thread"
        name = getattr(fn, "__name__", "")
        if name == "set_progress":
            self.progress.append(a[1])
            return True
        if name == "get_asset":
            return {"id": 7, "kind": "video", "storage_key": "orig/key.mp4",
                    "duration_s": None, "width": None, "height": None}
        if name == "get_project":
            return {"id": 1, "chat_session_id": 5}
        if name == "get_index_by_sha":
            return None
        if name == "update_asset_probe":
            return None
        if name == "insert_asset":
            self.assets.append((a[1], a[2]))     # (kind, storage_key)
            return 99
        if name == "upsert_index":
            self.index_json = a[2]
            return None
        return None


db = FakeDb()

# _finish_setup touches sessions/EDLs — not what this test pins.
indexer._finish_setup = lambda *a, **k: db.finish_calls.append(a)

job = {"id": 42, "project_id": 1, "user_id": 3,
       "payload": {"asset_id": 7}}

print("== run_index_job, two lanes ==")
try:
    result = indexer.run_index_job(db, job)

    check("job completed uncached", result["cached"] is False)
    check("transcript came through the sound lane",
          result["words"] == 1 and result["language"] == "en")
    check("index json persisted with words + video block",
          db.index_json and len(db.index_json["words"]) == 1
          and db.index_json["video"]["duration"] > 3.5)
    check("filmstrip tiles built and recorded",
          result["tiles"] >= 1 and db.index_json.get("tile_keys"))
    check("proxy uploaded", any(k.startswith("proxies/") for k in uploads))
    check("wav uploaded", any(k.startswith("audio/") for k in uploads))
    check("proxy asset row inserted", ("proxy", ) ==
          tuple(k for k, _ in db.assets if k == "proxy")[:1])
    check("audio asset row inserted", any(k == "audio" for k, _ in db.assets))
    check("progress stayed monotone on the job thread",
          db.progress == sorted(db.progress))
    check("lane timings recorded",
          "lanes_s" in result["timings"] and "proxy_s" in result["timings"])
    check("finish_setup ran once", len(db.finish_calls) == 1)
finally:
    (storage.download_to, storage.upload_file, storage.exists,
     transcribe.transcribe, indexer._finish_setup, config.TMP_DIR) = _orig
    shutil.rmtree(d, ignore_errors=True)

print(f"\nALL {PASS} CHECKS PASSED")
