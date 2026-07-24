# Brand assets baked into renders

`endcard.png` is the end card every EXPORT closes on: the Valmera robot above
the wordmark, over a tracked "EDITED WITH VALMERA" caption. It is a
tight-cropped RGBA PNG, composited centred on black by the renderer and scaled
to fit the output frame, so one asset serves every aspect ratio (9:16, 16:9,
1:1, 4:5).

Rebuild it with `python3 worker/tools/build_endcard.py`.

## robot.png — the mark

`robot.png` is the SITE's robot: the full-body white robot in the landing-page
navbar, i.e. `frontend-next/public/hustler-robot95.riv`, rendered from that Rive
artboard rather than redrawn, so the end card and the site show the same
character. (The first card redrew a HEAD-ONLY robot from the 180px favicon —
vector-crisp, but a different mark from the one users actually see in the nav.)

Regenerate it by rendering the .riv in headless Chrome — Rive is vector, so a
2400px canvas is a clean master:

```bash
# stage rive.js + rive.wasm from frontend-next/node_modules/@rive-app/canvas
# and hustler-robot95.riv beside a page that draws it to a 2400x2400 canvas
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --allow-file-access-from-files \
  --default-background-color=00000000 --virtual-time-budget=8000 \
  --window-size=2400,2400 --screenshot=robot.png file://$PWD/hi.html
# then crop to the alpha bbox
```

The artwork is three flat colours (white, black, red) and is drawn for a LIGHT
background. On the black card every black part still reads — it is enclosed by
white and becomes negative space — except the antenna stalk, which has white on
neither side and vanishes, leaving the ball floating.
`build_endcard.py::_fix_antenna` repaints exactly those rows white. It finds
them structurally (the only rows whose opaque pixels are all near-black and span
<12% of the width), so a future robot revision cannot silently shift a
hard-coded rectangle, and it raises rather than shipping a floating antenna.

## Type

The wordmark is Plus Jakarta Sans (SIL Open Font License 1.1) at weight 800 with
-0.03em tracking, matching the site's navbar exactly; the caption is weight 600
at +0.30em, 59% white. Two rules learned the hard way: the caption's WIDTH sizes
the whole card (the renderer fits the card into a box, so the widest element
decides how big the mark lands — a long tracked caption shrank the robot to 13%
of frame height), and mid-grey caption text under a pure-white wordmark is the
"cheap web ad" tell — muted white reads as the same brand, grey reads as a
second, worse one. The FONT is not bundled — only the rendered pixels are, and
OFL restricts distributing font software, not images made with it. The build
script fetches it from Google Fonts when regenerating.

`config.OUTRO_VERSION` must be bumped whenever this card's look changes: it is
stamped on every render asset and busts the render cache, otherwise finished
exports keep serving bytes that end on the OLD card.
