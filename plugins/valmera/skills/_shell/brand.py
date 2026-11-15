"""The brand finish: the product's own watermark and end card on a Mac-rendered programme.

Nothing is redrawn here. The mark's pixels and place come from the worker's renderer
(watermark_image, watermark_geometry) and the end card's join from outro_card_parts, so a
film from a workflow closes exactly as a Valmera export does. The end card and the mark are
untouchable: check() measures both on the delivered file.

The close (owner, 15 Nov: the old ring under the card was a bass hum, the tail of the film's music through a dark
reverb, 88% under 150 Hz for about 4 s). The film's sound now ends as the 28 reference reels' music does: it holds about
1.2 s past the last word, then falls 20 dB in about a second (linear in dB), near mono under 150 Hz, and is gone at the
card (accent_tail, tail_shape measures it on the file). Signals only: no model, no words.

The end accent (owner, 14 Nov: make people look at the mark and come to the site), behind chain.STING_ON: one sting
from the product's own licensed library (the unflagged recording nearest the references' own close, reveal and
transition sounds, pick_sting) lands on the card's reveal, on the programme music's beat when its grid has one there
(onsets and tempo, measured), with the card's loudness inside the programme's range and its true peak under -1 dBTP.
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
# The sting (owner, 15 Nov: the 1.4 s ring rule chose impact_10, "a mountain fall sound"). It is now the recording
# nearest the 28 metromedia reels' own close / reveal / transition / title / lead-in effects (147 events, measured with
# signals by lane STING-REF; score/sfx_quality.json carries them and the library on the same ruler), never a flagged one.
# The references' end sounds are short, bright and under the voice: no reel ends on a long rumbling hit.
STING_FEATS = ("attack_ms", "body_s", "tail30_any_s", "centroid_hz", "low_share_lt150", "noisiness")
# Plain 40 Hz-16 kHz flatness is left out: on the references it is read with the bed under the effect (p50 0.0004),
# on the library dry (clicks 0.06-0.15), so the two do not compare; noisiness (octave flatness 250 Hz-16 kHz, read only
# where the effect is over the music) is the comparable one.
STING_K = 10                # the mean distance to its 10 nearest reference events (about 7% of them) ...
STING_GATE = ("attack_ms", "body_s", "tail30_any_s")   # inside the references' p10-p90 on these, or not a sting
# A lead or a train is not one landing, and a recording that names an object (a camera, paper, a till, a heart; or one
# the library gives a family) answers only the picture that shows it, never a brand card.
STING_UNSPOKEN = ("riser", "typing", "shutter", "paper", "cash", "heartbeat")
STING_VS_VOICE_PCT = 50     # the sting's 50 ms K peak at the references' median against the voice (-10.7 dB)
DUCK_DB = 6.0               # the bed under the sting
DUCK_RAMP_S = 0.05
# The tail (owner, 15 Nov: a bass hum sat under the card, the old ring of "Too Cool", 88% under 150 Hz for 4 s). It now
# follows the 28 references' close (lane STING-REF, ref_profile.json close.music_end): their music ends 1.17 s after the
# last word (p50; p10 0.32 s), and in 15 of 28 it falls 12 dB or more, from its bed to -20 dB in 1.01 s (p50 of 12);
# their bed is near mono under 150 Hz (side 9.7 dB under mid, p50); the music ends with the film (0.02 s before it,
# p50). So the film's sound has ended, fallen TAIL_FALL_DB, by TAIL_AFTER_WORD_S after the last word (or at the card,
# when the programme closes sooner): it falls TAIL_FALL_DB over TAIL_FALL_S (linear in dB, the references' fall), never
# before TAIL_BREATH_S after the last word, keeps falling at that rate, and is gone at the card: nothing rings under it
# (the sting, when it is on, is the card's only sound). A programme that closes under a second after its last word cuts
# the fall short there: a drop of 12 dB or more, as 15 of 28 references end.
TAIL_AFTER_WORD_S = 1.2
TAIL_BREATH_S = 0.1
TAIL_FALL_S = 1.0
TAIL_FALL_DB = 20.0
TAIL_END_FADE_S = 0.03      # the last samples before the card, to digital zero without a click
TAIL_LEAD_S = 0.5           # the tail is laid from this long before its fall starts (from the last word when it is known)
MONO_LOW_HZ = 150.0         # under this the tail is brought near mono ...
MONO_SIDE_DB = -12.0        # ... its side this far under what it was (the references' bed: side 9.7 dB under mid there)
MONO_RAMP_S = 0.2
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


_quality = {}


def sfx_quality():
    """score/sfx_quality.json of the worker these films are made with: the library audit's flags, the recordings no
    film sounds, and the references' sting moments with the library on the same ruler. {} when it is missing."""
    p = os.path.join(worker_dir(), "score", "sfx_quality.json")
    if p not in _quality:
        try:
            _quality[p] = json.load(open(p))
        except (OSError, ValueError):
            _quality[p] = {}
    return _quality[p]


