"""Research a verified fact bank, plan the causal arc and write narration from that sheet.
Support, redundancy, spine and timing observations inform the same whole-script revision.
"""
import html
import html.parser
import math
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
import story_chain

FORMS = {
    # the judge's story reels run 61-124 s (median 87.5 s, teardown section 12): about a minute and a half of story
    "short": dict(name="vertical short", seconds=(60, 90), words=(175, 250), segments=(12, 20), band="03",
                  shape="length follows the causal chain; the numeric ranges describe the references. One turn and one payoff; a hook a viewer cannot scroll past in the first second; no chapters"),
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
        if isinstance(b, dict) and b.get("cache_control"):
            out[-1]["prompt_cache_breakpoint"] = {"mode": "explicit", "ttl": "30m"}
    return out


def openai_body(system, content, *, model, effort, max_tokens, searches, what, max_tool_calls=None):
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": _oa_parts(content)}],
            "max_output_tokens": int(max_tokens), "reasoning": {"effort": OA_EFFORT.get(effort, "high")}, "stream": True, "store": False}
    if searches:
        body["tools"] = [{"type": "web_search"}]
        body["include"] = ["web_search_call.action.sources"]
        body["max_tool_calls"] = int(max_tool_calls or searches)
    # Web search grows an internal conversation. Explicit-only caching drops
    # that history boundary and rebills it on each search inside this call.
    body = openai_cache.responses(body, "writer:" + what, conversation=bool(searches))
    return body


PROXY_WAITS = (5.0, 15.0, 40.0, 90.0)     # seconds before each further attempt at the local proxy (CODEX_PROXY_LOCAL)
RETRY_AFTER_MAX = 300.0


def retry_after(r, default):
    """The wait before the next attempt: the response's own Retry-After (seconds, at most RETRY_AFTER_MAX) when it sends
    one, else `default`."""
    try:
        v = float((getattr(r, "headers", None) or {}).get("retry-after"))
    except (TypeError, ValueError):
        return default
    return min(max(v, 1.0), RETRY_AFTER_MAX) if v == v else default


