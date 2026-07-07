#!/usr/bin/env python3
"""Synthesize a ~60s test clip: six colored slates (shot boundaries for
PySceneDetect) with a speech/tone burst at the start of each slate and a
real >=1s digital silence before the next one (for silencedetect).

Speech comes from the platform TTS when available (`say` on macOS,
`espeak-ng` on Linux) so whisper has something to transcribe; otherwise
falls back to sine tones (shots + silences still work).

Usage: python scripts/make_test_video.py [out.mp4]
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

RATE = 16000
SLATE_S = 10
BURST_S = 6.0
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan"]
LINES = [
    "This is segment one. Welcome to the Valmera test video.",
    "Segment two begins now. The quick brown fox jumps over the lazy dog.",
    "Here is segment three. We are testing silence removal today.",
    "Segment four is playing. Captions should follow every word.",
    "This is segment five. Almost done with the test recording.",
    "Final segment six. Thank you for watching this test.",
]


def tts_wav(text, out_path):
    """Best-effort TTS -> 16k mono wav. Returns True on success."""
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "raw")
        try:
            if shutil.which("say"):        # macOS
                subprocess.run(["say", "-o", raw + ".aiff", text],
                               check=True, capture_output=True, timeout=60)
                src = raw + ".aiff"
            elif shutil.which("espeak-ng"):
                subprocess.run(["espeak-ng", "-w", raw + ".wav", text],
                               check=True, capture_output=True, timeout=60)
                src = raw + ".wav"
            elif shutil.which("espeak"):
                subprocess.run(["espeak", "-w", raw + ".wav", text],
                               check=True, capture_output=True, timeout=60)
                src = raw + ".wav"
            else:
                return False
            subprocess.run(["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar",
                            str(RATE), "-c:a", "pcm_s16le", out_path],
                           check=True, capture_output=True, timeout=60)
            return True
        except Exception:
            return False


def tone_samples(freq, seconds):
    n = int(RATE * seconds)
    return [int(12000 * math.sin(2 * math.pi * freq * i / RATE)
                * min(1.0, i / 800, (n - i) / 800))
            for i in range(n)]


def read_samples(path, max_seconds):
    with wave.open(path, "rb") as w:
        n = min(w.getnframes(), int(RATE * max_seconds))
        raw = w.readframes(n)
    return list(struct.unpack(f"<{len(raw)//2}h", raw))


def main(out_path):
    total_s = SLATE_S * len(COLORS)
    master = [0] * (RATE * total_s)
    used_tts = False

    with tempfile.TemporaryDirectory() as td:
        for i, line in enumerate(LINES):
            wav = os.path.join(td, f"burst{i}.wav")
            if tts_wav(line, wav):
                samples = read_samples(wav, BURST_S)
                used_tts = True
            else:
                samples = tone_samples(300 + i * 110, BURST_S)
            off = i * SLATE_S * RATE
            master[off:off + len(samples)] = samples

        audio_path = os.path.join(td, "master.wav")
        with wave.open(audio_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(struct.pack(f"<{len(master)}h", *master))

        inputs, vlabels = [], ""
        for i, c in enumerate(COLORS):
            inputs += ["-f", "lavfi", "-i",
                       f"color=c={c}:s=1280x720:d={SLATE_S}:r=30"]
            vlabels += f"[{i}:v]"
        cmd = ["ffmpeg", "-y", *inputs, "-i", audio_path,
               "-filter_complex",
               f"{vlabels}concat=n={len(COLORS)}:v=1:a=0[v]",
               "-map", "[v]", "-map", f"{len(COLORS)}:a",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-shortest", "-movflags", "+faststart", out_path]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)

    print(f"wrote {out_path} ({total_s}s, {len(COLORS)} slates, "
          f"speech={'tts' if used_tts else 'tones'})")
    return out_path


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "test_video.mp4")
