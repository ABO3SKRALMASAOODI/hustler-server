# Story selection and briefs

The coordinator chooses the stories personally. Valmera and the editors do
not choose, merge or rewrite arcs. Select fewer, stronger shorts: a hero short
gets full motion design, and a weak story cannot be styled into a good one.

## Read the source once

- Read the whole transcript with `get_transcript` (or `get_words` pages)
  until it is exhausted, not just search hits. Use `get_shots` for camera
  changes (its shots carry no visual description: look). A whole-podcast
  `get_editorial_map(focus='peaks')` lists nearly every sentence and
  truncates; use it on a candidate's range, or skip it. Batch
  `look_at(times=[...])` for the faces and settings of serious candidates.
- **Source fit first.** One batched `look_at` on 3-4 source times answers
  what the layouts can be: where the speaker's face is, how many source
  pixels it has (a face crop needing more than 2x for full-bleed goes on a
  card), and what must stay out of frame (a call window's picture-in-picture,
  call buttons, a burned-in screen, channel logos). Write that down; the
  framing pilot proves it.
- While the source is still indexing, you may draft candidates from the
  platform's captions; verify every boundary against `get_words` before
  `make_shorts`.
- Verify the speakers from the source title, description, introductions or
  on-screen names. If identity is uncertain, leave names out. Never carry a
  name, fact or headline from a reference or an earlier run.

## What makes a story

Choose the moment before its effect. A strong short changes what the viewer
understands or feels: a surprising answer, an earned reversal, a useful
principle with evidence, a revealing admission, a clear disagreement, or a
specific story with stakes. Celebrity recognition alone is not the payoff.

For each candidate write four short sentences: **viewer question, immediate
hook, turn or evidence, final payoff**. Then check:

- The range is contiguous and chronological; the editor may tighten inside it
  but never joins distant sections or splices a new claim.
- The start is understandable on its own: no "yes", "because" or "it"
  openings, and names, pronouns and stakes resolve. If the first ten seconds
  are routine interviewer setup, find a start closer to the answer; keep a
  necessary question or qualification.
- The payoff resolves the question, does not merely restate the hook, and is
  at least as strong as anything after it. Stop when the thought resolves.
- **The ends are editable.** The start is a whole first word that does not
  run on from the previous one (a pause before it in `get_words`), on a
  frame with the speaker's eyes open (look at it). The end leaves 0.8-1.5 s
  before the next spoken word, so the payoff holds on its natural tail: on a
  card layout Valmera cannot freeze the composed frame (Oct 2026: s02, s03
  and s05 spent renders and slow-motion hacks on it). When the payoff's last
  word runs straight into more speech, take a later sentence end or note
  "tail: slow last 0.4 s at 0.5x" in the brief.
- Qualifiers stay. Never turn a cautious statement into certainty.
- "Interesting topic", "famous person" and "complete sentence" are not reasons.
  Name the actual tension, image or insight.

## Rank, cap and tier

Score every candidate: hook strength 0-5, payoff strength 0-5, and visual
potential 0-3 as a tie-breaker (a concrete noun, number, contrast or action a
Look can show). Compare overlapping or near-duplicate candidates and keep the
stronger complete version.

- **Hero tier:** the top 8-12 by hook + payoff (the state script caps hero
  shorts at 12). Never pad: if only five stories clear the bar, make five.
  Older saved briefs that say "no maximum" are superseded on this point:
  more strong stories than 12 go to the standard tier only when the run
  asks for volume, otherwise to the not-selected list with the reason
  "beyond the hero cap".
- **Standard tier (optional):** only when the run brief asks for volume. Up to
  8 more, each with `apply_look` + captions + 1-2 designed beats, one render,
  and a spot-check review.
- Everything else goes in a not-selected list with one line each
  (`story (start-end s): reason`), passed to `run.py finalize --not-selected`.

## Durations

