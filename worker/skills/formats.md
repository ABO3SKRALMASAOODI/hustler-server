# formats — name the format first, then edit to THAT format's premium grammar

## Editorial decision principles

Choose the format from footage, audience, platform and promise, then apply
that format's full premium grammar. Hybrid formats are valid when their
relationships are explicit. The fastest way to look like a machine is to
give a plant timelapse, a gameplay montage and a podcast clip the same
treatment; the second fastest is to give a premium reel the restraint of a
long-form interview.

## Evidence to inspect

Inspect duration, output aspect, words per minute, shot variety, transcript
arc, performance, platform, references and the user's actual objective.
Aspect + duration + speech density + the filmstrip already tell you what
this is.

## Strong treatment patterns

FIRST, READ THE FOOTAGE AND NAME THE FORMAT before touching the EDL. Then
design the viewer's journey beat by beat: each beat names its anchor (the
exact phrase, shot or card), why it exists, what the picture, type and sound
contribute, and its relative energy — so every department serves the same
beat. This is an editorial decision you make while writing, not a plan
document.

Source time is file-local. When a beat cites an uploaded clip, use that
clip's exact `storage_key`, its CLIP seconds and exact sentence/shot IDs from
`get_editorial_map(asset_key=...)`. When several uploaded clips or images may
contribute, use `compare_uploaded_media` once with all relevant keys.

WHAT A PREMIUM EDIT LOOKS LIKE, BY FORMAT:
- **Podcast or interview reel (vertical, ≤120 s, including shorts
  children)** — the podcast_reel grammar (read short-form-direction): one
  complete micro-story (setup, turn, payoff), the hook moved to 0 s with a
  pattern interrupt and hook text by 1.5 s; one committed look (`apply_look`
  `editorial` is the default starting point); face-aware full-bleed or a card
  on a designed background; motion captions (`editorial` or `clean`) with
  accent words; 2–4 hero moments on exact words; alternating framing on
  jump cuts, landings on turns, punches on emphasis, pushes on holds;
  B-roll evidence on named nouns; a few library sound cues on the moments
  that earn them (about one every 4–5 s at most, never on captions or
  ordinary cuts); music only when the user asks for it or supplies a track,
  then ducked 13–20 dB under the voice; word-safe filler and
  dead-pause cleanup; `set_master_loudness`; a native CTA after the payoff
  only when the user or brief asks for one, built from the handle, keyword
  and offer they supplied (never invented). Hold the face for a vulnerable
  admission — that passage is the deliberate exception.
- **Talking-head reel / creator** — the same grammar, often higher energy:
  `creator_punch` or `editorial`, `pop`/`stack` captions for punchy
  delivery, faster camera rhythm, more UI and data graphics.
- **Long-form interview or podcast (horizontal, minutes long)** — story-first
  cutting, readable subtitles (`documentary` or `clean`), occasional
  evidence cutaways and chapter titles at real turns, a quiet bed only when
  the user asks for music, gentle pushes; long-form earns more stillness
  than a reel.
- **Sermon / speech / motivational** — the reel grammar plus freeze-frame
  "pearls" (`add_freeze_frame`) or word slams on the 2–3 strongest lines,
  a swelling bed when the user asks for music or supplies a track, correct
  spelling of every name (`set_caption_fixes`).
- **Screen recording / product demo** — pad_blur or a screen frame, cursor
  enhanced, one travelling zoom that follows the action (`add_zoom_path`),
  dead loading time cut, click sounds on real clicks, UI callouts; keep the
  UI's native colour.
- **Montage / gameplay / sports** — with the user's track (or music they
  asked for) the music IS the structure: cut on its beats, build to the
  peak, slow motion on the single best moment, pulses and flashes on
  transients, mostly hard cuts on the beat with designed junctions at
  section turns. A 9:16 brief fills the phone (mode='crop');
  pad_blur only when the HUD or whole frame must stay. Without a track, cut
  on the action, keep the natural sound and suggest a song in the reply.
- **Music video / performance** — cut on the phrase, speed ramps into energy
  rises, the artist's face at the chorus, no captions unless asked.
- **Timelapse / nature / architecture** — slow eased moves, no punches or
  whips, no captions, shots breathe 3–5 s, the user's music leads (or the
  natural sound when they gave none).
- **Vlog / lifestyle** — keep the personality: jump cuts with alternating
  framing, warm grade, captions optional, energy over polish.

Deliberately breaking one of these is fine — say why in your reply. For
anything meant to get views, read hooks-retention; for anything with music,
read music.

## Common failure modes

- Choosing a format from platform labels alone, or applying one bundle to
  every source.
- Treating a podcast reel as a long-form conversation: a small picture on
  black, static subtitles, hard cuts only.
- Sound as wallpaper (a whoosh on every caption or cut), or music added that
  the user never asked for.
- Mixing visual languages without a dominant spine.

## Verification procedure

Check the treatment against the format's grammar in the rendered preview:
opening, pacing, hierarchy, sound and ending behave like the chosen format,
and every device is bound to the story.

## Repair ladder

Recast the format → apply its grammar from the hook outward → simplify to
one spine and one look → revise department relationships → remove devices
the format does not want → verify the full treatment.
