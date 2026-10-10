# audio — sound edited to picture: the four layers, the approved sound library, when a sound earns its place, cue timing, music only on request, mixing, loudness

## Editorial decision principles

The voice is the program. Sound design is sparse and professional: a sound
goes only where something meaningful happens ON SCREEN, and most of a
talking reel carries no added sound at all. Music is never your choice to
make: it goes in only when the user asks for it or supplies a track.
Intelligibility of the voice always wins the mix.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

SOUND EFFECTS IN A PODCAST OR TALKING SHORT DEFAULT TO ZERO (owner, Oct
2026): at most 1-2 per short, each on a structural moment (the payoff, a
real section change) with a visual partner within ~50 ms of its hit (a
graphic landing, a B-roll entry, a real-world action shown). Never a
reflexive opening whoosh, never a bright sound (ding, pop, click, shutter)
on the onset of a payoff or emphasis word, and never a literal sound pun: a
shutter on the word 'pictures', a cash register on the word 'money' when
nothing on screen is a payment. Leave gain_db unset: add_sfx levels each
library sound against the measured voice at its hit and reports where it
sits.

- A sound earns its place only on a designed graphic landing, a real section
  change or B-roll entry, the payoff, or a real-world action shown on screen
  (a shutter on a photo being taken, typing under typed text, a click on a
  button press, a cash register on a payment shown) — and it needs a visual
  partner: something on screen changes within ~50 ms of its hit.
- Never a sound on captions, on ordinary cuts inside a conversation (jump
  cuts, angle changes), or on camera moves. Never a whoosh on every caption
  or graphic: sound design is structure, not wallpaper.
- Sparse: a podcast or talking short carries zero sounds by default and at
  most 1-2. Elsewhere at most about one sound every 4–5 s (a ceiling, not a
  target, usually far fewer), never the same sound twice within ~3 s. Zero
  is a fine answer when nothing on screen earns one.
- Match the material and keep one sound family per short; the peak lands on
  the visual frame; the sound sits under the voice so no word is masked, and
  a bright sound stays off the onset of the payoff word itself.
- Never add music on your own initiative. Suggesting in the reply that a song
  would help is fine; choosing one is not.
- No digital silence: under silent inserts, stills or a no-dialogue passage,
  keep the source's natural sound or design the passage around speech.

## Evidence to inspect

Inspect the current EDL (music, sfx, voiceover, volume), every motion graphic
and any owned cues, junctions and hero moments in the program map, dialogue
windows (`get_kept_transcript`), what the user asked for about music, the
track's structure and beats when a track exists (`get_audio_analysis`), the
rendered AUDIO CHECK (loudness, peaks, dead air) and any bounded listening
evidence.

## Strong treatment patterns

