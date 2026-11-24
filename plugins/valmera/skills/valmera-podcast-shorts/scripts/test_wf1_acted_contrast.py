from wf1_source_frame import bind


def test_source_read_keeps_the_switch_and_the_same_pending_item():
    words = [dict(w=str(i), start=i/4, end=(i+1)/4) for i in range(87)]
    example = dict(first=0, pivot=6, switch=11, item_at=14, gesture=50, after=65,
                   return_at=70, last=86, before="Trace the work", alternative="Assign blame",
                   item="Data item", state="Pending", outcome="Still pending",
                   subject=[0,.03,1,1], action=[.025,.06,.94,1])
    windows = [dict(first=55,last=59),dict(first=60,last=71),dict(first=72,last=84)]
    result = bind(dict(words=words, windows=windows), [], dict(acted_contrast=example,
                  caption_restarts=[dict(fragment=13, question=14, end=17)]))
    assert result.get("acted_contrast") == example
    assert result["replaces"] == [0,2]
    assert result["caption_restarts"] == [dict(fragment=13, question=14, end=17)]


def test_restart_uses_the_silence_and_keeps_the_original_spoken_clock(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    import numpy as np
    from wf1_acted_contrast import caption_clock

    words = [dict(w=w,t0=a,t1=b) for w,a,b in
             (("game",3.22,3.54),("who's",3.54,4.38),("why",4.38,4.86),
              ("isn't",4.86,5.28),("this",5.28,5.42),("done",5.42,5.64))]
    clock = SimpleNamespace(words=words)
    def waveform(*args, **kwargs):
        start = float(args[0][args[0].index("-ss")+1])
        voiced = np.ones(800,dtype="<f4")*.1
        quiet = np.zeros(4000 if start < 4 else 640,dtype="<f4")
        return SimpleNamespace(stdout=np.concatenate([voiced,quiet,voiced]).tobytes())
    monkeypatch.setattr(subprocess,"run",waveform)
    captions, gaps = caption_clock(clock,[dict(fragment=1,question=2,end=5)],"source.mp4","ffmpeg",.1)
    assert [w["w"] for w in captions.words] == ["game","Who's—","Why","isn't","this","done?"]
    assert words[1] == dict(w="who's",t0=3.54,t1=4.38)
    assert captions.words[1]["t0"] < gaps[0][1] < captions.words[2]["t0"]
    assert captions.words[0] is words[0] and captions.words[4] is words[4]
