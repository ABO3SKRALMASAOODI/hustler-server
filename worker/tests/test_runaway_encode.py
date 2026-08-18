"""A filtergraph that cannot terminate must die in seconds, not in an hour.

The watermark shipped with `-loop 1 -i robot.png` and no `-t`, overlaid with
ffmpeg's default `shortest=0`. The result was a stream with no end. Two real
exports each pinned an 8-vCPU container and a media slot for a FULL HOUR,
until Cloud Run's request deadline killed them — and every existing safety net
missed it:

  * the stall watchdog waits for SILENCE, and a runaway is loud: it emits
    -progress for as long as you let it;
  * the wall-clock cap was 5400s, ABOVE Cloud Run's own 3600s timeout, so the
    platform always killed the request first and the cap never fired once;
  * `progress_cb(min(0.999, secs / expected_out_s))` clamped the one number
    that proved what was happening, reporting a serene 99.9% forever.

The fix uses a quantity the renderer already knows exactly — how long the
output is supposed to be — so this is proof, not a heuristic.
"""
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config                                                  # noqa: E402
import media                                                   # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _overrun_at(expected):
    return expected * config.FFMPEG_OVERRUN_FACTOR + config.FFMPEG_OVERRUN_FLOOR_S


def test_wall_clock_cap_is_below_the_platform_request_timeout():
    """At 5400 > 3600 this cap was dead code and a wedge produced no error at
    all — the request just vanished when Cloud Run killed it. Whatever the
    platform limit becomes, ours has to bind first so the failure is OURS and
    carries a message."""
    assert config.FFMPEG_TIMEOUT_S < 3600, (
        f"FFMPEG_TIMEOUT_S={config.FFMPEG_TIMEOUT_S} is at or above Cloud Run's "
        "3600s request timeout, so it can never fire")
    assert config.FFMPEG_STALL_TIMEOUT_S < config.FFMPEG_TIMEOUT_S


def test_overrun_threshold_leaves_real_encodes_room():
    # Generous everywhere: a 5s clip tolerates >3x, a 2h render still gets 25%.
    assert _overrun_at(5) >= 15
    assert _overrun_at(60) >= 80
    assert _overrun_at(7200) >= 9000


def test_the_check_runs_before_the_clamp_that_hid_it():
    """Order matters: below the clamp, `secs` is already crushed to 0.999 and
    the evidence is gone. This asserts the source order, because a refactor
    that moves the check after the clamp would silently restore the bug."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "media.py")).read()
    body = src[src.index('line.startswith("out_time_ms=")'):]
    # Match the CALL, not prose — the comment above it also says min(0.999.
    assert body.index("if secs > overrun_at") < body.index("progress_cb(min("), \
        "the runaway check must read out_time BEFORE it is clamped to 0.999"


@pytest.mark.parametrize("sentinel", [
    9223372036854775807,
    9223372036854775000,
    -9223372036854775808,
])
def test_ffmpeg_nopts_progress_sentinel_is_not_a_runaway(sentinel):
    """AV_NOPTS_VALUE means the clock is unavailable, not years of output."""
    seen = []
    script = (
        f"print('out_time_ms={sentinel}', flush=True); "
        "print('out_time_ms=2000000', flush=True); "
        "print('progress=end', flush=True)"
    )

    media.run([sys.executable, "-c", script],
              progress_cb=seen.append, expected_out_s=3.0)

    assert seen == [pytest.approx(2.0 / 3.0)]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs a real ffmpeg")
def test_a_runaway_graph_is_killed_in_seconds(tmp_path):
    png = str(tmp_path / "still.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=white:s=64x94:d=0.1", "-frames:v", "1", png],
                   check=True)
    out = str(tmp_path / "runaway.mp4")
    # Exactly the shipped shape: an unbounded looped still + shortest=0.
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
           "-loop", "1", "-i", png,
           "-filter_complex", "[0:v][1:v]overlay=5:5:shortest=0[v]",
           "-map", "[v]", "-t", "600", "-c:v", "libx264", "-preset", "ultrafast",
           "-progress", "pipe:1", "-nostats", out]
    t0 = time.monotonic()
    with pytest.raises(media.MediaError) as ei:
        media.run(cmd, progress_cb=lambda f: None, expected_out_s=3.0)
    elapsed = time.monotonic() - t0
    assert "runaway encode" in str(ei.value)
    # It would otherwise run for the full 600s -t (and in prod, forever).
    assert elapsed < 30, f"took {elapsed:.1f}s to notice a runaway"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs a real ffmpeg")
@pytest.mark.parametrize("expected", [3.0, 2.5, 2.0])
def test_a_correct_encode_is_never_killed(tmp_path, expected):
    """Including when the timeline UNDER-estimates the output by a third — a
    false positive here would kill real customer exports, which is strictly
    worse than the bug this guards against."""
    png = str(tmp_path / "still.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=white:s=64x94:d=0.1", "-frames:v", "1", png],
                   check=True)
    out = str(tmp_path / "ok.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
           "-loop", "1", "-i", png,
           "-filter_complex", "[0:v][1:v]overlay=5:5:shortest=1[v]",
           "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
           "-progress", "pipe:1", "-nostats", out]
    media.run(cmd, progress_cb=lambda f: None, expected_out_s=expected)
    assert os.path.getsize(out) > 0