The final is 15-45 s including the 5 s native ending, so the editorial
program is 10-40 s and a montage's editorial program at most 25 s. Choose
source windows of about 20-55 s that tighten to that length without splicing
meaning. `make_shorts` accepts clips of 10-120 s.

## Materialize once

One `make_shorts(project_id=<parent>, clips=[...])` call with, per clip:
`start` on the first word of a sentence and `end` just after the payoff's
final word (both from `get_words`), `title` (the verified speaker-first
headline, which becomes the card title), `hook`, `story` (setup, development,
payoff) and `score` = `round(100 * (hook + payoff + visual) / 13)` (0-100,
higher is better; never the rank, which would invert the order). The tool
rejects the first clip that starts or ends mid-sentence; check every boundary
before calling, and on a rejection fix only that clip and resubmit.

Building the children takes about 4-7 minutes. Do not poll: write the briefs
meanwhile (they need no child ids), then call `shorts_status(parent)` once.
If the children are not there yet, `wait_for_job(job_id)` at most 3 times,
then wait a few minutes outside Valmera before the next check. Add each
child's `child_project_id`, `rank` (1 = best), `score`, `speaker`, `tier`
and `source_window_s` to its assignment JSON and register the whole slate
with one `run.py register --assignments <run>/assignments`.

## Framing pilot (once per source, before the briefs are final)

Every short of a podcast shares one camera geometry, so its frame is solved
once, by the coordinator, on the rank-1 child — not nine times by nine
editors (Oct 2026: each editor spent 10-30 calls re-solving the same phone
call window and 8 of 9 still ended below the picture floor). Skip it only
when every short is full-bleed on a clean modern source. About 15 minutes:

1. `get_edl` the child; `set_frame(ratio='9:16')` if `frame` is null
   (children can arrive 16:9, and a first render on them is wasted).
2. Pick the layout from looks.md **Card geometry** that the source fit
   allows, and set it in the order editing.md requires: the card with its
   source rect through `apply_edit_batch` (effects layer) BEFORE any erase
   patch, stock overlay or motion graphic exists; then `erase_region` (box
   fill) on a picture-in-picture or call UI that would sit inside the card;
   then the headline (`add_motion_graphic(template='headline')` or a
   hook-tier `word_slam`) with the speaker's real headline text.
3. `render_preview(quality='approval')`, then one rendered `look_at` at
   ~1 s and mid-clip and one `native_resolution` frame at ~1 s. Measure:
   picture area (card width x height), the face fully inside the card,
   upscale (from the result), the headline main line's cap height against
   the caption cap height, erase residue.
4. `export_final` on that version once and `wait_for_job`: proof that a
   layout with motion graphics exports on today's deployment (Oct 2026: it
   did not, and every edit of the run was unexportable).
5. Save `selection/framing.json`: layout name, `box`, card `source` rect,
   erase regions (source fractions; editors re-run them on their own child
   windows), headline band, caption `anchor_y`, the measured numbers, and
   the layouts that failed and why. Every brief copies it as `framing`.

Decide from the measurement, not from the table: if the floor (0.54, or
0.48 Editorial Serif) or a headline at least as large as the captions is
out of reach with a headline band, use the no-band layout (a hook-tier
`word_slam` carries the hook and the name) and assign Looks and structures
that work without a persistent band. If no layout reaches the floor at the
2x upscale cap, the source is not premium material: pick fewer shorts from
its best-framed moments, or stop and report it. If the render or the export
fails on a Valmera defect, stop the line (SKILL.md).

## Headlines

