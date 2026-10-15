# occlusion — type tucked behind real things: a word behind the laptop, behind a bottle, behind the boom mic, behind the speaker and the objects in front of him

## Editorial decision principles

Type that hides behind a real object stops being an overlay and becomes
part of the room. It is one of the most convincing devices there is — and
it costs a precise object mask, so it is used where the object is ALREADY
in front of the place the words want to be: a laptop on the lectern, a
bottle, a mic arm crossing the frame, a desk edge, a door jamb.

- The word sits in the space just behind the object, so the object cuts
  its lower edge or crosses it. Behind a laptop: low in frame, the lid
  biting the baseline. Behind a mic arm: the arm slicing through the
  letters.
- Behind the speaker AND the objects in front of them: `occluders`
  `["person", "object:<id>"]` — the word lives between the wall and the
  person, and the mic in front of the person still crosses it.
- One occlusion device per beat; it pairs with a payoff word or a name, not
  with filler. Two in a 30 s piece is plenty.
- Invent beyond the references: a word that slides out from behind the
  laptop as he says it, a number that the bottle hides until he moves it,
  type that a passing hand wipes away.

## Evidence to inspect

- `look_at(t)` on the frame: the object's box (program fractions) — tight
  round the object, not the whole table.
- The object must stay where it is (or move smoothly) for the window; the
  measurement covers at most 6 s per object (`select_object` again for a
  later window).
- Check that the word's place is actually BEHIND the object's top edge:
  type entirely below the object is invisible; entirely above it is never
  occluded (and then the device is wasted).

## Strong treatment patterns

- One call: `place_in_world(text='FLYING CARS', where='behind the laptop',
  t=<look_at time>, box=[x0, y0, x1, y1])` — it writes the object, the
  word and the occluder link in one batch.
- Two objects (laptop and bottle): `select_object` each, then
  `attach(motion_id, to='screen', pin=[0.5, 0.68],
  occluders=['object:ob1','object:ob2'])` (seated on the first).
- Behind the speaker and the mic: `occluders=['person','object:mic']`.

## Common failure modes

- A loose box that includes the table: the mask takes the table too and
  eats the word. Box the object only; add a point on it if it is thin.
- A thin object (a mic arm) under a box: add `points` [[x, y]] along it.
- Occluding the payoff's key letters: the reader must still read the word;
  aim for a third of the letter height hidden, not half the word.
- A word behind an object that leaves the frame mid-window: shorten the
  window.

## Verification procedure

- Preview, then look at the frame where the word lands and one where the
  object moves (if it does): the object's edge cuts the letters cleanly,
  with no halo and no flicker along the edge.
- The render report says "object:<id> is not measured yet: drawn in front"
  while the mask is measuring; re-render before approving.

## Repair ladder

1. The mask is wrong (too much or too little): `select_object` again with
   a tighter box or extra points; remove the old one.
2. The word is barely occluded: move it down (or the object's window) so
   the object crosses it.
3. The word is unreadable: move it up, or make it larger.
4. Still not measured: keep the word in front (remove the occluder) and
   say so.

## Through apply_edit_batch

```json
{"action": "upsert", "layer": "fx", "id": "ob1", "value": {"id": "ob1",
 "type": "object", "stage": "world", "start": 15.4, "end": 21.3,
 "label": "laptop", "prompt": {"src_t": 43.1,
 "box": [0.398, 0.774, 0.664, 1.0]}, "device": "object_occluder"}}
{"action": "upsert", "layer": "motion", "id": "cars", "value": {"id": "cars",
 "anchor": {"to": "screen", "pin": [0.5, 0.68],
            "occluders": ["object:ob1"]}}}
```

Prompts are SOURCE seconds and SOURCE fractions (the uncropped footage).
`pin` seats the word on the object at render (its ink point on the
object's measured top edge, or a tall object's middle); without it the
word stays where its params put it.
Removing an object that a graphic still names is refused: detach first
(`anchor: null`) or use `remove_world`, which detaches for you.
