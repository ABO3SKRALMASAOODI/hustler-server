# motion-design — premium motion graphics: template choice, word-cued timing, layering, sound pairing, authored HTML on the MG runtime, rendered review

## Editorial decision principles

Motion graphics are the main design surface of a premium short. Top
Instagram editors make something visual change every 0.3–0.6 s and give the
2–4 most important spoken moments a designed hero treatment: a giant word on
the word, a number that counts up as it is said, a phone notification when
the speaker describes a message, a circle drawn around the thing they point
at. Valmera's browser motion engine renders these at After-Effects quality;
your job is to choose the right composition, bind it to the exact spoken
frame, give it one clear role, decide whether the moment earns a sound,
and check the rendered motion.

- **Bind every graphic to a word or a beat.** The landing frame of a graphic
  sits on the spoken onset (0–3 frames early), never "somewhere in the
  sentence". A graphic that lands late reads as an error.
- **One leader per moment.** In any half-second one element leads (the hero
  word, the card, the counter). Camera, captions and sound support it. Two
  graphics fighting for the same instant cancel each other.
- **Typography carries the motion.** A tight bold grotesk backbone plus ONE
  accent role (serif italic, script or condensed heavy) and ONE accent
  colour on 1–2 words per sentence. Size ladders of 2:1 to 7:1 between
  connector words and hero words.
- **Entrances are short and graphic; objects spring.** Type pops, ghost-
  snaps, strobes or rises out of blur in 80–170 ms. Springs with overshoot
  are for UI pills, icons, cards and CTAs, not for every caption word.
  Settle, hold perfectly still, exit faster than you entered.
- **Honesty survives design.** A counter shows a number the speaker says or
  a verified source supports; a stat card never rounds up; a notification,
  chat or post card depicts what the speaker describes, with generic names,
  and never invents a real brand's message, endorsement, follower count or
  revenue figure.
- **Restraint is a deliberate passage, not the default.** Holding a
  vulnerable admission on the face with only captions is a choice you make
  for that moment — and the surrounding passages still carry motion.

## Evidence to inspect

- `list_motion_templates()` (optionally with a category: type, data,
  callout, social, layout, transition, texture, cta) for the LIVE library,
  each template's purpose, params and built-in sound cues. Never hard-code
  params from memory: the listing is the contract.
- `get_kept_transcript()` for each phrase's PROGRAM window and its source
  span; `get_words(start, end)` over that source span for exact word onsets.
  For a phrase inside one kept span with no speed change, program onset =
  phrase program start + (word source t0 − phrase source start).
- `suggest_emphasis()` and `get_audio_analysis()` for measured stress and
  numbers worth a hero treatment; `get_edl` (sections motion, sfx, captions,
  texts) for what is already placed and every owned sound cue.
- `look_at(output_times=[...])` on the assembled program for face position
  and clear space before placing anything; the tenths grid gives fractions
  for `y`, boxes and arrow targets.
- Music beats (get_audio_analysis on the placed track) when a graphic
  should land on a transient instead of a word.

## Strong treatment patterns

WHEN THE LIBRARY IS MISSING: the motion tools are absent from your tool list
only when this deployment has no browser renderer. Then cover the same roles
with native tools — `set_typography_scene` for word-cued lockups,
`set_editorial_graphic` for metrics, quotes and chapters, `add_text_behind`
for depth titles, `add_text` with keyframed motion for hero words,
`add_vector_graphic` for arrows and rings — and keep the same timing and
sound grammar.

CHOOSING A TEMPLATE — confirm names and params with `list_motion_templates()`:
- Opening and thesis: `hook_title` for the 1–3 line hook readable by 1.5 s;
  `word_slam` for one hero word on its spoken onset (the most common hero
  moment); `phrase_build` for a short lockup assembled word by word on
  onsets; `glow_title` for a luminous keyword on dark or night footage.
- Reveal grammar: `typewriter` for a typed prompt, definition or terminal
  line (25–40 chars/s with a cursor; `typing` sound under it when it is
  sounded); `text_scramble` for
  a decode reveal (secrets, tech, "the answer is…"); `marker_text` for a
  highlighter sweep behind the key phrase.
