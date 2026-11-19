---
name: valmera-marketing
description: Make a finished vertical marketing film that sells Valmera to video editors and creators (a technique or a before-and-after with the call to action inside the film) with one command and at most a few words (/valmera-marketing, or /valmera-marketing <a pain, a technique or a theme>). The run finds the editor's pain itself, records what Valmera really does with a clip and a typed sentence on a local copy of the product (never a customer's project), checks the export against the sentence, narrates with Phonon Easton over real footage cut at the references' pace, checks every picture against the words it sits under, lays sound design and chosen music, delivers one complete folder once and deletes its work. Use when the user asks for a Valmera marketing film, an editing tutorial or a before-and-after for editors. Does not publish.
---

# Valmera marketing films (workflow 2)

Never the same idea told the same way twice (owner, 14 Nov 2026): the ideas stage sees this workflow's delivered ideas from `.valmera/workflow/delivered.jsonl` and a gpt-6-sol judgement takes out any exact repeat before scripting; a new angle, version, structure or story is welcome (the same events told in a new structure, such as a dated timeline, a list or another narrator's frame, with new narration and beats, are a new way), but the same narration or beats with only a new visual look is a repeat. Only a folder inside the owner's delivery folders (Podcast Shorts, Story Films, Marketing Films, Long Films) is recorded; any other `--out` is a test. A test run uses `market.py --test-out <dir>` (or `--test-out=<dir>`), which never delivers or records; any other flag is refused, never read as the topic.

## Start it: one command, nothing or a few words

```
python3 /Users/masaoodi/Documents/Valmera/hustler-server/plugins/valmera/skills/valmera-marketing/market.py [a few words]
```

Run it in the background; it takes 40 to 90 minutes (the recording 5-15, the footage 10-30, the render 10-30). Do not
poll: wait for it to exit. Its last line is one JSON object: `delivered` (the folder), `title`, `style`, `typed` (the
sentence Valmera was given), `pictures_fit`, `spend_usd`, `work_empty`. Exit 2 with `stopped`: run the same command with
the same words; it continues where it stopped. `--out <folder>` delivers elsewhere (default
`/Users/masaoodi/Documents/Valmera/Marketing Films/`). Never rate the film yourself.

What the run does (`proof.py` on the workflow shell, `market.py` the launcher):

| Stage | What happens |
|---|---|
| ideas | One call (gpt-6.1-sol) reads what editors answered to (the marketing references' captions and counts), the references' pace (atlas medians), the clips the owner already works from (podcast sources with transcripts) and the few words if given. It returns ranked ideas: the pain or technique, the true claim, the clip and its seconds, two sentences to type (three for the triptych) and what each must visibly do, the style, the editing device (timeline, wipe or triptych; below) |
| script | **Real output first.** `studio.py` uploads the clip to a local stack of the product (the Gauntlet's runner on this week's main, edits on OpenAI, a synthetic paid account; nothing of production is written), types the sentence before the first result and downloads the export. A vision call (gpt-6-sol, low) checks the export frame by frame against the sentence: every element must be visible, the export vertical, nothing broken; otherwise the second sentence is recorded, and if that fails too the run stops (a film never claims what the export does not show). It also picks the after's seconds, the before's and the export's strongest frame. Before narration is written, the workflow acquires concrete external picture subjects and inspects the actual frames, grouping duplicate scenes and re-crops. The writer sees that inventory and writes the editing argument around available images plus the authenticated product receipts (the typed sentence spoken word for word, the call to action as the last line, VALMERA.IO on screen) and every claim is audited against the receipts by a second call. The before is at most 3 s; the export at most 4 s per showing |
| supply | Narration by Phonon Easton (the stories read, `valmera-stories/voice.py`), real footage per line (`worker/footage`), the already inspected photographs used by the script, and the recording cut in between two narrator lines with its own sound (`_shell/narrate/realvoice.py`): the clip as uploaded, then the export; and, before the narrator's first line, the opening beat: the export's most visible landing (`proof.hook_beat`, from its own edit list) on the speaker's own voice. The writer is given that beat as fixed and writes its first line to agree with it. Every display item the writer returns is anchored to its spoken word (`show[].word_i`, `proof.anchor_show`), so the cutter's anchored-only rule lands it |
| score | The story reel builder (`valmera-stories/reel.py`) at a shot length set from this film's own narration speed against the marketing references' medians; the recording shown as delivered (no section finish, flash or caption pool over it); the before opens whole and wide on a blurred copy of itself, then pushes into the export's own crop of the upload (read from the recording's edit list) until the speaker fills the phone; every line that names THE UPLOAD or THE EXPORT shows it, and a line that runs out of pictures plays the export's unplayed seconds, never a type page; captions at 72 px; the film's editing device shows the typed sentence and Valmera's real edit (below); verified per-line takes feed the story cutter, with bounded footage top-ups. One cut: every played picture window is checked against its exact words and the verdicts are recorded in `film.json`; a verdict never drops a picture or rebuilds the cut (16 Nov: four rounds of drops made 35-47% of two films flat type pages) |
| render, check, deliver | Valmera's engine, the shell's brand finish (watermark, end card), the music it chooses (MUSIC2), the sound design (score.sound); `film.mp4`, `film_clean.mp4`, `film.json` (receipts, picture check, pace, credits) and `SOURCES.md`, once. The run folder, the recording, the stack's database and its folder are deleted |

