"""The long-form writer: finds a story that holds 10-20 minutes, researches it into a dossier of quoted facts, and writes
it as chapters for a narrator, every fact with its source.

Three model calls, built on lane O's writer (valmera-stories/writer.py: the web, the providers, the fact check):

    find_ideas(card, avoid, line)   -> ranked ideas, each with its chapters, its ledger of stakes and what a camera can show
    dossier(idea)                   -> facts (a quote copied from a readable page and its URL), named things with Commons
                                       searches, places with coordinates, documents, a chronology; every quote is then
                                       fetched here and kept only when it stands on its page
    write(idea, dossier, card)      -> the script: chapters, segments with the actor's direction and the spoken form, the
                                       pictures and designed pictures of each segment, the ledger's ticks

The spoken form. The narrator reads `say`, written by the model the way it is said ("nineteen fourteen", "five thousand
dollars"); nothing here turns digits into words. The model also lists every proper noun as it is spelled (`names`): the
narrator's listeners hear with them as vocabulary, and the VOICE gate compares the spoken form with the listener's text
after a model, not a rule, has written that text in its spoken form (narrate/spoken.py).

Nothing here reads the model's words by rule; the checks compare measured strings (a quote on its page, a figure in a
verified quote).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES = os.path.join(os.path.dirname(HERE), "valmera-stories")
sys.path.insert(0, STORIES)
import writer  # noqa: E402  lane O's writer: ask_web, ask, json_block, verify, quote_found, page_text, VOICES

FORM = dict(name="long-form voiceover film", seconds=(600, 1200), words=(1850, 2500), segments=(55, 110), band="04",
            shape="a hook within ten seconds, chapters whose lengths and order emerge from the researched story, "
                  "a consequential turn in every chapter, open questions answered and a satisfying final payoff")
VOICES = {k: writer.VOICES[k] for k in ("sage", "iris", "atlas")}

# The story a long film tells (16 Nov). The 13 Nov "Saving Skylab" and the 16 Nov test read as procedures: a crew as a
# group, a promise that gave away the method ("how a sunshade, cutters and rope saved a space station"), the dramatic
# question never said, one chapter question per chapter read aloud, sixteen percent of sentences correcting a mistake
# nobody made ("this was not X, it was Y"), five "remember" callbacks, and two minutes of results after the climax.
STORY_SHAPE = (
    "the first segment is a cold open on a moment, in its first sentence (a person in danger, a thing that should not "
    "be true, a clock running), never a summary of the topic and never a greeting. Within twenty seconds the viewer knows "
    "who we follow, what they want and what they lose if they fail, and hears the one question the film answers last. "
    "The promise poses that question; it never gives away the answer or the method. The stakes rise chapter by chapter; "
    "each chapter opens inside an active situation and ends on something that pulls into the next one. Tell it through "
    "the protagonist's choices and what they cost. The film ends at the payoff: the answer, its price, and one line that "
    "reframes the first sentence; nothing after it lists further results. State each fact plainly once: never correct a "
    "mistake the viewer was not making ('this was not X, it was Y'), never remind the viewer what they just heard, never "
    "explain what a picture is not.")

# How a long film is written for the ear (16 Nov). Measured on the 13 Nov and 16 Nov Skylab scripts against three
# metromedia narrators: 10.8-13.1 words a sentence with 0.43-0.55 commas each (references 11.9-15.8 words, 0.62-1.35
# commas, a wider spread of lengths), so the narrator met a full stop every few seconds and fell at 78% of its line ends
# (references 53-55%, 20-29% rising; our read 4% rising). The engine reads only the words and punctuation: the shape of
# the sentences is the melody.
EAR = ("Write the way a storyteller talks, not the way a report reads. Let a thought run on with commas and 'and', "
       "'but', 'so', 'until', 'which' while it is still moving, and put a full stop only where the thought lands. Mix "
       "lengths widely: a run of eighteen to twenty-five words that builds, then a short line of three to six words that "
       "lands it. Average about one full stop every fourteen words, with a comma in most sentences. Put the news late in "
       "its sentence, where the voice climbs to it. Ask a question aloud only where the viewer is already asking it.")


def references():
    try:
        return open(os.path.join(HERE, "REFERENCES.md")).read()
    except OSError:
        return ""


# ----------------------------------------------------------------- ideas

def find_ideas(card, avoid=(), line=None, n=6, effort="high", delivered=""):
    system = ("You are the story finder of Valmera's long-form workflow: narrated films of 10 to 20 minutes in the line of "
              "The Sudden Theory, told by a narrator over real archive pictures and designed maps, documents and diagrams. "
              "You use the web to find stories and to check them. A story qualifies only if every fact in it can be sourced "
              "on pages a script can read (Wikipedia, NASA, the Library of Congress, archive.org, government and university "
              "pages), and if the people, places, machines and documents it names have real moving archive footage under a suitable licence "
              "or in the public domain (Wikimedia Commons, NASA, US government works, pre-1930 archives), because the film "
              "shows real moving footage of what it names and designed evidence where none exists. Prefer rich public archive video. No "
              "lookalikes, no generated faces.")
    system += "\n\n" + ((f"# Form\n{FORM['name']}, {FORM['seconds'][0] // 60}-{FORM['seconds'][1] // 60} minutes: {FORM['shape']}.\n\n"
               "Search widely. A long film holds the way the best story reels do, only longer: STORY first. Choose a story "
               "with ONE protagonist (a named person, or a small named crew led by one person) who wants one concrete thing "
               "against a clock, an opponent or nature; a price the viewer can feel if they fail (lives, a fortune, a "
               "company, a country, a life's work); ONE dramatic question the viewer can hold from the first ten seconds to "
               "the end (will they, can she, why did he); stakes that rise; at least one point where the plan fails or the "
               "truth turns; and a payoff that answers the question. A mechanism (how a thing was built, crossed, solved, "
               "survived) is how such a story is told, never the story itself: a procedure without a person who risks "
               "something is an explainer, not a story. It must be rich in real moving footage and original voices: archive "
               "recordings of its people, places and machines, maps, and documents (patents, letters, telegrams, reports) whose "
               "real words can be quoted. Avoid stories the references already told (Musk's Mars plan, SpaceX's fourth launch, "
               "Airbnb's cereal, Xerox PARC, the traitorous eight, the PayPal mafia).\n\n"
               f"Return JSON only: {{\"ideas\": [{n} items, best first]}}. Each: {{\"id\": \"i1\", \"title\": a plain title, "
               "\"hook\": the first sentence as it would be spoken, \"promise\": what the viewer will know by the end, in one "
               "sentence, \"protagonist\": who the story follows and what they want, \"question\": the one dramatic question "
               "the film answers last, \"stakes\": what is lost, and for whom, if they fail, \"story\": the story in plain sentences, with consequences as well as figures, \"chapters\": [chapters arising from the story, "
               "{\"title\": two to four words, \"question\": the question the chapter opens}], \"turn\": the turn, \"payoff\": the "
               "payoff with its figure, \"arc\": {\"setup\": one sentence opening the central question, \"escalation\": one sentence "
               "on how the stakes rise, \"payoff\": one sentence answering the question}, \"ledger\": [two to four {\"label\": the stake, \"start\": its value at the start, \"end\": "
               "its value at the end}], \"named\": [twelve to twenty {\"name\": usual English name, \"is\": person|place|machine|"
               "building|document|event|thing, \"searches\": one to three archive video and original interview searches, most specific first, "
               "\"must_show\": what a picture must visibly show to count}], \"sources\": [URLs you read], \"strength\": 1-10, how strongly the STORY holds a viewer for ten minutes "
               "(protagonist, question, stakes, turn, payoff; 9-10 only when all five are strong and true to the sources), "
               "never how much footage exists}."))
    system += f"\n\n# The bar\n{references()}\n\n"
    content = ((f"# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Already made\n{json.dumps(list(avoid), ensure_ascii=False)}\nThe owner's rule: never the exact same idea told the same "
               "way as one of these (the same story through the same beats, even under a new title or a new look). A new angle "
               "or a new version of the same subject is welcome.\n\n") + (delivered or "") +
               (f"# A line from the owner\n{line}\n\n" if line else ""))
    text, use = writer.ask_web(system, content, effort=effort, max_tokens=40000, searches=16, fetches=8, what="find ideas")
    got = writer.json_block(text)
    return (got.get("ideas") if isinstance(got, dict) else got) or [], use


# ----------------------------------------------------------------- the dossier

def dossier(idea, effort="high"):
    system = ("You are the researcher of Valmera's long-form workflow. You build the dossier a writer will tell a 10-20 minute "
              "true story from: facts with their exact sources, what the camera can show, where things happened. You read "
              "every page you cite with your web tools. Nothing is invented, rounded or dramatised beyond the sources.")
    content = (f"# The story\n{json.dumps(idea, ensure_ascii=False)}\n\n"
               "Research it now. Every quote is checked later by fetching its URL with a plain script and finding the quote "
               "on the page word for word, so cite pages a script can read (Wikipedia, NASA, the Library of Congress, "
               "archive.org, Smithsonian, government and university pages) and copy each quote exactly as it stands there, "
               "one or two sentences. Pages behind a paywall or a bot wall (Britannica, most newspapers) cannot be checked.\n\n"
               "Return JSON only: {\"facts\": [45 to 80 {\"id\": \"f1\", \"claim\": the fact in plain words, \"quote\": "
               "sentences copied exactly from the page, \"url\": the page, \"author\": byline on source, \"date\": publication date as printed, \"year\": the year it happened or null}], "
               "\"named\": [25 to 45 {\"name\", \"is\": person|place|machine|building|document|event|thing, \"searches\": one "
               "to three archive video and original interview searches, most specific first, \"must_show\": what a picture must visibly show, "
               "\"era\": the years a photograph of it must come from to be true to the story, or null}], \"places\": [{\"name\", "
               "\"lat\", \"lon\", \"why\": what happened there}], \"routes\": [{\"from\": a place name, \"to\": a place name, "
               "\"what\": what travelled}], \"documents\": [up to eight {\"title\": the document's name, \"date\": as printed, "
               "\"from\": who wrote or issued it, \"fact\": the id of the fact whose quote is its text}], \"chronology\": "
               "[{\"year\": n, \"event\": a few words, \"fact\": a fact id}]}")
    text, use = writer.ask_web(system, content, effort=effort, max_tokens=60000, searches=24, fetches=24, what="dossier")
    return writer.json_block(text), use


def check_dossier(d):
    """Each quote fetched from its own page here; only the facts whose quotes stand on their pages are kept."""
    rep = writer.verify(dict(facts=d.get("facts") or [], segments=[dict(facts=[f.get("id") for f in d.get("facts") or []])]))
    ok = {r["id"] for r in rep["facts"] if r.get("found")}
    kept = [f for f in d.get("facts") or [] if f.get("id") in ok]
    dropped = [dict(id=r["id"], url=r.get("url"), why=r.get("error") or "the quote is not on the page") for r in rep["facts"] if not r.get("found")]
    return dict(d, facts=kept), dict(verified=len(kept), total=len(rep["facts"]), dropped=dropped)


# ----------------------------------------------------------------- the script

DESIGN_KINDS = (
    '"design": null or one designed picture for this segment, where a photograph cannot show what is said: '
    '{"kind": "map", "places": [place names from the dossier], "route": [from, to] or null, "label": two to four words} | '
    '{"kind": "document", "fact": a fact id whose quote is shown as the document\'s text, "title": the document or page name, '
    '"mark": the few words of the quote to underline} | '
    '{"kind": "timeline", "points": [two to six {"year": n, "label": two to four words, "fact": id}]} | '
    '{"kind": "chart", "title": a few words, "bars": [two to five {"label": a few words, "value": number, "print": as printed, '
    '"fact": id}]} | {"kind": "number", "print": the figure as printed, "label": two to five words, "fact": id} | '
    '{"kind": "quote", "text": a sentence a person in the story said or wrote, copied from a fact\'s quote, "who": name, "fact": id}')


def production_brief(line):
    if not line:
        return ""
    return ("\n\n# Production brief\n" + line +
            "\nUse the requested running time when specified. Budget the narration for that range at about "
            "175 spoken words per minute, allowing time for archive inserts. Otherwise use the default form.")


def write(idea, dos, card, voices=None, notes=None, effort="high", line=None):
    voices = voices or card.get("voice") or ["sage", "iris"]
    facts = [dict(id=f["id"], claim=f.get("claim"), quote=f.get("quote"), year=f.get("year")) for f in dos.get("facts") or []]
    system = ("You are the writer and voice director of Valmera's long-form workflow. By default you write a 10-20 minute narration "
              "spoken by a synthetic narrator over real archive pictures and designed maps, documents and diagrams. Every "
              "statement of fact comes from the dossier's verified facts; you never add a fact, a figure or a date that is not "
              "in them, and you never round or dramatise beyond them. When citing an X post, paper or direct quotation choose design kind document or quote and its exact fact id. Choose the canonical status URL or actual paper PDF. You direct the actor too: pace, energy, melody, emphasis "
              "and pauses per segment. Write for the ear: concrete nouns, numbers a listener can hold, the "
              "voice of a storyteller who knows where the story goes and makes the viewer want the next minute.")
    lo, hi = FORM["words"]
    system += "\n\n" + ((f"# Form\n{FORM['name']}: {lo}-{hi} spoken words in {FORM['segments'][0]}-{FORM['segments'][1]} segments (at about "
               f"175 words a minute that is {lo // 175}-{hi // 175} minutes). {FORM['shape']}.\n\n"
               "How it holds (a story, not an explainer): " + STORY_SHAPE + " Every chapter has its own consequential turn, "
               "marked beat=turn on the segment where it happens. No call to action, no 'subscribe', no 'like'.\n\n"
               "The spoken form: `say` is exactly what the narrator says, every number, year, date, unit, currency and "
               "abbreviation written in words the way a narrator says it (\"nineteen fourteen\", \"five thousand six hundred "
               "dollars\", \"the eighteen nineties\", \"twenty-five percent\", \"Doctor\"), no digits and no symbols. `names` lists "
               "every proper noun in the narration exactly as spelled.\n\n"
               "Hard words: write difficult compounds in intelligible spoken forms (for example retro rockets) and preserve their meaning. "
               "Performance (the narrator reads only your words and punctuation, and every full stop is a falling end): " + EAR + " "
               "One or two focus words a segment; ordinary joins 0.22 seconds, long pauses only at consequential turns. "
               "Pictures: each segment lists the named things it talks about, in order (`pictures`, names from the list), so "
               "the film can show each on the word that names it; a segment may also ask for one designed picture.\n\n"
               "Return JSON only: {\"title\": a plain title, \"voice\": one voice id, \"pace\": slow|steady|brisk, \"energy\": "
               "low|mid|high, \"promise\": the promise as it is printed on screen, six to twelve words, \"names\": [proper nouns], "
               "\"chapters\": [{\"id\": \"c1\", \"title\": two to four words, \"question\": the chapter's question as printed, "
               "\"turn\": the factual reversal in this chapter, \"music_mood\": the emotional movement, \"pace\": slow|steady|brisk (how fast its pictures change: brisk for lists, money and action, slow for "
               "reflection)}], \"ledger\": {\"title\": one or two words, \"rows\": [two to four {\"id\": short id, \"label\": "
               "capitals, at most 10 characters, \"start\": the value shown at the start exactly as printed, \"number\": that "
               "value as a plain number}]}, \"segments\": [{\"chapter\": a chapter id, \"say\": the words spoken (the spoken "
               "form), \"beat\": hook|promise|setup|loop|turn|low|payoff|close, \"pictures\": [names], ") +
               (DESIGN_KINDS) +
               (", "
               "\"ticks\": [{\"row\": a ledger row id, \"to\": the new value as printed, \"number\": plain number, \"word\": the "
               "exact word of `say` on which it changes}] or [], \"pace\": slow|steady|brisk, \"melody\": falling|rising|level|"
               "wide, \"emphasis\": [one or two words of say], \"pause_after\": seconds of silence after it (0.18 to 0.26 normally, 0.4 only "
               "at chapter ends and after the turn), \"direction\": a few words for the actor, \"facts\": ids of the facts it "
               "states}], \"close\": {\"balance\": a one-line sum of the ledger as printed, \"line\": the last sentence}}\n"
               "Every digit printed anywhere (the ledger, a design, the promise, a question) must stand in the quote of a fact "
               "the same segment or design cites."))
    system += f"\n\n# The bar\n{references()}\n\n"
    content = ((f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n"
               f"# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Verified facts (only these)\n{json.dumps(facts, ensure_ascii=False)}\n\n"
               f"# Named things the camera can show\n{json.dumps([n.get('name') for n in dos.get('named') or []], ensure_ascii=False)}\n\n"
               f"# Places\n{json.dumps(dos.get('places') or [], ensure_ascii=False)}\n\n# Routes\n{json.dumps(dos.get('routes') or [], ensure_ascii=False)}\n\n"
               f"# Documents\n{json.dumps(dos.get('documents') or [], ensure_ascii=False)}\n\n"
               f"# Voices\n{json.dumps({k: VOICES[k] for k in voices if k in VOICES})}\n\n") +
               (f"# Notes from the last attempt (fix every one)\n{notes}\n\n" if notes else ""))
    content += production_brief(line)
    text, use = writer.ask_web(system, content, effort=effort, max_tokens=64000, searches=0, fetches=0, what="write")
    return writer.json_block(text), use


def printed_strings(script):
    """Every string the film prints that carries a figure: the ledger, the designs, the questions, the promise."""
    out = []
    led = script.get("ledger") or {}
    for r in led.get("rows") or []:
        out.append(str(r.get("start")))
    for s in script.get("segments") or []:
        for t in s.get("ticks") or []:
            out.append(str(t.get("to")))
        d = s.get("design") or {}
        k = d.get("kind")
        if k == "number":
            out.append(str(d.get("print")))
        elif k == "chart":
            out += [str(b.get("print") or b.get("value")) for b in d.get("bars") or []]
        elif k == "timeline":
            out += [str(p.get("year")) for p in d.get("points") or []]
    out += [str(c.get("question") or "") for c in script.get("chapters") or []]
    out.append(str(script.get("promise") or ""))
    out.append(str((script.get("close") or {}).get("balance") or ""))
    return [x for x in out if x and x != "None"]


def check_script(script, dos):
    """Measured: the facts it cites exist and were verified, every printed figure stands in a verified quote, the length
    is inside the form, the spoken form carries no digits."""
    facts = {f["id"]: f for f in dos.get("facts") or []}
    problems = []
    used = set()
    for i, s in enumerate(script.get("segments") or []):
        for fid in s.get("facts") or []:
            used.add(fid)
            if fid not in facts:
                problems.append(f"segment {i} cites {fid}, which is not a verified fact")
        d = s.get("design") or {}
        for fid in [d.get("fact")] + [b.get("fact") for b in d.get("bars") or []] + [p.get("fact") for p in d.get("points") or []]:
            if fid and fid not in facts:
                problems.append(f"segment {i}'s design cites {fid}, which is not a verified fact")
        if any(ch.isdigit() for ch in str(s.get("say", ""))):
            problems.append(f"segment {i}'s say has digits; write every number as it is spoken")
    have = set()
    for f in facts.values():
        have.update(writer.digits(f.get("quote", "")))
    unbacked = sorted({d for x in printed_strings(script) for d in writer.digits(x) if d not in have})
    if unbacked:
        problems.append(f"these printed figures stand in no verified quote: {unbacked}")
    n = writer.words_of(script)
    lo, hi = FORM["words"]
    if not lo <= n <= hi * 1.1:
        problems.append(f"{n} spoken words; the form wants {lo}-{hi}")
    for c in script.get("chapters") or []:
        if not any(s.get("chapter")==c.get("id") and s.get("beat")=="turn" for s in script.get("segments",[])):
            problems.append(f"chapter {c.get('id')} needs a beat=turn segment")
        if not c.get("turn"):
            problems.append(f"chapter {c.get('id')} needs a consequential turn")
    chs = {c.get("id") for c in script.get("chapters") or []}
    if len(chs) < 2:
        problems.append(f"{len(chs)} chapters; a long film needs distinct story movements")
    return dict(ok=not problems, problems=problems, words=n, segments=len(script.get("segments") or []), facts_used=len(used),
                figures_unbacked=unbacked)


def narration_script(script):
    """The narrate tool's script: the spoken form and the actor's direction as the writer gave them, and the names the
    listeners hear with."""
    segs = []
    for s in script["segments"]:
        seg = dict(text=" ".join(str(s["say"]).split()), pause_after=float(s.get("pause_after") or 0.22))
        for k in ("beat", "melody", "emphasis", "direction", "pace", "energy"):
            if s.get(k):
                seg[k] = s[k]
        # Madigan's two reference reads average 157–159 words per elapsed minute.
        # Articulated pace excludes pauses. Let the writer's brisk turns move
        # faster, and give reflective evidence passages a warmer, longer vowel.
        pace = seg.get("pace", script.get("pace", "steady"))
        seg["wpm"] = {"slow": 158, "steady": 175, "brisk": 190}.get(pace,175)
        seg["phonon_duration_delta"] = {"slow": .85, "steady": .60, "brisk": .25}.get(pace,.60)
        segs.append(seg)
    segs[-1]["pause_after"] = 0.0
    return dict(title=script.get("title"), voice=script.get("voice") or "sage", band=FORM["band"], pace=script.get("pace") or "steady",
                energy=script.get("energy") or "mid", names=list(script.get("names") or []), segments=segs)
