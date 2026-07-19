#!/usr/bin/env python3
"""Objective audio feature measurement for the Valmera music library.

No librosa / no scipy available -> everything implemented directly on
decoded PCM with numpy.
"""
import json, os, subprocess, sys, math
import numpy as np

MUSIC_DIR = "/Users/muslimshmary/Documents/hustler-server/worker/music"
SR = 22050
N_FFT = 2048
HOP = 512
FPS = SR / HOP  # onset-envelope frame rate ~43.07 Hz


# ---------------------------------------------------------------- decoding
def decode_stereo(path, sr=SR):
    """Decode to float32 stereo at `sr`. Returns (L, R)."""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", "2", "-ar", str(sr), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    x = x[: (len(x) // 2) * 2].reshape(-1, 2)
    return x[:, 0], x[:, 1]


# ---------------------------------------------------------------- stft
def frame_sig(x, n_fft=N_FFT, hop=HOP):
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def stft_mag(x, n_fft=N_FFT, hop=HOP):
    f = frame_sig(x, n_fft, hop) * np.hanning(n_fft)[None, :]
    return np.abs(np.fft.rfft(f, axis=1))  # (frames, bins)


# ---------------------------------------------------------------- tempo
def onset_envelope(S):
    """Spectral flux onset strength from a magnitude spectrogram."""
    C = np.sqrt(S)                       # amplitude compression
    d = np.diff(C, axis=0)
    flux = np.sum(np.maximum(d, 0.0), axis=1)
    flux = np.concatenate([[0.0], flux])
    # remove slow trend (local mean over ~0.5 s) so ACF sees rhythm not arc
    w = max(3, int(round(FPS * 0.5)) | 1)
    kern = np.ones(w) / w
    trend = np.convolve(flux, kern, mode="same")
    env = np.maximum(flux - trend, 0.0)
    return env


def acf_tempo(env, bpm_min=50.0, bpm_max=200.0, prior_center=110.0, prior_width=1.0):
    """Autocorrelation tempo estimate with a log-normal tempo prior.

    Returns (bpm, salience, raw_bpm_no_prior).
    """
    if env.size < int(FPS * 4) or env.std() == 0:
        return None, 0.0, None
    e = env - env.mean()
    n = len(e)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    F = np.fft.rfft(e, nfft)
    ac = np.fft.irfft(F * np.conj(F), nfft)[:n]
    if ac[0] <= 0:
        return None, 0.0, None
    ac = ac / ac[0]
    # unbiased-ish correction for shrinking overlap
    ac = ac * (n / np.maximum(n - np.arange(n), 1))

    lag_min = int(round(60.0 * FPS / bpm_max))
    lag_max = min(n - 1, int(round(60.0 * FPS / bpm_min)))
    if lag_max <= lag_min + 2:
        return None, 0.0, None
    lags = np.arange(lag_min, lag_max + 1)
    vals = ac[lags]
    bpms = 60.0 * FPS / lags

    raw_bpm = float(bpms[int(np.argmax(vals))])

    prior = np.exp(-0.5 * (np.log2(bpms / prior_center) / prior_width) ** 2)
    scored = vals * prior
    k = int(np.argmax(scored))
    # parabolic interpolation on the lag axis for sub-bin precision
    i = lags[k]
    if lag_min < i < lag_max:
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        denom = (y0 - 2 * y1 + y2)
        shift = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        shift = float(np.clip(shift, -0.5, 0.5))
    else:
        shift = 0.0
    bpm = 60.0 * FPS / (i + shift)

    # salience: how far the chosen peak stands above the typical ACF value
    med = float(np.median(vals))
    spread = float(np.percentile(vals, 90) - med)
    peak = float(vals[k])
    salience = (peak - med) / spread if spread > 1e-12 else 0.0
    return float(bpm), float(salience), raw_bpm


def octave_match(a, b, tol=0.04):
    if a is None or b is None:
        return False, None
    for mult in (1.0, 2.0, 0.5, 3.0, 1 / 3.0, 4.0, 0.25, 1.5, 2 / 3.0):
        if abs(a * mult - b) / b <= tol:
            return True, mult
    return False, None


def acf_at_bpm(env, bpm):
    """Normalised ACF value at the lag corresponding to `bpm`."""
    e = env - env.mean()
    n = len(e)
    nfft = 1 << int(math.ceil(math.log2(2 * n)))
    F = np.fft.rfft(e, nfft)
    ac = np.fft.irfft(F * np.conj(F), nfft)[:n]
    if ac[0] <= 0:
        return 0.0
    ac = (ac / ac[0]) * (n / np.maximum(n - np.arange(n), 1))
    lag = int(round(60.0 * FPS / bpm))
    return float(ac[lag]) if 0 < lag < n else 0.0


