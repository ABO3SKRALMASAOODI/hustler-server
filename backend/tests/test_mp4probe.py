"""Reading an MP4's duration from its header alone.

A proxy-first upload registers the original's duration from what the BROWSER
measured, because for the whole time the project has been editable that object
did not exist. Every timestamp in the edit is written against that number, so
when the background upload finally lands it has to be checked against the file
— and checking it must not mean downloading the file, which is the multi-GB
transfer the entire path exists to get out of the user's way.

The two cases that matter are both real: +faststart puts moov near the front
(what our own encodes produce), and moov-at-end is what a browser writes,
because holding an hour-long file in memory to patch a header is not an option
on a phone. A 14 GB original with moov after a 14 GB mdat must still cost a few
dozen bytes to read.

    cd backend && python -m pytest tests/test_mp4probe.py -q
"""

import os
import subprocess
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mp4probe                                         # noqa: E402


def _have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")


def _make(path, seconds, faststart, container="mp4"):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={seconds}",
           "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += [path]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _reader(path, counter):
    """A fetch() over a local file that COUNTS bytes, so the test can assert
    this is a header read and not a download."""
    size = os.path.getsize(path)

    def fetch(off, length):
        if off >= size:
            return None
        counter["reads"] += 1
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read(length)
        counter["bytes"] += len(data)
        return data
    return fetch, size


@needs_ffmpeg
@pytest.mark.parametrize("faststart", [True, False])
def test_duration_is_read_from_the_header_either_way(tmp_path, faststart):
    """+faststart is what our encodes produce; moov-at-end is what the browser
    writes. Both have to work, and neither may read the media."""
    path = _make(str(tmp_path / f"v_{faststart}.mp4"), 6.0, faststart)
    counter = {"reads": 0, "bytes": 0}
    fetch, size = _reader(path, counter)

    dur = mp4probe.duration_seconds(fetch, size)

    assert dur is not None, "duration could not be read"
    assert abs(dur - 6.0) < 0.25, f"got {dur}"
    assert counter["bytes"] < 4096, (
        f"read {counter['bytes']} bytes to find a duration — this must be a "
        "header probe, not a download of a multi-GB original")


@needs_ffmpeg
def test_a_quicktime_mov_reads_the_same_way(tmp_path):
    """iPhone footage arrives as .MOV, which is the same box structure."""
    path = _make(str(tmp_path / "v.mov"), 4.0, False)
    counter = {"reads": 0, "bytes": 0}
    fetch, size = _reader(path, counter)
    dur = mp4probe.duration_seconds(fetch, size)
    assert dur is not None and abs(dur - 4.0) < 0.25, f"got {dur}"


@needs_ffmpeg
def test_walking_past_a_huge_mdat_costs_almost_nothing(tmp_path):
    """The whole point. With moov at the END, a naive reader scans forward
    through the media; this one reads box HEADERS and skips."""
    path = _make(str(tmp_path / "big.mp4"), 20.0, False)
    counter = {"reads": 0, "bytes": 0}
    fetch, size = _reader(path, counter)
    assert size > 200_000, "test file is too small to prove anything"

    dur = mp4probe.duration_seconds(fetch, size)

    assert dur is not None and abs(dur - 20.0) < 0.3
    assert counter["bytes"] < size / 50, (
        f"read {counter['bytes']} of {size} bytes — the walk is not skipping "
        "the media")


def test_garbage_fails_open_rather_than_accusing_the_browser():
    """A container we cannot parse is NOT evidence that the browser lied.
    Returning None means 'no opinion', and the caller leaves the claim alone."""
    assert mp4probe.duration_seconds(lambda o, n: b"", 100) is None
    assert mp4probe.duration_seconds(lambda o, n: None, 100) is None
    assert mp4probe.duration_seconds(lambda o, n: b"not an mp4 at all", 100) is None
    assert mp4probe.duration_seconds(lambda o, n: b"\x00" * 32, 0) is None


def test_a_malformed_box_cannot_loop_forever():
    """A size smaller than its own header would leave the walk in place. On a
    server handling uploads, an infinite loop is a worse outcome than a wrong
    answer."""
    import struct
    # A box declaring size 0... which means "to end of file", followed by a
    # type we do not descend into: the walk must terminate, not spin.
    box = struct.pack(">I4s", 0, b"mdat")
    assert mp4probe.duration_seconds(lambda o, n: box[o:o + n], len(box)) is None
    # And a size of 4, which is less than the 8-byte header it sits in.
    bad = struct.pack(">I4s", 4, b"free")
    assert mp4probe.duration_seconds(lambda o, n: bad[o:o + n], len(bad)) is None


def test_an_unknown_duration_sentinel_is_not_a_49710_hour_video():
    """0xFFFFFFFF is what a fragmented or still-being-written file reports.
    Believing it would fail the drift check on every such upload."""
    import struct
    mvhd = (b"\x00\x00\x00\x00"                    # version 0 + flags
            + struct.pack(">II", 0, 0)             # creation, modification
            + struct.pack(">I", 1000)              # timescale
            + struct.pack(">I", 0xFFFFFFFF))       # duration sentinel
    assert mp4probe._mvhd_duration(mvhd) is None
    real = (b"\x00\x00\x00\x00" + struct.pack(">II", 0, 0)
            + struct.pack(">I", 1000) + struct.pack(">I", 5500))
    assert abs(mp4probe._mvhd_duration(real) - 5.5) < 1e-9
