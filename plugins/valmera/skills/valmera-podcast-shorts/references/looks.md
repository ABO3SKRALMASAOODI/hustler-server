# Looks

A Look is a complete art direction applied in one pass: canvas, type, grade,
signature moves and transitions, plus the camera moves and sounds it allows
when a moment earns one. Music is not part of a Look: it is off unless the
owner supplies a song. The seven
Looks below are distilled from a frame-level analysis of the owner's 28
reference reels in `/Users/masaoodi/Documents/Valmera/Instagram Reference Videos/`
(`orig-NN` = `01 - Original Instagram References/NN-*`, `gum-NN` = `02 - Mr Gum
References/NN-*`). Copy their craft, never their content, people, logos,
music or claims.

The bar: the premium references score about 7.5/10 on the review rubric, the
best about 8.2; our October shorts scored 2.6 because nothing was designed on
them (plain captions on a half-black canvas). A Look is not decoration on top
of a story. It is how the story's hook, turn and payoff become visible.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

The coordinator assigns one Look and one structure per short. The editor reads
**Shared grammar**, its structure, and its one Look block.

## Shared grammar (every Look)

### Targets

| Target | Value |
| --- | --- |
| Hook interrupt | a designed visual event at 0.0-0.6 s (`hook_title`, `word_slam` or a card reveal; a punch-in or landing zoom only when the opening earns it) — no reflexive opening whoosh; a sound only when the title has a real entrance that earns one |
| Hook as text | the hook line complete and readable on screen by 1.5 s (about 5% of frame height or more, not a slow typewriter); it poses the tension, never states or quotes the payoff, never contradicts the words under it |
| Speaker identity | each speaker named within ~3 s of first appearing (`lower_third` or the headline band: verified name plus role, venue or year) |
| Meaning | each graphic means what the speaker means: a tick is "achieved" (broken promises and myths take `mark='cross'`), slams go on information-bearing words, never on clichés, and no graphic only recaps an earlier one |
| Graphic choice | contrast punchlines as a two-beat swap (setup words, then payoff words, each on its onset); counters only for counted or growing quantities and landing ON the number; list items share one type role; zones rotate (above head, beside face, chest, header) and stay off the face; at least one hero word behind the subject when the background allows; no caption-only stretch over ~3 s in the last third |
| Visual change | something changes every 0.3-0.6 s (caption cue, graphic, cut); a structural event (cut, graphic, B-roll) every 1.5-2.5 s. Zooms and sounds never fill this: they are optional, only where a moment earns them |
| Hero moments | 2-4 designed beats on exact word cues (Kinetic Poster, Mono Noir, Creator Glow: 3-6) |
| Payoff | marked (type first; a sound or a camera move only when it earns one; the owner's song's button when music is on) and held 1.0-1.5 s before the editorial end |
| Picture area | full-bleed (1.0) when the face crop needs at most 2x upscale; otherwise a card on a designed backdrop covering at least 0.54 of the canvas (see Card geometry). Only Editorial Serif's square card may go down to 0.48 (gum-02), because its field carries the type |
| Sound | optional: zero by default, at most 1-2 approved library sounds on structural on-screen moments, each with a visual partner, none on a payoff word's onset, no literal puns, never the same sound within ~3 s, levelled by the tool (see Sound); no digital silence longer than 0.3 s except a deliberate 50-280 ms stop-down before a reveal or a montage passage flagged for the owner's song (see Sound without music) |
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
- Every heard word reaches the screen once. With `mute_captions` unset a
  graphic takes the words it shows and the captions carry every other word
  beside it, in a band clear of it (a `phrase_build` no longer sets micro
  bridge rows: its rows carry the words that matter, the captions the rest).
  End a `word_slam` or counter where its words end; the reply NOTEs the
  words the captions carry beside it and any it had to mute (carry those on
  it as a kicker). Kickers and rows quote the transcript word for word.
- Captions in a card or speaker + screen stack are placed by the engine:
  never on a panel edge or the seam between panels, never on the screen
  panel, never on the chin; usually the band above the speaker panel. Do
  not pin a `placement_track` between panels.
- One accent colour for the whole short, passed to every graphic's `accent`;
  at most about 4 template families per short, varied across the batch.
