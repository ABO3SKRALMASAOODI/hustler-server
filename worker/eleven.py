"""AI sound-effect generation via ElevenLabs.

A dedicated provider: the LLM stack (xAI/OpenAI-compatible) has no text-to-audio
endpoint, so "make me a whoosh from a description" needs its own client. This is
deliberately tiny and synchronous — the Sound Effects endpoint returns the audio
bytes directly (no queue/poll, unlike video). Empty ELEVENLABS_API_KEY disables
the whole feature gracefully; the built-in CC0 sfx pack is unaffected.

Round 34 (CapCut initiative — see the capcut-any-asset-initiative memory).
"""

import requests

import config


def sound_gen_available():
    return bool(config.ELEVENLABS_API_KEY)


def generate_sfx(prompt, out_path, duration_s=None):
    """Generate a one-shot sound effect from a text description.

    Returns (True, None) on success (bytes written to out_path) or
    (False, "<short reason>") — the caller turns the reason into an honest,
    model-readable message and never claims a sound was created on failure.
    """
    if not sound_gen_available():
        return False, "sound generation is not configured (no ElevenLabs key)"
    body = {"text": prompt, "prompt_influence": 0.3}
    if duration_s is not None:
        # 0.5–22s per the API; None lets the model choose a natural length.
        body["duration_seconds"] = round(
            min(max(float(duration_s), 0.5), config.SFX_MAX_DURATION_S), 2)
    if config.ELEVEN_SFX_MODEL:
        body["model_id"] = config.ELEVEN_SFX_MODEL
    try:
        resp = requests.post(
            config.ELEVEN_SFX_URL, json=body,
            headers={"xi-api-key": config.ELEVENLABS_API_KEY,
                     "Accept": "audio/mpeg"},
            timeout=config.SFX_TIMEOUT_S)
    except requests.RequestException as e:
        return False, f"sound provider unreachable ({str(e)[:160]})"
    if resp.status_code != 200:
        # ElevenLabs returns JSON errors; surface a trimmed reason.
        detail = ""
        try:
            detail = resp.json().get("detail") or resp.text[:200]
        except Exception:
            detail = (resp.text or "")[:200]
        return False, f"sound provider returned {resp.status_code}: {detail}"
    data = resp.content
    if not data or len(data) < 256:
        return False, "sound provider returned an empty clip"
    try:
        with open(out_path, "wb") as f:
            f.write(data)
    except OSError as e:
        return False, f"could not save the generated sound ({str(e)[:120]})"
    return True, None
