"""On-demand music generation — the agent's answer to "I want THIS music".

WHY THIS EXISTS
The bundled CC0 library (music_library.py) is 24 tracks across 8 moods. It is
excellent at "add some music" and useless at "epic movie-trailer music", "sad
piano", "drill beat" — and the failure was SILENT: list_music_library's mood
argument was an 8-value enum, so a request the catalog could not serve was
indistinguishable from one it could. The agent picked the nearest bucket,
reported success, and the user churned. This module supplies the missing
capability; music_search.py supplies the missing *perception* of a miss.

PROVIDER-AGNOSTIC BY CONSTRUCTION
Same shape as llm.image_provider(): the backend is inferred from whichever key
is configured, and when none is, available() is False and the tool disables
itself with an honest message instead of failing at call time. Adding a third
provider is one _generate_* function plus a branch.

    MUSIC_ELEVENLABS_API_KEY  -> elevenlabs  (preferred when both are set)
    MUSIC_STABILITY_API_KEY   -> stability
    neither                   -> disabled, honestly

LICENSING — READ BEFORE CHANGING THE CACHING STORY
Generated tracks are minted as PROJECT-SCOPED assets (generated-music/<project
_id>/...), exactly like generate_image. That is not an accident of convenience:
ElevenLabs' Music terms prohibit creating a "library, catalogue, database, or
other repository of Output with the intent of licensing it or otherwise making
it available to third parties", capped at 100 Outputs on EVERY tier including
Enterprise. A shared cross-customer cache keyed on prompt — the obvious
optimisation — is precisely that prohibited repository. Do not build one.
Regenerating per project costs ~$0.15-0.20 and keeps the deployment inside the
terms.

Neither vendor's self-serve terms AFFIRMATIVELY grant a platform the right to
pass commercial rights to ITS end users; both assign output to the account
holder (Valmera). Valmera therefore licenses the result onward under its own
terms, and the operator is the one who decides that posture — which is why
this ships disabled and is enabled by adding a key, not by a code change.

PROMPT CONTENT IS CONTRACTUALLY CONSTRAINED. ElevenLabs' Music terms forbid
submitting any artist's or songwriter's real or stage name, any song or album
title, any label or publisher name, or a substantial portion of any lyrics.
The AGENT writes these prompts, so the constraint is stated in the tool
description it reads (agent_tools.generate_music). It is deliberately NOT
enforced by a regex here: the set of artist names is unbounded, a blocklist
would be security theatre that fails open on everything it misses, and the
honest engineering answer to an unbounded constraint is to tell the writer
about it. Describe the SOUND ("wide brass, low taiko, rising strings"), never
the maker.
"""

import os
import time

import requests

import config
import llm
import media

# A 5-minute MP3 is ~5 MB; 32 MB is generous headroom for a lossless-ish
# response and still far below anything that would threaten the worker disk.
MAX_TRACK_BYTES = 32 << 20

# Vendor endpoints. Both are single synchronous POSTs that return raw audio
# bytes — no job/poll path, which matters on a 1-vCPU worker where an extra
# polling loop competes with ffmpeg for the box.
_ELEVEN_URL = "https://api.elevenlabs.io/v1/music"
_STABILITY_URL = ("https://api.stability.ai/v2beta/audio/"
                  "stable-audio-2/text-to-audio")


def provider():
    """Which music backend to use, inferred from the configured keys.
    ElevenLabs first when both are present: it is the only vendor in this
    space with rightsholder deals (Merlin, Kobalt) behind the model, which is
    the difference that matters if a Content ID claim ever lands on a
    customer's monetized upload."""
    if config.MUSIC_ELEVENLABS_API_KEY:
        return "elevenlabs"
    if config.MUSIC_STABILITY_API_KEY:
        return "stability"
    return None


def available():
    return bool(provider())


