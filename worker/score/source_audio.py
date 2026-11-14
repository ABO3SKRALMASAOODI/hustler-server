"""One decode of a film's immutable source, shared by signal readers.

The cache lives only in the film work directory and is removed with it. Its
identity includes the resolved file and stat, so retries/replaced sources do
not inherit another recording's stress or level measurements.
"""
import json
import os
import subprocess
import tempfile

import numpy as np


def decode(src, work):
    stat = os.stat(src)
    identity = [os.path.realpath(src), stat.st_size, stat.st_mtime_ns,
                stat.st_ino, 16000, "f32le-mono-v1"]
    path = os.path.join(work, "source_audio.npy")
    meta = path + ".json"
    try:
        with open(meta) as f:
            if json.load(f) == identity:
                return np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        pass
    result = subprocess.run(
        [os.environ.get("FFMPEG_BIN", "ffmpeg"), "-nostdin", "-v", "error",
         "-i", src, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, timeout=120, check=True)
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    # Publish complete files only. A failed decode never poisons the cache.
    with tempfile.NamedTemporaryFile(dir=work, suffix=".npy", delete=False) as f:
        temporary = f.name
        np.save(f, audio, allow_pickle=False)
    try:
        os.replace(temporary, path)
        with open(meta, "w") as f:
            json.dump(identity, f)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return audio
