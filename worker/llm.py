"""LLM access — OpenAI-compatible SDK only, configured entirely by env.
Default provider is DeepSeek V4 Pro (api.deepseek.com); swapping to any
OpenAI-compatible provider (xAI Grok, DashScope/Qwen, OpenAI, ...) is an env
change, never code.

Image generation runs on a SEPARATE provider (config.IMAGE_BASE_URL /
IMAGE_API_KEY) because it is not part of the chat-completions surface and not
every chat provider has it — DeepSeek ships no image model at all. Two
image backends are auto-detected (see image_provider): the OpenAI-compatible
/images/generations surface (xAI — text-to-image only) and DashScope's native
multimodal-generation endpoint (text-to-image AND frame/image restyling).
Editing is unavailable on the OpenAI/xAI backend."""

import base64
import json
import mimetypes
import re
import threading
from types import SimpleNamespace

import requests
from openai import OpenAI

import config


_client = None
_paid_client = None
_frontier_client = None
_image_client = None
_vision_client = None

# Set when the vision provider proves it cannot accept images at all (a 400 on
# the image part itself, not a transient error). Vision then reports itself
# unavailable for the rest of the process instead of burning a doomed call per
# look — DeepSeek rejected 59 in a row on Jul 26 2026 before anyone noticed.
_vision_blind = False

# Per-thread model-I/O recorder (set by the agent loop for the duration of a
# turn). Signature: fn(purpose, request_payload, response_payload, usage).
# Thread-local because agent lanes run turns concurrently.
_tls = threading.local()


def set_recorder(fn):
    _tls.recorder = fn


def get_recorder():
    return getattr(_tls, "recorder", None)


def last_error():
    """The most recent provider error on THIS thread, or None.

    Exists because ask_text returns None on failure, which tells a caller that
    something broke but not what. The indexer used to record the literal string
    "call failed" for its greeting, so when the provider ran out of credit on
    Jul 26 2026 the admin Model I/O tab showed ten rows of "call failed" and not
    one word about a 403 — the outage was invisible in the only place built to
    show it. Thread-local because agent lanes run turns concurrently."""
    return getattr(_tls, "last_error", None)


def _note_error(e):
    _tls.last_error = f"{type(e).__name__}: {str(e)[:400]}"


def record(purpose, request, response, usage=None):
    fn = get_recorder()
    if not fn:
        return
    try:
        fn(purpose, request, response, usage)
    except Exception as e:
        print(f"[llm] recorder failed: {e}", flush=True)


def client():
    """One pooled client per process (the OpenAI SDK's httpx client is
    thread-safe) — connection reuse across turns instead of a new TLS
    handshake per call."""
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.OPENAI_BASE_URL,
                         api_key=config.OPENAI_API_KEY,
                         timeout=config.LLM_TIMEOUT_S,
                         max_retries=config.LLM_MAX_RETRIES)
    return _client


def without_sdk_retries(client):
    """Return an OpenAI client view that surfaces failures immediately.

    Agent turns own their retry policy: they classify 429s, respect the
    remaining turn budget, heartbeat while waiting, and cap the number of
    retries.  Letting the SDK also retry first hid each 429 for as much as
    ``LLM_TIMEOUT_S * LLM_MAX_RETRIES`` before that policy could run.  Keep
    the pooled transport and timeout, but disable only the SDK's nested retry
    layer for those calls.  The fallback keeps light test/provider doubles
    that do not expose ``with_options`` compatible.
    """
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        return with_options(max_retries=0)
    return client


def image_client():
    """Separate pooled client for /images/generations — the image provider is
    configured independently of the chat provider (config.IMAGE_BASE_URL), so
    this must NOT reuse client()."""
    global _image_client
    if _image_client is None:
        _image_client = OpenAI(base_url=config.IMAGE_BASE_URL,
                               api_key=config.IMAGE_API_KEY,
                               timeout=config.IMAGE_TIMEOUT_S,
                               max_retries=config.LLM_MAX_RETRIES)
    return _image_client


def paid_client():
    """Separate pooled client for the model paying/trialling users get
    (config.PAID_*). Its own base URL and key, so it must NOT reuse client() —
    the whole point is that it is a different provider from the free one."""
    global _paid_client
    if _paid_client is None:
        _paid_client = OpenAI(base_url=config.PAID_BASE_URL,
                              api_key=config.PAID_API_KEY,
                              timeout=config.LLM_TIMEOUT_S,
                              max_retries=config.LLM_MAX_RETRIES)
    return _paid_client


_first_turn_client = None


def first_turn_available():
    """Is the round-81 first-turn A/B lane configured? OFF by default."""
    return bool(config.FIRST_TURN_AGENT_MODEL and config.FIRST_TURN_API_KEY)


def first_turn_client():
    """Pooled client for the first-turn lane (config.FIRST_TURN_*)."""
    global _first_turn_client
    if _first_turn_client is None:
        _first_turn_client = OpenAI(base_url=config.FIRST_TURN_BASE_URL,
                                    api_key=config.FIRST_TURN_API_KEY,
                                    timeout=config.LLM_TIMEOUT_S,
                                    max_retries=config.LLM_MAX_RETRIES)
    return _first_turn_client


def paid_available():
    """True once the Pro-tier provider is fully configured. All three parts are
    required: a half-set trio would send an empty key at a real endpoint and
    401 every turn for exactly the customers who are paying."""
    return bool(config.PAID_BASE_URL and config.PAID_API_KEY
                and config.PAID_AGENT_MODEL)


def is_paid_tier(plan):
    return (plan or "") in config.PAID_PLANS


def frontier_client():
    """Separate pooled client for the Frontier plan's provider
    (config.FRONTIER_*). Serves both its agent turns and its vision calls —
    same endpoint, same key, two different models."""
    global _frontier_client
    if _frontier_client is None:
        _frontier_client = OpenAI(base_url=config.FRONTIER_BASE_URL,
                                  api_key=config.FRONTIER_API_KEY,
                                  timeout=config.LLM_TIMEOUT_S,
                                  max_retries=config.LLM_MAX_RETRIES)
    return _frontier_client


def frontier_available():
    """True once the Frontier provider is fully configured. Unlike PAID_*, the
    base URL and models have real defaults, so in practice this is asking
    whether a key is reachable — which it is whenever the worker already has an
    xAI key for vision or image generation."""
    return bool(config.FRONTIER_BASE_URL and config.FRONTIER_API_KEY
                and config.FRONTIER_AGENT_MODEL)


def is_frontier(plan):
    return (plan or "") in config.FRONTIER_PLANS