FOUR DISTINCT LAYERS — never confuse them:
1. The ORIGINAL footage's audio (the speaker): `set_volume` on SOURCE-time
   spans. A spliced scene's own audio mutes with `set_insert_window(id,
   mute=true)`; image inserts are silent.
2. MUSIC: only when the user asked for it or supplied a track (read music).
   `add_music` defaults to a -18 dB auto-ducked bed under speech and a -4 dB
   lead with no duck where no speech survives. Change track with
   `swap_music`; refit with `set_music_fit` (start/end, loop, fade, offset,
   duck_mode); remove with `remove_music`. Music start/end are
   OUTPUT-timeline positions.
3. SOUND EFFECTS: `add_sfx` at a POINT in output time. A cue fires once,
   never loops, never ducks. Retime with `move_sfx`, delete with `remove_sfx`.
4. VOICEOVER: `add_voiceover` lays uploaded narration over the program,
   ducking everything else.
- To change music/sfx/voiceover level use `set_audio_gain` (kind 'music',
  'sfx' or 'voiceover') — never `set_volume`, which changes the speaker.

THE SOUND LIBRARY — `list_sound_library()` lists the owner-approved real
recordings (CC0, no attribution) with when to use each and its measured hit
level.
Roles and ids: whoosh (`whoosh_soft_1`, `whoosh_soft_2`), swish (`swish_1`),
impact (`impact_1`), riser (`riser_1` to `riser_4`), shutter (`shutter_1`,
`shutter_2`), typing (`typing_1`, `typing_2`), click (`click_1`, `click_2`),
pop (`pop_1`), tick (`tick_1`), ding (`ding_1`), glitch (`glitch_1`,
`glitch_2`), cash (`cash_register_1`), heartbeat (`heartbeat_1`). Place one
with `add_sfx(storage_key='sound:<id>', at=<program second it HITS>,
purpose=...)` and leave gain_db unset: the tool levels the recording against
the measured voice at its hit and reports it in a MIX line (see LEVELS).
Motion graphics
are silent by default; `add_motion_graphic(..., sfx=true)` opts one moment
in by mapping the template's sound roles onto library recordings (roles with
no approved recording are skipped). Read `get_edl` (sfx) before adding a cue
so a graphic's owned cue is never doubled.

CUE GRAMMAR — what may get a sound, and with what:
- A designed graphic or card landing → `whoosh_soft_1` or `whoosh_soft_2`
  with `at` on the landing frame; a quick word or element flick →
  `swish_1`.
- A real section change or B-roll entry with a designed junction → `swish_1`
  for a whip or zoom punch, a soft whoosh for a slide or light leak, `glitch_1`
  or `glitch_2` only under a deliberate glitch transition.
- The payoff or the single biggest landing → `impact_1`, at most once per
  short (its weight is in the sub, so it may sit under the payoff word); a
  riser (`riser_1`–`riser_4`) may lead into it and must END on the frame. A
  bright sound (ding, pop, click, tick, shutter, cash, glitch) never lands
  on the onset of the payoff or an emphasis word: the word is the payoff,
  and a ding there masks it. Let the type carry it, or put the sound in the
  speech gap after the line where the picture changes. `heartbeat_1` only for a tense pause or emotional beat that the
  picture holds.
- Real-world actions shown on screen → `shutter_1`/`shutter_2` on a photo
  being taken or a freeze; `typing_1`/`typing_2` under typed text;
  `click_1`/`click_2` on a visible button press; `pop_1` on a bubble, emoji
  or list item appearing; `tick_1` on a counter or timeline step; `ding_1`
  on a notification shown; `cash_register_1` on a payment shown.
- Never a literal sound pun: the sound names an action ON SCREEN, never the
  word being said. A shutter on the spoken word 'pictures' or a cash
  register on 'money' with only text on screen is the childish version.
- Repeating template cues (a counter's tick run, a tick per letter) break the
  ~3 s rule: leave that graphic silent and give its settled figure one sound.
- One family per short: soft whooshes plus one impact for a talking reel;
  clicks, pops and a ding for a product or UI short. Do not tour the library.
- Stop-down (optional): a beat of room before a reveal makes the landing hit
  harder. With no music, keep the speaker's own 0.1–0.3 s pause before the
  reveal word and place no cues inside it. Under a bed there is no dip
  control and a sound effect cannot be shortened, so split the bed:
  `set_music_fit(id, end=reveal − 0.15, fade_out_s=0.05)`, then `add_music`
  with the bed's own storage_key, read from `get_edl`, at start=reveal,
  offset_s = the bed's offset_s + (reveal − the bed's start), counting an
  unset offset as 0, fade_in_s=0 and the same gain_db and duck. Skip it
  when that offset would run past the track's end. Never end the bed early
  without restarting it; check the AUDIO CHECK dead-air line afterwards.
- Layering is allowed only on one landing (a riser into the impact on the
  payoff, a title template's whoosh and pop): that is one event. Count
  events, then check the density: about one every 4–5 s at most.

TIMING — PEAKS LAND ON THE PICTURE:
- For a library sound, `at` is the frame it HITS. `add_sfx` starts the
  recording early by its measured peak (about 0.26–0.48 s for the soft
  whooshes, 0.07 s for `swish_1`, 0.69 s for `impact_1`; risers by nearly
  their whole length, so they END on `at`), skips into the file when the
  hit is too close to 0 s, and reports where the peak lands and the span it
  plays. Never pre-roll by hand — that lands the peak early by the same
  amount. Typing is the exception that plays under its action: it starts at
  `at`. `move_sfx` uses the same hit time; in `get_edl` an sfx `at` is where
  the file starts. Template cues follow the same rule.
- Long tails stop at their measured fade point by default (`impact_1` about
  0.6 s after its hit, `ding_1`, `cash_register_1`), so a boom does not ring
  under the next line; `dur_s` changes that (seconds from where it starts
  playing).
- Tie cues to measured times: word onsets (`get_words` mapped to program
  time), junctions from the program map, graphic landing frames.

LEVELS:
- Voice is the reference, measured where the sound hits. `add_sfx` (and a
  graphic's owned cues, and look transition sounds) set each library
  recording's gain from the voice's short-term loudness at its hit — after
  the dialogue leveller on a mastered short — so it sits at its role level:
  whoosh and swish about 8 dB under the voice (6-10), a riser 9 under, ding,
  pop, tick, click, shutter, cash and glitch about 10 under, typing 14
  under, and an impact or heartbeat louder only below 150 Hz (13 dB under
  the voice above 150 Hz, the whole hit at most 8 dB under). The MIX line
  reports the gain and where it sits; a CHECK flags a sound that will be
  inaudible or too hot. An explicit gain_db wins and is reported the same
  way (`set_audio_gain` too), and `audit_audio_mix` lists every sound's
  level against the voice with its placement checks.
- When the user asked for music: the bed sits 13–20 dB under the voice,
  ducked; in speechless stretches it may rise. `set_music_fit(duck_mode=
  'smooth')` when the bed pumps or swallows the first word after a pause.

OTHER SOURCES:
- `search_sfx`, the audition/fetch chain and `add_web_sfx` find a real
  recording online only when the user explicitly asks for a specific sound
  the library lacks (a crowd cheer, a door, rain). Relay licence lines when
  they carry obligations.
- A sound the user uploads, or the sound off a clip they sent, places
  directly with `add_sfx` / `add_music`.
- Keep real environmental sound when it already tells the truth; it is the
  first answer to a silent passage.
- TRENDING platform sounds are licensed inside the platform apps only: cut to
  the user's uploaded copy, export, and they attach the licensed version
  in-app. Never substitute a soundalike silently.

"REMOVE THE BACKGROUND MUSIC": `get_edl` first. Music items → `remove_music`.
None → the music is baked into the source; when `separate_music` is listed,
`separate_music(music_gain_db=-60)` mutes it and keeps the speech (disclose
that separation is strong but not surgical; `remove_stem_mix` restores).
Without it, offer mute ranges, muting everything, or covering with new music.
- "The music" may be sitting in voiceover — fix the layering.
- User CANNOT HEAR the music: check gain, ducking and placement; a
  storage_key starting with 'audio/' is the video's own extracted track.

MASTERING: 9:16, 4:5 and 1:1 programs (and portrait sources left at
'source') are mastered by default; `set_master_loudness(enabled=true)` forces
it on 16:9. Mastering levels the main dialogue to a steady -20 LUFS before
music/voiceover/sfx are mixed — speakers on different mics meet in the middle
and every sound level is set relative to that voice — then normalizes the
mix to -14 LUFS on preview and export. `enabled=false` ships the natural,
unleveled mix: only when the user asks for the original/raw sound. The AUDIO
CHECK measures integrated LUFS, true peak and dead air — treat its findings
as work.

## Common failure modes

- Whoosh wars: a sound on every caption, cut, zoom or graphic; the same
  sound twice within ~3 s; more than 1-2 sounds in a podcast short.
- The reflexive opening whoosh on a hook that is already on screen at frame
  0; a sound with no visual partner within ~50 ms.
- A ding on the payoff word (it masks the punchline it should sell); a
  shutter on the word 'pictures' (a literal pun).
- Sounds where nothing meaningful happens on screen, or a sound that fights
  the material (an impact under a tender admission).
- Music added on the agent's own initiative, or a CC0 library bed chosen
  when the user never asked for generic background music.
- Cues whose peak lands off the visual (a hand pre-roll on top of the
  tool's, a riser `at` set before the payoff instead of on it); a hand-set
  gain the MIX line calls inaudible or too hot.
- Digital silence under stills or a no-dialogue passage.
- Gains changed through the wrong layer; duplicate cues stacked on a graphic
  that already owns one.

## Verification procedure

Render; read the AUDIO CHECK (LUFS, peaks, dead air, bed level under speech
when music exists); `audit_audio_mix` lists every cue with its level
against the voice and its placement checks (visual partner, payoff word,
pun, opening whoosh, the talking-short budget); check the spacing (1-2 in a
podcast short, about one every 4–5 s at most elsewhere, no repeat within
~3 s) and that each peak lands on its frame in dense rendered looks;
listen-check (when the reviewer is available) the opening, a dense dialogue
passage, each sounded moment and the ending. Remove any cue whose event you
cannot name.

## Repair ladder

Remove orphan, duplicate, caption-bound and pun cues → thin to 1-2 in a
podcast short (about one sound every 4–5 s elsewhere) → retime cues to their
landing frames, off payoff-word onsets → clear hand-set gains so the tool
levels them, correct ducking
→ swap a cue for the one that matches the on-screen action → restore natural
sound under a silent passage → refit music ends → render and review again.
