"""The OpenAI edit rung: a customer's edit on an OpenAI model, Haiku behind every call.

Owner, 14 Nov 2026: "we want the best for paid and trials since trials are the gateways for
conversion ... it would also be good to have an openai model like sol or terra that could be
better than haiku since the api credits are coming from a sub too ... we're constrained from
anthropic's monthly 200."

The Gauntlet scoreboard (.valmera/week-1112/lanes/EDIT-MODEL/table.json, the same 7 real
requests replayed on copies): claude-haiku-5-5 met 6 of 12 asks at eye quality 3.7;
gpt-6.1-sol met 8 of 12 at 4.4. That table is what CLAUDE.md asks for before an OpenAI model may
decide a customer's edit (llm.editing_lanes), and only the models it justified are in
``RUNG_MODELS``.

How it runs. The trial lane and the paid Claude lane keep their client (claude_lane's
ClaudeLaneClient, everything downstream unchanged); claude_lane.create hands a call whose model
is a rung model to ``create`` here, which sends the same conversation and tools to OpenAI's
Responses API and returns the chat-shaped completion the loop reads. Any OpenAI failure on that
call (a refusal, a 4xx/5xx, a timeout, an empty or unreadable body, the credit running out) is
answered by the fallback model on the Anthropic lane IN THE SAME CALL, and the rest of that turn
stays on the fallback: the customer's turn never fails or waits on OpenAI. A refusal that says
the wallet or the key is gone (insufficient_quota, billing, 401/403) also latches the rung shut
for this process for ``config.EDIT_RUNG_LATCH_S``, so the next turns go straight to Haiku with
no failed call in front, and queues one admin alert.

Money. OpenAI calls are paid from the OpenAI credit (a subscription), never the Anthropic
cycle: they are not metered onto the Claude credit line (claude_credit) or the paid Claude
ledger. What the CUSTOMER spends is unchanged by the switch: a subscriber's credits and a trial
account's allowance are priced at the fallback's (Haiku's) card for the same tokens
(``allowance_usd``; llm.record writes credit_cost_usd), so the better model costs them nothing
more. provider_cost_usd stays the true OpenAI cost on every row for every report.
"""

import json
import threading
import time
from types import SimpleNamespace

import config
import model_prices

PROVIDER = "openai"
_lock = threading.Lock()
_closed_until = 0.0
_closed_reason = ""
_tls = threading.local()


def rung_models():
    return tuple(m.strip().lower() for m in str(config.EDIT_RUNG_MODELS or "").split(",")
                 if m.strip())


def is_rung_model(model):
    """May this OpenAI model decide a customer's edit on the rung? Only the scoreboard's."""
    return str(model or "").strip().lower() in rung_models()


def fallback_model():
    return config.EDIT_RUNG_FALLBACK_MODEL


def configured():
    return bool(config.OPENAI_API_KEY)


def available():
    return configured() and time.monotonic() >= _closed_until


def closed_reason():
    return _closed_reason if time.monotonic() < _closed_until else ""


def _close(reason, seconds=None):
    global _closed_until, _closed_reason
    with _lock:
        _closed_until = time.monotonic() + float(
            config.EDIT_RUNG_LATCH_S if seconds is None else seconds)
        _closed_reason = str(reason)[:300]


def reopen():
    """Tests/operators: clear the latch."""
    global _closed_until, _closed_reason
    with _lock:
        _closed_until, _closed_reason = 0.0, ""


# ------------------------------------------------------------------ the turn's fallback mark

def _turn_holder():
    """What carries 'this turn fell back' across the turn's threads: the turn's llm recorder
    (pool threads install the caller's), else this thread."""
    try:
        import llm
        rec = llm.get_recorder()
    except Exception:  # pragma: no cover
        rec = None
    return rec if rec is not None else _tls


TURN_MARK_S = 1800.0     # a mark outlives no turn (a thread with no recorder keeps it this long)


def _live(mark):
    if not mark:
        return None
    reason, at = mark
    return reason if time.monotonic() - at < TURN_MARK_S else None


def turn_fell_back():
    return _live(getattr(_turn_holder(), "edit_rung_fallback", None))


def _mark_turn(reason):
    holder = _turn_holder()
    mark = (str(reason)[:300], time.monotonic())
    try:
        if not _live(getattr(holder, "edit_rung_fallback", None)):
            holder.edit_rung_fallback = mark
    except Exception:  # pragma: no cover - a builtin recorder cannot take attributes
        _tls.edit_rung_fallback = mark


def clear_turn():
    """Tests: forget this thread's mark."""
    _tls.edit_rung_fallback = None


def _cache_key():
    """One prompt-cache key per turn, so every step of a turn lands on the same cached prefix."""
    try:
        import claude_trial
        scope = claude_trial.current()
        if scope is not None and getattr(scope, "job_id", None):
            return f"valmera-edit-{scope.job_id}"
    except Exception:  # pragma: no cover
        pass
    try:
        import llm
        key = getattr(getattr(llm, "_turn", None), "cache_key", None)
        if key:
            return str(key)[:64]
    except Exception:  # pragma: no cover
        pass
    return None


