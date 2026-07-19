# Regenerating `worker/music/features.json`

`music_search.py` matches and — more importantly — **vetoes** tracks using
measured audio features, not adjectives someone typed. If you add, remove or
replace a track in `worker/music/`, regenerate this file or the new track is
invisible to search and carries no vetoes at all.

```bash
cd worker
python3 tools/analyze.py /tmp/music_raw.json                 # decode + measure
python3 tools/build_features.py /tmp/music_raw.json music/features.json
python3 tests/test_units.py                                  # round-27 checks
```

Deterministic: re-running the pair over the shipped 24 tracks reproduces the
committed `features.json` byte for byte (md5 `2c4eda97…`).

Requires `ffmpeg`/`ffprobe` and `numpy`. No librosa or scipy — the STFT,
onset envelope, autocorrelation tempo estimate and mid/side decomposition are
implemented directly on decoded PCM in `analyze.py`.

## What is measured, and why it is trustworthy

Per track: tempo (with a confidence), spectral centroid, RMS statistics,
**dynamic range** (p95−p20), **energy arc** across 8 segments and its build
correlation, low-end weight below 200 Hz, and stereo/mono-sum behaviour.

`build_features.py` turns those numbers into `tags` and `not_for` by
deterministic rules. Every tag traces to either a measured threshold or a
DOCUMENTED fact (the FMA album a track belongs to, per `SOURCE_NOTES.md`).
Nothing is guessed from listening, because nothing was listened to.

Three things the measurement pass got right that intuition would not have:

- **Tempo confidence was inflated.** "Peak above median" looks strong even in
  noise, and gave the beatless `ambient-calm-before-the-storm` a confidence of
  0.65. Gating on the *absolute* autocorrelation height drops it to 0.28,
  which is honest. 20 of 24 tracks remain octave-uncertain, so **no tempo term
  is written into `not_for` for those** — calling a possibly-70 BPM track
  "fast" is the costlier error.
- **A title contradicted its own audio.** *Bubbles (Lofi, **Bright**,
  Relaxed)* measures a 1186 Hz centroid, below the catalog median of 1540. The
  `bright` tag was dropped; the override is recorded in `_tag_overrides`.
- **Vocal presence is `unknown` on all 24, deliberately.** Both probes
  (centre-channel cancellation, 2–8 Hz syllabic modulation) return smooth
  continua that do not separate a sung line from a lead instrument —
  percussion modulates in the same band as speech syllables. `SOURCE_NOTES.md`
  records that candidates came from FMA's `only-instrumental=1` filter, but
  that is uploader-declared provenance, not measurement, so **no track is
  claimed instrumental**. If a request ever *requires* guaranteed no-vocals,
  this field cannot answer it.

`rms_dbfs_mean` is **useless as a discriminator** — every file was normalized
to ~−16 LUFS on ingest, so it spans just 6.8 dB. Dynamic range and energy arc
are unaffected by gain normalization and stay valid.

## The finding that motivated all of this

**Zero of 24 tracks can serve "epic cinematic movie-trailer music."** Not one
is close:

| criterion | trailer bar | catalog best | passes |
|---|---|---|---|
| dynamic range (p95−p20) | ≥ 20 dB | 16.8 dB | 0/24 |
| crest factor (peak−RMS) | ≥ 18 dB | 17.0 dB | 0/24 |
| build correlation | ≥ 0.70 | 0.82 | 1/24 |
| arc range | ≥ 12 dB | 16.3 dB | 1/24 |

Both build-criterion "passes" are false positives on inspection:
`inspiring-cute-melodies-10`'s 16.3 dB arc is a 20-second **fade-in** over a
flat body (dynamic range 5.9 dB), and `cinematic-across-the-border`'s 0.82
correlation covers a total rise of **5.8 dB** that plateaus by segment 3.
Neither is a trailer arc; both are intros.

The catalog's **median dynamic range is 6.6 dB and median arc range 4.1 dB** —
uniformly flat loop-bed material, against the 25–40 dB a real trailer cue
runs between a sparse open and an orchestral hit. So all 24 carry `epic`,
`trailer`, `movie-trailer`, `blockbuster`, `orchestral-hit`, `braam` and
`heroic-climax` in `not_for`, and a trailer request honestly returns NO MATCH
instead of the nearest mood.

## Incidental finding worth knowing

Three tracks are **phase-inverted between channels** and lose real level when
summed to mono — `cinematic-after-midnight` by 4.6 dB,
`cinematic-adventures-with-paddy` by 2.7 dB, `cinematic-across-the-border` by
2.3 dB. Most of what this product exports is watched on a phone's single
speaker, which sums to mono. `music_search._mono_penalty` demotes them in
ranking (it does not exclude them — a 4.6 dB dip is a bad default, not a
broken file). Before this, the catalog's most fragile bed was its default
answer for "cinematic".
