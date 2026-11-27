"""Tickets #1935/#1936, 27 Nov 2026: a sound-only .mp4 broke the project.

A customer (p6336) uploaded a lofi music mix saved as .mp4 with no picture
track. Its index failed ("No video stream found in file"), and once it sat on
the overlay track every preview failed: the graph bound [idx:v] to nothing
("Error binding filtergraph inputs/outputs: Invalid argument"). The clip is a
sound: the index reads it as one and the renderer plays it on its window.
"""
import os
import subprocess

import pytest

import indexer
import media
import renderer
from schemas import default_edl


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True,
                   timeout=60)


@pytest.fixture
def files(tmp_path):
    src = str(tmp_path / "src.mp4")
    snd = str(tmp_path / "lofi.mp4")
    _ff("-f", "lavfi", "-i", "testsrc=s=320x240:d=4:r=30",
        "-f", "lavfi", "-i", "sine=f=300:d=4", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src)
    _ff("-f", "lavfi", "-i", "sine=f=800:d=6", "-c:a", "aac", snd)
    return src, snd


def _band_db(path, start, length):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(length),
         "-i", path, "-af",
         "bandpass=f=800:width_type=h:w=60,volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60).stderr
    line = [x for x in out.splitlines() if "mean_volume" in x][-1]
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_stream_probe_tells_sound_only_from_picture(files):
    src, snd = files
    assert media.has_video_stream(src) is True
    assert media.has_video_stream(snd) is False
    assert media.has_audio_stream(snd) is True


def test_sound_only_overlay_renders_and_is_heard_on_its_window(files,
                                                                tmp_path):
    src, snd = files
    edl = default_edl(4.0)
    edl["keep"] = [[0.0, 1.5], [2.0, 4.0]]
    edl["overlays"] = [{"id": "ov1", "kind": "video",
                        "asset_key": "clips/1/lofi.mp4", "start": 0.5,
                        "duration_s": 2.0, "scale": 0.4, "x": 0.5,
                        "y": 0.5, "fit": "cover"}]
    out = str(tmp_path / "out.mp4")
    work = tmp_path / "work"
    work.mkdir()
    renderer.render_edl(
        edl, {"words": [], "sentences": [], "silences": [], "shots": []},
        src, out, str(work), True,
        asset_locals={"clips/1/lofi.mp4": snd})
    assert os.path.getsize(out) > 0
    # The 800 Hz cue sounds inside the overlay's window, not after it.
    assert _band_db(out, 1.0, 1.0) > _band_db(out, 2.8, 0.6) + 6.0


def test_helper_leaves_picture_overlays_alone(files):
    src, snd = files
    edl = {"overlays": [
        {"id": "a", "kind": "video", "asset_key": "pic", "start": 0,
         "duration_s": 1.0},
        {"id": "b", "kind": "video", "asset_key": "snd", "start": 1.0,
         "duration_s": 2.0, "source_start_s": 0.5},
        {"id": "c", "kind": "image", "asset_key": "img", "start": 0,
         "duration_s": 1.0}],
        "music": []}
    locals_ = {"pic": src, "snd": snd}
    out, notes = renderer._sound_only_overlays_to_music(
        edl, lambda k: locals_[k])
    assert [o["id"] for o in out["overlays"]] == ["a", "c"]
    assert out["music"] == [{"id": "b_sound", "storage_key": "snd",
                             "start": 1.0, "end": 3.0, "offset_s": 0.5}]
    assert len(notes) == 1
    same, none = renderer._sound_only_overlays_to_music(
        {"overlays": [edl["overlays"][0]]}, lambda k: locals_[k])
    assert none == [] and same["overlays"] == [edl["overlays"][0]]


class _Stop(BaseException):
    pass


def test_clip_index_reads_a_sound_only_clip_as_sound(monkeypatch, tmp_path,
                                                     files):
    _src, snd = files
    seen = {"meta": []}
    monkeypatch.setattr(indexer.config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(indexer.config, "index_source_should_stream",
                        lambda kind, size: False)
    monkeypatch.setattr(indexer.storage, "download_to",
                        lambda key, dst: _ff("-i", snd, "-c", "copy", dst))
    monkeypatch.setattr(indexer, "_refuse_unfinished_source", lambda a: None)
    monkeypatch.setattr(indexer, "_set_progress", lambda *a, **k: None)
    monkeypatch.setattr(indexer.scenes, "detect_shots",
                        lambda *a, **k: pytest.fail("no picture to read"))

    def transcribe(job_id, wav, dur, warnings, *a, **k):
        seen["duration"] = dur
        raise _Stop()
    monkeypatch.setattr(indexer, "_transcribe_wav", transcribe)

    class DB:
        def run(self, fn, *a, **k):
            if fn is indexer.dbx.update_asset_meta:
                seen["meta"].append(a[1])
            return None
    with pytest.raises(_Stop):
        indexer._run_clip_index(DB(), {"id": 1, "project_id": 7},
                                {"id": 3, "kind": "video_clip",
                                 "storage_key": "clips/7/lofi.mp4",
                                 "bytes": 120_000})
    assert {"no_video": True} in seen["meta"]
    assert seen["duration"] == pytest.approx(6.0, abs=0.1)
