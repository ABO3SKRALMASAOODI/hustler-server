import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import transcribe


def test_whisper_progress_uses_decoded_segment_time(monkeypatch):
    segments = [
        SimpleNamespace(
            end=2.0,
            words=[SimpleNamespace(word="hello", start=0.0, end=0.4)]),
        SimpleNamespace(
            end=7.5,
            words=[SimpleNamespace(word="world", start=7.0, end=7.5)]),
    ]
    model = SimpleNamespace(transcribe=lambda *_a, **_k: (
        iter(segments), SimpleNamespace(language="en", duration=10.0)))
    monkeypatch.setattr(transcribe, "get_model", lambda: model)
    monkeypatch.setattr(transcribe, "_wav_duration_s", lambda _p: 10.0)
    monkeypatch.setattr(transcribe, "_supports_hotwords", False)

    progress = []
    words, language = transcribe._transcribe_whisper(
        "/tmp/test.wav", progress_cb=progress.append)

    assert [word.w for word in words] == ["hello", "world"]
    assert language == "en"
    assert progress == [0.2, 0.75, 1.0]


def test_progress_callback_failure_never_fails_transcription(monkeypatch):
    model = SimpleNamespace(transcribe=lambda *_a, **_k: (
        iter([SimpleNamespace(end=1.0, words=[])]),
        SimpleNamespace(language="en", duration=1.0)))
    monkeypatch.setattr(transcribe, "get_model", lambda: model)
    monkeypatch.setattr(transcribe, "_wav_duration_s", lambda _p: 1.0)
    monkeypatch.setattr(transcribe, "_supports_hotwords", False)

    def broken_callback(_fraction):
        raise RuntimeError("progress store unavailable")

    words, language = transcribe._transcribe_whisper(
        "/tmp/test.wav", progress_cb=broken_callback)

    assert words == []
    assert language == "en"


def test_whisper_uses_slot_cpu_quota_without_changing_model(monkeypatch):
    import follow
    calls = []
    fake = object()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(
        WhisperModel=lambda *a, **k: (calls.append((a, k)), fake)[1]))
    monkeypatch.setattr(transcribe, "_guard_model_ram", lambda: None)
    for quota, expected in [(0.5, 1), (2, 2), (8, 8), (64, 8)]:
        monkeypatch.setattr(transcribe, "_model", None)
        monkeypatch.setattr(follow, "cpu_count", lambda: quota)
        assert transcribe.get_model() is fake
        assert calls[-1] == ((transcribe.config.WHISPER_MODEL,), dict(
            device=transcribe.config.WHISPER_DEVICE,
            compute_type=transcribe.config.WHISPER_COMPUTE,
            cpu_threads=expected))
