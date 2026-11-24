import hashlib
import json

from wf1_publisher_reading import read, local_coverage
from wf1_short import hold_windows


def test_reported_quote_and_filing_share_the_camera_on_their_own_words(tmp_path):
    quoted = dict(url="https://host.example/talk", speaker="Guest", words="understand our customers",
                  sha256="verified")
    pages = [dict(first=0,last=8,facts=[dict(source=dict(url=quoted["url"],publisher="Host"),
                    excerpt=quoted,chain=dict(speaker="Interviewer"))]),
             dict(first=9,last=11,facts=[dict(example=True,source=dict(url="https://issuer.example/report",
                    publisher="Issuer"),value="A network")])]
    words = [dict(w=str(i),start=i,end=i+.9) for i in range(12)]
    windows = [dict(first=0,last=8,t0=0,t1=9,say="An attributed remark")]
    coverage = dict(words=words,windows=windows)
    folder = tmp_path/"document-sources"
    folder.mkdir()
    data = b'<html><ix:nonnumeric name="r:OversightTextBlock">Our committee oversees risk.</ix:nonnumeric></html>'
    path = folder/"filing.html"
    path.write_bytes(data)
    (folder/"manifest.json").write_text(json.dumps([dict(url=pages[1]["facts"][0]["source"]["url"],
        path=str(path),sha256=hashlib.sha256(data).hexdigest())]))

    class Reader:
        json_block = staticmethod(json.loads)
        def ask(self, prompt, payload, **kwargs):
            assert "Our committee oversees risk." in payload
            return json.dumps(dict(readings=[dict(page=0,first=2,last=5,quote="understand our customers"),
                dict(page=1,passage=0,body="Committee oversees risk",credit="Issuer filing")],
                camera_windows=[0])), {}

    plan, _ = read(pages, coverage, str(tmp_path), Reader())
    assert [(r["first"],r["last"]) for r in plan["readings"]] == [(2,5),(9,11)]
    assert plan["readings"][0]["credit"] == "Guest · Host"
    assert plan["readings"][1]["evidence"] == "Our committee oversees risk."
    assert hold_windows(dict(holds=[[0,9]],cuts_per_10s=0),local_coverage(coverage,plan),[],fps=30,lead=0) == []
    assert read(pages,coverage,str(tmp_path),None,cached_only=True)[0] == plan


def test_only_the_same_transcript_in_its_reported_clause_loses_its_duplicate_strip():
    from wf1_scoped_reading import represented_excerpt
    original = dict(url="host/talk",words="Understand our customers")
    pages = [dict(first=9,last=44)]
    readings = [dict(page=0,first=15,last=27,original=original)]
    coverage = dict(windows=[dict(first=7,last=18),dict(first=83,last=89)])
    row = [91,109,"photo","author",dict(document=original,window=0)]
    assert represented_excerpt(row,readings,pages,coverage)
    assert not represented_excerpt([*row[:4],dict(row[4],window=1)],readings,pages,coverage)
    assert not represented_excerpt([*row[:4],dict(row[4],document=dict(original,words="Another result"))],
                                   readings,pages,coverage)


def test_a_filing_reading_settles_only_the_exact_illustration_clock():
    windows = [dict(first=7,last=12,t0=2.,t1=4.,say="the process"),
               dict(first=16,last=24,t0=5.,t1=9.,say="a new action")]
    snapshots = [{k:w[k] for k in ("say","first","last")} for w in windows]
    plan = dict(readings=[dict(first=7,last=12,evidence="Verified filing passage")],windows=snapshots)
    measured = dict(holds=[[1.,10.]],cuts_per_10s=0.)
    assert hold_windows(measured,local_coverage(dict(windows=windows),plan),[],fps=30,lead=0) == [1]
    updated = [dict(windows[0],last=14),windows[1]]
    assert hold_windows(measured,local_coverage(dict(windows=updated),plan),[],fps=30,lead=0) == [0,1]


def test_accepted_projection_settles_its_own_frames_without_claiming_motion(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import wf1_short as wf
    pack = dict(coverage=dict(windows=[dict(t0=1.,t1=2.)]))
    monkeypatch.setattr(wf.wf3,"load_pack",lambda ctx:pack)
    pic = dict(path="stage",f=[33,63],window=0,projection=dict(crop=[0,.3,.4,.7],gamma=1.6))
    sc = dict(beats=dict(pictures=[pic]))
    report = dict(placements=[dict(id=0,ok=True,motion_ok=False)])
    ctx = SimpleNamespace(supply=str(tmp_path),log=lambda _:None)
    assert wf._rebuild_motion(ctx,{},sc,report,defer=True) is False
    assert report["placements"][0]["motion_ok"] is False
    measured = dict(holds=[[0.,12.]],cuts_per_10s=0.)
    assert hold_windows(measured,pack["coverage"],[pic]) == []
    assert hold_windows(measured,pack["coverage"],[dict(pic,f=[33,41])]) == [0]


def test_live_source_panel_clears_the_mark_and_comparison_keeps_the_camera(tmp_path, monkeypatch):
    from test_wf1_display_facts import material
    from placement_check import CONTRACT
    import wf1_short as wf

    _, script, pack = material(tmp_path)
    monkeypatch.setattr(wf.F,"voice_stress",lambda _:[])
    monkeypatch.setattr(wf.F,"engines_finish",lambda scene,*args,**kwargs:scene)
    pack["archived_placement_plan"] = dict(run_id="copy",rows=[],report=dict(contract=CONTRACT,spend=[]))
    source = dict(url="original",publisher="Publisher")
    quote = dict(speaker="Author",words="one original excerpt",url="original",event="Talk",locator="Transcript")
    page = dict(first=2,last=12,facts=[dict(source=source,value=quote["words"],excerpt=quote)])
    reading = dict(page=0,first=4,last=10,credit="Author · Publisher",quote=quote["words"],source=source,original=quote)
    comparison = dict(first=23,change=28,last=32,speaker="Interviewee",left="10 years",right="40 years")
    plan = dict(readings=[reading],comparison=comparison)
    scene, beats = wf.wf1_film.build(script,pack,"footage-cut",dict(publisher_reading=plan,display_facts=[page]))
    lo,hi = next(p["f"] for p in beats["display_facts"] if p.get("publisher_excerpt"))
    for f in range(lo,hi):
        live = next(it for it in scene["items"] if it.get("speaker_panel") and it["f"][0] <= f < it["f"][1])
        assert live["y"] - live["h"]/2 > wf.F.MARK[3]
    ages = [it for it in scene["items"] if it.get("text_group","").startswith("comparison-age")]
    for f in range(min(it["f"][0] for it in ages),max(it["f"][1] for it in ages)):
        assert any(it.get("speaker_panel") and it.get("crop")==[0,0,1,1] and
                   it["f"][0] <= f < it["f"][1] for it in scene["items"])
