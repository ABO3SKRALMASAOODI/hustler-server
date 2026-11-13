"""narrate: the narrator voice for workflows 3 (stories) and 4 (long-form voiceover).

VoxCPM2 (Apache-2.0, on the Mac) speaks by default; OpenAI gpt-4o-mini-tts,
directed per line, is the second engine (--engine openai, openai_voice.py).
whisper large-v3-turbo (faster-whisper, CPU) hears every take; whisper large-v3
(mlx) times the finished words and is the gate's listener.

Every read gets the same finish (13 Nov voice study, 48 reference narrators):
pauses set from the script and the reference band (pauses.py), the writer's
stressed words ridden up, and a mastering chain solved against the references
by signal (master.py: high-pass, tonal EQ, compressor, leveler, presence,
de-ess, -14 LUFS / -1 dBTP). --no-master restores the old finish.

  PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-narrate/bin/python
  $PY narrate.py voices
  $PY narrate.py render SCRIPT.json --out DIR [--voice ID] [--band 03|04] [--takes 3] [--lufs -15.6]
  $PY narrate.py check DIR [--band 03|04|all]
  $PY narrate.py make-voice --id ID --describe "..." --line "..." [--takes 4] [--meta '{"gender":..,"band":..}']
  $PY narrate.py make-voices SPEC.json [--takes 5]

How a long script stays one performance
  The script is cut into chunks of one to three sentences. Every chunk is
  generated as a continuation of the chunk before it: the model sees the
  previous chunk's own latents and text, plus the voice's reference clip, so
  timbre, level, tempo and melody carry across. All chunk latents are then
  decoded in ONE pass through the causal audio decoder, so there is no
  waveform seam at all; the only edits in the signal are pause lengths, made
  inside the quiet core of a silence. `check` measures every chunk boundary
  (energy step, spectral flux, click, noise floor, level and pitch drift)
  against the sentence gaps inside chunks, which are the same moment without
  a join.

Performance layer (per segment of the script; a writer model fills it, the
tool never guesses intent from the words):
  pace       "slow" | "steady" | "brisk" | "fast" or a target in words/min
             (articulation rate, inside the reference band)
  energy     "low" | "mid" | "high"
  melody     "falling" | "rising" | "level" | "wide"   (sentence melody)
  emphasis   ["word", ...]   words to stress
  pause_after seconds of silence after the segment (exact, set in the signal)
  direction  free words for the actor ("quiet, almost confessional")
The fields become the actor's direction for the model. Pace is asked relative
to the tempo the voice is running at (the model keeps the tempo it continues),
and the voice's reference clip is first brought to the script's opening pace.
Takes are measured: a chunk is kept only when the listener hears every word
and its pace sits inside the reference band; among those the one nearest the
asked pace wins.

Outputs in DIR: narration.wav (48 kHz mono, mastered), words.json (every
script word with start/end, its segment and chunk, plus phrases and cut
points), render.json (chunks, takes, boundaries, timings); `check` adds
check.json (the VOICE gate).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure  # noqa: E402
import spoken  # noqa: E402

VOICES = HERE / "voices"
MODEL_ID = os.environ.get("NARRATE_MODEL", "openbmb/VoxCPM2")
_ATLAS_ASR = Path("/Users/masaoodi/Documents/Valmera/.valmera/week-1106/atlas/models/whisper-large-v3-mlx")
ASR_MODEL = os.environ.get("NARRATE_ASR") or (str(_ATLAS_ASR) if _ATLAS_ASR.exists()
                                               else "mlx-community/whisper-large-v3-mlx")
CHECK_ASR = os.environ.get("NARRATE_CHECK_ASR", "large-v3-turbo")      # faster-whisper, hears every take
# the reference narrators' band (reference-library/2026-11-05 folders 03 and 04),
# measured by .valmera/week-1112/lanes/M/tools/ref_band.py; shipped beside this file
BAND = Path(os.environ.get("NARRATE_BAND", str(HERE / "ref_band.json")))
OUT_SR = 48000
FFMPEG = os.environ.get("NARRATE_FFMPEG") or next(
    (p for p in ("/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg",
                 "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg") if os.path.exists(p)), "ffmpeg")
# pace words are targets for articulation rate (words/min while speaking),
# placed inside the reference narrators' band (03: p10 175, median 192, p90 217)
PACE_WPM = {"slow": 178, "steady": 190, "brisk": 201, "fast": 211}
# VoxCPM's own default on Apple GPUs is float32. Measured on this Mac (12 Nov):
# bfloat16 gives the same word error, ASR confidence, high band energy and
# clicks as float32 at a third of the memory, so it no longer swaps and runs
# 5-10x faster. NARRATE_DTYPE=float32 restores the default.
os.environ.setdefault("VOXCPM_MPS_DTYPE", os.environ.get("NARRATE_DTYPE", "bfloat16"))
CHUNK_WORDS = 42
# The voice engine. The 13 Nov voice study (.valmera/week-1112/lanes/VOICE: 48 reference narrators,
# 23 signal features, every candidate through the same finish) measured VoxCPM2 with the new finish
# nearest the references (79 of 100; 64.5 before it), OpenAI gpt-4o-mini-tts directed per line next (76),
# tts-1-hd 68.
# NARRATE_ENGINE / --engine / the script's "engine" choose; openai needs OPENAI_API_KEY.
DEFAULT_ENGINE = "voxcpm"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


# ================================================================ script

def split_sentences(text):
    text = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?…])[\"'”’]?\s+(?=[\"'“‘(]?[A-Z0-9])", text)
    return [p.strip() for p in parts if p.strip()]


def load_script(path):
    p = Path(path)
    if p.suffix == ".json":
        s = json.loads(p.read_text())
    else:
        paras = [x.strip() for x in re.split(r"\n\s*\n", p.read_text()) if x.strip()]
        s = {"segments": [{"text": x, "pause_after": 0.7} for x in paras]}
    segs = s.get("segments") or []
    if not segs:
        raise SystemExit("script has no segments")
    for g in segs:
        g["text"] = re.sub(r"\s+", " ", g["text"]).strip()
    return s


def target_wpm(seg, script):
    pace = seg.get("pace", script.get("pace"))
    return seg.get("wpm") or (pace if isinstance(pace, (int, float)) else PACE_WPM.get(pace))


def direction_for(seg, script, current=None):
    """The actor's direction, built from the performance fields. Pace is asked
    relative to how fast the voice is running now (`current`, words/min while
    speaking): the model keeps the tempo of the audio it continues, so 'fast'
    to a voice already above the target would run away from the band."""
    bits = []
    wpm = target_wpm(seg, script)
    if wpm:
        r = wpm / current if current else None
        if r is None:
            bits.append("slow, deliberate pace" if wpm < 180 else "steady pace" if wpm < 196
                        else "brisk pace" if wpm < 211 else "fast, driving pace")
        elif r < 0.90:
            bits.append("slow, deliberate pace, unhurried")
        elif r < 0.97:
            bits.append("steady, measured pace")
        elif r > 1.10:
            bits.append("fast, driving pace")
        elif r > 1.03:
            bits.append("brisk, moving pace")
    energy = seg.get("energy", script.get("energy"))
    if energy:
        bits.append({"low": "low, intimate energy", "mid": "warm, engaged energy",
                     "high": "high, intense energy"}.get(energy, f"{energy} energy"))
    melody = seg.get("melody", script.get("melody"))
    if melody:
        bits.append({"falling": "sentences land with a firm falling cadence",
                     "rising": "lifting, rising sentence melody",
                     "level": "even, restrained melody",
                     "wide": "wide, expressive pitch movement"}.get(melody, f"{melody} melody"))
    emph = seg.get("emphasis") or []
    if emph:
        bits.append("stress " + ", ".join(f"'{w}'" for w in emph))
    d = seg.get("direction") or script.get("direction")
    if d:
        bits.append(d)
    return ", ".join(bits), wpm


def make_chunks(script):
    chunks = []
    for si, seg in enumerate(script["segments"]):
        sents = split_sentences(seg["text"])
        cur = []
        for s in sents:
            n = len(s.split())
            if cur and sum(len(x.split()) for x in cur) + n > CHUNK_WORDS:
                chunks.append({"seg": si, "text": " ".join(cur)})
                cur = []
            cur.append(s)
        if cur:
            chunks.append({"seg": si, "text": " ".join(cur)})
    for i, c in enumerate(chunks):
        last = i == len(chunks) - 1 or chunks[i + 1]["seg"] != c["seg"]
        c["pause_after"] = float(script["segments"][c["seg"]].get("pause_after", 0) or 0) if last else None
    return chunks


# ================================================================ model

class Engine:
    def __init__(self, device=None):
        import torch
        from huggingface_hub import snapshot_download
        from voxcpm.model.voxcpm2 import VoxCPM2Model
        self.torch = torch
        path = MODEL_ID if os.path.isdir(MODEL_ID) else snapshot_download(MODEL_ID)
        dev = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        t = time.time()
        self.m = VoxCPM2Model.from_local(path, optimize=False, device=dev)
        self.sr = self.m.sample_rate
        self.spt = self.m.patch_size * self.m._decode_chunk_size      # output samples per latent token
        log(f"model on {dev} ({self.m.config.dtype}) in {time.time() - t:.0f}s, {self.sr} Hz")

    def close(self):
        """Give the GPU memory back before the large listener runs."""
        self.m = None
        import gc
        gc.collect()
        self.free()

    def free(self):
        if self.torch.backends.mps.is_available():
            self.torch.mps.empty_cache()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def ref_feat(self, wav):
        # VoxCPM's _encode_wav runs without inference_mode, so the encoder kept
        # every activation for a backward pass: a 13 s prompt took the MPS pool
        # to its 30 GB ceiling (13 Nov, long-form chunk 7)
        self.free()
        with self.torch.inference_mode():
            return self.m._encode_wav(str(wav), padding_mode="right")

    def generate(self, text, ref_feat, prev=None, direction="", seed=0, steps=10, cfg=2.0):
        """One take. prev = (prev_text, prev_feat) for continuation. Returns
        (audio at self.sr for listening, new latents [t,p,d])."""
        torch = self.torch
        torch.manual_seed(seed)
        lead = f"({direction})" if direction else ""
        if prev is None:
            cache = {"ref_audio_feat": ref_feat, "mode": "reference"}
            target = lead + text
        else:
            cache = {"ref_audio_feat": ref_feat, "mode": "ref_continuation",
                     "prompt_text": lead + prev[0].rstrip() + " ", "audio_feat": prev[1]}
            target = text
        from voxcpm.model.utils import next_and_close
        wav, _, feat = next_and_close(self.m._generate_with_prompt_cache(
            target_text=target, prompt_cache=cache, inference_timesteps=steps, cfg_value=cfg,
            retry_badcase=True, retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0,
            max_len=4096))
        return wav.squeeze().float().numpy(), feat

    def decode_all(self, feats, step=32):
        """Decode the whole latent sequence through the causal decoder in one
        stateful pass: identical to a single decode, bounded memory."""
        torch = self.torch
        from einops import rearrange
        z = torch.cat([f.detach().cpu() for f in feats], 0)       # [T, p, d]
        out = []
        with torch.inference_mode(), self.m.audio_vae.streaming_decode() as dec:
            for i in range(0, z.shape[0], step):
                part = rearrange(z[i:i + step][None], "b t p d -> b d (t p)").to(self.m.device, torch.float32)
                out.append(dec.decode_chunk(part).squeeze().float().cpu().numpy())
        return np.concatenate(out)


# ================================================================ listening

_asr_cache = {}
# The story's proper nouns as its writer spelled them (the script's `names`, written by the writer model). The gate's
# listener (large-v3) hears with them as vocabulary, as a human checker would know the names: a rare name spelled the
# common way ("Goldstein" for Goldstine) is the listener's spelling, not the narrator's error, and cost lane O 1-2% word
# error. The take listener (large-v3-turbo on 10-20 s chunks) does not: with the prompt it invented "thank you" and
# "forty six" at window edges on lane O's ENIAC film (13 Nov), more than the names it fixed.
VOCAB = []


def vocab_prompt():
    names = [str(n).strip() for n in VOCAB if str(n).strip()]
    return ("Names: " + ", ".join(names[:60]) + ".") if names else None


def listen(x, sr, model=None):
    """whisper large-v3 (mlx) words with timings: [{w,start,end,p}]."""
    import mlx_whisper
    y = measure.resample(x, sr, 16000) if sr != 16000 else x
    r = mlx_whisper.transcribe(y.astype(np.float32), path_or_hf_repo=model or ASR_MODEL, language="en",
                               word_timestamps=True, condition_on_previous_text=False, verbose=None,
                               temperature=0.0, initial_prompt=vocab_prompt())
    words = [{"w": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"]),
              "p": float(w.get("probability", 1))} for s in r["segments"] for w in s.get("words", [])]
    return r["text"], words


def listen_fast(x, sr):
    """The take listener: whisper large-v3-turbo through faster-whisper, int8 on
    the CPU. Takes are heard while VoxCPM holds the GPU; a second large model in
    unified memory pushed VoxCPM's weights to swap between takes (12 Nov: 15 s
    stalls on every take), so takes are heard on the CPU and the large-v3
    listener runs once, on the finished narration."""
    from faster_whisper import WhisperModel
    m = _asr_cache.get("fw") or WhisperModel(CHECK_ASR, device="cpu", compute_type="int8",
                                             cpu_threads=int(os.environ.get("NARRATE_ASR_THREADS", "6")))
    _asr_cache["fw"] = m
    y = measure.resample(x, sr, 16000) if sr != 16000 else x
    segs, _ = m.transcribe(y.astype(np.float32), language="en", beam_size=5, word_timestamps=True,
                           condition_on_previous_text=False, vad_filter=False)
    words = [{"w": w.word.strip(), "start": w.start, "end": w.end, "p": w.probability}
             for s in segs for w in (s.words or [])]
    return " ".join(w["w"] for w in words), words


def listen_long(x, sr, fn=None, max_s=25.0):
    """Hear a long narration in windows cut in its own silences (each at most
    `max_s`, under whisper's 30 s window), so the listener never skips or
    invents a passage across a window edge, as one pass over 9 minutes did."""
    fn = fn or listen
    db, n = measure._frames_db(x, sr)
    quiet = db < np.percentile(db, 90) - 35
    cuts, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if j - i >= 15:
                cuts.append((i + j) / 2 * n / sr)
            i = j
        else:
            i += 1
    dur = len(x) / sr
    texts, words, a = [], [], 0.0
    while a < dur - 0.05:
        inside = [c for c in cuts if a + 1.0 < c <= a + max_s]
        b = dur if dur - a <= max_s else (inside[-1] if inside else a + max_s)
        t, w = fn(x[int(a * sr): int(b * sr)], sr)
        texts.append(t.strip())
        words += [dict(v, start=round(v["start"] + a, 3), end=round(v["end"] + a, 3)) for v in w]
        a = b
    return " ".join(texts), words


def map_words(script_text, heard):
    """Give every script word a start/end from the heard words."""
    toks = script_text.split()
    sub = [measure.norm_words(t) for t in toks]
    flat, owner = [], []
    for i, s in enumerate(sub):
        flat += s
        owner += [i] * len(s)
    hyp = [measure.norm_words(w["w"]) for w in heard]
    hflat, hown = [], []
    for j, s in enumerate(hyp):
        hflat += s
        hown += [j] * len(s)
    _, _, _, pairs = measure.align(flat, hflat)
    span = [[None, None] for _ in toks]
    for fi, hj in pairs.items():
        w = heard[hown[hj]]
        i = owner[fi]
        span[i][0] = w["start"] if span[i][0] is None else min(span[i][0], w["start"])
        span[i][1] = w["end"] if span[i][1] is None else max(span[i][1], w["end"])
    # words the listener missed: share the gap between their neighbours
    i = 0
    while i < len(toks):
        if span[i][0] is not None:
            i += 1
            continue
        j = i
        while j < len(toks) and span[j][0] is None:
            j += 1
        a = span[i - 1][1] if i > 0 else (heard[0]["start"] if heard else 0.0)
        b = span[j][0] if j < len(toks) else (heard[-1]["end"] if heard else a)
        n = j - i
        for k in range(n):
            span[i + k] = [a + (b - a) * k / n, a + (b - a) * (k + 1) / n]
        for k in range(i, j):
            span[k].append(True)
        i = j
    return [{"w": t, "start": round(s[0], 3), "end": round(s[1], 3), **({"guessed": True} if len(s) > 2 else {})}
            for t, s in zip(toks, span)]


# ================================================================ voices

def voice_list():
    out = []
    for j in sorted(VOICES.glob("*.json")):
        v = json.loads(j.read_text())
        v["id"] = j.stem
        out.append(v)
    return out


def voice_path(vid):
    for ext in (".flac", ".wav"):
        p = VOICES / f"{vid}{ext}"
        if p.exists():
            return p
    raise SystemExit(f"no voice '{vid}'; have: {', '.join(v['id'] for v in voice_list())}")


# ================================================================ signal edits

def _frames(x, sr, win=0.01):
    db, n = measure._frames_db(x, sr, win)
    return db, n


def set_pause(x, sr, t, want, floor_db):
    """Make the silence around sample time t last `want` seconds, editing only
    inside the silence (mirror-tiled room tone in, or a crossfaded cut out).
    Returns (new signal, shift in samples, old pause s)."""
    db, n = _frames(x, sr)
    c = int(t * sr / n)
    lo, hi = max(1, c - 80), min(len(db) - 1, c + 80)
    quiet = db < floor_db
    # the quiet run nearest t
    best = None
    i = lo
    while i < hi:
        if quiet[i]:
            j = i
            while j < len(db) and quiet[j]:
                j += 1
            k = i
            while k > 0 and quiet[k - 1]:
                k -= 1
            d = 0 if k <= c <= j else min(abs(c - k), abs(c - j))
            if best is None or d < best[0] or (d == best[0] and j - k > best[2] - best[1]):
                best = (d, k, j)
            i = j
        else:
            i += 1
    if best is None:
        return x, 0, 0.0
    _, k, j = best
    a, b = k * n, j * n
    have = (b - a) / sr
    xf = int(0.012 * sr)
    if want is None or abs(want - have) < 0.02:
        return x, 0, have
    # the quiet core of the pause: frames within 6 dB of its floor. Edits happen
    # there, so the decay of the last word is never copied (a mirrored tail is a
    # faint ghost syllable in the silence) and never cut short
    seg_db = db[k:j]
    core = seg_db <= seg_db.min() + 6
    ck, cj, i = k, k, 0
    while i < len(core):
        if core[i]:
            e = i
            while e < len(core) and core[e]:
                e += 1
            if e - i > cj - ck:
                ck, cj = k + i, k + e
            i = e
        else:
            i += 1
    ca, cb = ck * n, cj * n
    if want > have:
        add = int((want - have) * sr)
        mid = (ca + cb) // 2
        seg = x[ca: cb] if cb - ca > 2 * xf else np.zeros(2 * xf, np.float32)
        tiles, flip = [], False
        while sum(len(s) for s in tiles) < add + 2 * xf:
            tiles.append(seg[::-1] if flip else seg)
            flip = not flip
        fill = np.concatenate(tiles)[: add + 2 * xf].astype(np.float32)
        r = np.linspace(0, 1, xf, dtype=np.float32)
        left, right = x[:mid], x[mid:]
        fill[:xf] = left[-xf:] * (1 - r) + fill[:xf] * r
        fill[-xf:] = fill[-xf:] * (1 - r) + right[:xf] * r
        y = np.concatenate([left[:-xf], fill, right[xf:]])
        return y, len(y) - len(x), have
    cut = int((have - want) * sr)
    keep = max(int(0.06 * sr), 0)
    if b - a - cut < 2 * keep:
        cut = max(0, b - a - 2 * keep)
    if cut <= 2 * xf:
        return x, 0, have
    mid = min(max((ca + cb) // 2, a + keep + cut // 2 + xf), b - keep - cut // 2 - xf)
    s0, s1 = mid - cut // 2, mid + (cut - cut // 2)
    r = np.linspace(0, 1, xf, dtype=np.float32)
    joint = x[s0 - xf: s0] * (1 - r) + x[s1: s1 + xf] * r
    y = np.concatenate([x[: s0 - xf], joint, x[s1 + xf:]])
    return y, len(y) - len(x), have


def side_steps(a, b, sr, ctx_s=1.5):
    """Speech level (dB) and median pitch (semitones) step from the last ctx_s
    of `a` to the first ctx_s of `b`: what `check` measures at a join."""
    la, lb = a[-int(ctx_s * sr):], b[:int(ctx_s * sr)]
    if len(la) < sr // 2 or len(lb) < sr // 2:
        return None, None
    lstep = abs(measure._speech_db(la, sr) - measure._speech_db(lb, sr))
    pa, pb = measure._f0_med(la, sr), measure._f0_med(lb, sr)
    return round(lstep, 2), (round(abs(12 * math.log2(pa / pb)), 2) if pa and pb else None)


def inner_steps(wav, sr, heard):
    """The same steps at this take's own pauses (sentence gaps without a join)."""
    out = []
    for a, b in zip(heard, heard[1:]):
        if b["start"] - a["end"] >= measure.PAUSE_S:
            t = int((a["end"] + b["start"]) / 2 * sr)
            out.append(side_steps(wav[:t], wav[t:], sr))
    return [o for o in out if o[0] is not None]


def take_tail_ok(wav, sr, tail_s=0.06, below=30.0):
    """True when a take ends in silence: its last 60 ms sit `below` dB under its speech."""
    db, _ = measure._frames_db(wav, sr, 0.02)
    if len(db) < 10:
        return False
    tail = wav[-int(tail_s * sr):].astype(np.float64)
    t_db = 10 * np.log10((tail ** 2).mean() + 1e-12)
    return bool(t_db < np.percentile(db, 90) - below)


def loudness_set(x, sr, target, tp_db=-1.5):
    g = 10 ** ((target - measure.lufs(x, sr)) / 20)
    y = x * g
    # true-peak guard: 4x oversampled peak, then a smooth look-ahead gain ride
    from scipy.signal import resample_poly
    lim = 10 ** (tp_db / 20)
    over = np.abs(resample_poly(y, 4, 1)).reshape(-1, 4).max(1)[: len(y)]
    if over.max() > lim:
        need = np.minimum(1.0, lim / np.maximum(over, 1e-9))
        w = int(0.005 * sr)
        from scipy.ndimage import minimum_filter1d, uniform_filter1d
        env = minimum_filter1d(need, size=2 * w + 1)
        env = uniform_filter1d(env, size=w)
        env = np.minimum(env, minimum_filter1d(need, size=3))
        y = y * env
    return y.astype(np.float32)


# ================================================================ render

def pick_engine(args, script):
    """VoxCPM2 on the Mac by default; openai when asked and a key is there."""
    name = getattr(args, "engine", None) or script.get("engine") or os.environ.get("NARRATE_ENGINE") or DEFAULT_ENGINE
    if name == "openai":
        import openai_voice
        if not openai_voice.available():
            log("no OPENAI_API_KEY: the VoxCPM2 voice reads this script")
            return "voxcpm"
    return name


def render(args):
    import soundfile as sf
    script = load_script(args.script)
    VOCAB[:] = list(script.get("names") or [])
    vid = args.voice or script.get("voice") or "atlas"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fallback = None
    if pick_engine(args, script) == "openai":
        try:
            return render_openai(args, script, vid, out)
        except Exception as e:  # noqa: BLE001 - the service down or refusing: the local voice reads it
            fallback = f"{type(e).__name__}: {str(e)[:200]}"
            log(f"OpenAI voice failed ({fallback}); the VoxCPM2 voice reads this script")
    chunks = make_chunks(script)
    t_all = time.time()
    eng = Engine()
    t_load = time.time() - t_all
    vmeta = next((v for v in voice_list() if v["id"] == vid), {})
    vpace = (vmeta.get("pace") or {}).get("wpm_artic")
    first = target_wpm(script["segments"][0], script)
    import subprocess
    import soundfile as sf_

    def tempo_feat(src, f):
        """Latents of `src` (a file, or audio at the model's rate) played at
        tempo x f. Only ever a prompt: the narration keeps its real latents."""
        if abs(f - 1) <= 0.01 and not isinstance(src, np.ndarray):
            return eng.ref_feat(src)
        tmp_in, tmp_out = out / "_tempo_in.wav", out / "_tempo.wav"
        if isinstance(src, np.ndarray):
            sf_.write(tmp_in, src, eng.sr)
            src = tmp_in
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-af", f"atempo={f:.4f}",
                        "-ar", "16000", "-ac", "1", str(tmp_out)], check=True)
        feat = eng.ref_feat(tmp_out)
        tmp_in.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
        return feat

    ref_tempo = 1.0
    if vpace and first:
        # the clip's tempo carries into the whole read: bring it to the asked pace first
        ref_tempo = min(1.15, max(0.80, first / vpace))
        if abs(ref_tempo - 1) <= 0.03:
            ref_tempo = 1.0
    log(f"voice {vid}: natural pace {vpace}, first target {first}, reference tempo x{ref_tempo:.3f}")
    ref = tempo_feat(voice_path(vid), ref_tempo)
    feats, prev = [], None
    prev_wav, prev_af = None, 1.0      # the kept chunk's audio and the tempo its prompt was shown at
    # level and pitch steps at the natural pauses heard so far: a join may not
    # step further than the read already does without one (check's rule)
    nat_l, nat_f = [2.0], [3.0]
    prev_real, ctx_tok = None, max(1, int(3.0 * eng.sr / eng.spt))   # the kept latents; 3 s of decoder context
    read_hz = []                       # voiced f0 of every kept chunk
    sent_gaps = []                     # the read's own sentence gaps inside chunks (s)
    current = first if ref_tempo != 1.0 else vpace
    takes_total = 0
    bandname = args.band or script.get("band") or "all"
    try:
        bb = json.load(open(BAND))["band"][bandname]["wpm_artic"]
        pace_lo, pace_hi = bb["p10"], bb["p90"]
        # the file's overall rate (pauses included) has its own band; a chunk
        # read without a breath above it pushes the whole read out (13 Nov hook)
        wpm_hi = json.load(open(BAND))["band"][bandname]["wpm"]["p90"]
        range_hi = json.load(open(BAND))["band"][bandname]["f0_range_st"]["p90"]
        range_lo = json.load(open(BAND))["band"][bandname]["f0_range_st"]["p10"]
    except Exception:
        pace_lo, pace_hi, range_hi, range_lo, wpm_hi = 0, 1e9, None, None, None
    t_gen = time.time()
    # Every kept chunk is saved as it lands (DIR/_state), so a render cut short
    # (a sleeping Mac, a killed job) resumes at the next chunk: same command,
    # same DIR. The state is removed once the narration is written.
    state = out / "_state"
    state.mkdir(exist_ok=True)
    import hashlib
    run_key = hashlib.sha1(json.dumps([vid, round(ref_tempo, 4), args.takes, args.steps, args.cfg, args.seed,
                                       bandname, bool(args.tempo_anchor)]).encode()).hexdigest()[:12]
    for ci, ch in enumerate(chunks):
        seg = script["segments"][ch["seg"]]
        direction, wpm = direction_for(seg, script, current)
        key = hashlib.sha1(f"{run_key}|{ci}|{ch['text']}|{direction}".encode()).hexdigest()[:16]
        sp = state / f"{ci:04d}.pt"
        if sp.exists():
            try:
                st = eng.torch.load(sp, weights_only=False)
            except Exception:
                st = None
            if st and st.get("key") == key:
                ch.update(st["ch"])
                feats.append(st["feat"])
                prev, current = (ch["text"], st["prev_feat"]), st["current"]
                prev_wav, prev_af = st.get("prev_wav"), st.get("prev_af", 1.0)
                nat_l, nat_f = st.get("nat_l", nat_l), st.get("nat_f", nat_f)
                prev_real = st["feat"]
                read_hz = st.get("read_hz", read_hz)
                sent_gaps = st.get("sent_gaps", sent_gaps)
                takes_total += len(st["ch"]["takes"])
                log(f"chunk {ci + 1}/{len(chunks)}: resumed from saved state")
                continue
        cands = []
        # The ladder: takes continuing the read; if none is heard whole with a
        # clean ending, takes from the voice clip alone (a bad prompt can trap
        # the continuation, 12 Nov: two chunks failed every continuation take);
        # then continuation with more guidance and more sampling steps.
        plan = [("cont", args.steps, args.cfg)] * args.takes + [("ref", args.steps, args.cfg)] * 3 + \
            [("cont", max(args.steps, 16), args.cfg + 0.5)] * 2
        # Pace is steered by the tempo of the prompt the take continues (the
        # model keeps the tempo it is shown) and by the direction. Both are
        # corrected after every take that misses, from its measured pace.
        cont_f, ref_f, cont_prev, ref_cur = 1.0, 1.0, prev, ref
        for k, (mode, steps, cfg) in enumerate(plan):
            # the next rung only when no take so far is heard whole, ends clean
            # AND sits in the band (13 Nov: a hook chunk ran 266-303 wpm on every
            # continuation take, all heard perfectly, and the read left the band)
            if k in (args.takes, args.takes + 3) and any(c["ok"] for c in cands):
                break
            takes_total += 1
            t0 = time.time()
            from_prev = mode == "cont" and prev is not None
            wav, feat = eng.generate(ch["text"], ref_cur, cont_prev if from_prev else None, direction,
                                     seed=args.seed + 7919 * ci + k, steps=steps, cfg=cfg)
            dur = len(wav) / eng.sr
            text, heard = listen_fast(wav, eng.sr)
            w = measure.wer(ch["text"], text)
            if w["wer"] > 0 and any(spoken._letters(t) is None for t in text.split()):
                # the listener wrote a number or a symbol: a model puts its text in spoken form before the take is
                # judged, as the gate does; a rule's guess at "-60°F" must not send a good take back to the ladder
                spf = spoken.spoken_form(text, ch["text"], VOCAB)
                if spf.get("ok"):
                    w2 = measure.wer(ch["text"], spf["text"])
                    if w2["wer"] < w["wer"]:
                        w = w2
            p = measure.pace(heard)
            pa = p["wpm_artic"]
            dev = abs(pa / wpm - 1) if (wpm and pa) else 0.0
            oob = 0.0 if (pa is None or pace_lo <= pa <= pace_hi) else min(abs(pa - pace_lo), abs(pa - pace_hi)) / pace_hi
            if wpm_hi and p["wpm"] and p["wpm"] > wpm_hi:
                oob += (p["wpm"] - wpm_hi) / wpm_hi
            # the take must end in its own silence: a chunk cut off mid-sound
            # leaves a dropout where the next chunk starts
            tail_ok = take_tail_ok(wav, eng.sr)
            hz = measure.f0_voiced(wav, eng.sr)
            rng = measure.pitch(wav, eng.sr, hz)["range_st"] if dur >= 4 else None
            poob = max(0.0, (rng - range_hi) / range_hi) if (rng and range_hi) else 0.0
            # the read's range so far with this take in it: a take whose register
            # drifts off the read widens the file's range though its own is fine
            # (13 Nov long-form: every chunk inside the band, the file 15.8 st)
            rng_read = measure.pitch(None, eng.sr, np.concatenate(read_hz + [hz]))["range_st"] if read_hz else None
            if rng_read and range_hi and rng_read > range_hi:
                poob += (rng_read - range_hi) / range_hi
            if rng and range_lo and rng < range_lo:
                poob += (range_lo - rng) / range_lo
            # the join this take would make with the kept chunk before it
            # heard as the narration will be: the end of the kept latents and this
            # take decoded together through the causal decoder (a take's own audio
            # can differ at its start: 13 Nov hook, 1.7 st alone, 8.9 st in place)
            jl = jf = None
            if prev_real is not None:
                pt = prev_real[-ctx_tok:]
                y = eng.decode_all([pt, feat])
                cut = int(pt.shape[0] * eng.spt)
                jl, jf = side_steps(y[:cut], y[cut:], eng.sr)
            # soft: the score prefers the smoother join; hard: a take is good only
            # inside check's own rule (natural max + 1), floored at 3 dB / 4 st
            # while the read has few pauses yet (13 Nov long-form: an early 1.6 st
            # natural max sent every chunk down the whole ladder)
            jx = max(0.0, (jl or 0) - (max(nat_l) + 0.5)) / 2 + max(0.0, (jf or 0) - (max(nat_f) + 0.5)) / 3
            j_bad = (jl or 0) > max(nat_l + [3.0]) + 1.0 or (jf or 0) > max(nat_f + [4.0]) + 1.0
            # a word missed or added is worse than any pace miss; leaving the band is worse than missing the target
            score = w["wer"] * 100 + dev + 3 * oob + 2 * poob + 2 * jx + (0 if tail_ok else 5)
            clean = w["wer"] == 0 and tail_ok
            cands.append({"take": k, "mode": mode, "wer": w["wer"], "errs": w["S"] + w["D"] + w["I"],
                          "dur": round(dur, 2), "wpm_artic": p["wpm_artic"], "range_st": rng, "tail_ok": tail_ok, "range_read": rng_read,
                          "clean": clean, "ok": bool(clean and oob == 0 and poob == 0 and not j_bad), "join": [jl, jf], "pace_dev": round(dev, 3), "score": round(score, 3),
                          "secs": round(time.time() - t0, 1), "_feat": feat, "_text": text, "_wav": wav,
                          "_heard": heard, "_hz": hz})
            log(f"chunk {ci + 1}/{len(chunks)} take {k + 1} ({mode}): wer {w['wer']:.3f} pace {p['wpm_artic']} "
                f"(want {wpm}) range {rng} tail {'ok' if tail_ok else 'CUT'} {dur:.1f}s audio in {time.time() - t0:.1f}s")
            cands[-1].update(direction=direction, prompt_tempo=round(cont_f if from_prev else ref_f, 3))
            if clean and oob == 0 and poob == 0 and not j_bad and dev <= args.pace_tol:
                break
            # Closed loop on pace: the next take is directed against what this
            # take measured (13 Nov hook: told "brisk" after a 196 wpm chunk, every
            # take ran 266-303; told "slow" against 275 it comes back to the band)
            # Only a take outside the band is steered, and by half the measured
            # ratio: the model's tempo answers the prompt loosely (13 Nov hook: a
            # full correction swung a chunk 182 -> 268 -> 155 wpm)
            if pa and wpm and oob > 0:
                direction, _ = direction_for(seg, script, pa)
                corr = min(1.15, max(0.87, (wpm / pa) ** 0.5))
                if from_prev and prev_wav is not None:
                    cont_f = min(1.35, max(0.70, cont_f * corr))
                    cont_prev = (prev[0], tempo_feat(prev_wav, prev_af * cont_f))
                elif not from_prev:
                    ref_f = min(1.35, max(0.70, ref_f * corr))
                    ref_cur = tempo_feat(voice_path(vid), min(1.5, max(0.6, ref_tempo * ref_f)))
        best = min(cands, key=lambda c: c["score"])
        ch.update(direction=best["direction"], wpm_target=wpm, heard=best["_text"], tokens=int(best["_feat"].shape[0]),
                  takes=[{k: v for k, v in c.items() if not k.startswith("_")} for c in cands], kept=best["take"])
        feats.append(best["_feat"])
        current = best["wpm_artic"] or current
        prev = (ch["text"], best["_feat"])
        prev_wav, prev_af, prev_real = best["_wav"], 1.0, best["_feat"]
        read_hz.append(best["_hz"])
        sent_gaps += [b["start"] - a["end"] for a, b in zip(best["_heard"], best["_heard"][1:])
                      if a["w"][-1:] in ".!?" and b["start"] - a["end"] >= measure.PAUSE_S]
        for sl, sf0 in inner_steps(best["_wav"], eng.sr, best["_heard"]):
            nat_l.append(sl)
            if sf0 is not None:
                nat_f.append(sf0)
        # Tempo anchor. The model continues the tempo of the audio it is shown,
        # so a read drifts: one fast chunk makes the next faster. When the kept
        # chunk is off the next chunk's pace, the next chunk is shown that same
        # chunk brought to the target tempo (only as its prompt; the narration
        # keeps the real latents, decoded in one pass).
        nxt = chunks[ci + 1] if ci + 1 < len(chunks) else None
        want_next = target_wpm(script["segments"][nxt["seg"]], script) if nxt else None
        if args.tempo_anchor and want_next and best["wpm_artic"]:
            f = min(1.25, max(0.80, want_next / best["wpm_artic"]))
            if abs(f - 1) > 0.06:
                prev = (ch["text"], tempo_feat(best["_wav"], f))
                prev_af = f
                current = want_next
                ch["anchor_tempo"] = round(f, 3)
        for c in cands:
            c.pop("_wav", None)
        keep = ("direction", "wpm_target", "heard", "tokens", "takes", "kept", "anchor_tempo")
        eng.torch.save({"key": key, "feat": best["_feat"], "prev_feat": prev[1], "current": current,
                        "prev_wav": prev_wav, "prev_af": prev_af, "nat_l": nat_l, "nat_f": nat_f, "read_hz": read_hz, "sent_gaps": sent_gaps,
                        "ch": {k: ch[k] for k in keep if k in ch}}, state / f"{ci:04d}.tmp")
        os.replace(state / f"{ci:04d}.tmp", sp)
        eng.free()
    t_gen = time.time() - t_gen

    t_dec = time.time()
    x = eng.decode_all(feats)
    sr = eng.sr
    t_dec = time.time() - t_dec
    eng.close()
    if sr != OUT_SR:
        x, sr = measure.resample(x, sr, OUT_SR), OUT_SR
    spt = eng.spt * OUT_SR / eng.sr
    # chunk boundaries in samples, then pause lengths set inside the silences
    pos = np.cumsum([c["tokens"] for c in chunks])[:-1] * spt
    db, _ = _frames(x, sr)
    speech = np.percentile(db, 90)
    floor_db = max(speech - 38, np.percentile(db, 3) + 6)
    shift = 0
    bounds = []
    # a chunk boundary inside a segment is a sentence end: it gets at least the
    # read's own sentence gap (13 Nov long-form: a 0.1 s gap ran two sentences
    # together and the onset landed as a 40 dB step at the join)
    sent_gap = float(np.median(sent_gaps)) if sent_gaps else 0.45
    for ci, b in enumerate(pos):
        t = (b + shift) / sr
        want = chunks[ci]["pause_after"]
        if want is None:
            _, _, had0 = set_pause(x, sr, t, None, floor_db)
            if had0 < 0.6 * sent_gap:
                want = round(sent_gap, 3)
        x, d, had = set_pause(x, sr, t, want, floor_db)
        bounds.append({"after_chunk": ci, "t_gen": round(b / sr, 3), "pause_had": round(had, 3),
                       "pause_set": want, "t": None, "_shift_before": shift})
        shift += d
        bounds[-1]["t"] = round((b + shift - (d / 2 if d else 0)) / sr, 3)
    rep = {"engine": "voxcpm", "engine_fallback": fallback, "script": str(args.script), "voice": vid, "band": bandname,
           "voice_pace": vpace, "ref_tempo": round(ref_tempo, 4), "chunks": chunks, "takes_total": takes_total,
           "settings": {"takes": args.takes, "steps": args.steps, "cfg": args.cfg, "seed": args.seed,
                        "pace_tol": args.pace_tol, "chunk_words": CHUNK_WORDS, "model": MODEL_ID,
                        "listener": ASR_MODEL}}
    deliver(x, sr, script, chunks, bounds, rep, args, out, t_all, gen_s=t_gen + t_dec, load_s=t_load,
            timing={"generate_s": round(t_gen, 1), "decode_s": round(t_dec, 1)})
    import shutil
    shutil.rmtree(state, ignore_errors=True)


