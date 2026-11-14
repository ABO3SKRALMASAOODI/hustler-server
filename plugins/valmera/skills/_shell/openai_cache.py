"""OpenAI prefix policy and append-only workflow spend (mirrored in worker/backend/_shell).

Docs checked 2026-11-14: GPT-5.6+ uses explicit boundaries and 12h/30m TTL;
older supported models use 24h retention. Never pad a prompt to earn a hit.
https://developers.openai.com/api/docs/guides/prompt-caching
"""
import copy
import datetime as dt
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import uuid

MODERN = ("gpt-5.6", "gpt-6")
EXTENDED = ("gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.2", "gpt-5.1-codex-max",
            "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.1-chat-latest",
            "gpt-5", "gpt-5-codex", "gpt-4.1")
DEFAULT_LOG = "/Users/masaoodi/Documents/Valmera/.valmera/week-1112/openai-spend.jsonl"
# Standalone skills do not import worker/config or connect to a production DB.
PRICES = {"gpt-6.1-sol": (2, .1, 10), "gpt-6.1-terra": (1, .05, 5),
          "gpt-6-sol": (2, .2, 10), "gpt-6-luna": (.1, .01, .5),
          "gpt-5.6-luna": (.2, .02, 1.2), "gpt-5.5": (5, .5, 30)}


def get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def tokens(usage):
    u = usage or {}
    details = get(u, "prompt_tokens_details") or get(u, "input_tokens_details") or {}
    tin = int(get(u, "prompt_tokens", get(u, "input_tokens", 0)) or 0)
    cached = min(tin, max(0, int(get(details, "cached_tokens", 0) or 0)))
    written = min(tin - cached, max(0, int(get(details, "cache_write_tokens", 0) or 0)))
    lifetimes = get(details, "cache_write_tokens_details") or {}
    long = min(written, max(0, int(get(lifetimes, "ttl_12h_tokens", 0) or 0)))
    return dict(prompt_tokens=tin, cached_tokens=cached, cache_write_tokens=written,
                cache_write_12h_tokens=long,
                completion_tokens=int(get(u, "completion_tokens", get(u, "output_tokens", 0)) or 0))


def cost(model, usage):
    t = tokens(usage)
    pin, pc, pout = PRICES.get(model, PRICES["gpt-5.5"])
    try:
        import model_prices
        p = model_prices.MODEL_PRICES.get(model)
        if p:
            pin, pc, pout = p["in"], p.get("cached_in", p["in"]), p["out"]
    except ImportError:
        pass
    w, long = t["cache_write_tokens"], t["cache_write_12h_tokens"]
    inp = ((t["prompt_tokens"] - t["cached_tokens"] - w) * pin + t["cached_tokens"] * pc
           + (w - long) * pin * 1.25 + long * pin * 2.5)
    out = t["completion_tokens"] * pout
    if model in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol") and t["prompt_tokens"] > 272000:
        inp, out = inp * 2, out * 1.5
    tier = {"priority": 2, "fast": 2, "flex": .5, "batch": .5}.get(get(usage, "service_tier"), 1)
    return (inp + out) * tier / 1e6


def key(role):
    # Role is a code label, never inferred from the customer's words.
    return "valmera:" + str(role)[:55]


def chat_options(model, role):
    if not str(model).startswith("gpt-"):
        return {}
    out = {"prompt_cache_key": key(role)}
    if any(model == alias or str(model).startswith(alias + "-20") for alias in EXTENDED):
        out["prompt_cache_retention"] = "24h"
    return out


def responses(body, role, *, conversation=False):
    """Copy, never mutate caller messages. Static instructions precede variable input.

    One-shot calls write only the shared developer prefix; editor conversations
    also retain the default evolving-history breakpoint. Each of the first two
    instruction messages gets its own boundary, so project state cannot hide the
    kernel boundary. No content is removed, shortened, or inferred here.
    """
    b = copy.deepcopy(body)
    model = str(b.get("model", ""))
    b.update(chat_options(model, role))
    if not model.startswith(MODERN):
        return b
    b.pop("prompt_cache_retention", None)
    instructions = b.pop("instructions", None)
    inp = b.get("input") or []
    if isinstance(inp, str):
        inp = [{"role": "user", "content": [{"type": "input_text", "text": inp}]}]
    if instructions:
        inp.insert(0, {"role": "developer", "content": [{"type": "input_text", "text": instructions}]})
    b["input"] = inp
    b["prompt_cache_options"] = {"mode": "implicit" if conversation else "explicit", "ttl": "30m"}
    # 12h writes cost twice 30m writes. Keep the measured cheaper default;
    # workflows reused across long gaps can explicitly opt into 12h.
    ttl = os.environ.get("VALMERA_OPENAI_PREFIX_TTL", "30m")
    if ttl not in ("30m", "12h"):
        ttl = "30m"
    marked = 0
    for msg in inp:
        if msg.get("role") not in ("system", "developer") or marked == 2:
            break
        parts = msg.get("content", [])
        if isinstance(parts, str):
            parts = [{"type": "input_text", "text": parts}]
            msg["content"] = parts
        for part in reversed(parts):
            if part.get("type") == "input_text":
                part["prompt_cache_breakpoint"] = {"mode": "explicit", "ttl": ttl}
                marked += 1
                break
    return b


def workflow(skill=None, run=None):
    """Bind once at the workflow entry point; children inherit these identifiers."""
    if skill:
        os.environ["VALMERA_SKILL"] = str(skill)
    if run:
        os.environ["VALMERA_RUN"] = str(run)
    os.environ.setdefault("VALMERA_RUN", dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10])


def identity():
    skill = os.environ.get("VALMERA_SKILL")
    if not skill:
        # File paths, not user intent. Covers direct helpers and worker functions
        # imported by a workflow, including ThreadPoolExecutor calls.
        for part in Path(sys.argv[0]).parts:
            if part in ("valmera-podcast-shorts", "valmera-stories", "valmera-marketing", "valmera-longform", "_shell"):
                skill = part
                break
    return skill, os.environ.get("VALMERA_RUN")


def record(model, purpose, usage=None, *, usd=None, request_id=None, status="completed", force=False, **extra):
    """One lock-protected JSON line per provider response, including incomplete calls.

    No prompts, frames, transcripts, credentials or customer IDs are written.
    Product processes opt in with VALMERA_OPENAI_SPEND_LOG; Mac skills default
    to the coordinator's ledger. A failed write is visible and never retried as
    another paid model call.
    """
    skill, run = identity()
    path = os.environ.get("VALMERA_OPENAI_SPEND_LOG")
    if not (skill or path or force):
        return
    if not run:
        workflow()
        run = os.environ["VALMERA_RUN"]
    row = dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(), skill=skill or "worker",
               run=run, purpose=str(purpose), model=model,
               usd=(cost(model, usage) if usage is not None else None) if usd is None else float(usd), **tokens(usage),
               usage_reported=usage is not None,
               request_id=request_id, status=status, **extra)
    try:
        import fcntl
        p = Path(path or DEFAULT_LOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as exc:
        print(f"[openai-spend] append failed: {type(exc).__name__}", file=sys.stderr, flush=True)


def role_for(system, prefix="workflow"):
    # Stable for the exact instructions, independent of film/run/frame data.
    for frame in inspect.stack(context=0)[1:]:
        stem = Path(frame.filename).stem
        if stem not in ("llm", "openai_cache"):
            prefix = stem + ":" + frame.function
            break
    return prefix + ":" + hashlib.sha256(str(system).encode()).hexdigest()[:12]
