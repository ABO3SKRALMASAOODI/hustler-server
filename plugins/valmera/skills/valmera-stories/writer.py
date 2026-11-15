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
    settle(script)                         -> verify, then a failed fact is sourced again (one call),
                                              else its lines are rewritten without it or dropped;
                                              only an unverifiable hook, turn, payoff or close stops the film
    spine_bank(idea)                       -> VERIFY FIRST (15 Nov): the idea's spine (hook, turn, payoff,
                                              close) researched as claims and verified before a word is
                                              written: its own sources, then up to three search rounds a
                                              claim with new queries, primary sources first
    write_from_bank(idea, card, form, bank) -> the script written from the verified bank alone (no web)
    hold_spine(script, bank)               -> every line judged on its quotes; each spine beat must carry
                                              the idea's point, rewritten on the bank or searched again
    final_support(script)                  -> the last text gate after the ear

The model decides what a story is and how it is told; nothing here reads its words by rule.
The key comes from this process's environment (run under .valmera/claude_trial_env.sh).
"""
import html
import html.parser
import json
import os
import re
import gzip
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib

MODEL = "claude-opus-5-5"
# USD per token: input, cache write, cache read, output; and per web search
PRICE = (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6)
SEARCH_USD = 0.01
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


def ask_openai(system, content, *, effort="high", max_tokens=32000, searches=0, what="writer", model=None, timeout=2400,
               max_tool_calls=None):
    """(text, usage): one streamed Responses call; with `searches`, the model may use OpenAI's web search. The key comes
    from this process's environment (OPENAI_API_KEY) and is never printed or stored. `max_tool_calls` caps the searches
    of one call (each one feeds its results back as input: uncapped, an ideas call ran 21 searches and read 191k tokens)."""
    import httpx
    model = model or OA_MODEL
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": _oa_parts(content)}],
            "max_output_tokens": int(max_tokens), "reasoning": {"effort": OA_EFFORT.get(effort, "high")}, "stream": True, "store": False}
    if searches:
        body["tools"] = [{"type": "web_search"}]
        body["include"] = ["web_search_call.action.sources"]
        if max_tool_calls:
            body["max_tool_calls"] = int(max_tool_calls)
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
    text, n_search, cited, queries = "", 0, [], []
    for item in final.get("output") or []:
        if item.get("type") == "web_search_call":
            n_search += 1
            act = item.get("action") or {}
            queries += [q for q in [act.get("query")] + list(act.get("queries") or []) if isinstance(q, str) and q.strip()]
            for s in (act.get("sources") or []):
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
                      incomplete=(final.get("incomplete_details") or {}).get("reason"),
                      **(dict(queries=list(dict.fromkeys(queries))) if queries else {}))


SEEN_URLS = set()


def ask_web(system, content, *, effort="high", max_tokens=32000, searches=12, fetches=12, what="writer", max_tool_calls=None):
    """(text, usage). The model may search and fetch; a paused turn is continued until it ends. On OpenAI,
    `max_tool_calls` caps the searches (ask_openai)."""
    if provider() == "openai":
        return ask_openai(system, content, effort=effort, max_tokens=max(max_tokens, 48000), searches=searches or fetches, what=what,
                          max_tool_calls=max_tool_calls)
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


IDEAS_N = 4                # fewer, better candidates: the run builds the first one whose pictures exist (15 Nov: 6 cost ~$0.9)
IDEAS_SEARCHES = 8         # each search feeds its pages back as input: uncapped, one call read 185-280k tokens


def find_ideas(form, cards, avoid=(), line=None, n=IDEAS_N, effort="high", delivered=""):
    """Ranked ideas for the styles in `cards`, found on the web by the model itself. The system prompt is static per
    form (the bar, the form, the answer's shape) so it is a cached prefix; what varies (the styles left, what was made,
    the owner's line) is in the content, and the searches are capped (15 Nov, one Woody run each: 21 searches, 191k tokens
    in, $0.77 before; 9 searches, 75k in, $0.37 after, the same top idea at strength 10)."""
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
               "Search with purpose (about eight searches: find the strongest candidates, then check them; never many weak ones) and "
               "think like an editor of the best founder-story accounts. Prefer stories most people half know with "
               "a detail they do not, an object that holds the story, a human turn (a betrayal, a humiliation, a bet), and a payoff "
               "with a figure. Avoid the stories the references already told (SpaceX's fourth launch, Airbnb's cereal, Xerox PARC, "
               "the traitorous eight, the PayPal mafia). Check that the named people and events were filmed (an interview, a "
               "keynote, a news report, archive footage) and have photos on Commons.\n\n"
               "Truth and grip are one test: a story qualifies only when its spine (the hook's collision, the turn, the payoff's "
               "figure and who paid it, the close's irony) is stated in so many words on pages a plain script can read. Before you "
               "offer an idea, find for each spine claim one whole sentence that states it, primary sources first (a court record "
               "or judgment, a filing, the company's or the person's own statement, a contemporary news report), else Wikipedia "
               "or another free reference page. An idea whose point no readable page states is not offered.\n\n"
               f"Return JSON only: {{\"ideas\": [{n} items, best first]}}. Each: {{\"id\": \"i1\", \"title\": a plain title, "
               "\"style\": one style id from the list, \"why_style\": what in this story that style needs, \"hook\": the first sentence "
               "as it would be spoken, \"story\": the story in four to six plain sentences with its figures, \"turn\": the human turn, "
               "\"payoff\": the payoff with its figure, \"spine\": [one or two items for each beat hook, turn, payoff and close: "
               "{\"beat\": hook|turn|payoff|close, \"claim\": the one fact this beat cannot do without, as one plain checkable "
               "sentence (who did what to whom, when, the figure; no detail the point does not need): the hook's claims are both "
               "halves of its collision, the payoff's claim names the figure and who paid or got it, the close's claim is the fact "
               "that makes the irony true, \"source\": {\"url\": a readable page that states it, \"quote\": one whole sentence "
               "copied word for word from that page that states it, never a bare date, number or heading}}], "
               "\"named\": [up to 8 {\"name\": usual English name, \"is\": person|place|company|"
               "thing|event, \"searches\": one to three Wikimedia Commons searches, most specific first, \"must_show\": one sentence: what "
               "a picture must visibly show to count}], \"photo\": for one-photograph only, {\"file\": the exact Commons file title "
               "starting File:, \"licence\": its licence, \"why\": why this one picture holds the whole story} else null, \"sources\": "
               "[URLs you read that a plain script can read too: Wikipedia, archive.org, a studio's or company's own site, free trade "
               "press; never a document mirror (studylib, scribd), a paywall, a bot wall or a PDF: every fact is checked by fetching "
               "its page], \"strength\": 1-10}."))
    content = ((f"# Styles this film can take\n{menu(cards)}\n\n"
               f"# Already made\n{json.dumps(list(avoid), ensure_ascii=False)}\nThe owner's rule: never the exact same idea told the same "
               "way as one of these (the same story through the same beats, even under a new title or a new look). A new angle "
               "or a new version of the same subject is welcome.\n\n") + (delivered or "") +
               (f"# What the owner asked for\n{line}\nEvery idea must be about this. When it names a person, a company or a "
                  "topic, find the strongest true stories inside it (a single moment, decision or turn, not a biography).\n\n" if line else
                  "# No subject given\nChoose the subjects yourself: founders, companies, inventions and moments in business or "
                  "technology history that a curious viewer half knows, told through one decisive moment.\n\n"))
    text, use = ask_web(system, content, effort=effort, max_tokens=24000, searches=14, fetches=10, what="find ideas",
                        max_tool_calls=IDEAS_SEARCHES)
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
    '(the turn or the release). In this style each item of "show" is {"text": as printed, "role": figure|name|key|chapter|ordinal}: the '
    'payoff carries its figure (role figure), each person (is=person) gets a name item at the line that first says them, at '
    'most one key word on the turn, a chapter word only at a joint of the story; "ordinal" only for the numbers of a list the film '
    'itself counts out ("1.", "2.", "3." on its items, one number each, from 1), never a rank, a date or a quantity: a rank ("#1", '
    '"No. 1") is a figure. "collage": true on the two to four segments '
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
               "words for the actor, \"facts\": ids of the facts it states, \"quotes\": every span of say that presents "
               "someone's own words (in quotation marks, or as what they said, recalled, wrote or asked) as {\"words\": the span "
               "exactly as in say, \"who\": whose words, \"fact\": the id of the fact whose quote holds those words word for word}, "
               "or [] (never put words in anyone's mouth that a fact's quote does not hold verbatim)}], \"facts\": [{\"id\": \"f1\", \"claim\": the fact in plain "
               "words, \"quote\": a whole sentence copied exactly, word for word, from the page at url that states it (never a bare "
               "date, number or fragment: a quote of fewer than four words besides its numbers is refused), \"url\": the page, "
               "\"also\": [other readable pages you read that carry the same sentence, or []]}], "
               "\"named\": [{\"name\", \"is\", \"searches\", \"must_show\"} for every person, place or thing the film shows], ") +
               (STYLE_DATA.get(card["id"], "")) +
               ("}\n"
               "Every digit that appears in `show` or in the style's data must appear in some fact's quote (only the numbers of a list the "
               "film itself counts out, marked role ordinal, are exempt; a rank such as #1 is a figure and needs its quote, or say it in "
               "words). Every quote is checked "
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
ROLES = ("figure", "name", "key", "chapter", "ordinal")
DEVICE_SHARE = (0.12, 0.25)              # the judge: black and white, a gate or scanlines on 12-25% of the voice, never the payoff or close
PHRASE_MAX = 5

ANNOTATE_SYSTEM = """You are the picture editor of a narrated vertical story film cut like the best documentary reels. The script is final and checked: you never change a word of it. You read it and write the data the cut needs.

For each segment:
- "see": one to four items, in the order the line says them: {"thing": exactly one name from the named list, "as_of": the story time of that thing as THIS line means it, a year plus its version or state (e.g. "the finished 1995 release, not the 1993 test reel"; "Pixar's Point Richmond office in 1993, not the later Emeryville campus"), "must_show": one sentence, only what must be VISIBLE in a picture for it to carry this line (who or what, doing what, in which version); never the film's treatment of it (colour, black and white, muted, grain, composition, framing): treatment belongs to "finish" alone, "moment": the scene or event the line describes when it describes one (e.g. "Woody knocking Buzz out of the window in the finished 1995 film"), else null, "search": one photo search an archivist would type to find a photograph of exactly this thing at this time (e.g. "John Lasseter 1995"; "Pixar Point Richmond 1993"), "still_only": true when the true picture of this line is still by its nature (a storyboard, a document, a drawing, a letter, a printed page), else false}. A line about a scene of a film names that scene as its moment, in the version the line means.
- "list": true when the line itself reads as a list of several distinct things or a quick montage of them (the cut may burst through them in a few frames each), else false; "list_items": the things of the list, in order, as see things, when list is true.
- "finish": bw|gate|crt|none and "finish_rank" (1 = the segment that most needs it). Black and white, a film gate or scanlines are a device for a memory, an early version, the past cut in, a low point; never a look for the whole film. The devices together cover 12 to 25 percent of the spoken words, never the payoff or the close, never a segment at or after the colour turn.
- "show": the segment's display items, each {"text": as printed, "role": figure|name|key|chapter|ordinal (ordinal only for the numbers of a list the film itself counts out, "1.", "2.", from 1; a rank such as #1 is a figure), "word_i": the index of the spoken word it lands on (a figure: the first word of the spoken figure; a name: the first word of the name; a chapter: the first word of the segment), "big": the part of the text set large (e.g. "$362M"), "small": the rest set small under it (e.g. "box office, 1995"), or null}. Every item needs its word_i. Keep every given item's text exactly. You may add: the payoff's figure if it lacks one (a figure printed with digits only when the same figure stands in a fact's quote), a name item for each person (is=person) at the line that first says them, one key word on the turn, and a short chapter title (two to four words) on the segment that opens each new section (the turn, the low point, the payoff). Display type lands on about one picture in six across the film: a figure card for each verified figure, a name for each person, a chapter at each turn. At most three items a segment.
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
    """A page's readable text: what a reader sees, never its scripts, styles, footnote marks or hidden templates."""
    SKIP = ("script", "style", "sup", "noscript", "template", "svg")

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)


def norm(s):
    """Lower case, letters and digits only, single spaces; thousands separators dropped."""
    s = html.unescape(str(s)).lower().replace("’", "'")
    s = re.sub("(?<=\\d)[,\u202f\u00a0](?=\\d{3})", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


APOSTROPHES = "\u2019\u2018\u02bc\u02b9\u0060\u00b4\u2032\uff07"
_SPEECH_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def speech_words(s):
    """The words of a span, for identity only: Unicode NFKC, case folded, every apostrophe one character, thousands
    separators dropped; a word is a run of letters or digits, with apostrophes inside it ("I'm" is one word, never
    "I" and "m"). Never a reading of meaning: it decides only whether the same words stand in the same order."""
    s = unicodedata.normalize("NFKC", html.unescape(str(s or ""))).casefold()
    for a in APOSTROPHES:
        s = s.replace(a, "'")
    s = re.sub("(?<=\\d)[,\u202f\u00a0](?=\\d{3})", "", s)
    return _SPEECH_WORD.findall(s)


def has_words(text, span):
    """True when the span's words stand in text as one contiguous run of whole words (never a substring: "I'm in" is
    not in "I'm interested in the part")."""
    a, b = speech_words(text), speech_words(span)
    n = len(b)
    return bool(b) and any(a[k:k + n] == b for k in range(len(a) - n + 1))


MIN_QUOTE_WORDS = 4


def quote_words(q):
    """The quote's words that hold no digit (a word with a digit is a figure, a year or a date): a structural count."""
    return [w for w in speech_words(q) if not any(c.isdigit() for c in w)]


def thin_quote(q):
    """Why a quote is too thin to verify anything (None when it is a meaningful fragment): fewer than MIN_QUOTE_WORDS
    words besides its numbers, so a bare number, year or date ("1983", "on 13th October 2000", "Thursday 4th October
    2001") never passes as a verified quote. Structure only, never a reading of meaning."""
    n = len(quote_words(q))
    if n < MIN_QUOTE_WORDS:
        return (f"a quote must be a meaningful fragment of at least {MIN_QUOTE_WORDS} words besides its numbers, never a "
                f"bare number or date (this one has {n})")
    return None


def _both(raw):
    """HTML to (the normalised text a quote is measured on, the readable text as a reader sees it, case and punctuation
    kept, for a model to copy sentences from)."""
    p = _Text()
    p.feed(raw)
    p.close()
    t = " ".join(" ".join(p.out).split())
    return norm(t), t


def readable(raw):
    """HTML to the normalised text a reader sees (quote presence is measured on this, never on the markup)."""
    return _both(raw)[0]


_PAGES = {}
_RAW = {}                  # {url: the page's readable text with its case and punctuation}: what an extraction call reads
# pages the web_fetch tool read during this process: fetched by Anthropic's servers, not written by the model, so a
# quote is also checked against them when a site refuses this machine (a 403 to a script)
FETCHED = {}
# every fetch made for a page, in order: {url: [{"via", "url", "status"}]}; verify reports them per fact
TRIED = {}
_VIA = {}

# a browser's request: some sites refuse an unknown agent outright (a 403 to "compatible; ...")
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
           "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate"}