def _sting_vec(row):
    """A sound's shape on the references' ruler, in the units the ear spaces it by (logs of time and frequency)."""
    import numpy as np
    a, b, t, c, lo, nz = (row.get(k) for k in STING_FEATS)
    return np.array([math.log10(max(float(a or 5.0), 5.0)), math.log10(max(float(b or 0.01), 0.01)),
                     math.log10(max(float(t or 0.02), 0.02)), math.log10(max(float(c or 50.0), 50.0)),
                     float(lo or 0.0), float(nz if nz is not None else 0.36)])


def sting_target():
    """The references' sting moments: their vectors, the spread that scales a distance (p10-p90 / 2.56, a robust sd),
    their centre, the p10-p90 range of each feature, and their median level against the voice. None without data."""
    import numpy as np
    q = sfx_quality()
    feats = list((q.get("about") or {}).get("features") or [])
    ref = (q.get("sting") or {}).get("reference") or []
    if not ref or any(k not in feats for k in STING_FEATS):
        return None
    rows = [{k: r[feats.index(k)] for k in STING_FEATS} for r in ref]
    X = np.stack([_sting_vec(r) for r in rows])
    sc = np.maximum((np.percentile(X, 90, axis=0) - np.percentile(X, 10, axis=0)) / 2.56, 1e-3)
    rng = {}
    for k in STING_FEATS:
        v = [float(r[k]) for r in rows if r[k] is not None]
        rng[k] = (float(np.percentile(v, 10)), float(np.percentile(v, 50)), float(np.percentile(v, 90)))
    lv = [float(r[-1]) for r in ref if r[-1] is not None]
    return dict(X=X, scale=sc, centre=np.median(X, axis=0), range=rng, n=len(rows),
                vs_voice_db=float(np.percentile(lv, STING_VS_VOICE_PCT)) if lv else -10.7)


def sting_ranking(used=()):
    """Every library recording that may be the sting, nearest first: [(distance, id, numbers)]. Distance = the
    recording's distance to the references' centre plus its mean distance to its STING_K nearest reference events
    (typical of the set, and inside a crowd of real ones). Never a recording the audit flagged at all, never one whose
    file changed since it was measured (another sha), never a lead or a train, and only inside the references' p10-p90
    on attack, body and tail."""
    import numpy as np
    T = sting_target()
    if T is None:
        return []
    q = sfx_quality()
    flagged = set(q.get("flags") or ()) | set(q.get("excluded") or ())
    lib = os.path.join(worker_dir(), "sound_library")
    try:
        man = {x["id"]: x for x in json.load(open(os.path.join(lib, "manifest.json"))) if x.get("id") and x.get("file")}
    except (OSError, ValueError):
        return []
    used = {os.path.basename(str(u)) for u in used or ()} | {str(u) for u in used or ()}
    out = []
    for sid, row in ((q.get("sting") or {}).get("library") or {}).items():
        x = man.get(sid)
        if (not x or sid in flagged or row.get("sha") != x.get("sha") or x.get("role") in STING_UNSPOKEN or x.get("family")
                or x.get("align") == "start" or sid in used or x["file"] in used
                or not os.path.exists(os.path.join(lib, x["file"]))):
            continue
        if any(row.get(k) is None or not (T["range"][k][0] <= float(row[k]) <= T["range"][k][2]) for k in STING_GATE):
            continue
        v = _sting_vec(row)
        dd = np.sqrt((((T["X"] - v) / T["scale"]) ** 2).sum(axis=1))
        d = float(np.sqrt((((T["centre"] - v) / T["scale"]) ** 2).sum()) + np.sort(dd)[:STING_K].mean())
        out.append((round(d, 4), sid, {k: row.get(k) for k in STING_FEATS}))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def pick_sting(used=()):
    """The sting: the recording of the product's own library (the one SND3 designs with) nearest the references' close,
    reveal and transition sounds (sting_ranking), never one the film already used. None when none qualifies (the card
    then carries the ring alone)."""
    import numpy as np
    S = _score_signal()
    rank = sting_ranking(used)
    if not rank:
        return None
    lib = os.path.join(worker_dir(), "sound_library")
    man = {x["id"]: x for x in json.load(open(os.path.join(lib, "manifest.json"))) if x.get("id")}
    dist, sid, nums = rank[0]
    x = man[sid]
    y = _pcm(os.path.join(lib, x["file"]), channels=1)[:, 0]
    d = S.describe(y)
    if not d:
        return None
    kl = S.k_level(S.power(y))                                   # its loudest 50 ms, K-weighted (the references' ruler)
    w = int(round(0.05 * SR / S.HOP))
    if len(kl) > w:
        k50 = float(10 * np.log10(max(float(np.convolve(10 ** (kl / 10), np.ones(w) / w, mode="valid").max()), 1e-12)))
    else:
        k50 = float(kl.max())
    d["k50"] = round(k50, 1)
    return dict(id=sid, path=os.path.join(lib, x["file"]), desc=d, license=x.get("license"), title=x.get("title"),
                author=x.get("author"), distance=dist, numbers=nums, vs_voice_db=round(sting_target()["vs_voice_db"], 1))


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


