# first-requests — START HERE on a new request: the six things people ask first (a website or product demo, clean up my footage, a vertical short with captions, highlights, gaming, shorts from a long talk), each with its default chain, its done bar and the one self-review

## Editorial decision principles

Name the request first, then run its chain below with the defaults it
lists. A first request is judged on ONE finished, watchable result, not on
a plan or a questionnaire: choose sensible defaults, make it, look at it,
fix the weakest moment, and say in the reply what you chose. Ask only when
the request cannot start (no footage and no URL, or two contradictory
asks).

Defaults whenever the user did not say otherwise:

- 9:16 vertical for social; the source's own shape for a long-form or a
  demo the user wants for a website.
- Captions whenever there is speech, every heard word on screen exactly
  once, quoting the transcript (read captions for the faithful-text rules).
- Dead air and fillers trimmed on speech; never a cut through a word.
- One committed look (`apply_look`), restrained and readable.
- No music unless the user asks or supplies it; never pick a track.
- ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
  restraint is the default. Reach for a zoom or a sound only when a
  specific moment needs it — a key word, a reveal, a genuinely jarring
  jump cut, a real-world action shown — and zero is a fine answer. Never a
  zoom per cut, a camera move per hero moment or a sound per landing or
  transition: used where nothing calls for them they make an edit look
  childish.
- The corner mark and the native end card are never touched.

Every request ends the same way: render once, look at the rendered frames,
fix the single weakest moment, reply in two or three sentences.

## Evidence to inspect

- The request type and its words: a URL with "demo", "walkthrough", "show
  my site" or "launch video" is a website demo; "clean", "remove the ums",
  "fix the audio" is clean-up; "short", "reel", "TikTok", "vertical" is a
  vertical short; "highlights", "best moments", "recap" is highlights;
  gameplay footage (a HUD, a kill feed, a scoreboard) is gaming; a source
  over about three minutes of conversation asked for shorts is a long talk.
- `get_video_info` (length, shape, speech, a call window or screen) and one
  batched `look_at` of 3-4 source times: where the face is, what must stay
  out of frame, whether the picture can fill 9:16 at 2x upscale or less.
- For speech, `get_transcript` (or `get_words` for exact boundaries).

## Strong treatment patterns

WEBSITE OR PRODUCT DEMO (the most common first request):
1. `record_website_demo` with 4-8 steps that tell the site's story: the
   hero section, the ONE feature that matters, the result, the call to
   action. At least two clicks or hovers on visible buttons, tabs or links
   named by their visible text; `seconds` on a step that shows a result the
   viewer must read. Portrait for social, landscape for a website.
2. `showcase_demo` on the recording so the frame pushes in on each click.
3. A hook title by 1.5 s that names what the product does (not "Check out
   my site"), one callout on the control that matters, the result as a
   card. 15-25 s.
4. It records the PUBLIC site and never types into password or payment
   fields: say which steps did not work, never describe a step that failed
   as done. Read screen-takeover for the full grammar.

CLEAN UP MY FOOTAGE:
1. `cut_silences`, then `remove_filler_words` (speech only); keep every
   qualifier and every sentence the user would miss.
2. `enhance_video` only when the picture or the voice is visibly or
   audibly poor; `set_master_loudness` leaves the voice even. A request for
   louder or quieter audio is honoured as asked.
3. Captions when asked or when the result is for social. Keep the user's
   shape unless they asked for vertical.
4. Done: no gap over ~0.4 s inside a sentence, no clipped first or last
   word (`check_speech` on the joins), the length the user asked for.

A VERTICAL SHORT WITH CAPTIONS:
1. Tighten inside the take (`keep_segments`, `cut_silences`) to 15-45 s.
2. `auto_reframe` to 9:16 with the face whole; when the face crop needs
   more than 2x, a card on a designed backdrop (read reframe-aspect).
3. `apply_look` (a premium system), then `add_captions` with the
   meaning-bearing words as `emphasis_words`.
4. ONE hook by 1.5 s written from the clip's strongest line, and 2-4
   designed beats on exact word cues that add what the captions cannot (a
   number, a contrast, a name). Read hooks-retention and motion-design.
5. The payoff holds about 0.6-1.2 s of live footage after the last word,
   never a freeze or a slowed tail (read cutting, END ON PURPOSE).

HIGHLIGHTS / BEST MOMENTS:
1. Speech: walk the whole transcript and pick moments with a hook and a
   payoff (read story-selection). Visual events (a goal, a reaction, a
   product reveal): `find_visual_moments` with one concrete visible event,
   then ONE batched `look_at` to confirm candidates.
2. `keep_segments` in source order; each moment starts on a clean first
   word or the action's start and ends on its resolution. No moment is cut
   mid-action or mid-sentence.
3. One look and captions across all of them; a section title only when the
   moments change subject.

GAMING:
1. Find the plays (`find_visual_moments`: a kill, a save, a clutch, a
   fail) and keep 2-4 s of build-up before each.
2. 9:16 with the action whole: a card or a split (the facecam above the
   gameplay) when the HUD or the action would be cropped; never crop the
   score or the kill feed the moment depends on.
3. Captions for commentary; a hero word or a counter only on the moment
   that earns it; speed or slow motion only on the decisive frame. Restraint
   applies: the play is the show.

SHORTS FROM A LONG TALK (podcast, interview, keynote):
1. Read story-selection, then make every moment that clears its bar with
   `make_shorts` in one call.
2. Each short: the vertical-short chain above, with the speaker named in
   the hook's kicker or a headline band within ~3 s.

## Common failure modes

- Asking questions instead of delivering a first result.
- A demo that tours every page (it shows nothing) or scrolls only (nothing
  to push in on), or claims a step that failed.
- Captions that drop, double or invent words; a hook that is a generic
  question; a graphic that re-types the words being heard.
- A highlight cut mid-sentence or mid-play; a short ending on a freeze.
- Unearned zooms or sounds; music the user never asked for.
- Rendering after every operation instead of once after the build.

## Verification procedure

1. `render_preview(quality='approval')` once the build is complete; read
   its PICTURE CHECK, CAPTION CHECK and VERIFICATION ADVISORIES.
2. One `look_at(rendered=true, output_times=[...])` with the first frame,
   1.0 s, every hero moment and the payoff.
3. Speech: `audit_captions` (fix every heard-but-unshown word and every
   NEGATION HIDDEN line) and `check_speech` across the joins.
4. Read review for the full screening pass when the result is a premium
   short.

## Repair ladder

Fix a real defect first (a clipped word, a caption error, a face cut by
the frame, a graphic on the face) → then the single weakest designed
moment → re-render only when pixels or sound that matter changed → when a
tool cannot do what was asked, say so plainly and deliver the closest
honest result.
