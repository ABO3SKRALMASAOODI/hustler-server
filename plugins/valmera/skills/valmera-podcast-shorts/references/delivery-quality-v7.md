# Source, typography, and delivery quality v7

Read before acquisition, the first edit, and final approval. The September 23
correction preserves the user's intentionally added **current spoken word
color** while restoring sharp video and restrained, stable phrase typography.
For this user's current four-style brief, apply the defaults below. A different
brief may specify another ratio, typography, or quality policy; do not turn
this example into a universal visual template.

## Establish real detail before editing

Target 1080×1920 portrait delivery. Native 1012×1800 is acceptable without
forced enlargement. For this brief the minimum is 720×1280 unless the user has
explicitly accepted a documented source limitation. A minimum is a floor, not
an acquisition target: obtain the highest practical clean source and inspect
its actual detail, compression, and cropped face at intended viewing size.

Save format discovery, each attempted format/client, the actual successful
download selection, its probe, and checksum in `source/`. Discovery JSON can
list a 4K stream that was never downloaded; only the actual media proves what
was acquired. A 403 for one rendition/client is an access failure, not proof
that only 360p exists. Make a bounded acquisition repair: try other advertised
compatible high-resolution formats/clients, refresh extraction, or use an
available authorized original. Record failures and the next action if usable
source quality remains unavailable. Never bypass access controls.

A low-resolution proxy can support transcription and selection, but must not
become the production original silently. Resolve a below-floor source before
materializing the batch. If resolution is adequate but the recording itself
is soft, compare actual pixels and disclose that limit; bitrate and dimension
labels cannot establish detail. Do not claim a better recording is required
until distinguishing access failure from inherent recording quality.

Valmera's native `frame_dims` caps the canvas using the **original project
source**: a 640×338 source can deliver only about 338×600 at 9:16. A 720×1280
overlay or enlarged local asset does not raise that native export ceiling or
recover face detail. Repair the genuine original/project lineage, then render
again; do not deliver a resized low-resolution final as the quality fix.

Record each main-source crop in original/native pixels and its destination
rectangle in expected final pixels. Check source detail **after** the crop,
not merely the width of the uncropped original. Avoid magnifying a small
speaker crop into a large panel. Loosen/reposition the crop, use a better
original, or flag the unresolved detail limitation. Check each camera shot.

## Stable phrase captions with moving color

The approved relationship is a dominant concise headline, a clean picture,
and smaller readable speech phrases. The September 19 affordability short is
a concrete reference for that relationship; its static all-white words are
superseded by the user's later active-word-color instruction. Preserve that
color. Generic tool/playbook suggestions for stacked, large, popping reels
captions do not override this explicit preference.

Start this brief with these transcript-caption style values, passed through
the live tool's supported `style` object:

```json
{
  "preset": "classic", "dynamic": true,
  "animation": "none", "emphasis": "none",
  "font": "Inter Display Black", "size": "m", "size_scale": 0.82,
  "color": "#FFFFFF", "highlight_color": "#F5D35A",
  "outline_color": "#000000", "outline_width": 2,
  "shadow": 1, "uppercase": false
}
```

These are starting values, not a claim of a fixed rendered point size.
Confirm the actual font and readable stroke weight at delivery scale. Use
semantic groups normally containing three or four words, hard maximum four,
and at most two balanced rows. Prefer shorter natural groups over shrinking
type or clipping a long phrase. Keep neighboring words the same size and
stationary while **only the currently spoken word changes to gold**. Set
`animation:none` explicitly: an omitted value in legacy dynamic captions can
invoke a 62%→114%→106% word-pop even though coloring was the only intent.
Do not disable `dynamic` just to remove the pop. Verify current production
pixels; an accepted option is not proof the active renderer implements it.

The legacy dynamic renderer may keep whole groups on one row; do not promise
two-row semantic control without verifying the tool path. Shorten/regroup
instead of widening or oversizing the line. Manual caption items do not
automatically inherit transcript active-word coloring. If authored phrases
are needed, preserve real word timing and explicitly verify color transitions.

