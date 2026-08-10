"""The local-whisper fallback must never OOM the worker it runs in.

Deepgram is primary; faster-whisper is its automatic fallback. The default
model is 'medium' (~1.5-2.5GB resident), and production has run on instances
far smaller than that — so the first Deepgram hiccup would have had the
fallback's own load kernel-killed, taking every in-flight turn with it and
wearing the same 'Worker died' mask as every other OOM. The guard prices the
configured model conservatively and refuses the load when the container
cannot hold it, failing ONE index cleanly instead.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest                                                  # noqa: E402

import config                                                  # noqa: E402
import storage                                                 # noqa: E402
import transcribe                                              # noqa: E402


def test_refuses_medium_on_a_small_instance(monkeypatch):
    monkeypatch.setattr(config, "WHISPER_MODEL", "medium")
    monkeypatch.setattr(storage, "_cgroup_memory_available",
                        lambda: 512 * 1024 * 1024)
    with pytest.raises(RuntimeError) as e:
        transcribe._guard_model_ram()
    msg = str(e.value)
    assert "refusing" in msg
    assert "Cloud transcription" in msg


def test_allows_when_room_exists(monkeypatch):
    monkeypatch.setattr(config, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(storage, "_cgroup_memory_available",
                        lambda: 2 * 1024 ** 3)
    transcribe._guard_model_ram()


def test_unknown_model_is_priced_conservatively(monkeypatch):
    # An unrecognized name must be treated as medium-class, not waved through.
    monkeypatch.setattr(config, "WHISPER_MODEL", "distil-whisper-exotic")
    monkeypatch.setattr(storage, "_cgroup_memory_available",
                        lambda: 1 * 1024 ** 3)
    with pytest.raises(RuntimeError):
        transcribe._guard_model_ram()


def test_unmeasurable_never_blocks(monkeypatch):
    # Outside a cgroup (local dev, tests) the guard stays out of the way.
    monkeypatch.setattr(config, "WHISPER_MODEL", "medium")
    monkeypatch.setattr(storage, "_cgroup_memory_available", lambda: None)
    transcribe._guard_model_ram()
