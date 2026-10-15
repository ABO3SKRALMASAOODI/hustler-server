# montage — shots cut to the user's track: beat-slot montages, call-and-response with type, the track bent to the story (drop on the payoff, held breath, intro that opens on the drop), eased slow-motion swings, and archive stills that move

## Editorial decision principles

The user's track is the clock and the agent never chooses one. With no
track the user supplied, uploaded, named or picked, `plan_to_music` cuts on
the SPEECH stresses (clock 'speech'). `shape_music` refuses outright: there
is nothing of the user's to shape.

- A montage is a SENTENCE of pictures. Choose the pool for what the words
  mean (find_footage, research_broll), then let `plan_to_music` do the
  arithmetic: one cut per beat slot, ONE FRAME BEFORE THE KICK (lead_f 1;
  0-2 allowed), a mode per section (`hold` for an intro or a break, `pulse`
  for a verse or a drop, `strobe` only when the user asked for strobing:
  flash safety), no shot cluster twice inside six cuts, wide and close
  alternating, and cuts inside long clips (each reuse walks further in).
- Range over a house style. Vary the cut rate with `every` (every 2nd beat
  for a calm build, every beat for the drop), the style (`broll`,
  `call_response` type on black answering b-roll on the hits, `arc` words
  on black -> words over picture -> picture only), and the modes per
  section. Two montages in one short should not share all three.
- `shape_music` solves the music item's OWN fields (offset, start, end,
  fades) so the drop lands on the payoff word (±1 frame) and the bed ends
  on a bar line; `hold_s` puts the held breath before it; `intro_filter`
  muffles the intro until the drop frame. Only on a confident bar grid.
- `set_speed_ramp` is a swing, not a crawl: 1.0 -> low -> 1.0 eased, the
  low held where the source still shows a fresh frame (60p: 0.5x). It is
  refused over 2+ spoken words (the voice drags, the joins are audible)
  unless the user asks (`allow_speech`): ramp B-roll, a gesture, a walk, a
  pause.
- A photo never sits dead on screen: `animate_still` moves it (toward a
  face it finds, a pan across a panorama, a rise up a scan); montage stills
  move by themselves, each a different move.
- B&W stays a device (a section, one colour kept, a silhouette hold), never
  the whole montage's look unless the user asks.

## Evidence to inspect

- `analyze_music`: tempo, bars, sections, drops, kicks; its confidence.
- `plan_to_music(dry_run=true)`: the slots, their modes and onsets.
- `get_edl`: the music item, the overlays/motion/fx the montage wrote (ids
  `mt1_*`), the `arrange` records.
- `list_assets`: the pool (clips and stills) before planning.

## Strong treatment patterns

- Sizzle on the kick: pulse on every kick through the drop, hold the intro,
  a call-and-response bar of type on black answering b-roll on the hits.
- Silence then drop: `shape_music(payoff='the words', hold_s=0.45,
  intro_filter='lowpass')`: muffled intro, bed out, the drop and the
  payoff word on one frame, a cut on it.
- Archive story: stills that push toward the faces, a pan across the
  clipping, one slow swing on the moment of motion.

## Common failure modes

- A pool of three clips: clusters repeat; the plan says so — fetch more or
  cut every 2nd beat.
- A strobe the user never asked for: it is cut as pulses (flash safety).
- A drop that the program reaches before the track can: shape_music says
  so; move the payoff later or start the music earlier.
- Too many cuts for one batch (60 edits): a shorter window or every=2.

## Verification procedure

- `proof_device` / a preview of the window: every cut one frame before its
  kick, no repeated cluster inside six, holds >= 1.5 s, modes switch on the
  section line.
- `check_speech` is untouched by a montage (the program audio keeps
  playing); the ledger line says no sound or zoom was added.
- After `shape_music`: the drop on the payoff frame, the held gap silent,
  the bed ending on a bar before the end card.

## Repair ladder

1. Cuts feel late: confirm lead_f 1; re-plan (`plan_to_music(replan='mt1')`).
2. Too busy: every=2, or the section to hold.
3. The song changed: the swap re-cuts a b-roll montage by itself; a type
   montage re-plans with replan.
4. A still looks dead or crops a face: `animate_still(id, path=...)` with
   focus on the face, or fit contain_blur.

## Through apply_edit_batch

Every montage is overlays (`fit` cover), motion items (`still_motion`,
`typeset`), fx items (`strobe`, `arrange`) and the music item's own fields:
all writable, movable and removable by `apply_edit_batch`. An `arrange`
item names its `music_id` and only shapes a track with user provenance;
`montage_intent` / `fit_intent` records render nothing.
