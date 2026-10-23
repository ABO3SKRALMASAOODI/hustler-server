"""The delivered file's true peak holds the -1 dBTP ceiling (studio project
2592, 22 Oct 2026: a mastered preview measured +0.33 dBTP and audio QC told
the editor to call set_master_loudness, which was already on)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import audio_qc  # noqa: E402
import config  # noqa: E402
import renderer  # noqa: E402
import true_peak  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")


def _make(path, audio_expr, seconds=6.0):
    """A small mp4: a moving test picture and the given mono expression,
    AAC 128k like a preview."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=s=160x90:r=24:d={seconds}",
         "-f", "lavfi", "-i",
         f"aevalsrc='{audio_expr}':s=48000:d={seconds}",
         "-filter_complex", "[1:a]pan=stereo|c0=c0|c1=c0[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", path],
        check=True, capture_output=True)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,nb_frames:format=duration",
         "-of", "json", path], check=True, capture_output=True)
    return json.loads(out.stdout)


@needs_ffmpeg
def test_an_encode_over_the_ceiling_is_held_under_it(tmp_path):
    # Square-wave bursts at full scale: the AAC encode reconstructs them well
    # over the level they were written at, like the 2592 gaming mix.
    path = str(tmp_path / "hot.mp4")
    _make(path, "0.98*sgn(sin(2*PI*330*t))*gt(mod(t,1.5),0.7)"
                "+0.3*sin(2*PI*90*t)")
    before = true_peak.measure(path)
    assert before is not None and before > true_peak.CEILING_DBTP
    info_before = _probe(path)

    report = true_peak.guard(path, bitrate="128k", workdir=str(tmp_path))

    assert report["tp_in"] == pytest.approx(before, abs=0.01)
    assert report["passes"] >= 1 and report["held"] is True
    after = true_peak.measure(path)
    assert after <= true_peak.CEILING_DBTP
    assert report["tp"] == pytest.approx(after, abs=0.01)
    # The loudnorm reading audio QC reports agrees with the guard's.
    qc = audio_qc.measure(path, duration_s=6.0, master="social")
    assert qc["tp"] <= audio_qc.TP_FINDING_DBTP
    assert not any("true peak" in f for f in qc["findings"])
    # Only the sound changed: same picture stream, same length.
    info_after = _probe(path)
    v0 = [s for s in info_before["streams"] if s["codec_type"] == "video"]
    v1 = [s for s in info_after["streams"] if s["codec_type"] == "video"]
    assert v0[0]["codec_name"] == v1[0]["codec_name"]
    assert v0[0]["nb_frames"] == v1[0]["nb_frames"]
    assert float(info_after["format"]["duration"]) == pytest.approx(
        float(info_before["format"]["duration"]), abs=0.1)
    # No scratch passes left behind.
    assert not [p for p in os.listdir(tmp_path) if p.startswith("tp_guard_")]


@needs_ffmpeg
def test_a_file_under_the_ceiling_is_left_byte_identical(tmp_path):
    path = str(tmp_path / "calm.mp4")
    _make(path, "0.25*sin(2*PI*440*t)")
    with open(path, "rb") as fh:
        original = fh.read()
    report = true_peak.guard(path, bitrate="128k", workdir=str(tmp_path))
    assert report["passes"] == 0 and report["held"] is True
    assert report["tp"] <= true_peak.CEILING_DBTP
    with open(path, "rb") as fh:
        assert fh.read() == original


@needs_ffmpeg
def test_a_file_without_sound_is_not_measured(tmp_path):
    path = str(tmp_path / "mute.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=s=160x90:r=24:d=2", "-c:v", "libx264", "-preset",
         "ultrafast", path], check=True, capture_output=True)
    assert true_peak.measure(path) is None
    assert true_peak.guard(path) is None


def test_each_pass_lowers_the_limiter_by_the_measured_excess():
    assert true_peak.next_limit(-2.5, 0.33) == pytest.approx(-2.5 - 1.83)
    # A pass that barely missed still moves by a meaningful step.
    assert true_peak.next_limit(-4.0, -1.45) == pytest.approx(-4.25)
    chain = true_peak.limiter_chain(-6.0)
    assert "alimiter=limit=0.50119" in chain
    assert "latency=1" in chain and "level=0" in chain
    assert chain.endswith("channel_layouts=stereo")


def test_guard_keeps_the_original_when_a_correction_fails(tmp_path,
                                                          monkeypatch):
    path = tmp_path / "x.mp4"
    path.write_bytes(b"original")

    def boom(*_a, **_k):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(true_peak, "_reencode", boom)
    monkeypatch.setattr(true_peak, "_duration", lambda _p: 6.0)
    report = true_peak.guard(str(path), workdir=str(tmp_path), measured=0.5)
    assert report["held"] is False and report["tp"] == 0.5
    assert "error" in report
    assert path.read_bytes() == b"original"


def test_cached_renders_over_the_ceiling_are_rendered_again():
    stamped = {"tp_v": config.TRUE_PEAK_GUARD_VERSION}
    assert renderer.true_peak_current(stamped)
    # 89245's meta: measured +0.33 before the guard existed.
    assert not renderer.true_peak_current({"audio_qc": {"tp": 0.33}})
    assert renderer.true_peak_current({"audio_qc": {"tp": -1.4}})
    # A render nobody measured (a final before the guard) keeps its cache.
    assert renderer.true_peak_current({})
    assert renderer.true_peak_current({"audio_qc": {"tp": None}})


def test_a_mastered_mix_over_the_ceiling_never_prescribes_set_master_loudness(
        monkeypatch):
    monkeypatch.setattr(
        audio_qc, "_run_ffmpeg",
        lambda _p: '{"input_i":"-15.27","input_tp":"0.33",'
                   '"input_lra":"12.7"}')
    social = audio_qc.measure("x.mp4", duration_s=1134.5, master="social")
    [peak] = [f for f in social["findings"] if "true peak" in f]
    assert "set_master_loudness" not in peak
    assert "already on" in peak and "-1 dBTP" in peak
    natural = audio_qc.measure("x.mp4", duration_s=1134.5, master=None)
    [peak] = [f for f in natural["findings"] if "true peak" in f]
    assert "set_master_loudness" in peak
    # At the ceiling (within measurement slack) there is no finding.
    monkeypatch.setattr(
        audio_qc, "_run_ffmpeg",
        lambda _p: '{"input_i":"-14.1","input_tp":"-0.95",'
                   '"input_lra":"8"}')
    held = audio_qc.measure("x.mp4", duration_s=60, master="social")
    assert not any("true peak" in f for f in held["findings"])
