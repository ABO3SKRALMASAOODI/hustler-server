"""The brand finish: the product's own watermark and end card on a Mac-rendered programme.

Nothing is redrawn here. The mark's pixels and place come from the worker's renderer
(watermark_image, watermark_geometry) and the end card's join from outro_card_parts, so a
film from a workflow closes exactly as a Valmera export does. The end card and the mark are
untouchable: check() measures both on the delivered file.

The end accent (owner, 14 Nov: make people look at the mark and come to the site). The card used to open on digital
silence. Now, without touching a pixel of the mark or the card: one sting from the product's own licensed library lands
on the card's reveal, on the programme music's beat when its grid has one there (onsets and tempo, measured), and the
programme's last moments ring under the card (the bed ducks and its reverberant tail fades out across the card), with
the card's loudness inside the programme's range and its true peak under -1 dBTP. Signals only: no model, no words.
No motion accent on the mark: at the sting the card's dissolve is already laying the card (which carries the mark's own
robot and name) over it, so a pulse there would fight the card's reveal rather than read.
"""
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V = "/Users/masaoodi/Documents/Valmera"
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = os.environ.get("SHELL_FFMPEG", BIN + "/ffmpeg"), os.environ.get("SHELL_FFPROBE", BIN + "/ffprobe")


def worker_dir():
    """The worker these films are made with: one that holds this week's engines (the scene engine, the footage
    engine, the film's music). The checkout beside this plugin when it has them; else the synced main checkout
    (.valmera/sync_skill.sh keeps worktrees/skill-main at main). The installed copy under hustler-server sits beside
    an older worker without the engines, and the Codex cache has no worker beside it: both reach the synced one."""
    if os.environ.get("VALMERA_WORKER"):
        return os.environ["VALMERA_WORKER"]
    d = HERE
    for _ in range(6):
        d = os.path.dirname(d)
        w = os.path.join(d, "worker")
        if all(os.path.exists(os.path.join(w, n)) for n in ("renderer.py", "scene/run.py", "footage/run.py", "music_film.py")):
            return w
    return f"{V}/worktrees/skill-main/worker"


def _renderer():
    w = worker_dir()
    if w not in sys.path:
        sys.path.insert(0, w)
    import config  # noqa: F401
    import renderer
    return renderer


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-count_packets", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,nb_read_packets:format=duration",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, den = v["r_frame_rate"].split("/")
    return dict(w=int(v["width"]), h=int(v["height"]), fps=float(n) / float(den), frames=int(v.get("nb_read_packets") or 0),
                dur=float(d["format"]["duration"]), audio=any(s["codec_type"] == "audio" for s in d["streams"]))


# The end accent's numbers. Levels are K-weighted (score.sound_signal's ruler, the one the library's hit_lufs is on),
# taken against the programme's own closing loudness (its last CLOSE_S), so a quiet film gets a quiet sting.
SR = 48000
CLOSE_S = 3.0               # the programme's closing loudness: its last 3 s (a short-term window)
STING_UNDER_DB = 1.0        # the sting's loudest 200 ms this far under the closing loudness: a landing, not a jump
STING_ROLES = ("impact", "hit", "braam")     # the library's landing roles; the signal decides among them
STING_ATTACK_S = 0.09       # the sting must land on its frame: its peak within 90 ms of its start
STING_DECAY_S = 1.4         # and ring into the card
DUCK_DB = 6.0               # the bed under the sting
DUCK_RAMP_S = 0.05
SEND_S = 1.2                # the programme's last moments that ring on under the card
RING_UNDER_DB = 5.0         # the ring's first second this far under the closing loudness (the references' bed sits 5.3 dB under the voice)
RING_RT60_S = 3.2
RING_HOLD_S = 1.0           # the ring holds this long into the card, then fades to nothing at its far edge
TP_CEIL_DB = -1.6           # true peak of the closing section (the AAC encode adds a few tenths; the bar is -1 dBTP)
BEAT_WIN_S = 8.0            # the beat grid is read on the programme's last 8 s
BPM_LO, BPM_HI = 70.0, 180.0
BEAT_CLARITY = 0.2          # the onset envelope's autocorrelation at the beat over its energy (speech alone reads 0.07-0.14, a clear beat 0.2-0.5): below this, no clear beat
BEAT_ON_GRID = 0.3          # and the share of onset energy on the grid above chance (speech 0.12-0.14, a beat 0.3-1)
BEAT_MIN_BEATS = 6


