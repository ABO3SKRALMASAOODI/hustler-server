"""A video over the 3-hour limit is analysed for its first three hours.

2530 (24 Oct 2026) tried a 3.16-hour stream VOD four times and 2038 (3 Oct) a
3.3-hour recording; both were refused and neither ever got a video in. The
index now covers the first MAX_DURATION_S: the proxy and the transcript audio
read only that span (an input-side -t, so the rest is never decoded), and the
asset and index carry the clamped duration.

    cd worker && PYTHONPATH=.. python -m pytest tests/test_long_source_clamp.py -q
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config                                                  # noqa: E402
import indexer                                                 # noqa: E402
import media                                                   # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def test_clamp_keeps_the_first_three_hours_and_says_so():
    info = {"duration": 11_376.0, "video_duration": 11_376.0, "fps": 60}
    out = indexer.clamp_to_duration_limit(info)
    assert out is info
    assert info["duration"] == config.MAX_DURATION_S
    assert info["video_duration"] == config.MAX_DURATION_S
    assert info["limit_s"] == config.MAX_DURATION_S
    assert info["source_duration"] == 11_376.0
    # Idempotent: the streamed path and the job both call it.
    assert indexer.clamp_to_duration_limit(info)["duration"] == config.MAX_DURATION_S


def test_a_video_within_the_limit_is_untouched():
    info = {"duration": 3600.0}
    indexer.clamp_to_duration_limit(info)
    assert info == {"duration": 3600.0}


def test_input_side_limit_precedes_the_input():
    args = media.input_args("https://bucket.invalid/a.mp4", 10800)
    i = args.index("-i")
    assert args[i - 2:i] == ["-t", "10800.000"]
    assert media.input_args("/tmp/a.mp4") == ["-i", "/tmp/a.mp4"]
    assert media.input_args("/tmp/a.mp4", 5) == ["-t", "5.000", "-i", "/tmp/a.mp4"]


def test_proxy_and_wav_commands_carry_the_limit(monkeypatch):
    commands = []
    monkeypatch.setattr(media, "run", lambda cmd, **kw: commands.append(cmd))
    media._encode_proxy("/tmp/a.mp4", "p.mp4", 30, False, True, limit_s=10800)
    media.extract_wav("/tmp/a.mp4", "a.wav", limit_s=10800)
    for cmd in commands:
        i = cmd.index("-i")
        assert cmd[i - 2:i] == ["-t", "10800.000"], cmd


def test_the_old_refusal_text_is_gone_from_the_index_path():
    import inspect
    src = inspect.getsource(indexer.run_index_job) + inspect.getsource(
        indexer._stream_index_source)
    assert "the limit is" not in src


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_real_proxy_and_wav_stop_at_the_limit(tmp_path):
    clip = tmp_path / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(clip)], check=True)
    proxy, wav = tmp_path / "proxy.mp4", tmp_path / "a.wav"
    media.make_proxy(str(clip), str(proxy), 25.0, False, True, duration=2.0,
                     limit_s=2.0)
    media.extract_wav(str(clip), str(wav), limit_s=2.0)
    assert abs(media.probe(str(proxy))["duration"] - 2.0) < 0.2
    # 16-kHz mono s16: 32,000 bytes a second after the 44-byte header.
    assert abs((wav.stat().st_size - 44) / 32000 - 2.0) < 0.1
