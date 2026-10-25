"""The free-trial model lane: Claude through the Anthropic API.

Owner decision D2 (25 Oct 2026, strategy-1025/DECISIONS.md): trial usage runs
on Claude models through the Anthropic API (ANTHROPIC_API_KEY), funded by the
owner's Anthropic credit, never on the paid OpenAI wallet. Paying users keep
their contracted lanes.

The agent loop, the vision helpers and the critics speak the OpenAI
chat.completions shape. This module is one client object with that shape
(``client.chat.completions.create(**kwargs)`` and ``with_options``) whose
calls go through the official Anthropic SDK's Messages API:

- messages: the leading system messages become ``system``; later system
  messages that end the request stay a real mid-conversation system message,
  any other becomes a text note inside the adjacent user turn (always a valid
  position); ``tool`` results become ``tool_result`` blocks first in the user
  turn after the assistant's ``tool_use`` blocks; images become base64 or URL
  image blocks; consecutive same-role turns merge; the request always ends on
  a user turn (no prefill).
- tools: OpenAI function schemas -> ``name``/``description``/``input_schema``,
  ``tool_choice`` auto.
- thinking is DISABLED and depth is set with ``output_config.effort`` (low,
  medium or high; disabled thinking is accepted up to high). The loop edits
  earlier turns (it compacts old tool results and consumed frames), and a
  replayed thinking block after an edited prefix is a 400 on this model; with
  thinking off there is no block to replay.
- sampling parameters are never sent (they 400 on current Claude models).
- prompt caching: the stable tools + system prefix carries a breakpoint and
  the request carries top-level automatic caching for the growing turn.
- the response becomes ``choices[0].message`` (content, tool_calls), a
  ``finish_reason`` and an OpenAI-shaped ``usage`` that also keeps the
  Anthropic cache fields, so llm.provider_cost_usd prices it exactly.

The SDK is imported lazily: a worker without it (or without the key) simply
reports the lane unavailable.
"""
import base64
import json
import re
import threading
from types import SimpleNamespace

import config

BASE_URL = "https://api.anthropic.com"
_DATA_URI = re.compile(r"^data:([\w.+/-]+);base64,(.*)$", re.S)
_lock = threading.Lock()
_clients = {}


def available():
    """Is the trial lane configured (key + model + SDK importable)?"""
    if not (config.ANTHROPIC_API_KEY and config.TRIAL_AGENT_MODEL):
        return False
    try:
        import anthropic  # noqa: F401
    except Exception:
        return False
    return True


def _sdk(max_retries=None):
    key = "default" if max_retries is None else int(max_retries)
    with _lock:
        sdk = _clients.get(key)
        if sdk is None:
            import anthropic
            sdk = anthropic.Anthropic(
                api_key=config.ANTHROPIC_API_KEY,
                timeout=config.LLM_TIMEOUT_S,
                max_retries=(config.LLM_MAX_RETRIES if max_retries is None
                             else int(max_retries)))
            _clients[key] = sdk
        return sdk


# --------------------------------------------------------------------------
#  Request: OpenAI chat shape -> Anthropic Messages
# --------------------------------------------------------------------------

def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict)
                         and part.get("type") in ("text", "input_text"))
    return "" if content is None else str(content)


def _image_block(part):
    url = ((part.get("image_url") or {}).get("url")
           if isinstance(part.get("image_url"), dict)
           else part.get("image_url"))
    url = str(url or "")
    match = _DATA_URI.match(url)
    if match:
        media = match.group(1).lower()
        if media == "image/jpg":
            media = "image/jpeg"
        return {"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": match.group(2).strip()}}
    if url.startswith(("https://", "http://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _user_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text"):
            text = str(part.get("text") or "")
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif kind in ("image_url", "input_image"):
            block = _image_block(part)
            if block:
                blocks.append(block)
        elif kind == "input_audio":
            blocks.append({"type": "text",
                           "text": "[an audio clip was attached here; this "
                                   "model does not take audio]"})
    return blocks


def _tool_input(arguments):
    if isinstance(arguments, dict):
        return arguments
    try:
        value = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _push(out, role, blocks):
    """Append blocks to the conversation, merging same-role neighbours. In a
    user turn tool_result blocks always stay ahead of everything else."""
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        merged = out[-1]["content"] + blocks
    else:
        out.append({"role": role, "content": []})
        merged = list(blocks)
    if role == "user":
        merged = ([b for b in merged if b.get("type") == "tool_result"]
                  + [b for b in merged if b.get("type") != "tool_result"])
    out[-1]["content"] = merged


def _repair_tool_pairs(out):
    """Every tool_use is answered in the next user turn and every tool_result
    answers the assistant turn just before it; anything else is a 400."""
    fixed = []
    for index, message in enumerate(out):
        if message["role"] == "user":
            previous = fixed[-1] if fixed else None
            asked = ({b["id"] for b in previous["content"]
                      if b.get("type") == "tool_use"}
                     if previous and previous["role"] == "assistant" else set())
            content = []
            for block in message["content"]:
                if block.get("type") == "tool_result" \
                        and block.get("tool_use_id") not in asked:
                    text = _text_of(block.get("content"))
                    content.append({"type": "text", "text": (
                        "[earlier tool result] " + text) if text else
                        "[earlier tool result]"})
                else:
                    content.append(block)
            answered = {b.get("tool_use_id") for b in content
                        if b.get("type") == "tool_result"}
            missing = [{"type": "tool_result", "tool_use_id": tool_id,
                        "content": "(no result was recorded for this call)"}
                       for tool_id in sorted(asked - answered)]
            message = {"role": "user", "content": missing + content}
        fixed.append(message)
        if message["role"] == "assistant":
            pending = [b["id"] for b in message["content"]
                       if b.get("type") == "tool_use"]
            nxt = out[index + 1] if index + 1 < len(out) else None
            if pending and (nxt is None or nxt["role"] != "user"):
                fixed.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_id,
                     "content": "(no result was recorded for this call)"}
                    for tool_id in pending]})
    return fixed


