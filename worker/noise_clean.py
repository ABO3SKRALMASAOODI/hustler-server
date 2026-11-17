"""Background-noise cleaning of the footage's own sound (EDIT-CORE A3).

p5744 (16 Nov 2026 golden replay): asked "Background noise ko mute kar do"
(mute the background noise), the editing model called an op named
'source_clean' three times. That name is the PICTURE clean-up (erasing a
watermark or a cursor); nothing in the product could clean a sound, so the
calls met schema errors and the customer was told there is no noise removal.
The render after the ask was within 2 dB of the one before.

This is the capability. A spectral gate, measured on the source itself:

1. The noise profile: the short-time spectrum (2048-sample frames, hop 512)
   of the stretches where nobody speaks (the transcript's words +-0.25 s are
   left out; a source with no pause uses its quietest frames), the quieter
   70% of those frames by energy so a footstep or a door does not count as
   the floor. Per frequency bin: the mean and the spread of its level.
2. The gate: a time-frequency cell is kept when it rises GATE_SIGMA spreads
   over its bin's floor (speech, music, a footstep), and lowered by
   reduction_db when it does not (hiss, hum, fan, room tone, traffic bed).
   The keep/lower mask is smoothed over 5 frames and 7 bins so it never
   chirps ("musical noise").
3. Overlap-add with a root-Hann window at hop N/4 (perfect reconstruction),
   streamed in chunks with globally aligned frames, so an hour-long source
   never sits in memory and the seams are exact.

The cleaned track is encoded (AAC 256k) and stored once per source and
strength; the renderer plays it in place of the original track
(renderer: noise_clean). Removing the EDL node restores the original.
"""
import json
import math
import os
import subprocess

import numpy as np

NOISE_CLEAN_VERSION = 1
RATE = 48000
N_FFT = 2048
HOP = 512
GATE_SIGMA = 1.5
SPEECH_PAD_S = 0.25
CHUNK_S = 20.0
PROFILE_FRAMES = 6000
QUIET_SHARE = 0.7
MIN_NOISE_FRAMES = 20
SMOOTH_T = 2            # frames each side
SMOOTH_F = 3            # bins each side


def reduction_for(strength):
    """dB a noise-only cell is lowered by at this strength (0..1)."""
    s = min(1.0, max(0.0, float(strength)))
    return round(10.0 + 30.0 * s, 1)


def key_for(sha, strength):
    return (f"analysis/noise_clean/v{NOISE_CLEAN_VERSION}/{sha}/"
            f"s{int(round(float(strength) * 100)):03d}.m4a")