def ask_openai(system, content, *, effort="high", max_tokens=32000, searches=0, what="writer", model=None, timeout=2400,
               max_tool_calls=None):
    """(text, usage): one streamed Responses call; with `searches`, the model may use OpenAI's web search. The key comes
    from this process's environment (OPENAI_API_KEY) and is never printed or stored. `max_tool_calls` caps the searches
    of one call (each one feeds its results back as input: uncapped, an ideas call ran 21 searches and read 191k tokens)."""
    import httpx
    import openai_budget
    model = model or OA_MODEL
    body = openai_body(system, content, model=model, effort=effort, max_tokens=max_tokens,
                       searches=searches, what=what, max_tool_calls=max_tool_calls)
    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"}
    t0 = time.time()
    final = None
    # The local subscription proxy refuses with 429/503 when a backend is capped, latched or queued full ("Nothing was
    # run"), and those refusals pass: on 19 Nov a 90-second cap answered all 43 shot-audit calls with 503 and, asked once
    # each, they emptied the film of its pictures. It is retried like any provider, on PROXY_WAITS (or its Retry-After).
    # A request the proxy may already be running is different: after a read timeout, a dropped stream or a stream that
    # ended without a response, each further attempt can start another backend call on the shared daily cap (the cap
    # that ran out on 19 Nov) and, at the default timeout, block for another 40 minutes. Such a request is asked once
    # more at most; the proxy's response cache answers that one if the first call completed meanwhile. A refusal (HTTP
    # 429/5xx) or a connection that never opened ran nothing and is waited out on PROXY_WAITS.
    local = os.environ.get("CODEX_PROXY_LOCAL") == "1"
    attempts = len(PROXY_WAITS) + 1 if local else 4
    accepted_retries = [0]

    def once_more_after_accept():
        """On the local proxy, a request that may have run is asked again at most once."""
        if not local:
            return True
        accepted_retries[0] += 1
        return accepted_retries[0] <= 1
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/responses"

    def wait(attempt, r=None):
        time.sleep(retry_after(r, PROXY_WAITS[attempt]) if local else 10 * (attempt + 1))
    for attempt in range(attempts):
        final = None
        try:
            with openai_budget.call(body, "writer:" + what, lane="workflow", endpoint=endpoint) as lease, \
                    httpx.Client(timeout=httpx.Timeout(timeout, connect=30)) as cl, \
                    cl.stream("POST", endpoint, headers=headers, json=body) as r:
                if r.status_code >= 400:
                    if r.status_code in (400, 401, 403, 404, 422, 429):
                        lease.settle(usd=0)
                    openai_cache.record(model, "writer:" + what, status="http_error", usd=0.0,
                                        http_status=r.status_code, attempt=attempt + 1, force=True)
                    msg = r.read().decode("utf-8", "replace")[:400]
                    if r.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                        wait(attempt, r)
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
                        lease.settle(final.get("usage"), payload=final)
            if final is not None:
                break
            openai_cache.record(model, "writer:" + what, status="stream_without_usage", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
            if not once_more_after_accept():
                break
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if final is not None:  # terminal usage arrived before the connection closed
                break
            openai_cache.record(model, "writer:" + what, status="transport_error", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
            nothing_ran = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
            if attempt + 1 == attempts or not (nothing_ran or once_more_after_accept()):
                raise
            wait(attempt)
    if final is None:
        raise RuntimeError("openai: the stream ended without a response")
    usage = final.get("usage") or {}
    searches_used = sum(item.get("type") == "web_search_call" for item in final.get("output") or [])
    openai_cache.record(model, "writer:" + what, usage,
                        usd=openai_cache.cost(model, usage) + searches_used * OA_SEARCH_USD,
                        request_id=final.get("id"), status=final.get("status"), force=True,
                        cache_request=openai_cache.describe(body))
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
    usd = 0.0 if os.environ.get("CODEX_PROXY_LOCAL") == "1" else openai_cache.cost(model, u) + n_search * OA_SEARCH_USD
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
    system += " Research who wanted what and why, who objected and on what grounds, what decided it and what it led to. Include each as a quoted spine claim under want, objection, decision or outcome."
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
               "\"payoff\": the payoff with its figure, \"spine\": [the essential facts for hook, want, objection, decision, turn, payoff, outcome and close: "
               "{\"beat\": hook|want|objection|decision|turn|payoff|outcome|close, \"claim\": the one fact this beat cannot do without, as one plain checkable "
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
    if n > IDEAS_N:
        content += ("Select a different principal subject or company for every film. When a theme is supplied, "
                    "use different participants or companies within it. Include subject, its usual name, on each idea.")
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
    'most one key word on the turn, a chapter title only where the story truly changes subject (a film of about a minute and a half '
    'needs none, at most one), made of the words that segment itself speaks or the name of the thing it turns on: the beat marks '
    '(hook, turn, low, payoff, close) are the editor\'s private notes on the structure, never words for the screen; "ordinal" only for the numbers of a list the film '
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
    """Standalone callers use the same source bank and beat sheet as the workflow."""
    bank, research = spine_bank(idea)
    if not bank["ok"]:
        raise ValueError(bank["why"])
    sheet, planner = story_chain.plan("story", idea, dict(facts=bank_facts(bank), spine=spine_view(bank)))
    script, use = write_from_bank(idea, card, form, bank, voices, notes, effort, sheet=sheet)
    return script, dict(use, research=research, planner=planner,
                        usd=sum(float(u.get("usd", 0)) for u in [*research, planner, use] if u))


# ----------------------------------------------------------------- the picture brief on the final words

ANNOTATE_MODEL = "gpt-6.1-sol"           # pinned to OpenAI: writer.ask would turn to Anthropic when its key is present
FINISHES = ("bw", "gate", "crt", "none")
ROLES = ("figure", "name", "key", "chapter", "ordinal")
DEVICE_SHARE = (0.12, 0.25)              # the judge: black and white, a gate or scanlines on 12-25% of the voice, never the payoff or close
PHRASE_MAX = 5

ANNOTATE_SYSTEM = """You are the picture editor of a narrated vertical story film cut like the best documentary reels. The script is final and checked: you never change a word of it. You read it and write the data the cut needs.

For each segment:
- "see": one to four items, in the order the line says them: {"thing": exactly one name from the named list, "as_of": the story time of that thing as THIS line means it, a year plus its version or state (e.g. "the finished 1995 release, not the 1993 test reel"; "Pixar's Point Richmond office in 1993, not the later Emeryville campus"), "must_show": one sentence, only what must be VISIBLE in a picture for it to carry this line (who or what, doing what, in which version); never the film's treatment of it (colour, black and white, muted, grain, composition, framing): treatment belongs to "finish" alone, "moment": the scene or event the line describes when it describes one (e.g. "Woody knocking Buzz out of the window in the finished 1995 film"), else null, "search": one photo search an archivist would type to find a photograph of exactly this thing at this time (e.g. "John Lasseter 1995"; "Pixar Point Richmond 1993"), "still_only": true when the true picture of this line is still by its nature (a storyboard, a document, a drawing, a letter, a printed page), else false, "word_i": the index of the first spoken word that needs this particular picture, "keep_full_frame": true for a detail, comparison or group whose evidence a tight crop could hide}. A line about a scene of a film names that scene as its moment, in the version the line means. The hook's first see item is the moment its collision names (the person in the scene, the crowd, the screen), the strongest picture of the film, never the figure the line says (a figure belongs to type).
- "exhibit": the ID of a fact the segment cites and a short exact excerpt from its quote that shows the action or proof, as {"fact": id, "excerpt": verbatim text}. Use a clause that lets a viewer see the relationship, even if an archival image is unavailable. Within that excerpt, mark two or three successive exact phrases as "passages": [{"text": exact words from excerpt, "word_i": spoken anchor}]. Land a result or number only as the narrator reaches it; together the successive printed phrases should explain the evidence.
- "list": true when the line itself reads as a list of several distinct things or a quick montage of them (the cut may burst through them in a few frames each), else false; "list_items": the things of the list, in order, as see things, when list is true.
- "finish": bw|gate|crt|none and "finish_rank" (1 = the segment that most needs it). Black and white, a film gate or scanlines are a device for a memory, an early version, the past cut in, a low point; never a look for the whole film. The devices together cover 12 to 25 percent of the spoken words, never the payoff or the close, never a segment at or after the colour turn.
- "show": the segment's display items, each {"text": as printed, "role": figure|name|key|chapter|ordinal (ordinal only for the numbers of a list the film itself counts out, "1.", "2.", from 1; a rank such as #1 is a figure), "word_i": the index of the spoken word it lands on (a figure: the first word of the spoken figure; a name: the first word of the name; a chapter: the first word of the segment), "big": the part of the text set large (e.g. "$362M"), "small": the rest set small under it (e.g. "box office, 1995"), or null}. Every item needs its word_i. Keep every given item's text exactly. You may add: the payoff's figure if it lacks one (a figure printed with digits only when the same figure stands in a fact's quote), a name item for each person (is=person) at the line that first says them, one key word where the story turns, and a chapter title only where the story truly changes subject (a film of about a minute and a half needs none, at most one). A chapter title is words a viewer reads as the story itself: exactly the spoken word or words of that segment, or the name of the thing it turns on, the way a key word is. Each segment's role in the story is given in plain words so you know the structure; those roles are the editor's private notes and never become words on the screen. Display type lands on about one picture in six across the film: a figure card for each verified figure, a name for each person, a key word or a spoken phrase where the story needs weight. At most three items a segment.
- "phrases": the caption lines: lists of word indices, each 1 to 5 consecutive indices, together covering every word of the segment once and in order. Never split a name, a figure, a date or a noun phrase across two lines; a line reads as one unit of sense.

And once for the film: "colour_turn": the index of the one segment from which the film stays in colour (the turn or the release); "true_pictures": for every named thing, where a faithful picture of it can exist: "stills" (a free photo archive holds true photographs of it: a real person, a building, an object), "footage" (it exists faithfully only in the work's own moving pictures: an animated film's characters and scenes, whose photographs are toys, rides, statues and costumes), or "both".

Answer with one JSON object only: {"colour_turn": int, "true_pictures": {"<named thing>": "stills"|"footage"|"both", ...}, "segments": [{"i": int, "see": [...], "exhibit": {"fact": id, "excerpt": exact clause}, "list": bool, "list_items": [...], "finish": ..., "finish_rank": int or null, "show": [...], "phrases": [[int, ...], ...]}]}"""

SEE_SYSTEM = """You are the picture editor of a narrated documentary film. The script is final; you never change a word. For the ONE segment you are given, write what a picture must show to carry it.

Answer with one JSON object only: {"see": [one to four items, in the order the line says them: {"thing": exactly one name from the named list, "as_of": the story time of that thing as THIS line means it, a year plus its version or state, "must_show": one sentence, only what must be VISIBLE (never colour, black and white or composition), "moment": the scene or event the line describes, or null, "search": one photo search an archivist would type to find exactly this thing at this time, "still_only": true when the true picture of this line is still by its nature (a storyboard, a document, a drawing), else false}]}"""
TRUE_PICTURES = ("stills", "footage", "both")


# A segment's role in the story, said to the picture editor in plain words: a bare label beside the words ("beat=turn")
# reads as a ready-made title, and the round-1 film printed "THE TURN" on a card (19 Nov).
BEAT_PROSE = {
    "hook": "the opening line that must stop a viewer",
    "setup": "it sets the story up",
    "turn": "where the story turns",
    "low": "where the story is at its worst",
    "payoff": "where the story pays off",
    "close": "the last line, which answers the opening",
}


def beat_prose(beat):
    return BEAT_PROSE.get(str(beat or ""), "it carries the story on")


def _ann_content(sc, idea_full, problems=None):
    rows = []
    for k, s in enumerate(sc["segments"]):
        ws = str(s.get("say", "")).split()
        given = [dict(text=str(x.get("text") if isinstance(x, dict) else x), role=x.get("role") if isinstance(x, dict) else None)
                 for x in s.get("show") or []]
        rows.append(f"{k}. role in the story: {beat_prose(s.get('beat'))}; era={s.get('era')} facts={s.get('facts') or []} show={json.dumps(given, ensure_ascii=False)}\n   words: "
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
                        still_only=x.get("still_only") is True,
                        **({"keep_full_frame": True} if x.get("keep_full_frame") is True else {}),
                        **({"word_i": x["word_i"]} if type(x.get("word_i")) is int else {})))
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
        excerpt = g.get("exhibit")
        if isinstance(excerpt, dict) and excerpt.get("fact") in (s.get("facts") or []):
            fact = next((f for f in out.get("facts") or [] if f.get("id") == excerpt["fact"]), {})
            q = excerpt.get("excerpt")
            if isinstance(q, str) and q.strip() and q.strip() in (fact.get("quote") or ""):
                s["exhibit"] = dict(fact=excerpt["fact"], excerpt=q.strip())
                passages, last, offset = [], -1, 0
                for part in excerpt.get("passages") or []:
                    if not isinstance(part, dict):
                        continue
                    text, wi = part.get("text"), part.get("word_i")
                    if not isinstance(text, str) or not text.strip() or type(wi) is not int or not last < wi < len(s["say"].split()):
                        continue
                    loc = q.find(text.strip(), offset)
                    if loc >= 0:
                        passages.append(dict(text=text.strip(), word_i=wi))
                        last, offset = wi, loc + len(text.strip())
                if len(passages) >= 2:
                    s["exhibit"]["passages"] = passages
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
            # which prompt wrote the item stays on the record (write or annotate; wf3's density pass marks its own)
            x.setdefault("from", "annotate" if x.pop("added", None) else "write")
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


def verify(script, previous=None):
    """Each quote fetched from its own page; every number on screen inside a verified quote; every span the narration
    presents as someone's words verbatim in a verified quote the line cites."""
    rows, ok_quotes, by_id = [], [], {}
    for f in script.get("facts") or []:
        r = check_fact(f)
        prior = (previous or {}).get((f.get("url"), f.get("quote")))
        if prior and r.get("error") and not r["found"]:
            r = dict(r, found=True, read="delivered verification", source=prior, reread_error=r["error"])
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

SUPPORT_MODEL = "gpt-6.1-sol"           # pinned to OpenAI: the judgement of meaning is a model's, never a rule's

SUPPORT_SYSTEM = """Measure each line in the context of the complete causal chain and verified bank. A link is supported when the bank holds the cause, including in another line's source. Several quotes can establish the want, objection, decision and consequence together. A conjunction needs no separate quote. Distinguish a documented cause from mere chronology. Report missing evidence and its fact ids when the citation belongs elsewhere.

Also list every span of the line that presents someone's own words: in quotation marks, or given as what a person said, recalled, wrote, asked or thought in their own words. Copy each span exactly as the LINE has it (never as the quote has it), with whose words the line says they are, by name. Paraphrase in the narrator's own voice ("he agreed to do it") is not such a span.

And name the cited facts the line actually uses: those whose words it says or paraphrases. A fact cited for a date, a figure or a detail the line never says is not used.

Answer with one JSON object only: {"lines": [{"i": the line index, "supported": true or false, "unsupported": the claim no quote states (empty when supported), "speech": [{"words": the span exactly as in the line, "who": whose words}], "uses": [the ids of the cited facts the line uses]}]}"""

REPEAT_SYSTEM = """Read the whole story and flag only lines whose removal loses no setup, development or consequence.
Stating the objection that explains the hook's conflict develops it. Paying off an earlier plant and reusing the
hook word in the resolved meaning line belong in the film. Return JSON {"repeats":[{"i":index,"of":index,"why":reason}]}."""


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
               f"\n\n# Verified bank (including causes carried by adjacent beats)\n{json.dumps(quotes_by_id, ensure_ascii=False)}"
               f"\n# Lines and local citations\n"
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


def judge_repeats(script, changed=(), ask=None):
    """([{"i", "of", "why"}], usage): one model call over the whole script flags a line that says again what another
    line says (its claim or its phrasing), naming the line that should change."""
    ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=8000, what="line repeats",
                                                     model=SUPPORT_MODEL))
    segs = script.get("segments") or []
    core = core_segments(script)
    content = ("# The script\n" + "\n".join(f"{k}. beat={s.get('beat')}{' CORE' if k in core else ''}\n   {s.get('say')}"
                                            for k, s in enumerate(segs)))
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


