# Story selection and briefs

The coordinator chooses the stories personally. Valmera and the editors do
not choose, merge or rewrite arcs. Select every moment that clears the bar
and only those: a weak story cannot be styled into a good one, and a good
one left out is a short the owner will miss. Oct 2026: on a 2 h 41 min Elon
Musk episode the owner watched and loved, a count rule picked 10 (6 finals)
and parked 32 moments, many for batch reasons ("beyond the bar for this
batch", "third manufacturing story"); its second most-replayed stretch (why
he bought X, about 25:00) was never even listed, and the pizza exchange the
top comments quote (39:06-40:02) was parked as "chatter". A 1 h 54 min
episode selected without a count gave 37.

## Full scan, not a skim

- Walk the whole transcript in order, chapter by chapter (YouTube chapters,
  else the topic changes you find), with `get_transcript` or `get_words`
  pages until it is exhausted: never search hits, never a skim. In each
  chapter list EVERY self-contained moment worth a short: a story, a
  striking claim, a surprising number, a funny or emotional exchange, a
  strong opinion, a vivid explanation. Write each to
  `selection/candidates.md` as you find it (range, one line), so nothing
  found is forgotten.
- Use `get_shots` for camera changes (its shots carry no visual
  description: look). A whole-podcast `get_editorial_map(focus='peaks')`
  lists nearly every sentence and truncates; use it on a candidate's range,
  or skip it. Batch `look_at(times=[...])` for the faces and settings of
  serious candidates.
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

## Audience signal (YouTube sources)

Once, while the source indexes (about 30 s, nothing is downloaded):

```bash
yt-dlp --skip-download --dump-json --write-comments \
  --extractor-args "youtube:comment_sort=top;max_comments=100,all,0" \
  '<url>' > selection/source-info.json
```

(`yt-dlp` not on PATH: `.valmera/venv-worker/bin/yt-dlp` under
`/Users/masaoodi/Documents/Valmera`.) If it fails, run it again without the
two comment flags. Then save `selection/audience.json` with:

- **Most replayed:** `heatmap` is about 100 windows with `value` 0-1 (1 =
  the most replayed). Take the ten highest, skipping the first window
  (everyone starts there) and sponsor reads, and merge neighbours.
- **Chapters:** `chapters`, else the timestamps in `description` or in a top
  comment that lists them.
