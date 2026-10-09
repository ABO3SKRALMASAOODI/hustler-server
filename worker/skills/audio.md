# audio — sound edited to picture: the four layers, the music bed, the sound kit, cue timing, mixing, loudness

## Editorial decision principles

Premium reels are never silent between words. A music bed sits 13–20 dB under
the voice, and a sparse, structural layer of sound effects is edited to the
picture: a whoosh under a graphic entrance with its peak on the landing, pops
and ticks on reveals, typing under a typewriter, a shutter on a photo, a low
hit on a hero landing, a riser into the payoff, a short stop-down before a
reveal. Intelligibility of the voice always wins the mix.

- Every cue binds to a visible or narrative event and lands ON its frame.
- Music and sound design are the default for short-form reels; a dry passage
  is a deliberate choice for a specific moment (an admission, a joke that
  needs room), not the whole piece.
- Never a whoosh on every caption; structure, not wallpaper.
- Digital silence in a finished reel reads as broken.

## Evidence to inspect

Inspect the current EDL (music, sfx, voiceover, volume), every motion graphic
and its owned cues, junctions and hero moments in the program map, dialogue
windows (`get_kept_transcript`), the track's structure and beats
(`get_audio_analysis`), the rendered AUDIO CHECK (loudness, peaks, dead air)
and any bounded listening evidence.

## Strong treatment patterns

FOUR DISTINCT LAYERS — never confuse them:
1. The ORIGINAL footage's audio (the speaker): `set_volume` on SOURCE-time
   spans. A spliced scene's own audio mutes with `set_insert_window(id,
   mute=true)`; image inserts are silent.
2. MUSIC: a bed under speech, or the lead on a speechless video. Place from
   the music library when its tools are listed (read music), from the user's
   upload or link, or from a named song. `add_music` defaults to a -18 dB
   auto-ducked bed under speech and a -4 dB lead with no duck where no speech
   survives. Change track with `swap_music`; refit with `set_music_fit`
   (start/end, loop, fade, offset, duck_mode); remove with `remove_music`.
   Music start/end are OUTPUT-timeline positions.
3. SOUND EFFECTS: `add_sfx` at a POINT in output time. A cue fires once,
   never loops, never ducks. Retime with `move_sfx`, delete with `remove_sfx`.
4. VOICEOVER: `add_voiceover` lays uploaded narration over the program,
   ducking everything else.
- To change music/sfx/voiceover level use `set_audio_gain` (kind 'music',
  'sfx' or 'voiceover') — never `set_volume`, which changes the speaker.

THE SOUND KIT — `list_sfx_kit()` lists Valmera's 22 built-in, licence-free
sounds, always available and instant: whoosh_soft, whoosh_hard, swoosh_up,
swish_short, swipe, pop_soft, pop_bright, click_ui, tick, kick, ding, chime,
notification, coin, riser_short, riser_long, impact_soft, impact_hard,
sub_drop, glitch, shutter, typing. Place one with
`add_sfx(storage_key='kit:<kind>', at=<program seconds>, gain_db=...,
purpose=...)`. Motion templates already add their own synced kit cues;
adjust those gains or pass `sfx=false` rather than stacking duplicates.

CUE GRAMMAR — what goes where:
- Graphic or card entrance → `whoosh_soft` or `swipe`, pre-rolled so the
  peak lands on the landing frame.
- Word, icon or list reveal → `pop_soft`, `pop_bright`, `tick` or
  `click_ui`, quiet.
- Typewriter or terminal → `typing`; counter → `tick` run or a `coin` on the
  final money figure; photo or freeze → `shutter`; phone UI → `notification`
  or `click_ui`.
- Hero landing → a low hit: `kick` (word slam), `impact_soft` (reveal),
  `impact_hard` (the single biggest beat), `sub_drop` (gravity, after a
  riser).
- Payoff → `riser_short` (1.2 s) or `riser_long` (2.6 s) ENDING on the
  payoff frame, then the hit.
- Transition → `whoosh_hard` or `swoosh_up` peaking on the cut; `glitch`
  under a glitch.
- Stop-down: 50–280 ms of near-silence before a reveal — dip the bed with
  `set_music_fit` or end a cue early — then land the hit.
- Layering is allowed: a whoosh into an impact on one hero beat is one event.
- Typical density on a 30–60 s talking reel: a structural cue every 2–4 s,
  stacked on hero moments, quieter on reveals. Count purposes, not sounds.

TIMING — PEAKS LAND ON THE PICTURE:
- `at` is when the file starts; its peak arrives later. Pre-roll by the
  sound's attack: whooshes ~40–50% of their length early, risers by their
  full length so they END on the frame, pops/ticks/clicks 0–1 frame early,
  hits 0–2 frames early.
- Tie cues to measured times: word onsets (`get_words` mapped to program
  time), junctions from the program map, graphic landing frames, music
  transients.

LEVELS:
- Voice is the reference. Bed 13–20 dB under the voice (a library bed at
  about -18 to -22 dB, ducked); in speechless stretches the music may rise.
- UI ticks, pops and clicks at -8 to -12 dB; whooshes -6 to -10 dB; hero hits
  -3 to -6 dB but never masking the hero word itself.
- `set_music_fit(duck_mode='smooth')` when the bed pumps or swallows the first
  word after a pause.

OTHER SOURCES:
- `add_web_sfx` and the search/audition/fetch chain find real recordings for
  an exact sound the kit lacks (a crowd cheer, a door, rain). Relay licence
  lines when they carry obligations.
- A sound the user uploads, or the sound off a clip they sent, places
  directly with `add_sfx` / `add_music`.
- Keep real environmental sound when it already tells the truth.
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

MASTERING: `set_master_loudness` normalizes the final mix to -14 LUFS on
preview and export. The AUDIO CHECK measures integrated LUFS, true peak and
dead air — treat its findings as work.

## Common failure modes

- No bed and no sound design on a reel; digital silence between phrases or
  on montage passages.
- Cues whose peak lands after the visual (late whoosh, riser that ends past
  the payoff).
- A whoosh on every caption or graphic; UI pops loud enough to compete with
  speech.
- Bed too loud under the voice, or pumping on short gaps.
- Gains changed through the wrong layer; duplicate cues stacked on a graphic
  that already owns them.

## Verification procedure

Render; read the AUDIO CHECK (LUFS, peaks, dead air, bed level under speech);
compare every cue's authored time with its named event and its landing frame
in dense rendered looks; listen-check (when the reviewer is available) the
opening, a dense dialogue passage, each hero moment, transitions and the
ending. Be able to name the event for every cue; remove orphans.

## Repair ladder

Retime cues to their landing frames → correct gain and ducking → swap a
cue's kind → remove orphan or duplicate cues → add the missing bed or
structural cue → refit music ends → render and review again.
