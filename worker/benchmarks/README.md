# Finished-edit quality benchmarks

Valmera quality changes should be judged on completed renders from the same
source and brief, not tool counts or render success. A useful case contains:

- a stable source clip and exact brief;
- the current Valmera render and a human/reference render;
- assembled transcripts for both outputs plus enough source transcript to
  detect broken context;
- a blind human pairwise label (`left`, `right`, `tie`, or `insufficient`).

Prepare bounded, equivalent visual/audio evidence:

```bash
PYTHONPATH=worker venv/bin/python worker/benchmark_runner.py prepare \
  worker/benchmarks/manifest.example.json /tmp/valmera-benchmark-evidence \
  --prepared /tmp/valmera-benchmark-prepared.json
```

On a worker configured with the vision/audio/text reviewers, run the blinded
two-order comparison:

```bash
PYTHONPATH=worker venv/bin/python worker/benchmark_runner.py evaluate \
  /tmp/valmera-benchmark-prepared.json \
  --results /tmp/valmera-benchmark-results.json
```

The evaluator reports visual, story, and audio channels separately and turns
left/right order disagreement into `insufficient`. It never combines them into
a synthetic quality score. Human judgments remain visible beside model
evidence and are never disclosed to the judge.

For release decisions, keep at least these editorial families separate:
`podcast_conversation`, `talking_head_social`, `narrative_vlog`,
`voiceover_montage`, `product_promo`, `music_montage`, `tutorial_screen`, and
`mixed_other`. A build should not ship merely because aggregate wins hide a
regression in one family.
