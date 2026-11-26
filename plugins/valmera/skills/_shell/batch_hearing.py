"""Hear source footage on the installed Atlas speech model, with the worker's local listener as fallback."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

ATLAS = Path("/Users/masaoodi/Documents/Valmera/.valmera/week-1106/atlas")
MODEL = ATLAS / "models/whisper-large-v3-mlx"
PYTHON = ATLAS / "venv/bin/python"
HEAVY = ATLAS.parent / "heavy.sh"


def speech_windows(pcm):
    """Return pairs of timestamps on the uncut original, using the footage worker's normal speech detector."""
    from faster_whisper.vad import get_speech_timestamps
    return [span[edge] / 16000 for span in get_speech_timestamps(pcm) for edge in ("start", "end")]


def read_local(path, seconds, hear, lock):
    import numpy as np
    from footage import transcript_cache as cache
    if seconds <= 1:
        return [], 0.0
    duration = min(float(seconds), hear.LOCAL_MAX_S)
    settings = dict(provider="atlas-mlx", model=MODEL.name, seconds=duration, version=1,
                    vad="silero", word_timestamps=True)
    key = cache.digest(path, settings)
    with cache.locked(key, namespace="atlas-clip") as dest:
        rows = cache.read(dest)
        if rows is not None:
            return rows, 0.0
        with tempfile.TemporaryDirectory(prefix="atlas-hear-", dir=os.environ.get("FOOTAGE_WORK")) as tmp:
            work = Path(tmp)
            wav = work / "clip.wav"
            subprocess.run([hear.fetch.ffmpeg(), "-v", "error", "-i", path, "-t", str(duration), "-vn",
                            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
            with wave.open(str(wav), "rb") as decoded:
                pcm = np.frombuffer(decoded.readframes(decoded.getnframes()), dtype="<i2").astype(np.float32) / 32768
            windows = speech_windows(pcm)
            rows = []
            if windows:
                (work / "windows.json").write_text(json.dumps(windows))
                # The GPU reader enters the same heavy-job queue as the other workflow tools.
                with open(lock, "a") as lease:
                    fcntl.flock(lease, fcntl.LOCK_EX)
                    from warm_film_tools import run_media
                    command = ["zsh", str(HEAVY), "wf3-batch-hearing", str(PYTHON), __file__, str(work)]
                    run_media(command, cwd=str(work), env=dict(os.environ, HF_HUB_OFFLINE="1"),
                              timeout=5400, progress_root=work).check_returncode()
                rows = hear._timed_rows(json.loads((work / "segments.json").read_text()), duration)
            cache.write(dest, rows)
            return rows, 0.0


def install(hear):
    lock = os.environ.get("VALMERA_BATCH_HEARING_LOCK")
    if not lock or not PYTHON.is_file() or not (MODEL / "config.json").is_file():
        return
    if getattr(hear.transcribe, "_batch_hearing", False):
        return
    normal = hear.transcribe

    def transcribe(path, dur, workers=8):
        # Preserve explicit provider choices and the worker's normal receipt accounting.
        if os.environ.get("FOOTAGE_HEAR_PROVIDER", "local") == "local":
            try:
                return read_local(path, dur, hear, os.environ["VALMERA_BATCH_HEARING_LOCK"])
            except (OSError, TypeError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
                print(f"Atlas hearing unavailable ({type(exc).__name__}); resuming with the footage listener.", flush=True)
        return normal(path, dur, workers=workers)

    transcribe._batch_hearing = True
    hear.transcribe = transcribe


if __name__ == "__main__":
    import mlx_whisper
    import numpy as np
    work = Path(sys.argv[1])
    with wave.open(str(work / "clip.wav"), "rb") as audio:
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
    windows = json.loads((work / "windows.json").read_text())
    result = mlx_whisper.transcribe(pcm, path_or_hf_repo=str(MODEL), clip_timestamps=windows,
                                    word_timestamps=True, condition_on_previous_text=False, verbose=None)
    segments = [dict(start=float(segment["start"]), end=float(segment["end"]), text=segment["text"])
                for segment in result["segments"]]
    (work / "segments.json").write_text(json.dumps(segments))
