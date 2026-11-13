"""A source's speech, transcribed on OpenAI (gpt-4o-transcribe), timed to a few seconds.

gpt-4o-transcribe answers text only (no timestamps: verbose_json is refused for it, 13 Nov 2026), so the
audio is cut into pieces of about PIECE_S seconds, each split placed in the quietest stretch near its
mark (ffmpeg silencedetect), and each piece is timed by where it sits. The look needs to know which few
seconds speak of a thing, not which word.

Price: $0.006 a minute heard (developers.openai.com, gpt-4o-transcribe).
"""
import concurrent.futures as cf
import os
import re
import subprocess
import tempfile

from . import fetch

MODEL = os.environ.get("FOOTAGE_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
USD_PER_MIN = 0.006
PIECE_S = 12.0
URL = "https://api.openai.com/v1/audio/transcriptions"


def _key():
    import config
    return config.OPENAI_API_KEY


def splits(path, dur):
    """Piece boundaries: every PIECE_S, moved to the middle of a silence within 4 s of the mark."""
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-nostats", "-t", f"{dur:.2f}", "-i", path, "-vn",
                        "-af", "silencedetect=noise=-32dB:d=0.25", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    mids = [(a + b) / 2 for a, b in zip(starts, ends)]
    out, t = [0.0], 0.0
    while t + PIECE_S < dur - 2.0:
        mark = t + PIECE_S
        near = [m for m in mids if abs(m - mark) <= 4.0 and m > t + 4.0]
        t = min(near, key=lambda m: abs(m - mark)) if near else mark
        out.append(round(t, 2))
    out.append(round(dur, 2))
    return out


def _one(path, t0, t1, tmp, i):
    import httpx
    piece = os.path.join(tmp, f"{i}.mp3")
    subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", path,
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-y", piece], capture_output=True)
    if not os.path.exists(piece) or os.path.getsize(piece) < 1500:
        return dict(t0=t0, t1=t1, text="")
    for k in range(3):
        try:
            with open(piece, "rb") as f:
                r = httpx.post(URL, headers={"Authorization": f"Bearer {_key()}"}, files={"file": (f"{i}.mp3", f, "audio/mpeg")},
                               data={"model": MODEL, "response_format": "json"}, timeout=90)
            if r.status_code == 200:
                return dict(t0=t0, t1=t1, text=(r.json().get("text") or "").strip())
            if r.status_code < 500 and r.status_code != 429:
                break
        except httpx.HTTPError:
            pass
    return dict(t0=t0, t1=t1, text="", error="not heard")


def transcribe(path, dur, workers=8):
    """([{t0, t1, text}], usd) for the first dur seconds of path."""
    if dur <= 1.0:
        return [], 0.0
    cuts = splits(path, dur)
    tmp = tempfile.mkdtemp(prefix="hear-", dir=os.path.dirname(path))
    try:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(lambda i: _one(path, cuts[i], cuts[i + 1], tmp, i), range(len(cuts) - 1)))
    finally:
        subprocess.run(["rm", "-rf", tmp])
    usd = round(dur / 60.0 * USD_PER_MIN, 5)
    try:
        import llm
        llm.record("footage_hear", {"model": MODEL}, {"provider": "openai", "provider_cost_usd": usd, "seconds_heard": round(dur, 1)})
    except Exception:  # noqa: BLE001 metering is never a stop
        pass
    return [dict(t0=round(r["t0"], 2), t1=round(r["t1"], 2), text=r["text"]) for r in rows], usd
