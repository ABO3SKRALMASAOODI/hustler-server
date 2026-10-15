"""Bar phase: which beat is ONE (track 2C).

4/4 is assumed (the corpus, trap and pop alike). For each of the four
phases the downbeat evidence is scored over the whole track:

  + a kick (or a strong low onset) on the beat
  + harmonic change INTO the beat (chroma before vs after: chords change
    on the bar)
  + new low-band energy on the beat
  - a snare on the beat (snares and claps sit on 2 and 4)

The best phase wins; ``confidence`` is its margin over the runner-up. Low
confidence means "onsets only, never an invented grid" upstream.
"""

import numpy as np

from . import dsp


def _at(env, t, w=0.05):
    i = int(round(t * dsp.FPS))
    r = max(1, int(round(w * dsp.FPS)))
    lo, hi = max(0, i - r), min(len(env), i + r + 1)
    return float(env[lo:hi].max()) if hi > lo else 0.0


def _chroma_change(chroma, t, span_s):
    i = int(round(t * dsp.FPS))
    n = max(2, int(round(span_s * dsp.FPS)))
    a = chroma[max(0, i - n):i]
    b = chroma[i:min(len(chroma), i + n)]
    if not len(a) or not len(b):
        return 0.0
    va, vb = a.mean(axis=0), b.mean(axis=0)
    den = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
    return float(1.0 - (va @ vb) / den)


def phase(feat, beats, onsets=None):
    """(phase 0-3, confidence 0-1, per-phase scores)."""
    if len(beats) < 8:
        return 0, 0.0, [0.0] * 4
    b = feat["bands"]
    kick = dsp.band_flux(b["kick"], lag=2)
    kick = kick / (np.percentile(kick, 99) or 1.0)
    snare = dsp.band_flux(b["noise"], lag=2) + 0.5 * dsp.band_flux(
        b["snare"], lag=2)
    snare = snare / (np.percentile(snare, 99) or 1.0)
    low = np.log1p(1e4 * np.asarray(b["bass"], np.float64))
    period = float(np.median(np.diff(beats)))
    feats = []
    for t in beats:
        i = int(round(t * dsp.FPS))
        r = max(1, int(round(0.1 * dsp.FPS)))
        lo_before = low[max(0, i - 2 * r):i].mean() if i > 0 else 0.0
        lo_after = low[i:i + 2 * r].mean() if i < len(low) else 0.0
        feats.append((_at(kick, t), _at(snare, t),
                      _chroma_change(feat["chroma"], t, period),
                      max(0.0, lo_after - lo_before)))
    f = np.array(feats)
    if f.std(axis=0).min() == 0:
        f = f + 1e-9
    z = (f - f.mean(axis=0)) / (f.std(axis=0) + 1e-9)
    # harmony changes on the bar line are the most reliable cue across
    # styles (a half-time trap snare sits on 3, a rock backbeat on 2 and 4,
    # so the snare only argues AGAINST a phase, never for its neighbours)
    score = z[:, 0] * 0.6 - z[:, 1] * 0.4 + z[:, 2] * 1.0 + z[:, 3] * 0.4
    per = []
    for p in range(4):
        idx = np.arange(p, len(beats), 4)
        per.append(float(score[idx].mean()))
    order = np.argsort(per)[::-1]
    best, second = per[order[0]], per[order[1]]
    spread = (max(per) - min(per)) or 1.0
    conf = float(np.clip((best - second) / spread * 1.5, 0.0, 1.0))
    return int(order[0]), conf, per


def downbeats(beats, ph):
    return [beats[i] for i in range(ph, len(beats), 4)]
