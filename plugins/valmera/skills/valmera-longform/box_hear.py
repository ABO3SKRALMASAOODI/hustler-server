"""One local Whisper pass per archive source, serialized inside the private box job.

This supplies coarse research transcripts only. Witness attribution, quote
verification and final narration WER remain separate mandatory gates.
"""
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace

_LISTENER = threading.Lock()


def transcribe(path, dur, workers=1):
    from footage import hear, transcript_cache
    from transcribe import get_model
    if dur <= 1:return [], 0.
    key = transcript_cache.digest(path, dict(backend='box-medium-local', beam_size=5,
                                           duration=dur, piece_s=hear.PIECE_S, version=1))
    with transcript_cache.locked(key, namespace='clip') as cache:
        cached = transcript_cache.read(cache)
        if cached is not None:return cached, 0.
        with _LISTENER:
            cuts = hear.splits(path, dur)
            tmp = tempfile.mkdtemp(prefix='local-hear-', dir=str(Path(path).parent))
            previous = hear.LOCAL_MAX_S
            try:
                model = get_model()
                # Match the local adapter's beam width without loading it for
                # every twelve-second piece. Keep the engine's timestamp map.
                listener = SimpleNamespace(transcribe=lambda p, **kw:
                    model.transcribe(p, **dict(kw, beam_size=5)))
                hear.LOCAL_MAX_S = dur
                heard = hear.local_hear(path, cuts, list(range(len(cuts)-1)), tmp, model=listener)
            finally:
                hear.LOCAL_MAX_S = previous
                shutil.rmtree(tmp)
        if len(heard) != len(cuts)-1:
            raise RuntimeError('Local archive listener left source intervals unheard')
        rows = [dict(t0=a, t1=b, text=heard[i]) for i,(a,b) in enumerate(zip(cuts,cuts[1:]))]
        transcript_cache.write(cache, rows)
        return rows, 0.


def install():
    from footage import hear
    hear.transcribe = transcribe