RETRY_STATUS = (403, 408, 425, 429, 500, 502, 503, 504, "unreachable")
BACKOFF_S = 2.5
# The archive answers a burst with 429 for minutes at a time (15 Nov: 23-84 s a page spent waiting on it, for nothing).
# Per page it is waited on at most WAYBACK_WAITS times and WAYBACK_WAIT_S seconds in all, then the page moves on:
# re-research (another readable page for the same claim) is the real backstop, not the archive.
WAYBACK_BACKOFF_S = 4.0
WAYBACK_WAITS = 2
WAYBACK_WAIT_S = 10.0
WAYBACK_TIMEOUT_S = 20
UA = BROWSER_UA
PDF = "a PDF cannot be read here: quote an HTML page that says it"
_sleep = time.sleep


class FetchError(Exception):
    """A page that would not open: `status` is the HTTP code, or "unreachable" (a reset, a timeout, no route)."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


def _decompress(body, enc):
    """The page's bytes as sent: the Wayback Machine's raw copies (id_) keep the site's own gzip whatever was asked."""
    enc = (enc or "").lower()
    try:
        if body[:2] == b"\x1f\x8b" or "gzip" in enc:
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error):
        return body
    return body


def _get(url, timeout):
    """One GET with a browser's headers: the page's text (decoded), or FetchError with its status."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "pdf" in (r.headers.get_content_type() or ""):
                raise ValueError(PDF)
            body = r.read(6 * 1024 * 1024)
            enc, charset = r.headers.get("Content-Encoding"), r.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP Error {e.code}: {e.reason}", status=e.code) from None
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - a reset, a timeout, no route: unreachable from here
        raise FetchError(f"unreachable: {str(getattr(e, 'reason', e))[:120]}", status="unreachable") from None
    body = _decompress(body, enc)
    if body[:5] == b"%PDF-":
        raise ValueError(PDF)
    return body.decode(charset or "utf-8", "replace")


def _read(url, timeout, retries=1, backoff=BACKOFF_S, log_to=None, via="live", budget=None):
    """The raw page, asked again after a short wait when the refusal may pass (403, 429, 5xx, a reset). With `budget`
    ({"waits": n, "seconds": s}, shared by every request for one page) the waits come out of it, and an empty budget
    ends the asking at once."""
    for k in range(retries + 1):
        try:
            raw = _get(url, timeout)
            if log_to is not None:
                log_to.append(dict(via=via, url=url, status=200))
            return raw
        except FetchError as e:
            if log_to is not None:
                log_to.append(dict(via=via, url=url, status=e.status))
            if k == retries or e.status not in RETRY_STATUS:
                raise
            wait = backoff * (k + 1)
            if budget is not None:
                if budget["waits"] <= 0 or budget["seconds"] <= 0:
                    raise
                wait = min(wait, budget["seconds"])
                budget["waits"] -= 1
                budget["seconds"] -= wait
            _sleep(wait)


def wikipedia_api(url):
    """[(via, url)] of Wikipedia's own APIs for a Wikipedia article (REST html, then the parse API), else []."""
    p = urllib.parse.urlsplit(str(url))
    host = p.netloc.lower().replace(".m.wikipedia.org", ".wikipedia.org")
    if not host.endswith("wikipedia.org") or not p.path.startswith("/wiki/") or len(p.path) <= 6:
        return []
    title = p.path[len("/wiki/"):]
    return [("wikipedia_rest", f"https://{host}/api/rest_v1/page/html/{title}"),
            ("wikipedia_parse", f"https://{host}/w/api.php?action=parse&page={title}&prop=text&format=json&formatversion=2&redirects=1")]


def _wiki_html(via, raw):
    if via == "wikipedia_parse":
        return ((json.loads(raw) or {}).get("parse") or {}).get("text") or ""
    return raw


def wayback(url, timeout=WAYBACK_TIMEOUT_S, log_to=None):
    """The raw text of the Wayback Machine's latest copy of `url` (its original bytes, `id_`): the availability API's
    newest snapshot, else the copy nearest this year. FetchError when it holds none. One wait budget for the whole page
    (WAYBACK_WAITS waits, WAYBACK_WAIT_S seconds); a 429 that outlasts it ends the page's archive door at once."""
    if str(url).startswith(("https://web.archive.org/", "http://web.archive.org/")):
        raise FetchError("already an archived copy", status="archived")
    budget = dict(waits=WAYBACK_WAITS, seconds=WAYBACK_WAIT_S)
    last = None
    try:
        api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(str(url), safe="")
        snap = ((json.loads(_read(api, timeout, retries=2, backoff=WAYBACK_BACKOFF_S, log_to=log_to, via="wayback_api",
                                  budget=budget)) or {}).get("archived_snapshots") or {}).get("closest") or {}
        ts = snap.get("timestamp") if str(snap.get("status", "200")) == "200" and snap.get("available", True) else None
    except (FetchError, ValueError) as e:
        ts, last = None, e
        if getattr(e, "status", None) == 429:
            raise                                   # the archive is rate-limiting this machine: move on
    tries = ([f"https://web.archive.org/web/{ts}id_/{url}"] if ts else []) + [f"https://web.archive.org/web/{time.gmtime().tm_year}id_/{url}"]
    for u in tries:
        try:
            return _read(u, timeout, retries=2, backoff=WAYBACK_BACKOFF_S, log_to=log_to, via="wayback", budget=budget)
        except FetchError as e:
            last = e
            if e.status == 404:
                continue
            break                                   # a refusal the budget could not wait out: the next copy is refused too
    raise last if isinstance(last, FetchError) else FetchError("no archived copy", status="not archived")


_LOCK = threading.Lock()
_URL_LOCKS = {}


def _url_lock(key):
    """One lock a page: threads asking for the same page wait for the first fetch and read its cached answer (15 Nov: two
    threads fetched one page at once, the site refused the second with a 403, and that refusal overwrote the cached text,
    so 23 sentences verified on that page failed)."""
    with _LOCK:
        return _URL_LOCKS.setdefault(str(key), threading.Lock())


def page_text(url, timeout=20):
    """The page's readable text. The live page with a browser's headers, asked twice; when it will not open, Wikipedia's
    own API for a Wikipedia article, then the Wayback Machine's latest copy of the same URL: the same page from another
    door, never a model's account of it. Every fetch is kept in TRIED[url]; the copy read in _VIA[url]. One fetch a page,
    whatever the threads (_url_lock)."""
    with _url_lock(url):
        return _page_text(url, timeout)


def _page_text(url, timeout=20):
    if url in _PAGES:
        if isinstance(_PAGES[url], Exception):
            raise _PAGES[url]
        return _PAGES[url]
    if str(url).lower().split("?")[0].endswith(".pdf"):
        raise ValueError(PDF)
    tried = TRIED.setdefault(url, [])
    first, raw = None, None
    try:
        (text, raw), via = _both(_read(url, timeout, log_to=tried)), "live"
    except ValueError:
        raise
    except FetchError as e:
        first, text, via = e, None, None
    if text is None:
        for v, u in wikipedia_api(url):
            try:
                (text, raw), via = _both(_wiki_html(v, _read(u, timeout, log_to=tried, via=v))), v
                break
            except (FetchError, ValueError):
                continue
    if text is None:
        try:
            (text, raw), via = _both(wayback(url, log_to=tried)), "wayback"
        except FetchError:
            pass
    if text is None:
        _PAGES[url] = first
        raise first
    _PAGES[url], _VIA[url], _RAW[url] = text, via, raw
    return text


def archived_text(url):
    """The Wayback copy's readable text, for a live page whose text hides the quote (scripts, a soft wall); None when
    there is none. Cached like the pages."""
    key = "wayback:" + str(url)
    with _url_lock(key):
        if key not in _PAGES:
            try:
                _PAGES[key], _RAW[key] = _both(wayback(url, log_to=TRIED.setdefault(url, [])))
            except (FetchError, ValueError):
                _PAGES[key] = None
        return _PAGES[key]


def quote_found(quote, page):
    """True when the quote stands on the page: whole, or 85% of its six-word runs in place, or (for a quote copied off a
    table or inline markup, where cells and tags run together: "26Thanksgiving") its letters and digits in the same
    unbroken order. Exact matching of measured strings only."""
    q = norm(quote)
    if not q:
        return False
    if q in page:
        return True
    tight = q.replace(" ", "")
    if len(tight) >= 24 and tight in page.replace(" ", ""):
        return True
    w = q.split()
    if len(w) < 8:
        return False
    runs = [" ".join(w[i:i + 6]) for i in range(len(w) - 5)]
    return sum(r in page for r in runs) / len(runs) >= 0.85


def digits(text):
    return re.findall(r"\d+", norm(text))


def ordinal_items(script):
    """The display items that number the film's own list (role "ordinal": "1.", "2.", "3." on the items it counts), by
    structure alone: each holds exactly one number, and together they are 1..n once each, n >= 2. Such a number is a
    position in the film, not a claim about the world, so no quote has to hold it. Anything else marked ordinal (a
    lone "1.", a "#1" rank, two numbers) is read as a figure and must stand in a verified quote."""
    items = [x for s in script.get("segments") or [] for x in s.get("show") or [] if isinstance(x, dict) and x.get("role") == "ordinal"]
    nums = [digits(str(x.get("text") or "")) for x in items]
    if len(items) >= 2 and all(len(n) == 1 for n in nums) and sorted(int(n[0]) for n in nums) == list(range(1, len(items) + 1)):
        return items
    return []


def shown_strings(script):
    """Every string the film prints whose numbers are claims: display items (not a valid list's ordinals), the
    character sheet's ticks, stats and overflow."""
    out = []
    skip = {id(x) for x in ordinal_items(script)}
    for s in script.get("segments") or []:
        out += [str(x.get("text") if isinstance(x, dict) else x) for x in s.get("show") or [] if id(x) not in skip]
        for t in s.get("ticks") or []:
            out.append(str(t.get("to")))
    for st in script.get("stats") or []:
        out.append(str(st.get("start")))
    if script.get("overflow"):
        out.append(str(script["overflow"].get("to")))
    return [x for x in out if x and x != "None"]


def check_fact(f):
    """One fact's row: its quote looked for in the readable text of its own page (any door page_text opens), then of
    the page's Wayback copy when the live text lacks it, then of the other pages the writer cited for the same quote
    (`also`), then of a page the web_fetch tool read. Every fetch is listed with its status."""
    r = dict(id=f.get("id"), url=f.get("url"), claim=f.get("claim"), found=False)
    q = f.get("quote", "")
    thin = thin_quote(q)
    if thin:                            # a bare number or date verifies nothing, wherever it stands
        return dict(r, read="here", missing=thin, thin=True)
    tried = []
    urls = [f.get("url")] + [u for u in (f.get("also") or [])[:3] if isinstance(u, str) and u.startswith("http") and u != f.get("url")]
    for k, u in enumerate(urls):
        if not u:
            continue
        tag = "" if k == 0 else "also:"
        try:
            pg = page_text(u)
            via = _VIA.get(u, "live")
            if quote_found(q, pg):
                r.update(found=True, read=tag + ("here" if via == "live" else via), source=u)
            elif via == "live":
                arch = archived_text(u)
                if arch and quote_found(q, arch):
                    r.update(found=True, read=tag + "wayback", source=u)
                elif k == 0:
                    r["missing"] = "the quote is not in the page's text"
        except Exception as e:  # noqa: BLE001 - an unreadable page verifies nothing here
            if k == 0:
                r["error"] = str(e)[:160]
        tried += [dict(t) for t in TRIED.get(u) or []]
        if r["found"]:
            break
    if not r["found"] and f.get("url") in FETCHED:
        r["found"] = quote_found(q, norm(FETCHED[f["url"]]))
        r["read"] = "web_fetch"
    if r["found"]:
        r.pop("error", None)
        r.pop("missing", None)
    elif "read" not in r and "error" not in r:
        r["read"] = "here"
    if tried:
        r["fetches"] = tried
    return r


def speech_of(seg):
    """The spans a segment presents as someone's own words, as the writer declared them (its `quotes` field)."""
    out = []
    for q in seg.get("quotes") or [] if isinstance(seg.get("quotes"), list) else []:
        if isinstance(q, dict) and norm(q.get("words") or ""):
            out.append(dict(words=str(q["words"]), who=q.get("who"), fact=q.get("fact")))
    return out


def same_person(a, b):
    """True when two names name the same person, by identity of their words only: one name's words stand whole inside
    the other's ("Hanks" in "Tom Hanks"), or both end in the same word (a surname: "Mr Dyson", "James Dyson")."""
    wa, wb = speech_words(a), speech_words(b)
    if not wa or not wb:
        return False
    return has_words(" ".join(wa), " ".join(wb)) or has_words(" ".join(wb), " ".join(wa)) or wa[-1] == wb[-1]


def speakers_of(script):
    """{fact id: the person whose own words its quote holds}, for facts that say (the bank records it; a fact without the
    field is unknown, never a mismatch)."""
    return {f.get("id"): str(f.get("speaker")) for f in script.get("facts") or [] if f.get("speaker")}


def speech_backed(words, seg, quotes_by_id, who=None, speakers=None):
    """Why a span presented as someone's words cannot stand, or None when it can. Both, by identity of whole words in
    order (speech_words, has_words; never a substring, never a reading of meaning):
      - the span stands in the LINE (a span the judge copied from the quote but the line does not say, "I'm in" for a
        line saying "I'm all in", is not the line's words);
      - it stands verbatim in the verified quote of a fact the line cites, and that quote is that person's (when the
        facts record whose words their quotes hold, `speakers`, and `who` is given, one holding quote must be theirs).
    `quotes_by_id` holds each verified fact's quote as written."""
    if not speech_words(words):
        return None
    if not has_words(seg.get("say") or "", words):
        return "the quoted words are not in the line as given (the line says other words than the ones named as someone's)"
    holding = [f for f in (seg.get("facts") or []) if f in quotes_by_id and has_words(quotes_by_id[f], words)]
    if not holding:
        return "the quoted words stand in no verified quote the line cites"
    speakers = speakers or {}
    if who and all(speakers.get(f) for f in holding) and not any(same_person(who, speakers[f]) for f in holding):
        return f"the quoted words stand only in a quote of {speakers[holding[0]]}'s, not {who}'s"
    return None


