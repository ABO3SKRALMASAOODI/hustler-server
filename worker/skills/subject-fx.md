# subject-fx — the person cut-out as a graphic: white silhouette flashes, posters on a colour field, glows, sticker outlines, subject-only negatives and grades, colour isolation, clones, and new backgrounds behind the subject

## Editorial decision principles

The subject cut-out is one of the strongest devices in short-form: the
person becomes the graphic. That is exactly why it is rare. A subject
effect earns its place on a STRUCTURAL beat — the payoff word, the turn of
the argument, a section change, the drop of a track the user supplied — and
never as decoration on an ordinary sentence. Two or three in a 30–45 s
talking head is already a lot; zero is a finished edit when nothing calls
for one.

- **Fill** (`add_subject_effect` kind fill): the subject goes flat white (or
  the accent, or black) for 1–2 frames with a short decay. It is
  punctuation: on the strongest stress of a payoff line, on a hard cut into
  a new idea, or on the kick of the user's track. One per thought.
- **Poster**: the subject on a flat field — the Look's accent by default —
  as footage or as a flat silhouette (`style.silhouette`). A title-card
  moment: the opening line, a section header, the name of the person.
  Keep it as long as the thought it frames, not longer.
- **Glow**: light around the subject. "This person matters / is the idea."
  White or the accent; it reads best on a darker plate.
- **Outline**: a sticker stroke. Playful, collage-like; avoid on busy
  plates.
- **Negate / exposure / isolate** with `scope`: change only the subject or
  only the world. A subject-only negative is a one-frame shock; a world
  that drops to mono while the subject keeps colour is a mood shift for a
  section; a darkened world pushes the subject forward.
- **Echo**: copies from k frames earlier under the subject (clone, twin,
  trail). Only on a subject that MOVES — a seated speaker stacks the copies
  invisibly.
- **Backgrounds** (`set_background`): a clean blur, a colour or gradient,
  another shot, an asset or a screen recording behind the subject.
  `hold_subject` freezes the cut-out while the world swaps — the
  "same person, new world" beat on a cut or a drop.

Every subject effect is silent and zoom-free: it never brings a sound or a
camera move with it, and none is added to "support" it. Flashing is held to
3 full-frame flashes a second unless the user asked for flashing.

## Evidence to inspect

- Look at the frames first: `look_at` on the moment. A subject effect needs
  a PERSON in shot, clearly separated from the plate; a crowd, a hand-held
  phone at arm's length or a lectern hiding the body make the cut-out
  ambiguous.
- The words: `get_words` / `get_transcript` for the payoff line;
  `get_audio_analysis` for stresses and, when the user supplied a track,
  its beats.
- The program map (`get_edl`, `project_state`): where the cuts and sections
  are, and which fx already sit nearby (one punctuation family per section,
  no device on consecutive shots).
- A write that has not been measured yet says "Measuring the subject": the
  person matte is computed in the background on the batch lane. Until it
  is ready, previews show the stand-in (a full-frame flash for a fill, a
  colour card for a poster, a halation for a glow) or skip the effect;
  finals wait up to 90 s for it.

## Strong treatment patterns

- **Three white hits on the payoff**: `add_subject_effect(kind="fill",
  on="stress", count=3, start=<line start>, end=<line end>)` — the three
  strongest stresses of that line, at least 1 s apart, each 1 frame plus a
  2-frame decay. With a user track: `on="beat"` or `on="downbeat"`.
- **The same treatment on every shot of a montage**: one call,
  `add_subject_effect(kind="fill", on="stress", count=1, per="shot")` — the
  strongest stress of EVERY shot (a shot with none takes its strongest
  word), each bound to its word; or `on="cut"` for a hit on every cut.
- **Poster opener**: `add_subject_effect(kind="poster", start=0,
  end=2.5)` puts the speaker on the accent field while the hook title
  lands; `style={"silhouette": "black"}` for the flat graphic version
  behind big type.
