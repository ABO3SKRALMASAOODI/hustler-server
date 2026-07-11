"""LLM access — OpenAI-compatible SDK only, configured entirely by env.
Swapping DashScope for OpenAI/anything else is an env change, never code."""

import base64
import json
import re
import threading

from openai import OpenAI

import config


_client = None

# Per-thread model-I/O recorder (set by the agent loop for the duration of a
# turn). Signature: fn(purpose, request_payload, response_payload, usage).
# Thread-local because agent lanes run turns concurrently.
_tls = threading.local()


def set_recorder(fn):
    _tls.recorder = fn


def get_recorder():
    return getattr(_tls, "recorder", None)


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


def vision_available():
    return bool(config.VISION_MODEL and config.OPENAI_API_KEY)


def image_part(jpeg_path):
    with open(jpeg_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def ask_vision(prompt, image_paths, max_tokens=1500, purpose="vision",
               image_names=None):
    """One call to VISION_MODEL with N images. Returns text, or None on any
    failure — vision is always optional. image_names (storage keys / labels)
    are what gets recorded to llm_calls — never the image bytes."""
    if not vision_available():
        return None
    content = [{"type": "text", "text": prompt}]
    content += [image_part(p) for p in image_paths]
    names = image_names or [str(p).rsplit("/", 1)[-1] for p in image_paths]
    try:
        resp = client().chat.completions.create(
            model=config.VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=0.1,
        )
        answer = (resp.choices[0].message.content or "").strip() or None
        record(purpose,
               {"model": config.VISION_MODEL, "question": prompt,
                "images": names},
               {"answer": answer}, getattr(resp, "usage", None))
        return answer
    except Exception as e:
        print(f"[vision] call failed: {e}", flush=True)
        record(purpose,
               {"model": config.VISION_MODEL, "question": prompt,
                "images": names},
               {"error": str(e)[:300]}, None)
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
        resp = client().chat.completions.create(
            model=config.AGENT_MODEL, messages=messages,
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
        record(purpose,
               {"model": config.AGENT_MODEL, "system": system, "user": user},
               {"error": str(e)[:300]}, None)
        return None


def extract_json_array(text):
    """Lenient JSON array extraction from a model reply."""
    if not text:
        return None
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else None
    except json.JSONDecodeError:
        return None
