# elements — procedural light and illustration: a figure turning into light and embers, lightning on a hit, doodles that boil and ride the footage, words orbiting a head, pictograms, sprites, abstract shapes

## Editorial decision principles

Procedural elements are made from the footage and from code, never from
stock: embers that leave the speaker's own outline, a pictogram that moves
the way he moves, a doodle that rides the product he holds. That is what
makes them feel authored instead of templated. They are rare by design:
each one is a TRANSFORMATION (the figure becomes light), an INTERRUPTION (a
strike on the hit), or a POINTER (a mark on the thing named).

- **Light dissolve** (`add_element` kind `light_dissolve`): the figure
  glows, then is gone into the room while embers drift off its edge. An
  ending, a departure, "and then it was gone". Once per piece at most.
- **Light trail / rim**: a moving subject leaves light; a rim of light
  traces a figure on a dark plate. For movement and for the reveal of a
  person, not for a seated talker.
- **Particles**: embers, sparks, dust, confetti, snow, fireworks. Sparks
  and fireworks land on `at` times (a hit of the user's track, a hard
  consonant); dust and embers drift. Composited as light.
- **Lightning**: one strike lands a word; two or three only on a track that
  earns a storm. It is a line, never a full-frame flash.
- **Doodles**: arrow, underline, circle, scribble, burst, star, check,
  cross, bracket, spiral — one at a time, attached (`attach`) to what it
  points at; the line boils like hand animation.
- **Orbit**: words on a ring round the head (attach `head`: the ring takes
  the head's size and rides it) or round a thing.
- **Pictogram, sprites, shapes**: the abstract double of a person, a
  lineup of cutouts, rhythm made visible — visual-essay and sizzle tools,
  not for under a talking head.

Invent: an underline that becomes a spark trail; embers that spell the
next word; a pictogram that walks off the speaker and onto the desk plane.
The references never had these; build the one that fits THIS story.

## Evidence to inspect

- `look_at` the window: a light dissolve needs a person on a plate darker
  or lighter than them (the light must read); embers off the edge need the
  person matte (the write says "measuring" until it is ready).
- For a doodle or an orbit that rides the footage:
  `look_at_world(times=[t])` shows what can be attached to. A ring round
  the head is about 1.35 head-heights across: it needs a medium shot with
  headroom and the head away from the frame's side — on a tight close-up
  it leaves the frame (the render reports "leaves the frame"); choose
  another device there.
- Lightning and bursts need their `at` times: the hit in the user's track
  or the stressed syllable.

## Strong treatment patterns

- Ending: `add_element(kind='light_dissolve', start=<last line end - 0.4>,
  end=<+2.5>)` — embers come with it.
- A strike: `add_element(kind='lightning', start=t-0.1, end=t+0.9,
  at=[t], box=[x0, y0, x1, y1])` from the sky to the word.
- A pointer: `add_element(kind='arrow', start, end, box=[...],
  attach='track:tr1')` after `track_point`.
- A ring of words: `add_element(kind='orbit', text='...', start, end,
  attach='head')`.

## Common failure modes

- Elements as decoration on ordinary lines: each one must be the beat.
- Two elements on screen at once (embers and confetti, a doodle and a
  ring): one at a time.
- A dissolve on a figure that keeps talking: the light is an ending.
- Lightning without a hit to land on: it reads as an accident.

## Verification procedure

- Preview: the dissolve's glow peaks before the figure fades, and the room
  fills where the figure was (no smear of the figure); embers leave the
  outline, not the frame's corners.
- Every element is deterministic: re-rendering gives the same frames.
- The render report lists `light` items with their rung: "substitute"
  (a soft bloom) while the person matte is measuring.

## Repair ladder

1. The light does not read: a warmer colour, a higher intensity, or a
   darker moment of the plate.
2. Embers too sparse / dense: `count`.
3. Lightning in the wrong place: `box` from (x0, y0) to (x1, y1).
4. Remove it: `remove_world` (light) or `remove_motion_graphic`.

## Through apply_edit_batch

```json
{"action": "upsert", "layer": "fx", "id": "lt1", "value": {"id": "lt1",
 "type": "light", "stage": "world", "start": 6.0, "end": 9.0,
 "kind": "dissolve", "duration_s": 2.6, "device": "light_dissolve"}}
{"action": "upsert", "layer": "motion", "id": "em1", "value": {"id": "em1",
 "template": "el_particles", "start": 6.0, "end": 9.6,
 "params": {"kind": "embers", "emit": "edge", "count": 420},
 "composite": {"blend": "screen"}}}
{"action": "upsert", "layer": "motion", "id": "orb", "value": {"id": "orb",
 "template": "el_orbit", "start": 1.0, "end": 4.0,
 "params": {"text": "WORLD OF BITS"},
 "anchor": {"to": "head", "follow": ["position", "scale"]}}}
```
