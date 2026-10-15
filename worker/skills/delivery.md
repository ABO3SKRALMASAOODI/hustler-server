# delivery — every aspect re-solved (not scaled), every language from the user's own translation, and the genre format packs (E UI explainer, F visual essay, G sizzle, I documentary, J edit about the edit; A-D and H too) that brief a genre's craft through apply_format_pack

## Editorial decision principles

One edit, many deliveries — each one designed for its frame and its
audience, never a scaled copy.

- Aspects are re-solved: `deliver_aspects` keeps the crop aim (source
  space), re-lays out UI scenes for the new design space, lets the render
  measure every graphic and caption again (keep-out, caption plan, type
  fit) and re-fits overlays with their own proportions. Look at each
  master: a 16:9 frame has room the 9:16 did not — sometimes a graphic
  that was a hero in portrait wants to be a side card in landscape.
- Languages come from the USER: their translation (one line per spoken
  phrase), their subtitle file, their on-screen text mapping. Valmera
  never machine-translates. The choreography stays the original's: every
  phrase keeps its own spoken window.
- A format pack is a brief, not a template: it names the genre's
  structure, palette and budgets and its never-rules. Invent inside it for
  THIS story — the pack's structure is the spine, the devices are chosen
  per moment, and nothing is applied to every short the same way.
- Packs never pick music, a zoom or a sound (owner rules); B&W is only
  ever a device inside a pack, never its look.

## Evidence to inspect

- `deliver_aspects()` (no apply): what each master re-solves.
- `retypeset_language(lang, list_phrases=true)`: the phrases a translation
  is written against, with their windows.
- After each master: `render_preview` / `export_final`, then
  `critique_short` — type on a face, the brand, the safe area.
- The ledger line after `apply_format_pack` (department `direction`):
  the pack's budgets.

## Strong treatment patterns

- Five masters from one edit: `deliver_aspects(apply='16:9')`, export;
  `apply='1:1'`, export; … — each a full re-solve.
- A German version: `retypeset_language(lang='de', translation=[...one
  line per phrase...], mapping={'FINISHED SHORTS': 'FERTIGE SHORTS'})`.
- A product explainer: `apply_format_pack(pack='E')` (its frame, brief and
  proposals to pick), then `add_ui_scene` with the user's data; a
  documentary: `apply_format_pack(pack='I')`, then `source_broll`
  per claim with archival where the history is.

## Common failure modes

- Scaling a portrait master to landscape (letterboxed graphics, tiny
  type): always re-solve.
- A machine translation: refused; ask the user for theirs.
- Translations longer than the phrase can carry: shorten the line (the
  window does not grow).
- Treating a pack as a recipe: two shorts of the same pack should still
  look like two ideas.

## Verification procedure

1. `deliver_aspects()` lists the re-solves; apply one, render, look.
2. `critique_short` on every master: no type on a face, the end card and
   watermark exact, nothing outside the safe area.
3. For a language: `look_at` the longest phrases; captions stay within
   the ladder and the windows did not move.

## Repair ladder

1. A graphic collides in one aspect: move it for that master (the others
   are untouched).
2. A translated line is too long: ask the user for a shorter line, or
   split it at the phrase's own pause.
3. A pack's budget is exceeded: thin to the moments that earn it.

## Through apply_edit_batch

The verbs write ordinary layers; a stale client writes the same result
with `set` operations:

```json
{"action": "set", "layer": "frame", "value": {"ratio": "4:3",
  "mode": "crop", "focus_x": 0.42}}
```

```json
{"action": "set", "layer": "captions", "value": {"mode":
  "from_transcript", "corrections": [{"from": "it really changes your time
  horizons", "to": "es verändert wirklich deinen Zeithorizont",
  "start": 22.69, "end": 25.17}]}}
```