def speech_problems(script, quotes_by_id):
    """[{segment, words, who, why}] for every declared quoted span that is not in its line, or not verbatim in a
    verified quote of that person the line cites."""
    out, speakers = [], speakers_of(script)
    for k, s in enumerate(script.get("segments") or []):
        for q in speech_of(s):
            why = speech_backed(q["words"], s, quotes_by_id, who=q.get("who"), speakers=speakers)
            if why:
                out.append(dict(segment=k, words=q["words"], who=q.get("who"), why=why))
    return out


def verify(script):
    """Each quote fetched from its own page; every number on screen inside a verified quote; every span the narration
    presents as someone's words verbatim in a verified quote the line cites."""
    rows, ok_quotes, by_id = [], [], {}
    for f in script.get("facts") or []:
        r = check_fact(f)
        if r["found"]:
            ok_quotes.append(norm(f.get("quote", "")))
            by_id[f.get("id")] = str(f.get("quote") or "")
        rows.append(r)
    have = set()
    for q in ok_quotes:
        have.update(digits(q))
    unbacked = sorted({d for s in shown_strings(script) for d in digits(s) if d not in have})
    used = {fid for s in script.get("segments") or [] for fid in s.get("facts") or []}
    bad_facts = [r["id"] for r in rows if not r["found"] and r["id"] in used]
    speech = speech_problems(script, by_id)
    return dict(facts=rows, verified=sum(r["found"] for r in rows), total=len(rows), numbers_unbacked=unbacked,
                facts_unverified=bad_facts, speech_unbacked=speech,
                ok=not unbacked and not bad_facts and not speech and bool(rows))


# ----------------------------------------------------------------- a fact that fails is re-researched, never the film

CORE_BEATS = ("hook", "turn", "payoff", "close")
RESEARCH_MODEL = "gpt-6.1-sol"          # pinned to OpenAI (ask_openai): Anthropic spend stays at zero

RESEARCH_SYSTEM = """You are the fact checker of a narrated documentary film. Some of its facts could not be checked: the page cited would not open for a plain script (a bot wall, a 403, a reset), or the sentence quoted is not on that page. For each one, search the web for other pages that state the SAME claim, and copy from each one sentence that states it, word for word exactly as it stands on the page.

Cite only pages a plain script can read: Wikipedia and Wikimedia, archive.org, government and university pages, a company's or studio's own site and newsroom, museums, and magazines and trade press that are free to read. Never the page that failed, never a PDF, never a page behind a paywall, a login or a bot wall (document mirrors such as studylib or scribd, Britannica, most newspapers). The sentence is checked by fetching the page and finding it in the page's text, so copy it exactly; a sentence that only implies the claim, or a paraphrase, is worth nothing. When no readable page states the claim, give no sources for it: never stretch a source.

Answer with one JSON object only: {"facts": [{"id": the fact id, "sources": [up to three {"url": the page, "quote": the sentence copied word for word}]}]}"""

REWRITE_SYSTEM = """You are the writer of a narrated documentary film. Every fact was checked against its page; the film may say only what the checked quotes state. You get the whole script, the facts that were checked (each with its quote), the facts that could not be checked (no readable page states them, so the film must not say them), and the segments that must change, each with the reason. Rewrite each of those segments so every claim in it is stated by the quote of a checked fact it cites (cite them in "facts"), keeping its place and job in the story and the narrator's voice and length, or drop it (say null) when nothing true is left to say there. Never change any other segment, never use an unchecked fact, and never say what another segment of the film already says (neither its point nor its words).

"say" is spoken: numbers and years written as words the way they are said, no digits, no abbreviations, no symbols. "show": at most two display items in the same form as the script's ({"text": as printed, "role": one of the roles given}), digits only when the same figure stands in a checked fact's quote; or []. "quotes": every span of "say" that presents someone's own words (in quotation marks, or as what they said, recalled, wrote or asked), each copied word for word from the quote of a checked fact the segment cites and from that person; or []. Never put words in anyone's mouth that a checked quote does not hold.

Answer with one JSON object only: {"segments": [{"i": the segment index, "say": the new words or null to drop it, "show": [...], "facts": [checked fact ids], "quotes": [{"words": the span exactly as in say, "who": whose words, "fact": the fact id whose quote holds them}]}]}"""

SUPPORT_MODEL = "gpt-6.1-sol"           # pinned to OpenAI: the judgement of meaning is a model's, never a rule's
SUPPORT_ROUNDS = 2                      # an unsupported line is rewritten at most twice, then dropped (or the core fails)

SUPPORT_SYSTEM = """You are the fact checker of a narrated documentary film. Every quote below was fetched from its page and found there word for word. For each line you are asked about, decide whether EVERY claim the line makes is stated by the quotes of the facts it cites: a person, a number, a date, an order of events, a cause, a feeling, who said what. A claim the quotes only suggest, a detail they do not hold, or a quote put in a different person's mouth is not supported. The other lines of the script are there only so you know who "he" or "it" is.

Also list every span of the line that presents someone's own words: in quotation marks, or given as what a person said, recalled, wrote, asked or thought in their own words. Copy each span exactly as the LINE has it (never as the quote has it), with whose words the line says they are, by name. Paraphrase in the narrator's own voice ("he agreed to do it") is not such a span.

And name the cited facts the line actually uses: those whose words it says or paraphrases. A fact cited for a date, a figure or a detail the line never says is not used.

Answer with one JSON object only: {"lines": [{"i": the line index, "supported": true or false, "unsupported": the claim no quote states (empty when supported), "speech": [{"words": the span exactly as in the line, "who": whose words}], "uses": [the ids of the cited facts the line uses]}]}"""

REPEAT_SYSTEM = """You are the story editor of a narrated documentary film of about a minute and a half. Some lines were rewritten just now. Read the whole script and find any line that says again what another line already says: the same claim or point made twice, or the same phrasing used twice. A line that adds a new detail, a consequence or a turn is not a repeat; a deliberate echo of the hook in the last line (the close reframing it) is not a repeat.

For each repeat, name the line that should change: the line rewritten just now when one of the two was; never a hook, turn, payoff or close line (marked CORE) when the other is not one.

Answer with one JSON object only: {"repeats": [{"i": the line that should change, "of": the line it repeats, "why": a few words}]} (an empty list when nothing repeats)."""


def core_segments(script):
    """The segments the story stands on: its hook, turn, payoff and close (the first segment is the hook whatever its
    mark; the last segment is the close when no segment is marked close). The close is restated, never silently dropped
    (15 Nov: it was dropped in two runs in a row). The script's own beat marks, read structurally."""
    segs = script.get("segments") or []
    out = {k for k, s in enumerate(segs) if k == 0 or s.get("beat") in CORE_BEATS}
    if segs and not any(s.get("beat") == "close" for s in segs):
        out.add(len(segs) - 1)
    return out


def _why(row):
    return row.get("error") or row.get("missing") or "the quote is not in the page's text"


def research(script, rows, ask=None):
    """({fact id: [{"url", "quote"}]}, usage): one web-search call (RESEARCH_MODEL on OpenAI) asking for other readable
    pages that state each unverified fact's claim, with the sentence copied from each."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=16000, searches=10,
                                                     what="re-research", model=RESEARCH_MODEL, max_tool_calls=10))
    by = {f.get("id"): f for f in script.get("facts") or []}
    want = [dict(id=r["id"], claim=(by.get(r["id"]) or {}).get("claim"), failed_url=r.get("url"),
                 failed_quote=(by.get(r["id"]) or {}).get("quote"), why=_why(r)) for r in rows]
    title = script.get("title") or ""
    text, use = ask(RESEARCH_SYSTEM, f"# The film\n{title}\n\n# Facts to source again\n{json.dumps(want, ensure_ascii=False, indent=1)}")
    try:
        got = json_block(text)
    except ValueError:
        return {}, use
    out = {}
    for g in got.get("facts") or [] if isinstance(got, dict) else []:
        if not isinstance(g, dict) or g.get("id") not in by:
            continue
        src = [dict(url=str(x["url"]).strip(), quote=str(x["quote"]).strip()) for x in g.get("sources") or []
               if isinstance(x, dict) and str(x.get("url") or "").startswith("http") and str(x.get("quote") or "").strip()]
        out[g["id"]] = src[:3]
    return out, use


def _norm_show(items, roles):
    out = []
    for x in items if isinstance(items, list) else []:
        if isinstance(x, dict) and str(x.get("text") or "").strip() and x.get("role") in roles:
            out.append(dict(text=str(x["text"]).strip(), role=x["role"]))
        elif isinstance(x, str) and x.strip() and not roles:
            out.append(x.strip())
    return out[:2]


def _norm_quotes(items, facts):
    out = []
    for q in items if isinstance(items, list) else []:
        if isinstance(q, dict) and str(q.get("words") or "").strip():
            out.append(dict(words=str(q["words"]).strip(), who=q.get("who"), fact=q.get("fact") if q.get("fact") in facts else None))
    return out


def _script_lines(segs):
    return "\n".join(f"{k}. beat={s.get('beat')} facts={s.get('facts') or []} show={json.dumps(s.get('show') or [], ensure_ascii=False)}"
                     f" quotes={json.dumps([q['words'] for q in speech_of(s)], ensure_ascii=False)}\n   {s.get('say')}"
                     for k, s in enumerate(segs))


def rewrite_without(script, bad, ask=None, targets=None, why=None, style=None):
    """(script, info, usage): segments rewritten on the checked facts alone, or dropped, by one call (RESEARCH_MODEL on
    OpenAI, no web). The segments are those citing an unverifiable fact in `bad` (which leave the script), plus
    `targets` (indices) with `why` ({index: the reason it must change}: a claim no quote states, a repeat). Structure
    is held here: only those segments change, each cites checked facts only, and a segment the answer leaves invalid
    is dropped. A core segment (the hook, the turn, the payoff: core_segments) is never dropped: it is restated on at
    least one checked fact or listed in info["core_failed"], and the story then cannot stand. Every rewritten segment
    is marked `_check` for the line-support judgement; the caller verifies the result again."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=12000, what="rewrite unverified",
                                                     model=RESEARCH_MODEL))
    sc = json.loads(json.dumps(script))
    bad, why = set(bad), dict(why or {})
    good = [f for f in sc.get("facts") or [] if f.get("id") not in bad]
    good_ids = {f.get("id") for f in good}
    segs = sc.get("segments") or []
    targets = sorted({k for k, s in enumerate(segs) if set(s.get("facts") or []) & bad} |
                     {k for k in targets or () if _valid_i(k, len(segs))})
    for k in targets:
        if set(segs[k].get("facts") or []) & bad:
            why.setdefault(k, "it rests on a fact no readable page states")
    core = core_segments(sc)
    points = spine_points(sc)
    dict_show = any(isinstance(x, dict) for s in segs for x in s.get("show") or [])
    roles = ROLES if dict_show else ()
    use, got, err = None, {}, None
    if targets:
        content = (f"# The script\n" + _script_lines(segs) +
                   f"\n\n# Checked facts\n{json.dumps([dict(id=f.get('id'), claim=f.get('claim'), quote=f.get('quote')) for f in good], ensure_ascii=False)}"
                   f"\n\n# Unchecked facts (the film must not say them)\n{json.dumps([dict(id=f.get('id'), claim=f.get('claim')) for f in sc.get('facts') or [] if f.get('id') in bad], ensure_ascii=False)}"
                   f"\n\n# Rewrite or drop these segments\n{targets}"
                   f"\n\n# Why each must change\n{json.dumps({str(k): why.get(k, '') for k in targets}, ensure_ascii=False)}" +
                   (f"\n\n# Core segments (the hook, the turn, the payoff): never drop these; restate each on the checked facts "
                    f"only when they still carry its point, else answer say null for it\n{[k for k in targets if k in core]}"
                    if any(k in core for k in targets) else "") +
                   (f"\n\n# The point each core segment must carry (the story's spine, with the checked facts that state it): "
                    f"restate the segment so it says that point as sharply, in words those facts support\n"
                    f"{json.dumps({str(k): points[k] for k in targets if k in points}, ensure_ascii=False)}"
                    if any(k in points for k in targets) else "") +
                   (f"\n\n# Display roles\n{list(roles)}" if roles else "") +
                   (f"\n\n# The spoken style to keep in every rewritten segment\n{style}" if style else ""))
        try:
            text, use = ask(REWRITE_SYSTEM, content)
            ans = json_block(text)
            got = {g["i"]: g for g in ans.get("segments") or [] if isinstance(g, dict) and _valid_i(g.get("i"), len(segs))}
        except Exception as e:  # noqa: BLE001 - no answer: the segments that must change are dropped
            got, err = {}, repr(e)[:200]
    rewritten, dropped, core_failed = [], [], []
    for k in targets:
        g = got.get(k) or {}
        say = g.get("say")
        facts = g.get("facts") if isinstance(g.get("facts"), list) else None
        if k in core and not (isinstance(say, str) and say.strip() and facts and set(facts) <= good_ids):
            core_failed.append(k)               # the hook, turn or payoff restated on nothing checked does not stand
        elif isinstance(say, str) and say.strip() and facts is not None and set(facts) <= good_ids and k in got:
            segs[k].setdefault("say_first", segs[k].get("say"))     # the writer's own words, for the point check
            segs[k]["say"] = " ".join(say.split())
            if isinstance(segs[k].get("emphasis"), list):             # the actor stresses words the new line still says
                segs[k]["emphasis"] = [e for e in segs[k]["emphasis"] if has_words(segs[k]["say"], e)]
            segs[k]["facts"] = list(dict.fromkeys(facts))
            segs[k]["show"] = _norm_show(g.get("show"), roles)
            segs[k]["quotes"] = _norm_quotes(g.get("quotes"), set(facts))
            segs[k]["rewritten"] = True
            segs[k]["_check"] = why.get(k) or "rewritten"
            rewritten.append(k)
        else:
            dropped.append(k)
    # why a target was not restated, in the rewrite's own terms (recorded by the callers: a rejection always has a reason)
    answer = {k: ("the rewrite call failed: " + err if err else "the rewrite gave no answer for it" if k not in got
                  else "the rewrite answered null: it found no checked quote that says it" if got[k].get("say") is None
                  else "the rewrite cited a fact that is not checked" if not set(got[k].get("facts") or []) <= good_ids
                  else "the rewrite cited no checked fact" if not got[k].get("facts") else "the rewrite's answer was not usable")
              for k in targets if k not in rewritten}
    if core_failed:
        return json.loads(json.dumps(script)), dict(rewritten=[], dropped=[], facts_dropped=[], core_failed=core_failed,
                                                    why={k: why.get(k) for k in core_failed}, answer=answer, error=err), use
    sc["segments"] = [s for k, s in enumerate(segs) if k not in dropped]
    sc["facts"] = good
    if sc["segments"]:
        sc["hook_line"], sc["close_line"] = sc["segments"][0].get("say"), sc["segments"][-1].get("say")
    return sc, dict(rewritten=rewritten, dropped=dropped, facts_dropped=sorted(bad), core_failed=[], answer=answer, error=err), use