def beat_alternation(env, bpm):
    """Phase-lock a beat grid at `bpm`; compare odd vs even beat strength.

    If beats alternate strong/weak the grid is very likely DOUBLE the real
    tempo (a half-time feel). Returns (ratio, n_beats). ratio ~1.0 = even.
    """
    lag = 60.0 * FPS / bpm
    if lag < 2 or len(env) < 8 * lag:
        return 1.0, 0
    n_beats = int((len(env) - 1) / lag)
    best_phase, best_sum = 0.0, -1.0
    for ph in np.linspace(0, lag, 24, endpoint=False):
        idx = np.round(ph + lag * np.arange(n_beats)).astype(int)
        idx = idx[idx < len(env)]
        s = env[idx].sum()
        if s > best_sum:
            best_sum, best_phase = s, ph
    idx = np.round(best_phase + lag * np.arange(n_beats)).astype(int)
    idx = idx[idx < len(env)]
    vals = env[idx]
    if len(vals) < 8:
        return 1.0, len(vals)
    a, b = vals[0::2].mean(), vals[1::2].mean()
    lo, hi = min(a, b), max(a, b)
    return float(lo / hi) if hi > 0 else 1.0, len(vals)


def tempo_measure(env):
    """Full-track ACF tempo + stability across 4 windows + octave check."""
    bpm, sal, raw = acf_tempo(env)
    if bpm is None:
        return dict(bpm=None, bpm_confidence=0.0, bpm_method="acf-failed")

    raw_choice = bpm
    # --- octave folding: prefer the tapping range (70-140 BPM). Only fold
    # DOWNWARDS, and only when the half-tempo lag is itself a strong ACF
    # peak (>50% of the chosen peak). Folding upwards is deliberately not
    # done: calling a possibly-70 BPM track "fast" is the costlier error.
    halved = 0
    while bpm > 140.0 and acf_at_bpm(env, bpm / 2.0) > 0.5 * acf_at_bpm(env, bpm):
        bpm /= 2.0
        halved += 1
        if halved >= 2:
            break

    alt_after, _ = beat_alternation(env, bpm)
    acf_half = acf_at_bpm(env, bpm / 2.0) if bpm / 2.0 >= 40 else 0.0
    acf_here = acf_at_bpm(env, bpm)
    acf_double = acf_at_bpm(env, bpm * 2.0) if bpm * 2.0 <= 320 else 0.0

    n = len(env)
    wins = []
    wlen = n // 4
    for i in range(4):
        seg = env[i * wlen:(i + 1) * wlen]
        b, s, _ = acf_tempo(seg)
        wins.append(b)

    exact = sum(1 for b in wins if b and abs(b - bpm) / bpm <= 0.04)
    with_oct = sum(1 for b in wins if octave_match(b, bpm)[0])
    stability = exact / 4.0
    stability_oct = with_oct / 4.0

    sal_score = float(np.clip(sal / 3.0, 0.0, 1.0))
    # absolute periodicity: a relative "peak above median" can look strong
    # even in an unpulsed ambient wash, so the raw ACF height gates it.
    pulse = float(np.clip(acf_here / 0.60, 0.0, 1.0))
    ambiguous = max(acf_half, acf_double) > 0.75 * max(acf_here, 1e-9)

    conf = (0.35 * sal_score + 0.28 * stability
            + 0.12 * stability_oct + 0.25 * pulse)
    conf = min(conf, float(np.clip(acf_here / 0.35, 0.0, 1.0)))  # hard gate
    if ambiguous:
        conf *= 0.80  # the value may be the wrong metrical level
    conf = float(np.clip(conf, 0.0, 1.0))
    return dict(
        bpm=round(bpm, 1),
        bpm_confidence=round(conf, 2),
        bpm_acf_salience=round(float(sal), 2),
        bpm_window_agreement=round(stability, 2),
        bpm_window_agreement_with_octaves=round(stability_oct, 2),
        bpm_window_estimates=[round(b, 1) if b else None for b in wins],
        bpm_unprioritised=round(raw, 1) if raw else None,
        bpm_pre_octave_fold=round(raw_choice, 1),
        bpm_octave_folds_applied=halved,
        bpm_beat_alternation_ratio=round(float(alt_after), 3),
        bpm_acf_at_half=round(acf_half, 3),
        bpm_acf_at_bpm=round(acf_here, 3),
        bpm_acf_at_double=round(acf_double, 3),
        bpm_octave_ambiguous=bool(ambiguous),
        bpm_alternate_candidates=[round(bpm / 2.0, 1), round(bpm * 2.0, 1)],
        bpm_method="spectral-flux onset envelope (magnitude-compressed, trend-removed) "
                   "-> normalised autocorrelation with a log-normal tempo prior "
                   "(centre 110 BPM), parabolic peak interpolation, then a "
                   "strong/weak beat-alternation test to undo double-time octave "
                   "errors; confidence = ACF peak salience (50%) + tempo agreement "
                   "across the 4 track quarters (35% exact / 15% octave-tolerant)",
    )


