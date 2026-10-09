# Story selection and briefs

The coordinator chooses the stories personally. Valmera and the editors do
not choose, merge or rewrite arcs. Select fewer, stronger shorts: a hero short
gets full motion design, and a weak story cannot be styled into a good one.

## Read the source once

- Read the whole transcript with `get_transcript` (or `get_words` pages)
  until it is exhausted, not just search hits. Use `get_shots` and one
  `get_editorial_map` pass (`focus='story'` or `'peaks'`) for camera changes
  and energy. Batch `look_at(times=[...])` for the faces and settings of
  serious candidates.
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
then wait a few minutes outside Valmera before the next check. Register each
child with `run.py add-short` (tier, `--rank` 1 = best, `--score`, speaker,
source range).

## Headlines

`Name: claim`, using the verified speaker and a faithful paraphrase of the
claim, at most about 60 characters. It poses the tension and never gives
away the payoff ("Elon Musk: What is money actually worth?", not the answer).
No dates, venues or metadata sub-lines. No first-person testimony the speaker
did not give.

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
  "look": "kinetic-poster",
  "structure": "fast-conversation",
  "tier": "hero",
  "music": "inherit",
  "speaker": "Steve Jobs",
  "headline": "Steve Jobs: Computer fonts have been garbage",
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
    {"role": "turn", "cue": "same width", "source_s": 1321.8, "move": "W-R-I-T-I-N-G mono cells, silent"},
    {"role": "payoff", "cue": "real typefaces", "source_s": 1340.2, "move": "typeface cycle 0.3 s each, riser_2 into impact_1 on settle"}
  ],
  "brief": "About 150 words of art direction ..."
}
```

`story` with all four fields, `look`, `structure` and `brief` are required;
the script rejects a brief over 250 words. `music` is `inherit` (the default:
follow the run, which is off unless the owner supplied a song) or `off` to
keep this short dry; `on` is accepted only when the run records the owner's
song. Never choose a track in a brief. `run.py assign` prints the resulting
`music_effective` and `music_song`, which the editor receives with the
brief. Editors convert source cues to output time after
their cuts.
