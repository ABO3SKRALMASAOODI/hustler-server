# Editor contract v7

## Input and output

Receive one immutable assignment containing:

- run ID, short ID, parent ID, and child project ID;
- exact source range and verbatim transcript;
- setup, development, turn, and payoff;
- target viewer and intended feeling;
- selected taste-profile lane and reference observations;
- for the current four-style brief, the content-based lane reason, closest
  alternative, headline weight, and required branding layout/runtime;
- output ratio and frame treatment, allowed duration, pacing, planned audio
  states, and transition cues;
- forbidden choices and known risks;
- local candidate output directory.

Return a machine-readable `candidate.json`, the current preview MP4, a contact
sheet, transcript/caption evidence, deterministic media probe results, and any
nonterminal Valmera job IDs. Return status `candidate`, `repair_required`, or
`failed_technical`; never `ready` or `exported`.

## Story first

The viewer must understand the short without the podcast title or preceding
conversation. Preserve the speaker's meaning. You may tighten disfluencies and
dead air but must not manufacture a claim, rearrange cause and effect, or hide
a qualification.

The opening should create immediate curiosity without clipping the first word.
Supply the minimum missing context early. Build one clear line of development,
land the payoff, and end on the last meaningful audiovisual beat. Never leave
an empty, silent, frozen, or reaction-only tail unless it is an intentional and
approved story beat.
For the current four-style brief, the complete native Valmera ending is a
required intentional segment. Reserve its duration; do not trim it as a tail.

For the requested named-style brief, read `style-lanes-v7.md`. A complete
spoken premise followed by a visual payoff satisfies the montage structure.
The planned silent montage or silent action opening is intentional content;
do not fill it with speech, music, or sound effects. Record its exact output
time interval after all timeline changes.
For `hook-to-silent-montage`, record a shot-by-shot relevance ledger: output
time, asset identity/source, visible subject, the premise/payoff beat served,
and the viewer-visible link. Reject a merely adjacent topic or an association
that requires private background knowledge. For a person-centered story,
prioritize verified footage of that person and identifiable real work or
accomplishments; use truthful before/after contrast when the story calls for
it. If direct assets are insufficient, report the gap or change the lane.
Keep the montage at no more than 15 seconds and the entire editorial program
at no more than 25 seconds, both measured before the separate native ending.

For `headline-conversation`, read `headline-conversation-references-v7.md`.
Use no B-roll, preserve the full-duration topic headline above the picture,
and keep spoken subtitles as a separate readable layer, horizontally centered
around the middle of the inner picture rectangle as specified in the lane
contract. Match the reference placement across camera cuts. This lane's
longer speaker holds must not be mistaken for slow B-roll or replaced by cutaways.

## Editorial choices

Apply `delivery-quality-v7.md` to source/crop detail, premium caption execution,
and the candidate's `quality_evidence`. Select the visual treatment from this
brief and story; a past repair or tool preset does not dictate future styling.

- Follow the assigned opening structure. Add visual material when it supports
  evidence, memory, contrast, location, stakes, consequence, or the requested
  montage's emotional direction without implying false facts.
- Do not meet a B-roll quota. A strong face is better than generic stock.
- Set visual holds from the assigned lane and references. Fast conversational
  B-roll is frequent and brief while dialogue continues. Longer video holds
  need visible action or information worth watching; a long source file alone
  does not justify a long insert. Do not apply a universal 2–7 second hold.
- Use licensed, clean assets. Reject third-party watermarks, conflicting embedded
  subtitles, unrelated branding or UI residue, and people misidentified as the
  speaker/event. A relevant company name, logo, or designed card can be valid
  when requested; inspect its factual role and readability.
  Preserve the user's required Valmera corner mark and native ending. Follow
  `style-lanes-v7.md` for the native admin placement choices and duration; generic
  cleanup rules do not authorize removing platform branding.
- Reframe every speaker shot intentionally. Keep faces, gestures, and eyelines
  clear of captions. Check all speaker changes and cut boundaries for a
  one-frame leak of the wrong person.
- Captions must follow the spoken words exactly enough to preserve meaning.
  Choose phrase length, line breaks, font, color, placement, and motion for
  legibility and the selected treatment. When the brief requests spoken-word
  highlighting, verify the active word against real timings in the render.
  Record any brief-specific limits in the assignment. Explicitly choose
  animation/emphasis and inspect their effect; configuration alone is not proof.
- Avoid duplicate words, broken punctuation,
  unreadable single-frame captions, face collisions, and inconsistent casing.
- Inventory every external B-roll still or video shot by stable asset identity,
  source-time window, and output-time window before rendering. Within one
  short, never repeat the same or near-identical visual moment. Cropping,
  mirroring, zooming, speeding, recoloring, or adding text does not create a
  new shot. Distinct non-overlapping moments from one longer source are valid.
  Cross-short reuse is permitted, but prefer a fresh equally relevant asset.
- For speech-emphasis word/name cards, follow the word-card synchronization
  contract in `style-lanes-v7.md`: the first readable frame lands on the actual
  spoken word in final output time. Preserve speech underneath, recalculate
  cues after timeline changes, and verify every such card against the rendered
  audio. An attractive card appearing seconds after its word is a timing defect.