def convert_messages(messages):
    """(system_text, anthropic_messages) for an OpenAI-shaped history."""
    system, out, leading = [], [], True
    for message in messages or []:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            text = _text_of(content).strip()
            if not text:
                continue
            if leading:
                system.append(text)
            else:
                _push(out, "user", [{"type": "text",
                                     "text": "[Valmera system note] " + text,
                                     "_system_text": text}])
            continue
        leading = False
        if role == "user":
            _push(out, "user", _user_blocks(content))
        elif role == "assistant":
            blocks = []
            text = _text_of(content).strip()
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                function = (call.get("function") if isinstance(call, dict)
                            else getattr(call, "function", None)) or {}
                get = (function.get if isinstance(function, dict)
                       else lambda k, d=None: getattr(function, k, d))
                call_id = (call.get("id") if isinstance(call, dict)
                           else getattr(call, "id", None))
                if not call_id or not get("name"):
                    continue
                blocks.append({"type": "tool_use", "id": str(call_id),
                               "name": str(get("name")),
                               "input": _tool_input(get("arguments"))})
            _push(out, "assistant", blocks)
        elif role == "tool":
            result = _text_of(content)
            _push(out, "user", [{"type": "tool_result",
                                 "tool_use_id": str(message.get("tool_call_id")),
                                 "content": result or "(empty result)"}])
    out = _repair_tool_pairs(out)
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [
            {"type": "text", "text": "(continuing this editing session)"}]})
    if out[-1]["role"] != "user":
        out.append({"role": "user", "content": [
            {"type": "text", "text": "Continue."}]})
    # Operator notes that END the request go back to a real mid-conversation
    # system message (valid as the last entry; the model treats it as the
    # operator rather than as words the user typed). Notes anywhere else stay
    # folded into their user turn, the one position that is always valid.
    trailing = []
    final = out[-1]["content"]
    while final and final[-1].get("_system_text") is not None \
            and len(final) > 1:
        trailing.insert(0, final.pop()["_system_text"])
    for message in out:
        for block in message["content"]:
            block.pop("_system_text", None)
    if trailing:
        out.append({"role": "system", "content": "\n\n".join(trailing)})
    return "\n\n".join(system), out


def convert_tools(tools):
    converted = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        schema = dict(function.get("parameters") or {})
        schema.setdefault("type", "object")
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        converted.append({"name": str(function["name"]),
                          "description": str(function.get("description")
                                             or "")[:4000],
                          "input_schema": schema})
    return converted


_EFFORT = {"none": "low", "minimal": "low", "low": "low", "medium": "medium",
           "high": "high", "xhigh": "high", "max": "high"}


def build_request(model, messages, tools=None, max_tokens=None,
                  max_completion_tokens=None, reasoning_effort=None,
                  **_ignored):
    """Anthropic Messages kwargs. temperature/top_p, service_tier, OpenAI
    extra headers and response formats are dropped on purpose."""
    system, converted = convert_messages(messages)
    request = {
        "model": model or config.TRIAL_AGENT_MODEL,
        "max_tokens": int(max_tokens or max_completion_tokens
                          or config.AGENT_MAX_TOKENS),
        "messages": converted,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": _EFFORT.get(
            str(reasoning_effort or config.TRIAL_AGENT_EFFORT).lower(),
            "medium")},
        "cache_control": {"type": "ephemeral"},
    }
    if system:
        request["system"] = [{"type": "text", "text": system,
                              "cache_control": {"type": "ephemeral"}}]
    tool_list = convert_tools(tools)
    if tool_list:
        request["tools"] = tool_list
        request["tool_choice"] = {"type": "auto"}
    return request


