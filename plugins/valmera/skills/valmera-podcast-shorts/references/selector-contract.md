# Parent Story Selection Contract

## Contents

- Inspect all evidence
- Select complete micro-stories
- Return strict JSON only

The coordinator must personally author and approve the story selection for one
Valmera Shorts parent. Valmera and child editors may not choose, rank, merge, or
rewrite the arcs. Read-only helpers may collect evidence, but their candidates
are non-authoritative until the coordinator verifies them against the source.
Helpers must not call Valmera directly or concurrently. The coordinator obtains
each evidence page under the single global request lease and may give helpers
only already-returned immutable transcript, shot, and visual-inspection evidence.

## Inspect all evidence

1. Read the source identity, duration, hash/provenance record, and index state.
2. Read the shot map across the complete source to cursor/section exhaustion.
   Inspect timestamped frames at the start, middle, and end of every shot plus
   serious-candidate boundaries and internal beats. Record exact sampled times
   and evidence gaps; do not claim continuous native-video perception.
3. Process the full source audio with ASR and obtain all available platform
   captions,
   then page through that transcript from start to finish. Do not stop after
   search hits. Continue until the cursor/section reports exhaustion and record
   complete processing/source coverage plus warnings. ASR output is fallible
   text/timing evidence; zero or missing words never proves no speech, and it is
   not evidence of human-experienced delivery or mix quality.
4. Reinspect every serious candidate using frames at gaps of at most 1.0 second
   across that candidate plus its shot boundaries, and reread its
   exact transcript. Include the nearby question/premise when the answer depends
   on it.

## Select complete micro-stories

Choose contiguous, chronological source ranges. Do not synthesize one short by
joining distant podcast sections. The later editor may tighten within the
selected range but must not invent a new argument from unrelated quotes.

Every accepted range must contain:

- an immediately understandable authentic hook or premise;
- enough context to resolve names, pronouns, and stakes;
- development that changes, deepens, or escalates the idea;
- a payoff such as a reveal, lesson, consequence, resolution, or punchline;
- a final sentence at least as strong as everything after it.

Reject a catchy quotation when it lacks setup, development, or payoff. Reject
ranges beginning with contextless words such as “yes,” “because,” or “it.”
Reject ranges that stop before the thought resolves.

Use the server limits: 10-120 seconds and no overlaps. Target 45-75 seconds
when the complete arc permits it. Select every distinct arc that genuinely
passes the quality threshold. Do not impose an arbitrary total count and do
not fill a quota with weak material. The finite source, non-overlap rule, and
minimum duration provide the natural upper bound. Reject near-duplicate clips
that deliver substantially the same premise and payoff even when their source
ranges differ.

Score each candidate from 0-100:

- hook and immediate clarity: 0-20;
- self-contained context: 0-20;
- development/escalation: 0-20;
- payoff/power ending: 0-20;
- emotional, intellectual, or entertainment value: 0-20.

Accept only scores of 80 or higher.

Freeze the complete approved selection before style-reference research. Compute
and retain its fingerprint with
`scripts/canonical_fingerprint.py --kind selection --input <selection.json>`.
The helper removes only the self-referential fingerprint field, sorts object
keys, preserves list order, normalizes equivalent JSON numbers, encodes UTF-8,
and returns `sha256:<64 lowercase hex>`. Do not hash an ad-hoc summary or a
different field projection on resume.

## Return strict JSON only

The complete artifact must validate against `schemas/selection.schema.json`
and the semantic selection checks in `scripts/validate_contract.py`. Persist
the schema's separate complete visual-coverage, transcript-coverage, and shot-
coverage evidence; do not substitute an unmuted-player boolean or a prose
summary for any of them.

When nothing passes, return an empty `clips` array, set `abstained` to true,
and explain why. Do not lower the threshold. `coordinator_approved` still means
the empty result was reviewed, not that clips exist.