- Over a bright shirt or wall, raise the template's `scrim` (or move the type
  to darker space); white type on a white shirt is a defect.
- Safe area on 9:16: x 60-1020 px (captions: 97-983, the feeds' ~9% side crop), y 8-80% of height. Keep the native corner
  mark and the bottom 20% clear; no card, caption or graphic below y 0.80.

### Card geometry (1080x1920)

`set_picture_card(box=[left, top, right, bottom], ...)` takes canvas
fractions. Always pass a designed backdrop: `background_style='blur'` (the
card's own footage blurred and darkened; `background_dim` about 0.5),
`'radial_gradient'` or `'vertical_gradient'` (with `background` and
`background_color2`), plus `grain` about 0.25 and `vignette` about 0.4.
Never the flat default colour.

| Card | `box` | Area | Headline zone | Captions and hero words |
| --- | --- | --- | --- | --- |
| 4:5 at 88% width (950x1188) | [0.06, 0.175, 0.94, 0.794] | 0.54 | y 0.08-0.17 | inside the card's lower third, y 0.62-0.76, in face-free space |
| 1:1 at full width (1080x1080), for 4:3 archival | [0, 0.21, 1, 0.773] | 0.56 | y 0.08-0.20 | inside the card, y 0.60-0.74, face-free |
| 1:1 at 92% width (994x994), Editorial Serif only | [0.04, 0.12, 0.96, 0.637] | 0.48 | none | in the field below the card, y 0.66-0.80 |

The headline zone above a card holds the persistent `headline` motion
template (or the editorial headline): one per short, written with y omitted
so it fills that zone; hero lockups placed in the zone replace it and hand it
back automatically, so it is never empty for seconds.

A wider 4:5 card (up to 92% width, 64.7% of the height) fits only without a
headline above it and must still end at or above y 0.80. Nothing designed
goes below y 0.80, the band the platform covers: with the 4:5 and
full-width cards, captions and hero words therefore sit inside the card.

### Camera (`add_zoom`; read its schema before the first call)

**Zooms are optional, never a rule** (see the rule above). A steady,
well-framed picture is the default; each zoom needs a reason you can name
(the payoff word, the number the story turns on, a real turn between ideas,
a genuinely jarring jump cut). Never a landing on every cut, a `push_in` on
every take or a punch on every sentence. Each Look's **Camera** line below
lists the moves that suit it when a moment earns one — a menu, not a quota;
zero zooms is a fine result.

`strength` is the added zoom: 0.15 means 1.15x. Mode names are the schema's
(`push_in`, not `push`).

| Mode | Use | Numbers |
| --- | --- | --- |
| `landing` | the first frame after a hard cut into a new idea (not every angle change) | strength 0.12-0.18, settles to 1.0 in about 0.35 s; start exactly on the cut, end about 0.4 s later |
| `punch` | a stressed word, number or payoff | strength 0.10-0.25, about 0.12 s snap, held, steps back out at `end` (the next cut or sentence turn); `overshoot` 0.05-0.15 only on the biggest beat; at least 4 s apart |
| `pulse` | a beat, laugh or list item | strength 0.05-0.08, in and out in about 0.3 s |
| `push_in` | a take longer than about 4 s that builds toward something (not every long take) | strength 0.05-0.08 across the take |
| `shake` (or `shake` 0.3-0.8 on a punch) | only the single biggest impact | with the short's one `impact_1` on that frame |

Every still image moves (`push_in` or a slow pan). Start a zoom after a cut,
never across it. Aim with `rect` or `cx`/`cy` read off `look_at`'s grid.

### Transitions and their sounds

`apply_look` sets a base transition, firing only at real scene changes, and
places no sound unless called with `transition_sounds=true` (do not, unless
the brief asks for transition sounds). `set_transitions` (keep
`scope='scene'` so jump cuts stay invisible) adds no sound. Add 1-2
transition templates (`list_motion_templates('transition')`) at the story's
turn; templates are silent unless you pass `sfx=true`.

Only a junction at a real turn earns a sound, and a run of junctions inside
~3 s gets one sound in total. Ordinary cuts inside the conversation get none.

| Move | Tool | Library sound when the turn earns one, `at` on the cut (it peaks there) |
| --- | --- | --- |
| hard cut on a speech onset | none | none |
| whip | `set_transitions` `whip_left`/`whip_right`, 0.2-0.3 s | `swish_1` on the cut |
| zoom punch | `set_transitions` `zoom_punch` | `swish_1` on the cut (one sound, no second hit) |
| flash | `flash_transition` or `set_transitions` `flash` 0.15-0.25 s | none, or `impact_1` when the flash is the payoff |
| light leak | `light_leak` | `whoosh_soft_1` or `whoosh_soft_2` on the leak's peak |
| film burn | `film_burn` | a soft whoosh, or `riser_3` ending on the cut at the one big section change |
| glitch | `glitch_burst` or `set_transitions` `glitch` | `glitch_1` or `glitch_2` |

### Sound

Sound is used the way a professional editor uses it, never as decoration.
The owner rejected decorative "whoosh wars": every sound must be earned by
something on screen.

**Only the approved library.** `list_sound_library()` lists 21 real
recordings the owner approved by ear (CC0), with when to use each and its
measured hit level. Roles and ids: whoosh (`whoosh_soft_1`, `whoosh_soft_2`),
swish (`swish_1`), impact (`impact_1`), riser (`riser_1`-`riser_4`), shutter
(`shutter_1`, `shutter_2`), typing (`typing_1`, `typing_2`), click
(`click_1`, `click_2`), pop (`pop_1`), tick (`tick_1`), ding (`ding_1`),
glitch (`glitch_1`, `glitch_2`), cash (`cash_register_1`), heartbeat
(`heartbeat_1`). Place one with `add_sfx(storage_key='sound:<id>', at=...)`
as a typed call, not inside `apply_edit_batch`, and leave gain_db unset: the
tool levels each recording against the measured voice at its hit and
reports where it sits (see Timing and level). Never
`search_sfx`, `add_web_sfx` or any other online sound in these shorts. There
is no paper sound, no sub drop and no hard whoosh: whips take `swish_1`,
hits take `impact_1`.

**Graphics are silent by default.** `add_motion_graphic(..., sfx=true)` opts
one moment in and maps the template's sound roles onto the library (roles
with no approved recording are skipped). Opt in only where the graphic's
landing earns a sound; a template whose cue repeats (a tick per counter step
or per letter) stays silent and its settled figure gets one sound at most,
on a visual landing in the pause after its spoken number, never on the
number's onset.

**Where a sound goes.** Only where something meaningful happens on screen:
the hook graphic landing, a hero graphic landing, a real section change or
B-roll entry, the payoff, or a real-world action shown on screen (a shutter
on a photo or still arriving, typing under typed text, a click on a visible
button press, a cash register on a payment shown, a ding on a notification
card). Something on screen changes within ~50 ms of the hit. Never on
captions, jump cuts, ordinary cuts inside the conversation, landing zooms,
`push_in`, punches or pulses on speech; never the reflexive opening whoosh;
never a bright sound (ding, pop, click, shutter) on the onset of the payoff
or an emphasis word, where it masks the word; never a literal pun on the
spoken word (a shutter on 'pictures', a cash register on 'money' with only
type on screen).

**How many.** Sound effects are optional, never a rule: a sound with no
clear on-screen reason makes a short look childish. A podcast short carries
zero by default and at most 1-2 for the whole short (template cues count),
each on a structural moment — the payoff, a real section change — never the
same sound twice within ~3 s. Each Look names one consistent family to draw
those one or two from; match the material (no impact under a tender
admission). The ceiling is never a quota.

**Timing and level.** The peak lands on the visual frame: pass `at` = the
frame the sound HITS and `add_sfx` starts each library recording early by
its measured peak (risers end on `at`, typing starts there) and reports
where the peak lands, so never pre-roll by hand. Long tails (`impact_1`)
stop at their measured fade point unless `dur_s` asks for more. The level
is the tool's: with gain_db unset, `add_sfx` (and a graphic's own cues)
sets each recording against the voice's short-term loudness at its hit —
whoosh and swish about 8 dB under the voice, ding, pop, click, tick and
shutter about 10 under, typing 14 under, an impact louder only below
150 Hz (13 dB under the voice above it, the whole hit at most 8 under) —
and its MIX line says where it sits; a CHECK names a sound that will be
inaudible or too hot, or that sits on a payoff word, punning, or with no
visual partner. `impact_1` is used at most once per short, on the payoff
or the single biggest landing.