def deliver(x, sr, script, chunks, bounds, rep, args, out, t_all, gen_s, load_s=0.0, timing=None):
    """The same finish for every engine: pauses set from the script and the reference band (pauses.py),
    the mastering chain (master.py: EQ, compression, presence, de-ess, -14 LUFS), then every word timed
    on the finished signal. `bounds`: chunk joins, "t" in seconds of x. --no-master keeps the old finish
    (pauses as generated, loudness only)."""
    import soundfile as sf
    bandname = rep.get("band") or "03"
    full = " ".join(c["text"] for c in chunks)
    t_lis = time.time()
    edits, mrep = [], None
    if not getattr(args, "no_master", False):
        import master as MS
        import pauses as PZ
        _, heard = listen_long(x, sr)
        words0 = map_words(full, heard)
        me = sys.modules[__name__]
        x, edits = PZ.shape(x, sr, script["segments"], words0, me, bandname if bandname in PZ.CAP else "03")
        for b in bounds:   # a join moves with every pause edit before it
            b["t"] = round(PZ.remap(b["t"], edits), 3)
        target = args.lufs if args.lufs is not None else MS.LUFS
        x, mrep = MS.master(x, sr, band=bandname, lufs_target=target,
                            ride=PZ.stress_spans(words0, script["segments"], edits, me), words=PZ.remap_words(words0, edits))
        sr = MS.SR
    else:
        target = args.lufs if args.lufs is not None else band_target()
        x = loudness_set(x, sr, target)
    sf.write(out / "narration.wav", x, sr, subtype="PCM_24")

    # words, phrases and cut points from the finished signal
    _, heard = listen_long(x, sr)
    words = map_words(full, heard)
    seg_of = [si for si, s in enumerate(script["segments"]) for _ in s["text"].split()]
    k = 0
    for ci, c in enumerate(chunks):
        n = len(c["text"].split())
        for w in words[k:k + n]:
            w["chunk"] = ci
        c["start"], c["end"] = words[k]["start"], words[k + n - 1]["end"]
        k += n
    for w, si in zip(words, seg_of):
        w["seg"] = si
    for b in bounds:   # the boundary is the middle of the silence where the chunks meet
        g = measure.gap_at(x, sr, b["t"])
        if g:
            b["t"], b["gap"] = round((g[0] + g[1]) / 2, 3), [round(g[0], 3), round(g[1], 3)]
        b.pop("_shift_before", None)
    phrases, cur = [], [words[0]]
    for a, z in zip(words, words[1:]):
        if z["start"] - a["end"] >= measure.PAUSE_S or a["seg"] != z["seg"]:
            phrases.append(cur)
            cur = []
        cur.append(z)
    phrases.append(cur)
    cuts = [round((a["end"] + z["start"]) / 2, 3) for a, z in zip(words, words[1:])
            if z["start"] - a["end"] >= measure.PAUSE_S]
    t_lis = time.time() - t_lis
    dur = len(x) / sr
    json.dump({"voice": rep["voice"], "duration": round(dur, 2), "words": words,
               "phrases": [{"text": " ".join(w["w"] for w in p), "start": p[0]["start"], "end": p[-1]["end"],
                            "seg": p[0]["seg"]} for p in phrases],
               "cut_points": cuts}, open(out / "words.json", "w"), indent=1)
    rep.update(duration_s=round(dur, 2), lufs_target=target, boundaries=bounds, pauses=edits, master=mrep, names=list(VOCAB))
    rep["timing"] = {"load_s": round(load_s, 1), **(timing or {"generate_s": round(gen_s, 1)}),
                     "listen_s": round(t_lis, 1), "total_s": round(time.time() - t_all, 1),
                     "render_s_per_min_speech": round(60 * gen_s / dur, 1),
                     "end_to_end_s_per_min_speech": round(60 * (time.time() - t_all - load_s) / dur, 1)}
    json.dump(rep, open(out / "render.json", "w"), indent=1, default=_jsonable)
    log(f"done: {dur:.1f}s of speech, {rep['timing']}")
    print(json.dumps({"narration": str(out / "narration.wav"), "words": str(out / "words.json"),
                      "duration_s": round(dur, 2), "engine": rep["engine"], "timing": rep["timing"]}))


