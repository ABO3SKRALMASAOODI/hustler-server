# Brand assets baked into renders

`endcard.png` is the end card every EXPORT closes on (v3, round 99b):

    THIS VIDEO WAS EDITED
        BY AN AI AGENT
         [the robot]
         Valmera.io

Big tracked Plus Jakarta Sans ExtraBold statement, then the GRAY-RED robot
as the largest element on the card, then the wordmark with the ".io" in the
robot's own red. It is a tight-cropped RGBA PNG, composited centred on black
by the renderer and scaled to fit the output frame, so one asset serves
every aspect ratio (9:16, 16:9, 1:1, 4:5).

Rebuild it with `python3 worker/tools/build_endcard.py`.

## robot112.png — the mark

`robot112.png` is the BILLING PAGE's Pro-plan robot — the gray-red one
(`frontend-next/public/hustler-robot112.riv`) — rendered from that Rive
artboard rather than redrawn, so the end card and the pricing page show the
same character. (v2's card wore the white navbar robot, `robot.png`, kept
here for history.)

Regenerate it by rendering the .riv in headless Chrome — Rive is vector, so
a 2400px canvas is a clean master:

```bash
# stage rive.js + the wasm from frontend-next/node_modules/@rive-app/canvas
# and hustler-robot112.riv beside a page that draws it to a 2400x2400 canvas
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --allow-file-access-from-files \
  --default-background-color=00000000 --virtual-time-budget=8000 \
  --window-size=2400,2400 --screenshot=robot112.png file://$PWD/hi.html
# then crop to the alpha bbox
```

The artwork is drawn for a LIGHT card: its pure-black parts (antenna stalk,
neck, visor plate, chest port, upper legs) read as ink there and would
dissolve into the black end card entirely — a floating red antenna ball, a
head hovering off its body. `build_endcard.py::_lift_blacks` raises exactly
those near-black pixels to a dark charcoal (structurally — by pixel value,
never by hard-coded rectangles — and it raises rather than ships if the
asset changes and nothing lifts). The body grays get a 1.22x brightness lift
on top (charcoal at reel scale on black read as mud), and a barely-there
red-leaning radial glow sits behind the mark so the silhouette separates.

## Type

Headline, wordmark and ".io" are all Plus Jakarta Sans (SIL Open Font
License 1.1) at weight 800; the wordmark keeps the site navbar's -0.03em
tracking, the headline runs +0.045em for air. The ".io" is the one accent on
the card and uses the robot's own red — a second red anywhere else tips the
card from premium into ad. The FONT is not bundled — only the rendered
pixels are, and OFL restricts distributing font software, not images made
with it. The build script fetches it from Google Fonts when regenerating.

`config.OUTRO_VERSION` (worker) AND `routes/video.py OUTRO_VERSION`
(backend) must BOTH be bumped whenever this card's look changes: the stamp
busts the render cache and the backend's final-is-current gate, otherwise
finished exports keep serving bytes that end on the OLD card. test_units
checks the two constants match.
