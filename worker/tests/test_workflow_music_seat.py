import numpy as np

from score import music


def test_half_second_bed_valleys_recover_without_syllable_pumping(monkeypatch):
    monkeypatch.setattr(music, "SR", 8000)
    n = 8 * music.SR
    t = np.arange(n) / music.SR
    signal = np.sin(2*np.pi*240*t)
    voice = (.12*signal).astype("float32")
    bed = (.045*signal*np.where((t%2)>1, .21, 1.)).astype("float32")
    bed = np.column_stack([bed,bed])
    rep = music.seat_programme_bed(bed,voice,dict(words=[(0,8)]),dict(end_hit_s=8))
    assert rep["swing_before_db"] > 12
    assert rep["swing_after_db"] < 5