SPINE_BEATS = ("hook", "turn", "payoff", "close")
CAUSAL_BEATS = ("want", "objection", "decision", "outcome")
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

SPINE_CLAIMS_SYSTEM = """Propose checkable claims to research for this story. Give the starting paradox,
who wanted what and why, who objected on what grounds, what decided the outcome, what changed as a result,
the turn and one payoff figure that answers the hook. State causal claims as simple positive propositions to look for. Use only the motive, objection, decision or direct consequence the story needs.
Return JSON {"spine":[{"beat":"hook|want|objection|decision|turn|payoff|outcome|close","claim":"..."}]}.
A close returns to the hook's word and explains what it came to mean."""

RESEARCH_SPINE_SYSTEM = """You are the researcher of a narrated documentary film. The film is written only after every claim its story stands on is found stated on a page a plain script can read. For each claim below, search the web and copy sentences that state it, word for word exactly as they stand on the page.

Search with new queries for each claim, never one already tried (listed with the claim), and at least two different ways for each claim before you give up on it. This round: {tier}.

Cite only pages a plain script can read: no paywall, no login, no bot wall, no PDF, no document mirror (studylib, scribd), and never a page listed under a claim as failed with a refusal (a 403, a 429, unreachable): it refused this script and will refuse it again, so find another page that states the same claim. Copy a whole sentence (or a clause of at least eight words) that states the claim with its subject and its verb: never a bare date, a number, a heading or a fragment. A sentence that only implies the claim, or a paraphrase, is worth nothing: the sentence is checked by fetching the page and finding it there. When no readable page states a claim, give no sources for it; never stretch a source.

For each sentence say whose statement it is (the court, the company, the newspaper, the author), the person whose own words it quotes when it quotes someone (else null), and when (the date of the event it states, else the page's date, else null).

Answer with one JSON object only: {"claims": [{"id": the claim id, "queries": [every search you ran for it], "sources": [up to four {"url": the page, "quote": the sentence copied word for word, "source": whose statement, "speaker": a person or null, "when": a date or null}]}]}"""

