# devices — when each creative device earns its place: silhouette flashes, posters, punctuation frames, junctions, grade windows, finishes, type behind the subject; plan with suggest_devices, write in one batch, prove with proof_device

## Editorial decision principles

A device is punctuation for the story, never decoration. The references
that beat us use the person cut out as a graphic, one inverted frame on the
kick, silence before the payoff and a word tucked behind a head — each ON a
moment that earns it. Every device is bound to that moment: the payoff, the
turn of the argument (a contrast, a negation), a real cut, a section
change, or a beat of a track the USER supplied. A device with no moment is
noise; a talking head with two well-placed devices beats one with ten.

- The payoff gets the strongest device (a silhouette flash, a poster, the
  hero word behind the speaker) and then a HOLD: nothing punctuates the
  0.8-1.5 s after the payoff word.
- One device per moment, one punctuation family per section, and no device
  on consecutive shots (a deliberate callback is the exception).
- Budgets follow the format and the project's intensity (`set_intensity`:
  calm, designed, charged). Designed defaults: a talking head with no track
  takes 0-2 punctuation frames (on cuts or the turn only), at most 2 matte
  effects, one type-in-depth hero every 6-8 s; transitions only at scene
  changes. On the user's own track punctuation follows the track (8ths only
  inside drop sections).
- Flash safety is on: never more than 3 full-frame flashes in any second
  unless the user asked for strobing; the export is then flagged.
- Never pick music to get a beat grid; devices bind to speech (the prosody
  grid) unless the user supplied a track.
- The Valmera end card and the free-tier watermark are untouchable: every
  device renders before them.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

## One signature move per short: recipes and invention

The owner's bar is CREATIVITY with taste: last week's best moments were
devices nobody had shown (captions that lit up like a lamp as the speaker
reached each word). Those are examples of the bar, not a style to repeat:
every short should surprise in a way that fits ITS story, and a batch of
shorts must not wear one house style.

- `suggest_recipes` reads the cut and ranks whole moves (several devices on
  the right words, with taste built in): `mono_until_turn` — grey until
  the turn, colour lands ON the word; `colour_kept_payoff` — the world goes
  grey, the speaker stays in colour; `silhouette_hold` — identity as a flat
  shape; `negative_hits` — 1-frame negatives into the payoff;
  `white_silhouette`; `lab_scan` — a registered variant sequence on a
  mechanism or a number; `thermal_flip` — a breath of thermal on heat,
  energy, the body; `light_figure` — the world drops to black and the
  speaker glows in the accent; `echo_trail` — a trail of copies on a
  speed word.
  Each names the word it
  lands on, why, a reference exemplar and the exact ops; moves the other
  shorts of the batch used rank lower (freshness).
- Take ONE recipe per short as its signature, the one the story earns, and
  prefer a fresh one. `apply_recipe(name)` writes it; it is once per short,
  flash-safe unless the user asked for flashing, silent and zoom-free.
- Recipes are a floor, not a ceiling. When the story calls for something no
  recipe names, compose it from the devices below (a poster field in the
  accent the captions use, a grade window that closes as the claim lands, a
  type window onto the next shot) — invented, bound to a word, proved with
  `proof_device`. Never re-apply last short's move because it worked there.
- Black and white is a device on a moment, never the whole short's look.

## Evidence to inspect

- `suggest_devices` returns up to 8 ranked candidates, each bound to its
  moment with the reason, an exemplar reference moment, a render cost and
  the exact apply_edit_batch operation. It never proposes a zoom or a sound
  unless the user asked, and it already applies the budgets and the rules
  above. It also starts measuring the person matte its matte candidates
  need (a batch launches nothing itself). Zero candidates is a valid
  answer.
- `look_at(times=[...], overlay='matte')` shows the person matte where a
  matte device would draw (it starts measuring a missing chunk and says
  "measuring"); `overlay='free_space'` shades faces, UI bands and the
  watermark and outlines the free rectangles for type; `overlay='beats'`
  rules stresses, cuts, the user's beats and every device landing under the
  frame.
- The project state's ANALYSIS line says which analysis is measured and
  whether the person matte covers the current cut.
- `audit_motion` reads the edit as written: the ledger at the project's
  intensity, flash safety, each device's expected rung and the restraint
  findings.

## Strong treatment patterns

When each device earns its place:

- Silhouette fill flash (subject fill, white or the accent, 1-2 frames,
  optional decay): the payoff word or a section turn; on a user track the
  structural hits. Never on every stress.
- Poster (the speaker on a flat accent field, footage kept or a flat
  silhouette): through the payoff hold or as a section intro.
- Glow, outline: a reveal, "the figure becomes light", an essay beat; soft
  devices that survive a lower-quality matte.
- Keep the subject (or one hue) in colour, the world in mono: a passage
  about the person against their world; hold it at least a sentence.
- Background swap, subject held while the background changes: montage and
  sizzle on the user's track, not a talking-head body.
- Echo, clone, twin, trail: real movement (a walk-on, 60p), never a seated
  talking head.
- Punctuation frames (negative, white hit, threshold, posterize, mono,
  mirror, hue step, RGB slice, box invert): ON transients — a cut, the turn
  word, a kick of the user's track — never mid-word on a payoff, one family
  per section; a card-clipped hit stays inside the picture card.
