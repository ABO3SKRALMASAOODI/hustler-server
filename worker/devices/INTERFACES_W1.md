# Wave 1 interfaces: the contracts six tracks build on in parallel

Round lead's interface pass for Wave 1, "One matte, every hit" (ROADMAP §3
rule A8). It extends `INTERFACES.md` (Wave 0), which stays binding where this
file is silent.

- **Base commit.** The skeleton commit on `claude/w1-base`, cut from the
  production release `e8a775f`. It holds every registration, stub and seam
  named here, all switched off: nothing renders differently, no tool is added,
  the core compact catalog is byte-identical (81,688 bytes).
- **Track branches.** `claude/w1-1a` … `claude/w1-1f`, worktrees
  `/Users/masaoodi/Documents/Valmera/worktrees/w1-<track>`, all from the base.
- **Status.** Binding. Only the round lead edits this file.
- **Changing a contract.** Ask in your report and code against the pinned
  version until the round lead re-pins it.
- **Precedence.** ROADMAP.md wins on intent. This file wins on names, shapes
  and file ownership. Where this file re-pins a Wave 0 pre-pin (§3.7), this
  file wins.

---

## 0. Rules every track inherits

Everything in `INTERFACES.md` §0 still holds. Wave 1 adds:

1. **Owner rules (hard).** Agents never choose music. Sound effects only from
   the owner-approved library, only where earned. Zooms and sounds are
   optional, never defaults: no Wave 1 card sets `adds_zoom` or `adds_sound`.
   The end card and the free-tier watermark stay exactly as they are: every
   stage compiles before the aspect bars, the screen-frame plate, the
   watermark and the end card, and each track adds a bit-exact brand test for
   its compilers (the AT-0B-5 pattern). Flash safety stays on: automatic
   proposals keep at most 3 full-frame flashes per second; a user opt-in
   flags the export `contains_flashing`.
2. **No new paid capacity.** The vision lane stays dark. Sidecars run on the
   batch lane through the one `sidecar` job kind. No Wave 1 track adds a job
   kind, a lane or a model. The shared R2 clip store stays off by default.
3. **Tool reach.** MCP clients cache `tools/list` when they connect, so a
   long-lived client never sees a department verb added later. Every Wave 1
   device must therefore be writable AND removable through
   `apply_edit_batch` (the `fx` layer, and `composite` on motion items),
   validated exactly like its verb (§5).
4. **Inert until live.** A Wave 1 member module registers its fx classes only
   when its track sets `LIVE = True` in that module (one line, in a file it
   owns), or when the preview switch `VALMERA_FX_PREVIEW` is set (`1`, or a
   comma list such as `1b,1d`; tests and refbench only, never production).
   A department registers only when its `TOOL_SPECS` is non-empty. Flip
   `LIVE`, fill `TOOL_SPECS` and ship the cards in the same commit: a card's
   `verb` must be a live tool and its `fx_type` a registered type
   (`tests/test_w0c_cards.py`).
5. **Old EDLs and caches.** Old EDLs stay valid with unchanged signatures.
   Cached renders stay untouched unless a device is used. Bump a `config.py`
   version stamp only for a rendering change that must reach cached renders,
   and only with the round lead (`tests/test_w1_seams.py` pins the stamps).
6. **`renderer.py` is 1A's alone** in Wave 1. A track that needs a renderer
   change asks 1A through the round lead; §1.6 lists every seam 1A builds for
   the others.
7. **Browserless lanes.** Production agent, MCP and Shorts lanes have no
   Chromium and no models. Every verb, planner and validator works there;
   models run only as sidecars; renders never run models.
8. **Proofs.** Acceptance tests and internal boards may use the footage
   library windows (`benchmarks/footage/vfl.json`, trimmed windows in
   `/Users/masaoodi/Documents/Valmera/.valmera/fixtures`) and the house
   stand-ins (`fixtures/CATALOG.md`). Do not re-edit the old showcase shorts
   for the owner: the owner-facing proof of Wave 1 is the next NEW podcast
   run after release, made by the release, not by a track.
9. **Data hygiene.** The 41 references
   (`/Users/masaoodi/.openclaw/workspace/references`) are internal
   comparison input only: never uploaded, published, committed or put in
   product storage. Music fixtures (the owner's pick, Drake "National
   Treasures", `fixtures/music/drake-national-treasures`) are internal only.
10. **Scratch** goes to
    `/private/tmp/claude-501/-Users-masaoodi-Documents-Valmera/3950ff90-9877-4a92-8ad5-9b6b2ec520c6/scratchpad/w1-<track>/`.
11. **Git.** Commit with
    `git -c user.name=ABO3SKRALMASAOODI -c user.email=shmarymuslim@gmail.com commit`,
    messages ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
    Never push, pull, rebase or stash. Never touch
    `/Users/masaoodi/Documents/Valmera/hustler-server` or another worktree.

---

## 1. The program matte, masks and regions (1A → 1B, 1C, 1D, 1E, 1F)

All in `fxgraph.py` (function-level owner **1A**: `request_matte`,
`matte_info`, `program_matte`, `handle_matte`, `_person_mask`). The base
commit holds the stubs below; consumers code against them today and treat
"absent" as their substitute rung.

### 1.1 Prepare time: ask, and learn what you will get

```python
info = fxgraph.request_matte(plan, item, label="person", space="world")
info = fxgraph.matte_info(plan, label="person")       # read-only, same dict
```

- Call `request_matte` from your compiler's `prepare(plan, item)`. It records
  `(label, space)` in `plan.matte_requests`; the renderer builds a matte
  chain **only** when something asked (no request, no mask filters: md5
  identity).
- `space`: `world` (the picture's geometry before the camera; rides zooms)
  or `screen` (after the camera; screen-locked).
- **MatteInfo** (pinned shape):

  | Key | Meaning |
  |---|---|
  | `label` | `person` (or `face`) |
  | `status` | `ready` (every window measured), `partial` (some windows black, inert), `absent` |
  | `source` | `rvm`, `u2net`, `photometric` or None |
  | `reason` | one human line, used as the report's degrade reason |
  | `windows` | `[{t0, t1, status, source, flicker, edge_chatter, ok}]` in PROGRAM seconds. `ok` False = chunk QA over the bar: crisp devices (fill, outline, poster) demote to soft ones (glow) in that window |
  | `qa` | `{flicker, edge_chatter}` program-wide maxima, or `{}` |

### 1.2 Emit time

- `fxgraph.program_matte(plan, space)` returns a **fresh** gray pad (program
  W×H, program rate, full length, 255 inside, black inert) or None. Inside a
  stage, `ctx.mask("person")` is the same call in the stage's own space
  (`person@world` / `person@screen` to choose).
- `fxgraph.handle_matte(ctx, side)` at the `junction` stage: the person
  matte of one side's 2h-frame handle stream (`"a"` outgoing, `"b"`
  incoming), measured on the handle's own source frames (frames past an
  out-point are not in the program matte). 1D's `matte_cut` takes its
  substitute rung on None.
