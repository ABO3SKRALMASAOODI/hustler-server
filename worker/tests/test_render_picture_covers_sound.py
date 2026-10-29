"""A delivered render's picture must cover its sound (ticket #1,
render-video-ends-before-audio, 22-25 Oct 2026: draft v19 of project 2592 had
a 566.9 s video stream under 601.3 s of audio, and project 4751's last insert
lost its picture; the container length check read the longer stream and
passed them). _verify_render now fails such a render (retryable), so the
worker renders once more instead of delivering it."""
import json
import os
import subprocess
import shutil

import pytest

import media
import renderer


def _probe_json(video=None, audio=None):
    streams = []
    if video is not None:
        streams.append({"codec_type": "video", "duration": str(video)})
    if audio is not None:
        streams.append({"codec_type": "audio", "duration": str(audio)})
    return json.dumps({"streams": streams})


@pytest.fixture
def no_black(monkeypatch):
    monkeypatch.setattr(media, "black_seconds", lambda *a, **k: 0.0)


def _verify(monkeypatch, video, audio, out_dur=601.3, keep_end=None):
    monkeypatch.setattr(media, "run", lambda cmd, timeout=None, **k:
                        _probe_json(video, audio))
    outro = renderer.outro_seconds(True)
    edl = {"keep": [[0.0, (keep_end or out_dur) - outro]]}
    renderer._verify_render(edl, "out.mp4", out_dur, 109171, "preview")


def test_a_picture_that_stops_34_s_before_the_sound_is_not_delivered(
        monkeypatch, no_black):
    with pytest.raises(media.MediaError) as e:
        _verify(monkeypatch, video=566.9, audio=601.3)
    assert "picture check failed" in str(e.value)
    assert "34.40s before the sound" in str(e.value)
    # Retryable: a fresh read usually completes (not a PermanentJobError).
    assert not isinstance(e.value, renderer.RenderVerificationError)


def test_a_picture_covering_the_sound_passes(monkeypatch, no_black):
    _verify(monkeypatch, video=601.3, audio=601.3)
    _verify(monkeypatch, video=601.28, audio=601.3)     # a frame of rounding
    _verify(monkeypatch, video=601.3, audio=600.9)      # sound ends first


def test_no_audio_or_a_failed_probe_never_fails_a_render(monkeypatch,
                                                         no_black):
    _verify(monkeypatch, video=601.3, audio=None)
    _verify(monkeypatch, video=None, audio=601.3)

    def boom(cmd, timeout=None, **k):
        raise media.MediaError("ffprobe missing")
    monkeypatch.setattr(media, "run", boom)
    outro = renderer.outro_seconds(True)
    renderer._verify_render({"keep": [[0.0, 10.0 - outro]]}, "out.mp4", 10.0,
                            1, "preview")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_a_real_file_with_a_short_video_stream_is_measured(tmp_path):
    out = tmp_path / "short_picture.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "testsrc=size=160x90:rate=10:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=5", "-c:v", "libx264", "-pix_fmt",
         "yuv420p", "-c:a", "aac", str(out)], check=True)
    gap = renderer._picture_gap(str(out))
    assert gap is not None and 2.5 < gap < 3.5
