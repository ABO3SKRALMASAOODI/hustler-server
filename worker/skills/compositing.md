# compositing — blend modes, footage inside letters, letters as windows, ghost outlines, additive light and region effects

## Editorial decision principles

The best reels composite instead of stacking layers: a word is blended into
the plate so it reads as part of the picture, a title is made of the footage
itself, the next shot is glimpsed through the letters before the cut, a word
hidden behind the speaker still reads as outlines, light adds instead of
painting over. Each of these is a MOMENT — a hook, a payoff, a real scene
change — never a filter on every graphic.

- Difference blend for an interruption or a hero word over a busy plate
  (bookshelves, a crowd, a lit room): the ink inverts against whatever is
  under it, so it reads everywhere. Over a flat, mid-grey plate it reads
  poorly — let blend='auto' measure the plate and choose.
- Screen / add for light: light leaks, flashes and film burns brighten the
  picture and never darken it. Type is never screen-blended (it washes out).
- Footage in letters for a title or a section opener that names what the
  footage shows; the footage must move and have texture. field='black' gives
  the cinematic title card (letters full of footage on black).
- Letters as windows (the cross-cut reveal) only at a REAL scene change: the
  next shot is seen through the letters, which open into it on the cut.
  Never on a jump cut inside one take.
- Ghost outlines only on a behind-the-subject word whose letters the head
  actually hides; the outline keeps the word legible without breaking depth.
- Region effects single out one area — the hands framing a face, a screen,
  a word's letters, the picture card — and leave every other pixel alone.
  Glass materials (ribbed glass, refraction, heat) are a moment of texture,
  not a look for the whole short.
- Zooms and sounds are never part of a composite. Nothing here adds either.
- At most one composite device per beat; one blended hero word per short is
  usually enough (designed budget: blend 2, type window 1, region tone 2).

## Evidence to inspect

- The plate under the graphic: get_edl shows what plays there; look at the
  frames with look_at before and after the write. Busy (many edges, mixed
  light and dark) → difference; dark → screen; calm and lit → normal.
- Whether a behind_subject word is really hidden: if no letter is covered,
  the ghost switches itself off (auto measures the stored subject mask).
- For a cross-cut reveal: the program around the cut (get_edl inserts and
  keep spans). An insert after the cut gives real moving footage in the
  letters; main footage across a cut holds the next shot's edge frame until
  the cut (the render says so).
- For a region: where the face, the hands or the card sit over the window.
  A face or card region draws only where the render serves those masks; the
  write says so when it does not yet.
- The render report's devices rows: rung full, or the degrade it took and
  why (a glass material on a lane without displacement falls back to a
  blur; a region it cannot see is omitted).

## Strong treatment patterns

- Hero word over a busy plate: add_motion_graphic word_slam, then
  set_composite(motion_id, blend='difference') — or blend='auto'.
- Light: a light_leak / film_burn / flash_transition graphic on the cut,
  then set_composite(motion_id) — light templates default to screen (flash:
  add), so the leak brightens the footage under it.
- Title made of footage: add_type_window(text='YOU SHOULD KNOW I',
  fill_source='<asset key>', field='black', reveal='words_open') — one word
  per hard cut, then the I opens into the footage full frame.
- Cross-cut reveal: add_type_window(text='SOUNDS', fill_source='next',
  reveal='open', start=cut-1.2, end=cut) — the next shot inside the
  letters, the letter opening onto it exactly as the cut lands. For the
  red-field look (letters as windows on a colour): field='#c8102e'.
- Letters as windows onto the speaker: add_type_window(..., fill_source=
  'black', fill_mode='outside') — the picture shows only through the glyphs.
- Hidden letters: set_composite(motion_id, ghost='auto') on the hero word
  that sits behind the speaker.
- Magenta solarise inside the hands: add_region_effect(effect='solarise',
  colour='#ff00ff', region=<polygon keys between the hands>, start, end).
- Ribbed glass over the face for a beat: add_region_effect(effect=
  'ribbed_glass', region='face', start, end).
- Footage refracted inside a word: add_region_effect(effect='refraction',
  region='glyph:<motion id>', start, end); each letter bends the picture
  and catches light at its own edges, like cut glass.

## Common failure modes

- Difference over a flat mid-grey plate: the word turns muddy. Use auto or
  normal. Over black (a dark shirt, a night sky) difference IS normal paint:
  nothing changes, so the device has not earned its place — move the word
  over the busy or lit part of the picture, or leave it plain.