Montage cuts are not designed beats: a montage gets at most one or two
sounds in all — on its entry, a still's arrival, or its final image.

### Music: off unless the owner supplies a song

The owner does not use agent-chosen music. Music is **off** for every short
by default: no CC0 library bed, no stock track, no song picked by taste, and
`apply_look` is never called with a music option.

Music is **on** only when the owner supplies or approves a specific song for
the run. The saved brief's Music line names it, and the coordinator records
it with `run.py init --song '<file, link or Artist - Title>'`. A brief's
`music` is `inherit` (follow the run) or `off` (keep this short dry); `on` is
accepted only when the run has the owner's song. `run.py assign` prints
`music_effective` and `music_song`, and the coordinator hands both to the
editor. Editors never re-derive them and never substitute another track.

When on, place exactly that song: the owner's file
(`list_assets(kind='music')`, then `add_music`), the owner's link
(`fetch_url(url, as_kind='music')`), or a named song via `find_song` then `fetch_url` (the
artist's own or "- Topic" upload; note in the handback that a found song is
not a usage licence and platforms may mute it). The bed sits about -20 dB
(-18 to -22) ducked under speech, rises when speech stops and ends on a
button at the payoff. Record the song in the handback so the manifest names
it.

### Sound without music (montage and action openers, the default)

