"""Ticket #555, 12 Nov 2026: clip analysis overran its deadline.

The main video's perception has always read its 540p proxy; a spliced clip's
read the source. On production clips that took 395-857 s for 4K clips of
48-174 s and for a 634 s 720p clip, against a deadline of 600 s or 1.5x the
source (job 137263: 48 s of 4K at 48 fps, 555 s of shots, tiles and faces).
A heavy or long clip now reads one proxy, decoded once; the light clips that
always met their deadline read the source as before.
"""
import os

import pytest

import indexer


@pytest.mark.parametrize("info,heavy", [
    ({"width": 3840, "height": 2160, "fps": 47.952, "duration": 48.0}, True),
    ({"width": 3840, "height": 2160, "fps": 29.994, "duration": 30.0}, True),
    ({"width": 720, "height": 1280, "fps": 29.5, "duration": 633.9}, True),
    ({"width": 1920, "height": 1080, "fps": 29.97, "duration": 60.0}, False),
    ({"width": 1080, "height": 1920, "fps": 30.0, "duration": 12.0}, False),
    ({"width": 0, "height": 0, "fps": 0, "duration": 0}, False),
    ({"width": None, "height": "x", "fps": None, "duration": None}, False),
])
def test_heavy_is_measured_from_the_probe(info, heavy):
    assert indexer.perception_is_heavy(info) is heavy


class Stop(BaseException):
    pass


def _run(monkeypatch, tmp_path, info):
    seen = {}
    monkeypatch.setattr(indexer.config, "TMP_DIR", str(tmp_path))
    monkeypatch.setattr(indexer.config, "index_source_should_stream",
                        lambda kind, size: False)
    monkeypatch.setattr(indexer.storage, "download_to",
                        lambda key, dst: open(dst, "wb").close())
    monkeypatch.setattr(indexer.media, "sha256_file", lambda p: "ab" * 32)
    monkeypatch.setattr(indexer.media, "probe", lambda p: dict(info))

    def make_proxy(src, dst, *a, **k):
        seen["proxy_from"] = os.path.basename(src)
        open(dst, "wb").close()
    monkeypatch.setattr(indexer.media, "make_proxy", make_proxy)
    monkeypatch.setattr(indexer.media, "extract_wav", lambda *a, **k: None)
    monkeypatch.setattr(indexer, "_transcribe_wav", lambda *a, **k: ([], [], None))
    monkeypatch.setattr(indexer, "_detect_silences", lambda *a, **k: [])
    monkeypatch.setattr(indexer, "_refuse_unfinished_source", lambda a: None)
    monkeypatch.setattr(indexer, "_set_progress", lambda *a, **k: None)

    def shots(src, *a, **k):
        seen["perceived"] = os.path.basename(src)
        raise Stop()
    monkeypatch.setattr(indexer.scenes, "detect_shots", shots)

    class DB:
        def run(self, fn, *a, **k):
            return None
    with pytest.raises(Stop):
        indexer._run_clip_index(DB(), {"id": 1, "project_id": 7},
                                {"id": 3, "kind": "video_clip",
                                 "storage_key": "clips/7/a.mp4",
                                 "bytes": 590_491_399})
    return seen


def test_a_4k_clip_is_perceived_from_its_proxy(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, {"duration": 48.0, "width": 3840,
                                        "height": 2160, "fps": 47.952,
                                        "has_audio": True, "vfr": False})
    assert seen == {"proxy_from": "src.mp4", "perceived": "proxy.mp4"}


def test_a_light_clip_is_still_read_directly(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, {"duration": 12.0, "width": 1080,
                                        "height": 1920, "fps": 30.0,
                                        "has_audio": True, "vfr": False})
    assert seen == {"perceived": "src.mp4"}
