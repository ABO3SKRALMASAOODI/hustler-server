# explainers — explaining through interface: persistent cards, a karaoke pill, comment threads, dashboards with counters and bars from the user's data, editor windows and real recordings in media slots, morphing between scenes and opening as irises from what caused them

## Editorial decision principles

An explainer (genre E) shows the idea's STRUCTURE: the parts, the steps,
the numbers, the product's screens. `add_ui_scene` builds it as a small
interface whose objects persist: the card you met in scene one is the same
card in scene three — it moves, shrinks into a chip, grows into the focus —
so the viewer never loses the thread. That continuity is the craft.

- Invent for THIS explanation. A process wants a pill that goes idle ->
  live -> done; a comparison wants two metrics side by side and then bars;
  an argument wants a comment thread; a product wants its real screen
  recording in a media slot. Never the same dashboard on every short.
- Same id = same object. Keep the objects that carry the idea alive across
  scenes; let the supporting ones come and go.
- Open a new idea FROM the thing that caused it: `from: '<id>'` irises the
  new object out of that element (the warning card opens out of the
  handlebar counter that hit zero). One or two irises per scene graph.
- Figures are never invented. A number on screen is bound to the user's
  data (`data`: CSV or {name: number}; `bind`), said in the transcript, or
  written in the user's request — anything else is refused by name.
- Let the counters land ON the spoken number (automatic when it is said
  inside the window); the eye and the ear meet.
- Restraint: 2-4 scenes, at most 4-5 objects on screen; the words carry
  the explanation, the interface carries its shape.

## Evidence to inspect

- `get_kept_transcript`: the steps and the numbers as said (which ones are
  spoken, so the counters can land on them).
- The user's data (a CSV, a table in the request): the only source of
  figures not spoken.
- The write's reply: which objects persist, where the irises open from,
  which counters land on a word.
- `render_preview(complete=true)` + `look_at(rendered=true)` on each scene
  change: the morph reads as one object moving, the iris opens from the
  right element.
- `critique_short`: type scale, dead stretches, graphics share.

## Strong treatment patterns

- A process in three beats: scene 1 `pill` (state idle) + a `card`;
  scene 2 the same pill `live` and a `metric` that counts; scene 3 the
  pill `done` and a `card` opened `from` the metric.
- Data, not decoration: `bars` with `rows_from: {key: 'part', col:
  'units'}` over the user's CSV; the tallest bar is the point.
- The product itself: a `media` slot playing the screen recording (media:
  {slot: {asset_key}}), the explanation in cards around it, the slot
  moving to the `focus` when the words reach it.
- An argument as a thread: `comment` objects in a `thread` layout, each
  arriving on its line.

## Common failure modes

- A dashboard on every short (a house style).
- Numbers the user never gave (refused) — or round, fake-looking ones.
- Too many objects: chips smaller than the type can carry.
- Scenes faster than the words: each scene holds while its line is said.
- A UI scene over the punchline: the face carries the payoff.

## Verification procedure

1. Read the reply: persistent objects, iris origins, counters landing.
2. `render_preview(complete=true)`, then `look_at(rendered=true,
   output_times=[...])` at every scene start: nothing cuts that should
   morph.
3. Check every figure against the user's data.
4. `critique_short`: no dead stretch, no type below the ladder.

## Repair ladder

1. A morph reads as a cut: keep the same id across the scenes.
2. Too busy: drop to `focus` layout with chips, or split into two scenes.
3. A figure refused: bind it to the user's data or leave it out.
4. The slot crops a screen recording: `media: {slot: {asset_key, fit:
   'contain'}}`.
5. Still not earning it: `remove_ui_scene` — the face may carry it better.

## Through apply_edit_batch

The scene graph is SOLVED by the verb (layout, morphs, irises, counters);
a stale client writes the solved item. The motion item's `params.graph` is
the solved graph (`ui_scene.solve`), `params.shown` every string it
prints; each media slot is a `ui_media` fx item naming the motion item and
the slot.

```json
{"action": "upsert", "layer": "motion", "id": "ui1", "value": {
  "id": "ui1", "template": "ui_scene", "start": 3.0, "end": 11.0,
  "layer": "below_captions", "params": {"graph": {"v": 1, "W": 1080,
  "H": 1920, "dur": 8.0, "scenes": [0.0], "objects": [{"id": "c1",
  "kind": "card", "title": "Front forks", "design": [70, 760, 940, 200],
  "segs": [[0.0, 0.45, 70, 760, 940, 200, 70, 760, 940, 200, 0, 1,
  "snap"], [0.45, 8.0, 70, 760, 940, 200, 70, 760, 940, 200, 1, 1,
  "linear"]], "iris": null, "enter": 0.0}], "irises": [], "u": 3.0},
  "shown": ["Front forks"], "backdrop": "solid"}}}
```

```json
{"action": "upsert", "layer": "fx", "id": "um1", "value": {
  "id": "um1", "type": "ui_media", "stage": "screen", "start": 3.0,
  "end": 11.0, "motion_id": "ui1", "slot": "screen", "source": {
  "kind": "asset", "asset_key": "media/12/recording.mp4"},
  "fit": "contain", "device": "ui_media_slot"}}
```

Removal: `{"action": "remove", "layer": "motion", "id": "ui1"}` and
`{"action": "remove", "layer": "fx", "id": "um1"}`.
