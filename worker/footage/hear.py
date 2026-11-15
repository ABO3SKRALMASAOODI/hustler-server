"""A source's speech, transcribed on OpenAI (gpt-4o-transcribe), timed to a few seconds.

gpt-4o-transcribe answers text only (no timestamps: verbose_json is refused for it, 13 Nov 2026), so the
audio is cut into pieces of about PIECE_S seconds, each split placed in the quietest stretch near its
mark (ffmpeg silencedetect), and each piece is timed by where it sits. The look needs to know which few
seconds speak of a thing, not which word.

Price: $0.006 a minute heard (developers.openai.com, gpt-4o-transcribe).

Out of OpenAI credit (worker/openai_credit.py, 15 Nov 2026) the pieces are heard on the box instead:
the worker's own faster-whisper (transcribe.get_model: WHISPER_MODEL, baked into the image, loaded
under its memory guard, on the slot's CPUs), one pass over the pieces' span, each segment placed in
the piece its middle falls in. $0. At most LOCAL_MAX_S seconds are heard locally per source, so a
long source cannot hold the slot; the rest stays unheard, as before.
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
LOCAL_MAX_S = float(os.environ.get("FOOTAGE_HEAR_LOCAL_MAX_S", "900"))


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


def _credit_open():
    try:
        import openai_credit
        return openai_credit.available()
    except Exception:  # noqa: BLE001 - no gate module: OpenAI as before
        return True


def _one(path, t0, t1, tmp, i):
    import httpx
    if not _credit_open():
        return dict(t0=t0, t1=t1, text="", local=True)
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
                import openai_cache
                payload = r.json()
                usage = payload.get("usage")
                reported = isinstance(usage, dict) and usage.get("type") == "tokens" and MODEL in openai_cache.PRICES
                usd = openai_cache.cost(MODEL, usage) if reported else (t1 - t0) / 60.0 * USD_PER_MIN
                openai_cache.record(MODEL, "footage_hear", usage, usd=usd,
                                    seconds=t1 - t0, cost_basis="reported_tokens" if reported else "audio_duration_estimate")
                return dict(t0=t0, t1=t1, text=(payload.get("text") or "").strip(), usd=usd)
            import openai_cache
            openai_cache.record(MODEL, "footage_hear", usd=0.0, status="http_error", http_status=r.status_code)
            import openai_credit
            if openai_credit.note(r.text[:400], "footage_hear", status=r.status_code):
                return dict(t0=t0, t1=t1, text="", local=True)
            if r.status_code < 500 and r.status_code != 429:
                break
        except httpx.HTTPError:
            import openai_cache
            openai_cache.record(MODEL, "footage_hear", status="transport_error", billing_unconfirmed=True)
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
        need = [i for i, r in enumerate(rows) if r.get("local")]
        if need:
            try:
                heard = local_hear(path, cuts, need, tmp)
            except Exception as e:  # noqa: BLE001 the pieces stay unheard, as an OpenAI failure left them
                print(f"[footage-hear] local hearing failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
                heard = {}
            for i in need:
                if i in heard:
                    rows[i] = dict(t0=cuts[i], t1=cuts[i + 1], text=heard[i], usd=0.0)
                else:
                    rows[i] = dict(t0=cuts[i], t1=cuts[i + 1], text="", error="not heard")
    finally:
        subprocess.run(["rm", "-rf", tmp])
    usd = round(sum(r.get("usd", 0.0) for r in rows), 7)
    try:
        import llm
        llm.record("footage_hear", {"model": MODEL}, {"provider": "openai", "provider_cost_usd": usd, "seconds_heard": round(dur, 1), "local_spend_recorded": True})
    except Exception:  # noqa: BLE001 metering is never a stop
        pass
    return [dict(t0=round(r["t0"], 2), t1=round(r["t1"], 2), text=r["text"]) for r in rows], usd


def local_hear(path, cuts, idx, tmp, model=None):
    """{piece index: text} for the pieces idx, heard on this machine's faster-whisper in one pass.
    Pieces past LOCAL_MAX_S from the first are left out (unheard)."""
    lo = cuts[min(idx)]
    hi = min(cuts[max(idx) + 1], lo + LOCAL_MAX_S)
    keep = [i for i in idx if cuts[i] < hi]
    if not keep or hi - lo <= 0.5:
        return {}
    wav = os.path.join(tmp, "local.wav")
    r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{lo:.2f}", "-t", f"{hi - lo:.2f}",
                        "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", wav], capture_output=True)
    if r.returncode != 0 or not os.path.exists(wav):
        raise RuntimeError("audio cut failed")
    if model is None:
        import transcribe as asr
        model = asr.get_model()
    segments, _info = model.transcribe(wav, beam_size=1, vad_filter=True, condition_on_previous_text=False)
    texts = {i: [] for i in keep}
    for seg in segments:
        mid = lo + (float(seg.start) + float(seg.end)) / 2.0
        for i in keep:
            if cuts[i] <= mid < cuts[i + 1]:
                t = str(seg.text or "").strip()
                if t:
                    texts[i].append(t)
                break
    print(f"[footage-hear] heard {hi - lo:.0f} s locally (OpenAI credit out)", flush=True)
    return {i: " ".join(t) for i, t in texts.items()}