def agent_client_for(subscribed, plan=None, first_turn=False):
    """(client, model) for one agent turn.

    One tier per plan, most specific first — this IS the "more intelligence" /
    "frontier intelligence" the pricing page sells, so the mapping is the
    product, not an optimisation:

      Frontier 'ai_max'   FRONTIER_* — strongest, and vision too.
      Pro      'ai_pro'   PAID_*     — the stronger agent model.
      Creator / free      AGENT_MODEL.

    A trial runs its OWN plan's model, because a trial that previews a better
    model than the plan delivers is the bait-and-switch it was meant to avoid.

    first_turn (round 81) is the A/B lever, not a tier: a FREE account's
    first-ever agent turn runs FIRST_TURN_AGENT_MODEL when that lane is
    configured. Free accounts only — a subscriber's model is a promise their
    plan already resolved above, and boosting a trial is the same
    bait-and-switch the trial rule exists to avoid.

    Every branch falls through to the next when its provider is not configured,
    so a missing key degrades the model rather than 401ing the turn. `plan`
    defaults to None so any two-tier caller keeps working unchanged.
    """
    if subscribed and is_frontier(plan) and frontier_available():
        return frontier_client(), config.FRONTIER_AGENT_MODEL
    if subscribed and is_paid_tier(plan) and paid_available():
        return paid_client(), config.PAID_AGENT_MODEL
    if first_turn and not subscribed and first_turn_available():
        return first_turn_client(), config.FIRST_TURN_AGENT_MODEL
    return client(), config.AGENT_MODEL


def vision_client():
    """Separate pooled client for vision — VISION_BASE_URL is configured
    independently of the chat provider (a text-only chat provider is normal),
    so this must NOT reuse client()."""
    global _vision_client
    if _vision_client is None:
        _vision_client = OpenAI(base_url=config.VISION_BASE_URL,
                                api_key=config.VISION_API_KEY,
                                timeout=config.VISION_TIMEOUT_S,
                                max_retries=config.LLM_MAX_RETRIES)
    return _vision_client


def shared_vision_configured():
    """Is the DEDICATED VISION_* provider usable right now?"""
    return bool(config.VISION_MODEL and config.VISION_API_KEY
                and not _vision_blind)


def vision_fallback():
    """(client, model) for vision when no dedicated VISION_* provider is
    configured — the AGENT's own provider, if the agent model can see.

    THE INCIDENT THIS EXISTS FOR (round 80). VISION_API_KEY inherits
    OPENAI_API_KEY only when `VISION_BASE_URL == OPENAI_BASE_URL` — a STRING
    EQUALITY between two independently-set env vars. That is a fine default and
    a terrible invariant: it holds until someone moves the chat provider and
    leaves VISION_BASE_URL pointing at the old one, at which point the key
    inherits from nothing, `vision_available()` goes False, and the whole
    product quietly loses its eyes. It has now happened twice —

      * Jul 26 2026, the DeepSeek switch: 419 agent turns across two days with
        ZERO look_at (already recorded in indexer.py's warning branch).
      * Jul 31 2026, the Luna switch: index-time captioning stopped dead at
        13:24 and did not run again. EVERY index for the next two days shipped
        with `moments: []` and not one shot caption — so `get_shots` answered
        "(no visual caption)" for every shot of every upload, and the agent
        edited blind. One user asked for the women in his footage to be
        covered; the agent had no visual timeline to find them in, burned six
        look_at calls sampling six frames across fifteen minutes, and ended up
        offering to black out the whole frame. Another uploaded a 31-minute
        match with two transcribed words in it — where the picture is the ONLY
        signal there is.

    The fix is not a better string comparison. It is that the agent model is
    ALREADY a funded, working, multimodal provider on this box (Luna takes
    images — it is the entire basis of round 67's direct-sight look_at), so
    there is no such thing as "no vision configured" while the agent itself can
    see. Vision now degrades to the agent's own eyes instead of to nothing, and
    the only way to lose sight completely is to lose the agent too.

    Honesty is preserved in both directions: a genuinely text-only agent model
    (`AGENT_MULTIMODAL=0`, or one that has already latched blind on an image
    part) returns None here, and `vision_available()` goes False exactly as
    before — the honest-off contract, not a fallback that 400s per look.
    """
    if not vision_fallback_available():
        return None
    return client(), config.AGENT_MODEL


def vision_fallback_available():
    """Same question as vision_fallback(), without constructing a client —
    so a health probe can ask it for free."""
    return bool(config.OPENAI_API_KEY and config.AGENT_MODEL
                and agent_sees(config.AGENT_MODEL))


def _host(url):
    """The hostname out of a base URL. Never the key."""
    try:
        return (url or "").split("//", 1)[-1].split("/", 1)[0] or None
    except Exception:                                    # pragma: no cover
        return None


def config_report():
    """Which PROVIDER this process is actually wired to, as plain JSON.

    version.py made CODE skew between the dispatcher and the executor cost one
    curl instead of a day of forensics. This is the same answer for CONFIG
    skew, which has now cost more than the code kind ever did: the executor is
    deployed by hand with its own env, so a provider change applied to the
    Render dispatcher leaves it pointing at the old vendor, and every symptom
    of that is silent by design.

    Live example, Jul 28 - Aug 2 2026: the executor kept `OPENAI_BASE_URL` on
    xAI while `AGENT_MODEL` moved to OpenAI's `gpt-5.6-luna`. xAI answered
    `Model not found: gpt-5.6-luna` to every index greeting for five days — so
    every new user got the canned notice instead of a written one — and the
    same mismatch broke `VISION_API_KEY`'s inheritance (it keys off
    `VISION_BASE_URL == OPENAI_BASE_URL`), which is what put the indexer's eyes
    out. One vendor mismatch, two capabilities, nothing said either.

    `agent_model` next to `llm_host` is the whole point: an OpenAI model id
    beside `api.x.ai` is the bug, visible at a glance. Leaks nothing — model
    ids and hostnames are public config, and no key is reported beyond whether
    one is set. Never a gate; it only ever explains.
    """
    if shared_vision_configured():
        vision, vhost, vmodel = ("shared", _host(config.VISION_BASE_URL),
                                 config.VISION_MODEL)
    elif vision_fallback_available():
        vision, vhost, vmodel = ("agent", _host(config.OPENAI_BASE_URL),
                                 config.AGENT_MODEL)
    else:
        vision, vhost, vmodel = "off", None, None
    return {
        "agent_model": config.AGENT_MODEL or None,
        "llm_host": _host(config.OPENAI_BASE_URL),
        "llm_key_set": bool(config.OPENAI_API_KEY),
        "vision": vision,
        "vision_host": vhost,
        "vision_model": vmodel,
    }


def vision_client_for(plan):
    """(client, model) for one vision call.

    Frontier buys the frontier model for LOOKING at the footage as well as for
    reasoning about it — which is the half of that promise that would be
    easiest to quietly not deliver, since vision has always had its own
    provider and nobody would see the difference in the chat. Everyone else
    gets the shared VISION_* provider, or — when there isn't one — the agent's
    own eyes (see vision_fallback).
    """
    if is_frontier(plan) and frontier_available() and config.FRONTIER_VISION_MODEL:
        return frontier_client(), config.FRONTIER_VISION_MODEL
    if shared_vision_configured():
        return vision_client(), config.VISION_MODEL
    return vision_fallback() or (vision_client(), config.VISION_MODEL)


# Which plan the turn running on THIS THREAD belongs to.
#
# Thread-local, not a module global: the worker runs its lanes as threads
# (worker/main.py), so two agent turns can be in flight at once and a global
# would hand one user's turn the other user's model — and, because credits are
# priced from the model recorded on each row, the other user's price.
#
# It exists because vision is reached from eight different call sites across
# three modules, none of which know or should know about billing. Setting it
# once where the turn's plan is already resolved beats threading a `plan`
# argument through every look, reframe and contact sheet.
_turn = threading.local()