- Strobe (stills, clip frames or a registered variant set, 1-4-frame
  holds): lab-scan and sizzle sections on a user track; never in a talking
  head's body.
- Junctions (iris from the face or a glyph, matte cut, column wipe, box
  steps, datamosh, whip): content-born transitions at real scene changes.
  A jump cut inside one take stays a hard cut.
- Grade windows: mono to colour landing on a drop (the ramp ENDS on the
  frame), a black-and-white flashback, a then-versus-now. One per short in
  a talking head.
- Finishes (CRT, LCD re-photography, film, halftone, thermal, X-ray,
  blueprint): archival footage, a screen, a metaphor for a mechanism or
  "inside the machine" — never a default look.
- Region effects (negate, blur, ribbed glass, refraction inside a rect,
  face, glyph or card): privacy, a reveal behind a title; the picture
  outside the region is untouched.
- Composite on a motion graphic: difference blend for an interruption over
  a busy plate, screen or add for light leaks and white hits, footage inside
  the letters of a section title, the ghost stroke where a word behind the
  subject hides its letters, screen-locked type under a requested push.
- Type behind the subject: the payoff word, one hero every 6-8 s. The
  program matte keeps it behind the speaker under a follow crop, across cuts
  and over speed ramps; where the matte cannot be measured it renders as a
  face-safe slam and the report says so.

The loop: `suggest_devices` → keep the candidates that serve the story →
ONE apply_edit_batch with their ops → `proof_device` on what you wrote →
fix or remove what does not land → `render_preview(complete=true)` and look
at the landings → `audit_motion` before handing over.

## Common failure modes

- A device on every stressed word, or three devices stacked on one word:
  pick one; the planner offers the others as alternatives on that moment.
- Punctuation inside the payoff hold, or on a word in the middle of the
  payoff phrase: the payoff must breathe.
- Two punctuation families in one section (a negative and a white hit
  trading places): pick one family per section.
- The same device on consecutive shots: it reads as a template.
- A matte device written before the matte is measured, shipped without
  saying it rendered as its substitute.
- Flashing past 3 per second without the user asking.
- Adding a zoom or a sound because a device "needs" it: devices are silent
  and zoom-free by design.

## Verification procedure

1. `proof_device(ids=[...])` on every device you wrote (default: the
   devices the last write changed). It renders only the windows around them,
   with and without them, and returns per device a strip of frames -3..+6
   around the landing, an onion of the pixels it changed, and metrics:
   landing error (bar: within 1 frame, 2 for syllables), legibility of type
   (payload Lc 60, connectors 45), matte jitter, flash risk, junction
   integrity (same frame count, audio untouched), face collisions and the
   budget.
2. Look at the strips: does the hit land on its word or cut, read as
   intentional, and leave the subject and type readable?
3. `render_preview(complete=true)` and `look_at(rendered=true,
   output_times=[...])` densely around the landings.
4. `audit_motion` before handoff: no blocking finding, nothing degraded
   without saying so.

## Repair ladder

1. Landing error: rebind or move the item (its `bind.offset_f` shifts it by
   whole frames), then prove again.
2. Rendered as a substitute or left out: the report names the reason. If
   the matte was still measuring, wait until the ANALYSIS line says it is
   ready, then `render_preview(quality='approval')`: a draft render of the
   same version, and a stitched draft of an unchanged device, reuse the
   substitute frames. Otherwise tell the user what it rendered as. A final
   with an unacknowledged degraded device is held until it is fixed (a new
   version) or acknowledged with justify_verification_findings.
3. Over budget or too busy: `set_intensity` one step down removes the
   lowest-ranked devices in one write that lists their ids; or remove the
   weakest by id.
4. Flash risk: remove the weakest flashes until at most 3 sit in any second.
5. Illegible type: a different blend or colour, a scrim, or move it into the
   free space (`look_at(overlay='free_space')`).
6. A device that still does not serve the story: remove it. Zero is fine.

## Through apply_edit_batch

Every device is an item of the `fx` layer (or the `composite` of a motion
graphic), written and removed through apply_edit_batch exactly like its
verb — clients whose tool list predates the device verbs reach every device
this way. Copy the `op` of a `suggest_devices` candidate as it is; the
shapes are:

- add or replace: {"action": "upsert", "layer": "fx", "id": "<id>",
  "value": {"id": "<id>", "type": "<type>", "stage": "<stage>", "start":
  <s>, "end": <s>, ..., "device": "<card id>", "bind": {"on": "stress",
  "ref": <word index>, "rhythm": "speech"}}} — a junction carries "at" (its
  cut) instead of start and end; written by hand without a candidate's
  word index, bind it by time: {"on": "time", "ref": <program second>};
- remove: {"action": "remove", "layer": "fx", "id": "<id>"};
- a motion graphic's composite: {"action": "upsert", "layer": "motion",
  "id": "<motion id>", "value": {"id": "<motion id>", "composite":
  {"blend": "difference"}}}; clear it with "composite": null.

Types and stages: subject and background at world or screen; region at
world, screen, pre_captions or over_type; accent and strobe at pre_captions,
which leaves the type untouched, or over_type, which inverts it too; junction at
junction; grade_window at world or screen; finish at world, screen,
pre_captions or over_type. A 1-frame
device at program frame k writes start = round(k / fps, 2) and end =
round((k + 1) / fps, 2). get_edl shows every stored item's exact shape.