def _score_signal():
    _renderer()                                   # puts the worker on the path
    from score import sound_signal
    return sound_signal


def _pcm(path, start=0, end=None, total=None, channels=2):
    """A stream's samples [start, end) at 48 kHz, (n, channels) float32, by the very chain finish() lays the
    programme with (so the head the graph keeps and the tail laid here join on the same samples). `total` pads a
    short stream with silence to exactly that many samples."""
    chain = f"aresample={SR},aformat=sample_fmts=fltp:channel_layouts={'stereo' if channels == 2 else 'mono'}"
    if total is not None:
        chain += f",apad=whole_len={int(total)}"
    trim = [f"start_sample={int(start)}"] + ([f"end_sample={int(end)}"] if end is not None else [])
    chain += ",atrim=" + ":".join(trim)
    r = subprocess.run([FF, "-v", "error", "-i", path, "-filter_complex", f"[0:a]{chain}[a]", "-map", "[a]",
                        "-f", "f32le", "-acodec", "pcm_f32le", "-"], capture_output=True, check=True)
    import numpy as np
    return np.frombuffer(r.stdout, np.float32).reshape(-1, channels).copy()


def _k_db(x):
    """K-weighted level of the samples, LUFS-like (mono mean, ungated): the library's hit_lufs ruler."""
    import numpy as np
    S = _score_signal()
    if len(x) < S.NFFT:
        return -99.0
    kl = S.k_level(S.power(x))
    return float(10 * np.log10(max(float(np.mean(10 ** (kl / 10))), 1e-12)))


def _true_peak_db(x):
    """True peak, dBTP: the 4x oversampled peak (BS.1770 annex 2)."""
    import numpy as np
    from scipy.signal import resample_poly
    if not len(x):
        return -120.0
    y = resample_poly(np.asarray(x, np.float64), 4, 1, axis=0)
    return float(20 * np.log10(max(float(np.abs(y).max()), 1e-9)))


