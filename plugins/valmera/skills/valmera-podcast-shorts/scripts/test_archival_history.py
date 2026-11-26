import json
from types import SimpleNamespace

import wf1_alignment as alignment
import wf1_coverage as coverage
import wf1_display as display
from test_wf1_take import quiet_engines  # noqa: F401


def test_qualified_hook_and_sourced_sequence():
    words = [dict(w=str(i), t0=i+.1, t1=i+.2, seg=0) for i in range(16)]
    sources = {"interview": {"url": "episode", "publisher": "Podcast"},
               "founding": {"url": "founding", "publisher": "Company archive"}}
    editorial = dict(sources=sources, claims=[], speaker_claims=[
        dict(first=0,last=5,speaker="Founder",subject="Founder",report="The lab may have been the largest.",
             stance="personal_estimate")])
    seq = [dict(at=6,label="Sought users",source="interview"),
           dict(at=10,label="Company founded in 2013",source="founding")]
    cues = [dict(dev="facts",at={"w":1},until={"w":2},args=dict(facts=[dict(source="interview")])),
            dict(dev="staircase",at={"w":6},until={"w":15},
                 args=dict(speaker="Founder",sequence_kind="history",sequence=seq))]
    hook, timeline = display.bind(dict(asks=cues,arc=[]),editorial,words)["display_facts"]
    assert (hook["first"],hook["last"]) == (0,5)
    assert timeline["sequence_kind"] == "history"
    assert timeline["sequence"][1]["source"] == sources["founding"]
    assert timeline["sequence"][0]["source"] == sources["interview"]


def test_heard_resolution_can_precede_a_complete_aside():
    words = [dict(w=str(i),t0=i+.1,t1=i+.6) for i in range(32)]
    idx = dict(words=words,video=dict(duration=32.),sentences=[dict(wi0=0,wi1=31)])
    content = dict(window_start=150,source_captions=[],names=[],hook="0",close="31")
    for aligned in (False, True):
        out, _, keep, _ = alignment.apply_decision(idx,content,dict(first=0,last=25,close_first=21),aligned)
        assert not keep and [w["w"] for w in out["words"]] == [str(i) for i in range(26)]


def test_checked_excerpt_returns_on_another_clause_only_by_editorial_selection(tmp_path):
    rec = dict(path=str(tmp_path/"paper.jpg"),page="original",shows="Research diagram",
               document=dict(sha256="paper",page=3,box=[0,0,1,1]))
    windows = [dict(first=i,last=i,t0=i*2,t1=(i+1)*2,name=n,must_show=n,say=n)
               for i,n in enumerate(("Lab research","Its mechanism","Founding record"))]
    rec_path = tmp_path/"paper.jpg"
    rec_path.write_bytes(b"checked page pixels")
    import hashlib
    digest = hashlib.sha256(rec_path.read_bytes()).hexdigest()
    windows[1]["reuse_excerpt"] = dict(path=rec["path"],sha256=digest)
    data = dict(windows=windows)
    rows = coverage.proposals(data,{},{"Lab research":[rec]})
    assert [(r[4]["window"],r[4]["path"]) for r in rows] == [(0,rec["path"]),(1,rec["path"])]
    assert rows[1][4]["clause"] == "Its mechanism"
    assert len(coverage.proposals(data,{},{"Lab research":[rec]},rejected={(1,rec["path"])})) == 1


def test_refill_reuses_a_catalogued_document_and_keeps_the_new_event_open(tmp_path,monkeypatch):
    import wf1_short as wf

    paper = tmp_path/"paper.jpg"
    paper.write_bytes(b"issued figure")
    pack = dict(coverage=dict(windows=[
        dict(name="Mechanism",say="How the method worked",t0=0,t1=2,must_show="Original method"),
        dict(name="Opening",say="The office opened",t0=2,t1=4,must_show="Actual opening")]),
        footage=[], pictures={"Paper":[dict(path=str(paper),document={"page":1},page="paper",shows="Method diagram")]},
        referent_research={"references":[]})
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=pack)))
    def direct(system,payload,**kwargs):
        data = json.loads(payload)
        item, = data["research"]["excerpts"]
        assert item["shows"] == "Method diagram"
        return json.dumps(dict(windows=[dict(w,**({"reuse_id":item["id"]} if w["i"]==0 else {}))
                                        for w in data["windows"]])), {}
    monkeypatch.setattr(wf,"_llm",lambda:SimpleNamespace(ask=direct,json_block=json.loads))
    missing = []
    monkeypatch.setattr(wf,"motion_research",lambda ctx,pack,shortage:missing.extend(shortage["missing_windows"]) or True)
    wf.refill(SimpleNamespace(supply=str(tmp_path),log=lambda _:None),{},pack,
              dict(weak_windows=[0,1],missing_windows=[0,1],real_seconds=0))
    assert pack["coverage"]["windows"][0]["reuse_excerpt"]["path"] == str(paper)
    assert missing == [1]


def test_contemporary_statement_and_spoken_memory_have_separate_credits(tmp_path,quiet_engines,monkeypatch):
    from test_wf1_display_facts import material
    import wf1_film

    _,script,pack = material(tmp_path)
    interview = dict(url="episode",publisher="Podcast")
    record = dict(url="lab",publisher="Lab archive",publication_date="2012")
    claim = dict(speaker="Founder",stance="recollection",report="Wider adoption was difficult.")
    scoped = display.claim_fact(dict(claim,scope_fact=dict(source="lab",statement="Some teams used the software in production.")),
                                {},{"interview":interview,"lab":record},[])
    ordinary = display.claim_fact(dict(claim,stance="concern"),{},{"interview":interview},[])
    monkeypatch.setattr(coverage,"speaker_moves",lambda *a:([],[]))
    scene,beats = wf1_film.build(script,pack,"prints",dict(display_facts=[
        dict(first=1,last=12,facts=[scoped]),dict(first=8,last=16,facts=[ordinary])]))
    # The contemporary statement and the spoken memory keep separate credits, in the record: neither byline nor
    # the archive's credit is printed over the speaker.
    words = [i for i in scene["items"] if i.get("fact_type")]
    assert not any(i.get("t") in ("Founder · Recollection","Founder · Concern","Lab archive") for i in words)
    facts = [f for p in beats["research_facts"] for f in p["facts"]]
    assert {f["chain"]["stance"] for f in facts} == {"recollection","concern"}
    assert [(p["first"],p["last"]) for p in beats["research_facts"]] == [(1,12),(8,16)]
    assert all(f["attribution"] == "Founder" for f in facts)


    at = len(pack["words"]["words"]) - 15
    seq = [dict(at=at-4,label="Sought wider adoption",source=interview),
           dict(at=at,label="Company founded",source=record)]
    history = dict(first=at-4,last=at+1,sequence=seq,sequence_kind="history",
                   facts=[dict(attribution="Founder",source=interview,relation="History")])
    tail = dict(first=at+2,last=len(pack["words"]["words"])-1,facts=[ordinary])
    scene, beats = wf1_film.build(script,pack,"prints",dict(display_facts=[history,tail]))
    timeline, = beats["display_facts"]
    assert timeline["last"] > history["last"]
    assert timeline["last"] < tail["last"]
    assert not any(i.get("t") == "Founder · Concern" for i in scene["items"])