- **Glowing figure**: `add_subject_effect(kind="glow", colour="white",
  start=…, end=…, intensity=0.9)`; `style.radius` 0.03–0.06 of frame
  height (the halo reaches about 0.9 of it). For a figure that BECOMES
  light, stack a long fill at intensity 0.6–0.8 in the same colour under
  the glow, and the world mono with an isolate.
- **Subject-only negative on the turn**: `add_subject_effect(kind="negate",
  start=<turn word>, frames=2)`.
- **Mono world, subject in colour**: `add_subject_effect(kind="isolate",
  start=…, end=…)`; add `hue="auto"` to keep one colour of the subject
  (a red jacket) and the matching colour around it.
- **Red ghost clones on a moving subject**: `add_subject_effect(
  kind="echo", echo={"mode": "clone", "offsets_f": [4, 8], "opacity": 0.75,
  "tint": "#ff2a2a"}, start=…, end=…)`.
- **Same person, new world**: `set_background(kind="source",
  source="<media key>", start=<cut>, end=<cut+0.6>, hold_subject=true,
  hold_t=<cut - 1 frame>)`; three plates in rhythm: `kind="strobe",
  sources=[…], style={"hold_f": 4}`.
- **Clean defocus**: `set_background(kind="blur", start=…, end=…,
  style={"radius": 0.025})` — the subject never bleeds into the blur.

## Common failure modes

- A subject effect on every line, or one per shot: decoration, not
  emphasis. Thin to the moments that turn the story.
- A fill on a face-dependent payoff: the expression IS the payoff; flash on
  the word before or after it.
- Two punctuation families in one section (a subject negate next to an
  accent negative or a flash transition).
- Echoes on a still speaker; backgrounds behind a subject the matte loses
  (fast hands, crowds, the lectern).
- A poster held through several thoughts: the field becomes wallpaper.
- Writing `hue` on a kind other than isolate, `field` on a kind other than
  poster, or an `echo` on a kind other than echo: the write is refused with
  the rule.

## Verification procedure

1. Read the write's reply: the ids, the moments it bound, the ledger line
   (`matte n/budget`), any flash-safety drop, and a "Measuring the subject"
   note.
2. `render_preview` and look at the landing frames: the fill covers the
   subject and nothing else, the poster field is flat with no dark fringe
   round the hair, the glow falls off softly, the clone sits where the
   subject was k frames earlier.
3. The render report lists each device's rung. A rung other than `full`
   (a stand-in, a demoted crisp edge, a missing matte window) must be
   acknowledged before a final ships.

## Repair ladder

1. Wrong moment: `remove_subject_effect(ids=[…])` and write it again on the
   right word or cut (or use `on=` with a narrower start/end).
2. Too much: remove the weakest ones; one strong hit beats three weak ones.
3. Edge problems (fringe, chatter): a poster or outline whose matte
   chatters there is drawn as a glow automatically; prefer a glow or a
   background blur on that shot.
4. No matte on this footage (the stand-in keeps showing after the
   measurement): choose a device that does not need the subject — a
   region effect, an accent or a grade.

## Through apply_edit_batch

The same items, written directly (a client that loaded its tools before
these verbs existed reaches them this way). `get_edl` shows them under
`fx`. Times are program seconds (centiseconds; a 1-frame item is
`end = start + 1/fps` rounded up to 0.01). `device` is optional (the card
id the verb writes); colours are `white`, `black`, `accent` or `#rrggbb`.

Fill (1 frame + 2-frame decay at 25 fps):

    {"action": "upsert", "layer": "fx", "id": "sx1", "value": {"id": "sx1",
     "type": "subject", "stage": "world", "start": 4.0, "end": 4.12,
     "kind": "fill", "colour": "white", "decay_f": 2,
     "device": "subject_fill_flash",
     "bind": {"on": "stress", "ref": 12, "rhythm": "speech"}}}

Poster (footage on the accent field; a flat silhouette with
`"keep_footage": false, "colour": "black"`):

    {"action": "upsert", "layer": "fx", "id": "sx2", "value": {"id": "sx2",
     "type": "subject", "stage": "world", "start": 0.0, "end": 2.5,
     "kind": "poster", "field": "accent", "device": "subject_poster"}}

