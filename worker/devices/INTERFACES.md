# Wave 0 interfaces: the contracts four tracks build on in parallel

Round lead's interface pass (ROADMAP §3 rule A8). It covers Wave 0 (tracks
0A–0D) and the seams Wave 1 (1A–1F) will plug into.

- **Base commit.** The skeleton commit on `claude/w0-base`. It contains every
  registry, loader and stub named here, and nothing that renders.
- **Status.** Binding. Only the round lead edits this file.
- **Changing a contract.** A track that needs a change asks in its report and
  codes against the pinned version until the round lead re-pins it.
- **Precedence.** ROADMAP.md (`/Users/masaoodi/Documents/Valmera/.valmera/refbench/ROADMAP.md`)
  wins on intent. This file wins on names, shapes and file ownership.
- **Wave 1.** The Wave 1 contracts are in `INTERFACES_W1.md`, which extends
  this file and re-pins its §9 pre-pin.

---

## 0. Rules every track inherits

### Owner rules (hard; enforced in code by the end of Wave 0)

1. Agents never choose music. Music comes only from a user upload, a song
   the user named, or a library track the user picked.
2. Sound effects come only from the owner-approved library, and only where
   earned. Web-SFX is off and out of the live catalog. A missing sound
   becomes a `sound_request` row.
3. Zooms and sounds are optional, never defaults. No card, format pack,
   planner default or `apply_look` may add a zoom or a sound unless the user
   asked.
4. The Valmera end card and the free-tier watermark stay exactly as they
   are. Every creative stage compiles **before** the aspect bars,
   screen-frame plate, watermark and end card.

### Decisions taken as the roadmap's recommended defaults

- **Flash safety is on.** Automatic proposals allow at most 3 full-frame
  flashes per second. An opt-in export is flagged `contains_flashing`.
- **Web-SFX** is off and out of the catalog.
- **Generative lane:** none.
- **Fonts:** OFL or Apache only.
- **No new paid production capacity.**
  - The `ValmeraVision` lane is built as code but stays **dark** (§2.6).
  - Sidecar jobs run on the existing **batch** lane (standard-4, which
    already has RVM, u2net and full Chromium).
- **Models** are downloaded only from the official sources in ROADMAP §7,
  with the licence verified for commercial use.
  - Each is pinned by sha256 in `models.lock`.
  - Production images bake them at build time, as `Dockerfile.cloudflare`
    already does for RVM.

### Inertness

- Every new EDL field defaults to empty, so old EDLs validate unchanged.
  - Top level: an empty list.
  - Nested: `None`. A nested `[]` default **changes** `edl_signature`; use
    `None`.
- EDL signatures and cached-render stamps stay untouched.
- Bump a `config.py` version stamp only for a rendering change that must
  reach cached renders.
- The frontend's TypeScript mirror of the EDL (`src/types/edl.ts`, in the
  frontend repo) is synced at the wave's release integration, not by tracks.
- The corpus test `tests/test_w0_seams.py` with
  `tests/fixtures/w0_inert_corpus.json` must keep passing.
  - Never regenerate that fixture to make a change pass.
  - If a field you add appears in a stored-EDL dump, extend
    `_without_inert` in that test. Do not edit the golden data.
  - Exact dump pins also live in `tests/test_units.py` (the transition dict)
    and `backend/tests/fixtures/editor-command-conformance.json`.
  - Adding only keys whose value is the empty default is the one allowed
    change to those pins.

### The compact tool catalog

- **Budget:** 83,000 bytes, measured over **core** tools only
  (`tools_ext.core_names`).
  - At this commit it is **82,941 bytes**: 59 bytes of headroom.
  - Never raise the budget. Tighten existing compact prose instead.
- **New verbs** are department tools (§7).

### Lanes

- Production agent, MCP and Shorts lanes are standard-1, with no browser and
  no models. Every write path must work there.
  - Layout is measured in Python.
  - Sidecar reads are status checks.
  - Nothing waits on analysis.
- Renders never run models.

### Tests

- **Running them.**
  - Worker: from `<worktree>/worker`, run
    `PYTHONPATH=.. /private/tmp/claude-501/-Users-masaoodi-Documents-Valmera/5651f458-cc98-4830-97e9-8c6503292143/scratchpad/t.sh -q -p no:cacheprovider tests/`.
  - Backend: from `<worktree>/backend`, run the command in your brief.
  - Also run `worker/skill_validator.py` and the plugin `test_run.py`.
  - The baseline at the base commit is 3,671 passed and 5 skipped on the
    worker (3,632 before the seams); 690 passed and 7 skipped on the
    backend.
- **CI environment.** CI runs on Linux with the static ffmpeg 8.1, Noto fonts
  and Pillow with raqm. Locally you have ffmpeg 8.1/9.0 and Pillow without
  raqm.
  - No platform-specific pixel or font-metric margins.
  - Pin behaviour, not ffmpeg-version byte streams.
- **Naming.** Put your tests in your own files: `tests/test_w0a_*.py`,
  `test_w0b_*`, `test_w0c_*`, `test_w0d_*`.
- **Local footage.** A test that needs it uses `tests/localfix.py`
  (`@local_only(fixture(...))`) and skips in CI.

### Data hygiene

- **References** (`~/.openclaw/workspace/references`) are internal
  comparison inputs only.
  - Never upload, publish or commit them.
  - Never put them in product storage.
- **Durable fixtures** go to `/Users/masaoodi/Documents/Valmera/.valmera/fixtures`.
  Never commit them to git.
- **Scratch** goes to `/private/tmp/claude-501/-Users-masaoodi-Documents-Valmera/3950ff90-9877-4a92-8ad5-9b6b2ec520c6/scratchpad/w0-<track>/`.

### Git

- Commit with
  `git -c user.name=ABO3SKRALMASAOODI -c user.email=shmarymuslim@gmail.com commit`.
- End each message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never push, pull, rebase or stash.
- Never touch `/Users/masaoodi/Documents/Valmera/hustler-server` or another
  track's worktree.

---

## 1. File ownership (Wave 0)

Only the owner edits a file. A **one-time edit** is the exact change listed
here, made once. Anything else in a shared file needs the round lead.

### Frozen after the base commit

These files are owned by the round lead:

- `worker/schemas.py`. Members arrive by file drop; see §4.
- `worker/edit_batch.py`
- `worker/http_server.py`
- `worker/version.py`
- `worker/playback_plan.py`
- `worker/skill_validator.py`
- `worker/fx_schemas/__init__.py`
- `worker/jobs_ext/__init__.py`
- `worker/devices/INTERFACES.md`
- `worker/tests/test_w0_seams.py`
- `worker/tests/fixtures/w0_inert_corpus.json`
- `worker/tests/localfix.py`

`worker/tools_ext/__init__.py` is frozen too, except that **0C** may tighten
budgets or error text with the round lead's sign-off.

### 0A: sidecar store, Vision lane, clip persistence

**Owns:**

- `worker/sidecars.py`: replace the STUB bodies and keep every name and
  signature.
- `worker/analysis_jobs.py`.
- `worker/jobs_ext/sidecar.py`.
- `worker/clip_store.py`: replace the STUB bodies.
- `worker/Dockerfile.vision`.
- `worker/models/*.json` and `worker/models.lock`.
- `worker/models/bake.py`, the baker.
  - It is **not** under `tools/`: `worker/.dockerignore` excludes `tools/`
    and `tests/` from the image context.
- `worker/requirements.d/*.txt`.
- `worker/cloudflare/vision.jsonc`, the dark overlay (§2.6).
- `tests/test_w0a_*`.

**One-time edits:**

- `remote.py`
  - Add `sidecar_available()` and `run_sidecar_remote(project_id, payload, user_id=None)`.
    These follow the `matte_available` / `run_matte_remote` shape.
  - In `_cloudflare_lane`, route `"sidecar"` to `"vision"` only when
    `config.VISION_LANE_ENABLED`. Otherwise it stays on the batch default.
- `config.py`: one block placed directly after `CLOUDFLARE_SYNCHRONOUS_TYPES`.
  - Append `sidecar` to the **default strings** of both
    `CLOUDFLARE_EXECUTOR_TYPES` and `CLOUDFLARE_SYNCHRONOUS_TYPES`.
  - Add `VISION_LANE_ENABLED` (env, default `"0"`).
  - Add the `sidecar` entries in the timeout tables.
- `cloudflare/src/index.ts`
  - Add `"sidecar"` to `BATCH_TYPES`, `SYNCHRONOUS_TYPES` and both env type
    lists.
  - Add a `SIDECAR_SHARDS` cap, shaped like `FACES_SHARDS`, so analysis never
    crowds index or final off the 8 batch shards.
  - Add an optional `VISION?: DurableObjectNamespace` binding and an
    exported `ValmeraVision` class that is unused while dark.
  - Run `npm run check`.
- `Dockerfile.cloudflare` and `Dockerfile.agent-base`: one baker step (A8)
  that reads `models/*.json` and `requirements.d/*.txt`.
  - YuNet (0.23 MB) is baked into every image.
  - Nothing else is added to agent-base.
- `.github/workflows/deploy-cloudflare-executor.yml`: merge `vision.jsonc`
  into `wrangler.jsonc` **only** when repository variable
  `VALMERA_VISION_LANE == '1'`.
  - Nothing else changes: same probe list and verify set.
  - An optional separate `workflow_dispatch`-only workflow may build
    `Dockerfile.vision` without deploying it.
- `artifact-cleanup-policy.json`: 90-day last-use rules for the `sidecars/`
  and `motion-clips/` prefixes.
- `indexer.py`: one call at the end of a successful index,
  `sidecars.ensure("faces.yunet", …, priority="prefetch")`.
- `shorts.py`: one call after ranking that prefetches `matte.person` for the
  kept ranges of the top shorts.
- `personseg.py`: you may **add** functions. Do not edit existing ones; 1A
  owns this file from Wave 1.
- `keepout.py`: one new function plus one call site that read
  `faces.yunet`, behind `config.YUNET_KEEPOUT`.
  - The flag defaults to off, so renders are unchanged.
  - It is flipped only after AT-0A-4, by the round lead.

**Do not edit:**

- `matte.py` and `motion_layer.py` (1A, Wave 1).
- `wrangler.jsonc`.
- The deploy probe list.

### 0B: FX graph seams

**Owns:**

- `worker/fxgraph.py` and `worker/fxprims.py`.
- `worker/fx_schemas/core.py`: engine-only probe types, `AUTHORABLE = False`
  (§4.1).
- `worker/rhythm_types.py` and `worker/prosody_grid.py`.
- `worker/motion/lib/*.js`.
- `tests/test_w0b_*`.

**One-time edits:**

- `renderer.py`
  - The hook call sites at the landmarks in §4.4.
  - A `fx_plan=None` keyword on `build_filtergraph`, threaded from
    `render_edl`.
  - Call `schemas.strip_unknown_fx` on the render path only.
  - Attach the `devices` report block (§6.3) to the render result and asset
    meta.
- `stitch.py`: fx items become video-local windows, or force a full render.
  Until this edit, a non-empty `fx` already forces a full render.
- `timeline.py`: an `fx` branch in `remap_program_items`.
  - Picture-stage items are **content-anchored**, like zooms and stylize.
  - `junction` items remap `at` through the source.
  - `music_bus` items follow their music item.
  - Return disclosure notes.
- `motion_engine.py`
  - Concatenate `motion/lib/*.js` into the runtime.
  - Plumb `params._beats`.
  - Wire `clip_store.get` / `clip_store.put` into `_cache_get` /
    `_cache_put`.
  - Consult `faultinject.fault("chromium_crash")`.

**AT-0B-4** (the ffmpeg filter set) is a CI test against the static 8.1 that
CI installs. It is not a Dockerfile edit.

**Do not edit:**

- `schemas.py`. A member is a new file in `fx_schemas/`.
- `matte.py`.

### 0C: device contract, restraint as code, fault injection

**Owns:**

- `worker/devices/` (everything except `INTERFACES.md`): `__init__.py`,
  `schema.py`, `compiler.py` and `cards/`.
- `worker/cost_model.json`.
- `worker/faultinject.py`: extend it and keep the names in §6.2.
- `worker/music_gate.py`, `worker/sound_requests.py`,
  `worker/flash_safety.py` and `worker/hooks.py`.
