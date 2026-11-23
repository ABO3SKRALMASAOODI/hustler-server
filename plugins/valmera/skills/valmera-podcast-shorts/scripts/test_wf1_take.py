"""The take follows who speaks and moves like an editor; the clip keeps a breath before the end card (19 Nov rating,
causes static-take and clip-edges)."""
import os

import pytest

import wf1_film as film


def cuts_two_shot():
    """The show's own edit: one two-shot (the guest on the left speaks, then the host on the right), then a single."""
    zuck = dict(box=[.28, .19, .36, .38], eyes=[[.30, .26], [.34, .26]], path=[])
    wang = dict(box=[.65, .17, .74, .40], eyes=[[.67, .25], [.72, .25]], path=[])
    return [dict(t0=0.0, t1=4.0, faces=[dict(box=wang["box"], eyes=wang["eyes"], score=1.0)],
                 spans=[dict(t0=0.0, t1=2.5, who="speaker", box=zuck, track=1), dict(t0=2.5, t1=4.0, who="speaker", box=wang, track=0)]),
            dict(t0=4.0, t1=7.0, faces=[dict(box=zuck["box"], eyes=zuck["eyes"], score=1.0)],
                 spans=[dict(t0=4.0, t1=4.6, who="quiet", box=None), dict(t0=4.6, t1=7.0, who="speaker", box=zuck, track=0)])]


def test_take_pieces_frame_whoever_speaks_and_punch_in_on_the_payoff():
    pieces = film.take_pieces(cuts_two_shot(), 7.0, punch=[int(5.5 * 30) + 3])
    assert [p["f"] for p in pieces] == [[0, 78], [78, 123], [123, 168], [168, 213]]
    assert pieces[0]["faces"][0]["box"][0] == .28            # the guest speaking, not the bigger face beside him
    assert pieces[1]["faces"][0]["box"][0] == .65            # the host's answer: a cut inside the show's two-shot
    assert pieces[2]["faces"][0]["box"][0] == .28            # the quiet start of the single keeps the speaker's framing
    assert [p["punch"] for p in pieces] == [False, False, False, True]


def test_old_cuts_without_spans_stay_one_piece_a_shot():
    cuts = [dict(t0=0.0, t1=3.0, faces=[dict(box=[.4, .2, .5, .4], eyes=[[.42, .27], [.48, .27]], score=1.0)]),
            dict(t0=3.0, t1=6.0, faces=[])]
    pieces = film.take_pieces(cuts, 6.0)
    assert [p["f"] for p in pieces] == [[0, 93], [93, 183]]
    assert pieces[0]["faces"] and pieces[1]["faces"] == []


def test_stress_moves_break_the_holds_the_director_left():
    holds = [[30, 200], [300, 330]]
    stress = [(40, 0.9), (100, 0.7), (150, 0.95), (195, 1.0), (310, 0.9)]
    assert film.stress_moves(holds, stress) == [100, 150]                 # every part left under three seconds
    long = [[0, 400]]
    stress = [(f, 0.5) for f in range(0, 400, 20)] + [(200, 1.0), (90, 0.9), (300, 0.9)]
    got = film.stress_moves(long, stress)
    assert 200 in got and 90 in got and 300 in got
    edges = [0] + got + [400]
    assert max(b - a for a, b in zip(edges, edges[1:])) <= 90


def _sup(tmp_path, cuts):
    words = "My favorite use case is having it coach me between the rounds and that is a hard problem".split()
    wj = dict(duration=7.0, words=[dict(w=w, start=round(.1 + i * .36, 3), end=round(.38 + i * .36, 3), seg=0 if i < 9 else 1)
                                   for i, w in enumerate(words)])
    return dict(words=wj, work=str(tmp_path), ffmpeg="ffmpeg", pictures={}, footage=[], narration="/take.mp4",
                take=dict(path="/take.mp4", w=1920, h=1080, fps=30, dur=7.0, faces=[], cuts=cuts),
                coverage=dict(words=wj["words"], moves=[], windows=[]))


@pytest.fixture
def quiet_engines(monkeypatch):
    monkeypatch.setattr(film.F, "voice_stress", lambda sup: [])
    monkeypatch.setattr(film.F, "engines_finish", lambda l1, *a, **k: l1)
    import placement_check
    monkeypatch.setattr(placement_check, "check", lambda rows, *a, **k: (rows, dict(placements=[], rejected=[], spend=[])))


