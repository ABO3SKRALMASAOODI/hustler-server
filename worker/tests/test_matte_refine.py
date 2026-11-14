import cv2
import numpy as np
import matte_refine
import matte_track


def test_small_face_and_shirt_holes_filled_without_filling_leg_gap():
    mask = np.zeros((200, 160), np.uint8)
    mask[20:180, 30:130] = 255
    mask[40:43, 70:74] = 0
    mask[100:180, 65:95] = 0
    support = matte_refine.support(mask, cv2)
    assert support[41, 71] == 1
    assert support[150, 80] == 0
    assert support[50, 145] == 0


def test_background_refinement_changes_sidecar_key_only_for_composites():
    old = matte_track.sidecar_params({'fx': []}) or {}
    new = matte_track.sidecar_params({'fx': [{'type': 'background'}]})
    assert 'refine' not in old
    assert new['refine'] == matte_refine.VERSION
    assert len(new['support_model']) == 64
