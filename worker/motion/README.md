# Valmera motion templates

Browser-rendered motion design for Valmera edits. A template is one file,
`templates/<name>.html`, rendered frame-by-frame by headless Chromium
(`worker/motion_engine.py`) into a transparent clip that the ffmpeg graph
overlays on the program (`worker/motion_layer.py`). Editors place it with
`add_motion_graphic(template=..., start, end, params)`; the EDL stores it in
`edl.motion`.

## File format

```html
<!--MG-SPEC {"title": "...", "category": "type|caption|data|callout|social|layout|transition|texture|cta",
 "description": "one or two sentences: what it is and WHEN an editor should use it",
 "duration": 2.5, "layer": "above_captions", "mutes_captions": false,
 "params": { "text": {"type": "text", "required": true, "max": 90, "hint": "..."},
             "accent": {"type": "color", "default": "#FFD84D"}, ... },
 "sfx": [{"at": 0.0, "kind": "whoosh_soft"}]} -->
<style> ... </style>
<div class="mg-root"> ... </div>
<script> ... uses MG ... </script>
```

The spec MUST be the first thing in the file and valid JSON. Param types:
`str` (single line), `text` (multi-line), `int`, `float` (`min`/`max`),
`bool`, `color` (#RRGGBB), `enum` (`values`), `list` (strings: `max_items`,
`max`), `rows` (objects: `fields`, `max_items`, `max`), `asset` (a project
image the renderer serves to the page; the param arrives as a URL or `null`).
Every non-required param needs a `default`. Keep params few and meaningful;
good defaults matter more than knobs.

`mutes_captions` says the template exists to SAY the spoken words (a slam, a
phrase build, a marker line, a quote). Every heard word reaches the screen
once either way (worker/caption_carry.py): an item drops from the captions the
spoken words its visible text params show, and every other heard word stays
captioned in a band clear of the box it draws, the face and any card layout
(worker/caption_place.py). The flag decides the fallback when no band is
clear — a speech template mutes those words and names them (the write NOTE,
`heard_unshown`), any other keeps them in place — so every text a template
prints must come from its params (that is what the captions are matched
against).

`reads_phrase` says the template is a lockup that reads a phrase row by row
(phrase_build): the engine hands the page `params._reading`
(MotionItem.reading — per row, per printed word, its spoken onset in item
seconds or null) and the page reveals every word on its time, in reading
order. Its `bridges` are always [] now: the phrase's words the rows leave out
are captioned beside the lockup, never set as micro bridge rows inside it (an
old stored reading with bridges still validates; the render recomputes it).

