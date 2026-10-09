# short-form-direction — directing one story into a premium reel: committed look, hook, hero moments, motion, sound, review

## Editorial decision principles

A premium reel is one coherent experience from promise to payoff, carried by
committed art direction. The owner's reference reels — the bar Valmera must
meet and beat — share a measurable grammar: a pattern interrupt in the first
half-second, the hook line readable as text by 1.5 s, something visual
changing every 0.3–0.6 s, 2–4 designed hero moments on exact spoken words,
motion typography instead of static subtitles, eased camera moves, sound
edited to picture under a music bed, one committed grade with grain, and a
frame with no dead black. A podcast clip with plain captions on a small card
over black is a clip page, not a premium reel.

- Story first: the reel still needs setup → development/turn → payoff, and
  every design decision serves that spine.
- Commit to ONE look per edit and keep it from frame one to the CTA.
- Design density is the expected state for vertical reels of 120 s or less
  (including podcast shorts children). Stillness is a deliberate choice for a
  specific passage — a vulnerable admission held on the face, a laugh, a
  silence after a reveal — not the default for the whole piece.
- Every device is bound to a word, a beat or a cut, and carries a purpose.
  Dense is good; random is not.
- Never invent facts: numbers, quotes, identities, brand messages and
  metrics come from the transcript, the user or a verified source.

## Evidence to inspect

Watch and hear this exact story: the kept transcript with program times
(`get_kept_transcript`), word onsets for hero words (`get_words`), measured
stress and numbers (`suggest_emphasis`, `get_audio_analysis`), shots and
faces (`get_editorial_map`, `get_shots`), the opening frames and every turn
(`look_at`), the reference asset when present (`look_at_asset`), the live
motion library (`list_motion_templates`) and the sound kit (`list_sfx_kit`).

## Strong treatment patterns

YOU ARE THE FRESH EDITOR. For a shorts child, the parent chose a complete
story because it is worth developing. The selection is a strong hypothesis,
not sacred footage: tighten repetition, reorder a cold open, or restore a
nearby beat when that makes the story clearer. Never turn it back into a
contextless quote.

THE PREMIUM SHORT-FORM GRAMMAR — targets you can measure in the render:

1. ONE COMMITTED LOOK. Start with `apply_look` — `editorial` (premium
   default for podcast and interview reels), `creator_punch` (high-energy
   creator), `cinematic_doc` (story and documentary), `mono_noir` (B&W plus
   one accent), `clean_minimal` (quiet product or education) — or build a
   deliberate equivalent: a caption `motion_look`, one grade plus grain, a
   transition vocabulary and a music bed. Refine components afterwards; do
   not mix looks. On a vertical reel there is no fade-in from black; remove
   one with `set_fades` if a look adds it.
2. HOOK. A visual pattern interrupt lands within 0.1–0.6 s: open punched-in
   with a landing zoom, a `hook_title` or `word_slam`, a flash or light leak,
   with a low hit or sub drop under it. The hook line is on screen as text
   by 1.5 s; the speaker is visible and talking by ~0.3 s. Never open on
   black, a logo, dead air or more than ~3 s of a non-speaker setup. The
   hook text poses the question; it never spoils the payoff. (Read
   hooks-retention.)
3. RHYTHM. Something visual changes every 0.3–0.6 s — motion-caption word
   reveals on onsets count — and a bigger change (camera move, graphic,
   B-roll, layout shift) arrives every 2–4 s. No static hold longer than
   ~2 s without a designed reason. Vary the interval with the speech:
   cluster on dense ideas, breathe on a real admission.
4. HERO MOMENTS. Choose 2–4 per reel, each bound to the exact spoken word:
   a hero word slam, a counter on a spoken number, a UI card when the speaker
   describes a message or search, text behind the subject, a callout circle
   or arrow on a visible thing, a chapter title on a real turn. Each hero
   moment stacks ONE leader graphic + an eased camera move + a sound landing
   on the same frame. (Read motion-design.)
5. CAPTIONS. Browser motion captions via `style.motion_look`: `editorial`
   or `clean` as the premium default for podcast and interview speech,
   `lockup` or `serif` for editorial accents, `pop` or `stack` for hype,
   `box` or `glow` for high-contrast karaoke, `mono` for tech. Words reveal on
   their onsets; a size ladder separates connector words from 1–2 accent
   words per sentence; captions mute under graphics that say the same words.
   (Read captions.)
6. CAMERA. Eased punch-ins (1.08–1.18x) on emphasis words, landing zooms
   (1.12–1.18 easing to 1.0) just after jump cuts, a slow push on holds
   longer than ~3 s, a beat pulse where music drives, shake only on impacts.
   Always aimed at the face or target off the tenths grid, varied in
   strength, never metronomic. (Read zooms.)