- 1A builds both from `matte.person` sidecars (source space, chunked), through
  the **same** per-block chain as the picture (normalize, follow window, card
  source rect, speed pieces, concat); the `screen` variant also goes through
  the camera chain; finals use `fxprims.mask_upsample` (guided) with the
  picture's luma; previews skip it. Missing chunks are black.

### 1.3 Mask labels (grammar unchanged: `<label>[@world|@screen]`)

| Label | Provider | Track |
|---|---|---|
| `person` | `fxgraph._person_mask` → `program_matte` | 1A |
| `face`, `face:<index>` | `matte_track:face_mask` (faces.yunet boxes as soft ellipses) | 1A |
| `glyph:<motion_id>` | `compositor:glyph_mask` (that motion item's clip alpha in program space) | 1C |
| `region:<fx_id>` | `regions:region_mask` (that fx item's `region`) | base (done) |
| `hue:<rrggbb>` | `gradewin:hue_mask` (the `hue_key` of that hue) | 1E |

- Providers in track modules are listed in `fxgraph.MASK_PEERS` and resolved
  on first use; a module that does not exist yet reads as absent.
- A provider that derives its mask from the picture (`hue:`) splits
  `ctx.label` and **rebinds** `ctx.label` to the pass-through branch. A
  compiler reads `ctx.label` after its last `ctx.mask()` call.
- Inversion and morphology are consumer parameters, never labels:
  `mask_ops` in `fxprims` — `fxprims.mask_ops(parts, in, out, uid=,
  invert=, grow_px=, feather_px=)` (3×3 dilation/erosion passes, gaussian
  feather, negate; list `fxprims.MASK_OP_FILTERS` in your `FILTERS`).
- Edge band, onion skins and frame offsets (echo k frames back) are built by
  the consumer from a fresh pad (`split`, `setpts`).

### 1.4 Regions (`regions.py`)

One resolver for every `FxRegion` (§2): an accent's, a subject effect's, a
region effect's, a grade window's, a finish's.

```python
regions.prepare(plan, item, region, key="region") -> None | (rung, reason)
regions.pad(ctx, item, region, key="region") -> pad | None     # one per key
regions.bbox(region, W, H) -> (x, y, w, h) | None               # static only
```

| Shape | Built by | Owner |
|---|---|---|
| `rect`, `ellipse`, `polygon` with `box`/`points` | a PIL still, feather and invert baked in | base (done) |
| the same with `keys` | `regions.keyed_prepare` / `regions.keyed_pad` | 1C |
| `glyph` | the `glyph:<ref>` mask | 1C |
| `face` | the `face` mask | 1A |
| `card` | `regions.card_prepare` / `regions.card_pad` (the picture card's inner rect, rounded, from `picture_cards`) | 1D |

### 1.5 Cross-track calls (`fxgraph.peer`)

Call another track's function by name; it returns `default` when the module
or function does not exist yet or raises, so a track never imports a sibling
that has not merged:

```python
fxgraph.peer("compositor:glyph_box", plan, motion_id, t, default=None)
```

| Peer | Signature | Owner | Consumers |
|---|---|---|---|
| `compositor:glyph_box` | `(plan, motion_id, t) -> [x0, y0, x1, y1] \| None` (frame fractions) | 1C | 1D iris and letter-window origins, 1F proofs |
| `compositor:glyph_mask` | mask provider (§1.3) | 1C | 1B, 1D, 1E regions |
| `matte_track:face_mask` | mask provider (§1.3) | 1A | 1C, 1D |
| `matte_track:face_box` | `(plan, t, index=None) -> [x0, y0, x1, y1] \| None` | 1A | 1D iris origin, 1C |
| `gradewin:hue_mask` | mask provider (§1.3) | 1E | 1B isolate |
| `gradewin:auto_hue` | `(tool_ctx, start, end) -> "#rrggbb" \| None` (the subject region's dominant hue, solved at WRITE time) | 1E | 1B and 1E verbs |
| `variants:make_variant_set` | `(tool_ctx, source_t, kinds) -> [asset_key]` | 1E | 1D `add_strobe(variant_set=…)`, 1F |
| `motion_qa:proof` | §8 | 1F | refbench |

### 1.6 Renderer seams 1A builds (the complete Wave 1 renderer edit list)

1. The program matte chains (§1.2), built only for `plan.matte_requests`;
   `program_matte` splits fresh pads from them; leftover tails are
   nullsinked before the brand stages.
2. The behind-subject loop calls `motion_layer.composite_behind(parts,
   vlabel, mask_label, j, fps=, motion=, ass_path=, fonts_dir=, plan=)` (the
   base commit holds it, emitting exactly today's filters; a test pins them).
   An item whose stored `behind` mask is still valid (`behind_why` None)
   keeps today's path byte for byte; the program matte serves the items that
   demote today (follow crops, cuts, ramps, cards, coverage).
3. A behind-subject item with `composite.stage == "screen"` is composited
   after the camera, through the screen-space matte, by the same call.
4. `plan=fx_plan` passed to `motion_layer.append_graph`, `overlay_clip` (via
   append_graph) and `composite_behind` (the keyword exists, unused, in the
   base).
5. `handle_matte` for junction handles (§1.2).
6. Stitched previews: mask chains respect video-local fx windows.

Nothing else in `renderer.py` changes in Wave 1.

### 1.7 Sidecars and clocks

- Wave 1 adds no sidecar kind and no job kind. It reads `matte.person` and
  `faces.yunet` (`INTERFACES.md` §2). 1A may add `matte.person` params (a
  `downsample_ratio`, a 15 fps rate for low-motion shots, never for flash or
  clone windows); a request that passes none keeps Wave 0's keys, so warm
  sidecars stay warm. Per-chunk QA goes in the manifest's `qa`
  (`flicker`, `edge_chatter`, `coverage`).
- A device write requests what its card `needs` with
  `sidecars.ensure(…, priority="interactive")` and never waits. A batch
  written by a stale client launches nothing; the first render requests the
  windows it needs (previews degrade that window, finals wait at most 90 s).
- Moments (`on=` in the verbs, `bind` in the items) come from
  `prosody_grid` (speech, the default clock for a talking head) and the
  perception beat grid of a track the USER supplied
  (`rhythm_types.to_program`). `bind.rhythm` is `"speech"` or that
  `MusicItem.id`. No verb or planner ever adds music to get a clock.

---

## 2. Shared value models (`fx_schemas/__init__.py`, round lead)

Members reuse these instead of defining their own, so one region, one
colour and one footage reference mean the same everywhere.

- **`live(flag, track)`**: the `LIVE` / `VALMERA_FX_PREVIEW` switch (§0.4).
- **`FxColour`**: `white`, `black`, `accent` (the Look's accent, resolved at
  render) or `#rrggbb` (stored lower-case).
- **`FxRegion`** `{shape, box, points, keys, ref, invert, feather_px}`:

  | shape | fields |
  |---|---|
  | `rect`, `ellipse` | `box` [x0, y0, x1, y1] frame fractions, or `keys` [{t, points: [[x0, y0], [x1, y1]], ease}] |
  | `polygon` | `points` (3–32 [x, y]) or `keys` |
  | `glyph` | `ref` = motion item id (checked by `region_check`) |
  | `face` | `ref` None (every face) or a face index |
  | `card` | nothing |

  Anchors (glyph, face, card) are preferred over boxes (ROADMAP A4). The
  person matte is NOT a region shape: anything bounded by the subject is a
  `subject` item (1B).
- **`FxSource`** `{kind, offset_s, src_t, asset_key, colour, loop}`: `program`
  (a time offset), `source` (main video from source second `src_t`; checked
  by `source_check`), `asset` (a project image or video, a 1E variant
  included; `asset_key` is policed by `edit_batch.media_keys`), `colour`,
  `next` / `previous` (the shot after / before the item's cut, aligned to
  continue at the cut).
- **`FxItemBase.flash_times()`**: the hook `flash_safety` reads (the base
  wires it): return the program seconds of every FULL-FRAME flash the item
  plays, `[]` for none, or None for the Wave 0 reading (one flash at `start`
  when its card is flash-flagged). Implement it on every type that can flash.

---

## 3. fx types, stages and slots per track

### 3.1 The table

| Track | Module (owned) | Tag | Class | Stages | `COMPILER` |
|---|---|---|---|---|---|
| 1B | `fx_schemas/subject.py` | `subject` | `SubjectFx` | world, screen | `subject_fx:COMPILER` |
| 1B | | `background` | `BackgroundFx` | world, screen | `subject_fx:COMPILER` |
| 1C | `fx_schemas/composite.py` | `region` | `RegionFx` | world, screen, pre_captions, over_type | `compositor:REGION` |
| 1D | `fx_schemas/accents.py` | `accent` | `AccentFx` | pre_captions (scope picture), over_type (scope all) | `accents:ACCENT` |
| 1D | | `strobe` | `StrobeFx` | pre_captions, over_type | `strobe:STROBE` |
| 1D | | `junction` | `JunctionFx` | junction | `junctions_v2:JUNCTION` |
| 1E | `fx_schemas/finish.py` | `grade_window` | `GradeWindowFx` | world, screen | `gradewin:GRADE` |
| 1E | | `finish` | `FinishFx` | world, screen, pre_captions, over_type | `finish:FINISH` |
| 1A, 1F | — | none | | | |

Owning a member module means: add optional fields (default None or the
inert value, so stored items keep validating), tighten `check`, implement
`flash_times` and `describe`, flip `LIVE`. Renaming or removing a pinned
field, kind or style is a round-lead change. Every authoring rule lives in
the class (field constraints, validators, `check`), never only in a verb
(§5).

### 3.2 `subject` and `background` (1B)

- `subject.kind`: `fill` (`colour`, `decay_f`), `poster` (`field`,
  `keep_footage`, `colour`), `glow` (`colour`, `radius` share of frame
  height), `outline` (`colour`, `width_px`), `negate`, `exposure` (`ev`,
  `saturation`), `isolate` (`hue` 'auto' | #hex, `hue_op` union |
  intersect: ∪/∩ with 1E's hue key), `echo` (`echo` = {mode echo | clone |
  twin | trail, offsets_f 1–240 frames back, opacity, decay, tint}).
  `scope` subject | background (fill, poster, glow, outline and echo draw the
  subject). `intensity` 0–1. Optional `region` (∩).
- `background.kind`: `solid`, `gradient` (`colour`, `colour2`, `angle`),
  `blur`, `radial_blur` (`radius`), `source` (`source`: a shot, an asset, a
  screen recording), `strobe` (`sources` 2–16, `hold_f`). `hold_subject` +
  `hold_t`: the subject cutout frozen at that program second while the
  background swaps (several items sharing `hold_t` keep one frozen frame:
  `subject_hold`).
- Canvas programs are refused (`check`).

### 3.3 `region` and `MotionItem.composite` (1C)

- `region`: `effect` negate | solarise | mono | posterize | threshold | blur |
  pixelate | exposure (`ev`) | tint (`colour`) | ribbed_glass | refraction |
  heat (`scale`; maps built once in numpy), inside `region`, at `strength`.
  `region_locality` = 0 outside.
- **`MotionItem.composite`** (slot owned by 1C, `MotionComposite`):
  `{blend, opacity, fill, fill_mode, ghost, stage, region}`.
  - `blend`: normal | screen | add | multiply | overlay | softlight |
    difference | exclusion | lighten | darken | auto (difference over busy
    plates, screen over dark ones, via `plate.py`), bbox-local through
    `fxprims.blend` (`add` maps to its `addition`).
  - `fill` (`FxSource`) + `fill_mode` inside | outside: footage inside the
    glyphs, or the glyphs as windows onto the picture with the fill around
    them; `fill.kind = next` is the cross-cut reveal (1C-5).
  - `ghost`: `auto` | `off` | {opacity 0.2–0.8, width_px 1–8, threshold}:
    the stroke-only pass (`motion/lib/paint.js`) over the subject where
    matte ∧ glyph, for `behind_subject` items.
  - `stage`: world | screen, for `behind_subject` items only (other layers
    ignore it): screen = screen-locked under a camera move (§1.6.3).
  - `region`: clip the graphic (not a glyph region).
  - **`composite: None` is exactly today's composite, byte for byte.**
    Nothing is automatic on an item that does not carry one (the ghost
    included).
- 1C implements composite in `motion_layer.overlay_clip`, `append_graph`,
  `prepare_inputs` (extra fill inputs) and `composite_behind`.

### 3.4 `accent`, `strobe`, `junction` (1D)

- `accent.kind`: negative | white_hit (`decay_f`, τ≈2) | colour_hit (`colour`;
  None = the outgoing shot's dominant hue) | threshold | posterize | mono |
  mirror | hue_step | rgb_slice (`bands`) | box_invert (`bands`, `seed`).
  `intensity`, optional `region` (a `card` region gives the card-clipped
  hits of 1D-2). One filter per kind with a union `enable` (graph size flat).
- `strobe`: `frames` [{`source` (no next/previous), `hold_f` 1–8, `negate`}],
  `pattern` sequence | pingpong | random (seeded), `fit` cover | contain |
  register. A timeline-neutral full-frame cover; `as_insert` is a verb
  option that writes an insert instead, never a field.
- `junction`: `at` (the cut), `style` (xfade: dissolve, fadegrays, pixelize,
  radial, slices, wipe_left/right/up/down; mask wipes: iris, columns,
  staircase, box_steps, bands, letter_window, matte_cut; others:
  double_exposure, pixel_stretch, hit_flash, whip_up, whip_down, zoom_blur,
  radial_blur, datamosh), `duration_s` 0.04–2 (d/2 each side,
  duration-preserving; handles from `fxgraph.apply_junction`), `origin`
  {kind centre | face | glyph | point, ref, point}, `count`, `direction`,
  `colour` (hit_flash). `letter_window` needs a glyph origin (the motion
  item whose letters window the incoming shot; its mask is
  `glyph:<motion_id>`).

### 3.5 `grade_window` and `finish` (1E)

- `grade_window`: `saturation`, `ev`, `contrast`, `temperature`, `tint`,
  `lut` {asset_key, strength, origin import | match, match_t}, `isolate`
  {hue (#rrggbb, 'auto' solved before writing), tolerance, softness},
  `split_tone` {shadows, highlights, balance}, `protect` [skin, brand],
  `ramp_in_f` / `ramp_out_f` 0–48 (mono → colour on a drop), optional
  `region`. At least one change is required.
- `finish.kind`: lcd, crt, led, halftone, xerox, band_tear, film, thermal,
  xray, blueprint, line_art, solarise, posterise, kaleidoscope,
  pixel_stretch, night_vision; `strength`, `scale`, `palette`,
  `matte_weighted` (thermal and xray read the person matte through 1A), and
  optional `region`.
- Variant STILLS are project assets, not fx items:
  `variants.make_variant_set` runs inside the verb on the agent lane from one
  proxy frame (ffmpeg + numpy, no model; ≤ 10 CPU-s for ≤ 6 kinds); kinds
  photo, negative, line, blueprint, thermal, xray, solarise, posterise,
  kaleidoscope, pixel_stretch, night_vision, halftone. The optional
  Informative-Drawings model is out of Wave 1 unless re-pinned (it would
  need a sidecar kind and a `models.lock` pin).

### 3.6 Device cards

The card schema is unchanged (`INTERFACES.md` §5; `devices/schema.py`).
Wave 1 adds:

- **Card-id prefixes, one owner each** (`devices.CARD_PREFIXES`): `type_` 1A;
  `subject_`, `background_` 1B; `composite_`, `region_` 1C; `accent_`,
  `strobe_`, `junction_` 1D; `grade_`, `finish_`, `variant_` 1E.
- **Families**: 1A `type_depth`; 1B `matte_fx`; 1C `composite`; 1D
  `punctuation` (accent, strobe) and `transition` (junction); 1E `finish`.
  The ledger already maps them (`taste._CARD_LEDGER`, `_FX_TYPE_LEDGER`).
- **Built-in substitutes** added: `flash_junction` (subject_hold and
  matte_cut → a white flash at the cut), `hue_key_only` (subject isolate →
  the hue key alone). Existing: `full_frame_flash` (fill at 0.6),
  `colour_field_card` (poster), `halation` (glow), `face_safe_slam`
  (text-behind), `type_in_front`, `static_placement`.
- **`params` keys are fx item field names** (list values are the planner's
  choices, first = default). 1F's planner builds candidates from them
  generically.
- `fx_type` is required on every card that writes an fx item, and must
  match the item's `type` (`edit_batch.check_fx_devices`).
- A card that misses acceptance ships `status: dark` with a `flag` defined
  in its track's `config.py` block (§6); a dark card's items are refused on
  every write path until the flag is on.
- No Wave 1 card sets `adds_zoom` or `adds_sound`. Flash cards set
  `flash: true` and their types implement `flash_times`.

### 3.7 Re-pins of Wave 0 pre-pins (and overlaps resolved)

- **`type_window`** stays reserved for 1C but is NOT registered: footage
  inside letters, letters as windows and the cross-cut reveal are
  `composite.fill` on the letters' own motion item (one item to write and
  remove). `add_type_window` creates that item.
- **`TransitionSpec.per_junction`** stays unowned (None only): every per-cut
  style is a `junction` item (one representation per cut). The
  `set_transitions` extension is `set_junction` in 1D's department.
- **`MotionItem.anchor`** stays None only until Wave 3 (3A/3C). Screen-lock
  in Wave 1 is `composite.stage`.
- **`MusicItem.automation`** stays None only until Wave 2.
- **`matte_flash`** is not an accent kind: `add_accent(kind="matte_flash")`
  writes a `subject` fill (1B's type). **`card_clip`** is not a kind: it is
  `region: {shape: card}`.
- **Isolation**: keep by HUE → 1E `grade_window.isolate`; keep the SUBJECT
  (with or without a hue) → 1B `subject` kind isolate. `isolate_colour`
  (1E's verb) writes whichever applies.
- **Subject-bounded vs region-bounded**: anything bounded by the person
  matte is 1B; rect/ellipse/polygon/glyph/face/card regions are 1C's
  `region` (and the `region` field of other types).
- **`matte_cut`** is a 1D junction style using 1A's `handle_matte`; 1B does
  not register it. **Mono background** is `subject` isolate, not a
  background kind.

---

## 4. Department verbs (`tools_ext`)

### 4.1 Modules and verb names (pinned; the base holds each module with an
empty `TOOL_SPECS` and its `VERBS`)

| Module (owned) | Department | Track | Verbs |
|---|---|---|---|
| `tools_ext/subject_tools.py` | compositing | 1B | `add_subject_effect`, `set_background`, `remove_subject_effect` |
| `tools_ext/composite_tools.py` | compositing | 1C | `set_composite`, `add_type_window`, `add_region_effect`, `remove_region_effect` |
| `tools_ext/punctuation_tools.py` | rhythm | 1D | `add_accent`, `add_strobe`, `set_junction`, `remove_punctuation` |
| `tools_ext/finish_tools.py` | compositing | 1E | `set_grade_window`, `isolate_colour`, `import_lut`, `match_grade`, `add_finish`, `make_variant_set`, `remove_finish` |
| `tools_ext/verify_tools.py` | verify | 1F | `suggest_devices`, `set_intensity`, `proof_device`, `audit_motion` |

Argument sketches (names pinned; types and prose are the track's):

- `add_subject_effect(kind, start?, end?, on?, count?, frames?, colour?,
  scope?, intensity?, region?, hue?, echo?)`: `on` = stress | beat |
  downbeat | onset | word | cut | phrase_end resolves moments on the prosody
  grid / perception beats and writes one item per moment, each with `bind`.
- `set_background(kind, start, end, colour?, colour2?, source?, sources?,
  hold_subject?, hold_t?)`.
- `set_composite(motion_id, blend?, opacity?, fill?, fill_mode?, ghost?,
  stage?, region?, clear?)`; `add_type_window(text, fill_source, reveal,
  start, end)`; `add_region_effect(effect, region, start, end, strength?)`.
- `add_accent(kind, at?, on?, density?, scope?, region?, frames?, colour?)`
  with `density` sparse | pulse | accelerate | 8ths | 16ths and `scope`
  picture | all (= stage pre_captions | over_type); `add_strobe(frames? |
  variant_set?, hold, start, end, pattern?, as_insert?)`;
  `set_junction(at? | cuts?, style, origin?, duration?)`.
- `set_grade_window(start, end, …)`, `isolate_colour(hue, keep, start, end)`,
  `import_lut(asset_key)`, `match_grade(hero_t, start, end)`,
  `add_finish(kind, start, end, …)`, `make_variant_set(t, kinds)`.
- Removers take `ids` (a list); `remove_finish` removes grade windows and
  finishes.

### 4.2 Rules

- Write verbs SOLVE their arguments into apply_edit_batch operations and
  commit through `tools_ext._write.commit(ctx, ops, summary)` (§5). Builders:
  `upsert_fx`, `remove_fx`, `set_composite`. Success is the `EDL vX -> vY`
  line with its ledger note. List each write verb in `WRITE_TOOLS`.
- Every item a verb writes carries `device` (its card id) and, when a
  moment earned it, `bind` (intent, ROADMAP A4).
- Gate a new type on the executor with `remote.executor_supports("fx:<type>")`
  (None = proceed) or a `DISABLED` predicate.
- A verb before its sidecar is warm calls `sidecars.ensure(…,
  priority="interactive")` and says "measuring (≈N s)"; it never waits.
- No Wave 1 track adds a parameter to a core tool, except 1F's
  `look_at(overlay=matte|free_space|beats)`.

### 4.3 Budgets

- **Core compact catalog** (cap 83,000, never raised; base 81,688). Wave 1
  growth is pinned: the `fx` layer enum (+5 B, automatic when the first
  authorable type goes live), department names in two enums (compositing
  +28, rhythm +18, verify +18, automatic), and 1F's `look_at` overlay plus an
  `apply_edit_batch` line naming the fx layer (≤ 250 B together). Ceiling at
  wave end: **82,700**. `tests/test_w1_seams.py` pins the rest of the core
  catalog byte for byte; only 1F re-pins `tests/fixtures/w1_core_catalog.json`.
- **Department compact budget** (12,000 each, tested): compositing shared
  by 1B ≤ 3,000, 1C ≤ 3,500, 1E ≤ 5,000; rhythm (1D) ≤ 6,000; verify (1F)
  ≤ 5,000. Every tool's full description ≤ 900 characters (MCP pays for it).
- MCP read-only classification: 1F adds `suggest_devices`, `proof_device`
  and `audit_motion` to `backend/routes/mcp.py` `_READ_ONLY_EXACT`.

---

## 5. apply_edit_batch: the stale-client path

**One validation, two entrances.** A department verb and an
`apply_edit_batch` call run the same code: `edit_batch.apply_batch` → the
fx_schemas member (field constraints, validators, `check`) →
`edit_batch.media_keys` (media must be attached to the project) →
`check_fx_devices` in `edit_batch` (a `device` names an existing card whose
`fx_type` is the item's type; a dark card only while its flag is on; items
the batch leaves unchanged are not re-judged) → on the worker,
`ToolContext.write_edl`'s gates; in the backend, the MCP music and web-sound
gates. Flash safety is judged at export from the EDL on both paths.

**The `fx` layer is accepted whatever the client's cached schema says**; a
stale client learns the shapes from `get_edl` and `read_skill`. Each track's
skill (`skills/subject-fx.md`, `compositing.md`, `punctuation.md`,
`finishing.md`) has a section "Through apply_edit_batch" with the exact
operations below for every device it ships.

Canonical shapes (all pinned by `tests/fixtures/w1_stale_client_ops.json`,
run through the worker and the backend):

```json
{"action": "upsert", "layer": "fx", "id": "sx_fill", "value": {
  "id": "sx_fill", "type": "subject", "stage": "world", "start": 4.0,
  "end": 4.04, "kind": "fill", "colour": "white", "decay_f": 2,
  "device": "subject_fill_flash",
  "bind": {"on": "stress", "ref": 12, "rhythm": "speech"}}}

{"action": "upsert", "layer": "fx", "id": "bg_hold", "value": {
  "id": "bg_hold", "type": "background", "stage": "world", "start": 15.0,
  "end": 15.6, "kind": "strobe", "hold_f": 2, "hold_subject": true,
  "hold_t": 15.0, "sources": [{"kind": "source", "src_t": 2.0},
  {"kind": "asset", "asset_key": "media/p1/variant_thermal.png"}]}}

{"action": "upsert", "layer": "fx", "id": "rg_glass", "value": {
  "id": "rg_glass", "type": "region", "stage": "pre_captions", "start": 1.0,
  "end": 3.0, "effect": "ribbed_glass", "region": {"shape": "face"}}}

{"action": "upsert", "layer": "motion", "id": "m1", "value": {
  "id": "m1", "composite": {"blend": "difference", "ghost": "auto",
  "fill": {"kind": "asset", "asset_key": "media/p1/starship.mp4",
  "loop": true}}}}

{"action": "upsert", "layer": "fx", "id": "ac_hit", "value": {
  "id": "ac_hit", "type": "accent", "stage": "pre_captions", "start": 16.0,
  "end": 16.04, "kind": "white_hit", "decay_f": 3,
  "region": {"shape": "card"}}}

{"action": "upsert", "layer": "fx", "id": "st_lab", "value": {
  "id": "st_lab", "type": "strobe", "stage": "pre_captions", "start": 22.0,
  "end": 22.4, "frames": [{"source": {"kind": "asset",
  "asset_key": "media/p1/variant_thermal.png"}, "hold_f": 2},
  {"source": {"kind": "source", "src_t": 25.0}, "hold_f": 2}]}}

{"action": "upsert", "layer": "fx", "id": "jn_iris", "value": {
  "id": "jn_iris", "type": "junction", "stage": "junction", "at": 10.0,
  "style": "iris", "duration_s": 0.4, "origin": {"kind": "face"}}}

{"action": "upsert", "layer": "fx", "id": "gw_mono", "value": {
  "id": "gw_mono", "type": "grade_window", "stage": "world", "start": 0.0,
  "end": 9.9, "saturation": 0.0, "ramp_out_f": 3}}

{"action": "upsert", "layer": "fx", "id": "fi_crt", "value": {
  "id": "fi_crt", "type": "finish", "stage": "over_type", "start": 25.0,
  "end": 28.0, "kind": "crt", "scale": 1.5}}
```

Removal: `{"action": "remove", "layer": "fx", "id": "<id>"}`; a composite is
cleared with `{"action": "upsert", "layer": "motion", "id": "m1", "value":
{"id": "m1", "composite": null}}`. Both restore the exact previous signature.

**Refbench engine cases express devices as these operations** (case
`edl.ops`), never as verb calls, so every acceptance test also proves the
stale-client path.

A track adds its own shapes to its tests (`tests/test_w1<x>_*.py`), not to
the round lead's fixture; a new pinned type or field reaches the fixture at
integration.

---

## 6. File ownership and one-time edits

### 6.1 Frozen (round lead)

Everything frozen in `INTERFACES.md` §1, plus: `fx_schemas/__init__.py`,
`tools_ext/__init__.py`, `tools_ext/_write.py`, `regions.py` (except the
functions assigned below), `devices/__init__.py` enumerations,
`devices/INTERFACES_W1.md`, `tests/test_w1_seams.py`,
`tests/w1_preview_probe.py`, `tests/fixtures/w1_*.json` (1F may re-pin
`w1_core_catalog.json`), and `flash_safety.py`, `edit_batch.py`,
`fxgraph.py`, `fxprims.py`, `motion_layer.py` except the functions assigned
below. `backend/tests/test_backend_motion_edl.py` is round-lead owned.

### 6.2 `config.py`

`config.py` holds one empty block per track, right after
`FLASH_SAFE_MAX_PER_S`:

```
# ---- W1 1A (matte stack v2) begin
# ---- W1 1A end
```

A track adds its flags between its own two lines and nowhere else (card
flags, matte options, budgets). Every flag defaults to the inert value.

### 6.3 Per track

**1A — Matte Stack v2.**
- Owns: `matte.py`, `personseg.py`, new `matte_track.py` (with
  `face_mask`, `face_box`), `tests/test_w1a_*`, cards `type_*`,
  `benchmarks/refbench/cases/1A/`.
- Function-level: `fxgraph.request_matte`, `matte_info`, `program_matte`,
  `handle_matte`, `_person_mask`; `motion_layer.behind_why`, `hero_front`,
  `demote`, `demote_behind`; `agent_tools._measure_subject_matte` and the
  behaviour (not the schema or description) of `add_text_behind`;
  `analysis_jobs.py`'s `matte.person` runner functions (per-chunk QA in the
  manifest `qa`); `models/rvm*.json` params (no new model).
- One-time edits: `renderer.py` (§1.6, the only Wave 1 renderer editor);
  `edit_batch.py`: the behind_subject rule only (a `behind_subject` motion
  item without `behind` is accepted once the program matte is live, and
  renders through it, degrading to the face-safe slam); `config.py` 1A block.
- Must not change core tool schemas or descriptions (1F removes the "follow
  crop disables text-behind" caveats at close-out).

**1B — Subject FX and background operations.**
- Owns: `subject_fx.py` (`COMPILER`), `sprites.py` (RGBA cutouts as project
  assets; a library function in Wave 1, no verb), `fx_schemas/subject.py`,
  `tools_ext/subject_tools.py`, cards `subject_*` / `background_*`,
  `skills/subject-fx.md`, `tests/test_w1b_*`, `cases/1B/`.
- One-time edits: `config.py` 1B block. Nothing else shared.

**1C — Footage-aware compositor.**
- Owns: `compositor.py` (`REGION`, `glyph_mask`, `glyph_box`),
  `displacement.py`, `fx_schemas/composite.py`, `tools_ext/composite_tools.py`,
  `motion/lib/paint.js`, the template `type_window` (its html and spec),
  cards `composite_*` / `region_*`, `skills/compositing.md`,
  `tests/test_w1c_*`, `cases/1C/`.
- Function-level: `motion_layer.overlay_clip`, `append_graph`,
  `prepare_inputs`, `composite_behind` (its body for items with a
  `composite`; None stays byte-identical); `regions.keyed_prepare`,
  `keyed_pad`.
- One-time edits: composite defaults (screen/add light) on the
  `flash_transition`, `light_leak` and `film_burn` template specs, through
  their composite (no core schema change); the `type_window` template's
  registration in `motion_templates.py` (one entry); `config.py` 1C block.

**1D — Punctuation, strobes and junctions v2.**
- Owns: `accents.py` (`ACCENT`), `strobe.py` (`STROBE`), `junctions_v2.py`
  (`JUNCTION`), `wipe_masks.py`, `fx_schemas/accents.py`,
  `tools_ext/punctuation_tools.py` (including the `set_transitions`
  extension as `set_junction`), cards `accent_*` / `strobe_*` /
  `junction_*`, `skills/punctuation.md`, `tests/test_w1d_*`, `cases/1D/`.
- Function-level: `regions.card_prepare`, `card_pad`.
- One-time edits: `config.py` 1D block. `flash_safety.py` needs none (types
  implement `flash_times`); the thinner `flash_safety.plan` is called by the
  verbs.

**1E — Finishing and the Variant Forge.**
- Owns: `finish.py` (`FINISH`), `gradewin.py` (`GRADE`, `hue_key`,
  `hue_mask`, `auto_hue`), `lutlib.py`, `variants.py`
  (`make_variant_set`), `fx_schemas/finish.py`, `tools_ext/finish_tools.py`,
  cards `grade_*` / `finish_*` / `variant_*`, `skills/finishing.md`,
  `tests/test_w1e_*`, `cases/1E/`.
- One-time edits: `gradelut.py` (extend: 33³ cubes, `.cube` ≤ 65³ import);
  `config.py` 1E block.

**1F — Agent layer v1 and the proof loop.**
- Owns: `device_planner.py`, `motion_qa.py`, `preference_memory.py` (extend),
  `tools_ext/verify_tools.py`, `tests/test_w1f_*`, `cases/1F/` (agent-mode
  refbench cases replace the roadmap's `acceptance/agent_cases/`).
- Function-level: `agent_tools.look_at` (the `overlay` parameter),
  `agent_tools.project_state` (`sidecars` from `sidecars.summary`,
  `matte_status`), `preview_critic.py` (motion strips; a
  `NEVER_BLOCK_CATEGORIES` finding becomes actionable only when a
  deterministic metric corroborates it), the final-export DEGRADED gate
  (`quality_verifier.unacknowledged_degradations` called on the worker's
  export path and the backend's `_final_gate`).
- One-time edits: `backend/routes/mcp.py` `_READ_ONLY_EXACT`;
  `tests/fixtures/w1_core_catalog.json` re-pin; `config.py` 1F block.
- **Close-out (after the other five merge):** `agent_prompt.py` catalog
  lines; the shared skills (`motion-design.md`, `transitions.md`,
  `music.md`, `effects-grades.md`, `text-graphics.md`) and the
  `valmera-podcast-shorts` plugin references; the follow-crop caveats out of
  the prompt, skills and the `add_text_behind` description; an
  `apply_edit_batch` description line naming the fx layer and `read_skill`.

---

## 7. Tests

- **Pinned by the base:** `tests/test_w1_seams.py` (the registry and LIVE
  switch, the preview-lane child `tests/w1_preview_probe.py`, the stale-client
  fixture, the core catalog golden and ceiling, the render stamps, the
  department verbs, card prefixes, config blocks, every shared helper) and
  `backend/tests/test_backend_motion_edl.py` (every Wave 1 type validated,
  batched and removed in the backend process). `tests/test_w0_seams.py` is
  re-pinned to read "nothing outside the pinned table", so flipping `LIVE`
  keeps it green.
- **Run before handing back:** the full worker suite from `worker/`
  (`PYTHONPATH=.. …/t.sh -q -p no:cacheprovider tests/`), once more with
  `VALMERA_FX_PREVIEW=1` for `tests/test_w0_seams.py tests/test_w1_seams.py
  tests/test_w1<x>_*`, the backend suite, `worker/skill_validator.py`, the
  plugin `test_run.py`, and the Cloudflare `npm run check` if you touched
  `worker/cloudflare` or the deploy workflow.
- **Each track adds:** its compiler tests (frame-exact windows on 25 / 29.97 /
  24 / 59.94 fps, `region_locality`), a brand-integrity test (end card md5,
  watermark ROI) for every compiler, its devices in the fault suite (every
  fault mode × its devices: 0 render failures, AT-0C-3), a backend-style
  load of its member module, and its refbench cases (`cases/<track>/`,
  engine mode = apply_edit_batch ops; `local_only` when they need footage).
- **CI** is Linux, ffmpeg 8.1, Noto fonts, Pillow with raqm: no platform pixel
  or font-metric margins; footage tests are `@local_only`. Local ffmpeg:
  `/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm`.
- **Refbench cases** keep the Wave 0 schema (`INTERFACES.md` §8.1) in
  `worker/benchmarks/refbench/cases/<1A…1F>/AT-1X-n.yaml`; engine mode
  expresses every device as `edl.ops` apply_edit_batch operations; 1F's
  agent cases use `mode: agent` with the user's sentence in `chat`.
- **Compiler protocol additions** (fxgraph reads them; all optional):
  `landing(plan, item) -> [program frame]` (the frames a device lands on;
  default: first frame, or the cut frame) and `cost_ms(plan, item)`.

---

## 8. The proof loop and the planner (1F builds on these)

**`motion_qa.proof(tool_ctx, edl, *, ids=None, window=None) -> dict`** (verb
`proof_device(ids?, start?, end?)`):
- Renders only the changed windows through the existing preview path
  (stitched, 480p, full frame rate); no new job kind.
- Returns `{edl_version, render: {variant: "proof480", wall_s, lane,
  window}, devices: [{id, type, device, family, rung, reason, landing_f,
  planned_f, strip, onion, metrics}], ledger, pass}`.
- `strip`: 10 frames from −3 to +6 around each landing frame
  (`fxgraph.landing_frames`); `onion`: the difference image against the
  render without the device.
- `metrics`: the card's `verify` entries plus `land_err_f`, `legibility`
  (min Lc), `glyph_visible`, `matte_flicker`, `flash_risk`, `duration_exact`
  (junction integrity), collisions and budget, each `{value, bar, pass}`
  from `qa_metrics.METRICS`.

**`motion_qa.audit(edl, index=None, *, intensity="designed") -> dict`**
(verb `audit_motion`, read-only, browserless): `{ledger:
taste.ledger(...).as_dict(), flash: flash_safety.assess(...), devices:
fxgraph.job_report(edl, []) (static rungs), repeats, sections, collisions,
findings: [{code, severity, id, message}]}` — one punctuation family per
section, no device on consecutive shots except callbacks, none inside a
payoff hold.

**`device_planner.suggest(edl, index, *, intensity="designed", window=None,
families=None, asked=(), max_n=8) -> [Candidate]`** (verb `suggest_devices`,
read-only): ≤ 8 ranked candidates, never a card that `devices.proposable`
refuses. A Candidate is `{device, family, verb, op, at, bind, reason,
exemplar, est_ms, owner_flags, rank}` where **`op` is the exact
apply_edit_batch operation** that writes it (the agent, or a stale client,
writes chosen candidates in one batch; the planner never writes). Items are
built from the card's `params` (§3.6) and bound to the moment that earned
them.

**`set_intensity(level)`**: calm | designed | charged; removes the
lowest-ranked items over the new budget through `_write.commit` (the diff
lists ids) and stores the level with `preference_memory` (project-level,
never an EDL field in Wave 1).

---

## 9. Integration

- Merge order: 1A, 1E, 1B, 1C, 1D, then 1F (its close-out needs the others).
  Shared-file overlaps are limited to the `config.py` blocks, the
  `motion_layer` functions (1A: behind_why / hero_front / demote; 1C:
  overlay_clip / append_graph / prepare_inputs / composite_behind) and
  `regions.py` (1C keyed, 1D card): disjoint functions.
- At integration the round lead: adds the merged types' shapes to the
  stale-client fixture, re-runs everything in §7 on the merge, renders the
  brand check on fixture windows, syncs the frontend EDL mirror
  (`composite`) at release, and records the compact-catalog delta.
- **Report on handing back:** commits, test counts (full suite, preview run,
  backend, skills, plugin), compact-catalog bytes (core and your department
  share), the AT results with numbers, any card shipped `dark` and why, and
  every contract you needed changed.
