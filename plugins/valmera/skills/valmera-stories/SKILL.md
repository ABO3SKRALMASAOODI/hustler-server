---
name: valmera-stories
description: Make narrated vertical films about founders, companies and topics (workflow 3, in the line of metromedia.house) on the workflow shell and Valmera's own engine. The workflow finds its own story on the web, writes it with every fact quoted from its source and checked here, narrates it with the local narrator voice, supplies real footage and free-licence photographs of every person, place and thing it names, cuts it at the references' pace on the engines (pacing, look, sound), delivers it once and deletes its work folder. Use when the user asks for a founder story, a company story or a topic story film. Never publishes; a song only when the owner hands one to the run.
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
2. **Real pictures of the real thing: footage first, then photographs.** Per line the footage engine
   (`worker/footage`, through `_shell/chain.footage`) finds real video of what the line names on YouTube
   and archive.org, every frame checked by a vision model and a person held to one face, cut to 1-3 s
   clips with source, channel and time range. Photographs come from Wikimedia Commons and Openverse
   under public domain, CC0, CC BY or CC BY-SA (`broll_source.find_named`), checked the same way. No
   stock lookalikes, no generated faces. The footage is **not licensed** (the owner's decision, 13 Nov):
   the shell never publishes, and every clip and photograph is in `film.json` under
   `checks.stories.credits` (**the publisher pastes the credits into the post's caption**).
3. **Taste** (`.valmera/week-1112/TASTE.md`, the owner's 13 Nov section first): a new picture about every
   second, every picture moving and eased, faces whole, one caption seat, type inside the safe area and
   off the mark, nothing under 38 px, sounds on the film's events in the references' classes and count,
   no upscale past 3x. `score` checks the placement rules as numbers; the pace is in `film.json`.
4. **Never** publish, put music in the plan (the shell lays the film's music after the render: a track chosen
   from the licensed library, `film.mp4` with it and `film_clean.mp4` without, its credit line in `film.json`;
   a song the owner hands to the run is `film_song.mp4` beside the clean `film.mp4`), touch a customer's project, alter the end card or the mark (the shell
   adds both), or rate the film (a separate Opus session rates it on rubric v3).

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
Music is every film's: the build starts where the story turns and the drop lands on the payoff's landing
(`worker/music_film.py`, `score.music`); `music: false` keeps a run dry. `song` (a file the owner handed over)
adds `film_song.mp4` instead: that song cut the same way (a full part from the first frame, never its intro,
under every word, a hit before the end card). `remake` (a delivered folder) makes
that film's story and script again on today's engines with no model call: the facts are fetched and
checked again. `narration` (a folder the narrate tool rendered for this very script) and `footage_from`
(folders of `worker/footage` records for it) are reused instead of rendered and fetched again.
`SHELL_RENDER_CMD=<script>` draws the film with that renderer in place of the Mac engine (the box image:
`.valmera/week-1112/lanes/PROOF/tools/box_render.sh`); it gets the scene, the work folder, the sound and
the output path.
Run every stage in the background and let it wake you; never poll. **Do not start a new run while
another run is stopped at its look**: every new start sweeps stopped runs (the shell's contract).
Resume or finish it first. Runs may start together; their narrations queue on one lock
(`.valmera/week-1106/.narrate.lock`), because VoxCPM2 shares the Apple GPU: three at once measured
5-10 minutes a chunk against 20-50 seconds alone.

| Stage | What happens | Time, cost |
|---|---|---|
| ideas | `writer.find_ideas`: Opus with web search and fetch finds six stories for the allowed styles (never one already in the ledger), each with the named things a camera could show; `wf3.probe` counts free photographs of each on Commons and Openverse. Ideas without pictures sink | 3-8 min, about $1 |
| script | `writer.write`: the best idea in its style, segments with the actor's direction (pace, melody, emphasis, pauses), display strings, facts with quotes and URLs, the style's data. `writer.verify` checks it | 3-6 min, about $1 |
| supply | `pictures.narrate`: the narrate tool (VoxCPM2, local, `_shell/narrate`) through `heavy.sh`, with its VOICE gate; `pictures.pictures`: the named photographs, checked and downloaded, faces found (YuNet); `chain.footage`: real video per line, each clip with a still and its faces. One Photograph: the one Commons file, its faces, who is who (a model reads the photo's own Commons description), and the microtext portrait. Whole Page: loudness and pitch per word | 3-12 min, about $1 on OpenAI |
| score | `film.py` bakes the scene (L1); for Character Sheet the engines finish it (`film.engines_finish`: `scene.pace` cuts and moves every picture on the voice's stress, `scene.looks` solves one look on the film's own pictures, `score.sound` designs the sounds against the narration); stills of the beats go to `look/sheet.jpg`; taste is checked | **Look.** The run stops here |
| render | `film.audio_master` (the engine's own mixer: narration from frame 3, the designed sounds on their events, -14 LUFS, under -1 dBTP), then `worker/scene` at `--tier master`, through `heavy.sh` (or `SHELL_RENDER_CMD`) | 2-10 min |
| check, deliver, cleanup | the shell's brand finish (watermark, end card) and checks, the mark on six frames, the credits; `<out>/<date>-wf3-<style>-<title>-<id>/{film.mp4, film.json}`, once; the run folder is deleted | |

### The director's look

Open `.valmera/workflow/work/<run_id>/look/sheet.jpg`. Frame 0 must already be the hook's picture;
every name must be on its own picture; nothing on a face or under the platform's buttons. Then write
`<run folder>/direction.json` with only what you change, `touch <run folder>/look.ok`, and resume.
`{"script": {...}}` replaces parts of the script's data (facts are verified again); any other key is
a knob of the style's builder (the `d` dict at the top of each builder in `film.py`). Two knobs come up
often: `"drop": [picture titles or file names, clip file names]` keeps a photograph or a clip the check
passed out of the film (a 2025 meeting photo would read as 1973; a group photo would put a name on a
stranger), `"open": clip file` names the clip that opens the film, `"aim_s"` is the seconds a picture
holds (0.85), and `"low_stat": id` names the stat that goes red at the low point.
Read each photograph's own title and date in `supply/pack.json` before approving: the vision check
knows who is in a picture, not when it was taken.

### A film that must be redone

A delivered folder is never changed or added to. Before handover: delete its folder, remove its row
from `.valmera/workflow/ledger.json`, run again with `notes`. After handover: report the defect.

## The styles (atlas/ALIEN.md 8.3)

| Style | Spine | As built |
|---|---|---|
| **Character Sheet** | G4, T4 | A new picture about every 0.85 s: each line's real footage first (a clip is never held past its length; a broadcast close-up plays as cut, with no push into the face), then photographs of what it names; a thing's pictures begin with the clause that says its name; frame 0 is the hook's subject in his world. The pacing driver cuts and moves every picture on the voice's stress (slow pushes, settles, eased moves, a whip through about one cut in eleven); one look is solved on the film's own pictures and the low point is drawn in black and white, opened by a negative frame, colour back on the turn with a white frame. Captions are laid out as they will stand and inked word by word at one seat; the writer's display strings land large on that seat on the words that say them (the caption yields). A compact stat block in the top-left corner changes on the word that causes each change: the number ticks over 7 frames in accent, its bar sets to the value; at the low point the bar goes red and blinks; heads are slid clear of it where the picture has room. At most one in five sentence endings is a cut through its own full stop. The last stat overflows on the film's last words: the number leaves the block and fills the frame. The sound design gives the events their recordings by the references' classes; the turn takes the hit with its climb |
| **Whole Page** | N6, C1 | The whole script set as a page (one or two columns, sized to fill it), photographs as windows in the text. Words not yet spoken wait at 22% ink and are inked as they are said; a window opens when its line is said. The page is seen below a dark desk edge at y 0.215, so no word ever passes under the product's mark. The camera follows the spoken line and holds a window while it is talked about. Each word's size step and lift come from the narrator's own loudness and pitch (signal only). The close pulls back to the whole page: a poster |
| **One Photograph** | T3, X2 | One free archive photograph. Hook: it lies blurred and dark, and its faces land sharp, a few on each spoken word. The camera travels inside it to each person as the script looks at them, with their name once. A person whose part is told keeps a small gold mark to the end. Once, a face turns out to be the sentence being spoken, set in tiny type in its tones; the camera pushes in and reads along it. The close pulls back to the whole photograph with every mark and one line |
| Living Print, The Thread, Census | | not built: they need a stateful damage layer, a path planner over matte and depth, or particles from a photo's pixels |

A new style is one builder in `film.py` (`(script, supply, direction) -> (l1, beats)`), a row in
`STYLES`, a card in `styles.json` with `buildable: true`, and its data in `writer.STYLE_DATA`.

## The alien bank (`worker/scene/alien`, since 13 Nov 2026)

Five devices the engine lays into a scene before it draws it, each bound to this film's own frames, words and voice.
A film asks for one by carrying it under `alien` in its scene: put `"alien": [asks]` in `direction.json`.
The shell lays them in at the render (`_shell/chain.py alien_bank`, log in `supply/alien_bank.json`). A device that
cannot stand on the film leaves it as it was and says why; it never fails a render. Frames are programme frames.

- `rhyme` `{"dev":"rhyme","at":[a,z],"cuts":[f,f],"pool":[assets],"hints":{asset:{"t":s,"at":[x,y]}}}`: a chain of
  pictures cut on one round shape, registered and growing through every cut. For a line that names like things.
- `diecut` `{"dev":"diecut","at":[a,z],"t":"WORD"}`: the word is stamped out of the live picture, its letters lift
  carrying the footage, and drop back. For a word about the thing itself.
- `lamp` `{"dev":"lamp","at":[a,z],"t":"WORD","ink":"#FF3D1F","faces":[[x,y,w,h]]}`: the word is a light that relights
  the take at the voice's level; needs the take's matte for the shadow. For the one quotable word of a take.
- `arc` `{"dev":"arc","marks":[{"f":f,"year":1927,"stakes":-0.4}]}`: the finish follows the year each sentence speaks
  from, the light and the frame follow the stakes. Once a film, for a story with dates or a fall and a turn.
- `answer` `{"dev":"answer","at":[a,z],"picks":[{"f":f,"pic":asset,"word":"VEGAS"}]}`: each named picture is first
  seen inside its own word, then takes the frame on the stressed vowel. For a hook or a list that names things.

One device a beat, each with a reason in the words. Which word, picture and year is your reading, never a rule.
Proofs beside the judge: `.valmera/week-1112/side-by-side/S/`. Full asks: `.valmera/week-1112/notes/S.md`.

## Providers, the spoken form, pace

- **Provider.** `writer.ask_web` and `writer.ask` run on Claude when the process holds an Anthropic key, else on OpenAI's
  Responses API with its web search (`WRITER_PROVIDER=openai|anthropic` forces one; `WRITER_OPENAI_MODEL`, default
  gpt-6.1-sol). Quotes are verified here either way.
- **The spoken form.** `say` is the writer model's spoken form and `names` lists every proper noun as spelled; the
  narrator's gate listener hears with the names and the VOICE gate compares spoken forms (`_shell/narrate/spoken.py`).
- **Pace.** Each segment carries `pace` (slow, steady, brisk, fast); `film.assign_pictures` holds each framing between
  that pace's shortest and longest (`film.PACE_S`), so picture density follows the story's pace.

## Workflow 4 reuses this

`valmera-longform` (the 10-20 minute voiceover) builds on `writer.py` (the web, the providers, `verify`) and on
`pictures.narrate` / `pictures.pictures` / `pictures.commons_file` / `pictures.faces` for its supply, and on `film.py`'s
clock, curves and ticks. Long-form voices are `sage` and `iris`.

## Where things are

`run.py` launcher; `wf3.py` the stages; `writer.py` the story finder, writer and fact check;
`pictures.py` the narrator, the pictures and faces; `film.py` the styles, the sound, the taste check;
`styles.json` the cards; `REFERENCES.md` what makes the reference films a 10 (the writer reads it);
`test_film.py`. The engine is `worker/scene` beside this plugin; the narrator is
`_shell/narrate` (`.valmera/venv-narrate`).
