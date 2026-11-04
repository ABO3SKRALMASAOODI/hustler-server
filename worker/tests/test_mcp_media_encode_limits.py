"""Large transfer allowances must still produce valid short watch copies."""
import shutil
import subprocess

import pytest

import config
import media
import mcp_media

MB = 1048576


@pytest.mark.parametrize("duration,budget_mb,height", [
    (0.1, 512, 1920), (0.5, 512, 1080), (3.5, 512, 1920),
    (3.5, 1024, 480), (0.1, 512, 128), (0.1, 512, 180),
    (60, 50, 1080), (1200, 8, 1080), (3600, 2, 1080),
])
def test_watch_plan_fits_encoder_buffer_and_preserves_size_policy(
        duration, budget_mb, height):
    h, kbps, fps, tight = mcp_media.encode_plan(
        duration, budget_mb * MB, height, None, 30)
    # ffmpeg's bufsize is signed int32 in bits, whereas our plan is kbps
    # with a two-second VBV buffer. The original 3.5s incident overflowed it.
    assert 0 < kbps * 2000 <= 2**31 - 1
    assert h <= min(height, config.MCP_VIDEO_HEIGHT)
    assert fps <= min(30, config.MCP_VIDEO_FPS_CAP)
    if not tight:
        payload_bits = (kbps + config.MCP_VIDEO_AUDIO_KBPS) * 1000 * duration
        assert payload_bits <= budget_mb * MB * 8


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required")
    path = tmp_path_factory.mktemp("watch-source") / "source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=960x540:rate=30", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000", "-t", "4",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac", "-y", str(path),
    ], check=True, capture_output=True, timeout=30)
    return path


@pytest.mark.parametrize("duration", [0.1, 3.5])
def test_short_window_really_encodes_and_decodes_with_large_allowance(
        source_clip, tmp_path, duration):
    height, kbps, fps, _ = mcp_media.encode_plan(
        duration, 512 * MB, 540, None, 30)
    out = tmp_path / "window.mp4"
    mcp_media._encode(str(source_clip), str(out), start=0.5,
                      duration=duration, height=height, video_kbps=kbps,
                      fps=fps, has_audio=True)
    info = media.probe(str(out))
    assert info["has_audio"]
    assert info["height"] == height
    assert abs(info["duration"] - duration) < 0.15
    assert out.stat().st_size < 512 * MB
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(out),
                    "-f", "null", "-"], check=True,
                   capture_output=True, timeout=30)
