# Editor playbook

You own one child short at a time: its `project_id`, Look, structure,
brief, `music_effective` (off unless the owner supplied a song for the run)
and, when that is on, `music_song`, the owner's song. Make it the
best short in the batch. You do not spawn agents, touch siblings, export, or
approve your own work. Read [looks.md](looks.md): **Shared grammar**, your
structure, and your Look block. Nothing else is required.

**Budget per short:** about 25-40 Valmera calls, at most 2 renders, at most 3
`wait_for_job` calls per render, one batched `look_at` on source frames and
one rendered `look_at` per render. Savings come from polling, verification
and bookkeeping; never drop a planned beat to save calls. Independent typed
writes (graphics, zooms, sounds) may go back to back without re-reading the
state between them; re-read only after a cut or a rejection.

**Hard stops** (Oct 2026 editors averaged 77 calls and 2.6 renders, mostly
working around broken tools, and all nine shorts were killed): two renders
is the ceiling, never three; an operation that fails twice is not tried a
third way; past about 60 calls, or when the pilot's frame cannot be
reproduced on your child, stop and hand back `blocked` with the call, its
arguments and the error text. A blocked handback is useful work: it is how
Valmera gets fixed.

## 0. Once per editor (reuse for every later short)

- Read **Known Valmera limits** below and the run's `selection/framing.json`
  (the layout proven on this source: box, source rect, erase regions,
  caption anchor, measured numbers). Reproduce it; do not re-solve it.

- `list_motion_templates()`: the live template list with params and sound
  cues. Params change; never guess them from memory or from these docs.
- `list_sound_library()`: the owner-approved real recordings with their use
  and measured hit level (add_sfx levels each against the voice). These are
  the only sounds you use: never `search_sfx`,
  `add_web_sfx` or a music library, and never a song you chose.
- Read a tool's schema before its first use. Do not read Valmera backend
  source code to guess EDL fields; typed tools and `get_edl` show the shapes.

## 1. Read the assignment and check the story

The brief gives the Look, structure, the story in four lines
(viewer question, hook, turn, payoff), a beat sheet with source-time word
cues, and art direction. Call `get_kept_transcript` once and confirm:

- The first 1.5 s answers "why keep watching?" If the child opens on routine
  interviewer setup, tighten inside the selected range with `keep_segments`
  so it starts closer to the answer; a faithful on-screen context line may
  carry the question.
- Frame 0 is clean: the speaker faces camera, the first audio is a whole
  word (not the tail of the previous one, not "if somebody was like,"), and
  the hook plays as one take (no jump cut in the first 1.5 s, at most one in
  the first 3 s). Move the start to the next clean onset when it is not;
  the PICTURE CHECK names an opening on closed eyes or mid-sound with the
  nearest clean start (it never moves the cut).
- Keep tools place every new cut edge audio-safe (out of words, onto the
  quietest point within ~80 ms) and report it as AUDIO-SAFE CUTS; a cut
  that "joins running speech" has no pause near it — re-cut at a breath
  or sentence end unless it sounds clean. snap_to_words:false keeps exact
  times for a deliberate stutter.