- Apply one coherent graphic/type/color language per short. The batch may use
  different approved style lanes.
  In the current brief, headlines use an actual bold/heavy face, with at least
  the visible stroke weight of dialogue captions. Verify the rendered title
  at phone size; a font fallback or thin headline needs repair.
- Make a headline about the story's consequential idea. Do not add a secondary
  date, location, interview timestamp, venue, or other incidental metadata
  line beneath it. Include a date or place only when indispensable to the
  claim, integrated into the main headline or spoken context. Inspect sourced
  footage for embedded lower thirds that violate this rule too.
- Do not add music by default when downstream publishing handles it. If the
  taste profile explicitly includes music, keep speech intelligible and verify
  the full mix deterministically.

## Efficient edit loop

1. Inspect transcript, source boundaries, speakers, and the seed EDL.
2. Write a concise edit plan keyed to story beats and risks.
3. Make structural cuts and reframing.
4. Add captions and only the purposeful overlays/B-roll.
5. Run deterministic EDL/caption/asset checks before rendering.
6. Render one rough preview after the composition is substantially complete.
7. Review it end to end using the adaptive QC schedule. Consolidate every
   issue into one repair pass.
8. Render the current candidate once, verify its EDL version, and build the
   evidence bundle.

Do not render after every small mutation. Do not generate every frame of the
video. If one region looks suspicious, inspect that region densely.

## Candidate bundle

`candidate.json` must include:

```json
{
  "version": "candidate-v1",
  "run_id": "...",
  "short_id": "...",
  "child_project_id": 123,
  "style_lane": "hook-to-silent-montage",
  "edl_version": 7,
  "preview_path": "absolute/path.mp4",
  "preview_sha256": "...",
  "duration_s": 23.0,
  "editorial_duration_s": 23.0,
  "story_beats": {"setup": [0, 8], "development": [8, 17], "payoff": [17, 23]},
  "caption_treatment": {
    "version": "caption-treatment-v2",
    "style_intent": "Describe the treatment selected for this story and why it fits.",
    "animation": "none",
    "emphasis": "none",
    "rendered_readability_check": "pass",
    "rendered_timing_check": "pass",
    "rendered_motion_check": "pass",
    "spoken_word_highlighting": true,
    "active_word_color": "#FFD54A",
    "max_words_visible": 4,
    "rendered_active_word_check": "pass"
  },
  "broll_shots": [
    {"asset_key": "subject-early", "source_start_s": 2.0, "source_end_s": 3.5, "output_start_s": 8.0, "output_end_s": 9.5},
    {"asset_key": "subject-later", "source_start_s": 12.0, "source_end_s": 14.0, "output_start_s": 9.5, "output_end_s": 11.5}
  ],
  "duplicate_broll_within_short": false,
  "montage_timing": {"start_s": 8.0, "end_s": 23.0, "duration_s": 15.0},
  "checks": {"media_probe": "pass", "captions": "pass", "assets": "pass"},
  "known_issues": [],
  "outstanding_job_ids": []
}
```

The caption values above illustrate the evidence format, not a required style.
Replace them with the chosen treatment and observed checks.

Use absolute local paths. The coordinator rejects a missing file, mismatched
checksum, stale EDL version, or outstanding mutation/render job.

Also include `style_lane`, `output_ratio`, `frame_treatment`,
`intentional_silence_spans`, and `music_cues` in the bundle. Use final program
seconds for silence spans and cues, and an empty list when absent. Record
framing settings and the measured dimensions in the media probe evidence.
For a persistent-headline layout, also record `picture_region` and `headline`
(exact text, start/end, bounds, font face, and weight) so QC can distinguish
the inner picture,
the fixed topic headline, and the separate changing speech captions.
For speech-emphasis cards, include `word_card_cues` with card text, the matched
speaker/word occurrence, the word's final audio onset, and the card's first
readable output frame/time. Use an empty list when none apply. These cues let
QC verify actual synchronization instead of trusting approximate placement.
For `hook-to-silent-montage`, include `montage_shot_relevance` with the
shot-by-shot ledger described above. The coordinator must be able to verify
each connection against the rendered shot, not merely the asset filename.
Also include `montage_timing` with `start_s`, `end_s`, and `duration_s`, all in
editorial-program seconds. `editorial_duration_s` excludes the native ending.
For every lane, include `caption_treatment` as shown above and a `broll_shots`
entry for every external still/video use with `asset_key`, `source_start_s`,
`source_end_s`, `output_start_s`, and `output_end_s`. Images use `0` for both
source times. Set `duplicate_broll_within_short` only after comparing the
ledger and rendered pixels; any repetition makes the candidate incomplete.

For the current four-style brief, retain `style_choice_reason` and
`closest_alternative`. Include `branding` with the chosen admin mode, corner anchor, and
picture bounds, native end-card duration reserved, and whether each element
is preview-verified or pending final-only verification. Do not claim to have
verified final-only branding from a preview. Confirm actual branding in the
downloaded final before reporting delivery.

## Repair response

A repair packet contains timestamps, observed evidence, severity, and the
required outcome. Fix all items in one deliberate pass. Re-render the full
current preview and replace the candidate bundle; do not patch only the sampled
frame and assume adjacent playback is correct.
