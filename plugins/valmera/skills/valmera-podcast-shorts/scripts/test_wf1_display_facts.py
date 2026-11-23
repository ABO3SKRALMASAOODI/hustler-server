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


def test_reported_forecast_and_verified_original(tmp_path, monkeypatch):
    import net_fetch
    import wf1_context
    from pathlib import Path
    import wf1_display as display
    ctx, script, pack = material(tmp_path)
    original = "https://example.org/roadshow"
    claim = dict(start=3264.34, end=3270.5, speaker="Jason Calacanis", subject="Dario Amodei",
                 event="IPO roadshow", report="Amodei thought cures might come within a decade")
    script.update(window=[3264.34, 3292.66], claims=[claim])
    src = dict(url=original, publisher="Anthropic", supports="The IPO site hosted the presentation.")
    pack["referent_research"] = dict(references=[dict(sources=[src])])
    words = [dict(w=w, seg=0, t0=i*.25, t1=(i+1)*.25)
             for i,w in enumerate("Dario during the roadshow is saying cancer and Alzheimer's I believe will be gone in under 10 years".split())]
    fact = dict(source=original, attribution="Dario Amodei", qualifier="I think", relation="Cancer and Alzheimer’s gone",
                value="under 10 years")
    direction = dict(arc=[], asks=[dict(dev="staircase", at=dict(w=6), until=dict(w=len(words)-1),
                                       args=dict(facts=[fact]))])
    def page():
        material = display.editorial(ctx, script, pack)
        bound, = display.bind(direction, material, words)["display_facts"]
        item, = bound["facts"]
        return item, [line for line,_ in display.lines([item])]
    report, lines = page()
    assert lines[:2] == ["Jason Calacanis reports", claim["report"]]
    assert report["source"]["url"] == INTERVIEW and report["chain"]["event"] == "IPO roadshow"
    # Synthetic primary transcript fixture, including the speaker's uncertainty.
    quote = "I think the trial could finish in five years."
    src["quotations"] = [dict(words=quote, speaker="Dario Amodei", event="IPO roadshow", locator="40:38")]
    def transcript(url, dest, **kw):
        Path(dest).write_text("<article>Dario Amodei · IPO roadshow · 40:38. "+quote+"</article>")
        return Path(dest).stat().st_size, url
    monkeypatch.setattr(net_fetch, "download", transcript)
    wf1_context.read_quotations(pack["referent_research"]["references"], str(tmp_path))
    fact["quote"] = quote
    item, lines = page()
    assert item["source"]["url"] == original and item["chain"]["speaker"] == "Jason Calacanis"
    assert "“"+quote+"”" in lines and "Dario Amodei" in lines and any("40:38" in s and "IPO roadshow" in s for s in lines)
    fact["quote"] = "The trial will finish in five years."
    assert page()[1][0] == "Jason Calacanis reports"
    # The launch URL now lands on an investor page with no wording from the event.
    def redirected(url, dest, **kw):
        Path(dest).write_text("<article>Investor relations</article>")
        return 35, "https://example.org/investors"
    monkeypatch.setattr(net_fetch, "download", redirected)
    wf1_context.read_quotations(pack["referent_research"]["references"], str(tmp_path))
    fact["quote"] = quote
    assert page()[1][0] == "Jason Calacanis reports"

    # Calacanis's actual closing words in the same source can be quoted directly.
    closing = "I actually think he does."
    heard = [dict(w=w, seg=6, t0=26+i*.2, t1=26.2+i*.2) for i,w in enumerate(closing.split())]
    script["claims"] = [dict(start=3290., end=3292., speaker="Jason Calacanis", subject="Jason Calacanis",
                             event="All-In Podcast", report=closing)]
    fact.update(quote=closing, source="interview")
    ask = dict(dev="typewriter", at=dict(w=0), until=dict(w=4), args=dict(facts=[fact]))
    bound = display.bind(dict(arc=[], asks=[ask]), display.editorial(ctx,script,pack), heard)
    cited, = bound["display_facts"][0]["facts"]
    assert cited["value"] == "“"+closing+"”" and cited["attribution"] == "Jason Calacanis"
    assert cited["source"]["url"] == INTERVIEW and cited["relation"] == "54:50"


