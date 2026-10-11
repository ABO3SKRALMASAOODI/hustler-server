# punctuation — 1-2-frame accents on transients, frame-exact strobes, and designed junction devices at real cuts

## Editorial decision principles

Punctuation is percussion: a frame or two that lands exactly on a transient
the viewer already hears (a stressed syllable, a hard consonant, the cut, a
kick in a track the user supplied). It marks structure; it never decorates.

- Land on transients, never mid-word on a payoff. The payoff word itself
  stays clean; a hit lands on the stress BEFORE it, or on the cut after it.
- None inside a payoff hold. A held frame after the line is silence for the
  eyes; a flash there breaks it.
- One punctuation family per section. Negatives OR white hits OR boxes in a
  section, not all three; change family when the section changes.
- Talking head, no track (format C): 0-2 accents per short, on the turn or a
  real cut. Speech/quote pieces (A): at most one every 2 s while building,
  accelerating into the drop. A user's track (B): 8ths only inside drop
  sections. Essays/sizzles (F/G): strobe sections are fair.
- Content-born transitions first: an iris from an element on screen, the
  next shot through the word's letters, the subject's own silhouette. A
  plain dissolve or wipe is the fallback, never the habit.
- Junctions only at real scene changes. A jump cut inside one continuous
  take stays bare (set_junction's default cuts='scene' already skips them).
- Flash safety is on: automatic placements keep at most 3 full-frame
  flashes a second, counted together with every flash the edit already
  plays (accents, hit flashes, box steps, matte cuts, strobes). More needs
  the user's explicit ask (they asked for strobing or flashing), and the
  export is then flagged contains_flashing.
- No device adds a sound or a zoom. Never add music to get a beat grid:
  beats, downbeats and onsets exist only on a track the user supplied;
  otherwise the clock is the speaker (on='stress').

## Evidence to inspect

- The transcript and its stresses: add_accent on='stress' reads the prosody
  grid (word onsets, stressed words, phrase ends, pauses) mapped through the
  current cut, so every placement is a real word time.
- The cuts: get_edl / the program's keep and inserts. set_junction lists the
  real cut times when asked for one that is not there.
- The picture card (effects.picture_cards): a card-clipped white hit needs a
  card on screen at that moment.
- Existing fx items (get_edl fx): one junction per cut; ids for
  remove_punctuation.
- The ledger line every write returns (punctuation n/budget, transition
  n/budget): stay inside the budget of the short's format and intensity.

## Strong treatment patterns

- Accelerating negatives into a payoff (ref 04): add_accent kind=negative,
  on=stress, density=accelerate, start/end around the build, count 6-9.
  Gaps shrink monotonically into the payoff; flash safety thins the tail
  unless the user asked for strobing.
- Card-clipped white hits (ref 02): add_accent kind=white_hit on stresses
  with region {shape: card}: a 1-frame white peak inside the rounded card
  whose light lingers, then drains to 0 over 3-5 frames (decay_f, default
  5: ref 02's own fall-off); the backdrop never moves.
- The outgoing colour as light (ref 01@3.833): kind=colour_hit on the cut
  frame, colour omitted: the outgoing shot's dominant hue screened over two
  frames of the incoming one.
- Scope all (ref 04): scope='all' inverts the captions and graphics too; the
  aspect bars, watermark and end card are never touched.
- Inverted boxes (ref 15@2.33): kind=box_invert, 2 frames, bands 3-4, or a
  region whose box is the subject's product.
- A product strobe with negatives (ref 01@0.04-0.96): add_strobe frames
  (project stills or a registered variant set), hold 2, negate alternate,
  fit register — every image exactly 2 frames, the sound untouched. A
  'src:<s>' clip frame is the raw source frame filled to the frame (no
  grade, crop focus or picture card of the edit); for frames of the edit
  itself use 'program' (the live picture, as graded and framed).
- Iris from an element (ref 03@7.93): set_junction style=iris with origin a
  face or a point on the element; the iris blooms with a soft ring ahead of
  its edge.
- Box steps (ref 14@15.30): style=box_steps, 0.8 s: the next shot opens
  in a growing box, each step landing with a paper-white fill.
- Column wipe (ref 22@≈4.9), bands, staircase: geometric wipes for a
  montage; one family per section.
- Matte cut (ref 20@7.40): style=matte_cut — the next shot through the
  outgoing speaker's silhouette; without a person matte it degrades to a
  white flash on the cut and says so (flash safety counts it as a flash).
- Zoom blur / radial blur / whip: energy changes at a real turn, 0.4-0.5 s.

## Common failure modes

- A hit on the payoff word itself, or inside the payoff hold.
- Negatives, white hits and boxes mixed in one section (no family).
- A junction on every jump cut of a talking head.
- A strobe or a dense accent train without the user's ask (flash safety
  refuses or thins it; do not work around it with apply_edit_batch).
- A beat-synced accent request with no user track: never add music for a
  clock; offer on='stress'.
- A card region where no card is on screen: the render omits that accent
  and says why.
- datamosh: its device card is dark until the renderer can run the real
  codec path; offer pixel_stretch, zoom_blur or double_exposure instead.

## Verification procedure

1. Read the write's ledger line: punctuation and transition counts inside
   the budget.
2. Read the result's thinned note: if flash safety held hits back, say so;
   do not re-add them.
3. Render a preview and look at the frames around each landing (look_at at
   the item's start, and one frame either side): the hit lands on the word's
   onset frame, exactly 1-2 frames, and the frames around it are untouched.
4. For a junction: the program length is unchanged and the sound runs
   straight through the cut; the window is d/2 each side of the cut.
5. The render report (devices rows): every rung is full; a reduced or
   substitute rung names why (no face box, no matte, no source handle).

## Repair ladder

1. Wrong moment: remove_punctuation the id and re-place with at= (program
   seconds) one frame earlier or later.
2. Too busy: remove the weakest accents (the ledger and the strength order
   in the result), or switch density from pulse to sparse.
3. A junction reads as noise: switch to a content-born style (iris from an
   element, letter_window) or a dissolve, or set_junction style='none'.
4. A degraded matte cut or letter window: accept the substitute the report
   names, or pick a style that needs no matte (iris, box_steps).
5. Still wrong: remove every punctuation item (ids='all') and leave the cuts
   clean — clean beats busy.

## Through apply_edit_batch

An MCP client that cached its tool list before these verbs existed writes
the very same items through apply_edit_batch (layer fx), validated exactly
like the verbs. Times are program seconds; a 1-frame item at 30 fps is
end = start + 0.04 (round((frame + 1) / fps, 2)).

Accelerating negative (one item per hit; scope all = stage over_type):

    {"action": "upsert", "layer": "fx", "id": "ac_neg1", "value": {
      "id": "ac_neg1", "type": "accent", "stage": "over_type",
      "start": 6.0, "end": 6.04, "kind": "negative",
      "device": "accent_negative",
      "bind": {"on": "stress", "ref": 41, "rhythm": "speech"}}}

Card-clipped white hit with a 3-frame decay:

    {"action": "upsert", "layer": "fx", "id": "ac_hit1", "value": {
      "id": "ac_hit1", "type": "accent", "stage": "pre_captions",
      "start": 16.0, "end": 16.04, "kind": "white_hit", "decay_f": 3,
      "region": {"shape": "card"}, "device": "accent_white_hit"}}

Colour hit (omit colour for the outgoing shot's hue), seeded boxes:

    {"action": "upsert", "layer": "fx", "id": "ac_col1", "value": {
      "id": "ac_col1", "type": "accent", "stage": "pre_captions",
      "start": 9.2, "end": 9.27, "kind": "colour_hit", "decay_f": 2,
      "device": "accent_colour_hit"}}
    {"action": "upsert", "layer": "fx", "id": "ac_box1", "value": {
      "id": "ac_box1", "type": "accent", "stage": "pre_captions",
      "start": 17.0, "end": 17.08, "kind": "box_invert", "bands": 4,
      "seed": 7, "device": "accent_box_invert"}}

Strobe (frames: asset keys, clip frames, colours or the live program):

    {"action": "upsert", "layer": "fx", "id": "st1", "value": {
      "id": "st1", "type": "strobe", "stage": "pre_captions",
      "start": 22.0, "end": 22.4, "fit": "register",
      "device": "strobe_frames", "frames": [
        {"source": {"kind": "asset", "asset_key": "media/p1/a.png"},
         "hold_f": 2},
        {"source": {"kind": "source", "src_t": 25.0}, "hold_f": 2,
         "negate": true}]}}

Junction at the cut at 10.0 s (one per cut; remove the old one first):

    {"action": "upsert", "layer": "fx", "id": "jn1", "value": {
      "id": "jn1", "type": "junction", "stage": "junction", "at": 10.0,
      "style": "iris", "duration_s": 0.4, "origin": {"kind": "face"},
      "device": "junction_iris"}}
    {"action": "upsert", "layer": "fx", "id": "jn2", "value": {
      "id": "jn2", "type": "junction", "stage": "junction", "at": 14.2,
      "style": "letter_window", "duration_s": 0.8,
      "origin": {"kind": "glyph", "ref": "m1"},
      "device": "junction_letter_window"}}

Remove any of them: {"action": "remove", "layer": "fx", "id": "ac_neg1"}.
The device ids are the cards: accent_<kind>, strobe_frames,
junction_dissolve, junction_xfade for fadegrays, pixelize, radial and
slices,
junction_wipe, junction_iris, junction_columns, junction_staircase,
junction_box_steps, junction_bands, junction_letter_window,
junction_matte_cut, junction_double_exposure, junction_pixel_stretch,
junction_hit_flash, junction_whip, and junction_zoom_blur for zoom_blur
and radial_blur. An unknown or dark device is refused; an item may also omit
device.