def set_turn_plan(plan):
    """Called once at the top of an agent turn. Pair with clear_turn_plan() in
    a finally — worker threads are reused, so a plan left behind would apply to
    the next job that lands on this thread."""
    _turn.plan = plan or ""


def clear_turn_plan():
    _turn.plan = ""


def turn_plan():
    return getattr(_turn, "plan", "") or ""


# The provider's own words for "I do not take images". Matched on the error
# body because the only honest source for this is the API itself: a model card
# claiming multimodality is not evidence (deepseek-v4-pro claimed it and 400s).
_BLIND_MARKERS = ("unknown variant `image_url`", "unknown variant 'image_url'",
                  "does not support image", "image input is not supported",
                  "vision is not supported")


# An OpenAI-compatible provider that does not know a request field says so in
# one of a few shapes. Matched on the error body for the same reason as the
# blind markers above: the API is the only honest source for what it accepts.
# "not supported", not "is not supported": OpenAI writes "Function tools with
# reasoning_effort ARE not supported", and the singular-only marker is why the
# first Luna agent turn in production died as a hard failure instead of
# adapting (job 1626, 2026-07-31).
_BAD_PARAM_MARKERS = ("unrecognized request argument", "unknown parameter",
                      "unknown field", "unexpected keyword argument",
                      "extra inputs are not permitted", "unknown argument",
                      "not supported", "invalid_request_error")

# Models whose provider has told us it does not accept reasoning_effort. Latched
# per process so one rejection is one wasted call, not one per iteration.
_no_reasoning_effort = set()

# Agent models whose provider rejected an image content part (round 67). The
# direct-sight look_at hands frames straight to the AGENT model; a blind
# provider (DeepSeek) 400s on the part itself and would 400 identically
# forever, so one rejection latches per model and look_at falls back to the
# separate VISION_* provider — the pre-round-67 behaviour, not a failure.
_agent_blind = set()


def agent_sees(model):
    """Can THIS agent model read image parts in its own context?"""
    return bool(config.AGENT_MULTIMODAL) and model not in _agent_blind


def mark_agent_blind(model):
    _agent_blind.add(model)


def looks_like_blind_model(exc):
    """Is `exc` the provider refusing the IMAGE PART itself (not a transient
    failure)? Same markers vision latches on."""
    msg = str(getattr(exc, "message", "") or exc).lower()
    return any(m in msg for m in _BLIND_MARKERS)


def audio_token_counts(usage):
    """(input, output) audio tokens across object/dict SDK spellings."""
    def _one(details):
        if isinstance(details, dict):
            value = details.get("audio_tokens")
        else:
            value = getattr(details, "audio_tokens", None) if details else None
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    if not usage:
        return 0, 0
    return (_one(getattr(usage, "prompt_tokens_details", None)),
            _one(getattr(usage, "completion_tokens_details", None)))


_audio_review_dead = False


def audio_review_available():
    """Whether the bounded actual-audio reviewer is configured and live."""
    return bool(config.AUDIO_REVIEW_MODEL and config.AUDIO_REVIEW_API_KEY
                and not _audio_review_dead)


def audio_part(path, fmt=None):
    """OpenAI-style input_audio part for one intentionally short clip."""
    if fmt is None:
        fmt = "mp3" if str(path).lower().endswith(".mp3") else "wav"
    with open(path, "rb") as source:
        b64 = base64.b64encode(source.read()).decode()
    return {"type": "input_audio",
            "input_audio": {"data": b64, "format": fmt}}


def _chat_usage(raw):
    """Small object adapter for the requests-based audio response."""
    raw = raw or {}
    return SimpleNamespace(
        prompt_tokens=raw.get("prompt_tokens") or 0,
        completion_tokens=raw.get("completion_tokens") or 0,
        total_tokens=raw.get("total_tokens") or 0,
        prompt_tokens_details=raw.get("prompt_tokens_details") or {},
        completion_tokens_details=raw.get("completion_tokens_details") or {},
    )


