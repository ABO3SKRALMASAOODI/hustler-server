# Wave 4 interfaces: beyond footage, every format

Interface pass for Wave 4 (ROADMAP "Wave 4 — Beyond footage, every format";
tracks 4A, 4B, 4C, 4E and 4F). One agent builds the whole wave on
`claude/w4-engine`, cut from `claude/w1-engine` (`4b11a65`). It extends
`INTERFACES.md` (Wave 0) and `INTERFACES_W1.md` (Wave 1), which stay binding
where this file is silent.

- **Not in Wave 4: 4D (the generative lane).** It needs an owner decision
  (ROADMAP §10.3). Nothing here generates pixels with a model, restores
  `generate_image` / `generate_video`, or calls a generation provider.
- **Parallel waves.** Waves 2, 3 and 4 are built at the same time from the
  same base. Wave 4 therefore adds only NEW modules and registers them
  through the Wave 0/1 seams; every edit to a shared core file is surgical
  and listed in §8. Where a Wave 4 device needs a Wave 2/3 capability that
  is not merged yet (3B depth order, 2E embeddings, 3E video slots, 2A
  typesetter), it codes against a peer (`fxgraph.peer`) or its own interim
  path and says which (§7).
- **Precedence.** ROADMAP.md wins on intent; this file wins on names, shapes
  and file ownership for Wave 4.

---

## 0. Rules every Wave 4 track inherits

Everything in `INTERFACES.md` §0 and `INTERFACES_W1.md` §0 still holds:

1. **Owner rules (hard).** Agents never choose music. Sound effects only from
   the owner-approved library, only where earned. Zooms and sounds are
   optional, never defaults: the one Wave 4 card that moves the camera
   (`depth_push_through`) sets `adds_zoom: true`, so no planner, format pack
   or look ever proposes it unless the user asked. The Valmera end card and
   the free-tier watermark stay exactly as they are: every Wave 4 stage
   compiles before the aspect bars, the screen-frame plate, the watermark and
   the end card, and each compiler has a bit-exact brand test. Flash safety
   stays on (≤ 3 full-frame flashes per second in automatic proposals; a user
   opt-in flags `contains_flashing`).