7. TRANSITIONS. Motivated junctions only — section turns, hook → body,
   B-roll in and out, location changes — each with a paired whoosh or hit
   peaking on the junction. Hard cuts inside a continuous take; jump cuts are
   covered by landing zooms, not effects. (Read transitions.)
8. SOUND EDITED TO PICTURE. A music bed 13–20 dB under the voice, ducked;
   kit cues pre-rolled so their peaks land on the visual frame: whoosh under
   entrances, pop or tick on reveals, typing under typewriters, shutter on
   photos, low hit on hero landings, a riser into the payoff, a 50–280 ms
   stop-down before a reveal. Never a whoosh on every caption; never digital
   silence in the final. (Read audio and music.)
9. GRADE AND TEXTURE. One committed grade for the whole piece plus fine
   grain; vignette or bloom only as part of that look. (Read
   effects-grades.)
10. LAYOUT. Face-aware full-bleed 9:16 with a shot-aware focus track, or the
   picture as a card on a designed background (blurred copy of the picture,
   gradient, grain or vignette) — never a small card on a flat black void.
   The face fills roughly 28–40% of the frame height in full-bleed. (Read
   reframe-aspect and premium-composition.)
11. LEGIBILITY. Size, placement in face-free clear space, soft shadows or
   plates; important type inside x 60–1020 px and y 8–80% on 9:16, away from
   the bottom platform band.
12. CTA AND ENDING. Hold the payoff 1.0–1.5 s with an accent (scale, colour,
   impact, music button), then a native-UI CTA — `comment_cta`, `follow_cta`
   or `save_cta` — in the last 2–4 s, never over the payoff. Land so the
   reel loops.

BUILD ORDER — write in a few atomic passes, not forty serial calls:
1. Story: keep/order the micro-story, remove fillers and dead pauses
   (cutting), master loudness.
2. Frame and look: reframe or card layout, `apply_look`, caption
   `motion_look`, music bed.
3. Hook and hero moments: motion graphics on exact word onsets with their
   own sound cues, camera moves bound to the same frames.
4. Connective tissue: landing zooms on jump cuts, motivated transitions with
   paired sounds, slow pushes on long holds, B-roll as evidence.
5. CTA and ending.
6. Review the rendered motion and sound (read review) and repair.

B-ROLL IS EVIDENCE. Use the user's footage first. Cover the speaker when
the picture adds proof, context, contrast, scale, time, place or payoff; hold
the face when performance, vulnerability, comedy or reaction is the
information. Inspect downloaded motion, not thumbnails; enter and exit
B-roll on the words with a transition and sound when it marks a turn.
`photo_stack` and `image_card` turn stills into designed cards instead of
frozen full-frame pictures. (Read broll-inserts.)

TRANSFER A REFERENCE'S CRAFT, NOT ITS CONTENT. Copy hierarchy, rhythm,
entrance vocabulary, density and sound relationships; never a creator's
logos, watermark, identity or wording.

ADVISORIES ARE ADVISORY. Taste and density findings help you catch defects;
when every device is bound to a word or beat with a purpose and the render
reads clean, a dense premium reel is the intended result. Fix real defects
(collisions, illegible type, late cues, clipped faces, fades on reels,
silence) and justify intentional density.

## Common failure modes

- A clip page: small picture on black, static captions, hard cuts only, no
  music or sound design, nothing on the hook word in the first second.
- Mixed looks (hype captions on a cinematic grade, three type systems).
- Decoration not bound to words: graphics, zooms and whooshes on a timer.
- A hero moment where four things animate independently instead of one
  leader with support.
- Restraint as a blanket default; or density that buries a vulnerable
  moment that should have been held.
- Headline that spoils the payoff; payoff cut off before it lands; CTA over
  the payoff.
- Invented numbers or fabricated UI claims; stopping before the rendered
  preview has been watched.

## Verification procedure

Screen the complete preview as one experience, then measure against the
grammar: first visual event ≤ 0.6 s; hook text by 1.5 s; no hold over ~2 s
without design; 2–4 hero moments each landing on its word with camera and
sound; captions readable at phone size; music bed present and ducked; no
digital silence; one grade; no flat black void; payoff held; CTA after the
payoff. Use dense `look_at(rendered=true, output_times=[...])` frames on the
hook and every hero moment, and the AUDIO CHECK for the mix.

## Repair ladder

Fix the story spine → strengthen the hook (interrupt + text by 1.5 s) →
re-bind mistimed hero moments to their onsets → commit the look (one caption
system, one grade) → fill dead holds with camera or word cues → add the
music bed and structural sound → fix layout (full-bleed or designed
background) → simplify any moment with two leaders → render and review
again.