def _audio_answer_is_actionable(answer, purpose):
    value = str(answer or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    if any(phrase in lowered for phrase in (
            "i will listen", "i'll listen", "will now listen",
            "provided clip to assess", "once i listen")):
        return False
    if purpose == "audio_render_review" and not re.search(
            r"(?:^|\W)(?:pass|fix)(?:\W|$)", lowered):
        return False
    return True


def ask_audio(prompt, audio_paths, labels=None, max_tokens=260,
              purpose="audio_review"):
    """Listen to bounded real clips and return advisory editorial evidence.

    This lane never grants permission to edit and never blocks an approved
    choice.  Failures return ``None`` so measured waveform/audio-QC evidence
    stays the always-on fallback.  Audio bytes are never persisted in llm_calls;
    only the labels, answer and provider usage are recorded.
    """
    global _audio_review_dead
    if not audio_review_available() or not audio_paths:
        return None
    labels = list(labels or [])
    content = [{"type": "text", "text": prompt}]
    recorded = []
    for i, path in enumerate(audio_paths[:3]):
        label = (labels[i] if i < len(labels)
                 else str(path).rsplit("/", 1)[-1])
        content.append({"type": "text", "text": f"CLIP {i + 1}: {label}"})
        try:
            content.append(audio_part(path))
        except Exception as exc:
            print(f"[audio-review] could not read clip: {exc}", flush=True)
            continue
        recorded.append(label)
    if not recorded:
        return None

    body = {
        "model": config.AUDIO_REVIEW_MODEL,
        "modalities": ["text"],
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": content}],
    }
    url = config.AUDIO_REVIEW_BASE_URL.rstrip("/") + "/chat/completions"
    try:
        headers = {
            "Authorization": f"Bearer {config.AUDIO_REVIEW_API_KEY}",
            "Content-Type": "application/json",
        }
        retries_left, attempts = 2, 0
        used_dual_modality = False
        answer, usage = "", None
        while True:
            attempts += 1
            response = requests.post(
                url, json=body, timeout=config.AUDIO_REVIEW_TIMEOUT_S,
                headers=headers)
            lowered = response.text.lower()
            if response.status_code == 400 and "modalit" in lowered \
                    and not used_dual_modality:
                body["modalities"] = ["text", "audio"]
                body["audio"] = {"voice": "alloy", "format": "wav"}
                used_dual_modality = True
                continue
            if response.status_code in (500, 502, 503, 504) \
                    and retries_left > 0:
                retries_left -= 1
                continue
            if response.status_code < 400:
                payload = response.json()
                message = ((payload.get("choices") or [{}])[0].get("message")
                           or {})
                answer = (message.get("content") or
                          (message.get("audio") or {}).get("transcript")
                          or "").strip()
                usage = _chat_usage(payload.get("usage"))
                if not _audio_answer_is_actionable(answer, purpose) \
                        and retries_left > 0:
                    retries_left -= 1
                    content[0]["text"] = (
                        prompt + "\n\nYou already have the audio. Answer NOW "
                        "from what is audible. Return plain text only; do not "
                        "describe what you will do and do not return JSON.")
                    continue
            break
        if response.status_code >= 400:
            raise RuntimeError(
                f"audio review HTTP {response.status_code}: "
                f"{response.text[:240]}")
        if not _audio_answer_is_actionable(answer, purpose):
            answer = ""
        record(purpose,
               {"model": config.AUDIO_REVIEW_MODEL, "question": prompt,
                "clips": recorded},
               {"answer": answer or None, "attempts": attempts}, usage)
        return answer or None
    except Exception as exc:
        msg = str(exc)
        print(f"[audio-review] call failed: {msg}", flush=True)
        if any(marker in msg.lower() for marker in (
                "http 404", "model_not_found", "does not support audio",
                "input_audio is not supported")):
            _audio_review_dead = True
        _note_error(exc)
        record(purpose,
               {"model": config.AUDIO_REVIEW_MODEL, "question": prompt,
                "clips": recorded},
               {"error": msg[:300]}, None)
        return None


def looks_like_bad_parameter(exc, field):
    """Is `exc` the provider refusing a request FIELD, rather than failing?

    Deliberately narrow. Latching on any 400 would silently disable a setting
    because of an unrelated bad request, and treating a real outage as "the
    parameter is unsupported" would hide the outage. The field name must appear
    in the error, alongside a phrase that means "I do not know this".
    """
    msg = str(getattr(exc, "message", "") or exc).lower()
    if field.lower() not in msg:
        return False
    return any(m in msg for m in _BAD_PARAM_MARKERS)


def reasoning_effort_rejected(model):
    return model in _no_reasoning_effort


def mark_reasoning_effort_rejected(model):
    _no_reasoning_effort.add(model)


# Models that accept function tools on /v1/chat/completions ONLY with an
# explicit reasoning_effort of 'none' (round 67b, from the first Luna agent
# turn in production): gpt-5.6-luna 400s on tools + ANY reasoning — including
# the default that applies when the field is ABSENT — with "Function tools
# with reasoning_effort are not supported ... use /v1/responses or set
# reasoning_effort to 'none'". So the existing latch (strip the field) can
# never fix it: the request that omits the field is the request that fails.
# The dialect is the opposite — always SEND the field, as 'none', on every
# tools call. Latched per model from the provider's own words, same policy as
# every other dialect here.
_tools_effort_none = set()


def tools_need_effort_none(model):
    return model in _tools_effort_none


def mark_tools_need_effort_none(model):
    _tools_effort_none.add(model)


def looks_like_tools_reasoning_conflict(exc):
    """Is `exc` the provider saying tools and reasoning cannot ride the same
    chat/completions call? Distinct from looks_like_bad_parameter because the
    failing request usually does NOT carry reasoning_effort at all — the
    default effort is what conflicts — so there is no field to strip."""
    msg = str(getattr(exc, "message", "") or exc).lower()
    return ("reasoning_effort" in msg and "not supported" in msg
            and ("function tools" in msg or "tool" in msg))


# ── Completion-parameter dialects (round 67) ─────────────────────────────
# OpenAI's reasoning-model family (which gpt-5.6-luna belongs to) rejects the
# classic `max_tokens` field ("use max_completion_tokens") and any
# non-default `temperature`, while DeepSeek/xAI accept both — so the same
# call cannot be written one way for every provider. Same policy as
# reasoning_effort: send the classic spelling, and on the narrow
# unknown-parameter rejection adapt ONCE and latch per model, so the cost is
# one failed call per process instead of a 400 on every step of every turn.
_use_max_completion_tokens = set()
_no_temperature = set()


def _seed_known_dialects():
    """Pre-latch the dialects OpenAI's reasoning family has already proven.

    The latches are process-local, so every fresh worker used to re-learn
    them the expensive way: the first Luna agent call burned up to three 400s
    (max_tokens -> max_completion_tokens, temperature, tools+reasoning) before
    a step could succeed — and the adapt budget those retries share is also
    the budget real transient failures need. Worse, the tools+reasoning
    conflict could latch on thread A between thread B building its kwargs and
    B's own 400 arriving, at which point every recovery branch (all guarded by
    "not latched yet") stepped aside and B's turn died on the raw 400 — jobs
    3123/3156/3201, three real users' first sessions, all on 2026-08-08.

    Seeding costs nothing when wrong (the adapters still latch live) and
    saves the whole class when right. Matched narrowly: OpenAI host, gpt-5+
    reasoning family — xAI/DeepSeek keep their own measured dialects."""
    if "api.openai.com" not in (config.OPENAI_BASE_URL or ""):
        return
    for m in {config.AGENT_MODEL, config.FIRST_TURN_AGENT_MODEL}:
        if m and re.match(r"gpt-[5-9]", m):
            _use_max_completion_tokens.add(m)
            _no_temperature.add(m)
            _tools_effort_none.add(m)


_seed_known_dialects()


def completion_kwargs(model, max_tokens=None, temperature=None):
    """The token-limit and temperature kwargs in the dialect this model has
    proven to accept."""
    kw = {}
    if max_tokens is not None:
        if model in _use_max_completion_tokens:
            kw["max_completion_tokens"] = max_tokens
        else:
            kw["max_tokens"] = max_tokens
    if temperature is not None and model not in _no_temperature:
        kw["temperature"] = temperature
    # Priority processing (round 94): OpenAI-only — another provider behind
    # OPENAI_BASE_URL would 400 on the unknown parameter, so the tier is
    # gated on the host, same rule responses_available uses.
    if config.OPENAI_SERVICE_TIER and \
            "api.openai.com" in (config.OPENAI_BASE_URL or ""):
        kw["service_tier"] = config.OPENAI_SERVICE_TIER
    return kw


# ── The Responses lane: how a reasoning model keeps its tools (round 91) ──
#
# gpt-5.6-luna refuses the two things an editing agent needs at once, and says
# so precisely:
#
#   400: "Function tools with reasoning_effort are not supported for
#         gpt-5.6-luna in /v1/chat/completions. To use function tools, use
#         /v1/responses or set reasoning_effort to 'none'."
#
# The loop obeys that by latching reasoning_effort='none' — tools are not
# optional for an editor — and the consequence was invisible: from Jul 31 2026
# the agent planned every edit with NO deliberation at all. Measured over the
# ten days after the switch: 875 agent calls, ZERO reasoning tokens. The model
# it replaced (deepseek-v4-pro) was spending 335-621 reasoning tokens on every
# single call, and the "the edits are disgusting" complaints date from exactly
# the changeover.
#
# So the same model gets its thinking back by asking the endpoint that allows
# both. Deliberately NOT via the OpenAI SDK: both services pin openai==1.59.9,
# which predates .responses, and bumping a shared dependency to buy one call is
# a far larger blast radius than one HTTPS request through `requests` — which
# this module already imports and already uses for image generation.
#
# This is a LANE, not a migration. /v1/responses is OpenAI-only; the paid,
# frontier and image providers are xAI, which serves chat/completions. Every
# other caller is untouched, and any failure here falls back to exactly what
# runs today (see agent_loop._agent_completion). Not being able to reach the
# live API from the dev machine is precisely why that fallback is mandatory
# rather than optional.


class _RespToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = type("F", (), {"name": name,
                                       "arguments": arguments})()


class _RespMessage:
    """A chat-completions-shaped message, so the agent loop needs no new
    branch to read one."""

    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class _RespChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _RespUsage:
    def __init__(self, raw):
        self.prompt_tokens = raw.get("input_tokens") or 0
        self.completion_tokens = raw.get("output_tokens") or 0
        self.total_tokens = raw.get("total_tokens") or 0
        det = raw.get("output_tokens_details") or {}
        # The number this whole lane exists to make non-zero. Named to match
        # what reasoning_tokens() already looks for, so the recorder writes
        # llm_calls.response->>'reasoning_out' with no change there — which is
        # also how anyone verifies the lane is really working, in SQL.
        self.reasoning_tokens = det.get("reasoning_tokens") or 0
        # Likewise for the cache discount: cached_input_tokens() reads
        # prompt_tokens_details.cached_tokens, so present it under that name
        # rather than the Responses spelling, or every cached turn would be
        # billed at the full input price.
        self.prompt_tokens_details = {
            "cached_tokens":
                (raw.get("input_tokens_details") or {}).get("cached_tokens")
                or 0}


class _RespCompletion:
    def __init__(self, choices, usage):
        self.choices = choices
        self.usage = usage


def _to_responses_input(messages):
    """chat `messages` -> Responses `input`.

    Raises on anything it cannot map, so a shape this does not understand
    falls back to chat/completions instead of being sent half-translated.
    """
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"type": "function_call_output",
                        "call_id": m.get("tool_call_id"),
                        "output": str(m.get("content") or "")})
            continue
        if role == "assistant" and m.get("tool_calls"):
            if (m.get("content") or "").strip():
                out.append({"role": "assistant",
                            "content": [{"type": "output_text",
                                         "text": m["content"]}]})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                out.append({"type": "function_call",
                            "call_id": tc.get("id"),
                            "name": fn.get("name"),
                            "arguments": fn.get("arguments") or "{}"})
            continue
        content = m.get("content")
        if isinstance(content, str):
            kind = "output_text" if role == "assistant" else "input_text"
            out.append({"role": role,
                        "content": [{"type": kind, "text": content}]})
            continue
        if isinstance(content, list):
            parts = []
            for p in content:
                if p.get("type") == "text":
                    parts.append({"type": "input_text", "text": p["text"]})
                elif p.get("type") == "image_url":
                    url = (p.get("image_url") or {}).get("url")
                    if not url:
                        raise ValueError("image part without a url")
                    parts.append({"type": "input_image", "image_url": url})
                else:
                    raise ValueError(f"unmapped content part "
                                     f"{p.get('type')!r}")
            out.append({"role": role, "content": parts})
            continue
        raise ValueError(f"unmapped message shape for role {role!r}")
    return out


