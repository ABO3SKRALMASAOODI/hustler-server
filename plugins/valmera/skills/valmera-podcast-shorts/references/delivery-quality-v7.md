# Source, typography, and delivery quality v7

Read before acquisition, caption design, and final approval. High production
quality is a standing requirement. The visual treatment is a creative decision
for the current story, audience, platform, and brief.

## Keep quality consistent and design flexible

Aim for premium, deliberate work: clear source detail, coherent composition,
well-crafted typography, readable captions, precise timing, and clean delivery.
Choose the font, palette, scale, weight, phrase length, line breaks, placement,
headline treatment, and motion together. Different shorts may use different
caption styles when the story and approved direction support them. Premium
quality does not mean a particular font, minimalism, more effects, bigger text,
or copying the last successful render.

Do not promote a one-off repair, reference, or renderer setting into a permanent
preset. A liked reference establishes useful quality evidence; identify which
traits fit this project instead of reproducing its exact recipe. Honor explicit
current requests such as spoken-word highlighting, while choosing its execution
for the brief. The September repair's font, gold color, scale, static sizing,
and canvas dimensions are historical choices, not defaults for future work.

The failure to prevent is accepting a weak source, inheriting an unintended
caption effect, or approving small previews without checking the real export.
Changing a preset alone does not solve those process failures.

## Preserve real picture detail

Before editing, establish the delivery format and a high-quality target suited
to the destination. Obtain the highest practical clean original, and check its
actual sharpness, compression, motion, and detail after each intended crop.
Choose measurable source/crop and export thresholds for this run; do not reuse
another video's dimensions as an aesthetic rule. Larger encoded dimensions,
higher bitrate, sharpening, or a successful render do not prove genuine detail.

Record format discovery, attempted acquisition routes, actual successful format,
media probe, and checksum in `source/`. An access failure for one advertised
rendition is not proof that only a low-quality source exists. Make bounded
repairs using other advertised compatible formats, a refreshed supported
extractor, or an authorized original; never bypass access controls. A proxy may
support selection but must not silently become the production master. Resolve
unacceptable source quality before multiplying the problem across a batch.

Valmera's native export canvas is constrained by the original project source.
An enlarged overlay or resized final cannot restore detail or raise that source's
native export ceiling. Repair the source/project lineage and rebuild derived
picture layers when necessary. Minimize avoidable resampling and re-encoding.

Record native crop coordinates and destination rectangles. Check each camera
shot, B-roll asset, and graphic at its actual delivered size. Loosen an excessive
crop, obtain a better asset, or make another composition choice when enlargement
exposes softness. Distinguish acquisition/encoding failures from detail missing
in the original recording. If a limitation cannot be resolved, disclose it and
record the user's accepted exception instead of calling it premium quality.

## Preserve one picture, speech, and caption clock

Treat source preparation as a timing change until verified. Joining compressed
clips, decoding frames for enhancement, changing frame rates, and remuxing audio
can preserve nominal duration while changing when content is presented. Keep
the original media and source-to-master time map; verify the prepared master
against them before upload. When assembling excerpts, normalize both streams
onto the intended timeline and verify joins rather than assuming stream-copy
concatenation or a new FPS label is timing-safe.

Check anchors near the beginning, middle, and end of the prepared source, and
on both sides of joins. A pilot from the beginning cannot rule out cumulative
drift in later stories. Compare picture timing, speech timing, and word timing
separately. Audio stream start/end metadata alone is insufficient: decoded
sample duration can differ from the presentation timeline when packets overlap
or have gaps. WAV extraction for timing evidence must honor those timestamps,
including delayed starts; simply packing decoded samples can shift every later
caption. Preserve this evidence with the source checksum.

For every actual final, verify that displayed phrases and any active-word
highlight match its own speech at the opening, across edits, and near the payoff.
Use synchronized playback when available or timestamped waveform/word-alignment
evidence against that exact file. Record the method, checked output intervals,
measured lead/lag where available, and evidence paths in the quality review.
Reading the same source transcript that generated the captions is not an
independent sync check. Still frames establish appearance, not synchronization;
never mark timing passed using only screenshots or copied pass labels.