def tail_start(prog_s, last_word_s):
    """Where the film's sound starts its fall (programme seconds): TAIL_FALL_S before it has to be over (TAIL_AFTER_WORD_S
    after the last word, or the programme's end when that comes sooner), never sooner than TAIL_BREATH_S after the word.
    Without a last word, the programme's last TAIL_FALL_S."""
    if last_word_s is None or not (0 < float(last_word_s) < prog_s):
        return max(0.0, prog_s - TAIL_FALL_S)
    L = float(last_word_s)
    end = min(L + TAIL_AFTER_WORD_S, prog_s)
    return max(end - TAIL_FALL_S, min(L + TAIL_BREATH_S, prog_s))


def tail_gain(tt, t0, prog_s):
    """The tail's gain at programme seconds tt: 1 until t0, then down TAIL_FALL_DB every TAIL_FALL_S (linear in dB),
    to zero over TAIL_END_FADE_S at the programme's end, and zero under the card."""
    import numpy as np
    db = -TAIL_FALL_DB * np.clip(tt - t0, 0.0, None) / TAIL_FALL_S
    g = 10 ** (db / 20)
    g *= np.clip((prog_s - tt) / TAIL_END_FADE_S, 0.0, 1.0)
    return g


def mono_low(x, ramp):
    """x (n, 2) with its side under MONO_LOW_HZ brought MONO_SIDE_DB down, by `ramp` (0..1 per sample)."""
    import numpy as np
    from scipy.signal import butter, sosfiltfilt
    if len(x) < 64:
        return x
    mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
    sos = butter(4, MONO_LOW_HZ, "low", fs=SR, output="sos")
    lo = sosfiltfilt(sos, side) if len(side) > 64 else side
    side = side - (1 - 10 ** (MONO_SIDE_DB / 20)) * lo * ramp
    return np.stack([mid + side, mid - side], axis=1)


def last_word(l1_path, work=None):
    """The film's last word end (programme seconds), read from its own voice track (the scene engine's split of the L1
    with no sounds: score.music.voice_spans_of), or None."""
    try:
        _renderer()
        from score import music as MU
        l1 = json.load(open(l1_path))
        spans = MU.voice_spans_of(l1, work or os.path.dirname(os.path.abspath(l1_path)))
        return float(spans[-1][1]) if spans else None
    except Exception:  # noqa: BLE001 - unknown: the tail falls over the programme's last second
        return None


