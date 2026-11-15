import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from measure_mix import listen_mix
import narrate as N


def test_music_cannot_force_asr_to_cut_through_speech(monkeypatch):
    sr=1000
    voice=np.ones(60000,dtype=np.float32)
    voice[23200:23600]=0;voice[46600:47000]=0
    music=np.full_like(voice,.1)
    mixed=voice+music
    heard=[]
    monkeypatch.setattr(N,'listen',lambda x,rate:heard.append(x.copy()) or ('',[]))
    _,_,windows=listen_mix(mixed,voice,sr)
    assert windows==[[0,23.4],[23.4,46.8],[46.8,60]]
    assert np.array_equal(np.concatenate(heard),mixed)
    assert heard[0][23300]==pytest.approx(.1), 'the bed stays audible in the pause'
    # The old mix-based silence detector has no pauses and cuts voiced samples.
    old=[]
    N.listen_long(mixed,sr,fn=lambda x,rate:old.append(len(x)/rate) or ('',[]))
    assert old==[25,25,10] and voice[25000] and voice[50000]


def test_boundary_stem_must_match_the_entire_mix_clock():
    with pytest.raises(ValueError,match='same clock'):
        listen_mix(np.ones(30000),np.ones(29000),1000)