EXTRACT_SYSTEM = """You copy sentences from web pages a script has already fetched, for the fact bank of a narrated documentary film. Every sentence you give is checked against the page text below word for word, so copy it exactly: the same words in the same order (you may leave out a footnote mark).

For each claim, copy the best one or two sentences from these pages that establish its cause or outcome. Then copy the number of sentences asked for that tell the rest of this story: who wanted what and why, who objected and on what grounds, what decided it and what it led to. Copy the causal action itself from the first-hand account. Each one a whole sentence, or a clause of at least eight words that makes sense alone: never a heading, a caption, a short list item, a bare date or a number.

For each sentence say whose statement it is (the court, the company, the newspaper, the author), the person whose own words it quotes when it quotes someone (else null), and when (the date of the event it states, as the page gives it, else null).

Answer with one JSON object only: {"quotes": [{"page": the page number, "quote": the sentence, "claims": [ids of the claims it states, or []], "source": whose statement, "speaker": a person or null, "when": a date or null}]}"""

CLAIMS_JUDGE_SYSTEM = """You are the fact checker of a narrated documentary film. Before it is written, each claim of its spine must be stated by quotes found word for word on their pages. For each claim decide whether the quotes below state its point in full: who did what to whom, the reversal, the figure and who paid or got it. Quotes may be combined when together they plainly state it (one names the payer, another the amount of the same payment). A quote that only suggests the claim, or states a different or weaker version (an amount accepted without who paid it, a ruling without whom it went against), does not state it. A detail the point does not need (an exact day, a place) may be missing: the claim is then stated, and "as_stated" gives the claim exactly as the quotes state it, without that detail.

Answer with one JSON object only: {"claims": [{"id": the claim id, "stated": true or false, "by": [ids of the quotes that state it], "as_stated": the claim as the quotes state it (when stated), "missing": what no quote states (when not stated)}]}"""


