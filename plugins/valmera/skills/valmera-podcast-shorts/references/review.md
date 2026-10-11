# Review

Fast, decisive and done from the moving result. Three tiers; only Tier 2 is
the coordinator's job. There is no numeric score gate: a short ships because
it answers five questions with yes, not because someone wrote 90.

## Tier 0: automatic

Read what Valmera already reports: render warnings, `audit_captions` (late or
missing words, overlaps), and `audit_audio_mix` when music or sound changed.
A Tier-0 defect goes straight back to the editor as a fix; it is not a matter
of taste. The render result's VERIFICATION ADVISORIES also carry an EARN
ITS PLACE note (a generic or spent hook, a fragment or jump-cut opening,
graphics past the budget or restating the captions, a short payoff hold):
advisory, but read it before answering questions 1, 2 and 5.

## Tier 1: editor self-check

The editor checks the render on rendered frames and the audio tools, looks
at the hook, hero moments and payoff, measures picture area, headline and
caption cap heights and the payoff's size, fixes defects plus the single
weakest moment, and hands back a note of at most 10 lines with those numbers
([editing.md](editing.md) steps 5-6). The coordinator verifies the numbers
on its own native frames rather than repeating the check frame by frame.

## Tier 2: coordinator batch review

Review each candidate as it arrives, ahead of any new edit (in a shared
queue reviews go first). The run's first candidate is reviewed before a
4th short is handed out (`run.py` enforces this gate), because its verdict
decides whether the other briefs and the framing recipe hold.

**Evidence, about 6-8 calls and 5 minutes:** `render_preview(report=true)`
on the candidate (the stored PICTURE CHECK, CAPTION CHECK, MEASURES line and
advisories, no new render: the measured picture area, upscale and cap
heights the floors below are judged on), one rendered `look_at` batch (0,
0.3, 1.0 s, each hero, the densest caption, the payoff, the last frame),
`native_resolution` frames at the hook and the payoff in one call to confirm
those numbers by eye, `audit_captions`, and the editor's
`review_audio` / `audit_audio_mix` results (spot-check one join with
`review_audio` when the note flags it). The preview has no end card or
corner mark: branding is checked on the final. Then answer:

1. **Hook:** does a designed moment land by 0.6 s, with the hook line
   readable by 1.5 s, written from the clip's strongest line or statistic
   (not a generic question, not showing a later hero word), on a clean
   first frame (speaker facing camera, a whole first word, no jump cut in
   the first 1.5 s), and would a stranger keep watching?
2. **Payoff:** is the payoff the largest accented lockup (number and noun
   together; a sound or a camera move only where it earns one; the owner's
   song's button when music is on), held 0.8-1.5 s after the last word,
   and does it resolve the viewer question?
3. **Targets:** does it meet its Look's targets: change rate, hero moments,
   picture area; sound only from the approved library, zero by default and
   at most 1-2 per short, each on a structural on-screen moment with a
   visual partner within ~50 ms, none on a payoff word's onset, no literal
   pun on the spoken word, no reflexive opening whoosh, nothing too quiet to
   hear or masking a word (`audit_audio_mix`), no sound repeated within
   ~3 s, nothing on captions, zooms or ordinary cuts; music only if `music_effective` is on and only the
   owner's song; no digital silence (a flagged montage passage is noted in
   the handback)?
4. **Attention:** with the famous name hidden, is the idea still worth
   finishing?
5. **Clean:** faithful claims and qualifiers, accurate readable captions
   (none muted over a punchline's setup), speakers named (in the kicker or
   headline band, no broadcast lower third over a famous face), graphics
   that mean what is said and each add what the captions cannot (about one
   per 6-8 s at most, under ~50% of the runtime, at most 3 type roles and
   one accent, no transcript re-typeset, no spoken list as rows of text,
   products identified), no zoom or sound without a reason you can name (unearned
   zooms and sounds look childish — zero of either is fine), no white type
   on white clothing, at most one impact sound
   (on the payoff), 0.8-1.5 s after the last word before the end card,
   nothing over the face or the brand corner, the corner mark and native end
   card untouched, no flash frames, pops, exposed edges or clipped words,
   no face at the frame's edge, a clean PICTURE CHECK on the complete
   preview (each finding repaired or kept with a reason), rights recorded
   for every asset?

