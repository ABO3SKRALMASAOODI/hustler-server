# captions — motion caption looks, size ladders and accents, placement, legacy presets, fixes, translation, pre-captioned footage

## Editorial decision principles

On short-form speech, captions are the motion-typography layer the viewer
reads on every frame: words reveal on their spoken onsets, a size ladder
separates connector words from the 1–2 words that carry the sentence, and
one accent role (colour, serif italic or a hero size) marks meaning. Static
all-white subtitles are an accessibility track, not a premium reel. Legibility
is still the craft: hierarchy, speech-rhythm phrasing, measured placement and
one coherent treatment.

- Short-form speech (reels, shorts, podcast clips, vertical ≤120 s):
  browser motion captions via `style.motion_look`.
- Long-form, interviews for accessibility, education and translation:
  readable phrase subtitles (presets such as `documentary`, `clean`,
  `broadcast`).
- One caption system per video; designed graphics take over the words for
  their own windows instead of stacking a second system.

## Evidence to inspect

Inspect complete surviving transcript coverage (`get_kept_transcript`),
speech rhythm and stress (`suggest_emphasis`, `get_audio_analysis`), face and
UI geometry and clear bands (`look_at`, `get_editorial_map`), burned-in text,
densest phrases, distinct backgrounds, script/font fallback, and every
placement or layout change in the rendered preview.

## Strong treatment patterns

MOTION LOOKS — `add_captions(mode='from_transcript', style={...})` or
`set_caption_style` with `style.motion_look`. The browser engine draws the
captions from the same kept, corrected, mute-filtered words as every other
caption path, so they follow cuts exactly: a card never runs across a cut,
and a line whose words are all spoken clears on the cut instead of holding
onto the new shot.
- `editorial` — the premium default for podcast and interview reels: tight
  grotesk phrases with a size ladder and an accent role on hero words.
- `clean` — quiet premium sentence case; each word snaps up out of a short
  blur (two frames) as it is spoken. Documentary and calm conversation.
- `lockup` — pre-laid-out stacked lockups; words reveal in place and the
  block never reflows. Designed, poster-like delivery.
- `serif` — sans phrases whose emphasis words switch to a large serif
  italic. Editorial luxury, reflective speech.
- `pop` — bold 1–3 word punches; the spoken word springs in and takes the
  accent colour. Hype, motivation, fast creators.
- `stack` — small connector words above a huge hero word that slams in.
  Punchy statements.
- `box` — phrase on a soft dark pill with a highlight gliding to each word.
  Busy or bright backgrounds.
- `glow` — whole phrase dim, spoken words light up with an accent glow.
  Karaoke and music-led edits.
- `mono` — monospace typewriter with a caret. Tech and terminal stories.
`color`, `highlight_color` (the accent), `font`, `size`/`size_scale`,
`uppercase`, `position`/`anchor_y` and `max_words_per_caption` still apply
to motion looks. Set `motion_look` to null to return to presets. `apply_look`
sets a matching motion look as part of its package; refine it with
`set_caption_style` rather than re-adding.

SIZE LADDER AND ACCENT:
- Connector words sit around 2.5–4% of frame height; accent words 2–3x that
  (hero words in graphics go far larger — see motion-design).
- Accent colour on 1–2 words per sentence only, from one restrained palette
  (near-white type with a single red, gold, sand or periwinkle accent).
  Choose the meaning words — numbers, names, the verb that lands, the
  contrast — and pass them VERBATIM as `emphasis_words`; otherwise emphasis is
  auto-selected from measured stress, numbers and outcome words. Pass [] for
  a deliberately flat hierarchy.
- Words containing digits are emphasized automatically. Wrong emphasis reads
  worse than none.
- No thick outlines, no yellow boxes by default, no emoji streams.
  Legibility comes from size, clear-space placement, soft shadow and the
  grade.

CAPTIONS AND GRAPHICS SHARE ONE STAGE. A motion graphic that says the spoken
words (word slam, phrase build, a hook title of the spoken hook, a typewriter
of the line being said) must mute captions for its own window;
complementary graphics keep them. Template defaults differ, so pass
`mute_captions` explicitly on `add_motion_graphic` rather than duplicating
it with `set_caption_mutes`. Never let a caption page and a graphic animate
in the same band at the same instant. A mute over words the graphic does not
carry (a counter over its own setup line) leaves sound-off viewers with
nothing; the write reply NOTEs the words that would vanish. Kickers, labels
and quotes copy the transcript's exact words — the reply NOTEs a
paraphrase and quotes the phrase to use.

PLACEMENT LAW: multi-word captions sit in measured clear space — usually
the lower-middle band above the platform UI, or beside the face in a
designed layout — never across eyes or mouth, never inside the bottom ~13–15%
of a 9:16 frame, never covering what the speaker points at. Placement is
shot-measured and stabilized; `position` or `anchor_y` locks one band for the
whole video, so omit them to let collision-aware placement adapt by shot.
Only single-word looks (or the `lyric` preset) hold dead centre.

LEGACY PRESET FAMILIES (`style.preset`) remain available and are the right
choice for subtitles and specific grammars:
- `documentary` — restrained phrases on a translucent panel: long-form,
  accessibility, education, bright or changing backgrounds, translations.
- `clean` — white complete short phrases with size-only hierarchy;
  `composed` — complete readable phrases with a muted spoken-word tint.