def test_build_frames_the_speaker_ends_after_a_breath_and_flashes_the_turn(tmp_path, quiet_engines):
    words = _sup(tmp_path, cuts_two_shot())
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in words["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1)])
    l1, beats = film.build(script, words, "footage-cut", dict(turn_seg=1, coverage_final=True))
    last_word_f = round(words["words"]["words"][-1]["end"] * 30) + 3
    # the programme holds its breath after the last word (the clip kept it); the card joins after it
    assert beats["dur_f"] == min(last_word_f + film.BREATH_F, round(7.0 * 30) + 3)
    assert beats["voice_f"][1] == last_word_f
    # the take is cut where the speaker changes inside the show's two-shot, and punches in on the payoff
    pieces = beats["coverage"]["take_pieces"]
    assert any(p["punch"] for p in pieces) and len(pieces) >= 4
    assert beats["finishes"]["flashes"] == 1
    assert beats["turn_f"] in [it["f"][0] for it in l1["items"] if it["k"] == "flash" and it.get("mode") == "fill"]
    metas = beats["face_metadata"]
    takes = [it for it in l1["items"] if it["k"] == "shot"]
    assert {metas[it["pic"]]["face_h"] for it in takes if not metas[it["pic"]]["face_h"] == film.PUNCH_FACE_H} == \
        {film.medium_face_h([[0, 0, 0, .19]], 1080), film.medium_face_h([[0, 0, 0, .23]], 1080)}
    assert any(metas[it["pic"]]["face_h"] == film.PUNCH_FACE_H for it in takes)
    assert all(metas[it["pic"]]["max_up"] == film.TAKE_UP for it in takes)


def test_the_medium_leaves_room_for_a_real_punch_in():
    """A face 20% of a 1080p two-shot reaches the 3x ceiling at a 0.3 share: the medium widens so the payoff's
    close-up is still 1.3x tighter (19 Nov: every 'move' on the take was 4-7%)."""
    assert film.medium_face_h([[.3, .2, .08, .2]], 1080) == pytest.approx(3.0 / 1.3 * .2 * 1080 / 1920, abs=1e-4)
    assert film.medium_face_h([[.3, .2, .2, .5]], 2160) == film.TAKE_FACE_H         # a big face in 4K: the references' medium
    assert film.medium_face_h([], 1080) == film.TAKE_FACE_H


def test_a_payoff_near_a_framings_edge_punches_that_framing():
    cuts = cuts_two_shot()
    near_start = film.take_pieces(cuts, 7.0, punch=[80])                 # 2 frames into the host's answer
    assert [p["punch"] for p in near_start] == [False, True, False]
    near_end = film.take_pieces(cuts, 7.0, punch=[120])                  # 3 frames before the single's cut
    assert [p["punch"] for p in near_end] == [False, False, True]


def test_the_punch_in_is_on_the_one_speaking_never_a_listener():
    """b7c90a: the payoff over the host laughing must not become a close-up on the host."""
    cuts = cuts_two_shot()
    cuts[0]["spans"][1]["who"] = "listener"                                # the show's camera on the host while the guest talks
    pieces = film.take_pieces(cuts, 7.0, punch=[100], until=200)           # the payoff opens in the listener span
    assert [p["who"] for p in pieces][:2] == ["speaker", "listener"]
    assert not pieces[1]["punch"] and [p["punch"] for p in pieces if p["punch"]] == [True]
    punched = next(p for p in pieces if p["punch"])
    assert punched["who"] == "speaker" and punched["f"][0] >= 100
    # a payoff that ends before any speaker framing opens has no punch-in at all
    assert not any(p["punch"] for p in film.take_pieces(cuts, 7.0, punch=[100], until=110))
    # a listener framing 3 frames before its cut: still never the listener
    near_end = film.take_pieces(cuts, 7.0, punch=[120], until=200)
    assert [p["punch"] for p in near_end] == [False, False, True] and near_end[2]["who"] == "speaker"


def _flash_frames(l1):
    return [it["f"][0] for it in l1["items"] if it["k"] == "flash" and it.get("mode") == "fill"]


@pytest.mark.parametrize("lag", [2, -3])
def test_the_payoff_flash_lands_on_a_real_cut(tmp_path, quiet_engines, monkeypatch, lag):
    """A payoff 2 frames into a framing (the framing is the punch, cut at its start) or 3 frames before a cut (the
    next framing is the punch): the white flash sits on that cut, never on the payoff's word inside a shot."""
    words = _sup(tmp_path, cuts_two_shot())
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in words["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1)])
    turn_word = next(w for w in words["words"]["words"] if w["seg"] == 1)
    turn_f = round(turn_word["start"] * 30) + film.LEAD_F - 1
    cut_f = turn_f - lag                                                   # where the show's camera cuts, near the payoff
    import wf1_coverage                                                    # the director is not asked here (no model in tests)
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda coverage, *a, **k: (list(coverage.get("moves", [])), []))
    cuts = cuts_two_shot()
    t = (cut_f - film.LEAD_F) / 30
    cuts[0]["t1"] = cuts[1]["t0"] = cuts[0]["spans"][1]["t1"] = cuts[1]["spans"][0]["t0"] = t
    cuts[1]["spans"][0]["t1"] = max(cuts[1]["spans"][0]["t1"], t + 0.2)
    words["take"]["cuts"] = cuts
    l1, beats = film.build(script, words, "footage-cut", dict(turn_seg=1, coverage_final=True))
    assert beats["turn_f"] == turn_f
    starts = {it["f"][0] for it in l1["items"] if it["k"] == "shot"}
    assert cut_f in starts and turn_f not in starts
    assert _flash_frames(l1) == [cut_f]
    assert set(_flash_frames(l1)) <= starts


def test_a_rebuild_with_no_open_holds_adds_no_stress_moves(tmp_path, quiet_engines, monkeypatch):
    """The pack's coverage is reused by retries and resumes: a second build whose holds are all broken (here the
    director's moves now cover them) must not break the first build's stale holds again."""
    import functools
    import json as _json
    from types import SimpleNamespace
    import wf1_coverage
    asked = []
    director = SimpleNamespace(ask=lambda *a, **k: (asked.append(1) or '{"moves": []}', {}), json_block=_json.loads)
    monkeypatch.setattr(wf1_coverage, "speaker_moves", functools.partial(wf1_coverage.speaker_moves, llm=director))
    monkeypatch.setattr(film.F, "voice_stress", lambda sup: [(f, 0.5 + (f % 7) / 10) for f in range(0, 240, 10)])
    one_shot = [dict(t0=0.0, t1=7.0, faces=[dict(box=[.4, .2, .5, .4], eyes=[[.42, .27], [.48, .27]], score=1.0)])]
    sup = _sup(tmp_path, one_shot)
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in sup["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1)])
    _, first = film.build(script, sup, "footage-cut", dict(turn_seg=1, coverage_final=True))
    assert asked and sup["coverage"]["unresolved_holds"] and first["coverage"]["stress_moves"]
    sup["coverage"]["moves"] = [dict(word=4, why="the turn of the claim"), dict(word=15, why="the hard part")]
    _, second = film.build(script, sup, "footage-cut", dict(turn_seg=1, coverage_final=True))
    assert sup["coverage"]["unresolved_holds"] == [] and second["coverage"]["stress_moves"] == []


@pytest.mark.parametrize("story_word,flashed", [(7, True), (3, False)])
def test_the_story_turn_flash_snaps_to_a_real_cut_or_stays_off(tmp_path, quiet_engines, monkeypatch, story_word, flashed):
    """Integration of the craft and take lanes (20 Nov): the story turn's flash lands on the film's real cut nearest it
    (here the speaker change inside the two-shot), never on a join of its own inside a continuous shot; with no real
    cut within a second there is no story flash, and the take is never split at the story turn."""
    words = _sup(tmp_path, cuts_two_shot())
    for i, w in enumerate(words["words"]["words"]):
        w["seg"] = 0 if i < story_word else 1 if i < 15 else 2
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in words["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1, 2)])
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda coverage, *a, **k: (list(coverage.get("moves", [])), []))
    l1, beats = film.build(script, words, "footage-cut", dict(turn_seg=2, story_turn_seg=1, coverage_final=True))
    starts = {it["f"][0] for it in l1["items"] if it["k"] == "shot"}
    story_f = beats["story_turn_f"]
    assert story_f is not None and story_f not in starts
    flashes = _flash_frames(l1)
    assert set(flashes) <= starts
    real = sorted(f for f in starts if 0 < f and abs(f - story_f) <= 30)
    if flashed:
        assert real and min(real, key=lambda f: (abs(f - story_f), f)) in flashes
    else:
        assert not real and len(flashes) == 1                              # the payoff's flash alone


def test_scarce_footage_keeps_live_argument_and_attributes_listener_cut(tmp_path,quiet_engines,monkeypatch):
    import wf1_coverage
    face=dict(box=[.36,.15,.65,.54],eyes=[[.43,.28],[.56,.28]],score=1.)
    cuts=[dict(t0=a,t1=b,faces=[face],spans=[dict(t0=a,t1=b,who=who,box=face,track=0)])
          for a,b,who in [(0,12,"speaker"),(12,16,"listener")]]
    sup=_sup(tmp_path,cuts)
    for word in sup["words"]["words"]:
        word["start"]*=3.0
        word["end"]*=3.0
    sup["take"]["dur"]=sup["words"]["duration"]=16.
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *a:([dict(word=4,why="turn to the answer")],[]))
    lines=[" ".join(w["w"] for w in sup["words"]["words"] if w["seg"]==i) for i in (0,1)]
    script=dict(title="Coach",source_credit="Interview",source_start=120,source_url="https://example.org/interview",
                segments=[dict(say=line,pictures=[]) for line in lines])
    script["speaking_turns"] = [dict(start=120,end=136,speaker="The coach")]
    sup["coverage"]["source_reading"] = dict(inserts=[],quote_segments=[0])
    l1,beats=film.build(script,sup,"footage-cut",dict(coverage_final=True))
    evidence=beats["coverage"]
    quoted=evidence["quoted_source"][0]
    assert quoted["quote"]==lines[0] and quoted["credit"]=="The coach  ·  Interview  ·  2:00" and quoted["url"]==script["source_url"]
    panel=evidence["speaker_panels"][0]
    live=next(i for i in l1["items"] if i["k"]=="card" and i["f"]==panel["f"])
    assert l1["assets"][live["pic"]]["path"]==sup["take"]["path"]
    assert live["h"] >= .80
    edges = [(y-live["h"]*scale/2, y+live["h"]*scale/2) for y,scale in zip(live["y"],live["s"])]
    assert min(b for _,b in edges) >= .86
    assert edges[0] == edges[-1] == pytest.approx((0.,1.))
    assert live["src"][0]==pytest.approx((panel["word_frame"]-film.LEAD_F)/film.FPS)


def test_a_local_card_repair_preserves_other_frames_layer_identity():
    def ids(extra):
        p=film.F.Plan({}, {}, frame_ids=True)
        if extra:p.page([300,340],"#010203")
        a=p.shot([0,120],"main",[0,0,1,1])
        b=p.word([3,25],"Hello","cap",.1,.7)
        return a["id"],b["id"]
    assert ids(False)==ids(True)


def test_archival_credit_quotes_event_date_independently_of_recollection():
    rec=dict(faces=[{}],source=dict(url="archive",channel="Berkeley",title="Algorithms (DataEDGE Conference 2012)"))
    assert film.archive_event(rec)=="DataEDGE Conference 2012"
    rec["source"].update(title="Intro to Big Data - UC Berkeley AmpLab 2013",upload_date="20171015")
    assert film.archive_event(rec)=="UC Berkeley AmpLab 2013"
    rec["source"]["title"]="Intro to Big Data"
    assert film.archive_event(rec)==""

    rec["source"]["title"]="Transit Across Africa - September 2017"
    assert film.archive_event(rec)==""


def test_full_page_year_cards_and_archival_footage_own_distinct_frames(tmp_path,quiet_engines,monkeypatch):
    import placement_check,wf1_coverage
    sup=_sup(tmp_path,cuts_two_shot())
    rec=dict(path="/archive.mp4",w=1920,h=1080,seconds=2.,src=[0.,47/30],audit_until=100)
    rec["provider"]="primary-document"
    sup["archived_placement_plan"]=dict(run_id="copy",rows=[[53,100,"clip","Lab",rec]],
                                      report=dict(contract=placement_check.CONTRACT,rejected=[],placements=[]))
    pages=dict(dev="type_run",words=["2009","2010"],f=[72,115])
    monkeypatch.setattr(film._director(),"palette_asks",lambda *a:([pages],[]))
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *a:([],[]))
    script=dict(title="Interview",segments=[dict(say=" ".join(w["w"] for w in sup["words"]["words"] if w["seg"]==i),pictures=[]) for i in (0,1)])
    scene,beats=film.build(script,sup,"footage-cut",dict(coverage_final=True,director=dict(by="director")))
    assert pages in scene["devices"]
    shot=beats["pictures"][0]
    assert shot["f"]==shot["visible_f"]==[53,72] and shot["source_slice"]==pytest.approx([0.,19/30],abs=1e-4)
    window = next(it for it in scene["items"] if it.get("source_ink") and it["f"] == [53,72])
    assert window["crop"] == [0,0,1,1]
    assert any(it["k"] == "shot" and it["pic"] == "plate" and it["f"] == window["f"] for it in scene["items"])


