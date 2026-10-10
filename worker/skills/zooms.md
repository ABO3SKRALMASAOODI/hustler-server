# zooms — the optional eased camera: when a move earns its place, punch-ins on emphasis, framing changes on jarring jump cuts, landings on turns, slow pushes, beat pulses, aiming, travelling paths, crops vs zooms

## Editorial decision principles

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

A steady, well-framed picture is the default and needs no justification;
a camera move needs a clear editorial reason — the punchline word, the
number the story turns on, the cut into a new idea, a thing on screen the
viewer must look at. A punch on every sentence, a push on every hold, a
landing on every cut is exactly what a professional editor does NOT do. There is no quota, no
density target and no "something must move every few seconds" rule for
the camera — the type, the graphics and the cuts carry the rhythm. A short
with no zoom at all is finished when nothing in it called for one.

When a move does earn its place, it is done properly:

- It has a named event (word, cut, beat, reveal, UI action) and a measured
  target, aimed precisely at the face or the thing being discussed.
- It is eased (never linear or stepped) and varied: never the same punch
  three times in a row, never on a metronome.
- Fewer, stronger moves beat many weak ones: one well-placed punch on the
  payoff reads as intent; five small ones read as noise.

## Evidence to inspect

Inspect unzoomed target frames (`look_at` on source times or
`look_at(output_times=...)` on the program — both work without a render),
the tenths grid, every shot boundary and jump cut in the program map, word
onsets of emphasis words (`get_words`, `suggest_emphasis`), beat times when
music leads (`get_audio_analysis`), subject/face/UI geometry, and the full
rendered window.

## Strong treatment patterns

CAMERA GRAMMAR FOR REELS — read the `add_zoom` schema for exact fields and
mode names; the modes below are the vocabulary.
STRENGTH IS MAGNIFICATION − 1: strength 0.12 is a 1.12x frame, 0.2 is 1.2x;
above 1.0 is a 2x+ zoom. Never pass a magnification (1.15) as strength.
RESOLUTION CAPS IT: framing x zoom (overlapping zooms add up) never enlarges
the source past 3x. A 9:16 crop of 1080p is already 1.78x (room ~0.68); a
480p crop is 4x, so its zooms drop to the 5% minimum — the write says
"capped". On low-resolution footage, motion comes from cuts and type, not
punches.
- `punch` — a fast expo snap in (~0.12 s, optional overshoot 0.05–0.15 on
  the biggest beats), held, then a hard cut back out at `end`: strength
  0.08–0.18 landing ON an emphasis word. Start 0–2 frames before the onset
  and end on the next cut or sentence turn so the step back reads as a
  second camera. Use it on meaning words, numbers, contrasts and punchlines.
- `landing` — starts pushed in (strength 0.12–0.18) and settles to the wide
  in ~0.35 s. Start it EXACTLY on the cut (end about start + 0.4). Reserve it
  for cuts between ideas or sections, B-roll returns and the hook — not for
  every jump cut, where a stream of landings makes the camera bounce.
- `ease` — a smooth ramp in, hold and ramp out: a gentle reframe onto a
  subject mid-shot. Its ramps never straddle a cut: see EDGES ON CUTS.
- `push_in` — a slow continuous push, strength 0.05–0.12, across a hold of
  3 s or more that builds toward something (a confession, a reveal) — not
  on every long statement; a steady hold on a good frame is fine.
  `pull_out` is the release or reveal.
- `pulse` — 1 → 1 + strength → 1 in ~0.3 s on a musical beat or a rhythmic
  list beat, strength 0.05–0.08.
- `shake` — a decaying impact shake (the `shake` amount, 0.3–0.8, does the
  work); a `shake` value on a punch turns it into an impact hit. Only on
  real impacts: a hero slam, a hit.
- JUMP CUTS: after `cut_silences` a take is full of jump cuts every 1–2 s,
  and a bare jump cut is FINE — it is the accepted grammar of talking-head
  short-form. Act on one only where it is genuinely jarring (the head
  visibly jumps right on a key line). The options: leave it, cover it with
  B-roll or a cutaway, or change the framing on that cut with ALTERNATING
  FRAMING — a punch (hard step, `ramp_s=0` when the schema offers it) from
  that cut to the next at strength 0.12–0.2, back to the wide at the
  following cut, aimed at the face — so it reads as a second camera. A
  framing change is a step of at least ~10% across the cut (0.05 punches,
  the judged 7.4% card steps and two zooms of nearly equal strength read as
  the same frame stuttering — remove those, or raise one to ≥12% only where
  the cut genuinely pops) or a crop move. Never cover every jump cut: a
  camera that moves on every cut looks childish.
