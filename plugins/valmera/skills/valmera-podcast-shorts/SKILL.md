---
name: valmera-podcast-shorts
description: Turn podcasts and interviews into premium, story-led social shorts with Valmera, using one coordinator and reusable editor subagents, ranked hero stories, reference-derived Looks (motion typography and designed graphics, with sound and camera moves only where a moment earns them), fast batch review and verified local exports. Use when the user asks to make shorts from a podcast or interview with this workflow. Does not publish or operate the downstream CRM.
---

# Valmera Podcast Shorts (v9)

Make shorts that look and sound like the best Instagram editors made them,
and better: a story worth finishing, committed art direction, motion
typography and designed graphics, and — only where a moment clearly earns
them — sound edited to picture and eased camera moves.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 2026):
restraint is the default. Reach for a zoom or a sound only when a specific
moment needs it — a key word, a reveal, a genuinely jarring jump cut, a
real-world action shown — and zero is a fine answer. Never a zoom per cut,
a camera move per hero moment or a sound per landing or transition: used
where nothing calls for them they make an edit look childish.

SOUND EFFECTS IN A PODCAST OR TALKING SHORT DEFAULT TO ZERO (owner, Oct
2026): at most 1-2 per short, each on a structural moment (the payoff, a
real section change) with a visual partner within ~50 ms of its hit (a
graphic landing, a B-roll entry, a real-world action shown). Never a
reflexive opening whoosh, never a bright sound (ding, pop, click, shutter)
on the onset of a payoff or emphasis word, and never a literal sound pun:
a shutter on the word 'pictures', a cash register on the word 'money' when
nothing on screen is a payment. Leave gain_db unset: add_sfx levels each
library sound against the measured voice at its hit and reports where it
sits.

EVERY GRAPHIC EARNS ITS PLACE (owner and judges, Oct 2026): it adds what
the captions cannot — a number, a contrast, an identification, evidence, an
image. About one hero graphic per 6-8 s at most, graphics on screen for
under ~50% of the runtime, at most 3 type roles and one accent colour;
never a re-typeset of the words being heard, never a spoken list as rows
of text (show the items: real photos or clips on their onsets, else one
accumulating list_build of the noun phrases said). The other half of the
budget: every thesis line, spoken list or triad, named product or place and
number gets a beat that adds information, with no body stretch past ~6-8 s
without one. The hook is written from the clip's strongest line or
statistic (never a generic question), is a headline that owns its zone
(`word_slam` `tier='hook'`: the captions wait until it exits) and never
shows a word a later graphic slams; a counter really counts and shows a
spoken range as said ('30–40'); the payoff locks its number and noun
together and holds 0.8-1.5 s after the last word (the natural tail, chosen
at selection; `add_freeze_frame` has no composed hold on a card layout, see
editing.md **Known Valmera limits**; a payoff number ~2 s on screen). Valmera's render result carries an EARN ITS PLACE advisory naming
what breaks these rules, each with a fix ([looks.md](references/looks.md),
**Shared grammar**, has the details).

A steady, well-framed picture and a clean voice are the defaults, and every
zoom or sound needs a reason you can name. The owner's references score
about 7.5/10; October's bare clip pages (small picture, plain captions,
nothing designed) scored 2.6. v9 answers with **Looks**
([references/looks.md](references/looks.md)): designed type and graphics on
the story's words, the picture filling the frame, sparse optional sound from
the owner-approved library, one render as the norm and review as a quick
binary verdict. Superseded v6-v8 material lives in `legacy/`.

## Roles and what to read

- **Coordinator (you):** this file, [selection.md](references/selection.md),
  [looks.md](references/looks.md) and [review.md](references/review.md).
  You select, run the framing pilot, brief, review, export and write the
  manifest. Beyond the pilot's frame you do not edit shorts yourself.
- **Editors:** [editing.md](references/editing.md), the Shared grammar,
  structure and Look sections of looks.md, one brief, its `music_effective`
  and, when that is on, the owner's song. Default **three editors**, reused:
  a finished editor takes the next brief once the run's first candidate has
  a verdict (the first-candidate gate). Raise the pool (and `--max-jobs`) to 4-6
  when renders return in about a minute with no "shard is busy" errors.
