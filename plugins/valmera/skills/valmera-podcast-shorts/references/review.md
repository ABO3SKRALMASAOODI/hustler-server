# Review

Fast, decisive and done from the moving result. Three tiers; only Tier 2 is
the coordinator's job. There is no numeric score gate: a short ships because
it answers five questions with yes, not because someone wrote 90.

## Tier 0: automatic

Read what Valmera already reports: render warnings, `audit_captions` (late or
missing words, overlaps), and `audit_audio_mix` when music or sound changed.
A Tier-0 defect goes straight back to the editor as a fix; it is not a matter
of taste.

## Tier 1: editor self-check

The editor watches the render at 1x, looks at the hook, hero moments and
payoff, fixes defects plus the single weakest moment, and hands back a note of
at most 10 lines ([editing.md](editing.md) steps 5-6). The coordinator does
not repeat this frame by frame.

## Tier 2: coordinator batch review

Review candidates in small batches as they arrive (2-4 at a time), while the
freed editors start their next shorts. For each one, `watch_video(render=false)`
at 1x with the sound on, then answer:

1. **Hook:** does a designed moment land by 0.6 s, with the hook line
   readable by 1.5 s, and would a stranger keep watching?
2. **Payoff:** is the payoff designed (type, sound or camera; the owner's
   song's button when music is on) and held 1.0-1.5 s, and does it resolve
   the viewer question?
3. **Targets:** does it meet its Look's targets: change rate, hero moments,
   picture area; sound only from the approved library and only on
   meaningful on-screen moments, at most about one every 4-5 s and within
   the Look's ceiling, no sound repeated within ~3 s, nothing on captions,
   zooms or ordinary cuts; music only if `music_effective` is on and only the
   owner's song; no digital silence (a flagged montage passage is noted in
   the handback)?
4. **Attention:** with the famous name hidden, is the idea still worth
   finishing?
5. **Clean:** faithful claims and qualifiers, accurate readable captions,
   nothing over the face or the brand corner, the corner mark and native end
   card untouched, no flash frames, pops, exposed edges or clipped words,
   rights recorded for every asset?

Record it with `run.py review --checks hook=...,payoff=...,targets=...,attention=...,clean=...`.

| Answers | Verdict |
| --- | --- |
| all yes | **ship**: note the strongest moment in one line |
| one no that one change can repair | **fix**: one targeted fix (below) |
| attention is no, or several no | **kill**: say why; a weak story is not rescued by styling |
| a technical failure outside the edit | `run.py exception` with the next action |

Each short gets at most one targeted fix. A second weak candidate is shipped
if all five answers are yes, otherwise killed or recorded as an exception.
Killing a short is cheaper and better than a rescue; every selected short
still appears in the manifest.

### Fix packet (at most 5 lines)

```text
s07 fix · EDL v14
18.2-20.4s: two seconds of talking head with captions only; the turn "ten million" has no beat.
Change: counter 0 -> 10M on "ten million" (cue 18.6, silent), punch 1.15x at 18.6.
Keep: everything else, especially the hook and payoff.
```

Name the timestamp, the effect on the viewer, the exact change and what not
to touch. Never send a list of nitpicks or a re-edit request, and never ask
for more sound than the Look's ceiling; removing a sound that nothing on
screen earns is always a fair fix.

### Golden traits per Look

| Look | Ship only if |
| --- | --- |
| Headline Pro | big face on a designed card, a still headline, captions that perform, the payoff lands with sound |
| Editorial Serif | a quiet field, serif contrast on the right word, depth behind the subject |
| Kinetic Poster | type lands on almost every stressed word (sound only on the few that earn it); the payoff is the biggest event |
| Cinematic Doc | graded, textured, always drifting; archive feels like film |
| Mono Noir | stark monochrome, red only where it matters, a few sounds that hit with weight |
| Clean Data | numbers become pictures, and a settled figure lands with one sound |
| Creator Glow | glowing words build beside the face; the hook reads before the sentence ends |

### Batch view

After each batch, look across the shipped shorts: the same Look on most
stories, the same hero device in every short, or identical openings are batch
problems. Fix them in the next briefs rather than reopening shipped shorts.

## Finals

After `export_final` completes and the file is downloaded, watch the run's
first final in full: the native corner mark is legible and clear of faces and
type, the complete 5 s native Valmera ending is present, the audio plays
through, and the duration matches. Record it with `run.py export
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
references means 8 or more. **S** rewards sounds that are placed, sparse and
earned, never their count; **M** is scored only when the owner supplied a
song. These scores are for calibration and owner
conversations, never a ship gate or a number to stamp on every short.
