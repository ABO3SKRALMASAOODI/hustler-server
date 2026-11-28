from unittest.mock import Mock
from renderer import _compose_join


def test_only_same_shot_dissolve_resolves_to_cut():
    order = [("seg", 0, 4), ("seg", 1, 2)]
    idx = {"shots": [{"start": 0, "end": 10}, {"start": 10, "end": 13}]}
    dissolve = {"id": "d", "style": "dissolve"}
    wipe = {"id": "w", "style": "slide"}
    def join(at, items, index=idx):
        return _compose_join(dict(k=0, items=items), order, [(0, 4), (at, at+2)],
                             index, 30, Mock())
    assert join(5, [dissolve]) is None
    assert join(5, [dissolve, wipe])["items"] == [wipe]
    assert join(10, [dissolve])["items"] == [dissolve]
    assert join(5, [dissolve], {})["items"] == [dissolve]
    assert join(5, [dict(dissolve, hold=True)]) is not None


def test_handle_hold_does_not_make_matching_source_pictures_dissolve():
    import numpy as np
    background = np.random.default_rng(4).integers(0, 255, (64, 96, 3), dtype=np.uint8)
    incoming = background.copy()
    incoming[20:45, 40:55] = 128
    cap = Mock()
    cap.read.side_effect = [(True, background), (True, incoming)]
    join = dict(k=0, items=[dict(style="dissolve", hold=True)])
    order = [("seg", 0, 3), ("seg", 1, 4)]
    assert _compose_join(join, order, [(0,3),(5,9)], {}, 30, Mock(), cap) is None
    cap.read.side_effect = [(True, background), (True, 255-background)]
    assert _compose_join(join, order, [(0,3),(5,9)], {}, 30, Mock(), cap) == join


def test_background_registration_recognizes_small_camera_push():
    import cv2
    import numpy as np
    from renderer import _same_join_picture
    src = np.random.default_rng(8).integers(0, 255, (192, 288, 3), dtype=np.uint8)
    transform = cv2.getRotationMatrix2D((144,96), 0, 1.12)
    incoming = cv2.warpAffine(src, transform, (288,192))
    cap = Mock()
    cap.read.side_effect = [(True, src), (True, incoming)]
    assert _same_join_picture(cap, 4.7, 5.3) is True


def test_range_read_source_uses_bounded_frame_reads(monkeypatch):
    import cv2
    from renderer import _join_capture, _same_join_picture
    cap = Mock()
    open_video = Mock(return_value=cap)
    monkeypatch.setattr(cv2, "VideoCapture", open_video)
    url = "https://example.test/clip.mp4"
    assert _join_capture(url) is cap
    open_video.assert_called_once_with(url, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000])
    cap.set.return_value = False
    assert _same_join_picture(cap, 2., 7.) is None
    cap.read.assert_not_called()
