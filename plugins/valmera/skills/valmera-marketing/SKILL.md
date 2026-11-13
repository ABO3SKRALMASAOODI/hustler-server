---
name: valmera-marketing
description: Make short vertical films that market Valmera to video editors (before-and-after and technique films with the call to action inside the film), on the workflow shell and Valmera's own engine. The workflow records what the product really did with a clip and a typed sentence, finds its own idea from those recordings, writes the script, directs and renders the film, delivers it once and deletes its work folder. Use when the user asks for a Valmera marketing film, an editing tutorial or a before-and-after for editors. Never publishes, never picks music.
---

# Valmera marketing films (workflow 2)

One film per run, in one of the built styles, for editors. The film is an argument an editor can
check: a pain they know, one true thing Valmera did about it, shown with the real output.

## Three rules that decide everything

1. **Only what the product did.** Every picture of an edit is an export the product itself made
   in a recorded session (`studio.py`), read in place and shown as delivered: never re-typeset,
   never drawn over, never re-graded (`check` measures it against the export file). The typed
   sentence on screen is the recording's own, word for word. Every number on screen is read from
   the recording's row and kept in `film.json` under `checks.marketing.receipts` with the file and
   its sha256. A claim without a receipt is not made: `score` fails on a number the row does not hold.
2. **Restraint is the design** (`.valmera/week-1112/TASTE.md`). One device carries the film.
   One left edge. Type inside the safe area, off the watermark's place, left of the platform's
   right rail, nothing under 38 px. A sound only where something lands on screen. Pages are a
   colour the product's white mark reads on. The `score` stage checks these as numbers and stops
   the run on any miss.
3. **Never:** publish, put music in the plan (the shell lays the film's music after the render from the licensed
   library: `film.mp4` with it, `film_clean.mp4` without, the credit in `film.json`), touch a customer's project or
   footage, alter the end card
   or the mark (the shell adds both after the film), rate the film (a separate session does).

## Record first: `studio.py`

A film needs rows to be about. A row is a recording: one clip uploaded to the product on a local
stack (the Gauntlet's runner: its own database, storage and worker; nothing of production is
written and no customer is involved), the sentence typed into the Studio, and what came back.

```
PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
zsh /Users/masaoodi/Documents/Valmera/.valmera/week-1106/heavy.sh wf2-studio \
    $PY studio.py record --spec <spec.json> --run studio-<date><letter> --parallel 2
$PY studio.py ledger                      # every recording: its sentences and exports
```

