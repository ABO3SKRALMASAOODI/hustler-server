"""OpenAI speech for the narrator: gpt-4o-mini-tts directed by the script's performance fields.

One request reads a chunk of consecutive segments (up to CHUNK_WORDS words): the voice keeps one
line of thought across the chunk, which read 15-25% faster and with more melody than one request
per segment (13 Nov voice study, .valmera/week-1112/lanes/VOICE). The instructions are the voice's
persona, the script's overall pace and every line's own direction (pace, energy, melody, emphasis,
direction) as the writer model gave them. Nothing here reads intent from the words.

The key is OPENAI_API_KEY from the environment, else the one line of the Mac's backend .env
(never printed, never written anywhere). Paid from the OpenAI credit: about $0.015 a minute of speech.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

MODEL = os.environ.get("NARRATE_OPENAI_MODEL", "gpt-4o-mini-tts-2025-12-15")
CHUNK_WORDS = int(os.environ.get("NARRATE_OPENAI_CHUNK_WORDS", "60"))
ENV_FILE = Path(os.environ.get("NARRATE_ENV_FILE", "/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env"))
USD_PER_MIN = 0.015
DEFAULT_VOICE = "echo"
DEFAULT_PERSONA = ("Voice: a warm, confident narrator of a top short-form documentary channel, telling a true story. Close "
                   "to the microphone, natural and human, never an announcer, never salesy, never theatrical.\n"
                   "Delivery: conversational storytelling with intent behind every line; wide, natural pitch movement; "
                   "clear contrast between setup and payoff; numbers and names land crisp and exact.")

PACE_TXT = {"slow": "slow and deliberate, about 175 words a minute", "steady": "steady, about 190 words a minute",
            "brisk": "brisk, about 200 words a minute", "fast": "fast and driving, about 210 words a minute"}
MELODY_TXT = {"falling": "the sentence lands with a firm falling cadence", "rising": "a lifting, rising melody that leans forward",
              "level": "an even, restrained melody", "wide": "wide, expressive pitch movement"}
ENERGY_TXT = {"low": "low, intimate energy", "mid": "warm, engaged energy", "high": "high, intense energy"}


def api_key():
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        return None
    return None


def available():
    return bool(api_key())


def chunks(script, max_words=CHUNK_WORDS):
    """Consecutive segments grouped into requests of up to max_words words (a longer segment stands alone)."""
    out, cur = [], []
    for i, s in enumerate(script["segments"]):
        n = len(s["text"].split())
        if cur and sum(len(script["segments"][j]["text"].split()) for j in cur) + n > max_words:
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def instructions(persona, segs, script):
    lines = [persona.strip()]
    pace = script.get("pace")
    if pace:
        lines.append("Overall pace: " + (PACE_TXT.get(pace) or f"about {pace} words a minute") + ".")
    if script.get("energy"):
        lines.append("Overall energy: " + ENERGY_TXT.get(script["energy"], str(script["energy"])) + ".")
    if script.get("direction"):
        lines.append("Direction: " + str(script["direction"]))
    lines.append("Line by line:")
    for k, g in enumerate(segs):
        bits = []
        if g.get("pace") and g.get("pace") != pace:
            bits.append(PACE_TXT.get(g["pace"], f"about {g['pace']} words a minute"))
        if g.get("energy") and g.get("energy") != script.get("energy"):
            bits.append(ENERGY_TXT.get(g["energy"], str(g["energy"])))
        if g.get("melody"):
            bits.append(MELODY_TXT.get(g["melody"], str(g["melody"])))
        if g.get("emphasis"):
            bits.append("stress " + ", ".join(f'"{w}"' for w in g["emphasis"]))
        if g.get("direction"):
            bits.append(str(g["direction"]))
        head = " ".join(g["text"].split()[:5])
        lines.append(f'{k + 1}. "{head} ...": ' + ("; ".join(bits) if bits else "as the story needs") + ".")
    return "\n".join(lines)


def styled(seg):
    """The segment's text with the writer's emphasis words in capitals (the persona tells the voice that
    capitals carry the stress; measured 13 Nov: emphasis +15 points, pitch movement +20 against the
    references). The first occurrence of each marked word only; the words themselves never change."""
    import re
    t = seg["text"]
    for w in seg.get("emphasis") or []:
        w = str(w).strip()
        if len(w) >= 4:     # a short word in capitals reads as letters ("US", "IT")
            t = re.sub(r"(?<![\w'])" + re.escape(w) + r"(?![\w'])", w.upper(), t, count=1, flags=re.I)
    return t


def speech(text, voice, instr, model=MODEL, timeout=180):
    """One take: (mono float32 audio, sample rate)."""
    import soundfile as sf
    body = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
    if instr and model.startswith("gpt-"):
        body["instructions"] = instr
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + (api_key() or ""), "Content-Type": "application/json"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                x, sr = sf.read(io.BytesIO(r.read()), dtype="float32", always_2d=True)
                return x.mean(1), sr
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:  # noqa: BLE001 - a network blip is retried
            last = type(e).__name__
        time.sleep(2 + 4 * attempt)
    raise RuntimeError(f"OpenAI speech failed: {last}")


def trim(x, sr, below=45.0, keep=0.03, tail=0.06):
    """The take without the silence the service puts around it."""
    n = int(0.01 * sr)
    k = len(x) // n
    if k < 3:
        return x
    db = 10 * np.log10((x[: k * n].reshape(k, n).astype(np.float64) ** 2).mean(1) + 1e-14)
    on = np.where(db > db.max() - below)[0]
    if not len(on):
        return x
    a = max(0, on[0] * n - int(keep * sr))
    b = min(len(x), (on[-1] + 1) * n + int((keep + tail) * sr))
    return x[a:b]
