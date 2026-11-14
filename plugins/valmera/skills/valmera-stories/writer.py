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
            out.append({"type": "input_image", "image_url": url, "detail": b.get("detail") or "high"})
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
        final = None
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=30)) as cl, \
                    cl.stream("POST", "https://api.openai.com/v1/responses", headers=headers, json=body) as r:
                if r.status_code >= 400:
                    openai_cache.record(model, "writer:" + what, status="http_error", usd=0.0,
                                        http_status=r.status_code, attempt=attempt + 1, force=True)
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
            openai_cache.record(model, "writer:" + what, status="stream_without_usage", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
        except (httpx.TransportError, httpx.TimeoutException):
            if final is not None:  # terminal usage arrived before the connection closed
                break
            openai_cache.record(model, "writer:" + what, status="transport_error", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
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
    system += f"\n\n# The bar\n{references()}\n\n"
    system += "\n\n" + ((f"# Form\n{F['name']}, {F['seconds'][0]}-{F['seconds'][1]} s, {F['shape']}.\n\n"
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
               "[URLs you read], \"strength\": 1-10}."))
    content = ((f"# Styles this film can take\n{menu(cards)}\n\n"
               f"# Already made (never repeat a subject)\n{json.dumps(list(avoid), ensure_ascii=False)}\n\n") +
               (f"# What the owner asked for\n{line}\nEvery idea must be about this. When it names a person, a company or a "
                  "topic, find the strongest true stories inside it (a single moment, decision or turn, not a biography).\n\n" if line else
                  "# No subject given\nChoose the subjects yourself: founders, companies, inventions and moments in business or "
                  "technology history that a curious viewer half knows, told through one decisive moment.\n\n"))
    text, use = ask_web(system, content, effort=effort, max_tokens=24000, searches=14, fetches=10, what="find ideas")
    got = json_block(text)
    ideas = got.get("ideas") if isinstance(got, dict) else got
    ids = {c["id"] for c in cards}
    return [i for i in ideas or [] if i.get("style") in ids], use


# ----------------------------------------------------------------- the script

REEL_DATA = (
    '"era" in each segment: the year that segment speaks from, as a number (the year of the events it tells; the present year '
    'for a segment about today). "see" in each segment: one to four {"thing": a name from `named`, "as_of": the story time of '
    'that thing as this line means it, a year plus its version (e.g. "the finished 1995 release, not the 1993 test reel"), '
    '"must_show": one sentence, what a picture must plainly show to carry THIS line, "moment": the scene or event the line '
    'describes when it describes one (e.g. "the window fall in the finished film"), else null}, in the order the line says '
    'them; "pictures" is then just the list of those things. "finish" in each segment: bw|gate|crt|none, and "finish_rank": '
    '1 for the segment that most needs it, 2 next, and so on: black and white, a film gate or scanlines are a device for a '
    'memory, the past cut in, a low point, never a look for the whole film; the device budget is 12 to 25 percent of the voice '
    'time, never on the payoff or the close. "colour_turn": the index of the one segment from which the film stays in colour '
    '(the turn or the release). In this style each item of "show" is {"text": as printed, "role": figure|name|key|chapter}: the '
    'payoff carries its figure (role figure), each person (is=person) gets a name item at the line that first says them, at '
    'most one key word on the turn, a chapter word only at a joint of the story. "collage": true on the two to four segments '
    'that name or list several things at once: their pictures land as stacked prints. Mark beats truthfully: one "low" where '
    'the story is at its worst, one "turn", the "payoff" with its figure. Name in "named" every person, place, company, '
    'product and event a camera filmed, with Commons searches; the footage engine finds the video of each.')
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
    system += f"\n\n# The bar\n{references()}\n\n"
    system += "\n\n" + (("Read the sources again with the web tools and check every fact. The first segment is the hook: it must work in the "
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
               "\"named\": [{\"name\", \"is\", \"searches\", \"must_show\"} for every person, place or thing the film shows], ") +
               (STYLE_DATA.get(card["id"], "")) +
               ("}\n"
               "Every digit that appears in `show` or in the style's data must appear in some fact's quote. Every quote is checked "
               "by fetching its URL with a plain script, so cite pages a script can read (Wikipedia, Wikimedia, archive.org, "
               "government and university pages, a company's own newsroom) and read each one with web_fetch before quoting it; "
               "pages behind a paywall or a bot wall (Britannica, most newspapers) and PDF files cannot be checked and will be dropped: "
               "cite the HTML page that says the same thing."))
    content = ((f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Form\n{F['name']}: {F['words'][0]}-{F['words'][1]} spoken words in {F['segments'][0]}-{F['segments'][1]} segments "
               f"(about {F['seconds'][0]}-{F['seconds'][1]} s at 175 words a minute). {F['shape']}.\n\n"
               f"# Voices\n{json.dumps({k: VOICES[k] for k in voices if k in VOICES})}\n\n") +
               (f"# Notes from the last attempt\n{notes}\n\n" if notes else ""))
    text, use = ask_web(system, content, effort=effort, max_tokens=32000, searches=8, fetches=10, what="write")
    return json_block(text), use


# ----------------------------------------------------------------- the picture brief on the final words

ANNOTATE_MODEL = "gpt-6.1-sol"           # pinned to OpenAI: writer.ask would turn to Anthropic when its key is present
FINISHES = ("bw", "gate", "crt", "none")
ROLES = ("figure", "name", "key", "chapter")
DEVICE_SHARE = (0.12, 0.25)              # the judge: black and white, a gate or scanlines on 12-25% of the voice, never the payoff or close
PHRASE_MAX = 5

ANNOTATE_SYSTEM = """You are the picture editor of a narrated vertical story film cut like the best documentary reels. The script is final and checked: you never change a word of it. You read it and write the data the cut needs.

For each segment:
- "see": one to four items, in the order the line says them: {"thing": exactly one name from the named list, "as_of": the story time of that thing as THIS line means it, a year plus its version or state (e.g. "the finished 1995 release, not the 1993 test reel"; "Pixar's Point Richmond office in 1993, not the later Emeryville campus"), "must_show": one sentence, only what must be VISIBLE in a picture for it to carry this line (who or what, doing what, in which version); never the film's treatment of it (colour, black and white, muted, grain, composition, framing): treatment belongs to "finish" alone, "moment": the scene or event the line describes when it describes one (e.g. "Woody knocking Buzz out of the window in the finished 1995 film"), else null, "search": one photo search an archivist would type to find a photograph of exactly this thing at this time (e.g. "John Lasseter 1995"; "Pixar Point Richmond 1993"), "still_only": true when the true picture of this line is still by its nature (a storyboard, a document, a drawing, a letter, a printed page), else false}. A line about a scene of a film names that scene as its moment, in the version the line means.
- "list": true when the line itself reads as a list of several distinct things or a quick montage of them (the cut may burst through them in a few frames each), else false; "list_items": the things of the list, in order, as see things, when list is true.
- "finish": bw|gate|crt|none and "finish_rank" (1 = the segment that most needs it). Black and white, a film gate or scanlines are a device for a memory, an early version, the past cut in, a low point; never a look for the whole film. The devices together cover 12 to 25 percent of the spoken words, never the payoff or the close, never a segment at or after the colour turn.
- "show": the segment's display items, each {"text": as printed, "role": figure|name|key|chapter, "word_i": the index of the spoken word it lands on (a figure: the first word of the spoken figure; a name: the first word of the name; a chapter: the first word of the segment), "big": the part of the text set large (e.g. "$362M"), "small": the rest set small under it (e.g. "box office, 1995"), or null}. Every item needs its word_i. Keep every given item's text exactly. You may add: the payoff's figure if it lacks one (a figure printed with digits only when the same figure stands in a fact's quote), a name item for each person (is=person) at the line that first says them, one key word on the turn, and a short chapter title (two to four words) on the segment that opens each new section (the turn, the low point, the payoff). Display type lands on about one picture in six across the film: a figure card for each verified figure, a name for each person, a chapter at each turn. At most three items a segment.
- "phrases": the caption lines: lists of word indices, each 1 to 5 consecutive indices, together covering every word of the segment once and in order. Never split a name, a figure, a date or a noun phrase across two lines; a line reads as one unit of sense.

And once for the film: "colour_turn": the index of the one segment from which the film stays in colour (the turn or the release); "true_pictures": for every named thing, where a faithful picture of it can exist: "stills" (a free photo archive holds true photographs of it: a real person, a building, an object), "footage" (it exists faithfully only in the work's own moving pictures: an animated film's characters and scenes, whose photographs are toys, rides, statues and costumes), or "both".

Answer with one JSON object only: {"colour_turn": int, "true_pictures": {"<named thing>": "stills"|"footage"|"both", ...}, "segments": [{"i": int, "see": [...], "list": bool, "list_items": [...], "finish": ..., "finish_rank": int or null, "show": [...], "phrases": [[int, ...], ...]}]}"""

SEE_SYSTEM = """You are the picture editor of a narrated documentary film. The script is final; you never change a word. For the ONE segment you are given, write what a picture must show to carry it.

Answer with one JSON object only: {"see": [one to four items, in the order the line says them: {"thing": exactly one name from the named list, "as_of": the story time of that thing as THIS line means it, a year plus its version or state, "must_show": one sentence, only what must be VISIBLE (never colour, black and white or composition), "moment": the scene or event the line describes, or null, "search": one photo search an archivist would type to find exactly this thing at this time, "still_only": true when the true picture of this line is still by its nature (a storyboard, a document, a drawing), else false}]}"""
TRUE_PICTURES = ("stills", "footage", "both")


def _ann_content(sc, idea_full, problems=None):
    rows = []
    for k, s in enumerate(sc["segments"]):
        ws = str(s.get("say", "")).split()
        given = [dict(text=str(x.get("text") if isinstance(x, dict) else x), role=x.get("role") if isinstance(x, dict) else None)
                 for x in s.get("show") or []]
        rows.append(f"{k}. beat={s.get('beat')} era={s.get('era')} show={json.dumps(given, ensure_ascii=False)}\n   words: "
                    + " ".join(f"{j}:{w}" for j, w in enumerate(ws)))
    named = [dict(name=n.get("name"), is_=n.get("is"), must_show=n.get("must_show")) for n in sc.get("named") or []]
    facts = [dict(id=f.get("id"), claim=f.get("claim"), quote=f.get("quote")) for f in sc.get("facts") or []]
    idea = {k: (idea_full or {}).get(k) for k in ("title", "story", "turn", "payoff") if (idea_full or {}).get(k)}
    out = (f"# The story\n{json.dumps(idea, ensure_ascii=False)}\n\n# Named things\n{json.dumps(named, ensure_ascii=False)}\n\n"
           f"# Facts\n{json.dumps(facts, ensure_ascii=False)}\n\n# Segments (word indices start at 0 in each segment)\n" + "\n".join(rows))
    if problems:
        out += "\n\n# Your last answer had these problems; answer again in full with them fixed\n- " + "\n- ".join(problems[:40])
    return out


def _clean_see(v, names):
    """The valid items of a see list (structure only: thing in the named list, the fields present)."""
    out = []
    for x in v if isinstance(v, list) else []:
        if not isinstance(x, dict) or x.get("thing") not in names or not str(x.get("as_of") or "").strip():
            continue
        m, q = x.get("moment"), x.get("search")
        out.append(dict(thing=x["thing"], as_of=str(x["as_of"]).strip(), must_show=str(x.get("must_show") or "").strip(),
                        moment=str(m).strip() if isinstance(m, str) and m.strip() else None,
                        search=str(q).strip()[:120] if isinstance(q, str) and q.strip() else None,
                        still_only=x.get("still_only") is True))
    return out[:4]


def _clean_phrases(v, n):
    """[[a, b)] spans when the lists of indices are contiguous, 1..PHRASE_MAX long and cover 0..n-1 in order; else None."""
    if not isinstance(v, list) or not v:
        return None
    spans, at = [], 0
    for ph in v:
        if not isinstance(ph, list) or not ph or len(ph) > PHRASE_MAX or not all(isinstance(q, int) and not isinstance(q, bool) for q in ph):
            return None
        if ph != list(range(at, at + len(ph))):
            return None
        spans.append([at, at + len(ph)])
        at += len(ph)
    return spans if at == n else None


def _big_small(x, m):
    """The model's split of a display item into a large part and a small line under it, kept only when the two parts are
    the item's own text (structure: the large part stands in the text, nothing is invented)."""
    big, small = m.get("big"), m.get("small")
    text = str(x.get("text") or "")
    if isinstance(big, str) and big.strip() and big.strip() in text and big.strip() != text.strip():
        x["big"] = big.strip()
        x["small"] = str(small).strip()[:60] if isinstance(small, str) and small.strip() else None
    return x


def _valid_i(v, n):
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n


def _merge_ann(sc, got):
    """The script with the model's annotation laid in where it is structurally valid (what the script already holds
    validly is kept), and the list of what is still missing or invalid. `say` is never touched."""
    names = {n.get("name") for n in sc.get("named") or [] if n.get("name")}
    segs = {}
    for g in (got or {}).get("segments") or []:
        if isinstance(g, dict) and _valid_i(g.get("i"), 10 ** 6):
            segs[g["i"]] = g
    out = json.loads(json.dumps(sc))
    problems = []
    nseg = len(out["segments"])
    tp = (got or {}).get("true_pictures")
    tp = tp if isinstance(tp, dict) else {}
    for n in out.get("named") or []:
        # where a faithful picture of each thing can exist (the model's reading; a missing or invalid value means both)
        if tp.get(n.get("name")) in TRUE_PICTURES:
            n["true_pictures"] = tp[n.get("name")]
        elif n.get("true_pictures") not in TRUE_PICTURES:
            n["true_pictures"] = "both"
    for k, s in enumerate(out["segments"]):
        g = segs.get(k) or {}
        n = len(str(s.get("say", "")).split())
        # the model's brief when it is valid (an older script's brief may lack the photo search or carry treatment in
        # must_show); else what the script already holds
        see = _clean_see(g.get("see"), names) or _clean_see(s.get("see"), names)
        if see:
            s["see"] = see
            s["pictures"] = list(dict.fromkeys(x["thing"] for x in see))
        else:
            problems.append(f"segment {k}: no valid see item (thing must be exactly a name from the named list, as_of given)")
        if s.get("finish") not in FINISHES:
            if g.get("finish") in FINISHES:
                s["finish"] = g["finish"]
                fr = g.get("finish_rank")
                s["finish_rank"] = int(fr) if isinstance(fr, (int, float)) and not isinstance(fr, bool) else None
            else:
                problems.append(f"segment {k}: finish must be one of {list(FINISHES)}")
        if not isinstance(s.get("list"), bool) and isinstance(g.get("list"), bool):
            s["list"] = g["list"]
            if g["list"]:
                s["list_items"] = [x for x in g.get("list_items") or [] if x in names][:8]
        given = [dict(x) if isinstance(x, dict) else dict(text=str(x)) for x in s.get("show") or []]
        texts = [str(x.get("text") or "") for x in given]
        answer = [x for x in g.get("show") or [] if isinstance(x, dict) and str(x.get("text") or "").strip()]
        for x in given:
            m = next((y for y in answer if str(y.get("text")) == x["text"]), None) or {}
            if x.get("role") not in ROLES and m.get("role") in ROLES:
                x["role"] = m["role"]
            _big_small(x, m)
            if not _valid_i(x.get("word_i"), n):
                x.pop("word_i", None)
                if _valid_i(m.get("word_i"), n):
                    x["word_i"] = m["word_i"]
                else:
                    problems.append(f"segment {k}: show item {x['text']!r} needs word_i, an index 0..{n - 1} of its spoken word")
        for y in answer:
            if str(y.get("text")) in texts or len(given) >= 3:
                continue
            if y.get("role") in ROLES and _valid_i(y.get("word_i"), n):
                given.append(_big_small(dict(text=str(y["text"]).strip(), role=y["role"], word_i=y["word_i"], added=True), y))
                texts.append(given[-1]["text"])
            else:
                problems.append(f"segment {k}: added item {y.get('text')!r} needs a role in {list(ROLES)} and a word_i 0..{n - 1}")
        s["show"] = given
        ph = s.get("phrases") if isinstance(s.get("phrases"), list) and s["phrases"] and \
            all(isinstance(q, list) and len(q) == 2 for q in s["phrases"]) else None
        ph = ph or _clean_phrases(g.get("phrases"), n)
        if ph:
            s["phrases"] = ph
        else:
            s.pop("phrases", None)
            problems.append(f"segment {k}: phrases must be lists of 1-{PHRASE_MAX} consecutive word indices covering 0..{n - 1} in order")
    if not _valid_i(out.get("colour_turn"), nseg):
        ct = (got or {}).get("colour_turn")
        if _valid_i(ct, nseg):
            out["colour_turn"] = ct
        else:
            problems.append(f"colour_turn must be a segment index 0..{nseg - 1}")
    return out, problems


def device_budget(sc, weights=None, share=DEVICE_SHARE):
    """The writer's finishes held to the judge's budget: none on the payoff, the close or from colour_turn on; above
    the top of the band only the best ranked stay. weights: each segment's voice time (word counts when not given).
    Returns the device share after the budget (a measure of the given weights)."""
    segs = sc.get("segments") or []
    w = list(weights) if weights else [len(str(s.get("say", "")).split()) for s in segs]
    ct = sc.get("colour_turn")
    for k, s in enumerate(segs):
        if s.get("beat") in ("payoff", "close") or (isinstance(ct, int) and k >= ct):
            if s.get("finish") not in (None, "none"):
                s["finish_cut"] = s["finish"]
            s["finish"] = "none"
    total = max(sum(w), 1e-9)
    dev = [k for k, s in enumerate(segs) if s.get("finish") in ("bw", "gate", "crt")]
    dev.sort(key=lambda k: (segs[k].get("finish_rank") if isinstance(segs[k].get("finish_rank"), (int, float)) else 99, k))
    kept, used = [], 0.0
    for k in dev:
        if (used + w[k]) / total <= share[1] + 1e-9:
            kept.append(k)
            used += w[k]
        else:
            segs[k]["finish_cut"] = segs[k]["finish"]
            segs[k]["finish"] = "none"
    return round(used / total, 3)


def annotate_segment(sc, k, idea_full=None, ask=None):
    """(see items, usage) for ONE segment that came back without a picture brief: a call pinned to ANNOTATE_MODEL on
    OpenAI (ask_openai, never ask(), so Anthropic spend stays at zero)."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=8000, what="annotate segment",
                                                     model=ANNOTATE_MODEL))
    names = {n.get("name") for n in sc.get("named") or [] if n.get("name")}
    s = sc["segments"][k]
    named = [dict(name=n.get("name"), is_=n.get("is"), must_show=n.get("must_show")) for n in sc.get("named") or []]
    idea = {q: (idea_full or {}).get(q) for q in ("title", "story", "turn", "payoff") if (idea_full or {}).get(q)}
    near = [dict(i=j, say=sc["segments"][j].get("say")) for j in range(max(k - 1, 0), min(k + 2, len(sc["segments"])))]
    content = (f"# The story\n{json.dumps(idea, ensure_ascii=False)}\n\n# Named things\n{json.dumps(named, ensure_ascii=False)}\n\n"
               f"# Around it\n{json.dumps(near, ensure_ascii=False)}\n\n# The segment\n{k}. beat={s.get('beat')} era={s.get('era')}: {s.get('say')}")
    text, use = ask(SEE_SYSTEM, content)
    try:
        got = json_block(text)
    except ValueError:
        return [], use
    return _clean_see(got.get("see"), names), use


def annotate(sc, idea_full=None, ask=None, verify_fn=None, log=print, problems=None):
    """(script, report): the final script with its picture brief (see), its devices (finish, colour_turn), its display
    items anchored to word indices (show[].word_i) and its caption phrases, written by one model call (a second when
    the first answer is not structurally valid). `say` is never changed; only valid output is laid in; the result is
    verified again (every figure on screen must stand in a verified quote), and items the model added that do not
    verify are taken out. ask: (system, content) -> (text, usage), OpenAI only."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=24000, what="annotate",
                                                     model=ANNOTATE_MODEL))
    verify_fn = verify_fn or verify
    spend = []
    problems = list(problems) if problems else None
    out = sc
    for attempt in range(2):
        text, use = ask(ANNOTATE_SYSTEM, _ann_content(sc, idea_full, problems))
        spend.append(use)
        try:
            got = json_block(text)
        except ValueError:
            problems = ["the answer was not one JSON object"]
            continue
        out, problems = _merge_ann(out, got)          # a repair keeps what the first answer got right
        if not problems:
            break
        log(f"annotate: {len(problems)} problems: " + "; ".join(problems[:4]))
    say0 = [s.get("say") for s in sc["segments"]]
    if [s.get("say") for s in out["segments"]] != say0:
        raise RuntimeError("annotate changed the words")
    share = device_budget(out)
    device_added = []
    if share < DEVICE_SHARE[0] and out.get("segments"):
        # under the judge's floor: the writer is asked once more for ONE device beat, where it chooses; the budget's cap
        # (DEVICE_SHARE[1]) and its exclusions (payoff, close, from colour_turn on) still hold
        share, device_added, use = device_repair(out, idea_full, ask, share)
        if use is not None:
            spend.append(use)
    rep = verify_fn(out)
    dropped = []
    if not rep.get("ok"):
        # only the added items whose own numbers stand in no verified quote go; the other added names, chapters and key
        # words stay. When that does not verify, every added item goes, as before.
        unb = set(rep.get("numbers_unbacked") or [])
        for s in out["segments"]:
            bad = [x for x in s.get("show") or [] if x.get("added") and set(digits(str(x.get("text") or ""))) & unb]
            dropped += [x["text"] for x in bad]
            s["show"] = [x for x in s.get("show") or [] if not any(x is b for b in bad)]
        rep = verify_fn(out)
    if not rep.get("ok"):
        for s in out["segments"]:
            keep_ = [x for x in s.get("show") or [] if not x.get("added")]
            dropped += [x["text"] for x in s.get("show") or [] if x.get("added")]
            s["show"] = keep_
        rep = verify_fn(out)
    for s in out["segments"]:
        for x in s.get("show") or []:
            x.pop("added", None)
    ok = bool(rep.get("ok")) and not problems
    return out, dict(ok=ok, problems=problems or [], device_share_words=share, dropped_unverified=dropped, verify=rep, spend=spend,
                     device_added=device_added)