# ---------------------------------------------------------------- helpers
def db(x, floor=1e-10):
    return 20.0 * np.log10(np.maximum(x, floor))


def main():
    manifest = json.load(open(os.path.join(MUSIC_DIR, "manifest.json")))
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    out = {}

    for entry in manifest:
        slug = entry["slug"]
        path = os.path.join(MUSIC_DIR, entry["file"])
        L, R = decode_stereo(path)
        mono = 0.5 * (L + R)
        side = 0.5 * (L - R)
        dur = len(mono) / SR

        S = stft_mag(mono)                      # (frames, bins)
        P = S ** 2                              # power
        frame_e = P.sum(axis=1)
        voiced = frame_e > (frame_e.max() * 1e-4)

        # --- spectral centroid, conventional (magnitude-weighted, as in
        # librosa.feature.spectral_centroid), averaged over non-silent frames
        frame_m = S.sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            cen = (S * freqs[None, :]).sum(axis=1) / np.maximum(frame_m, 1e-20)
        centroid_mean = float(np.mean(cen[voiced]))
        centroid_median = float(np.median(cen[voiced]))
        # power-weighted variant: much lower, dominated by bass energy
        with np.errstate(invalid="ignore", divide="ignore"):
            cen_p = (P * freqs[None, :]).sum(axis=1) / np.maximum(frame_e, 1e-20)
        centroid_power_mean = float(np.average(cen_p[voiced], weights=frame_e[voiced]))
        # rolloff: frequency below which 85% of magnitude sits
        csum = np.cumsum(S[voiced], axis=1)
        tot = csum[:, -1:]
        roll_idx = np.argmax(csum >= 0.85 * tot, axis=1)
        rolloff85 = float(np.mean(freqs[roll_idx]))

        # --- RMS / dynamic range (25 ms hop short-time RMS)
        win = int(SR * 0.050)
        hop_r = int(SR * 0.025)
        nfr = max(1, 1 + (len(mono) - win) // hop_r)
        idx = np.arange(win)[None, :] + hop_r * np.arange(nfr)[:, None]
        fr = mono[idx]
        rms = np.sqrt(np.mean(fr ** 2, axis=1))
        rms_db = db(rms)
        overall_rms_db = float(db(np.sqrt(np.mean(mono ** 2))))
        p95, p20 = float(np.percentile(rms_db, 95)), float(np.percentile(rms_db, 20))
        dyn_range = p95 - p20
        peak_db = float(db(np.max(np.abs(mono))))
        crest = peak_db - overall_rms_db

        # --- energy arc: 8 equal segments, RMS normalised to the loudest
        seg_rms = []
        n8 = len(mono) // 8
        for i in range(8):
            s = mono[i * n8:(i + 1) * n8]
            seg_rms.append(float(np.sqrt(np.mean(s ** 2))) if len(s) else 0.0)
        seg_rms = np.array(seg_rms)
        arc = (seg_rms / seg_rms.max()) if seg_rms.max() > 0 else seg_rms
        seg_db = db(seg_rms) - db(seg_rms.max())
        # build slope: least-squares slope of segment dB vs index (dB per segment)
        xi = np.arange(8)
        slope = float(np.polyfit(xi, seg_db, 1)[0])
        # correlation with a monotone ramp = "does it build"
        ramp_corr = float(np.corrcoef(xi, seg_db)[0, 1]) if np.std(seg_db) > 1e-9 else 0.0
        arc_range_db = float(seg_db.max() - seg_db.min())

        # --- low-end weight: fraction of spectral energy below 200 Hz
        lo = freqs < 200.0
        total_e = P.sum()
        low_frac = float(P[:, lo].sum() / max(total_e, 1e-20))
        sub_frac = float(P[:, freqs < 80.0].sum() / max(total_e, 1e-20))

        # --- vocal-presence probes -------------------------------------
        vb = (freqs >= 300.0) & (freqs <= 3400.0)
        Pmid = P
        Sside = stft_mag(side)
        Pside = Sside ** 2

        side_rms = float(np.sqrt(np.mean(side ** 2)))
        mid_rms = float(np.sqrt(np.mean(mono ** 2)))
        side_mid_ratio = side_rms / max(mid_rms, 1e-20)

        # mono-compatibility: level lost when L+R is summed, vs the louder
        # channel alone. Large loss = phasey mix that collapses on mono
        # playback (phone speakers), which matters for exported video.
        lr_rms = max(float(np.sqrt(np.mean(L ** 2))),
                     float(np.sqrt(np.mean(R ** 2))))
        mono_sum_loss_db = float(db(mid_rms) - db(lr_rms))
        lr_corr = float(np.corrcoef(L, R)[0, 1]) if L.std() > 0 and R.std() > 0 else 1.0

        e_mid_vb = float(Pmid[:, vb].sum())
        e_side_vb = float(Pside[:, vb].sum())
        centre_dominance = e_mid_vb / max(e_mid_vb + e_side_vb, 1e-20)
        vocal_band_frac = float(Pmid[:, vb].sum() / max(total_e, 1e-20))

        # syllabic modulation: envelope of the 300-3400 band, energy at 2-8 Hz
        vb_env = np.sqrt(Pmid[:, vb].sum(axis=1))
        if vb_env.size > 64 and vb_env.std() > 0:
            ve = vb_env - vb_env.mean()
            ve = ve * np.hanning(len(ve))
            Fm = np.abs(np.fft.rfft(ve)) ** 2
            mf = np.fft.rfftfreq(len(ve), 1.0 / FPS)
            band = (mf >= 2.0) & (mf <= 8.0)
            ref = (mf >= 0.5) & (mf <= 20.0)
            syllabic = float(Fm[band].sum() / max(Fm[ref].sum(), 1e-20))
            vb_cv = float(vb_env.std() / max(vb_env.mean(), 1e-20))
        else:
            syllabic, vb_cv = 0.0, 0.0

        # mid-band spectral flatness (tonal vs noisy) on voiced frames
        Pvb = np.maximum(Pmid[:, vb][voiced], 1e-20)
        flat = np.exp(np.mean(np.log(Pvb), axis=1)) / np.mean(Pvb, axis=1)
        vb_flatness = float(np.mean(flat))

        out[slug] = dict(
            title=entry["title"],
            file=entry["file"],
            author=entry["author"],
            manifest_mood=entry["mood"],
            duration_s=round(dur, 2),
            **tempo_measure(onset_envelope(S)),
            spectral_centroid_hz_mean=round(centroid_mean, 1),
            spectral_centroid_hz_median=round(centroid_median, 1),
            spectral_centroid_hz_power_weighted=round(centroid_power_mean, 1),
            spectral_rolloff85_hz=round(rolloff85, 1),
            rms_dbfs_mean=round(overall_rms_db, 2),
            peak_dbfs=round(peak_db, 2),
            crest_factor_db=round(crest, 2),
            dynamic_range_db=round(dyn_range, 2),
            rms_p95_dbfs=round(p95, 2),
            rms_p20_dbfs=round(p20, 2),
            energy_arc=[round(float(v), 3) for v in arc],
            energy_arc_db=[round(float(v), 2) for v in seg_db],
            arc_slope_db_per_segment=round(slope, 2),
            arc_build_correlation=round(ramp_corr, 2),
            arc_range_db=round(arc_range_db, 2),
            low_end_fraction_below_200hz=round(low_frac, 4),
            sub_fraction_below_80hz=round(sub_frac, 4),
            stereo=dict(
                lr_correlation=round(lr_corr, 4),
                mono_sum_loss_db=round(mono_sum_loss_db, 2),
                side_to_mid_rms_ratio=round(side_mid_ratio, 4),
            ),
            vocal_probe=dict(
                side_to_mid_rms_ratio=round(side_mid_ratio, 4),
                centre_dominance_300_3400hz=round(centre_dominance, 4),
                vocal_band_energy_fraction=round(vocal_band_frac, 4),
                syllabic_modulation_2_8hz_ratio=round(syllabic, 4),
                vocal_band_envelope_cv=round(vb_cv, 4),
                vocal_band_spectral_flatness=round(vb_flatness, 5),
            ),
        )
        print(f"{slug:48s} bpm={out[slug]['bpm']} conf={out[slug]['bpm_confidence']} "
              f"cen={centroid_mean:6.0f} dr={dyn_range:5.1f} low={low_frac:.3f} "
              f"s/m={side_mid_ratio:.3f} cd={centre_dominance:.3f} syl={syllabic:.3f}",
              flush=True)

    json.dump(out, open(sys.argv[1], "w"), indent=2)


if __name__ == "__main__":
    main()