- Everyone runs on the session's current model (in Codex, `gpt-6.1-sol`).

## Saved briefs written for v7/v8

Runs often start from a saved brief such as
`.valmera/podcast-shorts/briefs/reusable-four-styles.md`. Where it and v9
disagree, v9 wins on method and the brief wins on preferences.

- **Still binding:** the source and people, lane (structure) names,
  speaker-first verified headlines, spoken-word highlighting when asked,
  asset provenance notes, no publishing or scheduling, B-roll and montage
  rules, and the music preference. Music is off unless the owner supplies
  or approves a specific song for the run (the brief's Music line names it;
  record it with `--song`).
- **Superseded:** "no maximum" story counts (use 8-12 heroes; a standard
  tier only when asked); v7 run state (use `scripts/run.py`);
  `premium-design.md`, `additional-reels.md` and `set_typography_scene` as
  primary direction (use looks.md); the bare clip-page defaults, the blanket
  SFX ban (sound is now optional, sparse and library-only), any `auto` music
  or library bed
  (music is off without the owner's song), the black canvas, the
  per-candidate evidence path (use review.md) and the score-90 gate.

## The production loop

1. **Start.** Make `.tmp/valmera-podcast-shorts/<run-id>` under
   `/Users/masaoodi/Documents/Valmera` and run `scripts/run.py init`
   (source, editors; `--song '<file, link or Artist - Title>'` only when the
   owner supplied or approved one, otherwise music stays off). A new podcast
   is a new run.
2. **Acquire and index once.** `create_project(kind='shorts')`, upload the
   source as `original` (`upload_start`/`upload_finish`; download a URL
   locally once if Valmera cannot fetch it; with yt-dlp fetch the video and
   the `en-orig` captions in separate calls, so a caption 429 cannot block the
   download). Indexing takes about 20-25 min and shows "0%" while queued:
   do not poll. Meanwhile read selection.md and looks.md and draft candidates
   from the platform's captions if it has them; then check `index_status` about every 5
   minutes. Waiting: the harness blocks a foreground `sleep`; run
   `sleep 300` with `run_in_background` and continue when it reports done.
3. **Select hero stories** ([selection.md](references/selection.md)): read
   the full transcript once, rank by hook and payoff, keep the top 8-12
   (fewer if fewer clear the bar), a standard tier only when asked, and a
   one-line reason for every story left out. Look at 3-4 source frames
   first: what the picture allows (a call window, a picture-in-picture,
   UI, logos, a 4:3 archive) decides the layouts and so the Looks.
4. **Materialize once.** One `make_shorts` call with verified sentence
   boundaries and a 0-100 score.
5. **Pilot the frame and the export path once per source** (selection.md,
   **Framing pilot**; about 15 min, 1 render, 1 export) on the rank-1 child
   whenever a short will use a card or letterbox or the source has
   obstacles: set the layout the briefs will use, render, measure picture
   area and headline vs caption cap height on a native frame, and export it
   once. Save `selection/framing.json`; every brief copies it. If no layout
   meets the Look floors, change the Looks and structures to one that does
   (looks.md, **Card geometry**); if Valmera cannot render or export the
   proven layout, stop here and report the product defect (**Stop the
   line**). Oct 2026: 8 of 9 shorts died on a frame the pilot would have
   caught in the first 20 minutes.
6. **Brief.** Draft while the children build (4-7 min), finish with the
   pilot's measurements (Looks and layouts it allows): for each short a Look, a
   structure and a 150-word brief with a beat sheet (hook, turn, payoff,
   2-4 hero moments with exact word cues, no source gap over 12 s),
   saved as `assignments/<id>.json` with `child_project_id`, `rank`,
   `score` and the pilot's `framing`. Then one `shorts_status(parent)` and
   one `run.py register --assignments <run>/assignments` (it rejects a brief
   whose beats leave a long gap).
7. **Edit.** Hand each editor one short (`run.py assign --editor`) with its
   brief, `music_effective` and `music_song`; run exactly `max_editors`
   editors. Editors execute in batched calls, render once, self-check with
   rendered `look_at` (editing.md step 5), fix only the weakest moment and
   hand back a note of at most 10 lines, recorded by the editor itself with
   `run.py candidate --editor <name>` (no slot needed).
8. **Review** ([review.md](references/review.md)) each candidate as it
   arrives, before any new short goes out: five yes/no questions, then
   **ship**, **one targeted fix** or **kill**, recorded with `run.py review`.
   **First-candidate gate:** `run.py` hands out no more than `max_editors`
   shorts until the run's first review. If a verdict names a cause the shorts
   in flight share (layout, source, template, a Valmera tool), fix the
   framing recipe and the open briefs before the next hand-out, or stop the
   line.
9. **Export** only a short whose latest review is **ship** on that exact
   EDL version: `export_final(project_id, edl_version)`, `wait_for_job`,
   `download_url(kind='final', edl_version=...)`, save as
   `exports/<structure>__<short_id>__<slug>.mp4`, then `run.py export`
   (it computes the sha256). Watch the first final in full (`--verified-full`).
10. **Manifest.** `run.py finalize --not-selected <file>` writes
   `exports/manifest.json`, `publishing-manifest.json` and `PUBLISHING.md`
   in the format the publisher already reads.

## Non-negotiables

- **Faithful claims.** No spliced claims, qualifiers kept, cautious
  statements stay cautious. No invented numbers, quotes, messages, posts or
  identities: UI and data templates dramatize only what the source says.
- **Rights.** Licensed or CC media, the Valmera sound library, and only the
  owner's own song for music; record source and licence for every asset and
  put required credits in the handback. Never use another creator's logo,
  footage, music or identity, or present archival footage as the exact event
  discussed.
- **Speaker-first, verified headlines** (`Name: claim`, the claim taken
  from the clip's strongest line, never a generic question). The name lives
  in the headline or the hook's kicker, so a famous speaker needs no
  broadcast lower third. If identity is uncertain, omit the name; never
  borrow one from a reference or earlier run.
- **Branding.** Every final keeps the native corner watermark ("Edited
  using Valmera AI") and the complete 5-second native Valmera end card
  exactly as Valmera renders them: never cropped, covered, trimmed,
  shortened, moved or replaced. They are the owner's marketing. The final is
  15-45 s including that ending.
- **Accounted for.** Every selected short ends exported, killed,
  needs_user_review or failed_technical, with a reason and next action.
- Archival footage is used as is; never upscale or re-upload children.
- Nothing is published or scheduled; the CRM is out of scope.
- The user's current message overrides Look defaults.

## Sound and music

**Sound** comes only from the owner-approved library of real recordings
(`list_sound_library`; `add_sfx(storage_key='sound:<id>', at=...)` with
gain_db unset), never from online search. A podcast short carries zero by
default and at most 1-2, each on a structural moment where something on
screen changes within ~50 ms of the hit: a designed graphic landing, a real
section change or B-roll entry, the payoff, or a real-world action shown (a
shutter on a photo being taken, typing under typed text, a click on a
button press, a cash register on a payment shown). Never on captions or
ordinary cuts inside the conversation, never the reflexive opening whoosh,
never a bright sound (ding, pop, click, shutter) on the payoff word's
onset, never a literal pun on the spoken word. Never the same sound twice
within ~3 s; one family per short, matched to the material; peaks on the
visual frame. `add_sfx` levels each recording against the measured voice
at its hit (whoosh and swish about 8 dB under, ding, pop, click and
shutter about 10 under, an impact louder only below 150 Hz) and reports
where it sits; `audit_audio_mix` lists every sound's level and placement
checks. Motion graphics are silent unless the editor passes `sfx=true`, and
`apply_look` places no sound unless called with `transition_sounds=true`
(looks.md, **Sound**).

**Music is off.** Agents never choose music: no CC0 library bed, no stock
track, no song picked by taste. It is on only when the owner supplies or
approves a specific song for the run (`run.py init --song ...`); then that
exact song goes under every short whose brief does not say `off`, about
-20 dB and ducked. A brief's `music` is `inherit` or `off`; `on` is accepted
only when the run has the owner's song. Montages and action openers never
ship digital silence: they keep the source's natural sound, are designed
around speech, or, when neither works, are flagged in the handback for the
owner to add a song when posting (looks.md, **Sound without music**). Never
invent a music choice.

## Stop the line (the runs exist to upgrade Valmera)

The owner's priority is upgrading Valmera; a podcast run is how its defects
are found, not a deadline to edit around them. Run one podcast at a time.
When the pilot, the first review or two editors hit the same Valmera defect
(a tool that errors, a template that renders type smaller than captions, a
layout the engine refuses, an export that fails), stop handing out shorts:
record the unstarted ones with `run.py exception --status failed_technical`
and the next action "revive after Valmera fix: <defect>", write the defect
with its evidence (call, arguments, error text, job id) into the report, and
end the run. Editing nine shorts around a defect costs hours and ships
nothing (Oct 2026: 2 h 55 min, 729 editor calls, 25 renders, 0 exports).

## Efficiency rules

- Per short: about 25-40 Valmera calls, at most 2 renders, at most 3
  `wait_for_job` calls per render. Savings come from polling, verification
  and bookkeeping; never drop a planned beat to save calls.
- **Hard stops.** Two renders is a ceiling, never a target: a defect found
  after render 2 goes into the handback, not into a third render (5 of 9
  Oct shorts rendered 3-4 times and were killed anyway). The same operation
  failing twice, or about 60 calls on one short, means stop working around
  it: hand back `blocked` with the error text, and the coordinator records
  `failed_technical` instead of a review.
- `render_preview` now waits longer. Never poll in a loop, never re-render
  an unchanged version, never use a heartbeat or scheduled task. Long waits
  (indexing, `make_shorts`, exports) are checked every few minutes, not
  every few seconds (a backgrounded `sleep`, never a foreground one).
- Reviews go before new edits in any shared queue: a verdict can change every
  open brief, and a candidate waiting for review is the run's most valuable
  information (Oct 2026: the first candidate waited 49 min while five more
  shorts were started with its defect).
- At most `max_jobs` Valmera jobs in flight (default 3). Writes to one child
  are serial; independent children proceed in parallel.
- Reuse the parent source and index. No local re-encodes, local ASR, frame
  dumps, preview downloads or reading Valmera's backend code; use the tool
  schemas. Editors list templates and the sound library once.
- On a rejected call, correct the arguments and retry once. On a lost
  response, check `shorts_status` and the job id before retrying. A transient
  failure gets one retry, then `run.py exception` and move on.
- To resume, read `run.py status --json` and `shorts_status(parent)`.

## Known Valmera limits

Tools change; the live schema wins over any doc. As of Oct 2026 several
tools behave differently from what these references once assumed (no
`source` on `set_picture_card`, no composed freeze hold, `apply_edit_batch`
failing once motion graphics or erase patches exist, `watch_video` returning
no link, the headline band starting below the free-tier mark). editing.md
**Known Valmera limits** lists each with the working path. Read it before the
pilot and before editing; never spend more than two calls rediscovering one.

## Run state (`scripts/run.py`, `--help` on each command)

`init` · `register` (add-short + brief for a whole assignments folder) ·
`add-short` · `assign` · `candidate` · `review` (ship | fix | kill) ·
`export` · `exception` · `status [--json]` · `finalize`, run with
`python3`. It locks `run.json` (editors record their own candidates, from
`queued` or `fix` too, without holding a slot), enforces the hero cap, one
fix per short, brief and note limits, beat coverage in the brief, the
first-candidate gate, the export name and length, and that every short is
accounted for. It never scores taste. Editors save their note and handback
as `candidates/<id>-note.txt` and `candidates/<id>-handback.json`, never in
a session scratchpad.

## Completion report

Shorts exported by tier, Look and structure; the two or three best moments
(short, timestamp, what lands); kills and exceptions with reasons; elapsed
time; renders per short; re-index count; manifest paths. Optionally score
2-3 shorts on the calibration rubric. Never claim viral performance or
self-award a quality score.

## Legacy and resuming older runs

A `run.json` with version `valmera-podcast-shorts-v7` is an older run: resume
it with `scripts/run_state.py` and [legacy/README.md](legacy/README.md),
keeping its frozen brief. `legacy/` holds the v6-v8 references, contracts,
JSON schemas and the registry, contract-validation, source-ledger and
transcription scripts. `scripts/compose_short.py`, `design_graphics.py` and
`inspect_cut.py` are v8 helpers that v9 does not use.
