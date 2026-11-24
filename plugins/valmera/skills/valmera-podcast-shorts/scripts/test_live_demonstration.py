import json
from types import SimpleNamespace

from PIL import Image

import wf1_coverage
from test_wf1_take import quiet_engines


def test_demo_reserves_explanation_and_pins_the_visible_job_on_the_pointer(tmp_path):
    words = [dict(w=w, start=i, end=i+.8, seg=0) for i,w in enumerate(
        "It maps people and files look here my daily brief".split())]
    path = tmp_path / "source.png"
    Image.new("RGB", (640,360)).save(path)
    frames = [dict(t=6.3, shot=[5,10], path=str(path))]
    result = dict(windows=[dict(first=3,last=4,name="Files",external=True,must_show="sample spreadsheet")],
                  live_demo=dict(first=0,cue=5,last=9,frame=0,box=[.02,.3,.24,.78],
                                 tile=[.8,.2,1,.4],shows="Daily brief",speaker="Guest"),
                  source_inserts=[dict(first=8,last=9,frame=0,box=[0,0,1,1])])
    llm = SimpleNamespace(ask=lambda *a, **kw: (json.dumps(result), {}),json_block=json.loads)
    plan = wf1_coverage.plan(words, "Demo", str(tmp_path), llm, frames=frames)
    assert plan["windows"] == []
    view, = plan["source_reading"]["inserts"]
    assert (view["first"],view["last"],view["box"]) == (5,9,result["live_demo"]["box"])
    assert view["path"] == str(path)


def test_rebuilt_score_draws_the_speaker_then_one_definition_then_the_episode_result(tmp_path, quiet_engines, monkeypatch):
    import wf1_film
    from test_wf1_take import _sup
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    pack = _sup(tmp_path, [dict(t0=0.,t1=7.,faces=[])])
    path = tmp_path / "episode.png"
    Image.new("RGB", (1920,1080)).save(path)
    words = pack["coverage"]["words"]
    demo = dict(first=0,last=len(words)-1,cue=13,speaker="Operator")
    inset = dict(first=13,last=demo["last"],t=5.,shot=[4.,7.],path=str(path),w=1920,h=1080,
                 box=[.02,.34,.22,.45],shows="Original result",demonstration=True)
    pack["coverage"].update(contract=wf1_coverage.CONTRACT,source_reading=dict(live_demo=demo,inserts=[inset]))
    fact = dict(value="This is one documented relationship",source=dict(url="issuer",publisher="Maker",publication_date="2026-11-11"))
    from placement_check import CONTRACT
    pack["archived_placement_plan"] = dict(run_id="previous",report=dict(contract=CONTRACT,spend=[],placements=[]),
        rows=[[19,46,"photo","Unrelated tutorial",dict(path=str(tmp_path/"retired.png"),w=640,h=360)]])
    original = dict(first=0,last=8,facts=[dict(fact,perspective=dict(stance="characterization",speaker="Operator"))])
    note = dict(first=4,last=8,demo_note="Definition",facts=[fact])
    script = dict(source_url="episode",source_credit="Interview",segments=[dict(say="speech"),dict(say="result")])
    scene,beats = wf1_film.build(script, pack, "window", dict(display_facts=[original],demo_notes=[note]))
    page, = beats["display_facts"]
    assert page["first"] == 4 and page["demo_note"] == "Definition"
    opening = [i for i in scene["items"] if i["k"]=="card" and i["f"][0]==0 and i.get("source_frame")]
    assert opening and opening[0]["crop"] == [0,0,1,1]
    pic, = beats["pictures"]
    assert pic["source"] == "episode" and pic["f"][0] == round(words[13]["start"]*30)+2
    detail, = [it for it in scene["items"] if it.get("crop")==inset["box"] and it["f"]==pic["f"]]
    assert detail["w"] >= .92
