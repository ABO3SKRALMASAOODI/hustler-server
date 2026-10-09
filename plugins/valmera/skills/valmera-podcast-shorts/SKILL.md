---
name: valmera-podcast-shorts
description: Turn podcasts and interviews into premium, story-led social shorts with Valmera, using one coordinator and reusable editor subagents, ranked hero stories, reference-derived Looks (motion typography, sound design, eased camera moves), fast batch review and verified local exports. Use when the user asks to make shorts from a podcast or interview with this workflow. Does not publish or operate the downstream CRM.
---

# Valmera Podcast Shorts (v9)

Make shorts that look and sound like the best Instagram editors made them,
and better: a story worth finishing, committed art direction, motion
typography, sound edited to picture and eased camera moves. The owner's
premium references score about 7.5/10; October's restrained, silent shorts
scored 2.6. A clean MP4 is not the goal; a short people finish and share is.

v9 replaces restraint with **Looks** ([references/looks.md](references/looks.md)):
sound effects are on, the picture fills the frame, one render is the norm and
review is a quick binary verdict. Superseded v6-v8 material lives in `legacy/`.

## Roles and what to read

- **Coordinator (you):** this file, [selection.md](references/selection.md),
  [looks.md](references/looks.md) and [review.md](references/review.md).
  You select, brief, review, export and write the manifest. You do not edit
  shorts yourself except to record an exception.
- **Editors:** [editing.md](references/editing.md), the Shared grammar,
  structure and Look sections of looks.md, and one brief. Default **three
  editors**, reused: each finished editor immediately takes the next brief.
  The coordinator may raise the pool (and `--max-jobs`) to 4-6 when renders
  come back in about a minute and no "shard is busy" errors appear.
- Everyone runs on the session's current model (in Codex, `gpt-6.1-sol`).
  Do not escalate to a more expensive model.

## The production loop

1. **Start.** Make `.tmp/valmera-podcast-shorts/<run-id>` under
   `/Users/masaoodi/Documents/Valmera` and run `scripts/run.py init` (source,
   music switch, editors). A new podcast is a new run; never overwrite one.
2. **Acquire and index once.** `create_project(kind='shorts')`, upload the
   source as `original` (`upload_start`/`upload_finish`; download a URL
   locally once if Valmera cannot fetch it), then `index_status` until done.
   All children share this index.
3. **Select hero stories** ([selection.md](references/selection.md)): read
   the full transcript once, rank by hook and payoff, keep the top 8-12 hero
   shorts (fewer if fewer clear the bar), an optional standard tier only when
   asked, and a one-line reason for every story left out.
4. **Materialize once.** One `make_shorts` call with verified sentence
   boundaries; `run.py add-short` for each child.
5. **Brief.** For each short choose a Look and a structure, then write a
   150-word brief with a beat sheet (hook, turn, payoff, 2-4 hero moments with
   exact word cues). Save `assignments/<id>.json`; `run.py assign --brief`.
6. **Edit.** Hand each editor one short (`run.py assign --editor`). Editors
   execute in batched calls, render one preview, self-check with
   `watch_video` and rendered `look_at`, fix only the weakest moment and hand
   back a note of at most 10 lines. Record it with `run.py candidate` and
   refill that editor at once.
7. **Review** ([review.md](references/review.md)) in batches as candidates
   arrive: five yes/no questions, then **ship**, **one targeted fix**, or
   **kill**, recorded with `run.py review`.
8. **Export.** `export_final(project_id, edl_version)`, `wait_for_job`,
   `download_url(kind='final', edl_version=...)`, save as
   `exports/<structure>__<short_id>__<slug>.mp4`, then `run.py export`
   (it computes the sha256). Watch the first final in full (`--verified-full`).
9. **Manifest.** `run.py finalize --not-selected <file>` writes
   `exports/manifest.json`, `publishing-manifest.json` and `PUBLISHING.md`
   in the format the publisher already reads.

## Non-negotiables