If a common mismatch appears across a batch, compare original, prepared master,
transcription audio, preview, and final before choosing the repair. Distinguish
a constant offset from accumulating drift or a jump at an edit. Correct the
faulty stage and re-check affected timings; do not hide varying drift with one
global offset. Keep already delivered media intact unless the user asks for
revised deliverables. These checks do not prescribe any caption style.

## Design captions, then verify their execution

- **Readability:** choose clear letterforms, sufficient contrast and weight,
  useful spacing, natural phrase breaks, and reading time suited to the speech.
  Inspect dense and long phrases at phone size as well as native resolution.
  Avoid blurry glyphs, clipped strokes, cramped lines, and excessive outlines.
- **Hierarchy and composition:** balance captions with the face, headline,
  graphics, and important action. Maintain safe margins across camera changes;
  emphasize the intended information without hiding the speaker or creating
  competing layers. Do not solve poor layout by making words too small.
- **Accuracy and timing:** preserve meaning, punctuation, speaker changes,
  word boundaries, and complete endings. If spoken-word highlighting is
  requested, verify that it follows the actual word timings and remains legible.
- **Motion:** choose static or animated typography deliberately. Motion and
  size changes are allowed when they support the selected style and remain
  readable. Accidental bouncing, inconsistent scale/baselines, distracting
  movement, or animation that conceals words are defects. Set animation and
  emphasis explicitly so a renderer's implicit defaults cannot choose the style.
- **Coherence:** use a consistent visual language within each treatment. Vary
  styles for a reason; do not change colors or effects randomly to signal effort.

Inspect actual rendered transitions. A tool accepting a font, line-wrap option,
color, or animation setting does not prove that production implements it.
Legacy dynamic captions can animate words when animation is omitted; request
no animation when that is the chosen treatment, and inspect any intentional
animation just as carefully. Do not disable a requested highlight merely to
remove an unwanted effect. Use supported renderer capabilities and honest
word timing; configuration names and static screenshots are not enough.

## Record and enforce a per-run quality policy

Every new production run writes `taste/quality-policy.json` and supplies it to
`run_state.py init --quality-policy <absolute-policy-path>`. The existing
`delivery-quality-v1` schema uses these configurable fields:

- `min_final_dimensions` and `target_final_dimensions`: width/height pairs chosen
  for this destination and aspect ratio. The minimum is a floor, not a target.
- `min_native_short_edge`: required genuine source detail before enlargement.
- `max_picture_upscale`: maximum acceptable enlargement of the native crop.

Use appropriately demanding values and inspect the pixels; numbers cannot
approve an aesthetically weak result. Do not lower thresholds merely to pass a
failed source. Record explicit user exceptions. The policy is checksummed at
initialization. Historic runs keep their original evidence and rules.

Each candidate's `quality_evidence` links a JSON file containing `source_path`,
`source_sha256`, `acquisition_record`, `native_dimensions`,
`expected_final_dimensions`, and `picture_regions`. Each region records
`source_crop_native` and `output_rect` as `[x, y, width, height]`. Cover every
distinct crop. Native dimensions describe real pre-enlargement detail, not an
upscaled intermediate; the helper checks consistency, not the truth of an
uploader's provenance claim.

Run `run_state.py quality-check --policy <policy.json> --evidence <evidence.json>
--output <measurement.json>` before editing, and add `--final <actual.mp4>` for
delivery. Candidate registration repeats preflight; final registration probes
the downloaded file. Exit 2 is failure. FFprobe must be on PATH. Draft-preview
resolution is not delivery resolution.

For new candidates, use `caption_treatment.version: "caption-treatment-v2"`:

- Record a concrete `style_intent`, explicit `animation` and `emphasis`, the
  measured `max_words_visible`, and boolean `spoken_word_highlighting`.