`spec.json` names a source the owner already works from, the seconds to cut, the account's plan
and the sentences (`studio.py`'s docstring has the shape). `"phase": "before_first_result"` types
the sentence before the first cut, so the film can say "one sentence, this film";
`"export_before": true` on a sentence typed after the first cut also downloads the project as it
stood before it. A session takes 2 to 7 minutes and a few cents of the replay lane. Kept under
`.tmp/valmera-studio/records/<sid>/`: `upload.mp4`, `exports/`, every EDL version, the journey
and `record.json`. Look at each export before you build on it: the product does not always
return a vertical film, and a recording whose crop cuts the speaker is not a film's hero.
The local Postgres must be up first (`week-1029/harness/env.sh` starts it).

| Style | The rows it needs |
|---|---|
| Reading Wipe Before/After | one recording: one sentence typed before the first result, a vertical export, the sentence short enough for one line (about 50 characters) |
| One Clip, Three Films | three recordings of one upload (same clip, same seconds) with three different sentences, each a vertical export |
| It Edits Itself | one recording with `export_before` on its sentence: the project before and after it |

## Run it

```
cd <this folder>
zsh /Users/masaoodi/Documents/Valmera/.valmera/claude_trial_env.sh $PY run.py run \
    --out /Users/masaoodi/Documents/Valmera/.valmera/week-1112/lanes/I [--args '{"style": "edits-itself"}']
$PY run.py run --resume <run_id>      # after the director's look, or after any stop
$PY run.py status
$PY -m pytest -q test_film.py
```

Stages (the shell's contract, `../_shell/README.md`): `ideas -> script -> supply -> score ->
render -> check -> deliver -> cleanup`. Run long stages in the background and let them wake
you; never poll.

| Stage | What happens | Yours to do |
|---|---|---|
| ideas | `evidence.rows()` (the recordings, as rows with what each can carry) and `evidence.references()` (what editors answered to) go to one model call. It returns ranked ideas: pain, claim, the row that proves it, the style | Nothing. Nobody hands it a topic. `--args '{"style": ..., "row_id": ...}'` keeps it to one style or one row; `"line"` passes a line from the owner |
| script | The best idea whose style is buildable and not one of the last three. One call writes the few words that are not facts (a line, the call to action) and the director's brief. Facts on screen (shapes, lengths, counts, EDL versions) are built by code from the row | Read `script.json` |
| supply | `supply.py studio`: the recordings packed with receipts; for the timeline film, small frames of both exports and of each picture the edit added | Nothing |
| score | `film.py` bakes the scene plan (L1), draws a still of every beat into `look/sheet.jpg`, checks numbers against the row and the plan against the taste rules | **Look.** The run stops here on purpose |
| render | The film's sound as one file (`film.audio_master`: the exports' own voices and the library's sounds, -14 LUFS), then Valmera's engine (`worker/scene`), `--tier master`, through `heavy.sh` | Nothing |
| check | The shell's brand finish and checks, then, measured on the film: the export against the export file (PSNR); for three films at once, each card against its own export at that instant; for the timeline, the playhead's place against the second on screen; for a static keyword, one frame against motion | Read `checks.marketing` |
| deliver, cleanup | `<out>/<date>-wf2-<style>-<title>-<id>/{film.mp4, film.json}`, once; the run folder is deleted | Tell the user the folder |

### The director's look

The run stops in `score` with `look/sheet.jpg` in the run folder
(`.valmera/workflow/work/<run_id>/`). Open the sheet. Judge it as an editor would: does frame 0
say what the film is, is every beat legible in a still, does anything sit on a face or under
the platform's buttons, is there a frame with nothing on it. Then write
`<run folder>/direction.json` with only what you change, `touch <run folder>/look.ok`, and
resume. Changing `records` needs the supply again: delete `supply/pack.json` first.

```
{"records": ["kids-show", "kids-cinematic", "kids-bold"],     which recordings, in order (One Clip, Three Films: left to right)
 "copy": {"line": "On his words."},                           your hand on the script's words; checked again
 "why": "one line on what you changed and why",

 Reading Wipe:  "hero_in_s": 4.85      where in the export the film starts (the wipe plays from here)
                "voice_from_s": 1.0    film second the voice comes in (moved to the nearest quiet point); "voice_end_s": export second it stops
                "hero_f": 150, "pair_f": 140, "word_f": 5      frames of the film whole, of the side by side, per word of the caret
 Three Films:   "window_s": [6.1, 10.3]   the seconds of the upload all three show (default: the first stretch all three keep whole)
 Edits Itself:  "before_in_s": 0.0, "after_in_s": 0.0   where each cut is entered
                "watch_f": 24, "after_f": 231          frames the new cut plays in the viewer, then whole
                "before_voice_from_s": 0.0, "after_voice_end_s": 8.9}   the first cut heard from this film second; the new cut's voice stops at this export second
```

Choose seconds where the edit is seen doing what the sentence asked: a picture landing on its
word, a title, the cut. Let a picture land as the caret leaves, not under it. The voice is the
export's own audio and nothing else: bring it in where a sentence starts and stop it where one ends.

`--args '{"look": false, "direction": {...}}'` runs straight through when the look was already
done on the same plan. `"copy"` in `--args` replaces the script stage's words, checked the same way.

### A film that must be redone

A delivered folder is never changed or added to. If a film has a defect and has not been handed
over, delete its folder, remove its row from `.valmera/workflow/ledger.json` (the style is
otherwise banned for three runs), and run again; say what changed in `direction.why`, which
goes into `film.json`. After handover a film is not withdrawn: report the defect instead. A new
film in a style that was handed over needs that style out of the last three, or its ledger row
removed by the owner's word.

## The styles (atlas/ALIEN.md 8.2)

`styles.json` holds the cards the model reads. A style is offered only when the engine can
draw its whole spine, `film.py` has its builder and a recorded row fits it. Nothing is faked.

| Style | Spine | State | What it still needs |
|---|---|---|---|
| Reading Wipe Before/After | T5, M1, A1 | **built** | a recorded row |
| One Clip, Three Films | N7 | **built** | three vertical recordings of one upload |
| It Edits Itself | N8, A3 | **built** | a recording with the export before its sentence |
| Silent Film, Hidden Word | T6, T4, T2, A1 | not built | a sound plan that holds without a bed; the engine's `weave` and `glyph` joins are ready |
| Exploded | N9, N8 | not built | layers along z (engine class C) |
| Did You Notice | K6 | not built | matte, clean plate and blink times (engine class B) |

A new style is one function in `film.py` (`(copy, pack, direction) -> (l1, beats)`), a row in
`STYLES`, a card in `styles.json` with `buildable: true`, its slots in `wf2.SLOTS`, and what its
row looks like in `evidence.rows()`.

### Reading Wipe Before/After, as built

1. **A page, the upload and the sentence.** The upload sits whole and wide on a coloured page,
   with its shape and length, and under it the sentence as it was typed. A leaning caret stands at it.
2. **The caret is the seam.** It steps a word at a time (a tick on each step). Everything it has
   passed is already the film the product made, full frame, at the same instant as the upload
   beside it. The sentence is spent as it is read.
3. **The film, untouched,** for about five seconds, on its own voice.
4. **The two, side by side,** at the same instant (the upload's frame comes from the EDL's own
   mapping), with one line and the row's facts.
5. **The keyword** in static (A1), 3.3 s, no sound: whole cells of the static's own grid, with a
   plain ground under the product's mark.

### One Clip, Three Films, as built

1. **Three films of one instant.** Three exports of the same upload side by side, lips together:
   each card plays its own export from the second the EDL says holds the same source frame.
2. **Each sentence, typed, then its film whole,** the same seconds each time, on its own voice.
3. **The three again,** and a question: which one would you post. The answer is a number to comment.

### It Edits Itself, as built

1. **The first cut,** full frame, for a second.
2. **The frame pulls back onto its own timeline.** Every block is a kept range of the project's
   EDL at its true second, with frames of the cut on it; pictures and titles have their own
   tracks; the playhead stands on the second the viewer shows.
3. **The sentence is typed.** On send, the blocks it removed are marked and leave, the rest close
   up to where the new EDL puts them, the pictures it added land, the EDL's version and length flip.
4. **The frame goes back into the new cut,** whole, on its own voice.
5. **The receipt** prints, a tick a row: what was typed, what was cut, what was added or moved
   with the cut, the versions. Its last lines are the call to action.

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

## The call to action

Inside the film, before the end card. `film.json` carries `script.cta`: the keyword when there is
one, what the rows promise, and `needs_fulfilment`; the words as shown are
`checks.marketing.on_screen`. A keyword in static must not be spelled by any row. A promise is
the publisher's to keep: say so when you hand the film over. A person must also confirm on a
phone that a static keyword reads in motion; the check measures it, it cannot see it.

## Where things are

`run.py` launcher; `wf2.py` the stages; `studio.py` the recorder; `evidence.py` the rows, the
references and the capabilities; `supply.py` the pack; `film.py` the styles, the sound, the
taste check and the film checks; `styles.json` the cards; `test_film.py`. The engine is
`worker/scene` beside this plugin once it is on main; until then `film.scene_worker()` uses lane
C's tree, or `VALMERA_SCENE_WORKER`. The timeline arithmetic is the worker's own
(`worker/timeline.py`, found by `film.worker_dir()`).
