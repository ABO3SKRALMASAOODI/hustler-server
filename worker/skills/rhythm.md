# rhythm — the clock of the edit: the user's track measured (beats, bars, sections, drops, gaps, kicks, 808s), the speech's own rhythm without one, syllables, lyrics, and measured listening

## Editorial decision principles

The user's track is the clock; never choose a track. With no track the user
supplied, uploaded, named or picked, the SPEECH is the clock: its stresses,
phrase ends and pauses (the prosody grid every punctuation verb binds to).
Never invent a beat over speech.

- `analyze_music` reads the user's track once (cached by its sound):
  tempo, beats, bars, sections (intro / build / drop / break / outro),
  drops, gaps (the held breath before a drop), kicks / snares / hats, 808
  notes and filter openings, each with its program position. Low
  confidence (live, rubato) means onsets only.
- A cut lands ONE FRAME before the kick (the eye sees the cut as the ear
  hears the hit); a payoff lands on a DROP; a gap before the drop is
  silence the picture holds too.
- `get_syllables` splits the words said in a window into timed syllables
  ("try / ing.") for type that builds syllable by syllable.
- `check_speech` LISTENS deterministically: which transcript words are
  audible, what sound no word covers (an "um" the transcript dropped, the
  tail of a word a cut left, a breath), clipping, clicks. Use it instead of
  guessing what the sound is; it never claims a word it is not shown.
- `align_lyrics` puts the user's lyrics on the singer's timing (after
  `separate_music`).

## Evidence to inspect

- `analyze_music` before any music-timed device; its confidence line.
- `get_edl`: the music item (its `start`, `offset_s`), the program length.
- `check_speech` around every cut you are unsure of (before and after).

## Strong treatment patterns

- Trend desk on a user track: cuts on kicks one frame early, the quote on a
  section start, the payoff on the drop, a loop rhyme before the end card.
- Silence then drop: the bed out on the gap, the payoff word on the drop
  (shape_music / plan_to_music do the arithmetic).
- Syllable staircase: each syllable a step, built on its onset.
- Talking head, no track: punctuation and type on stresses and phrase ends;
  nothing pretends to be on a beat.

## Common failure modes

- Cutting to a grid the track does not have (low confidence): use onsets.
- A cut that leaves half a word or an "um": `check_speech` the join; the
  audio-safe keep tools already cut fragments they can hear.
- Opening a short mid-question: starts snap to the sentence's first word.

## Verification procedure

- After a music-timed edit, `proof_device` the cuts: landing within a frame.
- `check_speech` each join you changed: no untranscribed fragment left.
- The ledger line on the write: no sound or zoom was added by the rhythm
  verbs (they never add either).

## Repair ladder

1. Cuts feel late: confirm the one-frame lead; re-plan with plan_to_music.
2. The payoff misses the drop: shape_music fits the track to the payoff.
3. A fragment at a join: keep_segments again (it trims fragments it hears),
   or move the edge by hand to the quiet point check_speech reports.
4. No track: ask the user for one; the speech clock serves until then.
