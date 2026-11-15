"""One local Whisper pass per archive source, serialized inside the private box job.

This supplies coarse research transcripts only. Witness attribution, quote
verification and final narration WER remain separate mandatory gates.
"""
from pathlib import Path
import subprocess
import tempfile
import threading

_LISTENER = threading.Lock()


def transcribe(path, dur, workers=1):
    from footage import hear, fetch, transcript_cache
    from transcribe import get_model
    if dur <= 1:return [], 0.
    key = transcript_cache.digest(path, dict(backend='box-medium-local', beam_size=5,
                                           duration=dur, word_timestamps=True, version=2))
    with transcript_cache.locked(key, namespace='clip') as cache:
        cached = transcript_cache.read(cache)
        if cached is not None:return cached, 0.
        with _LISTENER:
            with tempfile.TemporaryDirectory(prefix='local-hear-', dir=str(Path(path).parent)) as tmp:
                wav=Path(tmp)/'clip.wav'
                subprocess.run([fetch.ffmpeg(),'-hide_banner','-loglevel','error','-t',str(dur),
                    '-i',path,'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le','-y',str(wav)],
                    check=True,capture_output=True)
                model = get_model()
                segments,_=model.transcribe(str(wav),beam_size=5,vad_filter=True,
                    word_timestamps=True,condition_on_previous_text=False)
                # The shared engine now retains real segment timestamps;
                # never reuse the earlier twelve-second coarse clock as these.
                rows=hear._timed_rows(segments,dur)
        transcript_cache.write(cache, rows)
        return rows, 0.


def install():
    from footage import hear
    hear.transcribe = transcribe