def test_source_chart_precedes_face_fit_and_type(tmp_path, quiet_engines):
    from PIL import Image
    path = str(tmp_path / "screen.png")
    Image.new("RGB", (1920,1080)).save(path)
    pack = _sup(tmp_path, cuts_two_shot())
    words = pack["coverage"]["words"]
    source = dict(path=path,t=1.5,shot=[0,4],w=1920,h=1080,box=[.30,.19,.79,.66],
                  tile=[.79,.21,1,.43],first=3,last=8,shows="Pull requests per week by rank")
    pack["coverage"]["source_reading"] = dict(inserts=[source],quote_segments=[])
    script = dict(source_credit="Interview",source_start=100,source_url="https://example.org/interview",
                  segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==i),pictures=[]) for i in (0,1)])
    director = dict(asks=[dict(dev="diecut",at=dict(w=4,seg=0,word=words[4]["w"]),args={})])
    scene, beats = film.build(script,pack,"window",dict(coverage_final=True,director=director))
    evidence, = [p for p in beats["pictures"] if p.get("mode")=="source-insert"]
    card, = [i for i in scene["items"] if i.get("pic")==evidence["pic"] and i["k"]=="card"]
    assert card["crop"]==source["box"] and card["f"]==evidence["f"]
    assert evidence["source_evidence"]["time_s"]==101.5
    assert abs(card["w"]*1080/(card["h"]*1920) - .49*1920/(.47*1080)) < .002
    assert any(i.get("src") and i["f"]==card["f"] for i in scene["items"] if i["k"]=="card")
    assert not any(d["dev"]=="diecut" for d in scene.get("devices",[]))
    assert any(i["k"]=="shot" and i["f"][0]>=card["f"][1] and i["pic"]!="plate" for i in scene["items"])


