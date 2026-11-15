from pathlib import Path
import numpy as np
import chapter_audio


def test_final_master_is_sum_of_the_retained_post_duck_stems(tmp_path,monkeypatch):
    import cinema as C
    C.worker()
    from scene import audio as AU
    from score import sound as SD,music as MU
    import stem_evidence
    n=48000
    voice=np.full((n,2),.04,np.float32);bed=np.full((n,2),.01,np.float32)
    fx=np.zeros((n,2),np.float32).view(AU.Sounds)
    fx[24000:26000]=.1;fx.ducks=[(24000,9)];fx.own=fx.copy()
    sounds=[dict(id='recording',start_s=.5,db=-10)]
    monkeypatch.setattr(SD,'present_over',lambda sounds,v,b,fps:[dict(s,db=-8) for s in sounds])
    monkeypatch.setattr(MU,'to_ar',lambda x:x.mean(axis=1)[::3])
    monkeypatch.setattr(AU,'split_audio',lambda plan:(np.zeros_like(fx),fx,dict(sounds=1)))
    saved={};mastered=[]
    monkeypatch.setattr(stem_evidence,'write_audio',lambda p,x,*args:saved.__setitem__(str(p),x.copy()))
    def master(v,x,*args):
        assert v is None  # do not level or decode the verified voice again
        mastered.append(x.copy());return dict(lufs=-14)
    monkeypatch.setattr(AU,'master',master)
    monkeypatch.setattr(AU,'mux',lambda film,audio,out:Path(out).touch())
    result=chapter_audio.mix(dict(sound=sounds,fps=30,dur_f=30),voice,bed,'picture.mp4',str(tmp_path/'film.mp4'))
    v=saved[str(tmp_path/'audio-stems/voice.flac')];s=saved[str(tmp_path/'audio-stems/sfx.flac')];b=saved[str(tmp_path/'film.bed.wav')]
    assert np.array_equal(v,voice) and np.array_equal(s,fx)
    assert np.allclose(mastered[0],v+s+b)
    assert b.min()<bed.min() and np.all(b<=bed)
    assert result['final_sounds'][0]['db']==-8 and len(result['final_sounds'])==1