def card_reveal_frame(card, fps):
    """The end card's first frame with its type on it, read from its pixels: the first frame whose bright ink reaches a
    quarter of the settled card's (the motion card opens on black and lands its first line at frame 3; the poster is
    whole at once)."""
    import numpy as np
    R = _renderer()
    import config
    if card.lower().endswith(R.IMAGE_EXTS):
        return 0
    settle = math.ceil(config.ENDCARD_SETTLE_S * fps - 1e-6)
    n = settle + 1
    r = subprocess.run([FF, "-v", "error", "-i", card, "-frames:v", str(n), "-vf", f"fps={fps},scale=180:320",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8)[: n * 180 * 320].reshape(-1, 320, 180)
    ink = (a > 128).mean(axis=(1, 2))
    if not len(ink) or ink[-1] <= 0:
        return 0
    return int(np.argmax(ink >= 0.25 * ink[-1]))


def beat_grid(x, sr=SR):
    """The music's beat grid on mono samples x, by signal: a spectral-flux onset envelope (5 ms hops), its
    autocorrelation for the tempo (70-180 BPM), then the period and phase whose grid sits on the most onset energy.
    {period_s, phase_s (a beat's time from x's start), bpm, clarity, on_grid} when the beat is clear, else None
    (speech alone, a pad, silence)."""
    import numpy as np
    S = _score_signal()
    x = np.asarray(x, np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < sr * 2:
        return None
    P = S.power(x)
    hz = np.fft.rfftfreq(S.NFFT, 1.0 / sr)
    band = (hz >= 30) & (hz < 8000)
    L = np.log1p(1e4 * P[:, band])
    env = np.maximum(np.diff(L, axis=0), 0).sum(axis=1)
    env = np.concatenate([[0.0], env])
    hop = S.HOP / sr
    k = max(1, int(round(0.25 / hop)))
    env = np.maximum(env - np.convolve(env, np.ones(k) / k, mode="same"), 0)
    if env.max() <= 0:
        return None
    e = env - env.mean()
    lo, hi = int(60.0 / BPM_HI / hop), int(math.ceil(60.0 / BPM_LO / hop))
    n = len(e)
    if hi * 2 >= n:
        return None
    ac = np.array([float(np.dot(e[:n - l], e[l:])) / (n - l) for l in range(hi + 1)])
    if ac[0] <= 0:
        return None
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    clarity = float(ac[lag] / ac[0])
    if clarity < BEAT_CLARITY:
        return None
    t = np.arange(n) * hop
    best = (-1.0, None, None)
    for per in lag * hop * (1 + np.arange(-0.03, 0.0301, 0.0025)):
        ph = np.arange(0, per, hop)
        idx = np.rint((ph[:, None] + np.arange(0, t[-1], per)[None, :]) / hop).astype(int)
        val = np.where(idx < n, env[np.minimum(idx, n - 1)], 0.0).sum(axis=1) / max(1, idx.shape[1])
        i = int(np.argmax(val))
        if val[i] > best[0]:
            best = (float(val[i]), float(per), float(ph[i]))
    if t[-1] / best[1] < BEAT_MIN_BEATS:
        return None
    # how much of the onset energy sits on the grid (within 20 ms of a beat), over what any grid would catch by chance
    w = int(round(0.02 / hop))
    near = np.convolve(env, np.ones(2 * w + 1), mode="same")
    idx = np.rint((best[2] + np.arange(0, t[-1], best[1])) / hop).astype(int)
    share = float(near[idx[idx < n]].sum() / max(float(env.sum()), 1e-12))
    chance = (2 * w + 1) * hop / best[1]
    on_grid = (share - chance) / (1 - chance)
    if on_grid < BEAT_ON_GRID:
        return None
    return dict(period_s=round(best[1], 5), phase_s=round(best[2], 4), bpm=round(60.0 / best[1], 1),
                clarity=round(clarity, 3), on_grid=round(on_grid, 3))


def pick_sting(used=()):
    """The sting: a recording of the product's own library (the one SND3 designs with), chosen by its signal. Among the
    landing roles, the ones that land on their frame (peak within STING_ATTACK_S of the start) and ring into the card
    (decay STING_DECAY_S or longer), the one whose energy sits most in the band a phone plays (120-2000 Hz), never one
    the film already used. None when the library offers none."""
    S = _score_signal()
    lib = os.path.join(worker_dir(), "sound_library")
    try:
        man = json.load(open(os.path.join(lib, "manifest.json")))
    except (OSError, ValueError):
        return None
    used = {os.path.basename(str(u)) for u in used or ()} | {str(u) for u in used or ()}
    rows = []
    for x in man:
        if x.get("role") not in STING_ROLES or not os.path.exists(os.path.join(lib, x["file"])):
            continue
        y = _pcm(os.path.join(lib, x["file"]), channels=1)[:, 0]
        d = S.describe(y)
        if not d or d["attack_s"] > STING_ATTACK_S or d["decay_s"] < STING_DECAY_S or d["kind"] != "impact":
            continue
        mid = sum(10 ** (b / 10) for b in d["bands"][1:4])
        rows.append((-mid, x["id"], x, d))
    rows.sort(key=lambda r: (r[0], r[1]))
    fresh = [r for r in rows if r[1] not in used and r[2]["file"] not in used]
    pick = (fresh or rows or [None])[0]
    if not pick:
        return None
    return dict(id=pick[1], path=os.path.join(lib, pick[2]["file"]), desc=pick[3], license=pick[2].get("license"),
                title=pick[2].get("title"), author=pick[2].get("author"))


def sting_time(prog_s, fps, j, reveal_k, settle_k, grid):
    """Where the sting lands: on the beat nearest the card's reveal, inside the reveal (from one frame before the card's
    type appears to the frame the card has settled); with no clear beat there, on the reveal itself."""
    f0 = round(prog_s * fps) - j                  # the card's first frame over the programme
    t_rev, t_set = (f0 + reveal_k) / fps, (f0 + settle_k) / fps
    if grid:
        per, ph = grid["period_s"], grid["abs_phase_s"]
        k0 = math.floor((t_rev - 1.0 / fps - ph) / per)
        beats = [ph + (k0 + i) * per for i in range(4)]
        inside = [b for b in beats if t_rev - 1.0 / fps - 1e-6 <= b <= t_set + 1e-6]
        if inside:
            return min(inside, key=lambda b: abs(b - t_rev)), "beat", t_rev
    return t_rev, "reveal", t_rev


def accent_tail(programme, workdir, prog_s, fps, j, outro_s, used=(), has_audio=True):
    """The closing section's sound, as a 48 kHz stereo WAV laid from `cut` samples on: the programme's own samples
    there (the bed ducked under the sting, the old 0.25 s fade at the join), the sting on its beat, and the
    programme's last moments ringing on under the card. Returns (wav, cut_sample, info)."""
    import numpy as np
    from scipy.signal import fftconvolve, lfilter
    R = _renderer()
    import config
    total = int(round(prog_s * SR))
    n_out = int(round(outro_s * SR))
    card = R.endcard_path()
    settle_k = math.ceil(config.ENDCARD_SETTLE_S * fps - 1e-6) if not card.lower().endswith(R.IMAGE_EXTS) else 0
    reveal_k = card_reveal_frame(card, fps)
    w0 = max(0, total - int(BEAT_WIN_S * SR))
    tail_prog = _pcm(programme, w0, total, total=total) if has_audio else np.zeros((total - w0, 2), np.float32)
    grid = beat_grid(tail_prog) if has_audio else None
    if grid:
        grid["abs_phase_s"] = w0 / SR + grid["phase_s"]
    t_sting, on, t_rev = sting_time(prog_s, fps, j, reveal_k, settle_k, grid)
    ns = min(int(SEND_S * SR), total)                        # the send: the programme's last moments
    cut = max(w0, min(int((t_sting - 0.25) * SR), total - ns))
    dry = tail_prog[cut - w0:].astype(np.float64)
    n_tail = len(dry) + n_out
    tt = (cut + np.arange(n_tail)) / SR                       # programme seconds of each tail sample
    ref = _k_db(tail_prog[-int(CLOSE_S * SR):]) if has_audio else -99.0
    if ref < -40:
        ref = -18.0                                           # a near-silent close: the sting sits at a quiet speech level
    # the bed: ducked under the sting, then the old 0.25 s fade to the join
    tdry = tt[:len(dry)]
    duck = 10 ** (-DUCK_DB / 20)
    g = np.interp(tdry, [t_sting - DUCK_RAMP_S, t_sting], [1.0, duck])
    fade = float(getattr(config, "OUTRO_AUDIO_TAIL_FADE_S", 0.25))
    g *= np.clip((prog_s - tdry) / fade, 0.0, 1.0)
    out = np.zeros((n_tail, 2), np.float64)
    out[:len(dry)] = dry * g[:, None]
    acc = np.zeros((n_tail, 2), np.float64)
    info = dict(on=on, sting_s=round(t_sting, 4), reveal_s=round(t_rev, 4), grid=grid, ref_lufs=round(ref, 1))
    # the ring: the programme's last moments through a dark, wide tail, rising under the sting, fading across the card
    if has_audio and ns > SR // 10:
        send = tail_prog[-ns:].astype(np.float64) * (np.sin(np.linspace(0, np.pi / 2, ns)) ** 2)[:, None]
        rng = np.random.default_rng(1114)
        nl = int(RING_RT60_S * 1.1 * SR)
        decay = np.exp(-6.9078 * np.arange(nl) / (RING_RT60_S * SR))
        ir = rng.standard_normal((nl, 2)) * decay[:, None]
        ir = lfilter([0.12], [1, -0.88], ir, axis=0)          # dark: a one-pole low-pass near 1 kHz
        ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))[:, None]
        wet = np.stack([fftconvolve(send[:, c], ir[:, c]) for c in (0, 1)], axis=1)
        start = total - ns - cut                              # the send's first sample in the tail
        wet = wet[: n_tail - start]
        lvl = _k_db(wet[ns: ns + SR].astype(np.float32))
        if lvl > -90:
            wet *= 10 ** ((ref - RING_UNDER_DB - lvl) / 20)
            tw = tt[start:start + len(wet)]
            env = np.interp(tw, [min(t_sting, prog_s) - DUCK_RAMP_S, prog_s], [0.0, 1.0])
            u = np.clip((tw - prog_s - RING_HOLD_S) / max(1e-3, outro_s - RING_HOLD_S), 0.0, 1.0)
            env = env * np.cos(u * np.pi / 2) ** 2
            acc[start:start + len(wet)] += wet * env[:, None]
            info["ring_lufs"] = round(ref - RING_UNDER_DB, 1)
    st = pick_sting(used)
    if st:
        y = _pcm(st["path"]).astype(np.float64)
        d = st["desc"]
        gdb = min(12.0, ref - STING_UNDER_DB - float(d["k200"]))
        y *= 10 ** (gdb / 20)
        at = int(round((t_sting - float(d["hit_s"])) * SR)) - cut    # its hit (the attack within 6 dB of the peak) on the beat
        a0, b0 = max(0, at), min(n_tail, at + len(y))
        if b0 > a0:
            fo = min(float(config.OUTRO_FADE_OUT_S), outro_s / 3)   # it ends with the card's own far-edge fade
            acc[a0:b0] += y[a0 - at:b0 - at] * np.clip((prog_s + outro_s - tt[a0:b0]) / fo, 0.0, 1.0)[:, None]
        info["sting"] = dict(id=st["id"], file=os.path.basename(st["path"]), gain_db=round(gdb, 1),
                             hit_in_file_s=d["hit_s"], license=st["license"], title=st["title"], author=st["author"])
    # the closing section's true peak, kept under the ceiling by lowering the accent only (the programme is as it was)
    s = 1.0
    if _true_peak_db(out + acc) > TP_CEIL_DB:
        lo, hi = 0.0, 1.0
        for _ in range(12):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _true_peak_db(out + mid * acc) <= TP_CEIL_DB else (lo, mid)
        s = lo
    mix = (out + s * acc).astype(np.float32)
    info["accent_scale_db"] = round(20 * math.log10(max(s, 1e-6)), 2)
    info["tail_true_peak_db"] = round(_true_peak_db(mix), 2)
    import wave
    wav = os.path.join(workdir, "brand_tail.wav")
    with wave.open(wav, "wb") as f:                           # 24-bit PCM: the tail is already under full scale
        f.setnchannels(2)
        f.setsampwidth(3)
        f.setframerate(SR)
        q = np.clip(np.rint(mix * 8388607.0), -8388608, 8388607).astype("<i4")
        f.writeframes(q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes())
    return wav, cut, info


