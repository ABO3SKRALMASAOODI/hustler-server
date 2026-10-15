# effects-grades — one committed look: apply_look, grade and grain, stylize, custom chains, enhancement, speed, fades

## Editorial decision principles

Coherence is the look. Premium reels commit to ONE grade for the whole piece
— high-key warm, low-key teal/orange, matte filmic, a muted noir with one
accent colour — finished with fine grain over everything, a vignette or
highlight bloom when the look calls for it, and a consistent transition and
typography language. Black and white is never that whole-piece grade unless
the user asks for it: in the references it is a MOMENT (below). Effects are punctuation inside that look: flashes,
shakes, glitches and light leaks mark real beats.

- Commit to one look per edit, from the first write.
- Grade for the footage and the story, and keep skin believable.
- Effects mark beats; a stack of five full-frame effects on one instant is a
  fault, not a look.
- Screen recordings and UI keep their native colour.

## Evidence to inspect

Inspect representative frames from every scene, skin tones, white balance,
exposure, palette, source defects (dropouts, noise, compression), motion
energy, the user's reference and the rendered result at every effect
boundary.

## Black and white is a device, never a flat look

The owner's references use B&W as a MOMENT with a reason, never as the
whole short's grade: a mono section that opens to colour on the drop or the
turn, the speaker (or one colour) kept in a grey world, a silhouette held
on a flat field for one thought, 1-frame negatives on the strongest hits.
A whole-short `bw` grade is refused unless the user's own words ask for
black and white (the write says so). To use B&W:
- mono until the turn: `set_grade_window(start, end=<turn word>,
  saturation=0, ramp_out_f=3)` — colour lands ON the word;
- one colour kept: `isolate_colour(keep='subject')` (the speaker) or
  `keep='hue'` (a red jacket), for the section the colour means something;
- silhouette hold: `add_subject_effect(kind='poster')` for one thought;
- negatives on hits: `add_accent(kind='negative', on='stress')`, 2-4 of them.
`suggest_devices` proposes these bound to the right words. One per short,
and a different one from the shorts before it.

## Strong treatment patterns

LOOKS (`apply_look`) — one call composes caption look, grade, grain and
transitions into a single coherent package and reports every component it
set:
- `editorial` — premium podcast/interview default: editorial motion captions,
  warm-neutral grade, fine grain, restrained junctions.
- `creator_punch` — high-energy creator: punchy captions, vibrant contrast,
  fast whips/flashes.
- `cinematic_doc` — story and documentary: filmic contrast, matte blacks,
  grain, light-leak/burn junctions.
- `mono_noir` — noir: deep contrast, colour pushed down (not removed), one
  red accent on type. Black and white is NOT its grade: it is a device on
  one moment (see "Black and white is a device" below).
- `clean_minimal` — quiet product and education: clean captions, neutral
  grade, minimal texture.
- Legacy looks `hype`, `clean`, `cinematic`, `luxury`, `meme` remain.
The descriptions above are the intent of each look; the tool result lists
the exact components it set — read it before refining. A look places no
sound unless it is called with `transition_sounds=true` (only when the user
asked for transition sounds; sound effects are optional, never rules); when
it did, check its receipt or `get_edl` (sfx) before adding junction cues,
and adjust or remove the look's own cues rather than stacking (read audio
for the sparse sound rules). Where the schema lists a music
option, pass it only when the user explicitly asked for generic background
music — never on your own initiative (read music). Refine any component
afterwards with its own tool; do not layer a second look on top. On a
vertical reel there must be no fade-in from black: if a look sets one,
remove it with `set_fades`.

GRADES (`set_color_grade`: vibrant, warm, cool, bw, vintage, cinematic) —
choose FOR the footage: 'cinematic' crushes and desaturates (night, drama,
moody interiors; wrong for a bright kitchen or a colourful product);
'vibrant' for things that should look alive; 'warm' for skin and interiors;
'cool' for tech and rain; 'bw' ONLY when the user asked for black and white
(the write is refused otherwise — a B&W moment is a grade window, below).
`set_grade_custom` adds continuous control after the preset: exposure,
temperature, tint, shadows and highlights use 0 as neutral; contrast and
saturation accept small signed deltas (`contrast=0.08` → 1.08x,
`saturation=-0.08` → 0.92x, 0 clears the axis).

