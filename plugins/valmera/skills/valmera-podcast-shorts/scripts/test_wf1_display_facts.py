"""The Netflix Prize display score, including the supers the recorded m03 proposed."""
import json
from types import SimpleNamespace

import wf1_short as wf
import wf1_coverage
from test_wf1_take import quiet_engines  # noqa: F401


PAPER = "https://www.cs.uic.edu/~liub/KDD-cup-2007/proceedings/The-Netflix-Prize-Bennett.pdf"
NETFLIX = "https://netflixtechblog.com/netflix-recommendations-beyond-the-5-stars-part-1-55838468f429"
UCSD = "https://jacobsschool.ucsd.edu/news/release/888?id=888"
INTERVIEW = "https://www.youtube.com/watch?v=1bR0gtecoHU"


def material(tmp_path):
    clauses = [
        (0, "Netflix started uh obviously in the Bay Area as DVDs that were mailed to you in an envelope and then you had this queue."),
        (6.1, "And it was critically important uh for people who don't know the Netflix prize."),
        (9.5, "This was in the 2008 2009 time period."),
        (14.5, "They put a million dollar prize out to any computer science team that can make the algorithm I think 10% better at predicting what movie you would give four or five stars to."),
        (25, "So they just took all their data, they released it, and then they got"),
        (31, "probably $50 million worth of work from computer scientists around the world."),
    ]
    words, lines = [], []
    for i, (start, clause) in enumerate(clauses):
        tokens = clause.split()
        end = clauses[i+1][0] if i+1 < len(clauses) else 38.14
        step = (end-start)/len(tokens)
        words.extend(dict(seg=i, w=w, start=start+j*step, end=start+(j+1)*step) for j,w in enumerate(tokens))
        lines.append(dict(i=i, text=clause))
    sources = [dict(url=PAPER, publisher="Bennett and Lanning", supports="Netflix released the dataset and launched the challenge in October 2006."),
               dict(url=NETFLIX, publisher="Netflix TechBlog", supports="Netflix announced the competition in 2006 and offered a $1 million grand prize."),
               dict(url=UCSD, publisher="UC San Diego", supports="Netflix awarded the $1 million grand prize in September 2009.")]
    pack = dict(words=dict(words=words, duration=38.14), lines=lines, ffmpeg="ffmpeg", work=str(tmp_path), pictures={}, footage=[],
                coverage=dict(contract=wf1_coverage.CONTRACT, windows=[], moves=[], words=words), take=dict(path="/take.mp4",dur=38.14,w=1920,h=1080,fps=30,faces=[]),
                referent_research=dict(references=[dict(referent="Netflix Prize", sources=sources)]))
    script = dict(title="$50 Million Worth of Work", direction="Calacanis estimates the value of the outside work.",
                  style=dict(id="window"), close_line=clauses[-1][1], segments=[dict(say=x["text"],pictures=[]) for x in lines])
    ctx = SimpleNamespace(supply=str(tmp_path), args=dict(source_url=INTERVIEW, channel="All-In Podcast", moment=dict(names=["Jason Calacanis"],why=script["direction"])))
    return ctx, script, pack


def writing(pack):
    words = pack["words"]["words"]
    starts = [next(i for i,w in enumerate(words) if w["seg"]==s) for s in range(6)]
    def ask(dev, first, last, args, facts):
        return dict(dev=dev, at=first, until=last, args=dict(args,facts=facts))
    date = [dict(source=PAPER, relation="Prize launched", value="October 2006"),
            dict(source=UCSD, relation="Prize awarded", value="September 2009")]
    prize = [dict(source=NETFLIX, relation="Grand prize", value="$1 million")]
    estimate = [dict(source=INTERVIEW, attribution="Jason Calacanis", qualifier="probably", value="$50 million", relation="worth of work")]
    return dict(asks=[ask("typewriter", starts[2], starts[3]-1, dict(t="2008–2009"), date),
                      ask("counter", starts[3], starts[3]+5, {"from":0,"to":1000000,"fmt":"${n}"}, prize),
                      ask("staircase", starts[5], starts[5]+5, dict(lines=["50","MILLION","DOLLARS"]), estimate)],
                display=[dict(seg=5,text="$50 million",role="payoff")],
                arc=[dict(seg=2,word="2008",year=2008)])


def test_netflix_prize_display_on_the_spoken_clock(tmp_path, monkeypatch, quiet_engines):
    ctx,script,pack = material(tmp_path)
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *a: ([], []))
    messages = []
    def write(system, content, **kwargs):
        messages.append(content)
        return json.dumps(writing(pack)), {}
    monkeypatch.setattr(wf, "_llm", lambda: SimpleNamespace(ask=write))
    d = wf.direction_of(ctx,script,pack)
    l1,b = wf.wf1_film.build(script,pack,"window",wf.film_direction(d))
    drawn = [w for w in l1["items"] if w.get("role")=="big"]
    expected_pages = [
        ["Prize launched", "October 2006", "Prize awarded", "September 2009", "Bennett and Lanning · UC San Diego"],
        ["Grand prize", "$1 million", "Netflix TechBlog"],
        ["Jason Calacanis", "probably $50 million", "worth of work", "All-In Podcast"],
    ]
    assert len(b.get("display_facts", [])) == len(expected_pages)
    for span, expected in zip(b["display_facts"], expected_pages):
        for frame in range(*span["f"]):
            visible = [w["t"].casefold() for w in drawn if w["f"][0] <= frame < w["f"][1]]
            assert visible == [v.casefold() for v in expected]
    assert all(a["dev"] not in ("counter","typewriter","staircase","arc") for a in l1.get("devices",[]))
    assert [s["source"]["url"] for f in b["display_facts"] for s in f["facts"]] == [PAPER,UCSD,NETFLIX,INTERVIEW]
    caps = [i["t"] for i in l1["items"] if i.get("role")=="cap"]
    assert caps == [i["t"] for i in wf.wf1_film.build(script,pack,"window",{})[0]["items"] if i.get("role")=="cap"]
    assert all(v in str(messages[0]) for v in (PAPER,NETFLIX,UCSD,INTERVIEW,"Jason Calacanis","September 2009"))


def test_counter_uses_the_sourced_change_and_keeps_a_fixed_quote_whole(tmp_path):
    import wf1_display
    from scene.palette import type as type_devices
    ctx, script, pack = material(tmp_path)
    evidence = wf1_display.editorial(ctx, script, pack)
    evidence["sources"]["interview"]["supports"] = ["The team grew from 3 to 12 members."]
    words = [dict(w=w, seg=0) for w in "The team grew from 3 to 12 members".split()]
    ask = dict(dev="counter", at=dict(w=3,seg=0), until=dict(w=7,seg=0), args={"from":0,"to":12})
    d = dict(arc=[],asks=[ask])
    fixed = wf1_display.bind(d, evidence, words)
    assert not fixed["asks"] and fixed["display_facts"][0]["facts"][0]["value"] == "from 3 to 12 members"
    ask["args"]["facts"] = [dict(source="interview",relation="Team members",value="12",
                                 progression={"from":3,"to":12})]
    bound = wf1_display.bind(d, evidence, words)["asks"][0]
    scene = dict(frame=[1080,1920], fps=30, dur_f=120, items=[], assets={}, voices={})
    planned = type_devices.counter(scene,str(tmp_path),dict(bound["args"],id="team",at=[20,90]))
    assert planned["log"]["from"] == "3" and planned["log"]["to"] == "12" and planned["log"]["steps"] > 2
