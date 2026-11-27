import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
import fit
import reel
import evidence_cut
import writer


def test_literal_phrase_briefs_retain_actions_outside_the_named_list():
    result = writer._clean_see([dict(thing="sweeping the floor", entity="the company", kind="literal",
                                    word_i=3, search="period shop floor broom", video_searches=["sweeping a shop"])], {"the company"})
    assert result[0]["word_i"] == 3
    assert result[0]["video_searches"] == ["sweeping a shop"]


def test_ad_actor_cannot_fill_insider_phrase_and_dropped_take_cannot_return():
    words = "Insiders objected but listeners bought it".split()
    clock = SimpleNamespace(segs=[(0,3)], seg_words=lambda _: [dict(w=w,t0=i/2) for i,w in enumerate(words)])
    script = dict(segments=[dict(say=" ".join(words),see=[
        dict(thing="the listeners",word_i=3),dict(thing="company insiders",word_i=0)])])
    def verdict(grade):
        return dict(fits_line=bool(grade), exact_thing=bool(grade),time_ok=True,version_ok=True,contradicts=False,grade=grade)
    files = {"z-insider":{0:5,3:0},"a-listener":{0:0,3:5},"ad-actor":{0:0,3:0},"dropped":{0:5,3:5}}
    photos = {"company":[dict(path=p) for p in files]}
    table = {p:{0:{0:dict(verdict(max(v.values())), phrases={i:verdict(g) for i,g in v.items()})}} for p,v in files.items()}
    sp = reel.Supply(photos,{},fit=fit.fits(table),deny={("dropped",0)})
    sp.verdicts=json.loads(json.dumps(table))
    cut=reel.cut_fit(script,clock,sp,90,[])
    assert [(r[0],r[1],r[4]["path"]) for r in cut] == [(0,45,"z-insider"),(45,90,"a-listener")]


def test_identifying_portrait_survives_render_readability_repair_at_any_age():
    photo=dict(path="portrait",w=600,h=900)
    supply=reel.Supply({"person":[photo]}, {}, deny={("portrait",0)})
    names={fit.take_key("portrait",(0,0)): dict(work="a later interview",year=2018,people=[dict(name="A Person",role="founder")])}
    rows=evidence_cut.place(dict(segments=[dict(era=1970,show=[dict(role="name",text="A Person",word_i=0)])]),
        SimpleNamespace(seg_words=lambda _:[dict(t0=.1)]),[[0,90,"clip","other",dict(path="other",pick_line=0)]],supply,names)
    portrait=next(r for r in rows if r[4].get("depicted_name"))
    assert portrait[4]["path"]=="portrait" and portrait[1]-portrait[0]>=1.2*reel.FPS
    assert portrait[4]["portrait_repair"]


def test_burned_timecode_crop_preserves_subject_and_measures_changing_glyphs(tmp_path):
    from PIL import Image,ImageDraw
    frames=[]
    for i in range(3):
        im=Image.new("RGB",(320,180),"grey"); draw=ImageDraw.Draw(im)
        draw.text((10,154),f"12:45:0{i}",fill="white")
        path=tmp_path/f"{i}.png";im.save(path);frames.append(str(path))
    box=[.01,.82,.35,1]
    signal=fit.overlay_signal(frames,box)
    assert signal["fixed_strokes"]>.2 and signal["changing_strokes"]>0
    assert fit.overlay_crop([box],[.12,.15,.81,.72]) == [0,0,1,.82]


def test_slide_recorded_as_video_preserves_the_readable_rows_and_video_clock(monkeypatch):
    import film
    monkeypatch.syspath_prepend(film.scene_worker())
    from keepout import watermark_zone
    rows = [.04,.28,.94,.48]
    slide = dict(w=1920,h=1080,src=[3.,5.],meaning_box=rows)
    plan = film.Plan.__new__(film.Plan)
    plan.items, plan.frame_ids, plan.n = [],None,0
    geometry = film.placed_evidence(plan,[0,60],"slide",slide,rows)
    item = plan.items[-1]
    assert item["k"]=="card" and item["src"]==[3.,5.]
    assert item["crop"][0]<=rows[0]<rows[2]<=item["crop"][2]
    assert geometry["panel"][1]>watermark_zone()[3]
    assert geometry["meaning_height_px"]>=100


def test_scarce_picture_is_reported_on_the_specific_uncovered_phrase():
    import wf3
    v = dict(fits_line=True,exact_thing=True,time_ok=True,version_ok=True,contradicts=False,grade=4)
    table = {"one photo":{0:{0:dict(v,phrases={0:v,2:v})}}}
    report, = wf3.fit_report(dict(fit_verdicts=table),dict(segments=[dict(see=[
        dict(word_i=0,thing="first"),dict(word_i=2,thing="second")])]),[3.])
    assert report["deficit"]==1 and report["missing"][0]["word_i"] in (0,2)


def test_a_short_witness_plays_at_native_speed_while_the_true_still_holds():
    assert reel.slot_edges(10,100,[12,90])==[10,22,100]


def test_adjacent_phrases_can_hold_the_same_photo_when_it_depicts_both():
    words=["His","desk","held","letters"]
    clock=SimpleNamespace(segs=[(0,4)],seg_words=lambda _:[dict(w=w,t0=i) for i,w in enumerate(words)])
    sc=dict(segments=[dict(say=" ".join(words),see=[dict(thing="desk",word_i=0),dict(thing="letters",word_i=2)])])
    v=dict(fits_line=True,exact_thing=True,time_ok=True,version_ok=True,contradicts=False,grade=5)
    table={"desk-photo":{0:{0:dict(v,phrases={0:v,2:v})}}}
    sp=reel.Supply({"desk":[dict(path="desk-photo")]},{},fit=fit.fits(table))
    sp.verdicts=table
    rows=reel.cut_fit(sc,clock,sp,120,[])
    assert len(rows)==1 and rows[0][:2]==[0,120]
    assert [p["word_i"] for p in rows[0][4]["phrase_spans"]]==[0,2]
    assert not sp.shortfall



def test_remake_refreshes_entity_only_brief_even_when_display_clock_is_current():
    import wf3
    old = dict(thing="company", search="company", word_i=0)
    seg = dict(say="A team asked", see=[old], phrases=[[0, 3]], show=[], list=False)
    assert wf3.needs_annotate(dict(segments=[seg]))
    old.update(kind="literal", video_searches=["team making the request"])
    assert not wf3.needs_annotate(dict(segments=[seg]))