def _ffmpeg():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def channels_of(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=channels", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return max(1, min(2, int(out.splitlines()[0])))
    except Exception:  # noqa: BLE001 - stereo is the renderer's own layout
        return 2


def _decoder(path, channels):
    return subprocess.Popen(
        [_ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
         "-vn", "-ac", str(channels), "-ar", str(RATE), "-f", "f32le",
         "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _window():
    n = np.arange(N_FFT)
    return np.sqrt(0.5 - 0.5 * np.cos(2.0 * np.pi * n / N_FFT)).astype(
        np.float32)


def _frames(x, start_pos):
    """Frames of mono/2-D (samples, ch) x starting at sample offsets that are
    multiples of HOP from start_pos (x[0] is at start_pos)."""
    n = (x.shape[0] - N_FFT) // HOP + 1
    if n <= 0:
        return None
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx]


def _speech_mask(times, spans):
    if not spans:
        return np.zeros(times.size, dtype=bool)
    out = np.zeros(times.size, dtype=bool)
    for a, b in spans:
        out |= (times >= a - SPEECH_PAD_S) & (times <= b + SPEECH_PAD_S)
    return out


def profile(path, speech_spans=(), cancel=None):
    """{"mean_db", "std_db", "floor_db", "frames"} of the noise where nobody
    speaks. mean/std are per-bin arrays (dB); floor_db the full-band RMS
    level (dBFS) of those frames."""
    win = _window()
    proc = _decoder(path, 1)
    picked, energies, every = [], [], 1
    pos = 0
    carry = np.zeros(0, dtype=np.float32)
    seen = 0
    try:
        while True:
            raw = proc.stdout.read(int(CHUNK_S * RATE) * 4)
            if not raw:
                break
            x = np.concatenate([carry, np.frombuffer(raw, dtype=np.float32)])
            fr = _frames(x, pos)
            if fr is None:
                carry = x
                continue
            n = fr.shape[0]
            centres = (pos + HOP * np.arange(n) + N_FFT / 2) / RATE
            noise = ~_speech_mask(centres, speech_spans)
            spec = np.abs(np.fft.rfft(fr * win, axis=1)) ** 2
            e = np.mean(fr * fr, axis=1)
            for k in np.nonzero(noise)[0]:
                seen += 1
                if seen % every:
                    continue
                picked.append(10.0 * np.log10(spec[k] + 1e-12))
                energies.append(float(e[k]))
                if len(picked) >= 2 * PROFILE_FRAMES:
                    picked, energies = picked[::2], energies[::2]
                    every *= 2
            used = n * HOP
            carry = x[used:]
            pos += used
            if cancel is not None and cancel():
                raise TimeoutError("cancelled")
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
    if len(picked) < MIN_NOISE_FRAMES:
        return None
    arr = np.asarray(picked, dtype=np.float32)
    en = np.asarray(energies)
    keep = en <= np.quantile(en, QUIET_SHARE)
    arr, en = arr[keep], en[keep]
    return {"mean_db": arr.mean(axis=0), "std_db": arr.std(axis=0),
            "floor_db": round(10.0 * math.log10(float(en.mean()) + 1e-12), 1),
            "frames": int(arr.shape[0])}


def _quietest_profile(path):
    """A source with no pause: its quietest frames are the floor."""
    win = _window()
    proc = _decoder(path, 1)
    raw = proc.stdout.read(int(120 * RATE) * 4)
    proc.kill()
    proc.wait()
    x = np.frombuffer(raw, dtype=np.float32)
    fr = _frames(x, 0)
    if fr is None or fr.shape[0] < MIN_NOISE_FRAMES:
        return None
    e = np.mean(fr * fr, axis=1)
    order = np.argsort(e)[:max(MIN_NOISE_FRAMES, fr.shape[0] // 5)]
    spec = np.abs(np.fft.rfft(fr[order] * win, axis=1)) ** 2
    db = 10.0 * np.log10(spec + 1e-12)
    return {"mean_db": db.mean(axis=0), "std_db": db.std(axis=0),
            "floor_db": round(10.0 * math.log10(float(e[order].mean())
                                                + 1e-12), 1),
            "frames": int(order.size)}


def _box(a, r, axis):
    """Centred moving mean of width 2r+1 along axis (edges replicated)."""
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    p = np.pad(a, pad, mode="edge")
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    zero = np.zeros_like(np.take(c, [0], axis=axis))
    c = np.concatenate([zero, c], axis=axis)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def clean(src_path, out_path, speech_spans=(), strength=0.8, cancel=None):
    """Write the cleaned track of src_path's sound to out_path (AAC m4a).
    Returns the report {"floor_db", "reduction_db", "channels", "frames",
    "profile": "pauses"|"quietest"}; raises on a file with no sound."""
    prof = profile(src_path, speech_spans, cancel=cancel)
    how = "pauses"
    if prof is None:
        prof, how = _quietest_profile(src_path), "quietest"
    if prof is None:
        raise ValueError("the footage's sound is too short to measure a "
                         "noise floor")
    thr = (prof["mean_db"] + GATE_SIGMA * prof["std_db"]).astype(np.float32)
    nr = reduction_for(strength)
    att = float(10.0 ** (-nr / 20.0))
    ch = channels_of(src_path)
    win = _window()
    margin = N_FFT + 4 * HOP
    core = int(CHUNK_S * RATE) // HOP * HOP
    dec = _decoder(src_path, ch)
    enc = subprocess.Popen(
        [_ffmpeg(), "-y", "-v", "error", "-nostdin", "-f", "f32le", "-ar",
         str(RATE), "-ac", str(ch), "-i", "pipe:0", "-c:a", "aac", "-b:a",
         "256k", "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    # buf holds input samples from global position buf_pos (a multiple of
    # HOP); the first buffer is preceded by `margin` zeros.
    buf = np.zeros((margin, ch), dtype=np.float32)
    buf_pos = -margin
    c = 0                       # next output sample (global)
    eof = False
    total_in = 0
    try:
        while True:
            need = c + core + margin
            while not eof and buf_pos + buf.shape[0] < need:
                raw = dec.stdout.read(int(CHUNK_S * RATE) * 4 * ch)
                if not raw:
                    eof = True
                    break
                x = np.frombuffer(raw, dtype=np.float32)
                x = x[:x.size // ch * ch].reshape(-1, ch)
                total_in += x.shape[0]
                buf = np.concatenate([buf, x])
            if eof:
                if c >= total_in:
                    break
                pad = need - (buf_pos + buf.shape[0])
                if pad > 0:
                    buf = np.concatenate(
                        [buf, np.zeros((pad, ch), dtype=np.float32)])
            # the window [c - margin, c + core + margin)
            a = c - margin - buf_pos
            seg = buf[a:a + core + 2 * margin]
            fr = _frames(seg, c - margin)          # (n, N, ch)
            n = fr.shape[0]
            spec = np.fft.rfft(fr * win[None, :, None], axis=1)
            mono = np.abs(spec).mean(axis=2) ** 2
            db = 10.0 * np.log10(mono + 1e-12)
            mask = (db > thr[None, :]).astype(np.float32)
            mask = _box(_box(mask, SMOOTH_T, 0), SMOOTH_F, 1)
            gain = att + (1.0 - att) * mask
            out_fr = np.fft.irfft(spec * gain[:, :, None], n=N_FFT, axis=1) \
                * win[None, :, None]
            y = np.zeros((seg.shape[0], ch), dtype=np.float64)
            wsum = np.zeros(seg.shape[0], dtype=np.float64)
            w2 = (win * win).astype(np.float64)
            for k in range(n):
                p = k * HOP
                y[p:p + N_FFT] += out_fr[k]
                wsum[p:p + N_FFT] += w2
            y = y / np.maximum(wsum, 1e-6)[:, None]
            end = min(core, max(0, total_in - c)) if eof else core
            piece = y[margin:margin + end].astype(np.float32)
            if piece.size:
                enc.stdin.write(piece.tobytes())
            c += end
            drop = c - margin - buf_pos
            if drop > 0:
                buf = buf[drop:]
                buf_pos += drop
            if cancel is not None and cancel():
                raise TimeoutError("cancelled")
            if eof and c >= total_in:
                break
        enc.stdin.close()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError(f"encoding the cleaned sound failed: {err[-300:]}")
    finally:
        for p in (dec, enc):
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:  # noqa: BLE001
                pass
        dec.wait()
    if total_in == 0:
        raise ValueError("the footage has no sound to clean")
    return {"floor_db": prof["floor_db"], "reduction_db": nr,
            "channels": ch, "frames": prof["frames"], "profile": how}


def level_db(path, start=None, end=None):
    """Full-band RMS level (dBFS) of path's sound, optionally in [start, end)."""
    args = [_ffmpeg(), "-v", "error", "-nostdin"]
    if start is not None:
        args += ["-ss", f"{float(start):.3f}"]
    if end is not None:
        args += ["-t", f"{max(0.05, float(end) - float(start or 0)):.3f}"]
    args += ["-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f",
             "f32le", "pipe:1"]
    raw = subprocess.run(args, capture_output=True, timeout=600).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size == 0:
        return None
    return round(10.0 * math.log10(float(np.mean(x * x)) + 1e-12), 1)


if __name__ == "__main__":  # python noise_clean.py <src> <out.m4a> [strength] [spans.json]
    import sys
    spans = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else []
    rep = clean(sys.argv[1], sys.argv[2], spans,
                float(sys.argv[3]) if len(sys.argv) > 3 else 0.8)
    print(json.dumps(rep))