# ------------------------------------------------------------------ errors

_WALLET_MARKERS = ("insufficient_quota", "billing", "quota", "credit", "exceeded your current")


class RungFailed(RuntimeError):
    def __init__(self, message, status=None, wallet=False):
        super().__init__(message)
        self.status = status
        self.wallet = wallet


def _wallet_refusal(status, text):
    low = str(text or "").lower()
    if status in (401, 403):
        return True
    return status in (402, 429) and any(m in low for m in _WALLET_MARKERS)


# ------------------------------------------------------------------ translation

class _RespUsage:
    """Chat-shaped usage from a Responses body, with the call's exact OpenAI cost."""

    def __init__(self, model, raw):
        raw = raw or {}
        cached = int((raw.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        reasoning = int((raw.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        self.prompt_tokens = int(raw.get("input_tokens") or 0)
        self.completion_tokens = int(raw.get("output_tokens") or 0)
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        self.input_tokens = max(0, self.prompt_tokens - cached)
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = 0
        self.output_tokens = self.completion_tokens
        self.prompt_tokens_details = {"cached_tokens": cached, "cache_write_tokens": 0}
        self.completion_tokens_details = {"reasoning_tokens": reasoning}
        self.service_tier = raw.get("service_tier")
        if model in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"):
            # the long-context and service-tier rules of the GPT-6 cards
            self.provider_cost_usd = model_prices.luna6_usage_cost(
                self.prompt_tokens, self.completion_tokens, cached, 0,
                self.service_tier or "default", model=model)
        else:
            self.provider_cost_usd = model_prices.base_usage_cost(
                model, self.prompt_tokens, self.completion_tokens, cached=cached,
                fallback=model_prices.MODEL_PRICES["gpt-6.1-sol"])
        self.prompt_cache = {}


def _strip(messages):
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        m = {k: v for k, v in m.items() if k not in ("output_config", "cache_control")}
        if isinstance(m.get("content"), list):
            m["content"] = [{k: v for k, v in p.items() if k != "cache_control"}
                            if isinstance(p, dict) else p for p in m["content"]]
        out.append(m)
    return out


def _parts(content, role):
    if isinstance(content, str) or content is None:
        return content or ""
    out = []
    for p in content:
        if not isinstance(p, dict):
            out.append({"type": "input_text", "text": str(p)})
            continue
        kind = p.get("type")
        if kind in ("text", "input_text", "output_text"):
            out.append({"type": "output_text" if role == "assistant" else "input_text",
                        "text": p.get("text", "")})
        elif kind in ("image_url", "input_image"):
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if url and role != "assistant":
                out.append({"type": "input_image", "image_url": url, "detail": "auto"})
    return out


def responses_input(messages):
    """Chat messages -> Responses input items (system -> developer, tool calls and results as
    function_call / function_call_output, a tool result's images as the next user item)."""
    items = []
    for m in _strip(messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            c = m.get("content")
            images = []
            if isinstance(c, list):
                texts = [p.get("text", "") for p in c if isinstance(p, dict)
                         and p.get("type") in ("text", "input_text")]
                images = [p for p in c if isinstance(p, dict)
                          and p.get("type") in ("image_url", "input_image")]
                c = "\n".join(texts)
            items.append({"type": "function_call_output",
                          "call_id": m.get("tool_call_id"), "output": str(c or "")})
            if images:
                items.append({"role": "user", "content": _parts(images, "user")})
            continue
        if role == "assistant":
            c = m.get("content")
            if c:
                items.append({"role": "assistant",
                              "content": c if isinstance(c, str) else _parts(c, "assistant")})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append({"type": "function_call", "call_id": tc.get("id"),
                              "name": fn.get("name"), "arguments": fn.get("arguments") or "{}"})
            continue
        r = "developer" if role == "system" else (role or "user")
        items.append({"role": r, "content": _parts(m.get("content"), r)})
    return items


def responses_tools(tools):
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if fn:
            out.append({"type": "function", "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                        "strict": False})
    return out


_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}


def request_body(model, messages, tools=None, max_tokens=None, effort=None, tool_choice=None):
    body = {"model": model, "input": responses_input(messages), "store": False,
            "max_output_tokens": max(int(max_tokens or 8000), 8000)}
    rt = responses_tools(tools)
    if rt:
        body["tools"] = rt
        if tool_choice in ("auto", "required", "none"):
            body["tool_choice"] = tool_choice
        elif isinstance(tool_choice, dict) and (tool_choice.get("function") or {}).get("name"):
            body["tool_choice"] = {"type": "function", "name": tool_choice["function"]["name"]}
    e = _EFFORT.get(str(effort or "").lower())
    if e:
        body["reasoning"] = {"effort": e}
    key = _cache_key()
    if key:
        body["prompt_cache_key"] = key
    return body


def _completion(model, data):
    import claude_lane as cl
    texts, calls, refusal = [], [], None
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    texts.append(c["text"])
                elif c.get("type") == "refusal":
                    refusal = c.get("refusal")
        elif item.get("type") == "function_call":
            calls.append(cl._ToolCall(item.get("call_id"), item.get("name"),
                                      item.get("arguments") or "{}"))
    finish = "tool_calls" if calls else "stop"
    if data.get("status") == "incomplete":
        finish = "length"
    text = "\n".join(texts) or (refusal if refusal and not calls else None)
    if not text and not calls:
        raise RungFailed(f"{model} answered with nothing (status {data.get('status')})")
    out = cl._Message(text, calls or None, refusal)
    usage = _RespUsage(model, data.get("usage"))
    result = cl._Completion([cl._Choice(out, finish)], usage, model, data.get("id"), finish)
    result.provider = PROVIDER
    return result


def _post(body, timeout):
    import httpx
    url = str(config.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/") + "/responses"
    headers = {"Authorization": "Bearer " + str(config.OPENAI_API_KEY or ""),
               "Content-Type": "application/json"}
    attempt = 0
    while True:
        attempt += 1
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=float(timeout))
        except httpx.HTTPError as exc:
            if attempt <= config.EDIT_RUNG_RETRIES:
                time.sleep(1.5 * attempt)
                continue
            raise RungFailed(f"transport: {type(exc).__name__}: {str(exc)[:200]}") from exc
        if r.status_code < 400:
            try:
                return r.json()
            except ValueError as exc:
                raise RungFailed("unreadable body") from exc
        text = r.text[:400]
        if _wallet_refusal(r.status_code, text):
            raise RungFailed(f"OpenAI {r.status_code}: {text}", r.status_code, wallet=True)
        if r.status_code in (408, 409, 429, 500, 502, 503, 504) \
                and attempt <= config.EDIT_RUNG_RETRIES:
            time.sleep(1.5 * attempt)
            continue
        raise RungFailed(f"OpenAI {r.status_code}: {text}", r.status_code)


# ------------------------------------------------------------------ money

def allowance_usd(usage, model=None):
    """What the customer's credits and a trial's allowance are charged for this call: the same
    tokens at the fallback model's card (the price before the switch)."""
    import llm
    try:
        return float(llm.credit_parity_cost_usd(usage, fallback_model()))
    except Exception:  # noqa: BLE001
        return 0.0


def _meter(model, result):
    """A trial account's allowance at the fallback's card, off the Claude credit line; nothing
    on the paid Claude ledger (this call was paid from the OpenAI credit)."""
    import claude_trial
    if claude_trial.current() is not None:
        claude_trial.meter(model, allowance_usd(result.usage, model), kind="openai")


# ------------------------------------------------------------------ the call

def _alert(reason):
    try:
        import claude_trial
        claude_trial._alert(
            "edit-rung-closed-" + time.strftime("%Y%m%d%H"), "edit_rung",
            "OpenAI edits paused: Haiku is editing",
            "<p>The OpenAI edit rung refused a customer's call and is latched shut for "
            f"{int(config.EDIT_RUNG_LATCH_S // 60)} minutes; every edit runs on "
            f"{fallback_model()} meanwhile, with no failed turns.</p><p>Reason: "
            f"{str(reason)[:300]}</p>")
    except Exception:  # noqa: BLE001 - an alert never breaks a turn
        pass


def create(model, messages, tools=None, max_tokens=None, effort=None, tool_choice=None,
           timeout=None, fallback=None):
    """One call on the rung, or on the fallback when the rung cannot answer it.

    fallback(model) makes the same call on the Anthropic lane with that model. Raises only what
    the fallback raises."""
    import claude_trial
    why = None
    if turn_fell_back():
        why = "turn already on the fallback: " + str(turn_fell_back())
    elif not configured():
        why = "no OpenAI key"
    elif not available():
        why = "rung latched: " + closed_reason()
    if why is None:
        claude_trial.guard_call(model)
        body = request_body(model, messages, tools, max_tokens, effort, tool_choice)
        t0 = time.monotonic()
        try:
            data = _post(body, min(float(timeout or config.EDIT_RUNG_TIMEOUT_S),
                                   float(config.EDIT_RUNG_TIMEOUT_S)))
            result = _completion(model, data)
        except RungFailed as exc:
            why = str(exc)
            if exc.wallet:
                _close(why)
                _alert(why)
        except Exception as exc:  # noqa: BLE001 - every rung failure is the fallback's
            why = f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            result.usage.latency_s = round(time.monotonic() - t0, 2)
            result.usage.answered_model = model
            _meter(model, result)
            return result
        _mark_turn(why)
        print(f"[edit-rung] {model} -> {fallback_model()} for the rest of this turn: "
              f"{why[:240]}", flush=True)
    if fallback is None:
        raise RungFailed(why or "no fallback")
    return fallback(fallback_model())


def status():
    """For config reports: never a key."""
    return {"models": list(rung_models()), "fallback": fallback_model(),
            "configured": configured(), "open": available(),
            "closed_reason": closed_reason() or None}


__all__ = ["create", "is_rung_model", "rung_models", "fallback_model", "available", "reopen",
           "allowance_usd", "status", "turn_fell_back", "SimpleNamespace", "json"]