def finish(programme, out, workdir, used=(), accent=True):
    """programme (the engine's film) -> out, with the mark on every programme frame and the end card joined.
    accent: the end accent (the sting on the reveal and the ring under the card; the module's docstring). `used`: the
    library recordings the film already sounds, so the sting is never one of them."""
    R = _renderer()
    import config
    p = probe(programme)
    W, H, fps = p["w"], p["h"], round(p["fps"])
    prog_s = p["frames"] / fps if p["frames"] else p["dur"]
    outro_s = float(config.OUTRO_DURATION_S)
    card = R.endcard_path()
    mark = R.watermark_image(workdir, W, H)
    if not card or not mark:
        raise RuntimeError("brand asset missing: the end card and the mark are not optional here")
    x, y = R.watermark_geometry(W, H)["image"][:2]
    parts, j = R.outro_card_parts(2, W, H, fps, outro_s, "", motion=not card.lower().endswith(R.IMAGE_EXTS), program_s=prog_s)
    n_prog = p["frames"] or math.ceil(prog_s * fps - 1e-6)
    t_join = max(0.0, (n_prog - j - 0.25) / fps)
    fade = float(getattr(config, "OUTRO_AUDIO_TAIL_FADE_S", 0.25))
    g = [f"[0:v]setsar=1,format=yuv420p[pv]",
         f"[pv][1:v]overlay={x}:{y}:format=auto:shortest=1[vm]"] + parts + [
        "[ohead]format=yuva420p[oheadp]",
        f"[oheadp]settb=AVTB,setpts=PTS-STARTPTS+{t_join:.6f}/TB[oheadt]",
        "[vm][oheadt]overlay=0:0:format=auto:eof_action=repeat[vjoin]"]
    cmd = [FF, "-v", "error", "-y", "-i", programme, "-loop", "1", "-r", str(fps), "-i", mark]
    cmd += R._endcard_input_args(card, outro_s, fps)
    info = {}
    if accent:
        wav, cut, info = accent_tail(programme, workdir, n_prog / fps, fps, j, outro_s, used=used, has_audio=p["audio"])
        total = int(round(n_prog / fps * SR))
        cmd += ["-i", wav]
        if p["audio"]:
            g.append(f"[0:a]aresample={SR},aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_len={total},"
                     f"atrim=start_sample=0:end_sample={cut},asetpts=N/SR/TB[ahead]")
        else:
            g.append(f"anullsrc=r={SR}:cl=stereo,atrim=end_sample={cut},aformat=sample_fmts=fltp:channel_layouts=stereo[ahead]")
        g += [f"[3:a]aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[atail]",
              "[ahead][atail]concat=n=2:v=0:a=1[aout]",
              "[vjoin][ovid]concat=n=2:v=1:a=0[vout]"]
    else:
        g.append(f"anullsrc=r=48000:cl=stereo:d={outro_s:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[osil]")
        if p["audio"]:
            g.append(f"[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"afade=t=out:st={max(0.0, prog_s - fade):.3f}:d={fade:.3f},asetpts=N/SR/TB[aprogc]")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo:d={prog_s:.3f},aformat=sample_fmts=fltp:channel_layouts=stereo[aprogc]")
        g.append("[vjoin][aprogc][ovid][osil]concat=n=2:v=1:a=1[vout][aout]")
    cmd += ["-filter_complex", ";".join(g), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    res = dict(programme_s=round(prog_s, 3), outro_s=outro_s, join_frames=j, mark_xy=[x, y], card=os.path.basename(card))
    if info:
        res["accent"] = info
    return res


def _frame(path, t, W, H):
    import numpy as np
    r = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={W}:{H}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.uint8)[: W * H * 3].reshape(H, W, 3).astype(np.float32)


