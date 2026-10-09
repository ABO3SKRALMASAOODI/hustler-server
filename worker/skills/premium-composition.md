# premium-composition — editorial type, rounded footage cards and purposeful motion systems

## Editorial decision principles

For a designed social edit, give the viewer one clear visual idea at a time.
Choose a picture treatment, a typography relationship and the meaningful beat
where each changes. Premium is hierarchy, spacing, good footage and controlled
timing; a palette or effect count cannot supply those decisions.

## Art direction before geometry

A polished render of crude artwork is still crude artwork. Do not construct
people, food, islands, buildings or products from circles/rectangles as a
shortcut for an advanced editorial brief. Keep primitives for their real
strengths: alignment, rules, measured data, masks and useful annotations.
An intentionally illustrated brief is different; its artwork still needs
coherent material, shape language and craft, not stock presentation icons.

For a visual explanation, decide what the viewer should SEE, not which tool
to exercise. Inspect real source imagery, then acquire a specific asset when
it improves the argument. Use a real object/shot at a strong scale, deliberate
cropping, an intelligible relationship to the words, and one controlled motion
handoff. A photographic object pasted into the same crowded diagram is not a
repair. Neither is a sequence of generic stock images. Conceptual imagery must
be distinguishable from evidence of a real event; don't invent an account,
endorsement, identity or statistic.

At phone width the picture, claim and dialogue cannot all compete equally.
Give the face or meaningful image enough area to matter. Prefer one readable
claim over a mesh of tiny labels. Show the actual opening and strongest scene
beside the chosen reference at the SAME display size. Judge image craft,
hierarchy and motion continuity before checking technical polish. If the
scene resembles a slide deck or simple game illustration, rebuild that scene;
changing its palette or easing curve does not address the weakness.

## Evidence to inspect

Inspect the opening, actual source framing, complete speech arc, reference
movement, densest caption, turn and payoff. Transfer a reference's relationships,
not another creator's watermark or an unrelated visual gimmick. Preserve the
user's requested mood, source aspect, music and identity.

## Strong treatment patterns

- **Speaker-first headline:** when the brief calls for attribution and the
  speaker is verified from the source/user, use
  `set_editorial_graphic(kind="headline", speaker="…", text="…")`.
  The name leads the sentence, with quiet color distinction; the claim wraps
  at a readable size in its box, holds still and leaves dialogue enabled.
  Place it just above the actual picture. `font_size` defaults to .052 of the
  short canvas edge; don't make the name an unrelated extra badge. Shorten
  crowded copy instead of shrinking it. State a faithful, specific idea;
  a paraphrase is not a verbatim quotation. Never infer identity from the
  stylistic reference. Unknown speakers get an unattributed title instead.

- **A phrase built in place:** `set_typography_scene` measures the complete
  phrase before revealing any part of it. Give deliberate `lines`, each with
  `runs` containing exact text and its real PROGRAM `at` cue. Runs stay in
  fixed slots until the scene end. Pair one sans family with a sparse
  Instrument Serif italic word when that contrast distinguishes setup from
  consequence; don't alternate fonts mechanically. `align=left/right` places
  the group in useful negative space beside a speaker. `reveal=build` reveals
  runs at speech cues; `still` displays the full statement. A run may contain
  several words. `font_size` is relative to the canvas short edge; line `size`
  and run `scale` control hierarchy. Overflow is rejected: break or shorten
  the line instead of making it tiny. Stable id replaces the group; removal
  restores its owned caption windows. Use `mute_captions=false` for separate
  topic labels. Use the exposed native setters serially for one project and
  check each saved receipt. The internal recipe helper is not a public tool.
- **Picture as an object:** `set_frame(picture=[...])` preserves wide footage
  on a portrait canvas. `set_picture_card` then gives that footage its own
  rounded box, hairline border, quiet shadow and a lift/reveal opening or
  closing. Captions and designed words remain separate and full-resolution.
  Keep the face large. A card need not occupy the whole video; use it at an
  evidence shot or story transition, with stillness during sustained dialogue.
  Inspect source focus before changing the destination box. `fit=pad` preserves
  the whole picture; `crop` fills and may discard important context.
  Never leave the canvas around a card as a flat void: `background_style=
  "blur"` fills it with the card's own footage blurred and darkened (the
  premium podcast look), `radial_gradient`/`vertical_gradient` give a graded
  field; add `grain` ~.25 and `vignette` ~.4 for texture.
