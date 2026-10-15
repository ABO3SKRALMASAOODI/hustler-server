# depth-time — 2.5D moves through stills and held frames, depth of field and rack focus, fog and light through depth, LiDAR point clouds, and picture-only time (reverse, echo, step-print, stutter, smooth slow motion, eased remaps)

## Editorial decision principles

Depth turns a flat frame into a place. Valmera measures the depth of the
shot once (the `depth.small` analysis, kept with the footage) and every
depth device reads it; the person matte is the two-plane fallback. Use one
when a MOMENT earns it — the payoff, the turn of an argument, a named thing,
a section change — never as a filter on everything.

- Invent for THIS story. These devices are vocabulary, not a recipe: the
  best moment in one short wants a rack focus, in the next a frozen frame
  that drifts in 2.5D, in another nothing at all. Never repeat the same
  device on every short of a run; vary the move, the moment, the material.
- A still should never sit dead on screen: archive photos, product shots
  and a held frame on the payoff come alive with `parallax` (layered by
  measured depth). `ken_burns` is the honest 2D move when there is no depth;
  a photo already placed as an overlay or insert takes `animate_still`
  (rhythm department) for a 2D move toward a face. One move per photo —
  never parallax and animate_still on the same one.
- A rack focus is a sentence with a turn ("not the rocket — the PEOPLE"):
  focus starts on what he is arguing against and lands ON the word that
  turns it (`word`). One per idea; never two in one sentence.
- Depth of field quiets a busy background behind the subject for a phrase
  — a cinematic hold, not a look for the whole short.
- Fog and light are moods for a passage (an essay, a reveal), rare in a
  talking-head short.
- The point cloud is the figure as data: a mechanism, a scan, a "how it
  works" beat, a runner or a product. Hold it for a beat (1-2 s) on black
  or over the dimmed picture; it hides the lips, so not over a long spoken
  line.
- Time devices are picture-only: the audio keeps playing forward, so
  reverse, stutter and remap belong on B-roll, a gesture, a walk or a pause
  — never on a face mid-sentence. Echo trails need real movement. When a
  speed ramp slows footage that has no spare frames, `interpolate` gives
  real in-between frames instead of a stuttering slow motion.
- `push_through` (the camera pushes through a word set in Z) is a ZOOM:
  zooms are optional and only when the user asks for one. Everything else
  here keeps the camera still.
- Restraint: one depth device per 8-10 s of a talking short is plenty; zero
  is a fine answer. Never stack two depth devices on the same moment.

## Evidence to inspect

- `get_kept_transcript` / `get_words`: the exact word a rack focus or a
  held frame lands on (pass it as `word`; the verb finds its onset).
- `look_at(times=[...])`: is there a background to lose (dof, fog), two
  planes worth pulling between (rack focus), a still or a held frame worth
  moving (parallax), real movement (echo)?
- `look_at(overlay='matte')`: where the person matte sits (the two-plane
  fallback, and the subject a point cloud draws).
- The write's reply says "measuring depth (≈N s)" when the depth analysis
  is still running: previews draw the stand-in (two planes, a Ken Burns
  move); finals wait up to 90 s for it.
- `audit_motion` and `critique_short` count devices, flag repeats and a
  house style, and judge the render.

## Strong treatment patterns

- Payoff hold: `add_depth_effect(kind='parallax', word='<LAST word of
  the payoff>')` holds the frame from the moment that word ENDS and drifts
  through it in 2.5D during the pause — the line lands, then breathes. The
  verb refuses a hold that would freeze his lips over words still being
  said; if there is no pause after the payoff, extend the final keep into
  it first. With no `move` each hold in an edit travels a different path
  (drift, rise, orbit — never a push, which is a zoom).
- Archive photo or product still as B-roll: `add_depth_effect(kind=
  'parallax', asset_key=..., start=..., end=...)` (screen stage: above the
  camera, like a cutaway). Two photos in a row: different paths.
- Turn of the argument: `add_depth_effect(kind='rack_focus',
  word='<turn word>')` — focus from the background to him (or the reverse
  for "look at THIS").
- A quiet phrase: `add_depth_effect(kind='dof', start=..., end=...)` keeps
  him sharp while the room falls away.
- How-it-works beat: `add_depth_effect(kind='point_cloud', start=...,
  end=..., backdrop='black')` — a red LiDAR scan of the subject, orbiting
  slowly; follow it with the real shot.
- Smooth slow motion on ACTION (B-roll, a walk, a launch, a gesture in a
  pause): `add_time_effect(kind='ramp', start=..., end=..., factor=0.4)`
  writes an eased ramp, re-anchors everything after it and interpolates
  the slowed part. A ramp is a real speed change — the sound slows too —
  so it is refused over speech.
- A gesture that carries feeling: `add_time_effect(kind='echo', ...)` on
  the movement, 0.6-1.2 s.

## Common failure modes

- A frozen frame over speech: the lips stop while the words go on. Freeze
  only on a pause, the payoff hold, or use a cutaway still (the verb
  refuses a hold over words unless the user asked for it).
- A ramp over a sentence: the voice drags at 0.4x. Ramp action, not
  talk.
- The same device on every short of a run (a house style): the critic
  flags it; choose the device this story earns.
- A rack focus with no turn in the words: decoration.
- A rack from an EMPTY background (a dark wall, a plain backdrop): the
  first beat is only his face out of focus while he talks — it reads as
  a focus fault. Pull from something worth seeing (a screen, a product,
  the other person; `focus_from: 'near'` for an object in front of him),
  or choose another device.