- The existing `taste.py` (ledger and budget governor) and
  `quality_verifier.py` (the `DEGRADED` gate, `contains_flashing`).
- `tests/test_w0c_*`.

**One-time, function-level edits in `agent_tools.py`:**

- `add_music`
- `apply_look`: the `music=` path only.
- `find_song`
- `fetch_url`: stamp `user_named` where it places music.
- `ask_user`: issue a `user_pick` token.
- `_tool_disabled`: the web-SFX branch only.

**Other one-time edits:**

- `music_library.add_library_music`.
- `sfx_search.available()`: AND with `config.WEB_SFX_ENABLED`.
- `config.py`: one block directly after `SFX_FETCH_MAX_MB`, containing
  `WEB_SFX_ENABLED` (default `"0"`) and `FLASH_SAFE_MAX_PER_S = 3`.
- Remove web-SFX mentions and fix music-gate wording only. Wave 0 has no
  agent-layer track, so this one exception goes to 0C:
  - `agent_prompt.py`
  - `skills/audio.md` and `skills/generate-fetch.md`
  - `plugins/valmera/skills/valmera-podcast-shorts/references/{editing,looks}.md`
- Pin `RESTRAINT_RULE` and `SOUND_RULE` with tests. Do not reword them.

**Budget timing.** Turning web-SFX off frees **1,237 bytes** of the core
compact catalog (measured). Land it no later than the first department
registration (§7).

### 0D: refbench, metrics, reference signatures, footage library

**Owns:**

- `worker/benchmarks/refbench/`: the spec schema, runner, boards, judge
  prompts, verdict writer and label import (`labels.py`).
- `HC_SHOTLIST.md` and a dev-only `requirements.txt`.
  - RapidOCR is installed locally only. It is **not** in
    `worker/requirements.txt` in Wave 0.
- `worker/qa_metrics.py` and `worker/refsig.py`.
- `worker/benchmarks/footage/vfl.json`.
- `.github/workflows/refbench-nightly.yml`.
  - It runs the CI-safe subset only, on synthetic clips.
  - It never deploys, publishes or touches footage.
- `tests/test_w0d_*`.

**One-time edits:** none in runtime code.

---

## 2. Sidecars (0A)

### 2.1 Kinds

- Wave 0 ships `matte.person` (RVM; u2net fallback) and `faces.yunet`.
- These names are **reserved**. Use them, never a synonym:
  - Rhythm: `rhythm.music`, `rhythm.speech`.
  - Embeddings: `embed.siglip2`, `embed.dinov2`.
  - Anchors: `landmarks.face`, `landmarks.hand`, `landmarks.pose`,
    `track.points`, `camera.motion`.
  - World: `objects.sam2`, `depth.small`, `planes.h`, `plate.clean`.
  - Text: `ocr.text`.
- A kind's `model_id` and `model_version` live with its runner. They come
  from `models/<model>.json`, never from a call site.

### 2.2 Key, clock and chunks

These are implemented and pinned in `sidecars.py`.

**Key.**

```
sidecar_key(kind, source_sha, model_id, model_version, params, (t0, t1))
  = sha256(json([source_sha, kind, model_id, str(model_version),
                 canonical_params(params), [ms(t0), ms(t1)]]))
```

- `canonical_params` is sorted-key compact JSON.
- Times are folded to whole milliseconds.

**Clock.** Sidecars are measured on the **source** clock of the original
upload. `source_sha` is that upload's content sha256 (`assets.sha256`, or
render meta `src_sha256`). The job reads the proxy. Nothing is measured on
program time.

**Chunks.**

- `chunk_ranges(shots, duration)` splits every index shot `[a, b)` into
  `ceil((b-a)/15)` **equal** chunks.
- A request maps onto the chunks it overlaps (`chunks_for`).
- The same footage therefore always hits the same keys, whoever asks.
- RVM state resets at chunk starts. A 1-second warm-up overlap is computed
  inside the job and discarded.

### 2.3 API

The signatures are frozen. The bodies are 0A's.

| Call | Returns | Notes |
|---|---|---|
| `SourceRef(sha256, storage_key, duration_s, fps, width, height, project_id=None, shots=())` | — | `shots` = index shot starts (source s) |
| `ensure(kind, source, ranges, priority="interactive", params=None)` | `Handle` | Returns at once. Launches missing chunks. Priorities: `interactive` > `prefetch` > `backfill` |
| `status(handle)` | `ready\|pending\|failed\|absent` | Aggregate. `ready` only when every chunk is ready |
| `chunk_status(handle)` | `{key: status}` | Per chunk: a degrade affects only its window |
| `wait(handles, budget_s)` | `{key: status}` | Previews pass `0`; finals at most `90` |
| `fetch(handle, dest_dir)` | `[{key, t0, t1, path, meta}]` | Ready chunks only, ordered by `t0`. A missing chunk is **black (inert)** to every consumer |
| `estimate_s(handle)` | float | Feeds the write's "measuring (≈N s)" note |
| `summary(source_sha)` | `{kind: {ready, pending, failed}}` | Feeds `project_state.sidecars`. It must stay importable without the worker tree if the backend calls it; otherwise defer that key to 1F |

### 2.4 Storage layout (R2, product bucket)

```
sidecars/<sha[:2]>/<source_sha>/<kind>/<key>.<ext>       data (mask chunks: gray H.264 CRF 20 .mp4; tracks: .json)
sidecars/<sha[:2]>/<source_sha>/<kind>/<key>.json        manifest, written LAST: presence == ready
sidecars/<sha[:2]>/<source_sha>/<kind>/<key>.pending.json {lease_until, attempt, lane, started_at}
sidecars/<sha[:2]>/<source_sha>/<kind>/<key>.failed.json  {reason, attempts, at}
motion-clips/<key[:2]>/<key>.mov (+ .json)               clip_store (motion_engine cache key)
```

**Manifest fields:**

- `key`, `kind`, `model_id`, `model_version`, `params`.
- `src_range`: `[t0, t1]`.
- `fps`, `frames`, `width`, `height`, `format`, `bytes`, `created_at`.
- `qa`: `{flicker, edge_chatter, coverage: [..]}`.

