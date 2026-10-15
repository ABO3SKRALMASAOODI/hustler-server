# Wave 3 interfaces: "Into the world"

Interface pass for Wave 3 (ROADMAP §3 rule A8; tracks 3A–3F). It extends
`INTERFACES.md` (Wave 0) and `INTERFACES_W1.md` (Wave 1), which stay binding
where this file is silent.

- **Built by one agent** (key `w3`, branch `claude/w3-engine`) for the whole
  wave; this file is that agent's contract with the integrator, the Wave 2
  and Wave 4 builders (same base, in parallel) and the reviewer.
- **Precedence.** ROADMAP.md wins on intent; this file wins on names, shapes
  and file ownership for everything Wave 3 adds.
- **Status of each device** is in its card (`devices/cards/<id>.yaml`): a
  device that misses acceptance ships `status: dark` behind its flag.

---

## 0. Rules (inherited, plus Wave 3)

1. **Owner rules (hard).** No agent-chosen music; sounds only from the
   approved library and only when asked; zooms never by default (no Wave 3
   card sets `adds_zoom` or `adds_sound`). The end card and the free-tier
   watermark are untouched: every Wave 3 compiler and the world compositor
   run before the aspect bars, the screen-frame plate, the watermark and the
   end card (a bit-exact brand test per compiler). Flash safety stays on:
   Wave 3 types implement `flash_times` (the light dissolve and lightning
   flash only inside a region; a full-frame flash is never automatic).