def render_openai(args, script, vid, out):
    """The OpenAI voice (openai_voice.py): chunks of consecutive segments, each read with the voice's
    persona and every line's direction; takes heard by the take listener until one is heard whole with
    its pace inside the reference band; then the shared finish (deliver)."""
    import openai_voice as OV
    from concurrent.futures import ThreadPoolExecutor
    t_all = time.time()
    bandname = args.band or script.get("band") or "all"
    vmeta = next((v for v in voice_list() if v["id"] == vid), {})
    oc = vmeta.get("openai") or {}
    voice = oc.get("voice") or OV.DEFAULT_VOICE
    persona = oc.get("persona") or OV.DEFAULT_PERSONA
    segs = script["segments"]
    groups = OV.chunks(script, args.chunk_words or OV.CHUNK_WORDS)
    chunks = [{"seg": g[0], "segs": g, "text": " ".join(segs[i]["text"] for i in g),
               "pause_after": float(segs[g[-1]].get("pause_after") or 0.0)} for g in groups]
    instr = [OV.instructions(persona, [segs[i] for i in g], script) for g in groups]
    try:
        bb = json.load(open(BAND))["band"][bandname]["wpm_artic"]
        lo, hi = bb["p10"], bb["p90"]
    except Exception:
        lo, hi = 0, 1e9
    want = target_wpm(segs[0], script) or (lo + hi) / 2
    try:
        import spoken     # the listener's written numbers in spoken form, when the tool has it
    except ImportError:
        spoken = None
    names = list(globals().get("VOCAB") or [])

    said = [" ".join(OV.styled(segs[i]) for i in g) for g in groups]

    def gen(ci):
        x, s = OV.speech(said[ci], voice, instr[ci])
        return ci, OV.trim(x, s), s

    cands = [[] for _ in chunks]
    todo, sr, takes_total, t_gen = list(range(len(chunks))), None, 0, time.time()
    for rnd in range(max(1, args.takes)):
        if not todo:
            break
        with ThreadPoolExecutor(min(6, len(todo))) as ex:
            res = list(ex.map(gen, todo))
        for ci, x, s in res:
            sr = s
            takes_total += 1
            text, heard = listen_fast(x, s)
            w = measure.wer(chunks[ci]["text"], text)
            errs = w["S"] + w["D"] + w["I"]
            if errs and spoken is not None:
                try:
                    sp = spoken.spoken_form(text, chunks[ci]["text"], names)
                    if sp.get("ok"):
                        w2 = measure.wer(chunks[ci]["text"], sp["text"])
                        errs = min(errs, w2["S"] + w2["D"] + w2["I"])
                except Exception:  # noqa: BLE001 - the rule figure stands
                    pass
            pa = measure.pace(heard)["wpm_artic"]
            oob = 0.0 if (pa is None or lo <= pa <= hi) else min(abs(pa - lo), abs(pa - hi)) / hi
            cands[ci].append({"take": rnd, "wer": w["wer"], "errs": errs, "wpm_artic": pa, "oob": round(oob, 3),
                              "dur": round(len(x) / s, 2), "heard": text, "_x": x})
            log(f"chunk {ci + 1}/{len(chunks)} take {rnd + 1}: errors {errs} (rule wer {w['wer']:.3f}) pace {pa} "
                f"{'in' if not oob else 'OUT of'} band, {len(x) / s:.1f}s")
        # a chunk is done once a take is heard whole and reads inside the band
        todo = [ci for ci in range(len(chunks)) if not any(c["errs"] == 0 and c["oob"] == 0 for c in cands[ci])]
    t_gen = time.time() - t_gen
    parts, bounds, t = [], [], 0.0
    for ci, ch in enumerate(chunks):
        best = min(cands[ci], key=lambda c: (c["errs"], c["oob"], abs((c["wpm_artic"] or want) / want - 1)))
        ch.update(kept=best["take"], heard=best["heard"], takes=[{k: v for k, v in c.items() if not k.startswith("_")} for c in cands[ci]])
        parts.append(best["_x"])
        t += len(best["_x"]) / sr
        if ci < len(chunks) - 1:
            gap = max(ch["pause_after"], 0.25)
            parts.append(np.zeros(int(gap * sr), np.float32))
            bounds.append({"after_chunk": ci, "t": round(t + gap / 2, 3), "pause_set": gap})
            t += gap
    x = np.concatenate(parts).astype(np.float32)
    minutes = sum(c["dur"] for cc in cands for c in cc) / 60
    rep = {"engine": "openai", "model": OV.MODEL, "openai_voice": voice, "persona": persona, "script": str(args.script),
           "voice": vid, "band": bandname, "chunks": chunks, "takes_total": takes_total,
           "usd_estimate": round(minutes * OV.USD_PER_MIN, 4),
           "settings": {"takes": args.takes, "chunk_words": args.chunk_words or OV.CHUNK_WORDS, "pace_band": [lo, hi],
                        "take_listener": "faster-whisper " + CHECK_ASR, "listener": ASR_MODEL}}
    deliver(x, sr, script, chunks, bounds, rep, args, out, t_all, gen_s=t_gen)