GRAIN AND TEXTURE: grain at a low intensity (about 0.15–0.3 on
`add_stylize` grain) across the whole program reads as film, not noise;
vignette 0.2–0.4 focuses a talking head; glow on highlights suits hero
numbers and night footage. Apply texture program-wide so it never pops on
and off between shots.

STYLIZE (`add_stylize`): grain, vignette, glow, chromatic, dream_blur, vhs,
flash, shake, stabilize, motion_blur — windowed, intensity 0–1. Use flash
(1–2 frames), shake (short, decaying) and chromatic as beat punctuation on
hero landings, together with the graphic (and its sound, when that landing
earns one). 'stabilize'
smooths handheld wobble; 'motion_blur' blurs real movement.

WHEN THE USER LISTS SEVERAL DEVICES ("zoom + flash + shake + glow + speed
ramp") they are asking for several MOMENTS — give each device the beat that
earns it instead of firing them all into one half-second.

PICTURE QUALITY IS NOT A LOOK. "Clearer / sharper / HD" means
`enhance_video` (sharpen + optional denoise), not a grade; it recovers detail,
not resolution.

WRITE YOUR OWN CHAIN (`add_custom_filter`) for a look no preset makes
(CRT phosphor, posterize, duotone, selective hue):
- ONE comma-separated chain on the single video stream; no ';' or
  '[labels]', no file access, same frame size and rate out as in.
- It dry-runs on the real footage before storing; fix the chain the error
  names instead of resending it.
- start/end are program seconds; give it a short label.
- After the preview, look at frames inside the window before describing it.
- RECOLOR ONE THING: `huesaturation` rotates hue for one colour range
  (`huesaturation=hue=120:colors=g:strength=8`; desaturate just blues with
  `huesaturation=saturation=-1:colors=b:strength=10`). Check a frame with and
  without the object.

SPEED (`set_speed` / `remove_speed`): 0.25–4x on a SOURCE range; audio keeps
pitch; everything re-anchors. Below 0.6x frames are duplicated, so prefer
0.6–0.8x and say the trade-off.

FADES (`set_fades`): a closing fade belongs on long-form and cinematic
pieces. On a reel it plays into the loop, and a fade-in spends the only
second where retention is decided on black.

GRADE WINDOWS, ISOLATION AND FINISHES (compositing devices): a grade
window changes one stretch — mono to colour landing on a drop (the ramp
ends ON the frame), a black-and-white then-versus-now; isolate_colour keeps
the subject or one hue in colour while the world goes mono; import_lut and
match_grade carry a supplied look or match two shots of the same set;
add_finish — CRT or LCD re-photography, film, halftone, thermal, X-ray,
blueprint — is a metaphor or an archival treatment, never a default look;
make_variant_set builds registered stills from one frame for a strobe. The
one committed look still rules: these are moments inside it.
`suggest_devices` proposes them where a section earns one, `proof_device`
proves them; read_skill devices.

## Common failure modes

- Two looks mixed (hype captions over a cinematic grade), or a grade that
  changes from shot to shot.
- Crushed highlights or blacks, damaged skin tones, a cinematic crush on
  bright cheerful footage.
- Texture that pops on and off; grain so heavy it reads as noise.
- Five effects on one instant; effects masking information; a fade-in on a
  reel.

## Verification procedure

Review representative rendered frames from every distinct scene plus every
effect boundary: one consistent grade, believable skin, readable type over
the graded picture, grain visible but quiet, punctuation effects landing on
their beats.

## Repair ladder

Reduce intensity → scope to the intended scene or colour → correct custom
controls → unify to the one committed look → remove the extra effect → render
every affected scene again.