def accent_tail(programme, workdir, prog_s, fps, j, outro_s, used=(), has_audio=True, last_word_s=None, sting=True):
    """The closing section's sound, as a 48 kHz stereo WAV laid from `cut` samples on: the programme's own samples
    there, holding TAIL_AFTER_WORD_S past the last word and then falling as the references' music falls (tail_gain),
    the low end near mono, nothing under the card; with `sting`, the sting on the card's reveal (on the music's beat
    when its grid has one there), the bed ducked under it. Returns (wav, cut_sample, info)."""
    import numpy as np
    R = _renderer()
    import config
    total = int(round(prog_s * SR))
    n_out = int(round(outro_s * SR))
    w0 = max(0, total - int(BEAT_WIN_S * SR))
    tail_prog = _pcm(programme, w0, total, total=total) if has_audio else np.zeros((total - w0, 2), np.float32)
    t0 = tail_start(prog_s, last_word_s)
    info = dict(last_word_s=None if last_word_s is None else round(float(last_word_s), 3), fall_from_s=round(t0, 3))
    t_sting = None
    if sting:
        card = R.endcard_path()
        settle_k = math.ceil(config.ENDCARD_SETTLE_S * fps - 1e-6) if not card.lower().endswith(R.IMAGE_EXTS) else 0
        reveal_k = card_reveal_frame(card, fps)
        grid = beat_grid(tail_prog) if has_audio else None
        if grid:
            grid["abs_phase_s"] = w0 / SR + grid["phase_s"]
        t_sting, on, t_rev = sting_time(prog_s, fps, j, reveal_k, settle_k, grid)
        info.update(on=on, sting_s=round(t_sting, 4), reveal_s=round(t_rev, 4), grid=grid)
    cut = max(w0, min(int((t0 - TAIL_LEAD_S) * SR), total))
    if last_word_s is not None and 0 < float(last_word_s) < prog_s:
        cut = max(w0, min(cut, int(float(last_word_s) * SR)))   # from the last word on the bed's low end goes near mono
    if t_sting is not None:
        cut = min(cut, max(w0, int((t_sting - 0.25) * SR)))
    dry = tail_prog[cut - w0:].astype(np.float64)
    n_tail = len(dry) + n_out
    tt = (cut + np.arange(n_tail)) / SR                       # programme seconds of each tail sample
    ref = _k_db(tail_prog[-int(CLOSE_S * SR):]) if has_audio else -99.0
    if ref < -40:
        ref = -18.0                                           # a near-silent close: the sting sits at a quiet speech level
    tdry = tt[:len(dry)]
    g = tail_gain(tdry, t0, prog_s)
    if t_sting is not None:
        g *= np.interp(tdry, [t_sting - DUCK_RAMP_S, t_sting], [1.0, 10 ** (-DUCK_DB / 20)])
    ramp = np.clip((tdry - cut / SR) / MONO_RAMP_S, 0.0, 1.0)
    out = np.zeros((n_tail, 2), np.float64)
    out[:len(dry)] = mono_low(dry, ramp) * g[:, None]
    acc = np.zeros((n_tail, 2), np.float64)
    info["ref_lufs"] = round(ref, 1)
    st = pick_sting(used) if sting else None
    if st:
        y = _pcm(st["path"]).astype(np.float64)
        d = st["desc"]
        gdb = min(12.0, ref + st["vs_voice_db"] - float(d["k50"]))      # its 50 ms peak where the references' sit
        y *= 10 ** (gdb / 20)
        at = int(round((t_sting - float(d["hit_s"])) * SR)) - cut    # its hit (the attack within 6 dB of the peak) on the beat
        a0, b0 = max(0, at), min(n_tail, at + len(y))
        if b0 > a0:
            fo = min(float(config.OUTRO_FADE_OUT_S), outro_s / 3)   # it ends with the card's own far-edge fade
            acc[a0:b0] += y[a0 - at:b0 - at] * np.clip((prog_s + outro_s - tt[a0:b0]) / fo, 0.0, 1.0)[:, None]
        info["sting"] = dict(id=st["id"], file=os.path.basename(st["path"]), gain_db=round(gdb, 1),
                             hit_in_file_s=d["hit_s"], license=st["license"], title=st["title"], author=st["author"],
                             distance=st["distance"], numbers=st["numbers"], vs_voice_db=st["vs_voice_db"])
    # the closing section's true peak, kept under the ceiling by lowering the accent only (the programme is as it was)
    s = 1.0
    if acc.any() and _true_peak_db(out + acc) > TP_CEIL_DB:
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