def _verified_quotes(script, rep=None):
    rep = rep or verify(script)
    ok = {r["id"] for r in rep["facts"] if r["found"]}
    return {f.get("id"): str(f.get("quote") or "") for f in script.get("facts") or [] if f.get("id") in ok}


def judge_support(script, idx, ask=None, quotes_by_id=None):
    """({index: {"ok", "why", "speech"}}, usage): one model call (SUPPORT_MODEL) judges whether every claim of each line
    in `idx` is stated by the verified quotes of the facts it cites, and names the spans each line presents as someone's
    own words. Each such span (the model's and the writer's own `quotes`) must then stand word for word in a verified
    quote the line cites (has_words: whole words in order); a span that does not, even one the judge copied loosely
    and called supported, makes the line unsupported. A line the answer leaves out is unsupported (fail closed)."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=12000, what="line support",
                                                     model=SUPPORT_MODEL))
    segs = script.get("segments") or []
    idx = [k for k in idx if _valid_i(k, len(segs))]
    quotes_by_id = quotes_by_id if quotes_by_id is not None else _verified_quotes(script)
    if not idx:
        return {}, None
    facts = {f.get("id"): f for f in script.get("facts") or []}
    ask_lines = [dict(i=k, say=segs[k].get("say"), facts=[dict(id=f, claim=(facts.get(f) or {}).get("claim"),
                                                                quote=(facts.get(f) or {}).get("quote"), url=(facts.get(f) or {}).get("url"))
                                                           for f in segs[k].get("facts") or [] if f in quotes_by_id])
                      for k in idx]
    content = (f"# The whole script (context)\n" + "\n".join(f"{k}. {s.get('say')}" for k, s in enumerate(segs)) +
               f"\n\n# Judge these lines against the verified quotes of the facts each one cites\n"
               f"{json.dumps(ask_lines, ensure_ascii=False, indent=1)}")
    out, use = {}, None
    try:
        text, use = ask(SUPPORT_SYSTEM, content)
        ans = json_block(text)
        got = {g["i"]: g for g in ans.get("lines") or [] if isinstance(g, dict) and _valid_i(g.get("i"), len(segs))}
    except Exception as e:  # noqa: BLE001 - no judgement: every line asked about counts as unsupported
        got, use = {}, use
        out["_error"] = repr(e)[:200]
    speakers = speakers_of(script)
    for k in idx:
        g = got.get(k)
        if g is None:
            out[k] = dict(ok=False, why="no judgement for this line", speech=[])
            continue
        speech = [dict(words=str(x.get("words")), who=x.get("who")) for x in g.get("speech") or []
                  if isinstance(x, dict) and speech_words(x.get("words") or "")]
        declared = [dict(words=q["words"], who=q.get("who")) for q in speech_of(segs[k])]
        bad_speech = []
        for q in declared + speech:
            # fail closed: every span given as someone's words, the writer's and the judge's alike, must stand in the
            # line AND word for word in a verified quote of that person the line cites; a span the judge copied loosely
            # ("Im in") or from the quote instead of the line ("I'm in" for "I'm all in") makes the line unsupported
            # whatever the verdict says
            w = speech_backed(q["words"], segs[k], quotes_by_id, who=q.get("who"), speakers=speakers)
            if w and not any(speech_words(q["words"]) == speech_words(b["words"]) for b in bad_speech):
                bad_speech.append(dict(q, why=w))
        cited = [f for f in segs[k].get("facts") or [] if f in quotes_by_id]
        uses = [f for f in g.get("uses") if f in cited] if isinstance(g.get("uses"), list) else None
        if not bad_speech and g.get("supported") is True:
            out[k] = dict(ok=True, why="", speech=speech, uses=uses)
        else:
            why = str(g.get("unsupported") or "") if g.get("supported") is not True else ""
            if bad_speech:
                why = "; ".join(filter(None, [why] + [f"words given as {q.get('who') or 'someone'}'s own ({q['words']!r}) are in no "
                                                       f"verified quote it cites" for q in bad_speech]))
            out[k] = dict(ok=False, why=why or "a claim no verified quote states", speech=speech, bad_speech=bad_speech)
    return out, use


def judge_repeats(script, changed, ask=None):
    """([{"i", "of", "why"}], usage): one model call over the whole script flags a line that says again what another
    line says (its claim or its phrasing), naming the line that should change."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=8000, what="line repeats",
                                                     model=SUPPORT_MODEL))
    segs = script.get("segments") or []
    core = core_segments(script)
    content = ("# The script\n" + "\n".join(f"{k}. beat={s.get('beat')}{' CORE' if k in core else ''}\n   {s.get('say')}"
                                            for k, s in enumerate(segs)) +
               f"\n\n# Lines rewritten just now\n{sorted(changed)}")
    try:
        text, use = ask(REPEAT_SYSTEM, content)
        ans = json_block(text)
    except Exception:  # noqa: BLE001 - no judgement: nothing is changed on a guess
        return [], None
    out, seen = [], set()
    for r in ans.get("repeats") or [] if isinstance(ans, dict) else []:
        if isinstance(r, dict) and _valid_i(r.get("i"), len(segs)) and _valid_i(r.get("of"), len(segs)) and r["i"] != r["of"] \
                and r["i"] not in seen:
            seen.add(r["i"])
            out.append(dict(i=r["i"], of=r["of"], why=str(r.get("why") or "")))
    return out, use


def _ids(sc):
    return [s.get("_sid") for s in sc.get("segments") or []]


def _at(sc, sid):
    for k, s in enumerate(sc.get("segments") or []):
        if s.get("_sid") == sid:
            return k
    return None


def uncite(seg, verdict, qb, info, stage):
    """A supported line cites only the facts whose words it uses or paraphrases (the judge's `uses`, a model's
    judgement): the others leave its `facts` (a bare "1983" cited by lines that never say it, 15 Nov). A fact holding
    words the line gives as someone's own is kept. No `uses` in the answer changes nothing."""
    uses = verdict.get("uses")
    if uses is None:
        return
    facts = list(seg.get("facts") or [])
    spans = [q["words"] for q in speech_of(seg)] + [q["words"] for q in verdict.get("speech") or []]
    keep = [f for f in facts if f in uses or f not in qb or any(has_words(qb[f], w) for w in spans)]
    if keep != facts:
        info.setdefault("uncited", []).append(dict(stage=stage, sid=seg.get("_sid"), say=seg.get("say"),
                                                   removed=[f for f in facts if f not in keep]))
        seg["facts"] = keep


def check_lines(sc, info, spend, log, ask_judge=None, ask_rewrite=None, rounds=SUPPORT_ROUNDS, style=None, stage="settle"):
    """(script, core_failed {sid: why}): every segment marked `_check` (it cites a fact whose quote was replaced, it was
    rewritten, or it declares quoted speech no verified quote holds) is judged for support; an unsupported line is
    rewritten on the verified quotes and judged again, at most `rounds` times, then dropped. A core line that
    cannot be restated on its quotes is returned in core_failed (the story cannot stand)."""
    core_failed = {}
    for rnd in range(rounds + 1):
        idx = [k for k, s in enumerate(sc.get("segments") or []) if s.get("_check")]
        if not idx:
            break
        qb = _verified_quotes(sc)
        verdicts, use = judge_support(sc, idx, ask=ask_judge, quotes_by_id=qb)
        if use:
            spend.append(use)
        segs = sc["segments"]
        info["lines_checked"] += [dict(stage=stage, round=rnd, line=k, sid=segs[k].get("_sid"), say=segs[k].get("say"),
                                       why_checked=segs[k].get("_check"),
                                       ok=verdicts[k]["ok"], why=verdicts[k]["why"],
                                       speech=[dict(words=q["words"], who=q.get("who"),
                                                    source=next((f for f in segs[k].get("facts") or []
                                                                 if has_words(qb.get(f, ""), q["words"])), None))
                                               for q in verdicts[k]["speech"]])
                                  for k in idx]
        bad = {k: verdicts[k]["why"] for k in idx if not verdicts[k]["ok"]}
        for k in idx:
            if verdicts[k]["ok"]:
                segs[k].pop("_check", None)
                uncite(segs[k], verdicts[k], qb, info, stage)
        if not bad:
            break
        log(f"line support (round {rnd}): unsupported {[(k, w[:80]) for k, w in bad.items()]}"[:600])
        core = core_segments(sc)
        if rnd == rounds:
            for k in bad:
                if k in core:
                    core_failed[segs[k].get("_sid")] = bad[k]
            drop = {k for k in bad if k not in core}
            info["support_dropped"] += [dict(say=segs[k].get("say"), why=bad[k]) for k in sorted(drop)]
            sc["segments"] = [s for k, s in enumerate(segs) if k not in drop]
            break
        sids = {k: segs[k].get("_sid") for k in bad}
        why = {k: f"{w} (checked because {str(segs[k].get('_check')).strip()})" for k, w in bad.items()}
        sc2, ri, use = rewrite_without(sc, (), ask=ask_rewrite, targets=sorted(bad), why=why, style=style)
        if use:
            spend.append(use)
        if ri["core_failed"]:
            for k in ri["core_failed"]:
                core_failed[sids[k]] = bad[k]
            break
        info["support_rewritten"] += [sids[k] for k in ri["rewritten"]]
        info["support_dropped"] += [dict(say=segs[k].get("say"), why=bad[k]) for k in ri["dropped"]]
        sc = sc2
    if sc.get("segments"):
        sc["hook_line"], sc["close_line"] = sc["segments"][0].get("say"), sc["segments"][-1].get("say")
    return sc, core_failed


def check_repeats(sc, changed, info, spend, log, ask_judge=None, ask_rewrite=None, ask_repeats=None, style=None, stage="settle"):
    """The script after a rewrite: one judgement over the whole script flags a line that repeats another's claim or
    phrasing; it is rewritten once (and judged for support and for repeats again), else dropped when it is not core
    (a core line keeps its last supported words)."""
    idx = [k for k, s in enumerate(sc.get("segments") or []) if s.get("_sid") in changed]
    if not idx:
        return sc
    reps, use = judge_repeats(sc, idx, ask=ask_repeats)
    if use:
        spend.append(use)
    if not reps:
        return sc
    segs = sc["segments"]
    info["repeats"] += [dict(line=r["i"], of=r["of"], say=segs[r["i"]].get("say"), why=r["why"]) for r in reps]
    log(f"repeats: {[(r['i'], r['of'], r['why'][:60]) for r in reps]}"[:400])
    before = json.loads(json.dumps(sc))
    flagged = {segs[r["i"]].get("_sid"): r for r in reps}
    why = {r["i"]: f"it says again what line {r['of']} says ({r['why']}): say something else this point of the story needs, "
                   "on the checked facts, or drop it" for r in reps}
    sc2, ri, use = rewrite_without(sc, (), ask=ask_rewrite, targets=sorted(why), why=why, style=style)
    if use:
        spend.append(use)
    core = {segs[k].get("_sid") for k in core_segments(sc)}
    if ri["core_failed"]:
        sc2 = before                           # a core line that cannot be said otherwise keeps its words
    info["repeat_rewritten"] += [segs[k].get("_sid") for k in ri["rewritten"]]
    info["repeat_dropped"] += [dict(say=segs[k].get("say"), why=why[k]) for k in ri["dropped"]]
    sc2, failed = check_lines(sc2, info, spend, log, ask_judge=ask_judge, rounds=0, stage=stage)     # rewritten once: judged, never again
    if failed:
        sc2 = before                           # the core line's new words do not stand: its last supported words do
        for s in sc2["segments"]:
            s.pop("_check", None)
    left = [k for k, s in enumerate(sc2["segments"]) if s.get("_sid") in flagged]
    still, use = judge_repeats(sc2, left, ask=ask_repeats) if left else ([], None)
    if use:
        spend.append(use)
    core2 = core_segments(sc2)
    drop = {r["i"] for r in still if sc2["segments"][r["i"]].get("_sid") in flagged and r["i"] not in core2
            and sc2["segments"][r["i"]].get("_sid") not in core}
    if drop:
        info["repeat_dropped"] += [dict(say=sc2["segments"][k].get("say"), why="still repeats after one rewrite") for k in sorted(drop)]
        sc2["segments"] = [s for k, s in enumerate(sc2["segments"]) if k not in drop]
    if sc2.get("segments"):
        sc2["hook_line"], sc2["close_line"] = sc2["segments"][0].get("say"), sc2["segments"][-1].get("say")
    return sc2


