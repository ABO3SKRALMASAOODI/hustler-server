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


def test_opaque_body_core_repairs_dark_clothes_without_hardening_motion_edges():
    from types import SimpleNamespace
    mask = np.zeros((200, 160), np.uint8)
    mask[20:180, 30:130] = 255
    mask[100:180, 65:95] = 0  # genuine opening between legs
    alpha = mask.astype(np.float32) / 255
    alpha[50:90, 50:110] = 0  # dark shirt mistaken for backdrop by RVM
    alpha[30:70, 28:32] = .35  # blurred edge must keep its soft alpha
    ref = object.__new__(matte_refine.Refiner)
    ref.previous = ref.mask = ref.core = None
    ref.n = ref.accepted = ref.rejected = 0
    ref.seg = SimpleNamespace(segment=lambda *a, **kw: (mask, .95))
    out = ref.step(np.zeros((200, 160, 3), np.uint8), alpha, cv2)
    assert out[70, 80] == 1
    assert out[150, 80] == 0
    assert 0 < out[50, 29] < .4


def test_background_refinement_changes_sidecar_key_only_for_composites():
    old = matte_track.sidecar_params({'fx': []}) or {}
    new = matte_track.sidecar_params({'fx': [{'type': 'background'}]})
    assert 'refine' not in old
    assert new['refine'] == matte_refine.VERSION
    assert len(new['support_model']) == 64


def test_support_moves_with_the_subject_instead_of_clipping_the_new_position():
    """A carried mask follows a moving limb; the previous silhouette cannot cut it."""
    from types import SimpleNamespace
    h, w = 80, 100
    old = np.zeros((h, w), np.float32)
    old[20:60, 20:40] = 1
    alpha = np.zeros((h, w), np.float32)
    alpha[20:60, 30:50] = 1
    flow = np.zeros((h, w, 2), np.float32)
    flow[..., 0] = -10  # current pixel -> previous location
    ref = object.__new__(matte_refine.Refiner)
    ref.previous = np.zeros((h, w), np.uint8)
    ref.mask, ref.n = old, 1
    ref.flow = SimpleNamespace(calc=lambda *args: flow.copy())
    out = ref.step(np.zeros((h, w, 3), np.uint8), alpha, cv2)
    assert np.array_equal(out, alpha)
    assert out[:, :30].sum() == 0


def test_uncertain_new_segmentation_cannot_erase_a_newly_entering_person():
    from types import SimpleNamespace
    h, w = 80, 100
    ref = object.__new__(matte_refine.Refiner)
    ref.previous = ref.mask = None
    ref.n = ref.accepted = ref.rejected = 0
    ref.seg = SimpleNamespace(segment=lambda *a, **kw: (np.zeros((h, w), np.uint8), .9))
    alpha = np.zeros((h, w), np.float32)
    alpha[10:70, 20:60] = .8
    out = ref.step(np.zeros((h, w, 3), np.uint8), alpha, cv2)
    assert np.array_equal(out, alpha)
    assert ref.rejected == 1
