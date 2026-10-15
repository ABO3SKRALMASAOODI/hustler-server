# Wave 2 interfaces: "The voice and the beat"

Wave 2's contract pass (ROADMAP §3 rule A8, Wave 2 tracks 2A–2F). It extends
`INTERFACES.md` (Wave 0) and `INTERFACES_W1.md` (Wave 1), which stay binding
where this file is silent. One agent builds the whole wave on
`claude/w2-engine`; this file is what Wave 3/4, the reviewer and the
integrator code against.

- **Base.** `claude/w1-engine` (4b11a65), re-merged at milestones.
- **Precedence.** ROADMAP.md wins on intent; this file wins on names, shapes
  and ownership; where it re-pins a Wave 0/1 pre-pin (§9) it wins.

---

## 0. Rules every Wave 2 module inherits

Everything in `INTERFACES.md` §0 and `INTERFACES_W1.md` §0 still holds.

1. **Owner rules (hard).** Agents never choose music: every Wave 2 rhythm,
   montage and arrangement path reads only a track the USER supplied
   (`MusicItem.provenance` in `user_upload | user_named |
   user_picked_library`); with no such track the clock is the speech
   prosody grid and music verbs refuse with the upload/name/pick offer.
   Sound effects only from the approved library and only where earned. No
   Wave 2 card sets `adds_zoom` or `adds_sound`; no format pack, voice or
   planner default adds a zoom or a sound. The end card and the free-tier
   watermark stay bit-exact: every Wave 2 compiler emits before them
   (tests `tests/test_w2*_brand*`). Flash safety stays on (≤3 full-frame
   flashes/s in automatic proposals; an opt-in flags `contains_flashing`).
2. **B&W is a device, never a look.** No Wave 2 pack, voice, preset or
   planner proposes a whole-short monochrome grade. Monochrome appears only
   as a bounded device: a B&W section that opens to colour on a drop or a
   turn (1E `grade_window` with `ramp_out_f`), B&W with one colour kept
   (1E isolate / 1B subject isolate), a B&W silhouette hold (1B poster /
   background), a negative on a hit (1D accent). A whole-short mono grade is
   written only when the user's own words ask for it
   (`creative.mono_allowed(user_text)`). `apply_look` stops offering
   `mono_noir` unprompted (§8).
3. **Range over house style.** Planners (`story_planner`, `typeset.voices`,
   format packs) are seeded per story and remember what the project already
   used (`creative.variety_ledger`): the same voice, layout family or
   signature device is never the default twice in a row inside a project;
   every proposal carries a reason tied to the story beat it serves.
4. **No new paid capacity, no new job kinds.** Rhythm analysis is DSP
   (numpy/scipy, no model) and runs on any lane, cached by audio sha256 in
   the sidecar store as kind `rhythm.music`; Demucs stems (existing, batch)
   refine it when already warm. Embedding models (`embed.siglip2`,
   `embed.dinov2`) stay reserved and dark: Wave 2 library recall runs on
   model-free descriptors (§6). No model enters `models.lock` in Wave 2.
5. **Tool reach.** Every Wave 2 device is writable and removable through
   `apply_edit_batch` (motion items with `template: "typeset"`, fx items of
   type `arrange`, speed spans, music-layer fields, insert/overlay items);
   verbs only SOLVE arguments into those operations and commit through
   `tools_ext._write.commit`.
6. **Browserless.** Every verb, solver and planner runs on the agent, MCP
   and Shorts lanes (no Chromium, no models). Type geometry is measured in
   Python (`typeset.shaping`, uharfbuzz when installed, fontTools always).
7. **Old EDLs and caches.** Nothing changes for an EDL that uses no Wave 2
   device. New fields default to the inert value. No `*_VERSION` stamp is
   bumped by Wave 2 (`tests/fixtures/w1_render_stamps.json` stays green).

---

## 1. File ownership (Wave 2)

