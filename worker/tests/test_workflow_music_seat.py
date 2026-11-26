import numpy as np

from score import music
from score import sound


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


def test_composed_title_gets_one_bound_tick_and_empty_picture_remains_quiet():
    scene = dict(items=[],dur_f=400,fps=30,story_cues=[],composed_events=[])
    assert sound.composed_accents(scene) == ([],[])
    event = dict(id="reveal",dev="counter",kind="type",f=42,strength=.9,items=["figure"])
    scene["composed_events"] = [event]
    cues, partners = sound.composed_accents(scene)
    assert [(c["sound"],c["f"]) for c in cues] == [("tick",42)]
    assert partners[0]["items"] == ["figure"]
