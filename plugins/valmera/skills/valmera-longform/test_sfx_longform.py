"""Long-form (wf4) never places or mixes a flagged library recording (owner, 15 Nov 2026; worker/sfx_filter.py): its
top sound was impact_1 (sub only on a phone); it now plays impact_9, and the class-L thinning still reads the name
the film asked for."""
import json
import os
import sys
import wave
from types import SimpleNamespace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "valmera-stories"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell", "narrate"))
import film_long as FL  # noqa: E402
import pictures as PX  # noqa: E402


def test_the_turns_impact_plays_its_clean_replacement_and_keeps_its_priority():
    SF = PX.sfx_filter()
    lib = PX.sound_facts(FL.SOUNDS)
    P = FL.Plan({}, lib)
    P.hit("whoosh_soft_2", 30, -20.0)
    P.hit("impact_1", 300, -15.0)
    P.hit("tick_1", 600, -24.0)
    assert [s["s"] for s in P.sounds] == ["whoosh_soft_2", "impact_9", "tick_1"]
    kept = FL.thin_sounds(P.sounds, 900, lib, rate=0.4)                  # one sound allowed: the turn's impact first
    assert [s["s"] for s in kept] == ["impact_9"] and kept[0]["asked"] == "impact_1"
    assert not {s["s"] for s in FL.thin_sounds(P.sounds, 900, lib)} & SF.flagged()


def test_the_mixer_never_plays_a_flagged_file(tmp_path, monkeypatch):
    """A plan written before the filter (a resumed run's l1) still names impact_1: the mixer plays impact_9."""
    import importlib
    A = importlib.import_module("scene.audio")
    read = []
    real = A.pcm
    monkeypatch.setattr(A, "pcm", lambda p, seconds=None: (read.append(os.path.basename(p)), real(p, seconds))[1])
    monkeypatch.setattr(A, "master", lambda voice, fx, out, tmp, dur: {"ok": True})
    nar = tmp_path / "nar.wav"
    with wave.open(str(nar), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(np.zeros(48000 * 3, "<i2").tobytes())
    l1 = {"dur_f": 120, "sound": [dict(id="s1", s="impact_1", file="impact_1.flac", event_f=60, start_s=1.245, trim_s=0.0, db=-10.0, peak_s=0.755)]}
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path))
    FL.audio_master(ctx, l1, str(nar))
    assert "impact_9.flac" in read and "impact_1.flac" not in read, read