- `broadcast` — left-aligned lower-third panel for news and explainers.
- `reels`, `podcast`, `beast`, `karaoke`, `spotlight` (one glowing word at a
  time, centre), `lyric` (phrase-led music and quote typography, centre) for
  ASS-rendered social grammars.
- `luxe`, `editorial`, `fashion`, `elegant` for serif/fashion footage (the
  `editorial` PRESET is the ASS serif look; `motion_look='editorial'` is the
  browser motion look above — different fields);
  stack presets (`stacked`, `iridescent`, `chrome`, `impact`) compose lines
  of very different sizes; `retro`, `neon` for visual genres; `classic` is the
  legacy subtitle look.
- Composition overrides: `font` (bundled family), `emphasis` (big, accent,
  pop, box, serif, script, chrome, glow, chroma), `emphasis_scale` 1.0–3.0,
  `layout='stack'` with `leading` 0.85–0.95 for interlocking lines,
  `animation` (fade, pop, punch, blur_in, whip, flash, rise, drop, elastic,
  bounce, swing, zoom_blur, or none), outline/shadow/background panel,
  `tracking`, `text_align`, `anchor_y`.

USER PREFERENCES WIN:
- When the user rejects a caption colour or accent, the answer is NO accent —
  white, emphasis by size alone. Never swap the rejected colour for another
  hue. Honour explicit "clean/minimal/no colour" requests the same way.
- A named preset, font or size request is honoured exactly; a second "too
  small" means much bigger.

BASICS:
- `add_captions('from_transcript')` burns word-timed captions for
  everything that survives the cut; timing always comes from the transcript.
  `add_captions('off')` removes captions WE added. It replaces the whole set
  in one call — never call 'off' first and re-add.
- Restyle existing captions with `set_caption_style` and only the fields to
  change.
- Manual caption items are only for dictated text and translations; they
  render at their exact authored start/end.

TRANSLATION CAPTIONS: build manual items from `get_kept_transcript`, ONE item
per transcript segment with its own start/end and a COMPLETE, faithful
translation of every segment (count items against segments). These are
subtitles: default to `documentary`. RTL scripts render through Noto
fallback; write natural RTL text. Text corrections → ONE
`add_captions(mode='items')` call with the full corrected list.

READABILITY:
- Group words by meaning and reading time; avoid both crowded sentences and
  rapid isolated fragments. New tracks reset at breath pauses and sentence
  ends and avoid ending on "the / because / of".
- Contrast is non-negotiable: check rendered frames at 2–3 caption moments
  on distinct backgrounds; fix with the look's plate or glow, a clearer band,
  or a grade adjustment. Motion looks (clean, editorial, lockup, serif,
  glow, stack) measure the picture under every cue and lay a soft dark
  pocket under the words where the plate is too bright for them (4.5:1);
  on dark plates nothing changes.
- `audit_captions()` compiles the exact caption artifact and reports
  lateness, uncovered words and overlaps. `render_preview(complete=false)`
  returns caption QA pages of real rendered caption states — judge those
  while iterating. To inspect its `qa_output_times` or any other moment,
  render a complete preview of the current version
  (`render_preview(complete=true)`), then
  `look_at(rendered=true, output_times=[...])`; rendered looks reject a
  changed-section proof. The geometry-only view without `rendered=true`
  contains no burned captions.

CAPTIONS OFF FOR PART OF THE VIDEO: `set_caption_mutes(spans=[[start,end],
...])` in PROGRAM seconds replaces the manual mute list; spans=[] clears it.
Designed text and motion graphics own suppression for their own windows.
Inserted media and title cards are never captioned.

CORRECTIONS: `set_caption_fixes` replaces the correction set by default;
operation='list' inspects, 'append' upserts, 'clear' removes. Pairs apply
globally; {from,to,start,end} targets one occurrence in output seconds.
Check names and ASR confidence with `get_words` before correcting.

PHRASE CADENCE: with static phrase presets, `min_words_per_caption` sets a
preferred minimum bounded by `max_words_per_caption`; caption QA reports
short states.

PRE-CAPTIONED FOOTAGE: when captions are burned into the source, never burn
new ones on top. `erase_burned_text()` repaints those bands; then
`add_captions` writes ours on a clear frame. If the erase ghosts, escalate:
cover the band (`blur_region`), crop it out, or place new captions elsewhere.
Style new captions so they cannot be mistaken for the old ones. "Remove the
captions": `get_edl` first — ours turn off with `add_captions('off')`; burned
ones are the erase case.

## Common failure modes

- Static white subtitles on a premium reel; captions shrunk until the hero
  words carry no weight.
- Accent colour on every other word, or on random nouns.
- Corrupt glyphs, missing words, orphan connectors, phrases too fast to read.
- Face, UI-band or graphic collisions; two caption systems on screen;
  burned-caption stacking.
- Swapping a rejected colour for another colour.

## Verification procedure

Run `audit_captions`, render, and inspect the caption QA pages plus (on a
complete preview) `look_at(rendered=true, ...)` across distinct backgrounds
and layouts and the densest phrase: words appear on their onsets, the accent
lands on the right words, the ladder reads at phone size, nothing crosses
the face or the UI band, and no graphic and caption page fight for the same
band.

## Repair ladder

Correct text/coverage → regroup by speech rhythm → re-pick accent words →
move to clear space or adjust size → switch motion look (box/glow for busy
backgrounds) → mute under the graphic that says the words → erase or avoid
burned text → render and audit again.
