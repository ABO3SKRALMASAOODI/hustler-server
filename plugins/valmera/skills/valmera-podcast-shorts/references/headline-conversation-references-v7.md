# Headline conversation — user reference set, September 9

Use for the user's current four-style brief and `headline-conversation` lane.
These are the user's approved style directions, with independently inspected
visual evidence. They are not generic requirements for unrelated users.

## Local reference library

The user supplied three files from Downloads. Verified copies, metadata,
SHA-256 hashes, full-duration contact sheets, sampled OCR, and cut-boundary
evidence are under:

`/Users/masaoodi/Documents/Valmera/.valmera/podcast-shorts/reference-library/20260909-headline-conversation/`

Read `inventory.json` for exact paths/hashes and `analysis.md` for timestamped
observations and limitations. Original filenames remain intact:

| ID | Filename | Measured dimensions | Duration |
| --- | --- | --- | --- |
| ref-05 | dca7eda5921f4bbe9b42a43bc8df67b3.MP4 | 720x1280, 9:16 | 78.367 s |
| ref-06 | db41af7529b94f5e8332a867be5efb52.MP4 | 720x1280, 9:16 | 61.067 s |
| ref-07 | ab28cc01c2fb495692d464c61c8e2443.MP4 | 720x1280, 9:16 | 54.900 s |

The dimensions are the encoded file, not the inner picture. All three show a
wide rectangular picture inside large black top/bottom margins. Their inner
picture crops vary; do not label the files 4:5 or 4:3. The headline sits in
black space immediately above the picture, not at the extreme top of the file.

## Current requested composition and later corrections

- Outer canvas: 9:16, black background, straight-edged picture window spanning
  the width. Start with a roughly 4:3 inner picture as a design approximation,
  and adjust its crop/height to the source and reference composition. This is
  not a claim that every reference contains an exact 4:3 panel.
- Place the picture around the vertical middle, leaving room for a readable
  one- or two-line headline immediately above it. Preserve faces, gestures,
  and relevant scene context. Inspect the actual picture region, not just
  the exported width/height. Do not stretch the source to force a shape.
- This black-canvas composition is the default across this brief's shorts;
  rounded corners, a floating card, gradients, and shadows are optional
  alternatives rather than mandatory finishing effects.
- The September 14 correction removes Style 4's default status. Choose it
  when the exchange, expression, or argument is best served by clean footage
  and a persistent headline; evaluate the other lanes for each idea using
  `style-lanes-v7.md`. It has no B-roll, external action opening, picture
  montage, or standalone editorial cards. Natural camera changes are allowed.
- Style 4 keeps a single topic headline above the picture from first to last
  editorial frame, plus separate changing dialogue subtitles horizontally centered
  around the middle of the visible picture rectangle, matching the references.
  Anchor subtitles to that inner rectangle rather than the full portrait
  canvas or lower black margin. Keep placement consistent across shots and
  protect faces through reframing or small shifts within the central area.
  Use an actual bold/heavy headline face with at least the visible stroke
  weight of the dialogue captions. Check the rendered hierarchy at phone size.
  Other lanes retain their own headline timing. Write a truthful new headline;
  do not copy creator endorsements, handles, watermarks, or logos. Do not place
  a smaller interview date, location, venue, or other incidental metadata
  beneath it; the headline is the story promise, not a source label.
- Keep 15–45 seconds for complete new outputs, reserving time for the required
  native Valmera ending. Do not copy reference creators' promotional tails;
  preserve Valmera's own complete branded ending and corner watermark using
  `style-lanes-v7.md`. Neither is unwanted third-party branding.
- Keep the no-added-music brief. The reference audio does not authorize adding
  a track; no subjective claims about hearing or beat matching were made.

## Practical tool checks

Verify the current tool schema before editing. `set_frame` distinguishes the
output ratio and crop/pad modes. A 9:16 setting alone does not create the
desired inner picture region. Preserve or crop the source for the intended
wide panel, then fit that panel within the black canvas using a supported
composition path. Review framing on every speaker change. Do not use an
animated aspect transition to fake a static layout from the first frame.

The inspected `add_text` tool sets `mute_captions: true` for its whole window.
A full-duration headline added this way suppresses all dialogue captions even
when its position is above the picture. For this two-layer design, prefer a
separate transparent headline image placed with `add_overlay` (not
`fit='cover'`), or another live capability explicitly supporting both layers.
Set its program window to the complete editorial duration and verify the render. A
headline graphic is permitted in the no-B-roll lane; it must not replace the
podcast picture. Do not use a full-screen title card for the headline.
The final-only Valmera ending follows that program window and remains intact
without the topic headline being extended over its native design.

Inspect the new candidate for both the headline and dialogue caption stream,
not merely for a successful tool response. Inspect face framing and type at
phone size. Use one consistent headline; do not animate or rewrite it at every
cut. The story must substantiate its promise.
