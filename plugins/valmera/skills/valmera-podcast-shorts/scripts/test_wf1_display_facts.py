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


def test_comparison_evidence_keeps_the_scope_and_researches_only_the_demo(tmp_path, monkeypatch):
    import wf1_context
    import wf1_display
    import writer

    clauses = ["Their data might train the next model.", "The two partners previewed Claude in Excel.",
               "The demo edits a spreadsheet.", "Basically an Excel killer. They're killing Photoshop."]
    words = [dict(w=w, seg=s) for s, clause in enumerate(clauses) for w in clause.split()]
    for i, w in enumerate(words):
        w.update(start=i * .3, end=(i + 1) * .3)
    def span(s):
        ids = [i for i, w in enumerate(words) if w["seg"] == s]
        return dict(first=ids[0], last=ids[-1])

    opinion = dict(speaker="Jason Calacanis", stance="characterization",
                   report="He sees Anthropic challenging Excel and Photoshop.", **span(3))
    concern = dict(speaker=opinion["speaker"], stance="concern", report="Customer data might train future models.",
                   scope_question="Which commercial inputs and outputs are used by default?", **span(0))
    evidence = [dict(name="Provider policies", first=0, last=span(0)["last"]),
                dict(name="Microsoft partnership", **span(1)),
                dict(name="Claude Sheets", first=span(2)["first"], last=span(3)["last"])]
    answer = dict(speaker_claims=[dict(concern, first=2), opinion], windows=[dict(r, external=True, must_show=r["name"]) for r in evidence])
    prompts = []
    def direct(prompt, content, **kw):
        prompts.append(prompt)
        return json.dumps(answer), {}
    coverage = wf1_coverage.plan(words, "Partners and competitors", tmp_path,
                                 SimpleNamespace(ask=direct, json_block=json.loads),
                                 story=dict(episode_start=500,claims=[dict(speaker=concern["speaker"],subject=concern["speaker"],
                                            start=500,end=500+words[concern["last"]]["end"])]))
    assert [(w["name"], w["say"]) for w in coverage["windows"]] == [
        ("Microsoft partnership", clauses[1]), ("Claude Sheets", clauses[2])]
    assert "speaker_claims" in prompts[0] and "scope_question" in prompts[0]
    assert wf1_coverage.outside_comparisons(coverage["windows"][-1], span(2)["first"], len(words)-1, coverage)[1] == span(2)["last"]
    policies = [dict(url="policy-a",publisher="Anthropic",supports="Commercial data excluded by default; opted-in data may train."),
                dict(url="policy-b",publisher="OpenAI",supports="Business and API data excluded from training by default.")]
    def research(system, content, **kwargs):
        part = json.loads(content)
        assert part["scope_checks"] == [concern]
        assert [s["name"] for s in part["subjects"]] == ["Microsoft partnership", "Claude Sheets"]
        return json.dumps(dict(references=[dict(windows=[], sources=policies)])), {}
    monkeypatch.setattr(writer, "ask_openai", research)
    refs = wf1_context.research(coverage, tmp_path/"research", SimpleNamespace(openai_key=lambda: None, json_block=json.loads))
    editorial = wf1_display.editorial(SimpleNamespace(args=dict(source_url=INTERVIEW)), {},
                                       dict(coverage=coverage, referent_research=refs, footage=[dict(clips=[
                                           dict(source=dict(url="demo",channel="Claude"),shows="Editing spreadsheet cells")])]))
    assert editorial["sources"]["demo"] == dict(url="demo", publisher="Claude", supports=["Editing spreadsheet cells"])
    scope = "Business and API data stay out of training by default."
    fact = dict(source="policy-a", also_sources=["policy-b"], statement=scope)
    direction = dict(arc=[], asks=[dict(dev="typewriter", at=dict(w=1), until=dict(w=2), args=dict(facts=[fact]))])
    bound = wf1_display.bind(direction, editorial, [dict(w, t0=w["start"], t1=w["end"]) for w in words])
    opening, close = bound["display_facts"]
    assert (opening["first"],opening["last"]) == (concern["first"],concern["last"])
    scope_fact, = opening["facts"]
    assert scope_fact["perspective"] == dict(speaker=concern["speaker"], stance="concern", report=concern["report"], interview=INTERVIEW)
    assert scope_fact["statement"] == scope and [s["url"] for s in scope_fact["supporting_sources"]] == ["policy-a","policy-b"]
    assert close["facts"][0]["attribution"] == opinion["speaker"] and close["facts"][0]["value"] == opinion["report"]
    assert close["facts"][0]["source"]["url"] == INTERVIEW
    # The writer can choose a live camera with no type ask. Keep the scoped text from the primary reading.
    refs["references"][0].update(scope_for=0, statement=scope)
    editorial = wf1_display.editorial(SimpleNamespace(args=dict(source_url=INTERVIEW)), {},
                                       dict(coverage=coverage, referent_research=refs))
    retained = wf1_display.bind(dict(asks=[],arc=[]), editorial, [])
    assert retained["display_facts"][0]["facts"] == [scope_fact]
    # A second window for an already illustrated subject can still need its own source.
    by_line = {0:["Claude Sheets"], 1:["Microsoft"], 2:["Microsoft"], 3:["Unseen"]}
    footage = [dict(i=0,clips=[dict(source={"url":"demo"})]),dict(i=1,clips=[dict(source={"url":"ignite"})]),
               dict(i=2,clips=[dict(source={"url":"blank"},dull=True)])]
    assert wf.photo_subjects(footage, by_line) == {"Microsoft","Unseen"}