def max_duration_s():
    """Longest track this backend will produce. Stability's cap is a hard
    190s; ElevenLabs documents 5 min on its capabilities page and 10 min in
    the API reference — the conservative number is used because exceeding it
    is a hard API error mid-turn, and no edit this product renders needs a
    single cue longer than 5 minutes."""
    return {"elevenlabs": 300.0, "stability": 190.0}.get(provider(), 0.0)


def price_usd(duration_s):
    """What one generation of this length costs us, in dollars. Used to bill
    the user honestly (1 credit = $0.01, same convention as everywhere else).
    ElevenLabs meters per minute of audio; Stability is flat per call."""
    p = provider()
    if p == "elevenlabs":
        return round(config.MUSIC_ELEVENLABS_USD_PER_MIN * (duration_s / 60.0),
                     4)
    if p == "stability":
        return config.MUSIC_STABILITY_USD_PER_CALL
    return 0.0


def describe():
    """One line for the agent's capability digest."""
    p = provider()
    if not p:
        return None
    return (f"AI music generation ({p}), up to "
            f"{int(max_duration_s())}s per track")


def _redact(text):
    """Strip any configured API key out of a string bound for the model.

    requests validates header VALUES and raises InvalidHeader containing the
    offending value — which here is the API key. A key pasted into Render's
    env UI with a stray newline or a non-latin1 character (routine) would put
    the secret verbatim into the tool result the model reads, into the
    llm_calls table, and potentially into the user-facing reply."""
    for k in (config.MUSIC_ELEVENLABS_API_KEY, config.MUSIC_STABILITY_API_KEY):
        k = (k or "").strip()
        if len(k) >= 8:
            text = text.replace(k, "***")
    return text


def _record(prompt, duration_s, ok, detail, cost_usd):
    """Log the call so charge_turn_credits can bill it.

    The response MUST carry 'cost_usd' on success — db.charge_turn_credits
    SUMs that field, and a missing key silently makes the generation free.
    Failures record an error instead and cost the user nothing, which is the
    same contract llm._openai_image_gen follows for images."""
    body = {"cost_usd": cost_usd} if ok else {"error": _redact(str(detail))}
    llm.record("music_gen",
               {"provider": provider(), "prompt": prompt[:500],
                "duration_s": duration_s},
               body, None)


def _write(resp, out_path):
    """Stream the response to disk, bounded by SIZE and by WALL CLOCK.

    requests' `timeout=` is a per-socket-read timeout, not a transfer
    deadline — it resets on every byte, so a provider dribbling data can hold
    the read open indefinitely. AGENT_TURN_TIMEOUT_S cannot rescue it either:
    the loop only checks the wall clock BETWEEN iterations, so a blocked read
    inside a tool call is invisible to it. Unbounded, this could hold the
    turn open forever and fill the worker's ephemeral disk (TMP_DIR is the
    container overlay, shared with every render on the box)."""
    t0 = time.monotonic()
    n = 0
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(1 << 16):
            n += len(chunk)
            if n > MAX_TRACK_BYTES:
                raise ValueError(
                    f"the music service sent more than "
                    f"{MAX_TRACK_BYTES >> 20} MB")
            if time.monotonic() - t0 > config.MUSIC_TIMEOUT_S:
                raise ValueError(
                    f"the music download exceeded "
                    f"{config.MUSIC_TIMEOUT_S:.0f}s")
            f.write(chunk)


def _err_detail(resp):
    """A short, useful reason from a vendor error response. The agent shows
    this to the user, so it must not be a wall of JSON."""
    try:
        j = resp.json()
    except Exception:
        return _redact(f"HTTP {resp.status_code}: {resp.text[:160]}")
    for k in ("detail", "message", "error", "errors"):
        v = j.get(k)
        if isinstance(v, dict):
            v = v.get("message") or v.get("detail") or str(v)
        if isinstance(v, list) and v:
            v = str(v[0])
        if v:
            return _redact(f"HTTP {resp.status_code}: {str(v)[:200]}")
    return _redact(f"HTTP {resp.status_code}: {str(j)[:160]}")