def idea_spine(idea):
    """[{beat, claim, evidence: [{url, quote}]}]: the idea's own spine, as the ideas stage gave it (shape only)."""
    out = []
    for x in idea.get("spine") or [] if isinstance(idea.get("spine"), list) else []:
        if not isinstance(x, dict) or x.get("beat") not in SPINE_BEATS + CAUSAL_BEATS or not str(x.get("claim") or "").strip():
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
    if not {r["beat"] for r in rows} >= set(SPINE_BEATS + CAUSAL_BEATS):
        ask = ask or (lambda system, content: ask_openai(system, content, effort="medium", max_tokens=6000, what="spine claims",
                                                         model=SPINE_MODEL))
        view = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff", "spine") if idea.get(k)}
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
        if not c["for"] and sum(not f.get("for") for f in bank["facts"]) >= BANK_MAX:
            bank.setdefault("over_cap", 0)
            bank["over_cap"] += 1           # a story sentence past the cap; a quote found for a spine claim always joins
            continue
        next_id = len(bank["facts"]) + 1
        occupied = {f["id"] for f in bank["facts"]}
        while f"b{next_id}" in occupied:
            next_id += 1
        f = dict(id=f"b{next_id}", quote=c["quote"], url=c["url"], source=c.get("source") or _host(c["url"]),
                 speaker=c.get("speaker"), when=c.get("when"), read=row.get("read"), how=how, **{"for": c["for"]})
        bank["facts"].append(f)
        added.append(f["id"])
    if cands:
        log(f"spine bank ({how}): {len(added)} of {len(cands)} quotes verified on their pages; the bank holds {len(bank['facts'])}")
    return added


