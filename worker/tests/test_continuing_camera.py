"""Render geometry from the two contiguous original m38 frames at the closing layout switch."""
import os
from pathlib import Path

import pytest

from scene.continuity import closing_crops
from scene.motion import marked_figure


def test_first_party_result_marks_the_source_row_on_its_phone_crop():
    seat, mark = marked_figure([.012,.049,.241,.505], [.123,.423,.203,.499], (1840,512), (1080,1920))
    assert seat[2] - seat[0] > .77
    assert .47 < mark[0] < mark[2] < seat[2]
    assert seat[1] < mark[1] <= seat[3]
    assert (mark[2]-mark[0])/(seat[2]-seat[0]) == pytest.approx(.080/.229)


def test_m38_layout_cut_preserves_the_moving_face(tmp_path):
    import cv2
    import numpy as np

    episode = os.environ.get("VALMERA_TEST_M38_SOURCE")
    if not episode:
        pytest.skip("Set VALMERA_TEST_M38_SOURCE to the original 3148.80–3170.92 take, normalized to 30 fps")
    views = [dict(first=79,shot=[15.8667,21.6],box=[.035,.045,.635,.94]),
             dict(first=79,shot=[21.6,22.12],box=[.02,.163,.48,.849])]
    cap = cv2.VideoCapture(str(Path(episode)))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES,647)
        frames = [cap.read()[1] for _ in views]
    finally:
        cap.release()
    h,w = frames[0].shape[:2]
    crops = closing_crops(episode,views,(w,h),30)
    phone = []
    for frame, crop in zip(frames,crops):
        l,t,r,b = np.rint(np.array(crop)*[w,h,w,h]).astype(int)
        phone.append(cv2.resize(frame[t:b,l:r],(216,216)))
    # Both eyes, the moving hand and the shelf remain in the same screen coordinates.
    assert cv2.absdiff(*phone).mean() < 12
    assert crops[1][2] < .5


def test_review_row_keeps_the_real_filename_and_neighboring_rows_at_reading_size():
    from scene.motion import readable_row_crop
    pane = [0,.502,1,1]
    row = [.010,.633,.343,.651]
    crop = readable_row_crop(pane,row,(2000,1720),(1080,1920))
    seat, underline = marked_figure(crop,row,(2000,1720),(1080,1920))
    assert pane[1] <= crop[1] < row[1] < row[3] < crop[3] <= pane[3]
    assert crop[0] <= row[0] < row[2] <= crop[2]
    assert (row[3]-row[1])/(crop[3]-crop[1])*(seat[3]-seat[1])*1920 >= 32
    assert underline[1] == pytest.approx(seat[1]+(row[3]-crop[1])/(crop[3]-crop[1])*(seat[3]-seat[1]))
