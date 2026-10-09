# Looks

A Look is a complete art direction applied in one pass: canvas, type, grade,
camera, signature moves, transitions with their sounds, and music. The seven
Looks below are distilled from a frame-level analysis of the owner's 28
reference reels in `/Users/masaoodi/Documents/Valmera/Instagram Reference Videos/`
(`orig-NN` = `01 - Original Instagram References/NN-*`, `gum-NN` = `02 - Mr Gum
References/NN-*`). Copy their craft, never their content, people, logos,
music or claims.

The bar: the premium references score about 7.5/10 on the review rubric, the
best about 8.2; our October shorts scored 2.6 because they were silent,
static and half black. A Look is not decoration on top of a story. It is how
the story's hook, turn and payoff become visible and audible.

The coordinator assigns one Look and one structure per short. The editor reads
**Shared grammar**, its structure, and its one Look block.

## Shared grammar (every Look)

### Targets

| Target | Value |
| --- | --- |
| Hook interrupt | a designed visual event at 0.0-0.6 s (landing zoom, `hook_title`, `word_slam`, card reveal) with its sound |
| Hook as text | the hook line readable on screen by 1.5 s; it poses the tension, never states the payoff |
| Visual change | something changes every 0.3-0.6 s (caption cue, graphic, zoom, cut); a structural event (cut, zoom, graphic, B-roll) every 1.5-2.5 s |
| Hero moments | 2-4 designed beats on exact word cues (Kinetic Poster, Mono Noir, Creator Glow: 3-6) |
| Payoff | marked (type, sound, camera or music button) and held 1.0-1.5 s before the editorial end |
| Picture area | full-bleed 1.0 when the face crop needs at most 2x upscale; otherwise a card covering at least 0.55-0.65 of the canvas on a designed background |
| Sound | SFX on every structural event; no digital silence longer than 0.3 s except a deliberate 50-280 ms stop-down before a reveal |
| Length | final 15-45 s including the 5 s native ending, so the editorial program is 10-40 s (montage editorial at most 25 s) |

### Type

- One bold grotesk backbone (Inter Display, Inter Tight, Montserrat; tracking
  -2 to -4%) plus ONE accent role: italic serif (Instrument Serif, Playfair),
  condensed heavy (Anton, Bebas, Archivo), script or mono.
- Size ladder 2:1 to 7:1. Connector words small (2-3% of frame height), body
  captions with a cap height of 3-4% of frame height (60-75 px on 1920), hero
  words 8-30%.