2. **No new paid capacity.** Every Wave 3 model runs as a sidecar on the
   existing batch lane through the one `sidecar` job kind; the vision lane
   stays dark; the shared R2 clip store stays off. No new job kind, lane or
   pip package: the models run on the pinned `opencv-python-headless`
   (cv2.dnn's TFLite importer) and `onnxruntime`.
3. **Models** come only from the official sources of ROADMAP §7, licence
   checked and pinned by sha256 in `models.lock` (§2). Images: `batch` and
   `vision` only. Renders and the agent / MCP / Shorts lanes never run a
   model; on those lanes every verb and planner works from sidecar status
   (a verb before its sidecar is warm returns "measuring (≈N s)").
4. **Tool reach.** Every Wave 3 device is writable and removable through
   `apply_edit_batch`: the `fx` layer (definition and effect items, §3) and
   the motion layer's `anchor` field (§4) and template params (§6). Every
   track's skill has a section "Through apply_edit_batch" with the exact
   operations.
5. **Inert until used.** Old EDLs validate with unchanged signatures; a
   render with no Wave 3 field or template is byte-identical (the world
   compositor and the slot capture are no-ops for every other item; pinned
   by `tests/test_w3_seams.py`). No render version stamp is bumped.
6. **Creative range, not a house style.** Skills say when each device EARNS
   its place and push invention (combine anchors, objects and planes into
   devices the references never showed); they never prescribe a look to
   repeat on every short. B&W is a device (sections, colour on the drop, one
   colour kept, silhouette holds), never a whole-short look unless the user
   asks.

---

## 1. Sidecar kinds and the `sidecar_kinds/` seam

Wave 0 pinned the names; Wave 3 ships seven of them. They are registered
by DROPPING a module into `worker/sidecar_kinds/` (new package, owned by
the round lead of each wave that adds one): each module exports

```python
KIND_SPECS = {"<kind>": {
    "model": "<models/<name>.json stem>" | None,   # None: model-free kind
    "model_id": "...", "model_version": "...",     # model-free kinds only
    "format": "<fmt>.v1", "formats": ("<fmt>.v1",),
    "ext": "json" | "mp4" | "png", "content_type": "...",
    "fallback_models": (), "defaults": {...}, "rate": "<param>",
    "ms_per_frame": float, "group_s": float,
    "child": "<module>:<function>",                # the child-process runner
    "models": ("<name>", ...)}}                    # extra pinned files it reads
```

`sidecars.py` merges them into its kind table at import and
`analysis_jobs.py` dispatches their children; a module that fails to
import registers nothing (its kinds read `absent`). The two Wave 0 kinds
are untouched.

| Kind | Track | Producer | Data (`format`) | Rate |
|---|---|---|---|---|
| `landmarks.face` | 3A | YuNet + MediaPipe Face Landmarker (478 + iris) | JSON `landmarks.face.v1`: per sample `faces[{box, score, pts{name:[x,y]}, pose[yaw,pitch,roll], head[cx,cy,rx,ry,deg]}]` | 15 fps |
| `landmarks.hand` | 3A | MediaPipe hand detector + hand landmarks | JSON `landmarks.hand.v1`: `hands[{side, score, pts[21][x,y], box}]` | ≤30 fps |
| `landmarks.pose` | 3A | MediaPipe pose landmarks (ROI from face / previous pose) | JSON `landmarks.pose.v1`: `people[{score, pts[33][x,y,vis]}]` | 15 fps |
| `track.points` | 3A | LK pyramids, forward-backward checked (no model) | JSON `track.points.v1`: per seed `[{t, x, y, ok}]` | ≤30 fps |
| `camera.motion` | 3A | ORB + RANSAC homography, person matte excluded (no model) | JSON `camera.motion.v1`: per frame `H` (3×3, frame fractions) to the chunk's first frame + `static` flag | ≤15 fps |
| `objects.sam2` | 3B | EfficientSAM-Ti keyframes + DIS flow propagation | gray H.264 mask `mask.h264.v1` (as `matte.person`), black outside the object's window | ≤30 fps |
| `planes.h` | 3C | plane homographies (static / planar LK / camera.motion) | JSON `planes.h.v1`: per frame `H` seed quad → frame, `conf` | ≤30 fps |
| `plate.clean` | 3C | matte-excluded temporal median per shot chunk (no model) | PNG `plate.png.v1` | one per chunk |

All coordinates are **SOURCE frame fractions** on the **source clock**
(INTERFACES.md §2.2). `objects.sam2` keeps the reserved name; its manifest
records `model_id` `efficientsam_ti_2025april`. `depth.small` stays
unproduced in Wave 3 (4A owns depth; 3B's depth order is measured from the
mattes, §3.2).

---

## 2. Models (`worker/models/*.json`, `models.lock`)

| Record | File | Official source | Licence | Used by |
|---|---|---|---|---|
| `mediapipe_face_landmarker` | `face_landmarker.task` (float16/1) | storage.googleapis.com/mediapipe-models | Apache-2.0 | landmarks.face |
| `mediapipe_hand_landmarker` | `hand_landmarker.task` (float16/1) | storage.googleapis.com/mediapipe-models | Apache-2.0 | landmarks.hand |
| `mediapipe_pose_landmarker` | `pose_landmarker_full.task` (float16/1) | storage.googleapis.com/mediapipe-models | Apache-2.0 | landmarks.pose |
| `efficientsam_ti` | `image_segmentation_efficientsam_ti_2025april.onnx` | github.com/opencv/opencv_zoo (pinned commit) | Apache-2.0 | objects.sam2, cutouts |

`.task` files are zip archives of TFLite graphs; `anchors/models.py` opens
them in memory and loads each graph with `cv2.dnn.readNetFromTFLite`.

---

## 3. EDL.fx types (Wave 3 members)

| Track | Module (owned) | Tag | Stages | Compiler | Draws |
|---|---|---|---|---|---|
| 3A | `fx_schemas/world.py` | `track` | world | `world_defs:COMPILER` | nothing: a tracked point/box definition |
| 3B | `fx_schemas/world.py` | `object` | world | `world_defs:COMPILER` | nothing (or its own debug tint); provides the `object:<id>` mask |
| 3C | `fx_schemas/world.py` | `plane` | world | `world_defs:COMPILER` | nothing; provides `plane:<id>` homographies and mask |
| 3D | `fx_schemas/world.py` | `light` | world, screen | `fx_light:COMPILER` | light devices on the person matte (trail, dissolve to light) |

### 3.1 Definition items (`track`, `object`, `plane`)

A definition is a picture-stage item whose window is where the entity
exists; it is content-anchored like every picture item (a keep change moves
it). Its seed is stored on the **source clock** (`src_t`) in **source frame
fractions**, so a reframe or a new aspect never re-measures (A4). They draw
nothing; the world compositor and the `object:` / `plane:` mask providers
read them.

```
track   {seed: {src_t, x, y} | {src_t, box: [x0,y0,x1,y1]}, label?}
object  {prompt: {src_t, box?: [x0,y0,x1,y1], points?: [[x,y,1|0],...]},
         label?, depth: auto|front|behind}
plane   {src_t, quad: [[x,y]*4 TL,TR,BR,BL in the plane's own orientation],
         surface: floor|wall|desk|table|screen|ceiling|other,
         motion: auto|static|camera|planar, label?}
```

Removing a definition that an anchor still names is refused (the anchor's
EDL check names it); detach first.

### 3.2 Depth order (3B)

`object.depth = auto` decides from measurements only (objseg/depthorder.py,
world_layer.object_depth, three moments of the window): the object's mask is
its VISIBLE pixels, so it is IN FRONT of the person when most of it sits
inside the person's closed outline (whether the matte cut a hole for it or
swallowed it as "person"), BEHIND when it shows only at the outline's edge
or through a gap the closed outline spans, and undecided (treated as front)
with no contact. An object behind the person adds the person to the
occluders of a graphic hidden behind that object. `front` / `behind` force
it; the decision is in the compose notes.