- The turn changes or deepens the idea and the payoff resolves it. Stop on the
  strongest sentence; the payoff needs 0.8-1.5 s after its last word before
  the end card (a reaction button 1.0-1.5 s, or none): the natural tail
  first (the brief's end leaves it), else `add_freeze_frame`
  audio_mode='hold' (the COMPOSED last frame — card, type and all — over the
  source's room tone; check it on render 1), or slow the last ~0.4 s of air
  with `set_speed` 0.5x. Never audio_mode 'continue' for a payoff hold: it
  plays on under the raw source frame.
- Trim for rhythm: cut low-information connectors ("it was like a lot of
  things and"), keep 150-250 ms at sentence boundaries, never drop an
  article inside a clause.
- Keep every qualifier ("I think", "probably", "in our case"). Never splice
  words into a claim the person did not make, reorder causes, or turn caution
  into certainty. Cuts remove setup and filler, not meaning.
- The editorial program fits 10-40 s (montage editorial at most 25 s); the
  native 5 s Valmera ending is added at export and must not be trimmed.

If the story fails these checks, say so in the handback instead of styling it.

## 2. Beat sheet in output time

After any cut, read the program words once more (`get_kept_transcript`) and
write the beats you will execute, in output seconds:

```text
hook    0.00  word_slam tier='hook' "Every computer / has used *weird type*" kicker "Steve Jobs, 1983" 0.15-2.6 (his strongest line as a headline that owns its zone: captions wait; names him, no lower third; silent)
hero 1  6.42  "garbage"   word_slam serif, start 6.22 (lands +0.2, silent; not in the hook)
turn   14.80  "Lisa"      image_card Apple Lisa photo + label "Apple Lisa, 1983" (identifies the product; silent)
hero 2 18.60  "30, 40 fonts" counter value '30–40' label "fonts on one screen" (counts into each figure on its word; silent)
list   26.90  "let's get…" list_build lead "Let's get" rows proportionally spaced fonts / multiple fonts / graphics (each row on its onset, the newest accented; clears on the payoff; silent)
payoff 33.60  "without one" phrase_build 2 tiers "writing a paper / WITHOUT *ONE*" (the largest accented lockup; silent: the type is the payoff); hold 0.8 s after the last word
budget  6 graphics in 36 s (one per ~6 s, no body gap over 8 s), ~35% of the runtime, 2 type roles, 1 accent
camera  none: steady frame, every jump cut left bare
sound   none: nothing on screen calls for one (zero is the podcast default); music off (no owner song)
```

Most beats are silent and the camera is steady: that is the default, not a
gap. Every graphic in the sheet adds what the captions cannot (a number, a
contrast, an identification, evidence, an image); the captions carry the
rest of the words. Each zoom or sound in the sheet names the moment that earns it; a
sound also names its visual partner (what changes on screen within ~50 ms
of its hit) and never sits on the payoff word's onset.

Every timestamp comes from a tool result. Readable frames land on the word's
audible onset (0-3 frames early); `word_slam` lands 0.2 s after its start;
`phrase_build` rows quote the transcript and land on their spoken onsets
by themselves (`at` times only matter for a row nobody says). Check shot changes with
`get_shots`, and batch `look_at(times=[...])` into one call to see the faces
and the negative space you will design into.

## 3. Execute in a few deliberate calls

Order matters: cuts first (they move every later time; the child already
starts 9:16 from the SOURCE LAYOUT), then the pilot's card (card end =
program end), THEN `erase_region`, stock overlays and captions, then
designed beats, camera, transitions and sound. If you must re-cut later,
recheck every output-timed item and the card's end.

| Step | Calls | Notes |
| --- | --- | --- |
| Cuts | 0-1 `keep_segments` | only to tighten inside the range |
| Look | 1 `apply_look(name)`, never with a music option | sets caption look, grade, grain and base transitions, and no sound (never pass `transition_sounds=true` unless the brief asks); read what it set |
| Captions | 1 `add_captions(mode='from_transcript', style={...})` or `set_caption_style` | `style.motion_look` per Look; `emphasis_words` = the meaning-bearing words; keep the active-word accent if the brief asks |
| Frame | 1-2: the pilot's layout (`set_picture_card`), or `auto_reframe`/`set_frame` for full-bleed | full-bleed with a per-shot `focus_track` (source seconds), or the card from `framing.json` / looks.md **Card geometry** with `background_style` (`blur` or a gradient), grain and vignette. On a call source (SOURCE LAYOUT in `get_video_info`) `auto_reframe` and `source='auto'` frame the call's picture at <= 2x (an 'auto' card narrows rather than cut the face) and name the `erase_region` for the host's self-view: run that erase first. `auto_reframe` and an `'auto'` card follow a speaker who leans or steps inside a shot (still while they sway, a glide only when a still frame would cut the head; a turned close-up keeps its nose side clear) — a hand-written `set_frame` aim is still and drops that. A burned-in screenshot or screen share the host reads from is never cropped through and never shown without the speaker for more than a second while they talk: act on SCREEN INSET / NO FACE IN THE CROP notes with the call they name (a speaker + screen stack per camera shot, `panels=[{..., source:'auto'}, {..., source:'inset'}]`, so the speaker stays on screen while they talk — the speaker panel is solved from the face track: whole head, chin and hair margins, lead room, the screen box kept out where any framing can, and a gutter between the panels that captions never sit on (they take a free band, usually above the speaker panel); `set_picture_card(source='inset')` alone only for a beat — over speech it reports NO FACE ON SCREEN), and keep captions and graphics clear of the card. A card's fade/lift entrance and exit dissolve the whole card with the full-frame shot (never a frame of bare canvas); on a cut use `'none'` |
| Headline | 0-1 `set_editorial_graphic(kind="headline", speaker, text)`, or with a card 0-1 `add_motion_graphic(template='headline', start=0, params={text, kicker})` | the editorial headline for headline-conversation (verified speaker first; it never moves); on a card layout whose band also carries hero lockups, the persistent `headline` template instead: end and y omitted, it sits in the band above the card and yields to every motion-graphic lockup there by itself (not to add_text or typography scenes, so band lockups are motion templates), so the band is never empty for seconds |
| Designed beats | 2-5 `add_motion_graphic` | one per beat that EARNS ITS PLACE: about one hero graphic per 6-8 s at most, under ~50% of the runtime, at most 3 type roles and one accent; never a lockup or typewriter of the words being heard, never a spoken list as rows of text (insert the items instead); silent by default; `sfx=true` only on the hook, the payoff or a graphic showing a real-world action; `purpose` names the beat, stable `id` |
| Camera | 0-3 `add_zoom` (optional) | only where a moment clearly earns a move (the payoff word, a real turn between ideas, a genuinely jarring jump cut); zero is fine; never on every cut, take, sentence or hero moment. Modes per the Look (schema names) |
| Cut hygiene | none in MCP | `conceal_jump_cuts` is not in the MCP tool list (Oct 2026): leave a same-shot jump cut bare, land a graphic change or B-roll on it, or re-cut at a breath; none in the first 1.5 s, at most one in the first 3 s |
| Transitions | 0-1 `set_transitions` | base style, `scope='scene'` |
| Sound | 0-2 `add_sfx(storage_key='sound:<id>', at=...)` with gain_db unset (optional) | zero by default; at most 1-2 per short (template cues count), each on a structural moment with a visual partner, from your Look's family, none on a payoff word's onset, no literal puns, none repeated within ~3 s; the tool levels each against the voice and its MIX/CHECK lines say where it sits |
| Music | 0-1 `add_music` (after `fetch_url` for a link) | only the owner's song, only when `music_effective` is on; bed -20 dB ducked; a montage's bed leads |
| Sound without music | 0-2 | montage or action opener with music off: natural sound, design around speech, one or two library sounds, or a flag (looks.md) |

Plain layer changes you already know the shape of may go together in one
`apply_edit_batch`; prefer typed tools when unsure, and place library
sounds (`sound:<id>`) with `add_sfx`, not in a batch. One child's writes are
serial. On a `REJECTED` result, correct the arguments from the message and
retry once; a transient error (for example "shard is busy") gets one retry
after a short pause, then report it.

### Do

- Fill the frame: full-bleed when the face crop needs at most 2x upscale;
  otherwise a card from **Card geometry** (at least 0.54 of the canvas; the
  card's bottom at or above y 0.80) on a `blur` or gradient backdrop.
  Archival footage is accepted as is; type stays sharp.
- Captions with a cap height of 3-4% of frame height, in face-free space
  (inside the card's lower third when there is a card), stable within a
  shot; 1-2 accent words per phrase.
- Hero type at 8-30% of frame height on the exact word; hold 0.8-1.6 s;
  exits faster than entrances.
- A hook written from the clip's strongest line or statistic, with the
  speaker's verified name in its kicker or the headline band.
- A spoken list shown as items (a real photo or clip per item, 0.3-0.6 s
  on its onset), else one accumulating `list_build` of the noun phrases
  said; a triad ("Let's get… / Let's get… / Let's get…") the same, its
  opener as the `lead`; a named product labelled for what it is
  ("Apple Lisa, 1983") and shown when an image exists.
- The payoff as the largest accented lockup: number and noun together,
  rhyming with an earlier setup device where one exists.
- A number lands ON its spoken word, never before it (the write sets a
  counter's `land` on the onset and moves a number slam onto it). A count
  really counts, 0 -> value over ~0.4 s into the word (the window opens
  ~0.45 s before it, so nothing shows across the setup); a spoken range is
  value '30–40', each figure on its own word; `style='reveal'` (a hard cut
  on the word) stays for a number that must not move before it lands.
- Every thesis line, list, triad, name and number gets a beat that adds
  information; no body stretch past ~6-8 s without one (the review's
  dead_stretch note names the line and a beat for it).
- `layer='behind_subject'` only inside one continuous shot (no cut in the window).
- B-roll that shows the exact noun or action, licensed, with provenance in the
  handback; every still pushes or pans.
- A sound only where something meaningful happens on screen (a hero
  landing that earns it, a real turn or B-roll entry, the payoff, or a
  real-world action shown: a shutter on a photo being taken, typing under
  typed text, a click on a press, a cash register on a payment shown),
  zero by default and at most 1-2 per short, never the same sound within
  ~3 s, from your Look's family and at the level the tool sets; a stop-down of 50-280 ms before a
  reveal is a choice, longer digital silence is a defect unless the passage
  is flagged for the owner's song.

### Don't

- A picture band on a flat black canvas, or a fade from black on frame 1.
- The reflexive opening whoosh; a ding, pop or shutter on the payoff word's
  onset (it masks the word); a literal sound pun (a shutter on the word
  'pictures', a cash register on 'money' with only type on screen); a
  sound with nothing changing on screen within ~50 ms; a hand-set gain the
  MIX line calls inaudible or too hot.
- A whoosh on every caption; sounds on captions, landing zooms, `push_in`,
  pulses on speech, jump cuts or ordinary cuts in the conversation; any
  sound outside the approved library; a punch every sentence (keep 4 s
  between punches); a zoom across a cut; a landing on every cut or a
  `push_in` on every take; any zoom or sound you cannot name a reason for
  (they make the short look childish — leave the frame steady instead).
- Graphics over the face for more than 1 s, under the corner brand mark,
  or below y 0.80.
- A generic question hook; a hook that shows a word a later graphic slams;
  a broadcast lower third over a famous face; a product name set as
  "Name:"; a graphic that only re-typesets the words being heard; more than
  3 type roles or a second accent colour; a payoff number without its noun.
- Music of any kind when `music_effective` is off (no CC0 library bed, no
  song you chose), anything but the owner's song when it is on, or a
  montage of digital silence (use **Sound without music**).
- Any change to the corner mark or the native end card.
- Numbers, quotes, notifications, chats or posts the source does not support;
  a reference person's name or identity.
- Downloading previews to re-encode, local ASR, re-uploading or re-indexing
  the source, or hundreds of frame dumps.

## 4. Render once

`render_preview(quality='approval')` renders the complete edit and now waits
longer before returning. If it still hands back a running job, call
`wait_for_job(job_id)` at most 3 times. If it is still running after that,
the render lane is slow: wait about 2 minutes outside Valmera (`sleep 120`
with `run_in_background`; the harness blocks a foreground sleep) before
each further check and mention the delay in the handback. Never start a
second render of the same version, and never render after each operation.
Render 1 is the complete edit, not a framing test: the framing was proven
in the pilot.

## 5. Self-check, then fix only the weakest moment

1. Read the render result: its PICTURE CHECK, CAPTION CHECK, MEASURES
   line and advisories (`render_preview(report=true)` re-reads them for the
   existing preview without rendering). `watch_video` is for watching the
   whole thing: its reply carries the link, one frame sheet and the sound.
2. One `look_at(rendered=true, output_times=[...])` with the hook (0, 0.3 s,
   1.0 s), every hero moment, the densest caption and the payoff (up to 8
   times in that one call), then `native_resolution=true` frames at the hook
   and the payoff in one call (one image per time, up to 4 per reply; it
   names any it did not return; previews are 720x1280). Check that every
   hero and the payoff look as intended in the pixels (an Oct handback
   described a node graph the render showed as an empty box).
3. `audit_captions` once (heard-but-unshown words are defects, not
   advisories) and `review_audio` on the opening and the joins (up to 6 clips
   per call); read the render's warnings.
4. Copy the render's MEASURES numbers into the handback: picture area
   (and upscale), the hook headline's cap height and the captions' (share
   of frame height), the payoff's cap height against the largest other
   lockup; add the longest stretch without a beat. Picture area under the
   Look floor or a hook headline under 1.2x the captions after following
   the pilot's recipe is a recipe or Valmera problem, not something to
   re-render around: hand back `blocked` with the numbers.
5. Measure against your Look's targets: hook at or before 0.6 s, visual
   change rate, hero count, picture area, sounds within budget and spacing
   (list each with its time, on-screen partner and level; `audit_audio_mix`
   reports each sound's level against the voice and its placement checks),
   no digital silence, payoff held 0.8-1.5 s after the last word.
6. Read the render's VERIFICATION ADVISORIES (quote the ones you keep in the
   handback: reviewers cannot re-read them without re-rendering): the EARN ITS PLACE note lists
   a generic or spent hook, a fragment or jump-cut opening, graphics past
   the budget or restating the captions, extra type roles or accents, a
   short payoff hold and colliding sentences, each with a fix. Act on the
   ones that hurt this short (advisory: keep if intentional); none of them
   is ever fixed by adding a zoom or a sound.

Fix real defects (clipped words, collisions, unreadable or wrong captions,
digital silence, a sound with no on-screen event, an exposed edge or
one-frame pop) and the single weakest designed
moment. Re-render only if the fix changed pixels or sound that matter;
two renders is the ceiling.

## 6. Hand back

Return a note of at most 10 lines plus `project_id`, `edl_version` and the
preview link:

```text
s07 · kinetic-poster · fast-conversation · EDL v14 · 1 render
Hook: hook-tier word_slam by 1.1s ("Every computer has used weird type", kicker Steve Jobs, 1983), silent
Heroes: 6.4 garbage slam; 14.8 Apple Lisa photo + label; 18.6 counter 30–40 fonts; 26.9 "Let's get…" list_build
Payoff: 33.6 "writing a paper / WITHOUT ONE" lockup, held 0.8s after the last word
Camera/sound: no zoom, no sound; music off (no owner song)
Measured (render MEASURES): card 0.54 (1.9x); hook cap 7.2% vs captions 3.1%; payoff 9% > slam 6%; longest beatless 5.8s
Targets: change ~0.4s, 3 heroes, graphics ~30% of runtime
Weakest: 9-13s "the fonts were…" setup with only captions (the next pass: an image of an early screen)
Assets: Lisa photo (Wikimedia, CC BY-SA 4.0, credit in handback)
```

Optionally attach a handback JSON (`music`, `broll`, `required_credits`,
`rights_note`, `headline`, `kept_transcript`, `kept_source_ranges_s`) so the
manifest is complete. `music` names the owner's song, or reads `none - owner
to add a song when posting (montage <start>-<end> s)` for a flagged
passage. Do not self-score.

Save the note as `candidates/<id>-note.txt` and the JSON as
`candidates/<id>-handback.json` in the run folder (never a session
scratchpad), then record it yourself: `run.py candidate --run-dir <run>
--short-id <id> --editor <name> --preview <asset or job> --edl-version <n>
--renders <n> --note-file ... --handback ...`. It works whether or not you
were given a slot (`queued`) and for a fix pass (`fix`); never wait for a
slot. A `blocked` handback is recorded the same way, its first line
`BLOCKED: <call> -> <error>`, so the coordinator can file it.

## Known Valmera limits (Oct 2026; the live schema wins, delete a row when fixed)

| Symptom | Working path |
| --- | --- |
| a tool's schema in YOUR client lacks a parameter these docs name (e.g. `set_picture_card` `source`/`panels`/`follow`, `add_freeze_frame` audio_mode 'hold') | your client cached `tools/list` when it connected; the server has them (Oct 2026). Start a fresh session to refresh the tool list; until then write the card with `apply_edit_batch` on the `effects` layer, copying the shape from `get_edl` and `framing.json` |
| a close-up face fills the card to its foot: no room under the chin for the captions at your `anchor_y` | the captions keep the anchor (pages of one line in the card's column) and the render and `audit_captions` name it, CAPTIONS ON THE MEASURED FACE; leave ~0.06 of the frame between the chin and the card's bottom edge (a looser source rect, a smaller zoom) or set `anchor_y` below the card |
| a deliberate caption mute (a word the speaker swallows) is reported by the blocking CAPTION CHECK like any heard-but-unshown word | carry the word on a graphic, or keep the mute and say why in the handback |
| write-time size and position estimates for lockups (`word_slam`, `phrase_build`, the payoff) run up to 2-3x off the render | size the payoff and band lockups from render 1's MEASURES line, never on estimates (the `headline` claim alone is measured at write) |
| `erase_region` repaints only its box: a picture-in-picture's frame or shadow outside it stays visible | draw the box over the whole inset, border included; check the patch on rendered frames once |
