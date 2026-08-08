# Brand assets baked into renders

`endcard.png` is the end card every EXPORT closes on (v6, round 101). It is
a **2160×3840 (9:16) RGBA PNG** — the shape every reel is already in:

    THIS VIDEO WAS EDITED BY
          [the robot]
          Valmera.io
    AI VIDEO EDITING AGENT
       [ TRY IT FREE ]

The sentence sets up the robot, the robot answers it, the destination
follows — and the whole stack is centred, so the setup line sits in the
frame rather than pinned to the top edge. The renderer composites it centred on black and scales
it to fit the output frame, so it fills a 9:16 export and pillarboxes
cleanly on 16:9, 1:1 and 4:5.

Rebuild it with `python3 worker/tools/build_endcard.py`.

## One pixel edit, and only one

`build_endcard.py::_white_stalk` repaints the antenna STALK white, so the
red ball reads as attached to the head instead of hovering over it on a
black frame. It finds the head dome structurally — the first row where the
artwork covers more than a fifth of its own width, which the ball (about a
tenth) never does — so the ball, the visor and everything below the head
are untouched however the artboard is redrawn, and it raises rather than
ships silently if nothing repaints.

## Otherwise nothing sits behind the robot, and nothing is done to it

v3 recolored the gray-red plan robot to survive bare black and it read as
the wrong robot. v4 seated it on an elevated panel and the panel read as a
background. v5 replaced the panel with two blurred radial washes and, at
reel scale, the washes read as a grey smudge behind the character — still a
background.

v6 stops doing anything at all. It wears `robot.png` — the WHITE navbar /
free-plan mark — exactly as drawn: no recolour, no ink lift, no glow. The
alpha channel is empty everywhere the artwork is not.

**The known cost.** The mark was drawn for a light page, so its neck, elbow
joints and shins are pure black and are invisible against a black frame.
This is a deliberate trade for true colour on true
transparency. The only two ways out are recolouring the ink (v3, rejected)
or putting light behind the robot (v4/v5, rejected) — if the missing parts
ever matter more than the trade, the real fix is a version of the artboard
whose structural parts are drawn in a dark grey rather than #000, exported
from Rive; nothing in this script should be patching pixels again.

## robot.png / robot112.png — the marks

`robot.png` is the white navbar robot (`frontend-next/public/hustler-
robot95.riv`) and is what the card wears. `robot112.png` is the billing
page's Pro-plan gray-red robot (`hustler-robot112.riv`), kept here for
history (v2–v5 wore it) and because the site icons are rendered from it — a
dark mark reads on Google's white favicon chip where a white one disappears.

Both are captured from the artboard with its state machine STOPPED — the
still version, the same one `PlanCTACard.js` mounts with `autoplay: false`.
(`components/Robot.js` mounts the identical file with the machine running;
that is the landing page's moving robot. The end card is deliberately
still: it plays for 2.5 seconds under a fade and motion there competes with
the wordmark rather than helping it.)

Regenerate either by rendering the .riv in headless Chrome — Rive is
vector, so a 2400px canvas is a clean master:

```bash
# stage rive.js + the wasm from frontend-next/node_modules/@rive-app/canvas
# and the .riv beside a page that draws it to a 2400x2400 canvas
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --allow-file-access-from-files \
  --default-background-color=00000000 --virtual-time-budget=8000 \
  --window-size=2400,2400 --screenshot=robot.png file://$PWD/hi.html
# then crop to the alpha bbox
```

## Type

Every line is Inter Display (SIL Open Font License 1.1): Black for the
wordmark at -0.032em, ExtraBold for the sentence (+0.26em), the descriptor
(+0.30em) and the pill label (+0.13em). v5's Anton is gone — condensed
display type read as a meme caption, and the card's job is to read as
software. The wordmark is the only large type on the card; the CTA pill is
small on purpose, because it is the last thing read, not the first thing
seen. The ".io" is the only red. The FONT is not bundled — only the
rendered pixels are, and OFL restricts distributing font software, not
images made with it.

`config.OUTRO_VERSION` (worker) AND `routes/video.py OUTRO_VERSION`
(backend) must BOTH be bumped whenever this card's look changes: the stamp
busts the render cache and the backend's final-is-current gate, otherwise
finished exports keep serving bytes that end on the OLD card. test_units
checks the two constants match. v6 ships as OUTRO_VERSION = 6.
