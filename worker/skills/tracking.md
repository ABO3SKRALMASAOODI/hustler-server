# tracking — graphics that ride the footage: a card on a fingertip, a window between two hands, words circling a head, a label beside a face, an arrow that stays on a moving thing

## Editorial decision principles

A tracked graphic says "this, here, now": the thing the speaker is holding
up, pointing at or shaping with their hands becomes the place the idea
lives. It earns its place when the footage already ACTS — a hand rises, a
head turns, a product is shown — and the line names what the gesture
shows. A graphic stuck to a still face reads as a sticker; stuck to a
gesture it reads as the speaker's own idea made visible.

- **Fingertip / palm** (`attach` to `hand.index_tip`, `hand.palm`): a small
  glass card, a number, one word — the size of the hand, never bigger. Use
  `pin` [0.5, 1.1] so the card sits just above the finger, and
  `lost: hide` (the default for hands) so it fades when the hand drops.
- **Between the hands** (`hand.frame`): the graphic fills the quad of both
  thumbs and index fingers — a window, a picture, an effect the speaker
  "holds". Only when both hands are up and apart for the whole window.
- **Round the head** (`head`, template `el_orbit`): an arc or ring of words
  on the head's own ellipse — a title for the person, an idea "in their
  head". `follow` scale keeps it the head's size as they lean in.
- **Beside the face** (`face`, `offset` [±0.9, 0]): a name, a label, a
  quote mark that turns with the head.
- **Iris / pupil** (`face.iris_l`): the origin of something opening from
  the eye — an iris transition, a reveal, a tiny HUD.
- **Body** (`pose.chest`, `pose.shoulder_*`): a tag on the chest, a label on
  a shoulder; for walking subjects a marker that keeps up.
- **A tracked point** (`track_point`, then `track:<id>`): anything else
  that moves — a product edge, a logo, a car — for arrows, circles,
  doodles and callouts.

Invent: combine anchors. A word on the fingertip that becomes a plane on
the desk when the hand lands; an orbit that tightens as the voice drops; a
label that leaves the face and settles on the object he names. The
references never did this; the footage now lets you.

Restraint: one tracked device per beat, at most two or three in a 30–45 s
talking head; never repeat the same attachment on consecutive lines. Every
tracked graphic is silent and zoom-free.

## Evidence to inspect

- `look_at(t, overlay='anchors')` shows the measured faces, hands and body
  on the frame before you write (and what is not measured yet).
- The hand must be UP and visible for the whole window: check two or three
  frames. A hand that drops halfway needs `lost: hide`, or a shorter window.
- Face size: under ~6% of the frame height the landmarks get noisy; anchor
  to `face` (the box) rather than a small part.
- A write that says "measuring (≈N s)" renders the graphic where it was
  authored until the landmarks are ready; finals wait up to 90 s.

## Strong treatment patterns

- Fingertip card: `add_motion_graphic` (a small card or `glass_card`), then
  `attach(motion_id, to='hand.index_tip', pin=[0.5, 1.12])`.
- "Between his hands": `attach(motion_id, to='hand.frame')` with an image
  or a footage fill — the graphic is warped into the hands' quad.
- Head arc: `place_in_world(text='SIX FACES OF THE FUTURE', where='round
  his head')` writes an `el_orbit` attached to `head` with follow scale.
- Arrow on a moving product: `track_point(t, x, y)` then an `el_doodle`
  arrow attached to `track:<id>` with `pin` at the arrow's tip.

## Common failure modes

- A card that covers the face because the hand passed in front of it:
  pin the card ABOVE the finger and keep it small.
- `hand.frame` while only one hand is up: nothing to frame; the graphic
  hides (`lost: hide`) or holds the last frame — choose a window where both
  hands are up.
- Anchoring to a face part on a profile shot: the far eye is hidden;
  use `face` or `head`.
- Smoothing too high on fast gestures (lag) or too low on a still hand
  (jitter): 0.4–0.6 suits most; raise it for slow drifting moves.

## Verification procedure

- Render a preview and look at three frames across the window: the
  graphic sits on its anchor (within ~1.5% of the frame width) and moves
  with it.
- On a still hand the graphic must not shimmer; on a fast one it must not
  trail more than a frame.
- The render report lists "pinned static" or "tracking lost" notes when
  the anchor was not measured or not seen; read them before approving.

## Repair ladder

1. Lost frames: shorten the window to where the anchor is visible, or
   `lost: hide`.
2. Wrong hand or face: `which` left / right (on screen) or an index.
3. Jitter or lag: `smooth` up (steadier) or down (snappier).
4. Too big or too small: `follow` with "scale" (keeps the anchor's size
   relation), or resize the graphic itself.
5. Not measured yet: re-render later; or `clear=true` to detach and keep
   the graphic static.

## Through apply_edit_batch

A client whose tool list predates Wave 3 writes the same anchor directly:

```json
{"action": "upsert", "layer": "motion", "id": "m1", "value": {"id": "m1",
 "anchor": {"to": "hand.index_tip", "pin": [0.5, 1.12], "lost": "hide"}}}
{"action": "upsert", "layer": "motion", "id": "m2", "value": {"id": "m2",
 "anchor": {"to": "head", "follow": ["position", "scale"]}}}
{"action": "upsert", "layer": "fx", "id": "tr1", "value": {"id": "tr1",
 "type": "track", "stage": "world", "start": 3.0, "end": 6.0,
 "seed": {"src_t": 812.4, "x": 0.62, "y": 0.41}, "device": "anchor_track"}}
{"action": "upsert", "layer": "motion", "id": "m1", "value": {"id": "m1",
 "anchor": null}}
```

Seeds are SOURCE seconds and SOURCE frame fractions (the uncropped
footage); the verbs convert from what look_at shows. Removing a `track`
that an anchor names is refused until the anchor is detached.
