# screen-takeover — pushing into a screen in the shot, product demos, website capture

## Editorial decision principles

A takeover must preserve spatial continuity between the filmed screen and its content. Use it when the transition improves understanding, not as generic spectacle.

## Evidence to inspect

Inspect screen corners/visibility, tracking confidence, reflections/occlusion, content match, shot boundaries, capture walls, entrance/exit geometry and the complete rendered path.

## Strong treatment patterns

GOING INTO A SCREEN IN THE SHOT (add_screen_takeover): "I filmed my laptop — zoom into the screen and continue with the other scene", "make it go into the phone". ONE tool, one continuous move: the camera pushes into the filmed screen; only once the push is about half done does the clip dissolve onto the glass, corner-pinned so it rides it; the picture flattens out into the full frame and the clip cuts in on the SAME frame the push lands — which is why the join cannot be seen.
- DO NOT build this by hand from add_zoom + insert_media or an overlay over a zoom: an overlay draws ABOVE the zoom, sits flat while the shot pushes past it, and the cut arrives as a jump — exactly what users call "not smooth".
- Call with at_output_s = where the takeover FINISHES (clip full screen); the push happens in the duration_s before that (1.0-1.5s is the move people mean).
- The tool finds the screen's corners itself, in order of trust: MATCHED (the content's own pixels found on the glass — exact), MEASURED (screen-shaped region), READ (vision estimate — it says so). Do not pass `corners` unless all three were refused or the user corrects you. Never estimate a rectangle to get past a refusal: the corners ARE the effect; 2% out slides visibly under magnification. If refused, look_at that moment to check the device is really visible and big enough, then ask the user where the corners are.
- Honest limits to say out loud: the screen must be ~8% of the frame or more (smaller needs a >12x blowup and arrives as mush). Handheld wobble is fine — the pin TRACKS the glass — but a real pan or walk that carries the screen across the frame refuses. An angled screen is handled (content skews on and straightens as it arrives).
- Undo with remove_screen_takeover (takes the push and the clip with it).

PRODUCT DEMOS (record_website_demo + showcase_demo — only when listed in CAPABILITIES): "record my site and show it off", "make a launch video". The browser actually USES the site with a visible cursor — glides to a button, clicks, waits, types at human speed, scrolls. You write the script as `steps`. Then showcase_demo(asset_key) places the capture and cuts it: a zoom that pushes in and GLIDES from click to click, a click sound on the exact frame of each press, a soft pop on page changes.
- SCRIPT A STORY, NOT A TOUR: 4-10 steps showing ONE thing working end to end. A demo that clicks everything shows nothing; ask the user what the one moment is if unclear.
- LET IT BREATHE: pass `seconds` on a step to hold on a result the viewer needs to read.
- It records the PUBLIC site and will not type into password or payment fields — offer to demo the public part, or cut a screen recording they upload. Report every step that did not work; never describe a click that missed as if it landed.

THE USER'S OWN SCREEN RECORDINGS: showcase_demo takes ANY clip — on the user's recording pass click_times=[...] (seconds into the clip) and it lands the click sounds and zooms on them. You CANNOT see clicks in pixels — ASK when the clicks are rather than guessing; if the user does not know, say what will not be synced. The finishing tools: add_zoom_path (a zoom that follows the action), enhance_cursor (bigger, steadier pointer with click ripples), set_screen_frame (the floating product-video window).

WEBSITE CAPTURE (record_website — only when listed): records the LIVE page as real video — opens at the project's aspect, holds the top, smooth-scrolls down, holds (duration_s 4-30; scroll=false to hold the top). SILENT, PUBLIC page only. Like every created asset it reaches the video only when placed (insert_media or add_overlay fit='cover'). If it fails, repeat the tool's reason and offer the upload route.

## Common failure modes

- Wrong screen match, drifting corners, crossing a shot/speed change, occlusion/reflection breakage or capturing a consent/login wall.

## Verification procedure

Review approach, lock, handoff, full takeover and exit; inspect each corner/path extreme and confirm the captured content is the intended public page.

## Repair ladder

Choose a clearer window → shorten/split at the boundary → remeasure match/track → use a direct full-frame insert → request an upload → remove the takeover.
