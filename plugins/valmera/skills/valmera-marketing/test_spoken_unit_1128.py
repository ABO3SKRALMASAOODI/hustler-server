from pathlib import Path
from types import SimpleNamespace
import json
import sys

import proof


def test_spoken_unit_ignores_a_long_pause_and_cannot_snap_across_its_neighbors():
    words = [dict(t0=1,t1=2,w="Earlier."),dict(t0=3,t1=4,w="The"),
             dict(t0=6,t1=7,w="claim"),dict(t0=7,t1=8,w="lands."),
             dict(t0=9,t1=10,w="Next.")]
    rows = proof.numbered_sentences(words)
    assert [(r["i"],r["text"]) for r in rows] == [(0,"Earlier."),(1,"The claim lands."),(2,"Next.")]
    gaps = lambda *args, **kwargs: [(1,1.5),(2.2,2.8),(4.5,6),(8.2,8.8),(9.1,9.8)]
    assert proof.snap_window(rows,[1,1],"x",10,clock=words,silence=gaps) == [2.5,8.5]


def test_recorded_clips_use_the_separate_upload_and_export_clocks(tmp_path, monkeypatch):
    rec = {"upload":{"file":"up","sha256":"up-sha","dur":56}, "exports":[
        {"file":"ex","sha256":"ex-sha","dur":38,"tag":"final"}]}
    record = tmp_path / "record.json"
    record.write_text(json.dumps(rec))
    cuts = {}
    def cut(src, start, end, out, audio=True):
        cuts[Path(out).name] = (src,start,end)
    monkeypatch.setattr(proof, "cut", cut)
    monkeypatch.setattr(proof, "export_edl", lambda ex: {})
    monkeypatch.setattr(proof, "product_crop", lambda *a: None)
    spans = dict(split=[2,8],before=[13,25],after=[9,20],stretch=[5,30])
    sc = dict(record=str(record),export_check={},hook={"t":spans["split"]},script={"proof":{"spans":spans}})
    clips = proof.product_clips(sc,str(tmp_path))
    assert clips["stretch"] == {"src":"ex","t":[5,30],"sha256":"ex-sha"}
    assert cuts["before.mp4"] == ("up",13,25)
    assert cuts["after.mp4"] == ("ex",9,20)
    assert cuts["hook.mp4"] == ("ex",2,8)


def test_recoverable_tutorial_error_resumes_the_same_record_and_format(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(proof.HERE).parent / "_shell"))
    import batch_films as batch
    root, work = tmp_path/"root", tmp_path/"batch"
    root.mkdir()
    rid = batch.film_id(work,0)
    folder = work/"work"/rid
    folder.mkdir(parents=True)
    state = {"run_id":rid,"done":["ideas","script"],"args":{"format":"tutorial"},"pid":None}
    (folder/"run.json").write_text(json.dumps(state))
    script = {"record":"record-a","script":{"format":"tutorial"}}
    (folder/"script.json").write_text(json.dumps(script))
    visits = []

    def resume(options):
        visits.append((options.work_root,json.loads((folder/"script.json").read_text())))
        if len(visits) == 1:
            (folder/"run.json").write_text(json.dumps(dict(state,stopped={"stage":"supply","error":"voice join"})))
            return 2
        out = Path(options.out)/"complete"
        out.mkdir()
        (out/"film.json").write_text("{}")
        return 0

    monkeypatch.setattr(batch.shell,"run",resume)
    batch.edit_film("wf2",root,work,0,{"title":"Pain","style":"window"},False)
    assert visits == [(str(folder.parent),script)]*2

def test_podcast_fragments_keep_both_word_edges_when_the_thought_crosses_a_pause(tmp_path):
    media = tmp_path/"source.mp4"
    rows = [{"text":"They wanted", "start":0.4, "end":1.0},
            {"text":"the same thing.", "start":3.1, "end":4.2},
            {"text":"So they agreed.", "start":5.2, "end":6.1}]
    (tmp_path/"valmera-segments.json").write_text(json.dumps(rows))
    sentences, edges = proof.source_sentences(None, media, "upload", 0.8, 5.8)
    assert sentences == [{"i":0, "text":"They wanted the same thing.", "t0":0.4, "t1":4.2},
                         {"i":1, "text":"So they agreed.", "t0":5.2, "t1":6.1}]
    assert edges[2]["t0"] == 5.2


def test_no_breath_threshold_uses_measured_sound_inside_the_interword_gap(tmp_path):
    import wave
    import numpy as np
    path = tmp_path / "noise.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1,2,16000,0,"NONE",""))
        audio = np.full(16000,2000,dtype=np.int16)
        audio[6350:6470] = 0
        wav.writeframes(audio.tobytes())
    point = proof.quiet_gap(str(path), .38, .43)
    assert .396 <= point <= .407


def test_click_blend_preserves_both_words_and_the_cut_clock():
    import numpy as np
    rate = 48000
    audio = np.full((rate,1), .012, dtype="float32")
    audio[rate//2:] = -.014
    original = audio.copy()
    changes = proof.blend_pauses(audio,rate,[dict(start=.1,end=.42),dict(start=.58,end=.9)],
                                 [dict(t=.5,artefacts=["click"])])
    assert len(changes)==1 and np.array_equal(audio[:int(.42*rate)],original[:int(.42*rate)])
    assert np.array_equal(audio[int(.58*rate):],original[int(.58*rate):])
    assert np.max(abs(np.diff(audio[:,0]))) < .001