2. **B&W is a device, never a look.** Nothing in Wave 4 (format packs,
   presets, planners, the critic's fixes) applies a black-and-white grade to
   a whole short unless the user asked for it. Monochrome appears as a
   section with colour on the drop, one colour kept, a silhouette hold or a
   negative on a hit. The critic (§6) flags a constant B&W short.
3. **Invention over a house style.** The owner's bar is creativity: fresh,
   surprising, premium ideas that fit each story, with taste and restraint.
   Wave 4 guidance and the critic push range (a device the last shorts did
   not use, chosen because THIS story earns it); nothing re-applies a fixed
   recipe to every short.
4. **No new paid capacity.** The vision lane stays dark. Wave 4's one model
   (Depth Anything V2 Small) runs as the reserved `depth.small` sidecar on
   the batch lane, which already carries torch-CPU (Demucs). No new job kind,
   no new lane. The shared R2 clip store stays off by default.
5. **Models** come only from ROADMAP §7 official sources, licence-checked for
   commercial use and pinned by sha256 in `models.lock` (§2).
6. **Renders never run models.** Depth is read from sidecars; a render with
   no depth degrades (§3.2). Agent, MCP and Shorts lanes (no browser, no
   models) run every Wave 4 verb: layout and scoring are solved in Python.
7. **Tool reach.** Every Wave 4 device is writable AND removable through
   `apply_edit_batch` (`fx` layer; `motion` layer for UI scenes), validated
   exactly like its verb (the fx_schemas member holds every rule). Each
   track skill has a "Through apply_edit_batch" section with the exact ops.
8. **Inert until used.** Old EDLs stay valid with unchanged signatures;
   cached renders stay untouched unless a Wave 4 device is used. No existing
   `*_VERSION` stamp is bumped.
9. **Data hygiene.** References are internal comparison input only. Wave 4
   commits no reference frames, audio fingerprints or per-reference data;
   trend grammars (§5.3) are structural rules recognised on the USER's audio.
10. **Scratch** goes to `/Users/masaoodi/Documents/Valmera/.valmera/engines/w4/`;
    boards to `.valmera/refbench/boards/wave4/`.

---

## 1. Registrations (what Wave 4 adds to each seam)

| Seam | Wave 4 adds |
|---|---|
| `EDL.fx` tags (`fx_schemas/`) | `depth`, `time` (4A, `fx_schemas/depth.py`, TRACK `4a`); `ui_media` (4B, `fx_schemas/ui.py`, TRACK `4b`); `broll` (4C, `fx_schemas/broll.py`, TRACK `4c`) |
| fx compilers | `depth_fx:DEPTH`, `timewarp:TIME`, `ui_media:UI_MEDIA`, `broll_fx:BROLL` |
| Sidecar kinds | `depth.small` (reserved name; 4A ships the runner unless 3B merged one first — same model id, same key) |
| Departments (`tools_ext`) | `world`: `add_depth_effect`, `add_time_effect`, `remove_depth_time`, `add_ui_scene`, `remove_ui_scene`, `source_broll`, `remove_broll`; `verify`: `critique_short`; new department `delivery`: `apply_format`, `deliver_aspects`, `retypeset_language` |
| Card prefixes | `depth` 4A, `time` 4A, `ui` 4B, `broll` 4C |
| Motion templates | `ui_scene` (4B, file drop `motion/templates/ui_scene.html`) |
| Skills | `depth-time` (4A), `explainers` (4B), `broll-director` (4C), `delivery` (4E), `critic` (4F) |
| Config | one block `# ---- W4 (beyond footage) begin` / `end`, after the W1 blocks |
| Later-wave pins | `tests/fixtures/waves/w4.json` (§8.2) |

Department compact budgets (12,000 B each, tested): Wave 4's share of
`world` ≤ 5,000 B (Wave 3 owns the rest), `verify` +≤ 1,200 B, `delivery`
≤ 3,000 B. Every full tool description ≤ 900 characters.

---

## 2. Depth: the `depth.small` sidecar (4A)

- **Model.** Depth Anything V2 **Small** (ViT-S), Apache-2.0, weights
  `depth_anything_v2_vits.pth` from the official Hugging Face repository of
  the official GitHub project (ROADMAP §7). Base and Large are CC-BY-NC and
  are refused by checksum. `models/depth_anything_v2_small.json` pins url,
  sha256, bytes and licence; `models.lock` carries the entry.
- **Code.** The network definition is vendored from the official repository
  (`worker/depth_anything/`, Apache-2.0 headers kept). It needs torch, which
  only the batch image has; nothing imports it outside the sidecar child.
- **Kind spec.** `depth.small`: format `depth.npz.v1`, one `.npz` per chunk
  (`depth` uint16 [n, h, w] — relative inverse depth normalised per chunk to
  0..65535, high = near; `t` float32 [n] SOURCE seconds), keyframes at `fps`
  (default 8) on the source clock, long edge `max_side` (default 518).
  Images (asset stills) use the same kind with `src_range` [0, 0.04] on the
  asset's own sha.
- **Reader.** `depth_track.frames(source, t0, t1, dest) -> DepthClip | None`
  and `depth_track.still(asset_sha, dest) -> ndarray | None` (temporal
  interpolation between keyframes, flow-free: per-pixel linear blend; the
  consumers' maps are smoothed in time with a 3-key window).
- **Lane.** Batch, through the one `sidecar` job kind (`analysis_jobs`
  child runner `depth.small`). Previews never wait; finals wait ≤ 90 s.

---

## 3. fx types (members, compilers, rungs)

### 3.1 The table

| Track | Module (owned) | Tag | Class | Stages | `COMPILER` |
|---|---|---|---|---|---|
| 4A | `fx_schemas/depth.py` | `depth` | `DepthFx` | world, screen | `depth_fx:DEPTH` |
| 4A | | `time` | `TimeFx` | world | `timewarp:TIME` |
| 4B | `fx_schemas/ui.py` | `ui_media` | `UiMediaFx` | screen | `ui_media:UI_MEDIA` |
| 4C | `fx_schemas/broll.py` | `broll` | `BrollFx` | screen | `broll_fx:BROLL` |

### 3.2 `depth` (4A)

`kind`:

| kind | What it draws | Needs | Rungs |
|---|---|---|---|
| `parallax` | a 2.5D camera move through a still or a frozen frame: layered by depth, disocclusion edges inpainted | `source` (main-video frame `src_t`, an asset still, or None = the program frame at `start`); `move` | full (depth) → reduced (two layers from the person matte) → substitute `depth_ken_burns` → omit |
| `dof` | depth of field: what is `focus` stays sharp, blur grows with depth distance | `focus` | full (depth) → reduced (matte: subject sharp, world blurred) → omit |
| `rack_focus` | focus pulls from `focus_from` to `focus` over `rack_f` frames, landing on `rack_at` (a word onset) | `focus_from`, `focus`, `rack_at` | full (depth) → reduced (matte two-plane) → omit |
| `fog` | atmosphere by depth (far = more `colour`) | — | full → omit |
| `light` | a light sweep travelling through depth (near → far), additive | — | full → omit |
| `point_cloud` | a LiDAR-style point cloud of the shot (depth × matte), `colour` palette, scanline sweep, trails, slow orbit | — | full (depth) → reduced (matte silhouette points) → omit |
| `push_through` | **opt-in camera move**: the camera pushes through `text` set in Z; the near plane defocuses as it passes | `text` | full → omit (card `adds_zoom: true`) |
| `ken_burns` | a classic 2D move over a still (the honest substitute, also a device of its own) | `source`, `move` | full → omit |

Fields: `source` (FxSource: `source`, `asset`, or None), `fit` cover|contain,
`move` {path push_in|pull_out|drift_left|drift_right|rise|fall|orbit_left|
orbit_right, amount 0.05–1, ease linear|in_out|out}, `focus` / `focus_from`
{on subject|background|near|far|depth, depth 0–1}, `rack_at` (program s),
`rack_f` 2–48, `blur` 0–1, `strength` 0–1, `colour` (FxColour), `density`
0.1–1, `scan` bool, `trails` 0–8, `backdrop` black|dim|program, `text` ≤ 40
chars. Unused fields for a kind are refused (`check`). All pixel work for a
kind is precomputed in numpy (maps or a clip) in `prepare` and composited by
ffmpeg (`finish.window_composite`): bit-exact outside the window.

`flash_times`: `point_cloud` / `parallax` / `ken_burns` with a black backdrop
shorter than 0.5 s over the full frame count as one flash at `start`; every
other kind `[]`.

### 3.3 `time` (4A)

Picture-only, duration-preserving (the program clock and the audio never
move). `kind`:

| kind | What it does |
|---|---|
| `reverse` | the window plays backwards (sources must not be lip-sync speech unless the user asked: the verb refuses on speech) |
| `echo` | trails: `echo` {count 2–8, spacing_f 1–6, decay 0.2–0.9} |
| `posterize` | step-printing: each picture held `frames` (2–8) frames |
| `stutter` | a `frames`-frame loop repeated `repeats` times, then catches up |
| `interpolate` | real in-between frames for a slowed window: replaces repeated frames with motion-interpolated ones (`method` auto = native high-rate source frames when the source has them, else ffmpeg `minterpolate`, flagged `interp: minterpolate`) |
| `remap` | eased time remap inside the window (`curve` ease_in|ease_out|ease_in_out|hold_then_catch): slow then fast, ends on the same frame |

Speed ramps that change program length stay in `edl.speed` (core
`SpeedSpan`); `add_time_effect(kind="ramp")` writes an eased stair of speed
spans plus an `interpolate` item over the slowed part (one call, both ops).

### 3.4 `ui_media` (4B)

`motion_id` (a `ui_scene` motion item on layer `above_captions`), `slot` (an
object id of that scene graph with a media window), `source` (FxSource:
asset video/image or `source`), `fit` cover|contain. The compiler draws the
footage into the slot's solved rectangle per frame (rounded to the card's
radius), underneath the scene's chrome; `check` refuses an unknown motion
item, slot, template or layer.

The scene graph (`ui_scene.solve`, design space 1080 wide): per object
`segs` [t0, t1, x, y, w, h (from), x, y, w, h (to), opacity from, to,
ease] — enter (`snap`), morph (`morph`: eased in and out, no first-frame
jump), hold (`linear`), exit (`inCubic`); `iris` {t, dur, ox, oy, from}
(an object present in the previous scene opens the new one: the layout
morphs first, then the iris opens where the cause now rests); `count`
{t0, t1, to, landed_on_word} (lands on the spoken number said from 0.4 s
before the tile shows to 3 s after); `karaoke` (the pill's word onsets);
`updates` (content changes by scene). The template param `graph` is the
new motion-template param type `json` (motion_templates; never read as
printed text — `shown` lists what the scene prints for the captions).

### 3.5 `broll` (4C)

A full-frame cutaway over [start, end) at the `screen` stage (after the
camera, before overlays and captions: speech and captions continue over
it). Fields: `source` (FxSource kind `asset` only; `offset_s` into a clip,
`loop`), `media` video|still, `fit` cover|contain|frame (archival: the
whole picture on a soft, darkened copy of itself), `focus_x`/`focus_y` (the
cover crop's aim, from the scorer's saliency), `move` (stills only:
2.5D through the asset's `depth.small` when measured, else Ken Burns —
rung `substitute`), `depth` (bool), `match` {`asset_key` (.cube matched to
the hero frame), `strength`, `hero_t`, `de_before`, `de_after`}, `grain`,
`soften` (the de-stocking finish), `credit` (REQUIRED: the licence ledger
line — `provider`, `license`, `title`, `author`, `license_url`,
`page_url`, `source_url`, `attribution_required`, `line`; NC / ND /
reserved licences are refused by the schema; attribution-required
licences need `author` or `line`), `moment` (what it proves).
`broll_fx.ledger(edl)` lists every cutaway's line in program order.
`flash_times`: a window under 0.5 s is one flash at `start`. Rungs: full;
reduced (no match grade); substitute (a still without depth); omit.

Sourcing (`broll_source.py`): Pexels, Pixabay (keys), Openverse,
Wikimedia Commons (video transcodes + photos), NASA Image and Video
Library, the Internet Archive (archival only); `BROLL_PROVIDERS` (W4
config) narrows them. Scoring (`broll_score.py`): see §6.1 of the release
note; pure numpy/OpenCV on review frames, every lane. Acquisition reuses
`stock.download` / the `stock_acquire` egress job (redesign policy);
`stock.download` now refuses a "photo" whose bytes are not an image.

---

## 4. Verbs (department tools)

| Module | Dept | Verbs |
|---|---|---|
| `tools_ext/depth_tools.py` | world | `add_depth_effect(kind, start?, end?, on?, source?, asset_key?, src_t?, move?, focus?, focus_from?, rack_at?, text?, …)`, `add_time_effect(kind, start, end, …)`, `remove_depth_time(ids)` |
| `tools_ext/ui_tools.py` | world | `add_ui_scene(scenes, start?, end?, data?, theme?, media?, id?)`, `remove_ui_scene(ids)` |
| `tools_ext/broll_tools.py` | world | `source_broll(moments, max?, providers?, place?)`, `remove_broll(ids)` |
| `tools_ext/format_tools.py` | delivery | `apply_format(format)`, `deliver_aspects(ratios?, apply?)`, `retypeset_language(lang, translation?, srt?, captions_only?)` |
| `tools_ext/critic_tools.py` | verify | `critique_short(job_id?, asset_key?, render?, deep?)` (READ) |

Rules (as Wave 1): write verbs SOLVE arguments into apply_edit_batch ops and
commit through `tools_ext._write.commit`; every item carries `device` and,
when a moment earned it, `bind`; new types are gated with
`remote.executor_supports("fx:<type>")`; a verb before its sidecar is warm
calls `sidecars.ensure(…, priority="interactive")`, says "measuring (≈N s)"
and never waits. `critique_short` is added to `backend/routes/mcp.py`
`_READ_ONLY_EXACT`.

---

## 5. Delivery (4E)

### 5.1 Aspects

`aspects.solve(edl, ratio, index) -> (edl, notes)` re-solves every
intent-stored device for one aspect: `frame.ratio` (16:9, 4:3, 1:1, 4:5,
9:16; `4:3` joins `Frame.ratio`), the crop aim from the face track
(`focus_track` / `follow` are source-space and carry over), picture cards and
panels re-laid out for the aspect, motion graphics re-fit (frame-fraction
params; keep-out re-run), captions re-placed, fx regions kept (frame
fractions) and anchors (face, glyph, card) re-solved at render. Nothing is
scaled from another aspect's pixels. `aspects.verify(render, edl)` checks
face, watermark and safe-area violations on the rendered frames.

### 5.2 Languages

`retypeset_lang.retypeset(edl, index, lang, translation=None, srt=None)`:
captions from a user SRT (timing kept) or from a user translation aligned to
the source phrases (each phrase's window kept, words distributed by
syllable weight); motion-graphic texts swapped by the user's mapping; every
layout re-fit (keep-out, lockup widths). Never machine-translates.

### 5.3 Format packs and trend grammars

`formats/<code>.json` (codes E, F, G, I, J in Wave 4; Wave 2 owns A–D, H):
`{code, name, genre, intensity, budgets {family: per_short | {every_s}},
look, captions, type_voice, structure [sections], devices_prefer [card ids],
devices_avoid, never [rules], aspect, notes}`. A pack never names music, a
zoom or a sound; `budgets` feed `taste.ledger` through
`format_packs.budgets(code)`.

`formats/trends/<id>.json`: a structural grammar (`sections` relative to the
user track's drop, in beats; `requires` {gap_before_drop, bpm_range}; the
device plan per section). `format_packs.recognise(rhythm_map)` matches only
the USER's supplied audio, structurally; no reference audio or fingerprint
is stored.

---

## 6. The critic (4F)

`motion_critic.critique(render_path, edl, index, *, request_text=None,
fps=None, previous=None) -> {score, findings, measures, board}` — browserless
analysis of a rendered short (ffmpeg decode + numpy; YuNet faces when
available):

| Lens | Measures | Finding codes |
|---|---|---|
| rhythm | shot lengths (render scene cuts ∪ EDL cuts), change cadence per 2 s, dead stretches, cut-lead vs words (cut inside a word) and vs the user's beats | `dead_stretch`, `cut_mid_word`, `cadence_monotone`, `cut_lead_off_beat` |
| type scale | type height ladder from the render report MEASURES / motion items: hook and payoff largest, captions ≥ 2.5 % of H, ladder ≥ 2:1 | `payoff_not_largest`, `type_too_small`, `flat_ladder` |
| restraint | ledger over budget, device repeats, graphics coverage > 55 %, zoom/sfx count, one punctuation family per section | `over_budget`, `graphics_wall`, `repeat_device`, `owner_rule` |
| colour | saturation over time: B&W share of the program | `bw_as_look` (B&W over > 60 % of the program without a user request) |
| brand | end card present and unchanged (md5 against the brand clip), corner mark present on free tier | `brand_endcard`, `brand_watermark` |
| flash | `photosens.analyse` on rendered frames: Harding-style general and red flashes per second | `flash_rate` (with the safe-mode fix) |
| faces | ink over a face core on rendered frames (YuNet / Haar), graphic over the chin band | `type_on_face` |
| motion | easing smoothness from glyph-box velocity curves (jerk), temporal artefacts (repeated frames in slowed windows, interpolation warping) | `jerky_entrance`, `repeated_frames`, `interp_warp` |
| range | devices used vs the project's previous shorts (`previous`), story moments with no device | `house_style`, `untapped_moment` (≤ 3 suggestions, each bound to a moment, each a fresh device) |

Every finding: `{code, severity (blocking|fix|advisory), t, id, message,
fix, op?}` — `fix` is a concrete instruction, `op` the exact apply_edit_batch
operation when one exists. `photosens.safe_mode(events, downbeats)` keeps the
downbeat hits and thins the rest to ≤ 3 per second.

---

## 7. Interim paths (until the other waves merge)

| Needs | From | Interim |
|---|---|---|
| depth order of objects | 3B | `depth.small` per-pixel depth + the person matte |
| embeddings (SigLIP cheese penalty, recall) | 2E | metadata + pixel scorer; the SigLIP term reads 0 and the report says so |
| video slots in layouts | 3E | `ui_media` compiles its own slot composite |
| typesetter re-fit for languages / aspects | 2A | the template's own `MG.fit` + keep-out re-run |
| format packs A–D, H | 2F | — (Wave 4 ships E, F, G, I, J) |

---

## 8. Shared-file discipline

### 8.1 Surgical edits (the complete list lives in the progress file)

Only these shared files are touched, each at one place: `schemas.py`
(`Frame.ratio` gains `4:3`), `sidecars.py` / `analysis_jobs.py` (the
`depth.small` kind spec and child runner), `devices/__init__.py`
(`CARD_PREFIXES`, `SIDECAR_FEATURE`, lane table rows), `tools_ext/__init__.py`
(`DEPARTMENTS` gains `delivery`), `taste.py` (format-pack budgets fallback),
`config.py` (the W4 block), `backend/routes/mcp.py` (`_READ_ONLY_EXACT`),
`stock.py` (keyless public-domain providers), `models.lock`, the seam tests
(§8.2) and `backend/tests/test_backend_motion_edl.py` (Wave 4 types loaded by
path).

### 8.2 Later-wave pins

The Wave 0/1 seam tests pin "nothing outside the Wave 1 table". A later wave
declares its growth in its own file, `tests/fixtures/waves/w<N>.json`
(`fx_tags`, `verbs`, `skills`, `departments`, `card_prefixes`,
`sidecar_kinds`), and the seam tests set exactly that growth aside. Each
wave drops its own file, so parallel waves never edit the same pin.