- **A visible argument:** use `set_editorial_graphic(kind="comparison")` for
  two actual opposing ideas, `metric` for a supported figure plus its meaning,
  `statement` for a specific thesis, `quote` for a faithful short quotation,
  or `chapter` for an actual turn. Give the real words and cue time. The tool
  produces editable, measured type/vector layers in one revision; a stable
  id revises the group. It rejects unreadably short holds and overcrowding.
  Choose `ink`, `paper`, or `slate` to match the edit. No compulsory cards.
  `treatment="type"` removes the panel so the words can share a deliberate
  composition with the picture. Use a panel only when the separation helps;
  a large empty rectangle around small words is not an advanced design.
- **A useful label:** `kind="label"` identifies a real person, place, machine
  or piece of evidence. Its caption coexistence is deliberate. Place its box
  in measured clear space, not across a face or the existing dialogue band.
- **Meaning-led type:** quiet phrases carry ordinary speech; a consequential
  word, number or contrast gets larger type. Native captions offer phrase
  reveal, semantic `emphasis_words`, active-word color and separate animation.
  For restrained work use fade/rise or still phrases and modest scale contrast.
  Elastic/bounce, glow and rotating colors are appropriate only when the
  reference/brief earns them. Graphics preserve captions by default. Set
  `mute_captions=true` only when a graphic replaces the spoken words; a
  complementary comparison or label should not erase the dialogue subtitles.
  For whole readable phrases with spoken-word tint, use `preset="composed"`.
  In karaoke mode `animation="fade"` eases tint without hiding already visible
  words; `none` makes the tint immediate. Inspect actual word boundaries.
  It shows the phrase together, keeping connectors in context. Use reveal
  when accumulation itself is the intended motion, not as a universal default.
- **Movement with a landing:** text and vectors share local keyframes. A small
  eased translation and opacity change can resolve together in .25–.5s, then
  hold. Use one leader. Reserve an abrupt scale/cut for an actual reversal;
  don't make the camera, caption, panel and arrow all bounce independently.
- **Evidence sequences:** show the actual object/action being discussed, cut
  on its useful movement, then return to the reaction or insight. One strong
  relevant shot beats several generic search results. Use overlays for silent
  evidence over continuing speech; inserts change program timing. Source
  material and cards are distinct tools; do not substitute generic slides
  for an unavailable visual fact.

`set_editorial_graphic` is a starting composition, not the full language. Its
ordinary text/vector layers remain individually editable. `font_size` on a
text item is a fraction of the canvas short side; `max_width` defines its text
column. Use them with x/y and general motion for custom hierarchy. Do not
rasterize captions/labels into low-resolution pictures. `set_screen_frame`
still serves whole-window app demos; it shrinks the finished video including
its text and is a different operation from a footage-only card.

## Common failure modes

Tiny explanatory subtitles under generic PROGRESS/FUTURE headings; the same
panel on every noun; repeated noisy word effects; disconnected animations;
rounded cards so small the person disappears; hiding a gesture to show stock;
flat speech with no chosen focal moment despite a designed-edit brief.
An entrance replayed at every cue; words re-centering as the phrase builds;
changing typeface or position merely because another word arrived.
Behind-subject type requires `add_text_behind` (or a motion graphic placed
by `add_motion_graphic` on `layer="behind_subject"`) and a verified real matte.
Perspective galleries/3D cylinders are not supplied by a typography scene.

## Verification procedure

Render and inspect the first visible state, opening midpoint, settled state,
closing midpoint and the next shot. At phone width read the longest phrase
without zooming. Check one dominant element, actual word/graphic timing,
face clearance, stable readable caption position and enough settled reading
time. Review real audio for joins. Describe one weak moment honestly; a clean
decode or a favorable self-score does not certify compelling design.

## Repair ladder

Improve the chosen moment/hook first → remove generic copy → strengthen the
picture/type relationship → unify motion and palette → enlarge or simplify
unreadable information → inspect the revised sequence and its boundaries.