def settle(script, log=print, ask_research=None, ask_rewrite=None, ask_judge=None, ask_repeats=None):
    """(script, report, spend): the script verified, and what fails repaired one claim at a time instead of the film.

      1. every fact is checked (verify: its page through every door, its Wayback copy, its other cited copies);
      2. each fact a segment cites that does not verify is re-researched: one call finds other readable pages for the
         same claim; the first candidate whose quote is found on its page takes the fact's place;
      3. a fact still unverified leaves the film: the segments resting on it are rewritten on the checked facts or
         dropped (rewrite_without, one call). The hook, turn and payoff are never dropped: each is restated on checked
         facts or the story cannot stand (report.ok False, report.core_unverified naming the facts it rested on,
         report.core_why why each failed);
      4. every line that cites a replaced fact, every rewritten line, and every line whose declared quoted speech is in
         no verified quote it cites is judged by a model: is every claim in it stated by those quotes? Spans it presents
         as someone's words must stand verbatim in one. Unsupported lines are rewritten and judged again (at most
         SUPPORT_ROUNDS), else dropped; a core line that cannot be restated fails the story (report.core_unsupported);
      5. after any rewrite, one judgement over the whole script flags a line that repeats another; it is rewritten once,
         else dropped when not core;
      6. a display item whose number stands in no verified quote is taken off the screen (the words stay).
    Facts no segment cites and that do not verify are removed, so the record lists checked facts only."""
    original = json.loads(json.dumps(script))
    sc = json.loads(json.dumps(script))
    for k, s in enumerate(sc.get("segments") or []):
        s["_sid"] = k
    spend = []
    info = dict(researched=[], replaced=[], core_unverified=[], rewritten=[], dropped_segments=[], facts_dropped=[],
                display_dropped=[], unused_dropped=[], lines_checked=[], support_rewritten=[], support_dropped=[],
                repeats=[], repeat_rewritten=[], repeat_dropped=[], core_unsupported={})

    def done(sc_, rep_, ok=None):
        for s in sc_.get("segments") or []:
            s.pop("_sid", None)
            s.pop("_check", None)
        if ok is False:
            return sc_, dict(rep_, ok=False, settle=info), spend
        return sc_, dict(verify(sc_), settle=info), spend

    rep = verify(sc)
    bad_rows = [r for r in rep["facts"] if r["id"] in rep["facts_unverified"]]
    replaced = set()
    if bad_rows:
        info["researched"] = [dict(id=r["id"], url=r.get("url"), why=_why(r)) for r in bad_rows]
        log("fact check: re-researching " + "; ".join(f"{r['id']} ({_why(r)[:60]})" for r in bad_rows))
        try:
            found, use = research(sc, bad_rows, ask=ask_research)
            if use:
                spend.append(use)
        except Exception as e:  # noqa: BLE001 - no new sources: the claims are treated as unverifiable below
            log(f"fact check: re-research failed: {e!r}"[:300])
            found = {}
        by = {f.get("id"): f for f in sc.get("facts") or []}
        for r in bad_rows:
            f = by.get(r["id"])
            for cand in found.get(r["id"]) or []:
                trial = dict(f, url=cand["url"], quote=cand["quote"], also=[])
                row = check_fact(trial)
                if row["found"]:
                    f["replaced"] = dict(url=f.get("url"), quote=f.get("quote"), why=_why(r))
                    f["url"], f["quote"] = cand["url"], cand["quote"]
                    f.pop("also", None)
                    replaced.add(f["id"])
                    info["replaced"].append(dict(id=f["id"], old=f["replaced"]["url"], new=cand["url"], read=row.get("read"),
                                                 old_quote=f["replaced"]["quote"], new_quote=cand["quote"]))
                    break
        for s in sc.get("segments") or []:
            hit = sorted(set(s.get("facts") or []) & replaced)
            if hit:
                s["_check"] = f"it cites {hit}, whose quote was replaced: the line must say only what the new quote states"
        rep = verify(sc)
    unused = [r["id"] for r in rep["facts"] if not r["found"] and r["id"] not in rep["facts_unverified"]]
    if unused:
        sc["facts"] = [f for f in sc.get("facts") or [] if f.get("id") not in unused]
        info["unused_dropped"] = unused
        rep = verify(sc)
    if rep["facts_unverified"]:
        sc, ri, use = rewrite_without(sc, rep["facts_unverified"], ask=ask_rewrite)
        if use:
            spend.append(use)
        if ri["core_failed"]:
            rows, segs = {r["id"]: r for r in rep["facts"]}, sc.get("segments") or []
            info["core_unverified"] = sorted({f for k in ri["core_failed"] for f in segs[k].get("facts") or [] if f in rep["facts_unverified"]})
            info["core_why"] = {i: dict(claim=rows[i].get("claim"), url=rows[i].get("url"), why=_why(rows[i]),
                                        beats=[segs[k].get("beat") or "hook" for k in ri["core_failed"] if i in (segs[k].get("facts") or [])])
                                for i in info["core_unverified"]}
            log(f"fact check: the core cannot stand without {info['core_unverified']}")
            return done(json.loads(json.dumps(original)), rep, ok=False)
        info.update(rewritten=ri["rewritten"], dropped_segments=ri["dropped"], facts_dropped=ri["facts_dropped"])
        log(f"fact check: unverifiable {ri['facts_dropped']} left the film; segments rewritten {ri['rewritten']}, dropped {ri['dropped']}")
        rep = verify(sc)
    for p in rep.get("speech_unbacked") or []:
        s = sc["segments"][p["segment"]]
        s["_check"] = (s.get("_check") or "") + f" words given as {p.get('who') or 'someone'}'s own ({p['words']!r}): {p['why']}"
    changed = {s["_sid"] for s in sc.get("segments") or [] if s.get("_check")}
    if changed:
        sc, failed = check_lines(sc, info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite)
        if failed:
            segs0 = original.get("segments") or []
            info["core_unsupported"] = {str(sid): dict(say=segs0[sid].get("say") if sid < len(segs0) else None, why=w,
                                                       beat=segs0[sid].get("beat") if sid < len(segs0) else None)
                                        for sid, w in failed.items()}
            log(f"line support: the core cannot stand on its quotes: {info['core_unsupported']}"[:600])
            return done(json.loads(json.dumps(original)), verify(original), ok=False)
        rewritten = set(info["rewritten"]) | set(info["support_rewritten"])      # segment ids: no segment was dropped before step 3
        if rewritten:
            sc = check_repeats(sc, rewritten, info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite, ask_repeats=ask_repeats)
        used = {f for s in sc.get("segments") or [] for f in s.get("facts") or []}
        gone = [f.get("id") for f in sc.get("facts") or [] if f.get("id") in replaced and f.get("id") not in used]
        if gone:                               # a replacement no line could stand on leaves the record
            sc["facts"] = [f for f in sc.get("facts") or [] if f.get("id") not in gone]
            info["unused_dropped"] = list(info["unused_dropped"]) + gone
        rep = verify(sc)
    if rep["numbers_unbacked"]:
        unb = set(rep["numbers_unbacked"])
        keep_ord = {id(x) for x in ordinal_items(sc)}
        for k, s in enumerate(sc.get("segments") or []):
            off = [x for x in s.get("show") or [] if id(x) not in keep_ord
                   and set(digits(str(x.get("text") if isinstance(x, dict) else x))) & unb]
            if off:
                info["display_dropped"] += [dict(segment=k, text=str(x.get("text") if isinstance(x, dict) else x)) for x in off]
                s["show"] = [x for x in s.get("show") or [] if not any(x is o for o in off)]
        log(f"fact check: display items with unbacked numbers taken off screen: {info['display_dropped']}")
    return done(sc, rep)


# ----------------------------------------------------------------- verify first: the spine bank, then write from it
#
# 15 Nov (the independent check of dcbe553e): every final line stood on verified quotes, but the stories lost their spine.
# The Dyson idea "Hoover rejected James Dyson, then had to pay for copying his invention" ended as a flat legal line (no
# rejection, no reversal, no price): the writer had cited weak fragments ("1983", "The appeal should be dismissed.") and
# the gates cut every claim those fragments did not carry. Now the spine is researched and verified BEFORE a word is
# written, and the writer writes only from the verified bank. Truth is never traded for punch; punch never dies for truth.

SPINE_BEATS = ("hook", "turn", "payoff", "close")
SPINE_MODEL = "gpt-6.1-sol"              # pinned to OpenAI (ask_openai): Anthropic spend stays at zero
SPINE_ROUNDS = 3                         # search rounds a spine claim gets, each with new queries, before the idea is let go
SPINE_TIERS = (
    "primary sources first: court records and judgments, filings and official registers, the company's or the person's own "
    "statements, newsroom and history pages, and news reports written at the time",
    "news reports and trade press written at the time, and any primary source not tried yet",
    "any readable page that states it: reference works (Wikipedia, encyclopaedias), museums, universities, later news")
EXTRACT_PAGES = 3                        # pages one extraction call reads
EXTRACT_CHARS = 45000                    # of one page's readable text
STORY_SENTENCES = 14                     # sentences that tell the rest of the story, copied from the pages read first
BANK_MAX = 40
SKIP_HOSTS = ("commons.wikimedia.org",)  # a picture category holds no sentence of the story (identity of the host only)
FETCH_THREADS = 6

SPINE_CLAIMS_SYSTEM = """You are the story editor of a narrated documentary film of about a minute and a half. Before a word is written, the story's spine is researched as claims. From the idea below write its spine: for each beat (hook, turn, payoff, close) the one or two facts it cannot do without, each one plain checkable sentence naming who did what to whom, when, and the figure, with no detail the point does not need. The hook's claims are both halves of its collision (a rejection AND what came of it); the turn's claim is the moment the story turns; the payoff's claim names the figure and who paid or got it; the close's claim is the fact that makes the irony true. Use only what the idea says.

Answer with one JSON object only: {"spine": [{"beat": "hook" | "turn" | "payoff" | "close", "claim": the claim}]}"""

RESEARCH_SPINE_SYSTEM = """You are the researcher of a narrated documentary film. The film is written only after every claim its story stands on is found stated on a page a plain script can read. For each claim below, search the web and copy sentences that state it, word for word exactly as they stand on the page.

Search with new queries for each claim, never one already tried (listed with the claim), and at least two different ways for each claim before you give up on it. This round: {tier}.

Cite only pages a plain script can read: no paywall, no login, no bot wall, no PDF, no document mirror (studylib, scribd), and never a page listed under a claim as failed with a refusal (a 403, a 429, unreachable): it refused this script and will refuse it again, so find another page that states the same claim. Copy a whole sentence (or a clause of at least eight words) that states the claim with its subject and its verb: never a bare date, a number, a heading or a fragment. A sentence that only implies the claim, or a paraphrase, is worth nothing: the sentence is checked by fetching the page and finding it there. When no readable page states a claim, give no sources for it; never stretch a source.

For each sentence say whose statement it is (the court, the company, the newspaper, the author), the person whose own words it quotes when it quotes someone (else null), and when (the date of the event it states, else the page's date, else null).

Answer with one JSON object only: {"claims": [{"id": the claim id, "queries": [every search you ran for it], "sources": [up to four {"url": the page, "quote": the sentence copied word for word, "source": whose statement, "speaker": a person or null, "when": a date or null}]}]}"""

EXTRACT_SYSTEM = """You copy sentences from web pages a script has already fetched, for the fact bank of a narrated documentary film. Every sentence you give is checked against the page text below word for word, so copy it exactly: the same words in the same order (you may leave out a footnote mark).

For each claim, copy every sentence on these pages that states it (or the part of it the page states). Then copy the number of sentences asked for that tell the rest of this story: what happened, to whom, when, the figures, the turn, anyone's own words. Each one a whole sentence, or a clause of at least eight words that makes sense alone: never a heading, a caption, a short list item, a bare date or a number.

For each sentence say whose statement it is (the court, the company, the newspaper, the author), the person whose own words it quotes when it quotes someone (else null), and when (the date of the event it states, as the page gives it, else null).

Answer with one JSON object only: {"quotes": [{"page": the page number, "quote": the sentence, "claims": [ids of the claims it states, or []], "source": whose statement, "speaker": a person or null, "when": a date or null}]}"""

CLAIMS_JUDGE_SYSTEM = """You are the fact checker of a narrated documentary film. Before it is written, each claim of its spine must be stated by quotes found word for word on their pages. For each claim decide whether the quotes below state its point in full: who did what to whom, the reversal, the figure and who paid or got it. Quotes may be combined when together they plainly state it (one names the payer, another the amount of the same payment). A quote that only suggests the claim, or states a different or weaker version (an amount accepted without who paid it, a ruling without whom it went against), does not state it. A detail the point does not need (an exact day, a place) may be missing: the claim is then stated, and "as_stated" gives the claim exactly as the quotes state it, without that detail.

Answer with one JSON object only: {"claims": [{"id": the claim id, "stated": true or false, "by": [ids of the quotes that state it], "as_stated": the claim as the quotes state it (when stated), "missing": what no quote states (when not stated)}]}"""


def idea_spine(idea):
    """[{beat, claim, evidence: [{url, quote}]}]: the idea's own spine, as the ideas stage gave it (shape only)."""
    out = []
    for x in idea.get("spine") or [] if isinstance(idea.get("spine"), list) else []:
        if not isinstance(x, dict) or x.get("beat") not in SPINE_BEATS or not str(x.get("claim") or "").strip():
            continue
        ev = []
        for c in (([x.get("source")] if isinstance(x.get("source"), dict) else []) +
                  [c for c in x.get("sources") or [] if isinstance(c, dict)] + ([x] if x.get("url") and x.get("quote") else [])):
            if str(c.get("url") or "").startswith("http") and str(c.get("quote") or "").strip():
                ev.append(dict(url=str(c["url"]).strip(), quote=" ".join(str(c["quote"]).split())))
        out.append(dict(beat=x["beat"], claim=" ".join(str(x["claim"]).split()), evidence=ev))
    return out


def spine_claims(idea, ask=None):
    """([claims], usage): the idea's spine as claims {id, beat, claim, evidence}, one or two a beat (hook, turn, payoff,
    close): the idea's own when it gives every beat, else the beats it lacks written by one model call (no web)."""
    rows, use = idea_spine(idea), None
    if not {r["beat"] for r in rows} >= set(SPINE_BEATS):
        ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=6000, what="spine claims",
                                                         model=SPINE_MODEL))
        view = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff") if idea.get(k)}
        try:
            text, use = ask(SPINE_CLAIMS_SYSTEM, f"# The idea\n{json.dumps(view, ensure_ascii=False)}")
            got = json_block(text)
            got = idea_spine(dict(spine=got.get("spine") if isinstance(got, dict) else None))
        except Exception:  # noqa: BLE001 - no spine: the idea cannot be researched and the next one is taken
            got = []
        have = {r["beat"] for r in rows}
        rows += [r for r in got if r["beat"] not in have]
    return [dict(id=f"c{k + 1}", beat=r["beat"], claim=r["claim"], evidence=r["evidence"], queries=[], stated=False)
            for k, r in enumerate(rows)], use


def _host(url):
    return urllib.parse.urlsplit(str(url)).netloc.lower()


def _cand(x, claim_ids=()):
    """A candidate quote in one shape, or None (shape only)."""
    if not isinstance(x, dict):
        return None
    url, q = str(x.get("url") or "").strip(), " ".join(str(x.get("quote") or "").split())
    if not url.startswith("http") or not q:
        return None
    sp = x.get("speaker")
    own = [i for i in x.get("for") or [] if isinstance(i, str)] if isinstance(x.get("for"), list) else []
    return dict(url=url, quote=q, source=str(x.get("source") or "") or None, speaker=str(sp) if sp and str(sp).lower() != "null" else None,
                when=str(x.get("when") or "") or None, **{"for": sorted(set(claim_ids) | set(own))})


def _take(bank, cands, how, log):
    """Each candidate's quote looked for on its page through every door (check_fact: the live page, Wikipedia's API,
    the Wayback copy; a thin quote refused); the found ones join the bank once (by their normalised words). Returns the
    ids added; the misses are kept in bank["failed"] with why."""
    from concurrent.futures import ThreadPoolExecutor
    cands = [c for c in cands if c]
    with ThreadPoolExecutor(FETCH_THREADS) as ex:
        rows = list(ex.map(lambda c: check_fact(dict(id="candidate", quote=c["quote"], url=c["url"], also=[])), cands))
    added = []
    for c, row in zip(cands, rows):
        if not row["found"]:
            bank["failed"].append(dict(url=c["url"], quote=c["quote"][:200], why=_why(row), how=how, **{"for": c["for"]}))
            continue
        key = norm(c["quote"])
        old = next((f for f in bank["facts"] if norm(f["quote"]) == key), None)
        if old:
            old["for"] = sorted(set(old.get("for") or []) | set(c["for"]))
            continue
        if len(bank["facts"]) >= BANK_MAX and not c["for"]:
            bank.setdefault("over_cap", 0)
            bank["over_cap"] += 1           # a story sentence past the cap; a quote found for a spine claim always joins
            continue
        f = dict(id=f"b{len(bank['facts']) + 1}", quote=c["quote"], url=c["url"], source=c.get("source") or _host(c["url"]),
                 speaker=c.get("speaker"), when=c.get("when"), read=row.get("read"), how=how, **{"for": c["for"]})
        bank["facts"].append(f)
        added.append(f["id"])
    if cands:
        log(f"spine bank ({how}): {len(added)} of {len(cands)} quotes verified on their pages; the bank holds {len(bank['facts'])}")
    return added


