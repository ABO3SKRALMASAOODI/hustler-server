# transitions — motivated junctions, their optional sound, the styles and motion transitions, the jump-cut law

## Editorial decision principles

Premium reels mark every real turn with a designed junction — a whip, a
zoom-through, a flash, a light leak, a film burn, a glitch — and may give it
one sound from the approved library whose peak lands on the cut. Inside one
continuous take the cut stays hard, invisible and silent. A transition
expresses a motivated relationship across a real boundary; it never
decorates a jump cut.

- Motivated junctions: section turns, hook → body, B-roll in and out,
  location or speaker changes, chapter titles, the montage-to-face return.
- Hard cuts inside a take; a jump cut stays bare (fine by default) or,
  only where its pop distracts, gets alternating framing (see zooms) —
  never a full-screen effect.
- A junction at a real turn may carry one library sound, counted in the
  short's sparse budget (about one sound every 4–5 s at most, never the same
  sound twice within ~3 s). Ordinary cuts inside a conversation get none.
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
so its peak sits on the junction (read motion-design). They are silent
unless you pass `sfx=true`, which maps their sound roles onto the approved
library. Use them to vary the vocabulary: a light leak into the
story, a whip into B-roll, a flash on the reveal.

JUNCTION SOUND: a look from `apply_look` may already place its own
transition sounds (ids starting `look_tx`) — read its receipt or `get_edl`
(sfx) first and adjust, move or remove those with `set_audio_gain`,
`move_sfx` or `remove_sfx` instead of stacking a second sound. When
`set_transitions` places effects it adds no sound; choose which real turns
earn one and place it from the library (`list_sound_library`):
`add_sfx(storage_key='sound:swish_1', at=..., gain_db=-14)` for a whip or
zoom punch, `sound:whoosh_soft_1` or `sound:whoosh_soft_2` into a slide, a
light leak or a reveal, `sound:glitch_1` under a deliberate glitch, and
`sound:impact_1` under a flash only when that junction is the payoff or the
single biggest landing (at most once per short). Put `at` ON the cut: the
tool starts each recording early by its measured peak so the peak lands
there (never pre-roll by hand). Pass the suggested gain so it sits under
the voice, and give each cue a
`purpose` naming the junction. A run of junctions inside ~3 s gets one
sound, not one each.

THE JUMP-CUT LAW:
- After `cut_silences`, a single talking-head take has one junction per
  removed pause — often 40+ — and every one is a JUMP CUT inside the same
  continuous shot. Decorating each with a full-screen effect looks broken (a
  whip on all of them fires an effect every couple of seconds through
  footage that never changed scene). Leave jump cuts hard; where one pop
  genuinely distracts, alternating framing covers it — tight from that cut
  to the next, wide at the following one (read zooms; zooms are optional,
  never on every cut); keep `landing` zooms for real cuts between ideas.
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
- Whoosh wars: a sound on every junction or jump cut, the same sound on
  junctions less than ~3 s apart, a whoosh whose peak lands after the cut,
  or two sounds on one junction because a look already placed one.
- One effect repeated mechanically on every boundary.
- Direction or energy that contradicts the footage (a whip against the
  motion, a glitch in a tender passage).
- A transition that delays the story's first words or covers a punchline.

## Verification procedure

Review every authored junction with dense rendered frames around the cut
and the AUDIO CHECK: it sits on a real turn, the motion reads clean, a
sound (when the junction earned one) peaks on the cut, the next shot's first
word is not swallowed, and the sequence of junction types varies.

## Repair ladder

Retime the sound to the cut → remove sounds the junction does not earn →
shorten or subdue → change direction or style
→ move to the real section turn → replace with a hard cut plus a framing
change → rescreen the sequence cadence.
