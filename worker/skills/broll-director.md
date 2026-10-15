# broll-director — sourcing, judging and cutting in cinematic B-roll that proves what the words say: licensed footage and photos from six libraries, judged on the real file, graded into the camera's world, photos that move, a licence ledger on every cutaway

## Editorial decision principles

B-roll is evidence, not wallpaper. A cutaway earns its place when it SHOWS
what the line claims (the rocket he built, the factory he means, the
number's real-world size) or when it lets a long thought breathe over a
picture that carries its feeling. `source_broll` does the legwork — every
library at once, licences checked, the short list downloaded and judged
like a cinematographer would — so the decisions left are the editor's:
which moments, which picture idea, how long.

- Invent for THIS story. Do not run the same recipe (a stock shot on every
  noun) on every short. One short wants three archival cutaways in a row
  under a history line; the next wants a single still that drifts in 2.5D
  on the payoff; another wants none — his face is the story.
- Query the PICTURE, not the topic: "Saturn V lifting off, close" proves a
  launch; "space" proves nothing. Give one or two `query_variants` that
  reach the same proof by another route (an object, a place, an action).
- Cut in ON the word that names the thing (`word`), hold 1.8-3 s, and come
  back to the face for the turn of the argument. The face establishes
  first: never a cutaway in the first second.
- Vary the material across a sequence: a clip, then a still in 2.5D, then
  archival film shown whole on its own soft copy (`archival: true`) —
  different scales, different motion, one world (the match grade).
- Public-domain archive (NASA, the Internet Archive, Wikimedia Commons) is
  often the most honest picture for history, science and space. Ask for it
  with `archival: true` and the cutaway keeps its film texture.
- The candidate list is judged on the real file: too small for the
  vertical crop, a subject the crop would cut, a starved encode, a logo or
  handle welded to the frame, a posed model staring into the lens — all
  refused on sight. The board shows hero | as fetched | after the match
  grade: look at it before you keep anything.
- Restraint: in a talking short two or three cutaways are usually plenty;
  a montage under a voice-over can carry one per phrase.

## Evidence to inspect

- `get_kept_transcript` / `get_words`: the exact word each cutaway lands
  on, and where a thought is long enough to leave the face.
- The director's board (hero | fetched | matched): is the picture the
  proof you asked for, does it sit in the same world after the grade, is
  anything in it off-tone for the line?
- The reply: per moment the source, licence, on-topic, crop survival and
  colour distance before -> after the match; "rejected on sight" counts;
  which libraries are off on this deployment.
- `look_at(rendered=true, output_times=[...])` after a complete render:
  the cut in and out, the captions over the cutaway.
- `critique_short`: rhythm, a house style, the same device too often.

## Strong treatment patterns

- Proof on the noun: `source_broll(moments=[{query: 'Falcon 9 landing on
  a droneship', word: 'landed', purpose: 'the rocket came back'}])`.
- History in archival: `{query: 'assembly line 1950s factory', word:
  'factories', archival: true}` — shown whole, with grain.
- A still that breathes: `{query: 'portrait of Ada Lovelace', kind:
  'photo', word: 'Ada'}` — it moves in 2.5D through its measured depth.
- A voice-over montage: one moment per phrase, each a different scale and
  motion (wide establishing, close detail, a still), all matched.
- Research first when unsure: `place=false` returns the judged board
  without writing anything.

## Common failure modes

- Wallpaper: a generic shot that proves nothing the words say.
- The same library look three times in a row.
- A cutaway over the punchline or the turn of the argument — the face
  carries those.
- An off-tone picture (a nightlife street for "a world of adults"): the
  query was too loose; tighten it and run again.
- Leaving out the credits: the ledger lines belong in the description
  whenever a licence asks for attribution (CC BY / BY-SA).

## Verification procedure

1. Read the reply: every cutaway has a licence line; the matched colour
   distance is small (the "-> N" after "dE to hero").
2. Look at the director's board: proof, tone, one world.
3. `render_preview(complete=true)`, then `look_at(rendered=true, ...)` at
   each cut in: captions readable over it, no logo anywhere.
4. `critique_short`: no dead stretch, no house style.
5. Paste the LICENCE LEDGER lines into the post's description when any
   says CC BY or CC BY-SA.

## Repair ladder

1. Off-topic or off-tone: tighten the query (an object, an action, a
   place) and run `source_broll` again for that moment; `remove_broll`
   the old one.
2. Nothing found: try `archival: true`, a still (`kind: 'photo'`), or the
   shoot list the reply gives — what to film, at what framing.
3. A cutaway that fights the colour: it is matched already; if it still
   reads wrong, choose a different shot rather than grading harder.
4. Too many cutaways: remove the weakest — restraint is a choice.

## Through apply_edit_batch

Clients that connected before these verbs existed write the same item with
`apply_edit_batch` on the `fx` layer (validated exactly like the verb).
The asset must already be in the project; `credit` is required and its
licence must allow commercial use and edits (no NC / ND). Times are
program seconds; `end` is exclusive.

A licensed clip, cut in on a word, graded to the hero:

```json
{"action": "upsert", "layer": "fx", "id": "br1", "value": {
  "id": "br1", "type": "broll", "stage": "screen", "start": 4.2,
  "end": 6.6, "source": {"kind": "asset", "asset_key":
  "fetched/12/broll/abc.mp4", "offset_s": 3.0}, "media": "video",
  "fit": "cover", "focus_x": 0.42, "match": {"asset_key":
  "generated/12/lut-1a2b.cube", "strength": 1.0}, "grain": 0.2,
  "credit": {"provider": "wikimedia", "license": "CC BY-SA 4.0",
  "author": "Jane Doe", "title": "Falcon 9 landing",
  "page_url": "https://commons.wikimedia.org/wiki/File:F9.webm",
  "attribution_required": true}, "device": "broll_cutaway"}}
```

A photo that moves (2.5D once its depth is measured, Ken Burns until):

```json
{"action": "upsert", "layer": "fx", "id": "br2", "value": {
  "id": "br2", "type": "broll", "stage": "screen", "start": 9.0,
  "end": 11.4, "source": {"kind": "asset", "asset_key":
  "fetched/12/broll/def.jpg"}, "media": "still", "move": {"path":
  "drift_left", "amount": 0.3}, "credit": {"provider": "nasa",
  "license": "Public domain (NASA media guidelines)", "author": "NASA",
  "page_url": "https://images.nasa.gov/details/x"},
  "device": "broll_still"}}
```

Archival film shown whole on its own soft copy, with grain:

```json
{"action": "upsert", "layer": "fx", "id": "br3", "value": {
  "id": "br3", "type": "broll", "stage": "screen", "start": 14.0,
  "end": 16.6, "source": {"kind": "asset", "asset_key":
  "fetched/12/broll/film.mp4", "offset_s": 41.0}, "fit": "frame",
  "grain": 0.35, "credit": {"provider": "archive", "license":
  "Public domain", "title": "Factory film 1952",
  "page_url": "https://archive.org/details/film1"},
  "device": "broll_archival"}}
```

Removal restores the exact previous picture:
`{"action": "remove", "layer": "fx", "id": "br1"}`.