### 3.3 `light` (3D)

`kind`: `trail` (lagfun light trails of the subject, screen-blended),
`dissolve` (the subject turns to light over `duration_s`: glow up, alpha to
0, the background stays; pairs with the `el_particles` embers template),
`rim` (rim light from the matte edge). `colour` (FxColour), `intensity`.
Needs `matte.person` (request_matte); substitute `halation`.

---

## 4. `MotionItem.anchor` (slot owned by Wave 3, `fx_schemas/world.py`)

A motion graphic attached to the world. `anchor: null` (default) is today's
graphic, byte for byte.

```
MotionAnchor {
  to:        face | face.<part> | head | hand.<part> | pose.<joint>
             | track:<fx id> | object:<fx id> | plane:<fx id> | screen
  which:     any | left | right | 0..7         (which face / hand / person)
  lock:      world | screen                     (world rides the camera)
  pin:       [px, py]   point of the graphic's box set on the anchor
             (fractions of the graphic box; default [0.5, 0.5]). On a
             ``screen`` anchor whose occluders name an object, ``pin``
             SEATS the graphic: its ink point ``pin`` is set on the object
             (a wide object's top edge, a tall one's middle) measured on
             the landing frame, kept in frame and off the faces —
             ``[0.5, 0.68]`` puts about a third of a word behind a laptop
  offset:    [dx, dy]   in units of the anchor's size (face height, hand
             span) when follow includes "scale", else frame fractions
  follow:    ["position"] + optional "scale", "rotation"; "shape" on face /
             head anchors (an affine fitted to the face mesh each frame:
             a registered plate foreshortens with a turn). Smoothing is
             zero-phase (forward + backward One-Euro: renders are offline)
  place:     [u0, v0, u1, v1]  planes only: where on the plane (plane
             fractions; default: fitted, centred)
  occluders: ["person" | "hands" | "object:<fx id>", ...]  what passes in
             front of the graphic (default: none; a plane anchor — floor,
             desk, wall, screen — defaults to ["person"]: the person stands
             in front of the room's surfaces; [] draws over them)
  shadow:    bool  planes only: the surface's real shadows darken the type
  smooth:    0..1  One-Euro smoothing (default 0.5)
  lost:      hold | hide | static   (tracking lost: keep the last pose,
             fade out, or pin where it was written)
}
```

