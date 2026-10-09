# music — music only when the user asks: their track or named song first, generic background music only on request, levels, placing the drop, fitting the ends, beat culture

## Editorial decision principles

Music is the user's choice, never yours. Never add music on your own
initiative: a speech-led reel without music is a complete edit, carried by
the voice, the source's natural sound and sparse sound design. Music goes in
only when the user asks for it or supplies a track, and the strongest answer
is the song they actually want — their own upload or link, or a song they
name — not a stock bed. When music is placed, it sits 13–20 dB under the
voice, ducks, and its structure is edited to the story: a lift into the
payoff, room before a reveal, a button on the ending.

- Suggesting is fine: when a montage or a no-dialogue passage would clearly
  play better with a song, say so in one clause of the reply and let the user
  send or name one.
- Generic background music from the CC0 library only when the user explicitly
  asks for generic background music.
- A missing bed is never a defect to repair on your own; digital silence is
  (fix it with the source's natural sound, not invented music).

## Evidence to inspect

Inspect what the user asked for or supplied (an upload, a pasted link, a
named song, "add some background music"), the treatment and energy arc,
dialogue density, the payoff and turns in program time, the supplied track's
tempo, energy and structure (`get_audio_analysis(asset_key=...)`), the
opening, representative speech, the ending, and the rendered AUDIO CHECK.

## Strong treatment patterns

SOURCING — only after the user asked for music or supplied a track, in this
order:
1. The user's own upload or link — when they provide a song, that IS the
   song (`list_assets(kind='music')`; `fetch_url(url, as_kind='music')` for a
   pasted link; `extract_audio` for the sound of a clip they sent).
2. A SPECIFIC song they NAME: `find_song(query)` returns candidate links
   (prefer the artist's own or "- Topic" channel, never a lyric/sped-up/cover
   version unless asked), then `fetch_url(url, as_kind='music')`. Tell the
   user which version you grabbed, and say plainly that a found song is not
   a usage licence: platforms may mute or claim it, and they should attach
   the licensed version in-app when posting.
3. Generic background music, only when the user explicitly asks for it: the
   Valmera music library when its tools are listed (the list_music_library
   tool browses licence-free CC0 tracks by mood and the add_library_music
   tool places one as a ducked bed; apply_look's music option, where the
   schema lists it, follows the same rule). Name the track you chose; CC0
   carries no credit or licence obligation.
4. A video can be a sound source: pass the clip's storage_key to
   `add_music`.
If the user asked for music and no route works, finish the edit with the
source's speech and a strong picture rhythm and say music was not available
— never block a complete edit waiting for an MP3.

CHOOSE WITHIN WHAT THE USER GAVE: with their track, choose the section, not
the song — the part whose energy matches the story. With a generic request,
choose by content: insight → cinematic, ambient or inspiring beds that stay
out of the words; business, product and education → corporate or chill;
hype → hiphop or upbeat; stakes → dramatic; tender stories → sparse ambient
that can drop out.

LEVELS AND DUCKING: under speech the bed sits about 13–20 dB below the voice
(the `add_music` default is -18 dB ducked); `set_music_fit(duck_mode='smooth')`
fixes pumping or a swallowed first word. In speechless stretches — B-roll
montage, the ending button — let it rise. On a speechless video the music IS
the program: lead level, no duck, cuts on its beats.

THE DROP LANDS ON THE MOMENT:
- Find the payoff (reveal, punchline, number, transformation) in OUTPUT
  seconds and the track's build or drop from `get_audio_analysis`.
- `set_music_fit(offset_s=...)` so the drop hits the moment:
  offset_s = drop_time_in_track − (moment_in_program − music start) (when
  negative, start the music later instead).
- Pair it with picture: the hero graphic and camera land on the drop; add a
  library sound only if the moment needs more than the drop gives (read
  audio).
- STOP-DOWN (optional): a beat of room just before a reveal makes the
  landing hit harder. There is no mid-track dip control: split the bed
  around the reveal as audio describes (shorten it with `set_music_fit`,
  restart the same track at the reveal with the matching offset), or skip
  the stop-down. Never end the bed early without restarting it.

ENDS MATTER: end on a musical resolve or fade the last 1–2 s with
`set_music_fit`; never stop mid-bar or leave dead air. Enter on a phrase
boundary — use offset_s to skip a limp intro.

BEAT CULTURE BY FORMAT: montage, gameplay, sports and music-led pieces with
the user's track — the music is the structure: cut on beats
(`beat_align_cuts`), land graphics and pulses on transients, build to the
peak. Talking-head and podcast reels — the words are the structure: cuts
follow speech, a requested bed supports, and only the hook, B-roll passages
and the payoff lock to the music. When both exist, switch rules at the
section boundary.

TRENDING SOUNDS: platforms license them in-app only. The user uploads the
sound (or a clip carrying it — `extract_audio`), you analyze it and cut the
edit to its grid with the hook on its drop; they attach the licensed version
in-app.

LICENCES TRAVEL WITH THE TRACK: relay any obligation a user-provided or
fetched track carries (credit, non-commercial-only); never work around a
commercial-use restriction for a business or monetized reel.

## Common failure modes

- Music added that the user never asked for, or a stock bed chosen when they
  wanted a real song.
- Presenting a found song as licensed, or not naming the version grabbed.
- Bed too loud under speech, or a section that fights the story.
- The drop landing nowhere near the payoff; music stopping mid-bar at the
  end; a limp intro left in.
- Beat-synced captions or cuts on a talking reel where the words should lead.
- Claiming to have heard a track that was only measured.

## Verification procedure

Confirm the music traces to the user's request or track. Validate placement,
coverage, fades and ducking deterministically; check the AUDIO CHECK for bed
level under speech and dead air; inspect the drop against the payoff frame;
listen-check (when the reviewer is available) the opening, representative
dialogue, the payoff and the ending.

## Repair ladder

Remove music the user did not ask for → refit the offset to the payoff →
adjust duck/gain/fades → use a different section of the user's track →
offer another version or ask for their file → dry the specific passage
that needs it → render and review again.
