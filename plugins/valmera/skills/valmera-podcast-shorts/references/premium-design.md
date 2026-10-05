# Current premium design direction

The owner supplied 17 reference reels in
`/Users/masaoodi/Downloads/Valmera Instagram Reference Reels/` on October 5.
Use the folder as viewable evidence, not material to copy into exports.
The inventory and sampled/motion boards are saved under the Valmera project's
`audits/2026-10-05/premium-editor/`. A coordinator picks relevant evidence once
and passes a compact reference packet to editors; don't re-analyze all reels
for every child. New run taste profiles must reflect this direction.

Useful observed relationships (inspect the actual clip/window):

- **Creativity is Dead**, 12–14.7s: rounded footage changes between compact
  square and taller frames; each contains a dominant meaning-bearing word.
  Transfer deliberate picture scale and hierarchy, not its entire effect mix.
- **I had no idea...deep level**, 41–44.8s: small quiet speech gives way to
  large serif name typography, then a small rounded evidence clip on black.
  Stillness versus emphasis is the contrast; not every word moves.
- **Steal Viral Background Songs**, 6–8.4s: one rounded asset tile, concise
  metadata and a supporting value column form a coherent information unit.
  Never invent a view count or imply those results belong to our edit.
- **Sell the emotion**, 2.1–4.2s: a specific product image hands off to one
  large idea, then a contrasting phrase. Copy serves the argument.
- **Thought I'd try this trend**, 1.7–3.8s: phrase accumulation with selected
  expressive typography; the background is steady enough for reading.
- **after much demand...aftereffects**, 0–3.1s: a unified monochrome scene
  with coordinated elements and a camera transition. It is a larger authored
  sequence, not permission to sprinkle unrelated icons around the speaker.

The current target is premium, modern and deliberate. White/ivory type, one
muted accent, clean edges and eased settle/hold movements are useful starting
points. Bright colors, glow and bounce need a specific reason from the brief.
Do not confuse restraint with a flat batch: every designed format still needs
an actual focal moment, specific visual evidence and a purposeful progression.

## Native tools shared with ordinary Valmera users

Read Valmera's `premium-composition` skill and the live tool schemas once.

- `set_picture_card(id,start,end,box,...)` shapes only the footage. It supports
  rounded edges, a fine border, shadow, and lift/reveal openings/closings while
  captions/branding retain their independent resolution and placement. Choose
  frame.picture first to preserve the source composition. Inspect actual crop.
- `set_editorial_graphic(id,kind,text,start,end,secondary,eyebrow,...)` composes
  a statement/comparison/metric/quote/chapter/label in one revision. The output
  is editable native text and vectors. Stable ids replace groups. Hold long
  enough to read; shorten copy instead of turning it into fine print.
  Choose `treatment="type"` when typography should sit directly in the scene;
  boxed panels are optional. Do not fill a short with generic black slides.
- Use custom text/vector motion for an idea the supplied compositions don't
  express. `font_size` is relative to the short canvas edge; `max_width` sets
  the text column. A graphic is judged by what it explains, not its API name.

Keep the four format definitions, no-added-music preference, 5s native ending,
original-source sharing and Sol coordinator/editor model. The headline format
still has a persistent topic above the picture and no interrupting cards.
Rounded picture treatment is allowed; do not hide the headline behind it.

`compose_short.py` now starts with the restrained `composed` caption recipe.
It presents a complete phrase with spoken-word tint and semantic size emphasis,
without the isolated connector states or one-word-per-line default of stacked
reveal captions. That default addresses a failure seen in the independent
October 5 tests; still inspect the actual words, sizes and timing.
`kinetic`, `quiet` and `editorial` remain available. Choose a recipe then inspect
the actual phrases; no helper can choose a story or certify taste. Prefer live
`set_editorial_graphic` for measured panels over the earlier loose local card
helper. Use local primitives only when they help author a deliberate custom idea.

Review the actual opening, the strongest design beat and the payoff at phone
width. Identify the weakest moment and repair a concrete weakness before
adding decoration. Never claim exceptional autonomous performance from a
single hand-directed showcase or a clean technical render.

The October 5 trials were readable but too conservative to certify exceptional
editing. A rounded picture plus captions is a foundation, not a finished art
direction for the designed lanes. The fast lane still needs a visible sequence
of idea/evidence/reaction; the headline lane earns stillness through its story.
If the selected passage spends most of its time in setup, pick a better passage
before using typography to camouflage weak pacing.
