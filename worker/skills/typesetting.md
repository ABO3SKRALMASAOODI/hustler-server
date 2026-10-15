# typesetting — poster-scale type solved for the frame: type voices, layout families (magazine lockups, staircases, slots, constellations, side columns, quote windows, arcs round the head), every word landing on its spoken onset

## Editorial decision principles

Poster type is the voice made visible: the speaker's line becomes the
picture. It earns its place on a HOOK, a TURN of the argument or a PAYOFF —
two to four moments in a 30–45 s short — never on a dense explanation (the
captions carry those; a paragraph of poster type is unreadable).

- **One voice per beat, never a house style.** A voice is a typographic
  personality, not a brand: `add_typeset` with `voice: "auto"` picks one
  from the phrase itself (a figure, a negation, an emotion, an aside, a
  long reflective line, a one-word blow) and the story position, and it
  never repeats the previous typeset's voice unless the line is a callback.
  Surprise the viewer with a voice that fits THIS sentence; do not reach for
  the one that worked last time.
- **It replaces the captions** for the words it shows (every heard word
  still reaches the screen once). Never set type that duplicates a caption.
- **The face is sacred.** The solver keeps the face zone clear in front and
  the eyes-to-mouth core clear even behind the subject; it never shrinks a
  hero below legibility to squeeze in — it changes family or reports why.
- **Depth is a choice.** `depth: "behind"` sets the lockup behind the
  subject with ghost outlines where letters hide; on a close-up with no
  headroom the solver sets it in front and says so.
- **Silent and still.** Typeset adds no sound and no zoom, ever.

Voices (each fits a different beat): `magazine` (payoff behind the head),
`whisper_shout` (a turn: small connectors, one shouted word),
`staircase_red` (a build that climbs), `slot_blackletter` (attitude, a
drop), `neon_script` (a feeling, a night), `editorial_serif` (a reflective
sentence beside the speaker), `quote_window` (one word at a time on a held
shot), `condensed_poster` (one defiant word), `lamp_karaoke` (the line waits
dim and ignites as it is said), `hairline_wide` (one enormous quiet word),
`marker_note` (an aside, the editor talking back), `swiss_grid` (a figure
as a data sheet), `techno_crt` (machines, code, archives).

## Evidence to inspect

- The words and their onsets: `get_words` / `get_kept_transcript` on the
  window; the payoff, the negation or the figure is the hero.
- The frame: `look_at` the window — headroom, negative space beside the
  head, a close-up that leaves only the chest band, a bright wall.
- What the edit already uses: `get_edl` (motion items with template
  `typeset`, their `voice`) — the variety rule reads it too.
- The reply of `add_typeset`: voice and why, the family solved, payload cap
  height as a share of the frame, depth (and any front fallback).

## Strong treatment patterns

- Hook (first 3 s): a short, bold voice — `condensed_poster`,
  `whisper_shout` or `slot_blackletter` — on the claim itself.
- A number: `swiss_grid` or `staircase_red`; the figure is the hero rung.
- A negation or contrast: `whisper_shout` with the negation as the accent.
- A reflective or long line: `editorial_serif` side column (layout
  `side_column`), pauses left empty.
- A repeated line (a callback): `add_typeset` with `callback_of` the first
  one's id — the same voice and geometry (or `mirror: true`).
- A word that should feel spoken: verbs `lamp` (ignites on the onset),
  `duration_wipe` (wipes across the spoken duration), `blur_resolve`
  (sharp exactly on the onset), `scramble_lock` (machines), `typewriter`
  (an aside), `echo_stack` (insistence), `strobe_flip` (a drop).
- A colour flip on a hit: `flip_at` (item seconds) swaps ink and accent.
- Words round the head: layout `arc` (and `orbit` to make them circle).

## Common failure modes

- Poster type over a dense explanation: the viewer reads nothing. Keep it
  to the line that matters (at most 24 words; usually 3–9).
- The same voice three times in a row: the edit feels templated. Let
  `voice: "auto"` vary it, or choose deliberately.
- Behind-the-subject type on a tight close-up: the head hides it; accept
  the solver's front fallback or reframe with headroom first.
- Type that fights a graphic: two lockups up at once compete — one at a
  time.

## Verification procedure

- Render a preview and `look_at` the hero's onset frame and the last frame:
  every word legible, nothing on the eyes or mouth, nothing in the corner
  mark's zone or the platform's button rail.
- `audit_captions`: the typeset's words are not also captioned; the
  phrase's other words are.
- `proof_device` on the item: landing within one frame of each onset.

## Repair ladder

1. Words too small or crowded: fewer words (set the payoff, not the
   sentence), or another layout (`side_column`, `stack`, `spread`).
2. Wrong mood: `set_typeset` voice to one that fits the beat.
3. Collides with the face or a graphic: `region` (above/below/left/right),
   or `depth: "front"`.
4. Still wrong: `remove_typeset`; the captions carry the line.

## Through apply_edit_batch

A typeset item is a motion item with template `typeset`; the geometry is
solved by the render for every canvas, so only the intent is written.
`get_edl` shows it under `motion`.

    {"action": "upsert", "layer": "motion", "id": "ts1", "value": {
     "id": "ts1", "template": "typeset", "start": 12.4, "end": 16.2,
     "layer": "above_captions",
     "params": {"text": "AI may be *bigger* than the internet",
                "voice": "whisper_shout", "layout": "auto",
                "depth": "front", "verb": "auto"}}}

Behind the subject: `"layer": "behind_subject"` and `"depth": "behind"`.
Remove: `{"action": "remove", "layer": "motion", "id": "ts1"}`.
