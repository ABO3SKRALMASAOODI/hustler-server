"""E3 (30 Oct 2026): review sheets read their stills in ONE decode.

The renderer prefetches every still its result, screening and verify sheets
need (and, in a second pass, its caption sheets') with sheets.prefetch;
each sheet then takes its tiles from that pool.

media.frames_at must write, for every requested time, the very file
media.frame_at writes (the agent's sheets must not change by a pixel):
the first frame at or after the time, through the same decoder, scale and
JPEG settings. Checked byte for byte on previews' shape (CFR 30 fps, no
B-frames) and finals' (B-frames, 29.97 and 24 fps), at random times, at
times a millisecond either side of frame boundaries, at 0 and past the end.
"""

import os
import random
import shutil
import subprocess

import pytest

import media
import sheets

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


def _video(path, rate, preset, seconds=4.0, size="360x640"):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=s={size}:r={rate}:d={seconds}", "-c:v", "libx264",
         "-preset", preset, "-crf", "27", "-g", "48", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", path], check=True)
    return path


def _times(rate_num, rate_den, seconds, rng):
    frame = rate_den / rate_num
    out = [0.0, 0.001, seconds - 0.01, seconds + 1.0]
    for k in rng.sample(range(1, int(seconds / frame) - 1), 12):
        edge = k * frame
        out += [edge - 0.001, edge, edge + 0.001]
    out += [rng.uniform(0, seconds) for _ in range(12)]
    return out


@pytest.mark.parametrize("rate,num,den,preset", [
    ("30", 30, 1, "ultrafast"),            # a preview
    ("30000/1001", 30000, 1001, "medium"),  # a final with B-frames
    ("24", 24, 1, "medium"),
])
def test_frames_at_writes_exactly_what_frame_at_writes(
        tmp_path, rate, num, den, preset):
    src = _video(str(tmp_path / "v.mp4"), rate, preset)
    rng = random.Random(7)
    times = _times(num, den, 4.0, rng)
    items = [(t, str(tmp_path / f"many_{i}.jpg")) for i, t in enumerate(times)]
    written = media.frames_at(src, items, width=200)
    compared = 0
    for i, (t, dst) in enumerate(items):
        one = str(tmp_path / f"one_{i}.jpg")
        try:
            media.frame_at(src, t, one, width=200)
        except media.MediaError:
            one = None
        if dst in written:
            assert one is not None, f"t={t}: frame_at found no frame"
            assert open(dst, "rb").read() == open(one, "rb").read(), \
                f"t={t}: a different still"
            compared += 1
        else:
            # left to frame_at: only past the end
            assert t > 3.9, f"t={t} was not placed"
    assert compared >= len(times) - 3


def test_a_file_frames_at_cannot_read_leaves_every_still_to_frame_at(
        tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert media.frames_at(str(bad), [(1.0, str(tmp_path / "a.jpg"))]) == \
        set()
    assert not os.path.exists(tmp_path / "a.jpg")


def test_prefetched_sheets_are_the_sheets_frame_at_builds(tmp_path,
                                                          monkeypatch):
    src = _video(str(tmp_path / "v.mp4"), "30", "ultrafast", seconds=3.0)
    screen = [0.1 * k for k in range(1, 29)]           # two pages
    captions = [0.05 + 0.1 * k for k in range(0, 20)]   # overlaps nothing
    work = str(tmp_path / "stills")

    def build(prefix):
        a = str(tmp_path / f"{prefix}_r.jpg")
        sheets.build_result_sheet(src, a, 2.9)
        out = [a]
        for n, page in enumerate((screen[:16], screen[16:])):
            p = str(tmp_path / f"{prefix}_s{n}.jpg")
            sheets.build_frames_sheet(src, p, page, cols=4,
                                      max_tiles=len(page), parallelism=4)
            out.append(p)
        sheets.prefetch(src, captions, work)
        p = str(tmp_path / f"{prefix}_c.jpg")
        sheets.build_frames_sheet(src, p, captions[:16], cols=4,
                                  max_tiles=16)
        out.append(p)
        return out
    calls = []
    real = media.frame_at
    monkeypatch.setattr(media, "frame_at", lambda *a, **k: calls.append(1)
                        or real(*a, **k))
    placed = sheets.prefetch(src, sheets.result_sheet_times(2.9) + screen,
                             work)
    assert placed == 9 + len(screen)
    new = build("new")
    assert calls == []                      # every still came from the pool
    sheets.release(src)
    assert not sheets._POOL
    monkeypatch.setattr(sheets, "PREFETCH_MIN", 10 ** 6)   # no pool
    old = build("old")
    assert len(calls) == 9 + len(screen) + 16
    for a, b in zip(new, old):
        assert open(a, "rb").read() == open(b, "rb").read()


def test_a_small_set_of_stills_is_not_prefetched(tmp_path):
    src = _video(str(tmp_path / "v.mp4"), "30", "ultrafast", seconds=2.0)
    assert sheets.prefetch(src, [0.5, 1.0], str(tmp_path / "s")) == 0
    assert not sheets._POOL