A no-dialogue passage must never ship as digital silence, and it must never
get an invented music choice. With music off, in this order:

1. **Natural sound.** Prefer footage that carries its own sound and keep it:
   spliced video inserts play their own audio unless muted, so leave them
   unmuted when the sound is clean. Or carry the source's own room tone,
   laughter or applause under `add_overlay` cutaways (overlays keep the
   program audio; set its level with `set_volume` over that source span),
   never with audible words outside the story.
2. **Design around speech.** Keep the silent stretch short, or lay the
   montage as overlays over the premise's last spoken lines so the voice
   carries it and the montage proper is only the final few seconds.
3. **One or two library sounds, not a bed.** A shutter as a still arrives,
   or a riser ending on the final image, inside the short's 1-2 budget. Never string sounds together to
   fill the silence, and never search online for ambience.
4. **Flag it.** Only when neither works: keep the passage short and write
   the handback `music` as `none - owner to add a song when posting
   (montage <start>-<end> s)`; it flows into PUBLISHING.md so the owner adds
   a song at posting. Never pick a song yourself.
5. Check with `audit_audio_mix` and the render's audio check: no digital
   silence over 0.3 s outside a deliberate stop-down or a flagged passage.

### Never

A picture band on a flat black void; a fade from black on frame 1 (if an old
look added one, remove it with `set_fades`); a still held frozen; a whoosh on
every caption or cut, or the same sound twice within ~3 s; a zoom or sound
you cannot name a reason for; music the owner
did not supply; any change to the corner mark or the native end card; a
graphic over the face for more than 1 s; a headline that spoils the payoff;
a number, quote, notification, chat or post that the source does not
support; another creator's logo, footage or identity.

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
  the captions, the card reveal and 1-3 designed beats inside the card's
  face-free space (at most 2 s each); a camera move only where a moment earns
  it (at most 1-3 in the short, never one on every angle change or take).
  Best with Headline Pro; also Editorial Serif or Cinematic Doc.
- **hook-to-silent-montage**: a complete spoken premise, then a montage of at
  most 15 s (editorial at most 25 s) anchored by recognizable footage of the
  featured person plus direct, viewer-visible results of the premise. Never
  an all-product or multi-hop montage. Never digital silence: with the
  owner's song (`music_effective` on) it enters under the last spoken line at
  about -22 dB, becomes the lead as speech ends and buttons on the final
  image, and cuts land on its beats (`beat_align_cuts`) without a sound per
  cut. With music off (the default), follow **Sound without music**: natural
  sound, a montage designed around speech, a few library sounds, or a flag
  for the owner to add a song when posting.
