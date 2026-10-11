"""Track 0A: keep-out reads ready faces.yunet sidecars, behind YUNET_KEEPOUT.

The flag is off (renders unchanged) until the round lead accepts AT-0A-4.
With it on, a READY sidecar answers before the index's sparse cascade
samples; a read never launches or waits on analysis.
"""

import pytest

import config
import keepout
import sidecars
import w0a_support

SHA = "5a" * 32


def _index(dur=6.0):
    return {"video": {"duration": dur, "width": 320, "height": 180,
                      "fps": 25.0},
            "shots": [{"id": 0, "start": 0.0, "end": 3.0},
                      {"id": 1, "start": 3.0, "end": dur}],
            "tile_keys": [f"tiles/7/{SHA}/tile_000.jpg"],
            "words": [], "sentences": []}


@pytest.fixture
def warm(monkeypatch, tmp_path):
    store, launcher = w0a_support.install(monkeypatch, tmp_path)
    clip = w0a_support.make_clip(str(tmp_path / "proxy.mp4"), dur=6.0)
    src = w0a_support.source(clip, shots=(3.0,), sha=SHA)
    h = sidecars.ensure("faces.yunet", src, [(0, 6)])
    assert set(sidecars.wait([h], 60).values()) == {"ready"}
    yield store, launcher
    w0a_support.uninstall(launcher)


def test_off_by_default_and_renders_are_unchanged(warm, monkeypatch):
    assert config.YUNET_KEEPOUT is False
    assert keepout.yunet_faces(_index(), 1.0) is None
    edl = {"keep": [[0.0, 6.0]]}
    track = keepout.face_track(edl, _index(), 1080, 1920, 0.0, 6.0)
    assert all(faces == [] for _t, faces in track)   # no index samples


def test_on_it_answers_from_the_ready_sidecar(warm, monkeypatch):
    monkeypatch.setattr(config, "YUNET_KEEPOUT", True)
    faces = keepout.yunet_faces(_index(), 1.0)
    assert faces and faces[0] == pytest.approx([0.25, 0.2, 0.75, 0.6],
                                               abs=0.01)
    edl = {"keep": [[0.0, 6.0]]}
    track = keepout.face_track(edl, _index(), 1080, 1920, 0.0, 6.0)
    assert track and all(faces for _t, faces in track)
    # a measured frame still wins over the sidecar
    track = keepout.face_track(edl, _index(), 1080, 1920, 0.0, 6.0,
                               measure=lambda t: [])
    assert all(faces == [] for _t, faces in track)


def test_on_without_a_sidecar_falls_back_and_launches_nothing(warm,
                                                              monkeypatch):
    _store, launcher = warm
    monkeypatch.setattr(config, "YUNET_KEEPOUT", True)
    calls = launcher.stats["calls"]
    other = dict(_index(), tile_keys=[f"tiles/7/{'0f' * 32}/tile_000.jpg"])
    assert keepout.yunet_faces(other, 1.0) is None
    assert keepout.yunet_faces(dict(_index(), tile_keys=[]), 1.0) is None
    assert keepout.yunet_faces(_index(), 40.0) is None     # outside source
    assert launcher.stats["calls"] == calls


def test_the_index_prefetch_and_the_keepout_read_share_keys(monkeypatch,
                                                            tmp_path):
    """The indexer keys the prefetch on the index's duration and shots;
    keep-out reads the same grid off the index, even when the proxy's own
    duration differs by a frame or two."""
    import schemas
    store, launcher = w0a_support.install(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr(config, "YUNET_KEEPOUT", True)
        clip = w0a_support.make_clip(str(tmp_path / "proxy.mp4"), dur=6.0)
        shots = [schemas.Shot(id=1, start=0.0, end=3.0),
                 schemas.Shot(id=2, start=3.0, end=6.0)]
        proxy_info = {"duration": 5.96, "fps": 25.0, "width": 320,
                      "height": 180}
        h = sidecars.prefetch_after_index(7, SHA, clip, proxy_info, shots,
                                          duration=6.0)
        assert h is not None and h.priority == "prefetch"
        assert set(sidecars.wait([h], 60).values()) == {"ready"}
        faces = keepout.yunet_faces(_index(), 4.5)
        assert faces and faces[0] == pytest.approx([0.25, 0.2, 0.75, 0.6],
                                                   abs=0.01)
    finally:
        w0a_support.uninstall(launcher)
