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


def test_saved_empty_fx_receipt_requires_a_visible_landing_and_preserves_existing_fx(tmp_path):
    import os
    import subprocess
    from score import programme_accents as accents
    film = str(tmp_path/"scene.mp4")
    subprocess.run([os.environ.get("FFMPEG_BIN","ffmpeg"),"-v","error","-f","lavfi","-i",
                    "color=black:size=128x72:rate=30:duration=1","-vf",
                    "drawbox=x=0:y=0:w=90:h=50:color=white:t=fill:enable='gte(n,15)'",
                    "-c:v","libx264",film], check=True)
    render = {"audio":{"sounds":0}}
    record = dict(checks={"stories":{"render":render}},devices={"log":[dict(done=True,landed="figure",marks=[
        dict(kind="type",strength=.9,f=8), dict(kind="type",strength=.8,f=15)])]})
    events = accents.retained_events(record)
    assert len(events)==2
    assert accents.visible_event(film,events)["f"] == 15
    render["audio"]["sounds"] = 1
    assert accents.retained_events(record) == []
