# zooms — aiming, travelling paths, screen-recording choreography, crops vs zooms

AIMING — a coordinate is a MEASUREMENT, never an impression:
- Every frame you look at carries a faint tenths grid ((0,0) = top-left, labels .2/.4/.6/.8). Read aim points, rects and positions off it.
- To zoom INTO a thing (a message, a button, a panel): read its box off the grid and pass add_zoom rect=[x0,y0,x1,y1]. cx/cy pin a POINT in place and cannot bring an edge subject to centre — rect framing is the reliable way.
- Frame the THING, not its container: the rect hugs the message/button the user named. A whole-panel rect makes the subject small; "zoom more" means a tighter rect or higher strength on IT.
- A chat message is its bubble PLUS the avatar/label beside it — extend the rect to the panel edge (x0=0 for left-side messages) or the avatar gets clipped.
- Aim coordinates come only from UNZOOMED frames: a tile labeled as zoomed shows the magnified view's screen coordinates, not positions you can aim at.
- To RETIME an existing zoom ("make it longer"): KEEP ITS AIM — copy rect (or cx/cy) from get_edl and change only start/end. Re-deriving a target you already had is how a correct zoom moves to the wrong place.

GRAMMAR — gentle is the default:
- strength 0.08-0.18 (a push the viewer feels rather than sees), mode 'ease' or 'push_in'. A hard 'punch' above ~0.3 is a deliberate hype device for the single biggest peak or an explicit request — never the routine move.
- 2-3 zooms a minute on the real turns of the argument; never on a filler word, never adjacent, never all the same size. One harder punch on the single peak reads as a hit; ten punches read as a nervous tic.

TRAVELLING ZOOMS (add_zoom_path) — when the user asks a zoom to MOVE ("keep it, then move to my prompt, then the answer"): ONE add_zoom_path visiting each subject as a rect keyframe — never a chain of static zooms, never one wide zoom over everything.
- Hold = the SAME keyframe repeated at the hold's start and end. THERE IS NO IMPLICIT HOLD: between two keyframes that disagree, the camera is IN MOTION the whole gap — to stay on a target, repeat its keyframe just before the next beat. The tool's DRIFT CHECK names any gap that glides — fix it, don't ship it.
- Travels between subjects are FAST (0.4-0.8s); end wide (strength 0) exactly at a scene boundary, never mid-shot.

SCREEN-RECORDING CHOREOGRAPHY — what makes a travelling zoom over a UI read as directed:
- ARRIVE WITH THE APPEARANCE. Moving to something that APPEARS (a message pops in, a panel opens): park the camera at its position BEFORE it exists — glide during the tail of the previous shot and land exactly at the cut, so the thing pops into an already-composed frame. Holding where it pops and THEN travelling shows the reveal twice.
- RE-AIM AT CUTS, NOT MID-SHOT. Need a different centre for the next subject? Two keyframes ≤0.1s apart exactly ON the scene cut — the content changes there anyway. Never chase a subject that moved ACROSS a cut with a visible glide; hold one viewport through the cut and let the cut do the move.
- EXCLUSIONS PICK THE STRENGTH. "Don't show the player / the top part" is a viewport constraint: read the boundary off the grid; the viewport must be at most that wide/tall (strength >= 1/size - 1). Compute it — don't guess and check.
- NEW SCENES DROPPED INSIDE YOUR MOVE PLAY WIDE. When new content lands mid-path, the remap re-anchors keyframes to their own scene and plays the new scene wide — aim it deliberately if it deserves a shot.

A WIDE UI STRIP IS A CROP, NOT A ZOOM. "Show the full timeline, nothing else" is geometrically impossible for a zoom — a 16:9 viewport wide enough for a 2.6:1 strip must include what sits above it. set_insert_window(id, crop=[x0,y0,x1,y1]) makes a spliced scene show ONLY that region, letterboxed; keep the zoom wide across it. Split the insert first when only a stretch should be the detail shot. Read the region's bounds off a look_at_asset grid of THAT clip.