- **Timestamped top comments:** a comment quoting a time ("39:06 the pizza
  clip", 154 likes) marks a moment people came back for.

Map every high-replay window and quoted timestamp to a candidate in the
scan. A high-replay moment you do not select needs a valid reason (below)
that names its window. No heatmap (not on YouTube, too few views): write
"no audience signal" and rely on the scan. The signal adds and checks
candidates; it never replaces grading.

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

## Grade, select, challenge

Grade every moment on its own against a fixed bar, never against the other
candidates or the slate so far (the fifth moment on manufacturing is
graded exactly like the first):

- **hook** 0-5: would a viewer stop scrolling in the first 2-3 s?
- **payoff** 0-5: does it resolve, so they finish it?
- **inspiration** 0-5: do they leave with a laugh, a feeling or something
  learned?
- **visual** 0-3, for ordering only: a concrete noun, number, contrast or
  action a Look can show.

3 is a plain yes from a viewer who has never heard of the speaker. A moment
clears the bar when hook, payoff and inspiration are each 3 or more.

- **Hero tier:** every moment that clears the bar. The count follows the
  podcast, never a number: no target, minimum, maximum or rate. Never pad
  (if five clear the bar, make five) and never drop one that clears it (if
  60 do, make 60). Different moments on one theme are different shorts.
- **Standard tier (optional):** only when the run brief asks for volume:
  good moments just below the bar, each with `apply_look` + captions + 1-2
  designed beats, one render, and a spot-check review.
- **A weak boundary is a re-cut, not a reason.** A host interjection, a
  slow start or a tail that runs on gets a later start, an earlier end or a
  tighter window (Durations) before it counts against the moment.

### Valid reasons to leave a moment out

Only these six, as `story (start-end s): <reason>: <the specific fact>` in
the not-selected list passed to `run.py finalize --not-selected`:

1. **no arc**: no standalone arc even after trimming (name what is missing:
   the context the start needs, the payoff that never comes);
2. **duplicate**: the same lines as another moment (repeated or retold
   nearly word for word); keep the stronger complete version;
3. **sponsor read**;
4. **private individual**: defamation of, or insinuation about, a private
   person;
5. **unfaithful**: a medical or other claim that cannot be shown
   faithfully in the clip (its qualifier or context does not fit);
6. **footage**: footage you cannot use (no usable picture or sound, a third
   party's video on screen, a frame no layout brings to the floor).

**Never valid:** quota or batch logic ("beyond the bar for this batch",
"already have N on this theme", "overlaps another short's topic",
"third story on X"); "host-led" (a great exchange with the host is a
short); "chatter" or "low stakes" for a moment that is actually funny or
revealing (a laugh is a payoff); vague "brand risk" for a fair, faithfully
quoted opinion; anything a re-cut fixes.

### Challenge pass (before make_shorts)

Re-read the not-selected list with fresh eyes, as the owner who watched the
whole podcast: for each line ask "would I miss this?". Overturn every
reason that is not one of the six, or that a re-cut fixes, and every
high-replay window or comment-quoted moment left out without a valid
reason; grade the overturned moments against the bar and select the ones
that clear it. In a multi-agent run a separate critic does this pass; a
single coordinator does it as an explicit second pass. Save
`selection/challenge.md`: each overturned moment with its new grade, and
each upheld line with its reason number. Only then call `make_shorts`.

**Kills are expensive.** A moment that cleared this bar is not killed in
review for a fixable craft reason (a measured floor, the frame, a headline,
a beat gap, a boundary): it gets its fix, as many passes as it has
concrete defects (review.md). Kill only a story that fails on its own
(attention is no with the name hidden) or one where review finds one of the
six reasons.

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
payoff) and `score` = `round(100 * (hook + payoff + inspiration + visual) /
18)` (0-100, higher is better; never the rank, which would invert the
order). The tool
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

1. `get_edl` the child: it starts 9:16 from the SOURCE LAYOUT (the
   make_shorts reply and `get_video_info` name its call window, self-view
   and largest card at <= 2x with and without a headline band).
2. Pick the layout from looks.md **Card geometry** that the source fit and
   the SOURCE LAYOUT allow, and set it in the order editing.md requires:
   the card (`set_picture_card`; on a call source `source='auto'` frames
   the call's picture at <= 2x, keeps the face whole and names the
   `erase_region` for the host's self-view); then that `erase_region` (box
   fill, the inset's border included) on a picture-in-picture or call UI
   that would sit inside the card; then the headline
   (`add_motion_graphic(template='headline')`, whose write measures the
   claim against the captions and names the fix, or a hook-tier
   `word_slam`) with the speaker's real headline text.
3. `render_preview(quality='approval')`, then one rendered `look_at` at
   ~1 s and mid-clip and `native_resolution` frames at ~1 s and the payoff
   in one call. Read the render's MEASURES line (picture area and upscale,
   the headline's and captions' cap heights) and PICTURE CHECK (a face cut
   by the card edge, or at a call window's edge as a source limit); check
   the face fully inside the card and the erase residue by eye.
4. `export_final` on that version once and `wait_for_job`: proof that a
   layout with motion graphics exports on today's deployment (Oct 2026: it
   did not, and every edit of the run was unexportable).
5. Save `selection/framing.json`: layout name, `box`, card `source` rect,
   erase regions (source fractions; editors re-run them on their own child
   windows), headline band, caption `anchor_y`, the measured numbers, and
   the layouts that failed and why. Every brief copies it as `framing`.

Decide from the measurement, not from the table: if the floor (0.54, or
0.48 Editorial Serif) or a headline claim at 1.2x the captions is out of
reach with a headline band (the headline write's FIX names the card top it
needs and the card that leaves), use the no-band layout (a hook-tier
`word_slam` carries the hook and the name) and assign Looks and structures
that work without a persistent band. If no layout reaches the floor at the
2x upscale cap for a moment, leave out only that moment (reason 6,
footage, naming the frame); every other moment that clears the bar stays. If the render or the export fails on a Valmera
defect, use the next layout that works, note the defect for the end-of-run
report and carry on (SKILL.md, **Defects: keep going, report at the end**).

## Headlines

`Name: claim`, using the verified speaker and a faithful paraphrase of the
clip's strongest line or statistic, at most about 60 characters (the
publishing title and `make_shorts` title). The on-screen text is shorter:
a persistent headline band fits two lines, and over the 0.1 band its main
line reaches 1.2x caption size only at about 36 characters or fewer (the
write measures it), so give the brief an `onscreen_headline` of at most 36
characters (the claim alone when the name sits in the hook's kicker or a
short kicker beside the corner mark). Where the headline's wording comes from outside
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
