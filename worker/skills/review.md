# review — the final screening pass: rendered motion, sound, the premium bar, honest handoff

## Editorial decision principles

Completion is earned by evidence from the latest EDL and its rendered
preview. A successful tool call is not proof that an edit is good, and a
single settled still is not proof that motion is good: entrances, landings,
eases and sound cues have to be inspected in the rendered frames around the
moment they happen. The bar is the owner's premium references, not "nothing
is broken".

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.
SOUND EFFECTS IN A PODCAST OR TALKING SHORT DEFAULT TO ZERO (owner, Oct
2026): at most 1-2 per short, each on a structural moment (the payoff, a
real section change) with a visual partner within ~50 ms of its hit (a
graphic landing, a B-roll entry, a real-world action shown). Never a
reflexive opening whoosh, never a bright sound (ding, pop, click, shutter)
on the onset of a payoff or emphasis word, and never a literal sound pun: a
shutter on the word 'pictures', a cash register on the word 'money' when
nothing on screen is a payment. Leave gain_db unset: add_sfx levels each
library sound against the measured voice at its hit and reports where it
sits.
A missing zoom or sound is never a defect; an unearned one is.

## Evidence to inspect

Inspect deterministic checks, the AUDIO CHECK, the PICTURE CHECK, dense
rendered frames around the hook and every hero moment, caption pages, every
junction the edit touched, the payoff and the ending, plus any bounded
listening evidence.

## Strong treatment patterns

