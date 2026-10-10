# short-form-direction — directing one story into a premium reel: committed look, hook, hero moments, motion, sound, review

## Editorial decision principles

A premium reel is one coherent experience from promise to payoff, carried by
committed art direction. The owner's reference reels — the bar Valmera must
meet and beat — share a measurable grammar: a pattern interrupt in the first
half-second, the hook line readable as text by 1.5 s, something visual
changing every 0.3–0.6 s, 2–4 designed hero moments on exact spoken words,
motion typography instead of static subtitles, one committed grade with
grain, and a frame with no dead black. A podcast clip with plain captions on
a small card over black is a clip page, not a premium reel.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.
The type, the graphics and the cuts carry the rhythm; a steady, well-framed
picture is the camera's default and a clean voice is the mix's.

- Story first: the reel still needs setup → development/turn → payoff, and
  every design decision serves that spine.
- Commit to ONE look per edit and keep it from frame one to the CTA.
- Designed TYPE is the expected state for vertical reels of 120 s or less
  (including podcast shorts children): motion captions, a hook line, hero
  moments on their words. A bare clip page is not the bar. That density
  comes from type and graphics — not from zooms or sounds added to fill time.
- Every device is bound to a word, a beat or a cut, and carries a purpose.
  Purposeful is good; random is not — and a zoom or a sound with no clear
  reason is random.
- Never invent facts: numbers, quotes, identities, brand messages and
  metrics come from the transcript, the user or a verified source.

## Evidence to inspect

Watch and hear this exact story: the kept transcript with program times
(`get_kept_transcript`), word onsets for hero words (`get_words`), measured
stress and numbers (`suggest_emphasis`, `get_audio_analysis`), shots and
faces (`get_editorial_map`, `get_shots`), the opening frames and every turn
(`look_at`), the reference asset when present (`look_at_asset`), the live
motion library (`list_motion_templates`) and the sound library
(`list_sound_library`).

## Strong treatment patterns

YOU ARE THE FRESH EDITOR. For a shorts child, the parent chose a complete
story because it is worth developing. The selection is a strong hypothesis,
not sacred footage: tighten repetition, trim the lead-in so it opens on its
strongest line (kept footage plays in source order), or restore a nearby
beat when that makes the story clearer. Never turn it back into a
contextless quote.

THE PREMIUM SHORT-FORM GRAMMAR — targets you can measure in the render:

1. ONE COMMITTED LOOK. Start with `apply_look` — `editorial` (premium
   default for podcast and interview reels), `creator_punch` (high-energy
   creator), `cinematic_doc` (story and documentary), `mono_noir` (B&W plus
   one accent), `clean_minimal` (quiet product or education) — or build a
   deliberate equivalent: a caption `motion_look`, one grade plus grain and
   a transition vocabulary. Refine components afterwards; do not mix looks.
   A look places no sound unless you pass `transition_sounds=true` (only
   when the user asked for transition sounds); read its receipt or `get_edl`
   before adding junction cues. Never pass a look's music option unless the
   user asked for generic background music. On a vertical reel there is no
   fade-in from black; remove one with `set_fades` if a look adds it.
