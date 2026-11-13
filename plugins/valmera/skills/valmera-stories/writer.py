"""The writer: finds stories worth telling and writes them as narration, every fact with its source.

Workflow 3 (topic and founder stories, 45-75 s) and workflow 4 (long-form voiceover, 10-20 min) both
call it; `FORMS` holds what differs. Two model calls, each with the web (search and fetch):

    find_ideas(form, cards, avoid, line)   -> ranked ideas: a subject, why it hooks, the named things
                                              a camera could show, the sources
    write(idea, card, form, voices)        -> the script: segments to be spoken (with the actor's
                                              direction), what is shown, every fact with a quote and
                                              its URL, and the style's own data
    verify(script)                         -> each quote fetched from its URL here, not trusted from
                                              the model; every number on screen must be in a
                                              verified quote. A comparison of measured strings.

The model decides what a story is and how it is told; nothing here reads its words by rule.
The key comes from this process's environment (run under .valmera/claude_trial_env.sh).
"""
import html
import html.parser
import json
import os
import re
import time
import urllib.request

MODEL = "claude-opus-5-5"
# USD per token: input, cache write, cache read, output; and per web search
PRICE = (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6)
SEARCH_USD = 0.01
UA = "Mozilla/5.0 (compatible; ValmeraStories/1.0)"
HERE = os.path.dirname(os.path.abspath(__file__))

FORMS = {
    "short": dict(name="vertical short", seconds=(45, 75), words=(130, 190), segments=(8, 16), band="03",
                  shape="one story, one turn, one payoff; a hook a viewer cannot scroll past in the first second; no chapters"),
    "long": dict(name="long-form voiceover film", seconds=(600, 1200), words=(1600, 3200), segments=(40, 110), band="04",
                 shape="a promise on screen by 30 seconds, chapters of 40 to 120 seconds each opening a new question, a turn at "
                       "two thirds, the promise paid at the end"),
}

VOICES = {
    "atlas": "deep, warm male documentary storyteller",
    "nova": "clear, warm, expressive female storyteller",
    "blaze": "energetic, punchy, high-register voice for hooks",
    "sage": "mature, powerful male, for long-form",
    "iris": "soft, intimate, thoughtful female, for long-form",
}


# ----------------------------------------------------------------- one model call with the web

def _cost(u):
    pin, pw, pr, pout = PRICE
    s = getattr(u, "server_tool_use", None)
    searches = int(getattr(s, "web_search_requests", 0) or 0) if s else 0
    fetches = int(getattr(s, "web_fetch_requests", 0) or 0) if s else 0
    usd = (u.input_tokens * pin + (u.cache_creation_input_tokens or 0) * pw + (u.cache_read_input_tokens or 0) * pr
           + u.output_tokens * pout + searches * SEARCH_USD)
    return usd, searches, fetches


def ask_web(system, content, *, effort="high", max_tokens=32000, searches=12, fetches=12, what="writer"):
    """(text, usage). The model may search and fetch; a paused turn is continued until it ends."""
    import anthropic
    c = anthropic.Anthropic()
    tools = []
    if searches:
        tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": searches, "allowed_callers": ["direct"]})
    if fetches:
        tools.append({"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": fetches, "allowed_callers": ["direct"]})
    msgs = [{"role": "user", "content": content}]
    t0, usd, n_s, n_f, text, tin, tout = time.time(), 0.0, 0, 0, "", 0, 0
    for _ in range(6):
        final = None
        for attempt in range(4):              # a long turn with the web can lose its connection; the turn is asked again
            try:
                with c.messages.stream(model=MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"}, output_config={"effort": effort},
                                       system=system, tools=tools, messages=msgs) as st:
                    final = st.get_final_message()
                break
            except (anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.RateLimitError):
                if attempt == 3:
                    raise
                time.sleep(8 * (attempt + 1))
            except Exception as e:  # noqa: BLE001 - transport errors arrive unwrapped from a stream
                if type(e).__name__ not in ("RemoteProtocolError", "ReadTimeout", "ReadError", "ConnectError", "WriteError") or attempt == 3:
                    raise
                time.sleep(8 * (attempt + 1))
        u = final.usage
        d, s, f = _cost(u)
        usd, n_s, n_f, tin, tout = usd + d, n_s + s, n_f + f, tin + u.input_tokens, tout + u.output_tokens
        text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
        if final.stop_reason == "refusal":
            raise RuntimeError("the model refused this stage")
        if final.stop_reason != "pause_turn":
            break
        msgs = [msgs[0], {"role": "assistant", "content": final.content}]
    return text, dict(provider="anthropic", model=MODEL, effort=effort, what=what, input=tin, output=tout, web_searches=n_s,
                      web_fetches=n_f, usd=round(usd, 4), seconds=round(time.time() - t0, 1), stop=final.stop_reason)


