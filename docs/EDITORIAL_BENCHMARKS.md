# Editorial benchmark and release gate

Valmera's release benchmark compares two finished edits of the same source and
brief. It does not score tool count, effect density, or render success as
quality. Visual, story, audio, human preference, latency, cost, and corrective
turns remain separate evidence lanes so one strong dimension cannot hide a
regression in another.

## Corpus manifest

Each case must identify which blinded side is the candidate and what it is
being compared with. Paths are relative to the manifest unless absolute.

```json
{
  "cases": [
    {
      "id": "founder-01-vs-previous",
      "family": "talking_head_social",
      "brief": "A credible 35-second founder reel with a clear proof beat",
      "source_context_path": "founder-01/source.txt",
      "candidate_side": "left",
      "opponent_kind": "previous_build",
      "human_winner": "left",
      "left": {
        "video_path": "founder-01/candidate.mp4",
        "story_text_path": "founder-01/candidate.txt",
        "build_id": "candidate-sha",
        "metrics": {
          "wall_time_s": 210.4,
          "model_cost_usd": 0.42,
          "corrective_turns": 0,
          "agent_dispatches": 9,
          "edl_versions": 3,
          "complete_previews": 1,
          "review_pass_reopens": 0,
          "department_execution_gaps": 0,
          "motion_contract_gaps": 0
        }
      },
      "right": {
        "video_path": "founder-01/previous.mp4",
        "story_text_path": "founder-01/previous.txt",
        "build_id": "previous-sha",
        "metrics": {
          "wall_time_s": 430.1,
          "model_cost_usd": 0.83,
          "corrective_turns": 1,
          "agent_dispatches": 22,
          "edl_versions": 8,
          "complete_previews": 4,
          "review_pass_reopens": 2,
          "department_execution_gaps": 0,
          "motion_contract_gaps": 0
        }
      }
    }
  ]
}
```

Use separate cases for candidate-versus-previous-build and
candidate-versus-human-reference. `candidate_side` is copied into the result
only after blinded evaluation. Human labels never enter model prompts.

## Run it

From the repository root:

```bash
venv/bin/python worker/benchmark_runner.py prepare \
  benchmarks/manifest.json benchmarks/evidence \
  --prepared benchmarks/prepared.json

venv/bin/python worker/benchmark_runner.py evaluate \
  benchmarks/prepared.json --results benchmarks/results.json

venv/bin/python worker/benchmark_runner.py gate \
  benchmarks/results.json \
  --policy worker/benchmark_policy.example.json \
  --report benchmarks/gate-report.json
```

`run` combines `prepare` and `evaluate`. Evaluation uses configured visual,
text, and audio model lanes and therefore has model cost. Preparation gives
both sides equal output-time visual sampling and opening/body/ending audio
windows; it does not expose a Valmera EDL to the judge.

The gate exits `0` only when every policy invariant passes and `2` otherwise.
It fails closed on missing families, channels, craft dimensions, candidate
identity, required opponent identity, human labels, and paired efficiency
measurements. A missing dimension is `insufficient`, never an inherited
overall win. Any candidate cost/latency increase from a zero baseline is an
explicit regression.

## Policy and interpretation

[`worker/benchmark_policy.example.json`](../worker/benchmark_policy.example.json)
is an intentionally demanding starting policy, not proof that Valmera already
meets it. It requires family-specific visual/story/audio evidence, separately
gates comparisons against the previous build and human references, and applies
paired p50/p95 efficiency limits.

The example policy also gates agent dispatches, EDL-version churn and complete
preview encodes independently. `review_pass_reopens` is the sum of visual and
audio pass-to-repair transitions within one edit and has an absolute ceiling
of zero: a candidate release is not stable while its own finishing system
repeatedly reverses clean verdicts. These fields come from the outcome
scorecard: `edl_versions=versions_written`,
`complete_previews=previews_rendered`, and `review_pass_reopens` is
`visual_passes_reopened + audio_passes_reopened`. Changed-section proof reels
are not counted as complete previews. `department_execution_gaps` is also
gated at an absolute zero: a candidate cannot pass by promising captions,
motion, B-roll, music, SFX or color in its treatment and silently omitting it
(or by retaining a layer it explicitly chose to omit). `preserve` remains a
valid non-promise, so this invariant measures honesty/execution rather than
effect density.