def _to_responses_tools(tools):
    """chat tool schemas -> Responses tool schemas (the function is flattened
    up one level: {type, function:{name,...}} becomes {type, name, ...})."""
    out = []
    for t in tools or []:
        fn = t.get("function") or {}
        if not fn.get("name"):
            raise ValueError("tool without a name")
        out.append({"type": "function", "name": fn["name"],
                    "description": fn.get("description") or "",
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}}})
    return out


def _from_responses(payload):
    """Responses output -> a chat-completions-shaped object.

    Raises when the body is not what this understands, so a provider change
    degrades to the old lane rather than to garbage tool calls.
    """
    if not isinstance(payload, dict) or "output" not in payload:
        raise ValueError("no output in the responses payload")
    text_bits, calls = [], []
    for item in payload.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    text_bits.append(c["text"])
        elif kind == "function_call":
            calls.append(_RespToolCall(item.get("call_id") or item.get("id"),
                                       item.get("name"),
                                       item.get("arguments") or "{}"))
        # 'reasoning' items carry the thinking itself. They are deliberately
        # NOT fed back into the next request yet: threading them is what
        # preserves one chain of thought ACROSS tool calls, and getting that
        # wrong while unable to test against the live API would be worse than
        # the model thinking afresh on each step — which is already the entire
        # difference between this lane and reasoning_effort='none'.
    if not text_bits and not calls:
        if payload.get("status") == "incomplete":
            # The model spent the whole max_output_tokens budget REASONING and
            # never reached content — routine at high/max effort, and exactly
            # the shape the agent loop's truncation retry exists for. Raising
            # here sent the step to chat/completions, where this model answers
            # with effort='none' — the lane's whole point, lost on precisely
            # the steps that think hardest (the 'high,none' mixing on every
            # long Aug 6-8 turn in llm_calls). Hand back an empty 'length'
            # completion instead, so the caller doubles the budget and retries
            # IN the lane.
            return _RespCompletion(
                [_RespChoice(_RespMessage(None, None), "length")],
                _RespUsage(payload.get("usage") or {}))
        raise ValueError("responses returned neither text nor a tool call")
    # 'length' is what the loop's truncation retry keys on, and an incomplete
    # response with nothing in it is exactly that case.
    finish = ("length" if payload.get("status") == "incomplete"
              else ("tool_calls" if calls else "stop"))
    msg = _RespMessage("\n".join(text_bits) or None, calls or None)
    return _RespCompletion([_RespChoice(msg, finish)],
                           _RespUsage(payload.get("usage") or {}))


_responses_dead = set()


def mark_responses_dead(model):
    """Stop trying the lane for this model in this process."""
    _responses_dead.add(model)


def responses_available(model, base_url):
    """Is the Responses lane worth trying for this model?

    Gated on configuration and on the endpoint being one that serves
    /v1/responses at all (OpenAI's) — NOT on the model having already failed
    on chat/completions.

    It was gated on that latch for one deploy and the gate never opened. The
    latch is process-local and starts EMPTY, so a fresh worker's first agent
    call found it unset, took the chat path, ate the 400, latched, retried
    with effort='none' and answered without thinking. Job 2602 was a
    single-call turn, so that was the whole turn. Requiring the failure
    before allowing the fix means every process's first call — and every
    short turn — never reasons at all.

    So: try the lane, and let the fallback be the safety net it already is.
    A model that proves the endpoint is not there for it is latched OFF
    (mark_responses_dead) so a doomed request is paid once per process, not
    once per step.
    """
    if not config.AGENT_RESPONSES_LANE or not config.AGENT_REASONING_EFFORT:
        return False
    if model in _responses_dead:
        return False
    return "api.openai.com" in (base_url or "")


def rate_limit_wait(exc, attempt, seconds_left, shutting_down=False):
    """How long to sleep before retrying a rate-limited agent step, or None
    to give up and fail the turn (round 91).

    One agent turn can exceed the provider's whole TPM tier by itself
    (round 80), so a long turn hitting 429 mid-flight is expected operation —
    and it used to KILL the turn: minutes of finished edits ended in "I'm
    being rate-limited, resend that". Waiting is strictly better than dying
    while the turn still has wall clock. Bounds: never during a deploy drain,
    at most 6 waits, never into the turn's last 20s, honour Retry-After up
    to 60s, otherwise grow 12s per attempt capped at 45s — four flat 15s
    waits (60s total patience) was less than one real TPM burst, and job
    4120 died mid-burst on Aug 9 while the burst still had minutes to run.
    """
    text = f"{exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if not (status == 429 or "rate limit" in text
            or "too many requests" in text):
        return None
    if shutting_down or attempt > 6 or seconds_left < 20:
        return None
    wait = min(45.0, 12.0 * max(1, attempt))
    try:
        resp = getattr(exc, "response", None)
        ra = resp.headers.get("retry-after") if resp is not None else None
        if ra:
            wait = min(60.0, max(wait, float(ra)))
    except Exception:
        pass
    return max(1.0, min(wait, seconds_left - 10.0))


