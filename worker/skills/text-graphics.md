# text-graphics — native text, kinetic phrases, text behind the subject, vectors, title cards, colour/corrupt screens, freeze frames

## Editorial decision principles

Designed type is the main motion surface of a short-form edit. For premium
animated titles, hero words, counters, callouts, UI cards and CTAs, the
browser motion library is the first choice (read motion-design); the native
tools here cover text the renderer draws directly, the person-matted
behind-subject effect, vector annotations, splice-in cards and freezes. All
of them follow one type system and one motion language per video, bound to
the exact phrase, object, action or beat they clarify.

## Evidence to inspect

Inspect exact words and program timing (`get_kept_transcript`, `get_words`),
clear space, face and UI geometry, target objects for arrows and rings,
caption collisions, reading time, path extremes, matte coverage and the
rendered entrance, settle and exit frames.

## Strong treatment patterns

CHOOSING THE TOOL:
- Animated hero word, hook title, counter, chart, callout, UI mockup, CTA,
  photo card, transition texture → `add_motion_graphic` (motion-design).
- Mixed-font phrase built in fixed positions at speech cues →
  `set_typography_scene` (premium-composition).
- Measured editorial layouts (headline, statement, comparison, metric,
  quote, chapter, label) as editable native layers →
  `set_editorial_graphic`.
- Simple dictated titles, labels and stats with keyframed motion →
  `add_text` / `set_text_motion`.
- Words genuinely behind a person → `add_text_behind`, or a motion graphic
  with `layer='behind_subject'`.

TEXT TEMPLATES (`add_text` / `set_text_motion` / `remove_text`): 'title',
'subtitle', 'lower_third', 'callout', 'big_number', 'quote', 'chapter'.
Entrances: typewriter, pop, whip, blur_in, and 'none' for instant text.
Designed text owns caption suppression for its window by default; use
`mute_captions=false` for an independent label in a separate region.
`font_size` is a fraction of the canvas short side; `max_width` is a column
fraction. outline_width=0 and shadow=0 give clean flat type on a panel.

GENERAL TEXT MOTION — `motion` on `add_text`, or `set_text_motion(id,
motion)`, takes element-local x/y/scale/rotation/opacity values or keyframes
(`[{"t":0,"v":...},{"t":...,"v":...,"ease":"out"}]`); `t` is seconds from
that text's start. Explicit motion replaces entrance/exit. Express a
relationship: travel toward the named object, settle into clear space,
overshoot only a stressed word. Settle, then hold still.

VECTOR GRAPHICS (`add_vector_graphic` / `set_vector_graphic` /
`remove_vector_graphic`): rectangle, ellipse, line, arrow, ring, progress.
- An arrow or ring identifies a REAL visible object, control, statistic or
  action read off the grid; a panel sits behind words with deliberate
  padding; progress represents real completion only — never invent 73%.
- For drawn-on arrows, hand-drawn circles and highlighter sweeps (sounded
  only when the moment earns it), the motion library (arrow_callout, circle_highlight, marker_text) is the
  premium route.
- Revise an existing vector instead of stacking a near-duplicate.

WORDS BEHIND THE SUBJECT (`add_text_behind`) — a person-matting model cuts
the subject out per frame and the words pass genuinely behind them.
- PEOPLE occlude the words (including what they carry); static objects do
  not. Say exactly that if asked.
- Arguments are add_text's plus at_output_s + duration_s (2–4 s reads well).
- It refuses with no person in the window, a subject filling most of the
  frame, a window crossing a cut, or a speed ramp; relay the reason. It
  reports how much of the text the subject crosses — near zero means an
  ordinary title.
- Behind-subject type is LARGE: the person crosses the middle of tall glyphs
  while tops and bottoms stay readable. Never shrink it to reduce hidden
  letters. No zoom, stabilize or speed ramp over the window.

STANDALONE TITLE CARDS (`add_title_card`): a term on its own screen —
`add_title_card(text, at_output_s, duration_s)` splices a card in (captions
never land on it; everything after shifts by duration_s). Never fake it with
a full-frame blur; pass subtitle= for a second line. For a chapter turn
over continuing footage, `chapter_title` in the motion library is usually
stronger.

PLAIN COLOUR / GRADIENT SCREENS (`add_color_screen`): a white or black flash,
a coloured interstitial or a gradient backdrop with no text, built locally;
color2 + direction for a gradient; motion adds a slow push. Words on it →
`add_text` at the same window, or `add_title_card`.

CORRUPT / GLITCH SCREENS (`add_corrupt_screen`): the signal "breaks" between
sections — style 'digital', 'vhs' or 'static', 0.3–1 s, silent by
default; sound=true adds a synthesized static burst, only when the user
asks for one (it is not a library recording). Different from the `glitch` junction style and the
`glitch_burst` motion transition.

FREEZE FRAMES (`add_freeze_frame`): freeze the picture and hold big words
over the blurred, darkened still — a real cut, captions never land on it.
Right for the 2–3 strongest lines of a sermon or motivational piece; a
freeze may carry one `shutter_1` or `shutter_2` cue from the sound library
(the strongest one may take `impact_1` instead, once per short).

KINETIC TEXT (`add_kinetic_text`): transcript-timed phrase typography over a
range, placed in measured clear bands; `motion_style='composed'` is one
inward settle with an overshoot reserved for `emphasis_words`; `still` when
cutting carries the energy. It mutes duplicate bottom captions only under the
phrases it creates. For premium word-on-onset motion across a whole reel,
caption motion looks plus hero motion graphics usually read stronger.

## Common failure modes

- Cheap mixed templates or three unrelated type systems; too many words or
  levels; type across a face or in the UI band.
- Arrows with no visible target; invented progress or numbers.
- Panel and text moving independently; paths that leave the frame.
- Small behind-subject text the person never crosses.

## Verification procedure

Render a complete preview and review entry, path extremes, settled state
and exit on every distinct background with
`look_at(rendered=true, output_times=[...])` (rendered looks need a
complete preview of the current version);
check reading time, geometry, hierarchy, caption suppression and that each
graphic lands on its word.

## Repair ladder

Shorten copy → unify hierarchy/type → move to the motion-library equivalent →
align panel/vector geometry → reposition or reduce motion → remove
unsupported decoration → rerender the complete window.