- Accent colour on 1-2 words per sentence, otherwise near-white (#F6F3EE).
  No thick outlines, no default yellow boxes, no emoji spam. Legibility comes
  from size, face-free placement, a soft shadow and the grade.
- One text system on screen at a time. Templates that replace the spoken words
  (`word_slam`, `phrase_build`) mute the captions under them.
- Safe area on 9:16: x 60-1020 px, y 8-80% of height. Keep the native corner
  mark and the bottom 20% clear.

### Camera (`add_zoom` modes; read its schema for exact fields)

| Mode | Use | Numbers |
| --- | --- | --- |
| `landing` | just after a hard cut or camera change | 1.12-1.18x easing to 1.0 over 0.35-0.5 s |
| `punch` | a stressed word | 1.10-1.25x, expo-out over 0.12-0.2 s, optional small overshoot; at least 4 s apart |
| `pulse` | a beat, laugh or list item | 1.0 to 1.07 to 1.0 over 0.3-0.4 s |
| `push` | any take longer than about 4 s | 1.00 to 1.04-1.06 across the take |
| shake | only the single biggest impact | always with `impact_hard` or `kick` |

Every still image moves (push or slow pan). Start a zoom after a cut, never
across it. Aim with `rect` or `cx`/`cy` read off `look_at`'s grid.

### Transitions and their sounds

`set_transitions` sets one base style for all scene junctions (keep
`scope='scene'` so jump cuts stay invisible). Add 1-2 transition templates
(`list_motion_templates('transition')`) at the story's turn. Templates bring
their own sound; for `set_transitions` add the kit sound with `add_sfx`.

| Move | Tool | Kit sound, placed so the peak lands on the cut |
| --- | --- | --- |
| hard cut on a speech onset | none | none, or `tick` on list beats |
| whip | `set_transitions` `whip_left`/`whip_right`, 0.2-0.3 s | `whoosh_hard`, start about 0.25 s early |
| zoom punch | `set_transitions` `zoom_punch` | `whoosh_hard` + `kick` on the cut |
| flash | `flash_transition` or `set_transitions` `flash` 0.15-0.25 s | `impact_soft` or `kick` |
| light leak | `light_leak` | `whoosh_soft` or `swoosh_up` |
| film burn | `film_burn` | `swoosh_up` into `impact_soft` |
| glitch | `glitch_burst` or `set_transitions` `glitch` | `glitch` |

### Sound and the music switch

SFX are on in every Look: a sound on each structural event, cued where the
motion lands (0-3 frames early), never one per caption. Gains: whooshes and
impacts -6 to -10 dB, pops, ticks, clicks and typing -12 to -16 dB. The voice
always stays on top. Risers end on the payoff frame (`riser_short` starts
1.2 s before, `riser_long` 2.6 s). Kit sounds are licence-free: `list_sfx_kit`,
then `add_sfx(storage_key='kit:<kind>', at=...)`.

Music comes from `list_music_library(mood)` + `add_library_music(slug, ...)`
(CC0; moods upbeat, chill, cinematic, corporate, dramatic, hiphop, ambient,
inspiring). The run brief sets `music`:

- **auto** (default): montage and action-opener structures get a quiet
  library bed; conversation structures stay dry with SFX.
- **on**: a bed under every short in the Look's mood, -20 dB (-18 to -22)
  ducked under speech; it rises when speech stops and ends on a button at
  the payoff.
- **off**: no music anywhere (the owner's earlier preference). Montages and
  action openers then carry deliberate SFX plus natural or ambient sound;
  digital silence is still not allowed.

A brief may override the switch per short. Record the track slug in the
handback so the manifest names it.

### Never

A picture band on a flat black void; a fade from black on frame 1 (if an old
look added one, remove it with `set_fades`); a still held frozen; a whoosh on
every caption; a graphic over the face for more than 1 s; a headline that
spoils the payoff; a number, quote, notification, chat or post that the source
does not support; another creator's logo, footage or identity.

## Structures

Structures are the four story shapes. Their IDs prefix every final's filename,
so they stay as named; "silent" now means no dialogue, never no sound. Any
structure can wear any Look unless noted.

- **fast-conversation**: continuous speech under frequent specific visual
  evidence: B-roll of the exact noun or action (`add_overlay(fit='picture')`
  or `'cover'`, licensed stock, `image_card`, `photo_stack`) and designed beats.
  A new idea, image or hero word every 1.5-2.5 s; no repeated B-roll moment.
- **headline-conversation**: the conversation carries the story under one
  persistent, verified, speaker-first headline (`set_editorial_graphic(kind=
  "headline", speaker=..., text=...)`) held through the editorial program.
  No B-roll cutaways and no full-screen interrupting cards. Motion lives in
  the captions, the camera (landing after every angle change, pushes, 1-3
  punches), the card reveal and 1-3 designed beats beside or below the picture
  (at most 2 s each). Best with Headline Pro; also Editorial Serif or Cinematic Doc.
- **hook-to-silent-montage**: a complete spoken premise, then a montage of at
  most 15 s (editorial at most 25 s) anchored by recognizable footage of the
  featured person plus direct, viewer-visible results of the premise. Never
  an all-product or multi-hop montage. Sound is mandatory: with music auto or
  on, the bed enters under the last spoken line at about -22 dB, becomes the
  lead as speech ends and buttons on the final image; cuts land on beats
  (`beat_align_cuts`) with `whoosh_soft`, `shutter` or `tick`. With music off,
  build it from natural sound, ambience and SFX.
- **silent-action-to-conversation**: 3-4 s of recognizable action by the
  actual subject, then the conversation that pays it off. The action gets
  sound (natural audio, `riser_short` or `whoosh_soft`, a bed when music
  allows) and a `hook_title` by 1.5 s.

## Choosing a Look

| The story is mostly | Look |
| --- | --- |
| one strong speaker carrying an opinion, joke or claim | Headline Pro |
| wisdom, reflection, a quiet admission | Editorial Serif |
| a contrarian argument, a list, high energy | Kinetic Poster |
| history, archive, a prediction, emotion | Cinematic Doc |
| stakes, warnings, grit, technology | Mono Noir |
| numbers, money, products, how something works | Clean Data |
| advice or a tutorial from a creator-style host | Creator Glow |

Across 8 or more hero shorts use at least three Looks. One Look on more than
half the batch needs a stated reason (for example a single archival talk).
A `custom-<slug>` Look is allowed when the brief spells out every field below.

## The Looks

### Headline Pro

- **Use for:** a single speaker whose delivery sells the idea; the upgraded
  headline lane.
- **References:** gum-05 (headline + 2.6x emphasis words), gum-08 (warm
  italic captions, piano bed whose bass enters on the punchline), gum-06 (the
  floor to beat), `.valmera/podcast-shorts/reference-library/20260909-headline-conversation/`.
- **Base:** `apply_look('editorial')`.
- **Canvas:** rounded 4:5 card (1:1 for 4:3 archival) at about 92% width,
  top near 24% height, on `set_picture_card` background `blur` (blurred,
  darkened copy of the picture + grain + vignette). Picture area at least
  0.55. A modern 16:9 single-speaker source may go full-bleed with a soft
  top gradient under the headline. Card entrance `reveal` at 0.0.
- **Headline:** `Name: claim`, name in accent (periwinkle #9DB1FF or sand
  #F1CA97), claim near-white, Inter Display 800, at most 2 lines.
- **Captions:** `motion_look` `editorial`; accent sand #F1CA97 or butter
  #F7E499; serif-italic emphasis on 1 word per phrase; inside the lower card
  or just below it, never over the mouth.
- **Grade:** warm matte, light grain, soft vignette.
- **Camera:** `landing` on every angle change; `push` on takes over 4 s;
  `punch` on 1-2 stressed words; `pulse` on the laugh or punchline.
- **Signature:** payoff hero word below the card (`word_slam` role serif,
  entrance `ghost` or `rise`) or `marker_text` on the payoff phrase;
  `circle_highlight`/`arrow_callout` when the speaker points at something
  visible; `counter` or `stat_card` only for a spoken number.
- **Transitions + sound:** hard cuts with landing zooms; `whoosh_soft` at
  the open; `pop_soft` on hero words; `impact_soft` or `chime` on the payoff.
- **Music (when on):** chill, cinematic or inspiring at -22 dB; bass or
  swell enters at the turn.
- **Targets:** change every 0.4-0.7 s; hook at or before 0.6 s; 2-3 hero moments.
- **Golden traits:** big face, still headline, captions that perform, payoff word lands with sound.

### Editorial Serif

- **Use for:** reflective, philosophical or intimate passages.
- **References:** gum-02 (rounded square card on olive grain, per-word type
  switches), gum-03 (giant Didone behind the speaker, red accent nouns,
  strobe exits), gum-11 (sparse serif word in lowercase sans, sub boom at the
  end), orig-06 (fat serif in the scene), orig-14 (serif quote with script accents).
- **Base:** `apply_look('editorial')`, captions `motion_look` `serif`.
- **Canvas:** square or 4:5 card at 89-92% width on a textured dark field
  (gradient or `blur`, olive-black #1F200C or warm charcoal, grain), or
  full-bleed when the shot has negative space. Type sits in the negative space.
- **Type:** Inter Display lowercase backbone + Instrument Serif italic for
  1-2 words per sentence in red #ED080D or near-white; ladder up to 7:1.
- **Grade:** filmic, slightly desaturated warm, grain, vignette.
- **Camera:** `push` on every long take; `landing` after cuts; at most one `punch`.
- **Signature:** `phrase_build` lockups for the hook and the turn (small sans
  connector row, large serif hero row, condensed qualifier), placed in face-free
  space with each row's `at` on its spoken onset; 1-2 hero words behind the
  subject (`add_motion_graphic(layer='behind_subject')` with `word_slam`, or
  `add_text_behind`) inside one continuous shot; `quote_card` or `marker_text`
  for the payoff.
- **Transitions + sound:** hard cuts; strobe exits on type; at most one
  `light_leak` at the turn. Template pops and ticks on rows; `whoosh_soft`
  into a behind-subject word; `sub_drop` or `impact_soft` under the payoff.
- **Music (when on):** ambient or cinematic at -22 dB.
- **Targets:** change every 0.35-0.6 s; hook at or before 0.6 s (first
  `phrase_build` row on the first word); 2-4 hero moments.
- **Golden traits:** quiet field, huge serif contrast on the right word, depth behind the subject.

### Kinetic Poster

- **Use for:** arguments, contrarian takes, lists, energy. The wow ceiling.
- **References:** orig-04 Creativity is Dead (change every 0.5-0.8 s, red
  keyword stacks, floating cards, stop-down drops), orig-12 Sell the emotion,
  orig-07 (poster cards cut to the beat), gum-09 (every word a designed event).
- **Base:** `apply_look('creator_punch')`, captions `motion_look` `stack`
  (small lead-in over a huge hero word) or `pop`.
- **Canvas:** full-bleed face-aware 9:16, alternating with rounded floating
  cards on a dark vignette (`set_picture_card` windows) for evidence; type
  dominates 25-70% of height.
- **Type:** Inter Display Black or Archivo condensed caps, tight; accent red
  #D90F17 or gold #FFD400; the payoff word 1.5x the other heroes.
- **Grade:** desaturated base with selective red and yellow, contrast, grain.
- **Camera:** `punch` with overshoot on stressed words; `landing` on every
  cut; `pulse` on beats; one shake on the biggest hit.
- **Signature:** `word_slam` on 3-5 hero words (start 0.2 s before the word);
  `phrase_build` for the thesis; `versus_split` for a contrast; `image_card`
  or `photo_stack` for licensed evidence; `chapter_title` between list items;
  `glitch_burst` or `flash_transition` at the turn.
- **Transitions + sound:** `zoom_punch` or whip base; dense but structural:
  `whoosh_hard` on transitions, `kick`/`impact_hard` on slams, `riser_short`
  into the payoff and `sub_drop` after it, with a 100-250 ms stop-down before
  the payoff word.
- **Music (when on):** hiphop, dramatic or upbeat at -18 dB, with the stop-down.
- **Targets:** change every 0.25-0.45 s; hook at or before 0.3 s; 4-6 hero moments.
- **Golden traits:** something lands on almost every stressed word, and the payoff is the biggest thing in the short.

### Cinematic Doc

- **Use for:** archival talks, history, predictions, emotional stories.
- **References:** gum-01 (two-frame exposure-flash cuts with clicks, light
  leaks, glow title), gum-08 (teal/orange archive, piano bed), orig-16 (red
  keywords behind the subject, film-burn leak, accelerating push), orig-17
  (low-key grade, grain), orig-10 (riser into a beat drop).
- **Base:** `apply_look('cinematic_doc')`, captions `motion_look` `clean`
  (sentence case) with serif hero words.
- **Canvas:** full-bleed for modern sources; 4:3 archival on a 4:5 or 1:1
  card filling at least 60% of the height over a `blur` plate; optional
  letterbox strips for B-roll passages.
- **Type:** Inter Tight captions; `glow_title` or `chapter_title` in Bodoni
  Moda or Playfair italic; accent butter #F7E499 or red.
- **Grade:** teal/orange low-key or warm archival, grain, vignette, highlight bloom.
- **Camera:** `push` on every take (to about 1.06 over 4-8 s); `landing`
  after cuts; Ken Burns on every still; rare `punch`.
- **Signature:** `glow_title` for the premise or a date; `timeline_steps` or
  `chapter_title` for a time jump (1983 to 2010); `image_card`/`photo_stack`
  archival evidence; `quote_card` for the payoff line; `focus_spotlight` on
  one face in a group; `light_leak`/`film_burn` at the turn.
- **Transitions + sound:** `dip_white` 0.2 s or short flash base; `shutter`
  on photos, `whoosh_soft` into titles, `riser_long` into the turn (once),
  `impact_soft` on the payoff.
- **Music (when on):** cinematic, inspiring or ambient at -20 dB, swelling
  into the payoff.
- **Targets:** change every 0.5-0.8 s with a structural event every 2-3 s;
  hook at or before 0.6 s; 2-3 hero moments.
- **Golden traits:** graded, textured, always drifting; the past feels like film, not a webcam.

### Mono Noir

- **Use for:** stakes, warnings, founder grit, technology, intensity.
- **References:** orig-15 (black and white, one-word captions, white flash
  on drops), orig-16 (red keywords behind the subject), gum-03 (red Didone),
  gum-10 (monochrome cuts decelerating on ticks), gum-07 (invert flashes).
- **Base:** `apply_look('mono_noir')`, captions `motion_look` `stack`, or
  `mono` for technical stories.
- **Canvas:** full-bleed black and white; red type behind the subject.
- **Type:** condensed caps (Anton, Bebas, Archivo condensed) for heroes in
  red #ED080D; white Inter Bold for 1-3-word captions; mono for code and numbers.
- **Grade:** high-contrast monochrome with one red accent, heavier grain, vignette.
- **Camera:** `punch` on hits (shake on the biggest), `landing` after cuts, `pulse` on beats.
- **Signature:** `word_slam` with `strobe` or `ghost` entrances; `text_scramble`
  or `typewriter` for numbers and technical terms; `counter` for a spoken
  number; red behind-subject keyword; `glitch_burst` at the turn.
- **Transitions + sound:** white flash on drops, glitch, hard cuts on speech
  onsets; `impact_hard` + `sub_drop` for the hero, `glitch`, `typing` under
  typewriters, `tick` on counters, `riser_short` into the payoff.
- **Music (when on):** dramatic or hiphop at -18 dB with a stop-down before the payoff.
- **Targets:** change every 0.3-0.5 s; hook at or before 0.4 s; 3-5 hero moments.
- **Golden traits:** stark, loud, red only where it matters.

### Clean Data

- **Use for:** numbers, money, products, business mechanics, how-to.
- **References:** orig-13 (UI planes, song card, typed comment), orig-11
  (Stripe-style revenue cards, spread caption rows), orig-03 (product UI on
  a grid stage), orig-09 (UI cards and charts around the subject), orig-12
  (gradient cards).
- **Base:** `apply_look('clean_minimal')`, captions `motion_look` `clean`
  (or `box` over busy footage).
- **Canvas:** full-bleed, or a card on a designed gradient (white to
  periwinkle, royal blue #1C4DB0) during data beats; UI cards float beside the head.
- **Type:** Inter Tight Bold, tight tracking; accent periwinkle #6D93D6 or
  a growth green; numbers big.
- **Grade:** high-key, clean, neutral, light grain.
- **Camera:** `push` on talking takes, `pulse` when a card lands, `landing` after cuts.
- **Signature:** `counter`, `stat_card`, `bar_compare`, `line_chart`,
  `progress_ring`, `timeline_steps`, `checklist`, `search_bar`, `notification`,
  `chat_bubbles`, `post_card`, `versus_split`, `arrow_callout`. Only for
  numbers and events the source actually states; never fake a real person's
  message or post.
- **Transitions + sound:** short whips and swipes; `click_ui`, `tick`, `coin`
  for money, `ding` on a result, `typing` under `search_bar`, `notification`
  for notifications, `whoosh_soft` on cards.
- **Music (when on):** corporate or upbeat at -20 dB.
- **Targets:** change every 0.35-0.6 s; hook at or before 0.6 s; 3-5 data beats.
- **Golden traits:** the number becomes the picture, and it counts up with a sound.

### Creator Glow

- **Use for:** advice, tutorials and energetic creator-style hosts.
- **References:** orig-05 (dark talking head, glowing white and gold keyword
  stacks beside the face, flare and zoom-blur transitions), orig-08 (caps hook
  then small narration captions), orig-01 (textured keywords behind the head,
  script accents, photo collages).
- **Base:** `apply_look('creator_punch')`, captions `motion_look` `glow` or `pop`.
- **Canvas:** full-bleed with the subject off-centre; the type stack fills
  the opposite side.
- **Type:** Inter Display Black or ExtraBold, white plus gold #FFC21A
  glowing accent words, emphasis about 3:1.
- **Grade:** low-key, contrasty, warm highlights, bloom.
- **Camera:** `punch` on keywords, `landing` after cuts, zoom-blur or whip into B-roll.
- **Signature:** `hook_title` (treatment `glow`) beside the face; `glow_title`;
  `phrase_build`; `arrow_callout` or `circle_highlight`; `photo_stack`;
  `focus_spotlight`. CTA templates (`follow_cta`, `save_cta`, `comment_cta`)
  only when the run brief asks.
- **Transitions + sound:** `zoom_punch` or whip base plus `flash_transition`
  or `light_leak`; `whoosh_hard`, `pop_bright` on keyword stacks,
  `riser_short` into the payoff, `impact_soft`.
- **Music (when on):** upbeat or hiphop at -18 dB.
- **Targets:** change every 0.3-0.5 s; hook at or before 0.4 s; 3-5 hero moments.
- **Golden traits:** glowing words build beside the face, so the hook reads before the sentence ends.