def looks_like_responses_unsupported(exc):
    """Is this the endpoint or model saying "not here", as opposed to a
    transient failure? Only these latch the lane off for the process — a
    timeout or a 500 must not cost the agent its thinking for the rest of
    the worker's life."""
    text = f"{exc}".lower()
    # A 400 complaining about the reasoning EFFORT is a bad knob value, not
    # the endpoint saying "not here". Latching the lane dead on it would turn
    # one config typo (or a provider trimming its effort enum) into a
    # process-wide reasoning blackout — the fallback answers with
    # effort='none'. Fall back for this step only and try again next step.
    if "http 400" in text and "reasoning" in text and "effort" in text:
        return False
    return any(k in text for k in (
        "http 404", "http 400", "http 405", "not found", "unknown endpoint",
        "unsupported", "does not exist", "invalid_request_error",
        "no output in the responses payload"))


def responses_create(base_url, api_key, model, messages, tools,
                     max_tokens=None, effort=None, timeout=None):
    """One /v1/responses call, in and out in chat-completions shape.

    Raises on ANY problem — transport, HTTP status, or a body this does not
    understand — because the only correct answer to "this lane did not work"
    is to fall back to the lane that does.
    """
    url = (base_url or "").rstrip("/") + "/responses"
    body = {"model": model,
            "input": _to_responses_input(messages),
            "tools": _to_responses_tools(tools)}
    if max_tokens:
        body["max_output_tokens"] = int(max_tokens)
    if effort:
        body["reasoning"] = {"effort": effort}
    if config.OPENAI_SERVICE_TIER:
        body["service_tier"] = config.OPENAI_SERVICE_TIER
    r = requests.post(url, json=body,
                      timeout=timeout or config.LLM_TIMEOUT_S,
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"responses HTTP {r.status_code}: {r.text[:300]}")
    return _from_responses(r.json())


def adapt_completion_kwargs(exc, model, kw):
    """If `exc` is the provider refusing max_tokens or temperature, latch the
    model's dialect and return corrected kwargs for one retry. None = not an
    adaptable failure (let it propagate)."""
    if "max_tokens" in kw and looks_like_bad_parameter(exc, "max_tokens"):
        _use_max_completion_tokens.add(model)
        out = dict(kw)
        out["max_completion_tokens"] = out.pop("max_tokens")
        print(f"[llm] {model} wants max_completion_tokens — latched",
              flush=True)
        return out
    if "temperature" in kw and looks_like_bad_parameter(exc, "temperature"):
        _no_temperature.add(model)
        out = dict(kw)
        out.pop("temperature")
        print(f"[llm] {model} rejects a custom temperature — latched",
              flush=True)
        return out
    return None


def create_with_dialect(client_obj, model, messages, max_tokens=None,
                        temperature=None, **extra):
    """chat.completions.create with automatic parameter-dialect adaptation.
    For the simple one-shot callers (ask_text / ask_vision / regen); the
    agent loop integrates the same helpers into its richer retry chain."""
    kw = completion_kwargs(model, max_tokens, temperature)
    kw.update(extra)
    if "tools" in kw and tools_need_effort_none(model):
        kw["reasoning_effort"] = "none"
    attempts = 0
    while True:
        try:
            return client_obj.chat.completions.create(
                model=model, messages=messages, **kw)
        except Exception as e:
            attempts += 1
            if attempts > 3:
                raise
            if "tools" in kw and looks_like_tools_reasoning_conflict(e) \
                    and kw.get("reasoning_effort") != "none":
                # The honesty regen passes the tool schemas for context
                # (tool_choice='none'), which is still a tools call to the
                # provider — Luna refuses it with default reasoning exactly
                # as it refuses the agent loop's.
                # Deliberately NOT gated on "not already latched": another
                # thread latching between this call's kwargs being built and
                # its 400 arriving used to make every branch here step aside,
                # and the raw 400 killed the request. The recovery is
                # idempotent — set 'none' and retry — so run it whenever the
                # request that failed wasn't already carrying 'none'.
                mark_tools_need_effort_none(model)
                kw["reasoning_effort"] = "none"
                continue
            if "reasoning_effort" in kw \
                    and looks_like_bad_parameter(e, "reasoning_effort"):
                # A provider that does not know the field (DeepSeek, xAI)
                # says so once; strip it, latch, and never send it again —
                # the same one-failed-call-per-process policy as max_tokens.
                mark_reasoning_effort_rejected(model)
                kw = {k: v for k, v in kw.items() if k != "reasoning_effort"}
                continue
            adapted = adapt_completion_kwargs(e, model, kw)
            if adapted is None:
                raise
            kw = adapted


def vision_available(plan=None):
    """Can THIS turn look at pictures?

    Reads the thread-local plan by default so the ~dozen tools that ask this
    (to decide whether to offer visual inspection at all) get the right answer
    without any of them learning about billing. A Frontier turn can see even
    when the shared VISION_* provider is unconfigured or has been latched
    blind, because it is a different endpoint with a different key.
    """
    plan = turn_plan() if plan is None else plan
    if (is_frontier(plan) and frontier_available()
            and config.FRONTIER_VISION_MODEL):
        return True
    # ...and when the shared VISION_* provider is unconfigured or has latched
    # blind, the agent's own eyes still count as vision (see vision_fallback).
    return shared_vision_configured() or vision_fallback() is not None


def cached_input_tokens(usage):
    """Cache-HIT input tokens from a usage object, across the two spellings
    OpenAI-compatible providers use: DeepSeek's top-level
    prompt_cache_hit_tokens, and OpenAI's prompt_tokens_details.cached_tokens.
    Returns 0 when the provider reports no caching — which makes the cached
    price a no-op rather than a wrong discount."""
    if not usage:
        return 0
    n = getattr(usage, "prompt_cache_hit_tokens", None)
    if n is None:
        details = getattr(usage, "prompt_tokens_details", None)
        n = getattr(details, "cached_tokens", None) if details else None
        if n is None and isinstance(details, dict):
            n = details.get("cached_tokens")
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return 0
    # Never let a bogus provider number exceed the prompt itself: the charge
    # subtracts this from prompt_tokens, and a too-large value would credit
    # the user for tokens they did use.
    total = getattr(usage, "prompt_tokens", None) or 0
    return max(0, min(n, int(total)))


