import numpy as np
import pytest

from score import music


def test_half_second_bed_valleys_recover_without_syllable_pumping(monkeypatch):
    monkeypatch.setattr(music, "SR", 8000)
    n = 8 * music.SR
    t = np.arange(n) / music.SR
    signal = np.sin(2*np.pi*240*t)
    voice = (.12*signal).astype("float32")
    bed = (.045*signal*np.where((t%2)>1, .035, 1.)).astype("float32")
    bed = np.column_stack([bed,bed])
    rep = music.seat_programme_bed(bed,voice,dict(words=[(0,8)]),dict(end_hit_s=8))
    assert rep["swing_before_db"] > 25
    assert rep["swing_after_db"] < 5


def test_the_seat_reads_levels_exactly_as_scipy_did_without_needing_it():
    """The seat's moving mean and gaussian smoothing are numpy (scipy is in no worker image nor in CI); where scipy is
    installed they equal its uniform_filter1d(mode="nearest") and gaussian_filter1d to rounding."""
    ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(7)
    for n in (10, 11, 167, 2000):
        x = np.abs(rng.standard_normal(n)) * 10 ** rng.uniform(-6, 1)
        for w in (1, 2, 3, 10):
            np.testing.assert_allclose(music._box_mean(x, w), ndimage.uniform_filter1d(x, w, mode="nearest"), rtol=1e-9, atol=1e-12 * x.max())
        for sigma in (1.2, 1.6):
            np.testing.assert_allclose(music._gauss_smooth(x, sigma), ndimage.gaussian_filter1d(x, sigma), rtol=1e-9, atol=1e-12 * x.max())
