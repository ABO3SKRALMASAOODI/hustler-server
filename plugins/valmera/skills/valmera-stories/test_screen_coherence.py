"""Programme ownership and timing regressions shared by the three short-film workflows."""
import json
import sys
from datetime import date
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(SKILLS / name) for name in ("valmera-stories", "_shell", "valmera-podcast-shorts/scripts")]

import director
import film
import wf1_display
import wf1_short
import wf3


def test_after_owns_ask_addressed_inside():
    ask = dict(id="date", dev="counter", f=[104, 120])
    performed = []
    assert director.around([ask], [dict(device="after", frames=[100, 160])], 200, performed) == []
    assert [(r["owner"], r["at"], r["word_frame"]) for r in performed] == [("after", 104, 104)]


def test_moment_sheet_identifies_who_and_what_is_printed():
    words = [dict(n=0, w="earned", t0=.4, t1=.7, seg=0)]
    pictures = [dict(f=[0, 30], person="Jane", role="speaker", meaning_box=[.1,.1,.8,.8], identity_year=2001)]
    ink = [dict(k="word", f=[6, 22], t="$17", role="title")]
    sheet = director.composed_words(words, pictures, ink, [], [dict(f=[0,30], person="Jane")], 30)
    text = director.moment_sheet([dict(i=0, words=sheet)], [], [], None, {}, "story", 1, [])
    for value in ('"person": "Jane"', '"role": "speaker"', '"text": "$17"', '"voice": ["Jane"]', '"identity_year": 2001'):
        assert value in text


def test_only_viewer_writing_reaches_the_podcast_composer():
    words = [dict(seg=0), dict(seg=0)]
    rows = [dict(first=0, last=1, text="NELL", byline="Personal estimate", stance="Opinion", source="Research")]
    display = wf1_display.viewer_text(rows, words)
    knobs = wf1_short.film_direction(dict(display=display, display_facts=rows, reporting_example=rows))
    assert knobs["display"] == [dict(first=0, last=1, seg=0, text="NELL")]
    assert "display_facts" not in knobs and "reporting_example" not in knobs


def clock(tmp_path, segments, words):
    path = tmp_path / "words.json"
    path.write_text(json.dumps(dict(words=words, duration=words[-1]["end"])))
    return film.Clock(str(path), dict(segments=segments))


def test_story_turn_and_payoff_address_distinct_lines(tmp_path):
    segments = [dict(say=text) for text in ("Rejected", "Returned", "Won")]
    words = [dict(w=s["say"], seg=i, start=i*3., end=i*3.+.5) for i,s in enumerate(segments)]
    measured = clock(tmp_path, segments, words)
    times = wf3.music_times(dict(beats=dict(setback=0,turn=1,payoff=2)), measured, dict(turn_f=183))
    assert times == dict(setback=.1,turn=3.1,payoff=6.1)


def test_figure_card_spans_the_measured_spoken_form(tmp_path, monkeypatch):
    from types import SimpleNamespace
    tokens = "a hundred and eighty six million".split()
    segments = [dict(say=" ".join(tokens), display="186 MILLION", print={" ".join(tokens):"186 MILLION"})]
    words = [dict(w=w, seg=0, start=5.+i*.25, end=5.15+i*.25) for i,w in enumerate(tokens)]
    measured = clock(tmp_path, segments, words)
    plan = SimpleNamespace(voices={"dsp":dict(px=80)}, R=SimpleNamespace(fit_px=lambda *args:100),
                           word=lambda f,*args,**kwargs:dict(f=f), events=[])
    bake = SimpleNamespace(BOUNCE={"n":1},bounce=lambda n,rest:[0]*n,bounce_blur=lambda n:[0]*n)
    monkeypatch.setattr(film,"_engines",lambda:(None,None,bake))
    spans = film.display_words(plan, measured, dict(segments=segments), "dsp", .5, 300, anchored_only=True)
    assert spans == [(film.Clock.f(5.1),film.Clock.f(6.5),"186 MILLION")]


def sections():
    direction = dict(sections=[dict(frames=[0,13], surface="colour"),
                               dict(frames=[13,90], surface="projector")], past_segs=[0,1])
    pictures = [dict(f=[0,30],line=0,identity_year=1979), dict(f=[30,60],line=0,identity_year=1979),
                dict(f=[60,90],line=1,identity_year=date.today().year)]
    return director.shot_sections(direction, pictures, lambda w:w, None, [dict(era=1980),{}], 90)


def test_era_finish_keeps_contemporary_shot_in_colour():
    assert [(r["frames"],r["surface"]) for r in sections()][-2:] == [([30,60],"projector"),([60,90],"colour")]


def test_section_changes_at_the_cut():
    assert [r["frames"] for r in sections()] == [[0,30],[30,60],[60,90]]

def test_composed_fallback_keeps_recorded_after_whole():
    director.palette_module()
    original = dict(items=[dict(id="raw", k="shot", f=[0,12], src=[0.,.4])])
    laid = dict(items=[dict(id="new", k="shot", f=[0,12], src=[0.,.4]),
                       dict(id="type", k="word", f=[2,10], t="TITLE", op=list(range(8)))])
    scene = director.compose_owned(original, laid, [dict(frames=[5,8],device="after")])
    assert [(it["id"], it["f"]) for it in scene["items"] if it["f"][0] < 8 and 5 < it["f"][1]] == [("raw~5",[5,8])]
    assert [it["op"] for it in scene["items"] if it["k"]=="word"] == [[0,1,2],[6,7]]

def test_printed_form_inside_a_longer_text_field():
    assert director.printed_text("Visit valmera dot I O today", {"valmera dot I O": "VALMERA.IO"}) == "Visit VALMERA.IO today"
