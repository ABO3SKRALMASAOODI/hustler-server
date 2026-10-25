"""The Claude lane: Anthropic's Messages API behind an OpenAI-shaped client.

Owner decision D2 (25 Oct 2026) funds a free trial from the Anthropic credit
that comes with the owner's subscription. The in-app agent, its vision and
its critics all speak OpenAI chat-completions shapes (``messages`` with
``tool_calls`` / ``role: tool``, ``choices[0].message``, ``usage``). Rather
than fork the 5,000-line loop, this module gives it a client object whose
``chat.completions.create`` sends the SAME conversation, tools and images to
Claude and hands back a chat-shaped completion. Everything downstream (tool
execution, verification, honesty checks, recording, credit maths) is
untouched, which is what keeps the trial's safety and verification identical
to the paid lane.

Translation rules (all deterministic, so the prompt cache holds):

* Leading ``system`` messages become the top-level ``system`` blocks. A
  breakpoint sits on the stable editor kernel and another on the last
  leading block, so tools + kernel + project context are cached; a moving
  breakpoint on the last block caches the growing conversation.
* Later ``system`` messages stay ``role: system`` where the API allows it
  (after a user turn, and last or followed by an assistant turn); anywhere
  else they become a clearly marked text block in the neighbouring user turn.
* ``assistant.tool_calls`` -> ``tool_use`` blocks; ``role: tool`` ->
  ``tool_result`` blocks at the front of the next user turn. Orphans on
  either side (history compaction can leave them) are repaired, never sent.
* ``image_url`` parts (data URLs or https) -> ``image`` blocks.
* Thinking blocks are never replayed: the loop edits history (compaction,
  filmstrip release), and a replayed block after an edit is a 400 on
  accounts enforced for preserved thinking. Each step thinks afresh.

Errors: 429s propagate unchanged so the loop's budget-aware rate-limit waits
run. Overloaded/5xx refusals (nothing was billed) are retried here, twice.
Billing, credit and authentication refusals LATCH the lane closed for
``CLAUDE_TRIAL_OUTAGE_LATCH_S`` and alert the admin, and are re-raised with
the provider's words so ``llm.is_quota_error`` recognises them.

The key comes only from ``config.ANTHROPIC_API_KEY``. It is never logged,
never returned and never placed in a recorded payload.
"""

import json
import threading
import time
from types import SimpleNamespace

import config
import model_prices

try:  # The SDK is pinned in worker/requirements.txt. Missing = lane closed.
    import anthropic
except Exception:  # pragma: no cover - exercised only on a broken image
    anthropic = None


PROVIDER = "anthropic"
BASE_URL = "https://api.anthropic.com"

# Models that accept a mid-conversation ``role: system`` message (Claude API
# docs, 25 Oct 2026). Claude Sonnet 5 does not; everything else here does.
_MID_SYSTEM_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5",
                      "claude-opus-5-5", "claude-opus-5", "claude-fable-5")
# Forced tool_choice ("any"/"tool") is a 400 on these.
_NO_FORCED_TOOL = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1")

_SYSTEM_NOTE = "[Valmera system note — operator instruction, not user text]\n"

_lock = threading.Lock()
_client = None
_closed_until = 0.0
_closed_reason = ""


def is_claude_model(model):
    return str(model or "").strip().lower().startswith("claude-")


def configured():
    """Key present and SDK importable (says nothing about the balance)."""
    return bool(config.ANTHROPIC_API_KEY) and anthropic is not None


def available():
    """Configured and not latched closed by a billing/auth refusal."""
    return configured() and time.monotonic() >= _closed_until


def closed_reason():
    return _closed_reason if time.monotonic() < _closed_until else ""


def _close(reason, seconds=None):
    global _closed_until, _closed_reason
    _closed_until = time.monotonic() + float(
        config.CLAUDE_TRIAL_OUTAGE_LATCH_S if seconds is None else seconds)
    _closed_reason = str(reason)[:300]


def reopen():
    """Tests/operators: clear the outage latch."""
    global _closed_until, _closed_reason
    _closed_until, _closed_reason = 0.0, ""