def test_same_source_frame_and_words_reuse_the_joint_reading(tmp_path):
    import json
    from types import SimpleNamespace
    from PIL import Image
    import wf1_coverage

    path = tmp_path / "original.png"
    Image.new("RGB",(160,90)).save(path)
    frames = [dict(path=str(path),t=1,shot=[0,2])]
    words = [dict(w="Here",seg=0,start=.1,end=.3),dict(w="look",seg=0,start=.3,end=1)]
    calls = []
    def ask(system,content,**kwargs):
        assert content[-1]["type"]=="image"
        calls.append(1)
        return json.dumps(dict(entities=[],windows=[],moves=[],quote_segments=[],
                          source_inserts=[dict(first=0,last=1,frame=0,box=[.1,.2,.7,.8],shows="source chart")])),{}
    llm = SimpleNamespace(ask=ask,json_block=json.loads)
    for name in ("original.png","copied.png"):
        copied = tmp_path / name
        copied.write_bytes(path.read_bytes())
        frames[0]["path"] = str(copied)
        plan = wf1_coverage.plan(words,"Chart",str(tmp_path),llm,frames=frames)
        assert plan["source_reading"]["inserts"][0]["path"]==str(copied)
        assert plan["windows"]==[] and plan["source_reading"]["quote_segments"]==[]
    assert len(calls)==1

