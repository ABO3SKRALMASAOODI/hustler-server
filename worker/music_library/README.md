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
- `gate.json` (15 Nov 2026): every track's verdict on the signal gate against the @metromedia.house music beds (lane
  MUSIC-SET, `.valmera/week-1112/lanes/MUSIC-SET/tools/gate.py`): `pass` (full gate), `fail` (with its reasons; such a
  track is never chosen again, anywhere) and `provisional` (passed the partial measures only). The workflow films take
  only `pass` tracks, after the reference music set.
- The reference music set's audio and its own `index.json` live only on the owner's Mac (`.valmera/models/music_ref`,
  read through `VALMERA_MUSIC_SET` by the workflow films). Since 19 Nov 2026 its rows are ALSO library rows here
  (`reference_set: true`, every one under `pass` in `gate.json`; built by `.valmera/week-1119/music/build_ref_rows.py`
  with the embedding from `embed_ref.py`), so every customer's worker chooses from it (`music_choose.reference_pool`,
  harness v2's `find(kind="music")`, `list_music_library`'s "lib:<cid>" rows, the looks' and the Director's beds).
  Their audio is kept in storage at `music-library/<cid>.mp3` (`upload_ref_b2.py`); Pixabay tracks registered with
  YouTube Content ID are left out. This repository is public: no audio file of either is ever committed
  (`.gitignore`, `worker/tests/test_music_ref_set.py`).

Code: `worker/music_choose.py` (the chooser; read its module doc). Study and build tools:
`.valmera/week-1112/lanes/MUSIC/tools/`. Notes: `.valmera/week-1112/notes/MUSIC.md`.