- **silent-action-to-conversation**: 3-4 s of recognizable action by the
  actual subject, then the conversation that pays it off. The action keeps
  its natural audio (or follows **Sound without music**), may take one
  library sound into the hook title (`riser_2` ending on it, or a soft
  whoosh), carries the owner's song only when `music_effective` is on, and
  gets a `hook_title` by 1.5 s.

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
- **Canvas:** rounded 4:5 card at 88% width, `box` [0.06, 0.175, 0.94,
  0.794] (picture area 0.54); for 4:3 archival a 1:1 card at full width,
  `box` [0, 0.21, 1, 0.773] (0.56). Backdrop `background_style='blur'`,
  `background_dim` about 0.5, grain and vignette. A modern 16:9
  single-speaker source may go full-bleed (face-aware `set_frame`) with the
  headline over clear space at the top. Card entrance `reveal` at 0.0.
- **Headline:** `Name: claim` in the headline zone (y 0.08-0.17; 0.08-0.20
  over a 1:1 card), name in accent (periwinkle #9DB1FF or sand #F1CA97),
  claim near-white, Inter Display 800, at most 2 lines.
- **Captions:** `motion_look` `editorial`; accent sand #F1CA97 or butter
  #F7E499; serif-italic emphasis on 1 word per phrase; inside the card's
  lower third (y 0.62-0.76) in face-free space, never below the card and
  never over the mouth.
- **Grade:** warm matte, light grain, soft vignette.
- **Camera (optional, only where earned):** a `punch` on the 1-2 words the
  story turns on; a `landing` on a real turn between ideas; a `pulse` on the
  laugh or punchline. Never a landing on every angle change or a push on
  every take.
- **Signature:** payoff hero word inside the card's lower third (`word_slam`
  role serif, entrance `ghost` or `rise`, y about 0.70, the words it shows
  leaving the captions) or `marker_text` on the payoff phrase;
  `circle_highlight`/`arrow_callout` when the speaker points at something
  visible; `counter` or `stat_card` only for a spoken number.
- **Transitions + sound:** hard cuts (a landing zoom only on a real turn,
  silent). Family, only where a moment earns a sound: `whoosh_soft_1` into the
  opening card when it has a real entrance, `impact_1` under the payoff.
  At most 2 sounds per short, zero by default.