def test_explanatory_closeup_stays_inside_the_verified_live_tile():
    from wf1_source_frame import presenter_crop
    share = dict(tile=[.79,.21,1,.43],t=1.5,shot=[0,8],w=1920,h=1080)
    host = [dict(box=[.87,.25,.93,.39])]
    tile,(l,t,r,b) = presenter_crop([share],host,[2,6],1080/(1920*.76))
    assert .79 <= l <= .87 < .93 <= r <= 1 and (t,b)==(.21,.43)
    assert presenter_crop([share],host,[9,12],.75) is None
    assert presenter_crop([share],[dict(box=[.12,.15,.31,.60])],[2,6],.75) is None


def test_authored_interview_layout_preserves_source_corners(tmp_path, quiet_engines, monkeypatch):
    import wf1_coverage
    import wf1_source_frame
    cuts = cuts_two_shot()
    pack = _sup(tmp_path, cuts)
    reading = wf1_source_frame.bind(pack["coverage"], [dict(t=2.,shot=[0,4])], dict(authored_frames=[0]))
    pack["coverage"]["source_reading"] = reading
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    script = dict(title="Report", source_start=100., source_credit="Interview", source_url="interview-url",
                  segments=[dict(say=" ".join(w["w"] for w in pack["words"]["words"] if w["seg"]==i),pictures=[]) for i in (0,1)])
    scene, beats = film.build(script, pack, "archive-reel", dict(coverage_final=True))
    cards = [it for it in scene["items"] if it.get("source_frame")]
    assert cards and all(it["crop"] == [0,0,1,1] and it["w"]*1080/(it["h"]*1920)==pytest.approx(16/9,abs=.001)
                         for it in cards)
    assert sum(it["f"][1]-it["f"][0] for it in cards) == 4*30 + film.LEAD_F
    assert all(r["url"]=="interview-url" for r in beats["coverage"]["authored_source"])
    assert any(it.get("pic","").startswith("own:") and it["k"]=="shot" for it in scene["items"])


