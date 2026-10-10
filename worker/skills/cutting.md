# cutting — silences, fillers, keep-list edits, word-safe boundaries, pacing

## Editorial decision principles

Cut for meaning, performance and useful rhythm. Preserve setup/turn/payoff and never confuse “shorter” with “better.”

## Evidence to inspect

Inspect word boundaries, complete sentences, pauses, reactions, shot changes, source audio continuity, the program map and the requested delivery length.

## Strong treatment patterns

THE KEEP LIST defines what SURVIVES, in source-video seconds; everything outside it is cut.
- Local fixes: cut_range(start, end) removes one range; restore_range(start, end) brings one back. The rest of the edit is untouched.
- Output-time cuts: when the user's times are OUTPUT seconds ("cut 12-15 of the video", "cut that part of the third scene") use cut_output_range(start, end). It cuts whatever plays there: kept footage in source time, and a spliced insert is SPLIT around the span (or removed when swallowed). Cutting inside an insert IS possible — set_insert_window is not a substitute (it changes which part of the clip plays; it cannot remove a middle).
- keep_segments REPLACES the whole list. Use it only for wholesale restructuring, and ALWAYS call get_edl first so you rebuild from the real current state. If its result warns you re-included previously cut material, treat that as a probable mistake and fix it.

SILENCES AND FILLERS
- For "cut the silences" / "tighten this up": use cut_silences (one call — cuts every pause over threshold, pads speech, snaps to word edges), then get_kept_transcript to verify. Cut pauses longer than ~0.7s between sentences but PRESERVE pauses that carry meaning — after a question, a dramatic beat, an emotional moment. When unsure whether a pause matters, look_at that moment.
- "Remove the ums": remove_filler_words cuts every um/uh/er/hmm at exact indexed timestamps in one call and NEVER needs a prior `get_words` call. A broad polished/professional short-form speech brief also authorizes this load-bearing cleanup when the index reports timed fillers; include it in the first atomic recipe. Skip it for natural/raw/uncut delivery or when the hesitation carries meaning. Filler sounds are in the transcript with timestamps; they are never burned into captions, so removing them changes the AUDIO, not subtitles.
- When a speaker repeats or restarts a sentence, the LATER take is normally their correction — keep the LAST take, cut the earlier ones, unless told otherwise. When two takes both survive scrutiny, compare stressed-word scores and sentence boundaries, then keep the one with stronger measured energy and the cleaner ending.
- BREATHS ARE NOT FILLERS. A breath before a big line is part of the delivery — cut_silences pads for this; when hand-cutting, leave ~0.15s before speech resumes or the voice clips in mid-inhale (word timing and the render's AUDIO CHECK help catch it).
- TRIM FOR RHYTHM, NOT FOR LENGTH (short-form): remove low-information connectors ("it was like a lot of things and", "I think that's kind of a tell that", a stray "you know") so no stretch runs over ~3 s without a new idea, but keep 150–250 ms at every sentence boundary (more before the payoff line) so sentences never collide, never drop an article or function word inside a clause ("has used a weird type", not "has used weird type"), and never start a keep on a word fragment. Archival or iconic speakers keep the pauses their timing depends on (about 250 ms or more inside a phrase). The render's EARN ITS PLACE advisory names colliding sentences and dropped clause words by time.

WORD-SAFE BOUNDARIES
- AUDIO-SAFE BY DEFAULT: every keep write (keep_segments, cut_range, restore_range, cut_silences, remove_filler_words, cut_output_range) places each cut edge it writes on the source's sound — out of any word it falls in, onto the quietest point within ~80 ms of the time you wrote (a word's trailing 's' or a breath's lead-in up to 0.25 s), never into the word on the removed side — and reports every move as AUDIO-SAFE CUTS. The local tools place the edges they make (the others stay where they were); keep_segments places every edge of its list — rewriting get_edl's keep through it re-places an older edit's cuts. Read the report: "joins running speech" means no pause exists near that cut — keep it only if it sounds clean, else cut at a breath or a sentence end. snap_to_words:true first moves edges outward to word edges with a breath; snap_to_words:false writes your exact times (a deliberate stutter cut).
- NEVER cut mid-word. When cutting inside a sentence, get_words on that region tells you where the words are; the audio-safe placement does the last 80 ms. If a write result still WARNS that a boundary lands inside a word, fix it before rendering (snap to the offered candidates).
- Prefer fewer, cleaner edits over micro-cuts; merge adjacent cuts when the kept sliver between them is under ~0.3s.
- JOINS ARE CLEAN BY CONSTRUCTION: every cut between kept spans renders as a 12 ms equal-power crossfade centred on the cut (no click from two waveforms butted together, no change in length or sync), and every cut lands on the exact frame its program time names, the frame zooms, captions and graphics timed to it switch on. A click that survives is a keep edge inside a word or sibilant — re-cut it at a word edge.
- SHOT CUTS: an edge a few frames past a source camera cut flashes the other shot. Every keep write moves an end on or within 6 frames past an indexed cut to just before it (a start to the first frame after it) and reports it as SHOT-CUT HYGIENE; if that trims a word, re-cut at a cleaner point rather than restoring the flash.

VERIFY WHAT SURVIVED
- After ANY pass that cuts repetitions or tightens the video, call get_kept_transcript before rendering — it shows exactly what the viewer will hear and flags phrases that still repeat. Never tell the user repetitions are gone without it. If a render result contains a REPETITION AUDIT, address it or tell the user what still repeats.

PACE FIRST, THEN DESIGN
- Cut the dead air first and judge the story before designing on top of it. A tighter 40s beats a padded 90s. If the material only supports 25 good seconds, deliver 25 good seconds and say why. Design (captions, graphics, camera, sound) is then built on the tightened program, never used to disguise slack.
- JUMP CUTS ON A REEL: a tightened talking-head take is full of jump cuts. Keep them hard — a bare jump cut is fine and is the default. Where one is genuinely jarring (the head visibly jumps on a key line), leave it, cover it with B-roll, or change the framing on THAT cut with alternating framing — tight from it to the next cut, wide at the following one, aimed at the face (read zooms; zooms are optional and never go on every cut) — keeping `landing` zooms for real cuts between ideas; never a full-screen transition on a jump cut. A framing change is a step of at least ~10% (or a crop move) across the cut — under that it stutters. `conceal_jump_cuts(mode='report')` lists every jump cut with how visible it is, what covers it, the hook/stutter flags and the options (leave it, move a graphic change onto it, restore the removed pause or re-cut the join, or a step) and writes nothing; the render's taste notes name only the flagged, near-miss and visibly jumping cuts — never a rule. Keep the hook's first 1.5 s free of jump cuts where the take allows (restore the pause, or start on the later take). When cuts visibly pop on a locked single camera (a static or archival talk tightened by pause removal), `conceal_jump_cuts()` is the optional cut-hygiene pass: hard alternating 10-15% framing steps on only the cuts that measurably pop (full frame or inside a picture card), removable with `mode='off'` — never a default and never on every cut.
- When a silence pass removes more than HALF the runtime, deliver it but LEAD your reply with the numbers ("5:12 → 1:53") and offer the gentler pass — the same cut with the numbers up front is a professional decision the user gets to keep or undo.
- THE FIRST SECOND IS THE WHOLE EDIT (short-form): open on the strongest frame or sentence — no fade from black, no logo, no dead air — on a clean word onset with the speaker facing camera and eyes open (the PICTURE CHECK names HOOK OPENS ON CLOSED EYES or MID-SOUND with the nearest clean start; it never moves your cut — you decide), never the tail of the previous word or a disfluency, and with no jump cut inside the first 1.5 s (at most one in the first 3 s). The hook text (a title or word slam on the first strong word) is the pattern interrupt; a punched-in opening is an option when it earns its place, not a requirement. If the best line is 40s in, MOVE it to the front (keep_segments) or cut into it. Finding the hook, holding the middle and ending the loop is a craft of its own — read_skill hooks-retention whenever the goal is views.
- END ON PURPOSE. Short-form loops: land on the last word or beat, no fade to black, no dead tail after the music stops — and give the punchline 0.8–1.5 s of air before the end card (a reaction button 1.0–1.5 s, or none): the speaker's natural tail or a reaction first (restore_range into the pause the EARN ITS PLACE note names); when the source runs straight on into the next line, hold the frame — add_freeze_frame(at_output_s=<programme end>, audio_mode='hold', duration_s≈1.0 minus the air you have): the composed last frame (crop, card, grade) over the source's own room tone, the last words fading into it, any payoff graphic held over it (darken dims it under the graphic). Never cut to the end card less than 0.8 s after the last payoff word. Long-form and cinematic pieces earn a fade.

## Common failure modes

- Mid-word cuts, missing setup/payoff, duplicate source spans, breathless pacing or dead tails.
- Sentences colliding across a cut with no breath; an article cut out of a clause; a hook that opens on a word fragment or stutters through jump cuts; a punchline cut 0.5 s after its last word.
- Cutting reactions/performance merely to hit a duration.

## Verification procedure

Audit word boundaries and program order, then screen the opening, every story turn/junction, pacing holds and ending with sound.

## Repair ladder

Snap boundaries (audio-safe placement, the AUDIO-SAFE CUTS report) → restore needed context/reaction (or a payoff hold) → drop or trim whole beats (kept footage plays in source order) → relax or compress pacing → rebuild the arc → verify the full preview.
