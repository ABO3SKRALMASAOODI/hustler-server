# The workflow shell

One skeleton for the four workflow skills (podcast shorts, marketing Valmera, topic and founder
stories, long-form voiceover). Each film is found, written, supplied, scored, rendered, checked
and delivered the same way. A workflow writes only what is its own: how it finds ideas and how it
writes the script. This folder is not a skill by itself (no SKILL.md); the workflow skills call it.

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY shell.py run \
    --workflow wf1 --source <file on disk> --out <deliver root>
$PY shell.py run --resume <run_id>          # a stopped run continues at its stage
$PY shell.py status                         # runs in the work root, last styles per workflow
$PY -m pytest -q test_shell.py              # the cleanup test
```

## The stage contract

Every stage reads the files before it and writes one file, `<stage>.json`, atomically, into the
run folder `<state root>/work/<run_id>/` (state root `.valmera/workflow/`). `run.json` holds the
stages done, their seconds and the owning pid. A stage already done is never redone, so a run
killed anywhere resumes where it stopped.

| Stage | Who | Reads | Writes | What |
|---|---|---|---|---|
| ideas | workflow | the source, `styles.json`, the ledger | `ideas.json`: `source` (path, bytes, hash, origin URL, how it was transcribed), `ideas` ranked, each with the styles it can carry | The workflow finds its own ideas; nobody hands it a topic. The source is logged |
| script | workflow | `ideas.json`, the styles allowed | `script.json`: `title`, `style` (a registry row), `window`, `hook_line`, `close_line`, `direction` (the Director's brief), `idea`, `spend` | Picks the best idea whose style is not one of the workflow's last three. The shell refuses a banned style |
| supply | shell (`chain.supply`) | `script.json` | `supply.json` and `supply/` (the session pack: `source.mp4`, `hearing.json`, `index.json`, `session.json`, `perception.json`, `scout.json`, `bin/`, `brief/`) | Cut, hear (whisper, local), perceive, Brief, Scout, pictures (Bin), offers, Brief again |
| score | shell (`chain.score`) | `supply/` | `score.json`: the L1 path, Director spend, gate | One Director call with its repair round; the compiler writes L1 |
| render | shell (`chain.render`) | L1 | `render.json`: engine, film path, the engine's own check | The Mac engine, always through `week-1106/heavy.sh`. The slice engine now; `python -m scene.run --local` once `week-1112/notes/C.md` names `worker/scene` and the package is in the checkout (`SHELL_ENGINE` forces one) |
| check | shell (`chain.finish_and_check`) | the engine's film | `check.json`: `final.mp4` and measured checks | The product's own watermark and end card (`brand.py`, from `worker/renderer.py`), then measured: length = programme + end card, the mark's pixels on the programme, the end card's frames at the close, loudness, no music in the plan. A failed check is a defect in the result, never a stop |
| deliver | shell | everything | `<out>/<date>-<wf>-<style>-<title>-<id>/{film.mp4, film.json}` | Written to a hidden `.partial` folder, then renamed: delivered once, complete. A folder that exists is never added to. The ledger gets the style and window |
| cleanup | shell | | nothing | The run folder and its slice link are deleted. The run exits 3 if anything of it is left in the work root |

A workflow module (`wf1.py` is the first) has `ideas(ctx)` and `script(ctx, ideas)`. It may
replace `supply`, `score`, `render` or `check` when its films need it (narration for WF3/WF4,
screen capture for WF2); everything else stays the shell's. `ctx` gives `run_dir`, `tmp`,
`supply`, `source`, `args` (`--args` JSON, e.g. a line from the owner), `styles_allowed()`,
`styles_last()`, `windows_used()` and `log()`.

**Footage** (`chain.footage`, 13 Nov): real video of the people, places and events a script names, per
line, from YouTube and archive.org (worker/footage through `footage.seam`): searched and ranked by
gpt-6-sol, transcribed on gpt-4o-transcribe, every frame checked by gpt-6-sol (low) and, for a person,
the same face across sources (SFace); 1-3 s clips inside one shot, graded to one measured look, each
with its source URL, channel and time range in `footage.json` and the film's credits. WF3 calls it
in its supply; `VALMERA_FOOTAGE=0` turns it off. A source YouTube will not open (it takes the Mac for
a bot) is looked for on archive.org: the same film by its title, then the subject's own searches. OpenAI only (credit until 30 Nov 2026, money after).
Rights: not licensed, so the shell never publishes and customers stay behind `app_kv.footage` (off).

WF3 and WF4 speak through `narrate/` (Phonon 31M Easton, local open weights, the owner's choice of 14 Nov): a script with a performance
layer per segment becomes `narration.wav`, `words.json` (every word timed) and `check.json` (the
VOICE gate). See `narrate/README.md`.

## Workspace rules

- **Every byte a run writes is inside its run folder.** Tools get `TMPDIR` and `SLICE_TMP` in it;
  the slice's `work/<sid>` is a link to `run/supply/`.
- **Deleted on delivery.** The run folder and the link go after the rename; the result line says
  `work_empty`, and a run whose folder survives exits 3.
- **A stopped run is kept** for `--resume`, until the next new run starts: every start sweeps run
  folders whose pid is dead, and their links.
- **No product drafts.** The shell renders on the Mac engine; it makes no Valmera project. A
  workflow that does must delete it through `DELETE /projects/<id>` before deliver.

## Styles and the ledger

`styles.json` lists each workflow's styles from `atlas/ALIEN.md` section 8 (spine, pace, hook,
close, what the source must have). `<state root>/ledger.json` keeps the last 30 deliveries per
workflow. The ideas stage only sees styles outside the last three, and the shell refuses a
script whose style is one of them.

## The alien bank

`chain.alien_bank(ctx, l1_path)`: a scene that carries asks under `alien` (rhyme, diecut, lamp, arc, answer; the
engine's `worker/scene/alien`) is laid out before the engine draws it, through heavy.sh, and the scene to draw is
written beside it as `*.alien.json` with `supply/alien_bank.json` saying what stood. Every render path calls it.

**Every film gets music** (owner, 13 Nov 2026; `chain.music_version`, after the render, on a copy of the
engine's film). `worker/music_film.py` reads the story's turn and payoff from the film's own lines (a model;
a workflow may name the landing as `score.turn_s`), chooses a track from the licensed library the way the
references do (`worker/music_choose.py`: signal shortlist, a model's ranking, then the engine tries the first
three against the film and keeps the one whose bars carry it), and `worker/score/music.py` cuts it: the film
opens on a full part of the song, a build of two to four bars where the story turns, the drop on the payoff,
under every word and up in the pauses, a hit before the end card. The shell delivers that film as `film.mp4`,
the same film without music as `film_clean.mp4`, and the track's title, licence and credit line in
`film.json` under `music` (CC-BY: the credit goes wherever the film is published). `--args '{"music": false}'`
keeps a run dry. A film over four minutes is scored chapter by chapter (the narrator's longest pauses), a track
each, never one twice, every chapter closing on its own hit; `film.json` lists the tracks and their credit
lines. **A song the owner hands over** (`--args '{"song": "<file>"}'`) gets the same section choice
and level flow and is delivered as `film_song.mp4` beside the clean `film.mp4` (a test song is never the
film). A narrated film hands the scene with its narration as the spine (`score.l1_audio`); a film whose
sound is one file is scored against that sound. A music fault never stops a run: the clean film is delivered. **A renderer other than the Mac engine**: `SHELL_RENDER_CMD=<script>` (workflows that
render through `wf3.render`): the script gets the scene, the work folder, the sound and the output path.

## Never

Never publishes. The plan itself never carries music: it may only carry the sound library's effects, and the
check fails a plan with any other audio (the film's music is laid after the render, from the licensed library
or the owner's own song, never anything else). The end card and the watermark are the product's own,
on every film. A builder never rates its own film: `film.json` carries measurements, not scores.
