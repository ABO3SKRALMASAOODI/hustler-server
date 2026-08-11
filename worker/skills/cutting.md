# cutting — silences, fillers, keep-list edits, word-safe boundaries, pacing

THE KEEP LIST defines what SURVIVES, in source-video seconds; everything outside it is cut.
- Local fixes: cut_range(start, end) removes one range; restore_range(start, end) brings one back. The rest of the edit is untouched.
- Output-time cuts: when the user's times are OUTPUT seconds ("cut 12-15 of the video", "cut that part of the third scene") use cut_output_range(start, end). It cuts whatever plays there: kept footage in source time, and a spliced insert is SPLIT around the span (or removed when swallowed). Cutting inside an insert IS possible — set_insert_window is not a substitute (it changes which part of the clip plays; it cannot remove a middle).
- keep_segments REPLACES the whole list. Use it only for wholesale restructuring, and ALWAYS call get_edl first so you rebuild from the real current state. If its result warns you re-included previously cut material, treat that as a probable mistake and fix it.

SILENCES AND FILLERS
- For "cut the silences" / "tighten this up": use cut_silences (one call — cuts every pause over threshold, pads speech, snaps to word edges), then get_kept_transcript to verify. Cut pauses longer than ~0.7s between sentences but PRESERVE pauses that carry meaning — after a question, a dramatic beat, an emotional moment. When unsure whether a pause matters, look_at that moment.
- "Remove the ums": remove_filler_words cuts every um/uh/er/hmm at exact word timestamps in one call. A broad polished/professional short-form speech brief also authorizes this load-bearing cleanup when the index reports timed fillers; include it in the first atomic recipe. Skip it for natural/raw/uncut delivery or when the hesitation carries meaning. Filler sounds are in the transcript with timestamps; they are never burned into captions, so removing them changes the AUDIO, not subtitles.
- When a speaker repeats or restarts a sentence, the LATER take is normally their correction — keep the LAST take, cut the earlier ones, unless told otherwise. When two takes both survive scrutiny, judge DELIVERY, not words: listen_to both (or compare stressed-word scores) and keep the one with more energy and cleaner ending.
- BREATHS ARE NOT FILLERS. A breath before a big line is part of the delivery — cut_silences pads for this; when hand-cutting, leave ~0.15s before speech resumes or the voice clips in mid-inhale (the render's AUDIO CHECK and your ears catch it).

WORD-SAFE BOUNDARIES
- NEVER cut mid-word. When cutting inside a sentence, first call get_words on that region and place every boundary on a word edge or silence midpoint. Passing snap_to_words:true to a keep write guarantees clean boundaries.
- If a write result WARNS that a boundary lands inside a word, fix it before rendering (snap to the offered candidates).
- Prefer fewer, cleaner edits over micro-cuts; merge adjacent cuts when the kept sliver between them is under ~0.3s.

VERIFY WHAT SURVIVED
- After ANY pass that cuts repetitions or tightens the video, call get_kept_transcript before rendering — it shows exactly what the viewer will hear and flags phrases that still repeat. Never tell the user repetitions are gone without it. If a render result contains a REPETITION AUDIT, address it or tell the user what still repeats.

PACE IS EDITING, EVERYTHING ELSE IS DECORATION
- Cut the dead air first and judge the result before adding anything. A tighter 40s beats a padded 90s. If the material only supports 25 good seconds, deliver 25 good seconds and say why.
- When a silence pass removes more than HALF the runtime, deliver it but LEAD your reply with the numbers ("5:12 → 1:53") and offer the gentler pass — the same cut with the numbers up front is a professional decision the user gets to keep or undo.
- THE FIRST SECOND IS THE WHOLE EDIT (short-form): open on the strongest frame or sentence — no fade from black, no logo, no dead air, no zoom that lands before the shot is read. If the best line is 40s in, MOVE it to the front (keep_segments) or cut into it. Finding the hook, holding the middle and ending the loop is a craft of its own — read_skill hooks-retention whenever the goal is views.
- END ON PURPOSE. Short-form loops: land on the last word or beat, no fade to black, no dead tail after the music stops. Long-form and cinematic pieces earn a fade.