Measured floors are binary. Picture area under the Look's floor, a hook
under its size rule (a band headline's claim under 1.2x the captions' cap
height, a hook-tier slam's main line under 7% of the frame height), a
payoff smaller than an earlier lockup or a beatless stretch over ~8 s is a **no**, whatever the reason; a
"source limit" explains it but does not pass it (Oct 2026: a fix verdict
that waived a 0.31 card cost a fix pass, two renders and a failed export,
and the short was killed for the same 0.31 card).

Record it with `run.py review --checks hook=...,payoff=...,targets=...,attention=...,clean=...`.

| Answers | Verdict |
| --- | --- |
| all yes | **ship**: note the strongest moment in one line |
| one no that one change can repair, and nothing else would still fail | **fix**: one targeted fix (below) |
| attention is no, or several no | **kill**: say why; a weak story is not rescued by styling |
| a Valmera defect decides it (a tool that errors, a template or layout the engine renders below the floor, an export that fails), or the editor handed back `blocked` | `run.py exception --status failed_technical`, next action "revive after Valmera fix: <defect>"; it is a product finding, not an editorial kill |
| a technical failure outside the edit | `run.py exception` with the next action |

**After every verdict ask: do the shorts in flight share this cause?** A
layout, source, template or tool cause is shared. Then the coordinator
stops handing out shorts, fixes `framing.json` and the open briefs (or
stops the line, SKILL.md) before the next hand-out. Oct 2026: the first
candidate showed the run-killing cause (picture 0.34, headline smaller
than captions) at 23:15; it was reviewed at 00:04, after five more shorts
had been started with the same defect.

Each short gets at most one targeted fix. A second weak candidate is shipped
if all five answers are yes, otherwise killed or recorded as an exception.
Killing a short is cheaper and better than a rescue; every selected short
still appears in the manifest.

### Fix packet (at most 5 lines)

```text
s07 fix · EDL v14
18.2-20.4s: two seconds of talking head with captions only; the turn "ten million" has no beat.
Change: counter 0 -> 10M on "ten million" (cue 18.6, silent); no zoom or sound needed.
Keep: everything else, especially the hook and payoff.
```

Name the timestamp, the effect on the viewer, the exact change and what not
to touch. Never send a list of nitpicks or a re-edit request, and never ask
for a zoom or a sound as the fix for a flat moment (a beat of type or a
graphic is); removing a zoom or sound that nothing on screen earns is always
a fair fix.

### Golden traits per Look

| Look | Ship only if |
| --- | --- |
| Headline Pro | big face on a designed card, a still headline, captions that perform, the payoff lands |
| Editorial Serif | a quiet field, serif contrast on the right word, depth behind the subject |
| Kinetic Poster | captions perform almost every stressed word (sound only on the few that earn it), hero graphics add information; the payoff is the biggest event |
| Cinematic Doc | graded and textured; archive feels like film |
| Mono Noir | stark monochrome, red only where it matters, any sound hits with weight |
| Clean Data | numbers become pictures, and a settled figure lands (one sound at most) |
| Creator Glow | glowing words build beside the face; the hook reads before the sentence ends |

### Batch view

After each batch, look across the shipped shorts: the same Look on most
stories, the same hero device in every short, or identical openings are batch
problems. Fix them in the next briefs rather than reopening shipped shorts.

## Finals

After `export_final` completes and the file is downloaded, watch the run's
first final in full: the native corner mark is legible and clear of faces and
type, the complete 5 s native Valmera ending is present, the audio plays
through, and the duration matches. The final's PICTURE CHECK (the render
result's `picture_check`) confirms the end card and, on free-tier exports,
the corner mark were measured in the file; a missing one is a render fault
to re-export and report, never something to edit around. Record it with `run.py export
--verified-full`. Later finals of the same geometry need only the probe that
`run.py export` already does (file, duration, sha256), unless their layout
differs. The corner mark and the 5 s end card stay exactly as Valmera
renders them in every short: if the corner mark collides with the
composition, move the type, card or graphic, never the mark, and never
crop, cover, trim, shorten or replace the ending.

## Calibration rubric (not a gate)

Use this at the end of a run on 2-3 shipped shorts next to the reference
reels, and whenever the owner gives feedback. Adjust the Looks, not the
prose, when the batch scores low. Score 0-10 per dimension: **H** hook,
**C** captions and typography, **G** motion graphics, **A** animation craft,
**K** camera, **T** transitions, **S** sound effects, **M** music, **L** colour,
texture and grade, **F** layout, **P** pacing, **E** payoff, ending and loop.

Anchors: 0 absent or broken; 3 present but generic or static; 5 a competent
clip page (gum-04, gum-05, gum-08); 7 a strong creator (orig-07, orig-11,
orig-17); 9-10 the best in the set (orig-04, orig-05, gum-03, gum-09).

| Measured | H | C | G | A | K | T | S | M | L | F | P | E | Mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Valmera October shorts | 3.9 | 4.9 | 3.2 | 1.8 | 1.3 | 1.1 | 0 | 0 | 3.3 | 3.3 | 4.3 | 3.5 | 2.6 |
| orig-04 Creativity is Dead | 9 | 9 | 10 | 9 | 8 | 10 | 8 | 8 | 9 | 9 | 9 | 7 | 8.8 |
| orig-05 Hooks | 9 | 9 | 8 | 9 | 8 | 8 | 8 | 7 | 8 | 8 | 8 | 6 | 8.0 |
| gum-09 | 8 | 8 | 10 | 9 | 7 | 9 | 8 | 8 | 8 | 8 | 9 | 6 | 8.2 |
| gum-05 clip page | 5 | 6 | 3 | 4 | 3 | 3 | 2 | 6 | 6 | 6 | 4 | 3 | 4.3 |

Premium references average 7.5, the top five 8.2. A hero short worth
shipping as the owner's marketing should reach 7 or more; beating the
references means 8 or more. **K** rewards camera moves that are motivated
and few, never their count — a steady, well-framed short with no zoom where
nothing called for one scores on its framing; **S** rewards sounds that are
placed, sparse and earned, never their count — a podcast short with no
sound scores on its clean voice; **M** is scored only when the owner supplied a
song. These scores are for calibration and owner
conversations, never a ship gate or a number to stamp on every short.
