# Brand assets baked into renders

`endcard.png` is the end card every EXPORT closes on: the Valmera robot above
the wordmark, over "Edited by Valmera agent". It is a tight-cropped RGBA PNG,
composited centred on black by the renderer and scaled to fit the output frame,
so one asset serves every aspect ratio (9:16, 16:9, 1:1, 4:5).

Rebuild it with `python3 worker/tools/build_endcard.py`.

The robot is REDRAWN as vector primitives in that script, not upscaled. The
only robot art in the repos is a 180px favicon (frontend `public/icon-512.png`)
and an animated Rive file; an 11x blow-up of the favicon looks like a blurry
sticker next to crisp type. Every measurement in the script (superellipse head
fit n=2.2, visor corner radius 20, brow arc R=103, the three antenna droplets)
was read off that favicon programmatically, so it is the same robot at vector
quality. Palette is sampled from it too: #EB3223 red, #F93525 stalk, #EF8783
droplets.

The wordmark is set in Plus Jakarta Sans (SIL Open Font License 1.1) at weight
800, matching the site. The FONT is not bundled — only the rendered pixels are,
and OFL restricts distributing font software, not images made with it. The
build script fetches it from Google Fonts when regenerating.
