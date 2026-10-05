# Editor: one child, one story

Read creative-v8 and your assignment. Open only its immutable `project_id`.
Stay on Sol. Do not spawn other agents, edit siblings, publish, or approve your
own work. Return a candidate plus exact EDL version and any outstanding job ID.

## Plan the result

Inspect the source words and camera/gesture changes in the selected range.
Resolve hook, turn and payoff before styling. Write a short `design.json` with
the visual idea, deliberate caption grammar and exact moments that need visual
explanation. Use the relevant saved reference frames/motion already supplied
in the assignment. Do not re-analyze all seven references for each child.

## Preserve the original source clock

Children created by `make_shorts` already share the indexed parent. Keep that
source. Use `keep_segments` for cuts; captions follow the native timeline.
Do not export a trimmed source, re-upload it, re-index it, then rebuild missed
words as little ASS videos. Do not pre-upscale archival footage for sharp type.
The final renderer composes new graphics on an HD canvas independently.

Use `set_frame` or one edit batch to place the picture natively:

```json
{"ratio":"9:16","mode":"crop","picture":[0,0.2890625,1,0.7109375],"focus_x":0.5,"focus_y":0.5}
```

This is a 4:3 picture on a black 9:16 canvas, not a mandatory crop. Choose the
rectangle and per-shot `focus_track` from the actual speakers; `pad` retains
the whole source inside it. Inspect the edges. This operation changes pixels,
not audio, transcript or source identity. It removes the routine need for a
prepared master. A focus track uses **source** seconds, not output seconds.

Use `add_overlay(fit="picture")` for silent B-roll over continuing original
dialogue. It fills `frame.picture` without covering the headline area. The
overlay's audio stays silent. `fit="cover"` intentionally covers the full
canvas. Use `insert_media` for the explicitly silent montage/action formats,
checking their audio states and final timing. Do not insert a title-card clip
when you mean a graphic over uninterrupted speech.

## Independent headline, expressive dialogue

`add_text(..., mute_captions=false)` keeps the topic headline and dialogue
captions separate. Put the headline just above the actual picture, with heavy
type, readable wrapping and no incidental date/place subline. It is visible
from the first frame through the editorial ending in the headline format.
Do not upload a rasterized full-frame title to work around caption suppression.

Read the live captions skill once per editor. Choose a suitable native preset
and `emphasis_words` for meaning-bearing words. Use a clear phrase hierarchy
and a coherent motion treatment. Keep the requested active-word color. Do not
copy a previous candidate's `none/none` settings without judging the passage.
Start with stable placement in the visible picture and adjust for actual face
collisions. Let the native compiler group speech; correct ASR errors with
`set_caption_fixes`, not a whole new transcription pipeline.

## Start from a tested native composition

For a fresh child, `scripts/compose_short.py plan.json --index index.json
--out composition.json` compiles cuts, picture placement, expressive captions
and optional graphic beats into one batch. It also emits `.edl.json`, surviving
words on both clocks and the expected duration including the 5s ending. It
never uploads, re-indexes, transcribes or judges the edit.

The plan contains `id`, chronological source `keep` ranges, `caption_recipe`
(`kinetic`, `quiet`, `editorial`), meaningful `emphasis_words`, optional `accent`,
`frame`, `caption_style` overrides, `headline:{text,y,scale}` and `graphics`.
`graphics.beats` use the kinds below. Use `source_start` / `source_end` for a
spoken cue in one kept source range; the compiler maps it after the cuts. Or
use output `start` / `end`, never both. Inspect the original word boundaries;
this helper does not decide which words to cut.

The recipes are adjustable starting points, not a compulsory house style.
They place readable, centered dialogue in the lower picture with visible
spoken-word color and semantic scale. Choose from actual performance and
reference evidence. Do not solve a collision by shrinking all captions to tiny
left-aligned text in the black margin. Move the band or simplify the layout.

This helper is for the initial shared-source child. For later cuts, use native
timeline tools to remap existing overlays/audio/graphics; replacing only keep
would strand those layers. The compiler cannot infer whether a face is covered.
Use `outline_width:0, shadow:0` for flat text on solid graphic panels; template
shadows useful on footage can look like doubled letters on a pale card.

## Native designed beats

When a word, contrast or figure deserves a graphic, optionally author:

```json
{
  "id":"a-story-specific-slug",
  "duration_s":24,
  "picture":[0,0.2890625,1,0.7109375],
  "accent":"#BDF76A",
  "beats":[
    {"kind":"contrast","start":8,"end":10.4,
     "before":"An answer","after":"The right answer",
     "purpose":"Express the distinction the speaker is making",
     "cue":"Replace this example with the actual matching spoken occurrence"}
  ]
}
```

This illustrates the data shape, not copy to use in unrelated shorts. Available
kinds are `word` (`text`), `statement` (`text`), `contrast` (`before`,`after`),
and `metric` (`value`,`label`). `headline:{text,y,scale}` is optional. A beat
can set `panel:false` to keep the footage visible, or `keep_captions:true` when
the words are independent and placed in a separate region. Choose colors and
font for the story. The script rejects unreadably short holds.

Run `python <skill>/scripts/design_graphics.py design.json --edl edl.json
--out graphic-layers.json`. Submit its `operations` in `apply_edit_batch` with
the current `base_version` and a unique `operation_id`. Existing matching IDs
are updated rather than duplicated. Related cuts, framing and caption changes
may share the same deliberate batch. Do not render after each operation.

These are editable native text/vector layers. Make custom compositions when
the idea needs them; do not convert every noun into the same card. To emphasize
a speech word, align the first readable frame to that occurrence in the final
timeline. Changing cuts means recalculating those cues.

## Inspect once, repair deliberately

Render the complete candidate. Save its exact EDL/version and MP4 once.
Use `inspect_cut.py candidate.mp4 --out inspection --cues graphic-layers.json`
for a bounded contact sheet, media probe and detector findings. Review the
moving result and actual audio, including opening, strongest design beat,
densest caption passage, all meaningful joins and payoff. Contact sheets do
not establish speech or animation quality. A `review_audio`/`watch_video`
result must actually answer the question; capability denials aren't evidence.

Use the live Valmera `review_audio` lane for actual listening; local credentials
may differ from the deployed service. `times`/`output_times` are window centers,
not starts. For three consecutive 8s samples request centers `[4,12,20]` with
`span_s:8`; use the returned ranges as the evidence scope. Sampling overlap is
not repeated speech in the edit, and a sample edge is not proof of a clipped
source word. Corroborate a claimed defect at the same output time before a
repair. Do not claim full listening when only excerpts were reviewed.

Normal evidence is one reusable packet and concise timestamped observations.
If actual audio exposes a missed or shifted word, fix that specific native
caption occurrence and recheck that window. First establish whether it is a
source-index defect, an edit-clock defect or a visual placement issue. Do not
replace the source as a general repair. ASR/PCM/CTC is a targeted diagnostic
only after a concrete discrepancy—not a routine ritual for every child.

Return `candidate.json` with the established v7 identity fields and
`caption_treatment.version="caption-treatment-v2"`; describe the actual
motion/emphasis you chose. Use the v7 candidate schema only as a data reference
if a field is unclear. Include native EDL, inspection packet, story/graphic
cues, B-roll source ranges/rights, quality evidence and known issues. Never
invent a pass to satisfy a schema. The coordinator independently decides.
