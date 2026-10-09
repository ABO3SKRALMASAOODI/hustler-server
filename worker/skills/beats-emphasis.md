# beats-emphasis — measuring stress and beats, emphasis words, beat-aligned cuts, camera punches and sound on emphasis

## Editorial decision principles

Emphasis is where the speaker's meaning lands; beats are where the music
lands. Premium reels bind their camera moves, hero graphics and sound cues to
those measured instants, 0–3 frames early. Measure first, then let the
strongest words get the strongest treatment and vary the rest so the
rhythm never feels mechanical.

## Evidence to inspect

Inspect measured tempo confidence and the beat grid, vocal stress, energy
rises, numbers and outcome words (`suggest_emphasis`), existing cut
geometry, music placement and the rendered audiovisual result.

## Strong treatment patterns

MEASURE FIRST (`get_audio_analysis`): tempo with a confidence score, the beat
grid, energy peaks and rises, the most vocally stressed words. Pass asset_key
to measure a SONG; when that song is in the edit it also prints beat times in
PROGRAM seconds, ready for cuts, cues, pulses and graphics.

EMPHASIS WORDS: `suggest_emphasis()` lists measured stressed words, numbers
and distinctive terms verbatim. Use them three ways at once on a reel:
- caption accent words (`emphasis_words`, 1–2 per sentence);
- camera: punch-ins on the strongest, alternating framing across jump
  cuts, `pulse` on rhythmic list beats (read zooms);
- hero graphics on the 2–4 biggest, with a sound only where the landing
  earns one (read motion-design and audio).
The strongest word gets the biggest move; adjacent loud words do not each
get a bump — vary strength and skip some.

PUNCH-INS (`punch_in_on_emphasis`): writes one measured pass of punches on
stressed words that survive the cut, aimed at detected faces, with spacing
that avoids adjacent bumps. On a reel it is a valid first pass; inspect it
and hand-tune the top moments (strength, timing, mode) rather than accepting
uniform punches. Explicit count/strength remain available.

BEAT-ALIGNED CUTS (`beat_align_cuts`): snaps internal cut points to the
beat — the SONG the viewer hears when the edit has music. It MOVES existing
cuts; to cut ON every beat, build spans with `keep_segments` from beat times
first, then snap. If the USER tells you the tempo, pass every_s or bpm —
their ears beat the estimator. If a track measures as no-pulse and the
analysis warns the file is broken, say so.

SOUND ON EMPHASIS IS RARE: emphasis alone never earns a sound — captions and
punch-ins stay silent. A library cue lands on a measured instant only when
something meaningful happens on screen there: `impact_1` once, on the payoff
or the single biggest landing; `pop_1` or `tick_1` on a list item that
appears as a graphic, spaced at least ~3 s from the last one; a riser that
ENDS on the payoff (its `at` is the payoff: the tool starts it early). At
most about one sound every 4–5 s (read audio for timing and levels).

Every one of these writes concrete timestamps; any number you quote must come
from the tool result. The reply does not recite them — the timeline shows
them.

MUSIC-LED FORMATS: in a montage, gameplay or music piece the music IS the
structure — cut on its beats, pulse and flash on transients, build to the
peak, one slow-motion beat on the best moment.

## Common failure modes

- Snapping to a weak or incorrect grid; punching every loud word with the
  same strength.
- Emphasis on filler or random nouns instead of meaning words.
- Camera, graphic and sound for one word landing on three different frames.

## Verification procedure

Compare authored timestamps with the measured onsets and beats, then review
changed windows with dense rendered frames and the AUDIO CHECK: each accent
lands on its word or beat, the strongest word reads as the strongest moment,
and the rhythm varies.

## Repair ladder

Correct the grid → move to the actual onset → vary or remove weak accents →
align camera, graphic and sound to one frame → rebuild the cut structure →
verify the complete rhythmic arc.
