---
name: valmera-longform
description: Make long-form narrated story films of 10 to 20 minutes (workflow 4, in the line of The Sudden Theory) on the workflow shell and Valmera's own engine. The workflow finds its own story on the web, researches it into a dossier of quoted facts that are each fetched and checked here, writes it in chapters with a promise and open loops, narrates it with the local narrator voice, supplies real free-licence archive pictures of everything it names plus designed maps, documents, timelines and charts made from the verified facts, directs it as The Ledger (a 16:9 paper desk with a running ledger of the story's stakes), delivers it once and deletes its work folder. Use when the user asks for a long-form voiceover film, a documentary-style story or a 10-20 minute explainer. Never publishes, never picks music.
---

# Valmera long-form films (workflow 4)

One film per run, 10 to 20 minutes, 1920x1080. A true story told by a narrator over real archive pictures and designed
pages, held together by a promise said by 30 s, chapters that each open a question, open loops closed out loud, a turn at
two thirds, and a ledger of the stakes that balances at the end. `REFERENCES.md` is the bar (three reference films watched).

## Rules that decide everything

1. **Only true things, with receipts.** The researcher reads the web and gives every fact a quote copied from a page and
   its URL. Every quote is fetched here and found on its page (`writer.verify`); only verified facts reach the writer, and
   every segment cites the facts it states. Every figure printed anywhere (the ledger, a chart, a number page, a timeline)
   must stand in a verified quote (`story.check_script`). `film.json` keeps the dossier, the cited facts and the check.
2. **Only real pictures, with licences.** People, places, machines and documents are photographs from Wikimedia Commons,
   Openverse or NASA under public domain, CC0, CC BY or CC BY-SA, found by the worker's named route
   (`broll_source.find_named`) and passed by a vision model against what each must show, with the era it must come from.
   Where no photograph can show what is said, a designed page made of the verified material: the source's own words as a
   clipped document with its address, the places on NASA's Blue Marble (public domain), dates on a timeline, figures on
   a chart. The credits are in `film.json` under `checks.longform.credits`: **the publisher must paste them into the
   post's description** (CC BY and BY-SA require attribution).
3. **The spoken form is the writer model's.** `say` is written as it is said ("nineteen fourteen"); `names` lists every
   proper noun as spelled. The narrator's listeners hear with the names, and the VOICE gate compares the script with the
   listener's text after a model has put that text in spoken form (`_shell/narrate/spoken.py`; the model's words are
   taken only where the listener wrote digits or symbols). No rule turns digits into words anywhere.
4. **Picture density follows the chapter's pace.** slow 1.6, steady 2.2, brisk 3.0 new pictures a 10 s; no framing holds
   past 7 s without something landing in it. Measured in `checks.longform.density`.
5. **Taste** (`.valmera/week-1112/TASTE.md`, class L): sounds only where something lands (a print meets the desk, a
   page turns, a chapter opens, a ledger value ticks, the turn), about one a 10 s, 2.5 s apart, two roles and one
   shutter in any 30 s, a recording's third use takes its alternate; captions at one seat with ink measured from the
   picture under them and a soft pool on photographs; type inside the safe area and off the mark; pushes on stills at
   most 6%.
6. **Never** publish, put music in the plan (the narrator and a few library sounds only; the shell lays the film's music
   after the render from the licensed library: `film.mp4` with it, `film_clean.mp4` without, the credit in `film.json`),
   touch a customer's project, alter the end
   card or the mark (the shell adds both), or rate the film (a separate Opus session rates it on rubric v3).

## Run it