def _generate_elevenlabs(prompt, out_path, duration_s, instrumental):
    body = {
        "prompt": prompt,
        "music_length_ms": int(round(duration_s * 1000)),
        # Pin the model explicitly — the API default is the older music_v1.
        "model_id": config.MUSIC_ELEVENLABS_MODEL,
        "force_instrumental": bool(instrumental),
    }
    resp = requests.post(
        _ELEVEN_URL, json=body, stream=True,
        timeout=config.MUSIC_TIMEOUT_S,
        headers={"xi-api-key": config.MUSIC_ELEVENLABS_API_KEY,
                 "accept": "audio/mpeg"})
    if resp.status_code != 200:
        return False, _err_detail(resp)
    _write(resp, out_path)
    return True, None


def _generate_stability(prompt, out_path, duration_s, instrumental):
    # multipart/form-data with no file part: requests needs the `files=`
    # shape to emit multipart at all, hence the (None, value) tuples.
    fields = {
        "prompt": (None, prompt),
        # Pass the model EXPLICITLY: the endpoint's default is the older,
        # formula-priced stable-audio-2.0.
        "model": (None, "stable-audio-2.5"),
        # Pass duration EXPLICITLY too — it defaults to the 190s maximum,
        # which would bill a full-length track for a 20s sting.
        "duration": (None, str(int(round(duration_s)))),
        "output_format": (None, "mp3"),
    }
    if instrumental:
        # Stability has no force_instrumental flag; the documented lever is
        # the negative prompt.
        fields["negative_prompt"] = (None, "vocals, singing, lyrics, voice")
    resp = requests.post(
        _STABILITY_URL, files=fields, stream=True,
        timeout=config.MUSIC_TIMEOUT_S,
        headers={"authorization": f"Bearer {config.MUSIC_STABILITY_API_KEY}",
                 # The API hard-rejects the usual '*/*' default.
                 "accept": "audio/*"})
    if resp.status_code != 200:
        return False, _err_detail(resp)
    _write(resp, out_path)
    return True, None


def generate(prompt, out_path, duration_s, instrumental=True):
    """Generate one track to out_path (MP3). Returns (ok, short_error).

    Never raises: a vendor outage must surface as an honest tool result the
    agent can tell the user about, not as a traceback that kills the turn
    after the user has already been charged for the tokens that got here.
    """
    p = provider()
    if not p:
        return False, "no music generation backend configured"
    cap = max_duration_s()
    duration_s = max(5.0, min(float(duration_s), cap))
    cost = price_usd(duration_s)
    try:
        fn = (_generate_elevenlabs if p == "elevenlabs"
              else _generate_stability)
        ok, err = fn(prompt, out_path, duration_s, instrumental)
    except requests.Timeout:
        err = f"the music service did not respond within {config.MUSIC_TIMEOUT_S:.0f}s"
        _record(prompt, duration_s, False, err, 0.0)
        return False, err
    except Exception as e:
        err = _redact(f"music API error: {str(e)[:200]}")
        _record(prompt, duration_s, False, err, 0.0)
        return False, err
    if not ok:
        _record(prompt, duration_s, False, err, 0.0)
        return False, err
    # A 200 with an empty or absurdly small body is a vendor bug that would
    # otherwise become a silent 0-byte "track" the renderer fails on much
    # later, with no trace back to here.
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    if size < 2048:
        err = f"the music service returned an empty file ({size} bytes)"
        _record(prompt, duration_s, False, err, 0.0)
        return False, err
    # Size alone does not mean AUDIO. A 200 carrying an HTML error page or a
    # JSON blob sails past the floor above, and the caller would then upload
    # it as a music asset, bill the user, and tell them a track was composed
    # — the renderer only discovering it is unplayable at export time, with
    # no trace back to here. ffprobe is the real test, so it gates the
    # success record rather than being computed and discarded.
    try:
        media.probe_audio_duration(out_path)
    except Exception:
        err = "the music service returned a file that is not playable audio"
        _record(prompt, duration_s, False, err, 0.0)
        return False, err
    _record(prompt, duration_s, True, None, cost)
    return True, None