| Track | Owns (new unless noted) |
|---|---|
| 2A | `typeset/` (`__init__`, `shaping`, `roles`, `ladder`, `freespace`, `layouts`, `solver`, `layoutdoc`, `memory`, `aspect`, `voices`), `type_metrics.py` (extend), `tools_ext/typeset_tools.py`, `skills/typesetting.md`, cards `typeset_*`, `tests/test_w2a_*`, `cases/2A/` |
| 2B | `motion/templates/typeset.html`, `motion/lib/typefx.js` (`MG.typefx`), `motion/lib/paths.js` (`MG.paths`), new faces in `motion/fonts/` + `motion/fonts/manifest.json` + `motion/fonts/licenses/`, `fonts_brand.py`, `glyph_outlines.py`, `skills/type-fx.md`, cards `typefx_*`, `tests/test_w2b_*`, `cases/2B/` |
| 2C | `rhythm/` (`__init__`, `dsp`, `analyze`, `drums`, `downbeats`, `sections`, `drops`), `syllables.py`, `lyrics_align.py`, `audio_checks.py`, `motion/lib/beats.js` (extend: `MG.beat`), `tools_ext/rhythm_tools.py`, `skills/rhythm.md`, cards `rhythm_*`, `tests/test_w2c_*`, `cases/2C/` |
| 2D | `montage.py`, `arrangement.py`, `ramps.py`, `still_motion.py`, `fx_schemas/arrange.py`, `tools_ext/montage_tools.py`, `skills/montage.md`, cards `montage_*` / `arrange_*` / `ramp_*` / `still_*`, `tests/test_w2d_*`, `cases/2D/` |
| 2E | `library.py`, `story_match.py`, `stills.py`, `callbacks.py`, `loops.py`, `arc_planner.py`, `virtual_shots.py`, `tools_ext/structure_tools.py`, `skills/structure.md`, cards `recall_*`, `tests/test_w2e_*`, `cases/2E/` |
| 2F | `story_planner.py` (device planner v2, wrapping 1F's `device_planner` through its public functions), `creative.py` (variety ledger, B&W rule, invention prompts), `formats/{A,B,C,H}.json` + `formats/__init__.py`, `reference_devices.py`, `variants_edit.py`, `meaning_map.py`, `tools_ext/format_tools.py`, `skills/creative-direction.md`, `tests/test_w2f_*`, `cases/2F/`; wave close-out of `agent_prompt.py` catalog lines and the shared skills |

Shared-file edits are surgical and listed in the progress file
(`.valmera/engines/w2_PROGRESS.md`) and the release doc.

---

## 2. Departments and verbs (`tools_ext`)

Two departments join `tools_ext.DEPARTMENTS`: **`type`** and **`direction`**
(core growth: the names in the `load_tools` / `expand_toolset` enums, 38 B).
`story` is NOT used: it is an existing core domain (cutting tools), and a
department of that name would merge into it.
Wave 2 verbs:

| Module | Department | Verbs |
|---|---|---|
| `tools_ext/typeset_tools.py` | type | `add_typeset`, `set_typeset`, `remove_typeset`, `import_font` |
| `tools_ext/rhythm_tools.py` | rhythm | `analyze_music`, `get_syllables`, `check_speech`, `align_lyrics` |
| `tools_ext/montage_tools.py` | rhythm | `plan_to_music`, `shape_music`, `set_speed_ramp`, `animate_still` |
| `tools_ext/structure_tools.py` | direction | `find_shots`, `suggest_match_cuts`, `pick_still`, `plan_loop` |
| `tools_ext/format_tools.py` | direction | `suggest_story`, `apply_format_pack`, `analyze_reference`, `render_variants` |

Budgets: department compact schemas ≤ 12,000 B each (rhythm is shared with
1D: 1D ≤ 6,000, 2C+2D ≤ 6,000); every full description ≤ 900 characters;
core compact catalog growth from Wave 2 ≤ 150 B (department names + Wave 2
skill names in `read_skill`'s enum), ceiling 82,700 unchanged. Read-only
verbs (`analyze_music`, `get_syllables`, `check_speech`, `align_lyrics`, `find_shots`, `suggest_match_cuts`,
`pick_still`, `plan_loop`, `suggest_story`, `analyze_reference`) are added to
`backend/routes/mcp.py` `_READ_ONLY_EXACT`.

---

## 3. Typesetting (2A ↔ 2B): one item, intent in, geometry solved

### 3.1 The device is a motion item

```json
{"id": "ts1", "template": "typeset", "start": 4.2, "end": 7.9,
 "layer": "above_captions" | "behind_subject",
 "params": {"text": "AI may be *bigger* than the internet",
            "layout": "auto", "voice": "auto", "depth": "front",
            "verb": "auto", "accent": "#e8202a", "ink": "#f7f5f2",
            "seed": 7, "region": "auto", "material": "flat",
            "path": "line", "flip_at": 0, "callback_of": "",
            "mirror": false, "scale": 1.0, "emphasis": []}}
```

- **Intent only** (ROADMAP A4). `params` hold the words as printed, the
  layout family, the type voice, depth, entrance verb, palette, material,
  path and seed. No pixel geometry is stored.
- **Solved at render, per canvas.** `motion_templates.build_job` calls the
  derived-params seam (§3.3): `typeset.layoutdoc.page_params(item, W, H, fps)`
  returns `{"_layout": LayoutDoc}` for the actual canvas, so 9:16, 4:5 and
  16:9 masters re-solve from the same item. The same function runs at write
  time on browserless lanes (footprint, keep-out, audit), so the write and
  the render agree by construction.
- **Captions.** The template `reads_phrase` (its words land on their spoken
  onsets through `caption_carry.attach_readings`) and `mutes_captions`
  (it replaces the captions for its words). Kinetic type that DUPLICATES
  the captions is refused by the verb.
- **Depth.** `depth: behind` writes `layer: behind_subject` and a 1C
  `composite: {"ghost": "auto"}`; `ghost` writes the stroke pass only;
  `auto` picks behind for a hero lockup when the program matte is
  available and the solved `glyph_visible` estimate is 0.65–0.9.

### 3.2 LayoutDoc v1 (the contract with 2B, 3C and 4E)

```
LayoutDoc { version: 1, canvas: {w, h, fps, aspect},
  runs: [{ id, text, shows_words: [word index into the item's text],
           font: {family, file, axes: {wght, wdth, ...}, style},
           size_px, tracking_em, leading, x, y, w, h, baseline, rot_deg,
           role: connector|payload|number|negation|emotion|quote|profanity|accent,
           fill: solid|gradient|outline|ghost|texture|plate,
           colour, material: flat|neon|crt|outline|ghost|emboss|texture,
           path: {kind: line|arc|wave|contour, cx, cy, rx, ry, a0, a1, amp, pts},
           anchor: screen|track|plane, layer: above|below|behind_subject|screen,
           blend, t_in, t_out,
           timeline: [{t, verb, params}] }],
  keepouts_used: [{kind: face|watermark|caption_band|safe, box}],
  solver_report: {family, voice, score, payload_cap_pct, ladder, glyph_visible_est,
                  min_clearance_pct, collisions, ms} }
```

Coordinates are design pixels on the solved canvas (`motion_engine.design_size`).
`typeset.html` only renders runs; it never measures or moves anything.
Python vs Chromium bbox IoU ≥ 0.95 (parity test, §10).

### 3.3 Seams (surgical shared-file edits)

- `motion_templates.build_job`: after `_series`, `params.update(
  derived_params(item, out_w, out_h, fps))`, where `derived_params` resolves
  `DERIVED = {"typeset": "typeset.layoutdoc:page_params"}` lazily and fails
  open (an exception leaves the page to draw its own fallback).
- `motion_engine`: faces listed in `motion/fonts/manifest.json` join
  `MOTION_FONT_FACES` (one loader call); nothing else.
- `caption_place.choose` (defect #20, the placement cliff) and the band-slam
  corner floor (defect #5): fixed inside their own functions, with tests.

### 3.4 Type voices (`typeset.voices`)

A voice is a named bundle (families per role, ladder, case, tracking,
palette rule, layout preferences, verbs, material). Shipped voices:
`magazine`, `whisper_shout`, `staircase_red`, `slot_blackletter`,
`neon_script`, `editorial_serif`, `quote_window`, `condensed_poster`,
`lamp_karaoke`, `hairline_wide`, `marker_note`, `swiss_grid`. `auto` picks
by story beat and the project's variety ledger (§0.3): never the same voice
as the previous typeset in the same project unless it is a callback.

### 3.5 TypeFX (2B)

`MG.typefx` verbs (pure functions of t): `cut`, `blur_resolve`, `settle`,
`decay`, `scramble_lock`, `mosaic_resolve`, `stretch_smear`,
`duration_wipe`, `strobe_flip`, `emboss_ink`, `echo_stack`, `outline_fill`,
`glyph_morph`, `lamp` (per-word ignition on the spoken onset), `typewriter`,
`drop`. Materials: neon, crt, led, outline, ghost, emboss, texture.
`MG.paths`: arc, ellipse, wave, polyline (contour), with glyph placement
along the path. Glyph outlines for `glyph_morph` come precomputed from
`glyph_outlines.py` (fontTools) inside `_layout`; opentype.js and flubber
are never loaded (200 KB cap).

---

## 4. Rhythm (2C)

- **RhythmMap** is the Wave 0 shape (`INTERFACES.md` §4.6), produced for a
  user track by `rhythm.analyze.analyze(path) -> RhythmMap` (producer
  `rhythm.dsp@1`): onsets with classes `kick|snare|hat|perc|transient`
  and strength, beats, downbeats, bpm, sections (`intro|verse|build|drop|
  break|outro` with energy), drops, gaps (< −40 dBFS or ≥ 18 dB dips),
  `confidence`. Extra keys (allowed, ignored by Wave 0 readers):
  `"808": [{t, hz}]`, `"phrase_ends": [t]`, `"stutters": [{t0, t1, period}]`.
- **Cache.** Sidecar kind `rhythm.music` (one chunk = the whole track,
  source = the music asset's sha256), format `rhythm.json.v1`. A missing
  map is computed inline on any lane (≤ 20 s for 3 min on 1 vCPU) and put.
- **Syllables.** `syllables.split(words, samples, sr) -> [{word_i, k, t0,
  t1, text}]`: speech-band envelope nuclei inside each word span, matched to
  pyphen hyphenation (fallback: vowel groups). Clock: the words' own.
- **Lyrics.** `lyrics_align.align(lyrics_text, asr_words) -> [{w, t0, t1,
  conf}]` (DP over normalised tokens; unmatched words interpolated).
- **Audio checks (defects #25–#27).** `audio_checks.py`: `word_audit`
  (energy evidence per transcript word and between words, deterministic,
  replaces the LLM-only review for questions about specific words),
  `clean_edges(keep, samples)` (a cut never lands inside voiced energy or
  leaves a fragment shorter than 80 ms), `sentence_start(words, t)` (a
  short never opens mid-question).
- **Page clock.** `MG.beat(t, kind?)` in `beats.js`: `{i, phase, since,
  until, down}` for the item's `_beats`.

## 5. Montage, arrangement, ramps, moving stills (2D)

- **Montage.** `montage.plan(slots, shots, *, mode, lead_f=1) -> ops` (DP
  over beat slots; modes `strobe|pulse|hold` per section; no cluster repeat
  within 6 cuts; scale alternates; cut lead 1 frame before the kick, 0–2
  allowed). Output is ONE apply_edit_batch (inserts / keep edits / strobe
  fx). The intent is stored as `fx` item `arrange` kind `montage_intent`
  (no-op at render) so a song swap re-plans deterministically
  (`hooks.register("music_swap", montage.on_swap)`).
- **fx type `arrange`** (`fx_schemas/arrange.py`, stage `music_bus`,
  compiler `arrangement:ARRANGE`): kinds `hole` (gain hole, 10 ms fades),
  `hold` (held silence before the payoff), `filter` (low/high-pass sweep
  opening on a moment: `lowpass|highpass`, `from_hz`, `to_hz`, `ramp_s`),
  `stutter` (1/8 or 1/16 slices), `montage_intent` and `fit_intent`
  (records only). Every kind needs a `music_id` whose item has user
  provenance.
- **Fit.** `arrangement.fit(music_item, rmap, payoff_t, program_s)` solves
  the music item's own `offset_s`, `start`, `end`, `fade_in_s`,
  `fade_out_s` so the drop downbeat lands on the payoff onset (±1 frame)
  and the bed ends on a phrase boundary before the end card. Fields only:
  `MusicItem.automation` stays unowned (None only, §9).
- **Ramps.** `ramps.plan(src0, src1, curve, *, fps_src, fps_out)` →
  contiguous `speed` spans (an eased curve quantised to ≤ 12 constant
  steps, never a factor of exactly 1.0), monotonic by construction.
- **Moving stills (defect #21).** `still_motion.move(asset, kind, dur,
  canvas)` pre-renders a Ken Burns / parallax-drift / push clip from a
  still (ffmpeg `zoompan`-free: exact-subpixel `crop`+`scale` per frame
  from an eased path) into a project video asset; `animate_still` swaps the
  insert/overlay to it. No renderer change.

## 6. Library recall and story planners (2E)

- **Descriptors** (`library.describe(frame) -> vec`, model-free): 8×8
  luma layout, 4×4 Lab colour layout, edge-orientation histogram (GIST-
  like), face boxes (YuNet when present, Haar fallback), Hough circles,
  colour temperature + brightness (day/night tag), sharpness. Stored per
  sampled frame in the sidecar store under kind `embed.dinov2`'s reserved
  successor name **`embed.classic`** (format `embed.classic.v1`).
- **Finders** (`story_match`): `same_framing`, `day_night`, `shape_chain`
  (circles/boxes), `face_aligned` (eye-line conform), `match_action`;
  `stills.pick(window, box)`; `callbacks.find(words)` (repeated sentences,
  feeding `typeset.memory`); `loops.plan(edl)` (a rhyme frame placed before
  the end card); `arc_planner.density(words, sections)`;
  `virtual_shots.macros(source, saliency)`.

## 7. Agent layer v2 (2F)

- **Format packs** `formats/{A,B,C,H}.json`: `{id, structure: [beats],
  palette: {device families: budgets}, voices: [...], punctuation,
  junctions, colour_world: {rule, never: ["whole_short_mono"]},
  footage_quotas}`. Selection follows `grammar.py` classify-or-abstain;
  the user's words win.
- **`story_planner.suggest(edl, index, *, intensity, seed, asked)`** →
  ≤ 8 candidates across Wave 1 + Wave 2 devices, each with `op` (exact
  apply_edit_batch operation), `beat` (the story moment), `reason`,
  `invention` (what is new about it for this story), `exemplar`, `cost`.
  Seeded by the story so two different shorts never receive the same plan;
  `creative.variety_ledger(project)` down-ranks what this project already
  used.
- **Reference transfer.** `reference_devices.analyze(path)` (refsig
  detectors on a user-pasted reel) → device-class sequence; `transfer` maps
  it onto our devices without imitating creator marks.
- **Variants.** `variants_edit.branches(edl, axis="voice"|"pack", n=3)` →
  EDL branches rendered as cheap previews.

## 8. Looks and B&W (2F close-out)

- `apply_look` stays available; its descriptions and the skills no longer
  present `mono_noir` as a default choice; the agent prompt's look guidance
  names B&W only as a device (§0.2). `creative.mono_allowed(text)` is the
  one predicate every planner and pack consults.

## 9. Re-pins

- `MusicItem.automation` stays None-only through Wave 2 (arrangement is
  `arrange` fx items + music-layer fields: one representation).
- `MotionItem.anchor` stays Wave 3's.
- `embed.classic` joins `sidecars.RESERVED_KINDS` (model-free; Wave 2).

## 10. Tests and acceptance

- Files: `tests/test_w2a_*` … `tests/test_w2f_*`, `tests/test_w2_seams.py`
  (departments, catalog growth, typeset seam, arrange registration, B&W
  rule), backend `test_backend_motion_edl.py` extended for `arrange` and
  `typeset`.
- Refbench cases in `benchmarks/refbench/cases/2A…2F/` (engine mode as
  apply_edit_batch ops).
- Boards: `.valmera/refbench/boards/wave2/`. Release:
  `.valmera/refbench/WAVE2_RELEASE.md`.