def reasoning_tokens(usage):
    """"Thinking" tokens from a usage object, across the spellings
    OpenAI-compatible providers use: completion_tokens_details.reasoning_tokens
    (object or dict), and DeepSeek's top-level reasoning_tokens.

    Returns 0 when the provider reports none.

    IMPORTANT: this is recorded, not automatically charged. Whether these
    tokens are ALREADY inside completion_tokens or sit beside it is a per-model
    fact — see model_prices.MODEL_PRICES' `reasoning_separate`, and the
    verification query in that module's docstring. Adding them for a provider
    that folds them in would double-charge every reasoning turn.
    """
    if not usage:
        return 0
    details = getattr(usage, "completion_tokens_details", None)
    n = getattr(details, "reasoning_tokens", None) if details else None
    if n is None and isinstance(details, dict):
        n = details.get("reasoning_tokens")
    if n is None:
        n = getattr(usage, "reasoning_tokens", None)
    try:
        return max(0, int(n or 0))
    except (TypeError, ValueError):
        return 0


def image_part(jpeg_path):
    with open(jpeg_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def ask_vision(prompt, image_paths, max_tokens=1500, purpose="vision",
               image_names=None, reasoning_effort=None):
    """One call to VISION_MODEL with N images. Returns text, or None on any
    failure — vision is always optional. image_names (storage keys / labels)
    are what gets recorded to llm_calls — never the image bytes.

    max_tokens is the whole output budget, and on a REASONING model (gpt-5.6-
    luna) the thinking spends from it BEFORE the first answer token — so a cap
    that merely fits the answer can return `content=""` with every token gone
    to reasoning. That is not a hypothetical: from the Luna switch until Aug 3
    2026 every 25-tile index sheet starved exactly like that (answer null,
    reasoning_out == the cap), and every real upload indexed blind while the
    provider, the key and the pipeline were all healthy. Two defenses, both
    here so every caller gets them: callers ask for small reasoning on
    descriptive work (reasoning_effort), and a starved call — no content, the
    budget exhausted — is retried ONCE with triple the cap rather than
    reported as "the model didn't answer"."""
    if not vision_available():
        return None
    content = [{"type": "text", "text": prompt}]
    content += [image_part(p) for p in image_paths]
    names = image_names or [str(p).rsplit("/", 1)[-1] for p in image_paths]
    # The Frontier plan looks at footage with the frontier model too, not just
    # reasons with it. The model recorded below MUST be the one that actually
    # answered — every credit charge and admin cost view prices a row from its
    # own `model` column.
    vclient, vmodel = vision_client_for(turn_plan())
    try:
        # Vision gets a MORE generous timeout than the text agent (spiky
        # multimodal latency); retries stay at the client default. Dialect-
        # aware (round 67): OpenAI reasoning models refuse max_tokens and a
        # custom temperature.
        extra = {}
        if reasoning_effort and not reasoning_effort_rejected(vmodel):
            extra["reasoning_effort"] = reasoning_effort
        cap = int(max_tokens)
        for attempt in (1, 2):
            resp = create_with_dialect(
                vclient.with_options(timeout=config.VISION_TIMEOUT_S),
                vmodel,
                [{"role": "user", "content": content}],
                max_tokens=cap,
                temperature=0.1,
                **extra,
            )
            answer = (resp.choices[0].message.content or "").strip() or None
            usage = getattr(resp, "usage", None)
            spent = max(reasoning_tokens(usage),
                        int(getattr(usage, "completion_tokens", 0) or 0))
            if answer is None and attempt == 1 and spent >= cap * 0.9:
                # Starved, not refused: the model was still thinking when the
                # budget ran out. Record the starved attempt so admin shows
                # both calls, then retry with room to finish.
                record(purpose,
                       {"model": vmodel, "question": prompt, "images": names},
                       {"answer": None, "starved_at_max_tokens": cap}, usage)
                cap *= 3
                continue
            record(purpose,
                   {"model": vmodel, "question": prompt,
                    "images": names},
                   {"answer": answer}, usage)
            return answer
    except Exception as e:
        global _vision_blind
        msg = str(e)
        print(f"[vision] call failed: {msg}", flush=True)
        # A provider that rejects the image PART is not having a bad minute —
        # it will reject every future call identically. Stop asking, and let
        # vision_available() tell the tools so they say "no vision configured"
        # rather than "the model didn't answer" over and over.
        #
        # Only the SHARED provider may latch this process-wide. A Frontier-only
        # failure must not blind every other user's turns — and a shared-model
        # 400 says nothing about the frontier one.
        #
        # Latch by WHICH MODEL proved blind, not by which path chose it. The
        # two latches can both fire on one failure (VISION_MODEL and
        # AGENT_MODEL default to the same id), and that is the correct
        # outcome: a model that cannot see must not be reached down either
        # route. Latching only _vision_blind would have handed the very next
        # look to vision_fallback — the same blind model, one layer down.
        if any(m in msg for m in _BLIND_MARKERS):
            if vmodel == config.VISION_MODEL and shared_vision_configured():
                _vision_blind = True
                print(f"[vision] {config.VISION_MODEL} at "
                      f"{config.VISION_BASE_URL} does not accept images — the "
                      "shared vision provider is DISABLED for this process. "
                      "Set VISION_BASE_URL/VISION_MODEL/VISION_API_KEY to a "
                      "multimodal provider.", flush=True)
            if vmodel == config.AGENT_MODEL:
                mark_agent_blind(vmodel)
                print(f"[vision] {vmodel} does not accept images — the agent "
                      "model can no longer stand in for vision.", flush=True)
        _note_error(e)
        record(purpose,
               {"model": vmodel, "question": prompt,
                "images": names, "base_url": config.VISION_BASE_URL},
               {"error": msg[:300],
                "provider_cannot_see": _vision_blind or None}, None)
        return None


def ask_text(system, user, max_tokens=300, temperature=0.5, purpose="text"):
    """One plain-text completion against AGENT_MODEL. Returns
    {"text", "model", "prompt_tokens", "completion_tokens"} or None on any
    failure — callers must keep a non-LLM fallback."""
    if not config.OPENAI_API_KEY:
        return None
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    try:
        resp = create_with_dialect(
            client(), config.AGENT_MODEL, messages,
            max_tokens=max_tokens, temperature=temperature)
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        record(purpose,
               {"model": config.AGENT_MODEL, "system": system, "user": user},
               {"text": text}, usage)
        if not text:
            return None
        return {"text": text, "model": config.AGENT_MODEL,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens",
                                             None)}
    except Exception as e:
        print(f"[llm] ask_text failed: {e}", flush=True)
        _note_error(e)
        record(purpose,
               {"model": config.AGENT_MODEL, "system": system, "user": user},
               {"error": str(e)[:300]}, None)
        return None


# ------------------------------------------------------------------ #
#  Image generation / editing (DashScope multimodal-generation)        #
# ------------------------------------------------------------------ #

# Valid sizes differ between the qwen-image 1.x and 2.x families; an
# unknown aspect (or an API size rejection) falls back to the model default.
_IMAGE_SIZES_V1 = {"16:9": "1664*928", "9:16": "928*1664",
                   "1:1": "1328*1328", "4:3": "1472*1140",
                   "3:4": "1140*1472"}
_IMAGE_SIZES_V2 = {"16:9": "2688*1536", "9:16": "1536*2688",
                   "1:1": "2048*2048", "4:3": "2368*1728",
                   "3:4": "1728*2368"}


