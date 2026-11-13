---
name: valmera-stories
description: Make narrated vertical films about founders, companies and topics (workflow 3, in the line of metromedia.house) on the workflow shell and Valmera's own engine. The workflow finds its own story on the web, writes it with every fact quoted from its source and checked here, narrates it with the local narrator voice, supplies real free-licence photographs of every person, place and thing it names, directs it in one of the built styles, delivers it once and deletes its work folder. Use when the user asks for a founder story, a company story or a topic story film. Never publishes, never picks music.
---

# Valmera story films (workflow 3)

One film per run, 45 to 75 seconds, in one of the built styles. The film is a true story told by a
narrator over real pictures of what it names, with one device that no editor would build by hand.

## Rules that decide everything

1. **Only true things, with receipts.** The writer reads the web and gives every fact a quote copied
   from its page and the page's URL. `writer.verify` fetches every page here and finds every quote on
   it; every number on screen must stand in a verified quote. A script that does not verify is
   rewritten once with the failures named, and otherwise the run stops. `film.json` keeps every fact,
   quote and URL (`script.script.facts`, `script.verify`).
2. **Only real pictures, with licences.** Every person, place or thing on screen is a photograph from
   Wikimedia Commons or Openverse under public domain, CC0, CC BY or CC BY-SA, found through the
   worker's pictures route (`broll_source.find_named`: Wikidata `depicts` first, then text) and passed
   by a vision model against what it must show. No film clips, no stock lookalikes, no generated
   faces. Where no photograph exists the film uses a designed page of type. The credits are in
   `film.json` under `checks.stories.credits`: **the publisher must paste them into the post's
   caption** (CC BY and BY-SA require attribution).
3. **Taste** (`.valmera/week-1112/TASTE.md`): one caption seat, type inside the safe area and off the
   mark, nothing under 38 px, a sound only where something lands and within the supply's allowance,
   pushes on stills at most 6%, no upscale past 3x. `score` checks these as numbers.
4. **Never** publish, pick music (the narrator and at most a few library sounds), touch a customer's
   project, alter the end card or the mark (the shell adds both), or rate the film (a separate
   Opus session rates it on rubric v3).

## Run it

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
cd <this folder>
zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run \
    --out /Users/masaoodi/Documents/Valmera/.valmera/week-1112/lanes/O [--args '{"style": "one-photograph"}']
zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run --resume <run_id>
$PY run.py status
$PY -m pytest -q test_film.py
```

`--args`: `style` keeps it to one style, `line` passes a line from the owner to the story finder,
`notes` to the writer, `look: false` skips the stop for the director's look, `direction` sets knobs.
Run every stage in the background and let it wake you; never poll. **Do not start a new run while
another run is stopped at its look**: every new start sweeps stopped runs (the shell's contract).
Resume or finish it first. Runs may start together; their narrations queue on one lock
(`.valmera/week-1106/.narrate.lock`), because VoxCPM2 shares the Apple GPU: three at once measured
5-10 minutes a chunk against 20-50 seconds alone.

| Stage | What happens | Time, cost |
|---|---|---|
| ideas | `writer.find_ideas`: Opus with web search and fetch finds six stories for the allowed styles (never one already in the ledger), each with the named things a camera could show; `wf3.probe` counts free photographs of each on Commons and Openverse. Ideas without pictures sink | 3-8 min, about $1 |
| script | `writer.write`: the best idea in its style, segments with the actor's direction (pace, melody, emphasis, pauses), display strings, facts with quotes and URLs, the style's data. `writer.verify` checks it | 3-6 min, about $1 |
| supply | `pictures.narrate`: the narrate tool (VoxCPM2, local, `_shell/narrate`) through `heavy.sh`, with its VOICE gate; `pictures.pictures`: the named photographs, checked and downloaded, faces found (YuNet). One Photograph: the one Commons file, its faces, who is who (a model reads the photo's own Commons description), and the microtext portrait. Whole Page: loudness and pitch per word | 3-6 min |
| score | `film.py` bakes the scene (L1), draws stills of the beats into `look/sheet.jpg`, checks taste | **Look.** The run stops here |
| render | `film.audio_master` (narration from frame 3, library sounds on their events, -14 LUFS), then `worker/scene` at `--tier master`, through `heavy.sh` | 2-5 min |
| check, deliver, cleanup | the shell's brand finish (watermark, end card) and checks, the mark on six frames, the credits; `<out>/<date>-wf3-<style>-<title>-<id>/{film.mp4, film.json}`, once; the run folder is deleted | |

### The director's look

Open `.valmera/workflow/work/<run_id>/look/sheet.jpg`. Frame 0 must already be the hook's picture;
every name must be on its own picture; nothing on a face or under the platform's buttons. Then write
`<run folder>/direction.json` with only what you change, `touch <run folder>/look.ok`, and resume.
`{"script": {...}}` replaces parts of the script's data (facts are verified again); any other key is
a knob of the style's builder (the `d` dict at the top of each builder in `film.py`).

### A film that must be redone

A delivered folder is never changed or added to. Before handover: delete its folder, remove its row
from `.valmera/workflow/ledger.json`, run again with `notes`. After handover: report the defect.

## The styles (atlas/ALIEN.md 8.3)

| Style | Spine | As built |
|---|---|---|
| **Character Sheet** | G4, T4 | Photographs of the named people and places full frame (or whole on a blurred bed of themselves when a crop would cut them or pass 3x), designed pages for the figures. A stat block in the top-left corner changes on the word that causes each change: the number ticks over 7 frames in accent, its bar sets to the value. At the low point the bar goes red and blinks. At most one in three sentence endings is a cut through its own full stop: the stop swells 1.9x a frame and is a window onto the next picture. The last stat overflows: the number leaves the block and fills the frame |
| **Whole Page** | N6, C1 | The whole script set as a page (one or two columns, sized to fill it), photographs as windows in the text. Words not yet spoken wait at 22% ink and are inked as they are said; a window opens when its line is said. The camera follows the spoken line and holds a window while it is talked about. Each word's size step and lift come from the narrator's own loudness and pitch (signal only). The close pulls back to the whole page: a poster |
| **One Photograph** | T3, X2 | One free archive photograph. Hook: it lies blurred and dark, and its faces land sharp, a few on each spoken word. The camera travels inside it to each person as the script looks at them, with their name once. A person whose part is told keeps a small gold mark to the end. Once, a face turns out to be the sentence being spoken, set in tiny type in its tones; the camera pushes in and reads along it. The close pulls back to the whole photograph with every mark and one line |
| Living Print, The Thread, Census | | not built: they need a stateful damage layer, a path planner over matte and depth, or particles from a photo's pixels |

A new style is one builder in `film.py` (`(script, supply, direction) -> (l1, beats)`), a row in
`STYLES`, a card in `styles.json` with `buildable: true`, and its data in `writer.STYLE_DATA`.

## Workflow 4 reuses this

`writer.py` takes a form: `FORMS["long"]` is the 10-20 minute voiceover (band 04, chapters, a promise
by 30 s). `find_ideas`, `write`, `verify` and `narration_script` work the same for it, and
`pictures.narrate` / `pictures.pictures` / `pictures.commons_file` are its supply. Long-form voices
are `sage` and `iris`.

## Where things are

`run.py` launcher; `wf3.py` the stages; `writer.py` the story finder, writer and fact check;
`pictures.py` the narrator, the pictures and faces; `film.py` the styles, the sound, the taste check;
`styles.json` the cards; `REFERENCES.md` what makes the reference films a 10 (the writer reads it);
`test_film.py`. The engine is `worker/scene` beside this plugin; the narrator is
`_shell/narrate` (`.valmera/venv-narrate`).
