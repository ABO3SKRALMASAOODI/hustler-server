# Brand assets baked into renders

`endcard.mp4` is the animated signature every export closes on (v13).
It is a 1080×1920, 5-second H.264 asset with a centered reading path:

    Edited using
    [robot] Valmera AI
    Autonomous AI video editor

    VALMERA.IO

The larger attribution leads, followed by the robot/name, a smaller descriptor,
and the address lower in the frame. All elements resolve within 0.36 seconds
and hold until the final 0.2-second fade.

`endcard.png` is the fully revealed poster. The renderer prefers the MP4 and
uses the PNG only as a graceful fallback if the animation is missing from a
build. Both are scaled to fit on black, never cropped, so the same 9:16 master
pillarboxes cleanly on 16:9, 1:1 and 4:5 exports.

Rebuild both assets with:

```bash
python3 worker/tools/build_endcard.py
```

## Design contract

- “Edited using” is the largest type. The robot and Valmera AI name support
  the attribution. The smaller descriptor explains the product category;
  `VALMERA.IO` retains its own clear space at 75% of the frame height.
- The approved attribution/name and address positions remain unchanged when
  the descriptor is added. Each element is centered by its visible ink.
- The reveal uses short, restrained upward fades. A clean hold and final
  fade complete the five-second card. There is no bounce, glow, panel or
  background texture competing with the mark.
- The attribution uses `Plus Jakarta Sans ExtraBold`: larger, upright and
  friendlier than the previous italic. The supporting lockup stays in
  `Inter Display`, and the uppercase URL uses the approved bold spaced style.

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
