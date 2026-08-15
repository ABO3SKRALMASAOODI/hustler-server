# Brand assets baked into renders

`endcard.mp4` is the animated signature every export closes on (v7, round
102). It is a 1080×1920, 2.5-second H.264 asset with one short reading path:

    Edited by
    [robot] Valmera AI
    www.valmera.io

The headline appears first, the compact robot + Valmera lockup follows, and
the URL resolves last. The signature occupies about 20% of a vertical reel's
height and then holds, so it is recognizable without becoming a second piece
of content after the viewer's video.

`endcard.png` is the fully revealed poster. The renderer prefers the MP4 and
uses the PNG only as a graceful fallback if the animation is missing from a
build. Both are scaled to fit on black, never cropped, so the same 9:16 master
pillarboxes cleanly on 16:9, 1:1 and 4:5 exports.

Rebuild both assets with:

```bash
python3 worker/tools/build_endcard.py
```

## Design contract

- One message only: attribution. The old descriptor and CTA pill were removed
  because five stacked elements were too much to parse at the end of a reel.
- “Edited by” is the largest type. The robot and Valmera AI name are supporting
  marks; `www.valmera.io` is the quiet but fully legible final read below them.
- The reveal uses three restrained upward fades over 0.75 seconds, followed by
  a clean hold and fade to black. There is no bounce, glow, panel or background
  texture competing with the mark.
- The attribution uses `Plus Jakarta Sans ExtraBold`: larger, upright and
  friendlier than the previous italic. The supporting lockup stays in
  `Inter Display`, and the URL uses natural spacing.

## Robot assets

`robot.png` is the white navbar/free-plan robot from
`frontend-next/public/hustler-robot95.riv`. `robot112.png` is the billing
page's gray-red Pro-plan robot, retained for favicon history.

The end card uses `robot.png` at 148px high. Its antenna stalk is repainted
white by `build_endcard.py::_white_stalk` so the red ball reads as attached on
black; every other pixel is preserved. Do not regenerate or reinterpret the
robot for this card—the exported site mark is the brand source of truth.

## Cache version

`config.OUTRO_VERSION` in the worker and `routes/video.py OUTRO_VERSION` in the
backend must both be bumped whenever the card's look or motion changes. The
stamp busts cached finals; without it, existing downloads keep serving the old
card even after a new asset ships. `worker/tests/test_units.py` asserts the two
constants match.
