# finishing — section grades, one kept colour, LUTs, shot match, screen and print finishes, registered variants

## Editorial decision principles

A finish is a decision about what the viewer is looking AT, not a filter.
Every device here changes the whole picture, so each one must carry a
meaning the words alone do not:

- A B&W section says "memory", "then", "before". Colour arriving ON the
  drop frame says "now". Mono that fades out over three frames that end on
  the drop beats a cut to colour a frame late.
- Keeping one colour (the red jacket in a grey world) points at the one
  thing that matters. Keep the colour the story is about, never a colour
  that happens to be loud.
- A shot match makes two cameras or two days one world. Match to the hero
  shot, never to whichever shot came first.
- Variants are metaphors: thermal = heat, effort, the body; X-ray = inside,
  the mechanism; blueprint / line art = how it is built, the plan, data;
  night vision = surveillance, the hidden. Never decoration, never a filter
  for its own sake.
- Screen finishes (lcd, crt, led) put the picture ON a device: a UI or a
  quote that should read as "on a screen", "archival", "broadcast".
- One finish family per section. A finish never sits on the payoff hold
  of a face the viewer must read.
- Zooms and sounds are never part of a finish; nothing here adds either.

## Evidence to inspect

Look at the frames of the window before and after a write: skin tones and
the colour you mean to keep (is it really one hue, or is skin the same
hue?), the brightest and darkest areas (thermal and X-ray map them), where
the cut or drop lands (the ramp must end ON it), and the type over the
window (a finish at pre_captions leaves captions clean; at over_type it
finishes the type too). For a shot match, look at a neutral (white shirt,
grey wall) and the face in both shots.

## Strong treatment patterns

GRADE WINDOWS (`set_grade_window`):
- Mono intro that opens to colour on the drop: `set_grade_window(start=0,
  end=<drop second>, saturation=0, ramp_out_f=3)`. The last three frames
  ease, and the drop frame itself is in full colour.
- B&W section between two cuts: start and end on the cut seconds, no
  ramps (a hard switch on the cut is the look).
- Split tone with natural faces: `split_tone={"shadows": "#1f4e79",
  "highlights": "#e9b44c"}`, `protect=["skin"]`.
- `protect=["brand"]` keeps the Look's accent hue untouched by the grade.

ONE KEPT COLOUR (`isolate_colour`):
- `isolate_colour(hue="auto", keep="hue")` solves the subject's dominant
  colour now (skin never counts) and keeps it everywhere it appears; the
  rest goes to clean neutral grey. Name the hue as `#rrggbb` when you know
  it. `keep_skin=True` keeps faces natural inside a mono world.
- `keep="subject"` keeps the PERSON in colour (subject effects);
  `keep="both"` the person plus the hue.

LUTS AND MATCHING:
- `import_lut(url=...)` or `import_lut(asset_key=...)`: a `.cube` up to
  65^3, validated, at `strength` 0-1.
- `match_grade(hero_t=<second of the hero frame>, start, end)` matches a
  section to the hero (mkl by default, reinhard for a gentler move) and
  reports the colour gap before and after. It learns from what both shots
  share; neon and colours more saturated than that keep their own.

FINISHES (`add_finish`):
- lcd / crt / led: a UI capture or a quote card re-photographed off a
  screen (subpixel grid, moire, barrel, bloom). `scale` sets the pixel
  pitch.
- halftone / xerox: print and photocopy, for archival or zine moments.
- film: gate weave, halation and grain for a whole section.
- band_tear: a torn, glitching signal between two ideas.
- thermal / xray / blueprint / line_art / night_vision / solarise /
  posterise / kaleidoscope / pixel_stretch: live variants of the moving
  shot. `matte_weighted=True` makes the person the hottest thing in a
  thermal frame, or clips the X-ray to the body.
- A 2-6 frame thermal or X-ray flip on a transient is a flash: it counts
  against the 3 flashes a second safety limit.

REGISTERED VARIANTS (`make_variant_set`):
- `make_variant_set(t=<program second>, kinds=["photo", "negative",
  "line", "blueprint", "thermal"])` makes project stills of that exact
  frame, registered pixel for pixel, for a 2-frame strobe or a fill. It
  places nothing.

## Common failure modes

- Keeping a hue that the speaker's skin shares: the face blotches in and
  out of colour. Check the kept colour against the face first; use
  `keep_skin=True` or a different hue.