def tail_shape(final, prog_s, last_word_s=None, outro_s=None):
    """The delivered film's close, measured on its file (signals only): the level in 50 ms steps, the bed (the loudest
    50 ms in the quarter second after the last word), when the sound has ended after the last word (20 dB under the
    bed), how long its fall takes (from 3 dB under to 20 dB under), how far it has fallen at the card, the loudest 400 ms under the card and its
    share under 150 Hz against the bed's, and the side-to-mid ratio under 150 Hz from the last word to the card (else
    over the last 2 s before it)."""
    import numpy as np
    outro_s = float(outro_s if outro_s is not None else probe(final)["dur"] - prog_s)
    s0 = max(0.0, prog_s - 6.0)
    x = _pcm(final, int(s0 * SR), int((prog_s + outro_s) * SR)).astype(np.float64)
    hop = int(0.05 * SR)
    m = x.mean(axis=1)
    lv = np.array([10 * np.log10(max(float((m[i:i + hop] ** 2).mean()), 1e-14)) for i in range(0, len(m) - hop + 1, hop)])
    tl = s0 + np.arange(len(lv)) * 0.05
    L = float(last_word_s) if last_word_s is not None else prog_s - 2.0
    win = (tl >= L) & (tl < min(L + 0.25, prog_s))           # the bed as the last word leaves it
    bed = float(lv[win].max()) if win.any() else float(lv.max())
    after = tl >= L
    sm = np.convolve(lv, np.ones(3) / 3, mode="same")
    f3 = next((i for i in np.where(after)[0] if sm[i] <= bed - 3.0), None)
    m20 = next((i for i in np.where(after)[0] if sm[i] <= bed - 20.0 and (f3 is None or i >= f3)), None)
    end_s = None if m20 is None else float(tl[m20])          # the sound has ended: 20 dB under its bed
    fall_s = None if (f3 is None or m20 is None) else float(tl[m20] - tl[f3])
    pre = (tl >= prog_s - 0.5) & (tl < prog_s)
    card = x[int((prog_s - s0) * SR):]
    sec = int(0.4 * SR)
    card_db, card_low = -140.0, 0.0
    for i in range(0, max(1, len(card) - sec + 1), sec // 2):
        seg = card[i:i + sec].mean(axis=1)
        if len(seg) < 64:
            continue
        e = float((seg ** 2).mean())
        db = 10 * np.log10(max(e, 1e-14))
        if db > card_db:
            F = np.abs(np.fft.rfft(seg)) ** 2
            hz = np.fft.rfftfreq(len(seg), 1 / SR)
            card_db, card_low = db, float(F[hz < 150].sum() / max(F.sum(), 1e-20))

    def low_db(seg):
        from scipy.signal import butter, sosfilt
        sos = butter(4, 150.0, "low", fs=SR, output="sos")
        y = sosfilt(sos, seg.mean(axis=1))
        return 10 * np.log10(max(float((y ** 2).mean()), 1e-14))
    bed_seg = x[int((L + 0.1 - s0) * SR):int((min(L + 1.1, prog_s) - s0) * SR)]
    from scipy.signal import butter, sosfilt
    sos = butter(4, 150.0, "low", fs=SR, output="sos")
    a2 = L + 0.25 if last_word_s is not None and L + 0.5 < prog_s else prog_s - 2.0   # the bed after the last word
    last2 = x[int((max(a2, s0) - s0) * SR):int((prog_s - s0) * SR)]
    mid, side = sosfilt(sos, (last2[:, 0] + last2[:, 1]) / 2), sosfilt(sos, (last2[:, 0] - last2[:, 1]) / 2)
    s2m = 10 * np.log10(max(float((side ** 2).mean()), 1e-14) / max(float((mid ** 2).mean()), 1e-14))
    return dict(last_word_s=None if last_word_s is None else round(L, 3), bed_db=round(bed, 1),
                ends_after_word_s=None if end_s is None else round(end_s - L, 2),
                fall_to_minus20_s=None if fall_s is None else round(fall_s, 2),
                at_card_vs_bed_db=round(float(np.max(lv[pre])) - bed, 1) if pre.any() else None,
                card_peak400_vs_bed_db=round(card_db - bed, 1), card_peak400_low_share=round(card_low, 3),
                card_low_vs_bed_low_db=round(low_db(card) - low_db(bed_seg), 1) if len(bed_seg) > 64 and len(card) > 64 else None,
                side_to_mid_lt150_db=round(float(s2m), 1),
                level_50ms=[[round(float(a), 2), round(float(b), 1)] for a, b in zip(tl, lv) if a >= L - 0.5])


def finish(programme, out, workdir, used=(), accent=True, last_word_s=None):
    """programme (the engine's film) -> out, with the mark on every programme frame and the end card joined.
    The film's sound holds a little past its last word (`last_word_s`, programme seconds; brand.last_word reads it from
    the plan) and falls as the references' music falls, near mono in the low end, gone at the card (accent_tail).
    accent: the sting on the card's reveal (pick_sting). `used`: the library recordings the film already sounds, so the
    sting is never one of them."""
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
    g = [f"[0:v]setsar=1,format=yuv420p[pv]",
         f"[pv][1:v]overlay={x}:{y}:format=auto:shortest=1[vm]"] + parts + [
        "[ohead]format=yuva420p[oheadp]",
        f"[oheadp]settb=AVTB,setpts=PTS-STARTPTS+{t_join:.6f}/TB[oheadt]",
        "[vm][oheadt]overlay=0:0:format=auto:eof_action=repeat[vjoin]"]
    cmd = [FF, "-v", "error", "-y", "-i", programme, "-loop", "1", "-r", str(fps), "-i", mark]
    cmd += R._endcard_input_args(card, outro_s, fps)
    wav, cut, info = accent_tail(programme, workdir, n_prog / fps, fps, j, outro_s, used=used, has_audio=p["audio"],
                                 last_word_s=last_word_s, sting=accent)
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
    cmd += ["-filter_complex", ";".join(g), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    res = dict(programme_s=round(prog_s, 3), outro_s=outro_s, join_frames=j, mark_xy=[x, y], card=os.path.basename(card))
    res["tail"] = {k: v for k, v in info.items() if k in ("last_word_s", "fall_from_s")}
    if accent:
        res["accent"] = info
    return res


def _frame(path, t, W, H):
    import numpy as np
    r = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={W}:{H}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.uint8)[: W * H * 3].reshape(H, W, 3).astype(np.float32)


def _card_frame(path,t,W,H):
    """Compare the asset in its actual renderer seat, preserving its aspect.

    A portrait end card is fitted inside the 98% box on landscape films, not
    stretched to their canvas. Keep the same pixel-error threshold for both.
    """
    import numpy as np
    R=_renderer();cw,ch=R._even(W*.98),R._even(H*.98)
    graph=(f'[0:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,format=rgba[c];'
           '[1:v]format=rgba[b];[b][c]overlay=(W-w)/2:(H-h)/2:shortest=1,'
           'format=yuv420p,format=rgb24[out]')
    seek=[] if path.lower().endswith(R.IMAGE_EXTS) else ['-ss',str(t)]
    r=subprocess.run([FF,'-v','error',*seek,'-i',path,'-f','lavfi','-i',f'color=black:s={W}x{H}:r=30',
                      '-filter_complex',graph,'-map','[out]','-frames:v','1','-f','rawvideo','-'],capture_output=True,check=True)
    return np.frombuffer(r.stdout,np.uint8).reshape(H,W,3).astype(np.float32)


def check(programme, final, workdir):
    """Measured on the files: length, the mark on the programme, the end card at the close, the sound level."""
    import numpy as np
    R = _renderer()
    import config
    p, f = probe(programme), probe(final)
    W, H = f["w"], f["h"]
    prog_s = p["frames"] / round(p["fps"]) if p["frames"] else p["dur"]
    want = prog_s + float(config.OUTRO_DURATION_S)
    out = dict(final_s=round(f["dur"], 3), want_s=round(want, 3), length_ok=abs(f["dur"] - want) <= 0.1,
               programme_s=round(prog_s, 3), outro_s=round(f["dur"] - prog_s, 3))
    ix0, iy0, iw, ih = R.watermark_geometry(W, H)["image"]
    t = min(prog_s * 0.5, max(0.5, prog_s - 1.0))
    a, b = _frame(programme, t, W, H), _frame(final, t, W, H)
    roi = np.abs(a[iy0:iy0 + ih, ix0:ix0 + iw] - b[iy0:iy0 + ih, ix0:ix0 + iw]).mean()
    rest = np.abs(a - b).mean()
    out.update(mark_roi_diff=round(float(roi), 2), outside_diff=round(float(rest), 2), mark_ok=bool(roi > 4 * max(rest, 0.5)))
    card = R.endcard_path()
    c = _card_frame(card, 2.5, W, H)
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
