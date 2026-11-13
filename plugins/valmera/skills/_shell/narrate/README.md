# narrate: the narrator voice for workflows 3 and 4

Local, open weights, on the Mac. VoxCPM2 (Apache-2.0) speaks; whisper hears. No paid API, no
person's recording: every voice is designed from a description.

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-narrate/bin/python
cd plugins/valmera/skills/_shell/narrate
$PY narrate.py voices
zsh .valmera/week-1106/heavy.sh narrate $PY narrate.py render script.json --out DIR --band 03   # 04 for long-form
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

## The VOICE gate (`check`)

- Word error under 1%, by whisper large-v3 (it never chose a take); the take listener's figure is
  reported beside it.
- Pace (wpm and wpm while speaking), pitch range (p5..p95 of f0, semitones) and voice loudness inside
  p10..p90 of the reference narrators in `reference-library/2026-11-05` folders 03 and 04
  (`ref_band.json`, measured by `.valmera/week-1112/lanes/M/tools/ref_band.py`: demucs vocal stem, whisper-large-v3 word timings).
- No join artefact at any chunk boundary: energy step and spectral flux under the file's own p99,
  no click (sample step over the file's p99.9), noise floor inside the pause steady within 3 dB,
  level and pitch drift no larger than at the natural sentence gaps (+1 dB / +1 st).

`measure.py` measures both sides (the reference narrators and our renders) the same way.

## Gate results (13 Nov, this code)

| script | voice | length | word error (large-v3) | wpm / while speaking | range | loudness | joins with artefact | render s per min of speech |
|---|---|---|---|---|---|---|---|---|
| founder story, band 03 | atlas | 58 s | 0.0% | 180.5 / 194.3 | 11.9 st | -15.6 LUFS | 0 of 5 | 99 |
| long-form excerpt, band 04 | sage | 9 min 9 s | 0.12% | 183.0 / 193.0 | 12.9 st | -15.6 LUFS | 0 of 55 | 284 |
| energetic hook, band 03 | blaze | 14 s | 0.0% | 193.1 / 197.7 | 11.2 st | -15.6 LUFS | 0 of 2 | 632 |

Render seconds are wall time for generation plus decode on the owner's M-series Mac while other
lanes shared it; a hook pays for its ladder (one chunk took all 8 takes to sit in the band).
