import numpy as np
import pytest
from first_cut_floor import prefer_floor
from film_measure import numeric


def reading(cls='light_touch', shots=0, sounds=0, motion=0, steps=0):
    return {'class': cls, 'program_s': 10, 'shots_added': shots,
            'camera_moves_added': motion,
            'taste': {'sound_times': list(range(sounds)), 'off_edit_steps': steps}}


def test_before_style_wins_over_thin_fallback():
    assert prefer_floor(reading(), reading('edited', shots=2, sounds=3, motion=2))
    assert prefer_floor(reading(), reading(sounds=3, motion=1))


def test_unknown_evidence_and_extra_punches_never_win():
    assert not prefer_floor(reading(), reading('unknown', shots=30))
    assert not prefer_floor(reading(), reading('edited', steps=2))
    assert not prefer_floor(reading('edited', shots=3), reading('edited', sounds=20, motion=15))


@pytest.mark.parametrize('size', [2, 3, 5, 18, 101])
@pytest.mark.parametrize('mode', ['nearest', 'reflect'])
def test_signal_filters_match_fixed_meter(size, mode):
    from scipy import ndimage
    a = np.random.default_rng(48).normal(size=170)
    np.testing.assert_allclose(numeric.median_filter(a, size, mode), ndimage.median_filter(a, size, mode=mode))
    np.testing.assert_allclose(numeric.uniform_filter1d(a, size, mode), ndimage.uniform_filter1d(a, size, mode=mode), atol=1e-12)
    np.testing.assert_allclose(numeric.maximum_filter1d(a, size, mode), ndimage.maximum_filter1d(a, size, mode=mode))


def test_connected_pixels_do_not_join_diagonally():
    from scipy import ndimage
    a = np.eye(6, dtype=np.uint8)
    actual, n = numeric.label(a)
    expected, m = ndimage.label(a)
    assert n == m == 6
    np.testing.assert_array_equal(actual, expected)
