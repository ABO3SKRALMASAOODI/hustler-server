# reframe-aspect — vertical/square conversion, screen frames, erasing pixels, blurs

CHANGING ASPECT MUST RE-FIT THE VIDEO, NOT TRUNCATE IT.
- "Make it 9:16 / vertical / for TikTok" on real footage → auto_reframe("9:16") and let it decide HOW. It measures faces AND how much picture detail would survive: it crops footage with a subject to follow, and FITS everything else (gameplay + HUD, screen recordings, wide scenes) into the frame over a blurred copy — losing nothing. Read what the tool reports and repeat THAT: if it fitted, say the soft bands are their own footage blurred; never claim subject-aware framing when it used a center crop.
- A bare set_frame crop is a DEAD-CENTER window — on an off-center speaker it looks "cut down the middle". set_frame(ratio, mode, focus_x, focus_y) is the manual control (focus from a look); "1:1", "4:5"; pad/pad_blur letterbox instead of cropping — pad_blur is the right default for screen recordings, gameplay and wide landscapes.
- HONEST LIMIT: the focus is one fixed point for the whole video; it does not track a moving subject — say so and offer pad_blur when the subject moves across shots.
- Gameplay is the footage most often ruined by a vertical CROP: HUD, minimap, kill feed and score live at the edges — fit it, don't crop two thirds away.

MID-VIDEO ASPECT CHANGE (add_aspect_shift): the frame morphs to another ratio mid-video and back (ratio='source'), timing untouched. Remove with remove_aspect_shift.

SCREEN FRAME (set_screen_frame): the floating rounded window with a shadow on a colour/gradient backdrop — "make it look like those product videos". Remove with remove_screen_frame.

ERASING PIXELS FOR REAL (you CAN remove burned text/objects — stop offering a blur as the best you can do):
1. find_burned_text measures the exact boxes from the frames and says what each is (caption band, watermark, label) and when it is visible — never guess a rectangle.
2. erase_region (one box) or erase_burned_text (every caption band in one pass) REPAINTS those pixels and reconstructs the picture behind them. fill='text' removes only letter strokes (captions, subtitles, handles, usernames); fill='box' repaints the whole rectangle (an object, a sticker, a solid logo).
- The tool measures the result and reports ink before/after: only say "removed" when the measurement says gone. STILL VISIBLE → widen the box or switch fill='box' and try again. Reconstruction is excellent for text and steady shots; a big object on a moving, detailed background can leave a soft patch — check the preview and say so.
- blur_region is for when the user WANTS a visible censor bar (a face, a document, a phone number). Remove with remove_blur; undo erases with remove_erase.

CURSOR (enhance_cursor): finds the mouse pointer in a screen recording, filters the jitter, redraws it up to 4x with a ripple at each click time. Remove with remove_cursor_enhance.
