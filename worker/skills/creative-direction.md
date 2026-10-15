# creative-direction — invention first: a plan bound to THIS story's beats (hook, turn, payoff, callback, loop), genre packs as a starting point (A manifesto, B trend desk, C typographic portrait, H lyric film), "make it like this" from a pasted reel, type voices to choose from, and B&W only as a device

## Editorial decision principles

The bar is range and invention per story, never a house style. A device
that impressed once (lamp-lit captions, a magazine lockup) is an example
of the standard, not a recipe to repeat: the next short earns its own.

- Start from the story: what is the hook line, where does it turn, which
  word pays it off, does the speaker repeat himself (a callback), can it
  loop? `suggest_story` answers with at most eight proposals bound to
  those beats, each saying what is NEW about it for this story. Two
  different shorts never receive the same plan (it is seeded by the words)
  and what this project already used is ranked down.
- One or two moves a sentence at most; a bridge sentence gets nothing.
- Packs (`apply_format_pack`) bring a genre's structure, palette, voices,
  punctuation, junctions and colour world. The user's words always win: a
  pack they name beats any measurement; no words and no confident family
  is no pack (compose freely).
- "Make it like this": `analyze_reference` reads the pasted reel's device
  classes in order (negatives, flashes, B&W with a kept colour, strobes,
  type on black, fast cuts) and maps them onto this edit's moments. Match
  the grammar, never the marks: no creator text, logo, handle, watermark
  or footage is copied. A strobing reel is the user's flash opt-in only
  because they asked to match it.
- Type voices: offer a choice when the user asks (`render_variants`:
  three voices on the real frame in seconds), and never set two
  consecutive lockups in one voice unless the second is a callback.
- B&W is a DEVICE: a stretch that opens to colour on the payoff, one
  colour kept, a silhouette hold, a negative on a hit. Never the whole
  short unless the user asks for black and white (a write that turns
  the whole short flat B&W is refused without that ask; `mono_noir` is a
  noir COLOUR look, not a grey one).
- Never music the user did not supply, name or pick; zooms and sounds are
  optional, never defaults; the end card and the watermark are untouched.

## Evidence to inspect

- `suggest_story` (READ): the beats, the devices, the invention lines.
- `get_edl` after `apply_format_pack`: the batch it wrote; the next calls
  it listed (montage, drop on the payoff, loop) are made separately.
- `analyze_reference` before claiming "like this": the classes in order.

## Strong treatment patterns

- Manifesto (A): words on black -> words over picture -> picture, the bed
  out before the payoff, colour returning on the payoff word, a loop.
- Trend desk (B): a stutter intro under slot type, the low-pass opening on
  the drop, macros cut on the kick one frame early, three voices.
- Typographic portrait (C): the hook as a lockup around the head, a side
  column for the quote, the payoff behind the head, the callback rhyme.
- Lyric film (H): syllable-split lyrics on the singer's timing, the hook
  line widening into a footage window on the drop (needs the user's track
  and lyrics).

## Common failure modes

- The same lockup voice on every short: the planner rotates voices and
  says which are new; take the hint.
- A pack applied over the user's words: the words win.
- Copying a reference's look wholesale (its fonts, its handle, its text):
  transfer the device classes only.
- A whole short in B&W because it looked serious: refuse yourself; use a
  B&W stretch that resolves into colour on the turn.

## Verification procedure

- After applying: the ledger line (no sound, zoom or music added by the
  plan), the voices used (no repeats except callbacks), every device on
  its beat (proof_device or a preview at each landing).
- For "like this": the device classes of the result in the same order as
  the reference's.

## Repair ladder

1. Too busy: drop to intensity calm (fewer lockups, one punctuation).
2. Wrong genre: name the pack, or pack none.
3. A voice that does not suit the line: render_variants, then
   set_typeset with the pick.
4. Reference classes missing in our footage (a strobe without stills, a
   type-on-black without words): say which, and offer the closest device.

## Through apply_edit_batch

Every proposal carries the exact apply_edit_batch operation (typeset
motion items, grade_window / accent fx items) or the verb call that solves
it; `apply_format_pack` commits through the same batch path an MCP client
uses, so a stale client reaches every device the plan writes.
