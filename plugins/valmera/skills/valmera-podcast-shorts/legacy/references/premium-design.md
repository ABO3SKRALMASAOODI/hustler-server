# Current premium design direction

## Latest owner correction: editorial craft, not toy diagrams

The October 5 island/$1T/soup sample was rejected. Its stick figure, cylinder
can, concentric island ovals and little labels resembled an old game. Treat
that render as a negative example, even though it passed technical checks.
Its restraint and native editability did not make its artwork good.

Do not substitute primitive illustrations for the authored visual language
of the references. Use real footage, well-composed photographic imagery or
properly crafted artwork when an object earns a place. Acquire assets when
needed; an efficiency target does not mean forbidding asset research and
falling back to circles. Research related moments together, inspect a few
strong candidates and use the best specific image. Keep provenance. For a
hypothetical analogy, do not pass illustrative imagery off as a real event.
Availability of generation depends on the deployment; inspect the live tools.

The repair is the whole composition: scale, crop, white space, type and the
handoff from performance to image to payoff. Do not merely put a photograph
of a can into the old diagram. Do not respond by making every short a static
headline either. The designed lane still earns its visual progression;
the headline lane still earns stillness. One image and one clear thought
usually read better than an interview thumbnail above a dashboard of labels.

Prefer the verified speaker's name at the START of a headline, in the same
reading sequence: “Elon Musk: What is money actually worth?” is an example,
not reusable identity or copy. Reference `0568dc` shows this relationship.
Use the actual source/user identity; if uncertain, omit attribution. Keep
the claim faithful, the name quietly distinguished, and separate captions.
`set_editorial_graphic(kind="headline", speaker=…, text=…, box=…)` handles
native measured wrapping without shrinking type or adding a panel. Other
lanes may use a speaker-first opening title without a persistent headline.

Before an expensive full render, inspect one actual composed opening and
one dominant design beat at phone size beside the relevant reference. Judge
the image craft and hierarchy, not whether all planned objects are present.
Then inspect their movement and story handoff in the candidate. Reject tiny
faces, decorative icon diagrams, stock wallpaper and text-heavy dashboards.
No numeric self-score, clean decode or render receipt overrides weak pixels.
This early check replaces wasted polishing; it is not a new multi-stage ritual.

Also read [the eleven additional reels](additional-reels.md) for the latest
fixed-slot phrase builds, selective serif contrast, depth type and evidence
gallery observations. They supplement the following seventeen references.

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
  A card entrance runs on every new card window. During continuing speech,
  inspect the handoff between adjacent windows: use `entrance="none"` when a
  direct scale/framing change should preserve the face. Fade/lift deliberately
  fades the new picture in; it is not a seamless shape morph.
- `set_editorial_graphic(id,kind,text,start,end,secondary,eyebrow,...)` composes
  a statement/comparison/metric/quote/chapter/label in one revision. The output
  is editable native text and vectors. Stable ids replace groups. Hold long
  enough to read; shorten copy instead of turning it into fine print.
  Choose `treatment="type"` when typography should sit directly in the scene;
  boxed panels are optional. Do not fill a short with generic black slides.
  Dialogue captions remain by default. Use `mute_captions=true` only when the
  graphic actually replaces the spoken words, not for a complementary label.
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
reveal captions. Still inspect the actual words, sizes and timing.
Karaoke fade now eases tint instead of hiding each already-visible word.
For designed typography outside ordinary subtitles, `set_typography_scene`
reveals measured mixed-font runs without moving previous words. It creates
editable text, supports atomic recipes and owns only its live caption mutes.
`kinetic`, `quiet` and `editorial` remain available. Choose a recipe then inspect
the actual phrases; no helper can choose a story or certify taste. Prefer live
`set_editorial_graphic` for measured panels over the earlier loose local card
helper. Use local primitives only when they help author a deliberate custom idea.

Review the actual opening, the strongest design beat and the payoff at phone
width. Identify the weakest moment and repair a concrete weakness before
adding decoration. Never claim exceptional autonomous performance from a
single hand-directed showcase or a clean technical render.

A rounded picture plus captions is a foundation, not a finished art direction
for the designed lanes. The fast lane still needs a visible sequence of
idea/evidence/reaction; the headline lane earns stillness through its story.
If the selected passage spends most of its time in setup, find a self-contained
start near the answer or pick a better passage. Faithful visual context may
replace routine interviewer setup; it must not replace an essential spoken
qualification. Never use typography to camouflage weak pacing.
