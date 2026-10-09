# music — the bed under every reel: choosing from the library or the user's track, levels, placing the drop, fitting the ends, beat culture

## Editorial decision principles

Premium short-form almost always carries a music bed: it fills the air
between phrases, sets the emotional register and gives the payoff a place to
land. The bed sits 13–20 dB under the voice and ducks; its structure is
edited to the story — a lift into the payoff, room before a reveal, a
button on the ending. Choose by what the video IS and how the speaker
sounds, not by a mood word alone. A dry passage is a deliberate exception for
a specific moment, never "no music" by habit.

## Evidence to inspect

Inspect the treatment and energy arc, dialogue density, the payoff and turns
in program time, candidate tracks' tempo, energy and structure
(`get_audio_analysis(asset_key=...)`), the opening, representative speech,
the ending, and the rendered AUDIO CHECK.

## Strong treatment patterns

SOURCING — in this order:
1. The user's own upload or link — when they provide a song, that IS the
   song (`list_assets(kind='music')`, `fetch_url` for links when listed).
2. The Valmera music library when its tools are listed: the
   list_music_library tool browses 24 licence-free CC0 tracks by mood
   (upbeat, chill, cinematic, corporate, dramatic, hiphop, ambient,
   inspiring) and the add_library_music tool places one as a ducked bed
   (about -18 to -22 dB under speech). When the look you apply offers it,
   apply_look's music option ('auto', a mood or a slug) lays the look's own
   library bed in the same call — check `get_edl` before adding a second
   bed. CC0 means no credit or licence obligation; still name the track you
   chose.
3. A SPECIFIC song they NAME: `find_song(query)` returns candidate links
   (prefer the artist's own or "- Topic" channel, never a lyric/sped-up/cover
   version unless asked), then `fetch_url(url, as_kind='music')`. Tell the
   user which version you grabbed; a found link verifies the recording, not a
   usage licence.
4. A video can be a sound source: pass the clip's storage_key to
   `add_music`.
If no track route is available, finish the edit with the source's speech
and a strong picture rhythm and say music was not available — never block a
complete edit waiting for an MP3.

CHOOSE BY CONTENT: podcast and interview insight → cinematic, ambient or
inspiring beds that stay out of the words; business, product and education →
corporate or chill; hustle, sport and hype → hiphop or upbeat; tension and
stakes → dramatic; tender or vulnerable stories → sparse ambient that can
drop out. Match tempo to cut rate: fast-cut montage 120–160 BPM, talking-head
beds anything unobtrusive, cinematic 60–90.

LEVELS AND DUCKING: under speech the bed sits about 13–20 dB below the voice
(the `add_music` default is -18 dB ducked); `set_music_fit(duck_mode='smooth')`
fixes pumping or a swallowed first word. In speechless stretches — B-roll
montage, the hook interrupt, the ending button — let it rise. On a speechless
video the music IS the program: lead level, no duck, cuts on its beats.

THE DROP LANDS ON THE MOMENT:
- Find the payoff (reveal, punchline, number, transformation) in OUTPUT
  seconds and the track's build or drop from `get_audio_analysis`.
- `set_music_fit(offset_s=...)` so the drop hits the moment:
  offset_s = drop_time_in_track − (moment_in_program − music start) (when
  negative, start the music later instead).
- Pair it with picture: a riser cue into the moment, the hero graphic and a
  low hit on the landing.
- STOP-DOWN (optional): a beat of room just before a reveal makes the
  landing hit harder. There is no mid-track dip control: split the bed
  around the reveal as audio describes (shorten it with `set_music_fit`,
  restart the same track at the reveal with the matching offset), or skip
  the stop-down. Never end the bed early without restarting it.

ENDS MATTER: end on a musical resolve or fade the last 1–2 s with
`set_music_fit`; never stop mid-bar or leave dead air. Enter on a phrase
boundary — use offset_s to skip a limp intro.

BEAT CULTURE BY FORMAT: montage, gameplay, sports and music-led pieces — the
music is the structure: cut on beats (`beat_align_cuts`), land graphics and
pulses on transients, build to the peak. Talking-head and podcast reels — the
words are the structure: cuts follow speech, the bed supports, and only the
hook, B-roll passages and the payoff lock to the music. When both exist,
switch rules at the section boundary.

TRENDING SOUNDS: platforms license them in-app only. The user uploads the
sound (or a clip carrying it — `extract_audio`), you analyze it and cut the
edit to its grid with the hook on its drop; they attach the licensed version
in-app.

LICENCES TRAVEL WITH THE TRACK: relay any obligation a user-provided or
fetched track carries (credit, non-commercial-only); never work around a
commercial-use restriction for a business or monetized reel.

## Common failure modes

- No bed on a reel, leaving digital silence between phrases.
- Bed too loud under speech, or a genre that fights the story.
- The drop landing nowhere near the payoff; music stopping mid-bar at the
  end; a limp intro left in.
- Beat-synced captions or cuts on a talking reel where the words should lead.
- Claiming to have heard a track that was only measured.

## Verification procedure

Validate placement, coverage, fades and ducking deterministically; check the
AUDIO CHECK for bed level under speech and dead air; inspect the drop against
the payoff frame; listen-check (when the reviewer is available) the opening,
representative dialogue, the payoff and the ending.

## Repair ladder

Refit the offset to the payoff → adjust duck/gain/fades → use a different
section of the track → choose another track or mood → dry the specific
passage that needs it → render and review again.