def check(programme, final, workdir):
    """Measured on the files: length, the mark on the programme, the end card at the close, the sound level."""
    import numpy as np
    R = _renderer()
    import config
    p, f = probe(programme), probe(final)
    W, H = f["w"], f["h"]
    prog_s = p["frames"] / round(p["fps"]) if p["frames"] else p["dur"]
    want = prog_s + float(config.OUTRO_DURATION_S)
    out = dict(final_s=round(f["dur"], 3), want_s=round(want, 3), length_ok=abs(f["dur"] - want) <= 0.1)
    ix0, iy0, iw, ih = R.watermark_geometry(W, H)["image"]
    t = min(prog_s * 0.5, max(0.5, prog_s - 1.0))
    a, b = _frame(programme, t, W, H), _frame(final, t, W, H)
    roi = np.abs(a[iy0:iy0 + ih, ix0:ix0 + iw] - b[iy0:iy0 + ih, ix0:ix0 + iw]).mean()
    rest = np.abs(a - b).mean()
    out.update(mark_roi_diff=round(float(roi), 2), outside_diff=round(float(rest), 2), mark_ok=bool(roi > 4 * max(rest, 0.5)))
    card = R.endcard_path()
    c = _frame(card, 2.5, W, H)
    e = _frame(final, f["dur"] - 2.5, W, H)
    cd = float(np.abs(c - e).mean())
    out.update(endcard_diff=round(cd, 2), endcard_ok=cd < 6.0)
    r = subprocess.run([FF, "-v", "info", "-i", final, "-t", f"{prog_s:.3f}", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    lufs = None
    for line in r.stderr.splitlines()[::-1]:
        s = line.strip()
        if s.startswith("I:") and "LUFS" in s:
            lufs = float(s.split()[1])
            break
    out.update(lufs=lufs, loud_ok=lufs is not None and -18.0 <= lufs <= -10.0)
    out["ok"] = all(out[k] for k in ("length_ok", "mark_ok", "endcard_ok", "loud_ok"))
    return out
