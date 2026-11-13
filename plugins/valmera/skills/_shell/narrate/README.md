# narrate: the narrator voice for workflows 3 and 4

VoxCPM2 (Apache-2.0, open weights, on the Mac) speaks by default; OpenAI gpt-4o-mini-tts, directed
line by line, is the second engine (`--engine openai`). whisper hears. No person's recording: every
voice is designed from a description (VoxCPM2) or is a stock voice (OpenAI). Every read, from either
engine, gets the same finish: pauses set from the script and the reference narrators, the writer's
stressed words ridden, and a mastering chain solved against the references by signal.

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-narrate/bin/python
cd plugins/valmera/skills/_shell/narrate
$PY narrate.py voices
zsh .valmera/week-1106/heavy.sh narrate $PY narrate.py render script.json --out DIR --band 03   # 04 for long-form
$PY narrate.py render script.json --out DIR --band 03 --engine openai      # the OpenAI voice (NARRATE_ENGINE=openai)
$PY narrate.py check DIR --band 03
```

Setup (once): `python3.12 -m venv .valmera/venv-narrate` then
`pip install voxcpm==2.0.3 torch==2.15.0 torchaudio==2.11.1 faster-whisper==1.1.1 mlx-whisper==0.4.3 inflect==7.6.0 librosa==1.0.1 soundfile==0.14.0 scipy==1.18.1`.
Weights: `openbmb/VoxCPM2`, `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, whisper large-v3 mlx
(`.valmera/week-1106/atlas/models/whisper-large-v3-mlx`, or `mlx-community/whisper-large-v3-mlx`).

## Script

```json
{"title": "...", "voice": "atlas", "band": "03", "pace": "brisk", "energy": "mid",
 "segments": [
  {"text": "In 2007, two roommates in San Francisco had a problem.", "melody": "falling",
   "emphasis": ["problem"], "pause_after": 0.45},
  {"text": "...", "pace": "steady", "direction": "land the ending with quiet confidence", "pause_after": 0}
 ]}
```

The performance fields (pace, energy, melody, emphasis, pause_after, direction) are written by the
workflow's writer model per segment; the tool never reads intent from the words. Pace words are
articulation-rate targets inside the reference band (slow 178, steady 190, brisk 201, fast 211
words/min) or a number.

## Voices

| id | asked for | f0 | melody range | pace (wpm while speaking) | band |
|---|---|---|---|---|---|
| atlas | deep, warm male documentary storyteller | 102 Hz | 12.0 st | 201 | 03 founder and topic stories |
| nova | clear, warm, expressive female storyteller | 231 Hz | 11.2 st | 207 | 03 |
| blaze | energetic, punchy motivational voice (high register) | 244 Hz | 12.1 st | 225 | 03 hooks |
| sage | mature, powerful motivational male | 158 Hz | 11.7 st | 180 | 04 long-form |
| iris | soft, intimate, thoughtful female | 215 Hz | 11.6 st | 192 | 04 |

A voice is a 16 kHz reference clip plus its measurements (`voices/<id>.flac|json`). `make-voice`
designs a new one: several takes from the description, kept is the take the listener hears
perfectly whose melody range, tempo and register sit nearest the reference narrators.

## How it holds together

- **Chunks, one performance.** Chunks of one to three sentences; each is generated as a
  continuation of the previous chunk's own latents and text plus the voice clip, so timbre, level,
  tempo and melody carry over. All latents are decoded in one pass of the causal decoder: no
  waveform seam exists. Pause lengths are set inside the quiet core of each silence.
- **Takes.** Every chunk is heard (whisper large-v3-turbo, CPU). Kept only with every word heard,
  a clean silent ending, pace (while speaking and overall) and melody range inside the band, the
  read's range so far with the take in it inside the band too (a take whose register drifts widens
  the file's range though its own is fine),
  and a join no larger than the read's own sentence gaps (level and pitch step, measured with the
  end of the kept latents and the take decoded together, as the narration will be); nearest the
  asked pace wins. The ladder: 3 takes continuing the read, then 3 from the voice clip alone, then
  2 continuing with more guidance and steps, each rung only while no take is good.
- **Pace steering.** A take outside the band steers the next one: the direction is rewritten
  against its measured pace, and the prompt it continues (or the voice clip) is replayed at a
  damped tempo correction. The model keeps the tempo it is shown; words alone barely move it.
- **Resume.** Each kept chunk is saved in `DIR/_state` as it lands; the same command on the same
  DIR continues at the next chunk after a killed job or a sleeping Mac. `_state` is deleted once
  `narration.wav` is written.
- **Tempo.** The model keeps the tempo of what it continues, so pace is directed relative to the
  running tempo, and the voice clip is first brought to the opening pace.
- **Words.** whisper large-v3 times the finished narration; every script word gets start/end, its
  segment and chunk, plus phrases and cut points (`words.json`).
