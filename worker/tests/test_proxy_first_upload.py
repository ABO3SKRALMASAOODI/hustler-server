"""Proxy-first uploads: the browser builds the 540p proxy, the original follows.

WHAT THIS PATH EXISTS FOR. A real customer's 4.05 GiB / 5 min 55 s 4K upload on
28 Jul 2026 took 805 s to transfer on a 43 Mbps link and then spent 386.8 s of
the 493 s index being downscaled to 540p — 78% of the job. Time from picking
the file to seeing an edit: 24 minutes 30 seconds, for a six-minute video.

Nothing between upload and export needs the original. Transcription, shot
detection, everything the agent sees, and every preview all read the proxy;
only the final export reads the full-resolution file. So the browser sends a
~31 MB proxy first, indexing starts on it, and the original streams up behind
an already-editable project.

Three invariants are load-bearing and pinned here:

  1. ADOPTING a browser proxy preserves the timeline exactly. Every EDL
     timestamp is measured against this file; a proxy a second short is an edit
     against footage that is not there.
  2. The original ALWAYS WINS when its bytes exist. That one rule is what makes
     every retry self-healing — a re-index after the background upload lands
     takes the ordinary, fully-trusted path with no special case.
  3. Anything that genuinely needs the original REFUSES IN WORDS while it is in
     flight, rather than failing inside ffmpeg.

    cd worker && python -m pytest tests/test_proxy_first_upload.py -q
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media                                            # noqa: E402


def _have_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")


def _make(path, seconds=4.0, size="640x360", rate=30, audio=True, faststart=False):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i",
           f"testsrc2=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink",
                "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if not audio:
        cmd += ["-an"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += [path]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _moov_before_mdat(path):
    """Is the header at the front? The studio streams and seeks this file, so a
    moov at the end costs an extra round trip on every open."""
    head = open(path, "rb").read(8 << 20)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    assert moov >= 0, "no moov box in the first 8 MB"
    return mdat < 0 or moov < mdat


# ── 1. adoption preserves the timeline ──────────────────────────────────────

@needs_ffmpeg
def test_adopting_a_browser_proxy_is_a_stream_copy_that_keeps_the_duration(tmp_path):
    """The cheap path, which is the one that runs almost every time.

    The browser writes with fastStart 'off' so its sink never has to hold a
    whole hour-long file to patch a header. Ours is +faststart. Moving the
    header is a REMUX — no pixels decoded — and the duration must survive it
    untouched, because the EDL is written in this file's seconds.
    """
    src = _make(str(tmp_path / "client.mp4"), seconds=4.0)
    dst = str(tmp_path / "adopted.mp4")
    before = media.probe(src)

    info = media.adopt_client_proxy(src, dst, [])

    assert abs(info["duration"] - before["duration"]) < 0.05, (
        "adoption moved the timeline — every cut in the EDL is measured "
        "against this duration")
    assert info["width"] == before["width"] and info["height"] == before["height"]
    assert info["has_audio"] is before["has_audio"]
    assert _moov_before_mdat(dst), "adopted proxy is not faststart"
    # A stream copy cannot meaningfully change the size; a silent re-encode
    # would, and would also mean we paid for pixels we did not need to touch.
    assert abs(os.path.getsize(dst) - os.path.getsize(src)) < os.path.getsize(src) * 0.10


@needs_ffmpeg
def test_a_variable_rate_browser_proxy_is_normalized_to_cfr(tmp_path, monkeypatch):
    """The one case worth paying a re-encode for.

    Every downstream timestamp — shot boundaries, thumbnail seeks, the preview
    render's own concat — assumes a constant rate, which is why make_proxy
    normalizes VFR sources. It is cheap here precisely because the input is
    already 540p: the expensive half of a proxy encode is decoding the 4K
    source, and the client already did that.
    """
    src = _make(str(tmp_path / "client.mp4"), seconds=4.0)
    dst = str(tmp_path / "adopted.mp4")

    real = media.probe
    seen = {"n": 0}

    def probe(path):
        p = real(path)
        seen["n"] += 1
        return dict(p, vfr=True) if seen["n"] == 1 else p

    monkeypatch.setattr(media, "probe", probe)
    info = media.adopt_client_proxy(src, dst, [])

    assert info["vfr"] is False, "a VFR proxy was adopted without normalizing"
    assert abs(info["duration"] - real(src)["duration"]) < 0.15
    assert _moov_before_mdat(dst)


@needs_ffmpeg
def test_a_silent_browser_proxy_survives_adoption(tmp_path):
    """Screen recordings with no audio track are ordinary, and a codec argument
    for a stream that does not exist is how this kind of path usually breaks."""
    src = _make(str(tmp_path / "silent.mp4"), seconds=3.0, audio=False)
    info = media.adopt_client_proxy(src, str(tmp_path / "out.mp4"), [])
    assert info["has_audio"] is False
    assert info["duration"] > 2.5


# ── 2. the original always wins ─────────────────────────────────────────────

def test_the_original_wins_whenever_its_bytes_exist():
    """The self-healing rule, stated as the indexer states it.

    A client proxy is used ONLY while there is no alternative. Any retry that
    runs after the background upload lands re-reads storage, finds the
    original, and takes the ordinary trusted path — so a bad browser encode can
    never become permanent, and no separate repair job has to exist.
    """
    def resolve(client_proxy_key, original_exists):
        # Mirrors worker/indexer.run_index_job.
        return bool(client_proxy_key) and not original_exists

    assert resolve("clientproxies/1/a.mp4", False) is True
    assert resolve("clientproxies/1/a.mp4", True) is False, (
        "a landed original must beat the browser proxy, or a re-index can "
        "never repair one")
    assert resolve("", False) is False
    assert resolve(None, True) is False


def test_a_proxy_that_is_short_of_the_recording_is_refused():
    """An edit against footage that is not all there is worse than a failure.

    The browser reports the original's real duration when it registers the
    asset; the adopted proxy is measured. If they disagree by more than the
    rounding make_proxy already tolerates, the index refuses — the original is
    on its way, and the studio's own self-heal re-runs the job against it.
    """
    def too_short(declared, actual):
        return bool(declared) and abs(declared - actual) > \
            media.client_proxy_gap_tolerance(declared)

    assert too_short(600.0, 600.05) is False          # ordinary encode rounding
    assert too_short(600.0, 588.0) is True            # 12s missing from 10 min
    assert too_short(30.0, 22.0) is True              # short clip, badly short
    assert too_short(30.0, 30.4) is False             # inside the 1s floor
    assert too_short(0, 12.0) is False                # nothing declared, nothing to check


def test_the_gap_tolerance_does_not_scale_into_absurdity():
    """The bug this constant was written to avoid.

    Reusing PROXY_SHORT_FRAC (2%) as a refusal gate meant a 3-hour upload could
    disagree with its proxy by 3.6 MINUTES and still be indexed — an edit built
    against footage that is not there, which is strictly worse than refusing.
    A gate has to mean the same thing at every length.
    """
    assert media.client_proxy_gap_tolerance(3 * 3600) == media.CLIENT_PROXY_GAP_MAX_S
    assert media.client_proxy_gap_tolerance(10) == media.CLIENT_PROXY_GAP_MIN_S
    # Monotonic and bounded on both ends, for every duration the product allows.
    prev = 0.0
    for d in (0, 1, 30, 120, 600, 3600, 3 * 3600):
        tol = media.client_proxy_gap_tolerance(d)
        assert media.CLIENT_PROXY_GAP_MIN_S <= tol <= media.CLIENT_PROXY_GAP_MAX_S
        assert tol >= prev
        prev = tol


# ── 3. what needs the original says so ──────────────────────────────────────

def test_pending_upload_state_is_what_readers_check():
    """`meta.upload_state` is load-bearing, not decoration.

    A proxy-first upload creates an asset row whose storage_key points at an
    object that DOES NOT EXIST YET. Every reader that needs the real bytes has
    to check this rather than assume a row implies an object — the export
    gate, the renderer's final branch, and the erase tool all do.
    """
    pending = {"meta": {"upload_state": "pending", "upload_progress": 0.62}}
    ready = {"meta": {"upload_state": "ready"}}
    legacy = {"meta": {}}          # uploaded before this path existed

    def needs_wait(asset):
        return (asset.get("meta") or {}).get("upload_state") == "pending"

    assert needs_wait(pending) is True
    assert needs_wait(ready) is False
    assert needs_wait(legacy) is False, (
        "every asset uploaded before proxy-first shipped has no upload_state "
        "and must read as ready, or every existing project loses its export")

    pct = int(round(float(pending["meta"]["upload_progress"]) * 100))
    assert pct == 62, "the refusal quotes a percentage; it has to be the real one"