def test_m09_comparison_is_one_speaker_graphic_and_the_archive_keeps_its_words(tmp_path, quiet_engines):
    import json
    from types import SimpleNamespace
    import wf1_coverage as coverage
    import wf1_display

    words = "Imagine another 500 horsepower in your Ford Explorer it could rip the chassis off. Databricks started doing dashboards.".split()
    words = [dict(w=w, start=.2+i*.37, end=.53+i*.37, seg=int(i>=14)) for i,w in enumerate(words)]
    script = dict(title="500 Extra Horsepower", idea=dict(why="Imagined horsepower illustrates unused capacity."),
                  direction="Land on Databricks dashboards.", segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==i),
                                                                            pictures=[]) for i in (0,1)])
    compare = dict(first=0,last=13,premise="More power?",consequence="Same chassis")
    named = [dict(name=name,must_show=brief) for name,brief in
             [("Ford Explorer","An SUV"),("Databricks","Original dashboard demonstration")]]
    def ask(prompt, content, **kw):
        assert json.loads(content)["story"]["meaning"] == script["idea"]["why"]
        return json.dumps(dict(comparisons=[compare], entities=[dict(name="Ford Explorer",word_ids=[6,7])],
                               windows=[dict(first=a,last=b,external=True,**n) for (a,b),n in zip([(0,13),(14,17)],named)])), {}
    pack = _sup(tmp_path, cuts_two_shot())
    pack["words"] = dict(words=words, duration=7.)
    plan = coverage.plan(words,script["title"],str(tmp_path),SimpleNamespace(ask=ask,json_block=json.loads),
                         named=named,story=coverage.story_context(script))
    pack["coverage"] = plan
    assert [(w["name"],w["first"],w["last"]) for w in plan["windows"]] == [("Databricks",14,17)]
    assert [r["name"] for r in coverage.research_names(plan)] == ["Databricks"]
    assert wf1_display.editorial(SimpleNamespace(args={}),script,pack)["comparisons"] == [compare]
    # No new search on a missing archive while exercising the ordinary score composer.
    scene, beats = film.build(script,pack,"footage-cut",dict(coverage_final=True))
    graphic, = beats["display_facts"]
    assert graphic["analogy"] and graphic["last"] == 13
    assert {"MORE POWER?", "SAME CHASSIS"}.issubset({i.get("t","").upper() for i in scene["items"]})
    assert coverage.outside_comparisons(plan["windows"][0],0,17,plan) == (14,17)