def test_estimate_cards_own_a_phone_reading_interval(tmp_path, monkeypatch, quiet_engines):
    import wf1_display
    from wf1_layout import _bounds, intersects

    ctx, script, pack = material(tmp_path)
    passages = ["The idea that there's a 10% chance that 7 billion people die because of AI seems so farcical and out there",
                "when you pick a number it makes you seem credible", "But for me that risk is close to zero"]
    words, timed = [], [(0, 6), (6, 10), (10, 13)]
    for seg, (line, (a, b)) in enumerate(zip(passages, timed)):
        tokens = line.split()
        for i, token in enumerate(tokens):
            words.append(dict(seg=seg,w=token,start=a+i*(b-a)/len(tokens),end=a+(i+1)*(b-a)/len(tokens)))
    script.update(segments=[dict(say=t,pictures=[]) for t in passages], source_url=INTERVIEW,source_start=0)
    pack["words"] = dict(words=words,duration=13.)
    pack["coverage"]["words"] = words
    pack["take"]["dur"] = 13.
    direction = dict(claims=[], display_facts=[])
    for seg, speaker, stance in [(0,"Jason Calacanis","discussed_estimate"),(2,"Ali Ghodsi","personal_estimate")]:
        ids=[i for i,w in enumerate(words) if w["seg"]==seg]
        claim=dict(first=ids[0],last=ids[-1],start=timed[seg][0],speaker=speaker,subject=speaker,
                   report=passages[seg],stance=stance)
        fact=wf1_display.claim_fact(claim,dict(quote=passages[seg]),{"interview":dict(url=INTERVIEW,publisher="All-In Podcast")},words)
        direction["display_facts"].append(dict(first=ids[0],last=ids[-1],facts=[fact]))
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *a:([],[]))
    scene,beats=wf.wf1_film.build(script,pack,"prints",direction)
    ruler=wf.wf1_film.F.Ruler(scene["voices"])
    caps=[it for it in scene["items"] if it.get("role")=="cap"]
    assert [it["t"].lower() for it in caps] == passages[1].lower().split()
    for page, speaker in zip(beats["display_facts"], ["Jason Calacanis","Ali Ghodsi"]):
        a,b=page["f"]
        lettering=[it for it in scene["items"] if it.get("fact_type") and a<=it["f"][0]<b]
        assert page["facts"][0]["source"]["url"]==INTERVIEW
        copy=" ".join(it["t"] for it in lettering).casefold()
        assert speaker.lower() in copy and 'estimate' in copy and 'all-in podcast' in copy
        assert min(scene["voices"][it["v"]]["px"]/3 for it in lettering)>=16
        for f in range(a,b):
            visible=[it for it in lettering if it["f"][0]<=f<it["f"][1]]
            boxes=[box for it in visible for _,box,_ in _bounds(scene,[dict(it,f=[f,f+1])],ruler)]
            assert boxes and all(.05<=x0<x1<=.86 and .46<=y0<y1<=.81 for x0,y0,x1,y1 in boxes)
            assert not any(intersects(box,other,pad=0) for i,box in enumerate(boxes) for other in boxes[i+1:])
        body=" ".join(it["t"] for it in lettering if it.get("ink")==wf.wf1_film.STYLES["prints"]["ink"])
        assert body.strip("“”") == page["facts"][0]["value"].strip("“”")


def test_retained_multi_fact_estimate_pages_keep_the_qualifications(tmp_path, monkeypatch, quiet_engines):
    _, script, pack = material(tmp_path)
    source = dict(url=INTERVIEW, publisher="All-In Podcast")
    base = dict(source=source, attribution="Jason Calacanis")
    facts = [dict(base, qualifier="The idea that there's", value="10% chance", relation="of mass deaths because of AI"),
             dict(base, qualifier="10% chance", value="7 billion people", relation="die in the scenario")]
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    scene, beats = wf.wf1_film.build(script, pack, "prints", dict(display_facts=[dict(first=0, last=15, facts=facts)]))
    start, end = beats["display_facts"][0]["f"]
    rows = [it for it in scene["items"] if it.get("fact_type")]
    assert all(it["f"] != [start, end] for it in rows)
    for frame, fact in ((start, facts[0]), (end-1, facts[1])):
        copy = " ".join(it["t"] for it in rows if it["f"][0] <= frame < it["f"][1])
        assert all(part in copy for part in (fact["qualifier"], fact["value"], fact["attribution"], source["publisher"]))
        assert not any(it.get("role") == "cap" and it["f"][0] <= frame < it["f"][1] for it in scene["items"])
