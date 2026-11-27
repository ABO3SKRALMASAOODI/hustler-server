"""Use the factory's warm Whisper pool for take auditions. Final checks retain their own listeners."""
import io
import json
import os
import subprocess

import soundfile as sf


def pooled_take(x, sr):
    import proxy_client
    audio = io.BytesIO()
    sf.write(audio, x, sr, format="WAV", subtype="PCM_24")
    # The proxy client normally lengthens audio timeouts to cover a whole footage queue.
    # A short audition can switch to the local listener after one bounded request.
    res = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail", "--max-time", "45",
         "--header", "X-Codex-Proxy-Run: " + os.environ.get("CODEX_PROXY_PROOF", "stories-takes"),
         "--form-string", "_proxy_run=" + os.environ.get("CODEX_PROXY_PROOF", "stories-takes"),
         "--form", "model=whisper-1", "--form", "language=en", "--form", "response_format=verbose_json",
         "--form", "file=@-;filename=take.wav;type=audio/wav", proxy_client.AUDIO_BASE + "/audio/transcriptions"],
        input=audio.getvalue(), capture_output=True, check=True, timeout=50)
    heard = json.loads(res.stdout)
    words = [dict(w=w["word"].strip(), start=float(w["start"]), end=float(w["end"]),
                  p=float(w["probability"])) for w in heard["words"]]
    if not words or not any(w["end"] > w["start"] for w in words):
        raise ValueError("no timed speech in pool answer")
    return heard["text"], words


def pool_or_local(narrate):
    available = True
    def listen(x, sr):
        nonlocal available
        if available:
            try:
                return pooled_take(x, sr)
            except (ImportError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                narrate.log(f"Take pool unavailable ({type(exc).__name__}); using unprompted local large-v3 for this read")
                available = False
        names = narrate.VOCAB[:]
        try:
            narrate.VOCAB.clear()
            return narrate.listen(x, sr)
        finally:
            narrate.VOCAB[:] = names
    return listen