**Version handshake.** A manifest whose model version or format the reader
does not know reads as `absent`, never as `ready`. Cache poisoning is
impossible by construction.

### 2.5 Job kind

- There is **one** job kind for every sidecar: `"sidecar"`, registered in
  `jobs_ext/sidecar.py`.
- The payload is `{kind, source: {...SourceRef}, chunks: [{key, t0, t1}], params}`.
- It is synchronous, like `matte`: no `video_jobs` row.
- Its home lane is `batch`. A future sidecar kind never adds a job type.

### 2.6 The dark Vision lane

- **Code that exists:** `Dockerfile.vision`, `cloudflare/vision.jsonc` (the
  container entry, the DO binding `VISION` and a `v3` migration for
  `ValmeraVision`), the exported class in `index.ts`, and the routing branch
  behind `config.VISION_LANE_ENABLED`.
- **Not deployed:** `wrangler.jsonc` is untouched, and the deploy workflow
  merges the overlay only when `VALMERA_VISION_LANE == '1'`. The owner has
  not approved that.
- **Fallback while dark:** batch, then the local photometric matte (static
  camera), then the device substitute.

### 2.7 clip_store (0A stores, 0B wires)

- `get(key, dest_dir) -> (mov_path, meta) | None`
- `put(key, mov_path, meta) -> bool`

Both are best effort and never raise. A cold container with a warm store
renders a 6-item preview with zero Chromium renders (AT-0A-2).

---

## 3. Job-kind registry (`jobs_ext`, implemented)

- A module `jobs_ext/<name>.py` (not starting with `_`) exports
  `JOB_KINDS = [JobKind(name, runner="module:function", lane="batch", synchronous=True, timeout_s=900, features=())]`.
- `lane` is one of `batch`, `interactive` or `vision` (dark; falls back to
  batch).
- `http_server` calls `jobs_ext.merge_into(COMPUTE_RUNNERS)` once.
  - Compute roles only: agent, MCP and Shorts keep their single job family.
  - Runners import lazily on first call.
  - A kind that shadows a core runner fails at import.
- **Routing is not automatic.** The owning track lists the kind once in each
  of these: `config.CLOUDFLARE_EXECUTOR_TYPES` and `CLOUDFLARE_SYNCHRONOUS_TYPES`
  defaults, `remote._cloudflare_lane` if it is not batch, and the
  `index.ts` type sets and env lists.

---

## 4. EDL seams (implemented in `schemas.py`; members by file drop)

### 4.1 `EDL.fx`: a discriminated union on `type`

- **Field.** `EDL.fx: List[FxItem] = []`. The top-level empty list drops out
  of `edl_signature`.
- **Members.** Every `fx_schemas/<name>.py` that does not start with `_`
  exports `FX_MEMBERS = (Cls, ...)`.
  - Each class subclasses `fx_schemas.FxItemBase`, imported **relatively**
    (`from . import FxItemBase`).
  - Each sets a unique `type: Literal["<tag>"] = "<tag>"`.
  - The backend loads `schemas.py` by path as `worker_schemas`, without the
    worker tree. So `fx_schemas` modules may import **only** pydantic, the
    standard library and their own package. Never import `config`,
    `renderer`, `matte` and so on.
- **The loader** (`schemas._load_fx_registry`) fails at import on any of
  these:
  - a duplicate tag;
  - a non-subclass;
  - `STAGES` outside `FX_STAGES`;
  - an unknown slot;
  - a slot with two owners.
- **Derived constants.**
  - `schemas.FX_TYPES`: every tag.
  - `schemas.FX_AUTHORABLE_TYPES`: tags with `AUTHORABLE = True`.
  - `schemas.FX_STAGES`.
- **Tags are namespaced by owning track.**

  | Track | Tags | Module |
  |---|---|---|
  | 0B | `fx_probe` (`AUTHORABLE = False`) | `core.py` |
  | 1B | `subject`, `background` | `subject.py` |
  | 1C | `region`, `type_window` | `composite.py` |
  | 1D | `accent`, `strobe`, `junction` | `accents.py` |
  | 1E | `grade_window`, `finish` | `finish.py` |

  Later waves claim their tags here first.

**`FxItemBase` fields:**

| Field | Type | Meaning |
|---|---|---|
| `id` | str 1–80 | unique within `fx` |
| `type` | Literal tag | discriminator |
| `stage` | one of `world, screen, pre_captions, over_type, junction, music_bus` | must be in the class's `STAGES` |
| `start`, `end` | program seconds | required for picture stages. Rounded to 0.01; `end` exclusive; min 0.01 s; within program length |
| `at` | program seconds | `junction` items: the cut they decorate |
| `music_id` | str | `music_bus` items: an existing `MusicItem.id` |
| `bind` | `{on, ref, rhythm, offset_f}` | **intent** (A4). `on` ∈ `time, word, syllable, stress, phrase_end, beat, downbeat, onset, cut, section, drop, gap`. `ref` is a word index, a RhythmMap list index, a cut's source second, or a program second. `rhythm` is `"speech"` or a `MusicItem.id`. `offset_f` is whole frames (−240..240) |
| `device` | str | the device card id that wrote it (ledger, budgets, report) |
| `seed` | int ≥ 0 | determinism for seeded devices |
| `purpose` | str ≤ 300 | |

**Class hooks and flags:**

- `STAGES: ClassVar[tuple]`.
- `AUTHORABLE: ClassVar[bool] = True`. Engine and test types set `False`:
  they render, `edit_batch` refuses to author them, and they are never
  advertised.
- `check(self, ctx)`: raise `ValueError` for EDL-level problems. `ctx` is an
  `FxCheckContext` with `.edl`, `.program_s`, `.source_s`, `.canvas` and
  `.render_fragment`.
- `describe(self)`: the one-line diff text.

**Generic checks** run in `validate_edl`: id uniqueness, stage membership,
window and `at` bounds, `music_id` existence, then the member's `check`.
Items are sorted by (stage order, `start` or `at`, `id`).

**Frames.** Renderers convert with `frame = round(t * fps)` and treat `end` as
exclusive. Centisecond times identify frames exactly up to 100 fps. A
1-frame device writes `end = round((frame + 1) / fps, 2)`.

**Re-solving.**

