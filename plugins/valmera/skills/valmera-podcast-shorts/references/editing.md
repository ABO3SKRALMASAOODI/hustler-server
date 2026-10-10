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

## 0. Once per editor (reuse for every later short)

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
- The turn changes or deepens the idea and the payoff resolves it. Stop on the
  strongest sentence; the payoff needs 1.0-1.5 s of hold before the end.
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
hook    0.00  hook_title "Computers look like *garbage*" 0.15-2.6 (silent; the title is the interrupt)
hero 1  6.42  "garbage"   word_slam serif, start 6.22 (lands +0.2, silent)
turn   14.80  "ten million" counter 0 -> 10M (silent)
hero 2 21.10  marker_text "whether they look great or not" (silent)
payoff 33.60  "look great" word_slam + punch 0.12 (the one move: the line the story turns on); silent: the type is the payoff (no ding on the word itself, it would mask it); hold to 35.0
camera  1 zoom (payoff); every jump cut left bare
sound   none: nothing on screen calls for one (zero is the podcast default); music off (no owner song)
```

Most beats are silent and the camera is steady: that is the default, not a
gap. Each zoom or sound in the sheet names the moment that earns it; a
sound also names its visual partner (what changes on screen within ~50 ms
of its hit) and never sits on the payoff word's onset.

Every timestamp comes from a tool result. Readable frames land on the word's
audible onset (0-3 frames early); `word_slam` lands 0.2 s after its start;
`phrase_build` rows quote the transcript and land on their spoken onsets
by themselves (`at` times only matter for a row nobody says). Check shot changes with
`get_shots`, and batch `look_at(times=[...])` into one call to see the faces
and the negative space you will design into.

## 3. Execute in a few deliberate calls

Order matters: cuts first (they move every later time), then frame, captions,
designed beats, camera, transitions and sound. If you must re-cut later,
recheck every output-timed item.

| Step | Calls | Notes |
| --- | --- | --- |
| Cuts | 0-1 `keep_segments` | only to tighten inside the range |
| Look | 1 `apply_look(name)`, never with a music option | sets caption look, grade, grain and base transitions, and no sound (never pass `transition_sounds=true` unless the brief asks); read what it set |
| Captions | 1 `add_captions(mode='from_transcript', style={...})` or `set_caption_style` | `style.motion_look` per Look; `emphasis_words` = the meaning-bearing words; keep the active-word accent if the brief asks |
| Frame | 1-2 `auto_reframe` (or `set_frame`), `set_picture_card` | full-bleed with a per-shot `focus_track` (source seconds), or a card from looks.md **Card geometry** with `background_style` (`blur` or a gradient), grain and vignette; `auto_reframe` and an `'auto'` card follow a speaker who leans or steps inside a shot (still while they sway, a glide only when a still frame would cut the head; a turned close-up keeps its nose side clear) — a hand-written `set_frame` aim is still and drops that. A burned-in screenshot or screen share the host reads from is never cropped through and never shown without the speaker for more than a second while they talk: act on SCREEN INSET / NO FACE IN THE CROP notes with the call they name (a speaker + screen stack per camera shot, `panels=[{..., source:'auto'}, {..., source:'inset'}]`, so the speaker stays on screen while they talk; `set_picture_card(source='inset')` alone only for a beat — over speech it reports NO FACE ON SCREEN), and keep captions and graphics clear of the card |
| Headline | 0-1 `set_editorial_graphic(kind="headline", speaker, text)` | headline-conversation only; verified speaker first |
| Designed beats | 3-6 `add_motion_graphic` | one per beat, silent by default; `sfx=true` only on the hook, the payoff or a graphic showing a real-world action; `purpose` names the beat, stable `id` |
| Camera | 0-3 `add_zoom` (optional) | only where a moment clearly earns a move (the payoff word, a real turn between ideas, a genuinely jarring jump cut); zero is fine; never on every cut, take, sentence or hero moment. Modes per the Look (schema names) |
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
- A number lands ON its spoken word, never before it (the write sets a
  counter's `land` on the onset and moves a number slam onto it). A count
  starts on the lead-in (~0.45 s before the word); a punchline number, or one
  that is not a growing quantity ('140 characters'), is `counter`
  `style='reveal'`: no count, a hard cut on the word.
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
the render lane is slow: wait about 2 minutes outside Valmera (a shell
`sleep 120`) before each further check and mention the delay in the
handback. Never start a second render of the same version, and never render
after each operation.

## 5. Self-check, then fix only the weakest moment

1. `watch_video(render=false)` on the whole edit at 1x: the hook, the pace,
   the sound, the payoff.
2. One `look_at(rendered=true, output_times=[...])` with the hook (0.3 s,
   1.0 s), every hero moment, the densest caption and the payoff (up to 8
   times in that one call).
3. `audit_captions` once if you changed captions; read the render's warnings.
4. Measure against your Look's targets: hook at or before 0.6 s, visual
   change rate, hero count, picture area, sounds within budget and spacing
   (list each with its time, on-screen partner and level; `audit_audio_mix`
   reports each sound's level against the voice and its placement checks),
   no digital silence, payoff held.

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
Hook: hook_title by 1.1s ("Why fonts were garbage"), silent
Heroes: 6.4 garbage slam; 14.8 counter 10M; 29.0 typeface cycle
Payoff: 33.6 "look great" slam + punch 0.12, held 1.3s
Camera/sound: 1 zoom (on the payoff), no sound; music off (no owner song)
Targets: change ~0.4s, picture full-bleed, 3 heroes
Weakest: 18-20s talking head with only captions (a kicker on "Lisa" would carry it)
Assets: Lisa photo (Wikimedia, CC BY-SA 4.0, credit in handback)
```

Optionally attach a handback JSON (`music`, `broll`, `required_credits`,
`rights_note`, `headline`, `kept_transcript`, `kept_source_ranges_s`) so the
manifest is complete. `music` names the owner's song, or reads `none - owner
to add a song when posting (montage <start>-<end> s)` for a flagged
passage. Do not self-score.
