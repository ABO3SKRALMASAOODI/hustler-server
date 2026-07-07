"""LLM access — OpenAI-compatible SDK only, configured entirely by env.
Swapping DashScope for OpenAI/anything else is an env change, never code."""

import base64
import json
import re

from openai import OpenAI

import config


def client():
    return OpenAI(base_url=config.OPENAI_BASE_URL,
                  api_key=config.OPENAI_API_KEY,
                  timeout=180.0, max_retries=2)


def vision_available():
    return bool(config.VISION_MODEL and config.OPENAI_API_KEY)


def image_part(jpeg_path):
    with open(jpeg_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def ask_vision(prompt, image_paths, max_tokens=1500):
    """One call to VISION_MODEL with N images. Returns text, or None on any
    failure — vision is always optional."""
    if not vision_available():
        return None
    content = [{"type": "text", "text": prompt}]
    content += [image_part(p) for p in image_paths]
    try:
        resp = client().chat.completions.create(
            model=config.VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        print(f"[vision] call failed: {e}", flush=True)
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