# --------------------------------------------------------------------------
#  Response: Anthropic Message -> OpenAI chat completion shape
# --------------------------------------------------------------------------

_FINISH = {"end_turn": "stop", "stop_sequence": "stop", "pause_turn": "stop",
           "tool_use": "tool_calls", "max_tokens": "length",
           "refusal": "content_filter"}


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def convert_response(message, model=None):
    texts, calls = [], []
    for block in _field(message, "content", None) or []:
        kind = _field(block, "type")
        if kind == "text":
            texts.append(str(_field(block, "text", "") or ""))
        elif kind == "tool_use":
            calls.append(SimpleNamespace(
                id=str(_field(block, "id")), type="function",
                function=SimpleNamespace(
                    name=str(_field(block, "name")),
                    arguments=json.dumps(_field(block, "input", {}) or {}))))
    stop = _field(message, "stop_reason")
    usage = _field(message, "usage", None)
    uncached = int(_field(usage, "input_tokens", 0) or 0)
    cache_read = int(_field(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(_field(usage, "cache_creation_input_tokens", 0) or 0)
    output = int(_field(usage, "output_tokens", 0) or 0)
    prompt = uncached + cache_read + cache_write
    text = "".join(texts).strip()
    refusal = None
    if stop == "refusal":
        details = _field(message, "stop_details", None)
        refusal = ("declined by the model's safety classifier"
                   + (f" ({_field(details, 'category')})"
                      if _field(details, "category") else ""))
    msg = SimpleNamespace(role="assistant", content=text or None,
                          tool_calls=calls or None, refusal=refusal)
    return SimpleNamespace(
        id=_field(message, "id"), model=model or _field(message, "model"),
        choices=[SimpleNamespace(index=0, message=msg,
                                 finish_reason=_FINISH.get(stop, "stop"))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=output,
            total_tokens=prompt + output,
            prompt_tokens_details={"cached_tokens": cache_read,
                                   "cache_write_tokens": cache_write},
            completion_tokens_details={"reasoning_tokens": 0},
            input_tokens=uncached, cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write, output_tokens=output,
            provider="anthropic"))


class _Completions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, model=None, messages=None, tools=None, **kwargs):
        request = build_request(model, messages, tools, **kwargs)
        sdk = self._owner._sdk_factory(self._owner._max_retries)
        reply = sdk.messages.create(**request)
        return convert_response(reply, model=request["model"])


class ClaudeChatClient:
    """chat.completions-shaped client for the trial lane."""

    def __init__(self, max_retries=None, sdk_factory=None):
        self._max_retries = max_retries
        self._sdk_factory = sdk_factory or _sdk
        self.chat = SimpleNamespace(completions=_Completions(self))

    def with_options(self, max_retries=None, **_ignored):
        return ClaudeChatClient(max_retries=max_retries,
                                sdk_factory=self._sdk_factory)


def client():
    return ClaudeChatClient()


def trial_lane():
    """The agent loop's lane dict for a trial turn, or None."""
    if not available():
        return None
    return {"name": "trial_claude", "client": client(),
            "model": config.TRIAL_AGENT_MODEL, "base_url": BASE_URL,
            "api_key": config.ANTHROPIC_API_KEY}


def usage_cost_usd(usage, model):
    """Exact Anthropic cost of one call from its usage (cache reads 0.1x,
    5-minute cache writes 1.25x of the input rate; the long-prompt rate card
    applies to the whole call above the model's threshold)."""
    import model_prices
    price = model_prices.MODEL_PRICES.get(model_prices.normalize(model))
    if not price or usage is None:
        return None
    uncached = int(_field(usage, "input_tokens", 0) or 0)
    cache_read = int(_field(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(_field(usage, "cache_creation_input_tokens", 0) or 0)
    output = int(_field(usage, "output_tokens", 0) or 0)
    prompt = uncached + cache_read + cache_write
    scale = model_prices.context_multiplier(model, prompt)
    cost = (uncached * price["in"] + cache_read * price["cached_in"]
            + cache_write * price.get("cache_write_in", price["in"] * 1.25)
            + output * price["out"]) / 1e6
    return round(cost * scale, 8)


def ask_text(system, user, max_tokens=1200, effort="low"):
    """One plain Claude call (the First cut's long-source scout). Returns
    (text, usage) or raises."""
    request = build_request(
        config.TRIAL_AGENT_MODEL,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        max_tokens=max_tokens, reasoning_effort=effort)
    request.pop("cache_control", None)
    reply = _sdk().messages.create(**request)
    converted = convert_response(reply, model=request["model"])
    return (converted.choices[0].message.content or ""), converted.usage


def b64_image_part(path, media_type="image/jpeg"):
    with open(path, "rb") as source:
        data = base64.standard_b64encode(source.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"}}