def extract_quotes(urls, claims, story=STORY_SENTENCES, ask=None):
    """([candidates], usage): one model call (no web) reads pages already fetched (their readable text, `_RAW`) and copies
    the sentences that state each claim, plus `story` sentences that tell the rest of the story; each is then verified
    word for word on its page like any quote."""
    pages = [(u, _RAW[u]) for u in urls if _RAW.get(u)][:EXTRACT_PAGES]
    if not pages:
        return [], None
    ask = ask or (lambda system, content: ask_openai(system, content, effort="low", max_tokens=12000, what="spine extract",
                                                     model=SPINE_MODEL))
    content = (f"# Claims\n{json.dumps([dict(id=c['id'], claim=c['claim']) for c in claims], ensure_ascii=False)}\n\n"
               f"# Sentences that tell the rest of the story: {story}\n\n" +
               "\n\n".join(f"# Page {k}: {u}\n{t[:EXTRACT_CHARS]}" for k, (u, t) in enumerate(pages)))
    text, use = ask(EXTRACT_SYSTEM, content)
    try:
        got = json_block(text)
    except ValueError:
        return [], use
    ids, out = {c["id"] for c in claims}, []
    for g in (got.get("quotes") if isinstance(got, dict) else None) or []:
        if isinstance(g, dict) and isinstance(g.get("page"), int) and 0 <= g["page"] < len(pages):
            c = _cand(dict(g, url=pages[g["page"]][0], **{"for": [i for i in g.get("claims") or [] if i in ids]}))
            if c:
                out.append(c)
    return out, use


def _extract(bank, urls, claims, story, ask, spend, log, how):
    """Pages read but not yet copied from (pictures' pages aside) go through extract_quotes; what verifies joins."""
    todo = [u for u in dict.fromkeys(urls) if u and u in _RAW and u not in bank["extracted"] and _host(u) not in SKIP_HOSTS]
    todo = todo[:EXTRACT_PAGES]
    if not todo:
        return []
    bank["extracted"] += todo
    if story >= STORY_SENTENCES:
        bank["story_copied"] += todo
    try:
        cands, use = extract_quotes(todo, claims, story=story, ask=ask)
    except Exception as e:  # noqa: BLE001 - no copy from the pages: the research rounds still run
        log(f"spine bank: extraction failed: {e!r}"[:200])
        return []
    if use:
        spend.append(use)
    return _take(bank, cands, how, log)


def _open_all(urls):
    """Pages fetched (cached) so their readable text is in hand for an extraction call; refusals are ignored here."""
    from concurrent.futures import ThreadPoolExecutor

    def one(u):
        try:
            page_text(u)
        except Exception:  # noqa: BLE001 - an unreadable page gives nothing to copy
            pass
    with ThreadPoolExecutor(FETCH_THREADS) as ex:
        list(ex.map(one, [u for u in dict.fromkeys(urls) if u and _host(u) not in SKIP_HOSTS]))


def judge_claims(claims, facts, ask=None):
    """({claim id: {"stated", "by", "as_stated", "missing"}}, usage): one model call (no web) decides whether the
    verified quotes state each claim's point in full. A claim the answer leaves out, or calls stated without naming a
    verified quote, is not stated (fail closed)."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=8000, what="spine judge",
                                                     model=SPINE_MODEL))
    ids = {f["id"] for f in facts}
    content = (f"# The spine's claims\n{json.dumps([dict(id=c['id'], beat=c['beat'], claim=c['claim']) for c in claims], ensure_ascii=False)}\n\n"
               f"# Verified quotes (each found word for word on its page)\n"
               f"{json.dumps([dict(id=f['id'], quote=f['quote'], source=f.get('source'), speaker=f.get('speaker'), when=f.get('when'), url=f['url']) for f in facts], ensure_ascii=False, indent=0)}")
    got, use = {}, None
    try:
        text, use = ask(CLAIMS_JUDGE_SYSTEM, content)
        ans = json_block(text)
        got = {g.get("id"): g for g in (ans.get("claims") if isinstance(ans, dict) else None) or [] if isinstance(g, dict)}
    except Exception:  # noqa: BLE001 - no judgement: every claim counts as not stated
        got = {}
    out = {}
    for c in claims:
        g = got.get(c["id"]) or {}
        by = [b for b in g.get("by") or [] if b in ids] if isinstance(g.get("by"), list) else []
        stated = g.get("stated") is True and bool(by)
        out[c["id"]] = dict(stated=stated, by=by, as_stated=" ".join(str(g.get("as_stated") or c["claim"]).split()) if stated else None,
                            missing="" if stated else (str(g.get("missing") or "") or ("no judgement" if not g else "no verified quote named")))
    return out, use


def _judge(bank, claims, ask, spend, log, how):
    if not bank["facts"]:
        for c in claims:
            c.update(stated=False, by=[], missing=c.get("missing") or "no verified quote yet")
        return
    verdict, use = judge_claims(claims, bank["facts"], ask=ask)
    if use:
        spend.append(use)
    for c in claims:
        v = verdict[c["id"]]
        c.update(stated=v["stated"], by=v["by"], missing=v["missing"])
        if v["stated"]:
            c["as_stated"] = v["as_stated"]
    log(f"spine bank ({how}): stated {[c['beat'] for c in claims if c['stated']]}, not yet "
        f"{[(c['beat'], c['missing'][:80]) for c in claims if not c['stated']]}"[:600])


def research_spine(story, claims, rnd, bank=None, ask=None):
    """([candidates], {claim id: [queries]}, usage): one web-search call (SPINE_MODEL on OpenAI) asks, for each claim not
    yet stated, other readable pages that state it, with queries never tried before; round `rnd` sets where to look
    (SPINE_TIERS: primary sources first)."""
    n = len(claims)
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=16000, searches=1,
                                                     what="spine research", model=SPINE_MODEL, max_tool_calls=min(16, 2 * n + 2)))
    rows = [dict(id=c["id"], beat=c["beat"], claim=c["claim"], tried=c.get("queries") or [],
                 why_not_yet=c.get("missing") or "no readable page found yet",
                 failed=[dict(url=x["url"], why=x["why"][:120]) for x in (bank or {}).get("failed") or [] if c["id"] in x.get("for", [])][-6:])
            for c in claims]
    have = [dict(id=f["id"], quote=f["quote"], url=f["url"]) for f in (bank or {}).get("facts") or []]
    content = (f"# The story\n{json.dumps(story, ensure_ascii=False)}\n\n"
               f"# Claims to source (round {rnd + 1} of {SPINE_ROUNDS})\n{json.dumps(rows, ensure_ascii=False, indent=1)}\n\n"
               f"# Verified already (no need to search for these sentences again)\n{json.dumps(have, ensure_ascii=False)}")
    text, use = ask(RESEARCH_SPINE_SYSTEM.replace("{tier}", SPINE_TIERS[min(rnd, len(SPINE_TIERS) - 1)]), content)
    try:
        got = json_block(text)
    except ValueError:
        return [], {}, use
    ids, cands, queries = {c["id"] for c in claims}, [], {}
    for g in (got.get("claims") if isinstance(got, dict) else None) or []:
        if not isinstance(g, dict) or g.get("id") not in ids:
            continue
        queries[g["id"]] = [" ".join(str(q).split()) for q in g.get("queries") or [] if str(q).strip()]
        cands += [c for c in (_cand(x, [g["id"]]) for x in (g.get("sources") or [])[:4]) if c]
    return cands, queries, use


def _research_round(bank, story, claims, rnd, asks, spend, log, why=None):
    """One search round for `claims`: new queries (never one tried), the candidates verified on their pages, the new
    pages copied from, the claims judged again."""
    for c in claims:
        if why and why.get(c["id"]):
            c["missing"] = why[c["id"]]
    try:
        cands, queries, use = research_spine(story, claims, rnd, bank=bank, ask=asks.get("research"))
    except Exception as e:  # noqa: BLE001 - a failed search round counts as a round with nothing found
        log(f"spine bank: research round {rnd + 1} failed: {e!r}"[:200])
        cands, queries, use = [], {}, None
    api = list((use or {}).pop("queries", None) or []) if isinstance(use, dict) else []
    if use:
        spend.append(use)
    for c in claims:
        c["queries"] = list(dict.fromkeys((c.get("queries") or []) + (queries.get(c["id"]) or api)))
        c["rounds"] = int(c.get("rounds") or 0) + 1
    bank["rounds"].append(dict(round=rnd + 1, claims=[c["id"] for c in claims], candidates=len(cands), queries=api or
                               sorted({q for v in queries.values() for q in v})))
    added = _take(bank, cands, f"research {rnd + 1}", log)
    _open_all([c["url"] for c in cands])
    added += _extract(bank, [c["url"] for c in cands], claims, 4, asks.get("extract"), spend, log, f"page copy {rnd + 1}")
    _judge(bank, claims, asks.get("judge"), spend, log, f"round {rnd + 1}")
    return added


def spine_view(bank):
    """[{beat, claim, by}]: the spine as the writer and the later point checks read it (stated claims only, as stated)."""
    return [dict(beat=c["beat"], claim=c.get("as_stated") or c["claim"], by=list(c.get("by") or []))
            for c in bank.get("claims") or [] if c.get("stated")]


def bank_facts(bank):
    """The bank as the script's facts: every verified quote with its page, whose statement it is and when."""
    return [dict(id=f["id"], claim=f.get("claim") or f["quote"], quote=f["quote"], url=f["url"], source=f.get("source"),
                 speaker=f.get("speaker"), when=f.get("when"), also=[]) for f in bank.get("facts") or []]


def spine_bank(idea, log=print, ask_claims=None, ask_research=None, ask_extract=None, ask_judge=None, rounds=SPINE_ROUNDS):
    """(bank, spend): VERIFY FIRST. The idea's spine (hook, turn, payoff, close) is researched as claims and verified
    before a word is written:
      0. the idea's own evidence for each claim is fetched and looked for on its page (every door: check_fact), and the
         pages it read are copied from (extract_quotes: one call, the sentences that state each claim and that tell the
         rest of the story), each sentence verified word for word; a model judges which claims the verified quotes state;
      1. a claim not stated gets up to `rounds` search rounds, each with queries never tried before, primary sources
         first (SPINE_TIERS: court records, filings, the company's own statements, news written at the time; then any
         readable page), its candidates verified the same way and the new pages copied from, then judged again.
    bank["ok"] only when every spine claim is stated by verified quotes; else bank["why"] names each claim, what is
    missing and the queries tried, and the caller takes the next idea. Every quote is a meaningful fragment (thin_quote)."""
    spend = []
    claims, use = spine_claims(idea, ask=ask_claims)
    if use:
        spend.append(use)
    bank = dict(idea=idea.get("title"), claims=claims, facts=[], rounds=[], failed=[], extracted=[], story_copied=[], ok=False, why="")
    lack = sorted(set(SPINE_BEATS) - {c["beat"] for c in claims})
    if lack:
        bank["why"] = f"the idea gives no spine claim for {lack}"
        return bank, spend
    asks = dict(research=ask_research, extract=ask_extract, judge=ask_judge)
    story = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff") if idea.get(k)}
    cands = [_cand(e, [c["id"]]) for c in claims for e in c.get("evidence") or []]
    _take(bank, cands, "idea", log)
    pages = [c["url"] for c in cands if c] + [u for u in idea.get("sources") or [] if isinstance(u, str) and u.startswith("http")]
    _open_all(pages)
    _extract(bank, pages, claims, STORY_SENTENCES, ask_extract, spend, log, "page copy")
    _judge(bank, claims, ask_judge, spend, log, "the idea's own sources")
    for rnd in range(rounds):
        missing = [c for c in claims if not c["stated"]]
        if not missing:
            break
        _research_round(bank, story, missing, rnd, asks, spend, log)
    if len([f for f in bank["facts"] if not f.get("for")]) < 4:
        # a thin bank: the pages found in the search rounds that hold the spine are copied from for the rest of the story
        used = [f["url"] for f in bank["facts"] if f["url"] not in bank["story_copied"]]
        bank["extracted"] = [u for u in bank["extracted"] if u not in used]
        _extract(bank, used, [c for c in claims if c["stated"]], STORY_SENTENCES, ask_extract, spend, log, "story copy")
    missing = [c for c in claims if not c["stated"]]
    hook_ok = all(c["stated"] for c in claims if c["beat"] == "hook")
    if missing and hook_ok and all(c["beat"] == "close" for c in missing):
        # the close reframes the hook (TEARDOWN section 12): its material is the hook's verified facts; a close claim of
        # its own that no readable page states never costs a verified story (recorded, never silent)
        bank["close_from_hook"] = [dict(claim=c["claim"], missing=c.get("missing")) for c in missing]
        log(f"spine bank: the close's own claim is not stated ({missing[0].get('missing', '')[:120]}); the close reframes "
            "the hook on the hook's verified facts")
        missing = []
    bank["ok"] = not missing
    bank["why"] = "" if not missing else (
        f"no readable page states these spine claims after {rounds} search rounds: " +
        "; ".join(f"{c['beat']}: {c['claim']} (missing: {c.get('missing') or '?'}; queries tried: {len(c.get('queries') or [])})"
                  for c in missing))
    log(f"spine bank: {'verified' if bank['ok'] else 'NOT verified'}: {len(bank['facts'])} quotes, "
        f"{sum(c['stated'] for c in claims)}/{len(claims)} claims stated" + ("" if bank["ok"] else f"; {bank['why'][:300]}"))
    return bank, spend


def spine_again(bank, idea, cids, why, log=print, ask_research=None, ask_extract=None, ask_judge=None):
    """(bank, spend): back to step 1 for some claims (the bank could not carry their point in a line): one more search
    round for them, with queries never tried, the reason given; their verdicts are judged again."""
    spend = []
    claims = [c for c in bank["claims"] if c["id"] in set(cids)]
    if not claims:
        return bank, spend
    story = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff") if idea.get(k)}
    rnd = max([int(c.get("rounds") or 0) for c in claims] + [0])
    _research_round(bank, story, claims, rnd, dict(research=ask_research, extract=ask_extract, judge=ask_judge), spend, log, why=why)
    bank["ok"] = all(c["stated"] for c in bank["claims"] if not (c["beat"] == "close" and bank.get("close_from_hook")))
    return bank, spend


# ----------------------------------------------------------------- write from the bank

BANK_WRITE_MODEL = OA_MODEL