- Lips are red too: a red key (a curtain, a jacket) on a talking-head
  close-up keeps the mouth red in a grey face. Look at the mouth; narrow
  `tolerance` (0.04-0.05), or `keep_skin=True` (the whole face natural).
- A ramp that ends a frame after the drop: colour arrives late. The window
  END is the drop second; `ramp_out_f` eases the frames before it.
- A finish as decoration on a talking head with nothing to say about a
  screen, the body or a plan.
- Two finish families stacked on one section, or a finish on every cut.
- A long thermal or X-ray window: the metaphor wears out in a second or
  two.
- Matching a section to a hero frame inside the same section (refused).

## Verification procedure

Render a preview and look at the first, middle and last frame of every
window, and at the frame after each ramp. Read the write's ledger line.
For a kept colour, check the background is neutral grey and the kept
object is its own colour, with no fringe. For a match, compare a neutral
and a face in both shots. For screen finishes, look at a 100% crop: the
grid must be visible and the type still legible.

## Repair ladder

1. Wrong timing: rewrite the same id with corrected start/end
   (`set_grade_window(id=...)` or `add_finish(id=...)`).
2. A colour key that catches skin or lips: narrow `tolerance`, or
   `keep_skin=True`.
3. Too strong: lower `strength` (finishes, LUTs) or saturation/contrast.
4. Wrong device for the moment: `remove_finish(ids=[...])` and choose
   another family, or none.

## Through apply_edit_batch

Clients that connected before these verbs existed write the same items
with `apply_edit_batch` on the `fx` layer; the batch is validated exactly
like the verbs (field rules, media keys, device cards). Times are program
seconds; `end` is exclusive.

Mono to colour landing on a drop at 9.9 s:

```json
{"action": "upsert", "layer": "fx", "id": "gw_mono", "value": {
  "id": "gw_mono", "type": "grade_window", "stage": "world", "start": 0.0,
  "end": 9.9, "saturation": 0.0, "ramp_out_f": 3,
  "device": "grade_section"}}
```

Keep red, the world mono (a stored hue is `#rrggbb`; 'auto' is solved by
`isolate_colour` only):

```json
{"action": "upsert", "layer": "fx", "id": "gw_iso", "value": {
  "id": "gw_iso", "type": "grade_window", "stage": "world", "start": 12.0,
  "end": 18.0, "isolate": {"hue": "#c0392b", "tolerance": 0.12},
  "device": "grade_isolate_hue"}}
```

Split tone with skin protected:

```json
{"action": "upsert", "layer": "fx", "id": "gw_tone", "value": {
  "id": "gw_tone", "type": "grade_window", "stage": "world", "start": 0.0,
  "end": 20.0, "split_tone": {"shadows": "#1f4e79",
  "highlights": "#e9b44c"}, "protect": ["skin"],
  "device": "grade_split_tone"}}
```

An imported or matched LUT (the asset must be in the project):

```json
{"action": "upsert", "layer": "fx", "id": "gw_lut", "value": {
  "id": "gw_lut", "type": "grade_window", "stage": "world", "start": 18.0,
  "end": 28.0, "lut": {"asset_key": "generated/1/lut-0123abcd.cube",
  "strength": 0.8, "origin": "import"}, "device": "grade_lut_import"}}
```

A finish (any kind; thermal weighted by the person):

```json
{"action": "upsert", "layer": "fx", "id": "fi_thermal", "value": {
  "id": "fi_thermal", "type": "finish", "stage": "world", "start": 24.0,
  "end": 25.0, "kind": "thermal", "matte_weighted": true,
  "device": "finish_thermal"}}
```

```json
{"action": "upsert", "layer": "fx", "id": "fi_crt", "value": {
  "id": "fi_crt", "type": "finish", "stage": "over_type", "start": 25.0,
  "end": 28.0, "kind": "crt", "scale": 1.5, "device": "finish_crt"}}
```

Pixel stretch from the middle of the frame to the left:

```json
{"action": "upsert", "layer": "fx", "id": "fi_ps", "value": {
  "id": "fi_ps", "type": "finish", "stage": "pre_captions", "start": 3.0,
  "end": 3.4, "kind": "pixel_stretch", "origin": 0.55,
  "direction": "left", "device": "finish_pixel_stretch"}}
```

Removal restores the exact previous edit:
`{"action": "remove", "layer": "fx", "id": "gw_mono"}`.
Variant stills are made only by `make_variant_set`; a batch names them by
their `asset_key`.