Model calls go to OpenAI while the Anthropic wallet is at its floor (`WRITER_PROVIDER=openai`, the writer's Responses
path with OpenAI's web search; `NAMED_PICTURE_MODEL` for the picture check). The key comes from the environment only.

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
cd <this folder>
zsh <a wrapper that exports OPENAI_API_KEY> env WRITER_PROVIDER=openai NAMED_PICTURE_MODEL=gpt-6.1-sol \
    $PY run.py run --out <deliver root> [--state-root <own state root>] [--args '{"line": "...", "look": false}']
$PY run.py run --resume <run_id> [--state-root ...]
$PY -m pytest -q test_longform.py
```

`--args`: `line` passes a line from the owner to the story finder, `notes` to the writer, `look: false` skips the stop
for the director's look, `direction` sets the builder's knobs (`seed`, `bleed_every`, `cap_px`, `drop`: picture titles to
keep out, `redesign`: segment numbers whose designed page is drawn again). `NARRATE_RENDER_ARGS` passes flags to the
narrator's render (e.g. `--takes 1`). Run in the background and let it wake you; never poll. Every new start of the shell sweeps stopped runs in its
state root, so a run that will stop at its look should use its own `--state-root`.

| Stage | What happens | Time, cost (13 Nov) |
|---|---|---|
| ideas | `story.find_ideas`: six stories for The Ledger with their chapters and ledgers; the best three are probed for free pictures | 15-25 min, about $1 |
| script | `story.dossier` (45-80 quoted facts, named things, places, documents) checked page by page; `story.write` writes 1850-2500 words in chapters from the verified facts only; `story.check_script` measures it, two repair rounds | 15-30 min, $1-3 |
| supply | the narrator (VoxCPM2, band 04; 71 chunks for 12.6 min of speech; about a minute a chunk on a quiet Mac, 7-30 min a chunk when other lanes load it; one narration at a time on `.narrate.lock`) while the pictures are found, checked and downloaded and the designed pages are drawn (`design.py`) | 1-3 h |
| score | NASA's archive tops up any subject with fewer than seven pictures (`nasa_top_up`, the same vision check); maps lost in supply are drawn again; `film_long.build`: the L1 at 1920x1080; density, taste; stills of the beats into `look/sheet.jpg` | **Look.** The run stops here |
| render | `film_long.audio_master`: each narration chunk brought to the read's median level (`level_chunks`, signal only), the placed sounds, lane J's `scene.audio.master`; then `worker/scene` at `--tier master` through `heavy.sh` | 20-40 min |
| check, deliver, cleanup | the shell's brand finish and checks, the mark on ten frames, length 10-20 min; `<out>/<date>-wf4-the-ledger-<title>-<id>/{film.mp4, film.json}`, once; the run folder is deleted | |

### The director's look

Open `<run folder>/look/sheet.jpg`. Frame 0 must already be the story's first picture with the ledger showing; each
chapter page must read; no print may sit under the ledger or the mark. Read each photograph's title and date in
`supply/pack.json`: the vision check knows what a picture shows, not when it was taken (`"drop": [titles]`). Then write
`direction.json` with only what you change, `touch look.ok`, and resume.

## The style: The Ledger (atlas/ALIEN.md 8.4, G4 + G5, the chapter system of `sYSIyYjC3dg` made real)

One paper desk. Archive photographs land as taped prints (they rise 7% into place and settle over 12 frames) and the
subject's name is stamped under its first print; documents land as clipped pages; up to three on the desk, the oldest
leaves; the camera breathes into the desk (0.25% a second). At the story's beats (hook, turn, low, payoff) a photograph
that can fill the frame takes it with a slow push. Maps: the camera travels from the first place to the last. Each
chapter opens on a dark page: CHAPTER n / N, its title, its question, spoken over. The promise is printed on the desk by
30 s. The ledger card (top left) ticks on the word that changes a stake (lane O's tick); at the low point the changed
value is red. The close: the ledger fills the frame, every row's last value, a double rule, the balance in one line.

Not built yet: Memory Palace, The Dark Room, Worn Print, One Day (`styles.json`, `buildable: false`).

## Where things are

`run.py` launcher; `wf4.py` the stages; `story.py` the finder, researcher and writer (on lane O's `writer.py`);
`film_long.py` supply, the builder, sound, render and check; `design.py` the designed pages; `styles.json`;
`REFERENCES.md`; `test_longform.py`. Shared: `valmera-stories/pictures.py` (narrator, pictures, faces),
`valmera-stories/film.py` (clock, curves, ticks, `PACE_S`), `_shell/narrate` (VoxCPM2, the VOICE gate), `worker/scene`.
