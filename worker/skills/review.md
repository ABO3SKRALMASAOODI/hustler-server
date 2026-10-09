# review — the final screening pass: rendered motion, sound, the premium bar, honest handoff

## Editorial decision principles

Completion is earned by evidence from the latest EDL and its rendered
preview. A successful tool call is not proof that an edit is good, and a
single settled still is not proof that motion is good: entrances, landings,
eases and sound cues have to be inspected in the rendered frames around the
moment they happen. The bar is the owner's premium references, not "nothing
is broken".

## Evidence to inspect

Inspect deterministic checks, the AUDIO CHECK, dense rendered frames around
the hook and every hero moment, caption pages, every junction the edit
touched, the payoff and the ending, plus any bounded listening evidence.

## Strong treatment patterns

HOW TO LOOK AT MOTION — two different renders:
- During iteration, `render_preview(complete=false)` encodes only the
  seconds changed since the last complete preview and returns RENDER CHECK
  and caption QA tiles of them. Judge placement, legibility and collisions
  from that result; the complete preview adds the AUDIO CHECK of the whole
  mix.
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
   Hook text readable by 1.5 s? Speaker on screen and talking by ~0.3 s? No
   black, no fade-in, no dead air?
2. RHYTHM: scanning the program, is anything static for more than ~2 s
   without a designed reason? Does a bigger change arrive every 2–4 s?
3. HERO MOMENTS (dense frames each): does one leader land on its word
   (0–3 frames early), with camera (and any sound) on the same frame, clear
   of the face and the UI band, readable at phone size, and exit cleanly?
4. CAPTIONS: words appear on onsets; accents on the right 1–2 words; legible
   against every background; no overlap with faces, graphics or the platform
   band; no caption stacked over burned-in text.
5. CAMERA: each zoom aimed at the face or target, eased (no steps or drift),
   jump cuts covered by alternating framing, landings only on cuts between
   ideas, varied strengths, no more than one camera event per ~1.5 s except
   a designed hit.
6. JUNCTIONS: cuts on word edges, no flash or double frames; transitions only
   on real turns, any sound peaking on the cut; no effect or sound on a jump
   cut.
7. SOUND: AUDIO CHECK loudness and peaks; no digital silence; every cue on
   a named on-screen event (never a caption or an ordinary cut), about one
   every 4–5 s at most, no sound repeated within ~3 s; nothing masking the
   voice; music present only if the user asked for it or supplied it, then
   roughly 13–20 dB below the voice. ACTUAL-AUDIO REVIEW, when present,
   adds bounded listening evidence — never claim continuous listening beyond
   its labeled windows.
8. LOOK AND LAYOUT: one grade and texture throughout; no flat black void; no
   scene obviously rawer than the rest.
9. THE END: payoff held 1.0–1.5 s, a requested CTA after it (not over it),
   last beat lands clean for the loop.
10. THE BRIEF: reread the user's message once. Every named item delivered or
    honestly reported? Anything they forbade present anyway?
11. HONESTY: every number, name, quote and UI claim on screen is supported by
    the transcript, the user or a verified source; a CTA uses only the
    handle, keyword and offer the user or brief supplied (no invented
    verified badge or promised resource); every device has a purpose you
    can name.

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
- Ignoring the AUDIO CHECK; claiming to have heard unreviewed seconds.
- Defending an orphan device as polish, or stripping intentional design to
  silence an advisory.

## Verification procedure

Require deterministic checks, dense rendered frames on the hook and every
hero moment, caption QA pages, the AUDIO CHECK, current-version visual and
audio review, repairs or justifications for every finding, and one complete
Studio preview.

## Repair ladder

Localize the finding → apply the narrowest repair → prove the changed
window with `render_preview(complete=false)` → render complete and
re-inspect motion with dense rendered frames → rerun deterministic checks →
screen the complete preview → continue until it meets the bar or a genuine
blocker remains.