def ask(system, content, *, effort="medium", max_tokens=8000, what="look"):
    """(text, usage): one call without tools (a picture may be in the content)."""
    import anthropic
    c = anthropic.Anthropic()
    t0 = time.time()
    with c.messages.stream(model=MODEL, max_tokens=max_tokens, thinking={"type": "adaptive"}, output_config={"effort": effort},
                           system=system, messages=[{"role": "user", "content": content}]) as st:
        final = st.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("the model refused this stage")
    u = final.usage
    usd, _, _ = _cost(u)
    text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
    return text, dict(provider="anthropic", model=MODEL, effort=effort, what=what, input=u.input_tokens, output=u.output_tokens,
                      usd=round(usd, 4), seconds=round(time.time() - t0, 1), stop=final.stop_reason)


def json_block(text):
    """The model's JSON answer: the outermost object in its text (machine syntax only)."""
    s = text.strip()
    i, j = s.find("{"), s.rfind("}")
    while i != -1 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except ValueError:
            i = s.find("{", i + 1)
    raise ValueError("no JSON in the model's answer")


# ----------------------------------------------------------------- ideas

def references():
    try:
        return open(os.path.join(HERE, "REFERENCES.md")).read()
    except OSError:
        return ""


def menu(cards):
    return "\n".join(f"- {s['id']} ({s['name']}): {s['look']} Hook: {s['hook']} Close: {s['close']} Needs: {s['needs']}." for s in cards)


def find_ideas(form, cards, avoid=(), line=None, n=6, effort="high"):
    """Ranked ideas for the styles in `cards`, found on the web by the model itself."""
    F = FORMS[form]
    system = ("You are the story finder of Valmera's story workflow. You find true stories about founders, companies, inventions "
              "and topics that make a viewer stop scrolling, for a narrated " + F["name"] + ". You use the web to find them and to "
              "check them. A story qualifies only if every fact in it can be sourced (Wikipedia, a major newspaper, the company's own "
              "record, an archive), and if the people, places and things it names have real photographs under a free licence on "
              "Wikimedia Commons, because the film shows only real pictures of what it names (no film clips, no stock lookalikes, "
              "no generated faces).")
    content = (f"# The bar\n{references()}\n\n# Styles this film can take\n{menu(cards)}\n\n"
               f"# Already made (never repeat a subject)\n{json.dumps(list(avoid), ensure_ascii=False)}\n\n"
               + (f"# A line from the owner\n{line}\n\n" if line else "")
               + f"# Form\n{F['name']}, {F['seconds'][0]}-{F['seconds'][1]} s, {F['shape']}.\n\n"
               "Search widely and think like an editor of the best founder-story accounts. Prefer stories most people half know with "
               "a detail they do not, an object that holds the story, a human turn (a betrayal, a humiliation, a bet), and a payoff "
               "with a figure. Avoid the stories the references already told (SpaceX's fourth launch, Airbnb's cereal, Xerox PARC, "
               "the traitorous eight, the PayPal mafia). Check on Commons that the named things have photos.\n\n"
               f"Return JSON only: {{\"ideas\": [{n} items, best first]}}. Each: {{\"id\": \"i1\", \"title\": a plain title, "
               "\"style\": one style id from the list, \"why_style\": what in this story that style needs, \"hook\": the first sentence "
               "as it would be spoken, \"story\": the story in four to six plain sentences with its figures, \"turn\": the human turn, "
               "\"payoff\": the payoff with its figure, \"named\": [up to 8 {\"name\": usual English name, \"is\": person|place|company|"
               "thing|event, \"searches\": one to three Wikimedia Commons searches, most specific first, \"must_show\": one sentence: what "
               "a picture must visibly show to count}], \"photo\": for one-photograph only, {\"file\": the exact Commons file title "
               "starting File:, \"licence\": its licence, \"why\": why this one picture holds the whole story} else null, \"sources\": "
               "[URLs you read], \"strength\": 1-10}.")
    text, use = ask_web(system, content, effort=effort, max_tokens=24000, searches=14, fetches=10, what="find ideas")
    got = json_block(text)
    ideas = got.get("ideas") if isinstance(got, dict) else got
    ids = {c["id"] for c in cards}
    return [i for i in ideas or [] if i.get("style") in ids], use