BANK_WRITE_RULES = (
    "# The spine\nThe hook, the turn, the payoff and the close each carry the idea's point (the reversal, the price, the irony) "
    "in words the bank supports. The spine below gives, for each beat, the claim it must carry and the bank facts that state "
    "it: write that beat from those facts, as sharply as they allow. The first segment is the hook: it works in the first "
    "second, a known name and the collision itself (when the idea is 'X rejected Y, then paid for copying him', the hook says "
    "the rejection AND the price), never a greeting, a question to the viewer, or one flat half of the story. The turn is "
    "said as a turn. The payoff lands its figure and who paid or got it. The last segment is the close: it reframes the hook "
    "in one sentence that says the hook's collision again, resolved, in new words, with the facts that carry it (never a "
    "moral, a lesson or someone's comment on the story, never flatter than the hook). A true line and a "
    "sharp line are the same line here: the bank holds the words for the point, so never write a flat line where the bank "
    "holds the point. No call to action, no 'follow for more'.\n\n"
    "Mark the beats: segment 0 is the hook, exactly one segment the turn, one the payoff, the last segment the close; the "
    "others setup or low (one low where the story is at its worst).\n\n"
    "Every line says only what the quotes of the facts it cites state, each fact read with whose statement it is and when: "
    "an order of events only when the quotes or their dates give it, never a motive, feeling, 'first', 'never' or 'only' "
    "no quote states. Cite in each segment's facts only the bank facts whose words that line uses or paraphrases, never one "
    "for a date or a detail the line does not say. Give someone's own words only when a bank fact holds them word for word "
    "and is that person's (its speaker).\n\n")


def write_from_bank(idea, card, form, bank, voices=None, notes=None, effort="high", ask=None):
    """(script, usage): the script written from the verified bank alone (one call, no web: the writer cannot reach a
    fact the bank does not hold). Every segment cites bank facts only; the script's facts are the bank's, and the
    spine is kept on the script (`spine`) for the point checks after it."""
    F = FORMS[form]
    voices = voices or card.get("voice") or list(VOICES)
    ask = ask or (lambda system, content: ask_openai(system, content, effort=effort, max_tokens=48000, what="write from bank",
                                                     model=BANK_WRITE_MODEL))
    system = ("You are the writer and voice director of Valmera's story workflow. You write narration spoken by a synthetic narrator "
              "over real footage and photographs of what it names, for a " + F["name"] + ". You write ONLY from the bank of verified "
              "facts you are given: each is a sentence found word for word on its page, with whose statement it is and when. "
              "Nothing is invented, rounded or dramatised beyond them. You also direct the actor: pace, energy, melody, emphasis "
              "and pauses per segment. Write for the ear: short sentences, concrete nouns, numbers a listener can hold.")
    system += f"\n\n# The bar\n{references()}\n\n" + BANK_WRITE_RULES
    system += ("Return JSON only: {\"title\": a plain title, \"voice\": one voice id, \"pace\": slow|steady|brisk|fast, \"energy\": "
               "low|mid|high, \"hook_line\": the first segment's words, \"close_line\": the last segment's words, \"names\": [every "
               "proper noun the narration says, exactly as spelled], \"segments\": [{\"say\": the words spoken, numbers and years "
               "written as words the way they are said (\"nineteen seventy-three\", \"five thousand dollars\"), no digits, no "
               "abbreviations, no symbols, \"show\": at most two short display strings for this segment as they would be printed, "
               "digits allowed (a year, a figure, a name), or [], \"beat\": hook|setup|turn|low|payoff|close, \"pace\": "
               "slow|steady|brisk|fast, \"pictures\": names from the named list this segment talks about, in order, \"melody\": "
               "falling|rising|level|wide, \"emphasis\": [one or two words of say], \"pause_after\": seconds of silence after it "
               "(0.18 to 0.26 inside the story; 0.35 to 0.5 only where the story turns, at most three times), \"direction\": a few "
               "words for the actor, \"facts\": ids of the bank facts it states, \"quotes\": every span of say that presents "
               "someone's own words as {\"words\": the span exactly as in say, \"who\": whose words, \"fact\": the id of the bank "
               "fact whose quote holds those words word for word}, or []}], \"named\": [{\"name\", \"is\", \"searches\", "
               "\"must_show\"} for every person, place or thing the film shows], " + STYLE_DATA.get(card["id"], "") + "}\n"
               "Every digit in `show` or in the style's data must appear in the quote of a bank fact (only the numbers of a list the "
               "film itself counts out, role ordinal, are exempt).")
    view = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff", "named") if idea.get(k)}
    content = (f"# The idea\n{json.dumps(view, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Form\n{F['name']}: {F['words'][0]}-{F['words'][1]} spoken words in {F['segments'][0]}-{F['segments'][1]} segments "
               f"(about {F['seconds'][0]}-{F['seconds'][1]} s at 175 words a minute). {F['shape']}.\n\n"
               f"# Voices\n{json.dumps({k: VOICES[k] for k in voices if k in VOICES})}\n\n"
               f"# The spine (each beat's claim, as the verified quotes state it, and the bank facts that state it; the close "
               f"reframes the hook on the hook's facts, and its own claim, when given, may support it)\n"
               f"{json.dumps(spine_view(bank), ensure_ascii=False, indent=1)}\n\n"
               f"# The bank: the only facts this film may state\n"
               f"{json.dumps([dict(id=f['id'], quote=f['quote'], source=f.get('source'), speaker=f.get('speaker'), when=f.get('when')) for f in bank['facts']], ensure_ascii=False, indent=0)}\n\n" +
               (f"# Notes from the last attempt\n{notes}\n\n" if notes else ""))
    text, use = ask(system, content)
    sc = json_block(text)
    ids = {f["id"] for f in bank["facts"]}
    for s in sc.get("segments") or []:
        s["facts"] = [f for f in s.get("facts") or [] if f in ids] if isinstance(s.get("facts"), list) else []
        s["quotes"] = [q for q in s.get("quotes") or [] if isinstance(q, dict) and q.get("fact") in ids] if isinstance(s.get("quotes"), list) else []
    sc["facts"] = bank_facts(bank)
    sc["spine"] = spine_view(bank)
    sc["idea_point"] = {k: idea.get(k) for k in ("hook", "turn", "payoff") if idea.get(k)}
    if sc.get("segments"):
        sc["hook_line"], sc["close_line"] = sc["segments"][0].get("say"), sc["segments"][-1].get("say")
    return sc, use


# ----------------------------------------------------------------- the spine held on the written lines

SPINE_JUDGE_SYSTEM = """You are the story editor of a narrated documentary film of about a minute and a half, cut like the best founder-story reels: true AND gripping. Its spine was researched first: for each beat (hook, turn, payoff, close) you get the claim the beat must carry (the idea's point: the reversal, the price, the irony), the line that carries that beat now, and the verified quotes it cites; the idea's own promise is given too. Decide whether the line carries the point in its own words: the claim at its centre AND its tension (both halves of a collision, the price and who paid it). A line that says only one half, buries the point in background, or turns the collision into a flat statement of record has lost it. The close carries its point only when it reframes the hook: the hook's collision said again, resolved, in new words (the best reels end on a reframe or a loop back to the hook); a moral, a lesson, a comment on the story or a repeat of the hook's own words has lost it. Judge the point and the grip only: whether the quotes support the line is checked elsewhere.

Answer with one JSON object only: {"beats": [{"beat": "hook" | "turn" | "payoff" | "close", "carried": true or false, "lost": what the line lacks, in a few words (empty when carried)}]}"""

BANK_WHY = "written from the bank: every line is judged on the quotes it cites before the ear"
HOLD_STEPS = 3


def spine_lines(sc):
    """{beat: segment index} of the lines that carry the spine: the hook is the first line, the turn and the payoff the
    first lines so marked, the close the last line marked close, else the last line. Structure only."""
    segs = sc.get("segments") or []
    out = {"hook": 0} if segs else {}
    for b in ("turn", "payoff"):
        k = next((k for k, s in enumerate(segs) if k and s.get("beat") == b), None)
        if k is not None:
            out[b] = k
    if len(segs) > 1:
        close = [k for k, s in enumerate(segs) if k and s.get("beat") == "close"]
        out["close"] = close[-1] if close else len(segs) - 1
    return out


REFRAME = "it reframes the hook: the hook's collision said again, resolved, in new words ({})"


def spine_points(sc):
    """{segment index: [{"beat", "claim", "by"}]}: the point each core line must carry, from the script's spine. The
    close also carries the hook's point, reframed (the references end on a reframe or a loop back to the hook, never on
    a moral: TEARDOWN section 12)."""
    out, spine = {}, sc.get("spine") or []
    for b, k in spine_lines(sc).items():
        pts = [dict(beat=b, claim=x.get("claim"), by=x.get("by") or []) for x in spine if x.get("beat") == b]
        if b == "close":
            hook = [x for x in spine if x.get("beat") == "hook"]
            pts = [dict(beat=b, claim=REFRAME.format(x.get("claim")), by=x.get("by") or []) for x in hook] + pts
        if pts:
            out.setdefault(k, []).extend(pts)
    return out


def judge_spine(sc, ask=None):
    """({beat: {"carried", "lost", "line"}}, usage): one model call (SUPPORT_MODEL) decides whether the line of each
    spine beat carries the beat's point (its claim and its tension) in its words. A beat without a line, or left out of
    the answer, is not carried (fail closed); a failed call is asked once more."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=6000, what="spine point",
                                                     model=SUPPORT_MODEL))
    segs = sc.get("segments") or []
    lines = spine_lines(sc)
    points = spine_points(sc)
    qb = {f.get("id"): f.get("quote") for f in sc.get("facts") or []}
    rows = []
    for b in SPINE_BEATS:
        if not any(x.get("beat") in ((b, "hook") if b == "close" else (b,)) for x in sc.get("spine") or []):
            continue
        k = lines.get(b)
        rows.append(dict(beat=b, point=[p["claim"] for p in points.get(k, []) if p["beat"] == b], line=k,
                         say=segs[k].get("say") if k is not None else None,
                         quotes=[qb[f] for f in (segs[k].get("facts") or []) if f in qb] if k is not None else []))
    if not rows:
        return {}, None
    promise = {k: v for k, v in (sc.get("idea_point") or {}).items() if v}
    content = ("# The whole script (context)\n" + "\n".join(f"{k}. {s.get('say')}" for k, s in enumerate(segs)) +
               (f"\n\n# The idea's own promise\n{json.dumps(promise, ensure_ascii=False)}" if promise else "") +
               f"\n\n# The spine's beats: the point each must carry, the line that carries it now, its quotes\n"
               f"{json.dumps([r for r in rows if r['line'] is not None], ensure_ascii=False, indent=1)}")
    got, use = None, None
    for _ in range(2):
        try:
            text, use = ask(SPINE_JUDGE_SYSTEM, content)
            ans = json_block(text)
            got = {g.get("beat"): g for g in (ans.get("beats") if isinstance(ans, dict) else None) or [] if isinstance(g, dict)}
            break
        except Exception:  # noqa: BLE001 - asked once more, then every beat counts as not carried
            got = None
    out = {}
    for r in rows:
        g = (got or {}).get(r["beat"])
        if r["line"] is None:
            out[r["beat"]] = dict(carried=False, lost=f"no line carries the {r['beat']}", line=None)
        elif g is None:
            out[r["beat"]] = dict(carried=False, lost="no judgement", line=r["line"])
        else:
            out[r["beat"]] = dict(carried=g.get("carried") is True, lost=str(g.get("lost") or ""), line=r["line"])
    return out, use


def hold_spine(script, bank, idea=None, log=print, ask_judge=None, ask_rewrite=None, ask_repeats=None, ask_point=None,
               again=None, style=None):
    """(script, report, spend, bank): the script written from the bank, held to its spine before the ear.
      1. every line is judged on the quotes it cites (check_lines: an unsupported line is rewritten on the bank, at most
         twice, else dropped; a core line, the close included, is restated or the script fails); a supported line keeps
         citing only the facts it uses;
      2. the line of each spine beat is judged for its point (judge_spine). A beat that lost it is rewritten on the bank
         to carry the point; when that does not carry it, its claims go back to step 1 (`again`: one more search round,
         new queries) and the line is rewritten on the grown bank; still lost after that, the report is not ok and the
         caller writes again or takes the next idea. A flat line is never kept as the spine.
    The words that hold the spine become every line's `say_first`: the later point checks (writer.final_support) compare
    with them."""
    original = json.loads(json.dumps(script))
    sc = json.loads(json.dumps(script))
    for k, s in enumerate(sc.get("segments") or []):
        s["_sid"] = k
        s["_check"] = BANK_WHY
    spend = []
    info = dict(lines_checked=[], support_rewritten=[], support_dropped=[], repeats=[], repeat_rewritten=[], repeat_dropped=[],
                uncited=[], spine=[], spine_rewritten=[], spine_failed=[], researched=[], core_unsupported={})

    def done(sc_, ok, why=""):
        for s in sc_.get("segments") or []:
            s.pop("_sid", None)
            s.pop("_check", None)
            if ok:
                s["say_first"], s["facts_first"] = s.get("say"), list(s.get("facts") or [])
        rep_ = verify(sc_)
        ok = bool(ok and rep_["ok"])
        return sc_, dict(info, ok=ok, why=why or ("" if ok else "the script does not verify"), verify=rep_,
                         usd=round(sum(float((u or {}).get("usd") or 0) for u in spend), 4)), spend, bank

    sc, failed = check_lines(sc, info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite, style=style, stage="bank")
    if failed:
        segs0 = original.get("segments") or []
        info["core_unsupported"] = {str(sid): dict(say=segs0[sid].get("say") if sid < len(segs0) else None, why=w,
                                                   beat=segs0[sid].get("beat") if sid < len(segs0) else None)
                                    for sid, w in failed.items()}
        return done(original, False, "core_unsupported")
    if info["support_rewritten"]:
        sc = check_repeats(sc, set(info["support_rewritten"]), info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite,
                           ask_repeats=ask_repeats, style=style, stage="bank")
    lost = {}
    for step in range(HOLD_STEPS):
        pv, use = judge_spine(sc, ask=ask_point)
        if use:
            spend.append(use)
        segs = sc["segments"]
        info["spine"].append(dict(step=step, beats={b: dict(v, say=segs[v["line"]].get("say") if v["line"] is not None else None)
                                                    for b, v in pv.items()}))
        lost = {b: v for b, v in pv.items() if not v["carried"]}
        if not lost:
            log(f"spine: every beat carries its point ({sorted(pv)})")
            return done(sc, True)
        log(f"spine (step {step}): lost {[(b, v['lost'][:80]) for b, v in lost.items()]}"[:400])
        if step == HOLD_STEPS - 1:
            break
        if step == 1:
            if again is None:
                break
            cids = [c["id"] for c in bank["claims"] if c["beat"] in lost]
            reasons = {c["id"]: f"the line for the {c['beat']} could not carry it: {lost[c['beat']]['lost']}" for c in bank["claims"]
                       if c["beat"] in lost}
            n0 = len(bank["facts"])
            bank, more = again(bank, cids, reasons)
            spend += [u for u in more if u]
            have = {f.get("id") for f in sc.get("facts") or []}
            sc["facts"] = list(sc.get("facts") or []) + [f for f in bank_facts(bank) if f["id"] not in have]
            sc["spine"] = spine_view(bank)
            info["researched"].append(dict(claims=cids, new_quotes=len(bank["facts"]) - n0))
        lines = spine_lines(sc)
        targets = {lines[b]: b for b in lost if lines.get(b) is not None}
        if not targets:
            info["spine_failed"].append(dict(step=step, beats=sorted(lost), why="no line is marked for these beats"))
            break
        points = spine_points(sc)
        hook_now = sc["segments"][lines["hook"]].get("say") if "hook" in lines else ""
        why = {}
        for k, b in targets.items():
            pts = [p for p in points.get(k, []) if p["beat"] == b]
            ids = ", ".join(sorted({i for p in pts for i in p["by"]}))
            if b == "close":
                why[k] = (f"it must carry the close's point and it does not ({lost[b]['lost'] or 'the point is flat'}): the close "
                          f"reframes the hook ({hook_now!r}): the hook's collision said again, resolved, in new words, with the "
                          f"facts that state it ({ids}); never a moral, a lesson or someone's comment on the story, never the "
                          "hook's own words again. The points: " + "; ".join(p["claim"] for p in pts))
            else:
                why[k] = (f"it must carry the {b}'s point and it does not ({lost[b]['lost'] or 'the point is flat'}): "
                          + "; ".join(p["claim"] for p in pts) +
                          f". Say that point as sharply as the facts that state it allow ({ids}), in words those quotes support")
        before = json.loads(json.dumps(sc))
        sc2, ri, use = rewrite_without(sc, (), ask=ask_rewrite, targets=sorted(targets), why=why, style=style)
        if use:
            spend.append(use)
        if ri["core_failed"] or not ri["rewritten"]:
            info["spine_failed"] += [dict(step=step, beat=b, line=k, why=(ri.get("answer") or {}).get(k) or "no line restated")
                                     for k, b in targets.items()]
            continue
        n0 = len(info["lines_checked"])
        sc2, failed = check_lines(sc2, info, spend, log, ask_judge=ask_judge, rounds=0, style=style, stage="spine")
        if failed or len(sc2["segments"]) != len(before["segments"]):
            judged = {r.get("sid"): r for r in info["lines_checked"][n0:]}
            info["spine_failed"] += [dict(step=step, beat=b, line=k, why="its new words do not stand on their quotes: " +
                                          str((judged.get(before["segments"][k]["_sid"]) or {}).get("why") or "a line was dropped"))
                                     for k, b in targets.items()]
            sc = before
            for s in sc["segments"]:
                s.pop("_check", None)
            continue
        info["spine_rewritten"] += [sc2["segments"][k]["_sid"] for k in ri["rewritten"]]
        sc = sc2
    return done(sc, False, "the spine's point is not carried: " + "; ".join(f"{b}: {v['lost']}" for b, v in lost.items()))


# ----------------------------------------------------------------- the final support pass: the last text gate

POINT_SYSTEM = """You are the story editor of a narrated documentary film of about a minute. Its core lines (the hook that must stop a viewer in the first second, the turn, the payoff) were rewritten after fact checking and for the narrator's ear. For each one you get the line as the writer first wrote it and as it stands now. Decide whether the line now still makes the same point: the claim at its centre (who did what, what was at stake) and its tension (the surprise, the collision or the stake that makes a viewer need the next line). The new line may name the person the sources name instead of a company, drop a detail no source states, and change its wording and rhythm; it has lost the point when its central claim is gone, buried or softened into background, or when its collision or stake is gone (a flat statement where there was a turn of the knife).

