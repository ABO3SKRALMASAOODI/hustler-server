"""The brand continuation preserves a healthy passage and loops a short sounding one."""
import numpy as np
from score import music


def test_existing_stereo_dynamics_and_short_loop(monkeypatch):
    sr = music.SR
    t = np.arange(7*sr)/sr
    signal = (.3 + .12*np.sin(2*np.pi*t)) * np.sin(2*np.pi*227*t)
    stereo = np.column_stack([signal,-signal]).astype(np.float32)
    monkeypatch.setattr(music,"_decode",lambda *args: stereo)
    np.testing.assert_array_equal(music.card_phrase("track",1.,1.,3*sr,2.),stereo[sr:4*sr])
    # A nearly silent plateau within a longer recording can have no local dip.
    mastered = stereo.copy()
    mastered[4*sr:] *= .002
    monkeypatch.setattr(music,"_decode",lambda *args: mastered)
    repaired = music.card_phrase("track",4.1,1.,2*sr,2.)
    assert np.mean(repaired**2) > .001
    short = stereo[:sr].copy()
    short[:sr//5] = short[-sr//5:] = 0
    monkeypatch.setattr(music,"_decode",lambda *args: short)
    loop = music.card_phrase("track",.9,1.,3*sr,2.)
    assert loop.shape == (3*sr,2)
    assert np.min(np.mean(loop.reshape(30,sr//10,2)**2,axis=(1,2))) > .001
