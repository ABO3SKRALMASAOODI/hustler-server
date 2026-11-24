import json
from types import SimpleNamespace

import wf1_coverage


def test_enacted_question_owns_its_words_and_preserves_neighboring_research(tmp_path):
    words = [dict(w=str(i),start=i,end=i+1) for i in range(22)]
    example = dict(first=1,last=18,speaker="Host",question=[3,4],inference=[6,7],memory=[10,11],
                   return_at=14,accent=15,release=17,inference_label="Their intention",memory_label="Later recollections")
    windows = [dict(first=i,last=i,name=str(i),must_show=str(i),external=True) for i in [0,3,8,20]]
    answer = dict(record_example=example, windows=windows, callback=dict(first=4,last=7,question="Another use?"))
    model = SimpleNamespace(ask=lambda *a, **kw:(json.dumps(answer),{}),json_block=json.loads)
    plan = wf1_coverage.plan(words,"A remembered call",tmp_path,model,story=dict(callback=dict(transcript=["earlier demo"])))
    assert [r["first"] for r in plan["windows"]] == [0,20]
    assert not plan.get("callback")
    assert plan["record_example"] == example


def test_record_reuses_actual_interview_words_and_locator_through_the_return():
    import wf1_record
    words = [dict(w=w,start=i/2) for i,w in enumerate("intro How was it? wonder memory qualify here turn back close".split())]
    example = dict(first=0,last=9,speaker="Host",question=[1,3],inference=[4,4],memory=[5,6],
                   return_at=7,accent=8,release=9,inference_label="Private intent",memory_label="A recollection")
    source = dict(url="episode",publisher="Interview")
    pages = wf1_record.pages([dict(first=0,last=10,facts=[dict(value="Old callback")])],
                             wf1_record.reading(example,words),words,source,120)
    assert [(p.get("record_role"),p["first"],p["last"]) for p in pages] == [
        ("wording",1,3),("inference",4,4),("memory",5,6),("return",7,8),(None,10,10)]
    assert pages[0]["facts"] == pages[3]["facts"] == [
        dict(source=source,attribution="Host",value="How was it?",time_s=120.5,word_ids=[1,3])]


def test_qualification_uses_the_directors_adjacent_contrast_and_preserves_other_sources():
    import wf1_record
    words = [dict(w=w,start=i,end=i+1) for i,w in enumerate(
        "setup Why now? intent They meant well. Memory fades. Listen again. Close.".split())]
    record = dict(first=0,last=11,speaker="Guest",question=[1,2],inference=[3,3],memory=[7,8],
                  inference_label="Intent",memory_label="Remembering",return_at=9,accent=10,release=11)
    pages = wf1_record.pages([],record,words,dict(url="episode"),100,[dict(w=6,role="contrast")],[2,3,6,8])
    qualification = next(p for p in pages if p["record_role"]=="qualification")
    assert qualification["facts"][0]["value"] == "They meant well."
    assert qualification["facts"][0]["word_ids"] == [4,6]
    claims = [dict(start=101,end=102),dict(start=120,end=124)]
    scoped = wf1_record.source_claims(claims,dict(record_example=record,words=words,story=dict(episode_start=100)))
    assert scoped == [dict(claims[0],interview_only=True),claims[1]]