def extract_quotes(urls, claims, story=STORY_SENTENCES, ask=None, known=()):
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
    if known:
        content += ("\n\n# Facts already on file\n" + json.dumps(known, ensure_ascii=False) +
                    "\nCopy new evidence for the want, objection, deciding action or consequence. "
                    "Prefer a firsthand explanation when accounts differ.")
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
    batches = [todo[i:i + EXTRACT_PAGES] for i in range(0, len(todo), EXTRACT_PAGES)]
    found = []
    for pages in batches:
        try:
            cands, use = extract_quotes(pages, claims, story=max(1, math.ceil(story / len(batches))), ask=ask,
                                        known=[f["quote"] for f in bank["facts"]])
        except Exception as exc:
            log(f"spine bank: extraction failed: {exc!r}"[:200])
            continue
        bank["extracted"].extend(pages)
        if story >= STORY_SENTENCES:
            bank["story_copied"].extend(pages)
        if use:
            spend.append(use)
        found.append(iter(cands))
    # Share the remaining story slots across batches, including later archive pages.
    from itertools import zip_longest
    return _take(bank, [c for group in zip_longest(*found) for c in group if c], how, log)


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


def spine_view(bank, sheet=None):
    """The researched spine, narrowed to the planned resolution after the planner picks it."""
    claims = [dict(beat=c["beat"], claim=c.get("as_stated") or c["claim"], by=list(c.get("by") or []))
              for c in bank.get("claims", []) if c.get("stated")]
    if sheet:
        ending = sheet["sheet"][sheet["beats"]["payoff"]]
        claims = [c for c in claims if c["beat"] != "payoff"]
        claims.append(dict(beat="payoff", claim=ending["action"], by=ending["facts"]))
    return claims


def bank_facts(bank):
    """The bank as the script's facts: every verified quote with its page, whose statement it is and when."""
    return [dict(id=f["id"], claim=f.get("claim") or f["quote"], quote=f["quote"], url=f["url"], source=f.get("source"),
                 speaker=f.get("speaker"), when=f.get("when"), also=f.get("also", [])) for f in bank.get("facts") or []]