- THE JUMP-CUT REPORT: `conceal_jump_cuts(mode='report')` writes nothing and
  lists every same-shot jump cut — how visible the jump is (the head's
  travel in face widths, the picture change against the speaker's own
  motion), what already covers it (a graphic entering or leaving on the cut
  frame, a layout change, a ≥10% step, a crop re-aim; a caption block
  changing there only softens it), the flags (a cut inside the hook's first
  1.5 s; a step under ~10%, which stutters) and the options, most
  restrained first: leave it; move a graphic change that sits within 0.4 s
  onto the cut (one event instead of two); restore a short removed pause
  (one continuous take) or re-cut the join on a still head; and — optional
  and rare — a hard ≥12% step on that one cut. The render's taste notes
  carry the flagged, near-miss and visibly jumping cuts with those options,
  and an EDL write that puts a jump cut in the hook or writes a sub-10% step
  says so — never an order, and never a zoom on every cut.
- CUT HYGIENE IS NOT A ZOOM: when the render shows same-angle jump cuts
  visibly POPPING (pause removal on one locked camera — the head and hands
  jump inside a constant frame, typically a static archival or single-camera
  talk), `conceal_jump_cuts()` is the optional tool: it measures every bare
  same-angle cut (a head jump, or a picture change well above the speaker's
  own motion) and writes HARD, non-animated framing steps of 10-15% (default
  12%) on just the ones that pop, each held to the next cut, where it steps
  back — so popping cuts alternate like a second camera. Full frame gets a
  `cut_step` zoom (ramp 0, aimed at the face); a source picture card
  alternates its own SOURCE crop instead (stepping WIDE when the source is
  near its upscale cap, so a 480p card never softens). `at=[...]` names cuts
  you saw pop; `mode='off'` removes every step. It is never a default, no
  look or planner writes it, its steps are not counted as camera moves, and
  it is not a reason to add expressive zooms; run it after the cut is final
  and re-run it after re-cutting.
- EDGES ON CUTS: a zoom start or end within 4 frames of a cut (a jump, a
  camera change, an insert edge, a crop re-aim, or the programme's last
  frame) is moved ONTO the cut and holds through it: an `ease` ending on a
  cut stays pushed in to the cut's last frame instead of releasing over it,
  and a `punch` or `ease` starting on a cut is already in on the cut's
  first frame — unless the frame before the cut is already pushed in about
  as far (a punch ending there), when it ramps up from the wide so the cut
  still steps. Put edges on cuts and let the cut change the framing; to
  release BEFORE a cut on purpose, end the zoom at least 0.2 s earlier.
- On a hero moment the graphic leads; a punch or pulse on the same frame
  as the word slam is optional support for the biggest beat only (and its
  sound, when that landing earns one) — never a reflex on every graphic,
  never a camera move per hero moment. A hook may open punched-in or with
  a landing at 0 s when that opening earns it; the hook title does the
  interrupting either way.
- Density is a CEILING, never a target: no more than one camera event per
  ~1.5 s unless it is a designed hit, and in practice far fewer — a handful
  of earned moves in a 30–45 s talking-head short, or none. Rest on an
  admission; never add a move to fill a quiet stretch.

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

PURPOSE AND EVIDENCE: pass `purpose` naming the event ("landing on the turn
at 6.4 s", "punch on 'forty' at 12.1 s"). If a move has no correlated
word, cut, beat or visible action, it does not belong; a random move weakens
the intentional ones.

`punch_in_on_emphasis` writes a measured pass of punches on vocally stressed
words that survive the cut, using face targets when detected: each snaps in
on its word, holds to the next cut or sentence end, then cuts back out.
Omitted count and strength are directed from program length; explicit
values win. It is an optional starting point, never a required pass: when
you use it, keep only the punches on words that truly carry the story (pass
a small count) and remove the rest; skip it for calm or minimal briefs and
whenever the type and cuts already carry the moment.

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
- Strength passed as a magnification (1.15 instead of 0.15), producing a
  2x+ zoom.
- A landing on every jump cut, so the camera bounces every second; a punch
  on every sentence or a push on every hold — zooms as filler, the
  "childish" look the owner rejects.
- Visible drift during a travelling-zoom hold.

## Verification procedure

Check aim and framing first without a render: `look_at(output_times=[...])`
shows the program in true geometry with every zoom applied. Then judge the
motion itself on a complete preview — `render_preview(complete=true)`, then
`look_at(rendered=true, output_times=[...])` at start, mid-ease, landing and
release (rendered looks need a complete preview of the current version): the
face or target stays composed and centred where intended, the ease reads
smooth, the landing sits on the word, cut or beat, and adjacent moves vary
in size and mode. Check every shot boundary and path extreme.

## Repair ladder

Remove a move that has no clear event (the first fix, not the last) →
re-aim from an unzoomed frame → retime to the onset or cut → change mode
(punch ↔ ease ↔ landing) or vary strength → thin moves closer than ~1.5 s →
add a shot-specific path → split at the boundary → convert to a crop where
appropriate → verify again.
