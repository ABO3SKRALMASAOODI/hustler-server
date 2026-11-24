import json
from types import SimpleNamespace

import wf1_alignment
import wf1_context
import wf1_coverage
import wf1_display
import wf1_film
from test_wf1_take import quiet_engines  # noqa: F401
from test_wf1_display_facts import material


def test_local_example_keeps_the_documented_neighbor(tmp_path):
    words = [dict(w=t, start=i, end=i+.9) for i,t in enumerate(
        "this episode insurance or medicine the published study found".split())]
    windows = [dict(first=a,last=b,name=n,external=True,must_show=n) for a,b,n in
               [(2,2,"Insurance"),(4,4,"Medicine"),(5,8,"Study")]]
    row = dict(first=0,last=4,graphic_first=0,graphic_last=1,label="Where listeners stop")
    model = SimpleNamespace(ask=lambda *args,**kw:(json.dumps(dict(windows=windows,episode_examples=[row])),{}),
                            json_block=json.loads)
    plan = wf1_coverage.plan(words,"Signals",tmp_path,model)
    assert [(w["name"],w["say"]) for w in plan["windows"]] == [("Study","the published study found")]
    assert plan["episode_examples"] == [row]


def test_interview_gloss_alone_needs_no_external_research(tmp_path, monkeypatch):
    claim = dict(stance="definition",speaker="Guest",subject="CRM")
    def external(*args):
        raise AssertionError("Fetched evidence for an interview gloss")
    monkeypatch.setattr(wf1_context,"research",external)
    result = wf1_context.direct(dict(claims=[claim], windows=[]),tmp_path,None)
    assert result["spend"] == []


def test_example_and_optional_definition_share_the_running_captions(tmp_path, quiet_engines):
    ctx,script,pack = material(tmp_path)
    words = pack["words"]["words"]
    example = dict(first=0,last=10,graphic_first=1,graphic_last=8,label="Where listeners stop")
    pack["coverage"]["episode_examples"] = [example]
    a,b = 20,27
    script["claims"] = [dict(start=words[a]["start"],end=words[b]["end"],stance="definition",
                             subject="CRM",speaker="Guest",report="Customer records")]
    edit = wf1_display.editorial(ctx,script,pack)
    clock = [dict(w=w["w"],seg=w["seg"],t0=w["start"]+.1,t1=w["end"]+.1) for w in words]
    direction = wf1_display.bind(dict(asks=[],arc=[],caption_ends=[8,19,b,len(words)-1]), edit, clock)
    scene,beats = wf1_film.build(script,pack,"prints",direction)
    assert any(p.get("episode_example") and p["facts"][0]["source"]["url"] == ctx.args["source_url"]
               for p in beats["display_facts"])
    assert next(p for p in beats["display_facts"] if p.get("episode_example"))["last"] == 10
    assert any(i.get("t")=="e.g." for i in scene["items"])
    page = next(p for p in beats["display_facts"] if p["facts"][0].get("chain"))
    caps = " ".join(i["t"] for i in scene["items"] if i.get("role")=="cap"
                    and i["f"][0]<page["f"][1] and page["f"][0]<i["f"][1]).lower()
    assert all(w["w"].strip(".,").lower() in caps for w in words[a:b+1])


def test_complete_reply_uses_the_timed_turn_after_the_guest():
    index=dict(words=[dict(w=w,t0=a,t1=b) for w,a,b in
                      [("management.",0,1),("in",1.3,1.4),("fact,",1.4,1.7),("yes.",1.7,2)]],
               sentences=[dict(wi0=0,wi1=0),dict(wi0=1,wi1=3)])
    source=[(11.2,">> Yeah,"),(11.3,"in"),(11.4,"fact,"),(11.7,"yes.")]
    got=wf1_alignment.caption_words(index,source,10,"Yeah, in fact, yes.")
    assert [w["w"] for w in got] == ["management.","Yeah,","in","fact,","yes."]
    assert got[1] == dict(w="Yeah,",seg=1,start=1.2,end=1.3)


def test_context_gloss_releases_with_its_own_clause():
    words = [dict(seg=s) for s in (0,0,0,1,1,2)]
    fact = dict(chain=dict(stance="definition",start=308.69,subject="CRM"))
    source = dict(contextual_definitions=[dict(start=308.69,gloss="Customer relationship management")])
    page, = wf1_display.episode_pages([dict(first=2,last=5,facts=[fact])],source,words)
    assert (page["first"],page["last"],page["gloss"]) == (0,2,True)


def test_late_text_accent_returns_to_the_spoken_word_and_keeps_pictured_action():
    words = [dict(w=w,seg=i//4) for i,w in enumerate("data that comes out data from the study".split())]
    local = dict(first=0,last=3,graphic_first=0,graphic_last=1,label="Possible signals")
    edit = dict(episode_examples=[local],sources={"interview": {}},picture_windows=[dict(first=4,last=7)])
    asks = [dict(dev="diecut",at=dict(w=i),args=dict(t="data")) for i in (3,6)]
    bound = wf1_display.bind(dict(caption_ends=[3,7],asks=asks,arc=[]),edit,words)
    assert bound["caption_accents"] == [0]
    assert bound["asks"] == [asks[1]]
