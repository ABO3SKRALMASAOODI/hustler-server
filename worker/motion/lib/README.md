# Motion page libraries (`motion/lib/*.js`)

A `<name>.js` file here is appended to `motion/runtime.js` (name order,
comments stripped; `motion_engine._runtime_js`) in every motion document
that uses it: the page names `MG.<name>`, or its params carry `_<name>`
(`motion_engine.libs_used`). Every other document carries `runtime.js`
byte for byte, as before Wave 0. Every library enters every clip cache key.
A device track that needs shared page code adds a file here (1C:
`paint.js`, used as `MG.paint`) instead of editing the runtime.

Rules: pure functions of `t` (no clocks, no randomness, no network), attach
to `window.MG.<name>` (the file's stem: that name is what pulls it into a
page), keep each file small (a document carries it under the 200 KB cap),
and no multi-line template literals (the comment stripper is line based).

## `beats.js` — `MG.beats`

Reads `MG.params._beats`, the item's rhythm on its own composition clock
(`motion_engine.beats_param` from a program-clock RhythmMap;
`build_document(beats=...)`): `{t: beats, down: downbeats, on: [[t, cls,
strength]], src: "music" | "speech"}`.

- `MG.beats.times(kind)` — `"t"`, `"down"`, `"on"` or an onset class
  (`"stress"`, `"kick"`, ...)
- `MG.beats.index(t, kind)`, `since(t, kind)`, `until(t, kind)`
- `MG.beats.pulse(t, decay=0.25, kind)` — 1 on a beat, falling to 0 over
  `decay` seconds

Without `_beats` every helper answers "no beat", so a page that uses them
still renders.
