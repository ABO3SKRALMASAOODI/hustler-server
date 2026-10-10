# premium-composition — art direction, layouts, typography systems, footage cards on designed backgrounds, legibility

## Editorial decision principles

A premium social edit has committed art direction: one look, one type
system with a strong hierarchy, a frame that is designed edge to edge, and
one clear visual idea at a time that changes on the beat where the meaning
changes. Premium is hierarchy, scale, placement, good footage and precise
timing — plus motion bound to the words (a zoom or a sound only where a
moment earns one: they are optional, never rules). A small picture floating
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
  talking heads when the plate is clean; a busy or washed-out plate — a
  bright projector screen, a cluttered set — reads better as a card on a
  textured canvas or a letterbox with a headline band, with a committed
  grade, a taste call per short): `auto_reframe` or `set_frame` crop with a shot-aware focus
  track so each speaker's face sits in the upper-middle with the face around
  28–40% of the frame height; type lives in the clear space above, beside or
  below the face. `auto_reframe` also follows a speaker who leans or steps
  inside a shot (still while they sway, a speed-limited glide only when a
  still crop would cut the head, never across a cut). A zoom is optional: use one only where a moment clearly
  earns it (read zooms) — a steady, well-framed picture is the default.
- **Card on a designed background** (archival 4:3, wide shots that a crop
  would destroy, two-shot frames): `set_picture_card` takes the card's
  footage straight from the full SOURCE frame — never a re-crop of the 9:16
  crop — enlarged once and at most 2x (up to 3x only to make a small
  archival face readable). `source='auto'` frames the speaker
  from a measured face track with headroom above the head and follows a
  speaker who moves inside a shot (still while they sway, a smooth glide
  only when a still card would cut the head, never across a cut — each
  shot gets its own framing); a source below
  720p is shown whole (a 4:3 talk becomes a full-width 4:3 card, ~1.6x,
  instead of a 3.7x crop that cuts the head). Give it rounded corners, a
  hairline border, a soft shadow and an entrance that dissolves the card in
  from the full-frame shot (fade/lift; on a cut, 'none'). Leave the
  canvas to the default — a dark tone sampled from the footage glowing to
  near-black, vignette, and film grain on low-resolution footage — or pick
  a gradient; a blurred copy of the picture (`blur`) only behind sharp HD
  footage, never behind archival or sub-720p video, where it reads as a
  muddy smear. Use the free bands for a headline and hero type: the band
  above the card carries ONE persistent `headline` motion graphic (a short
  third-person claim with one accent span, a who + when kicker; read
  motion-design) that hero lockups in the band replace and hand back
  automatically, so the band is never left empty for seconds.
- **Speaker + evidence** (the speaker reads a study, shows a tweet, points
  at a browser or document inset in the source): a stacked card,
  `set_picture_card(panels=[...])` — the speaker in one box, the evidence
  region of the SAME source frame in another, both over the window the
  evidence is discussed. Crop the evidence to what is being read (the
  highlighted sentence and its source line), so it is legible, and keep the
  speaker's face in frame the whole time (the speaker panel is solved from
  the face: chin, hair and lead room inside it); never a crop that drops the
  speaker for seconds or slices the inset. The panels keep a caption band
  between them; leave the band above for the stat; zooms do not play inside
  a stack.
- **Two speakers**: shot-aware reframing that cuts to the active speaker, or
  a stacked card when both reactions matter.
- Never leave a fixed band of the canvas empty for the whole reel.

TYPE SYSTEM — one per video:
- Captions are the frequent tier (motion looks; read captions). Hero words
  and numbers are the big tier (7–20% of frame height, motion templates;
  read motion-design). The accent tier is one role — serif italic, script or
  condensed heavy — on a few words.
- Pre-lay the block: a lockup's full layout is computed before any word
  reveals, so words appear IN PLACE and the block never reflows. Stacked
  lockups use tight leading (0.85–0.95), but glyphs never collide:
  `phrase_build`, `word_slam` and its kicker measure every glyph's ink and
  push a row down just enough that a descender or swash clears the caps
  below. A crossing is a deliberate choice (`phrase_build leading='overlap'`),
  never the default.
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
  holds still and keeps dialogue captions. On a reel it is a specific
  third-person claim from the clip's strongest line that the payoff
  completes — never a generic question, never the punchline or a word a
  later graphic slams — and sits inside the designed layout rather than
  above a small card on black. It also carries the speaker's name, so a
  broadcast lower third is unnecessary. It does not yield to other graphics: where
  hero lockups share the band above a card or letterbox, use the persistent
  `headline` motion template instead, which steps aside for them.
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
- Every heard word reaches the screen once: a motion graphic hides the
  spoken words it shows, and the captions carry every other word beside it,
  clear of it (a phrase_build's left-out words included — no micro bridge
  rows; end graphics where their words end). Keep hero graphics off the
  caption band so nothing has to be muted.

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
captions shrunk to avoid collisions; generic PROGRESS/FUTURE headings; a
lockup that re-typesets the transcript instead of adding information; the
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
