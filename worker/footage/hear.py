"""Whole-clip hearing: local Whisper now; timestamped OpenAI by explicit opt-in.

One pass preserves actual segment times; repeats use the bounded content cache.
"""
import concurrent.futures as cf
import os
import re
import subprocess
import tempfile
import shutil
import hashlib
import json

from . import fetch, transcript_cache

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


def _one(path, t0, t1, tmp, i, source_hash=None):
    # Content plus exact interval/model, never a URL, filename or project ID.
    source_hash = source_hash or transcript_cache.digest(path, {"version": 1})
    key = hashlib.sha256(json.dumps([source_hash, MODEL, t0, t1, 1]).encode()).hexdigest()
    with transcript_cache.locked(key) as cache:
        rows = transcript_cache.read(cache)
        if rows is not None:
            return dict(rows[0], usd=0.0)
        row = _transcribe_piece(path, t0, t1, tmp, i)
        if not row.get("local"):
            transcript_cache.write(cache, [row])
        return row


def _transcribe_piece(path, t0, t1, tmp, i):
    import httpx, openai_budget
    if not _credit_open():
        return dict(t0=t0, t1=t1, text="", local=True)
    piece = os.path.join(tmp, f"{i}.mp3")
    encoded = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", path,
                    "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-y", piece], capture_output=True)
    if encoded.returncode or not os.path.exists(piece) or os.path.getsize(piece) < 1500:
        return dict(t0=t0, t1=t1, text="", error="audio extraction failed")
    for k in range(3):
        try:
            with openai_budget.call({"model": MODEL}, "footage_hear", ceiling_usd=(t1-t0)/60*.02) as lease, open(piece, "rb") as f:
                r = httpx.post(URL, headers={"Authorization": f"Bearer {_key()}"}, files={"file": (f"{i}.mp3", f, "audio/mpeg")},
                               data={"model": MODEL, "response_format": "json"}, timeout=90)
            if r.status_code == 200:
                import openai_cache
                payload = r.json()
                usage = payload.get("usage")
                reported = isinstance(usage, dict) and usage.get("type") == "tokens" and MODEL in openai_cache.PRICES
                usd = openai_cache.cost(MODEL, usage) if reported else (t1 - t0) / 60.0 * USD_PER_MIN
                lease.settle(usage, usd=usd)
                openai_cache.record(MODEL, "footage_hear", usage, usd=usd,
                                    seconds=t1 - t0, audio_sha256=transcript_cache.digest(piece, {"model": MODEL}),
                                    piece_start_s=t0, piece_end_s=t1,
                                    cost_basis="reported_tokens" if reported else "audio_duration_estimate")
                return dict(t0=t0, t1=t1, text=(payload.get("text") or "").strip(), usd=usd)
            if r.status_code in (400, 401, 403, 404, 422, 429):
                lease.settle(usd=0)
            import openai_cache
            openai_cache.record(MODEL, "footage_hear", usd=0.0, status="http_error", http_status=r.status_code)
            import openai_credit
            if openai_credit.note(r.text[:400], "footage_hear", status=r.status_code):
                return dict(t0=t0, t1=t1, text="", local=True)
            if r.status_code < 500 and r.status_code != 429:
                break
        except httpx.HTTPError:
            if r.status_code in (400, 401, 403, 404, 422, 429):
                lease.settle(usd=0)
            import openai_cache
            openai_cache.record(MODEL, "footage_hear", status="transport_error", billing_unconfirmed=True)
    return dict(t0=t0, t1=t1, text="", error="not heard")


def _provider():
    # Local is the safe default during the outage. Funding alone must not
    # silently switch transcript semantics. Opt in to timestamped OpenAI later.
    return "openai" if os.environ.get("FOOTAGE_HEAR_PROVIDER", "local") == "openai" and _credit_open() else "local"


def _timed_rows(segments, duration):
    rows = []
    for seg in segments:
        get = seg.get if isinstance(seg, dict) else lambda k, d=None: getattr(seg, k, d)
        a, b = max(0.0, float(get("start", 0))), min(duration, float(get("end", 0)))
        text = str(get("text", "") or "").strip()
        if b > a and text:
            rows.append(dict(t0=round(a, 3), t1=round(b, 3), text=text))
    if any(rows[i]["t0"] < rows[i-1]["t0"] for i in range(1, len(rows))):
        raise ValueError("transcriber returned unordered timestamps")
    return rows


