import numpy as np
from first_film_sound import power200, bed_space


def test_tiny_spectral_peak_is_not_a_full_audible_event():
    rate = 16000
    t = np.arange(rate) / rate
    long = .1 * np.sin(2*np.pi*1000*t)
    tiny = long.copy(); tiny[int(.02*rate):] = 0
    assert power200(tiny, rate) < power200(long, rate) * .12
    sub = .4 * np.sin(2*np.pi*60*t)
    assert power200(sub, rate) < power200(long, rate) * .01


def test_music_dip_is_on_the_hit_and_returns_smoothly():
    song = np.ones((2000, 2), np.float32)
    out = bed_space(song, [{'event_f': 30, 'layer': 'hit'}], 30, 1000)
    np.testing.assert_allclose(song, 1)  # no mutation of cached arrangement
    assert out[1000, 0] == .5
    assert out[800, 0] == out[1500, 0] == 1
    assert np.max(np.abs(np.diff(out[:, 0]))) < .01
