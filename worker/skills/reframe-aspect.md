# reframe-aspect — vertical/square conversion, screen frames, erasing pixels, blurs

CHANGING ASPECT — TWO DIFFERENT ASKS.
- "Make it 9:16 / vertical / for TikTok / Shorts / Reels / crop it" → FILL THE PHONE. auto_reframe("9:16", mode="crop") or set_frame("9:16", "crop"). A postage-stamp of gameplay in blurred bars is the wrong conversion for a Short. Aim the crop at the action (focus from a look, or auto_reframe) so the fight/subject fills the frame.
- "Fit the whole picture / keep the HUD / letterbox / don't crop" → pad_blur. auto_reframe("9:16", mode="pad_blur") or set_frame("9:16", "pad_blur"). Screen recordings and "don't lose the UI" briefs live here.
- A bare set_frame crop is a DEAD-CENTER window — on an off-center speaker it looks "cut down the middle". set_frame(ratio, mode, focus_x, focus_y) is the manual aim (focus from a look).
- HONEST LIMIT: the focus is one fixed point for the whole video; it does not track a moving subject — say so. On a Short, still crop-fill; don't fall back to pad_blur just because the subject moves.

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
