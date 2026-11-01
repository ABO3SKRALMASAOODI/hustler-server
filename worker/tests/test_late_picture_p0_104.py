"""P0 #104 (1 Nov 2026): a source whose picture starts after its sound.

Asset 39026 (5.03 s, h264 video start_time 1.0 s, aac audio from 0): every
approval preview failed "the video stream ends 0.96s before the sound" and
the proxy came out 6.03 s. The renderer and make_proxy read the late START as
a short END: they held the last frame for the missing second while every trim
rebased the picture to its first frame, a second early."""
import shutil
import subprocess

import pytest

import media
import renderer


def _ffmpeg():
    return shutil.which("ffmpeg") and shutil.which("ffprobe")


def test_a_late_picture_is_measured_from_the_recording_start():
    assert media.picture_lead({"video_start": 1.0, "format_start": 0.0}) == 1.0
    # B-frame priming (a frame or two) is not a late picture
    assert media.picture_lead({"video_start": 0.067, "format_start": 0.0}) == 0
    assert media.picture_lead({"video_start": 0.5, "format_start": 0.4}) == 0
    assert media.picture_lead({"video_start": None, "format_start": 0.0}) == 0
    assert media.picture_lead({}) == 0


def _late_source(tmp_path):
    src = str(tmp_path / "late.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-itsoffset", "1",
         "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=4.033",
         "-f", "lavfi", "-i", "sine=f=440:d=4.98",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True)
    info = media.probe(src)
    assert media.picture_lead(info) == 1.0
    return src, info


def _psnr(a, ta, b, tb, tmp_path):
    fa, fb = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    for path, t, dst in ((a, ta, fa), (b, tb, fb)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i",
                        path, "-frames:v", "1", "-vf", "scale=320:180", dst],
                       check=True)
    out = subprocess.run(["ffmpeg", "-i", fa, "-i", fb, "-lavfi", "psnr",
                          "-f", "null", "-"], capture_output=True,
                         text=True).stderr
    val = out.split("average:")[1].split()[0]
    return 99.0 if val == "inf" else float(val)


@pytest.mark.skipif(not _ffmpeg(), reason="needs ffmpeg")
@pytest.mark.parametrize("keep_end", [5.033, 6.033])
def test_a_late_picture_renders_in_sync_and_verifies(tmp_path, keep_end):
    # keep_end 6.033: the EDL the old 6.03 s proxy produced
    src, info = _late_source(tmp_path)
    index = {"words": [], "video": {"width": 640, "height": 360,
                                    "fps": 30.0, "duration": info["duration"]}}
    edl = {"version": 1, "keep": [[0.0, keep_end]]}
    out = str(tmp_path / "out.mp4")
    token = renderer._PREVIEW_QUALITY.set("approval")
    try:
        dur = renderer.render_edl(edl, index, src, out, str(tmp_path),
                                  preview=True)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)
    renderer._verify_render(edl, out, dur, 0, "preview", src_path=src,
                            src_dur=info["duration"])
    assert abs(renderer._picture_gap(out)) < 0.1
    # the picture plays on the sound's clock, not a second early
    assert _psnr(src, 2.5, out, 2.5, tmp_path) > 35
    assert _psnr(src, 2.5, out, 1.5, tmp_path) < 30


@pytest.mark.skipif(not _ffmpeg(), reason="needs ffmpeg")
def test_the_proxy_of_a_late_picture_holds_its_first_frame(tmp_path):
    src, info = _late_source(tmp_path)
    px = str(tmp_path / "px.mp4")
    media.make_proxy(src, px, info["fps"], info["vfr"], info["has_audio"],
                     duration=info["duration"])
    got = media.probe(px)
    assert abs(got["duration"] - info["duration"]) < 0.1     # not 6.03 s
    assert media.picture_lead(got) == 0
    assert abs(got["video_duration"] - info["duration"]) < 0.1
    assert _psnr(src, 2.5, px, 2.5, tmp_path) > 30
