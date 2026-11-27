import numpy as np


def test_649s_accent_has_one_layer_and_measured_speech_room():
    from chapter_audio import editorial_cues,speech_room
    hit=dict(layer='hit',event_f=19475,start_s=0.,dur_s=.4,db=-3)
    cues=editorial_cues([dict(hit,layer='lead'),hit],[19475],30)
    assert cues==[hit]
    voice=np.full((400,2),.05)
    fx=np.full_like(voice,.2)
    speech_room(cues,voice,fx,1000)
    assert np.max(fx)<=.05*10**(-7/20)+1e-9
    assert hit['over_voice_db']==-7


def test_653s_end_of_last_chapter_continues_on_licensed_recording():
    from music_chapters import bridge_rests
    bed=np.full((550,2),.04)
    bed[-150:]=0
    rec=dict(path='recording',tempo=1,at_s=5,bar_s=2)
    def phrase(path,at,tempo,frames,bar):
        assert path=='recording'
        return np.full((frames,2),.08)
    assert bridge_rests(bed,rec,dict(segments=[]),1000,phrase)==[[.4,.55]]
    assert np.mean(bed[-50:]**2)>10**(-58/10)