def test_spoken_verdict_keeps_the_speaker_captions_and_byline(tmp_path, quiet_engines, monkeypatch):
    from test_wf1_take import _sup, cuts_two_shot
    import wf1_display
    pack = _sup(tmp_path, cuts_two_shot())
    words = pack["words"]["words"]
    for w, token in zip(words[12:], "basically an Excel killer killing Photoshop".split()):
        w["w"] = token
    claim = dict(first=12,last=17,speaker="The host",stance="characterization",report="Sees rivals to Excel and Photoshop.")
    pack["coverage"]["speaker_claims"] = [claim]
    script = dict(segments=[dict(say=" ".join(w["w"] for w in words if w["seg"] == i),pictures=[]) for i in (0,1)])
    direction = wf1_display.bind(dict(asks=[],arc=[]),dict(sources={"interview":dict(url=INTERVIEW)},speaker_claims=[claim]), [])
    direction.update(turn_seg=1, coverage_final=True, display=[dict(seg=1,text="Excel killer",role="payoff")])
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *args: ([],[]))
    scene, beats = wf.wf1_film.build(script,pack,"prints",direction)
    credit, = [i for i in scene["items"] if i.get("t") == "The host · Opinion"]
    a,b = credit["f"]
    assert any(i["k"]=="shot" and i["f"][0]<b and a<i["f"][1] for i in scene["items"])
    assert any(i.get("role")=="cap" and i["f"][0]<b and a<i["f"][1] for i in scene["items"])
    assert not any(row["text"] == "Excel killer" for row in beats["display"])


def test_public_listing_phrase_ownership_and_interview_diagram():
    import wf1_display
    tokens = "We will go public. We are going to be public. Too much up and down. Stock is down then stock is up.".split()
    words = [dict(w=t, t0=i/4, t1=(i+1)/4, seg=0) for i,t in enumerate(tokens)]
    direction = dict(arc=[], caption_ends=[3,9,14,17,21], display=[dict(seg=0,text="will go public.")], asks=[
        dict(dev="staircase",at={"w":12},until={"w":14},args={"lines":["UP","DOWN."]}),
        dict(dev="staircase",at={"w":15},until={"w":21},args={"sequence":[
            dict(at=15,label="Price falls"),dict(at=19,label="Price rises")], "speaker":"Ali Ghodsi"})])
    material_ = dict(sources={"interview":{"url":INTERVIEW,"publisher":"All-In Podcast"}}, speakers=["Ali Ghodsi"])
    result = wf1_display.bind(direction, material_, words)
    assert result["asks"] == [] and result["display"] == []
    assert set(result["caption_accents"]) >= {1,12}
    page, = result["display_facts"]
    assert page["sequence"] == direction["asks"][1]["args"]["sequence"]
    assert page["facts"][0]["attribution"] == "Ali Ghodsi"
    assert page["facts"][0]["source"]["url"] == INTERVIEW
    clock = wf.F.Clock(dict(words=[dict(w=w["w"],start=w["t0"],end=w["t1"]) for w in words],
                            duration=words[-1]["t1"]), {"segments":[{}]})
    plan = wf.F.Plan({"cap":dict(font="InterDisplay-Bold.ttf",px=58,track=0)}, {})
    wf.F.caption_build(plan, clock, "cap", .68, phrase_ends=result["caption_ends"],
                       accents=result["caption_accents"], accent_ink="#7FE3FF")
    expected = ["We will go public.", "We are going to be public.", "Too much up and down.",
                "Stock is down", "then stock is up."]
    assert [it["t"] for it in plan.items] == expected
    for i,it in enumerate(plan.items):
        for frame in range(*it["f"]):
            assert [w["t"] for w in plan.items if w["f"][0] <= frame < w["f"][1]] == [expected[i]]
        if i:
            assert plan.items[i-1]["f"][1] == it["f"][0]


