"""One model call for a workflow stage, with its dollars recorded.

Two rungs answer the same prompts:
  openai     the Responses API (gpt-6.1-sol). The default since 14 Nov 2026: the owner's OpenAI credit pays for
             workflow calls, and the Anthropic wallet is kept for customers.
  anthropic  Claude, when SHELL_LLM=anthropic and the run is under .valmera/claude_trial_env.sh.

The key comes from this process's environment; the OpenAI key is read from the backend's .env into this one
process when the environment has none. Nothing here prints or stores a key.
"""
import json
import os
import time
import openai_cache

MODEL = "claude-opus-5-5"
OA_MODEL = os.environ.get("SHELL_OPENAI_MODEL", "gpt-6.1-sol")
ENV_FILE = "/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env"
# USD per token: input, cache write, cache read, output (slice/direct.py's card)
PRICES = {"claude-opus-5-5": (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6),
          "claude-haiku-5-5": (0.10e-6, 0.125e-6, 0.01e-6, 0.50e-6)}
# USD per token: input, cached input, output (worker/model_prices.py)
OA_PRICES = {"gpt-6.1-sol": (2.0e-6, 0.1e-6, 10.0e-6), "gpt-6-sol": (2.0e-6, 0.2e-6, 10.0e-6), "gpt-5.5": (5.0e-6, 0.5e-6, 30.0e-6),
             "gpt-6-luna": (0.10e-6, 0.01e-6, 0.50e-6)}
OA_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}


def provider():
    p = os.environ.get("SHELL_LLM")
    if p in ("openai", "anthropic"):
        return p
    return "openai"


def openai_key():
    """The key for this process: its own environment first, else the backend's .env. Never printed."""
    k = os.environ.get("OPENAI_API_KEY")
    if not k and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith("OPENAI_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
        if k:
            os.environ["OPENAI_API_KEY"] = k
    if not k:
        raise RuntimeError("no OPENAI_API_KEY in the environment or the backend's .env")
    return k


def cost(model, u):
    pin, pw, pr, pout = PRICES.get(model, PRICES[MODEL])
    return (u.input_tokens * pin + (u.cache_creation_input_tokens or 0) * pw
            + (u.cache_read_input_tokens or 0) * pr + u.output_tokens * pout)


def _parts(content):
    """Text, or Claude-shaped blocks (text, base64 image), as Responses input parts."""
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


def ask_openai(system, content, *, model=None, effort="medium", max_tokens=16000, timeout=1800):
    """(text, usage record): one streamed Responses call, retried on a busy or broken line."""
    import httpx
    model = model or OA_MODEL
    # reasoning shares the output budget: a small answer still needs room to think
    body = {"model": model, "instructions": system, "input": [{"role": "user", "content": _parts(content)}],
            "max_output_tokens": int(max(max_tokens, 16000)), "reasoning": {"effort": OA_EFFORT.get(effort, "medium")}, "stream": True, "store": False}
    purpose = openai_cache.role_for(system)
    body = openai_cache.responses(body, purpose)
    headers = {"Authorization": "Bearer " + openai_key(), "Content-Type": "application/json"}
    t0, final = time.time(), None
    for attempt in range(4):
        final = None
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=30)) as cl, \
                    cl.stream("POST", "https://api.openai.com/v1/responses", headers=headers, json=body) as r:
                if r.status_code >= 400:
                    openai_cache.record(model, purpose, status="http_error", usd=0.0,
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
            openai_cache.record(model, purpose, status="stream_without_usage", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
        except (httpx.TransportError, httpx.TimeoutException):
            if final is not None:  # terminal usage arrived before the connection closed
                break
            openai_cache.record(model, purpose, status="transport_error", billing_unconfirmed=True,
                                attempt=attempt + 1, force=True)
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    if final is None:
        raise RuntimeError("openai: the stream ended without a response")
    openai_cache.record(model, purpose, final.get("usage"), request_id=final.get("id"),
                        status=final.get("status"), force=True)
    if final.get("status") == "failed":
        raise RuntimeError(f"openai failed: {json.dumps(final.get('error'))[:300]}")
    text = "".join(c.get("text", "") for item in final.get("output") or [] if item.get("type") == "message"
                   for c in item.get("content") or [] if c.get("type") == "output_text")
    u = final.get("usage") or {}
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    cached = int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    pin, pc, pout = OA_PRICES.get(model, OA_PRICES["gpt-5.5"])
    usd = openai_cache.cost(model, u)
    return text, dict(provider="openai", model=model, effort=effort, input=tin, cache_write=openai_cache.tokens(u)["cache_write_tokens"], cache_read=cached, output=tout,
                      usd=round(usd, 5), seconds=round(time.time() - t0, 1), stop=final.get("status"),
                      incomplete=(final.get("incomplete_details") or {}).get("reason"))


def ask_anthropic(system, content, *, model=MODEL, effort="medium", max_tokens=16000):
    """(text, usage record). Streams, thinking adaptive, no temperature."""
    import anthropic
    c = anthropic.Anthropic()
    t0 = time.time()
    with c.messages.stream(model=model, max_tokens=max_tokens, thinking={"type": "adaptive"},
                           output_config={"effort": effort}, system=system,
                           messages=[{"role": "user", "content": content}]) as st:
        text = "".join(st.text_stream)
        final = st.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("the model refused this stage")
    u = final.usage
    rec = dict(provider="anthropic", model=model, effort=effort, input=u.input_tokens,
               cache_write=u.cache_creation_input_tokens or 0, cache_read=u.cache_read_input_tokens or 0,
               output=u.output_tokens, usd=round(cost(model, u), 5), seconds=round(time.time() - t0, 1),
               stop=final.stop_reason)
    return text, rec


def ask(system, content, *, model=None, effort="medium", max_tokens=16000):
    """(text, usage record) from the run's provider. A Claude model id goes to Claude only on the anthropic rung;
    on the openai rung every call is the OpenAI model."""
    if provider() == "anthropic":
        return ask_anthropic(system, content, model=model or MODEL, effort=effort, max_tokens=max_tokens)
    oa = model if str(model or "").startswith("gpt-") else None
    return ask_openai(system, content, model=oa, effort=effort, max_tokens=max_tokens)


def json_block(text):
    """The model's JSON answer: the outermost object or array in its text (machine syntax only)."""
    s = text.strip()
    for o, c in (("{", "}"), ("[", "]")):
        i, j = s.find(o), s.rfind(c)
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no JSON in the model's answer")