HOW TO LOOK AT MOTION — two different renders:
- During iteration, `render_preview(complete=false)` encodes only the
  seconds changed since the last complete preview and returns RENDER CHECK
  and caption QA tiles of them. Judge placement, legibility and collisions
  from that result; the complete preview adds the AUDIO CHECK of the whole
  mix and the PICTURE CHECK of its frames: a face within ~6% of the frame's
  (or its card's) edge, a one-frame pop or a jump that is not on a cut, an
  ORPHAN FRAME (a shot of 1-2 frames between two cuts — a crop, card or
  zoom switch a frame off the source's camera cut), a REFRAME OFF THE CUT
  or MID-SHOT, a hook that opens on closed eyes or mid-sound (with the
  nearest clean start, never applied), a missing end card or watermark
  (finals). It measures the file that ships
  and never changes the edit: repair each finding (look_at the frames it
  names first) or keep it deliberately and say why.
- `look_at(rendered=true, ...)` reads only a COMPLETE preview of the current
  EDL version; a changed-section proof does not count, and calling it
  without one is rejected. For dense motion frames, build the hook and hero
  moments, call `render_preview(complete=true)` (draft quality), then
  `look_at(rendered=true, output_times=[...])` with up to 8 dense times per
  call around a moment — e.g. landing − 0.1, landing, +0.03, +0.07, +0.13,
  +0.27, +0.5, and the release. Batch several moments into a few calls.
- Every repair makes a new version: render it complete again before the
  next rendered look. The geometry-only view (no `rendered=true`) needs no
  render and shows framing and zoom aim, but no captions, graphics or
  grade. `native_resolution=true` on one time gives full-detail pixels.

SCREEN IN THIS ORDER — each item is a yes/no question:
1. THE OPEN (rendered 0.0, 0.2, 0.6, 1.5): first visual event by 0.6 s?
   Hook text readable by 1.5 s, written from the clip's strongest line or
   statistic (not a generic question) and not showing a word a later
   graphic slams? Speaker on screen, facing camera and talking by ~0.3 s,
   on a clean first word (no fragment, no disfluency) with no jump cut in
   the first 1.5 s? Is the hook a headline (main line 7%+ of the frame
   height) owning its zone — no live caption stacked under it? No black,
   no fade-in, no dead air?
2. RHYTHM: scanning the program, does the type keep moving with the
   speech, and does the structure (a graphic, B-roll, a layout shift) move
   where the story turns? Is any zoom or sound there only to fill time
   (remove it)?
3. HERO MOMENTS (dense frames each): does one leader land on its word
   (0–3 frames early), with any camera move or sound on the same frame, clear
   of the face and the UI band, readable at phone size, and exit cleanly?
   Does each graphic EARN ITS PLACE — adding a number, a contrast, an
   identification, evidence or an image the captions cannot — with about
   one hero graphic per 6–8 s at most, under ~50% of the runtime, at most 3
   type roles and one accent? Remove any that only restate the caption.
   And the other half: does every thesis line, spoken list or triad, named
   product or place and number get a beat that adds information (an image
   first for concrete nouns, an accumulating list_build, a contrast, a
   counter that really counts and shows a range as said), with no body
   stretch past ~6–8 s without one?
4. CAPTIONS: words appear on onsets; accents on the right 1–2 words; legible
   against every background; no overlap with faces, graphics or the platform
   band; no caption stacked over burned-in text.
5. CAMERA (optional — zero zooms is fine): can you name the reason for
   every zoom? Remove any you cannot. Each one aimed at the face or target,
   eased (no steps or drift), landings only on cuts between ideas, varied
   strengths, never a move on every cut or sentence, no more than one camera
   event per ~1.5 s except a designed hit. A bare jump cut is fine; act on
   one only where it is genuinely jarring (B-roll or a framing change).
6. JUNCTIONS: cuts on word edges, no flash or double frames, no one-frame
   framing pop before or after a cut (PICTURE CHECK); transitions only
   on real turns, any sound peaking on the cut; no effect or sound on a jump
   cut.
7. SOUND (sound effects are optional — zero is fine): AUDIO CHECK loudness
   and peaks; no digital silence; every cue on a named on-screen event with
   a visual partner within ~50 ms (never a caption or an ordinary cut;
   remove any you cannot name a reason for), 1-2 at most in a podcast short
   and about one every 4–5 s at most elsewhere, no sound repeated within
   ~3 s; no reflexive opening whoosh, no bright sound on a payoff word's
   onset, no literal pun on the spoken word; nothing masking the voice and
   nothing too quiet to hear (`audit_audio_mix` reports each sound's level
   against the voice and its placement checks); music present only if the user asked for it or supplied it, then
   roughly 13–20 dB below the voice. ACTUAL-AUDIO REVIEW, when present,
   adds bounded listening evidence — never claim continuous listening beyond
   its labeled windows.
8. LOOK AND LAYOUT: one grade and texture throughout; no flat black void; no
   scene obviously rawer than the rest.
9. THE END: the payoff is the largest accented lockup (a number with its
   noun), held 0.8–1.5 s after the last word before the end card (the
   natural tail, else add_freeze_frame audio_mode='hold'; a big number
   ~2 s on screen), a
   reaction button 1.0–1.5 s or none, a requested CTA after it (not over
   it), last beat lands clean for the loop.
10. THE BRIEF: reread the user's message once. Every named item delivered or
    honestly reported? Anything they forbade present anyway?
11. HONESTY: every number, name, quote and UI claim on screen is supported by
    the transcript, the user or a verified source; a CTA uses only the
    handle, keyword and offer the user or brief supplied (no invented
    verified badge or promised resource); every device has a purpose you
    can name.

THE EARN ITS PLACE ADVISORY (in VERIFICATION ADVISORIES) lists, by time,
a generic or spent hook, a fragment or jump-cut opening, graphics past the
budget or restating the captions, a spoken list set as text, extra type
roles or accents, a lower third beside the hook, a product set as "Name:",
a short payoff hold, colliding sentences, and a barely visible or stacked
zoom (offered for removal only). Act on the notes that hurt this short.

TASTE AND DENSITY FINDINGS ARE ADVISORY: fix real defects (collisions,
illegible type, mistimed cues, clipped faces, fades on reels, silence,
invented facts) and keep intentional density bound to words and beats. When
a verification finding flags intentional design that the rendered frames
show is clean, resolve it with `justify_verification_findings`, citing that
pixel or audio evidence.
Deterministic render failures and unresolved current-version checks need
repair or an honest limitation.

FIX DISCIPLINE: choose the narrowest repair that fixes the moment —
retime, re-aim, move, resize, swap — check it with a changed-section proof,
and render complete again only when you need rendered motion frames.

THE BAR: would a top Instagram editor post this next to the reference reels?
The hook earns the stop, the middle never sags, the few sounds feel placed,
nothing looks accidental. If the honest answer is no and the cause is within
your tools, keep working. If the cause is the footage, say exactly that in
one sentence.

## Common failure modes

- Declaring completion from the EDL, a midpoint still or a geometry-only
  look that contains no graphics.
- Judging motion from one settled frame; missing a late landing or a
  drifting hold.
- Ignoring the AUDIO CHECK or the PICTURE CHECK; claiming to have heard
  unreviewed seconds.
- Defending an orphan device as polish, or stripping intentional design to
  silence an advisory.

## Verification procedure

Require deterministic checks, dense rendered frames on the hook and every
hero moment, caption QA pages, the AUDIO CHECK and the PICTURE CHECK,
current-version visual and
audio review, repairs or justifications for every finding, and one complete
Studio preview.

## Repair ladder

Localize the finding → apply the narrowest repair → prove the changed
window with `render_preview(complete=false)` → render complete and
re-inspect motion with dense rendered frames → rerun deterministic checks →
screen the complete preview → continue until it meets the bar or a genuine
blocker remains.
