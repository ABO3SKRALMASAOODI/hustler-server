---
name: valmera-marketing
description: Make short vertical films that market Valmera to video editors (technique tutorials and before-and-after films with the call to action inside the film), on the workflow shell and Valmera's own engine. The workflow finds its own idea from what Valmera really delivered, writes the script, records real Valmera output with receipts, directs and renders the film, delivers it once and deletes its work folder. Use when the user asks for a Valmera marketing film, an editing tutorial or a before-and-after for editors. Never publishes, never picks music.
---

# Valmera marketing films (workflow 2)

One film per run, in one of six styles, for editors. The film is an argument an editor can
check: a pain they know, one true thing Valmera did about it, shown with the real output.

## Three rules that decide everything

1. **Only what the product did.** Every picture of "after" is a real Valmera export, read in
   place and shown as delivered: never re-typeset, never drawn over, never re-graded
   (`check` measures it against the export file). Every number on screen is read from a file
   and recorded in `film.json` under `checks.marketing.receipts` with the file and its sha256. A claim
   without a receipt is not made. The script stage fails if it shows a number the evidence
   does not hold.
2. **Restraint is the design** (`.valmera/week-1112/TASTE.md`). One device carries the film.
   One left edge. Type inside the safe area, off the watermark's place, left of the platform's
   right rail, at most three rows at once, nothing under 38 px except one source credit. A
   sound only where something lands on screen: the caret's steps, the move, the lock. The
   `score` stage checks these as numbers and stops the run on any miss.
3. **Never:** publish, pick music, touch a customer's project or footage, alter the end card
   or the mark (the shell adds both after the film), rate the film (a separate session does).

## Run it

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
cd <this folder>
zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run \
    --out /Users/masaoodi/Documents/Valmera/.valmera/week-1112/lanes/I
$PY run.py run --resume <run_id>      # after the director's look, or after any stop
$PY run.py status
$PY -m pytest -q test_film.py
```

Stages (the shell's contract, `../_shell/README.md`): `ideas -> script -> supply -> score ->
render -> check -> deliver -> cleanup`. Run long stages in the background and let them wake
you; never poll.

| Stage | What happens | Yours to do |
|---|---|---|
| ideas | `evidence.ledger()` (finished podcast-shorts runs: source, count of exports, Looks) and `evidence.references()` (what editors answered to) go to one model call. It returns ranked ideas: pain, claim, the ledger row that proves it, the style | Nothing. Nobody hands it a topic. `--args '{"line": "..."}'` passes a line from the owner |
| script | The best idea whose style is buildable and not one of the last three. One call writes the film's words for that style's slots and the director's brief | Read `script.json` |
| supply | `supply.py`: the hero short and the quick cuts read in place, the raw source cut for the same seconds and registered to the hero (`register.py`: rectangle, crop, frame), one tile per delivered short, receipts | Name the hero and cuts in `direction.json` (below), or take the default pick |
| score | `film.py` bakes the scene plan (L1), draws a still of every beat into `look/sheet.jpg`, runs the taste check | **Look.** The run stops here on purpose |
| render | Valmera's engine (`worker/scene`), `--tier master`, through `heavy.sh` | Nothing |
| check | The shell's brand finish and checks, then: the export inside the film against the export file (PSNR), and the keyword against one frame and against motion | Read `checks.marketing` |
| deliver, cleanup | `<out>/<date>-wf2-<style>-<title>-<id>/{film.mp4, film.json}`, once; the run folder is deleted | Tell the user the folder |

### The director's look

The run stops in `score` with `look/sheet.jpg` in the run folder
(`.valmera/workflow/work/<run_id>/`). Open the sheet. Judge it as an editor would: does frame 0
say what the film is, is every beat legible in a still, does anything sit on a face or under
the platform's buttons, is there a frame with nothing on it. Then write
`<run folder>/direction.json` with only what you change, `touch <run folder>/look.ok`, and
resume. Changing `hero` or `salvo` needs the supply again: delete `supply/pack.json` first.

```
{"hero": "s042",                      short id of the before/after short
 "salvo": ["s028", "s061", "s007", "s067"],   quick cuts; the last one is the tile the pull-back starts from
 "hero_out_s": 7.38, "resume_s": 10.55, "resume_end_s": 13.1,   the voice: leave the hero here, pick it up here, stop here
 "copy": {"cta": {"rows": ["...", "..."]}},                      your hand on the script's words; checked again
 "why": "one line on what you changed and why",
 "voice_from_s": 0.45,                the voice starts here; default: past a cut-off syllable at the head, found from the signal
 "word_f": 8, "hold_f": 15, "salvo_f": 10, "fill_f": 45, "wall_hold_f": 48, "cta_f": 99}