Glow, outline, negate, exposure, isolate, echo (one item each; `scope` is
`subject` or `background` for negate / exposure / isolate):

    {"action": "upsert", "layer": "fx", "id": "sx3", "value": {"id": "sx3",
     "type": "subject", "stage": "world", "start": 7.0, "end": 9.0,
     "kind": "glow", "colour": "#ffd23f", "radius": 0.04, "intensity": 0.9,
     "device": "subject_glow"}}
    {"action": "upsert", "layer": "fx", "id": "sx4", "value": {"id": "sx4",
     "type": "subject", "stage": "world", "start": 2.0, "end": 3.0,
     "kind": "outline", "colour": "white", "width_px": 4,
     "device": "subject_outline"}}
    {"action": "upsert", "layer": "fx", "id": "sx5", "value": {"id": "sx5",
     "type": "subject", "stage": "world", "start": 1.0, "end": 1.08,
     "kind": "negate", "scope": "subject", "device": "subject_negate"}}
    {"action": "upsert", "layer": "fx", "id": "sx6", "value": {"id": "sx6",
     "type": "subject", "stage": "world", "start": 12.0, "end": 14.0,
     "kind": "exposure", "scope": "background", "ev": -1.5,
     "saturation": 0.0, "device": "subject_exposure"}}
    {"action": "upsert", "layer": "fx", "id": "sx7", "value": {"id": "sx7",
     "type": "subject", "stage": "world", "start": 14.0, "end": 20.0,
     "kind": "isolate", "hue": "#c0392b", "hue_op": "union",
     "device": "subject_isolate"}}
    {"action": "upsert", "layer": "fx", "id": "sx8", "value": {"id": "sx8",
     "type": "subject", "stage": "world", "start": 20.0, "end": 21.0,
     "kind": "echo", "echo": {"mode": "clone", "offsets_f": [4, 8],
     "opacity": 0.75, "tint": "#ff2a2a"}, "device": "subject_echo"}}

Backgrounds (`kind` solid, gradient with `colour2` and `angle`, blur and
radial_blur with `radius`, source with a `source`, strobe with `sources` and
`hold_f`; `hold_subject` + `hold_t` freeze the cut-out):

    {"action": "upsert", "layer": "fx", "id": "bg1", "value": {"id": "bg1",
     "type": "background", "stage": "world", "start": 3.0, "end": 6.0,
     "kind": "blur", "radius": 0.025, "device": "background_blur"}}
    {"action": "upsert", "layer": "fx", "id": "bg2", "value": {"id": "bg2",
     "type": "background", "stage": "world", "start": 15.0, "end": 15.6,
     "kind": "strobe", "hold_f": 4, "hold_subject": true, "hold_t": 14.96,
     "sources": [{"kind": "asset", "asset_key": "media/p1/plate1.mp4"},
                 {"kind": "asset", "asset_key": "media/p1/plate2.png"},
                 {"kind": "source", "src_t": 2.0}],
     "device": "background_subject_hold"}}

A source is `{"kind": "asset", "asset_key": …, "offset_s", "loop"}` (a
project image, video or screen recording), `{"kind": "source", "src_t": …}`
(the main video from that source second: another shot, read from the
main video even where the program never plays it),
`{"kind": "program", "offset_s": …}`, `{"kind": "colour", "colour": …}`,
or `{"kind": "next"}` / `{"kind": "previous"}` (the neighbouring shot's
first / last frame, held).

`hold_t` is a program second and does not move with the item: after a
trim that moves a held background, write its `hold_t` again (a `hold_t`
left past the end is dropped, and a preview window that renders the item
alone holds the item's own first frame). Echo copies
and plates read far ahead of their window are bounded per render (the
farthest copies are left out, with the reason in the render report).

Remove any of them: `{"action": "remove", "layer": "fx", "id": "sx1"}`.