Place speech relative to the visible picture and faces, following the lane.
Keep a headline visually dominant, usually by shortening it to two lines;
omit redundant identity prefixes such as “ELON MUSK:” when the image/context
already identifies him. Do not shrink the title merely to fit verbose copy.
Preserve each lane's timing, required branding, and the separate native ending.

## Opt-in durable quality gate

For new runs under this brief, write `taste/quality-policy.json` and supply it
to `run_state.py init --quality-policy <absolute-policy-path>`:

```json
{
  "version": "delivery-quality-v1",
  "min_final_dimensions": [720, 1280],
  "target_final_dimensions": [1080, 1920],
  "min_native_short_edge": 720,
  "max_picture_upscale": 1.1
}
```

The small resampling allowance is a ceiling, not permission to accept visibly
soft faces. A different ratio/brief needs appropriate dimensions. Preserve
explicit user exceptions in the profile; never lower a policy to make a
failed render pass. The policy is checksummed at initialization. Historic
completed runs remain auditable without invented evidence or retroactive
policy changes. Repair work can use a new run referencing existing project
IDs and reviewed versions, preserving previous deliverables.

Each candidate's `quality_evidence` points to a JSON file like:

```json
{
  "source_path": "/run/source/original.mp4",
  "source_sha256": "actual-sha256",
  "acquisition_record": "/run/source/acquisition.json",
  "native_dimensions": [1920, 1012],
  "expected_final_dimensions": [1012, 1800],
  "picture_regions": [
    {"source_crop_native": [0, 0, 1100, 824],
     "output_rect": [0, 519, 1012, 760]}
  ]
}
```

`native_dimensions` records the real source detail before any enlargement,
not an upscaled intermediate. Keep the genuine original when available. The
helper can reject geometry/provenance inconsistencies but cannot detect an
uploader's pre-upscaled recording or independently prove asserted provenance;
inspect source pixels and the acquisition record. Cover every distinct crop.
Evaluate external B-roll detail under the existing asset contract.

Run the same deterministic check before editing and on the downloaded final:

```bash
python <skill-root>/scripts/run_state.py quality-check \
  --policy <policy.json> --evidence <quality-evidence.json> \
  --final <actual-final.mp4> --output <quality-measurement.json>
```

Omit `--final` for preflight; that checks expected geometry without confusing
a 270×480 draft preview with delivery. Exit 2 means failure. The helper needs
FFprobe on PATH and Python's standard library. Candidate registration repeats
preflight, and final registration probes the actual file. Upscaled low-detail
source and undersized exports fail independently. The policy does not force
an unnecessary upscale to its target dimensions.

## Inspect one actual final before the batch

Complete one representative short through native final export and compare it
beside the user-liked reference at the **same intended display size**. Inspect
original-resolution frames and playback of the opening, dense phrases,
active-word changes, camera cuts, and payoff. Check face detail, clean glyph
edges, uniform word size/baseline, readable gold, title hierarchy, and picture
placement. A 270×480 contact sheet alone cannot approve sharpness. Neither a
94/100 score nor a valid checksum can waive an unresolved quality defect.

For opted-in runs, `claim` holds subsequent shorts until the first actual final
passes `export`; a failed pilot is repaired or recorded as an exception.
Candidates add `animation:"none"`, `emphasis:"none"`, and
`rendered_stable_size_check:"pass"` to existing `caption_treatment` evidence.
QC records `delivery_quality_review`, an absolute path to a JSON review:

```json
{
  "source_detail": "pass", "typography": "pass",
  "stable_word_size": "pass", "active_word_color": "pass",
  "reference_comparison": "pass",
  "observations": "Specific observed frames, phrase transitions, and reference comparison.",
  "evidence_files": ["/run/candidates/short-01/full-size-comparison.png"],
  "final_sha256": "required-for-final-review-only"
}
```

Use genuine observations, not the sample sentence. A review is the independent
coordinator's visual judgment; the script validates evidence linkage, not
image aesthetics. Preview QC can precede export; final review must name the
actual final checksum. Pass its path to `run_state.py export --quality-review
<review.json>`. Repeat quality and existing final checks for every delivered
file, recording exact EDL, dimensions, original/crop detail, typography
settings, evidence, and checksum in candidate/QC/final records. Recheck a new
font, renderer, source, or materially different layout before repeating it.

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