### Two formats, one story, one call to action (owner, 17 Nov 2026)

"The main thing is that there is a CTA and there is an extremely powerful edit, such that the user really gets curious
about how did he do this edit." The ideas call chooses the film's `format` (`direction.json` `"format"` overrides; the
narrated tutorial when none is named):

- **showcase** (rebuilt 17 Nov after the owner's "How is this marketing the product? ... Where's the editing
  capability? Look at the references."; the references' answer is `.valmera/week-1112/films-1116/marketing-rebuild/SPEC.md`):
  no narrator, four beats (`devices.showcase`, frames in `devices.showcase_marks`):
  1. **split** (0-2.8 s): the raw clip as uploaded (small, left, "RAW CLIP") and Valmera's export (big, right, "1 SENTENCE
     IN VALMERA") at the same instant, both whole from frame 0, on the export's own sound. The seconds are the export's
     most visible change, its longest picture or title, entered a breath before it lands (`proof.showcase_hook`).
  2. **studio** (~3.5 s): the real Valmera Studio on a phone: the recording's own photographs, one per typed word
     (`studio.py`, phone view), so the sentence lands in the composer while the frame pushes in on it (44-48 px); Send
     puts it in its bubble while the Studio's player plays the clip; the export grows out of the screen to full frame
     under the recording's own time to the result ("3 MIN LATER"). Keys, Send and the push are its only sounds.
  3. **export**: the export as delivered, full frame, on its own sound, from its first frame to the end of a spoken
     thought (`showcase_window`, the longest pause between 12 and 20 s outside its pictures; `direction.json`
     `"export_window"` / `"hook_window"` override).
  4. **site** (3.6 s): the call to action is the website itself: the recording's photograph of valmera.io's front page
     as a phone shows it, under **valmera.io** as the film's largest line. It lands 1 s before the export's voice ends,
     so the speaker's last line is said over it (`SITE_OVER_S`). Then the brand's end card.
  **Scale variant** (17 Nov, "a capability that reads as how-did-they-do-that in one glance"): when the recording carried
  a Shorts board to films (`studio.py` `"shorts"` on the ask: the scout's board from one typed sentence, Edit pressed
  on every card so the product's own editor finishes each reel, every reel exported), the film opens on every finished
  short playing at once in a grid while a counter counts them ("1 SENTENCE IN VALMERA", "N SHORTS from a M-min
  podcast"), then the Studio (the sentence typed, Send, the board), then the hero short (the one whose picture changes
  most often, measured) grows out of the board and plays its busiest 8-11 s on its own sound, then the website
  (`proof.scale_plan`, `devices.scale_showcase`). Four films or more; otherwise the four-beat showcase above.
  No writer, no footage search, no picture check (every picture is the recording's own). A recording without Studio
  photographs (made before 17 Nov, or in the desktop view) cannot make a showcase: record it again (`STACK_V` 4).
- **tutorial**: the narrated proof reel below, with its device, then the same call to action on its last line.

Every film, either format: a stranger follows it from its first two seconds. One continuous thought of the speaker (the
before is the same moment of the clip as the after: `product_clips` maps the after's seconds through the final edit
list), a picture inside the export set up by the words said before it, Valmera named on screen in the first second
(the showcase's "1 SENTENCE IN VALMERA" over the edit; the tutorial's "TYPED AT VALMERA.IO") and, in a tutorial, aloud
in the first two lines with what it is.

**The call to action**: the website itself (`devices.site`: the recording's photograph of valmera.io under **valmera.io**,
fitted to the frame, 140 px and up), in both formats when the recording has the photograph; otherwise `devices.cta`
("The AI video editor", "Type what you want.", "It edits your video.", valmera.io). Held to the end of the programme,
at least 3.4 s. True claims only: never "free" (the trial is off), never a price or a plan. The brand finish (the mark
with its adaptive ink on every programme frame, the end card) is the shell's and always follows; it is never changed
here.

**Music** (owner, 17 Nov: "what does Mark Cuban talking have to do with a music type that's in Turkish series"): under a
speaker and a product screen the references play a steady beat, hip-hop or electronic, low under the voice and forward
where nobody speaks, and the talking-head before/afters are speech-led (music in 9-33% of the reel); never an epic or
drama cue (SPEC.md section 4). The library's gated tracks were an epic cue (Future Gladiator, the signal's first pick for
every marketing film) and 125 BPM funk grooves (the 16 Nov marketing films carried Werq; the owner on their music:
"either no music or ... very cringe"). Since 18 Nov the music step chooses first from the reference music set
(`.valmera/models/music_ref`, `chain.use_music_set`), whose rows for marketing are steady beats only (hip-hop, lofi,
electronic, pop; no cinematic cue). A **showcase still bakes
no library bed** (`proof.check`): its sound is the speaker's own, the export's, and the Studio's keys, Send and push.
Its song is the platform's catalog hit, chosen at delivery from what the judge plays under marketing talk
(`platform_music.json`, the whole film at a quarter of the voice; the brief is `proof.SHOWCASE_DIRECTION`). The
tutorial keeps the shell's music step.

### The editing devices (`devices.py`, since 17 Nov 2026)

The owner liked the 12-13 Nov films' tricks ("a good direction that needs to be followed"); they are beats of the proof
reel now, laid in `score` over the reel's own frames (`proof.lay_devices`). The ideas call chooses one per film
(`device`; `direction.json` `"device"` overrides; the timeline when none is named):

| Device | Where | What the viewer sees |
|---|---|---|
| timeline | the line that says the typed sentence | the frame pulls back from the speaker (the largest face, YuNet) onto the project's own timeline: the upload as one block at its true length with its frames, the playhead at the true second, the version and length labels; the sentence is typed in the prompt word by word as the narrator says it; on send the blocks the edit removed are marked and leave, the rest close up to the final edit list, its pictures land on their track (a frame of the export showing each), the captions track fills, the labels flip, and the prompt prints the receipt's rows (read from the two edit lists: seconds cut, pictures, captions, titles, reframe); the export lands in the viewer as its own voice starts and pushes to full frame |
| wipe | the same line | the upload plays whole on its blurred copy, the sentence rises a word as it is said, then a leaning seam crosses the frame and everything it passed is the export at the same instant; after the export, when the product made four or more films of this upload (same sha256, synthetic Studio records), a grid of them lands while a counter counts them up |
| split (every tutorial since 20 Nov, except a triptych's) | the opening beat (frame 0, before the narrator) | the clip as uploaded beside Valmera's export at the same instant, on the speaker's own voice, a hit where the export's picture lands (`proof.lay_hook`); the narrator's first line follows and names what was just seen |
| triptych (by direction only since 17 Nov: a cold viewer read three edits as three moments) | the hook (frame 0) | three exports of the same upload made from three typed sentences, side by side at one source instant (each export's own edit list maps it), the narration's captions under them; the typed line gets the timeline. The two other exports come from the run's own sessions of the idea's other sentences, or from the reused folder's films of the same upload |

Fixed against the old films and kept that way by `test_devices.py`: every device stands on real picture (the clip's
blurred copy, the viewer, the export), never plain black or static; type at 44 px and up (typed sentence 52-72 px,
receipt rows 54 px, labels 44-48 px), under y 0.205 never (the watermark), left of the rail below y 0.55; the receipt
prints inside the change (no held page); the reel's pictures and the sounds that answered them give way inside the span;
the device's own sounds are a few, each on a landing (the typing, the send, the blocks moving, a picture landing, the
seam, the counter), and the narration and the film's bed run under all of it. A device that cannot be laid leaves the
reel as built and the typed line gets the instruction panel (`checks.marketing.devices.faults`).

The styles of the one-command path are in `reels.json` (Proof Reel, Window, Pinboard, Archive); a style rests for three
films. The state root is `.valmera/workflow/marketing` (its own ledger and work folder).

For film-only iteration, `WF2_REUSE_RECORDINGS=<Studio records directory>` explicitly offers existing synthetic
recordings to the ideas stage. Only owner-source, successful, single-instruction recordings qualify. Upload and export
hashes must match before a private copy is made inside this run; the export still passes the same visual check.
The receipts retain the original date, model information and revision. This is never evidence of a fresh product replay.
Without this option, `studio.py` records a new session under the replay lock.

The rest of this page is the older, hand-directed path (three built styles on recordings made beforehand, with a stop
for the director's look). It still works through `run.py` without `WF2_ENGINE=proof`.


One film per run, in one of the built styles, for editors. The film is an argument an editor can
check: a pain they know, one true thing Valmera did about it, shown with the real output.

## Three rules that decide everything

1. **Only what the product did.** Every picture of an edit is an export the product itself made
   in a recorded session (`studio.py`), read in place and shown as delivered: never re-typeset,
   never drawn over, never re-graded (`check` measures it against the export file). The typed
   sentence on screen is the recording's own, word for word. Every number on screen is read from
   the recording's row and kept in `film.json` under `checks.marketing.receipts` with the file and
   its sha256. A claim without a receipt is not made: `score` fails on a number the row does not hold.
2. **Every device earns its moment** (`.valmera/week-1112/TASTE.md`). The editing problem carries the film; vary picture, motion and finish with the story.
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
and the sentences (`studio.py`'s docstring has the shape). `"view": "phone"` records the Studio as a phone shows it and
keeps its photographs in `ui/`: one per typed word (`type_00_NN.png`, the empty composer first), the sent screen, the
result, the site's front page (`site.png`, at three times the device scale) and the session's video; the showcase is
built from them. `"shorts": {"plan_s", "edit_s", "final_s"}` on an ask carries the Shorts board that sentence makes to
finished films (`finish_shorts`): it waits for the scout, presses Edit on every card through the product's own API,
waits for every editor, exports every reel and downloads it (`shorts/`, `record.json` `shorts.films`); a photograph of
the board before and after (`ui/board_00.png`, `board_01.png`). Type it with `"phase": "after_result"`, so the Studio is
photographed after its analysis, never on "Waiting in line". `"phase": "before_first_result"` types
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

The one-command path: see "Two formats, one story, one call to action" above. The older hand-directed path: inside
the film, before the end card. `film.json` carries `script.cta`: the keyword when there is
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
