# transitions — motivated junctions with paired sound, the styles and motion transitions, the jump-cut law

## Editorial decision principles

Premium reels mark every real turn with a designed junction — a whip, a
zoom-through, a flash, a light leak, a film burn, a glitch — and give it a
sound: a whoosh or hit whose peak lands on the cut. Inside one continuous
take the cut stays hard and invisible. A transition expresses a motivated
relationship across a real boundary; it never decorates a jump cut.

- Motivated junctions: section turns, hook → body, B-roll in and out,
  location or speaker changes, chapter titles, the montage-to-face return.
- Hard cuts inside a take; jump cuts get landing zooms (see zooms), not
  full-screen effects.
- Every authored transition carries a paired sound cue unless the music
  transient already lands there.
- Vary the junction vocabulary across a reel; never the same effect
  back-to-back on every boundary.

## Evidence to inspect

Inspect the program map and `get_shots` for real shot changes and insert
boundaries, adjacent shot content and motion direction, the semantic turns
in `get_kept_transcript`, music beats (`get_audio_analysis`), the junction
count `set_transitions` reports, and rendered junction frames with sound.

## Strong treatment patterns

GLOBAL JUNCTION STYLES (`set_transitions`, duration-preserving, footage
never overlaps): dip_black (calm), dip_white (soft, bright), whip_left /
whip_right (fast directional smear with motion blur), zoom_punch (an
accelerating push whose momentum carries through the cut), glitch (RGB/noise
burst), flash (white pop peaking ON the cut). Fast reels: whip, zoom_punch
or flash at 0.15–0.3 s; calm or emotional: dip_black 0.3–0.5 s. True
crossfades do not exist — say so when asked.

PER-JUNCTION MOTION TRANSITIONS: to give one specific junction its own
treatment, place a motion graphic centred on that cut —
`flash_transition` (1–2 frame exposure pop), `light_leak` (warm wash across a
section turn), `glitch_burst` (RGB split on a tech or twist beat),
`film_burn` (cinematic section change) — with `add_motion_graphic`, starting
so its peak sits on the junction (read motion-design). These carry their own
synced kit sounds. Use them to vary the vocabulary: a light leak into the
story, a whip into B-roll, a flash on the reveal.

PAIRED SOUND: when `set_transitions` places effects, add kit cues yourself
(it adds none): `add_sfx(storage_key='kit:whoosh_hard', at=...)` for whips and
zoom punches, `kit:swoosh_up` into a reveal, `kit:glitch` under a glitch,
`kit:impact_soft` or `kit:kick` under a flash on a hit. Pre-roll the whoosh
so its peak lands on the cut — start roughly 40–50% of its length early
(about 0.2 s for whoosh_hard, 0.3 s for whoosh_soft) — and keep it under the
voice. Give each cue a `purpose` naming the junction.

THE JUMP-CUT LAW:
- After `cut_silences`, a single talking-head take has one junction per
  removed pause — often 40+ — and every one is a JUMP CUT inside the same
  continuous shot. Decorating each with a full-screen effect looks broken (a
  whip on all of them fires an effect every couple of seconds through
  footage that never changed scene). Cover jump cuts with landing zooms or
  framing changes instead.
- `set_transitions` defaults to scope='scene' and lands only on real shot
  changes and insert boundaries. READ THE RESULT — it says how many junctions
  it used ("7 of 45"); report THAT number, not the cut count.
- If nothing qualified, this footage is one continuous shot: the junction
  vocabulary then comes from per-junction motion transitions on the real
  section turns, chapter titles and B-roll entries. Use scope='every_cut'
  only when the user explicitly wants an effect on every cut.

CADENCE: a montage of nine far-apart clips has nine scene changes; a whip on
all nine inside 30 seconds is an effect every 3.3 s. Mix hard cuts on the
beat with a few designed junctions at the real turns. Read the cadence the
tool reports.

## Common failure modes

- Effects on jump cuts inside one take.
- Silent transitions, or a whoosh whose peak lands after the cut.
- One effect repeated mechanically on every boundary.
- Direction or energy that contradicts the footage (a whip against the
  motion, a glitch in a tender passage).
- A transition that delays the story's first words or covers a punchline.

## Verification procedure

Review every authored junction with dense rendered frames around the cut
and the AUDIO CHECK: it sits on a real turn, the motion reads clean, the
sound peak lands on the junction, the next shot's first word is not swallowed,
and the sequence of junction types varies.

## Repair ladder

Retime the sound to the cut → shorten or subdue → change direction or style
→ move to the real section turn → replace with a hard cut plus landing zoom
→ rescreen the sequence cadence.
