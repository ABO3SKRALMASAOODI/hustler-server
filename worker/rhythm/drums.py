"""Drum onsets with class and strength, and 808 notes (track 2C).

Band split of the mix (or of a Demucs drum stem when one is warm):
kick 30-120 Hz, snare body 150-400 Hz + noise 2-6 kHz, hats 6-11 kHz.
Each band's log-energy flux is peak-picked against an adaptive threshold;
a peak is classed by the band that dominates it (a kick also lights the
snare body, a snare also lights the hats: the strongest band relative to
its own typical peak wins). 808 notes come from the bass band: a low onset
whose energy then SUSTAINS (a kick decays in ~80 ms; an 808 holds), with
its pitch from the dominant low-frequency peak.
"""

import numpy as np

from . import dsp

CLASSES = ("kick", "snare", "hat", "perc", "transient")
MIN_GAP_S = 0.06
LOUD_DB = 9.0           # a hit reaches its band's loud level
RISE_DB = 5.0           # and a kick lifts the low band this much


def _norm(x):
    p = float(np.percentile(x, 99)) if len(x) else 0.0
    return x / p if p > 0 else x


def _db(x):
    return 10.0 * np.log10(np.maximum(np.asarray(x, np.float64), 1e-12))


def _rise(db, i, pre=3, post=2):
    """dB rise of a band at frame i: its level just after against just
    before."""
    a = db[max(0, i - pre - 2):max(1, i - 1)]
    b = db[i:min(len(db), i + post + 1)]
    if not len(a) or not len(b):
        return 0.0
    return float(b.max() - a.mean())


def _level_after(db, i, post=4):
    return float(db[i:min(len(db), i + post + 1)].max()) if i < len(db) \
        else -120.0


def onsets(feat, max_per_s=12.0):
    """[{t, cls, strength}] sorted by time.

    Each drum band's own flux is peak-picked, and a peak counts for that
    class only when the band actually reaches its LOUD level there (within
    LOUD_DB of the band's 95th percentile): a snare's noise leaks into the
    low band but never lifts it to the kick's level, a hat over a kick is
    still a kick. Leftover onsets of the full mix are ``transient``."""
    b = feat["bands"]
    gap = max(2, int(round(MIN_GAP_S * dsp.FPS)))
    dbs = {k: _db(b[k]) for k in ("kick", "snare", "noise", "hat")}
    loud = {k: float(np.percentile(v, 95)) for k, v in dbs.items()}
    fluxes = {"kick": dsp.band_flux(b["kick"], lag=2),
              "snare": dsp.band_flux(b["noise"], lag=2) + 0.5 *
              dsp.band_flux(b["snare"], lag=2),
              "hat": dsp.band_flux(b["hat"], lag=1)}
    level_band = {"kick": "kick", "snare": "noise", "hat": "hat"}
    out = []
    taken = {k: [] for k in fluxes}
    # one kick per 1/16 at 140 BPM at most (a pitched kick's sweep flickers)
    min_gap = {"kick": max(gap, int(round(0.11 * dsp.FPS))),
               "snare": max(gap, int(round(0.09 * dsp.FPS))), "hat": gap}
    for cls in ("kick", "snare", "hat"):
        env = _norm(fluxes[cls])
        th = dsp.adaptive_threshold(env, win_s=1.5, k=1.4, floor_q=70)
        band = level_band[cls]
        for i in dsp.onset_peaks(env, np.maximum(th, 0.2), min_gap[cls]):
            if _level_after(dbs[band], i) < loud[band] - LOUD_DB:
                continue
            if cls == "kick" and _rise(dbs["kick"], i) < RISE_DB:
                continue                 # beating under a held bass note
            if cls == "snare" and _level_after(dbs["snare"], i) < \
                    loud["snare"] - LOUD_DB - 4.0:
                continue                 # noise with no body: a hat or fx
            t = dsp.frame_time(i)
            if cls == "hat" and any(abs(t - u) < 0.03 for u in
                                    taken["kick"] + taken["snare"]):
                continue                 # a hat on a hit belongs to the hit
            taken[cls].append(t)
            out.append({"t": round(t, 3), "cls": cls,
                        "strength": round(float(min(1.0, env[i])), 3)})
    full = _norm(dsp.superflux(feat["logmel"]))
    th = dsp.adaptive_threshold(full, win_s=1.5, k=2.0, floor_q=85)
    have = np.array(sorted(o["t"] for o in out)) if out else np.zeros(0)
    for i in dsp.peaks(full, np.maximum(th, 0.3), gap):
        t = dsp.frame_time(i)
        if have.size and np.min(np.abs(have - t)) < MIN_GAP_S:
            continue
        out.append({"t": round(t, 3), "cls": "transient",
                    "strength": round(float(min(1.0, full[i])), 3)})
    out.sort(key=lambda o: o["t"])
    dur = max(1.0, feat.get("duration") or len(full) / dsp.FPS)
    cap = int(max_per_s * dur)
    if len(out) > cap:
        keep = sorted(out, key=lambda o: -o["strength"])[:cap]
        out = sorted(keep, key=lambda o: o["t"])
    return out


def notes_808(feat, kicks=None):
    """[{t, hz, dur}]: sustained low notes (808s / bass hits)."""
    bass = np.asarray(feat["bands"]["bass"], np.float64)
    lb = 10.0 * np.log10(np.maximum(bass, 1e-12))
    flux = dsp.band_flux(feat["bands"]["bass"], lag=2)
    nf = _norm(flux)
    th = dsp.adaptive_threshold(nf, win_s=2.0, k=1.5, floor_q=75)
    hold = int(round(0.18 * dsp.FPS))
    out = []
    med = float(np.median(lb)) if lb.size else -120.0
    for i in dsp.peaks(nf, np.maximum(th, 0.3), int(0.1 * dsp.FPS)):
        j = min(len(lb) - 1, i + hold)
        peak = lb[i:min(len(lb), i + 4)].max()
        if peak < med + 6.0:
            continue
        # sustain: 180 ms later the bass still holds within 9 dB
        if lb[j] < peak - 9.0:
            continue
        k = j
        while k < len(lb) - 1 and lb[k] > peak - 15.0 and k - i < 2 * dsp.FPS:
            k += 1
        hz = float(np.median(feat["bass_hz"][i:j + 1]))
        out.append({"t": round(dsp.frame_time(i), 3), "hz": round(hz, 1),
                    "dur": round((k - i) / dsp.FPS, 3)})
    return out
