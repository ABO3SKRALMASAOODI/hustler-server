import hashlib
import json
from types import SimpleNamespace

from wf1_publisher_reading import local_coverage
from wf1_coverage import proposals
import wf1_short as wf


def test_publishers_read_over_exact_retained_insets_without_scouting_neighbour(tmp_path):
    camera = dict(name="Interview", first=0,last=21,t0=0.,t1=9.,say="the opening concern",must_show="policy")
    other = dict(name="Independent example",first=22,last=30,t0=9.,t1=12.,say="the example",must_show="record")
    windows = [camera, other]
    selected = dict(camera_windows=[0],windows=[{k:w[k] for k in ("first","last","say")} for w in windows])
    asset = tmp_path/"checked.png"
    asset.write_bytes(b"approved image")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    crop = [.1,.15,.83,.68]
    prior = dict(path=str(asset),sha256=digest,source="publication", f=[70,162],clause=camera["say"],
                 clause_t=[0.,9.], approved_crop=crop,approved_media=digest)
    coverage = dict(windows=windows,passed_placements={"0":[prior]})
    for legacy in ("Andrew Lear", None, {}, dict(camera_windows=[0]),
                   dict(camera_windows=None, windows=None), dict(camera_windows=[1, -1], windows=[camera])):
        assert local_coverage(coverage, legacy) == coverage
    seated = local_coverage(coverage, selected)
    photos = {"Interview":[dict(path=str(asset),page="publication"),dict(path="/unused.png")]}
    rows = proposals(seated,{},photos)
    assert [(r[:2],r[4]["path"],r[4]["approved_crop"]) for r in rows] == [([70,162],str(asset),crop)]
    assert seated["windows"][1] is other and "publisher_camera" not in camera
    assert not local_coverage(dict(windows=[dict(camera,last=23)]),selected)["windows"][0].get("publisher_camera")
    measured = dict(holds=[[0,12]],longest_hold_s=12.,cuts_per_10s=2.)
    ctx = SimpleNamespace(supply=str(tmp_path),log=lambda message:None)
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=dict(coverage=coverage))))
    rendered = tmp_path/"programme.mp4"
    rendered.touch()
    sc=dict(beats=dict(publisher_reading=selected,pictures=[]))
    assert wf._rebuild_holds(ctx,{},sc,dict(film=str(rendered),measured_cuts=measured),defer=True)
    assert sc["beats"]["research_windows"] == [1]


def test_a_cached_source_reading_reuses_its_publisher_pixels_and_dates(tmp_path,monkeypatch):
    import wf1_documents as docs
    import wf1_publisher_reading as reading
    from PIL import Image
    sources = [dict(url="https://example.org/"+key, publisher="Company",publication_date=date)
               for key,date in [("plan","2025-11-21"),("update","2026-09-03")]]
    pages = [dict(first=0,last=1,facts=[dict(source=sources[0],supporting_sources=sources,
                 perspective=dict(stance="concern"))])]
    coverage = dict(words=[dict(w=w,start=i,end=i+1) for i,w in enumerate(("these","concerns"))],windows=[])
    calls=[]
    def download(url, work):
        calls.append(url)
        return b"<p>We will change the ranking.</p>",url
    def capture(url,folder,**kw):
        assert kw["offline"] and b"We will" in kw["cached"]
        image=Image.new("RGB",(40,20))
        image.info["selected_words"]=kw["words"]
        return [image]
    monkeypatch.setattr(docs,"download",download)
    monkeypatch.setattr(docs,"webpage",capture)
    answer=dict(readings=[dict(first=0,last=1,page=0,url=sources[0]["url"],quote="We will change the ranking.",
                               followup=sources[1]["url"])])
    llm=SimpleNamespace(ask=lambda *a,**kw:(json.dumps(answer),{"seconds":2}),json_block=json.loads)
    plan,cost=reading.read(pages,coverage,tmp_path,llm)
    assert plan["readings"][0]["followup"]["publication_date"] == "2026-09-03"
    assert cost and reading.read(pages,coverage,tmp_path,None)==(plan,[])
    (tmp_path/"pack.json").write_text(json.dumps(dict(pack=dict(coverage=coverage))))
    legacy = dict(beats=dict(publisher_reading="Andrew Lear"), publisher_read=True)
    ctx = SimpleNamespace(supply=str(tmp_path))
    wf.restore_publisher_reading(ctx, legacy, pages)
    assert legacy["beats"]["publisher_reading"] == plan
    for cache in (tmp_path/"publisher-readings").glob("*.json"):
        cache.unlink()
    excerpt = plan["readings"][0]
    legacy["beats"] = dict(display_facts=[dict(publisher_excerpt=excerpt)])
    wf.restore_publisher_reading(ctx, legacy, pages)
    assert legacy["beats"]["publisher_reading"] == dict(readings=[excerpt])
    assert calls==[s["url"] for s in sources]


def test_selected_publisher_inset_keeps_the_exact_image(tmp_path, monkeypatch):
    import wf1_documents as docs
    import wf1_publisher_reading as reading
    from PIL import Image

    source = dict(url="https://example.org/record", publisher="Publisher", publication_date="2025-11-21")
    pages = [dict(first=0,last=0,facts=[dict(source=source,perspective=dict(stance="concern"))])]
    coverage = dict(words=[dict(w="Ranking",start=0,end=1)],windows=[])
    original = tmp_path/"approved.png"
    Image.new("RGB",(60,20)).save(original)
    doc = dict(url=source["url"],words="We will prioritize original reporting.",sha256="publisher-original")
    stock = {"record":[dict(document=doc,provider="primary-document",path=str(original))]}
    answer = dict(readings=[dict(page=0,first=0,last=0,url=source["url"],quote="prioritize original reporting")])
    llm = SimpleNamespace(ask=lambda *a,**kw:(json.dumps(answer),{}),json_block=json.loads)
    def no_lookup(*args, **kwargs):
        raise AssertionError("Already have the approved publisher crop")
    monkeypatch.setattr(docs,"download",no_lookup)
    monkeypatch.setattr(docs,"webpage",no_lookup)
    plan,_ = reading.read(pages,coverage,tmp_path,llm,stock)
    assert plan["readings"][0]["path"] == str(original)
    assert plan["readings"][0]["quote"] == doc["words"]
    assert reading.read(pages,coverage,tmp_path,None,stock) == (plan,[])