def image_provider():
    """Which image backend to use, inferred from the IMAGE endpoint (NOT the
    chat endpoint — they are configured separately):
      'dashscope' — native multimodal-generation (generate AND restyle/edit);
      'openai'    — OpenAI-compatible /images/generations (xAI Grok, etc.);
                    text-to-image ONLY, no editing.
    Returns None when no image backend is resolvable."""
    if config.IMAGE_API_URL or "dashscope" in (config.IMAGE_BASE_URL or ""):
        return "dashscope"
    if config.IMAGE_BASE_URL:
        return "openai"
    return None


def image_api_url():
    """DashScope native endpoint (only meaningful for the dashscope provider)."""
    if config.IMAGE_API_URL:
        return config.IMAGE_API_URL
    base = (config.IMAGE_BASE_URL or "").split("/compatible-mode")[0]
    base = base.rstrip("/")
    if "dashscope" not in base:
        return None
    return base + "/api/v1/services/aigc/multimodal-generation/generation"


def image_available():
    """IMAGE_API_KEY, not OPENAI_API_KEY: a chat key is not an image key once
    the two providers differ, and claiming otherwise turns a missing key into a
    404 per call instead of an honestly-absent capability."""
    return bool(config.IMAGE_GEN_MODEL and config.IMAGE_API_KEY
                and image_provider())


def image_edit_available():
    """Frame/image restyling needs the DashScope native endpoint — the
    OpenAI-compatible /images/generations surface (xAI) can only generate."""
    return image_available() and image_provider() == "dashscope"


def image_size_for(aspect, model):
    table = _IMAGE_SIZES_V2 if "2." in (model or "") else _IMAGE_SIZES_V1
    return table.get((aspect or "").strip())


def _image_data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _image_call(model, content, purpose, record_request, size=None):
    """One synchronous DashScope image call. content is the message content
    list (text and/or image parts). Returns (result_url, None) or
    (None, short_error). record_request is what gets logged to llm_calls —
    never the image bytes."""
    url = image_api_url()
    body = {"model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"n": 1, "watermark": False}}
    if size:
        body["parameters"]["size"] = size
    try:
        resp = requests.post(
            url, json=body, timeout=config.IMAGE_TIMEOUT_S,
            headers={"Authorization": f"Bearer {config.IMAGE_API_KEY}",
                     "Content-Type": "application/json"})
        data = resp.json()
    except Exception as e:
        err = f"image API unreachable: {str(e)[:200]}"
        record(purpose, record_request, {"error": err}, None)
        return None, err
    if data.get("code") or resp.status_code >= 400:
        err = (f"{data.get('code') or resp.status_code}: "
               f"{str(data.get('message') or data)[:300]}")
        # An invalid size for this model family is recoverable — retry once
        # letting the model pick its default resolution.
        if size and "size" in err.lower():
            return _image_call(model, content, purpose, record_request,
                               size=None)
        record(purpose, record_request, {"error": err}, None)
        return None, err
    try:
        parts = data["output"]["choices"][0]["message"]["content"]
        image_url = next(p["image"] for p in parts if p.get("image"))
    except (KeyError, IndexError, StopIteration, TypeError):
        err = f"image API returned no image: {str(data)[:300]}"
        record(purpose, record_request, {"error": err}, None)
        return None, err
    usage = data.get("usage") or {}
    record(purpose, record_request,
           {"image_url": image_url.split("?")[0][:300],
            "width": usage.get("width"), "height": usage.get("height")},
           None)
    return image_url, None


def _download_image(url, out_path):
    r = requests.get(url, timeout=60, stream=True)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)


def _openai_image_gen(model, prompt, out_path):
    """Text-to-image via the OpenAI-compatible /images/generations endpoint
    (xAI Grok). Uses the IMAGE client — the chat provider may not have this
    endpoint at all (DeepSeek does not)."""
    try:
        resp = image_client().images.generate(model=model, prompt=prompt, n=1)
        d = resp.data[0]
        b64 = getattr(d, "b64_json", None)
        url = getattr(d, "url", None)
    except Exception as e:
        err = f"image API error: {str(e)[:200]}"
        record("image_gen", {"model": model, "prompt": prompt},
               {"error": err}, None)
        return False, err
    try:
        if b64:
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(b64))
        elif url:
            _download_image(url, out_path)
        else:
            err = "image API returned no image"
            record("image_gen", {"model": model, "prompt": prompt},
                   {"error": err}, None)
            return False, err
    except Exception as e:
        return False, f"could not save the generated image: {str(e)[:200]}"
    # response MUST carry an 'image_url' key so charge_turn_credits counts it.
    record("image_gen", {"model": model, "prompt": prompt},
           {"image_url": (url.split("?")[0][:300] if url else "b64")}, None)
    return True, None


def generate_image(prompt, out_path, aspect=None):
    """Text-to-image via IMAGE_GEN_MODEL. Saves a PNG to out_path.
    Returns (True, None) or (False, short_error)."""
    if not image_available():
        return False, "no image model configured"
    model = config.IMAGE_GEN_MODEL
    if image_provider() == "openai":
        return _openai_image_gen(model, prompt, out_path)
    size = image_size_for(aspect, model)
    url, err = _image_call(
        model, [{"text": prompt}], "image_gen",
        {"model": model, "prompt": prompt, "size": size}, size=size)
    if not url:
        return False, err
    try:
        _download_image(url, out_path)
    except Exception as e:
        return False, f"could not download the generated image: {str(e)[:200]}"
    return True, None


def edit_image(image_path, instruction, out_path, image_name="image"):
    """Instruction-based edit of a local image via IMAGE_EDIT_MODEL (DashScope
    only). Returns (True, None) or (False, short_error). image_name is what
    gets logged, never the bytes."""
    if not image_available():
        return False, "no image model configured"
    if not image_edit_available():
        return (False, "the current image model can only GENERATE images from "
                       "a text description — it cannot restyle or edit an "
                       "existing frame/image. Describe the desired image "
                       "instead and it will be generated fresh.")
    model = config.IMAGE_EDIT_MODEL or config.IMAGE_GEN_MODEL
    url, err = _image_call(
        model,
        [{"image": _image_data_url(image_path)}, {"text": instruction}],
        "image_edit",
        {"model": model, "instruction": instruction, "image": image_name})
    if not url:
        return False, err
    try:
        _download_image(url, out_path)
    except Exception as e:
        return False, f"could not download the edited image: {str(e)[:200]}"
    return True, None


def extract_json_array(text):
    """Lenient JSON array extraction from a model reply.

    Lenient includes TRUNCATED: a reply that hit its token cap mid-array used
    to lose every element (no closing bracket -> no match -> None), which for
    an index sheet meant one long answer threw away 25 captions. The salvage
    walks back to the last complete object and closes the array there —
    partial captions beat blind."""
    if not text:
        return None
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            out = json.loads(m.group(0))
            if isinstance(out, list):
                return out
        except json.JSONDecodeError:
            pass
    start = text.find("[")
    if start < 0:
        return None
    tail = text[start:]
    last = tail.rfind("}")
    while last > 0:
        try:
            out = json.loads(tail[:last + 1] + "]")
            return out if isinstance(out, list) else None
        except json.JSONDecodeError:
            last = tail.rfind("}", 0, last)
    return None