- `start`, `end` and `at` are the **solved** values.
- On a keep change, `timeline.remap_program_items` (0B) moves them.
- On a song swap or an aspect change, the 0C hooks re-solve them from `bind`
  (§6.4).

**Version skew** (fault `executor_skew`):

- The render path calls `schemas.strip_unknown_fx(edl)`, which returns
  `(edl, dropped_ids)`. It drops types this lane does not know, and reports
  them as rung `omit`.
- Authoring still rejects unknown types.
- `/health` advertises `features: ["fx:<type>", ...]`. A write tool gates a
  new type with `remote.executor_supports("fx:<type>")`. `None` (unknown)
  means proceed.

### 4.2 Typed slots on existing models

Each slot is `Optional[...] = None`. Until its owner registers
`FIELD_TYPES = {"<slot>": annotation}` from an `fx_schemas` module, it
accepts **only** `None`.

| Slot | Owner | Intended shape |
|---|---|---|
| `MotionItem.composite` | 1C (`fx_schemas/composite.py`) | `{blend, fill, ghost, stage, region}` |
| `MotionItem.anchor` | Wave 3 (3A/3C) | attachment to a tracked point or plane |
| `TransitionSpec.per_junction` | 1D (`fx_schemas/accents.py`) | `List[{at, style, origin, duration_s}]` (`set_junction`) |
| `MusicItem.automation` | Wave 2 (2C/2D) | bus automation bound to the RhythmMap |

### 4.3 `MusicItem.provenance` (typed now; enforced by 0C)

- Values: `user_upload`, `user_named`, `user_picked_library` or `legacy`.
- `None` reads as `legacy`. It still renders, and **no migration rewrites
  stored EDLs**, because that would change signatures and bust caches.
- **0C's music gate** requires a non-legacy provenance on every **new** music
  item.
  - It is enforced in `add_music`, `add_library_music`, `apply_look(music=)`,
    `swap_music` and `edit_batch` writes to the `music` layer that add an
    item.
  - Gain, fit or mute edits to an existing item keep that item's
    provenance.
- `add_library_music` needs a `user_pick` token that only `ask_user`
  returns.

### 4.4 Stage landmarks in `renderer.build_filtergraph` (0B)

- **API.** Each hook is `vlabel = fxgraph.apply_stage(stage, parts, vlabel, fx_plan)`.
- **No items, no filters.** With no items at a stage the call appends
  **nothing** and returns `vlabel` unchanged. This is the md5-identity
  guarantee (AT-0B-1).
- **Layout tags.** Where `card_runs` is set, a stage that rebuilds frames
  wraps itself in `_keep_layout_tags`, as stylize does.

| Stage | Insert exactly at | Behaviour |
|---|---|---|
| `world` | after the custom-chain loop (`for ci, cf in enumerate(custom_filters)`), before `# ---- words BEHIND the subject (round 60)` | rides zooms |
| `screen` | after the camera and screen takeovers, before `# ---- overlays (round 35)` | screen-locked |
| `pre_captions` | after `motion_layer.append_graph(..., "below_captions", fps)`, before the `if ass_path:` caption burn | |
| `over_type` | after `motion_layer.append_graph(..., "above_captions", fps)`, before `# ---- mid-video aspect change (round 51)` | below the bars, plate, watermark and end card |
| `junction` | at each block-pair junction, where `timeline.transition_junctions` is resolved | gets source-handle streams |
| `music_bus` | per user music item, after its own fit and fades, before the music mix (`amix`) | `alabel = fxgraph.apply_music_bus(parts, alabel, music_id, fx_plan)` |

**fxgraph API.** The names are pinned; 0B writes the bodies.

- `fxgraph.prepare(edl, *, tl, W, H, fps, preview, workdir, source=None) -> FxPlan`
  - It resolves sidecars and rungs, calling `devices.resolve`, which is a
    stub until 0C lands.
  - It allocates extra ffmpeg inputs: masks, maps, stills.
  - `FxPlan` has `.inputs`, `.by_stage` and `.notes`. `.notes` feeds the
    `devices` report.
- `fxgraph.apply_stage(stage, parts, vlabel, plan) -> vlabel`.
- `fxgraph.apply_junction(parts, a_label, b_label, junction, plan) -> label | None`.
  `None` means the existing transition path handles it.
- `fxgraph.apply_music_bus(parts, alabel, music_id, plan) -> alabel`.
- `fxgraph.mask(plan, label, space=None) -> pad name | None`. Consumers never
  name mask pads themselves.
- `fxgraph.program_matte(plan, space="world") -> pad name | None`.
  - It is a **stub** that returns `None` until 1A replaces it.
  - 1B, 1C and 1D treat `None` as "matte absent", which means the substitute
    rung.
- **Primitives** (`fxprims.py`):
  - `window()` uses `enable` on **frame index `n`**, never `t`.
  - `blend()` is bbox-local `gbrp` with `all_mode` + `maskedmerge`.
  - `track_matte()`, `region_fx()`.
  - `mask_upsample()` is `guided`, finals only.
  - Wipes and materials use precomputed gray mask clips or maps; never
    per-pixel expressions (ROADMAP §2 benchmarks).

### 4.5 Mask labels

- **Format.** A program mask is a gray8 stream at program W×H and fps, for
  the full program length. 255 means inside. **Black is inert**: an absent
  or degraded mask is black, plus a note.
- **Grammar.** `<label>[@world|@screen]`. The default space is the
  requesting stage's own: `world` for the `world` stage, `screen` for every
  later stage.
- **Inversion and morphology** (dilate, erode, feather, edge band) are
  consumer parameters, never labels.

| Label | Source | Track |
|---|---|---|
| `person` | `matte.person` (all people), through the picture's own geometry chain | 1A (stub `None` until then) |
| `face` | `faces.yunet` boxes as soft ellipses | 0A sidecar, 1A/1C consumers |
| `glyph:<motion_id>` | the alpha of that motion item's clip | 1C |
| `region:<fx_id>` | the rect, ellipse or keyframed polygon in that fx item | 1C |
| `hue:<rrggbb>` | hue key (`hue_key()`) | 1E |
| `object:<id>`, `plane:<id>`, `depth:near\|far` | Wave 3/4 sidecars | 3B, 3C, 4A |