- Require `rendered_readability_check`, `rendered_timing_check`, and
  `rendered_motion_check` to pass based on actual frames and timing evidence.
- When highlighting is enabled, also record the chosen `active_word_color`
  (`#RRGGBB`) and a passed `rendered_active_word_check`.
- Put any actual brief-specific word limit or highlighting requirement in the
  assignment's `captions.max_words_visible` and
  `captions.word_timed_active_word_color`. Do not invent a permanent word limit.

The helper checks these assignment constraints without choosing the aesthetic.
Old unversioned candidate/review records remain compatible with their original
rules; use the new evidence format for new work rather than copying old recipes.

## Judge the actual final before repeating a treatment

Complete one representative short through native final export before batch
editing. Compare it with the chosen quality reference at the same intended
viewing size. Inspect source detail, glyph edges, reading comfort, hierarchy,
caption transitions, framing, cut boundaries, payoff, audio measurements, and
the entire ending. Use playback where available and describe the evidence
honestly. A tiny contact sheet, a score, or a checksum cannot waive a visible
quality defect. When the renderer, source, or treatment changes materially,
verify the new behavior before repeating it.

`claim` holds subsequent shorts until the pilot passes `export`. Coordinator QC
records `caption_quality_check: "pass"`, and also `active_word_caption_check`
when highlighting is used. `delivery_quality_review` points to an independent
JSON review with these fields:

```json
{
  "version": "delivery-review-v2",
  "source_detail": "pass", "typography": "pass",
  "caption_timing": "pass", "caption_motion": "pass", "style_fit": "pass",
  "observations": "Concrete observed results and their fit to this brief.",
  "evidence_files": ["/run/candidates/short/full-size-evidence.png"],
  "final_sha256": "required-for-final-review-only"
}
```

The review judges intended animation, deliberate static styling, or another
supported treatment on its execution. Its observations must identify real
frames and timing evidence; the script validates linkage, not aesthetics.
Pass the review to `run_state.py export --quality-review <review.json>`.
Repeat actual-file review for every final, retaining its EDL, checksum, dimensions,
source/crop detail, and visual/timing evidence. Repair visible defects before
delivery even when previews, technical checks, or review scores passed.

## Reject a failed actual final before delivery

Decode and inspect the last actual editorial frames through the first branding
frames, plus the final decoded frame. Fractional cut durations can expose one
or two raw-source frames after a picture overlay ends; a duration total or
preview pass does not prove that the actual final has a clean transition.

Inspect every authored B-roll card boundary in the actual final at native size:
one frame before, at, and after each boundary, and every intervening frame in
small gaps between adjacent cards. Valid timings can still expose distracting
one-to-four-frame interview flashes. Close accidental gaps by extending the
preceding card to the next card's onset, preserving the next card's word cue,
audio, and keeps. Render and inspect the repaired final again before approving it.

If the downloaded final fails, keep it and its native download receipt. A
coordinator may reopen a `ready` or `exporting` short with:

```bash
python <skill-root>/scripts/run_state.py reject-final \
  --run-dir <run> --short-id <id> --file <actual-failed-final.mp4> \
  --edl-version <version> --report <final-rejection.json>
```

The rejection JSON must bind `run_id`, `short_id`, `child_project_id`,
`style_lane`, `edl_version`, `verdict:"repair"`, and `final_sha256` to that
candidate and file. Set `final_receipt` to the absolute native download JSON
path (containing a `final_export` receipt with matching version and SHA), and
record concrete observations and evidence paths. Finish and record all
queued/running jobs first, including an export started with `export-start`.
The command snapshots prior candidate/QC/export and the rejection/receipt in
`rejected_finals`, clears current QC/export approval, and consumes one of the
existing two repair rounds. The editor then `claim`s the short and returns a
fresh candidate for coordinator QC and a newly inspected actual final. It
does not reopen delivered or terminal shorts or waive the repair limit.