def test_interview_context_completes_a_spoken_list_promise():
    import wf1_display
    from film import LEAD_F, FPS
    words = [dict(w=w, t0=i+LEAD_F/FPS, t1=i+.9+LEAD_F/FPS, seg=0)
             for i,w in enumerate("RSI needs four conditions the next model trains faster".split())]
    chain = dict(speaker="Guest",subject="Guest")
    claims = [dict(chain,start=10,end=12,stance="definition",report="Recursive self-improvement"),
              dict(chain,start=12,end=19,stance="framework",report="First of four: train the next model faster")]
    edit = dict(episode_start=10, claims=claims, sources={"interview":{"url":"episode","publisher":"Interview"}})
    score = dict(asks=[],display=[],arc=[],caption_ends=[1,8])
    pages = wf1_display.bind(score,edit,words)["display_facts"]
    assert [(p["first"],p["last"],p["facts"][0]["value"]) for p in pages] == [
        (0,1,claims[0]["report"]), (2,8,claims[1]["report"])]


def test_dense_scenario_keeps_each_active_thought_with_its_credit():
    import wf1_display
    from scene.palette.kit import item_box
    from wf1_layout import intersects

    labels = ["Hypothetical holder", "Sell shares → cash now", "Keep shares → retain growth exposure",
              "What if 1.7³ or 1.7⁴?", "Holder rethinks how much to sell"]
    palette = {"accent": "#FFCC44", "ink": "#FFFFFF"}
    for height in (.303, .20):
        for active, label in enumerate(labels):
            voice = dict(font="InterDisplay-Bold.ttf", px=58, track=0)
            plan = wf.F.Plan({"cap": voice, "dsp": voice}, {})
            wf1_display.reading_card(plan, [0, 30], "", "Ali Ghodsi · Scenario", "All-In Podcast",
                                     palette, centre=.605, max_height=height,
                                     steps=[(t, i == active) for i, t in enumerate(labels)])
            for f in range(30):
                visible = [it for it in plan.items if it["f"][0] <= f < it["f"][1]]
                lines = [it["t"] for it in visible if it["v"] == "sequence_label" and it["ink"] == palette["accent"]]
                assert " ".join(lines) == label
                assert visible[0]["t"] == "Ali Ghodsi · Scenario" and visible[-1]["t"] == "All-In Podcast"
                boxes = [item_box(it, plan.voices, wf.F.W, wf.F.H, f) for it in visible]
                assert all(.06 < x0 < x1 < .86 and .44 < y0 < y1 < .77 for x0, y0, x1, y1 in boxes)
                assert not any(intersects(box, other, pad=0) for i, box in enumerate(boxes) for other in boxes[i+1:])
            assert plan.voices["sequence_label"]["px"] >= 54