### 4.6 RhythmMap (0B owns `rhythm_types.py`; the shape is pinned here)

```python
RhythmMap = {
  "version": 1,
  "source": "music" | "speech",
  "clock": "media" | "source",   # music: seconds into the audio asset; speech: SOURCE seconds
  "ref": {"storage_key": str | None, "sha256": str | None, "music_id": str | None},
  "producer": "perception.beat_grid@<v>" | "prosody_grid@<v>" | "<model>@<v>",
  "bpm": float | None,
  "beats": [t, ...], "downbeats": [t, ...],
  "onsets": [{"t": float, "cls": str, "strength": 0..1}, ...],
  "sections": [{"t0": float, "t1": float, "label": str, "energy": 0..1}, ...],
  "drops": [t, ...],
  "gaps": [{"t0": float, "t1": float}, ...],
  "confidence": 0..1,
}
```

**Onset classes:**

- Music: `kick`, `snare`, `hat`, `perc`, `transient`.
- Speech: `word`, `stress`, `syllable`, `phrase_end`, `pause_end`.

**Section labels:**

- Music: `intro`, `verse`, `build`, `drop`, `break`, `outro`.
- Speech: `phrase`.

**Rules:**

- Maps are stored on their own clock and cached as sidecars
  (`rhythm.music`, `rhythm.speech`).
- `rhythm_types.to_program(rmap, timeline, music_item=None)` maps them to
  program time when used. For music it maps through `start` and `offset_s`
  and loops.
- **Producers in Wave 0:** the `perception.py` beat grid and the speech
  prosody grid. The prosody grid takes word onsets, `word_stress` peaks,
  phrase ends and pauses. It is the default clock for a talking head with no
  track.
- **Low confidence** means onsets only. Never invent a grid.
- A map must stay at or under 60 KB.

### 4.7 `edit_batch`

- `fx` is in `LAYERS` and `ITEM_LAYERS`.
- `apply_edit_batch` advertises `fx` in its layer enum only once
  `FX_AUTHORABLE_TYPES` is non-empty. That enum is in the core catalog and
  costs 5 bytes.
- `media_keys` already polices any `asset_key` or `storage_key` inside fx
  items.

---

## 5. Device cards (0C implements; the schema is pinned)

**File.** One card per file, `worker/devices/cards/<id>.yaml`. The filename
stem equals `id`. The track that ships the device owns its card.

**Format.** PyYAML is present today only transitively, through faster-whisper.
If cards load at runtime, 0C pins `PyYAML` explicitly or compiles the cards
to JSON at build time.

```yaml
id: subject_fill_flash          # ^[a-z][a-z0-9_]{2,47}$
family: matte_fx                # punctuation|matte_fx|type_depth|transition|camera|sfx|composite|finish|rhythm|layout|world
verb: add_subject_effect        # the tool that writes it (core or department)
fx_type: subject                # optional: the EDL.fx type it writes
stage: world                    # an FX stage, or motion|texts for devices writing those layers
params: {kind: fill, colour: [white, black, accent], frames: [1, 2]}   # the space a planner may choose from
needs:
  sidecars: [matte.person]      # §2.1 names
  lane_features: ["ffmpeg:maskedmerge"]   # chromium|onnx.rvm|onnx.u2net|onnx.yunet|demucs|whisper|ffmpeg:<filter>
  footage: "person, coverage >= 0.4%"     # human-readable; the check itself is code
binds_to: [beat.kick, beat.downbeat, cut, word.payoff]
budget: {calm: {per_short: 1, min_gap_s: 4}, designed: {per_short: 3, min_gap_s: 2}, charged: {per_short: 5, min_gap_s: 1}}
never: ["on a face-dependent payoff onset", "within 0.5 s of another matte_fx"]
owner_flags: {adds_sound: false, adds_zoom: false, flash: true}   # all three keys required
rungs: [full, {substitute: full_frame_flash, intensity: 0.6}, omit] # ordered best-first; last is omit
cost: {ms_per_frame: {interactive: 4.0, batch: 2.5}, fixed_ms: 300}
exemplars: ["22@≈8.43", "40@≈4.67", "20@9.10"]   # internal reference notation only
verify: ["land_err_f<=1", "fill_iou>=0.95", "flash_risk"]   # qa_metrics names (§8.3)
status: live                    # live|beta|dark
flag: null                      # optional config flag gating a dark/beta card
description: "one line, used as the planner's reason"
skill: subject-fx               # optional skills/<stem>.md
```

**Field order.** The constants in `devices/__init__.py` are `CARD_REQUIRED`,
`CARD_OPTIONAL`, `STATUSES`, `INTENSITIES`, `OWNER_FLAGS`, `RUNGS`,
`FAMILIES`, `STAGES`, `BINDS_TO` and `LANE_FEATURES`. Extend the
enumerations only through the round lead.

**Invariants** (0C tests them over every card):

- `owner_flags.adds_zoom` or `adds_sound` is true: the card is never
  proposed unless the user asked. It is never part of a format-pack or look
  default.
- `flash: true`: instances pass through `flash_safety`.
- `status: dark`: `resolve` returns `omit` with reason `"dark"`, and the
  verb refuses unless `flag` is on.
- `rungs` ends with `omit`. A `substitute` rung names an existing card or a
  built-in substitute.
- Every `needs.sidecars` entry is a pinned kind. Every `verify` metric
  exists in `qa_metrics.METRICS` (0D).

---

## 6. Restraint as code (0C)

### 6.1 Gates

| Gate | Behaviour |
|---|---|
| **Music** | §4.3. With no user track, music tools refuse and offer upload, name or pick through `ask_user`. |
| **Web-SFX** | `config.WEB_SFX_ENABLED` defaults off. `search_sfx`, `audition_sfx_candidates`, `fetch_sfx` and `add_web_sfx` are hidden through `_tool_disabled`. |
| **Missing sound** | A request for a sound the library lacks calls `sound_requests.file(ctx, description, at=None, purpose=None) -> id` and declines in one clause. Prefer R2 JSON or an existing table. A new table needs the next migration number (`031_…`), which **must not be applied** in Wave 0. |
| **Flash safety** | `flash_safety.plan(events, fps, opted_in=False, max_per_s=config.FLASH_SAFE_MAX_PER_S) -> (kept, thinned)`. An opted-in export is flagged `contains_flashing`, computed in `quality_verifier` and the export gate from the EDL. There is no renderer edit. |
| **Zoom and sound** | §5 invariants. `RESTRAINT_RULE` and `SOUND_RULE` are pinned by tests. |

