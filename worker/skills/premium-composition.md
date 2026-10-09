# premium-composition — art direction, layouts, typography systems, footage cards on designed backgrounds, legibility

## Editorial decision principles

A premium social edit has committed art direction: one look, one type
system with a strong hierarchy, a frame that is designed edge to edge, and
one clear visual idea at a time that changes on the beat where the meaning
changes. Premium is hierarchy, scale, placement, good footage and precise
timing — plus motion and sound bound to the words. A small picture floating
on a flat black canvas with static text is a clip page, not a premium
composition.

- Layout: face-aware full-bleed, or the picture as a card on a DESIGNED
  background — never a flat black void.
- Typography: a tight bold grotesk backbone plus one accent role (serif
  italic, script or condensed heavy), near-white type with one accent colour
  on 1–2 words per sentence, size ladders of 2:1 to 7:1.
- Legibility from size, placement in clear space and soft shadows or plates,
  not outlines and boxes.
- One leader per moment; everything else supports it.

## Art direction before geometry

A polished render of crude artwork is still crude artwork. Do not construct
people, food, buildings or products from circles and rectangles as a
shortcut. Keep primitives for their real strengths: alignment, rules,
measured data, masks, arrows and annotations. Use real imagery, UI mockups
from the motion library, or photographic cards for objects.

For a visual explanation, decide what the viewer should SEE, then choose the
asset or template that shows it at a strong scale with one controlled
motion handoff. Conceptual imagery must stay distinguishable from evidence of
a real event; never invent an account, endorsement, identity or statistic.

At phone width the picture, the claim and the dialogue cannot all compete
equally. Give the face or meaningful image enough area to matter, and one
readable claim instead of a mesh of tiny labels. Compare the opening and
strongest scene beside the reference at the same display size.

## Evidence to inspect

Inspect the source framing and face positions per shot, clear space beside
and above the face, the complete speech arc, the reference's layout and type
hierarchy, the densest caption state, the turn and the payoff. Transfer a
reference's relationships, never another creator's watermark, logo or
wording. Preserve the user's requested mood, aspect, music and identity.

## Strong treatment patterns

LAYOUTS FOR 9:16:
- **Face-aware full-bleed** (the default for modern 16:9 podcasts and
  talking heads): `auto_reframe` or `set_frame` crop with a shot-aware focus
  track so each speaker's face sits in the upper-middle with the face around
  28–40% of the frame height; type lives in the clear space above, beside or
  below the face. Alternate tight and medium framings with eased zooms on
  sentence turns (read zooms).
- **Card on a designed background** (archival 4:3, wide shots that a crop
  would destroy, two-shot frames): `set_frame(picture=[...])` keeps the wide
  original inside the portrait canvas, `set_picture_card` gives it rounded
  corners, a hairline border, a soft shadow and a lift or reveal entrance,
  and its `background` is designed — `blur` (a blurred, darkened copy of the
  picture behind the card), a gradient, grain and vignette — read the
  `set_picture_card` schema for the exact fields. Make the card large — a
  4:3 picture trimmed toward 1:1 or 4:5 (crop mode with focus, when the
  sides hold nothing essential) fills 55–70% of the height — and use the
  remaining space for hero type.
- **Two speakers**: shot-aware reframing that cuts to the active speaker, or
  a stacked layout when both reactions matter.
- Never leave a fixed band of the canvas empty for the whole reel.

TYPE SYSTEM — one per video:
- Captions are the frequent tier (motion looks; read captions). Hero words
  and numbers are the big tier (7–20% of frame height, motion templates;
  read motion-design). The accent tier is one role — serif italic, script or
  condensed heavy — on a few words.
- Pre-lay the block: a lockup's full layout is computed before any word
  reveals, so words appear IN PLACE and the block never reflows. Stacked
  lockups with tight leading (0.85–0.95) may overlap deliberately.
- Tracking tight on bold sans (−2 to −5%), generous size, mixed case unless
  the look calls for caps.

LEGIBILITY:
- Size first; then placement in measured face-free negative space; then a
  soft shadow (0 2–6 px 12–30 px at 35–60% black), a frosted or dark plate,
  or the grade.
- Safe area on 9:16: important type inside x 60–1020 px and y 8–80% of the
  height; never in the bottom platform band or the right button rail (x >
  0.88 between y 0.5 and 0.85); never across eyes or mouth. Motion graphics
  enforce this on write (the face keep-out moves them and says where; see
  motion-design), so plan zones per beat and read where each one landed.
- Check every type state on the actual background in the render, bright
  and dark plates alike. Motion graphics and motion captions firm up their
  own backing (or switch to dark ink) over a measured bright plate, and keep
  secondary text at a cap height of at least 2.2% of the frame height.

NATIVE TOOLS THAT REMAIN USEFUL:
- **Speaker-first headline**: `set_editorial_graphic(kind="headline",
  speaker="…", text="…")` for a verified speaker and a faithful claim. It
  holds still and keeps dialogue captions. On a reel it should pose the
  question, never the payoff, and sit inside the designed layout rather than
  above a small card on black.
- **A phrase built in place**: `set_typography_scene` measures the complete
  phrase first, then reveals runs at real PROGRAM `at` cues without moving
  previous words. Give deliberate `lines` with `runs`; pair one sans family
  with a sparse Instrument Serif italic word; `align` places the group beside
  a speaker. Overflow is rejected — shorten instead of shrinking.
- **A visible argument**: `set_editorial_graphic` kinds comparison, metric,
  statement, quote, chapter and label produce editable measured type/vector
  layers; `treatment="type"` removes the panel. For animated equivalents
  use the motion library (versus_split, stat_card, quote_card,
  chapter_title, lower_third).
- **Words behind the subject**: `add_text_behind` (person matte) or a motion
  graphic with `layer='behind_subject'`; the type must be LARGE so the
  person crosses the middle of tall glyphs.
- Graphics preserve captions by default; mute captions only when the graphic
  says the spoken words.

MOVEMENT WITH A LANDING: text, cards and vectors enter with short graphic
motion (pop, rise-blur, mask, spring for objects), settle and hold. Use one
leader; don't make the camera, caption, panel and arrow all move
independently. Exits are faster than entrances.

EVIDENCE SEQUENCES: show the actual object or action being discussed as
B-roll or a photo card, cut on its useful movement, then return to the
reaction. One strong relevant shot beats several generic search results. Use
overlays for silent evidence over continuing speech; inserts change program
timing.

## Common failure modes

Small card on a black void; static headline bars that never change; tiny
captions shrunk to avoid collisions; generic PROGRESS/FUTURE headings; the
same panel on every noun; rounded cards so small the face disappears; two or
three unrelated type systems; words re-centering as a phrase builds; an
entrance replayed at every cue; type across the face or in the UI band;
crude primitive illustrations in place of real imagery.

## Verification procedure

Render and inspect at phone size the first visible state, every layout
change, a dense caption moment, each hero moment and the payoff: no flat
black void, face large and composed, one dominant element per moment, type
readable on its actual background, nothing in the UI band, words revealing
in place. Describe one weak moment honestly; a clean decode does not certify
compelling design.

## Repair ladder

Strengthen the hook and hero moments → fix the layout (full-bleed or designed
background) → unify the type system and accent → enlarge or simplify
unreadable information → move type into clear space → reduce to one leader
per moment → inspect the revised sequence and its boundaries.