# ----------------------------------------------------------------- the script

STYLE_DATA = {
    "character-sheet": (
        '"stats": [two to four {"id": short id, "label": capitals, at most 7 characters, "start": the value shown at frame 0 exactly '
        'as printed (e.g. "$5,000", "3", "0"), "number": that value as a plain number, "max": the most this stat reaches in the story '
        '(the bar\'s full length)}], and in each segment "ticks": [{"stat": id, "to": the value as printed, "number": plain number, '
        '"word": the exact word of `say` on which it changes}] (only changes the story states, every value in a fact\'s quote), '
        '"overflow": {"stat": id, "to": the final value as printed, "number": plain number, "word": a word of the last segments} for the '
        'stat that overflows the block at the close. Mark one segment "beat": "low" where a stat is at its worst.'),
    "whole-page": (
        'each segment may carry "window": {"subject": the name of one named thing, "line": four to eight words it sits after} for the '
        'four to six photographs set as windows into the page; the first window comes within the first three segments.'),
    "one-photograph": (
        '"photo": {"file": the Commons file title of the one photograph, "about": what it shows, "people": [{"name": a person the story '
        'names who is in the photo, "where": where in the photo (row and position, from its Commons description)}]}, and in each '
        'segment "look": the name of the person or the part of the photo the camera travels to for that segment ("whole" for the '
        'whole photo), "mark": the name of a person whose part is told in this segment (they keep a small mark from then on) or null. '
        '"portrait": {"name": one person, "segment": the index of the one segment whose sentence is set in tiny type inside their face}.'),
}


def write(idea, card, form, voices=None, notes=None, effort="high"):
    """The script for one idea in one style. Facts are re-read on the web; every one carries a quote and its URL."""
    F = FORMS[form]
    voices = voices or card.get("voice") or list(VOICES)
    system = ("You are the writer and voice director of Valmera's story workflow. You write narration that is spoken by a synthetic "
              "narrator over real photographs, for a " + F["name"] + ". Every sentence is true and checked on the web now; nothing "
              "is invented, rounded or dramatised beyond the sources. You also direct the actor: pace, energy, melody, emphasis and "
              "pauses per segment. Write for the ear: short sentences, concrete nouns, numbers a listener can hold.")
    content = (f"# The bar\n{references()}\n\n# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Form\n{F['name']}: {F['words'][0]}-{F['words'][1]} spoken words in {F['segments'][0]}-{F['segments'][1]} segments "
               f"(about {F['seconds'][0]}-{F['seconds'][1]} s at 175 words a minute). {F['shape']}.\n\n"
               f"# Voices\n{json.dumps({k: VOICES[k] for k in voices if k in VOICES})}\n\n"
               + (f"# Notes from the last attempt\n{notes}\n\n" if notes else "")
               + "Read the sources again with the web tools and check every fact. The first segment is the hook: it must work in the "
               "first second (a known name and something that should not be true, or a stake), never a greeting or a question to the "
               "viewer. The last segment reframes the hook in one sentence. No call to action, no 'follow for more'.\n\n"
               "Return JSON only: {\"title\": a plain title, \"voice\": one voice id, \"pace\": slow|steady|brisk|fast, \"energy\": "
               "low|mid|high, \"hook_line\": the first segment's words, \"close_line\": the last segment's words, \"segments\": [{\"say\": "
               "the words spoken, numbers and years written as words the way they are said (\"nineteen seventy-three\", \"five thousand "
               "dollars\"), no digits, no abbreviations, no symbols, \"show\": at most two short display strings for this segment as they "
               "would be printed, digits allowed (a year, a figure, a name), or [], \"beat\": hook|setup|turn|low|payoff|close, "
               "\"pictures\": names from the named list this segment talks about, in order, \"melody\": falling|rising|level|wide, "
               "\"emphasis\": [one or two words of say], \"pause_after\": seconds of silence after it (0.15 to 0.9), \"direction\": a few "
               "words for the actor, \"facts\": ids of the facts it states}], \"facts\": [{\"id\": \"f1\", \"claim\": the fact in plain "
               "words, \"quote\": a sentence copied exactly, word for word, from the page at url that states it, \"url\": the page}], "
               "\"named\": [{\"name\", \"is\", \"searches\", \"must_show\"} for every person, place or thing the film shows], "
               + STYLE_DATA.get(card["id"], "") + "}\n"
               "Every digit that appears in `show` or in the style's data must appear in some fact's quote.")
    text, use = ask_web(system, content, effort=effort, max_tokens=32000, searches=8, fetches=10, what="write")
    return json_block(text), use


