import wf1_coverage as coverage
import wf1_display as display
import wf1_film as film
from test_wf1_take import quiet_engines  # noqa: F401


def test_example_carries_the_claim_outside_its_short_picture_window():
    window = dict(name="Business software", say="Some have been around for a century.",
                  claim="Some firms still operate applications developed decades ago.",
                  must_show="First-party documentation of continued application compatibility",
                  t0=4.8, t1=6.5)
    assert coverage.footage_segment(0, window)["claim"] == window["claim"]
    picture = dict(path="/page.jpg", page="publisher", w=800, h=600)
    row, = coverage.proposals(dict(windows=[window]), {}, {window["name"]: [picture]})
    assert row[4]["claim"] == window["claim"]


def test_qualitative_evidence_needs_no_invented_number():
    words = [dict(w="applications", seg=0)]
    src = dict(url="ibm", publisher="IBM")
    statement = "Example: IBM supports applications refined over decades."
    ask = dict(dev="facts", at=dict(w=0), args=dict(facts=[
        dict(source="ibm", statement=statement, relation="Application compatibility")]))
    out = display.bind(dict(asks=[ask], arc=[]), dict(sources={"interview":{}, "ibm":src}), words)
    fact, = out["display_facts"][0]["facts"]
    assert fact["statement"] == fact["value"] == statement and fact["source"] == src


def test_reaction_credit_and_live_closing_record(tmp_path, quiet_engines, monkeypatch):
    from test_wf1_display_facts import material
    _, script, pack = material(tmp_path)
    monkeypatch.setattr(coverage, "speaker_moves", lambda *a: ([], []))
    words = pack["words"]["words"]
    first = next(i for i,w in enumerate(words) if w["seg"] == 4)
    last = next(i for i,w in enumerate(words) if w["seg"] == 5)
    pack["take"]["cuts"] = [dict(t0=a,t1=b,faces=[],spans=[dict(t0=a,t1=b,who=who)])
                            for a,b,who in [(0,27,"speaker"),(27,31,"listener"),(31,38.14,"speaker")]]
    interview = dict(url="episode",publisher="Show")
    concern = dict(source=interview, attribution="Guest", value="Concern",
                   chain=dict(stance="concern",speaker="Guest"))
    record = dict(source=dict(url="program",publisher="Lab"), statement="Enterprise Applications",
                  value="Enterprise Applications", perspective=dict(speaker="Guest",stance="characterization"))
    pages = [dict(first=first,last=last-1,facts=[concern]),
             dict(first=last,last=len(words)-1,facts=[record])]
    scene, beats = film.build(script, pack, "prints", dict(display_facts=pages))
    reaction = 29*30
    visible = [i for i in scene["items"] if i["f"][0] <= reaction < i["f"][1]]
    # The reaction stays live with its timed words; who is speaking and the claims stay in the record.
    assert not any(i.get("t") in ("Guest · Concern", "Voice · Guest") for i in visible)
    assert any(i.get("role") == "cap" for i in visible)
    assert sorted(p["first"] for p in beats["research_facts"]) == [first, last]
    final = round(words[last]["start"]*30)+3+15
    cameras = {key for key, asset in scene["assets"].items() if asset.get("path") == pack["take"]["path"]}
    assert any(i.get("pic") in cameras and i["f"][0] <= final < i["f"][1] for i in scene["items"])


def test_revised_primary_example_rebinds_retained_score_and_keeps_spoken_claim():
    words = [dict(w=str(i), t0=i, t1=i+1, seg=0) for i in range(8)]
    sources = {k: dict(url=k, publisher=k) for k in ("interview","Ford","IBM")}
    example = dict(source="IBM", statement="Some mainframe applications are refined over decades.")
    mat = dict(sources=sources, picture_windows=[dict(first=2,last=6,example=example)],
               speaker_claims=[dict(first=5,last=7,speaker="Guest",stance="concern",report="The risk grew.")])
    old = dict(dev="facts", at=dict(w=2), until=dict(w=4), args=dict(facts=[
        dict(source="Ford",relation="Centenary",value=2003)]))
    pages = display.bind(dict(asks=[old],arc=[]),mat,words)["display_facts"]
    assert [(p["first"],p["last"]) for p in pages] == [(2,4),(5,7)]
    assert pages[0]["facts"][0]["example"] and pages[0]["facts"][0]["source"] == sources["IBM"]
    assert pages[1]["facts"][0]["chain"]["speaker"] == "Guest"


def test_process_example_enters_with_the_spoken_phrase():
    words = [dict(w=t, t0=i, t1=i+1, seg=0) for i,t in enumerate(
        "processes a lot of compliance a lot of security".split())]
    sequence = [dict(at=0,label="Change approval"),dict(at=4,label="Review evidence"),dict(at=8,label="Review access")]
    ask = dict(dev="staircase",at=dict(w=0),until=dict(w=8),
               args=dict(sequence_kind="process",sequence=sequence))
    score = dict(asks=[ask],arc=[],caption_ends=[0,4,8])
    page, = display.bind(score,dict(sources={"interview":dict(url="episode")}),words)["display_facts"]
    assert [s["at"] for s in page["sequence"]] == [0,1,5]
