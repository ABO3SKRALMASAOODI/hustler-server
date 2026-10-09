---
name: valmera-podcast-shorts
description: Turn podcasts and interviews into premium, story-led social shorts with Valmera, using one coordinator and reusable editor subagents, ranked hero stories, reference-derived Looks (motion typography, sound design, eased camera moves), fast batch review and verified local exports. Use when the user asks to make shorts from a podcast or interview with this workflow. Does not publish or operate the downstream CRM.
---

# Valmera Podcast Shorts (v9)

Make shorts that look and sound like the best Instagram editors made them,
and better: a story worth finishing, committed art direction, motion
typography, sound edited to picture and eased camera moves. The owner's
references score about 7.5/10; October's restrained, silent shorts scored
2.6. v9 replaces restraint with **Looks** ([references/looks.md](references/looks.md)):
SFX on, the picture fills the frame, one render is the norm and review is a
quick binary verdict. Superseded v6-v8 material lives in `legacy/`.

## Roles and what to read

- **Coordinator (you):** this file, [selection.md](references/selection.md),
  [looks.md](references/looks.md) and [review.md](references/review.md).
  You select, brief, review, export and write the manifest. You do not edit
  shorts yourself.
- **Editors:** [editing.md](references/editing.md), the Shared grammar,
  structure and Look sections of looks.md, one brief and its
  `music_effective`. Default **three editors**, reused: a finished editor
  takes the next brief at once. Raise the pool (and `--max-jobs`) to 4-6
  when renders return in about a minute with no "shard is busy" errors.
- Everyone runs on the session's current model (in Codex, `gpt-6.1-sol`).

## Saved briefs written for v7/v8

Runs often start from a saved brief such as
`.valmera/podcast-shorts/briefs/reusable-four-styles.md`. Where it and v9
disagree, v9 wins on method and the brief wins on preferences.

- **Still binding:** the source and people, lane (structure) names,
  speaker-first verified headlines, spoken-word highlighting when asked,
  asset provenance notes, no publishing or scheduling, B-roll and montage
  rules, and the music preference ("do not add music" means `--music off`).
- **Superseded:** "no maximum" story counts (use 8-12 heroes; a standard
  tier only when asked); v7 run state (use `scripts/run.py`);
  `premium-design.md`, `additional-reels.md` and `set_typography_scene` as
  primary direction (use looks.md); restraint defaults, the SFX ban, the
  black canvas, the per-candidate evidence path (use review.md) and the
  score-90 gate.

## The production loop

1. **Start.** Make `.tmp/valmera-podcast-shorts/<run-id>` under
   `/Users/masaoodi/Documents/Valmera` and run `scripts/run.py init`
   (source, `--music`, editors). A new podcast is a new run.
2. **Acquire and index once.** `create_project(kind='shorts')`, upload the
   source as `original` (`upload_start`/`upload_finish`; download a URL
   locally once if Valmera cannot fetch it). Indexing takes about 15-20 min:
   do not poll. Meanwhile read selection.md and looks.md and draft candidates
   from the platform's captions if it has them; then check `index_status` about every 5
   minutes (a shell `sleep 300` between checks).
3. **Select hero stories** ([selection.md](references/selection.md)): read
   the full transcript once, rank by hook and payoff, keep the top 8-12
   (fewer if fewer clear the bar), a standard tier only when asked, and a
   one-line reason for every story left out.
4. **Materialize once.** One `make_shorts` call with verified sentence
   boundaries and a 0-100 score.
5. **Brief** while the children build (4-7 min): for each short a Look, a
   structure and a 150-word brief with a beat sheet (hook, turn, payoff,
   2-4 hero moments with exact word cues), saved as
   `assignments/<id>.json`. Then one `shorts_status(parent)`,
   `run.py add-short` per child and `run.py assign --brief`.
6. **Edit.** Hand each editor one short (`run.py assign --editor`) with its
   brief and `music_effective`. Editors execute in batched calls, render one
   preview, self-check with `watch_video` and rendered `look_at`, fix only
   the weakest moment and hand back a note of at most 10 lines. Record it
   with `run.py candidate` and refill that editor.
7. **Review** ([review.md](references/review.md)) in batches as candidates
   arrive: five yes/no questions, then **ship**, **one targeted fix** or
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
- **Rights.** Licensed, CC, Valmera kit or library media only; record source
  and licence for every asset and put required credits in the handback. Never
  use another creator's logo, footage, music or identity, or present
  archival footage as the exact event discussed.
- **Speaker-first, verified headlines** (`Name: claim`). If identity is
  uncertain, omit the name; never borrow one from a reference or earlier run.
- **Branding.** Every final keeps the native corner mark and the complete
  5-second native Valmera ending, never cropped, covered, trimmed or
  replaced. The final is 15-45 s including that ending.
- **Accounted for.** Every selected short ends exported, killed,
  needs_user_review or failed_technical, with a reason and next action.
- Archival footage is used as is; never upscale or re-upload children.
- Nothing is published or scheduled; the CRM is out of scope.
- The user's current message overrides Look defaults.

## Sound and music

SFX are on: a sound on each designed beat, within the Look's cue budget.
The run's `music` switch is `auto` (default: a quiet library bed for
montage and action-opener structures, dry with SFX for conversation), `on`
(a bed under every short at about -20 dB, ducked) or `off`. A brief's
`music` is `inherit` unless it says `on` or `off`, and only those override
the run. No final contains digital silence longer than 0.3 s; with music off,
montages get an ambience bed and SFX (looks.md, **Sound without music**).

## Efficiency rules

- Per short: about 25-40 Valmera calls, at most 2 renders, at most 3
  `wait_for_job` calls per render. Savings come from polling, verification
  and bookkeeping; never drop a planned beat to save calls.
- `render_preview` now waits longer. Never poll in a loop, never re-render
  an unchanged version, never use a heartbeat or scheduled task. Long waits
  (indexing, `make_shorts`, exports) are checked every few minutes, not
  every few seconds.
- At most `max_jobs` Valmera jobs in flight (default 3). Writes to one child
  are serial; independent children proceed in parallel.
- Reuse the parent source and index. No local re-encodes, local ASR, frame
  dumps, preview downloads or reading Valmera's backend code; use the tool
  schemas. Editors list templates, kit and music once.
- On a rejected call, correct the arguments and retry once. On a lost
  response, check `shorts_status` and the job id before retrying. A transient
  failure gets one retry, then `run.py exception` and move on.
- To resume, read `run.py status --json` and `shorts_status(parent)`.

## Run state (`scripts/run.py`, `--help` on each command)

`init` · `add-short` · `assign` · `candidate` · `review` (ship | fix | kill)
· `export` · `exception` · `status [--json]` · `finalize`, run with
`python3`. It locks `run.json` (editors may record their own candidates),
enforces the hero cap, one fix per short, brief and note limits, the export
name and length, and that every short is accounted for. It never scores
taste.

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