- **Faithful claims.** No spliced claims, qualifiers kept, cautious
  statements stay cautious. No invented numbers, quotes, messages, posts or
  identities: UI and data templates dramatize only what the source says.
- **Rights.** Use licensed, CC, Valmera kit or library media; record source
  and licence for every asset and put required credits in the handback. Never
  use another creator's logo, footage, music or identity, and never present
  archival footage as the exact event discussed.
- **Speaker-first, verified headlines** (`Name: claim`). If identity is
  uncertain, omit the name; never borrow one from a reference or earlier run.
- **Branding.** Every final keeps the native corner mark and the complete
  5-second native Valmera ending. Never crop, cover, trim, replace or bypass
  them. The final is 15-45 s including that ending.
- **Accounted for.** Every selected short ends exported, killed,
  needs_user_review or failed_technical, with a reason and next action.
- Accepted low-resolution archival footage is used as is; captions, graphics
  and branding still render in HD. Do not upscale or re-upload children.
- Nothing is published or scheduled; the CRM is out of scope.
- The user's current message overrides Look defaults (for example "no music").

## Sound and music

SFX are on by default. The run's `music` switch is `auto` (default: a quiet
library bed for montage and action-opener structures, dry-with-SFX for
conversation), `on` (a bed under every short at about -20 dB, ducked under
speech) or `off`. A brief may override it per short. No final contains
digital silence longer than 0.3 s except a deliberate stop-down before a
reveal; with music off, montages use natural sound, ambience and SFX. A saved
or user brief that explicitly says "no music" means `--music off`.

## Efficiency rules

- Target per short: about 15-25 Valmera calls and 1-2 renders.
- `render_preview` now waits longer. If it returns a running job, a few
  `wait_for_job` calls at most; never poll in a loop, never re-render an
  unchanged version, never use a heartbeat or scheduled task.
- At most `max_jobs` Valmera jobs in flight (default 3, one per editor).
  Writes to one child are serial; independent children proceed in parallel.
- Reuse the parent source and index. No local re-encodes, local ASR, frame
  dumps or preview downloads; watch with `watch_video` and `look_at`. Batch
  `look_at` times into one call. Editors list templates, kit and music once.
- Do not read Valmera's backend code to guess fields; use the tool schemas.
- On a rejected call, correct the arguments and retry once. On a lost
  response, check `shorts_status` and the job id before retrying. A transient
  failure gets one retry, then `run.py exception` and move on.
- To resume, read `run.py status --json` and `shorts_status(parent)`.

## Run state (`scripts/run.py`, `--help` on each command)

`init` · `add-short` · `assign` (brief and/or editor) · `candidate` ·
`review` (ship | fix | kill) · `export` · `exception` · `status [--json]` ·
`finalize`. Run with `python3`. It locks `run.json`, so editors may record
their own candidates. It enforces the hero cap, one fix per short, a brief of
at most 250 words, a 10-line note, the export name and length, and that every
short is accounted for. It never scores taste.

## Completion report

Report: shorts exported by tier, Look and structure; the two or three best
moments (short, timestamp, what lands); killed shorts and exceptions with
reasons; elapsed time; renders in total and per short; re-index count; the
manifest paths. Optionally score 2-3 shorts on the calibration rubric next to
the references. Never claim viral performance or self-award a quality score.

## Legacy and resuming older runs

A `run.json` with version `valmera-podcast-shorts-v7` is an older run: resume
it with `scripts/run_state.py` and [legacy/README.md](legacy/README.md),
keeping its frozen brief. `legacy/` holds the v6-v8 references, contracts,
JSON schemas and the registry, contract-validation, source-ledger and
transcription scripts. `scripts/compose_short.py`, `design_graphics.py` and
`inspect_cut.py` are v8 helpers that v9 does not use. Older saved briefs may
name v8 files; their restraint defaults, SFX ban, 4:3-on-black layout and
score-90 gate are superseded by v9.