def _sdk():
    global _client
    with _lock:
        if _client is None:
            kw = {"api_key": config.ANTHROPIC_API_KEY, "max_retries": 0,
                  "timeout": config.CLAUDE_TRIAL_TIMEOUT_S}
            if config.ANTHROPIC_BASE_URL:
                kw["base_url"] = config.ANTHROPIC_BASE_URL
            _client = anthropic.Anthropic(**kw)
        return _client


# ------------------------------------------------------------------ #
#  Error classification                                                #
# ------------------------------------------------------------------ #

_BILLING_MARKERS = ("credit balance", "billing_error", "billing", "purchase "
                    "credits", "plans & billing", "spend limit",
                    "usage limit", "quota")


def _status(exc):
    return getattr(exc, "status_code", None)


def is_billing_error(exc):
    """The Anthropic account cannot pay (empty balance, spend limit)."""
    text = str(exc).lower()
    return _status(exc) == 402 or (
        _status(exc) in (400, 403) and any(m in text for m in _BILLING_MARKERS))


def is_auth_error(exc):
    return _status(exc) in (401, 403) and not is_billing_error(exc)


def _retryable(exc):
    """A refusal the server answered (so nothing was billed) and may lift."""
    if _status(exc) == 429:
        return False             # the loop owns rate-limit waits
    return _status(exc) in (500, 502, 503, 504, 529) or (
        anthropic is not None and isinstance(
            exc, getattr(anthropic, "OverloadedError", ()) or ()))


class ClaudeLaneUnavailable(RuntimeError):
    """The lane is closed. Message words match llm.is_quota_error markers
    ("credit balance") so every caller treats it as a wallet problem."""


# ------------------------------------------------------------------ #
#  Request translation                                                 #
# ------------------------------------------------------------------ #

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)


