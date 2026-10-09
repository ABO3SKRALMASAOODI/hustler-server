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

HOW TO LOOK AT MOTION: `render_preview(complete=false)` during iteration
encodes only the changed seconds; then
`look_at(rendered=true, output_times=[...])` with up to 8 dense times around
a moment — e.g. landing − 0.1, landing, +0.03, +0.07, +0.13, +0.27, +0.5, and
the release. The geometry-only view (no `rendered=true`) shows framing and
zoom aim without a render but contains no captions, graphics or grade.
`native_resolution=true` on one time gives full-detail pixels. One complete
preview is produced for handoff; check its opening and hero moments again.

SCREEN IN THIS ORDER — each item is a yes/no question:
1. THE OPEN (rendered 0.0, 0.2, 0.6, 1.5): first visual event by 0.6 s?
   Hook text readable by 1.5 s? Speaker on screen and talking by ~0.3 s? No
   black, no fade-in, no dead air?
2. RHYTHM: scanning the program, is anything static for more than ~2 s
   without a designed reason? Does a bigger change arrive every 2–4 s?
3. HERO MOMENTS (dense frames each): does one leader land on its word
   (0–3 frames early), with camera and sound on the same frame, clear of the
   face and the UI band, readable at phone size, and exit cleanly?
4. CAPTIONS: words appear on onsets; accents on the right 1–2 words; legible
   against every background; no overlap with faces, graphics or the platform
   band; no caption stacked over burned-in text.
5. CAMERA: each zoom aimed at the face or target, eased (no steps or drift),
   landing zooms on jump cuts, varied strengths.
6. JUNCTIONS: cuts on word edges, no flash or double frames; transitions only
   on real turns, each with its sound peak on the cut; no effect on a jump
   cut.
7. SOUND: AUDIO CHECK loudness and peaks; a bed present under speech at
   roughly 13–20 dB below the voice; no digital silence; every cue on its
   named event; nothing masking the voice. ACTUAL-AUDIO REVIEW, when present,
   adds bounded listening evidence — never claim continuous listening beyond
   its labeled windows.
8. LOOK AND LAYOUT: one grade and texture throughout; no flat black void; no
   scene obviously rawer than the rest.
9. THE END: payoff held 1.0–1.5 s, CTA after it (not over it), last beat
   lands clean for the loop.
10. THE BRIEF: reread the user's message once. Every named item delivered or
    honestly reported? Anything they forbade present anyway?
11. HONESTY: every number, name, quote and UI claim on screen is supported by
    the transcript, the user or a verified source; every device has a
    purpose you can name.

TASTE AND DENSITY FINDINGS ARE ADVISORY: fix real defects (collisions,
illegible type, mistimed cues, clipped faces, fades on reels, silence,
invented facts) and keep intentional density bound to words and beats. When
a verification finding flags intentional design that the rendered frames
show is clean, resolve it with `justify_verification_findings`, citing that
pixel or audio evidence.
Deterministic render failures and unresolved current-version checks need
repair or an honest limitation.

FIX DISCIPLINE: choose the narrowest repair that fixes the moment —
retime, re-aim, move, resize, swap — and re-render only the changed window.

THE BAR: would a top Instagram editor post this next to the reference reels?
The hook earns the stop, the middle never sags, the sound feels produced,
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

Localize the finding → apply the narrowest repair → re-render the changed
window → re-inspect with dense rendered frames → rerun deterministic checks →
screen the complete preview → continue until it meets the bar or a genuine
blocker remains.
