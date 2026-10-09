# zooms — the eased camera: punch-ins on emphasis, landing zooms after cuts, slow pushes, beat pulses, aiming, travelling paths, crops vs zooms

## Editorial decision principles

On short-form, the digital camera is a constant, eased presence: premium
reels punch in on emphasis words, land every jump cut with a settling zoom,
push slowly through long holds and pulse with the music, all aimed precisely
at the face or the thing being discussed. Every move is bound to a word, a
cut or a beat and carries a purpose; what makes camera motion cheap is bad
aim, linear or stepped motion and metronomic spacing, not the number of
moves.

- Every zoom has a named event (word, cut, beat, reveal, UI action) and a
  measured target.
- Vary strength, mode and spacing with the speech; never the same punch three
  times in a row.
- A steady frame is a deliberate choice for a specific passage (a vulnerable
  admission, a reaction that must be read), not the default for a reel.

## Evidence to inspect

Inspect unzoomed target frames (`look_at` on source times or
`look_at(output_times=...)` on the program — both work without a render),
the tenths grid, every shot boundary and jump cut in the program map, word
onsets of emphasis words (`get_words`, `suggest_emphasis`), beat times when
music leads (`get_audio_analysis`), subject/face/UI geometry, and the full
rendered window.

## Strong treatment patterns

CAMERA GRAMMAR FOR REELS — read the `add_zoom` schema for exact fields and
mode names; the modes below are the vocabulary:
- `punch` — a fast expo-out push (optionally with a small overshoot) landing
  ON an emphasis word: 1.08–1.18x over ~0.12–0.25 s, held through the
  phrase. Start 0–2 frames before the onset. Use it on meaning words,
  numbers, contrasts and punchlines.
- `landing` — just after a jump cut, start 1.12–1.18x and ease to 1.0 over
  ~0.3–0.6 s. It hides the jump and gives every cut an arrival. This is the
  default treatment for jump cuts inside a continuous take.
- `ease` — a smooth eased reframe between two framings (medium → tight on a
  sentence turn), the alternative-framing move for podcast reels.
- `push` (or `push_in`) — a slow continuous push, about 1.00 → 1.06, over
  holds longer than ~3 s, so long statements never sit frozen.
- `pulse` — 1.0 → 1.07 → 1.0 on a musical beat or a rhythmic list beat.
- shake — only on impacts (a hero slam, a hit), short and decaying.
- On a hero moment the camera supports the graphic leader: a punch or pulse
  on the same frame as the word slam and its low hit. A deliberate hook
  may open already punched-in or with a landing zoom at 0 s.
- Density follows the speech: in a talking-head reel expect a camera event
  every 2–4 s (landings on cuts, punches on emphasis, pushes on holds),
  varied in size; cluster on dense ideas, rest on an admission.

AIMING — a coordinate is a MEASUREMENT, never an impression:
- Every frame you look at carries a faint tenths grid ((0,0) = top-left,
  labels .2/.4/.6/.8). Read aim points, rects and positions off it.
- On a talking head, aim at the face: cx/cy on the eyes-to-mouth centre so
  the face stays composed, or a rect around head and shoulders.
- To zoom INTO a thing (a message, a button, a panel): read its box off the
  grid and pass rect=[x0,y0,x1,y1]. cx/cy pin a POINT in place and cannot
  bring an edge subject to centre — rect framing is the reliable way.
- Frame the THING, not its container. A chat message is its bubble PLUS the
  avatar/label beside it — extend the rect so the avatar is not clipped.
- Aim coordinates come only from UNZOOMED frames: a tile labeled as zoomed
  shows magnified screen coordinates, not positions you can aim at.
- To RETIME an existing zoom, KEEP ITS AIM — copy rect (or cx/cy) from
  `get_edl` and change only start/end.
- After reframes, re-check aim per shot; a face that moves between shots
  needs a target per shot.

PURPOSE AND EVIDENCE: pass `purpose` naming the event ("landing after jump
cut at 6.4 s", "punch on 'forty' at 12.1 s"). If a move has no correlated
word, cut, beat or visible action, it does not belong; a random move weakens
the intentional ones.

`punch_in_on_emphasis` writes a measured pass of punches on vocally stressed
words that survive the cut, using face targets when detected. On a premium
reel it is a valid starting pass; inspect its choices, then retime, re-aim
or vary them by hand so the strongest words get the strongest moves.

TRAVELLING ZOOMS (`add_zoom_path`) — when the zoom must MOVE ("keep it, then
move to my prompt, then the answer"): ONE path visiting each subject as a
rect keyframe — never a chain of static zooms, never one wide zoom over
everything.
- Hold = the SAME keyframe repeated at the hold's start and end. There is no
  implicit hold: between two keyframes that disagree the camera is in motion
  the whole gap. The tool's DRIFT CHECK names any gap that glides.
- Travels between subjects are fast (0.4–0.8 s); end wide (strength 0)
  exactly at a scene boundary, never mid-shot.

SCREEN-RECORDING CHOREOGRAPHY:
- ARRIVE WITH THE APPEARANCE: park the camera where something will appear
  before it exists, landing exactly at the cut.
- RE-AIM AT CUTS, NOT MID-SHOT: two keyframes ≤0.1 s apart exactly on the
  scene cut.
- EXCLUSIONS PICK THE STRENGTH: "don't show the top part" is a viewport
  constraint; compute strength ≥ 1/size − 1 from the grid.
- New scenes dropped inside a move play wide; aim them deliberately.

A WIDE UI STRIP IS A CROP, NOT A ZOOM. A 16:9 viewport wide enough for a
2.6:1 strip must include what sits above it. `set_insert_window(id,
crop=[x0,y0,x1,y1])` shows only that region; keep the zoom wide across it.

## Common failure modes

- Unaimed zooms that crop foreheads or push into an empty wall.
- Stepped or linear motion; the identical punch at identical intervals.
- A zoom crossing a shot boundary without a path; stale targets after a cut.
- A camera bump that fights a graphic leader instead of supporting it.
- Jump cuts left as raw jumps on a reel instead of landing zooms.
- Visible drift during a travelling-zoom hold.

## Verification procedure

Render and inspect each move with dense rendered frames
(`look_at(rendered=true, output_times=[...])` at start, mid-ease, landing and
release): the face or target stays composed and centred where intended, the
ease reads smooth, the landing sits on the word, cut or beat, and adjacent
moves vary in size and mode. Check every shot boundary and path extreme.

## Repair ladder

Re-aim from an unzoomed frame → retime to the onset or cut → change mode
(punch ↔ ease ↔ landing) or vary strength → add a shot-specific path → split
at the boundary → convert to a crop where appropriate → remove a move that
has no event → verify again.
