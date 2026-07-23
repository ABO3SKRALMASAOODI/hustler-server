"""AI video generation via the fal.ai aggregator.

Chosen over first-party APIs because one FAL_KEY gives every major model
(Kling, Luma, Veo, Hailuo, Wan) selectable ENTIRELY by env — swap tiers without
a deploy, exactly like IMAGE_GEN_MODEL. NOT OpenAI-compatible: its own REST with
a submit → poll → fetch queue, so this is a dedicated adapter (never routed
through the OpenAI client). Async and minutes-scale.

Pricing is PER SECOND, not token-based — the caller computes cost from the
returned seconds and config.VIDEO_* and charges it to credits. Empty FAL_KEY
disables generate_video gracefully.

Round 34 (CapCut initiative — see the capcut-any-asset-initiative memory).
"""

import time

import requests

import config


def video_gen_available():
    return bool(config.FAL_KEY) and config.VIDEO_PROVIDER == "fal"


# Per-model input-shape quirks (the research flagged these as real: duration
# enums and image param names differ). Kling only accepts "5" or "10" as the
# duration; most others take an integer. Extend as models are added.
def _snap_seconds(model, seconds):
    seconds = min(max(float(seconds), 1.0), config.VIDEO_MAX_SECONDS)
    if "kling" in (model or "").lower():
        return 10.0 if seconds > 7.0 else 5.0
    return round(seconds)


def _build_body(model, prompt, image_url, seconds):
    body = {"prompt": prompt, "duration": str(int(seconds))}
    if image_url:
        body["image_url"] = image_url
    return body


def _headers():
    return {"Authorization": f"Key {config.FAL_KEY}",
            "Content-Type": "application/json"}


def _extract_video_url(result):
    if not isinstance(result, dict):
        return None
    v = result.get("video")
    if isinstance(v, dict) and v.get("url"):
        return v["url"]
    if isinstance(v, str):
        return v
    if result.get("video_url"):
        return result["video_url"]
    # some models return {"videos": [{"url": ...}]}
    vids = result.get("videos")
    if isinstance(vids, list) and vids and isinstance(vids[0], dict):
        return vids[0].get("url")
    return None


def generate_video(prompt, out_path, image_url=None, duration_s=5):
    """Generate a video clip from a text prompt (and optionally an image to
    animate). Blocks (polling) until the clip is ready, downloads it to
    out_path, and returns (True, None, seconds) or (False, "<reason>", 0.0).

    seconds is the billed length (used for the per-second credit charge)."""
    if not video_gen_available():
        return False, "video generation is not configured (no fal.ai key)", 0.0
    model = config.VIDEO_GEN_MODEL
    seconds = _snap_seconds(model, duration_s)
    body = _build_body(model, prompt, image_url, seconds)
    submit_url = f"{config.FAL_QUEUE_URL.rstrip('/')}/{model}"
    try:
        r = requests.post(submit_url, json=body, headers=_headers(),
                          timeout=30)
    except requests.RequestException as e:
        return False, f"video provider unreachable ({str(e)[:160]})", 0.0
    if r.status_code not in (200, 201, 202):
        detail = ""
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:
            detail = (r.text or "")[:200]
        return False, f"video provider returned {r.status_code}: {detail}", 0.0
    try:
        sub = r.json()
    except Exception:
        return False, "video provider returned an unreadable response", 0.0
    status_url = sub.get("status_url")
    response_url = sub.get("response_url")
    req_id = sub.get("request_id")
    if not status_url:
        if not req_id:
            return False, "video provider gave no request id to poll", 0.0
        status_url = f"{submit_url}/requests/{req_id}/status"
        response_url = f"{submit_url}/requests/{req_id}"

    deadline = time.monotonic() + config.VIDEO_POLL_TIMEOUT_S
    while True:
        if time.monotonic() > deadline:
            return False, ("video generation timed out — the clip is taking "
                           "longer than expected; try again"), 0.0
        time.sleep(config.VIDEO_POLL_INTERVAL_S)
        try:
            s = requests.get(status_url, headers=_headers(), timeout=30)
        except requests.RequestException:
            continue                    # transient — keep polling until deadline
        if s.status_code != 200:
            continue
        try:
            state = (s.json() or {}).get("status")
        except Exception:
            continue
        if state in ("COMPLETED", "OK", "SUCCESS"):
            break
        if state in ("ERROR", "FAILED", "CANCELLED"):
            return False, f"video generation failed on the provider ({state})", 0.0

    try:
        res = requests.get(response_url, headers=_headers(), timeout=30)
        result = res.json()
    except Exception as e:
        return False, f"could not read the finished video ({str(e)[:120]})", 0.0
    url = _extract_video_url(result)
    if not url:
        return False, "the provider finished but returned no video url", 0.0

    # Download to disk immediately — the fal URL is temporary; the caller
    # re-hosts to our own R2 (never hand the fal URL to the player).
    try:
        with requests.get(url, stream=True, timeout=90) as dl:
            if dl.status_code != 200:
                return False, f"downloading the clip failed ({dl.status_code})", 0.0
            with open(out_path, "wb") as f:
                for chunk in dl.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
    except (requests.RequestException, OSError) as e:
        return False, f"downloading the generated clip failed ({str(e)[:120]})", 0.0
    return True, None, float(seconds)


def price_for(seconds):
    """USD cost of a clip of `seconds` at the configured rate."""
    extra = max(0.0, float(seconds) - config.VIDEO_BASE_SECONDS)
    return round(config.VIDEO_BASE_PRICE_USD
                 + extra * config.VIDEO_PRICE_USD_PER_SEC, 4)
