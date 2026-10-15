# type-fx — how type arrives, moves and leaves: per-glyph verbs on the spoken onset (cut, blur resolve, settle, drop, stretch smear, mosaic, scramble lock, typewriter, duration wipe, strobe flip, emboss ink, outline fill, echo stack, glyph morph, lamp), path text round a head, decays, and materials

## Editorial decision principles

A verb is how a word MEANS on screen, chosen per beat, never one house
move for every lockup.

- Every verb lands on the spoken onset: a blur resolves sharp ON the
  onset frame, a duration wipe crosses the word while it is said (50% at
  its midpoint), a drop lands, a lamp glows on the word being said.
- Pick the verb from the meaning: a resolve for a realisation, a drop or
  cut for a fact, a duration wipe for a drawn-out word, a scramble lock
  for a code or a number, a typewriter for a quote being dictated, an echo
  stack for repetition, a glyph morph for a word that turns into another,
  a strobe flip only when the user asked for flashing.
- Path text circles the head (arc on the head's ellipse) without touching
  it; it is a hook or payoff device, not a caption.
- Exits matter: a decay (letters falling away) for an ending, a cut on a
  cut. A lockup never outstays its words by more than about a second.
- Materials (flat, glow, neon, crt, outline, ghost, emboss) belong to the
  voice; change the material only to make a point (a neon word in a flat
  sentence).
- No sound is attached to a verb by default; sounds stay optional.

## Evidence to inspect

- `get_syllables` for syllable-timed verbs (a staircase per syllable).
- The typeset item's params (verb, exit, material) in `get_edl`.
- Rendered frames at the onset (render_preview, look_at rendered) for a
  resolve or a wipe.

## Strong treatment patterns

- Blur resolve on the hook's hero word, cut on its kicker.
- Duration wipe on a long, held word ("forever").
- Arc round the head on the name of the person speaking.
- Decay exit on the last line before the loop.

## Common failure modes

- Every word on the same verb: the motion reads as a template.
- A verb that resolves before the word is heard (it must land on the
  onset).
- Path text crossing the face: the solver keeps clearance; re-solve with
  another layout instead of forcing it.

## Verification procedure

- On rendered frames: the blur is sharp on the onset frame, a wipe is at
  half on the word's midpoint, no glyph crosses the face.
- The motion audit (audit_motion) reports no landing error over a frame.

## Repair ladder

1. Too much motion: verb cut for the content words, keep one hero verb.
2. A verb that fights the voice: set_typeset with the voice's own verb
   (verb auto).
3. Arc text cramped: layout stack or side_column instead.

## Through apply_edit_batch

Verbs, exits and materials are params of the `typeset` motion item
(`verb`, `exit`, `material`, `orbit`, `flip_at`): set them with
`apply_edit_batch` (motion upsert) or `set_typeset`; nothing else renders
them.