def test_adjacent_survey_rows_reuse_clean_panel_then_show_live_speaker(tmp_path, quiet_engines, monkeypatch):
    from PIL import Image
    import wf1_coverage
    import wf1_source_frame as source

    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    pack = _sup(tmp_path, [])
    words = pack["coverage"]["words"]
    for w,text in zip(words, "FY27 culture survey Work-Life Blend 60% Action is 66% Each year I promise it could be 30% instead".split(), strict=True):
        w["w"] = text
    path = str(tmp_path / "survey.png")
    Image.new("RGB", (1920, 1080)).save(path)
    frames = [dict(path=path, t=2., shot=[0., 7.])]
    overview = [.45, .30, .86, .75]
    blend, action = [.50, .64, .85, .74], [.49, .60, .85, .73]
    tile = [.79, .21, 1., .43]
    result = dict(source_panels=[dict(frame=0, box=overview)], presenter_tiles=[dict(frame=0, box=tile)],
                  source_inserts=[dict(first=0,last=2,panel=0,speaker=0,shows="FY27 favorability"),
                                  dict(first=3,last=5,panel=0,box=blend,speaker=0,shows="Work & Life Blend 60%"),
                                  dict(first=6,last=8,panel=0,box=action,speaker=0,shows="Action 66%")],
                  speaker_inserts=[dict(first=9,last=17,speaker=0)], authored_frames=[0])
    pack["coverage"]["source_reading"] = source.bind(pack["coverage"], frames, result)
    script = dict(source_credit="Interview", source_start=100, source_url="interview-url",
                  segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==i),pictures=[]) for i in (0,1)])
    claim = dict(speaker="Guest", report="The promise recurs")
    pages = [dict(first=10,last=12,facts=[dict(value=claim["report"],chain=claim,source={})]),
             dict(first=14,last=17,facts=[dict(value="Possibly lower",chain=dict(claim,stance="personal_estimate"),
                                               attribution="Guest",source={})])]
    scene, beats = film.build(script, pack, "window", dict(coverage_final=True, display_facts=pages))
    rows = [p for p in beats["pictures"] if p["mode"]=="source-insert"]
    assert [r["source_crop"] for r in rows] == [overview, blend, action]
    assert [r["clause"] for r in rows] == ["FY27 culture survey", "Work-Life Blend 60%", "Action is 66%"]
    assert all(a["f"][1]==b["f"][0] for a,b in zip(rows,rows[1:]))
    assert len({r["pic"] for r in rows})==1
    for row in rows:
        assert row["source_evidence"]["time_s"]==102
        assert any(it.get("src") and it["f"]==row["f"] for it in scene["items"])
    assert any(it.get("presenter_insert") and it["crop"]==tile for it in scene["items"])
    assert [p["facts"][0]["value"] for p in beats["display_facts"]] == ["Possibly lower"]
    assert source.shared_presenter_region(pack["coverage"]["source_reading"]) == tile
    repaired, report = film.build(script, pack, "window", dict(coverage_final=True,
        reframe=[dict(window=rows[1]["source_slot"], path=path)]))
    assert [p["source_crop"] for p in report["pictures"]] == [overview, overview, action]
    assert [p["reframed"] for p in report["pictures"]] == [False, True, False]
    assert repaired["dur_f"] == scene["dur_f"]
    late = dict(frames[0],shot=[4.,7.])
    unrelated = source.bind(pack["coverage"], [late], result)
    assert unrelated["inserts"] == []
    # The same coverage call owns acquisition. Leave the first subsequent external
    # subject in research when an external window overlaps its locally served lead-in.
    import json
    from types import SimpleNamespace
    answer = dict(result, speaker_inserts=[], windows=[dict(first=5, last=12, name="Survey follow-up",
                  external=True, must_show="The follow-up event")])
    llm = SimpleNamespace(ask=lambda *a,**k:(json.dumps(answer),{}), json_block=json.loads)
    plan = wf1_coverage.plan(words, "Survey", str(tmp_path), llm, frames=frames)
    assert [(w["first"],w["last"]) for w in plan["windows"]] == [(9,12)]