def _jsonable(o):
    """numpy scalars in the report become plain numbers"""
    return o.item() if hasattr(o, "item") else str(o)


def band_target():
    try:
        return float(json.load(open(BAND))["band"]["all"]["voice_lufs"]["median"])
    except Exception:
        return -15.0


# ================================================================ check

def check(args):
    import soundfile as sf
    d = Path(args.dir)
    rep = json.load(open(d / "render.json"))
    wj = json.load(open(d / "words.json"))
    x, sr = measure.load_mono(d / "narration.wav")
    script = " ".join(c["text"] for c in rep["chunks"])
    VOCAB[:] = list(rep.get("names") or [])
    if getattr(args, "names", None):
        VOCAB[:] = json.load(open(args.names)) if args.names.endswith(".json") else [n.strip() for n in args.names.split(",")]
    # 1. words. The gate listener is whisper large-v3 (mlx), which never chose a
    # take; the take listener (large-v3-turbo) is reported beside it. Both hear
    # with the story's names. The script is the writer model's spoken form; the
    # listener's written form is put in its spoken form by a model (spoken.py),
    # never by rule, and the gate compares the two. The rule-based figure is kept
    # beside it for comparison only.
    text1, words1 = listen_long(x, sr)
    w_rule = measure.wer(script, text1)
    sp = spoken.spoken_form(text1, script, VOCAB)
    w1 = measure.wer(script, sp["text"]) if sp.get("ok") else dict(w_rule)
    text2, words2 = listen_long(x, sr, listen_fast)
    w2 = measure.wer(script, text2)
    # 2. voice against the reference band (timings from the same listener the band used)
    prof = measure.voice_profile(x, sr, words1)
    band = json.load(open(BAND))["band"][args.band]
    vals = {"wpm": prof["pace"]["wpm"], "wpm_artic": prof["pace"]["wpm_artic"],
            "f0_range_st": prof["pitch"]["range_st"], "voice_lufs": prof["loudness"]["lufs"]}
    inband = {k: {"value": v, "band": [band[k]["p10"], band[k]["p90"]],
                  "ok": v is not None and band[k]["p10"] <= v <= band[k]["p90"]} for k, v in vals.items()}
    if rep.get("master") and rep.get("lufs_target") is not None:
        # the mastering chain sets the voice to its own target (-14 LUFS, the film's level); the
        # reference stems sit 1.4 dB under their mixes because a music bed shares the level
        lt = rep["lufs_target"]
        inband["voice_lufs"].update(target=lt, ok=vals["voice_lufs"] is not None and abs(vals["voice_lufs"] - lt) <= 0.7)
    # 3. joins: every chunk boundary, against the natural sentence gaps inside chunks
    words = wj["words"]
    natural = []
    for a, z in zip(words, words[1:]):
        if a["chunk"] == z["chunk"] and a["w"][-1:] in ".!?" and z["start"] > a["end"]:
            natural.append((a["end"] + z["start"]) / 2)
    bts = [b["t"] for b in rep["boundaries"]]
    j = measure.join_metrics(x, sr, bts, natural)
    nat = j["summary"]

    def clean(b):
        why = []
        if b.get("energy_jump_pct", 0) > 99:
            why.append("energy step above the file's p99")
        if b.get("spec_flux_pct", 0) > 99:
            why.append("spectral flux above the file's p99")
        if b.get("sample_step", 0) > 1.0:
            why.append("sample step above the file's p99.9 (click)")
        nf = (nat.get("floor_step_db") or {}).get("natural")
        if (b.get("floor_step_db") or 0) > max(3.0, nf["max"] if nf else 0.0):
            why.append("noise floor step > 3 dB and beyond every natural gap")
        for k, slack in (("level_step_db", 1.0), ("f0_step_st", 1.0)):
            ref = (nat.get(k) or {}).get("natural")
            if b.get(k) is not None and ref and b[k] > ref["max"] + slack:
                why.append(f"{k} {b[k]} beyond natural gaps (max {ref['max']})")
        return why

    for b in j["boundaries"]:
        b["artefacts"] = clean(b)
    res = {"dir": str(d), "voice": rep["voice"], "duration_s": rep["duration_s"],
           "wer": {"gate_listener": "whisper-large-v3 (mlx)", **w1,
                   "spoken_form": {k: sp.get(k) for k in ("ok", "model", "guard", "usd", "why")},
                   "errors": measure.wer_detail(script, sp["text"] if sp.get("ok") else text1)},
           "wer_rule_normalised": {**w_rule, "errors": measure.wer_detail(script, text1),
                                   "note": "the listener's written form expanded by inflect and patterns; comparison only"},
           "names": list(VOCAB), "heard": text1, "heard_spoken": sp.get("text"),
           "wer_take_listener": {"listener": "faster-whisper " + CHECK_ASR, **w2,
                                 "errors": measure.wer_detail(script, text2)},
           "voice": prof, "band": args.band, "in_band": inband,
           "joins": {"count": len(bts), "with_artefact": sum(1 for b in j["boundaries"] if b["artefacts"]), **j},
           "timing": rep["timing"]}
    res["gate"] = {"wer_under_1pct": w1["wer"] < 0.01,
                   "pace_in_band": inband["wpm"]["ok"] and inband["wpm_artic"]["ok"],
                   "pitch_range_in_band": inband["f0_range_st"]["ok"],
                   "loudness_in_band": inband["voice_lufs"]["ok"],
                   "no_join_artefact": res["joins"]["with_artefact"] == 0}
    res["gate"]["pass"] = all(res["gate"].values())
    json.dump(res, open(d / "check.json", "w"), indent=1)
    print(json.dumps({"gate": res["gate"], "wer": w1["wer"], "wer_take_listener": w2["wer"], "in_band": inband,
                      "joins": [res["joins"]["count"], res["joins"]["with_artefact"]]}, indent=1))