`persistent: true` marks a LAYOUT template that holds a band for the whole
program (the `headline` of a card or letterbox layout). The renderer hands
such an item `MG.yields` — the composition seconds in which another graphic's
box meets its band (`motion_layer.yield_windows`, from the stored footprints
or the templates' estimated boxes) — and the runtime fades the whole page out
before each window and back after it, so the template itself never times
anything. A persistent item carries no spoken words (captions never drop a
word for it), is not counted as a designed moment, and its write contract
lives in `motion_tools._persistent_contract`. One held longer than a clip
may last (`motion_engine.MAX_DURATION_S`) renders as consecutive pieces on
the same composition clock (`motion_layer.render_pieces`).

`sfx` declares sound ROLES relative to the item start (where a sound would
belong if the editor chooses to add one): whoosh_soft, whoosh_hard, swish_short,
swipe, pop_soft, click_ui, tick, kick/impact_soft/impact_hard, ding, chime,
notification, coin, riser_short, riser_long, glitch, shutter, typing,
heartbeat. Roles map onto the owner-approved REAL recordings in
`worker/sound_library/` (`sound_library.ROLE_ALIASES`); a role with no approved
recording is skipped. Graphics are silent by default — `add_motion_graphic(...,
sfx=true)` opts a moment in. A cue's "at" is the visual landing the sound
HITS on (its measured peak lands there; the recording starts early by itself,
and typing starts there), so set it from the composition's own timing, not
from a file. A negative "at" counts back from the item end; a landing that
scales with the item length takes {"land": {"frac", "add", "min", "max"}}
(clamp(duration * frac + add, min, max), e.g. counter's count landing);
{"repeat": {"param": "items", "every": 0.3, "from": 0.2}} repeats a cue once
per list/rows entry. Declare at most one or two cues per template, on the
moment the motion lands — never a sound per word or per caption. Synthesized
sounds were rejected by the owner and must never be added.

## The design space

The page is `MG.W` = 1080 CSS px wide and `MG.H` tall (1920 for 9:16, 1080 for
1:1, 1350 for 4:5, 608 for 16:9). Lay out in CSS px of that space; the engine
scales to the output resolution. Respect platform-safe areas on 9:16: keep
important type between y ≈ 0.08·H and 0.80·H and x ≈ 60–1020 px.

## Layers

`above_captions` (designed moments, the default) and `below_captions`
composite on the finished frame. `behind_subject` puts the composition INTO
the shot: the editor's `add_motion_graphic(..., layer="behind_subject")`
measures a person matte for the window (the `add_text_behind` pipeline), and
the renderer draws the clip on the picture before the zoom stage and lays the
subject back over it — the references' "magazine cover" depth. Design for it:
giant type (25–35% of frame height, near full width) whose letters the head
and shoulders cross, so tops and bottoms stay readable; no box or plate
behind the words; the graphic is part of the picture, so a zoom scales it and
a footage card frames it with the picture. The mask is frame-for-frame with
the source at 1x in the framing it was measured in: when it no longer matches
(a cut inside the window, a speed ramp over its footage, a `set_frame`
change, a missing mask asset) the item falls back to `above_captions` — the
edit that causes it says so, and `set_motion_graphic` re-measures.

## Runtime (MG) — everything must be a pure function of time

Never use `Date`, `performance.now`, `Math.random`, `setTimeout`,
`requestAnimationFrame` or network URLs. Use:

- `MG.params`, `MG.duration` (item seconds), `MG.fps`, `MG.W`, `MG.H`, `MG.t`
- `MG.frame(t => {...})` — per-frame choreography. Declare moving windows
  with `MG.active([[a, b], ...])` so static holds are re-used (big speed
  win). Without a declaration every frame is captured.
- CSS `@keyframes` / transitions are seeked automatically; `animation-delay`
  works for staggers. Prefer JS (`MG.frame`) for anything param-dependent.
- Easing: `MG.ease.outExpo|outCubic|outQuart|outQuint|outBack|inOutCubic|inCubic|smooth|snap|outElastic|...`
- `MG.spring(t, start, {stiffness, damping, mass})` → 0..1 with overshoot.
- `MG.tween(t, start, dur, a, b, ease)`, `MG.range(t, start, dur)`,
  `MG.stagger(i, each)`, `MG.lerp`, `MG.clamp`.
- `MG.set(el, {x, y, z, scale, sx, sy, rotate, rx, ry, skewX, perspective, opacity, blur, bright})`
- `MG.anim(el, t, start, dur, fromState, toState, ease)`
- `MG.split(el, 'words'|'chars')` → `[{el, text, chars}]`
- `MG.fit(el, {width, height, min, max, nowrap})` — shrink-to-fit type.
- `MG.count(t, start, dur, from, to, {prefix, suffix, decimals, ease})`
- `MG.draw(svgPathEl, progress)` — stroke draw-on.
- `MG.typed(text, t, start, cps)`, `MG.wiggle(t, amp, freq, seed)`,
  `MG.noise(x)`, `MG.rand(seed)` (deterministic), `MG.esc(str)`.
- Set `MG.box = [x0, y0, x1, y1]` (design px, include glow/shadow/motion
  overshoot) once layout is known so capture is clipped to it.
- Stacked type: `MG.glyphBoxes(el)` → the ink box of every glyph (pen
  position from the DOM, ink from the font), and `MG.stackGap(upper, lower,
  {clear, pad})` → how far `lower` must move down so none of its glyphs comes
  within `clear` px of a glyph of `upper` in the same column. Tight lockups
  keep their designed leading and push a row down only where a descender or
  swash would hit the caps below (phrase_build, word_slam); measure before
  any transform is applied.

### Legibility: the plate under the graphic

Before rendering, the renderer measures the program picture under every item
at a few moments (every cue of the motion-caption track) — the footage fitted
and zoomed the way the render shows it, as an 18-column luma grid
(`worker/plate.py`) — and passes it as `MG.plate` (`null` when unmeasured).
Templates read it under their OWN laid-out boxes:

- `MG.plateAt(rect, t0, t1)` → `{mean, lo, hi}` (0-1 luma, area-weighted
  mean and 15th/85th percentiles) or `null`.
- `MG.need(rect, {ink, ratio, t0, t1, have})` → the black alpha (0-0.9) the
  plate needs behind light `ink` to reach `ratio`:1 over its bright part
  (4.5 body text, 3 display type, `MG.PANEL_RATIO` for glass cards); `have`
  is darkening the template already lays there. 0 = nothing to do.
- `MG.darkInkOK(rect, {ratio})` — the plate is bright all the way across, so
  `MG.DARK_INK` reads on it (slams, marker lines).
- `MG.backing(parent, rect, alpha, {pad, feather, radius})` — a feathered
  dark pocket behind type (starts hidden; the template drives its opacity;
  `.box` is its painted extent for `MG.growBox`).

The contract: with no plate, or a dark one, a template renders exactly as it
did before plates existed — compute `need` and change nothing when it is 0.

Secondary text (kickers, sub-labels, labels, attributions, chips) keeps a cap
height of at least `MG.MIN_CAP` (2.2%) of the frame height: `MG.minType(el)`
is the smallest font-size for el's font, `MG.floorType(el)` raises it, and
`MG.fitSecondary(el, {width, max})` fits one line down to the floor and then
wraps into balanced lines instead of shrinking further.

Fonts available by CSS family name (variable fonts accept any weight in
their range):
- Grotesk backbone: 'Inter Display' (700/800/900, 700 italic), 'Inter' (100–900,
  italic), 'Inter Tight' (100–900, italic), 'Manrope' (200–800),
  'Space Grotesk' (300–700), 'Montserrat' (100–900), 'Poppins' (300, 800, 900),
  'Plus Jakarta Sans' (800), 'Syne' (800).
- Extended / condensed: 'Archivo' (100–900, `font-stretch` 62%–125% — condensed
  heavy or extended black), 'Unbounded' (200–900), 'Anton', 'Bebas Neue',
  'Archivo Black'.
- Serif accent: 'Instrument Serif' (400, italic), 'Playfair Display' (400–900,
  italic), 'DM Serif Display' (400, italic), 'Bodoni Moda' (400–900, italic —
  high-contrast Didone).
- Script: 'Pinyon Script', 'Great Vibes' (formal), 'Yellowtail' (casual brush).
- Hand / marker: 'Caveat' (400–700). Mono: 'JetBrains Mono' (100–800).
- Emoji render from the system colour-emoji font (Noto Color Emoji in images).

## The quality bar

These templates are what makes a Valmera edit look like a top Instagram
editor made it. Every template must look intentional and expensive at phone
size on real footage:

1. **Motion with physics**: entrances use springs/expo-outs with a short
   blur-to-sharp or mask reveal; staggers of 30–80 ms per word/element;
   nothing moves linearly unless it is a constant drift. Settle, then hold
   perfectly still. Exits are faster than entrances (150–300 ms).
2. **Typography**: tight tracking on bold display type (-0.02 to -0.04em),
   generous size, one family + one accent (serif italic or color). Never
   default browser look. Text must be fitted (`MG.fit`) so any copy works.
3. **Depth and finish**: soft drop shadows for legibility on any footage,
   optional glow, subtle gradients, crisp 1px borders on cards, real
   rounded corners, backdrop-like dark plates when the text needs contrast.
   Light type answers `MG.need` over a bright plate (a pocket, dark ink or a
   darker card) and secondary text respects `MG.minType`.
4. **Robustness**: any reasonable param value must render well — long and
   short copy, 1–3 lines, all enums. Never overflow the frame. Never leave
   a blank first frame unless the design calls for it.
5. **Performance**: declare `MG.active` windows and `MG.box`. Avoid
   animating huge blurred areas every frame when a static hold works.

## Testing

```
PATH=<ffmpeg dir>:$PATH python3 worker/tools/motion_preview.py \
  --template NAME --params '{"text":"..."}' --bg some.mp4 --bg-start 10 \
  --out /tmp/review/NAME --sheet-times 0.05,0.15,0.3,0.6,1,1.5,2.4
```

Add `--bg-fit crop --bg-focus 0.4 --plate` to review a template over a real
bright plate exactly as a render measures it (pockets, dark ink).

Look at the contact sheet AND step through the dense early frames. Test
extremes of every param. `worker/tests/test_motion_templates.py` renders
every template with its defaults and fails on script errors or empty output.