`motion_contract_gaps` is separately gated at zero. For a Blueprint v3
motion-authoring treatment, each measured sequence beat is bound to a
free-named motif or deliberate `hold`. The candidate fails when no authored
event carrying that exact motif id in the motif's declared renderer domain
overlaps that beat, or when an explicit motion event contradicts a mapped
hold. An unbound animation—or one bound to a different motif—cannot pass by
coincidental timing. Untimed/unmappable beats are reported as `not_judged`,
not gaps. This checks causal execution—not whether the movement is tasteful;
the blinded rendered visual lane remains the taste authority. Its screening
packet should contain representative motif-labeled ordered states spanning
pre-trigger, renderer path knots and post-settle, with the exact EDL target id.
Playback speed/interpolation smoothness remains `not_judged` from stills. A
path/trigger/settle repair is actionable only when it cites the visible time
and labeled target; structural motif success alone is never a visual pass.

The audio lane follows the same evidence rule without creating a sound quota.
For edits with designed audio, preserve exact program windows for the heard
preview excerpts, label the music/SFX/voiceover ids and purposes actually
overlapping each window, and map measured sequence beats onto the output
clock. The evaluator may prefer silence or no SFX. A repair is actionable only
when it cites an existing target id at a time inside a genuinely heard window;
an old cached clip with no retained program clock cannot authorize a timed
change. Include adversarial cases where an effect is audible and technically
clean but lands outside its promised beat, uses the wrong sonic character, or
destroys planned contrast.

The repository deliberately does not contain fabricated benchmark cases. A
release decision becomes meaningful only after building a versioned corpus of
real sources, strong human reference edits, truly blind curator labels, and
measured results from both builds. Keep source rights and participant privacy
documented outside the model-facing manifest.

## Broad-brief and multi-upload regression cases

The corpus must include vague, production-shaped briefs, not only carefully
specified benchmark prompts. In particular, preserve at least one project with
several uploaded clips and a request such as “make a fast, stunning,
high-energy Instagram reel.” The expected invariant is not one visual style:

- platform and energy words alone must not force `talking_head_social` or any
  other family;
- the candidate must inspect the relevant uploaded-media set story-wide before
  recording its treatment;
- every auxiliary timed beat must retain its exact storage key, CLIP clock and
  source-index IDs through final rendered screening;
- a weak/redundant upload may be unused, but no supplied comparison candidate
  may be silently omitted;
- compare general dispatches, `set_edit_plan` call count/rejections, per-asset
  look calls, EDL versions and complete previews against the previous build.
- verify that the plan accounts for every live department as `author`,
  `preserve` or `omit`, that the author/omit promises match the final EDL, and
  that an unfinished promise is caught before a complete readiness encode.
- when motion is authored, verify that measured story beats bind to a declared
  motion motif or `hold`, that each fulfilling event carries the matching
  motif provenance (not merely a time/domain overlap), and that the final EDL
  has zero beat-level `motion_contract_gaps` rather than merely containing one
  animation somewhere;
- confirm the blind visual packet includes ordered state evidence for a
  representative set of those bound events and that a deliberately bad path
  can fail visual judgment even while its causal motion contract passes;
- when audio is authored, verify that semantic beat anchors participate in
  bounded final-mix sampling, cached evidence retains exact windows, and an
  audible-but-misplaced SFX cannot pass merely because the file rendered.

Production metadata exposes `editorial_family_explicit`,
`format_cast_confidence`, `format_cast_abstained`,
`uploaded_media_comparisons`, `uploaded_media_assets_compared`,
`uploaded_media_assets_requested`, `uploaded_media_frames_compared` and
`uploaded_media_comparison_pages`, plus `treatment_judge_reviews`,
`treatment_judge_reviews_reused`, `treatment_judge_accepts`,
`treatment_judge_revisions` and `treatment_judge_abstentions`. These
are diagnostic lanes rather than quality scores. A release still needs blinded
visual/story/audio results and human labels; “one comparison call” is not a win
if the actual edit is worse, and a treatment-review accept is not a quality
label. Track whether grounded revisions reduce later EDL churn and corrective
turns without raising total cost or simply moving indecision earlier.
Include vague briefs on sources whose measured grammar is already obvious;
knowing “podcast” or “talking head” must not be mistaken for having chosen a
specific treatment.

Multi-upload speech cases must also include the assembled transcript from
audible inserted clips in program order. File-local source starts and playback
rates must map into the real output clock; muted clip dialogue must not be
presented to the story judge as audible. This catches a coherent main-source
selection that becomes nonsense after auxiliary interview or podcast clips are
joined. Adversarial cases must cut through the beginning, middle and end of a
sentence and prove that the reviewer receives only the literal surviving words
with cut-in/cut-off markers. Long podcast cases must preserve the two speakers
and exact left/right words at each shown assembled edit join even when the full
transcript is sampled. An actionable semantic repair must cite an evidence-
visible keep, inserted clip or assembled-join id at a valid output time; an
invented target, impossible clock or unseen sampled boundary is insufficient.
The packed story-review request must remain within its 30,000-character budget
without constraining the number of cuts that the EDL can represent.
