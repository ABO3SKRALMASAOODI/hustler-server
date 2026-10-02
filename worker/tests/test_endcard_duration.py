"""The five-second end card must remain readable past the old two-second cut."""
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageChops

from tools.build_endcard import DURATION_S, frame_at


def test_endcard_holds_before_its_final_fade():
    assert DURATION_S == 5.0
    revealed = frame_at(1.0)
    assert revealed.getbbox() is not None
    assert ImageChops.difference(revealed, frame_at(4.7)).getbbox() is None
    assert frame_at(5.0).getbbox() is None


def test_shipped_animation_is_five_seconds_and_still_readable_after_four():
    path = Path(__file__).resolve().parents[1] / "brand" / "endcard.mp4"
    video = cv2.VideoCapture(str(path))
    try:
        assert video.isOpened()
        fps = video.get(cv2.CAP_PROP_FPS)
        assert abs(video.get(cv2.CAP_PROP_FRAME_COUNT) / fps - 5.0) < 1 / fps
        frames = []
        for seconds in (1.0, 4.5):
            video.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
            ok, frame = video.read()
            assert ok
            frames.append(frame)
        assert np.count_nonzero(frames[1] > 180) > 20000
        assert np.mean(cv2.absdiff(*frames)) < 1
    finally:
        video.release()
