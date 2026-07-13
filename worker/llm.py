"""LLM access — OpenAI-compatible SDK only, configured entirely by env.
Swapping DashScope for OpenAI/anything else is an env change, never code.
(The one exception: image generation/editing uses DashScope's native
multimodal-generation endpoint, because no OpenAI-compatible equivalent
exists there — it degrades to unavailable on non-DashScope bases.)"""

import base64
import json
import mimetypes
import re
import threading

import requests
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


def image_api_url():
    if config.IMAGE_API_URL:
        return config.IMAGE_API_URL
    base = (config.OPENAI_BASE_URL or "").split("/compatible-mode")[0]
    base = base.rstrip("/")
    if "dashscope" not in base:
        return None
    return base + "/api/v1/services/aigc/multimodal-generation/generation"


def image_available():
    return bool(config.IMAGE_GEN_MODEL and config.OPENAI_API_KEY
                and image_api_url())


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
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}",
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


def generate_image(prompt, out_path, aspect=None):
    """Text-to-image via IMAGE_GEN_MODEL. Saves a PNG to out_path.
    Returns (True, None) or (False, short_error)."""
    if not image_available():
        return False, "no image model configured"
    model = config.IMAGE_GEN_MODEL
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
    """Instruction-based edit of a local image via IMAGE_EDIT_MODEL.
    Saves the edited PNG to out_path. Returns (True, None) or
    (False, short_error). image_name is what gets logged, never the bytes."""
    if not image_available():
        return False, "no image model configured"
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