- Structure: `chapter_title` at a real section turn ("The problem", "Step
  two"); `timeline_steps` for a spoken sequence of dates or steps;
  `checklist` for a spoken list ticking on item by item; `versus_split` for
  a genuine A-versus-B contrast; `quote_card` for a faithful quotation held
  long enough to read.
- Numbers: `counter` when a figure is spoken (expo-out count-up landing on
  the word); a rapid run of spoken stats as one `word_slam` each with
  `fit='justify'` and the figure over its label (`'*32%* / fewer errors'`,
  pop entrance on the figure's onset); `stat_card` for a metric plus its meaning; `bar_compare` for
  2–4 spoken quantities; `line_chart` for a spoken trend; `progress_ring`
  for a spoken percentage or completion. Numbers come from the transcript
  or a verified source only.
- Pointing: `arrow_callout` and `circle_highlight` aim at a VISIBLE target
  read off the grid; `focus_spotlight` darkens everything except the
  subject or region for a reveal; `lower_third` names a verified person,
  place or role.
- Social and UI (Apple/SaaS-style mockups with an animated cursor; a click
  sound only on a visible press, when sounded): `notification` when the
  speaker describes a message or alert;
  `chat_bubbles` for a retold conversation; `search_bar` for "I searched…";
  `post_card` for a post they reference; `image_card` and `photo_stack` for
  photos and archival stills as floating 3D cards with a shutter.
- Endings: `comment_cta`, `follow_cta`, `save_cta` are native-UI calls to
  action — a typed comment keyword, a follow→following press, a bookmark
  fill — in the last 2–4 s after the payoff has landed, never over it. Use
  one only when the user or brief asks for a CTA, and fill it only with the
  handle, keyword and offer they supplied (owner marketing reels supply them
  in the brief). Never invent a handle, a verified badge (pass
  `verified=false` unless the user confirms it), a comment keyword or a
  promised resource. `save_cta` is the identity-free option.
- Accents: `emoji_pop` for one reaction beat, never a stream of emoji.
- Transitions and texture: `flash_transition` (1–2 frame exposure pop on a
  cut), `light_leak` (warm wash across a section turn), `glitch_burst` (RGB
  split on a tech or twist beat), `film_burn` (cinematic section change).
  These go ON a real junction; one library sound may peak on it when the
  turn earns it (read transitions).

CUING TO THE WORD:
- Find the onset as above, then set `start` so the template's LANDING
  frame falls 0–3 frames (0–0.1 s) before it. A template's description in
  the listing states its landing offset: an entrance that lands 0.2 s after
  start begins 0.2 s before the word; a hard pop lands at start. When the
  listing gives no offset, start 0.03–0.08 s before the onset and check the
  rendered frames.
- `end` is when the moment releases: usually at the next phrase or the next
  leader, rarely more than 2.5 s for a hero word. Omit it to use the
  template's natural duration.
- Pass `purpose` naming the word or event ("slam on 'garbage' at 14.2 s")
  and a stable `id` so later passes can `set_motion_graphic` it.
- Rhythm across the reel: aim for a visual change every 0.3–0.6 s (word
  reveals in motion captions count), a bigger change every 2–4 s (camera
  move, graphic, B-roll, layout) and 2–4 hero moments. Avoid metronomic
  spacing: cluster on dense ideas, let a real admission breathe.

LAYERING AND CAPTIONS:
- `layer='above_captions'` (default for designed moments) puts the graphic
  over the caption track; `below_captions` keeps captions on top (lower
  thirds, textures, background shapes); `behind_subject` composites the
  graphic behind the person's matte — the giant-word-behind-the-head look —
  and needs a person in frame and no cut inside the window.
- Captions and a graphic never say the same words twice. Mute defaults
  differ by template and the listing does not show them, so pass
  `mute_captions` explicitly: true when the graphic repeats the words being
  spoken (a word slam or phrase build on its words, a `hook_title` of the
  spoken hook line, a typewriter of the sentence being said), false when it
  complements them (a counter beside the speaker, a lower third, an arrow,
  a CTA).
- One text system at a time in one region: never stack a hook title, a
  caption page and a lower third in the same band.

LEGIBILITY:
- Size first: hero words 7–20% of frame height, connector words 2.5–4%.
- Place type in measured clear space beside or above the face, never across
  eyes or mouth. On 9:16 keep important type inside x 60–1020 px and y 8–80%
  of the height; the bottom ~15% belongs to the platform UI.
- Contrast from a soft shadow (0 2–6 px 12–30 px at 35–60% black), a
  frosted or dark plate, or the grade — not thick outlines or yellow boxes
  by default. Check bright and dark plates in the render.

SOUND PAIRING — graphics are SILENT by default:
- Most graphics need no sound. Opt a moment in with `sfx=true` only when its
  landing is meaningful on screen — the hook title, a hero landing, the
  payoff, or a graphic that shows a real-world action (a photo card's
  shutter, a typewriter's typing, a UI press's click, a money figure's cash
  register). `sfx=true` maps the template's declared sound roles onto the
  owner-approved library (`list_sound_library`); roles with no approved
  recording are skipped. Owned cues move and delete with the graphic.
- The add_motion_graphic result lists each owned cue as sound@time; in
  `get_edl` they are sfx items whose ids start `mg_<graphic id>_sfx`. Read
  them against the short's budget — about one sound every 4–5 s at most,
  never the same sound twice within ~3 s — and adjust rather than stack: set
  a level with `set_audio_gain(kind='sfx', id=..., gain_db=...)` after the
  graphic's params are final (changing params or the template re-derives
  the cues at their suggested gain), or `set_motion_graphic(id,
  sfx=false)` to drop them.
- Accents: `*one word*` or a `*multi word run*` takes the accent; star 1–2
  words per line. Over a bright shirt or wall, raise `scrim` on
  `hook_title`, `glow_title` and `phrase_build` (a soft dark backing) rather
  than moving the type onto the face.
- A long recording (typing, a riser) placed by hand stops with its event:
  `add_sfx(..., dur_s=<seconds of visible typing>)` trims it with a short
  fade.
- A template whose cue repeats (a tick per counter step or per letter)
  stays silent; give the settled figure one sound instead (`cash_register_1`
  on money, `ding_1` on a result).
- For a moment with no owned cue, place one library sound yourself:
  `add_sfx(storage_key='sound:<id>', at=..., gain_db=<suggested>)` —
  `whoosh_soft_1` or `whoosh_soft_2` pre-rolled into a landing, `swish_1`
  for a quick flick, `impact_1` only on the payoff or the single biggest
  landing (at most once per short), a riser (`riser_1`–`riser_4`) ending
  exactly on the payoff frame. Never a whoosh on every caption or graphic.

TIMING VOCABULARY — name the motion you want, then pick a template param or
author it:
- pop: hard on at the onset (0–40 ms early), no entrance animation.
- ghost-snap: 2–3 frames at 30–50% opacity, then 100%.
- strobe: on 3 frames / off 1 / on 1 / off 1 / on; strobe-off exits.
- rise-blur: 8–12 px rise with blur 6–12 px → 0 over 80–170 ms, ease-out,
  no overshoot — the premium default for phrases.
- per-letter stagger: 33–67 ms per letter, scale ~150% → 100% with blur, for
  a hero word.
- mask or line reveal: a clip wipe over 150–300 ms, expo-out.
- typewriter 25–40 chars/s; scramble/decode over 3–5 frames.
- springs for objects and UI: pills, icons, cards; CTA anticipation → snap
  (1.0 → 1.15 over ~270 ms, snap to 0.93, settle at 1.0 in two frames).
- exits: hard clear or a 130–300 ms fade/blur, always faster than entrances.

AUTHORED HTML (`template='html'`) — for an idea no template covers (a
bespoke diagram, a type lockup in a specific layout, a branded sequence).
Pass the whole composition (style + markup + script) in `html`; `params`
reaches the page as `MG.params` (keys starting `asset_` resolve a project
image storage key to a served URL). The page is `MG.W` = 1080 design px
wide and `MG.H` tall (1920 on 9:16, 1350 on 4:5, 1080 on 1:1, 608 on 16:9);
the engine scales it to the output. Runtime summary:
- `MG.frame(t => {...})` registers per-frame choreography; everything must be
  a pure function of `t` (item-local seconds). `MG.duration`, `MG.fps`,
  `MG.t` are provided.
- `MG.active([[a, b], ...])` declares the moving windows so static holds are
  captured once (large speed win); `MG.box = [x0, y0, x1, y1]` in design px
  (include shadow, glow and overshoot) clips capture to the drawn area.
- Easing and physics: `MG.ease.outExpo|outCubic|outQuart|outBack|snap|...`,
  `MG.tween(t, start, dur, a, b, ease)`, `MG.range(t, start, dur)`,
  `MG.spring(t, start, {stiffness, damping, mass})` (0→1 with overshoot),
  `MG.stagger(i, each)`, `MG.lerp`, `MG.clamp`.
- Applying state: `MG.set(el, {x, y, scale, rotate, rx, ry, opacity, blur,
  bright})` and `MG.anim(el, t, start, dur, from, to, ease)`.
- Type: `MG.split(el, 'words'|'chars')` returns `[{el, text, chars}]` for
  staggers; `MG.fit(el, {width, height, min, max, nowrap})` shrink-fits any
  copy; `MG.typed(text, t, start, cps)` for typewriters.
- Data and drawing: `MG.count(t, start, dur, from, to, {prefix, suffix,
  decimals, ease})` for counters; `MG.draw(svgPath, progress)` for stroke
  draw-on of arrows, circles, underlines and charts.
- Texture: `MG.wiggle`, `MG.noise`, and `MG.rand(seed)` for deterministic
  randomness.
- Determinism rules: never use Date, performance.now, Math.random,
  setTimeout, requestAnimationFrame or network URLs. CSS @keyframes and
  animation-delay are seeked automatically, but prefer MG.frame for anything
  that depends on params.
- Fonts are bundled by family name: Inter Display, Inter, Inter Tight,
  Manrope, Space Grotesk, Montserrat, Poppins, Archivo (variable width),
  Unbounded, Anton, Bebas Neue, Archivo Black, Instrument Serif, Playfair
  Display, DM Serif Display, Bodoni Moda, Pinyon Script, Great Vibes,
  Yellowtail, Caveat, JetBrains Mono; emoji from the system colour font.
- A write is rejected if the page throws or draws nothing; the result
  reports the drawn bounds — compare them with the safe area.

## Common failure modes

- A graphic that lands a beat late, or on a filler word instead of the
  meaning word.
- Two leaders at once: a hook title, a word slam and a caption page all
  animating in the same half-second.
- Hard-coded params from memory instead of the listing, producing rejected
  writes or default copy.
- Springy bounce on every word; slow linear fades; exits slower than
  entrances; elements that keep drifting after they should hold still.
- Small type in the middle of the face, or important type in the bottom UI
  band; thick outlines and yellow boxes used as the only legibility fix.
- A sound on every graphic (whoosh wars), a repeated tick run, or UI pops
  loud enough to compete with speech.
- Invented numbers, fake brand notifications, testimonials or metrics.
- Judging motion from one settled still.

## Verification procedure

1. While placing graphics, `render_preview(complete=false)` encodes only the
   changed seconds and returns RENDER CHECK and caption QA tiles of them:
   judge placement, legibility and collisions from those tiles.
2. `look_at(rendered=true, ...)` reads only a COMPLETE preview of the
   current EDL version — a changed-section proof does not count. Once the
   hook and hero moments are built, call `render_preview(complete=true)`
   (draft quality) once, then `look_at(rendered=true, output_times=[...])`
   with up to 8 dense times per call around each landing — for example
   onset − 0.1, onset, +0.03, +0.07, +0.13, +0.27, +0.5 and the release —
   to judge the entrance, the settled hold and the exit. The geometry-only
   view without `rendered=true` needs no render but contains no graphics,
   captions or grade.
3. Check: lands on the word (0–3 frames early), one leader, clear of the
   face and UI band, readable at phone size on the actual background, any
   sound peaking on the landing frame, exit clean before the next leader.
4. A repair makes a new EDL version: render it complete again before the
   next rendered look, and re-check the opening 0–2 s and every hero moment
   you changed.

## Repair ladder

Retime to the onset → move into clear space or change layer → reduce copy or
enlarge type → remove the competing element → change the template or
entrance → adjust or remove its owned sound cues (`set_motion_graphic`
with `sfx`) → author an html composition when no template fits → render a
complete preview and look again.