```

Choosing the hero: one speaker on one continuous take for its first 7 s, a line that stands
alone, a design that reads at a glance. Look at its first 9 s before you choose
(`ffmpeg -t 9 -vf fps=2,scale=160:-2,tile=9x2`). Choose quick cuts that differ in Look and
colour. The voice is the hero short's own audio and nothing else: find a place where a
sentence ends, and let it stop before the keyword.

`--args '{"look": false, "direction": {...}}'` runs straight through when the look was already
done on the same plan. `"run_id"` keeps the ideas stage to one ledger row (a film about that
delivery); `"copy"` replaces the script stage's words with given ones, checked the same way.

### A film that must be redone

A delivered folder is never changed or added to. If a film has a defect and has not been handed
over, delete its folder, remove its row from `.valmera/workflow/ledger.json` (the style is
otherwise banned for three runs), and run again; say what changed in `direction.why`, which
goes into `film.json`. After handover a film is not withdrawn: report the defect instead.

## The styles (atlas/ALIEN.md 8.2)

`styles.json` holds the cards the model reads. A style is offered only when the engine can
draw its whole spine and `film.py` has its builder. Nothing is faked.

| Style | Spine | State | What it still needs |
|---|---|---|---|
| Reading Wipe Before/After | T5, the sheet, A1 | **built** | a ledger run with its raw source on disk |
| It Edits Itself | N8, C8, A3 | not built | the film's own EDL as layers and a real re-render of one change, recorded through the product |
| One Clip, Three Films | N7 | not built | three real results on one clip from three requests, recorded through the product |
| Silent Film, Hidden Word | T6, T4, T2, A1 | not built | a sound plan that holds without a bed; the engine's `weave` and `glyph` joins are ready |
| Exploded | N9, N8 | not built | layers along z (engine class C) |
| Did You Notice | K6 | not built | matte, clean plate and blink times (engine class B) |

A new style is one function in `film.py` (`(copy, pack, direction) -> (l1, beats)`), a row in
`STYLES`, a card in `styles.json` with `buildable: true`, and its slots in `wf2.script`.

### Reading Wipe Before/After, as built

1. **Raw footage and one sentence.** The raw source sits in the frame exactly where the
   delivered short holds its picture (registered from the pixels), with a label, a source
   credit and the request, all on one left edge. A leaning caret stands at the sentence.
2. **The caret is the seam.** It steps a word at a time (3 frames to reach a word's end, a
   tick on each step). Everything it has passed is already the delivered short, in register,
   so the face never breaks: only the design arrives. The sentence is spent as it is read; no
   type of ours is ever left on the export.
3. **The short, untouched,** for about five seconds, on its own voice.
4. **Others like it,** four hard cuts of 10 frames, different Looks.
5. **The delivery, counted.** The last cut shrinks into its place on a sheet with one slot per
   delivered short; the rest land in reading order; the number is the tiles on screen.
6. **The keyword** in static (A1), 3.3 s, no sound. It is drawn as whole cells of the static's
   own grid: a letter cut with its true outline shows that outline in a still.

## The call to action

One keyword of 4 to 7 letters, inside the film, before the end card. `film.json` carries
`script.cta`: the keyword, what the rows promise, and `needs_fulfilment: true`; the words as shown
are `checks.marketing.on_screen`. No row may spell the keyword: then it is on screen in every frame. The promise is
the publisher's to keep: say so when you hand the film over. A person must also confirm on a
phone that the keyword reads in motion; the check measures it, it cannot see it.

## Where things are

`run.py` launcher; `wf2.py` the stages; `evidence.py` the ledger, the references and the
capabilities; `supply.py` the recorder; `register.py` raw to export registration; `film.py` the
styles, the taste check and the film checks; `styles.json` the cards; `test_film.py`. The
engine is `worker/scene` beside this plugin once it is on main; until then `film.scene_worker()`
uses lane C's tree, or `VALMERA_SCENE_WORKER`.
