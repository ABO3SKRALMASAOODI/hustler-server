"""An interview introduces a project, then contrasts two hypothetical cases on one camera."""
import json
from types import SimpleNamespace

import pytest
import wf1_film as film
from test_wf1_take import _sup, quiet_engines


@pytest.fixture
def composed(tmp_path, quiet_engines, monkeypatch):
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    face = dict(box=[.30, .12, .60, .62], eyes=[[.37, .27], [.50, .27]], score=1)
    pack = _sup(tmp_path, [dict(t0=0., t1=7., faces=[face])])
    words = pack["words"]["words"]
    for i, word in enumerate(words):
        word["seg"] = 0 if i < 4 else 1 if i < 8 else 2 if i < 13 else 3
    texts = ["We", "trained", "it", "together.", "Here", "is", "the", "question.",
             "This", "could", "drive", "the", "change.", "This", "one", "could", "follow", "it."]
    for word, text in zip(words, texts):
        word["w"] = text
    source = "https://example.com/interview"
    project = dict(stance="research_project", start=0, end=words[3]["end"], speaker="Guest",
                   report="Two teams trained a research model.", stage="Early discovery",
                   example_start=words[8]["start"], example_end=words[-1]["end"])
    comparison = dict(first=8, last=17, premise="Possible cause", consequence="It might be downstream", reveal=16,
                      steps=[dict(at=9, label="Possible cause", gesture=True, box=[.12,.08,.86,.99]),
                             dict(at=16, label="Possible response", gesture=True, box=[.19,.06,.95,.97])])
    answer = dict(comparisons=[comparison], windows=[
        dict(first=1,last=3,name="Other project",must_show="Other research",external=True)])
    model = SimpleNamespace(ask=lambda *a, **kw: (json.dumps(answer), {}), json_block=json.loads)
    pack["coverage"] = wf1_coverage.plan(words, "Early discovery", tmp_path, model,
                                         story=dict(claims=[project], episode_start=0))
    # Supply must carry the original writer's response label into composition.

    script = dict(title="Early discovery", source_url=source, source_credit="Interview",
                  segments=[dict(say=" ".join(w["w"] for w in words if w["seg"]==s)) for s in range(4)])
    pages = [dict(first=0,last=1,facts=[dict(value="An earlier project", source={"url":"other"})]),
             dict(first=2,last=3,facts=[dict(value="Spoken acronym", source={"url":"keynote"})])]
    scene, beats = film.build(script, pack, "archive-reel",
                              dict(coverage_final=True, display_facts=pages, caption_ends=[3,7,12,17]))
    return scene, beats, words


def visible(scene, frame):
    return [i for i in scene["items"] if i["f"][0] <= frame < i["f"][1]]


def test_opening_uses_the_project_speakers_recording(composed):
    scene, beats, words = composed
    opening = [page for page in beats["display_facts"] if page["f"][0] == 0]
    fact, = opening[0]["facts"]
    assert fact["source"]["url"] == "https://example.com/interview"
    for frame in (0, round(words[2]["start"]*30)+3):
        items = visible(scene, frame)
        ink = " ".join(i["t"] for i in items if i.get("role")=="graphic")
        assert "Guest" in ink and "Two teams trained" in ink
        pictures = [scene["assets"][i["pic"]]["path"] for i in items
                    if i["k"]=="card" and i.get("pic") not in (None,"plate")]
        assert pictures == ["/take.mp4"]


def test_visible_cases_switch_on_the_second_clause(composed):
    scene, _, words = composed
    switch = round(words[13]["start"]*30)+3
    for frame, case, other in [(switch-1,"Possible cause","Possible response"),
                               (switch,"Possible response","Possible cause"),
                               (round(words[-1]["start"]*30)+3,"Possible response","Possible cause")]:
        ink = " ".join(i["t"] for i in visible(scene,frame) if i.get("role")=="graphic")
        assert case in ink and other not in ink
        assert "Early discovery" not in ink                  # the research stage stays in the record


def test_the_setup_and_both_gestures_share_one_contained_view(composed):
    scene, _, words = composed
    seats = set()
    for index in (5, 9, 13, 16):
        frame = round(words[index]["start"]*30)+3
        picture, = [i for i in visible(scene,frame) if i["k"]=="card" and i.get("pic")!="plate"]
        left, top, right, bottom = picture["crop"]
        assert left <= .12 and top <= .06 and right >= .95 and bottom >= .99
        assert picture["s"] == 1
        seats.add((tuple(picture["crop"]),picture["x"],picture["y"],picture["w"],picture["h"]))
    assert len(seats) == 1