- Depth effects stacked on one moment, or on consecutive shots.
- Reverse/stutter/remap on spoken words (refused unless the user wants it).
- A point cloud held through a whole sentence: the face disappears.
- Expecting depth on a canvas program (no footage): dof, rack focus, fog
  and light need a shot.

## Verification procedure

1. `render_preview(complete=true)`, then `look_at(rendered=true,
   output_times=[...])` on the device's window: the subject sharp where
   it should be, the move smooth, no torn edges around the subject.
2. `proof_device(ids=[...])`: a rack focus lands within a frame of its word
   (land_err_f), nothing changes outside the window (region_locality).
3. `critique_short`: no dead stretches, no repeated device, B&W only as a
   device, the depth effect not on a face mid-word.
4. The report row's rung: `full` (depth), `reduced` (two planes from the
   person matte; or `interp: minterpolate` on a slowed window — the
   honest flag that the in-betweens are synthesised), `substitute` (Ken
   Burns). A final holds a degraded device until you fix it or
   acknowledge it (justify_verification_findings) — say so to the user.

## Repair ladder

1. Torn edges around the subject in a parallax: lower `amount` (0.2-0.3) or
   use push_in instead of an orbit.
2. The subject soft in dof: focus `subject` (the person matte, when
   measured, defines it); or shorten the window.
3. A rack lands late: pass the exact `word`, or `rack_at` = its onset.
4. Depth not measured yet on a final: wait for "measuring" to finish, or
   accept the reduced rung and say so.
5. Still too much: remove it (`remove_depth_time`) — restraint is a choice.

## Through apply_edit_batch

Clients that connected before these verbs existed write the same items
with `apply_edit_batch` on the `fx` layer; the batch is validated exactly
like the verbs. Times are program seconds; `end` is exclusive. A batch
does not start the depth analysis: the first render asks for it (previews
draw the stand-in meanwhile).

Parallax through the frame held at 12.4 s (the payoff hold):

```json
{"action": "upsert", "layer": "fx", "id": "dp_hold", "value": {
  "id": "dp_hold", "type": "depth", "stage": "world", "start": 12.4,
  "end": 14.6, "kind": "parallax", "move": {"path": "drift_left",
  "amount": 0.35, "ease": "in_out"}, "device": "depth_parallax"}}
```

An archive photo in 2.5D as a cutaway (the asset must be in the project):

```json
{"action": "upsert", "layer": "fx", "id": "dp_photo", "value": {
  "id": "dp_photo", "type": "depth", "stage": "screen", "start": 6.0,
  "end": 8.6, "kind": "parallax", "source": {"kind": "asset",
  "asset_key": "media/p1/archive.jpg"}, "move": {"path": "push_in",
  "amount": 0.4}, "device": "depth_parallax"}}
```

Rack focus landing on the word at 9.32 s:

```json
{"action": "upsert", "layer": "fx", "id": "dp_rack", "value": {
  "id": "dp_rack", "type": "depth", "stage": "world", "start": 8.3,
  "end": 10.5, "kind": "rack_focus", "focus_from": {"on": "background"},
  "focus": {"on": "subject"}, "rack_at": 9.32, "rack_f": 12,
  "device": "depth_rack_focus",
  "bind": {"on": "word", "ref": 57, "rhythm": "speech"}}}
```

Depth of field, a red point cloud, fog:

```json
{"action": "upsert", "layer": "fx", "id": "dp_dof", "value": {
  "id": "dp_dof", "type": "depth", "stage": "world", "start": 3.0,
  "end": 6.0, "kind": "dof", "focus": {"on": "subject"}, "blur": 0.6,
  "device": "depth_dof"}}
```

```json
{"action": "upsert", "layer": "fx", "id": "dp_cloud", "value": {
  "id": "dp_cloud", "type": "depth", "stage": "world", "start": 20.0,
  "end": 21.6, "kind": "point_cloud", "focus": {"on": "subject"},
  "backdrop": "black", "move": {"path": "orbit_left", "amount": 0.4},
  "device": "depth_point_cloud"}}
```

```json
{"action": "upsert", "layer": "fx", "id": "dp_fog", "value": {
  "id": "dp_fog", "type": "depth", "stage": "world", "start": 30.0,
  "end": 33.0, "kind": "fog", "strength": 0.7, "colour": "#c9cdd6",
  "device": "depth_fog"}}
```

Time: echo trails, smooth slow motion in a slowed window, an eased remap:

```json
{"action": "upsert", "layer": "fx", "id": "tm_echo", "value": {
  "id": "tm_echo", "type": "time", "stage": "world", "start": 15.0,
  "end": 16.0, "kind": "echo", "echo": {"count": 4, "spacing_f": 2,
  "decay": 0.55}, "device": "time_echo"}}
```

```json
{"action": "upsert", "layer": "fx", "id": "tm_smooth", "value": {
  "id": "tm_smooth", "type": "time", "stage": "world", "start": 18.0,
  "end": 21.0, "kind": "interpolate", "device": "time_interpolate"}}
```

```json
{"action": "upsert", "layer": "fx", "id": "tm_remap", "value": {
  "id": "tm_remap", "type": "time", "stage": "world", "start": 24.0,
  "end": 25.6, "kind": "remap", "curve": "hold_then_catch",
  "device": "time_remap"}}
```

Removal restores the exact previous edit:
`{"action": "remove", "layer": "fx", "id": "dp_rack"}`.
