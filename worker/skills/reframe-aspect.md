# reframe-aspect — vertical/square conversion, face-aware full-bleed, cards on designed backgrounds, screen frames, erasing pixels, blurs

## Editorial decision principles

Aspect conversion is shot-aware composition, not one global center crop. Preserve the subject, UI target and visual intent in every distinct scene. On a vertical reel the frame is designed edge to edge: face-aware full-bleed for talking heads and modern podcasts, or the picture as a card on a designed background when a crop would destroy it — never a small picture on a flat black void.

## Evidence to inspect

Inspect every shot/visual cluster, faces, body/action, UI/text geometry, path extremes, shot boundaries, safe areas and the full rendered sequence.

## Strong treatment patterns

CHANGING ASPECT — TWO DIFFERENT ASKS.
- "Make it 9:16 / vertical / for TikTok / Shorts / Reels / crop it" → compose for the vertical frame, preserving essential information. Crop when it preserves the action, face, HUD or demonstration. auto_reframe("9:16", mode="crop") or set_frame("9:16", "crop") — except a low-resolution source (LOW-RESOLUTION below), where a full-bleed crop smears. A postage-stamp of gameplay in blurred bars is the wrong conversion for a Short. Aim the crop at the action (focus from a look, or auto_reframe) so the fight/subject fills the frame without losing information required by the brief. Use a composed fit or layout when a crop would remove it.
- "Fit the whole picture / keep the HUD / letterbox / don't crop" → pad_blur. auto_reframe("9:16", mode="pad_blur") or set_frame("9:16", "pad_blur"). Screen recordings and "don't lose the UI" briefs live here.
- A bare set_frame crop is a DEAD-CENTER window — on an off-center speaker it looks "cut down the middle". set_frame(ratio, mode, focus_x, focus_y) is the manual aim (focus from a look).
- Use focus_track for shot-specific focus in SOURCE seconds; one fixed focus_x/y alone does not track a moving subject.
- PODCAST AND TALKING-HEAD REELS: default to face-aware full-bleed — auto_reframe("9:16", mode="crop") or set_frame with a shot-aware focus_track so each speaker's face sits in the upper-middle at roughly 28–40% of the frame height, leaving clear space for type. Upscaling a 1080p 16:9 source to fill 9:16 (about 1.8x) is normal for this look; check sharpness in the render and use enhance_video if needed. A 480p or archival 4:3 talk is not this case: use auto mode (next item), not mode="crop".
- LOW-RESOLUTION / ARCHIVAL SOURCES: a crop that enlarges the source past ~2.5x (a 480p or 4:3 archival talk is 4x) smears under sharp type. auto_reframe("9:16") in auto mode FITS such footage as a window and reports the free top band — put the headline there (who is speaking + the claim) and captions in the bottom band, or finish it as a picture card on a designed background. Force full-bleed only when the user asked to fill the screen.
- BURNED-IN INSETS AND FRAME EDGES: auto_reframe slides a crop off a persistent straight edge (browser/PIP inset, screen, letterbox border) that would leave a thin sliver (up to ~15% of the crop) down its edge while the subject stays central, and set_frame names the x that would exclude it from an authored crop. The detector cannot tell an inset from a door frame or wall panel: an "EDGE BAND (look before acting)" note means look_at the shot first — act (re-aim, fit that span, or show the inset deliberately as a cutaway or stacked layout) only when it really is a burned-in inset or screen.
- focus_track span edges within 0.25 s of a source camera cut snap onto the cut, so the crop re-aims exactly on it.
- PICTURE CARD ON A DESIGNED BACKGROUND (archival 4:3, wide two-shots, evidence a crop would destroy): set_frame(ratio="9:16", mode="crop", picture=[left,top,right,bottom]) places the picture natively (a 4:3 picture at full width is picture=[0,0.2890625,1,0.7109375], about 42% of the height; a taller rect in crop mode with focus on the subject trims it toward 1:1 or 4:5 so the card fills 55–70% of the height, which reads stronger when the sides hold nothing essential). crop/pad/pad_blur and focus apply INSIDE that rectangle; text and captions stay sharp on the full canvas. Then set_picture_card gives it rounded corners, border, shadow and an entrance, and a designed background — a blurred darkened copy of the picture, a gradient, grain or vignette (read the set_picture_card schema) — never a flat black void. add_overlay(fit="picture") preserves that layout during a B-roll cutaway.

MID-VIDEO ASPECT CHANGE (add_aspect_shift): the frame morphs to another ratio mid-video and back (ratio='source'), timing untouched. Remove with remove_aspect_shift.

SCREEN FRAME (set_screen_frame): the floating rounded window with a shadow on a colour/gradient backdrop — "make it look like those product videos". Remove with remove_screen_frame.

ERASING PIXELS FOR REAL (you CAN remove burned text/objects — stop offering a blur as the best you can do):
1. find_burned_text measures the exact boxes from the frames and says what each is (caption band, watermark, label) and when it is visible — never guess a rectangle. It reads the RAW source: a mark you already erased keeps listing (annotated "ALREADY repainted") — that is NOT a failure signal.
2. erase_region (one box) or erase_burned_text (every caption band in one pass) REPAINTS those pixels and reconstructs the picture behind them. fill='text' removes only letter strokes (captions, subtitles, handles, usernames); fill='box' repaints the whole rectangle (an object, a sticker, a solid logo).
- The tool measures the result and reports ink before/after: only say "removed" when the measurement says gone. STILL VISIBLE → widen the box (outlines and shadows sit outside the letters) or switch fill='box'.
- INK GONE IS NOT PROOF IT LOOKS CLEAN. Static marks on steady shots repaint invisibly; ANIMATED marks (moving/boxed caption bands, stickers) can ghost or smear even when ink measures gone. After erasing one, look_at(output_times=[...]) at 2-3 moments inside the window on the next preview and judge with your own eyes.
- THE LADDER — one erase, one look, then ESCALATE, never iterate: if the repaint ghosts, re-erasing the same band with a nudged rectangle ghosts the same way (a re-erase REPLACES the earlier repaint — they never stack). Next rungs in order: fill='box' over the exact band; a deliberate cover (blur_region, or mode='black' as a clean matte bar); crop it out (set_frame / auto_reframe) when the mark hugs an edge. Name the rung to the user — "repainted", "covered" and "cropped" are different promises.
- If the user says they still see a mark you cannot find, ask WHERE (which corner, which second) instead of erasing larger and larger guesses.
- blur_region is for when the user WANTS a visible censor bar (a face, a document, a phone number). Remove with remove_blur; undo erases with remove_erase.

CURSOR (enhance_cursor): finds the mouse pointer in a screen recording, filters the jitter, redraws it up to 4x with a ripple at each click time. Remove with remove_cursor_enhance.

## Common failure modes

- A global crop tracks one scene then shows a wall/empty region, clips faces, hides UI/text or uses stale coordinates after a cut.
- A small card floating on flat black for the whole reel, or tiny footage in gratuitous blurred bars.

## Verification procedure

Review every distinct affected scene, all shot boundaries and tracking/path extremes—not only the midpoint—and check platform safe composition.

## Repair ladder

Add shot-specific focus → split at boundaries → widen → switch to pad/composed framing → remove the global crop → rerender all affected scenes.