def test_m20_verified_author_and_review_own_their_reading_intervals(tmp_path, quiet_engines, monkeypatch):
    import wf1_display as display
    import podcast
    ctx, script, pack = material(tmp_path)
    essay = "I believe that AI could cure most major diseases in the next 5–10 years"
    review = "The FDA review team has 30 days to review the original IND submission."
    def source(url, author, title, date, words, locator):
        return dict(url=url, publisher=author, publication_date=date, quotations=[
            dict(speaker=author, event=title, locator=locator, words=words, url=url, sha256="verified")])
    author = source("essay", "Dario Amodei", "We Must Pace the Frontier", "September 2026", essay, "Opening")
    fda = source("fda", "FDA", "Step 3: Clinical Research", None, review, "Approval")
    script.update(window=[0,38.14], source_url=INTERVIEW, claims=[
        dict(start=0,end=6.1,stance="discussed_estimate",speaker="Jason Calacanis",subject="Dario Amodei",
             report="Could cancer be cured in a decade?",event="November roadshow")])
    concern = dict(first=42,last=57,speaker="Ali Ghodsi",stance="concern",report="Testing takes care.",
                   scope_question="What does FDA review?")
    pack["coverage"]["speaker_claims"] = [concern]
    pack["referent_research"] = dict(references=[dict(claim_start=0,sources=[author]),
        dict(scope_for=42,statement="FDA reviews the initial application.",sources=[fda])])
    words = [dict(w, t0=w["start"], t1=w["end"]) for w in pack["words"]["words"]]
    material_ = display.editorial(ctx, script, pack)
    direction = display.bind(dict(arc=[],asks=[dict(dev="staircase",at=dict(w=48),until=dict(w=56),
        args=dict(sequence=[dict(at=48,label="Can a trial begin?"),dict(at=53,label="Review")]))]),material_,words)
    direction.update(coverage_final=True, turn_seg=4)
    monkeypatch.setattr(wf1_coverage,"speaker_moves",lambda *a: ([],[]))
    scene, beats = wf.wf1_film.build(script, pack, "prints", direction)
    pages = beats["display_facts"]
    assert [p["facts"][0]["source"]["url"] for p in pages] == ["essay","fda"]
    for page, body, byline in zip(pages,[essay,review],["Dario Amodei","FDA"]):
        a,b = page["f"]
        ink = " ".join(i["t"] for i in scene["items"] if i.get("fact_type") and a<=i["f"][0]<b)
        assert body in ink and byline in ink and "Concern" not in ink
        assert all(i.get("pic")=="plate" for i in scene["items"] if i["k"] in ("shot","card")
                   and i["f"][0]<b and a<i["f"][1])
    assert "September 2026" in " ".join(i.get("t","") for i in scene["items"])
    report = podcast._sources_md("episode", [dict(n=1,title="Forecast",style="prints",
        film=dict(window=[0,38.14],checks=dict(podcast=dict(display_facts=pages))))])
    assert all(t in report for t in (essay,"essay",review,"fda","September 2026"))


def test_checkpoint_extracts_missing_forecast_from_author_page_and_keeps_scope(tmp_path, monkeypatch):
    import wf1_context
    import wf1_display
    import writer
    ctx, script, pack = material(tmp_path)
    script["claims"] = [dict(start=0,end=6,speaker="Host",subject="Author",report="The host asks about a forecast.")]
    pack["coverage"]["speaker_claims"] = [dict(first=42,last=57,speaker="Guest",stance="concern",
                                                scope_question="What is reviewed?",report="The guest raises a concern.")]
    scoped = dict(scope_for=42,statement="The regulator reviews the filing.",sources=[dict(url="scope",publisher="Agency",
        quotations=[dict(speaker="Agency",event="Guide",locator="Review",words="We review the filing.",url="scope",sha256="retained")])])
    article = dict(url="essay",author="Author",publication_date="2026",publisher="Author",supports="The author's forecast.")
    pack["referent_research"] = dict(references=[scoped,dict(sources=[article])])
    passage = "I believe the work could take five years."
    monkeypatch.setattr(wf1_context,"original_passages",lambda url, work: ([passage],url,"checked"))
    def extract(system, content, **options):
        data = json.loads(content)
        assert not data["scope_checks"] and options["searches"] == 0
        assert any(s.get("primary_text")==passage for s in data["existing_sources"])
        return json.dumps([dict(claim_start=0,sources=[dict(article,quotations=[
            dict(speaker="Author",event="Essay",locator="Opening",words=passage)])])]), {}
    monkeypatch.setattr(writer,"ask_openai",extract)
    llm = SimpleNamespace(openai_key=lambda:None,json_block=json.loads)
    assert wf1_context.complete_editorial(ctx,script,pack,llm)
    evidence = wf1_display.editorial(ctx,script,pack)
    assert evidence["claims"][0]["primary_excerpt"]["quote"] == passage
    assert evidence["speaker_claims"][0]["scope_fact"]["excerpt"]["sha256"] == "retained"
    assert not wf1_context.complete_editorial(ctx,script,pack,llm)