### 6.2 Fault names (`faultinject.py`, implemented)

- **Call.** `fault(name)` returns `bool`. A consult site takes the **same**
  path as the real failure.
- **Activation.** `with inject(*names):` or `VALMERA_FAULTS=a,b`.
- **Detail.** A site may refine a name with `:<detail>`. Activating the base
  name fires every detail.

| Name | Consulted at | By |
|---|---|---|
| `model_missing[:<kind>]` | sidecar runner / personseg model load | 0A |
| `sidecar_timeout` | `sidecars.wait` | 0A |
| `chromium_crash` | `motion_engine` per item | 0B |
| `executor_skew` | `remote` health / feature reads | 0A |
| `oom` | sidecar child and fx child | 0A, 0B |
| `ffmpeg_filter_missing[:<filter>]` | `fxgraph` filter availability | 0B |

AT-0C-3 (every fault mode × every spec gives 0 render failures) becomes the
CI gate that every later wave inherits.

### 6.3 Resolver, ledger and report

- **Resolver.** `devices.resolve(card_id, *, lane, sidecar_status, budget_ms=None) -> (rung, reason)`
  is a stub returning `("omit", ...)` until 0C lands.
  - `devices.lane_features(lane=None) -> set`.
  - `devices.load_cards() -> {id: card}`.
- **Report.** Each render result and asset meta carries
  `devices: [{"id", "device", "family", "stage", "rung", "reason", "est_ms"}]`.
  It comes from `fxgraph` plan notes and is attached by 0B's renderer edit.
  - A final with any rung other than `full` that has not been acknowledged
    is **blocked**: the `DEGRADED` gate in `quality_verifier`.
- **Ledger.** `taste.ledger(edl, intensity="designed", fmt=None)` exposes:
  - `.line()`, for example
    `punctuation 1/2 · matte 1/2 · depth 1/1 · zoom 0 · sfx 0`.
  - `.over_budget() -> [str]`.

  Every write returns that line.

### 6.4 Hooks (`hooks.py`)

- `hooks.register(event, fn)` registers a re-solver.
  - `event` is `"music_swap"` or `"keep_change"`.
  - Later tracks register here without editing `hooks.py`.
- `hooks.on_music_swap(edl, old_music_id, new_music_id) -> (edl, notes)`.
- `hooks.on_keep_change(old_edl, new_edl, old_tl, new_tl) -> (edl, notes)`.
  It runs after `timeline.remap_program_items`.

---

## 7. Department tools (`tools_ext`, implemented)

**Module.** `tools_ext/<name>.py`, not starting with `_`, exports:

- `DEPARTMENT`: one of `compositing`, `rhythm`, `world` or `verify`. A new
  department is a round-lead change.
- `TOOL_SPECS`: a **module-level dict literal** of `{name: (fn, description, properties)}`.
  - `fn(ctx, **args) -> str`, exactly as a core tool.
  - `{**BASE, ...}` merges are allowed.
  - `skill_validator` reads the literal statically: CI validates skills
    before dependencies install.
- Optional exports:
  - `REQUIRED_ARGS`.
  - `WRITE_TOOLS`: a write tool's success text is an `EDL vX -> vY: …`
    line.
  - `COMPACT`: `{name: contract}`, at most 600 characters each. Without it
    the compact description is the first sentence.
  - `DISABLED`: `{name: () -> bool}` honest-off. A raising predicate hides
    the tool.

**Install.** `agent_tools` ends with `tools_ext.install(sys.modules[__name__])`.

- Tools go into `TOOLS`, `TOOL_DOMAINS[DEPARTMENT]`, `REQUIRED_ARGS`,
  `WRITE_TOOLS` and `_COMPACT_CONTRACTS`.
- A collision with a core tool, an unknown department, a tool over
  6,000 bytes or an oversized `COMPACT` entry fails at import.

**Catalog mechanics:**

- **Core budget.** Department tools are **excluded from the core compact
  catalog** (`tools_ext.core_names`) that the 83,000-byte budget measures.
- **In-house agent.** It gets them only after
  `load_tools(domains=["<dept>"])` or `load_tools(names=[...])`.
- **MCP clients** get the full catalog. Keep full descriptions short, because
  MCP pays for them.
- **Department budget.** Each department's compact schemas are capped at
  `DEPARTMENT_COMPACT_BUDGET = 12,000` bytes, tested.
- **Domain enums.** A department appears (directory row, `load_tools` and
  `expand_toolset` enums) only once it has a tool. Its name in the two enums
  **is** core growth:

  | Department | Core cost |
  |---|---|
  | `compositing` | 28 B |
  | `rhythm` | 18 B |
  | `world` | 16 B |
  | `verify` | 18 B |

  Headroom today is 59 B, so the first department registration needs 0C's
  web-SFX removal (−1,237 B) merged first, or text tightened elsewhere.
- **MCP annotations.** Read-only annotations are classified by name
  prefixes in the backend (`get_`, `find_`, `list_`, `search_`, `look_at`).
  Verbs like `suggest_devices`, `proof_device` and `audit_motion` default to
  "write". 1F adds them to `backend/routes/mcp.py` `_READ_ONLY_EXACT`.

---

## 8. Refbench (0D implements; the case and verdict schemas are pinned)

### 8.1 Case spec

The file is `worker/benchmarks/refbench/cases/<track>/<id>.yaml`. Case specs
are small text and go in git.

