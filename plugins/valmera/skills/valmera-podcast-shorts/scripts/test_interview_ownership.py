import wf1_display as display
from podcast import _sources_md


def test_research_summary_cannot_become_the_issuers_recollection():
    interview = dict(url="interview", publisher="Show")
    source = dict(url="release", publisher="Issuer", quotations=[])
    view = dict(speaker="Guest", stance="recollection", report="The budget was never exhausted.", interview="interview")
    mixed = dict(source=source, statement="The guest recalls this. No annual records found.", perspective=view)
    pages = display.episode_pages([dict(first=3,last=9,facts=[mixed])], {}, [])
    fact, = pages[0]["facts"]
    assert fact["source"]["url"] == "interview"
    assert fact["value"] == view["report"] and fact["chain"]["stance"] == "recollection"


def test_definition_repeated_under_same_explicit_term_once():
    def page(first, term):
        return dict(first=first,last=first+1,facts=[dict(chain=dict(stance="definition",term=term))])
    rows = [page(0,"tender"), page(14,"tender"), page(20,"marketplace")]
    assert display.episode_pages(rows, {}, []) == [rows[0],rows[2]]


def test_source_package_includes_the_exact_document_item_and_interview_reading():
    pic=dict(f=[3,57],document=dict(url="release",page=1,publication="Issuer, December 2024",words="Exact selected sentence."))
    chain=dict(stance="recollection",report="We left some unused.",speaker="CEO",interview="episode")
    film=dict(window=[30,45],source=dict(source_url="episode"),checks=dict(podcast=dict(sources=[pic],
              display_facts=[dict(first=12,last=15,f=[180,220],facts=[dict(chain=chain,value=chain["report"])])])))
    report=_sources_md("Show", [dict(n=1,title="Tender",style="Window",film=film)])
    assert "Exact selected sentence." in report
    assert "CEO | We left some unused. | episode" in report


def test_interview_cue_leaves_selected_record_its_own_words():
    words = [dict(seg=0)] * 12
    statement = dict(first=3,last=5,facts=[dict(record={"heading":"Financing"})])
    cue = dict(first=1,last=8,speaker="Guest",report="That was my impression.",label="Recollection",
               stance="recollection")
    result = display.episode_pages([statement],dict(interview_cues=[cue]),words)
    assert [(p["first"],p["last"]) for p in result] == [(1,2),(3,5),(6,8)]
    assert result[1] == statement
    assert result[0]["facts"][0]["chain"]["witness"] == "recollection"


def test_compose_attributes_the_two_halves_of_one_recollection(tmp_path, monkeypatch):
    import wf1_film as film
    import wf1_coverage
    from test_wf1_take import _sup, cuts_two_shot
    monkeypatch.setattr(film.F,"engines_finish",lambda scene,*a,**k:scene)
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *a:([],[]))
    pack = _sup(tmp_path,cuts_two_shot())
    words = pack["words"]["words"]
    view = dict(stance="recollection",speaker="Founder",report="Some money was unused.",interview="episode")
    page = dict(first=3,last=14,facts=[dict(source={"url":"release"},statement="The editor could not find figures.",
                                             perspective=view)])
    pack["coverage"]["interview_cues"] = [dict(first=a,last=b,speaker="Founder",report=view["report"],label=label)
        for a,b,label in [(3,9,"Budget set aside"),(10,14,"Some unused")]]
    script = dict(title="Tender",source_url="episode", segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==i))
                                                                for i in (0,1)])
    scene,beats=film.build(script,pack,"window",dict(coverage_final=True,display_facts=[page],caption_ends=[6,9,14,17]))
    rows=[it for it in scene["items"] if it.get("k")=="word"]
    # Both halves are attributed in the record; the screen keeps the live founder and his timed words.
    halves = [p for p in beats["research_facts"] if p["facts"][0].get("chain")]
    assert [p["facts"][0]["chain"]["speaker"] for p in halves] == ["Founder","Founder"]
    assert {p["facts"][0]["chain"].get("label") for p in halves} == {"Budget set aside","Some unused"}
    assert all("recollection" not in it["t"].lower() and "could not find" not in it["t"] for it in rows)
    for w in words[3:15]:
        at = round((w["start"]+w["end"])/2*30)+3
        assert any(i.get("k")=="shot" and i.get("pic")!="plate" and i["f"][0]<=at<i["f"][1] for i in scene["items"])
        assert any(i.get("role")=="cap" and w["w"].strip(".,").lower() in i["t"].lower() and i["f"][0]<=at<i["f"][1]
                   for i in scene["items"]), w["w"]


def test_checked_window_selects_its_release_sentence_without_another_search(tmp_path, monkeypatch):
    import hashlib
    from PIL import Image
    import wf1_documents as docs
    quote="This capital is expected to provide employee liquidity."
    url="https://issuer.example/release"
    other=dict(url="https://issuer.example/profile",quotations=[dict(words="A listing is an option.",sha256="profile")])
    src=dict(url=url,publication_date="17 Dec 2024",quotations=[dict(words=quote,sha256="release",speaker="Issuer",event="Financing")])
    ref=dict(windows=[2],sources=[other,src],record=dict(source=url,words=quote,heading="Financing",event_date="2024"))
    window=dict(i=2,name="Issuer",say="We arrange our own tenders.",must_show=quote,
                context=dict(primary_research=dict(references=[ref])))
    def webpage(actual_url,folder,cached,words,focus):
        assert actual_url==url and words==quote
        from pathlib import Path
        Path(folder,"original.html").write_text(quote)
        image=Image.new("RGB",(512,100))
        image.info["selected_words"]=words
        return [image]
    monkeypatch.setattr(docs,"webpage",webpage)
    result=docs.acquire([window],str(tmp_path),None)
    picture,=result["pictures"]["Issuer"]
    assert picture["document"]["sha256"]==hashlib.sha256(quote.encode()).hexdigest()
    assert picture["document"]["words"]==quote and picture["page"]==url
    assert len(result["credits"])==1 and result["spend"]==[]
