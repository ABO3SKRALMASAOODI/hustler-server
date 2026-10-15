# world — the attachment language: put a graphic ON the desk, BEHIND the laptop, ON a fingertip, ROUND a head; one world device per line, never the same trick twice

## Editorial decision principles

The world layer makes graphics belong to the footage: they sit on its
surfaces, pass behind its objects, ride its hands and circle its people.
Say where in plain words and the verbs do the measuring:

| The user (or you) says | Verb | What it writes |
|---|---|---|
| "put the price on the desk" | `place_in_world(where='on the desk', text=..., t, quad)` | a plane definition + type pinned to it (`plane:<id>`) |
| "hide the word behind the laptop" | `place_in_world(where='behind the laptop', text=..., t, box)` | an object definition + the word occluded by it (`object:<id>`), set where the object can hide it (rising from behind a wide object's top edge; crossed by a tall one) |
| "make the label follow his hand" | `place_in_world(where='follow his hand', motion_id=...)` or `attach(to='hand.palm')` | a hand anchor; `lost='hide'` when the hand leaves |
| "the number on her fingertip" | `attach(to='hand.index_tip', pin=[0.5, 0.72])` | a glass card riding the fingertip |
| "between his hands" | `place_in_world(where='between his hands')` | the hands' frame quad (`hand.frame`) |
| "arc it round his head" | `add_element(kind='orbit', attach='round his head')` | words orbiting the head, the far side behind it |
| "next to her face" | `place_in_world(where='next to her face', motion_id=...)` or `attach(to='face', offset=[0.9, 0], follow=['position', 'scale'])` | a label beside the face, following it (offset in face heights; negative = the other side) |
| "on the floor under my shadow" | `pin_to_plane(quad, t, shadow=true)` | floor type darkened by the real shadow |
| "follow that thing" | `track_point(t, x, y)` then `attach(to='track:<id>')` | a tracked point |

Which device a line earns is a STORY decision. A price belongs on the
desk it is negotiated over; a secret goes behind the object that hides
it; a number someone counts on their fingers rides the finger; the idea
someone "can't stop thinking about" circles their head. A line that
names nothing in the room earns no world device — words on black or
captions alone are a strong answer.

The no-repeat budget (the exemplar essays repeat no device): within one
sentence no world device repeats, and no world device appears on two
consecutive sentences. Three world devices in a 30 s piece is rich; one
is often enough. Black-and-white is a device here too (a section, colour
returning on the drop, one colour kept, a silhouette hold), never the
whole short unless the user asks.

Invent beyond the table: a word that slides along the desk and drops off
its edge on the cut; a list whose items appear behind each object the
speaker touches; a fingertip card that the other hand flicks away; type
on the wall that the speaker's shadow erases word by word. The bar is
the most surprising true-to-the-footage idea for THIS line, not the last
trick that worked.

Never a zoom or a sound effect as part of a world device unless the user
asked; never music.

## Evidence to inspect

- `look_at_world(times=[t])` shows the measured face mesh, hands,
  pose and every world definition on the frame: check the hand is really
  up, the face big enough, the object's outline tight.
- The surface or object must be visible for the whole window; a quad's
  corners come from `look_at` in program fractions (TL, TR, BR, BL).
- `suggest_devices` proposes world candidates only where the
  measurements support them (a hand up for >= 0.8 s, a face >= 7% of the
  frame, an object or plane the edit already defined, the matte ready).
- A write that needs analysis returns "measuring (≈N s)": the preview
  shows the graphic where it was authored until the measurement lands
  (a face anchor uses the face detector's box meanwhile). Items written
  straight through apply_edit_batch are measured from their first render;
  a final waits up to 90 s, then reports the device as degraded.

## Strong treatment patterns

- Opening: a word already written into the room before the speaker talks
  (`place_in_world(where='on the wall', ...)`).
- Proof lines: one per proof, each attached to what the proof is about —
  the object behind, the surface under, the hand that shows it.
- Payoff: the line the piece exists for, once, large, on a surface or
  behind the object it names.
- Ending: the speaker turns to light (`add_element(kind='light_dissolve')`,
  embers off the edge by default) — only when the piece truly ends on a
  release or a vanishing, at most once, and not on every short of a batch:
  an ending that recurs becomes a house style.

## Common failure modes

- The same device on consecutive lines (two desk words in a row).
- A fingertip card while the hand is down or out of frame: `lost='hide'`
  hides it; better, choose the word where the hand is up.
- Type on a plane that the crop cuts away (check the 9:16 frame).
- An occluder object that moves more than its window: keep objects <= 6 s.
- Saying a device is "not possible" — when the footage cannot carry it,
  call `plan_footage` and offer the acquisition options
  (`read_skill acquisition`).

## Verification procedure

- `proof_device` on the world items: the anchor lands within 1.5% of the
  frame width of its landmark on 90% of frames; occluded letters are cut
  by the object's edge, not by a box; plane type keeps the surface's
  perspective as the camera moves.
- `look_at_world` on the first, middle and last frame of the
  window.

## Repair ladder

1. Wrong spot: `attach` again with `pin` / `offset`, or redraw the quad.
2. Jitter: raise `smooth`; lost landmarks: `lost='hold'` or `'static'`.
3. Occlusion too tight or loose: re-`select_object` with a box at a
   clearer frame.
4. Remove: `remove_world(ids=[...])` (graphics detach, definitions go).

## Through apply_edit_batch

Definitions are stored on the SOURCE clock in SOURCE frame fractions
(read them off `look_at(times=[<source second>])`, the uncropped frame);
the graphic's own params (`y`, `x`) are PROGRAM fractions — put a word
behind an object where the object can hide part of it.

```json
{"action": "upsert", "layer": "fx", "id": "ob1", "value": {"id": "ob1",
 "type": "object", "stage": "world", "start": 4.0, "end": 7.5,
 "prompt": {"src_t": 843.2, "box": [0.40, 0.77, 0.66, 1.0]},
 "label": "laptop", "device": "object_occluder"}}
{"action": "upsert", "layer": "motion", "id": "w1", "value": {"id": "w1",
 "template": "word_slam", "start": 4.2, "end": 6.4,
 "params": {"text": "SECRET", "y": 0.75, "width": 0.8},
 "layer": "above_captions",
 "anchor": {"to": "screen", "occluders": ["object:ob1"]}}}
{"action": "upsert", "layer": "motion", "id": "gc1", "value": {"id": "gc1",
 "template": "glass_card", "start": 9.0, "end": 11.0,
 "params": {"title": "3x"}, "anchor": {"to": "hand.index_tip",
 "pin": [0.5, 0.72], "lost": "hide"}}}
```