2. HOOK. A visual pattern interrupt lands within 0.1–0.6 s: a `hook_title`
   or `word_slam` on the first strong word, a card reveal, a flash or light
   leak. A punch-in at 0 s is an option, never a requirement, and at most
   one sound sits under the opening, only when it earns one (a soft whoosh
   into the title, via the graphic's `sfx=true`). The hook line is on screen
   as text by 1.5 s; the speaker is visible and talking by ~0.3 s. Never open on
   black, a logo, dead air or more than ~3 s of a non-speaker setup. The
   hook text poses the question; it never spoils or quotes the payoff, never
   contradicts the words spoken under it, and is complete and large (about
   5% of frame height or more) by 1.5 s — not a slow typewriter line. The
   speaker's face is fully in frame. (Read hooks-retention.)
   NAME THE SPEAKER within ~3 s with a `lower_third` (a verified name plus
   role, venue or year — the year matters on archival footage) and name each
   new on-screen speaker when they first appear. Names come from the title,
   metadata or the user, never a guess.
3. RHYTHM. Something visual changes every 0.3–0.6 s — motion-caption word
   reveals on onsets count — and the structure moves with the speech: a
   graphic, a B-roll cut or a layout shift where the story turns. Vary the
   interval with the speech: cluster on dense ideas, breathe on a real
   admission. One graphic state holds about 3–4 s at most (then it
   restates, shrinks to a corner label or leaves); no stretch of the middle
   third goes more than ~5 s without a word-bound beat. Never fill a quiet
   stretch with a zoom or a sound just to make something move.
4. HERO MOMENTS. Choose 2–4 per reel, each bound to the exact spoken word:
   a hero word slam, a counter on a spoken number, a UI card when the speaker
   describes a message or search, text behind the subject, a callout circle
   or arrow on a visible thing, a chapter title on a real turn. Each hero
   moment has ONE leader graphic; a camera move or a sound on the same
   frame is optional support, only where that landing clearly earns it
   (the payoff, the single biggest number) and within the limits in 6
   and 8. Slam the information-bearing word ("not enough", "40", the
   name), never a cliché ("next level"). A graphic's meaning must match the
   claim: a checklist tick means achieved, so broken promises, myths and
   don'ts take `mark='cross'`; a versus split means opposition; a counter
   lands ON its spoken number. Never a graphic that only recaps what an
   earlier one already showed. (Read motion-design.)
   ART DIRECTION: one design system per short — 2–3 font roles, ONE accent
   colour passed to every graphic's `accent`, one container style, one
   entrance vocabulary; at most about 4 template families in a short, and
   vary them across a batch so shorts from one episode don't look stamped
   from one template. Match the material (no iOS-style widgets on a 1983
   talk; an archival piece takes a period treatment).
5. CAPTIONS. Browser motion captions via `style.motion_look`: `editorial`
   or `clean` as the premium default for podcast and interview speech,
   `lockup` or `serif` for editorial accents, `pop` or `stack` for hype,
   `box` or `glow` for high-contrast karaoke, `mono` for tech. Words reveal on
   their onsets; a size ladder separates connector words from 1–2 accent
   words per sentence; captions mute only under a graphic that carries the
   words being spoken — never over a punchline's setup, which a sound-off
   viewer must read. Kickers and labels quote the transcript, not a
   paraphrase. (Read captions.)
6. CAMERA — OPTIONAL. A steady, well-framed picture is the default; add a
   zoom only where it clearly serves a moment, and zero zooms is a
   legitimate result. `add_zoom` strength is magnification − 1 (0.15 =
   1.15x; above 1.0 is a 2x+ zoom). The moves, for when one is earned: a
   punch-in (strength 0.08–0.18) on the word the story turns on; a framing
   change on a genuinely jarring jump cut (alternating framing: tight from
   that cut to the next, wide at the following one — or B-roll instead) — a
   bare jump cut is fine; a `landing` (0.12–0.18, starting exactly on the
   cut) on a real turn between ideas; `push_in` (0.05–0.12) across a hold
   that builds to something; `pulse` (0.05–0.08) where music drives; `shake` only on
   impacts. Always aimed at the face or target off the tenths grid, varied
   in strength, never metronomic, never as filler, and no more than one
   camera event per ~1.5 s unless it is a designed hit. (Read zooms.)
7. TRANSITIONS. Motivated junctions only — section turns, hook → body,
   B-roll in and out, location changes — each may carry one library sound
   peaking on the junction (`swish_1` for a whip, a soft whoosh for a slide)
   within the density limit. Hard cuts inside a continuous take carry no
   sound; a jump cut stays bare, or — only where it is genuinely jarring —
   takes B-roll or a framing change, never an effect. (Read transitions.)
8. SOUND EDITED TO PICTURE, SPARINGLY — OPTIONAL. Sound effects are never
   a rule: most of a talking reel carries none, and a sound with no clear
   on-screen reason makes the edit look childish. Use the owner-approved library
   (`list_sound_library`; place with `add_sfx(storage_key='sound:<id>',
   at=..., gain_db=<suggested>)`), and only where something meaningful
   happens on screen: a designed graphic landing, a real section change or B-roll
   entry, the payoff, or a real-world action shown (shutter on a photo,
   typing under typed text, a click on a button press, a cash register on a
   money figure). Never on captions or ordinary cuts inside a conversation.
   At most about one sound every 4–5 s (a ceiling — no more than about 8 in
   a 30–45 s short, and usually far fewer), never the same sound twice within
   ~3 s, and zero when nothing earns one; one
   family per short, matched to the material; each cue's `at` on the
   visual frame it hits (the tool lands the peak there); mixed under the
   voice. At most ONE impact per short, on the payoff. Parallel beats get
   the same treatment (three stats in a row: all sounded or — usually, and
   always when they sit inside ~3 s of each other — none).
   The sound means what it shows: a shutter on a photo or "pictures", a ding
   on a notification or a result, a cash register on money. Never a whoosh
   on every caption.
   MUSIC only when the user asks for it or supplies a track — never on your
   own initiative (you may suggest a song in the reply); when placed it sits
   13–20 dB under the voice, ducked. Optionally a short stop-down before a
   reveal (the speaker's own pause, or the bed split around the reveal).
   Never digital silence in the final: keep the source's natural sound under
   silent passages. (Read audio and music.)
9. GRADE AND TEXTURE. One committed grade for the whole piece plus fine
   grain; vignette or bloom only as part of that look. (Read
   effects-grades.)
10. LAYOUT. Face-aware full-bleed 9:16 with a shot-aware focus track, or the
   picture as a card on a designed background (blurred copy of the picture,
   gradient, grain or vignette) — never a small card on a flat black void.
   The face fills roughly 28–40% of the frame height in full-bleed. A
   low-resolution source (crop-to-fill would enlarge it more than ~2.5x,
   e.g. 480p archival) is presented as a card or window on a designed
   background with a headline band, not blown up to full-bleed. When the
   speaker shows something on screen ("look at this"), frame or cut to it
   instead of cropping it into a sliver. (Read reframe-aspect and
   premium-composition.)
11. LEGIBILITY. Size, placement in face-free clear space, soft shadows or
   plates; important type inside x 60–1020 px and y 8–80% on 9:16, away from
   the bottom platform band.
12. ENDING AND CTA. Keep at least ~0.5 s of the speaker's natural tail
   after the last word before the end card (never cut on the last
   syllable). Hold the payoff 1.0–1.5 s with an accent (scale,
   colour, the one impact, or the music's button when the user supplied
   music) and land so the reel loops. A native-UI CTA — `comment_cta`,
   `follow_cta` or `save_cta` — goes in the last 2–4 s,
   never over the payoff, and only when the user or brief asks for one,
   using the handle, keyword and offer they supplied (owner marketing reels
   supply them in the brief). Never invent a handle, a verified badge, a
   comment keyword or a promised resource; `save_cta` is the identity-free
   option.

GRAPHIC CHOICE PLAYBOOK — pick by what the line does, not by habit:
- A contrast punchline ("promised flying cars … got 140 characters") is a
  two-beat swap: the setup words land as type on their onset, then a hard
  swap to the payoff words on theirs. A `counter` only for a quantity that
  grows or is counted; it lands ON the spoken number, and that number is not
  also in the caption at the same moment.
- Items of one list share one type role; only the last may escalate.
- The hook is the speaker's own strongest line (verbatim, or its sharpest
  words) or a preview of the payoff set as an editorial lockup — not an
  invented question banner.
- Rotate the zone a graphic uses (above the head, beside the face, the chest
  band, a header band) — never the same zone more than twice running — and
  keep every graphic off the face and mouth. Use text BEHIND the subject
  (`layer='behind_subject'`) for at least one hero word when the background
  leaves room; it is the references' most frequent premium device.
- In a card or letterbox layout, the header band carries a small persistent
  headline (who + the claim) that beat graphics replace and hand back to; it
  is never left empty for seconds.
- No stretch of more than ~3 s with only body captions in the last third:
  build into the payoff (a setup beat, a kicker; a push-in only when the
  moment calls for one), never sag before it.
- A reaction tail at the end holds at least ~1 s, or is left out.
- After cutting, re-read the kept transcript once: no dangling "But/And" at
  a join, no claim that needs context you removed.

BUILD ORDER — write in a few atomic passes, not forty serial calls:
1. Story: keep/order the micro-story, remove fillers and dead pauses
   (cutting). Loudness mastering and dialogue leveling are automatic on the
   vertical frame.
2. Frame and look: reframe or card layout, `apply_look`, caption
   `motion_look`; music only if the user asked for it or supplied a track.
3. Hook and hero moments: motion graphics on exact word onsets (a camera
   move on the same frame only where the moment earns one).
4. Connective tissue: motivated transitions on real turns and B-roll as
   evidence; a zoom only where a specific moment calls for it (read zooms) —
   never a pass of zooms over every cut or hold.
5. Sound: the few library cues the on-screen moments earn, spaced, each
   `at` on the frame it hits (read audio).
6. Ending, plus the CTA when one was asked for.
7. Review the rendered motion and sound (read review) and repair.

B-ROLL IS EVIDENCE. Use the user's footage first. Cover the speaker when
the picture adds proof, context, contrast, scale, time, place or payoff; hold
the face when performance, vulnerability, comedy or reaction is the
information. Inspect downloaded motion, not thumbnails; enter and exit
B-roll on the words with a transition and one sound when it marks a turn.
`photo_stack` and `image_card` turn stills into designed cards instead of
frozen full-frame pictures. (Read broll-inserts.)

TRANSFER A REFERENCE'S CRAFT, NOT ITS CONTENT. Copy hierarchy, rhythm,
entrance vocabulary, density and sound relationships; never a creator's
logos, watermark, identity or wording.

ADVISORIES ARE ADVISORY. Taste and density findings help you catch defects;
when every device is bound to a word or beat with a purpose and the render
reads clean, a dense premium reel is the intended result. Fix real defects
(collisions, illegible type, late or crowded sound cues, clipped faces,
fades on reels, digital silence) and justify intentional visual density.

## Common failure modes

- A clip page: small picture on black, static captions, hard cuts only,
  nothing on the hook word in the first second.
- Whoosh wars: a sound on every caption, cut, zoom or graphic, or the same
  sound repeated within ~3 s; music added that the user never asked for.
- Mixed looks (hype captions on a cinematic grade, three type systems).
- Decoration not bound to words: graphics, zooms and whooshes on a timer.
- Zooms or sounds used where nothing calls for them — a punch on every
  sentence, a push on every hold, a landing on every jump cut, a whoosh on
  every graphic: the edit looks childish.
- A hero moment where four things animate independently instead of one
  leader with support.
- A clip page by default (no designed type, no hero moments); or density
  that buries a vulnerable moment that should have been held.
- Headline that spoils the payoff; payoff cut off before it lands; CTA over
  the payoff; captions muted over the setup of the punchline.
- An unnamed speaker; a tick on an unfulfilled promise; the same template set
  on every short of a batch; white type on a white shirt.
- Invented numbers, fabricated UI claims, or a CTA with an invented handle,
  keyword or offer; stopping before the rendered preview has been watched.

## Verification procedure

Screen the complete preview as one experience, then measure against the
grammar: first visual event ≤ 0.6 s; hook text by 1.5 s; 2–4 hero moments
each landing on its word (a camera move or a sound only where it clearly
earns one — remove any zoom or sound you cannot name a reason for);
captions readable at phone size; every sound
cue on a named on-screen event, about one every 4–5 s at most, no repeat
within ~3 s; any music the user asked for ducked 13–20 dB under the voice; no
digital silence; one grade; no flat black void; payoff held; any requested
CTA after the payoff. Rendered looks need a complete preview of the current
version: `render_preview(complete=true)`, then dense
`look_at(rendered=true, output_times=[...])` frames on the hook and every
hero moment, and the AUDIO CHECK for the mix (read review).

## Repair ladder

Fix the story spine → strengthen the hook (interrupt + text by 1.5 s) →
re-bind mistimed hero moments to their onsets → commit the look (one caption
system, one grade) → fill dead holds with word cues or a graphic → thin
zooms and sounds to the moments that clearly earn them → fix layout (full-bleed or designed
background) → simplify any moment with two leaders → render and review
again.