`Name: claim`, using the verified speaker and a faithful paraphrase of the
clip's strongest line or statistic, at most about 60 characters (the
publishing title and `make_shorts` title). The on-screen text is shorter:
a persistent headline band fits two lines, and its main line reaches
caption size only at about 36 characters or fewer, so give the brief an
`onscreen_headline` of at most 36 characters (the claim alone when the name
sits in the hook's kicker). Where the headline's wording comes from outside
the clip (the host's question), quote that context line with its source
time in the brief, so review need not search for it. It is
payoff-led: a specific claim the ending completes ("Peter Thiel: They
promised us flying cars…" for a clip that ends on "…all we got was 140
characters"), never a generic question ("Where did progress go?"), never
the punchline itself, and never the hero word a later graphic slams (a
headline that says "garbage" spends the moment the speaker says it). No
dates, venues or metadata sub-lines. No first-person testimony the speaker
did not give.

While reading, note for each candidate what the editor can SHOW rather than
re-type: a number, a contrast, a named product or place (with what it is:
"Apple Lisa, 1983"), a spoken list whose items have real images. The brief's
beats name those, so every graphic earns its place.

## The brief (one JSON per short)

Assign a Look and a structure ([looks.md](looks.md)) from the story, not from
editor convenience, and record why and the closest alternative. Write the art
direction in about 150 words: the one visual idea that makes the turn and
payoff visible, the hero words with their source-time cues, the evidence or
B-roll you expect (with rights), and anything the editor must avoid. Save it
as `assignments/<short_id>.json` and record it with `run.py assign --brief`.
The example shows the format only; never reuse its copy, cues or claims.

```json
{
  "short_id": "s07",
  "child_project_id": 3448,
  "rank": 7,
  "score": 85,
  "source_window_s": [1305.9, 1342.0],
  "look": "kinetic-poster",
  "structure": "fast-conversation",
  "tier": "hero",
  "music": "inherit",
  "speaker": "Steve Jobs",
  "headline": "Steve Jobs: Every computer has used weird type",
  "onscreen_headline": "Every computer used *weird type*",
  "framing": "selection/framing.json: no-band 4:5 card, box [0.095,0.13,0.905,0.795], source rect [...], erase [...]; measured area 0.54, 1.9x",
  "structure_reason": "he names concrete typefaces the viewer can see",
  "closest_alternative": "headline-pro: his delivery is strong but the fonts are the point",
  "story": {
    "viewer_question": "Why did early computer text look so bad?",
    "hook": "Computer fonts have been garbage.",
    "turn": "Each letter was forced into the same width cell.",
    "payoff": "The Mac would ship with real typefaces."
  },
  "beats": [
    {"role": "hook", "cue": "garbage", "source_s": 1312.4, "move": "word_slam serif, silent"},
    {"role": "turn", "cue": "same width", "source_s": 1321.8, "move": "W-R-I-T-I-N-G mono cells, silent (shows what the words can't)"},
    {"role": "hero", "cue": "30, 40 fonts", "source_s": 1330.6, "move": "counter '30–40' fonts, each figure on its word, silent"},
    {"role": "payoff", "cue": "real typefaces", "source_s": 1340.2, "move": "typeface cycle 0.3 s each, held 0.8 s after the last word; silent unless the landing earns one sound"}
  ],
  "brief": "About 150 words of art direction ..."
}
```

`story` with all four fields, `look`, `structure` and `brief` are required;
the script rejects a brief over 250 words. **Beat coverage is planned here:**
the timed beats (`source_s`) leave no gap over 12 s of source from the
window start to the payoff (about 6-8 s of program after tightening); the
script names the uncovered span and rejects it unless `beat_gap_reason`
says the span is cut. Oct 2026: 8 of 9 briefs left 13-27 s gaps (often the
setup right after the hook) and 5 shorts were killed for exactly those
stretches. A thesis line, a number, a name, a list, a contrast, an image
of the noun said: one of them is in every 12 s. Copy the pilot's
`framing` into the brief, and use its layout's numbers, not the table's.
`music` is `inherit` (the default:
follow the run, which is off unless the owner supplied a song) or `off` to
keep this short dry; `on` is accepted only when the run records the owner's
song. Never choose a track in a brief. `run.py assign` prints the resulting
`music_effective` and `music_song`, which the editor receives with the
brief. Editors convert source cues to output time after
their cuts.