def _image_block(part):
    url = (part.get("image_url") or {}).get("url") \
        if isinstance(part.get("image_url"), dict) else part.get("image_url")
    url = str(url or "")
    if url.startswith("data:") and ";base64," in url:
        head, data = url.split(",", 1)
        media = head[5:].split(";", 1)[0] or "image/jpeg"
        return {"type": "image", "source": {
            "type": "base64", "media_type": media, "data": data}}
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _blocks(content):
    """User/tool content -> Anthropic content blocks (text + images)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    out = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text" and str(part.get("text") or "").strip():
            out.append({"type": "text", "text": str(part["text"])})
        elif kind == "image_url":
            block = _image_block(part)
            if block:
                out.append(block)
        elif kind == "image" and part.get("source"):
            out.append({"type": "image", "source": part["source"]})
    return out


def _tool_input(arguments):
    if isinstance(arguments, dict):
        return arguments
    try:
        value = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {"_unparsed_arguments": str(arguments)[:4000]}
    return value if isinstance(value, dict) else {"value": value}


def _append(out, role, blocks):
    if not blocks:
        return
    if out and out[-1]["role"] == role and role != "system":
        out[-1]["content"].extend(blocks)
    else:
        out.append({"role": role, "content": list(blocks)})


def _repair_tool_pairs(out):
    """Every tool_use answered in the very next user turn; no orphans."""
    fixed = []
    for i, msg in enumerate(out):
        if msg["role"] == "user":
            prev = fixed[-1] if fixed else None
            wanted = set()
            if prev and prev["role"] == "assistant":
                wanted = {b["id"] for b in prev["content"]
                          if b.get("type") == "tool_use"}
            results, rest = [], []
            for b in msg["content"]:
                if b.get("type") == "tool_result":
                    if b.get("tool_use_id") in wanted:
                        results.append(b)
                        wanted.discard(b["tool_use_id"])
                    else:
                        body = b.get("content")
                        rest.append({"type": "text", "text": (
                            "Earlier tool result: " + (_text(body) if
                            isinstance(body, list) else str(body or "")))[:20000]})
                else:
                    rest.append(b)
            for missing in sorted(wanted):
                results.append({"type": "tool_result", "tool_use_id": missing,
                                "content": "(no result was recorded for this "
                                           "call)", "is_error": True})
            msg = {"role": "user", "content": results + rest}
            if not msg["content"]:
                continue
        elif msg["role"] == "assistant" and fixed and \
                fixed[-1]["role"] == "assistant":
            pass
        fixed.append(msg)
        # An assistant tool_use followed by no user turn at all (end of list
        # or a system note) still needs its results.
        nxt = out[i + 1] if i + 1 < len(out) else None
        if msg["role"] == "assistant" and (nxt is None or nxt["role"] != "user"):
            ids = [b["id"] for b in msg["content"] if b.get("type") == "tool_use"]
            if ids:
                fixed.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": t, "is_error": True,
                     "content": "(no result was recorded for this call)"}
                    for t in ids]})
    return fixed


def _place_system_notes(out, model):
    """Keep mid-conversation system notes where the API accepts them."""
    allowed = str(model or "").lower().startswith(_MID_SYSTEM_MODELS)
    merged = []
    for msg in out:                      # collapse adjacent notes first
        if msg["role"] == "system" and merged and merged[-1]["role"] == "system":
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(dict(msg))
    final = []
    for i, msg in enumerate(merged):
        if msg["role"] != "system":
            final.append(msg)
            continue
        prev = final[-1] if final else None
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if allowed and prev is not None and prev["role"] == "user" and (
                nxt is None or nxt["role"] == "assistant"):
            final.append(msg)
            continue
        note = {"type": "text", "text": _SYSTEM_NOTE + msg["content"]}
        if prev is not None and prev["role"] == "user":
            prev["content"].append(note)
        elif nxt is not None and nxt["role"] == "user":
            nxt["content"].insert(0, note)
        else:
            final.append({"role": "user", "content": [note]})
    # Re-merge any user turns the conversion made adjacent.
    clean = []
    for msg in final:
        if clean and clean[-1]["role"] == msg["role"] == "user":
            clean[-1]["content"].extend(msg["content"])
        else:
            clean.append(msg)
    return clean


def to_anthropic(messages, tools=None, model=None):
    """chat-completions ``messages``/``tools`` -> (system, messages, tools)."""
    system, out = [], []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        text = _text(messages[i].get("content"))
        if text.strip():
            system.append({"type": "text", "text": text})
        i += 1
    for m in messages[i:]:
        role = m.get("role")
        if role == "tool":
            body = m.get("content")
            content = (_blocks(body) if isinstance(body, list)
                       else str(body if body is not None else ""))
            if not content:
                content = "(empty result)"
            _append(out, "user", [{"type": "tool_result",
                                   "tool_use_id": m.get("tool_call_id"),
                                   "content": content}])
        elif role == "assistant":
            blocks = []
            text = _text(m.get("content"))
            if text.strip():
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = (tc.get("function") if isinstance(tc, dict)
                      else getattr(tc, "function", None)) or {}
                name = fn.get("name") if isinstance(fn, dict) \
                    else getattr(fn, "name", None)
                args = fn.get("arguments") if isinstance(fn, dict) \
                    else getattr(fn, "arguments", None)
                call_id = tc.get("id") if isinstance(tc, dict) \
                    else getattr(tc, "id", None)
                if name and call_id:
                    blocks.append({"type": "tool_use", "id": str(call_id),
                                   "name": str(name),
                                   "input": _tool_input(args)})
            _append(out, "assistant", blocks)
        elif role == "system":
            text = _text(m.get("content"))
            if text.strip():
                out.append({"role": "system", "content": text})
        else:
            _append(out, "user", _blocks(m.get("content")))
    out = _place_system_notes(out, model)
    out = _repair_tool_pairs(out)
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [
            {"type": "text", "text": "(The conversation so far follows.)"}]})
    if out[-1]["role"] == "assistant":
        # No prefill on Claude 5.x: a trailing assistant turn is a 400.
        out.append({"role": "user", "content": [
            {"type": "text", "text": "Continue."}]})
    tool_defs = []
    for t in tools or []:
        fn = t.get("function") or t
        if not fn.get("name"):
            continue
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        tool_defs.append({"name": fn["name"],
                          "description": str(fn.get("description") or "")[:10000],
                          "input_schema": schema})
    return system, out, tool_defs


def _mark_cache(system, msgs, tools):
    """Up to four ephemeral breakpoints at the stability boundaries."""
    if tools:
        tools[-1] = dict(tools[-1], cache_control={"type": "ephemeral"})
    if system:
        # The editor kernel + capability directory are identical for every
        # project; one breakpoint after them is a cross-account read point,
        # the last leading block caches this project's context.
        stable = min(1, len(system) - 1)
        system[stable] = dict(system[stable],
                              cache_control={"type": "ephemeral"})
        if len(system) - 1 != stable:
            system[-1] = dict(system[-1], cache_control={"type": "ephemeral"})
    for msg in reversed(msgs):
        if msg["role"] == "system":
            continue             # a mid-conversation note cannot carry one
        content = msg["content"]
        if content:
            content[-1] = dict(content[-1], cache_control={"type": "ephemeral"})
        break


def _effort(kw):
    value = kw.get("reasoning_effort")
    value = str(value or "").strip().lower()
    if value in ("low", "medium", "high", "xhigh", "max"):
        return value
    if value in ("none", "minimal"):
        return "low"
    return None


def _tool_choice(choice, model):
    if choice in (None, "", "auto"):
        return None
    if choice == "none":
        return {"type": "none"}
    forced_ok = not str(model or "").lower().startswith(_NO_FORCED_TOOL)
    if choice == "required":
        return {"type": "any"} if forced_ok else None
    if isinstance(choice, dict):
        name = ((choice.get("function") or {}).get("name")
                if choice.get("type") == "function" else None)
        if name and forced_ok:
            return {"type": "tool", "name": name}
    return None


# ------------------------------------------------------------------ #
#  Response translation                                                #
# ------------------------------------------------------------------ #

class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content, tool_calls, refusal=None):
        self.content = content
        self.tool_calls = tool_calls
        self.refusal = refusal
        self.role = "assistant"


class _Choice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason
        self.index = 0


class _Usage:
    """Chat-shaped usage. prompt_tokens is the WHOLE prompt (Anthropic's three
    input categories are disjoint), cached_tokens the cache-read slice, so
    llm.cached_input_tokens and the credit maths read it unchanged."""

    def __init__(self, model, raw):
        get = (lambda k: getattr(raw, k, None) or 0)
        self.input_tokens = int(get("input_tokens"))
        self.cache_read_input_tokens = int(get("cache_read_input_tokens"))
        self.cache_creation_input_tokens = int(
            get("cache_creation_input_tokens"))
        self.output_tokens = int(get("output_tokens"))
        self.prompt_tokens = (self.input_tokens + self.cache_read_input_tokens
                              + self.cache_creation_input_tokens)
        self.completion_tokens = self.output_tokens
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        self.prompt_tokens_details = {
            "cached_tokens": self.cache_read_input_tokens,
            "cache_write_tokens": self.cache_creation_input_tokens}
        self.completion_tokens_details = {}
        self.provider_cost_usd = model_prices.claude_usage_cost(
            model, self.input_tokens, self.output_tokens,
            self.cache_read_input_tokens, self.cache_creation_input_tokens)
        self.service_tier = getattr(raw, "service_tier", None)


class _Completion:
    def __init__(self, choices, usage, model, request_id=None,
                 stop_reason=None):
        self.choices = choices
        self.usage = usage
        self.model = model
        self.provider = PROVIDER
        self.request_id = request_id
        self.stop_reason = stop_reason


_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "pause_turn": "stop",
           "tool_use": "tool_calls", "max_tokens": "length",
           "model_context_window_exceeded": "length",
           "refusal": "content_filter"}


def from_anthropic(message, model):
    texts, calls = [], []
    for block in getattr(message, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "text" and getattr(block, "text", ""):
            texts.append(block.text)
        elif kind == "tool_use":
            calls.append(_ToolCall(
                block.id, block.name,
                json.dumps(getattr(block, "input", None) or {},
                           ensure_ascii=False, separators=(",", ":"))))
    stop = getattr(message, "stop_reason", None)
    refusal = None
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        refusal = (getattr(details, "explanation", None)
                   or "The model declined this request.")
    text = "\n".join(texts) or None
    if refusal and not text and not calls:
        text = refusal
    msg = _Message(text, calls or None, refusal)
    usage = _Usage(model, getattr(message, "usage", None))
    return _Completion([_Choice(msg, _FINISH.get(stop, "stop"))], usage,
                       model, getattr(message, "_request_id", None), stop)


# ------------------------------------------------------------------ #
#  The client                                                          #
# ------------------------------------------------------------------ #

def create(model, messages, tools=None, max_tokens=None, effort=None,
           tool_choice=None, timeout=None):
    """One Messages call in chat-completions shape. Raises on any failure."""
    if not configured():
        raise ClaudeLaneUnavailable(
            "Claude lane is not configured (no ANTHROPIC_API_KEY): no credit "
            "balance can be reached")
    if not available():
        raise ClaudeLaneUnavailable(
            "Claude lane is closed after a provider refusal (credit balance "
            "or key): " + closed_reason())
    import claude_trial            # metering + hard per-account ceiling
    claude_trial.guard_call()
    system, msgs, tool_defs = to_anthropic(messages, tools, model)
    _mark_cache(system, msgs, tool_defs)
    body = {"model": model, "max_tokens": int(max_tokens or 8000),
            "messages": msgs}
    if system:
        body["system"] = system
    if tool_defs:
        body["tools"] = tool_defs
        choice = _tool_choice(tool_choice, model)
        if choice:
            body["tool_choice"] = choice
    if effort:
        body["output_config"] = {"effort": effort}
    client = _sdk()
    if timeout:
        client = client.with_options(timeout=float(timeout))
    attempt = 0
    while True:
        attempt += 1
        try:
            with client.messages.stream(**body) as stream:
                message = stream.get_final_message()
            break
        except Exception as exc:          # noqa: BLE001 - classified below
            if is_billing_error(exc) or is_auth_error(exc):
                kind = "billing" if is_billing_error(exc) else "auth"
                _close(f"{kind}: {str(exc)[:200]}")
                claude_trial.provider_refused(kind, exc)
                raise ClaudeLaneUnavailable(
                    f"Anthropic refused the trial lane ({kind}; credit "
                    f"balance or key): {str(exc)[:300]}") from exc
            if _retryable(exc) and attempt <= 2:
                time.sleep(2.0 * attempt)
                continue
            raise
    result = from_anthropic(message, model)
    if claude_trial.current() is not None:
        claude_trial.meter(model, result.usage.provider_cost_usd)
    else:
        # A subscriber's Studio agent on the Claude lane (claude_plans): the
        # call is metered into the credit cycle's ledger, which the automatic
        # credit switch reads (the user's own credits are charged as usual).
        claude_trial.meter_paid(model, result.usage.provider_cost_usd)
    return result


class _Completions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, model=None, messages=None, tools=None, **kw):
        max_tokens = kw.get("max_tokens") or kw.get("max_completion_tokens")
        return create(model, messages or [], tools=tools,
                      max_tokens=max_tokens,
                      effort=self._owner.effort or _effort(kw),
                      tool_choice=kw.get("tool_choice"),
                      timeout=kw.get("timeout") or self._owner.timeout)


class ClaudeLaneClient:
    """Duck-types the OpenAI client surface the worker uses:
    ``.chat.completions.create(model=, messages=, tools=, **kw)`` and
    ``.with_options(timeout=, max_retries=)``. Holds no key."""

    is_claude_lane = True
    base_url = BASE_URL

    def __init__(self, timeout=None, effort=None):
        self.timeout = timeout
        self.effort = effort
        self.chat = SimpleNamespace(completions=_Completions(self))

    def with_options(self, timeout=None, max_retries=None, **_ignored):
        return ClaudeLaneClient(timeout=timeout or self.timeout,
                                effort=self.effort)

    def with_effort(self, effort):
        return ClaudeLaneClient(timeout=self.timeout, effort=effort)


def lane(model=None, name="claude_trial"):
    """The agent-loop lane dict for this provider (no key inside)."""
    return {"name": name, "provider": PROVIDER,
            "client": ClaudeLaneClient(),
            "model": model or config.CLAUDE_TRIAL_MODEL,
            "base_url": BASE_URL, "api_key": "anthropic"}