- Screen on type: white-on-anything washes out. Screen is for light.
- A footage fill that is static or dark: the letters read as flat colour.
  Pick moving, textured footage.
- A cross-cut reveal on a jump cut: there is no new shot to reveal.
- A composite on every graphic: the device stops meaning anything.
- Ghost on a graphic that is not behind the subject: nothing is hidden, so
  nothing draws (the write says so).
- A face or card CLIP region on a graphic (set_composite region='face'):
  not drawn yet — the render leaves the graphic out and names it. Clip a
  graphic to a rect, ellipse or polygon; face and card regions work for
  region effects.
- A region effect bigger than a quarter of the frame for a moment (negate,
  solarise, threshold, strong exposure) counts as a full-frame flash; more
  than 3 in a second are refused.

## Verification procedure

1. Read the write's line: EDL vX -> vY with its ledger note, and any NOTE
   (a face or card region not yet served, a ghost or stage on a graphic that
   is not behind the subject).
2. render_preview, then look_at the frames inside the window: the blend
   reads, the fill moves inside the letters, the window opens on the cut,
   outlines appear only where the head hides letters.
3. Check the preview's devices rows: every region item at rung full, or a
   named degrade the user is told about.
4. Outside the region or the graphic's window nothing changed.

## Repair ladder

1. A blend that does not read: blend='auto', then 'normal'.
2. A fill that reads as flat: another asset, or field='black' for contrast.
3. A reveal that holds a frozen next shot too long: start the type window
   closer to the cut (0.6-1.2 s before it).
4. A region effect omitted for its region: use a rect / ellipse box or a
   keyframed polygon instead of face or card.
5. A material that degraded to a blur: keep it (it says so) or remove it
   with remove_region_effect.
6. Undo a composite: set_composite(motion_id, clear=true).

## Through apply_edit_batch

Every device here is an ordinary EDL write; an MCP client that does not see
these verbs reaches every one with apply_edit_batch (validated exactly like
the verbs):

- Blend, fill, field, ghost, stage, region on a motion graphic. The
  upsert merges into the graphic but REPLACES its whole composite object:
  send every composite field to keep (get_edl shows the current one):
  {"action": "upsert", "layer": "motion", "id": "m1", "value": {"id":
  "m1", "composite": {"blend": "difference", "ghost": "auto"}}}
- Clear it: {"action": "upsert", "layer": "motion", "id": "m1", "value":
  {"id": "m1", "composite": null}}
- Footage in letters on black (a new type window): {"action": "upsert",
  "layer": "motion", "id": "tw1", "value": {"id": "tw1", "template":
  "type_window", "start": 2.0, "end": 5.0, "layer": "above_captions",
  "params": {"text": "YOU SHOULD KNOW I", "reveal": "words_open"},
  "composite": {"fill": {"kind": "asset", "asset_key": "media/p/broll.mp4",
  "loop": true}, "field": "black"}}}
- The cross-cut reveal: the same item with "composite": {"fill": {"kind":
  "next"}} and params {"text": "SOUNDS", "reveal": "open"}, ending on the
  cut.
- A region effect: {"action": "upsert", "layer": "fx", "id": "rg1",
  "value": {"id": "rg1", "type": "region", "stage": "pre_captions",
  "start": 1.0, "end": 3.0, "effect": "solarise", "colour": "#ff00ff",
  "region": {"shape": "ellipse", "box": [0.3, 0.2, 0.7, 0.5]}, "device":
  "region_tone"}} — keyframed: "region": {"shape": "polygon", "keys":
  [{"t": 0, "points": [[0.3, 0.4], [0.7, 0.4], [0.5, 0.7]]}, {"t": 1.5,
  "points": [[0.25, 0.35], [0.75, 0.38], [0.5, 0.75]], "ease": "in_out"}]};
  inside a graphic's letters: "region": {"shape": "glyph", "ref": "m1"}
- Remove it: {"action": "remove", "layer": "fx", "id": "rg1"}
- Fill shapes: {"kind": "asset", "asset_key", "offset_s", "loop"},
  {"kind": "colour", "colour"}, {"kind": "program", "offset_s"} (at most
  2 s away), {"kind": "source", "src_t"}, {"kind": "next"},
  {"kind": "previous"}. Region items carry their device card id as
  "device": region_tone, region_soften or region_material.
