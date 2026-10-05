# October 5 additional reference reels

The owner supplied eleven more local videos in Downloads. This is a technique
map from inspected frame sequences, not permission to reuse their footage,
logos, music or claims. Inventory hashes, twelve-frame boards for every reel
and dense 100 ms motion windows are in the Valmera project at
`audits/2026-10-05/reference-motion/`. Inspect the relevant original window
once when choosing a run's direction; pass a compact packet to the editors.
Do not replay the whole reference audit for each short.

| Filename prefix | Useful inspected window | Relationship to transfer |
| --- | --- | --- |
| `1d11db` | 8.8–10s | A held sentence on a quiet field links meaningful footage. Contrast comes from the cut to evidence. |
| `8b32b2` | 0–1.2s | Rounded wide footage; serif “Your” and bold “only / focus” accumulate in fixed negative space beside the face. |
| `9dd965` | 8.3–9.5s | Very large serif words behind the actual subject; one red word arrives at the consequential cue. Requires a real matte. |
| `463a93` | 5.7–6.9s | A stable paragraph builds beside Jobs and clears as a unit. Transfer stable slots and coherent phrase boundaries; enlarge for phone reading. |
| `526bd9` | 5.5–6.7s | Persistent topic above footage; ordinary speech gives way to a larger key noun. |
| `0568dc` | 0–1.2s | A specific restrained headline and short speech phrases let the interview carry the argument. |
| `03140a` | 4.9–6.1s | A central statement anchors a moving gallery. The perspective cylinder and ASCII distortion are specialized authored motion, not automatic caption options. |
| `398783` | 0–1.2s | Warm italic dialogue contrasts with a stable bold topic; the spoken idea carries the scene. |
| `c7a630` | 22.5–23.7s | One large idea anchors a gallery of actual examples. The editor timeline belongs to the reference recording; don't add it to customer edits. |
| `c7d955` | .3–1.5s | Monochrome material, small annotation clusters and one dominant object establish hierarchy. Don't copy the flashed fine print into a podcast. |
| `d0b88d` | 6.4–7.6s | Stable phrase build with a sparse serif word while both participants remain visible. |

## Shared Valmera capabilities

`set_typography_scene` handles fixed-slot phrase builds as native editable
text. Supply program `at` times from `get_kept_transcript` or `program_words`
in `compose_short.py` output. Choose the full row layout once. A small
connecting row, dominant outcome and short qualifier can form one thought;
don't center every prefix anew. One sans family plus a selective Instrument
Serif italic run can express contrast. Stable group IDs allow one replacement
or removal. Scenes, picture cards and editorial graphics can share an atomic
`apply_edit_recipe`; inspect its saved receipt before rendering.

`set_picture_card` shapes footage only, leaving type and branding sharp.
`reveal` opens inside its window; `fade`/`lift` deliberately fade the picture.
They are not seamless shape morphs. Keep ongoing faces opaque with
`entrance=none` when changing consecutive card windows. Use the existing
`add_text_behind` for a brief depth title on suitable footage; inspect the
actual matte around hair, hands and cuts. Don't fake depth with front text or
repeatedly regenerate the same matte.

For the persistent-topic format, keep separate dialogue and natural camera
changes. For the designed fast lane, make a meaning-bearing progression
(claim → evidence → consequence, for example), not a parade of generic slides.
Gallery references motivate concrete visual examples and one leader; they
don't justify fabricated metrics, random tiles or unsupported 3D effects.

## Caption defect to avoid

The previous sample used `composed` with a fade on every active word. That
made a visible word disappear at its cue; ASS alpha also leaked forward in
flow layout. Valmera now resets per-word state and treats karaoke fade as a
tint transition with stable geometry. Keep the active-word color the owner
likes. Don't fix flicker by removing all expressive type. Review consecutive
frames around two word changes, including a short connector; a settled still
cannot expose this defect.

No reference supplies a universal style or guarantees high views. Judge the
story and moving result. Keep the four formats, Sol pool, shared indexed
source, no-added-music preference and native five-second ending.
