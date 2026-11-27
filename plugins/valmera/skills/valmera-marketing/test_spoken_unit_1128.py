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
    proof.product_clips(sc,str(tmp_path))
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
