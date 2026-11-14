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
import sys
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
import openai_cache

FORMS = {
    # the judge's story reels run 61-124 s (median 87.5 s, teardown section 12): about a minute and a half of story
    "short": dict(name="vertical short", seconds=(60, 90), words=(175, 250), segments=(12, 20), band="03",
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


# The provider. Claude by default when this process holds an Anthropic key; OpenAI (the Responses API with its own web
# search) when WRITER_PROVIDER=openai or when only an OpenAI key is present. Both answer the same prompts.
OA_MODEL = os.environ.get("WRITER_OPENAI_MODEL", "gpt-6.1-sol")
# USD per token: input, cached input, output (worker/model_prices.py, the providers' own pages); per web search call
OA_PRICE = {"gpt-6.1-sol": (2.0e-6, 0.1e-6, 10.0e-6), "gpt-6-sol": (2.0e-6, 0.2e-6, 10.0e-6), "gpt-5.5": (5.0e-6, 0.5e-6, 30.0e-6),
            "gpt-6-luna": (0.10e-6, 0.01e-6, 0.50e-6), "gpt-5.6-luna": (0.20e-6, 0.02e-6, 1.20e-6)}
OA_SEARCH_USD = 0.01
OA_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}


def provider():
    p = os.environ.get("WRITER_PROVIDER")
    if p:
        return p
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"


def _oa_parts(content):
    """Claude content blocks (text, base64 image) to Responses input parts."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            src = b.get("source") or {}
            url = src.get("url") if src.get("type") == "url" else f"data:{src.get('media_type') or 'image/jpeg'};base64,{src.get('data', '')}"
            out.append({"type": "input_image", "image_url": url, "detail": "high"})
        else:
            out.append({"type": "input_text", "text": b.get("text", "") if isinstance(b, dict) else str(b)})
    return out


def ask_openai(system, content, *, effort="high", max_tokens=32000, searches=0, what="writer", model=None, timeout=2400):
    """(text, usage): one streamed Responses call; with `searches`, the model may use OpenAI's web search. The key comes
    from this process's environment (OPENAI_API_KEY) and is never printed or stored."""
    import httpx
    model = model or OA_MODEL
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": _oa_parts(content)}],
            "max_output_tokens": int(max_tokens), "reasoning": {"effort": OA_EFFORT.get(effort, "high")}, "stream": True, "store": False}
    if searches:
        body["tools"] = [{"type": "web_search"}]
        body["include"] = ["web_search_call.action.sources"]
    body = openai_cache.responses(body, "writer:" + what)
    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"}
    t0 = time.time()
    final = None
    for attempt in range(4):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=30)) as cl, \
                    cl.stream("POST", "https://api.openai.com/v1/responses", headers=headers, json=body) as r:
                if r.status_code >= 400:
                    msg = r.read().decode("utf-8", "replace")[:400]
                    if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                        time.sleep(10 * (attempt + 1))
                        continue
                    raise RuntimeError(f"openai {r.status_code}: {msg}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if ev.get("type") in ("response.completed", "response.incomplete", "response.failed"):
                        final = ev.get("response") or {}
            if final is not None:
                break
        except (httpx.TransportError, httpx.TimeoutException):
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    if final is None:
        raise RuntimeError("openai: the stream ended without a response")
    usage = final.get("usage") or {}
    searches_used = sum(item.get("type") == "web_search_call" for item in final.get("output") or [])
    openai_cache.record(model, "writer:" + what, usage,
                        usd=openai_cache.cost(model, usage) + searches_used * OA_SEARCH_USD,
                        request_id=final.get("id"), status=final.get("status"), force=True)
    if final.get("status") == "failed":
        raise RuntimeError(f"openai failed: {json.dumps(final.get('error'))[:300]}")
    text, n_search, cited = "", 0, []
    for item in final.get("output") or []:
        if item.get("type") == "web_search_call":
            n_search += 1
            for s in ((item.get("action") or {}).get("sources") or []):
                if s.get("url"):
                    cited.append(s["url"])
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                    cited += [a.get("url") for a in c.get("annotations") or [] if a.get("url")]
    u = final.get("usage") or {}
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    cached = int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    pin, pc, pout = OA_PRICE.get(model, OA_PRICE["gpt-5.5"])
    usd = openai_cache.cost(model, u) + n_search * OA_SEARCH_USD
    SEEN_URLS.update(u_ for u_ in cited if u_)
    return text, dict(provider="openai", model=model, effort=effort, what=what, input=tin, output=tout, cache_read=cached,
                      cache_write=openai_cache.tokens(u)["cache_write_tokens"], web_searches=n_search,
                      web_fetches=0, usd=round(usd, 4), seconds=round(time.time() - t0, 1), stop=final.get("status"),
                      incomplete=(final.get("incomplete_details") or {}).get("reason"))


SEEN_URLS = set()


def ask_web(system, content, *, effort="high", max_tokens=32000, searches=12, fetches=12, what="writer"):
    """(text, usage). The model may search and fetch; a paused turn is continued until it ends."""
    if provider() == "openai":
        return ask_openai(system, content, effort=effort, max_tokens=max(max_tokens, 48000), searches=searches or fetches, what=what)
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
        for b in final.content:
            if getattr(b, "type", "") == "web_fetch_tool_result" and getattr(b.content, "type", "") == "web_fetch_result":
                src = getattr(b.content.content, "source", None)
                if getattr(src, "type", "") == "text" and getattr(src, "data", None):
                    FETCHED[b.content.url] = src.data
        if final.stop_reason == "refusal":
            raise RuntimeError("the model refused this stage")
        if final.stop_reason != "pause_turn":
            break
        msgs = [msgs[0], {"role": "assistant", "content": final.content}]
    return text, dict(provider="anthropic", model=MODEL, effort=effort, what=what, input=tin, output=tout, web_searches=n_s,
                      web_fetches=n_f, usd=round(usd, 4), seconds=round(time.time() - t0, 1), stop=final.stop_reason)


def ask(system, content, *, effort="medium", max_tokens=8000, what="look"):
    """(text, usage): one call without tools (a picture may be in the content)."""
    if provider() == "openai":
        return ask_openai(system, content, effort=effort, max_tokens=max(max_tokens, 16000), what=what)
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
              "record, an archive), and if the people, places and events it names were filmed or photographed: the film is cut from "
              "real video of them (interviews, keynotes, launches, news reports, archive film on YouTube and archive.org) and free "
              "photographs on Wikimedia Commons, a new picture about every half second, so a story whose subjects were never filmed "
              "cannot be made. No stock lookalikes, no generated faces.")
    content = (f"# The bar\n{references()}\n\n# Styles this film can take\n{menu(cards)}\n\n"
               f"# Already made (never repeat a subject)\n{json.dumps(list(avoid), ensure_ascii=False)}\n\n"
               + (f"# What the owner asked for\n{line}\nEvery idea must be about this. When it names a person, a company or a "
                  "topic, find the strongest true stories inside it (a single moment, decision or turn, not a biography).\n\n" if line else
                  "# No subject given\nChoose the subjects yourself: founders, companies, inventions and moments in business or "
                  "technology history that a curious viewer half knows, told through one decisive moment.\n\n")
               + f"# Form\n{F['name']}, {F['seconds'][0]}-{F['seconds'][1]} s, {F['shape']}.\n\n"
               "Search widely and think like an editor of the best founder-story accounts. Prefer stories most people half know with "
               "a detail they do not, an object that holds the story, a human turn (a betrayal, a humiliation, a bet), and a payoff "
               "with a figure. Avoid the stories the references already told (SpaceX's fourth launch, Airbnb's cereal, Xerox PARC, "
               "the traitorous eight, the PayPal mafia). Check that the named people and events were filmed (an interview, a "
               "keynote, a news report, archive footage) and have photos on Commons.\n\n"
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

REEL_DATA = (
    '"era" in each segment: the year that segment speaks from, as a number (the year of the events it tells; the present year '
    'for a segment about today): the film draws each section in the finish of its years. "collage": true on the two to four '
    'segments that name or list several things (people, products, places) at once: their pictures land as stacked prints. '
    'Mark beats truthfully: one "low" where the story is at its worst, one "turn", the "payoff" with its figure. Give every '
    'segment two or three "pictures" (named things it shows, in the order it says them), so the cut can change picture '
    'about every half second, and name in "named" every person, place, company, product and event a camera filmed, with '
    'Commons searches; the footage engine finds the video of each.')
STYLE_DATA = {
    "reel": REEL_DATA,
    "pinboard": REEL_DATA + ' This style stacks prints on most lines: mark "collage": true on more segments, about half.',
    "archive": REEL_DATA,
    "window": REEL_DATA,
    "character-sheet": (
        '"stats": [two to four {"id": short id, "label": capitals, at most 7 characters, "start": the value shown at frame 0 exactly '
        'as printed (e.g. "$5,000", "3", "0"), "number": that value as a plain number, "max": the most this stat reaches in the story '
        '(the bar\'s full length)}], and in each segment "ticks": [{"stat": id, "to": the value as printed, "number": plain number, '
        '"word": the exact word of `say` on which it changes}] (only changes the story states, every value in a fact\'s quote), '
        '"overflow": {"stat": id, "to": the final value as printed, "number": plain number, "word": a word of the last segments} for the '
        'stat that overflows the block at the close. Mark one segment "beat": "low" where the story is at its worst, and give "low_stat": the '
        'id of the stat that is at its worst there (its bar goes red).'),
    "whole-page": (
        '"headline": the page\'s headline, two to five words, that does not repeat the first sentence, and each segment may carry '
        '"window": {"subject": the name of one named thing, "line": four to eight words it sits after} for the four to six photographs '
        'set as windows into the page; the first window comes within the first three segments.'),
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
              "narrator over real footage and photographs of what it names, for a " + F["name"] + ". Every sentence is true and checked on the web now; nothing "
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
               "low|mid|high, \"hook_line\": the first segment's words, \"close_line\": the last segment's words, \"names\": [every "
               "proper noun the narration says, exactly as spelled; the narrator's listeners hear with them], \"segments\": [{\"say\": "
               "the words spoken, numbers and years written as words the way they are said (\"nineteen seventy-three\", \"five thousand "
               "dollars\"), no digits, no abbreviations, no symbols, \"show\": at most two short display strings for this segment as they "
               "would be printed, digits allowed (a year, a figure, a name), or [], \"beat\": hook|setup|turn|low|payoff|close, \"pace\": slow|steady|brisk|fast (how fast this "
               "segment's pictures change and its voice moves: brisk on a list or on money, slow on the turn), "
               "\"pictures\": names from the named list this segment talks about, in order, \"melody\": falling|rising|level|wide, "
               "\"emphasis\": [one or two words of say], \"pause_after\": seconds of silence after it (0.18 to 0.26 inside the story; "
               "0.35 to 0.5 only where the story turns, at most three times), \"direction\": a few "
               "words for the actor, \"facts\": ids of the facts it states}], \"facts\": [{\"id\": \"f1\", \"claim\": the fact in plain "
               "words, \"quote\": a sentence copied exactly, word for word, from the page at url that states it, \"url\": the page}], "
               "\"named\": [{\"name\", \"is\", \"searches\", \"must_show\"} for every person, place or thing the film shows], "
               + STYLE_DATA.get(card["id"], "") + "}\n"
               "Every digit that appears in `show` or in the style's data must appear in some fact's quote. Every quote is checked "
               "by fetching its URL with a plain script, so cite pages a script can read (Wikipedia, Wikimedia, archive.org, "
               "government and university pages, a company's own newsroom) and read each one with web_fetch before quoting it; "
               "pages behind a paywall or a bot wall (Britannica, most newspapers) and PDF files cannot be checked and will be dropped: "
               "cite the HTML page that says the same thing.")
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
# pages the web_fetch tool read during this process: fetched by Anthropic's servers, not written by the model, so a
# quote is also checked against them when a site refuses this machine (a 403 to a script)
FETCHED = {}


def _read(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if "pdf" in (r.headers.get_content_type() or ""):
            raise ValueError("a PDF cannot be read here: quote an HTML page that says it")
        return r.read(6 * 1024 * 1024).decode(r.headers.get_content_charset() or "utf-8", "replace")


def page_text(url, timeout=20):
    """The page's text. A site that refuses this machine (403, 429, a reset) is read from the Wayback Machine's copy of
    the same URL (its original bytes, `id_`): the same page, fetched from an archive, never a model's account of it."""
    if url in _PAGES:
        return _PAGES[url]
    if str(url).lower().split("?")[0].endswith(".pdf"):
        raise ValueError("a PDF cannot be read here: quote an HTML page that says it")
    try:
        raw = _read(url, timeout)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - refused or unreachable: the archived copy, else the first error
        raw = None
        for wait in (0, 3, 8):          # the archive refuses a burst now and then: ask again, a little later
            time.sleep(wait)
            try:
                raw = _read("https://web.archive.org/web/2026id_/" + url, timeout + 20)
                break
            except Exception:  # noqa: BLE001
                continue
        if raw is None:
            raise e
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
            r["read"] = "here"
        except Exception as e:  # noqa: BLE001 - an unreadable page verifies nothing here
            r["found"], r["error"] = False, str(e)[:160]
        if not r["found"] and not r.get("error") and not str(f.get("url", "")).startswith("https://web.archive.org/"):
            try:                       # a page that hides its text from a script (scripts, a soft wall): its archived copy
                arch = "https://web.archive.org/web/2026id_/" + f["url"]
                if arch not in _PAGES:
                    p_ = _Text()
                    p_.feed(_read(arch, 40))
                    _PAGES[arch] = norm(" ".join(p_.out))
                if quote_found(f.get("quote", ""), _PAGES[arch]):
                    r["found"], r["read"] = True, "wayback"
            except Exception:  # noqa: BLE001 - not archived: the live page's verdict stands
                pass
        if not r["found"] and f.get("url") in FETCHED:
            r["found"] = quote_found(f.get("quote", ""), norm(FETCHED[f["url"]]))
            r["read"] = "web_fetch"
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
        for k in ("melody", "emphasis", "direction", "pace", "energy", "beat"):
            if s.get(k):
                seg[k] = s[k]
        segs.append(seg)
    segs[-1]["pause_after"] = 0.0
    return dict(title=script.get("title"), voice=script.get("voice") or "atlas", band=band, pace=script.get("pace") or "steady",
                energy=script.get("energy") or "mid", names=list(script.get("names") or []), segments=segs)