- **Parts.** face: `eye_l, eye_r, iris_l, iris_r, nose, mouth, chin,
  forehead, ear_l, ear_r, brow_l, brow_r`; hand: `index_tip, thumb_tip,
  middle_tip, palm, wrist, pinch, frame` (`frame` = the quad of the two
  hands' thumb and index tips: a graphic or region inside the fingers);
  pose: `nose, shoulder_l, shoulder_r, chest, elbow_l, elbow_r, wrist_l,
  wrist_r, hip_l, hip_r, knee_l, knee_r, ankle_l, ankle_r`. `left`/`right`
  are ON SCREEN (the viewer's left), never anatomical.
- **Rendering.** `world_layer.py` (3A) turns the item's rendered clip into
  a program-space RGBA clip before the graph is built: per program frame,
  program → source time (Timeline), the anchor's state from its sidecar,
  source → program geometry (`keepout.Geometry`: crop, follow, picture,
  zoom, cards), smoothing, then warp (affine, or the plane homography),
  occluders cut from the alpha (person matte, object masks, in program
  space), and shadow transfer. The result replaces the clip; the usual
  overlay (and 1C's `composite`, blend included) composites it. Layer:
  an anchored item draws on its own layer (`below_captions` default for
  world type); `behind_subject` is folded into `occluders: ["person"]`.
- **Measured from the first render** (review w3r): `world_layer.warm`
  launches every sidecar the render's world items read (landmarks, the
  definitions they name, the person matte, the clean plate) through the
  render's launch source, exactly as the readers key them — so a stale
  client's apply_edit_batch items get measured too. Previews never wait;
  finals wait within the render's sidecar budget (≤ 90 s), then degrade.
- **Device rows**: every anchored item reports a row in the render's
  `devices` block (`full`; `reduced` on the face detector's box, an
  unmeasured plane track or a graphic that leaves the frame; `substitute`
  when pinned static or drawn in front of an unmeasured occluder), so the
  DEGRADED gate holds a final that shipped one degraded.
- **Degrade ladder** (A3, ROADMAP §9): landmarks → YuNet box (faces:
  `faces.yunet`, prefetched at index, gives the box and five points) →
  last good pose → `static_placement` (where it was written, with "pinned
  static: tracking confidence low"). Occlusion missing → `type_in_front`
  with a note. A plane whose reprojection residual is over the bar → the
  graphic screen-locked at the plane's first-frame position.

---

## 5. Masks (fxgraph labels, Wave 3)

| Label | Provider | Notes |
|---|---|---|
| `object:<fx id>` | `world_defs:object_mask` | the object's program-space mask over its window (precomputed gray clip, plan input) |
| `plane:<fx id>` | `world_defs:plane_mask` | the plane quad, filled, per frame |
| `hands`, `hands:<left\|right>` | `world_defs:hands_mask` | convex hulls of the hand landmarks, feathered |

Not registered in `fxgraph.MASK_PEERS` in Wave 3: no fx compiler consumes
these labels yet (the world compositor reads objects, planes and hands
directly), so the providers exist for Wave 4 and a label reads absent =
black (inert) today. Registering them is one `MASK_PEERS` line each.

---

## 6. Templates and the slot contract (3D, 3E)

New templates (`motion/templates/`), all browserless-safe at write time:

| Track | Templates |
|---|---|
| 3D | `el_particles` (embers, sparks, dust, confetti, fireworks; emitters from a point, a box, or the subject's edge), `el_lightning`, `el_doodle` (arrow, underline, circle, scribble, burst, stamp — hand boil), `el_pictogram` (a runner / walker / pointer from pose or a library cycle), `el_shapes` (bouncing shapes with echo, golden spiral, rings), `el_sprites` (cutout choreography: ring, row, swarm, pop, implode, collapse), `el_orbit` (words orbiting a point) |
| 3E | `mosaic`, `scroll_wall`, `flatlay`, `stack`, `device_stage`, `glass_card`, `hud`, `breakdown`, `spec_sheet` |

**Engine data a page receives** (injected by `world_layer.augment` before
the render, never authored): `_edge` (subject-edge emitter samples in
design px per 1/10 s), `_pose` (joints per 1/15 s), `_spill` (slot content
mean colour per frame), `_edl` (the breakdown's truthful EDL summary),
`_beats` (0B's plumbing).

**Video slots.** A page calls `MG.slot(id, x, y, w, h, {r, z, fit})` every
frame (`motion/lib/slots.js`); `motion_engine` records the rects into
`<clip>.slots.json` (the only Wave 3 edit of `motion_engine.py`). Params
name each slot's source: `slots: [{id, source: FxSource-like {kind: asset |
source | program, asset_key | src_t, offset_s, loop}}]`. `slots.py` then
composites the real video into the rects, rounded and clipped, UNDER the
page's pixels, into the clip the overlay draws. A slot whose source fails
is drawn as the page's own placeholder (degrade, never fail).

---

## 7. Department verbs (`tools_ext`)

| Module | Department | Track | Verbs |
|---|---|---|---|
| `tools_ext/world_tools.py` | world | 3A–3C | `attach`, `track_point`, `select_object`, `pin_to_plane`, `place_in_world`, `remove_world`, `look_at_world` (READ: the anchors overlay; the core `look_at` enum stays fixed) |
| `tools_ext/element_tools.py` | world | 3D | `add_element` |
| `tools_ext/layout_tools.py` | world | 3E | `add_collage`, `set_stack`, `add_hud`, `add_breakdown`, `add_device_stage`, `set_brand_kit` |
| `tools_ext/preprod_tools.py` | verify | 3F | `plan_footage` |

- `place_in_world(what, where)` is the attachment grammar in one call
  ("on the desk", "behind the laptop", "follow her hand", "round his
  head"): it parses `where` (`attachment.py`), resolves the target, and
  writes the definition + the anchored graphic in ONE batch.
- Department compact budget: `world` ≤ 12,000 bytes (all of 3A–3E);
  `verify` share for 3F ≤ 2,500. Core growth: the `world` department name
  in the two domain enums (+16 B). Nothing else is added to the core
  catalog.
- 1F's `look_at(overlay=…)` gains `anchors` (faces, hands, pose, planes,
  objects drawn on the frame) — a function-level edit, no new parameter.

---

## 8. Cards (`devices/cards/`)

Card-id prefixes, one owner each: `anchor_` (3A), `object_` (3B), `plane_`
(3C), `element_`, `light_` (3D), `layout_` (3E). Families: `world` (3A–3C,
`light_`), `layout` (3E), `composite` (3D elements that composite as light).
The ledger maps `world` and `layout` already (`taste._CARD_LEDGER`); an
anchored motion item counts as `world`, an occluded one as `depth` too.

---

## 9. Agent layer v3 (3F)

- `attachment.py`: phrase → `{relation: on|behind|follow|around|in_front|
  between, target: <kind>, name?, part?}`; deterministic, browserless.
- `world_planner.py`: world and layout candidates for `suggest_devices`
  (called through `fxgraph.peer("world_planner:candidates", ...)` from the
  1F planner), each with the exact apply_edit_batch ops; per-line
  no-repeat budget (a line of speech never repeats a world device: ref 21).
- `coverage.py`: `plan_footage(brief)` → ≤12 shot cards (framing SVG,
  lens, duration, which device each enables); the acquisition dialogue
  offers at most 3 options, each with cost and time.
- `warmers.py`: upload-time analysis policy (what each sidecar kind is
  prefetched for, at which priority, on the batch lane only).
- `webrecord.py`: DOM element boxes recorded into `record_website`'s event
  track, so a push or an iris can land on a clicked element.
- `formats/D.json`: genre D (creator essay with type in the world).
- Skills: `tracking.md` (3A), `occlusion.md` (3B), `type-in-world.md` (3C),
  `elements.md` (3D), `layouts.md` (3E), `world.md` + `acquisition.md` (3F).

---

## 10. Shared-file edits Wave 3 makes (surgical, listed in the progress file)

As built (the progress file lists each): `sidecars.py` (merge
`sidecar_kinds`; the upload warm-up hook), `analysis_jobs.py` (dispatch their
children), `schemas.py` (one anchor check in `validate_edl`),
`motion_layer.py` (`prepare_inputs` calls `world_layer.augment` /
`world_layer.finish`), `renderer.py` (`_world_ctx`: the render context, on
the main and the canvas `prepare_inputs` calls; it launches the world
measurements through the render's launch source), `motion_engine.py` (slot
capture), `motion_qa.py` (the `anchors` overlay), `device_planner.py` (the
peer call; Wave 3 cards left to world_planner), `keepout.py` (element
exemptions, the glass card's ink), `webrecord.py` (element boxes,
`-fps_mode`), `agent_prompt.py` (one paragraph), `devices/__init__.py`
(card prefixes), `motion_look.py` (world slams never join a screen series),
`stitch.py` (world definitions rebased exactly), `backend/routes/mcp.py`
(`plan_footage` read-only) and the seam tests that set Wave 3 growth aside.
No `fxgraph.py` or `agent_tools.py` edit was needed (§5; `look_at_world`
carries the overlay).
