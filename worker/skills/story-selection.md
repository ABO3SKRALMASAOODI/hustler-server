# story-selection — choosing the moment before its effect: a full scan of a long talk, the hook / turn / payoff test, a fixed bar graded per moment, editable ends with a live tail, faithful ranges, and how many shorts (as many as clear the bar)

## Editorial decision principles

A weak story cannot be styled into a good one, and a good one left out is
a short the user will miss. Choose the moment first; the look, captions
and graphics only make its hook, turn and payoff visible.

A strong moment changes what the viewer understands or feels: a surprising
answer, an earned reversal, a useful principle with evidence, a revealing
admission, a clear disagreement, a specific story with stakes, or a laugh.
"Interesting topic", "famous person" and "a complete sentence" are not
reasons; name the actual tension, image or insight.

The count follows the footage, never a number: make every moment that
clears the bar (five if five do, thirty if thirty do), unless the user
asked for a number, then the strongest that many.

Faithfulness is part of the bar. The range is contiguous and in source
order: tighten inside it, never join distant sections or splice a new
claim. Qualifiers stay ("I think", "probably", "not always"); a cautious
statement never becomes certainty, and a negation is never cut off its
sentence.

## Evidence to inspect

- The WHOLE transcript in order (`get_transcript` pages until it ends, or
  `get_words` for a range), chapter by chapter: never search hits, never a
  skim. Write each candidate down as you find it: range, one line.
- `get_shots` for camera changes and one batched `look_at` on the faces
  and settings of the serious candidates.
- `get_words` at every boundary: the first word starts after a pause, the
  last word's release, the gap after it.
- Speaker identity from introductions, the title or on-screen names; leave
  names out when unsure.

## Strong treatment patterns

THE FOUR-LINE TEST, per candidate: viewer question, immediate hook, turn or
evidence, final payoff. If one line is empty, the moment has no arc yet:
look for a later start or an earlier end before dropping it.

GRADE EACH MOMENT ON ITS OWN, against a fixed bar, never against the other
candidates: hook 0-5 (would a stranger stop scrolling in the first 2-3 s?),
payoff 0-5 (does it resolve, so they finish?), inspiration 0-5 (a laugh, a
feeling or something learned). A moment clears the bar when all three are
3 or more. Order the selected moments by those grades.

VALID REASONS TO LEAVE A MOMENT OUT (only these): no arc even after
trimming; a duplicate of another moment's lines; a sponsor read; a claim
about a private person; a claim that cannot be shown faithfully in the
clip; footage that cannot be used. Never "already have one on this theme",
"host-led" for a good exchange, or "chatter" for something funny. A weak
boundary is a re-cut, not a reason.

EDITABLE ENDS:
- The start is a whole first word that does not run on from the previous
  one, on a frame with the speaker's eyes open, understandable on its own:
  no "yes", "because", "and" or "it" openings; names, pronouns and stakes
  resolve. Routine interviewer setup is skipped unless the answer needs it.
- The payoff resolves the question, is at least as strong as anything
  after it, and stops when the thought resolves.
- The end leaves about 0.6-1.2 s of LIVE footage after the payoff word (a
  reaction, a nod, a breath) before the end card. When speech runs straight
  on, take a later sentence end, or end on the last word. Never plan a
  freeze frame or a slowed tail: it reads as the video stalling.

DURATIONS: the final is 15-45 s including the 5 s end card, so the edited
programme is 10-40 s; choose source windows of about 20-55 s that tighten
to that without splicing meaning.

THE HEADLINE for each moment: `Name: claim`, from the clip's strongest line
or statistic, payoff-led (the ending completes it), at most about 60
characters; never a generic question, never the punchline itself, never a
word a later graphic slams.

MATERIALIZE ONCE: one `make_shorts` call with every selected moment: start
on a sentence's first word and end just after the payoff word (both from
`get_words`), a verified title, the hook, the story (setup, development,
payoff) and a 0-100 score from the grades.

For visual highlights (sport, gameplay, a product shown), the same test
applies to the action: a build-up the viewer can read, the decisive
moment, and its resolution; find them with `find_visual_moments` and
confirm with one batched `look_at`.

## Common failure modes

- Skimming (search hits, the first chapter) and missing the best moment.
- Picking by fame or topic instead of hook, turn and payoff.
- A start that needs context the clip does not give; an ending that trails
  into the next question; a payoff weaker than the line after it.
- Splicing distant sentences into a claim the speaker did not make;
  dropping a qualifier or a negation.
- A fixed quota of shorts, or dropping a good moment because its theme
  repeats.

## Verification procedure

Before `make_shorts`: re-read the left-out list as the user who watched
the whole talk ("would I miss this?") and overturn every reason that is
not one of the valid ones. After the cut: watch the first 3 s and the last
2 s of each short in rendered frames (`look_at(rendered=true, ...)`) and
read `check_speech` at both ends: a clean first word, the payoff complete,
live footage after it.

## Repair ladder

A later start or an earlier end → a tighter window inside the range → a
different sentence boundary from `get_words` → drop the moment only for one
of the valid reasons, naming the fact.
