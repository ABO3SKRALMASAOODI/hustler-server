# critic — judging a rendered short like a demanding editor against the reference reels: rhythm and dead stretches, the type ladder, restraint, B&W as a device, faces, late words, flicker, cuts through words, flashes, the brand, and whether the edit invented anything — with the exact fix

## Editorial decision principles

`critique_short` watches the RENDER, not the EDL: it reads the frames and
the sound the viewer gets and answers the questions an editor asks before
anything ships. Use it after every meaningful render, and believe it over
your own description of the edit.

- Blocking findings (a cut through a word, a flash rate over 3 per second
  without the user's opt-in, the end card or watermark disturbed, type on
  a face) are fixed before anything else.
- Fix findings are craft: a dead stretch, a late word, a flickering matte,
  a stuttering slow motion, a payoff that does not hold.
- Advisory findings are taste: a house style (the same devices as the
  last shorts), B&W as a whole-short look, an untapped moment. The owner
  wants invention — when the critic says "house style", change the idea,
  not the parameter.
- B&W is a device, never a look: sections with colour on the drop, one
  colour kept, a silhouette hold, a negative on a hit. A short that reads
  black-and-white throughout is flagged unless the user asked for it.
- The critic never adds music, a zoom or a sound; its ops only remove,
  move or reshape what is there.

## Evidence to inspect

- The reply: score, findings with times and a one-line fix, ideas (up to
  three moments the edit has not used yet), strengths.
- The critic board (attached): the render's frames with ticks — red
  blocking, amber fix, blue advisory.
- OPS: apply_edit_batch operations for the findings it can fix exactly
  (shift a late item, remove a flash, add interpolation to a stuttering
  slow window, keep a word whole).
- `photosens` measures in the reply: peak flashes per second, red flashes.

## Strong treatment patterns

- Render, critique, fix the blocking findings, render again; then the
  fix findings; then decide on the advisories with taste.
- When the critic offers an untapped moment, ask whether THIS story earns
  a device there — sometimes the strongest answer is still nothing.
- Compare against the previous short of a run: the critic reports devices
  repeated across the run as a house style.

## Common failure modes

- Treating the score as the goal: the findings are the work.
- Applying every OPS line blindly: read each; apply the ones you agree
  with in one batch.
- Fixing a flash rate by deleting every hit: the flash-safe plan keeps the
  downbeat hits and thins the rest.

## Verification procedure

1. `render_preview`, then `critique_short`.
2. Blocking findings: zero before export.
3. `export_final`, then `critique_short(variant='final')`: the brand
   check runs on finals (end card and watermark, exactly as rendered).

## Repair ladder

1. A cut through a word: move the join to the pause (the OPS line).
2. A late word: shift the item onto the word (OPS).
3. Too many flashes: the flash-safe plan (keeps downbeats), or the
   user's explicit opt-in (flags the export).
4. Type on a face: move the graphic or let the caption plan re-place it.
5. A house style: replace the repeated device with one this story earns.

## Through apply_edit_batch

`critique_short` is read-only; its OPS are ordinary apply_edit_batch
operations, for example shifting a late graphic onto its word:

```json
{"action": "upsert", "layer": "motion", "id": "m3", "value": {"id": "m3",
  "start": 12.41, "end": 14.2}}
```

and removing one flash of a strobe run:

```json
{"action": "remove", "layer": "fx", "id": "acc7"}
```