# ----------------------------------------------------------------- verify against the pages themselves

class _Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "sup"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "sup") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def norm(s):
    """Lower case, letters and digits only, single spaces; thousands separators dropped."""
    s = html.unescape(str(s)).lower().replace("’", "'")
    s = re.sub(r"(?<=\d)[,  ](?=\d{3})", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


_PAGES = {}


def page_text(url, timeout=20):
    if url in _PAGES:
        return _PAGES[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(6 * 1024 * 1024).decode(r.headers.get_content_charset() or "utf-8", "replace")
    p = _Text()
    p.feed(raw)
    _PAGES[url] = norm(" ".join(p.out))
    return _PAGES[url]


def quote_found(quote, page):
    """True when the quote stands on the page: whole, or 85% of its six-word runs in place."""
    q = norm(quote)
    if not q:
        return False
    if q in page:
        return True
    w = q.split()
    if len(w) < 8:
        return False
    runs = [" ".join(w[i:i + 6]) for i in range(len(w) - 5)]
    return sum(r in page for r in runs) / len(runs) >= 0.85


def digits(text):
    return re.findall(r"\d+", norm(text))


def shown_strings(script):
    out = []
    for s in script.get("segments") or []:
        out += [str(x) for x in s.get("show") or []]
        for t in s.get("ticks") or []:
            out.append(str(t.get("to")))
    for st in script.get("stats") or []:
        out.append(str(st.get("start")))
    if script.get("overflow"):
        out.append(str(script["overflow"].get("to")))
    return [x for x in out if x and x != "None"]


def verify(script):
    """Each quote fetched from its own page; every number on screen inside a verified quote."""
    rows, ok_quotes = [], []
    for f in script.get("facts") or []:
        r = dict(id=f.get("id"), url=f.get("url"), claim=f.get("claim"))
        try:
            pg = page_text(f["url"])
            r["found"] = quote_found(f.get("quote", ""), pg)
        except Exception as e:  # noqa: BLE001 - an unreadable page verifies nothing
            r["found"], r["error"] = False, str(e)[:160]
        if r["found"]:
            ok_quotes.append(norm(f.get("quote", "")))
        rows.append(r)
    have = set()
    for q in ok_quotes:
        have.update(digits(q))
    unbacked = sorted({d for s in shown_strings(script) for d in digits(s) if d not in have})
    used = {fid for s in script.get("segments") or [] for fid in s.get("facts") or []}
    bad_facts = [r["id"] for r in rows if not r["found"] and r["id"] in used]
    return dict(facts=rows, verified=sum(r["found"] for r in rows), total=len(rows), numbers_unbacked=unbacked,
                facts_unverified=bad_facts, ok=not unbacked and not bad_facts and bool(rows))


def words_of(script):
    return sum(len(str(s.get("say", "")).split()) for s in script.get("segments") or [])


def narration_script(script, band):
    """The narrate tool's script: the spoken words and the actor's direction, as the writer gave them."""
    segs = []
    for s in script["segments"]:
        seg = dict(text=" ".join(str(s["say"]).split()), pause_after=float(s.get("pause_after") or 0.3))
        for k in ("melody", "emphasis", "direction", "pace", "energy"):
            if s.get(k):
                seg[k] = s[k]
        segs.append(seg)
    segs[-1]["pause_after"] = 0.0
    return dict(title=script.get("title"), voice=script.get("voice") or "atlas", band=band, pace=script.get("pace") or "steady",
                energy=script.get("energy") or "mid", segments=segs)
