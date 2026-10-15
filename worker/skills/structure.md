# structure — recall over the footage and the shape of the story: the same framing on another day, faces on one eye line, circles chained across cuts, the freeze poster, the loop that closes on the first frame, callbacks, density arcs, and virtual shots cut from what is already there

## Editorial decision principles

A short is remembered for a SHAPE: a rhyme, a return, a sequence that
accelerates. These verbs find those shapes in the user's own footage; they
never invent footage and never pick music.

- `find_shots` recalls by example (`like` a program second or
  `key@seconds`: the same framing on another day), by light (night /
  day), by shape (circles), by face size, or by the storyboard's words.
  Descriptors are model-free and cached per clip; semantic text recall
  waits for the vision lane, so a word query reads the storyboard only.
- `suggest_match_cuts` builds a rhyming sequence and, with `write=true`,
  splices it as cropped, muted inserts: `face_aligned` conforms faces onto
  ONE eye line and ONE size (the accelerating face montage; level, frontal
  faces only), `shape_chain` lines circles up centre to centre (three true
  circles or nothing: two are a coincidence), `same_framing` cuts one
  framing across days.
- `pick_still` finds the freeze frame for a poster: sharp in its window,
  the lockup box clear and off the face. Freeze it there, then set the
  poster type (add_typeset) in that box.
- `plan_loop` closes the program on a shot that rhymes with its first
  frame, always BEFORE the end card (the end card and the watermark are
  untouchable and come after every creative stage).
- Callbacks: when the speaker repeats a phrase, the second telling is set
  as the first one's callback (add_typeset callback_of): same geometry.
- Invention over habit: a face line, a circle chain and a loop are three
  different rhymes; do not reach for the same one in every short.

## Evidence to inspect

- `find_shots` before promising a match: what is actually in the footage.
- `suggest_match_cuts` without write: the shots, their crops, the timing.
- `get_edl`: the inserts it wrote (`mc*`, `loop`) and the program length.

## Strong treatment patterns

- Face line on a list of people the story names: each face on one eye
  line, 0.6 s down to 0.24 s.
- Freeze poster at the hook: pick_still in the hook's window, freeze, set
  the hook as poster type in the clear box.
- Loop: the last beat returns to the opening framing (plan_loop), so the
  feed replays seamlessly.

## Common failure modes

- Forcing a chain from two shapes: shape_chain refuses below three.
- A conform that zooms a small face into mush: faces whose crop would be
  softer than 300 source pixels are left out.
- A loop that lands after the end card: impossible here; say so if asked.

## Verification procedure

- Preview the sequence: eyes on one line (within 1.5% of the height),
  circles centre to centre, no letterbox bars.
- pick_still's numbers: its percentile in the window, box clutter, face
  overlap 0.
- After plan_loop: the first frame and the last frame before the end card
  read as the same picture.

## Repair ladder

1. Too few faces or circles: widen the pool (find_footage, uploads) or
   drop to a two-shot rhyme by hand.
2. A face sits off the line: remove that insert; the rest stay aligned.
3. No rhyme for the loop: end on a hold of the opening shot's framing, or
   leave the ending as it is.

## Through apply_edit_batch

Every result is ordinary inserts (`crop`, `mute`, `source_start_s`) or a
motion item: movable and removable with `apply_edit_batch` on the
`inserts` / `motion` layers. Nothing new renders.
