"""The OpenAI rung of score.call.stream (lane Q, 13 Nov 2026): one streamed /v1/responses call for a film,
fenced, deadlined and metered like the Claude call it stands in for, returning the same Result.

It is reached only through call.stream's seam, for a model id that is not a Claude one. Production's
switch (flags.parse) admits an OpenAI model for the Director only with the row limited to its
test_user_ids, and replays pass their own models; no customer row names one, so customers are unchanged.

Content arrives in the Claude shape the Director builds (text blocks and base64 image blocks, a system
string or a list of text blocks) and is sent as Responses input parts. The deadline is hard, as in
call.py: the read runs on its own thread and is closed when time is up, and the tokens a closed stream
already bought are estimated and metered.
"""
import json
import threading
import time

import config
import llm

OUT_TOKENS_PER_S = 130.0
URL = "https://api.openai.com/v1/responses"
# Claude effort words to the reasoning efforts every OpenAI model here accepts (gpt-5.5 has no "max").
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}


def is_openai(model):
    return str(model or "").lower().startswith("gpt-")


class Usage:
    """One call's tokens under both spellings the callers read (OpenAI's and Claude's), and its dollars."""

    def __init__(self, model, raw=None, out_tokens=None):
        raw = raw or {}
        details = raw.get("input_tokens_details") or {}
        self.model = model
        self.prompt_tokens = self.total_in = int(raw.get("input_tokens") or 0)
        cached = int(details.get("cached_tokens") or 0)
        self.prompt_tokens_details = {"cached_tokens": cached}
        self.completion_tokens = int(out_tokens if out_tokens is not None else raw.get("output_tokens") or 0)
        self.input_tokens = max(0, self.prompt_tokens - cached)
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = 0
        self.output_tokens = self.completion_tokens
        self.service_tier = raw.get("service_tier")
        self.provider_cost_usd = cost(model, self.prompt_tokens, cached, self.completion_tokens)


def cost(model, tokens_in, cached, tokens_out):
    """USD from model_prices (the source of truth); an unpriced model costs the dearest listed OpenAI rate,
    never zero."""
    import model_prices
    p = model_prices.MODEL_PRICES.get(str(model).lower()) or model_prices.MODEL_PRICES["gpt-5.5"]
    cached = min(max(cached, 0), tokens_in)
    return ((tokens_in - cached) * p["in"] + cached * p.get("cached_in", p["in"]) + tokens_out * p["out"]) / 1e6


def _text(block):
    return block.get("text", "") if isinstance(block, dict) else str(block)


def instructions(system):
    if isinstance(system, (list, tuple)):
        return "\n\n".join(_text(b) for b in system)
    return str(system or "")


def parts(content):
    """Claude content blocks to Responses input parts."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out = []
    for b in content or []:
        if not isinstance(b, dict):
            out.append({"type": "input_text", "text": str(b)})
        elif b.get("type") == "image":
            src = b.get("source") or {}
            url = src.get("url") if src.get("type") == "url" else \
                f"data:{src.get('media_type') or 'image/jpeg'};base64,{src.get('data', '')}"
            out.append({"type": "input_image", "image_url": url, "detail": "high"})
        else:
            out.append({"type": "input_text", "text": _text(b)})
    return out


def body(model, system, content, max_tokens, effort, purpose):
    return {"model": model, "instructions": instructions(system), "input": [{"role": "user", "content": parts(content)}],
            "max_output_tokens": int(max_tokens), "reasoning": {"effort": EFFORT.get(effort, "low")},
            "stream": True, "store": False, "prompt_cache_key": f"valmera-{purpose}"[:64]}


def events(lines):
    """Server-sent events to (type, data) pairs."""
    for line in lines:
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw and raw != "[DONE]":
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                yield d.get("type", ""), d


def stream(model, system, content, *, max_tokens, effort, purpose, deadline, on_text=None, thinking=True, first_text_by=None):
    """call.stream for an OpenAI model. Returns call.Result; raises call.Late, call.Refused or the error."""
    import httpx
    from . import call
    key = config.OPENAI_API_KEY
    if not key or "api.openai.com" not in str(config.OPENAI_BASE_URL):
        raise call.Refused("the OpenAI rung has no OpenAI key")
    llm.fence(model)
    req = body(model, system, content, max_tokens, effort, purpose)
    box = {}
    t0 = time.monotonic()

    def read():
        try:
            with httpx.Client(timeout=httpx.Timeout(max(5.0, deadline - t0 + 5.0), connect=10.0)) as client:
                with client.stream("POST", URL, json=req, headers={"Authorization": f"Bearer {key}"}) as r:
                    box["resp"] = r
                    if r.status_code != 200:
                        box["error"] = RuntimeError(f"OpenAI HTTP {r.status_code}: {r.read().decode(errors='replace')[:300]}")
                        return
                    for kind, d in events(r.iter_lines()):
                        if box.get("cancel"):
                            return
                        if kind == "response.output_text.delta":
                            if "first" not in box:
                                box["first"] = time.monotonic() - t0
                            box.setdefault("text", []).append(d.get("delta", ""))
                            if on_text:
                                on_text(d.get("delta", ""))
                        elif kind == "response.refusal.delta":
                            box["refused"] = True
                        elif kind in ("response.completed", "response.incomplete", "response.failed"):
                            box["final"] = d.get("response") or {}
                        elif kind == "error":
                            box["error"] = RuntimeError(f"OpenAI stream error: {str(d)[:300]}")
        except BaseException as exc:  # noqa: BLE001 - carried to the caller's thread
            box["error"] = exc
    th = threading.Thread(target=read, name=f"film-{purpose}", daemon=True)
    th.start()
    if first_text_by is not None and first_text_by < deadline:
        while th.is_alive() and "first" not in box and time.monotonic() < first_text_by:
            th.join(min(0.5, max(0.0, first_text_by - time.monotonic())))
        if th.is_alive() and "first" not in box:
            deadline = time.monotonic()
    th.join(max(0.0, deadline - time.monotonic()))
    if th.is_alive():
        box["cancel"] = True
        elapsed = time.monotonic() - t0
        r = box.get("resp")
        if r is not None:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
        usage = Usage(model, {}, out_tokens=int(elapsed * OUT_TOKENS_PER_S))
        llm.record(purpose, {"model": model, "max_tokens": max_tokens}, {"late": True, "provider_cost_usd": usage.provider_cost_usd}, usage)
        call._meter(model, usage.provider_cost_usd)
        raise call.Late(f"{purpose} past its deadline after {elapsed:.0f} s")
    if box.get("error") is not None:
        raise box["error"]
    final = box.get("final") or {}
    usage = Usage(model, final.get("usage") or {})
    usd = float(usage.provider_cost_usd)
    status = final.get("status") or "completed"
    stop = "max_tokens" if status == "incomplete" else ("refusal" if box.get("refused") else "end_turn")
    llm.record(purpose, {"model": model, "max_tokens": max_tokens},
               {"stop": stop, "provider_cost_usd": usd, "request_id": final.get("id"), "provider": "openai"}, usage)
    call._meter(model, usd)
    if box.get("refused") and not box.get("text"):
        raise call.Refused(f"{purpose}: the model refused")
    if status == "failed":
        raise RuntimeError(f"{purpose}: OpenAI response failed: {str(final.get('error'))[:200]}")
    return call.Result("".join(box.get("text") or []), usage, stop, round(box.get("first", 0.0), 2),
                       round(time.monotonic() - t0, 2), usd, final.get("id"))