```yaml
id: AT-1B-1                     # ^(AT-[0-4][A-F]-\d+[a-z]?|AT-[0-4][A-F]-perf|W[0-4]-S\d+|BASE-\d+)$
track: 1B
title: white silhouette flashes on the three strongest stresses
local_only: true                # needs VFL fixtures; skipped in CI
interim: false                  # true when an HC stand-in is used (§4.3 of the roadmap)
reference:                      # internal comparison only
  - {ref: "22", t: [8.43, 12.33], approx: true}
footage:                        # VFL ids/windows from benchmarks/footage/vfl.json
  - {vfl: V12, window: walkon}  # or {vfl: V02, t: [840.47, 880.51]}
music: null                     # or {fixture: HM1} — owner-supplied only; provenance comes from the fixture
aspect: "9:16"
mode: engine                    # engine | agent
edl: {base: vfl_default, ops: []}   # engine: apply_edit_batch ops on the window's default EDL
chat: null                      # agent: the user's sentence, verbatim
render: {variant: preview}      # preview | final
allow: []                       # what the spec ASKS for among: music, sfx, zoom, flash_optin
metrics:
  - {name: land_err_f, op: "<=", value: 1}
  - {name: matte_iou, op: ">=", value: 0.92, labels: V12/walkon/person}
beyond: "the same sentence applies to all 6 shots of a montage in one call"   # required, non-empty
board: {stills: [8.43], clip: [6.0, 10.0]}
judges: {lenses: [motion, craft, owner_rules], pass: 2of3_no_major}
```

### 8.2 Verdict JSON

It is written to `$VALMERA_REFBENCH_OUT`, which defaults to the track's
scratch directory. It never goes into git or product storage.

```json
{"case": "AT-1B-1", "commit": "<sha>", "ts": "<iso>",
 "render": {"path": "...", "sha256": "...", "variant": "preview", "wall_s": 41.2, "lane": "local"},
 "metrics": {"land_err_f": {"value": 0, "bar": "<=1", "pass": true}},
 "standing": {"render_ok": true, "brand_integrity": true, "no_collisions": true,
              "loudness": true, "owner_rules": true, "ledger": true},
 "devices": [],
 "judges": [{"lens": "motion", "verdict": "exceeds", "major_defect": false, "notes": "..."}],
 "pass": true, "board": {"still": "...png", "clip": "...mp4"}}
```

**`judges[].verdict`** is one of `exceeds`, `matches` or `below`.

**Standing checks** (ROADMAP §4.5) are always on. A spec cannot switch
them off.

- `brand_integrity`: the watermark ROI |Δ| = 0, and the end-card frames are
  md5-identical to the render without fx.
- `owner_rules`: no music, sound or zoom unless that item is in `allow`.

### 8.3 Metrics (`qa_metrics.py`)

- **Registry.** `qa_metrics.METRICS = {name: fn}`. Each function returns
  `{"value": float, "pass": bool | None, "detail": {...}}`.
- **Pinned names:**
  - `land_err_f`, `matte_iou`, `matte_flicker`, `edge_chatter`,
    `glyph_visible`, `legibility`, `reproj_rms_px`, `region_locality`,
    `duration_exact`, `flash_risk`, `determinism`, `cost_ratio`.
  - Device-specific names (for example `fill_iou`) are added by the track
    that needs them.
- **`flash_risk`** is the Harding-style frame counter. 0C's
  `flash_safety.plan` is the planning-time thinner. They are separate on
  purpose.

### 8.4 Footage, labels and references

- **`benchmarks/footage/vfl.json`** (in git; the fixture files are not):

  ```json
  {"V02": {"source": "<path under .tmp/valmera-podcast-shorts>", "sha256": "...",
           "spec": {...}, "windows": {"thiel_flying_cars": {"t": [840.47, 880.51],
           "file": "V02/thiel_flying_cars.mp4", "sha256": "...", "labels": ["person"]}}}}
  ```

- **Fixture files** live in `$VALMERA_FIXTURES_DIR`, by default
  `~/Documents/Valmera/.valmera/fixtures`.
- **Labels** go to `<fixtures>/labels/<vfl>/<window>/<label>/`:
  - person masks: PNG per frame;
  - fingertips and plane corners: JSON;
  - syllable onsets: JSON.
- **References** are read from `$VALMERA_REFERENCES_DIR`
  (`tests/localfix.py`). They are internal only.

---

## 9. Wave 1 tracks: where each plugs in

Wave 1 gets its own interface pass. This table is the pre-pin.

| Track | New files | fx tags, slots and departments | Notes |
|---|---|---|---|
| 1A | `matte_track.py` | — | Owns `matte.py`, `personseg.py`, the mask plumbing in `renderer.py`, the real `fxgraph.program_matte`, `agent_tools._measure_subject_matte`, and `motion_layer.behind_why` / `hero_front` |
| 1B | `subject_fx.py`, `sprites.py` | tags `subject`, `background`; dept `compositing` (`tools_ext/subject_tools.py`) | cards `subject_*.yaml` |
| 1C | `compositor.py`, `displacement.py` | tags `region`, `type_window`; slot `MotionItem.composite`; dept `compositing` (`tools_ext/composite_tools.py`) | owns `motion_layer.overlay_clip` / `append_graph`, `motion/lib/paint.js` and `type_window.html` |
| 1D | `accents.py`, `strobe.py`, `junctions_v2.py`, `wipe_masks.py` | tags `accent`, `strobe`, `junction`; slot `TransitionSpec.per_junction`; dept `rhythm` (`tools_ext/punctuation_tools.py`) | the `set_transitions` style extension lives in its department module |
| 1E | `finish.py`, `gradewin.py`, `lutlib.py`, `variants.py` | tags `grade_window`, `finish`; dept `compositing` (`tools_ext/finish_tools.py`) | extends `gradelut.py`; exports `hue_key()` |
| 1F | `device_planner.py`, `motion_qa.py`, `preference_memory.py` (extend) | dept `verify` | at close-out: `agent_prompt.py` and shared skills |

---

## 10. Integration

- **Overlaps.** The four Wave 0 tracks touch disjoint files except:
  - `config.py`: 0A and 0C use the separate anchors above;
  - `agent_tools.py`: 0C only, at function level.
- **Merge order** is free. The recommended order is 0D, 0A, 0B, 0C, so 0C's
  catalog savings land before any department exists.
- **Before handing back,** each track must:
  - pass its full worker suite, the backend suite, `skill_validator` and the
    plugin test;
  - pass the Cloudflare `npm run check` if it touched `worker/cloudflare` or
    the deploy workflow;
  - keep `tests/test_w0_seams.py` green;
  - report any compact-catalog byte delta.