def _local_clip(wav, duration):
    import transcribe as asr
    segments, _ = asr.get_model().transcribe(
        wav, beam_size=1, vad_filter=True, word_timestamps=True,
        condition_on_previous_text=False)
    return _timed_rows(segments, duration)


def _openai_clip(audio, duration):
    """One upload, actual segment timestamps. Never fabricate times from text.

    gpt-4o-transcribe has no verbose timestamps. The explicit OpenAI opt-in
    uses whisper-1's supported verbose_json until a new timestamped adapter
    is validated. Local Whisper remains the default.
    """
    import httpx, openai_budget, openai_cache, openai_credit
    model = "whisper-1"
    with openai_budget.call({"model": model}, "footage_hear", ceiling_usd=duration / 60 * USD_PER_MIN + .006) as lease:
        with open(audio, "rb") as f:
            response = httpx.post(URL, headers={"Authorization": f"Bearer {_key()}"},
                files={"file": ("clip.mp3", f, "audio/mpeg")},
                data={"model": model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}, timeout=180)
        if response.status_code in (400, 401, 403, 404, 422, 429):
            lease.settle(usd=0)
        if response.status_code >= 400:
            openai_credit.note(response.text[:400], "footage_hear", status=response.status_code)
            raise RuntimeError(f"footage transcription HTTP {response.status_code}")
        payload = response.json()
        usd = float(payload.get("duration", duration)) / 60 * USD_PER_MIN
        lease.settle(usd=usd)
        openai_cache.record(model, "footage_hear", payload.get("usage"), usd=usd,
                            seconds=duration, audio_sha256=transcript_cache.digest(audio, {"model": model}),
                            cost_basis="audio_duration_official_rate")
        if not isinstance(payload.get("segments"), list):
            raise ValueError("whole-clip transcription omitted timestamps")
        return _timed_rows(payload["segments"], duration), usd


def transcribe(path, dur, workers=8):
    """One recognition pass over the clip, returning real segment timestamps.

    The stable return contract is ([{t0,t1,text}], usd). A content-addressed
    memo includes provider/model/options; local results never impersonate an
    OpenAI result. A failure never gets cached. ``workers`` remains accepted
    for callers; it no longer multiplies API calls by 12-second pieces.
    """
    if dur <= 1.0:
        return [], 0.0
    duration = min(float(dur), LOCAL_MAX_S)
    provider = _provider()
    import config
    model = "whisper-1" if provider == "openai" else str(config.WHISPER_MODEL)
    key = transcript_cache.digest(path, dict(provider=provider, model=model, duration=duration,
                                            version=2, beam_size=1, vad=True, word_timestamps=True))
    with transcript_cache.locked(key, namespace="clip") as cache:
        rows = transcript_cache.read(cache)
        if rows is not None:
            return rows, 0.0
        with tempfile.TemporaryDirectory(prefix="hear-", dir=os.path.dirname(path)) as tmp:
            wav = os.path.join(tmp, "clip.wav")
            r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-t", str(duration),
                                "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", wav], capture_output=True)
            if r.returncode or not os.path.exists(wav):
                raise RuntimeError("whole-clip audio extraction failed")
            usd = 0.0
            if provider == "openai":
                audio = os.path.join(tmp, "clip.mp3")
                r = subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", wav,
                                    "-b:a", "32k", "-y", audio], capture_output=True)
                if r.returncode or os.path.getsize(audio) >= 25 * 1024 * 1024:
                    raise RuntimeError("whole-clip audio exceeds supported upload size")
                try:
                    rows, usd = _openai_clip(audio, duration)
                except Exception as exc:
                    import openai_budget
                    openai_budget.propagate(exc)
                    # Cache the local fallback under its own provider identity.
                    # No recursive provider call, no second paid attempt.
                    rows = _local_clip(wav, duration)
                    return rows, 0.0
            else:
                rows = _local_clip(wav, duration)
            transcript_cache.write(cache, rows)
        if usd:
            import llm
            llm.record("footage_hear", {"model": "whisper-1"},
                       {"provider": "openai", "provider_cost_usd": usd,
                        "seconds_heard": duration, "local_spend_recorded": True})
        return rows, usd


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
