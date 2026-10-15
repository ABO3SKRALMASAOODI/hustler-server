# layouts — the layout stage: living mosaics, scroll walls, flat-lays, independent stacks, device stages with light spill, HUD furniture, the edit's own breakdown, glass cards and spec sheets

## Editorial decision principles

A layout device changes what the FRAME is for a moment: a wall of
everything, two worlds side by side, the product's screen on a stage, the
edit showing its own workings. It earns its place at a structural point —
an opening, a section change, the payoff of a list, a close — and it is
built from REAL footage (the user's clips, the main video at a source
second), never stock.

- **Mosaic** (`add_collage` kind mosaic): an event recap or a launch
  opener; tiles pop on the user's beats, the wall breathes, then collapses
  tile by tile into ONE hero tile that fills the frame and carries the
  title. Choose the hero tile for its colour and light — it is the shot
  the piece lands on.
- **Scroll wall**: "everything we made", the volume of a body of work.
  2–4 s, a title over it at most.
- **Flat-lay**: the kit, the ingredients, the products — cutouts dropped
  one per beat on a surface seen from above.
- **Stack** (`set_stack`): two to four INDEPENDENT sources at once — then
  and now, the edit and its source, three angles of one moment. One panel
  can grow to take the frame. Accents (a flash, a negative) can be clipped
  to one panel with `add_region_effect` and the rect the verb replies with.
- **Device stage** (`add_device_stage`): the product's own recording in a
  laptop / phone / tablet / monitor, its light spilling on the stage; a
  `breakout` lifts the part of the UI the line is about out of the screen.
- **HUD** (`add_hud`): furniture that runs through a whole piece — venue,
  date, chapter, REC, timecode, brackets. Small and steady; it never
  enters the corner-mark zone (the verb measures it per aspect).
- **Breakdown** (`add_breakdown`): the edit's own timeline with a live
  playhead and the true device names. A "how this was made" beat; it is
  truthful because it is drawn from the EDL at render.
- **Glass card / spec sheet** (`add_motion_graphic` templates `glass_card`,
  `spec_sheet`): one fact on a frosted card (attach it to a fingertip); leader
  lines from a product's parts to the user's own facts.

Invent: a stack whose panels are the same moment from three days; a
mosaic that collapses into the speaker's own face tile on the payoff; a
breakdown that appears only for the last device, revealing the trick. Pick
the layout THIS story needs, not the one used last time.

## Evidence to inspect

- The footage you name: `look_at_asset` / `look_at` the tiles or panels —
  a dark or empty clip is a dead tile (the mosaic's hero especially).
- The corner mark and captions: layouts mute captions while they own the
  frame; a HUD or breakdown keeps them.
- Beats: the user's track's beats (`analyze_music` when a track exists) or
  the prosody grid give the pop times; never add music to get beats.

## Strong treatment patterns

- `add_collage(kind='mosaic', tiles=[...36 keys...], start, end,
  at=[beats], hero=7, title='DEV DAY')`.
- `set_stack(panels=['source:812.0', 'asset:media/p1/then.mp4'],
  labels=['NOW', 'THEN'], grow=0, grow_at=<payoff>)`.
- `add_device_stage(screen='asset:media/p1/studio.mp4', device='laptop',
  breakout=[0.05, 0.1, 0.45, 0.4], breakout_at=<word>)`.
- `add_hud(tl='DEV DAY 2025', tr='SAN FRANCISCO', bl='KEYNOTE')` for the
  whole program.

## Common failure modes

- A mosaic of the same clip repeated: variety of tiles is the point.
- Layouts back to back: one at a time, and never two consecutive sections.
- A device stage whose recording is unreadable at the screen's size:
  pick a recording where the UI is large, or use the breakout.
- A breakdown on an edit with nothing to show (one cut, no devices).

## Verification procedure

- Preview: tiles play (not frozen), pops land on their beats (±1 frame),
  the collapse ends on the hero filling the frame; panels never bleed into
  each other; the HUD never touches the top-left corner-mark zone.
- The render report names any slot "could not be filled" (an unreadable
  source shows the page's placeholder): replace that tile.

## Repair ladder

1. A dead tile: replace its source.
2. Pops off the beat: pass `at` from the user's beats.
3. Too busy: fewer tiles, or a grid layout.
4. Remove: `remove_motion_graphic`.

## Through apply_edit_batch

```json
{"action": "upsert", "layer": "motion", "id": "co1", "value": {"id": "co1",
 "template": "mosaic", "start": 0.0, "end": 9.5, "layer": "above_captions",
 "params": {"tiles": ["asset:media/p1/a.mp4", "source:12.5"],
 "at": ["0.12", "0.30"], "hero": 1, "title": "DEV DAY"}}}
{"action": "upsert", "layer": "motion", "id": "ds1", "value": {"id": "ds1",
 "template": "device_stage", "start": 3.0, "end": 8.0,
 "params": {"screen": "asset:media/p1/studio.mp4", "device": "laptop",
 "breakout": ["0.05", "0.1", "0.45", "0.4"], "breakout_at": 2.0}}}
{"action": "upsert", "layer": "motion", "id": "bd1", "value": {"id": "bd1",
 "template": "breakdown", "start": 0.0, "end": 20.0, "params": {}}}
```