- **Apple GPU.** bfloat16 by default (`NARRATE_DTYPE=float32` for VoxCPM's own default): same word
  error, ASR confidence, high-band energy and clicks as float32 at a third of the memory; float32
  pushed the weights to swap on a loaded Mac and ran 5 to 10 times slower.

## Engines and the finish (voice study, 13 Nov)

The owner asked for a better voiceover. `.valmera/week-1112/lanes/VOICE` measured 48 reference
narrators (all 28 narrated metromedia stories in `reference-library/2026-11-05/03`, all 20 long-form
films in `04`; htdemucs voice stems at 44.1 kHz, whisper-large-v3 word times) on 23 signals: pitch
range, spread, velocity, phrase swing, declination and cadence; articulation rate and its change
between phrases; pauses per minute and their length; emphasis (loudest and highest word over its
phrase, word-level spread, prominences); spectral tilt, alpha ratio, presence, low-mid weight and
sibilance; room (the fall at phrase ends); crest factor, 400 ms loudness spread and 20 ms dynamics.
No listening model. `voice_ref.json` holds the bands (p10/p25/median/p75/p90) and the median
long-term spectrum. A candidate scores 0-100 per signal (exp(-z^2/2) from the reference median),
averaged per group, groups averaged.

What the metromedia narrators do (medians): 191 words a minute while speaking, 4 pauses a minute,
sentence gaps of 0.22 s and none inside a phrase; 13.8 semitones of range, pitch moving 16.6 st/s;
the stressed word 4.9 dB louder and 8.2 st higher than its phrase; a -6.0 dB/octave spectrum, a dry
room (72 ms fall), 15 dB crest.

| candidate (FedEx story, 177 words, band 03) | score | in band |
|---|---|---|
| VoxCPM2 atlas, today, as delivered | 64.5 | |
| VoxCPM2 atlas + the new finish (default) | 79.0 | 19/23 |
| VoxCPM2 atlas, 16 steps, guidance 2.5, + finish (a fresh read) | 77.1 | 19/23 |
| gpt-4o-mini-tts cedar, directed, stress in capitals, + finish | 75.7 | 18/23 |
| gpt-4o-mini-tts echo / verse / ash / onyx / ballad, + finish | 50-68 | |
| tts-1-hd echo (speed 1.12) / fable / onyx, + finish | 61-68 | |

Through the same htdemucs pass as the references (a fairness check) the order holds. Every OpenAI
voice reads flatter on emphasis (stressed word +2.6-3.3 dB against +4.9) and pauses 20-37 times a
minute before the finish; one request per segment read 120-150 words a minute, so the OpenAI engine
reads chunks of up to 60 words with the persona and every line's direction.

**The finish** (`deliver`, both engines):
- `pauses.py`: every silence in the read goes to its script boundary (punctuation first) and is set:
  a segment end gets the writer's `pause_after` as a ranking (the biggest beats, about 3 a minute in
  03 and 6 in 04, stay pauses of 0.32-0.55 s / 0.40-0.90 s; the rest read as sentence gaps); a
  sentence end at most 0.24 s (04: 0.50), a comma 0.16 s, a gap inside a phrase 0.10 s.
- `master.py`: 80 Hz high-pass; linear-phase tonal EQ toward the references' long-term spectrum
  shape (+-6 dB, 250 Hz-8 kHz); a compressor whose threshold is solved for the references' crest and
  20 ms dynamics; a leveler solved for their 400 ms loudness spread; the writer's emphasis words
  ridden 0-3 dB, solved for their emphasis; a 3.2 kHz presence bell solved for their 2-5 kHz
  balance; a split-band de-esser solved for their sibilance; -14 LUFS, true peak under -1 dBTP.
- `--no-master` keeps the old finish (pauses as generated, loudness only).

**OpenAI engine** (`openai_voice.py`): `gpt-4o-mini-tts-2025-12-15`; per narrate voice an OpenAI
voice and persona (`voices/<id>.json` "openai": atlas cedar, nova coral, blaze verse, sage onyx, iris
marin); the writer's emphasis words go in capitals (4 letters or more), the persona says capitals
carry the stress; takes are heard by the take listener until one is heard whole inside the pace
band (3 rounds at most). The key: `OPENAI_API_KEY`, else the backend `.env` line (never printed).
About $0.015 a minute of speech. If the service fails, the VoxCPM2 voice reads the script.

## The VOICE gate (`check`)

- Word error under 1%, by whisper large-v3 (it never chose a take); the take listener's figure is
  reported beside it.
- Pace (wpm and wpm while speaking) and pitch range (p5..p95 of f0, semitones) inside p10..p90 of
  the reference narrators in `reference-library/2026-11-05` folders 03 and 04 (`ref_band.json`,
  measured by `.valmera/week-1112/lanes/M/tools/ref_band.py`: demucs vocal stem, whisper-large-v3 word
  timings); the voice's loudness within 0.7 dB of the chain's target (-14 LUFS; with `--no-master`,
  inside that band).
- No join artefact at any chunk boundary: energy step and spectral flux under the file's own p99,
  no click (sample step over the file's p99.9), noise floor inside the pause steady within 3 dB,
  level and pitch drift no larger than at the natural sentence gaps (+1 dB / +1 st).

`measure.py` measures both sides (the reference narrators and our renders) the same way.

## Gate results (13 Nov, before the finish: loudness -15.6 LUFS, pauses as generated)

| script | voice | length | word error (large-v3) | wpm / while speaking | range | loudness | joins with artefact | render s per min of speech |
|---|---|---|---|---|---|---|---|---|
| founder story, band 03 | atlas | 58 s | 0.0% | 180.5 / 194.3 | 11.9 st | -15.6 LUFS | 0 of 5 | 99 |
| long-form excerpt, band 04 | sage | 9 min 9 s | 0.12% | 183.0 / 193.0 | 12.9 st | -15.6 LUFS | 0 of 55 | 284 |
| energetic hook, band 03 | blaze | 14 s | 0.0% | 193.1 / 197.7 | 11.2 st | -15.6 LUFS | 0 of 2 | 632 |

Render seconds are wall time for generation plus decode on the owner's M-series Mac while other
lanes shared it; a hook pays for its ladder (one chunk took all 8 takes to sit in the band).