def spine_bank(idea, log=print, ask_claims=None, ask_research=None, ask_extract=None, ask_judge=None, rounds=SPINE_ROUNDS, verified=()):
    """Retain the delivered bank on a remake. Research missing links through independent sources.
    A partially sourced secondary claim stays in the research log; the plan uses stated facts."""
    spend = []
    claims, use = spine_claims(idea, ask=ask_claims)
    if use:
        spend.append(use)
    bank = dict(idea=idea.get("title"), claims=claims, facts=[], rounds=[], failed=[], extracted=[], story_copied=[], ok=False, why="")
    # A delivered bank already has verified quotes. Keep their ids for the remake's audit.
    bank["facts"] = [dict(f, how="delivered verification") for f in verified]
    asks = dict(research=ask_research, extract=ask_extract, judge=ask_judge)
    story = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff") if idea.get(k)}
    cands = [_cand(e, [c["id"]]) for c in claims for e in c.get("evidence") or []]
    _take(bank, cands, "idea", log)
    pages = [c["url"] for c in cands if c] + [u for u in idea.get("sources") or [] if isinstance(u, str) and u.startswith("http")]
    pages += [f["url"] for f in verified]
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
    # A missing subsidiary detail changes the telling. Only abandon an unsourced central story.
    bank["ok"] = any(c["stated"] for c in claims if c["beat"] in ("hook", "turn", "payoff", "outcome"))
    bank["why"] = "" if bank["ok"] else f"No source for the central claim after {rounds} searches"
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

BANK_WRITE_RULES = story_chain.WRITE + """
Use the verified quotes together to tell the causal story. Cite the facts carrying each action
and its cause. Attribute direct speech verbatim to its speaker. Use the one payoff figure the
sheet selected. Mark the hook's landing word in lands. Revise with the whole causal sequence
in view: preserve setups, named roles, the late callback and the hook word in its new context.
Use measured hook and close seconds to move words between adjacent beats, including the last two.
"""


def write_from_bank(idea, card, form, bank, voices=None, notes=None, effort="high", ask=None, sheet=None):
    """(script, usage): the script written from the verified bank alone (one call, no web: the writer cannot reach a
    fact the bank does not hold). Every segment cites bank facts only; the script's facts are the bank's, and the
    spine is kept on the script (`spine`) for the point checks after it."""
    F = FORMS[form]
    if sheet is None:
        sheet, _ = story_chain.plan(form, idea, bank_facts(bank))
    voices = voices or card.get("voice") or list(VOICES)
    ask = ask or (lambda system, content: ask_openai(system, content, effort=effort, max_tokens=48000, what="write from bank",
                                                     model=BANK_WRITE_MODEL))
    system = ("You are the writer and voice director of Valmera's story workflow. You write narration spoken by a synthetic narrator "
              "over real footage and photographs of what it names, for a " + F["name"] + ". You write ONLY from the bank of verified "
              "facts you are given: each is a sentence found word for word on its page, with whose statement it is and when. "
              "Use the account that supplies the causal action. You also direct the actor: pace, energy, melody, emphasis "
              "and pauses per segment. Write for the ear: a thought runs on with commas and 'and', 'but', 'so' until it lands, a full stop only where it lands (the hook and the close excepted: each lands at once), lengths mixed (never three short sentences in a row, never a verbless fragment as a sentence), concrete nouns, numbers a listener can hold.")
    system += f"\n\n# The bar\n{references()}\n\n" + BANK_WRITE_RULES
    system += ("Return JSON only: {\"title\": a plain title, \"voice\": one voice id, \"pace\": slow|steady|brisk|fast, \"energy\": "
               "low|mid|high, \"hook_line\": the first segment's words, \"close_line\": the last segment's words, \"names\": [every "
               "proper noun the narration says, exactly as spelled], \"segments\": [{\"say\": the words spoken, numbers and years "
               "written as words the way they are said (\"nineteen seventy-three\", \"five thousand dollars\"), no digits, no "
               "abbreviations, no symbols, \"show\": at most two short display strings for this segment as they would be printed, "
               "digits allowed (a year, a figure, a name), or [], \"beat\": hook|setup|turn|low|payoff|close, \"pace\": "
               "slow|steady|brisk|fast, \"pictures\": names from the named list this segment talks about, in order, \"melody\": "
               "falling|rising|level|wide, \"emphasis\": [one or two words of say], \"lands\": (the hook only) the word or two of say "
               "where its collision lands, \"pause_after\": seconds of silence after it "
               "(0.18 to 0.26 inside the story; 0.35 to 0.5 only where the story turns, at most three times), \"direction\": a few "
               "words for the actor, \"facts\": ids of the bank facts it states, \"quotes\": every span of say that presents "
               "someone's own words as {\"words\": the span exactly as in say, \"who\": whose words, \"fact\": the id of the bank "
               "fact whose quote holds those words word for word}, or []}], \"named\": [{\"name\", \"is\", \"searches\", "
               "\"must_show\"} for every person, place or thing the film shows], " + STYLE_DATA.get(card["id"], "") + "}\n"
               "Every digit in `show` or in the style's data must appear in the quote of a bank fact (only the numbers of a list the "
               "film itself counts out, role ordinal, are exempt).")
    view = {k: idea.get(k) for k in ("title", "hook", "story", "turn", "payoff", "named") if idea.get(k)}
    content = (f"# The idea\n{json.dumps(view, ensure_ascii=False)}\n\n# The style\n{json.dumps(card, ensure_ascii=False)}\n\n"
               f"# Beat sheet (one segment per entry)\n{json.dumps(sheet, ensure_ascii=False)}\n\n"
               f"# Voices\n{json.dumps({k: VOICES[k] for k in voices if k in VOICES})}\n\n"
               f"# The spine (each beat's claim, as the verified quotes state it, and the bank facts that state it; the close "
               f"reframes the hook on the hook's facts, and its own claim, when given, may support it)\n"
               f"{json.dumps(spine_view(bank, sheet), ensure_ascii=False, indent=1)}\n\n"
               f"# The bank: the only facts this film may state\n"
               f"{json.dumps([dict(id=f['id'], quote=f['quote'], url=f['url'], source=f.get('source'), speaker=f.get('speaker'), when=f.get('when')) for f in bank['facts']], ensure_ascii=False, indent=0)}\n\n" +
               (f"# Notes from the last attempt\n{notes}\n\n" if notes else ""))
    def request(repair):
        text, usage = ask(system, content + "\n" + repair)
        return json_block(text), usage

    sc, use = story_chain.write_segments(sheet, request)
    ids = {f["id"] for f in bank["facts"]}
    for s in sc.get("segments") or []:
        s["facts"] = [f for f in s.get("facts") or [] if f in ids] if isinstance(s.get("facts"), list) else []
        s["quotes"] = [q for q in s.get("quotes") or [] if isinstance(q, dict) and q.get("fact") in ids] if isinstance(s.get("quotes"), list) else []
    sc["facts"] = bank_facts(bank)
    sc["spine"] = spine_view(bank, sheet)
    sc["idea_point"] = {k: idea.get(k) for k in ("hook", "turn", "payoff") if idea.get(k)}
    if sc.get("segments"):
        sc["hook_line"], sc["close_line"] = sc["segments"][0].get("say"), sc["segments"][-1].get("say")
    return sc, use