When a line comes with "point" (the claim the story's spine gives its beat: the reversal, the price, the irony), it has also lost its point when it no longer carries that claim's collision, price or irony, whatever its first words were.

Answer with one JSON object only: {"lines": [{"i": the line index, "kept": true or false, "lost": what the line lost and what it must win back, in a few words (empty when kept)}]}"""

FINAL_WHY = "the final pass: every line of the script is judged on its verified quotes after the ear's rewrite"


def judge_point(script, idx, ask=None):
    """({index: {"kept", "lost"}}, usage): one model call (SUPPORT_MODEL) compares each core line in `idx` as first
    written (`_origin`) with its words now, and says whether it still makes its point (its claim and its tension).
    No answer leaves the line as it is (a point is never rewritten on a guess)."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=6000, what="hook point",
                                                     model=SUPPORT_MODEL))
    segs = script.get("segments") or []
    points = spine_points(script)
    rows = [dict(i=k, beat=segs[k].get("beat") or ("hook" if k == 0 else None), first=segs[k].get("_origin"), now=segs[k].get("say"),
                 **(dict(point=[p["claim"] for p in points[k]]) if k in points else {}))
            for k in idx if _valid_i(k, len(segs))]
    if not rows:
        return {}, None
    content = ("# The whole script now (context)\n" + "\n".join(f"{k}. {s.get('say')}" for k, s in enumerate(segs)) +
               f"\n\n# Core lines: as first written, and now\n{json.dumps(rows, ensure_ascii=False, indent=1)}")
    try:
        text, use = ask(POINT_SYSTEM, content)
        ans = json_block(text)
    except Exception:  # noqa: BLE001
        return {}, None
    out = {}
    for g in ans.get("lines") or [] if isinstance(ans, dict) else []:
        if isinstance(g, dict) and _valid_i(g.get("i"), len(segs)) and g["i"] in idx:
            out[g["i"]] = dict(kept=g.get("kept") is not False, lost=str(g.get("lost") or ""))
    return out, use


def final_support(script, log=print, ask_judge=None, ask_rewrite=None, ask_repeats=None, ask_point=None, style=None,
                  rounds=SUPPORT_ROUNDS):
    """(script, report, spend): the last text gate before the script is frozen, run after the ear's rewrite (and after
    any other change to what the narrator says).

      1. EVERY line is judged against the verified quotes it cites, in one batched call (judge_support: every claim
         stated by a quote; every span given as someone's own words verbatim, word for word, in one of them). An
         unsupported line is rewritten on its quotes in the spoken style it has now (`style`) and judged again, at most
         `rounds` times, else dropped; a core line (hook, turn, payoff) is restated or the pass fails
         (report.ok False, report.core_unsupported).
      2. a rewrite that says again what another line says is caught (check_repeats), rewritten once and judged.
      3. every core line whose words changed since the writer first wrote them is compared with those first words by a
         model (judge_point): when it lost its claim or its tension it is rewritten once more to win the point back on
         its quotes, and judged for support; new words that do not stand are not kept (the supported line stays).
    report.final_lines gives every final line with the verdict of its last judgement."""
    original = json.loads(json.dumps(script))
    sc = json.loads(json.dumps(script))
    for k, s in enumerate(sc.get("segments") or []):
        s["_sid"] = k
        s["_origin"] = s.get("say_first") or s.get("say_written") or s.get("say")
        s["_check"] = FINAL_WHY
    spend = []
    info = dict(lines_checked=[], support_rewritten=[], support_dropped=[], repeats=[], repeat_rewritten=[], repeat_dropped=[],
                point=[], point_rewritten=[], point_kept_old=[], point_failed=[], point_restored=[], core_unsupported={}, uncited=[])

    def done(sc_, ok, why=None):
        for s in sc_.get("segments") or []:
            for k in ("_sid", "_check", "_origin", "facts_first"):
                s.pop(k, None)
        rep_ = verify(sc_)
        ok = bool(ok and rep_["ok"])
        return sc_, dict(info, ok=ok, why=why or ("" if ok else "the final script does not verify"), verify=rep_,
                         usd=round(sum(float((u or {}).get("usd") or 0) for u in spend), 4)), spend

    sc, failed = check_lines(sc, info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite, rounds=rounds, style=style,
                             stage="final")
    if failed:
        segs0 = original.get("segments") or []
        info["core_unsupported"] = {str(sid): dict(say=segs0[sid].get("say") if sid < len(segs0) else None, why=w,
                                                   beat=segs0[sid].get("beat") if sid < len(segs0) else None)
                                    for sid, w in failed.items()}
        log(f"final support: a core line cannot stand on its quotes: {info['core_unsupported']}"[:600])
        return done(json.loads(json.dumps(original)), False, "core_unsupported")
    if info["support_rewritten"]:
        sc = check_repeats(sc, set(info["support_rewritten"]), info, spend, log, ask_judge=ask_judge, ask_rewrite=ask_rewrite,
                           ask_repeats=ask_repeats, style=style, stage="final")
    segs = sc.get("segments") or []
    moved = [k for k in sorted(core_segments(sc)) if speech_words(segs[k].get("_origin")) != speech_words(segs[k].get("say"))]
    if moved:
        pv, use = judge_point(sc, moved, ask=ask_point)
        if use:
            spend.append(use)
        info["point"] += [dict(line=k, sid=segs[k]["_sid"], first=segs[k]["_origin"], now=segs[k].get("say"),
                               kept=(pv.get(k) or {}).get("kept", True), lost=(pv.get(k) or {}).get("lost", ""))
                          for k in moved]
        lost = {k: pv[k]["lost"] for k in moved if k in pv and not pv[k]["kept"]}
        if lost:
            log(f"final support: core lines lost their point: {lost}"[:400])
            before = json.loads(json.dumps(sc))
            why = {k: (f"it lost the point it had as first written ({segs[k]['_origin']!r}): {lost[k] or 'its claim and its tension'}. "
                       "Restate it so it makes that point again, as sharply (its central claim and its tension, the collision "
                       "or the stake), saying only what the quotes it cites state: name whom those quotes name, add nothing "
                       "they do not hold") for k in lost}
            sc2, ri, use = rewrite_without(sc, (), ask=ask_rewrite, targets=sorted(lost), why=why, style=style)
            if use:
                spend.append(use)

            def refused(reason_of):
                # a rejected point rewrite always says why (15 Nov: "the point rewrite failed without a recorded reason")
                info["point_kept_old"] += [segs[k]["_sid"] for k in lost]
                info["point_failed"] += [dict(line=k, sid=segs[k]["_sid"], first=segs[k]["_origin"], now=segs[k].get("say"),
                                              lost=lost[k], why=reason_of(k)) for k in lost]
                log(f"final support: the point rewrite was not kept: {[(k, reason_of(k)[:100]) for k in lost]}"[:500])
            def restore_held(sc_):
                # the point could not be won back in new words: the line the spine was held on (its first words, judged
                # supported and pointed before the ear, `say_first` with `facts_first`) is restored when it still stands
                # on its quotes; a flatter line is kept only when that one does not
                ks = [k for k in lost if sc_["segments"][k].get("_origin")
                      and speech_words(sc_["segments"][k]["_origin"]) != speech_words(sc_["segments"][k].get("say"))]
                if not ks:
                    return sc_
                trial = json.loads(json.dumps(sc_))
                for k in ks:
                    t = trial["segments"][k]
                    t["say"] = t["_origin"]
                    if isinstance(t.get("facts_first"), list):
                        t["facts"] = list(t["facts_first"])
                    if isinstance(t.get("emphasis"), list):
                        t["emphasis"] = [e for e in t["emphasis"] if has_words(t["say"], e)]
                    t["_check"] = "the held line restored: the point rewrite was not kept"
                trial2, bad = check_lines(trial, info, spend, log, ask_judge=ask_judge, rounds=0, stage="final-restore")
                if bad or len(trial2["segments"]) != len(trial["segments"]):
                    log("final support: the held line does not stand either: the supported line stays")
                    return sc_
                info["point_restored"] += [trial2["segments"][k]["_sid"] for k in ks]
                log(f"final support: restored the held line(s) {ks}")
                return trial2
            if ri["core_failed"] or not ri["rewritten"]:
                refused(lambda k: (ri.get("answer") or {}).get(k) or "the rewrite restated no line")
                sc = restore_held(before)
            else:
                n0 = len(info["lines_checked"])
                sc2, failed = check_lines(sc2, info, spend, log, ask_judge=ask_judge, rounds=0, stage="final-point")
                if failed or len(sc2["segments"]) != len(before["segments"]):
                    judged = {r.get("sid"): r for r in info["lines_checked"][n0:]}
                    refused(lambda k: (f"its new words do not stand on their quotes: {(judged.get(segs[k]['_sid']) or {}).get('why')}"
                                       if not (judged.get(segs[k]["_sid"]) or {}).get("ok", True)
                                       else (ri.get("answer") or {}).get(k) or "the rewrite dropped a line"))
                    sc = before                                # its new words do not stand: the supported line stays
                    for s in sc["segments"]:
                        s.pop("_check", None)
                    sc = restore_held(sc)
                else:
                    info["point_rewritten"] += [segs[k]["_sid"] for k in ri["rewritten"]]
                    again = [k for k, s in enumerate(sc2["segments"]) if s["_sid"] in info["point_rewritten"]]
                    pv2, use = judge_point(sc2, again, ask=ask_point)
                    if use:
                        spend.append(use)
                    info["point"] += [dict(line=k, sid=sc2["segments"][k]["_sid"], first=sc2["segments"][k]["_origin"],
                                           now=sc2["segments"][k].get("say"), kept=(pv2.get(k) or {}).get("kept", True),
                                           lost=(pv2.get(k) or {}).get("lost", ""), after_rewrite=True) for k in again]
                    info["point_failed"] += [dict(line=k, sid=sc2["segments"][k]["_sid"], now=sc2["segments"][k].get("say"),
                                                  lost=pv2[k]["lost"], why="kept (it stands on its quotes) but the point judge "
                                                  "still finds the point lost: " + (pv2[k]["lost"] or "no reason given"))
                                             for k in again if k in pv2 and not pv2[k]["kept"]]
                    sc = sc2
    last = {}
    for row in info["lines_checked"]:                  # the latest judgement of each line's exact final words
        last[(row.get("sid"), row.get("say"))] = row
    info["final_lines"] = []
    for k, s in enumerate(sc.get("segments") or []):
        row = last.get((s.get("_sid"), s.get("say"))) or {}
        info["final_lines"].append(dict(i=k, beat=s.get("beat"), say=s.get("say"), facts=s.get("facts") or [],
                                        supported=bool(row.get("ok")), why=row.get("why") or "",
                                        judged=f"{row.get('stage')} round {row.get('round')}" if row else None,
                                        restated=s.get("_sid") in set(info["support_rewritten"]) | set(info["repeat_rewritten"])
                                        | set(info["point_rewritten"])))
    unjudged = [x["i"] for x in info["final_lines"] if not x["supported"]]
    if sc.get("segments"):
        sc["hook_line"], sc["close_line"] = sc["segments"][0].get("say"), sc["segments"][-1].get("say")
    if unjudged:
        log(f"final support: lines without a supported judgement on their final words: {unjudged}")
        return done(sc, False, f"lines without a supported judgement: {unjudged}")
    log(f"final support: {len(sc['segments'])} lines stand on their quotes; restated {len(info['support_rewritten'])}, "
        f"dropped {len(info['support_dropped']) + len(info['repeat_dropped'])}, point rewritten {len(info['point_rewritten'])}")
    return done(sc, True)


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