# ================================================================ voices

GENDER_F0 = {"male": (85, 175), "female": (165, 300)}


def voice_fit(pit, args, pace=None):
    """How far a designed take sits from a reference narrator: melody range
    against the band median, articulation rate against the band median (the
    clip's tempo carries into everything the voice says), f0 against the
    asked voice's register."""
    meta = json.loads(args.meta) if getattr(args, "meta", None) else {}
    try:
        b = json.load(open(BAND))["band"][meta.get("band", "all")]
        med, pmed = float(b["f0_range_st"]["median"]), float(b["wpm_artic"]["median"])
    except Exception:
        med, pmed = 13.0, 192.0
    if not pit.get("range_st"):
        return 99.0
    d = abs(pit["range_st"] - med)
    if pace and pace.get("wpm_artic"):
        d += abs(pace["wpm_artic"] - pmed) / 8.0
    lo, hi = GENDER_F0.get(meta.get("gender"), (0, 1000))
    f = pit["f0_med_hz"]
    d += 0 if lo <= f <= hi else 12 * abs(math.log2(f / (lo if f < lo else hi)))
    return round(d, 2)


def make_voice(args, eng=None):
    """Design a new voice from words alone (no one's recording), keep the
    take the listener hears best, store it as the voice's reference clip."""
    import soundfile as sf
    eng = eng or Engine()
    VOICES.mkdir(exist_ok=True)
    best = None
    for k in range(args.takes):
        eng.torch.manual_seed(args.seed + k)
        from voxcpm.model.utils import next_and_close
        wav, _, _ = next_and_close(eng.m._generate_with_prompt_cache(
            target_text=f"({args.describe}){args.line}", prompt_cache=None, inference_timesteps=10,
            cfg_value=2.0, retry_badcase=True))
        wav = wav.squeeze().float().numpy()
        text, heard = listen_fast(wav, eng.sr)
        w = measure.wer(args.line, text)
        pit = measure.pitch(wav, eng.sr)
        log(f"take {k}: wer {w['wer']} f0 {pit} pace {measure.pace(heard)['wpm_artic']}")
        cand = (w["wer"], voice_fit(pit, args, measure.pace(heard)), k, wav, pit, heard)
        if best is None or cand[:2] < best[:2]:
            best = cand
        if getattr(args, "preview_dir", None):
            Path(args.preview_dir).mkdir(parents=True, exist_ok=True)
            sf.write(Path(args.preview_dir) / f"{args.id}_take{k}.wav", wav, eng.sr)
    wer_, _, k, wav, pit, heard = best
    y16 = measure.resample(wav, eng.sr, 16000)
    sf.write(VOICES / f"{args.id}.flac", y16, 16000)
    meta = {"describe": args.describe, "line": args.line, "take": k, "seed": args.seed + k, "wer": wer_,
            "f0_med_hz": pit["f0_med_hz"], "range_st": pit["range_st"], "pace": measure.pace(heard),
            "made_with": MODEL_ID + " voice design (description only; no person's recording)"}
    meta.update(json.loads(args.meta) if getattr(args, "meta", None) else {})
    (VOICES / f"{args.id}.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({"id": args.id, **meta}))


def main():
    ap = argparse.ArgumentParser(prog="narrate", description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("voices")
    r = sp.add_parser("render")
    r.add_argument("script")
    r.add_argument("--out", required=True)
    r.add_argument("--voice")
    r.add_argument("--takes", type=int, default=3)
    r.add_argument("--steps", type=int, default=10)
    r.add_argument("--cfg", type=float, default=2.0)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--pace-tol", type=float, default=0.10)
    r.add_argument("--no-tempo-anchor", dest="tempo_anchor", action="store_false",
                   help="continue every chunk from the kept chunk as generated")
    r.add_argument("--lufs", type=float, help="the narration's loudness (default: the mastering chain's -14 LUFS)")
    r.add_argument("--engine", choices=["openai", "voxcpm"], help="default: " + DEFAULT_ENGINE + " (NARRATE_ENGINE)")
    r.add_argument("--chunk-words", type=int, help="openai: words per request (default 60)")
    r.add_argument("--no-master", action="store_true", help="pauses as generated and loudness only (the pre-13 Nov finish)")
    r.add_argument("--band", choices=["03", "04", "all"], help="reference band the takes must sit in")
    c = sp.add_parser("check")
    c.add_argument("dir")
    c.add_argument("--band", default="all", choices=["03", "04", "all"])
    c.add_argument("--names", help="the story's names (comma list or a .json list); default: render.json's names")
    v = sp.add_parser("make-voice")
    v.add_argument("--id", required=True)
    v.add_argument("--describe", required=True)
    v.add_argument("--line", required=True)
    v.add_argument("--takes", type=int, default=4)
    v.add_argument("--seed", type=int, default=11)
    v.add_argument("--meta")
    v.add_argument("--preview-dir")
    vs = sp.add_parser("make-voices", help="design every voice in a JSON list with one model load")
    vs.add_argument("spec")
    vs.add_argument("--takes", type=int, default=4)
    vs.add_argument("--preview-dir")
    a = ap.parse_args()
    if a.cmd == "voices":
        for v in voice_list():
            print(f"{v['id']:<8} {v.get('f0_med_hz')} Hz  range {v.get('range_st')} st  {v['describe']}")
    elif a.cmd == "render":
        render(a)
    elif a.cmd == "check":
        check(a)
    elif a.cmd == "make-voices":
        eng = Engine()
        for v in json.load(open(a.spec)):
            ns = argparse.Namespace(takes=a.takes, seed=v.get("seed", 11), preview_dir=a.preview_dir,
                                    meta=json.dumps(v.get("meta", {})), **{k: v[k] for k in ("id", "describe", "line")})
            make_voice(ns, eng)
    else:
        make_voice(a)


if __name__ == "__main__":
    main()