# ----------------------------------------------------------------- the spine held on the written lines

SPINE_JUDGE_SYSTEM = """Measure the arc of this documentary. Does the hook pose the starting and ending paradox?
Does the turn follow its cause and does the payoff resolve the conflict? The meaning line should reuse the hook's
own word with its new significance. Count that callback as carried. Flag a missing setup or an unrelated ending.
Return JSON {"beats":[{"beat":"hook|turn|payoff|close","carried":true,"lost":""}]}."""


def spine_lines(sc):
    """{beat: segment index} of the lines that carry the spine: the hook is the first line, the turn and the payoff the
    first lines so marked, the close the last line marked close, else the last line. Structure only."""
    segs = sc.get("segments") or []
    if sc.get("beats"):
        return {b: sc["beats"]["meaning" if b == "close" else b] for b in SPINE_BEATS}
    out = {"hook": 0} if segs else {}
    for b in ("turn", "payoff"):
        k = next((k for k, s in enumerate(segs) if k and s.get("beat") == b), None)
        if k is not None:
            out[b] = k
    if len(segs) > 1:
        close = [k for k, s in enumerate(segs) if k and s.get("beat") == "close"]
        out["close"] = close[-1] if close else len(segs) - 1
    return out


REFRAME = "it reframes the hook: the resolved collision reuses the hook word ({})"


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


def measurements(script, verified, ask_support=None, ask_repeats=None, ask_point=None):
    """Each reader sees the same draft. No reader has a path to change its lines."""
    support, u1 = judge_support(script, range(len(script["segments"])), ask_support, verified)
    repeats, u2 = judge_repeats(script, ask=ask_repeats)
    spine, u3 = judge_spine(script, ask_point)
    return dict(support=support, repeats=repeats, spine=spine), [u for u in (u1, u2, u3) if u]


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