- **Music (owner's song only, when on):** -22 dB ducked; let its swell land
  at the turn.
- **Targets:** change every 0.4-0.7 s; hook at or before 0.6 s; 2-3 hero moments.
- **Golden traits:** big face, still headline, captions that perform, the payoff word lands.

### Editorial Serif

- **Use for:** reflective, philosophical or intimate passages.
- **References:** gum-02 (rounded square card on olive grain, per-word type
  switches), gum-03 (giant Didone behind the speaker, red accent nouns,
  strobe exits), gum-11 (sparse serif word in lowercase sans, sub boom at the
  end), orig-06 (fat serif in the scene), orig-14 (serif quote with script accents).
- **Base:** `apply_look('editorial')`, captions `motion_look` `serif`.
- **Canvas:** a 4:5 card at 88-92% width (area 0.54-0.59), or a 1:1 card at
  92% width or more, `box` [0.04, 0.12, 0.96, 0.637] (area 0.48, gum-02),
  with the type in the field below it; backdrop `background_style=
  'radial_gradient'` (olive-black #1F200C or warm charcoal) or `'blur'`, with
  grain and vignette. Or full-bleed when the shot has negative space. Type
  sits in the negative space, never below y 0.80.
- **Type:** Inter Display lowercase backbone + Instrument Serif italic for
  1-2 words per sentence in red #ED080D or near-white; ladder up to 7:1.
- **Grade:** filmic, slightly desaturated warm, grain, vignette.
- **Camera (optional, only where earned):** a slow `push_in` on the take
  that builds to the turn or the payoff; at most one `punch`; a `landing` only
  on a real section change. Never a push on every take or a landing on every cut.
- **Signature:** `phrase_build` lockups for the hook and the turn (small sans
  connector row, large serif hero row, condensed qualifier), placed in face-free
  space with rows quoting the transcript (the engine lands each spoken word on
  its onset); 1-2 hero words behind the
  subject (`add_motion_graphic(layer='behind_subject')` with `word_slam`, or
  `add_text_behind`) inside one continuous shot; `quote_card` or `marker_text`
  for the payoff.
- **Transitions + sound:** hard cuts; strobe exits on type; at most one
  `light_leak` at the turn. Family, only where a moment earns a sound:
  `whoosh_soft_2` into a behind-subject word or the light leak, `heartbeat_1`
  or `impact_1` under the payoff; `phrase_build` rows stay silent. At most 2
  sounds per short, zero by default.
- **Music (owner's song only, when on):** -22 dB ducked.
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
  cards for evidence (`set_picture_card` windows, `background_style=
  'radial_gradient'` dark with vignette); type dominates 25-70% of height.
- **Type:** Inter Display Black or Archivo condensed caps, tight; accent red
  #D90F17 or gold #FFD400; the payoff word 1.5x the other heroes.
- **Grade:** desaturated base with selective red and yellow, contrast, grain.
- **Camera (optional, only where earned):** a `punch` on the hero and
  payoff words (overshoot only there); a `pulse` on a real beat; one shake on
  the biggest hit at most. The energy comes from the type — never a landing
  on every cut or a punch on every sentence.
- **Signature:** `word_slam` on 3-5 hero words (start 0.2 s before the word);
  `phrase_build` for the thesis; `versus_split` for a contrast (`vs='serif'`
  for a typographic 'vs' instead of the disc); `image_card`
  or `photo_stack` for licensed evidence; `chapter_title` between list items;
  `glitch_burst` or `flash_transition` at the turn.
- **Transitions + sound:** `zoom_punch` or whip base. Family, only where a
  moment earns a sound: `swish_1` on the 1-2 real turns, `riser_2` ending on
  the payoff word after a 100-250 ms stop-down, `impact_1` on the payoff;
  slams land silent. At most 2 sounds per short, zero by default.
- **Music (owner's song only, when on):** -18 dB ducked, with the stop-down.
- **Targets:** change every 0.25-0.45 s; hook at or before 0.3 s; 4-6 hero moments.
- **Golden traits:** type lands on almost every stressed word (silently; sound only on the few that earn it), and the payoff is the biggest thing in the short.

### Cinematic Doc

- **Use for:** archival talks, history, predictions, emotional stories.
- **References:** gum-01 (two-frame exposure-flash cuts with clicks, light
  leaks, glow title), gum-08 (teal/orange archive, piano bed), orig-16 (red
  keywords behind the subject, film-burn leak, accelerating push), orig-17
  (low-key grade, grain), orig-10 (riser into a beat drop).
- **Base:** `apply_look('cinematic_doc')`, captions `motion_look` `clean`
  (sentence case) with serif hero words.
- **Canvas:** full-bleed for modern sources; 4:3 archival on a 4:5 card at
  88% width or more (about 62% of the height, `box` [0.06, 0.175, 0.94,
  0.794]) over a `background_style='blur'` backdrop with grain and vignette.
- **Type:** Inter Tight captions; `glow_title` or `chapter_title` in Bodoni
  Moda or Playfair italic; accent butter #F7E499 or red.
- **Grade:** teal/orange low-key or warm archival, grain, vignette, highlight bloom.
- **Camera (optional, only where earned):** a slow `push_in` (strength
  about 0.06 over 4-8 s) on the take that carries the story's weight, not on
  every take; Ken Burns on every still; rare `punch`.
- **Signature:** `glow_title` for the premise or a date; `timeline_steps` or
  `chapter_title` for a time jump (1983 to 2010); `image_card`/`photo_stack`
  archival evidence; `quote_card` for the payoff line; `focus_spotlight` on
  one face in a group; `light_leak`/`film_burn` at the turn.
- **Transitions + sound:** `dip_white` 0.2 s or short flash base. Family, only
  where a moment earns a sound: `shutter_2` as an archival photo arrives on
  screen (never on the spoken word 'photo'), `whoosh_soft_1` into a title,
  `riser_1` into the turn, `impact_1` on the payoff. At most 2 sounds per
  short, zero by default.
- **Music (owner's song only, when on):** -20 dB ducked, swelling into the
  payoff.
- **Targets:** change every 0.5-0.8 s with a structural event every 2-3 s;
  hook at or before 0.6 s; 2-3 hero moments.
- **Golden traits:** graded and textured; the past feels like film, not a webcam.

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
- **Camera (optional, only where earned):** a `punch` on the few real hits
  (shake on the biggest at most); a `pulse` on a real beat. Never a landing
  after every cut.
- **Signature:** `word_slam` with `strobe` or `ghost` entrances; `text_scramble`
  or `typewriter` for numbers and technical terms; `counter` for a spoken
  number; red behind-subject keyword; `glitch_burst` at the turn.
- **Transitions + sound:** white flash on drops, glitch, hard cuts on speech
  onsets. Family, only where a moment earns a sound: `glitch_1` or `glitch_2`
  under a real glitch transition, `typing_1` under a typewriter, `riser_4`
  into the payoff and `impact_1` on it (or `heartbeat_1` on a tense pause); a
  counter stays silent. At most 2 sounds per short, zero by default.
- **Music (owner's song only, when on):** -18 dB ducked, with a stop-down
  before the payoff.
- **Targets:** change every 0.3-0.5 s; hook at or before 0.4 s; 3-5 hero moments.
- **Golden traits:** stark, red only where it matters, and any sound it uses hits with weight.

### Clean Data

- **Use for:** numbers, money, products, business mechanics, how-to.
- **References:** orig-13 (UI planes, song card, typed comment), orig-11
  (Stripe-style revenue cards, spread caption rows), orig-03 (product UI on
  a grid stage), orig-09 (UI cards and charts around the subject), orig-12
  (gradient cards).
- **Base:** `apply_look('clean_minimal')`, captions `motion_look` `clean`
  (or `box` over busy footage).
- **Canvas:** full-bleed, or a card on a designed gradient during data beats
  (`background_style='vertical_gradient'`, white to periwinkle or royal blue
  #1C4DB0); UI cards float beside the head.
- **Type:** Inter Tight Bold, tight tracking; accent periwinkle #6D93D6 or
  a growth green; numbers big.
- **Grade:** high-key, clean, neutral, light grain.
- **Camera (optional, only where earned):** a `pulse` when the key card
  lands; a `push_in` only on a take that builds to a number. Never a move on
  every cut or take.
- **Signature:** `counter`, `stat_card`, `bar_compare`, `line_chart`,
  `progress_ring`, `timeline_steps`, `checklist`, `search_bar`, `notification`,
  `chat_bubbles`, `post_card`, `versus_split`, `arrow_callout`. Only for
  numbers and events the source actually states; never fake a real person's
  message or post.
- **Transitions + sound:** short whips and swipes. Family (UI), only where a
  moment earns a sound: `click_1` or `click_2` on a visible press, `typing_2`
  under `search_bar`, `cash_register_1` on a payment card, `ding_1` on a
  notification card, `pop_1` as a card lands; counters count up silently.
  At most 2 sounds per short, zero by default.
- **Music (owner's song only, when on):** -20 dB ducked.
- **Targets:** change every 0.35-0.6 s; hook at or before 0.6 s; 3-5 data beats.
- **Golden traits:** the number becomes the picture, and its settled figure lands (one sound at most).

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
- **Camera (optional, only where earned):** a `punch` on the one or two
  keywords the story turns on; zoom-blur or whip into B-roll at a real turn.
  Never a landing after every cut.
- **Signature:** `hook_title` (treatment `glow`) beside the face; `glow_title`;
  `phrase_build`; `arrow_callout` or `circle_highlight`; `photo_stack`;
  `focus_spotlight`. CTA templates (`follow_cta`, `save_cta`, `comment_cta`)
  only when the run brief asks.
- **Transitions + sound:** `zoom_punch` or whip base plus `flash_transition`
  or `light_leak`. Family, only where a moment earns a sound: `swish_1` on the
  1-2 real turns, `riser_2` into the payoff and `impact_1` on it. At most 2
  sounds per short, zero by default.
- **Music (owner's song only, when on):** -18 dB ducked.
- **Targets:** change every 0.3-0.5 s; hook at or before 0.4 s; 3-5 hero moments.
- **Golden traits:** glowing words build beside the face, so the hook reads before the sentence ends.