def device_repair(out, idea_full, ask, share):
    """(share, [segments given a device], usage): one more annotate call asking for one device beat when the devices
    cover under DEVICE_SHARE[0] of the words. The first valid device the answer gives a segment without one (not the
    payoff or close, before colour_turn) is laid in, then device_budget holds the cap again."""
    probs = [f"the devices (bw, gate, crt) cover {share:.0%} of the spoken words, under {DEVICE_SHARE[0]:.0%}: choose one more segment "
             "for a device (a memory, an early version, the past cut in, a low point) and give it its finish and finish_rank; "
             "never the payoff, the close, or a segment at or after colour_turn. Answer in full."]
    try:
        text, use = ask(ANNOTATE_SYSTEM, _ann_content(out, idea_full, probs))
    except Exception:  # noqa: BLE001 - the repair is one more ask; the film keeps its devices
        return share, [], None
    try:
        got = json_block(text)
    except ValueError:
        return share, [], use
    ct = out.get("colour_turn")
    segs = out["segments"]
    cands = []
    for g in got.get("segments") or []:
        if not isinstance(g, dict) or not _valid_i(g.get("i"), len(segs)) or g.get("finish") not in ("bw", "gate", "crt"):
            continue
        k = g["i"]
        s = segs[k]
        if s.get("finish") not in (None, "none") or s.get("beat") in ("payoff", "close") or (isinstance(ct, int) and k >= ct):
            continue
        fr = g.get("finish_rank")
        cands.append((fr if isinstance(fr, (int, float)) and not isinstance(fr, bool) else 99, k, g["finish"]))
    if not cands:
        return share, [], use
    _, k, fin = min(cands)
    segs[k]["finish"], segs[k]["finish_rank"] = fin, 1
    segs[k].pop("finish_cut", None)
    share = device_budget(out)
    return share, ([k] if segs[k].get("finish") == fin else []), use


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
        out += [str(x.get("text") if isinstance(x, dict) else x) for x in s.get("show") or []]
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
