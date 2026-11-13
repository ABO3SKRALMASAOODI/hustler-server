# Valmera's licensed music library (13 Nov 2026)

The owner, 13 Nov: "check the music at the references. Those are the type of music that the creators use and
aligns with the videos well ... optimize for the highest quality." Valmera may choose the music; the customer
decides whether to keep it, and the clean export without it is always kept.

- `manifest.json`: the tracks. Each row: `cid`, title, author, licence, licence URL, the credit line a CC-BY track
  needs, the publisher's own file (`source_url`, `source_sha256`), the storage key it is kept at after its first
  use (`music-library/<cid>.mp3`), its loudness (`lufs`), and its measurements on the references' ruler: `z` (PANNs
  Cnn14 embedding of the music, PCA 32), `bpm`, `mode`, `centroid_hz`, `tags`, `vocals`, `curve` (level per second,
  dB), and the product's rhythm engine's `sections`, `downbeats`, `phrase_ends`, `drops`. `kind` is the kind of
  film it was kept for; `fit` is how close it sits to each kind's references, relative to how close those
  references sit to each other.
- `profiles.json`: the references' music by kind of film (podcast, marketing, story, long-form): the same embedding
  per reference, tempo band, minor share, level under the voice (K-weighted and plain RMS), lift in the voice's
  gaps, energy shape, rises / drops / stops per minute, what the music does at each kind of story turn, endings.
- `LICENSES.md`: every track's licence and credit line.

Code: `worker/music_choose.py` (the chooser; read its module doc). Study and build tools:
`.valmera/week-1112/lanes/MUSIC/tools/`. Notes: `.valmera/week-1112/notes/MUSIC.md`.
