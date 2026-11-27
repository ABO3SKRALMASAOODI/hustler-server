"""Ticket #1938 (p6336, 27 Nov 2026): a customer dropped an audio-only .mp4
(a lofi mix) on the Studio timeline as a video layer, and every preview of the
project failed ("No video stream found in file", ffmpeg exit 234). The layer
now plays as sound and the render completes, in real ffmpeg."""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import pytest                                                 # noqa: E402

import media                                                  # noqa: E402
import renderer                                               # noqa: E402
from schemas import default_edl                               # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg not present")


def _make(tmp_path):
    source, song, out = [str(tmp_path / n) for n in
                         ("source.mp4", "song.mp4", "out.mp4")]
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "testsrc2=s=160x90:r=30:d=12", "-f", "lavfi", "-i",
               "anullsrc=r=48000:cl=stereo", "-t", "12", "-c:v", "libx264",
               "-preset", "ultrafast", "-c:a", "aac", "-shortest", source])
    # an .mp4 with sound and no picture, like the customer's music mix
    media.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=30", "-c:a", "aac", song])
    return source, song, out


def _volume(path, start, dur):
    import subprocess
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss",
                          str(start), "-t", str(dur), "-i", path, "-af",
                          "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    for line in err.splitlines():
        if "mean_volume" in line:
            return float(line.split("mean_volume:")[1].split()[0])
    return -91.0


@needs_ffmpeg
@pytest.mark.parametrize("preview", [True, False])
def test_audio_only_video_overlay_renders_as_sound(tmp_path, preview):
    source, song, out = _make(tmp_path)
    assert not renderer.has_picture(song)
    edl = default_edl(12)
    edl["overlays"] = [{"id": "ov1", "kind": "video", "asset_key": "clip.mp4",
                        "start": 4.0, "duration_s": 4.0, "fit": "cover",
                        "x": 0.5, "y": 0.5, "scale": 0.4}]
    duration = renderer.render_edl(
        edl, {"video": {"duration": 12}, "words": []}, source, out,
        str(tmp_path), preview, suppress_outro=True,
        asset_locals={"clip.mp4": song})
    assert duration == pytest.approx(12, abs=.2)
    info = media.probe(out)
    assert info["width"] > 0
    # the song sounds inside the layer's window and not before it
    assert _volume(out, 4.5, 3.0) > -45.0
    assert _volume(out, 0.5, 3.0) < -60.0


def test_sound_only_overlays_keeps_pictures_and_moves_sound(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(renderer, "has_picture", lambda p: p != "song")
    monkeypatch.setattr(media, "has_audio_stream", lambda p: True)
    locals_ = {"a.mp4": "song", "b.mp4": "clip"}
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    edl = {"music": [], "overlays": [
        {"id": "ov1", "kind": "video", "asset_key": "a.mp4", "start": 2.0,
         "duration_s": 5.0, "source_start_s": 1.5},
        {"id": "ov2", "kind": "video", "asset_key": "b.mp4", "start": 0.0,
         "duration_s": 1.0},
        {"id": "ov3", "kind": "image", "asset_key": "c.png", "start": 0.0,
         "duration_s": 1.0}]}
    out = renderer._sound_only_overlays(edl, str(tmp_path), locals_)
    assert [o["id"] for o in out["overlays"]] == ["ov2", "ov3"]
    assert out["music"] == [{"id": "ov1-sound", "storage_key": "a.mp4",
                             "start": 2.0, "end": 7.0, "offset_s": 1.5,
                             "provenance": "user_upload"}]
    assert len(edl["overlays"]) == 3        # the stored EDL is untouched
